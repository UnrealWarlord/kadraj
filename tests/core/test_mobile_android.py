"""Mobil 6e (M21–M23): Android'de çalışmayan özellikler gizli, Android metinleri "cihaz" der, Android simgesi imzasız.

Android ``KADRAJ_PLATFORM=android`` ile taklit edilir. Simge dosyaları ``tools/make_icons.py`` ile üretilir; burada
üretilenle depodaki dosyaların aynı olduğu ve işaretin her maskede görünür kaldığı denetlenir.
"""
from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import re
from pathlib import Path

import pytest

from kadraj.core import i18n, sysmon
from kadraj.core.strings import ANDROID_STRINGS

ROOT = Path(__file__).resolve().parents[2]
COMPUTER = re.compile(r"bilgisayar|computer", re.I)
#: "bilgisayar" diyen ama Android'de hiç gösterilmeyen metinler (neden yanında).
NOT_ON_ANDROID = {
    "home.drop.button": "ana ekrandaki bırakma alanı mobilde gizli",
    "vault.key_missing.cause": "Android'de kasa parola kipinde; anahtar zinciri hatası olmaz",
    "settings.local.title": "yerel yapay zekâ bölümü Android'de gizli",
    "settings.local.not.found": "yerel yapay zekâ bölümü Android'de gizli",
    "recorder.err.whisper_model.summary": "yazıya çevirme Android'de gizli",
    "ai.err.local_down.summary": "yerel yapay zekâ Android'de yok",
    "ai.err.local_model_missing.summary": "yerel yapay zekâ Android'de yok",
    "ai.err.local_model_missing.step1": "yerel yapay zekâ Android'de yok",
    "ai.setup.local_title": "kurulum kartının yerel bölümü Android'de gizli",
}


@pytest.fixture()
def on_android(monkeypatch):
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    monkeypatch.setenv("KADRAJ_FORM_FACTOR", "tablet")


@pytest.fixture()
def editor(ctx, tmp_path):
    from kadraj.modules.editor import render
    from kadraj.modules.editor.strings import STRINGS
    i18n.register_strings(STRINGS)
    render.set_hardware_override({"nvenc": True, "vaapi": True})
    ctx.project.create(tmp_path / "projeler", "Android")
    svc = ctx.service("editor")
    svc.reload("load")
    yield svc
    render.set_hardware_override(None)


@pytest.fixture()
def all_strings():
    """Bütün dize tabloları kayıtlı (modüller, AI katmanı, hata kataloğu, depolama)."""
    import kadraj.modules as pkg
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.ispkg and not info.name.startswith("_"):
            try:
                mod = importlib.import_module(f"kadraj.modules.{info.name}.strings")
            except ModuleNotFoundError:
                continue
            if hasattr(mod, "STRINGS"):
                i18n.register_strings(mod.STRINGS)
    from kadraj.ai_engine import aistrings
    i18n.register_strings(aistrings.STRINGS)
    importlib.import_module("kadraj.core.error_catalog")
    for name in ("disk", "profiles", "onboarding"):
        importlib.import_module(f"kadraj.storage.{name}")
    lang = i18n.get_language()
    yield
    i18n.set_language(lang)


# ------------------------------------------------------------------------------------------------ düzenleyici
def test_editor_hides_automations_android_cannot_install(editor, qtbot, monkeypatch):
    from kadraj.modules.editor.panels import ANDROID_UNAVAILABLE, MAGIC_ORDER, MAGIC_SIMPLE, MagicGrid
    desktop = MagicGrid(editor, MAGIC_ORDER)
    qtbot.addWidget(desktop)
    assert list(desktop.buttons) == list(MAGIC_ORDER)
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    tablet = MagicGrid(editor, MAGIC_ORDER)
    phone = MagicGrid(editor, MAGIC_SIMPLE)
    qtbot.addWidget(tablet)
    qtbot.addWidget(phone)
    assert not set(tablet.buttons) & ANDROID_UNAVAILABLE
    assert {"silence", "voice", "ducking", "vitalize", "watermark", "freeze", "colormatch"} <= set(tablet.buttons)
    assert list(phone.buttons) == ["silence", "voice", "ducking", "vitalize", "watermark"]


def test_pro_export_offers_only_what_android_ffmpeg_can_encode(editor, qtbot, monkeypatch):
    from kadraj.modules.editor.panels import QualityPicker

    def items(combo):
        return [combo.itemData(i) for i in range(combo.count())]

    desktop = QualityPicker(editor, pro=True)
    qtbot.addWidget(desktop)
    assert "h265" in items(desktop.codec) and items(desktop.hw) == ["auto", "cpu", "nvenc", "vaapi"]
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    android = QualityPicker(editor, pro=True)
    qtbot.addWidget(android)
    assert items(android.codec) == ["h264", "prores", "wav"]              # x265 Android FFmpeg'inde yok
    assert items(android.hw) == ["auto", "cpu"]                          # NVENC / VA-API yok
    editor.hardwareChanged.emit()                                        # algılama bitince yenilenir, çökmez
    assert all(android.hw.model().item(i).isEnabled() for i in range(android.hw.count()))


# ------------------------------------------------------------------------------------------------ sistem paneli
def test_system_panel_shows_only_measurable_rows_on_android(ctx, qtbot, monkeypatch):
    from kadraj.ui.shell.legend import SystemPanel
    desktop = SystemPanel(ctx)
    qtbot.addWidget(desktop)
    assert not any(desktop._vals[k].isHidden() for k in sysmon.PANEL_KEYS)
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    android = SystemPanel(ctx)
    qtbot.addWidget(android)
    assert [k for k in sysmon.PANEL_KEYS if not android._vals[k].isHidden()] == ["ram", "disk", "ping"]


def test_sysmon_without_psutil_reads_disk_and_meminfo(tmp_path, monkeypatch):
    info = tmp_path / "meminfo"
    info.write_text("MemTotal:        8000000 kB\nMemFree:          500000 kB\nMemAvailable:    3000000 kB\n",
                    encoding="ascii")
    monkeypatch.setattr(sysmon, "MEMINFO", info)
    s = sysmon.SystemMonitor(psutil_module=False, nvml=None).sample(str(tmp_path))
    assert s["cpu"] is None and s["gpu"] is None
    assert s["ram_total"] == 8000000 * 1024 and s["ram_used"] == 5000000 * 1024
    assert s["disk_free"] and s["disk_total"] >= s["disk_free"]
    monkeypatch.setattr(sysmon, "MEMINFO", tmp_path / "yok")
    assert sysmon.SystemMonitor(psutil_module=False).sample()["ram_total"] is None


def test_meminfo_parsing():
    assert sysmon.meminfo("MemTotal: 100 kB\nMemFree: 40 kB\n") == (60 * 1024, 100 * 1024)    # eski çekirdek
    assert sysmon.meminfo("MemFree: 40 kB\n") is None
    assert sysmon.meminfo("") is None


# ------------------------------------------------------------------------------------------------ metinler
def test_android_variants_replace_existing_keys_without_new_placeholders(all_strings):
    assert set(ANDROID_STRINGS["tr"]) == set(ANDROID_STRINGS["en"])
    for lang, table in ANDROID_STRINGS.items():
        for key, text in table.items():
            base = i18n._tables[lang].get(key)
            assert base is not None, f"{lang}: {key} yok"
            assert set(re.findall(r"{(\w+)}", text)) <= set(re.findall(r"{(\w+)}", base)), key   # yeni yer tutucu yok
            assert not COMPUTER.search(text), key


def test_android_texts_say_device_not_computer(all_strings, monkeypatch):
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    for lang in ("tr", "en"):
        i18n.set_language(lang)
        found = sorted(k for k in i18n._tables[lang] if COMPUTER.search(i18n.t(k)) and k not in NOT_ON_ANDROID)
        assert not found, f"{lang}: Android'de 'bilgisayar' diyen metinler: {found}"


def test_desktop_keeps_its_own_texts(all_strings, monkeypatch):
    i18n.set_language("tr")
    assert "bilgisayarında" in i18n.t("consent.p1")
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    assert "cihazında" in i18n.t("consent.p1") and i18n.has("consent.p1")
    assert i18n.t("err.tool.missing.summary", tool="FFmpeg").startswith("Bu işlem için FFmpeg")


def test_ffmpeg_error_on_android_offers_no_component_update(all_strings, monkeypatch):
    from kadraj.modules.downloader import errors
    i18n.set_language("tr")
    assert "update_components" in [a.id for a in errors.build("FFMPEG").actions]
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    err = errors.build("FFMPEG")
    assert "update_components" not in [a.id for a in err.actions]
    assert "uygulamayla gelir" in " ".join(err.steps)
    assert "update_components" in [a.id for a in errors.build("JS").actions]     # yt-dlp betikleri güncellenebilir


def test_splash_summary_skips_local_ai_on_android(monkeypatch):
    from kadraj.ui.shell.startup import ToolStatus, summary_line
    tools = {k: ToolStatus(k, label, True) for k, label in (("yt-dlp", "yt-dlp"), ("ffmpeg", "FFmpeg"),
                                                            ("ollama", "Ollama"))}
    assert "Ollama" in summary_line(tools, "3.0.0")
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    assert "Ollama" not in summary_line(tools, "3.0.0")


# ------------------------------------------------------------------------------------------------ Android simgesi
def _icons():
    spec = importlib.util.spec_from_file_location("make_icons", ROOT / "tools" / "make_icons.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _master() -> str:
    return (ROOT / "kadraj" / "ui" / "assets" / "icon" / "kadraj.svg").read_text(encoding="utf-8")


def test_android_icon_files_are_generated_from_the_master_svg():
    gen = _icons()
    for rel, text in gen.android_layers(_master()).items():
        path = gen.ANDROID_RES / rel
        assert path.is_file(), f"{rel} yok: python tools/make_icons.py"
        assert path.read_text(encoding="utf-8") == text, f"{rel} eski: python tools/make_icons.py"
    fg = (gen.ANDROID_RES / "drawable" / "kadraj_icon_foreground.xml").read_text(encoding="utf-8")
    assert "İnsibağ" not in fg and "<text" not in fg                     # başlatıcı simgesi ≤ 192 px: imza yok
    adaptive = (gen.ANDROID_RES / "mipmap-anydpi-v26" / "icon.xml").read_text(encoding="utf-8")
    assert "<monochrome" in adaptive                                     # Android 13+ temalı simge


def test_android_icon_mark_stays_inside_every_mask():
    gen = _icons()
    reach_dp = gen.mark_reach(_master()) * gen.ANDROID_UNIT_DP
    assert 26 <= reach_dp <= gen.SAFE_RADIUS_DP                          # kırpılmaz, ama küçücük de kalmaz
    assert list(gen.path_points("M3 8V4h4")) == [(3, 8), (3, 4), (7, 4)]
    with pytest.raises(ValueError):
        list(gen.path_points("M0 0C1 1 2 2 3 3"))


def test_apk_build_uses_the_adaptive_icon_and_a_signatureless_png():
    build = (ROOT / "packaging" / "android" / "build_apk.sh").read_text(encoding="utf-8")
    assert '"android.add_resources": f"{here}/res"' in build
    m = re.search(r'"icon\.filename": "[^"]*kadraj-(\d+)\.png"', build)
    assert m and int(m.group(1)) < _icons().SIGNATURE_MIN_PX              # imza yalnız ≥ 256 px
