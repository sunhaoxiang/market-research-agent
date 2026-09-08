/* eslint-disable */
/**
 * 由 scripts/gen-types.sh 生成，请勿手改。
 * 真源：services/agent/src/agent_service/schemas/
 * 修改模型后运行 `pnpm gen:types` 重新生成并提交。
 */

export type ResearchEvent =
  | SessionStartedEvent
  | SessionCompletedEvent
  | SessionFailedEvent
  | SessionCancelledEvent
  | IntentClassifiedEvent
  | PlanCreatedEvent
  | PlanUpdatedEvent
  | StageChangedEvent
  | AgentStartedEvent
  | AgentProgressEvent
  | AgentReasoningEvent
  | AgentCompletedEvent
  | AgentFailedEvent
  | AgentHandoffEvent
  | ToolStartedEvent
  | ToolCompletedEvent
  | ToolFailedEvent
  | SourceFoundEvent
  | MetricFoundEvent
  | FactCheckStartedEvent
  | FactCheckProgressEvent
  | ClaimVerifiedEvent
  | ConflictDetectedEvent
  | ReportStartedEvent
  | ReportSectionDeltaEvent
  | ReportCompletedEvent
  | UsageUpdatedEvent
  | AgentRunMetricsEvent
  | WarningEvent
  | HeartbeatEvent;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq = number;
export type SessionId = string;
export type Ts = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message = string | null;
export type Type = "session_started";
export type Question = string;
export type ModelId = string;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq1 = number;
export type SessionId1 = string;
export type Ts1 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message1 = string | null;
export type Type1 = "session_completed";
export type DurationMs = number;
export type Input = number;
export type Output = number;
export type Cached = number;
export type CostUsd = number | null;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq2 = number;
export type SessionId2 = string;
export type Ts2 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message2 = string | null;
export type Type2 = "session_failed";
export type Code = string;
export type Message3 = string;
/**
 * 研究流程的四个阶段（§12.2 STAGE_CHANGED）。
 */
export type Stage = "planning" | "researching" | "checking" | "writing";
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq3 = number;
export type SessionId3 = string;
export type Ts3 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message4 = string | null;
export type Type3 = "session_cancelled";
export type Payload = null;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq4 = number;
export type SessionId4 = string;
export type Ts4 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message5 = string | null;
export type Type4 = "intent_classified";
export type QuestionType = "crypto" | "stock" | "macro" | "compare" | "generic";
export type AssetType = "crypto" | "stock";
/**
 * 标准化代号，如 NVDA / BTC / HYPE
 */
export type Symbol = string;
/**
 * 全称，如 NVIDIA Corporation
 */
export type Name = string | null;
/**
 * 仅 crypto：所在链，如 ethereum / solana / hyperliquid
 */
export type Chain = string | null;
/**
 * 仅 crypto：合约地址（用于链上数据查询）
 */
export type ContractAddress = string | null;
export type Entities = Entity[];
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq5 = number;
export type SessionId5 = string;
export type Ts5 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message6 = string | null;
export type Type5 = "plan_created";
/**
 * Agent 对问题的理解，展示给用户以便及早发现偏差
 */
export type Interpretation = string;
export type Entities1 = Entity[];
/**
 * 计划内唯一，建议 t1/t2/…（供 depends_on 引用）
 */
export type Id = string;
/**
 * 六个 Agent（§6.2）。裁剪理由见 §6.1。
 */
export type AgentName =
  "research_manager" | "crypto_research" | "stock_research" | "web_research" | "fact_checker" | "report_writer";
/**
 * 给子 Agent 的具体目标，需自包含
 */
export type Objective = string;
export type Entities2 = Entity[];
/**
 * 建议使用的工具，非强制——Agent 可自行判断
 */
export type SuggestedTools = string[];
/**
 * 依赖的任务 id。用于分层 fan-out，必须无环
 */
export type DependsOn = string[];
export type Priority = number;
export type Tasks = ResearchTask[];
/**
 * 动态报告结构。单标的深研用完整章节；"为什么今天涨"用 Overview/Catalysts/Analysis/Risks 精简结构
 */
export type ReportSections = string[];
/**
 * 规划时做的假设，需在报告中披露
 */
export type Assumptions = string[];
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq6 = number;
export type SessionId6 = string;
export type Ts6 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message7 = string | null;
export type Type6 = "plan_updated";
export type AddedTasks = ResearchTask[];
export type Reason = string | null;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq7 = number;
export type SessionId7 = string;
export type Ts7 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message8 = string | null;
export type Type7 = "stage_changed";
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq8 = number;
export type SessionId8 = string;
export type Ts8 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message9 = string | null;
export type Type8 = "agent_started";
/**
 * 六个 Agent（§6.2）。裁剪理由见 §6.1。
 */
export type AgentName1 =
  "research_manager" | "crypto_research" | "stock_research" | "web_research" | "fact_checker" | "report_writer";
export type TaskId = string;
export type Objective1 = string;
export type ModelId1 = string;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq9 = number;
export type SessionId9 = string;
export type Ts9 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message10 = string | null;
export type Type9 = "agent_progress";
export type TaskId1 = string;
export type Message11 = string;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq10 = number;
export type SessionId10 = string;
export type Ts10 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message12 = string | null;
export type Type10 = "agent_reasoning";
export type TaskId2 = string | null;
export type Summary = string;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq11 = number;
export type SessionId11 = string;
export type Ts11 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message13 = string | null;
export type Type11 = "agent_completed";
export type TaskId3 = string;
export type Summary1 = string;
export type ClaimCount = number;
export type SourceCount = number;
export type DurationMs1 = number;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq12 = number;
export type SessionId12 = string;
export type Ts12 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message14 = string | null;
export type Type12 = "agent_failed";
export type TaskId4 = string;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq13 = number;
export type SessionId13 = string;
export type Ts13 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message15 = string | null;
export type Type13 = "agent_handoff";
export type Reason1 = string | null;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq14 = number;
export type SessionId14 = string;
export type Ts14 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message16 = string | null;
export type Type14 = "tool_started";
export type CallId = string;
export type Tool = string;
export type TaskId5 = string | null;
/**
 * 摘要而非完整入参，避免事件流膨胀
 */
export type InputSummary = string | null;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq15 = number;
export type SessionId15 = string;
export type Ts15 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message17 = string | null;
export type Type15 = "tool_completed";
export type CallId1 = string;
export type Tool1 = string;
export type Ok = boolean;
export type Provider = string | null;
export type CacheHit = boolean;
export type DurationMs2 = number;
export type ResultSummary = string | null;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq16 = number;
export type SessionId16 = string;
export type Ts16 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message18 = string | null;
export type Type16 = "tool_failed";
export type CallId2 = string;
export type Tool2 = string;
/**
 * §8.2。UNSUPPORTED / QUOTA_EXHAUSTED 必须与 NOT_FOUND 区分：
 * 前两者应产生 data gap，后者可能意味着标的名解析错误、值得重试。
 */
export type ToolErrorCode =
  | "not_found"
  | "rate_limited"
  | "quota_exhausted"
  | "timeout"
  | "upstream_error"
  | "invalid_input"
  | "unsupported"
  | "blocked"
  | "parse_error";
export type Message19 = string;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq17 = number;
export type SessionId17 = string;
export type Ts17 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message20 = string | null;
export type Type17 = "source_found";
export type Id1 = string;
/**
 * 会话内短引用，如 s1。LLM 在 claim 中用它指代本来源
 */
export type Ref = string;
export type Url = string;
/**
 * 归一化 URL，用于跨 Agent 去重（§15.1）
 */
export type UrlCanonical = string;
export type Title = string | null;
export type Domain = string | null;
export type SourceType = "web" | "news" | "official" | "sec" | "api" | "docs" | "github" | "social";
/**
 * coingecko / defillama / tavily …
 */
export type Provider1 = string | null;
/**
 * 来源可靠性分级（§15.2）。冲突时优先 primary。
 */
export type SourceReliability = "primary" | "secondary" | "aggregator" | "unknown";
export type PublishedAt = string | null;
export type RetrievedAt = string;
/**
 * 支撑证据片段，供 UI 悬浮预览与引用有效性校验
 */
export type Excerpt = string | null;
/**
 * 报告中的引用序号 [n]。未被引用的来源为 None（§15.4-3）
 */
export type CitationIndex = number | null;
/**
 * 抓取时的 HTTP 状态，404 标记为不可用
 */
export type HttpStatus = number | null;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq18 = number;
export type SessionId18 = string;
export type Ts18 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message21 = string | null;
export type Type18 = "metric_found";
/**
 * 机器可读指标名，snake_case，如 tvl / market_cap / pe_ratio
 */
export type Name1 = string;
/**
 * 人类可读标签，如 TVL / 市值 / 市盈率
 */
export type Label = string;
export type Value = number;
/**
 * USD / % / 倍 等
 */
export type Unit = string | null;
/**
 * 所属标的，便于前端分组
 */
export type EntitySymbol = string | null;
/**
 * 数据时点，不是抓取时点
 */
export type AsOf = string | null;
/**
 * 来源短引用（如 s1），由编排层解析为 source_id
 */
export type SourceRef = string | null;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq19 = number;
export type SessionId19 = string;
export type Ts19 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message22 = string | null;
export type Type19 = "fact_check_started";
export type ClaimCount1 = number;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq20 = number;
export type SessionId20 = string;
export type Ts20 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message23 = string | null;
export type Type20 = "fact_check_progress";
export type Checked = number;
export type Total = number;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq21 = number;
export type SessionId21 = string;
export type Ts21 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message24 = string | null;
export type Type21 = "claim_verified";
export type ClaimId = string;
export type VerificationStatus = "unverified" | "verified" | "conflicting" | "unsupported" | "refuted";
export type Note = string | null;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq22 = number;
export type SessionId22 = string;
export type Ts22 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message25 = string | null;
export type Type22 = "conflict_detected";
export type ClaimIds = string[];
export type Description = string;
/**
 * 各来源给出的不同值，用于 UI 并列展示
 */
export type Values = string[];
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq23 = number;
export type SessionId23 = string;
export type Ts23 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message26 = string | null;
export type Type23 = "report_started";
export type Payload1 = null;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq24 = number;
export type SessionId24 = string;
export type Ts24 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message27 = string | null;
export type Type24 = "report_section_delta";
export type SectionId = string;
export type Text = string;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq25 = number;
export type SessionId25 = string;
export type Ts25 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message28 = string | null;
export type Type25 = "report_completed";
export type Title1 = string;
/**
 * 结论先行，3-5 句
 */
export type ExecutiveSummary = string;
/**
 * 章节标识，对应 ResearchPlan.report_sections 中的项
 */
export type Id2 = string;
export type Title2 = string;
/**
 * 章节正文。引用用 [n] 形式，n 对应 Source.citation_index
 */
export type Markdown = string;
/**
 * 本章节引用的 claim，供 UI 反查与引用完整性校验
 */
export type ClaimIds1 = string[];
export type Sections = ReportSection[];
/**
 * 汇总各任务的 data_gaps，进入报告的「数据限制」章节
 */
export type DataGaps = string[];
export type Sources = Source[];
export type Id3 = string;
export type Text1 = string;
/**
 * 认知类型（§15.3）。事实与推测分离的载体。
 *
 * 报告主体应以 SOURCE_BACKED_FACT 为主；ANALYSIS / INFERENCE / PREDICTION
 * 必须出现在带标记的段落中，不得与事实段落混排。
 */
export type EpistemicType = "fact" | "source_backed_fact" | "analysis" | "inference" | "prediction" | "opinion";
export type ConfidenceLevel = "high" | "medium" | "low";
/**
 * 指向 Source.id
 */
export type SourceIds = string[];
export type AsOf1 = string | null;
export type TaskId6 = string | null;
export type Agent = string | null;
export type VerificationStatus1 = "unverified" | "verified" | "conflicting" | "unsupported" | "refuted";
export type VerificationNote = string | null;
export type CitationIndex1 = number | null;
/**
 * 各任务的陈述，供报告按 section.claim_ids 显示认知类型徽标
 */
export type Claims = Claim[];
export type CitationCount = number;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq26 = number;
export type SessionId26 = string;
export type Ts26 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message29 = string | null;
export type Type26 = "usage_updated";
export type CostUsd1 = number | null;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq27 = number;
export type SessionId27 = string;
export type Ts27 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message30 = string | null;
export type Type27 = "agent_run_metrics";
export type TaskId7 = string | null;
export type ModelId2 = string;
export type TaskStatus = "pending" | "running" | "completed" | "failed" | "skipped";
export type Hash = string;
export type Chars = number;
export type CostUsd2 = number | null;
export type DurationMs3 = number;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq28 = number;
export type SessionId28 = string;
export type Ts28 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message31 = string | null;
export type Type28 = "warning";
export type Code1 = string;
export type Message32 = string;
/**
 * 会话内单调递增，用于顺序保证与断线重连
 */
export type Seq29 = number;
export type SessionId29 = string;
export type Ts29 = string;
/**
 * 面向用户的一句话，前端可直接显示（§12.3）
 */
export type Message33 = string | null;
export type Type29 = "heartbeat";
export type Payload2 = null;
/**
 * 一句自包含的陈述，不依赖上下文即可理解
 */
export type Text2 = string;
/**
 * 支撑本陈述的来源短引用，如 ['s1', 's3']。只能引用工具结果中出现过的 ref
 */
export type SourceRefs = string[];
/**
 * 数据时点（不是抓取时点）。金融数据必须填
 */
export type AsOf2 = string | null;
export type ClaimId1 = string;
/**
 * 裁定理由，冲突时需说明取舍依据
 */
export type Note1 = string | null;
/**
 * 核查过程中新增的支撑来源
 */
export type AdditionalSourceRefs = string[];
/**
 * 本任务发现的要点，2-4 句
 */
export type Summary2 = string;
export type Claims1 = ClaimDraft[];
export type Metrics = MetricPoint[];
/**
 * 明确声明拿不到什么数据。这是反幻觉的关键设计——强制显式声明缺失，而不是用推测填补空白
 */
export type DataGaps1 = string[];
export type TaskId8 = string;
export type Summary3 = string;
export type Claims2 = Claim[];
export type Sources1 = Source[];
export type Metrics1 = MetricPoint[];
export type DataGaps2 = string[];
/**
 * 面向 LLM 的说明，需可据此决策；不含栈信息
 */
export type Message34 = string;
export type Tool3 = string | null;
export type Provider2 = string | null;
export type Retryable = boolean;
export type ToolErrors = ToolError[];
export type Verifications = ClaimVerification[];
export type Conflicts = Conflict[];
export type Notes = string[];
export type ReportSections1 = string[];
export type GeneratedByModel = string;
export type DataGaps3 = string[];
export type CitationCheckPassed = boolean;
export type CitationWarnings = string[];
/**
 * coingecko / defillama / fmp / sec / tavily
 */
export type Provider3 = string;
export type Endpoint = string;
/**
 * 人类可访问的 URL，用于 Citation
 */
export type SourceUrl = string | null;
/**
 * 抓取时间
 */
export type RetrievedAt1 = string;
/**
 * 数据本身的时点
 */
export type AsOf3 = string | null;
export type IsCached = boolean;
export type CacheAgeS = number | null;
export type Completeness = "full" | "partial";
export type MissingFields = string[];
/**
 * 如 "FDV 基于最大供应量估算"
 */
export type Caveats = string[];

/**
 * 由 services/agent 的 Pydantic 模型生成，请勿手改。真源见 agent_service/schemas/，改动后运行 pnpm gen:types。
 */
export interface MraSharedTypes {
  ResearchEvent: ResearchEvent;
  ResearchPlan: ResearchPlan;
  ResearchTask: ResearchTask;
  Entity: Entity;
  MetricPoint: MetricPoint;
  Source: Source;
  Claim: Claim;
  ClaimDraft: ClaimDraft;
  ClaimVerification: ClaimVerification;
  Conflict: Conflict;
  AgentFinding: AgentFinding;
  ResearchFinding: ResearchFinding;
  FactCheckResult: FactCheckResult;
  ResearchReport: ResearchReport;
  ReportSection: ReportSection;
  ReportMetadata: ReportMetadata;
  ToolError: ToolError;
  DataProvenance: DataProvenance;
  DataQuality: DataQuality;
  ErrorInfo: ErrorInfo;
  TokenUsage: TokenUsage;
}
export interface SessionStartedEvent {
  seq: Seq;
  session_id: SessionId;
  ts: Ts;
  message: Message;
  type: Type;
  payload: SessionStartedPayload;
}
export interface SessionStartedPayload {
  question: Question;
  model_id: ModelId;
}
export interface SessionCompletedEvent {
  seq: Seq1;
  session_id: SessionId1;
  ts: Ts1;
  message: Message1;
  type: Type1;
  payload: SessionCompletedPayload;
}
export interface SessionCompletedPayload {
  duration_ms: DurationMs;
  usage: TokenUsage;
  cost_usd: CostUsd;
}
export interface TokenUsage {
  input: Input;
  output: Output;
  cached: Cached;
}
export interface SessionFailedEvent {
  seq: Seq2;
  session_id: SessionId2;
  ts: Ts2;
  message: Message2;
  type: Type2;
  payload: SessionFailedPayload;
}
export interface SessionFailedPayload {
  error: ErrorInfo;
  stage: Stage | null;
}
export interface ErrorInfo {
  code: Code;
  message: Message3;
}
export interface SessionCancelledEvent {
  seq: Seq3;
  session_id: SessionId3;
  ts: Ts3;
  message: Message4;
  type: Type3;
  payload: Payload;
}
export interface IntentClassifiedEvent {
  seq: Seq4;
  session_id: SessionId4;
  ts: Ts4;
  message: Message5;
  type: Type4;
  payload: IntentClassifiedPayload;
}
export interface IntentClassifiedPayload {
  question_type: QuestionType;
  entities: Entities;
}
/**
 * 已解析的研究标的。
 *
 * 由 Research Manager 在规划阶段解析（"英伟达" → `{stock, NVDA}`），
 * 后续所有 Agent 与 Tool 都基于此结构，避免各自重复做名称解析。
 */
export interface Entity {
  type: AssetType;
  symbol: Symbol;
  name: Name;
  chain: Chain;
  contract_address: ContractAddress;
}
export interface PlanCreatedEvent {
  seq: Seq5;
  session_id: SessionId5;
  ts: Ts5;
  message: Message6;
  type: Type5;
  payload: PlanCreatedPayload;
}
export interface PlanCreatedPayload {
  plan: ResearchPlan;
}
export interface ResearchPlan {
  question_type: QuestionType;
  interpretation: Interpretation;
  entities: Entities1;
  tasks: Tasks;
  report_sections: ReportSections;
  assumptions: Assumptions;
}
export interface ResearchTask {
  id: Id;
  agent: AgentName;
  objective: Objective;
  entities: Entities2;
  suggested_tools: SuggestedTools;
  depends_on: DependsOn;
  priority: Priority;
}
export interface PlanUpdatedEvent {
  seq: Seq6;
  session_id: SessionId6;
  ts: Ts6;
  message: Message7;
  type: Type6;
  payload: PlanUpdatedPayload;
}
export interface PlanUpdatedPayload {
  added_tasks: AddedTasks;
  reason: Reason;
}
export interface StageChangedEvent {
  seq: Seq7;
  session_id: SessionId7;
  ts: Ts7;
  message: Message8;
  type: Type7;
  payload: StageChangedPayload;
}
export interface StageChangedPayload {
  stage: Stage;
  previous: Stage | null;
}
export interface AgentStartedEvent {
  seq: Seq8;
  session_id: SessionId8;
  ts: Ts8;
  message: Message9;
  type: Type8;
  payload: AgentStartedPayload;
}
export interface AgentStartedPayload {
  agent: AgentName1;
  task_id: TaskId;
  objective: Objective1;
  model_id: ModelId1;
}
export interface AgentProgressEvent {
  seq: Seq9;
  session_id: SessionId9;
  ts: Ts9;
  message: Message10;
  type: Type9;
  payload: AgentProgressPayload;
}
export interface AgentProgressPayload {
  agent: AgentName1;
  task_id: TaskId1;
  message: Message11;
}
export interface AgentReasoningEvent {
  seq: Seq10;
  session_id: SessionId10;
  ts: Ts10;
  message: Message12;
  type: Type10;
  payload: AgentReasoningPayload;
}
export interface AgentReasoningPayload {
  agent: AgentName1;
  task_id: TaskId2;
  summary: Summary;
}
export interface AgentCompletedEvent {
  seq: Seq11;
  session_id: SessionId11;
  ts: Ts11;
  message: Message13;
  type: Type11;
  payload: AgentCompletedPayload;
}
export interface AgentCompletedPayload {
  agent: AgentName1;
  task_id: TaskId3;
  summary: Summary1;
  claim_count: ClaimCount;
  source_count: SourceCount;
  duration_ms: DurationMs1;
}
export interface AgentFailedEvent {
  seq: Seq12;
  session_id: SessionId12;
  ts: Ts12;
  message: Message14;
  type: Type12;
  payload: AgentFailedPayload;
}
export interface AgentFailedPayload {
  agent: AgentName1;
  task_id: TaskId4;
  error: ErrorInfo;
}
export interface AgentHandoffEvent {
  seq: Seq13;
  session_id: SessionId13;
  ts: Ts13;
  message: Message15;
  type: Type13;
  payload: AgentHandoffPayload;
}
export interface AgentHandoffPayload {
  from_agent: AgentName1;
  to_agent: AgentName1;
  reason: Reason1;
}
export interface ToolStartedEvent {
  seq: Seq14;
  session_id: SessionId14;
  ts: Ts14;
  message: Message16;
  type: Type14;
  payload: ToolStartedPayload;
}
export interface ToolStartedPayload {
  call_id: CallId;
  tool: Tool;
  agent: AgentName1;
  task_id: TaskId5;
  input_summary: InputSummary;
}
export interface ToolCompletedEvent {
  seq: Seq15;
  session_id: SessionId15;
  ts: Ts15;
  message: Message17;
  type: Type15;
  payload: ToolCompletedPayload;
}
export interface ToolCompletedPayload {
  call_id: CallId1;
  tool: Tool1;
  ok: Ok;
  provider: Provider;
  cache_hit: CacheHit;
  duration_ms: DurationMs2;
  result_summary: ResultSummary;
}
export interface ToolFailedEvent {
  seq: Seq16;
  session_id: SessionId16;
  ts: Ts16;
  message: Message18;
  type: Type16;
  payload: ToolFailedPayload;
}
export interface ToolFailedPayload {
  call_id: CallId2;
  tool: Tool2;
  error_code: ToolErrorCode;
  message: Message19;
}
export interface SourceFoundEvent {
  seq: Seq17;
  session_id: SessionId17;
  ts: Ts17;
  message: Message20;
  type: Type17;
  payload: SourceFoundPayload;
}
export interface SourceFoundPayload {
  source: Source;
}
/**
 * 一个可引用的信息来源。
 *
 * API 类数据源也是 Source（provider + endpoint + 人类可访问 URL），
 * 这样"HYPE TVL 增长 18%"这种来自 API 的数字同样可溯源。
 */
export interface Source {
  id: Id1;
  ref: Ref;
  url: Url;
  url_canonical: UrlCanonical;
  title: Title;
  domain: Domain;
  source_type: SourceType;
  provider: Provider1;
  reliability: SourceReliability;
  published_at: PublishedAt;
  retrieved_at: RetrievedAt;
  excerpt: Excerpt;
  citation_index: CitationIndex;
  http_status: HttpStatus;
}
export interface MetricFoundEvent {
  seq: Seq18;
  session_id: SessionId18;
  ts: Ts18;
  message: Message21;
  type: Type18;
  payload: MetricFoundPayload;
}
export interface MetricFoundPayload {
  metric: MetricPoint;
}
/**
 * 一个结构化数值观测点，供前端画图（§6.2）。
 *
 * 刻意与 Claim 分开：Claim 是自然语言陈述，MetricPoint 是机器可读的数字。
 * 同一事实通常同时产生两者——"TVL 达到 12 亿美元" 与 `{name: tvl, value: 1.2e9}`。
 * 时间序列表示为 `name` 相同、`as_of` 不同的多个点。
 */
export interface MetricPoint {
  name: Name1;
  label: Label;
  value: Value;
  unit: Unit;
  entity_symbol: EntitySymbol;
  as_of: AsOf;
  source_ref: SourceRef;
}
export interface FactCheckStartedEvent {
  seq: Seq19;
  session_id: SessionId19;
  ts: Ts19;
  message: Message22;
  type: Type19;
  payload: FactCheckStartedPayload;
}
export interface FactCheckStartedPayload {
  claim_count: ClaimCount1;
}
export interface FactCheckProgressEvent {
  seq: Seq20;
  session_id: SessionId20;
  ts: Ts20;
  message: Message23;
  type: Type20;
  payload: FactCheckProgressPayload;
}
export interface FactCheckProgressPayload {
  checked: Checked;
  total: Total;
}
export interface ClaimVerifiedEvent {
  seq: Seq21;
  session_id: SessionId21;
  ts: Ts21;
  message: Message24;
  type: Type21;
  payload: ClaimVerifiedPayload;
}
export interface ClaimVerifiedPayload {
  claim_id: ClaimId;
  verification: VerificationStatus;
  note: Note;
}
export interface ConflictDetectedEvent {
  seq: Seq22;
  session_id: SessionId22;
  ts: Ts22;
  message: Message25;
  type: Type22;
  payload: ConflictDetectedPayload;
}
export interface ConflictDetectedPayload {
  conflict: Conflict;
}
/**
 * 多来源冲突（§12.2 CONFLICT_DETECTED）。
 */
export interface Conflict {
  claim_ids: ClaimIds;
  description: Description;
  values: Values;
}
export interface ReportStartedEvent {
  seq: Seq23;
  session_id: SessionId23;
  ts: Ts23;
  message: Message26;
  type: Type23;
  payload: Payload1;
}
export interface ReportSectionDeltaEvent {
  seq: Seq24;
  session_id: SessionId24;
  ts: Ts24;
  message: Message27;
  type: Type24;
  payload: ReportSectionDeltaPayload;
}
export interface ReportSectionDeltaPayload {
  section_id: SectionId;
  text: Text;
}
export interface ReportCompletedEvent {
  seq: Seq25;
  session_id: SessionId25;
  ts: Ts25;
  message: Message28;
  type: Type25;
  payload: ReportCompletedPayload;
}
export interface ReportCompletedPayload {
  report: ResearchReport;
  sources: Sources;
  claims: Claims;
  citation_count: CitationCount;
}
/**
 * Report Writer 的 `output_type`。
 */
export interface ResearchReport {
  title: Title1;
  executive_summary: ExecutiveSummary;
  sections: Sections;
  data_gaps: DataGaps;
}
export interface ReportSection {
  id: Id2;
  title: Title2;
  markdown: Markdown;
  claim_ids: ClaimIds1;
}
/**
 * 编排层补全后的陈述。
 */
export interface Claim {
  id: Id3;
  text: Text1;
  epistemic_type: EpistemicType;
  confidence: ConfidenceLevel;
  source_ids: SourceIds;
  as_of: AsOf1;
  task_id: TaskId6;
  agent: Agent;
  verification: VerificationStatus1;
  verification_note: VerificationNote;
  citation_index: CitationIndex1;
}
export interface UsageUpdatedEvent {
  seq: Seq26;
  session_id: SessionId26;
  ts: Ts26;
  message: Message29;
  type: Type26;
  payload: UsageUpdatedPayload;
}
export interface UsageUpdatedPayload {
  usage: TokenUsage;
  cost_usd: CostUsd1;
}
export interface AgentRunMetricsEvent {
  seq: Seq27;
  session_id: SessionId27;
  ts: Ts27;
  message: Message30;
  type: Type27;
  payload: AgentRunMetricsPayload;
}
/**
 * 一次 Agent run 的工程指标，对应 `agent_runs` 表的一行（§20.1）。
 *
 * 为什么要单独一个事件类型，而不是把这些字段塞进 `AGENT_COMPLETED`：
 *
 * 1. 决策 C 规定 Python 不碰业务库（§4），埋点数据只能经事件流到 Next 侧；
 * 2. 规划阶段的 run 没有 `task_id`，也不产生 `AGENT_COMPLETED`
 *    （它不是计划里的任务），塞进去就得为它伪造一个任务节点；
 * 3. `AGENT_COMPLETED` 是给 UI 看的，prompt hash 这类字段对界面毫无意义，
 *    混在一起会让前端 reducer 承载它不需要的概念。
 *
 * 一个事件 = 一行，Next 侧直接 insert，不需要任何关联状态。
 */
export interface AgentRunMetricsPayload {
  agent: AgentName1;
  task_id: TaskId7;
  model_id: ModelId2;
  status: TaskStatus;
  prompt: PromptDigest | null;
  usage: TokenUsage;
  cost_usd: CostUsd2;
  duration_ms: DurationMs3;
  error: ErrorInfo | null;
}
/**
 * prompt 的 hash 与长度。**不含全文**（§20.3）。
 */
export interface PromptDigest {
  hash: Hash;
  chars: Chars;
}
export interface WarningEvent {
  seq: Seq28;
  session_id: SessionId28;
  ts: Ts28;
  message: Message31;
  type: Type28;
  payload: WarningPayload;
}
export interface WarningPayload {
  code: Code1;
  message: Message32;
}
export interface HeartbeatEvent {
  seq: Seq29;
  session_id: SessionId29;
  ts: Ts29;
  message: Message33;
  type: Type29;
  payload: Payload2;
}
/**
 * LLM 输出的陈述。
 *
 * `epistemic_type` 必填是 §15.3 三重强制机制的第一重（schema 强制）——
 * LLM 无法省略对陈述性质的判断。
 */
export interface ClaimDraft {
  text: Text2;
  epistemic_type: EpistemicType;
  confidence: ConfidenceLevel;
  source_refs: SourceRefs;
  as_of: AsOf2;
}
/**
 * Fact Checker 对单条 claim 的裁定。
 */
export interface ClaimVerification {
  claim_id: ClaimId1;
  verification: VerificationStatus;
  note: Note1;
  /**
   * 若核查后置信度需调整，给出新值
   */
  confidence_adjustment: ConfidenceLevel | null;
  additional_source_refs: AdditionalSourceRefs;
}
/**
 * 子 Agent 的 LLM 输出（`output_type`）。
 */
export interface AgentFinding {
  summary: Summary2;
  claims: Claims1;
  metrics: Metrics;
  data_gaps: DataGaps1;
}
/**
 * 编排层组装后的完整结果，进入 Report Writer 的输入。
 */
export interface ResearchFinding {
  task_id: TaskId8;
  agent: AgentName1;
  summary: Summary3;
  claims: Claims2;
  sources: Sources1;
  metrics: Metrics1;
  data_gaps: DataGaps2;
  tool_errors: ToolErrors;
}
export interface ToolError {
  code: ToolErrorCode;
  message: Message34;
  tool: Tool3;
  provider: Provider2;
  retryable: Retryable;
}
/**
 * Fact Checker 的 `output_type`。
 */
export interface FactCheckResult {
  verifications: Verifications;
  conflicts: Conflicts;
  notes: Notes;
}
/**
 * 报告的附加信息，由编排层填充而非 LLM 输出。
 */
export interface ReportMetadata {
  report_sections: ReportSections1;
  generated_by_model: GeneratedByModel;
  data_gaps: DataGaps3;
  citation_check_passed: CitationCheckPassed;
  citation_warnings: CitationWarnings;
}
/**
 * 数据出处。每个成功的 tool 调用都必须产生。
 */
export interface DataProvenance {
  provider: Provider3;
  endpoint: Endpoint;
  source_url: SourceUrl;
  retrieved_at: RetrievedAt1;
  as_of: AsOf3;
  is_cached: IsCached;
  cache_age_s: CacheAgeS;
}
/**
 * 数据完整性声明。让"部分成功"可被 Agent 感知，而不是静默当成完整数据。
 */
export interface DataQuality {
  completeness: Completeness;
  missing_fields: MissingFields;
  caveats: Caveats;
}
