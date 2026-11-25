<?php
/**
 * MSME ERP — Router: MM03/MM02/MM03 → mm/material_master.php
 * Developed by: Prigag Online Pvt. Ltd.
 */
defined('ABSPATH') || exit;
if (empty($_GET['tcode'])) $_GET['tcode'] = 'MM03';
$tcode = $_GET['tcode'];
require_once __DIR__ . '/material_master.php';
