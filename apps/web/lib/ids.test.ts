/**
 * UUIDv7（P1-11）。
 *
 * 单调性是选 v7 的**全部理由**，所以它必须有测试兜着：一旦退化成随机排序，
 * `ORDER BY id` 会悄悄给出错误的事件顺序，而没有任何地方会报错。
 */

import { describe, expect, it } from "vitest";

import { newId } from "@/lib/ids";

describe("newId", () => {
  it("符合 UUID 的格式，版本位是 7、variant 是 RFC 4122", () => {
    const id = newId();

    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });

  it("前 48 bit 是当前 Unix 毫秒", () => {
    const before = Date.now();
    const tsMs = Number.parseInt(newId().replace(/-/g, "").slice(0, 12), 16);

    expect(tsMs).toBeGreaterThanOrEqual(before);
    expect(tsMs).toBeLessThanOrEqual(Date.now());
  });

  it("同一毫秒内批量生成也严格单调递增", () => {
    // 循环足够快，绝大多数 id 会落在同一毫秒里——正是靠 rand_a 当计数器
    // 才能保住顺序。纯随机的 rand_a 在这里必然失败
    const ids = Array.from({ length: 5000 }, () => newId());

    expect(ids).toEqual([...ids].sort());
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("字典序等于时间序", () => {
    const early = newId();
    const later = newId();

    expect(early < later).toBe(true);
  });
});
