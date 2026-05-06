"""策略层：消费 marketdata 与 core 信号，产出交易意图（`OrderRequest`）。

子包暴露 `Strategy` Protocol、`StrategyContext` 值对象、`StrategyRegistry`
（仿 `ExchangeRegistry`）以及内置 `HelloStrategy` 占位实装。导入期向模块级
默认注册表登记 ``"hello"``，让 runner / 测试通过 `get_default("hello")`
解析。

依赖边界由 `.importlinter` 第 5 段 `strategies-allows-core-marketdata`
contract 强制：本子包仅可 import `core` 与 `marketdata`。
"""

from copy_trader.strategies.base import Strategy, StrategyContext
from copy_trader.strategies.hello import HelloStrategy
from copy_trader.strategies.registry import (
    DuplicateStrategyError,
    InvalidStrategyNameError,
    StrategyFactory,
    StrategyRegistry,
    UnknownStrategyError,
    get_default,
    list_default,
    register_default,
)

# 导入期把内置 hello 策略登记到默认注册表。`register_default` 重复调用会抛
# `DuplicateStrategyError`，所以 Python 模块缓存确保了此处只执行一次。
register_default("hello", lambda **kw: HelloStrategy(**kw))

__all__ = [
    "DuplicateStrategyError",
    "HelloStrategy",
    "InvalidStrategyNameError",
    "Strategy",
    "StrategyContext",
    "StrategyFactory",
    "StrategyRegistry",
    "UnknownStrategyError",
    "get_default",
    "list_default",
    "register_default",
]
