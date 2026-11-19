"""main.py の post コマンドのテスト。Misskey への通信は「にせのクライアント」に置き換える。"""

import pytest

import config
import main
from database.database import HistoryStore
from modules import misskey as mk


class FakeClient:
    """MisskeyClient の代わり。投稿内容を覚えておき、必要ならエラーを起こす。"""

    posted: list[dict] = []
    error: Exception | None = None

    def __init__(self, host, token, timeout=10.0):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def create_note(self, text, *, visibility, local_only):
        if FakeClient.error:
            raise FakeClient.error
        FakeClient.posted.append({"text": text, "visibility": visibility, "local_only": local_only})
        return {"id": "note1"}


@pytest.fixture
def settings_for(monkeypatch, tmp_path):
    FakeClient.posted = []
    FakeClient.error = None
    monkeypatch.setattr(mk, "MisskeyClient", FakeClient)

    def make(enable_posting: bool):
        for k, v in {
            "MISSKEY_HOST": "misskey.example.org",
            "MISSKEY_TOKEN": "test-token",
            "ENABLE_POSTING": "true" if enable_posting else "false",
            "DB_PATH": str(tmp_path / "history.sqlite3"),
            "POST_VISIBILITY": "public",
            "POST_LOCAL_ONLY": "true",
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


def test_dry_run_does_not_post_or_record(settings_for, capsys):
    settings = settings_for(enable_posting=True)
    assert main.run_post(settings, slot_text="11:00", dry_run=True, seed=1) == 0
    assert FakeClient.posted == []
    assert history_count(settings) == 0
    assert not settings.db_path.exists()  # 空の履歴ファイルも作らない
    assert "投稿していません" in capsys.readouterr().out


def test_posting_disabled_does_not_post(settings_for):
    settings = settings_for(enable_posting=False)
    assert main.run_post(settings, slot_text="11:00", dry_run=False, seed=1) == 0
    assert FakeClient.posted == []


def test_successful_post_is_recorded(settings_for):
    settings = settings_for(enable_posting=True)
    assert main.run_post(settings, slot_text="11:00", dry_run=False, seed=1) == 0
    assert len(FakeClient.posted) == 1
    assert FakeClient.posted[0]["visibility"] == "public" and FakeClient.posted[0]["local_only"] is True
    assert FakeClient.posted[0]["text"].startswith("🍽 今日のお昼のサイゼ献立")
    assert history_count(settings) == 1


def test_failed_post_is_not_recorded(settings_for):
    settings = settings_for(enable_posting=True)
    FakeClient.error = mk.RateLimitError("Misskey API エラー（HTTP 429）", status=429, retry_after=30)
    assert main.run_post(settings, slot_text="11:00", dry_run=False, seed=1) == 1
    assert history_count(settings) == 0  # 投稿していない献立を「最近出した」扱いにしない
