"""LiveRunner 主循环测试（issue #19 acceptance）。

覆盖:
1. dry-run 模式 hello 策略（永远返回 []）→ 0 orders / 0 fills / 0 errors
2. dry-run mock binance.spot + 假 marketdata, 跑 max_iterations=3, tick_seconds=0
3. live ↔ paper 切换零业务代码改动（参数化 mode 测试）：
   同 OrderRequest 序列在两个 mode 下都跑通
4. errors 累积测试：marketdata 失败时记到 errors,不崩溃
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from copy_trader.core import Fill, Order, OrderRequest, Position, SymbolInfo
from copy_trader.marketdata import Kline
from copy_trader.persistence import TradesRepo
from copy_trader.runners import LiveRunner, run_live
from copy_trader.runners.live import default_marketdata_factory
from copy_trader.strategies import HelloStrategy, StrategyContext

# ---------- Fakes ----------


def _make_kline(close: Decimal) -> Kline:
    """构造一个最小合法 Kline。"""
    now = datetime.now(UTC)
    return Kline(
        open_ts=now,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1"),
        close_ts=now,
    )


class _FixedMarketdata:
    name = "fake.spot"

    def __init__(self, close: Decimal = Decimal("50000"), fail: bool = False) -> None:
        self._close = close
        self._fail = fail

    def fetch_klines(self, symbol: str, interval: str, limit: int = 100) -> list[Kline]:
        if self._fail:
            raise RuntimeError("simulated marketdata failure")
        return [_make_kline(self._close)]


class _FakeExchange:
    """最小 Exchange stub（mode=live/paper 都用得上）。"""

    name = "fake.spot"

    def __init__(self) -> None:
        self.placed_orders: list[OrderRequest] = []
        self._fills: list[Fill] = []

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

    def place_order(self, req: OrderRequest) -> Order:
        self.placed_orders.append(req)
        return Order(
            id=f"fake-{len(self.placed_orders)}",
            account=req.account,
            symbol=req.symbol,
            side=req.side,
            type=req.type,
            qty=req.qty,
            price=req.price,
            status="filled",
            ts=datetime.now(UTC),
        )

    def cancel(self, order_id: str) -> None:
        pass

    def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        return []

    def fetch_fills(self, symbol: str, since: datetime | None = None) -> list[Fill]:
        return self._fills

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        return SymbolInfo(
            venue="fake.spot",
            symbol=symbol,
            base_asset="BTC",
            quote_asset="USDT",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.00001"),
            min_notional=Decimal("10"),
        )

    def round_price(self, symbol: str, price: Decimal) -> Decimal:
        return price.quantize(Decimal("0.01"))

    def round_qty(self, symbol: str, qty: Decimal) -> Decimal:
        return qty.quantize(Decimal("0.00001"))


class _OneShotStrategy:
    """实装 Strategy Protocol：第一次 step 返回 1 个 OrderRequest，后续返回 []。"""

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


# ---------- Tests ----------


@pytest.fixture
def ledger(tmp_path: Any) -> TradesRepo:
    return TradesRepo(
        tmp_path / "ledger.db",
        env_tag="dev",
        machine_id="test-machine",
    )


def test_dry_run_hello_zero_orders(ledger: TradesRepo) -> None:
    """hello 策略永远返回 []，dry-run 跑 3 轮应当 0 order / 0 fill / 0 error。"""
    runner = LiveRunner(
        account="acc",
        strategy=HelloStrategy(),
        mode="dry-run",
        symbols=["BTCUSDT"],
        ledger=ledger,
        exchange=_FakeExchange(),
        marketdata=_FixedMarketdata(),
        max_iterations=3,
        tick_seconds=0,
    )
    result = runner.run()
    assert result.iterations == 3
    assert result.orders_proposed == 0
    assert result.orders_executed == 0
    assert result.fills_written == 0
    assert result.errors == []


def test_dry_run_one_shot_strategy_no_exchange_call(ledger: TradesRepo) -> None:
    """dry-run 即便策略产 OrderRequest 也不触达 exchange。"""
    fake_exchange = _FakeExchange()
    runner = LiveRunner(
        account="acc",
        strategy=_OneShotStrategy(),
        mode="dry-run",
        symbols=["BTCUSDT"],
        ledger=ledger,
        exchange=fake_exchange,
        marketdata=_FixedMarketdata(),
        max_iterations=2,
        tick_seconds=0,
    )
    result = runner.run()
    assert result.orders_proposed == 1  # 第一轮 fire
    assert result.orders_executed == 0  # dry-run 不下单
    assert fake_exchange.placed_orders == []  # exchange 没被触达


def test_paper_mode_writes_paper_ledger(ledger: TradesRepo) -> None:
    """paper 模式: 用 PaperExchange 包 wraps，env_tag='paper' 写 ledger。"""
    fake_exchange = _FakeExchange()
    runner = LiveRunner(
        account="acc",
        strategy=_OneShotStrategy(),
        mode="paper",
        symbols=["BTCUSDT"],
        ledger=ledger,
        exchange=fake_exchange,
        marketdata=_FixedMarketdata(close=Decimal("50000")),
        max_iterations=1,
        tick_seconds=0,
    )
    result = runner.run()
    assert result.orders_executed == 1
    assert result.fills_written >= 1  # paper 写了 fill
    # paper 不真调 wraps.place_order
    assert fake_exchange.placed_orders == []
    # ledger 里的 fill env_tag='paper'
    fills = ledger.fetch("acc", "BTCUSDT")
    assert len(fills) >= 1
    assert all(f.env_tag == "paper" for f in fills)


@pytest.mark.parametrize("mode", ["live", "paper", "dry-run"])
def test_modes_compile(mode: str, ledger: TradesRepo) -> None:
    """三个 mode 都能构造并跑过最少一轮，验证 'live ↔ paper 切换零业务代码改动'。"""
    runner = LiveRunner(
        account="acc",
        strategy=HelloStrategy(),  # 永远 [] 不下单
        mode=mode,  # type: ignore[arg-type]
        symbols=["BTCUSDT"],
        ledger=ledger,
        exchange=_FakeExchange(),
        marketdata=_FixedMarketdata(),
        max_iterations=1,
        tick_seconds=0,
    )
    result = runner.run()
    assert result.iterations == 1
    assert result.errors == []


def test_marketdata_failure_recorded_not_fatal(ledger: TradesRepo) -> None:
    """marketdata 失败时记 error，不崩溃。"""
    runner = LiveRunner(
        account="acc",
        strategy=HelloStrategy(),
        mode="dry-run",
        symbols=["BTCUSDT"],
        ledger=ledger,
        exchange=_FakeExchange(),
        marketdata=_FixedMarketdata(fail=True),
        max_iterations=2,
        tick_seconds=0,
    )
    result = runner.run()
    assert result.iterations == 2
    assert len(result.errors) == 2  # 每轮一次 fetch_klines failed
    assert all("fetch_klines failed" in e for e in result.errors)


def test_invalid_mode_raises() -> None:
    """构造 LiveRunner 时 mode 校验。"""
    with pytest.raises(ValueError, match="invalid mode"):
        LiveRunner(
            account="acc",
            strategy=HelloStrategy(),
            mode="bogus",  # type: ignore[arg-type]
            symbols=["BTCUSDT"],
            ledger=None,  # type: ignore[arg-type]
            exchange=_FakeExchange(),
            marketdata=_FixedMarketdata(),
        )


def test_default_marketdata_factory_known_venues() -> None:
    md_binance = default_marketdata_factory("binance.spot")
    assert md_binance.name == "binance.spot"
    md_hyper = default_marketdata_factory("hyperliquid.spot")
    assert md_hyper.name == "hyperliquid.spot"


def test_default_marketdata_factory_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown marketdata venue"):
        default_marketdata_factory("nonexistent.spot")


def test_run_live_unknown_account(ledger: TradesRepo) -> None:
    """run_live 不认识的 account 抛 KeyError。"""
    from types import SimpleNamespace

    fake_settings = SimpleNamespace(accounts={})  # 空 accounts
    with pytest.raises(KeyError, match="不在配置里"):
        run_live(
            account="nonexistent",
            strategy_name="hello",
            mode="dry-run",
            settings=fake_settings,  # type: ignore[arg-type]
            ledger=ledger,
            exchange=_FakeExchange(),
            marketdata=_FixedMarketdata(),
            max_iterations=1,
            tick_seconds=0,
        )
