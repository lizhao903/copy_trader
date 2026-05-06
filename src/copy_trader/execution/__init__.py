"""执行层：把策略 Intent 落到交易所订单，并联动 pnl / persistence / notify。

公共 API（issue #11）：

- :class:`ReconcileService`：启动期三级 diff（cache / ledger / exchange）
- :class:`ReconcileReport`、:class:`ReconcileEvent`：诊断结果与单条事件
- :class:`ReconcileError`、:class:`UnknownPositionError`：异常类
- :data:`DEFAULT_QTY_TOLERANCE`：ledger qty / exchange qty 默认容差
"""

from copy_trader.execution.reconciler import (
    DEFAULT_QTY_TOLERANCE,
    ReconcileError,
    ReconcileEvent,
    ReconcileReport,
    ReconcileService,
    UnknownPositionError,
)

__all__ = [
    "DEFAULT_QTY_TOLERANCE",
    "ReconcileError",
    "ReconcileEvent",
    "ReconcileReport",
    "ReconcileService",
    "UnknownPositionError",
]
