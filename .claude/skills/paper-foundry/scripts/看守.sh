#!/bin/bash
# 看守：阻塞式盯日志，只打印新出现的监督事件行；见到 --到 正则 / 驱动退出 / 终态 / 超时 就返回。
# Codex 没有后台监视工具，就靠它分段盯（每段 ≤ 命令超时，例如 --秒 540）；Claude Code 也可用 Bash run_in_background 跑它。
# 退出码：0 命中 --到；2 驱动不在跑；3 超时；4 见到 终态/驾驶结束（跑完了）。
source "$(dirname "$0")/公共.sh"
[ $# -lt 1 ] && { echo "用法: 看守.sh <产出目录> [--到 '正则'] [--秒 1800] [--间隔 30] [--日志=路径] [--全部]"; exit 1; }
OUT_ARG="$1"; shift; UNTIL=""; SECS=1800; INTERVAL=30; ALL=0
while [ $# -gt 0 ]; do case "$1" in
  --到) UNTIL="$2"; shift;; --秒) SECS="$2"; shift;; --间隔) INTERVAL="$2"; shift;;
  --日志=*) LOG_OVERRIDE="${1#--日志=}";; --全部) ALL=1;; esac; shift; done
炉 "$OUT_ARG"
[ -f "$LOG" ] || { echo "!! 日志不存在：$LOG"; exit 1; }
off=$(wc -c < "$LOG" | tr -d ' '); t0=$(date +%s)
echo "[$(时刻)] 看守 $LOG 从偏移 $off 起，最长 ${SECS}s${UNTIL:+，直到 /$UNTIL/}"
while :; do
  sleep "$INTERVAL"
  now=$(wc -c < "$LOG" | tr -d ' ')
  if [ "$now" -gt "$off" ]; then
    NEW="$(tail -c +$((off+1)) "$LOG")"; off=$now
    if [ "$ALL" = 1 ]; then printf '%s\n' "$NEW" | cut -c1-190
    else printf '%s\n' "$NEW" | grep -E "$WATCH_RE" | cut -c1-190; fi
    printf '%s\n' "$NEW" | grep -Eq "终态：|驾驶结束" && { echo "[$(时刻)] 跑完了（终态）"; exit 4; }
    [ -n "$UNTIL" ] && printf '%s\n' "$NEW" | grep -Eq "$UNTIL" && { echo "[$(时刻)] 命中 /$UNTIL/"; exit 0; }
  fi
  驱动活着 || { echo "[$(时刻)] 驱动不在跑；日志尾："; tail -5 "$LOG" | cut -c1-170; exit 2; }
  [ $(( $(date +%s) - t0 )) -ge "$SECS" ] && { echo "[$(时刻)] 看守超时 ${SECS}s（驱动仍在跑）"; exit 3; }
done
