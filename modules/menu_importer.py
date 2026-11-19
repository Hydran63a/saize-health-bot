"""公式のアレルゲン・カロリー塩分表の写し（TSV）を menu.json に変換する。

ここで扱うのは **公式値だけ**。推定値は別ファイル（data/estimates.json）で管理する。

使い方:
    python -m modules.menu_importer                      # data/raw の最新の写しを変換
    python -m modules.menu_importer --raw data/raw/allergy_2026-11-17.tsv
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"
DEFAULT_OUTPUT = BASE_DIR / "data" / "menu.json"

EXPECTED_HEADER = ["商品名", "エネルギーkcal", "食塩相当量g", "アレルゲン"]

# 公式表の列（法令で規定する9品目 + 表示を推奨する20品目 + 魚介類）
KNOWN_ALLERGENS = frozenset(
    "えび カシューナッツ かに くるみ 小麦 そば 卵 乳 落花生 "
    "アーモンド あわび いか いくら オレンジ キウイフルーツ 牛肉 ごま さけ さば "
    "大豆 鶏肉 バナナ ピスタチオ 豚肉 マカダミアナッツ もも やまいも りんご ゼラチン 魚介類".split()
)

# 公式表に載っていない栄養素。値を補完せず「非公開」として明示する。
UNPUBLISHED_NUTRIENTS = ("protein", "fat", "saturated_fat", "carbohydrate", "dietary_fiber")


class ImportError_(ValueError):
    """写しファイルの形式がおかしいときのエラー。"""


def parse_official_number(raw: str) -> dict[str, Any]:
    """公式表の数値セルを解釈する。

    - "-"  : 1食あたりで計算できない → value=None, status="not_calculable"
    - ""   : 空欄 → value=None, status="unknown"
    - "0"  : 微量（公式の表記ルール）→ value=0, status="official", trace=True
    - 数値 : value=数値, status="official"
    """
    text = raw.strip()
    if text == "-":
        return {"value": None, "status": "not_calculable"}
    if text == "":
        return {"value": None, "status": "unknown"}
    try:
        number = float(text)
    except ValueError as exc:
        raise ImportError_(f"数値として読めません: {raw!r}") from exc
    value: int | float = int(number) if number.is_integer() and "." not in text else number
    result: dict[str, Any] = {"value": value, "status": "official"}
    if number == 0:
        result["trace"] = True  # 公式表では「微量」を 0 と表示している
    return result


def split_name(raw_name: str) -> tuple[str, str | None]:
    """「サイゼリヤドレッシング※1食30gあたり」→ ("サイゼリヤドレッシング", "1食30gあたり")"""
    name, sep, note = raw_name.partition("※")
    return name.strip(), (note.strip() or None) if sep else None


def parse_allergens(raw: str) -> tuple[list[str], list[str]]:
    allergens: list[str] = []
    notes: list[str] = []
    for item in filter(None, (x.strip() for x in raw.split(","))):
        if item.endswith("▲"):
            item = item[:-1]
            notes.append(f"{item}：モッツァレラチーズの水牛の乳")
        if item not in KNOWN_ALLERGENS:
            raise ImportError_(f"未知のアレルゲン名です: {item!r}")
        allergens.append(item)
    return allergens, notes


def read_snapshot(tsv_path: Path) -> tuple[list[list[str]], dict[str, Any]]:
    meta_path = tsv_path.with_suffix(".meta.json")
    if not meta_path.exists():
        raise ImportError_(f"取得元情報ファイルがありません: {meta_path.name}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    data = tsv_path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != meta.get("sha256"):
        raise ImportError_(
            f"写しファイルの内容が記録時と違います（sha256 不一致）: {tsv_path.name}"
        )

    lines = data.decode("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    rows = [line.split("\t") for line in lines]
    if not rows or rows[0] != EXPECTED_HEADER:
        raise ImportError_(f"見出し行が想定と違います: {rows[0] if rows else None}")
    for number, row in enumerate(rows[1:], start=2):
        if len(row) != len(EXPECTED_HEADER):
            raise ImportError_(f"{number}行目の列数が {len(row)} です（想定は {len(EXPECTED_HEADER)}）")
    return rows[1:], meta


def build_menu(rows: list[list[str]], meta: dict[str, Any]) -> dict[str, Any]:
    name_counts = Counter(row[0] for row in rows)
    seen: Counter[str] = Counter()
    items = []
    for index, (raw_name, kcal, salt, allergen_text) in enumerate(rows, start=1):
        seen[raw_name] += 1
        item_id = raw_name if name_counts[raw_name] == 1 else f"{raw_name}#{seen[raw_name]}"
        name, serving_note = split_name(raw_name)
        allergens, allergen_notes = parse_allergens(allergen_text)

        nutrition: dict[str, Any] = {
            "calories": parse_official_number(kcal),
            "salt": parse_official_number(salt),
        }
        for key in UNPUBLISHED_NUTRIENTS:
            nutrition[key] = {"value": None, "status": "not_published"}

        items.append(
            {
                "id": item_id,
                "name": name,
                "official_name": raw_name,
                "serving_note": serving_note,
                "source_row": index,
                "duplicate_name": name_counts[raw_name] > 1,
                "category": None,  # 公式表に区分が無いため未設定（推測で付けない）
                "price": None,  # 価格は別途確認して登録する
                "available": True,
                "nutrition": nutrition,
                "allergens": allergens,
                "allergen_notes": allergen_notes,
            }
        )

    return {
        "schema_version": 1,
        "source": {
            "name": meta["source_name"],
            "url": meta["source_url"],
            "official_updated_at": meta["official_updated_at"],
            "captured_at": meta["captured_at"],
            "notes": meta.get("official_notes", []),
        },
        "status_legend": {
            "official": "公式サイトに掲載された値",
            "not_calculable": "公式表で「-」（1食あたりで計算できない）",
            "unknown": "公式表で空欄",
            "not_published": "公式サイトに掲載されていない（推定値は estimates.json で別管理）",
        },
        "items": items,
    }


def latest_snapshot(raw_dir: Path = RAW_DIR) -> Path:
    candidates = sorted(raw_dir.glob("allergy_*.tsv"))
    if not candidates:
        raise ImportError_(f"写しファイルが見つかりません: {raw_dir}")
    return candidates[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="公式表の写しを menu.json に変換します")
    parser.add_argument("--raw", type=Path, help="変換する TSV（省略時は data/raw の最新）")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    try:
        tsv_path = args.raw or latest_snapshot()
        rows, meta = read_snapshot(tsv_path)
        menu = build_menu(rows, meta)
    except ImportError_ as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(menu, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    items = menu["items"]
    duplicates = sorted({i["official_name"] for i in items if i["duplicate_name"]})
    print(f"読み込み: {tsv_path.name}（公式更新日 {meta['official_updated_at']}）")
    print(f"出力    : {args.output}（{len(items)} 品）")
    print(f"同名商品: {len(duplicates)} 種類 → {', '.join(duplicates)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
