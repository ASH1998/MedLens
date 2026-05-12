from __future__ import annotations

import csv
from pathlib import Path

from medlens.artifacts.build_evidence import build_evidence_db
from medlens.artifacts.build_normalization import build_normalization_db
from medlens.tools.local_safety import MedicationSafetyStore


def build_fixture_store(root: Path) -> MedicationSafetyStore:
    normalization_db = root / "normalization.sqlite"
    evidence_db = root / "evidence.sqlite"
    ddi_dir = root / "DDI"
    ddi_dir.mkdir()
    common_csv = root / "common_medicines_india_dataset_5000.csv"
    write_common_medicines_fixture(common_csv)
    build_normalization_db(normalization_db, common_medicines_csv=common_csv)
    write_usa_ddi_fixture(ddi_dir / "usa_prioritized_ddi_ade_signals.csv")
    build_evidence_db(ddi_dir, normalization_db, evidence_db)
    return MedicationSafetyStore(normalization_db, evidence_db)


def write_common_medicines_fixture(path: Path) -> None:
    fieldnames = [
        "medicine_id",
        "generic_or_common_name",
        "composition_or_strength_pattern",
        "dosage_form",
        "therapeutic_category",
        "common_daily_life_use_india",
        "common_brand_examples_india",
        "availability_context_india",
        "otc_or_rx",
        "nlem_or_jan_aushadhi_presence",
        "india_relevance",
        "patient_risk_flags_india",
        "source_basis",
        "source_urls",
        "dataset_note",
    ]
    rows = [
        {
            "medicine_id": "MEDIND-1",
            "generic_or_common_name": "Paracetamol / Acetaminophen",
            "composition_or_strength_pattern": "500 mg, 650 mg tablet",
            "dosage_form": "Tablet",
            "therapeutic_category": "Analgesic / antipyretic",
            "common_daily_life_use_india": "Fever, headache, body ache",
            "common_brand_examples_india": "Dolo, Calpol",
            "availability_context_india": "Common household medicine",
            "otc_or_rx": "OTC/Rx depending context",
            "nlem_or_jan_aushadhi_presence": "NLEM",
            "india_relevance": "High",
            "patient_risk_flags_india": "Liver disease; duplicate paracetamol",
            "source_basis": "fixture",
            "source_urls": "https://example.test/paracetamol",
            "dataset_note": "fixture",
        }
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_usa_ddi_fixture(path: Path) -> None:
    fieldnames = [
        "interaction_id",
        "drug1",
        "drug2",
        "adverse_effect",
        "severity",
        "interaction_category",
        "mechanism_or_rationale",
        "interaction_direction",
        "us_population_relevance",
        "patient_risk_flags_us",
        "evidence_basis",
        "source_basis",
        "source_urls",
        "dataset_type",
        "use_case_note",
    ]
    rows = [
        {
            "interaction_id": "x1",
            "drug1": "warfarin",
            "drug2": "ibuprofen",
            "adverse_effect": "gastrointestinal bleeding",
            "severity": "high",
            "mechanism_or_rationale": "additive bleeding",
            "patient_risk_flags_us": "elderly",
            "evidence_basis": "class interaction",
            "source_basis": "DailyMed",
            "source_urls": "https://example.test/a",
            "dataset_type": "screening/reference",
            "use_case_note": "screening only",
        },
        {
            "interaction_id": "x2",
            "drug1": "ibuprofen",
            "drug2": "warfarin",
            "adverse_effect": "hematuria",
            "severity": "high",
            "mechanism_or_rationale": "additive bleeding",
            "patient_risk_flags_us": "ckd",
            "evidence_basis": "class interaction",
            "source_basis": "DailyMed",
            "source_urls": "https://example.test/a",
            "dataset_type": "screening/reference",
            "use_case_note": "screening only",
        },
        {
            "interaction_id": "x3",
            "drug1": "acetaminophen",
            "drug2": "warfarin",
            "adverse_effect": "inr variability",
            "severity": "medium",
            "mechanism_or_rationale": "monitoring signal",
            "patient_risk_flags_us": "high dose acetaminophen",
            "evidence_basis": "screening signal",
            "source_basis": "DailyMed",
            "source_urls": "https://example.test/c",
            "dataset_type": "screening/reference",
            "use_case_note": "screening only",
        },
        {
            "interaction_id": "x4",
            "drug1": "captopril",
            "drug2": "ibuprofen",
            "adverse_effect": "acute kidney injury",
            "severity": "high",
            "mechanism_or_rationale": "renal perfusion risk",
            "patient_risk_flags_us": "ckd",
            "evidence_basis": "class interaction",
            "source_basis": "DailyMed",
            "source_urls": "https://example.test/captopril-ibuprofen",
            "dataset_type": "screening/reference",
            "use_case_note": "screening only",
        },
        {
            "interaction_id": "x5",
            "drug1": "captopril",
            "drug2": "potassium chloride",
            "adverse_effect": "hyperkalemia",
            "severity": "medium",
            "mechanism_or_rationale": "additive potassium increase",
            "patient_risk_flags_us": "ckd",
            "evidence_basis": "class interaction",
            "source_basis": "DailyMed",
            "source_urls": "https://example.test/captopril-potassium",
            "dataset_type": "screening/reference",
            "use_case_note": "screening only",
        },
        {
            "interaction_id": "x6",
            "drug1": "unknown drug",
            "drug2": "warfarin",
            "adverse_effect": "bleeding",
            "severity": "high",
            "mechanism_or_rationale": "unresolved fixture",
            "patient_risk_flags_us": "elderly",
            "evidence_basis": "fixture",
            "source_basis": "fixture",
            "source_urls": "https://example.test/unresolved",
            "dataset_type": "screening/reference",
            "use_case_note": "screening only",
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
