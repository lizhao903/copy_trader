"""Issue #5 acceptance：``copy-trader doctor`` 退出码 / 锁不一致告警 / 敏感字段掩码。

3 个验收场景（与 issue 验收清单对齐）：

1. dev env + tmp home → 退出码 0，输出含 home / env_tag / machine_id / 各子目录 OK
   + ledger 占位 + 配置来源中 ``env_tag=dev``。
2. 锁文件 ``env_tag`` 与当前进程不一致 → doctor 不 fail-fast，退出码 0，stdout
   含告警关键字（``mismatch``）+ ``env_tag`` 仍来自当前进程。
3. 敏感字段在 doctor 输出中显示 ``<redacted>``：
   - 通过 ``cli.main._is_sensitive_path`` 单测覆盖路径判定逻辑
     （命名后缀 _key / _secret / _token / _private_key，含 list 索引段）
   - 通过 ``_print_config_sources`` 的格式化分支单测覆盖 ``<redacted>`` 实际渲染

所有测试用 typer ``CliRunner`` 调用 app；不开 subprocess，不读用户真实 ``$HOME``，
不写真实凭证。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from copy_trader.cli.main import _is_sensitive_path, _print_config_sources, app


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """每个 test 都从干净 env 开始，避免外部 shell / 同 session 污染。"""
    monkeypatch.delenv("COPY_TRADER_ENV", raising=False)
    monkeypatch.delenv("COPY_TRADER_HOME", raising=False)
    monkeypatch.delenv("COPY_TRADER_CONFIG_DIR", raising=False)
    yield


@pytest.fixture
def repo_config_dir() -> Path:
    """指向仓库根 ``config/`` 的固定路径。

    doctor 的自动探测从 CWD 向上找 ``config/base.yaml``；测试场景里 pytest
    在仓库根跑没问题，但用 fixture 显式拿到便于在某些场景里走 ``--config-dir``。
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config" / "base.yaml"
        if candidate.is_file():
            return candidate.parent
    raise RuntimeError("找不到仓库根 config/ 目录")


# --------------------------------------------------------------------------- #
# Scenario 1：dev env + tmp home → 退出码 0 + 输出含运行时关键字
# --------------------------------------------------------------------------- #


def test_doctor_dev_env_tmp_home_exit_zero_with_runtime_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo_config_dir: Path,
) -> None:
    """spec runtime-isolation：dev env + tmp_path home → 完整输出 + 退出码 0。"""
    monkeypatch.setenv("COPY_TRADER_ENV", "dev")
    monkeypatch.setenv("COPY_TRADER_HOME", str(tmp_path))

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["doctor", "--config-dir", str(repo_config_dir)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout

    out = result.stdout
    # 必含字段（运行时根 + env + machine_id + ledger 占位）。
    assert "home          :" in out
    assert "env_tag       : dev" in out
    assert "machine_id    :" in out
    assert "schema_version: 1" in out
    assert "<not yet implemented (issue #8)>" in out

    # 5 个子目录可写性都打印为 OK（tmp_path 由 resolve_runtime 自动建出来）。
    for sub in ("state", "logs", "pids", "db", "secrets"):
        assert f"{sub:<8}: OK" in out, f"子目录 {sub} 应可写"

    # 配置来源段必须出现，且 env 字段层标注为 envvar / cli。
    assert "[config sources" in out
    assert "env_tag" in out
    # accounts.spot.venue 一定来自 base 层。
    assert "accounts.spot.venue" in out

    # spec 强调 doctor MUST 不修改任何状态——但 resolve_runtime 自身会写锁，
    # 这里我们只断言：state/.runtime_lock.json 写下来并指向 dev。
    lock_path = tmp_path / "state" / ".runtime_lock.json"
    assert lock_path.is_file()
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["env_tag"] == "dev"


# --------------------------------------------------------------------------- #
# Scenario 2：锁不一致 → doctor 退出 0 + 告警含 mismatch
# --------------------------------------------------------------------------- #


def test_doctor_does_not_fail_fast_on_lock_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo_config_dir: Path,
) -> None:
    """spec runtime-isolation："doctor 在锁不一致时仍可读" scenario。

    先用 paper env 建好锁；然后切到 dev env 启动 doctor 指向同一个 home，应：
    - 退出码 0（不 fail-fast）
    - stdout 含告警关键字 ``mismatch``（grep 友好）
    - env_tag 仍显示当前进程的值（dev），machine_id 显示锁里那个（doctor 不改）
    """
    # 先建一个 paper 锁（machine_id 由 paper 进程生成）。
    monkeypatch.setenv("COPY_TRADER_ENV", "paper")
    monkeypatch.setenv("COPY_TRADER_HOME", str(tmp_path))
    runner = CliRunner()
    setup_result = runner.invoke(
        app,
        ["doctor", "--config-dir", str(repo_config_dir)],
        catch_exceptions=False,
    )
    assert setup_result.exit_code == 0, setup_result.stdout
    paper_lock = json.loads((tmp_path / "state" / ".runtime_lock.json").read_text(encoding="utf-8"))
    assert paper_lock["env_tag"] == "paper"
    paper_machine_id = paper_lock["machine_id"]

    # 现在切到 dev 同 home；doctor 应不 fail-fast，告警里含 mismatch。
    monkeypatch.setenv("COPY_TRADER_ENV", "dev")
    result = runner.invoke(
        app,
        ["doctor", "--config-dir", str(repo_config_dir)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout

    out = result.stdout
    assert "env_tag       : dev" in out, "doctor 应展示当前进程的 env_tag"
    assert paper_machine_id in out, "machine_id 应来自锁里那个（doctor 只读不重生成）"
    assert "[warnings]" in out
    assert "mismatch" in out, f"应包含 mismatch 关键字便于 grep；实际：\n{out}"

    # 锁文件不应被 doctor 覆盖（spec："MUST 不修改任何状态"——这里 resolve_runtime
    # 在 raise 前已经走完 _enforce_lock_consistency，未到 _write_runtime_lock）。
    lock_after = json.loads((tmp_path / "state" / ".runtime_lock.json").read_text(encoding="utf-8"))
    assert lock_after["env_tag"] == "paper", "锁应保留 paper 不被 doctor 改写"


# --------------------------------------------------------------------------- #
# Scenario 3：敏感字段值显示 <redacted>
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path,expected",
    [
        # 命中：以 _key / _secret / _token / _private_key 结尾
        ("accounts.spot.api_key", True),
        ("accounts.spot.binance_spot_api_key", True),
        ("notify.slack_token", True),
        ("foo[0].something_secret", True),
        ("hyperliquid.private_key", True),
        # 不应误伤：非敏感后缀字段
        ("accounts.spot.venue", False),
        ("accounts.spot.credentials_alias", False),
        ("pyramid[0].add_trigger_pct", False),
        ("env", False),
        # 边界：含敏感字段名作为前缀但不在末段（key_id 不算 _key）
        ("foo.key_id", False),
    ],
)
def test_is_sensitive_path_matches_suffixes(path: str, expected: bool) -> None:
    """敏感后缀 _key/_secret/_token/_private_key 必须命中、非敏感字段不应误伤。"""
    assert _is_sensitive_path(path) is expected


def test_doctor_redacts_sensitive_field_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo_config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``_print_config_sources`` 在拿到含敏感字段的 settings 后，值必须显示为 <redacted>。

    我们没法在 yaml 里塞 ``binance_api_key``（spec config-overlay 第 3 个
    Requirement 要求 yaml 中此类字段触发 ValidationError），所以直接构造一个
    含敏感叶子的 stub Settings：用 monkeypatch 替换 ``Settings.load`` 让它返回
    一个 dummy 对象，dummy 的 ``model_dump`` 里有 ``binance.api_key`` 叶子，
    ``field_layer_map`` 标记为 envvar 层。
    """
    monkeypatch.setenv("COPY_TRADER_ENV", "dev")
    monkeypatch.setenv("COPY_TRADER_HOME", str(tmp_path))

    class _StubSettings:
        @staticmethod
        def model_dump(*, mode: str = "python") -> dict[str, object]:  # noqa: ARG004
            return {
                "env": "dev",
                "binance": {"api_key": "AK_super_secret_xxx"},
                "notify": {"slack_token": "xoxb-fake"},
                "accounts": {"spot": {"venue": "binance_spot"}},
            }

        def field_layer_map(self) -> dict[str, str]:
            return {
                "env": "envvar",
                "binance.api_key": "envvar",
                "notify.slack_token": "envvar",
                "accounts.spot.venue": "base",
            }

    with patch("copy_trader.cli.main.Settings") as mock_settings:
        mock_settings.load.return_value = _StubSettings()
        warnings = _print_config_sources(
            env_tag="dev",
            home=tmp_path,
            config_dir=repo_config_dir,
        )
    captured = capsys.readouterr().out

    # 敏感字段值被 redact，明文 ``AK_super_secret_xxx`` / ``xoxb-fake`` 不应出现。
    assert "<redacted>" in captured
    assert "AK_super_secret_xxx" not in captured
    assert "xoxb-fake" not in captured
    # 敏感字段路径仍打印（只是值掩码）。
    assert "binance.api_key" in captured
    assert "notify.slack_token" in captured
    # 非敏感字段值正常显示。
    assert "binance_spot" in captured
    assert warnings == [], f"无 settings 异常时不应回填 warnings，实际：{warnings}"
