"""アプリ全体の設定を .env / 環境変数から読み込むモジュール。

- トークンなどの秘密情報は repr / str に出さない（ログ流出防止）。
- パスは pathlib で扱い、Windows / Linux の両方で動くようにする。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in _TRUE_VALUES


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"環境変数 {name} は数値で指定してください: {value!r}") from exc


@dataclass(frozen=True)
class Settings:
    misskey_host: str
    # repr=False でトークンが print / ログに出ないようにする
    misskey_token: str = field(repr=False)
    enable_posting: bool
    http_timeout: float
    log_level: str
    db_path: Path
    log_dir: Path

    @property
    def has_token(self) -> bool:
        return bool(self.misskey_token) and self.misskey_token != "your-token-here"


def load_settings(env_file: Path | None = None) -> Settings:
    """設定を読み込む。既に設定済みの環境変数は .env より優先される。"""
    load_dotenv(env_file or BASE_DIR / ".env", override=False)

    db_path = Path(os.getenv("DB_PATH", "database/saize.sqlite3"))
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    return Settings(
        misskey_host=os.getenv("MISSKEY_HOST", "").strip(),
        misskey_token=os.getenv("MISSKEY_TOKEN", "").strip(),
        enable_posting=_get_bool("ENABLE_POSTING", False),
        http_timeout=_get_float("HTTP_TIMEOUT", 10.0),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        db_path=db_path,
        log_dir=BASE_DIR / "logs",
    )
