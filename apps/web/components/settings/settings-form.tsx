"use client";

import { useMemo, useState } from "react";

import { type ModelOption, optionLabel } from "@/components/research/model-selector";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/field";
import {
  FALLBACK_LIMITS,
  MODEL_ROLES,
  parsePreferences,
  type ExecutionLimitSettings,
  type ModelRole,
  type UserPreferences,
} from "@/lib/settings";

const ROLE_LABELS: Record<ModelRole, string> = {
  planner: "规划（Research Manager）",
  balanced: "研究 / 核查",
  fast: "意图分类 / Web",
  writing: "撰写报告",
};

const LANGUAGE_LABELS = {
  zh: "简体中文",
  en: "English",
} as const;

export function SettingsForm({
  models,
  initial,
  defaults,
}: {
  models: ModelOption[];
  initial: UserPreferences;
  defaults: {
    defaultModelId: string;
    roleModels: Record<string, string>;
    limits: ExecutionLimitSettings;
  };
}) {
  const envLimits = defaults.limits ?? FALLBACK_LIMITS;
  const [draft, setDraft] = useState<UserPreferences>(initial);
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [message, setMessage] = useState("");

  const shownLimits = draft.limits ?? envLimits;
  const byProvider = useMemo(() => {
    const groups = new Map<string, ModelOption[]>();
    for (const model of models) {
      groups.set(model.provider, [...(groups.get(model.provider) ?? []), model]);
    }
    return [...groups];
  }, [models]);

  async function save() {
    setStatus("saving");
    setMessage("");
    try {
      const response = await fetch("/api/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      });
      const body = (await response.json().catch(() => null)) as {
        preferences?: UserPreferences;
        error?: { message?: string };
      } | null;
      if (!response.ok) {
        throw new Error(body?.error?.message ?? "保存失败");
      }
      if (body?.preferences) setDraft(parsePreferences(body.preferences));
      setStatus("saved");
      setMessage("已保存，下次研究会话生效。");
    } catch (error) {
      setStatus("error");
      setMessage(error instanceof Error ? error.message : "保存失败");
    }
  }

  return (
    <form
      className="mx-auto max-w-2xl space-y-8"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <fieldset className="space-y-3">
        <legend className="text-sm font-medium">默认模型</legend>
        <p className="text-xs text-zinc-500">
          首页提问时预选此项。选「自动」则按下面的角色映射（或环境变量）解析。一次提问里再选模型会覆盖全部角色。
        </p>
        <ModelPick
          groups={byProvider}
          value={draft.defaultModelId ?? ""}
          emptyLabel={`自动（环境默认 ${defaults.defaultModelId}）`}
          onChange={(value) => setDraft({ ...draft, defaultModelId: value || null })}
        />
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-medium">按角色指定</legend>
        <p className="text-xs text-zinc-500">
          仅在默认模型为「自动」、且这次提问也没另选模型时生效。
        </p>
        <div className="space-y-3">
          {MODEL_ROLES.map((role) => (
            <label key={role} className="block space-y-1 text-sm">
              <span className="text-xs text-zinc-500">{ROLE_LABELS[role]}</span>
              <ModelPick
                groups={byProvider}
                value={draft.roleModels[role] ?? ""}
                emptyLabel={`环境默认（${defaults.roleModels[role] ?? "目录兜底"}）`}
                onChange={(value) =>
                  setDraft({
                    ...draft,
                    roleModels: { ...draft.roleModels, [role]: value || null },
                  })
                }
              />
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-medium">执行上限</legend>
        <p className="text-xs text-zinc-500">硬上限见开发计划 §7.2。保存后写入本次之后的研究。</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <NumberField
            label="每份计划最多任务"
            value={shownLimits.maxTasksPerPlan}
            min={1}
            max={10}
            onChange={(maxTasksPerPlan) =>
              setDraft({ ...draft, limits: { ...shownLimits, maxTasksPerPlan } })
            }
          />
          <NumberField
            label="并行任务"
            value={shownLimits.maxParallelTasks}
            min={1}
            max={6}
            onChange={(maxParallelTasks) =>
              setDraft({ ...draft, limits: { ...shownLimits, maxParallelTasks } })
            }
          />
          <NumberField
            label="每个 Agent 最多工具调用"
            value={shownLimits.maxToolCallsPerAgent}
            min={1}
            max={20}
            onChange={(maxToolCallsPerAgent) =>
              setDraft({ ...draft, limits: { ...shownLimits, maxToolCallsPerAgent } })
            }
          />
          <NumberField
            label="补充研究轮数"
            value={shownLimits.maxSupplementRounds}
            min={0}
            max={2}
            onChange={(maxSupplementRounds) =>
              setDraft({ ...draft, limits: { ...shownLimits, maxSupplementRounds } })
            }
          />
          <NumberField
            label="单任务超时（秒）"
            value={shownLimits.taskTimeoutS}
            min={30}
            max={600}
            onChange={(taskTimeoutS) =>
              setDraft({ ...draft, limits: { ...shownLimits, taskTimeoutS } })
            }
          />
          <NumberField
            label="整次超时（秒）"
            value={shownLimits.totalTimeoutS}
            min={60}
            max={1800}
            onChange={(totalTimeoutS) =>
              setDraft({ ...draft, limits: { ...shownLimits, totalTimeoutS } })
            }
          />
          <NumberField
            label="单次成本上限（美元）"
            value={shownLimits.maxSessionCostUsd}
            min={0.01}
            max={20}
            step={0.01}
            onChange={(maxSessionCostUsd) =>
              setDraft({ ...draft, limits: { ...shownLimits, maxSessionCostUsd } })
            }
          />
        </div>
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-medium">报告偏好</legend>
        <label className="block space-y-1 text-sm">
          <span className="text-xs text-zinc-500">语言</span>
          <Select
            value={draft.report.language}
            onChange={(event) =>
              setDraft({
                ...draft,
                report: { language: event.target.value === "en" ? "en" : "zh" },
              })
            }
          >
            <option value="zh">{LANGUAGE_LABELS.zh}</option>
            <option value="en">{LANGUAGE_LABELS.en}</option>
          </Select>
        </label>
      </fieldset>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={status === "saving"}>
          {status === "saving" ? "保存中…" : "保存"}
        </Button>
        {message && (
          <p className={status === "error" ? "text-sm text-amber-700" : "text-sm text-zinc-500"}>
            {message}
          </p>
        )}
      </div>
    </form>
  );
}

function ModelPick({
  groups,
  value,
  emptyLabel,
  onChange,
}: {
  groups: [string, ModelOption[]][];
  value: string;
  emptyLabel: string;
  onChange: (value: string) => void;
}) {
  return (
    <Select value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{emptyLabel}</option>
      {groups.map(([provider, group]) => (
        <optgroup key={provider} label={provider}>
          {group.map((model) => (
            <option key={model.id} value={model.id} disabled={!model.available}>
              {optionLabel(model)}
            </option>
          ))}
        </optgroup>
      ))}
    </Select>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  step = 1,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="text-xs text-zinc-500">{label}</span>
      <Input
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}
