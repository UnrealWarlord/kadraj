"""Ayar deposu: varsayılanlar, noktalı anahtar, atomik kalıcılık, bozuk dosya, sihirbaz cevapları."""
from __future__ import annotations

import json
from pathlib import Path

from kadraj import paths
from kadraj.core.contracts import WizardAnswers
from kadraj.core.settings import DEFAULTS, SettingsStore, apply_wizard_answers, default_project_root


def test_paths_are_isolated_from_the_real_home(test_home):
    """conftest HOME/KADRAJ_HOME'u geçici dizine çevirir; gerçek ~/.kadraj'a dokunulmaz."""
    for fn in (paths.home_dir, paths.settings_file, paths.consent_file, paths.recent_file,
               paths.vault_file, paths.profiles_dir):
        assert fn().is_relative_to(test_home), fn


def test_defaults_and_dotted_access(clean_home):
    s = SettingsStore()
    assert s.get("ui_mode") == "simple"
    assert s.get("a11y.large_text") is False
    assert s.get("no.such.key", "dflt") == "dflt"
    s.set("a11y.large_text", True)
    assert s.large_text is True and s.get("a11y.large_text") is True


def test_persistence_roundtrip_and_atomic_file(clean_home):
    s = SettingsStore()
    s.set("language", "en")
    s.set("window.legend_state.project", "AAAA")
    assert s.path.is_file()
    data = json.loads(s.path.read_text(encoding="utf-8"))
    assert data["language"] == "en" and data["window"]["legend_state"]["project"] == "AAAA"
    assert not list(s.path.parent.glob(".settings-*"))          # geçici dosya kalmadı
    s2 = SettingsStore()
    assert s2.get("language") == "en"


def test_change_signal(clean_home, qtbot):
    s = SettingsStore()
    with qtbot.waitSignal(s.changed, timeout=500) as sig:
        s.set("ui_mode", "legend")
    assert sig.args == ["ui_mode", "legend"]
    with qtbot.assertNotEmitted(s.changed):
        s.set("ui_mode", "legend")                                # değişmedi → sinyal yok


def test_corrupt_file_is_quarantined_and_defaults_used(clean_home):
    clean_home.mkdir(parents=True)
    (clean_home / "settings.json").write_text("{ not json", encoding="utf-8")
    s = SettingsStore()
    assert s.get("ui_mode") == "simple"
    assert (clean_home / "settings.json.bad").is_file()          # veri kaybı yok


def test_unknown_keys_are_preserved(clean_home):
    clean_home.mkdir(parents=True)
    (clean_home / "settings.json").write_text(json.dumps({"future_key": 42, "ui_mode": "legend"}), encoding="utf-8")
    s = SettingsStore()
    assert s.get("future_key") == 42 and s.get("ui_mode") == "legend"
    s.set("language", "tr")
    assert json.loads(s.path.read_text(encoding="utf-8"))["future_key"] == 42


def test_apply_wizard_answers(clean_home, tmp_path):
    s = SettingsStore()
    root = apply_wizard_answers(s, WizardAnswers(tasks=("rec", "photo"), place="custom",
                                                 root_path=str(tmp_path / "mine"), api="how"))
    assert root == tmp_path / "mine"
    assert s.get("wizard.done") is True and s.get("wizard.tasks") == ["rec", "photo"]
    assert s.get("project_root") == str(tmp_path / "mine") and s.get("wizard.api") == "how"
    assert s.get("ui_mode") == "simple"                           # ilk açılış her zaman Basit


def test_default_project_root_locations():
    assert default_project_root("videos").name == "KADRAJ_Projeler"
    assert default_project_root("desktop").name == "KADRAJ_Projeler"


def test_defaults_have_schema_and_a11y():
    assert DEFAULTS["schema"] == 1 and set(DEFAULTS["a11y"]) == {"large_text", "high_contrast", "reduced_motion"}
