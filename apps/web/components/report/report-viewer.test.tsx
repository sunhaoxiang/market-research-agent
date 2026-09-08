/**
 * Report + Source Panel（P2-10 验收）。
 *
 * 点 `[1]` 必须高亮对应来源；HTML 注入不能进 DOM；分析类陈述要打上徽标。
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import type { Claim, Conflict, MetricPoint, ResearchReport, Source } from "@mra/shared";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ReportViewer } from "@/components/report/report-viewer";
import { SourcePanel } from "@/components/sources/source-panel";

const SOURCE: Source = {
  id: "src-1",
  ref: "s1",
  url: "https://theblock.co/post/1",
  url_canonical: "https://theblock.co/post/1",
  title: "Fee share",
  domain: "theblock.co",
  source_type: "news",
  provider: "tavily",
  reliability: "secondary",
  published_at: null,
  retrieved_at: "2026-09-08T12:00:00.000Z",
  excerpt: "The protocol may share trading fees with HYPE holders.",
  citation_index: 1,
  http_status: null,
};

const CLAIM: Claim = {
  id: "c1",
  text: "手续费分享仍在讨论，落地时点不确定。",
  epistemic_type: "analysis",
  confidence: "medium",
  source_ids: ["src-1"],
  as_of: null,
  task_id: "t1",
  agent: "web_research",
  verification: "unverified",
  verification_note: null,
  citation_index: null,
};

const REPORT: ResearchReport = {
  title: "HYPE 近况",
  executive_summary: "Hyperliquid 正在讨论手续费分享。[1]",
  sections: [
    {
      id: "Overview",
      title: "概述",
      markdown: "Hyperliquid 正在讨论把部分交易手续费分享给 HYPE 持有人。[1]",
      claim_ids: ["c1"],
    },
  ],
  data_gaps: ["未找到官方解锁时间表"],
};

function tvlCurve(): MetricPoint[] {
  const origin = Date.parse("2026-08-09T00:00:00.000Z");
  return Array.from({ length: 31 }, (_, index) => ({
    name: "tvl",
    label: "TVL",
    value: 1_400_000_000 + index * 1_000_000,
    unit: "USD",
    entity_symbol: "HYPE",
    as_of: new Date(origin + index * 86_400_000).toISOString(),
    source_ref: "s1",
  }));
}

function Shell({
  report = REPORT,
  sources = [SOURCE],
  claims = [CLAIM],
  conflicts = [],
  metrics = [],
}: {
  report?: ResearchReport;
  sources?: Source[];
  claims?: Claim[];
  conflicts?: Conflict[];
  metrics?: MetricPoint[];
}) {
  const [active, setActive] = useState<number | null>(null);
  return (
    <>
      <ReportViewer
        report={report}
        sources={sources}
        claims={claims}
        conflicts={conflicts}
        metrics={metrics}
        activeIndex={active}
        onCite={setActive}
      />
      <SourcePanel sources={sources} activeIndex={active} onSelect={setActive} />
    </>
  );
}

describe("引用交互", () => {
  beforeEach(() => {
    HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  it("点 [1] 高亮对应来源", () => {
    render(<Shell />);

    fireEvent.click(screen.getAllByRole("link", { name: "来源 1：Fee share" })[0]!);

    expect(
      screen.getByRole("button", { name: "来源 1：Fee share" }).getAttribute("aria-current"),
    ).toBe("true");
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("来源行展示 domain、时间与摘录", () => {
    render(<Shell />);

    expect(screen.getByRole("heading", { name: "Sources (1)" })).toBeDefined();
    expect(screen.getByText(/theblock.co · 2026-09-08 12:00 UTC/)).toBeDefined();
    expect(screen.getAllByText(/share trading fees/).length).toBeGreaterThan(0);
  });
});

describe("sanitize 与徽标", () => {
  it("HTML 注入不会变成可执行节点", () => {
    const report: ResearchReport = {
      ...REPORT,
      executive_summary:
        '正常段落。\n\n<script>window.pwned=1</script><img src=x onerror="alert(1)">',
      sections: [
        {
          id: "Overview",
          title: "概述",
          markdown: "[钓鱼](javascript:alert(1))",
          claim_ids: [],
        },
      ],
    };
    const { container } = render(<Shell report={report} claims={[]} />);

    expect(screen.getByText("正常段落。")).toBeDefined();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.queryByRole("link", { name: "钓鱼" })).toBeNull();
  });

  it("分析类陈述显示认知类型徽标", () => {
    render(<Shell />);

    expect(screen.getByText("分析")).toBeDefined();
    expect(screen.queryByText("观点")).toBeNull();
  });

  it("数据缺口进入数据限制节", () => {
    render(<Shell />);

    expect(screen.getByText("数据限制")).toBeDefined();
    expect(screen.getByText("未找到官方解锁时间表")).toBeDefined();
  });

  it("数值冲突以黄色警示条并列展示各源数值", () => {
    render(
      <Shell
        conflicts={[
          {
            claim_ids: ["c1"],
            description: "TVL（HYPE）多源数值不一致，已并列保留各来源结果，未取平均。",
            values: ["coingecko: 1.2e+09 USD", "defillama: 1.8e+09 USD"],
          },
        ]}
      />,
    );

    expect(screen.getByRole("status")).toBeDefined();
    expect(screen.getByText("数值冲突")).toBeDefined();
    expect(screen.getByText("coingecko: 1.2e+09 USD")).toBeDefined();
    expect(screen.getByText("defillama: 1.8e+09 USD")).toBeDefined();
    expect(screen.queryByText(/1\.5e/)).toBeNull();
  });

  it("报告中显示 30 天 TVL 曲线", () => {
    render(<Shell metrics={tvlCurve()} />);

    expect(screen.getByRole("region", { name: "指标走势" })).toBeDefined();
    expect(screen.getByRole("figure", { name: /TVL · HYPE · 30 天，31 个数据点/ })).toBeDefined();
    expect(screen.getByText("TVL · HYPE · 30 天")).toBeDefined();
    expect(document.querySelector("svg .recharts-line")).not.toBeNull();
  });

  it("Comparison 章节的 Markdown 表格渲染成 table", () => {
    render(
      <Shell
        report={{
          ...REPORT,
          sections: [
            {
              id: "Comparison",
              title: "对比",
              markdown:
                "| 指标 | NVDA | AMD | AVGO |\n| --- | --- | --- | --- |\n| 营收（USD） | 46,743,000,000 | 7,400,000,000 | 15,000,000,000 |",
              claim_ids: [],
            },
          ],
        }}
      />,
    );

    expect(screen.getByRole("table")).toBeDefined();
    expect(screen.getByRole("columnheader", { name: "NVDA" })).toBeDefined();
    expect(screen.getByRole("cell", { name: "46,743,000,000" })).toBeDefined();
  });
});
