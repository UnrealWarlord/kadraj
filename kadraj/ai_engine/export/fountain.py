"""Senaryo modeli ve Fountain metin çıktısı.

Sahne başlıkları ``.`` ile zorlanır (Türkçe başlıklar INT./EXT. zorunluluğunda değildir); karakter
adları ``@`` ile, geçişler ``>`` ile zorlanır. Türkçe büyük harf dönüşümü i→İ, ı→I kuralıyla yapılır.
Karakter bloğu: ad satırı, hemen ardından parantez ve diyalog satırları (boş satır olmadan).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import List, Union

FORCE_CHARS = "!@>~=#."


class Kind(str, enum.Enum):
    SCENE = "scene"
    ACTION = "action"
    CHARACTER = "character"
    PARENTHETICAL = "parenthetical"
    DIALOGUE = "dialogue"
    TRANSITION = "transition"


@dataclass(frozen=True)
class Element:
    kind: Kind
    text: str


@dataclass
class Screenplay:
    title: str = ""
    author: str = ""
    elements: List[Element] = field(default_factory=list)

    def add(self, kind: Union[Kind, str], text: str) -> "Screenplay":
        clean = _clean(text)
        if not clean:
            raise ValueError("Boş bir öğe eklenemez.")
        self.elements.append(Element(Kind(kind), clean))
        return self

    def scene(self, heading: str) -> "Screenplay":
        return self.add(Kind.SCENE, heading)

    def action(self, text: str) -> "Screenplay":
        return self.add(Kind.ACTION, text)

    def character(self, name: str) -> "Screenplay":
        return self.add(Kind.CHARACTER, name)

    def parenthetical(self, text: str) -> "Screenplay":
        return self.add(Kind.PARENTHETICAL, text)

    def dialogue(self, text: str) -> "Screenplay":
        return self.add(Kind.DIALOGUE, text)

    def transition(self, text: str) -> "Screenplay":
        return self.add(Kind.TRANSITION, text)

    def problems(self) -> List[str]:
        """Yapısal uyarılar (çıktıyı engellemez): karaktersiz diyalog, boş karakter bloğu."""
        out: List[str] = []
        speaking = False
        for index, el in enumerate(self.elements, start=1):
            if el.kind is Kind.CHARACTER:
                speaking = True
                continue
            if el.kind in (Kind.DIALOGUE, Kind.PARENTHETICAL):
                if not speaking:
                    out.append(f"{index}. öğe: diyalog bir karakter adından sonra gelmeli.")
                continue
            speaking = False
        return out


def turkish_upper(text: str) -> str:
    """Türkçe büyük harf: ``i`` → ``İ``, ``ı`` → ``I`` (Python'un varsayılan dönüşümünden farklı)."""
    return text.replace("i", "İ").replace("ı", "I").upper()


def _clean(text: str) -> str:
    return "\n".join(line.strip() for line in str(text or "").splitlines() if line.strip())


def _safe_action(text: str) -> str:
    return "!" + text if text[:1] in FORCE_CHARS else text


def to_fountain(screenplay: Screenplay) -> str:
    """Senaryo modelini Fountain metnine çevirir (son satırda yeni satır)."""
    head = []
    if screenplay.title:
        head.append(f"Title: {screenplay.title}")
    if screenplay.author:
        head.append("Credit: Written by")
        head.append(f"Author: {screenplay.author}")
    prefix = ("\n".join(head) + "\n\n") if head else ""

    blocks: List[str] = []
    els = screenplay.elements
    i = 0
    while i < len(els):
        el = els[i]
        if el.kind is Kind.SCENE:
            blocks.append(el.text if el.text.startswith(".") else "." + turkish_upper(el.text))
        elif el.kind is Kind.ACTION:
            blocks.append(_safe_action(el.text))
        elif el.kind is Kind.TRANSITION:
            blocks.append(">" + turkish_upper(el.text))
        elif el.kind is Kind.CHARACTER:
            lines = ["@" + turkish_upper(el.text)]
            j = i + 1
            while j < len(els) and els[j].kind in (Kind.PARENTHETICAL, Kind.DIALOGUE):
                lines.append(f"({els[j].text.strip('()')})" if els[j].kind is Kind.PARENTHETICAL else els[j].text)
                j += 1
            blocks.append("\n".join(lines))
            i = j
            continue
        else:                                   # karaktersiz diyalog: düz satır (problems() uyarır)
            blocks.append(el.text)
        i += 1
    return prefix + "\n\n".join(blocks) + "\n"
