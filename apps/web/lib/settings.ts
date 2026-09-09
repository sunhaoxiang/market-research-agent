/**
 * 用户设置（P6-8 / §16.1）。
 *
 * 存在 SQLite `settings` 表；启动研究时由 BFF 读出来塞进 Python `options`，
 * 不改进程级环境变量——两次研究可以有不同上限。
 */

export const MODEL_ROLES = ["planner", "balanced", "fast", "writing"] as const;
export type ModelRole = (typeof MODEL_ROLES)[number];

export const REPORT_LANGUAGES = ["zh", "en"] as const;
export type ReportLanguage = (typeof REPORT_LANGUAGES)[number];

export type RoleModels = Record<ModelRole, string | null>;

export type ExecutionLimitSettings = {
  maxTasksPerPlan: number;
  maxParallelTasks: number;
  maxToolCallsPerAgent: number;
  maxSupplementRounds: number;
  taskTimeoutS: number;
  totalTimeoutS: number;
  maxSessionCostUsd: number;
};

export type UserPreferences = {
  defaultModelId: string | null;
  roleModels: RoleModels;
  limits: ExecutionLimitSettings | null;
  report: { language: ReportLanguage };
};

export const EMPTY_ROLE_MODELS: RoleModels = {
  planner: null,
  balanced: null,
  fast: null,
  writing: null,
};

export const FALLBACK_LIMITS: ExecutionLimitSettings = {
  maxTasksPerPlan: 6,
  maxParallelTasks: 2,
  maxToolCallsPerAgent: 12,
  maxSupplementRounds: 1,
  taskTimeoutS: 180,
  totalTimeoutS: 600,
  maxSessionCostUsd: 1,
};

export function emptyPreferences(): UserPreferences {
  return {
    defaultModelId: null,
    roleModels: { ...EMPTY_ROLE_MODELS },
    limits: null,
    report: { language: "zh" },
  };
}

const MODEL_ID = /^[a-z0-9_-]+:[a-z0-9._-]+$/i;

function optionalModelId(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return MODEL_ID.test(trimmed) ? trimmed : null;
}

function boundInt(value: unknown, fallback: number, min: number, max: number): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, Math.trunc(n)));
}

function boundFloat(value: unknown, fallback: number, min: number, max: number): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

export function parseLimits(raw: unknown): ExecutionLimitSettings | null {
  if (raw === null || raw === undefined) return null;
  if (typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  return {
    maxTasksPerPlan: boundInt(row.maxTasksPerPlan ?? row.max_tasks_per_plan, 6, 1, 10),
    maxParallelTasks: boundInt(row.maxParallelTasks ?? row.max_parallel_tasks, 2, 1, 6),
    maxToolCallsPerAgent: boundInt(
      row.maxToolCallsPerAgent ?? row.max_tool_calls_per_agent,
      12,
      1,
      20,
    ),
    maxSupplementRounds: boundInt(row.maxSupplementRounds ?? row.max_supplement_rounds, 1, 0, 2),
    taskTimeoutS: boundFloat(row.taskTimeoutS ?? row.task_timeout_s, 180, 30, 600),
    totalTimeoutS: boundFloat(row.totalTimeoutS ?? row.total_timeout_s, 600, 60, 1800),
    maxSessionCostUsd: boundFloat(row.maxSessionCostUsd ?? row.max_session_cost_usd, 1, 0.01, 20),
  };
}

export function parsePreferences(raw: unknown): UserPreferences {
  const base = emptyPreferences();
  if (raw === null || raw === undefined || typeof raw !== "object") return base;
  const row = raw as Record<string, unknown>;
  const roles = (row.roleModels ?? row.role_models) as Record<string, unknown> | undefined;
  const report = row.report as Record<string, unknown> | undefined;
  const language = report?.language;
  return {
    defaultModelId: optionalModelId(row.defaultModelId ?? row.default_model_id),
    roleModels: {
      planner: optionalModelId(roles?.planner),
      balanced: optionalModelId(roles?.balanced),
      fast: optionalModelId(roles?.fast),
      writing: optionalModelId(roles?.writing),
    },
    limits: parseLimits(row.limits),
    report: {
      language: language === "en" || language === "zh" ? language : "zh",
    },
  };
}

export type AgentResearchOptions = {
  role_models?: Partial<Record<ModelRole, string>>;
  limits?: {
    max_tasks_per_plan: number;
    max_parallel_tasks: number;
    max_tool_calls_per_agent: number;
    max_supplement_rounds: number;
    task_timeout_s: number;
    total_timeout_s: number;
    max_session_cost_usd: number;
  };
  report_language?: ReportLanguage;
};

export function toAgentOptions(prefs: UserPreferences): AgentResearchOptions {
  const role_models: Partial<Record<ModelRole, string>> = {};
  for (const role of MODEL_ROLES) {
    const id = prefs.roleModels[role];
    if (id) role_models[role] = id;
  }
  const options: AgentResearchOptions = {};
  if (Object.keys(role_models).length > 0) options.role_models = role_models;
  if (prefs.limits) {
    options.limits = {
      max_tasks_per_plan: prefs.limits.maxTasksPerPlan,
      max_parallel_tasks: prefs.limits.maxParallelTasks,
      max_tool_calls_per_agent: prefs.limits.maxToolCallsPerAgent,
      max_supplement_rounds: prefs.limits.maxSupplementRounds,
      task_timeout_s: prefs.limits.taskTimeoutS,
      total_timeout_s: prefs.limits.totalTimeoutS,
      max_session_cost_usd: prefs.limits.maxSessionCostUsd,
    };
  }
  if (prefs.report.language !== "zh") options.report_language = prefs.report.language;
  return options;
}
