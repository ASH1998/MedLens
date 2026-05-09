import { useState } from "react";
import { ARTIFACTS } from "../db/hf-fetch";
import { getDefaultMetaStore } from "../db/meta";
import type { BlobStore } from "../db/opfs";
import { checkAllForUpdates } from "../db/version";
import { getApiKey, maskKey, setApiKey } from "../providers/keystore";

const META = getDefaultMetaStore();

export type ProviderChoice = "template" | "gemini" | "anthropic";

export function Settings({
  open,
  provider,
  store,
  onClose,
  onProviderChange,
  onDataDeleted,
}: {
  open: boolean;
  provider: ProviderChoice;
  store: BlobStore | null;
  onClose: () => void;
  onProviderChange: (provider: ProviderChoice) => void;
  onDataDeleted: () => void;
}) {
  const [gemini, setGemini] = useState("");
  const [anthropic, setAnthropic] = useState("");
  const [status, setStatus] = useState("");

  if (!open) return null;

  async function checkUpdates() {
    setStatus("Checking data version...");
    try {
      const rows = await checkAllForUpdates(META);
      setStatus(rows.map((row) => `${row.filename}: ${row.reason}`).join("\n"));
    } catch (err) {
      setStatus((err as Error).message);
    }
  }

  async function deleteData() {
    if (!store) return;
    for (const artifact of ARTIFACTS) {
      await store.delete(artifact.filename);
      META.clear(artifact.filename);
    }
    onDataDeleted();
  }

  return (
    <div className="settings-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="settings-panel" role="dialog" aria-modal="true" onMouseDown={(e) => e.stopPropagation()}>
        <header>
          <h2>Settings</h2>
          <button type="button" onClick={onClose} aria-label="Close settings">
            ×
          </button>
        </header>

        <label>
          Provider
          <select value={provider} onChange={(event) => onProviderChange(event.target.value as ProviderChoice)}>
            <option value="template">Template offline</option>
            <option value="gemini">Gemini</option>
            <option value="anthropic">Anthropic</option>
          </select>
        </label>

        <div className="key-grid">
          <label>
            Gemini key
            <input value={gemini} onChange={(e) => setGemini(e.target.value)} placeholder={maskKey(getApiKey("gemini"))} />
          </label>
          <button type="button" onClick={() => { setApiKey("gemini", gemini.trim() || null); setGemini(""); }}>
            Save
          </button>
          <label>
            Anthropic key
            <input
              value={anthropic}
              onChange={(e) => setAnthropic(e.target.value)}
              placeholder={maskKey(getApiKey("anthropic"))}
            />
          </label>
          <button type="button" onClick={() => { setApiKey("anthropic", anthropic.trim() || null); setAnthropic(""); }}>
            Save
          </button>
        </div>

        <div className="settings-actions">
          <button type="button" onClick={checkUpdates}>Check For Updates</button>
          <button type="button" onClick={deleteData}>Delete Local Data</button>
        </div>
        {status && <pre className="settings-status">{status}</pre>}
      </section>
    </div>
  );
}
