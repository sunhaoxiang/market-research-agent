"use client";

import { Select } from "@/components/ui/field";

/**
 * 渲染选择器所需的最小字段，独立声明而不从 `lib/agent-client` 取。
 *
 * 那是个 `server-only` 模块，组件引它会被 lint 拦下（§17.1）——即使只是
 * 类型导入：一旦有人顺手加上一个值导入，服务端代码就进了客户端 bundle。
 */
export type ModelOption = {
  id: string;
  provider: string;
  display_name: string;
  available: boolean;
  unavailable_reason: string | null;
  /** 完整研究冒烟是否通过。不可用的模型只显示缺 key，不叠「未验证」。 */
  verified: boolean;
};

export function optionLabel(model: ModelOption): string {
  if (!model.available) {
    return `${model.display_name} — ${model.unavailable_reason ?? "不可用"}`;
  }
  return `${model.display_name} — ${model.verified ? "已验证" : "未验证"}`;
}

/**
 * Provider → Model 两级选择器（§9.3）。
 *
 * 不可用的模型仍然列出但置灰并带上原因，而不是当作不存在——用户需要知道
 * "GPT 在列表里但要配 key"，否则会以为项目不支持它。
 */
export function ModelSelector({
  models,
  value,
  onChange,
  disabled,
}: {
  models: ModelOption[];
  value: string;
  onChange: (modelId: string) => void;
  disabled?: boolean;
}) {
  const byProvider = new Map<string, ModelOption[]>();
  for (const model of models) {
    byProvider.set(model.provider, [...(byProvider.get(model.provider) ?? []), model]);
  }

  return (
    <label className="flex items-center gap-2 text-xs text-zinc-500">
      <span className="shrink-0">模型</span>
      <Select
        value={value}
        disabled={disabled}
        onChange={(currentTarget) => onChange(currentTarget.target.value)}
        className="max-w-56"
      >
        {/* 空 value 代表"交给后端按角色解析"，这是默认路径（§9.5） */}
        <option value="">自动（按角色）</option>
        {[...byProvider].map(([provider, group]) => (
          <optgroup key={provider} label={provider}>
            {group.map((model) => (
              <option key={model.id} value={model.id} disabled={!model.available}>
                {optionLabel(model)}
              </option>
            ))}
          </optgroup>
        ))}
      </Select>
    </label>
  );
}
