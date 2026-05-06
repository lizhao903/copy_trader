"""SQLite-backed Runner Registry (issue #25)。

`RunnerRegistry` 持久化 `RunnerInstance` 行,提供 CRUD + 心跳更新。表设计:

    runner_instances (
        id TEXT PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        venue TEXT NOT NULL,
        account TEXT NOT NULL,
        strategy TEXT NOT NULL,
        params_override TEXT NOT NULL,    -- JSON
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        pid INTEGER,
        last_heartbeat TEXT,              -- ISO8601 UTC, nullable
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )

`PRAGMA user_version = 1` 标记本表 schema 版本(独立于 ledger 的 user_version)。
状态机校验由上层 `RunnerService` (issue #26) 负责;本 repo 只负责持久化字段。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from copy_trader.core.runner_instance import RunnerInstance, RunnerMode, RunnerStatus

__all__ = [
    "DuplicateRunnerNameError",
    "RunnerNotFoundError",
    "RunnerRegistry",
]

_REGISTRY_SCHEMA_VERSION: Final[int] = 1

# sentinel: 区分 "不传" 和 "传 None"。`update(pid=None)` 想表达"清 pid",
# 用 _UNSET 默认值才能区别于"不更新此字段"。
_UNSET: Any = object()

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS runner_instances (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    venue TEXT NOT NULL,
    account TEXT NOT NULL,
    strategy TEXT NOT NULL,
    params_override TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    pid INTEGER,
    last_heartbeat TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


class RunnerNotFoundError(KeyError):
    """按 id 或 name 找不到 RunnerInstance。"""


class DuplicateRunnerNameError(ValueError):
    """name 已存在(SQLite UNIQUE 约束触发)。"""


class RunnerRegistry:
    """SQLite-backed runner_instances repo。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False 让 FastAPI / asyncio 等多线程场景能用同一连接;
        # 单进程内调用方负责自己同步 (RunnerService 不并发改同一行)。
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    @property
    def schema_version(self) -> int:
        cur = self._conn.cursor()
        cur.execute("PRAGMA user_version")
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def _ensure_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(_CREATE_TABLE_SQL)
        cur.execute(f"PRAGMA user_version = {int(_REGISTRY_SCHEMA_VERSION)}")
        self._conn.commit()

    # --- CRUD ----------------------------------------------------------

    def create(self, runner: RunnerInstance) -> None:
        cur = self._conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO runner_instances (
                    id, name, venue, account, strategy, params_override, mode,
                    status, pid, last_heartbeat, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    runner.id,
                    runner.name,
                    runner.venue,
                    runner.account,
                    runner.strategy,
                    json.dumps(runner.params_override, sort_keys=True),
                    runner.mode,
                    runner.status,
                    runner.pid,
                    runner.last_heartbeat.isoformat() if runner.last_heartbeat else None,
                    runner.created_at.isoformat(),
                    runner.updated_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc) and "name" in str(exc):
                raise DuplicateRunnerNameError(
                    f"runner name {runner.name!r} already exists"
                ) from exc
            raise
        self._conn.commit()

    def get(self, *, id: str | None = None, name: str | None = None) -> RunnerInstance:
        if id is None and name is None:
            raise ValueError("RunnerRegistry.get requires id or name")
        cur = self._conn.cursor()
        if id is not None:
            cur.execute("SELECT * FROM runner_instances WHERE id = ?", (id,))
        else:
            cur.execute("SELECT * FROM runner_instances WHERE name = ?", (name,))
        row = cur.fetchone()
        if row is None:
            key = id if id is not None else name
            raise RunnerNotFoundError(f"runner not found: {key!r}")
        return self._row_to_instance(row)

    def list(self, *, status: RunnerStatus | None = None) -> list[RunnerInstance]:
        cur = self._conn.cursor()
        if status is None:
            cur.execute("SELECT * FROM runner_instances ORDER BY created_at ASC")
        else:
            cur.execute(
                "SELECT * FROM runner_instances WHERE status = ? ORDER BY created_at ASC",
                (status,),
            )
        return [self._row_to_instance(row) for row in cur.fetchall()]

    def update(
        self,
        id: str,
        *,
        params_override: dict[str, Any] | None = None,
        mode: RunnerMode | None = None,
        status: RunnerStatus | None = None,
        pid: int | None | Any = _UNSET,
        last_heartbeat: datetime | None | Any = _UNSET,
    ) -> RunnerInstance:
        """更新指定字段。pid / last_heartbeat 用 _UNSET sentinel 默认,允许显式传 None 清空。"""
        existing = self.get(id=id)
        new = existing.model_copy(
            update={
                "params_override": params_override
                if params_override is not None
                else existing.params_override,
                "mode": mode if mode is not None else existing.mode,
                "status": status if status is not None else existing.status,
                "pid": pid if pid is not _UNSET else existing.pid,
                "last_heartbeat": last_heartbeat
                if last_heartbeat is not _UNSET
                else existing.last_heartbeat,
                "updated_at": datetime.now().astimezone(),
            }
        )
        cur = self._conn.cursor()
        cur.execute(
            """
            UPDATE runner_instances SET
                params_override = ?, mode = ?, status = ?, pid = ?,
                last_heartbeat = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(new.params_override, sort_keys=True),
                new.mode,
                new.status,
                new.pid,
                new.last_heartbeat.isoformat() if new.last_heartbeat else None,
                new.updated_at.isoformat(),
                id,
            ),
        )
        if cur.rowcount == 0:
            raise RunnerNotFoundError(f"runner not found: {id!r}")
        self._conn.commit()
        return new

    def delete(self, id: str) -> None:
        """删除 registry 行;ledger 历史 fills **不**级联删除。"""
        cur = self._conn.cursor()
        cur.execute("DELETE FROM runner_instances WHERE id = ?", (id,))
        if cur.rowcount == 0:
            raise RunnerNotFoundError(f"runner not found: {id!r}")
        self._conn.commit()

    def heartbeat(self, id: str, ts: datetime) -> None:
        """更新 last_heartbeat 不动其他字段。"""
        cur = self._conn.cursor()
        now = datetime.now().astimezone().isoformat()
        cur.execute(
            "UPDATE runner_instances SET last_heartbeat = ?, updated_at = ? WHERE id = ?",
            (ts.isoformat(), now, id),
        )
        if cur.rowcount == 0:
            raise RunnerNotFoundError(f"runner not found: {id!r}")
        self._conn.commit()

    # --- helpers -------------------------------------------------------

    @staticmethod
    def _row_to_instance(row: sqlite3.Row) -> RunnerInstance:
        return RunnerInstance(
            id=row["id"],
            name=row["name"],
            venue=row["venue"],
            account=row["account"],
            strategy=row["strategy"],
            params_override=json.loads(row["params_override"]),
            mode=row["mode"],
            status=row["status"],
            pid=row["pid"],
            last_heartbeat=datetime.fromisoformat(row["last_heartbeat"])
            if row["last_heartbeat"]
            else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def close(self) -> None:
        self._conn.close()
