// Persisted artifact metadata: ETag (LFS content/integrity anchor) and
// X-Repo-Commit (dataset-change anchor). Keyed by the artifact filename so
// future versions of the same artifact overwrite cleanly.
//
// Backed by localStorage in browsers; tests inject MemoryMetaStore.

export interface ArtifactMeta {
  etag: string | undefined;
  repoCommit: string | undefined;
  contentLength: number | undefined;
  /** ISO timestamp when this metadata was last updated. */
  updatedAt: string;
}

export interface MetaStore {
  get(name: string): ArtifactMeta | undefined;
  set(name: string, meta: ArtifactMeta): void;
  clear(name: string): void;
}

const KEY_PREFIX = "medlens.artifact.";

export class LocalStorageMetaStore implements MetaStore {
  get(name: string): ArtifactMeta | undefined {
    if (typeof localStorage === "undefined") return undefined;
    const raw = localStorage.getItem(KEY_PREFIX + name);
    if (!raw) return undefined;
    try {
      return JSON.parse(raw) as ArtifactMeta;
    } catch {
      return undefined;
    }
  }

  set(name: string, meta: ArtifactMeta): void {
    if (typeof localStorage === "undefined") return;
    localStorage.setItem(KEY_PREFIX + name, JSON.stringify(meta));
  }

  clear(name: string): void {
    if (typeof localStorage === "undefined") return;
    localStorage.removeItem(KEY_PREFIX + name);
  }
}

export class MemoryMetaStore implements MetaStore {
  private map = new Map<string, ArtifactMeta>();
  get(name: string) {
    return this.map.get(name);
  }
  set(name: string, meta: ArtifactMeta) {
    this.map.set(name, meta);
  }
  clear(name: string) {
    this.map.delete(name);
  }
}

let defaultStore: MetaStore | null = null;
export function getDefaultMetaStore(): MetaStore {
  if (!defaultStore) defaultStore = new LocalStorageMetaStore();
  return defaultStore;
}
