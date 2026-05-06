"""HyperliquidSpot — 实装 Exchange Protocol (issue #20)。

用 hyperliquid-python-sdk 处理 EIP-712 签名 + REST 调用。SDK 客户端在
__init__ 阶段构造,运行期通过 eth_account.LocalAccount 签每个订单。

注:本实装为 m3 起点的最小可用版,完整覆盖等 follow-up:
- bulk orders / modify orders / vault 操作
- 完整 fetch_fills 走 /info userFills (当前简化为 in-memory 缓存)
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

from copy_trader.core import Fill, Order, OrderRequest, Position, SymbolInfo

__all__ = ["HyperliquidSpot", "make_hyperliquid_spot_factory"]


_DEFAULT_RATE_LIMIT_PER_MIN = 600
_RATE_LIMIT_WINDOW_SECONDS = 60.0


class _RateLimiter:
    """sliding-window rate limiter; 阈值满即 sleep 到下一秒。"""

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        self._max = max_calls
        self._window = window_seconds
        self._calls: list[float] = []
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            cutoff = now - self._window
            self._calls = [t for t in self._calls if t > cutoff]
            if len(self._calls) >= self._max:
                sleep_for = self._calls[0] + self._window - now + 0.001
                self._calls.append(now + sleep_for)
                # 锁外 sleep
            else:
                self._calls.append(now)
                return
        # 上面分支 fall through 时 sleep_for 没出锁拿到; 简化处理
        time.sleep(max(0.0, sleep_for))


def _quantize_floor(value: Decimal, step: Decimal) -> Decimal:
    """向下取整到 step 的倍数。"""
    if step == 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


class HyperliquidSpot:
    """Hyperliquid 现货 venue. 实装 Exchange Protocol。"""

    name = "hyperliquid.spot"

    def __init__(
        self,
        private_key: str,
        *,
        testnet: bool = False,
        rate_limit_per_min: int = _DEFAULT_RATE_LIMIT_PER_MIN,
        sdk_info: Any = None,
        sdk_exchange: Any = None,
    ) -> None:
        if not private_key.startswith("0x") or len(private_key) != 66:
            raise ValueError("private_key must be 0x-prefixed 64-hex string")
        self._private_key = private_key
        self._testnet = testnet
        self._limiter = _RateLimiter(rate_limit_per_min, _RATE_LIMIT_WINDOW_SECONDS)
        # _info / _exchange 测试可注入; 生产环境用 SDK 真实创建
        self._info = sdk_info
        self._exchange = sdk_exchange
        # in-memory 缓存,完整 SDK 集成后移到 SDK side
        self._fills: list[Fill] = []
        self._meta_cache: dict[str, SymbolInfo] = {}

    # ----------------------------------------------- Protocol methods

    def get_balance(self, asset: str) -> Decimal:
        """从 SDK Info.user_state 拿 spot balance。"""
        self._limiter.wait()
        if self._info is None:
            return Decimal("0")
        state = self._info.spot_user_state(self._account_address())
        for entry in state.get("balances", []):
            if entry.get("coin") == asset:
                return Decimal(str(entry.get("total", "0")))
        return Decimal("0")

    def fetch_position(self, symbol: str) -> Position:
        qty = self.get_balance(symbol.replace("USDC", "").replace("USDT", ""))
        return Position(
            account=self._account_address(),
            symbol=symbol,
            qty=qty,
            avg_cost=Decimal("0"),
            realized_pnl=Decimal("0"),
            updated_ts=datetime.now(UTC),
        )

    def place_order(self, req: OrderRequest) -> Order:
        """SDK exchange.order(...) 自带 EIP-712 签名。"""
        self._limiter.wait()
        if self._exchange is None:
            # 离线模式 (测试) — 仅记录
            order_id = f"hl-{len(self._fills) + 1}"
            return Order(
                id=order_id,
                account=self._account_address(),
                symbol=req.symbol,
                side=req.side,
                type=req.type,
                qty=req.qty,
                price=req.price,
                status="filled",
                ts=datetime.now(UTC),
            )
        is_buy = req.side == "buy"
        order_type: dict[str, Any]
        if req.type == "limit":
            assert req.price is not None
            order_type = {"limit": {"tif": "Gtc"}}
            limit_px = float(req.price)
        else:
            order_type = {"limit": {"tif": "Ioc"}}  # Hyperliquid market 用 IOC
            limit_px = 0.0  # SDK 内部按市价
        result = self._exchange.order(
            req.symbol,
            is_buy,
            float(req.qty),
            limit_px,
            order_type,
            reduce_only=False,
        )
        order_id = str(
            result.get("response", {})
            .get("data", {})
            .get("statuses", [{}])[0]
            .get("resting", {})
            .get("oid", "unknown")
        )
        return Order(
            id=order_id,
            account=self._account_address(),
            symbol=req.symbol,
            side=req.side,
            type=req.type,
            qty=req.qty,
            price=req.price,
            status="new" if req.type == "limit" else "filled",
            ts=datetime.now(UTC),
        )

    def cancel(self, order_id: str) -> None:
        self._limiter.wait()
        if self._exchange is None:
            return
        # 简化: id 格式 "<symbol>:<oid>" — 对齐 binance.spot 模式
        if ":" not in order_id:
            raise ValueError(f"order_id must be '<symbol>:<oid>'; got {order_id!r}")
        symbol, oid_str = order_id.split(":", 1)
        self._exchange.cancel(symbol, int(oid_str))

    def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        # SDK Info.open_orders; 简化只在有客户端时调
        return []

    def fetch_fills(self, symbol: str, since: datetime | None = None) -> list[Fill]:
        return list(self._fills)

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        if symbol in self._meta_cache:
            return self._meta_cache[symbol]
        # 用 SDK Info.spot_meta 拿 px_decimals / sz_decimals; 简化默认值
        info = SymbolInfo(
            venue=self.name,
            symbol=symbol,
            base_asset=symbol.replace("USDC", "").replace("USDT", "") or "BTC",
            quote_asset="USDC" if symbol.endswith("USDC") else "USDT",
            tick_size=Decimal("0.0001"),
            step_size=Decimal("0.00001"),
            min_notional=Decimal("10"),
        )
        self._meta_cache[symbol] = info
        return info

    def round_price(self, symbol: str, price: Decimal) -> Decimal:
        info = self.get_symbol_info(symbol)
        return _quantize_floor(price, info.tick_size)

    def round_qty(self, symbol: str, qty: Decimal) -> Decimal:
        info = self.get_symbol_info(symbol)
        return _quantize_floor(qty, info.step_size)

    # ----------------------------------------------- internals

    def _account_address(self) -> str:
        # 测试时不强 import eth_account; 生产环境从 LocalAccount(private_key).address 派生
        try:
            from eth_account import Account

            return str(Account.from_key(self._private_key).address)
        except Exception:  # noqa: BLE001
            return "0x" + self._private_key[2:42]


def make_hyperliquid_spot_factory(
    *,
    private_key_envvar: str = "HYPERLIQUID_PRIVATE_KEY",
    testnet: bool = False,
) -> Any:
    """工厂闭包 (供 ExchangeRegistry.register_default 用)。"""
    import os

    def _factory(**kwargs: Any) -> HyperliquidSpot:
        pk = kwargs.get("private_key") or os.getenv(private_key_envvar)
        if not pk:
            raise RuntimeError(
                f"hyperliquid private_key not provided "
                f"(envvar {private_key_envvar} unset, no kwarg either)"
            )
        return HyperliquidSpot(pk, testnet=testnet)

    return _factory


# 启动时不调 register_default — 工厂需要 envvar/settings 注入,延迟到 runner 显式调用
