"""Güncelleme denetimi: istek adresi doğru kurulur (ağ gerçekten çağrılmaz, urlopen sahte)."""
from __future__ import annotations

import json
import urllib.request

from kadraj import updates


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def test_fetch_latest_builds_github_releases_url(monkeypatch):
    seen: list[str] = []

    def fake_urlopen(request, timeout=None):
        seen.append(request.full_url)
        return _FakeResponse({"tag_name": "v0.1.0",
                              "html_url": "https://github.com/UnrealWarlord/kadraj/releases/tag/v0.1.0"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    release = updates.fetch_latest()
    assert updates.GITHUB_REPO == "UnrealWarlord/kadraj"
    assert seen == ["https://api.github.com/repos/UnrealWarlord/kadraj/releases/latest"]
    assert release is not None and release.version == "0.1.0"
