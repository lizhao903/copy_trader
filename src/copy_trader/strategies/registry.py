"""`StrategyRegistry`：按名称解析具体 `Strategy` 实现。

仿 `ExchangeRegistry` 的结构（参考 `copy_trader.exchanges.registry`）：

- `register(name, factory)`：在子包导入期通过显式注册函数完成（不依赖反射扫描）。
- `get(name, **kwargs) -> Strategy`：拼错 name 在启动期抛 `UnknownStrategyError`
  并列出已注册名。`**kwargs` 透传给 factory，让策略接受参数化配置（如初始仓位、
  EMA 长度等）。
- `factory` 为 `Callable[..., Strategy]`：注册时传入闭包/类本身。

策略命名约定为单段标识（如 ``"hello"``、``"copy_v1"``、``"ema_crossover"``），
比 exchange 的 `<venue>.<market>` 更轻量；`_validate_name` 仅拒绝空串与首尾
空白，不强制 ``.`` 分隔。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from copy_trader.strategies.base import Strategy


class InvalidStrategyNameError(ValueError):
    """`name` 不符合策略命名规范（非空、不含首尾空白）。"""

    def __init__(self, name: str) -> None:
        super().__init__(f"invalid strategy name {name!r}: must be non-empty and trimmed")
        self.name = name


class UnknownStrategyError(KeyError):
    """请求的 `name` 未注册（启动期暴露拼写错误）。"""

    def __init__(self, name: str, registered: list[str]) -> None:
        sorted_names = sorted(registered)
        super().__init__(f"unknown strategy {name!r}; registered: {sorted_names}")
        self.name = name
        self.registered = sorted_names


class DuplicateStrategyError(ValueError):
    """同一 `name` 重复注册（避免静默互相覆盖）。"""

    def __init__(self, name: str) -> None:
        super().__init__(f"strategy {name!r} already registered")
        self.name = name


StrategyFactory = Callable[..., Strategy]


class StrategyRegistry:
    """name → factory 注册表。

    实例方法 `register / get / list` 维护一份私有 `dict[str, factory]`。
    模块级 `_default_registry` 提供单例入口（`register_default / get_default
    / list_default`），与策略子包导入期注册的约定配合使用。
    """

    def __init__(self) -> None:
        self._factories: dict[str, StrategyFactory] = {}

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name or name != name.strip():
            raise InvalidStrategyNameError(name)

    def register(self, name: str, factory: StrategyFactory) -> None:
        """注册策略工厂；拼写不符或重复注册直接抛错。"""
        self._validate_name(name)
        if name in self._factories:
            raise DuplicateStrategyError(name)
        self._factories[name] = factory

    def get(self, name: str, /, **kwargs: Any) -> Strategy:
        """按 name 解析 Strategy 实例；未注册抛 `UnknownStrategyError`。

        `name` 为 positional-only（避免与 factory kwargs 冲突），`**kwargs`
        透传给 factory，便于在启动期传入策略参数（如自定义 `name`）。
        """
        try:
            factory = self._factories[name]
        except KeyError:
            raise UnknownStrategyError(name, list(self._factories.keys())) from None
        return factory(**kwargs)

    def list(self) -> list[str]:
        """返回已注册 name 列表（排序，便于诊断输出）。"""
        return sorted(self._factories.keys())

    def clear(self) -> None:
        """测试辅助：清空注册表。生产代码不应调用。"""
        self._factories.clear()


_default_registry = StrategyRegistry()


def register_default(name: str, factory: StrategyFactory) -> None:
    """向模块级默认注册表登记策略。"""
    _default_registry.register(name, factory)


def get_default(name: str, /, **kwargs: Any) -> Strategy:
    """从模块级默认注册表解析策略。"""
    return _default_registry.get(name, **kwargs)


def list_default() -> list[str]:
    """列出模块级默认注册表中的 name。"""
    return _default_registry.list()
