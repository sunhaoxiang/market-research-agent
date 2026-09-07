/**
 * 数据库 Schema（DEVELOPMENT_PLAN.md §10）
 *
 * 可迁移性约束（SQLite → PostgreSQL），务必遵守：
 *   - 主键用 text 存 UUIDv7（时间有序 + 全局唯一）
 *   - 时间用 integer 存 Unix 毫秒（避免 SQLite 无 timestamp 类型与时区歧义）
 *   - 布尔用 integer + { mode: "boolean" }
 *   - JSON 用 text + { mode: "json" } + $type<T>()（PG 迁移时改 jsonb）
 *   - 枚举用 text + enum（TS 类型）+ CHECK 约束（DB 层强制），两者共用同一常量数组
 *   - 禁用 AUTOINCREMENT、rowid 依赖、INSERT OR REPLACE 等 SQLite 特有能力
 *   - 所有业务表带 user_id（MVP 恒为 'local'），为未来多用户预留
 */

import { sql } from "drizzle-orm";
import {
  check,
  index,
  integer,
  primaryKey,
  real,
  sqliteTable,
  text,
  uniqueIndex,
} from "drizzle-orm/sqlite-core";

// ─────────────────────────────────────────────────────────────────────────────
// 枚举常量（TS 类型与 DB CHECK 约束的唯一真源）
// ─────────────────────────────────────────────────────────────────────────────

export const SESSION_STATUSES = [
  "pending",
  "planning",
  "researching",
  "checking",
  "writing",
  "completed",
  "failed",
  "cancelled",
] as const;

export const QUESTION_TYPES = ["crypto", "stock", "macro", "compare", "generic"] as const;

export const EPISTEMIC_TYPES = [
  "fact",
  "source_backed_fact",
  "analysis",
  "inference",
  "prediction",
  "opinion",
] as const;

export const CONFIDENCE_LEVELS = ["high", "medium", "low"] as const;

export const VERIFICATION_STATUSES = [
  "unverified",
  "verified",
  "conflicting",
  "unsupported",
  "refuted",
] as const;

export const SOURCE_TYPES = [
  "web",
  "news",
  "official",
  "sec",
  "api",
  "docs",
  "github",
  "social",
] as const;

export const SOURCE_RELIABILITY = ["primary", "secondary", "aggregator", "unknown"] as const;

export const ASSET_TYPES = ["crypto", "stock"] as const;

export const AGENT_NAMES = [
  "research_manager",
  "crypto_research",
  "stock_research",
  "web_research",
  "fact_checker",
  "report_writer",
] as const;

export type SessionStatus = (typeof SESSION_STATUSES)[number];
export type QuestionType = (typeof QUESTION_TYPES)[number];
export type EpistemicType = (typeof EPISTEMIC_TYPES)[number];
export type ConfidenceLevel = (typeof CONFIDENCE_LEVELS)[number];
export type VerificationStatus = (typeof VERIFICATION_STATUSES)[number];
export type SourceType = (typeof SOURCE_TYPES)[number];
export type SourceReliability = (typeof SOURCE_RELIABILITY)[number];
export type AssetType = (typeof ASSET_TYPES)[number];
export type AgentName = (typeof AGENT_NAMES)[number];

/** 生成 `col IN ('a','b')` 形式的 CHECK 约束，与 TS enum 共用同一数组。 */
function oneOf(column: string, values: readonly string[]) {
  const list = values.map((v) => `'${v}'`).join(", ");
  return sql.raw(`"${column}" IN (${list})`);
}

// ─────────────────────────────────────────────────────────────────────────────
// JSON 列的类型（结构细节由 packages/shared 的生成类型约束，此处只做最小声明）
// ─────────────────────────────────────────────────────────────────────────────

export type TokenUsage = {
  input: number;
  output: number;
  cached: number;
  byAgent?: Record<string, { input: number; output: number }>;
};

export type ModelSnapshot = {
  modelId: string;
  provider: string;
  upstreamModel: string;
  adapter: string;
  temperature?: number;
  maxTokens?: number;
  capabilities: Record<string, unknown>;
  roleOverrides?: Record<string, string>;
};

export type ErrorPayload = {
  code: string;
  message: string;
  details?: Record<string, unknown>;
};

export type ReportSection = {
  id: string;
  title: string;
  markdown: string;
  claimIds: string[];
};

export type ReportMetadata = {
  reportSections: string[];
  generatedByModel: string;
  dataGaps: string[];
  conflicts?: { claimIds: string[]; description: string }[];
};

// ─────────────────────────────────────────────────────────────────────────────
// research_sessions
// ─────────────────────────────────────────────────────────────────────────────

export const researchSessions = sqliteTable(
  "research_sessions",
  {
    id: text("id").primaryKey(),
    userId: text("user_id").notNull().default("local"),
    question: text("question").notNull(),
    questionType: text("question_type", { enum: QUESTION_TYPES }),
    status: text("status", { enum: SESSION_STATUSES }).notNull().default("pending"),
    modelId: text("model_id").notNull(),
    /** 当次运行的完整模型配置，用于结果可复现（§14.2） */
    modelSnapshot: text("model_snapshot", { mode: "json" }).$type<ModelSnapshot>(),
    /** ResearchPlan，结构见 packages/shared */
    plan: text("plan", { mode: "json" }).$type<unknown>(),
    error: text("error", { mode: "json" }).$type<ErrorPayload>(),
    durationMs: integer("duration_ms"),
    tokenUsage: text("token_usage", { mode: "json" }).$type<TokenUsage>(),
    costUsd: real("cost_usd"),
    createdAt: integer("created_at").notNull(),
    startedAt: integer("started_at"),
    completedAt: integer("completed_at"),
  },
  (t) => [
    index("idx_sessions_user_created").on(t.userId, t.createdAt),
    index("idx_sessions_status").on(t.status),
    check("ck_sessions_status", oneOf("status", SESSION_STATUSES)),
  ],
);

// ─────────────────────────────────────────────────────────────────────────────
// research_events —— append-only。其余表可视为它的物化投影（§10.3）
// ─────────────────────────────────────────────────────────────────────────────

export const researchEvents = sqliteTable(
  "research_events",
  {
    id: text("id").primaryKey(),
    sessionId: text("session_id")
      .notNull()
      .references(() => researchSessions.id, { onDelete: "cascade" }),
    /** 会话内单调递增，用于顺序保证与断线重连（§12.1） */
    seq: integer("seq").notNull(),
    type: text("type").notNull(),
    agent: text("agent"),
    tool: text("tool"),
    taskId: text("task_id"),
    message: text("message"),
    payload: text("payload", { mode: "json" }).$type<unknown>(),
    createdAt: integer("created_at").notNull(),
  },
  (t) => [
    uniqueIndex("uq_events_session_seq").on(t.sessionId, t.seq),
    index("idx_events_session_type").on(t.sessionId, t.type),
  ],
);

// ─────────────────────────────────────────────────────────────────────────────
// research_reports
// ─────────────────────────────────────────────────────────────────────────────

export const researchReports = sqliteTable(
  "research_reports",
  {
    id: text("id").primaryKey(),
    sessionId: text("session_id")
      .notNull()
      .references(() => researchSessions.id, { onDelete: "cascade" }),
    title: text("title").notNull(),
    executiveSummary: text("executive_summary"),
    sections: text("sections", { mode: "json" }).$type<ReportSection[]>(),
    /** 渲染用的完整 Markdown（含 [n] 引用） */
    markdown: text("markdown").notNull(),
    metadata: text("metadata", { mode: "json" }).$type<ReportMetadata>(),
    createdAt: integer("created_at").notNull(),
  },
  (t) => [uniqueIndex("uq_reports_session").on(t.sessionId)],
);

// ─────────────────────────────────────────────────────────────────────────────
// sources
// ─────────────────────────────────────────────────────────────────────────────

export const sources = sqliteTable(
  "sources",
  {
    id: text("id").primaryKey(),
    sessionId: text("session_id")
      .notNull()
      .references(() => researchSessions.id, { onDelete: "cascade" }),
    url: text("url").notNull(),
    /** 归一化 URL，用于跨 Agent 去重（§15.1） */
    urlCanonical: text("url_canonical").notNull(),
    title: text("title"),
    domain: text("domain"),
    sourceType: text("source_type", { enum: SOURCE_TYPES }).notNull(),
    provider: text("provider"),
    reliability: text("reliability", { enum: SOURCE_RELIABILITY }).notNull().default("unknown"),
    publishedAt: integer("published_at"),
    retrievedAt: integer("retrieved_at").notNull(),
    /** 支撑证据片段，供 UI 悬浮预览与引用有效性校验 */
    excerpt: text("excerpt"),
    /** 报告中的引用序号 [n]；未被引用的来源为 null */
    citationIndex: integer("citation_index"),
    httpStatus: integer("http_status"),
  },
  (t) => [
    uniqueIndex("uq_sources_session_url").on(t.sessionId, t.urlCanonical),
    index("idx_sources_session").on(t.sessionId),
    check("ck_sources_type", oneOf("source_type", SOURCE_TYPES)),
    check("ck_sources_reliability", oneOf("reliability", SOURCE_RELIABILITY)),
  ],
);

// ─────────────────────────────────────────────────────────────────────────────
// claims —— Fact / Analysis / Prediction 分离的落地载体（§15.3）
// ─────────────────────────────────────────────────────────────────────────────

export const claims = sqliteTable(
  "claims",
  {
    id: text("id").primaryKey(),
    sessionId: text("session_id")
      .notNull()
      .references(() => researchSessions.id, { onDelete: "cascade" }),
    taskId: text("task_id"),
    agent: text("agent"),
    text: text("text").notNull(),
    epistemicType: text("epistemic_type", { enum: EPISTEMIC_TYPES }).notNull(),
    confidence: text("confidence", { enum: CONFIDENCE_LEVELS }).notNull(),
    /** 数据时点（不是抓取时点） */
    asOf: integer("as_of"),
    verification: text("verification", { enum: VERIFICATION_STATUSES })
      .notNull()
      .default("unverified"),
    verificationNote: text("verification_note"),
    citationIndex: integer("citation_index"),
    createdAt: integer("created_at").notNull(),
  },
  (t) => [
    index("idx_claims_session").on(t.sessionId),
    index("idx_claims_epistemic").on(t.sessionId, t.epistemicType),
    check("ck_claims_epistemic", oneOf("epistemic_type", EPISTEMIC_TYPES)),
    check("ck_claims_confidence", oneOf("confidence", CONFIDENCE_LEVELS)),
    check("ck_claims_verification", oneOf("verification", VERIFICATION_STATUSES)),
  ],
);

export const claimSources = sqliteTable(
  "claim_sources",
  {
    claimId: text("claim_id")
      .notNull()
      .references(() => claims.id, { onDelete: "cascade" }),
    sourceId: text("source_id")
      .notNull()
      .references(() => sources.id, { onDelete: "cascade" }),
  },
  (t) => [
    primaryKey({ columns: [t.claimId, t.sourceId] }),
    index("idx_claim_sources_source").on(t.sourceId),
  ],
);

// ─────────────────────────────────────────────────────────────────────────────
// 可观察性：tool_calls / agent_runs（§20.1）
// ─────────────────────────────────────────────────────────────────────────────

export const toolCalls = sqliteTable(
  "tool_calls",
  {
    id: text("id").primaryKey(),
    sessionId: text("session_id")
      .notNull()
      .references(() => researchSessions.id, { onDelete: "cascade" }),
    taskId: text("task_id"),
    agent: text("agent"),
    tool: text("tool").notNull(),
    provider: text("provider"),
    input: text("input", { mode: "json" }).$type<unknown>(),
    /** 只存摘要，不存全量 payload，避免库膨胀 */
    outputSummary: text("output_summary", { mode: "json" }).$type<unknown>(),
    ok: integer("ok", { mode: "boolean" }).notNull(),
    errorCode: text("error_code"),
    cacheHit: integer("cache_hit", { mode: "boolean" }).notNull().default(false),
    durationMs: integer("duration_ms").notNull(),
    createdAt: integer("created_at").notNull(),
  },
  (t) => [
    index("idx_tool_calls_session").on(t.sessionId),
    // 支撑 /debug 的「哪个 Tool 最容易失败」
    index("idx_tool_calls_tool_ok").on(t.tool, t.ok),
  ],
);

export const agentRuns = sqliteTable(
  "agent_runs",
  {
    id: text("id").primaryKey(),
    sessionId: text("session_id")
      .notNull()
      .references(() => researchSessions.id, { onDelete: "cascade" }),
    taskId: text("task_id"),
    agent: text("agent", { enum: AGENT_NAMES }).notNull(),
    modelId: text("model_id").notNull(),
    status: text("status").notNull(),
    /** prompt 只记 hash 与长度，绝不记全文（§20.3） */
    promptHash: text("prompt_hash"),
    promptChars: integer("prompt_chars"),
    tokensIn: integer("tokens_in"),
    tokensOut: integer("tokens_out"),
    tokensCached: integer("tokens_cached"),
    costUsd: real("cost_usd"),
    durationMs: integer("duration_ms"),
    error: text("error", { mode: "json" }).$type<ErrorPayload>(),
    createdAt: integer("created_at").notNull(),
  },
  (t) => [
    index("idx_agent_runs_session").on(t.sessionId),
    index("idx_agent_runs_agent").on(t.agent),
    check("ck_agent_runs_agent", oneOf("agent", AGENT_NAMES)),
  ],
);

// ─────────────────────────────────────────────────────────────────────────────
// settings / watchlist
// ─────────────────────────────────────────────────────────────────────────────

export const settings = sqliteTable("settings", {
  key: text("key").primaryKey(),
  userId: text("user_id").notNull().default("local"),
  value: text("value", { mode: "json" }).$type<unknown>(),
  updatedAt: integer("updated_at").notNull(),
});

export const watchlist = sqliteTable(
  "watchlist",
  {
    id: text("id").primaryKey(),
    userId: text("user_id").notNull().default("local"),
    assetType: text("asset_type", { enum: ASSET_TYPES }).notNull(),
    symbol: text("symbol").notNull(),
    displayName: text("display_name"),
    notes: text("notes"),
    createdAt: integer("created_at").notNull(),
  },
  (t) => [
    uniqueIndex("uq_watchlist_user_asset").on(t.userId, t.assetType, t.symbol),
    check("ck_watchlist_asset_type", oneOf("asset_type", ASSET_TYPES)),
  ],
);

// ─────────────────────────────────────────────────────────────────────────────
// 推导类型
// ─────────────────────────────────────────────────────────────────────────────

export type ResearchSession = typeof researchSessions.$inferSelect;
export type NewResearchSession = typeof researchSessions.$inferInsert;
export type ResearchEvent = typeof researchEvents.$inferSelect;
export type NewResearchEvent = typeof researchEvents.$inferInsert;
export type ResearchReport = typeof researchReports.$inferSelect;
export type NewResearchReport = typeof researchReports.$inferInsert;
export type Source = typeof sources.$inferSelect;
export type NewSource = typeof sources.$inferInsert;
export type Claim = typeof claims.$inferSelect;
export type NewClaim = typeof claims.$inferInsert;
export type ToolCall = typeof toolCalls.$inferSelect;
export type NewToolCall = typeof toolCalls.$inferInsert;
export type AgentRun = typeof agentRuns.$inferSelect;
export type NewAgentRun = typeof agentRuns.$inferInsert;
export type WatchlistItem = typeof watchlist.$inferSelect;
