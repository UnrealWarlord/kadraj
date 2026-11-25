"""Standart senaryo biçimli PDF (A4). Yazı tipi gömülü: JetBrains Mono (Türkçe glifler dahil).

Düzen (sol kenar boşluğu 1,5 in): sahne başlığı ve aksiyon sola yaslı; karakter adı 2,2 in girintili;
parantez 1,6 in; diyalog 1,0 in girintili ve 3,5 in genişlikte; geçiş sağa yaslı. Sayfa numarası
sağ altta. Şifre ya da dış bağlantı yoktur; PDF yalnızca bayt olarak üretilir ya da bir yola yazılır.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Optional, Union
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from ... import paths
from .fountain import Kind, Screenplay, turkish_upper

FONT = "ScreenplayMono"
FONT_BOLD = "ScreenplayMono-Bold"
FONT_FILE = "JetBrainsMono-Regular.ttf"
FONT_BOLD_FILE = "JetBrainsMono-Bold.ttf"
SIZE = 12
LEADING = 14


def register_fonts() -> str:
    """Gömülecek yazı tiplerini bir kez kaydeder ve normal yazı tipi adını döner."""
    names = pdfmetrics.getRegisteredFontNames()
    if FONT not in names:
        pdfmetrics.registerFont(TTFont(FONT, str(paths.fonts_dir() / FONT_FILE)))
    if FONT_BOLD not in names:
        pdfmetrics.registerFont(TTFont(FONT_BOLD, str(paths.fonts_dir() / FONT_BOLD_FILE)))
    return FONT


def _styles() -> dict:
    def style(name: str, **kw) -> ParagraphStyle:
        base = dict(fontName=FONT, fontSize=SIZE, leading=LEADING, spaceBefore=0, spaceAfter=LEADING)
        base.update(kw)
        return ParagraphStyle(name, **base)

    return {
        "scene": style("scene", fontName=FONT_BOLD),
        "action": style("action"),
        "character": style("character", leftIndent=2.2 * inch, spaceAfter=0),
        "parenthetical": style("parenthetical", leftIndent=1.6 * inch, rightIndent=2.2 * inch, spaceAfter=0),
        "dialogue": style("dialogue", leftIndent=1.0 * inch, rightIndent=1.5 * inch),
        "transition": style("transition", alignment=TA_RIGHT),
        "title": style("title", fontName=FONT_BOLD, fontSize=20, leading=26, alignment=TA_CENTER, spaceAfter=18),
        "author": style("author", fontSize=14, leading=18, alignment=TA_CENTER),
    }


def _page_number(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont(FONT, 10)
    canvas.drawRightString(A4[0] - inch, 0.6 * inch, str(doc.page))
    canvas.restoreState()


def _para(text: str) -> str:
    return escape(text).replace("\n", "<br/>")


def build_pdf(screenplay: Screenplay, target: Optional[Union[str, Path]] = None) -> Union[bytes, Path]:
    """Senaryoyu PDF'e çevirir. ``target`` yoksa baytları, varsa yazılan yolu döner."""
    font = register_fonts()
    styles = _styles()
    buf = io.BytesIO() if target is None else str(target)     # reportlab yolu düz metin ister
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.5 * inch, rightMargin=inch,
                            topMargin=inch, bottomMargin=inch,
                            title=screenplay.title or "Senaryo", author=screenplay.author or "")
    story: list = []
    if screenplay.title or screenplay.author:
        story.append(Spacer(1, 200))
        if screenplay.title:
            story.append(Paragraph(_para(screenplay.title), styles["title"]))
        if screenplay.author:
            story.append(Paragraph(_para(screenplay.author), styles["author"]))
        story.append(PageBreak())
    for el in screenplay.elements:
        if el.kind is Kind.SCENE:
            heading = el.text.lstrip(".")
            story.append(Paragraph(_para(turkish_upper(heading)), styles["scene"]))
        elif el.kind is Kind.ACTION:
            story.append(Paragraph(_para(el.text), styles["action"]))
        elif el.kind is Kind.CHARACTER:
            story.append(Paragraph(_para(turkish_upper(el.text)), styles["character"]))
        elif el.kind is Kind.PARENTHETICAL:
            story.append(Paragraph(_para(f"({el.text.strip('()')})"), styles["parenthetical"]))
        elif el.kind is Kind.DIALOGUE:
            story.append(Paragraph(_para(el.text), styles["dialogue"]))
        elif el.kind is Kind.TRANSITION:
            story.append(Paragraph(_para(turkish_upper(el.text)), styles["transition"]))
    if not story:
        story.append(Spacer(1, LEADING))
    doc.build(story, onFirstPage=_page_number, onLaterPages=_page_number)
    if target is None:
        assert isinstance(buf, io.BytesIO)
        return buf.getvalue()
    return Path(target)
