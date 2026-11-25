<?php
/**
 * MSME ERP — Router: XK02 → mm/supplier_create.php
 * Developed by: Prigag Online Pvt. Ltd.
 */
defined('ABSPATH') || exit;
if (empty($_GET['tcode'])) $_GET['tcode'] = 'XK02';
$tcode = $_GET['tcode'];
require_once __DIR__ . '/supplier_create.php';
