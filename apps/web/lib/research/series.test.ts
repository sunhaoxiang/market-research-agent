import type { MetricPoint } from "@mra/shared";
import { describe, expect, it } from "vitest";

import { chartTitle, formatChangePct, groupMetricCharts, seriesDelta } from "@/lib/research/series";

function point(
  overrides: Partial<MetricPoint> & Pick<MetricPoint, "value" | "as_of">,
): MetricPoint {
  return {
    name: "tvl",
    label: "TVL",
    unit: "USD",
    entity_symbol: "HYPE",
    source_ref: "s1",
    ...overrides,
  };
}

function days(count: number, start = "2026-08-10T00:00:00.000Z"): MetricPoint[] {
  const origin = Date.parse(start);
  return Array.from({ length: count }, (_, index) =>
    point({
      value: 1_400_000_000 + index * 1_000_000,
      as_of: new Date(origin + index * 86_400_000).toISOString(),
    }),
  );
}

describe("groupMetricCharts", () => {
  it("把 30 天 TVL 收成一条可画的序列", () => {
    const series = groupMetricCharts(days(30));
    expect(series).toHaveLength(1);
    expect(series[0]!.name).toBe("tvl");
    expect(series[0]!.points).toHaveLength(30);
    expect(series[0]!.spanDays).toBe(29);
    expect(chartTitle(series[0]!)).toBe("TVL · HYPE · 29 天");
    expect(seriesDelta(series[0]!).latest).toBe(1_429_000_000);
    expect(seriesDelta(series[0]!).changePct).toBeCloseTo((29_000_000 / 1_400_000_000) * 100);
    expect(formatChangePct(2.142)).toBe("+2.14%");
    expect(formatChangePct(-10)).toBe("-10.0%");
    expect(formatChangePct(0)).toBe("0.00%");
  });

  it("恰好跨 30 天时标题带 30 天", () => {
    const series = groupMetricCharts(days(31, "2026-08-09T00:00:00.000Z"));
    expect(series[0]!.spanDays).toBe(30);
    expect(chartTitle(series[0]!)).toContain("30 天");
  });

  it("单点或没有 as_of 的不画", () => {
    expect(groupMetricCharts(days(1))).toEqual([]);
    expect(
      groupMetricCharts([point({ value: 1, as_of: null }), point({ value: 2, as_of: null })]),
    ).toEqual([]);
  });

  it("价格与 TVL 分成两条；同日同源去重", () => {
    const tvl = days(2);
    const price = tvl.map((item, index) => ({
      ...item,
      name: "price",
      label: "价格",
      value: 30 + index,
    }));
    const dup = { ...tvl[0]! };
    const series = groupMetricCharts([...tvl, ...price, dup]);
    expect(series.map((item) => item.name)).toEqual(["price", "tvl"]);
    expect(series.find((item) => item.name === "tvl")?.points).toHaveLength(2);
  });

  it("市值这类非走势指标忽略", () => {
    expect(
      groupMetricCharts([
        point({ name: "market_cap", label: "市值", value: 1e9, as_of: "2026-08-10T00:00:00.000Z" }),
        point({
          name: "market_cap",
          label: "市值",
          value: 1.1e9,
          as_of: "2026-09-08T00:00:00.000Z",
        }),
      ]),
    ).toEqual([]);
  });
});
