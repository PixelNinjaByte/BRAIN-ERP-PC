import os, re, glob, sqlite3, sys

backend_dir = os.path.dirname(os.path.abspath(__file__))

# Read schema.sql
schema_file = os.path.join(backend_dir, "schema.sql")
with open(schema_file, "r", encoding="utf-8") as f:
    schema_sql = f.read()

# Test applying schema.sql to an in-memory SQLite DB
mem_conn = sqlite3.connect(":memory:")
try:
    mem_conn.executescript(schema_sql)
    print("schema.sql loaded successfully into in-memory SQLite.")
except Exception as e:
    print(f"Error loading schema.sql: {e}")
    sys.exit(1)

# Get all tables and columns from schema.sql
cur = mem_conn.cursor()
tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
schema_info = {}
for t in tables:
    cols = [r[1] for r in cur.execute(f"PRAGMA table_info({t})").fetchall()]
    schema_info[t] = cols

# Scan all python files for SQL strings
py_files = glob.glob(os.path.join(backend_dir, "**", "*.py"), recursive=True)

# Find string literals that look like SQL
sql_stmt_re = re.compile(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'|"((?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER)\b.*?)"|\'((?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER)\b.*?)\'', re.DOTALL | re.IGNORECASE)

issues = []

for pf in py_files:
    if any(x in pf for x in ["analyze_sql", "check_db", "test_", "check_queries"]):
        continue
    rel = os.path.relpath(pf, backend_dir)
    with open(pf, "r", encoding="utf-8", errors="ignore") as f:
        src = f.read()

    # Find ErpDB.insert("table", ...), ErpDB.update("table", ...), ErpDB.delete("table", ...)
    for m in re.finditer(r'ErpDB\.(insert|update|delete)\s*\(\s*["\']([a-zA-Z0-9_]+)["\']', src):
        op, tbl = m.groups()
        if tbl not in schema_info:
            issues.append((rel, f"ErpDB.{op} on non-existent table '{tbl}'"))

    # Also search for explicit SELECT / INSERT / UPDATE / DELETE statements
    for m in sql_stmt_re.finditer(src):
        s = m.group(1) or m.group(2) or m.group(3) or m.group(4)
        if not s:
            continue
        s_clean = s.strip()
        if not re.match(r'^(SELECT|INSERT|UPDATE|DELETE|REPLACE)\b', s_clean, re.IGNORECASE):
            continue

        # Look for table names in FROM / JOIN / INTO / UPDATE
        for tm in re.finditer(r'\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-zA-Z0-9_]+)', s_clean, re.IGNORECASE):
            tbl = tm.group(1)
            if tbl.upper() in ("SELECT", "SET", "WHERE", "VALUES", "AS", "ON", "CNT_TBL", "DUAL"):
                continue
            if tbl not in schema_info:
                issues.append((rel, f"SQL references missing table '{tbl}' in query: {s_clean[:60]}..."))

print("\n--- ISSUES FOUND ---")
seen = set()
for f, issue in issues:
    k = (f, issue)
    if k not in seen:
        seen.add(k)
        print(f"[{f}] {issue}")

print(f"\nTotal distinct issues: {len(seen)}")
