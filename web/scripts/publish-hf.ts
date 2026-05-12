// Publish MedLens SQLite artifacts to the public Hugging Face dataset
// `ASHu2/medlens`. Phase 1 stub — actual upload via @huggingface/hub is wired
// in alongside Phase 2 when the artifact lifecycle is needed end-to-end.
//
// Usage (after Phase 2 wires this up):
//   pnpm publish:hf
//
// Required env (only for publishing — read access is anonymous):
//   HF_TOKEN  Hugging Face write token with access to dataset ASHu2/medlens.

import { stat } from "node:fs/promises";
import { resolve } from "node:path";

const REPO_ROOT = resolve(import.meta.dirname, "..", "..");
const ARTIFACTS = [
  "data/artifacts/normalization.sqlite",
  "data/artifacts/evidence.mobile.sqlite",
] as const;

async function main() {
  console.log("MedLens publish-hf stub");
  console.log(`repo root: ${REPO_ROOT}`);
  for (const rel of ARTIFACTS) {
    const abs = resolve(REPO_ROOT, rel);
    try {
      const s = await stat(abs);
      console.log(`  ${rel}: ${(s.size / (1024 * 1024)).toFixed(1)} MB`);
    } catch {
      console.log(`  ${rel}: MISSING (build it first)`);
    }
  }
  if (!process.env.HF_TOKEN) {
    console.log("HF_TOKEN not set; skipping upload (stub).");
    return;
  }
  console.log("Upload path lands in Phase 2 — see docs/pwa_plan.md.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
