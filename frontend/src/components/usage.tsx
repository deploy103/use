import { useMemo, useState } from "react";
import {
  Activity,
  Coins,
  ArrowDownToLine,
  ArrowUpFromLine,
} from "lucide-react";
import { AreaChart } from "@/components/dither-kit/area-chart";
import { Area } from "@/components/dither-kit/area";
import { Grid } from "@/components/dither-kit/grid";
import { XAxis } from "@/components/dither-kit/x-axis";
import { YAxis } from "@/components/dither-kit/y-axis";
import { Tooltip } from "@/components/dither-kit/tooltip";
import { StatsGrid } from "@/components/stats-grid";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { number, dollars, providerName } from "@/lib/api";
import type { Dashboard, Metrics } from "@/lib/types";

const metrics = [
  { key: "total_tokens", label: "토큰" },
  { key: "requests", label: "요청" },
  { key: "cost_usd", label: "비용 (USD)" },
] as const;
const chartConfig = { value: { color: "blue" as const, label: "사용량" } };
export function UsageHistory({ data }: { data: Dashboard }) {
  const available = metrics.filter((metric) =>
    data.daily.some((day) => day[metric.key] != null),
  );
  const [choice, setChoice] = useState<keyof Metrics>("total_tokens");
  const selected = available.find((m) => m.key === choice) ?? available[0];
  const rows = useMemo(
    () =>
      selected
        ? data.daily
            .filter((row) => row[selected.key] != null)
            .map((row) => ({
              date: row.date,
              value: row[selected.key] as number,
            }))
        : [],
    [data.daily, selected],
  );
  const trend = (key: keyof Metrics) =>
    data.daily
      .map((d) => d[key])
      .filter((v): v is number => typeof v === "number");
  if (!data.daily.length) return null;
  return (
    <section className="space-y-4" aria-label="날짜별 사용 기록">
      <StatsGrid
        stats={[
          {
            label: "기간 토큰",
            value: number(data.totals.total_tokens, true),
            icon: Activity,
            meta: data.totals.partial
              ? "일부 토큰 항목 미제공 · 부분 합계"
              : "입력 + 출력 + 캐시",
            trend: trend("total_tokens"),
          },
          {
            label: "입력 토큰",
            value: number(data.totals.input_tokens, true),
            icon: ArrowDownToLine,
            meta: "캐시 읽기·쓰기 제외",
            trend: trend("input_tokens"),
          },
          {
            label: "출력 토큰",
            value: number(data.totals.output_tokens, true),
            icon: ArrowUpFromLine,
            meta: "제공자가 보고한 값",
            trend: trend("output_tokens"),
          },
          {
            label: "기간 비용",
            value: dollars(data.totals.cost_usd),
            icon: Coins,
            meta: "USD · 추정/청구 구분은 계정 설명 참고",
            trend: trend("cost_usd"),
          },
        ]}
      />
      {selected && (
        <div className="panel">
          <div className="panel-heading">
            <div>
              <h2>Usage over time</h2>
              <p>
                {data.from === "0001-01-01" ? "전체 기간" : data.from} —{" "}
                {data.to} · {data.timezone} · 보고된 날짜만 표시
              </p>
            </div>
            <select
              aria-label="차트 지표"
              value={selected.key}
              onChange={(e) => setChoice(e.target.value as keyof Metrics)}
            >
              {available.map((m) => (
                <option key={m.key} value={m.key}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>
          <div className="panel-body">
            <div
              role="img"
              aria-label={`보고된 ${rows.length}일의 ${selected.label} 기록. 상세 수치는 아래 날짜별 표에서 확인할 수 있습니다.`}
              className="h-[250px] w-full"
            >
              <AreaChart
                data={rows}
                config={chartConfig}
                className="h-full w-full"
                animate={false}
                margins={{ left: 48, bottom: 24 }}
              >
                <Grid />
                <Area dataKey="value" variant="gradient" />
                <XAxis
                  dataKey="date"
                  tickFormatter={(value) => String(value).slice(5)}
                  maxTicks={5}
                />
                <YAxis tickFormatter={(v) => number(v, true)} />
                <Tooltip
                  labelKey="date"
                  valueFormatter={(v) =>
                    selected.key === "cost_usd" ? dollars(v) : number(v)
                  }
                />
              </AreaChart>
            </div>
            <details className="mt-3">
              <summary className="text-xs muted">
                날짜별 수치 보기 · 미제공 값은 —
              </summary>
              <div className="mt-3 max-h-60 overflow-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>날짜</TableHead>
                      <TableHead>토큰</TableHead>
                      <TableHead>요청</TableHead>
                      <TableHead>비용</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.daily.map((day) => (
                      <TableRow key={day.date}>
                        <TableCell>{day.date}</TableCell>
                        <TableCell>{number(day.total_tokens)}</TableCell>
                        <TableCell>{number(day.requests)}</TableCell>
                        <TableCell>{dollars(day.cost_usd)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </details>
          </div>
        </div>
      )}
    </section>
  );
}

export function ModelUsage({ data }: { data: Dashboard }) {
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("total_tokens");
  const rows = data.models
    .filter((row) =>
      `${row.model} ${row.account_name}`
        .toLowerCase()
        .includes(search.toLowerCase()),
    )
    .sort((a, b) =>
      sort === "model"
        ? a.model.localeCompare(b.model)
        : ((b[sort as keyof Metrics] as number) ?? -Infinity) -
          ((a[sort as keyof Metrics] as number) ?? -Infinity),
    );
  if (!data.models.length) return null;
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>Model usage</h2>
          <p>날짜 필터에 포함된 모델 · 계정별 집계 · 미제공 값은 —</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Input
            aria-label="모델 검색"
            placeholder="모델 또는 계정 검색"
            className="w-44"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <select
            aria-label="모델 정렬"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
          >
            <option value="total_tokens">토큰 많은 순</option>
            <option value="cost_usd">비용 많은 순</option>
            <option value="model">모델 이름순</option>
          </select>
        </div>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="pl-5">모델 / 계정</TableHead>
            <TableHead>요청</TableHead>
            <TableHead>입력</TableHead>
            <TableHead>출력</TableHead>
            <TableHead>캐시 읽기 / 쓰기</TableHead>
            <TableHead>전체 토큰</TableHead>
            <TableHead>비용 (USD)</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.length ? (
            rows.map((row) => (
              <TableRow key={`${row.account_id}-${row.model}`}>
                <TableCell className="pl-5 py-4">
                  <div className="font-medium">{row.model}</div>
                  <div className="text-[10px] muted">
                    {providerName(row.provider)} · {row.account_name}
                  </div>
                </TableCell>
                <TableCell>{number(row.requests)}</TableCell>
                <TableCell>{number(row.input_tokens)}</TableCell>
                <TableCell>{number(row.output_tokens)}</TableCell>
                <TableCell>
                  {number(row.cache_read_tokens)} /{" "}
                  {number(row.cache_write_tokens)}
                </TableCell>
                <TableCell>
                  {number(row.total_tokens)}
                  {row.partial ? " *" : ""}
                </TableCell>
                <TableCell>{dollars(row.cost_usd)}</TableCell>
              </TableRow>
            ))
          ) : (
            <TableRow>
              <TableCell colSpan={7} className="p-6 text-center muted">
                일치하는 모델이 없습니다.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </section>
  );
}
