"use client";

import type { Claim, ResearchReport, Source } from "@mra/shared";

import { EpistemicBadge, marksForClaims } from "@/components/report/epistemic-badge";
import { ReportMarkdown } from "@/components/report/markdown";

type ReportViewerProps = {
  report: ResearchReport;
  sources: Source[];
  claims: Claim[];
  activeIndex: number | null;
  onCite: (index: number) => void;
};

export function ReportViewer({ report, sources, claims, activeIndex, onCite }: ReportViewerProps) {
  const sourcesByIndex = new Map(
    sources
      .filter((item) => item.citation_index !== null)
      .map((item) => [item.citation_index as number, item]),
  );
  const claimsById = new Map(claims.map((claim) => [claim.id, claim]));

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

      {report.sections.map((section) => {
        const sectionClaims = section.claim_ids
          .map((id) => claimsById.get(id))
          .filter((claim): claim is Claim => claim !== undefined);
        const marks = marksForClaims(sectionClaims);
        return (
          <section key={section.id} className="space-y-2">
            <div className="flex flex-wrap items-baseline gap-2">
              <h4 className="text-sm font-semibold">{section.title}</h4>
              {marks.map((mark) => (
                <EpistemicBadge key={mark.type} mark={mark} />
              ))}
            </div>
            <ReportMarkdown
              markdown={section.markdown}
              sourcesByIndex={sourcesByIndex}
              activeIndex={activeIndex}
              onCite={onCite}
            />
          </section>
        );
      })}

      {report.data_gaps.length > 0 && (
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
