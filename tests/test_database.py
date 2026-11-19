from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from database.database import HistoryStore, make_combo_key

JST = ZoneInfo("Asia/Tokyo")


def test_combo_key_ignores_order():
    assert make_combo_key(["b", "a", "c"]) == make_combo_key(["c", "b", "a"]) == "a|b|c"


def test_add_and_query_since():
    now = datetime(2026, 11, 19, 11, 0, tzinfo=JST)
    with HistoryStore(":memory:") as h:
        h.add(menu_ids=["パスタ", "サラダ"], staple_id="パスタ", pattern="balance", slot="11:00",
              created_at=now - timedelta(days=10))
        h.add(menu_ids=["ドリア", "サラダ"], staple_id="ドリア", pattern="balance", slot="15:00",
              created_at=now - timedelta(days=1))
        assert h.count() == 2
        assert h.combo_keys_since(now - timedelta(days=30)) == {"サラダ|パスタ", "サラダ|ドリア"}
        assert h.combo_keys_since(now - timedelta(days=3)) == {"サラダ|ドリア"}
        assert h.staples_since(now - timedelta(days=3)) == {"ドリア"}


def test_jst_and_utc_are_compared_correctly():
    """日本時間で記録しても UTC で検索しても、同じ瞬間として扱われる。"""
    jst_moment = datetime(2026, 11, 19, 11, 0, tzinfo=JST)  # = 02:00 UTC
    with HistoryStore(":memory:") as h:
        h.add(menu_ids=["A"], staple_id="A", pattern="balance", slot=None, created_at=jst_moment)
        assert h.staples_since(datetime(2026, 11, 19, 2, 0, tzinfo=timezone.utc)) == {"A"}
        assert h.staples_since(datetime(2026, 11, 19, 2, 1, tzinfo=timezone.utc)) == set()


def test_naive_datetime_is_rejected():
    with HistoryStore(":memory:") as h, pytest.raises(ValueError):
        h.add(menu_ids=["A"], staple_id="A", pattern="balance", slot=None, created_at=datetime(2026, 11, 19))


def test_mains_since_ignores_meals_without_main():
    now = datetime(2026, 11, 19, 11, 0, tzinfo=JST)
    with HistoryStore(":memory:") as h:
        h.add(menu_ids=["ライス", "サラダ", "チキン"], staple_id="ライス", main_id="チキン", pattern="balance",
              slot=None, created_at=now)
        h.add(menu_ids=["パスタ", "サラダ"], staple_id="パスタ", main_id=None, pattern="balance",
              slot=None, created_at=now)
        assert h.mains_since(now - timedelta(days=1)) == {"チキン"}


def test_old_history_file_gets_new_column(tmp_path):
    """main_id 列が無い古い履歴ファイルを開くと、列が追加され、古い記録も残る。"""
    import sqlite3

    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE recommendation_history (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,"
        " slot TEXT, pattern TEXT NOT NULL, combo_key TEXT NOT NULL, staple_id TEXT NOT NULL, menu_ids TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO recommendation_history (created_at, pattern, combo_key, staple_id, menu_ids)"
                 " VALUES ('2026-11-18T00:00:00+00:00', 'balance', 'A', 'A', '[\"A\"]')")
    conn.commit()
    conn.close()

    with HistoryStore(path) as h:
        columns = {row[1] for row in h.conn.execute("PRAGMA table_info(recommendation_history)")}
        assert "main_id" in columns
        assert h.count() == 1


def test_history_persists_in_file(tmp_path):
    path = tmp_path / "sub" / "history.sqlite3"
    now = datetime(2026, 11, 19, tzinfo=JST)
    with HistoryStore(path) as h:
        h.add(menu_ids=["A"], staple_id="A", pattern="balance", slot=None, created_at=now)
    with HistoryStore(path) as h:  # 開き直しても残っている
        assert h.count() == 1
