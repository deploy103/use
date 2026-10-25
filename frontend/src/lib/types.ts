export type Metrics = {
  requests: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  total_tokens: number | null;
  cost_usd: number | null;
  partial?: boolean;
};
export type Quota = {
  label: string;
  used_percent: number | null;
  remaining: number | null;
  limit: number | null;
  unit: string | null;
  resets_at: string | null;
};
export type Account = {
  id: string;
  name: string;
  provider: string;
  base_url: string;
  enabled: boolean;
  status: string;
  error: string | null;
  last_attempt_at: string | null;
  last_success_at: string | null;
  refreshing: boolean;
  quota: Quota[] | null;
  summary: (Partial<Metrics> & { period?: string }) | null;
  capabilities: string[] | null;
  note: string | null;
  has_credentials: boolean;
};
export type Provider = {
  id: string;
  name: string;
  default_url: string;
  help: string;
  fields: { name: string; label: string; type: string }[];
};
export type Dashboard = {
  range: string;
  from: string;
  to: string;
  timezone: string;
  refreshed_at: string | null;
  refreshing: boolean;
  totals: Metrics;
  daily: (Metrics & { date: string })[];
  models: (Metrics & {
    account_id: string;
    account_name: string;
    model: string;
    provider: string;
  })[];
  accounts: Account[];
  providers: Provider[];
};
export type OAuthProvider = "gemini" | "chatgpt";
export type OAuthSnapshot = {
  id: string;
  provider: OAuthProvider;
  status:
    | "awaiting_authorization"
    | "authorizing"
    | "connected"
    | "error"
    | "expired";
  authorization_url: string | null;
  callback_url: string | null;
  callback_mode: "code" | "url";
  expires_at: string | number | null;
  error: string | null;
};
export type AccountConfig = {
  name: string;
  provider: string;
  base_url: string;
  credential: string;
  extra: Record<string, string | number>;
};
export type TestResult = {
  ok: boolean;
  capabilities?: string[];
  quota?: Quota[];
  summary?: Account["summary"];
  note?: string;
};
