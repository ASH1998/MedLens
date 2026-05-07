import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ARTIFACTS, downloadArtifact } from "../db/hf-fetch";
import { hasOpfs, pickBlobStore, requestPersistence } from "../db/opfs";
import type { BlobStore } from "../db/opfs";
import { getDefaultMetaStore } from "../db/meta";
import { openDbHandles } from "../db/stores";
import type { DbHandles } from "../db/stores";
import { countRows } from "../db/sqlite";

type Stage =
  | { kind: "init" }
  | { kind: "no-opfs" }
  | { kind: "checking" }
  | { kind: "downloading"; progress: Record<string, ProgressRow> }
  | { kind: "ready"; counts: Record<string, number | string> }
  | { kind: "error"; message: string };

interface ProgressRow {
  filename: string;
  receivedBytes: number;
  totalBytes: number | undefined;
  resumed: boolean;
  done: boolean;
}

const META = getDefaultMetaStore();

export function App() {
  const [stage, setStage] = useState<Stage>({ kind: "init" });
  const storeRef = useRef<BlobStore | null>(null);
  const handlesRef = useRef<DbHandles | null>(null);

  const initProgress = useMemo<Record<string, ProgressRow>>(
    () =>
      Object.fromEntries(
        ARTIFACTS.map((a) => [
          a.filename,
          { filename: a.filename, receivedBytes: 0, totalBytes: undefined, resumed: false, done: false },
        ]),
      ),
    [],
  );

  const openAndCount = useCallback(async (store: BlobStore) => {
    const handles = await openDbHandles(store);
    handlesRef.current = handles;
    const counts: Record<string, number | string> = {};
    try {
      counts["normalization.drug"] = countRows(handles.normalization, "drug");
      counts["normalization.drug_alias"] = countRows(handles.normalization, "drug_alias");
      const evidence = await handles.evidence();
      counts["evidence.known_interaction"] = countRows(evidence, "known_interaction");
      counts["evidence.ddi_raw_signal"] = countRows(evidence, "ddi_raw_signal");
    } catch (err) {
      counts["error"] = (err as Error).message;
    }
    setStage({ kind: "ready", counts });
  }, []);

  const run = useCallback(async () => {
    if (!hasOpfs()) {
      setStage({ kind: "no-opfs" });
      return;
    }
    setStage({ kind: "checking" });
    try {
      await requestPersistence();
      const store = await pickBlobStore();
      storeRef.current = store;

      // Decide if any artifact still needs downloading.
      const sizes = await Promise.all(ARTIFACTS.map((a) => store.size(a.filename)));
      const needsDownload = ARTIFACTS.some((a, i) => {
        const m = META.get(a.filename);
        if (!m?.contentLength) return true;
        return sizes[i] !== m.contentLength;
      });

      if (!needsDownload) {
        await openAndCount(store);
        return;
      }

      const progress = { ...initProgress };
      setStage({ kind: "downloading", progress });

      for (const artifact of ARTIFACTS) {
        await downloadArtifact({
          filename: artifact.filename,
          url: artifact.url,
          store,
          meta: META,
          onProgress: (p) => {
            progress[artifact.filename] = {
              filename: artifact.filename,
              receivedBytes: p.receivedBytes,
              totalBytes: p.totalBytes,
              resumed: p.resumed,
              done: false,
            };
            setStage({ kind: "downloading", progress: { ...progress } });
          },
        });
        progress[artifact.filename] = { ...progress[artifact.filename], done: true };
        setStage({ kind: "downloading", progress: { ...progress } });
      }

      await openAndCount(store);
    } catch (err) {
      setStage({ kind: "error", message: (err as Error).message });
    }
  }, [initProgress, openAndCount]);

  useEffect(() => {
    run();
    return () => {
      handlesRef.current?.close();
    };
  }, [run]);

  return (
    <main style={{ padding: "2rem", maxWidth: 880, margin: "0 auto" }}>
      <h1 style={{ marginBottom: "0.25rem" }}>MedLens</h1>
      <p style={{ opacity: 0.75, marginTop: 0 }}>
        Offline-first medication safety. Phase 2 first-run shell.
      </p>

      {stage.kind === "init" && <p>Initializing…</p>}
      {stage.kind === "checking" && <p>Checking local artifacts…</p>}

      {stage.kind === "no-opfs" && (
        <Notice tone="warn">
          This browser does not expose OPFS. The IndexedDB blob fallback ships in a later phase.
        </Notice>
      )}

      {stage.kind === "error" && <Notice tone="error">{stage.message}</Notice>}

      {stage.kind === "downloading" && (
        <section>
          <h2>Downloading safety data</h2>
          <p style={{ opacity: 0.7 }}>
            Both files are fetched directly from the public dataset
            <code> ASHu2/medlens</code> and persisted to OPFS. Subsequent launches skip this.
          </p>
          {Object.values(stage.progress).map((row) => (
            <ProgressBar key={row.filename} row={row} />
          ))}
        </section>
      )}

      {stage.kind === "ready" && (
        <section>
          <h2>Ready</h2>
          <p style={{ opacity: 0.7 }}>
            Both SQLite databases opened from OPFS via sql.js. The Phase 2 acceptance slice runs
            <code> SELECT COUNT(*) </code> against the canonical tables below.
          </p>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th align="left">Source</th>
                <th align="right">Rows</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(stage.counts).map(([k, v]) => (
                <tr key={k} style={{ borderTop: "1px solid #2a2f45" }}>
                  <td>
                    <code>{k}</code>
                  </td>
                  <td align="right">{typeof v === "number" ? v.toLocaleString() : v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </main>
  );
}

function ProgressBar({ row }: { row: ProgressRow }) {
  const pct =
    row.totalBytes && row.totalBytes > 0
      ? Math.min(100, Math.round((row.receivedBytes / row.totalBytes) * 100))
      : undefined;
  return (
    <div style={{ marginBottom: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.25rem" }}>
        <strong>{row.filename}</strong>
        <span style={{ opacity: 0.75 }}>
          {formatBytes(row.receivedBytes)}
          {row.totalBytes ? ` / ${formatBytes(row.totalBytes)}` : ""}
          {row.resumed ? " · resumed" : ""}
          {row.done ? " · done" : ""}
        </span>
      </div>
      <div style={{ background: "#1a1f33", height: 8, borderRadius: 4, overflow: "hidden" }}>
        <div
          style={{
            width: pct !== undefined ? `${pct}%` : "0%",
            height: "100%",
            background: "#6c8cff",
            transition: "width 120ms linear",
          }}
        />
      </div>
    </div>
  );
}

function Notice({ tone, children }: { tone: "warn" | "error"; children: React.ReactNode }) {
  const palette = tone === "error" ? "#ff8a8a" : "#ffd166";
  return (
    <div
      style={{
        border: `1px solid ${palette}`,
        background: "rgba(255,255,255,0.04)",
        padding: "1rem",
        borderRadius: 6,
        margin: "1rem 0",
        color: palette,
      }}
    >
      {children}
    </div>
  );
}

function formatBytes(n: number | undefined): string {
  if (n === undefined || Number.isNaN(n)) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
