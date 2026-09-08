/**
 * 把 METRIC_FOUND 里的点收成可画的时间序列。
 *
 * 前端不猜指标含义：只认后端给的 `name` / `as_of` / `value`。
 * 同一序列 = 同名 + 同标的 + 同单位 + 同源；至少两点才画线。
 */

import type { MetricPoint } from "@mra/shared";

const CHARTABLE = new Set(["tvl", "price"]);
const DAY_MS = 86_400_000;

export type ChartPoint = {
  asOf: number;
  value: number;
  asOfLabel: string;
};

export type MetricChartSeries = {
  id: string;
  name: string;
  label: string;
  entitySymbol: string | null;
  unit: string | null;
  sourceRef: string | null;
  spanDays: number;
  points: ChartPoint[];
};

export function groupMetricCharts(metrics: readonly MetricPoint[]): MetricChartSeries[] {
  const buckets = new Map<string, MetricPoint[]>();
  const seen = new Set<string>();

  for (const metric of metrics) {
    const name = metric.name.toLowerCase();
    if (!CHARTABLE.has(name) || metric.as_of === null) continue;
    const asOf = Date.parse(metric.as_of);
    if (Number.isNaN(asOf)) continue;

    const entity = metric.entity_symbol ?? "";
    const unit = metric.unit ?? "";
    const source = metric.source_ref ?? "";
    const dedupe = `${name}|${entity}|${unit}|${source}|${asOf}`;
    if (seen.has(dedupe)) continue;
    seen.add(dedupe);

    const key = `${name}|${entity}|${unit}|${source}`;
    const list = buckets.get(key) ?? [];
    list.push(metric);
    buckets.set(key, list);
  }

  const series: MetricChartSeries[] = [];
  for (const [id, items] of buckets) {
    if (items.length < 2) continue;
    const sorted = [...items].sort((a, b) => Date.parse(a.as_of!) - Date.parse(b.as_of!));
    const first = Date.parse(sorted[0]!.as_of!);
    const last = Date.parse(sorted.at(-1)!.as_of!);
    const sample = sorted[0]!;
    series.push({
      id,
      name: sample.name.toLowerCase(),
      label: sample.label,
      entitySymbol: sample.entity_symbol,
      unit: sample.unit,
      sourceRef: sample.source_ref,
      spanDays: Math.max(1, Math.round((last - first) / DAY_MS)),
      points: sorted.map((item) => ({
        asOf: Date.parse(item.as_of!),
        value: item.value,
        asOfLabel: utcDay(item.as_of!),
      })),
    });
  }

  series.sort(
    (a, b) =>
      a.name.localeCompare(b.name) || (a.entitySymbol ?? "").localeCompare(b.entitySymbol ?? ""),
  );
  return series;
}

export function chartTitle(series: MetricChartSeries): string {
  const entity = series.entitySymbol ? ` · ${series.entitySymbol}` : "";
  return `${series.label}${entity} · ${series.spanDays} 天`;
}

export function formatMetricValue(value: number, unit: string | null): string {
  const compact = compactNumber(value);
  if (unit === "USD" || unit === "usd" || unit === "$") return `$${compact}`;
  if (unit === "%") return `${compact}%`;
  return unit ? `${compact} ${unit}` : compact;
}

function compactNumber(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(1)}k`;
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

function utcDay(iso: string): string {
  const date = new Date(iso);
  const mm = String(date.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(date.getUTCDate()).padStart(2, "0");
  return `${mm}-${dd}`;
}
