#!/bin/bash
# 启动或续跑一炉。核心保护：同一父目录绝不起第二个驱动（双驱动事故：两套驱动争抢同一状态/镜像）。
source "$(dirname "$0")/公共.sh"
用法() { cat <<'U'
用法: 启动.sh <输入目录> <产出目录> [--resume] [--档位=深度|标准|快速] [--并发=4] [--effort=xhigh] [--引擎=codex|claude] [--日志=路径] [--全新]
  --resume    从 <父目录>/状态.json 断点续跑（S5 有轮级断点）
  --档位      深度(=全开,默认) / 标准(摘要变体 3、审稿 3 轮、角色分档推理档) / 快速(冒烟) —— 见 references/流程与档位.md
  --并发      HIVE_MAX_CONCURRENT，同时在跑的 codex 腿数（默认 4；6 并发曾整晚命中 429）
  --effort    全局推理档（默认 xhigh；角色分档时按角色覆盖；两种引擎同名 low/medium/high/xhigh）
  --引擎      腿引擎：codex（codex exec + seatbelt）/ claude（claude -p 无头子代理）。默认取 scripts/引擎.default（发布版）或 LEG_ENGINE，再默认 codex；续跑换引擎会在驱动日志明示
  --日志      驱动 stdout（默认 <父目录>/驾驶_<产出名>.out；pid 同名 .pid）
  --全新      父目录已有 状态.json 时仍全新开跑（S0 会清空 蜂巢镜像/ 重建；不加此参就拒绝，防误覆盖）
U
}
[ $# -lt 2 ] && { 用法; exit 1; }
INPUT="$1"; OUT_ARG="$2"; shift 2
RESUME=0; PROFILE=""; CONC="${HIVE_MAX_CONCURRENT:-4}"; EFFORT="${LEG_EFFORT_DEFAULT:-${CODEX_EFFORT:-xhigh}}"; FRESH=0; ENGINE="${LEG_ENGINE:-$(默认引擎)}"; ENGINE_SET=0
for a in "$@"; do
  case "$a" in
    --resume) RESUME=1 ;; --档位=*) PROFILE="${a#--档位=}" ;; --快速) PROFILE="快速" ;;
    --并发=*) CONC="${a#--并发=}" ;; --effort=*) EFFORT="${a#--effort=}" ;; --引擎=*) ENGINE="${a#--引擎=}"; ENGINE_SET=1 ;;
    --日志=*) LOG_OVERRIDE="${a#--日志=}" ;; --全新) FRESH=1 ;; -h|--help) 用法; exit 0 ;;
    *) echo "!! 未知参数 $a"; 用法; exit 1 ;;
  esac
done
case "$PROFILE" in ""|深度|全开|标准|快速) ;; *) echo "!! 档位只能是 深度/标准/快速"; exit 1 ;; esac
case "$ENGINE" in codex|claude) ;; *) echo "!! 引擎只能是 codex/claude"; exit 1 ;; esac
炉 "$OUT_ARG"

# ---- 前置检查 ----
[ -d "$INPUT" ] || { echo "!! 输入目录不存在：$INPUT"; exit 1; }
ls "$INPUT"/*.pdf >/dev/null 2>&1 || echo "?? 输入目录里没有 PDF（题目应为 PDF）：$INPUT"
if [ "$ENGINE" = "claude" ]; then
  command -v claude >/dev/null || [ -x "$HOME/.local/bin/claude" ] || { echo "!! 找不到 claude CLI（引擎=claude 时腿靠它跑；登录态/端点由用户的 Claude Code 配置决定，本 skill 不读不改）"; exit 1; }
else
  command -v codex >/dev/null || { echo "!! 找不到 codex CLI（腿靠它跑；端点/模型由 ~/.codex/config.toml 决定）"; exit 1; }
fi
command -v xelatex >/dev/null || echo "?? 找不到 xelatex：编译节点会失败，先跑 bash 流水线/运行时/环境就绪.sh"
[ -x 流水线/运行时/venv/bin/python3 ] || echo "?? 流水线/运行时/venv 不存在：求解腿缺科学计算依赖，先跑 bash 流水线/运行时/环境就绪.sh"

# ---- 双驱动保护：同父目录已有驱动在跑 → 拒绝 ----
for p in $(父目录驱动); do
  echo "!! 拒绝启动：父目录 $PARENT 已有驱动在跑 pid=$p"; 驱动命令行 "$p"
  echo "   先 bash $SKILL_DIR/scripts/停跑.sh $OUT_ARG 停干净再来"; exit 3
done
for p in $(所有驱动); do echo "?? 另一炉驱动在跑（pid=${p}，不同父目录），两炉会争同一个端点：$(驱动命令行 "$p")"; done
if [ "$RESUME" = 1 ]; then
  [ -f "$STATE" ] || { echo "!! --resume 但没有 $STATE"; exit 1; }
  # 续跑默认沿用上一段记在 状态.json 的引擎；只有显式 --引擎= 才换（驱动日志会打「!! 腿引擎切换」）
  [ "$ENGINE_SET" = 1 ] || ENGINE="$(引擎)"
else
  if [ -f "$STATE" ] && [ "$FRESH" != 1 ]; then
    echo "!! $STATE 已存在（上一炉的断点）。要续跑加 --resume；要重头开跑加 --全新（会清空 ${MIRROR}）"; exit 1
  fi
fi

# ---- 起 ----
ARGS=("$INPUT" "$OUT_ARG"); [ "$RESUME" = 1 ] && ARGS+=("--resume")
case "$PROFILE" in 标准|快速) ARGS+=("--档位=$PROFILE") ;; esac
ARGS+=("--引擎=$ENGINE")
mkdir -p "$PARENT"; echo "$LOG" > "$PARENT/.驾驶_日志"
echo "[$(时刻)] 启动：档位=${PROFILE:-深度} 并发=$CONC effort=$EFFORT 引擎=$ENGINE 续跑=$RESUME"
echo "  LEG_EFFORT_DEFAULT=$EFFORT CODEX_EFFORT=$EFFORT HIVE_MAX_CONCURRENT=$CONC python3 流水线/后台启动.py $LOG python3 流水线/蜂群驾驶.py ${ARGS[*]}"
LEG_EFFORT_DEFAULT="$EFFORT" CODEX_EFFORT="$EFFORT" HIVE_MAX_CONCURRENT="$CONC" python3 流水线/后台启动.py "$LOG" python3 流水线/蜂群驾驶.py "${ARGS[@]}" || exit 1
sleep 20
if 驱动活着; then
  echo "[$(时刻)] 驱动 pid=$(驱动pid) 在跑。日志：$LOG"
  当前段 | grep -E "^- " | tail -4 | cut -c1-170
  echo "看状态：bash $SKILL_DIR/scripts/状态.sh $OUT_ARG"
else
  echo "!! 驱动 20 秒内退出了，日志尾："; tail -15 "$LOG" | cut -c1-200; exit 2
fi
