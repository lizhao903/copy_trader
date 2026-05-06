"""`Fill` 值对象：成交记录，对齐 issue #8 ledger 列定义。

字段集严格对齐 `pnl-single-source` spec 第一条 Requirement 列出的 ledger 列：
`id, ts, account, symbol, side, qty, price, fee, fee_asset, exchange_order_id,
env_tag, machine_id, schema_version`。这是 ledger 表的单一事实来源，所有持仓 /
PnL 计算都从 `Fill` 序列重建，不可变性由 `frozen=True` 强制。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

FillSide = Literal["buy", "sell"]


class Fill(BaseModel):
    """不可变成交记录（ledger 行）。"""

    model_config = ConfigDict(frozen=True, strict=True)

    id: str
    ts: datetime
    account: str
    symbol: str
    side: FillSide
    qty: Decimal
    price: Decimal
    fee: Decimal
    fee_asset: str
    exchange_order_id: str
    env_tag: str
    machine_id: str
    schema_version: int
    # issue #25 加: runner_id 把 fill 归属于具体 RunnerInstance.
    # 默认 "legacy" 兼容 schema v2 旧行 (TradesRepo 迁移时给 NULL→"legacy")。
    runner_id: str = "legacy"
