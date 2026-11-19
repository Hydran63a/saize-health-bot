"""アプリ全体の設定を .env / 環境変数から読み込むモジュール。

- トークンなどの秘密情報は repr / str に出さない（ログ流出防止）。
- パスは pathlib で扱い、Windows / Linux の両方で動くようにする。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

_TRUE_VALUES = {"1", "true", "yes", "on"}

DEFAULT_POST_TIMES = "11:00,15:00"
DEFAULT_TIMEZONE = "Asia/Tokyo"
DEFAULT_NO_REPEAT_COMBO_DAYS = 30
DEFAULT_NO_REPEAT_STAPLE_DAYS = 3
DEFAULT_POST_MAX_CHARS = 500
DEFAULT_POST_HASHTAGS = "#unofficial_saize_health_navi"

_HASHTAG = re.compile(r"^#[0-9A-Za-z_぀-ヿ一-鿿]+$")


def parse_hashtags(text: str) -> tuple[str, ...]:
    """'#a, #b' や '#a #b' → ('#a', '#b')。空なら () でタグなし。"""
    tags = tuple(t for t in re.split(r"[,\s]+", text.strip()) if t)
    for tag in tags:
        if not _HASHTAG.match(tag):
            raise ValueError(f"POST_HASHTAGS の形式が正しくありません（# から始まる英数字・_・日本語）: {tag!r}")
    return tags


def _get_non_negative_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    if not value.strip().isdigit():
        raise ValueError(f"環境変数 {name} は0以上の整数で指定してください: {value!r}")
    return int(value)


def parse_post_times(text: str) -> tuple[tuple[int, int], ...]:
    """'11:00,15:00' → ((11, 0), (15, 0))。重複は除き、時刻順に並べる。"""
    times: set[tuple[int, int]] = set()
    for part in filter(None, (p.strip() for p in text.split(","))):
        hour_text, sep, minute_text = part.partition(":")
        if not sep or not hour_text.isdigit() or not minute_text.isdigit() or len(minute_text) != 2:
            raise ValueError(f"POST_TIMES の形式が正しくありません（HH:MM）: {part!r}")
        hour, minute = int(hour_text), int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"POST_TIMES の時刻が範囲外です: {part!r}")
        times.add((hour, minute))
    if not times:
        raise ValueError("POST_TIMES に時刻が1つもありません")
    return tuple(sorted(times))


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
    # おすすめ投稿の時刻（timezone での時刻）。自動投稿は ENABLE_POSTING=true のときのみ
    post_times: tuple[tuple[int, int], ...]
    timezone: str
    # 重複防止：同じ組み合わせ／同じ主食をおすすめしない日数
    no_repeat_combo_days: int
    no_repeat_staple_days: int
    # 投稿文：最大文字数とハッシュタグ
    post_max_chars: int
    post_hashtags: tuple[str, ...]

    @property
    def post_times_text(self) -> str:
        return ", ".join(f"{h:02d}:{m:02d}" for h, m in self.post_times)

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
        post_times=parse_post_times(os.getenv("POST_TIMES") or DEFAULT_POST_TIMES),
        timezone=(os.getenv("TIMEZONE") or DEFAULT_TIMEZONE).strip(),
        no_repeat_combo_days=_get_non_negative_int("NO_REPEAT_COMBO_DAYS", DEFAULT_NO_REPEAT_COMBO_DAYS),
        no_repeat_staple_days=_get_non_negative_int("NO_REPEAT_STAPLE_DAYS", DEFAULT_NO_REPEAT_STAPLE_DAYS),
        post_max_chars=_get_non_negative_int("POST_MAX_CHARS", DEFAULT_POST_MAX_CHARS),
        post_hashtags=parse_hashtags(os.getenv("POST_HASHTAGS", DEFAULT_POST_HASHTAGS)),
    )
