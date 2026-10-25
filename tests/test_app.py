"""Owner-facing API contracts, using disposable state and no upstream requests."""

import asyncio
from copy import deepcopy
import csv
from datetime import datetime, timezone
import importlib
import io
import sqlite3

import httpx
import pytest
import pytest_asyncio

from collectors import CollectionError


ORIGIN = "http://atlas.test"
PASSWORD = "owner-pass12"  # Exactly the minimum accepted length.
METRICS = (
    "requests", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "total_tokens", "cost_usd",
)


class FakeCollector:
    """Only replace the documented async collector boundary, not app internals."""

    def __init__(self):
        self.results = {}
        self.failures = {}
        self.credentials = {}

    async def __call__(self, account):
        self.credentials[account["id"]] = account["credential"]
        if account["credential"] in self.failures:
            raise self.failures[account["credential"]]
        return deepcopy(self.results.get(account["credential"], {
            "records": [], "quota": [], "capabilities": [],
        }))


def record(model="model-one", *, date="2020-01-02", requests=2,
           input_tokens=100, output_tokens=20, cache_read_tokens=30,
           cache_write_tokens=5, cost_usd=0.25):
    return {
        "date": date, "model": model, "requests": requests,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens, "cost_usd": cost_usd,
    }


def result(*records):
    return {"records": list(records), "quota": [],
            "capabilities": ["tokens", "requests", "cost"]}


@pytest.fixture(scope="session")
def app_module(tmp_path_factory):
    # app.py creates its module-level app on import. Isolate that too, before
    # importing, rather than letting it open the owner's configured data files.
    root = tmp_path_factory.mktemp("module-app")
    with pytest.MonkeyPatch.context() as env:
        env.setenv("ATLAS_DATA_DIR", str(root))
        env.setenv("ATLAS_PASSWORD_FILE", str(root / "owner.password"))
        env.setenv("ATLAS_KEY_FILE", str(root / "encryption.key"))
        env.setenv("ATLAS_COOKIE_SECURE", "false")
        env.delenv("ATLAS_PUBLIC_ORIGIN", raising=False)
        yield importlib.import_module("app")


@pytest_asyncio.fixture
async def api(tmp_path, monkeypatch, app_module):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ATLAS_PASSWORD_FILE", str(tmp_path / "owner.password"))
    monkeypatch.setenv("ATLAS_KEY_FILE", str(tmp_path / "encryption.key"))
    monkeypatch.setenv("ATLAS_COOKIE_SECURE", "false")
    monkeypatch.setenv("ATLAS_GEMINI_CLIENT_ID", "synthetic-client.apps.googleusercontent.com")
    monkeypatch.setenv("ATLAS_GEMINI_CLIENT_SECRET", "synthetic-client-secret")
    monkeypatch.delenv("ATLAS_PUBLIC_ORIGIN", raising=False)
    fake = FakeCollector()
    application = app_module.create_app(tmp_path, collector=fake, poll_seconds=0)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url=ORIGIN,
            headers={"Origin": ORIGIN},
        ) as client:
            yield client, fake, tmp_path


@pytest_asyncio.fixture
async def owner(api):
    client, fake, root = api
    response = await client.post("/api/setup", json={
        "token": (root / "setup.token").read_text().strip(),
        "password": PASSWORD,
    })
    assert response.status_code == 200, response.text
    return client, fake, root


async def dashboard(client, **filters):
    response = await client.get("/api/dashboard", params={"range": "all", **filters})
    assert response.status_code == 200, response.text
    return response.json()


async def settled(client, **filters):
    # HTTPX does not run ASGI lifespan itself. The fixture does; polling the
    # public dashboard also waits for create/edit-triggered background work.
    async with asyncio.timeout(5):
        while True:
            data = await dashboard(client, **filters)
            if not data["refreshing"]:
                return data
            await asyncio.sleep(0.01)


async def add_account(client, *, name="Personal", provider="openai",
                      credential="dummy-personal-key", **fields):
    response = await client.post("/api/accounts", json={
        "name": name, "provider": provider, "base_url": "",
        "credential": credential, **fields,
    })
    assert response.status_code == 200, response.text
    account = response.json()
    await settled(client)
    return account


async def refresh(client, account_id=None):
    response = await client.post("/api/refresh", json=(
        {"account_id": account_id} if account_id else {}
    ))
    assert response.status_code == 200, response.text
    assert response.json() == {"started": True}
    return await settled(client)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [
    ("GET", "/api/dashboard"), ("GET", "/api/providers"),
    ("GET", "/api/export"), ("POST", "/api/accounts"),
    ("POST", "/api/refresh"), ("POST", "/api/logout"),
    ("PATCH", "/api/accounts/not-an-account"),
    ("DELETE", "/api/accounts/not-an-account"),
])
async def test_owner_routes_require_authentication(api, method, path):
    client, _, _ = api
    response = await client.request(method, path)
    assert response.status_code == 401
    assert (await client.get("/api/session")).json()["authenticated"] is False


@pytest.mark.asyncio
async def test_one_time_setup_password_cookie_login_and_logout(api):
    client, _, root = api
    session_response = await client.get("/api/session")
    assert set(session_response.headers["cache-control"].split(", ")) == {"no-store", "no-transform"}
    assert "script-src 'self'" in session_response.headers["content-security-policy"]
    session = session_response.json()
    assert session == {"authenticated": False, "setup_required": True}
    token = (root / "setup.token").read_text().strip()
    response = await client.post("/api/setup", json={"token": "wrong", "password": PASSWORD})
    assert response.status_code == 403
    response = await client.post("/api/setup", json={"token": token, "password": "x" * 11})
    assert response.status_code == 400
    response = await client.post("/api/setup", json={"token": token, "password": PASSWORD})
    assert response.status_code == 200, response.text
    assert response.json()["authenticated"] is True
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.cookies.get("atlas_session")
    assert (await client.get("/api/session")).json() == {
        "authenticated": True, "setup_required": False,
    }
    response = await client.post("/api/setup", json={"token": token, "password": "replacement-password"})
    assert response.status_code == 403
    old_cookie = client.cookies.get("atlas_session")
    assert (await client.post("/api/logout")).status_code == 200
    assert (await client.get("/api/session")).json()["authenticated"] is False
    assert (await client.get("/api/dashboard", headers={
        "Cookie": f"atlas_session={old_cookie}",
    })).status_code == 401
    assert (await client.post("/api/login", json={"password": "incorrect"})).status_code == 401
    assert (await client.post("/api/login", json={"password": PASSWORD})).status_code == 200
    assert (await client.get("/api/dashboard")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [None, "https://atlas.test", "http://atlas.test.evil"])
@pytest.mark.parametrize("method,path", [
    ("POST", "/api/accounts"), ("POST", "/api/refresh"),
    ("POST", "/api/logout"), ("PATCH", "/api/accounts/missing"),
    ("DELETE", "/api/accounts/missing"),
])
async def test_mutations_reject_missing_or_foreign_origin(owner, origin, method, path):
    client, _, _ = owner
    request = client.build_request(method, path, json={})
    request.headers.pop("origin", None)
    if origin is not None:
        request.headers["Origin"] = origin
    response = await client.send(request)
    assert response.status_code == 403
    assert (await client.get("/api/session")).json()["authenticated"] is True
    assert (await dashboard(client))["accounts"] == []


@pytest.mark.asyncio
async def test_multiple_accounts_models_cache_tokens_and_filters(owner):
    client, fake, _ = owner
    # Collector contract: input_tokens is uncached input. Cache reads/writes
    # stay separately visible and contribute to the total.
    fake.results["key-a"] = result(record("shared"), record(
        "other", requests=3, input_tokens=37, output_tokens=11,
        cache_read_tokens=13, cache_write_tokens=17, cost_usd=0.5,
    ))
    fake.results["key-b"] = result(record(
        "shared", requests=9, input_tokens=1000, output_tokens=200,
        cache_read_tokens=300, cache_write_tokens=50, cost_usd=2.0,
    ))
    first = await add_account(client, name="Work", credential="key-a")
    second = await add_account(client, name="Home", provider="gemini", credential="key-b")
    data = await settled(client)
    assert data["timezone"] == "Asia/Seoul"
    assert {key: data["totals"][key] for key in METRICS} == {
        "requests": 14, "input_tokens": 1137, "output_tokens": 231,
        "cache_read_tokens": 343, "cache_write_tokens": 72,
        "total_tokens": 1783, "cost_usd": 2.75,
    }
    assert len(data["daily"]) == 1
    assert {key: data["daily"][0][key] for key in METRICS} == {
        key: data["totals"][key] for key in METRICS
    }
    models = {(row["account_id"], row["model"]): row for row in data["models"]}
    assert len(models) == 3
    assert models[first["id"], "shared"]["input_tokens"] == 100
    assert models[second["id"], "shared"]["input_tokens"] == 1000
    assert models[first["id"], "other"]["input_tokens"] == 37
    assert models[first["id"], "shared"]["account_name"] == "Work"
    assert models[second["id"], "shared"]["provider"] == "gemini"
    assert all(account["status"] == "connected" for account in data["accounts"])
    assert data["refreshed_at"]
    filtered = await dashboard(client, account=first["id"])
    assert filtered["totals"]["input_tokens"] == 137
    assert {row["account_id"] for row in filtered["models"]} == {first["id"]}
    assert (await dashboard(client, provider="gemini"))["totals"]["input_tokens"] == 1000
    assert (await dashboard(client, account=first["id"], provider="gemini"))["models"] == []


@pytest.mark.asyncio
async def test_total_tokens_includes_exclusive_cache_metrics(owner):
    client, fake, _ = owner
    fake.results["dummy-personal-key"] = result(record())
    await add_account(client)
    data = await settled(client)
    for aggregate in (data["totals"], data["daily"][0], data["models"][0]):
        assert aggregate["cache_read_tokens"] == 30
        assert aggregate["cache_write_tokens"] == 5
        assert aggregate["total_tokens"] == 155


@pytest.mark.asyncio
async def test_repeated_refresh_replaces_instead_of_accumulating(owner):
    client, fake, _ = owner
    fake.results["dummy-personal-key"] = result(record())
    account = await add_account(client)
    before = await settled(client)
    assert (await refresh(client, account["id"]))["totals"] == before["totals"]
    fake.results["dummy-personal-key"] = result(record(
        requests=5, input_tokens=200, output_tokens=40,
        cache_read_tokens=60, cache_write_tokens=10, cost_usd=0.5,
    ))
    data = await refresh(client, account["id"])
    assert data["totals"]["requests"] == 5
    assert data["totals"]["input_tokens"] == 200
    assert data["totals"]["output_tokens"] == 40
    assert data["totals"]["cache_read_tokens"] == 60
    assert data["totals"]["cache_write_tokens"] == 10
    assert data["totals"]["cost_usd"] == 0.5
    assert len(data["models"]) == len(data["daily"]) == 1
    assert (await refresh(client))["totals"] == data["totals"]


@pytest.mark.asyncio
async def test_unknown_metrics_are_null_not_invented_zero(owner):
    client, fake, _ = owner
    empty = await dashboard(client)
    assert all(empty["totals"][key] is None for key in METRICS)
    fake.results["dummy-personal-key"] = result(record(
        requests=0, input_tokens=None, output_tokens=None,
        cache_read_tokens=None, cache_write_tokens=None, cost_usd=None,
    ))
    await add_account(client)
    data = await settled(client)
    for aggregate in (data["totals"], data["daily"][0], data["models"][0]):
        assert aggregate["requests"] == 0
        assert all(aggregate[key] is None for key in METRICS if key != "requests")


@pytest.mark.asyncio
async def test_transient_failure_preserves_data_and_last_success(owner, app_module):
    client, fake, _ = owner
    fake.results["dummy-personal-key"] = result(record())
    account = await add_account(client)
    before = await settled(client)
    fake.failures["dummy-personal-key"] = app_module.collectors.CollectionError("Temporary upstream outage")
    after = await refresh(client, account["id"])
    assert after["totals"] == before["totals"]
    assert after["models"] == before["models"]
    assert after["daily"] == before["daily"]
    assert after["refreshed_at"] == before["refreshed_at"]
    assert after["accounts"][0]["last_success_at"] == before["accounts"][0]["last_success_at"]
    assert after["accounts"][0]["status"] == "error"
    assert after["accounts"][0]["error"]
    del fake.failures["dummy-personal-key"]
    recovered = await refresh(client, account["id"])
    assert recovered["accounts"][0]["status"] == "connected"
    assert recovered["accounts"][0]["error"] is None


@pytest.mark.asyncio
async def test_blank_credential_edit_keeps_secret_without_json_disclosure(owner):
    client, fake, _ = owner
    secret = "dummy-distinctive-secret-never-send-to-browser"
    private_option = "dummy-private-extra-value"
    fake.results[secret] = result(record())
    account = await add_account(client, credential=secret, extra={"token": private_option})
    fake.credentials.clear()
    response = await client.patch(f"/api/accounts/{account['id']}", json={
        "name": "Renamed", "credential": "   ",
    })
    assert response.status_code == 200, response.text
    data = await settled(client)
    assert fake.credentials[account["id"]] == secret
    assert data["totals"]["input_tokens"] == 100
    assert data["accounts"][0]["name"] == "Renamed"
    assert data["accounts"][0]["has_credentials"] is True
    for public in (account, response.json(), data):
        serialized = str(public)
        assert secret not in serialized and private_option not in serialized
    for public in (account, response.json(), data["accounts"][0]):
        assert not ({"credential", "secret", "extra"} & public.keys())


@pytest.mark.asyncio
async def test_disabled_account_is_not_collected_even_on_global_refresh(owner):
    client, fake, _ = owner
    fake.results["disabled-key"] = result(record(input_tokens=999))
    account = await add_account(client, credential="disabled-key", enabled=False)
    data = await refresh(client)
    assert account["id"] not in fake.credentials
    assert data["accounts"][0]["enabled"] is False
    assert data["accounts"][0]["last_attempt_at"] is None
    assert data["models"] == []
    response = await client.post("/api/refresh", json={"account_id": account["id"]})
    assert response.status_code == 404
    assert account["id"] not in fake.credentials


@pytest.mark.asyncio
async def test_deletion_removes_account_and_its_collected_statistics(owner):
    client, fake, _ = owner
    fake.results["key-a"] = result(record())
    fake.results["key-b"] = result(record(input_tokens=40))
    first = await add_account(client, credential="key-a")
    second = await add_account(client, name="Keep", credential="key-b")
    response = await client.delete(f"/api/accounts/{first['id']}")
    assert response.status_code == 200 and response.json() == {"deleted": True}
    data = await settled(client)
    assert [account["id"] for account in data["accounts"]] == [second["id"]]
    assert data["totals"]["input_tokens"] == 40
    assert data["totals"]["output_tokens"] == 20
    assert {row["account_id"] for row in data["models"]} == {second["id"]}
    assert (await dashboard(client, account=first["id"]))["models"] == []
    assert (await client.post("/api/refresh", json={"account_id": first["id"]})).status_code == 404
    exported = await client.get("/api/export", params={"range": "all", "account": first["id"]})
    assert len(list(csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff"))))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("period,start,expected", [
    ("today", "2026-01-02", 1),
    ("7d", "2025-12-27", 11),
    ("30d", "2025-12-04", 1111),
    ("all", "0001-01-01", 11111),
])
async def test_periods_use_korea_date_and_inclusive_boundaries(
    owner, app_module, monkeypatch, period, start, expected,
):
    class KoreaBoundaryClock(datetime):
        @classmethod
        def now(cls, tz=None):
            # Jan 1 in UTC, already Jan 2 in Seoul.
            moment = datetime(2026, 1, 1, 15, 30, tzinfo=timezone.utc)
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr(app_module, "datetime", KoreaBoundaryClock)
    client, fake, _ = owner
    fake.results["dummy-personal-key"] = result(*[
        record(date=date, requests=requests) for date, requests in [
            ("2025-12-03", 10000), ("2025-12-04", 1000),
            ("2025-12-26", 100), ("2025-12-27", 10),
            ("2026-01-02", 1), ("2026-01-03", 100000),
        ]
    ])
    await add_account(client)
    data = await dashboard(client, range=period)
    assert data["range"] == period
    assert data["from"] == start and data["to"] == "2026-01-02"
    assert data["totals"]["requests"] == expected
    assert all(start <= row["date"] <= "2026-01-02" for row in data["daily"])
    response = await client.get("/api/export", params={"range": period})
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert sum(int(row["requests"]) for row in rows) == expected
    assert all(start <= row["date"] <= "2026-01-02" for row in rows)


@pytest.mark.asyncio
async def test_csv_neutralizes_formulas_and_applies_account_provider_filters(owner):
    client, fake, _ = owner
    models = ["=SUM(1,2)", "+formula", "-formula", "@formula", "  =formula", "normal,quoted\"model"]
    fake.results["export-key"] = result(*[record(model) for model in models])
    fake.results["other-key"] = result(record("must-not-appear"))
    account = await add_account(client, name="=UNTRUSTED()", credential="export-key")
    await add_account(client, name="Other", provider="gemini", credential="other-key")
    response = await client.get("/api/export", params={
        "range": "all", "account": account["id"], "provider": "openai",
    })
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert 'filename="use-usage.csv"' in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert len(rows) == len(models)
    assert {row["model"] for row in rows} == {
        "'=SUM(1,2)", "'+formula", "'-formula", "'@formula",
        "'  =formula", "normal,quoted\"model",
    }
    assert {row["account_name"] for row in rows} == {"'=UNTRUSTED()"}
    assert {row["provider"] for row in rows} == {"openai"}
    assert all(row["cache_read_tokens"] == "30" and row["cache_write_tokens"] == "5" for row in rows)
    response = await client.get("/api/export", params={"range": "all", "provider": "gemini"})
    rows = list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert [row["model"] for row in rows] == ["must-not-appear"]
    response = await client.get("/api/export", params={
        "range": "all", "account": account["id"], "provider": "gemini",
    })
    assert list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff")))) == []


@pytest.mark.asyncio
async def test_provider_catalog_replaces_amp_with_codex_lb_and_rejects_amp_creation(owner):
    client, _, root = owner
    response = await client.get("/api/providers")
    assert response.status_code == 200
    providers = {provider["id"] for provider in response.json()["providers"]}
    assert "codex-lb" in providers and "amp" not in providers
    response = await client.post("/api/accounts", json={
        "name": "Obsolete Amp", "provider": "amp", "base_url": "https://ampcode.com",
        "credential": "dummy-obsolete-key",
    })
    assert response.status_code == 400
    assert (await dashboard(client))["accounts"] == []
    assert not (root / "ingest.token").exists()
    assert not (root / "chatgpt-agent.token").exists()


@pytest.mark.asyncio
async def test_legacy_amp_rows_remain_on_disk_but_are_hidden_from_dashboard_export_and_refresh(owner):
    client, fake, root = owner
    fake.results["current-key"] = result(record("Current API"))
    current = await add_account(client, provider="usage-json", credential="current-key")
    before = await settled(client)
    # Seed a previous installation directly: the removed ingestion interface
    # must not be needed to verify retention or accidentally grant visibility.
    legacy_ids = ["amp-local", "legacy-workspace"]
    with sqlite3.connect(root / "atlas.sqlite") as connection:
        for account_id in legacy_ids:
            connection.execute(
                """INSERT INTO accounts(id,name,provider,base_url,secret,status,last_success_at)
                SELECT ?, 'Old Amp', 'amp', 'https://ampcode.com', secret,
                       'connected', '2099-01-01T00:00:00Z'
                FROM accounts WHERE id=?""", (account_id, current["id"]),
            )
            connection.execute(
                """INSERT INTO records(account_id,date,model,requests,input_tokens,cost_usd)
                VALUES (?, '2020-01-02', 'Hidden Amp model', 9999, 999999, 999)""",
                (account_id,),
            )
    fake.credentials.clear()
    after = await refresh(client)
    for key in ("totals", "daily", "models"):
        assert after[key] == before[key]
    assert [account["id"] for account in after["accounts"]] == [current["id"]]
    assert not (set(legacy_ids) & fake.credentials.keys())
    assert after.get("sources", []) == []
    for filters in ({"provider": "amp"}, *({"account": account_id} for account_id in legacy_ids)):
        hidden = await dashboard(client, **filters)
        assert hidden["models"] == hidden["daily"] == []
        assert hidden.get("sources", []) == []
        assert all(hidden["totals"][metric] is None for metric in METRICS)
    for filters in ({}, {"provider": "amp"}, *({"account": account_id} for account_id in legacy_ids)):
        response = await client.get("/api/export", params={"range": "all", **filters})
        assert response.status_code == 200
        rows = list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))
        assert [row["model"] for row in rows] == ([] if filters else ["Current API"])
    with sqlite3.connect(root / "atlas.sqlite") as connection:
        assert connection.execute("SELECT count(*) FROM accounts WHERE provider='amp'").fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM records WHERE model='Hidden Amp model'",
        ).fetchone()[0] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["claude-lb", "codex-lb", "gemini", "usage-json"])
async def test_lifetime_summary_and_quota_do_not_become_daily_usage(owner, provider):
    client, fake, _ = owner
    fake.results["daily-key"] = result(record("Dated model"))
    await add_account(client, provider="usage-json", credential="daily-key")
    before = await settled(client)
    fake.results["lifetime-key"] = {
        "records": [], "capabilities": ["tokens", "quota"],
        "summary": {"period": "lifetime", "total_tokens": 654321, "requests": 42},
        "quota": [{"label": "week", "used_percent": 70, "remaining": 30}],
    }
    account = await add_account(client, provider=provider, credential="lifetime-key")
    data = await settled(client)
    public = next(row for row in data["accounts"] if row["id"] == account["id"])
    assert public["summary"] == fake.results["lifetime-key"]["summary"]
    assert public["quota"] == fake.results["lifetime-key"]["quota"]
    assert public["status"] == "connected"
    for key in ("totals", "daily", "models"):
        assert data[key] == before[key]
    filtered = await dashboard(client, account=account["id"], range="today")
    assert filtered["daily"] == filtered["models"] == []
    assert all(filtered["totals"][metric] is None for metric in METRICS)
    assert next(row for row in filtered["accounts"] if row["id"] == account["id"])["summary"] == public["summary"]
    response = await client.get("/api/export", params={"range": "all", "account": account["id"]})
    assert response.status_code == 200
    assert list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff")))) == []


class FakeChatGPTManager:
    """Async module boundary; completion simulates the provider's login event."""

    def __init__(self, root, cipher, on_connected):
        self.on_connected = on_connected
        self.logins = {}
        self.names = {}
        self.credentials = set()
        self.results = {}
        self.start_error = None
        self.complete_error = None
        self.completions = []
        self.remove_failures = set()
        self.closed = False

    async def start(self, name):
        if self.start_error:
            raise CollectionError(self.start_error)
        login_id = f"{len(self.logins) + 1:032x}"
        self.names[login_id] = name
        self.logins[login_id] = {
            "id": login_id, "status": "awaiting_authorization",
            "authorization_url": "https://auth.openai.com/oauth/authorize?state=dummy-state",
            "callback_url": "http://localhost:1455/auth/callback",
            "expires_at": "2099-01-01T00:00:00Z",
            "error": None,
        }
        return await self.status(login_id)

    async def status(self, login_id):
        if login_id not in self.logins:
            raise CollectionError("Unknown ChatGPT account or login.")
        return deepcopy(self.logins[login_id])

    async def cancel(self, login_id):
        await self.status(login_id)
        if self.logins[login_id]["status"] == "awaiting_authorization":
            self.logins[login_id].update(
                status="error", error="ChatGPT login was cancelled.",
                authorization_url=None,
            )

    def has_credentials(self, account_id):
        return account_id in self.credentials

    async def complete(self, login_id, callback_url):
        self.completions.append((login_id, callback_url))
        if self.complete_error:
            raise CollectionError(self.complete_error)
        await self.status(login_id)
        if self.logins[login_id]["status"] != "awaiting_authorization":
            raise CollectionError("ChatGPT login is no longer awaiting authorization.")
        self.credentials.add(login_id)
        await self.on_connected(login_id, self.names[login_id])
        self.logins[login_id].update(
            status="connected", authorization_url=None,
        )
        return await self.status(login_id)

    async def collect(self, account_id):
        if not self.has_credentials(account_id):
            raise CollectionError("Connect ChatGPT using browser login first.")
        return deepcopy(self.results.get(account_id, {
            "records": [], "capabilities": ["tokens", "quota"],
            "summary": {"period": "lifetime", "total_tokens": 98765,
                        "input_tokens": None, "output_tokens": None,
                        "cache_read_tokens": None, "cache_write_tokens": None,
                        "requests": None, "cost_usd": None},
            "quota": [
                {"label": "5h", "unit": "percent", "limit": None,
                 "remaining": 75, "used_percent": 25, "resets_at": None},
                {"label": "7d", "unit": "percent", "limit": None,
                 "remaining": 40, "used_percent": 60, "resets_at": None},
            ],
            "note": "Codex subscription activity, not all ChatGPT web conversations.",
        }))

    async def remove(self, account_id):
        if account_id in self.remove_failures:
            raise CollectionError("ChatGPT credential cleanup failed. Please retry.")
        self.credentials.discard(account_id)

    async def close(self):
        self.closed = True


@pytest_asyncio.fixture
async def chatgpt_api(tmp_path, monkeypatch, app_module):
    # Keep the existing API fixtures unmodified: only ChatGPT scenarios replace
    # the real manager, before create_app captures its callback and instance.
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ATLAS_PASSWORD_FILE", str(tmp_path / "owner.password"))
    monkeypatch.setenv("ATLAS_KEY_FILE", str(tmp_path / "encryption.key"))
    monkeypatch.setenv("ATLAS_COOKIE_SECURE", "false")
    monkeypatch.delenv("ATLAS_PUBLIC_ORIGIN", raising=False)
    managers = []

    def manager_factory(root, cipher, on_connected):
        manager = FakeChatGPTManager(root, cipher, on_connected)
        managers.append(manager)
        return manager

    monkeypatch.setattr(app_module.chatgpt, "ChatGPTManager", manager_factory)
    fake = FakeCollector()
    application = app_module.create_app(tmp_path, collector=fake, poll_seconds=0)
    manager, = managers
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url=ORIGIN,
            headers={"Origin": ORIGIN},
        ) as client:
            yield client, manager, fake, tmp_path
    assert manager.closed is True


@pytest_asyncio.fixture
async def chatgpt_owner(chatgpt_api):
    client, _, _, root = chatgpt_api
    response = await client.post("/api/setup", json={
        "token": (root / "setup.token").read_text().strip(), "password": PASSWORD,
    })
    assert response.status_code == 200, response.text
    return chatgpt_api


async def start_chatgpt(client, name="Personal ChatGPT"):
    response = await client.post("/api/chatgpt/login", json={"name": name})
    assert response.status_code == 200, response.text
    login = response.json()
    assert login["status"] == "awaiting_authorization"
    assert login["authorization_url"].startswith("https://auth.openai.com/oauth/authorize?")
    assert login["callback_url"] == "http://localhost:1455/auth/callback"
    assert login["expires_at"]
    return login


async def complete_chatgpt(client, login_id):
    response = await client.post(f"/api/chatgpt/login/{login_id}/complete", json={
        "callback_url": "http://localhost:1455/auth/callback?code=dummy-code&state=dummy-state",
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "connected"
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [
    ("POST", "/api/chatgpt/login"),
    ("GET", "/api/chatgpt/login/missing"),
    ("DELETE", "/api/chatgpt/login/missing"),
    ("POST", "/api/chatgpt/login/missing/complete"),
])
async def test_chatgpt_login_routes_require_owner_session(chatgpt_api, method, path):
    client, _, _, _ = chatgpt_api
    response = await client.request(method, path, json={})
    assert response.status_code == 401
    assert (await client.get("/api/session")).json()["authenticated"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["start", "cancel", "complete"])
@pytest.mark.parametrize("origin", [None, "https://atlas.test", "http://atlas.test.evil"])
async def test_chatgpt_login_mutations_require_same_origin(chatgpt_owner, action, origin):
    client, manager, _, _ = chatgpt_owner
    login = await start_chatgpt(client)
    method, path, payload = {
        "start": ("POST", "/api/chatgpt/login", {"name": "Untrusted"}),
        "cancel": ("DELETE", f"/api/chatgpt/login/{login['id']}", {}),
        "complete": ("POST", f"/api/chatgpt/login/{login['id']}/complete", {
            "callback_url": "http://localhost:1455/auth/callback?code=dummy&state=dummy",
        }),
    }[action]
    request = client.build_request(method, path, json=payload)
    request.headers.pop("Origin", None)
    if origin is not None:
        request.headers["Origin"] = origin
    assert (await client.send(request)).status_code == 403
    status = await client.get(f"/api/chatgpt/login/{login['id']}")
    assert status.status_code == 200 and status.json()["status"] == "awaiting_authorization"
    assert manager.completions == []
    assert (await dashboard(client))["accounts"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {}, {"credential": "dummy-injected-key"},
    {"extra": {"auth_json": "dummy-access-token"}},
])
async def test_chatgpt_accounts_cannot_be_created_by_credential_injection(chatgpt_owner, fields):
    client, _, _, _ = chatgpt_owner
    response = await client.post("/api/accounts", json={
        "name": "Injected", "provider": "chatgpt", "base_url": "https://chatgpt.com",
        **fields,
    })
    assert response.status_code == 400
    assert "ChatGPT" in response.json()["detail"] and "로그인" in response.json()["detail"]
    assert "dummy-" not in response.text
    assert (await dashboard(client))["accounts"] == []


@pytest.mark.asyncio
async def test_missing_claude_key_is_needs_auth_not_connected_or_collection_error(owner):
    client, fake, _ = owner
    account = await add_account(client, provider="claude-lb", credential="")
    data = await settled(client)
    public = data["accounts"][0]
    assert public["id"] == account["id"]
    assert public["status"] == "needs_auth"
    assert public["has_credentials"] is False
    assert public["last_success_at"] is None
    assert public["error"] and public["quota"] == []
    assert account["id"] not in fake.credentials
    assert data["models"] == data["daily"] == []
    assert all(data["totals"][key] is None for key in METRICS)
    again = await refresh(client, account["id"])
    assert again["accounts"][0]["status"] == "needs_auth"
    assert account["id"] not in fake.credentials


@pytest.mark.asyncio
async def test_chatgpt_pending_callback_connection_keeps_lifetime_and_quotas_separate(chatgpt_owner):
    client, manager, fake, _ = chatgpt_owner
    fake.results["daily-key"] = result(record("API model"))
    daily_account = await add_account(client, provider="usage-json", credential="daily-key")
    before = await settled(client)
    providers = await client.get("/api/providers")
    assert providers.status_code == 200
    assert "chatgpt" in {p["id"] for p in providers.json()["providers"]}
    login = await start_chatgpt(client, "  Personal ChatGPT  ")
    pending = await client.get(f"/api/chatgpt/login/{login['id']}")
    assert pending.status_code == 200 and pending.json() == login
    assert {a["id"] for a in (await dashboard(client))["accounts"]} == {daily_account["id"]}
    assert manager.has_credentials(login["id"]) is False

    completed = await complete_chatgpt(client, login["id"])
    assert manager.completions == [(
        login["id"], "http://localhost:1455/auth/callback?code=dummy-code&state=dummy-state",
    )]
    connected = await client.get(f"/api/chatgpt/login/{login['id']}")
    assert connected.status_code == 200 and connected.json()["status"] == "connected"
    assert connected.json() == completed
    assert connected.json()["authorization_url"] is None
    data = await settled(client)
    account = next(a for a in data["accounts"] if a["id"] == login["id"])
    assert account["name"] == "Personal ChatGPT" and account["provider"] == "chatgpt"
    assert account["status"] == "connected" and account["error"] is None
    assert account["has_credentials"] is True and account["last_success_at"]
    assert account["summary"]["period"] == "lifetime"
    assert account["summary"]["total_tokens"] == 98765
    assert account["summary"]["input_tokens"] is None
    assert [(q["label"], q["used_percent"]) for q in account["quota"]] == [("5h", 25), ("7d", 60)]
    assert "quota" in account["capabilities"] and "ChatGPT" in account["note"]
    assert not ({"credential", "secret", "extra"} & account.keys())
    assert login["id"] not in fake.credentials  # Must dispatch to ChatGPT's collector.
    for key in ("totals", "daily", "models"):
        assert data[key] == before[key]
    chatgpt_only = await dashboard(client, provider="chatgpt")
    assert chatgpt_only["daily"] == chatgpt_only["models"] == []
    assert chatgpt_only.get("sources", []) == []
    assert all(chatgpt_only["totals"][key] is None for key in METRICS)
    assert (await dashboard(client, range="today"))["accounts"] == data["accounts"]
    repeated = await refresh(client, login["id"])
    assert repeated["totals"] == before["totals"]
    export = await client.get("/api/export", params={"range": "all", "provider": "chatgpt"})
    assert export.status_code == 200
    assert list(csv.DictReader(io.StringIO(export.text.lstrip("\ufeff")))) == []


@pytest.mark.asyncio
async def test_multiple_chatgpt_logins_edit_and_delete_keep_credentials_isolated(chatgpt_owner):
    client, manager, _, _ = chatgpt_owner
    first = await start_chatgpt(client, "Same display name")
    second = await start_chatgpt(client, "Same display name")
    assert first["id"] != second["id"]
    manager.results[second["id"]] = {
        "records": [], "capabilities": ["tokens", "quota"],
        "summary": {"period": "lifetime", "total_tokens": 4321},
        "quota": [{"label": "5h", "used_percent": 80, "remaining": 20}],
    }
    await complete_chatgpt(client, second["id"])
    await complete_chatgpt(client, first["id"])
    data = await settled(client)
    assert {a["id"] for a in data["accounts"]} == {first["id"], second["id"]}
    assert all(a["status"] == "connected" and a["has_credentials"] for a in data["accounts"])
    accounts = {a["id"]: a for a in data["accounts"]}
    assert accounts[first["id"]]["summary"]["total_tokens"] == 98765
    assert accounts[second["id"]]["summary"]["total_tokens"] == 4321
    assert accounts[first["id"]]["quota"][0]["used_percent"] == 25
    assert accounts[second["id"]]["quota"][0]["used_percent"] == 80
    for fields in (
        {"credential": "dummy-injected-key"}, {"extra": {"auth": "dummy-secret"}},
        {"base_url": "https://evil.example"}, {"provider": "chatgpt"},
        {"provider": "openai"}, {"unexpected": True},
    ):
        response = await client.patch(f"/api/accounts/{first['id']}", json=fields)
        assert response.status_code == 400, response.text
    response = await client.patch(f"/api/accounts/{first['id']}", json={
        "name": "Renamed", "enabled": False,
    })
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Renamed" and response.json()["enabled"] is False
    assert response.json()["has_credentials"] is True
    await settled(client)
    assert (await client.post("/api/refresh", json={"account_id": first["id"]})).status_code == 404
    response = await client.patch(f"/api/accounts/{first['id']}", json={"enabled": True})
    assert response.status_code == 200, response.text
    await settled(client)
    assert manager.has_credentials(first["id"]) and manager.has_credentials(second["id"])
    response = await client.delete(f"/api/accounts/{first['id']}")
    assert response.status_code == 200 and response.json() == {"deleted": True}
    assert manager.has_credentials(first["id"]) is False
    assert manager.has_credentials(second["id"]) is True
    data = await refresh(client, second["id"])
    assert [a["id"] for a in data["accounts"]] == [second["id"]]
    assert data["accounts"][0]["has_credentials"] is True
    assert data["accounts"][0]["name"] == "Same display name"
    assert data["accounts"][0]["summary"]["total_tokens"] == 4321
    assert data["accounts"][0]["quota"][0]["used_percent"] == 80
    assert (await client.delete(f"/api/accounts/{first['id']}")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["{broken", "null", "[]", '"name"'])
async def test_chatgpt_login_requires_a_json_object(chatgpt_owner, payload):
    client, _, _, _ = chatgpt_owner
    response = await client.post("/api/chatgpt/login", content=payload, headers={
        "Content-Type": "application/json",
    })
    assert response.status_code == 400
    assert (await dashboard(client))["accounts"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [None, False, 1, [], {}, "", "   ", "x" * 101])
async def test_chatgpt_login_rejects_invalid_names(chatgpt_owner, name):
    client, _, _, _ = chatgpt_owner
    response = await client.post("/api/chatgpt/login", json={"name": name})
    assert response.status_code == 400
    assert (await dashboard(client))["accounts"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["x", "x" * 100])
async def test_chatgpt_login_accepts_name_length_boundaries(chatgpt_owner, name):
    client, _, _, _ = chatgpt_owner
    login = await start_chatgpt(client, name)
    await complete_chatgpt(client, login["id"])
    assert (await settled(client))["accounts"][0]["name"] == name


@pytest.mark.asyncio
async def test_chatgpt_login_default_name_and_cancel_are_public_http_contracts(chatgpt_owner):
    client, manager, _, _ = chatgpt_owner
    response = await client.post("/api/chatgpt/login", json={})
    assert response.status_code == 200 and response.json()["status"] == "awaiting_authorization"
    login_id = response.json()["id"]
    other = await start_chatgpt(client, "Keep awaiting authorization")
    response = await client.delete(f"/api/chatgpt/login/{login_id}")
    assert response.status_code == 200 and response.json() == {"cancelled": True}
    status = await client.get(f"/api/chatgpt/login/{login_id}")
    assert status.status_code == 200 and status.json()["status"] != "awaiting_authorization"
    assert status.json()["authorization_url"] is None
    assert manager.has_credentials(login_id) is False
    assert (await dashboard(client))["accounts"] == []
    assert (await client.get(f"/api/chatgpt/login/{other['id']}")).json()["status"] == "awaiting_authorization"
    assert (await client.delete(f"/api/chatgpt/login/{login_id}")).status_code == 200
    fresh = await client.post("/api/chatgpt/login", json={})
    assert fresh.status_code == 200
    await complete_chatgpt(client, fresh.json()["id"])
    assert (await settled(client))["accounts"][0]["name"] == "ChatGPT 계정"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "DELETE"])
@pytest.mark.parametrize("login_id", ["not-an-id", "f" * 32, "x" * 200])
async def test_unknown_or_invalid_chatgpt_login_ids_return_404(chatgpt_owner, method, login_id):
    client, _, _, _ = chatgpt_owner
    response = await client.request(method, f"/api/chatgpt/login/{login_id}")
    assert response.status_code == 404
    assert response.json()["detail"]


@pytest.mark.asyncio
async def test_chatgpt_login_safe_provider_error_is_http_400(chatgpt_owner):
    client, manager, _, _ = chatgpt_owner
    manager.start_error = "At most three ChatGPT logins may be pending."
    response = await client.post("/api/chatgpt/login", json={"name": "Personal"})
    assert response.status_code == 400
    assert response.json() == {"detail": manager.start_error}
    assert (await dashboard(client))["accounts"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [
    ("POST", "/api/ingest/amp"),
    ("GET", "/api/collector/jobs"),
    ("GET", "/api/chatgpt/agent/jobs"),
    ("POST", "/api/chatgpt/agent/results"),
])
@pytest.mark.parametrize("token_file", ["ingest.token", "chatgpt-agent.token"])
@pytest.mark.parametrize("with_owner_cookie", [False, True])
async def test_removed_machine_routes_stay_unavailable_even_with_legacy_bearer(
    owner, method, path, token_file, with_owner_cookie,
):
    client, _, root = owner
    # Simulate tokens left by an older installation, not a newly issued API.
    legacy_token = "dummy-legacy-collector-token"
    (root / token_file).write_text(legacy_token)
    request = client.build_request(method, path, json={})
    if not with_owner_cookie:
        request.headers.pop("Cookie", None)
    request.headers["Authorization"] = "Bearer " + legacy_token
    response = await client.send(request)
    assert response.status_code == (404 if with_owner_cookie else 401), response.text
    assert (await dashboard(client))["accounts"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [
    ("GET", "/api/dashboard"),
    ("POST", "/api/accounts"),
    ("POST", "/api/chatgpt/login"),
    ("GET", "/api/chatgpt/login/missing"),
    ("DELETE", "/api/chatgpt/login/missing"),
    ("POST", "/api/chatgpt/login/missing/complete"),
])
@pytest.mark.parametrize("token_file", ["ingest.token", "chatgpt-agent.token"])
async def test_legacy_bearers_do_not_grant_owner_access(chatgpt_owner, method, path, token_file):
    client, _, _, root = chatgpt_owner
    legacy_token = "dummy-revoked-machine-token"
    (root / token_file).write_text(legacy_token)
    request = client.build_request(method, path, json={})
    request.headers.pop("Cookie", None)
    request.headers["Authorization"] = "Bearer " + legacy_token
    response = await client.send(request)
    assert response.status_code == 401
    assert (await dashboard(client))["accounts"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {}, {"callback_url": None}, {"callback_url": False},
    {"callback_url": 123}, {"callback_url": []}, {"callback_url": {}},
    {"callback_url": ""}, {"callback_url": "x" * 8193},
    {"callback_url": "http://localhost:1455/auth/callback?code=dummy", "extra": True},
])
async def test_chatgpt_completion_rejects_invalid_body_before_manager(chatgpt_owner, payload):
    client, manager, _, _ = chatgpt_owner
    login = await start_chatgpt(client)
    response = await client.post(f"/api/chatgpt/login/{login['id']}/complete", json=payload)
    assert response.status_code == 400, response.text
    assert manager.completions == []
    assert manager.has_credentials(login["id"]) is False
    assert (await dashboard(client))["accounts"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["{broken", "null", "[]", '"callback"'])
async def test_chatgpt_completion_requires_json_object(chatgpt_owner, payload):
    client, manager, _, _ = chatgpt_owner
    login = await start_chatgpt(client)
    response = await client.post(f"/api/chatgpt/login/{login['id']}/complete", content=payload,
                                 headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert manager.completions == []
    assert (await dashboard(client))["accounts"] == []


@pytest.mark.asyncio
async def test_chatgpt_completion_accepts_8192_char_boundary_without_truncating(chatgpt_owner):
    client, manager, _, _ = chatgpt_owner
    login = await start_chatgpt(client)
    prefix = "http://localhost:1455/auth/callback?code=dummy&state="
    callback_url = prefix + "s" * (8192 - len(prefix))
    response = await client.post(f"/api/chatgpt/login/{login['id']}/complete", json={
        "callback_url": callback_url,
    })
    assert response.status_code == 200, response.text
    assert response.json() == await manager.status(login["id"])
    assert manager.completions == [(login["id"], callback_url)]


@pytest.mark.asyncio
async def test_chatgpt_completion_provider_failure_is_http_400_without_account(chatgpt_owner):
    client, manager, _, _ = chatgpt_owner
    login = await start_chatgpt(client)
    manager.complete_error = "ChatGPT authorization state did not match."
    response = await client.post(f"/api/chatgpt/login/{login['id']}/complete", json={
        "callback_url": "http://localhost:1455/auth/callback?code=dummy&state=wrong",
    })
    assert response.status_code == 400
    assert response.json() == {"detail": manager.complete_error}
    assert manager.completions
    assert manager.has_credentials(login["id"]) is False
    assert (await dashboard(client))["accounts"] == []


@pytest.mark.asyncio
async def test_chatgpt_credential_cleanup_failure_preserves_account_until_retry(chatgpt_owner):
    client, manager, _, _ = chatgpt_owner
    login = await start_chatgpt(client)
    account_id = login["id"]
    await complete_chatgpt(client, account_id)
    before = await settled(client)
    manager.remove_failures.add(account_id)
    response = await client.delete(f"/api/accounts/{account_id}")
    assert response.status_code == 409
    assert "cleanup" in response.json()["detail"]
    assert manager.has_credentials(account_id) is True
    after = await dashboard(client)
    assert after["accounts"] == before["accounts"]
    refreshed = await refresh(client, account_id)
    assert refreshed["accounts"][0]["status"] == "connected"
    assert refreshed["accounts"][0]["has_credentials"] is True
    assert refreshed["accounts"][0]["summary"] == before["accounts"][0]["summary"]
    manager.remove_failures.remove(account_id)
    response = await client.delete(f"/api/accounts/{account_id}")
    assert response.status_code == 200 and response.json() == {"deleted": True}
    assert manager.has_credentials(account_id) is False
    assert (await dashboard(client))["accounts"] == []


@pytest.mark.asyncio
async def test_configured_https_origin_secure_cookie_proxy_and_host_validation(
    tmp_path, monkeypatch, app_module,
):
    public_origin = "https://use.example.com"
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ATLAS_PASSWORD_FILE", str(tmp_path / "owner.password"))
    monkeypatch.setenv("ATLAS_KEY_FILE", str(tmp_path / "encryption.key"))
    monkeypatch.setenv("ATLAS_PUBLIC_ORIGIN", public_origin)
    # A configured HTTPS origin must force Secure even if this legacy switch
    # was left false. Browser-facing HTTPS may terminate at the reverse proxy.
    monkeypatch.setenv("ATLAS_COOKIE_SECURE", "false")
    monkeypatch.setattr(app_module.chatgpt, "ChatGPTManager", FakeChatGPTManager)
    application = app_module.create_app(tmp_path, collector=FakeCollector(), poll_seconds=0)

    async def internal_http(scope, receive, send):
        # The browser sees HTTPS, but TLS terminates before this ASGI app.
        await application({**scope, "scheme": "http"}, receive, send)

    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=internal_http), base_url=public_origin,
            headers={"Origin": public_origin},
        ) as client:
            response = await client.post("/api/setup", json={
                "token": (tmp_path / "setup.token").read_text().strip(), "password": PASSWORD,
            })
            assert response.status_code == 200, response.text
            cookie = response.headers["set-cookie"].lower()
            assert "; secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
            assert (await client.get("/api/session")).json()["authenticated"] is True

            # Spoofed forwarding metadata cannot override the configured
            # origin or the actual Host, in either acceptance direction.
            spoofed = {
                "Forwarded": 'host="evil.example";proto=http',
                "X-Forwarded-Host": "evil.example",
                "X-Forwarded-Proto": "http",
            }
            response = await client.post("/api/chatgpt/login", json={}, headers=spoofed)
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "awaiting_authorization"
            for origin in (None, "http://use.example.com", "https://evil.example"):
                request = client.build_request("POST", "/api/logout", headers=spoofed)
                request.headers.pop("Origin", None)
                if origin is not None:
                    request.headers["Origin"] = origin
                assert (await client.send(request)).status_code == 403
            assert (await client.get("/api/session")).json()["authenticated"] is True

            for host in ("evil.example", "use.example.com.evil", "use.example.com:8080"):
                headers = {
                    "Host": host,
                    "Forwarded": 'host="use.example.com";proto=https',
                    "X-Forwarded-Host": "use.example.com",
                    "X-Forwarded-Proto": "https",
                }
                response = await client.get("/api/session", headers=headers)
                assert response.status_code == 400
                response = await client.post("/api/logout", headers=headers)
                assert response.status_code == 400
            response = await client.get("/healthz", headers={"Host": "internal-healthcheck"})
            assert response.status_code == 200 and response.json() == {"status": "ok"}
            response = await client.post("/healthz", headers={
                "Host": "internal-healthcheck", "Origin": "https://evil.example",
            })
            assert response.status_code == 403  # Only the Host check is exempt.
            assert (await client.post("/api/logout")).status_code == 200
            response = await client.post("/api/login", json={"password": PASSWORD})
            assert response.status_code == 200, response.text
            assert "; secure" in response.headers["set-cookie"].lower()
            assert (await client.get("/api/dashboard")).status_code == 200
    assert application.state.chatgpt.closed is True


@pytest.mark.parametrize("origin", [
    "http://atlas.test", "https://atlas.test/path", "https://atlas.test?query=value",
    "https://atlas.test#fragment", "https://user:password@atlas.test", "not-an-origin",
    "https://atlas.test:8443",
])
def test_public_origin_must_be_an_explicit_https_origin(tmp_path, monkeypatch, app_module, origin):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ATLAS_PASSWORD_FILE", str(tmp_path / "owner.password"))
    monkeypatch.setenv("ATLAS_KEY_FILE", str(tmp_path / "encryption.key"))
    monkeypatch.setenv("ATLAS_PUBLIC_ORIGIN", origin)
    with pytest.raises(ValueError, match="HTTPS origin"):
        app_module.create_app(tmp_path, collector=FakeCollector(), poll_seconds=0)


@pytest.mark.asyncio
async def test_gemini_browser_completion_persists_real_quota_and_deletes_credentials(owner, monkeypatch):
    client, _, root = owner
    requests = []

    def provider(request):
        requests.append(request)
        responses = {
            "https://oauth2.googleapis.com/token": {
                "access_token": "synthetic-access", "refresh_token": "synthetic-refresh",
                "token_type": "Bearer", "expires_in": 3600,
            },
            "https://www.googleapis.com/oauth2/v2/userinfo": {"id": "synthetic-user"},
            "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist": {
                "currentTier": {"id": "free-tier"}, "cloudaicompanionProject": "test-project",
            },
            "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota": {
                "buckets": [{"modelId": "gemini-pro", "remainingFraction": 0.37,
                             "resetTime": "2026-10-26T00:00:00Z"}],
            },
        }
        assert str(request.url) in responses
        return httpx.Response(200, json=responses[str(request.url)])

    real_client = httpx.AsyncClient
    def provider_client(**options):
        assert options["trust_env"] is False
        return real_client(**options, transport=httpx.MockTransport(provider))
    monkeypatch.setattr(httpx, "AsyncClient", provider_client)
    response = await client.post("/api/oauth/start", json={"provider": "gemini", "name": "Personal Gemini"})
    assert response.status_code == 200, response.text
    flow = response.json()
    assert flow["callback_mode"] == "code" and flow["provider"] == "gemini"
    assert flow["authorization_url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert (await dashboard(client))["accounts"] == []
    response = await client.post(f"/api/oauth/{flow['id']}/complete", json={"callback_input": "4/synthetic-code"})
    assert response.status_code == 200 and response.json()["status"] == "connected", response.text
    data = await settled(client)
    account, = data["accounts"]
    assert account["provider"] == "gemini-account" and account["has_credentials"]
    assert account["status"] == "connected" and account["last_success_at"]
    assert account["quota"][0]["label"] == "gemini-pro"
    assert account["quota"][0]["remaining"] == 37
    assert data["totals"]["total_tokens"] is None and data["models"] == []
    assert "synthetic-access" not in str(data) and "synthetic-refresh" not in str(data)
    credential = root / "subscriptions" / (flow["id"] + ".enc")
    assert credential.exists() and b"synthetic-access" not in credential.read_bytes()
    assert (await client.patch(f"/api/accounts/{flow['id']}", json={"credential": "wrong"})).status_code == 400
    assert (await client.patch(f"/api/accounts/{flow['id']}", json={"name": "Renamed", "enabled": False})).status_code == 200
    await settled(client)
    assert (await client.delete(f"/api/accounts/{flow['id']}")).status_code == 200
    assert not credential.exists() and (await dashboard(client))["accounts"] == []
    assert len(requests) >= 5


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["chatgpt", "gemini"])
async def test_oauth_flows_are_bound_to_starting_session_on_every_route(owner, provider):
    client, _, _ = owner
    started = await client.post("/api/oauth/start", json={"provider": provider, "name": "Personal"})
    assert started.status_code == 200, started.text
    flow_id = started.json()["id"]
    assert (await client.post("/api/login", json={"password": PASSWORD})).status_code == 200
    assert (await client.get(f"/api/oauth/{flow_id}")).status_code == 404
    assert (await client.post(f"/api/oauth/{flow_id}/complete", json={"callback_input": "wrong"})).status_code == 404
    assert (await client.delete(f"/api/oauth/{flow_id}")).status_code == 404
    if provider == "chatgpt":
        assert (await client.get(f"/api/chatgpt/login/{flow_id}")).status_code == 404
        assert (await client.post(f"/api/chatgpt/login/{flow_id}/complete", json={"callback_url": "wrong"})).status_code == 404
        assert (await client.delete(f"/api/chatgpt/login/{flow_id}")).status_code == 404


@pytest.mark.asyncio
async def test_connection_probe_does_not_save_accounts_or_echo_credentials(owner, monkeypatch):
    client, _, _ = owner
    async def probe(self, account):
        assert account["provider"] == "custom-api" and account["credential"] == "synthetic-probe-key"
        return {"records": [record()], "quota": [], "capabilities": ["tokens"], "note": "Read-only usage."}
    monkeypatch.setattr(FakeCollector, "__call__", probe)
    response = await client.post("/api/connections/test", json={
        "provider": "custom-api", "credential": "synthetic-probe-key",
        "base_url": "https://usage.example.com", "extra": {"auth_type": "auto"},
    })
    assert response.status_code == 200, response.text
    assert response.json()["ok"] and response.json()["capabilities"] == ["tokens"]
    assert "records" not in response.json() and "synthetic-probe-key" not in response.text
    assert (await dashboard(client))["accounts"] == []


@pytest.mark.asyncio
async def test_password_change_checks_current_and_revokes_other_sessions(owner):
    client, _, root = owner
    old_cookie = client.cookies.get("atlas_session")
    new_password = "replacement-pass123"
    assert (await client.post("/api/settings/password", json={
        "current_password": "wrong", "new_password": new_password,
    })).status_code == 400
    assert (await client.get("/api/session")).json()["authenticated"]
    assert (await client.post("/api/settings/password", json={
        "current_password": PASSWORD, "new_password": new_password,
    })).status_code == 200
    assert client.cookies.get("atlas_session") != old_cookie
    assert (root / "owner.password").stat().st_mode & 0o777 == 0o600
    assert (await client.get("/api/dashboard", headers={"Cookie": "atlas_session=" + old_cookie})).status_code == 401
    assert (await client.get("/api/dashboard")).status_code == 200
    assert (await client.post("/api/login", json={"password": PASSWORD})).status_code == 401
    assert (await client.post("/api/login", json={"password": new_password})).status_code == 200
