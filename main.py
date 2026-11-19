"""Saize Health Bot エントリーポイント。

Phase 1 時点では、開発環境が正しく整っているかを表示するだけ。
メニュー取得・Misskey 連携はまだ実装していない。
"""

from __future__ import annotations

import platform
import sys

from config import load_settings

MIN_PYTHON = (3, 12)


def check_environment() -> bool:
    ok = True
    print("=== Saize Health Bot 環境チェック ===")

    version_ok = sys.version_info >= MIN_PYTHON
    ok &= version_ok
    print(f"Python      : {platform.python_version()} {'OK' if version_ok else 'NG（3.12以上が必要）'}")
    print(f"OS          : {platform.system()} {platform.release()}")
    print(f"仮想環境    : {'有効' if sys.prefix != sys.base_prefix else '無効（.venv を有効化してください）'}")

    for module in ("httpx", "dotenv"):
        try:
            __import__(module)
            print(f"{module:<12}: OK")
        except ImportError:
            ok = False
            print(f"{module:<12}: 未インストール")

    settings = load_settings()
    print(f"Misskeyホスト: {settings.misskey_host or '未設定'}")
    print(f"トークン    : {'設定済み' if settings.has_token else '未設定'}")  # 値そのものは表示しない
    print(f"投稿        : {'有効' if settings.enable_posting else '無効'}")
    print(f"投稿時刻    : {settings.post_times_text}（{settings.timezone}）")
    print(f"DBパス      : {settings.db_path}")

    print("結果        :", "問題なし" if ok else "要確認")
    return ok


def main() -> int:
    return 0 if check_environment() else 1


if __name__ == "__main__":
    sys.exit(main())
