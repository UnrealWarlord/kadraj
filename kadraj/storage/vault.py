"""Şifreli API kasası — ``~/.kadraj/vault.bin`` (Fernet; anahtar OS keyring'de ya da parolayla türetilir).

Şifreleme ``cryptography.fernet`` ile; paket yoksa (Android APK'sı) aynı biçimi üreten ``fernet_lite`` ile (M19).
Parola türetme standart kütüphanenin ``hashlib.pbkdf2_hmac``'ı (``cryptography``'nin PBKDF2HMAC'ıyla aynı çıktı).
Android'de keyring yoktur: kasa parola kipindedir (M4) ve her oturumda ``unlock(parola)`` gerekir.

Güvenlik modeli
---------------
* Dosya: JSON başlığı (``format``, ``version``, ``mode``, ``key_id``, ``kdf``) + ``token`` (Fernet ile şifreli
  ``{"v":1,"namespaces":{ad:{sağlayıcı:anahtar}}}``). **Düz anahtar asla diske yazılmaz**, dosya izni ``0600``.
* ``mode="keyring"``: rastgele 32 baytlık Fernet anahtarı OS keyring'de (``kadraj`` / ``vault-key-<key_id>``)
  saklanır; kasa kullanıcı girişiyle birlikte kendiliğinden açılır.
* ``mode="passphrase"``: keyring yoksa/başarısızsa anahtar **parolayla** türetilir (PBKDF2-HMAC-SHA256, 600 000
  yineleme, 16 bayt tuz); her oturumda ``unlock(parola)`` gerekir. Yanlış parola ``SmartError``.
* Kasa açılamazsa (kilitli, anahtar kayıp, dosya bozuk) **sessizce yeni kasa kurulmaz** — veri kaybını önlemek
  için ``SmartError`` atılır; kurtarılamazsa ``reset()`` eski dosyayı ``.bad`` olarak yedekler.
* Arayüze yalnızca ``masked()`` verilir; düz anahtar yalnızca ``get_secret`` ile, sağlayıcı çağrısı sırasında alınır.
  Anahtarlar ``repr``'e, log'a ve hata ayrıntılarına girmez.
* Profil ad alanı: ``use_namespace`` / ``view``; her profil kendi anahtar kümesine sahiptir.

``VaultAPI`` (contracts §4) + ``VaultAdminAPI`` (contracts §9) uygulanır. Okuma yöntemleri (``has_secret``,
``masked``, ``providers``) kilitli/bozuk kasada istisna atmaz; yazma ve ``get_secret`` atar.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import paths
from ..core.contracts import (Severity, SmartAction, SmartError, Topics, VaultStatus)
from ..core.i18n import t
from ..core.stubs import mask_secret

FORMAT = "kadraj-vault"
VERSION = 1
KEYRING_SERVICE = "kadraj"
DEFAULT_ITERATIONS = 600_000
DEFAULT_NAMESPACE = "default"
MAX_SECRET_LEN = 8192
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


# ---------------------------------------------------------------------------
# Keyring soyutlaması (testler/selftest sahte arka uç verir; gerçek keyring'e dokunulmaz)
# ---------------------------------------------------------------------------
class MemoryKeyring:
    """Bellek-içi keyring (``get_password/set_password/delete_password``). Test ve ``--selftest`` içindir."""

    def __init__(self) -> None:
        self._d: Dict[tuple, str] = {}
        self._lock = threading.Lock()

    def get_password(self, service: str, username: str) -> Optional[str]:
        with self._lock:
            return self._d.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        with self._lock:
            self._d[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        with self._lock:
            self._d.pop((service, username), None)


_default_keyring: Any = None


def set_default_keyring(backend: Any) -> None:
    """Süreç genelinde varsayılan keyring arka ucunu değiştirir (``None`` → gerçek ``keyring`` modülü)."""
    global _default_keyring
    _default_keyring = backend


def _resolve_keyring(explicit: Any) -> Any:
    if explicit is not None:
        return explicit
    if _default_keyring is not None:
        return _default_keyring
    try:
        import keyring  # type: ignore
        return keyring
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Hata üretici
# ---------------------------------------------------------------------------
def _error(code: str, technical: str = "", **fmt: Any) -> SmartError:
    key = f"vault.{code}"
    actions = ()
    if code in ("key_missing", "corrupt"):
        actions = (SmartAction("reset", t("vault.action.reset"), "custom", {"topic": Topics.VAULT_RESET}),)
    return SmartError(f"vault.{code}", t(f"{key}.title"), t(f"{key}.summary", **fmt),
                      cause=t(f"{key}.cause"), steps=(t(f"{key}.step1"),), actions=actions,
                      severity=Severity.ERROR, technical=technical, source="vault")


def _check_name(name: str, what: str = "name") -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise _error("invalid_name", what=what)
    return name


def _check_secret(value: str) -> str:
    if not isinstance(value, str):
        raise _error("invalid_secret")
    value = value.strip()
    if not value or len(value) > MAX_SECRET_LEN or any(ord(c) < 32 for c in value):
        raise _error("invalid_secret")
    return value


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


class Vault:
    """Şifreli kasa. İş parçacığı güvenlidir; ağır işler (KDF) yalnızca ``initialize``/``unlock``'ta çalışır."""

    is_placeholder = False

    def __init__(self, path: Optional[Path] = None, *, keyring_backend: Any = None,
                 kdf_iterations: int = DEFAULT_ITERATIONS) -> None:
        self._explicit_path = Path(path) if path else None
        self._kr_backend = keyring_backend
        self._iterations = int(kdf_iterations)
        self._lock = threading.RLock()
        self._header: Optional[Dict[str, Any]] = None
        self._data: Optional[Dict[str, Any]] = None
        self._key: Optional[bytes] = None
        self.namespace = DEFAULT_NAMESPACE

    def __repr__(self) -> str:                      # anahtar/veri içermez
        return f"<Vault state={self.status().state} namespace={self.namespace!r}>"

    # -- dosya ------------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._explicit_path or paths.vault_file()

    def _read_header(self) -> Optional[Dict[str, Any]]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as exc:
            raise _error("corrupt", f"{type(exc).__name__}") from exc
        try:
            hdr = json.loads(raw)
        except ValueError as exc:
            raise _error("corrupt", "geçersiz JSON") from exc
        if not isinstance(hdr, dict) or hdr.get("format") != FORMAT or not isinstance(hdr.get("token"), str) \
                or hdr.get("mode") not in ("keyring", "passphrase"):
            raise _error("corrupt", "başlık tanınmadı")
        if isinstance(hdr.get("version"), int) and hdr["version"] > VERSION:
            raise _error("corrupt", f"daha yeni sürüm: {hdr['version']}")
        return hdr

    def _write(self, header: Dict[str, Any]) -> None:
        tmp = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, tmp = tempfile.mkstemp(prefix=".vault-", suffix=".tmp", dir=str(self.path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(header, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            tmp = None
        except OSError as exc:
            from ..core.errors import translate
            raise translate(exc, {"source": "vault", "path": str(self.path)}) from exc
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # -- şifreleme ---------------------------------------------------------
    @staticmethod
    def _crypto():
        """(Fernet, InvalidToken): ``cryptography`` varsa onunki, yoksa ``fernet_lite`` (aynı belirteç biçimi).
        İkisi de yüklenemezse ``SmartError`` (uygulama düşmez)."""
        try:
            from cryptography.fernet import Fernet, InvalidToken
            return Fernet, InvalidToken
        except Exception:
            pass
        try:
            from .fernet_lite import Fernet, InvalidToken
        except Exception as exc:
            raise _error("backend_missing", f"{type(exc).__name__}") from exc
        return Fernet, InvalidToken

    @classmethod
    def _fernet(cls, key: bytes):
        return cls._crypto()[0](key)

    def _derive(self, passphrase: str, salt: bytes, iterations: int) -> bytes:
        """PBKDF2-HMAC-SHA256 → 32 bayt Fernet anahtarı (url-safe base64)."""
        raw = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32)
        return base64.urlsafe_b64encode(raw)

    def _decrypt(self, header: Dict[str, Any], key: bytes, wrong: str) -> Dict[str, Any]:
        _fernet_cls, InvalidToken = self._crypto()
        try:
            plain = _fernet_cls(key).decrypt(header["token"].encode("ascii"))
            data = json.loads(plain.decode("utf-8"))
        except InvalidToken as exc:
            raise _error(wrong, "InvalidToken") from exc
        except (ValueError, UnicodeError) as exc:
            raise _error("corrupt", f"{type(exc).__name__}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("namespaces"), dict):
            raise _error("corrupt", "şema")
        return data

    def _keyring_name(self, key_id: str) -> str:
        return f"vault-key-{key_id}"

    # -- açma --------------------------------------------------------------
    def _open(self) -> Dict[str, Any]:
        """Çözülmüş veriyi döner (gerekirse yükler). Açılamazsa ``SmartError``."""
        with self._lock:
            if self._data is not None:
                return self._data
            header = self._read_header()
            if header is None:
                self._header = None
                self._data = {"v": 1, "namespaces": {}}
                return self._data
            self._header = header
            if header["mode"] == "keyring":
                kr = _resolve_keyring(self._kr_backend)
                key_text = None
                if kr is not None:
                    try:
                        key_text = kr.get_password(KEYRING_SERVICE, self._keyring_name(header.get("key_id", "")))
                    except Exception:
                        key_text = None
                if not key_text:
                    raise _error("key_missing")
                self._key = key_text.encode("ascii")
                self._data = self._decrypt(header, self._key, "key_missing")
                return self._data
            if self._key is None:
                raise _error("locked")
            self._data = self._decrypt(header, self._key, "wrong_passphrase")
            return self._data

    def _persist(self) -> None:
        """Mevcut ``_data``'yı şifreleyip yazar (anahtar ``_key``'de olmalı)."""
        assert self._header is not None and self._key is not None and self._data is not None
        token = self._fernet(self._key).encrypt(json.dumps(self._data).encode("utf-8")).decode("ascii")
        self._header = {**self._header, "token": token}
        self._write(self._header)

    def _create(self, passphrase: Optional[str]) -> None:
        """Yeni kasa kurar. ``passphrase`` verilmişse parola kipi; yoksa keyring denenir, olmazsa ``needs_passphrase``."""
        Fernet = self._crypto()[0]
        data = {"v": 1, "namespaces": {}}
        if passphrase is None:
            kr = _resolve_keyring(self._kr_backend)
            key_id = uuid.uuid4().hex
            key = Fernet.generate_key()
            ok = False
            if kr is not None:
                try:
                    kr.set_password(KEYRING_SERVICE, self._keyring_name(key_id), key.decode("ascii"))
                    ok = kr.get_password(KEYRING_SERVICE, self._keyring_name(key_id)) == key.decode("ascii")
                except Exception:
                    ok = False
            if not ok:
                raise _error("needs_passphrase")
            header: Dict[str, Any] = {"format": FORMAT, "version": VERSION, "mode": "keyring", "key_id": key_id,
                                      "token": ""}
            self._header, self._key, self._data = header, key, data
        else:
            if len(passphrase) < 8:
                raise _error("weak_passphrase")
            salt = secrets.token_bytes(16)
            key = self._derive(passphrase, salt, self._iterations)
            header = {"format": FORMAT, "version": VERSION, "mode": "passphrase",
                      "kdf": {"name": "pbkdf2-sha256", "iterations": self._iterations, "salt": _b64(salt)},
                      "token": ""}
            self._header, self._key, self._data = header, key, data
        self._persist()

    # -- VaultAdminAPI -------------------------------------------------------
    def is_available(self) -> bool:
        try:
            self._crypto()
            return True
        except Exception:
            return False

    def keyring_available(self) -> bool:
        """Sistem anahtar zinciri modülü var mı? Yoksa (Android) kasa ilk anahtardan önce parolayla kurulur."""
        return _resolve_keyring(self._kr_backend) is not None

    def status(self) -> VaultStatus:
        with self._lock:
            try:
                self._open()
            except SmartError as err:
                mode = (self._header or {}).get("mode", "none")
                if err.code == "vault.locked":
                    return VaultStatus("locked", mode, self.namespace)
                return VaultStatus("error", mode, self.namespace, err)
            if self._header is None:
                return VaultStatus("empty", "none", self.namespace)
            return VaultStatus("ready", self._header["mode"], self.namespace)

    def needs_unlock(self) -> bool:
        return self.status().state == "locked"

    def initialize(self, passphrase: Optional[str] = None) -> None:
        with self._lock:
            if self._read_header() is not None:
                raise _error("exists")
            self._data = None
            self._create(passphrase)

    def unlock(self, passphrase: str) -> None:
        with self._lock:
            header = self._read_header()
            if header is None:
                raise _error("empty")
            if header["mode"] != "passphrase":
                return                                             # keyring kipi zaten kendiliğinden açılır
            kdf = header.get("kdf") or {}
            try:
                salt, iterations = _unb64(kdf["salt"]), int(kdf["iterations"])
            except (KeyError, ValueError, TypeError) as exc:
                raise _error("corrupt", "kdf") from exc
            key = self._derive(passphrase, salt, iterations)
            self._header = header
            data = self._decrypt(header, key, "wrong_passphrase")
            self._key, self._data = key, data

    def lock(self) -> None:
        with self._lock:
            self._data = None
            self._key = None

    def change_passphrase(self, old: str, new: str) -> None:
        with self._lock:
            header = self._read_header()
            if header is None:
                raise _error("empty")
            if header["mode"] != "passphrase":
                raise _error("keyring_mode")
            if len(new) < 8:
                raise _error("weak_passphrase")
            saved = (self._header, self._key, self._data)
            try:
                self._data = None
                self._key = None
                self.unlock(old)                               # yanlış parola → SmartError, durum geri yüklenir
                salt = secrets.token_bytes(16)
                self._key = self._derive(new, salt, self._iterations)
                self._header = {**self._header, "kdf": {"name": "pbkdf2-sha256", "iterations": self._iterations,
                                                        "salt": _b64(salt)}}
                self._persist()
            except BaseException:
                self._header, self._key, self._data = saved
                raise

    def reset(self) -> None:
        with self._lock:
            header = None
            try:
                header = self._read_header()
            except SmartError:
                pass
            if self.path.exists():
                bad = self.path.with_name(f"{self.path.name}.bad")
                if bad.exists():
                    bad = self.path.with_name(f"{self.path.name}.{int(time.time())}.bad")
                try:
                    os.replace(self.path, bad)
                except OSError as exc:
                    from ..core.errors import translate
                    raise translate(exc, {"source": "vault", "path": str(self.path)}) from exc
            if header and header.get("mode") == "keyring":
                kr = _resolve_keyring(self._kr_backend)
                try:
                    if kr is not None:
                        kr.delete_password(KEYRING_SERVICE, self._keyring_name(header.get("key_id", "")))
                except Exception:
                    pass
            self._header = self._data = self._key = None

    # -- ad alanları ---------------------------------------------------------
    def use_namespace(self, namespace: str) -> None:
        self.namespace = _check_name(namespace, "namespace")

    def namespaces(self) -> List[str]:
        try:
            return sorted(self._open()["namespaces"])
        except SmartError:
            return []

    def delete_namespace(self, namespace: str) -> bool:
        _check_name(namespace, "namespace")
        with self._lock:
            data = self._open()
            if namespace not in data["namespaces"]:
                return False
            if self._header is None:
                return False
            del data["namespaces"][namespace]
            self._persist()
            return True

    def view(self, namespace: str) -> "VaultView":
        return VaultView(self, _check_name(namespace, "namespace"))

    # -- VaultAPI (etkin ad alanı) -------------------------------------------------
    def providers(self) -> List[str]:
        return self._providers(self.namespace)

    def has_secret(self, provider_id: str) -> bool:
        return self._has(self.namespace, provider_id)

    def masked(self, provider_id: str) -> str:
        return self._masked(self.namespace, provider_id)

    def set_secret(self, provider_id: str, value: str) -> None:
        self._set(self.namespace, provider_id, value)

    def get_secret(self, provider_id: str) -> Optional[str]:
        return self._get(self.namespace, provider_id)

    def delete_secret(self, provider_id: str) -> bool:
        return self._delete(self.namespace, provider_id)

    # -- ad alanı bazlı iç işlemler ------------------------------------------------------
    def _providers(self, ns: str) -> List[str]:
        try:
            return sorted(self._open()["namespaces"].get(ns, {}))
        except SmartError:
            return []

    def _has(self, ns: str, pid: str) -> bool:
        try:
            return pid in self._open()["namespaces"].get(ns, {})
        except SmartError:
            return False

    def _masked(self, ns: str, pid: str) -> str:
        try:
            return mask_secret(self._open()["namespaces"].get(ns, {}).get(pid, ""))
        except SmartError:
            return ""

    def _get(self, ns: str, pid: str) -> Optional[str]:
        _check_name(pid, "provider")
        return self._open()["namespaces"].get(ns, {}).get(pid)

    def _set(self, ns: str, pid: str, value: str) -> None:
        _check_name(pid, "provider")
        value = _check_secret(value)
        with self._lock:
            data = self._open()
            if self._header is None:                       # ilk yazma: kasayı kur
                self._data = None
                self._create(None)
                data = self._open()
            data["namespaces"].setdefault(ns, {})[pid] = value
            self._persist()

    def _delete(self, ns: str, pid: str) -> bool:
        _check_name(pid, "provider")
        with self._lock:
            data = self._open()
            bucket = data["namespaces"].get(ns, {})
            if pid not in bucket or self._header is None:
                return False
            del bucket[pid]
            if not bucket:
                data["namespaces"].pop(ns, None)
            self._persist()
            return True


class VaultView:
    """Tek bir ad alanına sabitlenmiş ``VaultAPI`` görünümü (profil başına kasa)."""

    is_placeholder = False

    def __init__(self, vault: Vault, namespace: str) -> None:
        self._v, self.namespace = vault, namespace

    def __repr__(self) -> str:
        return f"<VaultView namespace={self.namespace!r}>"

    def is_available(self) -> bool:
        return self._v.is_available()

    def providers(self) -> List[str]:
        return self._v._providers(self.namespace)

    def has_secret(self, provider_id: str) -> bool:
        return self._v._has(self.namespace, provider_id)

    def masked(self, provider_id: str) -> str:
        return self._v._masked(self.namespace, provider_id)

    def set_secret(self, provider_id: str, value: str) -> None:
        self._v._set(self.namespace, provider_id, value)

    def get_secret(self, provider_id: str) -> Optional[str]:
        return self._v._get(self.namespace, provider_id)

    def delete_secret(self, provider_id: str) -> bool:
        return self._v._delete(self.namespace, provider_id)
