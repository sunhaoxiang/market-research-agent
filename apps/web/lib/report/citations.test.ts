import { describe, expect, it } from "vitest";

import { citationHref, linkCitations, parseCitationHref } from "@/lib/report/citations";

describe("linkCitations", () => {
  it("把裸 [n] 写成指向 Source Panel 的锚点", () => {
    expect(linkCitations("手续费分享。[1] 详见 [2]。")).toBe(
      `手续费分享。[1](${citationHref(1)}) 详见 [2](${citationHref(2)})。`,
    );
  });

  it("不改写已经是 markdown 链接的 [n](url)", () => {
    const text = "见 [1](https://example.com/a) 与 [1]。";
    expect(linkCitations(text)).toBe(`见 [1](https://example.com/a) 与 [1](${citationHref(1)})。`);
  });
});

describe("parseCitationHref", () => {
  it("只识别来源锚点", () => {
    expect(parseCitationHref(citationHref(3))).toBe(3);
    expect(parseCitationHref("https://example.com/a")).toBeNull();
    expect(parseCitationHref("#cite-1")).toBeNull();
  });
});
