"""RunnerRegistry CRUD + schema 迁移幂等 + 心跳 (issue #25)。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

import pytest

from copy_trader.core import RunnerInstance
from copy_trader.persistence import (
    DuplicateRunnerNameError,
    RunnerNotFoundError,
    RunnerRegistry,
)


def _make_runner(
    *,
    id: str = "rid-001",
    name: str = "hello-spot",
    venue: str = "binance.spot",
    account: str = "spot",
    strategy: str = "hello",
    mode: str = "dry-run",
    status: str = "draft",
    pid: int | None = None,
    last_heartbeat: datetime | None = None,
    params_override: dict[str, Any] | None = None,
) -> RunnerInstance:
    now = datetime.now(UTC)
    return RunnerInstance(
        id=id,
        name=name,
        venue=venue,
        account=account,
        strategy=strategy,
        params_override=params_override or {},
        mode=mode,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        pid=pid,
        last_heartbeat=last_heartbeat,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def registry(tmp_path: Any) -> RunnerRegistry:
    return RunnerRegistry(tmp_path / "registry.db")


# ---------- schema ----------


def test_schema_version_is_1(registry: RunnerRegistry) -> None:
    assert registry.schema_version == 1


def test_schema_idempotent(tmp_path: Any) -> None:
    db = tmp_path / "registry.db"
    r1 = RunnerRegistry(db)
    r1.create(_make_runner())
    r1.close()
    # 二次打开,不报错,数据保留
    r2 = RunnerRegistry(db)
    out = r2.list()
    assert len(out) == 1
    assert out[0].name == "hello-spot"


# ---------- CRUD ----------


def test_create_and_get_by_id(registry: RunnerRegistry) -> None:
    runner = _make_runner()
    registry.create(runner)
    got = registry.get(id="rid-001")
    assert got.name == "hello-spot"
    assert got.venue == "binance.spot"
    assert got.params_override == {}


def test_create_and_get_by_name(registry: RunnerRegistry) -> None:
    registry.create(_make_runner(id="rid-002", name="foo"))
    got = registry.get(name="foo")
    assert got.id == "rid-002"


def test_get_not_found(registry: RunnerRegistry) -> None:
    with pytest.raises(RunnerNotFoundError):
        registry.get(id="ghost")
    with pytest.raises(RunnerNotFoundError):
        registry.get(name="ghost")


def test_get_requires_id_or_name(registry: RunnerRegistry) -> None:
    with pytest.raises(ValueError, match="requires id or name"):
        registry.get()


def test_create_duplicate_name_raises(registry: RunnerRegistry) -> None:
    registry.create(_make_runner(id="r1", name="dup"))
    with pytest.raises(DuplicateRunnerNameError):
        registry.create(_make_runner(id="r2", name="dup"))


def test_list_all_and_by_status(registry: RunnerRegistry) -> None:
    registry.create(_make_runner(id="r1", name="a", status="running"))
    registry.create(_make_runner(id="r2", name="b", status="stopped"))
    registry.create(_make_runner(id="r3", name="c", status="running"))
    all_ = registry.list()
    assert len(all_) == 3
    running = registry.list(status="running")
    assert {r.id for r in running} == {"r1", "r3"}


def test_update_partial_fields(registry: RunnerRegistry) -> None:
    registry.create(_make_runner())
    updated = registry.update("rid-001", status="running", pid=12345)
    assert updated.status == "running"
    assert updated.pid == 12345
    # 未指定的字段保留
    assert updated.name == "hello-spot"
    # updated_at 比创建时新
    fetched = registry.get(id="rid-001")
    assert fetched.updated_at > fetched.created_at


def test_update_params_override(registry: RunnerRegistry) -> None:
    registry.create(_make_runner(params_override={"a": 1}))
    updated = registry.update("rid-001", params_override={"b": 2})
    assert updated.params_override == {"b": 2}


def test_update_not_found(registry: RunnerRegistry) -> None:
    with pytest.raises(RunnerNotFoundError):
        registry.update("ghost", status="running")


def test_delete(registry: RunnerRegistry) -> None:
    registry.create(_make_runner())
    registry.delete("rid-001")
    with pytest.raises(RunnerNotFoundError):
        registry.get(id="rid-001")


def test_delete_not_found(registry: RunnerRegistry) -> None:
    with pytest.raises(RunnerNotFoundError):
        registry.delete("ghost")


# ---------- 心跳 ----------


def test_heartbeat_updates(registry: RunnerRegistry) -> None:
    registry.create(_make_runner())
    ts = datetime.now(UTC)
    registry.heartbeat("rid-001", ts)
    got = registry.get(id="rid-001")
    assert got.last_heartbeat == ts


def test_heartbeat_not_found(registry: RunnerRegistry) -> None:
    with pytest.raises(RunnerNotFoundError):
        registry.heartbeat("ghost", datetime.now(UTC))


# ---------- ledger 历史保留 (acceptance) ----------


def test_delete_does_not_cascade_to_ledger(tmp_path: Any) -> None:
    """spec acceptance: registry 表行删除时 ledger 历史保留(不级联)。"""
    from decimal import Decimal

    from copy_trader.core import Fill
    from copy_trader.persistence import TradesRepo

    registry = RunnerRegistry(tmp_path / "registry.db")
    ledger = TradesRepo(tmp_path / "ledger.db", env_tag="dev", machine_id="m1")

    runner = _make_runner(id="rid-x")
    registry.create(runner)

    fill = Fill(
        id="fill-1",
        ts=datetime.now(UTC),
        account="spot",
        symbol="BTCUSDT",
        side="buy",
        qty=Decimal("0.001"),
        price=Decimal("50000"),
        fee=Decimal("0.5"),
        fee_asset="USDT",
        exchange_order_id="ord-1",
        env_tag="dev",
        machine_id="m1",
        schema_version=3,
        runner_id="rid-x",
    )
    ledger.insert(fill)

    # 删 registry 行
    registry.delete("rid-x")

    # ledger 行还在(不级联)
    fills = ledger.fetch("spot", "BTCUSDT")
    assert len(fills) == 1
    assert fills[0].runner_id == "rid-x"

    registry.close()
    ledger.close()


# ---------- ledger schema 迁移 (acceptance) ----------


def test_ledger_v2_to_v3_migration(tmp_path: Any) -> None:
    """spec acceptance: schema 迁移脚本在已有 ledger 上跑过后保留所有历史 fills。"""
    db_path = tmp_path / "ledger.db"

    # 1. 模拟一个 v2 schema 的旧库 (手工建表 + insert 几行)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE trades (
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
            schema_version INTEGER NOT NULL
        )
        """
    )
    conn.execute("PRAGMA user_version = 2")
    # 插 2 行 v2 历史数据
    for i in range(2):
        conn.execute(
            """
            INSERT INTO trades (id, ts, account, symbol, side, qty, price, fee,
                fee_asset, exchange_order_id, env_tag, machine_id, schema_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"old-{i}",
                "2026-01-01T00:00:00+00:00",
                "spot",
                "BTCUSDT",
                "buy",
                "0.001",
                "50000",
                "0.5",
                "USDT",
                f"ord-old-{i}",
                "dev",
                "m1",
                2,
            ),
        )
    conn.commit()
    conn.close()

    # 2. 用新版 TradesRepo 打开 → 触发 v2→v3 迁移
    from copy_trader.persistence import TradesRepo

    repo = TradesRepo(db_path, env_tag="dev", machine_id="m1")
    assert repo.schema_version == 3

    # 3. 历史 fills 保留 + runner_id 默认 'legacy'
    fills = repo.fetch("spot", "BTCUSDT")
    assert len(fills) == 2
    assert all(f.runner_id == "legacy" for f in fills)
    assert all(f.schema_version == 2 for f in fills)

    repo.close()
