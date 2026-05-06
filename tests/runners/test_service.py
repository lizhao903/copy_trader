"""RunnerService 状态机 + 心跳 + reap + cascade kill 测试 (issue #26)。"""

from __future__ import annotations

import signal
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from copy_trader.persistence import RunnerRegistry
from copy_trader.runners import HEARTBEAT_TIMEOUT_SECONDS, InvalidStateTransition, RunnerService


class _ClockStub:
    """可控时钟。"""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture
def registry(tmp_path: Any) -> RunnerRegistry:
    return RunnerRegistry(tmp_path / "registry.db")


@pytest.fixture
def clock() -> _ClockStub:
    return _ClockStub()


@pytest.fixture
def service(registry: RunnerRegistry, clock: _ClockStub) -> RunnerService:
    return RunnerService(registry, clock=clock, kill_signal_send=lambda pid, sig: None)


# ---------- CRUD ----------


def test_create_initializes_to_stopped(service: RunnerService) -> None:
    runner = service.create(
        name="hello-spot",
        venue="binance.spot",
        account="spot",
        strategy="hello",
    )
    assert runner.status == "stopped"
    assert runner.mode == "dry-run"


def test_create_with_params(service: RunnerService) -> None:
    runner = service.create(
        name="x",
        venue="binance.spot",
        account="acc",
        strategy="hello",
        mode="paper",
        params_override={"slippage": 5},
    )
    assert runner.mode == "paper"
    assert runner.params_override == {"slippage": 5}


def test_resolve_by_name(service: RunnerService) -> None:
    service.create(name="alpha", venue="binance.spot", account="acc", strategy="hello")
    runner = service._resolve("alpha")
    assert runner.name == "alpha"


# ---------- 状态机 ----------


def test_legal_lifecycle(service: RunnerService) -> None:
    """draft → stopped → starting → running → stopping → stopped。"""
    runner = service.create(name="a", venue="binance.spot", account="acc", strategy="hello")
    assert runner.status == "stopped"

    runner = service.start(runner.id)
    assert runner.status == "starting"

    runner = service.mark_running(runner.id, pid=12345)
    assert runner.status == "running"
    assert runner.pid == 12345

    runner = service.stop(runner.id)
    assert runner.status == "stopping"

    runner = service.mark_stopped(runner.id)
    assert runner.status == "stopped"
    assert runner.pid is None


def test_repeat_start_running_raises(service: RunnerService) -> None:
    """spec acceptance: 重复 start running 实例 → InvalidStateTransition。"""
    runner = service.create(name="a", venue="binance.spot", account="acc", strategy="hello")
    service.start(runner.id)
    service.mark_running(runner.id, pid=1)
    with pytest.raises(InvalidStateTransition):
        service.start(runner.id)  # running → starting 非法


def test_stop_from_stopped_raises(service: RunnerService) -> None:
    runner = service.create(name="a", venue="binance.spot", account="acc", strategy="hello")
    with pytest.raises(InvalidStateTransition):
        service.stop(runner.id)


def test_errored_can_recover_to_stopped(service: RunnerService) -> None:
    """errored → stopped 允许 (修复后重启路径)。"""
    runner = service.create(name="a", venue="binance.spot", account="acc", strategy="hello")
    service.start(runner.id)
    service.mark_running(runner.id, pid=1)
    # 模拟变 errored (通常 reap 触发)
    service._registry.update(runner.id, status="errored")
    # mark_stopped 是 stopping → stopped, 但 errored → stopped 是合法转移
    runner = service._registry.update(runner.id, status="stopped")
    assert runner.status == "stopped"


# ---------- 心跳 + reap ----------


def test_heartbeat_updates_running(service: RunnerService, clock: _ClockStub) -> None:
    runner = service.create(name="a", venue="binance.spot", account="acc", strategy="hello")
    service.start(runner.id)
    service.mark_running(runner.id, pid=1)
    clock.advance(10)
    runner = service.heartbeat(runner.id)
    assert runner.last_heartbeat == clock.now


def test_heartbeat_outside_running_raises(service: RunnerService) -> None:
    runner = service.create(name="a", venue="binance.spot", account="acc", strategy="hello")
    with pytest.raises(InvalidStateTransition):
        service.heartbeat(runner.id)  # stopped 状态不能 heartbeat


def test_reap_skips_recent_heartbeat(service: RunnerService, clock: _ClockStub) -> None:
    runner = service.create(name="a", venue="binance.spot", account="acc", strategy="hello")
    service.start(runner.id)
    service.mark_running(runner.id, pid=1)
    # 心跳刚更新, 还没超时
    clock.advance(10)
    reaped = service.reap()
    assert reaped == []


def test_reap_marks_stale_running_as_errored(service: RunnerService, clock: _ClockStub) -> None:
    """spec acceptance: 心跳超时被 reap 标 errored。"""
    runner = service.create(name="a", venue="binance.spot", account="acc", strategy="hello")
    service.start(runner.id)
    service.mark_running(runner.id, pid=1)
    # 心跳前进超过阈值
    clock.advance(HEARTBEAT_TIMEOUT_SECONDS + 1)
    reaped = service.reap()
    assert len(reaped) == 1
    assert reaped[0].status == "errored"
    assert reaped[0].pid is None


def test_reap_kills_stale_pid(service: RunnerService, clock: _ClockStub) -> None:
    """reap 时尝试 SIGKILL stale PID。"""
    kill_calls: list[tuple[int, int]] = []
    service._kill = lambda pid, sig: kill_calls.append((pid, sig))

    runner = service.create(name="a", venue="binance.spot", account="acc", strategy="hello")
    service.start(runner.id)
    service.mark_running(runner.id, pid=12345)
    clock.advance(HEARTBEAT_TIMEOUT_SECONDS + 1)
    service.reap()
    assert kill_calls == [(12345, signal.SIGKILL)]


# ---------- cascade delete + 列表 ----------


def test_delete_running_runner_cascade_kill(
    service: RunnerService, clock: _ClockStub, registry: RunnerRegistry, tmp_path: Any
) -> None:
    """spec acceptance: delete running 实例 cascade kill 后 registry 行被移除、PID 文件被清理。"""
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()

    kill_calls: list[tuple[int, int]] = []
    service_with_pid_dir = RunnerService(
        registry,
        pid_dir=pid_dir,
        clock=clock,
        kill_signal_send=lambda pid, sig: kill_calls.append((pid, sig)),
    )

    runner = service_with_pid_dir.create(
        name="a", venue="binance.spot", account="acc", strategy="hello"
    )
    service_with_pid_dir.start(runner.id)
    service_with_pid_dir.mark_running(runner.id, pid=12345)

    # 创建 pid 文件
    pid_file = pid_dir / f"{runner.id}.pid"
    pid_file.write_text("12345")

    # delete - 立即 (kill_timeout=0 触发 SIGKILL 路径)
    service_with_pid_dir.delete(runner.id, kill_timeout_seconds=0.0)

    # registry 行已移除
    from copy_trader.persistence import RunnerNotFoundError

    with pytest.raises(RunnerNotFoundError):
        registry.get(id=runner.id)

    # PID 文件被清理
    assert not pid_file.exists()

    # SIGTERM (stop 时) + SIGKILL (delete cascade kill) 都发出
    assert (12345, signal.SIGTERM) in kill_calls
    assert (12345, signal.SIGKILL) in kill_calls


def test_list_filtered_by_status(service: RunnerService) -> None:
    a = service.create(name="a", venue="binance.spot", account="acc", strategy="hello")
    b = service.create(name="b", venue="binance.spot", account="acc", strategy="hello")
    service.start(a.id)
    service.mark_running(a.id, pid=1)
    running = service.list_all(status="running")
    assert {r.id for r in running} == {a.id}
    stopped = service.list_all(status="stopped")
    assert {r.id for r in stopped} == {b.id}
