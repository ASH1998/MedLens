import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import {
  ARTIFACT_URLS,
  ARTIFACTS,
  downloadArtifact,
  headArtifact,
} from "../hf-fetch";
import { MemoryBlobStore } from "../opfs";
import { MemoryMetaStore } from "../meta";

describe("hf-fetch URL pinning", () => {
  it("pins both artifact URLs to the public ASHu2/medlens dataset", () => {
    expect(ARTIFACT_URLS.normalization).toBe(
      "https://huggingface.co/datasets/ASHu2/medlens/resolve/main/normalization.sqlite",
    );
    expect(ARTIFACT_URLS.evidence).toBe(
      "https://huggingface.co/datasets/ASHu2/medlens/resolve/main/evidence.mobile.sqlite",
    );
    expect(ARTIFACTS).toHaveLength(2);
    expect(ARTIFACTS.map((a) => a.filename).sort()).toEqual([
      "evidence.mobile.sqlite",
      "normalization.sqlite",
    ]);
  });
});

describe("headArtifact", () => {
  const realFetch = globalThis.fetch;
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it("parses HF HEAD headers", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(null, {
          status: 200,
          headers: {
            "x-linked-size": "7409664",
            "x-linked-etag": '"50356a54fb3d6ec131044ddc6b72bad02eea4a7c"',
            "x-repo-commit": "b3933aca1510fc8f12fd47a5280da7a0b8c3a88a",
            "accept-ranges": "bytes",
          },
        }),
    ) as typeof fetch;
    const head = await headArtifact(ARTIFACT_URLS.normalization);
    expect(head.contentLength).toBe(7409664);
    expect(head.etag).toContain("50356a54");
    expect(head.repoCommit).toBe("b3933aca1510fc8f12fd47a5280da7a0b8c3a88a");
    expect(head.acceptsRanges).toBe(true);
  });

  it("throws on non-2xx HEAD responses", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response(null, { status: 401, statusText: "Unauthorized" }),
    ) as typeof fetch;
    await expect(headArtifact(ARTIFACT_URLS.evidence)).rejects.toThrow(/401/);
  });
});

describe("downloadArtifact", () => {
  const realFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  function streamResponse(bytes: Uint8Array, headers: HeadersInit, status = 200) {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        // Split into 3 chunks to exercise the progress loop.
        const third = Math.max(1, Math.ceil(bytes.byteLength / 3));
        for (let i = 0; i < bytes.byteLength; i += third) {
          controller.enqueue(bytes.slice(i, i + third));
        }
        controller.close();
      },
    });
    return new Response(stream, { status, headers });
  }

  it("downloads fresh, persists meta, and reports progress", async () => {
    const payload = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9]);
    const baseHeaders = {
      "x-linked-size": String(payload.byteLength),
      "x-linked-etag": '"etag-v1"',
      "x-repo-commit": "commit-v1",
      "accept-ranges": "bytes",
    };
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "HEAD") {
        return new Response(null, { status: 200, headers: baseHeaders });
      }
      return streamResponse(payload, baseHeaders);
    });
    globalThis.fetch = fetchMock as typeof fetch;

    const store = new MemoryBlobStore();
    const meta = new MemoryMetaStore();
    const progress: number[] = [];
    const result = await downloadArtifact({
      filename: "normalization.sqlite",
      url: ARTIFACT_URLS.normalization,
      store,
      meta,
      onProgress: (p) => progress.push(p.receivedBytes),
      progressChunkBytes: 1,
    });

    expect(result.totalBytes).toBe(payload.byteLength);
    expect(result.receivedBytes).toBe(payload.byteLength);
    expect(result.resumed).toBe(false);
    expect(Array.from(await store.read("normalization.sqlite"))).toEqual(Array.from(payload));
    expect(meta.get("normalization.sqlite")?.etag).toBe('"etag-v1"');
    expect(meta.get("normalization.sqlite")?.repoCommit).toBe("commit-v1");
    expect(progress.at(-1)).toBe(payload.byteLength);
  });

  it("resumes from partial offset when ETag matches", async () => {
    const payload = new Uint8Array([10, 11, 12, 13, 14, 15, 16, 17, 18, 19]);
    const baseHeaders = {
      "x-linked-size": String(payload.byteLength),
      "x-linked-etag": '"etag-resume"',
      "x-repo-commit": "commit-x",
      "accept-ranges": "bytes",
    };

    // Pre-seed: half the payload already downloaded with matching ETag.
    const store = new MemoryBlobStore();
    const seed = await store.openAppend("evidence.mobile.sqlite", 0);
    await seed.write(payload.slice(0, 5));
    await seed.close();
    const meta = new MemoryMetaStore();
    meta.set("evidence.mobile.sqlite", {
      etag: '"etag-resume"',
      repoCommit: "commit-x",
      contentLength: payload.byteLength,
      updatedAt: "earlier",
    });

    let rangeRequest: string | null = null;
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "HEAD") {
        return new Response(null, { status: 200, headers: baseHeaders });
      }
      const range = (init?.headers as Record<string, string> | undefined)?.["Range"];
      rangeRequest = range ?? null;
      return streamResponse(payload.slice(5), baseHeaders, 206);
    });
    globalThis.fetch = fetchMock as typeof fetch;

    const result = await downloadArtifact({
      filename: "evidence.mobile.sqlite",
      url: ARTIFACT_URLS.evidence,
      store,
      meta,
    });

    expect(rangeRequest).toBe("bytes=5-");
    expect(result.resumed).toBe(true);
    expect(result.receivedBytes).toBe(payload.byteLength);
    expect(Array.from(await store.read("evidence.mobile.sqlite"))).toEqual(Array.from(payload));
  });

  it("discards partial bytes and refetches when ETag changed upstream", async () => {
    const newPayload = new Uint8Array([99, 98, 97, 96]);
    const baseHeaders = {
      "x-linked-size": String(newPayload.byteLength),
      "x-linked-etag": '"etag-new"',
      "x-repo-commit": "commit-new",
      "accept-ranges": "bytes",
    };

    const store = new MemoryBlobStore();
    const seed = await store.openAppend("normalization.sqlite", 0);
    await seed.write(new Uint8Array([1, 2])); // stale partial
    await seed.close();
    const meta = new MemoryMetaStore();
    meta.set("normalization.sqlite", {
      etag: '"etag-old"',
      repoCommit: "commit-old",
      contentLength: 5,
      updatedAt: "earlier",
    });

    const seenRanges: (string | undefined)[] = [];
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "HEAD") {
        return new Response(null, { status: 200, headers: baseHeaders });
      }
      const range = (init?.headers as Record<string, string> | undefined)?.["Range"];
      seenRanges.push(range);
      return streamResponse(newPayload, baseHeaders);
    });
    globalThis.fetch = fetchMock as typeof fetch;

    const result = await downloadArtifact({
      filename: "normalization.sqlite",
      url: ARTIFACT_URLS.normalization,
      store,
      meta,
    });

    expect(seenRanges).toEqual([undefined]);
    expect(result.resumed).toBe(false);
    expect(Array.from(await store.read("normalization.sqlite"))).toEqual(Array.from(newPayload));
    expect(meta.get("normalization.sqlite")?.etag).toBe('"etag-new"');
  });
});
