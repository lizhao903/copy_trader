"""`SymbolInfo` 值对象：交易对元数据（tick / step / 最小 notional）。

不同交易所对同一逻辑 symbol（比如 `BTCUSDT`）的精度限制不一样，下单前需要按
`tick_size` / `step_size` 做四舍五入对齐，按 `min_notional` 做最小金额校验。
本对象就是这份元数据的不可变快照，由 `exchanges` 子包负责拉取与缓存。
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class SymbolInfo(BaseModel):
    """不可变交易对元数据。"""

    model_config = ConfigDict(frozen=True, strict=True)

    venue: str
    symbol: str
    base_asset: str
    quote_asset: str
    tick_size: Decimal
    step_size: Decimal
    min_notional: Decimal
