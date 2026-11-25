<?php
/**
 * MSME ERP — Goods Movement (MIGO Equivalent)
 * T-Code: MIGO
 * Handles: 101 - GRN, 122 - Return to Vendor, 261 - Issue to WO
 * Developed by: Prigag Online Pvt. Ltd.
 *
 * v1.15.0 - Pull from Purchase Order: pick the supplier's open PO and the
 * supplier, items, UOM, open quantities and PO prices are loaded onto the
 * GRN in one click. "Send to Quality Inspection" now creates an inspection
 * lot per line (QA32) and the stock is posted only when QC accepts.
 * Supplier / Warehouse / Material lists are tolerant of status values and
 * explain themselves when the active company has none (see helpers.php).
 *
 * Quality / Price Deduction: any line can be flagged "Qty/Quality Issue"
 * (percentage or flat amount, applied against the rejected — or if none
 * rejected, the received — line value), and the document as a whole can
 * carry one "Price Difference / Other" deduction (expected vs actual
 * billed amount, or a flat/percent "other" charge). Both write to the
 * confidential grn_deductions table on post — see T-Code ZQDR
 * (modules/admin/qc_deduction_register.php, SUPER_ADMIN only). Nothing
 * about these amounts is ever echoed back on this screen for anyone
 * other than a SUPER_ADMIN.
 */

if (empty($tcode)) $tcode = strtoupper($_GET['tcode'] ?? 'MIGO');
Auth::requireTcode(in_array($tcode, ['MIGO']) ? $tcode : 'MIGO');

$companyId    = Auth::companyId();
$currentTcode = 'MIGO';
$currency     = msme_erp_currency_symbol(Auth::user()['currency'] ?? null);
$isSuperAdmin = Auth::hasRole('SUPER_ADMIN');

$error   = '';
$mvtCode = $_GET['mvt'] ?? '101';   // Movement type
$poId    = (int)($_GET['po_id'] ?? 0);

$mvtLabels = [
    '101' => 'Goods Receipt against PO',
    '122' => 'Return to Vendor',
    '261' => 'Goods Issue to Work Order',
    '201' => 'Goods Issue to Cost Center',
    '501' => 'Receipt without PO',
];

// Load PO for pre-fill ("Pull from Purchase Order")
$po = null; $poLines = []; $poNotice = '';
if ($poId) {
    $po = DB::fetchOne(
        "SELECT po.*, s.name AS supplier_name, s.supplier_code FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id
         WHERE po.id=? AND po.company_id=?",
        [$poId, $companyId]
    );
    if ($po && !in_array($po['status'], ['APPROVED', 'PARTIAL'], true)) {
        $poNotice = 'PO ' . $po['po_number'] . ' is ' . $po['status'] . ' - goods can be received only against an APPROVED or PARTIAL purchase order.';
        $po = null; $poId = 0;
    } elseif (!$po) {
        $poNotice = 'That purchase order was not found in the active company.';
        $poId = 0;
    }
    if ($po) {
        foreach (msme_erp_po_open_lines($poId) as $__pl) {
            if (($__pl['status'] ?? 'OPEN') !== 'OPEN' && !empty($__pl['status'])) continue;
            $__open = (float)$__pl['qty_ordered'] - (float)$__pl['qty_received'] - (float)$__pl['qty_in_qc'];
            if ($__open <= 0.0004) continue;
            $__pl['qty_open'] = $__open;
            $poLines[] = $__pl;
        }
        if (!$poLines) $poNotice = 'PO ' . $po['po_number'] . ' has nothing left to receive (fully received or waiting in QC).';
    }
}

// ---- Existing GRN (view mode)? Resolved up front, same as every other
// detail screen (e.g. ME23N/po_view.php), so $pageTitle/$breadcrumbs/
// $pageActions are correct BEFORE erp_render_page_header() runs once
// below — previously this screen always rendered the generic "Goods
// Movement — MIGO" header even while displaying a specific posted GRN,
// which is what made this screen look inconsistent with every other
// detail screen in the ERP. ----
$grnId = (int)($_GET['grn_id'] ?? 0);
$grn = null; $grnLines = []; $grnDeductions = []; $grnDeductionTotal = 0.0;
if ($grnId) {
    $grn = DB::fetchOne(
        "SELECT gr.*, s.name AS supplier_name, w.name AS warehouse_name
         FROM goods_receipts gr JOIN suppliers s ON s.id=gr.supplier_id JOIN warehouses w ON w.id=gr.warehouse_id
         WHERE gr.id=? AND gr.company_id=?", [$grnId, $companyId]
    );
    if ($grn) {
        $grnLines = DB::fetchAll(
            "SELECT grl.*, i.item_code, i.item_name, u.code AS uom
             FROM goods_receipt_lines grl JOIN items i ON i.id=grl.item_id LEFT JOIN units_of_measure u ON u.id=grl.uom_id
             WHERE grl.grn_id=?", [$grnId]
        );
        // Deduction figures are pulled ONLY for a SUPER_ADMIN viewing this
        // GRN — every other role sees this screen exactly as if the
        // deduction feature didn't exist.
        if ($isSuperAdmin) {
            $grnDeductions = DB::fetchAll(
                "SELECT gd.* FROM grn_deductions gd WHERE gd.grn_id=? ORDER BY gd.id", [$grnId]
            );
            foreach ($grnDeductions as $gd) $grnDeductionTotal += (float) $gd['deducted_amount'];
        }
    } else {
        $grnId = 0; // not found / not this company — fall through to the create screen
    }
}

if ($grn) {
    $pageTitle   = 'GRN: ' . $grn['grn_number'];
    $pageTcode   = 'MIGO';
    $breadcrumbs = ['MM', 'Goods Movement (MIGO)', $grn['grn_number']];
    $pageActions = '<a href="' . admin_url('admin.php?page=msme-erp&tcode=MIGO') . '" class="btn btn-sm btn-success"><i class="bi bi-plus-circle me-1"></i>New GRN</a>'
                 . '<a href="javascript:window.print()" class="btn btn-sm btn-outline-secondary"><i class="bi bi-printer me-1"></i>Print</a>';
    if ($isSuperAdmin) {
        $pageActions .= '<a href="' . admin_url('admin.php?page=msme-erp&tcode=ZQDR') . '" class="btn btn-sm btn-outline-danger"><i class="bi bi-shield-lock me-1"></i>Deduction Reserve</a>';
    }
} else {
    $pageTitle   = 'Goods Movement — MIGO';
    $pageTcode   = 'MIGO';
    $breadcrumbs = ['MM', 'Goods Movement (MIGO)'];
    $pageActions = $isSuperAdmin
        ? '<a href="' . admin_url('admin.php?page=msme-erp&tcode=ZQDR') . '" class="btn btn-sm btn-outline-danger"><i class="bi bi-shield-lock me-1"></i>Deduction Reserve</a>'
        : '';
}
erp_render_page_header();

// POST — Post GRN
if (!$grn && $_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) die('CSRF failed');

    $postSupplierId  = (int)($_POST['supplier_id'] ?? 0);
    $postPoId        = (int)($_POST['po_id'] ?? 0);
    $postWarehouseId = (int)($_POST['warehouse_id'] ?? 0);
    $postDcNumber    = $_POST['dc_number'] ?? '';
    $postVehicle     = $_POST['vehicle_num'] ?? '';
    $postMvtCode     = $_POST['mvt_code'] ?? '101';
    $lines           = $_POST['grn_lines'] ?? [];
    $qcRequired      = (int)($_POST['qc_required'] ?? 0);

    $lines = array_filter($lines, fn($l) => !empty($l['item_id']) && (float)$l['qty_received'] > 0);

    // v1.16.2 QC gate: a receipt containing a material flagged "QC on GRN" in
    // the item master (or any receipt, when the company-wide option is on)
    // MUST go to Quality Inspection. The checkbox is only a convenience -
    // the decision is made here. Only a super admin can send it straight to
    // stock, and that override is audited.
    $qcOverridden = false;
    if ($postMvtCode === '101' && msme_erp_qc_mandatory($companyId, array_column($lines, 'item_id'))) {
        if (!$qcRequired) {
            if ($isSuperAdmin && !empty($_POST['qc_override'])) $qcOverridden = true;
            else $qcRequired = 1;
        }
    }

    // A GRN pulled from a PO must be for that PO's supplier, and a PO line can
    // never receive more than its open balance. Price comes from the PO line,
    // never from the (hidden) form field.
    $poCheckErr = '';
    if ($postPoId) {
        $postPo = DB::fetchOne("SELECT id, supplier_id, po_number, status FROM purchase_orders WHERE id=? AND company_id=?", [$postPoId, $companyId]);
        if (!$postPo || !in_array($postPo['status'], ['APPROVED', 'PARTIAL'], true)) {
            $poCheckErr = 'The selected purchase order is not open for receipt.';
        } else {
            $postSupplierId = (int)$postPo['supplier_id'];
            $openByLine = [];
            foreach (msme_erp_po_open_lines($postPoId) as $__x) {
                $openByLine[(int)$__x['id']] = ['open' => (float)$__x['qty_ordered'] - (float)$__x['qty_received'] - (float)$__x['qty_in_qc'], 'price' => (float)$__x['unit_price'], 'item' => (int)$__x['item_id']];
            }
            foreach ($lines as $k => $l) {
                $plid = (int)($l['po_line_id'] ?? 0);
                if (!$plid) continue;
                if (!isset($openByLine[$plid]) || $openByLine[$plid]['item'] !== (int)$l['item_id']) { $poCheckErr = 'A line does not belong to the selected purchase order.'; break; }
                if ((float)$l['qty_received'] > $openByLine[$plid]['open'] + 0.0004) {
                    $poCheckErr = 'Received quantity exceeds the open PO balance (' . rtrim(rtrim(number_format($openByLine[$plid]['open'], 3, '.', ''), '0'), '.') . ') on one of the lines.'; break;
                }
                $lines[$k]['unit_price'] = $openByLine[$plid]['price'];
            }
        }
    }

    // Deduction → ZQDR. Only a SUPER_ADMIN can tick/enter this; anyone else's
    // POST data for it is ignored outright (never trust the hidden form).
    $dedActive  = $isSuperAdmin && !empty($_POST['ded_active']);
    $dedType    = in_array($_POST['ded_type'] ?? '', ['QUALITY', 'PRICE_DIFF', 'OTHER'], true) ? $_POST['ded_type'] : 'QUALITY';
    $dedMode    = strtoupper($_POST['ded_mode'] ?? 'AMOUNT') === 'PERCENT' ? 'PERCENT' : 'AMOUNT';
    $dedValue   = (float)($_POST['ded_value'] ?? 0);
    $dedRemarks = sanitize_textarea_field($_POST['ded_remarks'] ?? '');

    // Warehouse is optional: blank -> the company's default warehouse. A chosen
    // one must belong to the active company.
    if ($postWarehouseId) {
        if (!DB::fetchValue("SELECT id FROM warehouses WHERE id=? AND company_id=?", [$postWarehouseId, $companyId])) $postWarehouseId = 0;
    }
    if (!$postWarehouseId) $postWarehouseId = msme_erp_default_warehouse_id($companyId);

    if ($poCheckErr) {
        $error = $poCheckErr;
    } elseif (!$postSupplierId) {
        $error = 'Supplier is required.';
    } elseif (!$postWarehouseId) {
        $error = 'No warehouse could be found or created for this company.';
    } elseif (!$lines) {
        $error = 'Enter at least one line with received quantity.';
    } else {
        try {
            DB::beginTransaction();

            // Get GRN number
            $result = DB::callProcedure('sp_get_next_number', [$companyId, 'GRN', date('Y')], ['p_doc_number']);
            $grnNum = $result['p_doc_number'];

            $newGrnId = DB::insert('goods_receipts', [
                'company_id'    => $companyId,
                'branch_id'     => Auth::branchId(),
                'grn_number'    => $grnNum,
                'grn_date'      => date('Y-m-d'),
                'po_id'         => ($postPoId && $postMvtCode === '101') ? $postPoId : null,
                'supplier_id'   => $postSupplierId,
                'warehouse_id'  => $postWarehouseId,
                'movement_type' => $postMvtCode,
                'dc_number'     => $postDcNumber,
                'vehicle_num'   => $postVehicle,
                'status'        => 'DRAFT',
                'qc_required'   => $qcRequired,
                'created_by'    => Auth::userId(),
            ] + msme_erp_cost_center_from_post($_POST));

            $grnTotalBase = 0.0;

            foreach ($lines as $l) {
                $qtyRecv    = (float)$l['qty_received'];
                // Accepted can never exceed received (or go negative) — a
                // stale/lower "accepted" box used to turn the difference
                // into a bogus rejection (or a negative one).
                $qtyAccept  = isset($l['qty_accepted']) && $l['qty_accepted'] !== '' ? (float)$l['qty_accepted'] : $qtyRecv;
                $qtyAccept  = max(0.0, min($qtyRecv, $qtyAccept));
                $qtyRej     = $qtyRecv - $qtyAccept;
                $uPrice     = (float)($l['unit_price'] ?? 0);
                $qtyOrdered = !empty($l['qty_ordered']) ? (float)$l['qty_ordered'] : null;

                $lineId = DB::insert('goods_receipt_lines', [
                    'grn_id'       => $newGrnId,
                    'po_line_id'   => !empty($l['po_line_id']) ? (int)$l['po_line_id'] : null,
                    'item_id'      => (int)$l['item_id'],
                    'uom_id'       => !empty($l['uom_id']) ? (int)$l['uom_id']
                                      : (int) DB::fetchValue("SELECT base_uom_id FROM items WHERE id=?", [(int)$l['item_id']]),
                    'qty_received' => $qtyRecv,
                    'qty_accepted' => $qtyAccept,
                    'qty_rejected' => $qtyRej,
                    'unit_price'   => $uPrice,
                    'batch_number' => $l['batch_number'] ?? null,
                    'expiry_date'  => !empty($l['expiry_date']) ? $l['expiry_date'] : null,
                ]);

                $lineValue     = $qtyRecv * $uPrice;
                $grnTotalBase += $lineValue;

            }

            // ---- Deduction → ZQDR (confidential; price never shown on GRN) ----
            // Percentage is worked out server-side against the GRN value from
            // the hidden PO / moving-average unit prices, so the store user
            // never sees or types a material price on this screen.
            if ($dedActive && $dedValue > 0) {
                $dedAmt = msme_erp_calc_deduction_amount($dedMode, $dedValue, $grnTotalBase);
                if ($dedAmt > 0) {
                    DB::insert('grn_deductions', [
                        'company_id'      => $companyId,
                        'branch_id'       => Auth::branchId(),
                        'grn_id'          => $newGrnId,
                        'grn_line_id'     => null,
                        'supplier_id'     => $postSupplierId,
                        'deduction_type'  => $dedType,
                        'calc_mode'       => $dedMode,
                        'input_value'     => $dedValue,
                        'base_amount'     => $grnTotalBase,
                        'deducted_amount' => $dedAmt,
                        'reason'          => ['QUALITY' => 'Quality / quantity not as ordered', 'PRICE_DIFF' => 'Price difference', 'OTHER' => 'Other deduction'][$dedType],
                        'remarks'         => $dedRemarks,
                        'created_by'      => Auth::userId(),
                    ]);
                }
            }

            // Auto-post if no QC required; otherwise hold the stock and send
            // every line to Quality Inspection (one inspection lot per line).
            if (!$qcRequired || $postMvtCode === '122') {
                DB::callProcedure('sp_process_grn', [$newGrnId, Auth::userId()]);
                $successMsg = $postMvtCode === '122'
                    ? "Return {$grnNum} posted. Stock reduced."
                    : "GRN {$grnNum} posted successfully. Stock updated.";
            } else {
                DB::update('goods_receipts', ['status' => 'PENDING_QC'], 'id=?', [$newGrnId]);
                $lotNums = msme_erp_create_grn_inspection_lots($newGrnId, $companyId);
                $successMsg = "GRN {$grnNum} sent to Quality Inspection"
                    . ($lotNums ? ' - lot' . (count($lotNums) > 1 ? 's ' : ' ') . implode(', ', $lotNums) : '')
                    . ". Stock is posted when QC accepts (QA32).";
            }

            Auth::audit('goods_receipts', $newGrnId, 'INSERT', [], ['grn_number' => $grnNum]);
            if ($qcOverridden) Auth::audit('goods_receipts', $newGrnId, 'QC_OVERRIDE', [], ['note' => 'QC-mandatory material posted straight to stock by a super admin']);
            DB::commit();

            msme_erp_set_flash($successMsg, 'success');
            wp_redirect(admin_url('admin.php?page=msme-erp&tcode=MIGO') . '&grn_id=' . $newGrnId); exit;

        } catch (Exception $e) {
            DB::rollback();
            $error = 'Error posting GRN: ' . $e->getMessage();
        }
    }
}

$suppliers = $warehouses = $uoms = $allItems = [];
$openPos = [];
try {
    $suppliers  = msme_erp_master_suppliers($companyId);
    $warehouses = msme_erp_master_warehouses($companyId);
    $uoms       = DB::fetchAll("SELECT id, code FROM units_of_measure WHERE is_active=1 ORDER BY code");
    $allItems   = msme_erp_master_items($companyId);
    if ($mvtCode === '101') $openPos = msme_erp_open_pos($companyId);
} catch (Throwable $e) {
    error_log('MIGO master data: ' . $e->getMessage());
    $error = $error ?: 'Could not load master data (suppliers / warehouses / items): ' . $e->getMessage();
}
$uomOptsHtml = '<option value="">UOM</option>';
foreach ($uoms as $__u) { $uomOptsHtml .= '<option value="' . (int)$__u['id'] . '">' . htmlspecialchars((string)$__u['code']) . '</option>'; }

// ============================================================
// VIEW — existing GRN
// ============================================================
if ($grn) {
?>
<div class="card border-0 shadow-sm mb-3">
  <div class="card-header bg-success text-white fw-bold small py-2 d-flex justify-content-between">
    <span><i class="bi bi-arrow-down-circle me-2"></i>GRN: <?= htmlspecialchars($grn['grn_number']) ?></span>
    <span class="badge bg-light text-dark"><?= $grn['status'] ?></span>
  </div>
  <div class="card-body">
    <div class="row g-2 small">
      <div class="col-md-3"><strong>Supplier:</strong> <?= htmlspecialchars($grn['supplier_name']) ?></div>
      <div class="col-md-3"><strong>Warehouse:</strong> <?= htmlspecialchars($grn['warehouse_name']) ?></div>
      <div class="col-md-2"><strong>Date:</strong> <?= $grn['grn_date'] ?></div>
      <div class="col-md-2"><strong>DC#:</strong> <?= htmlspecialchars($grn['dc_number'] ?? '—') ?></div>
      <div class="col-md-2"><strong>Vehicle:</strong> <?= htmlspecialchars($grn['vehicle_num'] ?? '—') ?></div>
      <?php if (!empty($grn['po_id'])): $__gpo = DB::fetchValue("SELECT po_number FROM purchase_orders WHERE id=?", [(int)$grn['po_id']]); ?>
      <div class="col-md-3"><strong>PO:</strong> <a href="<?= admin_url('admin.php?page=msme-erp&tcode=ME23N&id=' . (int)$grn['po_id']) ?>"><?= htmlspecialchars((string)$__gpo) ?></a></div>
      <?php endif; ?>
    </div>
  </div>
</div>
<?php
// Quality inspection lots raised for this GRN (v1.15.0)
$grnLots = DB::fetchAll(
    "SELECT il.id, il.lot_number, il.status, il.result, il.qty, i.item_code
     FROM inspection_lots il JOIN goods_receipt_lines g ON g.id=il.origin_id JOIN items i ON i.id=il.item_id
     WHERE il.origin_type='GRN_LINE' AND g.grn_id=? ORDER BY il.id", [$grnId]);
if ($grnLots): ?>
<div class="alert alert-info small py-2 mb-3">
  <i class="bi bi-clipboard-check me-1"></i><strong>Quality inspection</strong> —
  <?= $grn['status'] === 'PENDING_QC' ? 'stock is on hold until every lot is accepted or rejected.' : 'inspection finished.' ?>
  <?php foreach ($grnLots as $gl2): ?>
    <a class="badge bg-<?= $gl2['status']==='COMPLETED' ? ($gl2['result']==='REJECTED'?'danger':'success') : 'warning text-dark' ?> text-decoration-none ms-1"
       href="<?= admin_url('admin.php?page=msme-erp&tcode=QA32&lot_id=' . (int)$gl2['id']) ?>"><?= htmlspecialchars($gl2['lot_number'] . ' · ' . $gl2['item_code']) ?> · <?= htmlspecialchars($gl2['result'] ?: $gl2['status']) ?></a>
  <?php endforeach; ?>
</div>
<?php endif; ?>
<div class="card border-0 shadow-sm">
  <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-list-ul me-2"></i>Received Items</div>
  <div class="card-body p-0">
    <table class="table table-sm erp-table mb-0">
      <thead><tr><th>Item</th><th>Received</th><th>Accepted</th><th>Rejected</th><th>Batch</th></tr></thead>
      <tbody>
        <?php foreach ($grnLines as $gl): ?>
        <tr>
          <td><div class="fw-semibold small"><?= htmlspecialchars($gl['item_code']) ?></div><div class="text-muted" style="font-size:.72rem"><?= htmlspecialchars($gl['item_name']) ?></div></td>
          <td class="small"><?= number_format($gl['qty_received'],3) ?> <?= $gl['uom'] ?></td>
          <td class="small text-success fw-bold"><?= number_format($gl['qty_accepted'],3) ?></td>
          <td class="small text-danger"><?= number_format($gl['qty_rejected'],3) ?></td>
          <td class="small"><?= htmlspecialchars($gl['batch_number'] ?? '—') ?></td>
        </tr>
        <?php endforeach; ?>
      </tbody>
    </table>
  </div>
</div>

<?php if ($isSuperAdmin && $grnDeductions): ?>
<div class="card border-0 shadow-sm mt-3">
  <div class="card-header bg-danger text-white fw-bold small py-2 d-flex justify-content-between">
    <span><i class="bi bi-shield-lock-fill me-2"></i>Quality / Price Deductions — Confidential</span>
    <span class="badge bg-light text-dark">Visible to Super Admin only</span>
  </div>
  <div class="card-body p-0">
    <table class="table table-sm mb-0" style="font-size:.78rem">
      <thead class="table-light"><tr><th>Type</th><th>Mode</th><th>Value</th><th>Reason</th><th class="text-end">Amount</th></tr></thead>
      <tbody>
        <?php foreach ($grnDeductions as $gd): ?>
        <tr>
          <td><?= esc_html($gd['deduction_type']) ?></td>
          <td><?= esc_html($gd['calc_mode']) ?></td>
          <td><?= $gd['calc_mode'] === 'PERCENT' ? number_format((float)$gd['input_value'],2).'%' : $currency.number_format((float)$gd['input_value'],2) ?></td>
          <td><?= esc_html($gd['reason'] ?: '—') ?></td>
          <td class="text-end fw-bold text-danger"><?= $currency . number_format((float)$gd['deducted_amount'],2) ?></td>
        </tr>
        <?php endforeach; ?>
      </tbody>
      <tfoot><tr class="fw-bold"><td colspan="4" class="text-end">Total held to Deduction Reserve</td><td class="text-end text-danger"><?= $currency . number_format($grnDeductionTotal,2) ?></td></tr></tfoot>
    </table>
  </div>
  <div class="card-footer bg-white text-end py-1">
    <a href="<?= admin_url('admin.php?page=msme-erp&tcode=ZQDR') ?>" class="btn btn-sm btn-outline-danger"><i class="bi bi-shield-lock me-1"></i>Open Full Deduction Reserve (ZQDR)</a>
  </div>
</div>
<?php endif; ?>

<?php
    return;
}
?>
<?php if ($error): ?><div class="alert alert-danger"><i class="bi bi-exclamation-triangle me-1"></i><?= htmlspecialchars($error) ?></div><?php endif; ?>
<?php if ($poNotice): ?><div class="alert alert-warning small py-2"><i class="bi bi-info-circle me-1"></i><?= htmlspecialchars($poNotice) ?></div><?php endif; ?>
<?php msme_erp_master_diagnostic($companyId, $suppliers, $allItems, $warehouses); ?>

<!-- Movement Type Selector -->
<div class="row g-2 mb-3">
  <?php foreach ($mvtLabels as $code => $label): ?>
  <div class="col-auto">
    <a href="?page=msme-erp&tcode=MIGO&mvt=<?= $code ?>" class="btn btn-sm <?= $mvtCode===$code?'btn-primary':'btn-outline-secondary' ?>">
      <span class="badge bg-white text-dark me-1"><?= $code ?></span><?= $label ?>
    </a>
  </div>
  <?php endforeach; ?>
</div>

<form method="POST" id="grn-form">
<input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
<input type="hidden" name="mvt_code" value="<?= $mvtCode ?>">

<div class="card border-0 shadow-sm mb-3">
  <div class="card-header bg-success text-white fw-bold small py-2">
    <i class="bi bi-arrow-down-circle me-2"></i>Goods Receipt — MVT <?= $mvtCode ?>: <?= $mvtLabels[$mvtCode] ?? '' ?>
  </div>
  <div class="card-body">
    <div class="row g-3">
      <?php if ($mvtCode === '101'): ?>
      <div class="col-12">
        <div class="border rounded p-2 bg-light">
          <div class="row g-2 align-items-end">
            <div class="col-lg-7">
              <label class="form-label fw-semibold small mb-1"><i class="bi bi-cloud-download me-1"></i>Pull from Purchase Order</label>
              <select id="po-pick" class="form-select form-select-sm">
                <option value="">— Select an open PO: supplier, items, UOM and quantities load automatically —</option>
                <?php foreach ($openPos as $op): ?>
                <option value="<?= (int)$op['id'] ?>" data-supplier="<?= (int)$op['supplier_id'] ?>" <?= $poId == $op['id'] ? 'selected' : '' ?>>
                  <?= htmlspecialchars($op['po_number'] . ' — ' . $op['supplier_name'] . ' — ' . $op['po_date'] . ' (' . (int)$op['open_lines'] . ' open line' . ((int)$op['open_lines'] > 1 ? 's' : '') . ')') ?>
                </option>
                <?php endforeach; ?>
              </select>
            </div>
            <div class="col-lg-5 small text-muted">
              <?php if ($po): ?>
              <span class="text-success fw-semibold"><i class="bi bi-link me-1"></i>Linked to <?= htmlspecialchars($po['po_number']) ?> &mdash; <?= htmlspecialchars($po['supplier_name']) ?></span>
              <a href="<?= admin_url('admin.php?page=msme-erp&tcode=MIGO&mvt=101') ?>" class="ms-2">Clear</a>
              <?php elseif ($openPos): ?>
              <?= count($openPos) ?> purchase order<?= count($openPos) > 1 ? 's are' : ' is' ?> waiting for goods. Choose the supplier below to narrow the list.
              <?php else: ?>
              No approved purchase order has open quantity. Receive without a PO using <a href="?page=msme-erp&tcode=MIGO&mvt=501">MVT 501</a>, or pick items by hand below.
              <?php endif; ?>
            </div>
          </div>
        </div>
      </div>
      <?php endif; ?>
      <input type="hidden" name="po_id" value="<?= (int)$poId ?>">

      <div class="col-md-4">
        <label class="form-label fw-semibold small">Supplier <span class="text-danger">*</span></label>
        <?php if ($po): ?><input type="hidden" name="supplier_id" value="<?= (int)$po['supplier_id'] ?>"><?php endif; ?>
        <select <?= $po ? 'disabled' : 'name="supplier_id"' ?> class="form-select form-select-sm <?= $po ? '' : 'select2' ?>" <?= $po ? '' : 'required' ?>>
          <option value="">— Select —</option>
          <?php foreach ($suppliers as $s): ?>
          <option value="<?= $s['id'] ?>" <?= ($po && $po['supplier_id']==$s['id'])?'selected':'' ?>>
            <?= htmlspecialchars($s['supplier_code'].' — '.$s['name']) ?>
          </option>
          <?php endforeach; ?>
        </select>
      </div>
      <div class="col-md-3">
        <label class="form-label fw-semibold small">Warehouse <span class="text-muted fw-normal">(optional)</span></label>
        <select name="warehouse_id" class="form-select form-select-sm">
          <option value="">— Default warehouse —</option>
          <?php foreach ($warehouses as $w): ?>
          <option value="<?= $w['id'] ?>"><?= htmlspecialchars($w['code'].' — '.$w['name']) ?></option>
          <?php endforeach; ?>
        </select>
      </div>
      <div class="col-md-2">
        <label class="form-label fw-semibold small">DC / Challan#</label>
        <input type="text" name="dc_number" class="form-control form-control-sm" placeholder="Delivery Challan">
      </div>
      <div class="col-md-2">
        <label class="form-label fw-semibold small">Vehicle No.</label>
        <input type="text" name="vehicle_num" class="form-control form-control-sm" placeholder="MH12AB1234">
      </div>
      <div class="col-md-1">
        <label class="form-label fw-semibold small" title="Send to Quality Inspection: stock is held until QC accepts (QA32)">QC?</label>
        <div class="form-check mt-1">
          <input class="form-check-input" type="checkbox" name="qc_required" value="1" id="qcCheck">
          <label class="form-check-label small" for="qcCheck" title="Send to Quality Inspection: stock is held until QC accepts (QA32)">Yes</label>
        </div>
        <input type="hidden" name="qc_override" id="qcOverride" value="0">
      </div>
      <div class="col-12" id="qcNote" style="display:none"></div>
    </div>
    <hr>
    <?php msme_erp_render_cost_center_fields($companyId); ?>
    <?php if ($isSuperAdmin): ?>
    <hr>
    <div class="form-check mb-2">
      <input class="form-check-input" type="checkbox" name="ded_active" value="1" id="dedMaster" onchange="document.getElementById('ded-box').style.display=this.checked?'':'none'">
      <label class="form-check-label small fw-semibold" for="dedMaster">Deduction &mdash; send to ZQDR <span class="fw-normal text-muted">(Super Admin only, optional)</span></label>
    </div>
    <div id="ded-box" style="display:none">
      <div class="small fw-semibold mb-2"><i class="bi bi-lock-fill me-1"></i>ZQDR DEDUCTION <span class="fw-normal text-muted">(Super Admin only &mdash; optional)</span></div>
      <div class="row g-2 align-items-end">
        <div class="col-md-2">
          <label class="form-label small">Reason</label>
          <select name="ded_type" class="form-select form-select-sm">
            <option value="QUALITY">Quality / Qty not as ordered</option>
            <option value="PRICE_DIFF">Price difference</option>
            <option value="OTHER">Other</option>
          </select>
        </div>
        <div class="col-md-2">
          <label class="form-label small">Deduct as</label>
          <select name="ded_mode" class="form-select form-select-sm">
            <option value="AMOUNT">Amount (<?= esc_html(msme_erp_cur()) ?>)</option>
            <option value="PERCENT">Percentage (%)</option>
          </select>
        </div>
        <div class="col-md-2">
          <label class="form-label small">Value</label>
          <input type="number" name="ded_value" class="form-control form-control-sm" min="0" step="0.01" placeholder="0.00">
        </div>
        <div class="col-md-4">
          <label class="form-label small">Remarks</label>
          <input type="text" name="ded_remarks" class="form-control form-control-sm" placeholder="Optional note">
        </div>
        <div class="col-md-2">
          <a href="<?= admin_url('admin.php?page=msme-erp&tcode=ZQDR') ?>" class="small">View ZQDR register &rarr;</a>
        </div>
      </div>
    </div>
    <?php endif; ?>
  </div>
</div>

<!-- Line Items -->
<div class="card border-0 shadow-sm mb-3">
  <div class="card-header bg-white d-flex justify-content-between align-items-center py-2">
    <span class="fw-bold small"><i class="bi bi-boxes me-2"></i>Items Received</span>
    <button type="button" class="btn btn-sm btn-outline-success" onclick="addGrnLine()">
      <i class="bi bi-plus-circle"></i> Add Item
    </button>
  </div>
  <div class="card-body p-0">
    <table class="table table-sm table-bordered mb-0" style="font-size:.8rem">
      <thead class="table-success">
        <tr>
          <th>Item</th><th width="90">UOM</th><th width="100">Qty Received</th>
          <th width="100">Qty Accepted</th>
          <th width="110">Batch#</th><th width="100">Expiry</th><th width="60"></th>
        </tr>
      </thead>
      <tbody id="grn-body">
        <?php if ($poLines): foreach ($poLines as $i => $pl): ?>
        <tr class="grn-line">
          <td>
            <input type="hidden" name="grn_lines[<?= $i ?>][po_line_id]" value="<?= $pl['id'] ?>">
            <input type="hidden" name="grn_lines[<?= $i ?>][item_id]" value="<?= $pl['item_id'] ?>">
            <input type="hidden" name="grn_lines[<?= $i ?>][qty_ordered]" value="<?= $pl['qty_open'] ?>">
            <input type="hidden" name="grn_lines[<?= $i ?>][unit_price]" value="<?= $pl['unit_price'] ?>">
            <div class="fw-semibold"><?= htmlspecialchars($pl['item_code']) ?></div>
            <div class="text-muted small"><?= htmlspecialchars($pl['item_name']) ?></div>
            <div class="text-info small">Ordered <?= number_format($pl['qty_ordered'],3) ?> &middot; Received <?= number_format($pl['qty_received'],3) ?><?= (float)$pl['qty_in_qc'] > 0 ? ' &middot; In QC ' . number_format($pl['qty_in_qc'],3) : '' ?> &middot; <strong>Open <?= number_format($pl['qty_open'],3) ?></strong></div>
          </td>
          <td><select name="grn_lines[<?= $i ?>][uom_id]" class="form-select form-select-sm" style="min-width:80px"><?= str_replace('value="' . (int)$pl['uom_id'] . '"', 'value="' . (int)$pl['uom_id'] . '" selected', $uomOptsHtml) ?></select></td>
          <td><input type="number" name="grn_lines[<?= $i ?>][qty_received]" class="form-control form-control-sm grn-recv" value="<?= $pl['qty_open'] ?>" min="0.001" step="0.001" max="<?= $pl['qty_open'] ?>"></td>
          <td><input type="number" name="grn_lines[<?= $i ?>][qty_accepted]" class="form-control form-control-sm" value="<?= $pl['qty_open'] ?>" min="0" step="0.001"></td>
          <td><input type="text"   name="grn_lines[<?= $i ?>][batch_number]" class="form-control form-control-sm" placeholder="Optional"></td>
          <td><input type="date"   name="grn_lines[<?= $i ?>][expiry_date]" class="form-control form-control-sm"></td>
          <td><button type="button" class="btn btn-sm btn-outline-danger py-0 px-1" title="Do not receive this line now" onclick="removeGrnLine(this)"><i class="bi bi-x"></i></button></td>
        </tr>
        <?php endforeach; else: ?>
        <!-- Empty first row for manual GRN -->
        <tr class="grn-line" id="grn-row-0">
          <td>
            <select name="grn_lines[0][item_id]" class="form-select form-select-sm select2" style="min-width:180px">
              <option value="">— Select Item —</option>
              <?php foreach ($allItems as $it): ?>
              <option value="<?= $it['id'] ?>" data-uom="<?= $it['uom'] ?>" data-uomid="<?= $it['uom_id'] ?>" data-price="<?= $it['moving_avg_price'] ?>">
                <?= htmlspecialchars($it['item_code'].' – '.$it['item_name']) ?>
              </option>
              <?php endforeach; ?>
            </select>
          </td>
          <td><select name="grn_lines[0][uom_id]" class="form-select form-select-sm grn-uom-sel" style="min-width:80px"><?= $uomOptsHtml ?></select>
              <input type="hidden" name="grn_lines[0][unit_price]" value="0"></td>
          <td><input type="number" name="grn_lines[0][qty_received]" class="form-control form-control-sm" min="0.001" step="0.001" value="1"></td>
          <td><input type="number" name="grn_lines[0][qty_accepted]" class="form-control form-control-sm" min="0" step="0.001" value="1"></td>
          <td><input type="text"   name="grn_lines[0][batch_number]" class="form-control form-control-sm" placeholder="Optional"></td>
          <td><input type="date"   name="grn_lines[0][expiry_date]" class="form-control form-control-sm"></td>
          <td>
            <button type="button" class="btn btn-sm btn-outline-danger py-0 px-1" onclick="removeGrnLine(this)"><i class="bi bi-x"></i></button>
          </td>
        </tr>
        <?php endif; ?>
      </tbody>
    </table>
  </div>
</div>

<div class="d-flex gap-2">
  <button type="submit" class="btn btn-success"><i class="bi bi-check2-circle me-1"></i>Post Goods Receipt</button>
  <a href="<?= admin_url('admin.php?page=msme-erp&tcode=ME23N') ?>" class="btn btn-outline-secondary"><i class="bi bi-x-circle me-1"></i>Cancel</a>
</div>
</form>

<?php
// Slim payload (only what the browser needs) and tolerant of odd characters
// in item names — a failed json_encode() used to leave `const allItems = ;`
// (a JS syntax error that killed every button on this screen).
$slimItems = [];
foreach ($allItems as $__it) {
    $slimItems[$__it['id']] = [
        'id' => $__it['id'], 'item_code' => $__it['item_code'], 'item_name' => $__it['item_name'],
        'uom' => $__it['uom'], 'uom_id' => $__it['uom_id'], 'moving_avg_price' => $__it['moving_avg_price'],
    ];
}
$allItemsJson = json_encode($slimItems ?: new stdClass, JSON_INVALID_UTF8_SUBSTITUTE | JSON_PARTIAL_OUTPUT_ON_ERROR | JSON_HEX_TAG | JSON_HEX_AMP);
if ($allItemsJson === false) $allItemsJson = '{}';
$uomOptsJs = json_encode($uomOptsHtml, JSON_HEX_TAG | JSON_HEX_AMP);
$qcCfgJs = json_encode([
    'items' => array_map('intval', msme_erp_qc_item_ids($companyId)),
    'all'   => (bool) get_option('msme_erp_qc_all_receipts', 0),
    'admin' => (bool) $isSuperAdmin,
    'on'    => ($mvtCode === '101'),
], JSON_HEX_TAG | JSON_HEX_AMP);

$extraScripts = <<<'JS'
<script>
(function () {
  var grnLineIdx = __LINE_IDX__;
  var allItems   = __ALL_ITEMS_JSON__;
  var uomOpts    = __UOM_OPTS_JSON__;
  var qcCfg      = __QC_CFG_JSON__;

  // Item row -> default UOM + (hidden) unit price. The price is never
  // displayed here; it only feeds stock valuation and the % deduction base.
  function bindItemChange() {
    if (!window.jQuery) return;
    jQuery(document).on('change', '#grn-body select[name$="[item_id]"]', function () {
      var d = allItems[this.value], tr = this.closest('tr');
      if (!d || !tr) return;
      var u = tr.querySelector('.grn-uom-sel');            if (u) u.value = d.uom_id;
      var p = tr.querySelector('[name$="[unit_price]"]');  if (p) p.value = parseFloat(d.moving_avg_price || 0).toFixed(2);
    });
  }

  // Accepted follows Received until the user edits Accepted by hand, and can
  // never exceed Received.
  document.addEventListener('input', function (e) {
    var t = e.target; if (!t || !t.name) return;
    var tr = t.closest ? t.closest('tr.grn-line') : null; if (!tr) return;
    var recv = tr.querySelector('[name$="[qty_received]"]'), acc = tr.querySelector('[name$="[qty_accepted]"]');
    if (!recv || !acc) return;
    if (t === acc) { acc.dataset.touched = '1'; }
    if (t === recv && !acc.dataset.touched) { acc.value = recv.value; }
    if (parseFloat(acc.value) > parseFloat(recv.value)) { acc.value = recv.value; }
  });

  // ---- QC gate: materials flagged QC in the item master force Quality Inspection ----
  function refreshQc() {
    var cb = document.getElementById('qcCheck'), note = document.getElementById('qcNote'), ov = document.getElementById('qcOverride');
    if (!cb || !qcCfg.on) return;
    var hit = qcCfg.all;
    if (!hit) {
      Array.prototype.forEach.call(document.querySelectorAll('#grn-body select[name$="[item_id]"]'), function (sel) {
        if (sel.value && qcCfg.items.indexOf(parseInt(sel.value, 10)) !== -1) hit = true;
      });
    }
    if (hit) {
      cb.checked = true;
      if (qcCfg.admin) {
        cb.disabled = false;
        note.style.display = ''; note.innerHTML = '<div class="alert alert-warning py-1 px-2 small mb-0"><i class="bi bi-shield-check me-1"></i>Quality inspection is mandatory for this receipt. As admin you may untick QC? to post straight to stock (the override is audited).</div>';
        if (ov) ov.value = cb.checked ? '0' : '1';
      } else {
        cb.disabled = true;
        note.style.display = ''; note.innerHTML = '<div class="alert alert-info py-1 px-2 small mb-0"><i class="bi bi-lock-fill me-1"></i>Quality inspection is mandatory for this material. Stock will be posted only after QC approval (QA32).</div>';
      }
    } else {
      cb.disabled = false; note.style.display = 'none'; if (ov) ov.value = '0';
    }
  }
  document.addEventListener('change', function (e) {
    if (e.target && e.target.id === 'qcCheck') { var ov = document.getElementById('qcOverride'); if (ov && qcCfg.admin) ov.value = e.target.checked ? '0' : '1'; return; }
    if (e.target && e.target.name && /\[item_id\]$/.test(e.target.name)) refreshQc();
  });
  if (window.jQuery) jQuery(document).on('change', '#grn-body select[name$="[item_id]"]', refreshQc);
  document.addEventListener('click', function (e) { if (e.target && (e.target.closest && e.target.closest('.btn-outline-danger'))) setTimeout(refreshQc, 50); });
  window.addEventListener('load', refreshQc);

  window.addGrnLine = function () {
    var tbody = document.getElementById('grn-body');
    var i = grnLineIdx++;
    var itemOptions = Object.keys(allItems).map(function (k) {
      var it = allItems[k];
      return '<option value="' + it.id + '">' + esc(it.item_code + ' – ' + it.item_name) + '</option>';
    }).join('');
    tbody.insertAdjacentHTML('beforeend',
      '<tr class="grn-line">' +
        '<td><select name="grn_lines[' + i + '][item_id]" class="form-select form-select-sm item-sel" style="min-width:180px">' +
          '<option value="">— Select Item —</option>' + itemOptions + '</select></td>' +
        '<td><select name="grn_lines[' + i + '][uom_id]" class="form-select form-select-sm grn-uom-sel" style="min-width:80px">' + uomOpts + '</select>' +
          '<input type="hidden" name="grn_lines[' + i + '][unit_price]" value="0"></td>' +
        '<td><input type="number" name="grn_lines[' + i + '][qty_received]" class="form-control form-control-sm" min="0.001" step="0.001" value="1"></td>' +
        '<td><input type="number" name="grn_lines[' + i + '][qty_accepted]" class="form-control form-control-sm" min="0" step="0.001" value="1"></td>' +
        '<td><input type="text" name="grn_lines[' + i + '][batch_number]" class="form-control form-control-sm" placeholder="Optional"></td>' +
        '<td><input type="date" name="grn_lines[' + i + '][expiry_date]" class="form-control form-control-sm"></td>' +
        '<td><button type="button" class="btn btn-sm btn-outline-danger py-0 px-1" onclick="removeGrnLine(this)"><i class="bi bi-x"></i></button></td>' +
      '</tr>');
    var rows = tbody.querySelectorAll('.grn-line'), lastRow = rows[rows.length - 1];
    if (window.jQuery && jQuery.fn.select2) {
      jQuery(lastRow).find('.item-sel').select2({ theme: 'bootstrap-5', width: '100%' });
    }
  };

  window.removeGrnLine = function (btn) {
    var rows = document.querySelectorAll('.grn-line');
    if (rows.length < 2) return;
    btn.closest('tr').remove();
  };

  function esc(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML.replace(/"/g, '&quot;'); }

  // ---- Pull from Purchase Order: picking a PO reloads the screen with the PO's data;
  //      picking a supplier narrows the PO list to that supplier's POs. ----
  function bindPoPicker() {
    var pick = document.getElementById('po-pick'); if (!pick) return;
    var base = __MIGO_URL__;
    pick.addEventListener('change', function () { window.location.href = base + (pick.value ? '&po_id=' + encodeURIComponent(pick.value) : ''); });
    var sup = document.querySelector('select[name="supplier_id"]'); if (!sup) return;
    function filter() {
      Array.prototype.forEach.call(pick.options, function (o) {
        if (!o.value) return;
        var hide = sup.value && o.getAttribute('data-supplier') !== sup.value;
        o.hidden = hide; o.disabled = hide;
      });
    }
    sup.addEventListener('change', filter);
    if (window.jQuery) jQuery(sup).on('change', filter);
    filter();
  }

  if (document.readyState === 'complete') { bindItemChange(); bindPoPicker(); } else window.addEventListener('load', function () { bindItemChange(); bindPoPicker(); });
})();
</script>
JS;
$migoUrlJs = json_encode(admin_url('admin.php?page=msme-erp&tcode=MIGO&mvt=101'), JSON_UNESCAPED_SLASHES | JSON_HEX_TAG | JSON_HEX_AMP);
echo str_replace(['__ALL_ITEMS_JSON__', '__UOM_OPTS_JSON__', '__QC_CFG_JSON__', '__LINE_IDX__', '__MIGO_URL__'], [$allItemsJson, $uomOptsJs, $qcCfgJs, (string) max(1, count($poLines)), $migoUrlJs], $extraScripts);
