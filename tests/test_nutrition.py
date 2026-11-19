import pytest

from modules import nutrition as nu

# ---------------------------------------------------------------------------
# テスト用の小さな架空データ（本物のデータが更新されてもテストが壊れないように）
# ---------------------------------------------------------------------------

REFERENCES = {
    "meals_per_day": 3,
    "salt": {"daily_target_g": 6.0},  # 1食の目安 2.0g（計算しやすい値）
    "saturated_fat": {"energy_pct_target": 7},
    "dietary_fiber": {"daily_target_g": 18},  # 1食の目安 6.0g
    "cholesterol": {"daily_reference_mg": 200},
}


def menu_item(menu_id, kcal, salt):
    return {
        "id": menu_id,
        "name": menu_id,
        "nutrition": {"calories": {"value": kcal}, "salt": {"value": salt}},
    }


def estimate(menu_id, confidence, sat, fiber, chol=0, carb=0):
    def n(v):
        return {"value": v, "status": "estimated" if v is not None else "not_estimable"}

    return {
        "menu_id": menu_id,
        "confidence": confidence,
        "nutrients": {
            "saturated_fat": n(sat),
            "dietary_fiber": n(fiber),
            "cholesterol": n(chol),
            "carbohydrate": n(carb),
        },
    }


@pytest.fixture
def data():
    menu = {
        "items": [
            menu_item("サラダ", 100, 0.5),
            menu_item("パスタ", 600, 1.5),
            menu_item("ドリア", 500, 2.5),
            menu_item("ライス", 300, 0.0),
            menu_item("謎の料理", 200, 1.0),
        ]
    }
    estimates = {
        "items": [
            estimate("サラダ", "medium", sat=0.5, fiber=3.0, carb=5),
            estimate("パスタ", "high", sat=3.0, fiber=4.0, carb=80),
            estimate("ドリア", "low", sat=9.0, fiber=2.0),  # 信頼度「低」→使わない
            estimate("ライス", "high", sat=0.2, fiber=3.0, carb=70),
        ]
    }
    tags = {
        "items": {
            "サラダ": ["副菜", "野菜"],
            "パスタ": ["主食", "チーズ・クリーム・バター"],
            "ドリア": ["主食"],
            "ライス": ["主食"],
        }
    }
    return nu.NutritionData(menu, estimates, REFERENCES, tags)


# ---------------------------------------------------------------------------
# 塩分
# ---------------------------------------------------------------------------


def test_salt_within_guide(data):
    ev = nu.evaluate_meal(data, ["サラダ", "パスタ"])  # 0.5 + 1.5 = 2.0g ＝ ちょうど目安
    assert ev.salt.level == nu.WITHIN
    assert ev.salt.values["salt_g"] == 2.0
    assert ev.salt.values["daily_pct"] == 33


def test_salt_boundaries():
    assert nu.level_by_ratio(1.0) == nu.WITHIN
    assert nu.level_by_ratio(1.01) == nu.SLIGHTLY_HIGH
    assert nu.level_by_ratio(1.5) == nu.SLIGHTLY_HIGH
    assert nu.level_by_ratio(1.51) == nu.HIGH


def test_salt_uses_official_value_even_without_estimates(data):
    ev = nu.evaluate_meal(data, ["ドリア", "謎の料理"])  # 推定値なしでも塩分は公式値で評価できる
    assert ev.salt.values["salt_g"] == 3.5
    assert ev.salt.level == nu.HIGH  # 3.5 / 2.0 = 1.75倍


# ---------------------------------------------------------------------------
# LDL
# ---------------------------------------------------------------------------


def test_saturated_fat_energy_pct(data):
    ev = nu.evaluate_meal(data, ["サラダ", "パスタ"])
    # (0.5 + 3.0) g × 9 kcal ÷ 700 kcal × 100 = 4.5%
    assert ev.ldl.values["saturated_fat_energy_pct"] == 4.5
    assert ev.ldl.level == nu.WITHIN


def test_saturated_fat_levels():
    assert nu.saturated_fat_level(7.0, 7) == nu.WITHIN
    assert nu.saturated_fat_level(8.0, 7) == nu.SLIGHTLY_HIGH
    assert nu.saturated_fat_level(10.0, 7) == nu.SLIGHTLY_HIGH
    assert nu.saturated_fat_level(10.1, 7) == nu.HIGH


def test_fiber_levels(data):
    assert nu.evaluate_meal(data, ["サラダ", "パスタ"]).ldl.values["dietary_fiber_level"] == nu.ENOUGH  # 7.0g
    assert nu.evaluate_meal(data, ["サラダ"]).ldl.values["dietary_fiber_level"] == nu.MODERATE  # 3.0g
    assert nu.evaluate_meal(data, ["ライス"]).ldl.values["dietary_fiber_level"] == nu.MODERATE  # 3.0g


def test_low_confidence_estimate_is_not_used(data):
    ev = nu.evaluate_meal(data, ["サラダ", "ドリア"])
    assert ev.ldl.level == nu.NOT_EVALUATED
    assert "saturated_fat_g" not in ev.ldl.values  # 合計を出さない（0扱いもしない）
    assert ev.missing_estimates == ["ドリア"]
    assert any("ドリア" in note for note in ev.ldl.notes)  # 原因の料理名が注意書きに出る


def test_partially_missing_nutrient_names_the_dish():
    """推定値はあるが、飽和脂肪酸だけ算出不可の料理（例：デミグラスソースのハンバーグ）。"""
    menu = {"items": [menu_item("サラダ", 100, 0.5), menu_item("ハンバーグ", 500, 2.0)]}
    estimates = {"items": [
        estimate("サラダ", "medium", sat=0.5, fiber=3.0),
        estimate("ハンバーグ", "medium", sat=None, fiber=None, carb=20),
    ]}
    data = nu.NutritionData(menu, estimates, REFERENCES, {"items": {}})
    ev = nu.evaluate_meal(data, ["サラダ", "ハンバーグ"])
    assert ev.ldl.level == nu.NOT_EVALUATED
    assert any("ハンバーグ" in note and "飽和脂肪酸" in note for note in ev.ldl.notes)
    assert ev.missing_estimates == []  # 推定値そのものはある
    assert "carbohydrate_g" in ev.triglyceride.values  # 炭水化物は算出できる


def test_ldl_points_from_tags(data):
    points = nu.evaluate_meal(data, ["サラダ", "パスタ"]).ldl.points
    assert any("野菜" in p and "サラダ" in p for p in points)
    assert any("チーズ・クリーム・バター" in p and "パスタ" in p for p in points)


# ---------------------------------------------------------------------------
# 中性脂肪
# ---------------------------------------------------------------------------


def test_triglyceride_has_no_level_but_points(data):
    ev = nu.evaluate_meal(data, ["パスタ", "ライス"])
    assert ev.triglyceride.level is None  # エネルギー必要量は人によって違うので判定しない
    assert ev.triglyceride.values["calories_kcal"] == 900
    assert ev.triglyceride.values["carbohydrate_energy_pct"] == round(150 * 4 / 900 * 100)
    assert any("主食が重なっています" in p for p in ev.triglyceride.points)


# ---------------------------------------------------------------------------
# 入力のチェック
# ---------------------------------------------------------------------------


def test_unknown_menu_is_rejected(data):
    with pytest.raises(KeyError):
        nu.evaluate_meal(data, ["存在しない料理"])


def test_empty_meal_is_rejected(data):
    with pytest.raises(ValueError):
        nu.evaluate_meal(data, [])


def test_untagged_items_are_reported(data):
    assert nu.evaluate_meal(data, ["謎の料理"]).untagged == ["謎の料理"]


def test_tag_file_with_unknown_name_is_rejected():
    with pytest.raises(KeyError):
        nu.NutritionData({"items": []}, {"items": []}, REFERENCES, {"items": {"打ち間違い": ["主食"]}})


# ---------------------------------------------------------------------------
# 本物のデータ
# ---------------------------------------------------------------------------


def test_real_data_loads_and_all_tags_match_menu():
    data = nu.NutritionData.load()  # タグの名前が menu.json と食い違っていれば KeyError になる
    assert data.references["salt"]["daily_target_g"] == 6.5
    assert data.references["dietary_fiber"]["daily_target_g"] == 20
