"""PnL 引擎：单一事实来源（single source of truth）的盈亏计算与持仓估值。

公共 API：

- `PnlEngine`：从 `Iterable[Fill]` 重建持仓 / realized / unrealized PnL
- `CostBasis` / `WeightedAverageCostBasis` / `FifoCostBasis`：成本基础策略
- `PnlMode`：`Literal["weighted", "fifo"]`
"""

from copy_trader.pnl.cost_basis import (
    CostBasis,
    FifoCostBasis,
    WeightedAverageCostBasis,
)
from copy_trader.pnl.engine import PnlEngine, PnlMode

__all__ = [
    "CostBasis",
    "FifoCostBasis",
    "PnlEngine",
    "PnlMode",
    "WeightedAverageCostBasis",
]
