# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

This is a greenfield repo for a **copy-trade system** (`copy_trader`). At the time of writing the only committed content is the README, LICENSE, `.gitignore`, and the OpenSpec workflow scaffolding under `openspec/` and the various `.<tool>/` directories. There is **no source code yet** — no build system, no test runner, no language commands to document. The Python-style `.gitignore` is the only signal about the intended stack; treat it as a hint, not a commitment.

When the user asks you to start building, expect to bootstrap the project structure (package layout, dependency manager, test runner) as the first concrete change.

## Working through OpenSpec

All non-trivial work in this repo is meant to flow through the **OpenSpec spec-driven workflow** before code is written. The `openspec` CLI must be on PATH; `openspec/config.yaml` declares `schema: spec-driven`.

Four stages, exposed as both slash commands and skills:

1. `/opsx:explore` — think through the problem before committing to a proposal.
2. `/opsx:propose <name-or-description>` — `openspec new change "<name>"` then generate `proposal.md`, `design.md`, `tasks.md` under `openspec/changes/<name>/`. Drive artifact order from `openspec status --change "<name>" --json` and pull per-artifact guidance from `openspec instructions <artifact-id> --change "<name>" --json`.
3. `/opsx:apply [<name>]` — read context files listed by `openspec instructions apply --change "<name>" --json`, then implement `tasks.md` one item at a time, flipping `- [ ]` → `- [x]` as you go.
4. `/opsx:archive` — finalize a completed change.

Key rules when authoring artifacts:

- The `context` and `rules` fields returned by `openspec instructions` are **constraints for you**, not content to paste into the artifact file. Never copy `<context>`, `<rules>`, or `<project_context>` blocks into the output.
- Use the returned `template` as the structure of the file you write.
- Always read completed dependency artifacts before generating a new one.
- Re-run `openspec status --change "<name>" --json` after each artifact to confirm `applyRequires` items are `done`.

OpenSpec 在每个 harness 自带的 skill 安装路径里下发同名 skill；这些 IDE 镜像目录（`.claude/`、`.cursor/`、`.codex/`、`.gemini/`、`.qoder/`、`.qwen/`、`.trae/`）已加入 `.gitignore`，**不入库**。需要在新 harness 启用时各自走 OpenSpec 安装即可，无需跨 harness 同步本地副本。

## When there is no spec yet

If the user asks for code changes without an existing OpenSpec change, default to suggesting `/opsx:propose` first rather than writing code directly. Skipping the proposal stage is acceptable only for trivial fixes (typos, doc tweaks, gitignore adjustments) or when the user explicitly says so.

## Autonomous driver（夜间无人值守）

`bin/auto_dev_loop.sh` 是「驱动」脚本：cron 每次唤醒消费**一个** GitHub issue（#1–#11、#13–#35），派发独立 `claude -p` subagent 实现 issue 验收清单 → 跑本地校验五件套 → `gh pr merge --admin` 合 main → 关 issue → 退出。状态在 `var/dev/state/driver_state.json`，日志在 `logs/auto_dev_*.log`。完整契约：`openspec/specs/autonomous-driver/spec.md`。

### 控制命令

```bash
# 一次手动唤醒（流程验证）
bin/auto_dev_loop.sh
DRY_RUN=1 bin/auto_dev_loop.sh             # 开 PR 后立即 close 不合并

# 装/卸夜间 cron（macOS launchd 或 Linux crontab）
bin/install_auto_dev_cron.sh install       # 20:05 / 23:05 / 02:05 / 05:05
bin/install_auto_dev_cron.sh uninstall

# 急停 / 恢复
bin/install_auto_dev_cron.sh pause
bin/install_auto_dev_cron.sh resume

# 当前状态摘要
bin/install_auto_dev_cron.sh status
```

### Subagent 行为约束（写在 `bin/auto_dev_subagent_prompt.md` 中）

每个 subagent 在派发时被告知：

- 只在 `feature/m<N>-issue-<n>-<slug>` 分支上工作；**禁止** `git push origin main` / `--force` / `--no-verify`
- **禁止**修改驱动自身（`bin/auto_dev_*`）、CI 配置（`.github/workflows/`）、`mockup/`、任何 `_KEY/_SECRET/_TOKEN/_PRIVATE_KEY` 命名文件
- **禁止**在测试中调用真实交易所 / IM / 行情 HTTP API；必须用 `respx` mock 或 `vcrpy` 录制 fixture
- 每完成一块独立可验证的小改动 → `git add <specific files> && git commit && git push`
- 退出前写 `var/dev/state/.subagent_status_<n>.json`（含 verdict: `ok` / `timeout` / `needs-human` / `failed`）

驱动用 `check_subagent_diff_safe()` 双重护栏：subagent 退出后 driver 自查 `git diff --name-only`，命中禁区路径直接 abort 不合并。

### 本地校验五件套（merge 唯一硬门）

```bash
uv run pytest -q -m "not live" \
  && uv run lint-imports \
  && uv run ruff check . && uv run ruff format --check . \
  && uv run mypy src/copy_trader
```

GitHub Actions 仍在 PR 触发跑（事后兜底），但驱动用 `--admin` 不等它。`@pytest.mark.live` 标记的测试**不**在驱动跑——需要真实 API 的测试人工触发。

### Halt 条件（任一触发 → 全局停盘）

- `var/dev/state/.driver_pause` 锁文件存在（人工急停）
- `consecutive_failures >= 3`（连续 3 个 issue 整体失败）
- main 分支最新 GH Actions 是 `failure`
- 磁盘剩余 < 5%

恢复：`bin/install_auto_dev_cron.sh resume`。

### Stuck 跳过（不全局 halt）

同一 issue 在 3 次唤醒内未跑通 → driver 给该 issue 加 `stuck` label + 评论，跳过该 issue 但继续消费队列下一个。

### 启用前必跑 dry-run

第一次启用 cron 前**必须**先：

```bash
DRY_RUN=1 bin/auto_dev_loop.sh
```

走完整流程但开 PR 后自己 close 不合并。验证 subagent 能正常实现 issue #1 后再 `bin/install_auto_dev_cron.sh install`。

### 日志与抽查

- `logs/auto_dev_<wake_ts>.log`：driver 主流程
- `logs/auto_dev_subagent_<n>_<ts>.log`：subagent stream-json 完整输出
- `logs/auto_dev_cron.log`：单行汇总（每次唤醒结果）

**建议每天早上**抽查前一晚合并的 PR（`gh pr list --base main --state merged --limit 5 --label autonomous-driver`）；发现写错代码 → `bin/install_auto_dev_cron.sh pause` + `git revert <merge-sha> -m 1`。
