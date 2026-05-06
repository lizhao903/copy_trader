"""交易所适配层：把交易所私有协议翻译成 core 领域模型。

本层只暴露 `Exchange` Protocol + `ExchangeRegistry` + 注册相关异常。
具体 venue 实现（Binance / Hyperliquid / paper 等）放在子包，按
`<venue>.<market>` 名称在导入期注册到 `_default_registry`，
上层只通过 `get_default(name)` 解析。
"""

from copy_trader.exchanges.base import Exchange
from copy_trader.exchanges.registry import (
    DuplicateExchangeError,
    ExchangeFactory,
    ExchangeRegistry,
    InvalidExchangeNameError,
    UnknownExchangeError,
    get_default,
    list_default,
    register_default,
)

__all__ = [
    "DuplicateExchangeError",
    "Exchange",
    "ExchangeFactory",
    "ExchangeRegistry",
    "InvalidExchangeNameError",
    "UnknownExchangeError",
    "get_default",
    "list_default",
    "register_default",
]
