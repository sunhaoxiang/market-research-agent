/**
 * `/debug` 聚合（§20.2，P6-9）。
 *
 * 只读 `agent_runs` / `tool_calls` / `research_events` / `research_sessions`。
 * 报告 UI 仍然走事件回放，这里不读 sources / claims。
 */

import { and, desc, inArray, sql } from "drizzle-orm";
import type { Stage } from "@mra/shared";

import type { Db } from "@/db/client";
import { toClientEvent } from "@/db/queries/events";
import {
  agentRuns,
  researchEvents,
  researchSessions,
  toolCalls,
  type ResearchEvent as EventRow,
} from "@/db/schema";
import {
  percentile,
  rate,
  type DebugAgentRow,
  type DebugModelRow,
  type DebugRecentSession,
  type DebugSnapshot,
  type DebugStageAverage,
  type DebugStageSpan,
  type DebugToolRow,
  type DebugTotals,
} from "@/lib/debug-stats";
import { resolveStageSpans } from "@/lib/research/stages";
import { reduceAll } from "@/lib/research/state";

export const RECENT_SESSION_LIMIT = 15;
export type {
  DebugAgentRow,
  DebugModelRow,
  DebugRecentSession,
  DebugSnapshot,
  DebugStageAverage,
  DebugStageSpan,
  DebugToolRow,
  DebugTotals,
};

const STAGE_EVENT_TYPES = [
  "session_started",
  "stage_changed",
  "session_completed",
  "session_failed",
  "session_cancelled",
] as const;

export function loadDebugSnapshot(db: Db, recentLimit = RECENT_SESSION_LIMIT): DebugSnapshot {
  const recentSessions = loadRecentSessions(db, recentLimit);
  return {
    totals: loadTotals(db),
    recentSessions,
    stageAverages: averagesFromSessions(recentSessions),
    agents: loadAgentRanking(db),
    tools: loadToolRanking(db),
    models: loadModelRanking(db),
  };
}

function loadTotals(db: Db): DebugTotals {
  const row = db
    .select({
      sessions: sql<number>`count(*)`,
      costUsd: sql<number | null>`sum(${researchSessions.costUsd})`,
      durationMs: sql<number | null>`sum(${researchSessions.durationMs})`,
    })
    .from(researchSessions)
    .get();
  return {
    sessions: Number(row?.sessions ?? 0),
    costUsd: Number(row?.costUsd ?? 0),
    durationMs: Number(row?.durationMs ?? 0),
  };
}

function loadRecentSessions(db: Db, limit: number): DebugRecentSession[] {
  const sessions = db
    .select({
      id: researchSessions.id,
      question: researchSessions.question,
      status: researchSessions.status,
      modelId: researchSessions.modelId,
      durationMs: researchSessions.durationMs,
      costUsd: researchSessions.costUsd,
      createdAt: researchSessions.createdAt,
      completedAt: researchSessions.completedAt,
    })
    .from(researchSessions)
    .orderBy(desc(researchSessions.createdAt))
    .limit(limit)
    .all();

  const spansBySession = loadStageSpans(
    db,
    sessions.map((row) => row.id),
    Object.fromEntries(sessions.map((row) => [row.id, row.completedAt ?? Date.now()])),
  );

  return sessions.map((row) => ({
    id: row.id,
    question: row.question,
    status: row.status,
    modelId: row.modelId,
    durationMs: row.durationMs,
    costUsd: row.costUsd,
    createdAt: row.createdAt,
    stages: spansBySession[row.id] ?? [],
  }));
}

function averagesFromSessions(recent: DebugRecentSession[]): DebugStageAverage[] {
  const buckets = new Map<Stage, { total: number; samples: number }>();
  for (const session of recent) {
    for (const span of session.stages) {
      if (span.active) continue;
      const bucket = buckets.get(span.stage) ?? { total: 0, samples: 0 };
      bucket.total += span.durationMs;
      bucket.samples += 1;
      buckets.set(span.stage, bucket);
    }
  }
  return [...buckets.entries()]
    .map(([stage, bucket]) => ({
      stage,
      avgDurationMs: bucket.total / bucket.samples,
      samples: bucket.samples,
    }))
    .sort((a, b) => b.avgDurationMs - a.avgDurationMs);
}

function loadStageSpans(
  db: Db,
  sessionIds: string[],
  nowBySession: Record<string, number>,
): Record<string, DebugStageSpan[]> {
  if (sessionIds.length === 0) return {};
  const rows = db
    .select()
    .from(researchEvents)
    .where(
      and(
        inArray(researchEvents.sessionId, sessionIds),
        inArray(researchEvents.type, [...STAGE_EVENT_TYPES]),
      ),
    )
    .orderBy(researchEvents.sessionId, researchEvents.seq)
    .all();

  const grouped = new Map<string, EventRow[]>();
  for (const row of rows) {
    const list = grouped.get(row.sessionId) ?? [];
    list.push(row);
    grouped.set(row.sessionId, list);
  }

  const result: Record<string, DebugStageSpan[]> = {};
  for (const [sessionId, events] of grouped) {
    const state = reduceAll(events.map(toClientEvent));
    result[sessionId] = resolveStageSpans(state, nowBySession[sessionId] ?? Date.now()).map(
      (span) => ({
        stage: span.stage,
        durationMs: span.durationMs,
        active: span.active,
      }),
    );
  }
  return result;
}

function loadAgentRanking(db: Db): DebugAgentRow[] {
  const rows = db
    .select({
      agent: agentRuns.agent,
      runs: sql<number>`count(*)`,
      failed: sql<number>`sum(case when ${agentRuns.status} = 'failed' then 1 else 0 end)`,
      durationMs: sql<number | null>`sum(${agentRuns.durationMs})`,
      costUsd: sql<number | null>`sum(${agentRuns.costUsd})`,
      tokensIn: sql<number | null>`sum(${agentRuns.tokensIn})`,
      tokensOut: sql<number | null>`sum(${agentRuns.tokensOut})`,
      tokensCached: sql<number | null>`sum(${agentRuns.tokensCached})`,
    })
    .from(agentRuns)
    .groupBy(agentRuns.agent)
    .all();

  return rows
    .map((row) => ({
      agent: row.agent,
      runs: Number(row.runs),
      failed: Number(row.failed),
      durationMs: Number(row.durationMs ?? 0),
      costUsd: Number(row.costUsd ?? 0),
      tokensIn: Number(row.tokensIn ?? 0),
      tokensOut: Number(row.tokensOut ?? 0),
      tokensCached: Number(row.tokensCached ?? 0),
    }))
    .sort((a, b) => b.durationMs - a.durationMs);
}

function loadToolRanking(db: Db): DebugToolRow[] {
  const rows = db
    .select({
      tool: toolCalls.tool,
      ok: toolCalls.ok,
      cacheHit: toolCalls.cacheHit,
      durationMs: toolCalls.durationMs,
      errorCode: toolCalls.errorCode,
    })
    .from(toolCalls)
    .all();

  const grouped = new Map<
    string,
    { durations: number[]; failures: number; cacheHits: number; errorCodes: Record<string, number> }
  >();
  for (const row of rows) {
    const bucket = grouped.get(row.tool) ?? {
      durations: [],
      failures: 0,
      cacheHits: 0,
      errorCodes: {},
    };
    bucket.durations.push(row.durationMs);
    if (!row.ok) bucket.failures += 1;
    if (row.cacheHit) bucket.cacheHits += 1;
    if (row.errorCode)
      bucket.errorCodes[row.errorCode] = (bucket.errorCodes[row.errorCode] ?? 0) + 1;
    grouped.set(row.tool, bucket);
  }

  return [...grouped.entries()]
    .map(([tool, bucket]) => {
      const calls = bucket.durations.length;
      return {
        tool,
        calls,
        failures: bucket.failures,
        failureRate: rate(bucket.failures, calls),
        p50Ms: percentile(bucket.durations, 50),
        p95Ms: percentile(bucket.durations, 95),
        cacheHits: bucket.cacheHits,
        cacheHitRate: rate(bucket.cacheHits, calls),
        errorCodes: bucket.errorCodes,
      };
    })
    .sort(
      (a, b) => (b.failureRate ?? -1) - (a.failureRate ?? -1) || (b.p95Ms ?? 0) - (a.p95Ms ?? 0),
    );
}

function loadModelRanking(db: Db): DebugModelRow[] {
  const rows = db
    .select({
      modelId: agentRuns.modelId,
      runs: sql<number>`count(*)`,
      durationMs: sql<number | null>`sum(${agentRuns.durationMs})`,
      costUsd: sql<number | null>`sum(${agentRuns.costUsd})`,
      tokensIn: sql<number | null>`sum(${agentRuns.tokensIn})`,
      tokensOut: sql<number | null>`sum(${agentRuns.tokensOut})`,
      tokensCached: sql<number | null>`sum(${agentRuns.tokensCached})`,
    })
    .from(agentRuns)
    .groupBy(agentRuns.modelId)
    .all();

  return rows
    .map((row) => ({
      modelId: row.modelId,
      runs: Number(row.runs),
      durationMs: Number(row.durationMs ?? 0),
      costUsd: Number(row.costUsd ?? 0),
      tokensIn: Number(row.tokensIn ?? 0),
      tokensOut: Number(row.tokensOut ?? 0),
      tokensCached: Number(row.tokensCached ?? 0),
    }))
    .sort((a, b) => b.costUsd - a.costUsd);
}
