"""Senaryo dışa aktarımı: Fountain metni, yapı uyarıları, PDF (gömülü yazı tipi, Türkçe glifler)."""
from __future__ import annotations

from pathlib import Path

from reportlab.pdfbase.ttfonts import TTFont

from kadraj import paths
from kadraj.ai_engine.export import Screenplay, build_pdf, to_fountain, turkish_upper


def _sample() -> Screenplay:
    sp = Screenplay(title="Kıyı", author="Ali Emre")
    sp.scene("mutfak - gün").action("Işık pencereden süzülür.")
    sp.character("ayşe").parenthetical("fısıldayarak").dialogue("Bak, İstanbul uyanıyor.")
    sp.dialogue("Bir daha.")
    sp.transition("kesme:")
    return sp


def test_turkish_upper_maps_dotted_and_dotless_i():
    assert turkish_upper("ışık iç gün") == "IŞIK İÇ GÜN"


def test_fountain_title_page_scene_action_character_and_transition():
    text = to_fountain(_sample())
    assert text.startswith("Title: Kıyı\nCredit: Written by\nAuthor: Ali Emre\n\n")
    assert ".MUTFAK - GÜN" in text
    assert "Işık pencereden süzülür." in text
    assert "@AYŞE\n(fısıldayarak)\nBak, İstanbul uyanıyor.\nBir daha." in text   # blok, boş satır yok
    assert ">KESME:" in text
    assert text.endswith("\n") and not text.endswith("\n\n")


def test_forced_markers_are_respected_and_risky_action_is_escaped():
    sp = Screenplay().scene(".ZATEN ZORLANMIŞ").action("@ bu aksiyon").action("İÇ. kapı")
    text = to_fountain(sp)
    assert ".ZATEN ZORLANMIŞ" in text and "!@ bu aksiyon" in text
    assert "İÇ. kapı" in text                       # sıradan aksiyon olduğu gibi


def test_dialogue_without_character_is_reported_but_still_exported():
    sp = Screenplay().dialogue("karaktersiz satır")
    assert sp.problems() and "karakter" in sp.problems()[0]
    assert "karaktersiz satır" in to_fountain(sp)


def test_empty_elements_are_refused():
    try:
        Screenplay().action("   ")
    except ValueError as exc:
        assert "Boş" in str(exc)
    else:
        raise AssertionError("boş öğe kabul edildi")


def test_pdf_bytes_embed_the_font_and_have_turkish_glyphs():
    data = build_pdf(_sample())
    assert data.startswith(b"%PDF")
    assert b"JetBrains" in data                    # gömülü yazı tipi
    font = TTFont("check", str(Path(paths.fonts_dir()) / "JetBrainsMono-Regular.ttf"))
    for ch in "çğıİöşüÇĞÖŞÜ":
        assert font.face.charToGlyph.get(ord(ch), 0), ch


def test_pdf_can_be_written_to_a_path(tmp_path):
    target = tmp_path / "kiyi.pdf"
    result = build_pdf(_sample(), target)
    assert result == target and target.read_bytes().startswith(b"%PDF")


def test_empty_screenplay_still_produces_a_pdf():
    assert build_pdf(Screenplay()).startswith(b"%PDF")
