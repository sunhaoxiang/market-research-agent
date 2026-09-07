/**
 * UUIDv7 生成（TS 侧，与 `services/agent/.../utils/ids.py` 语义一致）。
 *
 * 为什么不用 `crypto.randomUUID()`：那是 v4，纯随机。§10.1 选 v7 是因为它前
 * 48 bit 是 Unix 毫秒时间戳，字典序即时间序——SQLite 主键索引接近顺序写入，
 * `ORDER BY id` 也不需要额外索引。`research_events` 是成批写入的，用 v4 会让
 * 每批 20 条散落到 B-tree 的各个页上。
 *
 * 结构见 RFC 9562 §5.7：
 *
 *     |unix_ts_ms (48 bit)                    |ver(4)|rand_a (12) |
 *     |var(2)|rand_b (62 bit)                                     |
 *
 * `rand_a` 按 RFC 9562 §6.2 Method 1 用作同毫秒内的单调计数器，而非纯随机。
 * 否则同一毫秒生成的多个 id 顺序是随机的，而我们要求 `ORDER BY id` 严格等于
 * 插入顺序。
 */

const COUNTER_BITS = 12;
const COUNTER_MAX = (1 << COUNTER_BITS) - 1;
/** 新毫秒的计数器起点上限。取一半空间，给同毫秒内的递增留出 2048 的余量。 */
const COUNTER_SEED_MAX = 1 << (COUNTER_BITS - 1);

let lastTsMs = -1;
let counter = 0;

/**
 * 时间戳 + 同毫秒计数器。
 *
 * 不需要锁：Node 的 JS 执行是单线程的，这个函数里没有 await，
 * 因此整体是原子的（Python 侧要加锁是因为那边混用 asyncio 与线程池）。
 */
function nextTick(): { tsMs: number; counter: number } {
  let tsMs = Date.now();

  if (tsMs > lastTsMs) {
    lastTsMs = tsMs;
    // 随机起点而非固定 0：避免从 id 反推该毫秒内的生成数量
    counter = randomInt(COUNTER_SEED_MAX);
  } else if (counter < COUNTER_MAX) {
    counter += 1;
  } else {
    // 同毫秒内耗尽计数空间：借用下一毫秒，宁可时间戳略微超前也要保持单调
    lastTsMs += 1;
    counter = 0;
    tsMs = lastTsMs;
  }

  return { tsMs, counter };
}

function randomInt(exclusiveMax: number): number {
  return crypto.getRandomValues(new Uint16Array(1))[0]! % exclusiveMax;
}

function hex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** 生成字符串形式的 UUIDv7，用于所有业务主键。同一进程内严格单调递增。 */
export function newId(): string {
  const { tsMs, counter: seq } = nextTick();
  const bytes = new Uint8Array(16);

  // 前 48 bit 时间戳。`Date.now()` 最大约 2^43，用位运算会溢出 32 bit，
  // 所以高 16 bit 走除法而不是 >>>
  bytes[0] = Math.floor(tsMs / 2 ** 40) & 0xff;
  bytes[1] = Math.floor(tsMs / 2 ** 32) & 0xff;
  bytes[2] = (tsMs >>> 24) & 0xff;
  bytes[3] = (tsMs >>> 16) & 0xff;
  bytes[4] = (tsMs >>> 8) & 0xff;
  bytes[5] = tsMs & 0xff;

  // version = 7，随后 12 bit 是单调计数器
  bytes[6] = 0x70 | ((seq >>> 8) & 0x0f);
  bytes[7] = seq & 0xff;

  crypto.getRandomValues(bytes.subarray(8));
  // variant = RFC 4122（最高两位 0b10）
  bytes[8] = 0x80 | (bytes[8]! & 0x3f);

  const text = hex(bytes);
  return [
    text.slice(0, 8),
    text.slice(8, 12),
    text.slice(12, 16),
    text.slice(16, 20),
    text.slice(20),
  ].join("-");
}
