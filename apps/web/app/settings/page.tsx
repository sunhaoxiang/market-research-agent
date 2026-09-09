import type { Metadata } from "next";

import type { ModelOption } from "@/components/research/model-selector";
import { SettingsForm } from "@/components/settings/settings-form";
import { getDb } from "@/db/client";
import { getPreferences } from "@/db/queries/settings";
import { fetchAgentModels } from "@/lib/agent-client";
import { FALLBACK_LIMITS } from "@/lib/settings";
import { notice } from "@/lib/ui";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "设置" };

export default async function SettingsPage() {
  const catalog = await fetchAgentModels();
  const models: ModelOption[] = catalog.ok ? catalog.data.models : [];
  const preferences = getPreferences(getDb());
  const limits =
    catalog.ok && catalog.data.limits
      ? {
          maxTasksPerPlan: catalog.data.limits.max_tasks_per_plan,
          maxParallelTasks: catalog.data.limits.max_parallel_tasks,
          maxToolCallsPerAgent: catalog.data.limits.max_tool_calls_per_agent,
          maxSupplementRounds: catalog.data.limits.max_supplement_rounds,
          taskTimeoutS: catalog.data.limits.task_timeout_s,
          totalTimeoutS: catalog.data.limits.total_timeout_s,
          maxSessionCostUsd: catalog.data.limits.max_session_cost_usd,
        }
      : FALLBACK_LIMITS;

  return (
    <main id="main" className="mx-auto max-w-6xl px-6 py-6">
      <h1 className="mb-6 text-lg font-semibold tracking-tight">设置</h1>
      {!catalog.ok && (
        <p className={cn(notice, "mb-6 px-3 py-2 text-sm")}>{catalog.error.message}</p>
      )}
      <SettingsForm
        models={models}
        initial={preferences}
        defaults={{
          defaultModelId: catalog.ok ? catalog.data.default_model_id : "deepseek:deepseek-v4-pro",
          roleModels: catalog.ok ? catalog.data.role_defaults : {},
          limits,
        }}
      />
    </main>
  );
}
