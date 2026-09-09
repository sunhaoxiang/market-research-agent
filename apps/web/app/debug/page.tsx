import type { Metadata } from "next";

import { DebugDashboard } from "@/components/debug/debug-dashboard";
import { getDb } from "@/db/client";
import { loadDebugSnapshot } from "@/db/queries/debug";
import { fetchProviderDebug } from "@/lib/agent-client";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "调试" };

export default async function DebugPage() {
  const snapshot = loadDebugSnapshot(getDb());
  const providers = await fetchProviderDebug();

  return (
    <main id="main" className="mx-auto max-w-6xl px-6 py-6">
      <h1 className="mb-6 text-lg font-semibold tracking-tight">调试</h1>
      <DebugDashboard
        snapshot={snapshot}
        providers={providers.ok ? providers.data.providers : []}
        providersError={providers.ok ? null : providers.error.message}
      />
    </main>
  );
}
