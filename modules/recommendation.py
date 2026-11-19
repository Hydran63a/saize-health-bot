"""おすすめ献立の生成（Phase 4）。

流れ:
  ① 候補づくり   献立の型（主食1・サラダ1・主菜0〜1・汁物0〜1）に合う組み合わせを全部作る
  ② ふるい分け   塩分・飽和脂肪酸が「多め」の組み合わせを外す
  ③ 重複チェック 最近おすすめした組み合わせ（30日）・主食（3日）を外す
  ④ 選ぶ         残った候補からランダムに1つ
  ⑤ 理由をつける Phase 3 の評価結果から、おすすめの根拠を作る
  ⑥ 履歴に記録   実際に投稿したときだけ（確認用には --record）

使い方（確認用）:
    python -m modules.recommendation                 # 1つ提案（履歴は見るが記録しない）
    python -m modules.recommendation --record        # 提案して履歴に記録
    python -m modules.recommendation --simulate 10   # 10回分を連続で提案（使い捨ての履歴で）
"""

from __future__ import annotations

import argparse
import itertools
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import load_settings
from database.database import HistoryStore, make_combo_key
from modules.nutrition import HIGH, NOT_EVALUATED, MealEvaluation, NutritionData, evaluate_meal

PATTERN_BALANCE = "balance"

# サラダ枠に入れる料理：「副菜」で、野菜・豆・海藻のどれかを含むもの
SALAD_CONTENT_TAGS = ("野菜", "豆", "海藻")
# 主食に「具」があるかを見るタグ。どれも無い主食（ライス・フォッカチオなど）には主菜を必ず付ける
STAPLE_FILLING_TAGS = ("肉", "魚介", "卵", "加工肉", "チーズ・クリーム・バター")
MAX_ITEMS = 4
# 1食に1品までにするタグ（推定値が無くても、飽和脂肪酸の多い料理が重ならないように）
AT_MOST_ONE_TAGS = ("チーズ・クリーム・バター", "加工肉")


# ---------------------------------------------------------------------------
# 結果の型
# ---------------------------------------------------------------------------


@dataclass
class Recommendation:
    menu_ids: list[str]
    names: list[str]
    staple_id: str
    pattern: str
    evaluation: MealEvaluation
    reasons: list[str]
    relaxed_rules: list[str] = field(default_factory=list)  # 候補不足で緩めたルール


@dataclass
class Slots:
    """献立の枠ごとに使える料理のID。"""

    staples: list[str]
    salads: list[str]
    mains: list[str]
    soups: list[str]


# ---------------------------------------------------------------------------
# ① 候補づくり
# ---------------------------------------------------------------------------


def classify(data: NutritionData) -> Slots:
    staples, salads, mains, soups = [], [], [], []
    for menu_id, tags in data.tags.items():
        if "デザート" in tags:
            continue
        if "主食" in tags:
            staples.append(menu_id)
        elif "副菜" in tags and any(t in tags for t in SALAD_CONTENT_TAGS):
            salads.append(menu_id)
        elif "主菜" in tags:
            mains.append(menu_id)
        elif "汁物" in tags:
            soups.append(menu_id)
    return Slots(sorted(staples), sorted(salads), sorted(mains), sorted(soups))


def staple_needs_main(data: NutritionData, staple_id: str) -> bool:
    """具の無い主食（ライス・フォッカチオ・ペペロンチーノなど）なら主菜が必要。"""
    return not any(t in data.tags[staple_id] for t in STAPLE_FILLING_TAGS)


def build_candidates(data: NutritionData, slots: Slots) -> list[list[str]]:
    candidates: list[list[str]] = []
    for staple, salad in itertools.product(slots.staples, slots.salads):
        main_options: list[str | None] = list(slots.mains)
        if not staple_needs_main(data, staple):
            main_options.append(None)  # 主菜なしも可
        for main, soup in itertools.product(main_options, [None, *slots.soups]):
            combo = [x for x in (staple, salad, main, soup) if x is not None]
            if len(combo) <= MAX_ITEMS:
                candidates.append(combo)
    return candidates


# ---------------------------------------------------------------------------
# ② ふるい分け
# ---------------------------------------------------------------------------


def is_balanced(data: NutritionData, combo: list[str], ev: MealEvaluation) -> bool:
    """バランスのよい組み合わせか。

    - 塩分・飽和脂肪酸が「多め」でない（評価できない項目は除外理由にしない）
    - チーズ・クリーム・バター／加工肉 の料理がそれぞれ1品まで（数値が無い料理でも効くルール）
    """
    if ev.salt.level == HIGH or ev.ldl.level == HIGH:
        return False
    for tag in AT_MOST_ONE_TAGS:
        if sum(tag in data.tags.get(m, []) for m in combo) > 1:
            return False
    return True


# ---------------------------------------------------------------------------
# ③ 重複チェック
# ---------------------------------------------------------------------------


def remove_recent(
    candidates: list[tuple[list[str], MealEvaluation]],
    history: HistoryStore,
    now: datetime,
    combo_days: int,
    staple_days: int,
) -> tuple[list[tuple[list[str], MealEvaluation]], list[str]]:
    """最近おすすめしたものを外す。全部消えてしまうときは、ルールを順に緩める。"""
    recent_combos = history.combo_keys_since(now - timedelta(days=combo_days))
    recent_staples = history.staples_since(now - timedelta(days=staple_days))

    def keep(c: list[str], check_combo: bool, check_staple: bool) -> bool:
        if check_combo and make_combo_key(c) in recent_combos:
            return False
        if check_staple and c[0] in recent_staples:  # c[0] は必ず主食
            return False
        return True

    # 緩める順番：まず主食のルール、次に組み合わせのルール
    steps = [
        (True, True, []),
        (True, False, [f"主食の重複防止（{staple_days}日）を緩めました"]),
        (False, False, [f"主食の重複防止（{staple_days}日）と組み合わせの重複防止（{combo_days}日）を緩めました"]),
    ]
    for check_combo, check_staple, relaxed in steps:
        remaining = [(c, ev) for c, ev in candidates if keep(c, check_combo, check_staple)]
        if remaining:
            return remaining, relaxed
    return [], []


# ---------------------------------------------------------------------------
# ⑤ 理由をつける
# ---------------------------------------------------------------------------


def build_reasons(data: NutritionData, combo: list[str], ev: MealEvaluation) -> list[str]:
    name = lambda m: data.items[m]["name"]  # noqa: E731
    slots = classify(data)
    reasons = [f"主食は1品（{name(combo[0])}）で、炭水化物が重なりません。"]

    salads = [m for m in combo if m in slots.salads]
    if salads:
        reasons.append(f"{'・'.join(name(m) for m in salads)}で野菜などをプラスしています。")
    mains = [m for m in combo if m in slots.mains]
    if mains:
        reasons.append(f"{'・'.join(name(m) for m in mains)}でたんぱく質をプラスしています。")

    s = ev.salt.values
    if s:
        reasons.append(
            f"塩分は合計{s['salt_g']}g（1日の目標量{s['daily_target_g']}g未満の約{s['daily_pct']}%・公式値）。"
        )
    ldl = ev.ldl.values
    if ldl.get("saturated_fat_level") not in (None, NOT_EVALUATED):
        reasons.append(f"飽和脂肪酸はエネルギーの約{ldl['saturated_fat_energy_pct']}%（目標7%以下・推定値）。")
    if ldl.get("dietary_fiber_level") not in (None, NOT_EVALUATED):
        reasons.append(f"食物繊維は約{ldl['dietary_fiber_g']}g（推定値）。")
    return reasons


# ---------------------------------------------------------------------------
# 窓口
# ---------------------------------------------------------------------------


def recommend(
    data: NutritionData,
    history: HistoryStore,
    now: datetime,
    *,
    combo_days: int,
    staple_days: int,
    rng: random.Random | None = None,
) -> Recommendation:
    rng = rng or random.Random()
    slots = classify(data)

    evaluated = [(c, evaluate_meal(data, c)) for c in build_candidates(data, slots)]
    if not evaluated:
        raise ValueError("献立の型に合う組み合わせがありません（タグを確認してください）")

    balanced = [(c, ev) for c, ev in evaluated if is_balanced(data, c, ev)]
    relaxed: list[str] = []
    if not balanced:
        balanced = evaluated
        relaxed.append("塩分・飽和脂肪酸のふるい分けを満たす組み合わせが無いため、外しました")

    remaining, relaxed_dup = remove_recent(balanced, history, now, combo_days, staple_days)
    relaxed += relaxed_dup
    combo, ev = rng.choice(remaining)

    return Recommendation(
        menu_ids=combo,
        names=[data.items[m]["name"] for m in combo],
        staple_id=combo[0],
        pattern=PATTERN_BALANCE,
        evaluation=ev,
        reasons=build_reasons(data, combo, ev),
        relaxed_rules=relaxed,
    )


def record(history: HistoryStore, rec: Recommendation, now: datetime, slot: str | None) -> None:
    history.add(menu_ids=rec.menu_ids, staple_id=rec.staple_id, pattern=rec.pattern, slot=slot, created_at=now)


def format_for_console(rec: Recommendation) -> str:
    lines = [f"■ おすすめ: {' ＋ '.join(rec.names)}"]
    lines += [f"  ・{r}" for r in rec.reasons]
    lines += [f"  ※{n}" for n in rec.evaluation.ldl.notes]
    lines += [f"  （{r}）" for r in rec.relaxed_rules]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="おすすめ献立を提案します（確認用）")
    parser.add_argument("--slot", default=None, help="投稿枠（例 11:00）")
    parser.add_argument("--record", action="store_true", help="提案を履歴に記録する")
    parser.add_argument("--simulate", type=int, default=0, help="使い捨ての履歴で N 回連続提案する")
    parser.add_argument("--seed", type=int, default=None, help="乱数の種（同じ結果を再現したいとき）")
    args = parser.parse_args(argv)

    settings = load_settings()
    data = NutritionData.load()
    rng = random.Random(args.seed)
    now = datetime.now(ZoneInfo(settings.timezone))
    kwargs = {"combo_days": settings.no_repeat_combo_days, "staple_days": settings.no_repeat_staple_days, "rng": rng}

    if args.simulate:
        # 本物の履歴を汚さないよう、メモリ上の履歴で「半日ごとに1回」（≒1日2回）投稿した想定で試す
        with HistoryStore(":memory:") as history:
            for i in range(args.simulate):
                moment = now + timedelta(hours=12 * i)
                rec = recommend(data, history, moment, **kwargs)
                record(history, rec, moment, slot=None)
                note = f" （{'／'.join(rec.relaxed_rules)}）" if rec.relaxed_rules else ""
                print(f"{i + 1:>2}回目: {' ＋ '.join(rec.names)}{note}")
        return 0

    with HistoryStore(settings.db_path) as history:
        rec = recommend(data, history, now, **kwargs)
        print(format_for_console(rec))
        if args.record:
            record(history, rec, now, slot=args.slot)
            print(f"（履歴に記録しました：{settings.db_path.name}、記録数 {history.count()}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
