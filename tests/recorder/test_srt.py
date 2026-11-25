"""SRT biçimlendirme/okuma."""
from __future__ import annotations

import pytest

from kadraj.ai_engine.providers.whisper_local import Segment
from kadraj.modules.recorder import srt as S


@pytest.mark.parametrize("sec, text", [(0, "00:00:00,000"), (1.5, "00:00:01,500"), (61.001, "00:01:01,001"),
                                       (3661.9996, "01:01:02,000"), (-5, "00:00:00,000"), (359999.9, "99:59:59,900")])
def test_timestamps(sec, text):
    assert S.format_timestamp(sec) == text


def test_format_srt_structure():
    out = S.format_srt([Segment(0.0, 2.5, "Merhaba dünya"), Segment(3.0, 4.2, "İkinci satır")])
    assert out == ("1\n00:00:00,000 --> 00:00:02,500\nMerhaba dünya\n\n"
                   "2\n00:00:03,000 --> 00:00:04,200\nİkinci satır\n")


def test_empty_segments_are_skipped_and_numbering_is_contiguous():
    out = S.format_srt([Segment(0, 1, "a"), Segment(1, 2, "   "), Segment(2, 3, "b")])
    assert out.count("-->") == 2 and "\n2\n" in out and "\n3\n" not in out


def test_zero_length_segment_gets_a_minimum_duration():
    out = S.format_srt([Segment(5.0, 5.0, "kısa")])
    assert "00:00:05,000 --> 00:00:05,001" in out


def test_caption_wrapping_keeps_all_text():
    long = "Bu çok uzun bir altyazı cümlesidir ve iki satıra bölünmesi gerekir çünkü çok fazla kelime içeriyor"
    wrapped = S.wrap_caption(long, 42, 2)
    assert wrapped.count("\n") == 1 and all(len(line) <= 60 for line in wrapped.split("\n"))
    assert " ".join(wrapped.split()) == long
    huge = " ".join(["kelime"] * 40)
    w3 = S.wrap_caption(huge, 20, 2)
    assert w3.count("\n") == 1 and " ".join(w3.split()) == huge                         # fazla satır sona katlanır, kayıp yok
    assert S.wrap_caption("   ", 42, 2) == ""


def test_write_is_atomic_utf8_without_bom(tmp_path):
    p = tmp_path / "sub" / "a.srt"
    S.write_srt(p, [Segment(0, 1, "Çok güzel ğüşiöç")])
    raw = p.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and "Çok güzel ğüşiöç" in raw.decode("utf-8")
    assert b"\r" not in raw and [x.name for x in p.parent.iterdir()] == ["a.srt"]


def test_parse_roundtrip_and_count(tmp_path):
    segs = [Segment(0.0, 2.5, "Merhaba dünya"), Segment(3.2, 61.5, "Uzun\nikinci")]
    p = S.write_srt(tmp_path / "r.srt", segs)
    parsed = S.parse_srt(p.read_text(encoding="utf-8"))
    assert [(round(s.start, 3), round(s.end, 3)) for s in parsed] == [(0.0, 2.5), (3.2, 61.5)]
    assert parsed[1].text == "Uzun ikinci" and S.count_segments(p) == 2
    assert S.count_segments(tmp_path / "yok.srt") == 0 and S.parse_srt("") == [] and S.parse_srt("saçma") == []
