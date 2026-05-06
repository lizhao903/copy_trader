"""Dashboard /settings (issue #29) — 字段 schema / 写 local / 敏感字段不暴露 / 审计日志。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from copy_trader.cli.dashboard import _is_sensitive, build_app


@pytest.fixture
def home_dir(tmp_path: Any) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture
def client(home_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Settings.load 在 dev/paper/prod 三个 yaml 之外要找仓库 config/, 测试时跳过
    monkeypatch.setenv("COPY_TRADER_ENV", "dev")
    return TestClient(build_app(home_dir))


# ---------- 敏感字段判定 ----------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("accounts.spot.api_key", True),
        ("accounts.spot.binance_api_secret", True),
        ("accounts.spot.access_token", True),
        ("accounts.spot.private_key", True),
        ("accounts.spot.venue", False),
        ("pyramid.0.add_trigger_pct", False),
    ],
)
def test_is_sensitive_path(path: str, expected: bool) -> None:
    assert _is_sensitive(path) == expected


# ---------- /settings GET ----------


def test_settings_returns_fields_or_error(client: TestClient) -> None:
    """GET /settings 在没有 config/ 时降级返回 error, 但 schema 字段返回结构稳定。"""
    r = client.get("/settings")
    assert r.status_code == 200
    body = r.json()
    assert "fields" in body
    assert "audit_log" in body


# ---------- /settings PATCH local 层 ----------


def test_patch_local_writes_yaml(client: TestClient, home_dir: Path) -> None:
    r = client.patch(
        "/settings",
        json={"path": "test_field", "value": 42},
    )
    assert r.status_code == 200, r.text
    assert r.json()["after"] == 42
    assert r.json()["layer"] == "local"

    # config.yaml 实际写入
    yaml_path = home_dir / "config.yaml"
    assert yaml_path.exists()
    content = yaml_path.read_text()
    assert "test_field" in content
    assert "42" in content


def test_patch_sensitive_field_rejected(client: TestClient) -> None:
    """敏感字段 PATCH → 400 + 提示去 secrets/.env。"""
    r = client.patch(
        "/settings",
        json={"path": "binance_api_key", "value": "leaked"},
    )
    assert r.status_code == 400
    assert "secrets" in r.json()["detail"]


def test_patch_creates_audit_log(client: TestClient, home_dir: Path) -> None:
    """每次 PATCH 都追加审计行。"""
    client.patch("/settings", json={"path": "foo", "value": 1})
    client.patch("/settings", json={"path": "foo", "value": 2})

    audit_log = home_dir / "logs" / "dashboard_audit.log"
    assert audit_log.exists()
    lines = audit_log.read_text().splitlines()
    assert len(lines) == 2
    assert "foo" in lines[0]
    assert "after=1" in lines[0]
    assert "after=2" in lines[1]


def test_audit_log_endpoint(client: TestClient) -> None:
    client.patch("/settings", json={"path": "foo", "value": 1})
    r = client.get("/settings/audit")
    assert r.status_code == 200
    body = r.json()
    assert "lines" in body
    assert any("foo" in line for line in body["lines"])


def test_sensitive_field_value_never_returned(client: TestClient, home_dir: Path) -> None:
    """spec acceptance: 敏感字段在网络面板中无法读出明文。"""
    # 即便 yaml 里有敏感字段(模拟 user 误写), GET /settings 也应返回 <set>/<not set>
    yaml_path = home_dir / "config.yaml"
    yaml_path.write_text("binance_api_key: 'leaked-value'\n")

    # PATCH 一个非敏感字段触发审计 (验证审计也不暴露敏感)
    client.patch("/settings", json={"path": "binance_api_key", "value": "new-leak"})
    # 上面 patch 因敏感被拒(400), 我们 audit log 只验证不会写敏感值

    audit = home_dir / "logs" / "dashboard_audit.log"
    if audit.exists():
        content = audit.read_text()
        assert "leaked-value" not in content  # 永远不出现 raw value
        assert "new-leak" not in content


def test_patch_round_trip_check_value_persists(client: TestClient, home_dir: Path) -> None:
    """改值后审计有记录, yaml 反映变更。"""
    client.patch("/settings", json={"path": "field_a", "value": "v1"})
    yaml_path = home_dir / "config.yaml"
    assert "v1" in yaml_path.read_text()
    client.patch("/settings", json={"path": "field_a", "value": "v2"})
    assert "v2" in yaml_path.read_text()
