"""おすすめ献立を Misskey 投稿用の文章にする（Phase 4）。

「何をおすすめするか」は recommendation.py、「どう書くか」はこのファイルが担当する。

文章のルール:
- 数値には必ず［公式］か［推定］を付ける（公式情報と推定情報を混同しない）
- 推定値を1つでも表示したら、食品成分表の出典を書く（文部科学省の求める記載）
- 「食べてはいけない」のような禁止表現は使わず、よりよい選び方を1つだけ添える
- 最大文字数を超えるときは「ひとこと」→「ポイントの3つ目以降」の順に削る。注意書きと出典は削らない
"""

from __future__ import annotations

from modules.nutrition import HIGH, NOT_EVALUATED, SLIGHTLY_HIGH, ENOUGH, NutritionData
from modules.pricing import PriceTable
from modules.recommendation import Recommendation

SLOT_HEADINGS = {
    (11, 0): "🍽 今日のお昼のサイゼ献立",
    (15, 0): "🌆 夕ごはんの参考に、サイゼ献立",
}
DEFAULT_HEADING = "🍽 今日のおすすめサイゼ献立"

MEXT_CITATION = "日本食品標準成分表（八訂）増補2023年"
DISCLAIMER = "※非公式Botです。医療上の判断には使わないでください。"
ESTIMATE_NOTE = "［推定］は材料を仮定した計算値です。"
KEEP_POINTS_WHEN_SHORTENING = 2


class PostTooLongError(ValueError):
    """削れるところを削っても最大文字数に収まらないとき。"""


def heading_for(slot: tuple[int, int] | None) -> str:
    return SLOT_HEADINGS.get(slot, DEFAULT_HEADING) if slot else DEFAULT_HEADING


def nutrition_lines(rec: Recommendation) -> tuple[list[str], bool]:
    """「1食分のめやす」の行と、推定値を使ったかどうかを返す。"""
    ev = rec.evaluation
    lines: list[str] = []
    salt = ev.salt.values
    if salt:
        lines.append(f"塩分 {salt['salt_g']}g（1日の目標{salt['daily_target_g']}g未満の約{salt['daily_pct']}%）［公式］")
    kcal = ev.triglyceride.values.get("calories_kcal")
    if kcal is not None:
        lines.append(f"エネルギー {kcal}kcal［公式］")

    ldl = ev.ldl.values
    used_estimate = False
    omitted: list[str] = []
    if ldl.get("saturated_fat_level") not in (None, NOT_EVALUATED):
        lines.append(
            f"飽和脂肪酸 エネルギーの約{ldl['saturated_fat_energy_pct']}%"
            f"（目標{ldl['saturated_fat_target_pct']}%以下）［推定］"
        )
        used_estimate = True
    else:
        omitted.append("飽和脂肪酸")
    if ldl.get("dietary_fiber_level") not in (None, NOT_EVALUATED):
        lines.append(f"食物繊維 約{ldl['dietary_fiber_g']}g［推定］")
        used_estimate = True
    else:
        omitted.append("食物繊維")
    if omitted:
        lines.append(f"（{'・'.join(omitted)}は、推定値がない料理を含むため省略）")
    return lines, used_estimate


def choose_tip(rec: Recommendation, data: NutritionData) -> str:
    """評価に合わせた「ひとこと」を1つ選ぶ（一般的な工夫だけ。数値の約束はしない）。"""
    ev = rec.evaluation
    has_soup = any("汁物" in data.tags.get(m, []) for m in rec.menu_ids)
    if ev.salt.level in (SLIGHTLY_HIGH, HIGH):
        if has_soup:
            return "スープは汁を少し残すと、塩分をおさえられます。"
        return "卓上の粉チーズや塩は控えめにすると、塩分をおさえられます。"
    if ev.ldl.values.get("dietary_fiber_level") == ENOUGH:
        return "塩分は控えめ、食物繊維もしっかりの組み合わせです。"
    return "野菜から先に食べると、満足感が出やすくなります。"


def source_line(data: NutritionData, used_estimate: bool, prices: PriceTable | None = None) -> str:
    updated = data.source.get("official_updated_at")
    parts = ["サイゼリヤ公式 カロリー塩分情報" + (f"（{updated}更新）" if updated else "")]
    if prices is not None:
        checked = prices.source.get("checked_at")
        parts[0] += "・メニューブック" + (f"（{checked}確認）" if checked else "")
    if used_estimate:
        parts.append(MEXT_CITATION)
    return "出典：" + "／".join(parts)


def price_line(rec: Recommendation) -> str | None:
    """合計金額の行。価格が分からない料理を含むときは出さない。"""
    if rec.total_price_yen is None:
        return None
    return f"💴 合計 {rec.total_price_yen:,}円（税込・標準価格）※店舗により異なる場合があります"


def build_post_text(
    rec: Recommendation,
    data: NutritionData,
    *,
    slot: tuple[int, int] | None,
    max_chars: int,
    hashtags: tuple[str, ...] = (),
    prices: PriceTable | None = None,
) -> str:
    nutrition, used_estimate = nutrition_lines(rec)
    price = price_line(rec)
    disclaimer = DISCLAIMER + (ESTIMATE_NOTE if used_estimate else "")
    footer_lines = [disclaimer, source_line(data, used_estimate, prices if price else None)]
    if hashtags:
        footer_lines.append(" ".join(hashtags))
    footer = "\n".join(footer_lines)

    heading = heading_for(slot)

    def assemble(points: list[str], tip: str | None) -> str:
        blocks = [
            heading,
            "\n".join(f"・{name}" for name in rec.names),
            "✅ ここがポイント\n" + "\n".join(f"・{p}" for p in points),
            "📊 1食分のめやす\n" + "\n".join(nutrition),
        ]
        if price:
            blocks.append(price)
        if tip:
            blocks.append(f"💡 {tip}")
        blocks.append(footer)
        return "\n\n".join(blocks)

    # 長すぎるときは、削ってよい部分から順に削る（ひとこと → ポイントの3つ目以降）
    points = list(rec.reasons)
    tip = choose_tip(rec, data)
    text = assemble(points, tip)
    if len(text) > max_chars:
        text = assemble(points, None)
    if len(text) > max_chars:
        text = assemble(points[:KEEP_POINTS_WHEN_SHORTENING], None)
    if len(text) > max_chars:
        raise PostTooLongError(f"投稿文が {len(text)} 文字で、上限 {max_chars} 文字に収まりません")
    return text
