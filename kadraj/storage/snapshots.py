"""Legend snapshot ağacı (``SnapshotAPI``; sözleşme: ``core.contracts`` §9).

* Her snapshot: proje verisinin tam kopyası (``<Proje>/.kadraj/snapshots/<id>.json``) + ``snapshots`` tablosunda
  satır (``id, parent_id, branch, label, path, created, auto``, plan §3). **Ebeveynli ağaç**: yeni snapshot,
  o anki ``head``'in çocuğudur; ``branch_from`` geçmişteki bir snapshot'tan yeni bir **dal** başlatır.
* Geçerli konum (``branch`` + ``head``) proje dosyasında ``extra["snapshots"]`` altında saklanır (tanınmayan
  alanlar korunduğu için ileri uyumludur) ve otomatik kayıtla birlikte yazılır.
* ``restore`` **geri alınabilir**: durum, ``head``'in içeriğinden farklıysa önce "Geri yüklemeden önce" etiketli
  otomatik bir snapshot alınır. Geri yüklemeden sonra ``ctx.undo`` temizlenir, ``Topics.PROJECT_RESTORED`` yayınlanır.
* ``compare(a, b)`` sade Türkçe bir özet + makine okur ``changes`` döner (kanal/klip/işaret/ayar farkları).
* Otomatik snapshot'lar (``auto=True``) ``prune_auto(keep)`` ile budanabilir; elle alınanlara dokunulmaz.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.contracts import (HIDDEN_DIR, Severity, SmartError, SnapshotDiff, SnapshotInfo, Topics)
from ..core.i18n import t
from .project import ProjectData, ProjectSession

SNAP_DIR = "snapshots"
POINTER_KEY = "snapshots"
DEFAULT_BRANCH = "main"
_BRANCH_RE = re.compile(r"^[A-Za-z0-9ÇĞİÖŞÜçğıöşü_.\- ]{1,48}$")


def _err(code: str, **fmt: Any) -> SmartError:
    key = f"snapshot.{code}"
    return SmartError(f"snapshot.{code}", t(f"{key}.title"), t(f"{key}.summary", **fmt), cause=t(f"{key}.cause"),
                      steps=(t(f"{key}.step1"),), severity=Severity.ERROR, source="snapshots")


# ---------------------------------------------------------------------------
# Karşılaştırma
# ---------------------------------------------------------------------------
def _strip(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if k != POINTER_KEY}


def content_digest(d: Dict[str, Any]) -> str:
    """Geçerli-konum işaretçisini saymadan içerik özeti ("içerik aynı mı?" kararı için)."""
    blob = json.dumps(_strip(d), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _tracks(d: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    tracks = (d.get("timeline") or {}).get("tracks") or []
    return {str(tr.get("id", i)): tr for i, tr in enumerate(tracks) if isinstance(tr, dict)}


def _clips(track: Dict[str, Any]) -> Dict[str, Any]:
    return {str(c.get("id", i)): c for i, c in enumerate(track.get("clips") or []) if isinstance(c, dict)}


def diff_projects(a: Dict[str, Any], b: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    """İki proje sözlüğü arasındaki farkın (sade Türkçe satırlar, makine okur sözlük) özeti."""
    a, b = _strip(a), _strip(b)
    lines: List[str] = []
    changes: Dict[str, Any] = {}
    if a.get("name") != b.get("name"):
        changes["name"] = (a.get("name"), b.get("name"))
        lines.append(t("snapshot.diff.name", a=a.get("name"), b=b.get("name")))
    sa, sb = a.get("settings") or {}, b.get("settings") or {}
    sets = {k: (sa.get(k), sb.get(k)) for k in sorted(set(sa) | set(sb)) if sa.get(k) != sb.get(k)}
    if sets:
        changes["settings"] = sets
        lines += [t("snapshot.diff.setting", setting=k, a=v[0], b=v[1]) for k, v in sets.items()]
    ta, tb = _tracks(a), _tracks(b)
    added, removed = sorted(set(tb) - set(ta)), sorted(set(ta) - set(tb))
    if added:
        changes["tracks_added"] = added
        lines.append(t("snapshot.diff.tracks_added", n=len(added)))
    if removed:
        changes["tracks_removed"] = removed
        lines.append(t("snapshot.diff.tracks_removed", n=len(removed)))
    clip_changes: Dict[str, Dict[str, int]] = {}
    for tid in sorted(set(ta) & set(tb)):
        ca, cb = _clips(ta[tid]), _clips(tb[tid])
        add, rem = len(set(cb) - set(ca)), len(set(ca) - set(cb))
        chg = len([c for c in set(ca) & set(cb) if ca[c] != cb[c]])
        if add or rem or chg:
            clip_changes[tid] = {"added": add, "removed": rem, "changed": chg}
            lines.append(t("snapshot.diff.clips", track=tid, added=add, removed=rem, changed=chg))
    if clip_changes:
        changes["clips"] = clip_changes
    ma = {(m.get("t"), m.get("label")) for m in a.get("markers") or [] if isinstance(m, dict)}
    mb = {(m.get("t"), m.get("label")) for m in b.get("markers") or [] if isinstance(m, dict)}
    if ma != mb:
        changes["markers"] = {"added": len(mb - ma), "removed": len(ma - mb)}
        lines.append(t("snapshot.diff.markers", added=len(mb - ma), removed=len(ma - mb)))
    if (a.get("series") or {}) != (b.get("series") or {}):
        changes["series"] = True
        lines.append(t("snapshot.diff.series"))
    known = {"format", "version", "name", "created", "ui_mode_last", "settings", "timeline", "markers", "series"}
    other = sorted(k for k in (set(a) | set(b)) - known if a.get(k) != b.get(k))
    if other:
        changes["other"] = other
        lines.append(t("snapshot.diff.other"))
    # zaman çizelgesinin tracks dışındaki alanları
    tla = {k: v for k, v in (a.get("timeline") or {}).items() if k != "tracks"}
    tlb = {k: v for k, v in (b.get("timeline") or {}).items() if k != "tracks"}
    if tla != tlb:
        changes["timeline_other"] = True
        lines.append(t("snapshot.diff.timeline"))
    return lines, changes


# ---------------------------------------------------------------------------
class SnapshotStore:
    """``SnapshotAPI`` uygulaması. Durum proje oturumunda/diskte; bu sınıf yalnızca erişim katmanıdır."""

    def __init__(self, project: ProjectSession, bus: Any = None, undo: Any = None) -> None:
        self._p = project
        self._bus = bus
        self._undo = undo

    # -- yardımcılar -------------------------------------------------------------
    def _need(self) -> ProjectSession:
        if not self._p.is_open or self._p.data is None:
            raise _err("no_project")
        return self._p

    def _dir(self) -> Path:
        d = self._need().root / HIDDEN_DIR / SNAP_DIR      # type: ignore[operator]
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _info(row: Dict[str, Any]) -> SnapshotInfo:
        return SnapshotInfo(int(row["id"]), row["branch"], row["label"], float(row["created"]),
                            bool(row["auto"]), row["parent_id"])

    def _row(self, snapshot_id: int) -> Dict[str, Any]:
        row = self._need().db.get_snapshot(int(snapshot_id))
        if row is None:
            raise _err("not_found", id=snapshot_id)
        return row

    def _load(self, row: Dict[str, Any]) -> Dict[str, Any]:
        path = self._need().root / row["path"]             # type: ignore[operator]
        try:
            return json.loads(path.read_text(encoding="utf-8"))["project"]
        except (OSError, ValueError, KeyError) as exc:
            raise SmartError("snapshot.file_missing", t("snapshot.file_missing.title"),
                             t("snapshot.file_missing.summary", id=row["id"]), cause=t("snapshot.file_missing.cause"),
                             steps=(t("snapshot.file_missing.step1"),), technical=type(exc).__name__,
                             source="snapshots") from exc

    def _write(self, path: Path, obj: Dict[str, Any]) -> None:
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".snap-", suffix=".tmp", dir=str(path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # -- geçerli konum ---------------------------------------------------------------
    def current(self) -> Tuple[str, Optional[int]]:
        """(dal, head id). Proje dosyasındaki işaretçiden; yoksa ``("main", None)``."""
        data = self._need().data
        ptr = data.extra.get(POINTER_KEY) if data else None          # type: ignore[union-attr]
        if isinstance(ptr, dict):
            head = ptr.get("head")
            return str(ptr.get("branch") or DEFAULT_BRANCH), int(head) if isinstance(head, int) else None
        return DEFAULT_BRANCH, None

    def _set_current(self, branch: str, head: Optional[int]) -> None:
        proj = self._need()
        proj.data.extra[POINTER_KEY] = {"branch": branch, "head": head}   # type: ignore[union-attr]
        proj.mark_dirty()

    # -- SnapshotAPI -----------------------------------------------------------------
    def create(self, label: str = "", *, branch: Optional[str] = None, auto: bool = False) -> SnapshotInfo:
        proj = self._need()
        cur_branch, head = self.current()
        branch = self._check_branch(branch) if branch else cur_branch
        parent = head
        if branch != cur_branch:                                    # başka dala geçiş: o dalın ucunun çocuğu
            rows = proj.db.list_snapshots(branch)
            parent = int(rows[-1]["id"]) if rows else None
        snap_dir = self._dir()
        pending = snap_dir / f"pending-{uuid.uuid4().hex}.json"
        data_dict = proj.data.to_dict()                              # type: ignore[union-attr]
        from time import time as _now
        created = _now()
        self._write(pending, {"label": label, "branch": branch, "created": created, "project": data_dict})
        sid = proj.db.add_snapshot(f"{HIDDEN_DIR}/{SNAP_DIR}/{pending.name}", label=label, branch=branch,
                                   parent_id=parent, auto=auto)
        final = snap_dir / f"{sid}.json"
        os.replace(pending, final)
        proj.db.update_snapshot(sid, path=f"{HIDDEN_DIR}/{SNAP_DIR}/{final.name}")
        self._set_current(branch, sid)
        return self._info(self._row(sid))

    def list(self, branch: Optional[str] = None) -> List[SnapshotInfo]:
        return [self._info(r) for r in self._need().db.list_snapshots(branch)]

    def branches(self) -> List[str]:
        names = {r["branch"] for r in self._need().db.list_snapshots()}
        names.add(DEFAULT_BRANCH)
        names.add(self.current()[0])
        return [DEFAULT_BRANCH] + sorted(n for n in names if n != DEFAULT_BRANCH)

    def tree(self) -> List[Dict[str, Any]]:
        """Kökten yapraklara iç içe liste: ``[{"info": SnapshotInfo, "children": [...]}, ...]``."""
        infos = self.list()
        kids: Dict[Optional[int], List[SnapshotInfo]] = {}
        ids = {i.id for i in infos}
        for i in infos:
            kids.setdefault(i.parent_id if i.parent_id in ids else None, []).append(i)

        def build(parent: Optional[int]) -> List[Dict[str, Any]]:
            return [{"info": i, "children": build(i.id)} for i in kids.get(parent, [])]
        return build(None)

    def restore(self, snapshot_id: int) -> SnapshotInfo:
        proj = self._need()
        row = self._row(snapshot_id)
        target = self._load(row)
        _branch, head = self.current()
        if self._differs_from_head(head):
            self.create(t("snapshot.before_restore"), auto=True)    # geri alınabilirlik
        self._apply(target, row, "snapshot")
        return self._info(row)

    def _differs_from_head(self, head: Optional[int]) -> bool:
        proj = self._need()
        if head is None:
            return True
        row = proj.db.get_snapshot(head)
        if row is None:
            return True
        try:
            old = content_digest(ProjectData.from_dict(self._load(row)).to_dict())
        except SmartError:
            return True
        return old != content_digest(proj.data.to_dict())             # type: ignore[union-attr]

    def _apply(self, project_dict: Dict[str, Any], row: Dict[str, Any], reason: str,
               branch: Optional[str] = None) -> None:
        proj = self._need()
        proj.replace_data(ProjectData.from_dict(project_dict), reason)
        self._set_current(branch or row["branch"], int(row["id"]))
        proj.save(silent=True)
        if self._undo is not None:
            try:
                self._undo.clear()
            except Exception:
                pass
        if self._bus is not None:
            self._bus.emit(Topics.PROJECT_RESTORED, {"reason": reason, "label": row["label"]})

    def _check_branch(self, name: str) -> str:
        name = (name or "").strip()
        if not _BRANCH_RE.match(name):
            raise _err("invalid_branch")
        return name

    def branch_from(self, snapshot_id: int, name: str) -> SnapshotInfo:
        """``snapshot_id``'deki duruma dönüp ``name`` adlı **yeni dalı** başlatır (sonraki snapshot'lar bu dalda)."""
        name = self._check_branch(name)
        if name in self.branches():
            raise _err("branch_exists", name=name)
        row = self._row(snapshot_id)
        target = self._load(row)
        _b, head = self.current()
        if self._differs_from_head(head):
            self.create(t("snapshot.before_restore"), auto=True)
        self._apply(target, row, "snapshot", branch=name)
        return self._info(row)

    def compare(self, a: int, b: int) -> SnapshotDiff:
        lines, changes = diff_projects(self._load(self._row(a)), self._load(self._row(b)))
        return SnapshotDiff(int(a), int(b), lines, changes)

    def compare_with_current(self, snapshot_id: int) -> SnapshotDiff:
        """Snapshot ile **şu anki** proje durumu arasındaki fark (a = snapshot, b = güncel)."""
        proj = self._need()
        lines, changes = diff_projects(self._load(self._row(snapshot_id)), proj.data.to_dict())   # type: ignore[union-attr]
        return SnapshotDiff(int(snapshot_id), -1, lines, changes)

    def rename(self, snapshot_id: int, label: str) -> SnapshotInfo:
        self._row(snapshot_id)
        self._need().db.update_snapshot(snapshot_id, label=label)
        return self._info(self._row(snapshot_id))

    def delete(self, snapshot_id: int) -> bool:
        proj = self._need()
        row = proj.db.get_snapshot(int(snapshot_id))
        if row is None:
            return False
        for child in [r for r in proj.db.list_snapshots() if r["parent_id"] == row["id"]]:
            proj.db.update_snapshot(int(child["id"]), parent_id=row["parent_id"], set_parent=True)   # çocukları yukarı bağla
        proj.db.remove_snapshot(int(row["id"]))
        try:
            (proj.root / row["path"]).unlink(missing_ok=True)       # type: ignore[operator]
        except OSError:
            pass
        branch, head = self.current()
        if head == row["id"]:
            self._set_current(branch, row["parent_id"])
        return True

    def prune_auto(self, keep: int = 20) -> int:
        """En eski otomatik snapshot'ları siler (elle alınanlara dokunmaz); silinen sayısını döner."""
        autos = [r for r in self._need().db.list_snapshots() if r["auto"]]
        removed = 0
        for r in autos[:max(0, len(autos) - keep)]:
            removed += 1 if self.delete(int(r["id"])) else 0
        return removed
