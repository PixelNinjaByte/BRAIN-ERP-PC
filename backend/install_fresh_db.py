import os, sys, sqlite3, logging

backend_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(backend_dir)

sys.path.insert(0, root_dir)
sys.path.insert(0, backend_dir)

from core.installer import run_install, ensure_columns
from core.db import ErpDB

logging.basicConfig(level=logging.INFO)

backend_db = os.path.join(backend_dir, "brain_erp.db")
root_db = os.path.join(root_dir, "brain_erp.db")

for db_path in [backend_db, root_db]:
    print(f"\n==========================================")
    print(f"Installing database at: {db_path}")
    print(f"==========================================")
    
    # Run installation: applies schema.sql, seeds tcodes, creates company and admin user
    run_install(db_path=db_path, company_name="MSME Manufacturing")
    
    # Also run ensure_columns
    ErpDB.init(db_path)
    ensure_columns()
    
    # Verify tables count
    conn = sqlite3.connect(db_path)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print(f"Verified {len(tables)} tables installed in {db_path}")
    conn.close()

print("\nAll databases successfully installed and verified!")
