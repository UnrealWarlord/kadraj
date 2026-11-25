"""Uygulama simgesi: pencere simgesi boş değil; üretilen dosyalar doğru boyutta; imza yalnızca 256 px ve üzerinde.

Dosyalar ``tools/make_icons.py`` ile üretilir; burada yalnızca çıktılar ve kuralın kendisi denetlenir.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from PIL import Image
from PySide6.QtWidgets import QApplication

from kadraj.ui.app_icon import ICON_SIZES, app_icon, icon_dir

ROOT = Path(__file__).resolve().parents[2]


def _load_generator():
    spec = importlib.util.spec_from_file_location("make_icons", ROOT / "tools" / "make_icons.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_app_window_icon_is_set_at_creation(ctx):
    assert not QApplication.windowIcon().isNull()
    assert not app_icon().isNull()


def test_icon_pngs_exist_with_their_pixel_sizes():
    for size in ICON_SIZES:
        path = icon_dir() / f"kadraj-{size}.png"
        assert path.is_file(), path
        with Image.open(path) as im:
            assert im.size == (size, size)
            assert im.mode == "RGBA"


def test_ico_contains_the_16_to_256_frames():
    with Image.open(icon_dir() / "kadraj.ico") as im:
        assert {w for w, _h in im.info["sizes"]} == {16, 24, 32, 48, 64, 128, 256}


def test_brand_signature_is_drawn_only_from_256_px_up():
    gen = _load_generator()
    svg = (icon_dir() / "kadraj.svg").read_text(encoding="utf-8")
    assert 'id="signature"' in gen.svg_for_size(svg, 256)
    assert 'id="signature"' in gen.svg_for_size(svg, 512)
    assert 'id="signature"' not in gen.svg_for_size(svg, 128)
    assert 'id="signature"' not in gen.svg_for_size(svg, 32)
    assert 'id="signature"' not in gen.svg_for_size(svg, 16)
