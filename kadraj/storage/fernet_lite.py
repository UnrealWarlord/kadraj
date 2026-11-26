"""Fernet şifrelemesi, ``cryptography`` paketi olmayan yerler için (Android; M19).

Masaüstünde kasa ``cryptography.fernet``'i kullanır. APK'da ``cryptography`` yoktur (Rust ile derlenen yerel paket).
Bu modül Fernet belirtimini (https://github.com/fernet/spec) standart kütüphane ve saf Python AES-128 ile uygular.
Belirteçler iki yönde de ``cryptography`` ile birebir uyumludur, kasa dosyası telefon ile bilgisayar arasında aynıdır.

* Belirteç: ``0x80 ‖ zaman (8 bayt) ‖ IV (16) ‖ AES-128-CBC(PKCS7) şifreli metin ‖ HMAC-SHA256 (32)``, url-safe base64.
* Anahtar: 32 bayt (url-safe base64): ilk 16 bayt imza (HMAC), son 16 bayt şifreleme (AES) anahtarı.
* Çözmeden önce HMAC sabit zamanlı karşılaştırılır (``hmac.compare_digest``); bozuk/yanlış anahtarlı belirteç
  ``InvalidToken``. IV ``secrets`` ile rastgele.
* Saf Python AES yavaştır (kasa birkaç KB, önemsiz) ve tablo erişimli olduğundan zamanlama yan kanallarına karşı
  sertleştirilmemiştir; yalnız cihazdaki kasa dosyasını şifrelemek için kullanılır.

Doğrulama: FIPS-197 ve NIST SP 800-38A (CBC) test vektörleri, Fernet belirtiminin vektörü ve masaüstünde
``cryptography`` ile çapraz deneme (``tests/security/test_fernet_lite.py``).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import struct
import time
from typing import List, Optional, Union

__all__ = ["Fernet", "InvalidToken", "aes128_encrypt_block", "aes128_decrypt_block", "cbc_encrypt", "cbc_decrypt"]

_VERSION = 0x80
_BLOCK = 16


class InvalidToken(Exception):
    """Belirteç bozuk, değiştirilmiş ya da başka bir anahtarla şifrelenmiş."""


# ---------------------------------------------------------------------------
# AES-128 (FIPS-197)
# ---------------------------------------------------------------------------
def _rotl8(x: int, shift: int) -> int:
    return ((x << shift) | (x >> (8 - shift))) & 0xFF


def _build_sboxes() -> tuple:
    """S-kutusu ve tersi: GF(2^8)'de çarpımsal ters + afin dönüşüm (3 üreteciyle gezinerek)."""
    sbox = [0] * 256
    p = q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)          # p *= 3
        q ^= q << 1                                                        # q /= 3
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        sbox[p] = q ^ _rotl8(q, 1) ^ _rotl8(q, 2) ^ _rotl8(q, 3) ^ _rotl8(q, 4) ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    inv = [0] * 256
    for i, v in enumerate(sbox):
        inv[v] = i
    return sbox, inv


def _xtime(a: int) -> int:
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _mul_table(factor: int) -> List[int]:
    out = []
    for a in range(256):
        result, x, f = 0, a, factor
        while f:
            if f & 1:
                result ^= x
            x = _xtime(x)
            f >>= 1
        out.append(result)
    return out


_SBOX, _INV_SBOX = _build_sboxes()
_M2, _M3, _M9, _M11, _M13, _M14 = (_mul_table(f) for f in (2, 3, 9, 11, 13, 14))
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)
#: ShiftRows: yeni[i] = eski[_SHIFT[i]] (durum sütun sütun: i = satır + 4·sütun)
_SHIFT = tuple((i % 4) + 4 * (((i // 4) + (i % 4)) % 4) for i in range(16))
_INV_SHIFT = tuple(_SHIFT.index(i) for i in range(16))


def _expand_key(key: bytes) -> List[List[int]]:
    if len(key) != 16:
        raise ValueError("AES-128 anahtarı 16 bayt olmalı")
    words = [list(key[4 * i:4 * i + 4]) for i in range(4)]
    for i in range(4, 44):
        temp = list(words[i - 1])
        if i % 4 == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // 4 - 1]
        words.append([a ^ b for a, b in zip(words[i - 4], temp)])
    return [words[4 * r] + words[4 * r + 1] + words[4 * r + 2] + words[4 * r + 3] for r in range(11)]


def _encrypt(state: List[int], rk: List[List[int]]) -> List[int]:
    s = [a ^ b for a, b in zip(state, rk[0])]
    for rnd in range(1, 11):
        s = [_SBOX[s[_SHIFT[i]]] for i in range(16)]                     # SubBytes + ShiftRows
        if rnd != 10:
            m = []
            for c in range(0, 16, 4):                                      # MixColumns
                a0, a1, a2, a3 = s[c], s[c + 1], s[c + 2], s[c + 3]
                m += (_M2[a0] ^ _M3[a1] ^ a2 ^ a3, a0 ^ _M2[a1] ^ _M3[a2] ^ a3,
                      a0 ^ a1 ^ _M2[a2] ^ _M3[a3], _M3[a0] ^ a1 ^ a2 ^ _M2[a3])
            s = m
        k = rk[rnd]
        s = [a ^ b for a, b in zip(s, k)]
    return s


def _decrypt(state: List[int], rk: List[List[int]]) -> List[int]:
    s = [a ^ b for a, b in zip(state, rk[10])]
    for rnd in range(9, -1, -1):
        s = [_INV_SBOX[s[_INV_SHIFT[i]]] for i in range(16)]             # InvShiftRows + InvSubBytes
        k = rk[rnd]
        s = [a ^ b for a, b in zip(s, k)]
        if rnd != 0:
            m = []
            for c in range(0, 16, 4):                                      # InvMixColumns
                a0, a1, a2, a3 = s[c], s[c + 1], s[c + 2], s[c + 3]
                m += (_M14[a0] ^ _M11[a1] ^ _M13[a2] ^ _M9[a3], _M9[a0] ^ _M14[a1] ^ _M11[a2] ^ _M13[a3],
                      _M13[a0] ^ _M9[a1] ^ _M14[a2] ^ _M11[a3], _M11[a0] ^ _M13[a1] ^ _M9[a2] ^ _M14[a3])
            s = m
    return s


def aes128_encrypt_block(key: bytes, block: bytes) -> bytes:
    if len(block) != _BLOCK:
        raise ValueError("blok 16 bayt olmalı")
    return bytes(_encrypt(list(block), _expand_key(key)))


def aes128_decrypt_block(key: bytes, block: bytes) -> bytes:
    if len(block) != _BLOCK:
        raise ValueError("blok 16 bayt olmalı")
    return bytes(_decrypt(list(block), _expand_key(key)))


def cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-128-CBC; ``data`` 16'nın katı olmalı (dolgu çağıranın işi)."""
    if len(iv) != _BLOCK or len(data) % _BLOCK:
        raise ValueError("IV 16 bayt, veri 16'nın katı olmalı")
    rk = _expand_key(key)
    prev, out = list(iv), bytearray()
    for i in range(0, len(data), _BLOCK):
        prev = _encrypt([a ^ b for a, b in zip(data[i:i + _BLOCK], prev)], rk)
        out += bytes(prev)
    return bytes(out)


def cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    if len(iv) != _BLOCK or len(data) % _BLOCK:
        raise ValueError("IV 16 bayt, veri 16'nın katı olmalı")
    rk = _expand_key(key)
    prev, out = list(iv), bytearray()
    for i in range(0, len(data), _BLOCK):
        block = list(data[i:i + _BLOCK])
        out += bytes(a ^ b for a, b in zip(_decrypt(block, rk), prev))
        prev = block
    return bytes(out)


def _pad(data: bytes) -> bytes:
    n = _BLOCK - len(data) % _BLOCK
    return data + bytes([n]) * n


def _unpad(data: bytes) -> bytes:
    n = data[-1] if data else 0
    if not 1 <= n <= _BLOCK or data[-n:] != bytes([n]) * n:
        raise InvalidToken()
    return data[:-n]


# ---------------------------------------------------------------------------
# Fernet
# ---------------------------------------------------------------------------
class Fernet:
    """``cryptography.fernet.Fernet`` ile aynı arayüz (kasanın kullandığı kısım) ve aynı belirteç biçimi."""

    def __init__(self, key: Union[bytes, str]) -> None:
        try:
            raw = base64.urlsafe_b64decode(key)
        except (binascii.Error, TypeError, ValueError) as exc:
            raise ValueError("Fernet anahtarı 32 baytlık url-safe base64 olmalı") from exc
        if len(raw) != 32:
            raise ValueError("Fernet anahtarı 32 baytlık url-safe base64 olmalı")
        self._signing_key, self._encryption_key = raw[:16], raw[16:]

    def __repr__(self) -> str:                                             # anahtar içermez
        return "<Fernet (kadraj)>"

    @classmethod
    def generate_key(cls) -> bytes:
        return base64.urlsafe_b64encode(secrets.token_bytes(32))

    def encrypt(self, data: bytes) -> bytes:
        return self.encrypt_at_time(data, int(time.time()))

    def encrypt_at_time(self, data: bytes, current_time: int) -> bytes:
        return self._encrypt_from_parts(data, current_time, secrets.token_bytes(16))

    def _encrypt_from_parts(self, data: bytes, current_time: int, iv: bytes) -> bytes:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data bayt olmalı")
        body = cbc_encrypt(self._encryption_key, iv, _pad(bytes(data)))
        basic = bytes([_VERSION]) + struct.pack(">Q", int(current_time)) + iv + body
        mac = hmac.new(self._signing_key, basic, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(basic + mac)

    def decrypt(self, token: Union[bytes, str], ttl: Optional[int] = None) -> bytes:
        if isinstance(token, str):
            token = token.encode("ascii", "replace")
        if not isinstance(token, (bytes, bytearray)):
            raise TypeError("token bayt ya da metin olmalı")
        try:
            raw = base64.urlsafe_b64decode(bytes(token))
        except (binascii.Error, ValueError) as exc:
            raise InvalidToken() from exc
        if len(raw) < 1 + 8 + 16 + 32 or raw[0] != _VERSION or (len(raw) - 57) % _BLOCK or len(raw) == 57:
            raise InvalidToken()
        basic, mac = raw[:-32], raw[-32:]
        expected = hmac.new(self._signing_key, basic, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected):
            raise InvalidToken()
        if ttl is not None:
            stamp = struct.unpack(">Q", basic[1:9])[0]
            now = int(time.time())
            if stamp + ttl < now or now + 60 < stamp:
                raise InvalidToken()
        iv, body = basic[9:25], basic[25:]
        return _unpad(cbc_decrypt(self._encryption_key, iv, body))
