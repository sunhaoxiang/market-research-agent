import { describe, expect, it } from "vitest";

import { EXAMPLE_PROMPTS, formatSessionDay } from "@/lib/research/empty-home";

describe("empty-home", () => {
  it("三条示例覆盖加密、对比、美股，提问原文不变", () => {
    expect(EXAMPLE_PROMPTS.map((item) => item.label)).toEqual(["加密", "对比", "美股"]);
    expect(EXAMPLE_PROMPTS.map((item) => item.question)).toEqual([
      "Hyperliquid 的协议收入最近怎么样？HYPE 值得关注吗？",
      "比较 Solana 和 Sui 的生态活跃度",
      "英伟达最新一季财报的关键信号是什么？",
    ]);
  });

  it("会话日期按 UTC 日显示，避免 SSR 时区漂移", () => {
    expect(formatSessionDay(Date.UTC(2026, 8, 9, 16, 30))).toBe("2026-09-09");
  });
});
