## Context

`bootstrap-architecture` 已经把 `copy_trader` 从空仓的整改路线分解为 34 个 GitHub issue（#1–#11、#13–#35）跨 6 个 milestone（`bootstrap/m0` … `bootstrap/m5`），每个 issue body 含 spec 链接 + 验收清单 + label。`mockup/` 已沉淀完整 UI 设计。**当前仓库主分支没有任何 src 代码**——所有 issue 都是从 0 开始建。

用户期望：

- 夜间无人值守消费这批 issue
- 每个 issue 起独立 subagent 写代码
- 本地校验通过后即合 main，无需人工 review
- 不依赖外部 API 测试（交易所、Slack 等真实调用一律 mock）

参照系：autotrader 仓库的 `auto_dev_loop`（CLAUDE.md 中详述）已经在单人项目跑通了类似模式：cron 启动、`claude -p --dangerously-skip-permissions` 模式、`gh pr merge --squash --admin` 自动合并、stuck 跳过策略。本变更把这套模式"copy_trader 化"，并用更严的本地测试边界把"不踩生产风险"放第一位。

约束：

- copy_trader 涉及真金白银（M5 上线后），驱动写错代码合到 main 最坏会引入 PnL 计算 bug；本变更通过"本地测试 + import-linter + mypy + ruff + pytest-socket 网络拦截"五件套尽量降低
- 驱动自身**不能**调用任何交易所 / Slack / Hyperliquid 真实 API；如果某个 M2/M3 issue 的测试需要 vcrpy 录制 fixture，必须在录制阶段由人工一次性介入，自动驱动只能消费已录制的 fixture
- 驱动单次唤醒受系统级 cron 时间限制，不能跑超过 ~60 分钟；超时则杀掉 subagent，下次续

## Goals / Non-Goals

**Goals:**

- G1：夜间 4 次唤醒（20:05 / 23:05 / 02:05 / 05:05）每次消费一个 issue，端到端完成"领任务 → 写代码 → 测试 → 合 PR"
- G2：本地测试是 merge 的唯一硬门（GH Actions 仍会跑作为事后兜底，但 driver 用 `--admin` 不等它）
- G3：所有外部网络调用（交易所、IM、HTTP 行情）在测试中 MUST 被拦截或 mock；`pytest-socket --disable-socket` 作为兜底护栏
- G4：halt 条件覆盖"代码错"+"环境错"+"人工急停"三类
- G5：驱动状态可持久（断点续传）、可观测（日志 + 状态文件 + Slack 可选通知）
- G6：本变更产出**脚手架** + **契约**，不产出策略业务代码（业务由被驱动的 issue 自己负责）

**Non-Goals:**

- 不并行执行多个 issue（避免 main 冲突）
- 不自动跑 GitHub 集成测试 / E2E
- 不替换人工 review——M5 生产部署的灰度判定仍需人工审视
- 不引入复杂的 task queue（Celery / RQ 等）；状态文件 + cron + bash 已经够单人项目
- 不取代 `bootstrap-architecture` 的 milestone 顺序——驱动严格按 m0→m5 推进
- 驱动自身**不**实施 M0–M5 的功能，它只是 orchestrator

## Decisions

### D1. 顺序模型：单进程单 issue + cron 续跑

**决定**：每次 cron 唤醒做一件事——选一个 issue → 跑完 → 退出。**不**保留长跑 daemon。

**为什么**：

- cron 是最稳的调度（系统级，不需要额外服务）
- 单 issue 单进程让超时 / 内存 / 上下文都易掌握
- 失败影响只限当次唤醒，下次干净重启

**备选拒绝原因**：

- *单进程长跑（systemd service）*：超时不可控，subagent 上下文累积 → token 爆炸；崩溃要救援
- *并行多 issue*：feature 分支互相不冲突理论上可行，但 PR 合 main 时序竞争太复杂；非 P0
- *Celery / 任务队列*：单人项目过度

### D2. 队列选择算法

**决定**：每次唤醒，driver 用一段 SQL-like 逻辑从 GitHub issues 选下一个 issue：

```
SELECT issue
FROM open_issues_in_lizhao903_copy_trader
WHERE state = 'open'
  AND not stuck (issue 没被标 stuck label)
  AND not work_in_progress (issue 没被标 wip label，即没有别的 PR 在跑)
  AND deps_satisfied (issue body 中"依赖 issue #N"全部 closed)
ORDER BY
  milestone_order ASC,    -- m0 先，m5 最后
  priority_order ASC,     -- p0 > p1 > p2
  issue_number ASC        -- 同等条件下取小号
LIMIT 1
```

`milestone_order` 由 milestone title `bootstrap/m<N>` 中的 `<N>` 决定。`priority_order` 由 label `priority:p0/p1/p2` 决定。

实现：`gh issue list --json number,title,labels,milestone --search "no:label:stuck no:label:wip"`，driver 用 `jq` / python 排序选第一个。

**为什么**：

- milestone 顺序保证不上 M2 之前不跑 M3
- priority 让 p0 先做（基础设施 / 契约性任务）
- issue_number 平局打破规则，可重放

### D3. Subagent 派发协议

**决定**：driver 把每个 issue 派给独立 `general-purpose` Claude subagent，调用方式：

```bash
claude -p --dangerously-skip-permissions \
  --output-format stream-json \
  --add-dir /Volumes/project/git_0xBroleez/copy_trader \
  < bin/auto_dev_subagent_prompt.md \
  > logs/auto_dev_subagent_<issue>_<ts>.log
```

`bin/auto_dev_subagent_prompt.md` 是标准化模板，驱动把以下变量替换进去：

- `{{ISSUE_NUMBER}}`、`{{ISSUE_TITLE}}`、`{{ISSUE_BODY}}`（含 spec 链接 + 验收）
- `{{FEATURE_BRANCH}}` = `feature/m<N>-<short>` （由 driver 创建）
- `{{LOCAL_CHECK_COMMANDS}}`（uv sync、pytest、lint-imports、ruff、mypy、grep）
- `{{TIMEBOX_MIN}}` = 45 分钟（cron 60 分钟 budget 的 75%，留 15 分钟给 driver 自己跑测试 + 开 PR）

模板内**硬性要求** subagent：

1. 只在 `{{FEATURE_BRANCH}}` 上工作，禁止 push main
2. 实现 issue 验收清单的全部子项
3. 每完成一块独立可验证的改动 → `git add <specific files> && git commit && git push`
4. 跑 `{{LOCAL_CHECK_COMMANDS}}` 全绿后退出
5. 测试**必须** mock 外部 API；如果该 issue 涉及交易所 adapter，用 vcrpy 录制 fixture 并提示 driver "needs human recording"
6. 禁止 `--no-verify` / `--force` / 改 git config / 装 cron / 改驱动自身

subagent 退出码：
- `0`：本地校验全过，等 driver 开 PR
- `2`：超时，已 commit + push WIP；driver 标 stuck（计数 +1），下次再试
- `3`：发现需要人工介入（如录 vcrpy）；driver 加 `needs-human` label，跳过
- 其他非 0：失败，driver 计入连续失败计数

### D4. 本地测试边界（硬约束）

**决定**：

1. `pyproject.toml [tool.uv].dev-dependencies` 加：
   - `pytest-socket`（默认拦截所有 socket 操作）
   - `vcrpy`（HTTP 录制回放）
   - `respx`（httpx mock）
   - `pytest-asyncio`（如未来需要）
2. `pyproject.toml [tool.pytest.ini_options]`：
   - `addopts = "--disable-socket --allow-unix-socket"`
   - `markers = ["live: 需要真实外部 API（驱动跳过）"]`
3. CI 与驱动跑测试时使用 `pytest -q -m "not live"`；任何 `@pytest.mark.live` 标记的测试由人工触发，driver 不跑
4. 每个 venue adapter 的契约测试 MUST 用 vcrpy fixture 或 respx mock；fixture 录制由人工一次性介入：
   - `tests/exchanges/binance/cassettes/<test_name>.yaml`
   - `tests/exchanges/hyperliquid/cassettes/<test_name>.yaml`
5. driver subagent 在本地跑 `uv run pytest -q -m "not live"`；如果发现 fixture 缺失，subagent 退出码 `3`（needs-human）

**为什么**：单人项目晚上没人盯，唯一负担得起的安全网就是"代码连真实 API 都连不上"。pytest-socket 是最后兜底——即使测试代码忘了 mock，socket 操作直接 OSError。

### D5. PR 与合并协议

**决定**：

```bash
# subagent 退出 0 后，driver 接管：
git checkout {{FEATURE_BRANCH}}
gh pr create --base main --head {{FEATURE_BRANCH}} \
  --title "$(head -1 <<< "$ISSUE_TITLE")" \
  --body "$(cat .pr_body_template.md)" \
  --label autonomous-driver
PR_NUM=$(gh pr view --json number -q .number)

# 等本地测试再跑一次（防止 push 后 base merge 进了新冲突）
uv sync --frozen && uv run pytest -q -m "not live" || exit 4

# auto-merge with admin（绕 GH Actions 等待）
gh pr merge "$PR_NUM" --squash --admin --delete-branch

# 关 issue（关联 PR）
gh issue close {{ISSUE_NUMBER}} --comment "Resolved by #$PR_NUM (auto-merged by driver)"
```

PR body 模板含：
- 关联 issue 号（`Closes #N`）
- spec 引用
- 本地校验输出摘要（pytest pass count、ruff clean、import-linter pass）
- driver 元信息（commit count、subagent 用时、wake timestamp）

**为什么 --admin**：

- driver 跑完本地全绿，再等 GH Actions 5–10 分钟没意义
- GH Actions 仍会在 PR 触发跑，但作为事后兜底（red 触发 halt）
- main 分支不设 required reviewers；这是单人项目可接受的取舍

**安全网**：每次合并后 driver 检查 `gh run list --branch main --limit 1 --json conclusion`，如果上次 main run 是 `failure` → halt 整个驱动直到人工修

### D6. 状态文件 schema

`var/dev/state/driver_state.json`：

```json
{
  "schema_version": 1,
  "last_wake_ts": "2026-05-06T20:05:00Z",
  "last_pid": 48213,
  "current_issue": null,
  "current_branch": null,
  "stuck_counter": {
    "12345": 1
  },
  "consecutive_failures": 0,
  "completed_issues": [
    {"issue": 1, "pr": 36, "merged_at": "2026-05-06T20:42:00Z", "wake_ts": "2026-05-06T20:05:00Z"}
  ],
  "halt_reason": null,
  "next_wake_eta": "2026-05-06T23:05:00Z"
}
```

driver 启动先 `flock` 状态文件，避免 cron 重叠（虽然 cron 间隔 3h 远大于 60min budget，仍加锁）。

### D7. Halt 条件 + 人工急停

| 条件 | 检测方法 | 动作 |
|---|---|---|
| `var/dev/.driver_pause` 锁文件存在 | `test -f` | 立即退出（不消费 issue） |
| 连续 3 个 issue 失败 | `consecutive_failures >= 3` | 写 halt_reason，置 `var/dev/.driver_pause`，发 critical alert |
| main 分支最新 GH Actions failure | `gh run list --branch main --limit 1` | 同上 |
| stuck issue 同号 3 次 | `stuck_counter[N] >= 3` | 给该 issue 加 `stuck` label + comment，跳过；不全局 halt |
| 磁盘 < 5% / klines.db > 50GB | `df` / `stat` | halt + alert |
| 超过 60 分钟未完成本次唤醒 | cron 自身的 SIGTERM 或 driver 内部 timer | kill subagent，commit WIP，退出 |

人工恢复：删除 `var/dev/.driver_pause` 即继续。

### D8. Cron 集成

`bin/install_auto_dev_cron.sh`：

- macOS：写 `~/Library/LaunchAgents/io.copy_trader.auto_dev_loop.plist`，4 个 StartCalendarInterval（20:05、23:05、02:05、05:05），调用 `bin/auto_dev_loop.sh` 并把 stdout/stderr 重定向到 `logs/auto_dev_cron.log`
- Linux：写 crontab `5 20,23,2,5 * * * cd /path/to/copy_trader && bin/auto_dev_loop.sh >> logs/auto_dev_cron.log 2>&1`

子命令：
- `install`：装 cron
- `uninstall`：卸载
- `status`：查看当前 cron 状态 + 最近一次 wake 摘要
- `pause`：写 `var/dev/.driver_pause`
- `resume`：删 `var/dev/.driver_pause`
- `dry-run`：手动跑一次驱动（不真合 PR，开 PR 后自己关）——用于首次验证

### D9. 预算与限速

每次唤醒：
- 总时长 ≤ 60 分钟（cron 间隔 3h，远超）
- 一个 issue
- subagent token 不超 X（用 claude --max-turns 间接限制）

每夜：
- 4 次唤醒 ≈ 4 个 issue
- 一周可推进 ~28 issue（理想）；实际 M2/M3 复杂会更慢

每周：
- 周日 6:00 driver 自动 pause + 发周报（最近 7 天合并的 issue 列表 + halt 历史 + 失败率）

### D10. 日志与可观测

- 每次唤醒：`logs/auto_dev_<wake_ts>.log`（驱动主流程）+ `logs/auto_dev_subagent_<issue>_<ts>.log`（subagent 详细输出）
- cron 汇总：`logs/auto_dev_cron.log`（每行一次唤醒）
- Slack 可选：如果 `SLACK_WEBHOOK_URL` 已配置，每次成功合并 / halt / pause 推一条；驱动启动时探测，未配置则跳过通知（不阻塞）

## Risks / Trade-offs

- **[subagent 写错策略代码]** 直接合 main 后才发现
  - **Mitigation**：本地测试 + import-linter + mypy + ruff 五件套；M5 之前 main 没有真实生产部署；CI 红事后兜底
- **[--admin 合并绕过 review]** 没有人工把关
  - **Mitigation**：单人项目可接受；M5 切到生产后切回 review-required（手动改 main 保护规则）
- **[cron 错过（笔记本休眠）]** 当晚不推进
  - **Mitigation**：自然失败；下次唤醒续；不补偿不重要
- **[vcrpy fixture 需要人工录制]** 卡 M2/M3
  - **Mitigation**：subagent 检测到缺失立即 needs-human 跳过；用户白天批量录制
- **[token 成本爆涨]** 4 wake/晚 × 30 天 × 上下文
  - **Mitigation**：每次唤醒重启上下文（cron 重启 claude 进程）；subagent 间无状态共享，只共享 git
- **[issue 排序错]** 不按依赖跑会写错
  - **Mitigation**：deps_satisfied 检查严格走 issue body 中"依赖 issue #N"；缺失时跳过本 issue 等下个唤醒重排
- **[main 一直红]** halt 后没人修
  - **Mitigation**：驱动 pause 锁存在，靠日常手动审查；周报推 Slack 兜底
- **[与 mockup 时序冲突]** mockup 是 light/dark 静态展示，与 driver 推进的 src 实装无直接 race；不冲突

## Migration Plan

不适用——本变更是从零起步的脚手架。回滚方法：

1. `bin/install_auto_dev_cron.sh uninstall` 卸载 cron
2. 删除 `bin/auto_dev_loop.sh` / `bin/auto_dev_subagent_prompt.md` / `var/dev/state/`
3. 已被驱动自动合并的 issue PR：保留（commit history 已沉淀），revert 走正常 git revert 流程

启动顺序（在 /opsx:apply 阶段）：

1. 实装 driver 脚本与 prompt 模板
2. 在 main 分支跑 `bin/auto_dev_loop.sh dry-run` 一次（消费 issue #1 端到端，含 PR 开关）—— **不**真合，验证流程
3. 验证无误后 `bin/install_auto_dev_cron.sh install`
4. 第一次自动唤醒发生后 ≤ 24h 内人工抽查首批 PR
5. 一周后总结失败率，若 < 20% 则继续；> 20% 则降频或转人工
