// Version / freshness check.
//
// HEAD each pinned artifact URL and compare the returned ETag and
// X-Repo-Commit against what the meta store remembers from the last download.
// A change in either value means the user should be offered an "Update data"
// action (UI lives in Settings.tsx in Phase 6).

import { ARTIFACTS, headArtifact } from "./hf-fetch";
import type { ArtifactHead } from "./hf-fetch";
import type { MetaStore } from "./meta";

export interface ArtifactFreshness {
  filename: string;
  url: string;
  remote: ArtifactHead;
  current: { etag?: string; repoCommit?: string; contentLength?: number } | undefined;
  hasUpdate: boolean;
  reason: "fresh" | "etag-changed" | "repo-commit-changed" | "no-local-copy";
}

export async function checkAllForUpdates(meta: MetaStore): Promise<ArtifactFreshness[]> {
  return Promise.all(ARTIFACTS.map((a) => checkForUpdate(a.filename, a.url, meta)));
}

export async function checkForUpdate(
  filename: string,
  url: string,
  meta: MetaStore,
): Promise<ArtifactFreshness> {
  const remote = await headArtifact(url);
  const current = meta.get(filename);
  if (!current) {
    return {
      filename,
      url,
      remote,
      current: undefined,
      hasUpdate: true,
      reason: "no-local-copy",
    };
  }
  if (remote.etag && current.etag && remote.etag !== current.etag) {
    return {
      filename,
      url,
      remote,
      current,
      hasUpdate: true,
      reason: "etag-changed",
    };
  }
  if (remote.repoCommit && current.repoCommit && remote.repoCommit !== current.repoCommit) {
    return {
      filename,
      url,
      remote,
      current,
      hasUpdate: true,
      reason: "repo-commit-changed",
    };
  }
  return {
    filename,
    url,
    remote,
    current,
    hasUpdate: false,
    reason: "fresh",
  };
}
