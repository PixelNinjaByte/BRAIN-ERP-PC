import os, re

php_installer = r"C:\Users\Yuvaraj\.gemini\antigravity\brain\284b3c65-2215-4488-b981-b444a71a643f\.user_uploaded\msme-erp-src\msme-erp\msme-erp\core\installer.php"
with open(php_installer, "r", encoding="utf-8", errors="ignore") as f:
    php_src = f.read()

# Pattern: "CREATE TABLE IF NOT EXISTS {$p}(\w+) ( ... ) ENGINE=InnoDB
# Or dbDelta tables
table_defs = {}
for m in re.finditer(r'CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+(?:\{?\$p\}?|`?\{?\$p\}?)([a-zA-Z0-9_]+)`?\s*\((.*?)\)\s*(?:ENGINE|\$charset|;)', php_src, re.DOTALL | re.IGNORECASE):
    tbl_name = m.group(1).lower()
    body = m.group(2).strip()
    table_defs[tbl_name] = body

print(f"Found {len(table_defs)} tables defined in installer.php:")
for t in sorted(table_defs.keys()):
    print(" ", t)
