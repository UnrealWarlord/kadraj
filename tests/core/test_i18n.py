"""i18n: TR/EN anahtar eşitliği, koddaki her anahtarın varlığı, mod terimleri, marka kuralı."""
from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

import pytest

from kadraj.core import i18n
from kadraj.core.strings import CORE_STRINGS

ROOT = Path(__file__).resolve().parents[2] / "kadraj"


@pytest.fixture(autouse=True)
def _restore_i18n():
    lang, mode = i18n.get_language(), i18n.get_ui_mode()
    yield
    i18n.set_language(lang)
    i18n.set_ui_mode(mode)


def test_tr_and_en_have_identical_keys():
    tr, en = set(CORE_STRINGS["tr"]), set(CORE_STRINGS["en"])
    assert tr == en, f"eksik EN: {sorted(tr - en)}  eksik TR: {sorted(en - tr)}"


def _module_tr_keys() -> set:
    """Her ``kadraj/modules/<ad>/strings.py`` içindeki ``STRINGS["tr"]`` anahtarları (modül dizeleri çekirdek tabloda değildir)."""
    import kadraj.modules as pkg
    keys: set = set()
    for info in pkgutil.iter_modules(pkg.__path__):
        if not info.ispkg or info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"kadraj.modules.{info.name}.strings")
        except ModuleNotFoundError:
            continue
        keys |= set(getattr(mod, "STRINGS", {}).get("tr", {}))
    from kadraj.ai_engine import aistrings                                   # AI motoru hata kartları (modül değil, ortak katman)
    keys |= set(aistrings.STRINGS["tr"])
    return keys


def test_every_literal_key_used_in_code_exists():
    pat = re.compile(r'\b(?:i18n\.)?(?:t|term|has)\(\s*"([^"]+)"')
    used = set()
    for p in ROOT.rglob("*.py"):
        if p.name == "strings.py":
            continue
        used |= set(pat.findall(p.read_text(encoding="utf-8")))
    used |= {"consent.p1", "consent.p2", "consent.p3", "consent.p4",
             "splash.step_settings", "splash.step_ffmpeg", "splash.step_ytdlp", "splash.step_ai", "splash.step_ready",
             "wizard.api.note_yes", "wizard.api.note_how", "status.mode_simple"}
    have = set(CORE_STRINGS["tr"]) | _module_tr_keys()                       # çekirdek + kayıtlı modüllerin dizeleri
    # term() çağrıları ``simple.``/``legend.`` çiftini ya da düz anahtarı arar
    missing = sorted(k for k in used if k not in have and f"simple.{k}" not in have)
    assert not missing, missing


def test_dynamic_key_families_exist():
    tr = CORE_STRINGS["tr"]
    for n in (1, 2, 3):
        assert f"wizard.q{n}" in tr and f"wizard.q{n}.hint" in tr and f"wizard.step{n}" in tr
    for tid in ("dl", "rec", "edit", "photo", "film", "unsure"):
        assert f"wizard.task.{tid}" in tr and f"wizard.task.{tid}.sub" in tr
    for ws in ("project", "edit", "studio", "capture", "system"):
        assert f"workspace.{ws}" in tr
    for mid in ("downloader", "recorder", "editor", "photo", "series", "settings"):
        for lang in ("tr", "en"):
            assert f"mod.{mid}.title" in CORE_STRINGS[lang] and f"mod.{mid}.desc" in CORE_STRINGS[lang]
            assert f"simple.mod.{mid}.title" in CORE_STRINGS[lang] and f"legend.mod.{mid}.title" in CORE_STRINGS[lang]


def test_t_fallbacks_and_formatting():
    i18n.set_language("tr")
    assert i18n.t("status.saved") == "Proje kaydedildi."
    assert i18n.t("autosave.every", seconds=20) == "Her 20 sn'de kaydediliyor"
    assert i18n.t("no.such.key") == "no.such.key"                      # anahtarın kendisi
    assert i18n.t("autosave.every") == "Her {seconds} sn'de kaydediliyor"   # eksik argüman → çökme yok
    i18n.set_language("en")
    assert i18n.t("status.saved") == "Project saved."


def test_unknown_language_raises():
    with pytest.raises(ValueError):
        i18n.set_language("xx")


def test_mode_aware_terms():
    i18n.set_language("tr")
    i18n.set_ui_mode("simple")
    assert i18n.term("snapshot") == "Kayıt noktası"
    i18n.set_ui_mode("legend")
    assert i18n.term("snapshot") == "Snapshot"
    assert i18n.term("status.saved") == "Proje kaydedildi."            # çift yoksa düz anahtar
    i18n.set_ui_mode("simple")
    assert i18n.term("mod.editor.title") == "Video Düzenle"
    i18n.set_ui_mode("legend")
    assert i18n.term("mod.editor.title") == "Kurgu"


def test_register_strings_cannot_override_core():
    i18n.set_language("tr")
    i18n.register_strings({"tr": {"status.saved": "HACK", "downloader.title": "İndirici başlık"}})
    assert i18n.t("status.saved") == "Proje kaydedildi."
    assert i18n.t("downloader.title") == "İndirici başlık"


def test_turkish_case_helpers():
    i18n.set_language("tr")
    assert i18n.upper("ilk ışık") == "İLK IŞIK"
    assert i18n.capitalize("izmir") == "İzmir"


def test_brand_word_not_in_ui_strings():
    """Plan §0.1: marka yalnızca Lockup ile; çeviri tablolarında 'KADRAJ' geçmez."""
    for lang, table in CORE_STRINGS.items():
        for key, value in table.items():
            assert "KADRAJ" not in value.upper(), (lang, key)


# ---- varsayılan dil: sistem yerel ayarından bağımsız (Docker/CI'da LANG yok ya da tr_TR kurulu değil) -------------------------
def test_default_language_is_turkish_whatever_the_locale(monkeypatch):
    import locale
    monkeypatch.setattr(i18n, "_language", None)
    for loc in (("en_US", "UTF-8"), ("de_DE", "UTF-8"), (None, None), ("C", None)):
        monkeypatch.setattr(locale, "getlocale", lambda *a, loc=loc: loc)
        monkeypatch.setattr(i18n, "_language", None)
        assert i18n.system_language() == "tr" and i18n.get_language() == "tr"


@pytest.mark.parametrize("env", [{"LANG": "C"}, {"LANG": "en_US.UTF-8"}, {"LC_ALL": "C"}, {}])
def test_default_language_in_a_clean_process_is_turkish(env):
    """Gerçek süreç: ``LANG`` yok / ``C`` / İngilizce iken de ilk dil Türkçe (``env -u LANG LC_ALL=C`` durumu)."""
    import os
    import subprocess
    import sys
    clean = {k: v for k, v in os.environ.items() if k not in ("LANG", "LC_ALL", "LANGUAGE", "LC_MESSAGES")}
    code = "from kadraj.core import i18n; print(i18n.get_language(), i18n.t('home.greeting') != 'home.greeting')"
    out = subprocess.run([sys.executable, "-c", code], env={**clean, **env}, capture_output=True, text=True, timeout=60,
                         cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    assert out.stdout.split() == ["tr", "True"], out.stderr[-300:]


def test_app_context_defaults_to_turkish_but_respects_a_saved_language(qapp, clean_home, monkeypatch):
    import locale
    from kadraj.core.settings import SettingsStore
    from kadraj.ui.shell.app import create_app
    monkeypatch.setattr(locale, "getlocale", lambda *a: ("en_US", "UTF-8"))
    results = {}
    for saved in ("", "en"):
        monkeypatch.setattr(i18n, "_language", None)
        settings = SettingsStore()
        settings.set("wizard.done", True)
        settings.set("language", saved)
        _app, context, theme = create_app(["kadraj"], settings=settings)
        theme.set_animations(False)
        results[saved] = i18n.get_language()
        theme.finish_transitions()
        context.shutdown()
    i18n.set_language("tr")
    assert results == {"": "tr", "en": "en"}                  # kayıtlı dile dokunulmaz; kayıt yoksa Türkçe
