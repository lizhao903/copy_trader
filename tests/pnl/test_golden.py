"""黄金测试：4 类组合 × 双模式（加权 / FIFO）= 8 个核心断言 + fee 扣除场景。

口径来自 `pnl-single-source` spec Requirement 5（"加仓后再止盈一半"），其余三类
组合按 spec 同样的"从 ledger 重建"原则推导：

1. 加仓：`buy 1@100, buy 1@110` → weighted avg=105/qty=2；FIFO qty=2 + 两批次
2. 加仓后部分止盈：`buy 1@100, buy 1@110, sell 1@130` → weighted realized=25 /
   FIFO realized=30（spec 黄金例）
3. 全平：`buy 1@100, buy 1@110, sell 2@130` → weighted (130-105)*2=50 /
   FIFO (130-100) + (130-110) = 50（双模式恰好相等）
4. 加仓后止损：`buy 1@100, sell 1@95` → weighted/FIFO 同 -5（单批次）

外加 fee 扣除场景：`buy 1@100, sell 1@130, fee=2` → realized = 30 - 2 = 28，
验证 `fill.fee` 直接从 gross realized 中扣减。

所有数值断言使用 `Decimal` 精确比较，禁用 `pytest.approx`。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from copy_trader.core.fill import Fill, FillSide
from copy_trader.pnl import PnlEngine, PnlMode

ACCOUNT = "acct-1"
SYMBOL = "BTCUSDT"


def _fill(
    side: FillSide,
    qty: str,
    price: str,
    *,
    fee: str = "0",
    seq: int = 1,
) -> Fill:
    """构造测试用 fill。`seq` 决定 id / order_id / ts 单调递增。"""
    return Fill(
        id=f"fill-{seq}",
        ts=datetime(2026, 1, 1, 12, 0, seq, tzinfo=UTC),
        account=ACCOUNT,
        symbol=SYMBOL,
        side=side,
        qty=Decimal(qty),
        price=Decimal(price),
        fee=Decimal(fee),
        fee_asset="USDT",
        exchange_order_id=f"o-{seq}",
        env_tag="test",
        machine_id="m-test",
        schema_version=2,
    )


def _engine(fills: list[Fill], mode: PnlMode) -> PnlEngine:
    return PnlEngine(account=ACCOUNT, symbol=SYMBOL, fills=fills, mode=mode)


# ---------- 1. 纯加仓 ----------


def test_add_position_weighted() -> None:
    """加仓：weighted 模式 avg_cost=105、qty=2、realized=0、unrealized 用当前价计算。"""
    fills = [_fill("buy", "1", "100", seq=1), _fill("buy", "1", "110", seq=2)]
    e = _engine(fills, "weighted")
    assert e.position().qty == Decimal("2")
    assert e.position().avg_cost == Decimal("105")
    assert e.realized() == Decimal("0")
    assert e.unrealized(Decimal("120")) == (Decimal("120") - Decimal("105")) * Decimal("2")


def test_add_position_fifo() -> None:
    """加仓：FIFO 模式 qty=2、avg_cost=（100+110）/2=105（两批次加权视图）、realized=0。"""
    fills = [_fill("buy", "1", "100", seq=1), _fill("buy", "1", "110", seq=2)]
    e = _engine(fills, "fifo")
    assert e.position().qty == Decimal("2")
    assert e.position().avg_cost == Decimal("105")
    assert e.realized() == Decimal("0")


# ---------- 2. 加仓后部分止盈（spec 黄金例） ----------


def test_partial_take_profit_weighted_25() -> None:
    """spec 黄金例：weighted realized = (130-105)*1 = 25。"""
    fills = [
        _fill("buy", "1", "100", seq=1),
        _fill("buy", "1", "110", seq=2),
        _fill("sell", "1", "130", seq=3),
    ]
    e = _engine(fills, "weighted")
    assert e.realized() == Decimal("25")
    assert e.position().qty == Decimal("1")
    assert e.position().avg_cost == Decimal("105")


def test_partial_take_profit_fifo_30() -> None:
    """spec 黄金例：FIFO realized = (130-100)*1 = 30；剩余批次 1@110。"""
    fills = [
        _fill("buy", "1", "100", seq=1),
        _fill("buy", "1", "110", seq=2),
        _fill("sell", "1", "130", seq=3),
    ]
    e = _engine(fills, "fifo")
    assert e.realized() == Decimal("30")
    assert e.position().qty == Decimal("1")
    assert e.position().avg_cost == Decimal("110")


# ---------- 3. 全平（双模式相同 = 50） ----------


def test_full_close_weighted_50() -> None:
    """全平：weighted realized = (130-105)*2 = 50；持仓归零、avg_cost 复位为 0。"""
    fills = [
        _fill("buy", "1", "100", seq=1),
        _fill("buy", "1", "110", seq=2),
        _fill("sell", "2", "130", seq=3),
    ]
    e = _engine(fills, "weighted")
    assert e.realized() == Decimal("50")
    assert e.position().qty == Decimal("0")
    assert e.position().avg_cost == Decimal("0")
    assert e.unrealized(Decimal("999")) == Decimal("0")


def test_full_close_fifo_50() -> None:
    """全平：FIFO realized = (130-100) + (130-110) = 50（与 weighted 数值恰好相等）。"""
    fills = [
        _fill("buy", "1", "100", seq=1),
        _fill("buy", "1", "110", seq=2),
        _fill("sell", "2", "130", seq=3),
    ]
    e = _engine(fills, "fifo")
    assert e.realized() == Decimal("50")
    assert e.position().qty == Decimal("0")
    assert e.position().avg_cost == Decimal("0")


# ---------- 4. 加仓后止损（单批次，双模式相同 = -5） ----------


def test_stop_loss_weighted_minus_5() -> None:
    """止损：weighted realized = (95-100)*1 = -5。"""
    fills = [_fill("buy", "1", "100", seq=1), _fill("sell", "1", "95", seq=2)]
    e = _engine(fills, "weighted")
    assert e.realized() == Decimal("-5")
    assert e.position().qty == Decimal("0")


def test_stop_loss_fifo_minus_5() -> None:
    """止损：FIFO 单批次出货 realized = (95-100)*1 = -5（与 weighted 同）。"""
    fills = [_fill("buy", "1", "100", seq=1), _fill("sell", "1", "95", seq=2)]
    e = _engine(fills, "fifo")
    assert e.realized() == Decimal("-5")
    assert e.position().qty == Decimal("0")


# ---------- 5. fee 扣除（额外场景） ----------


def test_fee_deducted_from_realized_weighted() -> None:
    """fee 扣除：weighted gross=(130-100)*1=30，扣 fee=2 后 realized=28。"""
    fills = [
        _fill("buy", "1", "100", seq=1),
        _fill("sell", "1", "130", fee="2", seq=2),
    ]
    e = _engine(fills, "weighted")
    assert e.realized() == Decimal("28")


def test_fee_deducted_from_realized_fifo() -> None:
    """fee 扣除：FIFO gross=30，扣 fee=2 后 realized=28（fee 一次性扣在该卖单）。"""
    fills = [
        _fill("buy", "1", "100", seq=1),
        _fill("sell", "1", "130", fee="2", seq=2),
    ]
    e = _engine(fills, "fifo")
    assert e.realized() == Decimal("28")


# ---------- 6. 不读 position cache（spec 红线） ----------


def test_engine_does_not_read_position_cache(tmp_path, monkeypatch) -> None:
    """红线：PnlEngine 不读 `state/position_*.json` —— 喂入纯 list[Fill] 且把 cwd
    切到空目录依然能算出正确 PnL，证明引擎完全无文件 IO。"""
    monkeypatch.chdir(tmp_path)
    fills = [
        _fill("buy", "1", "100", seq=1),
        _fill("buy", "1", "110", seq=2),
        _fill("sell", "1", "130", seq=3),
    ]
    e = _engine(fills, "weighted")
    assert e.realized() == Decimal("25")
    # tmp_path 没有任何 state/ 目录；如果引擎偷读 cache 一定 raise FileNotFoundError
    assert not (tmp_path / "state").exists()
