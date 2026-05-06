"""`ExchangeRegistry` 行为契约测试。

覆盖 spec 第 4 条 Requirement 的关键场景：
- 注册 → 解析 → 工厂被调用
- 未知 name 抛 `UnknownExchangeError` 并暴露已注册列表
- 名字不符 `<venue>.<market>` 抛 `InvalidExchangeNameError`
- 重复注册抛 `DuplicateExchangeError`（安全选择，避免静默覆盖）
- 最小 `_DummyExchange` 验证 Protocol 结构兼容（`runtime_checkable`）
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from copy_trader.core import Fill, Order, OrderRequest, Position, SymbolInfo
from copy_trader.exchanges import (
    DuplicateExchangeError,
    Exchange,
    ExchangeRegistry,
    InvalidExchangeNameError,
    UnknownExchangeError,
)


class _DummyExchange:
    """仅供测试：实现 `Exchange` Protocol 的全部方法（return stub 值）。"""

    def __init__(self, name: str = "dummy.spot") -> None:
        self.name = name

    def get_balance(self, asset: str) -> Decimal:
        return Decimal("0")

    def fetch_position(self, symbol: str) -> Position:
        return Position(
            account="acct",
            symbol=symbol,
            qty=Decimal("0"),
            avg_cost=Decimal("0"),
            realized_pnl=Decimal("0"),
            updated_ts=datetime(2025, 1, 1, tzinfo=UTC),
        )

    def place_order(self, req: OrderRequest) -> Order:
        return Order(
            id="oid-1",
            account=req.account,
            symbol=req.symbol,
            side=req.side,
            type=req.type,
            qty=req.qty,
            price=req.price,
            status="new",
            ts=datetime(2025, 1, 1, tzinfo=UTC),
        )

    def cancel(self, order_id: str) -> None:
        return None

    def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        return []

    def fetch_fills(self, symbol: str, since: datetime | None = None) -> list[Fill]:
        return []

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        return SymbolInfo(
            venue=self.name,
            symbol=symbol,
            base_asset="BTC",
            quote_asset="USDT",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.0001"),
            min_notional=Decimal("10"),
        )

    def round_price(self, symbol: str, price: Decimal) -> Decimal:
        return price

    def round_qty(self, symbol: str, qty: Decimal) -> Decimal:
        return qty


def _factory(name: str = "binance.spot") -> _DummyExchange:
    return _DummyExchange(name=name)


def test_register_and_get_returns_factory_result() -> None:
    reg = ExchangeRegistry()
    reg.register("binance.spot", lambda: _factory("binance.spot"))

    impl = reg.get("binance.spot")

    assert impl.name == "binance.spot"
    # 同名两次解析得到独立实例（工厂每次重建）
    impl2 = reg.get("binance.spot")
    assert impl is not impl2


def test_get_unknown_name_raises_with_registered_list() -> None:
    reg = ExchangeRegistry()
    reg.register("binance.spot", lambda: _factory("binance.spot"))
    reg.register("hyperliquid.spot", lambda: _factory("hyperliquid.spot"))

    with pytest.raises(UnknownExchangeError) as exc_info:
        reg.get("bnance.spot")

    err = exc_info.value
    assert err.name == "bnance.spot"
    # 暴露已注册名便于启动期定位拼写错误
    assert "binance.spot" in err.registered
    assert "hyperliquid.spot" in err.registered


@pytest.mark.parametrize("bad_name", ["binance", "", ".spot", "binance.", "..", "a..b"])
def test_register_invalid_name_raises(bad_name: str) -> None:
    reg = ExchangeRegistry()

    with pytest.raises(InvalidExchangeNameError):
        reg.register(bad_name, lambda: _factory(bad_name or "x.y"))


def test_duplicate_registration_raises() -> None:
    reg = ExchangeRegistry()
    reg.register("binance.spot", lambda: _factory("binance.spot"))

    with pytest.raises(DuplicateExchangeError):
        reg.register("binance.spot", lambda: _factory("binance.spot"))


def test_list_returns_sorted_registered_names() -> None:
    reg = ExchangeRegistry()
    reg.register("hyperliquid.spot", lambda: _factory("hyperliquid.spot"))
    reg.register("binance.spot", lambda: _factory("binance.spot"))

    assert reg.list() == ["binance.spot", "hyperliquid.spot"]


def test_paper_mirror_multi_segment_name_is_valid() -> None:
    # spec 例子 `paper.binance.spot` —— 多段也合法，只要至少一个 `.` 且无空段
    reg = ExchangeRegistry()
    reg.register("paper.binance.spot", lambda: _factory("paper.binance.spot"))

    impl = reg.get("paper.binance.spot")

    assert impl.name == "paper.binance.spot"


def test_dummy_exchange_satisfies_protocol_runtime_checkable() -> None:
    impl = _DummyExchange()

    assert isinstance(impl, Exchange)


def test_clear_helps_isolated_tests() -> None:
    reg = ExchangeRegistry()
    reg.register("binance.spot", lambda: _factory("binance.spot"))
    assert reg.list() == ["binance.spot"]

    reg.clear()

    assert reg.list() == []
