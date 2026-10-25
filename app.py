"""Single-owner, read-only-upstream AI usage dashboard."""

import asyncio
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import chatgpt
import collectors

METRICS = (
    "requests",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_usd",
)
KST = ZoneInfo("Asia/Seoul")
STATIC = Path(__file__).parent / "static"


def utcnow():
    return datetime.now(ZoneInfo("UTC")).isoformat()


def private_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value)


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.scrypt(
        password.encode(), salt=salt.encode(), n=16384, r=8, p=1
    ).hex()
    return f"{salt}:{value}"


def sums(rows):
    result = {}
    for key in METRICS:
        values = [r[key] for r in rows if r.get(key) is not None]
        result[key] = sum(values) if values else None
    tokens = [result[k] for k in METRICS[1:5] if result[k] is not None]
    result["total_tokens"] = sum(tokens) if tokens else None
    result["partial"] = any(r.get(k) is None for r in rows for k in METRICS[1:5])
    return result


def create_app(data_dir=None, collector=None, poll_seconds=300):
    root = Path(data_dir or os.environ.get("ATLAS_DATA_DIR", "data")).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    public_origin = os.environ.get("ATLAS_PUBLIC_ORIGIN", "").rstrip("/")
    if public_origin:
        parsed = urlsplit(public_origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 443)
        ):
            raise ValueError("ATLAS_PUBLIC_ORIGIN must be an HTTPS origin")
    key_file = Path(os.environ.get("ATLAS_KEY_FILE", str(root / "encryption.key")))
    password_file = Path(
        os.environ.get("ATLAS_PASSWORD_FILE", str(root / "owner.password"))
    )
    setup_file = root / "setup.token"
    if not key_file.exists():
        private_file(key_file, Fernet.generate_key().decode())
    cipher = Fernet(key_file.read_bytes().strip())
    if not password_file.exists() and not setup_file.exists():
        private_file(setup_file, secrets.token_urlsafe(32))

    @contextmanager
    def db():
        conn = sqlite3.connect(root / "atlas.sqlite", timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    with db() as c:
        c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS accounts (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, provider TEXT NOT NULL,
          base_url TEXT NOT NULL, secret TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL DEFAULT 'pending', error TEXT, last_attempt_at TEXT,
          last_success_at TEXT, quota TEXT NOT NULL DEFAULT '[]',
          capabilities TEXT NOT NULL DEFAULT '[]', summary TEXT, note TEXT);
        CREATE TABLE IF NOT EXISTS records (
          account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
          date TEXT NOT NULL, model TEXT NOT NULL, requests INTEGER,
          input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
          cache_write_tokens INTEGER, cost_usd REAL,
          PRIMARY KEY(account_id,date,model));
        CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, expires REAL NOT NULL);
        """)
    os.chmod(root / "atlas.sqlite", 0o600)
    active = set()
    tasks = set()
    attempts = {}
    collect = collector or collectors.collect

    async def chatgpt_connected(account_id, name):
        encrypted = cipher.encrypt(
            json.dumps({"credential": "", "extra": {}}).encode()
        ).decode()
        with db() as c:
            if c.execute(
                "SELECT 1 FROM accounts WHERE id=? AND provider='chatgpt'",
                (account_id,),
            ).fetchone():
                return
            if c.execute("SELECT count(*) FROM accounts").fetchone()[0] >= 50:
                raise collectors.CollectionError(
                    "최대 50개 계정까지 연결할 수 있습니다."
                )
            c.execute(
                "INSERT INTO accounts(id,name,provider,base_url,secret) VALUES (?,?,?,?,?)",
                (account_id, name, "chatgpt", "https://chatgpt.com", encrypted),
            )
        start_refresh([account_id])

    chatgpt_accounts = chatgpt.ChatGPTManager(root, cipher, chatgpt_connected)

    def store_result(account_id, result):
        records = result.get("records", [])
        if not isinstance(records, list) or len(records) > 100000:
            raise ValueError("invalid records")
        clean = []
        dates = {
            datetime.strptime(d, "%Y-%m-%d").date().isoformat()
            for d in result.get("dates", [])
        }
        for row in records:
            date = datetime.strptime(row["date"], "%Y-%m-%d").date().isoformat()
            model = row["model"]
            if (
                not isinstance(model, str)
                or not 1 <= len(model) <= 200
                or (dates and date not in dates)
            ):
                raise ValueError("invalid model/date")
            values = []
            for k in METRICS:
                value = row.get(k)
                lower = -1e18 if k == "cost_usd" else 0
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not lower <= value <= 1e18
                ):
                    raise ValueError("invalid metric")
                if value is not None and k != "cost_usd" and int(value) != value:
                    raise ValueError("invalid token count")
                values.append(value)
            clean.append((account_id, date, model, *values))
        with db() as c:
            if not c.execute(
                "SELECT 1 FROM accounts WHERE id=?", (account_id,)
            ).fetchone():
                return
            for date in dates:
                c.execute(
                    "DELETE FROM records WHERE account_id=? AND date=?",
                    (account_id, date),
                )
            c.executemany(
                """INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?)
              ON CONFLICT(account_id,date,model) DO UPDATE SET
              requests=excluded.requests,input_tokens=excluded.input_tokens,
              output_tokens=excluded.output_tokens,cache_read_tokens=excluded.cache_read_tokens,
              cache_write_tokens=excluded.cache_write_tokens,cost_usd=excluded.cost_usd""",
                clean,
            )
            c.execute(
                """UPDATE accounts SET status='connected',error=NULL,last_success_at=?,
              quota=?,capabilities=?,summary=?,note=? WHERE id=?""",
                (
                    utcnow(),
                    json.dumps(result.get("quota", [])),
                    json.dumps(result.get("capabilities", [])),
                    json.dumps(result.get("summary")),
                    result.get("note"),
                    account_id,
                ),
            )

    async def refresh_one(account_id):
        if account_id in active:
            return
        active.add(account_id)
        try:
            with db() as c:
                raw = c.execute(
                    "SELECT * FROM accounts WHERE id=? AND enabled=1 AND provider!='amp'",
                    (account_id,),
                ).fetchone()
                if not raw:
                    return
                account = dict(raw)
                account.update(
                    json.loads(cipher.decrypt(account.pop("secret").encode()))
                )
                c.execute(
                    "UPDATE accounts SET last_attempt_at=? WHERE id=?",
                    (utcnow(), account_id),
                )
            if account["provider"] not in ("chatgpt", "usage-json") and not account.get(
                "credential"
            ):
                with db() as c:
                    c.execute(
                        "UPDATE accounts SET status='needs_auth',error=? WHERE id=?",
                        ("조회 키를 연결하면 한도를 확인할 수 있습니다.", account_id),
                    )
                return
            try:
                operation = (
                    chatgpt_accounts.collect(account_id)
                    if account["provider"] == "chatgpt"
                    else collect(account)
                )
                result = await asyncio.wait_for(operation, timeout=120)
                store_result(account_id, result)
            except Exception as exc:  # noqa: BLE001 -- isolate background provider failures without leaking credentials
                message = (
                    str(exc)[:300]
                    if isinstance(exc, collectors.CollectionError)
                    else "수집 실패: 연결 또는 응답 형식을 확인하세요. 기존 데이터는 보존했습니다."
                )
                with db() as c:
                    c.execute(
                        "UPDATE accounts SET status='error',error=? WHERE id=?",
                        (message, account_id),
                    )
        finally:
            active.discard(account_id)

    def start_refresh(ids):
        for account_id in ids:
            task = asyncio.create_task(refresh_one(account_id))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    async def periodic():
        while True:
            with db() as c:
                ids = [
                    r[0]
                    for r in c.execute(
                        "SELECT id FROM accounts WHERE enabled=1 AND provider!='amp'"
                    )
                ]
            start_refresh(ids)
            await asyncio.sleep(poll_seconds)

    @asynccontextmanager
    async def lifespan(app):
        timer = asyncio.create_task(periodic()) if poll_seconds else None
        yield
        if timer:
            timer.cancel()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(
            *([timer] if timer else []), *tasks, return_exceptions=True
        )
        await chatgpt_accounts.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.root = root
    app.state.db = db
    app.state.cipher = cipher
    app.state.refresh_one = refresh_one
    app.state.chatgpt = chatgpt_accounts

    def authenticated(request):
        token = request.cookies.get("atlas_session", "")
        if not token:
            return False
        with db() as c:
            row = c.execute(
                "SELECT expires FROM sessions WHERE hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
        return bool(row and row[0] > time.time())

    @app.middleware("http")
    async def boundary(request, call_next):
        if (
            public_origin
            and request.url.path != "/healthz"
            and request.headers.get("host") != urlsplit(public_origin).netloc
        ):
            return JSONResponse(
                {"detail": "허용되지 않은 호스트입니다."}, status_code=400
            )
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            expected = (
                public_origin or f"{request.url.scheme}://{request.headers.get('host')}"
            )
            if request.headers.get("origin") != expected:
                return JSONResponse(
                    {"detail": "동일한 사이트에서만 요청할 수 있습니다."},
                    status_code=403,
                )
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 65536:
                    return JSONResponse(
                        {"detail": "요청이 너무 큽니다."}, status_code=413
                    )
                chunks.append(chunk)
            request._body = b"".join(chunks)
        if (
            request.url.path.startswith("/api/")
            and request.url.path not in ("/api/session", "/api/login", "/api/setup")
        ) and not authenticated(request):
            return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    def login_response():
        token = secrets.token_urlsafe(32)
        with db() as c:
            c.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            c.execute(
                "INSERT INTO sessions VALUES (?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), time.time() + 604800),
            )
        response = JSONResponse({"authenticated": True})
        response.set_cookie(
            "atlas_session",
            token,
            httponly=True,
            samesite="strict",
            max_age=604800,
            secure=bool(public_origin)
            or os.environ.get("ATLAS_COOKIE_SECURE", "false").lower() == "true",
        )
        return response

    def rate_limit(request):
        ip = request.client.host
        now = time.monotonic()
        attempts[ip] = [t for t in attempts.get(ip, []) if t > now - 300]
        if len(attempts[ip]) >= 10:
            raise HTTPException(429, "로그인 시도가 많습니다. 5분 후 다시 시도하세요.")
        attempts[ip].append(now)
        if len(attempts) > 1000:
            for key in list(attempts):
                if not attempts[key] or attempts[key][-1] < now - 300:
                    attempts.pop(key, None)

    async def body(request):
        try:
            value = await request.json()
        except (ValueError, UnicodeError):
            raise HTTPException(400, "올바른 JSON을 입력하세요.")
        if not isinstance(value, dict):
            raise HTTPException(400, "JSON 객체가 필요합니다.")
        return value

    @app.get("/api/session")
    def session(request: Request):
        return {
            "authenticated": authenticated(request),
            "setup_required": not password_file.exists(),
        }

    @app.post("/api/setup")
    async def setup(request: Request):
        rate_limit(request)
        data = await body(request)
        if (
            password_file.exists()
            or not setup_file.exists()
            or not hmac.compare_digest(
                str(data.get("token", "")), setup_file.read_text().strip()
            )
        ):
            raise HTTPException(403, "설정 링크가 유효하지 않거나 이미 사용되었습니다.")
        password = data.get("password", "")
        if not isinstance(password, str) or not 12 <= len(password) <= 200:
            raise HTTPException(400, "비밀번호를 12~200자로 입력하세요.")
        private_file(password_file, password_hash(password))
        setup_file.unlink()
        return login_response()

    @app.post("/api/login")
    async def login(request: Request):
        rate_limit(request)
        data = await body(request)
        password = data.get("password", "")
        if (
            not password_file.exists()
            or not isinstance(password, str)
            or len(password) > 200
        ):
            raise HTTPException(401, "비밀번호를 확인하세요.")
        stored = password_file.read_text().strip()
        if not hmac.compare_digest(
            password_hash(password, stored.split(":")[0]), stored
        ):
            raise HTTPException(401, "비밀번호를 확인하세요.")
        return login_response()

    @app.post("/api/logout")
    def logout(request: Request):
        with db() as c:
            c.execute(
                "DELETE FROM sessions WHERE hash=?",
                (
                    hashlib.sha256(
                        request.cookies.get("atlas_session", "").encode()
                    ).hexdigest(),
                ),
            )
        response = JSONResponse({"authenticated": False})
        response.delete_cookie("atlas_session")
        return response

    @app.get("/api/providers")
    def providers():
        return {"providers": collectors.PROVIDERS}

    @app.post("/api/chatgpt/login")
    async def chatgpt_login(request: Request):
        data = await body(request)
        name = data.get("name", "ChatGPT 계정")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise HTTPException(400, "계정 이름을 1~100자로 입력하세요.")
        with db() as c:
            if c.execute("SELECT count(*) FROM accounts").fetchone()[0] >= 50:
                raise HTTPException(400, "최대 50개 계정까지 연결할 수 있습니다.")
        try:
            return await chatgpt_accounts.start(name.strip())
        except collectors.CollectionError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/chatgpt/login/{login_id}")
    async def chatgpt_login_status(login_id: str):
        try:
            return await chatgpt_accounts.status(login_id)
        except collectors.CollectionError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.delete("/api/chatgpt/login/{login_id}")
    async def chatgpt_login_cancel(login_id: str):
        try:
            await chatgpt_accounts.cancel(login_id)
        except collectors.CollectionError as exc:
            raise HTTPException(404, str(exc)) from None
        return {"cancelled": True}

    @app.post("/api/chatgpt/login/{login_id}/complete")
    async def chatgpt_login_complete(login_id: str, request: Request):
        data = await body(request)
        callback_url = data.get("callback_url")
        if (
            set(data) != {"callback_url"}
            or not isinstance(callback_url, str)
            or not 1 <= len(callback_url) <= 8192
        ):
            raise HTTPException(400, "인증 후 돌아온 주소를 입력하세요.")
        try:
            return await chatgpt_accounts.complete(login_id, callback_url)
        except collectors.CollectionError as exc:
            raise HTTPException(400, str(exc)) from None

    def public_account(row):
        a = dict(row)
        secret = json.loads(cipher.decrypt(a.pop("secret").encode()))
        a["has_credentials"] = (
            chatgpt_accounts.has_credentials(a["id"])
            if a["provider"] == "chatgpt"
            else bool(secret.get("credential"))
        )
        a["enabled"] = bool(a["enabled"])
        for k in ("quota", "capabilities", "summary"):
            a[k] = json.loads(a[k]) if a[k] else None
        a["refreshing"] = a["id"] in active
        return a

    async def save_account(request, account_id=None):
        data = await body(request)
        with db() as c:
            old = (
                c.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
                if account_id
                else None
            )
            if account_id and not old:
                raise HTTPException(404, "계정을 찾을 수 없습니다.")
            provider = data.get("provider", old["provider"] if old else "")
            if provider not in {p["id"] for p in collectors.PROVIDERS}:
                raise HTTPException(400, "지원하지 않는 서비스입니다.")
            if provider == "chatgpt" and (not old or set(data) - {"name", "enabled"}):
                raise HTTPException(
                    400, "ChatGPT는 API 키 대신 ChatGPT 로그인 버튼으로 연결하세요."
                )
            name = data.get("name", old["name"] if old else "")
            url = data.get("base_url", old["base_url"] if old else "")
            if (
                not isinstance(name, str)
                or not 1 <= len(name.strip()) <= 100
                or not isinstance(url, str)
                or len(url) > 500
            ):
                raise HTTPException(400, "계정 이름과 서버 주소를 확인하세요.")
            secret = (
                json.loads(cipher.decrypt(old["secret"].encode()))
                if old
                else {"credential": "", "extra": {}}
            )
            credential = data.get("credential", "")
            if not isinstance(credential, str) or len(credential) > 16000:
                raise HTTPException(400, "인증 정보를 확인하세요.")
            if credential.strip():
                secret["credential"] = credential.strip()
            if "extra" in data:
                if (
                    not isinstance(data["extra"], dict)
                    or len(json.dumps(data["extra"])) > 16000
                ):
                    raise HTTPException(400, "추가 설정은 JSON 객체여야 합니다.")
                secret["extra"] = data["extra"]
            if old and provider != old["provider"]:
                raise HTTPException(
                    400, "서비스 종류를 변경하려면 새 계정을 추가하세요."
                )
            encrypted = cipher.encrypt(json.dumps(secret).encode()).decode()
            enabled = data.get("enabled", bool(old["enabled"]) if old else True)
            if not isinstance(enabled, bool):
                raise HTTPException(400, "활성화 값은 true/false여야 합니다.")
            if old:
                if account_id in active:
                    raise HTTPException(409, "수집 중입니다. 잠시 후 수정하세요.")
                c.execute(
                    "UPDATE accounts SET name=?,base_url=?,secret=?,enabled=? WHERE id=?",
                    (name.strip(), url.strip(), encrypted, int(enabled), account_id),
                )
            else:
                if c.execute("SELECT count(*) FROM accounts").fetchone()[0] >= 50:
                    raise HTTPException(400, "최대 50개 계정까지 연결할 수 있습니다.")
                account_id = secrets.token_hex(12)
                c.execute(
                    "INSERT INTO accounts(id,name,provider,base_url,secret,enabled) VALUES (?,?,?,?,?,?)",
                    (
                        account_id,
                        name.strip(),
                        provider,
                        url.strip(),
                        encrypted,
                        int(enabled),
                    ),
                )
            row = c.execute(
                "SELECT * FROM accounts WHERE id=?", (account_id,)
            ).fetchone()
        start_refresh([account_id])
        return public_account(row)

    @app.post("/api/accounts")
    async def add(request: Request):
        return await save_account(request)

    @app.patch("/api/accounts/{account_id}")
    async def edit(account_id: str, request: Request):
        return await save_account(request, account_id)

    @app.delete("/api/accounts/{account_id}")
    async def delete(account_id: str):
        with db() as c:
            row = c.execute(
                "SELECT provider FROM accounts WHERE id=? AND provider!='amp'",
                (account_id,),
            ).fetchone()
            if not row:
                raise HTTPException(404, "계정을 찾을 수 없습니다.")
            if row["provider"] == "chatgpt" and account_id in active:
                raise HTTPException(409, "수집 중입니다. 잠시 후 삭제하세요.")
        if row["provider"] == "chatgpt":
            try:
                await chatgpt_accounts.remove(account_id)
            except collectors.CollectionError as exc:
                raise HTTPException(409, str(exc)) from None
        with db() as c:
            c.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        return {"deleted": True}

    @app.post("/api/refresh")
    async def refresh(request: Request):
        data = await body(request)
        with db() as c:
            ids = [
                r[0]
                for r in c.execute(
                    "SELECT id FROM accounts WHERE enabled=1 AND provider!='amp'"
                )
            ]
        if data.get("account_id"):
            if data["account_id"] not in ids:
                raise HTTPException(404, "활성 계정을 찾을 수 없습니다.")
            ids = [data["account_id"]]
        start_refresh(ids)
        return {"started": True}

    def select_rows(range, account, provider):
        today = datetime.now(KST).date()
        days = {"today": 1, "7d": 7, "30d": 30, "all": None}
        if range not in days:
            raise HTTPException(400, "조회 기간을 확인하세요.")
        start = (
            (today - timedelta(days=days[range] - 1)).isoformat()
            if days[range]
            else "0001-01-01"
        )
        with db() as c:
            rows = [
                dict(r)
                for r in c.execute(
                    """SELECT r.*,a.name AS account_name,a.provider FROM records r
              JOIN accounts a ON a.id=r.account_id WHERE r.date>=? AND r.date<=?
              AND a.provider!='amp' AND (?='all' OR a.id=?) AND (?='all' OR a.provider=?) ORDER BY r.date,r.model""",
                    (start, today.isoformat(), account, account, provider, provider),
                )
            ]
        return start, today.isoformat(), rows

    @app.get("/api/dashboard")
    def dashboard(range: str = "7d", account: str = "all", provider: str = "all"):
        start, end, rows = select_rows(range, account, provider)
        with db() as c:
            accounts = [
                public_account(r)
                for r in c.execute(
                    "SELECT * FROM accounts WHERE provider!='amp' ORDER BY name,id"
                )
            ]
        day_groups, model_groups = {}, {}
        for row in rows:
            day_groups.setdefault(row["date"], []).append(row)
            model_groups.setdefault((row["account_id"], row["model"]), []).append(row)
        models = []
        for group in model_groups.values():
            row = group[0]
            models.append(
                {k: row[k] for k in ("account_id", "account_name", "model", "provider")}
                | sums(group)
            )
        models.sort(key=lambda r: r["total_tokens"] or 0, reverse=True)
        successes = [a["last_success_at"] for a in accounts if a["last_success_at"]]
        return {
            "range": range,
            "from": start,
            "to": end,
            "timezone": "Asia/Seoul",
            "refreshed_at": max(successes) if successes else None,
            "refreshing": bool(
                active or tasks or any(a["refreshing"] for a in accounts)
            ),
            "totals": sums(rows),
            "daily": [{"date": d, **sums(g)} for d, g in sorted(day_groups.items())],
            "models": models,
            "sources": [],
            "accounts": accounts,
            "providers": collectors.PROVIDERS,
        }

    @app.get("/api/export")
    def export(range: str = "7d", account: str = "all", provider: str = "all"):
        _, _, rows = select_rows(range, account, provider)
        output = io.StringIO()
        keys = ("date", "provider", "account_name", "model", *METRICS)
        writer = csv.writer(output)
        writer.writerow(keys)
        for row in rows:
            # Spreadsheet formula injection is possible even in provider model names.
            writer.writerow(
                [
                    (
                        "'" + row[k]
                        if isinstance(row[k], str)
                        and row[k].lstrip().startswith(("=", "+", "-", "@"))
                        else row[k]
                    )
                    for k in keys
                ]
            )
        return Response(
            "\ufeff" + output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="use-usage.csv"'},
        )

    @app.get("/healthz")
    def health():
        with db() as c:
            c.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
