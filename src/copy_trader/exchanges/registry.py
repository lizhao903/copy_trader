"""`ExchangeRegistry`：按 `<venue>.<market>` 名称解析具体 `Exchange` 实现。

按 `exchange-adapter` spec 第 4 条 Requirement：
- `register(name, factory)`：在子包导入期通过显式注册函数完成（不依赖反射扫描）
- `get(name) -> Exchange`：拼错 name 在启动期抛 `UnknownExchangeError` 并列出已注册名

命名规范 `<venue>.<market>`，例：`binance.spot`、`hyperliquid.spot`、
`paper.binance.spot`（paper 镜像保留多段 venue 路径，所以本实现只校验
「至少含一个 `.`」而非严格两段）。

`factory` 为零参可调用：注册时传入闭包/类本身，调用 `get(name)` 时执行
工厂得到 `Exchange` 实例。这样 venue 子包可以惰性初始化（建 HTTP client、
拉 symbol info 等）。
"""

from __future__ import annotations

from collections.abc import Callable

from copy_trader.exchanges.base import Exchange


class InvalidExchangeNameError(ValueError):
    """`name` 不符合 `<venue>.<market>` 命名规范。"""

    def __init__(self, name: str) -> None:
        super().__init__(f"invalid exchange name {name!r}: expected '<venue>.<market>' format")
        self.name = name


class UnknownExchangeError(KeyError):
    """请求的 `name` 未注册（启动期暴露拼写错误）。"""

    def __init__(self, name: str, registered: list[str]) -> None:
        sorted_names = sorted(registered)
        super().__init__(f"unknown exchange {name!r}; registered: {sorted_names}")
        self.name = name
        self.registered = sorted_names


class DuplicateExchangeError(ValueError):
    """同一 `name` 重复注册（避免 venue 子包静默互相覆盖）。"""

    def __init__(self, name: str) -> None:
        super().__init__(f"exchange {name!r} already registered")
        self.name = name


ExchangeFactory = Callable[[], Exchange]


class ExchangeRegistry:
    """name → factory 注册表。

    实例方法 `register / get / list` 维护一份私有 `dict[str, factory]`。
    模块级 `_default_registry` 提供单例入口（`register_default / get_default`），
    与 venue 子包导入期注册的约定配合使用。
    """

    def __init__(self) -> None:
        self._factories: dict[str, ExchangeFactory] = {}

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name or "." not in name:
            raise InvalidExchangeNameError(name)
        parts = name.split(".")
        if any(not part for part in parts):
            raise InvalidExchangeNameError(name)

    def register(self, name: str, factory: ExchangeFactory) -> None:
        """注册 venue 工厂；拼写不符或重复注册直接抛错。"""
        self._validate_name(name)
        if name in self._factories:
            raise DuplicateExchangeError(name)
        self._factories[name] = factory

    def get(self, name: str) -> Exchange:
        """按 name 解析 Exchange 实例；未注册抛 `UnknownExchangeError`。"""
        try:
            factory = self._factories[name]
        except KeyError:
            raise UnknownExchangeError(name, list(self._factories.keys())) from None
        return factory()

    def list(self) -> list[str]:
        """返回已注册 name 列表（排序，便于诊断输出）。"""
        return sorted(self._factories.keys())

    def clear(self) -> None:
        """测试辅助：清空注册表。生产代码不应调用。"""
        self._factories.clear()


_default_registry = ExchangeRegistry()


def register_default(name: str, factory: ExchangeFactory) -> None:
    """向模块级默认注册表登记 venue。"""
    _default_registry.register(name, factory)


def get_default(name: str) -> Exchange:
    """从模块级默认注册表解析 venue。"""
    return _default_registry.get(name)


def list_default() -> list[str]:
    """列出模块级默认注册表中的 name。"""
    return _default_registry.list()
