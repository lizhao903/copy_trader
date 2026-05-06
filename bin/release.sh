#!/usr/bin/env bash
# bin/release.sh — dev 机打 git tag + 推 origin（issue #30）
#
# spec: openspec/specs/delivery-roadmap/spec.md
#
# 用法:
#   bin/release.sh m2                    # 打 bootstrap/m2-<today> tag 并 push
#   bin/release.sh m2 2026-05-06         # 自定义日期
#
# tag 命名约定: bootstrap/m<N>-<YYYYMMDD> (CONTRIBUTING.md tag 命名约定章节)
#
# 安全:
# - 必须在 main 分支上跑（不接受 detached HEAD / feature 分支）
# - working tree 必须干净
# - origin/main 与本地 main 必须一致（否则提示 pull）

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ $# -lt 1 ]]; then
    echo "用法: bin/release.sh m<N> [YYYY-MM-DD]" >&2
    exit 64
fi

MILESTONE="$1"
DATE="${2:-$(date -u +%Y%m%d)}"
DATE="${DATE//-/}"  # 去掉破折号兼容 2026-05-06 / 20260506 两种写法

# 校验 milestone 名
if [[ ! "$MILESTONE" =~ ^m[0-5]$ ]]; then
    echo "milestone 必须是 m0..m5（收到: $MILESTONE）" >&2
    exit 64
fi

TAG="bootstrap/${MILESTONE}-${DATE}"

# 校验当前分支
CURRENT_BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || echo "<detached>")"
if [[ "$CURRENT_BRANCH" != "main" ]]; then
    echo "必须在 main 分支上跑 release（当前: $CURRENT_BRANCH）" >&2
    exit 65
fi

# 校验 working tree 干净
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "working tree 不干净，先 commit / stash" >&2
    git status --short
    exit 66
fi

# 校验 main 与远端一致
git fetch origin main >/dev/null
LOCAL_HEAD="$(git rev-parse main)"
REMOTE_HEAD="$(git rev-parse origin/main)"
if [[ "$LOCAL_HEAD" != "$REMOTE_HEAD" ]]; then
    echo "本地 main ($LOCAL_HEAD) 与 origin/main ($REMOTE_HEAD) 不一致" >&2
    echo "先 git pull --ff-only origin main" >&2
    exit 67
fi

# 校验 tag 不存在
if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "tag $TAG 已存在；同一 milestone 多次发布请用不同日期" >&2
    exit 68
fi

# 显示发布范围
LAST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || echo "<no-prev-tag>")"
echo "=== Release $TAG ==="
echo "main HEAD: $LOCAL_HEAD"
echo "上一 tag : $LAST_TAG"
if [[ "$LAST_TAG" != "<no-prev-tag>" ]]; then
    echo "----- commits since $LAST_TAG -----"
    git log --oneline "$LAST_TAG"..HEAD | head -30
fi
echo "================================"

# 创建 annotated tag
git tag -a "$TAG" -m "Release $TAG (auto by bin/release.sh)" "$LOCAL_HEAD"
echo "创建 tag: $TAG"

# Push tag
git push origin "$TAG"
echo "已推送 origin"

echo "=== Release $TAG 完成 ==="
echo "下一步: 在生产机跑 'sudo bin/deploy.sh $TAG'"
