import { describe, expect, it, vi, afterEach } from "vitest";
import { ARTIFACT_URLS } from "../hf-fetch";
import { checkForUpdate } from "../version";
import { MemoryMetaStore } from "../meta";

describe("checkForUpdate", () => {
  const realFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  function mockHead(headers: Record<string, string>) {
    globalThis.fetch = vi.fn(
      async () => new Response(null, { status: 200, headers }),
    ) as typeof fetch;
  }

  it("reports no-local-copy when meta is empty", async () => {
    mockHead({ "x-linked-etag": "a", "x-repo-commit": "c", "x-linked-size": "1" });
    const r = await checkForUpdate("normalization.sqlite", ARTIFACT_URLS.normalization, new MemoryMetaStore());
    expect(r.hasUpdate).toBe(true);
    expect(r.reason).toBe("no-local-copy");
  });

  it("reports etag-changed when remote etag differs", async () => {
    mockHead({ "x-linked-etag": "new", "x-repo-commit": "c", "x-linked-size": "1" });
    const meta = new MemoryMetaStore();
    meta.set("normalization.sqlite", {
      etag: "old",
      repoCommit: "c",
      contentLength: 1,
      updatedAt: "t",
    });
    const r = await checkForUpdate("normalization.sqlite", ARTIFACT_URLS.normalization, meta);
    expect(r.hasUpdate).toBe(true);
    expect(r.reason).toBe("etag-changed");
  });

  it("reports repo-commit-changed when only commit differs", async () => {
    mockHead({ "x-linked-etag": "same", "x-repo-commit": "new", "x-linked-size": "1" });
    const meta = new MemoryMetaStore();
    meta.set("normalization.sqlite", {
      etag: "same",
      repoCommit: "old",
      contentLength: 1,
      updatedAt: "t",
    });
    const r = await checkForUpdate("normalization.sqlite", ARTIFACT_URLS.normalization, meta);
    expect(r.hasUpdate).toBe(true);
    expect(r.reason).toBe("repo-commit-changed");
  });

  it("reports fresh when both anchors match", async () => {
    mockHead({ "x-linked-etag": "a", "x-repo-commit": "c", "x-linked-size": "1" });
    const meta = new MemoryMetaStore();
    meta.set("normalization.sqlite", {
      etag: "a",
      repoCommit: "c",
      contentLength: 1,
      updatedAt: "t",
    });
    const r = await checkForUpdate("normalization.sqlite", ARTIFACT_URLS.normalization, meta);
    expect(r.hasUpdate).toBe(false);
    expect(r.reason).toBe("fresh");
  });
});
