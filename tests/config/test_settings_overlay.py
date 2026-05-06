"""Issue #4 acceptance：Pydantic Settings 5 层 overlay + 业务 schema + LayerScope。

5 个场景：

1. 5 层来源优先级（CLI > envvar > local > env > base 各覆盖前一层）
2. 敏感字段守卫（yaml 中出现 binance_api_key 类字段 → ValidationError）
3. 缺失业务字段（capital.max_quote_amount 没填 → ValidationError 指向字段）
4. 互斥校验（同一 (account, strategy) 同时 pyramid.enabled=true + fixed_position）
5. LayerScope 映射（envvar 来的字段 field_layer_map() 返回 "envvar"）

所有测试用 tmp_path + 显式 envvars dict，不读用户真实 yaml，不写真实凭证。
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml
from pydantic import ValidationError

from copy_trader.config import Settings

# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


def _write_yaml(path: Path, payload: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


@pytest.fixture
def base_payload() -> dict[str, object]:
    """最小可用的 base 配置——所有必填字段都齐。"""
    return {
        "accounts": {
            "spot": {
                "venue": "binance_spot",
                "enabled": True,
                "credentials_alias": "BINANCE_SPOT",
                "symbols": ["BTCUSDT", "ETHUSDT"],
            },
        },
        "capital_allocation": [
            {
                "account": "spot",
                "strategy": "dca",
                "quote_asset": "USDT",
                "max_quote_amount": "1000",
                "reserve_quote_amount": "200",
            },
        ],
        "pyramid": [
            {
                "account": "spot",
                "strategy": "dca",
                "enabled": True,
                "first_entry_fraction": "0.25",
                "add_trigger_pct": "1.5",
                "reserve_quote_usdt": "200",
                "max_rounds": 4,
            },
        ],
        "fixed_position": [],
    }


@pytest.fixture
def config_dir(tmp_path: Path, base_payload: dict[str, object]) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    _write_yaml(cfg / "base.yaml", base_payload)
    _write_yaml(cfg / "dev.yaml", {})
    _write_yaml(cfg / "paper.yaml", {})
    _write_yaml(cfg / "prod.yaml", {})
    return cfg


# --------------------------------------------------------------------------- #
# Scenario 1: 五层来源优先级
# --------------------------------------------------------------------------- #


def test_five_layer_precedence(
    tmp_path: Path, base_payload: dict[str, object], config_dir: Path
) -> None:
    """每一层逐级覆盖前一层；最右优先级最高（CLI）。

    用 `accounts.spot.credentials_alias` 这个非敏感字符串字段做被动场。
    """
    # base 已写入 fixture，值为 "BINANCE_SPOT"
    # env 层覆盖
    _write_yaml(
        config_dir / "dev.yaml",
        {"accounts": {"spot": {"credentials_alias": "BINANCE_SPOT_FROM_ENV"}}},
    )
    # local 层
    home = tmp_path / "home"
    home.mkdir()
    _write_yaml(
        home / "config.yaml",
        {"accounts": {"spot": {"credentials_alias": "BINANCE_SPOT_FROM_LOCAL"}}},
    )

    # 仅有 base：值 = base
    s_base_only = Settings.load(config_dir=config_dir, env="paper", envvars={})
    assert s_base_only.accounts["spot"].credentials_alias == "BINANCE_SPOT"

    # base + env：值 = env
    s_with_env = Settings.load(config_dir=config_dir, env="dev", envvars={})
    assert s_with_env.accounts["spot"].credentials_alias == "BINANCE_SPOT_FROM_ENV"

    # base + env + local：值 = local
    s_with_local = Settings.load(
        config_dir=config_dir, env="dev", local_path=home / "config.yaml", envvars={}
    )
    assert s_with_local.accounts["spot"].credentials_alias == "BINANCE_SPOT_FROM_LOCAL"

    # base + env + local + envvar：值 = envvar
    envvars = {
        "COPY_TRADER_ACCOUNTS__SPOT__CREDENTIALS_ALIAS": "BINANCE_SPOT_FROM_ENVVAR",
    }
    s_with_envvar = Settings.load(
        config_dir=config_dir,
        env="dev",
        local_path=home / "config.yaml",
        envvars=envvars,
    )
    assert s_with_envvar.accounts["spot"].credentials_alias == "BINANCE_SPOT_FROM_ENVVAR"

    # base + env + local + envvar + cli：值 = cli
    s_with_cli = Settings.load(
        config_dir=config_dir,
        env="dev",
        local_path=home / "config.yaml",
        envvars=envvars,
        cli_overrides={
            "accounts": {"spot": {"credentials_alias": "BINANCE_SPOT_FROM_CLI"}},
        },
    )
    assert s_with_cli.accounts["spot"].credentials_alias == "BINANCE_SPOT_FROM_CLI"


# --------------------------------------------------------------------------- #
# Scenario 2: 敏感字段守卫
# --------------------------------------------------------------------------- #


def test_secret_in_yaml_rejected(
    tmp_path: Path, base_payload: dict[str, object], config_dir: Path
) -> None:
    """yaml 中误填 *_KEY/*_SECRET/*_TOKEN/*_PRIVATE_KEY 字段 → ValidationError。"""
    # 在 dev.yaml 下面塞一个 binance_api_key——这是 spec.md 明确点名的反例
    (config_dir / "dev.yaml").write_text(
        dedent(
            """
            accounts:
              spot:
                binance_api_key: AKxxxxxxxxxxxxxxxxxxxxx
            """
        ).strip(),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as excinfo:
        Settings.load(config_dir=config_dir, env="dev", envvars={})
    msg = str(excinfo.value)
    assert "binance_api_key" in msg
    assert "COPY_TRADER_" in msg

    # 同样测试 _SECRET / _TOKEN / _PRIVATE_KEY 后缀
    for sensitive_key in ("slack_bot_token", "hyperliquid_private_key", "webhook_secret"):
        (config_dir / "dev.yaml").write_text(
            yaml.safe_dump({"notify": {sensitive_key: "fake-value"}}),
            encoding="utf-8",
        )
        with pytest.raises(ValidationError) as exc2:
            Settings.load(config_dir=config_dir, env="dev", envvars={})
        assert sensitive_key in str(exc2.value)


# --------------------------------------------------------------------------- #
# Scenario 3: 缺失业务字段 → ValidationError 指向字段
# --------------------------------------------------------------------------- #


def test_missing_business_field_raises(tmp_path: Path) -> None:
    """capital_allocation[0] 漏 max_quote_amount → 报错指向该字段。"""
    cfg = tmp_path / "config"
    cfg.mkdir()
    bad = {
        "accounts": {
            "spot": {
                "venue": "binance_spot",
                "enabled": True,
                "credentials_alias": "BINANCE_SPOT",
                "symbols": ["BTCUSDT"],
            },
        },
        "capital_allocation": [
            {
                "account": "spot",
                "strategy": "dca",
                "quote_asset": "USDT",
                # 漏 max_quote_amount
                "reserve_quote_amount": "100",
            }
        ],
        "pyramid": [],
        "fixed_position": [],
    }
    _write_yaml(cfg / "base.yaml", bad)
    _write_yaml(cfg / "dev.yaml", {})

    with pytest.raises(ValidationError) as excinfo:
        Settings.load(config_dir=cfg, env="dev", envvars={})
    msg = str(excinfo.value)
    assert "max_quote_amount" in msg
    assert "capital_allocation" in msg


# --------------------------------------------------------------------------- #
# Scenario 4: pyramid 与 fixed_position 互斥
# --------------------------------------------------------------------------- #


def test_pyramid_fixed_position_mutex(tmp_path: Path) -> None:
    """同一 (account, strategy) 同时 pyramid.enabled=true + fixed_position → 报错。"""
    cfg = tmp_path / "config"
    cfg.mkdir()
    payload = {
        "accounts": {
            "spot": {
                "venue": "binance_spot",
                "enabled": True,
                "credentials_alias": "BINANCE_SPOT",
                "symbols": ["BTCUSDT"],
            },
        },
        "capital_allocation": [
            {
                "account": "spot",
                "strategy": "dca",
                "quote_asset": "USDT",
                "max_quote_amount": "1000",
                "reserve_quote_amount": "200",
            }
        ],
        "pyramid": [
            {
                "account": "spot",
                "strategy": "dca",
                "enabled": True,
                "first_entry_fraction": "0.25",
                "add_trigger_pct": "1.5",
                "reserve_quote_usdt": "200",
                "max_rounds": 4,
            }
        ],
        "fixed_position": [
            {
                "account": "spot",
                "strategy": "dca",
                "mode": "fixed_quote",
                "quote_amount": "100",
                "max_price": "200000",
            }
        ],
    }
    _write_yaml(cfg / "base.yaml", payload)
    _write_yaml(cfg / "dev.yaml", {})

    with pytest.raises(ValidationError) as excinfo:
        Settings.load(config_dir=cfg, env="dev", envvars={})
    msg = str(excinfo.value)
    assert "互斥" in msg or "mutex" in msg.lower() or "(spot,dca)" in msg


# --------------------------------------------------------------------------- #
# Scenario 5: LayerScope 映射
# --------------------------------------------------------------------------- #


def test_field_layer_map_provenance(
    tmp_path: Path, base_payload: dict[str, object], config_dir: Path
) -> None:
    """envvar 覆盖的字段 → field_layer_map() 报告 'envvar'；CLI 覆盖 → 'cli'。"""
    # base.yaml 已写入 credentials_alias=BINANCE_SPOT；dev.yaml 不覆盖
    home = tmp_path / "home"
    home.mkdir()
    _write_yaml(
        home / "config.yaml",
        # 在 local 层覆盖 max_rounds（嵌套在 list 内）—— list 整体替换，所以
        # 整段 pyramid 来源标 "local"
        {
            "accounts": {"spot": {"symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"]}},
        },
    )
    envvars = {
        "COPY_TRADER_ACCOUNTS__SPOT__CREDENTIALS_ALIAS": "FROM_ENVVAR",
    }
    s = Settings.load(
        config_dir=config_dir,
        env="dev",
        local_path=home / "config.yaml",
        envvars=envvars,
        cli_overrides={"accounts": {"spot": {"venue": "binance_spot_cli"}}},
    )
    layer_map = s.field_layer_map()

    # 来自 base：accounts.spot.enabled
    assert layer_map["accounts.spot.enabled"] == "base"
    # 来自 envvar：accounts.spot.credentials_alias（覆盖 base）
    assert layer_map["accounts.spot.credentials_alias"] == "envvar"
    # 来自 local：accounts.spot.symbols（list 整体替换）
    assert layer_map["accounts.spot.symbols"] == "local"
    # 来自 cli：accounts.spot.venue（覆盖 base）
    assert layer_map["accounts.spot.venue"] == "cli"

    # field_layer_map() 返回的是 dict 副本——修改不影响原对象
    layer_map["fake.path"] = "base"
    assert "fake.path" not in s.field_layer_map()
