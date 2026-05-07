// Thin wrapper around sql.js for opening read-only databases from bytes that
// were downloaded into OPFS. v1 keeps both DBs in the WASM heap as Uint8Array
// inputs — acceptable for normalization (~7 MB) and the mobile evidence
// artifact (~73 MB). If evidence size grows past ~150 MB, swap to wa-sqlite +
// OPFS-SAH per the plan.

import initSqlJs from "sql.js";
import type { Database, SqlJsStatic } from "sql.js";
// Vite resolves this to a hashed asset URL the browser can fetch.
import sqlWasmUrl from "sql.js/dist/sql-wasm.wasm?url";

let sqlJsPromise: Promise<SqlJsStatic> | null = null;

export function loadSqlJs(): Promise<SqlJsStatic> {
  if (!sqlJsPromise) {
    sqlJsPromise = initSqlJs({ locateFile: () => sqlWasmUrl });
  }
  return sqlJsPromise;
}

export async function openDatabase(bytes: Uint8Array): Promise<Database> {
  const SQL = await loadSqlJs();
  return new SQL.Database(bytes);
}

export type { Database } from "sql.js";

/** Convenience: count rows in `table` against an open database. */
export function countRows(db: Database, table: string): number {
  const stmt = db.prepare(`SELECT COUNT(*) AS n FROM ${table}`);
  try {
    stmt.step();
    const row = stmt.getAsObject() as { n: number };
    return Number(row.n);
  } finally {
    stmt.free();
  }
}
