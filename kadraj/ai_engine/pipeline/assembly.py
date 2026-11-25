"""Üretilen sahneleri **editör zaman çizelgesine** otomatik dizer: tek video kanalı, karakter başına ayrı ses kanalı (çoklu ses), dublaj
dilleri için sessize alınmış kanallar, AI foley kanalı ve **diyaloğun altında kalacak sidechain ducking**. Tek geri alma adımıdır."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ...modules.editor import ops
from ...modules.editor.probe import probe
from ...modules.editor.timeline import Clip, Timeline, new_id
from ..providers.aicore import wav_seconds
from .machine import RESOLUTIONS, SeriesProject

LINE_LEAD = 0.3
LINE_GAP = 0.25
MARK = "series"


def _wav_len(path: str) -> float:
    try:
        return wav_seconds(Path(path).read_bytes())
    except OSError:
        return 0.0


def _clip_len(path: str, fallback: float) -> float:
    try:
        d = probe(path).duration
        return d if d > 0.1 else fallback
    except Exception:
        return fallback


def build_timeline(tl: Timeline, project: SeriesProject) -> Dict[str, Any]:
    """``tl``'yi yerinde düzenler (önceki dizi kanallarını siler, yenisini kurar). Özet sözlüğü döner."""
    for tr in [t for t in tl.tracks if t.extra.get(MARK)]:
        tl.tracks.remove(tr)
    w, h = RESOLUTIONS.get(str((project.format or {}).get("resolution", "1080p")).lower(), RESOLUTIONS["1080p"])
    tl.width, tl.height = w, h
    vtrack = ops.add_track(tl, "video", "Dizi · Video")
    vtrack.extra[MARK] = True
    char_tracks: Dict[str, Any] = {}
    foley = None
    t = 0.0
    placed = 0
    for sc in project.scenes:
        if not sc.clip or not Path(sc.clip).exists():
            continue
        dur = _clip_len(sc.clip, sc.seconds)
        vtrack.clips.append(Clip(new_id(), "media", sc.clip, in_=0.0, out=dur, start=t, name=sc.id, data={"audio_muted": True}))
        placed += 1
        for lang in ["orig", *project.dub_languages]:
            at = t + LINE_LEAD
            for line in sc.lines:
                path = line.audio.get(lang)
                if not path or not Path(path).exists():
                    continue
                char = project.character(line.character)
                key = f"{line.character}:{lang}"
                if key not in char_tracks:
                    name = f"Ses · {char.name if char else line.character}" + ("" if lang == "orig" else f" ({lang.upper()})")
                    tr = ops.add_track(tl, "audio", name)
                    tr.extra[MARK] = True
                    tr.muted = lang != "orig"                 # dublaj kanalları hazır ama sessiz; kullanıcı açar
                    char_tracks[key] = tr
                length = _wav_len(path) or 1.0
                char_tracks[key].clips.append(Clip(new_id(), "media", path, in_=0.0, out=length, start=at, name=f"{sc.id} {line.character}"))
                at += length + LINE_GAP
        for cue in sc.foley:
            if cue.path and Path(cue.path).exists():
                if foley is None:
                    foley = ops.add_track(tl, "audio", "Foley")
                    foley.extra[MARK] = True
                foley.clips.append(Clip(new_id(), "media", cue.path, in_=0.0, out=max(0.5, _wav_len(cue.path) or cue.seconds),
                                        start=t + cue.at, name=cue.text[:24], volume_db=-6.0))
        t += dur
    if foley is not None and char_tracks:
        foley.effects.append({"type": "duck", "enabled": True, "params": {
            "keys": [tr.id for k, tr in char_tracks.items() if k.endswith(":orig")], "threshold": 0.04, "ratio": 8, "attack": 20, "release": 400}})
    return {"scenes": placed, "duration": t, "voice_tracks": len(char_tracks), "foley": foley is not None}


def assemble(editor: Any, project: SeriesProject) -> Dict[str, Any]:
    """Editör servisinde **tek undo adımı** olarak dizer (``EditorService.edit``)."""
    result: Dict[str, Any] = {}

    def fn(tl: Timeline) -> None:
        result.update(build_timeline(tl, project))
    editor.edit("series.assemble.undo", fn)
    return result
