"""package-layout acceptance tests for issue #2.

覆盖 Issue #2 的 acceptance checklist：

- 11 个子包都能被 import 而不抛异常
- `.import-linter.ini` 存在且包含 contracts
- `uv run lint-imports` 在子进程里跑通（exit 0），保证 CI 用同一命令也通过
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_SUBPACKAGES = (
    "copy_trader.core",
    "copy_trader.exchanges",
    "copy_trader.marketdata",
    "copy_trader.strategies",
    "copy_trader.execution",
    "copy_trader.pnl",
    "copy_trader.persistence",
    "copy_trader.notify",
    "copy_trader.runners",
    "copy_trader.config",
    "copy_trader.cli",
)


def test_all_subpackages_importable() -> None:
    for name in EXPECTED_SUBPACKAGES:
        module = importlib.import_module(name)
        assert module.__doc__, f"{name} 必须含一行 docstring 描述子包职责"


def test_import_linter_config_present() -> None:
    # 文件名采用 import-linter 默认查找的 `.importlinter`（其余候选是 setup.cfg /
    # pyproject.toml）。这样 `uv run lint-imports` 不需要 --config。
    config = REPO_ROOT / ".importlinter"
    assert config.is_file(), ".importlinter 必须入库以编码单向依赖图"
    content = config.read_text(encoding="utf-8")
    assert "[importlinter]" in content
    assert "root_package = copy_trader" in content
    # 至少要锁住 core 不能反向 import 上层
    assert "copy_trader.core" in content


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv 未安装；CI/driver 跑五件套时会装")
def test_lint_imports_passes() -> None:
    """子进程跑 `uv run lint-imports`，断言 exit 0。

    这是 issue #2 acceptance 的核心断言：依赖图不被反向 import 破坏。
    用 subprocess 而非直接 import import_linter，是为了与 CI / driver
    五件套用同一条命令、同一份配置文件。
    """
    result = subprocess.run(
        ["uv", "run", "lint-imports"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "lint-imports 失败，依赖图被反向 import 破坏：\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
