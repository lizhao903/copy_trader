"""FastAPI Dashboard 基线 (issue #28) + settings 中心 (issue #29)。

dashboard: /overview + /runners CRUD + /settings (字段 schema + 写入 + 审计)
直接调 RunnerService 与 Settings (与 CLI 等价)。

启动:
    uv run copy-trader dashboard --port 15000
"""

from __future__ import annotations

import datetime as _datetime
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel, Field

from copy_trader.config import Settings
from copy_trader.runners import (
    DuplicateRunnerNameError,
    InvalidStateTransition,
    RunnerNotFoundError,
    RunnerRegistry,
    RunnerService,
)

__all__ = ["build_app"]


_SENSITIVE_RE = re.compile(r"_(key|secret|token|private_key)$", re.IGNORECASE)


def _is_sensitive(path: str) -> bool:
    """字段路径包含敏感后缀 (与 config-overlay spec / cli doctor 一致)。"""
    return bool(_SENSITIVE_RE.search(path))


class SettingsPatchRequest(BaseModel):
    path: str = Field(..., description="字段路径,如 'pyramid[0].add_trigger_pct'")
    value: Any = Field(..., description="新值")


class CreateRunnerRequest(BaseModel):
    name: str = Field(..., description="唯一名")
    venue: str
    account: str
    strategy: str
    mode: str = "dry-run"
    params_override: dict[str, Any] = Field(default_factory=dict)


class RunnerResponse(BaseModel):
    id: str
    name: str
    venue: str
    account: str
    strategy: str
    mode: str
    status: str
    pid: int | None
    last_heartbeat: datetime | None
    params_override: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class OverviewResponse(BaseModel):
    runner_count: int
    running_count: int
    stopped_count: int
    errored_count: int


def build_app(home: Path) -> FastAPI:
    """构造 FastAPI app, 绑定指定 home 下的 RunnerRegistry。"""
    app = FastAPI(
        title="copy-trader dashboard",
        version="0.1.0",
        description="Issue #28: 最小基线 /overview + /runners CRUD",
    )

    db_dir = home / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    pid_dir = home / "pids"
    pid_dir.mkdir(parents=True, exist_ok=True)

    registry = RunnerRegistry(db_dir / "runner_registry.db")
    service = RunnerService(registry, pid_dir=pid_dir)

    # --------------------------------------------------------- routes

    @app.get("/overview", response_model=OverviewResponse)
    def overview() -> OverviewResponse:
        """概览: runner 计数按状态聚合 (PnL 详细数据 issue #29 接 PnlEngine)。"""
        all_runners = service.list_all()
        return OverviewResponse(
            runner_count=len(all_runners),
            running_count=sum(1 for r in all_runners if r.status == "running"),
            stopped_count=sum(1 for r in all_runners if r.status == "stopped"),
            errored_count=sum(1 for r in all_runners if r.status == "errored"),
        )

    @app.get("/runners", response_model=list[RunnerResponse])
    def list_runners(status: str | None = None) -> list[RunnerResponse]:
        runners = service.list_all(status=status)  # type: ignore[arg-type]
        return [_to_response(r) for r in runners]

    @app.post("/runners", response_model=RunnerResponse, status_code=201)
    def create_runner(req: CreateRunnerRequest) -> RunnerResponse:
        if req.mode not in ("live", "paper", "dry-run", "backtest"):
            raise HTTPException(status_code=400, detail=f"invalid mode: {req.mode}")
        try:
            runner = service.create(
                name=req.name,
                venue=req.venue,
                account=req.account,
                strategy=req.strategy,
                mode=req.mode,  # type: ignore[arg-type]
                params_override=req.params_override,
            )
        except DuplicateRunnerNameError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _to_response(runner)

    @app.get("/runners/{id_or_name}", response_model=RunnerResponse)
    def get_runner(id_or_name: str) -> RunnerResponse:
        try:
            runner = service._resolve(id_or_name)
        except RunnerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _to_response(runner)

    @app.delete("/runners/{id_or_name}", status_code=204)
    def delete_runner(id_or_name: str) -> None:
        try:
            service.delete(id_or_name)
        except RunnerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/runners/{id_or_name}/start", response_model=RunnerResponse)
    def start_runner(id_or_name: str) -> RunnerResponse:
        try:
            runner = service.start(id_or_name)
        except RunnerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InvalidStateTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _to_response(runner)

    @app.post("/runners/{id_or_name}/stop", response_model=RunnerResponse)
    def stop_runner(id_or_name: str) -> RunnerResponse:
        try:
            runner = service.stop(id_or_name)
        except RunnerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InvalidStateTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _to_response(runner)

    @app.post("/runners/reap", response_model=list[RunnerResponse])
    def reap_runners() -> list[RunnerResponse]:
        return [_to_response(r) for r in service.reap()]

    # ---------------------------------------------------------- settings (issue #29)

    audit_log = home / "logs" / "dashboard_audit.log"
    audit_log.parent.mkdir(parents=True, exist_ok=True)

    def _audit(action: str, path: str, before: Any, after: Any) -> None:
        """追加审计行 (敏感字段值掩码)。"""
        before_safe = "<redacted>" if _is_sensitive(path) else repr(before)
        after_safe = "<redacted>" if _is_sensitive(path) else repr(after)
        ts = _datetime.datetime.now(_datetime.UTC).isoformat()
        with audit_log.open("a", encoding="utf-8") as f:
            f.write(f"{ts}\t{action}\t{path}\tbefore={before_safe}\tafter={after_safe}\n")

    @app.get("/settings")
    def get_settings_schema() -> dict[str, Any]:
        """返回字段 schema + LayerScope + 当前值 (敏感字段掩码)。

        加载 Settings 失败时返回空 fields 列表 + error 字段, 让前端能展示降级
        信息(spec config-overlay 不要求严格成功)。
        """
        # 自动探测 config_dir: 仓库根的 config/ 或 home 上一级
        candidates = [home / "config", home.parent / "config", Path.cwd() / "config"]
        cfg_path = next((p for p in candidates if p.is_dir()), None)
        try:
            if cfg_path is None:
                raise FileNotFoundError(f"config/ 未找到 (looked at {candidates})")
            settings = Settings.load(config_dir=cfg_path, env="dev")
        except Exception as exc:  # noqa: BLE001
            return {
                "fields": [],
                "error": str(exc),
                "audit_log": str(audit_log),
            }

        layer_map = settings.field_layer_map()
        dump = settings.model_dump(mode="json")

        def _walk(prefix: str, obj: Any) -> dict[str, Any]:
            flat: dict[str, Any] = {}
            if isinstance(obj, dict):
                for k, v in obj.items():
                    flat.update(_walk(f"{prefix}.{k}" if prefix else k, v))
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    flat.update(_walk(f"{prefix}[{i}]", v))
            else:
                flat[prefix] = obj
            return flat

        leaves = _walk("", dump)
        fields: list[dict[str, Any]] = []
        for path, value in sorted(leaves.items()):
            sensitive = _is_sensitive(path)
            fields.append(
                {
                    "path": path,
                    "value": "<set>"
                    if sensitive and value not in (None, "")
                    else ("<not set>" if sensitive else value),
                    "layer": layer_map.get(path, "base"),
                    "sensitive": sensitive,
                    "writable": not sensitive,
                }
            )
        return {"fields": fields, "audit_log": str(audit_log)}

    @app.patch("/settings")
    def patch_settings(req: SettingsPatchRequest = Body(...)) -> dict[str, Any]:  # noqa: B008
        """写入 local 层 ($COPY_TRADER_HOME/config.yaml)。

        - 敏感字段拒写 (引导去 secrets/.env)
        - base/env 层只在 yaml 备注中提示 "走 OpenSpec change + draft PR"
          (gh pr create 集成留 follow-up)
        - 写入后追加审计行
        """
        if _is_sensitive(req.path):
            raise HTTPException(
                status_code=400,
                detail=f"敏感字段 {req.path!r} 不能通过 dashboard 写, 编辑 secrets/.env",
            )

        local_yaml = home / "config.yaml"
        existing: dict[str, Any] = {}
        if local_yaml.exists():
            existing = yaml.safe_load(local_yaml.read_text()) or {}

        # 简化: 用扁平 path 做 dotted-path 写入(不解析 [N] 数组索引,接受 'pyramid' 整段覆盖)
        before = existing.get(req.path)
        existing[req.path] = req.value
        local_yaml.write_text(yaml.safe_dump(existing, allow_unicode=True))
        _audit("patch", req.path, before, req.value)
        return {"path": req.path, "before": before, "after": req.value, "layer": "local"}

    @app.get("/settings/audit")
    def read_audit_log() -> dict[str, Any]:
        """读最近 100 行审计日志。"""
        if not audit_log.exists():
            return {"lines": []}
        lines = audit_log.read_text(encoding="utf-8").splitlines()[-100:]
        return {"lines": lines, "path": str(audit_log)}

    return app


def _to_response(runner: Any) -> RunnerResponse:
    return RunnerResponse(
        id=runner.id,
        name=runner.name,
        venue=runner.venue,
        account=runner.account,
        strategy=runner.strategy,
        mode=runner.mode,
        status=runner.status,
        pid=runner.pid,
        last_heartbeat=runner.last_heartbeat,
        params_override=runner.params_override,
        created_at=runner.created_at,
        updated_at=runner.updated_at,
    )
