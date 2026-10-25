"""Read-only usage collectors. Input tokens are UNCACHED (exclusive of caches).

The usage-json adapter reads GET /usage.json from a trusted HTTPS origin, with
Authorization: Bearer <credential>. Its JSON object has records (daily model
records from README), quota, capabilities, and optional summary (metric fields
plus period='lifetime'). Unknown measurements must be null/absent, never zero.
Quota windows are independent and are never converted to token consumption.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import re
import socket
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx


class CollectionError(Exception):
    """A fixed, credential-free message suitable for displaying to the owner."""


PROVIDERS = [
    {
        "id": "chatgpt",
        "name": "ChatGPT 계정",
        "default_url": "https://chatgpt.com",
        "help": "OpenAI 인증 페이지를 여는 브라우저 OAuth로 연결합니다. Codex 구독 한도만 조회하며, ChatGPT 웹 전체 대화의 토큰 기록은 아닙니다. API 키는 필요하지 않습니다.",
        "fields": [],
    },
    {
        "id": "codex-lb",
        "name": "Codex LB",
        "default_url": "",
        "help": "Codex LB 서버 주소와 gateway 조회 키를 연결합니다. 해당 키의 누적 토큰·요청·잔여 한도이며, OpenAI API 키나 ChatGPT 전체 사용량과는 다릅니다.",
        "fields": [],
    },
    {
        "id": "claude-lb",
        "name": "Claude LB",
        "default_url": "",
        "help": "Claude LB 서버 주소와 sk-clb 조회 키를 입력하세요. 해당 키의 누적 토큰과 세션·일·주간 한도를 조회합니다. 한도 금액은 실제 청구액이 아닌 사용량 환산액입니다.",
        "fields": [],
    },
    {
        "id": "openai",
        "name": "OpenAI API",
        "default_url": "https://api.openai.com",
        "help": "OpenAI API 조직 관리자 키가 필요합니다. ChatGPT 구독 계정은 별도의 ChatGPT 로그인으로 연결하세요. API 사용량은 UTC 일별 집계이며 조직 비용은 모델별 비용과 구분됩니다.",
        "fields": [
            {
                "name": "days",
                "label": "Lookback days (1–90, default 30)",
                "type": "number",
            }
        ],
    },
    {
        "id": "gemini",
        "name": "Gemini LB",
        "default_url": "",
        "help": "Gemini LB gateway mode: public HTTPS origin and gateway key for GET /v1/usage. Lifetime counters and separate quota windows only; no model/day history. Configured cost estimates are not billed costs. Not a Google inference API key.",
        "fields": [
            {"name": "mode", "label": "Integration mode (gateway)", "type": "text"}
        ],
    },
    {
        "id": "grok",
        "name": "Grok / xAI API",
        "default_url": "https://management-api.x.ai",
        "help": "Management key and team ID required. Documented daily API spend only; token/request counters are unavailable in this integration. Not Grok web subscription usage.",
        "fields": [
            {"name": "team_id", "label": "Team ID", "type": "text"},
            {
                "name": "days",
                "label": "Lookback days (1–90, default 30)",
                "type": "number",
            },
        ],
    },
    {
        "id": "usage-json",
        "name": "Trusted usage JSON",
        "default_url": "",
        "help": "Trusted HTTPS origin serving GET /usage.json with records, quota, capabilities and optional lifetime summary (README collector schema). Bearer credential optional. Input tokens exclude cache reads/writes. No arbitrary paths.",
        "fields": [],
    },
]

_METRICS = (
    "requests",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_usd",
)
_CAPABILITIES = ("tokens", "requests", "cost", "quota")
_MAX_BODY = 2 * 1024 * 1024
_MAX_RECORDS = 10000
_MAX_PAGES = 10
_TIMEOUT = 15
_KST = ZoneInfo("Asia/Seoul")
_INVALID = "The usage service returned an invalid or unsupported response."


def _object(value):
    if not isinstance(value, dict):
        raise CollectionError(_INVALID)
    return value


def _array(value, limit=_MAX_RECORDS):
    if not isinstance(value, list) or len(value) > limit:
        raise CollectionError(_INVALID)
    return value


def _text(value, limit=200):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise CollectionError(_INVALID)
    return value


def _number(value, *, integer=False, negative=False):
    if value is None:
        return None
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or abs(value) > 10**18
    ):
        raise CollectionError(_INVALID)
    if (not negative and value < 0) or (integer and (int(value) != value)):
        raise CollectionError(_INVALID)
    return int(value) if integer else value


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise CollectionError(_INVALID)
    try:
        date.fromisoformat(value)
    except ValueError:
        raise CollectionError(_INVALID) from None
    return value


def _metrics(value):
    value = _object(value)
    return {
        key: _number(
            value.get(key), integer=key != "cost_usd", negative=key == "cost_usd"
        )
        for key in _METRICS
    }


def _record(day, model, **metrics):
    return {"date": _day(day), "model": _text(model), **_metrics(metrics)}


def _merge(records):
    """Aggregate independent rows; an unknown component keeps the sum unknown."""
    merged = {}
    for row in records:
        key = (row["date"], row["model"])
        if key not in merged:
            merged[key] = row.copy()
        else:
            for metric in _METRICS:
                a, b = merged[key][metric], row[metric]
                merged[key][metric] = (
                    None
                    if a is None or b is None
                    else _number(
                        a + b,
                        integer=metric != "cost_usd",
                        negative=metric == "cost_usd",
                    )
                )
    if len(merged) > _MAX_RECORDS:
        raise CollectionError("The usage response contains too many records.")
    return sorted(merged.values(), key=lambda row: (row["date"], row["model"]))


def _public_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise CollectionError("The service address could not be validated.") from None
    if (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or (
            address.version == 6
            and (
                address.is_site_local
                or address.ipv4_mapped
                or address.sixtofour
                or address.teredo
            )
        )
    ):
        raise CollectionError("Only public HTTPS service addresses are allowed.")
    return str(address)


def _origin(value):
    if (
        not isinstance(value, str)
        or len(value) > 500
        or any(c.isspace() or ord(c) < 32 for c in value)
        or any(c in value for c in "\\?#%")
    ):
        raise CollectionError(
            "Configure an HTTPS origin without a path, credentials, query, or fragment."
        )
    try:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.path not in ("", "/")
            or parts.port not in (None, 443)
        ):
            raise ValueError
        url = httpx.URL(value)
        host = url.host
        if ":" in host or re.fullmatch(r"[\d.]+", host):
            _public_ip(host)
        elif (
            not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", host)
            or "." not in host
            or host.endswith((".localhost", ".local", ".internal"))
        ):
            raise ValueError
        return str(url.copy_with(path="", query=None, fragment=None)).rstrip("/")
    except (ValueError, httpx.InvalidURL):
        raise CollectionError(
            "Configure a public HTTPS origin on port 443 without credentials or a path."
        ) from None


async def _resolve_public(host):
    try:
        # Resolve exactly once per request and pin that address below. Validating
        # DNS then letting httpx resolve the name again permits DNS rebinding.
        answers = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM),
            timeout=5,
        )
    except (TimeoutError, OSError):
        raise CollectionError("The usage service DNS lookup failed.") from None
    if not answers:
        raise CollectionError("The usage service DNS lookup failed.")
    addresses = [_public_ip(answer[4][0]) for answer in answers]
    return addresses[0]


class _PublicTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport(retries=0, trust_env=False)

    async def handle_async_request(self, request):
        hostname = request.url.host
        address = await _resolve_public(hostname)
        # Preserve the original TLS identity and Host while connecting only to
        # the validated public numeric address; no proxy/environment bypass.
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=address),
            headers=request.headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": hostname},
        )
        return await self.inner.handle_async_request(pinned)

    async def aclose(self):
        await self.inner.aclose()


async def _json(client, method, url, *, params=None, body=None):
    async with client.stream(method, url, params=params, json=body) as response:
        if response.status_code in (401, 403):
            raise CollectionError(
                "Usage access was denied. Check the credential and usage/admin permissions."
            )
        if response.status_code == 429:
            raise CollectionError(
                "The usage service rate limited this request. Try again later."
            )
        if 300 <= response.status_code < 400:
            raise CollectionError("Usage service redirects are not allowed.")
        if response.status_code != 200:
            raise CollectionError(
                f"The usage service returned HTTP {response.status_code}."
            )
        if response.headers.get("content-encoding", "identity").lower() not in (
            "identity",
            "",
        ):
            raise CollectionError("Compressed usage responses are not supported.")
        if (
            response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            raise CollectionError("The usage service did not return JSON.")
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > _MAX_BODY:
                raise CollectionError("The usage response is too large.")
            chunks.append(chunk)
        try:
            return _object(json.loads(b"".join(chunks)))
        except (ValueError, RecursionError):
            raise CollectionError(_INVALID) from None


def _days(extra):
    days = extra.get("days", 30)
    if type(days) is not int or not 1 <= days <= 90:
        raise CollectionError("Lookback days must be an integer from 1 to 90.")
    return days


def _openai_uncached(result):
    total = _number(result.get("input_tokens"), integer=True)
    read = _number(result.get("input_cached_tokens"), integer=True)
    write = _number(result.get("input_cache_write_tokens"), integer=True)
    known_cache = sum(part for part in (read, write) if part is not None)
    if total is not None and known_cache > total:
        raise CollectionError(
            "The usage service returned inconsistent cached token counts."
        )
    if result.get("input_uncached_tokens") is not None:
        uncached = _number(result["input_uncached_tokens"], integer=True)
        if total is not None and (
            uncached + known_cache > total
            or (
                read is not None
                and write is not None
                and uncached + known_cache != total
            )
        ):
            raise CollectionError(
                "The usage service returned inconsistent cached token counts."
            )
        return uncached
    if total == 0:
        return 0
    # Optional counters absent from older responses are unknown, not measured
    # zero. Do not silently put unreported cache writes into uncached input.
    return (
        total - read - write
        if all(part is not None for part in (total, read, write))
        else None
    )


async def _openai(client, origin, extra):
    # Official API: developers.openai.com/api/reference/resources/admin/
    # subresources/organization/subresources/usage/methods/completions
    now = datetime.now(UTC)
    start = (now - timedelta(days=_days(extra) - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    common = {
        "start_time": int(start.timestamp()),
        "end_time": int(now.timestamp()),
        "bucket_width": "1d",
        "limit": 31,
    }
    records = []
    for path, costs in (
        ("/v1/organization/usage/completions", False),
        ("/v1/organization/costs", True),
    ):
        params = dict(common)
        if not costs:
            params["group_by"] = "model"
        seen_pages, seen_buckets = set(), set()
        for _ in range(_MAX_PAGES):
            payload = await _json(client, "GET", origin + path, params=params)
            for bucket in _array(payload.get("data"), 180):
                bucket = _object(bucket)
                stamp = _number(bucket.get("start_time"), integer=True)
                if stamp is None or stamp in seen_buckets:
                    raise CollectionError(_INVALID)
                seen_buckets.add(stamp)
                try:
                    day = datetime.fromtimestamp(stamp, UTC).date().isoformat()
                except (ValueError, OverflowError, OSError):
                    raise CollectionError(_INVALID) from None
                for result in _array(bucket.get("results")):
                    result = _object(result)
                    if costs:
                        amount = _object(result.get("amount"))
                        if amount.get("currency") != "usd":
                            raise CollectionError(
                                "The usage service returned costs without a supported USD currency."
                            )
                        records.append(
                            _record(
                                day,
                                "[organization costs]",
                                cost_usd=amount.get("value"),
                            )
                        )
                    else:
                        records.append(
                            _record(
                                day,
                                result.get("model") or "[unattributed model]",
                                requests=result.get("num_model_requests"),
                                input_tokens=_openai_uncached(result),
                                output_tokens=result.get("output_tokens"),
                                cache_read_tokens=result.get("input_cached_tokens"),
                                cache_write_tokens=result.get(
                                    "input_cache_write_tokens"
                                ),
                            )
                        )
            if type(payload.get("has_more")) is not bool:
                raise CollectionError(_INVALID)
            if not payload["has_more"]:
                break
            cursor = _text(payload.get("next_page"), 2048)
            if cursor in seen_pages:
                raise CollectionError("The usage service repeated a pagination cursor.")
            seen_pages.add(cursor)
            params["page"] = cursor
        else:
            raise CollectionError(
                "The usage service exceeded the pagination limit; no partial data was saved."
            )
    return {
        "records": _merge(records),
        "quota": [],
        "capabilities": ["tokens", "requests", "cost"],
        "note": "OpenAI API only, not ChatGPT web/subscription. UTC daily completions; API-wide billed costs shown separately, not allocated to models. Input excludes cache reads/writes; unknown if optional cache breakdown is missing.",
    }


async def _grok(client, origin, extra):
    # docs.x.ai/developers/rest-api-reference/management/billing.md documents
    # this POST as a read-only analytics query, with usd and description.
    team = extra.get("team_id")
    if not isinstance(team, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", team):
        raise CollectionError("Configure the xAI team ID for the management key.")
    end = datetime.now(_KST).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    body = {
        "analyticsRequest": {
            "timeRange": {
                "startTime": (end - timedelta(days=_days(extra))).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "endTime": end.strftime("%Y-%m-%d %H:%M:%S"),
                "timezone": "Asia/Seoul",
            },
            "timeUnit": "TIME_UNIT_DAY",
            "values": [{"name": "usd", "aggregation": "AGGREGATION_SUM"}],
            "groupBy": ["description"],
            "filters": [],
        }
    }
    payload = await _json(
        client, "POST", origin + f"/v1/billing/teams/{team}/usage", body=body
    )
    if type(payload.get("limitReached")) is not bool:
        raise CollectionError(_INVALID)
    if payload["limitReached"]:
        raise CollectionError(
            "xAI truncated the analytics response; no partial data was saved."
        )
    records = []
    for series in _array(payload.get("timeSeries")):
        series = _object(series)
        group = _array(series.get("group"), 1)
        if len(group) != 1:
            raise CollectionError(_INVALID)
        model = _text(
            group[0]
        )  # Keep the authoritative description; do not guess a model ID.
        for point in _array(series.get("dataPoints"), 100):
            point = _object(point)
            try:
                stamp = datetime.fromisoformat(_text(point.get("timestamp"), 50))
                if stamp.tzinfo is None:
                    raise ValueError
                day = stamp.astimezone(_KST).date().isoformat()
            except ValueError:
                raise CollectionError(_INVALID) from None
            values = _array(point.get("values"), 1)
            if len(values) != 1:
                raise CollectionError(_INVALID)
            records.append(_record(day, model, cost_usd=values[0]))
    return {
        "records": _merge(records),
        "quota": [],
        "capabilities": ["cost"],
        "note": "xAI management API spend by billing description, Asia/Seoul days. Tokens, cache counters, requests and Grok web subscription quotas are not available from this integration.",
    }


def _lifetime_summary(payload):
    if not {"request_count", "total_tokens", "cached_input_tokens", "limits"} <= set(
        payload
    ):
        raise CollectionError(_INVALID)
    summary = {
        "period": "lifetime",
        **_metrics(
            {
                "requests": payload["request_count"],
                "cache_read_tokens": payload["cached_input_tokens"],
            }
        ),
        "total_tokens": _number(payload["total_tokens"], integer=True),
    }
    total, cached = summary["total_tokens"], summary["cache_read_tokens"]
    if total is not None and cached is not None and cached > total:
        raise CollectionError(
            "The usage service returned inconsistent cached token counts."
        )
    return summary


async def _claude(client, origin):
    # Endpoint/auth from the user's deployment guide §7C. Schema independently
    # implemented from tokmaxxing/claude-lb-usage at revision
    # 9b080ff2b1da3548d54ddc228c70b0b8d2d11f48:
    # plugins/claude-lb-usage/bin/claude-lb-usage (_limit_amount, _window_title)
    # and tests/test_integration.py (_UsageHandler.payload). No code copied.
    payload = await _json(client, "GET", origin + "/v1/usage/self")
    summary = _lifetime_summary(payload)
    quota = []
    for window in _array(payload["limits"], 100):
        window = _object(window)
        unit = _text(window.get("limit_type"), 50)
        period = _text(window.get("limit_window"), 50)
        model = window.get("model_filter")
        if model is not None:
            model = _text(model)
        maximum = _number(window.get("max_value"))
        remaining = _number(window.get("remaining_value"))
        if unit == "cost_usd":
            # Limit amounts are microdollars of money-equivalent usage, not
            # actual provider bills. Never place this in cost_usd aggregates.
            maximum = maximum / 1_000_000 if maximum is not None else None
            remaining = remaining / 1_000_000 if remaining is not None else None
            unit = "USD equivalent"
        quota.append(
            {
                "label": period + (f" · {model}" if model else " · all models"),
                "unit": unit,
                "limit": maximum,
                "remaining": remaining,
                "used_percent": _number(window.get("used_percent")),
                "resets_at": window.get("reset_at"),
            }
        )
    result = _usage_json(
        {
            "records": [],
            "quota": quota,
            "summary": summary,
            "capabilities": ["tokens", "requests"] + (["quota"] if quota else []),
        }
    )
    result["note"] = (
        "Claude LB 키의 누적 기록입니다. 일별·모델별 기록은 제공되지 않습니다. 금액 한도는 사용량 환산액이며 실제 청구액이 아닙니다. 서로 겹치는 한도와 기간별 합계에 더하지 않습니다."
    )
    return result


async def _gateway(client, origin, extra):
    # Verified local Gemini LB app/modules/proxy/{api,schemas}.py:
    # GET /v1/usage authenticates via validate_usage_api_key, not dashboard cookies.
    if extra.get("mode", "gateway") != "gateway":
        raise CollectionError("LB gateway 조회 키와 공개 HTTPS 서버 주소를 입력하세요.")
    payload = await _json(client, "GET", origin + "/v1/usage")
    summary = _lifetime_summary(payload)
    quota, seen = [], set()
    for window in [
        *_array(payload["limits"], 100),
        *_array(payload.get("upstream_limits", []), 100),
    ]:
        window = _object(window)
        unit = _text(window.get("limit_type"), 50)
        period = _text(window.get("limit_window"), 50)
        source = _text(window.get("source", "api_key_limit"), 50)
        model = window.get("model_filter")
        if model is not None:
            model = _text(model)
        maximum, current = (
            _number(window.get("max_value")),
            _number(window.get("current_value")),
        )
        remaining = _number(window.get("remaining_value"))
        reset = window.get("reset_at")
        # limits falls back to upstream_limits when no key limits exist. Keep
        # both kinds, but do not show that fallback twice or sum any windows.
        identity = (source, unit, period, model, reset)
        if identity in seen:
            continue
        seen.add(identity)
        quota.append(
            {
                "label": f"{source} · {period}" + (f" · {model}" if model else ""),
                "unit": unit,
                "limit": maximum,
                "remaining": remaining,
                "used_percent": current / maximum * 100
                if maximum and current is not None
                else None,
                "resets_at": reset,
            }
        )
    # Pool counters are remaining percentages, not consumed tokens or dollars.
    pool = payload.get("account_pool_usage")
    if pool is not None:
        for key in ("primary", "secondary"):
            remaining = _number(_object(pool).get(key))
            if remaining is not None:
                if remaining > 100:
                    raise CollectionError(_INVALID)
                quota.append(
                    {
                        "label": f"Account pool · {key}",
                        "unit": "percent",
                        "limit": 100,
                        "remaining": remaining,
                        "used_percent": 100 - remaining,
                    }
                )
    result = _usage_json(
        {
            "records": [],
            "quota": quota,
            "capabilities": ["tokens", "requests"] + (["quota"] if quota else []),
        }
    )
    result["summary"] = summary
    result["note"] = (
        "LB 키의 누적 기록이며 일별·모델별·입출력 구분은 제공되지 않습니다. 한도는 서로 겹치므로 더하지 않습니다. LB 비용 환산값은 실제 청구액이 아니므로 합계에서 제외합니다."
    )
    return result


def _usage_json(payload):
    if set(payload) - {"records", "quota", "capabilities", "summary"}:
        raise CollectionError(_INVALID)
    records, keys = [], set()
    for row in _array(payload.get("records")):
        row = _object(row)
        if set(row) - {*_METRICS, "date", "model"}:
            raise CollectionError(_INVALID)
        record = _record(row.get("date"), row.get("model"), **_metrics(row))
        key = (record["date"], record["model"])
        if key in keys:
            raise CollectionError(
                "The usage JSON contains duplicate daily model records."
            )
        keys.add(key)
        records.append(record)
    quota = []
    for window in _array(payload.get("quota", []), 100):
        window = _object(window)
        if set(window) - {
            "label",
            "used_percent",
            "remaining",
            "limit",
            "resets_at",
            "unit",
        }:
            raise CollectionError(_INVALID)
        item = {
            "label": _text(window.get("label")),
            **{
                key: _number(window.get(key))
                for key in ("used_percent", "remaining", "limit")
            },
            "unit": None,
            "resets_at": None,
        }
        if window.get("unit") is not None:
            item["unit"] = _text(window["unit"], 50)
        if window.get("resets_at") is not None:
            stamp = _text(window["resets_at"], 50)
            try:
                if datetime.fromisoformat(stamp).tzinfo is None:
                    raise ValueError
            except ValueError:
                raise CollectionError(_INVALID) from None
            item["resets_at"] = stamp
        quota.append(item)
    capabilities = _array(payload.get("capabilities", []), 4)
    if any(
        not isinstance(item, str) or item not in _CAPABILITIES for item in capabilities
    ) or len(set(capabilities)) != len(capabilities):
        raise CollectionError(_INVALID)
    result = {
        "records": sorted(records, key=lambda row: (row["date"], row["model"])),
        "quota": quota,
        "capabilities": capabilities,
    }
    if "summary" in payload:
        summary = _object(payload["summary"])
        if summary.get("period") != "lifetime" or set(summary) - {
            *_METRICS,
            "period",
            "total_tokens",
        }:
            raise CollectionError(_INVALID)
        result["summary"] = {"period": "lifetime", **_metrics(summary)}
        if "total_tokens" in summary:
            result["summary"]["total_tokens"] = _number(
                summary["total_tokens"], integer=True
            )
        result["note"] = (
            "Lifetime summary is separate from daily records and date-filtered totals."
        )
    return result


async def collect(account: dict) -> dict:
    """Collect an account atomically, or raise only a display-safe error."""
    try:
        return await asyncio.wait_for(_collect(account), timeout=60)
    except CollectionError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise CollectionError("The usage service timed out. Try again later.") from None
    except httpx.HTTPError:
        raise CollectionError(
            "The usage service could not be reached securely."
        ) from None
    except Exception:  # noqa: BLE001 - public boundary must redact every upstream error
        # Never expose provider response bodies, headers, URLs, or credentials
        # through parser/network errors, even when an upstream changes schema.
        raise CollectionError(
            "Collection failed because the configuration or usage response is unsupported."
        ) from None


async def _collect(account):
    account = _object(account)
    provider = account.get("provider")
    metadata = next((item for item in PROVIDERS if item["id"] == provider), None)
    if metadata is None:
        raise CollectionError("This usage provider is not supported.")
    if provider == "chatgpt":
        raise CollectionError(
            "ChatGPT 로그인으로 계정을 연결하세요. API 키는 필요하지 않습니다."
        )
    origin = _origin(account.get("base_url") or metadata["default_url"])
    if provider in ("openai", "grok") and origin != metadata["default_url"]:
        raise CollectionError(
            "This provider uses a fixed official API origin; custom origins are not supported."
        )
    extra = _object(account.get("extra", {}))
    allowed = {field["name"] for field in metadata["fields"]}
    if set(extra) - allowed:
        raise CollectionError("The provider options contain unsupported fields.")
    credential = account.get("credential", "")
    if (
        not isinstance(credential, str)
        or len(credential) > 8192
        or any(ord(c) < 33 or ord(c) > 126 for c in credential)
    ):
        raise CollectionError("The credential is invalid; enter only the bearer token.")
    if not credential and provider != "usage-json":
        if provider == "claude-lb":
            raise CollectionError(
                "A Claude LB API key is required. Add the sk-clb key from your administrator."
            )
        raise CollectionError("A usage/admin credential is required for this provider.")
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    if provider == "claude-lb":
        headers["x-api-key"] = credential
        headers["anthropic-version"] = "2023-06-01"
    elif credential:
        headers["Authorization"] = "Bearer " + credential
    async with httpx.AsyncClient(
        transport=_PublicTransport(),
        headers=headers,
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(_TIMEOUT),
        limits=httpx.Limits(max_connections=2),
    ) as client:
        if provider == "openai":
            return await _openai(client, origin, extra)
        if provider == "grok":
            return await _grok(client, origin, extra)
        if provider in ("gemini", "codex-lb"):
            return await _gateway(client, origin, extra)
        if provider == "claude-lb":
            return await _claude(client, origin)
        return _usage_json(await _json(client, "GET", origin + "/usage.json"))
