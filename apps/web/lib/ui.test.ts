import { describe, expect, it } from "vitest";

import { notice, surface, surfaceMuted } from "@/lib/ui";

describe("surface / notice", () => {
  it("普通区块和强调区块同圆角同边框，只换底", () => {
    expect(surface).toContain("rounded-lg");
    expect(surface).toContain("border-border");
    expect(surfaceMuted).toContain("rounded-lg");
    expect(surfaceMuted).toContain("border-border");
    expect(surfaceMuted).toContain("bg-muted");
    expect(surface).toContain("bg-background");
  });

  it("警告跟普通区块同圆角同阴影，只换琥珀底", () => {
    expect(notice).toContain("rounded-lg");
    expect(notice).toContain("border-amber-300");
    expect(notice).toContain("shadow-[0_1px_2px_rgba(9,9,11,0.04)]");
    expect(surface).toContain("shadow-[0_1px_2px_rgba(9,9,11,0.04)]");
  });
});
