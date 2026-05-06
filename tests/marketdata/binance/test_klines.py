"""`BinanceMarketdata.fetch_klines` 单测：全部 mock，绝不真打 api.binance.com。

测试场景（issue #16 deliverables 第 5 条）：
1. fetch 1m × 5 根 → 解析成 `list[Kline]`，字段类型 / UTC 时区都正确
2. `limit=10` 透传到 query string
3. 多 interval 参数化（1m / 5m / 1h / 1d）都能调用
4. HTTP 5xx → 抛 `BinanceMarketdataError`（不是 unhandled exception）
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

from copy_trader.marketdata import BinanceMarketdata, Kline, KlineSource
from copy_trader.marketdata.binance import BinanceMarketdataError

# Binance K 线 row schema:
# [open_ts_ms, "open", "high", "low", "close", "volume", close_ts_ms,
#  "quote_vol", trades, "taker_buy_base", "taker_buy_quote", "ignore"]
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
    interval_ms: int = _MINUTE_MS,
) -> list[Any]:
    """构造一根 Binance klines 数组（close_ts = open_ts + interval_ms - 1）。"""
    close_ts_ms = open_ts_ms + interval_ms - 1
    return [
        open_ts_ms,
        o,
        h,
        low,
        c,
        v,
        close_ts_ms,
        "90075.0",  # quote vol
        100,  # trades
        "0.75",  # taker buy base
        "45037.5",  # taker buy quote
        "0",  # ignore
    ]


def _mock_payload(rows: list[list[Any]]) -> bytes:
    return json.dumps(rows).encode("utf-8")


class TestFetchKlines:
    """场景 1：fetch_klines 1m × 5，字段 + UTC 校验。"""

    def test_returns_list_of_kline_with_utc_timestamps(self) -> None:
        rows = [_make_row(open_ts_ms=_SAMPLE_OPEN_TS_MS + i * _MINUTE_MS) for i in range(5)]
        md = BinanceMarketdata()
        with patch(
            "copy_trader.marketdata.binance._open_url", return_value=_mock_payload(rows)
        ) as mock_open:
            klines = md.fetch_klines("BTCUSDT", "1m", limit=5)

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
        # UTC 时区 — acceptance 第 2 条
        assert first.open_ts.tzinfo == UTC
        assert first.close_ts.tzinfo == UTC
        # ms epoch 转换正确
        assert first.open_ts == datetime.fromtimestamp(_SAMPLE_OPEN_TS_MS / 1000, tz=UTC)
        # close_ts >= open_ts
        for k in klines:
            assert k.close_ts >= k.open_ts


class TestLimitPropagation:
    """场景 2：limit=10 透传到 query string。"""

    def test_limit_param_in_url(self) -> None:
        rows = [_make_row(open_ts_ms=_SAMPLE_OPEN_TS_MS)]
        md = BinanceMarketdata()
        with patch(
            "copy_trader.marketdata.binance._open_url", return_value=_mock_payload(rows)
        ) as mock_open:
            md.fetch_klines("BTCUSDT", "1m", limit=10)
        called_url = mock_open.call_args[0][0]
        assert "limit=10" in called_url
        assert "symbol=BTCUSDT" in called_url
        assert "interval=1m" in called_url
        assert called_url.startswith("https://api.binance.com/api/v3/klines?")

    def test_default_limit_is_100(self) -> None:
        rows = [_make_row(open_ts_ms=_SAMPLE_OPEN_TS_MS)]
        md = BinanceMarketdata()
        with patch(
            "copy_trader.marketdata.binance._open_url", return_value=_mock_payload(rows)
        ) as mock_open:
            md.fetch_klines("BTCUSDT", "1m")
        called_url = mock_open.call_args[0][0]
        assert "limit=100" in called_url

    def test_limit_zero_or_negative_rejected(self) -> None:
        md = BinanceMarketdata()
        with pytest.raises(ValueError, match="limit must be > 0"):
            md.fetch_klines("BTCUSDT", "1m", limit=0)
        with pytest.raises(ValueError, match="limit must be > 0"):
            md.fetch_klines("BTCUSDT", "1m", limit=-1)


class TestMultipleIntervals:
    """场景 3：多 interval 参数化。"""

    @pytest.mark.parametrize("interval", ["1m", "5m", "1h", "1d"])
    def test_interval_passed_through(self, interval: str) -> None:
        # 不同 interval 对应不同 ms 长度（粗略，仅为构造合法 close_ts）
        ms_per_interval = {
            "1m": 60_000,
            "5m": 5 * 60_000,
            "1h": 60 * 60_000,
            "1d": 24 * 60 * 60_000,
        }[interval]
        rows = [_make_row(open_ts_ms=_SAMPLE_OPEN_TS_MS, interval_ms=ms_per_interval)]
        md = BinanceMarketdata()
        with patch(
            "copy_trader.marketdata.binance._open_url", return_value=_mock_payload(rows)
        ) as mock_open:
            klines = md.fetch_klines("ETHUSDT", interval, limit=1)
        called_url = mock_open.call_args[0][0]
        assert f"interval={interval}" in called_url
        assert len(klines) == 1
        assert klines[0].open_ts.tzinfo == UTC


class TestNetworkErrors:
    """场景 4：5xx / 网络错误 → 明确异常，不让 unhandled 透出。"""

    def test_http_500_raises_binance_marketdata_error(self) -> None:
        http_err = urllib_error.HTTPError(
            url="https://api.binance.com/api/v3/klines",
            code=500,
            msg="Internal Server Error",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )
        md = BinanceMarketdata()
        with patch("copy_trader.marketdata.binance._open_url", side_effect=http_err):
            with pytest.raises(BinanceMarketdataError, match="HTTP 500"):
                md.fetch_klines("BTCUSDT", "1m", limit=1)

    def test_url_error_raises_binance_marketdata_error(self) -> None:
        url_err = urllib_error.URLError(reason="Name or service not known")
        md = BinanceMarketdata()
        with patch("copy_trader.marketdata.binance._open_url", side_effect=url_err):
            with pytest.raises(BinanceMarketdataError, match="network error"):
                md.fetch_klines("BTCUSDT", "1m", limit=1)

    def test_invalid_json_raises_binance_marketdata_error(self) -> None:
        md = BinanceMarketdata()
        with patch("copy_trader.marketdata.binance._open_url", return_value=b"not json"):
            with pytest.raises(BinanceMarketdataError, match="invalid JSON"):
                md.fetch_klines("BTCUSDT", "1m", limit=1)

    def test_unexpected_payload_shape_raises(self) -> None:
        # API 返回 {"code": -1, "msg": "..."} 而不是数组
        md = BinanceMarketdata()
        with patch(
            "copy_trader.marketdata.binance._open_url",
            return_value=json.dumps({"code": -1, "msg": "boom"}).encode("utf-8"),
        ):
            with pytest.raises(BinanceMarketdataError, match="unexpected payload type"):
                md.fetch_klines("BTCUSDT", "1m", limit=1)


class TestNoCredentials:
    """场景 5：实例化不传 api_key，调用成功。"""

    def test_instantiate_without_credentials(self) -> None:
        # 不传任何参数（无 api_key / api_secret / signature）
        md = BinanceMarketdata()
        # name 属性存在并符合命名规范
        assert md.name == "binance.spot"
        # 仍可成功 fetch
        rows = [_make_row(open_ts_ms=_SAMPLE_OPEN_TS_MS)]
        with patch("copy_trader.marketdata.binance._open_url", return_value=_mock_payload(rows)):
            klines = md.fetch_klines("BTCUSDT", "1m", limit=1)
        assert len(klines) == 1

    def test_constructor_rejects_api_key_kwarg(self) -> None:
        # `__init__` 不接受 api_key 参数 — acceptance 第 1 条
        with pytest.raises(TypeError):
            BinanceMarketdata(api_key="should-not-accept")  # type: ignore[call-arg]


class TestKlineSourceProtocolCompat:
    """额外：BinanceMarketdata 结构上兼容 KlineSource Protocol。"""

    def test_isinstance_kline_source(self) -> None:
        md = BinanceMarketdata()
        assert isinstance(md, KlineSource)
