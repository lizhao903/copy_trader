"""`Money` 值对象：`Decimal` 金额 + 货币单位，跨币种算术显式失败。

金融场景下绝不允许 float（IEEE 754 误差直接污染 ledger / PnL），所以本模块统一
用 `decimal.Decimal`。`Money` 自身做 `frozen=True`，加减运算只在同 `currency`
内部允许，跨币种抛 `CurrencyMismatchError`，由调用方显式做汇率换算后再合并。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict


class CurrencyMismatchError(ValueError):
    """跨货币算术错误：两侧 `currency` 不同。"""

    def __init__(self, left: str, right: str) -> None:
        super().__init__(f"currency mismatch: {left!r} vs {right!r}")
        self.left = left
        self.right = right


class Money(BaseModel):
    """不可变金额值对象。

    - `amount`：`Decimal`，禁止 float
    - `currency`：ISO-4217 或交易所原生币种符号（USDT/USDC/BTC/...）
    """

    model_config = ConfigDict(frozen=True, strict=True)

    amount: Decimal
    currency: str

    def _check_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(self.currency, other.currency)

    def __add__(self, other: Money) -> Self:
        self._check_same_currency(other)
        return type(self)(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Self:
        self._check_same_currency(other)
        return type(self)(amount=self.amount - other.amount, currency=self.currency)

    def __neg__(self) -> Self:
        return type(self)(amount=-self.amount, currency=self.currency)
