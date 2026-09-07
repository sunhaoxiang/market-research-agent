import { existsSync, mkdirSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";

import { config } from "dotenv";
import { defineConfig } from "drizzle-kit";

// migration 由 CLI 执行，此时 Next.js 未加载，需手动读取根目录的 env 文件。
// 注意：不能复用 lib/env.ts，它带 "server-only" 标记，无法在 CLI 中引入。
config({ path: ["../../.env.local", "../../.env"], quiet: true });

/** 与 lib/env.ts 的 findRepoRoot 保持一致：相对路径一律按仓库根解析。 */
function findRepoRoot(startDir: string = process.cwd()): string {
  let dir = resolve(startDir);
  for (;;) {
    if (existsSync(resolve(dir, "pnpm-workspace.yaml"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) return resolve(startDir);
    dir = parent;
  }
}

const databaseUrl = process.env.DATABASE_URL ?? "file:./data/app.db";
const raw = databaseUrl.startsWith("file:") ? databaseUrl.slice("file:".length) : databaseUrl;
const sqlitePath = isAbsolute(raw) ? raw : resolve(findRepoRoot(), raw);

// drizzle-kit 不会自动创建目录
mkdirSync(dirname(sqlitePath), { recursive: true });

export default defineConfig({
  dialect: "sqlite",
  schema: "./db/schema.ts",
  out: "./db/migrations",
  dbCredentials: { url: sqlitePath },
  strict: true,
  verbose: true,
});
