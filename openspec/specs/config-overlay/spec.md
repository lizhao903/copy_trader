# config-overlay Specification

## Purpose
TBD - created by archiving change bootstrap-architecture. Update Purpose after archive.
## Requirements
### Requirement: Configuration is loaded as Pydantic Settings with layered overlay

系统 MUST 通过 `copy_trader.config.settings.Settings`（基于 Pydantic Settings）加载配置，按以下优先级合并并产出最终 immutable settings 对象：

1. `config/base.yaml`（与环境无关默认值）
2. `config/<env>.yaml`（`<env>` 来自 `COPY_TRADER_ENV`）
3. `$COPY_TRADER_HOME/config.yaml`（机器本地覆盖，不入 git）
4. 环境变量（前缀 `COPY_TRADER_`，自动映射到嵌套字段）
5. CLI flag

任何缺失字段或类型错误 MUST 在启动期触发 Pydantic 校验错误，进程退出。

#### Scenario: 启动期校验缺失字段

- **WHEN** `prod.yaml` 漏配 `accounts.spot.symbols`
- **THEN** Pydantic 校验报错并指向缺失字段，进程不进入业务循环

#### Scenario: 机器本地覆盖被加载

- **WHEN** `$COPY_TRADER_HOME/config.yaml` 把 `notify.slack.channel_id` 改为生产值
- **THEN** 该字段覆盖 `prod.yaml` 中对应值；`copy-trader doctor` 输出能看到该字段来源为 local

### Requirement: Symbols, capital allocation, and strategy params live in YAML

`config/base.yaml` MUST 是符号、资金分配、策略参数等业务配置的主体；env-specific 仅在 `<env>.yaml` 中覆盖差异。仓库 MUST NOT 出现 `config/symbols_*.json`、`config/*capital_allocation*.json`、`config/strategy_*.json` 之类散落 JSON 文件，也 MUST NOT 出现 `*.bak.<timestamp>` 备份文件（git history 是事实备份）。

#### Scenario: 配置入口集中

- **WHEN** 用户阅读 `config/base.yaml`
- **THEN** `accounts.<name>.{symbols, capital, strategy_params}` 紧凑结构可见，无需打开多个 JSON 文件

#### Scenario: PR 引入散落 JSON 配置被拒

- **WHEN** PR 新增 `config/symbols_foo.json`
- **THEN** CI 静态检查作业失败、PR 不能合并

### Requirement: Secrets are never persisted in `config/*.yaml`

API key、secret、passphrase、Slack token、Hyperliquid 私钥等敏感凭证 MUST 仅通过 `$COPY_TRADER_HOME/secrets/.env`（开发机）或平台 secret manager（生产机的 systemd `EnvironmentFile`）注入到环境变量；`config/*.yaml` MUST 仅保存非敏感字段。`Settings` MUST 在校验阶段拒绝任何命名含 `_KEY`、`_SECRET`、`_TOKEN`、`_PRIVATE_KEY` 的字段从 yaml 中读出。

#### Scenario: yaml 中误填凭证被拦截

- **WHEN** 有人在 `dev.yaml` 写 `binance.spot.api_key: "AKxxx"`
- **THEN** Settings 校验报错并提示改用 `COPY_TRADER_BINANCE_SPOT_API_KEY` 环境变量

### Requirement: `copy-trader doctor` lists configuration provenance

`copy-trader doctor` MUST 输出最终生效的 settings（脱敏后）以及每个字段的来源层（base / env / local / envvar / cli），便于人工审计。

#### Scenario: doctor 显示配置来源

- **WHEN** 运维执行 `uv run copy-trader doctor`
- **THEN** 输出包含 `config_sources` 段，每个被覆盖字段标注来源；含 `_KEY/_SECRET/_TOKEN/_PRIVATE_KEY` 命名的字段值被掩码为 `<redacted>`

### Requirement: Configuration changes to base/env yaml are PR-only

任何对 `config/base.yaml`、`config/<env>.yaml` 的变更 MUST 通过 git PR 走 review；机器本地 `$COPY_TRADER_HOME/config.yaml` 的差异 MUST 由 `copy-trader doctor` 显式列出。生产机 MUST NOT 直接编辑 git 中的 yaml；Dashboard 设置中心写 base/env 时 MUST 改为产生 `git workdir` 改动并由用户触发 draft PR（见下条）。

#### Scenario: 生产机直接编辑被拦截

- **WHEN** 部署脚本 `bin/deploy.sh` 检测到生产机本地 git workdir 与远端不一致
- **THEN** 部署中止并打印 dirty 文件列表

### Requirement: Settings schema covers accounts, capital allocation, pyramid, fixed position

`Settings` 模型 MUST 在 M0 阶段就完整声明以下四段必填业务字段，并暴露给 Dashboard 设置中心使用：

- `accounts: dict[str, AccountConfig]` — 每账户至少含 `venue`（引用 ExchangeRegistry 名）、`enabled`、`credentials_alias`（指向 env vars 命名前缀）、`symbols: list[str]`
- `capital_allocation: list[CapitalSlice]` — 颗粒 `(account, strategy)`，含 `quote_asset`、`max_quote_amount`、`reserve_quote_amount`
- `pyramid: list[PyramidConfig]` — 颗粒 `(account, strategy)`，含 `enabled`、`first_entry_fraction`、`add_trigger_pct`、`reserve_quote_usdt`、`max_rounds`
- `fixed_position: list[FixedPositionConfig]` — 颗粒 `(account, strategy)`，含 `mode ∈ {fixed_qty, fixed_quote}`、`qty | quote_amount`、可选 `max_price`；同 `(account, strategy)` 与 `pyramid.enabled=true` 互斥（校验阶段拒绝同时存在）

每段 MUST 在 `base.yaml` 至少有一份样例条目；`<env>.yaml` 与 local 层可覆盖具体数值。

#### Scenario: 配置缺四段中任一段时启动报错

- **WHEN** `dev.yaml` 漏配 `pyramid` 而 base.yaml 也未提供默认
- **THEN** Pydantic 校验失败并指向缺失字段

#### Scenario: pyramid 与 fixed_position 互斥校验

- **WHEN** 同时为 `(account=spot, strategy=kdj_short_1h3m)` 配置 `pyramid.enabled=true` 与 `fixed_position` 条目
- **THEN** Settings 校验报错，提示二选一

### Requirement: Each settings field declares a `LayerScope`

`Settings` 中每个字段（递归到嵌套字段）MUST 通过 pydantic Field metadata 标注 `layer_scope ∈ {base, env, local}`，标识允许写入的最高层：

- `base`：仅允许在 `config/base.yaml` 编辑（含从 dashboard 走 PR 时写到 base）
- `env`：允许 `<env>.yaml`（PR） 与 base
- `local`：允许 local（直接写）/ env / base 任意层

`Settings.field_layer_map() -> dict[str, LayerScope]` MUST 作为后端 API；`copy-trader doctor --schema` MUST 输出该映射；Dashboard 设置中心 MUST 使用此映射决定每个字段的渲染（只读 / 可编辑）与写入路径。

#### Scenario: doctor 输出字段层映射

- **WHEN** 执行 `uv run copy-trader doctor --schema`
- **THEN** 输出 JSON 含每个字段路径与 `layer_scope`，例如 `accounts.<name>.venue: base`、`pyramid[].add_trigger_pct: local`

### Requirement: Dashboard settings center performs layered writes

Dashboard 提供"设置中心"页面，按 D10 设计 MUST：

- 渲染所有 Settings 字段为表单；只读 / 可写状态由 `field_layer_map()` 决定
- 写 `local` 层字段 → 直接修改 `$COPY_TRADER_HOME/config.yaml` 并触发 settings 热加载
- 写 `env` / `base` 层字段 → 改动写到 git workdir 中对应 yaml；用户触发"创建 draft PR"按钮时调用 `gh pr create --draft -B main`，MUST NOT 自动 commit-push 到 main
- 不显示敏感字段（`*_KEY/*_SECRET/*_TOKEN/*_PRIVATE_KEY`）原值；仅显示是否存在与最后修改时间，编辑指引引导到 `secrets/.env`
- 每次写入追加到 `$COPY_TRADER_HOME/logs/dashboard_audit.log`（字段路径、层、before/after 掩码后）

#### Scenario: 编辑 local 层字段立即生效

- **WHEN** 用户在 dashboard 设置中心把 `pyramid[0].add_trigger_pct` 从 1.5 改为 1.8 并保存
- **THEN** `$COPY_TRADER_HOME/config.yaml` 被更新；下一轮 runner 主循环读到新值；审计日志含此次变更

#### Scenario: 编辑 base 层字段触发 PR

- **WHEN** 用户在 dashboard 设置中心新增账户 `accounts.new_acct`
- **THEN** 改动写入 `config/base.yaml`，UI 展示 `git diff`；用户点击"创建 draft PR"时调用 `gh pr create --draft`；MUST NOT 自动 push 到 main 或当前分支

#### Scenario: 敏感字段不暴露

- **WHEN** 用户访问设置中心
- **THEN** `BINANCE_SPOT_API_KEY` 等字段仅显示 `<set>` 或 `<not set>`，无法在 UI 中读出明文

