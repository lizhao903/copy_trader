"""`PnlBreakdown` 值对象：把 PnL 拆成 realized / unrealized 两段。

`pnl-single-source` spec 要求所有 PnL 计算从 ledger 重建；本对象只是「计算结果
的传输容器」，不持有计算逻辑。`total` 是计算属性（`realized + unrealized`），
保证两侧加和总能还原 spec 中要求的报告口径。
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, computed_field


class PnlBreakdown(BaseModel):
    """不可变 PnL 拆分。"""

    model_config = ConfigDict(frozen=True, strict=True)

    account: str
    symbol: str
    realized: Decimal
    unrealized: Decimal

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> Decimal:
        return self.realized + self.unrealized
