# runtime-isolation Specification

## Purpose
TBD - created by archiving change bootstrap-architecture. Update Purpose after archive.
## Requirements
### Requirement: Runtime root resolves from `COPY_TRADER_HOME` and `COPY_TRADER_ENV`

系统 MUST 在每个进程启动时从两个变量解析运行时根目录：必填的 `COPY_TRADER_ENV ∈ {dev, paper, prod}`、可选的 `COPY_TRADER_HOME`（默认 `dev → ./var/dev/`、`paper → ./var/paper/`、`prod → /var/lib/copy_trader/`）。所有 state、logs、pids、db、secrets 写入路径 MUST 从 `$COPY_TRADER_HOME/{state,logs,pids,db,secrets}/` 派生。任何代码 MUST NOT 通过 CWD、源码相对路径或写死绝对路径访问可变运行时数据。

#### Scenario: 缺失环境变量时拒绝启动

- **WHEN** 进程启动且 `COPY_TRADER_ENV` 未设置
- **THEN** 进程在加载任何业务模块前 fail-fast 退出，错误信息列出受支持的取值与示例

#### Scenario: 解析顺序遵循 CLI > env > 默认

- **WHEN** 用户同时通过 `--home /tmp/foo`、`COPY_TRADER_HOME=/tmp/bar` 与默认值启动 `copy-trader run`
- **THEN** 进程使用 `/tmp/foo` 作为运行时根并在启动日志记录最终值与来源

#### Scenario: 启动期创建子目录

- **WHEN** `$COPY_TRADER_HOME` 存在但缺少 `state/`、`logs/`、`pids/`、`db/`、`secrets/` 之一
- **THEN** 进程以 `0700` 权限创建缺失目录并继续启动

### Requirement: Runtime lock file gates cross-environment and cross-machine usage

系统 MUST 在启动期写入 `$COPY_TRADER_HOME/state/.runtime_lock.json`，记录 `{env_tag, machine_id, schema_version, pid, started_at}`，其中 `machine_id` 来自 `$COPY_TRADER_HOME/state/.machine_id`（首次启动生成的 UUID）。如果该锁文件已存在且 `env_tag` 或 `machine_id` 与当前进程不一致，进程 MUST 拒绝启动并打印对比信息。

#### Scenario: 同机器同环境重启允许

- **WHEN** 进程在本机 `prod` 环境正常退出后再次启动
- **THEN** 锁文件被覆盖、`pid`/`started_at` 更新、进程正常进入业务循环

#### Scenario: 跨环境共享根目录直接拦截

- **WHEN** 用户错误地把 `COPY_TRADER_ENV=dev` 指向已经被 `prod` 进程使用过的 `$COPY_TRADER_HOME`
- **THEN** 进程在锁文件比对阶段退出，错误信息打印两侧 `env_tag/machine_id` 与建议修复步骤

#### Scenario: 跨机器复制状态目录被拦截

- **WHEN** 用户把生产机的 `$COPY_TRADER_HOME` 整体 rsync 到开发机后启动同环境进程
- **THEN** `machine_id` 比对失败、进程退出；恢复方法在错误信息中提示用户重新初始化或显式 `--reset-machine-id` 并清空 state

### Requirement: No production code path may reference legacy project-root state locations

仓库 MUST NOT 在 `src/` 与 `tests/` 中含有任何对仓库根 `trade_info/`、`logs/`、`klines.db`、`*.pid` 之类历史路径（autotrader 形态）的引用。CI MUST 通过静态扫描阻止此类引用合并入主干。

#### Scenario: CI 拦截重新引入项目根 state 路径

- **WHEN** PR 中新增 `os.path.join(ROOT, "trade_info", ...)` 之类引用
- **THEN** CI 路径扫描作业失败并指出违规文件与行号

### Requirement: `copy-trader doctor` reports runtime root and lock state

CLI 子命令 `doctor` MUST 输出当前进程解析到的 `COPY_TRADER_HOME`、`COPY_TRADER_ENV`、`machine_id`、当前锁文件状态、子目录可写性、ledger schema_version。MUST 不修改任何状态。

#### Scenario: doctor 在锁不一致时仍可读

- **WHEN** 锁文件存在但 `env_tag` 与当前进程不一致
- **THEN** `doctor` 不会触发 fail-fast，而是把不一致项作为告警打印，便于人工排查

