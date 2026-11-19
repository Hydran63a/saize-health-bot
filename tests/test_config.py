from pathlib import Path

import pytest

import config


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("MISSKEY_HOST", "MISSKEY_TOKEN", "ENABLE_POSTING", "HTTP_TIMEOUT", "LOG_LEVEL", "DB_PATH"):
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


def test_env_example_has_no_real_secrets():
    text = (Path(config.BASE_DIR) / ".env.example").read_text(encoding="utf-8")
    assert "MISSKEY_TOKEN=your-token-here" in text
