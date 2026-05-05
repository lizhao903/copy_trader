# exchange-adapter Specification

## Purpose
TBD - created by archiving change bootstrap-architecture. Update Purpose after archive.
## Requirements
### Requirement: All exchange access goes through a single `Exchange` Protocol

`copy_trader.exchanges.base.Exchange` Protocol MUST 是所有交易所交互的唯一入口，至少声明：`get_balance(asset)`、`fetch_position(symbol)`、`place_order(req)`、`cancel(order_id)`、`fetch_open_orders(symbol)`、`fetch_fills(symbol, since)`、`get_symbol_info(symbol)`、`round_price(symbol, price)`、`round_qty(symbol, qty)`、属性 `name: str`。每个交易所 MUST 在 `copy_trader.exchanges.<venue>/` 子包内提供该 Protocol 的具体实现。runner / execution / pnl / notify 等上层 MUST 仅依赖该 Protocol 与 `ExchangeRegistry`，MUST NOT 依赖具体实现类。

#### Scenario: 新增交易所只动一个子包

- **WHEN** 团队接入 OKX
- **THEN** 仅需在 `src/copy_trader/exchanges/okx/` 下新增实现并通过 `ExchangeRegistry.register(...)` 注册；`runners/`、`execution/`、`pnl/` 文件**零**改动即可被 CLI `--exchange okx.spot` 选用

#### Scenario: 直接 import 具体 Exchange 类被拦截

- **WHEN** 有人在 `runners/live.py` 写 `from copy_trader.exchanges.binance.spot import BinanceSpot`
- **THEN** import-linter contract 失败，要求改为 `ExchangeRegistry.get(name)`

### Requirement: Paper exchange shares the `Exchange` Protocol

`copy_trader.exchanges.paper` MUST 实现完整 `Exchange` Protocol，使用真实 marketdata 给出成交价、按可配置滑点 + 费率模拟成交，并把成交写入与 live exchange **相同结构** ledger（仅 `env_tag` 区分）。runner 层切换 live ↔ paper MUST 仅靠注入不同 Exchange 实例，MUST NOT 改任何业务代码。

#### Scenario: 同一策略在 live 与 paper 一致

- **WHEN** 同一份 `LiveRunner` 实例分别注入 `BinanceSpotExchange` 与 `PaperExchange`
- **THEN** 策略循环、风控、reconcile、PnL 计算逻辑完全一致；输出 ledger 仅 `env_tag` 与 `name` 字段不同

### Requirement: Exchange-specific symbol & precision rules stay inside the adapter

每个 venue 的下单精度、tick size、最小金额、限频规则 MUST 封装在该 venue 的 adapter 内部（`get_symbol_info` / `round_price` / `round_qty` / 内部 rate limiter）；上层 MUST NOT 通过 if-else 区分交易所。

#### Scenario: 上层不感知交易所差异

- **WHEN** `OrderRouter` 接收策略发出的 `OrderRequest`
- **THEN** 它只调用 `exchange.round_price` 与 `exchange.place_order`，不需要知道是 Binance 还是 Hyperliquid

### Requirement: ExchangeRegistry resolves implementations by name

`copy_trader.exchanges.registry.ExchangeRegistry` MUST 提供 `register(name, factory)` 与 `get(name) -> Exchange`；`name` 命名规范 `<venue>.<market>`（如 `binance.spot`、`hyperliquid.spot`、`paper.binance.spot`）。注册 MUST 在子包导入期通过 `__init_subclass__` 或显式注册函数完成；MUST NOT 依赖运行时反射扫描整个包。

#### Scenario: 拼错 name 在启动期暴露

- **WHEN** 配置文件指定 `exchange: bnance.spot`（拼写错误）
- **THEN** `ExchangeRegistry.get` 在启动期抛出 `UnknownExchangeError` 并列出已注册名称

### Requirement: First-batch venues are Binance spot and Hyperliquid spot

M2 / M3 milestone 完成时，`copy_trader.exchanges.{binance.spot, hyperliquid.spot, paper}` MUST 全部实现完整 Protocol、有镜像 paper 实现、有契约测试覆盖（同一 `OrderRequest` 序列在两个 venue 上分别产生预期 fills 与 ledger 行）。

#### Scenario: 契约测试覆盖两个 venue

- **WHEN** CI 跑 `tests/exchanges/test_protocol_contract.py`
- **THEN** Binance spot 与 Hyperliquid spot 的实现都被参数化测试用同一组合用例验证：`get_balance` 行为、`place_order` 边界（精度、最小金额）、`fetch_fills` 时间窗口、`round_price/round_qty` 取整方向

