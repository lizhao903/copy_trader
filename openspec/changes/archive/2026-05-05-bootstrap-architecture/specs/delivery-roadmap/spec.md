## ADDED Requirements

### Requirement: Delivery proceeds in five sequenced milestones M0–M5 (bottom-up)

构建 MUST 按顺序推进 M0（工具链与骨架）→ M1（PnL 与 reconcile 骨架）→ M2（Binance spot 端到端）→ M3（Hyperliquid spot）→ M4（Backtest + Dashboard）→ M5（生产部署与灰度）。每个 milestone MUST 是独立 PR；MUST NOT 在同一 PR 中混合不同 milestone 的范围。

#### Scenario: M2 PR 中混入 M3 venue 实现被拒

- **WHEN** PR 同时新增 `binance/spot.py` 与 `hyperliquid/spot.py`
- **THEN** review 拒绝合并，要求拆为两个 PR 并按 milestone 顺序排队

### Requirement: Each milestone has explicit acceptance and rollback procedures

每个 milestone MUST 在 `tasks.md` 中列出：

- 验收清单（可执行、grep-able 或可量化）
- smoke test 覆盖范围
- 回滚步骤（feature flag、`bin/rollback.sh <tag>`、或 revert PR）
- 灰度观察的关键指标（reconcile diff 行数、PnL deviation、critical alert 数）

每个 milestone 合并后 MUST 打 git tag `bootstrap/m<N>`，便于回滚。

#### Scenario: 验收清单未通过禁止合并

- **WHEN** M1 PR smoke test 报告 reconcile diff > 0 行
- **THEN** PR review 标记 changes requested，作者修复后重跑

#### Scenario: 回滚步骤可执行

- **WHEN** M2 灰度阶段出现严重告警，运维执行回滚预案
- **THEN** `git checkout bootstrap/m1` + `bin/rollback.sh` 即可恢复，全程 ≤ 15 分钟

### Requirement: M0 delivers a runnable skeleton with full CI red lines

M0 milestone MUST 一次性交付：

- `pyproject.toml` + `uv.lock` 跑通 `uv sync`
- `src/copy_trader/{core,config,cli}/` 骨架（CLI 仅含 `doctor` 子命令的最小可用实现）
- `tests/` 镜像目录 + 至少 5 个测试覆盖 runtime lock、`Settings` overlay、CLI 启动
- `.github/workflows/ci.yml` 跑 `uv sync --frozen` + `uv run pytest` + `uv run lint-imports` + `uv run ruff` + `uv run mypy` + 静态扫描（禁 `from script.` / 项目根 state 路径）
- `import-linter` contracts 文件覆盖完整 D2 单向依赖图
- README 含 onboard 三件套（uv 安装、`uv sync`、`uv run copy-trader doctor`）

完成 M0 后，仓库 MUST 满足"clone → 一行命令 onboard → 一行命令自检"的最小可用形态。

#### Scenario: M0 完成后 doctor 能跑

- **WHEN** 新机器克隆仓库后执行 `curl -LsSf https://astral.sh/uv/install.sh | sh && uv sync && COPY_TRADER_ENV=dev uv run copy-trader doctor`
- **THEN** 输出运行时根、env_tag、machine_id、子目录可写性、配置来源列表，进程正常退出

### Requirement: M1 delivers PnL engine and reconciler skeleton

M1 milestone MUST 一次性交付：

- `persistence/ledger.py`（含 schema 创建脚本与 schema_version 迁移）
- `pnl/engine.py`（加权平均 + FIFO 双模式）
- `execution/reconciler.py`（三级 diff 输出）
- `cli/main.py` 新增 `reconcile` 子命令
- 黄金测试 `tests/pnl/test_golden.py`（覆盖加仓 / 部分止盈 / 全平 / 加仓后止损四类组合）
- 跨环境写入拒绝测试 `tests/persistence/test_cross_environment_guard.py`

完成 M1 后，dev/prod 切换在没有任何业务代码的情况下也应已经被锁文件 + ledger 校验联合堵死。

#### Scenario: M1 完成后跨机器写入被拦截

- **WHEN** 模拟两台机器（不同 `machine_id`）对同 account 同时写入 ledger
- **THEN** 第二台机器写入抛 `CrossEnvironmentWriteError`，ledger 不变

### Requirement: M2 delivers end-to-end Binance spot path with paper parity

M2 milestone MUST 一次性交付：

- `exchanges/binance/spot.py`（完整 Protocol 实现）
- `exchanges/paper.py`（参数化匹配任意 venue）
- `marketdata/binance/`（K 线拉取与本地缓存）
- `strategies/base.py` + 一个 `strategies/hello.py` 最小策略（不下单或常数信号，仅验证管线）
- `runners/live.py`（单一 runner 同时支持 live / paper / dry-run 三种模式）
- 契约测试 `tests/exchanges/test_protocol_contract.py` 参数化两套 (binance.spot, paper.binance.spot)

#### Scenario: hello 策略 dry-run 跑通

- **WHEN** 执行 `COPY_TRADER_ENV=dev uv run copy-trader run --strategy hello --account spot --mode dry-run`
- **THEN** runner 启动 reconcile（空 ledger）→ 跑 N 轮策略循环 → 不写 ledger → 输出健康日志

### Requirement: M3 delivers Hyperliquid spot with multi-venue parity

M3 milestone MUST 一次性交付：

- `exchanges/hyperliquid/spot.py`（含 EIP-712 私钥签名）
- `marketdata/hyperliquid/`
- 同 `hello` 策略两个 venue 都能 dry-run 跑通
- 多 venue reconcile 用例

#### Scenario: 加 venue 不动上层

- **WHEN** M3 PR diff 涵盖 `exchanges/hyperliquid/`、`marketdata/hyperliquid/`、对应测试，与 `exchanges/registry.py` 注册表更新
- **THEN** `runners/`、`execution/`、`pnl/`、`strategies/` 文件 diff 行数为 0

### Requirement: M4 delivers backtest, dashboard settings center, and runner lifecycle

M4 milestone MUST 一次性交付：

- `runners/backtest.py`（消费 `marketdata/cache/` 历史 K 线、复用同 strategy / pnl / execution 栈）
- `marketdata/cache/` SQLite 缓存层
- `runners/service.py::RunnerService` + `persistence/runner_registry.py` 升级为实例表 + 生命周期状态机（见 `runner-lifecycle` spec）
- `cli/main.py::registry {create|update|delete|start|stop|list|reap}` 子命令
- `cli/dashboard`（FastAPI）：
  - `/runners`：runner 实例 CRUD + 启停 + 状态查看
  - `/overview`：账户余额 + unrealized PnL（沿用 PnlEngine）
  - `/settings`：统一设置中心，按 `field_layer_map` 渲染 accounts / capital_allocation / pyramid / fixed_position / strategies / notify 等字段；写 local 层立即生效，写 base/env 层走 draft PR（见 `config-overlay` spec 末尾两条 requirement）
- ROADMAP epic 接入点（指标 DSL、A/B 赛马、portfolio 等）骨架占位

#### Scenario: backtest 与 live 共用 strategy

- **WHEN** 同一个 `hello` 策略分别由 `runners/live.py`（dry-run 模式）和 `runners/backtest.py` 调用
- **THEN** 两端给出可比对的信号序列；不存在策略代码分叉

#### Scenario: dashboard 启停 runner 与 CLI 等价

- **WHEN** 同一 runner 实例分别由 dashboard 按钮与 `copy-trader registry start <id>` 启动
- **THEN** 两侧调用同一 `RunnerService.start(id)`，行为与产生的审计日志条目一致

#### Scenario: 设置中心改 local 字段立即生效

- **WHEN** 用户在 dashboard `/settings` 把 `pyramid[0].add_trigger_pct` 从 1.5 改 1.8 并保存
- **THEN** `$COPY_TRADER_HOME/config.yaml` 被更新，运行中 runner 在下一轮主循环读到新值，无需重启

### Requirement: M5 delivers production deployment with grace period

M5 milestone MUST 一次性交付：

- systemd unit 模板（`Environment=COPY_TRADER_ENV=prod`、`EnvironmentFile=/etc/copy_trader/secrets.env`）
- `bin/{deploy,release,rollback}.sh`（薄壳调用 `copy-trader` CLI）
- 生产机灰度运行 ≥ 14 天 + 0 critical alert + 0 reconcile drift
- onboard 文档（生产机首次部署 step-by-step）

完成 M5 后，本计划 archive；后续业务 epic 走独立 OpenSpec change。

#### Scenario: 灰度未达标阻塞 archive

- **WHEN** M5 灰度第 10 天出现 1 次 critical alert
- **THEN** 灰度时钟重置，archive 推迟到再连续 14 天无 alert 之后
