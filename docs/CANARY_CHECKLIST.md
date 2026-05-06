# 灰度 14 天观察清单

> 本文档对应 issue #33,spec: `openspec/specs/delivery-roadmap/spec.md`。
>
> 阈值定义见 [docs/CANARY_METRICS.md](CANARY_METRICS.md)(单一权威)。本文档只规定**每日操作步骤**与**回滚动作**。

## 灰度规则

每个 milestone (m0-m5) 完成后,生产首次部署进入 **14 天灰度观察期**。

**硬约束**:

- 14 天连续 **0 严重 + 0 警告** → 允许 archive 灰度,milestone 转生产
- 任何一天触发 **严重** 阈值 → 立即回滚 + 写 postmortem,灰度作废重来
- 触发 **警告** 阈值 → 灰度延长 7 天,重新计 14 天连续

## 每日观察清单

每天 UTC 00:00 由 cron 自动跑(或人工执行 `bin/canary_check.sh`),输出三类指标实测值并对照 [CANARY_METRICS.md](CANARY_METRICS.md) 阈值。

### Day-N 检查项

- [ ] **reconcile diff 行数**(过去 24h)
  - 命令: `cat $COPY_TRADER_HOME/logs/reconcile_*.log | grep -c '"event":"\(cache_drift\|ledger_exchange_mismatch\|unknown_position_on_exchange\)"'`
  - 阈值: 24h 警告 ≥ 1 / 严重 ≥ 3
  - 实测: ____ 行
  - 判定: ☐ pass ☐ warn ☐ critical
- [ ] **critical alert 数**(过去 24h)
  - 命令: `sqlite3 $COPY_TRADER_HOME/db/ledger.db "SELECT COUNT(*) FROM alerts WHERE severity='critical' AND ts >= datetime('now', '-24 hours')"`
  - 阈值: 24h 警告 ≥ 1 / 严重 ≥ 3
  - 实测: ____ 条
  - 判定: ☐ pass ☐ warn ☐ critical
- [ ] **PnL 实盘 vs 回测偏差**(过去 24h)
  - 命令: `cat $COPY_TRADER_HOME/logs/canary_pnl_$(date -u +%Y-%m-%d).json | jq .deviation_bps`
  - 阈值: 24h 警告 ≥ 50 bps / 严重 ≥ 200 bps
  - 实测: ____ bps
  - 判定: ☐ pass ☐ warn ☐ critical
- [ ] **systemd 状态**: `systemctl is-active copy-trader@<strategy>-<account>` → 应为 `active`
- [ ] **doctor 通过**: `COPY_TRADER_ENV=prod uv run copy-trader doctor` 退出码 0 + 无 ⚠️
- [ ] **磁盘 / 内存 / CPU 健康**(可选,接 systemd `top` / Dashboard)

### 累计窗口检查(每周一跑一次)

- [ ] **7d 累计 reconcile diff** ≤ 警告(5)
- [ ] **7d 累计 critical alert** ≤ 警告(3)
- [ ] **7d PnL 偏差累计** ≤ 警告(100 bps)

### 14 天结束(Day-14)判定

- [ ] 全部 14 天 daily check 全 pass(无 warn 无 critical)
- [ ] 7d × 2 累计窗口全 pass
- [ ] 14d 累计 reconcile diff < 警告(10)
- [ ] 14d 累计 critical alert < 警告(5)
- [ ] 14d PnL 偏差 < 警告(150 bps)

→ 全勾 = 灰度通过 → archive milestone + 解除 `--dry-run` / 进入正式生产

## 回滚流程(任一严重阈值触发)

```bash
# 1. 立即停 systemd
systemctl stop copy-trader@<strategy>-<account>

# 2. 跑 rollback 脚本(checkout 上一个 tag + 重启 systemd)
bin/rollback.sh

# 3. 验证回滚成功
COPY_TRADER_ENV=prod uv run copy-trader doctor
systemctl is-active copy-trader@<strategy>-<account>

# 4. 写 postmortem
cp docs/POSTMORTEM_TEMPLATE.md docs/postmortem/$(date -u +%Y%m%d)_<short-title>.md
# 编辑填 5 段
```

## 灰度暂停(警告阈值触发)

不立即回滚,但**暂停灰度推进**:

- 不能 archive 灰度;14 天倒计重新计
- 7 天观察期内必须 root-cause + 修复 + 重新 deploy
- 修复后 deploy 触发**新的 14 天灰度**(不是从原 day-N 续)

## 跑表模板(Day-1 起每天 fill)

| Day | Date (UTC) | Recon. diff (24h) | Crit. alerts (24h) | PnL dev (bps) | Verdict | Note |
|-----|-----------|-------------------|--------------------|--------------:|---------|------|
| 1   |           |                   |                    |               |         |      |
| 2   |           |                   |                    |               |         |      |
| ... |           |                   |                    |               |         |      |
| 14  |           |                   |                    |               |         |      |
