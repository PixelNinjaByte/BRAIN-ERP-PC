<?php
/**
 * MSME ERP — Router: VK11 → mm/supplier_invoice.php
 * Developed by: Prigag Online Pvt. Ltd.
 */
defined('ABSPATH') || exit;
if (empty($_GET['tcode'])) $_GET['tcode'] = 'VK11';
$tcode = $_GET['tcode'];
require_once __DIR__ . '/supplier_invoice.php';
