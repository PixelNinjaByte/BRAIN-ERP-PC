<?php
/**
 * MSME ERP — MM Procurement Follow-up Reports
 * T-Code: ZMMR
 * Developed by: Prigag Online Pvt. Ltd.
 *
 * One From/To-filterable screen with five tabs:
 *   1. PR Released — PO Not Created      (approved PR lines still OPEN)
 *   2. PO Released — Material Not Received
 *   3. PO Sent — Material Not Came In Time (overdue, or received late)
 *   4. Supplier Ranking (automatic score: delivery time, quality, PO terms)
 *   5. GRN Created — Not Transferred to Department
 *
 * Read-only: nothing here writes to any table, so it cannot disturb the
 * PR -> PO -> GRN -> stock flow. Date basis per tab: PR date, PO date,
 * GRN date (ranking uses PO date).
 *
 * "Received" is counted from goods_receipt_lines (GRNs in POSTED or
 * PENDING_QC status) rather than purchase_order_lines.qty_received,
 * because processGRN() overwrites that column with the latest GRN's
 * quantity instead of adding to it, so it under-reports after a second
 * partial receipt against the same PO line.
 */
defined('ABSPATH') || exit;

if (empty($tcode)) $tcode = 'ZMMR';
Auth::requireTcode('ZMMR');

$companyId = Auth::companyId();
$currency  = msme_erp_currency_symbol(Auth::user()['currency'] ?? null);
$today     = date('Y-m-d');

// ---------------- Filters ----------------
$from = sanitize_text_field($_GET['from'] ?? date('Y-m-d', strtotime('-90 days')));
$to   = sanitize_text_field($_GET['to']   ?? $today);
if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $from)) $from = date('Y-m-d', strtotime('-90 days'));
if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $to))   $to   = $today;
if ($from > $to) { $t = $from; $from = $to; $to = $t; }

$views = ['pr_open','po_open','po_late','grn_transfer','ranking'];
$activeTab  = in_array($_GET['view'] ?? '', $views, true) ? $_GET['view'] : 'pr_open';
$lateMode   = (($_GET['late_mode'] ?? '') === 'received_late') ? 'received_late' : 'pending';
$supplierId = (int)($_GET['supplier_id'] ?? 0);

// Ranking weights (percent) — normalised, so they need not add to 100.
$wD = max(0, min(100, (int)($_GET['w_d'] ?? 40)));
$wQ = max(0, min(100, (int)($_GET['w_q'] ?? 35)));
$wT = max(0, min(100, (int)($_GET['w_t'] ?? 25)));
if ($wD + $wQ + $wT === 0) { $wD = 40; $wQ = 35; $wT = 25; }

$suppliers = DB::fetchAll("SELECT id, supplier_code, name FROM suppliers WHERE company_id=? AND is_deleted=0 ORDER BY name", [$companyId]);

$hasNotified = (bool)DB::fetchValue(
    "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=? AND COLUMN_NAME='supplier_notified_at'",
    [DB::tbl('purchase_orders')]
);
$notifiedSel = $hasNotified ? 'po.supplier_notified_at' : 'NULL';

$pageTitle   = 'MM Procurement Follow-up Reports';
$pageTcode   = 'ZMMR';
$breadcrumbs = ['MM', 'Reports', 'Procurement Follow-up'];
erp_render_page_header();

// ---------------------------------------------------------------
// Shared SQL: one row per released PO line with GRN aggregates.
// ---------------------------------------------------------------
$poLineSql = "SELECT po.id AS po_id, po.po_number, po.po_date, po.delivery_date AS po_delivery_date,
        {$notifiedSel} AS notified_at, po.payment_terms,
        s.id AS supplier_id, s.supplier_code, s.name AS supplier_name,
        pol.id AS pol_id, pol.line_num, pol.qty_ordered, pol.unit_price,
        COALESCE(pol.delivery_date, po.delivery_date) AS due_date,
        i.item_code, i.item_name, un.code AS uom,
        COALESCE(gr.recvd, 0)       AS qty_recvd,
        COALESCE(gr.qc_hold, 0)     AS qty_qc_hold,
        COALESCE(gr.recv_posted, 0) AS recv_posted,
        COALESCE(gr.acc_posted, 0)  AS acc_posted,
        COALESCE(gr.price_dev, 0)   AS price_dev,
        COALESCE(gr.grn_lines, 0)   AS grn_lines,
        gr.last_grn, gr.first_grn
     FROM purchase_orders po
     JOIN suppliers s ON s.id = po.supplier_id
     JOIN purchase_order_lines pol ON pol.po_id = po.id
     JOIN items i ON i.id = pol.item_id
     LEFT JOIN units_of_measure un ON un.id = pol.uom_id
     LEFT JOIN (
        SELECT grl.po_line_id,
               SUM(grl.qty_received) AS recvd,
               SUM(CASE WHEN g.status = 'PENDING_QC' THEN grl.qty_received ELSE 0 END) AS qc_hold,
               SUM(CASE WHEN g.status = 'POSTED' THEN grl.qty_received ELSE 0 END) AS recv_posted,
               SUM(CASE WHEN g.status = 'POSTED' THEN grl.qty_accepted ELSE 0 END) AS acc_posted,
               SUM(CASE WHEN COALESCE(grl.unit_price,0) > 0 AND grl.unit_price > pl2.unit_price * 1.005 THEN 1 ELSE 0 END) AS price_dev,
               COUNT(grl.id) AS grn_lines,
               MAX(g.grn_date) AS last_grn, MIN(g.grn_date) AS first_grn
        FROM goods_receipt_lines grl
        JOIN goods_receipts g ON g.id = grl.grn_id AND g.status IN ('POSTED','PENDING_QC')
        JOIN purchase_order_lines pl2 ON pl2.id = grl.po_line_id
        GROUP BY grl.po_line_id
     ) gr ON gr.po_line_id = pol.id
     WHERE po.company_id = ? AND po.is_deleted = 0 AND po.release_status = 'RELEASED'
       AND po.status NOT IN ('CANCELLED','REJECTED','DRAFT')
       AND pol.status NOT IN ('CANCELLED')
       AND po.po_date BETWEEN ? AND ?";
$poLineParams = [$companyId, $from, $to];
if ($supplierId) { $poLineSql .= " AND po.supplier_id = ?"; $poLineParams[] = $supplierId; }
$poLineSql .= " ORDER BY po.po_date ASC, po.id ASC, pol.line_num ASC";

$poLines = DB::fetchAll($poLineSql, $poLineParams);

// -----------------------------------------------------------
// Tab 1 — PR released, PO not created
// -----------------------------------------------------------
$prOpen = DB::fetchAll(
    "SELECT pr.id AS pr_id, pr.pr_number, pr.pr_date, pr.required_by AS pr_required_by,
            d.name AS dept_name, u.full_name AS requester,
            prl.line_num, prl.est_price, prl.required_by AS line_required_by,
            COALESCE(prl.qty_approved, prl.qty_requested) AS qty,
            i.item_code, i.item_name, un.code AS uom,
            (SELECT COUNT(*) FROM rfqs rq WHERE rq.pr_id = pr.id) AS rfq_count,
            DATEDIFF(CURDATE(), pr.pr_date) AS age_days
     FROM purchase_requisitions pr
     JOIN purchase_requisition_lines prl ON prl.pr_id = pr.id AND prl.status = 'OPEN'
     JOIN items i ON i.id = prl.item_id
     LEFT JOIN units_of_measure un ON un.id = prl.uom_id
     LEFT JOIN departments d ON d.id = pr.dept_id
     LEFT JOIN users u ON u.id = pr.requested_by
     WHERE pr.company_id = ? AND pr.status = 'APPROVED' AND pr.pr_date BETWEEN ? AND ?
     ORDER BY pr.pr_date ASC, pr.id ASC, prl.line_num ASC",
    [$companyId, $from, $to]
);
$prOpenValue = 0.0; $prOpenDocs = [];
foreach ($prOpen as $r) { $prOpenValue += (float)$r['qty'] * (float)$r['est_price']; $prOpenDocs[$r['pr_id']] = 1; }

// -----------------------------------------------------------
// Tab 2 / 3 — from $poLines
// -----------------------------------------------------------
$tol = 0.0005;
$poOpen = []; $poLate = []; $poLateReceived = []; $noDueDate = 0;
foreach ($poLines as $r) {
    $ord = (float)$r['qty_ordered']; $rec = (float)$r['qty_recvd'];
    $pend = $ord - $rec;
    $due = $r['due_date'];
    if ($pend > $tol) {
        $r['qty_pending'] = $pend;
        $r['pending_value'] = $pend * (float)$r['unit_price'];
        $r['days_since_po'] = (int)floor((strtotime($today) - strtotime($r['po_date'])) / 86400);
        $poOpen[] = $r;
        if (!$due) { $noDueDate++; }
        elseif ($due < $today) {
            $r['days_late'] = (int)floor((strtotime($today) - strtotime($due)) / 86400);
            $poLate[] = $r;
        }
    } elseif ($due && $r['last_grn'] && $r['last_grn'] > $due) {
        $r['days_late'] = (int)floor((strtotime($r['last_grn']) - strtotime($due)) / 86400);
        $poLateReceived[] = $r;
    }
}
usort($poLate, fn($a, $b) => $b['days_late'] <=> $a['days_late']);
usort($poLateReceived, fn($a, $b) => $b['days_late'] <=> $a['days_late']);
$poOpenValue = array_sum(array_column($poOpen, 'pending_value'));
$poOpenDocs  = count(array_unique(array_column($poOpen, 'po_id')));
$poLateValue = array_sum(array_column($poLate, 'pending_value'));
$poLateDocs  = count(array_unique(array_column($poLate, 'po_id')));
$lateRows    = $lateMode === 'received_late' ? $poLateReceived : $poLate;

// -----------------------------------------------------------
// Tab 5 — GRN posted, stock not transferred onward
// (FIFO: each LT01 transfer out of a warehouse consumes the oldest
//  GRN lines of that item in that warehouse dated on/before it.)
// -----------------------------------------------------------
$grnRows = DB::fetchAll(
    "SELECT g.id AS grn_id, g.grn_number, g.grn_date, g.status AS grn_status, g.warehouse_id,
            w.name AS wh_name, grl.id AS grl_id, grl.item_id, grl.qty_received, grl.qty_accepted,
            i.item_code, i.item_name, un.code AS uom,
            po.po_number, s.name AS supplier_name, d.name AS req_dept
     FROM goods_receipts g
     JOIN goods_receipt_lines grl ON grl.grn_id = g.id
     JOIN items i ON i.id = grl.item_id
     LEFT JOIN units_of_measure un ON un.id = grl.uom_id
     LEFT JOIN warehouses w ON w.id = g.warehouse_id
     LEFT JOIN suppliers s ON s.id = g.supplier_id
     LEFT JOIN purchase_orders po ON po.id = g.po_id
     LEFT JOIN purchase_order_lines pol ON pol.id = grl.po_line_id
     LEFT JOIN purchase_requisition_lines prl ON prl.id = pol.pr_line_id
     LEFT JOIN purchase_requisitions pr ON pr.id = prl.pr_id
     LEFT JOIN departments d ON d.id = pr.dept_id
     WHERE g.company_id = ? AND g.status IN ('POSTED','PENDING_QC')
     ORDER BY g.grn_date ASC, g.id ASC, grl.id ASC",
    [$companyId]
);
$xfers = DB::fetchAll(
    "SELECT st.from_wh_id, stl.item_id, st.transfer_date, SUM(stl.qty_transfer) AS qty
     FROM stock_transfers st
     JOIN stock_transfer_lines stl ON stl.sto_id = st.id
     WHERE st.company_id = ? AND st.status = 'TRANSFERRED'
     GROUP BY st.from_wh_id, stl.item_id, st.transfer_date
     ORDER BY st.transfer_date ASC",
    [$companyId]
);
$grnPool = []; // key "wh|item" => list of row indexes (already oldest first)
foreach ($grnRows as $k => $r) {
    $grnRows[$k]['xfer_qty'] = 0.0; $grnRows[$k]['last_xfer'] = null;
    if ($r['grn_status'] === 'POSTED') $grnPool[$r['warehouse_id'] . '|' . $r['item_id']][] = $k;
}
foreach ($xfers as $x) {
    $key = $x['from_wh_id'] . '|' . $x['item_id'];
    $left = (float)$x['qty'];
    foreach (($grnPool[$key] ?? []) as $k) {
        if ($left <= $tol) break;
        if ($grnRows[$k]['grn_date'] > $x['transfer_date']) continue;
        $room = (float)$grnRows[$k]['qty_accepted'] - $grnRows[$k]['xfer_qty'];
        if ($room <= $tol) continue;
        $take = min($room, $left);
        $grnRows[$k]['xfer_qty'] += $take; $grnRows[$k]['last_xfer'] = $x['transfer_date'];
        $left -= $take;
    }
}
$grnPending = [];
foreach ($grnRows as $r) {
    if ($r['grn_date'] < $from || $r['grn_date'] > $to) continue;
    if ($r['grn_status'] === 'PENDING_QC') { $r['pending'] = (float)$r['qty_received']; $r['state'] = 'QC'; }
    else {
        $r['pending'] = (float)$r['qty_accepted'] - $r['xfer_qty'];
        if ($r['pending'] <= $tol) continue;
        $r['state'] = $r['xfer_qty'] > $tol ? 'PARTIAL' : 'NONE';
    }
    $r['days_since'] = (int)floor((strtotime($today) - strtotime($r['grn_date'])) / 86400);
    $grnPending[] = $r;
}
$grnPendingDocs = count(array_unique(array_column($grnPending, 'grn_id')));

// -----------------------------------------------------------
// Tab 4 — Supplier ranking (automatic, computed live)
// -----------------------------------------------------------
$agg = [];
foreach ($poLines as $r) {
    $sid = (int)$r['supplier_id'];
    if (!isset($agg[$sid])) $agg[$sid] = [
        'name' => $r['supplier_name'], 'code' => $r['supplier_code'], 'pos' => [],
        'eval' => 0, 'ontime' => 0, 'delay_sum' => 0, 'late_lines' => 0,
        'ord_due' => 0.0, 'got_due' => 0.0,
        'recv_posted' => 0.0, 'acc_posted' => 0.0,
        'grn_lines' => 0, 'price_dev' => 0,
    ];
    $a = &$agg[$sid];
    $a['pos'][$r['po_id']] = 1;
    $ord = (float)$r['qty_ordered']; $rec = (float)$r['qty_recvd']; $due = $r['due_date'];
    $full = ($ord - $rec) <= $tol;

    // Delivery time + fill: only lines that are complete, or already past due
    if ($due && $ord > $tol && ($full || $due < $today)) {
        $a['eval']++;
        $a['ord_due'] += $ord; $a['got_due'] += min($rec, $ord);
        if ($full && $r['last_grn'] && $r['last_grn'] <= $due) $a['ontime']++;
        else {
            $a['late_lines']++;
            $endDate = ($full && $r['last_grn']) ? $r['last_grn'] : $today;
            $a['delay_sum'] += max(0, (int)floor((strtotime($endDate) - strtotime($due)) / 86400));
        }
    }
    $a['recv_posted'] += (float)$r['recv_posted']; $a['acc_posted'] += (float)$r['acc_posted'];
    $a['grn_lines']   += (int)$r['grn_lines'];     $a['price_dev'] += (int)$r['price_dev'];
    unset($a);
}
$ranked = []; $unranked = [];
foreach ($agg as $sid => $a) {
    $dScore = $a['eval'] > 0 ? 100 * $a['ontime'] / $a['eval'] : null;
    $qScore = $a['recv_posted'] > 0 ? 100 * $a['acc_posted'] / $a['recv_posted'] : null;
    $fill   = $a['ord_due'] > 0 ? 100 * $a['got_due'] / $a['ord_due'] : null;
    $price  = $a['grn_lines'] > 0 ? 100 * ($a['grn_lines'] - $a['price_dev']) / $a['grn_lines'] : null;
    // PO-terms score = 60% quantity as ordered + 40% price as per PO
    if ($fill !== null && $price !== null) $tScore = 0.6 * $fill + 0.4 * $price;
    elseif ($fill !== null) $tScore = $fill;
    else $tScore = $price;
    $parts = [[$dScore, $wD], [$qScore, $wQ], [$tScore, $wT]];
    $sum = 0; $wsum = 0;
    foreach ($parts as [$sc, $w]) if ($sc !== null && $w > 0) { $sum += $sc * $w; $wsum += $w; }
    $row = $a + ['sid' => $sid, 'd' => $dScore, 'q' => $qScore, 't' => $tScore, 'fill' => $fill, 'price' => $price,
                 'overall' => $wsum > 0 ? $sum / $wsum : null,
                 'avg_delay' => $a['late_lines'] > 0 ? $a['delay_sum'] / $a['late_lines'] : 0];
    if ($row['overall'] === null) $unranked[] = $row; else $ranked[] = $row;
}
usort($ranked, fn($x, $y) => [$y['overall'], $y['eval']] <=> [$x['overall'], $x['eval']]);
$grade = static fn(float $s): array => $s >= 85 ? ['A', 'success'] : ($s >= 70 ? ['B', 'primary'] : ($s >= 50 ? ['C', 'warning text-dark'] : ['D', 'danger']));

// ---------------- Render ----------------
$qsBase = 'page=msme-erp&tcode=ZMMR&from=' . urlencode($from) . '&to=' . urlencode($to)
        . ($supplierId ? '&supplier_id=' . $supplierId : '')
        . "&w_d={$wD}&w_q={$wQ}&w_t={$wT}";
$fmtQ = static fn($v) => rtrim(rtrim(number_format((float)$v, 3), '0'), '.');
$ageBadge = static function (int $d): string {
    return '<span class="badge ' . ($d > 15 ? 'bg-danger' : ($d > 7 ? 'bg-warning text-dark' : 'bg-light text-dark')) . '">' . $d . 'd</span>';
};
?>
<form method="GET" class="d-flex flex-wrap align-items-end gap-2 mb-3 no-print">
  <input type="hidden" name="page" value="msme-erp">
  <input type="hidden" name="tcode" value="ZMMR">
  <input type="hidden" name="view" value="<?= esc_attr($activeTab) ?>">
  <input type="hidden" name="late_mode" value="<?= esc_attr($lateMode) ?>">
  <div><label class="form-label small mb-0">From</label>
    <input type="date" name="from" class="form-control form-control-sm" value="<?= esc_attr($from) ?>"></div>
  <div><label class="form-label small mb-0">To</label>
    <input type="date" name="to" class="form-control form-control-sm" value="<?= esc_attr($to) ?>"></div>
  <div><label class="form-label small mb-0">Supplier <span class="text-muted">(PO tabs)</span></label>
    <select name="supplier_id" class="form-select form-select-sm">
      <option value="">— All —</option>
      <?php foreach ($suppliers as $s): ?>
      <option value="<?= (int)$s['id'] ?>" <?= $supplierId === (int)$s['id'] ? 'selected' : '' ?>><?= esc_html($s['supplier_code'] . ' — ' . $s['name']) ?></option>
      <?php endforeach; ?>
    </select></div>
  <?php if ($activeTab === 'ranking'): ?>
  <div><label class="form-label small mb-0">Weight: Delivery %</label>
    <input type="number" name="w_d" min="0" max="100" class="form-control form-control-sm" style="width:90px" value="<?= $wD ?>"></div>
  <div><label class="form-label small mb-0">Quality %</label>
    <input type="number" name="w_q" min="0" max="100" class="form-control form-control-sm" style="width:90px" value="<?= $wQ ?>"></div>
  <div><label class="form-label small mb-0">PO Terms %</label>
    <input type="number" name="w_t" min="0" max="100" class="form-control form-control-sm" style="width:90px" value="<?= $wT ?>"></div>
  <?php else: ?>
  <input type="hidden" name="w_d" value="<?= $wD ?>"><input type="hidden" name="w_q" value="<?= $wQ ?>"><input type="hidden" name="w_t" value="<?= $wT ?>">
  <?php endif; ?>
  <button class="btn btn-primary btn-sm"><i class="bi bi-funnel me-1"></i>Apply</button>
  <button type="button" onclick="mmrExportCsv()" class="btn btn-outline-success btn-sm ms-auto"><i class="bi bi-file-earmark-spreadsheet me-1"></i>Export CSV</button>
  <button type="button" onclick="window.print()" class="btn btn-outline-secondary btn-sm"><i class="bi bi-printer me-1"></i>Print</button>
</form>

<div class="row g-3 mb-3">
  <?php
  $cards = [
    ['pr_open',      count($prOpenDocs),   'PR Released — PO Not Created', $currency . number_format($prOpenValue, 0) . ' est. · ' . count($prOpen) . ' lines', 'warning'],
    ['po_open',      $poOpenDocs,          'PO Released — Material Not Received', $currency . number_format($poOpenValue, 0) . ' pending', 'danger'],
    ['po_late',      $poLateDocs,          'PO Sent — Not Came In Time', count($poLate) . ' overdue lines', 'danger'],
    ['ranking',      count($ranked),       'Suppliers Ranked', 'delivery · quality · PO terms', 'primary'],
    ['grn_transfer', $grnPendingDocs,      'GRN — Not Transferred to Dept', count($grnPending) . ' lines', 'warning'],
  ];
  foreach ($cards as [$v, $n, $label, $sub, $col]): ?>
  <div class="col-6" style="flex:1 1 19%;max-width:20%;">
    <a href="?<?= $qsBase ?>&view=<?= $v ?>" class="text-decoration-none">
      <div class="card border-0 shadow-sm text-center p-3 h-100 <?= $activeTab === $v ? 'border border-primary' : '' ?>">
        <div class="fs-5 fw-bold text-<?= $col ?>"><?= (int)$n ?></div>
        <div class="small text-muted"><?= esc_html($label) ?></div>
        <div class="small text-muted"><?= esc_html($sub) ?></div>
      </div>
    </a>
  </div>
  <?php endforeach; ?>
</div>

<ul class="nav nav-tabs mb-3 no-print">
  <?php foreach ([
    'pr_open' => '1. PR Released — PO Not Created', 'po_open' => '2. PO Released — Material Not Received',
    'po_late' => '3. PO Sent — Not In Time', 'ranking' => '4. Supplier Ranking', 'grn_transfer' => '5. GRN — Not Transferred'
  ] as $v => $label): ?>
  <li class="nav-item"><a class="nav-link <?= $activeTab === $v ? 'active' : '' ?>" href="?<?= $qsBase ?>&view=<?= $v ?>"><?= esc_html($label) ?></a></li>
  <?php endforeach; ?>
</ul>

<div class="card border-0 shadow-sm">
  <div class="card-header bg-white py-2 small text-muted">
    Period: <?= esc_html($from) ?> to <?= esc_html($to) ?>
    <?php if ($activeTab === 'pr_open'): ?> &nbsp;|&nbsp; by PR date · approved PRs with lines not yet on a PO
    <?php elseif ($activeTab === 'grn_transfer'): ?> &nbsp;|&nbsp; by GRN date
    <?php else: ?> &nbsp;|&nbsp; by PO date · released POs only<?php endif; ?>
  </div>
  <div class="card-body p-0 table-responsive">

<?php if ($activeTab === 'pr_open'): ?>
    <table id="mmr-table" class="table table-sm table-hover mb-0 erp-table">
      <thead><tr><th>PR#</th><th>PR Date</th><th>Dept</th><th>Requester</th><th>Ln</th><th>Material</th><th class="text-end">Qty</th><th>UOM</th><th class="text-end">Est. Value</th><th>Required By</th><th>RFQ</th><th>Age</th></tr></thead>
      <tbody>
      <?php foreach ($prOpen as $r): $req = $r['line_required_by'] ?: $r['pr_required_by']; $overdue = $req && $req < $today; ?>
        <tr>
          <td><a href="?page=msme-erp&tcode=ME53N&id=<?= (int)$r['pr_id'] ?>"><?= esc_html($r['pr_number']) ?></a></td>
          <td class="small"><?= esc_html($r['pr_date']) ?></td>
          <td class="small"><?= esc_html($r['dept_name'] ?? '—') ?></td>
          <td class="small"><?= esc_html($r['requester'] ?? '—') ?></td>
          <td class="small"><?= (int)$r['line_num'] ?></td>
          <td class="small"><?= esc_html($r['item_code'] . ' — ' . $r['item_name']) ?></td>
          <td class="text-end small"><?= $fmtQ($r['qty']) ?></td>
          <td class="small"><?= esc_html($r['uom'] ?? '') ?></td>
          <td class="text-end small"><?= $currency ?><?= number_format((float)$r['qty'] * (float)$r['est_price'], 2) ?></td>
          <td class="small <?= $overdue ? 'fw-bold text-danger' : '' ?>"><?= esc_html($req ?: '—') ?></td>
          <td class="small"><?= (int)$r['rfq_count'] > 0 ? '<span class="badge bg-info text-dark">RFQ sent</span>' : '<span class="badge bg-light text-dark">No RFQ</span>' ?></td>
          <td><?= $ageBadge((int)$r['age_days']) ?></td>
        </tr>
      <?php endforeach; if (!$prOpen): ?>
        <tr><td colspan="12" class="text-center text-muted py-4"><i class="bi bi-check-circle-fill text-success"></i> Every released PR in this period already has a PO.</td></tr>
      <?php endif; ?>
      </tbody>
      <?php if ($prOpen): ?><tfoot class="table-light fw-bold"><tr><td colspan="8" class="text-end small">Total Est. Value (<?= count($prOpenDocs) ?> PRs):</td><td class="text-end small"><?= $currency ?><?= number_format($prOpenValue, 2) ?></td><td colspan="3"></td></tr></tfoot><?php endif; ?>
    </table>

<?php elseif ($activeTab === 'po_open'): ?>
    <table id="mmr-table" class="table table-sm table-hover mb-0 erp-table">
      <thead><tr><th>PO#</th><th>PO Date</th><th>Sent To Supplier</th><th>Supplier</th><th>Ln</th><th>Material</th><th class="text-end">Ordered</th><th class="text-end">Received</th><th class="text-end">Pending</th><th>UOM</th><th class="text-end">Pending Value</th><th>Due</th><th>Age</th></tr></thead>
      <tbody>
      <?php foreach ($poOpen as $r): $overdue = $r['due_date'] && $r['due_date'] < $today; ?>
        <tr>
          <td><a href="?page=msme-erp&tcode=ME23N&id=<?= (int)$r['po_id'] ?>"><?= esc_html($r['po_number']) ?></a></td>
          <td class="small"><?= esc_html($r['po_date']) ?></td>
          <td class="small"><?= $r['notified_at'] ? esc_html(substr($r['notified_at'], 0, 10)) : '<span class="text-muted">released</span>' ?></td>
          <td class="small"><?= esc_html($r['supplier_code'] . ' — ' . $r['supplier_name']) ?></td>
          <td class="small"><?= (int)$r['line_num'] ?></td>
          <td class="small"><?= esc_html($r['item_code'] . ' — ' . $r['item_name']) ?></td>
          <td class="text-end small"><?= $fmtQ($r['qty_ordered']) ?></td>
          <td class="text-end small"><?= $fmtQ($r['qty_recvd']) ?><?= (float)$r['qty_qc_hold'] > 0 ? ' <span class="badge bg-info text-dark" title="Received, awaiting QC">' . $fmtQ($r['qty_qc_hold']) . ' in QC</span>' : '' ?></td>
          <td class="text-end small fw-bold text-danger"><?= $fmtQ($r['qty_pending']) ?></td>
          <td class="small"><?= esc_html($r['uom'] ?? '') ?></td>
          <td class="text-end small"><?= $currency ?><?= number_format($r['pending_value'], 2) ?></td>
          <td class="small <?= $overdue ? 'fw-bold text-danger' : '' ?>"><?= esc_html($r['due_date'] ?: 'No date') ?></td>
          <td><?= $ageBadge((int)$r['days_since_po']) ?></td>
        </tr>
      <?php endforeach; if (!$poOpen): ?>
        <tr><td colspan="13" class="text-center text-muted py-4"><i class="bi bi-check-circle-fill text-success"></i> All material against released POs in this period has been received.</td></tr>
      <?php endif; ?>
      </tbody>
      <?php if ($poOpen): ?><tfoot class="table-light fw-bold"><tr><td colspan="10" class="text-end small">Total Pending Value (<?= $poOpenDocs ?> POs):</td><td class="text-end small"><?= $currency ?><?= number_format($poOpenValue, 2) ?></td><td colspan="2"></td></tr></tfoot><?php endif; ?>
    </table>

<?php elseif ($activeTab === 'po_late'): ?>
    <div class="p-2 border-bottom no-print d-flex gap-2 align-items-center">
      <a class="btn btn-sm <?= $lateMode === 'pending' ? 'btn-danger' : 'btn-outline-danger' ?>" href="?<?= $qsBase ?>&view=po_late&late_mode=pending">Overdue &amp; still not received (<?= count($poLate) ?>)</a>
      <a class="btn btn-sm <?= $lateMode === 'received_late' ? 'btn-warning' : 'btn-outline-warning' ?>" href="?<?= $qsBase ?>&view=po_late&late_mode=received_late">Received, but late (<?= count($poLateReceived) ?>)</a>
      <?php if ($noDueDate): ?><span class="small text-muted ms-2"><?= (int)$noDueDate ?> pending line(s) have no delivery date and can't be judged late.</span><?php endif; ?>
    </div>
    <table id="mmr-table" class="table table-sm table-hover mb-0 erp-table">
      <thead><tr><th>PO#</th><th>PO Date</th><th>Sent To Supplier</th><th>Supplier</th><th>Material</th><th class="text-end">Ordered</th><th class="text-end">Received</th><th class="text-end">Pending</th><th>Promised Date</th><th><?= $lateMode === 'pending' ? 'Days Overdue' : 'Received On / Days Late' ?></th></tr></thead>
      <tbody>
      <?php foreach ($lateRows as $r): ?>
        <tr class="<?= $lateMode === 'pending' && $r['days_late'] > 15 ? 'table-danger' : '' ?>">
          <td><a href="?page=msme-erp&tcode=ME23N&id=<?= (int)$r['po_id'] ?>"><?= esc_html($r['po_number']) ?></a></td>
          <td class="small"><?= esc_html($r['po_date']) ?></td>
          <td class="small"><?= $r['notified_at'] ? esc_html(substr($r['notified_at'], 0, 10)) : '<span class="text-muted">released</span>' ?></td>
          <td class="small"><?= esc_html($r['supplier_code'] . ' — ' . $r['supplier_name']) ?></td>
          <td class="small"><?= esc_html($r['item_code'] . ' — ' . $r['item_name']) ?></td>
          <td class="text-end small"><?= $fmtQ($r['qty_ordered']) ?> <?= esc_html($r['uom'] ?? '') ?></td>
          <td class="text-end small"><?= $fmtQ($r['qty_recvd']) ?></td>
          <td class="text-end small fw-bold <?= isset($r['qty_pending']) ? 'text-danger' : '' ?>"><?= isset($r['qty_pending']) ? $fmtQ($r['qty_pending']) : '0' ?></td>
          <td class="small"><?= esc_html($r['due_date']) ?></td>
          <td class="small"><?php if ($lateMode === 'received_late'): ?><?= esc_html($r['last_grn']) ?> · <?php endif; ?><span class="badge bg-danger"><?= (int)$r['days_late'] ?>d</span></td>
        </tr>
      <?php endforeach; if (!$lateRows): ?>
        <tr><td colspan="10" class="text-center text-muted py-4"><i class="bi bi-check-circle-fill text-success"></i> Nothing late in this period.</td></tr>
      <?php endif; ?>
      </tbody>
      <?php if ($lateMode === 'pending' && $poLate): ?><tfoot class="table-light fw-bold"><tr><td colspan="7" class="text-end small">Overdue value:</td><td colspan="3" class="small"><?= $currency ?><?= number_format($poLateValue, 2) ?></td></tr></tfoot><?php endif; ?>
    </table>

<?php elseif ($activeTab === 'ranking'): ?>
    <div class="p-2 border-bottom small text-muted">
      Computed automatically from released POs and their GRNs in the period. Overall = weighted average of
      <b>Delivery</b> (<?= $wD ?>%: lines received by the promised date ÷ lines due or complete),
      <b>Quality</b> (<?= $wQ ?>%: accepted ÷ received on posted GRNs) and
      <b>PO Terms</b> (<?= $wT ?>%: 60% quantity supplied as ordered + 40% GRN price within 0.5% of PO price).
      A component with no data yet is skipped and the others re-weighted. Grade: A ≥ 85, B ≥ 70, C ≥ 50, else D.
    </div>
    <table id="mmr-table" class="table table-sm table-hover mb-0 erp-table">
      <thead><tr><th>Rank</th><th>Supplier</th><th>POs</th><th>Lines Judged</th><th>On-time</th><th>Avg Delay (late lines)</th><th>Qty as Ordered</th><th>Price as PO</th><th>Quality</th><th>Delivery</th><th>Quality</th><th>PO Terms</th><th style="min-width:150px">Overall</th><th>Grade</th></tr></thead>
      <tbody>
      <?php $pct = static fn($v) => $v === null ? '—' : number_format($v, 1) . '%'; $rank = 0;
      foreach ($ranked as $r): $rank++; [$g, $gc] = $grade($r['overall']); ?>
        <tr>
          <td class="fw-bold"><?= $rank ?></td>
          <td class="small"><?= esc_html($r['code'] . ' — ' . $r['name']) ?></td>
          <td class="small"><?= count($r['pos']) ?></td>
          <td class="small"><?= (int)$r['eval'] ?></td>
          <td class="small"><?= (int)$r['ontime'] ?>/<?= (int)$r['eval'] ?></td>
          <td class="small"><?= $r['late_lines'] ? number_format($r['avg_delay'], 1) . 'd' : '—' ?></td>
          <td class="small"><?= $pct($r['fill']) ?></td>
          <td class="small"><?= $pct($r['price']) ?></td>
          <td class="small"><?= $r['recv_posted'] > 0 ? $fmtQ($r['acc_posted']) . '/' . $fmtQ($r['recv_posted']) : '—' ?></td>
          <td class="small"><?= $pct($r['d']) ?></td>
          <td class="small"><?= $pct($r['q']) ?></td>
          <td class="small"><?= $pct($r['t']) ?></td>
          <td><div class="progress" style="height:14px"><div class="progress-bar bg-<?= explode(' ', $gc)[0] ?>" style="width:<?= (int)round($r['overall']) ?>%"><?= number_format($r['overall'], 1) ?></div></div></td>
          <td><span class="badge bg-<?= $gc ?>"><?= $g ?></span></td>
        </tr>
      <?php endforeach;
      foreach ($unranked as $r): ?>
        <tr class="text-muted">
          <td>—</td><td class="small"><?= esc_html($r['code'] . ' — ' . $r['name']) ?></td>
          <td class="small"><?= count($r['pos']) ?></td><td colspan="10" class="small">Not enough data yet (nothing received and nothing past its due date).</td><td></td>
        </tr>
      <?php endforeach; if (!$ranked && !$unranked): ?>
        <tr><td colspan="14" class="text-center text-muted py-4">No released POs in this period.</td></tr>
      <?php endif; ?>
      </tbody>
    </table>

<?php elseif ($activeTab === 'grn_transfer'): ?>
    <div class="p-2 border-bottom small text-muted">
      Stock transfers (LT01) don't carry a department, so a GRN line counts as "transferred" when later LT01 transfers out of the
      same warehouse cover that item (oldest GRN first). The department shown is the one on the originating PR.
      GRNs awaiting QC haven't entered stock yet and are listed separately flagged.
    </div>
    <table id="mmr-table" class="table table-sm table-hover mb-0 erp-table">
      <thead><tr><th>GRN#</th><th>GRN Date</th><th>PO#</th><th>Supplier</th><th>Material</th><th>Warehouse</th><th>Requested By Dept</th><th class="text-end">Accepted</th><th class="text-end">Transferred</th><th class="text-end">Not Transferred</th><th>UOM</th><th>State</th><th>Age</th></tr></thead>
      <tbody>
      <?php foreach ($grnPending as $r): ?>
        <tr>
          <td><a href="?page=msme-erp&tcode=MIGO&grn_id=<?= (int)$r['grn_id'] ?>"><?= esc_html($r['grn_number']) ?></a></td>
          <td class="small"><?= esc_html($r['grn_date']) ?></td>
          <td class="small"><?= esc_html($r['po_number'] ?? '—') ?></td>
          <td class="small"><?= esc_html($r['supplier_name'] ?? '—') ?></td>
          <td class="small"><?= esc_html($r['item_code'] . ' — ' . $r['item_name']) ?></td>
          <td class="small"><?= esc_html($r['wh_name'] ?? '—') ?></td>
          <td class="small"><?= esc_html($r['req_dept'] ?? '—') ?></td>
          <td class="text-end small"><?= $fmtQ($r['state'] === 'QC' ? $r['qty_received'] : $r['qty_accepted']) ?></td>
          <td class="text-end small"><?= $r['state'] === 'QC' ? '—' : $fmtQ($r['xfer_qty']) ?></td>
          <td class="text-end small fw-bold text-danger"><?= $fmtQ($r['pending']) ?></td>
          <td class="small"><?= esc_html($r['uom'] ?? '') ?></td>
          <td><?php if ($r['state'] === 'QC'): ?><span class="badge bg-info text-dark">Awaiting QC</span>
              <?php elseif ($r['state'] === 'PARTIAL'): ?><span class="badge bg-warning text-dark">Partly transferred</span>
              <?php else: ?><span class="badge bg-danger">Not transferred</span><?php endif; ?></td>
          <td><?= $ageBadge((int)$r['days_since']) ?></td>
        </tr>
      <?php endforeach; if (!$grnPending): ?>
        <tr><td colspan="13" class="text-center text-muted py-4"><i class="bi bi-check-circle-fill text-success"></i> Everything received in this period has been transferred onward.</td></tr>
      <?php endif; ?>
      </tbody>
    </table>
<?php endif; ?>

  </div>
</div>

<script>
function mmrExportCsv() {
  var t = document.getElementById('mmr-table'); if (!t) return;
  var rows = [].slice.call(t.querySelectorAll('tr')).map(function (tr) {
    return [].slice.call(tr.children).map(function (c) {
      return '"' + c.innerText.replace(/\s+/g, ' ').trim().replace(/"/g, '""') + '"';
    }).join(',');
  });
  var a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['\ufeff' + rows.join('\n')], {type: 'text/csv'}));
  a.download = 'mm_report_<?= esc_js($activeTab) ?>_<?= esc_js($from) ?>_to_<?= esc_js($to) ?>.csv';
  document.body.appendChild(a); a.click(); a.remove();
}
</script>
