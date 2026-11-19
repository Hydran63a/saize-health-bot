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


def test_history_persists_in_file(tmp_path):
    path = tmp_path / "sub" / "history.sqlite3"
    now = datetime(2026, 11, 19, tzinfo=JST)
    with HistoryStore(path) as h:
        h.add(menu_ids=["A"], staple_id="A", pattern="balance", slot=None, created_at=now)
    with HistoryStore(path) as h:  # 開き直しても残っている
        assert h.count() == 1
