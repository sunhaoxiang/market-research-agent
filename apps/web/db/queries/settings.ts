/**
 * 用户设置 KV（P6-8）。单用户，key 固定为 `preferences`。
 */

import { eq } from "drizzle-orm";

import type { Db } from "@/db/client";
import { settings } from "@/db/schema";
import { LOCAL_USER_ID } from "@/db/queries/sessions";
import { parsePreferences, type UserPreferences } from "@/lib/settings";

export const PREFERENCES_KEY = "preferences";

export function getPreferences(db: Db): UserPreferences {
  const row = db.select().from(settings).where(eq(settings.key, PREFERENCES_KEY)).get();
  return parsePreferences(row?.value);
}

export function putPreferences(db: Db, value: UserPreferences): UserPreferences {
  const parsed = parsePreferences(value);
  const now = Date.now();
  db.insert(settings)
    .values({
      key: PREFERENCES_KEY,
      userId: LOCAL_USER_ID,
      value: parsed,
      updatedAt: now,
    })
    .onConflictDoUpdate({
      target: settings.key,
      set: { value: parsed, updatedAt: now, userId: LOCAL_USER_ID },
    })
    .run();
  return parsed;
}
