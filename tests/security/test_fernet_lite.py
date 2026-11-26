"""Saf Python Fernet (``kadraj/storage/fernet_lite.py``, M19): resmi test vektörleri ve ``cryptography`` ile uyum.

Android APK'sında ``cryptography`` yok; kasa bu modülle aynı belirteçleri üretmeli ki dosya telefon ile bilgisayar
arasında taşınabilsin.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import sys

import pytest
from cryptography.fernet import Fernet as RefFernet
from cryptography.fernet import InvalidToken as RefInvalid
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from kadraj.storage import fernet_lite as F
from kadraj.storage import vault as V
from kadraj.storage.vault import MemoryKeyring, Vault

H = bytes.fromhex


# ---- AES-128 ------------------------------------------------------------------------------------------------
def test_sbox_spot_values():
    assert (F._SBOX[0x00], F._SBOX[0x01], F._SBOX[0x53], F._SBOX[0xFF]) == (0x63, 0x7C, 0xED, 0x16)
    assert sorted(F._SBOX) == list(range(256)) and all(F._INV_SBOX[F._SBOX[i]] == i for i in range(256))


def test_fips197_appendix_c1():
    key, pt = H("000102030405060708090a0b0c0d0e0f"), H("00112233445566778899aabbccddeeff")
    ct = F.aes128_encrypt_block(key, pt)
    assert ct == H("69c4e0d86a7b0430d8cdb78070b4c55a")
    assert F.aes128_decrypt_block(key, ct) == pt


def test_fips197_appendix_b():
    key, pt = H("2b7e151628aed2a6abf7158809cf4f3c"), H("3243f6a8885a308d313198a2e0370734")
    assert F.aes128_encrypt_block(key, pt) == H("3925841d02dc09fbdc118597196a0b32")


def test_nist_sp800_38a_cbc_aes128():
    key, iv = H("2b7e151628aed2a6abf7158809cf4f3c"), H("000102030405060708090a0b0c0d0e0f")
    pt = H("6bc1bee22e409f96e93d7e117393172a" "ae2d8a571e03ac9c9eb76fac45af8e51"
           "30c81c46a35ce411e5fbc1191a0a52ef" "f69f2445df4f9b17ad2b417be66c3710")
    ct = H("7649abac8119b246cee98e9b12e9197d" "5086cb9b507219ee95db113a917678b2"
           "73bed6b8e3c1743b7116e69e22229516" "3ff1caa1681fac09120eca307586e1a7")
    assert F.cbc_encrypt(key, iv, pt) == ct and F.cbc_decrypt(key, iv, ct) == pt


def test_bad_sizes_are_rejected():
    with pytest.raises(ValueError):
        F.aes128_encrypt_block(b"k" * 15, b"x" * 16)
    with pytest.raises(ValueError):
        F.cbc_encrypt(b"k" * 16, b"i" * 16, b"x" * 17)


# ---- Fernet belirtimi -----------------------------------------------------------------------------------------
SPEC_SECRET = "cw_0x689RpI-jtRR7oE8h_eQsKImvJapLeSbXpwF4e4="
SPEC_TOKEN = ("gAAAAAAdwJ6wAAECAwQFBgcICQoLDA0ODy021cpGVWKZ_eEwCGM4BLLF_5CV9dOPmrhuVUPgJobwOz7JcbmrR64j"
              "VmpU4IwqDA==")
SPEC_TIME = 499162800                                                  # 1985-10-26T01:20:00-07:00


def test_fernet_spec_generate_and_verify_vector():
    f = F.Fernet(SPEC_SECRET)
    assert f._encrypt_from_parts(b"hello", SPEC_TIME, bytes(range(16))).decode() == SPEC_TOKEN
    assert f.decrypt(SPEC_TOKEN) == b"hello" and f.decrypt(SPEC_TOKEN.encode()) == b"hello"


def _forge(key: str, version: int, body: bytes, iv: bytes = bytes(16)) -> bytes:
    """İmzası doğru ama içi bozuk belirteç (dolgu/boyut hataları HMAC'tan sonra da yakalanmalı)."""
    raw = base64.urlsafe_b64decode(key)
    basic = bytes([version]) + struct.pack(">Q", SPEC_TIME) + iv + body
    return base64.urlsafe_b64encode(basic + hmac.new(raw[:16], basic, hashlib.sha256).digest())


@pytest.mark.parametrize("make", [
    lambda: SPEC_TOKEN[:-8] + "AAAAAAA=",                                                  # MAC değişti
    lambda: SPEC_TOKEN[:40],                                                                # kısa
    lambda: "%%%%" + SPEC_TOKEN[4:],                                                        # base64 değil
    lambda: _forge(SPEC_SECRET, 0x81, F.cbc_encrypt(base64.urlsafe_b64decode(SPEC_SECRET)[16:], bytes(16),
                                                     F._pad(b"x"))),                       # sürüm baytı
    lambda: _forge(SPEC_SECRET, 0x80, b"\x00" * 15),                                        # 16'nın katı değil
    lambda: _forge(SPEC_SECRET, 0x80, b""),                                                 # şifreli metin yok
    lambda: _forge(SPEC_SECRET, 0x80, F.cbc_encrypt(base64.urlsafe_b64decode(SPEC_SECRET)[16:], bytes(16),
                                                     b"A" * 16)),                          # dolgu bozuk
])
def test_invalid_tokens_are_rejected_like_cryptography(make):
    token = make()
    with pytest.raises(F.InvalidToken):
        F.Fernet(SPEC_SECRET).decrypt(token)
    with pytest.raises(RefInvalid):
        RefFernet(SPEC_SECRET).decrypt(token)


def test_wrong_key_and_ttl():
    f, other = F.Fernet(F.Fernet.generate_key()), F.Fernet(F.Fernet.generate_key())
    token = f.encrypt(b"gizli")
    with pytest.raises(F.InvalidToken):
        other.decrypt(token)
    assert f.decrypt(token, ttl=60) == b"gizli"
    with pytest.raises(F.InvalidToken):
        f.decrypt(f.encrypt_at_time(b"eski", 1_000_000), ttl=60)


def test_bad_keys_and_types():
    for key in ("kısa", base64.urlsafe_b64encode(b"x" * 31), "!!!!"):
        with pytest.raises(ValueError):
            F.Fernet(key)
    with pytest.raises(TypeError):
        F.Fernet(F.Fernet.generate_key()).encrypt("metin")                                  # bayt olmalı
    assert "Fernet" in repr(F.Fernet(SPEC_SECRET)) and SPEC_SECRET not in repr(F.Fernet(SPEC_SECRET))


@pytest.mark.parametrize("size", [0, 1, 15, 16, 17, 31, 32, 100, 1000])
def test_tokens_interoperate_with_cryptography_both_ways(size):
    data = bytes((i * 37 + size) & 0xFF for i in range(size))
    key = RefFernet.generate_key()
    assert RefFernet(key).decrypt(F.Fernet(key).encrypt(data)) == data
    assert F.Fernet(key).decrypt(RefFernet(key).encrypt(data)) == data
    lite_key = F.Fernet.generate_key()
    assert len(base64.urlsafe_b64decode(lite_key)) == 32
    assert RefFernet(lite_key).decrypt(F.Fernet(lite_key).encrypt(data)) == data


def test_pbkdf2_matches_cryptography():
    salt = bytes(range(16))
    ref = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=1000).derive("şifre-42".encode())
    vault = Vault(kdf_iterations=1000)
    assert vault._derive("şifre-42", salt, 1000) == base64.urlsafe_b64encode(ref)


# ---- kasa, cryptography olmadan (Android) -------------------------------------------------------------------------
@pytest.fixture()
def no_cryptography(monkeypatch):
    monkeypatch.setitem(sys.modules, "cryptography", None)
    monkeypatch.setitem(sys.modules, "cryptography.fernet", None)
    assert Vault._crypto()[0] is F.Fernet


def test_vault_without_cryptography_uses_the_lite_fernet(tmp_path, no_cryptography):
    v = Vault(tmp_path / "vault.bin", keyring_backend=MemoryKeyring(), kdf_iterations=1000)
    assert v.is_available()
    v.initialize("telefon-parolası")
    v.set_secret("anthropic", "sk-ant-ÖRNEK123456")
    v2 = Vault(v.path, kdf_iterations=1000)
    assert v2.status().state == "locked"
    with pytest.raises(Exception) as ei:
        v2.unlock("yanlış-parola-123")
    assert getattr(ei.value, "code", "") == "vault.wrong_passphrase"
    v2.unlock("telefon-parolası")
    assert v2.get_secret("anthropic") == "sk-ant-ÖRNEK123456" and v2.masked("anthropic") != "sk-ant-ÖRNEK123456"


def test_vault_files_move_between_phone_and_computer(tmp_path, monkeypatch):
    path = tmp_path / "vault.bin"
    with monkeypatch.context() as m:                                       # "telefon": cryptography yok
        m.setitem(sys.modules, "cryptography", None)
        m.setitem(sys.modules, "cryptography.fernet", None)
        phone = Vault(path, kdf_iterations=1000)
        phone.initialize("ortak-parola-1")
        phone.set_secret("openai", "sk-proj-TELEFON")
    desktop = Vault(path, kdf_iterations=1000)                             # "bilgisayar": cryptography var
    assert V.Vault._crypto()[0] is RefFernet
    desktop.unlock("ortak-parola-1")
    assert desktop.get_secret("openai") == "sk-proj-TELEFON"
    desktop.set_secret("gemini", "AIza-BILGISAYAR")
    with monkeypatch.context() as m:
        m.setitem(sys.modules, "cryptography", None)
        m.setitem(sys.modules, "cryptography.fernet", None)
        again = Vault(path, kdf_iterations=1000)
        again.unlock("ortak-parola-1")
        assert again.get_secret("gemini") == "AIza-BILGISAYAR"
