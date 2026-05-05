## Why

`bootstrap-architecture` 已经把 M0–M5 拆成 34 个 GitHub issue（#1–#11、#13–#35），但人工逐个 PR 推进太慢；用户希望**夜间无人值守**自动消费这批 issue：每次取一个最高优先级 + 依赖已满足的 issue，开 feature 分支，调一个独立 subagent 写代码 + 加测试，跑本地校验三件套（pytest / lint-imports / ruff / mypy / 静态扫描），通过后开 PR 用 `--admin` 直接合 main，再取下一个。autotrader 仓库的 `auto_dev_loop` 已经验证过这条路在单人项目上跑得通；本变更把它"copy_trader 化"——加上更严的本地测试边界（**禁止调用真实交易所 API**）、更明确的 halt 条件、显式的"一次唤醒只跑一个 issue"循环，避免单进程长跑超时或上下文爆炸。

## What Changes

- 新增 `bin/auto_dev_loop.sh` 驱动脚本（pure bash + `gh` + `uv run` + `claude -p`），每次唤醒消费**一个** issue 然后退出；状态写 `var/dev/state/driver_state.json`，下次唤醒续跑
- 调度通过 `bin/install_auto_dev_cron.sh {install|uninstall|status}` 装系统 cron（macOS launchd 或 Linux cron），默认 20:05 / 23:05 / 02:05 / 05:05 各唤醒一次（夜间高密度，白天可暂停）
- 驱动**派发 subagent**：每个 issue 起一个 `general-purpose` 子 agent（通过 `claude -p --dangerously-skip-permissions ...`），喂入 issue 号 + spec 路径 + 本地测试边界，子 agent 自行写代码、加测试、跑本地校验、commit；返回成功后由驱动开 PR 并 `gh pr merge --squash --admin`
- **顺序执行**：同一时刻最多一个活跃子 agent；按 milestone（m0 → m5）+ priority（p0 > p1 > p2）+ 依赖 satisfied 三键排序选下一个 issue，绝不并行多个 issue（避免 main 冲突 + 让 reviewer 看清单一变更）
- **本地测试边界（硬约束）**：
  - 只跑 `uv run pytest -q`（含 lint-imports / ruff / mypy 包内）
  - **禁止**调用 Binance / Hyperliquid / OKX / Bybit 真实 API；所有交易所相关测试 MUST mock 或用 vcrpy 录制 fixture
  - 禁止网络外联（pytest 用 `pytest-socket` 或 `--disable-socket` 拦截，本变更同时实现这道护栏）
  - GitHub Actions CI 也跑（PR 触发），但**不**作为 merge 阻塞——驱动用 `--admin` 直接合 main，CI 异步跑作为事后兜底
- **PR 自动合并**：本地通过 → `gh pr create --base main --fill` → `gh pr merge --squash --admin --delete-branch`；merged 后标 issue closed 并把 PR 号回填到本地状态
- **WIP commit 策略**：subagent 必须每完成一块可独立验证的小改动就 commit 一次 push 一次（即使该 issue 整体未跑通）；这样下次唤醒可断点续传，不丢工作进度
- **halt 条件**（任一触发 → 驱动写 critical alert 并优雅退出，等人工干预）：
  - 同 issue 三次唤醒未通过 → 标 stuck，跳过该 issue，PR 留 draft
  - 连续 3 个 issue 整体失败 → 暂停整个驱动 24h（环境问题假设）
  - main 分支最新 commit 的 GitHub Actions 回滚红 → 暂停推进直到人工修
  - `var/dev/.driver_pause` 锁文件存在 → 直接退出（人工应急停盘）
- **不在本变更内**：实际跑通 M0–M5 的代码（那是被驱动消费的下游）；驱动自身的执行（首次 cron 启动由用户在 apply 阶段触发）
- **新增 capability**：`autonomous-driver` —— 描述驱动的契约（顺序、状态机、本地测试边界、halt 条件、PR 合并协议）

## Capabilities

### New Capabilities

- `autonomous-driver`: 夜间无人值守驱动消费 GitHub issue 的契约（队列选择算法、subagent 派发协议、本地测试边界、状态持久化、halt 条件、PR 自动合并规则、断点续传语义）

### Modified Capabilities

<!-- 不修改任何已存在的 capability；本驱动构筑在 `delivery-roadmap` spec 之上但不改它。 -->

## Impact

- **新增文件**：
  - `bin/auto_dev_loop.sh`（驱动主脚本，bash）
  - `bin/install_auto_dev_cron.sh`（cron 安装/卸载/查询）
  - `bin/auto_dev_subagent_prompt.md`（subagent 喂入的标准化 prompt 模板）
  - `var/dev/state/driver_state.json`（运行时状态，gitignored）
  - `logs/auto_dev_<ts>.log`（每次唤醒日志，gitignored）
  - `pyproject.toml` 加 `pytest-socket` 与 `vcrpy` 到 dev deps（拦截真实网络外联）
  - `pytest.ini` 或 `pyproject.toml [tool.pytest.ini_options]` 加 `--disable-socket` + 白名单本地 SQLite
- **不影响**：`src/copy_trader/` 自身代码（驱动只读 issue，不直接动业务代码）；driver subagent 写的代码归 M0–M5 各 issue 自己负责
- **运行时**：
  - cron 每次唤醒约 20–60 分钟（一个 issue 端到端：拉代码 → 派发 subagent → 跑测试 → 开 PR → 合 main）
  - 一夜 4 次唤醒 → 理想推进 4 个 issue，一周可跑完 M0；M2 涉及交易所代码每 issue 可能需要更长，整体推进节奏比理想保守
- **风险**：
  - subagent 把策略代码写错 → main 偶发回退；mitigation：本地测试覆盖 + 主分支保护（虽用 --admin 绕，但 CI 红会触发 halt 条件）
  - cron 错过执行（笔记本休眠）→ 自然失败，下次唤醒续；不补偿
  - 本地测试 ≠ 生产环境覆盖 → 这是风险但可接受，M5 灰度阶段会从生产数据回归
- **不在本变更内的执行**：本计划仅产出驱动脚手架与契约。**首次手动启动 cron** 在 /opsx:apply 阶段最后一步由用户决定（cron 安装即生效）；如果用户决定不开 cron，驱动文件存在但不自动跑，可手动 `bin/auto_dev_loop.sh` 单次试跑
