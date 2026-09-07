import { fetchAgentHealth } from "@/lib/agent-client";

export const dynamic = "force-dynamic";

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      aria-hidden
      className={`inline-block size-2 rounded-full ${ok ? "bg-emerald-500" : "bg-zinc-400"}`}
    />
  );
}

function ProviderList({
  title,
  items,
}: {
  title: string;
  items: { provider: string; configured: boolean }[];
}) {
  return (
    <div>
      <h3 className="mb-2 text-xs font-medium tracking-wide text-zinc-500 uppercase">{title}</h3>
      <ul className="flex flex-wrap gap-2">
        {items.map((item) => (
          <li
            key={item.provider}
            className="flex items-center gap-1.5 rounded-md border border-zinc-200 px-2 py-1 text-xs dark:border-zinc-800"
          >
            <StatusDot ok={item.configured} />
            <span className={item.configured ? "" : "text-zinc-400"}>{item.provider}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default async function HomePage() {
  const agent = await fetchAgentHealth();

  return (
    <main className="mx-auto max-w-2xl px-6 py-16">
      <h1 className="text-2xl font-semibold">Market Research Agent</h1>
      <p className="mt-2 text-sm text-zinc-500">
        AI Financial Research Platform — Crypto &amp; US Stocks
      </p>

      <section className="mt-10 rounded-lg border border-zinc-200 p-5 dark:border-zinc-800">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium">Agent Service</h2>
          <span className="flex items-center gap-2 text-xs">
            <StatusDot ok={agent.ok} />
            {agent.ok ? `v${agent.data.version} · ${agent.data.status}` : "unreachable"}
          </span>
        </div>

        {agent.ok ? (
          <div className="mt-5 space-y-5">
            <ProviderList title="LLM Providers" items={agent.data.llm_providers} />
            <ProviderList title="Data Sources" items={agent.data.data_sources} />
            <p className="text-xs text-zinc-500">
              SDK tracing: {agent.data.tracing_enabled ? "已开启" : "已关闭"}
            </p>
          </div>
        ) : (
          <p className="mt-4 text-xs text-amber-600 dark:text-amber-500">{agent.error.message}</p>
        )}
      </section>

      <section className="mt-6 rounded-lg border border-dashed border-zinc-200 p-5 text-sm text-zinc-500 dark:border-zinc-800">
        <p className="font-medium text-zinc-700 dark:text-zinc-300">Phase 0 · 项目初始化</p>
        <p className="mt-1 text-xs">
          研究界面将在 Phase 1 接入。开发计划见{" "}
          <code className="rounded bg-zinc-100 px-1 py-0.5 dark:bg-zinc-800">
            docs/DEVELOPMENT_PLAN.md
          </code>
          ，进度见{" "}
          <code className="rounded bg-zinc-100 px-1 py-0.5 dark:bg-zinc-800">docs/ROADMAP.md</code>
          。
        </p>
      </section>
    </main>
  );
}
