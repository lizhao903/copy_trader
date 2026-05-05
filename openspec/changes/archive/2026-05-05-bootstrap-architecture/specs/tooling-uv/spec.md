## ADDED Requirements

### Requirement: `uv` is the only supported Python toolchain

仓库 MUST 使用 [`uv`](https://docs.astral.sh/uv/) 管理 Python 解释器版本、虚拟环境、依赖与脚本运行。`pyproject.toml` 是项目元数据与依赖的唯一来源；`uv.lock` MUST 提交入 git 作为唯一锁文件。仓库 MUST NOT 含有 `requirements.txt`、`requirements-dev.txt`、`Pipfile`、`Pipfile.lock`、`poetry.lock`、`pdm.lock`、`setup.py`、`setup.cfg`、`environment.yml` 之类替代工具的产物。

#### Scenario: onboard 路径只用 uv

- **WHEN** 新开发者 clone 仓库后按 README 操作
- **THEN** 仅需 `curl -LsSf https://astral.sh/uv/install.sh | sh && uv sync` 两步即可装齐依赖与虚拟环境，不需要再装 pip / poetry / pyenv

#### Scenario: 替代工具产物被拒入仓

- **WHEN** PR 中新增 `requirements.txt` 或 `poetry.lock`
- **THEN** CI 静态检查作业失败并指向本 spec

### Requirement: `uv.lock` consistency is enforced in CI

CI MUST 把 `uv sync --frozen` 作为第一步；该命令在 lock 与 `pyproject.toml` 不一致时会失败。修改依赖的 PR MUST 同时提交 `uv.lock` 变更。

#### Scenario: 修改依赖未更新 lock

- **WHEN** PR 修改 `pyproject.toml` 的 `dependencies` 但未提交 `uv.lock` 变更
- **THEN** CI `uv sync --frozen` 步骤失败、PR 不能合并

#### Scenario: 锁文件保持可重现

- **WHEN** 在两台机器上分别 `uv sync --frozen`
- **THEN** 两个 `.venv/` 中安装的所有依赖版本完全一致

### Requirement: uv version is pinned

`pyproject.toml` 的 `[tool.uv]` 段 MUST 设置 `required-version = ">=X.Y,<X.(Y+1)"` 区间锁定 uv minor 版本；CI 中 `astral-sh/setup-uv@v3` MUST 显式指定相同 minor 版本。每次升级 uv 主线版本 MUST 走单独 PR。

#### Scenario: 本地 uv 版本超出区间

- **WHEN** 开发者本地装了不在区间内的 uv 版本并执行 `uv sync`
- **THEN** uv 自身报错并退出，提示 `required-version` 限制

### Requirement: All project commands run via `uv run`

文档、CI、shell wrapper 中的项目命令 MUST 通过 `uv run <command>` 调用（包括 `pytest`、`ruff`、`mypy`、`lint-imports`、`copy-trader` CLI 自身）。MUST NOT 假设全局 `python` / 全局 pip 安装的工具可用。

#### Scenario: CI 调用 pytest

- **WHEN** `.github/workflows/ci.yml` 跑测试步骤
- **THEN** 步骤命令为 `uv run pytest -q` 而非 `pytest -q`

#### Scenario: CLI 入口经由 uv 暴露

- **WHEN** 开发者执行 `uv run copy-trader doctor`
- **THEN** 命令解析到 `copy_trader.cli.main:app`（pyproject `[project.scripts]` 定义的 entry）并正常运行
