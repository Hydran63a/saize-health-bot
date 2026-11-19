"""Saize Health Bot エントリーポイント。

使い方:
    python main.py check              # 環境チェック（通信しない）
    python main.py check --account    # Misskey につながるか確認（POST /api/i）
    python main.py post --slot 11:00  # おすすめを作って投稿する
                                      #   ENABLE_POSTING=false のときや --dry-run では投稿せず表示だけ
    python main.py post --slot auto   # 自動実行用：今の時刻から枠を決める（systemd タイマーから呼ぶ）
                                      #   同じ日・同じ枠ですでに投稿していれば何もしない
"""

from __future__ import annotations

import argparse
import logging
import platform
import random
import sys
import time
from datetime import datetime, timedelta
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


SLOT_TOLERANCE = timedelta(minutes=30)  # --slot auto：枠の時刻からこれ以上ずれていたら投稿しない
RETRY_DELAY_SECONDS = 300  # 一時的な失敗のとき、やり直すまで待つ秒数（5分）
MAX_RETRY_AFTER_SECONDS = 600  # 429 の Retry-After がこれより長ければ、やり直さない


def resolve_slot(now: datetime, post_times: tuple[tuple[int, int], ...],
                 tolerance: timedelta = SLOT_TOLERANCE) -> tuple[int, int] | None:
    """今の時刻に近い投稿枠を返す。どの枠からも tolerance 以上離れていれば None。"""
    for hour, minute in post_times:
        slot_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if abs(now - slot_time) < tolerance:
            return hour, minute
    return None


def post_with_retry(create, *, sleep=time.sleep):
    """create() を実行し、一時的な失敗なら1回だけ待ってやり直す。

    認証の失敗・権限不足・文章の問題は、やり直しても直らないのですぐに失敗にする。
    """
    from modules.misskey import AuthenticationError, MisskeyError, PermissionDeniedError, RateLimitError

    try:
        return create()
    except (AuthenticationError, PermissionDeniedError):
        raise
    except RateLimitError as exc:
        wait = exc.retry_after if exc.retry_after is not None else RETRY_DELAY_SECONDS
        if wait > MAX_RETRY_AFTER_SECONDS:
            raise
        logger.warning("回数制限のため %d 秒待ってやり直します", wait)
        sleep(wait)
    except MisskeyError as exc:
        if exc.status is not None and exc.status < 500:
            raise  # 400 など（送った内容の問題）はやり直しても同じ
        logger.warning("一時的な失敗のため %d 秒待ってやり直します: %s", RETRY_DELAY_SECONDS, exc)
        sleep(RETRY_DELAY_SECONDS)
    return create()


def run_post(
    settings: Settings,
    *,
    slot_text: str | None,
    dry_run: bool,
    seed: int | None = None,
    now: datetime | None = None,
    sleep=time.sleep,
) -> int:
    from database.database import HistoryStore
    from modules.misskey import MisskeyClient, MisskeyError
    from modules.nutrition import NutritionData
    from modules.post_text import build_post_text
    from modules.pricing import PriceTable
    from modules.recommendation import BudgetTooLowError, recommend, record

    now = now or datetime.now(ZoneInfo(settings.timezone))
    will_post = settings.enable_posting and not dry_run

    # 投稿枠を決める（auto なら今の時刻から。遅れすぎていたら投稿しない）
    if slot_text == "auto":
        slot = resolve_slot(now, settings.post_times)
        if slot is None:
            logger.info("投稿枠（%s）の時刻ではないため、投稿しません（%s）",
                        settings.post_times_text, now.strftime("%H:%M"))
            return 0
    else:
        slot = parse_post_times(slot_text)[0] if slot_text else None
    slot_label = f"{slot[0]:02d}:{slot[1]:02d}" if slot else None

    data, prices = NutritionData.load(), PriceTable.load()

    # 投稿しないときに履歴ファイルがまだ無ければ、空のファイルを作らないようメモリ上で済ませる
    db_path = settings.db_path if (will_post or settings.db_path.exists()) else ":memory:"
    with HistoryStore(db_path) as history:
        # 二重投稿の防止：今日、同じ枠ですでに投稿していれば何もしない
        start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if will_post and slot_label and history.posted_in_slot_since(slot_label, start_of_today):
            logger.info("今日の %s の枠はすでに投稿済みのため、投稿しません", slot_label)
            return 0

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
                note = post_with_retry(
                    lambda: client.create_note(
                        text, visibility=settings.post_visibility, local_only=settings.post_local_only
                    ),
                    sleep=sleep,
                )
        except MisskeyError as exc:
            # 投稿に失敗したときは履歴に記録しない（投稿していない献立を「最近出した」扱いにしないため）
            logger.error("投稿に失敗しました: %s", exc)
            return 1

        record(history, rec, now, slot=slot_label)
        logger.info("投稿しました: https://%s/notes/%s （%s）", settings.misskey_host, note.get("id"), " + ".join(rec.names))
    return 0


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Saize Health Bot")
    sub = parser.add_subparsers(dest="command")
    check = sub.add_parser("check", help="環境チェック")
    check.add_argument("--account", action="store_true", help="Misskey につながるかも確認する（通信する）")
    post = sub.add_parser("post", help="おすすめを投稿する")
    post.add_argument("--slot", default=None,
                      help="投稿枠（例 11:00）。auto なら今の時刻から決める（枠から30分以上ずれていたら投稿しない）")
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
