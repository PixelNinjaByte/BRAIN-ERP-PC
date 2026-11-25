<?php
/**
 * MSME ERP — Material Master: MM01 (create) / MM02 (change) / MM03 (display)
 * v1.17.1 — rebuilt. Moved out of supplier_create.php (suppliers and customers stay there).
 * Developed by: Prigag Online Pvt. Ltd. | www.prigag.com
 *
 *  MM01  create (blank code = automatic MAT number, "copy from" any material)
 *  MM02  change (code and base UOM locked once used), block status, alternate UOMs, delete-if-unused
 *  MM03  list (search / filters / sort / export) + detail with Stock, Purchases, Where-used, Alt UOM, History tabs
 * All validation lives in msme_erp_mat_clean() (core/material.php) and is shared with the CSV import.
 */
defined('ABSPATH') || exit;

$tcode = strtoupper($_GET['tcode'] ?? 'MM03');
if (!in_array($tcode, ['MM01', 'MM02', 'MM03'], true)) $tcode = 'MM03';
Auth::requireTcode($tcode);

$companyId = Auth::companyId();
$cur       = msme_erp_cur();
$error     = '';
$needDup   = [];

if (!function_exists('msme_erp_mat_url')) {
    function msme_erp_mat_url(string $tc, array $q = []): string { return admin_url('admin.php?' . http_build_query(['page' => 'msme-erp', 'tcode' => $tc] + $q)); }
    function msme_erp_mat_h($v): string { return esc_html((string)$v); }
    function msme_erp_mat_fmt($n, int $dp = 3): string { return rtrim(rtrim(number_format((float)$n, $dp, '.', ''), '0'), '.') ?: '0'; }
}

// legacy edit link (MM01&id=) and MM02 without a material
if ($tcode === 'MM01' && !empty($_GET['id'])) { wp_redirect(msme_erp_mat_url('MM02', ['id' => (int)$_GET['id']])); exit; }
if ($tcode === 'MM02' && empty($_GET['id']))  { msme_erp_set_flash('Select the material you want to change.', 'info'); wp_redirect(msme_erp_mat_url('MM03', ['pick' => 1])); exit; }

$uoms = DB::fetchAll("SELECT id,code,name,uom_type FROM units_of_measure WHERE COALESCE(is_active,1)=1 ORDER BY code");
if (!$uoms) { // empty UOM master (seeder never ran) — heal on the spot
    if (!function_exists('msme_erp_ensure_default_uoms')) require_once MSME_ERP_PLUGIN_DIR . 'msme-erp/core/installer.php';
    global $wpdb; msme_erp_ensure_default_uoms($wpdb, $wpdb->prefix . 'erp_');
    $uoms = DB::fetchAll("SELECT id,code,name,uom_type FROM units_of_measure WHERE COALESCE(is_active,1)=1 ORDER BY code");
}
$groups = DB::fetchAll("SELECT g.id,g.code,g.name,a.account_code,a.account_name FROM material_groups g LEFT JOIN chart_of_accounts a ON a.id=g.inventory_account_id WHERE g.company_id=? AND COALESCE(g.is_active,1)=1 ORDER BY g.name", [$companyId]);

// ══════════════════════════════════════════════════════════════════════
// MM01 / MM02 — create / change
// ══════════════════════════════════════════════════════════════════════
if ($tcode === 'MM01' || $tcode === 'MM02') {
    $editId = $tcode === 'MM02' ? (int)$_GET['id'] : 0;
    $item   = $editId ? DB::fetchOne("SELECT * FROM items WHERE id=? AND company_id=? AND is_deleted=0", [$editId, $companyId]) : null;
    if ($editId && !$item) { msme_erp_set_flash('Material not found.', 'danger'); wp_redirect(msme_erp_mat_url('MM03')); exit; }

    $usage      = $editId ? msme_erp_mat_usage($editId, $companyId) : [];
    $stockTotal = $editId ? msme_erp_mat_stock_total($editId) : 0.0;
    $uomLocked  = $editId && (($usage['moves'] ?? 0) > 0 || abs($stockTotal) > 0.0005);   // changing the base unit after movements would corrupt every quantity

    // ---------------- POST ----------------
    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) wp_die('CSRF');
        $in  = wp_unslash($_POST);
        $act = sanitize_key($in['mat_action'] ?? 'save');
        try {
            // ---- alternate units of measure
            if ($editId && $act === 'uom_add') {
                $alt = (int)($in['alt_uom_id'] ?? 0); $f = (float)($in['factor'] ?? 0);
                if (!$alt || $alt === (int)$item['base_uom_id']) throw new Exception('Pick an alternate unit different from the base unit.');
                if ($f <= 0) throw new Exception('Conversion factor must be greater than zero.');
                if (!DB::fetchValue("SELECT id FROM units_of_measure WHERE id=?", [$alt])) throw new Exception('Unknown unit.');
                $ex = (int)DB::fetchValue("SELECT id FROM item_uom_conversions WHERE item_id=? AND alt_uom_id=?", [$editId, $alt]);
                if ($ex) DB::update('item_uom_conversions', ['factor' => $f], 'id=? AND item_id=?', [$ex, $editId]);
                else     DB::insert('item_uom_conversions', ['company_id' => $companyId, 'item_id' => $editId, 'alt_uom_id' => $alt, 'factor' => $f]);
                Auth::audit('items', $editId, 'UPDATE', [], ['alt_uom_added' => $alt, 'factor' => $f]);
                msme_erp_set_flash('Alternate unit saved.');
                wp_redirect(msme_erp_mat_url('MM02', ['id' => $editId]) . '#uom'); exit;
            }
            if ($editId && $act === 'uom_del') {
                DB::query("DELETE FROM item_uom_conversions WHERE id=? AND item_id=?", [(int)($in['conv_id'] ?? 0), $editId]);
                Auth::audit('items', $editId, 'UPDATE', [], ['alt_uom_removed' => (int)($in['conv_id'] ?? 0)]);
                msme_erp_set_flash('Alternate unit removed.');
                wp_redirect(msme_erp_mat_url('MM02', ['id' => $editId]) . '#uom'); exit;
            }

            // ---- delete (only when never used)
            if ($editId && $act === 'delete') {
                if (!current_user_can('manage_options')) throw new Exception('Only an administrator can delete a material.');
                if ($stockTotal != 0.0 || array_sum($usage) > 0) throw new Exception('This material has stock or has been used in documents — it cannot be deleted. Deactivate or block it instead.');
                $newCode = mb_substr($item['item_code'], 0, 38) . '~DEL' . $editId;   // frees the code for reuse (unique key)
                DB::update('items', ['is_deleted' => 1, 'is_active' => 0, 'item_code' => $newCode], 'id=? AND company_id=?', [$editId, $companyId]);
                Auth::audit('items', $editId, 'DELETE', ['item_code' => $item['item_code'], 'item_name' => $item['item_name']], []);
                msme_erp_set_flash("Material {$item['item_code']} deleted.");
                wp_redirect(msme_erp_mat_url('MM03')); exit;
            }

            // ---- save
            $in['is_active'] = isset($_POST['is_active']) ? 1 : 0;
            if ($editId && $uomLocked) $in['base_uom_id'] = (int)$item['base_uom_id'];
            unset($GLOBALS['msme_erp_mat_similar']);
            [$d, $errs] = msme_erp_mat_clean($in, $companyId, $editId ?: null);
            if (!$errs && !empty($GLOBALS['msme_erp_mat_similar'])) {
                $needDup = $GLOBALS['msme_erp_mat_similar'];
                $errs[]  = 'A material with a very similar description already exists (listed below). Change the description, or tick "Create anyway" and save again.';
            }
            if ($errs) throw new Exception(implode("\n", $errs));

            if ($editId) {
                if ($d['material_group_id'] === null) $d['material_group_id'] = 0;        // update() cannot write NULL into an INT column
                $item['material_group_id'] = (int)$item['material_group_id'];
                $diff = msme_erp_mat_diff($item, $d);
                DB::update('items', $d, 'id=? AND company_id=?', [$editId, $companyId]);
                if ($diff) Auth::audit('items', $editId, 'UPDATE', array_map(fn($x) => $x[0], $diff), array_map(fn($x) => $x[1], $diff));
                msme_erp_set_flash($diff ? "Material {$item['item_code']} updated (" . count($diff) . ' field(s) changed).' : 'No changes to save.', $diff ? 'success' : 'info');
                wp_redirect(msme_erp_mat_url('MM03', ['id' => $editId])); exit;
            }
            $d['company_id'] = $companyId; $d['created_by'] = Auth::userId(); $d['is_deleted'] = 0;
            $newId = DB::insert('items', $d);
            Auth::audit('items', $newId, 'INSERT', [], ['item_code' => $d['item_code'], 'item_name' => $d['item_name']]);
            msme_erp_set_flash("Material '{$d['item_name']}' ({$d['item_code']}) created.");
            wp_redirect(msme_erp_mat_url('MM03', ['id' => $newId])); exit;
        } catch (Throwable $e) {
            $error = $e->getMessage();
            if (stripos($error, 'Duplicate entry') !== false) $error = 'That item code already exists in this company.';
        }
    }

    // ---------------- form values ----------------
    $defaults = ['item_code' => '', 'item_name' => '', 'description' => '', 'item_type' => 'MATERIAL', 'item_category' => 'RAW', 'manufacturer' => '',
        'material_group_id' => '', 'base_uom_id' => '', 'hsn_code' => '', 'gst_rate' => 18, 'standard_price' => 0, 'moving_avg_price' => 0,
        'min_stock' => 0, 'max_stock' => 0, 'reorder_point' => 0, 'lead_time_days' => 0, 'lot_size' => 1, 'qc_required' => 0, 'batch_managed' => 0,
        'shelf_life_days' => 0, 'gross_weight' => 0, 'block_status' => '', 'is_active' => 1];
    $v = $defaults;
    $copyId = (int)($_GET['copy_from'] ?? 0);
    if ($item) $v = array_merge($v, array_intersect_key($item, $defaults));
    elseif ($copyId && ($src = DB::fetchOne("SELECT * FROM items WHERE id=? AND company_id=?", [$copyId, $companyId]))) {
        $v = array_merge($v, array_intersect_key($src, $defaults)); $v['item_code'] = ''; $v['moving_avg_price'] = 0;
        $copyNote = "Copied from {$src['item_code']} — enter a new description and (optionally) a code.";
    }
    if ($_SERVER['REQUEST_METHOD'] === 'POST' && $error !== '') { $p = wp_unslash($_POST); $v = array_merge($v, array_intersect_key($p, $defaults)); $v['is_active'] = isset($p['is_active']) ? 1 : 0; if ($editId) $v['item_code'] = $item['item_code']; }

    $pageTitle   = $editId ? 'Change Material — MM02' : 'Create Material Master — MM01';
    $pageTcode   = $tcode;
    $breadcrumbs = ['MM', 'Materials', $editId ? 'Change Material' : 'Create Material'];
    $pageActions = '<a href="' . esc_url(msme_erp_mat_url('MM03', $editId ? ['id' => $editId] : [])) . '" class="btn btn-sm btn-outline-secondary"><i class="bi bi-list me-1"></i>Display (MM03)</a>';

    erp_render_page_header();
    if (!empty($copyNote)) erp_alert($copyNote, 'info');
    if ($error) echo '<div class="alert alert-danger small" style="white-space:pre-line">' . esc_html($error) . '</div>';
    if ($needDup): ?>
      <div class="alert alert-warning small">
        <b>Similar materials already in the master:</b>
        <ul class="mb-2"><?php foreach ($needDup as $s): ?><li><a href="<?= esc_url(msme_erp_mat_url('MM03', ['id' => $s['id']])) ?>" target="_blank"><?= msme_erp_mat_h($s['item_code']) ?></a> — <?= msme_erp_mat_h($s['item_name']) ?> <span class="badge bg-secondary"><?= (int)$s['score'] ?>%</span></li><?php endforeach; ?></ul>
        <div class="form-check"><input class="form-check-input" type="checkbox" form="mm-form" name="confirm_dup" value="1" id="cfdup"><label class="form-check-label fw-semibold" for="cfdup">Create anyway — this is a different material</label></div>
      </div>
    <?php endif;

    $selUom = (int)$v['base_uom_id'];
    $mapRo  = $editId || false;
    ?>
    <form method="POST" id="mm-form" autocomplete="off">
    <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
    <input type="hidden" name="mat_action" value="save">
    <div class="row g-3">
      <!-- Col 1: Basic, classification -->
      <div class="col-lg-4">
        <div class="card border-0 shadow-sm mb-3">
          <div class="card-header text-white fw-bold small py-2" style="background:linear-gradient(135deg,#1565c0,#1976d2)"><i class="bi bi-box-seam me-2"></i>Basic Data</div>
          <div class="card-body p-3">
            <div class="mb-2">
              <label class="form-label fw-semibold small mb-1">Material Code</label>
              <input type="text" name="item_code" id="mm-code" class="form-control form-control-sm fw-bold text-uppercase" maxlength="50"
                     value="<?= esc_attr($v['item_code']) ?>" <?= $editId ? 'readonly' : 'placeholder="Leave blank for automatic number (MAT000001…)"' ?>>
              <div class="form-text small"><?= $editId ? 'The code cannot be changed after creation.' : 'Letters, digits and . _ - / only. Unique within the company.' ?></div>
            </div>
            <div class="mb-2">
              <label class="form-label fw-semibold small mb-1">Material Description <span class="text-danger">*</span></label>
              <input type="text" name="item_name" id="mm-name" class="form-control form-control-sm" required maxlength="200" value="<?= esc_attr($v['item_name']) ?>">
            </div>
            <div class="mb-2">
              <label class="form-label fw-semibold small mb-1">Long Description</label>
              <textarea name="description" class="form-control form-control-sm" rows="2" placeholder="Specifications, dimensions, grade, standards..."><?= esc_textarea($v['description']) ?></textarea>
            </div>
            <div class="row g-2 mb-2">
              <div class="col-6"><label class="form-label fw-semibold small mb-1">Material Type</label>
                <select name="item_type" id="mm-type" class="form-select form-select-sm"><?php foreach (msme_erp_mat_types() as $k => $l): ?><option value="<?= $k ?>" <?= $v['item_type'] === $k ? 'selected' : '' ?>><?= $l ?></option><?php endforeach; ?></select></div>
              <div class="col-6"><label class="form-label fw-semibold small mb-1">Category</label>
                <select name="item_category" id="mm-cat" class="form-select form-select-sm"><?php foreach (msme_erp_mat_categories() as $k => $l): ?><option value="<?= $k ?>" <?= $v['item_category'] === $k ? 'selected' : '' ?>><?= $l ?></option><?php endforeach; ?></select></div>
            </div>
            <div><label class="form-label fw-semibold small mb-1">Manufacturer / Brand</label>
              <input type="text" name="manufacturer" class="form-control form-control-sm" maxlength="100" value="<?= esc_attr($v['manufacturer']) ?>"></div>
          </div>
        </div>

        <div class="card border-0 shadow-sm mb-3">
          <div class="card-header bg-white fw-bold small py-2 border-bottom"><i class="bi bi-tags me-2 text-primary"></i>Classification &amp; Tax</div>
          <div class="card-body p-3">
            <div class="mb-2">
              <label class="form-label fw-semibold small mb-1">Material Group</label>
              <select name="material_group_id" id="mm-group" class="form-select form-select-sm">
                <option value="" data-gl="">— None —</option>
                <?php foreach ($groups as $g): ?>
                <option value="<?= (int)$g['id'] ?>" data-gl="<?= esc_attr($g['account_code'] ? $g['account_code'] . ' — ' . $g['account_name'] : '') ?>" <?= (int)$v['material_group_id'] === (int)$g['id'] ? 'selected' : '' ?>><?= esc_html($g['code'] . ' — ' . $g['name']) ?></option>
                <?php endforeach; ?>
              </select>
              <div class="form-text small">Inventory GL account: <b id="mm-gl">company default</b> <a href="<?= esc_url(msme_erp_mat_url('OMSF')) ?>" class="ms-1">manage groups</a></div>
            </div>
            <div class="mb-2">
              <label class="form-label fw-semibold small mb-1">Base Unit of Measure <span class="text-danger">*</span></label>
              <select name="base_uom_id" id="mm-uom" class="form-select form-select-sm" required <?= $uomLocked ? 'disabled' : '' ?>>
                <option value="">UOM</option>
                <?php foreach ($uoms as $u): ?><option value="<?= (int)$u['id'] ?>" data-code="<?= esc_attr($u['code']) ?>" <?= $selUom === (int)$u['id'] ? 'selected' : '' ?>><?= esc_html($u['code']) ?></option><?php endforeach; ?>
              </select>
              <?php if ($uomLocked): ?><input type="hidden" name="base_uom_id" value="<?= $selUom ?>"><div class="form-text small text-warning">Locked — stock movements exist for this material.</div><?php endif; ?>
            </div>
            <div class="row g-2">
              <div class="col-6"><label class="form-label fw-semibold small mb-1">HSN / SAC</label>
                <input type="text" name="hsn_code" class="form-control form-control-sm" maxlength="8" inputmode="numeric" placeholder="4, 6 or 8 digits" value="<?= esc_attr($v['hsn_code']) ?>"></div>
              <div class="col-6"><label class="form-label fw-semibold small mb-1">GST Rate %</label>
                <select name="gst_rate" id="mm-gst" class="form-select form-select-sm">
                  <?php $gv = round((float)$v['gst_rate'], 2); $rates = msme_erp_mat_gst_rates(); if (!in_array($gv, array_map(fn($x) => round($x, 2), $rates), true)) $rates[] = $gv; sort($rates);
                  foreach ($rates as $r): ?><option value="<?= $r ?>" <?= round((float)$r, 2) === $gv ? 'selected' : '' ?>><?= $r ?>%</option><?php endforeach; ?>
                </select></div>
            </div>
          </div>
        </div>
      </div>

      <!-- Col 2: valuation, planning, quality -->
      <div class="col-lg-4">
        <div class="card border-0 shadow-sm mb-3">
          <div class="card-header bg-white fw-bold small py-2 border-bottom"><i class="bi bi-currency-rupee me-2 text-success"></i>Valuation &amp; Pricing</div>
          <div class="card-body p-3">
            <div class="small mb-2"><span class="text-muted">Valuation method:</span> <b>Moving average price</b> (weighted over all warehouses)</div>
            <div class="row g-2 mb-2">
              <div class="col-6"><label class="form-label fw-semibold small mb-1">Standard Cost (<?= esc_html($cur) ?>)</label>
                <input type="number" name="standard_price" id="mm-std" class="form-control form-control-sm text-end" value="<?= esc_attr($v['standard_price']) ?>" min="0" step="0.0001">
                <div class="form-text small">Reference cost for production costing.</div></div>
              <div class="col-6"><label class="form-label fw-semibold small mb-1">Moving Avg Price (<?= esc_html($cur) ?>)</label>
                <input type="number" name="moving_avg_price" id="mm-map" class="form-control form-control-sm text-end" value="<?= esc_attr($v['moving_avg_price']) ?>" min="0" step="0.0001" <?= $mapRo ? 'readonly' : '' ?>>
                <div class="form-text small"><?= $mapRo ? 'Maintained by goods movements.' : 'Opening value; updated by every GRN.' ?></div></div>
            </div>
          </div>
        </div>

        <div class="card border-0 shadow-sm mb-3">
          <div class="card-header bg-white fw-bold small py-2 border-bottom"><i class="bi bi-graph-up me-2 text-warning"></i>MRP / Planning</div>
          <div class="card-body p-3">
            <div class="row g-2 mb-2">
              <div class="col-4"><label class="form-label fw-semibold small mb-1">Min Stock</label><input type="number" name="min_stock" class="form-control form-control-sm text-end" value="<?= esc_attr($v['min_stock']) ?>" min="0" step="0.001"></div>
              <div class="col-4"><label class="form-label fw-semibold small mb-1">Max Stock</label><input type="number" name="max_stock" class="form-control form-control-sm text-end" value="<?= esc_attr($v['max_stock']) ?>" min="0" step="0.001"></div>
              <div class="col-4"><label class="form-label fw-semibold small mb-1">Reorder Pt.</label><input type="number" name="reorder_point" class="form-control form-control-sm text-end" value="<?= esc_attr($v['reorder_point']) ?>" min="0" step="0.001"></div>
            </div>
            <div class="row g-2">
              <div class="col-6"><label class="form-label fw-semibold small mb-1">Lead Time (days)</label><input type="number" name="lead_time_days" class="form-control form-control-sm" value="<?= esc_attr($v['lead_time_days']) ?>" min="0"></div>
              <div class="col-6"><label class="form-label fw-semibold small mb-1">Lot Size</label><input type="number" name="lot_size" class="form-control form-control-sm" value="<?= esc_attr($v['lot_size']) ?>" min="0.001" step="0.001"></div>
            </div>
            <div class="form-text small mt-2">Reorder point is compared with <b>total stock across all warehouses</b> (see ZMRP).</div>
          </div>
        </div>

        <div class="card border-0 shadow-sm mb-3">
          <div class="card-header bg-white fw-bold small py-2 border-bottom"><i class="bi bi-shield-check me-2 text-info"></i>Quality &amp; Storage</div>
          <div class="card-body p-3">
            <div class="row g-2 mb-2">
              <div class="col-6"><label class="form-label fw-semibold small mb-1">QC on GRN</label>
                <select name="qc_required" class="form-select form-select-sm"><option value="0">Not required</option><option value="1" <?= $v['qc_required'] ? 'selected' : '' ?>>Required</option></select></div>
              <div class="col-6"><label class="form-label fw-semibold small mb-1">Batch Managed</label>
                <select name="batch_managed" class="form-select form-select-sm"><option value="0">No batch</option><option value="1" <?= $v['batch_managed'] ? 'selected' : '' ?>>Batch managed</option></select></div>
            </div>
            <div class="row g-2">
              <div class="col-6"><label class="form-label fw-semibold small mb-1">Shelf Life (days)</label><input type="number" name="shelf_life_days" class="form-control form-control-sm" value="<?= esc_attr($v['shelf_life_days']) ?>" min="0" placeholder="0 = no expiry"></div>
              <div class="col-6"><label class="form-label fw-semibold small mb-1">Gross Weight (kg)</label><input type="number" name="gross_weight" class="form-control form-control-sm" value="<?= esc_attr($v['gross_weight']) ?>" min="0" step="0.001"></div>
            </div>
            <div class="form-text small mt-2">A shelf life automatically switches batch management on.</div>
          </div>
        </div>
      </div>

      <!-- Col 3: status, preview, actions -->
      <div class="col-lg-4">
        <div class="card border-0 shadow-sm mb-3">
          <div class="card-header bg-white fw-bold small py-2 border-bottom"><i class="bi bi-toggles me-2 text-secondary"></i>Status</div>
          <div class="card-body p-3">
            <div class="form-check mb-2"><input class="form-check-input" type="checkbox" name="is_active" value="1" id="mm-active" <?= $v['is_active'] ? 'checked' : '' ?>>
              <label class="form-check-label fw-semibold small" for="mm-active">Material is active</label></div>
            <label class="form-label fw-semibold small mb-1">Block status</label>
            <select name="block_status" class="form-select form-select-sm"><?php foreach (msme_erp_mat_blocks() as $k => $l): ?><option value="<?= $k ?>" <?= $v['block_status'] === $k ? 'selected' : '' ?>><?= $l ?></option><?php endforeach; ?></select>
            <div class="form-text small">Blocked materials disappear from the PR / PO (procurement) or sales order / invoice (sales) pickers; stock and history are kept.</div>
          </div>
        </div>

        <div class="card border-0 shadow-sm mb-3">
          <div class="card-header bg-white fw-bold small py-2 border-bottom"><i class="bi bi-eye me-2 text-secondary"></i>Live Preview</div>
          <div class="card-body p-3">
            <div class="text-center mb-3 p-3" style="background:#f8f9fa;border-radius:8px">
              <div class="fw-bold fs-5 text-primary" id="prev-code" style="font-family:monospace">———</div>
              <div class="text-muted small" id="prev-name">Description will appear here</div>
            </div>
            <table class="table table-sm table-borderless mb-0" style="font-size:.8rem">
              <tr><td class="text-muted">Category:</td><td class="fw-semibold" id="prev-cat">—</td></tr>
              <tr><td class="text-muted">UOM:</td><td id="prev-uom">—</td></tr>
              <tr><td class="text-muted">GST:</td><td id="prev-gst">—</td></tr>
              <tr><td class="text-muted">Std cost:</td><td class="fw-bold text-success" id="prev-price">—</td></tr>
            </table>
          </div>
        </div>

        <div class="card border-0 shadow-sm mb-3"><div class="card-body p-3 d-grid gap-2">
          <button type="submit" class="btn btn-primary fw-semibold py-2"><i class="bi bi-save me-1"></i><?= $editId ? 'Save Changes' : 'Create Material Master' ?></button>
          <a href="<?= esc_url(msme_erp_mat_url('MM03', $editId ? ['id' => $editId] : [])) ?>" class="btn btn-outline-danger btn-sm"><i class="bi bi-x me-1"></i>Cancel</a>
        </div></div>
      </div>
    </div>
    </form>

    <?php if ($editId): ?>
    <div class="row g-3 mt-0">
      <div class="col-lg-8" id="uom">
        <div class="card border-0 shadow-sm">
          <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-arrow-left-right me-2 text-primary"></i>Alternate Units of Measure <span class="text-muted fw-normal">(buy in one unit, stock in another)</span></div>
          <div class="card-body p-3">
            <?php $alts = DB::fetchAll("SELECT c.id,c.factor,u.code,u.name FROM item_uom_conversions c JOIN units_of_measure u ON u.id=c.alt_uom_id WHERE c.item_id=? ORDER BY u.code", [$editId]);
            $baseCode = (string)DB::fetchValue("SELECT code FROM units_of_measure WHERE id=?", [(int)$item['base_uom_id']]); ?>
            <?php if ($alts): ?><table class="table table-sm mb-3" style="font-size:.82rem"><thead class="table-light"><tr><th>Alternate unit</th><th>Equals</th><th></th></tr></thead><tbody>
              <?php foreach ($alts as $a): ?><tr><td>1 <b><?= msme_erp_mat_h($a['code']) ?></b></td><td><?= msme_erp_mat_fmt($a['factor'], 6) ?> <?= msme_erp_mat_h($baseCode) ?></td>
                <td class="text-end"><form method="POST" class="d-inline" onsubmit="return confirm('Remove this conversion?')"><input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>"><input type="hidden" name="mat_action" value="uom_del"><input type="hidden" name="conv_id" value="<?= (int)$a['id'] ?>"><button class="btn btn-sm btn-outline-danger py-0"><i class="bi bi-trash"></i></button></form></td></tr><?php endforeach; ?></tbody></table>
            <?php else: ?><div class="text-muted small mb-3">No alternate units yet.</div><?php endif; ?>
            <form method="POST" class="row g-2 align-items-end">
              <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>"><input type="hidden" name="mat_action" value="uom_add">
              <div class="col-auto"><label class="form-label small mb-1">1 unit of</label>
                <select name="alt_uom_id" class="form-select form-select-sm" required><option value="">UOM</option><?php foreach ($uoms as $u): if ((int)$u['id'] === (int)$item['base_uom_id']) continue; ?><option value="<?= (int)$u['id'] ?>"><?= esc_html($u['code']) ?></option><?php endforeach; ?></select></div>
              <div class="col-auto"><label class="form-label small mb-1">equals (<?= msme_erp_mat_h($baseCode) ?>)</label><input type="number" name="factor" class="form-control form-control-sm" step="0.000001" min="0.000001" required style="width:140px"></div>
              <div class="col-auto"><button class="btn btn-sm btn-outline-primary"><i class="bi bi-plus-circle me-1"></i>Add / update</button></div>
            </form>
            <div class="form-text small mt-2">Use <code>msme_erp_mat_to_base_qty()</code> to convert. PO / GRN lines still post in the unit chosen on the line.</div>
          </div>
        </div>
      </div>
      <div class="col-lg-4">
        <div class="card border-danger border-opacity-25 shadow-sm">
          <div class="card-header bg-white fw-bold small py-2 text-danger"><i class="bi bi-exclamation-octagon me-2"></i>Delete material</div>
          <div class="card-body p-3 small">
            <?php $used = array_sum($usage) > 0 || abs($stockTotal) > 0.0005; ?>
            <?php if ($used): ?>
              <div class="text-muted">Not deletable: used in <?= (int)$usage['po'] ?> PO line(s), <?= (int)$usage['pr'] ?> PR, <?= (int)$usage['grn'] ?> GRN, <?= (int)$usage['so'] ?> sales, <?= (int)$usage['bom'] ?> BOM, <?= (int)$usage['moves'] ?> stock movement(s) — deactivate or block it instead.</div>
            <?php else: ?>
              <div class="text-muted mb-2">Never used — can be removed. Its code becomes free again.</div>
              <form method="POST" onsubmit="return confirm('Delete this material permanently?')"><input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>"><input type="hidden" name="mat_action" value="delete"><button class="btn btn-sm btn-outline-danger"><i class="bi bi-trash me-1"></i>Delete</button></form>
            <?php endif; ?>
          </div>
        </div>
      </div>
    </div>
    <?php endif; ?>

    <script>
    (function(){
      var $=function(i){return document.getElementById(i)};
      var cur=<?= wp_json_encode($cur) ?>;
      function upd(){
        var u=$('mm-uom'); var opt=u&&u.options[u.selectedIndex];
        $('prev-code').textContent=$('mm-code').value||(<?= $editId ? '"—"' : '"auto"' ?>);
        $('prev-name').textContent=$('mm-name').value||'Description will appear here';
        $('prev-cat').textContent=$('mm-cat').options[$('mm-cat').selectedIndex].text;
        $('prev-uom').textContent=(opt&&opt.getAttribute('data-code'))||'—';
        $('prev-gst').textContent=$('mm-gst').value+'%';
        $('prev-price').textContent=cur+(parseFloat($('mm-std').value||0)).toFixed(4);
        var g=$('mm-group'); var go=g.options[g.selectedIndex]; $('mm-gl').textContent=(go&&go.getAttribute('data-gl'))||'company default (OBYC)';
      }
      ['mm-code','mm-name','mm-cat','mm-type','mm-uom','mm-gst','mm-std','mm-group'].forEach(function(id){var e=$(id); if(e){e.addEventListener('input',upd);e.addEventListener('change',upd);}});
      <?php if (!$editId): ?>$('mm-std').addEventListener('input',function(){$('mm-map').value=this.value;});<?php endif; ?>
      upd();
    })();
    </script>
    <?php
    return;
}

// ══════════════════════════════════════════════════════════════════════
// MM03 — display / search
// ══════════════════════════════════════════════════════════════════════
$itemId = (int)($_GET['id'] ?? 0);
$pick   = !empty($_GET['pick']);
$pageTitle   = $pick ? 'Select Material to Change — MM02' : 'Material Master Display — MM03';
$pageTcode   = 'MM03';
$breadcrumbs = ['MM', 'Materials', 'Display'];
$pageActions = '<a href="' . esc_url(msme_erp_mat_url('MM01')) . '" class="btn btn-sm btn-primary"><i class="bi bi-plus-circle me-1"></i>Create (MM01)</a> '
             . '<a href="' . esc_url(msme_erp_mat_url('ZMMI')) . '" class="btn btn-sm btn-outline-success"><i class="bi bi-file-earmark-spreadsheet me-1"></i>Import / Export</a> '
             . '<a href="' . esc_url(msme_erp_mat_url('ZMRP')) . '" class="btn btn-sm btn-outline-danger"><i class="bi bi-exclamation-diamond me-1"></i>Reorder report</a>';

$fCat    = sanitize_text_field($_GET['cat'] ?? '');
$fType   = sanitize_text_field($_GET['type'] ?? '');
$fSearch = sanitize_text_field($_GET['search'] ?? '');
$fStatus = sanitize_key($_GET['st'] ?? 'active');           // active | inactive | blocked | low | all
$sortKey = sanitize_key($_GET['sort'] ?? 'code');
$dir     = (($_GET['dir'] ?? 'asc') === 'desc') ? 'DESC' : 'ASC';
$stockSql = "(SELECT COALESCE(SUM(sb.qty_on_hand),0) FROM stock_balances sb WHERE sb.item_id=i.id)";
$sortMap  = ['code' => 'i.item_code', 'name' => 'i.item_name', 'cat' => 'i.item_category', 'gst' => 'i.gst_rate', 'map' => 'i.moving_avg_price', 'stock' => $stockSql];
$orderBy  = ($sortMap[$sortKey] ?? 'i.item_code') . " {$dir}, i.item_code ASC";

$sql = "SELECT i.id,i.item_code,i.item_name,i.item_category,i.item_type,i.hsn_code,i.gst_rate,i.moving_avg_price,i.standard_price,i.min_stock,i.reorder_point,
               i.lead_time_days,i.is_active,i.block_status,u.code AS uom,mg.name AS mg_name,{$stockSql} AS stock_total
          FROM items i LEFT JOIN units_of_measure u ON u.id=i.base_uom_id LEFT JOIN material_groups mg ON mg.id=i.material_group_id
         WHERE i.company_id=? AND i.is_deleted=0";
$params = [$companyId];
if ($fCat)    { $sql .= " AND i.item_category=?"; $params[] = $fCat; }
if ($fType)   { $sql .= " AND i.item_type=?";     $params[] = $fType; }
if ($fSearch) { $like = '%' . $fSearch . '%'; $sql .= " AND (i.item_code LIKE ? OR i.item_name LIKE ? OR i.hsn_code LIKE ? OR i.manufacturer LIKE ?)"; array_push($params, $like, $like, $like, $like); }
if ($fStatus === 'active')   $sql .= " AND i.is_active=1";
if ($fStatus === 'inactive') $sql .= " AND i.is_active=0";
if ($fStatus === 'blocked')  $sql .= " AND COALESCE(i.block_status,'')<>''";
if ($fStatus === 'low')      $sql .= " AND i.is_active=1 AND i.reorder_point>0 AND {$stockSql} <= i.reorder_point";
$sql .= " ORDER BY {$orderBy}";

$pg     = max(1, (int)($_GET['paged'] ?? 1));
$result = DB::paginate($sql, $params, $pg, 30);
$rows   = $result['data'];

$statMap = array_column(DB::fetchAll("SELECT item_category,COUNT(*) cnt FROM items WHERE company_id=? AND is_deleted=0 AND is_active=1 GROUP BY item_category", [$companyId]), 'cnt', 'item_category');
$nInactive = (int)DB::fetchValue("SELECT COUNT(*) FROM items WHERE company_id=? AND is_deleted=0 AND is_active=0", [$companyId]);
$nBlocked  = (int)DB::fetchValue("SELECT COUNT(*) FROM items WHERE company_id=? AND is_deleted=0 AND COALESCE(block_status,'')<>''", [$companyId]);
$total     = array_sum($statMap);

$detail = $itemId ? DB::fetchOne("SELECT i.*,u.code AS uom_code,u.name AS uom_name,mg.name AS mg_name,mg.code AS mg_code,a.account_code,a.account_name
        FROM items i LEFT JOIN units_of_measure u ON u.id=i.base_uom_id LEFT JOIN material_groups mg ON mg.id=i.material_group_id
        LEFT JOIN chart_of_accounts a ON a.id=mg.inventory_account_id WHERE i.id=? AND i.company_id=? AND i.is_deleted=0", [$itemId, $companyId]) : null;

$qs = static fn(array $o = []) => array_filter(array_merge(['cat' => $fCat, 'type' => $fType, 'search' => $fSearch, 'st' => $fStatus, 'pick' => $pick ? 1 : 0, 'id' => $itemId ?: 0], $o), static fn($x) => $x !== '' && $x !== 0 && $x !== null);
$sortLink = static function (string $key, string $label) use ($sortKey, $dir, $qs): string {
    $nd = ($sortKey === $key && $dir === 'ASC') ? 'desc' : 'asc';
    $arrow = $sortKey === $key ? ($dir === 'ASC' ? ' ▲' : ' ▼') : '';
    return '<a class="text-white text-decoration-none" href="' . esc_url(msme_erp_mat_url('MM03', $qs(['sort' => $key, 'dir' => $nd, 'id' => 0]))) . '">' . esc_html($label) . $arrow . '</a>';
};

erp_render_page_header();
if ($d1 = get_option('msme_erp_mat_dup_uq_company_item_code')) echo '<div class="alert alert-warning small"><b>Duplicate material codes exist in one company</b> (' . esc_html($d1) . '). Rename them so the per-company unique key can be installed (re-save any plugin setting or reactivate to retry).</div>';
$catColors = ['RAW' => 'primary', 'WIP' => 'warning', 'FG' => 'success', 'CONSUMABLE' => 'dark', 'SPARE' => 'info', 'TRADING' => 'secondary'];
?>
<div class="row g-2 mb-3">
  <div class="col"><a href="<?= esc_url(msme_erp_mat_url('MM03', ['st' => 'active'])) ?>" class="text-decoration-none"><div class="card border-0 shadow-sm text-center py-2"><div class="fw-bold fs-5"><?= $total ?></div><div class="small text-muted">Active</div></div></a></div>
  <?php foreach (['RAW' => '#1565c0', 'FG' => '#2e7d32', 'WIP' => '#e65100', 'CONSUMABLE' => '#6a1b9a', 'SPARE' => '#00695c', 'TRADING' => '#37474f'] as $cat => $color): $cnt = $statMap[$cat] ?? 0; if (!$cnt) continue; ?>
  <div class="col"><a href="<?= esc_url(msme_erp_mat_url('MM03', ['cat' => $cat])) ?>" class="text-decoration-none"><div class="card border-0 shadow-sm text-center py-2" style="border-top:3px solid <?= $color ?>!important"><div class="fw-bold fs-5" style="color:<?= $color ?>"><?= $cnt ?></div><div class="small text-muted"><?= $cat ?></div></div></a></div>
  <?php endforeach; ?>
  <div class="col"><a href="<?= esc_url(msme_erp_mat_url('MM03', ['st' => 'blocked'])) ?>" class="text-decoration-none"><div class="card border-0 shadow-sm text-center py-2"><div class="fw-bold fs-5 text-warning"><?= $nBlocked ?></div><div class="small text-muted">Blocked</div></div></a></div>
  <div class="col"><a href="<?= esc_url(msme_erp_mat_url('MM03', ['st' => 'inactive'])) ?>" class="text-decoration-none"><div class="card border-0 shadow-sm text-center py-2"><div class="fw-bold fs-5 text-secondary"><?= $nInactive ?></div><div class="small text-muted">Inactive</div></div></a></div>
</div>

<div class="row g-3">
  <div class="<?= $detail ? 'col-xl-7 col-lg-7' : 'col-12' ?>">
    <div class="card border-0 shadow-sm mb-3"><div class="card-body py-2 px-3">
      <form method="GET" class="row g-2 align-items-end">
        <input type="hidden" name="page" value="msme-erp"><input type="hidden" name="tcode" value="MM03"><?php if ($pick): ?><input type="hidden" name="pick" value="1"><?php endif; ?>
        <div class="col-md-3"><label class="form-label small mb-1">Search</label><input type="text" name="search" class="form-control form-control-sm" value="<?= esc_attr($fSearch) ?>" placeholder="Code / name / HSN / brand"></div>
        <div class="col-md-2"><label class="form-label small mb-1">Category</label><select name="cat" class="form-select form-select-sm"><option value="">All</option><?php foreach (msme_erp_mat_categories() as $k => $l): ?><option value="<?= $k ?>" <?= $fCat === $k ? 'selected' : '' ?>><?= $k ?></option><?php endforeach; ?></select></div>
        <div class="col-md-2"><label class="form-label small mb-1">Type</label><select name="type" class="form-select form-select-sm"><option value="">All</option><?php foreach (msme_erp_mat_types() as $k => $l): ?><option value="<?= $k ?>" <?= $fType === $k ? 'selected' : '' ?>><?= $k ?></option><?php endforeach; ?></select></div>
        <div class="col-md-2"><label class="form-label small mb-1">Status</label><select name="st" class="form-select form-select-sm">
          <?php foreach (['active' => 'Active', 'low' => 'Below reorder', 'blocked' => 'Blocked', 'inactive' => 'Inactive', 'all' => 'All'] as $k => $l): ?><option value="<?= $k ?>" <?= $fStatus === $k ? 'selected' : '' ?>><?= $l ?></option><?php endforeach; ?></select></div>
        <div class="col-auto"><button class="btn btn-primary btn-sm"><i class="bi bi-search me-1"></i>Filter</button>
          <a href="<?= esc_url(msme_erp_mat_url('MM03', $pick ? ['pick' => 1] : [])) ?>" class="btn btn-outline-secondary btn-sm ms-1">Clear</a>
          <a href="<?= esc_url(msme_erp_mat_url('ZMMI', ['export' => 'csv', 'cat' => $fCat, 'type' => $fType, 'search' => $fSearch, 'st' => $fStatus])) ?>" class="btn btn-outline-success btn-sm ms-1" title="Download the filtered list"><i class="bi bi-download"></i></a></div>
      </form>
    </div></div>

    <div class="card border-0 shadow-sm">
      <div class="card-header bg-white d-flex justify-content-between py-2"><span class="fw-bold small"><i class="bi bi-box-seam me-2"></i>Materials <span class="badge bg-secondary"><?= (int)$result['total_rows'] ?></span><?= $pick ? ' <span class="text-muted fw-normal">— click a row to change it</span>' : '' ?></span></div>
      <div class="table-responsive"><table class="table table-hover table-sm mb-0" style="font-size:.8rem">
        <thead style="background:#1565c0;color:#fff"><tr>
          <th class="px-3"><?= $sortLink('code', 'Code') ?></th><th><?= $sortLink('name', 'Description') ?></th><th><?= $sortLink('cat', 'Category') ?></th><th>UOM</th><th>HSN</th>
          <th class="text-center"><?= $sortLink('gst', 'GST%') ?></th><th class="text-end"><?= $sortLink('map', 'MAP (' . $cur . ')') ?></th><th class="text-end"><?= $sortLink('stock', 'Stock') ?></th><th class="text-center">Status</th></tr></thead>
        <tbody>
        <?php if (!$rows): ?><tr><td colspan="9" class="text-center text-muted py-5"><i class="bi bi-box-seam display-6 d-block mb-2 opacity-25"></i>No materials match. <a href="<?= esc_url(msme_erp_mat_url('MM01')) ?>">Create one →</a></td></tr><?php endif; ?>
        <?php foreach ($rows as $it):
            $low = (float)$it['reorder_point'] > 0 && (float)$it['stock_total'] <= (float)$it['reorder_point'];
            $go  = msme_erp_mat_url($pick ? 'MM02' : 'MM03', ['id' => $it['id']]); ?>
        <tr class="<?= (int)$it['id'] === $itemId ? 'table-primary' : '' ?>" style="cursor:pointer" onclick="window.location='<?= esc_url($go) ?>'">
          <td class="px-3 fw-bold text-primary"><?= msme_erp_mat_h($it['item_code']) ?></td>
          <td><div class="fw-semibold"><?= msme_erp_mat_h(mb_substr($it['item_name'], 0, 34)) ?><?= mb_strlen($it['item_name']) > 34 ? '…' : '' ?></div><?php if ($it['mg_name']): ?><div class="text-muted" style="font-size:.7rem"><?= msme_erp_mat_h($it['mg_name']) ?></div><?php endif; ?></td>
          <td><span class="badge bg-<?= $catColors[$it['item_category']] ?? 'secondary' ?>"><?= msme_erp_mat_h($it['item_category']) ?></span></td>
          <td><?= msme_erp_mat_h($it['uom']) ?></td><td class="text-muted"><?= msme_erp_mat_h($it['hsn_code'] ?: '—') ?></td>
          <td class="text-center"><?= msme_erp_mat_fmt($it['gst_rate'], 2) ?>%</td>
          <td class="text-end fw-bold"><?= number_format((float)$it['moving_avg_price'], 2) ?></td>
          <td class="text-end <?= $low ? 'text-danger fw-bold' : '' ?>"><?= msme_erp_mat_fmt($it['stock_total']) ?><?= $low ? ' <i class="bi bi-exclamation-triangle-fill" title="At or below reorder point"></i>' : '' ?></td>
          <td class="text-center"><?php if (!$it['is_active']): ?><span class="badge bg-secondary">Inactive</span><?php elseif ($it['block_status']): ?><span class="badge bg-warning text-dark" title="<?= esc_attr(msme_erp_mat_blocks()[$it['block_status']] ?? '') ?>">Blocked</span><?php else: ?><i class="bi bi-check-circle-fill text-success"></i><?php endif; ?></td>
        </tr>
        <?php endforeach; ?>
        </tbody></table></div>
      <?php if ($result['total_pages'] > 1): ?><div class="card-footer bg-white py-2"><?php erp_pagination($result, ['tcode' => 'MM03']); ?></div><?php endif; ?>
    </div>
  </div>

<?php if ($detail):
    $stock = DB::fetchAll("SELECT sb.qty_on_hand,sb.qty_reserved,sb.avg_cost,w.code AS wh_code,w.name AS wh_name FROM stock_balances sb JOIN warehouses w ON w.id=sb.warehouse_id WHERE sb.item_id=? AND w.company_id=? ORDER BY w.code", [$itemId, $companyId]);
    $totalStock = array_sum(array_column($stock, 'qty_on_hand'));
    $pos = DB::fetchAll("SELECT o.id,o.po_number,o.po_date,o.status,s.name AS supplier,l.qty_ordered,l.unit_price,l.qty_received
            FROM purchase_order_lines l JOIN purchase_orders o ON o.id=l.po_id LEFT JOIN suppliers s ON s.id=o.supplier_id
           WHERE l.item_id=? AND o.company_id=? AND o.status NOT IN ('CANCELLED','REJECTED','DRAFT') ORDER BY o.po_date DESC, o.id DESC LIMIT 5", [$itemId, $companyId]);
    $lastPrice = $pos ? (float)$pos[0]['unit_price'] : 0.0;
    $usage = msme_erp_mat_usage($itemId, $companyId);
    $boms  = DB::fetchAll("SELECT DISTINCT h.id,h.bom_number,p.item_code,p.item_name FROM bom_lines l JOIN bom_headers h ON h.id=l.bom_id JOIN items p ON p.id=h.item_id WHERE l.component_id=? AND h.company_id=? LIMIT 10", [$itemId, $companyId]);
    $openPR = DB::fetchAll("SELECT r.id,r.pr_number,l.qty_requested FROM purchase_requisition_lines l JOIN purchase_requisitions r ON r.id=l.pr_id WHERE l.item_id=? AND r.company_id=? AND COALESCE(l.status,'OPEN')='OPEN' AND r.status NOT IN ('REJECTED','CANCELLED') ORDER BY r.id DESC LIMIT 8", [$itemId, $companyId]);
    $openPO = DB::fetchAll("SELECT o.id,o.po_number,(l.qty_ordered-COALESCE(l.qty_received,0)) AS open_qty FROM purchase_order_lines l JOIN purchase_orders o ON o.id=l.po_id WHERE l.item_id=? AND o.company_id=? AND o.status NOT IN ('CANCELLED','REJECTED','CLOSED','DRAFT') AND l.qty_ordered>COALESCE(l.qty_received,0) ORDER BY o.id DESC LIMIT 8", [$itemId, $companyId]);
    $openSO = DB::fetchAll("SELECT s.id,s.so_number,(l.qty_ordered-COALESCE(l.qty_delivered,0)) AS open_qty FROM sales_order_lines l JOIN sales_orders s ON s.id=l.so_id WHERE l.item_id=? AND s.company_id=? AND COALESCE(l.status,'OPEN') NOT IN ('CANCELLED','CLOSED') AND l.qty_ordered>COALESCE(l.qty_delivered,0) AND s.status NOT IN ('CANCELLED','REJECTED','CLOSED') ORDER BY s.id DESC LIMIT 8", [$itemId, $companyId]);
    $alts  = DB::fetchAll("SELECT c.factor,u.code FROM item_uom_conversions c JOIN units_of_measure u ON u.id=c.alt_uom_id WHERE c.item_id=? ORDER BY u.code", [$itemId]);
    $hist  = DB::fetchAll("SELECT created_at,username,action,old_data,new_data FROM audit_logs WHERE table_name='items' AND record_id=? ORDER BY id DESC LIMIT 25", [$itemId]);
    $blocked = (string)($detail['block_status'] ?? '');
?>
  <div class="col-xl-5 col-lg-5"><div class="card border-0 shadow-sm" style="position:sticky;top:72px">
    <div class="card-header text-white fw-bold small py-2 d-flex justify-content-between align-items-center" style="background:linear-gradient(135deg,#1565c0,#1976d2)">
      <span><i class="bi bi-box-seam me-2"></i><?= msme_erp_mat_h($detail['item_code']) ?></span>
      <span class="d-flex gap-1">
        <a href="<?= esc_url(msme_erp_mat_url('MM02', ['id' => $itemId])) ?>" class="btn btn-sm btn-light py-0 px-2" title="Change (MM02)"><i class="bi bi-pencil"></i></a>
        <a href="<?= esc_url(msme_erp_mat_url('MM01', ['copy_from' => $itemId])) ?>" class="btn btn-sm btn-light py-0 px-2" title="Copy as new material"><i class="bi bi-files"></i></a>
        <a href="<?= esc_url(msme_erp_mat_url('MM03', $qs(['id' => 0]))) ?>" class="btn btn-sm btn-light py-0 px-2"><i class="bi bi-x-lg"></i></a></span></div>
    <div class="card-body p-3">
      <h6 class="fw-bold mb-1"><?= msme_erp_mat_h($detail['item_name']) ?></h6>
      <?php if ($detail['description']): ?><div class="text-muted small mb-2"><?= msme_erp_mat_h($detail['description']) ?></div><?php endif; ?>
      <div class="d-flex gap-2 mb-2 flex-wrap">
        <span class="badge bg-<?= $catColors[$detail['item_category']] ?? 'secondary' ?>"><?= msme_erp_mat_h($detail['item_category']) ?></span>
        <span class="badge bg-secondary"><?= msme_erp_mat_h($detail['item_type']) ?></span>
        <span class="badge bg-<?= $detail['is_active'] ? 'success' : 'danger' ?>"><?= $detail['is_active'] ? 'Active' : 'Inactive' ?></span>
        <?php if ($blocked): ?><span class="badge bg-warning text-dark"><?= msme_erp_mat_h(msme_erp_mat_blocks()[$blocked] ?? $blocked) ?></span><?php endif; ?>
      </div>
      <ul class="nav nav-tabs nav-tabs-sm small" role="tablist" style="font-size:.78rem">
        <?php foreach (['d' => 'Details', 's' => 'Stock', 'p' => 'Purchases', 'w' => 'Where used', 'u' => 'Units', 'h' => 'History'] as $k => $l): ?>
        <li class="nav-item"><button class="nav-link py-1 px-2 <?= $k === 'd' ? 'active' : '' ?>" data-bs-toggle="tab" data-bs-target="#mt-<?= $k ?>" type="button"><?= $l ?></button></li><?php endforeach; ?>
      </ul>
      <div class="tab-content pt-2" style="font-size:.8rem">
        <div class="tab-pane fade show active" id="mt-d"><table class="table table-sm table-borderless mb-0">
          <tr><td class="text-muted fw-semibold" width="130">Material group</td><td><?= msme_erp_mat_h($detail['mg_name'] ? $detail['mg_code'] . ' — ' . $detail['mg_name'] : '—') ?></td></tr>
          <tr><td class="text-muted fw-semibold">Inventory GL</td><td><?= $detail['account_code'] ? msme_erp_mat_h($detail['account_code'] . ' — ' . $detail['account_name']) : '<span class="text-muted">company default</span>' ?></td></tr>
          <tr><td class="text-muted fw-semibold">Base UOM</td><td><?= msme_erp_mat_h($detail['uom_code'] . ' — ' . $detail['uom_name']) ?></td></tr>
          <tr><td class="text-muted fw-semibold">HSN / GST</td><td><?= msme_erp_mat_h($detail['hsn_code'] ?: '—') ?> · <?= msme_erp_mat_fmt($detail['gst_rate'], 2) ?>%</td></tr>
          <tr><td class="text-muted fw-semibold">Moving avg price</td><td class="fw-bold text-success"><?= esc_html($cur) ?><?= number_format((float)$detail['moving_avg_price'], 4) ?></td></tr>
          <tr><td class="text-muted fw-semibold">Standard cost</td><td><?= esc_html($cur) ?><?= number_format((float)$detail['standard_price'], 4) ?></td></tr>
          <tr><td class="text-muted fw-semibold">Last purchase</td><td><?= $lastPrice > 0 ? esc_html($cur) . number_format($lastPrice, 4) : '—' ?></td></tr>
          <tr><td class="text-muted fw-semibold">Min / Max / Reorder</td><td><?= msme_erp_mat_fmt($detail['min_stock']) ?> / <?= msme_erp_mat_fmt($detail['max_stock']) ?> / <b><?= msme_erp_mat_fmt($detail['reorder_point']) ?></b></td></tr>
          <tr><td class="text-muted fw-semibold">Lead time / lot</td><td><?= (int)$detail['lead_time_days'] ?> days · <?= msme_erp_mat_fmt($detail['lot_size']) ?></td></tr>
          <tr><td class="text-muted fw-semibold">QC / batch / shelf</td><td><?= $detail['qc_required'] ? 'QC' : '—' ?> · <?= $detail['batch_managed'] ? 'Batch' : '—' ?> · <?= (int)$detail['shelf_life_days'] ?>d</td></tr>
          <?php if (!empty($detail['manufacturer'])): ?><tr><td class="text-muted fw-semibold">Manufacturer</td><td><?= msme_erp_mat_h($detail['manufacturer']) ?></td></tr><?php endif; ?>
        </table></div>

        <div class="tab-pane fade" id="mt-s">
          <div class="mb-2"><b>Total:</b> <span class="badge bg-<?= $totalStock > 0 ? 'success' : 'warning' ?>"><?= msme_erp_mat_fmt($totalStock) ?> <?= msme_erp_mat_h($detail['uom_code']) ?></span>
            <?php if ((float)$detail['reorder_point'] > 0): ?><span class="ms-2 text-<?= $totalStock <= (float)$detail['reorder_point'] ? 'danger' : 'muted' ?>">reorder at <?= msme_erp_mat_fmt($detail['reorder_point']) ?></span><?php endif; ?></div>
          <?php if ($stock): ?><table class="table table-sm table-bordered mb-2"><thead class="table-light"><tr><th>Warehouse</th><th class="text-end">On hand</th><th class="text-end">Avg cost</th></tr></thead><tbody>
            <?php foreach ($stock as $sb): ?><tr><td><?= msme_erp_mat_h($sb['wh_code'] . ' — ' . $sb['wh_name']) ?></td><td class="text-end fw-bold"><?= msme_erp_mat_fmt($sb['qty_on_hand']) ?></td><td class="text-end"><?= number_format((float)$sb['avg_cost'], 2) ?></td></tr><?php endforeach; ?></tbody></table>
          <?php else: ?><div class="text-muted mb-2">No stock posted yet.</div><?php endif; ?>
          <div class="d-grid gap-1"><a class="btn btn-sm btn-outline-primary" href="<?= esc_url(msme_erp_mat_url('MMBE', ['item_id' => $itemId])) ?>"><i class="bi bi-boxes me-1"></i>Stock overview (MMBE)</a>
            <a class="btn btn-sm btn-outline-secondary" href="<?= esc_url(msme_erp_mat_url('MB5L', ['item_id' => $itemId])) ?>"><i class="bi bi-journal-arrow-up me-1"></i>Stock ledger (MB5L)</a></div>
        </div>

        <div class="tab-pane fade" id="mt-p">
          <?php if ($pos): ?><table class="table table-sm mb-2"><thead class="table-light"><tr><th>PO</th><th>Date</th><th>Supplier</th><th class="text-end">Qty</th><th class="text-end">Price</th></tr></thead><tbody>
            <?php foreach ($pos as $po): ?><tr><td><a href="<?= esc_url(msme_erp_mat_url('ME23N', ['id' => $po['id']])) ?>"><?= msme_erp_mat_h($po['po_number']) ?></a></td><td><?= msme_erp_mat_h($po['po_date']) ?></td><td><?= msme_erp_mat_h(mb_substr((string)$po['supplier'], 0, 16)) ?></td><td class="text-end"><?= msme_erp_mat_fmt($po['qty_ordered']) ?></td><td class="text-end"><?= number_format((float)$po['unit_price'], 2) ?></td></tr><?php endforeach; ?></tbody></table>
          <?php else: ?><div class="text-muted mb-2">Never purchased.</div><?php endif; ?>
          <?php if ($blocked !== 'PROCUREMENT' && $blocked !== 'ALL'): ?><a class="btn btn-sm btn-outline-warning" href="<?= esc_url(msme_erp_mat_url('ME21N', ['item_id' => $itemId])) ?>"><i class="bi bi-bag-plus me-1"></i>Create purchase order</a><?php endif; ?>
        </div>

        <div class="tab-pane fade" id="mt-w">
          <div class="d-flex flex-wrap gap-1 mb-2"><?php foreach (['pr' => 'PR lines', 'po' => 'PO lines', 'grn' => 'GRN lines', 'so' => 'Sales lines', 'bom' => 'BOM links', 'moves' => 'Stock moves'] as $k => $l): ?><span class="badge bg-light text-dark border"><?= $l ?>: <b><?= (int)$usage[$k] ?></b></span><?php endforeach; ?></div>
          <?php if ($boms): ?><div class="fw-semibold mt-2">Used in BOMs</div><ul class="mb-1 ps-3"><?php foreach ($boms as $b): ?><li><?= msme_erp_mat_h($b['bom_number']) ?> → <?= msme_erp_mat_h($b['item_code'] . ' ' . $b['item_name']) ?></li><?php endforeach; ?></ul><?php endif; ?>
          <?php if ($openPR): ?><div class="fw-semibold mt-2">Open purchase requisitions</div><ul class="mb-1 ps-3"><?php foreach ($openPR as $r): ?><li><?= msme_erp_mat_h($r['pr_number']) ?> · <?= msme_erp_mat_fmt($r['qty_requested']) ?></li><?php endforeach; ?></ul><?php endif; ?>
          <?php if ($openPO): ?><div class="fw-semibold mt-2">Open purchase orders (to receive)</div><ul class="mb-1 ps-3"><?php foreach ($openPO as $r): ?><li><a href="<?= esc_url(msme_erp_mat_url('ME23N', ['id' => $r['id']])) ?>"><?= msme_erp_mat_h($r['po_number']) ?></a> · <?= msme_erp_mat_fmt($r['open_qty']) ?></li><?php endforeach; ?></ul><?php endif; ?>
          <?php if ($openSO): ?><div class="fw-semibold mt-2">Open sales orders (to deliver)</div><ul class="mb-1 ps-3"><?php foreach ($openSO as $r): ?><li><?= msme_erp_mat_h($r['so_number']) ?> · <?= msme_erp_mat_fmt($r['open_qty']) ?></li><?php endforeach; ?></ul><?php endif; ?>
          <?php if (!$boms && !$openPR && !$openPO && !$openSO): ?><div class="text-muted">No open documents.</div><?php endif; ?>
        </div>

        <div class="tab-pane fade" id="mt-u">
          <?php if ($alts): ?><ul class="ps-3 mb-1"><?php foreach ($alts as $a): ?><li>1 <b><?= msme_erp_mat_h($a['code']) ?></b> = <?= msme_erp_mat_fmt($a['factor'], 6) ?> <?= msme_erp_mat_h($detail['uom_code']) ?></li><?php endforeach; ?></ul>
          <?php else: ?><div class="text-muted">Only the base unit <b><?= msme_erp_mat_h($detail['uom_code']) ?></b>. Add alternates on MM02.</div><?php endif; ?></div>

        <div class="tab-pane fade" id="mt-h">
          <?php if (!$hist): ?><div class="text-muted">No changes recorded.</div><?php endif; ?>
          <?php foreach ($hist as $h): $o = json_decode((string)$h['old_data'], true) ?: []; $n = json_decode((string)$h['new_data'], true) ?: []; ?>
            <div class="border-bottom py-1"><span class="text-muted"><?= msme_erp_mat_h($h['created_at']) ?></span> · <b><?= msme_erp_mat_h($h['username']) ?></b> · <span class="badge bg-light text-dark border"><?= msme_erp_mat_h($h['action']) ?></span>
              <?php foreach ($n as $f => $nv): ?><div class="ps-2"><code><?= msme_erp_mat_h($f) ?></code>: <?= array_key_exists($f, $o) ? '<s class="text-muted">' . msme_erp_mat_h(is_scalar($o[$f]) ? $o[$f] : wp_json_encode($o[$f])) . '</s> → ' : '' ?><b><?= msme_erp_mat_h(is_scalar($nv) ? $nv : wp_json_encode($nv)) ?></b></div><?php endforeach; ?></div>
          <?php endforeach; ?>
        </div>
      </div>
    </div>
  </div></div>
<?php endif; ?>
</div>
<?php
return;
