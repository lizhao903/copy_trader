"""`HelloStrategy`：仅用于验证策略管线的占位实装。

按 `package-layout` spec 第 5 段「strategies → core, marketdata」依赖图，
本策略仅 import `Strategy / StrategyContext`（间接依赖 core / marketdata 值对象），
不触达 exchanges / execution。

行为契约：**永远返回空列表**。

选择「永远不下单」的根据：

1. issue #18 acceptance 第 2 条「hello 策略在 dry-run 下不产生真实下单」
   要求最低安全保证；让占位策略在任何上下文下都返回 `[]`，可避免后续
   paper / live 回归过程中误下单。
2. 验证管线只需要「策略可被注册、可被 step、可被 lint / mypy 通过」即可，
   不需要真实订单意图来证明。
3. 满足 spec 中「确定性 / 无副作用」要求：同一份输入序列 → 同一份输出（空），
   隐式即可。
"""

from __future__ import annotations

from copy_trader.core import OrderRequest
from copy_trader.strategies.base import Strategy, StrategyContext


class HelloStrategy:
    """永远返回 `[]` 的最小策略，用于验证 Strategy 管线。

    显式实现 `Strategy` Protocol 的 `name` 与 `step`；不持有任何可变状态、
    不访问网络 / 文件 / 时钟。
    """

    name: str = "hello"

    def __init__(self, name: str = "hello") -> None:
        # 允许 factory 透传自定义 name（如多实例区分），但默认即 "hello"。
        self.name = name

    def step(self, ctx: StrategyContext) -> list[OrderRequest]:
        """无论上下文如何，永远返回空列表（dry-run 安全）。"""
        return []


# 运行期由 `tests/strategies/test_hello.py::test_hello_satisfies_protocol_runtime_checkable`
# 用 `isinstance(hello, Strategy)` 验证 Protocol 兼容性。
_PROTOCOL_CHECK: Strategy = HelloStrategy()
