import os
import shlex
import shutil
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv
import psycopg
from psycopg import sql


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(__file__).resolve().parent

# ===== CONFIG =====
load_dotenv(PROJECT_ROOT / ".env")

SQL_GZ_FILE = DATA_DIR / "raw" / "effect_peds_19q2_v0.3_20211119.sql.gz"
POSTGRES_URI = os.getenv("POSTGRES_URI")
POSTGRES_SCHEMA = "raw_data"

# ==================


def run_command(cmd, *, stdin=None, env=None):
    print(f"\nRunning: {shlex.join(cmd)}\n")
    subprocess.run(cmd, stdin=stdin, env=env, check=True)


def ensure_dependencies():
    missing = [name for name in ("zcat", "sqlite3", "pgloader") if shutil.which(name) is None]
    if missing:
        missing_list = ", ".join(missing)
        raise RuntimeError(f"Missing required command(s): {missing_list}")


def validate_config():
    if not SQL_GZ_FILE.exists():
        raise FileNotFoundError(f"SQL dump not found: {SQL_GZ_FILE}")
    if not POSTGRES_URI:
        raise RuntimeError("POSTGRES_URI is not set in the environment or .env file")
    if not POSTGRES_SCHEMA:
        raise RuntimeError("POSTGRES_SCHEMA must be set")


def load_dump_into_sqlite(sqlite_db):
    with subprocess.Popen(["zcat", str(SQL_GZ_FILE)], stdout=subprocess.PIPE) as zcat_process:
        assert zcat_process.stdout is not None
        try:
            run_command(["sqlite3", str(sqlite_db)], stdin=zcat_process.stdout)
        finally:
            zcat_process.stdout.close()

        zcat_return_code = zcat_process.wait()
        if zcat_return_code != 0:
            raise subprocess.CalledProcessError(zcat_return_code, ["zcat", str(SQL_GZ_FILE)])


def list_sqlite_tables(sqlite_db):
    result = subprocess.run(
        [
            "sqlite3",
            str(sqlite_db),
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name;",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def copy_sqlite_to_postgres(sqlite_db):
    sqlite_uri = f"sqlite:///{sqlite_db.as_posix()}"
    pgloader_file = sqlite_db.with_suffix(".load")
    pgloader_file.write_text(
        "\n".join(
            [
                "load database",
                f"    from {sqlite_uri}",
                f"    into {POSTGRES_URI}",
                "",
                " with include drop, create tables, create indexes, reset sequences, downcase identifiers",
                "",
                f"  set search_path to '{POSTGRES_SCHEMA}';",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    run_command(["pgloader", str(pgloader_file)])


def move_loaded_tables_to_schema(table_names):
    if not table_names:
        return

    with closing(psycopg.connect(POSTGRES_URI)) as conn:
        with conn.cursor() as cur:
            for table_name in table_names:
                cur.execute(
                    """
                    SELECT
                        EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public' AND table_name = %s
                        ),
                        EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = %s AND table_name = %s
                        )
                    """,
                    (table_name, POSTGRES_SCHEMA, table_name),
                )
                exists_in_public, exists_in_target = cur.fetchone()

                if not exists_in_public or exists_in_target:
                    continue

                cur.execute(
                    sql.SQL("ALTER TABLE IF EXISTS {}.{} SET SCHEMA {}")
                    .format(
                        sql.Identifier("public"),
                        sql.Identifier(table_name),
                        sql.Identifier(POSTGRES_SCHEMA),
                    )
                )
        conn.commit()


def ensure_postgres_schema():
    with closing(psycopg.connect(POSTGRES_URI)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}")
                .format(sql.Identifier(POSTGRES_SCHEMA))
            )
        conn.commit()


def main():
    ensure_dependencies()
    validate_config()
    ensure_postgres_schema()

    temp_dir = tempfile.TemporaryDirectory(prefix="medlens_sqlite_")
    sqlite_db = Path(temp_dir.name) / "temp.db"

    try:
        print(f"Temporary SQLite DB: {sqlite_db}")
        print("The source dump is SQLite SQL, so no temporary MySQL instance is created.")
        print(f"Target PostgreSQL schema: {POSTGRES_SCHEMA}")

        load_dump_into_sqlite(sqlite_db)
        table_names = list_sqlite_tables(sqlite_db)
        copy_sqlite_to_postgres(sqlite_db)
        move_loaded_tables_to_schema(table_names)

        print("\nData successfully ingested into PostgreSQL.")
    finally:
        temp_dir.cleanup()
        print(f"Removed temporary SQLite database and directory: {sqlite_db.parent}")

if __name__ == "__main__":
    main()