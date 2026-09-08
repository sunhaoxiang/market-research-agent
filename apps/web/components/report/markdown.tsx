"use client";

import type { Source } from "@mra/shared";
import type { ReactNode } from "react";
import Markdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { SourceHoverBody } from "@/components/sources/source-preview";
import { citationHref, linkCitations, parseCitationHref } from "@/lib/report/citations";
import { cn } from "@/lib/utils";

type ReportMarkdownProps = {
  markdown: string;
  sourcesByIndex: Map<number, Source>;
  activeIndex: number | null;
  onCite: (index: number) => void;
};

export function ReportMarkdown({
  markdown,
  sourcesByIndex,
  activeIndex,
  onCite,
}: ReportMarkdownProps) {
  return (
    <div
      className={cn(
        "text-sm leading-relaxed",
        "[&_h1]:mt-4 [&_h1]:text-base [&_h1]:font-semibold",
        "[&_h2]:mt-4 [&_h2]:text-base [&_h2]:font-semibold",
        "[&_h3]:mt-3 [&_h3]:text-sm [&_h3]:font-semibold",
        "[&_p]:my-2 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5",
        "[&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5",
        "[&_blockquote]:border-l-2 [&_blockquote]:border-zinc-300 [&_blockquote]:pl-3 [&_blockquote]:text-zinc-600",
        "[&_code]:rounded [&_code]:bg-zinc-100 [&_code]:px-1 [&_code]:text-[0.85em] dark:[&_code]:bg-zinc-800",
        "[&_pre]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-zinc-100 [&_pre]:p-3 dark:[&_pre]:bg-zinc-900",
        "[&_table]:my-2 [&_table]:w-full [&_table]:text-xs",
        "[&_th]:border [&_th]:border-zinc-200 [&_th]:px-2 [&_th]:py-1 dark:[&_th]:border-zinc-700",
        "[&_td]:border [&_td]:border-zinc-200 [&_td]:px-2 [&_td]:py-1 dark:[&_td]:border-zinc-700",
        "[&_a]:text-blue-700 [&_a]:underline-offset-2 hover:[&_a]:underline dark:[&_a]:text-blue-400",
      )}
    >
      <Markdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        components={{
          a({ href, children }) {
            const index = parseCitationHref(href);
            if (index !== null) {
              return (
                <CitationLink
                  index={index}
                  source={sourcesByIndex.get(index)}
                  active={activeIndex === index}
                  onCite={onCite}
                >
                  {children}
                </CitationLink>
              );
            }
            if (!href) return <>{children}</>;
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
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

function CitationLink({
  index,
  source,
  active,
  onCite,
  children,
}: {
  index: number;
  source: Source | undefined;
  active: boolean;
  onCite: (index: number) => void;
  children: ReactNode;
}) {
  const label = source?.title ?? source?.domain ?? `来源 ${index}`;
  return (
    <span className="group relative inline-block">
      <a
        href={citationHref(index)}
        aria-label={`来源 ${index}：${label}`}
        aria-current={active ? "true" : undefined}
        onClick={() => onCite(index)}
        className={cn(
          "mx-0.5 inline-flex translate-y-px rounded px-0.5 text-xs font-medium no-underline",
          "text-blue-700 hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-950",
          active &&
            "bg-amber-200 text-amber-950 ring-1 ring-amber-400 dark:bg-amber-900 dark:text-amber-100",
        )}
      >
        {children}
      </a>
      {source && (
        <span
          role="tooltip"
          className="pointer-events-none invisible absolute bottom-full left-1/2 z-20 mb-1 w-64 -translate-x-1/2 rounded-md border border-zinc-200 bg-white p-2 text-left text-xs text-zinc-700 shadow-md group-hover:visible group-focus-within:visible dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
        >
          <SourceHoverBody source={source} />
        </span>
      )}
    </span>
  );
}
