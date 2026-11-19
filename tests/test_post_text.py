import random
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from database.database import HistoryStore
from modules import nutrition as nu
from modules import post_text as pt
from modules import recommendation as rc

NOW = datetime(2026, 11, 19, 11, 0, tzinfo=ZoneInfo("Asia/Tokyo"))

REFERENCES = {
    "meals_per_day": 3,
    "salt": {"daily_target_g": 6.0},  # 1食の目安 2.0g
    "saturated_fat": {"energy_pct_target": 7},
    "dietary_fiber": {"daily_target_g": 18},  # 1食の目安 6.0g
    "cholesterol": {"daily_reference_mg": 200},
}


def item(menu_id, kcal, salt):
    return {"id": menu_id, "name": menu_id, "nutrition": {"calories": {"value": kcal}, "salt": {"value": salt}}}


def est(menu_id, sat, fiber):
    n = lambda v: {"value": v, "status": "estimated"}  # noqa: E731
    return {"menu_id": menu_id, "confidence": "medium",
            "nutrients": {"saturated_fat": n(sat), "dietary_fiber": n(fiber), "cholesterol": n(0), "carbohydrate": n(0)}}


def make_data(with_estimates=True, salad_salt=0.5):
    menu = {
        "source": {"official_updated_at": "2099-01-01"},
        "items": [item("パスタ", 500, 1.0), item("サラダ", 100, salad_salt), item("スープ", 100, 1.0)],
    }
    # 飽和脂肪酸はパスタ+サラダで3.0g → 3.0×9÷600×100 = ちょうど4.5%（丸めの境目を避けた値）
    estimates = {"items": [est("パスタ", 2.7, 4.0), est("サラダ", 0.3, 3.0), est("スープ", 0.5, 0.5)]} if with_estimates else {"items": []}
    tags = {"items": {"パスタ": ["主食", "肉"], "サラダ": ["副菜", "野菜"], "スープ": ["汁物"]}}
    return nu.NutritionData(menu, estimates, REFERENCES, tags)


def make_rec(data, menu_ids):
    ev = nu.evaluate_meal(data, menu_ids)
    return rc.Recommendation(menu_ids=menu_ids, names=menu_ids, staple_id=menu_ids[0], pattern="balance",
                             evaluation=ev, reasons=rc.build_reasons(data, menu_ids))


def build(data, menu_ids, slot=(11, 0), max_chars=1000, hashtags=("#tag",)):
    return pt.build_post_text(make_rec(data, menu_ids), data, slot=slot, max_chars=max_chars, hashtags=hashtags)


# ---------------------------------------------------------------------------


def test_official_and_estimated_values_are_labeled():
    text = build(make_data(), ["パスタ", "サラダ"])
    assert "塩分 1.5g（1日の目標6.0g未満の約25%）［公式］" in text
    assert "エネルギー 600kcal［公式］" in text
    assert "飽和脂肪酸 エネルギーの約4.5%（目標7%以下）［推定］" in text
    assert "食物繊維 約7.0g［推定］" in text
    # 推定値を使ったら、成分表の出典と推定の注意書きが必ず入る
    assert pt.MEXT_CITATION in text
    assert pt.ESTIMATE_NOTE in text


def test_without_estimates_no_mext_citation_and_omission_note():
    text = build(make_data(with_estimates=False), ["パスタ", "サラダ"])
    assert "［推定］" not in text
    assert pt.MEXT_CITATION not in text
    assert "飽和脂肪酸・食物繊維は、推定値がない料理を含むため省略" in text
    assert "（2099-01-01更新）" in text  # 公式データの更新日は常に書く


def test_heading_changes_by_slot():
    data = make_data()
    assert build(data, ["パスタ", "サラダ"], slot=(11, 0)).startswith("🍽 今日のお昼のサイゼ献立")
    assert build(data, ["パスタ", "サラダ"], slot=(15, 0)).startswith("🌆 夕ごはんの参考に")
    assert build(data, ["パスタ", "サラダ"], slot=None).startswith(pt.DEFAULT_HEADING)


def test_tip_for_salty_meal_with_soup():
    text = build(make_data(salad_salt=1.5), ["パスタ", "サラダ", "スープ"])  # 3.5g → 多め
    assert "スープは汁を少し残す" in text


def test_tip_for_salty_meal_without_soup():
    text = build(make_data(salad_salt=1.5), ["パスタ", "サラダ"])  # 2.5g → やや多め
    assert "粉チーズや塩は控えめ" in text


def test_no_prohibition_words():
    for menu_ids in (["パスタ", "サラダ"], ["パスタ", "サラダ", "スープ"]):
        text = build(make_data(salad_salt=1.5), menu_ids)
        for word in ("ダメ", "禁止", "食べてはいけない", "NG"):
            assert word not in text


def test_hashtags_and_disclaimer_are_at_the_end():
    text = build(make_data(), ["パスタ", "サラダ"], hashtags=("#unofficial_saize_health_navi",))
    assert text.endswith("#unofficial_saize_health_navi")
    assert pt.DISCLAIMER in text
    assert "#" not in build(make_data(), ["パスタ", "サラダ"], hashtags=())


def test_shortening_drops_tip_first_then_keeps_footer():
    data = make_data()
    full = build(data, ["パスタ", "サラダ"])
    tip = pt.choose_tip(make_rec(data, ["パスタ", "サラダ"]), data)
    shorter = build(data, ["パスタ", "サラダ"], max_chars=len(full) - 1)
    assert tip not in shorter
    assert pt.DISCLAIMER in shorter and pt.MEXT_CITATION in shorter
    assert len(shorter) <= len(full) - 1


def test_too_long_raises():
    with pytest.raises(pt.PostTooLongError):
        build(make_data(), ["パスタ", "サラダ"], max_chars=50)


def test_real_data_posts_fit_in_500_chars():
    """本物のデータで何回提案しても、500文字に収まる。"""
    data = nu.NutritionData.load()
    with HistoryStore(":memory:") as h:
        for seed in range(30):
            rec = rc.recommend(data, h, NOW, combo_days=30, staple_days=3, rng=random.Random(seed))
            text = pt.build_post_text(rec, data, slot=(11, 0), max_chars=500, hashtags=("#unofficial_saize_health_navi",))
            assert len(text) <= 500
            assert "［公式］" in text
