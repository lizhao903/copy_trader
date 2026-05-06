# 灰度指标定义与告警阈值

> 本文档对应 issue #35,spec: `openspec/specs/pnl-single-source/spec.md` + `openspec/specs/delivery-roadmap/spec.md`。
>
> **单一权威来源**: `bin/deploy.sh` / systemd hook / Dashboard 都从本文档机器读取阈值,不在脚本里硬编码。

## 三类核心指标

灰度判定基于以下 **3 类** 指标的实测值:

### 1. reconcile diff 行数 (启动期)

**定义**: `copy-trader reconcile --account <name>` 输出的 `ReconcileReport.events` 中 `cache_drift` + `ledger_exchange_mismatch` + `unknown_position_on_exchange` 三种事件数之和。

**采集**: 每次 LiveRunner 启动 / systemd `ExecStartPre=copy-trader reconcile ...` 钩子产生 `logs/reconcile_<ts>.log`,机器解析为 JSON。

**阈值**:

| 时间窗 | 警告 | 严重 |
|-------|------|------|
| 24h 内 | ≥ 1 行 | ≥ 3 行 |
| 7d 累计 | ≥ 5 行 | ≥ 10 行 |
| 14d 累计 | ≥ 10 行 | ≥ 20 行 |

**不计入**: `cache_drift` 类型若 `acknowledged=True`(被 reconcile 自动覆盖且已记 `cache_overridden` 事件)在统计时按 0.5 行计权,因为它已被自动修正非业务异常。

**判定逻辑**:
- 严重 → 立即触发回滚([docs/CANARY_CHECKLIST.md](CANARY_CHECKLIST.md))
- 警告 → 灰度暂停推进,等下个 24h 窗口

### 2. critical alert 数

**定义**: `notify` 子包(m4+ 实装)产生的 `event.severity == "critical"` 累计数。来源包括:

- `CrossEnvironmentWriteError` 拦截
- `unknown_position_on_exchange` (fatal=True)
- `place_order` 失败累计 ≥ 5 次/min
- exchange API 5xx 持续 ≥ 60s
- `mypy` / `lint-imports` CI 红 (启动期 doctor 检测)

**采集**: 每个 alert event 写 ledger 副表(m4 #25 实装) `alerts(ts, account, severity, event_type, payload)`,Dashboard 实时聚合。

**阈值**:

| 时间窗 | 警告 | 严重 |
|-------|------|------|
| 24h 内 | ≥ 1 | ≥ 3 |
| 7d 累计 | ≥ 3 | ≥ 5 |
| 14d 累计 | ≥ 5 | ≥ 10 |

**判定**: 严重 → 立即回滚 + 写 postmortem。

### 3. PnL 实盘 vs 回测理论值偏差

**定义**: 同一 `(account, strategy)` 跑同一时间窗,实盘 ledger 累计 `realized_pnl` 与 BacktestRunner(m4 #24 实装)用历史 K 线 + 同策略 + 同 marketdata 算出的理论 PnL 之差,以基点(bps)表示:

```
deviation_bps = abs(实盘_pnl - 回测_pnl) / abs(回测_pnl) * 10000
```

**采集**: 每天 UTC 00:00 由 cron 触发 `copy-trader backtest --strategy <name> --account <name> --since <yesterday>`,与 `copy-trader pnl --realized` 输出比对,写 `logs/canary_pnl_<date>.json`。

**阈值**:

| 时间窗 | 警告 | 严重 |
|-------|------|------|
| 24h 偏差 | ≥ 50 bps | ≥ 200 bps |
| 7d 累计 | ≥ 100 bps | ≥ 300 bps |
| 14d 累计 | ≥ 150 bps | ≥ 500 bps |

**判定**:
- 严重 → 立即回滚 + 排查 (常见原因: paper-vs-live slippage 模型偏差、exchange 实际 fee_bps 不同 spec 假设、网络延迟导致 fill 价漂移)
- 警告 → 调整 paper.slippage_bps 或回测 marketdata 重采样

## 推进 / 回滚判定逻辑

灰度 14 天观察后:

```
if reconcile_diff_14d >= 严重阈值 OR critical_alerts_14d >= 严重阈值 OR pnl_dev_14d >= 严重阈值:
    立即回滚 + 写 POSTMORTEM_TEMPLATE.md
elif reconcile_diff_14d >= 警告阈值 OR critical_alerts_14d >= 警告阈值 OR pnl_dev_14d >= 警告阈值:
    灰度延长 7 天观察
else:  # 全 0 alert 全 0 drift 全偏差 < 警告阈值
    archive 灰度 + 转生产
```

**硬规则**: 14 天连续 **0 严重 + 0 警告** 才允许 archive。

## 机器可读格式

`bin/deploy.sh` 与 systemd hook 通过 `bin/canary_thresholds.sh` 读取本文档定义的阈值(由 `bin/canary_thresholds.sh` 解析下方 YAML 块):

```yaml
# canary_thresholds (machine-readable; 不要手工编辑表格,改这里同步)
reconcile_diff:
  24h: { warn: 1, critical: 3 }
  7d: { warn: 5, critical: 10 }
  14d: { warn: 10, critical: 20 }
critical_alert:
  24h: { warn: 1, critical: 3 }
  7d: { warn: 3, critical: 5 }
  14d: { warn: 5, critical: 10 }
pnl_deviation_bps:
  24h: { warn: 50, critical: 200 }
  7d: { warn: 100, critical: 300 }
  14d: { warn: 150, critical: 500 }
```

`bin/deploy.sh` smoke test 阶段提取 24h 阈值,实测值超阈值即触发自动 rollback (见 [docs/CANARY_CHECKLIST.md](CANARY_CHECKLIST.md))。

## Follow-up

- m4 #25 落实 `alerts` 表 schema 后,本文档的 critical_alert 采集要标 SQL 查询样例
- m4 BacktestRunner (#24) 落地后补 PnL 偏差采集脚本路径
- 阈值数值可按业务实测调整,但**调整必须经 OpenSpec change** (改本文档触发 review)
