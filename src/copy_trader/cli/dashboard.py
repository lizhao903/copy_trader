"""FastAPI Dashboard 基线 (issue #28)。

最小可用 dashboard: 暴露 /overview + /runners GET/POST/.../delete,
直接调 RunnerService (与 CLI 等价)。

启动:
    uv run copy-trader dashboard --port 15000

(本 issue 提供 FastAPI app + RunnerService 集成; 完整 HTML 模板 / PnL
计算 / settings 中心由 issue #29 / m4 后续补)。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from copy_trader.runners import (
    DuplicateRunnerNameError,
    InvalidStateTransition,
    RunnerNotFoundError,
    RunnerRegistry,
    RunnerService,
)

__all__ = ["build_app"]


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
