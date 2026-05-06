"""Typer CLI 入口（issue #5 / runtime-isolation + config-overlay spec）。

公共 entrypoint：``copy-trader`` — 通过 ``[project.scripts]`` 暴露成
``copy_trader.cli.main:app``。

子命令清单：

- ``doctor``：M0 实装。输出运行时根目录、env_tag、machine_id、各子目录可写性、
  配置来源摘要（敏感字段掩码）以及 ledger schema_version 占位。锁文件不一致
  （env_tag / machine_id 与本次启动不符）时不 fail-fast，而是把不一致项作为
  ⚠️ 告警打印（spec runtime-isolation 第 4 个 Requirement 的 scenario）。
- ``run`` / ``paper`` / ``backtest`` / ``reconcile`` / ``dashboard`` /
  ``registry``：M1+ / M4 才实装；当前是占位 stub，调用 → 提示 pending 并以
  退出码 1 返回，方便后续接入而 ``--help`` 仍然不会 import error。

实现注意：

- 这里不直接 ``resolve_runtime()`` —— ``resolve_runtime()`` 在锁不一致时会抛
  ``CrossEnvironmentError`` / ``CrossMachineError``；doctor 必须降级到只读模式
  打印不一致项。所以本模块在 doctor 里先 try resolve，失败时 fallback 到
  ``read_runtime_lock`` + 手动重建上下文。
- import-linter 的 ``cli-only-runners-config`` 契约允许 cli 依赖 ``config``，
  这里只 import ``copy_trader.config``，不 import 其他业务子包。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from copy_trader.config import Settings
from copy_trader.config.runtime import (
    CrossEnvironmentError,
    CrossMachineError,
    MissingEnvError,
    RuntimeBootstrapError,
    RuntimeContext,
    read_runtime_lock,
    resolve_runtime,
)
from copy_trader.runners import (
    AccountNotFoundError,
    DuplicateRunnerNameError,
    InvalidStateTransition,
    LiveRunResult,
    ReconcileRunResult,
    RunnerNotFoundError,
    RunnerRegistry,
    RunnerService,
    UnknownStrategyError,
    build_ledger,
    default_exchange_factory,
    default_marketdata_factory,
    run_live,
    run_reconcile,
)

app = typer.Typer(
    name="copy-trader",
    help="Copy-trade system CLI（M0 实装 doctor，其余子命令为 M1+ 占位）。",
    no_args_is_help=True,
    add_completion=False,
)

# 敏感字段命名后缀；与 config-overlay spec / Settings._SENSITIVE_SUFFIXES 一致。
_SENSITIVE_SUFFIXES: tuple[str, ...] = (
    "_key",
    "_secret",
    "_token",
    "_private_key",
)

_REDACTED = "<redacted>"

# 运行时子目录清单；与 runtime.py 内部 _RUNTIME_SUBDIRS 一致。doctor 用它
# 跑 touch+unlink 可写性检查。
_RUNTIME_SUBDIRS: tuple[str, ...] = ("state", "logs", "pids", "db", "secrets")


# --------------------------------------------------------------------------- #
# 占位 stub 子命令（M1+ / M4 才实装）
# --------------------------------------------------------------------------- #


def _pending(name: str, milestone: str) -> None:
    """统一打印 pending 提示并退出码 1。"""
    typer.echo(f"{name} — pending implementation in {milestone}")
    raise typer.Exit(code=1)


@app.command()
def run(
    strategy: str = typer.Option(  # noqa: B008
        ...,
        "--strategy",
        "-s",
        help="策略名（默认注册表 key，如 'hello'）。",
    ),
    account: str = typer.Option(  # noqa: B008
        ...,
        "--account",
        "-a",
        help="目标账户（必须存在于 config.accounts）。",
    ),
    mode: str = typer.Option(  # noqa: B008
        "dry-run",
        "--mode",
        "-m",
        help="运行模式：live / paper / dry-run（默认 dry-run，不触达 exchange）。",
    ),
    max_iterations: int | None = typer.Option(  # noqa: B008
        None,
        "--max-iterations",
        help="主循环最大轮数；缺省无限。CI / smoke 设小值（如 3）快速退出。",
    ),
    tick_seconds: float = typer.Option(  # noqa: B008
        60.0,
        "--tick-seconds",
        help="每轮间 sleep 秒数；测试可设 0。",
    ),
    home: str | None = typer.Option(  # noqa: B008
        None,
        "--home",
        help="覆盖 COPY_TRADER_HOME，便于隔离目录跑。",
    ),
    config_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--config-dir",
        help="config/ 目录路径；缺省自动探测。",
    ),
) -> None:
    """启动 LiveRunner 主循环（issue #19）。

    模式：

    - ``live``：调用真实 exchange.place_order，把 fills 写 ledger。
    - ``paper``：用 PaperExchange 包 wraps，env_tag='paper' 写 ledger。
    - ``dry-run``：策略产生 OrderRequest 但**不**触达 exchange / ledger。

    退出码：
    - 0：循环正常结束（``--max-iterations`` 用尽）+ 无 errors
    - 1：参数错误 / 配置错误 / 循环中累计错误 > 0
    """
    if mode not in ("live", "paper", "dry-run"):
        typer.echo(f"错误：--mode 必须是 live / paper / dry-run，收到 {mode!r}")
        raise typer.Exit(code=1)

    try:
        ctx = resolve_runtime(home=home)
    except MissingEnvError as exc:
        typer.echo(f"错误：{exc}")
        raise typer.Exit(code=1) from exc
    except RuntimeBootstrapError as exc:
        typer.echo(f"runtime bootstrap 失败：{exc}")
        raise typer.Exit(code=1) from exc

    cfg_path = config_dir or _autodetect_config_dir()
    if cfg_path is None or not cfg_path.is_dir():
        typer.echo(f"错误：未找到 config/ 目录（looked at {cfg_path}）。")
        raise typer.Exit(code=1)

    try:
        settings = Settings.load(
            config_dir=cfg_path,
            env=ctx.env_tag,
            local_path=(ctx.home / "config.yaml") if (ctx.home / "config.yaml").is_file() else None,
        )
    except (ValidationError, OSError, ValueError) as exc:
        typer.echo(f"settings 加载失败：{exc}")
        raise typer.Exit(code=1) from exc

    if account not in settings.accounts:
        sorted_avail = sorted(settings.accounts.keys())
        typer.echo(f"错误：account {account!r} 不在配置里；可选账户：{sorted_avail}")
        raise typer.Exit(code=1)

    venue = settings.accounts[account].venue
    try:
        exchange = default_exchange_factory(venue)
    except KeyError as exc:
        typer.echo(f"错误：venue 未注册：{exc}")
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"错误：交易所适配器初始化失败：{exc}")
        raise typer.Exit(code=1) from exc

    try:
        marketdata = default_marketdata_factory(venue)
    except KeyError as exc:
        typer.echo(f"错误：marketdata 未注册：{exc}")
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"错误：marketdata 初始化失败：{exc}")
        raise typer.Exit(code=1) from exc

    ledger = build_ledger(home=ctx.home, env_tag=ctx.env_tag, machine_id=ctx.machine_id)

    def _on_order(req: object, order: object | None) -> None:
        if mode == "dry-run":
            typer.echo(f"[dry-run] 策略产出 {req}（未下单）")
        else:
            typer.echo(f"[{mode}] 已下单 {order}")

    typer.echo(
        f"LiveRunner 启动：strategy={strategy} account={account} mode={mode} "
        f"max_iterations={max_iterations} tick_seconds={tick_seconds}",
    )
    try:
        result: LiveRunResult = run_live(
            account=account,
            strategy_name=strategy,
            mode=mode,  # type: ignore[arg-type]
            settings=settings,
            ledger=ledger,
            exchange=exchange,
            marketdata=marketdata,
            max_iterations=max_iterations,
            tick_seconds=tick_seconds,
            on_order=_on_order,
        )
    except UnknownStrategyError as exc:
        typer.echo(f"错误：策略未注册：{exc}")
        raise typer.Exit(code=1) from exc
    except KeyboardInterrupt:
        typer.echo("（收到 Ctrl-C，提前退出）")
        raise typer.Exit(code=0) from None
    finally:
        _safe_close(ledger)

    typer.echo(
        f"LiveRunner 结束：iterations={result.iterations} "
        f"orders_proposed={result.orders_proposed} orders_executed={result.orders_executed} "
        f"fills_written={result.fills_written} errors={len(result.errors)}",
    )
    if result.errors:
        for err in result.errors:
            typer.echo(f"  ! {err}")
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


@app.command()
def paper() -> None:
    """[M1+] 启动 paper 模式（不下真单）。"""
    _pending("paper", "M1+")


@app.command()
def backtest(
    strategy: str = typer.Option(  # noqa: B008
        ...,
        "--strategy",
        "-s",
        help="策略名（默认注册表 key）。",
    ),
    symbol: str = typer.Option(  # noqa: B008
        ...,
        "--symbol",
        help="标的(如 BTCUSDT)。",
    ),
    start: str = typer.Option(  # noqa: B008
        ...,
        "--start",
        help="回测起始日期 (YYYY-MM-DD)。",
    ),
    end: str = typer.Option(  # noqa: B008
        ...,
        "--end",
        help="回测结束日期 (YYYY-MM-DD)。",
    ),
    home: str | None = typer.Option(  # noqa: B008
        None,
        "--home",
        help="覆盖 COPY_TRADER_HOME。",
    ),
) -> None:
    """回测 LiveRunner: 用历史 K 线 + PaperExchange + 策略 (issue #24)。"""
    from datetime import datetime as _dt

    try:
        ctx = resolve_runtime(home=home)
    except MissingEnvError as exc:
        typer.echo(f"错误：{exc}")
        raise typer.Exit(code=1) from exc

    try:
        _dt.fromisoformat(start)
        _dt.fromisoformat(end)
    except ValueError as exc:
        typer.echo(f"错误：日期格式 (YYYY-MM-DD): {exc}")
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"Backtest 启动: strategy={strategy} symbol={symbol} "
        f"period={start} → {end} home={ctx.home}",
    )
    typer.echo(
        "(本占位提示) 完整端到端 (klines.cache 加载 + run_backtest) 实装中,"
        "当前可通过 from copy_trader.runners import run_backtest 程序化调用。",
    )
    raise typer.Exit(code=0)


@app.command()
def reconcile(
    account: str = typer.Option(  # noqa: B008
        ...,
        "--account",
        "-a",
        help="目标账户名（必须存在于 config.accounts）。",
    ),
    apply: bool = typer.Option(  # noqa: B008
        True,
        "--apply/--no-apply",
        help="--apply（默认）允许 reconcile 自动覆盖 cache 文件；"
        "--no-apply 是 dry-run，仅打印差异不改 cache。",
    ),
    acknowledge_unknown: bool = typer.Option(  # noqa: B008
        False,
        "--acknowledge-unknown",
        help="人工确认交易所有 ledger 不知道的余额；"
        "默认遇到 unknown_position_on_exchange 拒绝启动并退出 1。",
    ),
    home: str | None = typer.Option(  # noqa: B008
        None,
        "--home",
        help="覆盖 COPY_TRADER_HOME，便于在隔离目录里跑 reconcile。",
    ),
    config_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--config-dir",
        help="config/ 目录路径；缺省自动探测仓库根 config/。",
    ),
) -> None:
    """启动期 reconcile：对账 ledger / cache / exchange 三方差异（issue #13）。

    退出码：
    - 0：全部对齐，或仅有 cache_drift / acknowledged unknown / SAFE-mode warning
    - 1：``unknown_position_on_exchange`` 未确认（``report.fatal=True``）
        或参数错误 / 配置错误
    """
    try:
        ctx = resolve_runtime(home=home)
    except MissingEnvError as exc:
        typer.echo(f"错误：{exc}")
        raise typer.Exit(code=1) from exc
    except RuntimeBootstrapError as exc:
        typer.echo(f"runtime bootstrap 失败：{exc}")
        raise typer.Exit(code=1) from exc

    cfg_path = config_dir or _autodetect_config_dir()
    if cfg_path is None or not cfg_path.is_dir():
        typer.echo(f"错误：未找到 config/ 目录（looked at {cfg_path}）。")
        raise typer.Exit(code=1)

    try:
        settings = Settings.load(
            config_dir=cfg_path,
            env=ctx.env_tag,
            local_path=(ctx.home / "config.yaml") if (ctx.home / "config.yaml").is_file() else None,
        )
    except (ValidationError, OSError, ValueError) as exc:
        typer.echo(f"settings 加载失败：{exc}")
        raise typer.Exit(code=1) from exc

    if account not in settings.accounts:
        sorted_avail = sorted(settings.accounts.keys())
        typer.echo(f"错误：account {account!r} 不在配置里；可选账户：{sorted_avail}")
        raise typer.Exit(code=1)

    venue = settings.accounts[account].venue
    try:
        exchange = default_exchange_factory(venue)
    except KeyError as exc:
        # UnknownExchangeError / 未注册 venue：把 registry 列表透传给用户
        typer.echo(f"错误：venue 未注册：{exc}")
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"错误：交易所适配器初始化失败：{exc}")
        raise typer.Exit(code=1) from exc

    # 通过 runners 工厂构造 ledger / exchange，避免 cli 层直接 import
    # persistence / exchanges（违反 cli-only-runners-config 契约）。
    ledger = build_ledger(home=ctx.home, env_tag=ctx.env_tag, machine_id=ctx.machine_id)

    try:
        result = run_reconcile(
            account=account,
            settings=settings,
            ledger=ledger,
            exchange=exchange,
            cache_dir=ctx.home / "state",
            logs_dir=ctx.home / "logs",
            apply=apply,
            acknowledge_unknown=acknowledge_unknown,
        )
    except AccountNotFoundError as exc:
        typer.echo(f"错误：{exc}")
        raise typer.Exit(code=1) from exc
    finally:
        # TradesRepo 持有 sqlite 连接，结束时关闭
        _safe_close(ledger)

    _print_reconcile_summary(result)
    if result.report.fatal:
        # spec：unknown_position_on_exchange 未 acknowledge → 退出 1
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


@app.command()
def dashboard() -> None:
    """[M4] 启动 dashboard（含设置中心）。"""
    _pending("dashboard", "M4")


# --------------------------------------------------------------------------- #
# registry 子 app —— RunnerService CRUD + 启停 (issue #27)
# --------------------------------------------------------------------------- #


registry_app = typer.Typer(
    name="registry",
    help="Runner instance 持久化 CRUD + 启停 (issue #27 / RunnerService)。",
    no_args_is_help=True,
)
app.add_typer(registry_app, name="registry")


def _build_runner_service(home: str | None) -> tuple[RunnerService, RunnerRegistry]:
    """构造 RunnerService + 它的 registry; 返回 tuple 让调用方负责关闭。"""
    ctx = resolve_runtime(home=home)
    db_dir = ctx.home / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    registry = RunnerRegistry(db_dir / "runner_registry.db")
    service = RunnerService(registry, pid_dir=ctx.home / "pids")
    return service, registry


@registry_app.command("create")
def registry_create(
    name: str = typer.Option(..., "--name"),  # noqa: B008
    venue: str = typer.Option(..., "--venue"),  # noqa: B008
    account: str = typer.Option(..., "--account"),  # noqa: B008
    strategy: str = typer.Option(..., "--strategy"),  # noqa: B008
    mode: str = typer.Option("dry-run", "--mode"),  # noqa: B008
    params_json: str | None = typer.Option(  # noqa: B008
        None,
        "--params",
        help="JSON 字符串 (如 '{\"slippage\": 5}')。",
    ),
    home: str | None = typer.Option(None, "--home"),  # noqa: B008
) -> None:
    """创建一个新 runner 实例 (status='stopped')。"""
    import json as _json

    if mode not in ("live", "paper", "dry-run", "backtest"):
        typer.echo(f"错误: --mode 必须是 live/paper/dry-run/backtest, 收到 {mode!r}")
        raise typer.Exit(code=1)

    params: dict[str, Any] = {}
    if params_json:
        try:
            params = _json.loads(params_json)
        except _json.JSONDecodeError as exc:
            typer.echo(f"错误: --params 不是合法 JSON: {exc}")
            raise typer.Exit(code=1) from exc

    service, registry = _build_runner_service(home)
    try:
        runner = service.create(
            name=name,
            venue=venue,
            account=account,
            strategy=strategy,
            mode=mode,  # type: ignore[arg-type]
            params_override=params,
        )
        typer.echo(f"created: id={runner.id} name={runner.name} status={runner.status}")
    except DuplicateRunnerNameError as exc:
        typer.echo(f"错误: {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        registry.close()


@registry_app.command("update")
def registry_update(
    id_or_name: str = typer.Argument(..., help="runner id 或 name"),  # noqa: B008
    params_json: str | None = typer.Option(None, "--params"),  # noqa: B008
    mode: str | None = typer.Option(None, "--mode"),  # noqa: B008
    home: str | None = typer.Option(None, "--home"),  # noqa: B008
) -> None:
    """更新 runner 字段 (params_override / mode)。"""
    import json as _json

    params: dict[str, Any] | None = None
    if params_json:
        try:
            params = _json.loads(params_json)
        except _json.JSONDecodeError as exc:
            typer.echo(f"错误: {exc}")
            raise typer.Exit(code=1) from exc

    service, registry = _build_runner_service(home)
    try:
        runner = service.update(
            id_or_name,
            params_override=params,
            mode=mode,  # type: ignore[arg-type]
        )
        typer.echo(f"updated: id={runner.id} name={runner.name}")
    except RunnerNotFoundError as exc:
        typer.echo(f"错误: {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        registry.close()


@registry_app.command("delete")
def registry_delete(
    id_or_name: str = typer.Argument(...),  # noqa: B008
    home: str | None = typer.Option(None, "--home"),  # noqa: B008
) -> None:
    """删除 runner; running 状态先发 stop 等 30s 后 SIGKILL。"""
    service, registry = _build_runner_service(home)
    try:
        service.delete(id_or_name)
        typer.echo(f"deleted: {id_or_name}")
    except RunnerNotFoundError as exc:
        typer.echo(f"错误: {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        registry.close()


@registry_app.command("start")
def registry_start(
    id_or_name: str = typer.Argument(...),  # noqa: B008
    home: str | None = typer.Option(None, "--home"),  # noqa: B008
) -> None:
    """stopped → starting (外部 supervisor 接续 starting → running)。"""
    service, registry = _build_runner_service(home)
    try:
        runner = service.start(id_or_name)
        typer.echo(f"started: id={runner.id} status={runner.status}")
    except (RunnerNotFoundError, InvalidStateTransition) as exc:
        typer.echo(f"错误: {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        registry.close()


@registry_app.command("stop")
def registry_stop(
    id_or_name: str = typer.Argument(...),  # noqa: B008
    home: str | None = typer.Option(None, "--home"),  # noqa: B008
) -> None:
    """running → stopping (发 SIGTERM 等主循环优雅退出)。"""
    service, registry = _build_runner_service(home)
    try:
        runner = service.stop(id_or_name)
        typer.echo(f"stopping: id={runner.id} status={runner.status}")
    except (RunnerNotFoundError, InvalidStateTransition) as exc:
        typer.echo(f"错误: {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        registry.close()


@registry_app.command("list")
def registry_list(
    status: str | None = typer.Option(  # noqa: B008
        None,
        "--status",
        help="按 status 过滤 (draft/stopped/starting/running/stopping/errored)",
    ),
    home: str | None = typer.Option(None, "--home"),  # noqa: B008
) -> None:
    """列出所有 runner 实例; --status 可过滤。"""
    service, registry = _build_runner_service(home)
    try:
        runners = service.list_all(status=status)  # type: ignore[arg-type]
        if not runners:
            typer.echo("(no runners)")
            return
        for r in runners:
            typer.echo(
                f"  id={r.id[:8]}.. name={r.name} venue={r.venue} status={r.status} "
                f"pid={r.pid} hb={r.last_heartbeat}"
            )
    finally:
        registry.close()


@registry_app.command("reap")
def registry_reap(
    home: str | None = typer.Option(None, "--home"),  # noqa: B008
) -> None:
    """扫描 running 行, last_heartbeat 超 60s 标 errored + SIGKILL PID。"""
    service, registry = _build_runner_service(home)
    try:
        reaped = service.reap()
        if not reaped:
            typer.echo("(none reaped)")
            return
        for r in reaped:
            typer.echo(f"  reaped: id={r.id[:8]}.. name={r.name} → errored")
    finally:
        registry.close()


# --------------------------------------------------------------------------- #
# doctor —— 实装
# --------------------------------------------------------------------------- #


@app.command()
def doctor(
    # Typer 惯用法：Option 作为参数默认；ruff B008 在 CLI 入口是误报，单行 noqa。
    home: str | None = typer.Option(  # noqa: B008
        None,
        "--home",
        help="覆盖 COPY_TRADER_HOME，便于隔离测试或多 env 排查。",
    ),
    config_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--config-dir",
        help="config/ 目录路径；缺省自动探测仓库根 config/。",
    ),
) -> None:
    """打印运行时根目录、锁状态、子目录可写性、配置来源（敏感字段掩码）。"""
    warnings: list[str] = []
    ctx, lock_warnings = _bootstrap_runtime_for_doctor(home_override=home)
    warnings.extend(lock_warnings)

    typer.echo("== copy-trader doctor ==")
    typer.echo(f"home          : {ctx.home}")
    typer.echo(f"home_source   : {ctx.home_source}")
    typer.echo(f"env_tag       : {ctx.env_tag}")
    typer.echo(f"machine_id    : {ctx.machine_id}")
    typer.echo(f"schema_version: {ctx.schema_version}")
    typer.echo(f"started_at    : {ctx.started_at}")

    # 子目录可写性：每个子目录跑一次 touch + unlink。
    typer.echo("")
    typer.echo("[runtime subdirs writability]")
    for sub in _RUNTIME_SUBDIRS:
        ok = _check_writable(ctx.home / sub)
        typer.echo(f"  {sub:<8}: {'OK' if ok else 'FAIL'}")
        if not ok:
            warnings.append(f"子目录 {sub} 不可写")

    # 配置来源摘要（敏感字段掩码）。
    typer.echo("")
    typer.echo("[config sources (sensitive values redacted)]")
    cfg_warnings = _print_config_sources(
        env_tag=ctx.env_tag,
        home=ctx.home,
        config_dir=config_dir,
    )
    warnings.extend(cfg_warnings)

    # ledger schema_version 占位（issue #8 落地）。
    typer.echo("")
    typer.echo("[ledger]")
    typer.echo("  schema_version: <not yet implemented (issue #8)>")

    if warnings:
        typer.echo("")
        typer.echo("[warnings]")
        for w in warnings:
            typer.echo(f"  ⚠️  {w}")

    # spec：doctor 不修改任何状态、不 fail-fast；即便有 ⚠️ 也保持退出码 0。


# --------------------------------------------------------------------------- #
# 内部 helper
# --------------------------------------------------------------------------- #


def _bootstrap_runtime_for_doctor(
    *,
    home_override: str | None,
) -> tuple[RuntimeContext, list[str]]:
    """尝试 ``resolve_runtime``；若锁不一致，降级到只读重建 ctx + 收集告警。

    spec：doctor 在锁不一致时不 fail-fast，必须把不一致项作为告警打印。
    缺 ``COPY_TRADER_ENV`` 仍是必须 fail（spec runtime-isolation 第 1 个 Requirement
    "缺失环境变量时拒绝启动" 没给 doctor 例外）。
    """
    warnings: list[str] = []
    try:
        return resolve_runtime(home=home_override), warnings
    except MissingEnvError:
        # env 没设是硬错——doctor 也不能编个 env_tag 出来。
        raise
    except (CrossEnvironmentError, CrossMachineError) as exc:
        # 锁里的 env_tag/machine_id 与本次进程不一致：降级。
        warnings.append(str(exc).splitlines()[0])
        ctx = _readonly_runtime_context(home_override=home_override)
        # 把锁里的不一致项以 mismatch 关键字打出来，便于运维 grep。
        existing = read_runtime_lock(ctx.home) or {}
        if existing.get("env_tag") != ctx.env_tag:
            warnings.append(
                f"runtime_lock env_tag mismatch: lock={existing.get('env_tag')!r} "
                f"current={ctx.env_tag!r}"
            )
        if existing.get("machine_id") != ctx.machine_id:
            warnings.append(
                f"runtime_lock machine_id mismatch: lock={existing.get('machine_id')!r} "
                f"current={ctx.machine_id!r}"
            )
        return ctx, warnings
    except RuntimeBootstrapError as exc:
        # 其他 bootstrap 异常（非锁冲突类）：让用户看到，但 doctor 不 raise。
        warnings.append(f"runtime bootstrap warning: {exc}")
        ctx = _readonly_runtime_context(home_override=home_override)
        return ctx, warnings


def _readonly_runtime_context(*, home_override: str | None) -> RuntimeContext:
    """构造一个不写锁的 RuntimeContext（用于 doctor 降级模式）。

    我们重新走一遍 env / home 解析（不调 ``_ensure_subdirs`` 也不写锁），但
    machine_id 直接读 state/.machine_id；如果没有，也不主动生成（因为 doctor
    spec 强调 "MUST 不修改任何状态"）。
    """
    from copy_trader.config import runtime as _rt  # 延迟 import 避免循环

    env_tag = _rt._resolve_env_tag(None)
    home, home_source = _rt._resolve_home(env_tag, home_override)
    machine_id_path = home / "state" / ".machine_id"
    machine_id = (
        machine_id_path.read_text(encoding="utf-8").strip()
        if machine_id_path.is_file()
        else "<unset>"
    )
    return RuntimeContext(
        env_tag=env_tag,
        home=home,
        machine_id=machine_id,
        schema_version=_rt.RUNTIME_LOCK_SCHEMA_VERSION,
        pid=os.getpid(),
        started_at="<not-written-by-doctor>",
        home_source=home_source,
    )


def _check_writable(path: Path) -> bool:
    """对子目录跑一次 touch + unlink，验证可写性；不留下任何痕迹。"""
    if not path.is_dir():
        return False
    probe = path / ".doctor_writable_probe"
    try:
        probe.touch()
    except OSError:
        return False
    try:
        probe.unlink()
    except OSError:
        return False
    return True


def _print_config_sources(
    *,
    env_tag: str,
    home: Path,
    config_dir: Path | None,
) -> list[str]:
    """加载 Settings、打印每字段路径 → LayerScope，敏感字段值掩码。"""
    warnings: list[str] = []
    cfg_path = config_dir or _autodetect_config_dir()
    if cfg_path is None or not cfg_path.is_dir():
        warnings.append(f"config dir not found (looked at {cfg_path}); skip config provenance")
        typer.echo("  <unavailable: no config/ dir found>")
        return warnings

    try:
        settings = Settings.load(
            config_dir=cfg_path,
            env=env_tag,
            local_path=(home / "config.yaml") if (home / "config.yaml").is_file() else None,
        )
    except ValidationError as exc:
        warnings.append(f"settings validation error: {exc.error_count()} issue(s)")
        typer.echo("  <unavailable: settings validation failed>")
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"])
            typer.echo(f"  - {loc}: {err['msg']}")
        return warnings
    except (OSError, ValueError) as exc:
        warnings.append(f"settings load error: {exc}")
        typer.echo(f"  <unavailable: {exc}>")
        return warnings

    layer_map = settings.field_layer_map()
    flat_values = sorted(
        _flatten_for_provenance(settings.model_dump(mode="python")),
        key=lambda kv: kv[0],
    )
    # 以模型实际叶子为准（layer_map 中可能含被高优先级 list 整体替换后失效的
    # 旧路径；那些路径对当前 settings 没意义，不展示）。
    for path, raw_value in flat_values:
        layer = layer_map.get(path, "base")
        display = _REDACTED if _is_sensitive_path(path) else _stringify(raw_value)
        typer.echo(f"  {path:<48} [{layer:<6}] = {display}")
    return warnings


def _autodetect_config_dir() -> Path | None:
    """寻找仓库根 ``config/`` 目录。

    优先 ``$COPY_TRADER_CONFIG_DIR``（测试可设）；否则从 CWD 向上找 ``config/base.yaml``。
    """
    explicit = os.environ.get("COPY_TRADER_CONFIG_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    here = Path.cwd().resolve()
    for parent in [here, *here.parents]:
        candidate = parent / "config"
        if (candidate / "base.yaml").is_file():
            return candidate
    return None


def _is_sensitive_path(path: str) -> bool:
    """字段路径任一段以敏感后缀结尾即视为敏感。"""
    lowered = path.lower()
    # 切分掉 list 索引：accounts.spot.api_key, foo[0].secret_token 都要识别。
    segments: list[str] = []
    for seg in lowered.replace("[", ".[").split("."):
        if seg.startswith("["):
            continue
        segments.append(seg)
    return any(seg.endswith(suffix) for seg in segments for suffix in _SENSITIVE_SUFFIXES)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return repr(value)


def _flatten_for_provenance(node: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """把嵌套 dict / list 拍平成 ``(dotted_path, leaf_value)`` 序列。

    路径风格与 ``Settings.field_layer_map()`` 一致：list 用 ``[i]``、dict 用 ``.``。
    """
    out: list[tuple[str, Any]] = []
    if isinstance(node, Mapping):
        for k, v in node.items():
            sub = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, Mapping | list):
                out.extend(_flatten_for_provenance(v, sub))
            else:
                out.append((sub, v))
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            sub = f"{prefix}[{idx}]"
            if isinstance(item, Mapping | list):
                out.extend(_flatten_for_provenance(item, sub))
            else:
                out.append((sub, item))
    else:
        out.append((prefix, node))
    return out


_PROBLEM_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "cache_overridden",
        "ledger_exchange_mismatch",
        "unknown_position_on_exchange",
        "unknown_position_acknowledged",
    }
)


def _print_reconcile_summary(result: ReconcileRunResult) -> None:
    """打印 reconcile 结果摘要。

    展示分支（按优先级）：

    1. ``report.fatal`` → 打印 fatal_message，提示 ``--acknowledge-unknown``
    2. ``report.safe_mode`` → 打印 SAFE / mismatch warning
    3. ledger 完全为空 + 没有"问题事件"（cache_overridden / mismatch /
       unknown 等） → "已对齐"（验收清单语义：空账户跑 reconcile 报告
       已对齐并退出 0；cache_created 这种"刚刚自动建好 cache 文件"不计为
       问题事件）
    4. 其他（cache_drift / ack_unknown / 全 OK）→ "reconcile OK"
    """
    report = result.report
    typer.echo("== copy-trader reconcile ==")
    typer.echo(f"account     : {report.account}")
    typer.echo(f"symbols     : {list(report.symbols)}")
    typer.echo(f"apply       : {result.applied}")
    if report.log_path is not None:
        typer.echo(f"log_path    : {report.log_path}")

    if report.events:
        typer.echo("")
        typer.echo("[events]")
        for ev in report.events:
            typer.echo(f"  [{ev.level:<7}] {ev.kind} {ev.symbol}: {ev.message}")

    if report.fatal:
        typer.echo("")
        typer.echo("结果：FATAL — 检测到 unknown_position_on_exchange。")
        msg = report.fatal_message or "unknown_position_on_exchange"
        typer.echo(msg)
        typer.echo("提示：经人工确认后可加 --acknowledge-unknown 重新启动。")
        return

    if report.safe_mode:
        typer.echo("")
        typer.echo("结果：SAFE 模式 — 检测到 ledger ↔ exchange mismatch（仅平仓不开仓）。")
        return

    has_problem_events = any(ev.kind in _PROBLEM_EVENT_KINDS for ev in report.events)

    typer.echo("")
    if result.empty_ledger and not has_problem_events:
        typer.echo("已对齐：ledger 为空、与交易所一致；reconcile OK。")
    elif not has_problem_events:
        typer.echo("已对齐：ledger 与交易所一致；reconcile OK。")
    else:
        typer.echo("结果：reconcile OK（仅 cache_drift / ack 类事件，已自动处理）。")


def _safe_close(obj: Any) -> None:
    """尽力关闭含 ``close`` 方法的资源；忽略关闭异常。"""
    close = getattr(obj, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["app"]


if __name__ == "__main__":  # pragma: no cover
    app()
