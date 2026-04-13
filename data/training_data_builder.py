"""
MedLens training data builder.

One table: medlens.training_examples — Unsloth-compatible chat rows
with structured provenance for multi-drug interaction fine-tuning.

Usage:
    # (1) Create/reset the schema + table
    uv run python data/training_data_builder.py --create-schema
    uv run python data/training_data_builder.py --create-faers-indexes # (run once to add indexes on FAERS source tables; speeds up multi-drug query by ~10x)

    # (2) Populate from FAERS (multi-drug suspect cases → Type B examples)
    uv run python data/training_data_builder.py --build-faers --limit 5000
    uv run python data/training_data_builder.py --build-faers --min-drugs 3 --max-drugs 8

    # (3) Inspect what was loaded
    uv run python data/training_data_builder.py --stats

    # (4) Export JSONL for Unsloth
    uv run python data/training_data_builder.py --export data/medlens_train.jsonl

DrugBank / synthetic / agentic example generators are stubbed — plug in later.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import closing
from itertools import combinations
from pathlib import Path

from dotenv import load_dotenv
import psycopg
import tiktoken
from psycopg import sql
from psycopg.types.json import Json

CHUNK_SIZE = 500  # rows fetched + inserted per batch

# cl100k_base is GPT-4's BPE — not Gemma's, but within ~10% for English medical
# text and has no model download. Good enough for filtering/bucketing decisions.
# Swap for the real Gemma tokenizer later if needed.
_ENCODER = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_ENCODER.encode(text or ""))


def count_message_tokens(messages: list[dict]) -> int:
    return sum(count_tokens(m.get("content", "")) for m in messages)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(__file__).resolve().parent

load_dotenv(PROJECT_ROOT / ".env")
POSTGRES_URI = os.getenv("POSTGRES_URI")
SCHEMA = "medlens"
TABLE = "training_examples"

# ── severity map from FAERS outc_cod → MedLens severity buckets ──────────
# (from CLAUDE.md: DE/LT/HO=Major, DS/CA/RI=Moderate, OT=Minor)
OUTC_TO_SEVERITY = {
    "DE": "Major", "LT": "Major", "HO": "Major",
    "DS": "Moderate", "CA": "Moderate", "RI": "Moderate",
    "OT": "Minor",
}
SEVERITY_RANK = {"Major": 3, "Moderate": 2, "Minor": 1, "None": 0}

# ── DDL ──────────────────────────────────────────────────────────────────

DDL_SCHEMA = f"CREATE SCHEMA IF NOT EXISTS {SCHEMA};"

DDL_DROP = f"DROP TABLE IF EXISTS {SCHEMA}.{TABLE} CASCADE;"

DDL_TABLE = f"""
CREATE TABLE {SCHEMA}.{TABLE} (
    id              BIGSERIAL PRIMARY KEY,

    example_type    TEXT NOT NULL,          -- single_ddi | multi_drug | agentic_followup
    source          TEXT NOT NULL,          -- faers | drugbank | openfda_label | synthetic
    source_ref      TEXT,                   -- FAERS primaryid, DrugBank pair id, etc.

    -- drug payload (normalized generics)
    drugs           JSONB NOT NULL,         -- ["warfarin","ibuprofen",...]
    n_drugs         INT NOT NULL,
    pairs           JSONB,                  -- [["ibuprofen","warfarin"], ...]  sorted tuples
    n_pairs         INT,

    -- clinical payload
    reactions       JSONB,                  -- MedDRA PT list
    indications     JSONB,                  -- INDI.indi_pt list
    outcome_codes   JSONB,                  -- FAERS outc_cod array
    severity        TEXT,                   -- Major | Moderate | Minor | None
    mechanisms      JSONB,                  -- per-pair mechanism (DrugBank); null until enriched

    -- demographics
    age             TEXT,
    sex             TEXT,

    -- chat payload (Unsloth format)
    messages        JSONB NOT NULL,
    n_turns         INT NOT NULL,
    has_think       BOOLEAN DEFAULT false,
    think_trace     TEXT,

    -- ops
    token_count           INT,    -- total tokens in `messages` payload
    thinking_token_count  INT,    -- tokens inside the <|think|> block only
    quality_score         REAL,
    split           TEXT DEFAULT 'train',
    created_at      TIMESTAMPTZ DEFAULT now()
);
"""

DDL_INDEXES = [
    f"CREATE INDEX ON {SCHEMA}.{TABLE} (example_type);",
    f"CREATE INDEX ON {SCHEMA}.{TABLE} (severity);",
    f"CREATE INDEX ON {SCHEMA}.{TABLE} (n_drugs);",
    f"CREATE INDEX ON {SCHEMA}.{TABLE} (split);",
    f"CREATE INDEX ON {SCHEMA}.{TABLE} (source);",
    f"CREATE INDEX ON {SCHEMA}.{TABLE} USING GIN (drugs);",
    f"CREATE INDEX ON {SCHEMA}.{TABLE} USING GIN (pairs);",
    f"CREATE INDEX ON {SCHEMA}.{TABLE} USING GIN (reactions);",
]


def require_uri() -> str:
    if not POSTGRES_URI:
        raise RuntimeError("POSTGRES_URI not set in .env")
    return POSTGRES_URI


def create_schema() -> None:
    """Drop + recreate medlens.training_examples. Destructive."""
    uri = require_uri()
    with closing(psycopg.connect(uri)) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL_SCHEMA)
            cur.execute(DDL_DROP)
            cur.execute(DDL_TABLE)
            for ddl in DDL_INDEXES:
                cur.execute(ddl)
        conn.commit()
    print(f"Created {SCHEMA}.{TABLE} (dropped if existed).")


# Indexes on faers source tables — only needed once; massively speeds up
# the multi-CTE SELECT (all joins are on primaryid; drug has a large filter).
FAERS_INDEX_DDLS = [
    # drug: partial index covering the WHERE + GROUP BY + INCLUDE for prod_ai
    """CREATE INDEX IF NOT EXISTS faers_drug_suspect_idx
       ON faers.drug (primaryid)
       INCLUDE (prod_ai)
       WHERE role_cod IN ('PS','SS','I')
         AND prod_ai IS NOT NULL
         AND prod_ai <> '';""",
    # join targets — all keyed on primaryid
    "CREATE INDEX IF NOT EXISTS faers_reac_pid_idx ON faers.reac (primaryid);",
    "CREATE INDEX IF NOT EXISTS faers_indi_pid_idx ON faers.indi (primaryid);",
    "CREATE INDEX IF NOT EXISTS faers_outc_pid_idx ON faers.outc (primaryid);",
    "CREATE INDEX IF NOT EXISTS faers_demo_pid_idx ON faers.demo (primaryid);",
]


def create_faers_indexes() -> None:
    """Add indexes to faers source tables. Safe to re-run (IF NOT EXISTS)."""
    uri = require_uri()
    with closing(psycopg.connect(uri)) as conn:
        for ddl in FAERS_INDEX_DDLS:
            name = ddl.split("faers_")[1].split(" ")[0] if "faers_" in ddl else "?"
            print(f"  Creating index faers_{name} …", end=" ", flush=True)
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()
            print("done")
    print("All faers indexes created.")


# ── FAERS → Type B multi-drug examples ───────────────────────────────────

FAERS_CASE_QUERY = """
WITH suspect AS (
    SELECT primaryid,
           array_agg(DISTINCT LOWER(prod_ai) ORDER BY LOWER(prod_ai)) AS drugs
    FROM faers.drug
    WHERE role_cod IN ('PS','SS','I')
      AND prod_ai IS NOT NULL
      AND prod_ai <> ''
    GROUP BY primaryid
    HAVING COUNT(DISTINCT prod_ai) BETWEEN %(min_drugs)s AND %(max_drugs)s
),
reacs AS (
    SELECT primaryid, array_agg(DISTINCT pt) AS reactions
    FROM faers.reac WHERE pt IS NOT NULL GROUP BY primaryid
),
indis AS (
    SELECT primaryid, array_agg(DISTINCT indi_pt) AS indications
    FROM faers.indi WHERE indi_pt IS NOT NULL AND indi_pt <> '' GROUP BY primaryid
),
outcs AS (
    SELECT primaryid, array_agg(DISTINCT outc_cod) AS outcome_codes
    FROM faers.outc WHERE outc_cod IS NOT NULL GROUP BY primaryid
)
SELECT s.primaryid,
       s.drugs,
       r.reactions,
       i.indications,
       o.outcome_codes,
       d.age, d.age_cod, d.sex
FROM suspect s
LEFT JOIN reacs r USING (primaryid)
LEFT JOIN indis i USING (primaryid)
LEFT JOIN outcs o USING (primaryid)
LEFT JOIN faers.demo d USING (primaryid)
WHERE r.reactions IS NOT NULL              -- must have at least one reaction
ORDER BY s.primaryid
LIMIT %(limit)s;
"""


def severity_from_outcomes(outcome_codes: list[str] | None) -> str:
    if not outcome_codes:
        return "None"
    best = "None"
    for code in outcome_codes:
        sev = OUTC_TO_SEVERITY.get(code, "None")
        if SEVERITY_RANK[sev] > SEVERITY_RANK[best]:
            best = sev
    return best


def build_pairs(drugs: list[str]) -> list[list[str]]:
    return [list(p) for p in combinations(sorted(drugs), 2)]


def format_age(age: str | None, age_cod: str | None) -> str | None:
    if not age:
        return None
    unit_map = {"YR": "year-old", "MON": "month-old", "WK": "week-old",
                "DY": "day-old", "HR": "hour-old", "DEC": "decade-old"}
    unit = unit_map.get((age_cod or "").upper(), "")
    return f"{age} {unit}".strip() if unit else age


def render_messages_type_b(
    drugs: list[str],
    reactions: list[str],
    indications: list[str] | None,
    severity: str,
    age: str | None,
    sex: str | None,
) -> tuple[list[dict], str]:
    """Build Unsloth-format messages + think_trace for one Type B example."""
    sex_map = {"M": "male", "F": "female"}
    sex_str = sex_map.get((sex or "").upper(), "")

    patient_parts = []
    if age:
        patient_parts.append(f"a {age}")
    if sex_str:
        patient_parts.append(sex_str)
    patient_desc = " ".join(patient_parts) if patient_parts else "a patient"

    ind_str = (
        f" I take them for {', '.join(indications[:3])}."
        if indications else ""
    )
    user_msg = (
        f"I'm {patient_desc}. I currently take: "
        f"{', '.join(drugs)}.{ind_str} "
        f"I've been experiencing: {', '.join(reactions[:5])}. "
        f"Are any of these drugs interacting?"
    )

    n = len(drugs)
    n_pairs = n * (n - 1) // 2

    demo_bits = []
    if age:
        demo_bits.append(f"age: {age}")
    if sex_str:
        demo_bits.append(f"sex: {sex_str}")
    demo_str = f" ({', '.join(demo_bits)})" if demo_bits else ""

    think = (
        f"Patient{demo_str} on {n} concurrent drugs: {', '.join(drugs)}. "
        f"{n_pairs} possible pairwise interactions to evaluate. "
        f"Reported adverse events: {', '.join(reactions[:5])}. "
        f"Demographics are relevant — elderly and pediatric patients have "
        f"altered pharmacokinetics, and sex-based differences affect CYP450 "
        f"metabolism. "
        f"This pattern matches FAERS-documented multi-drug cases with "
        f"severity classification: {severity}. "
        f"Systematic pair check needed before giving advice."
    )

    icon = {"Major": "🚨", "Moderate": "⚠️", "Minor": "ℹ️", "None": "✅"}[severity]
    assistant_msg = (
        f"<|think|>{think}</think>\n\n"
        f"{icon} **{severity.upper()} interaction risk detected**\n\n"
        f"**Medications reviewed ({n}):** {', '.join(drugs)}\n\n"
        f"**Reported adverse events:** {', '.join(reactions[:5])}\n\n"
        f"Based on real-world pharmacovigilance data, this combination "
        f"has been associated with {severity.lower()} outcomes. "
        f"Please consult your prescribing physician or pharmacist before "
        f"making any changes — do not stop medications abruptly."
    )

    messages = [
        {"role": "user", "content": user_msg},
        {"role": "assistant", "content": assistant_msg},
    ]
    return messages, think


INSERT_SQL = f"""
INSERT INTO {SCHEMA}.{TABLE} (
    example_type, source, source_ref,
    drugs, n_drugs, pairs, n_pairs,
    reactions, indications, outcome_codes, severity,
    age, sex,
    messages, n_turns, has_think, think_trace,
    token_count, thinking_token_count,
    split
) VALUES (
    %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s,
    %s, %s, %s, %s,
    %s, %s,
    %s
);
"""


def _row_to_record(
    row: tuple, min_drugs: int
) -> tuple | None:
    """Convert one FAERS result row → INSERT tuple, or None if filtered out."""
    primaryid, drugs, reactions, indications, outcome_codes, age, age_cod, sex = row
    drugs = [d for d in (drugs or []) if d]
    reactions = [r for r in (reactions or []) if r]
    if len(drugs) < min_drugs or not reactions:
        return None

    pairs = build_pairs(drugs)
    severity = severity_from_outcomes(outcome_codes)
    age_str = format_age(age, age_cod)
    messages, think = render_messages_type_b(
        drugs, reactions, indications, severity, age_str, sex,
    )
    tok_total = count_message_tokens(messages)
    tok_think = count_tokens(think)
    return (
        "multi_drug", "faers", str(primaryid),
        Json(drugs), len(drugs), Json(pairs), len(pairs),
        Json(reactions), Json(indications or []), Json(outcome_codes or []), severity,
        age_str, sex,
        Json(messages), len(messages), True, think,
        tok_total, tok_think,
        "train",
    )


STREAM_THRESHOLD = 20_000  # above this, use ServerCursor + chunks; below, single-shot


def build_from_faers(limit: int, min_drugs: int, max_drugs: int, batch_size: int = CHUNK_SIZE) -> int:
    """
    Two modes:
      • limit <= STREAM_THRESHOLD: client cursor + single executemany
        (fastest for small batches — psycopg3 pipeline fast path kicks in)
      • limit  > STREAM_THRESHOLD: ServerCursor + chunked inserts across
        two connections (keeps memory flat when pulling all cases)
    """
    uri = require_uri()
    inserted = 0
    skipped = 0

    # ── small batch: simple path ─────────────────────────────────────────
    if limit <= STREAM_THRESHOLD:
        with closing(psycopg.connect(uri)) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    FAERS_CASE_QUERY,
                    {"limit": limit, "min_drugs": min_drugs, "max_drugs": max_drugs},
                )
                rows = cur.fetchall()
            print(f"Fetched {len(rows):,} candidate FAERS cases.")

            records = []
            for row in rows:
                rec = _row_to_record(row, min_drugs)
                if rec is None:
                    skipped += 1
                else:
                    records.append(rec)

            with conn.cursor() as cur:
                cur.executemany(INSERT_SQL, records)
            conn.commit()
            inserted = len(records)

        print(f"Inserted {inserted:,} FAERS multi-drug training examples "
              f"({skipped:,} skipped).")
        return inserted

    # ── large batch: streaming path (two connections) ────────────────────
    with closing(psycopg.connect(uri)) as reader_conn, \
         closing(psycopg.connect(uri)) as writer_conn:

        chunk: list[tuple] = []

        def flush_chunk() -> None:
            nonlocal inserted
            if not chunk:
                return
            with writer_conn.cursor() as wc:
                wc.executemany(INSERT_SQL, chunk)
            writer_conn.commit()
            inserted += len(chunk)
            chunk.clear()
            print(f"  … {inserted:,} inserted so far", end="\r", flush=True)

        with psycopg.ServerCursor(reader_conn, "faers_stream") as reader:
            reader.itersize = batch_size
            reader.execute(
                FAERS_CASE_QUERY,
                {"limit": limit, "min_drugs": min_drugs, "max_drugs": max_drugs},
            )
            for row in reader:
                record = _row_to_record(row, min_drugs)
                if record is None:
                    skipped += 1
                    continue
                chunk.append(record)
                if len(chunk) >= batch_size:
                    flush_chunk()

        flush_chunk()

    print(f"\nInserted {inserted:,} FAERS multi-drug training examples "
          f"({skipped:,} skipped — too few drugs or no reactions).")
    return inserted


# ── stats ─────────────────────────────────────────────────────────────────

def stats() -> None:
    uri = require_uri()
    with closing(psycopg.connect(uri)) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {SCHEMA}.{TABLE};")
            total = cur.fetchone()[0]
            print(f"\nTotal examples: {total:,}")

            if total == 0:
                return

            cur.execute(f"""
                SELECT example_type, source, severity, COUNT(*)
                FROM {SCHEMA}.{TABLE}
                GROUP BY example_type, source, severity
                ORDER BY COUNT(*) DESC;
            """)
            print("\nBy type / source / severity:")
            for t, s, sev, c in cur.fetchall():
                print(f"  {t:20s} | {s:10s} | {sev or 'null':9s} | {c:>8,}")

            cur.execute(f"""
                SELECT n_drugs, COUNT(*)
                FROM {SCHEMA}.{TABLE}
                GROUP BY n_drugs ORDER BY n_drugs;
            """)
            print("\nBy drug count:")
            for n, c in cur.fetchall():
                print(f"  {n:>2} drugs: {c:>8,}")

            cur.execute(f"""
                SELECT split, COUNT(*) FROM {SCHEMA}.{TABLE}
                GROUP BY split ORDER BY split;
            """)
            print("\nBy split:")
            for s, c in cur.fetchall():
                print(f"  {s:8s}: {c:>8,}")

            cur.execute(f"""
                SELECT
                    MIN(token_count), ROUND(AVG(token_count)::numeric, 1),
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY token_count),
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY token_count),
                    MAX(token_count),
                    ROUND(AVG(thinking_token_count)::numeric, 1),
                    MAX(thinking_token_count)
                FROM {SCHEMA}.{TABLE}
                WHERE token_count IS NOT NULL;
            """)
            row = cur.fetchone()
            if row and row[0] is not None:
                mn, avg, p50, p95, mx, avg_think, mx_think = row
                print("\nToken stats (messages total):")
                print(f"  min={mn}  avg={avg}  p50={p50:.0f}  p95={p95:.0f}  max={mx}")
                print(f"  think avg={avg_think}  think max={mx_think}")


# ── export ────────────────────────────────────────────────────────────────

def export_jsonl(out_path: Path, split: str | None = None) -> None:
    uri = require_uri()
    where = ""
    params: tuple = ()
    if split:
        where = "WHERE split = %s"
        params = (split,)

    with closing(psycopg.connect(uri)) as conn, open(out_path, "w") as f:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT messages FROM {SCHEMA}.{TABLE} {where} ORDER BY id;",
                params,
            )
            count = 0
            for (messages,) in cur:
                f.write(json.dumps({"messages": messages}) + "\n")
                count += 1
    print(f"Exported {count:,} rows → {out_path}")


# ── CLI ───────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="MedLens training data builder")
    p.add_argument("--create-schema", action="store_true",
                   help="Drop + recreate medlens.training_examples")
    p.add_argument("--create-faers-indexes", action="store_true",
                   help="Add indexes on faers source tables (run once before --build-faers)")
    p.add_argument("--build-faers", action="store_true",
                   help="Populate Type B multi-drug examples from FAERS")
    p.add_argument("--limit", type=int, default=5000,
                   help="Max FAERS cases to pull (default: 5000)")
    p.add_argument("--min-drugs", type=int, default=3)
    p.add_argument("--max-drugs", type=int, default=8)
    p.add_argument("--batch-size", type=int, default=CHUNK_SIZE,
                   help=f"Rows per insert batch (default: {CHUNK_SIZE})")
    p.add_argument("--stats", action="store_true")
    p.add_argument("--export", metavar="PATH",
                   help="Export messages to JSONL at PATH")
    p.add_argument("--split", default=None,
                   help="Optional split filter for --export (train/val/test)")
    args = p.parse_args()

    did_something = False

    if args.create_schema:
        create_schema()
        did_something = True

    if args.create_faers_indexes:
        create_faers_indexes()
        did_something = True

    if args.build_faers:
        build_from_faers(args.limit, args.min_drugs, args.max_drugs, args.batch_size)
        did_something = True

    if args.stats:
        stats()
        did_something = True

    if args.export:
        export_jsonl(Path(args.export), split=args.split)
        did_something = True

    if not did_something:
        p.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
