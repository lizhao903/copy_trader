## ADDED Requirements

### Requirement: Source code lives under an installable `src/copy_trader/` package

仓库 MUST 提供 `src/copy_trader/` 作为唯一的生产 Python 包，并通过 `pyproject.toml` 声明为可分发包，支持 `uv sync` 一步安装。所有运行时代码 MUST 从 `copy_trader.<subpackage>` 导入；MUST NOT 在入口脚本顶部使用 `sys.path.insert(...)` 之类伪包技巧；MUST NOT 在仓库根直接放散落 `.py` 入口。

#### Scenario: 任意工作目录可运行 CLI

- **WHEN** 开发者在 `uv sync` 后从任意 CWD 执行 `uv run copy-trader doctor`
- **THEN** 命令成功解析包模块路径，运行时数据按 `runtime-isolation` 规范走 `$COPY_TRADER_HOME`，与 CWD 无关

#### Scenario: 误抄 autotrader 的 `from script.x` 导入被拦截

- **WHEN** PR 中含有 `from script.run import ...` 或 `from script.utils import ...`
- **THEN** CI 静态扫描步骤失败并指向本 spec 与依赖契约

### Requirement: Subpackages follow the documented one-way dependency graph

`src/copy_trader/` MUST 包含 `core, exchanges, marketdata, strategies, execution, pnl, persistence, notify, runners, config, cli` 子包，依赖方向 MUST 严格单向：

```
cli → runners
runners → execution, pnl, exchanges, marketdata, strategies, notify, persistence, config
execution → pnl, exchanges, marketdata, persistence, core
pnl → persistence, core
exchanges → core
marketdata → core
strategies → core, marketdata
persistence → core
notify → core
config → core
core → (stdlib only + pydantic-core)
```

任何反向 import MUST 视为架构 bug；CI MUST 通过 `import-linter` 自动阻断。

#### Scenario: `core` 反向依赖被拦截

- **WHEN** 有人在 `core/order.py` 中 `from copy_trader.exchanges import ...`
- **THEN** `uv run lint-imports` 失败并指明违反的 contract

#### Scenario: 新增层未更新依赖图

- **WHEN** 有人为新功能新增顶层目录 `src/copy_trader/foo/` 且未在本 spec 与 import-linter 配置中声明
- **THEN** import-linter contract 失败、PR 不能合并

### Requirement: Tests mirror the package layout

`tests/` MUST 镜像 `src/copy_trader/<subpackage>/` 的目录结构（如 `tests/core/`、`tests/exchanges/binance/`）。每个新增模块 MUST 在对应位置补测试。

#### Scenario: 新模块缺失镜像测试目录

- **WHEN** PR 新增 `src/copy_trader/strategies/foo.py` 而 `tests/strategies/test_foo.py` 不存在
- **THEN** CI 覆盖率检查标红，要求作者补测试或显式标记 `# no-tests-required` 并在 PR 描述说明

### Requirement: Package metadata declares Python version and CLI entry

`pyproject.toml` 的 `[project]` 段 MUST 至少声明：

- `name = "copy-trader"`
- `requires-python = ">=3.12"`
- `dependencies = [...]`（运行时依赖）
- `[project.optional-dependencies] dev = [...]`（开发依赖）
- `[project.scripts] copy-trader = "copy_trader.cli.main:app"`（CLI entry）
- `[build-system]` 段使用 PEP 517 兼容后端（hatchling 优先）

#### Scenario: CLI entry 注册成功

- **WHEN** 开发者执行 `uv sync` 后查看 `.venv/bin/`
- **THEN** 该目录存在 `copy-trader` 可执行文件，调用即解析到 `copy_trader.cli.main:app`
