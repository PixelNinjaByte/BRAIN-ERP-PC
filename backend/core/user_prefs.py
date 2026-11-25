"""
Brain ERP — Per-user look & feel, profile photo, background picture.
Direct port of PHP core/user_prefs.php → Python.

Everything was stored as WordPress usermeta on the logged-in WP user; there is
no WordPress here, so each user's choices are stored as one small JSON file
under app_data/user_prefs/<user_id>.json instead:

  theme / accent / sidebar / page colour / background / overlay / text size
  photo_url   profile photo (square, resized) — path under app_data/user_uploads
  photo_file  absolute path of that file (for clean-up)
  bgimg_file  absolute path of the uploaded background (for clean-up)

A user's own choices sit on top of the company branding (CMPY): anything the
user has not chosen falls back to the company value, so nothing changes for a
user who never opens My Account.

Replaces:
  get_user_meta()/update_user_meta() → JSON file per user in app_data
  wp_upload_dir()                    → app_data/user_uploads on disk
  wp_get_image_editor()              → Pillow (falls back to a plain copy
                                        if Pillow is not installed)
  pre_get_avatar_data filter          → not applicable (no WP avatars);
                                        user_photo_url() is the single source

Original: Prigag Online Pvt. Ltd.
Port:     Python/SQLite standalone desktop version
"""

import os
import re
import json
import logging
import secrets
from typing import Any, Optional

from core.auth import ErpAuth

logger = logging.getLogger(__name__)

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFS_DIR = os.path.join(APP_DIR, "app_data", "user_prefs")
UPLOAD_DIR = os.path.join(APP_DIR, "app_data", "user_uploads")

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")


def bg_presets() -> dict[str, tuple[str, str]]:
    return {
        "aurora":   ("Aurora",   "linear-gradient(135deg,#a8edea 0%,#fed6e3 100%)"),
        "ocean":    ("Ocean",    "linear-gradient(135deg,#667eea 0%,#64b5f6 100%)"),
        "sunrise":  ("Sunrise",  "linear-gradient(135deg,#ffecd2 0%,#fcb69f 100%)"),
        "forest":   ("Forest",   "linear-gradient(135deg,#d4fc79 0%,#96e6a1 100%)"),
        "lavender": ("Lavender", "linear-gradient(135deg,#e0c3fc 0%,#8ec5fc 100%)"),
        "slate":    ("Slate",    "linear-gradient(135deg,#cfd9df 0%,#e2ebf0 100%)"),
        "night":    ("Night",    "linear-gradient(135deg,#232526 0%,#414345 100%)"),
        "mint":     ("Mint",     "linear-gradient(135deg,#e0f7fa 0%,#b2ebf2 100%)"),
    }


def accent_swatches() -> dict[str, str]:
    return {
        "#1565c0": "Blue", "#0d9488": "Teal", "#16a34a": "Green", "#ca8a04": "Amber",
        "#ea580c": "Orange", "#dc2626": "Red", "#c026d3": "Magenta", "#7c3aed": "Violet",
        "#475569": "Slate", "#111827": "Charcoal",
    }


def text_sizes() -> dict[str, tuple[str, str]]:
    return {"small": ("Compact", "90%"), "normal": ("Normal", "100%"), "large": ("Large", "112%")}


def ui_defaults() -> dict[str, Any]:
    return {
        "theme": "",          # '' = follow legacy 'theme' key / light
        "accent": "",         # '' = company colour
        "sidebar": "",        # '' = company colour
        "page_bg": "",        # '' = company colour
        "bg_mode": "company", # company | color | preset | image
        "bg_preset": "",
        "bg_image": "",       # path inside our own uploads folder
        "overlay": 0,         # 0-80 (% veil over a picture so text stays readable)
        "text": "normal",
    }


def _sanitize_hex(v: str) -> str:
    v = (v or "").strip()
    return v if _HEX_RE.match(v) else ""


def _prefs_path(user_id: int) -> str:
    return os.path.join(PREFS_DIR, f"{user_id}.json")


def _load_raw(user_id: int) -> dict:
    try:
        with open(_prefs_path(user_id), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_raw(user_id: int, data: dict) -> None:
    os.makedirs(PREFS_DIR, exist_ok=True)
    with open(_prefs_path(user_id), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


# The user's saved look, validated. Safe to drop straight into CSS.
def user_ui(user_id: Optional[int] = None) -> dict[str, Any]:
    user_id = user_id or ErpAuth.user_id()
    saved = _load_raw(user_id).get("ui")
    ui = {**ui_defaults(), **(saved if isinstance(saved, dict) else {})}

    ui["accent"] = _sanitize_hex(str(ui["accent"]))
    ui["sidebar"] = _sanitize_hex(str(ui["sidebar"]))
    ui["page_bg"] = _sanitize_hex(str(ui["page_bg"]))
    ui["overlay"] = max(0, min(80, int(ui["overlay"] or 0)))
    if ui["text"] not in text_sizes():
        ui["text"] = "normal"
    if ui["bg_mode"] not in ("company", "color", "preset", "image"):
        ui["bg_mode"] = "company"
    if ui["bg_mode"] == "preset" and ui["bg_preset"] not in bg_presets():
        ui["bg_mode"] = "company"

    # Theme: new setting wins, else the older single "theme" value.
    legacy = _load_raw(user_id).get("theme")
    if ui["theme"] not in ("light", "dark"):
        ui["theme"] = legacy if legacy in ("light", "dark") else "light"

    # A stored picture must live in our own uploads folder, nothing else is ever used.
    base = user_upload_base()
    bg_image = str(ui["bg_image"] or "")
    if bg_image == "" or not bg_image.startswith(base["dir"]):
        ui["bg_image"] = ""
        if ui["bg_mode"] == "image":
            ui["bg_mode"] = "company"
    return ui


def user_ui_save(ui: dict, user_id: Optional[int] = None) -> None:
    user_id = user_id or ErpAuth.user_id()
    raw = _load_raw(user_id)
    raw["ui"] = ui
    # Keep the older single-value key in step so anything still reading it agrees.
    if ui.get("theme") in ("light", "dark"):
        raw["theme"] = ui["theme"]
    _save_raw(user_id, raw)


def effective_look(brand: dict, user_id: Optional[int] = None) -> dict[str, Any]:
    """Combine company branding with the user's own choices."""
    ui = user_ui(user_id)
    dark = ui["theme"] == "dark"

    primary = ui["accent"] or brand.get("primary", "")
    sidebar = ui["sidebar"] or brand.get("sidebar", "")

    bg_color = brand.get("bg_color", "")
    bg_image = brand.get("bg_image", "")
    css = ""
    if ui["bg_mode"] == "color":
        css = f"background:{ui['page_bg'] or bg_color or '#f0f2f5'};"
    elif ui["bg_mode"] == "preset":
        css = f"background:{bg_presets()[ui['bg_preset']][1]};background-attachment:fixed;"
    elif ui["bg_mode"] == "image":
        veil = ui["overlay"] / 100
        rgb = "20,22,26" if dark else "255,255,255"
        layer = f"linear-gradient(rgba({rgb},{veil}),rgba({rgb},{veil}))," if veil > 0 else ""
        css = f"background:{layer}url('{ui['bg_image']}') center/cover fixed no-repeat;"
    else:  # company
        css = f"background-color:{bg_color or '#f0f2f5'};"
        if bg_image:
            css += f"background-image:url('{bg_image}');background-size:cover;background-position:center;background-attachment:fixed;"

    return {
        "theme": ui["theme"],
        "primary": primary,
        "sidebar": sidebar,
        "main_css": css,
        "text_size": text_sizes()[ui["text"]][1],
        "ui": ui,
    }


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------
def user_upload_base() -> dict[str, str]:
    return {"dir": UPLOAD_DIR}


_ALLOWED_IMG_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".gif": "image/gif", ".webp": "image/webp"}


def _sniff_image_ext(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return ""
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return ""


def user_save_image(tmp_path: str, declared_size: int, kind: str, user_id: Optional[int] = None) -> tuple:
    """
    Validate + store an uploaded picture. kind = 'photo' (square 256px) or
    'background' (max 1920px wide). Returns (url_or_path, abs_path, error).
    Re-encodes with Pillow when available (also strips EXIF metadata);
    otherwise falls back to a plain copy of the original file.
    """
    user_id = user_id or ErpAuth.user_id()
    if not tmp_path or not os.path.isfile(tmp_path):
        return (None, None, "The upload was not received properly. Please try again.")

    max_bytes = (3 if kind == "photo" else 6) * 1024 * 1024
    if declared_size > max_bytes:
        return (None, None, f"Image is too large — keep it under {max_bytes // 1048576} MB.")

    ext = _sniff_image_ext(tmp_path)
    if ext not in _ALLOWED_IMG_EXT:
        return (None, None, "Please choose a JPG, PNG, GIF or WebP picture.")

    base = user_upload_base()
    try:
        os.makedirs(base["dir"], exist_ok=True)
    except OSError:
        return (None, None, "Could not create the upload folder.")

    is_jpeg = ext in (".jpg", ".jpeg")
    out_ext = "jpg" if is_jpeg else "png"
    name = f"u{user_id}-{'photo' if kind == 'photo' else 'bg'}-{secrets.token_hex(5)}.{out_ext}"
    dest = os.path.join(base["dir"], name)

    try:
        from PIL import Image
        img = Image.open(tmp_path)
        img.load()  # also rejects truncated/invalid files
        if kind == "photo":
            img = _center_crop_resize(img, 256, 256)
        else:
            if img.width > 1920:
                ratio = 1920 / img.width
                img = img.resize((1920, max(1, round(img.height * ratio))))
        if is_jpeg:
            img.convert("RGB").save(dest, "JPEG", quality=85)
        else:
            img.save(dest, "PNG")
    except ImportError:
        # No Pillow available — store the original bytes as-is.
        try:
            with open(tmp_path, "rb") as src, open(dest, "wb") as dst:
                dst.write(src.read())
        except OSError:
            return (None, None, "Could not save the picture.")
    except Exception:
        return (None, None, "That file could not be read as a picture.")

    return (dest, dest, None)


def _center_crop_resize(img, w: int, h: int):
    src_w, src_h = img.size
    scale = max(w / src_w, h / src_h)
    new_w, new_h = round(src_w * scale), round(src_h * scale)
    img = img.resize((new_w, new_h))
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


def user_delete_file(path: str) -> None:
    """Delete a stored file, but only if it really is inside our own folder."""
    if not path:
        return
    base = os.path.normpath(user_upload_base()["dir"])
    real = os.path.normpath(path)
    if real.startswith(base + os.sep) and os.path.isfile(real):
        try:
            os.unlink(real)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Avatar
# ---------------------------------------------------------------------------
def user_photo_url(user_id: Optional[int] = None) -> str:
    user_id = user_id or ErpAuth.user_id()
    url = str(_load_raw(user_id).get("photo_url") or "")
    base = user_upload_base()
    return url if url.startswith(base["dir"]) else ""


def user_initials(name: str) -> str:
    parts = re.split(r"\s+", name.strip())
    a = parts[0][:1].upper() if parts and parts[0] else ""
    b = parts[-1][:1].upper() if len(parts) > 1 else ""
    return (a + b) or "?"