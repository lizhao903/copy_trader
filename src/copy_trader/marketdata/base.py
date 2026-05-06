"""`KlineSource` Protocol 与 `Kline` 值对象：marketdata 层统一接口。

所有公开 K 线来源（`BinanceMarketdata`、未来的 `BybitMarketdata` 等）MUST 实现
`KlineSource` 这个结构子类型，runner / strategies 层只依赖此 Protocol，不 import
具体实现类（与 exchange-adapter 同样的 import-linter 边界规则）。

`Kline` 是不可变（`frozen=True`）的 K 线值对象：所有数值用 `decimal.Decimal`
（金融场景禁止 float），所有时间戳必须 `tz=timezone.utc`，与 `Order.ts` /
`Fill.ts` 同样的 UTC 时区一致性保证。具体来源（如 Binance 返回 ms epoch）
负责把它转成 `datetime.fromtimestamp(ms/1000, tz=timezone.utc)`。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, model_validator

_ZERO_OFFSET = timedelta(0)


class Kline(BaseModel):
    """不可变 K 线值对象（OHLCV + open/close 时间戳）。

    - 所有数值字段（`open / high / low / close / volume`）：`Decimal`，禁止 float
    - `open_ts / close_ts`：`tzinfo == timezone.utc`，由 `model_validator` 强校验
    - `close_ts` 必须 `>= open_ts`
    """

    model_config = ConfigDict(frozen=True, strict=True)

    open_ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_ts: datetime

    @model_validator(mode="after")
    def _validate_utc_and_order(self) -> Kline:
        if self.open_ts.tzinfo is None or self.open_ts.utcoffset() != _ZERO_OFFSET:
            raise ValueError("open_ts must be UTC (tzinfo=timezone.utc)")
        if self.close_ts.tzinfo is None or self.close_ts.utcoffset() != _ZERO_OFFSET:
            raise ValueError("close_ts must be UTC (tzinfo=timezone.utc)")
        if self.close_ts < self.open_ts:
            raise ValueError("close_ts must be >= open_ts")
        return self


@runtime_checkable
class KlineSource(Protocol):
    """公开 K 线行情源的统一接口（结构子类型）。

    `name` 属性遵循 `<venue>.<market>` 命名规范（如 `binance.spot`），与
    `exchanges` 层的 `Exchange.name` 同构，便于 runner 在选交易所时同步选 marketdata。

    实现 MUST 不接收凭证（公开 K 线无需 API key / signature）。
    """

    name: str

    def fetch_klines(self, symbol: str, interval: str, limit: int = 100) -> list[Kline]:
        """拉取 `symbol` 在 `interval` 粒度的最近 `limit` 根 K 线。

        - `symbol` 由具体实现决定大小写与分隔（Binance 用 `BTCUSDT`）
        - `interval` 同样由具体实现决定（Binance 支持 `1m/5m/1h/1d/...`）
        - `limit` 一般 1..1000，越界由具体实现报错
        """
        ...
