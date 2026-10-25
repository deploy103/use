import type { LucideIcon } from "lucide-react";
import { lazy, Suspense } from "react";
import { cn } from "@/lib/utils";

const Sparkline = lazy(() =>
  import("@/components/dither-kit/sparkline").then((module) => ({
    default: module.Sparkline,
  })),
);

export type DashboardStat = {
  label: string;
  value: string;
  icon: LucideIcon;
  meta?: string;
  trend: number[];
  comparison?: { tone: "positive" | "negative" | "neutral"; text: string };
};

const ACCENT_STYLES = [
  "bg-slate-500/10 text-slate-600 dark:bg-neutral-500/15 dark:text-slate-400",
  "bg-slate-500/10 text-slate-600 dark:bg-neutral-500/15 dark:text-slate-400",
  "bg-slate-500/10 text-slate-600 dark:bg-neutral-500/15 dark:text-slate-400",
  "bg-slate-500/10 text-slate-600 dark:bg-neutral-500/15 dark:text-slate-400",
  "bg-slate-500/10 text-slate-600 dark:bg-neutral-500/15 dark:text-slate-400",
];

const COMPARISON_STYLES = {
  positive: "text-emerald-600 dark:text-emerald-400",
  negative: "text-red-600 dark:text-red-400",
  neutral: "text-muted-foreground",
} as const;

export type StatsGridProps = {
  stats: DashboardStat[];
};

export function StatsGrid({ stats }: StatsGridProps) {
  const columnsClass = stats.length >= 5 ? "xl:grid-cols-5" : "xl:grid-cols-4";

  return (
    <div className={cn("grid grid-cols-2 gap-3", columnsClass)}>
      {stats.map((stat, index) => {
        const Icon = stat.icon;
        const accent = ACCENT_STYLES[index % ACCENT_STYLES.length];
        return (
          <div
            key={stat.label}
            className="animate-fade-in-up card-hover rounded-xl border bg-card p-3 sm:p-4"
            style={{ animationDelay: `${index * 75}ms` }}
          >
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                {stat.label}
              </span>
              <div
                className={cn(
                  "flex h-6 w-6 items-center justify-center rounded-lg sm:h-8 sm:w-8",
                  accent,
                )}
              >
                <Icon className="h-4 w-4" aria-hidden="true" />
              </div>
            </div>
            <div className="mt-1">
              <div
                data-testid="stat-value-row"
                className="flex items-baseline gap-2"
              >
                <p className="text-[1.625rem] font-semibold tracking-[-0.02em]">
                  {stat.value}
                </p>
                {stat.comparison ? (
                  <p
                    className={cn(
                      "text-xs font-medium",
                      COMPARISON_STYLES[stat.comparison.tone],
                    )}
                  >
                    {stat.comparison.text}
                  </p>
                ) : null}
              </div>
              {stat.meta ? (
                <p className="mt-1 text-[10px] text-muted-foreground sm:text-xs">
                  {stat.meta}
                </p>
              ) : null}
            </div>
            {stat.trend.length > 0 ? (
              <div className="mt-1 h-10" aria-hidden="true">
                <Suspense fallback={null}>
                  <Sparkline
                    data={stat.trend}
                    color="blue"
                    className="h-full w-full"
                  />
                </Suspense>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
