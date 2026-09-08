/**
 * research_session 相关查询。
 *
 * 所有 DB 访问都收敛到 db/queries/ 下，便于将来迁移 PostgreSQL 时集中修改（§25.3）。
 */

import { and, desc, eq, inArray, sql, type SQL } from "drizzle-orm";

import type { Db } from "@/db/client";
import {
  type NewAgentRun,
  type NewResearchEvent,
  type NewResearchSession,
  type NewToolCall,
  type QuestionType,
  type ResearchSession,
  type SessionStatus,
  agentRuns,
  researchEvents,
  researchSessions,
  sources,
  toolCalls,
} from "@/db/schema";

export const LOCAL_USER_ID = "local";

export function createSession(db: Db, input: NewResearchSession): ResearchSession {
  const [row] = db.insert(researchSessions).values(input).returning().all();
  if (!row) {
    throw new Error("创建 research_session 失败");
  }
  return row;
}

export function getSession(db: Db, id: string): ResearchSession | undefined {
  return db.select().from(researchSessions).where(eq(researchSessions.id, id)).get();
}

export function listSessions(
  db: Db,
  {
    userId = LOCAL_USER_ID,
    limit = 50,
    offset = 0,
    status,
    questionType,
    modelId,
  }: ListSessionsOptions = {},
): ResearchSession[] {
  return db
    .select()
    .from(researchSessions)
    .where(sessionFilters({ userId, status, questionType, modelId }))
    .orderBy(desc(researchSessions.createdAt))
    .limit(limit)
    .offset(offset)
    .all();
}

export function countSessions(
  db: Db,
  { userId = LOCAL_USER_ID, status, questionType, modelId }: ListSessionsOptions = {},
): number {
  const row = db
    .select({ total: sql<number>`count(*)` })
    .from(researchSessions)
    .where(sessionFilters({ userId, status, questionType, modelId }))
    .get();
  return Number(row?.total ?? 0);
}

export function countSourcesBySession(db: Db, sessionIds: string[]): Record<string, number> {
  if (sessionIds.length === 0) return {};
  const rows = db
    .select({
      sessionId: sources.sessionId,
      total: sql<number>`count(*)`,
    })
    .from(sources)
    .where(inArray(sources.sessionId, sessionIds))
    .groupBy(sources.sessionId)
    .all();
  return Object.fromEntries(rows.map((row) => [row.sessionId, Number(row.total)]));
}

export type ListSessionsOptions = {
  userId?: string;
  limit?: number;
  offset?: number;
  status?: SessionStatus;
  questionType?: QuestionType;
  modelId?: string;
};

function sessionFilters({
  userId,
  status,
  questionType,
  modelId,
}: Pick<ListSessionsOptions, "userId" | "status" | "questionType" | "modelId">): SQL {
  const parts: SQL[] = [eq(researchSessions.userId, userId ?? LOCAL_USER_ID)];
  if (status) parts.push(eq(researchSessions.status, status));
  if (questionType) parts.push(eq(researchSessions.questionType, questionType));
  if (modelId) parts.push(eq(researchSessions.modelId, modelId));
  return and(...parts)!;
}

export function updateSessionStatus(
  db: Db,
  id: string,
  status: SessionStatus,
  patch: Partial<
    Pick<ResearchSession, "completedAt" | "durationMs" | "error" | "costUsd" | "tokenUsage">
  > = {},
): void {
  db.update(researchSessions)
    .set({ status, ...patch })
    .where(eq(researchSessions.id, id))
    .run();
}

/**
 * 更新研究过程中才知道的会话级字段。
 *
 * 与 `updateSessionStatus` 分开：状态机的推进和"补充元数据"是两件事，
 * 混在一个函数里会让调用方为了写 plan 而必须传一个状态，进而出现
 * 用当前状态覆盖当前状态这种无意义的写。
 */
export function patchSession(
  db: Db,
  id: string,
  patch: Partial<Pick<ResearchSession, "questionType" | "plan" | "modelId" | "tokenUsage">>,
): void {
  db.update(researchSessions).set(patch).where(eq(researchSessions.id, id)).run();
}

/**
 * 批量写入事件。
 *
 * 单事务插入，避免逐条事务的 fsync 开销（§10.3）。事件流按 200ms 或 20 条触发。
 */
export function appendEvents(db: Db, events: NewResearchEvent[]): void {
  if (events.length === 0) return;
  db.transaction((tx) => {
    tx.insert(researchEvents).values(events).run();
  });
}

/**
 * 一批事件连同它们派生出的指标行，单事务写入。
 *
 * 三张表放在同一个事务里，是为了让"事件已落库但指标没落"这种状态不可能出现——
 * 否则 `/debug` 里的成本会和事件流对不上，而这种偏差没有任何办法事后修正
 * （原始 usage 只在事件的 payload 里）。
 */
export function appendEventsWithMetrics(
  db: Db,
  events: NewResearchEvent[],
  runs: NewAgentRun[],
  calls: NewToolCall[],
): void {
  if (events.length === 0 && runs.length === 0 && calls.length === 0) return;
  db.transaction((tx) => {
    if (events.length > 0) tx.insert(researchEvents).values(events).run();
    if (runs.length > 0) tx.insert(agentRuns).values(runs).run();
    if (calls.length > 0) tx.insert(toolCalls).values(calls).run();
  });
}

export function listEvents(db: Db, sessionId: string, afterSeq = -1) {
  return db
    .select()
    .from(researchEvents)
    .where(sql`${researchEvents.sessionId} = ${sessionId} AND ${researchEvents.seq} > ${afterSeq}`)
    .orderBy(researchEvents.seq)
    .all();
}
