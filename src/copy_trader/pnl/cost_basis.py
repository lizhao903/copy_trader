"""成本基础（cost basis）计算策略：加权平均 + FIFO。

按 `pnl-single-source` spec 第 5 条 Requirement：

- `WeightedAverageCostBasis` 是默认模式：买入按数量加权刷新平均成本，卖出
  realized = (卖价 - avg_cost) * 数量 - fee
- `FifoCostBasis` 仅供报表场景：维护买入批次的 FIFO 队列，卖出按队首出货，
  realized = Σ(卖价 - 该批次进价) * 该批次量 - fee

两种实现都只接受 `Fill` 序列，不持有任何文件 IO，保证从同一份 ledger 出发
能够稳定可重放出相同结果（spec 黄金例：买 1@100、买 1@110、卖 1@130 →
weighted=25 / fifo=30）。

`CostBasis` 抽象基类约束最小接口：`update(fill)` 返回 realized PnL（buy 返
None，sell 返 Decimal），并暴露当前 `current_qty` / `avg_cost` 视图。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from decimal import Decimal

from copy_trader.core.fill import Fill


class CostBasis(ABC):
    """成本基础策略抽象基类。

    子类必须实现 `update(fill)`，并维护 `current_qty` / `avg_cost` 两个视图属性。
    `update` 对 buy 返回 `None`、对 sell 返回该笔 realized PnL（含 fee 扣减）。
    """

    current_qty: Decimal
    avg_cost: Decimal

    @abstractmethod
    def update(self, fill: Fill) -> Decimal | None:
        """喂入一笔成交，更新内部仓位 / 队列；buy 返回 None，sell 返回 realized PnL。"""


class WeightedAverageCostBasis(CostBasis):
    """加权平均成本基础。

    买入：`new_avg = (old_qty * old_avg + fill_qty * fill_price) / new_qty`
    卖出：`realized = (fill_price - avg_cost) * fill_qty - fee`，并按数量减仓，
    `avg_cost` 在持仓 > 0 时不变；持仓归零后 `avg_cost` 复位为 0。
    """

    def __init__(self) -> None:
        self.current_qty: Decimal = Decimal("0")
        self.avg_cost: Decimal = Decimal("0")

    def update(self, fill: Fill) -> Decimal | None:
        if fill.side == "buy":
            new_qty = self.current_qty + fill.qty
            # 加权平均：忽略买入 fee（fee 不进入 cost basis，下个版本若改口径再调整）
            self.avg_cost = (self.current_qty * self.avg_cost + fill.qty * fill.price) / new_qty
            self.current_qty = new_qty
            return None

        # side == "sell"
        realized = (fill.price - self.avg_cost) * fill.qty - fill.fee
        self.current_qty -= fill.qty
        if self.current_qty == 0:
            self.avg_cost = Decimal("0")
        return realized


@dataclass
class _Lot:
    """FIFO 队列中的一个买入批次（剩余可出货量 + 进价）。"""

    qty: Decimal
    price: Decimal


class FifoCostBasis(CostBasis):
    """FIFO（先进先出）成本基础。

    买入：在队尾追加一个批次。
    卖出：按队首批次出货，realized = Σ(卖价 - 批次进价) * 该批次出货量 - fee（fee
    一次性整体扣在该笔卖出上）。`avg_cost` 视图取**剩余批次**的加权平均，方便
    与加权平均模式同接口。
    """

    def __init__(self) -> None:
        self._lots: deque[_Lot] = deque()
        self.current_qty: Decimal = Decimal("0")
        self.avg_cost: Decimal = Decimal("0")

    def _recompute_avg_cost(self) -> None:
        if self.current_qty == 0:
            self.avg_cost = Decimal("0")
            return
        total = sum((lot.qty * lot.price for lot in self._lots), start=Decimal("0"))
        self.avg_cost = total / self.current_qty

    def update(self, fill: Fill) -> Decimal | None:
        if fill.side == "buy":
            self._lots.append(_Lot(qty=fill.qty, price=fill.price))
            self.current_qty += fill.qty
            self._recompute_avg_cost()
            return None

        # side == "sell"：从队首批次依次出货
        remaining = fill.qty
        gross_realized = Decimal("0")
        while remaining > 0:
            if not self._lots:
                # 卖出量超过持仓：spec 没有覆盖此场景，按数学口径继续（current_qty 转负），
                # 但本项目上层 ReconcileService 应该在更早阶段拦住这种情况。
                # 这里退化为单批次成本 = 0 来保证返回值仍是 Decimal。
                gross_realized += fill.price * remaining
                self.current_qty -= remaining
                remaining = Decimal("0")
                break
            head = self._lots[0]
            take = min(head.qty, remaining)
            gross_realized += (fill.price - head.price) * take
            head.qty -= take
            remaining -= take
            self.current_qty -= take
            if head.qty == 0:
                self._lots.popleft()
        self._recompute_avg_cost()
        return gross_realized - fill.fee
