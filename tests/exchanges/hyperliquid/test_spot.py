"""HyperliquidSpot 测试 (issue #20)。

不真打 api.hyperliquid.xyz; SDK info / exchange 客户端通过构造参数注入 mock。
私钥用 placeholder (0x111... 64 hex), 不进 git 真私钥。
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from copy_trader.core import OrderRequest
from copy_trader.exchanges import Exchange
from copy_trader.exchanges.hyperliquid import HyperliquidSpot, make_hyperliquid_spot_factory

# 测试用 placeholder 私钥 (不是真私钥)
_TEST_PK = "0x" + "1" * 64


def test_init_validates_private_key_format() -> None:
    with pytest.raises(ValueError, match="0x-prefixed"):
        HyperliquidSpot("invalid")
    with pytest.raises(ValueError, match="0x-prefixed"):
        HyperliquidSpot("0xshort")


def test_init_with_valid_pk() -> None:
    venue = HyperliquidSpot(_TEST_PK)
    assert venue.name == "hyperliquid.spot"


def test_get_balance_mocked() -> None:
    info_mock = MagicMock()
    info_mock.spot_user_state.return_value = {
        "balances": [
            {"coin": "BTC", "total": "1.5"},
            {"coin": "USDT", "total": "1000"},
        ]
    }
    venue = HyperliquidSpot(_TEST_PK, sdk_info=info_mock)
    assert venue.get_balance("BTC") == Decimal("1.5")
    assert venue.get_balance("ETH") == Decimal("0")


def test_fetch_position_returns_zero_when_no_balance() -> None:
    info_mock = MagicMock()
    info_mock.spot_user_state.return_value = {"balances": []}
    venue = HyperliquidSpot(_TEST_PK, sdk_info=info_mock)
    pos = venue.fetch_position("BTCUSDT")
    assert pos.qty == Decimal("0")


def test_place_order_offline_records_fill() -> None:
    """无 sdk_exchange 时 (测试模式) 仅记录 Order 不真调 SDK。"""
    venue = HyperliquidSpot(_TEST_PK)
    req = OrderRequest(
        account="acc",
        symbol="BTC",
        side="buy",
        type="market",
        qty=Decimal("0.001"),
        price=None,
    )
    order = venue.place_order(req)
    assert order.symbol == "BTC"
    assert order.qty == Decimal("0.001")
    assert order.status == "filled"


def test_place_order_with_sdk_mock() -> None:
    exchange_mock = MagicMock()
    exchange_mock.order.return_value = {
        "response": {"data": {"statuses": [{"resting": {"oid": 12345}}]}}
    }
    venue = HyperliquidSpot(_TEST_PK, sdk_exchange=exchange_mock)
    req = OrderRequest(
        account="acc",
        symbol="BTC",
        side="buy",
        type="limit",
        qty=Decimal("0.001"),
        price=Decimal("50000"),
    )
    order = venue.place_order(req)
    assert order.id == "12345"
    assert order.status == "new"
    exchange_mock.order.assert_called_once()


def test_cancel_requires_dotted_id() -> None:
    exchange_mock = MagicMock()
    venue = HyperliquidSpot(_TEST_PK, sdk_exchange=exchange_mock)
    with pytest.raises(ValueError, match="<symbol>:<oid>"):
        venue.cancel("plain-id")
    venue.cancel("BTC:12345")
    exchange_mock.cancel.assert_called_with("BTC", 12345)


def test_round_price_floor() -> None:
    venue = HyperliquidSpot(_TEST_PK)
    # 默认 tick_size 0.0001
    assert venue.round_price("BTCUSDT", Decimal("50000.12345")) == Decimal("50000.1234")
    assert venue.round_price("BTCUSDT", Decimal("50000.99999")) == Decimal("50000.9999")


def test_round_qty_floor() -> None:
    venue = HyperliquidSpot(_TEST_PK)
    # 默认 step_size 0.00001
    assert venue.round_qty("BTCUSDT", Decimal("0.123456")) == Decimal("0.12345")


def test_get_symbol_info_caches() -> None:
    venue = HyperliquidSpot(_TEST_PK)
    info1 = venue.get_symbol_info("BTCUSDT")
    info2 = venue.get_symbol_info("BTCUSDT")
    assert info1 is info2  # 缓存命中


def test_isinstance_exchange_protocol() -> None:
    venue = HyperliquidSpot(_TEST_PK)
    assert isinstance(venue, Exchange)


def test_factory_requires_envvar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HYPERLIQUID_PRIVATE_KEY", raising=False)
    factory = make_hyperliquid_spot_factory()
    with pytest.raises(RuntimeError, match="private_key not provided"):
        factory()


def test_factory_uses_envvar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYPERLIQUID_PRIVATE_KEY", _TEST_PK)
    factory = make_hyperliquid_spot_factory()
    venue = factory()
    assert isinstance(venue, HyperliquidSpot)


def test_factory_kwarg_overrides_envvar() -> None:
    factory = make_hyperliquid_spot_factory()
    venue = factory(private_key=_TEST_PK)
    assert isinstance(venue, HyperliquidSpot)
