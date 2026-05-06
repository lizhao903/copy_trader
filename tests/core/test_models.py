"""core 值对象测试（issue #9 acceptance）。

覆盖：
1. 不可变性 — `frozen=True` 阻止字段被改写
2. Decimal 字段 — 创建后字段类型仍是 `Decimal`，不会被悄悄转 float
3. `Money` 跨币种算术 — 抛 `CurrencyMismatchError`
4. `PnlBreakdown.total` — 等于 `realized + unrealized`
5. `SymbolInfo` 字段类型
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from copy_trader.core import (
    CurrencyMismatchError,
    Fill,
    Money,
    Order,
    PnlBreakdown,
    Position,
    SymbolInfo,
)

# --- helpers --------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _make_order(**overrides: object) -> Order:
    base: dict[str, object] = dict(
        id="o-1",
        account="acc-A",
        symbol="BTCUSDT",
        side="buy",
        type="limit",
        qty=Decimal("0.1"),
        price=Decimal("50000"),
        status="new",
        ts=_now(),
    )
    base.update(overrides)
    return Order.model_validate(base)


# --- 不可变性 -------------------------------------------------------------


def test_order_is_frozen() -> None:
    order = _make_order()
    with pytest.raises(ValidationError):
        order.qty = Decimal("0.2")  # type: ignore[misc]


def test_fill_is_frozen() -> None:
    fill = Fill(
        id="f-1",
        ts=_now(),
        account="acc-A",
        symbol="BTCUSDT",
        side="buy",
        qty=Decimal("0.1"),
        price=Decimal("50000"),
        fee=Decimal("0.05"),
        fee_asset="USDT",
        exchange_order_id="ex-1",
        env_tag="dev",
        machine_id="m-1",
        schema_version=2,
    )
    with pytest.raises(ValidationError):
        fill.qty = Decimal("0.2")  # type: ignore[misc]


def test_position_is_frozen() -> None:
    pos = Position(
        account="acc-A",
        symbol="BTCUSDT",
        qty=Decimal("0.1"),
        avg_cost=Decimal("50000"),
        realized_pnl=Decimal("0"),
        updated_ts=_now(),
    )
    with pytest.raises(ValidationError):
        pos.qty = Decimal("0.2")  # type: ignore[misc]


def test_money_is_frozen() -> None:
    m = Money(amount=Decimal("100"), currency="USDT")
    with pytest.raises(ValidationError):
        m.amount = Decimal("200")  # type: ignore[misc]


# --- Decimal 字段类型 -----------------------------------------------------


def test_order_qty_remains_decimal() -> None:
    order = _make_order(qty=Decimal("0.1"), price=Decimal("50000"))
    assert isinstance(order.qty, Decimal)
    assert isinstance(order.price, Decimal)
    assert order.qty == Decimal("0.1")
    assert order.price == Decimal("50000")


def test_order_strict_rejects_float() -> None:
    # strict=True 阻止 float → Decimal 隐式转换，避免 IEEE 754 误差污染 ledger
    with pytest.raises(ValidationError):
        _make_order(qty=0.1)  # type: ignore[arg-type]


def test_fill_decimal_fields() -> None:
    fill = Fill(
        id="f-1",
        ts=_now(),
        account="acc-A",
        symbol="BTCUSDT",
        side="sell",
        qty=Decimal("0.5"),
        price=Decimal("60000"),
        fee=Decimal("0.3"),
        fee_asset="USDT",
        exchange_order_id="ex-1",
        env_tag="prod",
        machine_id="m-1",
        schema_version=2,
    )
    for value in (fill.qty, fill.price, fill.fee):
        assert isinstance(value, Decimal)


# --- Order limit/market 校验 ---------------------------------------------


def test_limit_order_requires_price() -> None:
    with pytest.raises(ValidationError):
        _make_order(type="limit", price=None)


def test_market_order_rejects_price() -> None:
    with pytest.raises(ValidationError):
        _make_order(type="market", price=Decimal("50000"))


def test_market_order_accepts_none_price() -> None:
    order = _make_order(type="market", price=None)
    assert order.price is None


# --- Money 算术 -----------------------------------------------------------


def test_money_add_same_currency() -> None:
    a = Money(amount=Decimal("10"), currency="USDT")
    b = Money(amount=Decimal("5.5"), currency="USDT")
    assert (a + b).amount == Decimal("15.5")
    assert (a + b).currency == "USDT"


def test_money_sub_same_currency() -> None:
    a = Money(amount=Decimal("10"), currency="USDT")
    b = Money(amount=Decimal("3"), currency="USDT")
    assert (a - b).amount == Decimal("7")


def test_money_neg() -> None:
    a = Money(amount=Decimal("10"), currency="USDT")
    assert (-a).amount == Decimal("-10")


def test_money_cross_currency_raises() -> None:
    usdt = Money(amount=Decimal("10"), currency="USDT")
    btc = Money(amount=Decimal("0.001"), currency="BTC")
    with pytest.raises(CurrencyMismatchError) as exc:
        _ = usdt + btc
    assert exc.value.left == "USDT"
    assert exc.value.right == "BTC"
    with pytest.raises(CurrencyMismatchError):
        _ = usdt - btc


# --- PnlBreakdown.total --------------------------------------------------


def test_pnl_total_is_sum() -> None:
    p = PnlBreakdown(
        account="acc-A",
        symbol="BTCUSDT",
        realized=Decimal("25"),
        unrealized=Decimal("-5.5"),
    )
    assert p.total == Decimal("19.5")
    assert isinstance(p.total, Decimal)


def test_pnl_total_zero_zero() -> None:
    p = PnlBreakdown(
        account="acc-A",
        symbol="BTCUSDT",
        realized=Decimal("0"),
        unrealized=Decimal("0"),
    )
    assert p.total == Decimal("0")


# --- SymbolInfo 字段类型 -------------------------------------------------


def test_symbol_info_decimal_fields() -> None:
    info = SymbolInfo(
        venue="binance",
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.00001"),
        min_notional=Decimal("10"),
    )
    assert info.venue == "binance"
    assert info.base_asset == "BTC"
    for value in (info.tick_size, info.step_size, info.min_notional):
        assert isinstance(value, Decimal)


def test_symbol_info_is_frozen() -> None:
    info = SymbolInfo(
        venue="binance",
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.00001"),
        min_notional=Decimal("10"),
    )
    with pytest.raises(ValidationError):
        info.tick_size = Decimal("0.001")  # type: ignore[misc]
