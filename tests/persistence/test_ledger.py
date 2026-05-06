"""SQLite ledger 测试（issue #8 acceptance）。

覆盖六个场景：
1. schema 创建幂等（再次实例化不报错、`PRAGMA user_version` 仍 = 2）
2. insert + fetch 往返（Decimal 不丢精度）
3. 跨 `env_tag` 写入被拒（`CrossEnvironmentWriteError`，错误消息含两侧 env_tag）
4. 跨 `machine_id` 写入被拒（同上，machine_id 维度）
5. legacy 行（`schema_version=1`）不参与新写入校验
6. `fetch(since=...)` 仅返回 ts ≥ since 的行
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from copy_trader.core import Fill
from copy_trader.persistence import (
    SCHEMA_VERSION,
    CrossEnvironmentWriteError,
    TradesRepo,
)
from copy_trader.persistence.ledger import _CREATE_TABLE_SQL  # type: ignore[attr-defined]


def _make_fill(
    *,
    id_: str = "f-1",
    ts: datetime | None = None,
    account: str = "acc-A",
    symbol: str = "BTCUSDT",
    side: str = "buy",
    qty: str = "0.12345678",
    price: str = "67890.12345678",
    fee: str = "0.00012345",
    fee_asset: str = "USDT",
    exchange_order_id: str = "x-100",
    env_tag: str = "dev",
    machine_id: str = "host-1",
    schema_version: int = SCHEMA_VERSION,
) -> Fill:
    return Fill(
        id=id_,
        ts=ts if ts is not None else datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        account=account,
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        qty=Decimal(qty),
        price=Decimal(price),
        fee=Decimal(fee),
        fee_asset=fee_asset,
        exchange_order_id=exchange_order_id,
        env_tag=env_tag,
        machine_id=machine_id,
        schema_version=schema_version,
    )


def _table_columns(db_path: Path) -> list[tuple[str, str]]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("PRAGMA table_info(trades)").fetchall()
        # PRAGMA table_info 返回 (cid, name, type, notnull, dflt_value, pk)
        return [(row[1], row[2]) for row in rows]
    finally:
        conn.close()


def _read_user_version(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0])
    finally:
        conn.close()


# --- 1. schema 创建幂等 -------------------------------------------------


def test_schema_creation_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    repo1 = TradesRepo(db, env_tag="dev", machine_id="host-1")
    cols_first = _table_columns(db)
    assert _read_user_version(db) == SCHEMA_VERSION
    repo1.close()

    # 再次实例化：不应抛错；schema 与 user_version 保持不变
    repo2 = TradesRepo(db, env_tag="dev", machine_id="host-1")
    cols_second = _table_columns(db)
    assert cols_second == cols_first
    assert _read_user_version(db) == SCHEMA_VERSION
    assert repo2.schema_version == SCHEMA_VERSION
    repo2.close()


def test_create_table_sql_is_idempotent_constant() -> None:
    # 简单的反向防回归：CREATE 语句必须含 IF NOT EXISTS
    assert "IF NOT EXISTS" in _CREATE_TABLE_SQL


# --- 2. insert + fetch 往返 --------------------------------------------


def test_insert_and_fetch_roundtrip_preserves_decimal(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    repo = TradesRepo(db, env_tag="dev", machine_id="host-1")
    fill = _make_fill(qty="0.123456789012345678", price="12345.6789", fee="0.0000001")

    repo.insert(fill)
    fetched = repo.fetch(account="acc-A", symbol="BTCUSDT")

    assert len(fetched) == 1
    got = fetched[0]
    # 字段 100% 一致
    assert got == fill
    # Decimal 精度无损
    assert got.qty == Decimal("0.123456789012345678")
    assert got.price == Decimal("12345.6789")
    assert got.fee == Decimal("0.0000001")
    repo.close()


# --- 3. 跨 env_tag 写入拒绝 --------------------------------------------


def test_insert_rejects_cross_env_tag(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    repo_dev = TradesRepo(db, env_tag="dev", machine_id="host-1")
    repo_dev.insert(_make_fill(id_="f-1", env_tag="dev", machine_id="host-1"))
    repo_dev.close()

    repo_paper = TradesRepo(db, env_tag="paper", machine_id="host-1")
    with pytest.raises(CrossEnvironmentWriteError) as exc_info:
        repo_paper.insert(_make_fill(id_="f-2", env_tag="paper", machine_id="host-1"))
    msg = str(exc_info.value)
    # 错误消息必须列出两侧 env_tag，方便人工识别
    assert "dev" in msg
    assert "paper" in msg
    assert "env_tag" in msg
    repo_paper.close()


# --- 4. 跨 machine_id 写入拒绝 -----------------------------------------


def test_insert_rejects_cross_machine_id(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    repo_a = TradesRepo(db, env_tag="dev", machine_id="host-A")
    repo_a.insert(_make_fill(id_="f-1", env_tag="dev", machine_id="host-A"))
    repo_a.close()

    repo_b = TradesRepo(db, env_tag="dev", machine_id="host-B")
    with pytest.raises(CrossEnvironmentWriteError) as exc_info:
        repo_b.insert(_make_fill(id_="f-2", env_tag="dev", machine_id="host-B"))
    msg = str(exc_info.value)
    assert "host-A" in msg
    assert "host-B" in msg
    assert "machine_id" in msg
    repo_b.close()


# --- 5. legacy 行不参与校验 ---------------------------------------------


def test_legacy_schema_v1_row_does_not_block_new_write(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    # 初始化 schema（让 TradesRepo 建表，并保证 user_version = SCHEMA_VERSION）
    repo_init = TradesRepo(db, env_tag="dev", machine_id="host-old")
    repo_init.close()

    # 手工 INSERT 一条 schema_version=1 的 legacy 行（模拟早期 backfill 数据）
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """
            INSERT INTO trades (
                id, ts, account, symbol, side, qty, price, fee, fee_asset,
                exchange_order_id, env_tag, machine_id, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-1",
                datetime(2025, 12, 1, tzinfo=UTC).isoformat(),
                "acc-A",
                "BTCUSDT",
                "buy",
                "0.1",
                "60000",
                "0.0001",
                "USDT",
                "x-legacy",
                "ancient-env",
                "ancient-host",
                1,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # 用全新 (env_tag, machine_id) 写入：legacy 行不应阻断
    repo_new = TradesRepo(db, env_tag="prod", machine_id="host-new")
    repo_new.insert(_make_fill(id_="f-new", env_tag="prod", machine_id="host-new"))
    rows = repo_new.fetch(account="acc-A", symbol="BTCUSDT")
    # legacy + 新行都能 fetch 出来（fetch 不区分 schema_version）
    assert len(rows) == 2
    schema_versions = sorted(r.schema_version for r in rows)
    assert schema_versions == [1, SCHEMA_VERSION]
    repo_new.close()


# --- 6. fetch since 过滤 ------------------------------------------------


def test_fetch_since_filters_old_rows(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    repo = TradesRepo(db, env_tag="dev", machine_id="host-1")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    ts1 = base
    ts2 = base + timedelta(hours=1)
    ts3 = base + timedelta(hours=2)
    repo.insert(_make_fill(id_="f-1", ts=ts1))
    repo.insert(_make_fill(id_="f-2", ts=ts2))
    repo.insert(_make_fill(id_="f-3", ts=ts3))

    got = repo.fetch(account="acc-A", symbol="BTCUSDT", since=ts2)
    assert [f.id for f in got] == ["f-2", "f-3"]

    got_all = repo.fetch(account="acc-A", symbol="BTCUSDT")
    assert [f.id for f in got_all] == ["f-1", "f-2", "f-3"]
    repo.close()
