# MedLens PWA — How to Run

Offline-first PWA that runs the deterministic MedLens safety pipeline in the browser. The full plan lives in `docs/pwa_plan.md`; per-phase build status lives in `BUILD_PROGRESS.md`. This file is the operator's quick reference.

Currently shipped: **Phase 0** (HF artifact preflight) and **Phase 2** (streamed download → OPFS → sql.js, with first-run UI). Phase 3+ (tools / providers / agent / chat UI / service worker / TWA) are still pending.

---

## Prerequisites

- Node.js ≥ 22 (verified on `v22.16.0`)
- pnpm ≥ 10 (verified on `10.7.0`) — `npm i -g pnpm` if missing
- A modern Chromium-based browser for OPFS (Chrome / Edge / Brave). Safari 17+ also works.
- ~80 MB of free disk space inside the browser's storage quota for the two SQLite artifacts.

The PWA does **not** need any HF token. The dataset `ASHu2/medlens` is public; reads are anonymous.

---

## Install

```bash
cd web
pnpm install
```

This installs Vite, React, sql.js, ESLint, Prettier, and Vitest. ~310 packages, completes in under a minute on a warm cache.

---

## Run the dev server

```bash
cd web
pnpm dev
```

Open the printed URL (default `http://localhost:5173`). On first launch you should see:

1. "Initializing…" → "Checking local artifacts…"
2. The browser prompts for **persistent storage** (allow it — without persistence the OS may evict the 73 MB evidence DB under pressure).
3. Two progress bars stream `normalization.sqlite` (~7 MB) and `evidence.mobile.sqlite` (~73 MB) directly from `https://huggingface.co/datasets/ASHu2/medlens/resolve/main/...` into the Origin Private File System.
4. Both SQLite databases open via sql.js. The page renders a row-count table:
   - `normalization.drug` ≈ 981
   - `normalization.drug_alias` ≈ 1,669
   - `evidence.known_interaction` ≈ 21,810
   - `evidence.ddi_raw_signal` ≈ 162,600

Subsequent reloads skip the download — sizes are checked against persisted metadata in `localStorage` and the existing OPFS files are reused.

### Confirming the offline guarantee

This is the Phase 2 acceptance slice from the plan: after the first download completes, the app must work with no network.

1. Wait until the row-count table renders.
2. Chrome DevTools → **Network** tab → set throttling to **Offline**.
3. Hit reload.
4. The row counts should still render. No HTTP requests fire because both DBs come straight from OPFS and sql.js's WASM is precached as a Vite asset.

If the page errors instead of rendering, check DevTools → Application → Storage → IndexedDB / OPFS to confirm the files are there.

---

## Lint, test, build

```bash
cd web
pnpm lint           # eslint flat config
pnpm test           # vitest run — 16 tests across 4 suites at the time of writing
pnpm test:watch     # vitest watch mode
pnpm build          # tsc -b && vite build  →  web/dist/
pnpm preview        # serve web/dist/ locally to confirm the production bundle
```

`pnpm build` emits `web/dist/` with the sql.js WASM as a hashed asset (`sql-wasm-*.wasm`, ~323 KB gzip) plus the React app (~79 KB gzip).

`pnpm format` runs Prettier across the project.

---

## Re-verify Phase 0 (HF anonymous access)

A one-liner to confirm the dataset is still readable without a token. If either URL returns 401, the PWA's first-run download will fail until access is restored.

```bash
curl -sI -H "Origin: https://medlens.example.com" \
  -L https://huggingface.co/datasets/ASHu2/medlens/resolve/main/normalization.sqlite \
  | grep -iE "^HTTP|access-control-allow-origin|x-repo-commit|x-linked-etag|x-linked-size|accept-ranges"
```

Expected: `HTTP/2 200`, the `Origin` mirrored back in `access-control-allow-origin`, plus `accept-ranges: bytes`, `x-repo-commit`, `x-linked-etag`, `x-linked-size`. Repeat for `evidence.mobile.sqlite`.

---

## Forcing a re-download

The download path keys off persisted metadata in browser `localStorage` and OPFS file size. Two ways to start clean:

1. **DevTools** → Application → Storage → "Clear site data". Removes `localStorage`, OPFS, and Cache Storage in one click.
2. From the JS console:
   ```js
   localStorage.removeItem("medlens.artifact.normalization.sqlite");
   localStorage.removeItem("medlens.artifact.evidence.mobile.sqlite");
   const dir = await navigator.storage.getDirectory();
   await dir.removeEntry("normalization.sqlite").catch(() => {});
   await dir.removeEntry("evidence.mobile.sqlite").catch(() => {});
   location.reload();
   ```

Once Phase 6's Settings panel ships, this becomes a "Re-download data" button.

---

## Updating the artifacts

The PWA does not auto-update. When new artifacts are pushed to `ASHu2/medlens`, two anchors change:

- `x-linked-etag` — LFS object SHA256 of the file (content/integrity).
- `x-repo-commit` — the dataset commit (dataset-change anchor).

`web/src/db/version.ts` exports `checkAllForUpdates(meta)` which HEADs both URLs and reports `fresh | etag-changed | repo-commit-changed | no-local-copy` per artifact. The Settings UI that surfaces this prompt lands in Phase 6; until then you can call it from the console:

```js
import("/src/db/version.ts").then(async (m) => {
  const meta = (await import("/src/db/meta.ts")).getDefaultMetaStore();
  console.table(await m.checkAllForUpdates(meta));
});
```

---

## Republishing artifacts to Hugging Face (maintainer-only)

Phase 1 ships a stub uploader at `web/scripts/publish-hf.ts` that previews what would be pushed. The real `@huggingface/hub` upload wires up alongside the artifact-update UX — until then, push manually or use the existing Python build → `huggingface-cli upload`.

Required env when the real path lands:

```bash
export HF_TOKEN=hf_xxx          # write token with access to dataset ASHu2/medlens
cd web
pnpm publish:hf
```

`HF_TOKEN` is **never** read by the client — only by this dev script.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "OPFS not available in this browser." banner | Firefox without OPFS, or a private window in some browsers. | Use Chrome/Edge/Safari 17+. IndexedDB blob fallback ships in a later phase. |
| Download starts then stalls at the same byte count | Network tab shows the GET pending — likely the HF CDN dropping a long-lived stream. | Reload — the next attempt will resume via `Range: bytes=<offset>-` against the same partial file. |
| Row counts render `error` instead of numbers | The SQLite file in OPFS is corrupt (interrupted prior install). | Force a re-download (see above). |
| 401 on the HF preflight curl | Dataset visibility was changed. | Restore public read on `ASHu2/medlens` — the PWA will not authenticate. |
| `pnpm build` complains about `sql-wasm.wasm` | Out-of-date `sql.js` or stale Vite cache. | `rm -rf web/node_modules web/dist && pnpm install && pnpm build`. |

---

## File layout

```
web/
  package.json              Vite 6 + React 19 + TS 5 + Vitest 2 + ESLint 9 + Prettier 3
  index.html                PWA shell entry
  public/manifest.webmanifest
  scripts/publish-hf.ts     stub uploader for ASHu2/medlens
  src/
    main.tsx                React root
    ui/
      App.tsx               Phase 2 first-run shell (download + open + count)
      index.css
    db/
      hf-fetch.ts           pinned URLs + headArtifact + downloadArtifact (Range resume)
      opfs.ts               BlobStore + OpfsBlobStore + MemoryBlobStore
      meta.ts               LocalStorageMetaStore + MemoryMetaStore
      sqlite.ts             sql.js loader + openDatabase
      stores.ts             eager normalization, lazy evidence
      version.ts            HEAD-based freshness check
      types.ts              row interfaces mirroring Python dataclasses
      __tests__/            opfs / meta / hf-fetch / version (16 cases)
```

The two artifact URLs (the only HF resources ever fetched) are pinned as constants in `web/src/db/hf-fetch.ts`:

- `https://huggingface.co/datasets/ASHu2/medlens/resolve/main/normalization.sqlite`
- `https://huggingface.co/datasets/ASHu2/medlens/resolve/main/evidence.mobile.sqlite`

---

## What's next

- **Phase 3** — TS port of `MedicationSafetyStore` (SQL + severity ranking + structured report) plus `TOOL_SCHEMAS` / `dispatch` from `medlens/tools/registry.py`. Verbatim port; golden-file Vitest fixtures generated from the existing Python tests guarantee parity.
- **Phase 4–5** — Provider adapters (template + Gemini + Anthropic) and the agent loop port.
- **Phase 6** — ChatGPT-style chat UI (`FirstRunSetup`, `Sidebar`, `MessageList`, `ToolTrace`, `Settings`).
- **Phase 7** — `vite-plugin-pwa` injectManifest service worker.
- **Phase 8** — Bubblewrap → Trusted Web Activity → Play Store.

See `docs/pwa_plan.md` for the full plan and `BUILD_PROGRESS.md` for what's actually shipped.
