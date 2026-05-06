"""Binance 公开 K 线行情源。

`BinanceMarketdata` 实现 `KlineSource` Protocol，调用 Binance 公开 REST endpoint
`GET https://api.binance.com/api/v3/klines` 拉取 OHLCV K 线。**不接收任何凭证**
（公开数据无需 API key / signature）—— 这是 acceptance 第一条硬约束。

实装选择：
- HTTP 客户端用 stdlib `urllib.request`，避免引入新依赖（main `dependencies` 当前
  没有 `httpx`；prod `uv sync --frozen` 不会拉 `respx` transitive 的 httpx）。
- Binance `klines` 返回数组 `[open_ts_ms, "open", "high", "low", "close",
  "volume", close_ts_ms, ...]`；ms epoch 转成 `datetime.fromtimestamp(ms/1000,
  tz=timezone.utc)` 满足 `Kline` 的 UTC 校验。
- 数值字段 Binance 都返回字符串，直接喂 `Decimal(str)` 不丢精度。
- 5xx / 网络错误：抛 `BinanceMarketdataError`（不让 `urllib.error.HTTPError` /
  `URLError` 直接外泄给上层）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from copy_trader.marketdata.base import Kline

_KLINES_URL: Final[str] = "https://api.binance.com/api/v3/klines"
_HTTP_TIMEOUT_SEC: Final[float] = 10.0


class BinanceMarketdataError(RuntimeError):
    """Binance marketdata 调用错误（HTTP 5xx / 网络异常 / 解析失败）。"""


def _open_url(url: str, *, timeout: float) -> bytes:
    """thin wrapper around `urllib.request.urlopen`。

    抽出来是为了让测试可以 `unittest.mock.patch` 这一个点，而不是去戳整个
    `urllib.request.urlopen`（避免影响 stdlib 其它测试）。
    """
    with urllib_request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (https URL)
        data: bytes = resp.read()
    return data


class BinanceMarketdata:
    """Binance 现货公开 K 线源（实现 `KlineSource` Protocol）。

    ```python
    md = BinanceMarketdata()
    klines = md.fetch_klines("BTCUSDT", "1m", limit=100)
    assert klines[0].open_ts.tzinfo is timezone.utc
    ```
    """

    name: str = "binance.spot"

    def __init__(self) -> None:
        # 显式不接受 api_key / secret 等凭证参数：公开 K 线不需要。
        # acceptance: "marketdata.binance 模块不引入凭证依赖"。
        pass

    def fetch_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 100,
    ) -> list[Kline]:
        """调 `GET /api/v3/klines?symbol=&interval=&limit=` 返回 `list[Kline]`。

        - HTTP 5xx / 连接失败 / JSON 解析失败 → `BinanceMarketdataError`
        - 返回的每根 K 线 `open_ts / close_ts` 一致 `tzinfo=timezone.utc`
        """
        if limit <= 0:
            raise ValueError(f"limit must be > 0, got {limit!r}")
        query = urllib_parse.urlencode({"symbol": symbol, "interval": interval, "limit": limit})
        url = f"{_KLINES_URL}?{query}"
        try:
            raw = _open_url(url, timeout=_HTTP_TIMEOUT_SEC)
        except urllib_error.HTTPError as exc:
            raise BinanceMarketdataError(f"binance klines HTTP {exc.code}: {exc.reason}") from exc
        except urllib_error.URLError as exc:
            raise BinanceMarketdataError(f"binance klines network error: {exc.reason}") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BinanceMarketdataError(f"binance klines invalid JSON: {exc}") from exc

        if not isinstance(payload, list):
            raise BinanceMarketdataError(
                f"binance klines unexpected payload type: {type(payload).__name__}"
            )

        return [_parse_kline(row) for row in payload]


def _parse_kline(row: object) -> Kline:
    """把 Binance 单根 K 线数组解析成 `Kline` 值对象。

    Binance 返回每根 K 线是固定顺序数组：
    `[open_ts_ms, "open", "high", "low", "close", "volume", close_ts_ms, ...]`
    后续字段（quote volume / trades / taker volume / ignore）当前忽略。
    """
    if not isinstance(row, list) or len(row) < 7:
        raise BinanceMarketdataError(f"binance klines row malformed: {row!r}")
    open_ts_ms, open_, high, low, close, volume, close_ts_ms = row[:7]
    if not isinstance(open_ts_ms, int) or not isinstance(close_ts_ms, int):
        raise BinanceMarketdataError(
            f"binance klines timestamps must be int ms, got {open_ts_ms!r} / {close_ts_ms!r}"
        )
    return Kline(
        open_ts=datetime.fromtimestamp(open_ts_ms / 1000, tz=UTC),
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=Decimal(str(volume)),
        close_ts=datetime.fromtimestamp(close_ts_ms / 1000, tz=UTC),
    )


__all__ = [
    "BinanceMarketdata",
    "BinanceMarketdataError",
]
