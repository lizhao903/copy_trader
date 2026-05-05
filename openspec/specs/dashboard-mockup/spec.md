# dashboard-mockup Specification

## Purpose
TBD - created by archiving change dashboard-html-mockups. Update Purpose after archive.
## Requirements
### Requirement: Mockup directory contains one HTML file per management page

仓库 MUST 在 `mockup/` 顶层目录提供下列 16 个独立 HTML 文件，**禁止**把多个页面合并到同一个文件：

| 文件 | 对应后端路由 / 功能 | 主要内容 |
|---|---|---|
| `index.html` | mockup 入口（非生产路由） | 16 页导航网格 + 项目简介 + 占位数据声明 |
| `overview.html` | `/overview` | 总览：账户余额、unrealized PnL 卡片矩阵、系统健康徽章、reconcile 状态摘要 |
| `runners-list.html` | `/runners` | 运行实例表（id / name / venue / account / strategy / status / heartbeat / actions），含创建按钮 |
| `runners-detail.html` | `/runners/<id>` | 单实例详情：基本信息卡 + 最近 fills 表 + 当前持仓 + 最近日志预览 + 启停/编辑/删除 |
| `runners-create.html` | `/runners/new` | 创建表单：name / venue 下拉 / account 下拉 / strategy 下拉 / mode 单选 / params_override JSON 文本框 |
| `settings-index.html` | `/settings` | 设置中心入口：七段卡片导航（accounts/capital/pyramid/fixed_position/strategies/notify/risk）|
| `settings-accounts.html` | `/settings/accounts` | 账户列表 CRUD：venue / enabled / credentials_alias / symbols 多选 |
| `settings-capital.html` | `/settings/capital` | 资金分配表：(account, strategy, quote_asset, max_quote_amount, reserve_quote_amount) |
| `settings-pyramid.html` | `/settings/pyramid` | 滚仓配置表：(account, strategy, enabled, first_entry_fraction, add_trigger_pct, reserve_quote_usdt, max_rounds) |
| `settings-fixed-position.html` | `/settings/fixed-position` | 固定仓位表：(account, strategy, mode, qty / quote_amount, max_price)；含与 pyramid 互斥提示 |
| `settings-strategies.html` | `/settings/strategies` | 策略库：name / module / 默认参数表单 |
| `settings-notify.html` | `/settings/notify` | IM 网关：Slack / Telegram / Feishu / Dingtalk adapter 启停与 webhook 配置 |
| `settings-risk.html` | `/settings/risk` | 风控：日亏熔断 / 总敞口上限 / 连亏熔断阈值 |
| `doctor.html` | CLI `copy-trader doctor` 可视化 | runtime root / env_tag / machine_id / lock 状态 / 配置来源 / ledger schema_version |
| `audit.html` | `dashboard_audit.log` 查看 | 时序条目：ts / 字段路径 / 层 / before/after 掩码 |
| `reconcile.html` | `logs/reconcile_<ts>.log` 查看 | 三级 diff 列表：cache_drift / ledger_exchange_mismatch / unknown_position_on_exchange |

#### Scenario: 文件清单完整

- **WHEN** 在仓库根 `ls mockup/*.html`
- **THEN** 输出包含且仅包含上述 16 个文件名（不多不少）

#### Scenario: 不允许合并页面

- **WHEN** PR 中尝试把 `settings-pyramid.html` 与 `settings-fixed-position.html` 合并到 `settings-positions.html`
- **THEN** review 拒绝合并并指向本 spec

### Requirement: Mockup is offline-friendly with no external CDN dependency

每个 HTML 文件 MUST 在浏览器双击打开（`file://`）即可完整渲染：

- MUST NOT 引用任何外部 URL（无 `<script src="https://...">` / `<link href="https://...">` / `<img src="https://...">`）
- MUST NOT 引用 npm / webpack / vite 之类构建产物；no `node_modules/` 路径
- MUST NOT 依赖任何 JS 框架（React / Vue / Svelte / Tailwind / Bootstrap CDN）
- 字体使用 system font stack（无 web font 加载）
- 图标用 emoji 或 unicode 字符（无 icon font / SVG sprite）

#### Scenario: 离线环境完整渲染

- **WHEN** 在断网机器上 `open mockup/overview.html`
- **THEN** 页面完整渲染，所有样式、布局、交互（仅 inline JS）正常

#### Scenario: PR 引入 CDN 被拒

- **WHEN** PR 在某 mockup 文件中加入 `<script src="https://cdn.tailwindcss.com">`
- **THEN** review 拒绝合并

### Requirement: Shared stylesheet under `mockup/assets/style.css`

仓库 MUST 提供 `mockup/assets/style.css` 作为唯一 stylesheet；所有 HTML 文件 MUST `<link rel="stylesheet" href="assets/style.css">` 引用同一文件；MUST NOT 在 HTML 文件内写超过 5 行的 `<style>` 块（页面级 override 可短例外）。

文件目标 < 250 行；色彩仅使用 design 中 D3 限定的四档；MUST NOT 引入第二份 CSS 文件。

#### Scenario: 唯一样式表

- **WHEN** 在仓库根 `find mockup/ -name '*.css'`
- **THEN** 仅输出 `mockup/assets/style.css`

#### Scenario: HTML 内联样式不超额

- **WHEN** 任意 mockup HTML 文件内的 `<style>` 块超过 5 行
- **THEN** review 要求把样式抽到 `style.css`

### Requirement: Mockup uses placeholder fixture, never real data

页面展示数据 MUST 来自共享虚构 fixture（D4 中定义）：账户 `spot/alt/copy/hl_eth`、策略 `kdj_short_1h3m/hello`、symbol `BTCUSDT/ETHUSDT/SOLUSDT/SOL-USD/ETH-USD`；数值量级真实但具体值伪造。MUST NOT 出现任何真实交易所 API key / secret / 实盘账户名 / 真实 PnL 数字。

每个页面 MUST 在视觉显著位置（如顶部固定 banner）显示 "MOCKUP — 占位数据，非实盘" 提示，颜色用 design D3 中的 error 色。

#### Scenario: 页面顶部 mockup 标识

- **WHEN** 任一页面在浏览器渲染
- **THEN** 顶部固定 banner 含红色 "MOCKUP — 占位数据，非实盘" 字样

#### Scenario: 真实凭证不允许出现

- **WHEN** PR 引入任何形如 `BNBxxxxxxxxxxxx` 的真实 API key 或被 git-secret-scan 识别为凭证的字符串
- **THEN** CI 拦截或 review 拒绝

### Requirement: Each page links to spec / issue origin

每个 HTML 文件 MUST 在 `<head>` 内或显著位置注释引用其对应的 spec 与 GitHub issue：

```html
<!-- spec: openspec/specs/<capability>/spec.md -->
<!-- issue: lizhao903/copy_trader#<n> -->
```

供后续后端实装时反查 UX 来源。`mockup/README.md` MUST 提供完整的"页面 → spec → issue"映射表。

#### Scenario: README 包含追溯映射

- **WHEN** 阅读 `mockup/README.md`
- **THEN** 文件含一段表格列出全部 16 个 HTML 文件、对应 capability spec、对应 GitHub issue 号

### Requirement: Mockup does not enter runtime or backend imports

`mockup/` 目录 MUST NOT 被 `src/copy_trader/` 任何模块 import 或读取；MUST NOT 在 `pyproject.toml` 的 `dependencies` 中体现；MUST NOT 触发 import-linter 或 mypy 检查（视为静态资源）。

后端 M4 实装在引用 mockup 设计时 MUST 用 Jinja2 / SPA 框架重写，MUST NOT 直接 serve `mockup/*.html`。

#### Scenario: 后端不直接 serve mockup 文件

- **WHEN** 检查 M4 实装的 FastAPI 路由
- **THEN** 没有任何路由 `StaticFiles(directory="mockup")`；mockup 文件仅作为 UX 设计参考

### Requirement: Mockup must be archived when implementation supersedes it

当 M4 后端实装（issue #28 / #29）合并入 main 后，仓库维护者 MUST 在 30 天内决定 mockup 状态：

- **保持顶层** 当且仅当 mockup 与实装 UI 仍然语义一致（每页都对得上后端模板）
- 否则 MUST 移到 `mockup/archive/<YYYY-MM-DD>-pre-<milestone>/` 并在 `mockup/README.md` 顶部声明"已归档"
- 顶层 `mockup/` MUST NOT 长期保留与实装不一致的页面

#### Scenario: 实装合并后未归档触发提醒

- **WHEN** issue #28 / #29 关闭后超过 30 天，`mockup/` 顶层仍是旧版本
- **THEN** 在下一次 OpenSpec change archive 或 milestone PR 中由 review 提示归档

#### Scenario: 归档保留可追溯

- **WHEN** mockup 已归档到 `mockup/archive/<date>-pre-m4/`
- **THEN** 该目录文件结构与归档时刻一致；不允许只保留部分文件

