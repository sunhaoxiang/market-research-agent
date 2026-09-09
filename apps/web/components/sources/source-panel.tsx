"use client";

import { useEffect } from "react";
import type { Source } from "@mra/shared";

import { SourceHoverBody } from "@/components/sources/source-preview";
import { sourceElementId } from "@/lib/report/citations";
import { cn, formatTimestamp } from "@/lib/utils";

type SourcePanelProps = {
  sources: Source[];
  activeIndex: number | null;
  onSelect: (index: number) => void;
  className?: string;
};

export function SourcePanel({ sources, activeIndex, onSelect, className }: SourcePanelProps) {
  const numbered = sources
    .filter((item) => item.citation_index !== null)
    .sort((a, b) => (a.citation_index ?? 0) - (b.citation_index ?? 0));
  const orphans = sources.filter((item) => item.citation_index === null);

  useEffect(() => {
    if (activeIndex === null) return;
    const el = document.getElementById(sourceElementId(activeIndex));
    if (!(el instanceof HTMLElement)) return;
    el.scrollIntoView({ block: "nearest" });
    el.focus({ preventScroll: true });
  }, [activeIndex]);

  if (sources.length === 0) return null;

  return (
    <section className={cn("space-y-3", className)}>
      <h2 className="text-[11px] font-medium tracking-[0.16em] text-zinc-500 uppercase">
        Sources ({sources.length})
      </h2>
      <ol className="space-y-2">
        {numbered.map((source) => (
          <SourceRow
            key={source.id}
            source={source}
            active={source.citation_index === activeIndex}
            onSelect={onSelect}
          />
        ))}
      </ol>
      {orphans.length > 0 && (
        <ul className="space-y-1 text-xs text-zinc-400">
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
        <span className="flex items-baseline gap-2">
          <span className="text-accent-text font-mono font-medium">[{index}]</span>
          <span className="min-w-0 truncate font-medium text-zinc-800 dark:text-zinc-100">
            {label}
          </span>
        </span>
        <span className="mt-0.5 block truncate text-zinc-500">
          {[source.domain, formatTimestamp(source.retrieved_at)].filter(Boolean).join(" · ")}
        </span>
      </button>
      <div
        role="tooltip"
        className={cn(
          "absolute top-full left-0 z-20 mt-1 w-72 rounded-md border border-zinc-200 bg-white p-2 text-zinc-700 shadow-md dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200",
          active
            ? "visible"
            : "pointer-events-none invisible group-hover:visible group-focus-within:visible",
        )}
      >
        <SourceHoverBody source={source} />
      </div>
    </li>
  );
}
