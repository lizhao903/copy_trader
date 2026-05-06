"""Dashboard smoke (issue #28)。

测试 /overview + /runners CRUD 用 fastapi.TestClient。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from copy_trader.cli.dashboard import build_app


@pytest.fixture
def client(tmp_path: Any) -> TestClient:
    home = tmp_path / "home"
    home.mkdir()
    app = build_app(home)
    return TestClient(app)


def test_overview_empty(client: TestClient) -> None:
    r = client.get("/overview")
    assert r.status_code == 200
    data = r.json()
    assert data["runner_count"] == 0
    assert data["running_count"] == 0


def test_runners_list_empty(client: TestClient) -> None:
    r = client.get("/runners")
    assert r.status_code == 200
    assert r.json() == []


def test_create_runner(client: TestClient) -> None:
    r = client.post(
        "/runners",
        json={
            "name": "alpha",
            "venue": "binance.spot",
            "account": "spot",
            "strategy": "hello",
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["name"] == "alpha"
    assert data["status"] == "stopped"
    assert data["mode"] == "dry-run"


def test_create_invalid_mode(client: TestClient) -> None:
    r = client.post(
        "/runners",
        json={
            "name": "x",
            "venue": "binance.spot",
            "account": "spot",
            "strategy": "hello",
            "mode": "bogus",
        },
    )
    assert r.status_code == 400


def test_create_duplicate_name(client: TestClient) -> None:
    body = {
        "name": "dup",
        "venue": "binance.spot",
        "account": "spot",
        "strategy": "hello",
    }
    r1 = client.post("/runners", json=body)
    assert r1.status_code == 201
    r2 = client.post("/runners", json=body)
    assert r2.status_code == 409


def test_get_runner_by_name(client: TestClient) -> None:
    client.post(
        "/runners",
        json={
            "name": "g",
            "venue": "binance.spot",
            "account": "spot",
            "strategy": "hello",
        },
    )
    r = client.get("/runners/g")
    assert r.status_code == 200
    assert r.json()["name"] == "g"


def test_get_runner_not_found(client: TestClient) -> None:
    r = client.get("/runners/ghost")
    assert r.status_code == 404


def test_start_stopped(client: TestClient) -> None:
    client.post(
        "/runners",
        json={
            "name": "s",
            "venue": "binance.spot",
            "account": "spot",
            "strategy": "hello",
        },
    )
    r = client.post("/runners/s/start")
    assert r.status_code == 200
    assert r.json()["status"] == "starting"


def test_stop_from_stopped_409(client: TestClient) -> None:
    client.post(
        "/runners",
        json={
            "name": "noop",
            "venue": "binance.spot",
            "account": "spot",
            "strategy": "hello",
        },
    )
    r = client.post("/runners/noop/stop")
    assert r.status_code == 409


def test_delete(client: TestClient) -> None:
    client.post(
        "/runners",
        json={
            "name": "d",
            "venue": "binance.spot",
            "account": "spot",
            "strategy": "hello",
        },
    )
    r = client.delete("/runners/d")
    assert r.status_code == 204
    after = client.get("/runners/d")
    assert after.status_code == 404


def test_overview_counts_after_create(client: TestClient) -> None:
    for n in ("a1", "a2", "a3"):
        client.post(
            "/runners",
            json={
                "name": n,
                "venue": "binance.spot",
                "account": "spot",
                "strategy": "hello",
            },
        )
    client.post("/runners/a1/start")
    overview = client.get("/overview").json()
    assert overview["runner_count"] == 3
    assert overview["stopped_count"] == 2
    # a1 starting (not stopped, not running)


def test_reap_no_runners(client: TestClient) -> None:
    r = client.post("/runners/reap")
    assert r.status_code == 200
    assert r.json() == []
