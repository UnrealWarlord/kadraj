"""Paketlenmiş (PyInstaller) uygulamaya özgü düzeltmeler. Normal ``python main.py`` çalışmasında hiçbir şey yapmaz.

* **Linux**: PyInstaller önyükleyicisi ``LD_LIBRARY_PATH``'i paket klasörüne çevirir. Bu değişken alt süreçlere de
  geçer; sistemdeki ``ffmpeg`` gibi araçlar paketteki (başka bir dağıtımdan gelen) kütüphaneleri yükleyip
  ``version 'GLIBCXX_…' not found`` ile ölür. Alt süreçler özgün ortamla başlatılır (``<DEĞİŞKEN>_ORIG`` geri yüklenir).
* **Linux, ctypes ile yüklenen kütüphaneler**: ``sounddevice`` PortAudio'yu ``ctypes.util.find_library`` ile arar; bu çağrı
  ``ldconfig``/``ld`` ister ve temiz bir sistemde (ldconfig yolu/``ld`` yok) paketin içindeki ``libportaudio.so.2``'yi bulamaz →
  "ses arka ucu yok". Paketlenmişte ``find_library`` önce paket klasörüne bakar.
* **Windows**: konsolsuz (pencereli) uygulamadan başlatılan her ``ffmpeg``/``ffprobe`` kısa süreliğine bir konsol
  penceresi açar; alt süreçler ``CREATE_NO_WINDOW`` ile başlatılır.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, Dict, Mapping, Optional

_LIB_VARS = ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH")
_PATCHED_FLAG = "_kadraj_frozen_patch"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def clean_env(env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """``env`` (yoksa ``os.environ``) kopyası; PyInstaller'ın değiştirdiği kütüphane yolu özgün haline döner."""
    out = dict(os.environ if env is None else env)
    for var in _LIB_VARS:
        orig = out.pop(var + "_ORIG", None)
        if orig is None:
            out.pop(var, None)              # özgün ortamda hiç tanımlı değildi
        else:
            out[var] = orig
    return out


def _bundle_dir() -> Optional[str]:
    base = getattr(sys, "_MEIPASS", None)
    return str(base) if base else None


def _patch_find_library() -> None:
    """``ctypes.util.find_library("x")`` önce ``<paket>/libx.so*`` dosyasını döndürür (yalnız Linux; geri alınabilir)."""
    import ctypes.util
    import glob
    original = ctypes.util.find_library
    if getattr(original, _PATCHED_FLAG, False):
        return
    base = _bundle_dir()

    def find_library(name: str) -> Optional[str]:
        if base:
            hits = sorted(glob.glob(os.path.join(base, f"lib{name}.so*")))
            if hits:
                return hits[0]
        return original(name)

    setattr(find_library, _PATCHED_FLAG, True)
    setattr(find_library, "__wrapped__", original)
    ctypes.util.find_library = find_library          # type: ignore[assignment]


def apply() -> bool:
    """Yalnızca paketlenmiş uygulamada ``subprocess.Popen``'ı yamalar (tekrar çağrılsa da bir kez). Yamalandıysa ``True``."""
    if not is_frozen():
        return False
    if sys.platform.startswith("linux"):
        _patch_find_library()
    popen = subprocess.Popen
    if getattr(popen.__init__, _PATCHED_FLAG, False):
        return True
    original = popen.__init__

    def patched(self: Any, *args: Any, **kwargs: Any) -> None:
        if sys.platform == "win32":
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        else:
            kwargs["env"] = clean_env(kwargs.get("env"))
        original(self, *args, **kwargs)

    setattr(patched, _PATCHED_FLAG, True)
    setattr(patched, "__wrapped__", original)
    popen.__init__ = patched                # type: ignore[method-assign]
    return True


def revert() -> None:
    """Yamayı geri alır (testler için)."""
    init = subprocess.Popen.__init__
    if getattr(init, _PATCHED_FLAG, False):
        subprocess.Popen.__init__ = getattr(init, "__wrapped__")   # type: ignore[method-assign]
    import ctypes.util
    fl = ctypes.util.find_library
    if getattr(fl, _PATCHED_FLAG, False):
        ctypes.util.find_library = getattr(fl, "__wrapped__")   # type: ignore[assignment]
