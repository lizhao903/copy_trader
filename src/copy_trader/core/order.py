"""`Order` 值对象：交易所下单意图的不可变快照。

Spec（package-layout）没有强制字段，本实现基于 issue #9 acceptance 列出的最小集：
`id / account / symbol / side / type / qty / price / status / ts`。`market` 单
`price` 为 `None`；`limit` 单必须给出 `price`，由 `model_validator` 校验。
所有数量与价格统一 `Decimal`，禁止 float。

`OrderRequest` 是策略层向 `Exchange.place_order` 提交的下单意图值对象，
不含 `id / status / ts`（这些由交易所返回时填充进 `Order`）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]
OrderStatus = Literal["new", "partially_filled", "filled", "canceled", "rejected"]


class Order(BaseModel):
    """不可变下单记录。"""

    model_config = ConfigDict(frozen=True, strict=True)

    id: str
    account: str
    symbol: str
    side: OrderSide
    type: OrderType
    qty: Decimal
    price: Decimal | None
    status: OrderStatus
    ts: datetime

    @model_validator(mode="after")
    def _price_required_for_limit(self) -> Order:
        if self.type == "limit" and self.price is None:
            raise ValueError("limit order requires explicit price")
        if self.type == "market" and self.price is not None:
            raise ValueError("market order must not carry a price")
        return self


class OrderRequest(BaseModel):
    """不可变下单请求值对象。

    上层策略（execution / runners）通过 `Exchange.place_order(req)` 提交意图，
    `Exchange` 实现负责把它翻译成交易所私有协议、返回带 `id / status / ts`
    的 `Order`。`market` 单 `price=None`；`limit` 单必须给 `price`。
    """

    model_config = ConfigDict(frozen=True, strict=True)

    account: str
    symbol: str
    side: OrderSide
    type: OrderType
    qty: Decimal
    price: Decimal | None = None

    @model_validator(mode="after")
    def _price_required_for_limit(self) -> OrderRequest:
        if self.type == "limit" and self.price is None:
            raise ValueError("limit order requires explicit price")
        if self.type == "market" and self.price is not None:
            raise ValueError("market order must not carry a price")
        return self
