## 1. 骨架与共享资源

- [x] 1.1 创建目录 `mockup/` 与 `mockup/assets/`
- [x] 1.2 写 `mockup/assets/style.css`（< 250 行；含 D3 配色四档、topnav、容器、表格、表单、徽章 `.badge.running .stopped .errored .draft`、mockup-banner 红色横幅）
- [x] 1.3 写 `mockup/README.md`：用法（直接 `open mockup/index.html`）、16 页清单、"页面 → spec → issue 号" 映射表、修改导航 5 步同步操作（含 `grep -l 'class="brand"' mockup/*.html` 核对命令）、归档规则提醒（D6 红线）

## 2. 入口与总览

- [x] 2.1 `mockup/index.html`：顶部 mockup banner + topnav + 16 页导航网格（4×4 卡片），每张卡片含页面名 + 一句话描述 + spec/issue 注释
- [x] 2.2 `mockup/overview.html`：账户余额卡片 4 张（spot/alt/copy/hl_eth）+ unrealized PnL 摘要 + 系统健康徽章 + reconcile 状态摘要

## 3. Runners CRUD（3 页）

- [x] 3.1 `mockup/runners-list.html`：runner 实例表（id 截断 / name / venue / account / strategy / mode / status badge / heartbeat / actions），表头排序占位、行内启停/编辑/删除按钮、顶部"新建 runner"按钮
- [x] 3.2 `mockup/runners-detail.html`：单实例详情（基本信息卡 + 最近 fills 表 ≥ 8 行 + 当前持仓卡 + 最近日志预览 monospace 区块 + 启停/编辑/删除）；fixture id 用 `01HZ...` uuid7 风格
- [x] 3.3 `mockup/runners-create.html`：表单（name / venue 下拉 / account 下拉 / strategy 下拉 / mode 单选 / params_override JSON textarea）+ 提交按钮（仅 console.log）

## 4. 设置中心（8 页）

- [x] 4.1 `mockup/settings-index.html`：七段卡片导航（accounts / capital / pyramid / fixed_position / strategies / notify / risk）+ 顶部"配置 layer 状态"摘要（base / env / local / envvar 各几条覆盖）
- [x] 4.2 `mockup/settings-accounts.html`：账户 CRUD 表（name / venue / enabled toggle / credentials_alias / symbols 多选 chip）+ "新增账户"按钮
- [x] 4.3 `mockup/settings-capital.html`：资金分配表（account / strategy / quote_asset / max_quote_amount / reserve_quote_amount）+ 行内 inline edit 控件演示
- [x] 4.4 `mockup/settings-pyramid.html`：滚仓配置表（account / strategy / enabled / first_entry_fraction / add_trigger_pct / reserve_quote_usdt / max_rounds）+ "禁用此策略的滚仓"开关 + 与 fixed_position 互斥提示
- [x] 4.5 `mockup/settings-fixed-position.html`：固定仓位表（account / strategy / mode 单选 fixed_qty/fixed_quote / qty 或 quote_amount / max_price 可空）+ 与 pyramid 互斥提示
- [x] 4.6 `mockup/settings-strategies.html`：策略库列表（name / module 路径 / 默认参数表单），点击行展开默认参数详情
- [x] 4.7 `mockup/settings-notify.html`：四个 IM adapter 卡片（Slack / Telegram / Feishu / Dingtalk），各含 enabled toggle + webhook 字段 + 测试发送按钮（仅 console.log）+ 敏感字段显示 `<set>`/`<not set>`
- [x] 4.8 `mockup/settings-risk.html`：风控三项配置（日亏熔断阈值 / 总敞口上限 / 连亏熔断 N 笔），各含 enabled toggle + 数值输入 + 单位说明

## 5. 系统/审计页（3 页）

- [x] 5.1 `mockup/doctor.html`：分组展示 runtime root / env_tag / machine_id / lock 状态 / 子目录可写性 / 配置来源溯源表（每字段标 base/env/local/envvar/cli）/ ledger schema_version；敏感字段显示 `<redacted>`
- [x] 5.2 `mockup/audit.html`：dashboard_audit.log 时序条目（ts / 字段路径 / 层 / before/after 掩码），≥ 12 行示例，含一条敏感字段写入演示掩码效果
- [x] 5.3 `mockup/reconcile.html`：reconcile diff 时序，3 类 diff 各演示 ≥ 1 条（cache_drift 自动修正 / ledger_exchange_mismatch SAFE 模式 / unknown_position_on_exchange 拒绝启动）；状态色与 `style.css` 状态色一致

## 6. 校验与回归

- [x] 6.1 在浏览器实测打开 `mockup/index.html`，从导航进入每个子页确保不 404；每页 mockup banner 显示
- [x] 6.2 `find mockup -name '*.css'` 输出仅 `mockup/assets/style.css`；`grep -r 'cdn\.\|googleapis\|jsdelivr\|unpkg' mockup/` 输出 0 行
- [x] 6.3 `wc -l mockup/assets/style.css` < 250；`wc -l mockup/*.html` 单文件 < 200，总和 < 3000
- [x] 6.4 `grep -l 'class="brand"' mockup/*.html | wc -l` == 16（导航条全量同步检查）
- [x] 6.5 `mockup/README.md` 中的"页面 → spec → issue"映射表与实际 16 个文件一一对应
- [ ] 6.6 把仓库 push 后人工浏览所有页面截图存档到 `mockup/screenshots/`（可选，仅作 review 辅助）

## 7. 提交与收尾

- [x] 7.1 `git add mockup/ && git commit -m "feat(mockup): add static HTML mockups for 16 management pages"`
- [x] 7.2 push 到本仓 main（无对外影响、低风险）
- [x] 7.3 在 GitHub issue #28（FastAPI Dashboard 基线）与 #29（设置中心）下各添加一条 comment 引用 mockup 文件路径，作为后端实装的 UX 来源（issuecomment-4380859978 / issuecomment-4380860198）
- [ ] 7.4 用户触发 `/opsx:archive dashboard-html-mockups`
