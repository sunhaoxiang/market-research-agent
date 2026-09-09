"use client";

import type { Claim, Conflict, MetricPoint, ResearchReport, Source } from "@mra/shared";

import { EpistemicBadge, marksForClaims } from "@/components/report/epistemic-badge";
import { MetricCharts } from "@/components/report/metric-charts";
import { ReportMarkdown } from "@/components/report/markdown";
import { notice, surfaceMuted } from "@/lib/ui";
import { cn } from "@/lib/utils";

type ReportViewerProps = {
  report: ResearchReport;
  sources: Source[];
  claims: Claim[];
  conflicts?: Conflict[];
  metrics?: readonly MetricPoint[];
  activeIndex: number | null;
  onCite: (index: number) => void;
};

export function ReportViewer({
  report,
  sources,
  claims,
  conflicts = [],
  metrics = [],
  activeIndex,
  onCite,
}: ReportViewerProps) {
  const sourcesByIndex = new Map(
    sources
      .filter((item) => item.citation_index !== null)
      .map((item) => [item.citation_index as number, item]),
  );
  const claimsById = new Map(claims.map((claim) => [claim.id, claim]));
  const hasDataLimitations = report.sections.some(
    (section) => section.id.toLowerCase() === "data limitations",
  );

  return (
    <article className="space-y-8">
      <header className="max-w-[42rem] space-y-2">
        <p className="text-[11px] font-medium tracking-[0.16em] text-zinc-500 uppercase">报告</p>
        <h1 className="text-2xl leading-snug font-semibold tracking-tight text-pretty">
          {report.title}
        </h1>
      </header>

      <section className={cn(surfaceMuted, "max-w-[42rem] px-5 py-4")}>
        <h2 className="sr-only">摘要</h2>
        <ReportMarkdown
          markdown={report.executive_summary}
          sourcesByIndex={sourcesByIndex}
          activeIndex={activeIndex}
          onCite={onCite}
        />
      </section>

      {conflicts.length > 0 && (
        <aside
          role="status"
          className={cn(notice, "max-w-[42rem] space-y-2 px-4 py-3 text-[15px] leading-relaxed")}
        >
          <h2 className="font-semibold">数值冲突</h2>
          <ul className="space-y-2">
            {conflicts.map((item) => (
              <li key={item.description}>
                <p>{item.description}</p>
                {item.values.length > 0 && (
                  <ul className="mt-1 list-disc pl-5">
                    {item.values.map((value) => (
                      <li key={value}>{value}</li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        </aside>
      )}

      <MetricCharts metrics={metrics} />

      {report.sections.map((section) => {
        const sectionClaims = section.claim_ids
          .map((id) => claimsById.get(id))
          .filter((claim): claim is Claim => claim !== undefined);
        const marks = marksForClaims(sectionClaims);
        const disclaimer = section.id.toLowerCase() === "disclaimer";
        const limitations = section.id.toLowerCase() === "data limitations";
        return (
          <section
            key={section.id}
            className={
              disclaimer || limitations
                ? "border-border max-w-[42rem] space-y-3 border-t pt-6"
                : "max-w-[42rem] space-y-3"
            }
          >
            <div className="flex flex-wrap items-baseline gap-2">
              <h2
                className={
                  disclaimer
                    ? "text-[11px] font-medium tracking-[0.16em] text-zinc-500 uppercase"
                    : "text-lg font-semibold tracking-tight"
                }
              >
                {section.title}
              </h2>
              {marks.map((mark) => (
                <EpistemicBadge key={mark.type} mark={mark} />
              ))}
            </div>
            <div>
              <ReportMarkdown
                markdown={section.markdown}
                sourcesByIndex={sourcesByIndex}
                activeIndex={activeIndex}
                onCite={onCite}
                muted={disclaimer}
              />
            </div>
          </section>
        );
      })}

      {!hasDataLimitations && report.data_gaps.length > 0 && (
        <section className="border-border max-w-[42rem] space-y-3 border-t pt-6">
          <h2 className="text-lg font-semibold tracking-tight">数据限制</h2>
          <ul className="list-disc space-y-1.5 pl-5 text-[15px] leading-relaxed text-zinc-600 dark:text-zinc-400">
            {report.data_gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}
