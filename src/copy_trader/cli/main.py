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
def run() -> None:
    """[M1+] 启动主交易循环（live 形态）。"""
    _pending("run", "M1+")


@app.command()
def paper() -> None:
    """[M1+] 启动 paper 模式（不下真单）。"""
    _pending("paper", "M1+")


@app.command()
def backtest() -> None:
    """[M3+] 跑回测。"""
    _pending("backtest", "M3+")


@app.command()
def reconcile() -> None:
    """[M2+] 跑 reconcile（与交易所对账）。"""
    _pending("reconcile", "M2+")


@app.command()
def dashboard() -> None:
    """[M4] 启动 dashboard（含设置中心）。"""
    _pending("dashboard", "M4")


@app.command()
def registry() -> None:
    """[M2] ExchangeRegistry 列表 / 检查。"""
    _pending("registry", "M2")


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


__all__ = ["app"]


if __name__ == "__main__":  # pragma: no cover
    app()
