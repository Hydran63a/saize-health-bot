"""材料の仮定（recipes.json）と食品成分表から、公式に無い栄養素を推定する。

- 推定値は data/estimates.json に出力し、公式値（menu.json）とは混ぜない。
- 計算したエネルギーを公式エネルギーに合わせる「調整倍率」を全材料に掛ける。
- 信頼度は 調整倍率のずれ と 公式食塩相当量との差 で判定する。

使い方:
    python -m modules.estimator
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from modules.food_composition import CITATION, RECIPES_JSON, SUBSET_JSON, load_subset

BASE_DIR = Path(__file__).resolve().parent.parent
MENU_JSON = BASE_DIR / "data" / "menu.json"
ESTIMATES_JSON = BASE_DIR / "data" / "estimates.json"

# 推定して出力する栄養素（calories と salt は公式値があるので照合のみに使う）
ESTIMATED_NUTRIENTS = ("protein", "fat", "saturated_fat", "carbohydrate", "dietary_fiber", "cholesterol")
CHECK_NUTRIENTS = ("calories", "salt")

CONFIDENCE_RULES = (
    # (名前, 調整倍率のずれ上限, 食塩差の上限 g)
    ("medium", 0.20, 0.5),
    ("low", 0.40, 1.0),
)
HIGH_MAX_INGREDIENTS = 2
HIGH_RULE = (0.10, 0.3)


def sum_nutrient(ingredients: list[dict[str, Any]], foods: dict[str, Any], nutrient: str) -> dict[str, Any]:
    """材料の合計値。値の無い材料があれば None と、その材料名を返す。"""
    total = 0.0
    missing: list[str] = []
    mext_estimated: list[str] = []
    for ing in ingredients:
        per100 = foods[ing["food_code"]]["per_100g"]
        cell = per100[nutrient]
        value = cell.get("value")
        if value is None:
            fat = per100["fat"].get("value")
            if nutrient == "saturated_fat" and fat == 0:
                continue  # 脂質0の食品は飽和脂肪酸も0とみなす
            missing.append(ing["label"])
            continue
        if cell.get("mext_estimated"):
            mext_estimated.append(ing["label"])
        total += value * ing["grams"] / 100
    return {"value": None if missing else total, "missing": missing, "mext_estimated": mext_estimated}


def judge_confidence(scale: float, salt_diff: float | None, n_ingredients: int) -> str:
    deviation = abs(scale - 1)
    if salt_diff is None:
        return "low" if deviation <= CONFIDENCE_RULES[1][1] else "very_low"
    if n_ingredients <= HIGH_MAX_INGREDIENTS and deviation <= HIGH_RULE[0] and salt_diff <= HIGH_RULE[1]:
        return "high"
    for name, max_dev, max_salt in CONFIDENCE_RULES:
        if deviation <= max_dev and salt_diff <= max_salt:
            return name
    return "very_low"


def estimate_recipe(recipe: dict[str, Any], menu_item: dict[str, Any], foods: dict[str, Any]) -> dict[str, Any]:
    ingredients = recipe["ingredients"]
    official_kcal = menu_item["nutrition"]["calories"].get("value")
    official_salt = menu_item["nutrition"]["salt"].get("value")

    raw = {n: sum_nutrient(ingredients, foods, n) for n in (*CHECK_NUTRIENTS, *ESTIMATED_NUTRIENTS)}
    raw_kcal = raw["calories"]["value"]
    if not official_kcal or not raw_kcal:
        raise ValueError(f"{recipe['menu_id']}: エネルギーが計算できないため調整できません")
    scale = official_kcal / raw_kcal

    estimates: dict[str, Any] = {}
    for n in ESTIMATED_NUTRIENTS:
        r = raw[n]
        if r["value"] is None:
            estimates[n] = {"value": None, "status": "not_estimable", "missing_in": r["missing"]}
        else:
            digits = 0 if n == "cholesterol" else 1
            value = round(r["value"] * scale, digits)
            estimates[n] = {"value": int(value) if digits == 0 else value, "status": "estimated"}

    est_salt = raw["salt"]["value"] * scale if raw["salt"]["value"] is not None else None
    salt_diff = abs(est_salt - official_salt) if est_salt is not None and official_salt is not None else None
    mext_flags = sorted({label for r in raw.values() for label in r["mext_estimated"]})

    return {
        "menu_id": recipe["menu_id"],
        "name": menu_item["name"],
        "method": "recipe_calc_kcal_scaled",
        "confidence": judge_confidence(scale, salt_diff, len(ingredients)),
        "nutrients": estimates,
        "checks": {
            "calories_raw": round(raw_kcal),
            "calories_official": official_kcal,
            "kcal_scale": round(scale, 3),
            "salt_estimated": round(est_salt, 2) if est_salt is not None else None,
            "salt_official": official_salt,
            "salt_diff": round(salt_diff, 2) if salt_diff is not None else None,
        },
        "assumed_ingredients": [
            {"label": i["label"], "food_code": i["food_code"], "grams": i["grams"], "grams_scaled": round(i["grams"] * scale, 1)}
            for i in ingredients
        ],
        "recipe_notes": recipe.get("notes"),
        "inputs_with_mext_estimates": mext_flags,
    }


def build_estimates(recipes_doc: dict[str, Any], menu: dict[str, Any], foods: dict[str, Any]) -> dict[str, Any]:
    items = {i["id"]: i for i in menu["items"]}
    results = []
    for recipe in recipes_doc["recipes"]:
        menu_id = recipe["menu_id"]
        if menu_id not in items:
            raise KeyError(f"menu.json に無いメニューです: {menu_id}")
        unknown = [i["food_code"] for i in recipe["ingredients"] if i["food_code"] not in foods]
        if unknown:
            raise KeyError(f"{menu_id}: food_subset.json に無い食品番号 {unknown}（export を実行してください）")
        results.append(estimate_recipe(recipe, items[menu_id], foods))
    return {
        "schema_version": 1,
        "generated_at": date.today().isoformat(),
        "status": "estimated",
        "disclaimer": "推定値です。サイゼリヤの公式値ではありません。" + recipes_doc["assumption_policy"],
        "citation": CITATION,
        "official_source": menu["source"]["url"],
        "confidence_legend": {
            "high": "材料が単純で、エネルギー・食塩とも公式値とよく一致",
            "medium": f"調整倍率のずれ{int(CONFIDENCE_RULES[0][1] * 100)}%以内かつ食塩差{CONFIDENCE_RULES[0][2]}g以内",
            "low": f"調整倍率のずれ{int(CONFIDENCE_RULES[1][1] * 100)}%以内かつ食塩差{CONFIDENCE_RULES[1][2]}g以内",
            "very_low": "上記を満たさない（材料の仮定を見直すべき）",
        },
        "items": results,
    }


def main() -> int:
    recipes_doc = json.loads(RECIPES_JSON.read_text(encoding="utf-8"))
    menu = json.loads(MENU_JSON.read_text(encoding="utf-8"))
    foods = load_subset(SUBSET_JSON)
    try:
        doc = build_estimates(recipes_doc, menu, foods)
    except (KeyError, ValueError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    ESTIMATES_JSON.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"出力: {ESTIMATES_JSON}（{len(doc['items'])} 品）")
    print(f"{'メニュー':<16} 信頼度   倍率   塩分(推定/公式)  飽和脂肪酸  食物繊維")
    for it in doc["items"]:
        c, n = it["checks"], it["nutrients"]
        sat = n["saturated_fat"]["value"]
        fib = n["dietary_fiber"]["value"]
        print(
            f"{it['name']:<16} {it['confidence']:<8} {c['kcal_scale']:<6} "
            f"{c['salt_estimated']}/{c['salt_official']:<10} "
            f"{'算出不可' if sat is None else f'{sat}g':<10} {'算出不可' if fib is None else f'{fib}g'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
