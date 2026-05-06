"""KlineCache + CachingKlineSource 测试 (issue #23)。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from copy_trader.marketdata import Kline
from copy_trader.marketdata.cache import CachingKlineSource, KlineCache


def _make_kline(open_ts: datetime, close: Decimal = Decimal("50000")) -> Kline:
    return Kline(
        open_ts=open_ts,
        open=close,
        high=close + Decimal("100"),
        low=close - Decimal("100"),
        close=close,
        volume=Decimal("10"),
        close_ts=open_ts + timedelta(minutes=1),
    )


@pytest.fixture
def cache(tmp_path: Any) -> KlineCache:
    return KlineCache(tmp_path / "klines.db")


def test_schema_idempotent(tmp_path: Any) -> None:
    """重复打开同一 db, schema 不变 / version=1 / 不报错。"""
    db = tmp_path / "klines.db"
    c1 = KlineCache(db)
    assert c1.schema_version == 1
    c1.close()
    c2 = KlineCache(db)
    assert c2.schema_version == 1


def test_write_and_fetch_roundtrip(cache: KlineCache) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    klines = [_make_kline(base + timedelta(minutes=i)) for i in range(5)]
    n = cache.write("binance.spot", "BTCUSDT", "1m", klines)
    assert n == 5
    out = cache.fetch("binance.spot", "BTCUSDT", "1m")
    assert len(out) == 5
    assert out[0].open == Decimal("50000")
    assert out[0].open_ts == base


def test_fetch_with_limit(cache: KlineCache) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    klines = [_make_kline(base + timedelta(minutes=i)) for i in range(10)]
    cache.write("binance.spot", "BTCUSDT", "1m", klines)
    out = cache.fetch("binance.spot", "BTCUSDT", "1m", limit=3)
    assert len(out) == 3
    # limit 取最新 N 根, 升序返回 → 最后三根
    assert out[0].open_ts == base + timedelta(minutes=7)
    assert out[2].open_ts == base + timedelta(minutes=9)


def test_fetch_with_since(cache: KlineCache) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    klines = [_make_kline(base + timedelta(minutes=i)) for i in range(5)]
    cache.write("binance.spot", "BTCUSDT", "1m", klines)
    out = cache.fetch(
        "binance.spot",
        "BTCUSDT",
        "1m",
        since=base + timedelta(minutes=3),
    )
    assert len(out) == 2  # i=3, 4
    assert out[0].open_ts == base + timedelta(minutes=3)


def test_write_replace_on_pk_collision(cache: KlineCache) -> None:
    """同 (venue, symbol, interval, open_ts) 重复写, 后写覆盖前写。"""
    open_ts = datetime(2026, 1, 1, tzinfo=UTC)
    cache.write("binance.spot", "BTCUSDT", "1m", [_make_kline(open_ts, Decimal("50000"))])
    cache.write("binance.spot", "BTCUSDT", "1m", [_make_kline(open_ts, Decimal("60000"))])
    out = cache.fetch("binance.spot", "BTCUSDT", "1m")
    assert len(out) == 1
    assert out[0].close == Decimal("60000")


def test_count(cache: KlineCache) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cache.write(
        "binance.spot",
        "BTCUSDT",
        "1m",
        [_make_kline(base + timedelta(minutes=i)) for i in range(7)],
    )
    assert cache.count("binance.spot", "BTCUSDT", "1m") == 7
    # 不同 symbol/interval 不互相干扰
    assert cache.count("binance.spot", "ETHUSDT", "1m") == 0
    assert cache.count("binance.spot", "BTCUSDT", "5m") == 0


# ---------- CachingKlineSource 装饰器 ----------


class _RecordingKlineSource:
    name = "test.venue"

    def __init__(self, klines: list[Kline]) -> None:
        self._klines = klines
        self.fetch_calls: list[tuple[str, str, int]] = []

    def fetch_klines(self, symbol: str, interval: str, limit: int = 100) -> list[Kline]:
        self.fetch_calls.append((symbol, interval, limit))
        return self._klines


def test_caching_source_miss_then_hit(cache: KlineCache) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    klines = [_make_kline(base + timedelta(minutes=i)) for i in range(5)]
    upstream = _RecordingKlineSource(klines)
    src = CachingKlineSource(wraps=upstream, cache=cache)

    # 第一次 miss → 调 upstream, 写缓存
    out1 = src.fetch_klines("BTCUSDT", "1m", limit=5)
    assert len(out1) == 5
    assert len(upstream.fetch_calls) == 1

    # 第二次同 symbol/interval/limit → hit, 不调 upstream
    out2 = src.fetch_klines("BTCUSDT", "1m", limit=5)
    assert len(out2) == 5
    assert len(upstream.fetch_calls) == 1  # 仍 1 次


def test_caching_source_partial_hit_falls_through(cache: KlineCache) -> None:
    """缓存有 3 根但需要 5 根 → 应当 fallthrough 到 upstream。"""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cache.write(
        "test.venue",
        "BTCUSDT",
        "1m",
        [_make_kline(base + timedelta(minutes=i)) for i in range(3)],
    )
    # upstream 提供 5 根
    upstream_klines = [_make_kline(base + timedelta(minutes=i)) for i in range(5)]
    upstream = _RecordingKlineSource(upstream_klines)
    src = CachingKlineSource(wraps=upstream, cache=cache)

    out = src.fetch_klines("BTCUSDT", "1m", limit=5)
    assert len(out) == 5
    assert len(upstream.fetch_calls) == 1


def test_caching_source_name_passthrough(cache: KlineCache) -> None:
    upstream = _RecordingKlineSource([])
    src = CachingKlineSource(wraps=upstream, cache=cache)
    assert src.name == "test.venue"
