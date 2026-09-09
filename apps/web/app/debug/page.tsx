import { AppHeader } from "@/components/research/app-header";
import { DebugDashboard } from "@/components/debug/debug-dashboard";
import { getDb } from "@/db/client";
import { loadDebugSnapshot } from "@/db/queries/debug";
import { fetchProviderDebug } from "@/lib/agent-client";

export const dynamic = "force-dynamic";

export default async function DebugPage() {
  const snapshot = loadDebugSnapshot(getDb());
  const providers = await fetchProviderDebug();

  return (
    <main id="main" className="mx-auto max-w-6xl px-6 py-12">
      <AppHeader current="debug" />
      <DebugDashboard
        snapshot={snapshot}
        providers={providers.ok ? providers.data.providers : []}
        providersError={providers.ok ? null : providers.error.message}
      />
    </main>
  );
}
