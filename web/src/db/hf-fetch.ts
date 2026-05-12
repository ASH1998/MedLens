// Hugging Face artifact fetch helpers for the MedLens PWA.
//
// Phase 1 surfaced HEAD-only preflight. Phase 2 adds streamed download with
// Content-Length-driven progress, ETag/X-Repo-Commit capture, and pause/resume
// via HTTP Range against a partial OPFS file.
//
// IMPORTANT: only the two URLs in ARTIFACT_URLS are ever fetched. Do not
// enumerate the dataset, list siblings, or download other artifacts even if
// they appear in `ASHu2/medlens`.

import type { BlobStore } from "./opfs";
import type { ArtifactMeta, MetaStore } from "./meta";

const HF_RESOLVE_BASE = "https://huggingface.co/datasets/ASHu2/medlens/resolve/main";

export const ARTIFACT_URLS = {
  normalization: `${HF_RESOLVE_BASE}/normalization.sqlite`,
  evidence: `${HF_RESOLVE_BASE}/evidence.mobile.sqlite`,
} as const satisfies Record<string, string>;

export type ArtifactKey = keyof typeof ARTIFACT_URLS;

export const ARTIFACTS: ReadonlyArray<{ key: ArtifactKey; filename: string; url: string }> = [
  { key: "normalization", filename: "normalization.sqlite", url: ARTIFACT_URLS.normalization },
  { key: "evidence", filename: "evidence.mobile.sqlite", url: ARTIFACT_URLS.evidence },
];

export interface ArtifactHead {
  url: string;
  contentLength: number | undefined;
  etag: string | undefined;
  repoCommit: string | undefined;
  acceptsRanges: boolean;
}

/**
 * HEAD the resolve/main URL and surface the headers the PWA relies on.
 * - `x-linked-size` is the LFS object size (preferred over `content-length`,
 *   which on the redirect refers to the redirect body).
 * - `x-linked-etag` is the LFS object SHA256 (content/integrity anchor).
 * - `x-repo-commit` is the dataset-change anchor.
 * - `accept-ranges: bytes` indicates resumable Range downloads work.
 */
export async function headArtifact(url: string): Promise<ArtifactHead> {
  const res = await fetch(url, { method: "HEAD", redirect: "follow" });
  if (!res.ok) throw new Error(`HEAD ${url} returned ${res.status} ${res.statusText}`);
  return parseHeadHeaders(url, res.headers);
}

function parseHeadHeaders(url: string, headers: Headers): ArtifactHead {
  const linkedSize = headers.get("x-linked-size");
  const contentLengthHeader = headers.get("content-length");
  const contentLength = linkedSize
    ? Number(linkedSize)
    : contentLengthHeader
      ? Number(contentLengthHeader)
      : undefined;
  return {
    url,
    contentLength,
    etag: headers.get("x-linked-etag") ?? headers.get("etag") ?? undefined,
    repoCommit: headers.get("x-repo-commit") ?? undefined,
    acceptsRanges: (headers.get("accept-ranges") ?? "").toLowerCase() === "bytes",
  };
}

export interface DownloadProgress {
  filename: string;
  url: string;
  receivedBytes: number;
  totalBytes: number | undefined;
  resumed: boolean;
}

export interface DownloadOptions {
  filename: string;
  url: string;
  store: BlobStore;
  meta: MetaStore;
  signal?: AbortSignal;
  onProgress?: (p: DownloadProgress) => void;
  /** Smallest chunk size between progress callbacks (bytes). */
  progressChunkBytes?: number;
}

export interface DownloadResult {
  filename: string;
  totalBytes: number;
  receivedBytes: number;
  resumed: boolean;
  meta: ArtifactMeta;
}

/**
 * Download `url` into `store[filename]` resumably.
 *
 * Resume semantics:
 * - If a partial file exists in `store` with a smaller size than the remote,
 *   AND persisted metadata's ETag still matches the remote ETag, append from
 *   the partial offset using `Range: bytes=<offset>-`.
 * - If the ETag changed (artifact updated upstream) or no metadata exists,
 *   start fresh — partial bytes are discarded.
 * - On `signal.abort()` the in-progress sink is closed (not aborted) so the
 *   partial file remains and can be resumed on the next call.
 */
export async function downloadArtifact(opts: DownloadOptions): Promise<DownloadResult> {
  const { filename, url, store, meta, signal, onProgress } = opts;
  const progressChunkBytes = opts.progressChunkBytes ?? 256 * 1024;

  const head = await headArtifact(url);
  const total = head.contentLength;

  const existingMeta = meta.get(filename);
  const existingSize = await store.size(filename);
  const etagMatches = !!existingMeta?.etag && !!head.etag && existingMeta.etag === head.etag;

  let resumed = false;
  let offset = 0;
  if (etagMatches && total !== undefined && existingSize > 0 && existingSize < total) {
    resumed = true;
    offset = existingSize;
  } else if (existingSize > 0 && (!etagMatches || total === existingSize)) {
    // Either upstream changed (etag mismatch) or partial happens to equal
    // total but we cannot trust integrity without etag — discard and refetch.
    if (!etagMatches) await store.delete(filename);
    if (total !== undefined && existingSize === total && etagMatches) {
      // Already fully downloaded and matches. Short-circuit.
      const updated: ArtifactMeta = {
        etag: head.etag,
        repoCommit: head.repoCommit,
        contentLength: total,
        updatedAt: new Date().toISOString(),
      };
      meta.set(filename, updated);
      onProgress?.({
        filename,
        url,
        receivedBytes: total,
        totalBytes: total,
        resumed: false,
      });
      return { filename, totalBytes: total, receivedBytes: total, resumed: false, meta: updated };
    }
  }

  const reqHeaders: Record<string, string> = {};
  if (resumed) reqHeaders["Range"] = `bytes=${offset}-`;

  const res = await fetch(url, { headers: reqHeaders, redirect: "follow", signal });
  if (!res.ok && !(resumed && res.status === 206)) {
    throw new Error(`GET ${url} returned ${res.status} ${res.statusText}`);
  }
  if (!res.body) throw new Error(`GET ${url} returned empty body`);

  // Re-confirm ETag from the GET response in case we resumed against a
  // different revision than HEAD reported (rare but cheap to detect).
  const getHead = parseHeadHeaders(url, res.headers);
  if (resumed && getHead.etag && head.etag && getHead.etag !== head.etag) {
    // Upstream changed between HEAD and GET — restart fresh.
    await store.delete(filename);
    return downloadArtifact(opts);
  }

  const sink = await store.openAppend(filename, offset);
  let received = offset;
  let lastReportedAt = received;
  const reader = res.body.getReader();

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      if (signal?.aborted) {
        await sink.close(); // keep partial bytes
        throw new DOMException("aborted", "AbortError");
      }
      await sink.write(value);
      received += value.byteLength;
      if (received - lastReportedAt >= progressChunkBytes) {
        lastReportedAt = received;
        onProgress?.({
          filename,
          url,
          receivedBytes: received,
          totalBytes: total,
          resumed,
        });
      }
    }
    await sink.close();
  } catch (err) {
    await sink.close().catch(() => undefined);
    throw err;
  }

  // Final progress tick at 100%.
  onProgress?.({
    filename,
    url,
    receivedBytes: received,
    totalBytes: total ?? received,
    resumed,
  });

  const updated: ArtifactMeta = {
    etag: head.etag,
    repoCommit: head.repoCommit,
    contentLength: total ?? received,
    updatedAt: new Date().toISOString(),
  };
  meta.set(filename, updated);

  return {
    filename,
    totalBytes: total ?? received,
    receivedBytes: received,
    resumed,
    meta: updated,
  };
}
