// BlobStore — minimal abstraction over a content-addressable byte store.
//
// Browser implementation backs onto the Origin Private File System (OPFS) via
// `navigator.storage.getDirectory()`. Tests inject MemoryBlobStore so they
// don't need a real OPFS environment.
//
// The interface deliberately stays small: append-from-offset writes (for
// resumable HTTP Range downloads), full reads, size queries, and delete.

export interface BlobStore {
  size(name: string): Promise<number>;
  exists(name: string): Promise<boolean>;
  read(name: string): Promise<Uint8Array>;
  /** Open a sink that writes starting at `offset`. Use offset=0 for fresh writes. */
  openAppend(name: string, offset: number): Promise<BlobSink>;
  delete(name: string): Promise<void>;
}

export interface BlobSink {
  write(chunk: Uint8Array): Promise<void>;
  close(): Promise<void>;
  abort(): Promise<void>;
}

/** Detect if we have a usable OPFS in the current environment. */
export function hasOpfs(): boolean {
  return (
    typeof navigator !== "undefined" &&
    typeof navigator.storage !== "undefined" &&
    typeof navigator.storage.getDirectory === "function"
  );
}

/** Request persistent storage so the OS is less likely to evict OPFS. */
export async function requestPersistence(): Promise<boolean> {
  if (typeof navigator === "undefined" || !navigator.storage?.persist) return false;
  try {
    if (await navigator.storage.persisted?.()) return true;
    return await navigator.storage.persist();
  } catch {
    return false;
  }
}

export async function pickBlobStore(): Promise<BlobStore> {
  if (hasOpfs()) {
    const dir = await navigator.storage.getDirectory();
    return new OpfsBlobStore(dir);
  }
  throw new Error(
    "OPFS not available in this browser. IndexedDB blob fallback is planned but not implemented in Phase 2.",
  );
}

class OpfsBlobStore implements BlobStore {
  constructor(private dir: FileSystemDirectoryHandle) {}

  async size(name: string): Promise<number> {
    try {
      const handle = await this.dir.getFileHandle(name);
      const file = await handle.getFile();
      return file.size;
    } catch {
      return 0;
    }
  }

  async exists(name: string): Promise<boolean> {
    try {
      await this.dir.getFileHandle(name);
      return true;
    } catch {
      return false;
    }
  }

  async read(name: string): Promise<Uint8Array> {
    const handle = await this.dir.getFileHandle(name);
    const file = await handle.getFile();
    return new Uint8Array(await file.arrayBuffer());
  }

  async openAppend(name: string, offset: number): Promise<BlobSink> {
    const handle = await this.dir.getFileHandle(name, { create: true });
    const writable = await handle.createWritable({ keepExistingData: offset > 0 });
    if (offset > 0) await writable.seek(offset);
    return new OpfsSink(writable);
  }

  async delete(name: string): Promise<void> {
    try {
      await this.dir.removeEntry(name);
    } catch {
      // ignore missing
    }
  }
}

class OpfsSink implements BlobSink {
  private closed = false;
  constructor(private writable: FileSystemWritableFileStream) {}

  async write(chunk: Uint8Array): Promise<void> {
    if (this.closed) throw new Error("sink closed");
    // sql.js / fetch hand us Uint8Array<ArrayBufferLike>, but
    // FileSystemWritableFileStream's TS lib types insist on
    // Uint8Array<ArrayBuffer>. Re-wrap through a fresh ArrayBuffer.
    const copy = new Uint8Array(chunk.byteLength);
    copy.set(chunk);
    await this.writable.write(copy);
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    await this.writable.close();
  }

  async abort(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    await this.writable.abort();
  }
}

/** In-memory implementation for tests. */
export class MemoryBlobStore implements BlobStore {
  private files = new Map<string, Uint8Array>();

  async size(name: string): Promise<number> {
    return this.files.get(name)?.byteLength ?? 0;
  }

  async exists(name: string): Promise<boolean> {
    return this.files.has(name);
  }

  async read(name: string): Promise<Uint8Array> {
    const v = this.files.get(name);
    if (!v) throw new Error(`MemoryBlobStore: ${name} not found`);
    return v;
  }

  async openAppend(name: string, offset: number): Promise<BlobSink> {
    const existing = this.files.get(name);
    if (offset === 0 || !existing) {
      this.files.set(name, new Uint8Array(0));
    } else if (existing.byteLength !== offset) {
      throw new Error(
        `MemoryBlobStore: openAppend offset ${offset} != existing size ${existing.byteLength}`,
      );
    }
    const files = this.files;
    let closed = false;
    return {
      async write(chunk: Uint8Array) {
        if (closed) throw new Error("sink closed");
        const cur = files.get(name) ?? new Uint8Array(0);
        const next = new Uint8Array(cur.byteLength + chunk.byteLength);
        next.set(cur, 0);
        next.set(chunk, cur.byteLength);
        files.set(name, next);
      },
      async close() {
        closed = true;
      },
      async abort() {
        closed = true;
      },
    };
  }

  async delete(name: string): Promise<void> {
    this.files.delete(name);
  }
}
