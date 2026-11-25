"""Akıllı Hata — ``translate(exc)`` ve genel (yedek) kurallar.

Sıra: (1) zaten ``SmartError`` ise aynen döner; (2) ``kadraj.core.error_catalog.RULES``
(Haiku, Adım 2: FFmpeg / yt-dlp / HTTP / CUDA / disk) ``priority`` sırasıyla denenir;
(3) bu dosyadaki genel kurallar (ağ yok, zaman aşımı, disk dolu, izin, dosya yok);
(4) hiçbiri eşleşmezse genel "beklenmeyen hata" kartı. **Asla istisna atmaz.**
"""
from __future__ import annotations

import errno
import importlib
import socket
from typing import Callable, List, Optional, Sequence

from .contracts import (ErrorContext, ErrorRule, Severity, SmartAction,
                        SmartError)
from .i18n import t

_extra_rules: List[ErrorRule] = []
_catalog_rules: Optional[List[ErrorRule]] = None


def register_rule(rule: ErrorRule) -> None:
    """Çalışma anında kural ekler (modüller kendi hatalarını sözlüğe ekleyebilir)."""
    _extra_rules.append(rule)


def reset_catalog_cache() -> None:
    """Testler için: sözlük bir sonraki çağrıda yeniden yüklenir."""
    global _catalog_rules
    _catalog_rules = None


def _catalog() -> Sequence[ErrorRule]:
    global _catalog_rules
    if _catalog_rules is None:
        try:
            mod = importlib.import_module("kadraj.core.error_catalog")
            rules = list(getattr(mod, "RULES", ()))
        except ImportError:
            rules = []
        except Exception:  # bozuk sözlük çekirdeği düşürmesin
            rules = []
        _catalog_rules = [r for r in rules if isinstance(r, ErrorRule)]
    return _catalog_rules


def _class_names(exc: BaseException) -> set:
    return {c.__name__ for c in type(exc).__mro__}


_NETWORK_NAMES = {"URLError", "ConnectError", "ConnectTimeout", "ReadTimeout", "NetworkError",
                  "ConnectionError", "ConnectionResetError", "ConnectionRefusedError",
                  "ConnectionAbortedError", "RemoteProtocolError", "gaierror", "TransportError"}


def _is_network(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, socket.gaierror)):
        return True
    if _class_names(exc) & _NETWORK_NAMES:
        return True
    reason = getattr(exc, "reason", None)          # urllib.error.URLError.reason
    return isinstance(reason, (OSError, socket.gaierror)) and not isinstance(reason, FileNotFoundError)


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, (TimeoutError, socket.timeout)) or \
        any("Timeout" in n for n in _class_names(exc))


def is_network_error(exc: BaseException) -> bool:
    """Ağa ulaşılamadı mı (DNS/bağlantı reddi/kopma)? Sözlük kuralları kod adını değiştirse de geçerlidir."""
    return _is_network(exc)


def is_timeout_error(exc: BaseException) -> bool:
    return _is_timeout(exc)


def _tech(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _ctx_src(ctx: ErrorContext) -> str:
    return str(ctx.get("source") or ctx.get("provider") or "")


def _build_offline(exc: BaseException, ctx: ErrorContext) -> SmartError:
    return SmartError("net.offline", t("err.net.offline.title"), t("err.net.offline.summary"),
                      cause=t("err.net.offline.cause"),
                      steps=(t("err.net.offline.step1"), t("err.net.offline.step2")),
                      actions=(SmartAction("retry", t("err.action.retry"), "retry"),),
                      severity=Severity.WARNING, technical=_tech(exc), retryable=True,
                      source=_ctx_src(ctx))


def _build_timeout(exc: BaseException, ctx: ErrorContext) -> SmartError:
    return SmartError("net.timeout", t("err.net.timeout.title"), t("err.net.timeout.summary"),
                      cause=t("err.net.timeout.cause"), steps=(t("err.net.timeout.step1"),),
                      actions=(SmartAction("retry", t("err.action.retry"), "retry"),),
                      severity=Severity.WARNING, technical=_tech(exc), retryable=True,
                      source=_ctx_src(ctx))


def _build_disk_full(exc: BaseException, ctx: ErrorContext) -> SmartError:
    return SmartError("disk.full", t("err.disk.full.title"), t("err.disk.full.summary"),
                      cause=t("err.disk.full.cause"),
                      steps=(t("err.disk.full.step1"), t("err.disk.full.step2")),
                      technical=_tech(exc), source=_ctx_src(ctx))


def _build_permission(exc: BaseException, ctx: ErrorContext) -> SmartError:
    path = getattr(exc, "filename", None) or ctx.get("path") or ""
    return SmartError("fs.permission", t("err.fs.permission.title"),
                      t("err.fs.permission.summary", path=path),
                      cause=t("err.fs.permission.cause"), steps=(t("err.fs.permission.step1"),),
                      technical=_tech(exc), source=_ctx_src(ctx))


def _build_not_found(exc: BaseException, ctx: ErrorContext) -> SmartError:
    tool = ctx.get("tool")
    if tool:
        return SmartError("tool.missing", t("err.tool.missing.title", tool=tool),
                          t("err.tool.missing.summary", tool=tool),
                          cause=t("err.tool.missing.cause"), steps=(t("err.tool.missing.step1", tool=tool),),
                          actions=(SmartAction("settings", t("err.action.open_settings"),
                                               "open_settings", {"section": "tools"}),),
                          technical=_tech(exc), source=_ctx_src(ctx))
    path = getattr(exc, "filename", None) or ctx.get("path") or ""
    return SmartError("fs.not_found", t("err.fs.not_found.title"),
                      t("err.fs.not_found.summary", path=path),
                      cause=t("err.fs.not_found.cause"), steps=(t("err.fs.not_found.step1"),),
                      technical=_tech(exc), source=_ctx_src(ctx))


def _build_generic(exc: BaseException, ctx: ErrorContext) -> SmartError:
    return SmartError("app.unexpected", t("err.generic.title"), t("err.generic.summary"),
                      cause=t("err.generic.cause"), steps=(t("err.generic.step1"),),
                      actions=(SmartAction("copy", t("err.action.copy_details"), "copy",
                                           {"text": _tech(exc)}),),
                      technical=_tech(exc), source=_ctx_src(ctx))


def _is_enospc(exc: BaseException, _c: ErrorContext) -> bool:
    return isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT)


_BUILTIN_RULES: List[ErrorRule] = [
    ErrorRule("builtin.disk_full", _is_enospc, _build_disk_full, 900),
    ErrorRule("builtin.timeout", lambda e, c: _is_timeout(e), _build_timeout, 910),
    ErrorRule("builtin.network", lambda e, c: _is_network(e), _build_offline, 920),
    ErrorRule("builtin.permission", lambda e, c: isinstance(e, PermissionError), _build_permission, 930),
    ErrorRule("builtin.not_found", lambda e, c: isinstance(e, FileNotFoundError), _build_not_found, 940),
]


def translate(exc: BaseException, context: Optional[ErrorContext] = None) -> SmartError:
    """``Translator`` sözleşmesinin uygulaması (bkz. ``contracts.Translator``)."""
    ctx: ErrorContext = context or {}
    if isinstance(exc, SmartError):
        return exc
    try:
        rules = sorted([*_catalog(), *_extra_rules, *_BUILTIN_RULES], key=lambda r: r.priority)
        for rule in rules:
            try:
                if rule.matches(exc, ctx):
                    result = rule.build(exc, ctx)
                    if isinstance(result, SmartError):
                        return result
            except Exception:
                continue          # kötü bir kural diğerlerini engellemesin
    except Exception:
        pass
    return _build_generic(exc, ctx)
