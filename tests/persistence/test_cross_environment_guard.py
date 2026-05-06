"""Issue #13 acceptance：跨环境写入守卫端到端测试 + 稳定性回归。

模拟两台 machine（不同 ``machine_id``）/两个 env_tag 对同一 account
的同一份 ledger 反复写入，断言：

- 第一台用 (env_tag=dev, machine_id=A) 插入一条 schema_version=2 fill 成功
- 第二台用 (env_tag=dev, machine_id=B) 插入同 account 的 fill →
  抛 :class:`CrossEnvironmentWriteError`，错误消息含两侧 machine_id
- 跨 env_tag 维度同理（machine_id 一致，env_tag 不同）

「无 flakiness」：在同一个 db 文件上跑 100 次循环（每次重置 db），全部
都得到相同结果——等价于 issue 验收 "100 次 for loop 都过"。

测试只用 stdlib + ``copy_trader.persistence`` 公共 API；不涉及 HTTP /
exchange API。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from copy_trader.core import Fill
from copy_trader.persistence import (
    SCHEMA_VERSION,
    CrossEnvironmentWriteError,
    TradesRepo,
)


def _make_fill(
    *,
    id_: str,
    account: str = "spot",
    env_tag: str,
    machine_id: str,
) -> Fill:
    return Fill(
        id=id_,
        ts=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        account=account,
        symbol="BTCUSDT",
        side="buy",
        qty=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("0"),
        fee_asset="USDT",
        exchange_order_id=f"x-{id_}",
        env_tag=env_tag,
        machine_id=machine_id,
        schema_version=SCHEMA_VERSION,
    )


# --------------------------------------------------------------------------- #
# Scenario A: 跨 machine_id 写入被拒（machine_id 维度）
# --------------------------------------------------------------------------- #


def test_cross_machine_write_rejected_e2e(tmp_path: Path) -> None:
    """模拟 host-A 和 host-B 在同 env_tag/account 下争抢同一 ledger。

    断言：
    - host-A 第一次 insert 成功
    - host-B 第二次 insert 直接抛 ``CrossEnvironmentWriteError``
    - 错误消息含两侧 machine_id（``host-A`` / ``host-B``），不出现 fallback
      字符串如 ``unknown``
    """
    db = tmp_path / "trades.db"
    repo_a = TradesRepo(db, env_tag="dev", machine_id="host-A")
    repo_a.insert(_make_fill(id_="from-A", env_tag="dev", machine_id="host-A"))
    repo_a.close()

    repo_b = TradesRepo(db, env_tag="dev", machine_id="host-B")
    with pytest.raises(CrossEnvironmentWriteError) as exc_info:
        repo_b.insert(_make_fill(id_="from-B", env_tag="dev", machine_id="host-B"))
    msg = str(exc_info.value)
    assert "host-A" in msg
    assert "host-B" in msg
    assert "machine_id" in msg
    repo_b.close()


# --------------------------------------------------------------------------- #
# Scenario B: 跨 env_tag 写入被拒（env_tag 维度）
# --------------------------------------------------------------------------- #


def test_cross_env_tag_write_rejected_e2e(tmp_path: Path) -> None:
    """模拟同 host 不同 env_tag（dev → prod）写入同 account 被拒。"""
    db = tmp_path / "trades.db"
    repo_dev = TradesRepo(db, env_tag="dev", machine_id="host-1")
    repo_dev.insert(_make_fill(id_="from-dev", env_tag="dev", machine_id="host-1"))
    repo_dev.close()

    repo_prod = TradesRepo(db, env_tag="prod", machine_id="host-1")
    with pytest.raises(CrossEnvironmentWriteError) as exc_info:
        repo_prod.insert(_make_fill(id_="from-prod", env_tag="prod", machine_id="host-1"))
    msg = str(exc_info.value)
    assert "dev" in msg
    assert "prod" in msg
    assert "env_tag" in msg
    repo_prod.close()


# --------------------------------------------------------------------------- #
# 稳定性：100 次循环都得到相同结果（无 flakiness）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("iteration", range(100))
def test_cross_machine_guard_stable_100x(
    tmp_path_factory: pytest.TempPathFactory, iteration: int
) -> None:
    """100 次相同输入下，跨 machine 写入永远抛 ``CrossEnvironmentWriteError``。

    每次用独立 ``tmp_path``（避免上次 db 残留），等价于 issue body 要求的
    "for loop 100 次都过"。每次断言：
    - host-A 写入成功
    - host-B 写入抛错
    - 错误消息稳定包含两侧 machine_id
    """
    tmp_path = tmp_path_factory.mktemp(f"cross_machine_{iteration}")
    db = tmp_path / "trades.db"

    repo_a = TradesRepo(db, env_tag="dev", machine_id="host-A")
    repo_a.insert(_make_fill(id_="from-A", env_tag="dev", machine_id="host-A"))
    repo_a.close()

    repo_b = TradesRepo(db, env_tag="dev", machine_id="host-B")
    with pytest.raises(CrossEnvironmentWriteError) as exc_info:
        repo_b.insert(_make_fill(id_="from-B", env_tag="dev", machine_id="host-B"))
    msg = str(exc_info.value)
    assert "host-A" in msg, f"iteration={iteration} 缺 host-A：{msg}"
    assert "host-B" in msg, f"iteration={iteration} 缺 host-B：{msg}"
    repo_b.close()


@pytest.mark.parametrize("iteration", range(100))
def test_cross_env_tag_guard_stable_100x(
    tmp_path_factory: pytest.TempPathFactory, iteration: int
) -> None:
    """100 次相同输入下，跨 env_tag 写入永远抛 ``CrossEnvironmentWriteError``。"""
    tmp_path = tmp_path_factory.mktemp(f"cross_env_{iteration}")
    db = tmp_path / "trades.db"

    repo_dev = TradesRepo(db, env_tag="dev", machine_id="host-1")
    repo_dev.insert(_make_fill(id_="from-dev", env_tag="dev", machine_id="host-1"))
    repo_dev.close()

    repo_prod = TradesRepo(db, env_tag="prod", machine_id="host-1")
    with pytest.raises(CrossEnvironmentWriteError) as exc_info:
        repo_prod.insert(_make_fill(id_="from-prod", env_tag="prod", machine_id="host-1"))
    msg = str(exc_info.value)
    assert "dev" in msg, f"iteration={iteration} 缺 dev：{msg}"
    assert "prod" in msg, f"iteration={iteration} 缺 prod：{msg}"
    repo_prod.close()


# --------------------------------------------------------------------------- #
# 同 (env_tag, machine_id) 反复写入 100 次：永远成功（基线对照）
# --------------------------------------------------------------------------- #


def test_same_env_machine_writes_succeed_100x(tmp_path: Path) -> None:
    """100 次相同 (env_tag, machine_id) 写入：全部成功。

    既是稳定性基线（保证测试矩阵不会因 schema 状态漂移而误报），也覆盖了
    "同账户连续写入不会被自己拒绝" 的回归点。
    """
    db = tmp_path / "trades.db"
    repo = TradesRepo(db, env_tag="dev", machine_id="host-1")
    try:
        for i in range(100):
            repo.insert(_make_fill(id_=f"f-{i}", env_tag="dev", machine_id="host-1"))
        rows = repo.fetch(account="spot", symbol="BTCUSDT")
        assert len(rows) == 100
    finally:
        repo.close()
