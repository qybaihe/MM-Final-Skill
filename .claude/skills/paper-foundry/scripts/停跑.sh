#!/bin/bash
# 干净停跑：先驱动、再各腿组（只杀命令行确认是腿的），等到 驱动=0 codex=0 xelatex=0 才算停净。
# 停净之前不许碰 状态.json / 台账 / 镜像（驱动还在写）。macOS 无 GNU timeout/setsid，一切用 kill -- -pgid。
source "$(dirname "$0")/公共.sh"
[ $# -lt 1 ] && { echo "用法: 停跑.sh <产出目录> [--日志=路径] [--强制] [--等待=90]"; exit 1; }
OUT_ARG="$1"; shift; FORCE=0; WAIT=90
for a in "$@"; do case "$a" in --日志=*) LOG_OVERRIDE="${a#--日志=}";; --强制) FORCE=1;; --等待=*) WAIT="${a#--等待=}";; esac; done
炉 "$OUT_ARG"
p="$(驱动pid)"
if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then
  if 是驱动 "$p"; then
    echo "[$(时刻)] TERM 驱动组 -$p"; kill -TERM -- "-$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null
  else
    echo "?? $PIDF 里的 pid=$p 不是驱动（pid 被复用），不杀：$(ps -o comm= -p "$p")"
  fi
else
  echo "[$(时刻)] 驱动未在跑（pid 文件 ${PIDF}：${p:-无}）"
fi
# 其它没登记在 pid 文件里但命令行带本父目录的驱动（例如双驱动事故）
for q in $(父目录驱动); do
  [ "$q" = "$p" ] && continue
  echo "[$(时刻)] TERM 额外驱动 -${q}（$(驱动命令行 "$q")）"; kill -TERM -- "-$q" 2>/dev/null
done
sleep 2
NLEGS=0
for f in "$MIRROR"/日志/*.pid; do
  [ -f "$f" ] || continue
  q="$(tr -d ' \n' < "$f")"; [ -n "$q" ] && kill -0 "$q" 2>/dev/null || continue
  if 是腿 "$q"; then kill -TERM -- "-$q" 2>/dev/null || kill -TERM "$q" 2>/dev/null; NLEGS=$((NLEGS+1)); echo "  TERM 腿组 $(basename "$f" .pid) -$q"
  else echo "  跳过 $(basename "$f" .pid)：pid $q 已被复用（$(ps -o comm= -p "$q")）"; fi
done
for i in $(seq 1 $((WAIT/5))); do
  n=$(( $(所有驱动 | wc -l) + $(codex腿数) + $(xelatex数) ))
  [ "$n" -eq 0 ] && break; sleep 5
done
n=$(( $(所有驱动 | wc -l) + $(codex腿数) + $(xelatex数) ))
if [ "$n" -ne 0 ] && [ "$FORCE" = 1 ]; then
  echo "[$(时刻)] 残留 $n → KILL"; [ -n "$p" ] && kill -KILL -- "-$p" 2>/dev/null
  for q in $(所有驱动); do kill -KILL -- "-$q" 2>/dev/null; done
  pkill -KILL -f "codex exec" 2>/dev/null; pkill -KILL -x xelatex 2>/dev/null; sleep 3
fi
echo "[$(时刻)] 停跑结果：驱动=$(所有驱动 | wc -l | tr -d ' ') codex=$(codex腿数) xelatex=$(xelatex数)（本次 TERM 腿组 ${NLEGS}）"
n=$(( $(所有驱动 | wc -l) + $(codex腿数) + $(xelatex数) ))
[ "$n" -eq 0 ] && { echo "已停净，现在可以动 状态.json/台账/镜像；续跑：bash $SKILL_DIR/scripts/启动.sh <输入> $OUT_ARG --resume"; exit 0; }
echo "!! 未停净（残留 ${n}）。查：ps -eo pid,pgid,etime,command | grep -E 'codex exec|蜂群驾驶|xelatex' ；确认后可加 --强制"; exit 2
