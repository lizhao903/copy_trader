"""`PaperExchange` 行为契约测试（issue #17 acceptance）。

覆盖 spec `exchange-adapter` 第 2 条 Requirement 的所有验收点：

1. market 单买入 → fill_price = close × (1 + slippage_bps/10000)，fee 正确
2. market 单卖出 → fill_price = close × (1 - slippage_bps/10000)
3. limit 单 → 简化为按 limit_price 立即成交
4. fetch_fills 返回 paper ledger 行（env_tag='paper'）
5. get_symbol_info / round_price / round_qty 透传给 wraps
6. **paper / live 一致性**：同一 OrderRequest 序列在 fixed-price marketdata
   下，paper 与 fake-live 产生的 Fill 字段除 `env_tag` / `exchange_order_id`
   外完全一致 —— spec 第 2 条 Requirement 关键 acceptance。

所有 wraps / marketdata 都是本地 stub class，**不真打** API（满足 driver
"测试不调用真实 HTTP" 硬约束）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from copy_trader.core import Fill, Order, OrderRequest, Position, SymbolInfo
from copy_trader.exchanges import PAPER_ENV_TAG, Exchange, PaperExchange


class _FakeKline:
    """marketdata.base.Kline 的最小 stub（仅 `close` 字段）。"""

    def __init__(self, close: Decimal) -> None:
        self.close = close


class _FixedPriceMarketdata:
    """固定价 marketdata：每次 `fetch_klines` 都返回同一个 close。"""

    name: str = "fixed.spot"

    def __init__(self, close: Decimal) -> None:
        self._close = close
        # 让测试可以断言 fetch_klines 被以正确参数调用过
        self.calls: list[tuple[str, str, int]] = []

    def fetch_klines(self, symbol: str, interval: str, limit: int = 100) -> list[_FakeKline]:
        self.calls.append((symbol, interval, limit))
        return [_FakeKline(self._close)]


class _FakeWrapsExchange:
    """假 live `Exchange` 实现（结构兼容 Protocol）。

    - `get_symbol_info / round_price / round_qty` 由 paper 透传调用，记录调用次数
    - `place_order / cancel` paper **不应**调用；这里把它们做成抛错的 sentinel，
      只要 paper 误调就直接测试失败
    """

    name: str = "binance.spot"

    def __init__(self) -> None:
        self.symbol_info_calls: list[str] = []
        self.round_price_calls: list[tuple[str, Decimal]] = []
        self.round_qty_calls: list[tuple[str, Decimal]] = []
        self.place_order_calls = 0
        self.cancel_calls = 0

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
        # paper 绝对不应 call wraps.place_order；命中即测试失败。
        self.place_order_calls += 1
        raise AssertionError("paper exchange must not call wraps.place_order")

    def cancel(self, order_id: str) -> None:
        # paper 也不应 call wraps.cancel。
        self.cancel_calls += 1
        raise AssertionError("paper exchange must not call wraps.cancel")

    def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        return []

    def fetch_fills(self, symbol: str, since: datetime | None = None) -> list[Fill]:
        return []

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        self.symbol_info_calls.append(symbol)
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
        self.round_price_calls.append((symbol, price))
        # 简单 stub：保留两位小数
        return price.quantize(Decimal("0.01"))

    def round_qty(self, symbol: str, qty: Decimal) -> Decimal:
        self.round_qty_calls.append((symbol, qty))
        return qty.quantize(Decimal("0.0001"))


class _FakeLiveExchange(_FakeWrapsExchange):
    """假 live 交易所：与 paper 同样接收 OrderRequest 序列，按 fixed-price
    marketdata 立即成交，写到内部 ledger。

    用途：和 PaperExchange 喂同一序列，断言 Fill 字段一致（除 env_tag /
    exchange_order_id / Fill.id 这些显然不可能等的字段）。
    """

    name: str = "binance.spot"
    _ENV_TAG = "live"

    def __init__(
        self,
        marketdata: _FixedPriceMarketdata,
        slippage_bps: int = 0,
        fee_bps: int = 10,
    ) -> None:
        super().__init__()
        self._marketdata = marketdata
        self._slippage_bps = Decimal(slippage_bps)
        self._fee_bps = Decimal(fee_bps)
        self._fills: list[Fill] = []
        self._counter = 0

    def place_order(self, req: OrderRequest) -> Order:
        self._counter += 1
        now = datetime(2025, 1, 1, tzinfo=UTC)
        order_id = f"live-{self._counter}"

        if req.type == "market":
            close = self._marketdata.fetch_klines(req.symbol, "1m", 1)[-1].close
            slip = self._slippage_bps / Decimal("10000")
            fill_price = close * (Decimal("1") + slip if req.side == "buy" else Decimal("1") - slip)
        else:
            assert req.price is not None
            fill_price = req.price

        fee = fill_price * req.qty * self._fee_bps / Decimal("10000")

        order = Order(
            id=order_id,
            account=req.account,
            symbol=req.symbol,
            side=req.side,
            type=req.type,
            qty=req.qty,
            price=req.price,
            status="filled",
            ts=now,
        )
        fill = Fill(
            id=f"livefill-{self._counter}",
            ts=now,
            account=req.account,
            symbol=req.symbol,
            side=req.side,
            qty=req.qty,
            price=fill_price,
            fee=fee,
            fee_asset="USDT",
            exchange_order_id=order_id,
            env_tag=self._ENV_TAG,
            machine_id="live-machine",
            schema_version=1,
        )
        self._fills.append(fill)
        return order

    def fetch_fills(self, symbol: str, since: datetime | None = None) -> list[Fill]:
        return [f for f in self._fills if f.symbol == symbol]


# ---- helpers -----------------------------------------------------------


def _build_paper(
    *,
    close: Decimal = Decimal("50000"),
    slippage_bps: int = 0,
    fee_bps: int = 10,
) -> tuple[PaperExchange, _FakeWrapsExchange, _FixedPriceMarketdata]:
    wraps = _FakeWrapsExchange()
    md = _FixedPriceMarketdata(close=close)
    paper = PaperExchange(
        wraps=wraps,
        marketdata=md,
        slippage_bps=slippage_bps,
        fee_bps=fee_bps,
    )
    return paper, wraps, md


# ---- tests -------------------------------------------------------------


def test_name_is_derived_from_wraps() -> None:
    paper, _wraps, _md = _build_paper()

    assert paper.name == "paper.binance.spot"


def test_market_buy_applies_positive_slippage_and_fee() -> None:
    """场景 1：market 买入。close=50000, slippage=10bps, fee=10bps。

    fill_price = 50000 * 1.001 = 50050
    fee = 50050 * 0.1 * 0.001 = 5.005
    """
    paper, _wraps, md = _build_paper(close=Decimal("50000"), slippage_bps=10, fee_bps=10)

    order = paper.place_order(
        OrderRequest(
            account="acct",
            symbol="BTCUSDT",
            side="buy",
            type="market",
            qty=Decimal("0.1"),
        )
    )

    assert order.status == "filled"
    fills = paper.fetch_fills("BTCUSDT")
    assert len(fills) == 1
    f = fills[0]
    assert f.price == Decimal("50050.000")
    assert f.fee == Decimal("5.005000")
    assert f.fee_asset == "USDT"
    assert f.env_tag == PAPER_ENV_TAG
    assert f.qty == Decimal("0.1")
    assert f.side == "buy"
    # marketdata 应被以正确参数调用
    assert md.calls == [("BTCUSDT", "1m", 1)]


def test_market_sell_applies_negative_slippage() -> None:
    """场景 2：market 卖出。close=50000, slippage=10bps → 49950。"""
    paper, _wraps, _md = _build_paper(close=Decimal("50000"), slippage_bps=10, fee_bps=10)

    paper.place_order(
        OrderRequest(
            account="acct",
            symbol="BTCUSDT",
            side="sell",
            type="market",
            qty=Decimal("0.1"),
        )
    )

    fills = paper.fetch_fills("BTCUSDT")
    assert fills[0].price == Decimal("49950.000")
    assert fills[0].side == "sell"


def test_limit_order_fills_at_limit_price() -> None:
    """场景 3：limit 单。limit_price=49000 < market 50050（买）→ 立即按 49000 成交。"""
    paper, _wraps, _md = _build_paper(close=Decimal("50050"), slippage_bps=0, fee_bps=10)

    paper.place_order(
        OrderRequest(
            account="acct",
            symbol="BTCUSDT",
            side="buy",
            type="limit",
            qty=Decimal("0.2"),
            price=Decimal("49000"),
        )
    )

    fills = paper.fetch_fills("BTCUSDT")
    assert fills[0].price == Decimal("49000")
    # fee = 49000 * 0.2 * 0.001 = 9.8
    assert fills[0].fee == Decimal("9.8000")


def test_fetch_fills_returns_all_paper_ledger_rows_with_env_tag() -> None:
    """场景 4：跑两次 place_order，fetch_fills 返回两条 Fill，env_tag 都是 'paper'。"""
    paper, _wraps, _md = _build_paper(close=Decimal("50000"))

    paper.place_order(
        OrderRequest(account="a", symbol="BTCUSDT", side="buy", type="market", qty=Decimal("0.1"))
    )
    paper.place_order(
        OrderRequest(account="a", symbol="BTCUSDT", side="sell", type="market", qty=Decimal("0.05"))
    )

    fills = paper.fetch_fills("BTCUSDT")
    assert len(fills) == 2
    assert all(f.env_tag == PAPER_ENV_TAG for f in fills)
    assert all(f.machine_id and f.schema_version >= 1 for f in fills)
    # 两个 Fill id 应不重复
    assert fills[0].id != fills[1].id


def test_fetch_fills_filters_by_symbol_and_since() -> None:
    paper, _wraps, _md = _build_paper(close=Decimal("50000"))

    paper.place_order(
        OrderRequest(account="a", symbol="BTCUSDT", side="buy", type="market", qty=Decimal("0.1"))
    )
    paper.place_order(
        OrderRequest(account="a", symbol="ETHUSDT", side="buy", type="market", qty=Decimal("1"))
    )

    btc_fills = paper.fetch_fills("BTCUSDT")
    eth_fills = paper.fetch_fills("ETHUSDT")
    assert len(btc_fills) == 1
    assert len(eth_fills) == 1

    # since 过滤：远未来 → 空
    future = datetime(2999, 1, 1, tzinfo=UTC)
    assert paper.fetch_fills("BTCUSDT", since=future) == []


def test_paper_does_not_call_wraps_place_order_or_cancel() -> None:
    paper, wraps, _md = _build_paper()

    paper.place_order(
        OrderRequest(account="a", symbol="BTCUSDT", side="buy", type="market", qty=Decimal("0.1"))
    )
    paper.cancel("paper-doesnotmatter")

    # 关键护栏：paper 不应触达 wraps 的下单 / 撤单
    assert wraps.place_order_calls == 0
    assert wraps.cancel_calls == 0


def test_get_symbol_info_round_price_round_qty_passthrough_to_wraps() -> None:
    """场景 5：get_symbol_info / round_price / round_qty 透传给 wraps。"""
    paper, wraps, _md = _build_paper()

    info = paper.get_symbol_info("BTCUSDT")
    rounded_p = paper.round_price("BTCUSDT", Decimal("123.456789"))
    rounded_q = paper.round_qty("BTCUSDT", Decimal("0.123456789"))

    # 透传：wraps 收到了对应调用
    # `_fee_asset_for` 内部也会 call get_symbol_info，所以 symbol_info_calls
    # 至少包含一次显式调用（这里只断言 "BTCUSDT" 出现过）
    assert "BTCUSDT" in wraps.symbol_info_calls
    assert wraps.round_price_calls == [("BTCUSDT", Decimal("123.456789"))]
    assert wraps.round_qty_calls == [("BTCUSDT", Decimal("0.123456789"))]
    # paper 透传 wraps 的返回值（不在 paper 层重新 quantize）
    assert info.venue == "binance.spot"
    assert info.tick_size == Decimal("0.01")
    assert rounded_p == Decimal("123.46")
    assert rounded_q == Decimal("0.1235")


def test_open_orders_always_empty_for_paper() -> None:
    paper, _wraps, _md = _build_paper()

    paper.place_order(
        OrderRequest(account="a", symbol="BTCUSDT", side="buy", type="market", qty=Decimal("0.1"))
    )

    # paper 模式下所有下单立即 filled，open orders 永远空
    assert paper.fetch_open_orders() == []
    assert paper.fetch_open_orders("BTCUSDT") == []


def test_paper_implements_exchange_protocol() -> None:
    paper, _wraps, _md = _build_paper()

    assert isinstance(paper, Exchange)


def test_paper_and_live_produce_consistent_fills_under_fixed_price() -> None:
    """场景 6：spec 关键 acceptance — paper / live 同 OrderRequest 序列 → 一致 Fill。

    在 fixed-price marketdata 与同 slippage / fee 配置下，paper 与 fake-live
    对**同一序列**的 OrderRequest 应产出：
    - 相同 fill `price`（market 单滑点公式一致；limit 单按 limit price）
    - 相同 fill `fee`
    - 相同 `qty / side / symbol / account`
    - 仅 `env_tag` / `machine_id` / `id` / `exchange_order_id` / `ts` 不同
      （后者是显然差异，paper / live ledger 字段全部齐备）
    """
    md = _FixedPriceMarketdata(close=Decimal("50000"))
    wraps = _FakeWrapsExchange()
    paper = PaperExchange(wraps=wraps, marketdata=md, slippage_bps=10, fee_bps=10)
    live = _FakeLiveExchange(marketdata=md, slippage_bps=10, fee_bps=10)

    requests = [
        OrderRequest(
            account="acct", symbol="BTCUSDT", side="buy", type="market", qty=Decimal("0.1")
        ),
        OrderRequest(
            account="acct", symbol="BTCUSDT", side="sell", type="market", qty=Decimal("0.05")
        ),
        OrderRequest(
            account="acct",
            symbol="BTCUSDT",
            side="buy",
            type="limit",
            qty=Decimal("0.2"),
            price=Decimal("49000"),
        ),
    ]

    for req in requests:
        paper.place_order(req)
        live.place_order(req)

    paper_fills = paper.fetch_fills("BTCUSDT")
    live_fills = live.fetch_fills("BTCUSDT")
    assert len(paper_fills) == 3
    assert len(live_fills) == 3

    for p, lf in zip(paper_fills, live_fills, strict=True):
        # 业务字段一致
        assert p.price == lf.price, f"price mismatch: paper={p.price} vs live={lf.price}"
        assert p.fee == lf.fee, f"fee mismatch: paper={p.fee} vs live={lf.fee}"
        assert p.qty == lf.qty
        assert p.side == lf.side
        assert p.symbol == lf.symbol
        assert p.account == lf.account
        # ledger 字段：env_tag 是唯一允许 / 必须不同的标记
        assert p.env_tag == PAPER_ENV_TAG
        assert lf.env_tag == "live"
        # 完整 Fill 结构齐备（spec: paper / live 同结构）
        assert p.fee_asset and lf.fee_asset
        assert p.exchange_order_id and lf.exchange_order_id
        assert p.machine_id and lf.machine_id
        assert p.schema_version >= 1 and lf.schema_version >= 1


def test_market_order_without_klines_raises() -> None:
    """marketdata 返回空 → market 单无可用报价 → 显式抛错（不静默吞）。"""

    class _EmptyMarketdata:
        name: str = "empty.spot"

        def fetch_klines(self, symbol: str, interval: str, limit: int = 100) -> list[_FakeKline]:
            return []

    paper = PaperExchange(wraps=_FakeWrapsExchange(), marketdata=_EmptyMarketdata())

    with pytest.raises(ValueError, match="needs marketdata"):
        paper.place_order(
            OrderRequest(
                account="a", symbol="BTCUSDT", side="buy", type="market", qty=Decimal("0.1")
            )
        )


@pytest.mark.parametrize("bad_slippage", [-1, -100])
def test_negative_slippage_rejected(bad_slippage: int) -> None:
    with pytest.raises(ValueError, match="slippage_bps"):
        PaperExchange(
            wraps=_FakeWrapsExchange(),
            marketdata=_FixedPriceMarketdata(close=Decimal("100")),
            slippage_bps=bad_slippage,
        )


@pytest.mark.parametrize("bad_fee", [-1, -10])
def test_negative_fee_rejected(bad_fee: int) -> None:
    with pytest.raises(ValueError, match="fee_bps"):
        PaperExchange(
            wraps=_FakeWrapsExchange(),
            marketdata=_FixedPriceMarketdata(close=Decimal("100")),
            fee_bps=bad_fee,
        )


def test_machine_id_and_schema_version_injection() -> None:
    """允许 runner 注入 RuntimeContext 的 machine_id / schema_version。"""
    paper = PaperExchange(
        wraps=_FakeWrapsExchange(),
        marketdata=_FixedPriceMarketdata(close=Decimal("100")),
        machine_id="custom-machine-uuid",
        schema_version=2,
    )

    paper.place_order(
        OrderRequest(account="a", symbol="BTCUSDT", side="buy", type="market", qty=Decimal("0.1"))
    )

    fill = paper.fetch_fills("BTCUSDT")[0]
    assert fill.machine_id == "custom-machine-uuid"
    assert fill.schema_version == 2
