"""Android hata günlüğü: telefonda açılış hatası kara ekran bırakmaz, ekranda görünür ve dosyaya yazılır.

Masaüstünde hata konsolda görünür; Android'de konsol yoktur ve kullanıcı ``adb logcat`` bilmeyebilir. Bu yüzden:

* ``install(home)`` yakalanmamış Python hatalarını (``sys.excepthook``) ve yerel çökmeleri (``faulthandler``: SIGSEGV
  vb.) ``<home>/KADRAJ-hata.txt`` dosyasına da yazar. Android'de ``home`` uygulamanın Belgeler klasörüdür
  (``Android/data/org.insibag.kadraj/files/Documents``; telefon bilgisayara bağlanınca görünür).
* ``show_fatal(metin)`` açılışı durduran hatayı bir iletişim kutusunda gösterir (ekran görüntüsü alınabilsin) ve
  aynı dosyaya ekler.

Modül masaüstünde kullanılmaz; çağıran Android'de olduğunu bilir (``main.py``, ``ui/shell/app.py``).
"""
from __future__ import annotations

import faulthandler
import sys
import time
import traceback
from pathlib import Path
from typing import Optional, TextIO

from .branding import APP_NAME, window_title

FILE_NAME = f"{APP_NAME}-hata.txt"
#: İletişim kutusunda gösterilen en fazla karakter (geri izlemenin sonu: asıl hata oradadır)
SHOW_CHARS = 1800

_state: dict = {"home": None, "fault_file": None, "hook": None}


def log_path(home: Optional[Path] = None) -> Path:
    """Günlük dosyası: verilen klasör, yoksa ``install``'da verilen, o da yoksa ``Path.home()``."""
    return Path(home or _state["home"] or Path.home()) / FILE_NAME


def _version() -> str:
    try:
        from . import __version__
        return __version__
    except Exception:  # noqa: BLE001
        return "?"


def append(text: str, home: Optional[Path] = None) -> Optional[Path]:
    """Metni zaman damgasıyla günlüğe ekler; yazılamazsa ``None``."""
    path = log_path(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} · {APP_NAME} {_version()}\n{text.rstrip()}\n")
        return path
    except OSError:
        return None


def install(home: Path, *, fault: bool = True) -> None:
    """Yakalanmamış hataları ve (``fault`` ise) yerel çökmeleri ``home`` altındaki günlüğe de yazar. İki kez çağrılabilir."""
    _state["home"] = Path(home)
    if _state["hook"] is None:
        previous = sys.excepthook

        def hook(kind, value, tb):  # noqa: ANN001
            append("".join(traceback.format_exception(kind, value, tb)))
            previous(kind, value, tb)

        _state["hook"] = hook
        sys.excepthook = hook
    if fault and _state["fault_file"] is None:
        try:
            fh: TextIO = open(log_path(), "a", encoding="utf-8")       # çökme anında açık olmalı: kapatılmaz
        except OSError:
            return
        _state["fault_file"] = fh
        faulthandler.enable(fh, all_threads=True)


def show_fatal(text: str) -> Optional[Path]:
    """Açılışı durduran hatayı günlüğe yazar ve ekranda gösterir. Qt yoksa yalnız yazar. Günlük yolunu döner."""
    path = append(text)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(["kadraj"])
        box = QMessageBox(QMessageBox.Icon.Critical, window_title(),          # marka adı yalnız imzalı başlıkta (plan §0.1)
                          "Uygulama açılamadı. Bu ekranın görüntüsünü geliştiriciye gönderebilirsin.\n"
                          "The app could not start. You can send a screenshot of this screen to the developer.")
        tail = text.rstrip()[-SHOW_CHARS:]
        box.setInformativeText(tail + (f"\n\n{path}" if path else ""))
        box.exec()
        del app
    except Exception:  # noqa: BLE001 — Qt de açılamıyorsa günlük dosyası kalır
        pass
    return path
