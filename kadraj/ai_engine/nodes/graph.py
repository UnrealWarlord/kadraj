"""Düğüm grafiği modeli (saf Python; Qt/ağ yok). Sekiz düğüm türü: Logline, LLM, Referans/İncil, Görsel, Upscale, Video, Ses, Çıkış.

Bağlantılar girdi tarafında tutulur: ``node.inputs[port] = (kaynak_düğüm, kaynak_port)``. Türler: ``text | image | video | audio | any``
(``any`` her şeyle eşleşir). ``Graph.order()`` topolojik sıradır (Kahn; döngüde ``CycleError``); ``validate()`` insan-okur sorun listesi verir.
"""
from __future__ import annotations

import copy
import random
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


class CycleError(ValueError):
    pass


@dataclass(frozen=True)
class Port:
    name: str
    type: str = "any"                 # text | image | video | audio | any
    required: bool = True


@dataclass(frozen=True)
class NodeSpec:
    type: str
    title: str
    inputs: Tuple[Port, ...]
    outputs: Tuple[Port, ...]
    params: Mapping[str, Any]
    accent: str = "neutral"
    cost: str = ""                    # kullanıcıya gösterilen kabaca maliyet notu


SPECS: Dict[str, NodeSpec] = {s.type: s for s in (
    NodeSpec("logline", "Logline", (), (Port("text", "text"),), {"text": ""}, "pink"),
    NodeSpec("llm", "LLM", (Port("context", "text", False),), (Port("text", "text"),),
             {"prompt": "{context}", "system": "", "temperature": 0.7, "max_tokens": 2000, "json": False, "tier": "cheap"}, "lavender"),
    NodeSpec("reference", "Referans / İncil", (), (Port("text", "text"),), {"character": "", "location": "", "text": ""}, "mint"),
    NodeSpec("image", "Görsel", (Port("prompt", "text"), Port("reference", "image", False)), (Port("image", "image"),),
             {"model": "flux", "aspect": "16:9", "seed": 0, "seed_locked": False, "cfg": 7.0, "steps": 25, "ref_strength": 0.6, "n": 1}, "butter",
             "~$0.04 / görsel"),
    NodeSpec("upscale", "Upscale", (Port("image", "image"),), (Port("image", "image"),), {"factor": 2}, "peach"),
    NodeSpec("video", "Video", (Port("image", "image", False), Port("prompt", "text")), (Port("video", "video"), Port("last_frame", "image")),
             {"seconds": 5.0, "aspect": "16:9", "seed": 0, "seed_locked": False}, "pink", "~$0.10 / sn"),
    NodeSpec("audio", "Ses", (Port("text", "text"),), (Port("audio", "audio"),),
             {"kind": "tts", "voice_id": "", "seconds": 3.0}, "lavender", "~$0.0003 / karakter"),
    NodeSpec("output", "Çıkış", (Port("in", "any"),), (), {"name": "cikti"}, "mint"),
)}
RAW_KEYS = ("prompt", "negative", "json")          # ham çekmece alanları


def compatible(src: str, dst: str) -> bool:
    return src == dst or "any" in (src, dst)


@dataclass
class Node:
    id: str
    type: str
    title: str = ""
    pos: Tuple[float, float] = (0.0, 0.0)
    params: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, str] = field(default_factory=dict)                  # ham çekmece: prompt / negative / json
    inputs: Dict[str, Tuple[str, str]] = field(default_factory=dict)

    @property
    def spec(self) -> NodeSpec:
        return SPECS[self.type]

    def effective_params(self) -> Dict[str, Any]:
        return {**self.spec.params, **self.params}

    def randomize_seed(self) -> int:
        """"RND": yeni rastgele tohum yazar (kilit durumu değişmez)."""
        self.params["seed"] = random.randint(1, 2 ** 31 - 1)
        return int(self.params["seed"])

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "type": self.type, "title": self.title, "pos": list(self.pos), "params": copy.deepcopy(self.params),
                "raw": dict(self.raw), "inputs": {k: list(v) for k, v in self.inputs.items()}}


class Graph:
    def __init__(self) -> None:
        self.nodes: Dict[str, Node] = {}

    # -- düzenleme --------------------------------------------------------------------------------------------
    def add(self, type: str, *, id: Optional[str] = None, title: str = "", pos: Tuple[float, float] = (0.0, 0.0), **params: Any) -> Node:
        if type not in SPECS:
            raise ValueError(f"bilinmeyen düğüm türü: {type}")
        nid = id or self._unique(type)
        if nid in self.nodes:
            raise ValueError(f"düğüm kimliği var: {nid}")
        node = Node(nid, type, title or SPECS[type].title, pos, dict(params))
        self.nodes[nid] = node
        return node

    def _unique(self, type: str) -> str:
        n = 1
        while f"{type}_{n:02d}" in self.nodes:
            n += 1
        return f"{type}_{n:02d}"

    def remove(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)
        for n in self.nodes.values():
            n.inputs = {p: src for p, src in n.inputs.items() if src[0] != node_id}

    def connect(self, src: str, src_port: str, dst: str, dst_port: str) -> None:
        a, b = self.nodes.get(src), self.nodes.get(dst)
        if a is None or b is None:
            raise ValueError("düğüm yok")
        sp = next((p for p in a.spec.outputs if p.name == src_port), None)
        dp = next((p for p in b.spec.inputs if p.name == dst_port), None)
        if sp is None or dp is None:
            raise ValueError("port yok")
        if not compatible(sp.type, dp.type):
            raise ValueError(f"tür uyumsuz: {sp.type} → {dp.type}")
        if src == dst or self._reaches(dst, src):
            raise CycleError(f"{src} → {dst} döngü oluşturur")
        b.inputs[dst_port] = (src, src_port)

    def disconnect(self, dst: str, dst_port: str) -> None:
        if dst in self.nodes:
            self.nodes[dst].inputs.pop(dst_port, None)

    def _reaches(self, start: str, goal: str) -> bool:
        """``start``'ın çıktıları (aşağı yönde) ``goal``'a ulaşır mı?"""
        seen, stack = set(), [start]
        while stack:
            cur = stack.pop()
            if cur == goal:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack += [n.id for n in self.nodes.values() if any(src[0] == cur for src in n.inputs.values())]
        return False

    # -- sorgular ---------------------------------------------------------------------------------------------------
    def order(self) -> List[str]:
        """Topolojik sıra (Kahn). Eşit düzeyde kimliğe göre sıralı → kararlı."""
        deps = {i: {src for src, _p in n.inputs.values() if src in self.nodes} for i, n in self.nodes.items()}
        out: List[str] = []
        pending = sorted(self.nodes)
        while pending:
            ready = [i for i in pending if deps[i] <= set(out)]
            if not ready:
                raise CycleError("grafikte döngü var")
            out.append(ready[0])
            pending.remove(ready[0])
        return out

    def upstream(self, node_id: str) -> List[str]:
        """``node_id``'nin (dolaylı) bağımlılıkları, çalıştırma sırasıyla."""
        need, stack = set(), [node_id]
        while stack:
            cur = stack.pop()
            for src, _p in self.nodes[cur].inputs.values():
                if src in self.nodes and src not in need:
                    need.add(src)
                    stack.append(src)
        return [i for i in self.order() if i in need]

    def validate(self) -> List[str]:
        out: List[str] = []
        try:
            self.order()
        except CycleError:
            out.append("cycle")
        for n in self.nodes.values():
            for port in n.spec.inputs:
                if port.required and port.name not in n.inputs:
                    out.append(f"missing:{n.id}.{port.name}")
        if not any(n.type == "output" for n in self.nodes.values()):
            out.append("no_output")
        return out

    @property
    def connections(self) -> List[Tuple[str, str, str, str]]:
        return [(src, sp, n.id, dp) for n in self.nodes.values() for dp, (src, sp) in n.inputs.items()]

    # -- (de)serileştirme -----------------------------------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {"nodes": [n.to_dict() for n in self.nodes.values()]}

    @classmethod
    def from_dict(cls, d: Optional[Mapping[str, Any]]) -> "Graph":
        g = cls()
        for raw in (d or {}).get("nodes", []):
            if not isinstance(raw, Mapping) or raw.get("type") not in SPECS or not raw.get("id"):
                continue
            n = Node(str(raw["id"]), str(raw["type"]), str(raw.get("title") or SPECS[raw["type"]].title),
                     tuple(raw.get("pos", (0, 0)))[:2], dict(raw.get("params") or {}), {k: str(v) for k, v in (raw.get("raw") or {}).items()},
                     {p: (str(s[0]), str(s[1])) for p, s in (raw.get("inputs") or {}).items() if isinstance(s, (list, tuple)) and len(s) == 2})
            g.nodes[n.id] = n
        for n in g.nodes.values():                                      # kırık bağlantıları temizle
            n.inputs = {p: s for p, s in n.inputs.items() if s[0] in g.nodes}
        return g


def default_flow() -> Graph:
    """Tuvaldeki örnek akış: Logline → LLM → Görsel → Upscale → Video → Çıkış (+ İncil ve Ses)."""
    g = Graph()
    g.add("logline", id="logline_01", pos=(0, 0), text="Sisli bir sabahta İstanbul'da eski bir vapur")
    g.add("reference", id="bible_01", pos=(0, 140))
    g.add("llm", id="llm_01", pos=(220, 0), prompt="Write a one-paragraph English image prompt for: {context}")
    g.add("image", id="gorsel_01", pos=(440, 0))
    g.add("upscale", id="upscale_01", pos=(660, 0))
    g.add("video", id="video_01", pos=(880, 0))
    g.add("audio", id="ses_01", pos=(660, 140), kind="tts")
    g.add("output", id="cikis_01", pos=(1100, 0))
    g.connect("logline_01", "text", "llm_01", "context")
    g.connect("llm_01", "text", "gorsel_01", "prompt")
    g.connect("gorsel_01", "image", "upscale_01", "image")
    g.connect("upscale_01", "image", "video_01", "image")
    g.connect("llm_01", "text", "video_01", "prompt")
    g.connect("logline_01", "text", "ses_01", "text")
    g.connect("video_01", "video", "cikis_01", "in")
    return g
