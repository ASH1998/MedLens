// Tiny helpers around sql.js prepared statements. Keeps tool code readable.

import type { Database } from "sql.js";

export function selectAll<T extends object>(
  db: Database,
  sql: string,
  params: unknown[] = [],
): T[] {
  const stmt = db.prepare(sql);
  try {
    if (params.length > 0) stmt.bind(params as never[]);
    const rows: T[] = [];
    while (stmt.step()) rows.push(stmt.getAsObject() as T);
    return rows;
  } finally {
    stmt.free();
  }
}

export function selectOne<T extends object>(
  db: Database,
  sql: string,
  params: unknown[] = [],
): T | null {
  const stmt = db.prepare(sql);
  try {
    if (params.length > 0) stmt.bind(params as never[]);
    if (stmt.step()) return stmt.getAsObject() as T;
    return null;
  } finally {
    stmt.free();
  }
}

export function relationExists(db: Database, name: string): boolean {
  const row = selectOne<{ n: number }>(
    db,
    "SELECT 1 AS n FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
    [name],
  );
  return row !== null;
}

export function jsonTuple(value: string | null | undefined): string[] {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    if (!Array.isArray(parsed)) return [];
    return parsed.map((v) => String(v));
  } catch {
    return [];
  }
}
