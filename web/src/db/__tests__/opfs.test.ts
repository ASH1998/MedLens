import { describe, expect, it } from "vitest";
import { MemoryBlobStore } from "../opfs";

describe("MemoryBlobStore", () => {
  it("writes from offset 0 and reads back", async () => {
    const store = new MemoryBlobStore();
    const sink = await store.openAppend("a", 0);
    await sink.write(new Uint8Array([1, 2, 3]));
    await sink.close();
    expect(await store.size("a")).toBe(3);
    expect(Array.from(await store.read("a"))).toEqual([1, 2, 3]);
  });

  it("appends from a partial offset matching existing size", async () => {
    const store = new MemoryBlobStore();
    let sink = await store.openAppend("a", 0);
    await sink.write(new Uint8Array([10, 20]));
    await sink.close();

    sink = await store.openAppend("a", 2);
    await sink.write(new Uint8Array([30, 40]));
    await sink.close();

    expect(Array.from(await store.read("a"))).toEqual([10, 20, 30, 40]);
  });

  it("rejects appendOffset != existing size", async () => {
    const store = new MemoryBlobStore();
    const sink = await store.openAppend("a", 0);
    await sink.write(new Uint8Array([1, 2]));
    await sink.close();
    await expect(store.openAppend("a", 5)).rejects.toThrow(/offset/);
  });

  it("delete removes the file", async () => {
    const store = new MemoryBlobStore();
    const sink = await store.openAppend("x", 0);
    await sink.write(new Uint8Array([9]));
    await sink.close();
    expect(await store.exists("x")).toBe(true);
    await store.delete("x");
    expect(await store.exists("x")).toBe(false);
    expect(await store.size("x")).toBe(0);
  });
});
