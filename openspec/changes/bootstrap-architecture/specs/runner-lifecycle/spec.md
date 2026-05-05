## ADDED Requirements

### Requirement: Runner instances are first-class persisted entities

系统 MUST 以 `RunnerInstance` 数据模型作为运行时一等对象，定义至少含 `id`（uuid7）、`name`（用户友好名，账号内唯一）、`venue`（引用 `ExchangeRegistry` 名）、`account`（引用 `accounts.<name>`）、`strategy`（引用 `strategies.<name>`）、`params_override: dict`、`mode ∈ {live, paper, dry-run}`、`status`、`pid`、`started_at`、`last_heartbeat`、`created_at`、`updated_at`。

`persistence/runner_registry.py` MUST 把这些实例落到 SQLite `runner_instances` 表（含 `schema_version`），与 ledger 同库或独立库均可。PID 文件 MUST 仅作为进程层心跳辅助，registry 表 MUST 是 runner 定义与状态的唯一真相源。

#### Scenario: 创建新 runner 持久化

- **WHEN** 用户通过 CLI `copy-trader registry create --name hl-eth-kdj --venue hyperliquid.spot --account hl_eth --strategy kdj_short_1h3m --mode paper` 或 dashboard 表单创建实例
- **THEN** registry 表新增一行，状态为 `stopped`，分配 uuid7 id，返回该 id 给调用方

#### Scenario: PID 文件丢失但 registry 仍可信

- **WHEN** 用户误删 `$COPY_TRADER_HOME/pids/<runner_id>.pid`
- **THEN** registry 表仍记录该 runner 的最后心跳；`registry reap` 命令依据心跳判定状态，不依赖 PID 文件

### Requirement: Runner lifecycle is a finite state machine

Runner 状态机 MUST 严格遵循以下转移：

```
draft ─create→ stopped ─start→ starting ─ok→ running ─stop→ stopping ─→ stopped
                                       │            │
                                       └─fail→ errored ─reset→ stopped
                                                    │
                                                    └─delete (cascade kill if running)
```

非法转移 MUST 被 `RunnerService` 拒绝并返回结构化错误（含当前状态、目标状态、允许的下一步）。`status=running` 的 runner MUST 周期性写入 `last_heartbeat`（默认每 30 秒）；`registry reap` MUST 把超过 `2 * heartbeat_interval` 未更新的 running 实例标记为 `errored`。

#### Scenario: 重复 start 被拒绝

- **WHEN** 用户对已 `running` 的 runner 调 `registry start`
- **THEN** RunnerService 返回错误 `InvalidStateTransition`，含当前状态 `running`、目标 `starting`、合法转移列表

#### Scenario: 心跳超时被 reap 标错

- **WHEN** 一个 `running` 实例的 `last_heartbeat` 距今 > 60 秒（默认间隔 30s × 2）
- **THEN** 下一次 `registry reap` 把它标为 `errored` 并尝试 kill PID（若 PID 仍存在）

#### Scenario: 删除 running 实例 cascade kill

- **WHEN** 用户 `registry delete <id>` 一个仍 `running` 的实例
- **THEN** RunnerService 先发 stop（若 30 秒内未达 stopped 则 SIGKILL），再删除 registry 行；动作记录在 audit log

### Requirement: CLI and Dashboard share a single `RunnerService`

CLI 子命令 `copy-trader registry {create|update|delete|start|stop|list|reap}` 与 Dashboard `/runners` 路由 MUST 调用同一份 `runners/service.py::RunnerService`，确保两路 UX 行为完全一致。MUST NOT 在 CLI 与 dashboard 各实现一份生命周期逻辑。

#### Scenario: CLI 与 Dashboard 行为一致

- **WHEN** 同一个非法转移分别由 CLI 与 dashboard 触发
- **THEN** 两侧返回相同结构化错误码与提示

#### Scenario: Dashboard 启停透传给 RunnerService

- **WHEN** 用户在 dashboard `/runners` 点击行内 "Start" 按钮
- **THEN** 后端调用 `RunnerService.start(id)`，与 `copy-trader registry start <id>` 走同一代码路径

### Requirement: Runner state is fully namespaced by `runner_id`

每个 runner 实例的运行时状态 MUST 按 `runner_id` 命名空间彻底隔离：

- 持仓缓存：`state/position_<runner_id>.json`（替代旧式 `position_<strategy>_<account>.json`）
- ledger 行：新增 `runner_id TEXT NOT NULL`（与 `env_tag/machine_id/schema_version` 同期落地）
- PID 文件：`pids/<runner_id>.pid`
- 日志：`logs/run_<runner_id>.log` / `logs/trade_<runner_id>.log` / `logs/reconcile_<runner_id>_<ts>.log`
- 通知事件 metadata 含 `runner_id`

启动期 reconcile MUST 按 `(account, runner_id)` 颗粒；多个 runner 实例共享同一 account 时各自只对本实例产生的 fills 负责，相互不污染。

#### Scenario: 同账户两个 runner 的 PnL 互不干扰

- **WHEN** 同 `account=spot` 上有两个 runner 实例 A 与 B 各自跑不同策略
- **THEN** ledger 按 `runner_id` 区分；`PnlEngine.unrealized(account=spot, runner_id=A, ...)` 与 `runner_id=B` 各自只统计自己的 fills

#### Scenario: 删除 runner 不删 ledger 历史

- **WHEN** 用户删除一个 runner 实例
- **THEN** registry 表行删除，但 ledger 中该 `runner_id` 的历史 fills 保留（用于 audit 与 PnL 历史回放）；只是不再有新 fills 写入

### Requirement: One runner instance per OS process by default

默认部署形态 MUST 是「一个 RunnerInstance = 一个独立进程」，由 systemd `copy-trader@<runner_id>.service` 模板 unit 拉起；MUST NOT 在 M0–M5 范围内引入单进程多 runner 的并发模式（避免一个 runner 崩溃带崩其他）。

#### Scenario: 启动 runner 走 systemd 模板

- **WHEN** 用户在 dashboard 启动一个 prod 实例
- **THEN** 后端调用 `systemctl start copy-trader@<runner_id>.service`，systemd 启动独立进程；该进程退出不影响其他 runner

#### Scenario: 单进程多 runner 提案被拒

- **WHEN** 有人 PR 提议在单进程内用 asyncio 跑多个 runner
- **THEN** review 拒绝合并并指向本 spec；此能力 MUST 由独立 OpenSpec change 评估，超出本计划范围
