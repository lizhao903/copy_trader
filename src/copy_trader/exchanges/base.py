"""`Exchange` Protocol：所有交易所交互的唯一入口。

按 `exchange-adapter` spec 第 1 条 Requirement：runner / execution / pnl /
notify 等上层 MUST 仅依赖该 Protocol 与 `ExchangeRegistry`，MUST NOT 依赖具体
实现类。每个 venue 在 `copy_trader.exchanges.<venue>/` 子包内提供具体实现，
通过 `ExchangeRegistry.register(...)` 在导入期完成注册。

Protocol 用 `typing.Protocol` + `runtime_checkable`，可在测试里用
`isinstance(impl, Exchange)` 做结构兼容性断言。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from copy_trader.core import Fill, Order, OrderRequest, Position, SymbolInfo


@runtime_checkable
class Exchange(Protocol):
    """交易所适配器统一接口。

    `name` 属性遵循 `<venue>.<market>` 命名规范（如 `binance.spot`、
    `hyperliquid.perp`），与 `ExchangeRegistry` 的注册键对应。
    """

    name: str

    def get_balance(self, asset: str) -> Decimal:
        """返回账户某资产可用余额。"""
        ...

    def fetch_position(self, symbol: str) -> Position:
        """返回当前持仓快照（可能 `qty=0`）。"""
        ...

    def place_order(self, req: OrderRequest) -> Order:
        """下单；返回带交易所 `id / status / ts` 的 `Order`。"""
        ...

    def cancel(self, order_id: str) -> None:
        """按交易所 order id 撤单。"""
        ...

    def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        """返回未成交订单列表，`symbol=None` 表示账户内全部。"""
        ...

    def fetch_fills(self, symbol: str, since: datetime | None = None) -> list[Fill]:
        """返回成交记录；`since=None` 由具体实现决定默认窗口。"""
        ...

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """返回交易对元数据（tick / step / 最小金额）。"""
        ...

    def round_price(self, symbol: str, price: Decimal) -> Decimal:
        """按 PRICE_FILTER `tick_size` 量化价格。"""
        ...

    def round_qty(self, symbol: str, qty: Decimal) -> Decimal:
        """按 LOT_SIZE `step_size` 量化数量。"""
        ...
