"""Modül kayıt defteri: keşif kuralları, izolasyon, dizeler, planlı modüller."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from kadraj.core import i18n
from kadraj.core.contracts import Accent, ModuleSpec
from kadraj.core.registry import PLANNED, ModuleRegistry


def _make_pkg(tmp_path: Path, name: str, modules: dict) -> str:
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for mod, body in modules.items():
        d = pkg / mod
        d.mkdir()
        (d / "__init__.py").write_text("")
        if body is not None:
            (d / "module.py").write_text(textwrap.dedent(body))
    sys.path.insert(0, str(tmp_path))
    return name


@pytest.fixture()
def pkgs(tmp_path):
    yield tmp_path
    sys.path.remove(str(tmp_path))
    for k in [k for k in sys.modules if k.startswith("fakemods")]:
        del sys.modules[k]


def test_discovers_valid_modules_and_isolates_broken_ones(pkgs):
    name = _make_pkg(pkgs, "fakemods", {
        "alpha": """
            from kadraj.core.contracts import ModuleSpec, Accent
            def register():
                return ModuleSpec(id="alpha", title_key="alpha.title", order=5, accent=Accent.PEACH,
                                  strings={"tr": {"alpha.title": "Alfa"}, "en": {"alpha.title": "Alpha"}})
        """,
        "wrongid": """
            from kadraj.core.contracts import ModuleSpec
            def register():
                return ModuleSpec(id="other", title_key="k", order=1)
        """,
        "boom_import": "raise RuntimeError('import patladı')\n",
        "boom_register": "def register():\n    raise ValueError('register patladı')\n",
        "invalid": """
            from kadraj.core.contracts import ModuleSpec
            def register():
                return ModuleSpec(id="invalid", title_key="", order=1)
        """,
        "no_entry": None,              # module.py yok → sessizce atlanır, hata değil
        "_private": "def register():\n    raise SystemExit\n",
    })
    reg = ModuleRegistry(name)
    specs = reg.discover()
    assert [s.id for s in specs] == ["alpha"]
    assert reg.get("alpha").accent is Accent.PEACH
    codes = sorted(e.source for e in reg.errors)
    assert codes == ["boom_import", "boom_register", "invalid", "wrongid"]
    i18n.set_language("tr")
    assert i18n.t("alpha.title") == "Alfa"             # modül dizeleri birleşti


def test_missing_package_does_not_crash():
    reg = ModuleRegistry("no_such_package_xyz")
    assert reg.discover() == [] and reg.errors


def test_register_manual_and_sorting():
    reg = ModuleRegistry("kadraj.modules")
    reg.register(ModuleSpec(id="b", title_key="k", order=20))
    reg.register(ModuleSpec(id="a", title_key="k", order=10))
    assert [s.id for s in reg.specs()] == ["a", "b"]


def test_entries_merge_registered_and_planned():
    reg = ModuleRegistry("kadraj.modules")
    reg.register(ModuleSpec(id="downloader", title_key="mod.downloader.title", order=10,
                            desc_key="mod.downloader.desc"))
    entries = {e.id: e for e in reg.entries()}
    assert set(entries) == {p.id for p in PLANNED}
    assert entries["downloader"].available and not entries["recorder"].available
    assert [e.order for e in reg.entries()] == sorted(e.order for e in reg.entries())


def test_planned_modules_cover_the_plan():
    assert {p.id for p in PLANNED} == {"downloader", "recorder", "editor", "photo", "series", "settings"}
    assert {p.id: p.accent for p in PLANNED}["recorder"] is Accent.LAVENDER
    assert {p.id: p.chip for p in PLANNED}["recorder"] == ".wav"           # ses çıktısı yalnızca .wav
