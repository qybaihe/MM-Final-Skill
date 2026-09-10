#!/bin/bash
# 切换：等日志出现 --标记 → 干净停跑 → 跑钩子（改台账/状态等，必须在停净之后）→ 验证 → --resume → 核验新驱动。
# 用于「链路修好了，等当前轮落盘再上线」。本脚本第一件事是把自己复制到临时目录再执行——
# bash 按字节偏移读脚本，正在运行的脚本被原地改写会跑飞（双驱动事故的根因），复制后源文件随便改。
if [ -z "${_SWITCH_COPY:-}" ]; then
  _t="$(mktemp -d "${TMPDIR:-/tmp}/qiehuan.XXXXXX")"; _d="$(cd "$(dirname "$0")" && pwd -P)"
  for f in 切换.sh 公共.sh 停跑.sh 启动.sh 验证.sh; do cp "$_d/$f" "$_t/$f"; done
  _SWITCH_COPY=1 _SWITCH_SRC="$_d" exec bash "$_t/切换.sh" "$@"
fi
export SHUMO_PROJ="${SHUMO_PROJ:-$(cd "$_SWITCH_SRC/../../../.." && pwd -P)}"
source "$(dirname "$0")/公共.sh"
用法() { cat <<'U'
用法: 切换.sh <输入目录> <产出目录> --标记 '正则' [--钩子 文件.py|.sh] [--干跑=场景,..] [--不验证] [--最长 秒]
                [--档位=..] [--并发=..] [--effort=..] [--引擎=codex|claude] [--日志=路径]
  --标记   在当前段日志里等这个正则（如 'checkpoint\[S5-轮2修订后\]'）；已出现则立即切换
  --钩子   停净后、续跑前在项目根执行（.py 用 python3，.sh 用 bash）；失败则不续跑
  --干跑   验证时附带的干跑场景（全链,中断续跑,级联,门升格,韧性,S5续跑）
  --最长   最多等多少秒（默认 6h）；驱动先退出则放弃切换（返回 2）
U
}
[ $# -lt 3 ] && { 用法; exit 1; }
INPUT="$1"; OUT_ARG="$2"; shift 2; MARK=""; HOOK=""; DRY=""; VERIFY=1; MAXWAIT=21600; PASS=()
while [ $# -gt 0 ]; do case "$1" in
  --标记) MARK="$2"; shift;; --钩子) HOOK="$2"; shift;; --干跑=*) DRY="${1#--干跑=}";; --不验证) VERIFY=0;; --最长) MAXWAIT="$2"; shift;;
  --日志=*) LOG_OVERRIDE="${1#--日志=}"; PASS+=("$1");; --档位=*|--并发=*|--effort=*|--引擎=*) PASS+=("$1");; -h|--help) 用法; exit 0;;
  *) echo "!! 未知参数 $1"; 用法; exit 1;; esac; shift; done
[ -n "$MARK" ] || { echo "!! 需要 --标记"; exit 1; }
[ -z "$HOOK" ] || [ -f "$HOOK" ] || { echo "!! 钩子不存在：$HOOK"; exit 1; }
炉 "$OUT_ARG"
LOCK="$PARENT/.切换.lock"
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then echo "!! 已有切换在等（pid $(cat "$LOCK")），不重复挂"; exit 1; fi
echo $$ > "$LOCK"; trap 'rm -f "$LOCK"' EXIT
echo "[$(时刻)] 切换挂起：等 /$MARK/（日志 ${LOG}，最长 ${MAXWAIT}s）；本副本 $0"
t0=$(date +%s)
while ! 当前段 | grep -Eq "$MARK"; do
  驱动活着 || { echo "[$(时刻)] 驱动已退出，未见标记，放弃切换。日志尾："; tail -3 "$LOG" | cut -c1-160; exit 2; }
  [ $(( $(date +%s) - t0 )) -ge "$MAXWAIT" ] && { echo "[$(时刻)] 等标记超时 ${MAXWAIT}s，放弃"; exit 3; }
  sleep 5
done
echo "[$(时刻)] 命中：$(当前段 | grep -E "$MARK" | tail -1 | cut -c1-150)"
bash "$(dirname "$0")/停跑.sh" "$OUT_ARG" ${LOG_OVERRIDE:+--日志=$LOG_OVERRIDE} || { echo "!! 没停净，不动状态、不续跑"; exit 4; }
if [ -n "$HOOK" ]; then
  echo "[$(时刻)] 钩子：$HOOK"
  case "$HOOK" in *.py) python3 "$HOOK";; *) bash "$HOOK";; esac || { echo "!! 钩子失败，不续跑（驱动已停，人工处理后 启动.sh --resume）"; exit 5; }
fi
if [ "$VERIFY" = 1 ]; then
  bash "$(dirname "$0")/验证.sh" ${DRY:+--干跑=$DRY} || { echo "!! 验证失败，不续跑"; exit 6; }
fi
bash "$(dirname "$0")/启动.sh" "$INPUT" "$OUT_ARG" --resume "${PASS[@]}" || { echo "!! 续跑启动失败"; exit 7; }
sleep 60
if 驱动活着; then echo "[$(时刻)] 切换完成：新驱动 pid=$(驱动pid)"; 当前段 | grep -E "续跑|S5 续跑|基座同步|波次|Traceback|!!" | head -6 | cut -c1-180
else echo "!! 新驱动 60 秒内退出："; tail -12 "$LOG" | cut -c1-180; exit 8; fi
