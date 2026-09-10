#!/bin/bash
# 蜂巢看图腿（本地）：图片腿.sh <角色文件> <任务文件> <日志名> <超时秒> <推理0|1> [图片...]
# 由 本地蜂巢.leg_img 拉起；pid 语义同 role.sh。
set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p 日志 任务
ROLE_FILE="$1"; TASK_FILE="$2"; NAME="$3"; TMO="$4"; REASON="$5"; shift 5
echo $$ > "日志/${NAME}.pid"
ENGINE="${LEG_ENGINE:-codex}"
source "$(dirname "$0")/codex公共.sh"
[ "$ENGINE" = "claude" ] && source "$(dirname "$0")/claude公共.sh"
if [ "$ENGINE" = "claude" ]; then
  # Claude 腿没有 -i 附图：把图路径列进任务，腿用 Read 工具逐张打开（Read 对 PNG/JPG 会以图像呈现）。
  LIST=""; for im in "$@"; do LIST="$LIST- $im
"; done
  SYS="$(claude_system_file "$ROLE_FILE" "$NAME")"
  [ -z "${LEG_EFFORT:-}" ] && [ "$REASON" != "1" ] && export LEG_EFFORT="medium"   # 非推理看图腿沿用低档口径
  claude_run "$TMO" "$SYS" "=== 本次任务 ===
先用 Read 工具逐张打开下列附图（共 $# 张），看完再做任务：
$LIST
$(cat "$TASK_FILE")" > "日志/${NAME}.log" 2>&1
  RC=$?
else
PROMPT="$(cat "$ROLE_FILE")

=== 本次任务 ===
$(cat "$TASK_FILE")"
IMG_ARGS=()
for im in "$@"; do IMG_ARGS+=(-i "$im"); done
EFF=()
if [ -n "${LEG_EFFORT:-}" ]; then EFF=(-c model_reasoning_effort="$LEG_EFFORT")     # 驱动按角色分档下发（20260910）
elif [ "$REASON" = "1" ]; then EFF=(-c model_reasoning_effort="high"); fi          # 否则沿用旧口径：推理腿 high，非推理腿用 config.toml 默认
with_timeout "$TMO" "$CODEX_BIN" exec "${CODEX_BASE_FLAGS[@]}" "${EFF[@]}" \
  "${IMG_ARGS[@]}" -- "$PROMPT" > "日志/${NAME}.log" 2>&1
RC=$?
fi
[ -f "日志/${NAME}.done" ] || echo "AUTO(rc=$RC) 角色未写done标记" > "日志/${NAME}.done"
echo "rc=$RC"
