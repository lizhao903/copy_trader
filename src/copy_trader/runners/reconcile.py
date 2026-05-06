"""Reconcile runner facade（issue #13）。

CLI 子命令 ``copy-trader reconcile --account <name>`` 的薄壳层。

为什么要在 ``runners`` 而不是直接在 ``cli`` 里调 execution / persistence /
exchanges？因为 import-linter ``cli-only-runners-config`` 契约严格限制 cli
顶层只能 import ``copy_trader.runners`` 与 ``copy_trader.config``。本模块
组装 ``Settings`` / ``TradesRepo`` / ``Exchange`` / ``ReconcileService``，把
端到端动作收敛成 ``run_reconcile(...)`` 一个函数，并定义结构化返回值
``ReconcileRunResult``，让 CLI 层只负责打印与 exit code 映射。

注意：本模块**不**直接调用 ``resolve_runtime`` —— RuntimeContext / 配置目录
都由调用方注入，方便测试同时也让 CLI 层能在 doctor 风格的降级场景下复用。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from copy_trader.config import Settings
from copy_trader.exchanges import Exchange, get_default
from copy_trader.execution import (
    ReconcileReport,
    ReconcileService,
)
from copy_trader.persistence import TradesRepo

__all__ = [
    "AccountNotFoundError",
    "ReconcileRunResult",
    "build_ledger",
    "default_exchange_factory",
    "run_reconcile",
]


def build_ledger(*, home: Path, env_tag: str, machine_id: str) -> TradesRepo:
    """构造 :class:`TradesRepo`，绑定 ``$home/db/ledger.db``。

    cli 层依靠本工厂避免直接 import ``copy_trader.persistence``（被
    ``cli-only-runners-config`` 契约禁止）。``runtime-isolation`` spec 把
    ledger 路径定为 ``$COPY_TRADER_HOME/db/``；文件名 ``ledger.db`` 与
    pnl-single-source spec 中的 ``db/trades.db`` 等价（spec 没硬绑文件名，
    本项目内统一用 ``ledger.db`` 以避免和 ledger import 名冲突）。
    """
    db_dir = home / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    return TradesRepo(db_dir / "ledger.db", env_tag=env_tag, machine_id=machine_id)


class AccountNotFoundError(ValueError):
    """``--account <name>`` 在 settings.accounts 里找不到。"""

    def __init__(self, account: str, available: list[str]) -> None:
        sorted_avail = sorted(available)
        super().__init__(
            f"account {account!r} 不在配置里；可选账户: {sorted_avail}",
        )
        self.account = account
        self.available = sorted_avail


@dataclass(frozen=True, slots=True)
class ReconcileRunResult:
    """``run_reconcile`` 的返回值。

    Attributes:
        report: ``ReconcileReport``；当 ledger 完全无 fills 且无 exchange
            余额时仍会返回（events 为空）。
        empty_ledger: ledger 中该账户的所有 symbol 都没有 fills。
            在该路径下，CLI 应直接打印「已对齐」并退出 0，无需展开 events。
        applied: 是否真正写 cache（``--apply`` 默认 True；False 时
            ``cache_dir=None`` 跳过 cache 维护，仅打印差异）。
    """

    report: ReconcileReport
    empty_ledger: bool
    applied: bool


def run_reconcile(
    *,
    account: str,
    settings: Settings,
    ledger: TradesRepo,
    exchange: Exchange,
    cache_dir: Path | None,
    logs_dir: Path | None,
    apply: bool = True,
    acknowledge_unknown: bool = False,
) -> ReconcileRunResult:
    """跑 reconcile 并返回结构化结果。

    Args:
        account: 配置里的账户名，必须存在于 ``settings.accounts``。
        settings: 已加载的 :class:`Settings`，提供 ``account.symbols``。
        ledger: 已实例化的 :class:`TradesRepo`（含正确 env_tag/machine_id）。
        exchange: 实现 :class:`Exchange` Protocol 的实例。
        cache_dir: state 缓存目录；``None`` 时跳过 cache 维护（dry-run 模式）。
        logs_dir: 日志目录；用于 ``logs/reconcile_<ts>.log``。
        apply: ``False`` 强制 ``cache_dir=None``，对应 ``--apply=False``
            的 dry-run 语义（issue body）：只 print 不改 cache。
        acknowledge_unknown: 透传到 ``ReconcileService.reconcile``，对应
            ``--acknowledge-unknown`` flag。

    Returns:
        :class:`ReconcileRunResult`。

    Raises:
        AccountNotFoundError: ``account`` 不在配置中。
    """
    if account not in settings.accounts:
        raise AccountNotFoundError(account, list(settings.accounts.keys()))
    symbols = list(settings.accounts[account].symbols)

    # ledger 是否完全为空（任一 symbol 有 fills 即视为非空）。
    empty = all(not ledger.fetch(account=account, symbol=s) for s in symbols)

    effective_cache_dir = cache_dir if apply else None
    service = ReconcileService(
        ledger=ledger,
        exchange=exchange,
        cache_dir=effective_cache_dir,
        logs_dir=logs_dir,
    )
    report = service.reconcile(
        account=account,
        symbols=symbols,
        acknowledge_unknown=acknowledge_unknown,
    )
    return ReconcileRunResult(report=report, empty_ledger=empty, applied=apply)


# 显式 re-export ``get_default``：cli 层若想自定义工厂可以直接 import 这个；
# 但默认走下面的 ``default_exchange_factory`` 工厂便于 CLI 层 monkeypatch。
ExchangeFactory = Callable[[str], Exchange]


def default_exchange_factory(venue: str) -> Exchange:
    """默认工厂：通过 ``ExchangeRegistry`` 解析 venue 名。

    venue 名形如 ``binance.spot``；issue #4 的 ``AccountConfig.venue`` 当前
    使用了 ``binance_spot`` 这种下划线形态（M0 配置遗留），这里做一次
    ``_`` → ``.`` 的归一化使其能命中 registry 注册名。如果两段都不命中，
    交给 registry 抛 ``UnknownExchangeError``，CLI 层捕获展示。
    """
    if venue in (None, ""):
        raise ValueError("venue 不能为空")
    candidates = [venue]
    if "_" in venue and "." not in venue:
        candidates.append(venue.replace("_", ".", 1))
    last_err: Exception | None = None
    for name in candidates:
        try:
            return get_default(name)
        except KeyError as exc:  # UnknownExchangeError 派生自 KeyError
            last_err = exc
            continue
    # 把最后一次错误重新抛出，保留 registered 列表
    assert last_err is not None
    raise last_err
