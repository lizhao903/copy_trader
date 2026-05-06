"""ReconcileService 三级 diff 测试（issue #11 acceptance）。

5 个场景对齐 issue 验收清单：

1. cache_drift 自动修正：cache 写入虚假 entry_price，ledger 重建覆盖
2. cache 缺失：cache_dir 没有该 account/symbol 文件，reconcile 写新 cache
3. ledger_exchange_mismatch：ledger qty=1，exchange qty=0.5 → safe_mode=True
4. unknown_position_on_exchange (拒绝)：exchange BTC 余额 0.5，ledger 没 fills，
   acknowledge_unknown=False → fatal=True，错误消息含 --acknowledge-unknown
5. unknown_position_on_exchange (放行)：同 4，但 acknowledge_unknown=True →
   fatal=False，事件标 acknowledged

不打真实 HTTP / exchange API：``FakeExchange`` 实现 ``Exchange`` Protocol 的
最小子集（仅 ``fetch_position`` / ``get_balance`` 需要在 reconcile 中被调用）。
``TradesRepo`` 用 issue #8 真实实现 + ``tmp_path`` SQLite。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from copy_trader.core import Fill, Order, OrderRequest, Position, SymbolInfo
from copy_trader.execution import (
    ReconcileEvent,
    ReconcileReport,
    ReconcileService,
)
from copy_trader.persistence import TradesRepo

# --- fakes ----------------------------------------------------------------


class FakeExchange:
    """实现 ``Exchange`` Protocol 所需方法的内存假实现。

    构造参数：
    - ``positions``: ``{symbol: Position}``，``fetch_position`` 直接查表
    - ``balances``: ``{asset: Decimal}``，``get_balance`` 直接查表
    - ``balance_errors``: ``{asset: Exception}``，命中则 ``get_balance`` 抛错
    """

    name = "fake.spot"

    def __init__(
        self,
        positions: dict[str, Position] | None = None,
        balances: dict[str, Decimal] | None = None,
        balance_errors: dict[str, Exception] | None = None,
    ) -> None:
        self._positions = positions or {}
        self._balances = balances or {}
        self._balance_errors = balance_errors or {}

    def get_balance(self, asset: str) -> Decimal:
        if asset in self._balance_errors:
            raise self._balance_errors[asset]
        return self._balances.get(asset, Decimal("0"))

    def fetch_position(self, symbol: str) -> Position:
        if symbol in self._positions:
            return self._positions[symbol]
        # 默认空仓快照
        return Position(
            account="fake",
            symbol=symbol,
            qty=Decimal("0"),
            avg_cost=Decimal("0"),
            realized_pnl=Decimal("0"),
            updated_ts=datetime(2026, 1, 1, tzinfo=UTC),
        )

    # 下面的方法 reconcile 不调用，但 Protocol 要求存在以满足 isinstance 检查。
    def place_order(self, req: OrderRequest) -> Order:  # pragma: no cover - 占位
        raise NotImplementedError

    def cancel(self, order_id: str) -> None:  # pragma: no cover - 占位
        raise NotImplementedError

    def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:  # pragma: no cover
        return []

    def fetch_fills(
        self, symbol: str, since: datetime | None = None
    ) -> list[Fill]:  # pragma: no cover
        return []

    def get_symbol_info(self, symbol: str) -> SymbolInfo:  # pragma: no cover
        raise NotImplementedError

    def round_price(self, symbol: str, price: Decimal) -> Decimal:  # pragma: no cover
        return price

    def round_qty(self, symbol: str, qty: Decimal) -> Decimal:  # pragma: no cover
        return qty


# --- helpers --------------------------------------------------------------


def _make_fill(
    *,
    id_: str,
    ts: datetime,
    account: str = "acc-A",
    symbol: str = "BTCUSDT",
    side: str = "buy",
    qty: str = "1",
    price: str = "100",
    fee: str = "0",
    fee_asset: str = "USDT",
    exchange_order_id: str = "x-1",
    env_tag: str = "dev",
    machine_id: str = "host-1",
) -> Fill:
    return Fill(
        id=id_,
        ts=ts,
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
        schema_version=2,
    )


@pytest.fixture
def ledger(tmp_path: Path) -> TradesRepo:
    return TradesRepo(
        db_path=tmp_path / "trades.db",
        env_tag="dev",
        machine_id="host-1",
    )


def _events_by_kind(report: ReconcileReport, kind: str) -> list[ReconcileEvent]:
    return [ev for ev in report.events if ev.kind == kind]


# --- scenario 1: cache_drift 自动修正 ------------------------------------


def test_cache_drift_overridden_by_ledger(tmp_path: Path, ledger: TradesRepo) -> None:
    """spec Scenario「缓存被人工编辑后启动」。

    ledger 有一笔买入 1@100；cache 文件里被人工改成 ``avg_cost=99``；
    reconcile 必须用 ledger 重建结果（``avg_cost=100``）覆盖 cache 文件，
    并产出 ``cache_overridden`` 事件。
    """
    cache_dir = tmp_path / "state"
    cache_dir.mkdir()
    logs_dir = tmp_path / "logs"

    # ledger：1 笔 1@100
    ledger.insert(_make_fill(id_="f-1", ts=datetime(2026, 1, 1, 12, 0, tzinfo=UTC)))

    # 人工写一份"被改坏"的 cache：avg_cost=99（真实应为 100）
    bad_payload = {
        "account": "acc-A",
        "symbol": "BTCUSDT",
        "qty": "1",
        "avg_cost": "99",
        "realized_pnl": "0",
        "updated_ts": "2025-12-31T00:00:00+00:00",
    }
    cache_path = cache_dir / "acc-A_BTCUSDT.json"
    cache_path.write_text(json.dumps(bad_payload), encoding="utf-8")

    exchange = FakeExchange(
        positions={
            "BTCUSDT": Position(
                account="acc-A",
                symbol="BTCUSDT",
                qty=Decimal("1"),
                avg_cost=Decimal("100"),
                realized_pnl=Decimal("0"),
                updated_ts=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            )
        },
        balances={"BTC": Decimal("1")},
    )

    service = ReconcileService(
        ledger=ledger, exchange=exchange, cache_dir=cache_dir, logs_dir=logs_dir
    )
    report = service.reconcile("acc-A", ["BTCUSDT"])

    # cache 被覆盖
    overridden = _events_by_kind(report, "cache_overridden")
    assert len(overridden) == 1, (
        f"expected exactly one cache_overridden event, got: {report.events}"
    )
    ev = overridden[0]
    assert ev.level == "warning"
    assert ev.account == "acc-A"
    assert ev.symbol == "BTCUSDT"
    assert "cache_drift" in ev.message

    # cache 文件已被覆盖回 avg_cost=100
    after = json.loads(cache_path.read_text(encoding="utf-8"))
    assert after["qty"] == "1"
    assert after["avg_cost"] == "100"

    # 不进 SAFE 模式、不 fatal
    assert report.safe_mode is False
    assert report.fatal is False

    # 写了日志
    assert report.log_path is not None
    assert report.log_path.exists()


# --- scenario 2: cache 缺失 ----------------------------------------------


def test_cache_missing_creates_new(tmp_path: Path, ledger: TradesRepo) -> None:
    """spec Scenario「缓存文件丢失」。

    cache_dir 下无该 account/symbol 文件 → reconcile 写新 cache（不报错），
    报告含 ``cache_created`` 事件。
    """
    cache_dir = tmp_path / "state"  # 故意不预先 mkdir，验证 service 自建
    logs_dir = tmp_path / "logs"

    ledger.insert(_make_fill(id_="f-1", ts=datetime(2026, 1, 1, 12, 0, tzinfo=UTC)))

    exchange = FakeExchange(
        positions={
            "BTCUSDT": Position(
                account="acc-A",
                symbol="BTCUSDT",
                qty=Decimal("1"),
                avg_cost=Decimal("100"),
                realized_pnl=Decimal("0"),
                updated_ts=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            )
        },
        balances={"BTC": Decimal("1")},
    )

    service = ReconcileService(
        ledger=ledger, exchange=exchange, cache_dir=cache_dir, logs_dir=logs_dir
    )
    report = service.reconcile("acc-A", ["BTCUSDT"])

    created = _events_by_kind(report, "cache_created")
    assert len(created) == 1
    assert created[0].level == "info"

    cache_path = cache_dir / "acc-A_BTCUSDT.json"
    assert cache_path.exists()
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["qty"] == "1"
    assert payload["avg_cost"] == "100"

    assert report.safe_mode is False
    assert report.fatal is False


# --- scenario 3: ledger_exchange_mismatch -------------------------------


def test_ledger_exchange_mismatch_triggers_safe_mode(tmp_path: Path, ledger: TradesRepo) -> None:
    """spec Requirement「Startup reconcile compares ledger, exchange, and cache」。

    ledger qty=1，exchange.fetch_position 返回 qty=0.5 → SAFE 模式开启 +
    warning 事件，但 ``fatal`` 仍为 ``False``（不阻塞启动）。
    """
    cache_dir = tmp_path / "state"
    cache_dir.mkdir()
    logs_dir = tmp_path / "logs"

    # ledger：1 笔 1@100
    ledger.insert(_make_fill(id_="f-1", ts=datetime(2026, 1, 1, 12, 0, tzinfo=UTC)))

    # exchange 只看到 0.5
    exchange = FakeExchange(
        positions={
            "BTCUSDT": Position(
                account="acc-A",
                symbol="BTCUSDT",
                qty=Decimal("0.5"),
                avg_cost=Decimal("100"),
                realized_pnl=Decimal("0"),
                updated_ts=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            )
        },
        # 余额给 1（满足 ledger，避免 unknown_position 路径）
        balances={"BTC": Decimal("1")},
    )

    service = ReconcileService(
        ledger=ledger, exchange=exchange, cache_dir=cache_dir, logs_dir=logs_dir
    )
    report = service.reconcile("acc-A", ["BTCUSDT"])

    mismatches = _events_by_kind(report, "ledger_exchange_mismatch")
    assert len(mismatches) == 1
    ev = mismatches[0]
    assert ev.level == "warning"
    assert "SAFE" in ev.message or "safe" in ev.message.lower()
    details: dict[str, Any] = ev.details
    assert details["ledger_qty"] == Decimal("1")
    assert details["exchange_qty"] == Decimal("0.5")

    assert report.safe_mode is True
    assert report.fatal is False


# --- scenario 4: unknown_position_on_exchange (拒绝) -----------------------


def test_unknown_position_on_exchange_rejects(tmp_path: Path, ledger: TradesRepo) -> None:
    """spec Scenario「交易所有未知仓位拒绝启动」。

    exchange.get_balance("BTC")=0.5，ledger 该账户没 BTC fills，
    ``acknowledge_unknown=False`` → ``fatal=True`` + 错误消息提到 ``--acknowledge-unknown``。
    """
    cache_dir = tmp_path / "state"
    cache_dir.mkdir()
    logs_dir = tmp_path / "logs"

    # ledger：完全没 BTCUSDT fills

    exchange = FakeExchange(
        # fetch_position 返回 qty=0（默认）
        balances={"BTC": Decimal("0.5")},
    )

    service = ReconcileService(
        ledger=ledger, exchange=exchange, cache_dir=cache_dir, logs_dir=logs_dir
    )
    report = service.reconcile("acc-A", ["BTCUSDT"], acknowledge_unknown=False)

    unknown = _events_by_kind(report, "unknown_position_on_exchange")
    assert len(unknown) == 1
    ev = unknown[0]
    assert ev.level == "error"
    assert "BTC" in ev.message
    assert "unknown_position_on_exchange" in ev.message

    assert report.fatal is True
    assert report.fatal_message is not None
    assert "--acknowledge-unknown" in report.fatal_message


# --- scenario 5: unknown_position_on_exchange (放行) -----------------------


def test_unknown_position_on_exchange_acknowledged(tmp_path: Path, ledger: TradesRepo) -> None:
    """同 scenario 4 输入，但 ``acknowledge_unknown=True`` → 放行。

    断言事件被降级为 ``unknown_position_acknowledged`` (info)，
    ``fatal=False`` 不阻塞启动。
    """
    cache_dir = tmp_path / "state"
    cache_dir.mkdir()
    logs_dir = tmp_path / "logs"

    exchange = FakeExchange(balances={"BTC": Decimal("0.5")})

    service = ReconcileService(
        ledger=ledger, exchange=exchange, cache_dir=cache_dir, logs_dir=logs_dir
    )
    report = service.reconcile("acc-A", ["BTCUSDT"], acknowledge_unknown=True)

    ack = _events_by_kind(report, "unknown_position_acknowledged")
    assert len(ack) == 1
    assert ack[0].level == "info"
    assert "acknowledge-unknown" in ack[0].message

    # 不应再有未确认的 error 事件
    assert _events_by_kind(report, "unknown_position_on_exchange") == []

    assert report.fatal is False
    assert report.fatal_message is None
