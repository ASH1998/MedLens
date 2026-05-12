"""Build the MedLens evidence SQLite artifact from raw DDI CSVs."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from medlens.artifacts.build_normalization import normalize_lookup_text
from medlens.artifacts.schema import EVIDENCE_SCHEMA

SEVERITY_RANK = {"Minor": 1, "Moderate": 2, "Major": 3}
SEVERITY_MAP = {
    "high": "Major",
    "major": "Major",
    "medium": "Moderate",
    "moderate": "Moderate",
    "low moderate": "Moderate",
    "lowmoderate": "Moderate",
    "low": "Minor",
    "minor": "Minor",
}


@dataclass(frozen=True)
class SourceSpec:
    filename: str
    region: str
    id_column: str
    evidence_column: str
    risk_flags_column: str
    note_column: str
    dataset_type_column: str | None = "dataset_type"
    source_basis_column: str | None = "source_basis"
    category_column: str | None = "interaction_category"
    population_relevance_column: str | None = None


SOURCE_SPECS: tuple[SourceSpec, ...] = (
    SourceSpec(
        filename="usa_prioritized_ddi_ade_signals.csv",
        region="us",
        id_column="interaction_id",
        evidence_column="evidence_basis",
        risk_flags_column="patient_risk_flags_us",
        note_column="use_case_note",
        population_relevance_column="us_population_relevance",
    ),
    SourceSpec(
        filename="eu_eea_prioritized_ddi_ade_signals.csv",
        region="eu/eea",
        id_column="interaction_id",
        evidence_column="evidence_basis",
        risk_flags_column="patient_risk_flags_eu_eea",
        note_column="use_case_note",
        population_relevance_column="eu_eea_population_relevance",
    ),
    SourceSpec(
        filename="india_prioritized_ddi_ade_signals.csv",
        region="india",
        id_column="interaction_id",
        evidence_column="evidence_level",
        risk_flags_column="patient_risk_flags_india",
        note_column="use_case_note",
        dataset_type_column=None,
        category_column=None,
        population_relevance_column="india_relevance",
    ),
    SourceSpec(
        filename="india_expanded_prioritized_ddi_ade_signals.csv",
        region="india_expanded",
        id_column="signal_id",
        evidence_column="evidence_basis",
        risk_flags_column="patient_risk_flags_india",
        note_column="not_for_clinical_decision",
        dataset_type_column=None,
        source_basis_column=None,
        category_column="interaction_class",
        population_relevance_column="india_relevance_context",
    ),
    SourceSpec(
        filename="india_common_generic_ddi_5000.csv",
        region="india_common_generic",
        id_column="interaction_id",
        evidence_column="evidence_level",
        risk_flags_column="patient_risk_flags_india",
        note_column="use_case_note",
        dataset_type_column=None,
        category_column=None,
        population_relevance_column="india_relevance",
    ),
)


@dataclass(frozen=True)
class ResolvedDrug:
    drug_id: int
    canonical_name: str


@dataclass
class PairAggregate:
    drug_a_id: int
    drug_b_id: int
    drug_a: str
    drug_b: str
    severity_rank: int = 0
    row_count: int = 0
    source_regions: set[str] = field(default_factory=set)
    evidence_bases: set[str] = field(default_factory=set)
    source_bases: set[str] = field(default_factory=set)
    source_urls: set[str] = field(default_factory=set)
    mechanisms: set[str] = field(default_factory=set)
    risk_flags: set[str] = field(default_factory=set)
    dataset_types: set[str] = field(default_factory=set)
    use_case_notes: set[str] = field(default_factory=set)
    effects: dict[tuple[str, str], "EffectAggregate"] = field(default_factory=dict)


@dataclass
class EffectAggregate:
    adverse_effect: str
    severity: str
    severity_rank: int
    row_count: int = 0
    source_regions: set[str] = field(default_factory=set)


@dataclass
class FileImportStats:
    source_file: str
    region: str
    rows_seen: int = 0
    rows_imported: int = 0
    rows_unresolved: int = 0
    imported_pairs: set[tuple[str, str]] = field(default_factory=set)


@dataclass
class RawSignalRow:
    source_file: str
    source_row_number: int
    source_signal_id: str
    region: str
    drug1_raw: str
    drug2_raw: str
    normalized_drug1: str
    normalized_drug2: str
    drug_a: str | None
    drug_b: str | None
    resolved: bool
    adverse_effect: str
    severity: str
    severity_rank: int
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


def _json_array(values: set[str]) -> str:
    return json.dumps(sorted(v for v in values if v), separators=(",", ":"))


def _split_values(value: str) -> set[str]:
    return {part.strip() for part in value.split("|") if part.strip()}


def _field(row: dict[str, str], column: str | None) -> str:
    if not column:
        return ""
    return (row.get(column) or "").strip()


def normalize_severity(value: str) -> str:
    normalized = normalize_lookup_text(value)
    return SEVERITY_MAP.get(normalized, "Moderate")


def load_normalization_index(normalization_db: Path) -> dict[str, ResolvedDrug]:
    with sqlite3.connect(normalization_db) as conn:
        rows = conn.execute(
            """
            SELECT a.normalized_alias, d.id, d.canonical_name
            FROM drug_alias a
            JOIN drug d ON d.id = a.drug_id
            """
        ).fetchall()
    return {
        str(alias): ResolvedDrug(drug_id=int(drug_id), canonical_name=str(canonical_name))
        for alias, drug_id, canonical_name in rows
    }


def resolve_drug(raw_name: str, index: dict[str, ResolvedDrug]) -> ResolvedDrug | None:
    return index.get(normalize_lookup_text(raw_name))


def _pair_key(left: ResolvedDrug, right: ResolvedDrug) -> tuple[ResolvedDrug, ResolvedDrug]:
    return (left, right) if left.canonical_name <= right.canonical_name else (right, left)


def _add_text(target: set[str], value: str) -> None:
    if value:
        target.add(" ".join(value.split()))


def _raw_signal_from_row(
    row: dict[str, str],
    spec: SourceSpec,
    row_number: int,
    left: ResolvedDrug | None,
    right: ResolvedDrug | None,
) -> RawSignalRow:
    drug1 = _field(row, "drug1")
    drug2 = _field(row, "drug2")
    severity = normalize_severity(row.get("severity", ""))
    drug_a: str | None = None
    drug_b: str | None = None
    if left is not None and right is not None:
        first, second = _pair_key(left, right)
        drug_a = first.canonical_name
        drug_b = second.canonical_name

    return RawSignalRow(
        source_file=spec.filename,
        source_row_number=row_number,
        source_signal_id=_field(row, spec.id_column),
        region=spec.region,
        drug1_raw=drug1,
        drug2_raw=drug2,
        normalized_drug1=normalize_lookup_text(drug1),
        normalized_drug2=normalize_lookup_text(drug2),
        drug_a=drug_a,
        drug_b=drug_b,
        resolved=left is not None and right is not None,
        adverse_effect=normalize_lookup_text(row.get("adverse_effect", "")),
        severity=severity,
        severity_rank=SEVERITY_RANK[severity],
        mechanism_or_rationale=_field(row, "mechanism_or_rationale"),
        interaction_category=_field(row, spec.category_column),
        interaction_direction=_field(row, "interaction_direction"),
        evidence_basis=_field(row, spec.evidence_column),
        source_basis=_field(row, spec.source_basis_column),
        source_urls=_field(row, "source_urls"),
        population_relevance=_field(row, spec.population_relevance_column),
        patient_risk_flags=_field(row, spec.risk_flags_column),
        dataset_type=_field(row, spec.dataset_type_column),
        use_case_note=_field(row, spec.note_column),
    )


def _add_row_to_pair(pair: PairAggregate, row: dict[str, str], spec: SourceSpec) -> None:
    severity = normalize_severity(row.get("severity", ""))
    severity_rank = SEVERITY_RANK[severity]
    adverse_effect = normalize_lookup_text(row.get("adverse_effect", ""))

    pair.row_count += 1
    pair.severity_rank = max(pair.severity_rank, severity_rank)
    pair.source_regions.add(spec.region)
    _add_text(pair.evidence_bases, _field(row, spec.evidence_column))
    _add_text(pair.source_bases, _field(row, spec.source_basis_column))
    pair.source_urls.update(_split_values(_field(row, "source_urls")))
    _add_text(pair.mechanisms, _field(row, "mechanism_or_rationale"))
    _add_text(pair.risk_flags, _field(row, spec.risk_flags_column))
    _add_text(pair.dataset_types, _field(row, spec.dataset_type_column))
    _add_text(pair.use_case_notes, _field(row, spec.note_column))

    if adverse_effect:
        effect_key = (adverse_effect, severity)
        if effect_key not in pair.effects:
            pair.effects[effect_key] = EffectAggregate(
                adverse_effect=adverse_effect,
                severity=severity,
                severity_rank=severity_rank,
            )
        effect = pair.effects[effect_key]
        effect.row_count += 1
        effect.source_regions.add(spec.region)


def _issue_reason(left: ResolvedDrug | None, right: ResolvedDrug | None) -> str:
    if left is None and right is None:
        return "both_drugs_unresolved"
    if left is None:
        return "drug1_unresolved"
    return "drug2_unresolved"


def import_ddi_csvs(
    input_dir: Path,
    normalization_db: Path,
) -> tuple[dict[tuple[str, str], PairAggregate], list[dict[str, object]], list[FileImportStats], list[RawSignalRow]]:
    index = load_normalization_index(normalization_db)
    pair_aggregates: dict[tuple[str, str], PairAggregate] = {}
    issues: list[dict[str, object]] = []
    file_stats: list[FileImportStats] = []
    raw_signals: list[RawSignalRow] = []

    for spec in SOURCE_SPECS:
        path = input_dir / spec.filename
        if not path.exists():
            continue

        stats = FileImportStats(source_file=spec.filename, region=spec.region)
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row_number, row in enumerate(reader, start=2):
                stats.rows_seen += 1
                drug1 = _field(row, "drug1")
                drug2 = _field(row, "drug2")
                left = resolve_drug(drug1, index)
                right = resolve_drug(drug2, index)
                raw_signals.append(_raw_signal_from_row(row, spec, row_number, left, right))

                if left is None or right is None:
                    stats.rows_unresolved += 1
                    issues.append(
                        {
                            "source_file": spec.filename,
                            "row_number": row_number,
                            "drug1": drug1,
                            "drug2": drug2,
                            "normalized_drug1": normalize_lookup_text(drug1),
                            "normalized_drug2": normalize_lookup_text(drug2),
                            "reason": _issue_reason(left, right),
                        }
                    )
                    continue

                first, second = _pair_key(left, right)
                key = (first.canonical_name, second.canonical_name)
                if key not in pair_aggregates:
                    pair_aggregates[key] = PairAggregate(
                        drug_a_id=first.drug_id,
                        drug_b_id=second.drug_id,
                        drug_a=first.canonical_name,
                        drug_b=second.canonical_name,
                    )

                _add_row_to_pair(pair_aggregates[key], row, spec)
                stats.rows_imported += 1
                stats.imported_pairs.add(key)

        file_stats.append(stats)

    return pair_aggregates, issues, file_stats, raw_signals


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(EVIDENCE_SCHEMA)


def write_evidence_db(
    output: Path,
    pairs: dict[tuple[str, str], PairAggregate],
    issues: list[dict[str, object]],
    file_stats: list[FileImportStats],
    raw_signals: list[RawSignalRow],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    with sqlite3.connect(output) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        create_schema(conn)
        interaction_ids_by_pair: dict[tuple[str, str], int] = {}
        for pair in pairs.values():
            severity = next(name for name, rank in SEVERITY_RANK.items() if rank == pair.severity_rank)
            cur = conn.execute(
                """
                INSERT INTO known_interaction (
                    drug_a_id, drug_b_id, drug_a, drug_b, severity, severity_rank, row_count,
                    source_regions_json, evidence_bases_json, source_bases_json, source_urls_json,
                    mechanisms_json, risk_flags_json, dataset_types_json, use_case_notes_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pair.drug_a_id,
                    pair.drug_b_id,
                    pair.drug_a,
                    pair.drug_b,
                    severity,
                    pair.severity_rank,
                    pair.row_count,
                    _json_array(pair.source_regions),
                    _json_array(pair.evidence_bases),
                    _json_array(pair.source_bases),
                    _json_array(pair.source_urls),
                    _json_array(pair.mechanisms),
                    _json_array(pair.risk_flags),
                    _json_array(pair.dataset_types),
                    _json_array(pair.use_case_notes),
                ),
            )
            interaction_id = int(cur.lastrowid)
            interaction_ids_by_pair[(pair.drug_a, pair.drug_b)] = interaction_id
            for effect in pair.effects.values():
                conn.execute(
                    """
                    INSERT INTO known_interaction_effect (
                        known_interaction_id, adverse_effect, severity, severity_rank,
                        row_count, source_regions_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        interaction_id,
                        effect.adverse_effect,
                        effect.severity,
                        effect.severity_rank,
                        effect.row_count,
                        _json_array(effect.source_regions),
                    ),
                )

        conn.executemany(
            """
            INSERT INTO ddi_raw_signal (
                known_interaction_id, source_file, source_row_number, source_signal_id,
                region, drug1_raw, drug2_raw, normalized_drug1, normalized_drug2,
                drug_a, drug_b, resolved, adverse_effect, severity, severity_rank,
                mechanism_or_rationale, interaction_category, interaction_direction,
                evidence_basis, source_basis, source_urls, population_relevance,
                patient_risk_flags, dataset_type, use_case_note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    interaction_ids_by_pair.get((raw.drug_a or "", raw.drug_b or "")),
                    raw.source_file,
                    raw.source_row_number,
                    raw.source_signal_id,
                    raw.region,
                    raw.drug1_raw,
                    raw.drug2_raw,
                    raw.normalized_drug1,
                    raw.normalized_drug2,
                    raw.drug_a,
                    raw.drug_b,
                    1 if raw.resolved else 0,
                    raw.adverse_effect,
                    raw.severity,
                    raw.severity_rank,
                    raw.mechanism_or_rationale,
                    raw.interaction_category,
                    raw.interaction_direction,
                    raw.evidence_basis,
                    raw.source_basis,
                    raw.source_urls,
                    raw.population_relevance,
                    raw.patient_risk_flags,
                    raw.dataset_type,
                    raw.use_case_note,
                )
                for raw in raw_signals
            ],
        )

        conn.executemany(
            """
            INSERT INTO ddi_import_issue (
                source_file, row_number, drug1, drug2, normalized_drug1,
                normalized_drug2, reason
            )
            VALUES (:source_file, :row_number, :drug1, :drug2, :normalized_drug1, :normalized_drug2, :reason)
            """,
            issues,
        )
        conn.executemany(
            """
            INSERT INTO evidence_import_file (
                source_file, region, rows_seen, rows_imported, rows_unresolved, unique_pairs_imported
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    stat.source_file,
                    stat.region,
                    stat.rows_seen,
                    stat.rows_imported,
                    stat.rows_unresolved,
                    len(stat.imported_pairs),
                )
                for stat in file_stats
            ],
        )
        conn.commit()
        conn.execute("PRAGMA optimize")


def build_evidence_db(input_dir: Path, normalization_db: Path, output: Path) -> None:
    pairs, issues, file_stats, raw_signals = import_ddi_csvs(input_dir, normalization_db)
    write_evidence_db(output, pairs, issues, file_stats, raw_signals)


def compact_evidence_db(source: Path, output: Path) -> None:
    """Create a mobile-sized evidence DB while preserving raw signal rows.

    The compact artifact stores repeated raw-signal text once in a dictionary and
    exposes the original ``ddi_raw_signal`` shape as a read-only view. This keeps
    source URLs and raw evidence available without repeating the same long text on
    every row.
    """
    if not source.exists():
        raise FileNotFoundError(f"Evidence DB not found: {source}")
    if source.resolve() == output.resolve():
        raise ValueError("source and output must be different paths")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    shutil.copyfile(source, output)

    with sqlite3.connect(output) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = OFF;

            DROP TABLE IF EXISTS ddi_raw_signal_compact;
            DROP TABLE IF EXISTS raw_text_value;

            CREATE TABLE raw_text_value(
                id INTEGER PRIMARY KEY,
                value TEXT NOT NULL UNIQUE
            );

            INSERT OR IGNORE INTO raw_text_value(value)
            SELECT COALESCE(source_file, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(region, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(drug1_raw, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(drug2_raw, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(normalized_drug1, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(normalized_drug2, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(drug_a, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(drug_b, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(adverse_effect, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(severity, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(mechanism_or_rationale, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(interaction_category, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(interaction_direction, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(evidence_basis, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(source_basis, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(source_urls, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(population_relevance, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(patient_risk_flags, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(dataset_type, '') FROM ddi_raw_signal
            UNION SELECT COALESCE(use_case_note, '') FROM ddi_raw_signal;

            CREATE TABLE ddi_raw_signal_compact(
                id INTEGER PRIMARY KEY,
                known_interaction_id INTEGER,
                source_file_id INTEGER NOT NULL,
                source_row_number INTEGER NOT NULL,
                source_signal_id TEXT,
                region_id INTEGER NOT NULL,
                drug1_raw_id INTEGER NOT NULL,
                drug2_raw_id INTEGER NOT NULL,
                normalized_drug1_id INTEGER NOT NULL,
                normalized_drug2_id INTEGER NOT NULL,
                drug_a_id INTEGER NOT NULL,
                drug_b_id INTEGER NOT NULL,
                resolved INTEGER NOT NULL,
                adverse_effect_id INTEGER NOT NULL,
                severity_id INTEGER NOT NULL,
                severity_rank INTEGER NOT NULL,
                mechanism_or_rationale_id INTEGER NOT NULL,
                interaction_category_id INTEGER NOT NULL,
                interaction_direction_id INTEGER NOT NULL,
                evidence_basis_id INTEGER NOT NULL,
                source_basis_id INTEGER NOT NULL,
                source_urls_id INTEGER NOT NULL,
                population_relevance_id INTEGER NOT NULL,
                patient_risk_flags_id INTEGER NOT NULL,
                dataset_type_id INTEGER NOT NULL,
                use_case_note_id INTEGER NOT NULL
            );

            INSERT INTO ddi_raw_signal_compact
            SELECT
                r.id,
                r.known_interaction_id,
                sf.id,
                r.source_row_number,
                r.source_signal_id,
                reg.id,
                d1.id,
                d2.id,
                nd1.id,
                nd2.id,
                da.id,
                db.id,
                r.resolved,
                ae.id,
                sev.id,
                r.severity_rank,
                mech.id,
                cat.id,
                dir.id,
                eb.id,
                sb.id,
                urls.id,
                pop.id,
                risk.id,
                dtype.id,
                note.id
            FROM ddi_raw_signal r
            JOIN raw_text_value sf ON sf.value = COALESCE(r.source_file, '')
            JOIN raw_text_value reg ON reg.value = COALESCE(r.region, '')
            JOIN raw_text_value d1 ON d1.value = COALESCE(r.drug1_raw, '')
            JOIN raw_text_value d2 ON d2.value = COALESCE(r.drug2_raw, '')
            JOIN raw_text_value nd1 ON nd1.value = COALESCE(r.normalized_drug1, '')
            JOIN raw_text_value nd2 ON nd2.value = COALESCE(r.normalized_drug2, '')
            JOIN raw_text_value da ON da.value = COALESCE(r.drug_a, '')
            JOIN raw_text_value db ON db.value = COALESCE(r.drug_b, '')
            JOIN raw_text_value ae ON ae.value = COALESCE(r.adverse_effect, '')
            JOIN raw_text_value sev ON sev.value = COALESCE(r.severity, '')
            JOIN raw_text_value mech ON mech.value = COALESCE(r.mechanism_or_rationale, '')
            JOIN raw_text_value cat ON cat.value = COALESCE(r.interaction_category, '')
            JOIN raw_text_value dir ON dir.value = COALESCE(r.interaction_direction, '')
            JOIN raw_text_value eb ON eb.value = COALESCE(r.evidence_basis, '')
            JOIN raw_text_value sb ON sb.value = COALESCE(r.source_basis, '')
            JOIN raw_text_value urls ON urls.value = COALESCE(r.source_urls, '')
            JOIN raw_text_value pop ON pop.value = COALESCE(r.population_relevance, '')
            JOIN raw_text_value risk ON risk.value = COALESCE(r.patient_risk_flags, '')
            JOIN raw_text_value dtype ON dtype.value = COALESCE(r.dataset_type, '')
            JOIN raw_text_value note ON note.value = COALESCE(r.use_case_note, '');

            DROP TABLE ddi_raw_signal;

            CREATE VIEW ddi_raw_signal AS
            SELECT
                r.id,
                r.known_interaction_id,
                sf.value AS source_file,
                r.source_row_number,
                r.source_signal_id,
                reg.value AS region,
                d1.value AS drug1_raw,
                d2.value AS drug2_raw,
                nd1.value AS normalized_drug1,
                nd2.value AS normalized_drug2,
                NULLIF(da.value, '') AS drug_a,
                NULLIF(db.value, '') AS drug_b,
                r.resolved,
                NULLIF(ae.value, '') AS adverse_effect,
                NULLIF(sev.value, '') AS severity,
                r.severity_rank,
                NULLIF(mech.value, '') AS mechanism_or_rationale,
                NULLIF(cat.value, '') AS interaction_category,
                NULLIF(dir.value, '') AS interaction_direction,
                NULLIF(eb.value, '') AS evidence_basis,
                NULLIF(sb.value, '') AS source_basis,
                NULLIF(urls.value, '') AS source_urls,
                NULLIF(pop.value, '') AS population_relevance,
                NULLIF(risk.value, '') AS patient_risk_flags,
                NULLIF(dtype.value, '') AS dataset_type,
                NULLIF(note.value, '') AS use_case_note
            FROM ddi_raw_signal_compact r
            JOIN raw_text_value sf ON sf.id = r.source_file_id
            JOIN raw_text_value reg ON reg.id = r.region_id
            JOIN raw_text_value d1 ON d1.id = r.drug1_raw_id
            JOIN raw_text_value d2 ON d2.id = r.drug2_raw_id
            JOIN raw_text_value nd1 ON nd1.id = r.normalized_drug1_id
            JOIN raw_text_value nd2 ON nd2.id = r.normalized_drug2_id
            JOIN raw_text_value da ON da.id = r.drug_a_id
            JOIN raw_text_value db ON db.id = r.drug_b_id
            JOIN raw_text_value ae ON ae.id = r.adverse_effect_id
            JOIN raw_text_value sev ON sev.id = r.severity_id
            JOIN raw_text_value mech ON mech.id = r.mechanism_or_rationale_id
            JOIN raw_text_value cat ON cat.id = r.interaction_category_id
            JOIN raw_text_value dir ON dir.id = r.interaction_direction_id
            JOIN raw_text_value eb ON eb.id = r.evidence_basis_id
            JOIN raw_text_value sb ON sb.id = r.source_basis_id
            JOIN raw_text_value urls ON urls.id = r.source_urls_id
            JOIN raw_text_value pop ON pop.id = r.population_relevance_id
            JOIN raw_text_value risk ON risk.id = r.patient_risk_flags_id
            JOIN raw_text_value dtype ON dtype.id = r.dataset_type_id
            JOIN raw_text_value note ON note.id = r.use_case_note_id;

            CREATE INDEX idx_ddi_raw_signal_interaction ON ddi_raw_signal_compact(known_interaction_id);
            CREATE INDEX idx_ddi_raw_signal_pair ON ddi_raw_signal_compact(drug_a_id, drug_b_id);
            CREATE INDEX idx_ddi_raw_signal_resolved ON ddi_raw_signal_compact(resolved);

            VACUUM;
            ANALYZE;
            PRAGMA optimize;
            """
        )


def artifact_stats(output: Path) -> dict[str, int]:
    with sqlite3.connect(output) as conn:
        relations = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
        }
        return {
            "known_interactions": int(conn.execute("SELECT COUNT(*) FROM known_interaction").fetchone()[0]),
            "known_interaction_effects": int(conn.execute("SELECT COUNT(*) FROM known_interaction_effect").fetchone()[0]),
            "ddi_raw_signals": (
                int(conn.execute("SELECT COUNT(*) FROM ddi_raw_signal").fetchone()[0])
                if "ddi_raw_signal" in relations
                else 0
            ),
            "import_issues": (
                int(conn.execute("SELECT COUNT(*) FROM ddi_import_issue").fetchone()[0])
                if "ddi_import_issue" in relations
                else 0
            ),
            "source_files": int(conn.execute("SELECT COUNT(*) FROM evidence_import_file").fetchone()[0]),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("data/raw/DDI"), help="Directory containing DDI CSVs.")
    parser.add_argument(
        "--normalization-db",
        type=Path,
        default=Path("data/artifacts/normalization.sqlite"),
        help="Normalization SQLite artifact path.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/artifacts/evidence.sqlite"),
        help="Output evidence SQLite path.",
    )
    parser.add_argument(
        "--compact-from",
        type=Path,
        help="Build a mobile compact artifact from an existing evidence DB instead of importing CSVs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.compact_from:
        compact_evidence_db(args.compact_from, args.output)
    else:
        build_evidence_db(args.input_dir, args.normalization_db, args.output)
    stats = artifact_stats(args.output)
    print(f"Built {args.output}")
    for key, value in stats.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
