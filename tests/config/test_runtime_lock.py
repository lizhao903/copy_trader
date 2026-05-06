"""runtime-isolation 验收测试（issue #3）。

覆盖 spec 要求的 5 个场景：

1. 缺 ``COPY_TRADER_ENV`` → fail-fast（``MissingEnvError``）。
2. CLI > env > 默认 的解析优先级。
3. 跨 ``env_tag`` 启动被拒（``CrossEnvironmentError``，错误消息列两侧值）。
4. 跨 ``machine_id`` 启动被拒（``CrossMachineError``，错误消息列两侧值）。
5. 子目录以 ``0700`` 创建。

所有测试都把 ``COPY_TRADER_HOME`` 重定向到 ``tmp_path``；不会触碰用户实际
``~/.copy_trader/`` 或仓库根 ``state/``。
"""

from __future__ import annotations

import json
import os
import stat
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from copy_trader.config.runtime import (
    RUNTIME_LOCK_SCHEMA_VERSION,
    CrossEnvironmentError,
    CrossMachineError,
    InvalidEnvError,
    MissingEnvError,
    read_runtime_lock,
    resolve_runtime,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """每个 test 都从干净 env 开始，避免外部 shell / 同 session 测试污染。"""
    monkeypatch.delenv("COPY_TRADER_ENV", raising=False)
    monkeypatch.delenv("COPY_TRADER_HOME", raising=False)
    yield


def _read_machine_id(home: Path) -> str:
    return (home / "state" / ".machine_id").read_text(encoding="utf-8").strip()


def _write_machine_id(home: Path, value: str) -> None:
    (home / "state").mkdir(mode=0o700, parents=True, exist_ok=True)
    (home / "state" / ".machine_id").write_text(value + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Scenario 1：缺 COPY_TRADER_ENV → fail-fast                                  #
# --------------------------------------------------------------------------- #


def test_missing_env_var_fails_fast(tmp_path: Path) -> None:
    """spec: 进程在加载任何业务模块前 fail-fast，错误信息列出受支持取值与示例。"""
    with pytest.raises(MissingEnvError) as excinfo:
        resolve_runtime(home=tmp_path)
    msg = str(excinfo.value)
    assert "COPY_TRADER_ENV" in msg
    # 错误信息必须把可选值告知用户，不能只是空泛的"缺变量"。
    for env_tag in ("dev", "paper", "prod"):
        assert env_tag in msg
    assert "export" in msg  # 含示例


def test_invalid_env_var_value_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """spec 隐含约束：env_tag 必须 ∈ {dev, paper, prod}，外的值同样 fail-fast。"""
    monkeypatch.setenv("COPY_TRADER_ENV", "staging")
    with pytest.raises(InvalidEnvError) as excinfo:
        resolve_runtime(home=tmp_path)
    msg = str(excinfo.value)
    assert "staging" in msg
    assert "dev" in msg


# --------------------------------------------------------------------------- #
# Scenario 2：解析优先级 CLI > env > 默认                                      #
# --------------------------------------------------------------------------- #


def test_resolution_priority_cli_overrides_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI 传入的 home 优先于环境变量。"""
    cli_home = tmp_path / "cli_home"
    env_home = tmp_path / "env_home"
    monkeypatch.setenv("COPY_TRADER_HOME", str(env_home))

    ctx = resolve_runtime(env="dev", home=cli_home)

    assert ctx.home == cli_home.resolve()
    assert ctx.home_source == "cli"
    assert not env_home.exists(), "env 来源的目录不应被创建"


def test_resolution_priority_env_used_when_cli_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无 CLI 参数时退化到 env 变量。"""
    env_home = tmp_path / "env_home"
    monkeypatch.setenv("COPY_TRADER_ENV", "dev")
    monkeypatch.setenv("COPY_TRADER_HOME", str(env_home))

    ctx = resolve_runtime()

    assert ctx.home == env_home.resolve()
    assert ctx.home_source == "env"
    assert ctx.env_tag == "dev"


# --------------------------------------------------------------------------- #
# Scenario 3：跨 env_tag 启动被拒，错误消息列两侧值                             #
# --------------------------------------------------------------------------- #


def test_cross_env_tag_rejected_with_both_sides(tmp_path: Path) -> None:
    """spec: 锁文件已记 dev，再用 paper 启动 → 抛错并打印两侧 env_tag。"""
    home = tmp_path / "shared_home"

    # 第一次以 dev 启动，落锁。
    first = resolve_runtime(env="dev", home=home)
    lock = read_runtime_lock(first.home)
    assert lock is not None
    assert lock["env_tag"] == "dev"

    # 第二次以 paper 启动同一 home → 必须抛 CrossEnvironmentError。
    with pytest.raises(CrossEnvironmentError) as excinfo:
        resolve_runtime(env="paper", home=home)

    msg = str(excinfo.value)
    # spec 要求两侧值同时出现在错误信息里。
    assert "'dev'" in msg
    assert "'paper'" in msg
    assert str(home.resolve()) in msg


def test_same_env_restart_allowed(tmp_path: Path) -> None:
    """spec: 同机器同环境重启允许，pid / started_at 更新。"""
    home = tmp_path / "home"
    first = resolve_runtime(env="dev", home=home)
    second = resolve_runtime(env="dev", home=home)

    assert first.machine_id == second.machine_id
    assert first.env_tag == second.env_tag == "dev"
    # 同进程内 pid 一致是正常的；started_at 至少被覆盖一次。
    lock_after = read_runtime_lock(home)
    assert lock_after is not None
    assert lock_after["env_tag"] == "dev"
    assert lock_after["schema_version"] == RUNTIME_LOCK_SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# Scenario 4：跨 machine_id 启动被拒，错误消息列两侧值                          #
# --------------------------------------------------------------------------- #


def test_cross_machine_id_rejected_with_both_sides(tmp_path: Path) -> None:
    """spec: rsync 过来的 state 目录被新机器拒绝。"""
    home = tmp_path / "home"

    # 模拟"从生产机 rsync 过来的 state"：先用某个固定 machine_id 落锁。
    fake_remote_machine_id = str(uuid.uuid4())
    (home / "state").mkdir(mode=0o700, parents=True)
    (home / "state" / ".machine_id").write_text(fake_remote_machine_id + "\n", encoding="utf-8")
    (home / "state" / ".runtime_lock.json").write_text(
        json.dumps(
            {
                "env_tag": "dev",
                "machine_id": fake_remote_machine_id,
                "schema_version": RUNTIME_LOCK_SCHEMA_VERSION,
                "pid": 99999,
                "started_at": "2025-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    # 再"换一台机器"：把 .machine_id 换成本机的另一 UUID（保留旧锁的 remote id）。
    local_machine_id = str(uuid.uuid4())
    assert local_machine_id != fake_remote_machine_id
    (home / "state" / ".machine_id").write_text(local_machine_id + "\n", encoding="utf-8")

    with pytest.raises(CrossMachineError) as excinfo:
        resolve_runtime(env="dev", home=home)

    msg = str(excinfo.value)
    assert fake_remote_machine_id in msg
    assert local_machine_id in msg
    assert str(home.resolve()) in msg


# --------------------------------------------------------------------------- #
# Scenario 5：子目录 0700 权限创建                                             #
# --------------------------------------------------------------------------- #


def test_subdirs_created_with_0700_permissions(tmp_path: Path) -> None:
    """spec: state/logs/pids/db/secrets 子目录以 0700 创建。"""
    home = tmp_path / "fresh_home"
    ctx = resolve_runtime(env="dev", home=home)

    # home 自身也是 0700。
    assert _mode(ctx.home) == 0o700

    for sub in ("state", "logs", "pids", "db", "secrets"):
        path = ctx.home / sub
        assert path.is_dir(), f"missing subdir {sub}"
        assert _mode(path) == 0o700, f"subdir {sub} 权限非 0700: {oct(_mode(path))}"

    # machine_id 与 lock 文件作为 0600 落到 state/ 内。
    machine_id_file = ctx.home / "state" / ".machine_id"
    lock_file = ctx.home / "state" / ".runtime_lock.json"
    assert machine_id_file.is_file()
    assert lock_file.is_file()
    assert _mode(machine_id_file) == 0o600
    assert _mode(lock_file) == 0o600

    # machine_id 内容是合法 UUID。
    uuid.UUID(_read_machine_id(ctx.home))


def test_machine_id_persists_across_calls(tmp_path: Path) -> None:
    """`state/.machine_id` 一旦生成就不变；锁里的 machine_id 与之一致。"""
    home = tmp_path / "home"
    ctx1 = resolve_runtime(env="dev", home=home)
    ctx2 = resolve_runtime(env="dev", home=home)

    assert ctx1.machine_id == ctx2.machine_id == _read_machine_id(home)
    lock = read_runtime_lock(home)
    assert lock is not None
    assert lock["machine_id"] == ctx1.machine_id


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #


def _mode(path: Path) -> int:
    """Return the file-mode permission bits (rwx)，去掉 file-type 高位。"""
    return stat.S_IMODE(os.stat(path).st_mode)
