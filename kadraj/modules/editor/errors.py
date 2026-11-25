"""Düzenleyici hata kartları: anahtar ``editor.err.<key>.{title,summary,cause,step1..3}`` dizelerinden kurulur."""
from __future__ import annotations

from typing import Any, Sequence

from ...core import i18n
from ...core.contracts import SmartAction, SmartError


def smart(key: str, *, actions: Sequence[SmartAction] = (), technical: str = "", retryable: bool = False,
          **fmt: Any) -> SmartError:
    base = f"editor.err.{key}"
    steps = [i18n.t(f"{base}.step{i}", **fmt) for i in (1, 2, 3) if i18n.has(f"{base}.step{i}")]
    cause = i18n.t(f"{base}.cause", **fmt) if i18n.has(f"{base}.cause") else ""
    return SmartError(f"editor.{key}", i18n.t(f"{base}.title", **fmt), i18n.t(f"{base}.summary", **fmt), cause=cause,
                      steps=steps, actions=tuple(actions), technical=technical, retryable=retryable)


def install_hint(key: str, packages: str, **fmt: Any) -> SmartError:
    """İsteğe bağlı ağır paket yok: ``pip install`` komutunu kopyalatan "İsteğe bağlı paketi kur" eylemi."""
    return smart(key, actions=[SmartAction("install_optional", i18n.t("editor.err.install_action"), "copy",
                                           {"text": f"pip install {packages}"})], packages=packages, **fmt)
