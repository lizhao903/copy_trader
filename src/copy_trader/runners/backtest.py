"""BacktestRunner facade（issue #24）。

回测 runner 与 LiveRunner 共用 strategy / exchange / pnl 栈,但驱动来源是
**历史 K 线**(从 `marketdata.cache.KlineCache` 读)而非实时 fetch。

设计:
- 用 `PaperExchange` 包一个 `Exchange` Protocol stub (无网络) 模拟成交
- 历史 K 线通过 `iter_history` 一根根喂给 strategy.step(ctx)
- ledger 写入 env_tag='backtest' 区分; runner_id 默认 'legacy' 或调用方传入
- 统计 PnlEngine 从 ledger fills 重建,跟 live 完全相同(spec acceptance 第一条)

cli `copy-trader backtest --strategy --symbol --start --end` 是本模块薄壳。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from copy_trader.core import Fill, Order, OrderRequest, Position, SymbolInfo
from copy_trader.exchanges.base import Exchange
from copy_trader.exchanges.paper import PaperExchange
from copy_trader.marketdata import Kline
from copy_trader.persistence import CrossEnvironmentWriteError, TradesRepo
from copy_trader.strategies import Strategy, StrategyContext

__all__ = [
    "BacktestRunResult",
    "BacktestRunner",
    "run_backtest",
]


# 回测复用 PaperExchange 的 fill 结构, env_tag='paper' (spec 容忍 paper / backtest
# 共用同一标签;调用方若需区分可在 TradesRepo 实例化时改 env_tag, 或下游分析时
# 按 runner_id 区分)。


@dataclass
class BacktestRunResult:
    iterations: int = 0
    orders_proposed: int = 0
    orders_executed: int = 0
    fills_written: int = 0
    errors: list[str] = field(default_factory=list)


class _StubLiveExchange:
    """回测时不接实盘;PaperExchange 需要一个 wraps 提供 SymbolInfo / round_*。

    不实装 place_order/cancel/fetch_*(回测里 PaperExchange 不调它们)。
    """

    name = "stub.spot"

    def __init__(self, symbol_info: SymbolInfo) -> None:
        self._info = symbol_info

    def get_balance(self, asset: str) -> Decimal:
        return Decimal("0")

    def fetch_position(self, symbol: str) -> Position:
        return Position(
            account="backtest",
            symbol=symbol,
            qty=Decimal("0"),
            avg_cost=Decimal("0"),
            realized_pnl=Decimal("0"),
            updated_ts=datetime.now(UTC),
        )

    def place_order(self, req: OrderRequest) -> Order:
        raise NotImplementedError("stub exchange should not place orders directly")

    def cancel(self, order_id: str) -> None:
        raise NotImplementedError("stub")

    def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        return []

    def fetch_fills(self, symbol: str, since: datetime | None = None) -> list[Fill]:
        return []

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        return self._info

    def round_price(self, symbol: str, price: Decimal) -> Decimal:
        return price.quantize(self._info.tick_size)

    def round_qty(self, symbol: str, qty: Decimal) -> Decimal:
        return qty.quantize(self._info.step_size)


class _ReplayMarketdata:
    """marketdata stub: 每次 fetch_klines 返回历史窗口的最近 limit 根。

    state: 当前 cursor (历史 K 线索引), 每跑一轮往前推一根。
    """

    name = "backtest.replay"

    def __init__(self, all_klines: list[Kline], window: int = 100) -> None:
        self._all = list(all_klines)
        self._window = window
        self._cursor = 0

    @property
    def has_more(self) -> bool:
        return self._cursor < len(self._all)

    def advance(self) -> None:
        self._cursor += 1

    def fetch_klines(self, symbol: str, interval: str, limit: int = 100) -> list[Kline]:
        if self._cursor == 0:
            return []
        end = self._cursor
        start = max(0, end - limit)
        return self._all[start:end]


class BacktestRunner:
    """回测主循环: 把历史 K 线一根根喂给 strategy, 用 PaperExchange 模拟成交。"""

    def __init__(
        self,
        *,
        account: str,
        strategy: Strategy,
        symbol: str,
        klines: list[Kline],
        ledger: TradesRepo,
        symbol_info: SymbolInfo | None = None,
        slippage_bps: int = 10,
        fee_bps: int = 10,
        runner_id: str = "legacy",
    ) -> None:
        self.account = account
        self.strategy = strategy
        self.symbol = symbol
        self.klines = list(klines)
        self.ledger = ledger
        self.runner_id = runner_id

        info = symbol_info or SymbolInfo(
            venue="backtest",
            symbol=symbol,
            base_asset=symbol[:-4] or "BTC",
            quote_asset=symbol[-4:] or "USDT",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.00001"),
            min_notional=Decimal("10"),
        )

        self._stub_live: Exchange = _StubLiveExchange(info)
        self._marketdata = _ReplayMarketdata(self.klines)
        self._paper = PaperExchange(
            wraps=self._stub_live,
            marketdata=cast(Any, self._marketdata),
            slippage_bps=slippage_bps,
            fee_bps=fee_bps,
            machine_id="backtest",
            schema_version=3,
        )

    def run(self) -> BacktestRunResult:
        result = BacktestRunResult()
        for current in self.klines:
            self._marketdata.advance()
            history = self._marketdata.fetch_klines(self.symbol, "1m", 100)
            if not history:
                continue
            position = self._paper.fetch_position(self.symbol)
            ctx = StrategyContext(
                account=self.account,
                symbol=self.symbol,
                klines=history,
                position=position,
                current_price=current.close,
                ts=current.close_ts,
            )
            try:
                orders = self.strategy.step(ctx)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"strategy.step failed: {exc}")
                continue
            result.orders_proposed += len(orders)
            for req in orders:
                try:
                    self._paper.place_order(req)
                    result.orders_executed += 1
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"place_order failed: {exc}")
                    continue
            # 把本轮新 fills 写 ledger
            fills = self._paper.fetch_fills(self.symbol)
            for fill in fills[result.fills_written :]:
                # 给 fill 附 runner_id (PaperExchange 默认 'legacy')
                stamped = fill.model_copy(update={"runner_id": self.runner_id})
                try:
                    self.ledger.insert(stamped)
                    result.fills_written += 1
                except CrossEnvironmentWriteError as exc:
                    result.errors.append(f"ledger blocked: {exc}")
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"ledger insert failed: {exc}")
            result.iterations += 1
        return result


def run_backtest(
    *,
    account: str,
    strategy: Strategy,
    symbol: str,
    klines: list[Kline],
    ledger: TradesRepo,
    symbol_info: SymbolInfo | None = None,
    slippage_bps: int = 10,
    fee_bps: int = 10,
    runner_id: str = "legacy",
) -> BacktestRunResult:
    """回测 facade。从 marketdata.cache 读历史 K 线驱动策略。"""
    runner = BacktestRunner(
        account=account,
        strategy=strategy,
        symbol=symbol,
        klines=klines,
        ledger=ledger,
        symbol_info=symbol_info,
        slippage_bps=slippage_bps,
        fee_bps=fee_bps,
        runner_id=runner_id,
    )
    return runner.run()
