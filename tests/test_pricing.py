import json
import math
from decimal import Decimal

from modules.nutrition import NutritionData
from modules.pricing import PRICES_JSON, PriceTable

DOC = {
    "source": {"checked_at": "2099-01-01"},
    "items": {
        "A": {"price_yen": 291, "price_tax_included_yen": 320, "tax_rate": 0.10},
        "B": {"price_yen": 146, "price_tax_included_yen": 160, "tax_rate": 0.10},
    },
}


def test_total_of_priced_items():
    t = PriceTable(DOC).total(["A", "B", "A"])
    assert t.tax_included_yen == 800
    assert t.missing == []


def test_total_is_none_when_any_price_is_missing():
    t = PriceTable(DOC).total(["A", "不明"])
    assert t.tax_included_yen is None  # 0円扱いにしない
    assert t.missing == ["不明"]


def test_real_price_table_is_consistent():
    """本物の価格表：名前が menu.json にあり、税込＝税抜×(1+税率) の切り捨て になっている。"""
    prices = PriceTable.load()
    menu_ids = set(NutritionData.load().items)
    for menu_id, p in prices.items.items():
        assert menu_id in menu_ids, menu_id
        expected = math.floor(Decimal(p["price_yen"]) * (1 + Decimal(str(p["tax_rate"]))))
        assert p["price_tax_included_yen"] == expected, menu_id


def test_price_table_file_is_valid_json_with_source():
    doc = json.loads(PRICES_JSON.read_text(encoding="utf-8"))
    assert doc["source"]["checked_at"]
    assert doc["status"] == "official_transcribed"
