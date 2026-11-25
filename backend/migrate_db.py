"""
One-shot DB migration: creates missing tables and adds missing columns.
Run from the project root:  python backend/migrate_db.py
"""
import sqlite3, os, sys

# ErpDB opens "brain_erp.db" relative to CWD (the project root).
# Support both: running from project root OR from backend/
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)
_root_db = os.path.join(_root, "brain_erp.db")
_backend_db = os.path.join(_here, "brain_erp.db")

# Prefer root DB if it exists (that's what the running app uses)
DB_PATH = _root_db if os.path.exists(_root_db) else _backend_db


def _add_col_if_missing(cur, table, col, col_def):
    existing = [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
        print(f"  [OK] {table}.{col} column added.")
    else:
        print(f"  [--] {table}.{col} already exists, skipped.")


def migrate(db_path):
    print(f"Migrating: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1. master_suppliers
    cur.execute("""
        CREATE TABLE IF NOT EXISTS master_suppliers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id      INTEGER NOT NULL,
            supplier_code   VARCHAR(30),
            supplier_name   VARCHAR(200) NOT NULL,
            gstin           VARCHAR(20)  DEFAULT '',
            pan             VARCHAR(20)  DEFAULT '',
            email           VARCHAR(200) DEFAULT '',
            phone           VARCHAR(30)  DEFAULT '',
            address         TEXT         DEFAULT '',
            city            VARCHAR(100) DEFAULT '',
            state           VARCHAR(100) DEFAULT '',
            pincode         VARCHAR(10)  DEFAULT '',
            country         VARCHAR(100) DEFAULT '',
            payment_terms   VARCHAR(50)  DEFAULT '',
            currency_code   VARCHAR(10)  DEFAULT '',
            bank_name       VARCHAR(100) DEFAULT '',
            bank_account    VARCHAR(50)  DEFAULT '',
            bank_ifsc       VARCHAR(20)  DEFAULT '',
            bank_branch     VARCHAR(100) DEFAULT '',
            tds_applicable  INTEGER      DEFAULT 0,
            tds_section     VARCHAR(20)  DEFAULT '',
            tds_rate        REAL         DEFAULT 0.0,
            msme_registered INTEGER      DEFAULT 0,
            msme_number     VARCHAR(50)  DEFAULT '',
            remarks         TEXT         DEFAULT '',
            is_active       INTEGER      DEFAULT 1,
            created_by      INTEGER,
            created_at      TEXT,
            updated_by      INTEGER,
            updated_at      TEXT,
            is_deleted      INTEGER      DEFAULT 0
        )
    """)
    print("  [OK] master_suppliers table ensured.")

    # 2. tds_sections
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tds_sections (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id  INTEGER NOT NULL,
            section     VARCHAR(20)  NOT NULL,
            description VARCHAR(200) DEFAULT '',
            rate_pct    REAL         DEFAULT 0.0,
            is_active   INTEGER      DEFAULT 1
        )
    """)
    print("  [OK] tds_sections table ensured.")

    # 3. Missing columns
    _add_col_if_missing(cur, "rfqs",      "remarks", "TEXT DEFAULT ''")
    _add_col_if_missing(cur, "rfq_lines", "remarks", "TEXT DEFAULT ''")
    _add_col_if_missing(cur, "bid_items", "remarks", "TEXT DEFAULT ''")

    conn.commit()
    conn.close()
    print("Done.\n")


if __name__ == "__main__":
    # Migrate both DBs so either entry point works
    for path in [_root_db, _backend_db]:
        if os.path.exists(path):
            migrate(path)
        else:
            print(f"  [SKIP] Not found: {path}")
