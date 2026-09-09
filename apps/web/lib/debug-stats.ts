/**
 * `/debug` 用的纯函数与视图类型。
 *
 * 类型放这里而不是 `db/queries`：排行组件不能引服务端模块（§17.1）。
 */

import type { Stage } from "@mra/shared";

/** 最近邻名次法。空数组返回 null，不编 0——没样本和「全是 0ms」在排行里必须能分开。 */
export function percentile(values: readonly number[], p: number): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const rank = Math.ceil((p / 100) * sorted.length);
  return sorted[Math.max(0, rank - 1)] ?? null;
}

export function rate(part: number, whole: number): number | null {
  if (whole <= 0) return null;
  return part / whole;
}

export type DebugStageSpan = {
  stage: Stage;
  durationMs: number;
  active: boolean;
};

export type DebugRecentSession = {
  id: string;
  question: string;
  status: string;
  modelId: string;
  durationMs: number | null;
  costUsd: number | null;
  createdAt: number;
  stages: DebugStageSpan[];
};

export type DebugStageAverage = {
  stage: Stage;
  avgDurationMs: number;
  samples: number;
};

export type DebugAgentRow = {
  agent: string;
  runs: number;
  failed: number;
  durationMs: number;
  costUsd: number;
  tokensIn: number;
  tokensOut: number;
  tokensCached: number;
};

export type DebugToolRow = {
  tool: string;
  calls: number;
  failures: number;
  failureRate: number | null;
  p50Ms: number | null;
  p95Ms: number | null;
  cacheHits: number;
  cacheHitRate: number | null;
  errorCodes: Record<string, number>;
};

export type DebugModelRow = {
  modelId: string;
  runs: number;
  durationMs: number;
  costUsd: number;
  tokensIn: number;
  tokensOut: number;
  tokensCached: number;
};

export type DebugTotals = {
  sessions: number;
  costUsd: number;
  durationMs: number;
};

export type DebugSnapshot = {
  totals: DebugTotals;
  recentSessions: DebugRecentSession[];
  stageAverages: DebugStageAverage[];
  agents: DebugAgentRow[];
  tools: DebugToolRow[];
  models: DebugModelRow[];
};

/** 与 Python `ProviderDebugRow` 对齐的进程内快照。 */
export type ProviderDebugRow = {
  provider: string;
  configured: boolean;
  requests: number;
  cache_hits: number;
  cache_misses: number;
  cache_hit_rate: number | null;
  http_attempts: number;
  errors: number;
  daily_used: number | null;
  daily_limit: number | null;
  daily_remaining: number | null;
  monthly_used: number | null;
  monthly_limit: number | null;
  monthly_remaining: number | null;
};
