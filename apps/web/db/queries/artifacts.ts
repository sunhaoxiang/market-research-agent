/**
 * 把研究事件投影到 sources / claims / research_reports。
 *
 * 事件表是真相；这三张表是物化视图。刷新页面后 Source Panel 既可以
 * 重放事件重建，也可以直接读投影——两边必须对得上。
 *
 * Python 不碰业务库（决策 C），所以只能在 BFF 消费 SSE 时写入。
 */

import "server-only";

import { and, eq } from "drizzle-orm";

import type { ResearchEvent } from "@mra/shared";

import type { Db } from "@/db/client";
import {
  AGENT_NAMES,
  CONFIDENCE_LEVELS,
  EPISTEMIC_TYPES,
  SOURCE_RELIABILITY,
  SOURCE_TYPES,
  VERIFICATION_STATUSES,
  type NewClaim,
  type NewSource,
  type ReportMetadata,
  type ReportSection,
  claimSources,
  claims,
  researchReports,
  sources,
} from "@/db/schema";
import { newId } from "@/lib/ids";

export function projectArtifacts(db: Db, sessionId: string, event: ResearchEvent): void {
  switch (event.type) {
    case "source_found":
      upsertSource(db, sessionId, asRecord(event.payload.source));
      return;
    case "report_completed":
      persistReport(db, sessionId, event);
      return;
    default:
      return;
  }
}

function persistReport(db: Db, sessionId: string, event: ResearchEvent): void {
  if (event.type !== "report_completed") return;

  const report = asRecord(event.payload.report) ?? {};
  const rawSources = Array.isArray(event.payload.sources) ? event.payload.sources : [];
  const rawClaims = Array.isArray(event.payload.claims) ? event.payload.claims : [];

  for (const item of rawSources) {
    upsertSource(db, sessionId, asRecord(item));
  }

  const knownSources = new Set(
    db
      .select({ id: sources.id })
      .from(sources)
      .where(eq(sources.sessionId, sessionId))
      .all()
      .map((row) => row.id),
  );

  for (const item of rawClaims) {
    const raw = asRecord(item);
    if (!raw) continue;
    const row = toClaimRow(sessionId, raw, Date.parse(event.ts));
    if (!row) continue;
    upsertClaim(db, row);
    const linked = Array.isArray(raw.source_ids) ? raw.source_ids : [];
    for (const sourceId of linked) {
      if (typeof sourceId !== "string" || !knownSources.has(sourceId)) continue;
      db.insert(claimSources).values({ claimId: row.id, sourceId }).onConflictDoNothing().run();
    }
  }

  const title = typeof report.title === "string" && report.title ? report.title : "研究报告";
  const summary = typeof report.executive_summary === "string" ? report.executive_summary : "";
  const sections = (Array.isArray(report.sections) ? report.sections : [])
    .map(toStoredSection)
    .filter((section): section is ReportSection => section !== null);
  const dataGaps = Array.isArray(report.data_gaps)
    ? report.data_gaps.filter((gap): gap is string => typeof gap === "string")
    : [];
  const metadata: ReportMetadata = {
    reportSections: sections.map((section) => section.id),
    generatedByModel: "report_writer",
    dataGaps,
  };
  const stored = {
    title,
    executiveSummary: summary || null,
    sections,
    markdown: reportMarkdown(title, summary, sections),
    metadata,
  };

  const existing = db
    .select({ id: researchReports.id })
    .from(researchReports)
    .where(eq(researchReports.sessionId, sessionId))
    .get();
  if (existing) {
    db.update(researchReports).set(stored).where(eq(researchReports.id, existing.id)).run();
    return;
  }
  db.insert(researchReports)
    .values({
      id: newId(),
      sessionId,
      createdAt: Number.isNaN(Date.parse(event.ts)) ? Date.now() : Date.parse(event.ts),
      ...stored,
    })
    .run();
}

function upsertSource(db: Db, sessionId: string, raw: Record<string, unknown> | null): void {
  const row = toSourceRow(sessionId, raw);
  if (!row) return;

  const patch = {
    title: row.title,
    excerpt: row.excerpt,
    citationIndex: row.citationIndex ?? undefined,
    httpStatus: row.httpStatus,
    reliability: row.reliability,
    provider: row.provider,
  };

  const byUrl = db
    .select({ id: sources.id })
    .from(sources)
    .where(and(eq(sources.sessionId, sessionId), eq(sources.urlCanonical, row.urlCanonical)))
    .get();
  if (byUrl) {
    db.update(sources).set(patch).where(eq(sources.id, byUrl.id)).run();
    return;
  }

  const byId = db.select({ id: sources.id }).from(sources).where(eq(sources.id, row.id)).get();
  if (byId) {
    db.update(sources).set(patch).where(eq(sources.id, row.id)).run();
    return;
  }

  db.insert(sources).values(row).run();
}

function upsertClaim(db: Db, row: NewClaim): void {
  const existing = db.select({ id: claims.id }).from(claims).where(eq(claims.id, row.id)).get();
  if (existing) {
    db.update(claims)
      .set({
        text: row.text,
        epistemicType: row.epistemicType,
        confidence: row.confidence,
        asOf: row.asOf,
        verification: row.verification,
        verificationNote: row.verificationNote,
        citationIndex: row.citationIndex,
        taskId: row.taskId,
        agent: row.agent,
      })
      .where(eq(claims.id, row.id))
      .run();
    return;
  }
  db.insert(claims).values(row).run();
}

function toSourceRow(sessionId: string, raw: Record<string, unknown> | null): NewSource | null {
  if (!raw) return null;
  const id = required(raw.id);
  const url = required(raw.url);
  const urlCanonical = required(raw.url_canonical) ?? url;
  const retrievedAt = isoToMs(raw.retrieved_at);
  const sourceType = asEnum(raw.source_type, SOURCE_TYPES, null);
  if (!id || !url || !urlCanonical || retrievedAt === null || sourceType === null) return null;

  return {
    id,
    sessionId,
    url,
    urlCanonical,
    title: asText(raw.title),
    domain: asText(raw.domain),
    sourceType,
    provider: asText(raw.provider),
    reliability: asEnum(raw.reliability, SOURCE_RELIABILITY, "unknown"),
    publishedAt: isoToMs(raw.published_at),
    retrievedAt,
    excerpt: asText(raw.excerpt),
    citationIndex: asNumber(raw.citation_index),
    httpStatus: asNumber(raw.http_status),
  };
}

function toClaimRow(
  sessionId: string,
  raw: Record<string, unknown>,
  createdAt: number,
): NewClaim | null {
  const id = required(raw.id);
  const text = asText(raw.text);
  const epistemicType = asEnum(raw.epistemic_type, EPISTEMIC_TYPES, null);
  const confidence = asEnum(raw.confidence, CONFIDENCE_LEVELS, null);
  if (!id || text === null || epistemicType === null || confidence === null) return null;

  return {
    id,
    sessionId,
    taskId: asText(raw.task_id),
    agent: asEnum(raw.agent, AGENT_NAMES, null),
    text,
    epistemicType,
    confidence,
    asOf: isoToMs(raw.as_of),
    verification: asEnum(raw.verification, VERIFICATION_STATUSES, "unverified"),
    verificationNote: asText(raw.verification_note),
    citationIndex: asNumber(raw.citation_index),
    createdAt: Number.isNaN(createdAt) ? Date.now() : createdAt,
  };
}

function toStoredSection(raw: unknown): ReportSection | null {
  const section = asRecord(raw);
  if (!section) return null;
  const id = required(section.id);
  const title = asText(section.title);
  const markdown = asText(section.markdown);
  if (!id || title === null || markdown === null) return null;
  const claimIds = Array.isArray(section.claim_ids)
    ? section.claim_ids.filter((item): item is string => typeof item === "string")
    : [];
  return { id, title, markdown, claimIds };
}

function reportMarkdown(title: string, summary: string, sections: ReportSection[]): string {
  return [
    `# ${title}`,
    summary,
    ...sections.map((section) => `## ${section.title}\n\n${section.markdown}`),
  ]
    .filter((block) => block.trim().length > 0)
    .join("\n\n");
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function required(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function asText(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function isoToMs(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "string" || value.length === 0) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function asEnum<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T;
function asEnum<T extends string>(value: unknown, allowed: readonly T[], fallback: null): T | null;
function asEnum<T extends string>(
  value: unknown,
  allowed: readonly T[],
  fallback: T | null,
): T | null {
  return typeof value === "string" && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : fallback;
}
