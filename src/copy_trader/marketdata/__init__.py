"""行情接入层：订阅交易所 WS / REST 行情，输出标准化 tick / kline。

本子包定义统一的 `KlineSource` Protocol 与 `Kline` 值对象，并在 `binance/` 等子包
下提供具体实现。runner / strategies 层只依赖 Protocol，不 import 具体类
（与 `copy_trader.exchanges` 同样的边界规则，由 `.importlinter` `marketdata-only-core`
contract 强制）。
"""

from copy_trader.marketdata.base import Kline, KlineSource
from copy_trader.marketdata.binance import BinanceMarketdata
from copy_trader.marketdata.hyperliquid import HyperliquidMarketdata

__all__ = [
    "BinanceMarketdata",
    "HyperliquidMarketdata",
    "Kline",
    "KlineSource",
]
