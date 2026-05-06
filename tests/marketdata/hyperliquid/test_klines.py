"""`HyperliquidMarketdata.fetch_klines` 单测：全部 mock，绝不真打 api.hyperliquid.xyz。

测试场景（issue #21 deliverables 第 5 条，与 Binance 对齐）：
1. fetch 1m × 5 根 → 解析成 `list[Kline]`，字段类型 / UTC 时区都正确
2. `limit=10` 透传到 POST body 中（用 endTime - interval*(limit+1) 反推 startTime）
3. 多 interval 参数化（1m / 5m / 1h / 1d）都能调用
4. HTTP 5xx → 抛 `HyperliquidMarketdataError`（不是 unhandled exception）
5. 实例化不传 api_key，直接 fetch_klines 成功（无凭证依赖）

mock 选用 `unittest.mock.patch` 替换 `_open_url`（thin wrapper），不依赖
`respx`（respx 只 mock httpx；本实现用 stdlib urllib）。`pytest-socket`
`--disable-socket` 即使 mock 失败也会拦下真实网络访问，双保险。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import patch
from urllib import error as urllib_error

import pytest

from copy_trader.marketdata import HyperliquidMarketdata, Kline, KlineSource
from copy_trader.marketdata.hyperliquid import HyperliquidMarketdataError

# Hyperliquid candleSnapshot row schema:
# {"t": open_ts_ms, "T": close_ts_ms, "s": symbol, "i": interval,
#  "o": "open", "c": "close", "h": "high", "l": "low", "v": "volume", "n": trades}
_SAMPLE_OPEN_TS_MS = 1_714_000_000_000  # 2024-04-25T00:26:40Z
_MINUTE_MS = 60_000


def _make_row(
    *,
    open_ts_ms: int,
    o: str = "60000.00",
    h: str = "60100.00",
    low: str = "59900.00",
    c: str = "60050.00",
    v: str = "1.5",
    interval: str = "1m",
    interval_ms: int = _MINUTE_MS,
    coin: str = "BTC",
) -> dict[str, Any]:
    """构造一根 Hyperliquid candleSnapshot 字典（close_ts = open_ts + interval_ms - 1）。"""
    close_ts_ms = open_ts_ms + interval_ms - 1
    return {
        "t": open_ts_ms,
        "T": close_ts_ms,
        "s": coin,
        "i": interval,
        "o": o,
        "c": c,
        "h": h,
        "l": low,
        "v": v,
        "n": 100,
    }


def _mock_payload(rows: list[dict[str, Any]]) -> bytes:
    return json.dumps(rows).encode("utf-8")


class TestFetchKlines:
    """场景 1：fetch_klines 1m × 5，字段 + UTC 校验。"""

    def test_returns_list_of_kline_with_utc_timestamps(self) -> None:
        rows = [_make_row(open_ts_ms=_SAMPLE_OPEN_TS_MS + i * _MINUTE_MS) for i in range(5)]
        md = HyperliquidMarketdata()
        with patch(
            "copy_trader.marketdata.hyperliquid._open_url", return_value=_mock_payload(rows)
        ) as mock_open:
            klines = md.fetch_klines("BTC", "1m", limit=5)

        # 调用了 mock（说明走到 _open_url）
        assert mock_open.call_count == 1
        # 解析成 5 根 Kline
        assert len(klines) == 5
        assert all(isinstance(k, Kline) for k in klines)
        # 字段类型正确
        first = klines[0]
        assert isinstance(first.open, Decimal)
        assert isinstance(first.volume, Decimal)
        assert first.open == Decimal("60000.00")
        assert first.high == Decimal("60100.00")
        assert first.low == Decimal("59900.00")
        assert first.close == Decimal("60050.00")
        assert first.volume == Decimal("1.5")
        # UTC 时区 — acceptance 第 2 条（与 Binance 一致）
        assert first.open_ts.tzinfo == UTC
        assert first.close_ts.tzinfo == UTC
        # ms epoch 转换正确
        assert first.open_ts == datetime.fromtimestamp(_SAMPLE_OPEN_TS_MS / 1000, tz=UTC)
        # close_ts >= open_ts
        for k in klines:
            assert k.close_ts >= k.open_ts

    def test_utc_timestamp_matches_binance_convention(self) -> None:
        """与 Binance marketdata 用同一 ms→datetime 公式（acceptance 硬约束）。"""
        rows = [_make_row(open_ts_ms=_SAMPLE_OPEN_TS_MS)]
        md = HyperliquidMarketdata()
        with patch(
            "copy_trader.marketdata.hyperliquid._open_url", return_value=_mock_payload(rows)
        ):
            klines = md.fetch_klines("BTC", "1m", limit=1)
        # 与 Binance 实现 (`datetime.fromtimestamp(ms/1000, tz=UTC)`) 等价
        expected = datetime.fromtimestamp(_SAMPLE_OPEN_TS_MS / 1000, tz=UTC)
        assert klines[0].open_ts == expected
        assert klines[0].open_ts.tzinfo == UTC


class TestLimitPropagation:
    """场景 2：limit 透传到 POST body 中（startTime/endTime 窗口）。"""

    def test_limit_param_in_body_window(self) -> None:
        rows = [_make_row(open_ts_ms=_SAMPLE_OPEN_TS_MS + i * _MINUTE_MS) for i in range(10)]
        md = HyperliquidMarketdata()
        fixed_now_ms = _SAMPLE_OPEN_TS_MS + 100 * _MINUTE_MS
        with (
            patch(
                "copy_trader.marketdata.hyperliquid._open_url",
                return_value=_mock_payload(rows),
            ) as mock_open,
            patch("copy_trader.marketdata.hyperliquid._now_ms", return_value=fixed_now_ms),
        ):
            md.fetch_klines("BTC", "1m", limit=10)
        # 第 1 个位置参数是 URL，data= kwarg 是 POST body
        called_args, called_kwargs = mock_open.call_args
        url = called_args[0]
        body = json.loads(called_kwargs["data"])
        assert url == "https://api.hyperliquid.xyz/info"
        assert body["type"] == "candleSnapshot"
        assert body["req"]["coin"] == "BTC"
        assert body["req"]["interval"] == "1m"
        # 窗口大小 = interval_ms * (limit + 1)；endTime == fixed_now_ms
        assert body["req"]["endTime"] == fixed_now_ms
        assert body["req"]["startTime"] == fixed_now_ms - _MINUTE_MS * (10 + 1)
        # Content-Type header 正确
        assert called_kwargs["headers"]["Content-Type"] == "application/json"

    def test_default_limit_is_100(self) -> None:
        rows = [_make_row(open_ts_ms=_SAMPLE_OPEN_TS_MS)]
        md = HyperliquidMarketdata()
        fixed_now_ms = _SAMPLE_OPEN_TS_MS + 1000 * _MINUTE_MS
        with (
            patch(
                "copy_trader.marketdata.hyperliquid._open_url",
                return_value=_mock_payload(rows),
            ) as mock_open,
            patch("copy_trader.marketdata.hyperliquid._now_ms", return_value=fixed_now_ms),
        ):
            md.fetch_klines("BTC", "1m")
        body = json.loads(mock_open.call_args.kwargs["data"])
        # 默认 limit=100 → 窗口 = 1m * 101
        assert body["req"]["startTime"] == fixed_now_ms - _MINUTE_MS * (100 + 1)

    def test_limit_zero_or_negative_rejected(self) -> None:
        md = HyperliquidMarketdata()
        with pytest.raises(ValueError, match="limit must be > 0"):
            md.fetch_klines("BTC", "1m", limit=0)
        with pytest.raises(ValueError, match="limit must be > 0"):
            md.fetch_klines("BTC", "1m", limit=-1)

    def test_truncates_excess_rows_to_limit(self) -> None:
        """服务端多返时切尾保留最后 limit 根。"""
        rows = [_make_row(open_ts_ms=_SAMPLE_OPEN_TS_MS + i * _MINUTE_MS) for i in range(20)]
        md = HyperliquidMarketdata()
        with patch(
            "copy_trader.marketdata.hyperliquid._open_url", return_value=_mock_payload(rows)
        ):
            klines = md.fetch_klines("BTC", "1m", limit=5)
        assert len(klines) == 5
        # 保留的是最后 5 根
        assert klines[0].open_ts == datetime.fromtimestamp(
            (_SAMPLE_OPEN_TS_MS + 15 * _MINUTE_MS) / 1000, tz=UTC
        )


class TestMultipleIntervals:
    """场景 3：多 interval 参数化。"""

    @pytest.mark.parametrize("interval", ["1m", "5m", "1h", "1d"])
    def test_interval_passed_through(self, interval: str) -> None:
        ms_per_interval = {
            "1m": 60_000,
            "5m": 5 * 60_000,
            "1h": 60 * 60_000,
            "1d": 24 * 60 * 60_000,
        }[interval]
        rows = [
            _make_row(
                open_ts_ms=_SAMPLE_OPEN_TS_MS,
                interval=interval,
                interval_ms=ms_per_interval,
            )
        ]
        md = HyperliquidMarketdata()
        with patch(
            "copy_trader.marketdata.hyperliquid._open_url", return_value=_mock_payload(rows)
        ) as mock_open:
            klines = md.fetch_klines("ETH", interval, limit=1)
        body = json.loads(mock_open.call_args.kwargs["data"])
        assert body["req"]["interval"] == interval
        assert body["req"]["coin"] == "ETH"
        assert len(klines) == 1
        assert klines[0].open_ts.tzinfo == UTC

    def test_unsupported_interval_rejected(self) -> None:
        """未知 interval 在客户端层就拦下，不打 API。"""
        md = HyperliquidMarketdata()
        with pytest.raises(ValueError, match="unsupported interval"):
            md.fetch_klines("BTC", "7m", limit=1)


class TestNetworkErrors:
    """场景 4：5xx / 网络错误 → 明确异常，不让 unhandled 透出。"""

    def test_http_500_raises_hyperliquid_marketdata_error(self) -> None:
        http_err = urllib_error.HTTPError(
            url="https://api.hyperliquid.xyz/info",
            code=500,
            msg="Internal Server Error",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )
        md = HyperliquidMarketdata()
        with patch("copy_trader.marketdata.hyperliquid._open_url", side_effect=http_err):
            with pytest.raises(HyperliquidMarketdataError, match="HTTP 500"):
                md.fetch_klines("BTC", "1m", limit=1)

    def test_url_error_raises_hyperliquid_marketdata_error(self) -> None:
        url_err = urllib_error.URLError(reason="Name or service not known")
        md = HyperliquidMarketdata()
        with patch("copy_trader.marketdata.hyperliquid._open_url", side_effect=url_err):
            with pytest.raises(HyperliquidMarketdataError, match="network error"):
                md.fetch_klines("BTC", "1m", limit=1)

    def test_invalid_json_raises_hyperliquid_marketdata_error(self) -> None:
        md = HyperliquidMarketdata()
        with patch("copy_trader.marketdata.hyperliquid._open_url", return_value=b"not json"):
            with pytest.raises(HyperliquidMarketdataError, match="invalid JSON"):
                md.fetch_klines("BTC", "1m", limit=1)

    def test_unexpected_payload_shape_raises(self) -> None:
        # API 返回 {"status": "err", ...} 而不是数组
        md = HyperliquidMarketdata()
        with patch(
            "copy_trader.marketdata.hyperliquid._open_url",
            return_value=json.dumps({"status": "err", "msg": "boom"}).encode("utf-8"),
        ):
            with pytest.raises(HyperliquidMarketdataError, match="unexpected payload type"):
                md.fetch_klines("BTC", "1m", limit=1)


class TestNoCredentials:
    """场景 5：实例化不传 api_key，调用成功。"""

    def test_instantiate_without_credentials(self) -> None:
        # 不传任何参数（无 api_key / api_secret / signature）
        md = HyperliquidMarketdata()
        # name 属性存在并符合 <venue>.<market> 命名规范
        assert md.name == "hyperliquid.spot"
        # 仍可成功 fetch
        rows = [_make_row(open_ts_ms=_SAMPLE_OPEN_TS_MS)]
        with patch(
            "copy_trader.marketdata.hyperliquid._open_url", return_value=_mock_payload(rows)
        ):
            klines = md.fetch_klines("BTC", "1m", limit=1)
        assert len(klines) == 1

    def test_constructor_rejects_api_key_kwarg(self) -> None:
        # `__init__` 不接受 api_key 参数 — acceptance 第 1 条
        with pytest.raises(TypeError):
            HyperliquidMarketdata(api_key="should-not-accept")  # type: ignore[call-arg]


class TestKlineSourceProtocolCompat:
    """额外：HyperliquidMarketdata 结构上兼容 KlineSource Protocol。"""

    def test_isinstance_kline_source(self) -> None:
        md = HyperliquidMarketdata()
        assert isinstance(md, KlineSource)
