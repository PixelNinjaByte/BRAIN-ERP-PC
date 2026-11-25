"""
backend/core/finance_engine.py
------------------------------
Subset of core/finance_engine.php needed by GRN (MIGO) and Supplier Invoice (MR01):

  fin_round / fin_cents / fin_opt / fin_acct
  assert_period_open            posting-period / lock-date control
  post_journal                  the GL posting engine (balanced, validated, idempotent per source doc)
  gst_split / gst_log           CGST-SGST / IGST split and the inward GST register
  fin_post_grn                  Dr Inventory / Cr GR-IR   (RTV 122 reverses it)
  fin_post_supplier_invoice     Dr GR-IR, Dr input GST - Cr Payables, Cr TDS payable (RCM / ITC aware)
  three_way_match               PO price x goods received vs invoice base
  fin_try_post                  run a posting inside a SAVEPOINT; never blocks the caller

NOT ported yet (add when their screens are converted): sales invoice, payments, journal
reversal / release, fiscal-year & period maintenance, gl_determination auto-seeding.
If gl_determination is empty, fin_acct raises with the same "configure OBYC" message as PHP.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime
from typing import Any, Callable, Optional

from backend.core.auth import ErpAuth
from backend.core.db import ErpDB
from backend.core.util import get_option, has_col   

log = logging.getLogger("erp.finance")


class FinanceError(RuntimeError):
    """A business-rule failure while posting (closed period, missing GL mapping, unbalanced journal)."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def fin_cents(v: Any) -> int:
    return int(round(float(v or 0) * 100))


def fin_round(v: Any) -> float:
    return round(float(v or 0), 2)


def fin_opt(key: str, default: Any = "") -> Any:
    return get_option("msme_erp_fin_" + key, default)


def _uid() -> int:
    return int((ErpAuth.user() or {}).get("id") or 0)


def _cid() -> int:
    return int((ErpAuth.user() or {}).get("company_id") or 0)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_acct_cache: dict[str, int] = {}


def fin_acct(key: str, company_id: Optional[int] = None) -> int:
    """GL account id for a determination key (OBYC). Raises when it cannot be resolved."""
    company_id = company_id or _cid()
    ck = f"{company_id}:{key}"
    if ck in _acct_cache:
        return _acct_cache[ck]
    acc = ErpDB.fetch_value(
        "SELECT d.account_id FROM gl_determination d JOIN chart_of_accounts c ON c.id=d.account_id "
        "WHERE d.company_id=? AND d.key_code=?", [company_id, key])
    if not acc:
        raise FinanceError(f"GL account determination for '{key}' is not configured (T-code OBYC).")
    _acct_cache[ck] = int(acc)
    return int(acc)


def clear_acct_cache() -> None:
    _acct_cache.clear()


def mat_inventory_account(item_id: int, company_id: int) -> int:
    """Material group's inventory account, else the company's INVENTORY determination."""
    acc = 0
    if has_col("material_groups", "inventory_account_id"):
        acc = int(ErpDB.fetch_value(
            "SELECT c.id FROM items i JOIN material_groups g ON g.id=i.material_group_id "
            "JOIN chart_of_accounts c ON c.id=g.inventory_account_id AND c.company_id=? "
            "AND COALESCE(c.is_active,1)=1 WHERE i.id=?", [company_id, item_id]) or 0)
    return acc or fin_acct("INVENTORY", company_id)


# ---------------------------------------------------------------------------
# period control
# ---------------------------------------------------------------------------
def fiscal_year_for(d: str, company_id: Optional[int] = None) -> Optional[dict]:
    company_id = company_id or _cid()
    return ErpDB.fetch_one(
        "SELECT * FROM fiscal_years WHERE company_id=? AND ? BETWEEN start_date AND end_date ORDER BY id LIMIT 1",
        [company_id, d])


def assert_period_open(d: str, company_id: Optional[int] = None) -> Optional[dict]:
    company_id = company_id or _cid()
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        raise FinanceError("Invalid posting date.")
    lock = str(fin_opt(f"lock_date_{company_id}", "") or "")
    if lock and d <= lock:
        raise FinanceError(f"Posting date {d} is on or before the posting lock date ({lock}).")
    if not int(ErpDB.fetch_value("SELECT COUNT(*) FROM fiscal_years WHERE company_id=?", [company_id]) or 0):
        return None   # nothing configured yet -> do not block (legacy installs)
    fy = fiscal_year_for(d, company_id)
    if not fy:
        raise FinanceError(f"Posting date {d} is outside every defined fiscal year. "
                           f"Create the fiscal year first (T-code OB52).")
    if fy.get("is_closed"):
        raise FinanceError(f"Fiscal year {fy['name']} is closed - posting to {d} is not allowed.")
    per = ErpDB.fetch_one(
        "SELECT * FROM accounting_periods WHERE fiscal_year_id=? AND ? BETWEEN start_date AND end_date LIMIT 1",
        [int(fy["id"]), d])
    if per and per.get("is_closed"):
        raise FinanceError(f"Accounting period {per['name']} is closed - posting to {d} is not allowed.")
    return fy


# ---------------------------------------------------------------------------
# the posting engine
# ---------------------------------------------------------------------------
def line(account_id: int, debit: float = 0, credit: float = 0, narration: str = "",
         party_type: Optional[str] = None, party_id: Optional[int] = None) -> dict:
    return {"account_id": account_id, "debit": debit, "credit": credit, "narration": narration,
            "party_type": party_type, "party_id": party_id}


def post_journal(hdr: dict, lines: list[dict]) -> int:
    """
    hdr: company_id, entry_date, narration, entry_type (JOURNAL|AUTO|...), source_module, source_id,
         status (POSTED|PENDING_APPROVAL), cost-centre columns, skip_period_check
    Returns the journal entry id. A source document is posted to the GL once (idempotent).
    """
    company_id = int(hdr.get("company_id") or _cid())
    d = str(hdr.get("entry_date") or date.today().isoformat())
    source = hdr.get("source_module")
    source_id = int(hdr["source_id"]) if hdr.get("source_id") is not None else None

    if source and source_id:
        dup = ErpDB.fetch_value(
            "SELECT id FROM journal_entries WHERE company_id=? AND source_module=? AND source_id=? "
            "AND status IN ('POSTED','PENDING_APPROVAL') AND COALESCE(entry_type,'')<>'REVERSAL' "
            "AND reversed_by_id IS NULL LIMIT 1", [company_id, source, source_id])
        if dup:
            return int(dup)

    fy = fiscal_year_for(d, company_id) if hdr.get("skip_period_check") else assert_period_open(d, company_id)

    clean, dr_c, cr_c = [], 0, 0
    for ln in lines:
        acc = int(ln.get("account_id") or 0)
        dr, cr = fin_cents(ln.get("debit")), fin_cents(ln.get("credit"))
        if dr == 0 and cr == 0:
            continue
        if dr < 0 or cr < 0:
            raise FinanceError("Negative amounts are not allowed in a journal line.")
        if dr > 0 and cr > 0:
            raise FinanceError("A journal line cannot carry both a debit and a credit - use two lines.")
        if acc <= 0:
            raise FinanceError("Every journal line needs a GL account.")
        clean.append({"account_id": acc, "dr": dr, "cr": cr, "narration": str(ln.get("narration") or ""),
                      "party_type": ln.get("party_type"), "party_id": ln.get("party_id")})
        dr_c += dr
        cr_c += cr
    if len(clean) < 2:
        raise FinanceError("A journal entry needs at least two lines.")
    if dr_c != cr_c:
        raise FinanceError(f"Journal is not balanced: debit {dr_c/100:,.2f}, credit {cr_c/100:,.2f} "
                           f"(difference {abs(dr_c-cr_c)/100:,.2f}).")
    if dr_c == 0:
        raise FinanceError("Journal total is zero.")

    ids = sorted({c["account_id"] for c in clean})
    acc_rows = ErpDB.fetch_all(
        "SELECT id, account_code, account_name, is_group, is_active, block_posting, recon_type "
        f"FROM chart_of_accounts WHERE company_id=? AND id IN ({','.join(str(int(i)) for i in ids)})",
        [company_id])
    by_id = {int(r["id"]): r for r in acc_rows}
    manual = (hdr.get("entry_type") or "JOURNAL") == "JOURNAL"
    for aid in ids:
        a = by_id.get(aid)
        if not a:
            raise FinanceError(f"GL account #{aid} does not exist in this company.")
        if a.get("is_group"):
            raise FinanceError(f"Account {a['account_code']} is a group account - posting is not allowed.")
        if not a.get("is_active"):
            raise FinanceError(f"Account {a['account_code']} is inactive.")
        if a.get("block_posting"):
            raise FinanceError(f"Account {a['account_code']} is blocked for posting.")
        if manual and a.get("recon_type") in ("CUSTOMER", "VENDOR") and not hdr.get("allow_recon_accounts"):
            raise FinanceError(f"Account {a['account_code']} is a reconciliation (control) account - post "
                               f"through invoices, receipts and payments instead of a manual journal.")

    je_num = ErpDB.get_next_number(company_id, "JE", int(d[:4]))
    status = hdr.get("status") or "POSTED"
    je_id = ErpDB.insert("journal_entries", {
        "company_id": company_id, "entry_number": je_num, "entry_date": d,
        "entry_type": hdr.get("entry_type") or "JOURNAL",
        "source_module": source, "source_id": source_id,
        "narration": str(hdr.get("narration") or ""),
        "total_debit": dr_c / 100, "total_credit": cr_c / 100, "status": status,
        "posted_by": _uid() if status == "POSTED" else None,
        "posted_at": _now() if status == "POSTED" else None,
        "created_by": _uid(),
        "fiscal_year_id": (fy or {}).get("id"),
        "category_id": hdr.get("category_id"), "unit_id": hdr.get("unit_id"),
        "department_id": hdr.get("department_id"), "project_id": hdr.get("project_id"),
        "wbs_element_id": hdr.get("wbs_element_id"),
        "currency_code": hdr.get("currency_code"), "fx_rate": hdr.get("fx_rate"),
        "reversal_of_id": hdr.get("reversal_of_id"),
    })
    n = 0
    for ln in clean:
        n += 10
        ErpDB.insert("journal_entry_lines", {
            "je_id": je_id, "line_num": n, "account_id": ln["account_id"],
            "debit_amount": ln["dr"] / 100, "credit_amount": ln["cr"] / 100,
            "narration": ln["narration"][:300],
            "party_type": ln["party_type"], "party_id": ln["party_id"],
        })
    ErpAuth.audit("journal_entries", je_id, "INSERT", {},
                  {"entry_number": je_num, "source": f"{source}#{source_id}", "amount": dr_c / 100})
    return int(je_id)


def _cc(doc: dict) -> dict:
    return {k: doc.get(k) for k in ("category_id", "unit_id", "department_id", "project_id", "wbs_element_id")}


# ---------------------------------------------------------------------------
# GST
# ---------------------------------------------------------------------------
def gst_split(tax: float, party_gstin: Optional[str], company_id: Optional[int] = None,
              pos_state: Optional[str] = None) -> dict:
    """Split a tax amount into CGST/SGST (intra-state) or IGST from the two GSTIN state codes."""
    company_id = company_id or _cid()
    co_st = str(ErpDB.fetch_value("SELECT gstin FROM companies WHERE id=?", [company_id]) or "").strip()[:2]
    pt_st = pos_state or str(party_gstin or "").strip()[:2]
    tax = fin_round(tax)
    intra = True if (co_st == "" or pt_st == "" or not pt_st.isdigit()) else (co_st == pt_st)
    if intra:
        c = fin_round(tax / 2)
        return {"cgst": c, "sgst": fin_round(tax - c), "igst": 0.0}
    return {"cgst": 0.0, "sgst": 0.0, "igst": tax}


def gst_log(direction: str, doc_type: str, doc_id: int, doc_no: str, d: str, gstin: Optional[str],
            taxable: float, cgst: float, sgst: float, igst: float) -> None:
    key = f"{direction}:{doc_type}:{doc_id}"
    cid = _cid()
    if ErpDB.fetch_value("SELECT id FROM gst_transactions WHERE company_id=? AND source_key=?", [cid, key]):
        return
    ErpDB.insert("gst_transactions", {
        "company_id": cid, "trans_type": "OUTWARD" if direction == "OUT" else "INWARD",
        "doc_type": doc_type, "doc_id": doc_id, "doc_number": doc_no, "doc_date": d, "party_gstin": gstin,
        "taxable_amt": taxable, "cgst": cgst, "sgst": sgst, "igst": igst,
        "total_tax": cgst + sgst + igst, "source_key": key, "direction": direction,
    })


# ---------------------------------------------------------------------------
# automatic postings
# ---------------------------------------------------------------------------
def fin_post_grn(grn_id: int) -> Optional[int]:
    """GRN posted: Dr Inventory / Cr GR-IR at accepted qty x price (RTV 122 reverses it)."""
    grn = ErpDB.fetch_one("SELECT * FROM goods_receipts WHERE id=?", [grn_id])
    if not grn:
        return None
    is_return = str(grn.get("movement_type") or "101") == "122"
    cid = int(grn["company_id"])
    by_acc: dict[int, float] = {}
    for l in ErpDB.fetch_all(
            "SELECT item_id, qty_accepted, unit_price FROM goods_receipt_lines WHERE grn_id=?", [grn_id]):
        acc = mat_inventory_account(int(l["item_id"]), cid)
        by_acc[acc] = by_acc.get(acc, 0.0) + float(l["qty_accepted"] or 0) * float(l.get("unit_price") or 0)
    val = 0.0
    for acc in by_acc:
        by_acc[acc] = fin_round(by_acc[acc])
        val += by_acc[acc]
    val = fin_round(val)
    if val <= 0:
        return None
    gr = fin_acct("GRIR", cid)
    tag = ("RTV " if is_return else "GRN ") + grn["grn_number"]
    lines: list[dict] = []
    if is_return:
        lines.append(line(gr, val, 0, tag))
        lines += [line(acc, 0, v, tag) for acc, v in by_acc.items() if v > 0]
    else:
        lines += [line(acc, v, 0, tag) for acc, v in by_acc.items() if v > 0]
        lines.append(line(gr, 0, val, tag))
    return post_journal({
        "company_id": cid, "entry_date": str(grn["grn_date"]), "entry_type": "AUTO",
        "narration": ("Return to vendor " if is_return else "Goods receipt ") + grn["grn_number"],
        "source_module": "GRN", "source_id": grn_id, **_cc(grn)}, lines)


def grn_deductions_for_invoice(po_id: int, supplier_id: int) -> float:
    if not po_id:
        return 0.0
    return fin_round(ErpDB.fetch_value(
        "SELECT COALESCE(SUM(d.deducted_amount),0) FROM grn_deductions d JOIN goods_receipts g ON g.id=d.grn_id "
        "WHERE g.po_id=? AND g.supplier_id=? AND g.status='POSTED'", [po_id, supplier_id]))


def fin_post_supplier_invoice(si_id: int) -> Optional[int]:
    """
    Dr GR/IR (base) [+ tax when ITC is blocked], Dr input GST (ITC)
    Cr Payables (total - TDS), Cr TDS payable.  Reverse charge: tax is both ITC and an RCM
    liability and the supplier is not credited for it.
    """
    si = ErpDB.fetch_one(
        "SELECT si.*, s.gstin AS s_gstin FROM supplier_invoices si JOIN suppliers s ON s.id=si.supplier_id "
        "WHERE si.id=?", [si_id])
    if not si:
        return None
    cid = int(si["company_id"])
    base, tax, tds = fin_round(si["base_amount"]), fin_round(si["tax_amount"]), fin_round(si["tds_amount"])
    rcm, itc = bool(si.get("reverse_charge")), bool(si.get("itc_eligible"))
    cg, sg, ig = (float(si.get("cgst_amount") or 0), float(si.get("sgst_amount") or 0),
                  float(si.get("igst_amount") or 0))
    if tax > 0 and abs((cg + sg + ig) - tax) > 0.01:
        sp = gst_split(tax, si.get("s_gstin"), cid)
        cg, sg, ig = sp["cgst"], sp["sgst"], sp["igst"]
        ErpDB.update("supplier_invoices", {"cgst_amount": cg, "sgst_amount": sg, "igst_amount": ig},
                     "id=?", [si_id])
    supplier_id = int(si["supplier_id"])
    lines = [line(fin_acct("GRIR", cid), base, 0,
                  f"Invoice {si['invoice_number']} ({si.get('ref_invoice_num') or ''})")]
    if tax > 0:
        if itc:
            if cg > 0:
                lines.append(line(fin_acct("IN_CGST", cid), cg, 0, "Input CGST"))
            if sg > 0:
                lines.append(line(fin_acct("IN_SGST", cid), sg, 0, "Input SGST"))
            if ig > 0:
                lines.append(line(fin_acct("IN_IGST", cid), ig, 0, "Input IGST"))
        else:
            lines[0]["debit"] = fin_round(base + tax)     # ineligible ITC is a cost: stays in GR/IR
    payable_gross = base if rcm else fin_round(base + tax)
    if rcm and tax > 0:
        lines.append(line(fin_acct("RCM_PAYABLE", cid), 0, tax, "Reverse charge liability"))
    lines.append(line(fin_acct("AP", cid), 0, fin_round(payable_gross - tds),
                      f"Invoice {si['invoice_number']}", "SUPPLIER", supplier_id))
    if tds > 0:
        lines.append(line(fin_acct("TDS_PAYABLE", cid), 0, tds, f"TDS {si.get('tds_section') or ''}"))

    # defensive rebalance (e.g. RCM without ITC)
    dr = sum(fin_cents(l["debit"]) for l in lines)
    cr = sum(fin_cents(l["credit"]) for l in lines)
    if dr != cr:
        lines[0]["debit"] = fin_round(lines[0]["debit"] + (cr - dr) / 100)

    # QC / price deductions live in the CONFIDENTIAL ZQDR register; only posted to the shared
    # ledger when a Super Admin switches this on in FINSET.
    if str(fin_opt("post_qc_deductions", "0")) == "1":
        ded = grn_deductions_for_invoice(int(si.get("po_id") or 0), supplier_id)
        if ded > 0:
            lines.append(line(fin_acct("AP", cid), ded, 0, "QC / price deductions on PO", "SUPPLIER", supplier_id))
            lines.append(line(fin_acct("PURCH_DEDUCT", cid), 0, ded, "Purchase deduction (QC / price)"))
            ErpDB.update("supplier_invoices",
                         {"due_amount": fin_round(max(0, float(si["due_amount"]) - ded))},
                         "id=? AND paid_amount=0", [si_id])

    je_id = post_journal({
        "company_id": cid, "entry_date": str(si["invoice_date"]), "entry_type": "AUTO",
        "narration": f"Supplier invoice {si['invoice_number']}",
        "source_module": "SUPPLIER_INVOICE", "source_id": si_id, **_cc(si)}, lines)
    ErpDB.update("supplier_invoices", {"je_id": je_id}, "id=?", [si_id])
    gst_log("IN", "SUPPLIER_INVOICE", si_id, str(si["invoice_number"]), str(si["invoice_date"]),
            si.get("s_gstin"), base, cg, sg, ig)
    if tds > 0 and si.get("tds_section"):
        ErpDB.insert("tds_deductions", {
            "company_id": cid, "source_type": "INVOICE", "source_id": si_id,
            "deduct_date": si["invoice_date"], "supplier_id": supplier_id,
            "section": si["tds_section"], "gross_amount": base, "tds_amount": tds})
    return je_id


# ---------------------------------------------------------------------------
# three-way match
# ---------------------------------------------------------------------------
def three_way_match(po_id: int, supplier_id: int, invoice_base: float,
                    exclude_invoice_id: int = 0) -> tuple[Optional[float], float, bool, str]:
    """Returns (expected, variance, blocked, message). Expected = received qty x PO price, not yet invoiced."""
    if not po_id:
        return None, 0.0, False, ""
    received = float(ErpDB.fetch_value(
        "SELECT COALESCE(SUM(pol.qty_received * pol.unit_price * (1 - COALESCE(pol.discount_pct,0)/100)),0) "
        "FROM purchase_order_lines pol WHERE pol.po_id=?", [po_id]) or 0)
    sql = ("SELECT COALESCE(SUM(base_amount),0) FROM supplier_invoices "
           "WHERE po_id=? AND supplier_id=? AND status<>'CANCELLED'")
    args: list = [po_id, supplier_id]
    if exclude_invoice_id:
        sql += " AND id<>?"
        args.append(int(exclude_invoice_id))
    already = float(ErpDB.fetch_value(sql, args) or 0)
    expected = round(received - already, 2)
    var = round(invoice_base - expected, 2)
    tol_pct = float(fin_opt("match_tol_pct", 2) or 0)
    tol_amt = float(fin_opt("match_tol_amt", 10) or 0)
    allowed = max(tol_amt, abs(expected) * tol_pct / 100)
    blocked = var > allowed + 0.004     # only OVER-billing blocks
    msg = (f"Invoice base {invoice_base:,.2f} exceeds goods received × PO price still open to bill "
           f"({expected:,.2f}) by {var:,.2f}, beyond the tolerance of {allowed:,.2f}. "
           f"Check quantity and price against the PO and GRN.") if blocked else ""
    return expected, var, blocked, msg


# ---------------------------------------------------------------------------
# savepoint wrapper + warnings (PHP: msme_erp_fin_try_post + flash)
# ---------------------------------------------------------------------------
_tl = threading.local()


def pop_warnings() -> list[str]:
    w = getattr(_tl, "warnings", [])
    _tl.warnings = []
    return w


def _sp(sql: str) -> None:
    try:
        ErpDB.fetch_all(sql)
    except Exception:  # noqa: BLE001  (driver without savepoint support: degrade silently)
        pass


def fin_try_post(fn: Callable[[], Any], what: str) -> Optional[int]:
    """Run a GL posting in a SAVEPOINT; on failure undo only the posting and remember a warning."""
    _sp("SAVEPOINT erp_fin_post")
    try:
        r = fn()
        _sp("RELEASE SAVEPOINT erp_fin_post")
        return r
    except Exception as e:  # noqa: BLE001
        _sp("ROLLBACK TO SAVEPOINT erp_fin_post")
        _sp("RELEASE SAVEPOINT erp_fin_post")
        log.error("GL auto-post failed (%s): %s", what, e)
        if not hasattr(_tl, "warnings"):
            _tl.warnings = []
        _tl.warnings.append(f"{what} saved, but it could not be posted to the General Ledger: {e} "
                            f"- fix the cause and run FBPOST.")
        return None


# ---------------------------------------------------------------------------
# Dimensions & Notes (Credit / Debit Notes & Returns)
# ---------------------------------------------------------------------------
def _cc(doc: dict) -> dict:
    """Cost-center and project dimensions carried from source documents."""
    return {
        "category_id": doc.get("category_id"),
        "unit_id": doc.get("unit_id"),
        "department_id": doc.get("department_id"),
        "project_id": doc.get("project_id"),
        "wbs_element_id": doc.get("wbs_element_id"),
    }


def post_note(note_id: int) -> Optional[int]:
    """Post debit or credit note to the general ledger."""
    n = ErpDB.fetch_one("SELECT * FROM fin_notes WHERE id=?", [note_id])
    if not n:
        return None
    cid = int(n.get("company_id") or _cid())
    base = float(n.get("base_amount") or 0)
    cg = float(n.get("cgst_amount") or 0)
    sg = float(n.get("sgst_amount") or 0)
    ig = float(n.get("igst_amount") or 0)
    tot = float(n.get("total_amount") or (base + cg + sg + ig))
    note_type = str(n.get("note_type") or "").upper()
    lines = []
    if note_type == "CN":
        try:
            rev_acc = fin_acct("REVENUE", cid)
        except Exception:
            rev_acc = fin_acct("SALES", cid)
        try:
            ar_acc = fin_acct("AR", cid)
        except Exception:
            ar_acc = fin_acct("DEBTORS", cid)
        lines.append(line(rev_acc, debit=base, narration=f"Reverse revenue {n.get('note_number')}"))
        if cg > 0:
            lines.append(line(fin_acct("OUT_CGST", cid), debit=cg, narration="Reverse output CGST"))
        if sg > 0:
            lines.append(line(fin_acct("OUT_SGST", cid), debit=sg, narration="Reverse output SGST"))
        if ig > 0:
            lines.append(line(fin_acct("OUT_IGST", cid), debit=ig, narration="Reverse output IGST"))
        lines.append(line(ar_acc, credit=tot, narration=f"Credit note {n.get('note_number')}", party_type="CUSTOMER", party_id=n.get("party_id")))
        gstin = ErpDB.fetch_value("SELECT gstin FROM customers WHERE id=?", [n.get("party_id")])
        gst_log("OUT", "CREDIT_NOTE", note_id, str(n.get("note_number") or ""), str(n.get("note_date") or ""), gstin, -base, -cg, -sg, -ig)
    else:
        try:
            ap_acc = fin_acct("AP", cid)
        except Exception:
            ap_acc = fin_acct("CREDITORS", cid)
        offset_key = str(n.get("offset_key") or "PURCH_RETURN")
        try:
            offset_acc = fin_acct(offset_key, cid)
        except Exception:
            try:
                offset_acc = fin_acct("GRIR", cid)
            except Exception:
                offset_acc = fin_acct("INVENTORY", cid)
        lines.append(line(ap_acc, debit=tot, narration=f"Debit note {n.get('note_number')}", party_type="SUPPLIER", party_id=n.get("party_id")))
        lines.append(line(offset_acc, credit=base, narration=f"Debit note {n.get('note_number')}"))
        if cg > 0:
            lines.append(line(fin_acct("IN_CGST", cid), credit=cg, narration="Reverse input CGST"))
        if sg > 0:
            lines.append(line(fin_acct("IN_SGST", cid), credit=sg, narration="Reverse input SGST"))
        if ig > 0:
            lines.append(line(fin_acct("IN_IGST", cid), credit=ig, narration="Reverse input IGST"))
        gstin = ErpDB.fetch_value("SELECT gstin FROM suppliers WHERE id=?", [n.get("party_id")])
        gst_log("IN", "DEBIT_NOTE", note_id, str(n.get("note_number") or ""), str(n.get("note_date") or ""), gstin, -base, -cg, -sg, -ig)

    hdr = {
        "company_id": cid,
        "entry_date": str(n.get("note_date")),
        "entry_type": "AUTO",
        "narration": f"{'Credit note' if note_type == 'CN' else 'Debit note'} {n.get('note_number')}",
        "source_module": "FIN_NOTE",
        "source_id": note_id,
    }
    return post_journal(hdr, lines)


def note_to_invoice(note_type: str, inv_id: int, total: float) -> None:
    tbl = "sales_invoices" if note_type == "CN" else "supplier_invoices"
    ErpDB.query(
        f"UPDATE {tbl} SET paid_amount = paid_amount + ?, due_amount = due_amount - ? WHERE id=?",
        [total, total, inv_id],
    )
    ErpDB.query(
        f"UPDATE {tbl} SET status = CASE WHEN due_amount <= 0.004 THEN 'PAID' ELSE 'PARTIAL' END WHERE id=?",
        [inv_id],
    )


def create_note_split(
    note_type: str,
    party_id: int,
    date_str: str,
    base: float,
    cg: float,
    sg: float,
    ig: float,
    reason: str,
    ref_inv_id: int = 0,
    offset_key: str = "",
    ret_type: str = "",
    ret_id: int = 0,
    company_id: Optional[int] = None,
) -> int:
    cid = int(company_id or _cid())
    note_type = note_type.upper()
    if note_type not in ("CN", "DN"):
        raise FinanceError("Invalid note type.")
    base = round(float(base), 2)
    cg = round(float(cg), 2)
    sg = round(float(sg), 2)
    ig = round(float(ig), 2)
    if base <= 0:
        raise FinanceError("Note amount must be greater than zero.")
    total = round(base + cg + sg + ig, 2)
    tax = round(cg + sg + ig, 2)
    inv_num = None
    if ref_inv_id:
        tbl = "sales_invoices" if note_type == "CN" else "supplier_invoices"
        nc = "inv_number" if note_type == "CN" else "invoice_number"
        pc = "customer_id" if note_type == "CN" else "supplier_id"
        inv = ErpDB.fetch_one(
            f"SELECT id, {nc} AS num, {pc} AS pid, due_amount, status FROM {tbl} WHERE id=? AND company_id=?",
            [ref_inv_id, cid],
        )
        if not inv or int(inv.get("pid") or 0) != party_id:
            raise FinanceError("Referenced invoice not found for this party.")
        if inv.get("status") in ("PAID", "CANCELLED") or total > float(inv.get("due_amount") or 0) + 0.005:
            ref_inv_id = 0  # open note
        else:
            inv_num = inv.get("num")

    assert_period_open(date_str, cid)
    doc_prefix = "CN" if note_type == "CN" else "DN"
    yr = date_str[:4]
    last_num = ErpDB.fetch_value(
        "SELECT MAX(id) FROM fin_notes WHERE company_id=? AND note_type=?",
        [cid, note_type],
    ) or 0
    note_number = f"{doc_prefix}-{yr}-{int(last_num) + 1:04d}"

    row = {
        "company_id": cid,
        "note_number": note_number,
        "note_type": note_type,
        "note_date": date_str,
        "party_id": party_id,
        "ref_inv_id": ref_inv_id or None,
        "ref_inv_number": inv_num,
        "reason": str(reason)[:255],
        "base_amount": base,
        "cgst_amount": cg,
        "sgst_amount": sg,
        "igst_amount": ig,
        "total_amount": total,
        "gst_rate": round(tax / base * 100, 2) if base > 0 else 0,
        "status": "POSTED",
        "offset_key": offset_key or None,
        "ret_type": ret_type or None,
        "ret_id": ret_id or None,
        "created_by": ErpAuth.user_id(),
    }
    nid = ErpDB.insert("fin_notes", row)
    je = None
    try:
        je = post_note(nid)
    except Exception as e:
        log.warning(f"Failed to post GL entry for note {nid}: {e}")
    if je:
        ErpDB.update("fin_notes", {"je_id": je}, "id=?", [nid])
    if ref_inv_id:
        note_to_invoice(note_type, ref_inv_id, total)
    return nid
