"""Olay veri yolu — konu tabanlı yayın/abone, Qt sinyali üstünde.

Tek bir ``message(topic, payload)`` sinyali vardır; abonelik konuya göre süzülür.
Başka bir iş parçacığından ``emit`` çağrılırsa Qt, alıcıların yaşadığı iş
parçacığına kuyruklu teslim yapar; aboneler ana iş parçacığında çalışır.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, DefaultDict, List, Optional

from PySide6.QtCore import QObject, Signal


class EventBus(QObject):
    message = Signal(str, object)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._subs: DefaultDict[str, List[Callable[[Any], None]]] = defaultdict(list)
        self.message.connect(self._dispatch)

    def emit(self, topic: str, payload: Any = None) -> None:
        self.message.emit(topic, payload)

    def subscribe(self, topic: str, callback: Callable[[Any], None]) -> Callable[[], None]:
        self._subs[topic].append(callback)

        def _unsubscribe() -> None:
            try:
                self._subs[topic].remove(callback)
            except ValueError:
                pass
        return _unsubscribe

    def _dispatch(self, topic: str, payload: Any) -> None:
        for cb in list(self._subs.get(topic, ())):
            try:
                cb(payload)
            except Exception as exc:  # bir abone diğerlerini ya da uygulamayı düşüremez
                self._report(exc, topic)

    def _report(self, exc: Exception, topic: str) -> None:
        import sys
        import traceback
        print(f"[kadraj] olay abonesi hata verdi ({topic}): {exc!r}", file=sys.stderr)
        traceback.print_exc()
