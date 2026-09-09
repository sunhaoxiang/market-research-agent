import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EmptyHome } from "@/components/research/empty-home";
import { HOME_DISCLAIMER } from "@/lib/research/empty-home";

describe("EmptyHome", () => {
  it("示例卡片带类型标签，点击填入完整问题", () => {
    const onPickExample = vi.fn();
    render(
      <EmptyHome recent={[]} onPickExample={onPickExample}>
        <p>提问框</p>
      </EmptyHome>,
    );

    expect(screen.getByRole("heading", { name: "带来源、区分事实与推测" })).toBeDefined();
    expect(screen.getByText(HOME_DISCLAIMER)).toBeDefined();
    expect(screen.queryByRole("heading", { name: "最近研究" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /加密/ }));
    expect(onPickExample).toHaveBeenCalledWith(
      "Hyperliquid 的协议收入最近怎么样？HYPE 值得关注吗？",
    );
  });

  it("有最近研究时列出可点进回放的条目", () => {
    render(
      <EmptyHome
        onPickExample={() => undefined}
        recent={[
          {
            id: "sess-hype",
            question: "帮我做一份 HYPE 的投资研究报告",
            status: "completed",
            questionType: "crypto",
            createdAt: Date.UTC(2026, 8, 8),
          },
        ]}
      >
        <p>提问框</p>
      </EmptyHome>,
    );

    const entry = screen.getByRole("link", { name: /帮我做一份 HYPE 的投资研究报告/ });
    expect(entry.getAttribute("href")).toBe("/?session=sess-hype");
    expect(screen.getByText("已完成 · 加密 · 2026-09-08")).toBeDefined();
    expect(screen.getByRole("link", { name: "全部" }).getAttribute("href")).toBe("/history");
  });
});
