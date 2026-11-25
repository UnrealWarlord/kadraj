"""Güvenlik şeridi test düzeni: gerçek ağ yok, gerçek keyring yok.

* ``no_network`` (otomatik): ``socket.socket.connect`` ve ``getaddrinfo`` engellenir; bağlanma girişimleri
  ``attempts`` listesine yazılır ve ``OSError`` olur. Testler HTTP'yi ``httpx.MockTransport`` ile yapar.
* Keyring: kök ``conftest.py`` zaten bellek-içi sahte keyring kurar.
"""
from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempts = []

    def blocked_connect(self, *a, **k):
        attempts.append(a)
        raise OSError("ağ kapalı (test)")

    def blocked_dns(*a, **k):
        attempts.append(a)
        raise socket.gaierror("dns kapalı (test)")

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
    monkeypatch.setattr(socket, "getaddrinfo", blocked_dns)
    return attempts
