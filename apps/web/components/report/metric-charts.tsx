"use client";

import type { MetricPoint } from "@mra/shared";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  type MetricChartSeries,
  chartTitle,
  formatMetricValue,
  groupMetricCharts,
} from "@/lib/research/series";

export function MetricCharts({ metrics }: { metrics: readonly MetricPoint[] }) {
  const series = groupMetricCharts(metrics);
  if (series.length === 0) return null;

  return (
    <section className="space-y-4" aria-label="指标走势">
      {series.map((item) => (
        <MetricChart key={item.id} series={item} />
      ))}
    </section>
  );
}

function MetricChart({ series }: { series: MetricChartSeries }) {
  const title = chartTitle(series);
  const label = `${title}，${series.points.length} 个数据点`;

  return (
    <figure className="space-y-2" aria-label={label}>
      <figcaption className="text-sm font-medium tracking-tight text-zinc-600 dark:text-zinc-400">
        {title}
      </figcaption>
      <div className="h-48 w-full">
        <ResponsiveContainer
          width="100%"
          height="100%"
          initialDimension={{ width: 640, height: 192 }}
        >
          <LineChart data={series.points} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis
              dataKey="asOfLabel"
              tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
              tickLine={false}
              axisLine={{ stroke: "var(--border)" }}
            />
            <YAxis
              tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
              tickLine={false}
              axisLine={false}
              width={64}
              tickFormatter={(value: number) => formatMetricValue(value, series.unit)}
            />
            <Tooltip
              formatter={(value) => formatMetricValue(Number(value), series.unit)}
              labelFormatter={(_, payload) => {
                const point = payload[0]?.payload as { asOfLabel?: string } | undefined;
                return point?.asOfLabel ?? "";
              }}
              contentStyle={{
                background: "var(--background)",
                border: "1px solid var(--border)",
                fontSize: 12,
              }}
            />
            <Line
              type="monotone"
              dataKey="value"
              stroke="var(--accent)"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
              name={series.label}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}
