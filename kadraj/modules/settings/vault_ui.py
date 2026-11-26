"""Kasa parolası pencereleri (Basit ve Legend ortak): parola belirleme, kilit açma ve anahtar kaydederken hazırlık.

Sistem anahtar zinciri olmayan cihazlarda (Android; M4, M19) kasa bir parolayla kurulur ve her oturumda o parolayla
açılır. Parolalar yalnız maskeli alanlarda ve bellekte durur; diske, ayarlara ya da log'a yazılmaz. Parola unutulursa
ya da dosya bozuksa kasa onayla sıfırlanır (eski dosya ``.bad`` yedeği olarak kalır; telefonda dosya elle silinemez).
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QVBoxLayout, QWidget)

from ...core import i18n
from ...core.contracts import SmartError
from .strings import K

#: Kasa hazırlığı gerektiren durumlar (``vault_state()`` ya da ``SmartError.code``) → düğme metni
ACTION_LABELS = {"locked": K.BTN_UNLOCK, "needs_passphrase": K.BTN_SET_PASS}
#: Sıfırlama düğmesi gereken durumlar → düğme metni (kilitli: parola unutulmuş olabilir; error: dosya bozuk/anahtar yok)
RESET_LABELS = {"locked": K.BTN_FORGOT, "error": K.BTN_RESET}
_NEEDS_PASS = ("needs_passphrase", "vault.needs_passphrase")
_LOCKED = ("locked", "vault.locked")


class NewPassphraseDialog(QDialog):
    """İki maskeli alan (parola + tekrar) ve uyarı notu."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.t(K.PASS_TITLE))
        lay = QVBoxLayout(self)
        note = QLabel(i18n.t(K.PASS_NEW_NOTE))
        note.setWordWrap(True)
        lay.addWidget(note)
        form = QFormLayout()
        self.first, self.second = QLineEdit(), QLineEdit()
        for field in (self.first, self.second):
            field.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(i18n.t(K.PASS_FIELD), self.first)
        form.addRow(i18n.t(K.PASS_REPEAT), self.second)
        lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def values(self) -> Tuple[str, str]:
        return self.first.text(), self.second.text()


def ask_new_passphrase(parent: Optional[QWidget]) -> Optional[Tuple[str, str]]:
    dialog = NewPassphraseDialog(parent)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.values()
    finally:
        dialog.first.clear()
        dialog.second.clear()
        dialog.deleteLater()


def ask_passphrase(parent: Optional[QWidget]) -> Optional[str]:
    text, ok = QInputDialog.getText(parent, i18n.t(K.PASS_TITLE), i18n.t(K.PASS_PROMPT),
                                    QLineEdit.EchoMode.Password)
    return text if ok else None


def prepare_vault(parent: Optional[QWidget], service: Any, state: str, say: Callable[[str], None],
                  report: Callable[[BaseException], None]) -> bool:
    """Kasayı kullanılır yapar: parola gerekiyorsa belirletir, kilitliyse açtırır. Başardıysa ``True``."""
    try:
        if state in _NEEDS_PASS:
            values = ask_new_passphrase(parent)
            if values is None:
                return False
            service.set_vault_passphrase(*values)
            say(i18n.t(K.PASS_SET))
            return True
        if state in _LOCKED:
            text = ask_passphrase(parent)
            if text is None:
                return False
            service.unlock_vault(text)
            say(i18n.t(K.PASS_UNLOCKED))
            return True
    except ValueError as exc:                                  # parolalar farklı / kısa
        say(str(exc))
    except Exception as exc:  # noqa: BLE001 — yanlış parola vb. SmartError kartla gösterilir
        report(exc)
    return False


def save_key(parent: Optional[QWidget], service: Any, provider_id: str, text: str, say: Callable[[str], None],
             report: Callable[[BaseException], None]) -> bool:
    """Anahtarı kaydeder; kasa parola istiyorsa ya da kilitliyse önce onu halleder ve bir kez yeniden dener."""
    for attempt in range(2):
        try:
            service.save_key(provider_id, text)
            say(i18n.t(K.VAULT_SAVED))
            return True
        except ValueError as exc:
            say(str(exc))
            return False
        except SmartError as err:
            if attempt == 0 and err.code in _NEEDS_PASS + _LOCKED:
                if prepare_vault(parent, service, err.code, say, report):
                    continue
                return False                                       # vazgeçildi ya da hata zaten gösterildi
            report(err)
            return False
        except Exception as exc:  # noqa: BLE001
            report(exc)
            return False
    return False


def confirm_reset(parent: Optional[QWidget]) -> bool:
    """Sıfırlamadan önce sorar; varsayılan düğme "Vazgeç"."""
    box = QMessageBox(QMessageBox.Icon.Warning, i18n.t(K.RESET_TITLE), i18n.t(K.RESET_TEXT),
                      QMessageBox.StandardButton.NoButton, parent)
    yes = box.addButton(i18n.t(K.RESET_CONFIRM), QMessageBox.ButtonRole.DestructiveRole)
    no = box.addButton(i18n.t(K.RESET_CANCEL), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(no)
    box.exec()
    clicked = box.clickedButton()
    box.deleteLater()
    return clicked is yes


def reset_vault(parent: Optional[QWidget], service: Any, say: Callable[[str], None],
                report: Callable[[BaseException], None]) -> bool:
    """Onay alınırsa kasayı sıfırlar (anahtarlar gider, eski dosya yedeklenir). Başardıysa ``True``."""
    if not confirm_reset(parent):
        return False
    try:
        service.reset_vault()
    except Exception as exc:  # noqa: BLE001 — dosya taşınamadı vb. kartla gösterilir
        report(exc)
        return False
    say(i18n.t(K.RESET_DONE))
    return True
