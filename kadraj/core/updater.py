"""Bileşen güncelleyici: yt-dlp, FFmpeg (+ffprobe) ve Deno'yu açılışta kendi kendine günceller.

Kurallar (``docs/DECISIONS.md`` → "Açılışta bileşen güncelleme"):

* Yalnızca **HTTPS** ve **kodda yazılı resmî kaynaklar** (aşağıdaki ``*_API``); yönlendirmeler de bu sunucu
  listesinden (``ALLOWED_HOSTS``) çıkamaz.
* İndirilen her dosyanın **SHA-256**'sı, aynı sürümün yayımladığı sağlama dosyasıyla eşleşmeden kullanılmaz.
* **Atomik değiştirme**: önce hazırlık klasörüne açılır, çalıştırılarak denenir (``-version``), sonra yerine ``os.replace``
  ile konur; eski sürüm ``.prev/`` altında **geri alma** için saklanır.
* **Kısa zaman aşımları**; ağ yoksa sessizce atlanır (hata değil). Büyük indirmeler (FFmpeg, Deno) arka planda sürer;
  Windows'ta kullanımda olan dosya değiştirilemezse yeni sürüm ``.pending/`` altında bekler, **sonraki açılışta** konur.
* FFmpeg yalnızca **sürüm** (ör. 9.0 → 9.1) değişince indirilir; günlük yeniden derlemeler önemsenmez.

Qt'ye bağlı değildir; ``Updater.run`` bir iş parçacığında çağrılır, ilerlemeyi ``progress(metin, oran|None)`` ile bildirir.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .. import device
from . import components

YTDLP_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
DENO_API = "https://api.github.com/repos/denoland/deno/releases/latest"
FFMPEG_API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/latest"
#: FFmpeg derlemesi: editör libx264/libx265 kullanır → "gpl". (LGPL derlemede bu kodlayıcılar yoktur.)
FFMPEG_VARIANT = "gpl"
ALLOWED_HOSTS = ("api.github.com", "github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com", "github-releases.githubusercontent.com")

API_TIMEOUT = 5.0            # sn; sürüm sorgusu
DOWNLOAD_TIMEOUT = 20.0      # sn; her okuma için (toplam değil)
MIN_INTERVAL = 6 * 3600      # sn; açılışta iki denetim arası en az (elle "Şimdi güncelle" bunu yok sayar)
MAX_BYTES = {"yt-dlp": 20 << 20, "deno": 150 << 20, "ffmpeg": 400 << 20}

Progress = Callable[[str, Optional[float]], None]


@dataclass
class Release:
    group: str                     # "yt-dlp" | "deno" | "ffmpeg"
    version: str
    url: str
    sha256: str
    asset: str = ""


@dataclass
class Report:
    updated: List[str] = field(default_factory=list)      # bu çalıştırmada yerine konanlar
    pending: List[str] = field(default_factory=list)      # indirildi, sonraki açılışta konacak
    current: List[str] = field(default_factory=list)      # zaten güncel
    errors: List[str] = field(default_factory=list)       # "ad: sade neden"
    offline: bool = False
    skipped: bool = False                                  # son denetimden beri süre dolmadı / kapalı


# ---------------------------------------------------------------------------
# Ağ (yalnızca HTTPS + izinli sunucular)
# ---------------------------------------------------------------------------
class UpdateError(Exception):
    """Güvenlik/bütünlük ihlali ya da bozuk yanıt (ağ yokluğu değil)."""


def _context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def check_url(url: str) -> str:
    u = urlparse(url)
    if u.scheme != "https" or (u.hostname or "").lower() not in ALLOWED_HOSTS:
        raise UpdateError(f"izin verilmeyen adres: {url}")
    return url


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, timeout: float):
    check_url(url)
    opener = urllib.request.build_opener(_SafeRedirect, urllib.request.HTTPSHandler(context=_context()))
    req = urllib.request.Request(url, headers={"User-Agent": "kadraj-updater", "Accept": "application/vnd.github+json, */*"})
    return opener.open(req, timeout=timeout)


def http_json(url: str, timeout: float = API_TIMEOUT) -> Any:
    with _open(url, timeout) as r:
        return json.loads(r.read(4 << 20).decode("utf-8"))


def http_text(url: str, timeout: float = API_TIMEOUT) -> str:
    with _open(url, timeout) as r:
        return r.read(1 << 20).decode("utf-8", "replace")


def http_download(url: str, dest: Path, *, max_bytes: int, progress: Optional[Callable[[float], None]] = None,
                  timeout: float = DOWNLOAD_TIMEOUT) -> str:
    """``dest``'e indirir; SHA-256 (hex) döner. ``max_bytes`` aşılırsa ``UpdateError``."""
    h = hashlib.sha256()
    done = 0
    with _open(url, timeout) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            done += len(chunk)
            if done > max_bytes:
                raise UpdateError("beklenenden büyük dosya")
            h.update(chunk)
            f.write(chunk)
            if progress and total:
                progress(min(1.0, done / total))
    return h.hexdigest()


def is_offline(exc: BaseException) -> bool:
    """Ağ yokluğu/zaman aşımı → sessizce atla."""
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ssl.SSLError)) \
        and not isinstance(exc, (PermissionError, FileNotFoundError, UpdateError))


# ---------------------------------------------------------------------------
# Sağlama dosyaları ve platform
# ---------------------------------------------------------------------------
_HEX64 = re.compile(r"\b([0-9a-fA-F]{64})\b")


def parse_sums(text: str) -> Dict[str, str]:
    """``<sha256>  <ad>`` / ``<sha256> *<ad>`` satırlarından ``{ad: sha256}``."""
    out: Dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m:
            out[os.path.basename(m.group(2))] = m.group(1).lower()
    return out


def _platform() -> Optional[Dict[str, str]]:
    if device.is_android():
        return None                      # Android: yalnız yt-dlp (bkz. Updater.run)
    if sys.platform == "win32":
        return {"ff": "win64", "ff_ext": ".zip", "deno": "x86_64-pc-windows-msvc"}
    if sys.platform.startswith("linux") and os.uname().machine in ("x86_64", "amd64"):
        return {"ff": "linux64", "ff_ext": ".tar.xz", "deno": "x86_64-unknown-linux-gnu"}
    return None


def _vkey(v: str) -> Tuple[int, ...]:
    return components._version_key(v.lstrip("vn"))


# ---------------------------------------------------------------------------
# Sürüm sorguları
# ---------------------------------------------------------------------------
def _asset(release: Dict[str, Any], name: str) -> Optional[str]:
    for a in release.get("assets") or []:
        if a.get("name") == name:
            return str(a.get("browser_download_url") or "")
    return None


def latest_ytdlp(get_json=http_json, get_text=http_text) -> Release:
    rel = get_json(YTDLP_API)
    url, sums = _asset(rel, "yt-dlp"), _asset(rel, "SHA2-256SUMS")
    if not url or not sums:
        raise UpdateError("yt-dlp yayınında beklenen dosyalar yok")
    digest = parse_sums(get_text(sums)).get("yt-dlp")
    if not digest:
        raise UpdateError("yt-dlp sağlama dosyasında 'yt-dlp' satırı yok")
    return Release("yt-dlp", str(rel.get("tag_name") or "").lstrip("v"), url, digest, "yt-dlp")


def latest_deno(plat: Dict[str, str], get_json=http_json, get_text=http_text) -> Release:
    rel = get_json(DENO_API)
    name = f"deno-{plat['deno']}.zip"
    url, sumfile = _asset(rel, name), _asset(rel, name + ".sha256sum")
    if not url or not sumfile:
        raise UpdateError("Deno yayınında beklenen dosyalar yok")
    m = _HEX64.search(get_text(sumfile))
    if not m:
        raise UpdateError("Deno sağlama dosyası okunamadı")
    return Release("deno", str(rel.get("tag_name") or "").lstrip("v"), url, m.group(1).lower(), name)


_FF_RE = re.compile(r"^ffmpeg-n(\d+)\.(\d+)-latest-(?P<plat>\w+)-(?P<var>\w+)-\d+\.\d+(?P<ext>\.zip|\.tar\.xz)$")


def latest_ffmpeg(plat: Dict[str, str], get_json=http_json, get_text=http_text) -> Release:
    """BtbN sürüm-dalı derlemesi; sürüm = dal numarası (ör. ``9.0``). 'master' derlemesi kullanılmaz."""
    rel = get_json(FFMPEG_API)
    best: Optional[Tuple[Tuple[int, int], str, str]] = None
    for a in rel.get("assets") or []:
        name = str(a.get("name") or "")
        m = _FF_RE.match(name)
        if not m or m["plat"] != plat["ff"] or m["var"] != FFMPEG_VARIANT or m["ext"] != plat["ff_ext"]:
            continue
        key = (int(m.group(1)), int(m.group(2)))
        if best is None or key > best[0]:
            best = (key, name, str(a.get("browser_download_url") or ""))
    sums = _asset(rel, "checksums.sha256")
    if best is None or not sums:
        raise UpdateError("FFmpeg yayınında uygun derleme ya da sağlama dosyası yok")
    digest = parse_sums(get_text(sums)).get(best[1])
    if not digest:
        raise UpdateError("FFmpeg sağlama dosyasında arşiv satırı yok")
    return Release("ffmpeg", f"{best[0][0]}.{best[0][1]}", best[2], digest, best[1])


# ---------------------------------------------------------------------------
# Disk: sürüm kaydı, hazırlık, atomik yerleştirme, geri alma
# ---------------------------------------------------------------------------
def _read_json(p: Path) -> Dict[str, Any]:
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(p: Path, data: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _members(group: str) -> Tuple[str, ...]:
    return tuple(components.exe_name(n) for n in components.GROUPS[group])


def installed_version(group: str) -> str:
    """Etkin sürüm: güncellenmiş ve paketle gelen kopyaların en yenisi (env/sistem kopyaları dikkate alınmaz)."""
    best = ""
    for source in (components.SOURCE_UPDATED, components.SOURCE_BUNDLED):
        root = components.tools_dir() if source == components.SOURCE_UPDATED else components.bundled_dir()
        if root is None or not all((root / m).is_file() for m in _members(group)):
            continue
        v = components.recorded_version(group, source)
        if _vkey(v) > _vkey(best) or not best:
            best = v or best
    if group == "yt-dlp":
        mod = components.module_ytdlp_version()
        if _vkey(mod) > _vkey(best):
            best = mod
    return best


def _smoke(group: str, files: Dict[str, Path]) -> None:
    """Hazırlıktaki dosyaları çalıştırarak dener; olmazsa ``UpdateError``."""
    if group == "yt-dlp":
        p = files["yt-dlp"]
        try:
            with zipfile.ZipFile(p) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile as exc:
            raise UpdateError("yt-dlp dosyası bozuk") from exc
        if "yt_dlp/__init__.py" not in names or "__main__.py" not in names:
            raise UpdateError("yt-dlp dosyası beklenen yapıda değil")
        return
    flag = "--version" if group == "deno" else "-version"
    for name, path in files.items():
        if sys.platform != "win32":
            os.chmod(path, 0o755)
        try:
            r = subprocess.run([str(path), flag], capture_output=True, timeout=20)
        except Exception as exc:  # noqa: BLE001
            raise UpdateError(f"{name} çalıştırılamadı: {exc}") from exc
        if r.returncode != 0:
            raise UpdateError(f"{name} denemesi başarısız (kod {r.returncode})")


def _swap_in(group: str, staged: Dict[str, Path], version: str, digest: str, url: str) -> None:
    """Hazırlıktaki dosyaları yerine koyar; eskiyi ``.prev/``'e alır. Hata olursa eskiyi geri koyar (OSError yükselir)."""
    root = components.tools_dir()
    prev = root / ".prev"
    root.mkdir(parents=True, exist_ok=True)
    prev.mkdir(parents=True, exist_ok=True)
    moved: List[Tuple[Path, Path]] = []
    try:
        for name, src in staged.items():
            target = root / name
            if target.exists():
                saved = prev / name
                os.replace(target, saved)
                moved.append((saved, target))
            os.replace(src, target)
    except OSError:
        for saved, target in moved:                 # geri al: eski dosyalar yerine
            try:
                os.replace(saved, target)
            except OSError:
                pass
        raise
    versions = _read_json(root / components.VERSIONS_FILE)
    old = versions.get(group)
    if isinstance(old, dict):
        versions.setdefault(".prev", {})[group] = old
    versions[group] = {"version": version, "sha256": digest, "url": url, "installed": int(time.time())}
    _write_json(root / components.VERSIONS_FILE, versions)


def rollback(group: str) -> bool:
    """``group``'un önceki sürümünü geri koyar (güncelleme bozuk çıkarsa). Önceki yoksa ``False``."""
    root = components.tools_dir()
    prev = root / ".prev"
    names = _members(group)
    if not all((prev / n).is_file() for n in names):
        return False
    for n in names:
        os.replace(prev / n, root / n)
    versions = _read_json(root / components.VERSIONS_FILE)
    old = (versions.get(".prev") or {}).pop(group, None)
    if old is not None:
        versions[group] = old
    else:
        versions.pop(group, None)
    _write_json(root / components.VERSIONS_FILE, versions)
    return True


# -- arşivlerden çıkarma ----------------------------------------------------
def _extract(group: str, archive: Path, out_dir: Path) -> Dict[str, Path]:
    """Arşivden yalnızca beklenen dosyaları (ad ile, yol gezintisi olmadan) ``out_dir``'e çıkarır."""
    wanted = set(_members(group))
    found: Dict[str, Path] = {}
    out_dir.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                base = os.path.basename(info.filename)
                if base in wanted and not info.is_dir() and base not in found:
                    dest = out_dir / base
                    with z.open(info) as src, open(dest, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                    found[base] = dest
    else:
        with tarfile.open(archive, mode="r|xz") as t:
            for member in t:
                base = os.path.basename(member.name)
                if member.isfile() and base in wanted and "/bin/" in "/" + member.name and base not in found:
                    src = t.extractfile(member)
                    if src is None:
                        continue
                    dest = out_dir / base
                    with src, open(dest, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                    found[base] = dest
                if len(found) == len(wanted):
                    break
    missing = wanted - set(found)
    if missing:
        raise UpdateError("arşivde eksik dosya: " + ", ".join(sorted(missing)))
    return found


class Updater:
    """``run`` her çağrıda sürümleri sorar, gerekenleri indirir, doğrular, yerleştirir."""

    def __init__(self, *, get_json=http_json, get_text=http_text, download=http_download,
                 platform: Optional[Dict[str, str]] = None, seed: bool = False) -> None:
        self.get_json, self.get_text, self.download = get_json, get_text, download
        self.seed = seed          # paketleme: sistemde bulunanı yok say, hepsini indir
        self.plat = platform if platform is not None else _platform()

    # -- bekleyenler -------------------------------------------------------
    def apply_pending(self, report: Optional[Report] = None) -> List[str]:
        """Önceki oturumda indirilip yerleştirilemeyen sürümleri yerine koyar. Konanların grup adlarını döner."""
        applied: List[str] = []
        pend_root = components.tools_dir() / ".pending"
        if not pend_root.is_dir():
            return applied
        for gdir in sorted(p for p in pend_root.iterdir() if p.is_dir()):
            meta = _read_json(gdir / "meta.json")
            group = gdir.name
            if group not in components.GROUPS or not meta.get("version"):
                shutil.rmtree(gdir, ignore_errors=True)
                continue
            staged = {n: gdir / n for n in _members(group)}
            try:
                _smoke(group, staged)
                _swap_in(group, staged, str(meta["version"]), str(meta.get("sha256", "")), str(meta.get("url", "")))
                shutil.rmtree(gdir, ignore_errors=True)
                applied.append(group)
                if report is not None:
                    report.updated.append(group)
            except (OSError, UpdateError) as exc:
                if isinstance(exc, UpdateError):
                    shutil.rmtree(gdir, ignore_errors=True)    # bozuk: at; sonraki denetim yeniden indirir
                # OSError (kullanımda): bekletmeye devam
        return applied

    # -- ana akış ----------------------------------------------------------
    def due(self, force: bool = False, min_interval: float = MIN_INTERVAL) -> bool:
        if force:
            return True
        last = float(_read_json(components.tools_dir() / "state.json").get("checked", 0) or 0)
        return (time.time() - last) >= min_interval

    def run(self, progress: Optional[Progress] = None, *, force: bool = False, big: bool = True,
            small_done: Optional[threading.Event] = None) -> Report:
        """Tüm bileşenleri denetler. ``big=False``: yalnızca küçükler (yt-dlp); büyükler atlanır.

        ``small_done`` küçük işler (bekleyenleri koyma + yt-dlp + sürüm sorguları) bitince set edilir; böylece açılış
        ekranı büyük indirmeleri beklemeden kapanabilir (iş parçacığı sürer).
        """
        def say(text: str, fraction: Optional[float] = None) -> None:
            """İlerleme bildirimi YALNIZCA bilgidir: geri çağrı (ör. cp1252 konsola yazan ``print``) hata verse de güncelleme sürer."""
            if progress is not None:
                try:
                    progress(text, fraction)
                except Exception:  # noqa: BLE001
                    pass

        rep = Report()
        try:
            self.apply_pending(rep)
            android = device.is_android()
            if self.plat is None and not android:
                rep.skipped = True
                return rep
            if not self.due(force):
                rep.skipped = True
                return rep
            plan: List[Tuple[str, Callable[[], Release]]] = [("yt-dlp", lambda: latest_ytdlp(self.get_json, self.get_text))]
            if big and not android and self.plat is not None:
                # Android'de indirilen ikili çalıştırılamaz (W^X): FFmpeg yeni APK ile gelir, Deno yoktur; yalnız yt-dlp.
                plan.append(("deno", lambda: latest_deno(self.plat or {}, self.get_json, self.get_text)))
                plan.append(("ffmpeg", lambda: latest_ffmpeg(self.plat or {}, self.get_json, self.get_text)))
            larges: List[Release] = []
            for group, fetch in plan:
                if group != "yt-dlp" and not self._wants(group):
                    continue
                say(f"{components.LABELS.get(group, group)} sürümü sorgulanıyor…", None)
                try:
                    rel = fetch()
                except Exception as exc:  # noqa: BLE001
                    self._note(rep, group, exc)
                    if rep.offline:
                        break
                    continue
                cur = "" if self.seed else installed_version(group)
                if cur and _vkey(rel.version) <= _vkey(cur):
                    rep.current.append(group)
                    continue
                if group == "yt-dlp":
                    self._install(rel, rep, say)
                else:
                    larges.append(rel)
            if small_done is not None:
                small_done.set()
            for rel in larges:
                self._install(rel, rep, say)
            if not rep.offline and not rep.errors:
                _write_json(components.tools_dir() / "state.json", {"checked": int(time.time())})
        except Exception as exc:  # noqa: BLE001 — güncelleyici asla uygulamayı düşürmez
            rep.errors.append(f"güncelleyici: {exc}")
        finally:
            if small_done is not None:
                small_done.set()
        return rep

    def _wants(self, group: str) -> bool:
        """Sistemde (PATH) bulunan ya da env ile verilen bileşeni kullanıcıya ait sayıp indirmeyiz; hiç yoksa/eskiyse ineriz."""
        if self.seed:
            return True
        names = components.GROUPS[group]
        hits = [components.find(n) for n in names]
        if any(h and h.source == components.SOURCE_ENV for h in hits):
            return False
        if all(h and h.source == components.SOURCE_SYSTEM for h in hits) and not installed_version(group):
            return False
        return True

    def _note(self, rep: Report, group: str, exc: BaseException) -> None:
        if isinstance(exc, UpdateError):
            rep.errors.append(f"{group}: {exc}")
        elif is_offline(exc):
            rep.offline = True
        else:
            rep.errors.append(f"{group}: {exc}")

    def _install(self, rel: Release, rep: Report, say: Progress) -> None:
        label = components.LABELS.get(rel.group, rel.group)
        root = components.tools_dir()
        work = Path(tempfile.mkdtemp(prefix=f"{rel.group}-", dir=_ensure(root / ".staging")))
        try:
            say(f"{label} {rel.version} indiriliyor…", 0.0)
            archive = work / (rel.asset or "dosya")
            digest = self.download(rel.url, archive, max_bytes=MAX_BYTES[rel.group],
                                   progress=lambda f: say(f"{label} {rel.version} indiriliyor…", f))
            if digest.lower() != rel.sha256.lower():
                raise UpdateError("SHA-256 sağlaması uyuşmadı; dosya kullanılmadı")
            say(f"{label} hazırlanıyor…", None)
            staged = {"yt-dlp": archive} if rel.group == "yt-dlp" else _extract(rel.group, archive, work / "out")
            _smoke(rel.group, staged)
            try:
                _swap_in(rel.group, staged, rel.version, digest, rel.url)
                rep.updated.append(rel.group)
            except OSError:
                # Kullanımda (Windows): sonraki açılışta konsun.
                pend = _ensure(root / ".pending" / rel.group)
                for f in pend.iterdir():
                    f.unlink()
                for name, src in staged.items():
                    shutil.move(str(src), str(pend / name))
                _write_json(pend / "meta.json", {"version": rel.version, "sha256": digest, "url": rel.url})
                rep.pending.append(rel.group)
        except Exception as exc:  # noqa: BLE001
            self._note(rep, rel.group, exc)
        finally:
            shutil.rmtree(work, ignore_errors=True)


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Açılış denetimi
# ---------------------------------------------------------------------------
def auto_update_enabled(settings: Any = None) -> bool:
    """Ayar açık mı? ``KADRAJ_NO_UPDATE=1`` ile (testler, --selftest) kapatılır."""
    if os.environ.get("KADRAJ_NO_UPDATE") or "pytest" in sys.modules:
        return False
    if settings is None:
        return True
    try:
        v = settings.get("tools.auto_update")
        return True if v is None else bool(v)
    except Exception:  # noqa: BLE001
        return True


class BackgroundRun:
    """Açılış ekranının tuttuğu tutamaç: ``small_done`` kısa işler bitince, ``done`` her şey bitince set edilir."""

    def __init__(self, updater: Optional[Updater] = None, *, force: bool = False) -> None:
        self.updater = updater or Updater()
        self.force = force
        self.small_done = threading.Event()
        self.done = threading.Event()
        self.text = ""
        self.fraction: Optional[float] = None
        self.report: Optional[Report] = None
        self._thread: Optional[threading.Thread] = None

    def _progress(self, text: str, fraction: Optional[float]) -> None:
        self.text, self.fraction = text, fraction

    def start(self) -> "BackgroundRun":
        def target() -> None:
            try:
                self.report = self.updater.run(self._progress, force=self.force, small_done=self.small_done)
            finally:
                self.done.set()
        self._thread = threading.Thread(target=target, name="kadraj-updater", daemon=True)
        self._thread.start()
        return self


# ---------------------------------------------------------------------------
# Paketleme: python -m kadraj.core.updater --seed <klasör>
# ---------------------------------------------------------------------------
def seed(dest: Path, progress: Optional[Progress] = None) -> Report:
    """Kurulum paketine girecek bileşenleri (yt-dlp, deno, ffmpeg, ffprobe) ``dest``'e indirir; SHA-256 doğrulanır.

    Geçici bir ``KADRAJ_HOME`` kullanır (gerçek kullanıcı verisine dokunmaz); sonuç ``dest``'te düz klasördür
    (``ffmpeg``, ``ffprobe``, ``deno``, ``yt-dlp``, ``versions.json``)."""
    with tempfile.TemporaryDirectory(prefix="kadraj-seed-", ignore_cleanup_errors=True) as home:
        old = os.environ.get("KADRAJ_HOME")
        os.environ["KADRAJ_HOME"] = home
        try:
            rep = Updater(seed=True).run(progress, force=True)
            src = components.tools_dir()
            dest.mkdir(parents=True, exist_ok=True)
            if not src.is_dir():
                rep.errors.append("hiçbir bileşen indirilemedi (hazırlık klasörü oluşmadı)")
            else:
                for item in src.iterdir():
                    if item.is_file() and item.name != "state.json":
                        shutil.copy2(item, dest / item.name)
            vers = {k: v for k, v in _read_json(dest / components.VERSIONS_FILE).items() if not k.startswith(".")}
            _write_json(dest / components.VERSIONS_FILE, vers)
        finally:
            if old is None:
                os.environ.pop("KADRAJ_HOME", None)
            else:
                os.environ["KADRAJ_HOME"] = old
    return rep


def _utf8_stdio() -> None:
    """GitHub Windows çalıştırıcısında stdout cp1252'dir: 'ı' gibi harfler ``UnicodeEncodeError`` verirdi."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")      # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "--seed":
        print("kullanım: python -m kadraj.core.updater --seed <klasör>", file=sys.stderr)
        return 2
    dest = Path(args[1])
    try:
        rep = seed(dest, lambda t, f: print(t if f is None else f"{t} {int(f * 100)}%", flush=True)
                   if f is None or int(f * 100) % 25 == 0 else None)
    except Exception as exc:  # noqa: BLE001
        print(f"HATA: bileşenler hazırlanamadı: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    missing = [n for n in components.NAMES if not (dest / components.exe_name(n)).is_file()]
    print("güncellenen:", rep.updated, "| hatalar:", rep.errors, "| eksik:", missing, "| çevrimdışı:", rep.offline)
    print(json.dumps(_read_json(dest / components.VERSIONS_FILE), ensure_ascii=False))
    if rep.errors or missing or rep.offline:
        print(f"HATA: bileşen hazırlığı tamamlanamadı — eksik: {missing or '-'}; hatalar: {rep.errors or '-'}"
              + ("; ağa ulaşılamadı" if rep.offline else ""), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
