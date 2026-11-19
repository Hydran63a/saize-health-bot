"""栄養評価エンジン（Phase 3）。

選んだメニューの組み合わせ（1食分）を、次の3つの観点から別々に評価する。
総合点は出さない（仕様書「単純な総合点だけで料理を順位付けしない」）。

- 塩分        : 公式の食塩相当量の合計
- LDLコレステロール : 飽和脂肪酸・食物繊維（推定値）、コレステロール（参考）、材料のタグ
- 中性脂肪    : 公式エネルギーの合計、炭水化物（推定値）、気づきの点

数値に使うのは「公式値」と「信頼度 high / medium の推定値」だけ。
使える推定値が無い料理が1品でもあれば、その栄養素の合計は出さない
（足りない分を0扱いすると、実際より少なく見えてしまうため）。

使い方（確認用）:
    python -m modules.nutrition 小エビのサラダ "ミラノ風ドリア#1"
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# 数値評価に使ってよい推定値の信頼度
USABLE_CONFIDENCE = frozenset({"high", "medium"})

# 判定の名前（Phase 4 で文章にするときに使う）
WITHIN = "目安以内"
SLIGHTLY_HIGH = "やや多め"
HIGH = "多め"
ENOUGH = "しっかり"
MODERATE = "ふつう"
LOW = "少なめ"
NOT_EVALUATED = "評価できない"

GOOD_TAGS_FOR_LDL = ("野菜", "豆", "海藻", "きのこ")
CAUTION_TAGS_FOR_LDL = ("チーズ・クリーム・バター", "加工肉")


# ---------------------------------------------------------------------------
# データクラス：評価結果を入れておく「型」
# ---------------------------------------------------------------------------


@dataclass
class PerspectiveResult:
    """1つの観点（塩分・LDL・中性脂肪）の評価結果。"""

    name: str
    level: str | None  # 判定（中性脂肪のように判定しない観点は None）
    values: dict[str, Any] = field(default_factory=dict)  # 数値（合計・割合など）
    points: list[str] = field(default_factory=list)  # 気づきの点（タグから）
    notes: list[str] = field(default_factory=list)  # 注意書き（データ不足など）


@dataclass
class MealEvaluation:
    """1食分の評価結果全体。"""

    menu_ids: list[str]
    names: list[str]
    salt: PerspectiveResult
    ldl: PerspectiveResult
    triglyceride: PerspectiveResult
    missing_estimates: list[str]  # 使える推定値が無い料理
    untagged: list[str]  # タグが付いていない料理


# ---------------------------------------------------------------------------
# データの読み込み
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class NutritionData:
    """評価に必要な4つのデータをまとめて持つ。"""

    def __init__(
        self,
        menu: dict[str, Any],
        estimates: dict[str, Any],
        references: dict[str, Any],
        tags: dict[str, Any],
    ) -> None:
        self.items: dict[str, dict[str, Any]] = {i["id"]: i for i in menu["items"]}
        self.estimates: dict[str, dict[str, Any]] = {e["menu_id"]: e for e in estimates["items"]}
        self.references = references
        self.tags: dict[str, list[str]] = tags["items"]

        # タグ付けしたのに menu.json に無い名前があれば、打ち間違いなのですぐ知らせる
        unknown = sorted(set(self.tags) - set(self.items))
        if unknown:
            raise KeyError(f"menu_tags.json に menu.json に無い名前があります: {unknown}")

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "NutritionData":
        return cls(
            menu=_load_json(data_dir / "menu.json"),
            estimates=_load_json(data_dir / "estimates.json"),
            references=_load_json(data_dir / "reference_values.json"),
            tags=_load_json(data_dir / "menu_tags.json"),
        )

    def usable_estimate(self, menu_id: str) -> dict[str, Any] | None:
        """信頼度が high / medium の推定値だけを返す。それ以外は None。"""
        estimate = self.estimates.get(menu_id)
        if estimate is None or estimate["confidence"] not in USABLE_CONFIDENCE:
            return None
        return estimate

    @property
    def per_meal_ratio(self) -> float:
        """1日の目標量から1食分を出すための割合（3食なら 1/3）。"""
        return 1 / self.references["meals_per_day"]


# ---------------------------------------------------------------------------
# 小さな計算用の関数
# ---------------------------------------------------------------------------


def sum_official(data: NutritionData, menu_ids: list[str], nutrient: str) -> float | None:
    """公式値の合計。1品でも値が無ければ None。"""
    total = 0.0
    for menu_id in menu_ids:
        value = data.items[menu_id]["nutrition"][nutrient]["value"]
        if value is None:
            return None
        total += value
    return total


def sum_estimated(data: NutritionData, menu_ids: list[str], nutrient: str) -> tuple[float | None, list[str]]:
    """推定値の合計と、値が足りなかった料理名の一覧を返す。

    使える推定値が無い品、または値が算出不可の品が1つでもあれば、合計は None。
    （足りない分を0として足すと、実際より少なく見えてしまうため）
    """
    total = 0.0
    missing: list[str] = []
    for menu_id in menu_ids:
        estimate = data.usable_estimate(menu_id)
        value = None if estimate is None else estimate["nutrients"][nutrient]["value"]
        if value is None:
            missing.append(data.items[menu_id]["name"])
        else:
            total += value
    return (None if missing else total), missing


def missing_note(nutrient_label: str, missing: list[str]) -> str:
    return f"{nutrient_label}は、推定値が無い料理（{'、'.join(missing)}）を含むため合計を出していません。"


def collect_tags(data: NutritionData, menu_ids: list[str]) -> dict[str, list[str]]:
    """タグ → そのタグを持つ料理名の一覧。"""
    result: dict[str, list[str]] = {}
    for menu_id in menu_ids:
        for tag in data.tags.get(menu_id, []):
            result.setdefault(tag, []).append(data.items[menu_id]["name"])
    return result


def level_by_ratio(ratio: float) -> str:
    """目安に対する割合から、「少ないほど良い」項目の判定を決める。"""
    if ratio <= 1.0:
        return WITHIN
    if ratio <= 1.5:
        return SLIGHTLY_HIGH
    return HIGH


SATURATED_FAT_HIGH_PCT = 10  # これを超えたら「多め」（目標の7%〜10%は「やや多め」）


def saturated_fat_level(energy_pct: float, target_pct: float) -> str:
    """飽和脂肪酸のエネルギー比（%）の判定。"""
    if energy_pct <= target_pct:
        return WITHIN
    if energy_pct <= SATURATED_FAT_HIGH_PCT:
        return SLIGHTLY_HIGH
    return HIGH


# ---------------------------------------------------------------------------
# 観点ごとの評価
# ---------------------------------------------------------------------------


def evaluate_salt(data: NutritionData, menu_ids: list[str]) -> PerspectiveResult:
    ref = data.references["salt"]
    total = sum_official(data, menu_ids, "salt")
    if total is None:
        return PerspectiveResult("塩分", NOT_EVALUATED, notes=["公式の食塩相当量が不明な料理があります。"])

    per_meal = ref["daily_target_g"] * data.per_meal_ratio
    return PerspectiveResult(
        name="塩分",
        level=level_by_ratio(total / per_meal),
        values={
            "salt_g": round(total, 1),
            "per_meal_guide_g": round(per_meal, 1),
            "daily_target_g": ref["daily_target_g"],
            "daily_pct": round(total / ref["daily_target_g"] * 100),
        },
    )


def evaluate_ldl(data: NutritionData, menu_ids: list[str]) -> PerspectiveResult:
    refs = data.references
    result = PerspectiveResult(name="LDLコレステロール", level=None)

    # 飽和脂肪酸：エネルギーに対する割合（%）＝ 飽和脂肪酸g × 9kcal ÷ 総kcal × 100
    kcal = sum_official(data, menu_ids, "calories")
    sat, sat_missing = sum_estimated(data, menu_ids, "saturated_fat")
    if sat is not None and kcal:
        sat_pct = sat * 9 / kcal * 100
        target = refs["saturated_fat"]["energy_pct_target"]
        result.values["saturated_fat_g"] = round(sat, 1)
        result.values["saturated_fat_energy_pct"] = round(sat_pct, 1)
        result.values["saturated_fat_level"] = saturated_fat_level(sat_pct, target)
    else:
        result.values["saturated_fat_level"] = NOT_EVALUATED
        result.notes.append(missing_note("飽和脂肪酸", sat_missing))

    # 食物繊維：多いほど良い項目
    fiber, fiber_missing = sum_estimated(data, menu_ids, "dietary_fiber")
    if fiber is not None:
        per_meal = refs["dietary_fiber"]["daily_target_g"] * data.per_meal_ratio
        ratio = fiber / per_meal
        result.values["dietary_fiber_g"] = round(fiber, 1)
        result.values["dietary_fiber_per_meal_guide_g"] = round(per_meal, 1)
        result.values["dietary_fiber_level"] = ENOUGH if ratio >= 1.0 else MODERATE if ratio >= 0.5 else LOW
    else:
        result.values["dietary_fiber_level"] = NOT_EVALUATED
        result.notes.append(missing_note("食物繊維", fiber_missing))

    # コレステロール：参考表示のみ（判定しない）
    chol, _ = sum_estimated(data, menu_ids, "cholesterol")
    if chol is not None:
        result.values["cholesterol_mg"] = round(chol)
        result.values["cholesterol_daily_reference_pct"] = round(chol / refs["cholesterol"]["daily_reference_mg"] * 100)

    # タグからの気づき
    tags = collect_tags(data, menu_ids)
    for tag in GOOD_TAGS_FOR_LDL:
        if tag in tags:
            result.points.append(f"{tag}を含む料理があります（{'、'.join(tags[tag])}）。")
    for tag in CAUTION_TAGS_FOR_LDL:
        if tag in tags:
            result.points.append(f"{tag}が特徴の料理があります（{'、'.join(tags[tag])}）。")

    # LDL全体の判定：飽和脂肪酸の判定を代表とする（無ければ評価できない）
    result.level = result.values["saturated_fat_level"]
    return result


def evaluate_triglyceride(data: NutritionData, menu_ids: list[str]) -> PerspectiveResult:
    # 必要なエネルギー量は人によって大きく違うため、判定（level）はしない
    result = PerspectiveResult(name="中性脂肪", level=None)

    kcal = sum_official(data, menu_ids, "calories")
    if kcal is not None:
        result.values["calories_kcal"] = round(kcal)
    else:
        result.notes.append("公式のエネルギーが不明な料理があります。")

    carb, carb_missing = sum_estimated(data, menu_ids, "carbohydrate")
    if carb is not None and kcal:
        result.values["carbohydrate_g"] = round(carb)
        result.values["carbohydrate_energy_pct"] = round(carb * 4 / kcal * 100)
    elif carb_missing:
        result.notes.append(missing_note("炭水化物", carb_missing))

    tags = collect_tags(data, menu_ids)
    if len(tags.get("主食", [])) >= 2:
        result.points.append(f"主食が重なっています（{'、'.join(tags['主食'])}）。")
    if "揚げ物" in tags:
        result.points.append(f"揚げ物があります（{'、'.join(tags['揚げ物'])}）。")
    if "デザート" in tags:
        result.points.append(f"デザートがあります（{'、'.join(tags['デザート'])}）。")
    return result


# ---------------------------------------------------------------------------
# 窓口
# ---------------------------------------------------------------------------


def evaluate_meal(data: NutritionData, menu_ids: list[str]) -> MealEvaluation:
    if not menu_ids:
        raise ValueError("メニューが1つも指定されていません")
    unknown = [m for m in menu_ids if m not in data.items]
    if unknown:
        raise KeyError(f"menu.json に無いメニューです: {unknown}")

    return MealEvaluation(
        menu_ids=list(menu_ids),
        names=[data.items[m]["name"] for m in menu_ids],
        salt=evaluate_salt(data, menu_ids),
        ldl=evaluate_ldl(data, menu_ids),
        triglyceride=evaluate_triglyceride(data, menu_ids),
        missing_estimates=[data.items[m]["name"] for m in menu_ids if data.usable_estimate(m) is None],
        untagged=[data.items[m]["name"] for m in menu_ids if m not in data.tags],
    )


def format_for_console(ev: MealEvaluation) -> str:
    """確認用の簡単な表示（投稿文の作成は Phase 4）。"""
    lines = [f"■ 組み合わせ: {' + '.join(ev.names)}"]
    for p in (ev.salt, ev.ldl, ev.triglyceride):
        lines.append(f"【{p.name}】判定: {p.level or '（判定なし）'}")
        for key, value in p.values.items():
            lines.append(f"    {key}: {value}")
        lines.extend(f"    ・{x}" for x in p.points)
        lines.extend(f"    ※{x}" for x in p.notes)
    if ev.missing_estimates:
        lines.append(f"※推定値（信頼度 中以上）が無い料理: {'、'.join(ev.missing_estimates)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    menu_ids = sys.argv[1:] if argv is None else argv
    try:
        data = NutritionData.load()
        print(format_for_console(evaluate_meal(data, menu_ids)))
    except (KeyError, ValueError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
