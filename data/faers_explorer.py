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
import logging
import os
import re
import time
import zipfile
from collections import Counter
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv
import psycopg
from psycopg import sql

log = logging.getLogger("faers_loader")

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
        "unique_cols": ["primaryid"],
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
        "unique_cols": ["primaryid", "drug_seq"],
    },
    "REAC": {
        "table": "reac",
        "columns": {
            "primaryid": "BIGINT",
            "caseid": "BIGINT",
            "pt": "TEXT",                   # MedDRA Preferred Term
            "drug_rec_act": "TEXT",
        },
        "unique_cols": ["primaryid", "pt"],
    },
    "OUTC": {
        "table": "outc",
        "columns": {
            "primaryid": "BIGINT",
            "caseid": "BIGINT",
            "outc_cod": "TEXT",              # DE/LT/HO/DS/CA/RI/OT
        },
        "unique_cols": ["primaryid", "outc_cod"],
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
        "unique_cols": ["primaryid", "dsg_drug_seq"],
    },
    "INDI": {
        "table": "indi",
        "columns": {
            "primaryid": "BIGINT",
            "caseid": "BIGINT",
            "indi_drug_seq": "SMALLINT",
            "indi_pt": "TEXT",              # indication (MedDRA PT)
        },
        "unique_cols": ["primaryid", "indi_drug_seq"],
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


def _find_zip_member(zip_path: Path, prefix: str) -> str | None:
    """Find matching member name in zip for given table prefix."""
    with zipfile.ZipFile(zip_path) as zf:
        return next(
            (n for n in zf.namelist()
             if re.search(rf"/{prefix}\d{{2}}Q\d\.txt$", n, re.IGNORECASE)),
            None,
        )


def _stream_zip_rows(zip_path: Path, prefix: str):
    """
    Generator: yield header first, then each data row as split list.
    Streams rows one at a time — no full list in memory.
    Keeps zip/file handles open for duration of iteration.
    """
    target = _find_zip_member(zip_path, prefix)
    if target is None:
        return
    zf = zipfile.ZipFile(zip_path)
    try:
        f = zf.open(target)
        try:
            text = io.TextIOWrapper(f, encoding="latin-1")
            header = text.readline().rstrip("\n").split(SEP)
            yield header
            for line in text:
                stripped = line.rstrip("\n")
                if stripped.strip():
                    yield stripped.split(SEP)
        finally:
            f.close()
    finally:
        zf.close()


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

    # Cache file data to avoid re-reading same zip entries
    file_cache: dict[str, tuple[list[str], list[list[str]]]] = {}
    row_counts = {}
    for prefix, defn in TABLE_DEFS.items():
        header, rows = iter_zip_file(zip_path, prefix)
        file_cache[prefix] = (header, rows)
        row_counts[prefix] = len(rows)
        print(f"  {prefix:6s} ({defn['table']:8s}): {len(rows):>10,} rows  |  cols: {', '.join(header[:6])}{'…' if len(header)>6 else ''}")

    # role_cod breakdown from DRUG
    print()
    header, rows = file_cache["DRUG"]
    if header:
        ri = header.index("role_cod")
        role_dist = Counter(r[ri] for r in rows if len(r) > ri)
        print("  role_cod breakdown (PS=primary suspect, SS=secondary, C=concomitant, I=interacting):")
        for code, cnt in sorted(role_dist.items(), key=lambda x: -x[1]):
            print(f"    {code}: {cnt:>10,}")

    # outc_cod breakdown
    print()
    header, rows = file_cache["OUTC"]
    if header:
        oi = header.index("outc_cod")
        outc_dist = Counter(r[oi] for r in rows if len(r) > oi)
        print("  outc_cod breakdown (DE=death, LT=life-threat, HO=hosp, DS=disability, RI=req-interv, OT=other):")
        for code, cnt in sorted(outc_dist.items(), key=lambda x: -x[1]):
            sev = OUTCOME_SEVERITY.get(code, -1)
            print(f"    {code} (sev={sev}): {cnt:>10,}")

    # polypharmacy rate: cases with >=2 suspect drugs
    print()
    header, rows = file_cache["DRUG"]
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


def _constraint_name(table: str) -> str:
    return f"uq_{table}_key"


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                sql.Identifier(POSTGRES_SCHEMA)
            )
        )
    conn.commit()
    log.info("Schema '%s' ready.", POSTGRES_SCHEMA)


def ensure_tables(conn) -> None:
    """Create tables + unique constraints if they don't exist. Idempotent."""
    with conn.cursor() as cur:
        for prefix, defn in TABLE_DEFS.items():
            tbl = defn["table"]
            table_fqn = f"{POSTGRES_SCHEMA}.{tbl}"
            uq_name = _constraint_name(tbl)
            uq_cols = defn["unique_cols"]

            # Check if table exists
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = %s",
                (POSTGRES_SCHEMA, tbl),
            )
            table_exists = cur.fetchone() is not None

            if not table_exists:
                col_defs = ",\n".join(
                    f"    {col} {dtype}" for col, dtype in defn["columns"].items()
                )
                uq_clause = f",\n    CONSTRAINT {uq_name} UNIQUE ({', '.join(uq_cols)})"
                cur.execute(f"CREATE TABLE {table_fqn} (\n{col_defs}{uq_clause}\n);")
                log.info("[%s] Created table %s with UNIQUE(%s).", prefix, table_fqn, ", ".join(uq_cols))
            else:
                # Table exists — check if unique constraint present
                cur.execute(
                    "SELECT 1 FROM information_schema.table_constraints "
                    "WHERE table_schema = %s AND table_name = %s "
                    "AND constraint_name = %s AND constraint_type = 'UNIQUE'",
                    (POSTGRES_SCHEMA, tbl, uq_name),
                )
                has_uq = cur.fetchone() is not None
                if has_uq:
                    log.info("[%s] Table %s exists, UNIQUE(%s) already present — skip.", prefix, table_fqn, ", ".join(uq_cols))
                else:
                    # No unique constraint — drop and recreate with constraint.
                    # Faster than deduping millions of rows; data will be reloaded.
                    uq_col_list = ", ".join(uq_cols)
                    col_defs = ",\n".join(
                        f"    {col} {dtype}" for col, dtype in defn["columns"].items()
                    )
                    uq_clause = f",\n    CONSTRAINT {uq_name} UNIQUE ({uq_col_list})"
                    cur.execute(f"DROP TABLE {table_fqn} CASCADE;")
                    cur.execute(f"CREATE TABLE {table_fqn} (\n{col_defs}{uq_clause}\n);")
                    log.info("[%s] Recreated %s with UNIQUE(%s) (old data had no constraint).", prefix, table_fqn, ", ".join(uq_cols))

    conn.commit()


def ensure_indexes(conn) -> None:
    """Create indexes on primaryid for all FAERS tables. Idempotent."""
    with conn.cursor() as cur:
        for defn in TABLE_DEFS.values():
            table_fqn = f"{POSTGRES_SCHEMA}.{defn['table']}"
            idx_name = f"idx_{defn['table']}_primaryid"
            cur.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table_fqn} (primaryid);")
            log.info("Index %s ready.", idx_name)
    conn.commit()


def load_quarter(conn, label: str, zip_path: Path, dry_run: bool = False) -> dict[str, int]:
    """
    Load one quarter's data via staging table approach:
      1. COPY raw rows → temp staging table (no constraints, max speed)
      2. INSERT INTO real table ... ON CONFLICT DO NOTHING (dedup)
      3. DROP staging table
    Returns {prefix: inserted_count}.
    """
    counts = {}
    quarter_t0 = time.perf_counter()

    for prefix, defn in TABLE_DEFS.items():
        t0 = time.perf_counter()
        stream = _stream_zip_rows(zip_path, prefix)
        header = next(stream, None)
        if header is None:
            log.warning("[%s] %s: file not found in zip, skipping.", label, prefix)
            continue

        target_cols = list(defn["columns"].keys())
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
            log.warning("[%s] %s: missing cols %s — will insert NULL.", label, prefix, missing)

        tbl = defn["table"]
        table_fqn = f"{POSTGRES_SCHEMA}.{tbl}"
        staging_fqn = f"{POSTGRES_SCHEMA}._staging_{tbl}"
        uq_cols = defn["unique_cols"]
        col_list = ", ".join(target_cols)
        raw_count = 0

        if dry_run:
            for _ in stream:
                raw_count += 1
            counts[prefix] = raw_count
            log.info("[%s] %s: %s rows (dry-run).", label, prefix, f"{raw_count:,}")
            continue

        with conn.cursor() as cur:
            # 1. Create temp staging table (same columns, no constraints)
            col_defs = ",\n".join(
                f"    {col} {dtype}" for col, dtype in defn["columns"].items()
            )
            cur.execute(f"DROP TABLE IF EXISTS {staging_fqn};")
            cur.execute(f"CREATE UNLOGGED TABLE {staging_fqn} (\n{col_defs}\n);")

            # 2. COPY raw data into staging — fast, no constraint checks
            with cur.copy(f"COPY {staging_fqn} ({col_list}) FROM STDIN") as copy:
                for row in stream:
                    vals = []
                    for idx in col_indices:
                        if idx is None or idx >= len(row):
                            vals.append(None)
                        else:
                            v = row[idx].strip()
                            vals.append(v if v else None)
                    copy.write_row(vals)
                    raw_count += 1

            copy_elapsed = time.perf_counter() - t0

            # 3. INSERT from staging → real table, skip duplicates
            conflict_cols = ", ".join(uq_cols)
            cur.execute(
                f"INSERT INTO {table_fqn} ({col_list}) "
                f"SELECT {col_list} FROM {staging_fqn} "
                f"ON CONFLICT ({conflict_cols}) DO NOTHING;"
            )
            inserted = cur.rowcount

            # 4. Cleanup staging
            cur.execute(f"DROP TABLE {staging_fqn};")

        conn.commit()
        elapsed = time.perf_counter() - t0
        skipped = raw_count - inserted

        counts[prefix] = inserted
        log.info(
            "[%s] %s: %s raw → %s inserted, %s skipped (COPY %.1fs, total %.1fs)",
            label, prefix,
            f"{raw_count:,}", f"{inserted:,}", f"{skipped:,}",
            copy_elapsed, elapsed,
        )

    quarter_elapsed = time.perf_counter() - quarter_t0
    log.info("[%s] Quarter done in %.1fs.", label, quarter_elapsed)
    return counts


def load(quarters: list[tuple[str, Path]], filter_labels: list[str] | None = None) -> None:
    if not POSTGRES_URI:
        raise RuntimeError("POSTGRES_URI not set in .env")

    if filter_labels:
        quarters = [(lbl, p) for lbl, p in quarters if lbl in filter_labels]
        if not quarters:
            raise ValueError(f"No matching quarters found for: {filter_labels}")

    log.info("Loading %d quarter(s) into schema '%s'...", len(quarters), POSTGRES_SCHEMA)
    load_t0 = time.perf_counter()

    with closing(psycopg.connect(POSTGRES_URI)) as conn:
        ensure_schema(conn)
        ensure_tables(conn)

        total: dict[str, int] = {}
        for label, zip_path in quarters:
            log.info("── %s (%s) ──", label, zip_path.name)
            counts = load_quarter(conn, label, zip_path)
            for k, v in counts.items():
                total[k] = total.get(k, 0) + v

        ensure_indexes(conn)

    elapsed = time.perf_counter() - load_t0
    log.info("=" * 60)
    log.info("Load complete in %.1fs. Total rows inserted:", elapsed)
    for prefix, cnt in total.items():
        log.info("  %s: %s", prefix, f"{cnt:,}")


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

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

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
