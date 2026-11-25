<?php
/**
 * MSME ERP — Supplier Master (XK01/XK02) & Customer (XD01/XD02)  (Material Master: see material_master.php)
 * Full professional implementation with tabs, search, live preview
 * Developed by: Prigag Online Pvt. Ltd. | www.prigag.com
 */

$tcode     = strtoupper($_GET['tcode'] ?? 'MM01');
if (in_array($tcode, ['MM01', 'MM02', 'MM03'], true)) { require_once __DIR__ . '/material_master.php'; return; }
$companyId = Auth::companyId();
$error     = '';
$success   = '';

Auth::requireTcode($tcode);

// ── Indian States helper ──────────────────────────────────────
$IN_STATES = ['Andhra Pradesh','Arunachal Pradesh','Assam','Bihar','Chhattisgarh','Goa',
    'Gujarat','Haryana','Himachal Pradesh','Jharkhand','Karnataka','Kerala',
    'Madhya Pradesh','Maharashtra','Manipur','Meghalaya','Mizoram','Nagaland',
    'Odisha','Punjab','Rajasthan','Sikkim','Tamil Nadu','Telangana','Tripura',
    'Uttar Pradesh','Uttarakhand','West Bengal','Delhi','Jammu & Kashmir','Ladakh',
    'Puducherry','Chandigarh','Andaman & Nicobar'];

// ══════════════════════════════════════════════════════════════════════
// XK01 / XK02 — Create / Edit Supplier
// ══════════════════════════════════════════════════════════════════════
if (in_array($tcode, ['XK01','XK02'])) {
    $suppId = (int)($_GET['id'] ?? 0);
    $pageTitle   = $suppId ? 'Edit Supplier — XK02' : 'Create Supplier — XK01';
    $pageTcode   = $tcode;
    $breadcrumbs = ['MM','Purchasing', $suppId ? 'Edit Supplier' : 'Create Supplier'];
    $pageActions = '<a href="'.admin_url('admin.php?page=msme-erp&tcode=XK01').'" class="btn btn-sm btn-warning text-dark"><i class="bi bi-plus-circle me-1"></i>New Supplier</a>
        <a href="'.admin_url('admin.php?page=msme-erp&tcode=ME21N').'" class="btn btn-sm btn-outline-primary"><i class="bi bi-bag-plus me-1"></i>Create PO</a>';

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) wp_die('CSRF');
        $d = $_POST; unset($d['_csrf'],$d['action']);
        foreach ($d as $k => $v) $d[$k] = sanitize_text_field($v);
        try {
            if ($suppId) {
                unset($d['supplier_code']);
                DB::update('suppliers', $d, 'id=? AND company_id=?', [$suppId, $companyId]);
                msme_erp_set_flash("Supplier updated successfully.", 'success');
                wp_redirect(admin_url("admin.php?page=msme-erp&tcode=XK01&id={$suppId}")); exit;
            } else {
                if (empty($d['supplier_code'])) {
                    $cnt = (int)DB::fetchValue("SELECT COUNT(*) FROM suppliers WHERE company_id=?", [$companyId]);
                    $d['supplier_code'] = 'SUP'.str_pad($cnt+1,5,'0',STR_PAD_LEFT);
                }
                $d['supplier_code'] = strtoupper($d['supplier_code']);
                if (DB::fetchValue("SELECT COUNT(*) FROM suppliers WHERE company_id=? AND supplier_code=?", [$companyId,$d['supplier_code']])) {
                    throw new Exception("Supplier code '{$d['supplier_code']}' already exists.");
                }
                $d['company_id']=$companyId; $d['status']='ACTIVE'; $d['created_by']=Auth::userId();
                $newId = DB::insert('suppliers', $d);
                msme_erp_set_flash("Supplier '{$d['name']}' ({$d['supplier_code']}) created.", 'success');
                wp_redirect(admin_url("admin.php?page=msme-erp&tcode=XK01&id={$newId}")); exit;
            }
        } catch (Exception $e) { $error = $e->getMessage(); }
    }

    $edit  = $suppId ? DB::fetchOne("SELECT * FROM suppliers WHERE id=? AND company_id=?", [$suppId,$companyId]) : null;
    $supps = DB::fetchAll("SELECT id,supplier_code,name,phone,email,city,gstin,credit_days,status FROM suppliers WHERE company_id=? AND is_deleted=0 ORDER BY supplier_code", [$companyId]);

    erp_render_page_header();
    if ($error) erp_alert($error, 'danger');
    ?>
    <div class="row g-3">
      <div class="col-xl-5 col-lg-6">
        <div class="card border-0 shadow-sm">
          <div class="card-header fw-bold small py-2 text-white" style="background:linear-gradient(135deg,#e65100,#f57c00)">
            <i class="bi bi-truck me-2"></i><?= $edit?'Edit':'New'?> Supplier
            <?php if($edit): ?><span class="badge bg-light text-dark ms-2"><?= esc_html($edit['supplier_code']) ?></span><?php endif; ?>
          </div>
          <div class="card-body p-3">
            <form method="POST">
              <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">

              <div class="small fw-bold text-primary border-bottom pb-1 mb-2"><i class="bi bi-person-vcard me-1"></i>Basic Information</div>
              <div class="row g-2 mb-2">
                <div class="col-5">
                  <label class="form-label fw-semibold small mb-1">Supplier Code</label>
                  <input type="text" name="supplier_code" class="form-control form-control-sm text-uppercase fw-bold"
                    value="<?= esc_attr($edit['supplier_code']??'') ?>" placeholder="Auto" <?= $edit?'readonly':'' ?>>
                </div>
                <div class="col-7">
                  <label class="form-label fw-semibold small mb-1">Company / Name <span class="text-danger">*</span></label>
                  <input type="text" name="name" class="form-control form-control-sm" required
                    value="<?= esc_attr($edit['name']??'') ?>">
                </div>
              </div>
              <div class="row g-2 mb-2">
                <div class="col-6">
                  <label class="form-label fw-semibold small mb-1">Contact Person</label>
                  <input type="text" name="contact_person" class="form-control form-control-sm" value="<?= esc_attr($edit['contact_person']??'') ?>">
                </div>
                <div class="col-6">
                  <label class="form-label fw-semibold small mb-1">Phone</label>
                  <input type="tel" name="phone" class="form-control form-control-sm" value="<?= esc_attr($edit['phone']??'') ?>">
                </div>
              </div>
              <div class="row g-2 mb-3">
                <div class="col-6">
                  <label class="form-label fw-semibold small mb-1">Email</label>
                  <input type="email" name="email" class="form-control form-control-sm" value="<?= esc_attr($edit['email']??'') ?>">
                </div>
                <div class="col-6">
                  <label class="form-label fw-semibold small mb-1">Supplier Type</label>
                  <select name="supplier_type" class="form-select form-select-sm">
                    <?php foreach(['DOMESTIC','IMPORT','SERVICE','SUBCONTRACT'] as $t): ?>
                    <option <?= ($edit['supplier_type']??'DOMESTIC')===$t?'selected':'' ?>><?= $t ?></option>
                    <?php endforeach; ?>
                  </select>
                </div>
              </div>

              <div class="small fw-bold text-primary border-bottom pb-1 mb-2"><i class="bi bi-geo-alt me-1"></i>Address</div>
              <div class="mb-2">
                <label class="form-label fw-semibold small mb-1">Street Address</label>
                <input type="text" name="address" class="form-control form-control-sm" value="<?= esc_attr($edit['address']??'') ?>">
              </div>
              <div class="row g-2 mb-3">
                <div class="col-5">
                  <label class="form-label fw-semibold small mb-1">City</label>
                  <input type="text" name="city" class="form-control form-control-sm" value="<?= esc_attr($edit['city']??'') ?>">
                </div>
                <div class="col-4">
                  <label class="form-label fw-semibold small mb-1">State</label>
                  <select name="state" class="form-select form-select-sm">
                    <option value="">—</option>
                    <?php foreach($IN_STATES as $s): ?>
                    <option <?= ($edit['state']??'')===$s?'selected':'' ?>><?= $s ?></option>
                    <?php endforeach; ?>
                  </select>
                </div>
                <div class="col-3">
                  <label class="form-label fw-semibold small mb-1">Pincode</label>
                  <input type="text" name="pincode" class="form-control form-control-sm" value="<?= esc_attr($edit['pincode']??'') ?>" maxlength="6">
                </div>
              </div>

              <div class="small fw-bold text-primary border-bottom pb-1 mb-2"><i class="bi bi-receipt-cutoff me-1"></i>Tax & Compliance</div>
              <div class="row g-2 mb-2">
                <div class="col-7">
                  <label class="form-label fw-semibold small mb-1">GSTIN</label>
                  <input type="text" name="gstin" class="form-control form-control-sm text-uppercase" maxlength="15"
                    placeholder="22AAAAA0000A1Z5" value="<?= esc_attr($edit['gstin']??'') ?>">
                </div>
                <div class="col-5">
                  <label class="form-label fw-semibold small mb-1">PAN</label>
                  <input type="text" name="pan_number" class="form-control form-control-sm text-uppercase" maxlength="10"
                    placeholder="ABCDE1234F" value="<?= esc_attr($edit['pan_number']??'') ?>">
                </div>
              </div>
              <div class="row g-2 mb-3">
                <div class="col-6">
                  <label class="form-label fw-semibold small mb-1">Payment Terms</label>
                  <select name="payment_terms" class="form-select form-select-sm">
                    <?php foreach(['Net 30','Net 45','Net 60','Net 90','Advance','Immediate'] as $t): ?>
                    <option <?= ($edit['payment_terms']??'Net 30')===$t?'selected':'' ?>><?= $t ?></option>
                    <?php endforeach; ?>
                  </select>
                </div>
                <div class="col-3">
                  <label class="form-label fw-semibold small mb-1">Credit Days</label>
                  <input type="number" name="credit_days" class="form-control form-control-sm" value="<?= esc_attr($edit['credit_days']??30) ?>" min="0">
                </div>
                <div class="col-3">
                  <?php if($edit): ?>
                  <label class="form-label fw-semibold small mb-1">Status</label>
                  <select name="status" class="form-select form-select-sm">
                    <option value="ACTIVE"   <?= ($edit['status']??'')==='ACTIVE'?'selected':'' ?>>Active</option>
                    <option value="INACTIVE" <?= ($edit['status']??'')==='INACTIVE'?'selected':'' ?>>Inactive</option>
                    <option value="BLOCKED"  <?= ($edit['status']??'')==='BLOCKED'?'selected':'' ?>>Blocked</option>
                  </select>
                  <?php endif; ?>
                </div>
              </div>

              <div class="small fw-bold text-primary border-bottom pb-1 mb-2"><i class="bi bi-bank me-1"></i>Bank Details</div>
              <div class="row g-2 mb-3">
                <div class="col-12">
                  <label class="form-label fw-semibold small mb-1">Bank Name</label>
                  <input type="text" name="bank_name" class="form-control form-control-sm" value="<?= esc_attr($edit['bank_name']??'') ?>" placeholder="HDFC / SBI / ICICI">
                </div>
                <div class="col-7">
                  <label class="form-label fw-semibold small mb-1">Account Number</label>
                  <input type="text" name="bank_account" class="form-control form-control-sm" value="<?= esc_attr($edit['bank_account']??'') ?>">
                </div>
                <div class="col-5">
                  <label class="form-label fw-semibold small mb-1">IFSC Code</label>
                  <input type="text" name="bank_ifsc" class="form-control form-control-sm text-uppercase" value="<?= esc_attr($edit['bank_ifsc']??'') ?>" placeholder="HDFC0001234">
                </div>
              </div>

              <div class="d-grid gap-2">
                <button type="submit" class="btn btn-warning text-dark fw-semibold">
                  <i class="bi bi-save me-1"></i><?= $edit?'Update Supplier':'Create Supplier' ?>
                </button>
                <?php if($edit): ?>
                <a href="<?= esc_url(admin_url('admin.php?page=msme-erp&tcode=XK01')) ?>" class="btn btn-outline-secondary btn-sm">
                  <i class="bi bi-plus me-1"></i>New Supplier
                </a>
                <?php endif; ?>
              </div>
            </form>
          </div>
        </div>
      </div>

      <!-- Supplier List -->
      <div class="col-xl-7 col-lg-6">
        <div class="card border-0 shadow-sm">
          <div class="card-header bg-white d-flex justify-content-between align-items-center py-2">
            <span class="fw-bold small"><i class="bi bi-list-ul me-2"></i>All Suppliers <span class="badge bg-secondary"><?= count($supps) ?></span></span>
            <input type="text" id="supp-search" class="form-control form-control-sm" style="max-width:200px" placeholder="Search...">
          </div>
          <div class="card-body p-0" style="max-height:calc(100vh - 210px);overflow-y:auto">
            <table class="table table-hover table-sm mb-0" style="font-size:.8rem">
              <thead style="position:sticky;top:0;background:#e65100;color:#fff">
                <tr>
                  <th class="px-3 fw-semibold">Code</th>
                  <th class="fw-semibold">Name</th>
                  <th class="fw-semibold">City</th>
                  <th class="fw-semibold">GSTIN</th>
                  <th class="fw-semibold text-center">Credit</th>
                  <th class="fw-semibold text-center">Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody id="supp-tbody">
                <?php foreach($supps as $s):
                  $sc=['ACTIVE'=>'success','INACTIVE'=>'secondary','BLOCKED'=>'danger'];
                  $isEd=$suppId && $suppId==$s['id'];
                ?>
                <tr class="supp-row <?= $isEd?'table-warning':'' ?>">
                  <td class="px-3 fw-bold">
                    <a href="<?= esc_url(admin_url("admin.php?page=msme-erp&tcode=XK02&id={$s['id']}")) ?>" class="text-decoration-none text-primary"><?= esc_html($s['supplier_code']) ?></a>
                  </td>
                  <td>
                    <div class="fw-semibold"><?= esc_html(substr($s['name'],0,22)) ?></div>
                    <?php if($s['phone']): ?><div class="text-muted" style="font-size:.7rem"><?= esc_html($s['phone']) ?></div><?php endif; ?>
                  </td>
                  <td class="text-muted"><?= esc_html($s['city']??'—') ?></td>
                  <td style="font-size:.7rem;font-family:monospace"><?= esc_html($s['gstin']??'—') ?></td>
                  <td class="text-center small"><?= $s['credit_days'] ?>d</td>
                  <td class="text-center"><span class="badge bg-<?= $sc[$s['status']]??'secondary' ?> small"><?= $s['status'] ?></span></td>
                  <td>
                    <a href="<?= esc_url(admin_url("admin.php?page=msme-erp&tcode=XK02&id={$s['id']}")) ?>" class="btn btn-xs btn-outline-primary py-0 px-1"><i class="bi bi-pencil"></i></a>
                    <a href="<?= esc_url(admin_url("admin.php?page=msme-erp&tcode=ME21N&supplier_id={$s['id']}")) ?>" class="btn btn-xs btn-outline-success py-0 px-1 ms-1" title="Create PO"><i class="bi bi-bag-plus"></i></a>
                  </td>
                </tr>
                <?php endforeach; ?>
                <?php if(!$supps): ?>
                <tr><td colspan="7" class="text-center text-muted py-5">
                  <i class="bi bi-truck display-6 d-block mb-2 opacity-25"></i>No suppliers yet.
                </td></tr>
                <?php endif; ?>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
    <script>
    document.getElementById('supp-search')?.addEventListener('input', function(){
        var q=this.value.toLowerCase();
        document.querySelectorAll('.supp-row').forEach(function(r){
            r.style.display=r.textContent.toLowerCase().includes(q)?'':'none';
        });
    });
    </script>
    <?php
    return;
}

// MM01 / MM02 / MM03 (Material Master) moved to modules/mm/material_master.php in v1.17.1

// ══════════════════════════════════════════════════════════════════════
// XD01 / XD02 — Customer Master
// ══════════════════════════════════════════════════════════════════════
if (in_array($tcode, ['XD01','XD02'])) {
    $custId = (int)($_GET['id'] ?? 0);
    $pageTitle   = $custId ? 'Edit Customer — XD02' : 'Create Customer — XD01';
    $pageTcode   = $tcode;
    $breadcrumbs = ['SD','Sales','Customer Master'];
    $pageActions = '<a href="'.admin_url('admin.php?page=msme-erp&tcode=XD01').'" class="btn btn-sm btn-success"><i class="bi bi-person-plus me-1"></i>New Customer</a>';

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) wp_die('CSRF');
        $d = $_POST; unset($d['_csrf'],$d['action']);
        foreach ($d as $k => $v) $d[$k] = sanitize_text_field($v);
        try {
            if ($custId) {
                unset($d['customer_code']);
                DB::update('customers', $d, 'id=? AND company_id=?', [$custId, $companyId]);
                msme_erp_set_flash("Customer updated.", 'success');
                wp_redirect(admin_url("admin.php?page=msme-erp&tcode=XD01&id={$custId}")); exit;
            } else {
                if (empty($d['customer_code'])) {
                    $cnt = (int)DB::fetchValue("SELECT COUNT(*) FROM customers WHERE company_id=?", [$companyId]);
                    $d['customer_code'] = 'CUST'.str_pad($cnt+1,5,'0',STR_PAD_LEFT);
                }
                $d['customer_code'] = strtoupper($d['customer_code']);
                $d['company_id']=$companyId; $d['status']='ACTIVE'; $d['created_by']=Auth::userId();
                $d['credit_limit']=(float)($d['credit_limit']??0);
                $d['credit_days']=(int)($d['credit_days']??30);
                $d['discount_pct']=(float)($d['discount_pct']??0);
                $newId=DB::insert('customers',$d);
                msme_erp_set_flash("Customer '{$d['name']}' ({$d['customer_code']}) created.", 'success');
                wp_redirect(admin_url("admin.php?page=msme-erp&tcode=XD01&id={$newId}")); exit;
            }
        } catch (Exception $e) { $error=$e->getMessage(); }
    }

    $edit  = $custId ? DB::fetchOne("SELECT * FROM customers WHERE id=? AND company_id=?", [$custId,$companyId]) : null;
    $custs = DB::fetchAll("SELECT id,customer_code,name,phone,city,gstin,credit_limit,status FROM customers WHERE company_id=? AND is_deleted=0 ORDER BY customer_code",[$companyId]);

    erp_render_page_header();
    if ($error) erp_alert($error, 'danger');
    ?>
    <div class="row g-3">
      <div class="col-xl-5 col-lg-6">
        <div class="card border-0 shadow-sm">
          <div class="card-header fw-bold small py-2 text-white" style="background:linear-gradient(135deg,#2e7d32,#388e3c)">
            <i class="bi bi-person-lines-fill me-2"></i><?= $edit?'Edit':'New'?> Customer
            <?php if($edit): ?><span class="badge bg-light text-dark ms-2"><?= esc_html($edit['customer_code']) ?></span><?php endif; ?>
          </div>
          <div class="card-body p-3">
            <form method="POST">
              <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
              <div class="small fw-bold text-success border-bottom pb-1 mb-2"><i class="bi bi-person-vcard me-1"></i>Basic Information</div>
              <div class="row g-2 mb-2">
                <div class="col-5">
                  <label class="form-label fw-semibold small mb-1">Customer Code</label>
                  <input type="text" name="customer_code" class="form-control form-control-sm fw-bold text-uppercase"
                    value="<?= esc_attr($edit['customer_code']??'') ?>" placeholder="Auto" <?= $edit?'readonly':'' ?>>
                </div>
                <div class="col-7">
                  <label class="form-label fw-semibold small mb-1">Name <span class="text-danger">*</span></label>
                  <input type="text" name="name" class="form-control form-control-sm" required value="<?= esc_attr($edit['name']??'') ?>">
                </div>
              </div>
              <div class="row g-2 mb-2">
                <div class="col-6"><label class="form-label fw-semibold small mb-1">Contact Person</label><input type="text" name="contact_person" class="form-control form-control-sm" value="<?= esc_attr($edit['contact_person']??'') ?>"></div>
                <div class="col-6"><label class="form-label fw-semibold small mb-1">Phone</label><input type="tel" name="phone" class="form-control form-control-sm" value="<?= esc_attr($edit['phone']??'') ?>"></div>
              </div>
              <div class="mb-2"><label class="form-label fw-semibold small mb-1">Email</label><input type="email" name="email" class="form-control form-control-sm" value="<?= esc_attr($edit['email']??'') ?>"></div>

              <div class="small fw-bold text-success border-bottom pb-1 mb-2 mt-2"><i class="bi bi-geo-alt me-1"></i>Address</div>
              <div class="mb-2"><label class="form-label fw-semibold small mb-1">Billing Address</label><textarea name="billing_address" class="form-control form-control-sm" rows="2"><?= esc_textarea($edit['billing_address']??'') ?></textarea></div>
              <div class="row g-2 mb-3">
                <div class="col-5"><label class="form-label fw-semibold small mb-1">City</label><input type="text" name="city" class="form-control form-control-sm" value="<?= esc_attr($edit['city']??'') ?>"></div>
                <div class="col-4"><label class="form-label fw-semibold small mb-1">State</label>
                  <select name="state" class="form-select form-select-sm">
                    <option value="">—</option>
                    <?php foreach($IN_STATES as $s): ?><option <?= ($edit['state']??'')===$s?'selected':'' ?>><?= $s ?></option><?php endforeach; ?>
                  </select></div>
                <div class="col-3"><label class="form-label fw-semibold small mb-1">Pincode</label><input type="text" name="pincode" class="form-control form-control-sm" value="<?= esc_attr($edit['pincode']??'') ?>" maxlength="6"></div>
              </div>

              <div class="small fw-bold text-success border-bottom pb-1 mb-2"><i class="bi bi-receipt-cutoff me-1"></i>Tax & Credit</div>
              <div class="row g-2 mb-2">
                <div class="col-7"><label class="form-label fw-semibold small mb-1">GSTIN</label><input type="text" name="gstin" class="form-control form-control-sm text-uppercase" maxlength="15" placeholder="22AAAAA0000A1Z5" value="<?= esc_attr($edit['gstin']??'') ?>"></div>
                <div class="col-5"><label class="form-label fw-semibold small mb-1">PAN</label><input type="text" name="pan_number" class="form-control form-control-sm text-uppercase" maxlength="10" value="<?= esc_attr($edit['pan_number']??'') ?>"></div>
              </div>
              <div class="row g-2 mb-3">
                <div class="col-4"><label class="form-label fw-semibold small mb-1">Payment Terms</label>
                  <select name="payment_terms" class="form-select form-select-sm">
                    <?php foreach(['Net 30','Net 45','Net 60','Advance','Immediate'] as $t): ?><option <?= ($edit['payment_terms']??'Net 30')===$t?'selected':'' ?>><?= $t ?></option><?php endforeach; ?>
                  </select></div>
                <div class="col-3"><label class="form-label fw-semibold small mb-1">Credit Days</label><input type="number" name="credit_days" class="form-control form-control-sm" value="<?= esc_attr($edit['credit_days']??30) ?>" min="0"></div>
                <div class="col-5"><label class="form-label fw-semibold small mb-1">Credit Limit (<?= esc_html(msme_erp_cur()) ?>)</label>
                  <div class="input-group input-group-sm"><span class="input-group-text bg-light"><?= esc_html(msme_erp_cur()) ?></span>
                  <input type="number" name="credit_limit" class="form-control form-control-sm" value="<?= esc_attr($edit['credit_limit']??0) ?>" min="0" step="1000"></div></div>
              </div>

              <?php if($edit): ?>
              <div class="row g-2 mb-3">
                <div class="col-6"><label class="form-label fw-semibold small mb-1">Std Discount %</label><input type="number" name="discount_pct" class="form-control form-control-sm" value="<?= esc_attr($edit['discount_pct']??0) ?>" min="0" max="100" step="0.01"></div>
                <div class="col-6"><label class="form-label fw-semibold small mb-1">Status</label>
                  <select name="status" class="form-select form-select-sm">
                    <option value="ACTIVE" <?= ($edit['status']??'')==='ACTIVE'?'selected':'' ?>>Active</option>
                    <option value="INACTIVE" <?= ($edit['status']??'')==='INACTIVE'?'selected':'' ?>>Inactive</option>
                    <option value="BLOCKED" <?= ($edit['status']??'')==='BLOCKED'?'selected':'' ?>>Blocked</option>
                  </select></div>
              </div>
              <?php endif; ?>

              <div class="d-grid gap-2">
                <button type="submit" class="btn btn-success fw-semibold"><i class="bi bi-save me-1"></i><?= $edit?'Update Customer':'Create Customer' ?></button>
                <?php if($edit): ?><a href="<?= esc_url(admin_url('admin.php?page=msme-erp&tcode=XD01')) ?>" class="btn btn-outline-secondary btn-sm"><i class="bi bi-plus me-1"></i>New Customer</a><?php endif; ?>
              </div>
            </form>
          </div>
        </div>
      </div>

      <div class="col-xl-7 col-lg-6">
        <div class="card border-0 shadow-sm">
          <div class="card-header bg-white d-flex justify-content-between align-items-center py-2">
            <span class="fw-bold small"><i class="bi bi-people me-2"></i>All Customers <span class="badge bg-secondary"><?= count($custs) ?></span></span>
            <input type="text" id="cust-search" class="form-control form-control-sm" style="max-width:200px" placeholder="Search...">
          </div>
          <div class="card-body p-0" style="max-height:calc(100vh - 210px);overflow-y:auto">
            <table class="table table-hover table-sm mb-0" style="font-size:.8rem">
              <thead style="position:sticky;top:0;background:#2e7d32;color:#fff">
                <tr>
                  <th class="px-3 fw-semibold">Code</th>
                  <th class="fw-semibold">Name</th>
                  <th class="fw-semibold">City</th>
                  <th class="fw-semibold">Credit Limit</th>
                  <th class="fw-semibold text-center">Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody id="cust-tbody">
                <?php foreach($custs as $c): $sc=['ACTIVE'=>'success','INACTIVE'=>'secondary','BLOCKED'=>'danger']; $isEd=$custId&&$custId==$c['id']; ?>
                <tr class="cust-row <?= $isEd?'table-warning':'' ?>">
                  <td class="px-3 fw-bold"><a href="<?= esc_url(admin_url("admin.php?page=msme-erp&tcode=XD02&id={$c['id']}")) ?>" class="text-decoration-none text-success"><?= esc_html($c['customer_code']) ?></a></td>
                  <td>
                    <div class="fw-semibold"><?= esc_html(substr($c['name'],0,22)) ?></div>
                    <?php if($c['phone']): ?><div class="text-muted" style="font-size:.7rem"><?= esc_html($c['phone']) ?></div><?php endif; ?>
                  </td>
                  <td><?= esc_html($c['city']??'—') ?></td>
                  <td class="fw-bold"><?= esc_html(msme_erp_cur()) ?><?= number_format($c['credit_limit'],0) ?></td>
                  <td class="text-center"><span class="badge bg-<?= $sc[$c['status']]??'secondary' ?> small"><?= $c['status'] ?></span></td>
                  <td>
                    <a href="<?= esc_url(admin_url("admin.php?page=msme-erp&tcode=XD02&id={$c['id']}")) ?>" class="btn btn-xs btn-outline-primary py-0 px-1"><i class="bi bi-pencil"></i></a>
                    <a href="<?= esc_url(admin_url("admin.php?page=msme-erp&tcode=VA01&customer_id={$c['id']}")) ?>" class="btn btn-xs btn-outline-success py-0 px-1 ms-1" title="Create SO"><i class="bi bi-cart-plus"></i></a>
                  </td>
                </tr>
                <?php endforeach; ?>
                <?php if(!$custs): ?><tr><td colspan="6" class="text-center text-muted py-5"><i class="bi bi-people display-6 d-block mb-2 opacity-25"></i>No customers yet.</td></tr><?php endif; ?>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
    <script>
    document.getElementById('cust-search')?.addEventListener('input',function(){
        var q=this.value.toLowerCase();
        document.querySelectorAll('.cust-row').forEach(function(r){ r.style.display=r.textContent.toLowerCase().includes(q)?'':'none'; });
    });
    </script>
    <?php
    return;
}

// MB5L / LT01 fallback — handled in supplier_invoice.php
if (in_array($tcode, ['MB5L','LT01'])) {
    require_once __DIR__ . '/supplier_invoice.php';
    return;
}

echo '<div class="alert alert-warning">T-Code '.esc_html($tcode).' not handled.</div>';
