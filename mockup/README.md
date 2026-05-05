# mockup/

王牌带单员（`copy_trader`）Dashboard 静态 HTML mockup 集合。**纯静态资源**，浏览器双击 `index.html` 即可离线浏览，不依赖任何外部 CDN / npm / build step。

> 系统对外名称为「**王牌带单员**」（mockup 顶栏 brand 与 `<title>` 中所示）；包名 / 仓库名 / env 变量前缀仍为 `copy_trader` / `COPY_TRADER_*`，是技术标识符不译。

> ⚠️ 这些 mockup 仅作 UX 设计参考，**不进入运行时**。M4 后端实装（issue #28 / #29）合并后请按 `openspec/specs/dashboard-mockup/spec.md` 末条 requirement 决定保留 / 归档。

## 用法

```bash
# macOS
open mockup/index.html

# Linux
xdg-open mockup/index.html
```

无需任何依赖，离线可用。

### 深浅色主题

每个页面顶栏右侧有 🌙 / ☀️ 切换按钮。规则：

- 首次访问：跟随操作系统 `prefers-color-scheme`（macOS 浅色 / 深色模式自动识别）
- 点击切换：状态记到 `localStorage["theme"]`，下次访问保持
- 防 FOUC：`<head>` 内联脚本在样式应用前就设 `data-theme`，没有"白闪"

CSS 走单一文件，深色变体用 `:root[data-theme="dark"]` 覆盖 4 档调色板（fg / fg-muted / bd / accent / 状态色）；不引入第二份 stylesheet。

## 页面清单（→ spec → issue）

| HTML 文件 | 对应路由 / 功能 | Spec | M4 Issue |
|---|---|---|---|
| [index.html](index.html) | mockup 入口（非生产路由） | `dashboard-mockup` | — |
| [overview.html](overview.html) | `/overview` | `dashboard-mockup` | #28 |
| [runners-list.html](runners-list.html) | `/runners` | `runner-lifecycle` | #28 |
| [runners-detail.html](runners-detail.html) | `/runners/<id>` | `runner-lifecycle` | #28 |
| [runners-create.html](runners-create.html) | `/runners/new` | `runner-lifecycle` | #27 #28 |
| [settings-index.html](settings-index.html) | `/settings` | `config-overlay` | #29 |
| [settings-accounts.html](settings-accounts.html) | `/settings/accounts` | `config-overlay` | #29 |
| [settings-capital.html](settings-capital.html) | `/settings/capital` | `config-overlay` | #29 |
| [settings-pyramid.html](settings-pyramid.html) | `/settings/pyramid` | `config-overlay` | #29 |
| [settings-fixed-position.html](settings-fixed-position.html) | `/settings/fixed-position` | `config-overlay` | #29 |
| [settings-strategies.html](settings-strategies.html) | `/settings/strategies` | `config-overlay` | #29 |
| [settings-notify.html](settings-notify.html) | `/settings/notify` | `config-overlay` | #29 |
| [settings-risk.html](settings-risk.html) | `/settings/risk` | `config-overlay` | #29 |
| [doctor.html](doctor.html) | CLI `copy-trader doctor` 可视化 | `runtime-isolation` `config-overlay` | #5 #28 |
| [audit.html](audit.html) | `dashboard_audit.log` 查看 | `config-overlay` | #29 |
| [reconcile.html](reconcile.html) | `logs/reconcile_<ts>.log` 查看 | `pnl-single-source` | #11 #28 |

合计 16 个 HTML + 1 个 CSS（`assets/style.css`）。

## 占位 fixture

所有页面共享同一组虚构数据：

- **账户**：`spot`（venue=`binance.spot`）/ `alt`（`binance.spot`）/ `copy`（`binance.spot`）/ `hl_eth`（`hyperliquid.spot`）
- **策略**：`kdj_short_1h3m`（参数 oversold=20、overbought=78、stop_loss_pct=4.5、take_profit_pct=6.5）/ `hello`（占位空策略）
- **Symbol**：`BTCUSDT` / `ETHUSDT` / `SOLUSDT` / `SOL-USD` / `ETH-USD`
- **Runner 实例**（4 个）：
  | id 缩写 | name | venue | account | strategy | mode | status |
  |---|---|---|---|---|---|---|
  | `01HZ…0M1` | `spot-kdj` | `binance.spot` | spot | kdj_short_1h3m | live | running |
  | `01HZ…0M2` | `alt-kdj` | `binance.spot` | alt | kdj_short_1h3m | live | running |
  | `01HZ…0M3` | `copy-hello` | `binance.spot` | copy | hello | paper | stopped |
  | `01HZ…0M4` | `hl-eth-kdj` | `hyperliquid.spot` | hl_eth | kdj_short_1h3m | live | errored |
- **PnL 量级**：unrealized 在 ±200 USDT 范围、realized 在 ±2000 USDT 范围
- **Reconcile**：演示 1 条 `cache_drift`（自动修正）、1 条 `ledger_exchange_mismatch`（SAFE 模式）

> 任何数字、id、key、token 均为占位假值。**无任何真实凭证 / 实盘账户**。

## 修改导航条的同步规则

每个 HTML 顶部都手抄了同一份 `<nav class="topnav">…</nav>`（纯静态站点没有模板引擎）。修改导航时按以下 5 步同步：

1. 改 `index.html` 中的 nav 块
2. `grep -l 'class="brand"' mockup/*.html` 列出全部 16 个文件
3. `diff` 比对 nav 块与 `index.html` 一致
4. 同步剩余 15 个文件
5. 重新跑 `grep -c 'href="overview.html"' mockup/*.html` 确认每文件出现且仅出现一次

```bash
# 核对命令：所有 16 个 HTML 都应有 brand 链接
grep -l 'class="brand"' mockup/*.html | wc -l
# 期望输出：16
```

## 与后端实装的关系

mockup **不会**被 M4 后端 `cli/dashboard.py` 直接 serve。Issue #28 / #29 实装时把 mockup 翻译为 Jinja2 模板（或 SPA 组件，若届时切换）。后端 PR 合并后请：

- **若实装与 mockup 一致**：在本 README 顶部加一行 `## 状态：M4 已实装，对应 commit <sha>`
- **若实装偏离 mockup**：把当前 `mockup/` 顶层移到 `mockup/archive/<YYYY-MM-DD>-pre-m4/`；新版 mockup（如有）放回顶层

红线：顶层 `mockup/` **不允许**长期保留与实装不一致的页面（spec 末条 requirement）。
