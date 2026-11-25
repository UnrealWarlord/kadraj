"""Freesound: telifsiz ses araması (``GET /apiv2/search/text/``, ``Authorization: Token …``).

İndirme OAuth gerektirir; bu katman yalnızca önizleme ve sayfa adresi verir (``download_url`` boş).
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

from .stock import StockItem, StockProvider, _float

FIELDS = "id,name,username,duration,license,previews,url"


class FreesoundProvider(StockProvider):
    def __init__(self, *, settings: Any = None, transport: Any = None) -> None:
        super().__init__("freesound", "Freesound", base_url="https://freesound.org",
                         search_path="/apiv2/search/text/", auth="token",
                         ping_params={"query": "test", "page_size": "1"}, settings=settings, transport=transport)

    def search_params(self, query: str, limit: int) -> Dict[str, str]:
        return {"query": query, "page_size": str(max(1, min(limit, 150))), "fields": FIELDS}

    def parse(self, payload: Mapping[str, Any]) -> List[StockItem]:
        items: List[StockItem] = []
        for result in payload.get("results") or []:
            if not isinstance(result, Mapping):
                continue
            previews = result.get("previews") if isinstance(result.get("previews"), Mapping) else {}
            rid = str(result.get("id", ""))
            items.append(StockItem(
                provider=self.id, kind="audio", id=rid,
                title=str(result.get("name") or f"Freesound #{rid}"),
                page_url=str(result.get("url") or ""),
                preview_url=str(previews.get("preview-hq-mp3") or previews.get("preview-lq-mp3") or ""),
                download_url="",
                author=str(result.get("username") or ""),
                license=str(result.get("license") or ""),
                duration=_float(result.get("duration")),
            ))
        return items
