<?php
/**
 * MSME ERP — Reorder Report (ZMRP)
 * v1.17.1. Materials whose TOTAL stock (all warehouses, less reserved) is at or below the reorder point,
 * with open-PO cover and a suggested order quantity. One click turns the selection into a purchase requisition.
 * Developed by: Prigag Online Pvt. Ltd.
 */
defined('ABSPATH') || exit;
Auth::requireTcode('ZMRP');

$companyId = Auth::companyId();
$error = '';
$url = admin_url('admin.php?page=msme-erp&tcode=ZMRP');

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) wp_die('CSRF');
    $pick = [];
    foreach ((array)($_POST['sel'] ?? []) as $id => $on) { $q = (float)($_POST['qty'][$id] ?? 0); if ($q > 0) $pick[(int)$id] = $q; }
    $res = msme_erp_mat_create_pr($companyId, $pick);
    if ($res['ok']) { msme_erp_set_flash($res['message']); wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME53N&id=' . (int)$res['pr_id'])); exit; }
    $error = $res['message'];
}

$items = DB::fetchAll("SELECT i.id,i.item_code,i.item_name,i.min_stock,i.max_stock,i.reorder_point,i.lead_time_days,i.lot_size,u.code AS uom
        FROM items i LEFT JOIN units_of_measure u ON u.id=i.base_uom_id
       WHERE i.company_id=? AND i.is_deleted=0 AND i.is_active=1 AND i.item_type<>'SERVICE' AND i.reorder_point>0" . msme_erp_item_block_sql('PURCHASE') . " ORDER BY i.item_code", [$companyId]);
$map = $items ? msme_erp_item_stock_map($companyId, array_column($items, 'id')) : [];

$rows = [];
foreach ($items as $it) {
    $s = $map[(int)$it['id']] ?? ['on_hand' => 0, 'reserved' => 0, 'available' => 0, 'on_order' => 0];
    $avail = (float)$s['available']; $onOrder = (float)$s['on_order']; $rp = (float)$it['reorder_point'];
    if ($avail > $rp) continue;                                            // stock is healthy
    $position = $avail + $onOrder;
    $target   = (float)$it['max_stock'] > 0 ? (float)$it['max_stock'] : $rp * 2;
    $suggest  = 0.0; $covered = $position > $rp;                           // open POs already bring it back above the reorder point
    if (!$covered) {
        $suggest = max(0.0, $target - $position);
        $lot = (float)$it['lot_size'];
        if ($lot > 0) $suggest = ceil($suggest / $lot - 1e-9) * $lot;      // whole lots
    }
    $rows[] = $it + ['on_hand' => (float)$s['on_hand'], 'available' => $avail, 'on_order' => $onOrder, 'suggest' => $suggest, 'covered' => $covered, 'critical' => $avail < (float)$it['min_stock']];
}
usort($rows, static fn($a, $b) => [$b['critical'], $a['covered']] <=> [$a['critical'], $b['covered']]);

$pageTitle   = 'Reorder Report — ZMRP';
$pageTcode   = 'ZMRP';
$breadcrumbs = ['MM', 'Inventory', 'Reorder Report'];
$pageActions = '<a href="' . esc_url(admin_url('admin.php?page=msme-erp&tcode=MM03&st=low')) . '" class="btn btn-sm btn-outline-secondary"><i class="bi bi-box-seam me-1"></i>Materials</a>';
erp_render_page_header();
if ($error) erp_alert($error, 'danger');
$fmt = static fn($n) => rtrim(rtrim(number_format((float)$n, 3, '.', ''), '0'), '.') ?: '0';
?>
<div class="card border-0 shadow-sm">
  <div class="card-header bg-white py-2 d-flex justify-content-between"><span class="fw-bold small"><i class="bi bi-exclamation-diamond me-2 text-danger"></i>Below reorder point <span class="badge bg-danger"><?= count($rows) ?></span></span>
    <span class="small text-muted">Available = on hand − reserved, all warehouses. Suggestion tops up to Max stock (or 2 × reorder point), counting open POs, in whole lots.</span></div>
  <form method="POST">
    <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
    <div class="table-responsive"><table class="table table-hover table-sm mb-0" style="font-size:.82rem"><thead class="table-light"><tr>
      <th class="ps-3" style="width:34px"><input type="checkbox" id="all" onclick="document.querySelectorAll('.sel').forEach(function(c){if(!c.disabled)c.checked=this.checked}.bind(this))"></th>
      <th>Code</th><th>Material</th><th>UOM</th><th class="text-end">Available</th><th class="text-end">Reorder pt</th><th class="text-end">Min</th><th class="text-end">On order</th><th class="text-end">Lead (d)</th><th class="text-end" style="width:130px">Order qty</th><th></th></tr></thead><tbody>
    <?php if (!$rows): ?><tr><td colspan="11" class="text-center text-muted py-5"><i class="bi bi-check-circle display-6 d-block mb-2 text-success opacity-50"></i>Nothing is below its reorder point. (Materials need a reorder point &gt; 0 on MM02 to appear here.)</td></tr><?php endif; ?>
    <?php foreach ($rows as $r): $id = (int)$r['id']; ?>
      <tr class="<?= $r['critical'] ? 'table-danger' : '' ?>">
        <td class="ps-3"><input type="checkbox" class="sel" name="sel[<?= $id ?>]" value="1" <?= $r['covered'] ? 'disabled' : 'checked' ?>></td>
        <td class="fw-bold"><a href="<?= esc_url(admin_url('admin.php?page=msme-erp&tcode=MM03&id=' . $id)) ?>"><?= esc_html($r['item_code']) ?></a></td>
        <td><?= esc_html(mb_substr($r['item_name'], 0, 40)) ?></td><td><?= esc_html($r['uom']) ?></td>
        <td class="text-end fw-bold text-danger"><?= $fmt($r['available']) ?></td><td class="text-end"><?= $fmt($r['reorder_point']) ?></td><td class="text-end"><?= $fmt($r['min_stock']) ?></td>
        <td class="text-end"><?= $fmt($r['on_order']) ?></td><td class="text-end"><?= (int)$r['lead_time_days'] ?></td>
        <td><input type="number" name="qty[<?= $id ?>]" class="form-control form-control-sm text-end" min="0" step="0.001" value="<?= esc_attr($r['suggest']) ?>" <?= $r['covered'] ? 'disabled' : '' ?>></td>
        <td><?= $r['covered'] ? '<span class="badge bg-info text-dark">Covered by open PO</span>' : ($r['critical'] ? '<span class="badge bg-danger">Below min</span>' : '') ?></td></tr>
    <?php endforeach; ?></tbody></table></div>
    <?php if ($rows): ?><div class="card-footer bg-white d-flex gap-2 align-items-center"><button class="btn btn-primary btn-sm"><i class="bi bi-cart-plus me-1"></i>Create purchase requisition from selected</button><span class="small text-muted">The PR goes to the normal release workflow (ME53N).</span></div><?php endif; ?>
  </form>
</div>
