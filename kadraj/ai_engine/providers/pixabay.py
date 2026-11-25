"""Pixabay: telifsiz görsel araması (``GET /api/``, anahtar ``key`` sorgu parametresinde)."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

from .stock import StockItem, StockProvider, _int


class PixabayProvider(StockProvider):
    def __init__(self, *, settings: Any = None, transport: Any = None) -> None:
        super().__init__("pixabay", "Pixabay", base_url="https://pixabay.com", search_path="/api/",
                         auth="query:key", ping_params={"per_page": "3"}, settings=settings, transport=transport)

    def search_params(self, query: str, limit: int, *, image_type: str = "photo") -> Dict[str, str]:
        return {"q": query, "per_page": str(max(3, min(limit, 200))), "image_type": image_type,
                "safesearch": "true"}

    def parse(self, payload: Mapping[str, Any]) -> List[StockItem]:
        items: List[StockItem] = []
        for hit in payload.get("hits") or []:
            if not isinstance(hit, Mapping):
                continue
            hid = str(hit.get("id", ""))
            items.append(StockItem(
                provider=self.id, kind="image", id=hid,
                title=str(hit.get("tags") or f"Pixabay #{hid}"),
                page_url=str(hit.get("pageURL") or ""),
                preview_url=str(hit.get("webformatURL") or hit.get("previewURL") or ""),
                download_url=str(hit.get("largeImageURL") or ""),
                author=str(hit.get("user") or ""),
                license="Pixabay lisansı (telifsiz)",
                width=_int(hit.get("imageWidth")), height=_int(hit.get("imageHeight")),
            ))
        return items
