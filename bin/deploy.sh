#!/usr/bin/env bash
# bin/deploy.sh — 生产机部署 + smoke test + systemd reload（issue #30）
#
# spec: openspec/specs/delivery-roadmap/spec.md
#
# 用法（在生产机上以 sudo 跑）:
#   sudo bin/deploy.sh bootstrap/m2-20260506
#
# 流程:
#   1. git fetch + checkout 目标 tag
#   2. uv sync --frozen
#   3. smoke test: copy-trader doctor + 关键 pytest
#   4. systemd reload + 重启所有 copy-trader@*.service
#   5. 60s 观察期 + canary smoke
#   6. 失败 → 自动 bin/rollback.sh
#
# 失败时 LAST_TAG_FILE 内容不更新,bin/rollback.sh 用于回上一个 tag。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ $# -lt 1 ]]; then
    echo "用法: sudo bin/deploy.sh <tag>" >&2
    echo "       sudo bin/deploy.sh bootstrap/m2-20260506" >&2
    exit 64
fi

TARGET_TAG="$1"
LAST_TAG_FILE="/var/lib/copy_trader/.last_deployed_tag"
DEPLOY_LOG="/var/log/copy_trader/deploy_$(date -u +%Y%m%dT%H%M%SZ).log"

mkdir -p "$(dirname "$LAST_TAG_FILE")" "$(dirname "$DEPLOY_LOG")"

log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$DEPLOY_LOG"
}

abort_and_rollback() {
    local reason="$1"
    log "FATAL: $reason"
    log "触发自动 rollback..."
    if [[ -x "$REPO_ROOT/bin/rollback.sh" ]]; then
        bash "$REPO_ROOT/bin/rollback.sh" || log "rollback 也失败,人工介入"
    else
        log "rollback.sh 不存在,人工介入"
    fi
    exit 1
}

log "=== Deploy start: $TARGET_TAG ==="

# 1. fetch + checkout
log "git fetch origin --tags"
git fetch origin --tags >>"$DEPLOY_LOG" 2>&1 || abort_and_rollback "git fetch 失败"

if ! git rev-parse "$TARGET_TAG" >/dev/null 2>&1; then
    abort_and_rollback "tag $TARGET_TAG 在远端不存在,先在 dev 机跑 bin/release.sh"
fi

PREV_TAG="$(cat "$LAST_TAG_FILE" 2>/dev/null || echo "<none>")"
log "上一部署 tag: $PREV_TAG"

git checkout "$TARGET_TAG" >>"$DEPLOY_LOG" 2>&1 || abort_and_rollback "checkout $TARGET_TAG 失败"

# 2. uv sync
log "uv sync --frozen"
uv sync --frozen >>"$DEPLOY_LOG" 2>&1 || abort_and_rollback "uv sync 失败"

# 3. smoke test: doctor + 关键 pytest（不跑 live 标记）
log "smoke: copy-trader doctor"
COPY_TRADER_ENV=prod uv run copy-trader doctor >>"$DEPLOY_LOG" 2>&1 \
    || abort_and_rollback "doctor 跑失败"

log "smoke: pytest -m \"not live\" -q"
uv run pytest -q -m "not live" >>"$DEPLOY_LOG" 2>&1 \
    || abort_and_rollback "smoke pytest 失败"

# 4. systemd reload + restart
log "systemd daemon-reload"
sudo systemctl daemon-reload >>"$DEPLOY_LOG" 2>&1

# 列出所有 copy-trader@*.service 实例（unit pattern enabled 的）
INSTANCES="$(systemctl list-units --type=service --state=loaded --no-legend \
    | awk '/^copy-trader@/{print $1}' || true)"

if [[ -z "$INSTANCES" ]]; then
    log "(没有已启用的 copy-trader@*.service 实例;首次部署需手工 enable)"
else
    log "重启实例: $INSTANCES"
    for unit in $INSTANCES; do
        sudo systemctl restart "$unit" >>"$DEPLOY_LOG" 2>&1 \
            || abort_and_rollback "重启 $unit 失败"
    done
fi

# 5. 60s 观察期
log "60s 观察期..."
sleep 60

# canary smoke: 重新跑 doctor + 校验 systemd active
log "canary smoke: doctor 二次校验"
COPY_TRADER_ENV=prod uv run copy-trader doctor >>"$DEPLOY_LOG" 2>&1 \
    || abort_and_rollback "60s 后 doctor 失败"

if [[ -n "$INSTANCES" ]]; then
    for unit in $INSTANCES; do
        if ! systemctl is-active --quiet "$unit"; then
            abort_and_rollback "$unit 60s 后非 active"
        fi
    done
fi

# 6. 写入 last deployed tag
echo "$TARGET_TAG" > "$LAST_TAG_FILE"
log "=== Deploy $TARGET_TAG 完成 ==="
log "上一部署 tag (rollback 用): $PREV_TAG"
log "本次 tag: $TARGET_TAG"
log "deploy log: $DEPLOY_LOG"
