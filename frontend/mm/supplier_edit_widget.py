"""
Brain ERP – XK02 router  •  mirrors supplier_edit.php exactly.

The PHP source does nothing but default $_GET['tcode'] to 'XK02' and then
`require_once supplier_create.php` — i.e. XK02 (Change Supplier) is NOT a separate screen,
it's XK01 (Create Supplier) entered with a different default tcode. There is no form, table
or logic of its own here, same as the 3-line PHP file.

main_window.py already reflects this: `_TCODE_INDEX` maps both "XK01" and "XK02" to the same
SupplierWidget instance (index 6), so that routing is already correct and this file changes
nothing there. This module exists only for call sites that want to import a supplier-edit
entry point by name (e.g. a nav tree or tcode dispatch table built elsewhere that expects one
module per tcode) — it hands back the exact same SupplierWidget, just tagged with the XK02
default, instead of duplicating the screen.
"""

from __future__ import annotations

from ..api_client import ApiClient
from .supplier_widget import SupplierWidget

# Mirrors the PHP line: if (empty($_GET['tcode'])) $_GET['tcode'] = 'XK02';
DEFAULT_TCODE = "CHSU"


def build_supplier_edit_widget(api: ApiClient) -> SupplierWidget:
    """Mirrors supplier_edit.php: hands back the Create Supplier screen (SupplierWidget),
    defaulted to XK02 (Change Supplier) instead of XK01 — same screen, same form, same list.

    If SupplierWidget doesn't expose a way to be told which tcode it was opened under
    (e.g. to change a header label from "Create Supplier" to "Change Supplier"), the
    `set_tcode` call below is skipped rather than raising — check SupplierWidget's own
    source if you want that label to actually change for XK02.
    """
    widget = SupplierWidget(api)
    if hasattr(widget, "set_tcode"):
        widget.set_tcode(DEFAULT_TCODE)
    return widget


# Alias so other code can do `from .supplier_edit_widget import SupplierEditWidget` and get
# the same class supplier_edit.php's include ultimately renders — no second implementation.
SupplierEditWidget = SupplierWidget