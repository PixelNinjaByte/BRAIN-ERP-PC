<?php
/**
 * MSME ERP — Router: ME2M → mm/po_view.php
 * Developed by: Prigag Online Pvt. Ltd.
 */
defined('ABSPATH') || exit;
if (empty($_GET['tcode'])) $_GET['tcode'] = 'ME2M';
$tcode = $_GET['tcode'];
require_once __DIR__ . '/po_view.php';
