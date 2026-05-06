"""Hyperliquid spot adapter (issue #20)。

实装 Exchange Protocol 用 hyperliquid-python-sdk (官方 SDK 自带 EIP-712 签名)。
私钥**仅**通过参数注入(来源是 envvar / settings 的 *_PRIVATE_KEY),不在仓库
hardcode。

测试用 mock SDK 客户端,不真打 api.hyperliquid.xyz (pytest-socket --disable-socket
双保险)。

约束:
- name = "hyperliquid.spot"
- round_price/round_qty 按 SDK meta 拿到的 px_decimals/sz_decimals 量化
- 限频: sliding-window + 1ms epsilon (与 binance 风格对齐)
"""

from __future__ import annotations

from copy_trader.exchanges.hyperliquid.spot import HyperliquidSpot, make_hyperliquid_spot_factory

__all__ = ["HyperliquidSpot", "make_hyperliquid_spot_factory"]
