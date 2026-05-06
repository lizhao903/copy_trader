"""`Position` 值对象：当前持仓视图，由 ledger（fills）重建。

按 `pnl-single-source` spec：position 永远是从 ledger 重建出的视图，cache 文件
（`state/position_*.json`）只是缓存，不是事实来源。本对象只表达「某个时刻的持仓
快照」，不内置 reconcile 逻辑（那是 `pnl` 子包的职责）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class Position(BaseModel):
    """不可变持仓快照。"""

    model_config = ConfigDict(frozen=True, strict=True)

    account: str
    symbol: str
    qty: Decimal
    avg_cost: Decimal
    realized_pnl: Decimal
    updated_ts: datetime
