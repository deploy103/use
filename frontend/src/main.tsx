import { createRoot } from "react-dom/client";
import {
  Component,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import type { ErrorInfo, ReactNode } from "react";
import {
  Users,
  CheckCircle2,
  CircleAlert,
  Layers3,
  RefreshCw,
  Download,
  Info,
  Plus,
  ShieldCheck,
  Activity,
} from "lucide-react";
import { AppHeader } from "@/components/layout/app-header";
import type { Page } from "@/components/layout/app-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { StatsGrid } from "@/components/stats-grid";
import { EmptyState } from "@/components/empty-state";
import { accountHealth, QuotaCard } from "@/components/quota-card";
import { Accounts } from "@/components/accounts";
import { Settings } from "@/components/settings";
import { AccountConfigDialog } from "@/components/account-config";
import { ProviderLogin } from "@/components/provider-login";
import { api, message, providerName, timestamp } from "@/lib/api";
import type { Account, Dashboard, OAuthProvider } from "@/lib/types";
import "@/index.css";

type Session = { authenticated: boolean; setup_required: boolean };
const pages: Page[] = ["dashboard", "accounts", "usage", "settings"];
const currentPage = (): Page =>
  pages.find((page) => location.pathname === `/${page}`) ?? "dashboard";
const UsageHistory = lazy(() =>
  import("@/components/usage").then((module) => ({
    default: module.UsageHistory,
  })),
);
const ModelUsage = lazy(() =>
  import("@/components/usage").then((module) => ({
    default: module.ModelUsage,
  })),
);

class ErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(_error: Error, _info: ErrorInfo) {
    /* Never log auth/config data. */
  }
  render() {
    return this.state.failed ? (
      <div className="auth-screen">
        <div className="auth-card space-y-4">
          <h1 className="text-lg font-semibold">화면을 표시하지 못했습니다</h1>
          <p className="text-xs muted">
            서버 데이터 또는 화면 상태를 확인하고 다시 불러오세요.
          </p>
          <Button onClick={() => location.reload()}>다시 불러오기</Button>
        </div>
      </div>
    ) : (
      this.props.children
    );
  }
}

function App() {
  const [setupToken, setSetupToken] = useState(() => {
    const params = new URLSearchParams(location.hash.slice(1));
    const token = params.get("setup") ?? "";
    if (params.has("setup")) {
      params.delete("setup");
      history.replaceState(
        null,
        "",
        `${location.pathname}${location.search}${params.size ? `#${params}` : ""}`,
      );
    }
    return token;
  });
  const [session, setSession] = useState<Session | null>(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);
  const [dark, setDark] = useState(() => {
    try {
      return localStorage.getItem("use-theme") !== "light";
    } catch {
      return true;
    }
  });
  const request = useRef(0);
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try {
      localStorage.setItem("use-theme", dark ? "dark" : "light");
    } catch {
      /* Browser storage may be disabled. */
    }
  }, [dark]);
  const checkSession = useCallback(async () => {
    const version = ++request.current;
    setChecking(true);
    setError("");
    try {
      const next = await api<Session>("/api/session");
      if (version !== request.current) return;
      if (
        typeof next.authenticated !== "boolean" ||
        typeof next.setup_required !== "boolean"
      )
        throw new Error("세션 응답이 올바르지 않습니다.");
      setSession(next);
      if (!next.setup_required) setSetupToken("");
    } catch (e) {
      if (version === request.current) setError(message(e));
    } finally {
      if (version === request.current) setChecking(false);
    }
  }, []);
  useEffect(() => {
    void checkSession();
    const expired = () => {
      ++request.current;
      setSession({ authenticated: false, setup_required: false });
      setPassword("");
      setSetupToken("");
      setChecking(false);
      setError("관리자 세션이 만료되었습니다. 다시 로그인하세요.");
    };
    window.addEventListener("owner-session-expired", expired);
    return () => {
      ++request.current;
      window.removeEventListener("owner-session-expired", expired);
    };
  }, [checkSession]);
  async function login() {
    if (!session) return;
    const version = ++request.current;
    setBusy(true);
    setError("");
    try {
      await api(
        session.setup_required ? "/api/setup" : "/api/login",
        session.setup_required ? { token: setupToken, password } : { password },
      );
      if (version !== request.current) return;
      setPassword("");
      setSetupToken("");
      setSession({ authenticated: true, setup_required: false });
    } catch (e) {
      if (version === request.current) setError(message(e));
    } finally {
      if (version === request.current) setBusy(false);
    }
  }
  async function logout() {
    ++request.current;
    // Unmount workspace first: cancels provider sessions and erases in-memory keys.
    setSession(null);
    setChecking(true);
    setPassword("");
    setSetupToken("");
    setError("");
    try {
      await api("/api/logout", {});
      setSession({ authenticated: false, setup_required: false });
    } catch (e) {
      setError(
        `로그아웃을 확인하지 못했습니다. 서버 세션이 남아 있을 수 있습니다. ${message(e)}`,
      );
    } finally {
      setChecking(false);
    }
  }
  if (session?.authenticated)
    return (
      <Workspace
        dark={dark}
        toggleTheme={() => setDark((v) => !v)}
        logout={() => void logout()}
      />
    );
  return (
    <div className="auth-screen">
      <div className="auth-card space-y-6">
        <div className="brand !p-0">
          <span className="brand-mark">u</span>
          <span>
            Use<span className="brand-caption">AI USAGE WORKSPACE</span>
          </span>
        </div>
        <div>
          <p className="eyebrow">OWNER ACCESS</p>
          <h1 className="text-xl font-semibold">
            {session?.setup_required ? "소유자 첫 설정" : "관리자 로그인"}
          </h1>
          <p className="text-xs muted mt-2">
            내 사용량 워크스페이스에 로그인합니다.
            <br />
            Claude·Gemini·ChatGPT 계정 연결은 로그인 후 진행합니다.
          </p>
        </div>
        {checking ? (
          <p role="status" className="text-xs muted">
            서버 세션 확인 중…
          </p>
        ) : session ? (
          <form
            className="form-stack"
            onSubmit={(e) => {
              e.preventDefault();
              void login();
            }}
          >
            {session.setup_required && (
              <div className="field">
                <label htmlFor="setup-token">첫 설정 토큰</label>
                <Input
                  id="setup-token"
                  type="password"
                  value={setupToken}
                  onChange={(e) => setSetupToken(e.target.value)}
                  required
                  autoComplete="off"
                  disabled={busy}
                />
                <p className="form-hint">
                  서버가 발급한 일회성 설정 링크의 토큰입니다.
                </p>
              </div>
            )}
            <div className="field">
              <label htmlFor="owner-password">
                {session.setup_required
                  ? "새 관리자 비밀번호"
                  : "관리자 비밀번호"}
              </label>
              <Input
                id="owner-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={session.setup_required ? 12 : 1}
                maxLength={200}
                autoComplete={
                  session.setup_required ? "new-password" : "current-password"
                }
                disabled={busy}
              />
              {session.setup_required && (
                <p className="form-hint">12~200자로 설정하세요.</p>
              )}
            </div>
            <Button disabled={busy}>
              {busy
                ? "확인 중…"
                : session.setup_required
                  ? "워크스페이스 만들기"
                  : "로그인"}
            </Button>
          </form>
        ) : (
          <Button variant="outline" onClick={() => void checkSession()}>
            세션 다시 확인
          </Button>
        )}
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <p className="flex items-center gap-2 text-[10px] muted">
          <ShieldCheck size={13} />
          Single owner · 인증 정보는 서버에서만 보관
        </p>
      </div>
    </div>
  );
}

function Workspace({
  dark,
  toggleTheme,
  logout,
}: {
  dark: boolean;
  toggleTheme: () => void;
  logout: () => void;
}) {
  const [page, setPage] = useState<Page>(currentPage);
  const [range, setRange] = useState("7d");
  const [provider, setProvider] = useState("all");
  const [account, setAccount] = useState("all");
  const [data, setData] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [oauth, setOauth] = useState<OAuthProvider | null>(null);
  const [config, setConfig] = useState<{
    provider: string;
    account?: Account;
    claude?: boolean;
  } | null>(null);
  const [deleting, setDeleting] = useState<Account | null>(null);
  const controller = useRef<AbortController | null>(null);
  const revision = useRef(0);
  const alive = useRef(true);
  const query = new URLSearchParams({ range, provider, account }).toString();
  const load = useCallback(async () => {
    const version = ++revision.current;
    controller.current?.abort();
    controller.current = new AbortController();
    setLoading(true);
    try {
      const next = await api<Dashboard>(
        `/api/dashboard?${query}`,
        undefined,
        "GET",
        controller.current.signal,
      );
      if (
        !Array.isArray(next.accounts) ||
        !Array.isArray(next.daily) ||
        !Array.isArray(next.models) ||
        !Array.isArray(next.providers) ||
        !next.totals
      )
        throw new Error("대시보드 응답이 올바르지 않습니다.");
      if (alive.current && version === revision.current) {
        setData(next);
        setError("");
      }
    } catch (e) {
      if (alive.current && version === revision.current) setError(message(e));
    } finally {
      if (alive.current && version === revision.current) setLoading(false);
    }
  }, [query]);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      ++revision.current;
      controller.current?.abort();
    };
  }, []);
  useEffect(() => {
    void load();
    return () => {
      ++revision.current;
      controller.current?.abort();
    };
  }, [load]);
  useEffect(() => {
    const timer = setInterval(
      () => {
        if (!document.hidden) void load();
      },
      data?.refreshing ? 3000 : 30_000,
    );
    return () => clearInterval(timer);
  }, [load, data?.refreshing]);
  useEffect(() => {
    const pop = () => setPage(currentPage());
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  function navigate(next: Page) {
    setPage(next);
    history.pushState(null, "", `/${next}`);
    window.scrollTo({ top: 0 });
  }
  async function mutate(work: () => Promise<unknown>) {
    setBusy(true);
    setActionError("");
    try {
      await work();
      if (alive.current) await load();
      return true;
    } catch (e) {
      if (alive.current) {
        setActionError(message(e));
        void load();
      }
      return false;
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  const refresh = (ids?: string[]) =>
    void mutate(async () => {
      if (ids) {
        for (const id of ids) {
          if (!alive.current) return;
          await api("/api/refresh", { account_id: id });
        }
      } else await api("/api/refresh", {});
    });
  const accounts = data?.accounts ?? [];
  const filtered = accounts.filter(
    (a) =>
      (provider === "all" || a.provider === provider) &&
      (account === "all" || a.id === account),
  );
  const healthy = accounts.filter((a) => accountHealth(a) === "healthy").length;
  const attention = accounts.filter((a) =>
    ["error", "needs_auth"].includes(accountHealth(a)),
  ).length;
  const observed = accounts.reduce((sum, a) => sum + (a.quota?.length ?? 0), 0);
  const connections = (
    <div className="connection-buttons">
      <Button
        variant="outline"
        onClick={() => setConfig({ provider: "claude-lb", claude: true })}
      >
        Claude 연결
      </Button>
      <Button variant="outline" onClick={() => setOauth("gemini")}>
        Gemini 연결
      </Button>
      <Button onClick={() => setOauth("chatgpt")}>ChatGPT 연결</Button>
      <details>
        <summary>기타 +</summary>
        <div className="other-menu">
          <button
            onClick={(e) => {
              e.currentTarget.closest("details")?.removeAttribute("open");
              setConfig({ provider: "custom-api" });
            }}
          >
            <Plus size={13} className="inline mr-2" />
            커스텀 API 추가
          </button>
          {data?.providers
            .filter(
              (p) =>
                ![
                  "chatgpt",
                  "gemini-account",
                  "claude",
                  "claude-lb",
                  "custom-api",
                ].includes(p.id),
            )
            .map((p) => (
              <button
                key={p.id}
                onClick={(e) => {
                  e.currentTarget.closest("details")?.removeAttribute("open");
                  setConfig({ provider: p.id });
                }}
              >
                {p.name}
              </button>
            ))}
        </div>
      </details>
    </div>
  );
  const historyEmpty = (
    <div className="notice">
      <Info size={18} />
      <div className="space-y-2">
        <p className="font-medium">구독 한도와 날짜별 사용 기록은 다릅니다</p>
        <p className="muted">
          현재 선택한 기간의 날짜별 토큰·비용 기록이 제공되지 않았습니다. 구독
          계정은 위의 사용률·잔여 한도·재설정 시간만 제공할 수 있습니다. 누적
          카운터나 한도를 날짜별 토큰으로 변환하지 않습니다.
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setConfig({ provider: "custom-api" })}
        >
          <Plus />
          사용량 API 연결
        </Button>
      </div>
    </div>
  );
  return (
    <AppHeader
      page={page}
      navigate={navigate}
      dark={dark}
      toggleTheme={toggleTheme}
      logout={logout}
    >
      <div className="page-heading">
        <div>
          <p className="eyebrow">
            {page === "dashboard"
              ? "OVERVIEW"
              : page === "accounts"
                ? "CONNECTIONS"
                : page === "usage"
                  ? "OBSERVED USAGE"
                  : "PREFERENCES"}
          </p>
          <h1>
            {page === "dashboard"
              ? "Dashboard"
              : page === "accounts"
                ? "Accounts"
                : page === "usage"
                  ? "Usage"
                  : "Settings"}
          </h1>
          <p>
            {page === "dashboard"
              ? "계정별 한도와 실제 보고된 사용량을 한눈에."
              : page === "accounts"
                ? "여러 서비스, 여러 계정을 하나의 워크스페이스에서."
                : page === "usage"
                  ? "제공자가 보고한 날짜별 기록만 집계합니다."
                  : "워크스페이스와 소유자 보안을 관리합니다."}
          </p>
        </div>
        {page === "dashboard" || page === "accounts" ? connections : null}
      </div>
      {actionError && (
        <p className="error" role="alert">
          {actionError}
        </p>
      )}
      {page === "settings" ? (
        <Settings dark={dark} toggleTheme={toggleTheme} />
      ) : (
        <>
          {page !== "accounts" && (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="filter-row">
                {(page === "usage" || !!data?.daily.length) && (
                  <select
                    aria-label="조회 기간"
                    value={range}
                    onChange={(e) => setRange(e.target.value)}
                  >
                    <option value="today">오늘</option>
                    <option value="7d">최근 7일</option>
                    <option value="30d">최근 30일</option>
                    <option value="all">전체 기간</option>
                  </select>
                )}
                <select
                  aria-label="서비스 필터"
                  value={provider}
                  onChange={(e) => {
                    setProvider(e.target.value);
                    setAccount("all");
                  }}
                >
                  <option value="all">모든 서비스</option>
                  {[...new Set(accounts.map((a) => a.provider))].map((p) => (
                    <option key={p} value={p}>
                      {providerName(p)}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="계정 필터"
                  value={account}
                  onChange={(e) => setAccount(e.target.value)}
                >
                  <option value="all">모든 계정</option>
                  {accounts
                    .filter(
                      (a) => provider === "all" || a.provider === provider,
                    )
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                </select>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy || data?.refreshing}
                  onClick={() => refresh()}
                >
                  <RefreshCw
                    className={data?.refreshing ? "animate-spin" : ""}
                  />
                  {data?.refreshing ? "조회 중" : "전체 갱신"}
                </Button>
                <Button variant="outline" size="sm" asChild>
                  <a href={`/api/export?${query}`}>
                    <Download />
                    CSV
                  </a>
                </Button>
              </div>
            </div>
          )}
          <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] muted">
            <span>
              {data
                ? `마지막 수집 성공 ${timestamp(data.refreshed_at)} · 날짜 기준 ${data.timezone}`
                : "아직 불러온 기록이 없습니다"}
            </span>
            <span role="status">
              {loading
                ? data
                  ? "갱신 중 · 이전 데이터 표시"
                  : "대시보드 불러오는 중…"
                : data?.refreshing
                  ? "서버에서 사용량 수집 중 · 자동 갱신"
                  : "30초마다 상태 확인"}
            </span>
          </div>
          {error && (
            <div
              className="error flex flex-wrap items-center justify-between gap-3"
              role="alert"
            >
              <span>
                {error}
                {data ? " 마지막으로 불러온 데이터를 유지합니다." : ""}
              </span>
              <Button variant="outline" size="sm" onClick={() => void load()}>
                다시 시도
              </Button>
            </div>
          )}
          {!data && !loading && !error && (
            <EmptyState icon={Activity} title="표시할 데이터가 없습니다" />
          )}
          {data && page === "dashboard" && (
            <>
              <StatsGrid
                stats={[
                  {
                    label: "연결된 계정",
                    value: String(accounts.length),
                    icon: Users,
                    meta: `${new Set(accounts.map((a) => a.provider)).size}개 서비스 · 비활성 ${accounts.filter((a) => !a.enabled).length}개`,
                    trend: [],
                  },
                  {
                    label: "정상 연결",
                    value: String(healthy),
                    icon: CheckCircle2,
                    meta: "활성 · 마지막 수집 성공 확인",
                    trend: [],
                  },
                  {
                    label: "확인 필요",
                    value: String(attention),
                    icon: CircleAlert,
                    meta: "조회 실패 또는 재인증 필요",
                    trend: [],
                  },
                  {
                    label: "관측된 한도",
                    value: String(observed),
                    icon: Layers3,
                    meta: "계정별 독립 한도 창 · 날짜 필터 제외",
                    trend: [],
                  },
                ]}
              />
              <section className="space-y-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-semibold">Account quotas</h2>
                    <p className="mt-1 text-[11px] muted">
                      최신 관측값 · 기간 집계와 독립 · 사용률을 표시합니다
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => navigate("accounts")}
                  >
                    계정 관리 →
                  </Button>
                </div>
                {filtered.length ? (
                  <div className="grid gap-4 xl:grid-cols-2">
                    {filtered.map((a) => (
                      <QuotaCard
                        account={a}
                        key={a.id}
                        busy={busy}
                        refresh={() => refresh([a.id])}
                      />
                    ))}
                  </div>
                ) : (
                  <EmptyState
                    icon={Users}
                    title={
                      accounts.length
                        ? "선택한 계정이 없습니다"
                        : "첫 계정을 연결하세요"
                    }
                    description="위의 Claude, Gemini, ChatGPT 연결 버튼으로 시작하세요. 커스텀 API는 기타 메뉴에 있습니다."
                  />
                )}
              </section>
              <div className="flex flex-wrap gap-2 text-[11px]">
                {[...new Set(accounts.map((a) => a.provider))].map((p) => (
                  <span
                    className="rounded-md border bg-card px-3 py-1.5 muted"
                    key={p}
                  >
                    {providerName(p)}{" "}
                    <span className="ml-2 font-medium text-foreground">
                      {accounts.filter((a) => a.provider === p).length}
                    </span>
                    <span className="ml-2">
                      정상{" "}
                      {
                        accounts.filter(
                          (a) =>
                            a.provider === p && accountHealth(a) === "healthy",
                        ).length
                      }
                    </span>
                  </span>
                ))}
              </div>
              {data.daily.length ? (
                <Suspense
                  fallback={<p role="status">사용 기록 화면 준비 중…</p>}
                >
                  <UsageHistory data={data} />
                  <ModelUsage data={data} />
                </Suspense>
              ) : (
                historyEmpty
              )}
            </>
          )}
          {data && page === "accounts" && (
            <Accounts
              accounts={accounts}
              busy={busy}
              edit={(a) =>
                setConfig({
                  provider: a.provider,
                  account: a,
                  claude: ["claude", "claude-lb"].includes(a.provider),
                })
              }
              remove={setDeleting}
              refresh={refresh}
              toggle={(ids, enabled) =>
                void mutate(async () => {
                  for (const id of ids) {
                    if (!alive.current) return;
                    await api(
                      `/api/accounts/${encodeURIComponent(id)}`,
                      { enabled },
                      "PATCH",
                    );
                  }
                })
              }
            />
          )}
          {data &&
            page === "usage" &&
            (data.daily.length ? (
              <Suspense fallback={<p role="status">사용 기록 화면 준비 중…</p>}>
                <UsageHistory data={data} />
                <ModelUsage data={data} />
              </Suspense>
            ) : (
              <>
                {historyEmpty}
                <p className="text-xs muted">
                  구독 한도와 누적 집계는 Dashboard 또는 Accounts에서
                  확인하세요.
                </p>
              </>
            ))}
        </>
      )}
      {oauth && (
        <ProviderLogin
          key={oauth}
          provider={oauth}
          close={() => setOauth(null)}
          connected={() => void load()}
        />
      )}
      {config && (
        <AccountConfigDialog
          key={config.account?.id ?? config.provider}
          account={config.account}
          initialProvider={config.provider}
          claude={config.claude}
          providers={data?.providers ?? []}
          close={() => setConfig(null)}
          saved={() => void load()}
        />
      )}
      <Dialog
        open={!!deleting}
        onOpenChange={(open) => {
          if (!open && !busy) setDeleting(null);
        }}
      >
        <DialogContent>
          <DialogTitle>계정 연결을 삭제할까요?</DialogTitle>
          <DialogDescription>
            {deleting?.name}의 저장된 인증 정보와 로컬 사용 기록을 삭제합니다.
            제공자 계정 자체는 삭제하지 않습니다.
          </DialogDescription>
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
              onClick={() => {
                const id = deleting?.id;
                if (id)
                  void mutate(() =>
                    api(
                      `/api/accounts/${encodeURIComponent(id)}`,
                      undefined,
                      "DELETE",
                    ),
                  ).then((ok) => {
                    if (ok && alive.current) {
                      setDeleting(null);
                      if (account === id) setAccount("all");
                    }
                  });
              }}
            >
              {busy ? "삭제 중…" : "삭제 확인"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </AppHeader>
  );
}

createRoot(document.getElementById("root")!).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>,
);
