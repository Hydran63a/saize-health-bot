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
from modules.nutrition import HIGH, MealEvaluation, NutritionData, evaluate_meal
from modules.pricing import PriceTable

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
    total_price_yen: int | None = None  # 税込の合計（価格表を使ったときだけ）
    budget_yen: int | None = None  # 予算の上限（指定したときだけ）


class BudgetTooLowError(ValueError):
    """予算以内の組み合わせが1つも無いとき。"""


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


def classify(data: NutritionData, available: set[str] | None = None) -> Slots:
    """料理を献立の枠に分ける。available を渡すと、その中の料理だけを使う。"""
    staples, salads, mains, soups = [], [], [], []
    for menu_id, tags in data.tags.items():
        if "デザート" in tags:
            continue
        if available is not None and menu_id not in available:
            continue  # 例：価格表に無い（今のグランドメニューに載っていない）料理
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


def build_reasons(data: NutritionData, combo: list[str]) -> list[str]:
    """組み合わせの理由（なぜバランスがよいか）。数値の説明は post_text.py が担当する。"""
    name = lambda m: data.items[m]["name"]  # noqa: E731
    slots = classify(data)
    reasons = ["主食は1品で、炭水化物が重なりません"]

    for salad in (m for m in combo if m in slots.salads):
        contents = [t for t in SALAD_CONTENT_TAGS if t in data.tags[salad]]
        reasons.append(f"{name(salad)}で{'・'.join(contents)}をプラス")
    for main in (m for m in combo if m in slots.mains):
        reasons.append(f"{name(main)}でたんぱく質をプラス")
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
    prices: PriceTable | None = None,
    budget_yen: int | None = None,
) -> Recommendation:
    """おすすめを1つ選ぶ。

    prices を渡すと、価格表に載っている（＝今のグランドメニューにある）料理だけを使う。
    budget_yen を渡すと、税込合計がその金額以内の組み合わせだけから選ぶ。
    """
    rng = rng or random.Random()
    if budget_yen is not None and prices is None:
        raise ValueError("予算で絞り込むには価格表（prices）が必要です")
    slots = classify(data, available=set(prices.items) if prices else None)
    candidates = build_candidates(data, slots)

    # 予算での絞り込みは、栄養の評価より先に行う（評価する数が減って速くなる）
    if budget_yen is not None:
        candidates = [c for c in candidates if prices.total(c).tax_included_yen <= budget_yen]
        if not candidates:
            raise BudgetTooLowError(f"税込{budget_yen}円以内の組み合わせがありません")

    evaluated = [(c, evaluate_meal(data, c)) for c in candidates]
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
        reasons=build_reasons(data, combo),
        relaxed_rules=relaxed,
        total_price_yen=prices.total(combo).tax_included_yen if prices else None,
        budget_yen=budget_yen,
    )


def record(history: HistoryStore, rec: Recommendation, now: datetime, slot: str | None) -> None:
    history.add(menu_ids=rec.menu_ids, staple_id=rec.staple_id, pattern=rec.pattern, slot=slot, created_at=now)


def main(argv: list[str] | None = None) -> int:
    # post_text は recommendation を import しているので、ここで import する
    # （ファイルの先頭で import し合うと「循環 import」になって読み込めないため）
    from config import parse_post_times
    from modules.post_text import build_post_text

    parser = argparse.ArgumentParser(description="おすすめ献立を提案し、投稿文を表示します（確認用・投稿はしない）")
    parser.add_argument("--slot", default=None, help="投稿枠（例 11:00）。書き出しの文が変わる")
    parser.add_argument("--record", action="store_true", help="提案を履歴に記録する")
    parser.add_argument("--simulate", type=int, default=0, help="使い捨ての履歴で N 回連続提案する")
    parser.add_argument("--seed", type=int, default=None, help="乱数の種（同じ結果を再現したいとき）")
    parser.add_argument("--budget", type=int, default=None,
                        help="予算（税込・円）。省略時は .env の POST_BUDGET_YEN、0 で上限なし")
    args = parser.parse_args(argv)

    settings = load_settings()
    data = NutritionData.load()
    prices = PriceTable.load()
    rng = random.Random(args.seed)
    now = datetime.now(ZoneInfo(settings.timezone))
    budget = settings.post_budget_yen if args.budget is None else (args.budget or None)
    kwargs = {
        "combo_days": settings.no_repeat_combo_days,
        "staple_days": settings.no_repeat_staple_days,
        "rng": rng,
        "prices": prices,
        "budget_yen": budget,
    }

    try:
        if args.simulate:
            # 本物の履歴を汚さないよう、メモリ上の履歴で「半日ごとに1回」（≒1日2回）投稿した想定で試す
            with HistoryStore(":memory:") as history:
                for i in range(args.simulate):
                    moment = now + timedelta(hours=12 * i)
                    rec = recommend(data, history, moment, **kwargs)
                    record(history, rec, moment, slot=None)
                    note = f" （{'／'.join(rec.relaxed_rules)}）" if rec.relaxed_rules else ""
                    print(f"{i + 1:>2}回目: {' ＋ '.join(rec.names)}  {rec.total_price_yen}円{note}")
            return 0
    except BudgetTooLowError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1

    slot = parse_post_times(args.slot)[0] if args.slot else None
    # 記録しないときに履歴ファイルがまだ無ければ、空のファイルを作らないようメモリ上で済ませる
    db_path = settings.db_path if (args.record or settings.db_path.exists()) else ":memory:"
    with HistoryStore(db_path) as history:
        try:
            rec = recommend(data, history, now, **kwargs)
        except BudgetTooLowError as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            return 1
        text = build_post_text(rec, data, slot=slot, max_chars=settings.post_max_chars,
                               hashtags=settings.post_hashtags, prices=prices)
        print(text)
        print(f"\n（{len(text)}文字）")
        for note in rec.relaxed_rules:
            print(f"（{note}）")
        if args.record:
            record(history, rec, now, slot=args.slot)
            print(f"（履歴に記録しました：{settings.db_path.name}、記録数 {history.count()}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
