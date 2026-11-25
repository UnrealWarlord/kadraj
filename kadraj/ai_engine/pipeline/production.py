"""Sahne sahne üretim: **son kare → ilk kare devamlılığı**, karakter başına TTS (+ dublaj dilleri), AI foley, **checkpoint/resume**.

Sözleşme: ``Producer.run()`` sahneleri sırayla üretir. Her adım ``scene.done_steps``'e işlenir (video, last_frame, tts:<dil>, foley) → resume'da
**ödenmiş adım tekrarlanmaz**. Kota/anahtar/ağ/sağlayıcı hatasında üretim **o sahnede donar** (``paused``), durum ``series_state``'e yazılır,
bildirim gider; yeni anahtar/kota gelince ``run()`` yeniden çağrılır ve kaldığı sahneden sürer. Ses çıktıları yalnız ``.wav``.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional

from ...core.contracts import SmartError
from ..providers.aicore import ai_err
from . import director
from .gateway import AIGateway
from .machine import PipelineMachine, Scene

# bu hata kodları üretimi dondurur (kullanıcı çözünce devam edilir); ``ai.no_tts`` yalnız sesi atlar
SOFT_SKIP = {"ai.no_tts"}


@dataclass
class ProduceResult:
    status: str                       # done | paused | cancelled
    done: int = 0
    total: int = 0
    scene: str = ""
    error: Optional[SmartError] = None


def extract_last_frame(video: Path, out: Path, ffmpeg: Optional[str] = None) -> Path:
    """Videonun **son karesini** PNG olarak çıkarır (bir sonraki sahnenin başlangıç görüntüsü)."""
    from ...core import components
    exe = ffmpeg or components.ffmpeg_path()
    if not exe:
        raise ai_err("ffmpeg")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-v", "error", "-y", "-sseof", "-0.4", "-i", str(video), "-update", "1", "-q:v", "1", str(out)]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        raise ai_err("ffmpeg", technical=r.stderr.decode("utf-8", "replace")[-300:])
    return out


class Producer:
    def __init__(self, machine: PipelineMachine, gateway: AIGateway, out_dir: Path, *,
                 report: Optional[Callable[[float, str], None]] = None, cancelled: Optional[Callable[[], bool]] = None,
                 notify: Optional[Callable[[str, str], None]] = None, aspect: str = "16:9", ffmpeg: Optional[str] = None) -> None:
        self.m, self.gw, self.out = machine, gateway, Path(out_dir)
        self.report = report or (lambda f, m: None)
        self.cancelled = cancelled or (lambda: False)
        self.notify = notify or (lambda kind, msg: None)
        self.aspect, self.ffmpeg = aspect, ffmpeg

    @property
    def project(self):
        return self.m.project

    def run(self) -> ProduceResult:
        p = self.project
        self.m.clear_pause()
        total = len(p.scenes)
        for n, sc in enumerate(p.scenes):
            if sc.prod == "done":
                continue
            if self.cancelled():
                return ProduceResult("cancelled", p.progress()[0], total, sc.id)
            self.report(n / max(1, total), sc.id)
            self.m.mark(sc.id, "running")
            try:
                self._scene(sc, p.scenes[n - 1] if n else None)
            except _Cancel:
                self.m.mark(sc.id, "pending")
                return ProduceResult("cancelled", p.progress()[0], total, sc.id)
            except SmartError as err:
                self.m.pause(sc.id, err.code, err.summary)
                self.notify("paused", f"{sc.id}: {err.summary}")
                return ProduceResult("paused", p.progress()[0], total, sc.id, err)
            self.m.mark(sc.id, "done")
        self.report(1.0, "done")
        return ProduceResult("done", total, total, "")

    # -- tek sahne ---------------------------------------------------------------------------------------
    def _check(self) -> None:
        if self.cancelled():
            raise _Cancel()

    def _step(self, sc: Scene, name: str) -> bool:
        return name in sc.done_steps

    def _done(self, sc: Scene, name: str) -> None:
        if name not in sc.done_steps:
            sc.done_steps.append(name)
        self.m._touch()

    def _scene(self, sc: Scene, prev: Optional[Scene]) -> None:
        folder = self.out / director.slug(self.project.title or "dizi")
        folder.mkdir(parents=True, exist_ok=True)
        if not self._step(sc, "video"):
            self._check()
            start = self._start_image(sc, prev)
            res = self.gw.video(director.video_prompt(self.project, sc), image=start, seconds=sc.seconds, aspect=self.aspect, seed=sc.seed)
            path = folder / f"{sc.id}.mp4"
            path.write_bytes(res.data)
            sc.clip = str(path)
            self._done(sc, "video")
        if not self._step(sc, "last_frame"):
            self._check()
            sc.last_frame = str(extract_last_frame(Path(sc.clip), folder / f"{sc.id}_last.png", self.ffmpeg))
            self._done(sc, "last_frame")
        for lang in ["orig", *self.project.dub_languages]:
            step = f"tts:{lang}"
            if self._step(sc, step) or not sc.lines:
                continue
            self._check()
            try:
                for i, line in enumerate(sc.lines):
                    text = line.text if lang == "orig" else line.texts.get(lang)
                    if not text:
                        continue
                    char = self.project.character(line.character)
                    res = self.gw.tts(text, voice_id=(char.voice_id if char and char.voice_id else None), language=None if lang == "orig" else lang)
                    wav = folder / "audio" / f"{sc.id}_L{i + 1:02d}_{line.character}_{lang}.wav"
                    wav.parent.mkdir(parents=True, exist_ok=True)
                    wav.write_bytes(res.wav)
                    line.audio[lang] = str(wav)
                self._done(sc, step)
            except SmartError as err:
                if err.code not in SOFT_SKIP:
                    raise
                self._done(sc, step)                                  # TTS sağlayıcısı yok: sessiz geç, üretimi dondurma
                self.notify("warn", f"{sc.id}: {err.summary}")
        if sc.foley and not self._step(sc, "foley"):
            self._check()
            try:
                for i, cue in enumerate(sc.foley):
                    if cue.path:
                        continue
                    res = self.gw.sfx(cue.text, cue.seconds)
                    wav = folder / "audio" / f"{sc.id}_F{i + 1:02d}.wav"
                    wav.parent.mkdir(parents=True, exist_ok=True)
                    wav.write_bytes(res.wav)
                    cue.path = str(wav)
                self._done(sc, "foley")
            except SmartError as err:
                if err.code not in SOFT_SKIP:
                    raise
                self._done(sc, "foley")

    def _start_image(self, sc: Scene, prev: Optional[Scene]) -> Optional[bytes]:
        """Devamlılık: kesme yoksa **önceki sahnenin son karesi**; kesme/ilk sahne ise onaylı anahtar kare; ikisi de yoksa metinden video."""
        if not sc.cut and prev is not None and prev.last_frame and Path(prev.last_frame).is_file():
            return Path(prev.last_frame).read_bytes()
        if sc.keyframe and Path(sc.keyframe).is_file():
            return Path(sc.keyframe).read_bytes()
        return None


class _Cancel(Exception):
    pass
