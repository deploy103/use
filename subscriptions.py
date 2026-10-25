"""Direct, read-only subscription connections; never gateway/API-key login.

Gemini protocol adapted (not a CLI invocation) from google-gemini/gemini-cli
cd1b45c2db0a5ea7b7fc3b67c1ba5879601d6005, code_assist/{oauth2,types,server}.ts.
Copyright 2025 Google LLC, Apache-2.0; see licenses/Gemini-CLI-Apache-2.0.txt.
Modified for manual browser handoff, encrypted storage and quota-only operation.
OAuth application identity comes from explicit operator configuration, never
bundled credentials. No local CLI credential files or account environment tokens
are read. cloud-platform is required by Code Assist; userinfo.email verifies
the account when quota is unavailable. The CLI's profile scope is not requested.

Claude is deliberately unavailable: the official authentication/credential-use
policy at https://code.claude.com/docs/en/legal-and-compliance explicitly forbids
third-party Claude.ai login and credential storage. minzique/claude-code-oauth
c3651baa3a3e15fb30c5b3db627a0faaab757b53 was consulted, not copied: its broad
org:create_api_key/user:inference flow cannot establish permitted read-only auth.
Consequently https://api.anthropic.com/api/oauth/usage is NOT called, and no
Claude Code identity, authorization URL or successful login is fabricated.

One manager per process, with a trusted private application root. The dashboard
must authenticate/authorize the owner of each manager call. Local removal does
not revoke upstream consent. No listener, inference, onboarding, billing, cloud
project creation or automatic terms/privacy acceptance is implemented.
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
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from weakref import WeakValueDictionary

import httpx
from cryptography.fernet import Fernet

from collectors import CollectionError

_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_CALLBACK = "https://codeassist.google.com/authcode"
_TOKEN = "https://oauth2.googleapis.com/token"
_IDENTITY = "https://www.googleapis.com/oauth2/v2/userinfo"
_LOAD = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
_QUOTA = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"
_SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
)
_TTL, _OP_TIMEOUT, _MAX_BODY = 300, 60, 256 * 1024
_INVALID = "The subscription provider returned an invalid or unsupported response."
_FAILED = "Subscription connection failed. Start a new login and try again."
_UNKNOWN = "Unknown subscription account or login."
_CLIENT_REJECTED = (
    "Google rejected this OAuth login or installed client. "
    "Start a new login; no account was connected."
)
_POLICY = (
    "Claude account login is unavailable: Anthropic prohibits third-party "
    "Claude.ai login and OAuth credential storage. No credentials were requested."
)
_SETUP = (
    "Gemini requires an existing Code Assist project and accepted account setup. "
    "Complete setup in the official Gemini CLI, then reconnect. "
    "No project, billing, privacy or terms settings were changed."
)
_UNAVAILABLE = (
    "Gemini Code Assist quota is unavailable; remaining allowance is unknown."
)


class SubscriptionError(CollectionError):
    """Only fixed, credential-free error messages cross the public boundary."""


class _Unauthorized(SubscriptionError):
    pass


class _Unavailable(SubscriptionError):
    pass


def _require(condition):
    if not condition:
        raise SubscriptionError(_INVALID)


def _number(value, maximum=10**15):
    if value is None:
        return None
    _require(type(value) in (int, float) and math.isfinite(value))
    _require(0 <= value <= maximum)
    return value


def _text(value, maximum=512):
    _require(isinstance(value, str) and 0 < len(value) <= maximum)
    _require(all(32 <= ord(c) != 127 for c in value))
    return value


def _secret(value):
    _require(isinstance(value, str) and re.fullmatch(r"[!-~]{1,16384}", value))
    return value


def _iso(stamp):
    return datetime.fromtimestamp(stamp, UTC).isoformat()


def _reset(value):
    if value is None:
        return None
    _text(value, 64)
    _require(
        re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|[+-]\d\d:\d\d)", value
        )
    )
    try:
        return datetime.fromisoformat(value).isoformat()
    except ValueError:
        raise SubscriptionError(_INVALID) from None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _constant(value):
    raise SubscriptionError(_INVALID)


def _json(body):
    _require(0 < len(body) <= _MAX_BODY)
    value = json.loads(body, object_pairs_hook=_pairs, parse_constant=_constant)
    _require(isinstance(value, dict) and "error" not in value)
    return value


async def _request(method, url, **kwargs):
    _require(
        (method, url)
        in {("POST", _TOKEN), ("GET", _IDENTITY), ("POST", _LOAD), ("POST", _QUOTA)}
    )
    async with (
        httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=15,
            limits=httpx.Limits(max_connections=3),
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        ) as client,
        client.stream(method, url, **kwargs) as response,
    ):
        if response.status_code == 401 and url != _TOKEN:
            raise _Unauthorized(_FAILED)
        if url == _QUOTA and response.status_code in {
            403,
            404,
            429,
            500,
            502,
            503,
            504,
        }:
            raise _Unavailable(_UNAVAILABLE)
        if url == _TOKEN and response.status_code in {400, 401, 403}:
            raise SubscriptionError(_CLIENT_REJECTED)
        _require(response.status_code == 200)
        _require(
            response.headers.get("content-type", "").split(";")[0].strip().lower()
            == "application/json"
        )
        _require(response.headers.get("content-encoding", "identity") == "identity")
        length = response.headers.get("content-length")
        _require(length is None or length.isdigit() and int(length) <= _MAX_BODY)
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=8192):
            _require(len(body) + len(chunk) <= _MAX_BODY)
            body.extend(chunk)
        return _json(body)


def _tokens(data, previous=None):
    access = _secret(data.get("access_token"))
    refresh = _secret(data.get("refresh_token", (previous or {}).get("refresh_token")))
    _require(
        isinstance(data.get("token_type"), str)
        and data["token_type"].lower() == "bearer"
    )
    expiry = _number(data.get("expires_in"), 365 * 86400)
    _require(expiry is not None and expiry > 0)
    if "scope" in data:
        _require(isinstance(data["scope"], str) and len(data["scope"]) <= 4096)
        _require(set(_SCOPES).issubset(data["scope"].split()))
    return {
        **(previous or {}),
        "provider": "gemini",
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": time.time() + expiry,
    }


def _gated(value):
    if isinstance(value, dict):
        if value.get("showNotice") is True or value.get("hasAcceptedTos") is False:
            return True
        if value.get("reasonCode") in {
            "VALIDATION_REQUIRED",
            "PRIVACY_NOTICE_REQUIRED",
            "TERMS_ACCEPTANCE_REQUIRED",
        }:
            return True
        if any(
            value.get(key)
            for key in (
                "validationRequired",
                "privacyNoticeRequired",
                "termsAcceptanceRequired",
                "requiresTermsAcceptance",
            )
        ):
            return True
        return any(_gated(item) for item in value.values())
    if isinstance(value, list):
        return any(_gated(item) for item in value)
    return False


def _result(data=None):
    quota = []
    if data is not None:
        _require(isinstance(data, dict))
        buckets = data.get("buckets", [])
        _require(isinstance(buckets, list) and len(buckets) <= 128)
        for index, bucket in enumerate(buckets, 1):
            _require(isinstance(bucket, dict))
            fraction = _number(bucket.get("remainingFraction"), 1)
            # Proto int64 is encoded as a string, not a JSON number. Preserve
            # unknown units: tokenType is not proof of lifetime tokens consumed.
            amount = bucket.get("remainingAmount")
            if amount is not None:
                _require(isinstance(amount, str) and re.fullmatch(r"\d{1,19}", amount))
                _require(int(amount) <= 2**63 - 1)
            for field in ("tokenType", "modelId"):
                if bucket.get(field) is not None:
                    _text(bucket[field], 128)
            reset = _reset(bucket.get("resetTime"))
            label = bucket.get("modelId") or f"Gemini Code Assist bucket {index}"
            quota.append(
                {
                    "label": label,
                    "remaining": None if fraction is None else fraction * 100,
                    "limit": 100 if fraction is not None else None,
                    "used_percent": None if fraction is None else (1 - fraction) * 100,
                    "unit": "percent",
                    "resets_at": reset,
                }
            )
            if amount is not None:
                quota.append(
                    {
                        "label": label + " allowance",
                        "remaining": int(amount),
                        "limit": None,
                        "used_percent": None,
                        "unit": "allowance units",
                        "resets_at": reset,
                    }
                )
    return {
        "records": [],
        "quota": quota,
        "capabilities": ["quota"] if quota else [],
        "summary": None,
        "note": (
            "Gemini Code Assist subscription allowance only; not conversation history, "
            "daily/lifetime tokens, or Gemini web-app usage."
            if quota
            else _UNAVAILABLE
        ),
    }


class SubscriptionManager:
    """Async public API, except has_credentials(id); snapshots never carry tokens.

    complete(id, callback_input) accepts Google's *code only*, as the official
    authcode page/CLI does; it does not pretend the page returns state. PKCE is
    mandatory and binds the single-use code to this flow. Claude start returns
    a terminal error snapshot with null URLs. on_connected(id,name,provider) is
    awaited after verification and encrypted persistence; failure removes the
    credential. The callback must implement its own atomic registration.
    """

    def __init__(self, root: Path, cipher: Fernet, on_connected):
        self.root = Path(root).resolve() / "subscriptions"
        try:
            _require(not self.root.is_symlink())
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.root, 0o700)
        except (OSError, SubscriptionError):
            raise SubscriptionError(_FAILED) from None
        self.client_id = os.environ.get("ATLAS_GEMINI_CLIENT_ID", "")
        self.client_secret = os.environ.get("ATLAS_GEMINI_CLIENT_SECRET", "")
        self.cipher, self.on_connected = cipher, on_connected
        self.sessions = {}
        self.locks = WeakValueDictionary()
        self.active = set()
        self.slots = asyncio.Semaphore(3)
        self.closed = False

    def _client(self):
        if not all(
            re.fullmatch(r"[A-Za-z0-9_.-]{1,4096}", value)
            for value in (self.client_id, self.client_secret)
        ):
            raise SubscriptionError(
                "Gemini OAuth client is not configured. Ask the server owner to set "
                "ATLAS_GEMINI_CLIENT_ID and ATLAS_GEMINI_CLIENT_SECRET."
            )
        return {"client_id": self.client_id, "client_secret": self.client_secret}

    def _path(self, account_id):
        if not isinstance(account_id, str) or not re.fullmatch(
            r"[0-9a-f]{32}", account_id
        ):
            raise SubscriptionError(_UNKNOWN)
        return self.root / (account_id + ".enc")

    def _lock(self, account_id):
        return self.locks.setdefault(account_id, asyncio.Lock())

    def has_credentials(self, account_id):
        try:
            info = self._path(account_id).lstat()
            return (
                stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600
            )
        except (OSError, SubscriptionError):
            return False

    @asynccontextmanager
    async def _operation(self):
        if self.closed or len(self.active) >= 16:
            raise SubscriptionError(
                "Subscription manager is closed or busy. Try again later."
            )
        task = asyncio.current_task()
        self.active.add(task)
        try:
            async with asyncio.timeout(_OP_TIMEOUT), self.slots:
                yield
        finally:
            self.active.discard(task)

    def _finish(self, session, status, error=None):
        session["public"].update(status=status, error=error, authorization_url=None)
        session["state"] = session["verifier"] = None
        session["finished"] = time.monotonic()
        timer = session.get("timer")
        if timer and timer is not asyncio.current_task():
            timer.cancel()

    def _prune(self):
        now = time.monotonic()
        for session in self.sessions.values():
            if (
                session["public"]["status"] == "awaiting_authorization"
                and now >= session["deadline"]
            ):
                self._finish(
                    session, "expired", "Subscription login expired. Start again."
                )
        terminal = [
            key for key, value in self.sessions.items() if value["finished"] is not None
        ]
        for index, key in enumerate(terminal):
            if (
                now - self.sessions[key]["finished"] >= _TTL
                or index < len(terminal) - 64
            ):
                self.sessions.pop(key)

    async def _expire(self, session):
        await asyncio.sleep(max(0, session["deadline"] - time.monotonic()))
        if session["public"]["status"] == "awaiting_authorization":
            self._finish(session, "expired", "Subscription login expired. Start again.")

    async def start(self, provider, name):
        if self.closed:
            raise SubscriptionError("Subscription manager is closed.")
        if provider not in ("claude", "gemini"):
            raise SubscriptionError("Select claude or gemini.")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 100
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
        ):
            raise SubscriptionError("Enter an account name of 1–100 characters.")
        if provider == "gemini":
            self._client()
        self._prune()
        if sum(value["finished"] is None for value in self.sessions.values()) >= 3:
            raise SubscriptionError("At most three subscription logins may be pending.")
        account_id = secrets.token_hex(16)
        verifier, state = secrets.token_urlsafe(64), secrets.token_hex(32)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        url = (
            _AUTH
            + "?"
            + urlencode(
                {
                    "client_id": self.client_id,
                    "redirect_uri": _CALLBACK,
                    "response_type": "code",
                    "access_type": "offline",
                    "prompt": "consent select_account",
                    "scope": " ".join(_SCOPES),
                    "code_challenge_method": "S256",
                    "code_challenge": challenge,
                    "state": state,
                }
            )
        )
        public = {
            "id": account_id,
            "provider": provider,
            "status": "awaiting_authorization",
            "authorization_url": url,
            "callback_url": _CALLBACK,
            "callback_mode": "code",
            "expires_at": _iso(time.time() + _TTL),
            "error": None,
        }
        session = {
            "public": public,
            "name": name.strip(),
            "state": state,
            "verifier": verifier,
            "deadline": time.monotonic() + _TTL,
            "finished": None,
            "task": None,
            "timer": None,
        }
        self.sessions[account_id] = session
        if provider == "claude":
            public["callback_url"] = None
            self._finish(session, "error", _POLICY)
        else:
            session["timer"] = asyncio.create_task(self._expire(session))
        return public.copy()

    async def status(self, account_id):
        self._path(account_id)
        self._prune()
        if account_id not in self.sessions:
            raise SubscriptionError(_UNKNOWN)
        return self.sessions[account_id]["public"].copy()

    async def complete(self, account_id, callback_input):
        await self.status(account_id)
        session = self.sessions[account_id]
        saved = owner = False
        try:
            async with self._operation(), self._lock(account_id):
                self._prune()
                if session["public"]["provider"] == "claude":
                    raise SubscriptionError(_POLICY)
                if session["public"]["status"] != "awaiting_authorization":
                    raise SubscriptionError(
                        "Subscription login is no longer awaiting authorization."
                    )
                # Google's manual flow takes a code, not code#state or a URL.
                # Never strip/split or invent a missing callback state.
                if (
                    not isinstance(callback_input, str)
                    or not re.fullmatch(r"[A-Za-z0-9_./~-]{1,4096}", callback_input)
                    or "://" in callback_input
                ):
                    raise SubscriptionError(
                        "Paste only the Gemini authorization code from this login."
                    )
                verifier = session["verifier"]
                session["verifier"] = session["state"] = None
                session["public"].update(status="authorizing", authorization_url=None)
                session["task"] = asyncio.current_task()
                owner = True
                async with asyncio.timeout(
                    max(0, session["deadline"] - time.monotonic())
                ):
                    auth = _tokens(
                        await _request(
                            "POST",
                            _TOKEN,
                            data={
                                "grant_type": "authorization_code",
                                **self._client(),
                                "redirect_uri": _CALLBACK,
                                "code": callback_input,
                                "code_verifier": verifier,
                            },
                        )
                    )
                    identity = await _request(
                        "GET", _IDENTITY, headers=self._headers(auth)
                    )
                    auth["subject"] = _text(identity.get("id"), 256)
                    auth["project"] = await self._project(auth)
                    await self._quota(
                        auth
                    )  # invalid/401 fails; unavailable stays unknown
                    self._save(account_id, auth)
                    saved = True
                    await self.on_connected(account_id, session["name"], "gemini")
                    self._finish(session, "connected")
                return session["public"].copy()
        except asyncio.CancelledError:
            if owner and session["public"]["status"] == "authorizing":
                self._finish(session, "error", "Subscription login was cancelled.")
            raise
        except Exception as error:
            # Only our fixed messages are safe. Callback exceptions, even a
            # SubscriptionError raised by caller code, must not cross boundary.
            if owner and session["public"]["status"] == "authorizing":
                message = (
                    str(error)
                    if not saved
                    and isinstance(error, SubscriptionError)
                    and str(error) in {_SETUP, _CLIENT_REJECTED}
                    else _FAILED
                )
                self._finish(session, "error", message)
                raise SubscriptionError(message) from None
            if isinstance(error, SubscriptionError):
                raise
            raise SubscriptionError(_FAILED) from None
        finally:
            if owner:
                try:
                    if saved and session["public"]["status"] != "connected":
                        self._path(account_id).unlink(missing_ok=True)
                except OSError:
                    raise SubscriptionError(_FAILED) from None
                finally:
                    session["task"] = None

    @staticmethod
    def _headers(auth):
        return {"Authorization": "Bearer " + auth["access_token"]}

    async def _project(self, auth):
        data = await _request(
            "POST",
            _LOAD,
            headers=self._headers(auth),
            json={
                "metadata": {
                    "ideType": "IDE_UNSPECIFIED",
                    "platform": "PLATFORM_UNSPECIFIED",
                    "pluginType": "GEMINI",
                }
            },
        )
        tier, project = data.get("currentTier"), data.get("cloudaicompanionProject")
        if (
            _gated(data)
            or not isinstance(tier, dict)
            or not tier.get("id")
            or not isinstance(project, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", project)
        ):
            raise SubscriptionError(_SETUP)
        _text(tier["id"], 128)
        return project

    async def _quota(self, auth):
        try:
            data = await _request(
                "POST",
                _QUOTA,
                headers=self._headers(auth),
                json={"project": auth["project"]},
            )
        except (_Unavailable, httpx.TransportError):
            return _result()
        return _result(data)

    def _save(self, account_id, auth):
        temporary = self.root / ("encrypted-" + secrets.token_hex(16))
        try:
            value = self.cipher.encrypt(json.dumps(auth).encode())
            fd = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(fd, "wb") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path(account_id))
        finally:
            temporary.unlink(missing_ok=True)

    def _load(self, account_id):
        fd = os.open(
            self._path(account_id), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            _require(
                stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600
            )
            encrypted = handle.read(_MAX_BODY * 2 + 1)
        _require(len(encrypted) <= _MAX_BODY * 2)
        auth = _json(self.cipher.decrypt(encrypted))
        _require(auth.get("provider") == "gemini")
        _secret(auth.get("access_token"))
        _secret(auth.get("refresh_token"))
        _text(auth.get("subject"), 256)
        _require(
            isinstance(auth.get("project"), str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", auth["project"])
        )
        _require(_number(auth.get("expires_at")) is not None)
        return auth

    async def _refresh(self, account_id, auth):
        data = await _request(
            "POST",
            _TOKEN,
            data={
                "grant_type": "refresh_token",
                **self._client(),
                "refresh_token": auth["refresh_token"],
            },
        )
        updated = _tokens(data, auth)
        # Persist rotated refresh tokens before any subsequent quota request.
        self._save(account_id, updated)
        return updated

    async def collect(self, account_id):
        self._path(account_id)
        try:
            async with self._operation(), self._lock(account_id):
                auth = self._load(account_id)
                refreshed = auth["expires_at"] <= time.time() + 60
                if refreshed:
                    auth = await self._refresh(account_id, auth)
                try:
                    return await self._quota(auth)
                except _Unauthorized:
                    if refreshed:
                        raise
                    auth = await self._refresh(account_id, auth)
                    return await self._quota(auth)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- no raw HTTP, encryption, provider or filesystem errors
            raise SubscriptionError(
                "Subscription allowance could not be read. Reconnect if this persists."
            ) from None

    async def cancel(self, account_id):
        await self.status(account_id)
        session = self.sessions[account_id]
        task = session["task"]
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if session["public"]["status"] == "awaiting_authorization":
            self._finish(session, "error", "Subscription login was cancelled.")
        return session["public"].copy()

    async def remove(self, account_id):
        path = self._path(account_id)
        if account_id in self.sessions:
            await self.cancel(account_id)
        elif not self.has_credentials(account_id):
            raise SubscriptionError(_UNKNOWN)
        try:
            async with self._lock(account_id):
                path.unlink(missing_ok=True)
                self.sessions.pop(account_id, None)
        except OSError:
            raise SubscriptionError(_FAILED) from None

    async def close(self):
        self.closed = True
        tasks = [task for task in self.active if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        timers = []
        for session in self.sessions.values():
            if session["public"]["status"] == "awaiting_authorization":
                self._finish(session, "error", "Subscription login was cancelled.")
            if session["timer"]:
                session["timer"].cancel()
                timers.append(session["timer"])
        await asyncio.gather(*timers, return_exceptions=True)
