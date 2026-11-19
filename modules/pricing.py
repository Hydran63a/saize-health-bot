"""価格表（data/prices.json）の読み込みと合計計算。

価格はメニューブックから書き写した公式の標準価格。店舗によって異なる場合がある。
価格が登録されていない料理は「今のグランドメニューに載っていない料理」として扱う。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
PRICES_JSON = BASE_DIR / "data" / "prices.json"


@dataclass
class PriceTotal:
    tax_included_yen: int | None  # 1品でも価格が分からなければ None
    missing: list[str]  # 価格が分からない料理のID


class PriceTable:
    def __init__(self, doc: dict[str, Any]) -> None:
        self.source: dict[str, Any] = doc.get("source", {})
        self.items: dict[str, dict[str, Any]] = doc["items"]

    @classmethod
    def load(cls, path: Path = PRICES_JSON) -> "PriceTable":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def has_price(self, menu_id: str) -> bool:
        return menu_id in self.items

    def tax_included(self, menu_id: str) -> int | None:
        item = self.items.get(menu_id)
        return None if item is None else item["price_tax_included_yen"]

    def total(self, menu_ids: list[str]) -> PriceTotal:
        """税込の合計。足りない分を0円扱いすると安く見えてしまうので、1品でも不明なら None。"""
        missing = [m for m in menu_ids if not self.has_price(m)]
        if missing:
            return PriceTotal(None, missing)
        return PriceTotal(sum(self.items[m]["price_tax_included_yen"] for m in menu_ids), [])
