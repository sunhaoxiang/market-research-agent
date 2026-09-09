import { describe, expect, it } from "vitest";
import type { Source } from "@mra/shared";

import { countCitedByType } from "@/lib/report/source-groups";

function source(partial: Partial<Source> & Pick<Source, "id" | "source_type">): Source {
  return {
    ref: partial.ref ?? partial.id,
    url: partial.url ?? `https://example.com/${partial.id}`,
    url_canonical: partial.url_canonical ?? `https://example.com/${partial.id}`,
    title: partial.title ?? partial.id,
    domain: partial.domain ?? "example.com",
    provider: "tavily",
    reliability: partial.reliability ?? "unknown",
    published_at: null,
    retrieved_at: "2026-09-08T12:00:00.000Z",
    excerpt: null,
    citation_index: partial.citation_index ?? 1,
    http_status: null,
    ...partial,
  };
}

describe("countCitedByType", () => {
  it("只统计已编号来源，并按证据优先级排序", () => {
    const counts = countCitedByType([
      source({ id: "n1", source_type: "news", citation_index: 1 }),
      source({ id: "a1", source_type: "api", citation_index: 2 }),
      source({ id: "n2", source_type: "news", citation_index: 3 }),
      source({ id: "orphan", source_type: "web", citation_index: null }),
    ]);

    expect(counts).toEqual([
      { type: "api", label: "数据", count: 1 },
      { type: "news", label: "新闻", count: 2 },
    ]);
  });
});
