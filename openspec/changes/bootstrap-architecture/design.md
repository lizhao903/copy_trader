## Context

`copy_trader` 是从空仓库起步的独立量化跟单交易项目（截至本文档撰写时，仓库只有 README、LICENSE、Python `.gitignore`、OpenSpec 脚手架，无任何源代码）。需求来自项目作者本人长期的实盘经验，以及对参考项目 `lizhao903/autotrader` 的复盘——后者整体策略收益正向，但在两个维度上让作者吃过亏，本项目从第一行代码开始就要规避：

1. **结构不易扩展（来自 autotrader 的具体观察）**：`script/` 下 162 个文件靠 `sys.path.insert(0, SCRIPT_ROOT)` 伪包式组织；6 个写死策略名的 `run_*.py` 与较新的 `launcher.py` 双轨并存；27 个 `bin/*.sh` 各自封装一对启停命令；`script/run/broker/*` 已经是干净的 adapter 抽象，但 `script/utils/{binance,okx,bybit,hyperliquid}_*.py` 同时提供平行的函数式调用入口，老 runner 不走 broker 走老函数 → 二义性长期存在
2. **dev/prod 切换时 PnL 错乱（来自 autotrader 的根因）**：`trade_info/position_*.json`、`logs/`、`klines.db`、`*.pid`、`.env` 全部锚在项目根 CWD（CLAUDE.md 写明 `/Volumes/project/autotrade`）；PnL 在 runner 中散点重算 `(price - entry_price) * qty`；`trades_db.py` 已经落 SQLite 账本但**不被 PnL 计算路径读取**，仅供事后报表 → cache 与账本之间无强一致性，跨机器/跨环境复制 state 会直接污染下一轮 PnL

业务约束：

- 单人项目（项目作者本人就是用户），可以激进采用现代工具链
- 首批交付 Binance spot + Hyperliquid spot；带单子账户、合约、其他交易所属于第二阶段
- 实盘是真金白银，**结构错了的代价是真钱**——所以"先把骨架与护栏建好再写策略"是性价比最高的顺序
- 没有团队对齐成本，也没有外部贡献者；可以走 src layout + 强类型 + 严格 CI 的"工程化优先"路线，而不必为多人协作妥协

参考但**不复用**的资产：

- autotrader 的 KDJ 1h+3m 策略、Hyperliquid 私钥签名、SQLite klines 仓、IM Gateway 抽象——只作为业务知识参考，不直接复制粘贴代码
- autotrader 的 broker 抽象（`script/run/broker/`）已经是干净形态——本项目从第一天就采纳这个形态，并且**只有这一条路径**
- autotrader 的 ROADMAP 13 个 Epic 与 9 条 P0——本项目自身路线由后续 ROADMAP 决定，本计划只负责骨架

## Goals / Non-Goals

**Goals:**

- G1：在写第一行业务代码之前，把目标包结构、运行时根目录策略、PnL 单一来源、Exchange Protocol、配置 overlay、CLI 形态、CI 红线**一次性定稿**
- G2：用 `uv` 作为唯一 Python 工具链入口，从 onboard 第一步（`uv sync`）到 CI 都跑同一条命令链
- G3：把 dev/prod PnL 错乱在第一版骨架里就消除——不留"以后再加 reconcile"的债
- G4：交付一个能让"加一个交易所""加一个策略""加一种执行模式"都只动一个子包的分层，并由 import-linter 在 CI 强制单向依赖
- G5：M0 完成时（≤ 1 周工作量），仓库已经具备：可安装包、跑通 hello-world CLI、CI 三件套（pytest + import-linter + ruff）、runtime lock + reconcile 骨架，再开始往里塞业务

**Non-Goals:**

- 不实现具体策略（KDJ、Supertrend 等）——交给后续 ROADMAP epic
- 不写第二个交易所之外的 venue（OKX / Bybit / 带单 / 合约）
- 不写回测引擎算法本身（只定接口与目录位置，引擎实现走另一个 change）
- 不写 dashboard 前端（只定 `runners/` 与 `cli/dashboard` 的接入位）
- 不引入 async/await（保持同步循环模型，业务逻辑可读 > 微秒级延迟）
- 不引入 monorepo / pnpm workspace 之类多包结构——单 Python 包足够
- 不直接迁移或 import 任何 autotrader 代码（学习经验 ≠ 拷贝代码）

## Decisions

### D1. uv 是唯一 Python 工具链

**决定**：

1. 用 `uv` 管理 Python 版本（`requires-python = ">=3.12"`，由 `uv python` 拉取）、依赖（`uv add` / `uv remove`）、虚拟环境（`.venv/` 由 `uv sync` 创建）、脚本运行（`uv run <command>`）、lock（`uv.lock` 提交入 git）
2. `pyproject.toml` 是依赖与项目元数据的唯一来源：
   - `[project]` 段：name = `copy-trader`、version、`requires-python`、`dependencies`、`optional-dependencies`、`[project.scripts] copy-trader = "copy_trader.cli.main:app"`
   - `[tool.uv]` 段：`dev-dependencies`、`required-version`（钉死 uv 版本范围以避免环境飘）、`index-strategy = "first-index"`
3. CI 用 `astral-sh/setup-uv@v3` 安装 uv，第一步永远是 `uv sync --frozen`；任何 PR 修改依赖**必须**同时提交 `uv.lock` 变更，CI 校验 lock 一致性
4. 禁止 `requirements.txt` / `requirements-dev.txt` / `Pipfile` / `poetry.lock` / `setup.py` / `setup.cfg`；现有 `.gitignore` 已经覆盖虚拟环境目录
5. 开发者 onboard 路径：

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh   # 一次
   uv sync                                            # 装依赖 + 建虚拟环境
   uv run copy-trader doctor                          # 自检
   uv run pytest                                      # 跑测试
   ```

**为什么 uv 而不是别的**：

- `pip + venv`：手动管理虚拟环境与 lock 太繁琐，单人项目不值得
- `poetry`：解析速度慢、与 PEP 621 兼容差、`pyproject.toml` 语义偏离主线
- `pdm`：成熟，但 uv 在性能（Rust 实现，10–100× 速度差）与与 PEP 标准对齐上更优
- `pixi` / `conda`：科学计算栈友好，但本项目不依赖科学栈，conda 生态对纯 PyPI 项目反而是负担
- `hatch`：好用但聚焦发布而非依赖管理；本项目不打算发 PyPI

uv 缺点是仍在快速演进——**应对策略**：在 `[tool.uv].required-version` 钉死区间，CI 中 `setup-uv` 指定固定 minor 版本，每个季度统一升级一次。

### D2. 包结构与单向依赖（src layout）

```
src/copy_trader/
├── core/             # 纯值对象：Symbol, Order, Fill, Position, Money, PnlBreakdown
│                     # 零下游依赖（仅 stdlib + pydantic-core）
├── exchanges/
│   ├── base.py       # Exchange Protocol
│   ├── registry.py
│   ├── binance/      # 子包：spot.py（首批仅 spot）
│   ├── hyperliquid/
│   └── paper/        # 始终与 live broker 同 Protocol
├── marketdata/
│   ├── base.py       # KlineSource Protocol
│   ├── binance/
│   ├── hyperliquid/
│   └── cache/        # SQLite klines 缓存层
├── strategies/
│   ├── base.py       # Strategy Protocol
│   └── registry.py
├── execution/
│   ├── router.py     # OrderRouter
│   ├── risk.py       # RiskGate（日亏 / 总敞口 / 连亏熔断）
│   ├── pyramid.py
│   └── reconciler.py # ReconcileService
├── pnl/
│   ├── engine.py     # PnlEngine（加权平均 / FIFO 双模式）
│   └── cost_basis.py
├── persistence/
│   ├── ledger.py     # TradesRepo（SQLite）
│   ├── klines.py     # KlinesRepo
│   ├── runs.py       # RunsRepo
│   └── runner_registry.py
├── notify/
│   ├── gateway.py    # IM Gateway
│   └── adapters/     # slack / telegram / dingtalk / feishu（后续按需加）
├── runners/
│   ├── live.py
│   ├── paper.py
│   └── backtest.py
├── config/
│   ├── settings.py   # Pydantic Settings
│   └── overlay.py    # base + env + local + envvar + cli 合并
└── cli/
    └── main.py       # Typer CLI（run / backtest / paper / reconcile / dashboard / registry / doctor）

tests/                # 镜像 src/copy_trader/<layer>/
config/               # base.yaml / dev.yaml / paper.yaml / prod.yaml
bin/                  # ≤ 5 个运维 shell（deploy / release / rollback / dev_loop / install_cron）
docs/                 # ROADMAP / 配置示例
.github/workflows/    # ci.yml
```

**单向依赖契约**（用 import-linter 在 CI 强制）：

```
cli      → runners
runners  → execution, pnl, exchanges, marketdata, strategies, notify, persistence, config
execution→ pnl, exchanges, marketdata, persistence, core
pnl      → persistence, core
exchanges→ core
marketdata→ core
strategies→ core, marketdata
persistence→ core
notify   → core
config   → core
core     → (stdlib only + pydantic-core)
```

**为什么 src layout**：本地误 import（绕过包安装的 implicit relative import）会被 src layout 直接挡掉；测试运行必须先 `uv sync`，强制每个开发者验证打包正确性。

### D3. 运行时根目录与环境隔离：`COPY_TRADER_HOME` + `COPY_TRADER_ENV`

**决定**：

```
$COPY_TRADER_HOME/
├── state/         # position_*.json, runner registry cache, .runtime_lock.json, .machine_id
├── logs/          # run_*.log, trade_*.log, reconcile_*.log
├── pids/          # <runner>.pid
├── db/            # trades.db, klines.db, runs.db
└── secrets/.env   # 仅在没有 secret manager 的开发机使用
```

`COPY_TRADER_ENV ∈ {dev, paper, prod}` 必填；`COPY_TRADER_HOME` 默认：

| env   | 默认 `COPY_TRADER_HOME`   | 用途 |
|-------|---------------------------|------|
| dev   | `./var/dev/`              | 开发机本地 dry-run / paper 验证 |
| paper | `./var/paper/`            | paper broker 与真实数据并行 |
| prod  | `/var/lib/copy_trader/`   | 实盘 |

启动期：

1. 缺 `COPY_TRADER_ENV` → fail-fast
2. 解析 `COPY_TRADER_HOME`（CLI > env > 默认），创建缺失子目录（0700）
3. 读 / 写 `state/.machine_id`（首次启动生成 UUID）
4. 读 / 写 `state/.runtime_lock.json`（含 `env_tag, machine_id, schema_version, pid, started_at`）；如已有锁且 `env_tag` 或 `machine_id` 不匹配 → 退出，错误信息打印两侧值与修复指引

**为什么从第一天就要这套**：autotrader 的 PnL 错乱根因之一是状态目录跨机器/跨环境共享。本项目空仓库阶段实施成本最低，等业务代码长出来再回头加成本指数级上升。

### D4. PnL 单一来源：账本驱动 + 启动 reconcile

**决定**：

1. SQLite ledger（`db/trades.db`）是 fills 唯一真相源；列：`id, ts, account, symbol, side, qty, price, fee, fee_asset, exchange_order_id, env_tag, machine_id, schema_version`
2. `pnl.PnlEngine` 仅消费 ledger，输出 cost basis（默认加权平均，可选 FIFO）+ realized PnL + unrealized PnL（喂进当前价）
3. `state/position_*.json` = 可重建 cache；任何与 ledger 重建结果不一致字段被覆盖
4. 启动期 `ReconcileService.reconcile(account)` 强制运行：拉交易所余额 + 持仓 → 用 ledger 重建预期 → 三方比对 → 输出 `cache_drift` / `ledger_exchange_mismatch` / `unknown_position_on_exchange` 三级 diff
5. ledger 写入路径校验 `(env_tag, machine_id)` 与同账户上一行 schema_version >= 2 的记录一致；不一致拒绝写入并触发告警；`schema_version=1` 视为 legacy（本项目不会有 legacy，但保留语义供 backfill 工具使用）
6. cost basis 算法默认加权平均（`total_quote / total_qty`），可选 FIFO（仅税务 / 报表用）

**为什么从骨架就埋 reconcile**：和 D3 一样的逻辑——业务代码上来之前钉死，比将来打补丁安全得多。

### D5. Exchange Adapter Protocol

**决定**：

`copy_trader.exchanges.base.Exchange` Protocol：

```python
class Exchange(Protocol):
    name: str  # "binance.spot", "hyperliquid.spot", "paper.binance.spot", ...

    def get_balance(self, asset: str) -> Decimal: ...
    def fetch_position(self, symbol: str) -> Position | None: ...
    def place_order(self, req: OrderRequest) -> OrderAck: ...
    def cancel(self, order_id: str) -> None: ...
    def fetch_open_orders(self, symbol: str) -> list[Order]: ...
    def fetch_fills(self, symbol: str, since: datetime) -> list[Fill]: ...
    def get_symbol_info(self, symbol: str) -> SymbolInfo: ...
    def round_price(self, symbol: str, price: Decimal) -> Decimal: ...
    def round_qty(self, symbol: str, qty: Decimal) -> Decimal: ...
```

每个 venue 一个子包，内部封装 API client、限频、精度规则、签名。Paper exchange 实现完全相同的 Protocol，模拟成交并写入相同结构 ledger（`env_tag` 区分 paper/live）。

`exchanges/registry.py` 提供 `get(name) -> Exchange`；上层只通过 registry 拿实例，禁止直接 import 具体类（import-linter 拦截）。

**为什么不用 `ccxt` 之类统一库**：autotrader 的经验是带单子账户、Hyperliquid 私钥签名、Binance 子账户限频这类细节都需要 venue-specific 处理；统一抽象的代价是上层散落 if-else。自己定 Protocol 反而更干净。

### D6. 配置层：Pydantic Settings + YAML overlay

**决定**：

- `config/base.yaml`：与环境无关默认（symbols、capital allocation、策略参数、notify 通用）
- `config/<env>.yaml`：env 专属覆盖（日志级别、paper 默认、prod 限频更严等）
- `$COPY_TRADER_HOME/config.yaml`：机器本地覆盖（不入 git）
- 环境变量：前缀 `COPY_TRADER_`，自动映射到嵌套 settings 字段
- CLI flag：最高优先级

合并顺序：base → env → local → env vars → CLI。最终 settings 由 Pydantic 校验；含 `*_KEY/*_SECRET/*_TOKEN/*_PRIVATE_KEY` 命名的字段从 yaml 读出 → 启动报错，强制凭证只走 env vars。

`copy-trader doctor` 输出最终 settings + 来源（每个字段哪一层提供），便于人工审计。

### D7. CLI

`copy-trader`（pyproject scripts 注册的 entry point，由 uv 在 `uv sync` 时装到 venv `bin/`）：

```
copy-trader run        --strategy <name> --account <name> [--mode live|paper|dry-run]
copy-trader backtest   --strategy <name> --symbol <SYM> --start <DATE> --end <DATE>
copy-trader paper      --strategy <name> ...
copy-trader reconcile  --account <name> [--apply]
copy-trader dashboard  [--port 15000]
copy-trader registry   list|reap
copy-trader doctor     # env / lock / 账本一致性 + 配置来源
```

实现技术选型：[Typer](https://typer.tiangolo.com/)（Click 之上的类型注解 wrapper，与 Pydantic 配合好）。

`bin/*.sh` 仅保留：

- `bin/deploy.sh`、`bin/release.sh`、`bin/rollback.sh`：生产机部署（薄壳，调用 `copy-trader` CLI）
- `bin/dev_loop.sh` + `bin/install_cron.sh`：可选的自动化 driver（参考 autotrader `auto_dev_loop` 形态）

后台启停**不用** shell 脚本；生产机走 systemd unit，开发机直接 `copy-trader run` 前台或 `nohup uv run copy-trader run ... &`。

### D8. CI 红线

`.github/workflows/ci.yml` 必备 jobs（每条失败阻断 merge）：

1. `setup-uv@v3` → `uv sync --frozen`（验证 lock 一致）
2. `uv run pytest -q`（含 PnL 黄金测试 + runtime lock 测试 + paper/live parity 测试）
3. `uv run lint-imports`（D2 单向依赖契约）
4. `uv run ruff check . && uv run ruff format --check .`
5. `uv run mypy src/copy_trader`
6. 静态扫描：`grep -r "from script\." src/ tests/` → 期望 0 行（避免后续真有人误抄 autotrader 的 import 路径）
7. `grep -r "os.path.join(ROOT, \"trade_info\\|logs\\|klines.db" src/ tests/` → 期望 0 行（避免回到项目根 CWD 的老坑）

### D10. Dashboard 升级为「统一设置中心」+ 配置 schema 扩展

**决定**：

1. 配置 schema 在 M0 阶段就一次性定型，覆盖四段业务参数：

   ```yaml
   # config/base.yaml（结构性，进 git，PR-only review）
   accounts:
     spot:
       venue: binance.spot
       enabled: true
       credentials_alias: BINANCE_SPOT     # → BINANCE_SPOT_API_KEY / BINANCE_SPOT_SECRET_KEY 等
       symbols: [SOLUSDT]
     hl_eth:
       venue: hyperliquid.spot
       enabled: true
       credentials_alias: HYPERLIQUID_MAIN
       symbols: [ETH-USD]

   strategies:
     kdj_short_1h3m:
       module: copy_trader.strategies.kdj_short_1h3m
       params:
         oversold: 20
         overbought: 78
         stop_loss_pct: 4.5
         take_profit_pct: 6.5

   # 以下四段在 base.yaml 定结构与默认，在 <env>.yaml 与 local 层可覆盖具体数值
   capital_allocation:
     # 按 (account, strategy) 颗粒度
     - account: spot
       strategy: kdj_short_1h3m
       quote_asset: USDT
       max_quote_amount: 500.0
       reserve_quote_amount: 50.0

   pyramid:
     - account: spot
       strategy: kdj_short_1h3m
       enabled: true
       first_entry_fraction: 0.5     # 首仓占 max_quote_amount 比例
       add_trigger_pct: 1.5          # 浮盈 ≥ 此值触发加仓
       reserve_quote_usdt: 100.0
       max_rounds: 2

   fixed_position:
     # 与 pyramid 互斥；指定固定 size / qty 时跳过滚仓
     - account: hl_eth
       strategy: kdj_short_1h3m
       mode: fixed_qty               # fixed_qty | fixed_quote
       qty: 0.5                      # 当 mode=fixed_qty
       max_price: null               # 可选限价上限
   ```

2. **Dashboard 升级为「设置中心」**：把全部 settings（含 accounts / capital / pyramid / fixed_position / strategies / notify / risk 等）以表单呈现为读写界面。写入分流：

   - **Local 层字段**（即 `$COPY_TRADER_HOME/config.yaml` 允许覆盖的子集）→ 直接写本机文件，立即热加载（无需重启 runner，由 settings.py 的 watcher 触发）
   - **结构性字段**（base/env yaml 范畴：账户名单、symbol 白名单、策略 module 路径、`*_asset` 等）→ dashboard 写到当前 git workdir 的 `config/<env>.yaml`，调 `git diff` 在 UI 展示变更，提供"创建 draft PR"按钮（后端调 `gh pr create --draft -B main`），仍走 review；MUST NOT 直接 commit-push 到 main
   - **敏感字段**（`*_KEY/*_SECRET/*_TOKEN/*_PRIVATE_KEY`）→ dashboard 仅展示是否存在 / mask 后预览，不暴露值；编辑指引用户去 `secrets/.env` 或 secret manager

3. **字段边界声明**：每个 Pydantic Settings 字段用元数据标注 `LayerScope`（`base | env | local`）；dashboard 据此决定渲染只读 / 可编辑、写哪一层。`Settings.field_layer_map()` 是后端 API 的 source of truth，CLI `copy-trader doctor --schema` 也读它。

4. **审计**：dashboard 任何写入动作（local 写文件 / git workdir 改动 / draft PR 创建）都追加到 `$COPY_TRADER_HOME/logs/dashboard_audit.log`，含 `ts, user_agent, field, layer, before, after`（敏感字段掩码）。

**为什么这样划**：

- 用户一句话需求是"配置统一在看板设置中心"——这意味着 UX 唯一入口
- 但完全甩开 git audit 会丢"出问题时回滚到上一版"的能力——所以结构性字段仍走 PR
- 字段边界由 schema 元数据声明，dashboard 与 CLI 用同一份元数据，避免两边各做一套规则

**备选与拒绝原因**：

- *Dashboard 直接 commit + push main*：丢 review 与回滚链
- *Dashboard 完全只读，编辑全走 yaml + PR*：违反用户"统一在看板"诉求
- *把 yaml 全替换成 SQLite 配置表*：失去 git diff / blame 能力，长期不利

### D11. 多 runner 实例化与生命周期

**决定**：

1. **数据模型**：

   ```python
   # core/runner_instance.py
   class RunnerInstance(BaseModel):
       id: str                    # uuid7，新建时分配
       name: str                  # 用户友好名（"hl-eth-kdj-1h3m"），唯一
       venue: str                 # "binance.spot" / "hyperliquid.spot" → ExchangeRegistry.get(venue)
       account: str               # 引用 accounts.<name>
       strategy: str              # 引用 strategies.<name>
       params_override: dict      # 覆盖策略默认参数
       mode: Literal["live", "paper", "dry-run"]
       status: Literal["draft", "starting", "running", "stopping", "stopped", "errored"]
       pid: int | None
       started_at: datetime | None
       last_heartbeat: datetime | None
       created_at: datetime
       updated_at: datetime
   ```

2. **持久化**：`persistence/runner_registry.py` 升级为 SQLite 表 `runner_instances`（含 schema_version 列），不再仅用 PID 文件。PID 文件作为**进程层心跳**留在 `$COPY_TRADER_HOME/pids/`，registry 表才是真相源。

3. **生命周期状态机**：

   ```
   draft ─create→ stopped ─start→ starting ─ok→ running ─stop→ stopping ─→ stopped
                                       │            │
                                       └─fail→ errored ─reset→ stopped
                                                    │
                                                    └─delete (cascade kill if running)
   ```

   - 进程心跳 `last_heartbeat` 由 runner 主循环定期写入（如每 30s）
   - registry `reap` 命令检查 `last_heartbeat < now - 2 * interval` 的 running 实例，标记 `errored` 或回 `stopped`

4. **CLI 与 Dashboard 等价 CRUD**：

   ```bash
   copy-trader registry create --name hl-eth-kdj --venue hyperliquid.spot --account hl_eth \
       --strategy kdj_short_1h3m --mode paper
   copy-trader registry list [--status running]
   copy-trader registry start <id-or-name>
   copy-trader registry stop  <id-or-name>
   copy-trader registry update <id-or-name> --params '{"oversold": 18}'
   copy-trader registry delete <id-or-name>
   copy-trader registry reap
   ```

   Dashboard `/runners` 页面：列表 + 创建按钮 + 行内启停 + 编辑表单 + 删除确认。后端共用 `RunnerService`（`runners/service.py`），CLI 与 dashboard 都调用同一服务对象，确保行为完全一致。

5. **跨实例状态隔离**：

   - `position_<runner_id>.json` 而不是 `position_<strategy>_<account>.json`
   - ledger 行新增 `runner_id` 列（与 `env_tag/machine_id` 同期落地）；reconcile 按 `(account, runner_id)` 颗粒
   - notify 事件、log 文件、PID 文件全部按 `runner_id` 命名空间

6. **启动后的单进程多 runner**：早期 M2/M3 一个 runner 实例 = 一个独立进程（最简单）；M4 阶段视压力决定是否走 multi-runner-per-process（asyncio task 池或线程池）。本计划默认**一个 instance 一个进程**，`bin/deploy.sh` + systemd `copy-trader@<id>.service` 各管各。

**为什么这样设计**：

- 用户诉求是"创建新 runner 对应单独平台、执行单独策略"——把"实例化"显式建模为一等对象，比靠 CLI flag 临时拼接更可控
- 单进程隔离最简，避免一个 runner 崩溃带崩另一个；CPU 不是瓶颈
- registry 表 + 心跳让 dashboard 在不调任何活进程的情况下知道每个 runner 状态

**备选与拒绝原因**：

- *只在 yaml 中声明 runner 列表（静态）*：不能动态创建，违反用户"创建新 runner"诉求
- *用 K8s job / Celery worker*：单人项目过度
- *Asyncio 单进程多 runner*：策略代码现状是同步模型，混 async 会扩大 blast radius

### D9. 交付路线 M0–M5（自底向上）

每阶段独立可发布、可回滚：

- **M0 工具链与骨架**：`pyproject.toml` + `uv.lock` + `src/copy_trader/{core,config,cli}` + `tests/` + CI 三件套 + `copy-trader doctor` 跑通；`COPY_TRADER_HOME / COPY_TRADER_ENV` 解析 + lock 文件校验 + ledger schema 创建脚本；**配置 schema 一次性覆盖 accounts/capital_allocation/pyramid/fixed_position 四段**（D10），含 `LayerScope` 元数据
- **M1 PnL 单一来源骨架**：`pnl.PnlEngine` + `execution.reconciler.ReconcileService` + `persistence.ledger.TradesRepo`；`copy-trader reconcile` 子命令可对一个空账户跑通；PnL 黄金测试覆盖加权平均 + FIFO + 跨环境写入拒绝
- **M2 Binance spot 端到端**：`exchanges/binance/spot.py` + `marketdata/binance/` + `strategies/base.py` 与一个最小 hello-world 策略 + `runners/live.py`；`copy-trader run --strategy hello --account spot --mode dry-run` 跑通；paper exchange 同步实现，`copy-trader paper` 跑通
- **M3 Hyperliquid spot**：`exchanges/hyperliquid/spot.py` + `marketdata/hyperliquid/`；同一 hello-world 策略两个 venue 都能跑；reconcile 双 venue 并行；架构测试验证"加交易所只动一个子包"
- **M4 Backtest + Dashboard 设置中心 + Runner Lifecycle**：`runners/backtest.py` + `marketdata/cache/`（SQLite klines 缓存）+ `runners/service.py`（CLI 与 dashboard 共用的 RunnerService）+ `persistence/runner_registry.py`（实例表，D11） + `cli/registry {create,list,start,stop,update,delete,reap}` + `cli/dashboard`（FastAPI 设置中心：四段配置表单 + Runner CRUD + 启停 + 审计日志）；ROADMAP epic 接入点齐备
- **M5 生产部署**：systemd unit 模板 + `bin/deploy.sh / release.sh / rollback.sh`；prod 机灰度运行 ≥ 14 天，0 critical alert 后才结束本计划

每 milestone 一个 PR；本计划 archive 在 M5 灰度结束后执行。

## Risks / Trade-offs

- **[uv 还在快速演进]** 升级可能引入回归
  - **Mitigation**：`required-version` 钉死区间，CI 中固定 uv 版本；季度统一升级
- **[空仓库一次性塞太多骨架]** M0 工作量看起来比想象大
  - **Mitigation**：M0 骨架只造空类与 stub 实现，所有具体 venue/strategy 都留到 M2+；坚持"先骨架再业务"的顺序
- **[过度设计风险]** 单人项目搞 import-linter + Protocol + Pydantic Settings 可能被吐槽 over-engineering
  - **Mitigation**：autotrader 的两个具体痛点都是"该有的护栏没有"造成的，反例摆在面前；本项目就是要在第一行业务代码之前把这些护栏建好
- **[Pydantic / Typer 学习成本]** 此前若无相关经验
  - **Mitigation**：两个库都是社区主流、文档充分；M0 阶段做最小可用即可
- **[未来 ccxt-style 抽象遗憾]** 自己定 Protocol 而不用社区 ccxt 可能在第 5 个交易所开始痛
  - **Mitigation**：本计划仅承诺 Binance + Hyperliquid 两个 venue；第 3 个 venue 出现时再讨论是否引入 ccxt 作为后端 driver
- **[autotrader 经验复用边界]** 既然 autotrader 收益正向，是否应该 fork 而非新建
  - **拒绝原因**：用户明确要求独立项目；autotrader 的结构债清理成本 > 重写成本，复用经验而非代码是更优选择

## Migration Plan

不适用——本项目从空仓起步，没有迁移源。M0–M5 见 D9 的交付路线。

回滚策略：每个 milestone 之间有 git tag（`bootstrap/m<N>`）；如果 M2 或 M3 在生产灰度阶段出问题，回到上个 tag + `bin/rollback.sh`；M0–M1 阶段没有生产负载，回滚 = revert PR。
