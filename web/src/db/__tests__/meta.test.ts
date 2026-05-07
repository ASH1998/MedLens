import { describe, expect, it } from "vitest";
import { MemoryMetaStore } from "../meta";

describe("MemoryMetaStore", () => {
  it("round-trips per-filename meta", () => {
    const m = new MemoryMetaStore();
    expect(m.get("a")).toBeUndefined();
    m.set("a", { etag: "abc", repoCommit: "def", contentLength: 7, updatedAt: "t" });
    expect(m.get("a")).toEqual({ etag: "abc", repoCommit: "def", contentLength: 7, updatedAt: "t" });
    m.clear("a");
    expect(m.get("a")).toBeUndefined();
  });

  it("isolates entries by filename", () => {
    const m = new MemoryMetaStore();
    m.set("a", { etag: "1", repoCommit: undefined, contentLength: undefined, updatedAt: "t" });
    m.set("b", { etag: "2", repoCommit: undefined, contentLength: undefined, updatedAt: "t" });
    expect(m.get("a")?.etag).toBe("1");
    expect(m.get("b")?.etag).toBe("2");
  });
});
