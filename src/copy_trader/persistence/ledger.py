"""SQLite-backed trade ledger（pnl-single-source spec 实装）。

`TradesRepo` 是 issue #8 的核心：
- 把每一笔 `Fill` 作为不可变记录写入 `db/trades.db`
- 用 `PRAGMA user_version` 表达 schema_version（建表即写 2，spec 当前版本）
- 金额（qty / price / fee）用 TEXT 存 `Decimal` 字符串，避免 SQLite REAL 浮点丢精度
- 写入前校验同 account 上一行 `schema_version >= 2` 的 `(env_tag, machine_id)`，
  与当前 `(self.env_tag, self.machine_id)` 不一致 → `CrossEnvironmentWriteError`
- legacy 行（`schema_version == 1`）可读，但不参与新写入的环境校验

依赖：仅 stdlib（sqlite3 / pathlib / decimal / datetime）+ `copy_trader.core.Fill`，
满足 import-linter `persistence-only-core` 契约。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from copy_trader.core import Fill

__all__ = ["CrossEnvironmentWriteError", "TradesRepo"]

#: spec 当前版本；写表时通过 `PRAGMA user_version = SCHEMA_VERSION` 写入 SQLite header
#: v3 (issue #25): 加 runner_id 列, NOT NULL DEFAULT 'legacy'。已有 v2 库通过
#: `_migrate_v2_to_v3` ALTER TABLE 升级。
SCHEMA_VERSION = 3

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL,
    ts TEXT NOT NULL,
    account TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty TEXT NOT NULL,
    price TEXT NOT NULL,
    fee TEXT NOT NULL,
    fee_asset TEXT NOT NULL,
    exchange_order_id TEXT NOT NULL,
    env_tag TEXT NOT NULL,
    machine_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    runner_id TEXT NOT NULL DEFAULT 'legacy'
)
"""

_CREATE_INDEX_ACCOUNT_SYMBOL_TS_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_trades_account_symbol_ts ON trades(account, symbol, ts)"
)
_CREATE_INDEX_ACCOUNT_SCHEMA_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_trades_account_schema ON trades(account, schema_version)"
)


class CrossEnvironmentWriteError(Exception):
    """同 account 历史 (env_tag, machine_id) 与当前写入进程不一致时抛出。

    spec `pnl-single-source` Requirement「Ledger writes are stamped and gated by
    env_tag and machine_id」明确：
    新写入 ledger 行 MUST 与同账户上一行 `schema_version >= 2` 的
    `(env_tag, machine_id)` 一致；不一致 MUST 被拒绝。
    """


class TradesRepo:
    """SQLite ledger 仓库。

    使用约定：单进程内重用一个实例；连接在 `__init__` 阶段建立并保持，析构时关闭。
    库内只对 `qty / price / fee` 做 `Decimal ↔ str` 转换；`ts` 以 ISO8601 字符串写入。
    """

    def __init__(self, db_path: Path, env_tag: str, machine_id: str) -> None:
        self._db_path = db_path
        self.env_tag = env_tag
        self.machine_id = machine_id
        # 父目录可能不存在（首次 bootstrap）
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # `detect_types` 留默认（关闭），所有类型映射手动做 → 行为可预测
        self._conn: sqlite3.Connection = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    # --- schema --------------------------------------------------------

    def _ensure_schema(self) -> None:
        """幂等建表 + v2→v3 迁移 (加 runner_id 列)。"""
        cur = self._conn.cursor()
        cur.execute(_CREATE_TABLE_SQL)
        cur.execute(_CREATE_INDEX_ACCOUNT_SYMBOL_TS_SQL)
        cur.execute(_CREATE_INDEX_ACCOUNT_SCHEMA_SQL)
        # 检测已有库的 user_version；若 < 3 走 ALTER TABLE 加 runner_id 列。
        # 新建库由 _CREATE_TABLE_SQL 已含 runner_id 列,直接跳。
        cur.execute("PRAGMA user_version")
        row = cur.fetchone()
        current = int(row[0]) if row else 0
        if current < 3:
            self._migrate_v2_to_v3(cur)
        # PRAGMA user_version 不接受参数化绑定，需要内联整型字面量；这里值是常量、安全。
        cur.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
        self._conn.commit()

    def _migrate_v2_to_v3(self, cur: sqlite3.Cursor) -> None:
        """已有 v2 库加 runner_id 列。新建库不会进入 (CREATE TABLE 已含此列)。"""
        cur.execute("PRAGMA table_info(trades)")
        cols = {row[1] for row in cur.fetchall()}
        if "runner_id" not in cols:
            cur.execute("ALTER TABLE trades ADD COLUMN runner_id TEXT NOT NULL DEFAULT 'legacy'")

    @property
    def schema_version(self) -> int:
        """读取 SQLite `PRAGMA user_version`，即当前 db 文件的 schema_version。"""
        cur = self._conn.cursor()
        cur.execute("PRAGMA user_version")
        row = cur.fetchone()
        return int(row[0]) if row is not None else 0

    # --- write ---------------------------------------------------------

    def insert(self, fill: Fill) -> None:
        """写入一条 `Fill`；写之前做跨环境校验。

        校验语义（参见 spec Requirement「stamped and gated by env_tag and machine_id」）：
        - 查同 `account` 上一行 `schema_version >= 2` 的 `(env_tag, machine_id)`
        - 与当前 `(self.env_tag, self.machine_id)` 不一致 → `CrossEnvironmentWriteError`
        - legacy 行（`schema_version == 1`）忽略，不参与校验
        """
        self._guard_cross_environment(fill.account)

        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO trades (
                id, ts, account, symbol, side, qty, price, fee, fee_asset,
                exchange_order_id, env_tag, machine_id, schema_version, runner_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill.id,
                fill.ts.isoformat(),
                fill.account,
                fill.symbol,
                fill.side,
                str(fill.qty),
                str(fill.price),
                str(fill.fee),
                fill.fee_asset,
                fill.exchange_order_id,
                fill.env_tag,
                fill.machine_id,
                int(fill.schema_version),
                fill.runner_id,
            ),
        )
        self._conn.commit()

    def _guard_cross_environment(self, account: str) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT env_tag, machine_id
            FROM trades
            WHERE account = ? AND schema_version >= 2
            ORDER BY row_id DESC
            LIMIT 1
            """,
            (account,),
        )
        row = cur.fetchone()
        if row is None:
            return
        prior_env, prior_machine = row["env_tag"], row["machine_id"]
        if prior_env != self.env_tag or prior_machine != self.machine_id:
            raise CrossEnvironmentWriteError(
                "cross_environment_write: account="
                f"{account!r} prior=(env_tag={prior_env!r}, "
                f"machine_id={prior_machine!r}) current=(env_tag="
                f"{self.env_tag!r}, machine_id={self.machine_id!r})"
            )

    # --- read ----------------------------------------------------------

    def fetch(
        self,
        account: str,
        symbol: str,
        since: datetime | None = None,
    ) -> list[Fill]:
        """按 `account + symbol` 拉取 fills，可选 `since` 起点（含等于）。

        排序按 `ts` 升序；金额从 TEXT 还原为 `Decimal`，无精度损失。
        """
        cur = self._conn.cursor()
        if since is None:
            cur.execute(
                """
                SELECT id, ts, account, symbol, side, qty, price, fee, fee_asset,
                       exchange_order_id, env_tag, machine_id, schema_version, runner_id
                FROM trades
                WHERE account = ? AND symbol = ?
                ORDER BY ts ASC, row_id ASC
                """,
                (account, symbol),
            )
        else:
            cur.execute(
                """
                SELECT id, ts, account, symbol, side, qty, price, fee, fee_asset,
                       exchange_order_id, env_tag, machine_id, schema_version, runner_id
                FROM trades
                WHERE account = ? AND symbol = ? AND ts >= ?
                ORDER BY ts ASC, row_id ASC
                """,
                (account, symbol, since.isoformat()),
            )
        rows = cur.fetchall()
        return [self._row_to_fill(row) for row in rows]

    @staticmethod
    def _row_to_fill(row: sqlite3.Row) -> Fill:
        # row["runner_id"] 在 v2 迁移到 v3 后默认 'legacy'
        try:
            runner_id = row["runner_id"]
        except (KeyError, IndexError):
            runner_id = "legacy"
        return Fill(
            id=row["id"],
            ts=datetime.fromisoformat(row["ts"]),
            account=row["account"],
            symbol=row["symbol"],
            side=row["side"],
            qty=Decimal(row["qty"]),
            price=Decimal(row["price"]),
            fee=Decimal(row["fee"]),
            fee_asset=row["fee_asset"],
            exchange_order_id=row["exchange_order_id"],
            env_tag=row["env_tag"],
            machine_id=row["machine_id"],
            schema_version=int(row["schema_version"]),
            runner_id=runner_id,
        )

    # --- lifecycle -----------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> TradesRepo:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
