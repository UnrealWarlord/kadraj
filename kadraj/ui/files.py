"""Dosya eylemleri ve dosya seçme, platforma göre (``docs/steps/MOBILE_6.md``, karar M20).

* **Masaüstü:** "Aç" (varsayılan uygulama) ve "Klasörde göster" — ``QDesktopServices``; davranış değişmedi.
* **Android:** klasör kavramı kullanıcıya açık değil (dosyalar uygulamaya ayrılmış klasörde). "Klasörde göster"
  yerine **"Paylaş"** (sistemin paylaşım menüsü) ve **"Galeriye kaydet"** (``storage.gallery``); "Aç" köprüyle
  (``kadraj.bridge``) uygun uygulamayı açar. Köprünün sonucu birkaç yüz milisaniye içinde gelir; hata olursa
  sayfanın not satırına sade bir cümle yazılır (``note``).
* **Dosya seçme:** Android'in dosya seçicisi ``content://`` adresi döner; Python ve FFmpeg bunu açamaz.
  ``pick_file`` / ``pick_files`` seçilen dosyayı Qt'nin içerik motoruyla uygulama klasörüne (``~/Imports``)
  kopyalar ve yerel yolu döner. Aynı ad ve boyutta dosya daha önce kopyalandıysa yeniden kopyalanmaz.

Sayfalar düğme metinlerini kendileri seçer; bu modül eylemleri ve Android'e özgü düğmeleri (``mobile_buttons``) verir.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Callable, List, Optional, Set

from PySide6.QtCore import QFile, QFileInfo, QIODevice, QMimeDatabase, QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog, QProgressDialog, QWidget

from .. import bridge, device
from ..core import i18n
from ..storage import gallery

Note = Callable[[str], None]
#: Köprü yanıtı için en uzun bekleme (Android'in paylaşım menüsü açılırken uygulama kısa süre duraklar).
ANSWER_TIMEOUT_S = 8.0
_POLL_MS = 150
#: Bu boyuttan büyük içe aktarmada ilerleme penceresi gösterilir.
PROGRESS_MIN_BYTES = 8 << 20
_CHUNK = 1 << 20


# ------------------------------------------------------------------------------------------------- yetenekler
def can_reveal() -> bool:
    """"Klasörde göster" sunulur mu? (Android'de klasör dosya yöneticisinde açılamaz.)"""
    return not device.is_android()


def can_share() -> bool:
    return device.is_android()


def can_save_to_gallery() -> bool:
    return device.is_android() and gallery.available()


# ------------------------------------------------------------------------------------------------- eylemler
def _say(note: Optional[Note], text: str) -> None:
    if note is None or not text:
        return
    try:
        note(text)
    except RuntimeError:                     # not satırının widget'ı bu arada silinmiş
        pass


def bridge_error_text(code: str) -> str:
    key = f"files.err.{code}"
    return i18n.t(key if i18n.has(key) else "files.err.failed")


def _watch(req_id: str, note: Optional[Note], started: float) -> None:
    result = bridge.take_result(req_id)
    if result is not None:
        if not result.get("ok"):
            _say(note, bridge_error_text(str(result.get("error") or "failed")))
        return
    if time.monotonic() - started > ANSWER_TIMEOUT_S:
        _say(note, bridge_error_text("timeout"))
        return
    QTimer.singleShot(_POLL_MS, lambda: _watch(req_id, note, started))


def _bridge_call(req_id: Optional[str], note: Optional[Note]) -> bool:
    if req_id is None:
        _say(note, bridge_error_text("unavailable"))
        return False
    QTimer.singleShot(_POLL_MS, lambda: _watch(req_id, note, time.monotonic()))
    return True


def open_path(path: "str | os.PathLike[str]", note: Optional[Note] = None) -> bool:
    """Dosyayı varsayılan uygulamayla açar (oynatıcı, galeri…). Dosya yoksa ya da açılamazsa ``False``.

    Android'de başarısızlığın nedeni ``note``'a yazılır (masaüstünde çağıran kendi metnini gösterir).
    """
    file = Path(path)
    if device.is_android():
        if not file.is_file():
            _say(note, bridge_error_text("not_found"))
            return False
        return _bridge_call(bridge.open_file(file), note)
    if not file.is_file():
        return False
    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(file))))


def reveal_path(path: "str | os.PathLike[str]") -> bool:
    """Dosyanın (ya da klasörün) bulunduğu klasörü dosya yöneticisinde açar. Android'de ``False``."""
    if not can_reveal():
        return False
    target = Path(path)
    folder = target if target.is_dir() else target.parent
    if not folder.is_dir():
        return False
    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))


def share_path(path: "str | os.PathLike[str]", note: Optional[Note] = None) -> bool:
    """Android'in paylaşım menüsünü açar. Dosya yoksa ya da köprü yoksa ``False`` (nedeni ``note``'a)."""
    file = Path(path)
    if not file.is_file():
        _say(note, bridge_error_text("not_found"))
        return False
    return _bridge_call(bridge.share(file, title=i18n.t("files.share_title")), note)


class _CopyDone(QObject):
    finished = Signal(object, object)        # (yeni yol | None, GalleryError | None)


class _CopyJob(QRunnable):
    def __init__(self, src: Path, done: _CopyDone) -> None:
        super().__init__()
        self.src, self.done = src, done

    def run(self) -> None:
        try:
            out = gallery.save(self.src)
        except gallery.GalleryError as exc:
            self.done.finished.emit(None, exc)
        except Exception as exc:  # noqa: BLE001 — iş parçacığında kalmasın, kullanıcıya söylensin
            self.done.finished.emit(None, gallery.GalleryError("failed", str(exc)))
        else:
            self.done.finished.emit(out, None)


_pending: Set[_CopyDone] = set()


def gallery_error_text(err: gallery.GalleryError) -> str:
    key = f"files.gallery.err.{err.code}"
    return i18n.t(key if i18n.has(key) else "files.gallery.err.failed")


def save_to_gallery(path: "str | os.PathLike[str]", note: Optional[Note] = None,
                    done: Optional[Callable[[Optional[Path]], None]] = None) -> bool:
    """Dosyanın bir kopyasını galeriye yazar (arka planda; büyük videolar arayüzü dondurmasın).

    Başlarken ve bitince ``note``'a yazar; ``done(yeni_yol | None)`` bitince çağrılır. Başlatıldıysa ``True``.
    """
    src = Path(path)
    if not src.is_file():
        _say(note, gallery_error_text(gallery.GalleryError("not_found")))
        return False
    signal = _CopyDone()
    _pending.add(signal)

    def finished(out: Optional[Path], err: Optional[gallery.GalleryError]) -> None:
        _pending.discard(signal)
        if out is not None:
            bridge.scan([out])                       # galeri hemen görsün (Android 11+ zaten ekler)
            _say(note, i18n.t("files.gallery.done", folder=gallery.folder_for(out)))
        else:
            _say(note, gallery_error_text(err or gallery.GalleryError("failed")))
        if done is not None:
            done(out)

    signal.finished.connect(finished)
    _say(note, i18n.t("files.gallery.running"))
    QThreadPool.globalInstance().start(_CopyJob(src, signal))
    return True


def mobile_buttons(path: Callable[[], Optional[str]], note: Note, *, open_button: bool = False) -> List[QWidget]:
    """Android'de "Paylaş" ve "Galeriye kaydet" (istenirse önce "Aç") düğmeleri; masaüstünde boş liste.

    ``path()`` tıklandığı andaki dosyayı verir (sonuç kartı başka bir dosyaya geçmiş olabilir).
    """
    if not device.is_android():
        return []
    from .widgets.squishy_button import SquishyButton

    def act(fn: Callable[..., bool]) -> Callable[..., None]:
        def run(_checked: bool = False) -> None:
            target = path()
            if target:
                fn(target, note)
        return run

    out: List[QWidget] = []
    if open_button:
        btn = SquishyButton(i18n.t("files.open"), variant="secondary", icon="play")
        btn.setObjectName("file_open")
        btn.clicked.connect(act(open_path))
        out.append(btn)
    share = SquishyButton(i18n.t("files.share"), variant="secondary", icon="share")
    share.setObjectName("file_share")
    share.clicked.connect(act(share_path))
    out.append(share)
    if can_save_to_gallery():
        keep = SquishyButton(i18n.t("files.gallery"), variant="secondary", icon="image")
        keep.setObjectName("file_gallery")
        keep.clicked.connect(act(save_to_gallery))
        out.append(keep)
    return out


# ------------------------------------------------------------------------------------------------- dosya seçme
def is_content_uri(text: str) -> bool:
    return text.startswith("content:")


def import_dir() -> Path:
    """Android'de seçilen dosyaların kopyalandığı klasör (uygulamaya ayrılmış alan; projeler gibi ``HOME`` altında)."""
    return Path.home() / "Imports"


def _open_source(uri: str) -> QFile:
    """``content://`` adresini Qt'nin Android içerik motoruyla okumak için açar (testler bunu değiştirir)."""
    src = QFile(uri)
    if not src.open(QIODevice.OpenModeFlag.ReadOnly):
        raise OSError(src.errorString() or uri)
    return src


_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def display_name(uri: str, src: QFile) -> str:
    """Dosyanın görünen adı; uzantısı yoksa içeriğe bakılarak eklenir (FFmpeg ve kitaplık türü uzantıdan anlar)."""
    name = _UNSAFE.sub("_", QFileInfo(uri).fileName()).strip(" ._") or "dosya"
    if not Path(name).suffix:
        suffix = QMimeDatabase().mimeTypeForData(src.peek(4096)).preferredSuffix()
        if suffix:
            name = f"{name}.{suffix}"
    return name


def _free_name(folder: Path, name: str) -> Path:
    target = folder / name
    n = 2
    while target.exists():
        target = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return target


def import_content(uri: str, parent: Optional[QWidget] = None, folder: Optional[Path] = None) -> Optional[Path]:
    """``content://`` dosyasını ``folder``'a (varsayılan ``import_dir()``) kopyalar. Vazgeçilirse ``None``.

    Okunamazsa ``OSError``. Büyük dosyada ilerleme penceresi açılır (Vazgeç ile durur, yarım dosya silinir).
    """
    src = _open_source(uri)
    try:
        dest = folder or import_dir()
        dest.mkdir(parents=True, exist_ok=True)
        name = display_name(uri, src)
        size = src.size()
        same = dest / name
        if size > 0 and same.is_file() and same.stat().st_size == size:
            return same                                          # aynı dosya daha önce kopyalandı
        target = _free_name(dest, name)
        part = target.with_name(target.name + ".part")
        progress: Optional[QProgressDialog] = None
        if size > PROGRESS_MIN_BYTES and QApplication.instance() is not None:
            progress = QProgressDialog(i18n.t("files.import.running", name=name), i18n.t("common.cancel"),
                                       0, 1000, parent)
            progress.setMinimumDuration(0)
            progress.setAutoClose(False)
        copied = 0
        try:
            with open(part, "wb") as out:
                while True:
                    data = bytes(src.read(_CHUNK))
                    if not data:
                        break
                    out.write(data)
                    copied += len(data)
                    if progress is not None:
                        progress.setValue(int(1000 * copied / max(1, size)))
                        QApplication.processEvents()
                        if progress.wasCanceled():
                            raise InterruptedError
            os.replace(part, target)
        except InterruptedError:
            part.unlink(missing_ok=True)
            return None
        except OSError:
            part.unlink(missing_ok=True)
            raise
        finally:
            if progress is not None:
                progress.close()
        return target
    finally:
        src.close()


def localize(path: str, parent: Optional[QWidget] = None, note: Optional[Note] = None) -> str:
    """Seçilen yol ``content://`` ise yerel kopyanın yolu, değilse aynısı. Olmazsa boş metin (nedeni ``note``'a)."""
    if not path or not is_content_uri(path):
        return path
    try:
        out = import_content(path, parent)
    except OSError:
        _say(note, i18n.t("files.import.failed"))
        return ""
    return str(out) if out is not None else ""


def pick_file(parent: Optional[QWidget], title: str, filters: str = "", note: Optional[Note] = None) -> str:
    """Tek dosya seçtirir; Android'de seçilen dosya uygulama klasörüne kopyalanır. Seçilmezse boş metin."""
    chosen, _filter = QFileDialog.getOpenFileName(parent, title, "", filters)
    return localize(chosen, parent, note) if chosen else ""


def pick_files(parent: Optional[QWidget], title: str, filters: str = "", note: Optional[Note] = None) -> List[str]:
    chosen, _filter = QFileDialog.getOpenFileNames(parent, title, "", filters)
    out = [localize(p, parent, note) for p in chosen]
    return [p for p in out if p]
