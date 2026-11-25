"""Dizi/Film üretim hattı **durum makinesi** + proje modeli (saf Python; Qt/ağ yok).

Aşamalar (sıra sabit; her geçiş kuralları denetlenir, **onay gerektirenlerde kullanıcı onayı şart**):

    IDEA → SCREENPLAY* → FORMAT → BIBLE* → STORYBOARD* → PLAN* → PRODUCTION → ASSEMBLY → DONE      (* = kullanıcı onayı)

Her değişiklik JSON-uyumlu ``to_dict()`` ile ``proje.kadraj`` içindeki ``series`` bölümüne ve ``series_state`` tablosuna (**checkpoint**) yazılır;
kota/hata olursa üretim o sahnede **donar** (``paused``), yeni anahtar/kota gelince ``resume`` kaldığı sahneden sürer.
"""
from __future__ import annotations

import copy
import time
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple


class Stage(str, Enum):
    IDEA = "idea"
    SCREENPLAY = "screenplay"
    FORMAT = "format"
    BIBLE = "bible"
    STORYBOARD = "storyboard"
    PLAN = "plan"
    PRODUCTION = "production"
    ASSEMBLY = "assembly"
    DONE = "done"


STAGE_ORDER: Tuple[Stage, ...] = tuple(Stage)
APPROVAL_STAGES = (Stage.SCREENPLAY, Stage.BIBLE, Stage.STORYBOARD, Stage.PLAN)
RESOLUTIONS = {"1080p": (1920, 1080), "2k": (2560, 1440), "4k": (3840, 2160)}
CAMERA_ANGLES = {"wide": "wide establishing shot", "medium": "medium shot", "close": "close-up", "high": "high angle, bird's eye",
                 "eye": "eye level", "low": "low angle, heroic", "ots": "over-the-shoulder shot"}
STORY_STATUS = ("draft", "review", "approved")           # kanban sütunları: Taslak · Onay bekliyor · Onaylandı
PROD_STATUS = ("pending", "running", "done", "failed", "paused")


class MachineError(ValueError):
    """Geçersiz aşama geçişi; ``reason`` makine-okunur kod (``needs_approval``, ``missing_screenplay`` …)."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


@dataclass
class Character:
    id: str
    name: str
    desc: str = ""                   # görsel tarif (İngilizce; her sahnede aynı görünsün diye isteme eklenir)
    voice_id: str = ""               # ElevenLabs ses kimliği
    locked: bool = True              # "Hep aynı": kilitli karakter her sahnede aynı tarifle


@dataclass
class Location:
    id: str
    name: str
    desc: str = ""


@dataclass
class Line:
    character: str                   # karakter kimliği
    text: str
    texts: Dict[str, str] = field(default_factory=dict)      # dil → çeviri (dublaj hazırlığı)
    audio: Dict[str, str] = field(default_factory=dict)      # dil → .wav yolu


@dataclass
class FoleyCue:
    text: str
    at: float = 0.0
    seconds: float = 2.0
    path: str = ""


@dataclass
class Scene:
    id: str                          # "E01S03"
    episode: int
    index: int
    title: str = ""
    summary: str = ""
    location: str = ""
    characters: List[str] = field(default_factory=list)
    lines: List[Line] = field(default_factory=list)
    foley: List[FoleyCue] = field(default_factory=list)
    angle: str = "medium"
    seconds: float = 5.0
    cut: bool = False                # True: önceki sahneyle devamlılık yok (yeni mekân/zaman)
    keyframe: str = ""               # storyboard anahtar kare (görsel yolu)
    story: str = "draft"             # kanban durumu
    prod: str = "pending"            # üretim durumu
    clip: str = ""
    last_frame: str = ""
    done_steps: List[str] = field(default_factory=list)      # "video", "last_frame", "tts:<dil>", "foley"
    seed: Optional[int] = None
    error: str = ""                  # son hata kodu (donma nedeni)


def _from_dict(cls: Any, d: Mapping[str, Any]) -> Any:
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class SeriesProject:
    title: str = ""
    idea: str = ""
    logline: str = ""
    brainstorm: str = ""
    screenplay: str = ""             # .fountain metni
    format: Dict[str, Any] = field(default_factory=dict)    # kind: dizi|film · minutes · episodes · resolution
    episodes: List[Dict[str, Any]] = field(default_factory=list)       # {index, title, synopsis}
    characters: List[Character] = field(default_factory=list)
    locations: List[Location] = field(default_factory=list)
    scenes: List[Scene] = field(default_factory=list)
    dub_languages: List[str] = field(default_factory=list)
    stage: Stage = Stage.IDEA
    approvals: Dict[str, bool] = field(default_factory=dict)
    paused: Dict[str, Any] = field(default_factory=dict)    # {scene, code, message, at}
    exports: Dict[str, str] = field(default_factory=dict)
    updated: float = 0.0

    # -- sorgular ---------------------------------------------------------------------------------------
    def scene(self, scene_id: str) -> Optional[Scene]:
        return next((s for s in self.scenes if s.id == scene_id), None)

    def character(self, cid: str) -> Optional[Character]:
        return next((c for c in self.characters if c.id == cid), None)

    def location(self, lid: str) -> Optional[Location]:
        return next((l for l in self.locations if l.id == lid), None)

    @property
    def total_seconds(self) -> float:
        return sum(s.seconds for s in self.scenes)

    def progress(self) -> Tuple[int, int]:
        """(hazır sahne, toplam sahne) — "12 sahnenin 5'i hazır"."""
        return sum(1 for s in self.scenes if s.prod == "done"), len(self.scenes)

    def next_pending(self) -> Optional[Scene]:
        return next((s for s in self.scenes if s.prod != "done"), None)

    # -- (de)serileştirme ------------------------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["stage"] = self.stage.value
        return d

    @classmethod
    def from_dict(cls, raw: Optional[Mapping[str, Any]]) -> "SeriesProject":
        raw = raw or {}
        p = _from_dict(cls, {k: v for k, v in raw.items() if k not in ("characters", "locations", "scenes", "stage")})
        p.characters = [_from_dict(Character, c) for c in raw.get("characters", []) if isinstance(c, Mapping)]
        p.locations = [_from_dict(Location, c) for c in raw.get("locations", []) if isinstance(c, Mapping)]
        scenes = []
        for s in raw.get("scenes", []):
            if not isinstance(s, Mapping):
                continue
            sc = _from_dict(Scene, {k: v for k, v in s.items() if k not in ("lines", "foley")})
            sc.lines = [_from_dict(Line, l) for l in s.get("lines", []) if isinstance(l, Mapping)]
            sc.foley = [_from_dict(FoleyCue, f) for f in s.get("foley", []) if isinstance(f, Mapping)]
            scenes.append(sc)
        p.scenes = scenes
        try:
            p.stage = Stage(raw.get("stage", "idea"))
        except ValueError:
            p.stage = Stage.IDEA
        return p


class PipelineMachine:
    """Aşama geçişlerini ve onayları denetler. Durum ``self.project`` içindedir; ``changed`` geri çağrısı kaydetme için."""

    def __init__(self, project: Optional[SeriesProject] = None, on_change: Optional[Any] = None) -> None:
        self.project = project or SeriesProject()
        self.on_change = on_change

    def _touch(self) -> None:
        self.project.updated = time.time()
        if self.on_change:
            self.on_change(self.project)

    # -- kurallar ------------------------------------------------------------------------------------------
    @property
    def stage(self) -> Stage:
        return self.project.stage

    def next_stage(self) -> Optional[Stage]:
        i = STAGE_ORDER.index(self.stage)
        return STAGE_ORDER[i + 1] if i + 1 < len(STAGE_ORDER) else None

    def blockers(self, stage: Optional[Stage] = None) -> List[str]:
        """``stage``'den ileri geçmeyi engelleyen nedenler (boşsa geçilebilir)."""
        p, st = self.project, stage or self.stage
        out: List[str] = []
        if st in APPROVAL_STAGES and not p.approvals.get(st.value):
            out.append("needs_approval")
        if st is Stage.IDEA and not (p.idea.strip() or p.logline.strip()):
            out.append("missing_idea")
        if st is Stage.SCREENPLAY and not p.screenplay.strip():
            out.append("missing_screenplay")
        if st is Stage.FORMAT and not ({"kind", "minutes", "episodes", "resolution"} <= set(p.format)):
            out.append("missing_format")
        if st is Stage.BIBLE and not p.scenes:
            out.append("missing_scenes")
        if st is Stage.STORYBOARD and any(s.story != "approved" for s in p.scenes):
            out.append("storyboard_pending")
        if st is Stage.PRODUCTION and any(s.prod != "done" for s in p.scenes):
            out.append("production_incomplete")
        return out

    def can_advance(self) -> bool:
        return self.next_stage() is not None and not self.blockers()

    def advance(self) -> Stage:
        nxt = self.next_stage()
        if nxt is None:
            raise MachineError("finished")
        blockers = self.blockers()
        if blockers:
            raise MachineError(blockers[0], f"{self.stage.value}: {', '.join(blockers)}")
        self.project.stage = nxt
        self._touch()
        return nxt

    def approve(self, stage: Optional[Stage] = None) -> None:
        st = stage or self.stage
        if st not in APPROVAL_STAGES:
            raise MachineError("not_approvable")
        if st is Stage.STORYBOARD and any(s.story != "approved" for s in self.project.scenes):
            raise MachineError("storyboard_pending")
        self.project.approvals[st.value] = True
        self._touch()

    def reject(self, stage: Optional[Stage] = None) -> None:
        """Onayı geri alır; sonraki aşamalardaysak o aşamaya döner (içerik yeniden yazılabilsin)."""
        st = stage or self.stage
        self.project.approvals.pop(st.value, None)
        if STAGE_ORDER.index(self.stage) > STAGE_ORDER.index(st):
            self.project.stage = st
            for later in STAGE_ORDER[STAGE_ORDER.index(st) + 1:]:
                self.project.approvals.pop(later.value, None)
        self._touch()

    def goto(self, stage: Stage) -> None:
        """Yalnız **geri** gidilir (ileri atlama yok)."""
        if STAGE_ORDER.index(stage) > STAGE_ORDER.index(self.stage):
            raise MachineError("cannot_skip")
        self.reject(stage) if stage in APPROVAL_STAGES else setattr(self.project, "stage", stage)
        self._touch()

    # -- içerik değişimi onayı düşürür ---------------------------------------------------------------------------
    def content_changed(self, stage: Stage) -> None:
        """``stage`` içeriği düzenlendi → o aşamanın (ve sonrakilerin) onayı geçersiz."""
        for later in STAGE_ORDER[STAGE_ORDER.index(stage):]:
            self.project.approvals.pop(later.value, None)
        if STAGE_ORDER.index(self.stage) > STAGE_ORDER.index(stage) and stage in APPROVAL_STAGES:
            self.project.stage = stage
        self._touch()

    # -- üretim durumu ------------------------------------------------------------------------------------------
    def mark(self, scene_id: str, prod: str, *, error: str = "") -> None:
        sc = self.project.scene(scene_id)
        if sc is not None:
            sc.prod, sc.error = prod, error
            self._touch()

    def pause(self, scene_id: str, code: str, message: str = "") -> None:
        self.project.paused = {"scene": scene_id, "code": code, "message": message, "at": time.time()}
        self.mark(scene_id, "paused", error=code)

    def clear_pause(self) -> None:
        if self.project.paused:
            sc = self.project.scene(self.project.paused.get("scene", ""))
            if sc is not None and sc.prod in ("paused", "failed"):
                sc.prod, sc.error = "pending", ""
            self.project.paused = {}
            self._touch()

    def reset_scene(self, scene_id: str) -> None:
        """Tek sahneyi yeniden yaptır: üretim çıktıları sıfırlanır (başka sahneler dokunulmaz)."""
        sc = self.project.scene(scene_id)
        if sc is not None:
            sc.prod, sc.clip, sc.last_frame, sc.done_steps, sc.error = "pending", "", "", [], ""
            if self.stage in (Stage.ASSEMBLY, Stage.DONE):
                self.project.stage = Stage.PRODUCTION
            self._touch()
