"""Deterministic local medication-safety lookup tools.

These functions are the authority layer the agent should call before making
interaction claims. They intentionally return structured data instead of
natural language so the LLM can explain results without inventing evidence.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from itertools import combinations
from pathlib import Path

from medlens.artifacts.build_normalization import normalize_lookup_text

NSAID_CANONICALS = {
    "aceclofenac",
    "aspirin",
    "celecoxib",
    "diclofenac",
    "etoricoxib",
    "ibuprofen",
    "indomethacin",
    "ketoprofen",
    "ketorolac",
    "meloxicam",
    "naproxen",
    "nimesulide",
    "piroxicam",
}
ANTICOAGULANT_ANTIPLATELET_CANONICALS = {
    "apixaban",
    "aspirin",
    "clopidogrel",
    "dabigatran",
    "edoxaban",
    "prasugrel",
    "rivaroxaban",
    "ticagrelor",
    "warfarin",
}


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
class PracticalGuidance:
    rule_id: str
    practical_risk_tier: str
    practical_summary: str
    dose_context_needed: str
    risk_factor_questions: str
    source_urls: tuple[str, ...]


@dataclass(frozen=True)
class DuplicateIngredientWarning:
    ingredient: str
    input_names: tuple[str, ...]
    practical_risk_tier: str
    practical_summary: str
    dose_context_needed: str
    risk_factor_questions: str
    source_urls: tuple[str, ...]


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
    practical_guidance: PracticalGuidance | None = None


@dataclass(frozen=True)
class InteractionSearchResult:
    filters: dict[str, object]
    drug_normalization: NormalizedMedication | None
    interactions: tuple[KnownInteraction, ...]


@dataclass(frozen=True)
class MedicationSafetyReport:
    input_medications: tuple[str, ...]
    normalized_medications: tuple[NormalizedMedication, ...]
    unresolved_medications: tuple[NormalizedMedication, ...]
    checked_pair_count: int
    findings: tuple[KnownInteraction, ...]
    duplicate_ingredient_warnings: tuple[DuplicateIngredientWarning, ...]
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
            "duplicate_ingredient_warnings": [
                _duplicate_ingredient_warning_to_dict(warning)
                for warning in self.duplicate_ingredient_warnings
            ],
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
                ingredient_rows = []
                for candidate in _ingredient_map_candidates(normalized_input):
                    ingredient_rows = _ingredient_rows_for_candidate(conn, candidate)
                    if not ingredient_rows:
                        fuzzy_candidate = _fuzzy_brand_candidate(conn, candidate)
                        if fuzzy_candidate is not None:
                            ingredient_rows = _ingredient_rows_for_candidate(conn, fuzzy_candidate)
                    if ingredient_rows:
                        break
                if ingredient_rows:
                    for drug_id, canonical_name, matched_alias in ingredient_rows:
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
                    continue
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

    def search_common_medicines(
        self,
        query: str | None = None,
        limit: int = 10,
        *,
        therapeutic_category: str | None = None,
        otc_or_rx: str | None = None,
        nlem_or_jan_aushadhi: str | None = None,
        risk_flag: str | None = None,
    ) -> list[dict[str, object]]:
        """Search India common-medicine metadata, with optional structured filters."""
        if not self.normalization_db.exists():
            raise FileNotFoundError(f"Normalization DB not found: {self.normalization_db}")

        clauses: list[str] = []
        params: list[object] = []
        normalized_query: str | None = None

        if query and query.strip():
            normalized_query = normalize_lookup_text(query)
            if normalized_query:
                pattern = f"%{normalized_query}%"
                text_pattern = f"%{query.casefold().strip()}%"
                clauses.append(
                    "(m.normalized_generic_name LIKE ?"
                    " OR lower(m.common_brand_examples_india) LIKE ?"
                    " OR lower(m.common_daily_life_use_india) LIKE ?"
                    " OR lower(m.therapeutic_category) LIKE ?)"
                )
                params.extend([pattern, text_pattern, text_pattern, text_pattern])

        if therapeutic_category and therapeutic_category.strip():
            clauses.append("lower(m.therapeutic_category) LIKE ?")
            params.append(f"%{therapeutic_category.casefold().strip()}%")

        if otc_or_rx and otc_or_rx.strip():
            clauses.append("lower(m.otc_or_rx) LIKE ?")
            params.append(f"%{otc_or_rx.casefold().strip()}%")

        if nlem_or_jan_aushadhi and nlem_or_jan_aushadhi.strip():
            clauses.append("lower(m.nlem_or_jan_aushadhi_presence) LIKE ?")
            params.append(f"%{nlem_or_jan_aushadhi.casefold().strip()}%")

        if risk_flag and risk_flag.strip():
            clauses.append("lower(m.patient_risk_flags_india) LIKE ?")
            params.append(f"%{risk_flag.casefold().strip()}%")

        if not clauses:
            return []

        where = " AND ".join(clauses)
        order_exact = normalized_query if normalized_query else ""
        params.extend([order_exact, max(1, limit)])

        with sqlite3.connect(self.normalization_db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT m.*, d.canonical_name
                FROM india_common_medicine m
                JOIN drug d ON d.id = m.drug_id
                WHERE {where}
                ORDER BY
                    CASE WHEN m.normalized_generic_name = ? THEN 0 ELSE 1 END,
                    d.canonical_name,
                    m.source_row_number
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [_common_medicine_row_to_dict(row) for row in rows]

    def get_common_medicine_profile(self, name: str, limit: int = 10) -> dict[str, object]:
        """Return India common-medicine metadata for a user/brand/generic name."""
        if not self.normalization_db.exists():
            raise FileNotFoundError(f"Normalization DB not found: {self.normalization_db}")

        normalized = self.normalize_medication_names([name])[0]
        rows: list[sqlite3.Row] = []
        aliases: tuple[str, ...] = ()
        if normalized.resolved and normalized.drug_id is not None:
            with sqlite3.connect(self.normalization_db) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT m.*, d.canonical_name
                    FROM india_common_medicine m
                    JOIN drug d ON d.id = m.drug_id
                    WHERE m.drug_id = ?
                    ORDER BY m.source_row_number
                    LIMIT ?
                    """,
                    (normalized.drug_id, max(1, limit)),
                ).fetchall()
                aliases = tuple(
                    str(row["alias"])
                    for row in conn.execute(
                        """
                        SELECT alias
                        FROM drug_alias
                        WHERE drug_id = ?
                        ORDER BY
                            CASE alias_type WHEN 'canonical' THEN 0 WHEN 'brand' THEN 1 ELSE 2 END,
                            LENGTH(alias),
                            alias
                        LIMIT 20
                        """,
                        (normalized.drug_id,),
                    )
                )

        if not rows:
            matches = self.search_common_medicines(name, limit=limit)
            return {
                "query": name,
                "normalized": _normalized_medication_to_dict(normalized),
                "aliases": list(aliases),
                "matches": matches,
            }

        return {
            "query": name,
            "normalized": _normalized_medication_to_dict(normalized),
            "aliases": list(aliases),
            "matches": [_common_medicine_row_to_dict(row) for row in rows],
        }

    def search_interactions_by_mechanism(
        self,
        query: str,
        *,
        drug: str | None = None,
        region: str | None = None,
        min_severity: str | None = None,
        limit: int = 20,
        sample_per_pair: int = 3,
    ) -> dict[str, object]:
        """Source-text search over ddi_raw_signal mechanism_or_rationale + interaction_category.

        Returns matching pairs plus up to ``sample_per_pair`` excerpts of the matching
        mechanism/category text. Mechanism free-text is curated per source CSV and may be
        noisy or inconsistent across rows; treat as a hint, not a clean ontology.
        """
        if not query or not query.strip():
            raise ValueError("query is required")
        if not self.evidence_db.exists():
            raise FileNotFoundError(f"Evidence DB not found: {self.evidence_db}")

        drug_normalization: NormalizedMedication | None = None
        drug_canonical: str | None = None
        if drug is not None and drug.strip():
            drug_normalization = self.normalize_medication_names([drug])[0]
            if not drug_normalization.resolved or drug_normalization.canonical_name is None:
                return {
                    "query": query,
                    "filters": {"drug": drug, "region": region, "min_severity": min_severity},
                    "drug_normalization": _normalized_medication_to_dict(drug_normalization),
                    "count": 0,
                    "matches": [],
                }
            drug_canonical = drug_normalization.canonical_name

        where_ki, params_ki, _needs_join = _build_interaction_filters(
            drug_canonical=drug_canonical,
            effect=None,
            min_severity=min_severity,
            region=region,
            risk_flag=None,
        )
        mech_pattern = f"%{query.casefold().strip()}%"
        params: list[object] = [mech_pattern, mech_pattern, *params_ki, max(1, int(limit))]

        with sqlite3.connect(self.evidence_db) as conn:
            conn.row_factory = sqlite3.Row
            if not _relation_exists(conn, "ddi_raw_signal"):
                return {
                    "query": query,
                    "filters": {"drug": drug, "region": region, "min_severity": min_severity},
                    "drug_normalization": _normalized_medication_to_dict(drug_normalization)
                    if drug_normalization is not None
                    else None,
                    "count": 0,
                    "matches": [],
                    "note": "Raw signal search is unavailable in compact evidence artifacts.",
                }
            pair_rows = conn.execute(
                f"""
                SELECT DISTINCT ki.id, ki.drug_a, ki.drug_b, ki.severity, ki.severity_rank,
                                ki.row_count, ki.source_regions_json
                FROM known_interaction ki
                JOIN ddi_raw_signal r ON r.known_interaction_id = ki.id
                WHERE (lower(r.mechanism_or_rationale) LIKE ? OR lower(r.interaction_category) LIKE ?)
                  AND ({where_ki})
                ORDER BY ki.severity_rank DESC, ki.row_count DESC, ki.drug_a, ki.drug_b
                LIMIT ?
                """,
                params,
            ).fetchall()

            matches: list[dict[str, object]] = []
            for pair in pair_rows:
                samples = conn.execute(
                    """
                    SELECT mechanism_or_rationale, interaction_category, region
                    FROM ddi_raw_signal
                    WHERE known_interaction_id = ?
                      AND (lower(mechanism_or_rationale) LIKE ? OR lower(interaction_category) LIKE ?)
                    LIMIT ?
                    """,
                    (int(pair["id"]), mech_pattern, mech_pattern, max(1, int(sample_per_pair))),
                ).fetchall()
                seen_mech: set[str] = set()
                seen_cat: set[str] = set()
                mechanisms: list[str] = []
                categories: list[str] = []
                for sample in samples:
                    mech = (sample["mechanism_or_rationale"] or "").strip()
                    cat = (sample["interaction_category"] or "").strip()
                    if mech and mech not in seen_mech:
                        mechanisms.append(mech)
                        seen_mech.add(mech)
                    if cat and cat not in seen_cat:
                        categories.append(cat)
                        seen_cat.add(cat)
                matches.append(
                    {
                        "drug_a": str(pair["drug_a"]),
                        "drug_b": str(pair["drug_b"]),
                        "severity": str(pair["severity"]),
                        "row_count": int(pair["row_count"]),
                        "regions": list(_json_tuple(pair["source_regions_json"])),
                        "matched_mechanisms": mechanisms,
                        "matched_categories": categories,
                    }
                )

        return {
            "query": query,
            "filters": {"drug": drug, "region": region, "min_severity": min_severity},
            "drug_normalization": _normalized_medication_to_dict(drug_normalization) if drug_normalization else None,
            "count": len(matches),
            "matches": matches,
            "note": "Mechanism text is curated per source CSV and may be inconsistent; treat results as a hint.",
        }

    def list_drugs_by_category(
        self,
        category: str | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        """Browse curated drug categories.

        With no `category`, returns the catalog of categories with drug counts. With a
        `category` (substring match, case-insensitive), returns canonical drugs in that
        category ordered alphabetically.
        """
        if not self.normalization_db.exists():
            raise FileNotFoundError(f"Normalization DB not found: {self.normalization_db}")

        with sqlite3.connect(self.normalization_db) as conn:
            conn.row_factory = sqlite3.Row
            if category is None or not category.strip():
                rows = conn.execute(
                    """
                    SELECT category, COUNT(*) AS drug_count
                    FROM drug
                    GROUP BY category
                    ORDER BY drug_count DESC, category
                    """
                ).fetchall()
                return {
                    "category": None,
                    "categories": [
                        {"category": str(row["category"]), "drug_count": int(row["drug_count"])}
                        for row in rows
                    ],
                    "drugs": [],
                    "count": 0,
                }

            pattern = f"%{category.casefold().strip()}%"
            rows = conn.execute(
                """
                SELECT canonical_name, category, region_scope, is_common
                FROM drug
                WHERE lower(category) LIKE ?
                ORDER BY canonical_name
                LIMIT ?
                """,
                (pattern, max(1, int(limit))),
            ).fetchall()
            drugs = [
                {
                    "canonical_name": str(row["canonical_name"]),
                    "category": str(row["category"]),
                    "region_scope": str(row["region_scope"]),
                    "is_common": bool(row["is_common"]),
                }
                for row in rows
            ]
            return {
                "category": category,
                "categories": [],
                "drugs": drugs,
                "count": len(drugs),
            }

    def list_aliases_for_drug(self, drug: str, limit: int = 100) -> dict[str, object]:
        """List every alias mapped to a canonical drug, grouped by alias_type."""
        if not self.normalization_db.exists():
            raise FileNotFoundError(f"Normalization DB not found: {self.normalization_db}")

        normalized = self.normalize_medication_names([drug])[0]
        if not normalized.resolved or normalized.drug_id is None:
            return {
                "drug": _normalized_medication_to_dict(normalized),
                "count": 0,
                "aliases": [],
                "by_type": {},
            }

        with sqlite3.connect(self.normalization_db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT alias, alias_type, region
                FROM drug_alias
                WHERE drug_id = ?
                ORDER BY
                    CASE alias_type
                        WHEN 'canonical' THEN 0
                        WHEN 'brand' THEN 1
                        WHEN 'common_generic' THEN 2
                        WHEN 'regional_common' THEN 3
                        ELSE 4
                    END,
                    LENGTH(alias),
                    alias
                LIMIT ?
                """,
                (normalized.drug_id, max(1, int(limit))),
            ).fetchall()

        aliases = [
            {"alias": str(row["alias"]), "alias_type": str(row["alias_type"]), "region": str(row["region"])}
            for row in rows
        ]
        by_type: dict[str, list[str]] = {}
        for entry in aliases:
            by_type.setdefault(entry["alias_type"], []).append(entry["alias"])
        return {
            "drug": _normalized_medication_to_dict(normalized),
            "count": len(aliases),
            "aliases": aliases,
            "by_type": by_type,
        }

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
            raw_signals: tuple[RawDdiSignal, ...] = ()
            if raw_signal_limit > 0 and _relation_exists(conn, "ddi_raw_signal"):
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

        interaction = KnownInteraction(
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
        return replace(
            interaction,
            practical_guidance=self._practical_guidance_for_pair(interaction.drug_a, interaction.drug_b),
        )

    def list_interactions_for_drug(
        self,
        drug: str,
        limit: int = 20,
        effect_limit: int = 3,
        *,
        min_severity: str | None = None,
        region: str | None = None,
        risk_flag: str | None = None,
    ) -> tuple[NormalizedMedication, tuple[KnownInteraction, ...]]:
        """List known/reference DDI pairs involving one normalized drug."""
        normalized = self.normalize_medication_names([drug])[0]
        if not normalized.resolved or normalized.canonical_name is None:
            return normalized, ()
        if not self.evidence_db.exists():
            raise FileNotFoundError(f"Evidence DB not found: {self.evidence_db}")

        where, params, _needs_join = _build_interaction_filters(
            drug_canonical=normalized.canonical_name,
            effect=None,
            min_severity=min_severity,
            region=region,
            risk_flag=risk_flag,
        )
        params.append(max(1, limit))

        with sqlite3.connect(self.evidence_db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT ki.drug_a, ki.drug_b
                FROM known_interaction ki
                WHERE {where}
                ORDER BY ki.severity_rank DESC, ki.row_count DESC, ki.drug_a, ki.drug_b
                LIMIT ?
                """,
                params,
            ).fetchall()

        interactions = tuple(
            self.lookup_known_interaction(
                str(row["drug_a"]),
                str(row["drug_b"]),
                effect_limit=effect_limit,
                raw_signal_limit=0,
            )
            for row in rows
        )
        return normalized, interactions

    def search_interactions(
        self,
        *,
        drug: str | None = None,
        effect: str | None = None,
        min_severity: str | None = None,
        region: str | None = None,
        risk_flag: str | None = None,
        limit: int = 20,
        effect_limit: int = 3,
    ) -> InteractionSearchResult:
        """Search known DDI pairs across the whole evidence DB by any combination of filters."""
        if not self.evidence_db.exists():
            raise FileNotFoundError(f"Evidence DB not found: {self.evidence_db}")

        echoed_filters: dict[str, object] = {
            "drug": drug,
            "effect": effect,
            "min_severity": min_severity,
            "region": region,
            "risk_flag": risk_flag,
            "limit": max(1, int(limit)),
        }

        drug_normalization: NormalizedMedication | None = None
        drug_canonical: str | None = None
        if drug is not None and drug.strip():
            drug_normalization = self.normalize_medication_names([drug])[0]
            if not drug_normalization.resolved or drug_normalization.canonical_name is None:
                return InteractionSearchResult(
                    filters=echoed_filters,
                    drug_normalization=drug_normalization,
                    interactions=(),
                )
            drug_canonical = drug_normalization.canonical_name

        where, params, needs_effect_join = _build_interaction_filters(
            drug_canonical=drug_canonical,
            effect=effect,
            min_severity=min_severity,
            region=region,
            risk_flag=risk_flag,
        )
        join_sql = (
            "JOIN known_interaction_effect kie ON kie.known_interaction_id = ki.id"
            if needs_effect_join
            else ""
        )
        distinct_sql = "DISTINCT" if needs_effect_join else ""
        params.append(max(1, int(limit)))

        with sqlite3.connect(self.evidence_db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT {distinct_sql} ki.drug_a, ki.drug_b, ki.severity_rank, ki.row_count
                FROM known_interaction ki
                {join_sql}
                WHERE {where}
                ORDER BY ki.severity_rank DESC, ki.row_count DESC, ki.drug_a, ki.drug_b
                LIMIT ?
                """,
                params,
            ).fetchall()

        interactions = tuple(
            self.lookup_known_interaction(
                str(row["drug_a"]),
                str(row["drug_b"]),
                effect_limit=effect_limit,
                raw_signal_limit=0,
            )
            for row in rows
        )
        return InteractionSearchResult(
            filters=echoed_filters,
            drug_normalization=drug_normalization,
            interactions=interactions,
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
        duplicate_warnings = _duplicate_ingredient_warnings(normalized)
        overall_severity = _overall_severity(ranked_findings)
        limitations = _report_limitations(unresolved, checked_pair_count, ranked_findings)

        return MedicationSafetyReport(
            input_medications=tuple(medication_names),
            normalized_medications=normalized,
            unresolved_medications=unresolved,
            checked_pair_count=checked_pair_count,
            findings=ranked_findings,
            duplicate_ingredient_warnings=duplicate_warnings,
            overall_severity=overall_severity,
            evidence_status=_evidence_status(ranked_findings, unresolved, checked_pair_count),
            limitations=limitations,
        )

    def _practical_guidance_for_pair(self, drug_a: str, drug_b: str) -> PracticalGuidance | None:
        if not self.normalization_db.exists():
            return None

        keys = _guidance_lookup_keys(drug_a, drug_b)
        if not keys:
            return None
        with sqlite3.connect(self.normalization_db) as conn:
            conn.row_factory = sqlite3.Row
            if not _relation_exists(conn, "practical_pair_guidance"):
                return None
            for left_key, right_key in keys:
                row = conn.execute(
                    """
                    SELECT *
                    FROM practical_pair_guidance
                    WHERE left_key = ? AND right_key = ?
                    ORDER BY CASE match_type WHEN 'exact' THEN 0 ELSE 1 END
                    LIMIT 1
                    """,
                    tuple(sorted((left_key, right_key))),
                ).fetchone()
                if row is not None:
                    return PracticalGuidance(
                        rule_id=str(row["rule_id"]),
                        practical_risk_tier=str(row["practical_risk_tier"]),
                        practical_summary=str(row["practical_summary"]),
                        dose_context_needed=str(row["dose_context_needed"]),
                        risk_factor_questions=str(row["risk_factor_questions"]),
                        source_urls=_pipe_tuple(str(row["source_urls"])),
                    )
        return None

    def bulk_check_pairs(
        self,
        candidates: list[str] | tuple[str, ...],
        against: list[str] | tuple[str, ...],
        *,
        effect_limit: int = 3,
    ) -> dict[str, object]:
        """Check each candidate medication against every drug in ``against``.

        Returns one row per candidate with normalized form, the list of known interactions
        against the comparison set, the highest severity found, and an unresolved flag if
        the candidate could not be normalized.
        """
        if not self.evidence_db.exists():
            raise FileNotFoundError(f"Evidence DB not found: {self.evidence_db}")

        against_normalized = tuple(self.normalize_medication_names(tuple(against)))
        against_canonicals: list[str] = []
        seen_canonicals: set[str] = set()
        for item in against_normalized:
            if item.resolved and item.canonical_name and item.canonical_name not in seen_canonicals:
                against_canonicals.append(item.canonical_name)
                seen_canonicals.add(item.canonical_name)

        candidate_normalized = tuple(self.normalize_medication_names(tuple(candidates)))
        candidate_results: list[dict[str, object]] = []
        unresolved: list[NormalizedMedication] = []
        overall_rank = 0
        overall_severity = "None"

        for candidate in candidate_normalized:
            if not candidate.resolved or candidate.canonical_name is None:
                unresolved.append(candidate)
                candidate_results.append(
                    {
                        "candidate": candidate.input_name,
                        "normalized": _normalized_medication_to_dict(candidate),
                        "findings": [],
                        "highest_severity": "None",
                        "interaction_count": 0,
                        "unresolved": True,
                    }
                )
                continue

            findings: list[dict[str, object]] = []
            best_rank = 0
            best_severity = "None"
            for partner in against_canonicals:
                if partner == candidate.canonical_name:
                    continue
                interaction = self.lookup_known_interaction(
                    candidate.canonical_name,
                    partner,
                    effect_limit=effect_limit,
                    raw_signal_limit=0,
                )
                if not interaction.found:
                    continue
                findings.append(_known_interaction_to_dict(interaction))
                rank = _severity_rank(interaction.severity)
                if rank > best_rank:
                    best_rank = rank
                    best_severity = interaction.severity or "None"

            if best_rank > overall_rank:
                overall_rank = best_rank
                overall_severity = best_severity

            candidate_results.append(
                {
                    "candidate": candidate.input_name,
                    "normalized": _normalized_medication_to_dict(candidate),
                    "findings": findings,
                    "highest_severity": best_severity,
                    "interaction_count": len(findings),
                    "unresolved": False,
                }
            )

        return {
            "against": [_normalized_medication_to_dict(item) for item in against_normalized],
            "candidates": candidate_results,
            "unresolved_candidates": [_normalized_medication_to_dict(item) for item in unresolved],
            "overall_severity": overall_severity,
        }

    def list_evidence_sources(self) -> list[dict[str, object]]:
        """Return source-file import stats from the evidence artifact."""
        if not self.evidence_db.exists():
            raise FileNotFoundError(f"Evidence DB not found: {self.evidence_db}")

        with sqlite3.connect(self.evidence_db) as conn:
            conn.row_factory = sqlite3.Row
            return [
                {
                    "source_file": str(row["source_file"]),
                    "region": str(row["region"]),
                    "rows_seen": int(row["rows_seen"]),
                    "rows_imported": int(row["rows_imported"]),
                    "rows_unresolved": int(row["rows_unresolved"]),
                    "unique_pairs_imported": int(row["unique_pairs_imported"]),
                }
                for row in conn.execute(
                    """
                    SELECT source_file, region, rows_seen, rows_imported, rows_unresolved, unique_pairs_imported
                    FROM evidence_import_file
                    ORDER BY source_file
                    """
                )
            ]

    def list_import_issues(
        self,
        source_file: str | None = None,
        query: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        """Return unresolved DDI import rows for artifact/debug review."""
        if not self.evidence_db.exists():
            raise FileNotFoundError(f"Evidence DB not found: {self.evidence_db}")

        clauses: list[str] = []
        params: list[object] = []
        if source_file:
            clauses.append("source_file = ?")
            params.append(source_file)
        if query:
            normalized_query = normalize_lookup_text(query)
            clauses.append("(normalized_drug1 LIKE ? OR normalized_drug2 LIKE ? OR drug1 LIKE ? OR drug2 LIKE ?)")
            pattern = f"%{normalized_query}%"
            text_pattern = f"%{query.strip()}%"
            params.extend([pattern, pattern, text_pattern, text_pattern])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, limit))

        with sqlite3.connect(self.evidence_db) as conn:
            conn.row_factory = sqlite3.Row
            if not _relation_exists(conn, "ddi_import_issue"):
                return []
            return [
                {
                    "source_file": str(row["source_file"]),
                    "row_number": int(row["row_number"]),
                    "drug1": str(row["drug1"]),
                    "drug2": str(row["drug2"]),
                    "normalized_drug1": str(row["normalized_drug1"]),
                    "normalized_drug2": str(row["normalized_drug2"]),
                    "reason": str(row["reason"]),
                }
                for row in conn.execute(
                    f"""
                    SELECT source_file, row_number, drug1, drug2, normalized_drug1, normalized_drug2, reason
                    FROM ddi_import_issue
                    {where}
                    ORDER BY source_file, row_number
                    LIMIT ?
                    """,
                    params,
                )
            ]


def _relation_exists(conn: sqlite3.Connection, relation_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            (relation_name,),
        ).fetchone()
        is not None
    )


_REGION_ALIASES: dict[str, tuple[str, ...]] = {
    "us": ("us",),
    "usa": ("us",),
    "united states": ("us",),
    "united states of america": ("us",),
    "eu": ("eu/eea",),
    "eea": ("eu/eea",),
    "europe": ("eu/eea",),
    "european union": ("eu/eea",),
    "eu/eea": ("eu/eea",),
    "in": ("india", "india_expanded", "india_common_generic"),
    "india": ("india", "india_expanded", "india_common_generic"),
    "india_expanded": ("india_expanded",),
    "india_common_generic": ("india_common_generic",),
}


def _canonicalize_region(region: str) -> tuple[str, ...]:
    key = region.casefold().strip()
    if not key:
        return ()
    return _REGION_ALIASES.get(key, (key,))


def _input_severity_rank(severity: str) -> int:
    return {
        "major": 3,
        "moderate": 2,
        "minor": 1,
        "high": 3,
        "medium": 2,
        "low": 1,
    }.get(severity.casefold().strip(), 0)


def _build_interaction_filters(
    *,
    drug_canonical: str | None,
    effect: str | None,
    min_severity: str | None,
    region: str | None,
    risk_flag: str | None,
) -> tuple[str, list[object], bool]:
    """Build a WHERE clause + params for known_interaction (alias `ki`)."""
    clauses: list[str] = []
    params: list[object] = []
    needs_effect_join = False

    if drug_canonical:
        clauses.append("(ki.drug_a = ? OR ki.drug_b = ?)")
        params.extend([drug_canonical, drug_canonical])

    if min_severity:
        rank = _input_severity_rank(min_severity)
        if rank > 0:
            clauses.append("ki.severity_rank >= ?")
            params.append(rank)

    if region:
        canonical = _canonicalize_region(region)
        if canonical:
            sub = " OR ".join("ki.source_regions_json LIKE ?" for _ in canonical)
            clauses.append(f"({sub})")
            for value in canonical:
                params.append(f'%"{value}"%')

    if risk_flag and risk_flag.strip():
        clauses.append("lower(ki.risk_flags_json) LIKE ?")
        params.append(f"%{risk_flag.casefold().strip()}%")

    if effect and effect.strip():
        needs_effect_join = True
        clauses.append("lower(kie.adverse_effect) LIKE ?")
        params.append(f"%{effect.casefold().strip()}%")

    where = " AND ".join(clauses) if clauses else "1=1"
    return where, params, needs_effect_join


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


def _practical_guidance_to_dict(guidance: PracticalGuidance) -> dict[str, object]:
    return {
        "rule_id": guidance.rule_id,
        "practical_risk_tier": guidance.practical_risk_tier,
        "practical_summary": guidance.practical_summary,
        "dose_context_needed": guidance.dose_context_needed,
        "risk_factor_questions": guidance.risk_factor_questions,
        "source_urls": list(guidance.source_urls),
    }


def _duplicate_ingredient_warning_to_dict(warning: DuplicateIngredientWarning) -> dict[str, object]:
    return {
        "ingredient": warning.ingredient,
        "input_names": list(warning.input_names),
        "practical_risk_tier": warning.practical_risk_tier,
        "practical_summary": warning.practical_summary,
        "dose_context_needed": warning.dose_context_needed,
        "risk_factor_questions": warning.risk_factor_questions,
        "source_urls": list(warning.source_urls),
    }


def _common_medicine_row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    return {
        "medicine_id": str(row["medicine_id"]),
        "canonical_name": str(row["canonical_name"]),
        "generic_or_common_name": str(row["generic_or_common_name"]),
        "composition_or_strength_pattern": str(row["composition_or_strength_pattern"]),
        "dosage_form": str(row["dosage_form"]),
        "therapeutic_category": str(row["therapeutic_category"]),
        "common_daily_life_use_india": str(row["common_daily_life_use_india"]),
        "common_brand_examples_india": str(row["common_brand_examples_india"]),
        "availability_context_india": str(row["availability_context_india"]),
        "otc_or_rx": str(row["otc_or_rx"]),
        "nlem_or_jan_aushadhi_presence": str(row["nlem_or_jan_aushadhi_presence"]),
        "india_relevance": str(row["india_relevance"]),
        "patient_risk_flags_india": str(row["patient_risk_flags_india"]),
        "source_basis": str(row["source_basis"]),
        "source_urls": str(row["source_urls"]),
        "dataset_note": str(row["dataset_note"]),
    }


def _known_interaction_to_dict(interaction: KnownInteraction) -> dict[str, object]:
    payload = {
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
    if interaction.practical_guidance is not None:
        payload["practical_guidance"] = _practical_guidance_to_dict(interaction.practical_guidance)
    return payload


def _duplicate_ingredient_warnings(
    normalized: tuple[NormalizedMedication, ...],
) -> tuple[DuplicateIngredientWarning, ...]:
    by_ingredient: dict[str, set[str]] = {}
    for item in normalized:
        if item.resolved and item.canonical_name:
            by_ingredient.setdefault(item.canonical_name, set()).add(item.input_name)

    warnings: list[DuplicateIngredientWarning] = []
    for ingredient, input_names in sorted(by_ingredient.items()):
        if len(input_names) < 2:
            continue
        summary = (
            "Acetaminophen/paracetamol appears in more than one product. The main practical risk is taking too much total "
            "acetaminophen/paracetamol across the day, especially with other cold, flu, fever, or pain medicines."
            if ingredient == "acetaminophen"
            else f"{ingredient} appears in more than one product. This can turn an intended combination into duplicate dosing."
        )
        warnings.append(
            DuplicateIngredientWarning(
                ingredient=ingredient,
                input_names=tuple(sorted(input_names)),
                practical_risk_tier="duplicate_dose_risk",
                practical_summary=summary,
                dose_context_needed="Ask the dose, frequency, duration, and whether any other medicine contains the same active ingredient.",
                risk_factor_questions="Liver disease or heavy alcohol use for acetaminophen/paracetamol; kidney disease, ulcer/bleeding history, or blood thinner use for NSAIDs.",
                source_urls=(
                    "https://www.nhsinform.scot/tests-and-treatments/medicines-and-medical-aids/types-of-medicine/ibuprofen",
                    "https://assets.publishing.service.gov.uk/government/uploads/system/uploads/attachment_data/file/813026/national_pain_relief_poster_including_child_doses_v29.pdf",
                ),
            )
        )
    return tuple(warnings)


def _guidance_lookup_keys(drug_a: str, drug_b: str) -> tuple[tuple[str, str], ...]:
    keys: list[tuple[str, str]] = [(normalize_lookup_text(drug_a), normalize_lookup_text(drug_b))]
    class_a = _drug_classes(drug_a)
    class_b = _drug_classes(drug_b)
    for left_class in class_a:
        keys.append((left_class, normalize_lookup_text(drug_b)))
    for right_class in class_b:
        keys.append((normalize_lookup_text(drug_a), right_class))
    for left_class in class_a:
        for right_class in class_b:
            keys.append((left_class, right_class))
    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for left, right in keys:
        ordered = tuple(sorted((left, right)))
        if left and right and ordered not in seen:
            seen.add(ordered)
            deduped.append(ordered)
    return tuple(deduped)


def _drug_classes(canonical_name: str) -> tuple[str, ...]:
    normalized = normalize_lookup_text(canonical_name)
    classes: list[str] = []
    if normalized in NSAID_CANONICALS:
        classes.append("nsaid")
    if normalized in ANTICOAGULANT_ANTIPLATELET_CANONICALS:
        classes.append("anticoagulant antiplatelet")
    return tuple(classes)


def _pipe_tuple(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split("|") if part.strip())


def _ingredient_map_candidates(normalized_input: str) -> tuple[str, ...]:
    if not normalized_input:
        return ()
    candidates: set[str] = {normalized_input}
    tokens = [token for token in normalized_input.split(" ") if token]
    for window_size in range(1, min(5, len(tokens)) + 1):
        for start in range(0, len(tokens) - window_size + 1):
            candidates.add(" ".join(tokens[start : start + window_size]))
    return tuple(sorted((item for item in candidates if len(item) >= 3), key=lambda item: (-len(item), item)))


def _ingredient_rows_for_candidate(conn: sqlite3.Connection, normalized_candidate: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT d.id, d.canonical_name, m.brand_name
        FROM medicine_ingredient_map m
        JOIN drug d ON d.id = m.ingredient_drug_id
        WHERE m.normalized_brand_name = ?
        ORDER BY m.ingredient_order, d.canonical_name
        """,
        (normalized_candidate,),
    ).fetchall()


def _fuzzy_brand_candidate(conn: sqlite3.Connection, normalized_candidate: str) -> str | None:
    if len(normalized_candidate) < 5:
        return None
    rows = conn.execute(
        """
        SELECT DISTINCT normalized_brand_name
        FROM medicine_ingredient_map
        WHERE length(normalized_brand_name) BETWEEN ? AND ?
        """,
        (len(normalized_candidate) - 2, len(normalized_candidate) + 2),
    ).fetchall()
    ranked = []
    for (brand,) in rows:
        brand_value = str(brand)
        distance = _edit_distance(normalized_candidate, brand_value)
        if (
            distance <= max(1, min(2, len(brand_value) // 5))
            and brand_value[:1] == normalized_candidate[:1]
        ):
            ranked.append((distance, len(brand_value), brand_value))
    return sorted(ranked)[0][2] if ranked else None


def _edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    current = [0] * (len(b) + 1)
    for index_a, char_a in enumerate(a, start=1):
        current[0] = index_a
        for index_b, char_b in enumerate(b, start=1):
            current[index_b] = min(
                previous[index_b] + 1,
                current[index_b - 1] + 1,
                previous[index_b - 1] + (0 if char_a == char_b else 1),
            )
        previous, current = current, previous
    return previous[len(b)]


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
