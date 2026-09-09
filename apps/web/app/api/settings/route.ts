/**
 * 用户设置（P6-8 / §16.1）：默认模型、角色映射、执行上限、报告语言。
 */

import { getDb } from "@/db/client";
import { getPreferences, putPreferences } from "@/db/queries/settings";
import { fetchAgentModels } from "@/lib/agent-client";
import {
  FALLBACK_LIMITS,
  parsePreferences,
  type ExecutionLimitSettings,
  type UserPreferences,
} from "@/lib/settings";

export const dynamic = "force-dynamic";

export type SettingsPayload = {
  preferences: UserPreferences;
  defaults: {
    defaultModelId: string;
    roleModels: Record<string, string>;
    limits: ExecutionLimitSettings;
  };
};

export async function GET() {
  const catalog = await fetchAgentModels();
  const preferences = getPreferences(getDb());
  return Response.json({
    preferences,
    defaults: catalogDefaults(catalog),
  } satisfies SettingsPayload);
}

export async function PATCH(request: Request) {
  const body: unknown = await request.json().catch(() => null);
  const preferences = putPreferences(getDb(), parsePreferences(body));
  return Response.json({ preferences });
}

function catalogDefaults(
  catalog: Awaited<ReturnType<typeof fetchAgentModels>>,
): SettingsPayload["defaults"] {
  if (!catalog.ok) {
    return {
      defaultModelId: "deepseek:deepseek-v4-pro",
      roleModels: {},
      limits: FALLBACK_LIMITS,
    };
  }
  const limits = catalog.data.limits;
  return {
    defaultModelId: catalog.data.default_model_id,
    roleModels: catalog.data.role_defaults,
    limits: limits
      ? {
          maxTasksPerPlan: limits.max_tasks_per_plan,
          maxParallelTasks: limits.max_parallel_tasks,
          maxToolCallsPerAgent: limits.max_tool_calls_per_agent,
          maxSupplementRounds: limits.max_supplement_rounds,
          taskTimeoutS: limits.task_timeout_s,
          totalTimeoutS: limits.total_timeout_s,
          maxSessionCostUsd: limits.max_session_cost_usd,
        }
      : FALLBACK_LIMITS,
  };
}
