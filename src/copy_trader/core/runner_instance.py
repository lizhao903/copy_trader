"""RunnerInstance 值对象 (issue #25)。

`RunnerInstance` 是持久化的 runner 一等对象。每个跑起来的 LiveRunner /
BacktestRunner 都有一个 RunnerInstance 行,在 `runner_instances` SQLite 表里
持久。CLI / Dashboard 的 RunnerService 通过它做 CRUD + 状态机管理。

字段集严格对齐 spec runner-lifecycle 第一条 Requirement:
- ``id``: uuid (uuid7 优先, 回退 uuid4) 主键
- ``name``: 用户可读名 (唯一约束)
- ``venue`` / ``account`` / ``strategy`` / ``mode``: 启动参数
- ``params_override``: dict, 策略参数覆盖
- ``status``: draft / stopped / starting / running / stopping / errored
- ``pid`` / ``last_heartbeat``: 运行时状态 (#26 RunnerService 维护)
- ``created_at`` / ``updated_at``: 审计字段
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

RunnerStatus = Literal[
    "draft",
    "stopped",
    "starting",
    "running",
    "stopping",
    "errored",
]
RunnerMode = Literal["live", "paper", "dry-run", "backtest"]


class RunnerInstance(BaseModel):
    """持久化 runner 一等对象 (frozen, strict)。"""

    model_config = ConfigDict(frozen=True, strict=True)

    id: str
    name: str
    venue: str
    account: str
    strategy: str
    params_override: dict[str, Any]
    mode: RunnerMode
    status: RunnerStatus
    pid: int | None
    last_heartbeat: datetime | None
    created_at: datetime
    updated_at: datetime
