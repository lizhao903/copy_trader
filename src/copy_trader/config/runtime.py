"""运行时根目录解析与 lock 文件校验（issue #3 / runtime-isolation spec）。

本模块实现 spec `runtime-isolation` 在启动期必须做的事：

1. 从 `COPY_TRADER_ENV`（必填，取值 ∈ {dev, paper, prod}）和 `COPY_TRADER_HOME`
   （可选，按 env 给默认）解析运行时根目录；缺 ENV 时 fail-fast。
2. 在 `$COPY_TRADER_HOME/{state,logs,pids,db,secrets}/` 下创建子目录（0700）。
3. 首次启动时在 `state/.machine_id` 写入 UUID v4，后续启动复用。
4. 写入 `state/.runtime_lock.json`，记录 `(env_tag, machine_id, schema_version,
   pid, started_at)`；如果旧锁的 `env_tag` 或 `machine_id` 与当前进程不一致，
   立刻抛对应异常并在错误消息中列出两侧值。

公共 API（供 issue #5 CLI doctor 调用）：

- `resolve_runtime(...) -> RuntimeContext`：解析并落锁；任何异常都是用户必须
  看到的 fail-fast 信号。
- `read_runtime_lock(home) -> dict | None`：只读，doctor 在锁不一致时也能用。
- `RuntimeContext` / `RuntimeError`、`MissingEnvError`、`InvalidEnvError`、
  `CrossEnvironmentError`、`CrossMachineError`：分别对应 spec 列出的 fail
  场景，方便上层精准捕获或人工排查。
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

# spec D3: env 取值与每个 env 的默认 home。
SUPPORTED_ENVS: Final[tuple[str, ...]] = ("dev", "paper", "prod")

# 运行时锁 schema 版本。后续若 lock 字段结构变更（加列、改语义）再 bump，
# 旧锁可由 doctor 工具迁移；本号同时落到 ledger 行（见 D4），保持一致。
RUNTIME_LOCK_SCHEMA_VERSION: Final[int] = 1

# `$COPY_TRADER_HOME` 下必须存在的子目录及其权限。spec 要求 0700。
_RUNTIME_SUBDIRS: Final[tuple[str, ...]] = ("state", "logs", "pids", "db", "secrets")
_DIR_MODE: Final[int] = 0o700

_MACHINE_ID_FILENAME: Final[str] = ".machine_id"
_RUNTIME_LOCK_FILENAME: Final[str] = ".runtime_lock.json"

_ENV_VAR_ENV: Final[str] = "COPY_TRADER_ENV"
_ENV_VAR_HOME: Final[str] = "COPY_TRADER_HOME"


class RuntimeBootstrapError(RuntimeError):
    """runtime bootstrap 阶段的所有可预期失败的基类。

    使用自定义基类（而非直接 raise `ValueError`）让 CLI 入口可以集中捕获
    这一族异常并映射到统一的退出码 / 错误展示，业务模块也能精确选择
    catch 子类（如 doctor 想跳过 cross-env 异常但仍报告其他失败）。
    """


class MissingEnvError(RuntimeBootstrapError):
    """`COPY_TRADER_ENV` 未设置；spec 要求加载任何业务模块前 fail-fast。"""


class InvalidEnvError(RuntimeBootstrapError):
    """`COPY_TRADER_ENV` 值不在 `SUPPORTED_ENVS` 集合内。"""


class CrossEnvironmentError(RuntimeBootstrapError):
    """旧锁记录的 env_tag 与本次启动不一致（D3 跨环境共享根目录拦截）。"""


class CrossMachineError(RuntimeBootstrapError):
    """旧锁记录的 machine_id 与本机不一致（D3 跨机器复制 state 拦截）。"""


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """`resolve_runtime()` 的返回值；冻结 dataclass 避免下游误改。

    Attributes:
        env_tag: 解析后的 env，∈ `SUPPORTED_ENVS`。
        home: 运行时根目录绝对路径（已 mkdir）。
        machine_id: 本机 `state/.machine_id` 的 UUID 字符串。
        schema_version: 当前 runtime lock schema 版本。
        pid: 当前进程 PID（写入 lock 文件中的同名字段）。
        started_at: 当前进程启动时间（ISO 8601 UTC）。
        home_source: home 解析最终来源；spec D3 要求启动日志记录该信息。
    """

    env_tag: str
    home: Path
    machine_id: str
    schema_version: int
    pid: int
    started_at: str
    home_source: str  # "cli" | "env" | "default"


def _default_home_for(env_tag: str) -> Path:
    """spec D3: 按 env 给默认 home 路径。

    dev/paper 默认落仓库内 `./var/<env>/`（开发机便于一台机器多 env 并存），
    prod 默认走 `/var/lib/copy_trader/`（与 systemd unit 习惯目录一致）。
    """
    if env_tag == "dev":
        return Path("./var/dev").resolve()
    if env_tag == "paper":
        return Path("./var/paper").resolve()
    if env_tag == "prod":
        return Path("/var/lib/copy_trader")
    # 不可达：调用方已用 `_resolve_env_tag()` 收紧到 SUPPORTED_ENVS。
    raise InvalidEnvError(f"unknown env_tag={env_tag!r}; supported={SUPPORTED_ENVS}")


def _resolve_env_tag(env: str | None) -> str:
    """解析 env 参数；缺则读 `COPY_TRADER_ENV`，仍缺则 fail-fast。"""
    raw = env if env is not None else os.environ.get(_ENV_VAR_ENV)
    if not raw:
        raise MissingEnvError(
            f"{_ENV_VAR_ENV} 未设置；必须为 {SUPPORTED_ENVS} 之一。 示例：export {_ENV_VAR_ENV}=dev"
        )
    if raw not in SUPPORTED_ENVS:
        raise InvalidEnvError(
            f"{_ENV_VAR_ENV}={raw!r} 不在受支持取值 {SUPPORTED_ENVS} 内。"
            f" 示例：export {_ENV_VAR_ENV}=dev"
        )
    return raw


def _resolve_home(env_tag: str, home_cli: str | os.PathLike[str] | None) -> tuple[Path, str]:
    """按优先级 CLI > env > 默认 解析 home，返回 (绝对路径, 来源标签)。"""
    if home_cli is not None:
        return Path(home_cli).expanduser().resolve(), "cli"

    home_env = os.environ.get(_ENV_VAR_HOME)
    if home_env:
        return Path(home_env).expanduser().resolve(), "env"

    return _default_home_for(env_tag), "default"


def _ensure_subdirs(home: Path) -> None:
    """确保 home 与全部子目录存在且权限 0700。

    spec 中"以 0700 权限创建缺失目录"是启动期幂等动作：既能首次启动建好，
    也能在用户手动改坏权限后修复。home 自身也用同样权限，避免管子里的
    内容被旁路读取。
    """
    home.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    # home 已存在时 mkdir(exist_ok=True) 不改 mode，所以再显式 chmod 一次。
    os.chmod(home, _DIR_MODE)
    for sub in _RUNTIME_SUBDIRS:
        path = home / sub
        path.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        os.chmod(path, _DIR_MODE)


def _load_or_create_machine_id(home: Path) -> str:
    """读 / 写 `state/.machine_id`，首次启动生成 UUID v4。

    一旦写入就不再换；即便用户后续改 hostname 也不会影响 lock 比对。
    spec 中跨机器拦截依赖此文件不被复制——这里我们只负责生成 + 读取，
    不去检测复制（rsync 行为由 spec 中"复制 state 整体"那条 scenario 兜住）。
    """
    machine_id_path = home / "state" / _MACHINE_ID_FILENAME
    if machine_id_path.exists():
        existing = machine_id_path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
        # 空文件（异常情况）：当作首次启动重新生成。
    new_id = str(uuid.uuid4())
    machine_id_path.write_text(new_id + "\n", encoding="utf-8")
    os.chmod(machine_id_path, 0o600)
    return new_id


def read_runtime_lock(home: Path) -> dict[str, object] | None:
    """读取 `$home/state/.runtime_lock.json`；不存在或损坏返回 None。

    供 `copy-trader doctor` 在不触发 fail-fast 的前提下展示锁状态。
    损坏（非合法 JSON）当作 None；上层若想进一步分析，可以直接读文件。
    """
    lock_path = home / "state" / _RUNTIME_LOCK_FILENAME
    if not lock_path.exists():
        return None
    try:
        loaded = json.loads(lock_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded


def _write_runtime_lock(
    *,
    home: Path,
    env_tag: str,
    machine_id: str,
    pid: int,
    started_at: str,
) -> None:
    """落锁文件；与 spec D3 中字段一致。"""
    lock_path = home / "state" / _RUNTIME_LOCK_FILENAME
    payload = {
        "env_tag": env_tag,
        "machine_id": machine_id,
        "schema_version": RUNTIME_LOCK_SCHEMA_VERSION,
        "pid": pid,
        "started_at": started_at,
    }
    lock_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(lock_path, 0o600)


def _enforce_lock_consistency(
    *,
    home: Path,
    env_tag: str,
    machine_id: str,
) -> None:
    """旧锁存在则比对 env_tag / machine_id；不一致直接抛错（含两侧值）。

    spec 强调错误信息必须列出两侧值，便于运维迅速定位是错配 env 还是
    误把 state 目录 rsync 到其他机器。
    """
    existing = read_runtime_lock(home)
    if existing is None:
        return

    existing_env = existing.get("env_tag")
    existing_machine = existing.get("machine_id")

    if existing_env != env_tag:
        raise CrossEnvironmentError(
            "runtime lock env_tag 不匹配：拒绝跨环境共享 $COPY_TRADER_HOME。\n"
            f"  home={home}\n"
            f"  锁记录 env_tag={existing_env!r}\n"
            f"  当前进程 env_tag={env_tag!r}\n"
            "修复：把 COPY_TRADER_ENV 改回锁里的值，或换一个 COPY_TRADER_HOME。"
        )

    if existing_machine != machine_id:
        raise CrossMachineError(
            "runtime lock machine_id 不匹配：检测到跨机器复制 state 目录。\n"
            f"  home={home}\n"
            f"  锁记录 machine_id={existing_machine!r}\n"
            f"  本机 machine_id={machine_id!r}\n"
            "修复：清空 state/ 重新初始化，"
            "或显式 --reset-machine-id 覆盖（仅在确认本机就是新机器时使用）。"
        )


def resolve_runtime(
    *,
    env: str | None = None,
    home: str | os.PathLike[str] | None = None,
) -> RuntimeContext:
    """解析运行时上下文并落锁；所有失败均通过抛 `RuntimeBootstrapError` 子类传出。

    Args:
        env: CLI 显式传入的 env_tag；不传则读 `COPY_TRADER_ENV`。
        home: CLI 显式传入的 home；不传则按 env > 默认 顺序解析。

    Returns:
        `RuntimeContext`，含解析结果与启动时间戳。

    Raises:
        MissingEnvError: 缺 `COPY_TRADER_ENV`。
        InvalidEnvError: env 取值不在白名单。
        CrossEnvironmentError: 旧锁 env_tag 与本次不一致。
        CrossMachineError: 旧锁 machine_id 与本机不一致。
    """
    env_tag = _resolve_env_tag(env)
    home_path, home_source = _resolve_home(env_tag, home)
    _ensure_subdirs(home_path)

    machine_id = _load_or_create_machine_id(home_path)
    _enforce_lock_consistency(home=home_path, env_tag=env_tag, machine_id=machine_id)

    pid = os.getpid()
    started_at = datetime.now(UTC).isoformat()
    _write_runtime_lock(
        home=home_path,
        env_tag=env_tag,
        machine_id=machine_id,
        pid=pid,
        started_at=started_at,
    )

    return RuntimeContext(
        env_tag=env_tag,
        home=home_path,
        machine_id=machine_id,
        schema_version=RUNTIME_LOCK_SCHEMA_VERSION,
        pid=pid,
        started_at=started_at,
        home_source=home_source,
    )


__all__ = [
    "RUNTIME_LOCK_SCHEMA_VERSION",
    "SUPPORTED_ENVS",
    "CrossEnvironmentError",
    "CrossMachineError",
    "InvalidEnvError",
    "MissingEnvError",
    "RuntimeBootstrapError",
    "RuntimeContext",
    "read_runtime_lock",
    "resolve_runtime",
]
