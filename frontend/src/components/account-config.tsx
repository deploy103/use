import { useEffect, useRef, useState } from "react";
import { Info, FlaskConical, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { QuotaWindow } from "@/components/quota-card";
import { api, message, oauthAccount, providerName } from "@/lib/api";
import type { Account, AccountConfig, Provider, TestResult } from "@/lib/types";

export function AccountConfigDialog({
  account,
  initialProvider = "custom-api",
  claude = false,
  providers,
  close,
  saved,
}: {
  account?: Account;
  initialProvider?: string;
  claude?: boolean;
  providers: Provider[];
  close: () => void;
  saved: () => void;
}) {
  const [provider, setProvider] = useState(
    account?.provider ?? initialProvider,
  );
  const [name, setName] = useState(
    account?.name ??
      (initialProvider === "custom-api"
        ? "Custom API"
        : providerName(initialProvider)),
  );
  const [url, setUrl] = useState(
    (account?.provider ?? initialProvider) === "claude"
      ? "https://api.anthropic.com"
      : account?.base_url ??
          providers.find((p) => p.id === initialProvider)?.default_url ??
          "",
  );
  const [credential, setCredential] = useState("");
  const [enabled, setEnabled] = useState(account?.enabled ?? true);
  const [reconfigure, setReconfigure] = useState(!account);
  const [usagePath, setUsagePath] = useState("/v1/usage");
  const [format, setFormat] = useState("auto");
  const [authType, setAuthType] = useState("auto");
  const [extra, setExtra] = useState<Record<string, string | number>>({});
  const [result, setResult] = useState<TestResult | null>(null);
  const [tested, setTested] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const oauth = oauthAccount(provider);
  const definition = providers.find((p) => p.id === provider);
  const config: AccountConfig = {
    name: name.trim(),
    provider,
    base_url: url.trim(),
    credential: credential.trim(),
    extra:
      provider === "custom-api"
        ? { usage_path: usagePath, format, auth_type: authType }
        : provider === "gemini"
          ? { ...extra, mode: "gateway" }
          : extra,
  };
  const fingerprint = JSON.stringify(config);
  const readable =
    result?.ok === true &&
    result.capabilities?.some((c) =>
      ["quota", "tokens", "requests", "cost"].includes(c),
    );
  function validate() {
    if (!name.trim()) throw new Error("계정 이름을 입력하세요.");
    if (reconfigure && !oauth) {
      const parsed = new URL(url);
      if (
        parsed.protocol !== "https:" ||
        parsed.username ||
        parsed.password ||
        parsed.search ||
        parsed.hash
      )
        throw new Error(
          "인증 정보·쿼리·프래그먼트가 없는 HTTPS 주소를 입력하세요.",
        );
      if (
        !credential.trim() &&
        !["usage-json", "custom-api"].includes(provider)
      )
        throw new Error("조회용 API 키를 입력하세요.");
      if (
        provider === "custom-api" &&
        (!usagePath.startsWith("/") ||
          usagePath.startsWith("//") ||
          usagePath.includes("?") ||
          usagePath.includes("#"))
      )
        throw new Error("사용량 경로는 /로 시작하는 경로여야 합니다.");
    }
  }
  async function test() {
    setBusy(true);
    setError("");
    setResult(null);
    setTested("");
    try {
      validate();
      const next = await api<TestResult>("/api/connections/test", config);
      if (alive.current) {
        setResult(next);
        setTested(fingerprint);
      }
      return (
        next.ok === true &&
        next.capabilities?.some((c) =>
          ["quota", "tokens", "requests", "cost"].includes(c),
        )
      );
    } catch (e) {
      if (alive.current) setError(message(e));
      return false;
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  async function save() {
    setError("");
    try {
      validate();
      if (reconfigure && !oauth && !(tested === fingerprint && readable)) {
        if (!(await test())) return;
        if (!alive.current) return;
      }
      setBusy(true);
      await api(
        account
          ? `/api/accounts/${encodeURIComponent(account.id)}`
          : "/api/accounts",
        account && (!reconfigure || oauth)
          ? { name: name.trim(), enabled }
          : { ...config, enabled },
        account ? "PATCH" : "POST",
      );
      if (alive.current) {
        setCredential("");
        saved();
        close();
      }
    } catch (e) {
      if (alive.current) setError(message(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogContent>
        <DialogTitle>
          {account
            ? "계정 설정"
            : claude
              ? "Claude 연결"
              : "커스텀 API / 기타 연결"}
        </DialogTitle>
        <DialogDescription>
          {oauth
            ? "OAuth 계정은 이름과 활성화 여부만 수정할 수 있습니다."
            : claude
              ? "Claude API 조직 사용량 또는 Claude LB 사용량을 조회 키로 연결합니다."
              : "사용량 조회 API를 연결합니다. URL과 조회 키만 입력하면 형식을 자동 감지합니다."}
        </DialogDescription>
        {claude && (
          <div className="notice">
            <Info size={17} />
            <p>
              Claude.ai Pro/Max 소비자 계정 로그인은 제공하지 않습니다. 조직
              Admin API 키 또는 Claude LB 조회 키를 사용하세요. 계정 비밀번호는
              요청하지 않습니다.
            </p>
          </div>
        )}
        <form
          className="form-stack"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          {account && (
            <>
              <div className="field">
                <label htmlFor="edit-name">계정 이름</label>
                <Input
                  id="edit-name"
                  value={name}
                  maxLength={100}
                  onChange={(e) => setName(e.target.value)}
                  disabled={busy}
                  required
                />
              </div>
              <label className="flex gap-2 items-center">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                  disabled={busy}
                />
                수집 활성화
              </label>
              {!oauth && (
                <label className="flex gap-2 items-center">
                  <input
                    type="checkbox"
                    checked={reconfigure}
                    onChange={(e) => setReconfigure(e.target.checked)}
                    disabled={busy}
                  />
                  API 연결 설정 변경 (키 재입력)
                </label>
              )}
            </>
          )}
          {!oauth && reconfigure && (
            <fieldset disabled={busy} className="form-stack min-w-0">
              {provider !== "custom-api" && (
                <div className="field">
                  <label htmlFor="api-provider">
                    {claude ? "Claude 연결 범위" : "고급 연결 종류"}
                  </label>
                  <select
                    id="api-provider"
                    value={provider}
                    disabled={!!account}
                    onChange={(e) => {
                      setProvider(e.target.value);
                      setUrl(
                        e.target.value === "claude"
                          ? "https://api.anthropic.com"
                          : providers.find((p) => p.id === e.target.value)
                              ?.default_url ?? "",
                      );
                      setName(providerName(e.target.value));
                      setCredential("");
                      setExtra({});
                      setResult(null);
                    }}
                  >
                    {claude ? (
                      <>
                        <option value="claude-lb">Claude LB 사용량</option>
                        <option value="claude">Claude API 조직 사용량</option>
                      </>
                    ) : (
                      <>
                        <option value="custom-api">Custom API</option>
                        {providers
                          .filter(
                            (p) => !oauthAccount(p.id) && p.id !== "custom-api",
                          )
                          .map((p) => (
                            <option value={p.id} key={p.id}>
                              {p.name}
                            </option>
                          ))}
                      </>
                    )}
                  </select>
                  <p className="form-hint">
                    {provider === "claude"
                      ? "공식 Anthropic 조직 Usage/Cost API · 일반 추론 키가 아닌 Admin API 키가 필요합니다. Claude.ai 구독 기록이 아닙니다."
                      : definition?.help}
                  </p>
                </div>
              )}
              <div className="field">
                <label htmlFor="api-url">API 기본 URL</label>
                <Input
                  id="api-url"
                  type="url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  readOnly={provider === "claude"}
                  placeholder="https://api.example.com"
                  maxLength={500}
                  required
                  autoComplete="off"
                />
                <p className="form-hint">
                  {provider === "claude"
                    ? "공식 Anthropic API 주소로 고정됩니다."
                    : "소유자가 신뢰하는 HTTPS API 주소. 키를 URL에 넣지 마세요."}
                </p>
              </div>
              <div className="field">
                <label htmlFor="api-key">
                  {provider === "claude"
                    ? "Anthropic Admin API 키"
                    : "조회용 API 키"}
                </label>
                <Input
                  id="api-key"
                  type="password"
                  value={credential}
                  onChange={(e) => setCredential(e.target.value)}
                  maxLength={16000}
                  autoComplete="new-password"
                  required={!["usage-json", "custom-api"].includes(provider)}
                  placeholder={
                    account ? "설정 변경 시 키를 다시 입력하세요" : "API key"
                  }
                />
                {provider === "custom-api" && (
                  <p className="form-hint">
                    선택 사항 · 인증이 없는 공개 사용량 API는 비워 두세요.
                  </p>
                )}
              </div>
              <details>
                <summary className="text-xs muted">고급 설정 (선택)</summary>
                <div className="form-stack mt-4">
                  {!account && (
                    <div className="field">
                      <label htmlFor="api-name">계정 이름</label>
                      <Input
                        id="api-name"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        maxLength={100}
                        required
                      />
                    </div>
                  )}
                  {provider === "custom-api" ? (
                    <>
                      <div className="field">
                        <label htmlFor="usage-path">사용량 조회 경로</label>
                        <Input
                          id="usage-path"
                          value={usagePath}
                          onChange={(e) => setUsagePath(e.target.value)}
                          required
                          maxLength={300}
                        />
                      </div>
                      <div className="grid grid-cols-2 gap-3">
                        <div className="field">
                          <label htmlFor="usage-format">응답 형식</label>
                          <select
                            id="usage-format"
                            value={format}
                            onChange={(e) => setFormat(e.target.value)}
                          >
                            <option value="auto">자동 감지</option>
                            <option value="claude-lb">Claude LB</option>
                            <option value="gateway">Gateway</option>
                            <option value="usage-json">Usage JSON</option>
                          </select>
                        </div>
                        <div className="field">
                          <label htmlFor="auth-type">인증 헤더</label>
                          <select
                            id="auth-type"
                            value={authType}
                            onChange={(e) => setAuthType(e.target.value)}
                          >
                            <option value="auto">자동 감지 (권장)</option>
                            <option value="bearer">Bearer</option>
                            <option value="x-api-key">x-api-key</option>
                          </select>
                        </div>
                      </div>
                    </>
                  ) : (
                    definition?.fields
                      .filter((f) => f.name !== "mode")
                      .map((f) => (
                        <div className="field" key={f.name}>
                          <label htmlFor={`extra-${f.name}`}>{f.label}</label>
                          <Input
                            id={`extra-${f.name}`}
                            type={f.type === "number" ? "number" : "text"}
                            value={extra[f.name] ?? ""}
                            onChange={(e) =>
                              setExtra((v) => ({
                                ...v,
                                [f.name]:
                                  f.type === "number" && e.target.value
                                    ? Number(e.target.value)
                                    : e.target.value,
                              }))
                            }
                          />
                        </div>
                      ))
                  )}
                </div>
              </details>
              <div className="notice">
                <Info size={17} />
                <p>
                  추론 전용 API는 사용량을 제공하지 않을 수 있습니다.
                  미지원·읽기 실패는 0으로 표시하지 않습니다. 테스트는 조회
                  요청만 보냅니다.
                </p>
              </div>
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => void test()}
              >
                <FlaskConical />
                연결 테스트
              </Button>
            </fieldset>
          )}
          {result && tested === fingerprint && (
            <div
              className={readable ? "rounded-lg border p-4 space-y-3" : "error"}
              role="status"
            >
              {readable ? (
                <>
                  <p className="success flex gap-2 items-center text-xs">
                    <CheckCircle2 size={16} />
                    사용량 조회 확인 · {result.capabilities?.join(", ")}
                  </p>
                  {result.quota?.map((q, i) => (
                    <QuotaWindow quota={q} key={i} />
                  ))}
                </>
              ) : (
                <p>
                  사용량 API 미지원 또는 읽기 실패. 추론 가능 여부와 사용량 조회
                  가능 여부는 별개입니다.
                </p>
              )}
              {result.note && (
                <p className="text-xs muted mt-2">{result.note}</p>
              )}
            </div>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={close}>
              취소
            </Button>
            <Button disabled={busy}>
              {busy
                ? "확인 중…"
                : reconfigure && !oauth && !(tested === fingerprint && readable)
                  ? "테스트 후 저장"
                  : "저장"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
