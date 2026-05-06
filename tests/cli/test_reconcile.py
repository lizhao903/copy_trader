"""Issue #13 acceptance：``copy-trader reconcile`` 5 场景 + smoke。

5 个场景对齐 issue body 的 CLI 验收清单：

1. 空 ledger：``reconcile --account spot`` → 退出 0 + 输出含 "已对齐"。
2. ledger 与 fake exchange 一致：退出 0 + 输出含 reconcile OK。
3. ledger qty=1，fake exchange qty=0.5：退出 0 + 输出含 SAFE / mismatch。
4. fake exchange 有未知 BTC 余额，不传 ``--acknowledge-unknown``：退出 1
   + 输出含 ``--acknowledge-unknown`` 提示。
5. 同 4 但带 ``--acknowledge-unknown``：退出 0。

测试用 ``typer.testing.CliRunner``；``copy_trader.cli.main.default_exchange_factory``
被 monkeypatch 成返回 ``FakeExchange`` 实例，避免真实 HTTP / 注册表依赖。
``TradesRepo`` 走 issue #8 真实实现 + ``tmp_path`` SQLite。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from copy_trader.cli.main import app
from copy_trader.core import Fill, Order, OrderRequest, Position, SymbolInfo
from copy_trader.persistence import TradesRepo

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeExchange:
    """``Exchange`` Protocol 的内存假实现（与 execution 测试里的 FakeExchange 同构）。"""

    name = "fake.spot"

    def __init__(
        self,
        positions: dict[str, Position] | None = None,
        balances: dict[str, Decimal] | None = None,
    ) -> None:
        self._positions = positions or {}
        self._balances = balances or {}

    def get_balance(self, asset: str) -> Decimal:
        return self._balances.get(asset, Decimal("0"))

    def fetch_position(self, symbol: str) -> Position:
        if symbol in self._positions:
            return self._positions[symbol]
        return Position(
            account="fake",
            symbol=symbol,
            qty=Decimal("0"),
            avg_cost=Decimal("0"),
            realized_pnl=Decimal("0"),
            updated_ts=datetime(2026, 1, 1, tzinfo=UTC),
        )

    def place_order(self, req: OrderRequest) -> Order:  # pragma: no cover
        raise NotImplementedError

    def cancel(self, order_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:  # pragma: no cover
        return []

    def fetch_fills(
        self, symbol: str, since: datetime | None = None
    ) -> list[Fill]:  # pragma: no cover
        return []

    def get_symbol_info(self, symbol: str) -> SymbolInfo:  # pragma: no cover
        raise NotImplementedError

    def round_price(self, symbol: str, price: Decimal) -> Decimal:  # pragma: no cover
        return price

    def round_qty(self, symbol: str, qty: Decimal) -> Decimal:  # pragma: no cover
        return qty


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """每个 test 都从干净 env 开始，避免外部 shell / 同 session 污染。"""
    monkeypatch.delenv("COPY_TRADER_ENV", raising=False)
    monkeypatch.delenv("COPY_TRADER_HOME", raising=False)
    monkeypatch.delenv("COPY_TRADER_CONFIG_DIR", raising=False)
    yield


@pytest.fixture
def repo_config_dir() -> Path:
    """指向仓库根 ``config/`` 的固定路径（doctor 测试同款发现逻辑）。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config" / "base.yaml"
        if candidate.is_file():
            return candidate.parent
    raise RuntimeError("找不到仓库根 config/ 目录")


def _seed_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COPY_TRADER_ENV", "dev")
    monkeypatch.setenv("COPY_TRADER_HOME", str(tmp_path))


def _patch_exchange_factory(monkeypatch: pytest.MonkeyPatch, exchange: FakeExchange) -> None:
    """把 cli.main 引用的 ``default_exchange_factory`` 替换成返回 fake exchange。"""
    monkeypatch.setattr(
        "copy_trader.cli.main.default_exchange_factory",
        lambda venue: exchange,
    )


def _seed_ledger_fill(
    home: Path,
    *,
    account: str = "spot",
    symbol: str = "BTCUSDT",
    qty: str = "1",
    price: str = "100",
) -> None:
    """直接用 TradesRepo 写一笔 fill，让后续 cli 调用看到非空 ledger。"""
    db_dir = home / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    # machine_id 与 cli 进程要一致——我们读 state/.machine_id（resolve_runtime
    # 已生成）。
    machine_id_path = home / "state" / ".machine_id"
    machine_id = machine_id_path.read_text(encoding="utf-8").strip()
    repo = TradesRepo(db_dir / "ledger.db", env_tag="dev", machine_id=machine_id)
    fill = Fill(
        id="seed-1",
        ts=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        account=account,
        symbol=symbol,
        side="buy",
        qty=Decimal(qty),
        price=Decimal(price),
        fee=Decimal("0"),
        fee_asset="USDT",
        exchange_order_id="x-seed",
        env_tag="dev",
        machine_id=machine_id,
        schema_version=2,
    )
    repo.insert(fill)
    repo.close()


def _bootstrap_runtime(tmp_path: Path, repo_config_dir: Path) -> None:
    """先跑一次 doctor 让 ``state/.machine_id`` / 锁文件就位。

    reconcile 自身也会做 resolve_runtime；但 ledger seed 在 cli 调用之前需要
    machine_id 与 cli 进程一致，所以测试里先跑一次 doctor 落锁。
    """
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["doctor", "--config-dir", str(repo_config_dir)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout


# --------------------------------------------------------------------------- #
# Scenario 1: 空 ledger → 退出 0 + 输出含 "已对齐"
# --------------------------------------------------------------------------- #


def test_reconcile_empty_ledger_outputs_aligned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo_config_dir: Path,
) -> None:
    _seed_env(monkeypatch, tmp_path)
    _bootstrap_runtime(tmp_path, repo_config_dir)
    _patch_exchange_factory(monkeypatch, FakeExchange())

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["reconcile", "--account", "spot", "--config-dir", str(repo_config_dir)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    assert "已对齐" in result.stdout


# --------------------------------------------------------------------------- #
# Scenario 2: ledger 与 fake exchange 一致 → 退出 0 + reconcile OK
# --------------------------------------------------------------------------- #


def test_reconcile_ledger_matches_exchange_outputs_ok(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo_config_dir: Path,
) -> None:
    _seed_env(monkeypatch, tmp_path)
    _bootstrap_runtime(tmp_path, repo_config_dir)
    _seed_ledger_fill(tmp_path, qty="1", price="100")

    fake = FakeExchange(
        positions={
            "BTCUSDT": Position(
                account="spot",
                symbol="BTCUSDT",
                qty=Decimal("1"),
                avg_cost=Decimal("100"),
                realized_pnl=Decimal("0"),
                updated_ts=datetime(2026, 1, 1, tzinfo=UTC),
            )
        },
        balances={"BTC": Decimal("1")},
    )
    _patch_exchange_factory(monkeypatch, fake)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["reconcile", "--account", "spot", "--config-dir", str(repo_config_dir)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    assert "reconcile OK" in result.stdout
    # 不应触发 SAFE / FATAL
    assert "FATAL" not in result.stdout
    assert "SAFE" not in result.stdout


# --------------------------------------------------------------------------- #
# Scenario 3: ledger qty=1，exchange qty=0.5 → 退出 0 + SAFE / mismatch
# --------------------------------------------------------------------------- #


def test_reconcile_ledger_exchange_mismatch_safe_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo_config_dir: Path,
) -> None:
    _seed_env(monkeypatch, tmp_path)
    _bootstrap_runtime(tmp_path, repo_config_dir)
    _seed_ledger_fill(tmp_path, qty="1", price="100")

    fake = FakeExchange(
        positions={
            "BTCUSDT": Position(
                account="spot",
                symbol="BTCUSDT",
                qty=Decimal("0.5"),
                avg_cost=Decimal("100"),
                realized_pnl=Decimal("0"),
                updated_ts=datetime(2026, 1, 1, tzinfo=UTC),
            )
        },
        balances={"BTC": Decimal("1")},
    )
    _patch_exchange_factory(monkeypatch, fake)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["reconcile", "--account", "spot", "--config-dir", str(repo_config_dir)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    assert "SAFE" in result.stdout
    assert "mismatch" in result.stdout.lower()
    # report.fatal=False
    assert "FATAL" not in result.stdout


# --------------------------------------------------------------------------- #
# Scenario 4: 未知 BTC 余额, 不传 ack → 退出 1 + 含 --acknowledge-unknown
# --------------------------------------------------------------------------- #


def test_reconcile_unknown_position_rejects_without_ack(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo_config_dir: Path,
) -> None:
    _seed_env(monkeypatch, tmp_path)
    _bootstrap_runtime(tmp_path, repo_config_dir)
    # 注意：不写 ledger fills，这样交易所 BTC 余额=0.5 + 没 fills → unknown

    fake = FakeExchange(balances={"BTC": Decimal("0.5")})
    _patch_exchange_factory(monkeypatch, fake)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["reconcile", "--account", "spot", "--config-dir", str(repo_config_dir)],
        catch_exceptions=False,
    )
    assert result.exit_code == 1, result.stdout
    assert "FATAL" in result.stdout
    assert "--acknowledge-unknown" in result.stdout


# --------------------------------------------------------------------------- #
# Scenario 5: 同 4 但带 --acknowledge-unknown → 退出 0
# --------------------------------------------------------------------------- #


def test_reconcile_unknown_position_acknowledged_passes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo_config_dir: Path,
) -> None:
    _seed_env(monkeypatch, tmp_path)
    _bootstrap_runtime(tmp_path, repo_config_dir)

    fake = FakeExchange(balances={"BTC": Decimal("0.5")})
    _patch_exchange_factory(monkeypatch, fake)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "reconcile",
            "--account",
            "spot",
            "--acknowledge-unknown",
            "--config-dir",
            str(repo_config_dir),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    assert "FATAL" not in result.stdout
    # 仍应展示有 events（acknowledged），结尾走 "reconcile OK" 分支
    assert "reconcile OK" in result.stdout


# --------------------------------------------------------------------------- #
# Smoke: account 不存在 → 退出 1
# --------------------------------------------------------------------------- #


def test_reconcile_unknown_account_exits_1(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo_config_dir: Path,
) -> None:
    _seed_env(monkeypatch, tmp_path)
    _bootstrap_runtime(tmp_path, repo_config_dir)
    _patch_exchange_factory(monkeypatch, FakeExchange())

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "reconcile",
            "--account",
            "nonexistent",
            "--config-dir",
            str(repo_config_dir),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 1, result.stdout
    assert "nonexistent" in result.stdout
