"use client";

import type { Source } from "@mra/shared";
import { Fragment } from "react";
import Markdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { SourceHoverBody } from "@/components/sources/source-preview";
import { citationHref, linkCitations, parseCitationIndices } from "@/lib/report/citations";
import { surface } from "@/lib/ui";
import { cn } from "@/lib/utils";

type ReportMarkdownProps = {
  markdown: string;
  sourcesByIndex: Map<number, Source>;
  activeIndex: number | null;
  onCite: (index: number) => void;
  muted?: boolean;
};

export function ReportMarkdown({
  markdown,
  sourcesByIndex,
  activeIndex,
  onCite,
  muted = false,
}: ReportMarkdownProps) {
  return (
    <div
      className={cn(
        muted
          ? "text-sm leading-relaxed text-zinc-500 dark:text-zinc-400"
          : "text-[15px] leading-[1.75] text-zinc-800 dark:text-zinc-200",
        "[&_h1]:mt-8 [&_h1]:mb-3 [&_h1]:text-xl [&_h1]:leading-snug [&_h1]:font-semibold [&_h1]:tracking-tight [&_h1]:text-zinc-900 dark:[&_h1]:text-zinc-50",
        "[&_h2]:mt-7 [&_h2]:mb-2.5 [&_h2]:text-lg [&_h2]:leading-snug [&_h2]:font-semibold [&_h2]:tracking-tight [&_h2]:text-zinc-900 dark:[&_h2]:text-zinc-50",
        "[&_h3]:mt-5 [&_h3]:mb-2 [&_h3]:text-base [&_h3]:font-semibold [&_h3]:tracking-tight",
        "[&_:is(h1,h2,h3):first-child]:mt-0",
        "[&_p]:my-3 [&_ul]:my-3 [&_ul]:list-disc [&_ul]:pl-5",
        "[&_ol]:my-3 [&_ol]:list-decimal [&_ol]:pl-5",
        "[&_li]:my-1",
        "[&>:first-child]:mt-0 [&>:last-child]:mb-0",
        "[&_blockquote]:border-border [&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:text-zinc-600 dark:[&_blockquote]:text-zinc-400",
        "[&_code]:bg-muted [&_code]:rounded [&_code]:px-1 [&_code]:text-[0.85em]",
        "[&_pre]:bg-muted [&_pre]:my-3 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:p-3",
        "[&_table]:w-full [&_table]:text-sm [&_table]:leading-normal",
        "[&_th]:border [&_th]:border-border [&_th]:bg-muted [&_th]:px-2.5 [&_th]:py-1.5 [&_th]:text-left [&_th]:font-medium",
        "[&_td]:border [&_td]:border-border [&_td]:px-2.5 [&_td]:py-1.5",
        "[&_a]:text-accent-text [&_a]:underline-offset-2",
      )}
    >
      <Markdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        components={{
          table({ children }) {
            return (
              <div className="my-4 overflow-x-auto">
                <table>{children}</table>
              </div>
            );
          },
          a({ href, children }) {
            const indices = parseCitationIndices(href);
            if (indices) {
              return (
                <CitationCluster
                  indices={indices}
                  sourcesByIndex={sourcesByIndex}
                  activeIndex={activeIndex}
                  onCite={onCite}
                />
              );
            }
            if (!href) return <>{children}</>;
            return (
              <a href={href} target="_blank" rel="noreferrer" className="hover:underline">
                {children}
                <span className="sr-only">（新标签页）</span>
              </a>
            );
          },
        }}
      >
        {linkCitations(markdown)}
      </Markdown>
    </div>
  );
}

function CitationCluster({
  indices,
  sourcesByIndex,
  activeIndex,
  onCite,
}: {
  indices: readonly number[];
  sourcesByIndex: Map<number, Source>;
  activeIndex: number | null;
  onCite: (index: number) => void;
}) {
  return (
    <span
      role="group"
      aria-label={`来源 ${indices.join("、")}`}
      className={cn(
        "bg-accent-subtle text-accent-text relative -top-px mx-0.5 inline-flex items-baseline rounded-md px-1 py-px",
        "align-baseline text-[0.72em] leading-none font-medium tabular-nums",
      )}
    >
      <span aria-hidden className="opacity-45">
        [
      </span>
      {indices.map((index, offset) => (
        <Fragment key={`${index}-${offset}`}>
          {offset > 0 ? (
            <span aria-hidden className="px-px opacity-35">
              ·
            </span>
          ) : null}
          <CitationLink
            index={index}
            source={sourcesByIndex.get(index)}
            active={activeIndex === index}
            onCite={onCite}
          />
        </Fragment>
      ))}
      <span aria-hidden className="opacity-45">
        ]
      </span>
    </span>
  );
}

function CitationLink({
  index,
  source,
  active,
  onCite,
}: {
  index: number;
  source: Source | undefined;
  active: boolean;
  onCite: (index: number) => void;
}) {
  const label = source?.title ?? source?.domain ?? `来源 ${index}`;
  return (
    <span className="group relative inline-block">
      <a
        href={citationHref(index)}
        aria-label={`来源 ${index}：${label}`}
        aria-current={active ? "true" : undefined}
        onClick={(clickEvent) => {
          clickEvent.preventDefault();
          onCite(index);
        }}
        className={cn(
          "inline-flex rounded-sm px-0.5 no-underline hover:no-underline",
          "hover:bg-white/80 focus-visible:ring-accent focus-visible:ring-2 focus-visible:outline-none dark:hover:bg-white/10",
          active &&
            "bg-amber-200 text-amber-950 ring-1 ring-amber-400 dark:bg-amber-900 dark:text-amber-100",
        )}
      >
        {index}
      </a>
      {source && (
        <span
          role="tooltip"
          className={cn(
            surface,
            "pointer-events-none invisible absolute bottom-full left-1/2 z-20 mb-1 w-64 -translate-x-1/2 p-2 text-left text-xs text-zinc-700 shadow-md group-hover:visible group-focus-within:visible dark:text-zinc-200",
          )}
        >
          <SourceHoverBody source={source} />
        </span>
      )}
    </span>
  );
}
