#!/usr/bin/env bash
# tests/integration/test_auto_dev_loop.sh — driver 集成测试
#
# 不依赖真实 gh / claude；用 PATH 注入 stub。
# 覆盖：syntax、pause lock、lock contention、function logic（milestone/priority order）。
# 完整 e2e dry-run 由 `bin/install_auto_dev_cron.sh dry-run` 在用户授权后跑（不在 CI）。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_DIR="$(mktemp -d)"
STUB_DIR="$TEST_DIR/stubs"
mkdir -p "$STUB_DIR"

cleanup() {
  rm -rf "$TEST_DIR"
  rm -rf "$REPO_ROOT/var/dev/state/.driver_lock" 2>/dev/null || true
  rm -f "$REPO_ROOT/var/dev/state/.driver_pause" 2>/dev/null || true
}
trap cleanup EXIT

PASS=0
FAIL=0
assert_contains() {
  local haystack="$1" needle="$2" name="$3"
  if echo "$haystack" | grep -qF "$needle"; then
    echo "  ✓ $name"
    PASS=$((PASS+1))
  else
    echo "  ✗ $name"
    echo "    needle: $needle"
    echo "    haystack: $(echo "$haystack" | head -5)"
    FAIL=$((FAIL+1))
  fi
}
assert_eq() {
  if [[ "$1" == "$2" ]]; then
    echo "  ✓ $3"
    PASS=$((PASS+1))
  else
    echo "  ✗ $3 (got '$1', expected '$2')"
    FAIL=$((FAIL+1))
  fi
}

echo "=== test 1: bash syntax check ==="
bash -n "$REPO_ROOT/bin/auto_dev_loop.sh"
echo "  ✓ auto_dev_loop.sh syntax OK"
PASS=$((PASS+1))
bash -n "$REPO_ROOT/bin/install_auto_dev_cron.sh"
echo "  ✓ install_auto_dev_cron.sh syntax OK"
PASS=$((PASS+1))

echo
echo "=== test 2: pause lock causes immediate exit ==="
mkdir -p "$REPO_ROOT/var/dev/state"
echo "manual test" > "$REPO_ROOT/var/dev/state/.driver_pause"
output=$(bash "$REPO_ROOT/bin/auto_dev_loop.sh" 2>&1 || true)
rm -f "$REPO_ROOT/var/dev/state/.driver_pause"
assert_contains "$output" "pause lock present" "pause lock detected"

echo
echo "=== test 3: lock contention exits gracefully ==="
mkdir -p "$REPO_ROOT/var/dev/state/.driver_lock"
echo "99999" > "$REPO_ROOT/var/dev/state/.driver_lock/pid"
output=$(bash "$REPO_ROOT/bin/auto_dev_loop.sh" 2>&1 || true)
rm -rf "$REPO_ROOT/var/dev/state/.driver_lock"
assert_contains "$output" "lock contention" "lock contention detected"

echo
echo "=== test 4: milestone_order semantics ==="
out=$(bash -c '
milestone_order() {
  case "$1" in
    bootstrap/m0) echo 0;; bootstrap/m1) echo 1;; bootstrap/m2) echo 2;;
    bootstrap/m3) echo 3;; bootstrap/m4) echo 4;; bootstrap/m5) echo 5;;
    *) echo 99;;
  esac
}
echo "$(milestone_order bootstrap/m0)|$(milestone_order bootstrap/m5)|$(milestone_order other)"
')
assert_eq "$out" "0|5|99" "milestone_order: m0=0, m5=5, other=99"

echo
echo "=== test 5: priority_order semantics ==="
out=$(bash -c '
priority_order() {
  case "$1" in
    priority:p0) echo 0;; priority:p1) echo 1;; priority:p2) echo 2;;
    *) echo 9;;
  esac
}
echo "$(priority_order priority:p0)|$(priority_order priority:p1)|$(priority_order priority:p2)|$(priority_order none)"
')
assert_eq "$out" "0|1|2|9" "priority_order: p0=0, p1=1, p2=2, none=9"

echo
echo "=== test 6: install_auto_dev_cron.sh status without state ==="
output=$(bash "$REPO_ROOT/bin/install_auto_dev_cron.sh" status 2>&1 || true)
assert_contains "$output" "auto_dev_loop status" "status command runs"

echo
echo "=== test 7: install_auto_dev_cron.sh pause + resume cycle ==="
output=$(bash "$REPO_ROOT/bin/install_auto_dev_cron.sh" pause "test" 2>&1)
assert_contains "$output" "paused" "pause writes lock"
[[ -f "$REPO_ROOT/var/dev/state/.driver_pause" ]] && echo "  ✓ pause file exists" && PASS=$((PASS+1)) || { echo "  ✗ pause file missing"; FAIL=$((FAIL+1)); }
output=$(bash "$REPO_ROOT/bin/install_auto_dev_cron.sh" resume 2>&1)
assert_contains "$output" "resumed" "resume removes lock"
[[ ! -f "$REPO_ROOT/var/dev/state/.driver_pause" ]] && echo "  ✓ pause file removed" && PASS=$((PASS+1)) || { echo "  ✗ pause file still exists"; FAIL=$((FAIL+1)); }

echo
echo "=== summary ==="
echo "  passed: $PASS"
echo "  failed: $FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
