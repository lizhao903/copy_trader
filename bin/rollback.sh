#!/usr/bin/env bash
# bin/rollback.sh — 回滚到上一部署 tag + 重启 systemd（issue #30）
#
# spec: openspec/specs/delivery-roadmap/spec.md
#
# 用法:
#   sudo bin/rollback.sh                       # 回到 .last_deployed_tag 记的上一个
#   sudo bin/rollback.sh bootstrap/m1-20260420  # 显式指定回滚 tag
#
# 流程:
#   1. 拿到上一个 tag (从 LAST_TAG_FILE 或参数)
#   2. checkout 那个 tag
#   3. uv sync --frozen
#   4. systemd daemon-reload + restart 所有实例
#   5. 写 alert log + journalctl 提示

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LAST_TAG_FILE="/var/lib/copy_trader/.last_deployed_tag"
ROLLBACK_LOG="/var/log/copy_trader/rollback_$(date -u +%Y%m%dT%H%M%SZ).log"
mkdir -p "$(dirname "$ROLLBACK_LOG")"

log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$ROLLBACK_LOG"
}

# 取目标 tag
if [[ $# -ge 1 ]]; then
    TARGET_TAG="$1"
    log "显式指定回滚 tag: $TARGET_TAG"
else
    if [[ ! -f "$LAST_TAG_FILE" ]]; then
        echo "FATAL: $LAST_TAG_FILE 不存在,无法判断回滚目标" >&2
        echo "       手工指定: sudo bin/rollback.sh bootstrap/m<N>-<YYYYMMDD>" >&2
        exit 1
    fi
    TARGET_TAG="$(cat "$LAST_TAG_FILE")"
    log "从 $LAST_TAG_FILE 读出回滚 tag: $TARGET_TAG"
fi

if [[ -z "$TARGET_TAG" ]]; then
    log "FATAL: tag 为空"
    exit 1
fi

log "=== Rollback start: $TARGET_TAG ==="

# 1. fetch + checkout
log "git fetch origin --tags"
git fetch origin --tags >>"$ROLLBACK_LOG" 2>&1

if ! git rev-parse "$TARGET_TAG" >/dev/null 2>&1; then
    log "FATAL: tag $TARGET_TAG 不存在"
    exit 1
fi

git checkout "$TARGET_TAG" >>"$ROLLBACK_LOG" 2>&1

# 2. uv sync
log "uv sync --frozen"
uv sync --frozen >>"$ROLLBACK_LOG" 2>&1

# 3. systemd reload + restart
log "systemd daemon-reload"
sudo systemctl daemon-reload >>"$ROLLBACK_LOG" 2>&1

INSTANCES="$(systemctl list-units --type=service --state=loaded --no-legend \
    | awk '/^copy-trader@/{print $1}' || true)"

if [[ -z "$INSTANCES" ]]; then
    log "(没有已启用的 copy-trader@*.service 实例)"
else
    log "重启实例: $INSTANCES"
    for unit in $INSTANCES; do
        sudo systemctl restart "$unit" >>"$ROLLBACK_LOG" 2>&1 \
            || log "WARN: 重启 $unit 失败,继续其他"
    done
fi

# 4. 验证回滚后状态
log "回滚后 doctor 校验"
if ! COPY_TRADER_ENV=prod uv run copy-trader doctor >>"$ROLLBACK_LOG" 2>&1; then
    log "FATAL: 回滚后 doctor 仍失败,人工介入排查"
    exit 1
fi

# 5. 更新 LAST_TAG_FILE 反映当前回滚后的 tag
echo "$TARGET_TAG" > "$LAST_TAG_FILE"
log "=== Rollback to $TARGET_TAG 完成 ==="
log "rollback log: $ROLLBACK_LOG"
log "下一步: journalctl -u copy-trader@* -f 看主循环恢复;参考 docs/POSTMORTEM_TEMPLATE.md 写 postmortem"
