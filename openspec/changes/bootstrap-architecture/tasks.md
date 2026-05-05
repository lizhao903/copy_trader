> **Scope**：本变更只做"系统设计 + GitHub issue 拆分"，不写代码、不动 `pyproject.toml`、不建 CI。所有实装动作下沉到下方拆出的 issue，由后续独立 PR / change 承接。

## 1. 系统设计产出（已完成）

- [x] 1.1 `proposal.md` 完成（独立项目定位、autotrader 反面教训、7 个 capability、范围限定为设计 + issue 拆分）
- [x] 1.2 `design.md` 完成（D1–D9：uv 工具链、src layout 单向依赖、运行时根目录、PnL 单一来源、Exchange Protocol、配置 overlay、CLI、CI 红线、M0–M5 路线）
- [x] 1.3 `specs/tooling-uv/spec.md` 完成
- [x] 1.4 `specs/package-layout/spec.md` 完成
- [x] 1.5 `specs/runtime-isolation/spec.md` 完成
- [x] 1.6 `specs/pnl-single-source/spec.md` 完成
- [x] 1.7 `specs/exchange-adapter/spec.md` 完成
- [x] 1.8 `specs/config-overlay/spec.md` 完成（含 accounts/capital/pyramid/fixed_position 四段 + LayerScope + Dashboard 写入分流）
- [x] 1.9 `specs/delivery-roadmap/spec.md` 完成（含 M4 设置中心 + runner CRUD）
- [x] 1.10 `specs/runner-lifecycle/spec.md` 完成
- [x] 1.11 `openspec validate bootstrap-architecture` 通过

## 2. GitHub 仓库准备（label + milestone）

- [ ] 2.1 在 `lizhao903/copy_trader` 创建 6 个 milestone：`bootstrap/m0`、`bootstrap/m1`、`bootstrap/m2`、`bootstrap/m3`、`bootstrap/m4`、`bootstrap/m5`，描述各引用对应 spec 段落
- [ ] 2.2 在 `lizhao903/copy_trader` 创建 `area:*` label：`area:tooling`、`area:core`、`area:exchanges`、`area:marketdata`、`area:strategies`、`area:execution`、`area:pnl`、`area:persistence`、`area:notify`、`area:runners`、`area:config`、`area:cli`、`area:ci`、`area:docs`、`area:deploy`
- [ ] 2.3 创建优先级 label：`priority:p0`、`priority:p1`、`priority:p2`
- [ ] 2.4 创建类型 label：`type:bootstrap`（首批基础设施）、`type:contract`（spec 落地，验收门）

## 3. M0 工具链与骨架 — issue 拆分（约 7 个）

- [ ] 3.1 issue `[m0] 建立 uv 工具链与 pyproject.toml`：依赖 `tooling-uv` spec；交付 `pyproject.toml`、`uv.lock`、`.python-version`；label `area:tooling, type:bootstrap, priority:p0`
- [ ] 3.2 issue `[m0] 建立 src/copy_trader/ 包骨架与单向依赖图`：依赖 `package-layout` spec；交付 `src/copy_trader/{core,exchanges,marketdata,strategies,execution,pnl,persistence,notify,runners,config,cli}/__init__.py`、`.import-linter.ini`；label `area:core, type:contract, priority:p0`
- [ ] 3.3 issue `[m0] 实现运行时根目录解析与 lock 文件校验`：依赖 `runtime-isolation` spec；交付 `src/copy_trader/config/runtime.py` + 配套测试；label `area:config, area:runtime, type:contract, priority:p0`
- [ ] 3.4 issue `[m0] 实现 Pydantic Settings 多层 overlay + 业务 schema 四段 + LayerScope`：依赖 `config-overlay` spec；交付 `src/copy_trader/config/settings.py`（含 `AccountConfig` / `CapitalSlice` / `PyramidConfig` / `FixedPositionConfig` 四段必填 + 每字段 `LayerScope` 元数据 + `field_layer_map()` API）+ `config/{base,dev,paper,prod}.yaml` 占位（含四段样例）+ 测试（覆盖 4 层来源 / 敏感字段守卫 / pyramid 与 fixed_position 互斥校验 / LayerScope 映射）；label `area:config, type:contract, priority:p0`
- [ ] 3.5 issue `[m0] 实现 CLI 入口与 doctor 子命令`：依赖 `package-layout` spec 的 entry 点要求；交付 `src/copy_trader/cli/main.py`（Typer app）+ `doctor` 子命令 + 测试；label `area:cli, type:bootstrap, priority:p0`
- [ ] 3.6 issue `[m0] 建立 GitHub Actions CI 与静态护栏`：依赖 `tooling-uv` + `package-layout` + `runtime-isolation` spec；交付 `.github/workflows/ci.yml`（uv sync / pytest / lint-imports / ruff / mypy / 静态扫描）；label `area:ci, type:bootstrap, priority:p0`
- [ ] 3.7 issue `[m0] README + CLAUDE.md onboard 章节`：交付 onboard 三件套（uv 安装、`uv sync`、`uv run copy-trader doctor`）；label `area:docs, type:bootstrap, priority:p1`

## 4. M1 PnL 引擎与 reconciler — issue 拆分（约 5 个）

- [ ] 4.1 issue `[m1] 实现 SQLite ledger 与 schema_version 迁移`：依赖 `pnl-single-source` spec；交付 `persistence/ledger.py` + 跨环境写入校验；label `area:persistence, type:contract, priority:p0`
- [ ] 4.2 issue `[m1] 定义 core 数据模型（Order/Fill/Position/Money/PnlBreakdown/SymbolInfo）`：依赖 `package-layout` 与 `pnl-single-source` spec；label `area:core, type:contract, priority:p0`
- [ ] 4.3 issue `[m1] 实现 PnlEngine（加权平均 + FIFO 双模式）+ 黄金测试`：依赖 `pnl-single-source` spec；交付 `pnl/engine.py` + `pnl/cost_basis.py` + `tests/pnl/test_golden.py`（4 类组合）；label `area:pnl, type:contract, priority:p0`
- [ ] 4.4 issue `[m1] 实现 ReconcileService 三级 diff`：依赖 `pnl-single-source` spec；交付 `execution/reconciler.py` + 测试覆盖三种 diff + cache 缺失/被编辑场景；label `area:execution, type:contract, priority:p0`
- [ ] 4.5 issue `[m1] CLI reconcile 子命令 + 跨环境写入拒绝测试`：交付 `cli/main.py::reconcile` + `tests/persistence/test_cross_environment_guard.py`；label `area:cli, area:persistence, type:contract, priority:p0`

## 5. M2 Binance spot 端到端 + paper parity — issue 拆分（约 6 个）

- [ ] 5.1 issue `[m2] Exchange Protocol 与 ExchangeRegistry`：依赖 `exchange-adapter` spec；交付 `exchanges/base.py` + `exchanges/registry.py` + 注册命名规范；label `area:exchanges, type:contract, priority:p0`
- [ ] 5.2 issue `[m2] Binance spot adapter（含限频与精度规则）`：依赖 `exchange-adapter` spec；交付 `exchanges/binance/spot.py`；label `area:exchanges, priority:p0`
- [ ] 5.3 issue `[m2] Binance marketdata（公开 K 线）`：依赖 `exchange-adapter` spec；交付 `marketdata/base.py` + `marketdata/binance/`；label `area:marketdata, priority:p0`
- [ ] 5.4 issue `[m2] 通用 Paper Exchange（参数化包裹任意 venue）`：依赖 `exchange-adapter` spec；交付 `exchanges/paper.py` + 滑点/费率配置；label `area:exchanges, type:contract, priority:p0`
- [ ] 5.5 issue `[m2] Strategy Protocol + hello 最小策略`：交付 `strategies/base.py` + `strategies/registry.py` + `strategies/hello.py`；label `area:strategies, type:contract, priority:p0`
- [ ] 5.6 issue `[m2] LiveRunner 主循环 + run CLI 子命令 + 契约测试`：依赖 `exchange-adapter` + `pnl-single-source` + `runtime-isolation` spec；交付 `runners/live.py` + `cli/main.py::run` + `tests/exchanges/test_protocol_contract.py`（参数化 binance.spot / paper.binance.spot）；label `area:runners, area:cli, priority:p0`

## 6. M3 Hyperliquid spot — issue 拆分（约 3 个）

- [ ] 6.1 issue `[m3] Hyperliquid spot adapter（含 EIP-712 签名）`：依赖 `exchange-adapter` spec；label `area:exchanges, priority:p0`
- [ ] 6.2 issue `[m3] Hyperliquid marketdata`：label `area:marketdata, priority:p0`
- [ ] 6.3 issue `[m3] 多 venue reconcile 测试 + 架构测试断言上层零改动`：交付 `tests/runners/test_multi_venue_reconcile.py` + 架构测试；label `area:runners, type:contract, priority:p0`

## 7. M4 Backtest + 设置中心 + Runner Lifecycle — issue 拆分（约 7 个）

- [ ] 7.1 issue `[m4] SQLite klines 缓存层`：交付 `marketdata/cache/`；label `area:marketdata, priority:p1`
- [ ] 7.2 issue `[m4] BacktestRunner（共用 strategy/execution/pnl 栈）`：交付 `runners/backtest.py` + `cli/main.py::backtest`；label `area:runners, area:cli, priority:p1`
- [ ] 7.3 issue `[m4] Runner instance schema + persistence/runner_registry.py 升级`：依赖 `runner-lifecycle` spec；交付 `core/runner_instance.py`、SQLite `runner_instances` 表 + 迁移脚本、ledger 行新增 `runner_id` 列；label `area:persistence, area:runners, type:contract, priority:p0`
- [ ] 7.4 issue `[m4] runners/service.py::RunnerService（CLI 与 Dashboard 共用）+ 状态机`：依赖 `runner-lifecycle` spec；交付 RunnerService（CRUD + start/stop/reap）+ 状态机校验 + 心跳机制 + cascade kill；label `area:runners, type:contract, priority:p0`
- [ ] 7.5 issue `[m4] CLI registry 子命令`：交付 `cli/main.py::registry {create,update,delete,start,stop,list,reap}`；label `area:cli, priority:p0`
- [ ] 7.6 issue `[m4] FastAPI Dashboard 基线（/overview + /runners CRUD）`：交付 `cli/dashboard.py` 应用骨架 + `/overview` + `/runners` 列表与启停按钮；label `area:cli, priority:p0`
- [ ] 7.7 issue `[m4] Dashboard 设置中心 /settings（统一读写配置）`：依赖 `config-overlay` spec 末尾两条 requirement；交付 `/settings` 路由（按 `field_layer_map` 渲染表单）+ 写 local 层直接落盘并热加载 + 写 base/env 层调 `gh pr create --draft` + 敏感字段掩码 + `dashboard_audit.log`；label `area:cli, area:config, type:contract, priority:p0`

## 8. M5 生产部署与灰度 — issue 拆分（约 4 个）

- [ ] 8.1 issue `[m5] 部署脚本 deploy/release/rollback`：交付 `bin/deploy.sh` / `bin/release.sh` / `bin/rollback.sh`；label `area:deploy, priority:p0`
- [ ] 8.2 issue `[m5] systemd unit 模板`：交付 `deploy/systemd/copy-trader@.service`；label `area:deploy, priority:p0`
- [ ] 8.3 issue `[m5] 生产 onboard 文档`：交付 `docs/PRODUCTION_ONBOARD.md`；label `area:docs, area:deploy, priority:p0`
- [ ] 8.4 issue `[m5] 灰度 14 天清单与 postmortem 模板`：交付灰度 checklist + `m5-postmortem.md` 模板；label `area:deploy, type:contract, priority:p0`

## 9. 横向 issue（约 2 个）

- [ ] 9.1 issue `[meta] PR 模板与 milestone 推进规则`：交付 `.github/PULL_REQUEST_TEMPLATE.md`（含 spec 引用、验收清单、回滚步骤、灰度指标四段）+ `docs/CONTRIBUTING.md` 关于 milestone 串行推进的硬性规则；label `area:docs, type:bootstrap, priority:p1`
- [ ] 9.2 issue `[meta] 灰度指标定义与告警阈值`：交付 reconcile drift / PnL deviation / critical alert 阈值文档；label `area:deploy, type:contract, priority:p1`

## 10. 拆分执行（用户确认后由 Claude 代为创建）

- [ ] 10.1 用户 review 第 3–9 节的 issue 草稿，提出增删改
- [ ] 10.2 用户授权后，由 Claude 用 `gh issue create` 批量创建 issue（先创 milestone 与 label，再创 issue 并关联 milestone / label / spec 引用）
- [ ] 10.3 把开出的 issue 编号回填到本 tasks.md 对应行尾（形式 `(#nn)`）
- [ ] 10.4 在本仓 commit 一次：`docs: split bootstrap-architecture into issues`，把更新后的 `tasks.md` 入库

## 11. 归档

- [ ] 11.1 全部 issue 创建完毕、tasks.md 回填完成后，由用户触发 `/opsx:archive bootstrap-architecture`
- [ ] 11.2 archive 后，本计划生命周期结束；后续每个 milestone 完成时由对应 PR 关闭对应 issue，不再回到本 change
