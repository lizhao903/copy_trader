"""`PnlEngine`：从 fills 序列重建持仓 / realized / unrealized PnL。

按 `pnl-single-source` spec：

- 所有 PnL 来源**必须**是 `Fill` 序列；`PnlEngine` 不读 `position_*.json`、不做
  任何文件 IO（spec 红线 — 见 Requirement 1 Scenario "计算未实现 PnL 走 ledger"）
- 默认走加权平均成本（`mode="weighted"`），FIFO 仅供报表场景（`mode="fifo"`），
  两种模式从同一份 ledger 出发必须可稳定重放
- 输入抽象成 `Iterable[Fill]`：本项目 issue #8 的 `TradesRepo` 与 issue #19 的
  reconcile 都将通过返回 `Iterable[Fill]` 接进来；本模块不耦合具体仓储实现

`mode` 字段使用 `Literal["weighted", "fifo"]`，避免调用方传入字符串拼写错误，
也省去额外工厂依赖注入的样板。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from copy_trader.core.fill import Fill
from copy_trader.core.position import Position
from copy_trader.pnl.cost_basis import (
    CostBasis,
    FifoCostBasis,
    WeightedAverageCostBasis,
)

PnlMode = Literal["weighted", "fifo"]


def _make_cost_basis(mode: PnlMode) -> CostBasis:
    if mode == "weighted":
        return WeightedAverageCostBasis()
    return FifoCostBasis()


class PnlEngine:
    """从 fills 序列重建持仓与 PnL 的纯计算引擎。

    构造时立即消费 `fills` 序列以建立内部状态；`fills` 必须按时间升序，调用方
    （`TradesRepo` / reconcile 服务）负责排序。本类不持有任何 IO 资源、不读
    position cache 文件，因此可在测试中直接喂入 `list[Fill]`。
    """

    def __init__(
        self,
        account: str,
        symbol: str,
        fills: Iterable[Fill],
        mode: PnlMode = "weighted",
    ) -> None:
        self.account = account
        self.symbol = symbol
        self.mode: PnlMode = mode
        self._cost_basis: CostBasis = _make_cost_basis(mode)
        self._realized_total: Decimal = Decimal("0")
        self._last_ts: datetime | None = None
        for fill in fills:
            self._consume(fill)

    def _consume(self, fill: Fill) -> None:
        if fill.account != self.account:
            raise ValueError(f"fill.account={fill.account!r} != engine.account={self.account!r}")
        if fill.symbol != self.symbol:
            raise ValueError(f"fill.symbol={fill.symbol!r} != engine.symbol={self.symbol!r}")
        realized = self._cost_basis.update(fill)
        if realized is not None:
            self._realized_total += realized
        self._last_ts = fill.ts

    def position(self) -> Position:
        """当前持仓快照（qty + avg_cost + 累计 realized）。"""
        return Position(
            account=self.account,
            symbol=self.symbol,
            qty=self._cost_basis.current_qty,
            avg_cost=self._cost_basis.avg_cost,
            realized_pnl=self._realized_total,
            updated_ts=self._last_ts or datetime.now(UTC),
        )

    def realized(self) -> Decimal:
        """累计 realized PnL（已扣 fee）。"""
        return self._realized_total

    def unrealized(self, current_price: Decimal) -> Decimal:
        """`(current_price - avg_cost) * current_qty`，持仓为 0 时返回 0。"""
        qty = self._cost_basis.current_qty
        if qty == 0:
            return Decimal("0")
        return (current_price - self._cost_basis.avg_cost) * qty
