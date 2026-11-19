from pathlib import Path

import pytest

import config


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("MISSKEY_HOST", "MISSKEY_TOKEN", "ENABLE_POSTING", "HTTP_TIMEOUT", "LOG_LEVEL", "DB_PATH", "POST_TIMES", "TIMEZONE",
                 "NO_REPEAT_COMBO_DAYS", "NO_REPEAT_STAPLE_DAYS"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_defaults_without_env_file(clean_env, tmp_path):
    settings = config.load_settings(env_file=tmp_path / "missing.env")
    assert settings.enable_posting is False
    assert settings.http_timeout == 10.0
    assert settings.has_token is False
    assert settings.db_path.is_absolute()


def test_token_is_not_exposed_in_repr(clean_env, tmp_path):
    clean_env.setenv("MISSKEY_TOKEN", "super-secret-token")
    settings = config.load_settings(env_file=tmp_path / "missing.env")
    assert settings.has_token is True
    assert "super-secret-token" not in repr(settings)
    assert "super-secret-token" not in str(settings)


def test_dummy_token_is_treated_as_unset(clean_env, tmp_path):
    clean_env.setenv("MISSKEY_TOKEN", "your-token-here")
    assert config.load_settings(env_file=tmp_path / "missing.env").has_token is False


def test_invalid_timeout_raises(clean_env, tmp_path):
    clean_env.setenv("HTTP_TIMEOUT", "abc")
    with pytest.raises(ValueError):
        config.load_settings(env_file=tmp_path / "missing.env")


def test_default_post_times_are_11_and_15_jst(clean_env, tmp_path):
    settings = config.load_settings(env_file=tmp_path / "missing.env")
    assert settings.post_times == ((11, 0), (15, 0))
    assert settings.timezone == "Asia/Tokyo"
    assert settings.post_times_text == "11:00, 15:00"


def test_post_times_can_be_overridden_and_sorted(clean_env, tmp_path):
    clean_env.setenv("POST_TIMES", "18:30, 07:05,18:30")
    assert config.load_settings(env_file=tmp_path / "missing.env").post_times == ((7, 5), (18, 30))


@pytest.mark.parametrize("bad", ["25:00", "11:60", "11", "11:0", "aa:bb", " , "])
def test_invalid_post_times_raise(bad):
    with pytest.raises(ValueError):
        config.parse_post_times(bad)


def test_default_no_repeat_days(clean_env, tmp_path):
    settings = config.load_settings(env_file=tmp_path / "missing.env")
    assert settings.no_repeat_combo_days == 30
    assert settings.no_repeat_staple_days == 3


def test_invalid_no_repeat_days_raise(clean_env, tmp_path):
    clean_env.setenv("NO_REPEAT_COMBO_DAYS", "-1")
    with pytest.raises(ValueError):
        config.load_settings(env_file=tmp_path / "missing.env")


def test_env_example_has_no_real_secrets():
    text = (Path(config.BASE_DIR) / ".env.example").read_text(encoding="utf-8")
    assert "MISSKEY_TOKEN=your-token-here" in text
