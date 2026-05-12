import { useCallback, useEffect, useRef, useState } from "react";
import { ARTIFACTS, downloadArtifact } from "../db/hf-fetch";
import { getDefaultMetaStore } from "../db/meta";
import { hasOpfs, pickBlobStore, requestPersistence } from "../db/opfs";
import type { BlobStore } from "../db/opfs";
import { openDbHandles } from "../db/stores";
import type { DbHandles } from "../db/stores";

interface ProgressRow {
  filename: string;
  receivedBytes: number;
  totalBytes: number | undefined;
  resumed: boolean;
  done: boolean;
  startedAt: number;
}

type SetupStage =
  | { kind: "checking" }
  | { kind: "blocked"; message: string }
  | { kind: "downloading"; progress: Record<string, ProgressRow> }
  | { kind: "opening" }
  | { kind: "ready" }
  | { kind: "error"; message: string };

const META = getDefaultMetaStore();

export function FirstRunSetup({ onReady }: { onReady: (ready: { store: BlobStore; handles: DbHandles }) => void }) {
  const [stage, setStage] = useState<SetupStage>({ kind: "checking" });
  const onReadyRef = useRef(onReady);

  useEffect(() => {
    onReadyRef.current = onReady;
  }, [onReady]);

  const run = useCallback(() => {
    const abort = new AbortController();
    if (!hasOpfs()) {
      setStage({ kind: "blocked", message: "This browser does not expose OPFS storage." });
      return () => abort.abort();
    }
    setStage({ kind: "checking" });

    void (async () => {
      try {
        await requestPersistence();
        const store = await pickBlobStore();
        const sizes = await Promise.all(ARTIFACTS.map((a) => store.size(a.filename)));
        const needsDownload = ARTIFACTS.some((artifact, index) => {
          const meta = META.get(artifact.filename);
          return !meta?.contentLength || sizes[index] !== meta.contentLength;
        });

        if (needsDownload) {
          const progress = createInitialProgress();
          setStage({ kind: "downloading", progress });
          for (const artifact of ARTIFACTS) {
            if (abort.signal.aborted) return;
            const result = await downloadArtifact({
              filename: artifact.filename,
              url: artifact.url,
              store,
              meta: META,
              signal: abort.signal,
              onProgress: (p) => {
                if (abort.signal.aborted) return;
                progress[artifact.filename] = {
                  ...progress[artifact.filename],
                  receivedBytes: p.receivedBytes,
                  totalBytes: p.totalBytes,
                  resumed: p.resumed,
                };
                setStage({ kind: "downloading", progress: { ...progress } });
              },
            });
            progress[artifact.filename] = {
              ...progress[artifact.filename],
              receivedBytes: result.receivedBytes,
              totalBytes: result.totalBytes,
              resumed: result.resumed,
              done: true,
            };
            setStage({ kind: "downloading", progress: { ...progress } });
          }
        }

        if (abort.signal.aborted) return;
        setStage({ kind: "opening" });
        const handles = await openDbHandles(store);
        if (abort.signal.aborted) {
          handles.close();
          return;
        }
        setStage({ kind: "ready" });
        onReadyRef.current({ store, handles });
      } catch (err) {
        if (abort.signal.aborted || (err as Error).name === "AbortError") return;
        setStage({ kind: "error", message: (err as Error).message });
      }
    })();

    return () => abort.abort();
  }, []);

  useEffect(() => {
    return run();
  }, [run]);

  return (
    <main className="setup">
      <section className="setup-panel">
        <div>
          <p className="eyebrow">MedLens</p>
          <h1>Download Safety Data</h1>
          <p className="muted">
            The app stores the local medication index and interaction evidence on this device.
          </p>
        </div>

        {stage.kind === "checking" && <p>Checking local data...</p>}
        {stage.kind === "opening" && <p>Opening local SQLite data...</p>}
        {stage.kind === "ready" && <p>Ready.</p>}
        {stage.kind === "blocked" && <Notice>{stage.message}</Notice>}
        {stage.kind === "error" && <Notice>{stage.message}</Notice>}

        {stage.kind === "downloading" && (
          <div className="progress-stack">
            {Object.values(stage.progress).map((row) => (
              <ProgressBar key={row.filename} row={row} />
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

function createInitialProgress(): Record<string, ProgressRow> {
  return Object.fromEntries(
    ARTIFACTS.map((a) => [
      a.filename,
      {
        filename: a.filename,
        receivedBytes: 0,
        totalBytes: undefined,
        resumed: false,
        done: false,
        startedAt: performance.now(),
      },
    ]),
  ) as Record<string, ProgressRow>;
}

function ProgressBar({ row }: { row: ProgressRow }) {
  const pct =
    row.totalBytes && row.totalBytes > 0
      ? Math.min(100, Math.round((row.receivedBytes / row.totalBytes) * 100))
      : 0;
  const seconds = Math.max(0.5, (performance.now() - row.startedAt) / 1000);
  const speed = row.receivedBytes / seconds;
  return (
    <div className="progress-row">
      <div className="progress-copy">
        <strong>{row.filename}</strong>
        <span>
          {formatBytes(row.receivedBytes)}
          {row.totalBytes ? ` / ${formatBytes(row.totalBytes)}` : ""} · {formatBytes(speed)}/s
          {row.resumed ? " · resumed" : ""}
          {row.done ? " · done" : ""}
        </span>
      </div>
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Notice({ children }: { children: string }) {
  return <div className="notice">{children}</div>;
}

function formatBytes(n: number | undefined): string {
  if (n === undefined || Number.isNaN(n)) return "-";
  if (n < 1024) return `${Math.round(n)} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
