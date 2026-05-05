# pnl-single-source Specification

## Purpose
TBD - created by archiving change bootstrap-architecture. Update Purpose after archive.
## Requirements
### Requirement: SQLite trade ledger is the single source of truth for fills

系统 MUST 把每一笔成交（fill）作为不可变记录写入 `db/trades.db` 的 ledger 表。Ledger 行 MUST 至少包含 `id, ts, account, symbol, side, qty, price, fee, fee_asset, exchange_order_id, env_tag, machine_id, schema_version`。所有持仓数量、成本基础、已实现 PnL、未实现 PnL 计算 MUST 来自该 ledger，MUST NOT 从 `state/position_*.json` 缓存或 ad-hoc `entry_price` 字段反推。

#### Scenario: 写入 ledger 失败时阻断下单链路

- **WHEN** broker 返回成交但 ledger 写入抛出异常
- **THEN** runner 立即停止当前账户的本轮策略循环、记录 critical alert、下一轮 reconcile 之前不再下新单

#### Scenario: 计算未实现 PnL 走 ledger

- **WHEN** dashboard 或日志请求账户 X 上 symbol Y 的 unrealized PnL
- **THEN** `PnlEngine` 从 ledger 重建持仓与加权平均成本，配合当前价格输出 unrealized，MUST NOT 读取 `state/position_*.json`

### Requirement: Position cache is rebuildable, never authoritative

系统 MUST 把 `state/position_*.json` 视为可由 ledger 重建的缓存。任何缓存与 ledger 重建结果不一致字段 MUST 被 ledger 重建结果覆盖。手工修改 cache 文件 MUST 在下一次 reconcile 时被丢弃。

#### Scenario: 缓存被人工编辑后启动

- **WHEN** 用户手动把 `position_*.json` 的 `entry_price` 改成虚假值并重启 runner
- **THEN** 启动 reconcile 用 ledger 重建出真实 entry_price 覆盖该字段，并在日志记录 `cache_overridden` 事件

#### Scenario: 缓存文件丢失

- **WHEN** `position_*.json` 不存在
- **THEN** 启动 reconcile 从 ledger 与交易所余额重建缓存；runner MUST NOT 因为缓存缺失而重新开仓

### Requirement: Startup reconcile compares ledger, exchange, and cache

每个 runner 进入主循环前 MUST 调用 `ReconcileService.reconcile(account)`，该服务 MUST 拉取交易所当前余额与持仓、从 ledger 重建预期持仓、与 cache 三方比对，差异写入 `logs/reconcile_<ts>.log`。差异类型分级：

- `cache_drift`（仅 cache 不一致） → 自动以 ledger 为准修正、继续启动
- `ledger_exchange_mismatch`（ledger 与交易所余额差异 > 容差） → 进入 SAFE 模式（仅平仓不开仓）并触发告警
- `unknown_position_on_exchange`（交易所有 ledger 中没有的仓位） → 拒绝启动，要求人工介入

#### Scenario: 只有 cache 漂移自动修正

- **WHEN** ledger 与交易所一致，但 `position_*.json` 的 `peak_price` 比 ledger 重建结果旧
- **THEN** reconcile 以 ledger + 当前价格重算 `peak_price`，写回 cache，runner 正常进入主循环

#### Scenario: 交易所有未知仓位拒绝启动

- **WHEN** 交易所余额含有 0.5 BTC，但 ledger 中本账户从未买入过 BTC
- **THEN** runner 退出并打印 `unknown_position_on_exchange`，要求人工通过 `copy-trader reconcile --apply --acknowledge-unknown` 手动处置

### Requirement: Ledger writes are stamped and gated by `env_tag` and `machine_id`

新写入的 ledger 行 MUST 携带当前进程的 `env_tag` 与 `machine_id`，且 MUST 与同账户上一行 `schema_version >= 2` 的 `(env_tag, machine_id)` 一致；不一致 MUST 被拒绝并触发 `cross_environment_write` 告警。`schema_version=1` 视为 legacy（本项目首次写入即 `schema_version=2`，legacy 语义保留供未来 backfill 工具使用）。

#### Scenario: 跨机器写入被拒绝

- **WHEN** machine A 上的 dev 进程持有 ledger 写入会话，同时 machine B 上的 prod 进程尝试写入同 account
- **THEN** machine B 的写入操作返回错误、ledger 不变、machine B runner 进入 SAFE 模式并告警

#### Scenario: 跨环境写入被拒绝

- **WHEN** 用户错误地把 `COPY_TRADER_ENV=dev` 进程指向 prod 用过的账户名
- **THEN** ledger 写入因 `env_tag` 不一致被拒，告警事件 `cross_environment_write` 记录在日志

### Requirement: Cost basis uses weighted average by default, FIFO available for reports

`PnlEngine` MUST 默认使用按 `(account, symbol)` 的加权平均法计算 cost basis 与 unrealized PnL；MUST 同时提供 FIFO 模式仅供报表场景。两种模式 MUST 在同一份 ledger 之上得到稳定可重放结果。

#### Scenario: 加仓后再止盈一半计算 realized PnL

- **WHEN** 账户有以下 fills（同 symbol）：买 1@100、买 1@110、卖 1@130
- **THEN** 加权平均模式 realized PnL = 1 *(130 - 105) = 25；FIFO 模式 realized PnL = 1* (130 - 100) = 30；两者均能从相同 ledger 复算

