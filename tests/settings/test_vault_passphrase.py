"""Anahtar zinciri olmayan cihazda (Android) kasa: parola belirleme, kilit açma, anahtar kaydederken hazırlık (M19).

Gerçek ``Vault`` (geçici dosya, düşük PBKDF2 yinelemesi) ve ``cryptography`` olmadan çalışan Fernet; pencereler
sahte yanıtlarla değiştirilir (modal pencere açılmaz).
"""
from __future__ import annotations

import sys

import pytest

from kadraj.core.contracts import SmartError
from kadraj.modules.settings import vault_ui
from kadraj.modules.settings.page_legend import SettingsLegendPage
from kadraj.modules.settings.page_simple import SettingsSimplePage
from kadraj.storage import vault as V
from kadraj.storage.vault import Vault

PASS = "telefon-parolasi-42"
KEY = "pexels-test-key-0123456789"


@pytest.fixture()
def phone_vault(ctx, tmp_path, monkeypatch):
    """Android gibi: keyring modülü yok, cryptography yok."""
    monkeypatch.setattr(V, "_resolve_keyring", lambda explicit: None)
    monkeypatch.setitem(sys.modules, "cryptography", None)
    monkeypatch.setitem(sys.modules, "cryptography.fernet", None)
    vault = Vault(tmp_path / "vault.bin", kdf_iterations=1000)
    ctx.vault = vault
    return vault


@pytest.fixture()
def answers(monkeypatch):
    """Pencerelerin vereceği yanıtlar; her çağrı kaydedilir."""
    box = {"new": [(PASS, PASS)], "unlock": [PASS], "calls": []}

    def new(parent):
        box["calls"].append("new")
        return box["new"].pop(0) if box["new"] else None

    def unlock(parent):
        box["calls"].append("unlock")
        return box["unlock"].pop(0) if box["unlock"] else None

    monkeypatch.setattr(vault_ui, "ask_new_passphrase", new)
    monkeypatch.setattr(vault_ui, "ask_passphrase", unlock)
    return box


def test_service_reports_that_a_passphrase_is_needed(svc, phone_vault):
    state, message = svc.vault_state()
    assert state == "needs_passphrase" and "parola" in message.lower()
    with pytest.raises(ValueError):
        svc.set_vault_passphrase(PASS, PASS + "x")                              # tekrar farklı
    with pytest.raises(ValueError):
        svc.set_vault_passphrase("kısa", "kısa")
    assert not phone_vault.path.exists()
    svc.set_vault_passphrase(PASS, PASS)
    assert svc.vault_state()[0] == "ready" and phone_vault.status().mode == "passphrase"
    svc.save_key("pexels", KEY)
    assert phone_vault.get_secret("pexels") == KEY
    assert PASS not in phone_vault.path.read_text(encoding="utf-8")               # parola diske yazılmaz
    assert KEY not in phone_vault.path.read_text(encoding="utf-8")


def test_locked_vault_unlocks_with_the_passphrase(svc, ctx, phone_vault):
    svc.set_vault_passphrase(PASS, PASS)
    svc.save_key("pexels", KEY)
    ctx.vault = reopened = Vault(phone_vault.path, kdf_iterations=1000)          # uygulama yeniden açıldı
    assert svc.vault_state()[0] == "locked"
    with pytest.raises(SmartError) as ei:
        svc.unlock_vault("yanlis-parola-00")
    assert ei.value.code == "vault.wrong_passphrase"
    svc.unlock_vault(PASS)
    assert svc.vault_state()[0] == "ready" and reopened.get_secret("pexels") == KEY


def test_desktop_with_a_keyring_never_asks(svc, ctx, tmp_path):
    ctx.vault = Vault(tmp_path / "vault.bin", kdf_iterations=1000)               # test keyring'i (bellek) var
    assert svc.vault_state() == ("empty", "")
    svc.save_key("pexels", KEY)
    assert ctx.vault.status().mode == "keyring"


def test_adding_the_first_key_asks_for_a_passphrase_then_saves(ctx, svc, phone_vault, answers, qtbot):
    page = SettingsSimplePage(ctx)
    qtbot.addWidget(page)
    assert page.vault_action.isVisibleTo(page) and page.vault_action.text() == "Parola belirle"
    assert vault_ui.save_key(page, svc, "pexels", KEY, page._say, page._report)
    assert answers["calls"] == ["new"] and phone_vault.get_secret("pexels") == KEY
    assert page.status.text() == "Anahtar kaydedildi."
    assert not page.vault_action.isVisibleTo(page)                               # kasa hazır: düğme gizli


def test_cancelling_the_passphrase_saves_nothing(ctx, svc, phone_vault, answers, qtbot):
    answers["new"] = []
    page = SettingsSimplePage(ctx)
    qtbot.addWidget(page)
    assert not vault_ui.save_key(page, svc, "pexels", KEY, page._say, page._report)
    assert not phone_vault.path.exists()


def test_mismatched_passphrases_are_explained(ctx, svc, phone_vault, answers, qtbot):
    answers["new"] = [(PASS, "baska-parola-99")]
    page = SettingsSimplePage(ctx)
    qtbot.addWidget(page)
    page._on_vault_action()
    assert page.status.text() == "Parolalar aynı değil." and not phone_vault.path.exists()


def test_unlock_button_on_both_pages(ctx, svc, phone_vault, answers, qtbot):
    svc.set_vault_passphrase(PASS, PASS)
    svc.save_key("pexels", KEY)
    ctx.vault = Vault(phone_vault.path, kdf_iterations=1000)
    simple = SettingsSimplePage(ctx)
    qtbot.addWidget(simple)
    assert simple.vault_action.isVisibleTo(simple) and simple.vault_action.text() == "Kilidi aç"
    simple._on_vault_action()
    assert answers["calls"] == ["unlock"] and simple.status.text() == "Kasa açıldı."
    assert ctx.vault.get_secret("pexels") == KEY and not simple.vault_action.isVisibleTo(simple)
    ctx.vault.lock()
    legend = SettingsLegendPage(ctx)
    qtbot.addWidget(legend)
    legend.nav.setCurrentRow(1)                                                  # API bölümü ilk seçilişte kurulur
    assert legend.vault_action.isVisibleTo(legend) and legend.vault_action.text() == "Kilidi aç"
    answers["unlock"] = ["yanlis-parola-00"]
    legend._on_vault_action()
    assert ctx.vault.status().state == "locked" and legend.status.text()             # yanlış parola: kart/mesaj
    answers["unlock"] = [PASS]
    legend._on_vault_action()
    assert ctx.vault.status().state == "ready"


def test_saving_into_a_locked_vault_unlocks_first(ctx, svc, phone_vault, answers, qtbot):
    svc.set_vault_passphrase(PASS, PASS)
    ctx.vault = Vault(phone_vault.path, kdf_iterations=1000)
    page = SettingsLegendPage(ctx)
    qtbot.addWidget(page)
    assert vault_ui.save_key(page, svc, "pexels", KEY, page._say, page._report)
    assert answers["calls"] == ["unlock"] and ctx.vault.get_secret("pexels") == KEY


def test_new_passphrase_dialog_masks_both_fields(qtbot):
    from PySide6.QtWidgets import QLineEdit
    dialog = vault_ui.NewPassphraseDialog()
    qtbot.addWidget(dialog)
    assert dialog.first.echoMode() == QLineEdit.EchoMode.Password == dialog.second.echoMode()
    dialog.first.setText("a" * 8)
    dialog.second.setText("b" * 8)
    assert dialog.values() == ("a" * 8, "b" * 8)


# ---- parola unutuldu / kasa bozuk: onaylı sıfırlama ----------------------------------------------------------
def test_forgotten_passphrase_resets_the_vault_after_confirmation(ctx, svc, phone_vault, answers, qtbot,
                                                                  monkeypatch):
    svc.set_vault_passphrase(PASS, PASS)
    svc.save_key("pexels", KEY)
    ctx.vault = Vault(phone_vault.path, kdf_iterations=1000)                     # yeniden açıldı, parola unutuldu
    page = SettingsSimplePage(ctx)
    qtbot.addWidget(page)
    assert page.vault_reset.isVisibleTo(page) and page.vault_reset.text() == "Parolamı unuttum"
    monkeypatch.setattr(vault_ui, "confirm_reset", lambda parent: False)         # vazgeçti: hiçbir şey değişmez
    page._on_vault_reset()
    assert ctx.vault.status().state == "locked" and phone_vault.path.exists()
    monkeypatch.setattr(vault_ui, "confirm_reset", lambda parent: True)
    page._on_vault_reset()
    assert phone_vault.path.with_name("vault.bin.bad").exists() and not phone_vault.path.exists()
    assert svc.vault_state()[0] == "needs_passphrase" and page.status.text().startswith("Kasa sıfırlandı")
    assert page.vault_action.text() == "Parola belirle" and not page.vault_reset.isVisibleTo(page)
    assert vault_ui.save_key(page, svc, "pexels", "yeni-anahtar-0123456789", page._say, page._report)
    assert ctx.vault.get_secret("pexels") == "yeni-anahtar-0123456789"


def test_legend_page_offers_reset_for_a_locked_vault(ctx, svc, phone_vault, qtbot, monkeypatch):
    svc.set_vault_passphrase(PASS, PASS)
    ctx.vault = Vault(phone_vault.path, kdf_iterations=1000)
    legend = SettingsLegendPage(ctx)
    qtbot.addWidget(legend)
    legend.nav.setCurrentRow(1)
    assert legend.vault_reset.isVisibleTo(legend) and legend.vault_reset.text() == "Parolamı unuttum"
    monkeypatch.setattr(vault_ui, "confirm_reset", lambda parent: True)
    legend._on_vault_reset()
    assert svc.vault_state()[0] == "needs_passphrase" and legend.vault_action.text() == "Parola belirle"


def test_reset_dialog_defaults_to_cancel(qtbot):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    seen = {}

    def press_default():
        box = QApplication.activeModalWidget()
        if not isinstance(box, QMessageBox):
            QTimer.singleShot(20, press_default)
            return
        seen["default"] = box.defaultButton().text()
        box.defaultButton().click()

    QTimer.singleShot(0, press_default)
    assert vault_ui.confirm_reset(None) is False                                  # Enter/varsayılan → Vazgeç
    assert seen["default"] == "Vazgeç"


def test_error_card_reset_button_goes_through_the_shell(ctx, svc, phone_vault, qtbot, monkeypatch):
    from kadraj.ui.shell.main_window import MainWindow
    svc.set_vault_passphrase(PASS, PASS)
    phone_vault.path.write_text("bozuk", encoding="utf-8")                        # kasa dosyası bozuldu
    ctx.vault = Vault(phone_vault.path, kdf_iterations=1000)
    err = ctx.vault.status().error
    assert err.code == "vault.corrupt"
    reset = next(a for a in err.actions if a.kind == "custom")
    win = MainWindow(ctx, ctx.theme)
    qtbot.addWidget(win)
    monkeypatch.setattr(vault_ui, "confirm_reset", lambda parent: True)
    win.show_error(err)
    dialog = win._error_dialogs[-1]
    dialog.card.actionTriggered.emit(reset)                                       # karttaki "Kasayı sıfırla"
    qtbot.waitUntil(lambda: ctx.vault.status().state != "error", timeout=1000)
    assert phone_vault.path.with_name("vault.bin.bad").read_text(encoding="utf-8") == "bozuk"
    assert win.status.message().startswith("Kasa sıfırlandı")


def test_error_card_settings_button_opens_settings(ctx, qtbot):
    from kadraj.core.contracts import SmartAction, SmartError
    from kadraj.ui.shell.main_window import MainWindow
    win = MainWindow(ctx, ctx.theme)
    qtbot.addWidget(win)
    err = SmartError("vault.locked", "Kasa kilitli", "özet",
                     actions=(SmartAction("open_settings", "Ayarlar", "open_settings", {"section": "vault"}),))
    win.show_error(err)
    win._error_dialogs[-1].card.actionTriggered.emit(err.actions[0])
    assert ctx.current_module == "settings"
