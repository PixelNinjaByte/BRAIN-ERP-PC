<?php
/**
 * MSME ERP — Material Groups (OMSF) with inventory GL account
 * v1.17.1. A group's inventory account is where goods receipts of its materials are debited
 * (company default INVENTORY account when the group has none).
 * Developed by: Prigag Online Pvt. Ltd.
 */
defined('ABSPATH') || exit;
Auth::requireTcode('OMSF');

$companyId = Auth::companyId();
$error = '';
$editId = (int)($_GET['id'] ?? 0);
$url = static fn(array $q = []) => admin_url('admin.php?' . http_build_query(['page' => 'msme-erp', 'tcode' => 'OMSF'] + $q));

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) wp_die('CSRF');
    $in  = wp_unslash($_POST);
    $act = sanitize_key($in['mat_action'] ?? 'save');
    try {
        $gid = (int)($in['group_id'] ?? 0);
        if ($act === 'delete') {
            $g = DB::fetchOne("SELECT * FROM material_groups WHERE id=? AND company_id=?", [$gid, $companyId]);
            if (!$g) throw new Exception('Group not found.');
            if ((int)DB::fetchValue("SELECT COUNT(*) FROM items WHERE material_group_id=? AND is_deleted=0", [$gid])) throw new Exception('This group still has materials — move them first or deactivate the group.');
            DB::query("DELETE FROM material_groups WHERE id=? AND company_id=?", [$gid, $companyId]);
            Auth::audit('material_groups', $gid, 'DELETE', ['code' => $g['code'], 'name' => $g['name']], []);
            msme_erp_set_flash('Group deleted.'); wp_redirect($url()); exit;
        }
        $code = strtoupper(preg_replace('/[^A-Za-z0-9_\-]/', '', (string)($in['code'] ?? '')));
        $name = mb_substr(trim(sanitize_text_field($in['name'] ?? '')), 0, 100);
        if ($code === '' || mb_strlen($code) > 20) throw new Exception('Group code is required (letters, digits, - _ ; max 20).');
        if ($name === '') throw new Exception('Group name is required.');
        $acc = (int)($in['inventory_account_id'] ?? 0);
        if ($acc && !DB::fetchValue("SELECT id FROM chart_of_accounts WHERE id=? AND company_id=? AND COALESCE(is_group,0)=0", [$acc, $companyId])) throw new Exception('Choose a posting account from the chart of accounts.');
        if (DB::fetchValue("SELECT id FROM material_groups WHERE company_id=? AND code=? AND id<>?", [$companyId, $code, $gid])) throw new Exception("Group code '{$code}' already exists.");
        $row = ['code' => $code, 'name' => $name, 'description' => mb_substr(sanitize_text_field($in['description'] ?? ''), 0, 255),
                'inventory_account_id' => $acc, 'is_active' => isset($in['is_active']) ? 1 : 0];
        if ($gid) {
            $old = DB::fetchOne("SELECT code,name,inventory_account_id,is_active FROM material_groups WHERE id=? AND company_id=?", [$gid, $companyId]);
            DB::update('material_groups', $row, 'id=? AND company_id=?', [$gid, $companyId]);
            Auth::audit('material_groups', $gid, 'UPDATE', (array)$old, $row);
            msme_erp_set_flash("Group {$code} updated.");
        } else {
            $row['company_id'] = $companyId;
            $gid = DB::insert('material_groups', $row);
            Auth::audit('material_groups', $gid, 'INSERT', [], $row);
            msme_erp_set_flash("Group {$code} created.");
        }
        wp_redirect($url()); exit;
    } catch (Throwable $e) { $error = $e->getMessage(); if (stripos($error, 'Duplicate entry') !== false) $error = 'That group code already exists.'; }
}

$pageTitle   = 'Material Groups — OMSF';
$pageTcode   = 'OMSF';
$breadcrumbs = ['MM', 'Master Data', 'Material Groups'];
$pageActions = '<a href="' . esc_url(admin_url('admin.php?page=msme-erp&tcode=MM03')) . '" class="btn btn-sm btn-outline-secondary"><i class="bi bi-box-seam me-1"></i>Materials</a>';

$edit   = $editId ? DB::fetchOne("SELECT * FROM material_groups WHERE id=? AND company_id=?", [$editId, $companyId]) : null;
$accts  = DB::fetchAll("SELECT id,account_code,account_name FROM chart_of_accounts WHERE company_id=? AND COALESCE(is_active,1)=1 AND COALESCE(is_group,0)=0 AND account_type='ASSET' ORDER BY account_code", [$companyId]);
$list   = DB::fetchAll("SELECT g.*, a.account_code, a.account_name, (SELECT COUNT(*) FROM items i WHERE i.material_group_id=g.id AND i.is_deleted=0) AS n_items
                          FROM material_groups g LEFT JOIN chart_of_accounts a ON a.id=g.inventory_account_id WHERE g.company_id=? ORDER BY g.code", [$companyId]);
$v = $edit ?: ['id' => 0, 'code' => '', 'name' => '', 'description' => '', 'inventory_account_id' => 0, 'is_active' => 1];
if ($error && $_SERVER['REQUEST_METHOD'] === 'POST') { $p = wp_unslash($_POST); $v = array_merge($v, ['code' => $p['code'] ?? '', 'name' => $p['name'] ?? '', 'description' => $p['description'] ?? '', 'inventory_account_id' => (int)($p['inventory_account_id'] ?? 0), 'is_active' => isset($p['is_active']) ? 1 : 0]); }

erp_render_page_header();
if ($error) erp_alert($error, 'danger');
if (get_option('msme_erp_mat_dup_uq_company_group_code')) echo '<div class="alert alert-warning small">Duplicate group codes exist in one company — rename them so the unique key can be installed.</div>';
?>
<div class="row g-3">
  <div class="col-lg-4"><div class="card border-0 shadow-sm">
    <div class="card-header fw-bold small py-2 text-white" style="background:linear-gradient(135deg,#1565c0,#1976d2)"><i class="bi bi-collection me-2"></i><?= $edit ? 'Change group' : 'New material group' ?></div>
    <div class="card-body p-3"><form method="POST">
      <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>"><input type="hidden" name="mat_action" value="save"><input type="hidden" name="group_id" value="<?= (int)$v['id'] ?>">
      <div class="row g-2 mb-2">
        <div class="col-5"><label class="form-label small fw-semibold mb-1">Code *</label><input name="code" class="form-control form-control-sm text-uppercase fw-bold" maxlength="20" required value="<?= esc_attr($v['code']) ?>"></div>
        <div class="col-7"><label class="form-label small fw-semibold mb-1">Name *</label><input name="name" class="form-control form-control-sm" maxlength="100" required value="<?= esc_attr($v['name']) ?>"></div>
      </div>
      <div class="mb-2"><label class="form-label small fw-semibold mb-1">Description</label><input name="description" class="form-control form-control-sm" maxlength="255" value="<?= esc_attr($v['description'] ?? '') ?>"></div>
      <div class="mb-2"><label class="form-label small fw-semibold mb-1">Inventory GL account</label>
        <select name="inventory_account_id" class="form-select form-select-sm"><option value="0">— Company default (OBYC: INVENTORY) —</option>
          <?php foreach ($accts as $a): ?><option value="<?= (int)$a['id'] ?>" <?= (int)$v['inventory_account_id'] === (int)$a['id'] ? 'selected' : '' ?>><?= esc_html($a['account_code'] . ' — ' . $a['account_name']) ?></option><?php endforeach; ?></select>
        <div class="form-text small">Goods receipts of this group's materials are debited here (asset accounts only). Create more inventory accounts in FS00 (e.g. Finished Goods, Spares).</div></div>
      <div class="form-check mb-3"><input class="form-check-input" type="checkbox" name="is_active" value="1" id="ga" <?= $v['is_active'] ? 'checked' : '' ?>><label class="form-check-label small fw-semibold" for="ga">Active</label></div>
      <div class="d-grid gap-2"><button class="btn btn-primary fw-semibold"><i class="bi bi-save me-1"></i><?= $edit ? 'Save' : 'Create group' ?></button>
        <?php if ($edit): ?><a class="btn btn-outline-secondary btn-sm" href="<?= esc_url($url()) ?>">New group</a><?php endif; ?></div>
    </form></div></div></div>
  <div class="col-lg-8"><div class="card border-0 shadow-sm"><div class="card-header bg-white fw-bold small py-2">Groups <span class="badge bg-secondary"><?= count($list) ?></span></div>
    <div class="table-responsive"><table class="table table-hover table-sm mb-0" style="font-size:.82rem"><thead class="table-light"><tr><th class="px-3">Code</th><th>Name</th><th>Inventory GL</th><th class="text-center">Materials</th><th class="text-center">Active</th><th></th></tr></thead><tbody>
      <?php if (!$list): ?><tr><td colspan="6" class="text-center text-muted py-4">No groups yet.</td></tr><?php endif; ?>
      <?php foreach ($list as $g): ?><tr class="<?= $editId === (int)$g['id'] ? 'table-primary' : '' ?>">
        <td class="px-3 fw-bold"><?= esc_html($g['code']) ?></td><td><?= esc_html($g['name']) ?></td>
        <td><?= $g['account_code'] ? esc_html($g['account_code'] . ' — ' . $g['account_name']) : '<span class="text-muted">default</span>' ?></td>
        <td class="text-center"><?= (int)$g['n_items'] ?></td><td class="text-center"><?= $g['is_active'] ? '<i class="bi bi-check-circle-fill text-success"></i>' : '<i class="bi bi-x-circle text-danger"></i>' ?></td>
        <td class="text-end text-nowrap"><a class="btn btn-sm btn-outline-primary py-0" href="<?= esc_url($url(['id' => $g['id']])) ?>"><i class="bi bi-pencil"></i></a>
          <?php if (!(int)$g['n_items']): ?><form method="POST" class="d-inline" onsubmit="return confirm('Delete this group?')"><input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>"><input type="hidden" name="mat_action" value="delete"><input type="hidden" name="group_id" value="<?= (int)$g['id'] ?>"><button class="btn btn-sm btn-outline-danger py-0"><i class="bi bi-trash"></i></button></form><?php endif; ?></td></tr><?php endforeach; ?>
    </tbody></table></div></div></div>
</div>
