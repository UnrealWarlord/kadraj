"""Modül sözleşmesi: kayıt, dizeler (TR/EN eşitliği, kullanılan her anahtar), marka kuralı, hafif içe aktarma, bağımlılıklar."""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

from kadraj.core import i18n
from kadraj.modules.recorder import module as rec_module
from kadraj.modules.recorder.strings import EN, STRINGS, TR

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "kadraj" / "modules" / "recorder"


def test_register_returns_a_valid_spec():
    spec = rec_module.register()
    assert spec.id == "recorder" and spec.validate() == [] and spec.strings is STRINGS
    assert spec.simple_page and spec.legend_page and spec.service_factory
    assert spec.order == 20 and spec.chip == ".wav" and spec.desc_key == "mod.recorder.desc"


def test_discovered_by_the_registry_without_errors(ctx):
    assert ctx.registry.errors == [] and ctx.registry.get("recorder") is not None
    entry = next(e for e in ctx.registry.entries() if e.id == "recorder")
    assert entry.available and entry.workspace == "capture"


def test_tr_and_en_keys_match_and_are_namespaced():
    assert set(TR) == set(EN), (sorted(set(TR) - set(EN)), sorted(set(EN) - set(TR)))
    assert all(k.startswith("recorder.") for k in TR)
    for table in (TR, EN):
        for key, value in table.items():
            assert value.strip(), key
            assert "KADRAJ" not in value.upper(), key                                        # marka yalnızca Lockup'ta
    for key in TR:                                                                           # biçim yer tutucuları iki dilde aynı
        assert sorted(re.findall(r"\{(\w+)\}", TR[key])) == sorted(re.findall(r"\{(\w+)\}", EN[key])), key


def _sources():
    files = list(PKG.glob("*.py")) + [ROOT / "kadraj" / "ai_engine" / "providers" / "whisper_local.py"]
    return {p: p.read_text(encoding="utf-8") for p in files if p.name != "strings.py"}


def test_every_literal_key_used_in_code_exists():
    used = set()
    for src in _sources().values():
        used |= set(re.findall(r'\b(?:i18n\.)?(?:t|term|upper)\(\s*"(recorder\.[^"]+)"', src))
        used |= set(re.findall(r'"(recorder\.(?:a11y|status|vu|verdict|gate|test|takes|take|transcribe|job|prompter|speed|legend|device|saved)'
                               r'[A-Za-z0-9_.]*)"', src))
    used -= {"recorder.prompter", "recorder.transcribe", "recorder.master"}                  # ayar/iş türü kimlikleri, dize değil
    missing = sorted(k for k in used if k not in TR)
    assert not missing, missing


def test_dynamic_key_families_are_complete():
    for v in ("idle", "good", "quiet", "hot", "clip"):
        assert f"recorder.verdict.{v}" in TR
    for v in ("silent", "quiet", "good", "loud", "clipping"):
        assert f"recorder.test.result.{v}" in TR
    for s in ("slow", "normal", "fast"):
        assert f"recorder.speed.{s}" in TR
    for g in ("threshold_db", "attack_ms", "hold_ms", "release_ms", "range_db"):
        assert f"recorder.gate.{g}" in TR
    for c in ("take", "duration", "rate", "wave", "srt", "studio"):
        assert f"recorder.legend.col.{c}" in TR
    for code in ("no_backend", "no_input", "device_busy", "unsupported_rate", "unsupported_channels", "permission",
                 "open_failed", "whisper_missing", "whisper_model"):
        for part in ("title", "summary", "cause", "step1"):
            assert f"recorder.err.{code}.{part}" in TR, (code, part)
    assert "recorder.err.whisper_missing.action" in TR and TR["recorder.err.whisper_missing.action"].endswith("kur")


def test_speed_labels_match_the_spec():
    assert (TR["recorder.speed.slow"], TR["recorder.speed.normal"], TR["recorder.speed.fast"]) == ("Yavaş", "Normal", "Hızlı")
    assert TR["recorder.verdict.good"] == "Sesin harika!" and TR["recorder.gate.label"] == "Arka plandaki vızıltıyı sustur"
    assert TR["recorder.transcribe.button"] == "Konuşmayı Yazıya Çevir (.srt)" and TR["recorder.test.button"] == "Mikrofonu Dene"
    assert "-18 dBFS" in TR["recorder.legend.autogain"]


def test_importing_the_module_is_light():
    code = ("import sys, kadraj.modules.recorder.module as m; m.register();"
            "bad=[n for n in ('sounddevice','soundfile','faster_whisper','PySide6.QtWidgets') if n in sys.modules];"
            "print('BAD', bad) if bad else print('OK')")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "OK", out.stdout + out.stderr


def test_no_brand_word_in_non_docstring_strings():
    word = re.compile(r"\bKADRAJ\b")
    for path in list(PKG.glob("*.py")) + [ROOT / "kadraj/ai_engine/providers/whisper_local.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body and isinstance(node.body[0], ast.Expr) \
                    and isinstance(getattr(node.body[0], "value", None), ast.Constant):
                docs.add(id(node.body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                assert not word.search(node.value), (path.name, node.lineno)


def test_audio_output_is_wav_only_in_the_source():
    bad = (".mp3", ".aac", ".flac", ".ogg", ".m4a", ".opus", ".aiff")
    for path, src in _sources().items():
        low = src.lower()
        for ext in bad:
            assert f'"{ext}"' not in low and f"'{ext}'" not in low, (path.name, ext)
    assert "AUDIO_OUTPUT_EXT" in (PKG / "engine.py").read_text() and "AUDIO_OUTPUT_EXT" in (PKG / "mastering.py").read_text()


def test_module_requirements_file_exists_and_documents_optional_deps():
    text = (PKG / "requirements.txt").read_text(encoding="utf-8")
    assert "faster-whisper" in text and "ffmpeg" in text.lower() and "requirements-ai" in text


def test_core_requirements_cover_the_modules_hard_dependencies():
    core = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for dep in ("numpy", "sounddevice", "soundfile"):
        assert dep in core
    assert "faster-whisper" in (ROOT / "requirements-ai.txt").read_text(encoding="utf-8").lower()
