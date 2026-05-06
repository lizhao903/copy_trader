"""`Strategy` Protocol 与 `StrategyContext` 值对象：策略层统一接口。

按 `package-layout` spec：strategies 子包仅可依赖 `core` 与 `marketdata`，不得
依赖 `exchanges / execution / runners`（由 `.importlinter` 第 5 段
`strategies-allows-core-marketdata` contract 强制）。

`Strategy` 是策略契约的最小集：

- `name` 属性遵循人类可读的 ID（如 ``"hello"``、``"copy_v1"``），用于日志、
  metrics 与 `StrategyRegistry` 的注册键。
- `step(ctx)` 是「单步评估」入口：拿到一份 `StrategyContext` 快照（行情 +
  当前持仓 + 时间），返回零个或多个 `OrderRequest`。**纯函数式**：不允许在
  内部直接下单或访问网络；下单交给 execution 层处理。

`StrategyContext` 是 frozen pydantic 值对象（与 `Order / Position / Kline`
保持一致风格），保证：

- 同一份输入 → 同一份输出（便于回放 / 单测）。
- 跨进程发送时可直接 `model_dump()` JSON 化。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from copy_trader.core import OrderRequest, Position
from copy_trader.marketdata import Kline


class StrategyContext(BaseModel):
    """单步评估输入快照（不可变）。

    - `account`：策略要操作的账户 ID（execution 层据此选 `Exchange`）。
    - `symbol`：本步评估的交易对（与 `klines` / `position.symbol` 同名）。
    - `klines`：最近 N 根 K 线，按 `open_ts` 升序（最旧 → 最新）。
    - `position`：账户在 `symbol` 上的最新持仓快照（`qty=0` 表示无仓位）。
    - `current_price`：最新成交价（一般等于 ``klines[-1].close``，但允许
      调用方传入更新的 ticker 价以提高时效性）。
    - `ts`：本步评估的 UTC 时间戳（用于策略判断 K 线收没收 / 盘口时效）。
    """

    model_config = ConfigDict(frozen=True, strict=True, arbitrary_types_allowed=False)

    account: str
    symbol: str
    klines: list[Kline]
    position: Position
    current_price: Decimal
    ts: datetime


@runtime_checkable
class Strategy(Protocol):
    """策略契约（结构子类型）。

    实现 MUST 保证：

    - `step(ctx)` 是「输入决定输出」的纯函数；同一份 `StrategyContext` 多次调用
      应得到等价的 `OrderRequest` 列表（顺序、内容均一致）。允许内部维护
      可序列化状态（如 EMA 缓存），但不允许调用网络 / 文件 / 时钟。
    - 返回的每个 `OrderRequest.account / .symbol` 与 `ctx.account / ctx.symbol`
      一致；execution 层据此做分发。
    - 抛异常视为策略 bug，由 runner 捕获 + 告警，不会被 execution 当作「无信号」。
    """

    name: str

    def step(self, ctx: StrategyContext) -> list[OrderRequest]:
        """评估一步，返回 0 或多个下单意图。"""
        ...
