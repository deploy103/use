"""Browser-only Codex OAuth and read-only allowance collection.

No listener is opened: the owner pastes the localhost callback URL into the
dashboard. PKCE/state live only in this process; credentials use a separate
encrypted store and legacy CLI/agent credentials are never read.

Protocol reference: Soju06/codex-lb, revision
72630bbfeee9e3b0d9acfb95e2b1e706b8b0188e (MIT), oauth.py,
oauth/service.py, auth/refresh.py and usage/models.py. The authorization URL
parameters and PKCE construction below are adapted from that project.

MIT License — Copyright (c) 2025 Soju06
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import re
import secrets
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet

from collectors import CollectionError

_AUTH_URL = "https://auth.openai.com/oauth/authorize"
_TOKEN_URL = "https://auth.openai.com/oauth/token"
_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_CALLBACK_URL = "http://localhost:1455/auth/callback"
_SCOPE = "openid profile email"
_TTL = 15 * 60
_TIMEOUT = 15
_OP_TIMEOUT = 45
_REFRESH_AGE = 8 * 24 * 60 * 60
_MAX_BODY = 256 * 1024
_MAX_HISTORY = 64
_INVALID = "ChatGPT returned an invalid or unsupported response."
_FAILED = "ChatGPT connection failed. Please try connecting again."
_UNKNOWN = "Unknown ChatGPT account or login."
_CALLBACK_ERROR = "Invalid ChatGPT callback URL or state."


class ChatGPTError(CollectionError):
    """Only fixed, credential-free messages cross the dashboard boundary."""


class _Unauthorized(ChatGPTError):
    pass


def _require(ok):
    if not ok:
        raise ChatGPTError(_INVALID)


def _number(value, *, maximum=10**12, integer=False):
    if value is None:
        return None
    _require(type(value) in (int, float))
    _require(0 <= value <= maximum and math.isfinite(value))
    _require(not integer or int(value) == value)
    return int(value) if integer else value


def _iso(stamp):
    try:
        return datetime.fromtimestamp(stamp, UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        raise ChatGPTError(_INVALID) from None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _bad_constant(value):
    raise ChatGPTError(_INVALID)


def _json(value):
    _require(0 < len(value) <= _MAX_BODY)
    data = json.loads(value, object_pairs_hook=_pairs, parse_constant=_bad_constant)
    _require(isinstance(data, dict))
    return data


def _secret(value):
    _require(isinstance(value, str) and re.fullmatch(r"[!-~]{1,65536}", value))
    return value


def _claims(token):
    # Metadata only, not local authentication/JWT verification. Tokens come
    # exclusively from the pinned TLS token endpoint and wham verifies access.
    parts = token.split(".")
    _require(len(parts) == 3 and re.fullmatch(r"[A-Za-z0-9_-]+", parts[1]))
    return _json(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))


def _tokens(data):
    _require(isinstance(data, dict) and not data.get("error"))
    tokens = {
        key: _secret(data.get(key))
        for key in ("access_token", "refresh_token", "id_token")
    }
    _require(data.get("token_type", "Bearer").lower() == "bearer")
    claims = _claims(tokens["id_token"])
    auth = claims.get("https://api.openai.com/auth", {})
    _require(isinstance(auth, dict))
    account = auth.get("chatgpt_account_id") or claims.get("chatgpt_account_id")
    _require(
        isinstance(account, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", account)
    )
    subject = claims.get("sub")
    _require(subject is None or isinstance(subject, str) and 0 < len(subject) <= 512)
    now = time.time()
    expires_in = _number(data.get("expires_in"), maximum=365 * 24 * 3600)
    expires_at = None if expires_in is None else now + expires_in
    # Some token responses omit expires_in; use a JWT expiry when available.
    if expires_at is None and tokens["access_token"].count(".") == 2:
        expires_at = _number(_claims(tokens["access_token"]).get("exp"))
    return {
        **tokens,
        "account_id": account,
        "subject": subject,
        "last_refresh": now,
        "expires_at": expires_at,
    }


def _callback(value, expected_state):
    try:
        if (
            not isinstance(value, str)
            or len(value) > 16384
            or any(ord(c) <= 32 or ord(c) >= 127 for c in value)
        ):
            raise ValueError
        parsed = urlsplit(value)
        # Compare the literal origin/path, not a normalized host. No userinfo,
        # alternate loopback addresses, escaped path, empty fragment, or port.
        if (
            parsed.scheme != "http"
            or parsed.netloc != "localhost:1455"
            or parsed.path != "/auth/callback"
            or "#" in value
            or not value.startswith(_CALLBACK_URL + "?")
            or re.search(r"%(?![0-9a-fA-F]{2})", parsed.query)
        ):
            raise ValueError
        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=8,
            encoding="utf-8",
            errors="strict",
        )
        params = dict(pairs)
        if len(params) != len(pairs) or set(params) - {"code", "state", "scope"}:
            raise ValueError
        if not secrets.compare_digest(
            params.get("state", ""), expected_state
        ) or not re.fullmatch(r"[!-~]{1,4096}", params.get("code", "")):
            raise ValueError
        return params["code"]
    except (ValueError, TypeError, UnicodeError):
        raise ChatGPTError(_CALLBACK_ERROR) from None


async def _request(method, url, **kwargs):
    _require((method, url) in {("POST", _TOKEN_URL), ("GET", _USAGE_URL)})
    # Never inherit proxy credentials, alternate roots, ambient auth, redirects,
    # cookies, or configurable upstream origins. No response body is logged.
    async with (
        httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=_TIMEOUT,
            limits=httpx.Limits(max_connections=3),
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        ) as client,
        client.stream(method, url, **kwargs) as response,
    ):
        if response.status_code == 401 and url == _USAGE_URL:
            raise _Unauthorized(_FAILED)
        _require(response.status_code == 200)
        _require(
            response.headers.get("content-type", "").split(";")[0].strip().lower()
            == "application/json"
        )
        _require(response.headers.get("content-encoding", "identity") == "identity")
        length = response.headers.get("content-length")
        if length is not None:
            _require(length.isdigit() and int(length) <= _MAX_BODY)
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=8192):
            _require(len(body) + len(chunk) <= _MAX_BODY)
            body.extend(chunk)
        return _json(body)


def _result(data):
    _require(isinstance(data, dict) and not data.get("error"))
    _require(
        any(key in data for key in ("rate_limit", "credits", "additional_rate_limits"))
    )
    quota = []

    def windows(bucket, label):
        if bucket is None:
            return
        _require(isinstance(bucket, dict))
        for key in ("primary_window", "secondary_window"):
            window = bucket.get(key)
            if window is None:
                continue
            _require(isinstance(window, dict))
            _require(
                any(
                    field in window
                    for field in (
                        "used_percent",
                        "reset_at",
                        "reset_after_seconds",
                        "limit_window_seconds",
                    )
                )
            )
            used = _number(window.get("used_percent"), maximum=100)
            duration = _number(window.get("limit_window_seconds"), integer=True)
            reset = _number(window.get("reset_at"), integer=True)
            after = _number(window.get("reset_after_seconds"), integer=True)
            if reset is None and after is not None:
                reset = time.time() + after
            suffix = "" if duration is None else f" ({duration / 60:g} min)"
            quota.append(
                {
                    "label": f"{label} · {key.removesuffix('_window')}{suffix}",
                    "used_percent": used,
                    "remaining": None if used is None else 100 - used,
                    "limit": 100,
                    "unit": "percent",
                    "resets_at": None if reset is None else _iso(reset),
                }
            )

    windows(data.get("rate_limit"), "Codex")
    additional = data.get("additional_rate_limits")
    _require(
        additional is None or isinstance(additional, list) and len(additional) <= 32
    )
    for index, bucket in enumerate(additional or [], 1):
        _require(isinstance(bucket, dict))
        for key in ("limit_name", "metered_feature"):
            _require(isinstance(bucket.get(key), str) and 0 < len(bucket[key]) <= 256)
        # Provider text may contain personal data; labels are deliberately local.
        windows(bucket.get("rate_limit"), f"Codex additional {index}")
    credits = data.get("credits")
    if credits is not None:
        _require(isinstance(credits, dict))
        for key in ("has_credits", "unlimited"):
            _require(credits.get(key) is None or type(credits[key]) is bool)
        balance = credits.get("balance")
        if balance is not None:
            _require(
                isinstance(balance, str)
                and re.fullmatch(r"\d{1,15}(?:\.\d{1,12})?", balance)
            )
            balance = _number(float(balance), maximum=10**15)
        quota.append(
            {
                "label": "Codex credits"
                + (" (unlimited)" if credits.get("unlimited") is True else ""),
                "remaining": None if credits.get("unlimited") is True else balance,
                "used_percent": None,
                "limit": None,
                "unit": "credits",
                "resets_at": None,
            }
        )
    return {
        "records": [],
        "quota": quota,
        "capabilities": ["quota"],
        "summary": None,
        "note": "Codex allowance only; not all ChatGPT conversation history or token usage. "
        "Codex 구독 한도이며 ChatGPT 웹 전체 대화 사용량이 아닙니다.",
    }


class ChatGPTManager:
    """One manager per app process, using a trusted private application root.

    All public methods are async except has_credentials(id). start(name) and
    status/cancel/complete return secret-free snapshots sharing a 32-hex id.
    complete(id, callback_url) consumes one valid state once, verifies wham,
    persists encrypted credentials, then awaits on_connected(id, name).
    remove deletes only local credentials, not upstream authorization.
    """

    def __init__(self, root: Path, cipher: Fernet, on_connected):
        self.root = Path(root).resolve() / "chatgpt-browser"
        try:
            _require(not self.root.is_symlink())
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.root, 0o700)
        except OSError:
            raise ChatGPTError(_FAILED) from None
        self.cipher, self.on_connected = cipher, on_connected
        self.sessions = {}
        self.locks = {}
        self.active = set()
        self.closed = False

    def _id(self, account_id):
        if not isinstance(account_id, str) or not re.fullmatch(
            r"[0-9a-f]{32}", account_id
        ):
            raise ChatGPTError(_UNKNOWN)
        return self.root / (account_id + ".enc")

    def has_credentials(self, account_id):
        """Existence only: no decryption, legacy credential reads, or network."""
        try:
            info = self._id(account_id).lstat()
            return stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        except (ChatGPTError, OSError):
            return False

    def _finish(self, session, status, error=None):
        session["public"].update(status=status, error=error, authorization_url=None)
        session["verifier"] = session["state"] = None
        session["finished"] = time.monotonic()
        timer = session.get("timer")
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()

    def _prune(self):
        now = time.monotonic()
        for session in self.sessions.values():
            if (
                session["public"]["status"] == "awaiting_authorization"
                and now >= session["deadline"]
            ):
                self._finish(
                    session, "expired", "ChatGPT login expired. Please start again."
                )
        terminal = [
            key for key, s in self.sessions.items() if s["finished"] is not None
        ]
        for key in terminal:
            if now - self.sessions[key]["finished"] >= _TTL:
                self.sessions.pop(key)
        terminal = [
            key for key, s in self.sessions.items() if s["finished"] is not None
        ]
        for key in terminal[: max(0, len(terminal) - _MAX_HISTORY)]:
            self.sessions.pop(key)

    async def _expire(self, session):
        await asyncio.sleep(max(0, session["deadline"] - time.monotonic()))
        if session["public"]["status"] == "awaiting_authorization":
            self._finish(
                session, "expired", "ChatGPT login expired. Please start again."
            )

    async def start(self, name):
        if self.closed:
            raise ChatGPTError("ChatGPT manager is closed.")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 100
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
        ):
            raise ChatGPTError("Enter an account name of 1–100 characters.")
        self._prune()
        if sum(s["finished"] is None for s in self.sessions.values()) >= 3:
            raise ChatGPTError("At most three ChatGPT logins may be pending.")
        account_id = secrets.token_hex(16)
        verifier, state = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        url = (
            _AUTH_URL
            + "?"
            + urlencode(
                {
                    "response_type": "code",
                    "client_id": _CLIENT_ID,
                    "redirect_uri": _CALLBACK_URL,
                    "scope": _SCOPE + " offline_access",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": state,
                    "id_token_add_organizations": "true",
                    "codex_cli_simplified_flow": "true",
                    "originator": "codex_chatgpt_desktop",
                },
                quote_via=quote,
            )
        )
        session = {
            "public": {
                "id": account_id,
                "status": "awaiting_authorization",
                "authorization_url": url,
                "callback_url": _CALLBACK_URL,
                "expires_at": _iso(time.time() + _TTL),
                "error": None,
            },
            "name": name.strip(),
            "state": state,
            "verifier": verifier,
            "deadline": time.monotonic() + _TTL,
            "finished": None,
            "task": None,
        }
        self.sessions[account_id] = session
        session["timer"] = asyncio.create_task(self._expire(session))
        return session["public"].copy()

    async def status(self, account_id):
        self._id(account_id)
        self._prune()
        if account_id not in self.sessions:
            raise ChatGPTError(_UNKNOWN)
        return self.sessions[account_id]["public"].copy()

    async def complete(self, account_id, callback_url):
        await self.status(account_id)
        if self.closed:
            raise ChatGPTError("ChatGPT manager is closed.")
        session = self.sessions[account_id]
        async with self.locks.setdefault(account_id, asyncio.Lock()):
            self._prune()
            if session["public"]["status"] != "awaiting_authorization":
                raise ChatGPTError("ChatGPT login is no longer awaiting authorization.")
            code = _callback(callback_url, session["state"])
            verifier = session["verifier"]
            # Consume before any await, including errors/ambiguous exchanges:
            # no retry can send this single-use code to the token endpoint again.
            session["state"] = session["verifier"] = None
            session["public"].update(status="authorizing", authorization_url=None)
            task = asyncio.current_task()
            session["task"] = task
            self.active.add(task)
            saved = False
            try:
                budget = min(
                    _OP_TIMEOUT, max(0, session["deadline"] - time.monotonic())
                )
                async with asyncio.timeout(budget):
                    auth = _tokens(
                        await _request(
                            "POST",
                            _TOKEN_URL,
                            data={
                                "grant_type": "authorization_code",
                                "client_id": _CLIENT_ID,
                                "redirect_uri": _CALLBACK_URL,
                                "code": code,
                                "code_verifier": verifier,
                            },
                        )
                    )
                    _result(await self._usage(auth))
                    self._save(account_id, auth)
                    saved = True
                    await self.on_connected(account_id, session["name"])
                    self._finish(session, "connected")
                return session["public"].copy()
            except asyncio.CancelledError:
                self._finish(session, "error", "ChatGPT login was cancelled.")
                raise
            except Exception:  # noqa: BLE001 -- callback/provider errors must not expose credentials.
                self._finish(session, "error", _FAILED)
                raise ChatGPTError(_FAILED) from None
            finally:
                try:
                    if saved and session["public"]["status"] != "connected":
                        self._id(account_id).unlink(missing_ok=True)
                except OSError:
                    raise ChatGPTError(_FAILED) from None
                finally:
                    session["task"] = None
                    self.active.discard(task)

    async def _usage(self, auth):
        return await _request(
            "GET",
            _USAGE_URL,
            headers={
                "Authorization": "Bearer " + auth["access_token"],
                "ChatGPT-Account-Id": auth["account_id"],
            },
        )

    def _save(self, account_id, auth):
        value = self.cipher.encrypt(json.dumps(auth).encode())
        temporary = self.root / ("encrypted-" + secrets.token_hex(16))
        try:
            fd = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(fd, "wb") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._id(account_id))
        finally:
            temporary.unlink(missing_ok=True)

    def _load(self, account_id):
        fd = os.open(self._id(account_id), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
            encrypted = handle.read(_MAX_BODY * 2 + 1)
        _require(len(encrypted) <= _MAX_BODY * 2)
        auth = _json(self.cipher.decrypt(encrypted))
        # Validate all header/refresh material again after decrypting.
        for key in ("access_token", "refresh_token", "id_token"):
            _secret(auth.get(key))
        _require(
            isinstance(auth.get("account_id"), str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", auth["account_id"])
        )
        _require(_number(auth.get("last_refresh")) is not None)
        _number(auth.get("expires_at"))
        return auth

    async def _refresh(self, account_id, auth):
        refreshed = _tokens(
            await _request(
                "POST",
                _TOKEN_URL,
                json={
                    "grant_type": "refresh_token",
                    "client_id": _CLIENT_ID,
                    "refresh_token": auth["refresh_token"],
                    "scope": _SCOPE,
                },
            )
        )
        _require(
            refreshed["account_id"] == auth["account_id"]
            and refreshed["subject"] == auth.get("subject")
        )
        # Persist rotation immediately: later quota failure must not discard a
        # replacement refresh token after the old one was consumed upstream.
        self._save(account_id, refreshed)
        return refreshed

    async def collect(self, account_id):
        self._id(account_id)
        if self.closed or not self.has_credentials(account_id):
            raise ChatGPTError(_UNKNOWN)
        task = asyncio.current_task()
        self.active.add(task)
        try:
            async with (
                asyncio.timeout(_OP_TIMEOUT),
                self.locks.setdefault(account_id, asyncio.Lock()),
            ):
                if self.closed:
                    raise ChatGPTError(_UNKNOWN)
                auth = self._load(account_id)
                now = time.time()
                refreshed = (
                    now - auth["last_refresh"] > _REFRESH_AGE
                    or auth["expires_at"] is not None
                    and auth["expires_at"] <= now + 60
                )
                if refreshed:
                    auth = await self._refresh(account_id, auth)
                try:
                    usage = await self._usage(auth)
                except _Unauthorized:
                    if refreshed:
                        raise
                    auth = await self._refresh(account_id, auth)
                    usage = await self._usage(auth)
                return _result(usage)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- redact transport, encryption, and provider details.
            raise ChatGPTError(
                "ChatGPT allowance could not be read. Reconnect if this persists."
            ) from None
        finally:
            self.active.discard(task)

    async def cancel(self, account_id):
        await self.status(account_id)
        session = self.sessions[account_id]
        task = session["task"]
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if session["public"]["status"] == "awaiting_authorization":
            self._finish(session, "error", "ChatGPT login was cancelled.")
        return session["public"].copy()

    async def remove(self, account_id):
        path = self._id(account_id)
        if account_id in self.sessions:
            await self.cancel(account_id)
        elif not self.has_credentials(account_id):
            raise ChatGPTError(_UNKNOWN)
        try:
            # Keep lock identity stable for queued readers; never resurrect a
            # removed credential through a refresh racing this deletion.
            async with self.locks.setdefault(account_id, asyncio.Lock()):
                path.unlink(missing_ok=True)
                self.sessions.pop(account_id, None)
        except OSError:
            raise ChatGPTError(_FAILED) from None

    async def close(self):
        self.closed = True
        tasks = [task for task in self.active if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        timers = []
        for session in self.sessions.values():
            if session["public"]["status"] == "awaiting_authorization":
                self._finish(session, "error", "ChatGPT login was cancelled.")
            session["timer"].cancel()
            timers.append(session["timer"])
        await asyncio.gather(*timers, return_exceptions=True)
