## ADDED Requirements

### Requirement: Driver consumes one issue per cron wake-up

驱动 MUST 每次唤醒只处理一个 GitHub issue，处理完即退出。MUST NOT 在单次唤醒内连续消费多个 issue（避免单次时长超过 cron 间隔、上下文累积、失败 blast radius 扩大）。

#### Scenario: 唤醒消费一个 issue 后退出

- **WHEN** cron 在 20:05 唤醒 driver
- **THEN** driver 选定一个 issue、派发 subagent、跑本地校验、合 PR、关 issue，然后进程退出；下次唤醒（如 23:05）再选下一个

#### Scenario: 队列空时立即退出

- **WHEN** 没有任何符合条件的 open issue（全部 closed 或全部 stuck/wip/blocked）
- **THEN** driver 在 5 秒内退出（不空转），日志记录 "queue empty"

### Requirement: Issue queue is sorted by milestone, priority, then issue number

驱动选择下一个 issue 时 MUST 按以下三键排序：

1. **Milestone**：`bootstrap/m0` < `m1` < `m2` < `m3` < `m4` < `m5`（无 milestone 的 issue 排最后）
2. **Priority label**：`priority:p0` < `p1` < `p2`（无优先级标记排最后）
3. **Issue number**：升序（小号先）

只选状态为 `open` 且 deps_satisfied（issue body 中 "依赖 issue #N" 全部 closed）且未带 `stuck` / `wip` / `needs-human` label 的 issue。

#### Scenario: 跨 milestone 不抢跑

- **WHEN** m0 还有 1 个 p1 未完成、m1 已经有 p0 待办
- **THEN** driver 仍选 m0 的 p1（milestone 优先级高于 priority label）

#### Scenario: 同 milestone 同 priority 取小号

- **WHEN** m0 中有两个 p0 issue 都 deps_satisfied，号 #3 与 #6
- **THEN** driver 选 #3

#### Scenario: 依赖未满足跳过

- **WHEN** issue #15 body 含 "依赖 issue #14"，#14 仍 open
- **THEN** driver 跳过 #15，选下一个候选

### Requirement: One subagent per issue, no parallel issues

每个 issue 由一个独立 `general-purpose` Claude subagent 处理。同一时刻 MUST 仅有一个 subagent 在运行；MUST NOT 并行跑多个 issue（防止 main 合并冲突 + 让人类 reviewer 看清单一变更）。

driver 启动时 MUST 用 `flock` 锁住状态文件 `var/dev/state/driver_state.json`；如果锁已被其他进程持有 → 立即退出。

#### Scenario: cron 重叠时第二次唤醒退出

- **WHEN** 上一次唤醒未结束（极端情况 cron 误触发）
- **THEN** 第二次进程拿不到 flock，立即退出，日志记录 "lock contention, prev pid=N still running"

### Requirement: Subagent is bound by a strict prompt template

driver 派发 subagent 时 MUST 通过 `bin/auto_dev_subagent_prompt.md` 模板传入 issue 上下文。模板中 MUST 包含以下硬约束：

- 禁止 `git push origin main` 直推 main
- 禁止 `git rebase` 上游 main 后 force-push
- 禁止 `--no-verify` / `--force` / 改 git config / 改驱动自身（`bin/auto_dev_*`）
- 禁止安装 cron / 改 launchd / 改 systemd
- 必须只在 `feature/m<N>-<short>` 分支上工作
- 必须每完成一块独立可验证的小改动就 commit + push
- 必须在退出前跑完 `uv run pytest -q -m "not live" && uv run lint-imports && uv run ruff check . && uv run ruff format --check . && uv run mypy src/copy_trader`
- 必须把测试中所有交易所 / IM / HTTP 行情调用 mock 或 vcrpy 录制 fixture

subagent 退出码语义：
- `0` = 全本地校验通过，driver 接管开 PR + 合并
- `2` = 超时（≥ 45 分钟），已 commit + push WIP，driver 标该 issue stuck（计数 +1），下次再试
- `3` = needs-human（如 vcrpy fixture 缺失）；driver 加 `needs-human` label + 跳过
- 其他非零 = 失败，driver 计入连续失败计数

#### Scenario: subagent 试图改驱动自身被 review 拦截

- **WHEN** PR diff 含 `bin/auto_dev_loop.sh` 或 `bin/auto_dev_subagent_prompt.md` 的改动
- **THEN** driver 检测到后拒绝合并（pre-merge hook），把该 PR 标 stuck，要求人工 review

### Requirement: Local checks are the only merge gate; no live external API calls

合并 PR 到 main 的唯一硬条件 MUST 是本地校验全过。GitHub Actions 仍在 PR 触发跑（事后兜底），但 MUST NOT 作为合并阻塞——driver 用 `gh pr merge --squash --admin --delete-branch`。

测试 MUST NOT 调用任何真实交易所 / Slack / Telegram / Feishu / Dingtalk / Hyperliquid API。`pyproject.toml` MUST 把 `pytest-socket` 加入 dev deps，`pytest.ini_options.addopts` MUST 含 `--disable-socket --allow-unix-socket`，所有需要外联的测试 MUST 用 vcrpy fixture / respx mock。

带 `@pytest.mark.live` 标记的测试 MUST 仅在人工触发时跑；driver 用 `pytest -m "not live"` 跳过它们。

#### Scenario: 测试代码忘了 mock 真实 HTTP 请求

- **WHEN** 一个测试中残留 `requests.get("https://api.binance.com/...")`
- **THEN** pytest-socket 拦截 socket，测试以 SocketBlockedError 失败；driver 不会合并；subagent 收到失败信号

#### Scenario: GH Actions 红色不阻塞合并

- **WHEN** 本地 pytest 全过，driver 调 `gh pr merge --admin`，GH Actions 5 分钟后才跑完且红了
- **THEN** PR 已合并；下次 driver 唤醒时检查 `gh run list --branch main --limit 1`，发现 failure → halt

### Requirement: Driver state is persisted across cron invocations

driver MUST 把跨唤醒的状态写入 `var/dev/state/driver_state.json`，schema_version=1，至少含：

- `last_wake_ts`、`last_pid`
- `current_issue`、`current_branch`（若上次未跑完）
- `stuck_counter`（issue → count 映射）
- `consecutive_failures` 计数
- `completed_issues` 列表（issue / pr / merged_at / wake_ts）
- `halt_reason`（None 或字符串）
- `next_wake_eta`

driver 启动时先读状态、`flock` 加锁、再决定是否消费下一个 issue 或恢复上次未完成的工作。

#### Scenario: 上次唤醒中断后续跑

- **WHEN** 上次唤醒因系统休眠中断，state 中 `current_issue=#5`、`current_branch=feature/m0-pyproject`
- **THEN** 这次唤醒先 `git checkout` 该分支、检查最新 commit、决定是把 subagent 接着跑还是从头来；不会另选新 issue

### Requirement: Halt conditions are enforced and recoverable

driver MUST 在以下任一条件触发时立即写 `halt_reason` + 创建 `var/dev/.driver_pause` 锁文件并退出，**不再消费**任何 issue 直到人工删除该锁文件：

- `consecutive_failures >= 3`（连续 3 个 issue 整体失败）
- main 分支最新 GH Actions run 是 `failure`
- 磁盘剩余空间 < 5%
- klines.db 体积超过 50 GB
- 状态文件 schema_version 与代码不兼容

`stuck_counter[issue] >= 3` 触发的是**单 issue 跳过**：driver 给该 issue 加 `stuck` label + 评论说明，跳过该 issue 但继续消费队列下一个，**不**全局 halt。

人工恢复：`bin/install_auto_dev_cron.sh resume` 删除 `var/dev/.driver_pause`。

#### Scenario: 连续失败触发全局 halt

- **WHEN** issue #1 → 失败 / #2 → 失败 / #3 → 失败（cleanup 都已 commit WIP）
- **THEN** driver 写 `halt_reason="3 consecutive failures (issues 1, 2, 3)"`，置 `var/dev/.driver_pause`，退出；后续 cron 唤醒进入 driver 立即检测到锁文件，5 秒内退出

#### Scenario: 单 issue 卡住后跳过

- **WHEN** issue #15 在三次 wake 中均未通过本地测试
- **THEN** driver 给 #15 加 `stuck` label，评论 "stuck after 3 wake attempts"，**不**全局 halt；下次唤醒选下一个候选

### Requirement: Auto-merge is gated by post-merge sanity check

driver 在 `gh pr merge --admin` 之后 MUST 立即检查 main 分支 head 是否仍可 build / test：

- `git fetch origin main && git checkout main && git pull`
- `uv sync --frozen && uv run pytest -q -m "not live"`

如果失败 → `consecutive_failures += 1`，标 PR 关联 issue 为 reopened（`gh issue reopen <N>` + 评论"merged but main broke"），但**不**自动 revert（避免连环操作）；让下次唤醒判断是否 halt。

#### Scenario: merge 后 main 红

- **WHEN** PR #36 合并后，driver 在本地 pull main 跑测试报错
- **THEN** driver 把 issue #1 reopen，加 comment "auto-merged PR #36 broke main; manual revert needed"，`consecutive_failures += 1`，退出本次唤醒

### Requirement: Cron schedule is night-only with manual pause

driver 的 cron / launchd 调度 MUST 默认仅在夜间唤醒（macOS launchd `StartCalendarInterval` / Linux crontab）：20:05、23:05、02:05、05:05 各一次。`bin/install_auto_dev_cron.sh` MUST 提供：

- `install` / `uninstall` / `status`
- `pause`（写 `var/dev/.driver_pause`）/ `resume`（删除锁文件）
- `dry-run`（手动跑一次驱动，不真合 PR，开 PR 后自己 close 用于流程验证）

#### Scenario: 手工 pause 立即生效

- **WHEN** 用户执行 `bin/install_auto_dev_cron.sh pause`
- **THEN** 锁文件创建；下一次 cron 唤醒进入 driver 立即检测到 → 5 秒内退出，**不**消费任何 issue

#### Scenario: dry-run 不污染 main

- **WHEN** 用户执行 `bin/auto_dev_loop.sh dry-run`
- **THEN** driver 选 issue → 派发 subagent → 跑本地校验 → 开 PR → 立即 close PR（不合并）；issue 不被关闭，feature 分支自动删除

### Requirement: Driver is fully observable via logs and status

每次唤醒 MUST 产出：

- `logs/auto_dev_<wake_ts>.log`：driver 主流程日志（issue 选择、subagent 启动、本地校验输出、PR 操作）
- `logs/auto_dev_subagent_<issue>_<ts>.log`：subagent 完整 stream-json 输出
- `logs/auto_dev_cron.log`：单行汇总（appended）

`bin/install_auto_dev_cron.sh status` MUST 输出当前状态摘要：cron 是否启用、`var/dev/.driver_pause` 状态、最近 5 次唤醒的结果（成功 / 失败 / stuck）、当前 stuck 列表。

#### Scenario: status 命令输出可读摘要

- **WHEN** 用户执行 `bin/install_auto_dev_cron.sh status`
- **THEN** 输出含：cron 启用状态、上次唤醒 ts、上次结果、连续失败计数、stuck issue 列表、driver_pause 锁文件存在性

### Requirement: Driver MUST NOT modify itself or production safety files

driver 与 subagent MUST NOT 自动修改以下文件（pre-commit hook 拦截）：

- `bin/auto_dev_loop.sh`、`bin/install_auto_dev_cron.sh`、`bin/auto_dev_subagent_prompt.md`（驱动自身）
- 任何 `openspec/changes/<active-change>/` 下的 spec / proposal（运行时不能改设计）
- `.env`、`secrets/.env`、`COPY_TRADER_*` 命名的任何凭证文件
- `mockup/`（视觉前置物，独立维护）
- 主分支保护规则、CI workflow 配置（`.github/workflows/`）

如果 subagent 写入这些文件 → pre-commit hook 拦截 commit；driver 把该 issue 标为 `needs-human` 跳过。

#### Scenario: 试图改驱动自身被拦截

- **WHEN** subagent 试图修改 `bin/auto_dev_loop.sh` 并 commit
- **THEN** pre-commit hook 失败，commit 被拒；subagent 退出码 3（needs-human），driver 加 label 跳过

#### Scenario: 试图改 .env 被拦截

- **WHEN** subagent 试图把 API key 写到 `.env` 并 commit
- **THEN** pre-commit hook 拦截，提示"凭证文件不允许由 driver 修改"
