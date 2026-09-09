import Link from "next/link";
import type { Route } from "next";
import type { ReactNode } from "react";

import { StageBar } from "@/components/research/stage-bar";
import type {
  DebugSnapshot,
  DebugStageAverage,
  DebugToolRow,
  ProviderDebugRow,
} from "@/lib/debug-stats";
import { AGENT_LABELS, STAGE_LABELS } from "@/lib/research/stages";
import { formatCost, formatDuration, formatTokens } from "@/lib/utils";

const STATUS_LABELS: Record<string, string> = {
  pending: "等待",
  planning: "规划中",
  researching: "研究中",
  checking: "核查中",
  writing: "撰写中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export function DebugDashboard({
  snapshot,
  providers,
  providersError,
}: {
  snapshot: DebugSnapshot;
  providers: ProviderDebugRow[];
  providersError: string | null;
}) {
  return (
    <div className="space-y-12">
      <p className="text-sm text-zinc-500">
        {snapshot.totals.sessions} 次研究 · 合计 {formatDuration(snapshot.totals.durationMs)} ·{" "}
        {formatCost(snapshot.totals.costUsd)}
      </p>

      <Section
        title="为什么这次研究花了这么久？"
        hint="最近会话的阶段瀑布。点进一条会回到首页回放。"
      >
        {snapshot.recentSessions.length === 0 ? (
          <Empty>还没有会话。跑完一次研究后这里会画出各阶段耗时。</Empty>
        ) : (
          <ul className="space-y-6">
            {snapshot.recentSessions.map((row) => (
              <li key={row.id}>
                <Link
                  href={`/?session=${encodeURIComponent(row.id)}` as Route}
                  className="block rounded-md focus-visible:ring-2 focus-visible:ring-zinc-400 focus-visible:outline-none"
                >
                  <p className="text-sm font-medium">{row.question}</p>
                  <p className="mt-1 text-xs text-zinc-500">
                    {STATUS_LABELS[row.status] ?? row.status} · {row.modelId} ·{" "}
                    {formatDuration(row.durationMs)} · {formatCost(row.costUsd)}
                  </p>
                </Link>
                <div className="mt-2">
                  <StageBar spans={row.stages} />
                </div>
              </li>
            ))}
          </ul>
        )}
        {snapshot.stageAverages.length > 0 && <StageAverageTable rows={snapshot.stageAverages} />}
      </Section>

      <Section
        title="哪个 Agent 最慢？"
        hint="按 agent_runs 总耗时。失败的 run 也计入——它们往往更贵。"
      >
        {snapshot.agents.length === 0 ? (
          <Empty>还没有 Agent 埋点。</Empty>
        ) : (
          <RankingTable
            caption="Agent 耗时与成本排行"
            columns={["Agent", "次数", "失败", "耗时", "成本", "输入 / 输出 / 缓存"]}
            rows={snapshot.agents.map((row) => [
              AGENT_LABELS[row.agent] ?? row.agent,
              String(row.runs),
              String(row.failed),
              formatDuration(row.durationMs),
              formatCost(row.costUsd),
              `${formatTokens(row.tokensIn)} / ${formatTokens(row.tokensOut)} / ${formatTokens(row.tokensCached)}`,
            ])}
          />
        )}
      </Section>

      <Section
        title="哪个 Tool 最容易失败？"
        hint="失败率优先，其次 p95 耗时。abandoned 是会话中途断开留下的。"
      >
        {snapshot.tools.length === 0 ? (
          <Empty>还没有工具调用。</Empty>
        ) : (
          <RankingTable
            caption="Tool 失败率与耗时"
            columns={["Tool", "次数", "失败率", "p50", "p95", "缓存命中", "错误码"]}
            rows={snapshot.tools.map((row) => [
              row.tool,
              String(row.calls),
              formatRate(row.failureRate),
              formatDuration(row.p50Ms),
              formatDuration(row.p95Ms),
              formatRate(row.cacheHitRate),
              formatErrorCodes(row),
            ])}
          />
        )}
      </Section>

      <Section
        title="哪个模型效果最好？"
        hint="eval 分数是 P7。这里只能看成本与耗时——便宜且快不等于更好。"
      >
        {snapshot.models.length === 0 ? (
          <Empty>还没有模型用量。</Empty>
        ) : (
          <RankingTable
            caption="模型成本与耗时"
            columns={["模型", "runs", "耗时", "成本", "输入 / 输出 / 缓存", "eval"]}
            rows={snapshot.models.map((row) => [
              row.modelId,
              String(row.runs),
              formatDuration(row.durationMs),
              formatCost(row.costUsd),
              `${formatTokens(row.tokensIn)} / ${formatTokens(row.tokensOut)} / ${formatTokens(row.tokensCached)}`,
              "P7",
            ])}
          />
        )}
      </Section>

      <Section
        title="Provider 缓存与配额"
        hint="进程内计数，重启归零。配额窗口在 Provider SQLite 里，重启不清。"
      >
        {providersError && (
          <p className="mb-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
            {providersError}
          </p>
        )}
        {providers.length === 0 ? (
          <Empty>Agent 服务不可用时看不到配额。</Empty>
        ) : (
          <RankingTable
            caption="Provider 缓存与配额"
            columns={["Provider", "配置", "请求", "缓存命中", "错误", "日剩余", "月剩余"]}
            rows={providers.map((row) => [
              row.provider,
              row.configured ? "是" : "否",
              String(row.requests),
              formatRate(row.cache_hit_rate),
              String(row.errors),
              formatQuota(row.daily_remaining, row.daily_limit),
              formatQuota(row.monthly_remaining, row.monthly_limit),
            ])}
          />
        )}
      </Section>
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-base font-semibold">{title}</h2>
        <p className="mt-1 text-xs text-zinc-500">{hint}</p>
      </div>
      {children}
    </section>
  );
}

function Empty({ children }: { children: string }) {
  return <p className="text-sm text-zinc-500">{children}</p>;
}

function RankingTable({
  caption,
  columns,
  rows,
}: {
  caption: string;
  columns: string[];
  rows: string[][];
}) {
  return (
    <div
      className="overflow-x-auto rounded-md focus-visible:ring-2 focus-visible:ring-zinc-400 focus-visible:outline-none"
      tabIndex={0}
      role="region"
      aria-label={caption}
    >
      <table className="w-full min-w-[36rem] text-left text-sm">
        <thead>
          <tr className="border-b border-zinc-200 text-xs text-zinc-500 dark:border-zinc-800">
            {columns.map((column) => (
              <th key={column} className="py-2 pr-3 font-medium">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-b border-zinc-100 dark:border-zinc-900">
              {row.map((cell, cellIndex) => (
                <td
                  key={cellIndex}
                  className={cellIndex === 0 ? "py-2 pr-3 font-medium" : "py-2 pr-3 text-zinc-600"}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StageAverageTable({ rows }: { rows: DebugStageAverage[] }) {
  return (
    <p className="mt-4 text-xs text-zinc-500">
      最近会话平均：{" "}
      {rows
        .map((row) => `${STAGE_LABELS[row.stage]} ${formatDuration(row.avgDurationMs)}`)
        .join(" · ")}
    </p>
  );
}

function formatRate(value: number | null | undefined): string {
  if (value === null || value === undefined) return "--";
  return `${Math.round(value * 100)}%`;
}

function formatQuota(
  remaining: number | null | undefined,
  limit: number | null | undefined,
): string {
  if (limit === null || limit === undefined) return "无限额";
  if (remaining === null || remaining === undefined) return `-- / ${limit}`;
  return `${remaining} / ${limit}`;
}

function formatErrorCodes(row: DebugToolRow): string {
  const entries = Object.entries(row.errorCodes);
  if (entries.length === 0) return "--";
  return entries.map(([code, count]) => `${code}×${count}`).join(" ");
}
