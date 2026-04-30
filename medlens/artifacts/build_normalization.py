"""Build the MedLens normalization SQLite artifact."""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

from medlens.artifacts.common_meds import COMMON_MED_SEEDS, DrugSeed
from medlens.artifacts.schema import NORMALIZATION_SCHEMA

NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


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


def build_normalization_db(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        create_schema(conn)
        for seed in COMMON_MED_SEEDS:
            insert_seed(conn, seed)
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_normalization_db(args.output)
    drug_count, alias_count = artifact_stats(args.output)
    print(f"Built {args.output}")
    print(f"Drugs: {drug_count}")
    print(f"Aliases: {alias_count}")


if __name__ == "__main__":
    main()
