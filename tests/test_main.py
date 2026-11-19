"""main.py の post コマンドのテスト。Misskey への通信は「にせのクライアント」に置き換える。"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import config
import main
from database.database import HistoryStore
from modules import misskey as mk

JST = ZoneInfo("Asia/Tokyo")


def at(hour, minute, day=19):
    return datetime(2026, 11, day, hour, minute, tzinfo=JST)


class FakeClient:
    """MisskeyClient の代わり。投稿内容を覚えておき、errors に入れたエラーを順に起こす。"""

    posted: list[dict] = []
    errors: list[Exception] = []
    calls = 0

    def __init__(self, host, token, timeout=10.0):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def create_note(self, text, *, visibility, local_only):
        FakeClient.calls += 1
        if FakeClient.errors:
            raise FakeClient.errors.pop(0)
        FakeClient.posted.append({"text": text, "visibility": visibility, "local_only": local_only})
        return {"id": f"note{len(FakeClient.posted)}"}


class FakeSleep:
    """time.sleep の代わり。実際には待たず、待つはずだった秒数を覚えておく。"""

    def __init__(self):
        self.waited: list[int] = []

    def __call__(self, seconds):
        self.waited.append(seconds)


@pytest.fixture
def settings_for(monkeypatch, tmp_path):
    FakeClient.posted, FakeClient.errors, FakeClient.calls = [], [], 0
    monkeypatch.setattr(mk, "MisskeyClient", FakeClient)

    def make(enable_posting: bool):
        for k, v in {
            "MISSKEY_HOST": "misskey.example.org",
            "MISSKEY_TOKEN": "test-token",
            "ENABLE_POSTING": "true" if enable_posting else "false",
            "DB_PATH": str(tmp_path / "history.sqlite3"),
            "POST_VISIBILITY": "public",
            "POST_LOCAL_ONLY": "true",
            "POST_TIMES": "11:00,15:00",
            "TIMEZONE": "Asia/Tokyo",
        }.items():
            monkeypatch.setenv(k, v)
        # setup_logging() は呼ばない（本物の logs/bot.log にテストの記録を書かないため）
        return config.load_settings(env_file=tmp_path / "missing.env")

    return make


def history_count(settings):
    if not settings.db_path.exists():
        return 0
    with HistoryStore(settings.db_path) as h:
        return h.count()


def post(settings, slot="11:00", now=None, sleep=None, dry_run=False):
    return main.run_post(settings, slot_text=slot, dry_run=dry_run, seed=1,
                         now=now or at(11, 0), sleep=sleep or FakeSleep())


# ---------------------------------------------------------------------------
# 投稿する／しない
# ---------------------------------------------------------------------------


def test_dry_run_does_not_post_or_record(settings_for, capsys):
    settings = settings_for(enable_posting=True)
    assert post(settings, dry_run=True) == 0
    assert FakeClient.posted == []
    assert history_count(settings) == 0
    assert not settings.db_path.exists()  # 空の履歴ファイルも作らない
    assert "投稿していません" in capsys.readouterr().out


def test_posting_disabled_does_not_post(settings_for):
    settings = settings_for(enable_posting=False)
    assert post(settings) == 0
    assert FakeClient.posted == []


def test_successful_post_is_recorded(settings_for):
    settings = settings_for(enable_posting=True)
    assert post(settings) == 0
    assert len(FakeClient.posted) == 1
    assert FakeClient.posted[0]["visibility"] == "public" and FakeClient.posted[0]["local_only"] is True
    assert FakeClient.posted[0]["text"].startswith("🍽 今日のお昼のサイゼ献立")
    assert history_count(settings) == 1


# ---------------------------------------------------------------------------
# --slot auto
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "now,expected",
    [
        (at(11, 0), (11, 0)),
        (at(11, 29), (11, 0)),   # 29分遅れ → まだ 11:00 の枠
        (at(10, 45), (11, 0)),   # 少し早く起動しても 11:00 の枠
        (at(11, 30), None),      # 30分遅れ → 投稿しない
        (at(13, 0), None),
        (at(15, 5), (15, 0)),
    ],
)
def test_resolve_slot(now, expected):
    assert main.resolve_slot(now, ((11, 0), (15, 0))) == expected


def test_auto_slot_posts_with_matching_heading(settings_for):
    settings = settings_for(enable_posting=True)
    assert post(settings, slot="auto", now=at(15, 2)) == 0
    assert FakeClient.posted[0]["text"].startswith("🌆 夕ごはんの参考に")
    with HistoryStore(settings.db_path) as h:
        assert h.posted_in_slot_since("15:00", at(0, 0))


def test_auto_slot_outside_window_does_nothing(settings_for):
    settings = settings_for(enable_posting=True)
    assert post(settings, slot="auto", now=at(13, 0)) == 0  # 失敗ではない（再実行させない）
    assert FakeClient.calls == 0
    assert history_count(settings) == 0


# ---------------------------------------------------------------------------
# 二重投稿の防止
# ---------------------------------------------------------------------------


def test_same_slot_same_day_is_not_posted_twice(settings_for):
    settings = settings_for(enable_posting=True)
    assert post(settings, slot="auto", now=at(11, 0)) == 0
    assert post(settings, slot="auto", now=at(11, 10)) == 0  # 同じ日・同じ枠 → 何もしない
    assert len(FakeClient.posted) == 1
    assert history_count(settings) == 1


def test_other_slot_or_next_day_is_posted(settings_for):
    settings = settings_for(enable_posting=True)
    assert post(settings, slot="auto", now=at(11, 0)) == 0
    assert post(settings, slot="auto", now=at(15, 0)) == 0           # 同じ日の別の枠
    assert post(settings, slot="auto", now=at(11, 0, day=20)) == 0   # 次の日の同じ枠
    assert len(FakeClient.posted) == 3


# ---------------------------------------------------------------------------
# やり直し
# ---------------------------------------------------------------------------


def test_temporary_error_is_retried_once(settings_for):
    settings = settings_for(enable_posting=True)
    FakeClient.errors = [mk.MisskeyError("HTTP 502", status=502)]
    sleep = FakeSleep()
    assert post(settings, sleep=sleep) == 0
    assert sleep.waited == [main.RETRY_DELAY_SECONDS]
    assert FakeClient.calls == 2
    assert history_count(settings) == 1


def test_rate_limit_waits_retry_after(settings_for):
    settings = settings_for(enable_posting=True)
    FakeClient.errors = [mk.RateLimitError("HTTP 429", status=429, retry_after=30)]
    sleep = FakeSleep()
    assert post(settings, sleep=sleep) == 0
    assert sleep.waited == [30]


def test_two_failures_are_not_recorded(settings_for):
    settings = settings_for(enable_posting=True)
    FakeClient.errors = [mk.MisskeyError("timeout"), mk.MisskeyError("timeout")]
    assert post(settings) == 1
    assert FakeClient.calls == 2  # やり直しは1回だけ
    assert history_count(settings) == 0  # 投稿していない献立を「最近出した」扱いにしない


@pytest.mark.parametrize(
    "error",
    [
        mk.AuthenticationError("HTTP 401", status=401),
        mk.PermissionDeniedError("HTTP 403", status=403),
        mk.MisskeyError("HTTP 400", status=400),
        mk.RateLimitError("HTTP 429", status=429, retry_after=3600),  # 待ち時間が長すぎる
    ],
)
def test_permanent_errors_are_not_retried(settings_for, error):
    settings = settings_for(enable_posting=True)
    FakeClient.errors = [error]
    sleep = FakeSleep()
    assert post(settings, sleep=sleep) == 1
    assert FakeClient.calls == 1
    assert sleep.waited == []
