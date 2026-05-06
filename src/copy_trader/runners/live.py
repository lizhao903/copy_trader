"""Live runner facade（issue #19）。

把 strategies / exchanges / marketdata / persistence 装配成一个主循环，
按 ``mode={live,paper,dry-run}`` 决定下单路径：

- ``live``：直接调用注入的 ``Exchange.place_order``，把成交 fill 写 ledger。
- ``paper``：把注入的 live exchange 包进 ``PaperExchange``，使用注入的
  ``KlineSource`` 给市价 fill（滑点 + 费率），fill 仅 env_tag 不同。
- ``dry-run``：策略产生 ``OrderRequest`` 但**不**触达任何 exchange 与 ledger。
  用于 CI/手工 smoke 验证管线。

cli 子命令 ``copy-trader run`` 是本模块的薄壳；按 ``cli-only-runners-config``
契约，cli 不能 import strategies/exchanges/marketdata，所以本模块负责装配。

注意：本模块不直接 ``resolve_runtime`` —— RuntimeContext 由调用方注入，
保持与 ``reconcile`` facade 一致的风格，方便测试 + 多账户。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, cast

from copy_trader.config import Settings
from copy_trader.core import Order, OrderRequest, Position
from copy_trader.exchanges import Exchange
from copy_trader.exchanges.paper import PaperExchange
from copy_trader.marketdata import KlineSource
from copy_trader.persistence import CrossEnvironmentWriteError, TradesRepo
from copy_trader.strategies import (
    Strategy,
    StrategyContext,
    UnknownStrategyError,
)
from copy_trader.strategies import (
    get_default as get_default_strategy,
)

__all__ = [
    "LiveRunResult",
    "LiveRunner",
    "Mode",
    "default_marketdata_factory",
    "run_live",
]

Mode = Literal["live", "paper", "dry-run"]


def default_marketdata_factory(venue: str) -> KlineSource:
    """按 venue 名构造默认 marketdata 实例。

    cli 不能 import marketdata 子包（``cli-only-runners-config`` 契约），
    通过本工厂触达。venue 名做 ``_→.`` 归一化兼容 ``binance_spot`` 形态。
    """
    normalized = venue.replace("_", ".")
    head = normalized.split(".", 1)[0]
    if head == "binance":
        from copy_trader.marketdata import BinanceMarketdata

        return BinanceMarketdata()
    if head == "hyperliquid":
        from copy_trader.marketdata import HyperliquidMarketdata

        return HyperliquidMarketdata()
    raise KeyError(f"unknown marketdata venue: {venue!r} (头部 {head!r} 未注册)")


@dataclass
class LiveRunResult:
    mode: Mode
    iterations: int = 0
    orders_proposed: int = 0
    orders_executed: int = 0
    fills_written: int = 0
    errors: list[str] = field(default_factory=list)


class LiveRunner:
    """主循环：拉行情 → 调策略 → 下单 → 写 ledger（dry-run 跳过下单 + ledger）。

    构造参数显式注入,便于测试。``run()`` 阻塞跑 ``max_iterations`` 轮（None 表无限）。
    """

    def __init__(
        self,
        *,
        account: str,
        strategy: Strategy,
        mode: Mode,
        symbols: list[str],
        ledger: TradesRepo,
        exchange: Exchange,
        marketdata: KlineSource,
        paper_slippage_bps: int = 10,
        paper_fee_bps: int = 10,
        kline_interval: str = "1m",
        kline_limit: int = 100,
        max_iterations: int | None = None,
        tick_seconds: float = 60.0,
        on_order: Callable[[OrderRequest, Order | None], None] | None = None,
        sleep_func: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if mode not in ("live", "paper", "dry-run"):
            raise ValueError(f"invalid mode: {mode!r}")
        self.account = account
        self.strategy = strategy
        self.mode: Mode = mode
        self.symbols = list(symbols)
        self.ledger = ledger
        self.live_exchange = exchange
        self.marketdata = marketdata
        self.kline_interval = kline_interval
        self.kline_limit = kline_limit
        self.max_iterations = max_iterations
        self.tick_seconds = tick_seconds
        self.on_order = on_order
        self._sleep = sleep_func
        self._clock = clock

        if mode == "paper":
            # PaperExchange 内部用本地 _KlineSourceLike Protocol（与 KlineSource
            # 结构兼容，但 list[Kline] 不是 list[_KlineLike] 的不变 generic 子类
            # — Liskov OK，mypy 严格不接受协变）。cast Any 绕过；运行期 duck-type 兼容。
            self.exec_exchange: Exchange = PaperExchange(
                wraps=exchange,
                marketdata=cast(Any, marketdata),
                slippage_bps=paper_slippage_bps,
                fee_bps=paper_fee_bps,
            )
        else:
            self.exec_exchange = exchange

    def run(self) -> LiveRunResult:
        result = LiveRunResult(mode=self.mode)
        i = 0
        while self.max_iterations is None or i < self.max_iterations:
            for symbol in self.symbols:
                self._tick(symbol, result)
            i += 1
            result.iterations = i
            if self.max_iterations is not None and i >= self.max_iterations:
                break
            if self.tick_seconds > 0:
                self._sleep(self.tick_seconds)
        return result

    def _tick(self, symbol: str, result: LiveRunResult) -> None:
        try:
            klines = self.marketdata.fetch_klines(symbol, self.kline_interval, self.kline_limit)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"[{symbol}] fetch_klines failed: {exc}")
            return
        if not klines:
            result.errors.append(f"[{symbol}] empty klines")
            return
        current_price = klines[-1].close
        position = self._fetch_position(symbol, result)
        ctx = StrategyContext(
            account=self.account,
            symbol=symbol,
            klines=list(klines),
            position=position,
            current_price=current_price,
            ts=self._clock(),
        )
        try:
            orders = self.strategy.step(ctx)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"[{symbol}] strategy.step failed: {exc}")
            return
        result.orders_proposed += len(orders)
        for req in orders:
            self._handle_order(req, result)

    def _fetch_position(self, symbol: str, result: LiveRunResult) -> Position:
        if self.mode == "dry-run":
            return self._zero_position(symbol)
        try:
            return self.exec_exchange.fetch_position(symbol)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"[{symbol}] fetch_position failed: {exc}")
            return self._zero_position(symbol)

    def _zero_position(self, symbol: str) -> Position:
        return Position(
            account=self.account,
            symbol=symbol,
            qty=Decimal("0"),
            avg_cost=Decimal("0"),
            realized_pnl=Decimal("0"),
            updated_ts=self._clock(),
        )

    def _handle_order(self, req: OrderRequest, result: LiveRunResult) -> None:
        if self.mode == "dry-run":
            if self.on_order is not None:
                self.on_order(req, None)
            return
        try:
            order = self.exec_exchange.place_order(req)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"place_order failed: {exc}")
            return
        result.orders_executed += 1
        # paper 把 fills 累加在 PaperExchange 内部；live 走 fetch_fills 拉
        try:
            fills = self.exec_exchange.fetch_fills(req.symbol)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"fetch_fills failed: {exc}")
            fills = []
        for fill in fills:
            try:
                self.ledger.insert(fill)
                result.fills_written += 1
            except CrossEnvironmentWriteError as exc:
                result.errors.append(f"ledger insert blocked: {exc}")
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"ledger insert failed: {exc}")
        if self.on_order is not None:
            self.on_order(req, order)


def run_live(
    *,
    account: str,
    strategy_name: str,
    mode: Mode,
    settings: Settings,
    ledger: TradesRepo,
    exchange: Exchange,
    marketdata: KlineSource,
    paper_slippage_bps: int = 10,
    paper_fee_bps: int = 10,
    max_iterations: int | None = None,
    tick_seconds: float = 60.0,
    on_order: Callable[[OrderRequest, Order | None], None] | None = None,
    sleep_func: Callable[[float], None] = time.sleep,
) -> LiveRunResult:
    """Live runner 入口（cli `run` 子命令薄壳）。"""
    if account not in settings.accounts:
        sorted_avail = sorted(settings.accounts.keys())
        raise KeyError(f"account {account!r} 不在配置里；可选账户: {sorted_avail}")
    account_cfg = settings.accounts[account]
    try:
        strategy = get_default_strategy(strategy_name)
    except UnknownStrategyError:
        raise
    runner = LiveRunner(
        account=account,
        strategy=strategy,
        mode=mode,
        symbols=list(account_cfg.symbols),
        ledger=ledger,
        exchange=exchange,
        marketdata=marketdata,
        paper_slippage_bps=paper_slippage_bps,
        paper_fee_bps=paper_fee_bps,
        max_iterations=max_iterations,
        tick_seconds=tick_seconds,
        on_order=on_order,
        sleep_func=sleep_func,
    )
    return runner.run()
