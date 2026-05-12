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
- Phase 1 — scaffolding: **done**.
- Phase 2 — SQLite delivery + browser storage: **done**.
- Phase 3 — deterministic tools port: **done**.
- Phase 4 — provider adapters: **done**.
- Phase 5 — agent loop + slash commands: **done**.
- Phase 6 — chat UI: **done**.
- Phase 7 — service worker: **next**.

## Layout

```
web/
  public/manifest.webmanifest
  scripts/publish-hf.ts           # upload artifacts to ASHu2/medlens (stub)
  src/
    db/        artifact fetch, OPFS store, sql.js open, freshness checks
    tools/     deterministic safety store + tool registry
    providers/ template, Gemini, Anthropic, localStorage key store
    agent/     loop, prompts, slash commands
    ui/        chat shell, first-run setup, settings, transcript, trace
    pwa/       # Phase 7
```

Hardcoded artifact URLs (only these two are ever fetched):

- `https://huggingface.co/datasets/ASHu2/medlens/resolve/main/normalization.sqlite`
- `https://huggingface.co/datasets/ASHu2/medlens/resolve/main/evidence.mobile.sqlite`
