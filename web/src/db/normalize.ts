// Verbatim port of `medlens.artifacts.build_normalization.normalize_lookup_text`.
// Casefold, replace non-alphanumerics with spaces, collapse whitespace.
// Used for exact alias lookup against `drug_alias.normalized_alias`.

const NON_ALNUM_RE = /[^a-z0-9]+/g;

export function normalizeLookupText(value: string): string {
  const folded = (value ?? "").toLowerCase().trim();
  const stripped = folded.replace(NON_ALNUM_RE, " ");
  return stripped.split(/\s+/).filter(Boolean).join(" ");
}
