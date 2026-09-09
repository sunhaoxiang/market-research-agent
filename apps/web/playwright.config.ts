import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

/**
 * 独立端口，禁止复用已在跑的 `pnpm dev`。
 * 否则会打到本机真实 Python / 真实 LLM，既贵也不稳定。
 *
 * 本地每次先 `next build`：已有的 `.next` 可能是几天前的产物，
 * `next start` 会跑出和源码不一致的 UI。
 */
const isCI = Boolean(process.env.CI);
const repoRoot = fileURLToPath(new URL("../..", import.meta.url));
const stubPort = Number(process.env.STUB_AGENT_PORT ?? 18000);
const webPort = Number(process.env.E2E_WEB_PORT ?? 3100);
const agentUrl = `http://127.0.0.1:${stubPort}`;
const baseURL = `http://127.0.0.1:${webPort}`;
const e2eDb = "file:./data/e2e.db";

const needBuild = !isCI;
const dbFiles = ["e2e.db", "e2e.db-wal", "e2e.db-shm", "e2e.db-journal"]
  .map((name) => `"${repoRoot}/data/${name}"`)
  .join(" ");

const chrome = devices["Desktop Chrome"];
if (!chrome) throw new Error("Playwright 缺少 Desktop Chrome 设备描述");

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: isCI,
  retries: isCI ? 1 : 0,
  timeout: 30_000,
  reporter: isCI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    ...chrome,
    baseURL,
    trace: "on-first-retry",
  },
  webServer: [
    {
      command: "node e2e/stub-agent.mjs",
      url: `${agentUrl}/v1/health`,
      reuseExistingServer: false,
      timeout: 30_000,
      env: { STUB_AGENT_PORT: String(stubPort) },
    },
    {
      command: [
        needBuild ? "pnpm build &&" : "",
        `rm -f ${dbFiles} &&`,
        "pnpm db:migrate &&",
        `pnpm exec next start --hostname 127.0.0.1 --port ${webPort}`,
      ]
        .filter(Boolean)
        .join(" "),
      url: `${baseURL}/api/health`,
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        DATABASE_URL: e2eDb,
        AGENT_SERVICE_URL: agentUrl,
        // 空字符串让 Next 不带 X-Internal-Token；桩服务也会忽略该头
        INTERNAL_API_TOKEN: "",
      },
    },
  ],
});
