"""RunnerService 生命周期管理 (issue #26)。

CLI 与 Dashboard 共用的服务对象, 把 runner 全生命周期收敛到单一服务对象。

状态机 (严格 finite):

    draft → stopped → starting → running → stopping → stopped
                                              └→ errored (心跳超时 / 异常)

`InvalidStateTransition` 错误描述非法转移; 状态校验集中在 `_TRANSITIONS` 表。

心跳: runner 主循环每 30s 调 `RunnerRegistry.heartbeat`; `reap()` 把超过 60s
未更新的 running 标 errored 并尝试 kill PID。

Cascade kill: `delete()` 一个 running 实例先发 stop, 30s 内未达 stopped 则
SIGKILL; 完成后从 registry 移除行 + 清理 PID 文件。

实装层不直接 spawn 子进程 (那是 systemd / Dashboard 的职责); 本服务只维护
状态 + 心跳 + (可选) 信号发送。
"""

from __future__ import annotations

import os
import signal as _signal
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from copy_trader.core import RunnerInstance, RunnerMode, RunnerStatus
from copy_trader.persistence import (
    RunnerNotFoundError,
    RunnerRegistry,
)

__all__ = [
    "InvalidStateTransition",
    "RunnerService",
]


# 合法转移表; key 为 from-status, value 为允许的 to-status 集合。
_TRANSITIONS: dict[RunnerStatus, set[RunnerStatus]] = {
    "draft": {"stopped", "errored"},
    "stopped": {"starting", "errored"},
    "starting": {"running", "errored", "stopped"},
    "running": {"stopping", "errored"},
    "stopping": {"stopped", "errored"},
    "errored": {"stopped"},  # 修复后允许重新启
}

# 心跳超时阈值 (秒)。超过此值的 running 行被 reap 标 errored。
HEARTBEAT_TIMEOUT_SECONDS = 60.0


class InvalidStateTransition(ValueError):
    """非法状态机转移 (例如 running → starting)。"""

    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(
            f"invalid state transition: {from_status!r} → {to_status!r} "
            f"(allowed from {from_status!r}: {_TRANSITIONS.get(from_status, set())})"
        )
        self.from_status = from_status
        self.to_status = to_status


class RunnerService:
    """生命周期服务 (CLI / Dashboard 共用)。"""

    def __init__(
        self,
        registry: RunnerRegistry,
        *,
        pid_dir: Path | None = None,
        clock: Any = lambda: datetime.now(UTC),
        kill_signal_send: Any = None,  # 注入便于测试
    ) -> None:
        self._registry = registry
        self._pid_dir = pid_dir
        self._clock = clock
        # 默认用 os.kill;测试可注入 fake
        self._kill = kill_signal_send if kill_signal_send is not None else _safe_os_kill

    # --- CRUD ----------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        venue: str,
        account: str,
        strategy: str,
        mode: RunnerMode = "dry-run",
        params_override: dict[str, Any] | None = None,
    ) -> RunnerInstance:
        now = self._clock()
        runner = RunnerInstance(
            id=str(uuid.uuid4()),
            name=name,
            venue=venue,
            account=account,
            strategy=strategy,
            params_override=params_override or {},
            mode=mode,
            status="draft",
            pid=None,
            last_heartbeat=None,
            created_at=now,
            updated_at=now,
        )
        self._registry.create(runner)
        # draft → stopped 算是「初始化完成」, 但 spec 要求显式状态
        return self._registry.update(runner.id, status="stopped")

    def update(
        self,
        id_or_name: str,
        *,
        params_override: dict[str, Any] | None = None,
        mode: RunnerMode | None = None,
    ) -> RunnerInstance:
        runner = self._resolve(id_or_name)
        return self._registry.update(
            runner.id,
            params_override=params_override,
            mode=mode,
        )

    def delete(self, id_or_name: str, *, kill_timeout_seconds: float = 30.0) -> None:
        """删除 runner; running 状态先发 stop 等 30s, 超时 SIGKILL。"""
        runner = self._resolve(id_or_name)
        if runner.status == "running":
            self.stop(runner.id)
            deadline = time.monotonic() + kill_timeout_seconds
            while time.monotonic() < deadline:
                refreshed = self._registry.get(id=runner.id)
                if refreshed.status in ("stopped", "errored"):
                    break
                time.sleep(0.1)
            else:
                # 超时 SIGKILL
                if runner.pid is not None:
                    self._kill(runner.pid, _signal.SIGKILL)
                self._registry.update(runner.id, status="errored", pid=None)
        # 清理 pid 文件
        if self._pid_dir is not None:
            pid_file = self._pid_dir / f"{runner.id}.pid"
            if pid_file.exists():
                pid_file.unlink()
        self._registry.delete(runner.id)

    def list_all(self, *, status: RunnerStatus | None = None) -> list[RunnerInstance]:
        """按 status 过滤列出所有 runners。命名 list_all 避免与 builtin list 冲突。"""
        return self._registry.list(status=status)

    # --- lifecycle -----------------------------------------------------

    def start(self, id_or_name: str) -> RunnerInstance:
        """stopped → starting (spec: external supervisor 接续 starting → running)。"""
        runner = self._resolve(id_or_name)
        self._enforce_transition(runner.status, "starting")
        return self._registry.update(runner.id, status="starting")

    def mark_running(self, id_or_name: str, pid: int) -> RunnerInstance:
        """starting → running (由外部 supervisor 在子进程启动后调用)。"""
        runner = self._resolve(id_or_name)
        self._enforce_transition(runner.status, "running")
        return self._registry.update(
            runner.id,
            status="running",
            pid=pid,
            last_heartbeat=self._clock(),
        )

    def stop(self, id_or_name: str) -> RunnerInstance:
        """running → stopping (主循环检测到 stopping 后自然退出)。"""
        runner = self._resolve(id_or_name)
        self._enforce_transition(runner.status, "stopping")
        # 给主循环发 SIGTERM 让它优雅退出
        if runner.pid is not None:
            self._kill(runner.pid, _signal.SIGTERM)
        return self._registry.update(runner.id, status="stopping")

    def mark_stopped(self, id_or_name: str) -> RunnerInstance:
        """stopping → stopped (主循环退出后调)。"""
        runner = self._resolve(id_or_name)
        self._enforce_transition(runner.status, "stopped")
        return self._registry.update(runner.id, status="stopped", pid=None)

    def heartbeat(self, id_or_name: str) -> RunnerInstance:
        """running 状态下更新 last_heartbeat。"""
        runner = self._resolve(id_or_name)
        if runner.status != "running":
            raise InvalidStateTransition(runner.status, "heartbeat")
        return self._registry.update(runner.id, last_heartbeat=self._clock())

    def reap(self) -> list[RunnerInstance]:
        """扫描 running 行, last_heartbeat 超 60s 标 errored + 尝试 kill PID。

        返回被 reap 的 runner 列表。
        """
        reaped: list[RunnerInstance] = []
        now = self._clock()
        cutoff = now - timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS)
        for runner in self._registry.list(status="running"):
            if runner.last_heartbeat is None or runner.last_heartbeat < cutoff:
                if runner.pid is not None:
                    self._kill(runner.pid, _signal.SIGKILL)
                updated = self._registry.update(
                    runner.id,
                    status="errored",
                    pid=None,
                )
                reaped.append(updated)
        return reaped

    # --- helpers -------------------------------------------------------

    def _resolve(self, id_or_name: str) -> RunnerInstance:
        """允许传 id 或 name。"""
        try:
            return self._registry.get(id=id_or_name)
        except RunnerNotFoundError:
            return self._registry.get(name=id_or_name)

    def _enforce_transition(self, from_status: RunnerStatus, to_status: RunnerStatus) -> None:
        allowed = _TRANSITIONS.get(from_status, set())
        if to_status not in allowed:
            raise InvalidStateTransition(from_status, to_status)


def _safe_os_kill(pid: int, sig: int) -> None:
    """os.kill 但忽略 ProcessLookupError (PID 已退出)。"""
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass
