import { useEffect, useRef, useState } from "react";
import { KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { api, message, timestamp } from "@/lib/api";

type McpKey = { id: string; name: string; created_at: string };

export function McpSettings() {
  const [keys, setKeys] = useState<McpKey[] | null>(null);
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // The only copy of a newly issued secret is component state. It is never
  // merged into key metadata, persistent examples, URLs or browser storage.
  const [token, setToken] = useState("");
  const [deleting, setDeleting] = useState<McpKey | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const [copied, setCopied] = useState("");
  const [copyError, setCopyError] = useState("");
  const alive = useRef(true);
  const controller = useRef<AbortController | null>(null);
  const endpoint = `${window.location.origin}/mcp`;
  const config = JSON.stringify(
    {
      mcpServers: {
        "ai-dashboard": {
          type: "http",
          url: endpoint,
          headers: { Authorization: "Bearer <MCP key>" },
        },
      },
    },
    null,
    2,
  );

  async function load(signal?: AbortSignal) {
    setLoading(true);
    setError("");
    try {
      const result = await api<{ keys: McpKey[] }>(
        "/api/mcp/keys",
        undefined,
        "GET",
        signal,
      );
      if (alive.current) {
        // Keep only public metadata, even if a server sends extra properties.
        setKeys(
          result.keys.map(({ id, name, created_at }) => ({
            id,
            name,
            created_at,
          })),
        );
      }
    } catch (e) {
      if (alive.current) setError(message(e));
    } finally {
      if (alive.current) setLoading(false);
    }
  }

  useEffect(() => {
    alive.current = true;
    const abort = new AbortController();
    controller.current = abort;
    void load(abort.signal);
    return () => {
      alive.current = false;
      abort.abort();
      // Unmount discards token state; late responses cannot restore it.
    };
  }, []);

  async function create() {
    const trimmed = name.trim();
    if (!trimmed || trimmed.length > 100 || !keys || keys.length >= 20) return;
    setBusy(true);
    setError("");
    setCopied("");
    setCopyError("");
    try {
      const result = await api<McpKey & { token: string }>(
        "/api/mcp/keys",
        { name: trimmed },
        "POST",
        controller.current?.signal,
      );
      if (!alive.current) return;
      const { id, name: issuedName, created_at } = result;
      setKeys((previous) => [
        ...(previous ?? []),
        { id, name: issuedName, created_at },
      ]);
      setName("");
      setToken(result.token);
    } catch (e) {
      if (alive.current) setError(message(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  async function remove() {
    if (!deleting) return;
    const id = deleting.id;
    setBusy(true);
    setDeleteError("");
    try {
      await api(
        `/api/mcp/keys/${encodeURIComponent(id)}`,
        undefined,
        "DELETE",
        controller.current?.signal,
      );
      if (!alive.current) return;
      setKeys((previous) => previous?.filter((key) => key.id !== id) ?? []);
      setDeleting(null);
    } catch (e) {
      if (alive.current) setDeleteError(message(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  async function copy(value: string, label: string) {
    setCopied("");
    setCopyError("");
    try {
      await navigator.clipboard.writeText(value);
      if (alive.current) setCopied(label);
    } catch {
      if (alive.current)
        setCopyError("복사하지 못했습니다. 텍스트를 직접 선택해 복사하세요.");
    }
  }

  function dismissToken() {
    setToken("");
    setCopied("");
    setCopyError("");
  }

  return (
    <section
      className="panel min-w-0 xl:col-span-2"
      aria-labelledby="mcp-heading"
    >
      <div className="panel-heading">
        <h2 id="mcp-heading">MCP · 읽기 전용 공유</h2>
        <KeyRound size={17} className="muted" />
      </div>
      <div className="panel-body space-y-5">
        <div className="notice">
          <KeyRound size={18} className="shrink-0" />
          <div className="space-y-2">
            <p className="font-medium">신뢰하는 사람에게만 키를 발급하세요</p>
            <p>
              각 키는 이 설치의 모든 계정 이름·통계·한도를 읽을 수 있습니다.
              계정별 권한 분리나 다중 사용자 격리 기능이 아닙니다.
            </p>
            <p className="muted">
              소유자가 신뢰하는 수신자에게만 전달하세요. MCP에는 제공자 인증
              정보 조회나 계정 변경·갱신 등 쓰기 작업이 노출되지 않습니다.
              관리자 비밀번호 대신 별도의 MCP 키를 사용합니다.
            </p>
            <p className="muted">
              MCP는 저장된 데이터를 읽습니다. 마지막 수집 시각을 확인하세요.
              키는 자동 만료되지 않으며 관리자 비밀번호 변경 후에도 유지됩니다.
              접근을 중단하려면 아래 목록에서 해당 키를 삭제하세요.
            </p>
          </div>
        </div>

        <div className="grid min-w-0 gap-5 lg:grid-cols-2">
          <div className="min-w-0 space-y-4">
            <h3 className="text-sm font-semibold">연결 안내</h3>
            <p className="text-xs muted">
              전송 방식: Streamable HTTP · MCP를 지원하는 클라이언트에서
              연결하세요.
            </p>
            <div className="field">
              <label htmlFor="mcp-endpoint">MCP 엔드포인트</label>
              <Input id="mcp-endpoint" readOnly value={endpoint} />
              <Button
                variant="outline"
                onClick={() => void copy(endpoint, "엔드포인트")}
              >
                엔드포인트 복사
              </Button>
            </div>
            <p className="text-xs break-words">
              <code>Authorization: Bearer &lt;MCP key&gt;</code>
            </p>
            <details>
              <summary className="cursor-pointer text-xs">
                클라이언트 JSON 설정 예시
              </summary>
              <p className="form-hint my-3">
                HTTP 원격 서버 설정을 지원하는 클라이언트용 예시입니다.
                클라이언트별 형식은 다를 수 있습니다. &lt;MCP key&gt;를 발급받은
                키로 바꾸세요.
              </p>
              <pre
                className="max-w-full overflow-x-auto rounded-md border bg-muted p-3 text-xs"
                aria-label="MCP 클라이언트 설정"
              >
                {config}
              </pre>
              <Button
                className="mt-3"
                variant="outline"
                onClick={() => void copy(config, "설정 예시")}
              >
                JSON 설정 복사
              </Button>
            </details>
            <div className="text-xs muted space-y-2 break-words">
              <p className="font-medium text-foreground">사용 가능한 도구</p>
              <p>
                <code>usage_summary(range, account, provider)</code> — range:
                today / 7d / 30d / all (기본 7d), account·provider 기본 all
              </p>
              <p>
                <code>list_accounts()</code> — 계정 목록
              </p>
              <p>
                <code>account_quota(account_id)</code> — 계정별 저장된 한도
              </p>
            </div>
            {!token && copied && (
              <p role="status" className="text-xs success">
                {copied} 복사 완료
              </p>
            )}
            {!token && copyError && (
              <p role="alert" className="error">
                {copyError}
              </p>
            )}
          </div>

          <div className="min-w-0 space-y-4">
            <h3 className="text-sm font-semibold">
              발급한 키 {keys ? `(${keys.length}/20)` : ""}
            </h3>
            <p className="text-xs muted">
              수신자를 구분할 이름을 지정하세요 (1~100자, 최대 20개). 키 원문은
              발급 직후 한 번만 표시됩니다.
            </p>
            <form
              className="form-stack"
              onSubmit={(e) => {
                e.preventDefault();
                void create();
              }}
            >
              <div className="field">
                <label htmlFor="mcp-key-name">MCP 키 이름</label>
                <Input
                  id="mcp-key-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={100}
                  required
                  autoComplete="off"
                  disabled={
                    busy || loading || keys === null || keys.length >= 20
                  }
                  placeholder="예: 신뢰하는 팀원 · 데스크톱"
                />
              </div>
              <Button
                disabled={
                  busy ||
                  loading ||
                  keys === null ||
                  keys.length >= 20 ||
                  !name.trim()
                }
              >
                {busy ? "처리 중…" : "MCP 키 발급"}
              </Button>
            </form>
            {loading && (
              <p role="status" className="text-xs muted">
                키 목록 불러오는 중…
              </p>
            )}
            {error && (
              <div role="alert" className="error space-y-2">
                <p>{error}</p>
                {keys === null && (
                  <Button
                    variant="outline"
                    disabled={loading}
                    onClick={() => void load(controller.current?.signal)}
                  >
                    키 목록 다시 시도
                  </Button>
                )}
              </div>
            )}
            {keys?.length === 20 && (
              <p className="text-xs muted">
                최대 20개입니다. 새 키를 발급하려면 기존 키를 삭제하세요.
              </p>
            )}
            {keys?.length === 0 && (
              <p className="text-xs muted">발급한 MCP 키가 없습니다.</p>
            )}
            {!!keys?.length && (
              <ul
                className="divide-y rounded-md border px-3"
                aria-label="발급한 MCP 키"
              >
                {keys.map((key) => (
                  <li
                    key={key.id}
                    className="flex items-center justify-between gap-3 py-3"
                  >
                    <div className="min-w-0">
                      <p className="text-sm break-words">{key.name}</p>
                      <p className="text-xs muted">
                        발급 {timestamp(key.created_at)}
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      className="shrink-0"
                      disabled={busy || loading}
                      aria-label={`${key.name} MCP 키 삭제`}
                      onClick={() => {
                        setDeleteError("");
                        setDeleting(key);
                      }}
                    >
                      삭제
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>

      <Dialog
        open={!!token}
        onOpenChange={(open) => {
          if (!open) dismissToken();
        }}
      >
        <DialogContent>
          <DialogTitle>MCP 키를 지금 저장하세요</DialogTitle>
          <DialogDescription>
            이 키는 한 번만 표시됩니다. 닫거나 페이지를 떠나면 다시 볼 수
            없습니다. 신뢰하는 수신자에게 안전하게 전달하세요.
          </DialogDescription>
          <div className="field">
            <label htmlFor="mcp-created-token">새 MCP 키</label>
            <textarea
              id="mcp-created-token"
              className="w-full resize-none rounded-md border bg-muted p-3 font-mono text-xs break-all"
              rows={4}
              readOnly
              autoComplete="off"
              spellCheck={false}
              value={token}
            />
          </div>
          <Button variant="outline" onClick={() => void copy(token, "MCP 키")}>
            MCP 키 복사
          </Button>
          {copied && (
            <p role="status" className="text-xs success">
              {copied} 복사 완료
            </p>
          )}
          {copyError && (
            <p role="alert" className="error">
              {copyError}
            </p>
          )}
          <Button onClick={dismissToken}>저장했어요 · 닫기</Button>
        </DialogContent>
      </Dialog>
      <Dialog
        open={!!deleting}
        onOpenChange={(open) => {
          if (!open && !busy) setDeleting(null);
        }}
      >
        <DialogContent showCloseButton={!busy}>
          <DialogTitle>MCP 키를 삭제할까요?</DialogTitle>
          <DialogDescription>
            {deleting?.name} 키를 삭제하면 이 키의 MCP 접근 권한이 폐기됩니다.
            다시 사용하려면 새 키를 발급해야 합니다.
          </DialogDescription>
          {deleteError && (
            <p role="alert" className="error">
              {deleteError}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => setDeleting(null)}
            >
              취소
            </Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => void remove()}
            >
              {busy ? "삭제 중…" : "MCP 키 삭제 확인"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </section>
  );
}
