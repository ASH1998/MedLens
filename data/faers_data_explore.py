"""
FAERS data exploration — interactive-style script.

Each section is a standalone function designed to map cleanly to a Jupyter
notebook cell. Run the whole file, or import and call individual sections:

    from data.faers_explore import section_schema_overview
    section_schema_overview()

The script reads from the `faers` PostgreSQL schema populated by
`faers_explorer.py --load`. Results are printed as plain tables; pandas
DataFrames are returned from each function for downstream use.

Usage:
    uv run python data/faers_explore.py                # run all sections
    uv run python data/faers_explore.py --sections 1 3 # run specific sections
    uv run python data/faers_explore.py --list         # list sections
"""

from __future__ import annotations

import argparse
import os
import textwrap
from contextlib import closing
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
import psycopg

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

POSTGRES_URI = os.getenv("POSTGRES_URI")
SCHEMA = "faers"


# ── utilities ──────────────────────────────────────────────────────────────

def query(sql: str, params: tuple | None = None) -> pd.DataFrame:
    """Run a read-only query and return a DataFrame."""
    if not POSTGRES_URI:
        raise RuntimeError("POSTGRES_URI not set in .env")
    with closing(psycopg.connect(POSTGRES_URI)) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d.name for d in cur.description] if cur.description else []
            rows = cur.fetchall() if cur.description else []
    return pd.DataFrame(rows, columns=cols)


def header(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(f"  {title}")
    print('=' * 72)


def show(df: pd.DataFrame, max_rows: int = 25) -> None:
    """Pretty-print a DataFrame."""
    with pd.option_context(
        "display.max_rows", max_rows,
        "display.max_columns", None,
        "display.width", 200,
        "display.max_colwidth", 80,
    ):
        print(df.to_string(index=False))


# ── Section 1: Schema overview ─────────────────────────────────────────────

def section_schema_overview() -> pd.DataFrame:
    """Row counts, table sizes, column inventory — what exists and how big."""
    header("1. Schema overview — tables in `faers`")

    df = query(f"""
        SELECT
            c.relname AS table_name,
            pg_size_pretty(pg_total_relation_size(c.oid)) AS size,
            c.reltuples::bigint AS est_rows
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = '{SCHEMA}' AND c.relkind = 'r'
        ORDER BY pg_total_relation_size(c.oid) DESC;
    """)
    show(df)

    # Exact row counts (reltuples is only approximate)
    header("1b. Exact row counts")
    tables = df["table_name"].tolist()
    counts = []
    for t in tables:
        n = query(f"SELECT COUNT(*) AS n FROM {SCHEMA}.{t}")["n"].iloc[0]
        counts.append({"table": t, "rows": n})
    counts_df = pd.DataFrame(counts)
    show(counts_df)

    # Quarters covered (based on fda_dt in demo — if the field was loaded)
    header("1c. Date range of loaded reports")
    date_df = query(f"""
        SELECT
            MIN(LEFT(event_dt, 6)) AS earliest_event,
            MAX(LEFT(event_dt, 6)) AS latest_event,
            COUNT(DISTINCT LEFT(event_dt, 6)) AS distinct_months
        FROM {SCHEMA}.demo
        WHERE event_dt IS NOT NULL AND LENGTH(event_dt) >= 6;
    """)
    show(date_df)

    return counts_df


# ── Section 2: Column-level null rates ─────────────────────────────────────

def section_null_rates() -> pd.DataFrame:
    """How populated is each column we care about?"""
    header("2. Null rates for MedLens-relevant columns")

    checks = [
        ("demo", ["age", "age_grp", "sex", "wt", "reporter_country", "occp_cod"]),
        ("drug", ["drugname", "prod_ai", "role_cod", "route", "dose_amt", "dose_unit"]),
        ("reac", ["pt", "drug_rec_act"]),
        ("outc", ["outc_cod"]),
        ("ther", ["start_dt", "end_dt", "dur"]),
        ("indi", ["indi_pt"]),
    ]
    rows = []
    for table, cols in checks:
        total = query(f"SELECT COUNT(*) n FROM {SCHEMA}.{table}")["n"].iloc[0]
        for col in cols:
            nn = query(
                f"SELECT COUNT({col}) n FROM {SCHEMA}.{table}"
            )["n"].iloc[0]
            rows.append({
                "table": table,
                "column": col,
                "total": total,
                "populated": nn,
                "pct_populated": round(100 * nn / total, 1) if total else 0,
            })
    df = pd.DataFrame(rows)
    show(df, max_rows=100)
    return df


# ── Section 3: Role code distribution ──────────────────────────────────────

def section_role_codes() -> pd.DataFrame:
    """
    role_cod categorizes every drug mention in a case:
      PS = Primary Suspect, SS = Secondary Suspect, C = Concomitant,
      I = Interacting, DN = Drug Not Administered.

    For MedLens: PS/SS/I are the "suspect" set — the drugs we claim caused
    or contributed to the event. Concomitants are background context.
    """
    header("3. role_cod distribution (which drugs FDA blames)")

    df = query(f"""
        SELECT role_cod,
               COUNT(*) AS mentions,
               ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
        FROM {SCHEMA}.drug
        GROUP BY role_cod
        ORDER BY mentions DESC;
    """)
    show(df)

    # How many cases are DDI-flagged (role='I')?
    ddi_df = query(f"""
        SELECT COUNT(DISTINCT primaryid) AS ddi_cases,
               COUNT(*) AS ddi_drug_mentions
        FROM {SCHEMA}.drug
        WHERE role_cod = 'I';
    """)
    header("3b. FDA-flagged drug-drug interaction cases (role='I')")
    show(ddi_df)

    return df


# ── Section 4: Outcome severity ────────────────────────────────────────────

def section_outcomes() -> pd.DataFrame:
    """
    Per FDA: DE=Death, LT=Life-threatening, HO=Hospitalization,
    DS=Disability, CA=Congenital Anomaly, RI=Required Intervention, OT=Other.

    MedLens severity mapping:
      Major    = DE, LT, HO
      Moderate = DS, CA, RI
      Minor    = OT
    """
    header("4. outc_cod distribution — training severity labels")

    df = query(f"""
        WITH sev AS (
          SELECT outc_cod,
            CASE outc_cod
              WHEN 'DE' THEN 'Major'
              WHEN 'LT' THEN 'Major'
              WHEN 'HO' THEN 'Major'
              WHEN 'DS' THEN 'Moderate'
              WHEN 'CA' THEN 'Moderate'
              WHEN 'RI' THEN 'Moderate'
              WHEN 'OT' THEN 'Minor'
            END AS severity
          FROM {SCHEMA}.outc
        )
        SELECT severity, outc_cod, COUNT(*) n
        FROM sev
        GROUP BY severity, outc_cod
        ORDER BY CASE severity WHEN 'Major' THEN 1 WHEN 'Moderate' THEN 2 ELSE 3 END,
                 n DESC;
    """)
    show(df)

    header("4b. Cases with multiple outcomes (e.g. DE + HO)")
    multi = query(f"""
        SELECT n_outcomes, COUNT(*) AS cases
        FROM (
          SELECT primaryid, COUNT(DISTINCT outc_cod) AS n_outcomes
          FROM {SCHEMA}.outc
          GROUP BY primaryid
        ) t
        GROUP BY n_outcomes
        ORDER BY n_outcomes;
    """)
    show(multi)

    return df


# ── Section 5: Drug name / ingredient normalization ────────────────────────

def section_drug_names() -> pd.DataFrame:
    """
    drugname   = what the reporter wrote (often brand + formulation)
    prod_ai    = active ingredient(s) (FDA-normalized)

    For MedLens: prod_ai is the join key to RxNorm / DrugBank. drugname
    helps train OCR → normalization (seen-in-wild spellings).
    """
    header("5. Drug name vs active ingredient")

    df = query(f"""
        SELECT
            COUNT(*) AS total_mentions,
            COUNT(DISTINCT drugname) AS distinct_drugnames,
            COUNT(DISTINCT prod_ai) AS distinct_ingredients,
            ROUND(100.0 * COUNT(prod_ai) / COUNT(*), 1) AS pct_ai_populated
        FROM {SCHEMA}.drug;
    """)
    show(df)

    header("5b. Top 20 active ingredients by mention count")
    top = query(f"""
        SELECT prod_ai, COUNT(*) mentions,
               COUNT(DISTINCT drugname) AS n_brand_variants
        FROM {SCHEMA}.drug
        WHERE prod_ai IS NOT NULL
        GROUP BY prod_ai
        ORDER BY mentions DESC
        LIMIT 20;
    """)
    show(top)

    header("5c. Example brand variants for a common ingredient (ACETAMINOPHEN)")
    variants = query(f"""
        SELECT drugname, COUNT(*) n
        FROM {SCHEMA}.drug
        WHERE prod_ai = 'ACETAMINOPHEN'
        GROUP BY drugname
        ORDER BY n DESC
        LIMIT 15;
    """)
    show(variants)

    return top


# ── Section 6: Polypharmacy signal ─────────────────────────────────────────

def section_polypharmacy() -> pd.DataFrame:
    """Distribution of drug counts per case — the core training signal."""
    header("6. Polypharmacy: distribution of suspect drugs per case")

    df = query(f"""
        WITH case_drugs AS (
          SELECT primaryid, COUNT(DISTINCT prod_ai) AS n_drugs
          FROM {SCHEMA}.drug
          WHERE role_cod IN ('PS','SS','I') AND prod_ai IS NOT NULL
          GROUP BY primaryid
        )
        SELECT
          CASE
            WHEN n_drugs = 1 THEN '1 (monotherapy)'
            WHEN n_drugs = 2 THEN '2 drugs'
            WHEN n_drugs = 3 THEN '3 drugs'
            WHEN n_drugs BETWEEN 4 AND 5 THEN '4-5 drugs'
            WHEN n_drugs BETWEEN 6 AND 10 THEN '6-10 drugs'
            ELSE '11+ drugs'
          END AS bucket,
          COUNT(*) AS cases,
          ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
        FROM case_drugs
        GROUP BY bucket
        ORDER BY MIN(n_drugs);
    """)
    show(df)

    header("6b. Multi-drug cases × severity cross-tab")
    cross = query(f"""
        WITH case_drugs AS (
          SELECT primaryid, COUNT(DISTINCT prod_ai) AS n_drugs
          FROM {SCHEMA}.drug
          WHERE role_cod IN ('PS','SS','I') AND prod_ai IS NOT NULL
          GROUP BY primaryid
          HAVING COUNT(DISTINCT prod_ai) >= 2
        ),
        worst_outcome AS (
          SELECT primaryid,
                 MAX(CASE outc_cod
                       WHEN 'DE' THEN 6 WHEN 'LT' THEN 5 WHEN 'HO' THEN 4
                       WHEN 'DS' THEN 3 WHEN 'CA' THEN 2 WHEN 'RI' THEN 1 ELSE 0
                     END) AS severity_score
          FROM {SCHEMA}.outc
          GROUP BY primaryid
        )
        SELECT
          CASE severity_score
            WHEN 6 THEN '6 DE (death)'
            WHEN 5 THEN '5 LT (life-threat)'
            WHEN 4 THEN '4 HO (hospitalized)'
            WHEN 3 THEN '3 DS (disability)'
            WHEN 2 THEN '2 CA (cong anomaly)'
            WHEN 1 THEN '1 RI (req interv)'
            WHEN 0 THEN '0 OT (other)'
            ELSE 'no outcome'
          END AS severity,
          COUNT(*) AS multi_drug_cases
        FROM case_drugs cd
        LEFT JOIN worst_outcome wo USING (primaryid)
        GROUP BY severity_score
        ORDER BY severity_score DESC NULLS LAST;
    """)
    show(cross)

    return df


# ── Section 7: Drug-drug interaction pairs ────────────────────────────────

def section_ddi_pairs() -> pd.DataFrame:
    """Extract drug-pair co-occurrences from polypharmacy cases."""
    header("7. Top drug pairs in multi-drug suspect cases (FDA role='I')")

    df = query(f"""
        WITH ddi AS (
          SELECT DISTINCT primaryid, prod_ai
          FROM {SCHEMA}.drug
          WHERE role_cod = 'I' AND prod_ai IS NOT NULL
        ),
        pairs AS (
          SELECT a.prod_ai AS drug_a, b.prod_ai AS drug_b, a.primaryid
          FROM ddi a
          JOIN ddi b ON a.primaryid = b.primaryid AND a.prod_ai < b.prod_ai
        )
        SELECT drug_a, drug_b, COUNT(*) AS co_mentions
        FROM pairs
        GROUP BY drug_a, drug_b
        ORDER BY co_mentions DESC
        LIMIT 25;
    """)
    show(df)

    header("7b. Top pairs in ALL suspect cases (PS+SS+I)")
    all_pairs = query(f"""
        WITH suspects AS (
          SELECT DISTINCT primaryid, prod_ai
          FROM {SCHEMA}.drug
          WHERE role_cod IN ('PS','SS','I') AND prod_ai IS NOT NULL
        ),
        pairs AS (
          SELECT a.prod_ai AS drug_a, b.prod_ai AS drug_b, a.primaryid
          FROM suspects a
          JOIN suspects b ON a.primaryid = b.primaryid AND a.prod_ai < b.prod_ai
        )
        SELECT drug_a, drug_b, COUNT(*) AS co_mentions
        FROM pairs
        GROUP BY drug_a, drug_b
        ORDER BY co_mentions DESC
        LIMIT 25;
    """)
    show(all_pairs)

    return df


# ── Section 8: Drug → reaction patterns ────────────────────────────────────

def section_drug_reactions() -> pd.DataFrame:
    """For a given drug, what reactions does it cause?"""
    header("8. Top reactions for selected drugs")

    drugs = ["ACETAMINOPHEN", "ASPIRIN", "WARFARIN", "METFORMIN", "ATORVASTATIN CALCIUM"]
    for d in drugs:
        df = query(f"""
            SELECT r.pt AS reaction, COUNT(*) AS n
            FROM {SCHEMA}.drug dr
            JOIN {SCHEMA}.reac r USING (primaryid)
            WHERE dr.prod_ai = %s AND dr.role_cod IN ('PS','SS')
            GROUP BY r.pt
            ORDER BY n DESC
            LIMIT 8;
        """, (d,))
        print(f"\n-- {d} --")
        show(df)

    return None


# ── Section 9: Demographics by outcome ────────────────────────────────────

def section_demographics() -> pd.DataFrame:
    """Age group / sex breakdown of severe outcomes — training distribution check."""
    header("9. Age group × severity")

    df = query(f"""
        WITH worst AS (
          SELECT primaryid,
                 MAX(CASE outc_cod
                       WHEN 'DE' THEN 6 WHEN 'LT' THEN 5 WHEN 'HO' THEN 4
                       WHEN 'DS' THEN 3 WHEN 'CA' THEN 2 WHEN 'RI' THEN 1 ELSE 0
                     END) AS sev
          FROM {SCHEMA}.outc
          GROUP BY primaryid
        )
        SELECT
          COALESCE(d.age_grp, 'unknown') AS age_grp,
          SUM(CASE WHEN w.sev >= 4 THEN 1 ELSE 0 END) AS major,
          SUM(CASE WHEN w.sev BETWEEN 1 AND 3 THEN 1 ELSE 0 END) AS moderate,
          SUM(CASE WHEN w.sev = 0 THEN 1 ELSE 0 END) AS minor,
          COUNT(*) AS total
        FROM {SCHEMA}.demo d
        LEFT JOIN worst w USING (primaryid)
        GROUP BY d.age_grp
        ORDER BY total DESC;
    """)
    show(df)

    header("9b. Sex × severity")
    sx = query(f"""
        WITH worst AS (
          SELECT primaryid,
                 MAX(CASE outc_cod WHEN 'DE' THEN 6 WHEN 'LT' THEN 5 WHEN 'HO' THEN 4
                       WHEN 'DS' THEN 3 WHEN 'CA' THEN 2 WHEN 'RI' THEN 1 ELSE 0 END) AS sev
          FROM {SCHEMA}.outc
          GROUP BY primaryid
        )
        SELECT
          COALESCE(d.sex, 'unknown') AS sex,
          SUM(CASE WHEN w.sev >= 4 THEN 1 ELSE 0 END) AS major,
          SUM(CASE WHEN w.sev BETWEEN 1 AND 3 THEN 1 ELSE 0 END) AS moderate,
          SUM(CASE WHEN w.sev = 0 THEN 1 ELSE 0 END) AS minor,
          COUNT(*) AS total
        FROM {SCHEMA}.demo d
        LEFT JOIN worst w USING (primaryid)
        GROUP BY d.sex
        ORDER BY total DESC;
    """)
    show(sx)

    return df


# ── Section 10: Full case reconstruction (training example preview) ────────

def section_training_example_preview(limit: int = 3) -> pd.DataFrame:
    """
    Reconstruct full cases end-to-end, then format as candidate training
    examples. Shows what MedLens fine-tuning data will look like.
    """
    header(f"10. Full case reconstruction — {limit} sample training examples")

    # Find N interesting cases: multi-drug suspect + severe outcome + reactions
    cases = query(f"""
        WITH suspect_cases AS (
          SELECT primaryid, COUNT(DISTINCT prod_ai) AS n_drugs
          FROM {SCHEMA}.drug
          WHERE role_cod IN ('PS','SS','I') AND prod_ai IS NOT NULL
          GROUP BY primaryid
          HAVING COUNT(DISTINCT prod_ai) BETWEEN 3 AND 6
        ),
        severe AS (
          SELECT DISTINCT primaryid FROM {SCHEMA}.outc WHERE outc_cod IN ('DE','LT','HO')
        )
        SELECT sc.primaryid
        FROM suspect_cases sc
        JOIN severe s USING (primaryid)
        LIMIT {limit};
    """)

    for pid in cases["primaryid"]:
        print(f"\n{'─' * 72}")
        print(f"CASE {pid}")
        print('─' * 72)

        demo = query(f"""
            SELECT age, age_cod, age_grp, sex, wt, wt_cod, reporter_country, occp_cod
            FROM {SCHEMA}.demo WHERE primaryid = %s LIMIT 1;
        """, (pid,))
        print("\n[demographics]")
        show(demo)

        drugs = query(f"""
            SELECT drug_seq, role_cod, drugname, prod_ai, route, dose_amt, dose_unit
            FROM {SCHEMA}.drug WHERE primaryid = %s
            ORDER BY role_cod, drug_seq;
        """, (pid,))
        print("\n[drugs]")
        show(drugs)

        indi = query(f"""
            SELECT indi_drug_seq, indi_pt
            FROM {SCHEMA}.indi WHERE primaryid = %s ORDER BY indi_drug_seq;
        """, (pid,))
        print("\n[indications]")
        show(indi)

        reac = query(f"""
            SELECT pt, drug_rec_act
            FROM {SCHEMA}.reac WHERE primaryid = %s;
        """, (pid,))
        print("\n[reactions]")
        show(reac)

        outc = query(f"""
            SELECT outc_cod FROM {SCHEMA}.outc WHERE primaryid = %s;
        """, (pid,))
        print("\n[outcomes]")
        show(outc)

        # Format as candidate training example
        suspect_drugs = drugs[drugs["role_cod"].isin(["PS", "SS", "I"])]["prod_ai"].dropna().unique()
        reactions = reac["pt"].dropna().tolist()
        outcomes = outc["outc_cod"].tolist()
        indications = indi["indi_pt"].dropna().tolist()

        print("\n[formatted training candidate]")
        print(textwrap.indent(textwrap.dedent(f"""
            USER: Patient taking {', '.join(suspect_drugs[:6])}.
                  Indications: {', '.join(indications[:4])}.
                  What interactions should I watch for?

            ASSISTANT (target):
              <|think|>
              Multi-drug regimen. Check each pair for known interactions.
              Reported reactions in FAERS for this combo: {', '.join(reactions[:4])}.
              Severity (FAERS): {', '.join(outcomes)}.
              </|think>
              [generated clinical reasoning + severity-ranked report]
        """), "  "))

    return cases


# ── Section 11: Data quality red flags ─────────────────────────────────────

def section_quality_flags() -> pd.DataFrame:
    """Things that might bite us during training example generation."""
    header("11. Data quality red flags")

    q = query(f"""
        SELECT
          (SELECT COUNT(*) FROM {SCHEMA}.drug WHERE prod_ai IS NULL) AS drug_null_ai,
          (SELECT COUNT(*) FROM {SCHEMA}.drug WHERE drugname IS NULL) AS drug_null_name,
          (SELECT COUNT(*) FROM {SCHEMA}.drug d
             LEFT JOIN {SCHEMA}.demo de USING (primaryid)
             WHERE de.primaryid IS NULL) AS drug_orphan_no_demo,
          (SELECT COUNT(*) FROM {SCHEMA}.reac r
             LEFT JOIN {SCHEMA}.demo de USING (primaryid)
             WHERE de.primaryid IS NULL) AS reac_orphan_no_demo,
          (SELECT COUNT(DISTINCT primaryid) FROM {SCHEMA}.demo) AS unique_cases_demo,
          (SELECT COUNT(DISTINCT primaryid) FROM {SCHEMA}.drug) AS unique_cases_drug,
          (SELECT COUNT(DISTINCT primaryid) FROM {SCHEMA}.outc) AS unique_cases_outc;
    """)
    show(q)

    # Free-text garbage in prod_ai?
    header("11b. Suspicious active-ingredient strings (potential OCR/data-entry noise)")
    susp = query(f"""
        SELECT prod_ai, COUNT(*) n
        FROM {SCHEMA}.drug
        WHERE prod_ai IS NOT NULL
          AND (LENGTH(prod_ai) > 100 OR prod_ai ~ '[0-9]{{3,}}')
        GROUP BY prod_ai
        ORDER BY n DESC
        LIMIT 10;
    """)
    show(susp)

    return q


# ── registry ───────────────────────────────────────────────────────────────

SECTIONS = [
    ("1", "Schema overview", section_schema_overview),
    ("2", "Null rates per column", section_null_rates),
    ("3", "Role code distribution", section_role_codes),
    ("4", "Outcome severity", section_outcomes),
    ("5", "Drug name normalization", section_drug_names),
    ("6", "Polypharmacy signal", section_polypharmacy),
    ("7", "Drug-drug interaction pairs", section_ddi_pairs),
    ("8", "Drug → reaction patterns", section_drug_reactions),
    ("9", "Demographics × severity", section_demographics),
    ("10", "Training example preview", section_training_example_preview),
    ("11", "Data quality flags", section_quality_flags),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="FAERS data exploration")
    parser.add_argument("--sections", nargs="+", help="Section numbers to run")
    parser.add_argument("--list", action="store_true", help="List sections and exit")
    args = parser.parse_args()

    if args.list:
        print("Available sections:")
        for num, name, _ in SECTIONS:
            print(f"  {num:>3s}  {name}")
        return

    to_run = SECTIONS
    if args.sections:
        wanted = set(args.sections)
        to_run = [s for s in SECTIONS if s[0] in wanted]
        if not to_run:
            raise SystemExit(f"No sections matched: {args.sections}")

    for num, name, fn in to_run:
        try:
            fn()
        except Exception as e:
            print(f"\n[ERROR in section {num} ({name})]: {e}")


if __name__ == "__main__":
    main()
