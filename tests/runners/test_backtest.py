"""BacktestRunner 测试 (issue #24)。

acceptance:
1. backtest 与 live dry-run 的策略代码完全相同(无任何分支判断) — 通过让两者都
   用同一 HelloStrategy/_OneShotStrategy 实例验证
2. backtest 跑通: 喂历史 K 线 → strategy 产 OrderRequest → PaperExchange 模拟成交
   → fills 写 ledger
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from copy_trader.core import OrderRequest, Position, SymbolInfo
from copy_trader.marketdata import Kline
from copy_trader.persistence import TradesRepo
from copy_trader.runners import BacktestRunner, run_backtest
from copy_trader.strategies import HelloStrategy, StrategyContext


def _make_kline(open_ts: datetime, close: Decimal = Decimal("50000")) -> Kline:
    return Kline(
        open_ts=open_ts,
        open=close,
        high=close + Decimal("100"),
        low=close - Decimal("100"),
        close=close,
        volume=Decimal("1"),
        close_ts=open_ts + timedelta(minutes=1),
    )


class _OneShotStrategy:
    """第一次 step 返回 1 个 OrderRequest, 后续返回 []。"""

    name = "one-shot"

    def __init__(self) -> None:
        self._fired = False

    def step(self, ctx: StrategyContext) -> list[OrderRequest]:
        if self._fired:
            return []
        self._fired = True
        return [
            OrderRequest(
                account=ctx.account,
                symbol=ctx.symbol,
                side="buy",
                type="market",
                qty=Decimal("0.001"),
                price=None,
            )
        ]


@pytest.fixture
def ledger(tmp_path: Any) -> TradesRepo:
    return TradesRepo(
        tmp_path / "ledger.db",
        env_tag="paper",  # backtest 复用 paper env_tag
        machine_id="backtest",
    )


@pytest.fixture
def klines() -> list[Kline]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [_make_kline(base + timedelta(minutes=i)) for i in range(20)]


def test_backtest_hello_zero_orders(ledger: TradesRepo, klines: list[Kline]) -> None:
    """HelloStrategy 永远返回 [], backtest 0 orders / 0 fills / 0 errors。"""
    runner = BacktestRunner(
        account="acc",
        strategy=HelloStrategy(),
        symbol="BTCUSDT",
        klines=klines,
        ledger=ledger,
    )
    result = runner.run()
    assert result.iterations == 20
    assert result.orders_proposed == 0
    assert result.orders_executed == 0
    assert result.fills_written == 0
    assert result.errors == []


def test_backtest_one_shot_writes_one_fill(ledger: TradesRepo, klines: list[Kline]) -> None:
    """_OneShotStrategy 第一轮下单, fill 写 ledger。"""
    runner = BacktestRunner(
        account="acc",
        strategy=_OneShotStrategy(),
        symbol="BTCUSDT",
        klines=klines,
        ledger=ledger,
        runner_id="rid-bt-1",
    )
    result = runner.run()
    assert result.orders_proposed == 1
    assert result.orders_executed == 1
    assert result.fills_written == 1
    fills = ledger.fetch("acc", "BTCUSDT")
    assert len(fills) == 1
    assert fills[0].runner_id == "rid-bt-1"
    assert fills[0].env_tag == "paper"  # PaperExchange 写 'paper'


def test_run_backtest_facade(ledger: TradesRepo, klines: list[Kline]) -> None:
    """run_backtest facade 跟 BacktestRunner 行为一致。"""
    result = run_backtest(
        account="acc",
        strategy=HelloStrategy(),
        symbol="BTCUSDT",
        klines=klines,
        ledger=ledger,
    )
    assert result.iterations == 20
    assert result.errors == []


def test_backtest_uses_same_strategy_as_live(ledger: TradesRepo, klines: list[Kline]) -> None:
    """spec acceptance 第一条: backtest 与 live dry-run 的策略代码完全相同。

    验证: 同一 HelloStrategy 实例既能给 BacktestRunner 用,也能给 LiveRunner 用,
    无任何分支判断 (策略本身不感知 mode)。
    """
    from copy_trader.runners.live import LiveRunner

    # 共享一个策略实例
    strategy = HelloStrategy()

    # backtest 用
    bt_runner = BacktestRunner(
        account="acc",
        strategy=strategy,
        symbol="BTCUSDT",
        klines=klines,
        ledger=ledger,
    )
    bt_result = bt_runner.run()
    assert bt_result.errors == []

    # live dry-run 用同一策略实例 (HelloStrategy 是无状态的, 复用 OK)
    class _FixedMd:
        name = "fixed.spot"

        def fetch_klines(self, symbol: str, interval: str, limit: int = 100) -> list[Kline]:
            return klines[-limit:]

    class _StubExchange:
        name = "stub.spot"

        def get_balance(self, asset: str) -> Decimal:
            return Decimal("0")

        def fetch_position(self, symbol: str) -> Position:
            return Position(
                account="acc",
                symbol=symbol,
                qty=Decimal("0"),
                avg_cost=Decimal("0"),
                realized_pnl=Decimal("0"),
                updated_ts=datetime.now(UTC),
            )

        def place_order(self, req: OrderRequest) -> Any:
            raise NotImplementedError

        def cancel(self, order_id: str) -> None:
            pass

        def fetch_open_orders(self, symbol: str | None = None) -> list:
            return []

        def fetch_fills(self, symbol: str, since: datetime | None = None) -> list:
            return []

        def get_symbol_info(self, symbol: str) -> SymbolInfo:
            return SymbolInfo(
                venue="stub",
                symbol=symbol,
                base_asset="BTC",
                quote_asset="USDT",
                tick_size=Decimal("0.01"),
                step_size=Decimal("0.00001"),
                min_notional=Decimal("10"),
            )

        def round_price(self, symbol: str, price: Decimal) -> Decimal:
            return price

        def round_qty(self, symbol: str, qty: Decimal) -> Decimal:
            return qty

    live_runner = LiveRunner(
        account="acc",
        strategy=strategy,  # 同一实例
        mode="dry-run",
        symbols=["BTCUSDT"],
        ledger=ledger,
        exchange=_StubExchange(),
        marketdata=_FixedMd(),
        max_iterations=3,
        tick_seconds=0,
    )
    live_result = live_runner.run()
    assert live_result.errors == []
