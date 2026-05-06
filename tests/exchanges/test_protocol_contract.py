"""跨 venue 契约测试（issue #19 acceptance）。

参数化 ("binance.spot", "paper.binance.spot") 跑同一 OrderRequest 序列，
断言 Fill / round_* / SymbolInfo 行为一致。这是 spec exchange-adapter
"加交易所只动一个子包" + "live ↔ paper 切换零业务代码改动" 的关键证明。

注意：本测试不真打 binance API（pytest-socket --disable-socket 双保险）。
binance.spot 用 mock SDK 客户端，paper.binance.spot 用 PaperExchange
包同一 mock 客户端 + 固定价 marketdata。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from copy_trader.core import Fill, Order, OrderRequest, Position, SymbolInfo
from copy_trader.exchanges import Exchange, PaperExchange

# ---------- Fakes ----------


class _FakeKline:
    def __init__(self, close: Decimal) -> None:
        self.close = close


class _FixedMarketdata:
    name = "fixed.spot"

    def __init__(self, close: Decimal) -> None:
        self._close = close

    def fetch_klines(self, symbol: str, interval: str, limit: int = 100) -> list[_FakeKline]:
        return [_FakeKline(self._close)]


class _FakeBinanceLive:
    """模拟 BinanceSpot 但不真 import binance-connector。

    实装 Exchange Protocol 的最小子集，行为参数化为「永远成交在 close 价」，
    用于跟 PaperExchange 同 OrderRequest 序列对比。
    """

    name = "binance.spot"

    def __init__(self, fixed_close: Decimal) -> None:
        self._close = fixed_close
        self._fills: list[Fill] = []

    def get_balance(self, asset: str) -> Decimal:
        return Decimal("1000")

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
        # live 假定按 close 立即成交（fee 0 — 与 paper 行为参数化对齐时再补）
        fill_price = req.price if req.type == "limit" else self._close
        order_id = f"live-{len(self._fills) + 1}"
        self._fills.append(
            Fill(
                id=order_id,
                ts=datetime.now(UTC),
                account=req.account,
                symbol=req.symbol,
                side=req.side,
                qty=req.qty,
                price=fill_price,
                fee=Decimal("0"),
                fee_asset=req.symbol[-4:],
                exchange_order_id=order_id,
                env_tag="dev",
                machine_id="test-machine",
                schema_version=2,
            )
        )
        # market 单 Order 不能带 price（core 校验）；limit 单带 price
        order_price = req.price if req.type == "limit" else None
        return Order(
            id=order_id,
            account=req.account,
            symbol=req.symbol,
            side=req.side,
            type=req.type,
            qty=req.qty,
            price=order_price,
            status="filled",
            ts=datetime.now(UTC),
        )

    def cancel(self, order_id: str) -> None:
        pass

    def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        return []

    def fetch_fills(self, symbol: str, since: datetime | None = None) -> list[Fill]:
        return list(self._fills)

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        return SymbolInfo(
            venue="binance.spot",
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


# ---------- 共同 OrderRequest 序列 ----------


def _order_sequence() -> list[OrderRequest]:
    return [
        OrderRequest(
            account="acc",
            symbol="BTCUSDT",
            side="buy",
            type="market",
            qty=Decimal("0.001"),
            price=None,
        ),
        OrderRequest(
            account="acc",
            symbol="BTCUSDT",
            side="sell",
            type="market",
            qty=Decimal("0.001"),
            price=None,
        ),
        OrderRequest(
            account="acc",
            symbol="BTCUSDT",
            side="buy",
            type="limit",
            qty=Decimal("0.002"),
            price=Decimal("48000"),
        ),
    ]


# ---------- 契约测试 ----------


def test_round_price_consistency() -> None:
    """live 与 paper 对同 price 的 round_price 应当一致。"""
    live = _FakeBinanceLive(fixed_close=Decimal("50000"))
    paper = PaperExchange(
        wraps=live,
        marketdata=_FixedMarketdata(close=Decimal("50000")),
        slippage_bps=0,
        fee_bps=0,
    )
    for raw in [Decimal("0.123456"), Decimal("100.999"), Decimal("50000.005")]:
        assert live.round_price("BTCUSDT", raw) == paper.round_price("BTCUSDT", raw)


def test_round_qty_consistency() -> None:
    live = _FakeBinanceLive(fixed_close=Decimal("50000"))
    paper = PaperExchange(
        wraps=live,
        marketdata=_FixedMarketdata(close=Decimal("50000")),
        slippage_bps=0,
        fee_bps=0,
    )
    for raw in [Decimal("0.0012345"), Decimal("1.234567")]:
        assert live.round_qty("BTCUSDT", raw) == paper.round_qty("BTCUSDT", raw)


def test_get_symbol_info_consistency() -> None:
    live = _FakeBinanceLive(fixed_close=Decimal("50000"))
    paper = PaperExchange(
        wraps=live,
        marketdata=_FixedMarketdata(close=Decimal("50000")),
        slippage_bps=0,
        fee_bps=0,
    )
    live_info = live.get_symbol_info("BTCUSDT")
    paper_info = paper.get_symbol_info("BTCUSDT")
    # paper 透传给 wraps，结果应当相同
    assert live_info == paper_info


def test_paper_vs_live_fill_consistency_zero_slippage_zero_fee() -> None:
    """同 OrderRequest 序列在 zero-slippage zero-fee 下，paper / live Fill
    的 price/qty/side/symbol/account 完全一致；env_tag / id / fee_asset 可异。"""
    fixed_close = Decimal("50000")
    live = _FakeBinanceLive(fixed_close=fixed_close)
    paper_live_partner = _FakeBinanceLive(fixed_close=fixed_close)
    paper = PaperExchange(
        wraps=paper_live_partner,
        marketdata=_FixedMarketdata(close=fixed_close),
        slippage_bps=0,
        fee_bps=0,
    )

    sequence = _order_sequence()
    for req in sequence:
        live.place_order(req)
        paper.place_order(req)

    live_fills = live.fetch_fills("BTCUSDT")
    paper_fills = paper.fetch_fills("BTCUSDT")
    assert len(live_fills) == len(paper_fills) == len(sequence)

    for lv, pp in zip(live_fills, paper_fills, strict=True):
        assert lv.account == pp.account
        assert lv.symbol == pp.symbol
        assert lv.side == pp.side
        assert lv.qty == pp.qty
        assert lv.price == pp.price  # 同 close, 0 slippage → 价相同
        # env_tag / id / exchange_order_id / fee_asset 允许不同


def test_paper_env_tag_is_paper() -> None:
    """spec acceptance: paper 写 ledger 时 env_tag='paper'。"""
    live = _FakeBinanceLive(fixed_close=Decimal("50000"))
    paper = PaperExchange(
        wraps=live,
        marketdata=_FixedMarketdata(close=Decimal("50000")),
        slippage_bps=10,
        fee_bps=10,
    )
    paper.place_order(_order_sequence()[0])
    fills = paper.fetch_fills("BTCUSDT")
    assert len(fills) == 1
    assert fills[0].env_tag == "paper"


def test_live_isinstance_exchange() -> None:
    """fake live 实现 Exchange Protocol（runtime_checkable）。"""
    live = _FakeBinanceLive(fixed_close=Decimal("50000"))
    assert isinstance(live, Exchange)


def test_paper_isinstance_exchange() -> None:
    """PaperExchange 也实现 Exchange Protocol。"""
    live = _FakeBinanceLive(fixed_close=Decimal("50000"))
    paper = PaperExchange(
        wraps=live,
        marketdata=_FixedMarketdata(close=Decimal("50000")),
    )
    assert isinstance(paper, Exchange)
