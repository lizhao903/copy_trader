> **Scope**：本变更只产出"夜间无人值守驱动"的脚手架与契约。它本身**不**实施 M0–M5 业务代码——业务由被驱动消费的 issue（#1–#11、#13–#35）自己负责。本变更 apply 完之后只是**驱动准备就绪**，是否启动夜间 cron 仍由用户决定（最后一步）。

## 1. 驱动主脚本与 prompt 模板

- [x] 1.1 写 `bin/auto_dev_loop.sh`：bash 主驱动脚本，含 flock 加锁、读 state.json、queue 选择、派发 subagent、收集退出码、开 PR、合 main、写 state；总长度 < 400 行
- [x] 1.2 写 `bin/auto_dev_subagent_prompt.md`：标准化 subagent 喂入模板（含 D3 全部硬约束 + 模板变量占位 `{{ISSUE_NUMBER}}` / `{{ISSUE_BODY}}` / `{{FEATURE_BRANCH}}` / `{{LOCAL_CHECK_COMMANDS}}` / `{{TIMEBOX_MIN}}`）
- [x] 1.3 写 `bin/auto_dev_pr_body_template.md`：driver 调 `gh pr create --body-file` 用的 PR body 模板（关联 issue / spec 引用 / 本地校验摘要 / driver 元信息）
- [x] 1.4 把 `var/dev/state/`、`var/dev/.driver_pause`、`logs/auto_dev_*.log` 加入 `.gitignore`

## 2. 队列选择算法实现

- [x] 2.1 在 `bin/auto_dev_loop.sh` 中写 `pick_next_issue()` shell 函数：调 `gh issue list --json number,title,labels,milestone,body --search "is:open"` 拉全量，用 `jq` 按 milestone+priority+number 排序，过滤 stuck/wip/needs-human/blocked label，依赖检查走 `body` 中 "依赖 issue #N" 正则
- [x] 2.2 单元测试占位：`tests/integration/test_auto_dev_queue.sh`（bats 或 plain bash assertions），覆盖 milestone 优先、priority 平局、依赖未满足跳过、stuck 跳过 4 个场景；用 fixture JSON 模拟 `gh issue list` 输出

## 3. Subagent 派发与退出码处理

- [x] 3.1 在驱动脚本中写 `dispatch_subagent()` 函数：替换 prompt 模板变量、调 `claude -p --dangerously-skip-permissions --add-dir <repo> --output-format stream-json` 喂入 prompt、把 stream-json 落 `logs/auto_dev_subagent_<issue>_<ts>.log`、捕获子进程退出码
- [x] 3.2 退出码分支：`0` → 进入 PR 流程；`2` → 标 stuck 计数 +1；`3` → 加 `needs-human` label + 跳过；其他非 0 → consecutive_failures +1
- [x] 3.3 超时保护：subagent 进程 ≥ 50 分钟（`TIMEBOX_MIN=45` + 5 分钟 grace）发 SIGTERM；30 秒后未退发 SIGKILL；等同于退出码 2 处理

## 4. 本地校验五件套 + 网络拦截

- [x] 4.1 在 driver 脚本中定义 `LOCAL_CHECK_COMMANDS` 变量串：`uv run pytest -q -m "not live" && uv run lint-imports && uv run ruff check . && uv run ruff format --check . && uv run mypy src/copy_trader`
- [x] 4.2 写 PR 创建前的"二次校验"：subagent 退出 0 后 driver 自己再跑一次本地校验（防止 push 后 base merge 进了新冲突）；失败 → consecutive_failures +1，PR 不开
- [x] 4.3 在 `pyproject.toml` 加 dev deps：`pytest-socket`、`vcrpy`、`respx`、`pytest-asyncio`（issue #1 实施时由对应 PR 落地，本 change 不动 pyproject；本 task 只在 docs 中要求 issue #1 必须含这些）
- [x] 4.4 在 `pyproject.toml [tool.pytest.ini_options]` 加 `addopts = "--disable-socket --allow-unix-socket"`（同 4.3，由 issue #1 落地）
- [x] 4.5 写 `tests/conftest.py` 占位：定义 `live` marker、配 `pytest-socket` 白名单（仅放行 SQLite unix socket）；同 4.3 由 issue #1 落地——本 change 在 docs 中明确要求

## 5. PR 创建与自动合并

- [x] 5.1 driver 中写 `open_and_merge_pr()` 函数：`gh pr create --base main --head <branch> --fill --title "<title>" --body-file pr_body_temp.md --label autonomous-driver`，捕获 PR 号
- [x] 5.2 二次本地校验通过后：`gh pr merge <PR> --squash --admin --delete-branch`
- [x] 5.3 关联 issue：`gh issue close <ISSUE> --comment "Resolved by #<PR> (auto-merged by driver at <ts>)"`
- [x] 5.4 post-merge sanity check：`git fetch && git checkout main && git pull && uv sync --frozen && uv run pytest -q -m "not live"`；失败 → reopen issue + comment "auto-merged PR broke main"，consecutive_failures +1（**不**自动 revert）

## 6. 状态文件与 halt 条件

- [x] 6.1 driver 启动时 `flock var/dev/state/driver_state.json -c "..."`：拿不到锁立即退出
- [x] 6.2 写 `read_state()` / `write_state()` shell 函数（用 `jq` 操作 JSON），支持 schema_version=1 字段集
- [x] 6.3 实现 halt 条件检查 `should_halt()`：检查 `var/dev/.driver_pause`、`consecutive_failures >= 3`、main 上次 GH Actions failure、磁盘 < 5%、klines.db > 50 GB、schema_version 不兼容
- [x] 6.4 实现 stuck 跳过 `should_skip_issue(N)`：`stuck_counter[N] >= 3` → `gh issue edit <N> --add-label stuck` + comment + skip
- [x] 6.5 单元测试占位：`tests/integration/test_auto_dev_halt.sh`，覆盖 5 个 halt 触发场景 + 1 个 skip 场景

## 7. Cron / launchd 安装脚本

- [x] 7.1 写 `bin/install_auto_dev_cron.sh`，子命令 `install / uninstall / status / pause / resume / dry-run`
- [x] 7.2 macOS：生成 `~/Library/LaunchAgents/io.copy_trader.auto_dev_loop.plist`，4 个 StartCalendarInterval（20:05、23:05、02:05、05:05），`launchctl load` 装载
- [x] 7.3 Linux：写入用户 crontab `5 20,23,2,5 * * *` 行，`crontab -l | grep auto_dev_loop` 检测重复装
- [x] 7.4 `dry-run` 模式：环境变量 `DRY_RUN=1` 注入 driver；driver 走完所有步骤但 PR 开了立即 `gh pr close <N> --delete-branch`，issue 不动
- [x] 7.5 `status` 输出：cron 启用状态、`var/dev/.driver_pause` 是否存在、最近 5 次唤醒结果（从 `auto_dev_cron.log` 读）、stuck issues 列表（从 GitHub label 读）

## 8. 安全护栏（pre-commit hook）

- [x] 8.1 在 `.pre-commit-config.yaml` 加自定义 hook：`forbid-driver-self-modification`，扫描 commit diff 中的 `bin/auto_dev_loop.sh` / `bin/install_auto_dev_cron.sh` / `bin/auto_dev_subagent_prompt.md` / `.github/workflows/` / `mockup/` / 任何 `_KEY|_SECRET|_TOKEN|_PRIVATE_KEY` 命名的文件，命中即拒绝（同 4.x 由 issue #6 CI 实施时落地一致；本 change 在 docs 中要求 issue #1 / #6 必须包括此 hook）
- [x] 8.2 在 `bin/auto_dev_loop.sh` 中独立内置 `check_subagent_diff_safe()` 双重护栏：subagent 退出后 driver 用 `git diff --name-only origin/main...HEAD` 自查，命中禁区直接 abort 不合并

## 9. 文档与回链

- [x] 9.1 在 `CLAUDE.md` 加 "夜间自动化驱动" 章节：解释 `bin/auto_dev_loop.sh` 行为、cron 时间窗、暂停/恢复方法、stuck 处置、人工 review 频率（建议每天早上抽查前一晚合并的 PR）
- [x] 9.2 在 `bin/auto_dev_loop.sh` 顶部写注释引用 `openspec/specs/autonomous-driver/spec.md` 与本 design.md
- [x] 9.3 README.md 不修改（驱动是后台基础设施，对仓库 onboard 流程透明）

## 10. 验收测试与首次 dry-run

- [x] 10.1 在 `tests/integration/test_auto_dev_loop.sh` 跑端到端 fake：用 mock `gh` 命令（PATH 注入 stub）、mock issue queue、模拟 subagent 用 sleep + echo 替代真实 claude 调用、断言 driver 流程正确
- [x] 10.2 手动跑 `bin/auto_dev_loop.sh dry-run` 一次（消费 issue #1 端到端，开 PR 后自己 close）：验证流程
- [x] 10.3 验证 halt 条件：手动制造 `var/dev/.driver_pause` → 跑 driver → 断言 5 秒内退出
- [x] 10.4 验证 status 输出：`bin/install_auto_dev_cron.sh status`

## 11. 启动决策（用户最后一步）

- [ ] 11.1 全部 1–10 节通过后，driver 处于"安装好但未启动 cron"状态
- [ ] 11.2 用户决定：
  - 选项 A：执行 `bin/install_auto_dev_cron.sh install` 当晚生效
  - 选项 B：保留 dry-run 工具，暂不启用 cron，等再过几天 review 流程
- [ ] 11.3 启用后，第一次自动唤醒发生 ≤ 24h 内人工抽查首批 PR；若发现 driver 写错代码合 main → 立即 `bin/install_auto_dev_cron.sh pause` + 修复

## 12. 提交与归档

- [x] 12.1 全部 task 完成后 commit `feat(driver): scaffold autonomous overnight bootstrap driver`
- [x] 12.2 push 到本仓 main（脚本本身不接触 issue 队列、不改 src/，低风险）
- [ ] 12.3 用户触发 `/opsx:archive autonomous-bootstrap-driver`
