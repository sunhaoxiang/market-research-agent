import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  usePathname: () => "/history",
}));

import { AppHeader, navFromPath } from "@/components/research/app-header";

describe("navFromPath", () => {
  it("按路径识别当前栏目，首页算研究", () => {
    expect(navFromPath("/")).toBe("research");
    expect(navFromPath("/history")).toBe("history");
    expect(navFromPath("/settings")).toBe("settings");
    expect(navFromPath("/debug")).toBe("debug");
  });
});

describe("AppHeader", () => {
  it("是细顶栏：有产品名和导航，没有副标题", () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true } satisfies Pick<Response, "ok">));
    render(<AppHeader />);

    expect(screen.getByRole("link", { name: "Market Research Agent" })).toBeDefined();
    expect(screen.queryByText("Crypto · 美股研究")).toBeNull();
    expect(screen.getByRole("navigation", { name: "主导航" })).toBeDefined();
    expect(screen.getByRole("link", { name: "历史" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "研究" }).getAttribute("aria-current")).toBeNull();
  });
});
