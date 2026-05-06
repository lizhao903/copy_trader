"""CLI registry 子命令 smoke (issue #27)。

7 个子命令: create / update / delete / start / stop / list / reap。
所有操作走 RunnerService (cli 不实装独立生命周期逻辑)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from copy_trader.cli.main import app


@pytest.fixture
def cli_env(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Path:
    """每个测试用独立 home + dev env, 避免污染。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("COPY_TRADER_ENV", "dev")
    return home


@pytest.fixture
def runner_cli() -> CliRunner:
    return CliRunner()


def test_create_and_list(runner_cli: CliRunner, cli_env: Path) -> None:
    result = runner_cli.invoke(
        app,
        [
            "registry",
            "create",
            "--name",
            "alpha",
            "--venue",
            "binance.spot",
            "--account",
            "spot",
            "--strategy",
            "hello",
            "--home",
            str(cli_env),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "created" in result.stdout
    assert "name=alpha" in result.stdout
    assert "status=stopped" in result.stdout

    list_result = runner_cli.invoke(app, ["registry", "list", "--home", str(cli_env)])
    assert list_result.exit_code == 0
    assert "name=alpha" in list_result.stdout


def test_create_duplicate_name_fails(runner_cli: CliRunner, cli_env: Path) -> None:
    args = [
        "registry",
        "create",
        "--name",
        "dup",
        "--venue",
        "binance.spot",
        "--account",
        "spot",
        "--strategy",
        "hello",
        "--home",
        str(cli_env),
    ]
    r1 = runner_cli.invoke(app, args)
    assert r1.exit_code == 0
    r2 = runner_cli.invoke(app, args)
    assert r2.exit_code == 1
    assert "already exists" in r2.stdout


def test_create_invalid_mode(runner_cli: CliRunner, cli_env: Path) -> None:
    result = runner_cli.invoke(
        app,
        [
            "registry",
            "create",
            "--name",
            "x",
            "--venue",
            "binance.spot",
            "--account",
            "spot",
            "--strategy",
            "hello",
            "--mode",
            "bogus",
            "--home",
            str(cli_env),
        ],
    )
    assert result.exit_code == 1
    assert "--mode" in result.stdout


def test_create_invalid_params_json(runner_cli: CliRunner, cli_env: Path) -> None:
    result = runner_cli.invoke(
        app,
        [
            "registry",
            "create",
            "--name",
            "x",
            "--venue",
            "binance.spot",
            "--account",
            "spot",
            "--strategy",
            "hello",
            "--params",
            "not-json",
            "--home",
            str(cli_env),
        ],
    )
    assert result.exit_code == 1


def test_update(runner_cli: CliRunner, cli_env: Path) -> None:
    runner_cli.invoke(
        app,
        [
            "registry",
            "create",
            "--name",
            "u",
            "--venue",
            "binance.spot",
            "--account",
            "spot",
            "--strategy",
            "hello",
            "--home",
            str(cli_env),
        ],
    )
    result = runner_cli.invoke(
        app,
        ["registry", "update", "u", "--mode", "paper", "--home", str(cli_env)],
    )
    assert result.exit_code == 0, result.stdout
    assert "updated" in result.stdout


def test_start_stopped_runner(runner_cli: CliRunner, cli_env: Path) -> None:
    runner_cli.invoke(
        app,
        [
            "registry",
            "create",
            "--name",
            "s",
            "--venue",
            "binance.spot",
            "--account",
            "spot",
            "--strategy",
            "hello",
            "--home",
            str(cli_env),
        ],
    )
    result = runner_cli.invoke(app, ["registry", "start", "s", "--home", str(cli_env)])
    assert result.exit_code == 0
    assert "starting" in result.stdout


def test_delete(runner_cli: CliRunner, cli_env: Path) -> None:
    runner_cli.invoke(
        app,
        [
            "registry",
            "create",
            "--name",
            "d",
            "--venue",
            "binance.spot",
            "--account",
            "spot",
            "--strategy",
            "hello",
            "--home",
            str(cli_env),
        ],
    )
    result = runner_cli.invoke(app, ["registry", "delete", "d", "--home", str(cli_env)])
    assert result.exit_code == 0
    assert "deleted" in result.stdout

    # 删后 list 不再显示
    list_result = runner_cli.invoke(app, ["registry", "list", "--home", str(cli_env)])
    assert "no runners" in list_result.stdout or "name=d" not in list_result.stdout


def test_list_filter_by_status(runner_cli: CliRunner, cli_env: Path) -> None:
    runner_cli.invoke(
        app,
        [
            "registry",
            "create",
            "--name",
            "running1",
            "--venue",
            "binance.spot",
            "--account",
            "spot",
            "--strategy",
            "hello",
            "--home",
            str(cli_env),
        ],
    )
    runner_cli.invoke(app, ["registry", "start", "running1", "--home", str(cli_env)])
    # 现在 running1 是 starting (stopped→starting)

    starting_list = runner_cli.invoke(
        app, ["registry", "list", "--status", "starting", "--home", str(cli_env)]
    )
    assert "running1" in starting_list.stdout

    stopped_list = runner_cli.invoke(
        app, ["registry", "list", "--status", "stopped", "--home", str(cli_env)]
    )
    assert "running1" not in stopped_list.stdout


def test_reap_no_runners(runner_cli: CliRunner, cli_env: Path) -> None:
    result = runner_cli.invoke(app, ["registry", "reap", "--home", str(cli_env)])
    assert result.exit_code == 0
    assert "none reaped" in result.stdout


def test_delete_nonexistent(runner_cli: CliRunner, cli_env: Path) -> None:
    result = runner_cli.invoke(app, ["registry", "delete", "ghost", "--home", str(cli_env)])
    assert result.exit_code == 1
