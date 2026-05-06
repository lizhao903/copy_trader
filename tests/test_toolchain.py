"""tooling-uv acceptance smoke tests for issue #1.

覆盖 Issue #1 的 acceptance checklist：
- Python 解释器 ≥ 3.12（来自 [project].requires-python）
- 仓库不含替代工具的 lock / setup 产物
- 包可被 import（uv 已安装可分发包）
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_LEGACY_FILES = (
    "requirements.txt",
    "requirements-dev.txt",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "pdm.lock",
    "setup.py",
    "setup.cfg",
    "environment.yml",
)


def test_python_version_meets_requires_python() -> None:
    assert sys.version_info >= (3, 12), (
        f"copy-trader requires Python >= 3.12; running {sys.version_info}"
    )


def test_no_replacement_tool_artifacts() -> None:
    offenders = [name for name in FORBIDDEN_LEGACY_FILES if (REPO_ROOT / name).exists()]
    assert not offenders, (
        "tooling-uv spec forbids replacement-tool artifacts; found: "
        + ", ".join(offenders)
    )


def test_pyproject_pins_uv_required_version() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    required_version = pyproject.get("tool", {}).get("uv", {}).get("required-version")
    assert required_version, "[tool.uv].required-version 必须钉死 uv minor 版本区间"


def test_pyproject_declares_cli_entry() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject.get("project", {}).get("scripts", {})
    assert scripts.get("copy-trader") == "copy_trader.cli.main:app"


def test_uv_lock_committed() -> None:
    assert (REPO_ROOT / "uv.lock").is_file(), "uv.lock 必须提交入库"


def test_package_importable() -> None:
    import copy_trader

    assert hasattr(copy_trader, "__version__")
