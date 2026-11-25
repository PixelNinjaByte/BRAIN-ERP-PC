<?php
/**
 * MSME ERP — Material Import / Export (ZMMI)
 * v1.17.1. CSV export of the material master and a two-step (preview → confirm) CSV import.
 * Every row goes through the same validation as MM01/MM02 (msme_erp_mat_clean) — nothing bypasses the rules.
 * Developed by: Prigag Online Pvt. Ltd.
 */
defined('ABSPATH') || exit;
Auth::requireTcode('ZMMI');

$companyId = Auth::companyId();
$cols = msme_erp_mat_csv_columns();
$error = ''; $preview = null; $done = null;

// ---------- export / template (stream and exit before any output) ----------
if (($_GET['export'] ?? '') === 'csv') {
    $w = ["i.company_id=?", "i.is_deleted=0"]; $p = [$companyId];
    $cat = sanitize_text_field($_GET['cat'] ?? ''); $type = sanitize_text_field($_GET['type'] ?? ''); $q = sanitize_text_field($_GET['search'] ?? ''); $st = sanitize_key($_GET['st'] ?? 'all');
    if ($cat)  { $w[] = "i.item_category=?"; $p[] = $cat; }
    if ($type) { $w[] = "i.item_type=?"; $p[] = $type; }
    if ($q)    { $l = '%' . $q . '%'; $w[] = "(i.item_code LIKE ? OR i.item_name LIKE ? OR i.hsn_code LIKE ?)"; array_push($p, $l, $l, $l); }
    if ($st === 'active') $w[] = "i.is_active=1"; elseif ($st === 'inactive') $w[] = "i.is_active=0"; elseif ($st === 'blocked') $w[] = "COALESCE(i.block_status,'')<>''";
    $data = DB::fetchAll("SELECT i.*, u.code AS uom_code, g.code AS group_code FROM items i LEFT JOIN units_of_measure u ON u.id=i.base_uom_id
                           LEFT JOIN material_groups g ON g.id=i.material_group_id WHERE " . implode(' AND ', $w) . " ORDER BY i.item_code", $p);
    $rows = [];
    foreach ($data as $r) { $o = []; foreach ($cols as $c) $o[] = $r[$c] ?? ''; $rows[] = $o; }
    msme_erp_fin_csv_export('materials_' . date('Ymd_His') . '.csv', $cols, $rows);
}
if (($_GET['template'] ?? '') === '1') {
    msme_erp_fin_csv_export('material_import_template.csv', $cols, [
        ['RAW-STEEL-001', 'MS Plate 10mm', 'IS 2062 E250', 'MATERIAL', 'RAW', 'KG', '', '7208', '18', '62.5', '100', '1000', '250', '7', '1', '0', '0', '0', '0', '', '', '1'],
    ]);
}

// ---------- CSV parsing ----------
function msme_erp_mmi_parse(string $path): array {
    $fh = fopen($path, 'r'); if (!$fh) throw new Exception('Could not read the uploaded file.');
    $first = fgets($fh); rewind($fh);
    if ($first === false) throw new Exception('The file is empty.');
    $first = preg_replace('/^\xEF\xBB\xBF/', '', $first);
    $delim = ','; foreach ([';', "\t"] as $d) if (substr_count($first, $d) > substr_count($first, $delim)) $delim = $d;
    $hdr = fgetcsv($fh, 0, $delim); if (!$hdr) throw new Exception('No header row found.');
    $hdr[0] = preg_replace('/^\xEF\xBB\xBF/', '', (string)$hdr[0]);
    $hdr = array_map(static fn($h) => strtolower(trim((string)$h)), $hdr);
    $known = msme_erp_mat_csv_columns();
    if (!in_array('item_name', $hdr, true)) throw new Exception('The header row must contain at least item_name (download the template).');
    $rows = []; $n = 0;
    while (($r = fgetcsv($fh, 0, $delim)) !== false) {
        if (count($r) === 1 && trim((string)$r[0]) === '') continue;
        if (++$n > 5000) throw new Exception('Maximum 5000 rows per import.');
        $row = [];
        foreach ($hdr as $i => $h) if (in_array($h, $known, true)) $row[$h] = trim((string)($r[$i] ?? ''));
        $rows[$n + 1] = $row;   // key = spreadsheet line number
    }
    fclose($fh);
    return $rows;
}

/** Validates one CSV row. Returns [status NEW|UPDATE|SKIP|ERROR, cleanData, errors[], existingId]. */
function msme_erp_mmi_check(array $row, int $companyId, bool $update, array &$seen): array {
    $code = strtoupper(trim((string)($row['item_code'] ?? '')));
    $ex = $code !== '' ? DB::fetchOne("SELECT * FROM items WHERE company_id=? AND item_code=? AND is_deleted=0", [$companyId, $code]) : null;
    if ($code !== '') { if (isset($seen[$code])) return ['ERROR', [], ["Code {$code} appears twice in the file."], 0]; $seen[$code] = true; }
    if ($ex && !$update) return ['SKIP', [], ['Already exists (tick "update existing" to change it).'], (int)$ex['id']];

    if ($ex) {                                    // blank cells keep the current value
        $in = array_intersect_key($ex, array_flip(msme_erp_mat_csv_columns())) + ['base_uom_id' => $ex['base_uom_id'], 'material_group_id' => $ex['material_group_id']];
        foreach ($row as $k => $v) if ($v !== '') $in[$k] = $v;
        if (($row['uom_code'] ?? '') !== '')   unset($in['base_uom_id']);
        if (($row['group_code'] ?? '') !== '') unset($in['material_group_id']);
        unset($in['uom_code'], $in['group_code']);
        if (($row['uom_code'] ?? '') !== '') $in['uom_code'] = $row['uom_code'];
        if (($row['group_code'] ?? '') !== '') $in['group_code'] = $row['group_code'];
    } else {
        $in = array_filter($row, static fn($v) => $v !== '');
    }
    $in['confirm_dup'] = 1;
    [$d, $errs] = msme_erp_mat_clean($in, $companyId, $ex ? (int)$ex['id'] : null);
    if ($ex && (int)$d['base_uom_id'] !== (int)$ex['base_uom_id']) {
        $u = msme_erp_mat_usage((int)$ex['id'], $companyId);
        if ($u['moves'] > 0) $errs[] = 'Base UOM cannot be changed — stock movements exist.';
    }
    return [$errs ? 'ERROR' : ($ex ? 'UPDATE' : 'NEW'), $d, $errs, $ex ? (int)$ex['id'] : 0];
}

// ---------- POST: preview or commit ----------
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) wp_die('CSRF');
    try {
        if (!empty($_POST['confirm_token'])) {
            $tok = preg_replace('/[^a-f0-9]/', '', (string)$_POST['confirm_token']);
            $st  = get_transient('msme_erp_mmi_' . $tok);
            if (!$st || (int)$st['cid'] !== $companyId || (int)$st['uid'] !== get_current_user_id()) throw new Exception('The preview expired — upload the file again.');
            delete_transient('msme_erp_mmi_' . $tok);
            $seen = []; $c = ['NEW' => 0, 'UPDATE' => 0, 'SKIP' => 0, 'ERROR' => 0]; $msgs = [];
            foreach ($st['rows'] as $line => $row) {
                [$status, $d, $errs, $exId] = msme_erp_mmi_check($row, $companyId, (bool)$st['update'], $seen);
                try {
                    if ($status === 'NEW') {
                        $d['company_id'] = $companyId; $d['created_by'] = Auth::userId(); $d['is_deleted'] = 0;
                        $id = DB::insert('items', $d);
                        Auth::audit('items', $id, 'INSERT', [], ['item_code' => $d['item_code'], 'item_name' => $d['item_name'], 'source' => 'ZMMI']);
                    } elseif ($status === 'UPDATE') {
                        $old = DB::fetchOne("SELECT * FROM items WHERE id=? AND company_id=?", [$exId, $companyId]);
                        if ($d['material_group_id'] === null) $d['material_group_id'] = 0;
                        $old['material_group_id'] = (int)$old['material_group_id'];
                        $diff = msme_erp_mat_diff($old, $d);
                        if ($diff) { DB::update('items', $d, 'id=? AND company_id=?', [$exId, $companyId]); Auth::audit('items', $exId, 'UPDATE', array_map(fn($x) => $x[0], $diff), array_map(fn($x) => $x[1], $diff)); }
                        else $status = 'SKIP';
                    }
                } catch (Throwable $e) { $status = 'ERROR'; $msgs[] = "Line {$line}: " . $e->getMessage(); }
                $c[$status]++;
                if ($status === 'ERROR' && $errs) $msgs[] = "Line {$line}: " . implode(' ', $errs);
            }
            $done = ['counts' => $c, 'msgs' => array_slice($msgs, 0, 30)];
        } else {
            if (empty($_FILES['csv']['tmp_name']) || !is_uploaded_file($_FILES['csv']['tmp_name'])) throw new Exception('Choose a CSV file.');
            if ($_FILES['csv']['size'] > 2 * 1024 * 1024) throw new Exception('File is larger than 2 MB.');
            if (strtolower(pathinfo((string)$_FILES['csv']['name'], PATHINFO_EXTENSION)) !== 'csv') throw new Exception('Only .csv files are accepted (Excel: Save As → CSV UTF-8).');
            $rows = msme_erp_mmi_parse($_FILES['csv']['tmp_name']);
            if (!$rows) throw new Exception('No data rows found.');
            $update = !empty($_POST['update_existing']); $seen = []; $lines = [];
            $c = ['NEW' => 0, 'UPDATE' => 0, 'SKIP' => 0, 'ERROR' => 0];
            foreach ($rows as $line => $row) {
                [$status, $d, $errs] = msme_erp_mmi_check($row, $companyId, $update, $seen);
                $c[$status]++;
                if (count($lines) < 300) $lines[] = ['line' => $line, 'status' => $status, 'code' => ($row['item_code'] ?? '') ?: '(auto)', 'name' => $row['item_name'] ?? '', 'errs' => $errs];
            }
            $tok = bin2hex(random_bytes(12));
            set_transient('msme_erp_mmi_' . $tok, ['rows' => $rows, 'update' => $update, 'cid' => $companyId, 'uid' => get_current_user_id()], 1800);
            $preview = ['token' => $tok, 'counts' => $c, 'lines' => $lines, 'total' => count($rows)];
        }
    } catch (Throwable $e) { $error = $e->getMessage(); }
}

$pageTitle   = 'Material Import / Export — ZMMI';
$pageTcode   = 'ZMMI';
$breadcrumbs = ['MM', 'Master Data', 'Import / Export'];
$pageActions = '<a href="' . esc_url(admin_url('admin.php?page=msme-erp&tcode=MM03')) . '" class="btn btn-sm btn-outline-secondary"><i class="bi bi-box-seam me-1"></i>Materials</a>';
erp_render_page_header();
if ($error) erp_alert($error, 'danger');
if ($done): $c = $done['counts']; ?>
  <div class="alert alert-<?= $c['ERROR'] ? 'warning' : 'success' ?>"><b>Import finished.</b> Created <?= $c['NEW'] ?>, updated <?= $c['UPDATE'] ?>, unchanged/skipped <?= $c['SKIP'] ?>, failed <?= $c['ERROR'] ?>.
    <?php if ($done['msgs']): ?><ul class="mb-0 mt-2 small"><?php foreach ($done['msgs'] as $m): ?><li><?= esc_html($m) ?></li><?php endforeach; ?></ul><?php endif; ?></div>
<?php endif; ?>
<div class="row g-3">
  <div class="col-lg-4"><div class="card border-0 shadow-sm mb-3">
    <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-download me-2 text-success"></i>Export</div>
    <div class="card-body p-3 d-grid gap-2">
      <a class="btn btn-success btn-sm" href="<?= esc_url(admin_url('admin.php?page=msme-erp&tcode=ZMMI&export=csv&st=all')) ?>"><i class="bi bi-file-earmark-excel me-1"></i>All materials (CSV)</a>
      <a class="btn btn-outline-success btn-sm" href="<?= esc_url(admin_url('admin.php?page=msme-erp&tcode=ZMMI&export=csv&st=active')) ?>">Active materials only</a>
      <a class="btn btn-outline-secondary btn-sm" href="<?= esc_url(admin_url('admin.php?page=msme-erp&tcode=ZMMI&template=1')) ?>"><i class="bi bi-file-earmark me-1"></i>Blank template with example</a>
      <div class="small text-muted">Exports open in Excel (UTF-8). The same layout is accepted for import.</div>
    </div></div>
    <div class="card border-0 shadow-sm"><div class="card-header bg-white fw-bold small py-2">Columns</div><div class="card-body p-3 small text-muted" style="word-break:break-word"><code><?= esc_html(implode(', ', $cols)) ?></code><hr class="my-2">
      <b>uom_code</b> (e.g. KG) and <b>group_code</b> are looked up by code. Blank <b>item_code</b> = automatic MAT number. Types: MATERIAL, SERVICE, ASSET, TRADING. Categories: RAW, WIP, FG, CONSUMABLE, SPARE, TRADING. <b>block_status</b>: blank, PROCUREMENT, SALES, ALL. HSN 4/6/8 digits. On update, blank cells keep the current value.</div></div>
  </div>
  <div class="col-lg-8"><div class="card border-0 shadow-sm">
    <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-upload me-2 text-primary"></i>Import</div>
    <div class="card-body p-3">
    <?php if ($preview): $c = $preview['counts']; ?>
      <div class="mb-2"><span class="badge bg-success">New <?= $c['NEW'] ?></span> <span class="badge bg-primary">Update <?= $c['UPDATE'] ?></span> <span class="badge bg-secondary">Skip <?= $c['SKIP'] ?></span> <span class="badge bg-danger">Errors <?= $c['ERROR'] ?></span> <span class="text-muted small">of <?= (int)$preview['total'] ?> rows — nothing has been saved yet.</span></div>
      <div class="table-responsive mb-3" style="max-height:340px;overflow:auto"><table class="table table-sm" style="font-size:.78rem"><thead class="table-light"><tr><th>Line</th><th>Status</th><th>Code</th><th>Description</th><th>Problems</th></tr></thead><tbody>
        <?php foreach ($preview['lines'] as $l): $cl = ['NEW' => 'success', 'UPDATE' => 'primary', 'SKIP' => 'secondary', 'ERROR' => 'danger'][$l['status']]; ?>
        <tr><td><?= (int)$l['line'] ?></td><td><span class="badge bg-<?= $cl ?>"><?= $l['status'] ?></span></td><td><?= esc_html($l['code']) ?></td><td><?= esc_html(mb_substr($l['name'], 0, 40)) ?></td><td class="text-danger"><?= esc_html(implode(' ', $l['errs'])) ?></td></tr><?php endforeach; ?></tbody></table></div>
      <?php if ($preview['total'] > 300): ?><div class="small text-muted mb-2">Showing the first 300 rows; the import still processes all rows.</div><?php endif; ?>
      <form method="POST" class="d-flex gap-2"><input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>"><input type="hidden" name="confirm_token" value="<?= esc_attr($preview['token']) ?>">
        <button class="btn btn-primary" <?= ($c['NEW'] + $c['UPDATE']) ? '' : 'disabled' ?>><i class="bi bi-check2-circle me-1"></i>Import <?= $c['NEW'] + $c['UPDATE'] ?> valid row(s)</button>
        <a class="btn btn-outline-secondary" href="<?= esc_url(admin_url('admin.php?page=msme-erp&tcode=ZMMI')) ?>">Cancel</a></form>
      <div class="small text-muted mt-2">Rows with errors are skipped; fix them in the file and import again.</div>
    <?php else: ?>
      <form method="POST" enctype="multipart/form-data" class="row g-3 align-items-end">
        <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
        <div class="col-md-7"><label class="form-label small fw-semibold mb-1">CSV file (max 2 MB, 5000 rows)</label><input type="file" name="csv" accept=".csv,text/csv" class="form-control form-control-sm" required></div>
        <div class="col-md-5"><div class="form-check"><input class="form-check-input" type="checkbox" name="update_existing" value="1" id="ue"><label class="form-check-label small" for="ue">Update materials that already exist (matched by code)</label></div></div>
        <div class="col-12"><button class="btn btn-primary"><i class="bi bi-search me-1"></i>Validate &amp; preview</button></div>
      </form>
    <?php endif; ?>
    </div></div></div>
</div>
