import { useEffect, useRef, useState } from "react";
import { ShieldCheck, Info } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, message } from "@/lib/api";
import { McpSettings } from "@/components/mcp-settings";

export function Settings({
  dark,
  toggleTheme,
}: {
  dark: boolean;
  toggleTheme: () => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  async function change() {
    setError("");
    setSuccess(false);
    if (next !== confirm) {
      setError("새 비밀번호가 일치하지 않습니다.");
      return;
    }
    setBusy(true);
    try {
      await api("/api/settings/password", {
        current_password: current,
        new_password: next,
      });
      if (alive.current) {
        setCurrent("");
        setNext("");
        setConfirm("");
        setSuccess(true);
      }
    } catch (e) {
      if (alive.current) setError(message(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <section className="panel">
        <div className="panel-heading">
          <h2>Owner security</h2>
          <ShieldCheck size={17} className="muted" />
        </div>
        <div className="panel-body space-y-5">
          <p className="text-xs muted">
            AI 대시보드 소유자 관리 비밀번호입니다. Claude·Gemini·ChatGPT의 비밀번호와는
            별개입니다. 변경하면 이전 세션을 폐기하고 현재 브라우저에 새 세션
            쿠키를 발급합니다.
          </p>
          <form
            className="form-stack"
            onSubmit={(e) => {
              e.preventDefault();
              void change();
            }}
          >
            <div className="field">
              <label htmlFor="current-password">현재 관리자 비밀번호</label>
              <Input
                id="current-password"
                type="password"
                autoComplete="current-password"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
                disabled={busy}
                required
                maxLength={200}
              />
            </div>
            <div className="field">
              <label htmlFor="new-password">새 관리자 비밀번호</label>
              <Input
                id="new-password"
                type="password"
                autoComplete="new-password"
                minLength={12}
                maxLength={200}
                value={next}
                onChange={(e) => setNext(e.target.value)}
                disabled={busy}
                required
              />
              <p className="form-hint">12~200자</p>
            </div>
            <div className="field">
              <label htmlFor="confirm-password">새 비밀번호 확인</label>
              <Input
                id="confirm-password"
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                disabled={busy}
                required
              />
            </div>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            {success && (
              <p className="text-xs success" role="status">
                비밀번호를 변경했습니다. 이전 세션은 만료되었습니다.
              </p>
            )}
            <Button disabled={busy}>
              {busy ? "변경 중…" : "관리자 비밀번호 변경"}
            </Button>
          </form>
        </div>
      </section>
      <div className="space-y-5">
        <section className="panel">
          <div className="panel-heading">
            <h2>Appearance</h2>
          </div>
          <div className="panel-body flex items-center justify-between gap-3">
            <div>
              <p className="text-sm">{dark ? "Dark" : "Light"} theme</p>
              <p className="text-xs muted">
                테마 선택만 이 브라우저에 저장합니다.
              </p>
            </div>
            <Button variant="outline" onClick={toggleTheme}>
              테마 전환
            </Button>
          </div>
        </section>
        <div className="notice">
          <Info size={18} />
          <div>
            <p className="font-medium">측정값을 있는 그대로</p>
            <p className="muted mt-2">
              구독 한도는 토큰 기록이 아닙니다. 기간 집계는 날짜별 기록만
              포함하며, 누적 카운터와 한도는 별도 표시됩니다. 제공되지 않은
              수치는 0 대신 —로 표시합니다.
            </p>
            <p className="muted mt-2">
              인증 쿠키는 서버가 관리합니다. API 키·인증 코드·설정 토큰은
              브라우저 저장소에 저장하지 않습니다.
            </p>
          </div>
        </div>
      </div>
      <McpSettings />
    </div>
  );
}
