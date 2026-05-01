"""Deterministic local medication-safety lookup tools.

These functions are the authority layer the agent should call before making
interaction claims. They intentionally return structured data instead of
natural language so the LLM can explain results without inventing evidence.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from medlens.artifacts.build_normalization import normalize_lookup_text


@dataclass(frozen=True)
class NormalizedMedication:
    input_name: str
    normalized_input: str
    canonical_name: str | None
    drug_id: int | None
    matched_alias: str | None
    resolved: bool


@dataclass(frozen=True)
class InteractionEffect:
    adverse_effect: str
    severity: str
    row_count: int
    source_regions: tuple[str, ...]


@dataclass(frozen=True)
class RawDdiSignal:
    source_file: str
    source_row_number: int
    source_signal_id: str
    region: str
    drug1_raw: str
    drug2_raw: str
    adverse_effect: str
    severity: str
    mechanism_or_rationale: str
    interaction_category: str
    interaction_direction: str
    evidence_basis: str
    source_basis: str
    source_urls: str
    population_relevance: str
    patient_risk_flags: str
    dataset_type: str
    use_case_note: str


@dataclass(frozen=True)
class KnownInteraction:
    found: bool
    drug_a: str
    drug_b: str
    severity: str | None = None
    row_count: int = 0
    source_regions: tuple[str, ...] = ()
    evidence_bases: tuple[str, ...] = ()
    source_bases: tuple[str, ...] = ()
    source_urls: tuple[str, ...] = ()
    mechanisms: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    dataset_types: tuple[str, ...] = ()
    use_case_notes: tuple[str, ...] = ()
    effects: tuple[InteractionEffect, ...] = ()
    raw_signals: tuple[RawDdiSignal, ...] = ()
    evidence_source: str = "ddi_reference"


@dataclass(frozen=True)
class MedicationSafetyReport:
    input_medications: tuple[str, ...]
    normalized_medications: tuple[NormalizedMedication, ...]
    unresolved_medications: tuple[NormalizedMedication, ...]
    checked_pair_count: int
    findings: tuple[KnownInteraction, ...]
    overall_severity: str
    evidence_status: str
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "input_medications": list(self.input_medications),
            "normalized_medications": [_normalized_medication_to_dict(item) for item in self.normalized_medications],
            "unresolved_medications": [_normalized_medication_to_dict(item) for item in self.unresolved_medications],
            "checked_pair_count": self.checked_pair_count,
            "findings": [_known_interaction_to_dict(finding) for finding in self.findings],
            "overall_severity": self.overall_severity,
            "evidence_status": self.evidence_status,
            "limitations": list(self.limitations),
        }


class MedicationSafetyStore:
    """SQLite-backed deterministic tools for normalization and interaction lookup."""

    def __init__(
        self,
        normalization_db: Path | str = Path("data/artifacts/normalization.sqlite"),
        evidence_db: Path | str = Path("data/artifacts/evidence.sqlite"),
    ) -> None:
        self.normalization_db = Path(normalization_db)
        self.evidence_db = Path(evidence_db)

    def normalize_medication_names(self, names: list[str] | tuple[str, ...]) -> list[NormalizedMedication]:
        """Normalize OCR/user medication names to canonical ingredients."""
        if not self.normalization_db.exists():
            raise FileNotFoundError(f"Normalization DB not found: {self.normalization_db}")

        results: list[NormalizedMedication] = []
        with sqlite3.connect(self.normalization_db) as conn:
            for name in names:
                normalized_input = normalize_lookup_text(name)
                row = conn.execute(
                    """
                    SELECT d.id, d.canonical_name, a.alias
                    FROM drug_alias a
                    JOIN drug d ON d.id = a.drug_id
                    WHERE a.normalized_alias = ?
                    """,
                    (normalized_input,),
                ).fetchone()
                if row:
                    drug_id, canonical_name, matched_alias = row
                    results.append(
                        NormalizedMedication(
                            input_name=name,
                            normalized_input=normalized_input,
                            canonical_name=str(canonical_name),
                            drug_id=int(drug_id),
                            matched_alias=str(matched_alias),
                            resolved=True,
                        )
                    )
                else:
                    results.append(
                        NormalizedMedication(
                            input_name=name,
                            normalized_input=normalized_input,
                            canonical_name=None,
                            drug_id=None,
                            matched_alias=None,
                            resolved=False,
                        )
                    )
        return results

    def search_drug_aliases(self, query: str, limit: int = 10) -> list[dict[str, object]]:
        """Search canonical drug names and aliases for typo/OCR recovery."""
        if not self.normalization_db.exists():
            raise FileNotFoundError(f"Normalization DB not found: {self.normalization_db}")

        normalized_query = normalize_lookup_text(query)
        if not normalized_query:
            return []

        pattern = f"%{normalized_query}%"
        grouped: dict[str, list[str]] = {}
        with sqlite3.connect(self.normalization_db) as conn:
            for canonical_name, alias in conn.execute(
                """
                SELECT d.canonical_name, a.alias
                FROM drug_alias a
                JOIN drug d ON d.id = a.drug_id
                WHERE a.normalized_alias LIKE ?
                   OR d.canonical_name LIKE ?
                ORDER BY
                    CASE WHEN a.normalized_alias = ? THEN 0 ELSE 1 END,
                    LENGTH(a.alias),
                    d.canonical_name,
                    a.alias
                LIMIT ?
                """,
                (pattern, pattern, normalized_query, max(1, limit * 4)),
            ):
                aliases = grouped.setdefault(str(canonical_name), [])
                alias_value = str(alias)
                if alias_value not in aliases:
                    aliases.append(alias_value)
                if len(grouped) >= limit and all(len(items) >= 3 for items in grouped.values()):
                    break

        return [
            {"canonical": canonical, "aliases": aliases[:5]}
            for canonical, aliases in list(grouped.items())[:limit]
        ]

    def known_alias_terms(self) -> set[str]:
        """Return normalized known aliases for lightweight terminal extraction."""
        if not self.normalization_db.exists():
            raise FileNotFoundError(f"Normalization DB not found: {self.normalization_db}")

        with sqlite3.connect(self.normalization_db) as conn:
            return {str(row[0]) for row in conn.execute("SELECT normalized_alias FROM drug_alias")}

    def lookup_known_interaction(
        self,
        drug_a: str,
        drug_b: str,
        effect_limit: int = 8,
        raw_signal_limit: int = 20,
    ) -> KnownInteraction:
        """Look up a known/reference DDI-ADE pair before any future FAERS fallback."""
        if not self.evidence_db.exists():
            raise FileNotFoundError(f"Evidence DB not found: {self.evidence_db}")

        normalized = self.normalize_medication_names([drug_a, drug_b])
        left, right = normalized
        pair_a = left.canonical_name or left.normalized_input
        pair_b = right.canonical_name or right.normalized_input
        drug_key_a, drug_key_b = sorted((pair_a, pair_b))

        if not left.resolved or not right.resolved:
            return KnownInteraction(found=False, drug_a=drug_key_a, drug_b=drug_key_b)

        with sqlite3.connect(self.evidence_db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT *
                FROM known_interaction
                WHERE drug_a = ? AND drug_b = ?
                """,
                (drug_key_a, drug_key_b),
            ).fetchone()
            if row is None:
                # Future fallback point: query FAERS pair_evidence when that table exists.
                return KnownInteraction(found=False, drug_a=drug_key_a, drug_b=drug_key_b)

            interaction_id = int(row["id"])
            effects = tuple(
                InteractionEffect(
                    adverse_effect=str(effect["adverse_effect"]),
                    severity=str(effect["severity"]),
                    row_count=int(effect["row_count"]),
                    source_regions=_json_tuple(effect["source_regions_json"]),
                )
                for effect in conn.execute(
                    """
                    SELECT adverse_effect, severity, row_count, source_regions_json
                    FROM known_interaction_effect
                    WHERE known_interaction_id = ?
                    ORDER BY severity_rank DESC, row_count DESC, adverse_effect
                    LIMIT ?
                    """,
                    (interaction_id, effect_limit),
                )
            )
            raw_signals = tuple(
                RawDdiSignal(
                    source_file=str(raw["source_file"]),
                    source_row_number=int(raw["source_row_number"]),
                    source_signal_id=str(raw["source_signal_id"] or ""),
                    region=str(raw["region"]),
                    drug1_raw=str(raw["drug1_raw"]),
                    drug2_raw=str(raw["drug2_raw"]),
                    adverse_effect=str(raw["adverse_effect"] or ""),
                    severity=str(raw["severity"]),
                    mechanism_or_rationale=str(raw["mechanism_or_rationale"] or ""),
                    interaction_category=str(raw["interaction_category"] or ""),
                    interaction_direction=str(raw["interaction_direction"] or ""),
                    evidence_basis=str(raw["evidence_basis"] or ""),
                    source_basis=str(raw["source_basis"] or ""),
                    source_urls=str(raw["source_urls"] or ""),
                    population_relevance=str(raw["population_relevance"] or ""),
                    patient_risk_flags=str(raw["patient_risk_flags"] or ""),
                    dataset_type=str(raw["dataset_type"] or ""),
                    use_case_note=str(raw["use_case_note"] or ""),
                )
                for raw in conn.execute(
                    """
                    SELECT *
                    FROM ddi_raw_signal
                    WHERE known_interaction_id = ?
                    ORDER BY severity_rank DESC, source_file, source_row_number
                    LIMIT ?
                    """,
                    (interaction_id, raw_signal_limit),
                )
            )

        return KnownInteraction(
            found=True,
            drug_a=str(row["drug_a"]),
            drug_b=str(row["drug_b"]),
            severity=str(row["severity"]),
            row_count=int(row["row_count"]),
            source_regions=_json_tuple(row["source_regions_json"]),
            evidence_bases=_json_tuple(row["evidence_bases_json"]),
            source_bases=_json_tuple(row["source_bases_json"]),
            source_urls=_json_tuple(row["source_urls_json"]),
            mechanisms=_json_tuple(row["mechanisms_json"]),
            risk_flags=_json_tuple(row["risk_flags_json"]),
            dataset_types=_json_tuple(row["dataset_types_json"]),
            use_case_notes=_json_tuple(row["use_case_notes_json"]),
            effects=effects,
            raw_signals=raw_signals,
        )

    def build_structured_report(
        self,
        medication_names: list[str] | tuple[str, ...],
        effect_limit: int = 8,
    ) -> MedicationSafetyReport:
        """Build the deterministic safety report for a full medication list."""
        normalized = tuple(self.normalize_medication_names(tuple(medication_names)))
        unresolved = tuple(item for item in normalized if not item.resolved)
        resolved_unique = _dedupe_resolved_medications(normalized)
        checked_pair_count = 0
        findings: list[KnownInteraction] = []

        for left, right in combinations(resolved_unique, 2):
            if left.canonical_name is None or right.canonical_name is None:
                continue
            checked_pair_count += 1
            interaction = self.lookup_known_interaction(left.canonical_name, right.canonical_name, effect_limit=effect_limit)
            if interaction.found:
                findings.append(interaction)

        ranked_findings = tuple(sorted(findings, key=_interaction_sort_key))
        overall_severity = _overall_severity(ranked_findings)
        limitations = _report_limitations(unresolved, checked_pair_count, ranked_findings)

        return MedicationSafetyReport(
            input_medications=tuple(medication_names),
            normalized_medications=normalized,
            unresolved_medications=unresolved,
            checked_pair_count=checked_pair_count,
            findings=ranked_findings,
            overall_severity=overall_severity,
            evidence_status=_evidence_status(ranked_findings, unresolved, checked_pair_count),
            limitations=limitations,
        )


def _json_tuple(value: str) -> tuple[str, ...]:
    parsed = json.loads(value or "[]")
    return tuple(str(item) for item in parsed)


def _normalized_medication_to_dict(item: NormalizedMedication) -> dict[str, object]:
    return {
        "input_name": item.input_name,
        "normalized_input": item.normalized_input,
        "canonical_name": item.canonical_name,
        "drug_id": item.drug_id,
        "matched_alias": item.matched_alias,
        "resolved": item.resolved,
    }


def _interaction_effect_to_dict(effect: InteractionEffect) -> dict[str, object]:
    return {
        "adverse_effect": effect.adverse_effect,
        "severity": effect.severity,
        "row_count": effect.row_count,
        "source_regions": list(effect.source_regions),
    }


def _raw_ddi_signal_to_dict(raw: RawDdiSignal) -> dict[str, object]:
    return {
        "source_file": raw.source_file,
        "source_row_number": raw.source_row_number,
        "source_signal_id": raw.source_signal_id,
        "region": raw.region,
        "drug1_raw": raw.drug1_raw,
        "drug2_raw": raw.drug2_raw,
        "adverse_effect": raw.adverse_effect,
        "severity": raw.severity,
        "mechanism_or_rationale": raw.mechanism_or_rationale,
        "interaction_category": raw.interaction_category,
        "interaction_direction": raw.interaction_direction,
        "evidence_basis": raw.evidence_basis,
        "source_basis": raw.source_basis,
        "source_urls": raw.source_urls,
        "population_relevance": raw.population_relevance,
        "patient_risk_flags": raw.patient_risk_flags,
        "dataset_type": raw.dataset_type,
        "use_case_note": raw.use_case_note,
    }


def _known_interaction_to_dict(interaction: KnownInteraction) -> dict[str, object]:
    return {
        "found": interaction.found,
        "evidence_source": interaction.evidence_source,
        "drug_a": interaction.drug_a,
        "drug_b": interaction.drug_b,
        "severity": interaction.severity,
        "row_count": interaction.row_count,
        "source_regions": list(interaction.source_regions),
        "evidence_bases": list(interaction.evidence_bases),
        "source_bases": list(interaction.source_bases),
        "source_urls": list(interaction.source_urls),
        "mechanisms": list(interaction.mechanisms),
        "risk_flags": list(interaction.risk_flags),
        "dataset_types": list(interaction.dataset_types),
        "use_case_notes": list(interaction.use_case_notes),
        "effects": [_interaction_effect_to_dict(effect) for effect in interaction.effects],
        "raw_signals": [_raw_ddi_signal_to_dict(raw) for raw in interaction.raw_signals],
    }


def _dedupe_resolved_medications(items: tuple[NormalizedMedication, ...]) -> tuple[NormalizedMedication, ...]:
    seen: set[str] = set()
    deduped: list[NormalizedMedication] = []
    for item in items:
        if not item.resolved or item.canonical_name is None:
            continue
        if item.canonical_name in seen:
            continue
        seen.add(item.canonical_name)
        deduped.append(item)
    return tuple(deduped)


def _interaction_sort_key(interaction: KnownInteraction) -> tuple[int, int, str, str]:
    return (
        -_severity_rank(interaction.severity),
        -interaction.row_count,
        interaction.drug_a,
        interaction.drug_b,
    )


def _severity_rank(severity: str | None) -> int:
    return {"Major": 3, "Moderate": 2, "Minor": 1}.get(severity or "", 0)


def _overall_severity(findings: tuple[KnownInteraction, ...]) -> str:
    if not findings:
        return "None"
    return max((finding.severity or "None" for finding in findings), key=_severity_rank)


def _evidence_status(
    findings: tuple[KnownInteraction, ...],
    unresolved: tuple[NormalizedMedication, ...],
    checked_pair_count: int,
) -> str:
    if findings and not unresolved:
        return "verified_reference_findings"
    if findings and unresolved:
        return "verified_reference_findings_with_unresolved_inputs"
    if checked_pair_count > 0 and unresolved:
        return "no_reference_findings_with_unresolved_inputs"
    if checked_pair_count > 0:
        return "no_reference_findings"
    return "insufficient_resolved_medications"


def _report_limitations(
    unresolved: tuple[NormalizedMedication, ...],
    checked_pair_count: int,
    findings: tuple[KnownInteraction, ...],
) -> tuple[str, ...]:
    limitations: list[str] = [
        "This report uses local DDI reference signals only; it is a screening output, not patient-specific medical advice."
    ]
    if unresolved:
        unresolved_names = ", ".join(item.input_name for item in unresolved)
        limitations.append(f"Some medications could not be normalized and were not checked: {unresolved_names}.")
    if checked_pair_count == 0:
        limitations.append("Fewer than two medications were resolved, so no pairwise interaction check was possible.")
    if not findings and checked_pair_count > 0:
        limitations.append("No known/reference DDI signal was found locally for the resolved medication pairs.")
    return tuple(limitations)
