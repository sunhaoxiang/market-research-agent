import { describe, expect, it } from "vitest";

import {
  citationGroupHref,
  citationHref,
  linkCitations,
  parseCitationHref,
  parseCitationIndices,
} from "@/lib/report/citations";

describe("linkCitations", () => {
  it("把裸 [n] 写成指向 Source Panel 的锚点", () => {
    expect(linkCitations("手续费分享。[1] 详见 [2]。")).toBe(
      `手续费分享。[1](${citationHref(1)}) 详见 [2](${citationHref(2)})。`,
    );
  });

  it("紧挨着的 [n] 收成一组，中间有空格的保持分开", () => {
    expect(linkCitations("结论。[1][6][10] 以及 [2] [3]。")).toBe(
      `结论。[1,6,10](${citationGroupHref([1, 6, 10])}) 以及 [2](${citationHref(2)}) [3](${citationHref(3)})。`,
    );
  });

  it("不改写已经是 markdown 链接的 [n](url)", () => {
    const text = "见 [1](https://example.com/a) 与 [1][2]。";
    expect(linkCitations(text)).toBe(
      `见 [1](https://example.com/a) 与 [1,2](${citationGroupHref([1, 2])})。`,
    );
  });
});

describe("parseCitationHref", () => {
  it("只识别来源锚点", () => {
    expect(parseCitationHref(citationHref(3))).toBe(3);
    expect(parseCitationHref("https://example.com/a")).toBeNull();
    expect(parseCitationHref("#cite-1")).toBeNull();
  });

  it("连续引用不是单个锚点", () => {
    expect(parseCitationHref(citationGroupHref([1, 6, 10]))).toBeNull();
  });
});

describe("parseCitationIndices", () => {
  it("单个和连续都能拆出编号", () => {
    expect(parseCitationIndices(citationHref(3))).toEqual([3]);
    expect(parseCitationIndices(citationGroupHref([1, 6, 10]))).toEqual([1, 6, 10]);
  });

  it("拒绝坏片段", () => {
    expect(parseCitationIndices("#source-")).toBeNull();
    expect(parseCitationIndices("#source-0")).toBeNull();
    expect(parseCitationIndices("#source-1,x")).toBeNull();
  });
});
