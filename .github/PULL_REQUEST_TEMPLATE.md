<!-- 本模板由 issue #34 固化，对应 spec: openspec/specs/delivery-roadmap/spec.md -->

## Spec 引用

<!-- 列本 PR 落地的 spec 路径与具体段落（Requirement / Scenario）。
缺 spec 引用 → review 退回；spec 未涵盖的修改先发 OpenSpec change 提案。 -->

- spec: `openspec/specs/<capability>/spec.md` (#段落)
- closes: `#<issue-number>`

## 验收清单

<!-- 逐条对照 issue acceptance 勾选；缺一项就别勾。 -->

- [ ] uv run pytest -q -m "not live"
- [ ] uv run lint-imports
- [ ] uv run ruff check . && uv run ruff format --check .
- [ ] uv run mypy src/copy_trader
- [ ] forbidden file diff（无 bin/auto_dev_* / mockup/ / *_KEY.* 等）
- [ ] (按 issue 补) <场景一>
- [ ] (按 issue 补) <场景二>

## 回滚步骤

<!-- 描述本 PR 失败时怎么回滚。包括:
- main 是否有保护规则(本仓没保护,直接 git revert <merge-sha> -m 1)
- 数据迁移/schema 变更的回滚 SQL
- 配置变更的还原命令
- 关联的 cron / systemd 单元状态
若纯代码改动 + 无 schema 迁移 → 写 "git revert <merge-sha> -m 1" 即可。 -->

```bash
git revert <merge-sha> -m 1
git push origin main
```

## 灰度指标

<!-- 列本 PR 上线后 24h / 7d / 14d 三个时间窗的观察指标与阈值。
默认引用 docs/CANARY_METRICS.md 的三类指标(reconcile diff 行数 /
critical alert 数 / PnL 实盘与回测理论值偏差)。 -->

按 [docs/CANARY_METRICS.md](../docs/CANARY_METRICS.md) 三类指标观察:

- reconcile diff 行数: 24h 内 0 行 / 7d 内 ≤ X 行
- critical alert 数: 24h 内 0 / 7d 内 ≤ X
- PnL 实盘 vs 回测理论值偏差: 24h 内 ≤ Y bps / 14d 累计 ≤ Z bps

任意一项超阈值 → 触发 [docs/CANARY_CHECKLIST.md](../docs/CANARY_CHECKLIST.md) 回滚流程 + 写 [docs/POSTMORTEM_TEMPLATE.md](../docs/POSTMORTEM_TEMPLATE.md)。

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
