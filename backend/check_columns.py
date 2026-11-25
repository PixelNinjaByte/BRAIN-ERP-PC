import os, re, glob, sqlite3, sys

backend_dir = os.path.dirname(os.path.abspath(__file__))

schema_file = os.path.join(backend_dir, "schema.sql")
with open(schema_file, "r", encoding="utf-8") as f:
    schema_sql = f.read()

mem_conn = sqlite3.connect(":memory:")
mem_conn.executescript(schema_sql)
cur = mem_conn.cursor()
tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
schema_info = {}
for t in tables:
    cols = [r[1] for r in cur.execute(f"PRAGMA table_info({t})").fetchall()]
    schema_info[t] = set(cols)

py_files = glob.glob(os.path.join(backend_dir, "**", "*.py"), recursive=True)

missing_cols = []

# Pattern for alias.column or table.column
# Also checking ErpDB.insert("table", dict) / ErpDB.update("table", dict)
for pf in py_files:
    if any(x in pf for x in ["analyze_sql", "check_db", "test_", "check_queries"]):
        continue
    rel = os.path.relpath(pf, backend_dir)
    with open(pf, "r", encoding="utf-8", errors="ignore") as f:
        src = f.read()

    # Find alias definitions like "FROM rfqs r" or "JOIN purchase_orders po"
    table_alias = {}
    for m in re.finditer(r'\b(?:FROM|JOIN)\s+([a-zA-Z0-9_]+)\s+(?:AS\s+)?([a-zA-Z0-9_]+)\b', src, re.IGNORECASE):
        tbl, alias = m.groups()
        if tbl in schema_info and alias.upper() not in ("ON", "WHERE", "LEFT", "RIGHT", "INNER", "OUTER", "JOIN", "SET"):
            table_alias[alias] = tbl

    # Find alias.col references
    for m in re.finditer(r'\b([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\b', src):
        prefix, col = m.groups()
        target_tbl = None
        if prefix in schema_info:
            target_tbl = prefix
        elif prefix in table_alias:
            target_tbl = table_alias[prefix]

        if target_tbl and col not in schema_info[target_tbl] and col != "*":
            missing_cols.append((rel, target_tbl, col))

    # Check ErpDB.insert("table", {key: ...})
    for m in re.finditer(r'ErpDB\.insert\s*\(\s*["\']([a-zA-Z0-9_]+)["\']\s*,\s*\{([^}]+)\}', src):
        tbl, dict_content = m.groups()
        if tbl in schema_info:
            for km in re.finditer(r'["\']([a-zA-Z0-9_]+)["\']\s*:', dict_content):
                k = km.group(1)
                if k not in schema_info[tbl]:
                    missing_cols.append((rel, tbl, k))

print("\n--- MISSING COLUMNS IN EXISTING TABLES ---")
seen = set()
for f, tbl, col in missing_cols:
    k = (tbl, col)
    if k not in seen:
        seen.add(k)
        print(f"Table '{tbl}' is missing column '{col}' (found in {f})")

print(f"\nTotal distinct missing columns: {len(seen)}")
