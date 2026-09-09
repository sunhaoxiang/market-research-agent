"use client";

import { useEffect, useMemo, useState } from "react";
import type { Source, SourceType } from "@mra/shared";

import { SourceHoverBody } from "@/components/sources/source-preview";
import { sourceElementId } from "@/lib/report/citations";
import { countCitedByType, RELIABILITY_MARK } from "@/lib/report/source-groups";
import { surface } from "@/lib/ui";
import { cn } from "@/lib/utils";

type SourcePanelProps = {
  sources: Source[];
  activeIndex: number | null;
  onSelect: (index: number) => void;
  className?: string;
};

export function SourcePanel({ sources, activeIndex, onSelect, className }: SourcePanelProps) {
  const numbered = useMemo(
    () =>
      sources
        .filter((item) => item.citation_index !== null)
        .sort((a, b) => (a.citation_index ?? 0) - (b.citation_index ?? 0)),
    [sources],
  );
  const orphans = useMemo(() => sources.filter((item) => item.citation_index === null), [sources]);
  const typeCounts = useMemo(() => countCitedByType(numbered), [numbered]);
  const [typeFilter, setTypeFilter] = useState<SourceType | "all">("all");

  const visible = numbered.filter((item) => {
    if (typeFilter === "all" || item.citation_index === activeIndex) return true;
    return item.source_type === typeFilter;
  });

  useEffect(() => {
    if (activeIndex === null) return;
    const el = document.getElementById(sourceElementId(activeIndex));
    if (!(el instanceof HTMLElement)) return;
    el.scrollIntoView({ block: "nearest" });
    el.focus({ preventScroll: true });
  }, [activeIndex]);

  if (sources.length === 0) return null;

  return (
    <section
      className={cn(
        "flex min-h-0 flex-col gap-3 p-3",
        surface,
        "lg:sticky lg:top-[4.25rem] lg:max-h-[calc(100vh-5.25rem)]",
        className,
      )}
    >
      <div className="shrink-0 space-y-2">
        <h2 className="text-[11px] font-medium tracking-[0.16em] text-zinc-500 uppercase">
          Sources ({sources.length})
        </h2>
        {typeCounts.length > 1 && (
          <div className="flex flex-wrap gap-1" role="group" aria-label="按类型筛选来源">
            <TypeChip
              label="全部"
              count={numbered.length}
              active={typeFilter === "all"}
              onClick={() => setTypeFilter("all")}
            />
            {typeCounts.map((item) => (
              <TypeChip
                key={item.type}
                label={item.label}
                count={item.count}
                active={typeFilter === item.type}
                onClick={() => setTypeFilter(item.type)}
              />
            ))}
          </div>
        )}
      </div>

      <ol className="min-h-0 space-y-1 overflow-y-auto pr-0.5">
        {visible.map((source) => (
          <SourceRow
            key={source.id}
            source={source}
            active={source.citation_index === activeIndex}
            onSelect={onSelect}
          />
        ))}
      </ol>

      {orphans.length > 0 && (
        <ul className="shrink-0 space-y-1 text-xs text-zinc-400">
          {orphans.map((source) => (
            <li key={source.id} className="truncate">
              {source.title ?? source.domain ?? source.url}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function TypeChip({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "rounded-full px-2 py-0.5 text-[11px] tabular-nums",
        "focus-visible:ring-accent focus-visible:ring-2 focus-visible:outline-none",
        active
          ? "bg-accent-subtle text-accent-text"
          : "text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800",
      )}
    >
      {label} {count}
    </button>
  );
}

function SourceRow({
  source,
  active,
  onSelect,
}: {
  source: Source;
  active: boolean;
  onSelect: (index: number) => void;
}) {
  const index = source.citation_index;
  if (index === null) return null;
  const label = source.title ?? source.domain ?? source.url;
  const reliability = RELIABILITY_MARK[source.reliability];

  return (
    <li className="group relative">
      <button
        type="button"
        id={sourceElementId(index)}
        aria-current={active ? "true" : undefined}
        aria-label={`来源 ${index}：${label}`}
        onClick={() => onSelect(index)}
        className={cn(
          "w-full rounded-md px-2 py-1.5 text-left text-xs transition",
          "focus-visible:ring-accent hover:bg-zinc-100 focus-visible:ring-2 focus-visible:outline-none dark:hover:bg-zinc-800",
          active && "bg-amber-50 ring-1 ring-amber-400 dark:bg-amber-950/40",
        )}
      >
        <span className="flex items-start gap-2">
          <span className="text-accent-text mt-0.5 shrink-0 font-mono font-medium">[{index}]</span>
          <DomainMark domain={source.domain} />
          <span className="min-w-0 flex-1">
            <span className="flex items-start gap-1.5">
              <span className="line-clamp-2 min-w-0 flex-1 font-medium text-zinc-800 dark:text-zinc-100">
                {label}
              </span>
              {reliability && (
                <span
                  className={cn(
                    "shrink-0 rounded px-1 py-px text-[10px] leading-4 font-medium",
                    reliability.className,
                  )}
                >
                  {reliability.label}
                </span>
              )}
            </span>
            {source.domain && (
              <span className="mt-0.5 block truncate text-zinc-500">{source.domain}</span>
            )}
            {active && source.excerpt && (
              <span className="mt-1.5 block text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-300">
                {source.excerpt}
              </span>
            )}
          </span>
        </span>
      </button>
      {!active && (
        <div
          role="tooltip"
          className={cn(
            surface,
            "pointer-events-none invisible absolute top-full left-0 z-20 mt-1 w-72 p-2 text-zinc-700 shadow-md group-hover:visible group-focus-within:visible dark:text-zinc-200",
          )}
        >
          <SourceHoverBody source={source} />
        </div>
      )}
    </li>
  );
}

function DomainMark({ domain }: { domain: string | null }) {
  const letter = (domain?.[0] ?? "?").toUpperCase();
  return (
    <span
      aria-hidden
      className="bg-accent-subtle text-accent-text mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-sm text-[9px] font-semibold"
    >
      {letter}
    </span>
  );
}
