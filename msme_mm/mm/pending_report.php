<?php
/**
 * MSME ERP — Material Management Pending / Status Report
 * T-Code: ME2PN (Purchase Requests, Purchase Orders, PR Not Processed,
 *         PO Made But Material Not Received, Payment Pending)
 * Developed by: Prigag Online Pvt. Ltd.
 *
 * One period-filterable (From/To) report screen covering the five MM
 * status views requested: all PRs, all POs, PRs still not converted to
 * a PO (open lines), POs whose material has not fully arrived, and
 * supplier invoices still awaiting payment — with a summary strip on
 * top and a tab per detail list underneath. Every date range below
 * filters on the relevant document's own date column (pr_date /
 * po_date / invoice_date), never on today's date, so a period search
 * reliably finds documents raised inside that window regardless of
 * their current status.
 */
defined('ABSPATH') || exit;

if (empty($tcode)) $tcode = 'ME2PN';
Auth::requireTcode('ME2PN');

$companyId = Auth::companyId();
$currency  = msme_erp_currency_symbol(Auth::user()['currency'] ?? null);

$from = sanitize_text_field($_GET['from'] ?? date('Y-m-01'));
$to   = sanitize_text_field($_GET['to']   ?? date('Y-m-d'));
if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $from)) $from = date('Y-m-01');
if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $to))   $to   = date('Y-m-d');

$activeTab = in_array($_GET['view'] ?? '', ['pr','po','pr_pending','po_pending','payment_pending'], true)
    ? $_GET['view'] : 'pr_pending';

// ---- Category / Unit (Plant) / Department filter ----
// Same OS01 expense-center trio (Category > Unit/Factory > Department)
// used across ME51N/ME21N/MIGO create screens. Only PR, PO and GRN
// actually save these columns on their own header (see
// msme_erp_cost_center_from_post() call sites) — Supplier Invoice
// (MR01) never wired this in, so the Payment Pending tab below derives
// its cost center from the invoice's linked PO instead of its own
// (always-NULL) columns.
$categoryId = (int)($_GET['category_id']   ?? 0);
$unitId     = (int)($_GET['unit_id']       ?? 0);
$deptId     = (int)($_GET['department_id'] ?? 0);

$ccCategories = DB::fetchAll("SELECT id, code, name FROM factory_categories WHERE company_id=? AND is_active=1 ORDER BY code", [$companyId]);
$ccUnits      = DB::fetchAll("SELECT id, code, name, category_id FROM plants WHERE company_id=? AND is_active=1 ORDER BY code", [$companyId]);
$ccDepts      = DB::fetchAll("SELECT id, code, name, plant_id FROM departments WHERE company_id=? AND is_active=1 ORDER BY code", [$companyId]);

// Building blocks for the WHERE clauses below — $ccWhere\$Alias\$Params
// appends "AND <alias>.category_id=? ..." only for whichever of the
// three levels is actually selected (a Department pick implies its
// Unit and Category, but each is still checked independently in case
// the org structure changes underneath an old document).
$ccFilter = static function (string $alias) use ($categoryId, $unitId, $deptId): array {
    $sql = ''; $params = [];
    if ($categoryId) { $sql .= " AND {$alias}.category_id = ?"; $params[] = $categoryId; }
    if ($unitId)     { $sql .= " AND {$alias}.unit_id = ?";     $params[] = $unitId; }
    if ($deptId)      { $sql .= " AND {$alias}.department_id = ?"; $params[] = $deptId; }
    return [$sql, $params];
};

$pageTitle   = 'MM Pending / Status Report';
$pageTcode   = 'ME2PN';
$breadcrumbs = ['MM', 'Reports', 'Pending Report'];
erp_render_page_header();

// -----------------------------------------------------------
// 1. Purchase Requisitions raised in the period
// -----------------------------------------------------------
[$ccSqlPr, $ccParamsPr] = $ccFilter('pr');
$prList = DB::fetchAll(
    "SELECT pr.id, pr.pr_number, pr.pr_date, pr.pr_type, pr.status, pr.required_by,
            d.name AS dept_name, u.full_name AS requester,
            COUNT(prl.id) AS line_count,
            COALESCE(SUM(prl.qty_requested * prl.est_price), 0) AS est_value,
            SUM(CASE WHEN prl.status = 'OPEN' THEN 1 ELSE 0 END) AS open_lines
     FROM purchase_requisitions pr
     LEFT JOIN departments d ON d.id = pr.dept_id
     LEFT JOIN users u ON u.id = pr.requested_by
     LEFT JOIN purchase_requisition_lines prl ON prl.pr_id = pr.id
     WHERE pr.company_id = ? AND pr.pr_date BETWEEN ? AND ? AND pr.status != 'CANCELLED'{$ccSqlPr}
     GROUP BY pr.id ORDER BY pr.pr_date DESC, pr.id DESC",
    array_merge([$companyId, $from, $to], $ccParamsPr)
);

// -----------------------------------------------------------
// 2. Purchase Orders raised in the period
// -----------------------------------------------------------
[$ccSqlPo, $ccParamsPo] = $ccFilter('po');
$poList = DB::fetchAll(
    "SELECT po.id, po.po_number, po.po_date, po.status, po.release_status, po.grand_total,
            po.delivery_date, s.name AS supplier_name, s.supplier_code,
            COALESCE(SUM(pol.qty_ordered), 0) AS qty_ordered,
            COALESCE(SUM(pol.qty_received), 0) AS qty_received
     FROM purchase_orders po
     JOIN suppliers s ON s.id = po.supplier_id
     LEFT JOIN purchase_order_lines pol ON pol.po_id = po.id
     WHERE po.company_id = ? AND po.po_date BETWEEN ? AND ? AND po.is_deleted = 0 AND po.status != 'CANCELLED'{$ccSqlPo}
     GROUP BY po.id ORDER BY po.po_date DESC, po.id DESC",
    array_merge([$companyId, $from, $to], $ccParamsPo)
);

// -----------------------------------------------------------
// 3. Purchase Requests NOT yet processed (still have open lines,
//    i.e. not yet pulled onto any Purchase Order)
// -----------------------------------------------------------
$prPending = DB::fetchAll(
    "SELECT pr.id, pr.pr_number, pr.pr_date, pr.pr_type, pr.status, pr.required_by,
            d.name AS dept_name, u.full_name AS requester,
            COUNT(prl.id) AS open_line_count,
            COALESCE(SUM(prl.qty_requested), 0) AS open_qty,
            COALESCE(SUM(prl.qty_requested * prl.est_price), 0) AS open_value,
            DATEDIFF(CURDATE(), pr.pr_date) AS days_pending
     FROM purchase_requisitions pr
     JOIN purchase_requisition_lines prl ON prl.pr_id = pr.id AND prl.status = 'OPEN'
     LEFT JOIN departments d ON d.id = pr.dept_id
     LEFT JOIN users u ON u.id = pr.requested_by
     WHERE pr.company_id = ? AND pr.pr_date BETWEEN ? AND ? AND pr.status NOT IN ('CANCELLED'){$ccSqlPr}
     GROUP BY pr.id ORDER BY pr.pr_date ASC, pr.id ASC",
    array_merge([$companyId, $from, $to], $ccParamsPr)
);
$prPendingValue = array_sum(array_column($prPending, 'open_value'));

// -----------------------------------------------------------
// 4. PO made but material not (fully) received
// -----------------------------------------------------------
$poPending = DB::fetchAll(
    "SELECT po.id, po.po_number, po.po_date, po.status, po.release_status, po.delivery_date,
            s.name AS supplier_name, s.supplier_code,
            SUM(pol.qty_ordered) AS qty_ordered,
            SUM(pol.qty_received) AS qty_received,
            SUM(pol.qty_ordered - pol.qty_received) AS qty_pending,
            SUM((pol.qty_ordered - pol.qty_received) * pol.unit_price) AS pending_value,
            DATEDIFF(CURDATE(), po.po_date) AS days_pending
     FROM purchase_orders po
     JOIN suppliers s ON s.id = po.supplier_id
     JOIN purchase_order_lines pol ON pol.po_id = po.id
     WHERE po.company_id = ? AND po.po_date BETWEEN ? AND ? AND po.is_deleted = 0
       AND po.release_status = 'RELEASED' AND po.status NOT IN ('CLOSED','CANCELLED','REJECTED'){$ccSqlPo}
     GROUP BY po.id HAVING SUM(pol.qty_ordered - pol.qty_received) > 0.0001
     ORDER BY po.po_date ASC, po.id ASC",
    array_merge([$companyId, $from, $to], $ccParamsPo)
);
$poPendingValue = array_sum(array_column($poPending, 'pending_value'));

// -----------------------------------------------------------
// 5. Payment pending (supplier invoices, un-paid balance)
// -----------------------------------------------------------
// supplier_invoices has never had its own cost-center fields wired in
// (MR01 doesn't render/save them), so a Category/Unit/Department pick
// here filters through the invoice's linked Purchase Order instead —
// an invoice with no po_id can't be attributed to a unit and is
// correctly excluded whenever the filter is active.
[$ccSqlSiPo, $ccParamsSiPo] = $ccFilter('po');
$paymentPending = DB::fetchAll(
    "SELECT si.id, si.invoice_number, si.invoice_date, si.due_date, si.status,
            si.total_amount, si.paid_amount, si.due_amount,
            s.name AS supplier_name, s.supplier_code,
            po.po_number,
            DATEDIFF(CURDATE(), si.due_date) AS days_overdue
     FROM supplier_invoices si
     JOIN suppliers s ON s.id = si.supplier_id
     LEFT JOIN purchase_orders po ON po.id = si.po_id
     WHERE si.company_id = ? AND si.invoice_date BETWEEN ? AND ?
       AND si.status NOT IN ('PAID','CANCELLED') AND si.due_amount > 0{$ccSqlSiPo}
     ORDER BY si.due_date ASC, si.id ASC",
    array_merge([$companyId, $from, $to], $ccParamsSiPo)
);
$paymentPendingValue = array_sum(array_column($paymentPending, 'due_amount'));

$qs = 'page=msme-erp&tcode=ME2PN&from=' . urlencode($from) . '&to=' . urlencode($to)
    . ($categoryId ? '&category_id=' . $categoryId : '')
    . ($unitId ? '&unit_id=' . $unitId : '')
    . ($deptId ? '&department_id=' . $deptId : '');
?>
<form method="GET" class="d-flex flex-wrap align-items-end gap-2 mb-3 no-print">
  <input type="hidden" name="page" value="msme-erp">
  <input type="hidden" name="tcode" value="ME2PN">
  <input type="hidden" name="view" value="<?= esc_attr($activeTab) ?>">
  <div>
    <label class="form-label small mb-0">From</label>
    <input type="date" name="from" class="form-control form-control-sm" value="<?= esc_attr($from) ?>">
  </div>
  <div>
    <label class="form-label small mb-0">To</label>
    <input type="date" name="to" class="form-control form-control-sm" value="<?= esc_attr($to) ?>">
  </div>
  <?php if ($ccCategories || $ccUnits || $ccDepts): $ccUid = 'ccf_' . substr(md5(uniqid('', true)), 0, 6); ?>
  <div>
    <label class="form-label small mb-0">Category</label>
    <select name="category_id" id="<?= esc_attr($ccUid) ?>_cat" class="form-select form-select-sm">
      <option value="">— All —</option>
      <?php foreach ($ccCategories as $c): ?>
      <option value="<?= (int)$c['id'] ?>" <?= $categoryId === (int)$c['id'] ? 'selected' : '' ?>><?= esc_html($c['code'] . ' — ' . $c['name']) ?></option>
      <?php endforeach; ?>
    </select>
  </div>
  <div>
    <label class="form-label small mb-0">Unit / Plant</label>
    <select name="unit_id" id="<?= esc_attr($ccUid) ?>_unit" class="form-select form-select-sm">
      <option value="">— All —</option>
    </select>
  </div>
  <div>
    <label class="form-label small mb-0">Department</label>
    <select name="department_id" id="<?= esc_attr($ccUid) ?>_dept" class="form-select form-select-sm">
      <option value="">— All —</option>
    </select>
  </div>
  <script>
  (function(){
    var units = <?= wp_json_encode($ccUnits) ?>;
    var depts = <?= wp_json_encode($ccDepts) ?>;
    var catSel  = document.getElementById('<?= esc_js($ccUid) ?>_cat');
    var unitSel = document.getElementById('<?= esc_js($ccUid) ?>_unit');
    var deptSel = document.getElementById('<?= esc_js($ccUid) ?>_dept');
    var initUnit = <?= (int)$unitId ?>, initDept = <?= (int)$deptId ?>;

    function fillUnits(catId, keepSelected) {
      unitSel.innerHTML = '<option value="">— All —</option>';
      units.filter(function(u){ return !catId || String(u.category_id) === String(catId); }).forEach(function(u){
        var opt = document.createElement('option');
        opt.value = u.id; opt.textContent = u.code + ' — ' + u.name;
        if (keepSelected && String(u.id) === String(initUnit)) opt.selected = true;
        unitSel.appendChild(opt);
      });
    }
    function fillDepts(unitId, keepSelected) {
      deptSel.innerHTML = '<option value="">— All —</option>';
      depts.filter(function(d){ return !unitId || String(d.plant_id) === String(unitId); }).forEach(function(d){
        var opt = document.createElement('option');
        opt.value = d.id; opt.textContent = d.code + ' — ' + d.name;
        if (keepSelected && String(d.id) === String(initDept)) opt.selected = true;
        deptSel.appendChild(opt);
      });
    }
    catSel.addEventListener('change', function(){ fillUnits(catSel.value, false); fillDepts(null, false); });
    unitSel.addEventListener('change', function(){ fillDepts(unitSel.value, false); });

    fillUnits(catSel.value, true);
    fillDepts(initUnit || unitSel.value, true);
  })();
  </script>
  <?php endif; ?>
  <button class="btn btn-primary btn-sm"><i class="bi bi-funnel me-1"></i>Apply</button>
  <button type="button" onclick="window.print()" class="btn btn-outline-secondary btn-sm ms-auto"><i class="bi bi-printer me-1"></i>Print</button>
</form>

<div class="row g-3 mb-3">
  <div class="col-6" style="flex:1 1 19%;max-width:20%;">
    <a href="?<?= $qs ?>&view=pr" class="text-decoration-none">
      <div class="card border-0 shadow-sm text-center p-3 h-100">
        <div class="fs-5 fw-bold text-primary"><?= count($prList) ?></div>
        <div class="small text-muted">Purchase Requests</div>
      </div>
    </a>
  </div>
  <div class="col-6" style="flex:1 1 19%;max-width:20%;">
    <a href="?<?= $qs ?>&view=po" class="text-decoration-none">
      <div class="card border-0 shadow-sm text-center p-3 h-100">
        <div class="fs-5 fw-bold text-primary"><?= count($poList) ?></div>
        <div class="small text-muted">Purchase Orders</div>
      </div>
    </a>
  </div>
  <div class="col-6" style="flex:1 1 19%;max-width:20%;">
    <a href="?<?= $qs ?>&view=pr_pending" class="text-decoration-none">
      <div class="card border-0 shadow-sm text-center p-3 h-100">
        <div class="fs-5 fw-bold text-warning"><?= count($prPending) ?></div>
        <div class="small text-muted">PR Not Processed</div>
        <div class="small text-muted"><?= $currency ?><?= number_format($prPendingValue, 0) ?></div>
      </div>
    </a>
  </div>
  <div class="col-6" style="flex:1 1 19%;max-width:20%;">
    <a href="?<?= $qs ?>&view=po_pending" class="text-decoration-none">
      <div class="card border-0 shadow-sm text-center p-3 h-100">
        <div class="fs-5 fw-bold text-danger"><?= count($poPending) ?></div>
        <div class="small text-muted">PO — Material Not Received</div>
        <div class="small text-muted"><?= $currency ?><?= number_format($poPendingValue, 0) ?></div>
      </div>
    </a>
  </div>
  <div class="col-6" style="flex:1 1 19%;max-width:20%;">
    <a href="?<?= $qs ?>&view=payment_pending" class="text-decoration-none">
      <div class="card border-0 shadow-sm text-center p-3 h-100">
        <div class="fs-5 fw-bold text-danger"><?= count($paymentPending) ?></div>
        <div class="small text-muted">Payment Pending</div>
        <div class="small text-muted"><?= $currency ?><?= number_format($paymentPendingValue, 0) ?></div>
      </div>
    </a>
  </div>
</div>

<ul class="nav nav-tabs mb-3 no-print" role="tablist">
  <li class="nav-item"><a class="nav-link <?= $activeTab==='pr_pending'?'active':'' ?>" href="?<?= $qs ?>&view=pr_pending">PR Not Processed</a></li>
  <li class="nav-item"><a class="nav-link <?= $activeTab==='po_pending'?'active':'' ?>" href="?<?= $qs ?>&view=po_pending">PO — Material Not Received</a></li>
  <li class="nav-item"><a class="nav-link <?= $activeTab==='payment_pending'?'active':'' ?>" href="?<?= $qs ?>&view=payment_pending">Payment Pending</a></li>
  <li class="nav-item"><a class="nav-link <?= $activeTab==='pr'?'active':'' ?>" href="?<?= $qs ?>&view=pr">All Purchase Requests</a></li>
  <li class="nav-item"><a class="nav-link <?= $activeTab==='po'?'active':'' ?>" href="?<?= $qs ?>&view=po">All Purchase Orders</a></li>
</ul>

<div class="card border-0 shadow-sm">
  <div class="card-header bg-white py-2">
    <div class="small text-muted">
      Period: <?= esc_html($from) ?> to <?= esc_html($to) ?>
      <?php if ($categoryId || $unitId || $deptId):
        $labelOf = static function (array $rows, int $id): string {
            foreach ($rows as $r) if ((int)$r['id'] === $id) return $r['code'] . ' — ' . $r['name'];
            return '';
        };
        $ccBits = array_filter([
            $categoryId ? $labelOf($ccCategories, $categoryId) : '',
            $unitId ? $labelOf($ccUnits, $unitId) : '',
            $deptId ? $labelOf($ccDepts, $deptId) : '',
        ]);
      ?>
      &nbsp;|&nbsp;<i class="bi bi-diagram-3 me-1"></i><?= esc_html(implode(' / ', $ccBits)) ?>
      <?php endif; ?>
    </div>
  </div>
  <div class="card-body p-0">

<?php if ($activeTab === 'pr_pending'): ?>
    <table class="table table-sm table-hover mb-0 erp-table">
      <thead><tr><th>PR#</th><th>Date</th><th>Type</th><th>Dept</th><th>Requester</th><th>Status</th><th>Open Lines</th><th>Open Qty</th><th class="text-end">Est. Value</th><th>Age</th></tr></thead>
      <tbody>
        <?php foreach ($prPending as $r): ?>
        <tr>
          <td><a href="?page=msme-erp&tcode=ME53N&id=<?= (int)$r['id'] ?>"><?= esc_html($r['pr_number']) ?></a></td>
          <td class="small"><?= esc_html($r['pr_date']) ?></td>
          <td class="small"><?= esc_html($r['pr_type']) ?></td>
          <td class="small"><?= esc_html($r['dept_name'] ?? '—') ?></td>
          <td class="small"><?= esc_html($r['requester'] ?? '—') ?></td>
          <td><span class="badge bg-secondary"><?= esc_html($r['status']) ?></span></td>
          <td class="small"><?= (int)$r['open_line_count'] ?></td>
          <td class="small"><?= number_format((float)$r['open_qty'], 2) ?></td>
          <td class="text-end small"><?= $currency ?><?= number_format((float)$r['open_value'], 2) ?></td>
          <td class="small"><?php $d=(int)$r['days_pending']; ?><span class="badge <?= $d>15?'bg-danger':($d>7?'bg-warning text-dark':'bg-light text-dark') ?>"><?= $d ?>d</span></td>
        </tr>
        <?php endforeach; if (!$prPending): ?>
        <tr><td colspan="10" class="text-center text-muted py-4"><i class="bi bi-check-circle-fill text-success"></i> No pending PRs in this period — all converted to PO.</td></tr>
        <?php endif; ?>
      </tbody>
      <?php if ($prPending): ?>
      <tfoot class="table-light fw-bold"><tr><td colspan="8" class="text-end small">Total Open Value:</td><td class="text-end small"><?= $currency ?><?= number_format($prPendingValue, 2) ?></td><td></td></tr></tfoot>
      <?php endif; ?>
    </table>

<?php elseif ($activeTab === 'po_pending'): ?>
    <table class="table table-sm table-hover mb-0 erp-table">
      <thead><tr><th>PO#</th><th>Date</th><th>Supplier</th><th>Status</th><th>Qty Ordered</th><th>Qty Received</th><th>Qty Pending</th><th class="text-end">Pending Value</th><th>Delivery Due</th><th>Age</th></tr></thead>
      <tbody>
        <?php foreach ($poPending as $r): ?>
        <tr>
          <td><a href="?page=msme-erp&tcode=ME23N&id=<?= (int)$r['id'] ?>"><?= esc_html($r['po_number']) ?></a></td>
          <td class="small"><?= esc_html($r['po_date']) ?></td>
          <td class="small"><?= esc_html($r['supplier_code'].' — '.$r['supplier_name']) ?></td>
          <td><span class="badge bg-secondary"><?= esc_html($r['status']) ?></span></td>
          <td class="small"><?= number_format((float)$r['qty_ordered'], 2) ?></td>
          <td class="small"><?= number_format((float)$r['qty_received'], 2) ?></td>
          <td class="small fw-bold text-danger"><?= number_format((float)$r['qty_pending'], 2) ?></td>
          <td class="text-end small"><?= $currency ?><?= number_format((float)$r['pending_value'], 2) ?></td>
          <td class="small <?= (!empty($r['delivery_date']) && $r['delivery_date'] < date('Y-m-d')) ? 'fw-bold text-danger' : '' ?>"><?= esc_html($r['delivery_date'] ?: '—') ?></td>
          <td class="small"><?php $d=(int)$r['days_pending']; ?><span class="badge <?= $d>15?'bg-danger':($d>7?'bg-warning text-dark':'bg-light text-dark') ?>"><?= $d ?>d</span></td>
        </tr>
        <?php endforeach; if (!$poPending): ?>
        <tr><td colspan="10" class="text-center text-muted py-4"><i class="bi bi-check-circle-fill text-success"></i> No pending receipts in this period — all material received.</td></tr>
        <?php endif; ?>
      </tbody>
      <?php if ($poPending): ?>
      <tfoot class="table-light fw-bold"><tr><td colspan="7" class="text-end small">Total Pending Value:</td><td class="text-end small"><?= $currency ?><?= number_format($poPendingValue, 2) ?></td><td colspan="2"></td></tr></tfoot>
      <?php endif; ?>
    </table>

<?php elseif ($activeTab === 'payment_pending'): ?>
    <table class="table table-sm table-hover mb-0 erp-table">
      <thead><tr><th>Invoice#</th><th>Date</th><th>PO#</th><th>Supplier</th><th>Due Date</th><th class="text-end">Invoice Amt</th><th class="text-end">Paid</th><th class="text-end">Due</th><th>Aging</th></tr></thead>
      <tbody>
        <?php foreach ($paymentPending as $r):
          $isOver = !empty($r['due_date']) && $r['due_date'] < date('Y-m-d');
        ?>
        <tr class="<?= $isOver?'table-danger':'' ?>">
          <td class="fw-bold small"><?= esc_html($r['invoice_number']) ?></td>
          <td class="small"><?= esc_html($r['invoice_date']) ?></td>
          <td class="small"><?= $r['po_number'] ? esc_html($r['po_number']) : '—' ?></td>
          <td class="small"><?= esc_html($r['supplier_code'].' — '.$r['supplier_name']) ?></td>
          <td class="small <?= $isOver?'fw-bold text-danger':'' ?>"><?= esc_html($r['due_date'] ?: '—') ?></td>
          <td class="text-end small"><?= $currency ?><?= number_format((float)$r['total_amount'], 2) ?></td>
          <td class="text-end small text-success"><?= $currency ?><?= number_format((float)$r['paid_amount'], 2) ?></td>
          <td class="text-end small fw-bold text-danger"><?= $currency ?><?= number_format((float)$r['due_amount'], 2) ?></td>
          <td class="small">
            <?php if ($isOver): ?>
            <span class="badge bg-danger"><?= (int)$r['days_overdue'] ?>d overdue</span>
            <?php else: ?>
            <span class="badge bg-success">Not yet due</span>
            <?php endif; ?>
          </td>
        </tr>
        <?php endforeach; if (!$paymentPending): ?>
        <tr><td colspan="9" class="text-center text-muted py-4"><i class="bi bi-check-circle-fill text-success"></i> No pending payments in this period.</td></tr>
        <?php endif; ?>
      </tbody>
      <?php if ($paymentPending): ?>
      <tfoot class="table-light fw-bold"><tr><td colspan="7" class="text-end small">Total Due:</td><td class="text-end small"><?= $currency ?><?= number_format($paymentPendingValue, 2) ?></td><td></td></tr></tfoot>
      <?php endif; ?>
    </table>

<?php elseif ($activeTab === 'pr'): ?>
    <table class="table table-sm table-hover mb-0 erp-table">
      <thead><tr><th>PR#</th><th>Date</th><th>Type</th><th>Dept</th><th>Requester</th><th>Status</th><th>Lines</th><th>Open Lines</th><th class="text-end">Est. Value</th></tr></thead>
      <tbody>
        <?php foreach ($prList as $r): ?>
        <tr>
          <td><a href="?page=msme-erp&tcode=ME53N&id=<?= (int)$r['id'] ?>"><?= esc_html($r['pr_number']) ?></a></td>
          <td class="small"><?= esc_html($r['pr_date']) ?></td>
          <td class="small"><?= esc_html($r['pr_type']) ?></td>
          <td class="small"><?= esc_html($r['dept_name'] ?? '—') ?></td>
          <td class="small"><?= esc_html($r['requester'] ?? '—') ?></td>
          <td><span class="badge <?= $r['status']==='PO_CREATED'?'bg-success':'bg-secondary' ?>"><?= esc_html($r['status']) ?></span></td>
          <td class="small"><?= (int)$r['line_count'] ?></td>
          <td class="small"><?= (int)$r['open_lines'] ?></td>
          <td class="text-end small"><?= $currency ?><?= number_format((float)$r['est_value'], 2) ?></td>
        </tr>
        <?php endforeach; if (!$prList): ?>
        <tr><td colspan="9" class="text-center text-muted py-4">No purchase requisitions in this period.</td></tr>
        <?php endif; ?>
      </tbody>
    </table>

<?php elseif ($activeTab === 'po'): ?>
    <table class="table table-sm table-hover mb-0 erp-table">
      <thead><tr><th>PO#</th><th>Date</th><th>Supplier</th><th>Status</th><th>Release</th><th>Qty Ordered</th><th>Qty Received</th><th class="text-end">Grand Total</th></tr></thead>
      <tbody>
        <?php foreach ($poList as $r): ?>
        <tr>
          <td><a href="?page=msme-erp&tcode=ME23N&id=<?= (int)$r['id'] ?>"><?= esc_html($r['po_number']) ?></a></td>
          <td class="small"><?= esc_html($r['po_date']) ?></td>
          <td class="small"><?= esc_html($r['supplier_code'].' — '.$r['supplier_name']) ?></td>
          <td><span class="badge <?= in_array($r['status'],['CLOSED'])?'bg-success':'bg-secondary' ?>"><?= esc_html($r['status']) ?></span></td>
          <td class="small"><?= esc_html($r['release_status']) ?></td>
          <td class="small"><?= number_format((float)$r['qty_ordered'], 2) ?></td>
          <td class="small"><?= number_format((float)$r['qty_received'], 2) ?></td>
          <td class="text-end small"><?= $currency ?><?= number_format((float)$r['grand_total'], 2) ?></td>
        </tr>
        <?php endforeach; if (!$poList): ?>
        <tr><td colspan="8" class="text-center text-muted py-4">No purchase orders in this period.</td></tr>
        <?php endif; ?>
      </tbody>
    </table>
<?php endif; ?>

  </div>
</div>
