"""
FAERS quarterly data explorer + PostgreSQL loader.

Usage:
    # Explore only (no DB writes)
    python data/faers_explorer.py --explore

    # Load all quarters into PostgreSQL
    python data/faers_explorer.py --load

    # Load specific quarters
    python data/faers_explorer.py --load --quarters 2024Q1 2024Q2

    # Explore then load
    python data/faers_explorer.py --explore --load
"""

from __future__ import annotations

import argparse
import io
import os
import re
import zipfile
from collections import Counter
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv
import psycopg
from psycopg import sql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(__file__).resolve().parent
FAERS_DIR = DATA_DIR / "raw" / "faers"

load_dotenv(PROJECT_ROOT / ".env")
POSTGRES_URI = os.getenv("POSTGRES_URI")
POSTGRES_SCHEMA = "faers"

# ── field separator used by FDA ASCII exports ──────────────────────────────
SEP = "$"

# ── table definitions: (file_prefix, table_name, columns) ─────────────────
# Columns listed here are a subset we actually care about for MedLens.
# The loader reads all columns present in the file; the schema below is
# used to create the target tables with appropriate types.
TABLE_DEFS = {
    "DEMO": {
        "table": "demo",
        "columns": {
            "primaryid": "BIGINT",
            "caseid": "BIGINT",
            "caseversion": "SMALLINT",
            "i_f_code": "CHAR(1)",          # I=initial, F=follow-up
            "event_dt": "VARCHAR(8)",
            "age": "TEXT",
            "age_cod": "TEXT",
            "age_grp": "TEXT",
            "sex": "TEXT",
            "wt": "TEXT",
            "wt_cod": "TEXT",
            "reporter_country": "TEXT",
            "occr_country": "TEXT",
            "occp_cod": "TEXT",
            "rept_cod": "TEXT",
        },
        "pk": "primaryid",
    },
    "DRUG": {
        "table": "drug",
        "columns": {
            "primaryid": "BIGINT",
            "caseid": "BIGINT",
            "drug_seq": "SMALLINT",
            "role_cod": "TEXT",              # PS/SS/C/I
            "drugname": "TEXT",
            "prod_ai": "TEXT",              # active ingredient(s)
            "route": "TEXT",
            "dose_amt": "TEXT",
            "dose_unit": "TEXT",
        },
        "pk": None,  # composite (primaryid, drug_seq)
    },
    "REAC": {
        "table": "reac",
        "columns": {
            "primaryid": "BIGINT",
            "caseid": "BIGINT",
            "pt": "TEXT",                   # MedDRA Preferred Term
            "drug_rec_act": "TEXT",
        },
        "pk": None,
    },
    "OUTC": {
        "table": "outc",
        "columns": {
            "primaryid": "BIGINT",
            "caseid": "BIGINT",
            "outc_cod": "TEXT",              # DE/LT/HO/DS/CA/RI/OT
        },
        "pk": None,
    },
    "THER": {
        "table": "ther",
        "columns": {
            "primaryid": "BIGINT",
            "caseid": "BIGINT",
            "dsg_drug_seq": "SMALLINT",
            "start_dt": "TEXT",
            "end_dt": "TEXT",
            "dur": "TEXT",
            "dur_cod": "TEXT",
        },
        "pk": None,
    },
    "INDI": {
        "table": "indi",
        "columns": {
            "primaryid": "BIGINT",
            "caseid": "BIGINT",
            "indi_drug_seq": "SMALLINT",
            "indi_pt": "TEXT",              # indication (MedDRA PT)
        },
        "pk": None,
    },
}

# Outcome severity ranking (higher = more severe) — used for training labels
OUTCOME_SEVERITY = {
    "DE": 6,   # Death
    "LT": 5,   # Life-threatening
    "HO": 4,   # Hospitalization
    "DS": 3,   # Disability
    "CA": 2,   # Congenital anomaly
    "RI": 1,   # Required intervention
    "OT": 0,   # Other
}


# ── helpers ────────────────────────────────────────────────────────────────

def list_quarters() -> list[tuple[str, Path]]:
    """Return sorted list of (quarter_label, zip_path) pairs."""
    pattern = re.compile(r"faers_ascii_(\d{4}[Qq]\d)\.zip", re.IGNORECASE)
    result = []
    for p in sorted(FAERS_DIR.glob("*.zip")):
        m = pattern.match(p.name)
        if m:
            label = m.group(1).upper()
            result.append((label, p))
    return result


def iter_zip_file(zip_path: Path, prefix: str) -> tuple[list[str], list[list[str]]]:
    """
    Open a FAERS zip and return (header_cols, rows) for the given file prefix
    (e.g. 'DRUG', 'DEMO').  Handles case-insensitive filenames.
    """
    with zipfile.ZipFile(zip_path) as zf:
        # find matching member
        target = next(
            (n for n in zf.namelist()
             if re.search(rf"/{prefix}\d{{2}}Q\d\.txt$", n, re.IGNORECASE)),
            None,
        )
        if target is None:
            return [], []
        with zf.open(target) as f:
            text = io.TextIOWrapper(f, encoding="latin-1")
            header = text.readline().rstrip("\n").split(SEP)
            rows = [line.rstrip("\n").split(SEP) for line in text if line.strip()]
    return header, rows


# ── exploration ────────────────────────────────────────────────────────────

def explore(quarters: list[tuple[str, Path]]) -> None:
    print(f"\n{'='*60}")
    print(f"FAERS data directory: {FAERS_DIR}")
    print(f"Quarters found: {len(quarters)}")
    print(f"Range: {quarters[0][0]} → {quarters[-1][0]}")
    print(f"{'='*60}\n")

    # Use most recent quarter for detailed stats
    label, zip_path = quarters[-1]
    print(f"Detailed sample from {label}:\n")

    row_counts = {}
    for prefix, defn in TABLE_DEFS.items():
        header, rows = iter_zip_file(zip_path, prefix)
        row_counts[prefix] = len(rows)
        print(f"  {prefix:6s} ({defn['table']:8s}): {len(rows):>10,} rows  |  cols: {', '.join(header[:6])}{'…' if len(header)>6 else ''}")

    # role_cod breakdown from DRUG
    print()
    header, rows = iter_zip_file(zip_path, "DRUG")
    if header:
        ri = header.index("role_cod")
        role_dist = Counter(r[ri] for r in rows if len(r) > ri)
        print("  role_cod breakdown (PS=primary suspect, SS=secondary, C=concomitant, I=interacting):")
        for code, cnt in sorted(role_dist.items(), key=lambda x: -x[1]):
            print(f"    {code}: {cnt:>10,}")

    # outc_cod breakdown
    print()
    header, rows = iter_zip_file(zip_path, "OUTC")
    if header:
        oi = header.index("outc_cod")
        outc_dist = Counter(r[oi] for r in rows if len(r) > oi)
        print("  outc_cod breakdown (DE=death, LT=life-threat, HO=hosp, DS=disability, RI=req-interv, OT=other):")
        for code, cnt in sorted(outc_dist.items(), key=lambda x: -x[1]):
            sev = OUTCOME_SEVERITY.get(code, -1)
            print(f"    {code} (sev={sev}): {cnt:>10,}")

    # polypharmacy rate: cases with >=2 suspect drugs
    print()
    header, rows = iter_zip_file(zip_path, "DRUG")
    if header:
        pi = header.index("primaryid")
        ri = header.index("role_cod")
        from collections import defaultdict
        case_suspects: dict[str, set[str]] = defaultdict(set)
        di = header.index("drugname")
        for row in rows:
            if len(row) > max(pi, ri, di) and row[ri] in ("PS", "SS"):
                case_suspects[row[pi]].add(row[di].upper())
        poly = sum(1 for drugs in case_suspects.values() if len(drugs) >= 2)
        total = len(case_suspects)
        print(f"  Polypharmacy (>=2 suspect drugs): {poly:,} / {total:,} cases ({poly/total*100:.1f}%)")

    # Estimate totals across all quarters
    print(f"\n{'='*60}")
    print(f"Estimated total rows across all {len(quarters)} quarters")
    print(f"(based on {label} counts × {len(quarters)} quarters):")
    for prefix, cnt in row_counts.items():
        est = cnt * len(quarters)
        print(f"  {prefix:6s}: ~{est:>12,}")

    print(f"\n{'='*60}")
    print("MedLens relevance summary:")
    print("""
  DRUG.drugname / prod_ai  → normalize brand→generic, identify suspect drugs
  DRUG.role_cod (PS/SS)    → filter to drugs causing the event
  REAC.pt                  → adverse reaction (MedDRA PT) per drug combo
  OUTC.outc_cod            → severity label (DE/LT/HO…) for training
  THER.start_dt/end_dt     → confirm simultaneous drug use (polypharmacy)
  INDI.indi_pt             → indication — helps distinguish signal from noise

  Key training signal: cases where role_cod IN (PS,SS), >=2 drugs,
  joined to OUTC for severity → multi-drug interaction training examples.
""")


# ── loader ─────────────────────────────────────────────────────────────────

DDL_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {schema}.{table} (
{col_defs}
);
"""

def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                sql.Identifier(POSTGRES_SCHEMA)
            )
        )
    conn.commit()


def create_tables(conn) -> None:
    with conn.cursor() as cur:
        for prefix, defn in TABLE_DEFS.items():
            table_fqn = f"{POSTGRES_SCHEMA}.{defn['table']}"
            col_defs = ",\n".join(
                f"    {col} {dtype}" for col, dtype in defn["columns"].items()
            )
            cur.execute(f"DROP TABLE IF EXISTS {table_fqn} CASCADE;")
            cur.execute(f"CREATE TABLE {table_fqn} (\n{col_defs}\n);")
    conn.commit()
    print(f"Tables created in schema '{POSTGRES_SCHEMA}'.")


def load_quarter(conn, label: str, zip_path: Path, dry_run: bool = False) -> dict[str, int]:
    """Load one quarter's data into PostgreSQL. Returns row counts per table."""
    counts = {}
    with conn.cursor() as cur:
        for prefix, defn in TABLE_DEFS.items():
            header, rows = iter_zip_file(zip_path, prefix)
            if not header:
                print(f"  [{label}] {prefix}: file not found in zip, skipping.")
                continue

            target_cols = list(defn["columns"].keys())
            # map target col → index in file header (case-insensitive)
            header_lower = [h.lower() for h in header]
            col_indices = []
            missing = []
            for col in target_cols:
                try:
                    col_indices.append(header_lower.index(col.lower()))
                except ValueError:
                    missing.append(col)
                    col_indices.append(None)

            if missing:
                print(f"  [{label}] {prefix}: missing cols {missing} — will insert NULL")

            table_fqn = f"{POSTGRES_SCHEMA}.{defn['table']}"
            insert_cols = sql.SQL(", ").join(map(sql.Identifier, target_cols))
            placeholders = sql.SQL(", ").join(sql.Placeholder() * len(target_cols))
            insert_sql = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING").format(
                sql.SQL(table_fqn),
                insert_cols,
                placeholders,
            )

            batch = []
            for row in rows:
                vals = []
                for idx in col_indices:
                    if idx is None or idx >= len(row):
                        vals.append(None)
                    else:
                        v = row[idx].strip()
                        vals.append(v if v else None)
                batch.append(vals)

            if not dry_run:
                cur.executemany(insert_sql, batch)

            counts[prefix] = len(batch)
            print(f"  [{label}] {prefix}: {len(batch):>8,} rows {'(dry-run)' if dry_run else 'inserted'}")

    if not dry_run:
        conn.commit()
    return counts


def load(quarters: list[tuple[str, Path]], filter_labels: list[str] | None = None) -> None:
    if not POSTGRES_URI:
        raise RuntimeError("POSTGRES_URI not set in .env")

    if filter_labels:
        quarters = [(lbl, p) for lbl, p in quarters if lbl in filter_labels]
        if not quarters:
            raise ValueError(f"No matching quarters found for: {filter_labels}")

    print(f"\nLoading {len(quarters)} quarter(s) into schema '{POSTGRES_SCHEMA}'...")

    with closing(psycopg.connect(POSTGRES_URI)) as conn:
        ensure_schema(conn)
        create_tables(conn)
        total: dict[str, int] = {}
        for label, zip_path in quarters:
            print(f"\n-- {label} ({zip_path.name}) --")
            counts = load_quarter(conn, label, zip_path)
            for k, v in counts.items():
                total[k] = total.get(k, 0) + v

    print(f"\n{'='*60}")
    print("Load complete. Total rows inserted:")
    for prefix, cnt in total.items():
        print(f"  {prefix}: {cnt:,}")


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="FAERS quarterly data explorer + loader")
    parser.add_argument("--explore", action="store_true", help="Print exploration summary")
    parser.add_argument("--load", action="store_true", help="Load data into PostgreSQL")
    parser.add_argument(
        "--quarters",
        nargs="+",
        metavar="QUARTER",
        help="Specific quarters to load, e.g. 2024Q1 2024Q2 (default: all)",
    )
    args = parser.parse_args()

    if not args.explore and not args.load:
        parser.print_help()
        return

    quarters = list_quarters()
    if not quarters:
        raise RuntimeError(f"No FAERS zip files found in {FAERS_DIR}")

    if args.explore:
        explore(quarters)

    if args.load:
        filter_labels = [q.upper() for q in args.quarters] if args.quarters else None
        load(quarters, filter_labels=filter_labels)


if __name__ == "__main__":
    main()
