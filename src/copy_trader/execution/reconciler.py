"""ReconcileService：启动期三级 diff（pnl-single-source spec 实装，issue #11）。

按 `pnl-single-source` spec 第三条 Requirement「Startup reconcile compares
ledger, exchange, and cache」：

- 拉取交易所余额 / 当前持仓
- 从 ledger 重建预期持仓（走 `PnlEngine`，单一事实来源）
- 与 cache 三方比对，分级处理：

  1. ``cache_drift``：cache 与 ledger 重建结果不一致
     → 自动以 ledger 为准覆盖 cache 文件，记 ``cache_overridden`` 事件，继续启动
  2. ``ledger_exchange_mismatch``：ledger 重建 qty 与交易所 ``fetch_position``
     的 qty 差距超过容差 → 进入 SAFE 模式（``safe_mode=True``） + 告警事件，
     不阻塞启动（runner 上层据此切换 "仅平仓不开仓"）
  3. ``unknown_position_on_exchange``：交易所有非空 base 余额，但 ledger 中
     该 ``(account, symbol)`` 完全没有 fills → 拒绝启动
     （``fatal=True``），错误消息提示 ``--acknowledge-unknown``；
     调用方传 ``acknowledge_unknown=True`` 时降级为 acknowledged 事件、放行

差异详情写到 ``logs_dir/reconcile_<ts>.log``（若 ``logs_dir`` 为 None 则不写
日志，仅返回 ``ReconcileReport``）。本模块**不** import ``copy_trader.config``
（execution-bounded contract 禁止），运行时根目录解析的 ``logs`` 路径必须
由 runner / cli 层显式注入。

边界（``.importlinter`` ``execution-bounded``）：
本模块仅依赖 ``copy_trader.{core,persistence,exchanges,pnl}`` + stdlib + pydantic。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from copy_trader.core import Position
from copy_trader.exchanges import Exchange
from copy_trader.persistence import TradesRepo
from copy_trader.pnl import PnlEngine

__all__ = [
    "DEFAULT_QTY_TOLERANCE",
    "ReconcileError",
    "ReconcileEvent",
    "ReconcileReport",
    "ReconcileService",
    "UnknownPositionError",
]

#: ledger qty 与 exchange qty 默认容差（0.00000001 = 1 sat）。
#: 选 1e-8 是因为大多数交易所 lot_size 不会比 8 位小数更细；
#: ledger 全程用 ``Decimal``，exchange Protocol 也返回 ``Decimal``，
#: 因此这里只用来吸收四舍五入误差，不是真正的"风控容差"。
DEFAULT_QTY_TOLERANCE: Decimal = Decimal("0.00000001")

#: 事件级别（用于日志过滤 / runner 决策）。
EventLevel = Literal["info", "warning", "error"]

#: 事件分类（spec 三级 diff 的精确名）。
EventKind = Literal[
    "cache_overridden",
    "cache_created",
    "cache_ok",
    "ledger_exchange_mismatch",
    "unknown_position_on_exchange",
    "unknown_position_acknowledged",
]


_CACHE_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_cache_filename(account: str, symbol: str) -> str:
    """``<account>_<symbol>.json``，对非 ``[A-Za-z0-9_.-]`` 字符做替换防注入。

    spec 没规定 cache 文件命名，这里采用与日志/状态文件一致的 ``<account>_<symbol>``
    形式。account / symbol 来自上层配置或交易所 metadata，理论上是干净的，但
    为防御 path traversal 仍统一替换，避免 cache_dir 被穿越到外部。
    """
    safe_acc = _CACHE_FILENAME_SAFE.sub("_", account)
    safe_sym = _CACHE_FILENAME_SAFE.sub("_", symbol)
    return f"{safe_acc}_{safe_sym}.json"


@dataclass(frozen=True, slots=True)
class ReconcileEvent:
    """单条 reconcile 差异/事件，结构化便于上层告警 / 日志过滤。

    ``details`` 字段存事件相关的数值（qty / avg_cost / 文件路径等）；
    所有 ``Decimal`` 在序列化前会被转 ``str`` 以避免精度丢失。
    """

    kind: EventKind
    level: EventLevel
    account: str
    symbol: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """`json.dumps` 友好的字典；``Decimal`` → ``str``。"""
        out: dict[str, Any] = {
            "kind": self.kind,
            "level": self.level,
            "account": self.account,
            "symbol": self.symbol,
            "message": self.message,
            "details": _jsonable(self.details),
        }
        return out


def _jsonable(value: Any) -> Any:
    """递归把 ``Decimal`` / ``datetime`` 转成 JSON 安全形式。"""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """`reconcile()` 返回值；包含全部事件 + 关键决策位。

    Attributes:
        account: 被 reconcile 的账户。
        symbols: 被 reconcile 的 symbol 列表（与入参一致）。
        events: 所有差异事件，按发生顺序。
        safe_mode: ``ledger_exchange_mismatch`` 触发时为 ``True``。
        fatal: 检测到 ``unknown_position_on_exchange`` 且未 acknowledge
            时为 ``True``，调用方应当退出进程。
        log_path: 若写了 ``logs/reconcile_<ts>.log``，则为绝对路径；否则 ``None``。
    """

    account: str
    symbols: tuple[str, ...]
    events: tuple[ReconcileEvent, ...]
    safe_mode: bool
    fatal: bool
    log_path: Path | None

    @property
    def fatal_message(self) -> str | None:
        """汇总所有 ``unknown_position_on_exchange`` 错误事件的人类可读消息。"""
        msgs = [
            ev.message
            for ev in self.events
            if ev.kind == "unknown_position_on_exchange" and ev.level == "error"
        ]
        if not msgs:
            return None
        hint = " 加 --acknowledge-unknown 显式确认后重新启动。"
        return "\n".join(msgs) + hint


class ReconcileError(Exception):
    """`ReconcileService` 主流程未捕获错误的基类。

    设计上 reconcile 流程**不**抛 ``UnknownPositionError`` 异常 — 未知仓位
    的拒绝是通过 ``ReconcileReport.fatal=True`` 表达的，以便 CLI 可以
    打印完整事件列表后再退出，而不是被一次异常截断。
    """


class UnknownPositionError(ReconcileError):
    """供调用方在 ``report.fatal`` 时抛出的便捷异常。

    例：
        report = service.reconcile(...)
        if report.fatal:
            raise UnknownPositionError(report.fatal_message or "unknown position")
    """


class ReconcileService:
    """启动期三级 diff 服务。

    构造参数：

    - ``ledger``: ``TradesRepo``，用于按 ``(account, symbol)`` 拉 fills。
    - ``exchange``: 实现 ``Exchange`` Protocol 的适配器；
      ``fetch_position(symbol)`` / ``get_balance(asset)`` 是必需。
    - ``cache_dir``: 持仓缓存目录（``state/`` 下）。``None`` 时跳过 cache 读写
      （仅做 ledger ↔ exchange 比对，仍能产出报告）。
    - ``qty_tolerance``: ledger qty 与 exchange qty 的允许差。

    线程安全：单实例不可并发 ``reconcile()``；上层应当串行调用。
    """

    def __init__(
        self,
        ledger: TradesRepo,
        exchange: Exchange,
        cache_dir: Path | None = None,
        *,
        logs_dir: Path | None = None,
        qty_tolerance: Decimal = DEFAULT_QTY_TOLERANCE,
    ) -> None:
        self._ledger = ledger
        self._exchange = exchange
        self._cache_dir = cache_dir
        self._logs_dir = logs_dir
        self._qty_tolerance = qty_tolerance

    # --- public --------------------------------------------------------

    def reconcile(
        self,
        account: str,
        symbols: list[str],
        *,
        acknowledge_unknown: bool = False,
    ) -> ReconcileReport:
        """对 ``account`` 上每个 symbol 跑三级 diff，汇总成 ``ReconcileReport``。

        Args:
            account: 账户名（与 ledger 行 ``account`` 字段对齐）。
            symbols: 要 reconcile 的 symbol 列表（如 ``["BTCUSDT", "ETHUSDT"]``）。
                未在此列表里的交易所余额会触发 ``unknown_position_on_exchange``。
            acknowledge_unknown: ``True`` 时把 ``unknown_position_on_exchange``
                事件降级为 ``unknown_position_acknowledged``（``info``）+ 不
                设置 ``fatal``；典型用法是人工确认后用 ``--acknowledge-unknown``
                flag 重启。

        Returns:
            ``ReconcileReport``。``fatal`` 表示调用方必须退出；``safe_mode``
            表示调用方应只允许平仓。
        """
        events: list[ReconcileEvent] = []
        safe_mode = False
        fatal = False

        for symbol in symbols:
            ledger_position = self._rebuild_position_from_ledger(account, symbol)

            # 1. cache_drift 比对（cache 缺失也在这里处理）。
            cache_event = self._reconcile_cache(account, symbol, ledger_position)
            if cache_event is not None:
                events.append(cache_event)

            # 2. ledger ↔ exchange 比对。
            mismatch_event = self._reconcile_ledger_vs_exchange(account, symbol, ledger_position)
            if mismatch_event is not None:
                events.append(mismatch_event)
                safe_mode = True

        # 3. unknown_position_on_exchange：交易所有 ledger 完全不知道的资产余额。
        unknown_events, unknown_fatal = self._detect_unknown_positions(
            account=account,
            known_symbols=symbols,
            acknowledge_unknown=acknowledge_unknown,
        )
        events.extend(unknown_events)
        if unknown_fatal:
            fatal = True

        log_path = self._write_log(account=account, events=events)

        return ReconcileReport(
            account=account,
            symbols=tuple(symbols),
            events=tuple(events),
            safe_mode=safe_mode,
            fatal=fatal,
            log_path=log_path,
        )

    # --- step 1: ledger reconstruction --------------------------------

    def _rebuild_position_from_ledger(self, account: str, symbol: str) -> Position:
        """从 ledger 拉 fills 走 ``PnlEngine`` 重建持仓快照。

        ledger 没有 fills 时仍返回 ``Position(qty=0, avg_cost=0)``——这是有效
        的"已平仓 / 从未持仓"快照。
        """
        fills = self._ledger.fetch(account=account, symbol=symbol, since=None)
        engine = PnlEngine(account=account, symbol=symbol, fills=fills, mode="weighted")
        return engine.position()

    # --- step 2: cache vs ledger -------------------------------------

    def _reconcile_cache(
        self,
        account: str,
        symbol: str,
        ledger_position: Position,
    ) -> ReconcileEvent | None:
        """比对 cache 文件与 ledger 重建结果；不一致或缺失时覆盖写入。

        Returns:
            事件对象。``None`` 仅在 ``cache_dir is None`` 时返回（跳过
            cache 维护）。
        """
        if self._cache_dir is None:
            return None

        cache_path = self._cache_dir / _safe_cache_filename(account, symbol)
        ledger_payload = _position_to_cache_payload(ledger_position)

        if not cache_path.exists():
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            _write_cache(cache_path, ledger_payload)
            return ReconcileEvent(
                kind="cache_created",
                level="info",
                account=account,
                symbol=symbol,
                message=f"cache 缺失，按 ledger 重建写入 {cache_path}",
                details={
                    "cache_path": str(cache_path),
                    "ledger_qty": ledger_position.qty,
                    "ledger_avg_cost": ledger_position.avg_cost,
                },
            )

        cache_payload = _read_cache(cache_path)
        if cache_payload == ledger_payload:
            return ReconcileEvent(
                kind="cache_ok",
                level="info",
                account=account,
                symbol=symbol,
                message="cache 与 ledger 重建结果一致",
                details={"cache_path": str(cache_path)},
            )

        # cache_drift：人工编辑 / 旧版本残留 / peak_price 漂移等都走这条路径。
        _write_cache(cache_path, ledger_payload)
        return ReconcileEvent(
            kind="cache_overridden",
            level="warning",
            account=account,
            symbol=symbol,
            message=f"cache_drift 检测到，已用 ledger 重建结果覆盖 {cache_path}",
            details={
                "cache_path": str(cache_path),
                "before": cache_payload,
                "after": ledger_payload,
            },
        )

    # --- step 2: ledger vs exchange -----------------------------------

    def _reconcile_ledger_vs_exchange(
        self,
        account: str,
        symbol: str,
        ledger_position: Position,
    ) -> ReconcileEvent | None:
        """对单 symbol 比对 ledger qty 与 ``exchange.fetch_position(symbol).qty``。"""
        exchange_position = self._exchange.fetch_position(symbol)
        ledger_qty = ledger_position.qty
        exchange_qty = exchange_position.qty
        diff = abs(ledger_qty - exchange_qty)
        if diff <= self._qty_tolerance:
            return None
        return ReconcileEvent(
            kind="ledger_exchange_mismatch",
            level="warning",
            account=account,
            symbol=symbol,
            message=(
                f"ledger 重建 qty={ledger_qty} 与交易所 qty={exchange_qty} 差距 "
                f"{diff} > 容差 {self._qty_tolerance}；进入 SAFE 模式"
            ),
            details={
                "ledger_qty": ledger_qty,
                "exchange_qty": exchange_qty,
                "diff": diff,
                "tolerance": self._qty_tolerance,
            },
        )

    # --- step 3: unknown position on exchange -------------------------

    def _detect_unknown_positions(
        self,
        *,
        account: str,
        known_symbols: list[str],
        acknowledge_unknown: bool,
    ) -> tuple[list[ReconcileEvent], bool]:
        """扫描 ``known_symbols`` 推导出的 base 资产余额，发现 ledger 不知道的报告。

        ``known_symbols`` 形如 ``BTCUSDT``，base 资产取前若干字母（去掉常见
        quote 后缀 ``USDT/USDC/BUSD/USD``）；找不到 quote 时退化成把整段
        symbol 当 base（保守，避免漏报）。这是 spec 表述的"交易所余额含有
        BTC"的最小推断。
        """
        events: list[ReconcileEvent] = []
        fatal = False

        # 收集 known_symbols 推断出的 base 资产集合，避免对已知资产误报。
        known_bases = {_infer_base_asset(s) for s in known_symbols}

        # 对 ledger 没有 fills 的 known_symbols 也要检查：交易所有 base 余额
        # 但 ledger 完全没记录 → unknown。
        for symbol in known_symbols:
            base = _infer_base_asset(symbol)
            try:
                balance = self._exchange.get_balance(base)
            except Exception as exc:  # 交易所 API 失败 → 当 warning，不阻塞启动
                events.append(
                    ReconcileEvent(
                        kind="ledger_exchange_mismatch",
                        level="warning",
                        account=account,
                        symbol=symbol,
                        message=f"无法读取 {base} 余额：{exc}",
                        details={"base_asset": base, "error": str(exc)},
                    )
                )
                continue

            if balance <= self._qty_tolerance:
                continue

            # ledger 该账户 + symbol 是否完全没 fills？
            fills = self._ledger.fetch(account=account, symbol=symbol, since=None)
            if fills:
                # 有 fills 但余额仍不为 0 / 不一致 → 由 ledger_exchange_mismatch
                # 处理（已在 step 2 走过），这里跳过避免重复。
                continue

            if acknowledge_unknown:
                events.append(
                    ReconcileEvent(
                        kind="unknown_position_acknowledged",
                        level="info",
                        account=account,
                        symbol=symbol,
                        message=(
                            f"交易所有 {base} 余额={balance}，ledger 无 fills；"
                            "已通过 --acknowledge-unknown 放行"
                        ),
                        details={
                            "base_asset": base,
                            "exchange_balance": balance,
                        },
                    )
                )
            else:
                events.append(
                    ReconcileEvent(
                        kind="unknown_position_on_exchange",
                        level="error",
                        account=account,
                        symbol=symbol,
                        message=(
                            f"unknown_position_on_exchange: 账户 {account!r} 在交易所有 "
                            f"{base} 余额={balance}，但 ledger 中该 symbol "
                            f"{symbol!r} 完全没有 fills，拒绝启动"
                        ),
                        details={
                            "base_asset": base,
                            "exchange_balance": balance,
                        },
                    )
                )
                fatal = True

        # 备注：spec 也允许"交易所有 known_bases 之外的 base 余额"算 unknown，
        # 但 Protocol 没有 list_balances() 接口（issue #14 不暴露），无法穷举；
        # 在 issue #14 扩展前，本服务只检查 known_symbols 的 base 余额。
        # known_bases 暂时保留以便未来扩展。
        del known_bases

        return events, fatal

    # --- log writing --------------------------------------------------

    def _write_log(
        self,
        *,
        account: str,
        events: list[ReconcileEvent],
    ) -> Path | None:
        """把所有事件 dump 到 ``logs_dir/reconcile_<ts>.log``，每行一个 JSON。

        ``logs_dir is None`` 或事件为空时跳过写入。文件名带 UTC 时间戳，
        多次 reconcile 不会互相覆盖。
        """
        if self._logs_dir is None or not events:
            return None
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        log_path = self._logs_dir / f"reconcile_{ts}.log"
        lines = [
            json.dumps(
                {"account": account, **ev.to_dict()},
                ensure_ascii=False,
                sort_keys=True,
            )
            for ev in events
        ]
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # 同时把要点丢给 logging（runner 上层会接到 root logger）。
        logger = logging.getLogger(__name__)
        for ev in events:
            logger.log(_LEVEL_MAP[ev.level], "%s | %s", ev.kind, ev.message)
        return log_path


# --- helpers ------------------------------------------------------------


def _position_to_cache_payload(pos: Position) -> dict[str, Any]:
    """``Position`` → cache JSON 字典；金额 ``Decimal`` 用 ``str`` 序列化。"""
    return {
        "account": pos.account,
        "symbol": pos.symbol,
        "qty": str(pos.qty),
        "avg_cost": str(pos.avg_cost),
        "realized_pnl": str(pos.realized_pnl),
        "updated_ts": pos.updated_ts.isoformat(),
    }


def _read_cache(path: Path) -> dict[str, Any]:
    """读取 cache JSON；损坏时返回空字典（一定会触发 cache_drift 覆盖）。"""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    """原子写 cache JSON：先写临时文件再 rename，避免半写状态被下次启动读到。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


# 常见 USDⓈ-margined 现货 / 永续 quote。本项目当前只跑这几个 venue，
# 不写完美的交易对解析器（那是 marketdata 层的职责），先满足 reconcile 需要。
_KNOWN_QUOTES: tuple[str, ...] = ("USDT", "USDC", "BUSD", "FDUSD", "DAI", "USD")


def _infer_base_asset(symbol: str) -> str:
    """从 ``BTCUSDT`` / ``ETH-USDT`` / ``BTC/USDT`` 抽出 base 资产。

    简单规则：剥离 ``-`` / ``/`` / ``:`` 等分隔符后，命中 ``_KNOWN_QUOTES``
    后缀就去掉；否则把原 symbol 视作 base。这对 reconcile 已够用，未来要
    跑非 USD 币本位时再扩展。
    """
    cleaned = symbol.replace("-", "").replace("/", "").replace(":", "").upper()
    for quote in _KNOWN_QUOTES:
        if cleaned.endswith(quote) and len(cleaned) > len(quote):
            return cleaned[: -len(quote)]
    return cleaned


_LEVEL_MAP: dict[EventLevel, int] = {
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}
