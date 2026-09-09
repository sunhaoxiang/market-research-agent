/**
 * `/debug` 快照（P6-9）。页面自己也会直接查库；这个端点给 curl / 以后轮询用。
 */

import { getDb } from "@/db/client";
import { loadDebugSnapshot } from "@/db/queries/debug";
import { fetchProviderDebug } from "@/lib/agent-client";

export const dynamic = "force-dynamic";

export async function GET() {
  const snapshot = loadDebugSnapshot(getDb());
  const providers = await fetchProviderDebug();
  return Response.json({
    ...snapshot,
    providers: providers.ok ? providers.data.providers : [],
    providersError: providers.ok ? null : providers.error,
  });
}
