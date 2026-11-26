"""Harici bileşenlerin (ffmpeg, ffprobe, deno, yt-dlp) tek bulucusu.

Uygulamanın başka hiçbir yeri ``shutil.which("ffmpeg")`` gibi kendi aramasını yapmaz; hepsi buradan sorar.

Arama sırası (ilk bulunan kazanır):

1. **Ortam değişkeni** — ``KADRAJ_FFMPEG`` / ``KADRAJ_FFPROBE`` / ``KADRAJ_DENO`` / ``KADRAJ_YTDLP`` (geliştirici/test).
2. **Güncellenmiş kopya** — ``~/.kadraj/tools/`` (açılıştaki güncelleyicinin indirdiği, sağlaması doğrulanmış dosyalar).
3. **Uygulamayla gelen kopya** — paketlenmiş uygulamada ``<paket>/tools/`` (kurulum paketi hepsini içerir).
4. **Sistem PATH'i** (yt-dlp hariç: o bir Python paketidir, ``prepare_ytdlp`` ile içe aktarılır).

**Android** (``kadraj.device.is_android()``): Android 10+ yazılabilir klasörden ikili çalıştırmaya izin vermez (W^X).
Bu yüzden ffmpeg/ffprobe APK'nın yerel kütüphane klasörüne ``lib<ad>.so`` adıyla gömülür ve **yalnız** oradan
(ya da ortam değişkeninden) bulunur; ``~/.kadraj/tools`` ve PATH ikililer için denenmez. yt-dlp saf Python
olduğundan güncellenmiş zipapp Android'de de kullanılır. Deno'nun Android sürümü yoktur.

Bu modül **ağa çıkmaz** ve Qt'ye bağlı değildir; güncelleme kodu ``kadraj/core/updater.py``'dedir.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import device, paths

NAMES: Tuple[str, ...] = ("ffmpeg", "ffprobe", "deno", "yt-dlp")
_ENV = {"ffmpeg": "KADRAJ_FFMPEG", "ffprobe": "KADRAJ_FFPROBE", "deno": "KADRAJ_DENO", "yt-dlp": "KADRAJ_YTDLP"}
LABELS = {"ffmpeg": "FFmpeg", "ffprobe": "ffprobe", "deno": "Deno", "yt-dlp": "yt-dlp"}
#: Güncelleme birimleri: ffmpeg + ffprobe aynı arşivden gelir, birlikte değişir.
GROUPS: Dict[str, Tuple[str, ...]] = {"ffmpeg": ("ffmpeg", "ffprobe"), "deno": ("deno",), "yt-dlp": ("yt-dlp",)}

SOURCE_ENV, SOURCE_UPDATED, SOURCE_BUNDLED, SOURCE_SYSTEM, SOURCE_MODULE = "env", "updated", "bundled", "system", "module"
SOURCE_ANDROID = "android"
#: Android'de APK'nın yerel kütüphane klasöründeki dosya adları (``jniLibs/arm64-v8a/lib<ad>.so``). ``None``: Android'de yok.
ANDROID_LIBS: Dict[str, Optional[str]] = {"ffmpeg": "libffmpeg.so", "ffprobe": "libffprobe.so", "deno": None, "yt-dlp": None}
VERSIONS_FILE = "versions.json"
_PROBE_TIMEOUT = 6.0


@dataclass(frozen=True)
class Found:
    name: str
    path: str
    source: str            # env | updated | bundled | system | module | android
    version: str = ""


# ---------------------------------------------------------------------------
# Konumlar
# ---------------------------------------------------------------------------
def exe_name(name: str) -> str:
    """Dosya adı: Windows'ta ``ffmpeg.exe``; yt-dlp her yerde ``yt-dlp`` (Python zipapp'ıdır)."""
    return name + ".exe" if sys.platform == "win32" and name != "yt-dlp" else name


def tools_dir() -> Path:
    """Güncellenmiş kopyaların klasörü: ``~/.kadraj/tools`` (``KADRAJ_HOME`` ile değişir)."""
    return paths.home_dir() / "tools"


def bundled_dir() -> Optional[Path]:
    """Uygulamayla gelen kopyaların klasörü. Paketlenmişte ``<paket>/tools``; ``KADRAJ_BUNDLED_TOOLS`` ile de verilebilir."""
    override = os.environ.get("KADRAJ_BUNDLED_TOOLS")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "tools"
    return None


def _file_ok(p: Path) -> bool:
    try:
        if not p.is_file():
            return False
        if sys.platform == "win32" or p.name == "yt-dlp" or os.access(p, os.X_OK):   # yt-dlp içe aktarılır, çalıştırılmaz
            return True
        try:
            os.chmod(p, 0o755)           # paketleme yürütme bitini düşürmüş olabilir; kendi klasörümüzde düzeltiriz
        except OSError:
            return False
        return os.access(p, os.X_OK)
    except OSError:
        return False


def android_lib(name: str) -> Optional[Path]:
    """Android'de ``name`` ikilisinin APK içindeki yeri (``nativeLibraryDir/lib<ad>.so``); yoksa ``None``."""
    lib = ANDROID_LIBS.get(name)
    if not lib or not device.is_android():
        return None
    libdir = device.native_library_dir()
    return libdir / lib if libdir is not None else None


def _candidates(name: str) -> List[Tuple[str, Path]]:
    out: List[Tuple[str, Path]] = []
    env = os.environ.get(_ENV[name])
    if env:
        out.append((SOURCE_ENV, Path(env).expanduser()))
    if device.is_android() and name != "yt-dlp":
        lib = android_lib(name)                 # W^X: ikililer yalnız APK'nın kütüphane klasöründen çalışır
        if lib is not None:
            out.append((SOURCE_ANDROID, lib))
        return out
    out.append((SOURCE_UPDATED, tools_dir() / exe_name(name)))
    b = bundled_dir()
    if b is not None:
        out.append((SOURCE_BUNDLED, b / exe_name(name)))
    return out


def _versions(root: Optional[Path]) -> Dict[str, str]:
    if root is None:
        return {}
    try:
        data = json.loads((root / VERSIONS_FILE).read_text(encoding="utf-8"))
        return {str(k): str(v.get("version", "")) if isinstance(v, dict) else str(v) for k, v in data.items()}
    except (OSError, ValueError, AttributeError):
        return {}


def recorded_version(name: str, source: str) -> str:
    """``versions.json``'da yazılı sürüm (güncelleyici ya da paketleme yazar). Yoksa boş."""
    group = next((g for g, members in GROUPS.items() if name in members), name)
    root = tools_dir() if source == SOURCE_UPDATED else bundled_dir() if source == SOURCE_BUNDLED else None
    return _versions(root).get(group, "")


def find(name: str) -> Optional[Found]:
    """``name`` bileşeninin bulunduğu ilk yer; hiçbiri yoksa ``None``. Ağa çıkmaz, süreç başlatmaz."""
    if name not in NAMES:
        raise KeyError(name)
    for source, p in _candidates(name):
        if _file_ok(p):
            return Found(name, str(p), source, recorded_version(name, source))
    if name != "yt-dlp" and not device.is_android():
        hit = shutil.which(name)
        if hit:
            return Found(name, hit, SOURCE_SYSTEM)
    return None


def path_of(name: str) -> Optional[str]:
    hit = find(name)
    return hit.path if hit else None


def ffmpeg_path() -> Optional[str]:
    return path_of("ffmpeg")


def ffprobe_path() -> Optional[str]:
    return path_of("ffprobe")


def deno_path() -> Optional[str]:
    return path_of("deno")


def ffmpeg_location() -> Optional[str]:
    """yt-dlp'nin ``ffmpeg_location`` seçeneği: ffmpeg'in bulunduğu KLASÖR (ffprobe da yanında aranır)."""
    p = ffmpeg_path()
    return str(Path(p).parent) if p else None


def have(name: str) -> bool:
    return find(name) is not None


# ---------------------------------------------------------------------------
# yt-dlp: zipapp (güncellenmiş/paketle gelen) ya da pip modülü
# ---------------------------------------------------------------------------
def _version_key(text: str) -> Tuple[int, ...]:
    nums: List[int] = []
    for part in str(text or "").replace("-", ".").split("."):
        if part.isdigit():
            nums.append(int(part))
        else:
            break
    return tuple(nums)


def module_ytdlp_version() -> str:
    """Kurulu ``yt_dlp`` paketinin sürümü (içe aktarmadan, dağıtım üst verisinden); yoksa boş."""
    try:
        from importlib import metadata
        return metadata.version("yt-dlp")
    except Exception:  # noqa: BLE001
        try:
            from yt_dlp.version import __version__  # type: ignore
            return str(__version__)
        except Exception:  # noqa: BLE001
            return ""


def ytdlp_choice() -> Optional[Found]:
    """İçe aktarılacak yt-dlp. Zipapp adaylarından ve pip modülünden **en yeni sürüm** seçilir; env hep kazanır."""
    cands: List[Found] = []
    for source, p in _candidates("yt-dlp"):
        if _file_ok(p):
            cands.append(Found("yt-dlp", str(p), source, recorded_version("yt-dlp", source)))
    env = next((c for c in cands if c.source == SOURCE_ENV), None)
    if env:
        return env
    mod_version = module_ytdlp_version()
    if mod_version:
        cands.append(Found("yt-dlp", "", SOURCE_MODULE, mod_version))
    if not cands:
        return None
    return max(cands, key=lambda c: (_version_key(c.version), c.source == SOURCE_UPDATED))


def prepare_ytdlp() -> Optional[Found]:
    """``import yt_dlp`` ÖNCESİ çağrılır: seçilen zipapp'ı ``sys.path``'in başına koyar. Seçimi döner (yoksa ``None``).

    ``yt_dlp`` zaten içe aktarılmışsa süreç yeniden başlatılana dek o kullanılır (seçim yine döner).
    """
    choice = ytdlp_choice()
    if choice and choice.path and "yt_dlp" not in sys.modules and choice.path not in sys.path:
        sys.path.insert(0, choice.path)
    return choice


def ytdlp_version() -> str:
    """Kullanılacak (ya da kullanılmakta olan) yt-dlp sürümü."""
    mod = sys.modules.get("yt_dlp")
    if mod is not None:
        v = getattr(getattr(mod, "version", None), "__version__", "")
        if v:
            return str(v)
    choice = ytdlp_choice()
    return choice.version if choice else ""


# ---------------------------------------------------------------------------
# Sürüm bilgisi (yalnızca ayarlar sayfası için; süreç başlatır — arka planda çağırın)
# ---------------------------------------------------------------------------
def _run_first_line(args: List[str]) -> str:
    try:
        r = subprocess.run(args, capture_output=True, timeout=_PROBE_TIMEOUT, text=True, errors="replace")
        return (r.stdout or r.stderr or "").strip().splitlines()[0] if (r.stdout or r.stderr) else ""
    except Exception:  # noqa: BLE001
        return ""


def probe_version(name: str) -> str:
    """Bileşenin gerçek sürümü (ör. ``8.1``, ``2.9.8``, ``2026.11.23``). Bulunamazsa boş."""
    if name == "yt-dlp":
        return ytdlp_version()
    hit = find(name)
    if not hit:
        return ""
    if hit.version and hit.source in (SOURCE_UPDATED, SOURCE_BUNDLED):
        return hit.version
    line = _run_first_line([hit.path, "--version" if name == "deno" else "-version"])
    parts = line.split()
    if name == "deno":
        return parts[1] if len(parts) > 1 and parts[0].lower() == "deno" else (parts[0] if parts else "")
    return parts[2] if len(parts) > 2 and parts[0].lower() in ("ffmpeg", "ffprobe") else ""


def status() -> Dict[str, Dict[str, str]]:
    """``{ad: {path, source, version}}`` (süreç başlatmadan: yalnızca yazılı sürümler)."""
    out: Dict[str, Dict[str, str]] = {}
    for name in NAMES:
        if name == "yt-dlp":
            c = ytdlp_choice()
            out[name] = {"path": c.path if c else "", "source": c.source if c else "", "version": ytdlp_version()}
        else:
            c = find(name)
            out[name] = {"path": c.path if c else "", "source": c.source if c else "", "version": c.version if c else ""}
    return out
