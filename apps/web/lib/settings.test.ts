import { describe, expect, it } from "vitest";

import { emptyPreferences, parsePreferences, toAgentOptions } from "@/lib/settings";

describe("parsePreferences", () => {
  it("空值得到可保存的默认结构", () => {
    expect(parsePreferences(null)).toEqual(emptyPreferences());
  });

  it("丢掉非法模型 id，夹紧上限", () => {
    const parsed = parsePreferences({
      defaultModelId: "not-a-model",
      roleModels: { planner: "deepseek:deepseek-v4-pro", fast: "???" },
      limits: { maxTasksPerPlan: 99, maxParallelTasks: 0 },
      report: { language: "en" },
    });
    expect(parsed.defaultModelId).toBeNull();
    expect(parsed.roleModels.planner).toBe("deepseek:deepseek-v4-pro");
    expect(parsed.roleModels.fast).toBeNull();
    expect(parsed.limits?.maxTasksPerPlan).toBe(10);
    expect(parsed.limits?.maxParallelTasks).toBe(1);
    expect(parsed.report.language).toBe("en");
  });
});

describe("toAgentOptions", () => {
  it("只把真正覆盖的字段传给 Python", () => {
    expect(toAgentOptions(emptyPreferences())).toEqual({});
    expect(
      toAgentOptions({
        ...emptyPreferences(),
        roleModels: { ...emptyPreferences().roleModels, writing: "deepseek:deepseek-v4-pro" },
        report: { language: "en" },
      }),
    ).toEqual({
      role_models: { writing: "deepseek:deepseek-v4-pro" },
      report_language: "en",
    });
  });
});
