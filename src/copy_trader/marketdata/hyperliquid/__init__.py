"""Hyperliquid 公开 K 线行情源。

`HyperliquidMarketdata` 实现 `KlineSource` Protocol，通过 Hyperliquid 公开 REST
endpoint `POST https://api.hyperliquid.xyz/info` (`type: candleSnapshot`) 拉取
OHLCV K 线。**不接收任何凭证**（公开数据无需 API key / signature）—— 这是
acceptance 第一条硬约束。

实装选择（与 `binance/` 子包风格一致）：

- HTTP 客户端用 stdlib `urllib.request`，避免引入新依赖（main `dependencies`
  当前没有 `httpx`；prod `uv sync --frozen` 不会拉 `respx` transitive 的 httpx）。
- Hyperliquid `/info` 是 POST + JSON body：
  ``{"type": "candleSnapshot", "req": {"coin": <symbol>, "interval": <interval>,
  "startTime": <ms>, "endTime": <ms>}}``，返回数组 ``[{"t": open_ts_ms,
  "T": close_ts_ms, "s": symbol, "i": interval, "o": "open", "c": "close",
  "h": "high", "l": "low", "v": "volume", "n": trades}, ...]``。
- 没有原生 `limit` 参数；用 `interval × limit` 反推 `startTime = endTime - span`，
  服务端按 `[startTime, endTime]` 闭区间返回；如果服务端多返了几根，本地切尾保留
  最后 `limit` 根。这样 `KlineSource.fetch_klines(symbol, interval, limit)` 与
  Binance 实现保持同语义。
- ms epoch 转成 `datetime.fromtimestamp(ms/1000, tz=UTC)` 满足 `Kline` 的 UTC
  校验（与 `BinanceMarketdata` 完全一致）。
- 数值字段 Hyperliquid 返回字符串，喂 `Decimal(str)` 不丢精度。
- 5xx / 网络错误：抛 `HyperliquidMarketdataError`（不让 `urllib.error.HTTPError`
  / `URLError` 直接外泄给上层）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final
from urllib import error as urllib_error
from urllib import request as urllib_request

from copy_trader.marketdata.base import Kline

_INFO_URL: Final[str] = "https://api.hyperliquid.xyz/info"
_HTTP_TIMEOUT_SEC: Final[float] = 10.0

# Hyperliquid `candleSnapshot` 支持的 interval ↔ 毫秒数。
# 与官方文档 candleSnapshot 列出的颗粒度对齐：
# 1m / 3m / 5m / 15m / 30m / 1h / 2h / 4h / 8h / 12h / 1d / 3d / 1w / 1M
_INTERVAL_MS: Final[dict[str, int]] = {
    "1m": 60_000,
    "3m": 3 * 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "2h": 2 * 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "8h": 8 * 60 * 60_000,
    "12h": 12 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
    "3d": 3 * 24 * 60 * 60_000,
    "1w": 7 * 24 * 60 * 60_000,
    # 1M 用 31 天近似上界，仅用于反推 startTime；实际窗口由服务端裁剪。
    "1M": 31 * 24 * 60 * 60_000,
}


class HyperliquidMarketdataError(RuntimeError):
    """Hyperliquid marketdata 调用错误（HTTP 5xx / 网络异常 / 解析失败）。"""


def _open_url(url: str, *, data: bytes, headers: dict[str, str], timeout: float) -> bytes:
    """thin wrapper around `urllib.request.urlopen` (POST)。

    抽出来是为了让测试可以 `unittest.mock.patch` 这一个点，而不是去戳整个
    `urllib.request.urlopen`（避免影响 stdlib 其它测试）。与 `binance/_open_url`
    同名同位置，签名因 POST 多了 `data` / `headers` 参数。
    """
    req = urllib_request.Request(url, data=data, headers=headers, method="POST")
    with urllib_request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https URL)
        body: bytes = resp.read()
    return body


def _now_ms() -> int:
    """当前 UTC 时间的毫秒 epoch（抽成函数便于测试 patch）。"""
    return int(datetime.now(tz=UTC).timestamp() * 1000)


class HyperliquidMarketdata:
    """Hyperliquid 现货公开 K 线源（实现 `KlineSource` Protocol）。

    ```python
    md = HyperliquidMarketdata()
    klines = md.fetch_klines("BTC", "1m", limit=100)
    assert klines[0].open_ts.tzinfo is timezone.utc
    ```
    """

    name: str = "hyperliquid.spot"

    def __init__(self) -> None:
        # 显式不接受 api_key / secret 等凭证参数：公开 K 线不需要。
        # acceptance: "marketdata.hyperliquid 模块不引入凭证依赖"。
        pass

    def fetch_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 100,
    ) -> list[Kline]:
        """POST `/info` `type=candleSnapshot` 返回 `list[Kline]`。

        - `limit <= 0` → `ValueError`
        - 未知 `interval` → `ValueError`（Hyperliquid 支持的颗粒度有限）
        - HTTP 5xx / 连接失败 / JSON 解析失败 → `HyperliquidMarketdataError`
        - 返回的每根 K 线 `open_ts / close_ts` 一致 `tzinfo=timezone.utc`
        - 服务端多返时切尾保留最后 `limit` 根
        """
        if limit <= 0:
            raise ValueError(f"limit must be > 0, got {limit!r}")
        if interval not in _INTERVAL_MS:
            raise ValueError(
                f"unsupported interval {interval!r}; supported: {sorted(_INTERVAL_MS.keys())}"
            )

        end_ms = _now_ms()
        # 多取一点防止边界整除导致少 1 根（与 Binance limit 闭区间语义对齐）。
        start_ms = end_ms - _INTERVAL_MS[interval] * (limit + 1)
        body = json.dumps(
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": symbol,
                    "interval": interval,
                    "startTime": start_ms,
                    "endTime": end_ms,
                },
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        try:
            raw = _open_url(_INFO_URL, data=body, headers=headers, timeout=_HTTP_TIMEOUT_SEC)
        except urllib_error.HTTPError as exc:
            raise HyperliquidMarketdataError(
                f"hyperliquid candleSnapshot HTTP {exc.code}: {exc.reason}"
            ) from exc
        except urllib_error.URLError as exc:
            raise HyperliquidMarketdataError(
                f"hyperliquid candleSnapshot network error: {exc.reason}"
            ) from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HyperliquidMarketdataError(
                f"hyperliquid candleSnapshot invalid JSON: {exc}"
            ) from exc

        if not isinstance(payload, list):
            raise HyperliquidMarketdataError(
                f"hyperliquid candleSnapshot unexpected payload type: {type(payload).__name__}"
            )

        klines = [_parse_kline(row) for row in payload]
        if len(klines) > limit:
            klines = klines[-limit:]
        return klines


def _parse_kline(row: object) -> Kline:
    """把 Hyperliquid 单根 K 线字典解析成 `Kline` 值对象。

    Hyperliquid 返回每根 K 线是字典：
    ``{"t": open_ts_ms, "T": close_ts_ms, "s": symbol, "i": interval,
       "o": "open", "c": "close", "h": "high", "l": "low",
       "v": "volume", "n": trades}``
    """
    if not isinstance(row, dict):
        raise HyperliquidMarketdataError(f"hyperliquid candleSnapshot row malformed: {row!r}")
    try:
        open_ts_ms = row["t"]
        close_ts_ms = row["T"]
        open_ = row["o"]
        high = row["h"]
        low = row["l"]
        close = row["c"]
        volume = row["v"]
    except KeyError as exc:
        raise HyperliquidMarketdataError(
            f"hyperliquid candleSnapshot row missing key {exc.args[0]!r}: {row!r}"
        ) from exc

    if not isinstance(open_ts_ms, int) or not isinstance(close_ts_ms, int):
        raise HyperliquidMarketdataError(
            f"hyperliquid candleSnapshot timestamps must be int ms, "
            f"got {open_ts_ms!r} / {close_ts_ms!r}"
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
    "HyperliquidMarketdata",
    "HyperliquidMarketdataError",
]
