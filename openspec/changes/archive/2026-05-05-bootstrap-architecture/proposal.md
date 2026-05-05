## Why

`copy_trader` 是从零起步的独立量化跟单交易项目（与 `lizhao903/autotrader` 无任何代码、依赖、运行时关联），目标场景是 Binance + Hyperliquid 的实盘 / 回测 / paper / 看板一条龙。在写第一行业务代码之前，需要先把架构基线拍死，避免重蹈 autotrader 的两个典型坑：

1. **结构不易扩展**：脚本式 `script/{run,backtest,strategy,utils,...}` 加 `sys.path.insert(...)` 伪包，几十个写死策略名的 `run_*.py` 与启停 shell 脚本平铺，加一个交易所 / 加一种执行模式（live / paper / backtest）就要复制粘贴；broker 抽象与函数式调用路径并存，二义性长期没人收尾
2. **dev/prod 切换出现 PnL 计算错误**：所有运行时状态（`trade_info/position_*.json`、`logs/`、`klines.db`、`*.pid`、`.env`）锚在项目根 CWD，PnL 在多个 runner 里散点重算 `(price - entry_price) * qty`，position 缓存与 SQLite 账本之间没有强一致的 single source of truth，两台机器一旦共享 state（rsync、误入 git、网络盘）立刻互相污染下一轮 PnL

本变更产出 `copy_trader` 的**初始架构方案**：分层包结构 + dev/prod/paper 运行时根隔离 + PnL 单一来源 + 现代 Python 工具链（`uv` 管理依赖与虚拟环境），后续 `/opsx:apply` 按 milestone 落实代码。

## What Changes

- 选定 **uv 作为唯一的 Python 包与虚拟环境管理工具**：`uv sync` 装依赖、`uv run` 跑命令、`uv lock` 锁版本；不再使用 `pip` / `pip-tools` / `poetry` / `pdm` / `conda`
- 建立 src-layout 可安装包：`src/copy_trader/{core,exchanges,marketdata,strategies,execution,pnl,persistence,notify,runners,config,cli}/`，单向依赖（`core` 零下游，`cli → runners → execution/pnl/exchanges/...`），由 import-linter 在 CI 强制
- 引入 `COPY_TRADER_HOME` + `COPY_TRADER_ENV ∈ {dev, paper, prod}` 双轴隔离运行时数据；`state/`、`logs/`、`pids/`、`db/`、`secrets/.env` 全部从该根派生；启动期写入并校验 `state/.runtime_lock.json`（含 `env_tag` + `machine_id` + `schema_version`），跨环境或跨机器复制状态 → fail-fast
- 引入 SQLite 交易账本作为 fills 与 PnL 的**唯一真相源**；`state/position_*.json` 仅作可重建缓存；启动期强制 `ReconcileService` 比对 `ledger / exchange / cache` 三方差异
- ledger 行带 `env_tag / machine_id / schema_version` 三列；跨环境写入由 ledger 写入路径主动拒绝
- 配置层 = Pydantic Settings + `config/{base,dev,paper,prod}.yaml` overlay；敏感凭证只走 env vars 或 `$COPY_TRADER_HOME/secrets/.env`，禁止出现在 yaml
- 配置 schema 在 M0 阶段就**完整覆盖**四段业务参数：`accounts`（账户维护，含 venue/凭证别名/启停状态）、`capital_allocation`（按 account / symbol / strategy 分配资金）、`pyramid`（滚仓参数：首仓比例、加仓触发涨跌幅、储备金、轮次上限）、`fixed_position`（固定仓位 size / qty / 限价）
- **Dashboard 升级为「统一设置中心」**：把全部配置（含敏感字段元信息但不显示值）以表单形式呈现读写；写入分两路——本机参数（capital 金额、滚仓阈值、固定仓位 size 等）写 `$COPY_TRADER_HOME/config.yaml` 立即生效；结构性字段（账户名单、symbol 白名单、策略默认参数）写 git workdir 中的 `<env>.yaml` 并自动调 `gh pr create --draft` 走 review，保留审计链
- **多 runner 实例化**：runner 建模为 `RunnerInstance(id, name, exchange, strategy, account, params, status)`；`persistence/runner_registry.py` 从原"活跃 PID 跟踪表"升级为"实例定义 + 生命周期状态机"；CLI `copy-trader registry {create|update|delete|start|stop|list|reap}` 与 dashboard `/runners` 提供等价 CRUD；同一份业务代码可同时跑 N 个 runner 实例（不同 venue / 不同策略 / 不同账户），互不污染状态
- 单一 CLI（pyproject `[project.scripts] copy-trader = "copy_trader.cli.main:app"`）：`run / backtest / paper / reconcile / dashboard / registry / doctor` 子命令，shell 脚本只保留极薄运维 wrapper（`deploy.sh / release.sh / rollback.sh`）
- 交易所统一走 `copy_trader.exchanges.base.Exchange` Protocol；每个 venue 一个子包，包含 spot / perp / paper 实现；上层（runner / execution / pnl）只依赖 Protocol 与 `ExchangeRegistry`，禁止直接 import 具体类
- 制定 M0–M5 自底向上的**交付路线**：先 M0 包骨架 + 工具链 + CI；再 M1 PnL 引擎与 reconcile；再 M2 单交易所（Binance spot）端到端跑通；再 M3 第二交易所（Hyperliquid）；再 M4 paper / backtest / dashboard；再 M5 部署与生产灰度
- **本变更范围严格限定为「系统设计 + GitHub issue 拆分」，不做任何代码开发**：
  - 产出系统设计文档：proposal.md / design.md / specs/<7 个 capability>/spec.md（本目录下完成）
  - 在 GitHub `lizhao903/copy_trader` 仓库建立 6 个 milestone（`bootstrap/m0` ~ `bootstrap/m5`）与若干 `area:<layer>` label
  - 把 M0–M5 各阶段拆分为 25–35 个独立 issue，每个 issue 描述含验收清单 + 关联 spec 文件
  - 不在本变更内提交 `pyproject.toml`、`uv.lock`、源码、CI 配置、shell 脚本，所有实装工作交给 issue 后续单独的 OpenSpec change 或直接 PR 推进

## Capabilities

### New Capabilities

- `tooling-uv`: 用 uv 管理 Python 版本、依赖、虚拟环境、脚本入口的契约（命令集、lock 文件提交策略、CI 中的 uv 用法）
- `package-layout`: `src/copy_trader/<layer>/` 目标分层与单向依赖规范（哪一层可以依赖哪一层，禁止反向）
- `runtime-isolation`: 通过 `COPY_TRADER_HOME` + `COPY_TRADER_ENV` 隔离 dev/paper/prod 运行时数据根目录的契约（路径解析、写入护栏、跨环境检测）
- `pnl-single-source`: 持仓成本与盈亏计算的唯一来源契约（账本→引擎→缓存的方向、startup reconcile、跨机器写入拒绝）
- `exchange-adapter`: 统一 Exchange Protocol 与 venue 子包结构（registry 解耦、paper 与 live 同 Protocol、精度规则封装在 adapter 内部）
- `config-overlay`: 分层配置（base + env overlay + 本地机器覆盖 + env vars + CLI flags）解析规则、命名规范、业务字段 schema 边界（accounts / capital_allocation / pyramid / fixed_position 四段必填）、Dashboard 设置中心写入分流（local 层直接写 / base+env 层走 PR）
- `runner-lifecycle`: 多 runner 实例化能力（实例定义模型、CRUD API、生命周期状态机、CLI 与 Dashboard 等价、跨实例状态隔离）
- `delivery-roadmap`: M0–M5 自底向上交付路线、每阶段验收标准、阻塞推进的硬性指标

### Modified Capabilities

<!-- 当前 openspec/specs/ 为空，本项目尚无 spec 可修改 -->

## Impact

- **目标仓**：本仓（`copy_trader`），独立项目，与 `lizhao903/autotrader` 无任何代码 / git history / 部署链接
- **本变更直接产物**：
  - `openspec/changes/bootstrap-architecture/{proposal.md, design.md, specs/<8 个 capability>/spec.md, tasks.md}`（设计文档）
  - GitHub `lizhao903/copy_trader` 上的 6 个 milestone + N 个 `area:*` label + 30–40 个待办 issue（拆分产出）
- **后续受影响内容**（由 issue 后续承接，**不在本变更内修改**）：
  - `pyproject.toml`：声明 `requires-python`、依赖、`[project.scripts]`、`[tool.uv]` 配置
  - `uv.lock`：作为唯一 lock 文件提交入 git（PR 中不容忽视）
  - `src/copy_trader/`：完整包骨架
  - `tests/`：镜像分层；架构测试（import-linter）+ PnL 黄金测试 + 跨环境写入拒绝测试为 CI 红线
  - `config/{base,dev,paper,prod}.yaml`：分层配置，本仓内的运行时数据目录占位 `var/`
  - `bin/`：仅保留 ≤ 5 个真正必要的运维脚本；启停日常通过 `copy-trader run` 直接完成
  - `.github/workflows/ci.yml`：CI 流水线（`uv sync` → `uv run pytest` → `uv run lint-imports` → `uv run ruff` / `uv run mypy`）
  - `CLAUDE.md`：在 `bootstrap-architecture` 实施 PR 中更新，加入 uv / runtime root / CLI 速查
- **风险**：
  - uv 仍处于快速演进阶段，需要在 `pyproject.toml` 中钉死兼容版本范围与 CI 中固定 uv 版本
  - 在生产灰度阶段切错 `COPY_TRADER_ENV` 会让锁文件直接拒启动——这是设计目标但需要 onboard 文档配合
- **不在本计划范围**：具体策略实现、回测引擎算法、A/B 赛马框架、portfolio 资金分配模型、交易所新增（首批交付仅 Binance spot + Hyperliquid spot）
