"""Fake HTTP responses only: no auth, inference, subprocess, or real network."""

import asyncio
import base64
import copy
import hashlib
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

import chatgpt
from collectors import CollectionError

SECRET = "dummy-never-real-credential"
EMAIL = "dummy@example.invalid"
CALLBACK = "http://localhost:1455/auth/callback"
TOKEN = "https://auth.openai.com/oauth/token"
USAGE = "https://chatgpt.com/backend-api/wham/usage"
QUOTA = {
    "plan_type": "plus",
    "rate_limit": {
        "primary_window": {
            "used_percent": 23.5,
            "reset_at": 1792886400,
            "limit_window_seconds": 18000,
        },
        "secondary_window": {
            "used_percent": 80,
            "reset_after_seconds": 120,
            "limit_window_seconds": 604800,
        },
    },
    "additional_rate_limits": [
        {
            "limit_name": EMAIL,
            "metered_feature": "codex_other",
            "rate_limit": {"primary_window": {"used_percent": 0}},
        }
    ],
    "credits": {"has_credits": True, "unlimited": False, "balance": "12.75"},
}


def jwt(claims):
    payload = (
        base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    )
    return "eyJhbGciOiJub25lIn0." + payload + ".dummy-signature"


def tokens(account="provider-A", suffix="", **extra):
    return {
        "access_token": SECRET + suffix,
        "refresh_token": SECRET + "-refresh" + suffix,
        "id_token": jwt(
            {
                "email": EMAIL,
                "sub": "dummy-subject-" + account,
                "https://api.openai.com/auth": {"chatgpt_account_id": account},
            }
        ),
        "token_type": "Bearer",
        "expires_in": 3600,
        **extra,
    }


def callback(login, **params):
    state = parse_qs(urlsplit(login["authorization_url"]).query)["state"][0]
    return CALLBACK + "?" + urlencode({"code": "dummy-code", "state": state, **params})


@pytest_asyncio.fixture
async def factory(tmp_path, monkeypatch):
    managers = []
    requests = []
    options = []
    responses = []
    real_client = httpx.AsyncClient

    async def handle(request):
        requests.append(request)
        assert (request.method, str(request.url)) in {("POST", TOKEN), ("GET", USAGE)}
        if not responses:
            pytest.fail("Unexpected outbound request (no fake response queued)")
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = await response(request)
        return response

    def client(**kwargs):
        options.append(kwargs.copy())
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        assert kwargs["timeout"] == 15
        return real_client(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(chatgpt.httpx, "AsyncClient", client)
    monkeypatch.setenv("HTTPS_PROXY", "http://must-not-use.invalid:8080")
    monkeypatch.setenv("SSL_CERT_FILE", "/must-not-use")
    monkeypatch.setenv("ATLAS_CODEX_BIN", "/must-not-execute")
    monkeypatch.setenv("AUTH_BASE_URL", "https://must-not-use.invalid")

    def make(callback_hook=None, root=None, cipher=None):
        cipher = cipher or Fernet(Fernet.generate_key())
        connected = []

        async def on_connected(account_id, name):
            assert manager.has_credentials(account_id)
            assert (await manager.status(account_id))["status"] == "authorizing"
            if callback_hook:
                await callback_hook(account_id, name)
            connected.append((account_id, name))

        manager = chatgpt.ChatGPTManager(
            root or tmp_path / str(len(managers)), cipher, on_connected
        )
        managers.append(manager)
        return manager, cipher, connected

    def queue(*values):
        for value in values:
            responses.append(
                value
                if isinstance(value, (httpx.Response, Exception)) or callable(value)
                else httpx.Response(200, json=value)
            )

    make.queue, make.requests, make.options = queue, requests, options
    yield make
    for manager in managers:
        await manager.close()


async def connect(factory, manager, name="Personal", account="provider-A"):
    login = await manager.start(name)
    suffix = "" if account == "provider-A" else "-" + account
    factory.queue(tokens(account, suffix=suffix), QUOTA)
    result = await manager.complete(login["id"], callback(login))
    assert result["id"] == login["id"] and result["status"] == "connected"
    return login


def read_auth(manager, cipher, account_id):
    return json.loads(cipher.decrypt((manager.root / f"{account_id}.enc").read_bytes()))


@pytest.mark.asyncio
async def test_pkce_browser_contract_no_contact_until_user_completion(factory):
    manager, _, _ = factory()
    login = await manager.start("  Personal  ")
    assert re.fullmatch("[0-9a-f]{32}", login["id"])
    assert login["status"] == "awaiting_authorization"
    assert login["callback_url"] == CALLBACK and login["error"] is None
    expiry = datetime.fromisoformat(login["expires_at"]).timestamp()
    assert 895 < expiry - time.time() <= 900
    url = urlsplit(login["authorization_url"])
    assert (url.scheme, url.netloc, url.path) == (
        "https",
        "auth.openai.com",
        "/oauth/authorize",
    )
    params = parse_qs(url.query)
    assert params["client_id"] == ["app_EMoamEEZ73f0CkXaXp7hrann"]
    assert params["redirect_uri"] == [CALLBACK]
    assert params["response_type"] == ["code"]
    assert params["scope"] == ["openid profile email offline_access"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["originator"] == ["codex_chatgpt_desktop"]
    assert params["codex_cli_simplified_flow"] == ["true"]
    session = manager.sessions[login["id"]]
    verifier = session["verifier"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{43,128}", verifier)
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert params["code_challenge"] == [expected]
    assert len(params["state"][0]) >= 32 and verifier not in login["authorization_url"]
    snapshot = await manager.status(login["id"])
    snapshot["status"] = "tampered"
    assert (await manager.status(login["id"]))["status"] == "awaiting_authorization"
    assert not factory.requests and not list(manager.root.iterdir())


@pytest.mark.asyncio
async def test_encrypted_before_registration_quota_only_and_remove(factory):
    manager, cipher, connected = factory()
    login = await connect(factory, manager, "  Personal  ")
    account_id = login["id"]
    assert connected == [(account_id, "Personal")]
    public = await manager.status(account_id)
    assert public["authorization_url"] is None
    assert (
        manager.root.name == "chatgpt-browser"
        and manager.root.stat().st_mode & 0o777 == 0o700
    )
    path = manager.root / (account_id + ".enc")
    assert path.stat().st_mode & 0o777 == 0o600
    assert (
        SECRET.encode() not in path.read_bytes()
        and EMAIL.encode() not in path.read_bytes()
    )
    auth = read_auth(manager, cipher, account_id)
    assert auth["access_token"] == SECRET and auth["account_id"] == "provider-A"
    assert auth["refresh_token"] == SECRET + "-refresh"
    assert "verifier" not in auth and "state" not in auth
    factory.queue(QUOTA)
    before = time.time()
    result = await manager.collect(account_id)
    assert result["records"] == [] and result["summary"] is None
    assert result["capabilities"] == ["quota"]
    assert "not all ChatGPT" in result["note"] and "Codex allowance" in result["note"]
    primary, secondary, additional, credits = result["quota"]
    assert primary["used_percent"] == 23.5 and primary["remaining"] == 76.5
    assert primary["unit"] == "percent" and primary["limit"] == 100
    assert "300 min" in primary["label"]
    assert datetime.fromisoformat(primary["resets_at"]).timestamp() == 1792886400
    assert secondary["used_percent"] == 80 and secondary["remaining"] == 20
    assert (
        before + 120
        <= datetime.fromisoformat(secondary["resets_at"]).timestamp()
        <= time.time() + 120
    )
    assert additional["used_percent"] == 0 and additional["remaining"] == 100
    assert credits["unit"] == "credits" and credits["remaining"] == 12.75
    assert credits["limit"] is credits["used_percent"] is None
    assert SECRET not in json.dumps([public, result]) and EMAIL not in json.dumps(
        [public, result]
    )
    assert (
        manager.sessions[account_id]["state"]
        is manager.sessions[account_id]["verifier"]
        is None
    )
    token_request, usage_request = factory.requests[:2]
    form = parse_qs(token_request.content.decode())
    assert set(form) == {
        "grant_type",
        "client_id",
        "code",
        "code_verifier",
        "redirect_uri",
    }
    assert form["grant_type"] == ["authorization_code"] and form["redirect_uri"] == [
        CALLBACK
    ]
    assert usage_request.headers["authorization"] == "Bearer " + SECRET
    assert usage_request.headers["chatgpt-account-id"] == "provider-A"
    assert usage_request.headers["accept-encoding"] == "identity"
    assert await manager.cancel(account_id) == public
    await manager.remove(account_id)
    assert not path.exists() and not manager.has_credentials(account_id)
    with pytest.raises(chatgpt.ChatGPTError):
        await manager.collect(account_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        lambda u: u.replace("http:", "https:"),
        lambda u: u.replace("localhost", "127.0.0.1"),
        lambda u: u.replace("localhost", "localhost.evil.invalid"),
        lambda u: u.replace("localhost", "LOCALHOST"),
        lambda u: u.replace("localhost", "owner@localhost"),
        lambda u: u.replace(":1455", ":01455"),
        lambda u: u.replace(":1455", ":1456"),
        lambda u: u.replace(":1455", ""),
        lambda u: u.replace("/auth/callback", "/auth/callback/"),
        lambda u: u.replace("/auth/callback", "/auth/%63allback"),
        lambda u: u + "#",
        lambda u: u + "#fragment",
        lambda u: u + "&code=other",
        lambda u: u + "&state=other",
        lambda u: u + "&%73tate=other",
        lambda u: u + "&scope=a&scope=b",
        lambda u: u + "&redirect_uri=https://evil.invalid",
        lambda u: u.replace("code=dummy-code", "error=access_denied"),
        lambda u: u.replace("code=dummy-code", "code="),
        lambda u: u.replace("code=dummy-code", "code=%0Asecret"),
        lambda u: u.replace("code=dummy-code", "code=%FF"),
        lambda u: u.replace("code=dummy-code", "code=%ZZ"),
        lambda u: u.replace("code=dummy-code&", ""),
        lambda u: u.split("&state=")[0],
        lambda u: " " + u,
        lambda u: u + "\n",
        lambda u: "x" * 16385,
        lambda u: None,
    ],
)
async def test_callback_rejects_invalid_urls_duplicates_and_params(factory, change):
    manager, _, _ = factory()
    login = await manager.start("Invalid")
    with pytest.raises(chatgpt.ChatGPTError, match="callback URL or state"):
        await manager.complete(login["id"], change(callback(login)))
    assert not factory.requests and not manager.has_credentials(login["id"])
    assert (await manager.status(login["id"]))["status"] == "awaiting_authorization"


@pytest.mark.asyncio
async def test_state_binding_and_replay_including_concurrent_completion(factory):
    manager, _, connected = factory()
    first, second = await manager.start("First"), await manager.start("Second")
    with pytest.raises(chatgpt.ChatGPTError):
        await manager.complete(first["id"], callback(second))
    assert not factory.requests
    factory.queue(tokens(), QUOTA)
    outcomes = await asyncio.gather(
        manager.complete(first["id"], callback(first, scope="openid profile email")),
        manager.complete(first["id"], callback(first)),
        return_exceptions=True,
    )
    assert sum(isinstance(item, chatgpt.ChatGPTError) for item in outcomes) == 1
    assert len(factory.requests) == 2 and len(connected) == 1
    with pytest.raises(chatgpt.ChatGPTError):
        await manager.complete(first["id"], callback(first))
    assert len(factory.requests) == 2


@pytest.mark.asyncio
async def test_callback_failure_rolls_back_and_never_exposes_error(factory):
    async def fail(*args):
        raise RuntimeError(SECRET + EMAIL)

    manager, _, connected = factory(callback_hook=fail)
    login = await manager.start("Rollback")
    factory.queue(tokens(), QUOTA)
    with pytest.raises(chatgpt.ChatGPTError) as error:
        await manager.complete(login["id"], callback(login))
    result = await manager.status(login["id"])
    assert result["status"] == "error" and not manager.has_credentials(login["id"])
    assert not connected and not list(manager.root.iterdir())
    assert SECRET not in str(error.value) + json.dumps(result)
    assert EMAIL not in str(error.value) + json.dumps(result)
    with pytest.raises(chatgpt.ChatGPTError):
        await manager.complete(login["id"], callback(login))
    assert len(factory.requests) == 2


@pytest.mark.asyncio
async def test_pending_limit_expiry_secret_cleanup_and_close(factory, monkeypatch):
    manager, _, _ = factory()
    logins = [await manager.start(str(i)) for i in range(3)]
    with pytest.raises(chatgpt.ChatGPTError, match="three"):
        await manager.start("Fourth")
    assert (await manager.cancel(logins[0]["id"]))["status"] == "error"
    monkeypatch.setattr(chatgpt, "_TTL", 0.02)
    expired = await manager.start("Expiry")
    await asyncio.sleep(0.03)
    monkeypatch.setattr(chatgpt, "_TTL", 900)
    assert (await manager.status(expired["id"]))["status"] == "expired"
    assert manager.sessions[expired["id"]]["verifier"] is None
    with pytest.raises(chatgpt.ChatGPTError):
        await manager.complete(expired["id"], callback(expired))
    await manager.close()
    assert (await manager.status(logins[1]["id"]))["status"] == "error"
    assert all(s["verifier"] is None for s in manager.sessions.values())
    assert not factory.requests
    with pytest.raises(chatgpt.ChatGPTError, match="closed"):
        await manager.start("Closed")


@pytest.mark.asyncio
async def test_expiry_checked_even_before_timer_runs_and_history_bounded(
    factory, monkeypatch
):
    manager, _, _ = factory()
    login = await manager.start("Expired")
    manager.sessions[login["id"]]["deadline"] = time.monotonic() - 1
    with pytest.raises(chatgpt.ChatGPTError):
        await manager.complete(login["id"], callback(login))
    assert (await manager.status(login["id"]))["status"] == "expired"
    monkeypatch.setattr(chatgpt, "_MAX_HISTORY", 2)
    for i in range(8):
        entry = await manager.start(str(i))
        await manager.cancel(entry["id"])
    manager._prune()
    assert len(manager.sessions) <= 2 and not factory.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["expires_at", "last_refresh", "401"])
async def test_refresh_expiry_eight_day_age_and_unauthorized(factory, reason):
    manager, cipher, _ = factory()
    login = await connect(factory, manager)
    account_id = login["id"]
    auth = read_auth(manager, cipher, account_id)
    if reason == "expires_at":
        auth["expires_at"] = time.time() - 1
    elif reason == "last_refresh":
        auth["last_refresh"] = time.time() - 9 * 86400
    else:
        factory.queue(httpx.Response(401, json={"error": SECRET}))
    manager._save(account_id, auth)
    factory.queue(tokens(suffix="-rotated"), QUOTA)
    result = await manager.collect(account_id)
    assert result["quota"][0]["used_percent"] == 23.5
    request = factory.requests[-2]
    assert request.method == "POST" and str(request.url) == TOKEN
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "grant_type": "refresh_token",
        "refresh_token": SECRET + "-refresh",
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "scope": "openid profile email",
    }
    assert (
        factory.requests[-1].headers["authorization"] == "Bearer " + SECRET + "-rotated"
    )
    assert (
        read_auth(manager, cipher, account_id)["refresh_token"]
        == SECRET + "-refresh-rotated"
    )


@pytest.mark.asyncio
async def test_refresh_rotation_survives_later_quota_failure_and_no_refresh_loop(
    factory,
):
    manager, cipher, _ = factory()
    login = await connect(factory, manager)
    account_id = login["id"]
    factory.queue(httpx.Response(401), tokens(suffix="-new"), httpx.Response(401))
    with pytest.raises(chatgpt.ChatGPTError):
        await manager.collect(account_id)
    assert len(factory.requests) == 5
    assert (
        read_auth(manager, cipher, account_id)["refresh_token"]
        == SECRET + "-refresh-new"
    )
    factory.queue(QUOTA)
    await manager.collect(account_id)
    assert factory.requests[-1].headers["authorization"].endswith("-new")


@pytest.mark.asyncio
async def test_refreshed_identity_cannot_change_accounts(factory):
    manager, cipher, _ = factory()
    login = await connect(factory, manager)
    account_id = login["id"]
    original = read_auth(manager, cipher, account_id)
    factory.queue(httpx.Response(401), tokens("provider-B"))
    with pytest.raises(chatgpt.ChatGPTError):
        await manager.collect(account_id)
    assert read_auth(manager, cipher, account_id) == original
    assert len(factory.requests) == 4


@pytest.mark.asyncio
async def test_restart_isolation_legacy_files_untouched(factory, tmp_path):
    root = tmp_path / "data"
    legacy = root / "chatgpt"
    legacy.mkdir(parents=True)
    old_id = "f" * 32
    (legacy / (old_id + ".enc")).write_text("do not read or delete")
    manager, cipher, connected = factory(root=root)
    assert not manager.has_credentials(old_id)
    first = await connect(factory, manager, "First", "provider-A")
    second = await connect(factory, manager, "Second", "provider-B")
    assert len(connected) == 2
    await manager.close()
    reopened, _, newly_connected = factory(root=root, cipher=cipher)
    factory.queue(QUOTA, QUOTA)
    await asyncio.gather(reopened.collect(first["id"]), reopened.collect(second["id"]))
    assert {r.headers["chatgpt-account-id"] for r in factory.requests[-2:]} == {
        "provider-A",
        "provider-B",
    }
    for request in factory.requests[-2:]:
        expected = SECRET + (
            ""
            if request.headers["chatgpt-account-id"] == "provider-A"
            else "-provider-B"
        )
        assert request.headers["authorization"] == "Bearer " + expected
    assert not newly_connected
    await reopened.remove(first["id"])
    assert not reopened.has_credentials(first["id"]) and reopened.has_credentials(
        second["id"]
    )
    assert (legacy / (old_id + ".enc")).read_text() == "do not read or delete"


@pytest.mark.asyncio
async def test_serialized_refresh_read_and_remove_no_resurrection(factory):
    manager, cipher, _ = factory()
    login = await connect(factory, manager)
    account_id = login["id"]
    auth = read_auth(manager, cipher, account_id)
    auth["expires_at"] = 1
    manager._save(account_id, auth)
    entered, release = asyncio.Event(), asyncio.Event()

    async def refresh(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json=tokens(suffix="-rotated"))

    factory.queue(refresh, QUOTA, QUOTA)
    first = asyncio.create_task(manager.collect(account_id))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(manager.collect(account_id))
    await asyncio.sleep(0)
    assert len(factory.requests) == 3  # second cannot refresh concurrently
    release.set()
    await asyncio.gather(first, second)
    assert len(factory.requests) == 5
    assert read_auth(manager, cipher, account_id)["refresh_token"].endswith("-rotated")
    entered.clear()
    release.clear()

    async def usage(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json=QUOTA)

    factory.queue(usage)
    reading = asyncio.create_task(manager.collect(account_id))
    await asyncio.wait_for(entered.wait(), 1)
    removal = asyncio.create_task(manager.remove(account_id))
    await asyncio.sleep(0)
    queued = asyncio.create_task(manager.collect(account_id))
    release.set()
    outcomes = await asyncio.gather(reading, removal, queued, return_exceptions=True)
    assert isinstance(outcomes[-1], chatgpt.ChatGPTError)
    assert not manager.has_credentials(account_id) and not list(manager.root.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad", [True, -1, 101, "25", 10**40, float("nan"), float("inf")]
)
async def test_invalid_quota_used_percent_fails_closed(factory, bad):
    manager, _, connected = factory()
    login = await manager.start("Invalid quota")
    payload = {"rate_limit": {"primary_window": {"used_percent": bad}}}
    factory.queue(
        tokens(),
        httpx.Response(
            200,
            content=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
        ),
    )
    with pytest.raises(chatgpt.ChatGPTError):
        await manager.complete(login["id"], callback(login))
    assert not connected and not manager.has_credentials(login["id"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"error": SECRET},
        {"rate_limit": []},
        {"rate_limit": {"primary_window": []}},
        {"rate_limit": {"primary_window": {"used_percent": 5, "reset_at": "tomorrow"}}},
        {"additional_rate_limits": {}},
        {"additional_rate_limits": [{}]},
        {"credits": []},
        {"credits": {"balance": True}},
        {"credits": {"balance": "NaN"}},
        {"credits": {"balance": "-1"}},
        {"credits": {"has_credits": "true"}},
    ],
)
async def test_invalid_quota_schema_does_not_connect(factory, payload):
    manager, _, connected = factory()
    login = await manager.start("Bad schema")
    factory.queue(tokens(), payload)
    with pytest.raises(chatgpt.ChatGPTError):
        await manager.complete(login["id"], callback(login))
    assert not connected and not manager.has_credentials(login["id"])


@pytest.mark.asyncio
async def test_unknown_measurements_zero_and_unlimited_credits_are_not_tokens(factory):
    manager, _, _ = factory()
    login = await connect(factory, manager)
    factory.queue({"rate_limit": None, "credits": None, "additional_rate_limits": None})
    result = await manager.collect(login["id"])
    assert (
        result["quota"] == [] and result["summary"] is None and result["records"] == []
    )
    payload = copy.deepcopy(QUOTA)
    payload["rate_limit"]["primary_window"]["used_percent"] = None
    payload["rate_limit"]["secondary_window"]["used_percent"] = 100
    payload["credits"] = {"unlimited": True, "has_credits": True, "balance": "0"}
    factory.queue(payload)
    result = await manager.collect(login["id"])
    assert result["quota"][0]["used_percent"] is result["quota"][0]["remaining"] is None
    assert result["quota"][1]["remaining"] == 0
    assert result["quota"][-1]["remaining"] is None
    assert "unlimited" in result["quota"][-1]["label"]
    assert result["capabilities"] == ["quota"] and result["summary"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"location": "https://evil.invalid/"}),
        httpx.Response(400, json={"error": SECRET + EMAIL}),
        httpx.Response(200, text=SECRET),
        httpx.Response(
            200, content=b"{invalid", headers={"content-type": "application/json"}
        ),
        httpx.Response(
            200,
            content=b'{"rate_limit":null,"rate_limit":null}',
            headers={"content-type": "application/json"},
        ),
        httpx.Response(
            200,
            content=b"x" * (256 * 1024 + 1),
            headers={"content-type": "application/json"},
        ),
        httpx.Response(200, json=QUOTA, headers={"content-length": "999999999"}),
        httpx.ReadTimeout(SECRET + EMAIL),
    ],
)
async def test_transport_and_json_failures_are_safe_and_never_follow_redirect(
    factory, response
):
    manager, _, _ = factory()
    login = await connect(factory, manager)
    factory.queue(response)
    with pytest.raises(chatgpt.ChatGPTError) as error:
        await manager.collect(login["id"])
    assert isinstance(error.value, CollectionError)
    assert SECRET not in str(error.value) and EMAIL not in str(error.value)
    assert manager.has_credentials(login["id"]) and len(factory.requests) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch",
    [
        {"access_token": ""},
        {"refresh_token": None},
        {"id_token": "not-jwt"},
        {"access_token": "bearer\nheader"},
        {"expires_in": True},
        {
            "id_token": jwt(
                {"https://api.openai.com/auth": {"chatgpt_account_id": "bad\nheader"}}
            )
        },
        {"token_type": "Basic"},
    ],
)
async def test_bad_token_schema_never_registers_or_persists(factory, patch):
    manager, _, connected = factory()
    login = await manager.start("Bad tokens")
    factory.queue(tokens(**patch))
    with pytest.raises(chatgpt.ChatGPTError):
        await manager.complete(login["id"], callback(login))
    assert (
        not connected
        and not list(manager.root.iterdir())
        and len(factory.requests) == 1
    )


@pytest.mark.asyncio
async def test_invalid_names_ids_tamper_symlink_and_hardlink_fail_safe(
    factory, tmp_path
):
    manager, _, _ = factory()
    for name in ("", " ", None, "x" * 101, "bad\nname", "bad\x7fname"):
        with pytest.raises(chatgpt.ChatGPTError):
            await manager.start(name)
    for bad in ("../outside", "a" * 31, "A" * 32, "a" * 33, "0" * 32, None):
        assert not manager.has_credentials(bad)
        for method in (manager.status, manager.cancel, manager.remove, manager.collect):
            with pytest.raises(chatgpt.ChatGPTError):
                await method(bad)
        with pytest.raises(chatgpt.ChatGPTError):
            await manager.complete(bad, CALLBACK)
    outside = tmp_path / "outside"
    outside.write_text(SECRET)
    path = manager.root / ("1" * 32 + ".enc")
    path.symlink_to(outside)
    assert not manager.has_credentials("1" * 32)
    with pytest.raises(chatgpt.ChatGPTError):
        await manager.collect("1" * 32)
    path.unlink()
    os.link(outside, path)
    assert not manager.has_credentials("1" * 32)
    path.unlink()
    path.write_text("not encrypted")
    with pytest.raises(chatgpt.ChatGPTError):
        await manager.collect("1" * 32)
    assert outside.read_text() == SECRET and not factory.requests


@pytest.mark.asyncio
async def test_cancel_inflight_callback_and_shutdown_rollback(factory):
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait(*args):
        entered.set()
        await release.wait()

    manager, _, connected = factory(callback_hook=wait)
    login = await manager.start("Cancel")
    factory.queue(tokens(), QUOTA)
    task = asyncio.create_task(manager.complete(login["id"], callback(login)))
    await asyncio.wait_for(entered.wait(), 1)
    assert manager.has_credentials(login["id"])
    assert (await manager.status(login["id"]))["status"] == "authorizing"
    await manager.cancel(login["id"])
    assert (
        task.cancelled() and not connected and not manager.has_credentials(login["id"])
    )
    entered.clear()
    second = await manager.start("Close")
    factory.queue(tokens(), QUOTA)
    task = asyncio.create_task(manager.complete(second["id"], callback(second)))
    await asyncio.wait_for(entered.wait(), 1)
    await manager.close()
    assert task.cancelled() and not manager.has_credentials(second["id"])


@pytest.mark.asyncio
async def test_whole_operation_timeout_and_close_cancels_collection(
    factory, monkeypatch
):
    manager, _, _ = factory()
    login = await connect(factory, manager)
    entered = asyncio.Event()

    async def hang(request):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(chatgpt, "_OP_TIMEOUT", 0.02)
    factory.queue(hang)
    with pytest.raises(chatgpt.ChatGPTError):
        await manager.collect(login["id"])
    assert manager.has_credentials(login["id"])
    monkeypatch.setattr(chatgpt, "_OP_TIMEOUT", 45)
    entered.clear()
    factory.queue(hang)
    task = asyncio.create_task(manager.collect(login["id"]))
    await asyncio.wait_for(entered.wait(), 1)
    await manager.close()
    assert task.cancelled() and manager.has_credentials(login["id"])


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, fail=False):
        self.chunks, self.fail, self.closed = chunks, fail, False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.fail:
            raise httpx.ReadError(SECRET)

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["oversized", "partial", "encoding"])
async def test_streaming_bounds_and_transport_failure_close_responses(factory, failure):
    manager, _, _ = factory()
    login = await connect(factory, manager)
    if failure == "oversized":
        stream = _Chunks([b"x" * 8192] * 33)
    elif failure == "partial":
        stream = _Chunks([b'{"rate_limit":'], fail=True)
    else:
        stream = _Chunks([b"not compressed"])
    headers = {"content-type": "application/json"}
    if failure == "encoding":
        headers["content-encoding"] = "gzip"
    factory.queue(httpx.Response(200, stream=stream, headers=headers))
    with pytest.raises(chatgpt.ChatGPTError):
        await manager.collect(login["id"])
    assert stream.closed and manager.has_credentials(login["id"])


@pytest.mark.asyncio
async def test_access_jwt_expiry_fallback_and_failed_refresh_keep_original(factory):
    manager, cipher, _ = factory()
    login = await manager.start("JWT expiry")
    payload = tokens(access_token=jwt({"exp": time.time() - 1}))
    payload.pop("expires_in")
    factory.queue(payload, QUOTA)
    await manager.complete(login["id"], callback(login))
    original = read_auth(manager, cipher, login["id"])
    assert original["expires_at"] < time.time()
    factory.queue(httpx.Response(400, json={"error": SECRET + EMAIL}))
    with pytest.raises(chatgpt.ChatGPTError) as error:
        await manager.collect(login["id"])
    assert SECRET not in str(error.value) and EMAIL not in str(error.value)
    assert read_auth(manager, cipher, login["id"]) == original
    assert len(factory.requests) == 3


@pytest.mark.asyncio
async def test_credentials_directory_symlink_rejected_without_writes(factory, tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "chatgpt-browser").symlink_to(outside, target_is_directory=True)
    with pytest.raises(chatgpt.ChatGPTError):
        factory(root=root)
    assert not list(outside.iterdir()) and not factory.requests
