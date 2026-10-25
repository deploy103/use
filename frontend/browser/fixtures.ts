import type { Page, Route } from "@playwright/test";
import type {
  Account,
  Dashboard,
  Metrics,
  OAuthSnapshot,
} from "../src/lib/types";

// Deliberately synthetic. These values never enter production source or a live
// server. Every API request is intercepted and unknown/external requests blocked.
export const syntheticMcpToken = "synthetic-mcp-key-not-a-real-credential";
export const mcpKeyFixture = (index: number) => ({
  id: `synthetic-mcp-${index}`,
  name: `Trusted recipient ${index}`,
  created_at: "2026-10-25T07:00:00Z",
});
export const emptyMetrics: Metrics = {
  requests: null,
  input_tokens: null,
  output_tokens: null,
  cache_read_tokens: null,
  cache_write_tokens: null,
  total_tokens: null,
  cost_usd: null,
  partial: true,
};
const baseAccount: Account = {
  id: "synthetic-chatgpt",
  name: "Personal ChatGPT",
  provider: "chatgpt",
  base_url: "https://chatgpt.com",
  enabled: true,
  status: "connected",
  error: null,
  last_attempt_at: "2026-10-25T07:00:00Z",
  last_success_at: "2026-10-25T07:00:00Z",
  refreshing: false,
  quota: [],
  capabilities: ["quota"],
  summary: null,
  note: "Codex 구독 한도 · ChatGPT 웹 대화 전체의 토큰 기록이 아닙니다.",
  has_credentials: true,
};
export function dashboardFixture(dated = false): Dashboard {
  const accounts: Account[] = [
    {
      ...baseAccount,
      quota: [
        {
          label: "5시간 한도",
          used_percent: 17.5,
          remaining: 82.5,
          limit: 100,
          unit: "%",
          resets_at: "2026-10-25T12:00:00Z",
        },
        {
          label: "주간 한도",
          used_percent: 42,
          remaining: 58,
          limit: 100,
          unit: "%",
          resets_at: "2026-10-29T12:00:00Z",
        },
      ],
    },
    {
      ...baseAccount,
      id: "synthetic-claude",
      name: "Studio Claude LB",
      provider: "claude-lb",
      note: "Claude LB 누적 집계와 독립 한도 창입니다.",
      quota: [
        {
          label: "세션 한도",
          used_percent: 68,
          remaining: 32,
          limit: 100,
          unit: "%",
          resets_at: "2026-10-25T10:00:00Z",
        },
      ],
      capabilities: ["tokens", "quota"],
      summary: {
        ...emptyMetrics,
        total_tokens: 184000,
        requests: 96,
        period: "lifetime",
      },
    },
    {
      ...baseAccount,
      id: "synthetic-gemini",
      name: "Work Gemini",
      provider: "gemini-account",
      status: "needs_auth",
      error: "인증이 만료되었습니다. 다시 연결하세요.",
      last_success_at: null,
      quota: [],
      note: null,
    },
    {
      ...baseAccount,
      id: "synthetic-disabled",
      name: "Archive API",
      provider: "custom-api",
      enabled: false,
      status: "error",
      error: "사용량 API가 응답하지 않습니다.",
      quota: [],
      note: null,
    },
  ];
  const days = dated
    ? [20, 21, 22, 23, 24, 25].map((day, i) => ({
        date: `2026-10-${day}`,
        requests: i + 3,
        input_tokens: (i + 1) * 1200,
        output_tokens: (i + 1) * 300,
        cache_read_tokens: 0,
        cache_write_tokens: null,
        total_tokens: (i + 1) * 1500,
        cost_usd: i === 3 ? null : (i + 1) * 0.12,
        partial: true,
      }))
    : [];
  const totals = dated
    ? {
        requests: 33,
        input_tokens: 25200,
        output_tokens: 6300,
        cache_read_tokens: 0,
        cache_write_tokens: null,
        total_tokens: 31500,
        cost_usd: 2.04,
        partial: true,
      }
    : { ...emptyMetrics };
  return {
    range: "7d",
    from: "2026-10-19",
    to: "2026-10-25",
    timezone: "Asia/Seoul",
    refreshed_at: baseAccount.last_success_at,
    refreshing: false,
    accounts,
    totals,
    daily: days,
    models: dated
      ? [
          {
            ...totals,
            account_id: "synthetic-claude",
            account_name: "Studio Claude LB",
            model: "synthetic-model",
            provider: "claude-lb",
          },
        ]
      : [],
    providers: [
      {
        id: "claude-lb",
        name: "Claude LB",
        default_url: "",
        help: "HTTPS Claude LB 주소와 조회 키",
        fields: [],
      },
      {
        id: "claude",
        name: "Claude API 조직 사용량",
        default_url: "https://api.anthropic.com",
        help: "Admin key",
        fields: [{ name: "days", label: "조회 일수 (1–90)", type: "number" }],
      },
      {
        id: "gemini",
        name: "Gemini LB",
        default_url: "",
        help: "Gemini LB gateway",
        fields: [],
      },
      {
        id: "codex-lb",
        name: "Codex LB",
        default_url: "",
        help: "Codex LB gateway",
        fields: [],
      },
      {
        id: "openai",
        name: "OpenAI API",
        default_url: "https://api.openai.com",
        help: "Admin key",
        fields: [],
      },
      {
        id: "grok",
        name: "xAI",
        default_url: "https://management-api.x.ai",
        help: "Management key",
        fields: [{ name: "team_id", label: "Team ID", type: "text" }],
      },
    ],
  };
}
export function oauthFixture(provider: "gemini" | "chatgpt"): OAuthSnapshot {
  return {
    id: `synthetic-${provider}-flow`,
    provider,
    status: "awaiting_authorization",
    authorization_url:
      provider === "gemini"
        ? "https://accounts.google.com/o/oauth2/v2/auth?state=synthetic"
        : "https://auth.openai.com/oauth/authorize?state=synthetic",
    callback_url:
      provider === "gemini"
        ? "http://localhost:8085/oauth2callback"
        : "http://localhost:1455/auth/callback",
    callback_mode: provider === "gemini" ? "code" : "url",
    expires_at: Date.now() + 300_000,
    error: null,
  };
}
export type MockState = {
  data: Dashboard;
  authenticated: boolean;
  setup: boolean;
  failDashboard: boolean;
  calls: {
    path: string;
    method: string;
    body: Record<string, unknown> | null;
  }[];
  flow: OAuthSnapshot | null;
  startDelay: number;
  completeDelay: number;
  testReadable: boolean;
  unexpected: string[];
  mcpKeys: ReturnType<typeof mcpKeyFixture>[];
  mcpFailure: "GET" | "POST" | "DELETE" | null;
  mcpCreateDelay: number;
  mcpNextId: number;
};
export async function mockBackend(
  page: Page,
  dated = false,
): Promise<MockState> {
  const state: MockState = {
    data: dashboardFixture(dated),
    authenticated: true,
    setup: false,
    failDashboard: false,
    calls: [],
    flow: null,
    startDelay: 0,
    completeDelay: 0,
    testReadable: true,
    unexpected: [],
    mcpKeys: [],
    mcpFailure: null,
    mcpCreateDelay: 0,
    mcpNextId: 1,
  };
  const respond = (route: Route, json: unknown, status = 200) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(json),
    });
  await page.context().route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== "http://127.0.0.1:4179") {
      state.unexpected.push(url.origin);
      await route.abort();
      return;
    }
    if (request.resourceType() === "document") {
      const response = await route.fetch();
      await route.fulfill({
        response,
        headers: {
          ...response.headers(),
          "content-security-policy":
            "default-src 'self'; script-src 'self'; connect-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; base-uri 'self'",
        },
      });
      return;
    }
    if (!url.pathname.startsWith("/api/")) {
      await route.continue();
      return;
    }
    const path = url.pathname;
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    state.calls.push({ path, method, body });
    if (path === "/api/session")
      return respond(route, {
        authenticated: state.authenticated,
        setup_required: state.setup,
      });
    if (path === "/api/login" || path === "/api/setup") {
      state.authenticated = true;
      state.setup = false;
      return respond(route, { authenticated: true });
    }
    if (path === "/api/logout") {
      state.authenticated = false;
      return respond(route, { authenticated: false });
    }
    if (!state.authenticated)
      return respond(route, { detail: "로그인이 필요합니다." }, 401);
    if (path === "/api/dashboard")
      return respond(
        route,
        state.failDashboard
          ? { detail: "합성 서버 일시 오류" }
          : { ...state.data, range: url.searchParams.get("range") },
        state.failDashboard ? 503 : 200,
      );
    if (path === "/api/refresh") return respond(route, { started: true });
    if (path === "/api/settings/password")
      return respond(route, { authenticated: true });
    if (path === "/api/mcp/keys" || path.startsWith("/api/mcp/keys/")) {
      if (state.mcpFailure === method)
        return respond(route, { detail: "합성 MCP 서버 일시 오류" }, 503);
      if (path === "/api/mcp/keys" && method === "GET")
        return respond(route, { keys: state.mcpKeys });
      if (path === "/api/mcp/keys" && method === "POST") {
        if (state.mcpCreateDelay)
          await new Promise((resolve) =>
            setTimeout(resolve, state.mcpCreateDelay),
          );
        const name = typeof body?.name === "string" ? body.name.trim() : "";
        if (!name || name.length > 100)
          return respond(route, { detail: "키 이름은 1~100자입니다." }, 400);
        if (state.mcpKeys.length >= 20)
          return respond(route, { detail: "MCP 키는 최대 20개입니다." }, 400);
        const key = { ...mcpKeyFixture(state.mcpNextId++), name };
        state.mcpKeys.push(key);
        return respond(route, { ...key, token: syntheticMcpToken });
      }
      if (path.startsWith("/api/mcp/keys/") && method === "DELETE") {
        const id = decodeURIComponent(path.split("/").at(-1)!);
        state.mcpKeys = state.mcpKeys.filter((key) => key.id !== id);
        return respond(route, { deleted: true });
      }
    }
    if (path === "/api/connections/test")
      return respond(route, {
        ok: true,
        capabilities: state.testReadable ? ["quota", "tokens"] : [],
        quota: [],
        summary: null,
        note: state.testReadable
          ? "Synthetic read-only usage endpoint"
          : "추론 전용 API입니다.",
      });
    if (path === "/api/accounts" && method === "POST") {
      state.data.accounts.push({
        ...state.data.accounts[0],
        ...body,
        id: "synthetic-new",
        quota: [],
      });
      return respond(route, { id: "synthetic-new" });
    }
    if (path.startsWith("/api/accounts/")) {
      const id = path.split("/").at(-1);
      if (method === "PATCH")
        state.data.accounts = state.data.accounts.map((a) =>
          a.id === id ? { ...a, ...body } : a,
        );
      if (method === "DELETE")
        state.data.accounts = state.data.accounts.filter((a) => a.id !== id);
      return respond(route, { ok: true });
    }
    if (path === "/api/oauth/start") {
      state.flow ??= oauthFixture(body.provider);
      if (state.startDelay)
        await new Promise((resolve) => setTimeout(resolve, state.startDelay));
      return respond(route, state.flow);
    }
    if (path.startsWith("/api/oauth/")) {
      if (method === "DELETE") return respond(route, { cancelled: true });
      if (path.endsWith("/complete")) {
        if (state.completeDelay)
          await new Promise((resolve) =>
            setTimeout(resolve, state.completeDelay),
          );
        return respond(route, {
          ...state.flow,
          status: "connected",
          authorization_url: null,
        });
      }
      return respond(route, state.flow);
    }
    state.unexpected.push(`${method} ${path}`);
    return respond(route, { detail: "Unexpected synthetic API request" }, 500);
  });
  return state;
}
