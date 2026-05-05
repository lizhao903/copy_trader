## Why

`bootstrap-architecture` 已经把 `copy_trader` Dashboard 的功能面定型（`/overview` + `/runners` CRUD + `/settings` 统一设置中心 + `doctor` + 审计/对账日志查看），但每个页面长什么样、表单怎么排、列表用什么列、状态用什么色、设置项之间如何分组——这些 UX 细节还没落到任何可视的产物上。直接进 M4 写 FastAPI + Jinja2 容易先实现后改样，浪费往返。本变更先用**纯静态 HTML + 一份共享 CSS** 把所有管理页面拉一遍 mockup，让作者本人在写后端之前先把交互流走通；mockup 里只有占位假数据、不依赖任何外部 CDN、不连后端，可直接 `open mockup/index.html` 离线浏览。

## What Changes

- 新增顶层目录 `mockup/`，**只放静态资源**（HTML + CSS + 极少量纯 DOM JS）；不进 `src/`、不动后端、不进 `pyproject.toml` 依赖
- **每个管理功能一个独立 `.html` 文件**（用户硬要求），不允许把多个页面塞进单一大文件；页面之间通过顶部导航条互相超链接跳转
- 一份共享 `mockup/assets/style.css` 提供视觉一致性；HTML 页面之间不复用 partial（HTML 静态站点没有模板引擎），导航条以"复制 + 注释提醒"方式重复出现
- 不引入任何 JS 框架（无 React/Vue/Tailwind CDN/Bootstrap CDN）；如果某页需要 toggle/折叠，直接写 inline `<script>` ≤ 20 行
- 页面集合覆盖 `bootstrap-architecture` 设计文档中 Dashboard / CLI doctor / 审计 / 对账涉及的 **16 个管理界面**（详见 specs）：导航首页 / overview / runners 列表 / runner 详情 / runner 创建表单 / settings 索引 / settings 五段（accounts、capital、pyramid、fixed_position、strategies）/ notify / risk / doctor / audit / reconcile
- 数据全部 placeholder 假值（账户名 `spot/alt/copy/hl_eth`、symbol `BTCUSDT/ETHUSDT/SOL-USD/HL-PERP` 等），但保持类型和量级真实，便于评估视觉密度
- 提供 `mockup/README.md` 说明：用法（直接打开任一 HTML）、页面清单、与对应 spec / GitHub issue 的回链
- 本变更**不**取代后端 issue（M4 的 #28 / #29）；mockup 是这两个 issue 的"视觉前置"，作者用它对齐 UX 后再写代码

## Capabilities

### New Capabilities

- `dashboard-mockup`: 静态 HTML mockup 集合的契约（文件粒度=单页一个文件；offline-friendly；无外部 CDN；占位数据；导航一致；与 bootstrap-architecture spec 的页面映射）

### Modified Capabilities

<!-- 不修改任何主 spec；mockup 是视觉前置物，不属于运行时系统的能力 -->

## Impact

- **新增**：`mockup/` 目录，约 16 个 HTML 文件 + 1 个 CSS + 1 个 README
- **不影响**：`src/`、`config/`、`tests/`、`pyproject.toml`、`uv.lock`、CI 配置、生产部署、ledger 数据
- **不引入依赖**：纯静态资源，浏览器直接打开即可；无 webpack / npm / build step
- **后续承接**：M4 的 `#28 FastAPI Dashboard 基线` 与 `#29 Dashboard 设置中心` 在实装时把这些 mockup 翻译为 Jinja2 模板；mockup 文件本身不删除，归档到 `mockup/archive/` 留作 UX 演进对照
- **风险**：mockup 与最终实装容易"漂移"——一旦后端代码定稿，mockup 与 main 上的 UI 应当一次性对齐或归档；不允许 mockup 长期声称是"最新"
