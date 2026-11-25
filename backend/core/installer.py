"""
Brain ERP — Database Installer & Seeder
Port of PHP installer.php → Python + SQLite.

Run once on first launch. Idempotent — safe to run again on upgrade.
Creates all tables from schema.sql, then seeds default data:
  - Company, plant, warehouse
  - Units of measure
  - Leave types
  - Number series
  - Roles + T-codes + role_tcodes
  - Chart of accounts (India standard)
  - Fiscal year + 12 periods
  - Admin user with SUPER_ADMIN role

Original: Prigag Online Pvt. Ltd.
Port:     Python/SQLite standalone desktop version
"""

import os
import logging
from datetime import date, datetime
from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# T-CODE DEFINITIONS  — mirrors msme_erp_get_tcode_definitions() in PHP
# [tcode, module, description, file, icon, sort_order]
# ---------------------------------------------------------------------------
TCODE_DEFINITIONS = [
    # ADMIN
    ["SU01","ADMIN","User Maintenance","modules/admin/users","bi-person-gear",10],
    ["PFCG","ADMIN","Role Maintenance","modules/admin/roles","bi-shield-lock",11],
    ["OBY6","ADMIN","Company Configuration","modules/admin/company","bi-building",12],
    ["CMPY","ADMIN","Companies","modules/admin/companies","bi-buildings",16],
    ["NRIV","ADMIN","Number Range Maintenance","modules/admin/company","bi-hash",13],
    ["SM21","ADMIN","System Log","modules/admin/system_log","bi-journal-text",14],
    ["SREL","ADMIN","Release Strategy Config","modules/admin/release_strategy","bi-diagram-3",15],
    ["OS01","ADMIN","Org Structure","modules/admin/org_structure","bi-diagram-3-fill",17],
    ["APPROVALS","ADMIN","My Pending Approvals","modules/admin/my_approvals","bi-clipboard-check",16],
    ["MYACC","ADMIN","My Account","modules/admin/my_account","bi-person-badge",17],
    ["SU56","ADMIN","T-Code Access Requests","modules/admin/tcode_requests","bi-shield-check",20],
    ["SU3","ADMIN","My Preferences","modules/admin/my_preferences","bi-sliders",18],
    ["SU53","ADMIN","My Access","modules/admin/my_access","bi-key-fill",19],
    ["ZQDR","ADMIN","Quality/Price Deduction Reserve","modules/admin/qc_deduction_register","bi-shield-lock",18],
    ["ZMDC","ADMIN","Master Data Link Check","modules/admin/master_link_check","bi-diagram-3",19],
    ["ZMAT","ADMIN","Material Database Link","modules/admin/material_link","bi-box-seam",19],
    # HR
    ["PA01","HR","Create Employee","modules/hr/employee_master","bi-person-plus",20],
    ["PA30","HR","Employee Master Data","modules/hr/employee_master","bi-person-badge",21],
    ["OM01","HR","Organization Management","modules/hr/org_chart","bi-diagram-2",22],
    ["PT50","HR","Leave Management","modules/hr/leave","bi-calendar-check",23],
    ["PT60","HR","Attendance Sheet","modules/hr/attendance","bi-clock-history",24],
    ["PY01","HR","Process Payroll","modules/hr/payroll","bi-cash-stack",25],
    ["PC00","HR","Generate Payslips","modules/hr/payslips","bi-receipt",26],
    # MM
    ["MM01","MM","Create Material","modules/mm/material_create","bi-box-seam",30],
    ["MM02","MM","Change Material","modules/mm/material_edit","bi-pencil-square",32],
    ["MM03","MM","Display Material","modules/mm/material_view","bi-eye",31],
    ["OMSF","MM","Material Groups","modules/mm/material_groups","bi-collection",33],
    ["ZMMI","MM","Material Import / Export","modules/mm/material_import","bi-file-earmark-spreadsheet",34],
    ["ZMRP","MM","Reorder Report","modules/mm/material_reorder","bi-exclamation-diamond",35],
    ["XK01","MM","Create Supplier","modules/mm/supplier_create","bi-truck",32],
    ["XK02","MM","Change Supplier","modules/mm/supplier_edit","bi-truck",33],
    ["ME51N","MM","Create Purchase Requisition","modules/mm/pr_create","bi-file-earmark-plus",34],
    ["ME53N","MM","Display Purchase Requisition","modules/mm/pr_view","bi-file-earmark-text",35],
    ["ME41","MM","Create RFQ","modules/mm/rfq_create","bi-envelope-paper",36],
    ["ME47","MM","Compare Quotations","modules/mm/rfq_compare","bi-bar-chart-steps",37],
    ["ME21N","MM","Create Purchase Order","modules/mm/po_create","bi-cart-plus",38],
    ["ME23N","MM","Display Purchase Orders","modules/mm/po_view","bi-bag",39],
    ["ME2M","MM","Purchase Orders by Material","modules/mm/po_by_material","bi-bag-check",40],
    ["MIGO","MM","Goods Movement (GRN)","modules/mm/goods_movement","bi-arrow-down-circle",41],
    ["PRTN","MM","Purchase Return","modules/mm/purchase_return","bi-arrow-return-left",42],
    ["MR01","MM","Invoice Verification","modules/mm/supplier_invoice","bi-receipt-cutoff",42],
    ["ML81N","MM","Service Entry Sheet","modules/mm/service_entry","bi-clipboard-check",43],
    ["VK11","MM","Price List","modules/mm/price_list","bi-tags",44],
    ["LT01","MM","Stock Transfer","modules/inventory/transfer","bi-arrow-left-right",45],
    ["MMBE","MM","Stock Overview","modules/inventory/stock_overview","bi-boxes",46],
    ["MI01","MM","Create Physical Inventory","modules/inventory/pi_create","bi-clipboard-check",47],
    ["MI07","MM","Post Physical Inventory","modules/inventory/pi_post","bi-clipboard-data",48],
    ["BID01","MM","Create Bid / Tender","modules/mm/bid_create","bi-clipboard2-check",49],
    ["BID02","MM","Manage Bids / Tenders","modules/mm/bid_view","bi-clipboard-data-fill",49],
    ["ME2PN","MM","MM Pending Report","modules/mm/pending_report","bi-hourglass-split",49],
    ["ZMMR","MM","MM Procurement Reports","modules/mm/mm_reports","bi-clipboard-data",49],
    # SD
    ["XD01","SD","Create Customer","modules/sd/customer_create","bi-person-lines-fill",50],
    ["QB01","SD","Create Quotation","modules/sd/quotation_create","bi-file-earmark-plus",52],
    ["VA01","SD","Create Sales Order","modules/sd/so_create","bi-cart",53],
    ["VA03","SD","Display Sales Orders","modules/sd/so_view","bi-bag",54],
    ["VL01N","SD","Create Delivery Note","modules/sd/delivery_note","bi-truck-flatbed",55],
    ["VF01","SD","Create Sales Invoice","modules/sd/invoice_create","bi-file-earmark-text",56],
    ["SRTN","SD","Sales Return","modules/sd/sales_return","bi-arrow-return-right",57],
    ["VF03","SD","Display Invoices","modules/sd/invoice_view","bi-eye",57],
    ["EWB01","SD","Generate E-Way Bill","modules/sd/eway_bill","bi-truck",58],
    # BOM
    ["CS01","BOM","Create BOM","modules/bom/bom_create","bi-diagram-3-fill",70],
    ["CS03","BOM","Display BOM","modules/bom/bom_view","bi-eye",71],
    ["CA01","BOM","Create Routing","modules/bom/routing","bi-signpost-split",72],
    ["CR01","BOM","Create Work Center","modules/bom/work_center","bi-gear-wide-connected",73],
    # PRODUCTION
    ["CO01","PROD","Create Work Order","modules/production/wo_create","bi-gear",80],
    ["CO13","PROD","Work Order Completion","modules/production/wo_completion","bi-check2-circle",81],
    ["COOIS","PROD","Production Report","modules/production/production_report","bi-bar-chart",82],
    ["PP01","PROD","Production Planning","modules/production/planning","bi-calendar-week",83],
    ["CK13N","PROD","Production Cost Report","modules/production/cost_report","bi-currency-exchange",84],
    # FINANCE
    ["FS00","FI","Chart of Accounts","modules/finance/coa","bi-diagram-3",90],
    ["FB50","FI","Post Journal Entry","modules/finance/journal_post","bi-journal-plus",91],
    ["F-28","FI","Customer Receipt","modules/finance/receipts","bi-cash",92],
    ["F-53","FI","Supplier Payment","modules/finance/payments","bi-credit-card",93],
    ["FBL1N","FI","Supplier Open Items","modules/finance/ar_ap","bi-file-earmark-bar-chart",94],
    ["FBL5N","FI","Customer Open Items","modules/finance/ar_ap","bi-file-earmark-bar-chart",95],
    ["F.01","FI","Balance Sheet","modules/finance/reports","bi-graph-up",96],
    ["S_ALR","FI","P&L Statement","modules/finance/reports","bi-graph-up-arrow",97],
    ["GSTR1","FI","GSTR-1 Outward Supplies","modules/finance/gst","bi-file-earmark-pdf",98],
    ["GSTR3B","FI","GSTR-3B Summary Return","modules/finance/gst","bi-file-earmark-pdf",99],
    ["GSTR2B","FI","GSTR-2B Reconciliation","modules/finance/gst","bi-file-earmark-check",103],
    ["KS01","FI","Create Cost Center","modules/finance/cost_center","bi-plus-square",100],
    ["KS02","FI","Change Cost Center","modules/finance/cost_center","bi-pencil-square",101],
    ["KS03","FI","Display Cost Centers","modules/finance/cost_center","bi-diagram-2",102],
    ["F.08","FI","Trial Balance","modules/finance/reports","bi-table",107],
    ["FAGLL03","FI","GL Account Statement","modules/finance/reports","bi-journal-richtext",108],
    ["ZDAY","FI","Day Book","modules/finance/reports","bi-calendar-day",109],
    ["ZCBK","FI","Cash and Bank Book","modules/finance/reports","bi-bank",130],
    ["F.CF","FI","Cash Flow Statement","modules/finance/reports","bi-cash-stack",131],
    ["KSB1","FI","Cost Center P&L","modules/finance/reports","bi-pie-chart",132],
    ["OB52","FI","Fiscal Years","modules/finance/controls","bi-calendar-check",133],
    ["FAGLGVTR","FI","Year-End Closing","modules/finance/controls","bi-lock",134],
    ["OBYC","FI","GL Account Determination","modules/finance/controls","bi-diagram-2",135],
    ["FINSET","FI","Finance Settings","modules/finance/controls","bi-sliders",136],
    ["FF67","FI","Bank Reconciliation","modules/finance/bank","bi-bank2",146],
    ["TDS01","FI","TDS Register","modules/finance/tds","bi-percent",147],
    ["AFAB","FI","Run Depreciation","modules/finance/assets_dep","bi-arrow-down-circle",149],
    ["AW01N","FI","Asset Register","modules/finance/assets_dep","bi-building-gear",150],
    # QM
    ["QM01","QM","Inspection Lots","modules/quality/inspection","bi-clipboard-data",110],
    ["QA32","QM","Record Inspection Results","modules/quality/results","bi-check-square",111],
    ["QF01","QM","Non-Conformance Report","modules/quality/ncr","bi-exclamation-triangle",112],
    # PM
    ["IE01","PM","Create Asset","modules/pm/asset_create","bi-cpu",120],
    ["IW21","PM","Create Maintenance Order","modules/pm/maintenance_order","bi-tools",121],
    ["IW38","PM","Maintenance Report","modules/pm/maintenance_report","bi-clipboard2-pulse",122],
    ["IP01","PM","Maintenance Plan","modules/pm/maintenance_plan","bi-calendar-range",123],
    # PS
    ["CJ01","PS","Create Project","modules/ps/project_create","bi-kanban",130],
    ["CJ02","PS","Project Master","modules/ps/project_detail","bi-diagram-3",131],
    ["CJ03","PS","Project List","modules/ps/project_list","bi-list-task",132],
    ["CJ30","PS","Project Budget Release","modules/ps/project_budget","bi-cash-coin",133],
    ["CJI3","PS","Project Actual Cost Report","modules/ps/project_cost_report","bi-graph-up",134],
    ["CN01","PS","Project Activities","modules/ps/project_activity","bi-diagram-2",135],
    # COMM
    ["SBWP","COMM","Business Workplace (Inbox)","modules/comm/inbox","bi-inbox-fill",140],
    ["SO01","COMM","Compose Message","modules/comm/compose","bi-envelope-plus",141],
]

DEFAULT_ROLES = [
    ["SUPER_ADMIN","Super Administrator","Full system access"],
    ["HR_MANAGER","HR Manager","Full HR module access"],
    ["HR_EXECUTIVE","HR Executive","Employee & Attendance management"],
    ["PAYROLL_MANAGER","Payroll Manager","Payroll processing & reports"],
    ["PURCHASE_MANAGER","Purchase Manager","PO approval & supplier management"],
    ["PURCHASE_EXEC","Purchase Executive","PR & PO creation"],
    ["STORE_MANAGER","Store Manager","GRN, stock management"],
    ["SALES_MANAGER","Sales Manager","SO approval, customer management"],
    ["SALES_EXEC","Sales Executive","Quotation, SO creation"],
    ["ACCOUNTS_MANAGER","Accounts Manager","Journal posting, payments"],
    ["ACCOUNTS_EXEC","Accounts Executive","Invoice verification"],
    ["PRODUCTION_MANAGER","Production Manager","WO approval, production"],
    ["PRODUCTION_EXEC","Production Executive","WO creation, goods issue"],
    ["QC_MANAGER","Quality Manager","Inspection, NCR"],
    ["PLANT_MANAGER","Plant Maintenance Manager","Assets & maintenance"],
    ["MD","Managing Director","Top level approvals"],
    ["DIRECTOR","Director","Senior approval authority"],
    ["GM","General Manager","Department head approval"],
    ["VIEWER","Read Only Viewer","View all, no modifications"],
]

DEFAULT_UOMS = [
    ["NOS","Numbers","PIECE"],["KG","Kilograms","WEIGHT"],["GM","Grams","WEIGHT"],
    ["MT","Metric Tons","WEIGHT"],["LTR","Litres","VOLUME"],["ML","Millilitres","VOLUME"],
    ["MTR","Metres","LENGTH"],["BOX","Box","PIECE"],["PKT","Packet","PIECE"],
    ["SET","Set","PIECE"],["PCS","Pieces","PIECE"],["BAG","Bags","PIECE"],
    ["ROLL","Roll","PIECE"],["DRM","Drum","VOLUME"],["HRS","Hours","TIME"],
    ["DAY","Days","TIME"],["SQM","Sq Metres","AREA"],
]

DEFAULT_LEAVE_TYPES = [
    ["SL","Sick Leave",12,1,0],
    ["CL","Casual Leave",12,1,0],
    ["EL","Earned / Privilege Leave",15,1,1],
    ["ML","Maternity Leave",182,1,0],
    ["PL","Paternity Leave",15,1,0],
    ["LWP","Leave Without Pay",0,0,0],
]

INDIA_COA = [
    ["1000","ASSETS","ASSET","Balance Sheet",1,1,"DEBIT"],
    ["1100","Current Assets","ASSET","Current Asset",0,2,"DEBIT"],
    ["1101","Cash in Hand","ASSET","Current Asset",0,3,"DEBIT"],
    ["1102","Bank Account","ASSET","Bank",0,3,"DEBIT"],
    ["1110","Accounts Receivable","ASSET","Debtors",0,3,"DEBIT"],
    ["1130","GST Input Credit","ASSET","Tax",0,3,"DEBIT"],
    ["1140","Inventory - Raw Material","ASSET","Inventory",0,3,"DEBIT"],
    ["1141","Inventory - Finished Goods","ASSET","Inventory",0,3,"DEBIT"],
    ["1200","Fixed Assets","ASSET","Fixed Asset",1,2,"DEBIT"],
    ["1201","Plant & Machinery","ASSET","Fixed Asset",0,3,"DEBIT"],
    ["2000","LIABILITIES","LIABILITY","Balance Sheet",1,1,"CREDIT"],
    ["2100","Current Liabilities","LIABILITY","Current Liab",1,2,"CREDIT"],
    ["2101","Accounts Payable","LIABILITY","Creditors",0,3,"CREDIT"],
    ["2110","GST Payable","LIABILITY","Tax",0,3,"CREDIT"],
    ["2130","PF Payable","LIABILITY","Statutory",0,3,"CREDIT"],
    ["2140","Salary Payable","LIABILITY","Payroll",0,3,"CREDIT"],
    ["3000","EQUITY","EQUITY","Balance Sheet",1,1,"CREDIT"],
    ["3001","Share Capital","EQUITY","Capital",0,2,"CREDIT"],
    ["3002","Retained Earnings","EQUITY","Reserves",0,2,"CREDIT"],
    ["4000","INCOME","INCOME","P&L",1,1,"CREDIT"],
    ["4001","Sales Revenue","INCOME","Sales",0,2,"CREDIT"],
    ["4002","Service Revenue","INCOME","Sales",0,2,"CREDIT"],
    ["4003","Other Income","INCOME","Other",0,2,"CREDIT"],
    ["5000","EXPENSES","EXPENSE","P&L",1,1,"DEBIT"],
    ["5001","Cost of Goods Sold","EXPENSE","COGS",0,2,"DEBIT"],
    ["5010","Salaries & Wages","EXPENSE","Personnel",0,2,"DEBIT"],
    ["5020","Rent","EXPENSE","Overheads",0,2,"DEBIT"],
    ["5021","Electricity","EXPENSE","Overheads",0,2,"DEBIT"],
    ["5030","Freight & Transport","EXPENSE","Selling",0,2,"DEBIT"],
    ["5040","Depreciation","EXPENSE","Non-Cash",0,2,"DEBIT"],
    ["5041","Bank Charges","EXPENSE","Finance",0,2,"DEBIT"],
    ["5050","Repairs & Maintenance","EXPENSE","Maintenance",0,2,"DEBIT"],
]

DOC_TYPES = ["PO","PR","GRN","SO","INV","DN","QT","WO","JE","PAY",
             "STO","PI","MO","NCR","SINV","EMP","SES","SESACC","SRVITEM","RFQ","QLOT","BID"]


# ---------------------------------------------------------------------------
# MAIN INSTALL FUNCTION
# ---------------------------------------------------------------------------
def run_install(db_path: str = "brain_erp.db", company_name: str = "My Company") -> None:
    """
    Create all tables and seed default data.
    Idempotent — skips anything already present.
    """
    from core.db import ErpDB
    ErpDB.init(db_path)

    _apply_schema(db_path)
    _seed_tcodes()
    _seed_data(company_name)
    logger.info("Brain ERP installation complete.")


def _apply_schema(db_path: str) -> None:
    """Execute schema.sql against the SQLite database."""
    import sqlite3, os
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "schema.sql"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "schema.sql"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "schema_sqlite.sql"),
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "schema_sqlite.sql"),
    ]
    schema_path = next((p for p in candidates if os.path.exists(p)), None)
    if not schema_path:
        raise FileNotFoundError(f"schema.sql not found in candidate paths: {candidates}")
    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()
    conn = sqlite3.connect(db_path)
    conn.executescript(sql)
    conn.commit()
    conn.close()
    logger.info("Schema applied from %s", schema_path)


# ---------------------------------------------------------------------------
# SEED T-CODES — upsert, runs on every install/upgrade
# ---------------------------------------------------------------------------
def _seed_tcodes() -> None:
    from core.db import ErpDB
    for tc, mod, desc, file_, icon, sort in TCODE_DEFINITIONS:
        existing = ErpDB.fetch_value("SELECT id FROM tcodes WHERE tcode=?", (tc,))
        row = {
            "tcode": tc, "module": mod, "description": desc,
            "php_file": file_, "icon": icon, "is_active": 1, "sort_order": sort,
        }
        if existing:
            del row["tcode"]   # don't update the PK
            ErpDB.update("tcodes", row, "id=?", (int(existing),))
        else:
            ErpDB.insert("tcodes", row)
    logger.info("T-codes synced (%d definitions)", len(TCODE_DEFINITIONS))


# ---------------------------------------------------------------------------
# SEED DATA — only runs on a fresh install (companies table empty)
# ---------------------------------------------------------------------------
def _seed_data(company_name: str) -> None:
    from core.db import ErpDB

    already_seeded = ErpDB.fetch_value("SELECT COUNT(*) FROM companies")
    if int(already_seeded or 0) > 0:
        # Still ensure UOMs exist (self-healing, same as PHP)
        _ensure_uoms()
        _ensure_leave_types(1)
        return

    logger.info("Seeding default data for new installation…")

    # 1 — Company
    company_id = ErpDB.insert("companies", {
        "code": "CO001", "name": company_name,
        "legal_name": company_name + " Pvt. Ltd.",
        "country": "India", "country_code": "IN",
        "currency_code": "INR",
        "fiscal_year_start": f"{date.today().year}-04-01",
        "is_active": 1,
    })

    # 2 — Plant / Branch / Warehouse
    plant_id = ErpDB.insert("plants", {
        "company_id": company_id, "code": "P001", "name": "Main Plant", "is_active": 1,
    })
    ErpDB.insert("branches", {
        "company_id": company_id, "plant_id": plant_id,
        "code": "HQ", "name": "Head Office", "is_active": 1,
    })
    ErpDB.insert("warehouses", {
        "company_id": company_id, "code": "WH01",
        "name": "Main Warehouse", "wh_type": "GENERAL", "is_active": 1,
    })

    # 3 — Units of Measure
    _ensure_uoms()

    # 4 — Leave Types
    _ensure_leave_types(company_id)

    # 5 — Number Series
    year = date.today().year
    prefix_override = {"SESACC": "ACC", "SRVITEM": "SRV"}
    for dt in DOC_TYPES:
        ErpDB.insert("number_series", {
            "company_id": company_id,
            "doc_type": dt,
            "prefix": prefix_override.get(dt, dt),
            "current_num": 0,
            "padding": 6,
            "fiscal_year": year,
        })

    # 6 — Roles
    role_id_map: dict[str, int] = {}
    for code, name, desc in DEFAULT_ROLES:
        rid = ErpDB.insert("roles", {
            "company_id": company_id, "code": code,
            "name": name, "description": desc, "is_system": 1,
        })
        role_id_map[code] = rid

    # 7 — Assign ALL T-codes to SUPER_ADMIN
    super_id = role_id_map.get("SUPER_ADMIN")
    if super_id:
        all_tcodes = ErpDB.fetch_all("SELECT id FROM tcodes WHERE is_active=1")
        for tc in all_tcodes:
            try:
                ErpDB.insert("role_tcodes", {"role_id": super_id, "tcode_id": tc["id"]})
            except Exception:
                pass  # already exists

    # 8 — Chart of Accounts
    for code, name, acct_type, group, is_group, level, balance in INDIA_COA:
        exists = ErpDB.fetch_value(
            "SELECT COUNT(*) FROM chart_of_accounts WHERE company_id=? AND account_code=?",
            (company_id, code),
        )
        if not int(exists or 0):
            ErpDB.insert("chart_of_accounts", {
                "company_id": company_id, "account_code": code, "account_name": name,
                "account_type": acct_type, "account_group": group,
                "is_group": is_group, "level": level,
                "normal_balance": balance, "is_active": 1,
            })

    # 9 — Fiscal Year + 12 monthly periods
    _seed_fiscal_year(company_id, f"{date.today().year}-04-01")

    # 10 — Release Strategies (PO + SO standard approval)
    _seed_release_strategies(company_id, role_id_map)

    # 11 — Default Admin User
    _seed_admin_user(company_id, super_id)

    logger.info("Default data seeded for company '%s' (id=%d)", company_name, company_id)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def _ensure_uoms() -> None:
    from core.db import ErpDB
    existing = set(
        r["code"].upper() for r in ErpDB.fetch_all("SELECT code FROM units_of_measure")
    )
    for code, name, uom_type in DEFAULT_UOMS:
        if code.upper() not in existing:
            ErpDB.insert("units_of_measure", {
                "code": code, "name": name, "uom_type": uom_type, "is_active": 1,
            })


def _ensure_leave_types(company_id: int) -> None:
    from core.db import ErpDB
    count = int(ErpDB.fetch_value(
        "SELECT COUNT(*) FROM leave_types WHERE company_id=?", (company_id,)
    ) or 0)
    if count > 0:
        return
    for code, name, days, paid, carry in DEFAULT_LEAVE_TYPES:
        ErpDB.insert("leave_types", {
            "company_id": company_id, "code": code, "name": name,
            "days_per_year": days, "is_paid": paid,
            "is_carry_forward": carry, "is_active": 1,
        })


def _seed_fiscal_year(company_id: int, start_date_str: str) -> None:
    from core.db import ErpDB
    start = date.fromisoformat(start_date_str)
    end   = start + relativedelta(years=1) - relativedelta(days=1)
    label = f"FY{start.year}-{end.year}" if start.year != end.year else f"FY{start.year}"

    fy_id = ErpDB.insert("fiscal_years", {
        "company_id": company_id, "name": label,
        "start_date": start.isoformat(), "end_date": end.isoformat(), "is_closed": 0,
    })
    for i in range(12):
        ps = start + relativedelta(months=i)
        pe = ps + relativedelta(months=1) - relativedelta(days=1)
        ErpDB.insert("accounting_periods", {
            "fiscal_year_id": fy_id, "period_number": i + 1,
            "name": ps.strftime("%B %Y"),
            "start_date": ps.isoformat(), "end_date": pe.isoformat(), "is_closed": 0,
        })


def _seed_release_strategies(company_id: int, role_id_map: dict) -> None:
    from core.db import ErpDB
    # PO standard: 4-level
    po_strat = ErpDB.insert("release_strategies", {
        "company_id": company_id, "doc_type": "purchase_order",
        "strategy_code": "PO_STD", "description": "Standard PO Release", "is_active": 1,
    })
    po_levels = [
        [1,"L1","Purchase Executive Approval","PURCHASE_EXEC",0,50000],
        [2,"L2","Purchase Manager Approval","PURCHASE_MANAGER",0,500000],
        [3,"L3","GM Approval","GM",500001,2000000],
        [4,"L4","MD Approval","MD",2000001,99999999],
    ]
    for lvl, code, desc, role_code, frm, to in po_levels:
        rid = role_id_map.get(role_code)
        if rid:
            ErpDB.insert("release_levels", {
                "strategy_id": po_strat, "level_num": lvl, "level_code": code,
                "description": desc, "role_id": rid, "amount_from": frm, "amount_to": to,
            })

    # SO standard: 1-level (Sales Manager)
    so_strat = ErpDB.insert("release_strategies", {
        "company_id": company_id, "doc_type": "sales_order",
        "strategy_code": "SO_STD", "description": "Standard SO Release", "is_active": 1,
    })
    sm_rid = role_id_map.get("SALES_MANAGER")
    if sm_rid:
        ErpDB.insert("release_levels", {
            "strategy_id": so_strat, "level_num": 1, "level_code": "L1",
            "description": "Sales Manager", "role_id": sm_rid,
            "amount_from": 0, "amount_to": 99999999,
        })


def _seed_admin_user(company_id: int, super_role_id: int) -> None:
    """Create the initial admin user (username: admin, password: admin@123)."""
    from core.db import ErpDB
    from core.auth import ErpAuth

    exists = ErpDB.fetch_value("SELECT COUNT(*) FROM users WHERE username='admin'")
    if int(exists or 0):
        return

    user_id = ErpDB.insert("users", {
        "company_id": company_id, "branch_id": 1,
        "username": "admin", "email": "admin@brainerp.local",
        "full_name": "System Administrator", "is_active": 1,
    })
    # Assign SUPER_ADMIN role
    if super_role_id:
        ErpDB.insert("user_roles", {"user_id": user_id, "role_id": super_role_id})

    # Set default password: admin@123
    ErpAuth.set_password(user_id, "admin@123")
    logger.info(
        "Admin user created — username: admin | password: admin@123  "
        "(CHANGE THIS on first login via My Account → Change Password)"
    )


# ---------------------------------------------------------------------------
# UPGRADE — adds missing columns on existing installs (same as PHP ensure_columns)
# ---------------------------------------------------------------------------
def ensure_columns() -> None:
    """
    Add any columns that exist in schema.sql but are missing on an older DB.
    Safe to run on every startup.
    """
    from core.db import ErpDB
    patches = {
        "items": {
            "manufacturer":    "TEXT",
            "is_active":       "INTEGER DEFAULT 1",
            "is_deleted":      "INTEGER DEFAULT 0",
            "moving_avg_price":"REAL DEFAULT 0",
            "gst_rate":        "REAL DEFAULT 0",
        },
        "suppliers": {
            "status":    "TEXT DEFAULT 'ACTIVE'",
            "is_deleted":"INTEGER DEFAULT 0",
            "created_by":"INTEGER",
        },
        "purchase_orders": {
            "doc_token":            "TEXT",
            "supplier_notified_at": "TEXT",
            "discount_amount":      "REAL DEFAULT 0",
            "round_off":            "REAL DEFAULT 0",
        },
        "quotations": {
            "public_token":      "TEXT",
            "sent_at":           "TEXT",
            "accepted_at":       "TEXT",
            "negotiation_round": "INTEGER DEFAULT 0",
            "total_amount":      "REAL DEFAULT 0",
            "tax_amount":        "REAL DEFAULT 0",
            "converted_so_id":   "INTEGER",
            "customer_email":    "TEXT",
            "source":            "TEXT DEFAULT 'MANUAL'",
        },
        "rfqs": {
            "quotes_finalized_at":    "TEXT",
            "quotes_finalized_by":    "INTEGER",
            "auto_compare_at":        "TEXT",
            "auto_award_status":      "TEXT",
            "auto_award_at":          "TEXT",
            "auto_award_trigger":     "TEXT",
            "auto_award_supplier_id": "INTEGER",
            "auto_award_po_id":       "INTEGER",
        },
        "fin_notes": {
            "offset_key": "TEXT",
            "ret_type":   "TEXT",
            "ret_id":     "INTEGER",
        },
    }
    import sqlite3
    conn = sqlite3.connect(ErpDB._db_path)
    for table, cols in patches.items():
        try:
            existing = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        except Exception:
            continue
        for col, ddl in cols.items():
            if col not in existing:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                    logger.debug("Added column %s.%s", table, col)
                except Exception as e:
                    logger.warning("Could not add %s.%s: %s", table, col, e)
    conn.commit()
    conn.close()