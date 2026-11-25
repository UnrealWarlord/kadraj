"""``SeriesService`` — Dizi/Film'in **bütün durumu** (sayfalar yalnızca çizer; plan §0.5).

Durum makinesi ``ai_engine.pipeline.machine``'dedir; burada: kalıcılık (``proje.kadraj`` → ``series.pipeline`` + ``series_state`` checkpoint),
LLM adımlarının arka planda çalıştırılması, **onay kapıları**, storyboard kanban işlemleri, plan/kota tahmini, üretim işi (``series.produce``,
iptal/duraklat/devam), dublaj/foley hazırlığı, editör montajı ve ``.fountain/.pdf`` dışa aktarma.

Kurallar: kullanıcı onaylamadan bir sonraki aşamaya geçilmez; hiçbir üretim kendiliğinden başlamaz (maliyet); kota/hata → üretim o sahnede
donar ve ``Topics.NOTIFY`` ile bildirilir; çıktılar ``04_AI_Scenes`` ve ``05_Scripts``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ...ai_engine import aistrings
from ...ai_engine import analytics
from ...ai_engine.nodes.executor import Executor, Run, validate_raw
from ...ai_engine.nodes.graph import Graph, default_flow
from ...ai_engine.pipeline import assembly, director, quota
from ...ai_engine.pipeline.gateway import AIGateway
from ...ai_engine.pipeline.machine import (CAMERA_ANGLES, RESOLUTIONS, STORY_STATUS, Character, Location, MachineError, PipelineMachine,
                                           Scene, SeriesProject, Stage)
from ...ai_engine.pipeline.production import Producer
from ...ai_engine.providers.aicore import ai_err
from ...ai_engine.providers.install import install_all
from ...core import i18n
from ...core.contracts import Job, JobStatus, ProviderKind, SmartError, Topics
from ...core.errors import translate
from ...storage.folders import folder_for

JOB_PRODUCE = "series.produce"
STATE_STAGE = "pipeline"


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], done: Callable[[Any, Optional[BaseException]], None]) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.fn, self.done = fn, done

    def run(self) -> None:
        try:
            res, err = self.fn(), None
        except BaseException as exc:          # noqa: BLE001
            res, err = None, exc
        self.done(res, err)


class SeriesService(QObject):
    changed = Signal()                     # proje verisi değişti (sayfalar yeniden çizer)
    stageChanged = Signal(str)
    errorRaised = Signal(object)
    notice = Signal(str)
    busyChanged = Signal(bool)
    ideasReady = Signal(list)              # beyin fırtınası sonuçları
    jobChanged = Signal(object)
    assembled = Signal(dict)
    graphChanged = Signal()                # düğüm grafiği değişti
    runFinished = Signal(object)           # Run (düğüm akışı / tek düğüm / matris)
    _async_done = Signal(object, object, object)
    _persist = Signal()                    # iş parçacığından gelen değişiklikleri ana iş parçacığında kaydet

    def __init__(self, ctx: Any = None) -> None:
        super().__init__(ctx if isinstance(ctx, QObject) else None)
        self.ctx = ctx
        self.machine = PipelineMachine(SeriesProject(), on_change=lambda _p: self._persist.emit())
        self.gateway: Optional[AIGateway] = None
        self.last_error: Optional[SmartError] = None
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._busy = 0
        self._unsubs: List[Callable[[], None]] = []
        self._produce_job: Optional[Job] = None
        self._handled: set = set()
        self.graph: Graph = default_flow()
        self.executor: Optional[Executor] = None
        self.runs: List[Run] = []
        self._async_done.connect(self._finish_async)
        self._persist.connect(self._save)

    # -- yaşam döngüsü ---------------------------------------------------------------------------------------
    def start(self, ctx: Any) -> None:
        self.ctx = ctx
        i18n.register_strings(aistrings.STRINGS)
        install_all(ctx.providers, settings=ctx.settings)
        self.gateway = AIGateway(ctx.providers, ctx.vault, ctx.settings, db=lambda: ctx.project.db if ctx.project.is_open else None)
        ctx.jobs.register_handler(JOB_PRODUCE, self._run_produce)
        self._unsubs += [ctx.bus.subscribe(Topics.JOB_UPDATED, self._on_job),
                         ctx.bus.subscribe(Topics.PROJECT_OPENED, lambda _p: self.load()),
                         ctx.bus.subscribe(Topics.PROJECT_CLOSED, lambda _p: self.load())]
        try:
            ctx.project.dataReplaced.connect(lambda _r: self.load())
        except Exception:
            pass
        self.load()

    def stop(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        self._pool.waitForDone(2000)

    @property
    def project(self) -> SeriesProject:
        return self.machine.project

    @property
    def stage(self) -> Stage:
        return self.machine.stage

    @property
    def project_open(self) -> bool:
        return bool(self.ctx is not None and self.ctx.project.is_open)

    def gw(self) -> AIGateway:
        if self.gateway is None:
            raise ai_err("no_llm")
        return self.gateway

    # -- kalıcılık ----------------------------------------------------------------------------------------------
    def load(self) -> None:
        """Proje açılınca: ``proje.kadraj`` ve ``series_state`` checkpoint'inden **daha yeni olanı** yükler (çökme sonrası devam)."""
        proj = SeriesProject()
        if self.project_open and self.ctx.project.data is not None:
            raw = (self.ctx.project.data.series or {}).get(STATE_STAGE)
            cand = SeriesProject.from_dict(raw) if raw else None
            try:
                row = self.ctx.project.db.load_series_state(STATE_STAGE)
            except Exception:
                row = None
            ck = SeriesProject.from_dict(row["checkpoint"]) if row and row.get("checkpoint") else None
            best = max((x for x in (cand, ck) if x is not None), key=lambda x: x.updated, default=None)
            proj = best or proj
        self.machine = PipelineMachine(proj, on_change=lambda _p: self._persist.emit())
        self._load_nodes()
        for sc in self.project.scenes:                  # çöküşte "running" kalan sahne donmuş sayılır → devam edilebilir
            if sc.prod == "running":
                sc.prod = "pending"
        self.changed.emit()
        self.stageChanged.emit(self.stage.value)

    def _save(self) -> None:
        if not self.project_open or self.ctx.project.data is None:
            return
        d = self.project.to_dict()
        self.ctx.project.data.series = {**(self.ctx.project.data.series or {}), STATE_STAGE: d}
        self.ctx.project.mark_dirty()
        try:
            idx = next((i for i, s in enumerate(self.project.scenes) if s.prod != "done"), len(self.project.scenes))
            self.ctx.project.db.save_series_state(STATE_STAGE, idx, d)           # checkpoint (çökme güvenli)
        except Exception:
            pass
        self.changed.emit()

    def _touch(self) -> None:
        self.machine._touch()
        self._save()

    def out_dir(self, kind: str) -> Path:
        return Path(self.ctx.project.subfolder(folder_for(kind))) if self.project_open else Path.home() / "Dizi-Film"

    def _fail(self, exc: BaseException) -> None:
        err = exc if isinstance(exc, SmartError) else translate(exc, {"source": "series"})
        self.last_error = err
        self.errorRaised.emit(err)

    # -- arka plan --------------------------------------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._busy > 0

    def run_async(self, fn: Callable[[], Any], on_done: Callable[[Any], None]) -> None:
        self._busy += 1
        self.busyChanged.emit(True)
        self._pool.start(_Task(fn, lambda res, err: self._async_done.emit(on_done, res, err)))

    def _finish_async(self, on_done: Callable[[Any], None], res: Any, err: Any) -> None:
        self._busy = max(0, self._busy - 1)
        if self._busy == 0:
            self.busyChanged.emit(False)
        if err is not None:
            self._fail(err)
        else:
            on_done(res)

    def _copy(self) -> SeriesProject:
        return SeriesProject.from_dict(self.project.to_dict())

    # ================================================================================== aşama geçişleri
    def advance(self) -> bool:
        """Sonraki aşamaya geç. Engel varsa (onay yok, içerik eksik) sade kart; ``False`` döner."""
        try:
            self.machine.advance()
        except MachineError as exc:
            self._fail(self._blocked(exc.reason))
            return False
        self._save()
        self.stageChanged.emit(self.stage.value)
        return True

    def _blocked(self, reason: str) -> SmartError:
        key = f"series.block.{reason}"
        return SmartError(f"series.blocked.{reason}", i18n.t("series.block.title"), i18n.t(key) if i18n.has(key) else reason,
                          steps=(i18n.t("series.block.step"),), source="series")

    def approve(self, stage: Optional[Stage] = None) -> bool:
        try:
            self.machine.approve(stage)
        except MachineError as exc:
            self._fail(self._blocked(exc.reason))
            return False
        self._save()
        return True

    def reject(self, stage: Optional[Stage] = None) -> None:
        self.machine.reject(stage)
        self._save()
        self.stageChanged.emit(self.stage.value)

    def goto(self, stage: Stage) -> None:
        try:
            self.machine.goto(stage)
        except MachineError as exc:
            self._fail(self._blocked(exc.reason))
            return
        self._save()
        self.stageChanged.emit(self.stage.value)

    # ================================================================================== 1. fikir + beyin fırtınası
    def set_idea(self, idea: str, title: str = "") -> None:
        self.project.idea = " ".join(idea.split())
        if title:
            self.project.title = title.strip()
        self.machine.content_changed(Stage.IDEA)
        self._save()

    def brainstorm(self, n: int = 3) -> None:
        idea = self.project.idea
        self.run_async(lambda: director.brainstorm(self.gw(), idea, n), self.ideasReady.emit)

    def choose_idea(self, idea: Dict[str, str]) -> None:
        self.project.title = idea.get("title", "") or self.project.title
        self.project.logline = idea.get("logline", "")
        self.machine.content_changed(Stage.IDEA)
        self._save()

    # ================================================================================== 2. senaryo
    def write_screenplay(self) -> None:
        copy = self._copy()

        def job():
            sp = director.write_screenplay(self.gw(), copy)
            from ...ai_engine.export.fountain import to_fountain
            return sp, to_fountain(sp)

        def done(res) -> None:
            sp, text = res
            self.project.screenplay = text
            self.project.title = self.project.title or sp.title
            self.machine.content_changed(Stage.SCREENPLAY)
            self._save()
        self.run_async(job, done)

    def set_screenplay(self, text: str) -> None:
        """Elle yazılan/düzenlenen senaryo (LLM yoksa da çalışır). İçerik değişince onay düşer."""
        self.project.screenplay = text
        self.machine.content_changed(Stage.SCREENPLAY)
        self._save()

    def export_script(self, pdf: bool = True) -> Dict[str, str]:
        """``05_Scripts``'e ``.fountain`` (+``.pdf``). Düz metinden ``Screenplay`` kurar (satır başı kuralları)."""
        folder = self.out_dir("script")
        stem = director.slug(self.project.title or "senaryo")
        folder.mkdir(parents=True, exist_ok=True)
        fp = folder / f"{stem}.fountain"
        fp.write_text(self.project.screenplay or "", encoding="utf-8")
        out = {"fountain": str(fp)}
        if pdf:
            from ...ai_engine.export.pdf import build_pdf
            out["pdf"] = str(build_pdf(_screenplay_from_fountain(self.project.title or "Adsız", self.project.screenplay), folder / f"{stem}.pdf"))
        self.project.exports.update(out)
        self._save()
        return out

    # ================================================================================== 3. format
    def set_format(self, kind: str, minutes: float, episodes: int, resolution: str, style: str = "") -> None:
        if kind not in ("dizi", "film") or resolution not in RESOLUTIONS:
            self._fail(self._blocked("missing_format"))
            return
        self.project.format = {"kind": kind, "minutes": float(minutes), "episodes": max(1, int(episodes)) if kind == "dizi" else 1,
                               "resolution": resolution, **({"style": style} if style else {})}
        self.machine.content_changed(Stage.FORMAT)
        self._save()

    # ================================================================================== 4. incil + sahneler
    def write_bible(self) -> None:
        copy = self._copy()

        def job() -> SeriesProject:
            director.write_bible(self.gw(), copy)
            return copy

        def done(res: SeriesProject) -> None:
            self.project.episodes, self.project.characters = res.episodes, res.characters
            self.project.locations, self.project.scenes = res.locations, res.scenes
            self.machine.content_changed(Stage.BIBLE)
            self._save()
        self.run_async(job, done)

    def add_character(self, name: str, desc: str = "", voice_id: str = "") -> Character:
        c = Character(director.slug(name, f"k{len(self.project.characters) + 1}"), name.strip(), desc.strip(), voice_id.strip())
        while any(x.id == c.id for x in self.project.characters):
            c.id += "_"
        self.project.characters.append(c)
        self.machine.content_changed(Stage.BIBLE)
        self._save()
        return c

    def update_character(self, cid: str, **kw: Any) -> None:
        c = self.project.character(cid)
        if c is not None:
            for k, v in kw.items():
                if k in ("name", "desc", "voice_id", "locked"):
                    setattr(c, k, v)
            self.machine.content_changed(Stage.BIBLE)
            self._save()

    def add_scene(self, episode: int, title: str, summary: str, *, location: str = "", characters: Sequence[str] = (), seconds: float = 5.0) -> Scene:
        n = sum(1 for s in self.project.scenes if s.episode == episode) + 1
        sc = Scene(f"E{episode:02d}S{n:02d}", episode, n, title, summary, location, list(characters), seconds=seconds, cut=not self.project.scenes)
        self.project.scenes.append(sc)
        self.machine.content_changed(Stage.BIBLE)
        self._save()
        return sc

    # ================================================================================== 5. storyboard + kanban
    def storyboard_dir(self) -> Path:
        return self.out_dir("ai_scene") / director.slug(self.project.title or "dizi") / "storyboard"

    def generate_keyframes(self, scene_ids: Optional[Sequence[str]] = None) -> None:
        """Seçili (ya da tüm) sahneler için anahtar kare üretir → kanbanda "Onay bekliyor". Maliyet: sahne başına bir görsel."""
        ids = list(scene_ids) if scene_ids else [s.id for s in self.project.scenes if not s.keyframe]
        specs = {sid: director.keyframe_spec(self.project, self.project.scene(sid), self.project.format.get("aspect", "16:9"))
                 for sid in ids if self.project.scene(sid)}
        folder = self.storyboard_dir()
        model = str(self.ctx.settings.get("photo.model") or "flux") if self.ctx else "flux"

        def job() -> Dict[str, str]:
            out: Dict[str, str] = {}
            folder.mkdir(parents=True, exist_ok=True)
            for sid, spec in specs.items():
                res, _ad = self.gw().image(spec, prefer_model=model, n=1)
                p = folder / f"{sid}.png"
                p.write_bytes(res.images[0])
                out[sid] = str(p)
            return out

        def done(paths: Dict[str, str]) -> None:
            for sid, path in paths.items():
                sc = self.project.scene(sid)
                if sc is not None:
                    sc.keyframe, sc.story = path, "review"
            self.machine.content_changed(Stage.STORYBOARD)
            self._save()
        self.run_async(job, done)

    def regenerate_scene(self, scene_id: str) -> None:
        """"Bu sahneyi yeniden yap": storyboard'da yalnız o sahnenin anahtar karesi; üretimdeyse yalnız o sahnenin çıktıları sıfırlanır."""
        sc = self.project.scene(scene_id)
        if sc is None:
            return
        if self.stage in (Stage.PRODUCTION, Stage.ASSEMBLY, Stage.DONE):
            self.machine.reset_scene(scene_id)
            self._save()
        else:
            sc.keyframe, sc.story = "", "draft"
            self.machine.content_changed(Stage.STORYBOARD)
            self._save()
            self.generate_keyframes([scene_id])

    def move_card(self, scene_id: str, status: str) -> None:
        """Kanban: kartı bir sütuna taşır (Taslak · Onay bekliyor · Onaylandı)."""
        sc = self.project.scene(scene_id)
        if sc is None or status not in STORY_STATUS:
            return
        sc.story = status
        self.project.approvals.pop(Stage.STORYBOARD.value, None)
        self._save()

    def reorder(self, scene_id: str, new_index: int) -> None:
        """Sahne sırasını değiştirir (aynı bölüm içinde; kimlikler yeniden numaralanır)."""
        scenes = self.project.scenes
        sc = self.project.scene(scene_id)
        if sc is None:
            return
        scenes.remove(sc)
        scenes.insert(max(0, min(new_index, len(scenes))), sc)
        counters: Dict[int, int] = {}
        for s in scenes:
            counters[s.episode] = counters.get(s.episode, 0) + 1
            s.index = counters[s.episode]
        self.machine.content_changed(Stage.STORYBOARD)
        self._save()

    def set_angle(self, scene_id: str, angle: str) -> None:
        sc = self.project.scene(scene_id)
        if sc is not None and angle in CAMERA_ANGLES:
            sc.angle = angle
            sc.story = "draft" if sc.story == "approved" else sc.story
            self.project.approvals.pop(Stage.STORYBOARD.value, None)
            self._save()

    def columns(self) -> Dict[str, List[Scene]]:
        return {st: [s for s in self.project.scenes if s.story == st] for st in STORY_STATUS}

    # ================================================================================== 6. plan / kota
    def estimate(self) -> quota.Estimate:
        p = self.project
        todo = [s for s in p.scenes if s.prod != "done"]
        langs = 1 + len(p.dub_languages)
        return quota.estimate(images=sum(1 for s in p.scenes if not s.keyframe), video_seconds=sum(s.seconds for s in todo),
                              tts_chars=sum(len(l.text) for s in todo for l in s.lines) * langs,
                              sfx_seconds=sum(c.seconds for s in todo for c in s.foley), llm_tokens=60000,
                              settings=self.ctx.settings if self.ctx else None)

    def plan_share(self) -> quota.PlanShare:
        spent = quota.spent_this_month(self.ctx.project.db if self.project_open else None)
        return quota.plan_share(self.estimate(), spent=spent, settings=self.ctx.settings if self.ctx else None)

    # ================================================================================== 7. üretim
    def produce(self) -> Optional[Job]:
        """Üretim işini kuyruğa atar (kullanıcı eylemi; maliyet!). Önce **plan onaylı** olmalıdır. Donmuşsa kaldığı sahneden sürer."""
        if self.stage is not Stage.PRODUCTION:
            self._fail(self._blocked("needs_approval" if self.stage is Stage.PLAN else "not_in_production"))
            return None
        if self._produce_job is not None and not self._produce_job.finished:
            return self._produce_job
        self.machine.clear_pause()
        self._save()
        done, total = self.project.progress()
        self._produce_job = self.ctx.jobs.submit(JOB_PRODUCE, {"title": self.project.title}, title=i18n.t("series.job.produce", done=done, total=total))
        return self._produce_job

    resume = produce

    def cancel_production(self) -> None:
        if self._produce_job is not None and not self._produce_job.finished:
            self.ctx.jobs.cancel(self._produce_job.id)

    def _run_produce(self, job: Job, jc: Any) -> Dict[str, Any]:
        prod = Producer(self.machine, self.gw(), self.out_dir("ai_scene"), report=jc.report, cancelled=jc.is_cancelled,
                        notify=lambda kind, msg: self._notify(kind, msg), aspect=str(self.project.format.get("aspect", "16:9")))
        res = prod.run()
        if res.error is not None:
            self.last_error = res.error
            self.errorRaised.emit(res.error)
        return {"status": res.status, "done": res.done, "total": res.total, "scene": res.scene,
                "error": res.error.code if res.error else ""}

    def _notify(self, kind: str, msg: str) -> None:
        if self.ctx is not None:
            self.ctx.bus.emit(Topics.NOTIFY, {"text": msg, "level": "warn" if kind in ("paused", "warn") else "info"})

    def _on_job(self, job: Job) -> None:
        if job.kind != JOB_PRODUCE:
            return
        self.jobChanged.emit(job)
        if job.finished and job.id not in self._handled:
            self._handled.add(job.id)
            res = job.payload.get("_result") or {}
            if res.get("status") == "done" and self.stage is Stage.PRODUCTION and not self.machine.blockers():
                self.advance()                                  # tüm sahneler hazır → MONTAJ aşaması
            else:
                self._save()

    def is_paused(self) -> bool:
        return bool(self.project.paused)

    # ================================================================================== 8. dublaj / foley / montaj
    def prepare_dubbing(self, langs: Sequence[str]) -> None:
        copy = self._copy()

        def job() -> SeriesProject:
            director.translate_lines(self.gw(), copy, langs)
            return copy

        def done(res: SeriesProject) -> None:
            for sc, new in zip(self.project.scenes, res.scenes):
                for ln, nl in zip(sc.lines, new.lines):
                    ln.texts.update(nl.texts)
            self.project.dub_languages = list(dict.fromkeys([*self.project.dub_languages, *res.dub_languages]))
            self._save()
        self.run_async(job, done)

    def suggest_foley(self) -> None:
        copy = self._copy()

        def job() -> Dict[str, list]:
            return {sc.id: director.suggest_foley(self.gw(), sc) for sc in copy.scenes if not sc.foley}

        def done(res: Dict[str, list]) -> None:
            for sid, cues in res.items():
                sc = self.project.scene(sid)
                if sc is not None:
                    sc.foley = cues
            self._save()
        self.run_async(job, done)

    def assemble(self) -> Dict[str, Any]:
        """Üretilen sahneleri editör zaman çizelgesine dizer (tek geri alma adımı)."""
        editor = self.ctx.service("editor") if self.ctx else None
        if editor is None:
            self._fail(ai_err("no_video"))
            return {}
        summary = assembly.assemble(editor, self.project)
        if self.stage is Stage.PRODUCTION and not self.machine.blockers():
            self.machine.advance()                      # → MONTAJ
        if self.stage is Stage.ASSEMBLY:
            self.machine.advance()                      # → BİTTİ
        self._save()
        self.stageChanged.emit(self.stage.value)
        self.assembled.emit(summary)
        return summary


    # ================================================================================== düğüm editörü (Legend)
    def _load_nodes(self) -> None:
        raw = ((self.ctx.project.data.series or {}).get("nodes") if self.project_open and self.ctx.project.data else None) or {}
        self.graph = Graph.from_dict(raw.get("graph")) if raw.get("graph") else default_flow()
        self.runs = [Run.from_dict(r) for r in raw.get("runs", []) if isinstance(r, dict)]
        self.executor = None
        self.graphChanged.emit()

    def _save_nodes(self) -> None:
        if not self.project_open or self.ctx.project.data is None:
            return
        series = dict(self.ctx.project.data.series or {})
        series["nodes"] = {"graph": self.graph.to_dict(), "runs": [r.to_dict() for r in self.runs[:30]]}
        self.ctx.project.data.series = series
        self.ctx.project.mark_dirty()

    def node_executor(self) -> Executor:
        if self.executor is None:
            self.executor = Executor(self.gw(), self.out_dir("ai_scene") / "dugumler", project=self.project)
        self.executor.project = self.project
        return self.executor

    def add_node(self, type: str, pos: tuple = (0.0, 0.0)) -> Any:
        node = self.graph.add(type, pos=pos)
        self._save_nodes()
        self.graphChanged.emit()
        return node

    def remove_node(self, node_id: str) -> None:
        self.graph.remove(node_id)
        self._save_nodes()
        self.graphChanged.emit()

    def connect_nodes(self, src: str, sp: str, dst: str, dp: str) -> bool:
        try:
            self.graph.connect(src, sp, dst, dp)
        except Exception as exc:                          # tür uyumsuz / döngü → sade kart
            code = "cycle" if type(exc).__name__ == "CycleError" else "bad_response"
            self._fail(ai_err(code, name="bağlantı", technical=str(exc)))
            return False
        self._save_nodes()
        self.graphChanged.emit()
        return True

    def disconnect_nodes(self, dst: str, dp: str) -> None:
        self.graph.disconnect(dst, dp)
        self._save_nodes()
        self.graphChanged.emit()

    def move_node(self, node_id: str, x: float, y: float) -> None:
        if node_id in self.graph.nodes:
            self.graph.nodes[node_id].pos = (x, y)
            self._save_nodes()

    def set_param(self, node_id: str, key: str, value: Any) -> None:
        if node_id in self.graph.nodes:
            self.graph.nodes[node_id].params[key] = value
            self._save_nodes()
            self.graphChanged.emit()

    def set_raw(self, node_id: str, key: str, text: str) -> None:
        if node_id in self.graph.nodes:
            self.graph.nodes[node_id].raw[key] = text
            self._save_nodes()

    def randomize_seed(self, node_id: str) -> int:
        seed = self.graph.nodes[node_id].randomize_seed()
        self.graph.nodes[node_id].params["seed_locked"] = True
        self._save_nodes()
        self.graphChanged.emit()
        return seed

    def check_raw(self, node_id: str) -> tuple:
        return validate_raw(self.graph.nodes[node_id])

    def _record_run(self, run: Run) -> None:
        self.runs.insert(0, run)
        del self.runs[30:]
        self._save_nodes()
        self.runFinished.emit(run)

    def run_graph(self, only: Optional[str] = None) -> None:
        """Akışı (ya da yalnız ``only`` düğümünü) arka planda çalıştırır; sonuç ``runFinished``. Maliyet yazılır (``api_usage``)."""
        problems = self.graph.validate()
        if problems and only is None:
            self._fail(ai_err("missing_input", node=problems[0], port=""))
            return
        ex = self.node_executor()
        self.run_async(lambda: ex.run(self.graph, only=only), self._record_run)

    def run_matrix(self, node_id: str, x: tuple, y: tuple) -> None:
        ex = self.node_executor()
        self.run_async(lambda: ex.run_matrix(self.graph, node_id, x, y), self._record_run)

    def reset_graph(self) -> None:
        self.graph = default_flow()
        self.executor = None
        self._save_nodes()
        self.graphChanged.emit()

    # -- analitik ---------------------------------------------------------------------------------------------------------
    def analytics(self, days: int = 30) -> analytics.ChartData:
        rows = analytics.fetch_rows(self.ctx.project.db) if self.project_open else []
        budget = float(self.ctx.settings.get("plan.budget_usd") or 0) if self.ctx else 0.0
        return analytics.chart_data(rows, days=days, budget_usd=budget or None)

    def analytics_rows(self) -> List[Dict[str, Any]]:
        return analytics.fetch_rows(self.ctx.project.db) if self.project_open else []


def _screenplay_from_fountain(title: str, text: str):
    """Düz ``.fountain`` metninden (kaba) ``Screenplay`` kurar: ``INT./EXT.`` başlıkları sahne, büyük harfli tek satır karakter, ardındaki satırlar diyalog."""
    from ...ai_engine.export.fountain import Screenplay
    sp = Screenplay(title, "")
    lines = [l.rstrip() for l in (text or "").splitlines()]
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if not ln:
            i += 1
            continue
        if ln.startswith(".") or ln.upper().startswith(("INT.", "EXT.", "İÇ.", "DIŞ.")):
            sp.scene(ln.lstrip("."))
        elif ln.startswith(">"):
            sp.transition(ln.lstrip("> "))
        elif ln.startswith("(") and ln.endswith(")"):
            sp.parenthetical(ln.strip("()"))
        elif ln.lstrip("@") == ln.lstrip("@").upper() and nxt and not nxt.isupper() and len(ln) < 40:
            sp.character(ln.lstrip("@"))
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith("("):
                sp.dialogue(lines[i].strip())
                i += 1
            continue
        else:
            sp.action(ln)
        i += 1
    return sp
