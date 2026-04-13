import os
import subprocess
import tempfile

# ===== CONFIG =====
SQL_GZ_FILE = "effect_peds_19q2_v0.3_20211119.sql.gz"

POSTGRES_URI = "postgresql://medadmin:strongpassword@localhost:5432/medlens"

# ==================

def run_command(cmd):
    print(f"\nRunning: {cmd}\n")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        raise Exception(f"Command failed: {cmd}")



def main():
    # Create temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        sqlite_db = os.path.join(tmpdir, "temp.db")

        print(f"Temp SQLite DB: {sqlite_db}")

        # Step 1: Load into SQLite
        cmd_sqlite = f"zcat {SQL_GZ_FILE} | sqlite3 {sqlite_db}"
        run_command(cmd_sqlite)

        # Step 2: Load into PostgreSQL
        cmd_pgloader = f"pgloader {sqlite_db} {POSTGRES_URI}"
        run_command(cmd_pgloader)

        print("\n✅ Data successfully ingested into PostgreSQL!")

if __name__ == "__main__":
    main()