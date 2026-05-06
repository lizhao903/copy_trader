"""Pydantic Settings 多层 overlay 加载器。

实现 issue #4 / openspec/specs/config-overlay/spec.md 的核心契约：

1. 5 层 overlay：base.yaml → <env>.yaml → local.yaml → envvar (COPY_TRADER_*)
   → CLI；右侧优先级高。
2. 敏感字段守卫：yaml 中出现命名后缀为 `_KEY` / `_SECRET` / `_TOKEN` /
   `_PRIVATE_KEY` 的字段 → 加载阶段抛 ValidationError，引导改走 envvar 或
   secrets/.env。
3. 4 段必填业务 schema：accounts / capital_allocation / pyramid /
   fixed_position；同一 (account, strategy) 在 pyramid.enabled=true 下不允许
   同时配 fixed_position（互斥）。
4. `Settings.field_layer_map() -> dict[str, LayerScope]` 给出每个叶子字段
   实际来源层（base / env / local / envvar / cli），供 `copy-trader doctor`
   与 dashboard 设置中心使用。

注意：本文件只依赖 stdlib + pydantic + pyyaml；config 子包按 import-linter
契约 `config-only-core` 不允许 import 任何其他业务子包。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

LayerScope = Literal["base", "env", "local", "envvar", "cli"]
"""字段在最终 Settings 中的实际来源层。

注意：spec.md 把 `layer_scope` 描述为「字段允许写入的最高层」（schema metadata，
3 选一：base/env/local）；issue #4 acceptance 则要求 `field_layer_map()` 返回
字段「实际来源层」（5 选一）。本实现按 issue #4 acceptance 的语义实装；后续
若需暴露 schema 维度的 layer_scope，再加一个 `Settings.field_write_scope_map()`。
"""

LAYER_ORDER: tuple[LayerScope, ...] = ("base", "env", "local", "envvar", "cli")

_SENSITIVE_SUFFIXES: tuple[str, ...] = (
    "_key",
    "_secret",
    "_token",
    "_private_key",
)

ENVVAR_PREFIX = "COPY_TRADER_"


# --------------------------------------------------------------------------- #
# 业务 schema 段
# --------------------------------------------------------------------------- #


class _StrictModel(BaseModel):
    """禁止额外字段 + 不可变，所有业务子模型继承。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class AccountConfig(_StrictModel):
    """单账户配置：venue 引用 ExchangeRegistry 名，credentials_alias 是 envvar 前缀。"""

    venue: str
    enabled: bool
    credentials_alias: str
    symbols: list[str] = Field(min_length=1)


class CapitalSlice(_StrictModel):
    """`(account, strategy)` 颗粒的资金分配。"""

    account: str
    strategy: str
    quote_asset: str
    max_quote_amount: Decimal
    reserve_quote_amount: Decimal


class PyramidConfig(_StrictModel):
    """金字塔加仓策略参数；颗粒 `(account, strategy)`。"""

    account: str
    strategy: str
    enabled: bool = True
    first_entry_fraction: Decimal
    add_trigger_pct: Decimal
    reserve_quote_usdt: Decimal
    max_rounds: int = Field(ge=1)


class FixedPositionConfig(_StrictModel):
    """定额仓位策略参数；与同 `(account, strategy)` 的 pyramid.enabled=true 互斥。"""

    account: str
    strategy: str
    mode: Literal["fixed_qty", "fixed_quote"]
    qty: Decimal | None = None
    quote_amount: Decimal | None = None
    max_price: Decimal

    @model_validator(mode="after")
    def _check_mode_consistency(self) -> FixedPositionConfig:
        if self.mode == "fixed_qty" and self.qty is None:
            raise ValueError("fixed_position.mode=fixed_qty 必须提供 qty")
        if self.mode == "fixed_quote" and self.quote_amount is None:
            raise ValueError("fixed_position.mode=fixed_quote 必须提供 quote_amount")
        return self


# --------------------------------------------------------------------------- #
# Settings 主体
# --------------------------------------------------------------------------- #


class Settings(BaseModel):
    """系统全局 settings；通过 `Settings.load(...)` 完成 5 层 overlay 合并。

    `_layer_map` 是非 schema 字段，记录每个叶子字段路径的实际来源层；通过
    `field_layer_map()` 暴露。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    env: Literal["dev", "paper", "prod"]
    accounts: dict[str, AccountConfig]
    capital_allocation: list[CapitalSlice]
    pyramid: list[PyramidConfig]
    fixed_position: list[FixedPositionConfig]

    # 私有 provenance 表，不参与 schema 序列化。pydantic v2 用 PrivateAttr 时
    # frozen=True 会冲突，这里通过 ClassVar + 实例字典避免（实例字典在
    # `_assemble` 中通过 object.__setattr__ 注入）。
    _LAYER_MAP_ATTR: ClassVar[str] = "__layer_map__"

    @field_validator("accounts")
    @classmethod
    def _accounts_non_empty(cls, v: dict[str, AccountConfig]) -> dict[str, AccountConfig]:
        if not v:
            raise ValueError("accounts 至少需要一个账户")
        return v

    @model_validator(mode="after")
    def _validate_pyramid_fixed_mutex(self) -> Settings:
        pyramid_keys = {(p.account, p.strategy) for p in self.pyramid if p.enabled}
        fixed_keys = {(f.account, f.strategy) for f in self.fixed_position}
        overlap = pyramid_keys & fixed_keys
        if overlap:
            details = ", ".join(f"({a},{s})" for a, s in sorted(overlap))
            raise ValueError(
                f"pyramid 与 fixed_position 在以下 (account, strategy) 上互斥冲突: {details}"
            )
        # 引用完整性：capital_allocation/pyramid/fixed_position 的 account 必须
        # 在 accounts 字典里。
        accounts_set = set(self.accounts.keys())
        for slice_ in self.capital_allocation:
            if slice_.account not in accounts_set:
                raise ValueError(
                    f"capital_allocation 引用了未定义的 account: {slice_.account}"
                )
        for p in self.pyramid:
            if p.account not in accounts_set:
                raise ValueError(f"pyramid 引用了未定义的 account: {p.account}")
        for f in self.fixed_position:
            if f.account not in accounts_set:
                raise ValueError(f"fixed_position 引用了未定义的 account: {f.account}")
        return self

    # ----- 公共 API ------------------------------------------------------- #

    def field_layer_map(self) -> dict[str, LayerScope]:
        """返回每个叶子字段路径 → 实际来源层（5 选一）。

        路径形如 `accounts.spot.venue`、`pyramid[0].add_trigger_pct`、
        `capital_allocation[0].max_quote_amount`。供 `copy-trader doctor` 与
        dashboard 设置中心使用。
        """
        layer_map: dict[str, LayerScope] = getattr(self, self._LAYER_MAP_ATTR, {})
        return dict(layer_map)

    # ----- 加载入口 ------------------------------------------------------- #

    @classmethod
    def load(
        cls,
        *,
        config_dir: Path,
        env: str | None = None,
        local_path: Path | None = None,
        envvars: Mapping[str, str] | None = None,
        cli_overrides: Mapping[str, Any] | None = None,
    ) -> Settings:
        """完成 5 层 overlay 合并并构造 Settings。

        参数:
            config_dir: 含 `base.yaml` 与 `<env>.yaml` 的目录。
            env: 环境名（dev / paper / prod）；缺省时读 `COPY_TRADER_ENV`。
            local_path: `$COPY_TRADER_HOME/config.yaml`；缺省时读 envvar
                `COPY_TRADER_HOME`，再 fallback 到不加载 local 层。
            envvars: 用于注入 envvar 层（默认 `os.environ`）；显式传入便于测试。
            cli_overrides: 嵌套 dict 形式的 CLI flag 覆盖（最高优先级）。
        """
        envvars = dict(envvars if envvars is not None else os.environ)
        env_name = env or envvars.get(f"{ENVVAR_PREFIX}ENV") or "dev"
        if env_name not in ("dev", "paper", "prod"):
            raise ValueError(f"未知 env: {env_name!r}（仅支持 dev/paper/prod）")

        layered: list[tuple[LayerScope, dict[str, Any]]] = []

        # 1) base.yaml
        base_path = config_dir / "base.yaml"
        layered.append(("base", _load_yaml_with_secret_guard(base_path, layer="base")))

        # 2) <env>.yaml
        env_path = config_dir / f"{env_name}.yaml"
        layered.append(("env", _load_yaml_with_secret_guard(env_path, layer="env")))

        # 3) local.yaml
        if local_path is None:
            home = envvars.get(f"{ENVVAR_PREFIX}HOME")
            if home:
                candidate = Path(home).expanduser() / "config.yaml"
                if candidate.is_file():
                    local_path = candidate
        if local_path is not None and local_path.is_file():
            layered.append(
                ("local", _load_yaml_with_secret_guard(local_path, layer="local"))
            )
        else:
            layered.append(("local", {}))

        # 4) envvar overlay
        layered.append(("envvar", _envvars_to_nested(envvars, prefix=ENVVAR_PREFIX)))

        # 5) cli overlay
        layered.append(("cli", dict(cli_overrides or {})))

        merged, layer_map = _merge_layers(layered)
        # env 字段总是从 envvar / cli 推断，注入到 merged 让 schema 校验通过
        merged.setdefault("env", env_name)
        if "env" not in layer_map:
            # 来自 envvar 或 caller 显式 env=...；若 caller 显式提供 env 参数
            # 而 envvar 无 COPY_TRADER_ENV，把它登记为 cli 层。
            layer_map["env"] = "envvar" if envvars.get(f"{ENVVAR_PREFIX}ENV") else "cli"

        try:
            instance = cls.model_validate(merged)
        except ValidationError:
            raise
        # frozen=True，只能用 object.__setattr__ 注入私有 layer_map
        object.__setattr__(instance, cls._LAYER_MAP_ATTR, layer_map)
        return instance


# --------------------------------------------------------------------------- #
# 内部工具：yaml 加载 + 敏感字段守卫
# --------------------------------------------------------------------------- #


def _load_yaml_with_secret_guard(path: Path, *, layer: str) -> dict[str, Any]:
    """读取 yaml 并扫描敏感字段名。

    `path` 不存在 → 返回空 dict（不存在等价于该层无覆盖）。
    """
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} 顶层必须是 mapping，实际得到 {type(raw).__name__}")
    offenders = list(_iter_sensitive_keys(raw))
    if offenders:
        joined = ", ".join(offenders)
        raise ValidationError.from_exception_data(
            title="SettingsSecretGuard",
            line_errors=[
                {
                    "type": "value_error",
                    "loc": ("config", layer, *offenders[0].split(".")),
                    "input": str(path),
                    "ctx": {
                        "error": ValueError(
                            f"{path} 中出现敏感字段命名 ({joined})，"
                            "禁止从 yaml 读取；请通过 COPY_TRADER_* 环境变量注入"
                        )
                    },
                }
            ],
        )
    return raw


def _iter_sensitive_keys(node: Any, prefix: str = "") -> Iterable[str]:
    """深度优先扫描 mapping，yield 命中敏感后缀的 dotted path。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if not isinstance(k, str):
                continue
            current = f"{prefix}.{k}" if prefix else k
            if any(k.lower().endswith(s) for s in _SENSITIVE_SUFFIXES):
                yield current
            yield from _iter_sensitive_keys(v, current)
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            yield from _iter_sensitive_keys(item, f"{prefix}[{idx}]")


# --------------------------------------------------------------------------- #
# 内部工具：envvar → nested dict
# --------------------------------------------------------------------------- #


def _envvars_to_nested(envvars: Mapping[str, str], *, prefix: str) -> dict[str, Any]:
    """把 `COPY_TRADER_FOO__BAR=val` 翻译为 `{"foo": {"bar": "val"}}`。

    嵌套用 `__`（双下划线）分隔，单 `_` 视作字段名内部下划线。
    """
    out: dict[str, Any] = {}
    for key, val in envvars.items():
        if not key.startswith(prefix) or key == f"{prefix}HOME" or key == f"{prefix}ENV":
            continue
        path = key[len(prefix) :].lower().split("__")
        cursor: dict[str, Any] = out
        for part in path[:-1]:
            nxt = cursor.setdefault(part, {})
            if not isinstance(nxt, dict):
                # 冲突：不同 envvar 在同一前缀产生 leaf vs branch；保守抛错
                raise ValueError(f"envvar 嵌套冲突：{key}")
            cursor = nxt
        cursor[path[-1]] = val
    return out


# --------------------------------------------------------------------------- #
# 内部工具：5 层合并 + provenance 跟踪
# --------------------------------------------------------------------------- #


def _merge_layers(
    layered: list[tuple[LayerScope, dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, LayerScope]]:
    """按 LAYER_ORDER 顺序深合并 dict，记录每个叶子路径的最终来源层。

    list 不深合并：上层 list 整体替换下层 list；list 内每个元素的 provenance
    标为该 list 出现的顶层来源。dict 递归合并。
    """
    merged: dict[str, Any] = {}
    layer_map: dict[str, LayerScope] = {}
    for layer, payload in layered:
        if not payload:
            continue
        _deep_merge(merged, payload, layer, "", layer_map)
    return merged, layer_map


def _deep_merge(
    dst: dict[str, Any],
    src: Mapping[str, Any],
    layer: LayerScope,
    prefix: str,
    layer_map: dict[str, LayerScope],
) -> None:
    for k, v in src.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, Mapping) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v, layer, path, layer_map)
        elif isinstance(v, Mapping):
            dst[k] = {}
            _deep_merge(dst[k], v, layer, path, layer_map)
        elif isinstance(v, list):
            dst[k] = list(v)
            layer_map[path] = layer
            for idx, item in enumerate(v):
                _record_list_item_layer(item, layer, f"{path}[{idx}]", layer_map)
        else:
            dst[k] = v
            layer_map[path] = layer


def _record_list_item_layer(
    item: Any, layer: LayerScope, prefix: str, layer_map: dict[str, LayerScope]
) -> None:
    if isinstance(item, Mapping):
        for k, v in item.items():
            sub = f"{prefix}.{k}"
            if isinstance(v, Mapping):
                _record_list_item_layer(v, layer, sub, layer_map)
            elif isinstance(v, list):
                layer_map[sub] = layer
                for idx, sub_item in enumerate(v):
                    _record_list_item_layer(sub_item, layer, f"{sub}[{idx}]", layer_map)
            else:
                layer_map[sub] = layer
    else:
        layer_map[prefix] = layer
