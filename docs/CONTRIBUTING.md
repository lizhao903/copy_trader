# 贡献指南

「王牌带单员」(`copy_trader`) 项目的开发规范、milestone 推进规则、灰度门槛、tag 命名约定。

> 本文档对应 issue #34，spec: `openspec/specs/delivery-roadmap/spec.md`。

## 工作流: OpenSpec 驱动

所有非 trivial 改动**必须**走 OpenSpec spec-driven 工作流:

1. `openspec/changes/<change-name>/proposal.md`: 描述目的、设计、tasks
2. 实装时按 `tasks.md` 一项项跑,完成 flip `- [ ]` → `- [x]`
3. archive 完成的 change → spec 入主库

trivial 修改(typo / 文档微调 / `.gitignore` 增条目)可跳过 OpenSpec,直接 PR。

详见仓库根 `CLAUDE.md` 的 "Working through OpenSpec" 章节。

## Milestone 串行推进规则

Bootstrap 阶段分 m0 → m5 共 6 个 milestone (见 `delivery-roadmap` spec):

| Milestone | 范围 | 完成门槛 |
|-----------|------|---------|
| m0 | 工具链 + 包骨架 + CI + doctor | 五件套 CI 全绿 + doctor 跑通 |
| m1 | core 模型 + ledger + PnL + reconcile | 跨环境守卫 + reconcile 三级 diff + 黄金测试 |
| m2 | exchange Protocol + binance.spot + paper + strategies + LiveRunner | dry-run 跑通 + paper-vs-live Fill 一致 |
| m3 | hyperliquid + 多 venue 集成 + 架构断言 | "加交易所只动一个子包"证明 |
| m4 | klines 缓存 + Runner schema + Dashboard + CLI registry | Dashboard /overview + /runners CRUD |
| m5 | 部署脚本 + systemd + onboard 文档 + 灰度清单 | 灰度 14 天 0 alert / 0 drift |

**硬规则:milestone 串行推进**:

- m_N 的 issue 必须**全部** closed 后才允许开始 m_(N+1)
- 跨 milestone 范围的 PR 会被 review 拒绝(M2 PR 中混入 M3 范围 → 退回)
- 例外: meta issue (#34 #35) 可在任意 milestone 阶段跑

## 灰度门槛

每个 milestone 完成后,生产 deploy 前必跑 **灰度 14 天**,观察 [docs/CANARY_METRICS.md](CANARY_METRICS.md) 三类指标:

1. **reconcile diff 行数**: 启动期 ledger / 交易所 / cache 三方差异
2. **critical alert 数**: notify 子包 alert event 等级 = critical 的累计
3. **PnL 实盘 vs 回测理论值偏差**: 同一策略 + 历史 K 线回测的预期 PnL 与实盘 PnL 差

阈值与判定逻辑见 [docs/CANARY_METRICS.md](CANARY_METRICS.md)。任一超阈值 → 触发 [docs/CANARY_CHECKLIST.md](CANARY_CHECKLIST.md) 回滚流程 + 写 [docs/POSTMORTEM_TEMPLATE.md](POSTMORTEM_TEMPLATE.md)。

灰度 14 天连续 0 alert + 0 drift → 允许 archive 该 milestone 计划。

## Tag 命名约定

生产 deploy 用 git tag 锚定版本:

- 格式: `bootstrap/m<N>-<YYYYMMDD>` (例: `bootstrap/m2-20260506`)
- m0-m5 用 `bootstrap/` 前缀;后续业务 milestone 用 `release/` 前缀
- tag 由 `bin/release.sh` 创建 + push origin (不在 github web ui 手工创建)
- tag 创建后**不可移动**;同一 milestone 多次发布用不同日期 (`bootstrap/m2-20260506`、`bootstrap/m2-20260512`)

## PR 描述模板

所有 PR 自动套用 [`.github/PULL_REQUEST_TEMPLATE.md`](../.github/PULL_REQUEST_TEMPLATE.md),含四段:

1. **Spec 引用**: 列具体 spec 路径与段落
2. **验收清单**: 五件套 + issue acceptance 逐项勾选
3. **回滚步骤**: PR 失败时如何 revert
4. **灰度指标**: 24h / 7d / 14d 观察阈值

缺任一段 → review 退回。

## 五件套门槛 (CI 强制)

每个 PR 合入 main 前必过五件套(`.github/workflows/ci.yml`):

```bash
uv run pytest -q -m "not live"
uv run lint-imports
uv run ruff check . && uv run ruff format --check .
uv run mypy src/copy_trader
```

外加两条静态扫描:

- `grep -r 'from script\.' src/ tests/` → 期望 0 行 (禁止 autotrader 风格的反模式 import)
- `grep -rE 'os\.path\.join\(ROOT, "(trade_info|logs|klines\.db)' src/ tests/` → 期望 0 行 (禁止项目根硬编码路径)

`@pytest.mark.live` 标记的测试**不**在 CI 跑(需要真实 API 凭证),由人工触发。

## 安全红线

- **禁止** `git push --no-verify`、`git push --force` (除非用户明确要求)
- **禁止** 在仓库里 hardcode 任何 `*_KEY` `*_SECRET` `*_TOKEN` `*_PRIVATE_KEY` 文件;凭证走 `$COPY_TRADER_HOME/secrets/.env` 或 EnvironmentFile
- **禁止** 在生产代码里调用真实 HTTP / 交易所 API 而不带 mock(测试除外,且需 `@pytest.mark.live`)
- **禁止** 修改 `bin/auto_dev_*` 与 `.github/workflows/` (driver 自身配置;只在专门的 driver/CI issue 里改)
