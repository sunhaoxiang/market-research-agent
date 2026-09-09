import { describe, expect, it } from "vitest";

import { percentile, rate } from "@/lib/debug-stats";

describe("percentile", () => {
  it("空样本是 null 而不是 0", () => {
    expect(percentile([], 95)).toBeNull();
  });

  it("单点就是它自己", () => {
    expect(percentile([40], 95)).toBe(40);
  });

  it("五个样本的 p95 取最大", () => {
    expect(percentile([10, 20, 30, 40, 50], 95)).toBe(50);
    expect(percentile([10, 20, 30, 40, 50], 50)).toBe(30);
  });
});

describe("rate", () => {
  it("没有分母时不编 0%", () => {
    expect(rate(0, 0)).toBeNull();
    expect(rate(1, 4)).toBe(0.25);
  });
});
