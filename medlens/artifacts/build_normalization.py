"""Build the MedLens normalization SQLite artifact."""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
from pathlib import Path

from medlens.artifacts.common_meds import COMMON_MED_SEEDS, DrugSeed
from medlens.artifacts.schema import NORMALIZATION_SCHEMA

NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
COMMON_MEDICINES_INDIA_CSV = Path("data/raw/DDI/common_medicines_india_dataset_5000.csv")
INDIA_BRAND_INGREDIENT_MAP_CSV = Path("data/raw/DDI/india_common_brand_ingredient_map.csv")
SALT_SUFFIXES = (
    "hydrochloride",
    "sulfate",
    "sulphate",
    "phosphate",
    "acetate",
    "tartrate",
    "bitartrate",
    "bromide",
    "fumarate",
    "maleate",
    "mesylate",
    "medoxomil",
    "palmitate",
    "sodium",
    "potassium",
    "calcium",
)


def normalize_lookup_text(value: str) -> str:
    """Normalize OCR/user text for exact alias lookup."""
    value = value.casefold().strip()
    value = NON_ALNUM_RE.sub(" ", value)
    return " ".join(value.split())


def iter_aliases(seed: DrugSeed) -> tuple[tuple[str, str, str], ...]:
    aliases: list[tuple[str, str, str]] = [(seed.canonical_name, "canonical", "global")]
    aliases.extend((alias, "alias", seed.region_scope) for alias in seed.aliases)
    return tuple(aliases)


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(NORMALIZATION_SCHEMA)


def insert_seed(conn: sqlite3.Connection, seed: DrugSeed) -> None:
    cur = conn.execute(
        """
        INSERT INTO drug (canonical_name, category, region_scope, is_common)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(canonical_name) DO UPDATE SET
            category = excluded.category,
            region_scope = excluded.region_scope,
            is_common = excluded.is_common
        RETURNING id
        """,
        (seed.canonical_name, seed.category, seed.region_scope),
    )
    drug_id = int(cur.fetchone()[0])

    for alias, alias_type, region in iter_aliases(seed):
        normalized = normalize_lookup_text(alias)
        conn.execute(
            """
            INSERT INTO drug_alias (drug_id, alias, normalized_alias, alias_type, region)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(normalized_alias) DO NOTHING
            """,
            (drug_id, alias, normalized, alias_type, region),
        )


def import_india_common_medicines(conn: sqlite3.Connection, csv_path: Path) -> int:
    """Import India common-medicine names/brands for OCR and user-name recovery."""
    if not csv_path.exists():
        return 0

    imported = 0
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row_number, row in enumerate(reader, start=2):
            generic_name = _row_field(row, "generic_or_common_name")
            if not generic_name:
                continue

            generic_aliases = _generic_aliases(generic_name)
            brand_aliases = _brand_aliases(_row_field(row, "common_brand_examples_india"))
            brand_aliases = (
                *brand_aliases,
                *_brand_strength_aliases(
                    brand_aliases,
                    _row_field(row, "composition_or_strength_pattern"),
                ),
            )
            drug_id = _resolve_existing_drug_id(conn, (*generic_aliases, *brand_aliases))
            if drug_id is None:
                canonical_name = normalize_lookup_text(generic_name)
                drug_id = _upsert_drug(
                    conn,
                    canonical_name=canonical_name,
                    category=_category_from_text(_row_field(row, "therapeutic_category")),
                    region_scope="india_common_medicine",
                )

            for alias in generic_aliases:
                _insert_alias(conn, drug_id, alias, "common_generic", "india")
            for alias in brand_aliases:
                _insert_alias(conn, drug_id, alias, "brand", "india")

            conn.execute(
                """
                INSERT INTO india_common_medicine (
                    medicine_id, drug_id, source_row_number, generic_or_common_name,
                    normalized_generic_name, composition_or_strength_pattern, dosage_form,
                    therapeutic_category, common_daily_life_use_india, common_brand_examples_india,
                    availability_context_india, otc_or_rx, nlem_or_jan_aushadhi_presence,
                    india_relevance, patient_risk_flags_india, source_basis, source_urls, dataset_note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(medicine_id) DO UPDATE SET
                    drug_id = excluded.drug_id,
                    source_row_number = excluded.source_row_number,
                    generic_or_common_name = excluded.generic_or_common_name,
                    normalized_generic_name = excluded.normalized_generic_name,
                    composition_or_strength_pattern = excluded.composition_or_strength_pattern,
                    dosage_form = excluded.dosage_form,
                    therapeutic_category = excluded.therapeutic_category,
                    common_daily_life_use_india = excluded.common_daily_life_use_india,
                    common_brand_examples_india = excluded.common_brand_examples_india,
                    availability_context_india = excluded.availability_context_india,
                    otc_or_rx = excluded.otc_or_rx,
                    nlem_or_jan_aushadhi_presence = excluded.nlem_or_jan_aushadhi_presence,
                    india_relevance = excluded.india_relevance,
                    patient_risk_flags_india = excluded.patient_risk_flags_india,
                    source_basis = excluded.source_basis,
                    source_urls = excluded.source_urls,
                    dataset_note = excluded.dataset_note
                """,
                (
                    _row_field(row, "medicine_id"),
                    drug_id,
                    row_number,
                    generic_name,
                    normalize_lookup_text(generic_name),
                    _row_field(row, "composition_or_strength_pattern"),
                    _row_field(row, "dosage_form"),
                    _row_field(row, "therapeutic_category"),
                    _row_field(row, "common_daily_life_use_india"),
                    _row_field(row, "common_brand_examples_india"),
                    _row_field(row, "availability_context_india"),
                    _row_field(row, "otc_or_rx"),
                    _row_field(row, "nlem_or_jan_aushadhi_presence"),
                    _row_field(row, "india_relevance"),
                    _row_field(row, "patient_risk_flags_india"),
                    _row_field(row, "source_basis"),
                    _row_field(row, "source_urls"),
                    _row_field(row, "dataset_note"),
                ),
            )
            imported += 1
    return imported


def import_brand_ingredient_map(conn: sqlite3.Connection, csv_path: Path) -> int:
    """Import curated brand-to-active-ingredient mappings for combination products."""
    if not csv_path.exists():
        return 0

    imported = 0
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            brand_name = _row_field(row, "brand_name")
            ingredients = tuple(
                item.strip()
                for item in _row_field(row, "active_ingredients").split("|")
                if normalize_lookup_text(item)
            )
            if not brand_name or not ingredients:
                continue

            strengths = tuple(item.strip() for item in _row_field(row, "strengths").split("|"))
            normalized_brand = normalize_lookup_text(brand_name)
            region = _row_field(row, "region") or "india"
            source_basis = _row_field(row, "source_basis")
            source_urls = _row_field(row, "source_urls")

            for index, ingredient in enumerate(ingredients):
                drug_id = _resolve_existing_drug_id(conn, (ingredient,))
                if drug_id is None:
                    drug_id = _upsert_drug(
                        conn,
                        canonical_name=normalize_lookup_text(ingredient),
                        category="combination_product_ingredient",
                        region_scope=region,
                    )
                    _insert_alias(conn, drug_id, ingredient, "canonical", region)

                conn.execute(
                    """
                    INSERT INTO medicine_ingredient_map (
                        brand_name, normalized_brand_name, ingredient_drug_id,
                        ingredient_order, strength, region, source_basis, source_urls
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(normalized_brand_name, ingredient_drug_id) DO UPDATE SET
                        brand_name = excluded.brand_name,
                        ingredient_order = excluded.ingredient_order,
                        strength = excluded.strength,
                        region = excluded.region,
                        source_basis = excluded.source_basis,
                        source_urls = excluded.source_urls
                    """,
                    (
                        brand_name,
                        normalized_brand,
                        drug_id,
                        index,
                        strengths[index] if index < len(strengths) else "",
                        region,
                        source_basis,
                        source_urls,
                    ),
                )
            imported += 1
    return imported


def _upsert_drug(conn: sqlite3.Connection, canonical_name: str, category: str, region_scope: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO drug (canonical_name, category, region_scope, is_common)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(canonical_name) DO UPDATE SET
            category = excluded.category,
            region_scope = excluded.region_scope,
            is_common = excluded.is_common
        RETURNING id
        """,
        (canonical_name, category, region_scope),
    )
    return int(cur.fetchone()[0])


def _insert_alias(conn: sqlite3.Connection, drug_id: int, alias: str, alias_type: str, region: str) -> None:
    normalized = normalize_lookup_text(alias)
    if not normalized:
        return
    conn.execute(
        """
        INSERT INTO drug_alias (drug_id, alias, normalized_alias, alias_type, region)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(normalized_alias) DO NOTHING
        """,
        (drug_id, alias.strip(), normalized, alias_type, region),
    )


def _resolve_existing_drug_id(conn: sqlite3.Connection, aliases: tuple[str, ...]) -> int | None:
    for alias in aliases:
        normalized = normalize_lookup_text(alias)
        if not normalized:
            continue
        row = conn.execute("SELECT drug_id FROM drug_alias WHERE normalized_alias = ?", (normalized,)).fetchone()
        if row is not None:
            return int(row[0])
    return None


def _generic_aliases(generic_name: str) -> tuple[str, ...]:
    aliases: list[str] = [generic_name.strip()]
    if " / " in generic_name:
        aliases.extend(part.strip() for part in generic_name.split(" / ") if part.strip())
    if "+" in generic_name:
        aliases.append(generic_name.replace("+", " "))
    if "/" in generic_name:
        aliases.append(generic_name.replace("/", " "))
    aliases.extend(_slash_suffix_aliases(generic_name))

    for alias in tuple(aliases):
        aliases.extend(_salt_trimmed_aliases(alias))

    return tuple(dict.fromkeys(alias for alias in aliases if normalize_lookup_text(alias)))


def _slash_suffix_aliases(value: str) -> tuple[str, ...]:
    match = re.match(r"^(.+\s)([A-Za-z0-9]+)/(?:([A-Za-z0-9]+))$", value.strip())
    if not match:
        return ()
    prefix, first_suffix, second_suffix = match.groups()
    return (f"{prefix}{first_suffix}", f"{prefix}{second_suffix}")


def _salt_trimmed_aliases(value: str) -> tuple[str, ...]:
    normalized = normalize_lookup_text(value)
    aliases: list[str] = []
    for suffix in SALT_SUFFIXES:
        ending = f" {suffix}"
        if normalized.endswith(ending):
            aliases.append(normalized[: -len(ending)])
    return tuple(aliases)


def _brand_aliases(value: str) -> tuple[str, ...]:
    aliases: list[str] = []
    for raw_part in re.split(r"[,;]", value):
        part = raw_part.strip()
        if not part:
            continue
        lowered = part.casefold()
        if "component" in lowered:
            continue
        part = re.sub(r"\s+variants?$", "", part, flags=re.IGNORECASE).strip()
        part = re.sub(r"\s+as applicable$", "", part, flags=re.IGNORECASE).strip()
        if normalize_lookup_text(part):
            aliases.append(part)
    return tuple(dict.fromkeys(aliases))


def _brand_strength_aliases(brand_aliases: tuple[str, ...], composition: str) -> tuple[str, ...]:
    strengths = tuple(
        dict.fromkeys(
            match.group(1).lstrip("0") or "0"
            for match in re.finditer(
                r"\b(\d+(?:\.\d+)?)\s*(?:mg|mcg|g|ml|iu)\b",
                composition,
                flags=re.IGNORECASE,
            )
        )
    )
    if not strengths:
        return ()

    aliases: list[str] = []
    for brand in brand_aliases:
        normalized_brand = normalize_lookup_text(brand)
        if not normalized_brand:
            continue
        for strength in strengths[:4]:
            aliases.append(f"{brand} {strength}")
            aliases.append(f"{brand}{strength}")
    return tuple(dict.fromkeys(aliases))


def _category_from_text(value: str) -> str:
    normalized = normalize_lookup_text(value)
    return normalized.replace(" ", "_") or "india_common_medicine"


def _row_field(row: dict[str, str], column: str) -> str:
    return (row.get(column) or "").strip()


def build_normalization_db(
    output: Path,
    common_medicines_csv: Path | None = COMMON_MEDICINES_INDIA_CSV,
    brand_ingredient_map_csv: Path | None = INDIA_BRAND_INGREDIENT_MAP_CSV,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        create_schema(conn)
        for seed in COMMON_MED_SEEDS:
            insert_seed(conn, seed)
        if common_medicines_csv is not None:
            import_india_common_medicines(conn, common_medicines_csv)
        if brand_ingredient_map_csv is not None:
            import_brand_ingredient_map(conn, brand_ingredient_map_csv)
        conn.commit()
        conn.execute("PRAGMA optimize")


def artifact_stats(output: Path) -> tuple[int, int]:
    with sqlite3.connect(output) as conn:
        drug_count = conn.execute("SELECT COUNT(*) FROM drug").fetchone()[0]
        alias_count = conn.execute("SELECT COUNT(*) FROM drug_alias").fetchone()[0]
    return int(drug_count), int(alias_count)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/artifacts/normalization.sqlite"),
        help="Output SQLite path.",
    )
    parser.add_argument(
        "--common-medicines-csv",
        type=Path,
        default=COMMON_MEDICINES_INDIA_CSV,
        help="Optional India common medicines CSV to import for OCR/user-name recovery.",
    )
    parser.add_argument(
        "--brand-ingredient-map-csv",
        type=Path,
        default=INDIA_BRAND_INGREDIENT_MAP_CSV,
        help="Optional curated brand-to-active-ingredient CSV for combination products.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_normalization_db(args.output, args.common_medicines_csv, args.brand_ingredient_map_csv)
    drug_count, alias_count = artifact_stats(args.output)
    print(f"Built {args.output}")
    print(f"Drugs: {drug_count}")
    print(f"Aliases: {alias_count}")


if __name__ == "__main__":
    main()
