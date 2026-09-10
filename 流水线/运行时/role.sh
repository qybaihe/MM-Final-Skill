#!/bin/bash
# 蜂巢角色腿（本地）：role.sh <角色文件> <任务文件> <日志名>   引擎见 LEG_ENGINE（codex 默认 / claude）
# pid 文件是驱动做活性检查（kill -0）与整组回收（kill -- -pgid）的依据——
# 本脚本由 本地蜂巢.py 以 start_new_session 拉起，pid 即 pgid。
set -u
cd "$(dirname "$0")/.." || exit 1      # bin/ → 工作根
mkdir -p 日志 任务
ROLE_FILE="$1"; TASK_FILE="$2"; NAME="$3"
echo $$ > "日志/${NAME}.pid"
# 腿引擎：LEG_ENGINE=codex（默认，`codex exec` + seatbelt）| claude（`claude -p`，Claude Code 无头子代理）。
# 两条路的产物契约完全一样：腿在工作根里写文件、完成写 日志/<腿名>.done；驱动只认 done 与产物，不认引擎。
ENGINE="${LEG_ENGINE:-codex}"
source "$(dirname "$0")/codex公共.sh"          # with_timeout 两个引擎共用
[ "$ENGINE" = "claude" ] && source "$(dirname "$0")/claude公共.sh"
# 上限由驱动按波次下发（LEG_TIMEOUT，本地蜂巢.leg(leg_timeout=)），默认 1200；
# M5-1 实测：全文审稿腿在新锚点下 20 分钟跑不完，被 SIGALRM 杀掉后 done 只剩 AUTO(rc=142)。
if [ "$ENGINE" = "claude" ]; then
  SYS="$(claude_system_file "$ROLE_FILE" "$NAME")"
  claude_run "${LEG_TIMEOUT:-1200}" "$SYS" "=== 本次任务 ===
$(cat "$TASK_FILE")" > "日志/${NAME}.log" 2>&1
  RC=$?
else
PROMPT="$(cat "$ROLE_FILE")

=== 本次任务 ===
$(cat "$TASK_FILE")"
with_timeout "${LEG_TIMEOUT:-1200}" "$CODEX_BIN" exec "${CODEX_BASE_FLAGS[@]}" \
  -c model_reasoning_effort="${LEG_EFFORT:-${LEG_EFFORT_DEFAULT:-${CODEX_EFFORT:-high}}}" "$PROMPT" > "日志/${NAME}.log" 2>&1   # LEG_EFFORT=驱动按角色分档下发（20260910）> 全局值（历史名 CODEX_EFFORT，20260909 对照跑 xhigh）> high
RC=$?
fi
[ -f "日志/${NAME}.done" ] || echo "AUTO(rc=$RC) 角色未写done标记" > "日志/${NAME}.done"
echo "rc=$RC"
tail -c 500 "日志/${NAME}.log"
