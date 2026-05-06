"""Binance venue 子包：spot 实装 + 默认注册表登记。

按 `exchange-adapter` spec 第 4 条 Requirement，导入本子包即在
`_default_registry` 登记 `binance.spot` 工厂；上层只需 `import
copy_trader.exchanges.binance`（或显式调用 `register_default`）即可让
`get_default("binance.spot")` 解析出实例。

工厂签名为 `(**kwargs) -> BinanceSpot`，运行期由 runner 注入
`api_key / api_secret / testnet` 等参数；`ExchangeRegistry` 的
`ExchangeFactory` 类型 `Callable[[], Exchange]` 是「无参可调用」，所以这里
封一层 closure，把外部需要传参的工厂写成「先收参数 → 返回零参 closure」。
"""

from __future__ import annotations

from copy_trader.exchanges.binance.spot import BinanceSpot
from copy_trader.exchanges.registry import (
    DuplicateExchangeError,
    ExchangeFactory,
    register_default,
)


def make_binance_spot_factory(
    api_key: str,
    api_secret: str,
    testnet: bool = False,
) -> ExchangeFactory:
    """生成零参 ExchangeFactory：注入 key/secret/testnet 后由注册表延迟实例化。"""

    def _factory() -> BinanceSpot:
        return BinanceSpot(api_key=api_key, api_secret=api_secret, testnet=testnet)

    return _factory


def _register_default_binance_spot() -> None:
    """子包导入期登记 `binance.spot`；运行参数走 lambda，启动时不真打 API。

    注册的工厂用空 key/secret 占位，便于上层在 dry-run / 测试场景拿到一个 stub
    实例；生产代码 SHOULD 通过 `register_default("binance.spot", make_binance_spot_factory(...))`
    覆盖（先 `clear()` 再注册），或者直接 `BinanceSpot(...)` 自己持有。
    """
    try:
        register_default(
            "binance.spot",
            lambda: BinanceSpot(api_key="", api_secret="", testnet=False),
        )
    except DuplicateExchangeError:
        # 测试或多次导入幂等：同名已注册视为成功
        pass


_register_default_binance_spot()


__all__ = [
    "BinanceSpot",
    "make_binance_spot_factory",
]
