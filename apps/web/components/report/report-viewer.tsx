"use client";

import type { Claim, Conflict, MetricPoint, ResearchReport, Source } from "@mra/shared";

import { EpistemicBadge, marksForClaims } from "@/components/report/epistemic-badge";
import { MetricCharts } from "@/components/report/metric-charts";
import { ReportMarkdown } from "@/components/report/markdown";

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
    <article className="space-y-5">
      <header className="space-y-2">
        <h2 className="text-xs font-medium tracking-wide text-zinc-500 uppercase">Report</h2>
        <h3 className="text-lg font-semibold">{report.title}</h3>
      </header>

      <section>
        <h4 className="sr-only">摘要</h4>
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
          className="space-y-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
        >
          <h4 className="font-semibold">数值冲突</h4>
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
                ? "space-y-2 border-t border-dashed border-zinc-200 pt-4 dark:border-zinc-800"
                : "space-y-2"
            }
          >
            <div className="flex flex-wrap items-baseline gap-2">
              <h4
                className={
                  disclaimer
                    ? "text-xs font-medium tracking-wide text-zinc-500 uppercase"
                    : "text-sm font-semibold"
                }
              >
                {section.title}
              </h4>
              {marks.map((mark) => (
                <EpistemicBadge key={mark.type} mark={mark} />
              ))}
            </div>
            <div className={disclaimer ? "text-xs text-zinc-500 dark:text-zinc-400" : undefined}>
              <ReportMarkdown
                markdown={section.markdown}
                sourcesByIndex={sourcesByIndex}
                activeIndex={activeIndex}
                onCite={onCite}
              />
            </div>
          </section>
        );
      })}

      {!hasDataLimitations && report.data_gaps.length > 0 && (
        <section className="space-y-2 border-t border-dashed border-zinc-200 pt-4 dark:border-zinc-800">
          <h4 className="text-sm font-semibold">数据限制</h4>
          <ul className="list-disc space-y-1 pl-5 text-sm text-zinc-600 dark:text-zinc-400">
            {report.data_gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}
