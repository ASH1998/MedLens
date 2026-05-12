// API key storage. v1 uses plain localStorage. WebCrypto AES-GCM passphrase
// wrap is deferred to v2 per the plan's Phase 4 "Open" notes.

const KEY_GEMINI = "medlens.key.gemini";
const KEY_ANTHROPIC = "medlens.key.anthropic";

export type ProviderKeyName = "gemini" | "anthropic";

export function getApiKey(provider: ProviderKeyName): string | null {
  if (typeof localStorage === "undefined") return null;
  return localStorage.getItem(provider === "gemini" ? KEY_GEMINI : KEY_ANTHROPIC);
}

export function setApiKey(provider: ProviderKeyName, value: string | null): void {
  if (typeof localStorage === "undefined") return;
  const key = provider === "gemini" ? KEY_GEMINI : KEY_ANTHROPIC;
  if (!value) {
    localStorage.removeItem(key);
    return;
  }
  localStorage.setItem(key, value);
}

export function maskKey(value: string | null | undefined): string {
  if (!value) return "(not set)";
  if (value.length <= 8) return "•".repeat(value.length);
  return value.slice(0, 4) + "•".repeat(value.length - 8) + value.slice(-4);
}
