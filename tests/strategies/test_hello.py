"""`HelloStrategy` 与 `StrategyRegistry` 行为契约测试。

覆盖 issue #18 acceptance：

1. `hello.step(ctx)` 永远返回 `[]`（多次调用、不同 ctx 都是 0 长度）。
2. 同一 `ctx` 输入序列下行为确定（无随机 / 无副作用 / 无可变状态）。
3. `from copy_trader.strategies import hello`（即 hello 模块通过子包 init
   被注册）后，`get_default("hello")` 能解析得到一个实现 `Strategy` Protocol
   的实例。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from copy_trader.core import Position
from copy_trader.marketdata import Kline
from copy_trader.strategies import (
    DuplicateStrategyError,
    HelloStrategy,
    InvalidStrategyNameError,
    Strategy,
    StrategyContext,
    StrategyRegistry,
    UnknownStrategyError,
    get_default,
    list_default,
)


def _kline(close: str, ts: datetime) -> Kline:
    return Kline(
        open_ts=ts,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
        close_ts=ts,
    )


def _ctx(*, current_price: str = "100", n_klines: int = 3) -> StrategyContext:
    base = datetime(2025, 1, 1, tzinfo=UTC)
    klines = [_kline(str(100 + i), base) for i in range(n_klines)]
    return StrategyContext(
        account="acct-1",
        symbol="BTCUSDT",
        klines=klines,
        position=Position(
            account="acct-1",
            symbol="BTCUSDT",
            qty=Decimal("0"),
            avg_cost=Decimal("0"),
            realized_pnl=Decimal("0"),
            updated_ts=base,
        ),
        current_price=Decimal(current_price),
        ts=base,
    )


# --- (1) hello.step 永远返回 [] -------------------------------------------------


def test_hello_step_returns_empty_list_on_first_call() -> None:
    hello = HelloStrategy()

    out = hello.step(_ctx())

    assert out == []
    assert len(out) == 0


def test_hello_step_returns_empty_list_on_many_calls() -> None:
    hello = HelloStrategy()
    ctx = _ctx()

    for _ in range(20):
        assert hello.step(ctx) == []


@pytest.mark.parametrize(
    "current_price,n_klines",
    [("0.01", 1), ("100", 5), ("99999.99", 100)],
)
def test_hello_step_returns_empty_list_under_varied_ctx(current_price: str, n_klines: int) -> None:
    hello = HelloStrategy()

    out = hello.step(_ctx(current_price=current_price, n_klines=n_klines))

    assert out == []


# --- (2) 可重放：同一 ctx 序列 → 同一输出 -------------------------------------


def test_hello_step_is_deterministic_under_same_ctx_sequence() -> None:
    hello_a = HelloStrategy()
    hello_b = HelloStrategy()
    ctxs = [_ctx(current_price=str(price)) for price in ("100", "101", "102", "103")]

    out_a = [hello_a.step(c) for c in ctxs]
    out_b = [hello_b.step(c) for c in ctxs]

    assert out_a == out_b
    assert all(out == [] for out in out_a)


def test_hello_step_does_not_mutate_ctx() -> None:
    """frozen pydantic model 已经禁止 setattr，但确认 step 不依赖隐式状态。"""
    hello = HelloStrategy()
    ctx = _ctx()

    before_klines = list(ctx.klines)
    hello.step(ctx)
    hello.step(ctx)

    assert list(ctx.klines) == before_klines


# --- (3) registry 集成：导入子包 → get_default("hello") 解析成功 -------------


def test_hello_is_registered_via_subpackage_import() -> None:
    # 通过子包 __init__ 注册；list_default 应包含 "hello"
    assert "hello" in list_default()


def test_get_default_hello_returns_strategy_instance() -> None:
    impl = get_default("hello")

    assert isinstance(impl, Strategy)
    assert impl.name == "hello"


def test_get_default_hello_returns_independent_instances() -> None:
    impl1 = get_default("hello")
    impl2 = get_default("hello")

    # factory 闭包每次调用重建实例（与 ExchangeRegistry 同样语义）
    assert impl1 is not impl2


def test_hello_satisfies_protocol_runtime_checkable() -> None:
    hello = HelloStrategy()

    assert isinstance(hello, Strategy)


# --- 额外：StrategyRegistry 边界场景 ------------------------------------------


def test_registry_unknown_name_raises_with_registered_list() -> None:
    reg = StrategyRegistry()
    reg.register("hello", lambda **_: HelloStrategy())
    reg.register("hello2", lambda **_: HelloStrategy(name="hello2"))

    with pytest.raises(UnknownStrategyError) as exc_info:
        reg.get("hllo")

    err = exc_info.value
    assert err.name == "hllo"
    assert "hello" in err.registered
    assert "hello2" in err.registered


@pytest.mark.parametrize("bad_name", ["", " ", " hello", "hello ", "\thello"])
def test_registry_invalid_name_raises(bad_name: str) -> None:
    reg = StrategyRegistry()

    with pytest.raises(InvalidStrategyNameError):
        reg.register(bad_name, lambda **_: HelloStrategy())


def test_registry_duplicate_registration_raises() -> None:
    reg = StrategyRegistry()
    reg.register("hello", lambda **_: HelloStrategy())

    with pytest.raises(DuplicateStrategyError):
        reg.register("hello", lambda **_: HelloStrategy())


def test_registry_list_returns_sorted_names() -> None:
    reg = StrategyRegistry()
    reg.register("zeta", lambda **_: HelloStrategy(name="zeta"))
    reg.register("alpha", lambda **_: HelloStrategy(name="alpha"))

    assert reg.list() == ["alpha", "zeta"]


def test_registry_factory_receives_kwargs() -> None:
    reg = StrategyRegistry()
    reg.register("hello", lambda **kw: HelloStrategy(**kw))

    impl = reg.get("hello", name="custom-hello")

    assert impl.name == "custom-hello"
