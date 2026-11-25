"""Son projeler — ``~/.kadraj/recent.json`` (sözleşme: ``RecentAPI``, plan §6).

Biçim: ``{"version": 1, "recent": [{"path", "name", "last_opened", "kind"}]}``.
En yeni başta, en çok ``MAX_RECENT`` kayıt.

* ``get_recent`` diskte olmayan yolları sessizce ayıklar. Dosyaya geri yazmaz; harici
  disk geçici olarak çıkarılmışsa kayıt kaybolmaz.
* Bozuk ya da okunamayan dosya boş liste sayılır. Yazma atomiktir (geçici dosya + ``os.replace``).
* Yazma başarısız olursa işlem sessizce atlanır: son projeler bir kolaylıktır, uygulamayı durdurmaz.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .. import paths
from ..core.contracts import MAX_RECENT, RecentEntry

_log = logging.getLogger(__name__)
PathLike = Union[str, Path]
FORMAT_VERSION = 1


def _resolve(path: PathLike) -> str:
    return str(Path(path).expanduser().resolve())


def _is_dir(path: str) -> bool:
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _read_items() -> List[Dict[str, Any]]:
    try:
        raw = json.loads(paths.recent_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("recent") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict) and isinstance(i.get("path"), str) and i["path"]]


def _write_items(items: List[Dict[str, Any]]) -> None:
    target = paths.recent_file()
    tmp: Optional[str] = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".recent-", suffix=".tmp", dir=str(target.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": FORMAT_VERSION, "recent": items[:MAX_RECENT]}, fh,
                      ensure_ascii=False, indent=2)
        os.replace(tmp, target)
        tmp = None
    except OSError as exc:
        _log.debug("son projeler yazılamadı: %s", exc)
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def get_recent() -> List[RecentEntry]:
    """Diskte hâlâ duran son projeleri en yeniden eskiye döner (en çok ``MAX_RECENT``)."""
    entries = [
        RecentEntry(path=item["path"],
                    name=str(item.get("name") or Path(item["path"]).name),
                    last_opened=_as_float(item.get("last_opened")),
                    kind=str(item.get("kind") or "project"))
        for item in _read_items() if _is_dir(item["path"])
    ]
    entries.sort(key=lambda e: e.last_opened, reverse=True)
    return entries[:MAX_RECENT]


def add_recent(path: PathLike, name: Optional[str] = None) -> None:
    """Projeyi listenin başına ekler (aynı yol varsa öne taşır). Var olmayan klasör eklenmez."""
    resolved = _resolve(path)
    if not _is_dir(resolved):
        return
    items = [i for i in _read_items() if i["path"] != resolved]
    items.insert(0, {"path": resolved, "name": name or Path(resolved).name,
                     "last_opened": time.time(), "kind": "project"})
    _write_items(items)


def forget(path: PathLike) -> None:
    """Projeyi listeden çıkarır (klasöre dokunmaz)."""
    resolved = _resolve(path)
    _write_items([i for i in _read_items() if i["path"] != resolved])


def clear() -> None:
    """Son projeler listesini boşaltır (projelere dokunmaz)."""
    _write_items([])
