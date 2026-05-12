// Database stores: `normalizationDb` opens immediately after first-run
// download finishes; `evidenceDb` opens lazily on first interaction tool call,
// per docs/pwa_plan.md Phase 2.

import { openDatabase } from "./sqlite";
import type { Database } from "./sqlite";
import type { BlobStore } from "./opfs";
import { ARTIFACTS } from "./hf-fetch";

const FILENAMES = Object.fromEntries(ARTIFACTS.map((a) => [a.key, a.filename])) as Record<
  (typeof ARTIFACTS)[number]["key"],
  string
>;

export interface DbHandles {
  normalization: Database;
  /** Lazily opened on first call; cached afterwards. */
  evidence(): Promise<Database>;
  close(): void;
}

export async function openDbHandles(store: BlobStore): Promise<DbHandles> {
  const normalizationBytes = await store.read(FILENAMES.normalization);
  const normalization = await openDatabase(normalizationBytes);

  let evidence: Database | null = null;
  let evidencePromise: Promise<Database> | null = null;

  return {
    normalization,
    async evidence() {
      if (evidence) return evidence;
      if (!evidencePromise) {
        evidencePromise = (async () => {
          const bytes = await store.read(FILENAMES.evidence);
          evidence = await openDatabase(bytes);
          return evidence;
        })();
      }
      return evidencePromise;
    },
    close() {
      try {
        normalization.close();
      } catch {
        // ignore
      }
      if (evidence) {
        try {
          evidence.close();
        } catch {
          // ignore
        }
      }
    },
  };
}
