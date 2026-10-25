export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

// All credentials travel only to the same-origin owner backend. No provider
// request, token, cookie inspection or browser-side credential persistence.
export async function api<T>(
  path: string,
  body?: unknown,
  method = body === undefined ? "GET" : "POST",
  signal?: AbortSignal,
): Promise<T> {
  // OAuth exchange and bounded analytics probing can perform several upstream
  // calls. Their backend budget is longer than an ordinary owner GET.
  const longMutation =
    method === "POST" &&
    (path === "/api/connections/test" ||
      /^\/api\/oauth\/[^/]+\/complete$/.test(path));
  const timeout = AbortSignal.timeout(longMutation ? 90_000 : 15_000);
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  });
  if (
    response.status === 401 &&
    !["/api/login", "/api/setup", "/api/session"].includes(path)
  ) {
    window.dispatchEvent(new Event("owner-session-expired"));
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(
      typeof data?.detail === "string"
        ? data.detail
        : `요청을 완료하지 못했습니다 (HTTP ${response.status}).`,
      response.status,
    );
  }
  if (data === null)
    throw new Error("서버 응답을 읽을 수 없습니다. 다시 시도하세요.");
  return data as T;
}

export function message(error: unknown): string {
  if (
    error instanceof DOMException &&
    (error.name === "TimeoutError" || error.name === "AbortError")
  )
    return "요청 시간이 초과되었습니다. 연결을 확인하고 다시 시도하세요.";
  return error instanceof Error
    ? error.message
    : "요청에 실패했습니다. 다시 시도하세요.";
}

const authOrigins: Record<string, readonly string[]> = {
  gemini: ["https://accounts.google.com"],
  chatgpt: ["https://auth.openai.com"],
};
export function trustedAuthorizationUrl(
  provider: string,
  value: string | null,
): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return !url.username &&
      !url.password &&
      url.protocol === "https:" &&
      authOrigins[provider]?.includes(url.origin)
      ? url.href
      : null;
  } catch {
    return null;
  }
}

export const providerName = (id: string) =>
  ({
    claude: "Claude API Admin",
    "gemini-account": "Gemini",
    chatgpt: "ChatGPT",
    "custom-api": "Custom API",
    gemini: "Gemini LB",
    "codex-lb": "Codex LB",
    "claude-lb": "Claude LB",
    openai: "OpenAI Admin",
    grok: "xAI",
    "usage-json": "Usage JSON",
  })[id] ?? id;
export const oauthAccount = (id: string) =>
  ["gemini-account", "chatgpt"].includes(id);
export const number = (v: number | null | undefined, compact = false) =>
  v == null || !Number.isFinite(v)
    ? "—"
    : new Intl.NumberFormat(
        "ko-KR",
        compact
          ? { notation: "compact", maximumFractionDigits: 1 }
          : { maximumFractionDigits: 2 },
      ).format(v);
export const dollars = (v: number | null | undefined) =>
  v == null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 4,
      }).format(v);
export function timestamp(value: string | null | undefined) {
  if (!value) return "아직 없음";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "시간 정보 없음"
    : new Intl.DateTimeFormat("ko-KR", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }).format(date);
}
