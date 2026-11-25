"""Paketlenmiş (PyInstaller) uygulamaya özgü düzeltmeler. Normal ``python main.py`` çalışmasında hiçbir şey yapmaz.

* **Linux**: PyInstaller önyükleyicisi ``LD_LIBRARY_PATH``'i paket klasörüne çevirir. Bu değişken alt süreçlere de
  geçer; sistemdeki ``ffmpeg`` gibi araçlar paketteki (başka bir dağıtımdan gelen) kütüphaneleri yükleyip
  ``version 'GLIBCXX_…' not found`` ile ölür. Alt süreçler özgün ortamla başlatılır (``<DEĞİŞKEN>_ORIG`` geri yüklenir).
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


def apply() -> bool:
    """Yalnızca paketlenmiş uygulamada ``subprocess.Popen``'ı yamalar (tekrar çağrılsa da bir kez). Yamalandıysa ``True``."""
    if not is_frozen():
        return False
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
