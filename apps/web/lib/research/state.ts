/**
 * 事件 → 视图状态的归约（§12.4，P1-12）。
 *
 * 纯函数，不碰 React 也不碰时钟：事件顺序与状态机是这个项目里最容易出错的地方
 * （30 种事件类型、并发任务、失败降级），必须能脱离浏览器逐条喂事件来测。
 *
 * 设计前提是「事件是状态变更通知，不是日志字符串」（§12.1）：这里维护一棵
 * 结构化的状态树，前端据此渲染，而不是往一个数组里 append 文本。
 */

import type {
  AgentName,
  Entity,
  ErrorInfo,
  MetricPoint,
  QuestionType,
  ResearchEvent,
  ResearchPlan,
  ResearchReport,
  Source,
  Stage,
  TokenUsage,
} from "@mra/shared";

/** §13.2 的五种节点状态。 */
export type TaskNodeStatus = "pending" | "running" | "completed" | "failed" | "skipped";

export type ToolCallNode = {
  callId: string;
  tool: string;
  status: "running" | "completed" | "failed";
  provider: string | null;
  cacheHit: boolean;
  durationMs: number | null;
  /** 开始时刻，用于 running 节点显示实时耗时 */
  startedAtMs: number;
  errorCode: string | null;
  errorMessage: string | null;
};

export type TaskNode = {
  id: string;
  agent: AgentName;
  objective: string;
  dependsOn: string[];
  status: TaskNodeStatus;
  modelId: string | null;
  /** 最近一条 agent_progress，只留最新的：§13.3 明确不做滚动日志 */
  progress: string | null;
  reasoning: string | null;
  summary: string | null;
  error: ErrorInfo | null;
  claimCount: number | null;
  sourceCount: number | null;
  durationMs: number | null;
  startedAtMs: number | null;
  toolCalls: ToolCallNode[];
};

export type SessionStatus = "idle" | "running" | "completed" | "failed" | "cancelled";

export type ResearchViewState = {
  sessionId: string | null;
  status: SessionStatus;
  stage: Stage | null;
  question: string | null;
  modelId: string | null;
  questionType: QuestionType | null;
  entities: Entity[];
  plan: ResearchPlan | null;
  /** 任务顺序单独存：`Record` 的键顺序不该被依赖，而计划树要按计划里的顺序画 */
  taskIds: string[];
  tasks: Record<string, TaskNode>;
  sources: Source[];
  report: ResearchReport | null;
  metrics: MetricPoint[];
  warnings: { code: string; message: string }[];
  usage: TokenUsage;
  costUsd: number | null;
  /** 会话开始/结束的墙上时间，用于计时器 */
  startedAtMs: number | null;
  completedAtMs: number | null;
  durationMs: number | null;
  error: ErrorInfo | null;
  /** 最近一条面向用户的话。未覆盖的事件类型靠它优雅降级（§12.3） */
  lastMessage: string | null;
  lastSeq: number;
  /** seq 出现空洞说明漏收了事件，UI 应提示刷新 */
  hasGap: boolean;
};

export const initialState: ResearchViewState = {
  sessionId: null,
  status: "idle",
  stage: null,
  question: null,
  modelId: null,
  questionType: null,
  entities: [],
  plan: null,
  taskIds: [],
  tasks: {},
  sources: [],
  report: null,
  metrics: [],
  warnings: [],
  usage: { input: 0, output: 0, cached: 0 },
  costUsd: null,
  startedAtMs: null,
  completedAtMs: null,
  durationMs: null,
  error: null,
  lastMessage: null,
  lastSeq: 0,
  hasGap: false,
};

const TERMINAL_STATUSES = new Set<SessionStatus>(["completed", "failed", "cancelled"]);

export function reduce(state: ResearchViewState, event: ResearchEvent): ResearchViewState {
  const next = applyEvent({ ...envelope(state, event) }, event);

  // 终态时把没跑过的任务标成 skipped。执行器对"预算/时间耗尽"只发一条聚合
  // warning，不逐个任务通知（见 executor 的 _skip_remaining），所以具体是哪些
  // 任务被跳过只能在这里推断——会话已结束而某任务从未 started，它就是没跑。
  //
  // 判断的是"已到终态"而不是"不在运行中"：`idle` 也满足后者，会把还没开始的
  // 会话（断线重连后先收到 agent_started、或补充研究刚加进来的任务）
  // 一并标成 skipped
  return TERMINAL_STATUSES.has(next.status) ? settleUnstarted(next) : next;
}

/** 逐条归约一批事件。用于从 DB 重放历史会话。 */
export function reduceAll(
  events: readonly ResearchEvent[],
  from: ResearchViewState = initialState,
): ResearchViewState {
  return events.reduce(reduce, from);
}

/** 信封字段（seq / message）的处理与事件类型无关，单独抽出。 */
function envelope(state: ResearchViewState, event: ResearchEvent): ResearchViewState {
  return {
    ...state,
    // 只在事件真的带文案时才更新。`message` 为 null 表示"这个事件没有面向用户的
    // 话"（心跳、agent_progress、agent_completed 都是），而不是"把横幅清空"——
    // 否则规划阶段那 45 秒里，"正在制定研究计划"会被随后的心跳顶成空白
    lastMessage: event.message ?? state.lastMessage,
    lastSeq: event.seq,
    hasGap: state.hasGap || event.seq !== state.lastSeq + 1,
  };
}

function applyEvent(state: ResearchViewState, event: ResearchEvent): ResearchViewState {
  const at = Date.parse(event.ts);

  switch (event.type) {
    case "session_started":
      return {
        ...state,
        sessionId: event.session_id,
        status: "running",
        question: event.payload.question,
        modelId: event.payload.model_id,
        startedAtMs: at,
      };

    case "stage_changed":
      return { ...state, stage: event.payload.stage };

    case "intent_classified":
      return {
        ...state,
        questionType: event.payload.question_type,
        entities: [...event.payload.entities],
      };

    case "plan_created":
      // 计划一到就画出完整任务树（全部 pending）——§13.2「计划先行」，
      // 这是消除等待焦虑的关键，用户马上知道要做什么
      return attachTasks(state, event.payload.plan);

    case "plan_updated":
      return {
        ...state,
        taskIds: [...state.taskIds, ...event.payload.added_tasks.map((task) => task.id)],
        tasks: {
          ...state.tasks,
          ...Object.fromEntries(event.payload.added_tasks.map((t) => [t.id, blankTask(t)])),
        },
      };

    case "agent_started":
      return patchTask(state, event.payload.task_id, (task) => ({
        ...task,
        agent: event.payload.agent,
        objective: event.payload.objective,
        modelId: event.payload.model_id,
        status: "running",
        startedAtMs: at,
      }));

    case "agent_progress":
      return patchTask(state, event.payload.task_id, (task) => ({
        ...task,
        progress: event.payload.message,
      }));

    case "agent_reasoning":
      // task_id 可空：规划阶段的推理不归属任何任务。挂不上就只留在
      // `lastMessage` 里，凭空造个节点会在计划树上多出一行无主条目
      if (event.payload.task_id === null) return state;
      return patchTask(state, event.payload.task_id, (task) => ({
        ...task,
        reasoning: event.payload.summary,
      }));

    case "agent_completed":
      return patchTask(state, event.payload.task_id, (task) => ({
        ...task,
        status: "completed",
        summary: event.payload.summary,
        claimCount: event.payload.claim_count,
        sourceCount: event.payload.source_count,
        durationMs: event.payload.duration_ms,
      }));

    case "agent_failed":
      return patchTask(state, event.payload.task_id, (task) => ({
        ...task,
        status: "failed",
        error: event.payload.error,
      }));

    case "tool_started":
      // 同上：Research Manager 直接调工具时没有 task_id
      if (event.payload.task_id === null) return state;
      return patchTask(state, event.payload.task_id, (task) => ({
        ...task,
        toolCalls: [
          ...task.toolCalls,
          {
            callId: event.payload.call_id,
            tool: event.payload.tool,
            status: "running",
            provider: null,
            cacheHit: false,
            durationMs: null,
            startedAtMs: at,
            errorCode: null,
            errorMessage: null,
          },
        ],
      }));

    case "tool_completed":
      // tool 事件不带 task_id（call_id 已经唯一），所以按 call_id 全表找
      return patchToolCall(state, event.payload.call_id, (call) => ({
        ...call,
        status: event.payload.ok ? "completed" : "failed",
        provider: event.payload.provider,
        cacheHit: event.payload.cache_hit,
        durationMs: event.payload.duration_ms,
      }));

    case "tool_failed":
      return patchToolCall(state, event.payload.call_id, (call) => ({
        ...call,
        status: "failed",
        errorCode: event.payload.error_code,
        errorMessage: event.payload.message,
      }));

    case "source_found": {
      const incoming = event.payload.source;
      const exists = state.sources.some(
        (item) => item.id === incoming.id || item.url_canonical === incoming.url_canonical,
      );
      return exists ? state : { ...state, sources: [...state.sources, incoming] };
    }

    case "metric_found":
      return { ...state, metrics: [...state.metrics, event.payload.metric] };

    case "usage_updated":
      return { ...state, usage: event.payload.usage, costUsd: event.payload.cost_usd };

    case "warning":
      return {
        ...state,
        warnings: [...state.warnings, { code: event.payload.code, message: event.payload.message }],
      };

    case "report_completed": {
      const numbered = event.payload.sources;
      const byId = new Map(numbered.map((item) => [item.id, item]));
      const sources = state.sources.map((item) => byId.get(item.id) ?? item);
      for (const item of numbered) {
        if (!sources.some((existing) => existing.id === item.id)) {
          sources.push(item);
        }
      }
      return { ...state, report: event.payload.report, sources };
    }

    case "session_completed":
      return {
        ...state,
        status: "completed",
        usage: event.payload.usage,
        costUsd: event.payload.cost_usd,
        durationMs: event.payload.duration_ms,
        completedAtMs: at,
      };

    case "session_failed":
      return {
        ...state,
        status: "failed",
        error: event.payload.error,
        stage: event.payload.stage,
        completedAtMs: at,
      };

    case "session_cancelled":
      return { ...state, status: "cancelled", completedAtMs: at };

    default:
      // 未覆盖的类型（心跳、以及尚未接入的核查事件）只更新信封。
      // 刻意不做 exhaustive 检查：后端先上线新事件类型时，旧前端应当靠
      // `lastMessage` 优雅降级，而不是编译不过或运行时崩掉
      return state;
  }
}

function attachTasks(state: ResearchViewState, plan: ResearchPlan): ResearchViewState {
  return {
    ...state,
    plan,
    questionType: plan.question_type,
    entities: plan.entities.length > 0 ? [...plan.entities] : state.entities,
    taskIds: plan.tasks.map((task) => task.id),
    tasks: Object.fromEntries(plan.tasks.map((task) => [task.id, blankTask(task)])),
  };
}

function blankTask(task: ResearchPlan["tasks"][number]): TaskNode {
  return {
    id: task.id,
    agent: task.agent,
    objective: task.objective,
    dependsOn: [...task.depends_on],
    status: "pending",
    modelId: null,
    progress: null,
    reasoning: null,
    summary: null,
    error: null,
    claimCount: null,
    sourceCount: null,
    durationMs: null,
    startedAtMs: null,
    toolCalls: [],
  };
}

/**
 * 更新一个任务节点。
 *
 * 任务不在表里时**凭事件创建**它，而不是丢弃事件：`plan_created` 理论上先到，
 * 但断线重连后从中途开始接事件是 P6-6 的既定场景，那时先到的会是 agent_started。
 */
function patchTask(
  state: ResearchViewState,
  taskId: string,
  patch: (task: TaskNode) => TaskNode,
): ResearchViewState {
  const existing = state.tasks[taskId];
  const base =
    existing ??
    blankTask({
      id: taskId,
      agent: "research_manager",
      objective: "",
      entities: [],
      suggested_tools: [],
      depends_on: [],
      priority: 0,
    });

  return {
    ...state,
    taskIds: existing ? state.taskIds : [...state.taskIds, taskId],
    tasks: { ...state.tasks, [taskId]: patch(base) },
  };
}

function patchToolCall(
  state: ResearchViewState,
  callId: string,
  patch: (call: ToolCallNode) => ToolCallNode,
): ResearchViewState {
  const taskId = state.taskIds.find((id) =>
    state.tasks[id]?.toolCalls.some((call) => call.callId === callId),
  );
  // 没有对应的 tool_started。丢掉比凭空造一个节点好：工具名、所属任务都不知道
  if (!taskId) return state;

  return patchTask(state, taskId, (task) => ({
    ...task,
    toolCalls: task.toolCalls.map((call) => (call.callId === callId ? patch(call) : call)),
  }));
}

/** 会话已结束时，把从未开始的任务归为 skipped，running 的归为 failed。 */
function settleUnstarted(state: ResearchViewState): ResearchViewState {
  const stale = state.taskIds.filter((id) => {
    const status = state.tasks[id]?.status;
    return status === "pending" || status === "running";
  });
  if (stale.length === 0) return state;

  const tasks = { ...state.tasks };
  for (const id of stale) {
    const task = tasks[id]!;
    // running 却没收到 completed/failed：会话是被取消或崩掉的，
    // 显示成 pending 会让用户以为它还在排队
    tasks[id] = { ...task, status: task.status === "running" ? "failed" : "skipped" };
  }
  return { ...state, tasks };
}
