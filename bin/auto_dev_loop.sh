#!/usr/bin/env bash
# bin/auto_dev_loop.sh — autonomous overnight driver
#
# spec: openspec/specs/autonomous-driver/spec.md
# design: openspec/changes/autonomous-bootstrap-driver/design.md (apply 阶段)
#
# 行为：cron 每次唤醒消费一个 GitHub issue。流程：选 issue → 派发 subagent →
# 安全 diff 检查 → 本地校验五件套 → 开 PR → --admin 合并 → 关 issue → 退出。
# 状态持久化在 var/dev/state/driver_state.json。详细契约见 spec。
#
# 用法：
#   bin/auto_dev_loop.sh                # 正常一次唤醒
#   DRY_RUN=1 bin/auto_dev_loop.sh      # 走全流程但开 PR 后 close 不合并
#   GH_REPO=owner/repo bin/auto_dev_loop.sh   # 覆盖目标仓
#
# 安全护栏（与 spec autonomous-driver 对齐）：
# - flock 模式锁，单进程
# - 禁区文件 diff 命中 → 中止合并
# - 凭证文件命名 → 中止
# - 连续失败 / 磁盘满 / main 红 / pause 锁 → halt

set -euo pipefail

# claude -p 子进程优先吃 ANTHROPIC_API_KEY；用户当前以 claude.ai 订阅
# 通过 keyring OAuth 鉴权，env 里残留的旧 key 会 401 阻断 subagent。
# 显式 unset 让 subagent 回落到 keyring。需要走 API key 时改回这里。
unset ANTHROPIC_API_KEY

# === Config ===
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$REPO_ROOT/var/dev/state"
LOG_DIR="$REPO_ROOT/logs"
STATE_FILE="$STATE_DIR/driver_state.json"
PAUSE_FILE="$STATE_DIR/.driver_pause"
LOCK_DIR="$STATE_DIR/.driver_lock"
PROMPT_TEMPLATE="$REPO_ROOT/bin/auto_dev_subagent_prompt.md"
PR_BODY_TEMPLATE="$REPO_ROOT/bin/auto_dev_pr_body_template.md"
GH_REPO="${GH_REPO:-lizhao903/copy_trader}"
TIMEBOX_MIN="${TIMEBOX_MIN:-45}"
DRY_RUN="${DRY_RUN:-0}"
LOCAL_CHECK_CMDS='uv run pytest -q -m "not live" && uv run lint-imports && uv run ruff check . && uv run ruff format --check . && uv run mypy src/copy_trader'
FORBIDDEN_PATHS=(
  "bin/auto_dev_loop.sh"
  "bin/install_auto_dev_cron.sh"
  "bin/auto_dev_subagent_prompt.md"
  "bin/auto_dev_pr_body_template.md"
  ".github/workflows/"
  "mockup/"
)

# === Globals ===
WAKE_TS="$(date -u +%Y%m%dT%H%M%SZ)"
WAKE_LOG="$LOG_DIR/auto_dev_${WAKE_TS}.log"
CRON_LOG="$LOG_DIR/auto_dev_cron.log"
CURRENT_PID=$$

mkdir -p "$STATE_DIR" "$LOG_DIR"

# === Logging ===
log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$WAKE_LOG" >&2
}
log_cron() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >> "$CRON_LOG"; }
die() { log "FATAL: $*"; log_cron "FATAL: $*"; exit 1; }

# === Lock (mkdir 原子操作，跨平台) ===
acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$CURRENT_PID" > "$LOCK_DIR/pid"
    trap release_lock EXIT
    log "lock acquired (pid=$CURRENT_PID)"
  else
    local prev; prev=$(cat "$LOCK_DIR/pid" 2>/dev/null || echo unknown)
    log "lock contention, prev pid=$prev still running; exiting"
    log_cron "skip: lock held by pid=$prev"
    exit 0
  fi
}
release_lock() { rm -rf "$LOCK_DIR" 2>/dev/null || true; }

# === State I/O ===
default_state() {
  jq -n '{schema_version:1, last_wake_ts:null, last_pid:null, current_issue:null, current_branch:null, stuck_counter:{}, consecutive_failures:0, completed_issues:[], halt_reason:null, next_wake_eta:null}'
}
read_state() {
  [[ -f "$STATE_FILE" ]] && cat "$STATE_FILE" || default_state
}
write_state() { echo "$1" | jq '.' > "$STATE_FILE"; }
state_get() { read_state | jq -r "$1 // empty"; }
state_set() {
  local new; new=$(read_state | jq "$1")
  write_state "$new"
}

# === Halt checks ===
check_pause_lock() {
  if [[ -f "$PAUSE_FILE" ]]; then
    log "pause lock present; exiting"
    log_cron "skip: pause lock present"
    exit 0
  fi
}
check_disk_space() {
  local pct_free
  pct_free=$(df -k "$REPO_ROOT" | awk 'NR==2 {gsub(/%/,"",$5); print 100-$5}')
  (( pct_free < 5 )) && halt "disk free < 5% ($pct_free%)"
  return 0
}
check_main_ci() {
  local conclusion
  conclusion=$(gh run list --branch main --limit 1 --json conclusion -q '.[0].conclusion' 2>/dev/null || echo "")
  [[ "$conclusion" == "failure" ]] && halt "main last GH Actions run is FAILURE"
  return 0
}
check_consecutive_failures() {
  local n; n=$(state_get '.consecutive_failures // 0')
  (( n >= 3 )) && halt "consecutive_failures = $n (>= 3)"
  return 0
}
halt() {
  local reason="$1"
  log "HALT: $reason"
  log_cron "HALT: $reason"
  state_set "(.halt_reason = \"$reason\") | (.last_wake_ts = \"$WAKE_TS\")"
  echo "$reason" > "$PAUSE_FILE"
  exit 1
}

# === Issue queue ===
milestone_order() {
  case "$1" in
    bootstrap/m0) echo 0;; bootstrap/m1) echo 1;; bootstrap/m2) echo 2;;
    bootstrap/m3) echo 3;; bootstrap/m4) echo 4;; bootstrap/m5) echo 5;;
    *) echo 99;;
  esac
}
priority_order() {
  case "$1" in
    priority:p0) echo 0;; priority:p1) echo 1;; priority:p2) echo 2;;
    *) echo 9;;
  esac
}
deps_satisfied() {
  local body="$1"
  local deps; deps=$(echo "$body" | grep -oE '依赖 issue #[0-9]+' | grep -oE '[0-9]+' || true)
  for d in $deps; do
    local s; s=$(gh issue view "$d" --repo "$GH_REPO" --json state -q .state 2>/dev/null || echo "")
    [[ "$s" != "CLOSED" ]] && return 1
  done
  return 0
}
pick_next_issue() {
  log "fetching open issues from $GH_REPO..."
  local issues_json
  issues_json=$(gh issue list --repo "$GH_REPO" --state open --limit 200 \
    --json number,title,labels,milestone,body 2>/dev/null) || die "gh issue list failed"

  local count; count=$(echo "$issues_json" | jq 'length')
  log "fetched $count open issues"

  local best_n="" best_ms_o=99 best_pr_o=9 best_n_v=999999

  while IFS= read -r entry; do
    [[ -z "$entry" ]] && continue
    local n title ms prio labels body
    n=$(echo "$entry" | jq -r '.n')
    title=$(echo "$entry" | jq -r '.t')
    ms=$(echo "$entry" | jq -r '.ms')
    prio=$(echo "$entry" | jq -r '.prio')
    labels=$(echo "$entry" | jq -r '.labels')
    body=$(echo "$entry" | jq -r '.body')

    # Filter labels
    for forbidden in stuck wip needs-human blocked; do
      if [[ "$labels" == *",$forbidden,"* || "$labels" == "$forbidden,"* || "$labels" == *",$forbidden" || "$labels" == "$forbidden" ]]; then
        continue 2
      fi
    done

    deps_satisfied "$body" || continue

    local mso pro
    mso=$(milestone_order "$ms")
    pro=$(priority_order "$prio")

    if (( mso < best_ms_o )); then
      best_ms_o=$mso; best_pr_o=$pro; best_n_v=$n; best_n="$n"
    elif (( mso == best_ms_o && pro < best_pr_o )); then
      best_pr_o=$pro; best_n_v=$n; best_n="$n"
    elif (( mso == best_ms_o && pro == best_pr_o && n < best_n_v )); then
      best_n_v=$n; best_n="$n"
    fi
  done < <(echo "$issues_json" | jq -r '.[] | {n: .number, t: .title, ms: (.milestone.title // ""), prio: ([.labels[].name] | map(select(startswith("priority:"))) | first // ""), labels: ("," + ([.labels[].name] | join(",")) + ","), body: .body} | @json')

  if [[ -z "$best_n" ]]; then
    log "queue empty (no eligible issues)"
    return 1
  fi
  log "selected issue #$best_n (ms_o=$best_ms_o pr_o=$best_pr_o)"
  echo "$best_n"
}

# === Subagent dispatch ===
build_branch_name() {
  local n="$1" title="$2" ms="$3"
  local short_ms; short_ms=$(echo "$ms" | grep -oE 'm[0-5]' || echo "mX")
  local slug
  slug=$(echo "$title" | tr -dc 'a-zA-Z0-9 -' | tr ' ' '-' | tr -s '-' | cut -c1-30 | tr '[:upper:]' '[:lower:]' | sed 's/^-//; s/-$//')
  echo "feature/${short_ms}-issue-${n}-${slug}"
}
prepare_branch() {
  local branch="$1"
  cd "$REPO_ROOT"
  git fetch origin main >/dev/null 2>&1
  git checkout main >/dev/null 2>&1
  git pull --ff-only origin main >/dev/null 2>&1
  if git show-ref --verify --quiet "refs/heads/$branch"; then
    git checkout "$branch" >/dev/null 2>&1
  else
    git checkout -b "$branch" >/dev/null 2>&1
  fi
  log "branch ready: $branch"
}
render_prompt() {
  local n="$1" title="$2" body="$3" branch="$4"
  local out body_file
  out=$(cat "$PROMPT_TEMPLATE")
  out="${out//\{\{ISSUE_NUMBER\}\}/$n}"
  out="${out//\{\{ISSUE_TITLE\}\}/$title}"
  out="${out//\{\{FEATURE_BRANCH\}\}/$branch}"
  out="${out//\{\{TIMEBOX_MIN\}\}/$TIMEBOX_MIN}"
  out="${out//\{\{LOCAL_CHECK_COMMANDS\}\}/$LOCAL_CHECK_CMDS}"
  # multi-line body via temp file（BSD awk 不接受 -v 变量值含换行）
  body_file=$(mktemp)
  printf '%s' "$body" > "$body_file"
  printf '%s\n' "$out" | awk -v body_file="$body_file" '
    /\{\{ISSUE_BODY\}\}/ {
      while ((getline line < body_file) > 0) print line
      close(body_file)
      next
    }
    { print }
  '
  rm -f "$body_file"
}
dispatch_subagent() {
  local n="$1" branch="$2" prompt_file="$3"
  local agent_log="$LOG_DIR/auto_dev_subagent_${n}_${WAKE_TS}.log"
  local status_file="$STATE_DIR/.subagent_status_${n}.json"
  rm -f "$status_file"

  log "dispatching subagent for issue #$n (timeout=${TIMEBOX_MIN}min)"

  # Append exit-reporting instruction
  local prompt_full
  prompt_full=$(cat "$prompt_file"; cat <<EOF

---
**EXIT REPORTING（强制）**：在结束当前会话前你 MUST 写入以下 JSON 状态到磁盘：

\`$status_file\`

格式：
\`\`\`json
{"verdict": "ok|timeout|needs-human|failed", "reason": "<短描述>", "commits": <int>, "files_changed": <int>}
\`\`\`

verdict 取值含义：
- \`ok\`：本地校验全过，已 commit + push，请 driver 接管开 PR
- \`timeout\`：未完成但已 commit + push WIP（driver 标 stuck +1）
- \`needs-human\`：发现需要人工介入（如 vcrpy fixture 缺失）（driver 加 needs-human label）
- \`failed\`：未完成且无法继续（driver 计入连续失败）

写完该文件**才能**结束本会话。
EOF
)

  local timeout_sec=$(( TIMEBOX_MIN * 60 + 300 ))

  set +e
  local timeout_cmd=""
  if command -v timeout >/dev/null 2>&1; then
    timeout_cmd="timeout ${timeout_sec}s"
  elif command -v gtimeout >/dev/null 2>&1; then
    timeout_cmd="gtimeout ${timeout_sec}s"
  fi

  echo "$prompt_full" | $timeout_cmd claude -p --dangerously-skip-permissions \
    --add-dir "$REPO_ROOT" --verbose --output-format stream-json \
    > "$agent_log" 2>&1
  local rc=$?
  set -e

  log "subagent exited rc=$rc, log=$agent_log"

  if [[ -f "$status_file" ]]; then
    local verdict; verdict=$(jq -r '.verdict // "failed"' "$status_file")
    log "subagent verdict: $verdict"
    case "$verdict" in
      ok) return 0;;
      timeout) return 2;;
      needs-human) return 3;;
      *) return 4;;
    esac
  else
    log "subagent did not write status file; treating as failed"
    return 4
  fi
}

# === Diff safety check ===
check_subagent_diff_safe() {
  local branch="$1"
  cd "$REPO_ROOT"
  local files
  files=$(git diff --name-only origin/main..."$branch" 2>/dev/null || true)
  [[ -z "$files" ]] && { log "ABORT: empty diff (subagent did not commit)"; return 1; }

  for forbidden in "${FORBIDDEN_PATHS[@]}"; do
    while IFS= read -r f; do
      [[ -z "$f" ]] && continue
      if [[ "$f" == "$forbidden"* || "$f" == "$forbidden" ]]; then
        log "ABORT: forbidden path in diff: $f (matched $forbidden)"
        return 1
      fi
    done <<< "$files"
  done
  if echo "$files" | grep -qE '(_API_KEY|_SECRET|_TOKEN|_PRIVATE_KEY|^\.env$|/\.env$)'; then
    log "ABORT: diff includes potential credentials file"
    return 1
  fi
  return 0
}

# === Local checks ===
run_local_checks() {
  cd "$REPO_ROOT"
  log "running local checks: $LOCAL_CHECK_CMDS"
  if bash -c "$LOCAL_CHECK_CMDS" >> "$WAKE_LOG" 2>&1; then
    log "local checks: PASS"; return 0
  else
    log "local checks: FAIL"; return 1
  fi
}

# === PR creation and merge ===
open_and_merge_pr() {
  local n="$1" branch="$2" title="$3"
  cd "$REPO_ROOT"
  git push -u origin "$branch" >/dev/null 2>&1 || die "git push failed"

  local pr_body_file="/tmp/auto_dev_pr_body_${n}.md"
  sed "s|{{ISSUE_NUMBER}}|$n|g; s|{{WAKE_TS}}|$WAKE_TS|g" "$PR_BODY_TEMPLATE" > "$pr_body_file"

  log "opening PR for issue #$n"
  local pr_url
  pr_url=$(gh pr create --repo "$GH_REPO" --base main --head "$branch" \
    --title "$title" --body-file "$pr_body_file" --label autonomous-driver) \
    || die "gh pr create failed"
  local pr_num; pr_num=$(echo "$pr_url" | grep -oE '/pull/[0-9]+' | grep -oE '[0-9]+' | tail -1)
  log "opened PR #$pr_num: $pr_url"

  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY_RUN: closing PR #$pr_num without merging"
    gh pr close "$pr_num" --repo "$GH_REPO" --delete-branch \
      --comment "dry-run: closed without merge" >/dev/null 2>&1 || true
    echo "$pr_num"
    return 0
  fi

  log "replaying local checks before merge..."
  if ! run_local_checks; then
    log "post-push local check failed; aborting merge (PR remains open)"
    gh pr edit "$pr_num" --repo "$GH_REPO" --add-label stuck >/dev/null || true
    return 1
  fi

  log "merging PR #$pr_num with --admin --squash"
  gh pr merge "$pr_num" --repo "$GH_REPO" --squash --admin --delete-branch \
    || die "gh pr merge failed"

  gh issue close "$n" --repo "$GH_REPO" \
    --comment "Resolved by #$pr_num (auto-merged by driver at $WAKE_TS)" >/dev/null \
    || log "warn: failed to close issue #$n"

  echo "$pr_num"
}

# === Post-merge sanity ===
post_merge_sanity() {
  cd "$REPO_ROOT"
  git fetch origin main >/dev/null 2>&1
  git checkout main >/dev/null 2>&1
  git pull --ff-only origin main >/dev/null 2>&1
  log "post-merge sanity: re-running local checks on main"
  run_local_checks
}

# === Main ===
main() {
  log "=== auto_dev_loop wake start (ts=$WAKE_TS dry_run=$DRY_RUN repo=$GH_REPO) ==="
  log_cron "wake start ts=$WAKE_TS dry_run=$DRY_RUN"

  acquire_lock
  check_pause_lock
  check_consecutive_failures
  check_disk_space
  check_main_ci

  state_set ".last_wake_ts = \"$WAKE_TS\" | .last_pid = $CURRENT_PID"

  local n
  if ! n=$(pick_next_issue); then
    log "queue empty; exiting"
    log_cron "queue empty"
    exit 0
  fi

  # Stuck check
  local stuck_count; stuck_count=$(read_state | jq -r ".stuck_counter[\"$n\"] // 0")
  if (( stuck_count >= 3 )); then
    log "issue #$n stuck count >= 3; marking stuck label and skipping"
    gh issue edit "$n" --repo "$GH_REPO" --add-label stuck >/dev/null || true
    gh issue comment "$n" --repo "$GH_REPO" \
      --body "Auto-driver: stuck after 3 wake attempts. Adding 'stuck' label and skipping." >/dev/null || true
    log_cron "skip: issue #$n stuck"
    exit 0
  fi

  local issue_json title body ms branch prompt_file
  issue_json=$(gh issue view "$n" --repo "$GH_REPO" --json title,body,milestone) \
    || die "gh issue view failed"
  title=$(echo "$issue_json" | jq -r .title)
  body=$(echo "$issue_json" | jq -r .body)
  ms=$(echo "$issue_json" | jq -r '.milestone.title // ""')

  branch=$(build_branch_name "$n" "$title" "$ms")
  prepare_branch "$branch"
  state_set ".current_issue = $n | .current_branch = \"$branch\""

  prompt_file="/tmp/auto_dev_prompt_${n}_${WAKE_TS}.md"
  render_prompt "$n" "$title" "$body" "$branch" > "$prompt_file"

  set +e
  dispatch_subagent "$n" "$branch" "$prompt_file"
  local rc=$?
  set -e

  case "$rc" in
    0)
      log "subagent OK; safety + PR"
      if ! check_subagent_diff_safe "$branch"; then
        state_set ".consecutive_failures += 1"
        log_cron "fail: issue #$n forbidden diff"
        exit 1
      fi
      if open_and_merge_pr "$n" "$branch" "$title" >/dev/null; then
        if [[ "$DRY_RUN" != "1" ]]; then
          if post_merge_sanity; then
            state_set ".consecutive_failures = 0 | .completed_issues += [{issue: $n, merged_at: \"$WAKE_TS\", wake_ts: \"$WAKE_TS\"}] | .current_issue = null | .current_branch = null | del(.stuck_counter[\"$n\"])"
            log_cron "ok: issue #$n merged"
          else
            log "post-merge sanity FAIL; reopening issue"
            gh issue reopen "$n" --repo "$GH_REPO" \
              --comment "auto-merged but main broke; manual revert needed" >/dev/null || true
            state_set ".consecutive_failures += 1"
            log_cron "fail: issue #$n merged but main broke"
          fi
        else
          log_cron "dry-run: issue #$n flow OK"
        fi
      else
        state_set ".consecutive_failures += 1"
        log_cron "fail: issue #$n PR/merge failed"
      fi
      ;;
    2)
      log "subagent timeout; stuck counter +1"
      state_set ".stuck_counter[\"$n\"] = ((.stuck_counter[\"$n\"] // 0) + 1)"
      log_cron "stuck: issue #$n timeout"
      ;;
    3)
      log "subagent needs-human; adding label"
      gh issue edit "$n" --repo "$GH_REPO" --add-label needs-human >/dev/null || true
      gh issue comment "$n" --repo "$GH_REPO" \
        --body "Auto-driver: subagent reported needs-human." >/dev/null || true
      log_cron "skip: issue #$n needs-human"
      ;;
    *)
      log "subagent failed rc=$rc"
      state_set ".consecutive_failures += 1"
      log_cron "fail: issue #$n subagent rc=$rc"
      ;;
  esac

  log "=== wake end ==="
}

main "$@"
