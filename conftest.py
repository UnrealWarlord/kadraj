"""Ortak test düzeni: ekransız Qt ve gerçek kullanıcı verisine dokunmayan yalıtılmış ev dizini.

* ``QT_QPA_PLATFORM=offscreen`` (Qt içe aktarılmadan önce).
* Her test oturumu için geçici ``HOME`` / ``KADRAJ_HOME``; ``~/.kadraj`` asla okunmaz/yazılmaz.
* ``qapp`` (pytest-qt) tek ``QApplication`` sağlar; ``ctx`` fikstürü her test için temiz ayar + bağlam üretir.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TEST_HOME = Path(tempfile.mkdtemp(prefix="kadraj-tests-"))
os.environ["HOME"] = str(_TEST_HOME)
os.environ["USERPROFILE"] = str(_TEST_HOME)
os.environ["KADRAJ_HOME"] = str(_TEST_HOME / ".kadraj")
atexit.register(shutil.rmtree, _TEST_HOME, True)

import pytest  # noqa: E402

from kadraj.storage import vault as _vault  # noqa: E402

_vault.set_default_keyring(_vault.MemoryKeyring())      # gerçek OS keyring'e testler asla dokunmaz


@pytest.fixture(autouse=True)
def _fresh_keyring():
    """Her test için temiz bellek-içi keyring (kasa anahtarları testler arasında sızmaz)."""
    _vault.set_default_keyring(_vault.MemoryKeyring())
    yield


@pytest.fixture(autouse=True)
def _purge_deleted_widgets():
    """Her testten sonra ``deleteLater``'ı bekleyen widget'ları gerçekten sil: aksi halde süreç boyunca birikir ve her
    tema/QSS değişimi (mod geçişi) bütün birikmiş widget'ları yeniden boyayıp testleri yavaşlatır."""
    yield
    try:
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
            app.processEvents()
    except Exception:
        pass


@pytest.fixture(scope="session")
def test_home() -> Path:
    return _TEST_HOME


@pytest.fixture()
def clean_home(monkeypatch, tmp_path):
    """Her test için ayrı ``KADRAJ_HOME``."""
    home = tmp_path / "kadraj_home"
    monkeypatch.setenv("KADRAJ_HOME", str(home))
    return home


@pytest.fixture()
def ctx(qapp, clean_home):
    """Temiz ayarlı, projesiz, animasyonsuz bağlam + tema."""
    from kadraj.ui.shell.app import create_app
    from kadraj.core.settings import SettingsStore

    settings = SettingsStore()
    settings.set("wizard.done", True)
    settings.set("language", "tr")
    _app, context, theme = create_app(["kadraj"], settings=settings)
    theme.set_animations(False)
    yield context
    theme.finish_transitions()
    context.shutdown()
