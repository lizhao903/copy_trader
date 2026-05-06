"""`BinanceSpot`：Binance 现货 REST 适配器。

实装 `Exchange` Protocol 全部 9 个方法 + `name` 属性。spec 关键约束：

- **限频**：内置 sliding-window rate limiter；超阈值时**主动 sleep**而不是抛
  错（`exchange-adapter` spec 第 3 个 Requirement）。Binance spot 默认 1200
  req/min，本实装保守取 800 req/min。
- **精度规则**：启动时（首次需要某 symbol 时）拉 `/api/v3/exchangeInfo`，缓
  存 `PRICE_FILTER.tickSize` / `LOT_SIZE.stepSize`，`round_price/round_qty`
  按精度倍数**向下舍入**（保守不放大下单数量/价格）。
- **域内部封装**：所有 venue 私有逻辑（`Spot` SDK 调用、JSON → core 模型映
  射、限频、精度）都收口在本文件，不向上层泄漏。
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

from binance.spot import Spot  # type: ignore[import-untyped]

from copy_trader.core import (
    Fill,
    Order,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    SymbolInfo,
)

# Binance spot REST 默认上限 1200 req/min；本实装保守 800 req/min。
_DEFAULT_RATE_LIMIT_PER_MIN = 800
_RATE_LIMIT_WINDOW_SEC = 60.0


class _RateLimiter:
    """sliding-window rate limiter；超阈值时主动 `time.sleep` 而不是抛错。

    线程安全：所有访问 `_timestamps` 都加锁。`time.sleep` 在锁外释放（`acquire`
    内部 split 成 compute-wait → unlock → sleep → re-lock 重检）。
    """

    def __init__(self, max_per_window: int, window_sec: float) -> None:
        self._max = max_per_window
        self._window = window_sec
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """登记一次请求；窗口内已满则 `sleep` 至最早请求滑出窗口。"""
        while True:
            with self._lock:
                now = time.monotonic()
                # 清理已经滑出窗口的旧记录
                cutoff = now - self._window
                while self._timestamps and self._timestamps[0] < cutoff:
                    self._timestamps.popleft()

                if len(self._timestamps) < self._max:
                    self._timestamps.append(now)
                    return

                # 满了：算出最早记录滑出窗口需要等多久（多 sleep 一点点
                # 避免 monotonic 边界 (oldest + window == now) 时再次 cutoff
                # 不过的死循环）
                wait = self._timestamps[0] + self._window - now + 1e-3
            if wait > 0:
                time.sleep(wait)
            # sleep 完重新进 while 循环再检（可能并发又被填满）


def _quantize_floor(value: Decimal, step: Decimal) -> Decimal:
    """按 `step` 向下舍入到精度倍数（保守不放大）。"""
    if step <= 0:
        raise ValueError(f"step must be positive, got {step!r}")
    quantized = (value / step).to_integral_value(rounding=ROUND_DOWN) * step
    # 归一化精度，避免 `Decimal('0.10000') != Decimal('0.1')` 之类的展示差异
    return quantized.normalize() if quantized != 0 else Decimal("0")


class BinanceSpot:
    """Binance 现货 REST 适配器（实装 `Exchange` Protocol）。"""

    name: str = "binance.spot"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        client: Spot | None = None,
        rate_limit_per_min: int = _DEFAULT_RATE_LIMIT_PER_MIN,
    ) -> None:
        """构造适配器。

        Args:
            api_key: Binance API key（敏感数据；MUST 通过参数注入，不入库）。
            api_secret: 对应 secret。
            testnet: True 走 testnet endpoint。
            client: 可选，注入自定义 `Spot` 客户端（测试用 mock）。
            rate_limit_per_min: 每分钟请求上限，默认 800。
        """
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet

        if client is not None:
            self._client = client
        else:
            base_url = "https://testnet.binance.vision" if testnet else "https://api.binance.com"
            self._client = Spot(api_key=api_key, api_secret=api_secret, base_url=base_url)

        self._rate_limiter = _RateLimiter(rate_limit_per_min, _RATE_LIMIT_WINDOW_SEC)
        self._symbol_info_cache: dict[str, SymbolInfo] = {}
        self._cache_lock = threading.Lock()

    # ---- 内部辅助 ---------------------------------------------------------

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """统一入口：限频 → 调 `Spot` 客户端方法。"""
        self._rate_limiter.acquire()
        method = getattr(self._client, method_name)
        return method(*args, **kwargs)

    def _load_symbol_info(self, symbol: str) -> SymbolInfo:
        """命中缓存直接返回；否则拉 exchangeInfo 解析后写缓存。"""
        with self._cache_lock:
            cached = self._symbol_info_cache.get(symbol)
        if cached is not None:
            return cached

        raw = self._call("exchange_info", symbol=symbol)
        symbols = raw.get("symbols") or []
        if not symbols:
            raise ValueError(f"binance exchangeInfo returned no symbol info for {symbol!r}")
        meta = symbols[0]

        tick_size: Decimal | None = None
        step_size: Decimal | None = None
        min_notional: Decimal = Decimal("0")
        for f in meta.get("filters", []):
            ftype = f.get("filterType")
            if ftype == "PRICE_FILTER":
                tick_size = Decimal(str(f["tickSize"]))
            elif ftype == "LOT_SIZE":
                step_size = Decimal(str(f["stepSize"]))
            elif ftype in ("MIN_NOTIONAL", "NOTIONAL"):
                # MIN_NOTIONAL 字段名 minNotional；NOTIONAL（v2）字段名同
                raw_min = f.get("minNotional") or f.get("notional") or "0"
                min_notional = Decimal(str(raw_min))

        if tick_size is None or step_size is None:
            raise ValueError(f"binance exchangeInfo missing PRICE_FILTER/LOT_SIZE for {symbol!r}")

        info = SymbolInfo(
            venue=self.name,
            symbol=symbol,
            base_asset=str(meta.get("baseAsset", "")),
            quote_asset=str(meta.get("quoteAsset", "")),
            tick_size=tick_size,
            step_size=step_size,
            min_notional=min_notional,
        )
        with self._cache_lock:
            self._symbol_info_cache[symbol] = info
        return info

    @staticmethod
    def _parse_ts(ms: int | str | None) -> datetime:
        if ms is None:
            return datetime.now(tz=UTC)
        return datetime.fromtimestamp(int(ms) / 1000.0, tz=UTC)

    @staticmethod
    def _map_status(raw_status: str) -> OrderStatus:
        m: dict[str, OrderStatus] = {
            "NEW": "new",
            "PARTIALLY_FILLED": "partially_filled",
            "FILLED": "filled",
            "CANCELED": "canceled",
            "PENDING_CANCEL": "canceled",
            "REJECTED": "rejected",
            "EXPIRED": "canceled",
            "EXPIRED_IN_MATCH": "canceled",
        }
        return m.get(raw_status, "new")

    @staticmethod
    def _map_side_in(side: OrderSide) -> str:
        return "BUY" if side == "buy" else "SELL"

    @staticmethod
    def _map_side_out(raw: str) -> OrderSide:
        return "buy" if raw.upper() == "BUY" else "sell"

    @staticmethod
    def _map_type_in(t: OrderType) -> str:
        return "MARKET" if t == "market" else "LIMIT"

    @staticmethod
    def _map_type_out(raw: str) -> OrderType:
        return "market" if raw.upper() == "MARKET" else "limit"

    # ---- Exchange Protocol --------------------------------------------------

    def get_balance(self, asset: str) -> Decimal:
        """返回账户某资产的可用余额（`free` 字段）。"""
        data = self._call("account")
        for b in data.get("balances", []):
            if b.get("asset") == asset:
                return Decimal(str(b.get("free", "0")))
        return Decimal("0")

    def fetch_position(self, symbol: str) -> Position:
        """现货账户没有持仓概念；用 base_asset 的 free + locked 余额合成 Position。

        `avg_cost / realized_pnl` 现货从余额无法直接拿到，置 0；上层 `pnl` 子包
        负责从 ledger 重建真实成本。本字段满足 Protocol 即可。
        """
        info = self._load_symbol_info(symbol)
        data = self._call("account")
        qty = Decimal("0")
        for b in data.get("balances", []):
            if b.get("asset") == info.base_asset:
                qty = Decimal(str(b.get("free", "0"))) + Decimal(str(b.get("locked", "0")))
                break
        return Position(
            account="binance.spot",
            symbol=symbol,
            qty=qty,
            avg_cost=Decimal("0"),
            realized_pnl=Decimal("0"),
            updated_ts=self._parse_ts(data.get("updateTime")),
        )

    def place_order(self, req: OrderRequest) -> Order:
        """下单；返回带交易所 id/status/ts 的 `Order`。"""
        params: dict[str, Any] = {
            "symbol": req.symbol,
            "side": self._map_side_in(req.side),
            "type": self._map_type_in(req.type),
            "quantity": str(req.qty),
        }
        if req.type == "limit":
            params["timeInForce"] = "GTC"
            if req.price is None:
                raise ValueError("limit order requires explicit price")
            params["price"] = str(req.price)
            params["newOrderRespType"] = "FULL"
        else:
            params["newOrderRespType"] = "FULL"

        raw = self._call("new_order", **params)

        order_id = str(raw.get("orderId") or raw.get("clientOrderId") or "")
        status = self._map_status(str(raw.get("status", "NEW")))
        ts = self._parse_ts(raw.get("transactTime") or raw.get("workingTime"))

        # market 单返回 price=0；构造 Order 时按 type 决定是否带 price
        order_price: Decimal | None
        if req.type == "limit":
            order_price = req.price
        else:
            order_price = None

        return Order(
            id=order_id,
            account=req.account,
            symbol=req.symbol,
            side=req.side,
            type=req.type,
            qty=req.qty,
            price=order_price,
            status=status,
            ts=ts,
        )

    def cancel(self, order_id: str) -> None:
        """按 Binance orderId 撤单；本实装要求调用方在 order_id 前缀 `<symbol>:`，
        因为 Binance 撤单必须带 symbol。约定：`order_id` 形如 `"BTCUSDT:12345"`。

        如果 order_id 没有 ':'，则按全账户撤单不允许，直接 ValueError。
        """
        if ":" not in order_id:
            raise ValueError(
                f"binance cancel requires order_id of form '<symbol>:<id>', got {order_id!r}"
            )
        symbol, raw_id = order_id.split(":", 1)
        self._call("cancel_order", symbol=symbol, orderId=int(raw_id))

    def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        """返回未成交订单列表。"""
        kwargs: dict[str, Any] = {}
        if symbol is not None:
            kwargs["symbol"] = symbol
        raw = self._call("get_open_orders", **kwargs)
        out: list[Order] = []
        for o in raw or []:
            o_type_raw = str(o.get("type", "MARKET"))
            o_type = self._map_type_out(o_type_raw)
            o_price_str = str(o.get("price", "0"))
            o_price: Decimal | None = Decimal(o_price_str) if o_type == "limit" else None
            # market 单不带 price，但 Binance 偶尔回 "0"，需要兜底转换
            if o_type == "limit" and (o_price is None or o_price == 0):
                o_price = Decimal(o_price_str)
            out.append(
                Order(
                    id=str(o.get("orderId", "")),
                    account="binance.spot",
                    symbol=str(o.get("symbol", "")),
                    side=self._map_side_out(str(o.get("side", "BUY"))),
                    type=o_type,
                    qty=Decimal(str(o.get("origQty", "0"))),
                    price=o_price,
                    status=self._map_status(str(o.get("status", "NEW"))),
                    ts=self._parse_ts(o.get("time")),
                )
            )
        return out

    def fetch_fills(self, symbol: str, since: datetime | None = None) -> list[Fill]:
        """通过 `myTrades` 拉取成交记录。"""
        kwargs: dict[str, Any] = {"symbol": symbol}
        if since is not None:
            kwargs["startTime"] = int(since.timestamp() * 1000)
        raw = self._call("my_trades", **kwargs)
        fills: list[Fill] = []
        for t in raw or []:
            side: OrderSide = "buy" if t.get("isBuyer", False) else "sell"
            fills.append(
                Fill(
                    id=str(t.get("id", "")),
                    ts=self._parse_ts(t.get("time")),
                    account="binance.spot",
                    symbol=str(t.get("symbol", symbol)),
                    side=side,
                    qty=Decimal(str(t.get("qty", "0"))),
                    price=Decimal(str(t.get("price", "0"))),
                    fee=Decimal(str(t.get("commission", "0"))),
                    fee_asset=str(t.get("commissionAsset", "")),
                    exchange_order_id=str(t.get("orderId", "")),
                    env_tag="prod" if not self._testnet else "paper",
                    machine_id="",
                    schema_version=1,
                )
            )
        return fills

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """返回缓存的（或首次拉取的）`SymbolInfo`。"""
        return self._load_symbol_info(symbol)

    def round_price(self, symbol: str, price: Decimal) -> Decimal:
        """按 `tick_size` 向下舍入。"""
        info = self._load_symbol_info(symbol)
        return _quantize_floor(price, info.tick_size)

    def round_qty(self, symbol: str, qty: Decimal) -> Decimal:
        """按 `step_size` 向下舍入。"""
        info = self._load_symbol_info(symbol)
        return _quantize_floor(qty, info.step_size)


# 模块级 unique-id 生成器（测试 idempotency 用）
def _generate_client_order_id() -> str:
    return f"ct-{uuid.uuid4().hex[:16]}"
