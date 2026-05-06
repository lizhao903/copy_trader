"""Typer CLI 入口（issue #5 骨架）。

公共 entrypoint：``copy-trader`` — 通过 ``[project.scripts]`` 暴露成
``copy_trader.cli.main:app``。

本提交只放骨架：``app = typer.Typer(...)`` + 7 个子命令注册（``run`` /
``paper`` / ``backtest`` / ``reconcile`` / ``dashboard`` / ``registry`` /
``doctor``）。除了 ``doctor`` 之外都是 pending stub；``doctor`` 也先用 stub
占位，下一笔 commit 落实运行时 / 配置来源 / 敏感字段掩码。

import-linter 的 ``cli-only-runners-config`` 契约允许 cli 依赖 ``runners`` 与
``config``；目前骨架只 import typer，下一笔 commit 才会拉 ``copy_trader.config``。
"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="copy-trader",
    help="Copy-trade system CLI（M0 实装 doctor，其余子命令为 M1+ 占位）。",
    no_args_is_help=True,
    add_completion=False,
)


def _pending(name: str, milestone: str) -> None:
    """统一打印 pending 提示并退出码 1，便于后续接入而 ``--help`` 仍可跑通。"""
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


@app.command()
def doctor() -> None:
    """打印运行时根目录、锁状态、子目录可写性、配置来源（下一笔 commit 实装）。"""
    _pending("doctor", "issue #5 follow-up commit")


__all__ = ["app"]


if __name__ == "__main__":  # pragma: no cover
    app()
