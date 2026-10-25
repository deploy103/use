"""Public collect() contract tests; all DNS and HTTP are synthetic."""

import asyncio
import json
import socket

import httpx
import pytest

import collectors

PUBLIC_IP = "93.184.216.34"
TOKEN = "dummy-test-credential-not-real"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def dns(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port))]

    def unconfigured(request):
        pytest.fail("Unexpected HTTP request in collector test")

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    monkeypatch.setattr(
        httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(unconfigured)
    )


@pytest.fixture
def service(monkeypatch):
    def install(payload=None, handler=None, status=200, headers=None):
        requests = []

        def respond(request):
            requests.append(request)
            assert request.url.host == PUBLIC_IP  # DNS resolution is pinned.
            assert request.headers["accept-encoding"] == "identity"
            if handler:
                return handler(request)
            return httpx.Response(status, json=payload, headers=headers)

        monkeypatch.setattr(
            httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(respond)
        )
        return requests

    return install


def account(provider="usage-json", **kwargs):
    value = {"provider": provider, "credential": TOKEN, "extra": {}}
    if provider in ("usage-json", "gemini", "codex-lb", "claude-lb"):
        value["base_url"] = "https://usage.example.com"
    value.update(kwargs)
    return value


def collect(value=None):
    return asyncio.run(collectors.collect(value or account()))


def row(**kwargs):
    return {"date": "2026-10-23", "model": "test-model", **kwargs}


def page(results=None, stamp=1792713600, more=False, cursor=None):
    return {
        "data": [] if results is None else [{"start_time": stamp, "results": results}],
        "has_more": more,
        "next_page": cursor,
    }


def test_provider_metadata_is_public_and_complete():
    assert {p["id"] for p in collectors.PROVIDERS} == {
        "chatgpt",
        "claude-lb",
        "openai",
        "gemini",
        "grok",
        "codex-lb",
        "usage-json",
    }
    for provider in collectors.PROVIDERS:
        assert set(provider) == {"id", "name", "default_url", "help", "fields"}
        assert TOKEN not in json.dumps(provider)


@pytest.mark.parametrize("credential", ["", TOKEN])
def test_chatgpt_requires_browser_login_without_dns_or_http(monkeypatch, credential):
    def unexpected(*args, **kwargs):
        pytest.fail("ChatGPT collector must not resolve DNS or open HTTP clients")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected)
    monkeypatch.setattr(httpx, "AsyncClient", unexpected)
    with pytest.raises(collectors.CollectionError, match="ChatGPT.*로그인") as error:
        collect(account("chatgpt", credential=credential))
    assert "API 키" in str(error.value)
    assert TOKEN not in str(error.value)


def test_usage_json_zero_missing_and_repeat_refresh(service):
    requests = service(
        {
            "records": [
                row(requests=0, input_tokens=100, output_tokens=0, cache_read_tokens=30)
            ],
            "capabilities": ["tokens", "requests"],
        }
    )
    first, second = collect(), collect()
    assert first == second
    measured = first["records"][0]
    assert measured["requests"] == measured["output_tokens"] == 0
    assert measured["cache_write_tokens"] is None
    assert measured["cost_usd"] is None
    assert (
        measured["input_tokens"] == 100
    )  # Uncached; 30 cache-read tokens are separate.
    assert len(requests) == 2
    assert requests[0].method == "GET" and requests[0].url.path == "/usage.json"
    assert requests[0].headers["host"] == "usage.example.com"
    assert requests[0].extensions["sni_hostname"] == "usage.example.com"
    assert requests[0].headers["authorization"] == "Bearer " + TOKEN


def test_lifetime_and_quota_do_not_become_daily_tokens(service):
    service(
        {
            "records": [],
            "summary": {"period": "lifetime", "requests": 0, "total_tokens": 12345},
            "quota": [
                {"label": "5h", "used_percent": 20},
                {"label": "7d", "remaining": 70, "unit": "percent"},
            ],
            "capabilities": ["requests", "tokens", "quota"],
        }
    )
    result = collect()
    assert result["records"] == []
    assert result["summary"]["total_tokens"] == 12345
    assert result["summary"]["input_tokens"] is None
    assert result["summary"]["requests"] == 0
    assert len(result["quota"]) == 2
    assert result["quota"][0]["limit"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"records": "not a list"},
        {"records": [row(requests=True)]},
        {"records": [row(requests=-1)]},
        {"records": [row(input_tokens=1.2)]},
        {"records": [row(input_tokens="5")]},
        {"records": [row(cost_usd=float("nan"))]},
        {"records": [row(date="2026-02-30")]},
        {"records": [row(model="bad\nmodel")]},
        {"records": [row(credential=TOKEN)]},
        {"records": [row(), row()]},
        {"records": [], "next_url": "https://evil.example"},
        {"records": [], "capabilities": ["inference"]},
        {"records": [], "capabilities": ["tokens", "tokens"]},
        {"records": [], "quota": [{"label": "day", "resets_at": "2026-10-25"}]},
        {"records": [], "summary": {"period": "day", "requests": 1}},
    ],
)
def test_rejects_invalid_adapter_payloads(service, payload):
    # JSON NaN is deliberately accepted by Python's decoder but rejected by the
    # collector's finite-number check, not silently emitted in dashboard JSON.
    service(
        handler=lambda request: httpx.Response(
            200,
            content=json.dumps(payload),
            headers={"content-type": "application/json"},
        )
    )
    with pytest.raises(collectors.CollectionError):
        collect()


@pytest.mark.parametrize(
    "status, message",
    [
        (401, "denied"),
        (403, "denied"),
        (429, "rate limited"),
        (500, "HTTP 500"),
        (302, "redirects"),
    ],
)
def test_status_failures_are_safe_and_do_not_redirect(service, status, message):
    requests = service(
        {"error": TOKEN}, status=status, headers={"location": "https://other.example/"}
    )
    with pytest.raises(collectors.CollectionError, match=message) as error:
        collect()
    assert TOKEN not in str(error.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "exception, message",
    [
        (httpx.ConnectError, "securely"),
        (httpx.ReadTimeout, "timed out"),
        (RuntimeError, "unsupported"),
    ],
)
def test_network_errors_never_echo_secrets(service, exception, message):
    def fail(request):
        raise exception(TOKEN)

    service(handler=fail)
    with pytest.raises(collectors.CollectionError, match=message) as error:
        collect()
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://usage.example.com",
        "file:///etc/passwd",
        "https://user:password@usage.example.com",
        "https://usage.example.com/other",
        "https://usage.example.com?key=secret",
        "https://usage.example.com?",
        "https://usage.example.com#fragment",
        "https://usage.example.com#",
        "https://usage.example.com:8443",
        "https://127.0.0.1",
        "https://10.0.0.1",
        "https://169.254.169.254",
        "https://[::1]",
        "https://[::ffff:127.0.0.1]",
        "https://[2002:7f00:1::]",
        "https://localhost",
        "https://host.local",
        "https://[fec0::1]",
        "https://[64:ff9b::a00:1]",
        "https://[ff02::1]",
        "https://usage.example.com\\@127.0.0.1",
        "https://usage.example.com\n",
        "https://u%73age.example.com",
    ],
)
def test_ssrf_invalid_origins_never_send_credentials(url):
    with pytest.raises(collectors.CollectionError):
        collect(account(base_url=url))


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.0.0.1"],
        [PUBLIC_IP, "169.254.169.254"],
        ["::1"],
        ["100.100.100.200"],
    ],
)
def test_ssrf_private_or_mixed_dns_answers_blocked(monkeypatch, addresses):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses
        ],
    )
    with pytest.raises(collectors.CollectionError, match="public HTTPS"):
        collect()


def test_dns_failure_safe(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror(TOKEN)

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(collectors.CollectionError, match="DNS lookup failed") as error:
        collect()
    assert TOKEN not in str(error.value)


def test_empty_credential_allowed_only_for_trusted_adapter(service):
    requests = service({"records": []})
    collect(account(credential=""))
    assert "authorization" not in requests[0].headers
    with pytest.raises(collectors.CollectionError, match="credential is required"):
        collect(account("codex-lb", credential=""))


@pytest.mark.parametrize("provider", ["openai", "grok"])
def test_official_credentials_cannot_be_sent_to_custom_origin(provider):
    with pytest.raises(collectors.CollectionError, match="fixed official"):
        collect(account(provider, base_url="https://evil.example.com"))


def test_no_arbitrary_options_or_header_injection():
    with pytest.raises(collectors.CollectionError, match="unsupported fields"):
        collect(account(extra={"path": "/inference"}))
    with pytest.raises(collectors.CollectionError, match="credential is invalid"):
        collect(account(credential="key\r\nInjected: header"))


@pytest.mark.parametrize(
    "field, value",
    [("credential", False), ("credential", None), ("extra", []), ("extra", False)],
)
def test_account_option_types_are_strict(field, value):
    with pytest.raises(collectors.CollectionError):
        collect(account(**{field: value}))


def test_signed_billing_credit_is_not_discarded(service):
    service({"records": [row(cost_usd=-0.5)]})
    assert collect()["records"][0]["cost_usd"] == -0.5


def test_body_limit_and_malformed_json(service):
    service(
        handler=lambda request: httpx.Response(
            200,
            content=b" " * (collectors._MAX_BODY + 1),
            headers={"content-type": "application/json"},
        )
    )
    with pytest.raises(collectors.CollectionError, match="too large"):
        collect()
    service(
        handler=lambda request: httpx.Response(
            200, content=b"{broken", headers={"content-type": "application/json"}
        )
    )
    with pytest.raises(collectors.CollectionError, match="invalid"):
        collect()


def test_oversize_stream_stops_reading_and_closes(service):
    class Stream(httpx.AsyncByteStream):
        chunks = 0
        closed = False

        async def __aiter__(self):
            for _ in range(100):
                self.chunks += 1
                yield b" " * (1024 * 1024)

        async def aclose(self):
            self.closed = True

    stream = Stream()
    service(
        handler=lambda request: httpx.Response(
            200, headers={"content-type": "application/json"}, stream=stream
        )
    )
    with pytest.raises(collectors.CollectionError, match="too large"):
        collect()
    assert stream.chunks == 3 and stream.closed


def test_non_json_and_compressed_responses_rejected(service):
    service(handler=lambda request: httpx.Response(200, text="<html>login</html>"))
    with pytest.raises(collectors.CollectionError, match="did not return JSON"):
        collect()
    service(
        handler=lambda request: httpx.Response(
            200,
            headers={"content-encoding": "br", "content-type": "application/json"},
            stream=httpx.ByteStream(b"compressed"),
        )
    )
    with pytest.raises(collectors.CollectionError, match="Compressed"):
        collect()


def test_openai_paginates_usage_and_costs_without_cache_double_count(service):
    usage = {
        "model": "gpt-test",
        "num_model_requests": 2,
        "input_tokens": 1000,
        "output_tokens": 500,
        "input_cached_tokens": 400,
        "input_cache_write_tokens": 100,
    }

    def respond(request):
        assert request.method == "GET" and request.headers["host"] == "api.openai.com"
        assert request.url.params["bucket_width"] == "1d"
        assert request.url.params["limit"] == "31"
        if request.url.path.endswith("completions"):
            assert request.url.params["group_by"] == "model"
            payload = (
                page([usage], more=True, cursor="usage-next")
                if "page" not in request.url.params
                else page([{"model": "gpt-other", "input_tokens": 0}], stamp=1792800000)
            )
        else:
            assert request.url.path == "/v1/organization/costs"
            payload = (
                page(
                    [{"amount": {"value": 0.125, "currency": "usd"}}],
                    more=True,
                    cursor="cost-next",
                )
                if "page" not in request.url.params
                else page(
                    [{"amount": {"value": 0, "currency": "usd"}}], stamp=1792800000
                )
            )
        return httpx.Response(200, json=payload)

    requests = service(handler=respond)
    result = collect(account("openai"))
    assert len(requests) == 4
    measured = next(r for r in result["records"] if r["model"] == "gpt-test")
    assert measured["input_tokens"] == 500
    assert (
        sum(
            measured[k]
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
            )
        )
        == 1500
    )
    assert (
        measured["cache_read_tokens"] == 400 and measured["cache_write_tokens"] == 100
    )
    assert measured["cost_usd"] is None
    costs = [r for r in result["records"] if r["model"] == "[organization costs]"]
    assert sum(r["cost_usd"] for r in costs) == 0.125
    assert all(r["input_tokens"] is None for r in costs)
    assert "not ChatGPT" in result["note"]


@pytest.mark.parametrize(
    "usage, uncached",
    [
        (
            {
                "input_tokens": 1000,
                "input_cached_tokens": 400,
                "input_cache_write_tokens": 0,
            },
            600,
        ),
        ({"input_tokens": 1000, "input_cached_tokens": 400}, None),
        ({"input_tokens": 1000, "input_uncached_tokens": 500}, 500),
        ({"input_tokens": 0}, 0),
    ],
)
def test_openai_missing_cache_is_unknown_not_zero(service, usage, uncached):
    service(
        handler=lambda request: httpx.Response(
            200,
            json=page([{**usage, "model": "gpt"}])
            if request.url.path.endswith("completions")
            else page(),
        )
    )
    result = collect(account("openai"))
    assert result["records"][0]["input_tokens"] == uncached
    if "input_cache_write_tokens" not in usage:
        assert result["records"][0]["cache_write_tokens"] is None


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": 100, "input_cached_tokens": 101},
        {
            "input_tokens": 100,
            "input_cached_tokens": 80,
            "input_cache_write_tokens": 30,
        },
        {"input_tokens": 100, "input_uncached_tokens": 80, "input_cached_tokens": 30},
    ],
)
def test_openai_inconsistent_cached_counts_fail_safely(service, usage):
    service(page([{**usage, "model": "gpt"}]))
    with pytest.raises(collectors.CollectionError, match="inconsistent cached"):
        collect(account("openai"))


@pytest.mark.parametrize(
    "mode",
    [
        "repeated",
        "missing",
        "unbounded",
        "duplicate_bucket",
        "cost_failure",
        "currency",
    ],
)
def test_openai_pagination_or_cost_failure_discards_partial_result(service, mode):
    calls = []

    def respond(request):
        calls.append(request)
        if mode in ("cost_failure", "currency") and request.url.path.endswith(
            "completions"
        ):
            return httpx.Response(
                200, json=page([{"model": "gpt", "input_tokens": 100}])
            )
        if mode == "cost_failure":
            return httpx.Response(403, json={"error": TOKEN})
        if mode == "currency":
            return httpx.Response(
                200, json=page([{"amount": {"value": 1, "currency": "eur"}}])
            )
        payload = page(
            [] if mode == "duplicate_bucket" else None,
            more=True,
            cursor=None
            if mode == "missing"
            else str(len(calls))
            if mode == "unbounded"
            else "same",
        )
        return httpx.Response(200, json=payload)

    service(handler=respond)
    with pytest.raises(collectors.CollectionError):
        collect(account("openai"))
    assert len(calls) <= 10


@pytest.mark.parametrize("days", [0, 91, "30", True, 2.5])
def test_lookback_is_bounded_before_request(days):
    with pytest.raises(collectors.CollectionError, match="Lookback days"):
        collect(account("openai", extra={"days": days}))


def test_grok_uses_documented_read_only_analytics_query(service):
    requests = service(
        {
            "limitReached": False,
            "timeSeries": [
                {
                    "group": ["Chat grok-test"],
                    "dataPoints": [
                        {"timestamp": "2026-10-22T15:00:00Z", "values": [0.75]},
                        {"timestamp": "2026-10-23T15:00:00Z", "values": [0]},
                    ],
                }
            ],
        }
    )
    result = collect(account("grok", extra={"team_id": "team-test", "days": 7}))
    request = requests[0]
    assert (
        request.method == "POST"
        and request.url.path == "/v1/billing/teams/team-test/usage"
    )
    body = json.loads(request.content)["analyticsRequest"]
    assert body["values"] == [{"name": "usd", "aggregation": "AGGREGATION_SUM"}]
    assert body["groupBy"] == ["description"] and body["timeUnit"] == "TIME_UNIT_DAY"
    assert body["timeRange"]["timezone"] == "Asia/Seoul"
    assert result["capabilities"] == ["cost"]
    assert result["records"][0]["date"] == "2026-10-23"
    assert result["records"][1]["cost_usd"] == 0
    assert all(
        r["input_tokens"] is None and r["requests"] is None for r in result["records"]
    )


def test_grok_truncation_and_team_path_injection_rejected(service):
    service({"timeSeries": [], "limitReached": True})
    with pytest.raises(collectors.CollectionError, match="truncated"):
        collect(account("grok", extra={"team_id": "team-test"}))
    with pytest.raises(collectors.CollectionError, match="team ID"):
        collect(account("grok", extra={"team_id": "../admin"}))


@pytest.mark.parametrize("provider", ["gemini", "codex-lb"])
def test_gateway_lifetime_and_distinct_quota_windows(service, provider):
    window = {
        "limit_type": "credits",
        "limit_window": "5h",
        "max_value": 100,
        "current_value": 25,
        "remaining_value": 75,
        "source": "aggregate",
        "reset_at": "2026-10-25T06:00:00Z",
    }
    week = {**window, "limit_window": "7d", "current_value": 60, "remaining_value": 40}
    requests = service(
        {
            "request_count": 3,
            "total_tokens": 12345,
            "cached_input_tokens": 100,
            "total_cost_usd": 9.99,
            "limits": [window, week],
            "upstream_limits": [window, week],
            "account_pool_usage": {"primary": 75, "secondary": None},
        }
    )
    result = collect(account(provider))
    assert requests[0].method == "GET" and requests[0].url.path == "/v1/usage"
    assert requests[0].headers["authorization"] == "Bearer " + TOKEN
    assert result["records"] == []
    assert result["summary"]["total_tokens"] == 12345
    assert result["summary"]["requests"] == 3
    assert result["summary"]["cache_read_tokens"] == 100
    assert (
        result["summary"]["input_tokens"] is None
        and result["summary"]["cost_usd"] is None
    )
    assert "cost" not in result["capabilities"]
    assert len(result["quota"]) == 3  # Fallback duplicates removed; windows not summed.
    assert result["quota"][0]["used_percent"] == 25
    assert result["quota"][1]["used_percent"] == 60
    assert result["quota"][2]["remaining"] == 75


def test_gemini_no_history_or_google_key_endpoint_guesses(service):
    service({"usage": {"tokens": 0}})
    with pytest.raises(collectors.CollectionError, match="invalid"):
        collect(account("gemini"))
    with pytest.raises(collectors.CollectionError, match="gateway"):
        collect(account("gemini", extra={"mode": "google-api"}))


def test_claude_documented_self_usage_auth_and_independent_windows(service):
    cost_window = {
        "limit_type": "cost_usd",
        "limit_window": "3h",
        "max_value": 10_000_000,
        "current_value": 2_500_000,
        "remaining_value": 7_500_000,
        "used_percent": 25,
        "model_filter": None,
        "reset_at": "2026-10-25T07:00:00Z",
    }
    requests = service(
        {
            "request_count": 2,
            "total_tokens": 1500,
            "cached_input_tokens": 10,
            "total_cost_usd": 0.25,
            "limits": [
                cost_window,
                {**cost_window, "limit_window": "daily"},
                {**cost_window, "limit_window": "weekly"},
                {
                    **cost_window,
                    "model_filter": "claude-fable-test",
                    "limit_type": "total_tokens",
                    "max_value": 100,
                    "remaining_value": 0,
                    "used_percent": 100,
                },
            ],
        }
    )
    result = collect(account("claude-lb"))
    request = requests[0]
    assert request.method == "GET" and request.url.path == "/v1/usage/self"
    assert request.headers["host"] == "usage.example.com"
    assert request.headers["x-api-key"] == TOKEN
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in request.headers
    assert (
        result["records"] == []
    )  # Never fabricate dated history from a lifetime counter.
    assert result["summary"]["period"] == "lifetime"
    assert result["summary"]["requests"] == 2
    assert result["summary"]["total_tokens"] == 1500
    assert result["summary"]["cache_read_tokens"] == 10
    assert result["summary"]["input_tokens"] is None
    assert (
        result["summary"]["cost_usd"] is None and "cost" not in result["capabilities"]
    )
    assert len(result["quota"]) == 4  # No cross-window aggregation.
    first = result["quota"][0]
    assert first == {
        "label": "3h · all models",
        "unit": "USD equivalent",
        "limit": 10,
        "remaining": 7.5,
        "used_percent": 25,
        "resets_at": "2026-10-25T07:00:00Z",
    }
    assert result["quota"][3]["remaining"] == 0
    assert "claude-fable-test" in result["quota"][3]["label"]
    assert "기간별 합계에 더하지" in result["note"]


def test_claude_missing_key_and_auth_rejection_are_safe(service):
    with pytest.raises(
        collectors.CollectionError, match="Claude LB API key is required"
    ):
        collect(account("claude-lb", credential=""))
    requests = service({"error": TOKEN}, status=401)
    with pytest.raises(collectors.CollectionError, match="denied") as error:
        collect(account("claude-lb"))
    assert len(requests) == 1 and TOKEN not in str(error.value)


def test_claude_no_limits_preserves_measured_zero_and_missing_split(service):
    service(
        {
            "request_count": 0,
            "total_tokens": 0,
            "cached_input_tokens": 0,
            "total_cost_usd": 0,
            "limits": [],
        }
    )
    result = collect(account("claude-lb"))
    assert result["records"] == result["quota"] == []
    assert result["summary"]["requests"] == result["summary"]["total_tokens"] == 0
    assert result["summary"]["input_tokens"] is None
    assert result["summary"]["cost_usd"] is None


def test_claude_invalid_schema_cannot_be_reported_as_zero(service):
    service({"message": "login required"})
    with pytest.raises(collectors.CollectionError, match="invalid"):
        collect(account("claude-lb"))
