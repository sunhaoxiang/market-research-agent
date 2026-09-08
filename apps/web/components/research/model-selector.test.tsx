/**
 * 模型选择器标注（P5-9）：可用模型标已验证/未验证；缺 key 的只显示原因。
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type ModelOption, ModelSelector, optionLabel } from "./model-selector";

const MODELS: ModelOption[] = [
  {
    id: "deepseek:deepseek-v4-pro",
    provider: "deepseek",
    display_name: "DeepSeek V4 Pro",
    available: true,
    unavailable_reason: null,
    verified: true,
  },
  {
    id: "zhipu:glm-5.3-flash",
    provider: "zhipu",
    display_name: "GLM-5.3 Flash",
    available: true,
    unavailable_reason: null,
    verified: false,
  },
  {
    id: "openai:gpt-5.6-terra",
    provider: "openai",
    display_name: "GPT-5.6 Terra",
    available: false,
    unavailable_reason: "未配置 OPENAI_API_KEY",
    verified: false,
  },
];

describe("optionLabel", () => {
  it("可用且已冒烟 → 已验证", () => {
    expect(optionLabel(MODELS[0]!)).toBe("DeepSeek V4 Pro — 已验证");
  });

  it("可用但未冒烟 → 未验证", () => {
    expect(optionLabel(MODELS[1]!)).toBe("GLM-5.3 Flash — 未验证");
  });

  it("缺 key 只显示原因，不叠未验证", () => {
    expect(optionLabel(MODELS[2]!)).toBe("GPT-5.6 Terra — 未配置 OPENAI_API_KEY");
    expect(optionLabel(MODELS[2]!)).not.toContain("未验证");
  });
});

describe("ModelSelector", () => {
  it("把标注画进 option 文本", () => {
    render(<ModelSelector models={MODELS} value="" onChange={vi.fn()} />);

    expect(screen.getByRole("option", { name: "DeepSeek V4 Pro — 已验证" })).toBeDefined();
    expect(screen.getByRole("option", { name: "GLM-5.3 Flash — 未验证" })).toBeDefined();
    const openai = screen.getByRole("option", {
      name: "GPT-5.6 Terra — 未配置 OPENAI_API_KEY",
    }) as HTMLOptionElement;
    expect(openai.disabled).toBe(true);
  });
});
