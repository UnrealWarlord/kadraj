"""Android köprüsü: Python'dan uygulamanın Java parçasına komut (``docs/steps/MOBILE_6.md``, karar M20).

PySide6'nın Android yapısında JNI bağı (``QJniObject``) yok. Bu yüzden iki taraf dosyayla konuşur: Python uygulamanın
iç klasörüne (``~/.kadraj/bridge``; Android'de ``files/.kadraj/bridge``) ``<kimlik>.req`` adlı bir JSON dosyası
yazar. APK'daki ``org.insibag.kadraj.Bridge`` (``packaging/android/java``) klasörü ``FileObserver`` ile izler, işi
yapar ve sonucu ``<kimlik>.res`` olarak yazar: ``{"ok": true}`` ya da ``{"ok": false, "error": "no_app"}``.
Klasör yalnız bu uygulamaya açıktır; başka bir uygulama komut gönderemez.

İşler:

* ``open`` — dosyayı uygun uygulamayla açar (oynatıcı, galeri, PDF okuyucu).
* ``share`` — sistemin paylaşım menüsü (WhatsApp, Drive, e-posta…).
* ``screen`` — ekran açık kalsın (kayıt ve uzun işler sürerken; ``ScreenKeeper``).
* ``scan`` — yeni dosyaları galeriye tanıtır.

Köprü çalışıyorsa ``ready`` dosyasında protokol sürümü ve **bu sürecin** kimliği (pid) yazar; eski bir oturumdan
kalan dosya köprü var sanılmasın diye pid denetlenir. Masaüstünde köprü yoktur (``available()`` → ``False``);
çağıranlar masaüstü yolunu kullanır. Modül Qt'ye bağlı değildir.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Set

from . import device, paths

PROTOCOL = 1
READY = "ready"
#: Java tarafının bildirdiği hata kodları (``Bridge.java``) ve Python'un kendi kodları.
ERRORS = ("not_found", "not_shared", "no_app", "denied", "bad_request", "unknown_action", "failed",
          "unavailable", "timeout")


def bridge_dir() -> Path:
    return paths.home_dir() / "bridge"


def available() -> bool:
    """Köprü bu süreçte çalışıyor mu? (yalnız Android; ``ready`` dosyası ve pid)."""
    if not device.is_android():
        return False
    try:
        data = json.loads((bridge_dir() / READY).read_text(encoding="utf-8"))
        return int(data.get("protocol") or 0) >= PROTOCOL and int(data.get("pid") or -1) == os.getpid()
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def send(action: str, **fields: Any) -> Optional[str]:
    """Komutu yazar ve kimliğini döner. Köprü yoksa ya da yazılamazsa ``None``.

    Dosya önce ``.tmp`` adıyla yazılır, sonra ``.req`` yapılır: Java yarım dosya okumaz.
    """
    if not available():
        return None
    req_id = f"{int(time.time() * 1000):x}-{secrets.token_hex(4)}"
    folder = bridge_dir()
    tmp = folder / f"{req_id}.tmp"
    try:
        tmp.write_text(json.dumps({"id": req_id, "action": action, **fields}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, folder / f"{req_id}.req")
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return None
    return req_id


def take_result(req_id: str) -> Optional[Dict[str, Any]]:
    """Sonuç geldiyse okur, dosyayı siler ve döner; henüz gelmediyse ``None``."""
    path = bridge_dir() / f"{req_id}.res"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        path.unlink()
    except OSError:
        pass
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return {"ok": False, "error": "failed"}
    return data


def open_file(path: "str | os.PathLike[str]", mime: str = "") -> Optional[str]:
    return send("open", path=str(path), mime=mime)


def share(path: "str | os.PathLike[str]", mime: str = "", title: str = "") -> Optional[str]:
    return send("share", path=str(path), mime=mime, title=title)


def scan(files: Iterable["str | os.PathLike[str]"]) -> Optional[str]:
    return send("scan", paths=[str(f) for f in files])


class ScreenKeeper:
    """"Ekran açık kalsın" isteklerini nedenine göre toplar (ör. ``recorder``, ``jobs``).

    Bir neden bile açık istiyorsa ekran kararmaz; hepsi bitince sistem yeniden karartabilir. Köprü yoksa (masaüstü)
    hiçbir şey yapmaz; köprü sonradan gelirse bir sonraki değişiklikte durum yeniden gönderilir.
    """

    def __init__(self, sender: Optional[Callable[[bool], Optional[str]]] = None) -> None:
        self._reasons: Set[str] = set()
        self._sent: Optional[bool] = None
        self._send = sender or (lambda on: send("screen", on=on))

    @property
    def on(self) -> bool:
        return bool(self._reasons)

    def reasons(self) -> Set[str]:
        return set(self._reasons)

    def set(self, reason: str, on: bool) -> None:
        if on:
            self._reasons.add(reason)
        else:
            self._reasons.discard(reason)
        want = bool(self._reasons)
        if want != self._sent and self._send(want) is not None:
            self._sent = want


#: Uygulama genelindeki tek örnek: ``bridge.screen.set("recorder", True)``.
screen = ScreenKeeper()
