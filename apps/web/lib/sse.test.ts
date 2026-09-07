/**
 * SSE 解析器（P1-11 验收）。
 *
 * 重点在**分块边界**：真实网络里帧会被任意切开，而单元测试如果总是一次喂一整帧，
 * 就永远发现不了跨 chunk 的 bug——那类 bug 的表现是偶发丢事件，极难复现。
 */

import { describe, expect, it, vi } from "vitest";

import { parseFrames, toResearchEvents } from "@/lib/sse";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

async function collect<T>(source: AsyncIterable<T>): Promise<T[]> {
  const items: T[] = [];
  for await (const item of source) items.push(item);
  return items;
}

describe("parseFrames", () => {
  it("解析出 id / event / data 三个字段", async () => {
    const frames = await collect(
      parseFrames(streamOf('id: 7\nevent: research\ndata: {"a":1}\n\n')),
    );

    expect(frames).toEqual([{ id: "7", event: "research", data: '{"a":1}' }]);
  });

  it("一个 chunk 里的多帧会全部产出", async () => {
    const frames = await collect(
      parseFrames(streamOf("id: 1\ndata: one\n\nid: 2\ndata: two\n\nid: 3\ndata: three\n\n")),
    );

    expect(frames.map((f) => f.data)).toEqual(["one", "two", "three"]);
  });

  it("帧被切成任意多个 chunk 也能拼回来", async () => {
    // 逐字符喂：这是最严苛的切分方式，能同时覆盖字段名、冒号、分隔空行的边界
    const raw = 'id: 42\nevent: research\ndata: {"seq":42}\n\n';
    const frames = await collect(parseFrames(streamOf(...raw.split(""))));

    expect(frames).toEqual([{ id: "42", event: "research", data: '{"seq":42}' }]);
  });

  it("忽略注释帧", async () => {
    // 服务端用它撑开响应头，客户端不该把它当事件
    const frames = await collect(parseFrames(streamOf(": stream open\n\ndata: real\n\n")));

    expect(frames.map((f) => f.data)).toEqual(["real"]);
  });

  it("多行 data 用换行拼接", async () => {
    const frames = await collect(parseFrames(streamOf("data: line1\ndata: line2\n\n")));

    expect(frames[0]!.data).toBe("line1\nline2");
  });

  it("只去掉冒号后的一个空格", async () => {
    // 用 trim() 会吃掉数据本身的缩进
    const frames = await collect(parseFrames(streamOf("data:  leading\n\n")));

    expect(frames[0]!.data).toBe(" leading");
  });

  it("兼容 CRLF 行终止符", async () => {
    const frames = await collect(parseFrames(streamOf("id: 1\r\ndata: hi\r\n\r\n")));

    expect(frames).toEqual([{ id: "1", event: null, data: "hi" }]);
  });

  it("CRLF 正好被切在中间时不会把一帧劈成两半", async () => {
    // 孤立的 CR 如果当场规范化成 LF，紧跟的 LF 会凭空造出一个空行
    const frames = await collect(parseFrames(streamOf("data: hi\r", "\n\r\n")));

    expect(frames.map((f) => f.data)).toEqual(["hi"]);
  });

  it("连接被截断时仍交出已经完整的最后一帧", async () => {
    // 没有收尾空行。丢掉它意味着丢掉终态事件——前端会永远转圈
    const frames = await collect(parseFrames(streamOf("data: first\n\ndata: last\n")));

    expect(frames.map((f) => f.data)).toEqual(["first", "last"]);
  });

  it("没有 data 字段的块不产出帧", async () => {
    const frames = await collect(parseFrames(streamOf("id: 9\nevent: research\n\n")));

    expect(frames).toEqual([]);
  });
});

describe("toResearchEvents", () => {
  it("把 data 反序列化成事件", async () => {
    const events = await collect(
      toResearchEvents(parseFrames(streamOf('data: {"seq":1,"type":"heartbeat"}\n\n'))),
    );

    expect(events[0]!.seq).toBe(1);
  });

  it("坏帧被跳过而不是终止整条流", async () => {
    // 后面可能还有终态事件，抛异常会让一个字段的序列化问题变成"永远转圈"
    const onMalformed = vi.fn();
    const events = await collect(
      toResearchEvents(
        parseFrames(streamOf('data: {"seq":1,"type":"heartbeat"}\n\ndata: {不是 JSON\n\n')),
        onMalformed,
      ),
    );

    expect(events).toHaveLength(1);
    expect(onMalformed).toHaveBeenCalledOnce();
  });
});
