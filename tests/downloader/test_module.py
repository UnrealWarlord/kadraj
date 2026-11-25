"""Modül sözleşmesi, tembel içe aktarma (yt-dlp yok), dize anahtarları."""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

from kadraj.core.contracts import Accent
from kadraj.modules.downloader import module as M
from kadraj.modules.downloader.strings import STRINGS, K

PKG = pathlib.Path(M.__file__).resolve().parent
ROOT = PKG.parents[2]


def test_spec_matches_contract():
    spec = M.register()
    assert spec.validate() == []
    assert spec.id == "downloader" and spec.order == 10 and spec.accent is Accent.PEACH
    assert spec.simple_page is not None and spec.legend_page is not None
    assert spec.service_factory is not None
    assert spec.requires == ("ffmpeg",) and spec.workspace == "capture"


def test_module_files_do_not_import_yt_dlp_at_top_level():
    for name in ("module.py", "service.py", "strings.py", "range_bar.py", "page_simple.py", "page_legend.py"):
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            else:
                continue
            assert not any(m.split(".")[0] == "yt_dlp" for m in mods), (name, mods)


def test_register_does_not_load_yt_dlp():
    code = ("import sys; import kadraj.modules.downloader.module as m; m.register(); "
            "print('yt_dlp' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True,
                         check=True, timeout=120).stdout.strip()
    assert out == "False"


def test_strings_are_complete_and_namespaced():
    assert set(STRINGS["tr"]) == set(STRINGS["en"])
    for key in STRINGS["tr"]:
        assert key.startswith("downloader.")
    for name in dir(K):
        if name.isupper():
            assert getattr(K, name) in STRINGS["tr"], name
    for value in list(STRINGS["tr"].values()) + list(STRINGS["en"].values()):
        assert "KADRAJ" not in value.upper()
