"""日本食品標準成分表（八訂）増補2023年 の読み込み。

出典: 日本食品標準成分表（八訂）増補2023年から引用（文部科学省）
https://www.mext.go.jp/a_menu/syokuhinseibun/mext_00001.html

- Excel（data/external/、Git管理外）を読むのはデータ作成時のみ。
- 推定に使う食品だけを data/food_subset.json に書き出し、実行時はそちらを使う。

使い方:
    python -m modules.food_composition search レタス きゃべつ
    python -m modules.food_composition export        # recipes.json で使う食品を書き出す
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
EXTERNAL_DIR = BASE_DIR / "data" / "external"
MAIN_XLSX = EXTERNAL_DIR / "mext_honpyo_ch2.xlsx"
FATTY_XLSX = EXTERNAL_DIR / "mext_fattyacid_ch2_t1.xlsx"
SUBSET_JSON = BASE_DIR / "data" / "food_subset.json"
RECIPES_JSON = BASE_DIR / "data" / "recipes.json"

CITATION = "日本食品標準成分表（八訂）増補2023年から引用（文部科学省）"
SOURCE_URL = "https://www.mext.go.jp/a_menu/syokuhinseibun/mext_00001.html"

# 本表の成分識別子 → このプロジェクトでの名前（可食部100gあたり）
MAIN_FIELDS = {
    "ENERC_KCAL": "calories",
    "PROT-": "protein",
    "FAT-": "fat",
    "CHOCDF-": "carbohydrate",
    "FIB-": "dietary_fiber",
    "NACL_EQ": "salt",
    "CHOLE": "cholesterol",  # mg
}
FATTY_FIELDS = {"FASAT": "saturated_fat"}
NUTRIENTS = (*MAIN_FIELDS.values(), *FATTY_FIELDS.values())

_PAREN = re.compile(r"^\((.*)\)$")


def parse_cell(raw: Any) -> dict[str, Any]:
    """成分表のセルを解釈する。

    - 数値          → value
    - "Tr"          → 0（微量）, trace
    - "(1.2)"       → 1.2, mext_estimated（成分表自身の推計値）
    - "-" "*" 空欄  → None（未測定・算出対象外）
    """
    if raw is None:
        return {"value": None}
    if isinstance(raw, (int, float)):
        return {"value": float(raw)}
    text = str(raw).strip()
    flags: dict[str, Any] = {}
    match = _PAREN.match(text)
    if match:
        text = match.group(1).strip()
        flags["mext_estimated"] = True
    if text in ("", "-", "*"):
        return {"value": None}
    if text == "Tr":
        return {"value": 0.0, "trace": True, **flags}
    try:
        return {"value": float(text), **flags}
    except ValueError:
        return {"value": None, "unparsed": str(raw)}


def _rows(path: Path, header_marker: str = "成分識別子"):
    import openpyxl  # データ作成時のみ必要（requirements-dev.txt）

    if not path.exists():
        raise FileNotFoundError(f"{path} がありません。README の手順で成分表をダウンロードしてください。")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = wb["表全体"].iter_rows(values_only=True)
    ids = None
    for row in rows:
        if ids is None:
            if header_marker in row:
                ids = [str(c).strip() if c is not None else None for c in row]
            continue
        if row[1] is None:
            continue
        yield ids, row


def load_foods() -> dict[str, dict[str, Any]]:
    foods: dict[str, dict[str, Any]] = {}
    for ids, row in _rows(MAIN_XLSX):
        col = {k: i for i, k in enumerate(ids) if k}
        code = str(row[1]).strip()
        foods[code] = {
            "code": code,
            "name": re.sub(r"\s+", " ", str(row[3]).replace("　", " ")).strip(),
            "per_100g": {name: parse_cell(row[col[key]]) for key, name in MAIN_FIELDS.items()},
        }
    for ids, row in _rows(FATTY_XLSX):
        col = {k: i for i, k in enumerate(ids) if k}
        code = str(row[1]).strip()
        if code in foods:
            for key, name in FATTY_FIELDS.items():
                foods[code]["per_100g"][name] = parse_cell(row[col[key]])
    for food in foods.values():
        for name in FATTY_FIELDS.values():
            food["per_100g"].setdefault(name, {"value": None, "not_in_fatty_acid_table": True})
    return foods


def search(foods: dict[str, dict[str, Any]], keywords: list[str]) -> list[dict[str, Any]]:
    return [f for f in foods.values() if all(k in f["name"] for k in keywords)]


def export_subset(foods: dict[str, dict[str, Any]], codes: set[str]) -> dict[str, Any]:
    missing = sorted(codes - foods.keys())
    if missing:
        raise KeyError(f"成分表に無い食品番号: {missing}")
    return {
        "citation": CITATION,
        "source_url": SOURCE_URL,
        "unit": "可食部100gあたり（cholesterol は mg、それ以外は g / kcal）",
        "foods": {code: foods[code] for code in sorted(codes)},
    }


def load_subset(path: Path = SUBSET_JSON) -> dict[str, dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["foods"]


def recipe_food_codes(path: Path = RECIPES_JSON) -> set[str]:
    recipes = json.loads(path.read_text(encoding="utf-8"))["recipes"]
    return {ing["food_code"] for r in recipes for ing in r["ingredients"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="食品成分表ツール")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("search", help="食品名で検索（キーワードはすべて含むもの）")
    s.add_argument("keywords", nargs="+")
    sub.add_parser("export", help="recipes.json で使う食品を food_subset.json に書き出す")
    args = parser.parse_args(argv)

    foods = load_foods()
    if args.command == "search":
        for f in search(foods, args.keywords):
            p = f["per_100g"]
            print(
                f"{f['code']}  {f['name']}  "
                f"{p['calories']['value']}kcal 脂質{p['fat']['value']} 飽和{p['saturated_fat']['value']} "
                f"繊維{p['dietary_fiber']['value']} 塩{p['salt']['value']}"
            )
        return 0

    subset = export_subset(foods, recipe_food_codes())
    SUBSET_JSON.write_text(json.dumps(subset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"出力: {SUBSET_JSON}（{len(subset['foods'])} 食品）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
