"""Dizi/Film yazım adımları (LLM): beyin fırtınası, senaryo (.fountain/.pdf), karakter-mekân **incili** + bölüm/sahne listesi, dublaj çevirisi,
foley önerileri. Anlatı/diyalog Türkçe; görsel tarifler (karakter/mekân/anahtar kare) İngilizce (görsel modeller için). Çıktılar
doğrulanıp normalize edilir (``normalize_bible``) — model hatalı JSON verirse ``ai.invalid_json`` kartı.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ...core.contracts import SmartError
from ..export.fountain import Screenplay, to_fountain
from ..export.pdf import build_pdf
from ..prompt_adapter import PromptSpec
from ..providers.aicore import ai_err
from .gateway import AIGateway
from .machine import CAMERA_ANGLES, Character, FoleyCue, Line, Location, Scene, SeriesProject

WRITER = ("You are an experienced showrunner and screenwriter. Write story text, dialogue and summaries in Turkish. "
          "Write every *visual description* (character looks, location looks) in concise English so image models can use it. "
          "Reply with JSON only, matching the schema.")
_STR = {"type": "string"}
BRAINSTORM_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ideas"],
                     "properties": {"ideas": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                       "required": ["title", "logline", "hook"],
                                                                       "properties": {"title": _STR, "logline": _STR, "hook": _STR}}}}}
SCREENPLAY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["title", "elements"],
                     "properties": {"title": _STR, "elements": {"type": "array", "items": {
                         "type": "object", "additionalProperties": False, "required": ["type", "text"],
                         "properties": {"type": {"type": "string", "enum": ["scene", "action", "character", "parenthetical", "dialogue", "transition"]},
                                        "text": _STR}}}}}
BIBLE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["episodes", "characters", "locations", "scenes"], "properties": {
    "episodes": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["index", "title", "synopsis"],
                                           "properties": {"index": {"type": "integer"}, "title": _STR, "synopsis": _STR}}},
    "characters": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "name", "desc"],
                                             "properties": {"id": _STR, "name": _STR, "desc": _STR}}},
    "locations": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "name", "desc"],
                                            "properties": {"id": _STR, "name": _STR, "desc": _STR}}},
    "scenes": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                         "required": ["episode", "title", "summary", "location", "characters", "angle", "seconds", "cut", "lines"],
                                         "properties": {"episode": {"type": "integer"}, "title": _STR, "summary": _STR, "location": _STR,
                                                        "characters": {"type": "array", "items": _STR}, "angle": _STR,
                                                        "seconds": {"type": "number"}, "cut": {"type": "boolean"},
                                                        "lines": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                                            "required": ["character", "text"],
                                                                                            "properties": {"character": _STR, "text": _STR}}}}}}}}


def slug(text: str, default: str = "x") -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower().translate(str.maketrans("çğıöşüâî", "cgiosuai"))).strip("_")
    return s or default


# ----------------------------------------------------------------------------------- beyin fırtınası + senaryo
def brainstorm(gw: AIGateway, idea: str, n: int = 3) -> List[Dict[str, str]]:
    res = gw.llm(f"Idea: {idea}\nGive {n} distinct directions (title, logline, hook).", system=WRITER, json_schema=BRAINSTORM_SCHEMA,
                 tier="cheap", max_tokens=2000)
    data = res.json()
    ideas = data.get("ideas") if isinstance(data, Mapping) else None
    if not isinstance(ideas, list) or not ideas:
        raise ai_err("invalid_json")
    return [{k: str(i.get(k, "")) for k in ("title", "logline", "hook")} for i in ideas if isinstance(i, Mapping)]


def screenplay_from_elements(title: str, elements: Sequence[Mapping[str, Any]], author: str = "") -> Screenplay:
    sp = Screenplay(title, author)
    table = {"scene": sp.scene, "action": sp.action, "character": sp.character, "parenthetical": sp.parenthetical,
             "dialogue": sp.dialogue, "transition": sp.transition}
    for el in elements:
        fn = table.get(str(el.get("type")))
        text = str(el.get("text", "")).strip()
        if fn and text:
            fn(text)
    return sp


def write_screenplay(gw: AIGateway, project: SeriesProject) -> Screenplay:
    fmt = project.format or {}
    prompt = (f"Title: {project.title or '(untitled)'}\nLogline: {project.logline or project.idea}\nIdea: {project.idea}\n"
              f"Write the screenplay for {fmt.get('kind', 'film')} lasting about {fmt.get('minutes', 10)} minutes. "
              "Use proper screenplay structure: scene headings (INT./EXT.), action, character cues, dialogue, transitions.")
    res = gw.llm(prompt, system=WRITER, json_schema=SCREENPLAY_SCHEMA, max_tokens=16000)
    data = res.json()
    if not isinstance(data, Mapping) or not data.get("elements"):
        raise ai_err("invalid_json")
    return screenplay_from_elements(str(data.get("title") or project.title or "Adsız"), data["elements"])


def save_screenplay(sp: Screenplay, folder: Path, stem: str, *, pdf: bool = True) -> Dict[str, str]:
    """``05_Scripts``'e ``.fountain`` (+ ``.pdf``) yazar; yollar döner."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out: Dict[str, str] = {}
    fp = folder / f"{stem}.fountain"
    fp.write_text(to_fountain(sp), encoding="utf-8")
    out["fountain"] = str(fp)
    if pdf:
        out["pdf"] = str(build_pdf(sp, folder / f"{stem}.pdf"))
    return out


# ----------------------------------------------------------------------------------- incil + sahneler
def normalize_bible(data: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], List[Character], List[Location], List[Scene]]:
    chars: List[Character] = []
    for c in data.get("characters") or []:
        if isinstance(c, Mapping) and str(c.get("name", "")).strip():
            cid = slug(str(c.get("id") or c["name"]))
            if all(x.id != cid for x in chars):
                chars.append(Character(cid, str(c["name"]).strip(), str(c.get("desc", "")).strip()))
    locs: List[Location] = []
    for l in data.get("locations") or []:
        if isinstance(l, Mapping) and str(l.get("name", "")).strip():
            lid = slug(str(l.get("id") or l["name"]))
            if all(x.id != lid for x in locs):
                locs.append(Location(lid, str(l["name"]).strip(), str(l.get("desc", "")).strip()))
    cids, lids = {c.id for c in chars}, {l.id for l in locs}
    episodes = [{"index": int(e.get("index", i + 1)), "title": str(e.get("title", "")), "synopsis": str(e.get("synopsis", ""))}
                for i, e in enumerate(data.get("episodes") or []) if isinstance(e, Mapping)] or [{"index": 1, "title": "", "synopsis": ""}]
    scenes: List[Scene] = []
    counters: Dict[int, int] = {}
    for s in data.get("scenes") or []:
        if not isinstance(s, Mapping):
            continue
        ep = max(1, int(s.get("episode", 1) or 1))
        counters[ep] = counters.get(ep, 0) + 1
        angle = str(s.get("angle", "medium")).lower()
        angle = angle if angle in CAMERA_ANGLES else "medium"
        loc = slug(str(s.get("location", "")))
        sc = Scene(f"E{ep:02d}S{counters[ep]:02d}", ep, counters[ep], str(s.get("title", "")), str(s.get("summary", "")),
                   loc if loc in lids else (next(iter(lids), "")), [slug(str(c)) for c in (s.get("characters") or []) if slug(str(c)) in cids],
                   [], [], angle, float(min(12.0, max(3.0, float(s.get("seconds", 5) or 5)))), bool(s.get("cut", False)))
        for ln in s.get("lines") or []:
            if isinstance(ln, Mapping) and slug(str(ln.get("character", ""))) in cids and str(ln.get("text", "")).strip():
                sc.lines.append(Line(slug(str(ln["character"])), str(ln["text"]).strip()))
        scenes.append(sc)
    if scenes:
        scenes[0].cut = True                               # ilk sahne her zaman anahtar kareden başlar
    return episodes, chars, locs, scenes


def write_bible(gw: AIGateway, project: SeriesProject) -> None:
    fmt = project.format
    prompt = (f"Screenplay (fountain):\n{project.screenplay[:24000]}\n\nBuild the series bible: {fmt.get('episodes', 1)} episode(s), "
              f"~{fmt.get('minutes', 10)} minutes total. Each scene is one continuous shot of 3-12 seconds. 'angle' is one of: "
              f"{', '.join(CAMERA_ANGLES)}. Mark 'cut': true when the scene jumps in time/place. Keep character looks identical across scenes.")
    res = gw.llm(prompt, system=WRITER, json_schema=BIBLE_SCHEMA, max_tokens=16000)
    data = res.json()
    if not isinstance(data, Mapping):
        raise ai_err("invalid_json")
    eps, chars, locs, scenes = normalize_bible(data)
    if not scenes:
        raise ai_err("invalid_json")
    project.episodes, project.characters, project.locations, project.scenes = eps, chars, locs, scenes


# ----------------------------------------------------------------------------------- istemler (deterministik)
def style_text(project: SeriesProject) -> str:
    return str((project.format or {}).get("style") or "cinematic film still, natural light")


def keyframe_spec(project: SeriesProject, scene: Scene, aspect: str = "16:9") -> PromptSpec:
    """Storyboard anahtar karesi: karakter tarifleri her sahnede **aynı** eklenir (tutarlılık), kamera açısı sahnenin açısıdır."""
    loc = project.location(scene.location)
    chars = [c for cid in scene.characters if (c := project.character(cid))]
    subject = "; ".join(filter(None, [scene.summary, *[f"{c.name}: {c.desc}" for c in chars if c.desc]]))
    return PromptSpec.create(subject=subject or scene.title or "scene", lighting=(loc.desc if loc and loc.desc else "natural light"),
                             angle=CAMERA_ANGLES.get(scene.angle, "medium shot"), style=style_text(project), aspect=aspect,
                             idea=scene.summary)


def video_prompt(project: SeriesProject, scene: Scene) -> str:
    loc = project.location(scene.location)
    chars = [c for cid in scene.characters if (c := project.character(cid))]
    parts = [scene.summary, CAMERA_ANGLES.get(scene.angle, "medium shot"), loc.desc if loc else "",
             *[f"{c.name}: {c.desc}" for c in chars if c.desc], style_text(project)]
    return ". ".join(p for p in parts if p)


# ----------------------------------------------------------------------------------- dublaj + foley
def translate_lines(gw: AIGateway, project: SeriesProject, langs: Sequence[str]) -> int:
    """Diyalogları ``langs`` dillerine çevirir (``line.texts[dil]``); dublaj hazırlığı. Çevrilen satır sayısını döner."""
    schema = {"type": "object", "additionalProperties": False, "required": ["items"],
              "properties": {"items": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["i", "text"],
                                                                 "properties": {"i": {"type": "integer"}, "text": _STR}}}}}
    flat = [(sc, ln) for sc in project.scenes for ln in sc.lines]
    count = 0
    for lang in langs:
        todo = [(i, ln) for i, (_sc, ln) in enumerate(flat) if lang not in ln.texts]
        if not todo:
            continue
        listing = "\n".join(f"{i}: {ln.text}" for i, ln in todo)
        res = gw.llm(f"Translate each numbered line to {lang}. Keep the tone and lip-sync length natural. Lines:\n{listing}",
                     system="You are a dubbing translator. Reply with JSON only.", json_schema=schema, tier="cheap", max_tokens=8000)
        data = res.json()
        for item in (data.get("items") if isinstance(data, Mapping) else None) or []:
            try:
                idx, text = int(item["i"]), str(item["text"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= idx < len(flat) and text:
                flat[idx][1].texts[lang] = text
                count += 1
    for lang in langs:
        if lang not in project.dub_languages:
            project.dub_languages.append(lang)
    return count


def suggest_foley(gw: AIGateway, scene: Scene) -> List[FoleyCue]:
    schema = {"type": "object", "additionalProperties": False, "required": ["cues"],
              "properties": {"cues": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                "required": ["text", "at", "seconds"],
                                                                "properties": {"text": _STR, "at": {"type": "number"}, "seconds": {"type": "number"}}}}}}
    res = gw.llm(f"Scene ({scene.seconds:.0f}s): {scene.summary}\nList 1-4 ambient/foley sound effects (English sound descriptions) with start time and length.",
                 system="You are a foley artist. Reply with JSON only.", json_schema=schema, tier="cheap", max_tokens=800)
    data = res.json()
    cues = []
    for c in (data.get("cues") if isinstance(data, Mapping) else None) or []:
        if isinstance(c, Mapping) and str(c.get("text", "")).strip():
            cues.append(FoleyCue(str(c["text"]).strip(), max(0.0, float(c.get("at", 0) or 0)), min(10.0, max(0.5, float(c.get("seconds", 2) or 2)))))
    return cues[:4]
