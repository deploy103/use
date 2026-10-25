import { useState } from "react";
import { Search, RefreshCw, Pencil, Trash2, Users } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import {
  AccountStatus,
  accountHealth,
  QuotaWindow,
} from "@/components/quota-card";
import { providerName, timestamp, number, dollars } from "@/lib/api";
import type { Account } from "@/lib/types";

export function Accounts({
  accounts,
  edit,
  remove,
  refresh,
  toggle,
  busy,
}: {
  accounts: Account[];
  edit: (a: Account) => void;
  remove: (a: Account) => void;
  refresh: (ids: string[]) => void;
  toggle: (ids: string[], enabled: boolean) => void;
  busy: boolean;
}) {
  const [search, setSearch] = useState("");
  const [provider, setProvider] = useState("all");
  const [status, setStatus] = useState("all");
  const [selection, setSelection] = useState<string[]>([]);
  const rows = accounts.filter(
    (a) =>
      `${a.name} ${providerName(a.provider)}`
        .toLowerCase()
        .includes(search.toLowerCase()) &&
      (provider === "all" || a.provider === provider) &&
      (status === "all" || accountHealth(a) === status),
  );
  const selected = selection.filter((id) => rows.some((a) => a.id === id));
  const allChecked =
    rows.length > 0 && rows.every((a) => selected.includes(a.id));
  return (
    <section className="space-y-4">
      <div className="filter-row">
        <div className="relative flex-1 min-w-44 max-w-sm">
          <Search size={14} className="absolute left-3 top-3 muted" />
          <Input
            aria-label="계정 검색"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
            placeholder="계정 이름 또는 서비스 검색"
          />
        </div>
        <select
          aria-label="계정 서비스 필터"
          value={provider}
          onChange={(e) => {
            setProvider(e.target.value);
            setSelection([]);
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
          aria-label="계정 상태 필터"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setSelection([]);
          }}
        >
          <option value="all">모든 상태</option>
          <option value="healthy">정상 연결</option>
          <option value="needs_auth">재인증 필요</option>
          <option value="error">조회 실패</option>
          <option value="disabled">비활성</option>
          <option value="pending">조회 대기</option>
        </select>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={allChecked}
            disabled={!rows.length || busy}
            onChange={(e) =>
              setSelection(e.target.checked ? rows.map((a) => a.id) : [])
            }
          />
          전체 선택
        </label>
        <span className="muted">
          {selected.length
            ? `${selected.length}개 선택`
            : `${rows.length}개 계정`}
        </span>
        <Button
          size="xs"
          variant="outline"
          disabled={
            !selected.length ||
            busy ||
            !accounts.some((a) => selected.includes(a.id) && a.enabled)
          }
          onClick={() =>
            refresh(
              selected.filter(
                (id) => accounts.find((a) => a.id === id)?.enabled,
              ),
            )
          }
        >
          선택 갱신
        </Button>
        <Button
          size="xs"
          variant="outline"
          disabled={!selected.length || busy}
          onClick={() => toggle(selected, true)}
        >
          활성화
        </Button>
        <Button
          size="xs"
          variant="outline"
          disabled={!selected.length || busy}
          onClick={() => toggle(selected, false)}
        >
          비활성화
        </Button>
      </div>
      {!rows.length && (
        <EmptyState
          icon={Users}
          title={
            accounts.length ? "검색 결과가 없습니다" : "첫 계정을 연결하세요"
          }
          description={
            accounts.length
              ? "검색어나 필터를 변경하세요."
              : "Claude, Gemini, ChatGPT를 공식 인증으로 연결하거나 기타에서 사용량 API를 추가하세요."
          }
        />
      )}
      <div className="space-y-3">
        {rows.map((account) => (
          <article
            key={account.id}
            className="panel"
            aria-label={`${account.name} 계정`}
          >
            <div className="flex flex-wrap items-center justify-between gap-3 p-4 border-b bg-muted/15">
              <div className="flex min-w-0 items-center gap-3">
                <input
                  aria-label={`${account.name} 선택`}
                  type="checkbox"
                  checked={selected.includes(account.id)}
                  disabled={busy}
                  onChange={(e) =>
                    setSelection((s) =>
                      e.target.checked
                        ? [...s, account.id]
                        : s.filter((id) => id !== account.id),
                    )
                  }
                />
                <span className="provider-icon" aria-hidden="true">
                  {providerName(account.provider).slice(0, 1)}
                </span>
                <div className="min-w-0">
                  <h2 className="text-sm font-semibold break-all">
                    {account.name}
                  </h2>
                  <p className="text-[10px] muted">
                    {providerName(account.provider)} · 마지막 성공{" "}
                    {timestamp(account.last_success_at)}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-1">
                <AccountStatus account={account} />
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`${account.name} 새로고침`}
                  disabled={busy || account.refreshing || !account.enabled}
                  onClick={() => refresh([account.id])}
                >
                  <RefreshCw />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`${account.name} 편집`}
                  disabled={busy || account.refreshing}
                  onClick={() => edit(account)}
                >
                  <Pencil />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="text-destructive"
                  aria-label={`${account.name} 삭제`}
                  disabled={busy || account.refreshing}
                  onClick={() => remove(account)}
                >
                  <Trash2 />
                </Button>
              </div>
            </div>
            <div className="p-4 space-y-4">
              {account.error && (
                <p className="error" role="alert">
                  {account.error}
                  {account.last_success_at
                    ? " · 아래 값은 마지막 성공 시점의 기록입니다."
                    : ""}
                </p>
              )}
              {account.quota?.length ? (
                <div className="grid gap-5 lg:grid-cols-2">
                  {account.quota.map((q, i) => (
                    <QuotaWindow quota={q} key={i} />
                  ))}
                </div>
              ) : (
                <p className="text-xs muted">
                  {accountHealth(account) === "healthy"
                    ? "구독 한도 미제공"
                    : "구독 한도를 아직 읽지 못했습니다."}
                </p>
              )}
              {account.summary && (
                <p className="text-xs muted">
                  누적 집계 (날짜 필터 제외) · 토큰{" "}
                  {number(account.summary.total_tokens)} · 요청{" "}
                  {number(account.summary.requests)} · 비용{" "}
                  {dollars(account.summary.cost_usd)}
                </p>
              )}
              {account.note && (
                <p className="text-[11px] muted break-words">{account.note}</p>
              )}
              <div className="flex flex-wrap gap-x-6 gap-y-1 text-[10px] muted">
                <span>최근 시도 {timestamp(account.last_attempt_at)}</span>
                <span>
                  제공 항목:{" "}
                  {account.capabilities?.length
                    ? account.capabilities.join(" · ")
                    : "확인되지 않음"}
                </span>
              </div>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
