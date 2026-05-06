# Postmortem: <短标题>

> 本模板对应 issue #33,spec: `openspec/specs/delivery-roadmap/spec.md`。
>
> 灰度结束后(无论通过 / 回滚)与任何**生产事故**都必须写一份。命名规范:
> `docs/postmortem/<YYYYMMDD>_<short-title>.md`(例: `docs/postmortem/20260520_paper_pnl_drift.md`)。
>
> 写完后 commit + push;严重事故的 postmortem 在下次 milestone review 上 walkthrough。

## 元数据

| 字段 | 值 |
|------|-----|
| 事件日期 | YYYY-MM-DD |
| 撰写人 | <github-handle> |
| 撰写日期 | YYYY-MM-DD |
| 严重等级 | minor / major / critical |
| 影响 milestone | m0 / m1 / m2 / m3 / m4 / m5 |
| 关联 PR / issue | #N, #M |
| 触发阈值 | reconcile_diff / critical_alert / pnl_deviation 中的哪一项 |
| 实测值 vs 阈值 | 例: PnL 偏差 350 bps (严重 ≥ 200 bps) |
| 是否回滚 | yes / no |
| 回滚 tag | bootstrap/m<N>-YYYYMMDD |

## 1. 背景

<!-- 部署的 milestone / strategy / account 信息;事故发生时的系统状态:
- 哪个 venue / strategy / account 在跑
- 几台 systemd 服务在线
- 当时 marketdata 状况(平稳/剧烈波动/单边)
- 灰度第几天 (day-N)
-->

## 2. 时序

<!-- 用 UTC 时间戳精确到分钟,从最早的征兆开始按时间顺序列。
例:

- 2026-05-20T12:34Z `reconcile` 输出 ledger_exchange_mismatch (qty 实测 1.5 vs 期望 1.0)
- 2026-05-20T12:35Z notify 子包推 critical alert "ledger_exchange_mismatch on BTCUSDT"
- 2026-05-20T12:40Z bin/canary_check.sh cron 报警达警告阈值
- 2026-05-20T13:00Z 人工 `systemctl stop` + `bin/rollback.sh`
- 2026-05-20T13:05Z 回滚后 doctor 跑通,生产线恢复
-->

## 3. 根因

<!-- 至少答 3 个问题:
1. 直接原因是什么?(代码 bug / 配置错误 / 第三方 API 变更 / 网络故障 / 等)
2. 为什么直接原因没被五件套 / 灰度前 14 天的观察拦截?
3. 为什么写代码 / review / spec 阶段没预防?

写出**真因**,不要停在症状层。"网络抖动"不是根因 — "为什么我们没设 retry / 为什么没监控网络丢包率"才是。
-->

## 4. 修正

<!-- 立即修补(已完成):
- [ ] 回滚到 tag <bootstrap/m<N>-YYYYMMDD>
- [ ] 数据修正(如 ledger 行删除 / 修补 SQL)
- [ ] 凭证轮换(如有泄露嫌疑)
- [ ] 客户/上游通知(如影响外部)

PR 链接: #<number>
-->

## 5. 长期改进

<!-- 防止重复:具体可执行的 action items,各自有 owner + 时间承诺。

例:
- [ ] (owner: @lizhao903 by 2026-06-01) 在 BinanceSpot 加 fetch_position 双验证(ledger + exchange 双查),不一致立即停盘
- [ ] (owner: @lizhao903 by 2026-06-15) 给 paper.slippage_bps 加自适应推导(从过去 7d 实盘 fills 算 actual slippage)
- [ ] (owner: @lizhao903 by 2026-07-01) 写新 OpenSpec change `add-pre-trade-position-check`,提案上层先查 cache 再下单的双层保护
- [ ] (owner: SRE by 2026-06-08) systemd 加 `OnFailure=` 钩子自动写 alerts 表

每条要有 PR / issue 跟踪。
-->

---

**Cross-references**:
- 触发的灰度日: docs/CANARY_CHECKLIST.md (day-N 跑表模板)
- 阈值定义: docs/CANARY_METRICS.md
- 工作流约束: docs/CONTRIBUTING.md (灰度门槛章节)
