"""Akıllı Hata sözlüğü: kural sayısı, metinler, örnek hatalar ve asla istisna atmama."""
from __future__ import annotations

import errno
import urllib.error

import pytest

from kadraj.core import error_catalog as EC
from kadraj.core import errors
from kadraj.core.contracts import ErrorRule, SmartError
from kadraj.core.i18n import get_language, has, set_language


class PortAudioError(Exception):
    """PortAudio'nun sounddevice istisnası (ad eşleşmesi için yeniden tanımlandı)."""


@pytest.fixture(autouse=True)
def _fresh_catalog():
    errors.reset_catalog_cache()
    yield
    errors.reset_catalog_cache()


def _http(code):
    return urllib.error.HTTPError("https://api.example/v1", code, "msg", {}, None)


CASES = [
    (OSError(errno.ENOSPC, "No space left on device"), {"tool": "ffmpeg"}, "ffmpeg.disk_full"),
    (RuntimeError("ffmpeg failed"), {"tool": "ffmpeg", "stderr": "[mov] moov atom not found"},
     "ffmpeg.corrupt_input"),
    (RuntimeError("ffmpeg failed"), {"tool": "ffmpeg", "stderr": "Unknown encoder 'libx264'"},
     "ffmpeg.no_encoder"),
    (PermissionError(13, "Permission denied"), {"tool": "ffmpeg"}, "ffmpeg.permission"),
    (RuntimeError("bilinmeyen"), {"tool": "ffmpeg", "stderr": "something odd"}, "ffmpeg.failed"),
    (RuntimeError("ERROR: [youtube] abc: This video is not available in your country"),
     {"tool": "yt-dlp"}, "ytdlp.geo_blocked"),
    (RuntimeError("ERROR: Sign in to confirm your age"), {"tool": "yt-dlp"}, "ytdlp.age_restricted"),
    (RuntimeError("ERROR: Private video. Sign in if you've been granted access"),
     {"tool": "yt-dlp"}, "ytdlp.private"),
    (RuntimeError("ERROR: Video unavailable"), {"tool": "yt-dlp"}, "ytdlp.unavailable"),
    (RuntimeError("ERROR: Unsupported URL: https://example.org/x"), {"tool": "yt-dlp"},
     "ytdlp.unsupported_url"),
    (RuntimeError("ERROR: IncompleteRead(0 bytes read)"), {"tool": "yt-dlp"}, "ytdlp.connection"),
    (RuntimeError("ERROR: unable to download: HTTP Error 403: Forbidden"), {"tool": "yt-dlp"},
     "ytdlp.http_403"),
    (RuntimeError("ERROR: something else"), {"tool": "yt-dlp"}, "ytdlp.generic"),
    (_http(401), {"tool": "http"}, "http.unauthorized"),
    (_http(403), {"tool": "http"}, "http.forbidden"),
    (_http(429), {"tool": "http"}, "http.rate_limited"),
    (_http(503), {"tool": "http"}, "http.server_error"),
    (TimeoutError("timed out"), {"tool": "http"}, "http.timeout"),
    (RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB"), {"tool": "cuda"}, "cuda.oom"),
    (RuntimeError("CUDA error: no kernel image is available for execution on the device"),
     {"tool": "cuda"}, "cuda.unavailable"),
    (PortAudioError("Error opening InputStream: Invalid input device"), {"tool": "audio"},
     "audio.no_device"),
]


def test_catalog_has_at_least_20_unique_rules_sorted_by_priority():
    rules = EC.RULES
    assert len(rules) >= 20
    assert all(isinstance(r, ErrorRule) for r in rules)
    ids = [r.id for r in rules]
    assert len(ids) == len(set(ids))
    prios = [r.priority for r in rules]
    assert prios == sorted(prios)


def test_required_families_are_covered():
    ids = {r.id for r in EC.RULES}
    for family in ("ffmpeg.", "ytdlp.", "http.", "cuda.", "audio."):
        assert any(i.startswith(family) for i in ids), family
    for needed in ("ffmpeg.disk_full", "ffmpeg.corrupt_input", "ffmpeg.no_encoder", "ffmpeg.permission",
                   "ytdlp.geo_blocked", "ytdlp.private", "ytdlp.unavailable", "ytdlp.connection",
                   "ytdlp.http_403", "http.unauthorized", "http.forbidden", "http.rate_limited",
                   "http.server_error", "http.timeout", "cuda.oom", "audio.no_device"):
        assert needed in ids, needed


def test_every_rule_has_text_in_both_languages_without_brand_word():
    original = get_language()
    try:
        for lang in ("tr", "en"):
            set_language(lang)
            for r in EC.RULES:
                for field in ("title", "summary", "cause", "step1"):
                    key = f"err.cat.{r.id}.{field}"
                    assert has(key), (lang, key)
                    assert "KADRAJ" not in errors.t(key).upper()
    finally:
        set_language(original)


@pytest.mark.parametrize("exc, ctx, expected", CASES, ids=[c[2] for c in CASES])
def test_sample_errors_map_to_expected_card(exc, ctx, expected):
    result = errors.translate(exc, ctx)
    assert isinstance(result, SmartError)
    assert result.code == expected
    assert result.title and result.summary


def test_ffmpeg_missing_tool_still_uses_builtin_tool_card():
    result = errors.translate(FileNotFoundError("ffmpeg"), {"tool": "ffmpeg"})
    assert result.code == "tool.missing"


def test_network_error_under_ytdlp_still_reaches_offline_card():
    result = errors.translate(urllib.error.URLError(OSError("ağ yok")), {"tool": "yt-dlp"})
    assert result.code == "net.offline"


def test_retryable_and_copy_actions_are_set():
    connection = errors.translate(RuntimeError("ERROR: IncompleteRead(3 bytes)"), {"tool": "yt-dlp"})
    assert connection.retryable and any(a.kind == "retry" for a in connection.actions)
    generic = errors.translate(RuntimeError("ERROR: weird"), {"tool": "yt-dlp", "stderr": "ERROR: weird"})
    assert any(a.kind == "copy" for a in generic.actions)
    assert "weird" in generic.technical


def test_translate_never_raises_on_odd_inputs():
    samples = [RuntimeError(), Exception(None), OSError(), ValueError("x" * 5000)]
    contexts = [{}, {"stderr": None}, {"status": "weird"}, {"tool": 5}, {"provider": None}]
    for exc in samples:
        for ctx in contexts:
            result = errors.translate(exc, ctx)
            assert isinstance(result, SmartError) and result.title
