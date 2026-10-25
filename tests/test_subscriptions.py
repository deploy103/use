"""Bounded fake HTTP only; never real login, credentials, inference or setup."""

import asyncio
import base64
import hashlib
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

import subscriptions
from collectors import CollectionError

pytestmark = pytest.mark.asyncio
TOKEN = "https://oauth2.googleapis.com/token"
IDENTITY = "https://www.googleapis.com/oauth2/v2/userinfo"
LOAD = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
QUOTA = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"
SECRET = "dummy-only-never-real-access-token"
REFRESH = "dummy-only-never-real-refresh-token"
EMAIL = "synthetic@example.invalid"
PROJECT = {
    "currentTier": {"id": "free-tier"},
    "cloudaicompanionProject": "synthetic-project",
}
ALLOWANCE = {
    "buckets": [
        {
            "modelId": "gemini-2.5-pro",
            "tokenType": "REQUESTS",
            "remainingFraction": 0.25,
            "remainingAmount": "12",
            "resetTime": "2026-10-26T00:00:00Z",
        }
    ]
}


def tokens(**updates):
    return {
        "access_token": SECRET,
        "refresh_token": REFRESH,
        "token_type": "Bearer",
        "expires_in": 3600,
        **updates,
    }


@pytest_asyncio.fixture
async def factory(tmp_path, monkeypatch):
    managers, pending, requests = [], [], []
    real_client = httpx.AsyncClient
    monkeypatch.setenv(
        "ATLAS_GEMINI_CLIENT_ID", "synthetic-client.apps.googleusercontent.com"
    )
    monkeypatch.setenv("ATLAS_GEMINI_CLIENT_SECRET", "synthetic-client-secret")

    async def handle(request):
        requests.append(request)
        assert (request.method, str(request.url)) in {
            ("POST", TOKEN),
            ("GET", IDENTITY),
            ("POST", LOAD),
            ("POST", QUOTA),
        }
        assert pending, "Unexpected outbound request: no mock response queued"
        response = pending.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = await response(request)
        return response

    def client(**options):
        assert options["trust_env"] is False
        assert options["follow_redirects"] is False
        assert options["timeout"] == 15
        return real_client(**options, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(subscriptions.httpx, "AsyncClient", client)
    monkeypatch.setenv("HTTPS_PROXY", "http://must-not-use.invalid:8080")
    monkeypatch.setenv("SSL_CERT_FILE", "/must-not-read")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/must-not-read")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "must-not-use")

    def make(hook=None, root=None, cipher=None):
        connected = []
        cipher = cipher or Fernet(Fernet.generate_key())

        async def on_connected(account_id, name, provider):
            assert manager.has_credentials(account_id)
            assert (await manager.status(account_id))["status"] == "authorizing"
            if hook:
                await hook(account_id, name, provider)
            connected.append((account_id, name, provider))

        manager = subscriptions.SubscriptionManager(
            root or tmp_path / str(len(managers)), cipher, on_connected
        )
        managers.append(manager)
        return manager, cipher, connected

    def queue(*values):
        pending.extend(
            value
            if isinstance(value, (httpx.Response, Exception)) or callable(value)
            else httpx.Response(200, json=value)
            for value in values
        )

    make.queue, make.requests, make.pending = queue, requests, pending
    yield make
    for manager in managers:
        await manager.close()


async def connect(factory, manager, quota=ALLOWANCE):
    login = await manager.start("gemini", "  Personal  ")
    factory.queue(tokens(), {"id": "synthetic-subject", "email": EMAIL}, PROJECT, quota)
    result = await manager.complete(login["id"], "4/synthetic-code")
    assert result["status"] == "connected"
    return login["id"]


def read_auth(manager, cipher, account_id):
    return json.loads(
        cipher.decrypt((manager.root / (account_id + ".enc")).read_bytes())
    )


async def test_start_is_local_pkce_installed_client_and_minimal_scopes(factory):
    manager, _, _ = factory()
    login = await manager.start("gemini", "Personal")
    assert set(login) == {
        "id",
        "provider",
        "status",
        "authorization_url",
        "callback_url",
        "callback_mode",
        "expires_at",
        "error",
    }
    assert re.fullmatch(r"[0-9a-f]{32}", login["id"])
    assert login["provider"] == "gemini" and login["status"] == "awaiting_authorization"
    assert login["callback_mode"] == "code" and login["error"] is None
    assert login["callback_url"] == "https://codeassist.google.com/authcode"
    assert (
        295
        < datetime.fromisoformat(login["expires_at"]).timestamp() - time.time()
        <= 300
    )
    url = urlsplit(login["authorization_url"])
    assert (url.scheme, url.netloc, url.path) == (
        "https",
        "accounts.google.com",
        "/o/oauth2/v2/auth",
    )
    query = parse_qs(url.query)
    assert query["client_id"] == ["synthetic-client.apps.googleusercontent.com"]
    assert query["redirect_uri"] == [login["callback_url"]]
    assert query["response_type"] == ["code"] and query["access_type"] == ["offline"]
    assert query["code_challenge_method"] == ["S256"]
    assert set(query["scope"][0].split()) == {
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/userinfo.email",
    }
    assert re.fullmatch("[0-9a-f]{64}", query["state"][0])
    assert not factory.requests and not manager.has_credentials(login["id"])
    login["status"] = "forged"
    assert (await manager.status(login["id"]))["status"] == "awaiting_authorization"


@pytest.mark.parametrize(
    "client_id,client_secret",
    [
        ("", ""),
        ("synthetic-client", ""),
        ("", "synthetic-secret"),
        ("invalid\nclient", "synthetic-secret"),
        ("synthetic-client", "invalid secret"),
    ],
)
async def test_unconfigured_client_fails_before_creating_login_or_request(
    factory, monkeypatch, client_id, client_secret
):
    monkeypatch.setenv("ATLAS_GEMINI_CLIENT_ID", client_id)
    monkeypatch.setenv("ATLAS_GEMINI_CLIENT_SECRET", client_secret)
    manager, _, connected = factory()
    with pytest.raises(CollectionError, match="Gemini OAuth client is not configured"):
        await manager.start("gemini", "Personal")
    assert not factory.requests and not connected
    assert not list(manager.root.iterdir())
    assert not manager.sessions
    claude = await manager.start("claude", "Claude")
    assert claude["status"] == "error" and "Anthropic prohibits" in claude["error"]


async def test_claude_policy_failure_is_specific_and_never_authenticates(factory):
    manager, _, connected = factory()
    login = await manager.start("claude", "Claude")
    assert login["status"] == "error" and "Anthropic prohibits" in login["error"]
    assert login["authorization_url"] is None and login["callback_url"] is None
    with pytest.raises(CollectionError, match="Anthropic prohibits"):
        await manager.complete(login["id"], "code#state")
    assert (
        not connected
        and not factory.requests
        and not manager.has_credentials(login["id"])
    )
    with pytest.raises(CollectionError):
        await manager.collect(login["id"])
    await manager.remove(login["id"])


async def test_complete_exchange_encrypted_store_and_read_only_collection(factory):
    manager, cipher, connected = factory()
    login = await manager.start("gemini", "  Personal  ")
    query = parse_qs(urlsplit(login["authorization_url"]).query)
    factory.queue(
        tokens(), {"id": "synthetic-subject", "email": EMAIL}, PROJECT, ALLOWANCE
    )
    done = await manager.complete(login["id"], "4/synthetic-code")
    assert done["status"] == "connected" and done["authorization_url"] is None
    assert connected == [(login["id"], "Personal", "gemini")]
    exchange = parse_qs(factory.requests[0].content.decode())
    assert exchange["grant_type"] == ["authorization_code"]
    assert exchange["redirect_uri"] == ["https://codeassist.google.com/authcode"]
    assert exchange["code"] == ["4/synthetic-code"]
    verifier = exchange["code_verifier"][0]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert query["code_challenge"] == [challenge]
    assert "state" not in exchange  # manual authcode flow does not return state
    assert exchange["client_id"] == ["synthetic-client.apps.googleusercontent.com"]
    assert exchange["client_secret"] == ["synthetic-client-secret"]
    assert factory.requests[1].headers["Authorization"] == "Bearer " + SECRET
    assert json.loads(factory.requests[2].content) == {
        "metadata": {
            "ideType": "IDE_UNSPECIFIED",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI",
        }
    }
    assert json.loads(factory.requests[3].content) == {"project": "synthetic-project"}
    path = manager.root / (login["id"] + ".enc")
    assert (
        manager.root.name == "subscriptions"
        and manager.root.stat().st_mode & 0o777 == 0o700
    )
    assert path.stat().st_mode & 0o777 == 0o600
    assert (
        SECRET.encode() not in path.read_bytes()
        and REFRESH.encode() not in path.read_bytes()
    )
    auth = read_auth(manager, cipher, login["id"])
    assert auth["subject"] == "synthetic-subject" and "email" not in auth
    factory.queue(ALLOWANCE)
    result = await manager.collect(login["id"])
    assert result["records"] == [] and result["summary"] is None
    assert result["capabilities"] == ["quota"]
    assert result["quota"][0] == {
        "label": "gemini-2.5-pro",
        "remaining": 25,
        "limit": 100,
        "used_percent": 75,
        "unit": "percent",
        "resets_at": "2026-10-26T00:00:00+00:00",
    }
    assert result["quota"][1]["remaining"] == 12 and result["quota"][1]["limit"] is None
    public = json.dumps([done, result])
    assert all(
        secret not in public for secret in (SECRET, REFRESH, EMAIL, "synthetic-subject")
    )
    assert not any(
        field in result
        for field in ("access_token", "refresh_token", "id_token", "expires_in")
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "code#wrong-state",
        "code#state#extra",
        "https://codeassist.google.com/authcode?code=x&state=wrong",
        "code state",
        "code\n",
        "x" * 4097,
        None,
        {"code": "x"},
    ],
)
async def test_manual_code_contract_rejects_forged_state_url_and_oversize(
    factory, value
):
    manager, _, connected = factory()
    login = await manager.start("gemini", "Name")
    with pytest.raises(CollectionError, match="only the Gemini authorization code"):
        await manager.complete(login["id"], value)
    assert not factory.requests and not connected
    assert (await manager.status(login["id"]))["status"] == "awaiting_authorization"


async def test_replay_rejected_after_success_and_ambiguous_failure(factory):
    manager, _, connected = factory()
    account_id = await connect(factory, manager)
    count = len(factory.requests)
    with pytest.raises(CollectionError, match="no longer"):
        await manager.complete(account_id, "4/synthetic-code")
    assert len(factory.requests) == count and len(connected) == 1
    login = await manager.start("gemini", "Fail")
    factory.queue(httpx.ReadTimeout(SECRET))
    with pytest.raises(CollectionError) as caught:
        await manager.complete(login["id"], "4/ambiguous")
    assert SECRET not in str(caught.value)
    assert (await manager.status(login["id"]))["status"] == "error"
    count = len(factory.requests)
    with pytest.raises(CollectionError, match="no longer"):
        await manager.complete(login["id"], "4/ambiguous")
    assert len(factory.requests) == count and not manager.has_credentials(login["id"])


async def test_expiry_cancel_pending_limit_and_history_bound(factory):
    manager, _, _ = factory()
    logins = [await manager.start("gemini", str(index)) for index in range(3)]
    with pytest.raises(CollectionError, match="At most three"):
        await manager.start("gemini", "overflow")
    manager.sessions[logins[0]["id"]]["deadline"] = time.monotonic() - 1
    assert (await manager.status(logins[0]["id"]))["status"] == "expired"
    with pytest.raises(CollectionError, match="no longer"):
        await manager.complete(logins[0]["id"], "4/expired")
    assert (await manager.cancel(logins[1]["id"]))["status"] == "error"
    assert manager.sessions[logins[1]["id"]]["verifier"] is None
    for index in range(80):
        await manager.start("claude", str(index))
    assert len(manager.sessions) <= 66
    assert not factory.requests


@pytest.mark.parametrize("data", [{}, {"buckets": []}])
async def test_missing_quota_is_unknown_not_zero_or_tokens(factory, data):
    manager, _, connected = factory()
    account_id = await connect(factory, manager, quota=data)
    assert connected
    factory.queue(data)
    result = await manager.collect(account_id)
    assert result == {
        "records": [],
        "quota": [],
        "capabilities": [],
        "summary": None,
        "note": "Gemini Code Assist quota is unavailable; remaining allowance is unknown.",
    }


async def test_missing_bucket_fields_preserve_unknown(factory):
    manager, _, _ = factory()
    account_id = await connect(factory, manager)
    factory.queue(
        {
            "buckets": [
                {"modelId": "gemini-2.5-pro"},
                {"modelId": "gemini-2.5-flash", "remainingFraction": 0.8},
                {"remainingFraction": 0.1},
            ]
        }
    )
    quotas = (await manager.collect(account_id))["quota"]
    bucket = quotas[0]
    assert [item["label"] for item in quotas] == [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "Gemini Code Assist bucket 3",
    ]
    assert quotas[1]["remaining"] == 80 and quotas[2]["remaining"] == 10
    assert (
        bucket["remaining"] is None
        and bucket["limit"] is None
        and bucket["used_percent"] is None
    )


@pytest.mark.parametrize("status", [403, 404, 429, 500, 502, 503, 504])
async def test_quota_unavailable_after_identity_verification_can_connect(
    factory, status
):
    manager, _, _ = factory()
    account_id = await connect(
        factory, manager, quota=httpx.Response(status, text=SECRET)
    )
    factory.queue(httpx.Response(status, text=SECRET))
    result = await manager.collect(account_id)
    assert (
        result["quota"] == []
        and "unknown" in result["note"]
        and SECRET not in str(result)
    )


async def test_quota_transport_unavailable_is_unknown(factory):
    manager, _, _ = factory()
    account_id = await connect(factory, manager, quota=httpx.ConnectError(SECRET))
    factory.queue(httpx.ReadTimeout(SECRET))
    assert (await manager.collect(account_id))["quota"] == []


@pytest.mark.parametrize(
    "setup",
    [
        {},
        {"currentTier": {"id": "free-tier"}},
        {**PROJECT, "currentTier": None},
        {**PROJECT, "cloudaicompanionProject": {"id": "wrong-schema"}},
        {
            **PROJECT,
            "currentTier": {"id": "free-tier", "privacyNotice": {"showNotice": True}},
        },
        {**PROJECT, "currentTier": {"id": "free-tier", "hasAcceptedTos": False}},
        {
            **PROJECT,
            "ineligibleTiers": [
                {"reasonCode": "VALIDATION_REQUIRED", "validationUrl": SECRET}
            ],
        },
    ],
)
async def test_setup_or_terms_gates_do_not_onboard_or_register(factory, setup):
    manager, _, connected = factory()
    login = await manager.start("gemini", "Name")
    factory.queue(tokens(), {"id": "synthetic-subject"}, setup)
    with pytest.raises(CollectionError, match="official Gemini CLI") as caught:
        await manager.complete(login["id"], "4/code")
    assert (
        SECRET not in str(caught.value)
        and not connected
        and not manager.has_credentials(login["id"])
    )
    assert (
        len(factory.requests) == 3
        and (await manager.status(login["id"]))["status"] == "error"
    )


@pytest.mark.parametrize(
    "response",
    [
        tokens(refresh_token=None),
        tokens(expires_in=True),
        tokens(expires_in=0),
        httpx.Response(
            200,
            content=b'{"expires_in":Infinity}',
            headers={"content-type": "application/json"},
        ),
        tokens(access_token="bad\nheader"),
        tokens(token_type="Basic"),
        tokens(scope="https://www.googleapis.com/auth/userinfo.email"),
        httpx.Response(302, headers={"Location": "https://must-not-follow.invalid/"}),
        httpx.Response(200, text=SECRET, headers={"content-type": "text/plain"}),
        httpx.Response(
            200,
            content=b'{"access_token":"one","access_token":"two"}',
            headers={"content-type": "application/json"},
        ),
        httpx.Response(
            200,
            content=b"x" * (256 * 1024 + 1),
            headers={"content-type": "application/json"},
        ),
    ],
)
async def test_invalid_tokens_transport_and_body_bound_do_not_register(
    factory, response
):
    manager, _, connected = factory()
    login = await manager.start("gemini", "Name")
    factory.queue(response)
    with pytest.raises(CollectionError) as caught:
        await manager.complete(login["id"], "4/code")
    assert SECRET not in str(caught.value)
    assert not connected and not manager.has_credentials(login["id"])
    assert len(factory.requests) == 1


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_installed_client_rejection_specific_not_success(factory, status):
    manager, _, connected = factory()
    login = await manager.start("gemini", "Name")
    factory.queue(
        httpx.Response(status, json={"error": "unauthorized_client", "details": SECRET})
    )
    with pytest.raises(
        CollectionError, match="Google rejected this OAuth login or installed client"
    ):
        await manager.complete(login["id"], "4/code")
    assert not connected and not manager.has_credentials(login["id"])


@pytest.mark.parametrize(
    "data",
    [
        {"buckets": None},
        {"buckets": [None]},
        {"buckets": [{"remainingFraction": True}]},
        {"buckets": [{"remainingFraction": -0.1}]},
        {"buckets": [{"remainingFraction": 1.01}]},
        {"buckets": [{"remainingAmount": 10}]},
        {"buckets": [{"remainingAmount": "-1"}]},
        {"buckets": [{"remainingAmount": "9223372036854775808"}]},
        {"buckets": [{"resetTime": "not a date"}]},
        {"buckets": [{"resetTime": "2026-99-26T00:00:00Z"}]},
        {"buckets": [{"modelId": {"access_token": SECRET}}]},
        {"buckets": [{}] * 129},
    ],
)
async def test_malformed_quota_never_becomes_connected_or_token_stats(factory, data):
    manager, _, connected = factory()
    login = await manager.start("gemini", "Name")
    factory.queue(tokens(), {"id": "synthetic-subject"}, PROJECT, data)
    with pytest.raises(CollectionError):
        await manager.complete(login["id"], "4/code")
    assert not connected and not manager.has_credentials(login["id"])


async def test_callback_failure_redacted_and_saved_credential_removed(factory):
    async def fail(*args):
        raise subscriptions.SubscriptionError(SECRET)

    manager, _, connected = factory(hook=fail)
    login = await manager.start("gemini", "Name")
    factory.queue(tokens(), {"id": "synthetic-subject"}, PROJECT, ALLOWANCE)
    with pytest.raises(CollectionError) as caught:
        await manager.complete(login["id"], "4/code")
    assert SECRET not in str(caught.value)
    assert not connected and not manager.has_credentials(login["id"])
    assert list(manager.root.iterdir()) == []


async def test_refresh_rotation_serialized_and_persisted_before_unknown_quota(factory):
    manager, cipher, _ = factory()
    account_id = await connect(factory, manager)
    auth = read_auth(manager, cipher, account_id)
    auth["expires_at"] = 1
    manager._save(account_id, auth)
    factory.queue(
        tokens(access_token=SECRET + "-new", refresh_token=REFRESH + "-new"),
        httpx.Response(503),
        ALLOWANCE,
    )
    first, second = await asyncio.gather(
        manager.collect(account_id), manager.collect(account_id)
    )
    assert first["quota"] == [] and second["quota"][0]["remaining"] == 25
    assert (
        len([request for request in factory.requests if str(request.url) == TOKEN]) == 2
    )
    exchange = parse_qs(factory.requests[4].content.decode())
    assert exchange["grant_type"] == ["refresh_token"] and exchange[
        "refresh_token"
    ] == [REFRESH]
    assert read_auth(manager, cipher, account_id)["refresh_token"] == REFRESH + "-new"
    assert factory.requests[-1].headers["Authorization"] == "Bearer " + SECRET + "-new"


async def test_refresh_omitted_refresh_token_preserved_and_401_retries_once(factory):
    manager, cipher, _ = factory()
    account_id = await connect(factory, manager)
    refreshed = tokens(access_token=SECRET + "-new")
    del refreshed["refresh_token"]
    factory.queue(httpx.Response(401), refreshed, ALLOWANCE)
    assert (await manager.collect(account_id))["quota"][0]["used_percent"] == 75
    assert read_auth(manager, cipher, account_id)["refresh_token"] == REFRESH
    factory.queue(httpx.Response(401), refreshed, httpx.Response(401))
    with pytest.raises(CollectionError):
        await manager.collect(account_id)
    assert not factory.pending


async def test_failed_refresh_redacted_keeps_existing_store(factory):
    manager, cipher, _ = factory()
    account_id = await connect(factory, manager)
    auth = read_auth(manager, cipher, account_id)
    auth["expires_at"] = 1
    manager._save(account_id, auth)
    factory.queue(httpx.Response(400, text=SECRET))
    with pytest.raises(CollectionError) as caught:
        await manager.collect(account_id)
    assert SECRET not in str(caught.value)
    assert read_auth(manager, cipher, account_id)["refresh_token"] == REFRESH


async def test_remove_waits_for_refresh_and_no_resurrection(factory):
    manager, _, _ = factory()
    account_id = await connect(factory, manager)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json=tokens(refresh_token=REFRESH + "-new"))

    factory.queue(httpx.Response(401), blocked, ALLOWANCE)
    collect = asyncio.create_task(manager.collect(account_id))
    await asyncio.wait_for(entered.wait(), 1)
    remove = asyncio.create_task(manager.remove(account_id))
    await asyncio.sleep(0)
    assert not remove.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(collect, remove), 2)
    assert (
        not manager.has_credentials(account_id) and list(manager.root.iterdir()) == []
    )
    with pytest.raises(CollectionError):
        await manager.collect(account_id)


async def test_cancel_during_exchange_and_duplicate_cancellation_do_not_cross_flows(
    factory,
):
    manager, _, connected = factory()
    login = await manager.start("gemini", "Name")
    entered = asyncio.Event()

    async def blocked(request):
        entered.set()
        await asyncio.Event().wait()

    factory.queue(blocked)
    owner = asyncio.create_task(manager.complete(login["id"], "4/code"))
    await asyncio.wait_for(entered.wait(), 1)
    duplicate = asyncio.create_task(manager.complete(login["id"], "4/code"))
    await asyncio.sleep(0)
    duplicate.cancel()
    await asyncio.gather(duplicate, return_exceptions=True)
    assert (await manager.status(login["id"]))["status"] == "authorizing"
    assert (await manager.cancel(login["id"]))["status"] == "error"
    assert (
        owner.cancelled() and not connected and not manager.has_credentials(login["id"])
    )
    assert not manager.active


async def test_restart_persistence_corruption_permissions_links_and_paths(
    factory, tmp_path
):
    manager, cipher, _ = factory()
    account_id = await connect(factory, manager)
    restarted, _, _ = factory(root=manager.root.parent, cipher=cipher)
    factory.queue(ALLOWANCE)
    assert (await restarted.collect(account_id))["quota"][0]["remaining"] == 25
    with pytest.raises(CollectionError):
        await restarted.status(account_id)  # in-memory flows never persisted
    path = manager.root / (account_id + ".enc")
    path.chmod(0o644)
    assert not manager.has_credentials(account_id)
    with pytest.raises(CollectionError):
        await manager.collect(account_id)
    path.chmod(0o600)
    hardlink = tmp_path / "hardlink"
    os.link(path, hardlink)
    assert not manager.has_credentials(account_id)
    with pytest.raises(CollectionError):
        await manager.collect(account_id)
    hardlink.unlink()
    path.unlink()
    outside = tmp_path / "outside"
    outside.write_text(SECRET)
    path.symlink_to(outside)
    assert not manager.has_credentials(account_id)
    with pytest.raises(CollectionError):
        await manager.collect(account_id)
    await manager.remove(account_id)
    assert outside.read_text() == SECRET
    for invalid in ("../outside", "A" * 32, "0" * 31, None):
        assert not manager.has_credentials(invalid)
        with pytest.raises(CollectionError):
            await manager.collect(invalid)


async def test_close_cancels_network_timers_and_blocks_new_operations(factory):
    manager, _, _ = factory()
    login = await manager.start("gemini", "Name")
    entered = asyncio.Event()

    async def blocked(request):
        entered.set()
        await asyncio.Event().wait()

    factory.queue(blocked)
    task = asyncio.create_task(manager.complete(login["id"], "4/code"))
    await asyncio.wait_for(entered.wait(), 1)
    await manager.close()
    assert task.cancelled() and not manager.active
    assert (await manager.status(login["id"]))["status"] == "error"
    with pytest.raises(CollectionError, match="closed"):
        await manager.start("gemini", "Name")
    await manager.close()


async def test_pkce_cross_flow_code_rejected_by_upstream_not_forged_locally(factory):
    manager, _, connected = factory()
    first = await manager.start("gemini", "One")
    second = await manager.start("gemini", "Two")
    challenge = parse_qs(urlsplit(first["authorization_url"]).query)["code_challenge"][
        0
    ]

    async def verify_pkce(request):
        verifier = parse_qs(request.content.decode())["code_verifier"][0]
        sent = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        # A code issued for the first flow MUST fail when the second flow's
        # verifier is presented. PKCE, not an invented callback state, binds it.
        assert sent != challenge
        return httpx.Response(400, json={"error": "invalid_grant"})

    factory.queue(verify_pkce)
    with pytest.raises(CollectionError, match="Google rejected"):
        await manager.complete(second["id"], "4/code-issued-for-first-flow")
    assert not connected and not manager.has_credentials(second["id"])
    assert (await manager.status(first["id"]))["status"] == "awaiting_authorization"


async def test_operation_timeout_cleans_up_without_credentials(factory, monkeypatch):
    manager, _, connected = factory()
    login = await manager.start("gemini", "Name")
    monkeypatch.setattr(subscriptions, "_OP_TIMEOUT", 0.01)

    async def blocked(request):
        await asyncio.Event().wait()

    factory.queue(blocked)
    with pytest.raises(CollectionError):
        await manager.complete(login["id"], "4/code")
    assert (
        not connected
        and not manager.active
        and not manager.has_credentials(login["id"])
    )
    assert (await manager.status(login["id"]))["status"] == "error"


async def test_network_concurrency_and_waiter_count_are_bounded(factory):
    manager, _, _ = factory()
    account_ids = [await connect(factory, manager) for _ in range(3)]
    entered = 0
    three_entered = asyncio.Event()

    async def blocked(request):
        nonlocal entered
        entered += 1
        if entered == 3:
            three_entered.set()
        await asyncio.Event().wait()

    factory.queue(*([blocked] * 3))
    tasks = [
        asyncio.create_task(manager.collect(account_ids[index % 3]))
        for index in range(16)
    ]
    await asyncio.wait_for(three_entered.wait(), 1)
    with pytest.raises(CollectionError):
        await manager.collect(account_ids[0])
    assert entered == 3 and len(manager.active) == 16
    await manager.close()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert not manager.active


async def test_invalid_identity_and_unauthorized_quota_never_register(factory):
    manager, _, connected = factory()
    first = await manager.start("gemini", "No identity")
    factory.queue(tokens(), {"email": EMAIL})
    with pytest.raises(CollectionError):
        await manager.complete(first["id"], "4/code")
    second = await manager.start("gemini", "Invalid access")
    factory.queue(tokens(), {"id": "synthetic-subject"}, PROJECT, httpx.Response(401))
    with pytest.raises(CollectionError):
        await manager.complete(second["id"], "4/code")
    assert (
        not connected
        and not manager.has_credentials(first["id"])
        and not manager.has_credentials(second["id"])
    )


@pytest.mark.parametrize(
    "provider,name",
    [
        ("chatgpt", "Name"),
        ("Gemini", "Name"),
        (None, "Name"),
        ("gemini", ""),
        ("gemini", " " * 2),
        ("gemini", "x" * 101),
        ("gemini", "bad\nname"),
        ("gemini", None),
    ],
)
async def test_provider_and_name_validation(factory, provider, name):
    manager, _, _ = factory()
    with pytest.raises(CollectionError):
        await manager.start(provider, name)
    assert not factory.requests
