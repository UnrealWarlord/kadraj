"""Proje veritabanı — ``<Proje>/.kadraj/proje.db`` (SQLite, plan §3 şeması).

Tablolar: ``media``, ``jobs``, ``snapshots``, ``prompts``, ``api_usage``, ``series_state``.
Bağlantı **iş parçacığı başına** açılır (``threading.local``); WAL kipi ve
``foreign_keys`` açıktır. Şema sürümü ``PRAGMA user_version`` ile izlenir;
``MIGRATIONS`` listesine ekleme yapılarak ilerler (geriye dönük değişiklik yok).
Ham SQL yalnızca bu dosyadadır; modüller ``ProjectDB`` yöntemlerini kullanır.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS media (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,                 -- video | audio | image | subtitle | other
    duration    REAL,
    width       INTEGER,
    height      INTEGER,
    fps         REAL,
    codec       TEXT,
    sample_rate INTEGER,
    tags        TEXT NOT NULL DEFAULT '',      -- virgülle ayrılmış
    created     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    status       TEXT NOT NULL,
    progress     REAL NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    error        TEXT,
    created      REAL NOT NULL,
    updated      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS snapshots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES snapshots(id) ON DELETE SET NULL,
    branch    TEXT NOT NULL DEFAULT 'main',
    label     TEXT NOT NULL DEFAULT '',
    path      TEXT NOT NULL,
    created   REAL NOT NULL,
    auto      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS prompts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    model       TEXT NOT NULL DEFAULT '',
    idea        TEXT NOT NULL DEFAULT '',
    prompt      TEXT NOT NULL DEFAULT '',
    negative    TEXT NOT NULL DEFAULT '',
    params_json TEXT NOT NULL DEFAULT '{}',
    favorite    INTEGER NOT NULL DEFAULT 0,
    created     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS api_usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    provider   TEXT NOT NULL,
    model      TEXT NOT NULL DEFAULT '',
    tokens_in  INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    units      REAL NOT NULL DEFAULT 0,
    cost_usd   REAL NOT NULL DEFAULT 0,
    created    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS series_state (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stage           TEXT NOT NULL,
    scene_index     INTEGER NOT NULL DEFAULT 0,
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    updated         REAL NOT NULL
);
"""

#: ``MIGRATIONS[n]`` şemayı sürüm ``n``'den ``n+1``'e taşır. Yeni sürümde buraya
#: bir betik ekleyin ve ``SCHEMA_VERSION``'ı artırın; ``_SCHEMA_V1`` dokunulmaz.
MIGRATIONS: List[str] = [_SCHEMA_V1]


def _now() -> float:
    return time.time()


class ProjectDB:
    """Bir projenin SQLite veritabanı. Thread-güvenli (bağlantı thread başına)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conns: List[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._closed = False
        try:
            self._migrate()
        except BaseException:           # şema reddedildi/bozuk: bağlantıyı sızdırma
            self.close()
            raise

    # -- bağlantı ---------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        if self._closed:
            raise sqlite3.ProgrammingError("Veritabanı kapalı")
        conn: Optional[sqlite3.Connection] = getattr(self._local, "conn", None)
        if conn is None:
            # check_same_thread=False: bağlantıyı yalnızca oluşturan iş parçacığı kullanır (thread-local);
            # ama close() başka iş parçacığından çağrılabilsin ve bağlantı sızmasın.
            conn = sqlite3.connect(str(self.path), timeout=10.0, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.DatabaseError:
                pass
            conn.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = conn
            with self._lock:
                self._conns.append(conn)
        return conn

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for conn in self._conns:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._conns.clear()

    def __enter__(self) -> "ProjectDB":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- şema -------------------------------------------------------------
    @property
    def version(self) -> int:
        return int(self._conn().execute("PRAGMA user_version").fetchone()[0])

    def _migrate(self) -> None:
        conn = self._conn()
        current = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"Proje veritabanı daha yeni bir uygulama sürümünden (şema {current}); "
                f"bu sürüm en çok {SCHEMA_VERSION}'i anlar.")
        for step in range(current, SCHEMA_VERSION):
            conn.executescript(MIGRATIONS[step])
            conn.execute(f"PRAGMA user_version = {step + 1}")

    def tables(self) -> List[str]:
        rows = self._conn().execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        return [r["name"] for r in rows]

    # -- genel yardımcılar --------------------------------------------------
    def _insert(self, sql: str, params: Iterable[Any]) -> int:
        cur = self._conn().execute(sql, tuple(params))
        return int(cur.lastrowid)

    def _rows(self, sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._conn().execute(sql, tuple(params))]

    def _row(self, sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
        r = self._conn().execute(sql, tuple(params)).fetchone()
        return dict(r) if r else None

    # -- media ------------------------------------------------------------
    def add_media(self, path: str, kind: str, *, duration: Optional[float] = None,
                  width: Optional[int] = None, height: Optional[int] = None,
                  fps: Optional[float] = None, codec: Optional[str] = None,
                  sample_rate: Optional[int] = None, tags: str = "") -> int:
        """Medya ekler; aynı yol varsa mevcut kaydı günceller ve kimliğini döner."""
        existing = self._row("SELECT id FROM media WHERE path = ?", (path,))
        if existing:
            self._conn().execute(
                "UPDATE media SET kind=?, duration=?, width=?, height=?, fps=?, codec=?, "
                "sample_rate=?, tags=? WHERE id=?",
                (kind, duration, width, height, fps, codec, sample_rate, tags, existing["id"]))
            return int(existing["id"])
        return self._insert(
            "INSERT INTO media(path, kind, duration, width, height, fps, codec, sample_rate, tags, created)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (path, kind, duration, width, height, fps, codec, sample_rate, tags, _now()))

    def get_media(self, media_id: int) -> Optional[Dict[str, Any]]:
        return self._row("SELECT * FROM media WHERE id = ?", (media_id,))

    def list_media(self, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        if kind:
            return self._rows("SELECT * FROM media WHERE kind = ? ORDER BY id", (kind,))
        return self._rows("SELECT * FROM media ORDER BY id")

    def remove_media(self, media_id: int) -> None:
        self._conn().execute("DELETE FROM media WHERE id = ?", (media_id,))

    # -- jobs -------------------------------------------------------------
    def add_job(self, kind: str, status: str = "queued",
                payload: Optional[Mapping[str, Any]] = None) -> int:
        now = _now()
        return self._insert(
            "INSERT INTO jobs(kind, status, progress, payload_json, created, updated) VALUES (?,?,?,?,?,?)",
            (kind, status, 0.0, json.dumps(dict(payload or {}), ensure_ascii=False), now, now))

    def update_job(self, job_id: int, *, status: Optional[str] = None,
                   progress: Optional[float] = None, error: Optional[str] = None,
                   payload: Optional[Mapping[str, Any]] = None, clear_error: bool = False) -> None:
        sets, vals = ["updated = ?"], [_now()]
        if payload is not None:
            sets.append("payload_json = ?"); vals.append(json.dumps(dict(payload), ensure_ascii=False))
        if clear_error:
            sets.append("error = NULL")
        if status is not None:
            sets.append("status = ?"); vals.append(status)
        if progress is not None:
            sets.append("progress = ?"); vals.append(max(0.0, min(1.0, float(progress))))
        if error is not None:
            sets.append("error = ?"); vals.append(error)
        vals.append(job_id)
        self._conn().execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", vals)

    def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        return self._decode_job(self._row("SELECT * FROM jobs WHERE id = ?", (job_id,)))

    def list_jobs(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = (self._rows("SELECT * FROM jobs WHERE status = ? ORDER BY id", (status,))
                if status else self._rows("SELECT * FROM jobs ORDER BY id"))
        return [self._decode_job(r) for r in rows]          # type: ignore[misc]

    @staticmethod
    def _decode_job(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if row is not None:
            try:
                row["payload"] = json.loads(row.pop("payload_json") or "{}")
            except ValueError:
                row["payload"] = {}
        return row

    # -- snapshots --------------------------------------------------------
    def add_snapshot(self, path: str, *, label: str = "", branch: str = "main",
                     parent_id: Optional[int] = None, auto: bool = False) -> int:
        return self._insert(
            "INSERT INTO snapshots(parent_id, branch, label, path, created, auto) VALUES (?,?,?,?,?,?)",
            (parent_id, branch, label, path, _now(), 1 if auto else 0))

    def get_snapshot(self, snapshot_id: int) -> Optional[Dict[str, Any]]:
        row = self._row("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,))
        if row is not None:
            row["auto"] = bool(row["auto"])
        return row

    def update_snapshot(self, snapshot_id: int, *, path: Optional[str] = None, label: Optional[str] = None,
                        parent_id: Optional[int] = None, set_parent: bool = False) -> None:
        """``set_parent=True`` iken ``parent_id`` (``None`` dahil) yazılır; aksi halde dokunulmaz."""
        sets, vals = [], []
        if path is not None:
            sets.append("path = ?"); vals.append(path)
        if label is not None:
            sets.append("label = ?"); vals.append(label)
        if set_parent:
            sets.append("parent_id = ?"); vals.append(parent_id)
        if sets:
            vals.append(snapshot_id)
            self._conn().execute(f"UPDATE snapshots SET {', '.join(sets)} WHERE id = ?", vals)

    def list_snapshots(self, branch: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = (self._rows("SELECT * FROM snapshots WHERE branch = ? ORDER BY id", (branch,))
                if branch else self._rows("SELECT * FROM snapshots ORDER BY id"))
        for r in rows:
            r["auto"] = bool(r["auto"])
        return rows

    def remove_snapshot(self, snapshot_id: int) -> None:
        self._conn().execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))

    # -- prompts ----------------------------------------------------------
    def add_prompt(self, *, model: str = "", idea: str = "", prompt: str = "",
                   negative: str = "", params: Optional[Mapping[str, Any]] = None,
                   favorite: bool = False) -> int:
        return self._insert(
            "INSERT INTO prompts(model, idea, prompt, negative, params_json, favorite, created)"
            " VALUES (?,?,?,?,?,?,?)",
            (model, idea, prompt, negative, json.dumps(dict(params or {}), ensure_ascii=False),
             1 if favorite else 0, _now()))

    def list_prompts(self, favorites_only: bool = False) -> List[Dict[str, Any]]:
        rows = self._rows("SELECT * FROM prompts WHERE favorite = 1 ORDER BY id DESC" if favorites_only
                          else "SELECT * FROM prompts ORDER BY id DESC")
        for r in rows:
            r["favorite"] = bool(r["favorite"])
            try:
                r["params"] = json.loads(r.pop("params_json") or "{}")
            except ValueError:
                r["params"] = {}
        return rows

    def set_prompt_favorite(self, prompt_id: int, favorite: bool) -> None:
        self._conn().execute("UPDATE prompts SET favorite = ? WHERE id = ?",
                             (1 if favorite else 0, prompt_id))

    # -- api_usage --------------------------------------------------------
    def add_usage(self, provider: str, *, model: str = "", tokens_in: int = 0, tokens_out: int = 0,
                  units: float = 0.0, cost_usd: float = 0.0) -> int:
        return self._insert(
            "INSERT INTO api_usage(provider, model, tokens_in, tokens_out, units, cost_usd, created)"
            " VALUES (?,?,?,?,?,?,?)",
            (provider, model, tokens_in, tokens_out, units, cost_usd, _now()))

    def usage_summary(self, since: Optional[float] = None) -> Dict[str, Any]:
        where, params = ("WHERE created >= ?", (since,)) if since is not None else ("", ())
        row = self._row(
            f"SELECT COALESCE(SUM(tokens_in),0) AS tokens_in, COALESCE(SUM(tokens_out),0) AS tokens_out,"
            f" COALESCE(SUM(units),0) AS units, COALESCE(SUM(cost_usd),0) AS cost_usd,"
            f" COUNT(*) AS calls FROM api_usage {where}", params)
        return row or {}

    # -- series_state -----------------------------------------------------
    def save_series_state(self, stage: str, scene_index: int,
                          checkpoint: Mapping[str, Any]) -> int:
        """Tek satırlık "son durum" mantığıyla: aynı ``stage`` varsa günceller."""
        existing = self._row("SELECT id FROM series_state WHERE stage = ?", (stage,))
        payload = json.dumps(dict(checkpoint), ensure_ascii=False)
        if existing:
            self._conn().execute(
                "UPDATE series_state SET scene_index=?, checkpoint_json=?, updated=? WHERE id=?",
                (scene_index, payload, _now(), existing["id"]))
            return int(existing["id"])
        return self._insert(
            "INSERT INTO series_state(stage, scene_index, checkpoint_json, updated) VALUES (?,?,?,?)",
            (stage, scene_index, payload, _now()))

    def load_series_state(self, stage: Optional[str] = None) -> Optional[Dict[str, Any]]:
        row = (self._row("SELECT * FROM series_state WHERE stage = ?", (stage,)) if stage
               else self._row("SELECT * FROM series_state ORDER BY updated DESC LIMIT 1"))
        if row is not None:
            try:
                row["checkpoint"] = json.loads(row.pop("checkpoint_json") or "{}")
            except ValueError:
                row["checkpoint"] = {}
        return row
