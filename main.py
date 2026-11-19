"""Saize Health Bot エントリーポイント。

使い方:
    python main.py check              # 環境チェック（通信しない）
    python main.py check --account    # Misskey につながるか確認（POST /api/i）
    python main.py post --slot 11:00  # おすすめを作って投稿する
                                      #   ENABLE_POSTING=false のときや --dry-run では投稿せず表示だけ
"""

from __future__ import annotations

import argparse
import logging
import platform
import random
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

from config import Settings, load_settings, parse_post_times

MIN_PYTHON = (3, 12)
logger = logging.getLogger("saize_bot")


def setup_logging(settings: Settings) -> None:
    """ログを画面と logs/bot.log に出す（ファイルは1MBで切り替え、5世代まで残す）。"""
    settings.log_dir.mkdir(exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = RotatingFileHandler(settings.log_dir / "bot.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.handlers = [file_handler, console]
    logger.setLevel(settings.log_level)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def check_environment(settings: Settings) -> bool:
    ok = True
    print("=== Saize Health Bot 環境チェック ===")

    version_ok = sys.version_info >= MIN_PYTHON
    ok &= version_ok
    print(f"Python      : {platform.python_version()} {'OK' if version_ok else 'NG（3.12以上が必要）'}")
    print(f"OS          : {platform.system()} {platform.release()}")
    print(f"仮想環境    : {'有効' if sys.prefix != sys.base_prefix else '無効（.venv を有効化してください）'}")

    for module in ("httpx", "dotenv", "truststore", "tzdata"):
        try:
            __import__(module)
            print(f"{module:<12}: OK")
        except ImportError:
            ok = False
            print(f"{module:<12}: 未インストール（pip install -r requirements.txt）")

    print(f"Misskeyホスト: {settings.misskey_host if settings.has_host else '未設定'}")
    print(f"トークン    : {'設定済み' if settings.has_token else '未設定'}")  # 値そのものは表示しない
    print(f"投稿        : {'有効' if settings.enable_posting else '無効（表示だけ）'}")
    print(f"公開範囲    : {settings.post_visibility}{'・連合なし' if settings.post_local_only else ''}")
    print(f"投稿時刻    : {settings.post_times_text}（{settings.timezone}）")
    print(f"DBパス      : {settings.db_path}")
    print("結果        :", "問題なし" if ok else "要確認")
    return ok


def check_account(settings: Settings) -> bool:
    from modules.misskey import MisskeyClient, MisskeyError

    if not (settings.has_host and settings.has_token):
        print("Misskeyの確認：MISSKEY_HOST と MISSKEY_TOKEN を .env に設定してください")
        return False
    try:
        with MisskeyClient(settings.misskey_host, settings.misskey_token, timeout=settings.http_timeout) as client:
            me = client.get_me()
    except MisskeyError as exc:
        print(f"Misskeyの確認：失敗 — {exc}")
        return False
    print(f"Misskeyの確認：OK（@{me.get('username')}・Botフラグ {'オン' if me.get('isBot') else 'オフ'}）")
    if not me.get("isBot"):
        print("  ※ プロフィール設定で Bot フラグをオンにすることが推奨されています")
    return True


# ---------------------------------------------------------------------------
# post
# ---------------------------------------------------------------------------


def run_post(settings: Settings, *, slot_text: str | None, dry_run: bool, seed: int | None = None) -> int:
    from database.database import HistoryStore
    from modules.misskey import MisskeyClient, MisskeyError
    from modules.nutrition import NutritionData
    from modules.post_text import build_post_text
    from modules.pricing import PriceTable
    from modules.recommendation import BudgetTooLowError, recommend, record

    slot = parse_post_times(slot_text)[0] if slot_text else None
    will_post = settings.enable_posting and not dry_run
    data, prices = NutritionData.load(), PriceTable.load()
    now = datetime.now(ZoneInfo(settings.timezone))

    # 投稿しないときに履歴ファイルがまだ無ければ、空のファイルを作らないようメモリ上で済ませる
    db_path = settings.db_path if (will_post or settings.db_path.exists()) else ":memory:"
    with HistoryStore(db_path) as history:
        try:
            rec = recommend(
                data, history, now,
                combo_days=settings.no_repeat_combo_days,
                staple_days=settings.no_repeat_staple_days,
                main_days=settings.no_repeat_main_days,
                rng=random.Random(seed),
                prices=prices,
                budget_yen=settings.post_budget_yen,
            )
        except BudgetTooLowError as exc:
            logger.error("おすすめを作れませんでした: %s", exc)
            return 1
        for note in rec.relaxed_rules:
            logger.warning("重複防止を緩めました: %s", note)

        text = build_post_text(rec, data, slot=slot, max_chars=settings.post_max_chars,
                               hashtags=settings.post_hashtags, prices=prices)

        if not will_post:
            print(text)
            reason = "--dry-run" if dry_run else "ENABLE_POSTING=false"
            print(f"\n（投稿していません：{reason}。履歴にも記録していません）")
            return 0

        if not (settings.has_host and settings.has_token):
            logger.error("MISSKEY_HOST と MISSKEY_TOKEN を .env に設定してください")
            return 1
        try:
            with MisskeyClient(settings.misskey_host, settings.misskey_token, timeout=settings.http_timeout) as client:
                note = client.create_note(text, visibility=settings.post_visibility, local_only=settings.post_local_only)
        except MisskeyError as exc:
            # 投稿に失敗したときは履歴に記録しない（投稿していない献立を「最近出した」扱いにしないため）
            logger.error("投稿に失敗しました: %s", exc)
            return 1

        record(history, rec, now, slot=slot_text)
        logger.info("投稿しました: https://%s/notes/%s （%s）", settings.misskey_host, note.get("id"), " + ".join(rec.names))
    return 0


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Saize Health Bot")
    sub = parser.add_subparsers(dest="command")
    check = sub.add_parser("check", help="環境チェック")
    check.add_argument("--account", action="store_true", help="Misskey につながるかも確認する（通信する）")
    post = sub.add_parser("post", help="おすすめを投稿する")
    post.add_argument("--slot", default=None, help="投稿枠（例 11:00）。書き出しの文が変わる")
    post.add_argument("--dry-run", action="store_true", help="投稿せずに表示だけする")
    post.add_argument("--seed", type=int, default=None, help="乱数の種（確認用）")
    args = parser.parse_args(argv)

    settings = load_settings()
    setup_logging(settings)

    if args.command == "post":
        return run_post(settings, slot_text=args.slot, dry_run=args.dry_run, seed=args.seed)

    # 引数なし、または check
    ok = check_environment(settings)
    if args.command == "check" and args.account:
        ok &= check_account(settings)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
