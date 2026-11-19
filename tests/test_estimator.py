import json

import pytest

from modules import estimator as est
from modules import food_composition as fc


def food(kcal, fat, sat, fiber, salt, protein=0.0, carb=0.0, chole=0.0, sat_est=False):
    sat_cell = {"value": sat} if sat is not None else {"value": None}
    if sat_est:
        sat_cell["mext_estimated"] = True
    return {
        "per_100g": {
            "calories": {"value": kcal},
            "fat": {"value": fat},
            "saturated_fat": sat_cell,
            "dietary_fiber": {"value": fiber},
            "salt": {"value": salt},
            "protein": {"value": protein},
            "carbohydrate": {"value": carb},
            "cholesterol": {"value": chole},
        }
    }


FOODS = {
    "A": food(100, 10, 2.0, 1.0, 1.0),
    "SALT": food(0, 0, None, 0, 100),  # 脂質0・飽和脂肪酸の値なし
    "SAUCE": food(100, 3, None, None, 1.0),  # 脂質ありで飽和脂肪酸の値なし
}


def menu_item(kcal, salt):
    return {"name": "テスト", "nutrition": {"calories": {"value": kcal}, "salt": {"value": salt}}}


def test_scaling_matches_official_kcal():
    recipe = {"menu_id": "x", "ingredients": [{"label": "A", "food_code": "A", "grams": 100}]}
    r = est.estimate_recipe(recipe, menu_item(120, 1.2), FOODS)
    assert r["checks"]["kcal_scale"] == 1.2
    assert r["nutrients"]["saturated_fat"] == {"value": 2.4, "status": "estimated"}
    assert r["checks"]["salt_estimated"] == 1.2
    assert r["confidence"] == "medium"  # 材料1つでも倍率のずれが10%を超えるので high にはならない


def test_zero_fat_food_counts_as_zero_saturated_fat():
    recipe = {"menu_id": "x", "ingredients": [
        {"label": "A", "food_code": "A", "grams": 100},
        {"label": "塩", "food_code": "SALT", "grams": 1},
    ]}
    r = est.estimate_recipe(recipe, menu_item(100, 2.0), FOODS)
    assert r["nutrients"]["saturated_fat"]["status"] == "estimated"


def test_missing_value_makes_nutrient_not_estimable():
    recipe = {"menu_id": "x", "ingredients": [
        {"label": "A", "food_code": "A", "grams": 100},
        {"label": "ソース", "food_code": "SAUCE", "grams": 50},
    ]}
    r = est.estimate_recipe(recipe, menu_item(150, 1.5), FOODS)
    assert r["nutrients"]["saturated_fat"] == {"value": None, "status": "not_estimable", "missing_in": ["ソース"]}
    assert r["nutrients"]["dietary_fiber"]["status"] == "not_estimable"
    assert r["nutrients"]["fat"]["status"] == "estimated"


@pytest.mark.parametrize(
    "scale,salt_diff,n,expected",
    [
        (1.05, 0.1, 1, "high"),
        (1.05, 0.1, 3, "medium"),
        (1.15, 0.4, 5, "medium"),
        (1.30, 0.4, 5, "low"),
        (1.15, 0.8, 5, "low"),
        (1.50, 0.1, 5, "very_low"),
        (0.95, 1.5, 5, "very_low"),
        (1.10, None, 5, "low"),
    ],
)
def test_judge_confidence(scale, salt_diff, n, expected):
    assert est.judge_confidence(scale, salt_diff, n) == expected


def test_parse_cell():
    assert fc.parse_cell(12.5) == {"value": 12.5}
    assert fc.parse_cell("Tr") == {"value": 0.0, "trace": True}
    assert fc.parse_cell("(1.2)") == {"value": 1.2, "mext_estimated": True}
    assert fc.parse_cell("(Tr)") == {"value": 0.0, "trace": True, "mext_estimated": True}
    assert fc.parse_cell("-")["value"] is None
    assert fc.parse_cell("*")["value"] is None
    assert fc.parse_cell(None)["value"] is None


def test_unknown_menu_id_is_rejected():
    doc = {"assumption_policy": "", "recipes": [{"menu_id": "存在しない", "ingredients": []}]}
    menu = {"source": {"url": ""}, "items": []}
    with pytest.raises(KeyError):
        est.build_estimates(doc, menu, FOODS)


def test_real_estimates_never_overwrite_official_values():
    """estimates.json に calories / salt の推定値を出さない（公式値と混同しない）。"""
    path = est.ESTIMATES_JSON
    if not path.exists():
        pytest.skip("estimates.json 未生成")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["status"] == "estimated"
    for item in doc["items"]:
        assert "calories" not in item["nutrients"] and "salt" not in item["nutrients"]
        assert all(v["status"] in ("estimated", "not_estimable") for v in item["nutrients"].values())
