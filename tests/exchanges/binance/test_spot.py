"""`BinanceSpot` 适配器行为测试。

覆盖 issue #15 的 6 个验收场景：

1. `get_balance` mock `/api/v3/account` 返回 BTC=1.0
2. `fetch_position` 用 base_asset 的 free+locked 合成 qty
3. `place_order` market 单走 `new_order` POST
4. `round_price / round_qty` 走 cached exchangeInfo，向下舍入到 tick/step
5. 限频：连续调用超阈值时 `time.sleep` 被介入
6. registry 集成：`get_default("binance.spot")` 工厂可解析

所有测试都用 `unittest.mock` 直接替换 `Spot` 客户端实例（更精准也更轻量），
不真打 `api.binance.com`。`pytest-socket` 全局禁网，多一道安全网。
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from copy_trader.core import OrderRequest
from copy_trader.exchanges.binance.spot import BinanceSpot, _RateLimiter

# ---- exchangeInfo / round_* ------------------------------------------------


def _make_exchange_info(symbol: str, tick: str, step: str) -> dict:
    return {
        "symbols": [
            {
                "symbol": symbol,
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": tick},
                    {"filterType": "LOT_SIZE", "stepSize": step},
                    {"filterType": "NOTIONAL", "minNotional": "10"},
                ],
            }
        ]
    }


def _make_adapter(client: MagicMock | None = None) -> BinanceSpot:
    return BinanceSpot(api_key="k", api_secret="s", testnet=False, client=client or MagicMock())


def test_round_price_floors_to_tick_size() -> None:
    client = MagicMock()
    client.exchange_info.return_value = _make_exchange_info("BTCUSDT", "0.01", "0.00001")
    a = _make_adapter(client)

    assert a.round_price("BTCUSDT", Decimal("0.123456")) == Decimal("0.12")
    assert a.round_price("BTCUSDT", Decimal("100.999")) == Decimal("100.99")
    assert a.round_price("BTCUSDT", Decimal("0")) == Decimal("0")


def test_round_qty_floors_to_step_size() -> None:
    client = MagicMock()
    client.exchange_info.return_value = _make_exchange_info("BTCUSDT", "0.01", "0.00001")
    a = _make_adapter(client)

    assert a.round_qty("BTCUSDT", Decimal("1.234567")) == Decimal("1.23456")
    # 已经在步长上则不变
    assert a.round_qty("BTCUSDT", Decimal("1.23456")) == Decimal("1.23456")


def test_exchange_info_cached_after_first_call() -> None:
    client = MagicMock()
    client.exchange_info.return_value = _make_exchange_info("BTCUSDT", "0.01", "0.00001")
    a = _make_adapter(client)

    a.round_price("BTCUSDT", Decimal("100"))
    a.round_qty("BTCUSDT", Decimal("1"))
    a.get_symbol_info("BTCUSDT")

    # 三次调用只应触发一次 exchangeInfo
    assert client.exchange_info.call_count == 1


# ---- get_balance / fetch_position -----------------------------------------


def test_get_balance_returns_free_amount() -> None:
    client = MagicMock()
    client.account.return_value = {
        "balances": [
            {"asset": "BTC", "free": "1.0", "locked": "0.0"},
            {"asset": "USDT", "free": "5000", "locked": "0"},
        ],
        "updateTime": 1700000000000,
    }
    a = _make_adapter(client)

    assert a.get_balance("BTC") == Decimal("1.0")
    assert a.get_balance("USDT") == Decimal("5000")
    assert a.get_balance("ETH") == Decimal("0")


def test_fetch_position_uses_base_asset_balance() -> None:
    client = MagicMock()
    client.exchange_info.return_value = _make_exchange_info("BTCUSDT", "0.01", "0.00001")
    client.account.return_value = {
        "balances": [
            {"asset": "BTC", "free": "1.0", "locked": "0.5"},
            {"asset": "USDT", "free": "5000", "locked": "0"},
        ],
        "updateTime": 1700000000000,
    }
    a = _make_adapter(client)

    pos = a.fetch_position("BTCUSDT")
    assert pos.symbol == "BTCUSDT"
    assert pos.qty == Decimal("1.5")
    assert pos.avg_cost == Decimal("0")
    assert pos.account == "binance.spot"


# ---- place_order -----------------------------------------------------------


def test_place_order_market_returns_order_with_id() -> None:
    client = MagicMock()
    client.new_order.return_value = {
        "orderId": 1234567,
        "status": "FILLED",
        "transactTime": 1700000000000,
        "fills": [
            {"price": "50000", "qty": "0.001", "commission": "0.05", "commissionAsset": "USDT"}
        ],
    }
    a = _make_adapter(client)

    req = OrderRequest(
        account="acc1", symbol="BTCUSDT", side="buy", type="market", qty=Decimal("0.001")
    )
    order = a.place_order(req)

    assert order.id == "1234567"
    assert order.symbol == "BTCUSDT"
    assert order.side == "buy"
    assert order.type == "market"
    assert order.qty == Decimal("0.001")
    assert order.price is None
    assert order.status == "filled"

    # 验证传给 SDK 的关键字
    kwargs = client.new_order.call_args.kwargs
    assert kwargs["symbol"] == "BTCUSDT"
    assert kwargs["side"] == "BUY"
    assert kwargs["type"] == "MARKET"
    assert kwargs["quantity"] == "0.001"


def test_place_order_limit_includes_price_and_tif() -> None:
    client = MagicMock()
    client.new_order.return_value = {
        "orderId": 999,
        "status": "NEW",
        "transactTime": 1700000000000,
    }
    a = _make_adapter(client)

    req = OrderRequest(
        account="acc1",
        symbol="BTCUSDT",
        side="sell",
        type="limit",
        qty=Decimal("0.01"),
        price=Decimal("50000"),
    )
    order = a.place_order(req)

    assert order.type == "limit"
    assert order.price == Decimal("50000")
    assert order.status == "new"

    kwargs = client.new_order.call_args.kwargs
    assert kwargs["timeInForce"] == "GTC"
    assert kwargs["price"] == "50000"


# ---- 限频 ------------------------------------------------------------------


def test_rate_limiter_sleeps_when_window_full() -> None:
    """连续 >max 次 acquire 触发主动 sleep。"""
    sleeps: list[float] = []
    fake_now = [1000.0]

    def fake_monotonic() -> float:
        return fake_now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        # 模拟真实 sleep：把时间往前推
        fake_now[0] += seconds

    limiter = _RateLimiter(max_per_window=5, window_sec=60.0)

    with patch("copy_trader.exchanges.binance.spot.time.monotonic", fake_monotonic):
        with patch("copy_trader.exchanges.binance.spot.time.sleep", fake_sleep):
            # 5 次直接通过
            for _ in range(5):
                limiter.acquire()
            # 第 6 次应触发 sleep
            limiter.acquire()

    assert len(sleeps) >= 1
    # sleep 应该接近窗口 60s（因为窗口刚被填满，最早请求要等 60s 滑出）
    # 实装在 60s 上加一个 1ms epsilon 以避免边界死循环，所以允许略大
    assert sleeps[0] > 0
    assert sleeps[0] <= 60.1


def test_adapter_uses_rate_limiter_before_sdk_call() -> None:
    """连续 100 次 get_balance 触发限频 sleep（默认 800/min 不会触发，
    但用小阈值实例化即可触发；这里走 _RateLimiter 路径直接通过 mock 验证）。"""
    client = MagicMock()
    client.account.return_value = {"balances": [{"asset": "BTC", "free": "1", "locked": "0"}]}
    a = BinanceSpot(
        api_key="k",
        api_secret="s",
        testnet=False,
        client=client,
        rate_limit_per_min=10,  # 小阈值方便触发
    )

    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        # 推进 monotonic 让循环能跳出
        nonlocal_time[0] += seconds

    nonlocal_time = [10000.0]

    def fake_monotonic() -> float:
        # 真实流逝一点时间避免 sleep=0
        nonlocal_time[0] += 0.001
        return nonlocal_time[0]

    with patch("copy_trader.exchanges.binance.spot.time.monotonic", fake_monotonic):
        with patch("copy_trader.exchanges.binance.spot.time.sleep", fake_sleep):
            for _ in range(15):
                a.get_balance("BTC")

    # 超阈值时主动 sleep（不抛错）
    assert len(sleeps) >= 1
    # 每次 sleep 都是正数
    assert all(s > 0 for s in sleeps)


# ---- registry 集成 ---------------------------------------------------------


def test_registry_resolves_binance_spot_after_subpackage_import() -> None:
    # 触发子包导入期 register_default
    from copy_trader.exchanges import binance as _binance_pkg  # noqa: F401
    from copy_trader.exchanges.registry import get_default, list_default

    assert "binance.spot" in list_default()

    # 默认工厂返回 BinanceSpot 实例
    instance = get_default("binance.spot")
    assert isinstance(instance, BinanceSpot)
    assert instance.name == "binance.spot"


def test_make_binance_spot_factory_injects_credentials() -> None:
    from copy_trader.exchanges.binance import make_binance_spot_factory

    factory = make_binance_spot_factory(api_key="K", api_secret="S", testnet=True)
    instance = factory()
    assert isinstance(instance, BinanceSpot)
    assert instance.name == "binance.spot"
