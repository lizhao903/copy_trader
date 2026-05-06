"""SQLite klines 缓存层（issue #23）。

`KlineCache` 是单进程 SQLite 持久化, 表 schema:

    klines (
        venue TEXT, symbol TEXT, interval TEXT,
        open_ts TEXT,    -- ISO8601 UTC
        open TEXT, high TEXT, low TEXT, close TEXT, volume TEXT,
        close_ts TEXT,
        PRIMARY KEY (venue, symbol, interval, open_ts)
    )

`CachingKlineSource` 是装饰器: 把任意 `KlineSource` 包成命中缓存优先的版本。
fetch 时先按 (symbol, interval, 末 limit 个 open_ts) 查缓存; 命中即返回, miss
则调 wraps + 写穿透。这样 backtest / live / paper 都能用同一份缓存。

注意: 本模块**不**自动决定 cache 路径; 由调用方注入 db_path
(`$COPY_TRADER_HOME/db/klines.db`, runtime-isolation spec)。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

from copy_trader.marketdata.base import Kline, KlineSource

__all__ = [
    "CachingKlineSource",
    "KlineCache",
]

# `PRAGMA user_version` schema version. issue #8 ledger 用 2; 本表独立计数, 从 1 开始。
_KLINES_SCHEMA_VERSION: Final[int] = 1

_CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS klines (
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    open_ts TEXT NOT NULL,
    open TEXT NOT NULL,
    high TEXT NOT NULL,
    low TEXT NOT NULL,
    close TEXT NOT NULL,
    volume TEXT NOT NULL,
    close_ts TEXT NOT NULL,
    PRIMARY KEY (venue, symbol, interval, open_ts)
);
"""

_CREATE_INDEX_SQL = """\
CREATE INDEX IF NOT EXISTS idx_klines_venue_symbol_interval
    ON klines (venue, symbol, interval, open_ts DESC);
"""


class KlineCache:
    """SQLite klines 缓存 repo。线程不安全,单进程使用。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.execute(_CREATE_INDEX_SQL)
        self._conn.execute(f"PRAGMA user_version = {_KLINES_SCHEMA_VERSION}")
        self._conn.commit()

    @property
    def schema_version(self) -> int:
        cur = self._conn.execute("PRAGMA user_version")
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        self._conn.close()

    def write(self, venue: str, symbol: str, interval: str, klines: Iterable[Kline]) -> int:
        """写入 klines, 主键冲突用 INSERT OR REPLACE。返回写入行数。"""
        rows = [
            (
                venue,
                symbol,
                interval,
                k.open_ts.astimezone(UTC).isoformat(),
                str(k.open),
                str(k.high),
                str(k.low),
                str(k.close),
                str(k.volume),
                k.close_ts.astimezone(UTC).isoformat(),
            )
            for k in klines
        ]
        if not rows:
            return 0
        self._conn.executemany(
            "INSERT OR REPLACE INTO klines "
            "(venue, symbol, interval, open_ts, open, high, low, close, volume, close_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def fetch(
        self,
        venue: str,
        symbol: str,
        interval: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Kline]:
        """按 venue/symbol/interval 读缓存; since 可选下界, limit 取最新 N 根。"""
        sql = (
            "SELECT open_ts, open, high, low, close, volume, close_ts "
            "FROM klines WHERE venue = ? AND symbol = ? AND interval = ?"
        )
        params: list[object] = [venue, symbol, interval]
        if since is not None:
            sql += " AND open_ts >= ?"
            params.append(since.astimezone(UTC).isoformat())
        sql += " ORDER BY open_ts ASC"
        if limit is not None and limit > 0:
            # 取最新 limit 条 → DESC LIMIT 后再 reverse
            sql = (
                "SELECT * FROM ("
                + sql.replace("ORDER BY open_ts ASC", "ORDER BY open_ts DESC")
                + " LIMIT ?) ORDER BY open_ts ASC"
            )
            params.append(limit)
        cur = self._conn.execute(sql, params)
        rows = cur.fetchall()
        return [
            Kline(
                open_ts=datetime.fromisoformat(r[0]),
                open=Decimal(r[1]),
                high=Decimal(r[2]),
                low=Decimal(r[3]),
                close=Decimal(r[4]),
                volume=Decimal(r[5]),
                close_ts=datetime.fromisoformat(r[6]),
            )
            for r in rows
        ]

    def count(self, venue: str, symbol: str, interval: str) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM klines WHERE venue = ? AND symbol = ? AND interval = ?",
            (venue, symbol, interval),
        )
        return int(cur.fetchone()[0])


class CachingKlineSource:
    """`KlineSource` 装饰器: 命中缓存优先, miss 时调 wraps 并写穿透。"""

    def __init__(self, wraps: KlineSource, cache: KlineCache) -> None:
        self._wraps = wraps
        self._cache = cache

    @property
    def name(self) -> str:
        return self._wraps.name

    def fetch_klines(self, symbol: str, interval: str, limit: int = 100) -> list[Kline]:
        venue = self._wraps.name
        cached = self._cache.fetch(venue, symbol, interval, limit=limit)
        if len(cached) >= limit:
            return cached[-limit:]
        # miss 或部分命中 → 走 wraps 拉一次 + 写缓存
        fresh = self._wraps.fetch_klines(symbol, interval, limit)
        if fresh:
            self._cache.write(venue, symbol, interval, fresh)
        return fresh
