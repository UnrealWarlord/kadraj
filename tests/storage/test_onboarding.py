"""İlk açılış verisi: kök klasör, ilk proje iskeleti, ayarlar, hata durumunda ayarların değişmemesi."""
from __future__ import annotations

import pytest

from kadraj.core.contracts import WizardAnswers
from kadraj.core.settings import SettingsStore
from kadraj.storage import onboarding


@pytest.fixture()
def settings(tmp_path):
    return SettingsStore(tmp_path / "settings.json")


def test_custom_root_created_and_settings_written(tmp_path, settings):
    target = tmp_path / "Benim" / "Kok_Klasor"
    result = onboarding.run(settings, WizardAnswers(
        tasks=("dl", "edit"), place="custom", root_path=str(target), api="how"))
    assert result.root == target and target.is_dir()
    assert result.project_dir is None and result.folders == ()
    assert settings.get("project_root") == str(target)
    assert settings.get("wizard.done") is True
    assert settings.get("wizard.tasks") == ["dl", "edit"]
    assert settings.get("wizard.api") == "how"
    assert settings.get("ui_mode") == "simple"


def test_first_project_gets_full_layout(tmp_path, settings):
    result = onboarding.run(settings,
                            WizardAnswers(place="custom", root_path=str(tmp_path / "kok")),
                            first_project="  Dizi: Bölüm 1 / Taslak  ")
    assert result.project_dir == tmp_path / "kok" / "Dizi Bölüm 1 Taslak"
    assert len(result.folders) == 7
    assert (result.project_dir / "06_Proxies").is_dir()
    assert (result.project_dir / ".kadraj" / "autosave").is_dir()


def test_empty_project_name_skips_project_creation(tmp_path, settings):
    result = onboarding.run(settings, WizardAnswers(place="custom", root_path=str(tmp_path / "k")),
                            first_project="   ")
    assert result.project_dir is None
    assert result.root.is_dir()


def test_clean_project_name_strips_path_tricks():
    assert onboarding.clean_project_name("../../etc") == "etc"
    assert "/" not in onboarding.clean_project_name("a/b\\c:d")
    with pytest.raises(ValueError):
        onboarding.clean_project_name(" ... ")


def test_failure_leaves_settings_untouched(tmp_path, settings):
    blocker = tmp_path / "dosya"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        onboarding.run(settings, WizardAnswers(place="custom", root_path=str(blocker)))
    assert settings.get("wizard.done") is False
    assert settings.get("project_root") in ("", None)
