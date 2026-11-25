"""Marka kuralı (plan §0.1) ve ses kuralı (§0.4) için kaynak taraması.

* "KADRAJ" sözcüğü kullanıcıya görünen bir dize olarak yalnızca ``branding.py``'de bulunabilir; geri kalan her yer
  ``Lockup`` kullanır. (Docstring ve yorumlar serbesttir.)
* Çekirdekte ses çıktısı için ``.wav`` dışında uzantı yok.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "kadraj"
ALLOWED = {"branding.py"}
_WORDMARK = re.compile(r"\bKADRAJ\b")          # KADRAJ_HOME / proje.kadraj gibi teknik tanımlayıcılar eşleşmez


def _docstring_nodes(tree: ast.AST) -> set:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def test_brand_word_only_in_branding_module():
    offenders = []
    for path in ROOT.rglob("*.py"):
        if path.name in ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs \
                    and _WORDMARK.search(node.value):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: {node.value[:50]!r}")
    assert not offenders, "KADRAJ metni Lockup dışında yazılmış:\n" + "\n".join(offenders)


def test_lockup_is_the_only_wordmark_painter():
    """``branding.APP_NAME`` ile metin çizen tek widget Lockup'tır."""
    users = [p.name for p in (ROOT / "ui").rglob("*.py") if "APP_NAME" in p.read_text(encoding="utf-8")]
    assert users == ["lockup.py"], users


def test_no_non_wav_audio_output_extensions_in_core():
    bad = (".mp3", ".aac", ".flac", ".ogg", ".m4a", ".opus")
    for path in ROOT.rglob("*.py"):
        src = path.read_text(encoding="utf-8").lower()
        for ext in bad:
            assert f'"{ext}"' not in src and f"'{ext}'" not in src, (path.name, ext)


def test_license_keeps_original_author_attribution():
    lic = (ROOT.parent / "LICENSE").read_text(encoding="utf-8")
    for name in ("Batuhan Barış Döğer", "Erol Boz", "Furkan Kağan Ekici", "SoilClassPro", "MIT License"):
        assert name in lic
    assert "KADRAJ" in lic                                             # KADRAJ telif satırı eklendi
