/**
 * 来源面板的类型标签与可靠性徽标。
 *
 * 分组顺序把「能当证据」的类型放前面：数据 / SEC / 官方，再是新闻和网页。
 */

import type { Source, SourceReliability, SourceType } from "@mra/shared";

export const SOURCE_TYPE_ORDER: readonly SourceType[] = [
  "api",
  "sec",
  "official",
  "docs",
  "news",
  "web",
  "github",
  "social",
];

export const SOURCE_TYPE_LABELS: Record<SourceType, string> = {
  api: "数据",
  sec: "SEC",
  official: "官方",
  docs: "文档",
  news: "新闻",
  web: "网页",
  github: "GitHub",
  social: "社媒",
};

export const RELIABILITY_MARK: Record<
  SourceReliability,
  { label: string; className: string } | null
> = {
  primary: {
    label: "一手",
    className: "bg-accent-subtle text-accent-text",
  },
  secondary: {
    label: "媒体",
    className: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
  },
  aggregator: {
    label: "聚合",
    className: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
  },
  unknown: null,
};

export type SourceTypeCount = {
  type: SourceType;
  label: string;
  count: number;
};

export function countCitedByType(sources: readonly Source[]): SourceTypeCount[] {
  const counts = new Map<SourceType, number>();
  for (const source of sources) {
    if (source.citation_index === null) continue;
    counts.set(source.source_type, (counts.get(source.source_type) ?? 0) + 1);
  }
  return SOURCE_TYPE_ORDER.flatMap((type) => {
    const count = counts.get(type) ?? 0;
    if (count === 0) return [];
    return [{ type, label: SOURCE_TYPE_LABELS[type], count }];
  });
}
