# copy_trader（王牌带单员）

「王牌带单员」是一个量化跟单交易系统：监听上游交易者（master）信号，在多个下游账户（follower）按比例 / 风控策略复制下单，并提供回放、实盘 / 模拟切换、PnL 复核与 Dashboard。

> 仓库名 `copy_trader` 与 PyPI 包名仍以 `copy_trader` 为准；与早期参考仓库 [`lizhao903/autotrader`](https://github.com/lizhao903/autotrader) **无任何代码继承关系**——本仓 production code 任何路径都不得反向引用 autotrader 的目录与硬编码常量（由 issue #6 在 CI 中静态扫描 enforce）。

当前里程碑 **M0（脚手架）** 已完成 6/7：包骨架、import-linter 单向依赖图、运行时根目录解析与锁文件、5 层 overlay 配置、Typer CLI + `doctor`、CI 门栏。M1（exchange adapter）+ M2（marketdata）+ M3（execution）+ M4（dashboard / reconcile）按 milestone 顺序推进，详见 `openspec/`。

## Onboard 三件套

新机器从 zero 到 `copy-trader doctor` 退出码 0：

```bash
# 1. 装 uv（Python toolchain；详见 https://docs.astral.sh/uv/getting-started/installation/）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 同步依赖到 .venv（uv.lock frozen，保证可重现）
uv sync --frozen

# 3. 启动自检：必须显式设置 COPY_TRADER_ENV ∈ {dev, paper, prod}
COPY_TRADER_ENV=dev uv run copy-trader doctor
```

`doctor` 会输出当前进程解析到的 `COPY_TRADER_HOME / COPY_TRADER_ENV / machine_id`、5 个运行时子目录的可写性、当前 5 层 overlay 配置摘要（敏感字段已掩码）、以及 ledger schema_version 占位。任何步骤失败都会提示具体错误来源（环境变量缺失 / lock 文件不一致 / 子目录不可写等）。

> 没有 `pip install`、没有 `requirements.txt`：`uv` 是本仓**唯一**支持的 Python toolchain（spec: `openspec/specs/tooling-uv/spec.md`）。

## CLI 速查表

```bash
uv run copy-trader <subcommand> [options]
```

| 子命令       | 状态        | 说明                                                          |
| ------------ | ----------- | ------------------------------------------------------------- |
| `doctor`     | M0 已实装   | 运行时根目录、env、machine_id、配置摘要、子目录可写性自检     |
| `run`        | M3+ 占位    | 实盘 / paper 主循环（exchange ↔ marketdata ↔ execution）      |
| `paper`      | M3+ 占位    | 显式 paper 模式 wrapper（与 `COPY_TRADER_ENV=paper` 等价）    |
| `backtest`   | M2+ 占位    | 历史 K 线回放回测                                             |
| `reconcile`  | M4 占位     | 跨账户 PnL 与持仓对账，比对 ledger 与交易所返回               |
| `dashboard`  | M4 占位     | 启动 Dashboard 服务（HTML 原型见 `mockup/`）                  |
| `registry`   | M4 占位     | strategy / exchange / IM 注册表查询与诊断                     |

> 占位子命令调用会打印 `<name> — pending implementation in <milestone>` 并以退出码 1 返回；`--help` 始终可用、不会 import error。

## 目录结构概览

```
copy_trader/
├── src/copy_trader/         # 源码（11 个子包，单向依赖图见 .importlinter）
│   ├── core/                # 通用领域类型、常量、错误（被所有子包依赖）
│   ├── exchanges/           # 交易所 adapter（Binance / Bybit / OKX 等）
│   ├── marketdata/          # 行情订阅、K 线缓存、ws 重连
│   ├── strategies/          # 跟单策略（比例 / 反向 / 风控过滤）
│   ├── execution/           # 下单引擎、订单生命周期、滑点保护
│   ├── pnl/                 # PnL 计算、跨账户对账
│   ├── persistence/         # ledger（SQLite）、schema 迁移
│   ├── notify/              # Slack / TG / 邮件 IM 通知
│   ├── runners/             # 主循环（run / paper / backtest）
│   ├── config/              # 5 层 overlay Settings + 运行时根目录解析
│   └── cli/                 # Typer 入口（main.py 暴露 `copy-trader`）
├── tests/                   # pytest 用例（默认 disable-socket，禁真实 API）
├── bin/                     # autonomous driver：cron 唤醒消费 issue 的脚本
├── mockup/                  # Dashboard HTML 原型（开发阶段静态预览）
├── openspec/                # OpenSpec spec-driven 工作流（specs / changes）
├── pyproject.toml           # uv + ruff + mypy + pytest 单一来源
├── uv.lock                  # 锁文件（必须入库；CI `uv sync --frozen`）
├── .importlinter            # 单向依赖图契约
├── CLAUDE.md                # Claude Code / 各 harness 的工作约定（含 driver）
└── README.md                # 当前文件
```

子包之间的依赖方向被 `import-linter` 强制：`cli` 只能依赖 `config`；`runners` 是唯一允许 import 业务子包的层；`core` 不依赖任何业务子包。

## 运行时根目录约定（COPY_TRADER_HOME / COPY_TRADER_ENV）

所有 state、logs、pids、db、secrets 写入路径都从 `$COPY_TRADER_HOME/{state,logs,pids,db,secrets}/` 派生。运行时根目录解析顺序：CLI `--home` 参数 > `COPY_TRADER_HOME` 环境变量 > 按 `COPY_TRADER_ENV` 取默认值。

| `COPY_TRADER_ENV` | `COPY_TRADER_HOME` 默认值     | 用途                                       |
| ----------------- | ----------------------------- | ------------------------------------------ |
| `dev`             | `./var/dev/`（相对 CWD）      | 本机开发与 driver 自托管 issue 实装        |
| `paper`           | `./var/paper/`（相对 CWD）    | 模拟交易（接交易所但不下真单）             |
| `prod`            | `/var/lib/copy_trader/`（绝对）| 生产实盘                                  |

5 个固定子目录用途：

| 子目录    | 用途                                                                          |
| --------- | ----------------------------------------------------------------------------- |
| `state/`  | runtime lock（`.runtime_lock.json`）、`machine_id`、driver 状态、临时控制文件 |
| `logs/`   | 应用日志、driver 唤醒日志、subagent stream-json 输出                          |
| `pids/`   | 长进程 PID 文件（运行时单例守护用）                                           |
| `db/`     | SQLite ledger（订单 / 成交 / PnL 记账）                                       |
| `secrets/`| 加密后的 API key / token；命名以 `_KEY / _SECRET / _TOKEN / _PRIVATE_KEY` 结尾 |

启动期 `runtime-isolation` spec 强制：

- `COPY_TRADER_ENV` 缺失 → fail-fast，提示有效取值；
- 运行时锁文件 `state/.runtime_lock.json` 与当前进程 `env_tag / machine_id` 不一致 → 拒绝启动（`doctor` 例外，会以告警形式打印不一致项，方便排查）；
- 缺失子目录会以 `0700` 自动创建。

详细规范：`openspec/specs/runtime-isolation/spec.md`。

## 进一步阅读

- `CLAUDE.md` — Claude Code / Cursor / Codex 等 harness 的工作约定（OpenSpec 工作流、autonomous driver、五件套校验门）
- `openspec/specs/` — 已 archive 的 spec（tooling-uv / runtime-isolation / config-overlay / cli-doctor / ci-baseline / package-skeleton / autonomous-driver / dashboard-mockup）
- `openspec/changes/` — 进行中的 OpenSpec change

## 许可证

见 [LICENSE](LICENSE)。
