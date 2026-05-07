# MedLens Web (PWA)

ChatGPT-style offline-first PWA that runs the deterministic MedLens safety pipeline in the browser via `sql.js` against SQLite artifacts persisted to OPFS. See `docs/pwa_plan.md` for the full plan and `BUILD_PROGRESS.md` for build status.

## Quick start

```bash
cd web
pnpm install
pnpm dev      # open the printed URL
pnpm test     # vitest
pnpm lint
pnpm build
```

## Phase status

- Phase 0 — HF artifact access + storage preflight: **done** (anonymous reads on `ASHu2/medlens` confirmed; CORS, ETag, X-Repo-Commit captured).
- Phase 1 — scaffolding: **in progress** (this commit).
- Phase 2 — SQLite delivery + browser storage: **next**.

## Layout

```
web/
  public/manifest.webmanifest
  scripts/publish-hf.ts           # upload artifacts to ASHu2/medlens (stub)
  src/
    db/        hf-fetch.ts (HEAD-only in Phase 1)
    tools/     # Phase 3
    providers/ # Phase 4
    agent/     # Phase 5
    ui/        App.tsx, index.css
    pwa/       # Phase 7
```

Hardcoded artifact URLs (only these two are ever fetched):

- `https://huggingface.co/datasets/ASHu2/medlens/resolve/main/normalization.sqlite`
- `https://huggingface.co/datasets/ASHu2/medlens/resolve/main/evidence.mobile.sqlite`
