import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from database.database import HistoryStore
from modules import nutrition as nu
from modules import recommendation as rc

JST = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 11, 19, 11, 0, tzinfo=JST)

REFERENCES = {
    "meals_per_day": 3,
    "salt": {"daily_target_g": 6.0},  # 1食の目安 2.0g → 3.0g を超えると「多め」
    "saturated_fat": {"energy_pct_target": 7},
    "dietary_fiber": {"daily_target_g": 18},
    "cholesterol": {"daily_reference_mg": 200},
}


def item(menu_id, kcal, salt):
    return {"id": menu_id, "name": menu_id, "nutrition": {"calories": {"value": kcal}, "salt": {"value": salt}}}


def make_data(extra_items=(), extra_tags=None):
    items = [
        item("パスタA", 500, 1.5),   # 具あり主食
        item("パスタB", 500, 1.5),   # 具あり主食
        item("ライス", 300, 0.0),    # 具なし主食 → 主菜が必要
        item("サラダ", 100, 0.5),
        item("チキン", 300, 1.0),
        item("スープ", 100, 1.0),
        item("ケーキ", 200, 0.1),
        *extra_items,
    ]
    tags = {
        "パスタA": ["主食", "肉"],
        "パスタB": ["主食", "魚介"],
        "ライス": ["主食"],
        "サラダ": ["副菜", "野菜"],
        "チキン": ["主菜", "肉"],
        "スープ": ["汁物"],
        "ケーキ": ["デザート"],
        **(extra_tags or {}),
    }
    return nu.NutritionData({"items": items}, {"items": []}, REFERENCES, {"items": tags})


# ---------------------------------------------------------------------------
# ① 候補づくり
# ---------------------------------------------------------------------------


def test_classify_slots():
    slots = rc.classify(make_data())
    assert slots.staples == ["パスタA", "パスタB", "ライス"]
    assert slots.salads == ["サラダ"]
    assert slots.mains == ["チキン"]
    assert slots.soups == ["スープ"]  # デザートはどの枠にも入らない


def test_every_candidate_has_one_staple_and_a_salad():
    data = make_data()
    slots = rc.classify(data)
    for combo in rc.build_candidates(data, slots):
        assert sum(m in slots.staples for m in combo) == 1  # 主食はちょうど1品
        assert any(m in slots.salads for m in combo)  # サラダが必ずある
        assert "ケーキ" not in combo
        assert len(combo) <= rc.MAX_ITEMS


def test_plain_staple_always_gets_a_main():
    data = make_data()
    combos = rc.build_candidates(data, rc.classify(data))
    rice_combos = [c for c in combos if c[0] == "ライス"]
    assert rice_combos and all("チキン" in c for c in rice_combos)
    assert any("チキン" not in c for c in combos if c[0] == "パスタA")  # 具ありなら主菜なしも可


# ---------------------------------------------------------------------------
# ② ふるい分け
# ---------------------------------------------------------------------------


def test_high_salt_combos_are_filtered():
    data = make_data()
    # パスタA(1.5)+サラダ(0.5)+チキン(1.0)+スープ(1.0) = 4.0g → 2.0倍で「多め」
    combo = ["パスタA", "サラダ", "チキン", "スープ"]
    assert not rc.is_balanced(data, combo, nu.evaluate_meal(data, combo))
    ok = ["パスタA", "サラダ"]
    assert rc.is_balanced(data, ok, nu.evaluate_meal(data, ok))


def test_two_cheese_dishes_are_filtered_even_without_estimates():
    data = make_data(
        extra_items=[item("ピザ", 500, 1.0), item("カプレーゼ", 200, 0.3)],
        extra_tags={"ピザ": ["主食", "チーズ・クリーム・バター"], "カプレーゼ": ["副菜", "野菜", "チーズ・クリーム・バター"]},
    )
    combo = ["ピザ", "カプレーゼ"]  # 塩分は目安以内だが、チーズの料理が2品
    ev = nu.evaluate_meal(data, combo)
    assert ev.salt.level == nu.WITHIN
    assert not rc.is_balanced(data, combo, ev)
    assert rc.is_balanced(data, ["ピザ", "サラダ"], nu.evaluate_meal(data, ["ピザ", "サラダ"]))


def test_sweet_bread_is_not_a_staple():
    data = make_data(extra_items=[item("シナモンパン", 300, 0.5)], extra_tags={"シナモンパン": ["主食", "デザート"]})
    assert "シナモンパン" not in rc.classify(data).staples


# ---------------------------------------------------------------------------
# ③ 重複チェック
# ---------------------------------------------------------------------------


def test_recent_combo_and_staple_are_avoided():
    data = make_data()
    with HistoryStore(":memory:") as h:
        # 2日前にパスタA＋サラダ を記録 → パスタA は主食3日ルールで出ない
        h.add(menu_ids=["パスタA", "サラダ"], staple_id="パスタA", pattern="balance", slot=None,
              created_at=NOW - timedelta(days=2))
        for seed in range(20):
            rec = rc.recommend(data, h, NOW, combo_days=30, staple_days=3, rng=random.Random(seed))
            assert rec.staple_id != "パスタA"
            assert rec.relaxed_rules == []


def test_recent_main_is_avoided():
    data = make_data(extra_items=[item("ビーフ", 400, 1.0)], extra_tags={"ビーフ": ["主菜", "肉"]})
    with HistoryStore(":memory:") as h:
        # 1日前に「チキン」を主菜として出した → 主菜2日ルールで、ライスの献立は ビーフ になる
        h.add(menu_ids=["ライス", "サラダ", "チキン"], staple_id="ライス", main_id="チキン", pattern="balance",
              slot=None, created_at=NOW - timedelta(days=1))
        for seed in range(20):
            rec = rc.recommend(data, h, NOW, combo_days=30, staple_days=0, main_days=2, rng=random.Random(seed))
            assert rec.main_id != "チキン"
            assert rec.relaxed_rules == []


def test_main_id_is_set_only_when_combo_has_main():
    data = make_data()
    with HistoryStore(":memory:") as h:
        for seed in range(20):
            rec = rc.recommend(data, h, NOW, combo_days=30, staple_days=3, main_days=2, rng=random.Random(seed))
            assert (rec.main_id == "チキン") == ("チキン" in rec.menu_ids)


def test_rules_are_relaxed_when_everything_is_recent():
    data = make_data()
    with HistoryStore(":memory:") as h:
        for staple in ("パスタA", "パスタB", "ライス"):  # 主食を全部、昨日使ったことにする
            h.add(menu_ids=[staple], staple_id=staple, pattern="balance", slot=None,
                  created_at=NOW - timedelta(days=1))
        rec = rc.recommend(data, h, NOW, combo_days=30, staple_days=3, rng=random.Random(0))
        assert any("主食の重複防止" in r for r in rec.relaxed_rules)


def test_no_repeat_over_many_posts():
    """記録しながら連続で提案しても、30日以内に同じ組み合わせが出ない。"""
    data = make_data()
    seen = set()
    with HistoryStore(":memory:") as h:
        for i in range(6):
            moment = NOW + timedelta(hours=12 * i)
            rec = rc.recommend(data, h, moment, combo_days=30, staple_days=0, rng=random.Random(i))
            key = "|".join(sorted(rec.menu_ids))
            if not rec.relaxed_rules:
                assert key not in seen
            seen.add(key)
            rc.record(h, rec, moment, slot=None)


# ---------------------------------------------------------------------------
# ⑤ 理由
# ---------------------------------------------------------------------------


def test_reasons_explain_the_combination_only():
    data = make_data()
    reasons = rc.build_reasons(data, ["ライス", "サラダ", "チキン"])
    assert reasons == [
        "主食は1品で、炭水化物が重なりません",
        "サラダで野菜をプラス",
        "チキンでたんぱく質をプラス",
    ]
    # 数値（塩分など）は post_text.py の担当なので、理由には入れない
    assert not any("g" in r or "%" in r for r in reasons)


# ---------------------------------------------------------------------------
# 価格・予算
# ---------------------------------------------------------------------------


def price_table(prices: dict[str, int]):
    from modules.pricing import PriceTable

    return PriceTable({"items": {k: {"price_yen": v, "price_tax_included_yen": v, "tax_rate": 0.10}
                                 for k, v in prices.items()}})


ALL_PRICES = {"パスタA": 500, "パスタB": 300, "ライス": 150, "サラダ": 300, "チキン": 300, "スープ": 150}


def test_items_without_price_are_not_recommended():
    data = make_data()
    prices = price_table({k: v for k, v in ALL_PRICES.items() if k != "パスタA"})  # パスタA は価格なし
    with HistoryStore(":memory:") as h:
        for seed in range(20):
            rec = rc.recommend(data, h, NOW, combo_days=30, staple_days=3, rng=random.Random(seed), prices=prices)
            assert "パスタA" not in rec.menu_ids
            assert rec.total_price_yen is not None


def test_budget_limits_total_price():
    data = make_data()
    prices = price_table(ALL_PRICES)
    with HistoryStore(":memory:") as h:
        for seed in range(20):
            rec = rc.recommend(data, h, NOW, combo_days=30, staple_days=3, rng=random.Random(seed),
                               prices=prices, budget_yen=600)
            assert rec.total_price_yen <= 600
            assert rec.budget_yen == 600


def test_budget_too_low_raises_clear_error():
    data = make_data()
    with HistoryStore(":memory:") as h, pytest.raises(rc.BudgetTooLowError):
        rc.recommend(data, h, NOW, combo_days=30, staple_days=3, prices=price_table(ALL_PRICES), budget_yen=100)


def test_budget_without_price_table_is_rejected():
    with HistoryStore(":memory:") as h, pytest.raises(ValueError):
        rc.recommend(make_data(), h, NOW, combo_days=30, staple_days=3, budget_yen=1000)


def test_real_data_within_1000_yen():
    from modules.pricing import PriceTable

    data = nu.NutritionData.load()
    prices = PriceTable.load()
    with HistoryStore(":memory:") as h:
        for seed in range(10):
            rec = rc.recommend(data, h, NOW, combo_days=30, staple_days=3, rng=random.Random(seed),
                               prices=prices, budget_yen=1000)
            assert rec.total_price_yen <= 1000
            assert all(prices.has_price(m) for m in rec.menu_ids)


def test_same_seed_gives_same_result():
    data = make_data()
    with HistoryStore(":memory:") as h:
        a = rc.recommend(data, h, NOW, combo_days=30, staple_days=3, rng=random.Random(42))
        b = rc.recommend(data, h, NOW, combo_days=30, staple_days=3, rng=random.Random(42))
    assert a.menu_ids == b.menu_ids


def test_real_data_produces_balanced_recommendation():
    data = nu.NutritionData.load()
    with HistoryStore(":memory:") as h:
        rec = rc.recommend(data, h, NOW, combo_days=30, staple_days=3, rng=random.Random(0))
    slots = rc.classify(data)
    assert sum(m in slots.staples for m in rec.menu_ids) == 1
    assert any(m in slots.salads for m in rec.menu_ids)
    assert rec.relaxed_rules == []


@pytest.mark.parametrize("bad", [[], ["存在しない"]])
def test_evaluate_rejects_bad_input(bad):
    with pytest.raises((KeyError, ValueError)):
        nu.evaluate_meal(make_data(), bad)
