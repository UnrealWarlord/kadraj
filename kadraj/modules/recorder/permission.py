"""Mikrofon izni: Android'de ``RECORD_AUDIO`` çalışma zamanında istenir (Qt'nin ``QMicrophonePermission``'ı, M18).

* Masaüstünde (PortAudio yolu) izin sorusu işletim sisteminindir: ``AlwaysGranted`` hep "granted" der, davranış
  değişmez.
* Android'de ``QtMicPermission``: ``status()`` → ``granted`` / ``denied`` / ``undetermined``; ``request()`` sistemin
  izin penceresini açar, sonuç ``done(bool)`` ile arayüz iş parçacığında gelir. "Bir daha sorma" seçilmişse Android
  pencereyi göstermez, sonuç hemen "verilmedi" gelir; servis bu durumda telefon ayarlarını anlatan hata kartını
  gösterir.

Servis (``service.py``) kayıt ya da mikrofon testi başlamadan önce sorar; izin gelince istenen işi kendisi başlatır.
"""
from __future__ import annotations

from typing import Any, Callable

GRANTED, DENIED, UNDETERMINED = "granted", "denied", "undetermined"


class MicPermission:
    """Arayüz: ``status()`` ve ``request(context, done)``."""

    def status(self) -> str:
        raise NotImplementedError

    def request(self, context: Any, done: Callable[[bool], None]) -> None:
        raise NotImplementedError


class AlwaysGranted(MicPermission):
    """Masaüstü: izin uygulama içinden istenmez."""

    def status(self) -> str:
        return GRANTED

    def request(self, context: Any, done: Callable[[bool], None]) -> None:
        done(True)


class QtMicPermission(MicPermission):
    """Android: ``QCoreApplication.checkPermission`` / ``requestPermission`` ile ``QMicrophonePermission``."""

    @staticmethod
    def _qt() -> Any:
        from PySide6 import QtCore
        return QtCore

    def _status_of(self, value: Any) -> str:
        status = self._qt().Qt.PermissionStatus
        if value == status.Granted:
            return GRANTED
        if value == status.Denied:
            return DENIED
        return UNDETERMINED

    def status(self) -> str:
        qc = self._qt()
        app = qc.QCoreApplication.instance()
        if app is None:
            return UNDETERMINED
        try:
            return self._status_of(app.checkPermission(qc.QMicrophonePermission()))
        except Exception:  # noqa: BLE001 — izin sistemi yoksa sor (Qt "Granted" der)
            return UNDETERMINED

    def request(self, context: Any, done: Callable[[bool], None]) -> None:
        qc = self._qt()
        app = qc.QCoreApplication.instance()
        if app is None:
            done(False)
            return

        def finished(*args: Any) -> None:
            perm = args[0] if args else None
            try:
                value = perm.status() if perm is not None and hasattr(perm, "status") else \
                    app.checkPermission(qc.QMicrophonePermission())
            except Exception:  # noqa: BLE001
                value = app.checkPermission(qc.QMicrophonePermission())
            done(self._status_of(value) == GRANTED)

        app.requestPermission(qc.QMicrophonePermission(), context, finished)


def default() -> MicPermission:
    """Android'de (Qt ses yolu) gerçek izin, masaüstünde ``AlwaysGranted``."""
    from .devices import audio_backend
    from ... import device
    return QtMicPermission() if audio_backend() == "qt" and device.is_android() else AlwaysGranted()
