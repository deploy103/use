import { Clock3, RefreshCw, Info } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { number, dollars, providerName, timestamp } from "@/lib/api";
import type { Account, Quota } from "@/lib/types";

export function accountHealth(account: Account) {
  if (!account.enabled) return "disabled";
  if (account.status === "needs_auth") return "needs_auth";
  if (account.error || ["error", "failed"].includes(account.status))
    return "error";
  if (account.status === "connected" && account.last_success_at)
    return "healthy";
  return "pending";
}
export function AccountStatus({ account }: { account: Account }) {
  const health = accountHealth(account);
  const text = {
    disabled: "비활성",
    needs_auth: "재인증 필요",
    error: "조회 실패",
    healthy: "정상 연결",
    pending: "조회 대기",
  }[health];
  return (
    <Badge
      variant="outline"
      className={
        health === "healthy"
          ? "text-emerald-600 dark:text-emerald-400"
          : ["error", "needs_auth"].includes(health)
            ? "text-destructive"
            : "text-muted-foreground"
      }
    >
      {text}
      {account.refreshing && account.enabled ? " · 갱신 중" : ""}
    </Badge>
  );
}
export function QuotaWindow({ quota }: { quota: Quota }) {
  const used =
    quota.used_percent ??
    (quota.limit != null && quota.limit > 0 && quota.remaining != null
      ? (1 - quota.remaining / quota.limit) * 100
      : null);
  const bounded = used == null ? null : Math.max(0, Math.min(100, used));
  const remainingPercent = used == null ? null : Math.max(0, 100 - used);
  return (
    <div className="space-y-2" data-testid="quota-window">
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="font-medium">{quota.label}</span>
        <span className="muted tabular-nums">
          {used == null ? "사용률 미제공" : `${number(used)}% 사용`}
        </span>
      </div>
      <div
        className="quota-track"
        role={bounded == null ? undefined : "progressbar"}
        aria-label={`${quota.label} 사용률`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={bounded ?? undefined}
      >
        {bounded != null && (
          <div
            className={`quota-fill ${bounded >= 90 ? "danger" : bounded >= 70 ? "warning" : ""}`}
            style={{ width: `${bounded}%` }}
          />
        )}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-1 text-[11px] muted">
        <span>
          {quota.remaining != null
            ? `잔여 ${number(quota.remaining)}${quota.unit ? ` ${quota.unit}` : ""}${quota.limit != null ? ` / ${number(quota.limit)}` : ""}`
            : remainingPercent != null
              ? `잔여 ${number(remainingPercent)}%`
              : "잔여량 미제공"}
        </span>
        <span className="inline-flex items-center gap-1">
          <Clock3 size={11} />
          {quota.resets_at
            ? `${timestamp(quota.resets_at)} 재설정`
            : "재설정 시간 미제공"}
        </span>
      </div>
    </div>
  );
}
export function QuotaCard({
  account,
  refresh,
  busy,
}: {
  account: Account;
  refresh?: () => void;
  busy?: boolean;
}) {
  return (
    <article
      className="quota-card space-y-5"
      aria-label={`${account.name} 한도`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="provider-icon" aria-hidden="true">
            {providerName(account.provider).slice(0, 1)}
          </span>
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold" title={account.name}>
              {account.name}
            </h3>
            <p className="text-[11px] muted">
              {providerName(account.provider)}
            </p>
          </div>
        </div>
        <AccountStatus account={account} />
      </div>
      {account.error && (
        <p className="error" role="alert">
          {account.error}
          {account.last_success_at && (
            <span className="block mt-1">
              아래 값은 마지막 성공 시점의 기록입니다.
            </span>
          )}
        </p>
      )}
      {account.quota?.length ? (
        <div className="space-y-5">
          {account.quota.map((quota, index) => (
            <QuotaWindow key={`${quota.label}-${index}`} quota={quota} />
          ))}
        </div>
      ) : (
        <p className="text-xs muted flex items-start gap-2">
          <Info size={14} className="shrink-0" />
          {accountHealth(account) === "healthy"
            ? "이 연결은 구독 한도를 제공하지 않습니다."
            : "한도를 아직 읽지 못했습니다. 연결 상태를 확인하세요."}
        </p>
      )}
      {account.summary && (
        <div className="rounded-md bg-muted/50 p-3 text-xs">
          <p className="muted text-[10px] uppercase tracking-wider mb-1">
            누적 집계 ·{" "}
            {account.summary.period === "lifetime"
              ? "Lifetime"
              : account.summary.period ?? "기간 미제공"}{" "}
            · 날짜 필터 제외
          </p>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span>토큰 {number(account.summary.total_tokens, true)}</span>
            <span>요청 {number(account.summary.requests)}</span>
            <span>비용 {dollars(account.summary.cost_usd)}</span>
          </div>
        </div>
      )}
      {account.note && (
        <p className="text-[11px] muted break-words">{account.note}</p>
      )}
      <div className="flex items-center justify-between gap-2 border-t pt-3 text-[10px] muted">
        <span>
          마지막 성공{" "}
          <time dateTime={account.last_success_at ?? undefined}>
            {timestamp(account.last_success_at)}
          </time>
        </span>
        {refresh && (
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={`${account.name} 새로고침`}
            disabled={!account.enabled || account.refreshing || busy}
            onClick={refresh}
          >
            <RefreshCw />
          </Button>
        )}
      </div>
    </article>
  );
}
