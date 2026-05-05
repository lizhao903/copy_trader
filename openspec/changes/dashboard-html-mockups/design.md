## Context

`bootstrap-architecture` 已经定义了 Dashboard 的功能面：

- `/overview`、`/runners`（含 CRUD）、`/settings`（统一设置中心，覆盖 `accounts / capital_allocation / pyramid / fixed_position / strategies / notify / risk` 七段）、CLI `doctor`、`logs/dashboard_audit.log` 与 `logs/reconcile_<ts>.log` 查看面

后端实装在 M4 issue `#28`（FastAPI Dashboard 基线）与 `#29`（设置中心）。本变更产出**视觉前置物**：纯静态 HTML，作者可在浏览器直接打开评估 UI，再回头驱动后端实装。

工程上没有任何 build step、依赖或外部 CDN——这是为了让 mockup 在没有网络的环境下也能 100% 渲染。视觉风格目标是"信息密度高、形式克制、单人运维友好"，参考 Linear / Plausible / GoatCounter 这种 admin tool 美学，避免向 SaaS 营销页风格漂移。

## Goals / Non-Goals

**Goals:**

- G1：每个管理功能一个独立 `.html` 文件（硬约束，用户原话）
- G2：浏览器直接 `open mockup/index.html` 即可离线浏览，不需要任何 server / build step
- G3：导航 / 视觉风格在 16 个页面之间一致；样式集中在 `mockup/assets/style.css`
- G4：覆盖完整的"管理界面"清单，与 `openspec/specs/` 中的 capability 一一对应可追溯
- G5：占位数据真实可信（账户名、symbol、数量级、状态枚举值），但所有数字都是假的

**Non-Goals:**

- 不写任何 JS 框架（React / Vue / Svelte）；不引入 Tailwind / Bootstrap CDN
- 不连后端、不做表单提交、不做 fetch；表单的 `<form>` 仅作占位，提交不做任何事
- 不实现真正的多语言；中文为主，关键术语保留英文（保持与 spec / 代码一致）
- 不做响应式精细打磨（≥ 1024px 桌面优先，移动端可看不可用）
- 不做暗色主题切换（mockup 期单一主题足够；正式实装时再决定）

## Decisions

### D1. 目录结构

```
mockup/
├── README.md
├── index.html                  # 导航首页
├── overview.html               # 账户余额 + unrealized PnL（与 /overview）
├── runners-list.html           # runner 实例列表 + 启停（与 /runners）
├── runners-detail.html         # 单 runner 详情（与 /runners/<id>）
├── runners-create.html         # 创建 runner 表单（与 /runners/new）
├── settings-index.html         # 设置中心入口（与 /settings）
├── settings-accounts.html      # 账户维护
├── settings-capital.html       # 资金分配
├── settings-pyramid.html       # 滚仓
├── settings-fixed-position.html  # 固定仓位
├── settings-strategies.html    # 策略库
├── settings-notify.html        # IM 网关
├── settings-risk.html          # 风控
├── doctor.html                 # 自检（与 CLI doctor 输出可视化）
├── audit.html                  # dashboard_audit.log 查看
├── reconcile.html              # reconcile diff 查看
└── assets/
    └── style.css               # 共享样式
```

文件命名采用 `<area>-<page>.html` 模式，让 finder / `ls` 排序后同 area 邻接（settings-* 全部相邻），降低维护检索成本。

### D2. 页面共享导航条

每个页面顶部含相同导航：

```html
<!-- nav: 同步修改请改 README 中的清单 -->
<nav class="topnav">
  <a href="index.html" class="brand">copy_trader</a>
  <a href="overview.html">Overview</a>
  <a href="runners-list.html">Runners</a>
  <a href="settings-index.html">Settings</a>
  <a href="doctor.html">Doctor</a>
  <a href="audit.html">Audit</a>
  <a href="reconcile.html">Reconcile</a>
  <span class="env-badge">env: dev</span>
</nav>
```

**导航是手抄复制粘贴**——纯静态 HTML 没有 server-side include / template engine。我接受这一份代价（16 份重复），换"纯文件能离线打开"。`mockup/README.md` 有一段强提醒"修改导航请同步全部 16 个文件"，并附 `grep -l 'class="brand"' mockup/*.html` 一行命令做核对。

### D3. CSS 设计语言（精简）

`assets/style.css` 约定：

- 字体：`-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`，等宽用 `ui-monospace, SFMono-Regular, Menlo, monospace`
- 配色（仅四档）：
  - 文本主：`#1a1a1a`
  - 文本次：`#666`
  - 边框 / 分隔：`#e5e5e5`
  - 强调（运行中 / 主动操作）：`#0066cc`
  - 状态色：`#0a8c0a`（healthy）/ `#cc7a00`（warning）/ `#cc0000`（error）
- 容器：`max-width: 1200px; margin: 0 auto; padding: 24px`
- 表格：紧凑型，行高 32px，斑马纹用 `:nth-child(even)`
- 表单：左标签右控件，`<label>` 占 200px，控件占剩余宽度，敏感字段控件用 `<code>&lt;set&gt;</code>` 占位
- 状态徽章：`.badge.running .stopped .errored .draft`
- 不使用 flex/grid 之外的复杂布局；不引入图标字体（用 emoji 或 unicode 符号即可）

CSS 文件目标 < 250 行，把"信息密度优先"作为风格底色。

### D4. 占位数据约定

为了 mockup 之间口径一致，所有页面共用一组虚构 fixture：

- **账户**：`spot`（venue=binance.spot）、`alt`（venue=binance.spot）、`copy`（venue=binance.spot）、`hl_eth`（venue=hyperliquid.spot）
- **策略**：`kdj_short_1h3m`（参数 oversold=20 / overbought=78 / stop_loss_pct=4.5 / take_profit_pct=6.5）、`hello`（占位空策略）
- **Symbol**：`BTCUSDT`、`ETHUSDT`、`SOLUSDT`、`SOL-USD`、`ETH-USD`
- **Runner 实例**：4 个（每账户 1 个），状态分别 `running / running / stopped / errored` 演示状态色
- **PnL**：unrealized 在 ±200 USDT 量级，realized 在 ±2000 USDT 量级
- **Reconcile**：演示 1 条 cache_drift（已自动修正）、1 条 ledger_exchange_mismatch（SAFE 模式）的样例

每个 HTML 顶部 `<!-- fixture: ... -->` 注释引用上述 fixture，方便后续后端实装时校验

### D5. 占位提交行为

所有 `<form>` 的 `action="#"`、`<button type="submit">` 不做任何提交动作；点击后**仅在控制台 `console.log`** 一行（便于演示交互意图）。这条 JS 代码全局共用约 5 行，写在每页 `</body>` 前 inline `<script>`。

### D6. 与后端实装的边界

mockup 文件**不会**进入运行时；M4 后端实装把 mockup 视为"美工稿"翻译为 Jinja2 模板（或 React 组件，若届时选用 SPA）。后端实装 PR 完成后：

- 若实装与 mockup 一致 → 在 `mockup/README.md` 加一行"M4 已实装，参见 commit `<sha>`"
- 若实装偏离 mockup → mockup 移到 `mockup/archive/<date>-pre-m4/`，新版 mockup（如有）放回顶层

不允许 mockup 与实装代码长期不一致；这是 D6 的红线。

## Risks / Trade-offs

- **[导航 16 份复制粘贴]** 修改导航要改 16 个文件
  - **Mitigation**：`README.md` 醒目提醒 + 提供 `grep -l 'class="brand"' mockup/*.html` 核对脚本；如果将来文件 > 30 个再考虑引入构建（hugo / 11ty）
- **[mockup 漂移]** 后端实装后 mockup 不更新会误导
  - **Mitigation**：D6 红线 + M4 后端 PR 模板要求作者声明 mockup 状态（一致 / 已归档）
- **[过度设计静态站]** 16 个页面对单人项目可能多
  - **Mitigation**：每页 < 200 行 HTML，全部页面合计 < 3000 行；CSS < 250 行；可在 1–2 天内完成
- **[占位数据被误信]** 截图给别人看时被当真实数据
  - **Mitigation**：每页右上角加 `<div class="mockup-banner">MOCKUP — 占位数据，非实盘</div>` 红色横幅
