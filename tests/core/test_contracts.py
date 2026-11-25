"""Sözleşme (contracts.py) testleri: tipler, doğrulama, Protocol uyumu."""
from __future__ import annotations

from kadraj.core import contracts as C
from kadraj.core.contracts import (Accent, ContextAPI, EventBusAPI, ModuleSpec, ProjectAPI, RecentAPI,
                                   SettingsAPI, Severity, SmartAction, SmartError, UiMode, VaultAPI,
                                   JobQueueAPI, WizardAnswers)


def test_ui_mode_coerce_never_raises():
    assert UiMode.coerce("legend") is UiMode.LEGEND
    assert UiMode.coerce(UiMode.LEGEND) is UiMode.LEGEND
    assert UiMode.coerce("nonsense") is UiMode.SIMPLE
    assert UiMode.coerce(None) is UiMode.SIMPLE


def test_contract_imports_without_qt_at_runtime():
    # Qt yalnızca TYPE_CHECKING altında: modül sözlüğünde PySide6 isimleri yok
    assert not any(name.startswith("Q") for name in vars(C))


def test_module_spec_validation():
    ok = ModuleSpec(id="downloader", title_key="mod.downloader.title", order=10)
    assert ok.validate() == []
    bad = ModuleSpec(id="Down-Loader", title_key="", order=1, workspace="nope",
                     strings={"tr": {"other.key": "x"}})
    problems = bad.validate()
    assert any("id geçersiz" in p for p in problems)
    assert any("title_key" in p for p in problems)
    assert any("workspace" in p for p in problems)
    assert any("başlamalı" in p for p in problems)


def test_module_spec_is_frozen():
    spec = ModuleSpec(id="a", title_key="k", order=1)
    try:
        spec.id = "b"            # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ModuleSpec değiştirilebilir olmamalı")


def test_smart_error_roundtrip():
    err = SmartError("net.offline", "Başlık", "Özet", cause="Neden", steps=["a", "b"],
                     actions=[SmartAction("retry", "Yeniden dene", "retry", {"x": 1})],
                     severity=Severity.WARNING, technical="raw", retryable=True, source="downloader")
    d = err.to_dict()
    assert d["code"] == "net.offline" and d["steps"] == ["a", "b"] and d["severity"] == "warning"
    assert d["actions"][0]["payload"] == {"x": 1}
    assert str(err) == "Özet"            # Exception olarak da kullanılabilir


def test_wizard_answers_normalized():
    a = WizardAnswers(tasks=("zzz",), place="mars", api="maybe").normalized()
    assert a.tasks == ("unsure",) and a.place == "videos" and a.api == "skip"


def test_constants_match_plan():
    assert C.PROJECT_SUBFOLDERS == ("01_Downloads", "02_Audio_WAV", "03_AI_Images", "04_AI_Scenes",
                                    "05_Scripts", "06_Proxies", "07_Exports")
    assert C.AUDIO_OUTPUT_EXT == ".wav"            # plan §0.4
    assert C.SIMPLE_SAMPLE_RATE == 48000
    assert C.LEGEND_SAMPLE_RATES == (44100, 48000, 88200, 96000)
    assert C.PROJECT_FILE_NAME == "proje.kadraj" and C.PROJECT_FORMAT == "kadraj-project"


def test_every_topic_is_unique():
    topics = [v for k, v in vars(C.Topics).items() if k.isupper()]
    assert len(topics) == len(set(topics))


def test_protocols_are_satisfied_by_the_real_implementations(ctx):
    """Haiku'nun bağlanacağı arayüzlerin gerçek nesnelerce karşılandığını doğrular."""
    assert isinstance(ctx.settings, SettingsAPI)
    assert isinstance(ctx.bus, EventBusAPI)
    assert isinstance(ctx.project, ProjectAPI)
    assert isinstance(ctx.jobs, JobQueueAPI)
    assert isinstance(ctx.vault, VaultAPI)
    assert isinstance(ctx.recent, RecentAPI)
    assert isinstance(ctx, ContextAPI)
