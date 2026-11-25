"""Pexels: telifsiz fotoğraf araması (``GET /v1/search``, anahtar ``Authorization`` başlığında)."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

from .stock import StockItem, StockProvider, _int

ORIENTATIONS = ("landscape", "portrait", "square")


class PexelsProvider(StockProvider):
    def __init__(self, *, settings: Any = None, transport: Any = None) -> None:
        super().__init__("pexels", "Pexels", base_url="https://api.pexels.com", search_path="/v1/search",
                         auth="header:Authorization", ping_params={"query": "test", "per_page": "1"},
                         settings=settings, transport=transport)

    def search_params(self, query: str, limit: int, *, orientation: str = "") -> Dict[str, str]:
        params = {"query": query, "per_page": str(limit)}
        if orientation in ORIENTATIONS:
            params["orientation"] = orientation
        return params

    def parse(self, payload: Mapping[str, Any]) -> List[StockItem]:
        items: List[StockItem] = []
        for photo in payload.get("photos") or []:
            if not isinstance(photo, Mapping):
                continue
            src = photo.get("src") if isinstance(photo.get("src"), Mapping) else {}
            pid = str(photo.get("id", ""))
            items.append(StockItem(
                provider=self.id, kind="image", id=pid,
                title=str(photo.get("alt") or f"Pexels #{pid}"),
                page_url=str(photo.get("url") or ""),
                preview_url=str(src.get("medium") or ""),
                download_url=str(src.get("original") or ""),
                author=str(photo.get("photographer") or ""),
                license="Pexels lisansı (telifsiz)",
                width=_int(photo.get("width")), height=_int(photo.get("height")),
            ))
        return items
