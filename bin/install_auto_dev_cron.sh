#!/usr/bin/env bash
# bin/install_auto_dev_cron.sh — install/uninstall/manage cron for auto_dev_loop
#
# spec: openspec/specs/autonomous-driver/spec.md (Requirement: Cron schedule is night-only)
#
# 子命令：
#   install       装夜间 cron（macOS launchd 或 Linux crontab）
#   uninstall     卸载
#   status        查看 cron 状态 + 最近 5 次唤醒结果 + stuck issues + pause 锁
#   pause         写 var/dev/state/.driver_pause 锁（驱动下次唤醒立即退出）
#   resume        删 pause 锁
#   dry-run       手动跑一次驱动（DRY_RUN=1，开 PR 后自己 close 不合并）

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$REPO_ROOT/var/dev/state"
PAUSE_FILE="$STATE_DIR/.driver_pause"
LOG_DIR="$REPO_ROOT/logs"
CRON_LOG="$LOG_DIR/auto_dev_cron.log"
DRIVER="$REPO_ROOT/bin/auto_dev_loop.sh"

PLIST_LABEL="io.copy_trader.auto_dev_loop"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
CRON_TAG="# auto_dev_loop (managed by install_auto_dev_cron.sh — DO NOT EDIT)"

mkdir -p "$STATE_DIR" "$LOG_DIR"

usage() {
  cat <<EOF
Usage: $0 {install|uninstall|status|pause|resume|dry-run}

  install     装 cron / launchd（4 次/晚：20:05、23:05、02:05、05:05 UTC-LOCAL）
  uninstall   卸载 cron / launchd
  status      显示当前调度状态、暂停锁、最近 5 次唤醒、stuck issues
  pause       写 pause 锁；驱动下次唤醒立即退出
  resume      删除 pause 锁
  dry-run     手动跑一次驱动（DRY_RUN=1）；开 PR 后立即 close 不合并

EOF
}

is_macos() { [[ "$(uname)" == "Darwin" ]]; }

install_macos() {
  mkdir -p "$(dirname "$PLIST_PATH")"
  cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$PLIST_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>cd "$REPO_ROOT" &amp;&amp; "$DRIVER" &gt;&gt; "$CRON_LOG" 2&gt;&amp;1</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>20</integer><key>Minute</key><integer>5</integer></dict>
    <dict><key>Hour</key><integer>23</integer><key>Minute</key><integer>5</integer></dict>
    <dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>5</integer></dict>
    <dict><key>Hour</key><integer>5</integer><key>Minute</key><integer>5</integer></dict>
  </array>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/launchd_stdout.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/launchd_stderr.log</string>
</dict>
</plist>
EOF
  launchctl unload "$PLIST_PATH" 2>/dev/null || true
  launchctl load "$PLIST_PATH"
  echo "✓ launchd installed: $PLIST_PATH"
  echo "  schedule: 20:05 / 23:05 / 02:05 / 05:05 (local time)"
  echo "  driver log: $CRON_LOG"
  echo "  暂停: $0 pause"
}

uninstall_macos() {
  if [[ -f "$PLIST_PATH" ]]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "✓ launchd uninstalled: $PLIST_PATH"
  else
    echo "(no launchd plist at $PLIST_PATH)"
  fi
}

install_linux() {
  local existing
  existing=$(crontab -l 2>/dev/null || true)
  if echo "$existing" | grep -qF "$CRON_TAG"; then
    echo "(cron 已存在，先 uninstall 再 install)"
    return 1
  fi
  local new_cron
  new_cron=$(printf "%s\n%s\n5 20,23,2,5 * * * cd %s && %s >> %s 2>&1\n" \
    "$existing" "$CRON_TAG" "$REPO_ROOT" "$DRIVER" "$CRON_LOG")
  echo "$new_cron" | crontab -
  echo "✓ crontab installed"
  echo "  schedule: 5 20,23,2,5 * * *"
  echo "  driver log: $CRON_LOG"
}

uninstall_linux() {
  local existing
  existing=$(crontab -l 2>/dev/null || true)
  if ! echo "$existing" | grep -qF "$CRON_TAG"; then
    echo "(no managed cron entry)"
    return 0
  fi
  echo "$existing" | grep -vF "$CRON_TAG" | grep -v 'auto_dev_loop\.sh' | crontab -
  echo "✓ crontab uninstalled"
}

cmd_install() {
  if is_macos; then install_macos; else install_linux; fi
}
cmd_uninstall() {
  if is_macos; then uninstall_macos; else uninstall_linux; fi
}

cmd_status() {
  echo "=== auto_dev_loop status ==="
  echo
  if is_macos; then
    if [[ -f "$PLIST_PATH" ]]; then
      echo "launchd: ✓ installed ($PLIST_PATH)"
      launchctl list | grep "$PLIST_LABEL" || echo "  (not loaded)"
    else
      echo "launchd: ✗ not installed"
    fi
  else
    if crontab -l 2>/dev/null | grep -qF "$CRON_TAG"; then
      echo "crontab: ✓ installed"
      crontab -l 2>/dev/null | grep -A0 'auto_dev_loop\.sh' || true
    else
      echo "crontab: ✗ not installed"
    fi
  fi
  echo
  if [[ -f "$PAUSE_FILE" ]]; then
    echo "pause: ⏸ ACTIVE — driver 将拒绝消费 issue"
    echo "  reason: $(cat "$PAUSE_FILE" 2>/dev/null || echo unknown)"
    echo "  resume: $0 resume"
  else
    echo "pause: ▶ inactive"
  fi
  echo
  echo "--- recent 5 wake-ups (auto_dev_cron.log) ---"
  if [[ -f "$CRON_LOG" ]]; then
    tail -20 "$CRON_LOG" | tail -5 || echo "(no entries)"
  else
    echo "(log not yet created)"
  fi
  echo
  echo "--- driver state (var/dev/state/driver_state.json) ---"
  if [[ -f "$STATE_DIR/driver_state.json" ]]; then
    jq '{schema_version, last_wake_ts, current_issue, current_branch, consecutive_failures, halt_reason, completed: (.completed_issues | length), stuck: .stuck_counter}' \
      "$STATE_DIR/driver_state.json"
  else
    echo "(no state file yet)"
  fi
  echo
  echo "--- stuck issues on GitHub ---"
  gh issue list --repo "${GH_REPO:-lizhao903/copy_trader}" --state open --label stuck --json number,title 2>/dev/null \
    | jq -r '.[] | "  #\(.number): \(.title)"' || echo "(failed to query gh)"
}

cmd_pause() {
  echo "${1:-manual pause via install_auto_dev_cron.sh}" > "$PAUSE_FILE"
  echo "✓ paused (lock file written: $PAUSE_FILE)"
  echo "  resume with: $0 resume"
}
cmd_resume() {
  if [[ -f "$PAUSE_FILE" ]]; then
    rm "$PAUSE_FILE"
    echo "✓ resumed (lock file removed)"
  else
    echo "(no pause lock to remove)"
  fi
}

cmd_dry_run() {
  echo "=== dry-run: DRY_RUN=1 bin/auto_dev_loop.sh ==="
  echo "(开 PR 后立即 close 不合并；issue 不动)"
  DRY_RUN=1 "$DRIVER"
}

case "${1:-}" in
  install)   cmd_install;;
  uninstall) cmd_uninstall;;
  status)    cmd_status;;
  pause)     shift; cmd_pause "${1:-}";;
  resume)    cmd_resume;;
  dry-run)   cmd_dry_run;;
  ""|-h|--help) usage;;
  *) echo "unknown subcommand: $1"; usage; exit 1;;
esac
