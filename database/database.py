"""おすすめ履歴の保存（SQLite）。

重複防止のために「いつ・どの組み合わせを・どの主食で」おすすめしたかを記録する。
SQLite は Python 標準の sqlite3 で使える、ファイル1つのデータベース。
テストでは path に ":memory:" を渡すと、メモリ上の使い捨てデータベースになる。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS recommendation_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,   -- UTC の ISO 形式（文字列のまま大小比較できる）
    slot        TEXT,            -- 投稿枠（例 "11:00"）
    pattern     TEXT NOT NULL,   -- おすすめの種類（例 "balance"）
    combo_key   TEXT NOT NULL,   -- 組み合わせの識別子（メニューIDを並べ替えて連結）
    staple_id   TEXT NOT NULL,   -- 主食のメニューID
    menu_ids    TEXT NOT NULL,   -- メニューIDの一覧（JSON）
    main_id     TEXT             -- 主菜のメニューID（主菜なしの献立は NULL）
);
CREATE INDEX IF NOT EXISTS idx_history_created_at ON recommendation_history (created_at);
"""

# 後から追加した列。古い履歴ファイルにはこの列が無いので、開くときに足す（マイグレーション）
ADDED_COLUMNS = {"main_id": "TEXT"}


def make_combo_key(menu_ids: list[str]) -> str:
    """並び順が違っても同じ組み合わせなら同じキーになるよう、並べ替えて連結する。"""
    return "|".join(sorted(menu_ids))


def _to_utc_text(moment: datetime) -> str:
    if moment.tzinfo is None:
        # タイムゾーンの無い日時は、日本時間か UTC か分からず比較を誤るので受け付けない
        raise ValueError("タイムゾーン付きの日時を渡してください")
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


class HistoryStore:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """CREATE TABLE IF NOT EXISTS は既存の表を変えないので、足りない列をここで追加する。"""
        existing = {row[1] for row in self.conn.execute("PRAGMA table_info(recommendation_history)")}
        with self.conn:
            for name, sql_type in ADDED_COLUMNS.items():
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE recommendation_history ADD COLUMN {name} {sql_type}")

    # with 文で使えるようにする（終わったら自動で close される）
    def __enter__(self) -> "HistoryStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def add(
        self,
        *,
        menu_ids: list[str],
        staple_id: str,
        pattern: str,
        slot: str | None,
        created_at: datetime,
        main_id: str | None = None,
    ) -> None:
        # 値は「?」で渡す（文字列を直接つなげて SQL を作ると、SQLインジェクションの原因になる）
        with self.conn:
            self.conn.execute(
                "INSERT INTO recommendation_history (created_at, slot, pattern, combo_key, staple_id, menu_ids, main_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    _to_utc_text(created_at),
                    slot,
                    pattern,
                    make_combo_key(menu_ids),
                    staple_id,
                    json.dumps(menu_ids, ensure_ascii=False),
                    main_id,
                ),
            )

    def combo_keys_since(self, since: datetime) -> set[str]:
        rows = self.conn.execute(
            "SELECT combo_key FROM recommendation_history WHERE created_at >= ?", (_to_utc_text(since),)
        )
        return {row[0] for row in rows}

    def staples_since(self, since: datetime) -> set[str]:
        rows = self.conn.execute(
            "SELECT staple_id FROM recommendation_history WHERE created_at >= ?", (_to_utc_text(since),)
        )
        return {row[0] for row in rows}

    def mains_since(self, since: datetime) -> set[str]:
        rows = self.conn.execute(
            "SELECT main_id FROM recommendation_history WHERE created_at >= ? AND main_id IS NOT NULL",
            (_to_utc_text(since),),
        )
        return {row[0] for row in rows}

    def posted_in_slot_since(self, slot: str, since: datetime) -> bool:
        """since 以降に、同じ投稿枠（例 "11:00"）の記録があるか。二重投稿の防止に使う。"""
        row = self.conn.execute(
            "SELECT 1 FROM recommendation_history WHERE slot = ? AND created_at >= ? LIMIT 1",
            (slot, _to_utc_text(since)),
        ).fetchone()
        return row is not None

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM recommendation_history").fetchone()[0]
