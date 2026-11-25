"""Düğüm grafiği yürütücüsü: topolojik sırayla çalıştırır, çıktıları **önbelleğe** alır ("yalnız bu düğümü çalıştır" önbellekteki girdileri kullanır),
**seed kilidi + RND**, ham çekmece (prompt / negatif / JSON), **Batch varyasyon matrisi** (X: seed, Y: cfg …) ve **çalıştırma geçmişi** (zaman · düğüm ·
model · seed · süre · maliyet · durum) üretir. Her düğümün maliyeti ``AIGateway`` oturum sayacından ölçülür (``api_usage``'a zaten yazılır).
"""
from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ...core.contracts import SmartError
from ...core.errors import translate
from ..pipeline.gateway import AIGateway
from ..pipeline.production import extract_last_frame
from ..providers.aicore import ai_err
from .graph import RAW_KEYS, Graph, Node

MAX_MATRIX_CELLS = 16
HISTORY_LIMIT = 60


@dataclass
class NodeResult:
    node: str
    type: str = ""
    status: str = "idle"                  # done | error | skipped | cached
    outputs: Dict[str, Any] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    seconds: float = 0.0
    cost: float = 0.0
    tokens: int = 0
    error: str = ""
    error_code: str = ""
    started: float = 0.0


@dataclass
class Run:
    id: str
    mode: str                              # all | node | matrix
    started: float
    results: Dict[str, NodeResult] = field(default_factory=dict)
    status: str = "done"                   # done | error | cancelled
    matrix: Optional[Dict[str, Any]] = None

    @property
    def cost(self) -> float:
        return round(sum(r.cost for r in self.results.values()), 6)

    def rows(self) -> List[NodeResult]:
        return list(self.results.values())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["results"] = {k: asdict(v) for k, v in self.results.items()}
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Run":
        run = cls(str(d.get("id", "")), str(d.get("mode", "all")), float(d.get("started", 0)), {}, str(d.get("status", "done")), d.get("matrix"))
        for k, v in (d.get("results") or {}).items():
            run.results[k] = NodeResult(**{f: v[f] for f in NodeResult.__dataclass_fields__ if f in v})
        return run


def validate_raw(node: Node) -> Tuple[bool, str]:
    """"Doğrula": ham JSON geçerli bir nesne mi? ``(ok, ileti)``."""
    txt = node.raw.get("json", "").strip()
    if txt:
        try:
            data = json.loads(txt)
        except ValueError as exc:
            return False, f"JSON hatası: {exc}"
        if not isinstance(data, dict):
            return False, "JSON bir nesne ({...}) olmalı"
    return True, "ok"


class Executor:
    def __init__(self, gateway: AIGateway, workdir: Path, *, project: Any = None,
                 progress: Optional[Callable[[float, str], None]] = None, cancelled: Optional[Callable[[], bool]] = None,
                 ffmpeg: Optional[str] = None) -> None:
        self.gw, self.workdir, self.project = gateway, Path(workdir), project
        self.progress = progress or (lambda f, m: None)
        self.cancelled = cancelled or (lambda: False)
        self.ffmpeg = ffmpeg
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.history: List[Run] = []
        self.handlers: Dict[str, Callable[[Node, Dict[str, Any], Dict[str, Any], NodeResult], Dict[str, Any]]] = {
            "logline": self._logline, "llm": self._llm, "reference": self._reference, "image": self._image, "upscale": self._upscale,
            "video": self._video, "audio": self._audio, "output": self._output}

    # -- çalıştırma -----------------------------------------------------------------------------------------------
    def run(self, graph: Graph, only: Optional[str] = None, overrides: Optional[Mapping[str, Mapping[str, Any]]] = None) -> Run:
        """``only=None``: tüm akış. ``only=id``: yalnız o düğüm, girdileri **önbellekten**."""
        run = Run(uuid.uuid4().hex[:8], "node" if only else "all", time.time())
        order = [only] if only else graph.order()
        failed = False
        for n, nid in enumerate(order):
            node = graph.nodes[nid]
            if self.cancelled():
                run.status = "cancelled"
                break
            res = NodeResult(nid, node.type, started=time.time())
            run.results[nid] = res
            if failed:
                res.status = "skipped"
                continue
            self.progress(n / max(1, len(order)), nid)
            self._exec(graph, node, res, dict((overrides or {}).get(nid, {})), only is not None)
            if res.status == "error":
                failed = True
                run.status = "error"
        self.progress(1.0, "done")
        self._remember(run)
        return run

    def _inputs(self, graph: Graph, node: Node, only: bool, res: NodeResult) -> Dict[str, Any]:
        vals: Dict[str, Any] = {}
        for port in node.spec.inputs:
            src = node.inputs.get(port.name)
            if src is None:
                if port.required and not (node.raw.get("prompt") and port.name == "prompt"):
                    raise ai_err("missing_input", node=node.id, port=port.name)
                continue
            out = self.cache.get(src[0])
            if out is None or src[1] not in out:
                raise ai_err("missing_input", node=node.id, port=port.name)
            vals[port.name] = out[src[1]]
        return vals

    def _exec(self, graph: Graph, node: Node, res: NodeResult, override: Dict[str, Any], only: bool) -> None:
        cost0, tok0 = self.gw.session_cost, self.gw.session_tokens
        t0 = time.time()
        try:
            ok, msg = validate_raw(node)
            if not ok:
                raise ai_err("bad_response", name=node.title, technical=msg)
            params = node.effective_params()
            if node.raw.get("json", "").strip():
                params.update(json.loads(node.raw["json"]))
            params.update(override)
            if "seed" in node.spec.params and not params.get("seed_locked"):
                params["seed"] = random.randint(1, 2 ** 31 - 1)
            inputs = self._inputs(graph, node, only, res)
            res.params = dict(params)
            res.outputs = self.handlers[node.type](node, params, inputs, res)
            res.status = "done"
            self.cache[node.id] = res.outputs
        except SmartError as err:
            res.status, res.error, res.error_code = "error", err.summary, err.code
        except Exception as exc:                          # noqa: BLE001
            err = translate(exc, {"source": "nodes"})
            res.status, res.error, res.error_code = "error", err.summary, err.code
        res.seconds = round(time.time() - t0, 3)
        res.cost = round(self.gw.session_cost - cost0, 6)
        res.tokens = self.gw.session_tokens - tok0
        res.provider = res.provider or self.gw.last_provider

    def _remember(self, run: Run) -> None:
        self.history.insert(0, run)
        del self.history[HISTORY_LIMIT:]

    # -- Batch varyasyon matrisi -----------------------------------------------------------------------------------------
    def run_matrix(self, graph: Graph, node_id: str, x: Tuple[str, Sequence[Any]], y: Tuple[str, Sequence[Any]]) -> Run:
        """X ve Y parametrelerinin kombinasyonlarını **yalnız bu düğüm** için çalıştırır (ör. X=seed, Y=cfg). Hücre sayısı sınırlıdır (maliyet)."""
        xp, xv = x[0], list(x[1])
        yp, yv = y[0], list(y[1])
        if len(xv) * len(yv) > MAX_MATRIX_CELLS:
            raise ValueError(f"matris en çok {MAX_MATRIX_CELLS} hücre olabilir")
        run = Run(uuid.uuid4().hex[:8], "matrix", time.time(), matrix={"x": [xp, xv], "y": [yp, yv], "cells": []})
        node = graph.nodes[node_id]
        for j, b in enumerate(yv):
            row = []
            for i, a in enumerate(xv):
                if self.cancelled():
                    run.status = "cancelled"
                    break
                ov: Dict[str, Any] = {xp: a, yp: b}
                if "seed" in (xp, yp):
                    ov["seed_locked"] = True
                res = NodeResult(node_id, node.type, started=time.time())
                self._exec(graph, node, res, ov, True)
                run.results[f"{node_id}@{i},{j}"] = res
                row.append(f"{node_id}@{i},{j}")
                if res.status == "error":
                    run.status = "error"
            run.matrix["cells"].append(row)             # type: ignore[index]
            if run.status in ("error", "cancelled"):
                break
        self._remember(run)
        return run

    # -- düğüm işleyicileri -------------------------------------------------------------------------------------------------
    def _path(self, node: Node, ext: str) -> Path:
        self.workdir.mkdir(parents=True, exist_ok=True)
        stem = f"{node.id}_{time.strftime('%H%M%S')}"
        cand, n = self.workdir / f"{stem}{ext}", 2
        while cand.exists():
            cand = self.workdir / f"{stem}-{n}{ext}"
            n += 1
        return cand

    def _logline(self, node, params, inputs, res) -> Dict[str, Any]:
        return {"text": str(params.get("text", ""))}

    def _reference(self, node, params, inputs, res) -> Dict[str, Any]:
        p = self.project
        if p is None:
            return {"text": str(params.get("text", ""))}
        parts: List[str] = []
        for c in p.characters:
            if params.get("character") in ("", None, c.id):
                parts.append(f"{c.name}: {c.desc}")
        for l in p.locations:
            if params.get("location") in ("", None, l.id):
                parts.append(f"{l.name}: {l.desc}")
        return {"text": "\n".join(x for x in parts if x.strip(": ")) or str(params.get("text", ""))}

    def _llm(self, node, params, inputs, res) -> Dict[str, Any]:
        template = node.raw.get("prompt") or str(params.get("prompt", "{context}"))
        text = template.replace("{context}", str(inputs.get("context", "")))
        r = self.gw.llm(text, system=str(params.get("system", "")), tier=str(params.get("tier", "cheap")), want_json=bool(params.get("json")),
                        temperature=float(params.get("temperature", 0.7)), max_tokens=int(params.get("max_tokens", 2000)))
        res.model = r.model
        res.provider = r.provider
        return {"text": r.text}

    def _prompt_for(self, node: Node, inputs: Mapping[str, Any]) -> str:
        return node.raw.get("prompt") or str(inputs.get("prompt", ""))

    def _image(self, node, params, inputs, res) -> Dict[str, Any]:
        prompt = self._prompt_for(node, inputs)
        extra = {"aspect": params.get("aspect", "16:9"), "cfg": float(params.get("cfg", 7.0)), "steps": int(params.get("steps", 25)),
                 "negative": node.raw.get("negative", "")}
        ref = inputs.get("reference")
        out = None
        if ref and Path(str(ref)).is_file():
            out = self.gw.img2img(Path(str(ref)).read_bytes(), prompt, strength=float(params.get("ref_strength", 0.6)))
        if out is None:
            out, _ad = self.gw.image(prompt, prefer_model=str(params.get("model", "flux")), n=int(params.get("n", 1)),
                                     seed=int(params["seed"]), extra=extra)
        res.provider, res.model = out.provider, out.model
        paths = []
        for data in out.images:
            p = self._path(node, ".png")
            p.write_bytes(data)
            paths.append(str(p))
        return {"image": paths[0], "images": paths, "prompt": prompt}

    def _upscale(self, node, params, inputs, res) -> Dict[str, Any]:
        src = Path(str(inputs["image"]))
        factor = 4 if int(params.get("factor", 2)) >= 4 else 2
        out = self.gw.upscale(src.read_bytes(), factor)
        if out is not None and out.images:
            data, res.provider = out.images[0], out.provider
        else:
            from ...modules.photo import imageops
            data, res.provider = imageops.lanczos_upscale(src, factor), "local"
        p = self._path(node, ".png")
        p.write_bytes(data)
        return {"image": str(p)}

    def _video(self, node, params, inputs, res) -> Dict[str, Any]:
        img = inputs.get("image")
        r = self.gw.video(self._prompt_for(node, inputs), image=Path(str(img)).read_bytes() if img and Path(str(img)).is_file() else None,
                          seconds=float(params.get("seconds", 5)), aspect=str(params.get("aspect", "16:9")), seed=int(params["seed"]))
        res.provider, res.model = r.provider, r.usage.model
        p = self._path(node, ".mp4")
        p.write_bytes(r.data)
        out: Dict[str, Any] = {"video": str(p)}
        try:
            out["last_frame"] = str(extract_last_frame(p, p.with_suffix(".last.png"), self.ffmpeg))
        except SmartError:
            pass                                          # ffmpeg yoksa video yine geçerli
        return out

    def _audio(self, node, params, inputs, res) -> Dict[str, Any]:
        text = node.raw.get("prompt") or str(inputs.get("text", ""))
        if params.get("kind") == "sfx":
            r = self.gw.sfx(text, float(params.get("seconds", 3)))
        else:
            r = self.gw.tts(text, voice_id=params.get("voice_id") or None)
        res.provider = r.provider
        p = self._path(node, ".wav")
        p.write_bytes(r.wav)
        return {"audio": str(p)}

    def _output(self, node, params, inputs, res) -> Dict[str, Any]:
        value = inputs.get("in")
        out_dir = self.workdir / "cikti"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = str(params.get("name", "cikti"))
        if isinstance(value, str) and Path(value).is_file():
            dst = out_dir / f"{name}{Path(value).suffix}"
            n = 2
            while dst.exists():
                dst = out_dir / f"{name}-{n}{Path(value).suffix}"
                n += 1
            dst.write_bytes(Path(value).read_bytes())
        else:
            dst = out_dir / f"{name}.txt"
            dst.write_text(str(value), encoding="utf-8")
        return {"files": [str(dst)]}
