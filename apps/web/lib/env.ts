/**
 * 服务端环境变量。
 *
 * ⚠️ 本模块只能在服务端（Route Handler / Server Component / 脚本）引入。
 * 严禁使用 NEXT_PUBLIC_ 前缀承载 secret（会被打进浏览器 bundle）。
 * 见 DEVELOPMENT_PLAN.md §17.1。
 */

import "server-only";

import { existsSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";

/**
 * 向上查找含 pnpm-workspace.yaml 的目录作为仓库根。
 *
 * 必要性：DATABASE_URL 里的相对路径若按 process.cwd() 解析，
 * `next dev`（cwd = apps/web）与 drizzle-kit CLI、Python 服务（cwd = 仓库根）
 * 会指向不同的数据库文件。统一以仓库根为基准可消除这个歧义。
 */
export function findRepoRoot(startDir: string = process.cwd()): string {
  let dir = resolve(startDir);
  for (;;) {
    if (existsSync(resolve(dir, "pnpm-workspace.yaml"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) return resolve(startDir);
    dir = parent;
  }
}

function required(name: string, fallback?: string): string {
  const value = process.env[name] ?? fallback;
  if (value === undefined || value === "") {
    throw new Error(`缺少必需的环境变量 ${name}。参考仓库根目录的 .env.example`);
  }
  return value;
}

function optional(name: string): string | undefined {
  const value = process.env[name];
  return value === "" ? undefined : value;
}

export const serverEnv = {
  /** file:./data/app.db → ./data/app.db */
  databaseUrl: required("DATABASE_URL", "file:./data/app.db"),
  agentServiceUrl: required("AGENT_SERVICE_URL", "http://127.0.0.1:8000"),
  internalApiToken: optional("INTERNAL_API_TOKEN"),
  nodeEnv: process.env.NODE_ENV ?? "development",
} as const;

/**
 * 把 DATABASE_URL 转成 better-sqlite3 需要的绝对文件路径。
 *
 * 剥掉 `file:` 前缀，并把相对路径按仓库根解析（见 findRepoRoot 的说明）。
 */
export function resolveSqlitePath(databaseUrl: string = serverEnv.databaseUrl): string {
  const raw = databaseUrl.startsWith("file:") ? databaseUrl.slice("file:".length) : databaseUrl;
  if (raw === ":memory:" || isAbsolute(raw)) return raw;
  return resolve(findRepoRoot(), raw);
}
