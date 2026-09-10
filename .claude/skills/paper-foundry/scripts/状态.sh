#!/bin/bash
# 一屏看清一炉：驱动/阶段/腿数/门史/活腿/异常计数/最近日志。监督时每 15–30 分钟跑一次。
source "$(dirname "$0")/公共.sh"
[ $# -lt 1 ] && { echo "用法: 状态.sh <产出目录> [--日志=路径] [--行=12]"; exit 1; }
OUT_ARG="$1"; shift; NLINES=12
for a in "$@"; do case "$a" in --日志=*) LOG_OVERRIDE="${a#--日志=}";; --行=*) NLINES="${a#--行=}";; esac; done
炉 "$OUT_ARG"
p="$(驱动pid)"
if 驱动活着; then echo "驱动：pid=$p 在跑 $(ps -o etime= -p "$p" | tr -d ' ')（日志 ${LOG}）"
else echo "驱动：不在跑（pid 文件 ${p:-无}）$( [ -n "$(所有驱动)" ] && echo "—— 但另有驱动进程：$(所有驱动 | tr '\n' ' ')" )"; fi
if [ -f "$STATE" ]; then
python3 - "$STATE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
机时 = d.get("累计机时", 0) / 3600
print(f"状态：运行ID={d.get('运行ID')} 引擎={d.get('引擎', 'codex')} 阶段={d.get('阶段')} 腿数={d.get('腿数')} 已完成节点={len(d.get('已完成节点', []))} 累计机时={机时:.1f}h"
      + (f" S5已完成轮={d['S5已完成轮']}" if d.get("S5已完成轮") else ""))
print(f"门：问题门={d.get('问题门状态')} 失败计数={d.get('门失败计数')} 降级放行={[x.get('门') for x in d.get('降级放行', [])]}")
PY
else echo "状态：$STATE 不存在（未开跑或不是这炉的父目录）"; fi
LIVE="$(活腿列表)"
echo "活腿（$(printf '%s\n' "$LIVE" | grep -c . )）：$(printf '%s\n' "$LIVE" | awk '{printf "%s[%s] ", $1, $3}')"
if [ -f "$LOG" ]; then
  SEG="$(当前段)"
  echo "本段计数：429=$(printf '%s' "$SEG" | grep -c '\b429\b') rc=142=$(printf '%s' "$SEG" | grep -c 'rc=142') 孤儿=$(printf '%s' "$SEG" | grep -c '孤儿') Traceback=$(printf '%s' "$SEG" | grep -c Traceback) !!=$(printf '%s' "$SEG" | grep -c '!!') 死腿重派=$(printf '%s' "$SEG" | grep -c '死亡\|重派') 门FAIL=$(printf '%s' "$SEG" | grep -c '门\[.*FAIL') 降级=$(printf '%s' "$SEG" | grep -c '降级放行') 升格=$(printf '%s' "$SEG" | grep -c '升格蜂群\[')"
  echo "最近 $NLINES 行："; grep -E "^- " "$LOG" | tail -"$NLINES" | cut -c1-170
  grep -E "终态|驾驶结束|预算护栏|异常终止" "$LOG" | tail -2 | sed 's/^/终态线：/' | cut -c1-170
else echo "日志 $LOG 不存在"; fi
