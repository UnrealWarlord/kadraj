"""Şifreli kasa: Fernet + keyring, parola yedeği, ad alanları, bozuk/kilitli durumlar, sızıntı yok."""
from __future__ import annotations

import base64
import json
import os
import stat
import threading

import pytest

from kadraj import paths
from kadraj.core.contracts import SmartError, VaultAdminAPI, VaultAPI
from kadraj.storage import vault as V
from kadraj.storage.vault import MemoryKeyring, Vault

SECRET = "sk-ant-SECRET123456789"
PASS = "doğru-at-pil-yakıt-42"


class FailingKeyring:
    """Keyring hizmeti yok (NoKeyringError benzeri)."""

    def get_password(self, *a):
        raise RuntimeError("no keyring")

    def set_password(self, *a):
        raise RuntimeError("no keyring")

    def delete_password(self, *a):
        raise RuntimeError("no keyring")


class AmnesiacKeyring(MemoryKeyring):
    """set_password sessizce başarılı görünür ama hiçbir şey saklamaz."""

    def set_password(self, *a):
        return None


@pytest.fixture()
def kr():
    return MemoryKeyring()


@pytest.fixture()
def vault(tmp_path, kr):
    return Vault(tmp_path / "vault.bin", keyring_backend=kr, kdf_iterations=1000)


def _codes(fn):
    with pytest.raises(SmartError) as ei:
        fn()
    return ei.value


# ---- keyring kipi -------------------------------------------------------------------------------
def test_roundtrip_masked_and_persistence(vault, kr, tmp_path):
    assert vault.status().state == "empty" and vault.providers() == [] and vault.get_secret("anthropic") is None
    vault.set_secret("anthropic", SECRET)
    assert vault.get_secret("anthropic") == SECRET and vault.has_secret("anthropic")
    assert vault.masked("anthropic") == "sk-a••••" + SECRET[-4:]
    assert vault.providers() == ["anthropic"] and vault.status().state == "ready" and vault.status().mode == "keyring"
    again = Vault(vault.path, keyring_backend=kr, kdf_iterations=1000)
    assert again.get_secret("anthropic") == SECRET
    assert again.delete_secret("anthropic") is True and again.delete_secret("anthropic") is False
    assert Vault(vault.path, keyring_backend=kr).providers() == []


def test_file_never_contains_plaintext_and_is_private(vault):
    vault.set_secret("anthropic", SECRET)
    raw = vault.path.read_bytes()
    for needle in (SECRET.encode(), base64.b64encode(SECRET.encode()), b"SECRET123456789", b"anthropic"):
        assert needle not in raw
    header = json.loads(raw)
    assert header["format"] == "kadraj-vault" and header["version"] == 1 and header["mode"] == "keyring"
    assert header["key_id"] and "kdf" not in header and header["token"].startswith("gAAAA")   # Fernet belirteci
    if os.name == "posix":
        assert stat.S_IMODE(vault.path.stat().st_mode) == 0o600


def test_no_temp_files_left_behind(vault):
    for i in range(3):
        vault.set_secret(f"p{i}", SECRET + str(i))
    assert [p.name for p in vault.path.parent.iterdir()] == ["vault.bin"]


def test_key_lives_in_keyring_not_in_file(vault, kr):
    vault.set_secret("a", SECRET)
    key_id = json.loads(vault.path.read_text())["key_id"]
    key = kr.get_password(V.KEYRING_SERVICE, f"vault-key-{key_id}")
    assert key and key not in vault.path.read_text()


def test_missing_keyring_key_is_a_smart_error_and_does_not_overwrite(vault, kr, tmp_path):
    vault.set_secret("a", SECRET)
    before = vault.path.read_bytes()
    kr._d.clear()                                           # anahtar zinciri sıfırlandı
    v2 = Vault(vault.path, keyring_backend=kr)
    st = v2.status()
    assert st.state == "error" and st.error.code == "vault.key_missing" and st.error.actions[0].payload["topic"]
    assert v2.has_secret("a") is False and v2.masked("a") == "" and v2.providers() == []     # okumalar atmaz
    assert _codes(lambda: v2.get_secret("a")).code == "vault.key_missing"
    assert _codes(lambda: v2.set_secret("b", "x" * 10)).code == "vault.key_missing"
    assert vault.path.read_bytes() == before                # veri kaybı yok


def test_tampered_token_is_rejected(vault, kr):
    vault.set_secret("a", SECRET)
    hdr = json.loads(vault.path.read_text())
    tok = hdr["token"]
    hdr["token"] = tok[:-6] + ("AAAAAA" if tok[-6:] != "AAAAAA" else "BBBBBB")
    vault.path.write_text(json.dumps(hdr))
    v2 = Vault(vault.path, keyring_backend=kr)
    assert v2.status().state == "error"
    assert _codes(lambda: v2.get_secret("a")).code in ("vault.key_missing", "vault.corrupt")


@pytest.mark.parametrize("content", ["", "{not json", "[]", '{"format": "other"}',
                                     '{"format":"kadraj-vault","mode":"keyring"}',
                                     '{"format":"kadraj-vault","mode":"x","token":"y"}'])
def test_corrupt_file_is_error_then_reset_backs_up(tmp_path, kr, content):
    p = tmp_path / "vault.bin"
    p.write_text(content)
    v = Vault(p, keyring_backend=kr)
    assert v.status().state == "error" and v.status().error.code == "vault.corrupt"
    assert _codes(lambda: v.set_secret("a", SECRET)).code == "vault.corrupt"
    assert p.read_text() == content                         # üzerine yazılmadı
    v.reset()
    assert (tmp_path / "vault.bin.bad").read_text() == content and not p.exists()
    v.set_secret("a", SECRET)                               # sıfırlamadan sonra yeni kasa kurulur
    assert v.get_secret("a") == SECRET


def test_reset_deletes_keyring_entry(vault, kr):
    vault.set_secret("a", SECRET)
    key_id = json.loads(vault.path.read_text())["key_id"]
    vault.reset()
    assert kr.get_password(V.KEYRING_SERVICE, f"vault-key-{key_id}") is None
    assert vault.status().state == "empty"


# ---- parola kipi (keyring yok/başarısız) ------------------------------------------------------------------
@pytest.mark.parametrize("backend", [FailingKeyring(), AmnesiacKeyring()])
def test_without_keyring_first_write_requires_passphrase(tmp_path, backend):
    v = Vault(tmp_path / "vault.bin", keyring_backend=backend, kdf_iterations=1000)
    err = _codes(lambda: v.set_secret("a", SECRET))
    assert err.code == "vault.needs_passphrase" and not (tmp_path / "vault.bin").exists()


def test_passphrase_mode_lifecycle(tmp_path):
    kr = FailingKeyring()
    v = Vault(tmp_path / "vault.bin", keyring_backend=kr, kdf_iterations=1000)
    v.initialize(PASS)
    assert v.status().mode == "passphrase" and v.status().state == "ready"
    v.set_secret("anthropic", SECRET)
    hdr = json.loads(v.path.read_text())
    assert hdr["mode"] == "passphrase" and hdr["kdf"]["name"] == "pbkdf2-sha256" and hdr["kdf"]["iterations"] == 1000
    assert len(base64.b64decode(hdr["kdf"]["salt"])) == 16 and SECRET.encode() not in v.path.read_bytes()

    again = Vault(v.path, keyring_backend=kr, kdf_iterations=1000)          # yeni oturum
    assert again.needs_unlock() and again.status().state == "locked"
    assert again.has_secret("anthropic") is False and again.masked("anthropic") == ""
    assert _codes(lambda: again.get_secret("anthropic")).code == "vault.locked"
    assert _codes(lambda: again.set_secret("x", SECRET)).code == "vault.locked"
    assert _codes(lambda: again.unlock("yanlış-parola-1")).code == "vault.wrong_passphrase"
    assert again.needs_unlock()                                                # başarısız deneme kilidi açmadı
    again.unlock(PASS)
    assert again.get_secret("anthropic") == SECRET and not again.needs_unlock()
    again.lock()
    assert again.needs_unlock()


def test_passphrase_rules_and_initialize_guard(tmp_path):
    v = Vault(tmp_path / "vault.bin", keyring_backend=FailingKeyring(), kdf_iterations=1000)
    assert _codes(lambda: v.initialize("kısa")).code == "vault.weak_passphrase"
    assert not v.path.exists()
    v.initialize(PASS)
    assert _codes(lambda: v.initialize(PASS)).code == "vault.exists"
    assert _codes(lambda: Vault(tmp_path / "none.bin", keyring_backend=FailingKeyring()).unlock(PASS)).code == "vault.empty"


def test_change_passphrase(tmp_path):
    kr = FailingKeyring()
    v = Vault(tmp_path / "vault.bin", keyring_backend=kr, kdf_iterations=1000)
    v.initialize(PASS)
    v.set_secret("a", SECRET)
    assert _codes(lambda: v.change_passphrase("yanlış-eski-1", "yeni-parola-77")).code == "vault.wrong_passphrase"
    assert v.get_secret("a") == SECRET                                         # durum bozulmadı
    assert _codes(lambda: v.change_passphrase(PASS, "kısa")).code == "vault.weak_passphrase"
    v.change_passphrase(PASS, "yeni-parola-77")
    fresh = Vault(v.path, keyring_backend=kr, kdf_iterations=1000)
    assert _codes(lambda: fresh.unlock(PASS)).code == "vault.wrong_passphrase"
    fresh.unlock("yeni-parola-77")
    assert fresh.get_secret("a") == SECRET


def test_change_passphrase_on_keyring_vault_is_rejected(vault):
    vault.set_secret("a", SECRET)
    assert _codes(lambda: vault.change_passphrase("x" * 9, "y" * 9)).code == "vault.keyring_mode"
    vault.unlock("ignored")                                                    # keyring kipinde zararsız
    assert vault.get_secret("a") == SECRET


def test_keyring_unavailable_later_for_keyring_vault(vault):
    vault.set_secret("a", SECRET)
    v2 = Vault(vault.path, keyring_backend=FailingKeyring())
    assert v2.status().error.code == "vault.key_missing"


# ---- ad alanları (profiller) -----------------------------------------------------------------------------------
def test_namespaces_isolate_profiles(vault):
    vault.set_secret("anthropic", SECRET)
    vault.use_namespace("is-profili")
    assert vault.has_secret("anthropic") is False and vault.providers() == []
    vault.set_secret("anthropic", "sk-ant-OTHER-0000-9999")
    vault.use_namespace("default")
    assert vault.get_secret("anthropic") == SECRET
    assert vault.namespaces() == ["default", "is-profili"]
    view = vault.view("is-profili")
    assert view.get_secret("anthropic") == "sk-ant-OTHER-0000-9999" and view.masked("anthropic").endswith("9999")
    view.set_secret("pexels", "pexels-key-123456")
    assert vault.has_secret("pexels") is False and view.providers() == ["anthropic", "pexels"]
    assert vault.delete_namespace("is-profili") is True and vault.delete_namespace("is-profili") is False
    assert vault.namespaces() == ["default"] and view.providers() == []


@pytest.mark.parametrize("bad", ["", "a b", "../x", "x" * 65, "ş", "a/b", None, 5])
def test_invalid_names_rejected(vault, bad):
    assert _codes(lambda: vault.use_namespace(bad)).code == "vault.invalid_name"
    assert _codes(lambda: vault.set_secret(bad, "x" * 10)).code == "vault.invalid_name"


@pytest.mark.parametrize("bad", ["", "   ", "\n", "a\x00b", "x" * 9000, None, 123])
def test_invalid_secrets_rejected(vault, bad):
    assert _codes(lambda: vault.set_secret("a", bad)).code == "vault.invalid_secret"
    assert not vault.path.exists()


def test_secret_is_stripped(vault):
    vault.set_secret("a", f"  {SECRET}\n")
    assert vault.get_secret("a") == SECRET


# ---- sızıntı / API uyumu -----------------------------------------------------------------------------------------
def test_repr_and_errors_never_leak_the_secret(vault, kr):
    vault.set_secret("a", SECRET)
    assert SECRET not in repr(vault) and SECRET not in repr(vault.view("default"))
    kr._d.clear()
    v2 = Vault(vault.path, keyring_backend=kr)
    err = _codes(lambda: v2.get_secret("a"))
    for text in (err.title, err.summary, err.cause, err.technical, " ".join(err.steps), repr(err)):
        assert SECRET not in text


def test_protocol_conformance(vault):
    assert isinstance(vault, VaultAPI) and isinstance(vault, VaultAdminAPI)
    assert isinstance(vault.view("default"), VaultAPI) and vault.is_placeholder is False
    assert vault.is_available() is True


def test_missing_cryptography_is_a_smart_error(vault, monkeypatch):
    monkeypatch.setattr(Vault, "_crypto", staticmethod(lambda: (_ for _ in ()).throw(
        V._error("backend_missing", "ImportError"))))
    assert _codes(lambda: vault.set_secret("a", SECRET)).code == "vault.backend_missing"


def test_write_failure_keeps_previous_data(vault, monkeypatch):
    vault.set_secret("a", SECRET)
    real = os.replace

    def boom(src, dst):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "replace", boom)
    err = _codes(lambda: vault.set_secret("b", "x" * 12))
    assert err.code == "disk.full"
    monkeypatch.setattr(os, "replace", real)
    assert Vault(vault.path, keyring_backend=vault._kr_backend).get_secret("a") == SECRET


def test_concurrent_writers_do_not_lose_updates(vault, kr):
    vault.set_secret("seed", "seed-value-123")
    errors = []

    def work(i):
        try:
            vault.set_secret(f"p{i}", f"secret-value-{i:04d}")
        except Exception as exc:
            errors.append(exc)
    ts = [threading.Thread(target=work, args=(i,)) for i in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    reloaded = Vault(vault.path, keyring_backend=kr)
    assert sorted(reloaded.providers()) == sorted(["seed"] + [f"p{i}" for i in range(10)])


def test_default_path_follows_kadraj_home(tmp_path, monkeypatch, kr):
    monkeypatch.setenv("KADRAJ_HOME", str(tmp_path / "evim"))
    v = Vault(keyring_backend=kr)
    assert v.path == paths.vault_file() == tmp_path / "evim" / "vault.bin"
    v.set_secret("a", SECRET)
    assert (tmp_path / "evim" / "vault.bin").is_file()


def test_real_keyring_is_never_touched_by_the_suite():
    from kadraj.storage import vault as vmod
    assert isinstance(vmod._default_keyring, MemoryKeyring)
