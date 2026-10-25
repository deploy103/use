import { useEffect, useRef, useState } from "react";
import { ExternalLink, ShieldCheck, CheckCircle2, Clock3 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { api, message, trustedAuthorizationUrl } from "@/lib/api";
import type { OAuthProvider, OAuthSnapshot } from "@/lib/types";

const names = { gemini: "Gemini", chatgpt: "ChatGPT" };
const terminal = (status: OAuthSnapshot["status"]) =>
  ["connected", "error", "expired"].includes(status);
function expiry(snapshot: OAuthSnapshot) {
  const value = snapshot.expires_at;
  if (value == null) return Infinity;
  return typeof value === "number"
    ? value < 1e12
      ? value * 1000
      : value
    : new Date(value).getTime();
}

export function ProviderLogin({
  provider,
  close,
  connected,
}: {
  provider: OAuthProvider;
  close: () => void;
  connected: () => void;
}) {
  const [name, setName] = useState(`${names[provider]} 계정`);
  const [snapshot, setSnapshot] = useState<OAuthSnapshot | null>(null);
  const [callback, setCallback] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());
  const alive = useRef(true);
  const session = useRef<OAuthSnapshot | null>(null);
  const revision = useRef(0);
  const completing = useRef(false);
  const notified = useRef(false);
  const onConnected = useRef(connected);
  onConnected.current = connected;

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      revision.current++;
      const id = session.current?.id;
      session.current = null;
      if (id)
        void api(
          `/api/oauth/${encodeURIComponent(id)}`,
          undefined,
          "DELETE",
        ).catch(() => {});
    };
  }, []);

  function accept(next: OAuthSnapshot) {
    if (
      next.provider !== provider ||
      !next.id ||
      ![
        "awaiting_authorization",
        "authorizing",
        "connected",
        "error",
        "expired",
      ].includes(next.status) ||
      !["code", "url"].includes(next.callback_mode)
    )
      throw new Error("인증 세션 응답이 올바르지 않습니다. 다시 시작하세요.");
    session.current = next;
    setSnapshot(next);
    if (terminal(next.status)) setCallback("");
    if (next.status === "connected" && !notified.current) {
      notified.current = true;
      onConnected.current();
    }
  }

  function expire(current: OAuthSnapshot) {
    if (terminal(current.status)) return;
    revision.current++;
    accept({ ...current, status: "expired", authorization_url: null });
    setBusy(false);
    void api(
      `/api/oauth/${encodeURIComponent(current.id)}`,
      undefined,
      "DELETE",
    ).catch(() => {});
  }

  useEffect(() => {
    if (!snapshot || terminal(snapshot.status)) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      const current = session.current;
      if (!current || disposed) return;
      if (Date.now() >= expiry(current)) {
        expire(current);
        return;
      }
      if (!completing.current) {
        const version = revision.current;
        try {
          const next = await api<OAuthSnapshot>(
            `/api/oauth/${encodeURIComponent(current.id)}`,
            undefined,
            "GET",
            controller.signal,
          );
          if (
            !disposed &&
            alive.current &&
            version === revision.current &&
            session.current?.id === next.id
          ) {
            accept(next);
            setError("");
          }
        } catch (e) {
          if (!disposed && alive.current) setError(message(e));
        }
      }
      if (!disposed && session.current && !terminal(session.current.status))
        timer = setTimeout(poll, 1800);
    }
    timer = setTimeout(poll, 1000);
    const clock = setInterval(() => {
      setNow(Date.now());
      const current = session.current;
      if (current && Date.now() >= expiry(current)) expire(current);
    }, 1000);
    return () => {
      disposed = true;
      clearTimeout(timer);
      clearInterval(clock);
      controller.abort();
    };
    // Snapshot status restarts polling; session.current supplies the latest data.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot?.id, snapshot?.status]);

  async function start() {
    setBusy(true);
    setError("");
    try {
      const next = await api<OAuthSnapshot>("/api/oauth/start", {
        provider,
        name: name.trim(),
      });
      if (!alive.current) {
        if (next.id)
          void api(
            `/api/oauth/${encodeURIComponent(next.id)}`,
            undefined,
            "DELETE",
          ).catch(() => {});
        return;
      }
      accept(next);
    } catch (e) {
      if (alive.current) setError(message(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  async function complete() {
    if (!snapshot || snapshot.status !== "awaiting_authorization") return;
    if (Date.now() >= expiry(snapshot)) {
      expire(snapshot);
      return;
    }
    if (
      snapshot.callback_mode === "code" &&
      (callback.includes("#") || /https?:\/\//i.test(callback))
    ) {
      setError(
        "Google 인증 코드를 단독으로 붙여넣으세요. URL이나 #state를 넣지 마세요.",
      );
      return;
    }
    const version = ++revision.current;
    completing.current = true;
    setBusy(true);
    setError("");
    try {
      const next = await api<OAuthSnapshot>(
        `/api/oauth/${encodeURIComponent(snapshot.id)}/complete`,
        { callback_input: callback.trim() },
      );
      if (
        alive.current &&
        version === revision.current &&
        session.current?.id === next.id &&
        !terminal(session.current.status)
      ) {
        accept(next);
        setCallback("");
      }
    } catch (e) {
      if (alive.current && version === revision.current) setError(message(e));
    } finally {
      completing.current = false;
      if (alive.current) setBusy(false);
    }
  }
  const authUrl = trustedAuthorizationUrl(
    provider,
    snapshot?.authorization_url ?? null,
  );
  const seconds = snapshot
    ? Math.max(0, Math.ceil((expiry(snapshot) - now) / 1000))
    : 0;
  const expired =
    snapshot?.status === "expired" || (seconds === 0 && snapshot !== null);
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogContent>
        <DialogTitle>{names[provider]} 계정 연결</DialogTitle>
        <DialogDescription>
          서비스 제공자의 공식 인증으로 연결합니다. Use 관리자 로그인과
          별개이며, 제공자 비밀번호를 여기에 입력하지 마세요.
        </DialogDescription>
        <div className="notice">
          <ShieldCheck size={18} />
          <div>
            인증 정보는 소유자 서버에서만 보관합니다.
            <p className="muted mt-1">
              사용량·구독 한도 조회용 연결입니다. 추론 요청은 보내지 않습니다.
            </p>
          </div>
        </div>
        {!snapshot ? (
          <form
            className="form-stack"
            onSubmit={(e) => {
              e.preventDefault();
              void start();
            }}
          >
            <div className="field">
              <label htmlFor="oauth-name">계정 이름</label>
              <Input
                id="oauth-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={100}
                required
                disabled={busy}
              />
            </div>
            <Button disabled={busy || !name.trim()}>
              {busy ? "인증 세션 준비 중…" : "연결 시작"}
            </Button>
          </form>
        ) : snapshot.status === "connected" ? (
          <div className="space-y-4">
            <p className="flex items-center gap-2 success">
              <CheckCircle2 size={20} />
              계정이 연결되었습니다.
            </p>
            <p className="text-xs muted">
              제공되는 한도와 사용량을 수집합니다. 날짜별 기록을 제공하지 않는
              구독도 있습니다.
            </p>
            <Button onClick={close}>완료</Button>
          </div>
        ) : expired ? (
          <div className="error" role="alert">
            인증 세션이 만료되었습니다. 창을 닫고 다시 연결하세요.
          </div>
        ) : snapshot.status === "error" ? (
          <div className="error" role="alert">
            {snapshot.error || "제공자 인증에 실패했습니다."} 창을 닫고 다시
            연결하세요.
          </div>
        ) : (
          <div className="form-stack">
            <div className="flex items-center justify-between text-xs muted">
              <span className="inline-flex gap-1 items-center">
                <Clock3 size={13} />
                {Number.isFinite(seconds)
                  ? `${Math.floor(seconds / 60)}분 ${seconds % 60}초 남음`
                  : "인증 세션 대기 중"}
              </span>
              <span>
                {snapshot.status === "authorizing"
                  ? "서버에서 인증 확인 중"
                  : "공식 인증 대기"}
              </span>
            </div>
            {snapshot.status === "awaiting_authorization" && (
              <>
                {authUrl ? (
                  <Button asChild>
                    <a
                      href={authUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      referrerPolicy="no-referrer"
                    >
                      <ExternalLink />
                      공식 인증 페이지 열기
                    </a>
                  </Button>
                ) : (
                  <p className="error">
                    허용된 공식 HTTPS 인증 주소가 아니므로 열 수 없습니다. 창을
                    닫고 다시 시도하세요.
                  </p>
                )}
                <form
                  className="form-stack"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void complete();
                  }}
                >
                  <div className="field">
                    <label htmlFor="oauth-callback">
                      {snapshot.callback_mode === "code"
                        ? "인증 코드"
                        : "인증 후 돌아온 전체 주소"}
                    </label>
                    <Input
                      id="oauth-callback"
                      value={callback}
                      onChange={(e) => setCallback(e.target.value)}
                      maxLength={8192}
                      autoComplete="off"
                      spellCheck={false}
                      required
                      disabled={busy}
                      placeholder={
                        snapshot.callback_mode === "code"
                          ? "Google 인증 코드만 입력"
                          : "http://localhost:…"
                      }
                    />
                    <p className="form-hint">
                      {snapshot.callback_mode === "code"
                        ? "Google CLI 인증 코드를 단독으로 붙여넣으세요. URL이나 #state는 입력하지 마세요."
                        : "localhost 연결 오류 화면이 나와도 주소창의 전체 URL을 복사하세요."}
                    </p>
                    {snapshot.callback_url && (
                      <p className="form-hint break-all">
                        콜백 대상: {snapshot.callback_url}
                      </p>
                    )}
                  </div>
                  <Button disabled={busy || !callback.trim() || !authUrl}>
                    {busy ? "확인 중…" : "인증 완료"}
                  </Button>
                </form>
              </>
            )}
            {snapshot.status === "authorizing" && (
              <p role="status" className="text-xs muted">
                서버가 인증을 확인하고 있습니다. 실패 시 오류가 표시되며, 세션
                만료 후 다시 시도할 수 있습니다.
              </p>
            )}
          </div>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <Button variant="ghost" onClick={close}>
          취소 · 닫기
        </Button>
      </DialogContent>
    </Dialog>
  );
}
