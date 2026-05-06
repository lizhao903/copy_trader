"""`PaperExchange`：参数化包裹任意 venue 的纸面成交模拟器。

按 `exchange-adapter` spec 第 2 条 Requirement 实装：

- 实现完整 `Exchange` Protocol，但**不真打** wraps 交易所的 `place_order` /
  `cancel`（避免误把 paper 流量打到 live）。
- 用注入的 marketdata（`KlineSource` 结构兼容对象）给出 market 单成交价。
- 按可配置 `slippage_bps`（万分之）+ `fee_bps` 模拟成交。
- 写入与 live 同结构的 `Fill`（仅 `env_tag='paper'` 区分），保留全部 ledger 列。
- `wraps.get_symbol_info / round_price / round_qty` 透传（这些不下单，
  允许调用 venue 元数据）。

设计取舍：

- **不直接 import `copy_trader.marketdata.*`**：`exchanges` 子包受
  `.importlinter` `exchanges-only-core` contract 约束，禁止依赖
  `copy_trader.marketdata`。这里在模块内本地定义最小 `_KlineLike` /
  `_KlineSourceLike` Protocol（与 `marketdata.base.KlineSource` 结构兼容），
  靠 duck typing 接收任何实现 `fetch_klines(symbol, interval, limit) -> list`
  的对象。具体 `BinanceMarketdata` 实例由上层 runner（cli / runners 子包）
  注入；本子包不感知具体实现类。
- **limit 单简化**：暂时按 limit 价立即成交。真实「挂单到价」模拟（限价单
  在未来 K 线区间内由价格穿越触发成交）留待 m3+。本简化在 paper-vs-live
  契约测试里仍能保证「同 OrderRequest 序列产生预期一致 fills」。
- **schema_version / machine_id**：`Fill` 必填 ledger 列。`exchanges` 子包
  不允许 import `copy_trader.config.runtime` 取 `RUNTIME_LOCK_SCHEMA_VERSION`
  与本机 machine_id（同样的 import-linter 边界）。这里允许调用方在构造
  `PaperExchange` 时显式注入；不传则用 `_DEFAULT_*` 占位常量（适合单元
  测试），由 runner 子包负责把 RuntimeContext 的真实值传进来。
- **不自动注册到 `ExchangeRegistry`**：paper 是参数化包装，需要 wraps +
  marketdata 两份依赖才能构造，不存在零参 factory；由 runner / 测试显式
  构造 `PaperExchange(wraps, marketdata, ...)` 后再 `register_default(name,
  lambda: instance)` 入册。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final, Protocol, runtime_checkable

from copy_trader.core import Fill, Order, OrderRequest, Position, SymbolInfo
from copy_trader.exchanges.base import Exchange

# spec 第 2 条 Requirement：paper 写入与 live 同结构的 ledger，仅 `env_tag` 区分。
PAPER_ENV_TAG: Final[str] = "paper"

# `Fill` 必填字段的占位默认。生产代码（runner 子包）应注入 `RuntimeContext.machine_id`
# 与 `RUNTIME_LOCK_SCHEMA_VERSION`；占位仅用于单测便利。
_DEFAULT_MACHINE_ID: Final[str] = "paper-default"
_DEFAULT_SCHEMA_VERSION: Final[int] = 1

# 滑点 / 费率均以万分之（basis points）表示。`Decimal` 化避免 float 误差。
_BPS_DENOMINATOR: Final[Decimal] = Decimal("10000")


@runtime_checkable
class _KlineLike(Protocol):
    """marketdata K 线的最小结构（仅取 `close` 用于成交价）。

    与 `copy_trader.marketdata.base.Kline` 的 `close: Decimal` 字段兼容。
    本地定义 Protocol 以保持 `exchanges` 不 import `marketdata`。
    """

    close: Decimal


@runtime_checkable
class _KlineSourceLike(Protocol):
    """marketdata 源的最小结构（仅 `fetch_klines`）。

    与 `copy_trader.marketdata.base.KlineSource` 的 `fetch_klines(symbol,
    interval, limit) -> list[Kline]` 兼容。
    """

    name: str

    def fetch_klines(
        self, symbol: str, interval: str, limit: int = 100
    ) -> list[_KlineLike]:  # pragma: no cover - structural
        ...


class PaperExchange:
    """纸面成交交易所（包裹任意 live `Exchange`）。

    Args:
        wraps: 被包裹的真实 `Exchange` 实现。**不会**调用其 `place_order` /
            `cancel`；仅透传 `get_symbol_info / round_price / round_qty`。
        marketdata: 行情源（duck-type 兼容 `KlineSource`），给出 market 单
            成交参考价（用最近一根 K 线的 close）。
        slippage_bps: 滑点（万分之）。买单 fill_price = close * (1 + slippage)，
            卖单 = close * (1 - slippage)。默认 0。
        fee_bps: 手续费率（万分之）。fee = fill_price * qty * fee_bps / 10000。
            默认 10（0.1%）。
        kline_interval: 取 marketdata 报价的 K 线粒度。默认 `"1m"`。
        machine_id / schema_version: ledger 行的 `Fill.machine_id` / `schema_version`
            字段。生产代码应注入 RuntimeContext 的对应值；不传用本地占位常量。

    Note:
        `name` 自动派生为 `f"paper.{wraps.name}"`（如 wraps=`binance.spot` →
        `paper.binance.spot`），与 `ExchangeRegistry` 的命名规范对齐。
    """

    def __init__(
        self,
        wraps: Exchange,
        marketdata: _KlineSourceLike,
        slippage_bps: int = 0,
        fee_bps: int = 10,
        *,
        kline_interval: str = "1m",
        machine_id: str = _DEFAULT_MACHINE_ID,
        schema_version: int = _DEFAULT_SCHEMA_VERSION,
    ) -> None:
        if slippage_bps < 0:
            raise ValueError(f"slippage_bps must be >= 0, got {slippage_bps!r}")
        if fee_bps < 0:
            raise ValueError(f"fee_bps must be >= 0, got {fee_bps!r}")

        self._wraps = wraps
        self._marketdata = marketdata
        self._slippage_bps = Decimal(slippage_bps)
        self._fee_bps = Decimal(fee_bps)
        self._kline_interval = kline_interval
        self._machine_id = machine_id
        self._schema_version = schema_version

        self.name: str = f"paper.{wraps.name}"

        # paper ledger（in-memory）。`fetch_fills` 直接从这里读。
        self._fills: list[Fill] = []
        # 已 placed 订单（status='filled' 立即成交）。`fetch_open_orders` 总返回空。
        self._orders: list[Order] = []

    # ---- Protocol: 账户查询（透传 wraps 是不安全的——可能触发签名 HTTP；这里给
    # ---- 简单 stub 值。runner 实际跑 paper 时会在策略循环里读 fills 推算虚拟
    # ---- 余额/持仓，这是 issue #19 的工作）。

    def get_balance(self, asset: str) -> Decimal:
        """paper 模式下返回 0；上层应从 fills 重建虚拟余额。"""
        return Decimal("0")

    def fetch_position(self, symbol: str) -> Position:
        """paper 模式下返回零仓位；上层应从 fills 重建虚拟持仓。"""
        return Position(
            account="paper",
            symbol=symbol,
            qty=Decimal("0"),
            avg_cost=Decimal("0"),
            realized_pnl=Decimal("0"),
            updated_ts=datetime.now(UTC),
        )

    # ---- Protocol: 下单 / 撤单（核心成交模拟）

    def place_order(self, req: OrderRequest) -> Order:
        """模拟下单：market / limit 都立即成交，写一条 `Fill` 到 paper ledger。

        - market 单：fill_price = 最近一根 K 线 close × (1 ± slippage_bps/10000)
        - limit 单：fill_price = `req.price`（简化；真挂单到价模拟留 m3+）
        - fee：fill_price × qty × fee_bps / 10000，扣自 `Fill.fee`
        - `Fill.env_tag = 'paper'`，与 live 同结构

        Raises:
            ValueError: marketdata 没返回任何 K 线 / market 单时无可用报价
        """
        now = datetime.now(UTC)
        order_id = f"paper-{uuid.uuid4()}"

        if req.type == "market":
            fill_price = self._market_fill_price(req.symbol, req.side)
        else:
            assert req.price is not None  # OrderRequest validator 已保证
            fill_price = req.price

        fee = self._compute_fee(fill_price, req.qty)

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
            id=f"fill-{uuid.uuid4()}",
            ts=now,
            account=req.account,
            symbol=req.symbol,
            side=req.side,
            qty=req.qty,
            price=fill_price,
            fee=fee,
            fee_asset=self._fee_asset_for(req.symbol),
            exchange_order_id=order_id,
            env_tag=PAPER_ENV_TAG,
            machine_id=self._machine_id,
            schema_version=self._schema_version,
        )

        self._orders.append(order)
        self._fills.append(fill)
        return order

    def cancel(self, order_id: str) -> None:
        """paper 模式下所有订单立即成交，无可撤订单 → no-op。

        **不**调用 `wraps.cancel`（避免误打 live API）。
        """
        return None

    # ---- Protocol: 查询

    def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        """paper 模式下所有订单都已 filled，永远返回空列表。"""
        return []

    def fetch_fills(self, symbol: str, since: datetime | None = None) -> list[Fill]:
        """返回 paper ledger 中匹配 `symbol`（与可选 `since`）的全部 Fill。"""
        result = [f for f in self._fills if f.symbol == symbol]
        if since is not None:
            result = [f for f in result if f.ts >= since]
        return result

    # ---- Protocol: venue 元数据（透传 wraps；这些不下单，允许调用）

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """透传 `wraps.get_symbol_info`。"""
        return self._wraps.get_symbol_info(symbol)

    def round_price(self, symbol: str, price: Decimal) -> Decimal:
        """透传 `wraps.round_price`。"""
        return self._wraps.round_price(symbol, price)

    def round_qty(self, symbol: str, qty: Decimal) -> Decimal:
        """透传 `wraps.round_qty`。"""
        return self._wraps.round_qty(symbol, qty)

    # ---- 内部工具

    def _market_fill_price(self, symbol: str, side: str) -> Decimal:
        """market 单成交价：最近 K 线 close × (1 ± slippage_bps/10000)。"""
        klines = self._marketdata.fetch_klines(symbol, self._kline_interval, 1)
        if not klines:
            raise ValueError(
                f"paper market order needs marketdata, but fetch_klines("
                f"{symbol!r}, {self._kline_interval!r}, 1) returned empty"
            )
        close = klines[-1].close
        slip = self._slippage_bps / _BPS_DENOMINATOR
        if side == "buy":
            return close * (Decimal("1") + slip)
        return close * (Decimal("1") - slip)

    def _compute_fee(self, fill_price: Decimal, qty: Decimal) -> Decimal:
        """fee = fill_price * qty * fee_bps / 10000。"""
        return fill_price * qty * self._fee_bps / _BPS_DENOMINATOR

    def _fee_asset_for(self, symbol: str) -> str:
        """优先用 wraps 的 SymbolInfo.quote_asset 作为 fee_asset；失败 fallback。

        多数现货交易所手续费默认从 quote 资产扣（USDT 计价对扣 USDT），与
        实际 live 行为一致。如果 wraps 拉 SymbolInfo 失败（mock 或网络错），
        退回固定 `"USDT"` 占位，不让 paper 因元数据问题崩溃。
        """
        try:
            info = self._wraps.get_symbol_info(symbol)
        except Exception:  # noqa: BLE001 - paper 不应因元数据失败崩溃
            return "USDT"
        return info.quote_asset


__all__ = [
    "PAPER_ENV_TAG",
    "PaperExchange",
]
