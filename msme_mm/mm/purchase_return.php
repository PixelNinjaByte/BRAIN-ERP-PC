<?php
/**
 * MSME ERP - Purchase Return (goods back to the supplier)
 * T-Code: PRTN   (v1.18.0)
 * Raised against a posted GRN. Posts the stock-out (movement 122), gives the quantity back to the PO and,
 * when a supplier invoice is booked, raises the supplier debit note with the GST reversed.
 */
Auth::requireTcode('PRTN');

$companyId    = Auth::companyId();
$currentTcode = 'PRTN';
$pageTitle    = 'Purchase Return';
$pageTcode    = 'PRTN';
$breadcrumbs  = ['Materials', 'Returns', 'Purchase Return'];
$retId        = (int)($_GET['id'] ?? 0);
$grnId        = (int)($_GET['grn_id'] ?? $_POST['grn_id'] ?? 0);
$view         = $retId ? 'view' : (isset($_GET['new']) || $grnId ? 'new' : 'list');
$error = '';

// ---------------------------------------------------------------- save
if ($_SERVER['REQUEST_METHOD'] === 'POST' && ($_POST['do'] ?? '') === 'save_return') {
    if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) wp_die('Security check failed - please reload the page and try again.');
    try {
        $newId = msme_erp_purchase_return_create($_POST);
        $num   = DB::fetchValue("SELECT return_number FROM purchase_returns WHERE id=?", [$newId]);
        msme_erp_set_flash("Purchase return {$num} posted - stock reduced and the PO quantity re-opened.", 'success');
        wp_redirect(admin_url('admin.php?page=msme-erp&tcode=PRTN&id=' . $newId)); exit;
    } catch (Throwable $e) { $error = $e->getMessage(); $view = 'new'; }
}

erp_render_page_header();
if ($error): ?><div class="alert alert-danger"><i class="bi bi-exclamation-triangle me-1"></i><?= esc_html($error) ?></div><?php endif;
$base = admin_url('admin.php?page=msme-erp&tcode=PRTN');

// ================================================================= VIEW
if ($view === 'view') {
    $r = DB::fetchOne("SELECT pr.*, s.name AS supplier, s.supplier_code, g.grn_number, w.name AS wh, po.po_number, gs.grn_number AS stock_doc,
                              n.note_number, n.total_amount AS note_total, n.ref_inv_number
                       FROM purchase_returns pr
                       JOIN suppliers s ON s.id=pr.supplier_id JOIN goods_receipts g ON g.id=pr.grn_id JOIN warehouses w ON w.id=pr.warehouse_id
                       LEFT JOIN purchase_orders po ON po.id=pr.po_id LEFT JOIN goods_receipts gs ON gs.id=pr.stock_doc_id LEFT JOIN fin_notes n ON n.id=pr.debit_note_id
                       WHERE pr.id=? AND pr.company_id=?", [$retId, $companyId]);
    if (!$r) { echo '<div class="alert alert-warning">Return not found.</div>'; return; }
    $lines = DB::fetchAll("SELECT l.*, i.item_code, i.item_name, u.code AS uom FROM purchase_return_lines l JOIN items i ON i.id=l.item_id LEFT JOIN units_of_measure u ON u.id=l.uom_id WHERE l.return_id=?", [$retId]);
    ?>
    <div class="d-flex gap-2 mb-3 no-print">
      <button onclick="window.print()" class="btn btn-sm btn-outline-secondary"><i class="bi bi-printer"></i> Print</button>
      <a href="<?= esc_url($base) ?>" class="btn btn-sm btn-outline-secondary"><i class="bi bi-arrow-left"></i> All returns</a>
      <a href="<?= esc_url($base . '&new=1') ?>" class="btn btn-sm btn-primary"><i class="bi bi-plus-lg"></i> New return</a>
    </div>
    <div class="card border-0 shadow-sm"><div class="card-body p-4">
      <div class="row mb-3">
        <div class="col-8"><h5 class="fw-bold mb-1">PURCHASE RETURN (DEBIT ADVICE)</h5><div class="fw-bold"><?= esc_html($r['return_number']) ?></div>
          <span class="badge bg-success"><?= esc_html($r['status']) ?></span></div>
        <div class="col-4 text-end small">Date: <?= esc_html($r['return_date']) ?><br>GRN: <strong><?= esc_html($r['grn_number']) ?></strong><br>PO: <?= esc_html($r['po_number'] ?? '—') ?></div>
      </div>
      <div class="row mb-3 small">
        <div class="col-6"><strong>Supplier:</strong> <?= esc_html($r['supplier']) ?> (<?= esc_html($r['supplier_code']) ?>)</div>
        <div class="col-6"><strong>Returned from warehouse:</strong> <?= esc_html($r['wh']) ?><br><strong>Stock document:</strong> <?= esc_html($r['stock_doc'] ?? '—') ?></div>
      </div>
      <div class="small mb-2"><strong>Reason:</strong> <?= esc_html(MSME_ERP_RETURN_REASONS[$r['reason_code']] ?? $r['reason_code']) ?> - <?= esc_html($r['reason']) ?></div>
      <table class="table table-bordered table-sm" style="font-size:.85rem">
        <thead class="table-dark"><tr><th>#</th><th>Item</th><th class="text-end">Qty returned</th><th>UOM</th><th class="text-end">Rate</th><th class="text-end">GST %</th><th class="text-end">Value</th></tr></thead>
        <tbody><?php foreach ($lines as $i => $l): ?>
          <tr><td><?= $i + 1 ?></td><td><?= esc_html($l['item_code'] . ' - ' . $l['item_name']) ?></td><td class="text-end"><?= esc_html(rtrim(rtrim(number_format((float)$l['qty'], 3, '.', ''), '0'), '.')) ?></td>
              <td><?= esc_html($l['uom']) ?></td><td class="text-end"><?= number_format((float)$l['unit_price'], 2) ?></td><td class="text-end"><?= (float)$l['gst_rate'] ?></td><td class="text-end"><?= number_format((float)$l['line_value'], 2) ?></td></tr>
        <?php endforeach; ?></tbody>
        <tfoot><tr><td colspan="6" class="text-end">Goods value</td><td class="text-end"><?= number_format((float)$r['base_amount'], 2) ?></td></tr>
               <tr><td colspan="6" class="text-end">GST</td><td class="text-end"><?= number_format((float)$r['tax_amount'], 2) ?></td></tr>
               <tr class="fw-bold"><td colspan="6" class="text-end">Total</td><td class="text-end"><?= number_format((float)$r['total_amount'], 2) ?></td></tr></tfoot>
      </table>
      <div class="small"><?php if ($r['note_number']): ?>
        <i class="bi bi-receipt me-1"></i>Supplier debit note <strong><?= esc_html($r['note_number']) ?></strong> for <?= number_format((float)$r['note_total'], 2) ?>
        <?= $r['ref_inv_number'] ? ' against invoice <strong>' . esc_html($r['ref_inv_number']) . '</strong>' : ' (open note - apply it on FBRA)' ?>.
      <?php else: ?><span class="text-muted">No debit note raised - the return reduces what is still to be invoiced (GR/IR cleared).</span><?php endif; ?></div>
    </div></div>
    <?php
    return;
}

// ================================================================= NEW
if ($view === 'new') {
    $grns = DB::fetchAll("SELECT g.id, g.grn_number, g.grn_date, s.name AS supplier, po.po_number
                          FROM goods_receipts g JOIN suppliers s ON s.id=g.supplier_id LEFT JOIN purchase_orders po ON po.id=g.po_id
                          WHERE g.company_id=? AND g.status='POSTED' AND COALESCE(g.movement_type,'101')<>'122'
                          ORDER BY g.id DESC LIMIT 400", [$companyId]);
    $grn = null; $lines = []; $invoices = [];
    if ($grnId) {
        $grn = DB::fetchOne("SELECT g.*, s.name AS supplier, w.name AS wh, po.po_number FROM goods_receipts g JOIN suppliers s ON s.id=g.supplier_id JOIN warehouses w ON w.id=g.warehouse_id LEFT JOIN purchase_orders po ON po.id=g.po_id WHERE g.id=? AND g.company_id=?", [$grnId, $companyId]);
        if ($grn) {
            $lines = DB::fetchAll("SELECT gl.*, i.item_code, i.item_name, u.code AS uom,
                       COALESCE((SELECT SUM(prl.qty) FROM purchase_return_lines prl JOIN purchase_returns pr ON pr.id=prl.return_id WHERE prl.grn_line_id=gl.id AND pr.status='POSTED'),0) AS returned,
                       COALESCE((SELECT qty_on_hand FROM stock_balances sb WHERE sb.item_id=gl.item_id AND sb.warehouse_id=?),0) AS on_hand
                       FROM goods_receipt_lines gl JOIN items i ON i.id=gl.item_id LEFT JOIN units_of_measure u ON u.id=gl.uom_id WHERE gl.grn_id=? AND gl.qty_accepted>0", [(int)$grn['warehouse_id'], $grnId]);
            $invoices = DB::fetchAll("SELECT id, invoice_number, due_amount, status, invoice_date FROM supplier_invoices WHERE company_id=? AND supplier_id=? AND status<>'CANCELLED'" . ($grn['po_id'] ? " AND po_id=" . (int)$grn['po_id'] : '') . " ORDER BY id DESC LIMIT 30", [$companyId, (int)$grn['supplier_id']]);
        }
    }
    ?>
    <div class="card border-0 shadow-sm mb-3"><div class="card-body">
      <form method="GET" class="row g-2 align-items-end">
        <input type="hidden" name="page" value="msme-erp"><input type="hidden" name="tcode" value="PRTN">
        <div class="col-md-8"><label class="form-label fw-semibold small">Goods receipt (GRN) to return against</label>
          <select name="grn_id" class="form-select form-select-sm select2" onchange="this.form.submit()">
            <option value="">- select a posted GRN -</option>
            <?php foreach ($grns as $g): ?><option value="<?= (int)$g['id'] ?>" <?= $grnId === (int)$g['id'] ? 'selected' : '' ?>><?= esc_html($g['grn_number'] . '  |  ' . $g['grn_date'] . '  |  ' . $g['supplier'] . ($g['po_number'] ? '  |  PO ' . $g['po_number'] : '')) ?></option><?php endforeach; ?>
          </select></div>
        <div class="col-md-2"><button class="btn btn-sm btn-primary">Load</button> <a href="<?= esc_url($base) ?>" class="btn btn-sm btn-outline-secondary">Cancel</a></div>
      </form>
    </div></div>
    <?php if ($grnId && !$grn): ?><div class="alert alert-warning">That goods receipt was not found.</div><?php endif;
    if ($grn):
        $noLines = true; foreach ($lines as $l) if ((float)$l['qty_accepted'] - (float)$l['returned'] > 0.0005) $noLines = false; ?>
    <form method="POST" id="prtnForm"><input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>"><input type="hidden" name="do" value="save_return"><input type="hidden" name="grn_id" value="<?= (int)$grn['id'] ?>">
      <div class="card border-0 shadow-sm mb-3"><div class="card-body">
        <div class="row g-3 small mb-2">
          <div class="col-md-4"><strong>Supplier:</strong> <?= esc_html($grn['supplier']) ?></div>
          <div class="col-md-4"><strong>GRN:</strong> <?= esc_html($grn['grn_number']) ?> (<?= esc_html($grn['grn_date']) ?>)</div>
          <div class="col-md-4"><strong>PO:</strong> <?= esc_html($grn['po_number'] ?? '—') ?> &nbsp; <strong>Warehouse:</strong> <?= esc_html($grn['wh']) ?></div>
        </div>
        <?php if ($noLines): ?><div class="alert alert-info mb-0">Everything accepted on this GRN has already been returned.</div><?php else: ?>
        <table class="table table-sm table-bordered align-middle" style="font-size:.85rem">
          <thead class="table-light"><tr><th>Item</th><th class="text-end">Accepted</th><th class="text-end">Already returned</th><th class="text-end">In stock now</th><th class="text-end">Rate</th><th style="width:140px">Return qty</th></tr></thead>
          <tbody><?php foreach ($lines as $l): $max = round((float)$l['qty_accepted'] - (float)$l['returned'], 3); ?>
            <tr><td><?= esc_html($l['item_code'] . ' - ' . $l['item_name']) ?></td>
              <td class="text-end"><?= esc_html(rtrim(rtrim(number_format((float)$l['qty_accepted'], 3, '.', ''), '0'), '.')) ?> <?= esc_html($l['uom']) ?></td>
              <td class="text-end"><?= esc_html(rtrim(rtrim(number_format((float)$l['returned'], 3, '.', ''), '0'), '.')) ?></td>
              <td class="text-end <?= (float)$l['on_hand'] < $max ? 'text-danger' : '' ?>"><?= esc_html(rtrim(rtrim(number_format((float)$l['on_hand'], 3, '.', ''), '0'), '.')) ?></td>
              <td class="text-end"><?= number_format((float)$l['unit_price'], 2) ?></td>
              <td><?php if ($max > 0.0005): ?><input type="number" name="qty[<?= (int)$l['id'] ?>]" class="form-control form-control-sm" min="0" max="<?= $max ?>" step="0.001" value="0"><?php else: ?><span class="text-muted small">fully returned</span><?php endif; ?></td></tr>
          <?php endforeach; ?></tbody>
        </table>
        <div class="row g-3">
          <div class="col-md-2"><label class="form-label small fw-semibold">Return date</label><input type="date" name="return_date" class="form-control form-control-sm" value="<?= esc_attr(current_time('Y-m-d')) ?>" required></div>
          <div class="col-md-3"><label class="form-label small fw-semibold">Reason</label><select name="reason_code" class="form-select form-select-sm"><?php foreach (MSME_ERP_RETURN_REASONS as $k => $v): ?><option value="<?= esc_attr($k) ?>"><?= esc_html($v) ?></option><?php endforeach; ?></select></div>
          <div class="col-md-7"><label class="form-label small fw-semibold">Remarks *</label><input type="text" name="reason" class="form-control form-control-sm" maxlength="255" required placeholder="e.g. 20 bags damaged in transit, supplier agreed to take back"></div>
          <div class="col-md-12"><label class="form-label small fw-semibold">Supplier debit note</label>
            <select name="debit_note" class="form-select form-select-sm">
              <option value="0">No debit note (invoice not booked yet - the return just reduces what is still to be invoiced)</option>
              <?php foreach ($invoices as $iv): ?><option value="<?= (int)$iv['id'] ?>" <?= $iv === reset($invoices) ? 'selected' : '' ?>>Raise debit note against invoice <?= esc_html($iv['invoice_number']) ?> (due <?= number_format((float)$iv['due_amount'], 2) ?>, <?= esc_html($iv['status']) ?>)</option><?php endforeach; ?>
              <option value="-1">Raise an open debit note (no invoice)</option>
            </select>
            <div class="form-text">Book the debit note once the supplier's invoice is in the system, so Accounts Payable falls by the returned goods and the GST is reversed. A note larger than the invoice's due amount becomes an open note.</div></div>
        </div>
        <?php endif; ?>
      </div></div>
      <?php if (!$noLines): ?><button class="btn btn-warning fw-semibold" onclick="return confirm('Post this purchase return? Stock is reduced immediately.');"><i class="bi bi-arrow-return-left me-1"></i>Post Purchase Return</button><?php endif; ?>
    </form>
    <?php endif;
    return;
}

// ================================================================= LIST
$rows = DB::fetchAll("SELECT pr.id, pr.return_number, pr.return_date, pr.total_amount, pr.status, pr.reason_code, s.name AS supplier, g.grn_number, n.note_number
                      FROM purchase_returns pr JOIN suppliers s ON s.id=pr.supplier_id JOIN goods_receipts g ON g.id=pr.grn_id LEFT JOIN fin_notes n ON n.id=pr.debit_note_id
                      WHERE pr.company_id=? ORDER BY pr.id DESC LIMIT 500", [$companyId]);
?>
<div class="d-flex justify-content-between align-items-center mb-3">
  <div class="small text-muted">Goods sent back to a supplier against a posted GRN. Stock, PO quantity and (optionally) the supplier account are updated together.</div>
  <a href="<?= esc_url($base . '&new=1') ?>" class="btn btn-primary btn-sm"><i class="bi bi-plus-lg me-1"></i>New Purchase Return</a>
</div>
<div class="card border-0 shadow-sm"><div class="card-body p-0">
<table class="table table-sm table-hover mb-0 align-middle erp-datatable" data-empty="No purchase returns yet.">
  <thead class="table-light"><tr><th>Return #</th><th>Date</th><th>Supplier</th><th>GRN</th><th>Reason</th><th class="text-end">Value (incl. GST)</th><th>Debit note</th><th>Status</th></tr></thead>
  <tbody><?php foreach ($rows as $r): ?>
    <tr><td><a href="<?= esc_url($base . '&id=' . (int)$r['id']) ?>"><?= esc_html($r['return_number']) ?></a></td><td><?= esc_html($r['return_date']) ?></td><td><?= esc_html($r['supplier']) ?></td><td><?= esc_html($r['grn_number']) ?></td>
        <td><?= esc_html(MSME_ERP_RETURN_REASONS[$r['reason_code']] ?? $r['reason_code']) ?></td><td class="text-end"><?= number_format((float)$r['total_amount'], 2) ?></td><td><?= esc_html($r['note_number'] ?? '—') ?></td><td><span class="badge bg-success"><?= esc_html($r['status']) ?></span></td></tr>
  <?php endforeach; ?></tbody>
</table></div></div>
