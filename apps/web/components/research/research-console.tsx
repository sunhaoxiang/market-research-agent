"use client";

import { useEffect, useRef, useState } from "react";

import { MetricCharts } from "@/components/report/metric-charts";
import { ReportViewer } from "@/components/report/report-viewer";
import { LiveStatus } from "@/components/research/live-status";
import { ProcessPanel } from "@/components/research/process-panel";
import { StageTimeline } from "@/components/research/stage-timeline";
import { SourcePanel } from "@/components/sources/source-panel";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/field";
import type { RecentSessionPreview } from "@/lib/research/empty-home";
import { replayAndFollow } from "@/lib/research/replay";
import { useResearchState, useResearchStore, useTicker } from "@/lib/research/store";
import { ResearchStreamError, streamResearch } from "@/lib/sse";
import { notice, surface } from "@/lib/ui";
import { cn } from "@/lib/utils";

import { EmptyHome } from "./empty-home";
import { type ModelOption, ModelSelector } from "./model-selector";
import { SessionHeader } from "./session-header";

export function ResearchConsole({
  models,
  initialSessionId,
  defaultModelId,
  costLimitUsd,
  recent,
}: {
  models: ModelOption[];
  initialSessionId?: string;
  defaultModelId?: string;
  costLimitUsd: number;
  recent: RecentSessionPreview[];
}) {
  const store = useResearchStore();
  const state = useResearchState(store);
  const [question, setQuestion] = useState("");
  const [modelId, setModelId] = useState(defaultModelId ?? "");
  const [activeCitation, setActiveCitation] = useState<number | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [restoring, setRestoring] = useState(Boolean(initialSessionId));
  const abortRef = useRef<AbortController | null>(null);

  const running = state.status === "running";
  const idle = state.status === "idle" && !restoring;

  useEffect(() => {
    const sessionId =
      initialSessionId ?? new URLSearchParams(window.location.search).get("session");
    if (!sessionId) return;
    const controller = new AbortController();
    abortRef.current = controller;
    void (async () => {
      try {
        await replayAndFollow(sessionId, store, controller.signal);
      } catch (error) {
        if (controller.signal.aborted) return;
        store.failStream("REPLAY_ERROR", error instanceof Error ? error.message : "无法回放会话");
      } finally {
        if (!controller.signal.aborted) setRestoring(false);
      }
    })();
    return () => {
      controller.abort();
    };
  }, [initialSessionId, store]);

  async function submit() {
    const trimmed = question.trim();
    if (!trimmed || running) return;

    abortRef.current?.abort();
    store.reset();
    setActiveCitation(null);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      for await (const event of streamResearch(
        { question: trimmed, modelId: modelId || undefined },
        { signal: controller.signal, onSessionId: rememberSession },
      )) {
        store.apply(event);
      }
    } catch (error) {
      if (controller.signal.aborted) return;

      const detail =
        error instanceof ResearchStreamError
          ? ((error.detail as { error?: { code?: string; message?: string } } | null)?.error ??
            null)
          : null;
      store.failStream(
        detail?.code ?? "STREAM_ERROR",
        detail?.message ?? (error instanceof Error ? error.message : "研究流中断"),
      );
    }
  }

  async function cancel() {
    const sessionId = state.sessionId;
    if (!sessionId || !running || cancelling) return;
    setCancelling(true);
    try {
      const response = await fetch(`/api/research/${encodeURIComponent(sessionId)}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as {
          error?: { message?: string };
        } | null;
        store.failStream("CANCEL_FAILED", body?.error?.message ?? "取消失败");
      }
    } finally {
      setCancelling(false);
    }
  }

  function startFresh() {
    abortRef.current?.abort();
    store.reset();
    setQuestion("");
    setActiveCitation(null);
    setRestoring(false);
    const url = new URL(window.location.href);
    url.searchParams.delete("session");
    window.history.replaceState(null, "", url);
  }

  const now = useTicker(running);

  return (
    <div className="space-y-6">
      <LiveStatus state={state} />
      {idle ? (
        <EmptyHome recent={recent} onPickExample={setQuestion}>
          <form
            onSubmit={(submitEvent) => {
              submitEvent.preventDefault();
              void submit();
            }}
            className="space-y-3"
          >
            <Textarea
              value={question}
              onChange={(changeEvent) => setQuestion(changeEvent.target.value)}
              onKeyDown={(keyEvent) => {
                if (keyEvent.key === "Enter" && (keyEvent.metaKey || keyEvent.ctrlKey)) {
                  keyEvent.preventDefault();
                  void submit();
                }
              }}
              placeholder="问一个 Crypto / 美股 / 宏观的研究问题…"
              rows={5}
              maxLength={2000}
              disabled={running}
              aria-label="研究问题"
              className="min-h-[8rem] px-4 py-3 text-base leading-relaxed"
            />

            <div className="flex flex-wrap items-center justify-between gap-3">
              <ModelSelector
                models={models}
                value={modelId}
                onChange={setModelId}
                disabled={running}
              />
              <Button
                type="submit"
                disabled={running || question.trim().length === 0}
                className="px-5 py-2.5"
              >
                开始研究
              </Button>
            </div>
          </form>
        </EmptyHome>
      ) : (
        <div className="space-y-5" aria-busy={running || restoring}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <p className="max-w-3xl text-base leading-snug font-medium tracking-tight text-zinc-900 dark:text-zinc-100">
              {state.question ?? (restoring ? "正在恢复会话…" : question)}
            </p>
            <div className="flex shrink-0 items-center gap-2">
              {running && (
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => void cancel()}
                  disabled={cancelling}
                >
                  {cancelling ? "取消中…" : "取消研究"}
                </Button>
              )}
              <Button type="button" variant="ghost" onClick={startFresh}>
                新研究
              </Button>
            </div>
          </div>

          <div className={cn(surface, "space-y-3 px-4 py-3")}>
            <SessionHeader state={state} now={now} costLimitUsd={costLimitUsd} />
            <StageTimeline state={state} now={now} />
          </div>

          {state.lastMessage && (
            <p className="text-sm text-zinc-600 dark:text-zinc-400">{state.lastMessage}</p>
          )}

          {state.error && (
            <p className={cn(notice, "px-3 py-2 text-sm")}>
              {state.error.code}: {state.error.message}
            </p>
          )}

          <ProcessPanel state={state} now={now} />

          <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_18rem]">
            {state.report ? (
              <ReportViewer
                report={state.report}
                sources={state.sources}
                claims={state.claims}
                conflicts={state.conflicts}
                metrics={state.metrics}
                activeIndex={activeCitation}
                onCite={setActiveCitation}
              />
            ) : (
              <div className="space-y-3">
                <h2 className="text-[11px] font-medium tracking-[0.16em] text-zinc-500 uppercase">
                  报告
                </h2>
                <MetricCharts metrics={state.metrics} />
                {state.plan ? (
                  <div className="max-w-[42rem] space-y-3">
                    <p className="text-[15px] leading-[1.75] text-zinc-800 dark:text-zinc-200">
                      {state.plan.interpretation}
                    </p>
                    <ol className="list-decimal space-y-1.5 pl-5 text-[15px] leading-relaxed text-zinc-600 dark:text-zinc-400">
                      {state.plan.tasks.map((task) => (
                        <li key={task.id}>{task.objective}</li>
                      ))}
                    </ol>
                    {state.plan.assumptions.length > 0 && (
                      <div>
                        <p className="text-xs font-medium text-zinc-500">研究假设</p>
                        <ul className="list-disc pl-5 text-xs text-zinc-500">
                          {state.plan.assumptions.map((assumption) => (
                            <li key={assumption}>{assumption}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    <p className="border-border border-t pt-3 text-xs text-zinc-400">
                      {state.stage === "writing" ? "正在撰写研究报告…" : "报告生成后显示。"}
                    </p>
                  </div>
                ) : (
                  <p className="text-sm text-zinc-400">计划生成后显示。</p>
                )}
              </div>
            )}

            <SourcePanel
              sources={state.sources}
              activeIndex={activeCitation}
              onSelect={setActiveCitation}
              className="lg:self-start"
            />
          </div>
        </div>
      )}
    </div>
  );
}

function rememberSession(sessionId: string): void {
  const url = new URL(window.location.href);
  url.searchParams.set("session", sessionId);
  window.history.replaceState(null, "", url);
}
