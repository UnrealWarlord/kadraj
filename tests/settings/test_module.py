"""Modül sözleşmesi ve dize anahtarları."""
from __future__ import annotations

from kadraj.core.contracts import Accent
from kadraj.modules.settings import module as M
from kadraj.modules.settings.strings import STRINGS, K


def test_spec_matches_contract():
    spec = M.register()
    assert spec.validate() == []
    assert spec.id == "settings" and spec.order == 90 and spec.workspace == "system"
    assert spec.accent is Accent.NEUTRAL and spec.icon == "gear"
    assert spec.simple_page is not None and spec.legend_page is not None
    assert spec.service_factory is not None


def test_strings_are_complete_namespaced_and_brand_free():
    assert set(STRINGS["tr"]) == set(STRINGS["en"])
    for key in STRINGS["tr"]:
        assert key.startswith("settings.")
    for name in dir(K):
        if name.isupper():
            assert getattr(K, name) in STRINGS["tr"], name
    for value in list(STRINGS["tr"].values()) + list(STRINGS["en"].values()):
        assert "KADRAJ" not in value.upper()
