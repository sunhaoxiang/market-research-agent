"use client";

import { useState } from "react";
import type { MetricPoint } from "@mra/shared";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  type ChartPoint,
  type MetricChartSeries,
  chartTitle,
  formatChangePct,
  formatMetricValue,
  groupMetricCharts,
  seriesDelta,
} from "@/lib/research/series";
import { cn } from "@/lib/utils";

export function MetricCharts({ metrics }: { metrics: readonly MetricPoint[] }) {
  const series = groupMetricCharts(metrics);
  if (series.length === 0) return null;

  return (
    <section className="space-y-4" aria-label="指标走势">
      {series.map((item, index) => (
        <MetricChart key={item.id} series={item} gradientId={`metric-fill-${index}`} />
      ))}
    </section>
  );
}

function MetricChart({ series, gradientId }: { series: MetricChartSeries; gradientId: string }) {
  const title = chartTitle(series);
  const delta = seriesDelta(series);
  const latest = formatMetricValue(delta.latest, series.unit);
  const change = delta.changePct === null ? null : formatChangePct(delta.changePct);
  const label = change
    ? `${title}，${series.points.length} 个数据点，最新 ${latest}，区间 ${change}`
    : `${title}，${series.points.length} 个数据点，最新 ${latest}`;
  const [cross, setCross] = useState<{ x: number; y: number } | null>(null);

  return (
    <figure className="space-y-2" aria-label={label}>
      <figcaption className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="text-sm font-medium tracking-tight text-zinc-800 dark:text-zinc-100">
          {title}
        </span>
        <span className="flex items-baseline gap-2 font-medium tabular-nums">
          <span className="text-sm text-zinc-800 dark:text-zinc-100">{latest}</span>
          {change && (
            <span
              className={cn(
                "text-xs",
                delta.changePct !== null &&
                  delta.changePct > 0 &&
                  "text-emerald-600 dark:text-emerald-400",
                delta.changePct !== null &&
                  delta.changePct < 0 &&
                  "text-amber-700 dark:text-amber-400",
                delta.changePct === 0 && "text-zinc-500",
              )}
            >
              {change}
            </span>
          )}
        </span>
      </figcaption>
      <div className="h-48 w-full">
        <ResponsiveContainer
          width="100%"
          height="100%"
          initialDimension={{ width: 640, height: 192 }}
        >
          <AreaChart
            data={series.points}
            margin={{ top: 8, right: 8, bottom: 0, left: 8 }}
            onMouseMove={(state) => {
              const point = state.activeCoordinate;
              setCross(state.isTooltipActive && point ? { x: point.x, y: point.y } : null);
            }}
            onMouseLeave={() => setCross(null)}
          >
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.28} />
                <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
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
              cursor={false}
              content={(props) => (
                <ChartTooltip active={props.active} payload={props.payload} unit={series.unit} />
              )}
              isAnimationActive={false}
            />
            <Area
              type="monotone"
              dataKey="value"
              stroke="var(--accent)"
              strokeWidth={2}
              fill={`url(#${gradientId})`}
              dot={false}
              activeDot={{
                r: 3.5,
                fill: "var(--accent)",
                stroke: "var(--background)",
                strokeWidth: 2,
              }}
              isAnimationActive={false}
              name={series.label}
            />
            {cross && (
              <g className="recharts-crosshair" pointerEvents="none">
                <line
                  x1={cross.x}
                  y1={0}
                  x2={cross.x}
                  y2="100%"
                  stroke="var(--accent)"
                  strokeOpacity={0.35}
                />
                <line
                  x1={0}
                  y1={cross.y}
                  x2="100%"
                  y2={cross.y}
                  stroke="var(--accent)"
                  strokeOpacity={0.35}
                />
              </g>
            )}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

function ChartTooltip({
  active,
  payload,
  unit,
}: {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: ChartPoint }>;
  unit: string | null;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const point = payload[0]?.payload;
  if (!point) return null;

  return (
    <div className="rounded-md border border-zinc-200 bg-white px-3 py-2 text-xs shadow-md dark:border-zinc-700 dark:bg-zinc-900">
      <p className="text-zinc-500">{point.asOfLabel}</p>
      <p className="mt-0.5 text-sm font-medium tabular-nums text-zinc-800 dark:text-zinc-100">
        {formatMetricValue(point.value, unit)}
      </p>
    </div>
  );
}
