/**
 * 数据库客户端。Next.js 是数据库的唯一 writer（DEVELOPMENT_PLAN.md 决策 C）。
 *
 * PRAGMA 设置理由（§10.3）：
 *   - WAL：读写不互相阻塞
 *   - synchronous=NORMAL：WAL 下足够安全，写入快得多
 *   - busy_timeout：遇到锁时等待而不是立刻报错
 *   - foreign_keys：SQLite 默认关闭外键约束，必须显式开启
 */

import "server-only";

import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";

import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";

import { resolveSqlitePath, serverEnv } from "@/lib/env";

import * as schema from "./schema";

export function createSqliteConnection(filePath: string): Database.Database {
  if (filePath !== ":memory:") {
    mkdirSync(dirname(resolve(filePath)), { recursive: true });
  }

  const connection = new Database(filePath);
  connection.pragma("journal_mode = WAL");
  connection.pragma("synchronous = NORMAL");
  connection.pragma("busy_timeout = 5000");
  connection.pragma("foreign_keys = ON");
  return connection;
}

export function createDb(filePath: string) {
  return drizzle(createSqliteConnection(filePath), { schema });
}

export type Db = ReturnType<typeof createDb>;

// 缓存到 globalThis：dev 模式下 Next.js 热重载模块，否则会泄漏连接
const globalForDb = globalThis as unknown as { __mraDb?: Db };

/**
 * 获取数据库连接（懒初始化）。
 *
 * 必须是懒的：模块加载时就建连接会导致 `next build` 在收集路由元数据时
 * 创建数据库文件，产生不该有的构建期副作用。
 */
export function getDb(): Db {
  return (globalForDb.__mraDb ??= createDb(resolveSqlitePath(serverEnv.databaseUrl)));
}

export { schema };
