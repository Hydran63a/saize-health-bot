import hashlib
import json

import pytest

from modules import menu_importer as mi


def make_snapshot(tmp_path, body: str):
    tsv = tmp_path / "allergy_2099-01-01.tsv"
    data = ("商品名\tエネルギーkcal\t食塩相当量g\tアレルゲン\n" + body).encode("utf-8")
    tsv.write_bytes(data)
    meta = {
        "source_name": "test",
        "source_url": "https://example.invalid/",
        "official_updated_at": "2099-01-01",
        "captured_at": "2099-01-02",
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    tsv.with_suffix(".meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return tsv


def test_parse_official_number():
    assert mi.parse_official_number("196") == {"value": 196, "status": "official"}
    assert mi.parse_official_number("1.4") == {"value": 1.4, "status": "official"}
    assert mi.parse_official_number("0") == {"value": 0, "status": "official", "trace": True}
    assert mi.parse_official_number("-") == {"value": None, "status": "not_calculable"}
    assert mi.parse_official_number("") == {"value": None, "status": "unknown"}
    with pytest.raises(mi.ImportError_):
        mi.parse_official_number("abc")


def test_split_name():
    assert mi.split_name("食卓塩※1gあたり") == ("食卓塩", "1gあたり")
    assert mi.split_name("ライス") == ("ライス", None)


def test_parse_allergens_with_buffalo_mark():
    allergens, notes = mi.parse_allergens("乳▲,豚肉")
    assert allergens == ["乳", "豚肉"]
    assert notes == ["乳：モッツァレラチーズの水牛の乳"]


def test_unknown_allergen_is_rejected():
    with pytest.raises(mi.ImportError_):
        mi.parse_allergens("ぶどう")


def test_build_menu_marks_unpublished_and_duplicates(tmp_path):
    tsv = make_snapshot(tmp_path, "ライス\t303\t0\t\nフォッカチオ\t241\t0.6\t小麦\nフォッカチオ\t199\t0.7\t小麦,大豆\n")
    rows, meta = mi.read_snapshot(tsv)
    menu = mi.build_menu(rows, meta)
    items = menu["items"]

    assert [i["id"] for i in items] == ["ライス", "フォッカチオ#1", "フォッカチオ#2"]
    assert items[1]["duplicate_name"] and not items[0]["duplicate_name"]
    # 公式に無い栄養素は補完せず null + not_published
    for key in mi.UNPUBLISHED_NUTRIENTS:
        assert items[0]["nutrition"][key] == {"value": None, "status": "not_published"}
    assert items[0]["category"] is None and items[0]["price"] is None
    assert items[0]["nutrition"]["salt"]["trace"] is True


def test_tampered_snapshot_is_rejected(tmp_path):
    tsv = make_snapshot(tmp_path, "ライス\t303\t0\t\n")
    tsv.write_bytes(tsv.read_bytes().replace(b"303", b"300"))
    with pytest.raises(mi.ImportError_, match="sha256"):
        mi.read_snapshot(tsv)


def test_wrong_column_count_is_rejected(tmp_path):
    tsv = make_snapshot(tmp_path, "ライス\t303\t0\n")  # 行末のタブが欠けている
    with pytest.raises(mi.ImportError_, match="列数"):
        mi.read_snapshot(tsv)


def test_real_snapshot_matches_official_counts():
    rows, meta = mi.read_snapshot(mi.latest_snapshot())
    menu = mi.build_menu(rows, meta)
    assert len(menu["items"]) == 125
    shrimp = next(i for i in menu["items"] if i["id"] == "小エビのサラダ")
    assert shrimp["nutrition"]["calories"]["value"] == 196
    assert shrimp["nutrition"]["salt"]["value"] == 1.4
    assert shrimp["allergens"] == ["えび", "卵", "大豆", "りんご"]
