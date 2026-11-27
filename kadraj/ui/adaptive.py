"""Telefon ve tablet uyarlaması (mobil 6. adım; M1, M2).

Telefonda (``device.is_phone()``) yalnız Basit Mod vardır ve her sayfa ekranın genişliğine sığmalıdır: satırlar
sığmayanı alt satıra geçirir, büyük pencereler ekran boyunda açılır. Tablet masaüstüyle aynı düzeni kullanır; satırlar
yalnız dikey tutuşta sığmazsa sarılır. Telefon/tablet görünümü masaüstünde ``KADRAJ_FORM_FACTOR=phone|tablet`` ile
denenir (testler ve ekran görüntüleri).

* ``hbox()`` — masaüstünde ``QHBoxLayout``; telefon ve tablette ``FlowLayout`` (sığarsa aynı tek satır, sığmazsa
  alt satıra geçer; aynı ekleme işlevleri).
* ``breakable(text)`` — telefonda uzun yol/adres metnine görünmez kırma noktaları ekler; etiket taşmaz.
* ``margin(desktop, phone)`` — telefonda küçük kenar boşlukları.
* ``CurrentOnlyStack`` — boyut ipucunu yalnız görünen sayfadan alan ``QStackedWidget`` (gizli sayfa dar ekranda
  pencereyi genişletmez).
* ``tabs()`` — mobilde ``CurrentOnlyTabs``: sekme kümesi boyunu yalnız açık sekmeden alır (Legend, tablet dikey).
* ``scroll_both(widget)`` — mobilde iki yönde kayan alan (tablet dikey tutuşta sığmayan Legend sayfaları için).
* ``fit_dialog(dialog, w, h)`` — pencereyi ekrana sığdırır (telefon/tablette çalışma alanı kadar).
* ``h_scroll(widget)`` — telefonda yatayda kaydırılan alan (ör. sahne panosunun sütunları); başka yerde widget'ın
  kendisi.
* ``touch_scroll_all(root)`` — telefon ve tablette kaydırma alanları parmakla sürüklenerek kayar (``QScroller``;
  Qt Widgets Android'de bunu kendiliğinden yapmaz). Kısa dokunma düğmeye tıklama olarak geçer; parmak kayarsa sayfa
  kayar. ``NO_FLICK`` özelliği verilmiş alan ve açılır liste pencereleri hariç.
* ``direct_touch(widget)`` — sürüklenerek kullanılan çizim alanları (zaman çizgisi, aralık çubuğu, maske fırçası,
  eğri): dokunma doğrudan widget'ın fare işlevlerine gider, içinde bulunduğu sayfanın kaydırması araya girmez.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (QAbstractScrollArea, QComboBox, QFrame, QGraphicsView, QHBoxLayout, QLayout, QScrollArea,
                               QScroller, QScrollerProperties, QStackedWidget, QTabWidget, QWidget)

from .. import device
from .scaling import work_area
from .widgets.flow import FlowLayout

#: Bu özelliği ``True`` olan kaydırma alanına parmakla kaydırma bağlanmaz (kendi sürükleme davranışı olan alanlar).
NO_FLICK = "kadraj_no_flick"
#: ``scroll_both`` ve ``h_scroll`` alanlarında ``True``: içerik ekrandan geniş olabilir, yatayda kaydırılır (kasıtlı).
PAN = "kadraj_pan"
_FLICK_ON = "kadraj_flick_on"
#: Parmak bu kadar saniye kıpırdamadan durursa basma, altındaki düğmeye/alana iletilir (Qt varsayılanı 0,25).
PRESS_DELAY_S = 0.2

#: Kırma fırsatı: yol ayırıcı ve noktalamadan sonra görünmez boşluk (U+200B).
_BREAK_AFTER = re.compile(r"([/\\_.\-·,:])")
ZWSP = "​"


def is_phone() -> bool:
    return device.is_phone()


def is_mobile() -> bool:
    return device.is_mobile()


def hbox(parent: Optional[QWidget] = None, *, spacing: Optional[int] = None) -> QLayout:
    """Yatay satır: masaüstünde ``QHBoxLayout``; telefon ve tablette sığmayanı alt satıra geçiren ``FlowLayout``."""
    if device.is_mobile():
        return FlowLayout(parent, spacing=8 if spacing is None else spacing)
    lay = QHBoxLayout(parent) if parent is not None else QHBoxLayout()
    if spacing is not None:
        lay.setSpacing(spacing)
    return lay


def breakable(text: str) -> str:
    """Telefonda uzun yol/adresin ayırıcılarından sonra satır kırılabilsin (metin görünüşte aynı kalır)."""
    if not device.is_phone() or not text:
        return text
    return _BREAK_AFTER.sub(lambda m: m.group(1) + ZWSP, text)


def plain(text: str) -> str:
    """``breakable`` eklerini geri alır (kopyalama, karşılaştırma)."""
    return text.replace(ZWSP, "")


def margin(desktop: int, phone: int) -> int:
    return phone if device.is_phone() else desktop


#: Sarılma olmayacak kadar geniş (px): ``heightForWidth`` burada tek satırlık yüksekliği verir.
_UNWRAPPED_WIDTH = 100_000


def wrapped_extra(page: QWidget, width: int) -> int:
    """``page``'in ``width`` genişliğinde satır sarılması (akış düzeni, sözcük kaydırma) yüzünden ihtiyaç duyduğu ek yükseklik."""
    if not page.hasHeightForWidth():
        return 0
    return max(0, page.heightForWidth(width) - page.heightForWidth(_UNWRAPPED_WIDTH))


class CurrentOnlyStack(QStackedWidget):
    """Boyut ipucu yalnız görünen sayfadan: gizli sayfa (ör. Legend gövdesi) dar ekranı genişletmez.

    Genişliğe bağlı yükseklik de yalnız görünen sayfadandır ve *en küçük* yüksekliktir (en küçük boy + bu genişlikte sarılan
    satırların payı). Qt'nin kendi değeri bütün sayfaların *tercih edilen* yüksekliklerinin en büyüğüdür; kaydırma alanı onu
    en küçük boy sayar ve sayfa gereğinden uzun olurdu: düzenleyicinin zaman çizelgesi ekranın dışına itilirdi. Böylece
    tablette Legend sayfası masaüstündeki gibi ekrana sıkışır, yalnız en küçük boyundan dar/kısa ekranda kayar."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(lambda _i: self.updateGeometry())

    def _current_hint(self, minimum: bool) -> Optional[QSize]:
        page = self.currentWidget()
        if page is None:
            return None
        hint = page.minimumSizeHint() if minimum else page.sizeHint()
        return hint.expandedTo(page.minimumSize())

    def sizeHint(self) -> QSize:  # noqa: N802
        return self._current_hint(False) or super().sizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self._current_hint(True) or super().minimumSizeHint()

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        page = self.currentWidget()
        return page is not None and page.hasHeightForWidth()

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        page = self.currentWidget()
        if page is None or not page.hasHeightForWidth():
            return -1
        return self._current_hint(True).height() + wrapped_extra(page, width)


def stack(parent: Optional[QWidget] = None) -> QStackedWidget:
    """Telefon ve tablette ``CurrentOnlyStack``; masaüstünde (pencerenin en küçük boyu sabit) ``QStackedWidget``."""
    return CurrentOnlyStack(parent) if device.is_mobile() else QStackedWidget(parent)


class CurrentOnlyTabs(QTabWidget):
    """Boyut ipucu yalnız açık sekmeden (+ sekme çubuğu): kapalı geniş sekmeler dar ekranı genişletmez."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(lambda _i: self.updateGeometry())

    def _current_hint(self, minimum: bool) -> Optional[QSize]:
        page = self.currentWidget()
        if page is None:
            return None
        inner = (page.minimumSizeHint() if minimum else page.sizeHint()).expandedTo(page.minimumSize())
        bar = self.tabBar().minimumSizeHint() if minimum else self.tabBar().sizeHint()
        return QSize(max(inner.width() + self.FRAME, bar.width()), inner.height() + bar.height() + self.FRAME)

    FRAME = 4                                                 # sekme sayfasının çerçevesi (iki kenar)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self._current_hint(False) or super().sizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self._current_hint(True) or super().minimumSizeHint()

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        page = self.currentWidget()
        return page is not None and page.hasHeightForWidth()

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        """Yalnız açık sekme (+ sekme çubuğu), en küçük yükseklik; bkz. ``CurrentOnlyStack``."""
        page = self.currentWidget()
        if page is None or not page.hasHeightForWidth():
            return -1
        return self._current_hint(True).height() + wrapped_extra(page, width - self.FRAME)


def tabs(parent: Optional[QWidget] = None) -> QTabWidget:
    """Telefon ve tablette ``CurrentOnlyTabs``; masaüstünde ``QTabWidget``."""
    return CurrentOnlyTabs(parent) if device.is_mobile() else QTabWidget(parent)


def scroll_both(widget: QWidget) -> QWidget:
    """Mobilde ``widget``'ı iki yönde kayan alana koyar (sığmazsa kesilmez, kayar); masaüstünde widget'ın kendisi."""
    if not device.is_mobile():
        return widget
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setProperty(PAN, True)
    area.setWidget(widget)
    return area


def fit_dialog(dialog: QWidget, width: int, height: int) -> None:
    """Masaüstünde ``resize(width, height)``; telefon/tablette en fazla çalışma alanı kadar (ekrandan taşmaz)."""
    if not device.is_mobile():
        dialog.resize(width, height)
        return
    area = work_area(dialog.parentWidget() or dialog)
    w, h = min(width, area.width()), min(height, area.height())
    dialog.setMinimumSize(min(dialog.minimumWidth(), w), min(dialog.minimumHeight(), h))
    dialog.resize(w, h)
    touch_scroll_on_show(dialog)                          # içerik henüz kurulmadı: kaydırma gösterilince bağlanır


def h_scroll(widget: QWidget) -> QWidget:
    """Telefonda yatayda kaydırılan alan içine koyar; başka yerde widget'ı aynen döndürür."""
    if not device.is_phone():
        return widget
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    area.setProperty(PAN, True)
    area.setWidget(widget)
    area.setMinimumHeight(widget.minimumSizeHint().height())
    return area


# -- dokunmatik kaydırma ---------------------------------------------------------------------------------------------
def _flick_excluded(area: QAbstractScrollArea) -> bool:
    if area.property(NO_FLICK) or isinstance(area, QGraphicsView):
        return True
    w = area.parentWidget()
    while w is not None:                                  # açılır listenin (QComboBox) kendi penceresi
        if isinstance(w, QComboBox):
            return True
        w = w.parentWidget()
    return False


def touch_scroll(area: QAbstractScrollArea) -> bool:
    """Kaydırma alanını parmakla kaydırılır yapar (telefon/tablet). Bir alana bir kez bağlanır; bağlandıysa ``True``."""
    if not device.is_mobile() or area.property(_FLICK_ON) or _flick_excluded(area):
        return False
    viewport = area.viewport()
    QScroller.grabGesture(viewport, QScroller.ScrollerGestureType.LeftMouseButtonGesture)
    scroller = QScroller.scroller(viewport)
    props = scroller.scrollerProperties()
    off = QScrollerProperties.OvershootPolicy.OvershootAlwaysOff
    props.setScrollMetric(QScrollerProperties.ScrollMetric.VerticalOvershootPolicy, off)
    props.setScrollMetric(QScrollerProperties.ScrollMetric.HorizontalOvershootPolicy, off)
    props.setScrollMetric(QScrollerProperties.ScrollMetric.MousePressEventDelay, PRESS_DELAY_S)
    scroller.setScrollerProperties(props)
    area.setProperty(_FLICK_ON, True)
    return True


def touch_scroll_all(root: QWidget) -> int:
    """``root`` ve altındaki bütün kaydırma alanlarına parmakla kaydırma bağlar (masaüstünde hiçbir şey yapmaz).

    Sayfa ya da pencere kurulduktan sonra çağrılır; tekrar çağırmak zararsızdır (yalnız yeni alanlara bağlar)."""
    if not device.is_mobile() or root is None:
        return 0
    areas = root.findChildren(QAbstractScrollArea)
    if isinstance(root, QAbstractScrollArea):
        areas.insert(0, root)
    return sum(1 for area in areas if touch_scroll(area))


class _DirectTouch(QObject):
    """Dokunmayı (ilk parmak) widget'ın kendi ``mousePress/Move/ReleaseEvent`` işlevlerine çevirir.

    Olay ``sendEvent`` ile değil doğrudan işleve verilir: sayfadaki ``QScroller`` fare olaylarını görmez, sürüklemeyi
    kaydırmaya çeviremez. Dokunma kabul edildiği için Qt aynı dokunuştan ayrıca fare olayı üretmez."""

    _KINDS = {
        QEvent.Type.TouchBegin: QEvent.Type.MouseButtonPress,
        QEvent.Type.TouchUpdate: QEvent.Type.MouseMove,
        QEvent.Type.TouchEnd: QEvent.Type.MouseButtonRelease,
        QEvent.Type.TouchCancel: QEvent.Type.MouseButtonRelease,
    }

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        kind = self._KINDS.get(event.type())
        if kind is None or not isinstance(obj, QWidget):
            return False
        if event.type() == QEvent.Type.TouchBegin:
            wanted = getattr(obj, "_kadraj_touch_when", None)
            if wanted is not None and not wanted():               # şu an sürüklenecek bir şey yok: sayfa kaysın
                event.ignore()
                return False
        points = event.points()
        if points:
            pt = points[0]
            left = Qt.MouseButton.LeftButton
            button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else left
            buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else left
            local = obj.mapFromGlobal(pt.globalPosition())
            mouse = QMouseEvent(kind, local, local, pt.globalPosition(), button, buttons,
                                Qt.KeyboardModifier.NoModifier)
            handler = {QEvent.Type.MouseButtonPress: obj.mousePressEvent,
                       QEvent.Type.MouseMove: obj.mouseMoveEvent,
                       QEvent.Type.MouseButtonRelease: obj.mouseReleaseEvent}[kind]
            handler(mouse)
        event.accept()
        return True


_DIRECT_TOUCH: Optional[_DirectTouch] = None


class _ScrollOnShow(QObject):
    """Widget her gösterildiğinde içindeki (yeni) kaydırma alanlarına parmakla kaydırma bağlar."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget):
            touch_scroll_all(obj)
        return False


_SCROLL_ON_SHOW: Optional[_ScrollOnShow] = None


def touch_scroll_on_show(widget: QWidget) -> bool:
    """Pencere/sayfa gösterilince ``touch_scroll_all`` (içeriği sonradan kurulan pencereler için). Masaüstünde yok."""
    global _SCROLL_ON_SHOW
    if not device.is_mobile():
        return False
    if _SCROLL_ON_SHOW is None:
        _SCROLL_ON_SHOW = _ScrollOnShow()
    widget.installEventFilter(_SCROLL_ON_SHOW)
    return True


def direct_touch(widget: QWidget, when: Optional[Callable[[], bool]] = None) -> bool:
    """Telefon/tablette widget'a dokunma doğrudan gider (sürükleme sayfayı kaydırmaz). Masaüstünde hiçbir şey yapmaz.

    ``when`` verilirse dokunuş yalnız o ``True`` dönerken alınır (ör. önizlemede karşılaştırma açıkken); değilse dokunuş
    sayfaya geçer, sayfa kayar."""
    global _DIRECT_TOUCH
    if not device.is_mobile():
        return False
    if _DIRECT_TOUCH is None:
        _DIRECT_TOUCH = _DirectTouch()
    widget._kadraj_touch_when = when
    widget.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
    widget.installEventFilter(_DIRECT_TOUCH)
    return True
