#!/bin/bash
# 论文铸造厂 skill · 公共库：项目根解析、同炉文件定位、进程判定。其它脚本 source 它，不单独运行。
# 注意：macOS 自带 bash 3.2：变量名只能是 ASCII（函数名可以中文）；$var 后面紧跟中文时必须写 ${var}，否则中文字节会被并进变量名。
# 安装方式可能是符号链接（~/.codex/skills、~/.claude/skills → 仓库 .claude/skills/paper-foundry），
# 所以一律用 pwd -P 回到物理路径再往上找项目根；也接受 SHUMO_PROJ 环境变量指定。
set -u
_SK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

_find_proj() {
  local c
  for c in "${SHUMO_PROJ:-}" "$(cd "$_SK/../../.." 2>/dev/null && pwd -P)" "$(cd "$_SK/../.." 2>/dev/null && pwd -P)" "$PWD" \
           "$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null)"; do
    [ -n "$c" ] && [ -f "$c/流水线/蜂群驾驶.py" ] && { echo "$c"; return 0; }
  done
  return 1
}
PROJ="$(_find_proj)" || { echo "!! 找不到项目根（需含 流水线/蜂群驾驶.py）；请 export SHUMO_PROJ=<仓库路径> 或在仓库内运行" >&2; exit 1; }
cd "$PROJ" || exit 1
SKILL_DIR="$_SK"
LOG_OVERRIDE="${LOG_OVERRIDE:-}"

# 炉 <产出目录>：按驱动的约定推出同炉文件（状态/台账/镜像都在产出目录的父目录里——一个父目录只能住一炉）。
# 驱动 stdout 日志默认 <父目录>/驾驶_<产出名去掉“成品_”>.out，pid 同名 .pid；启动.sh 会把实际日志路径记到 <父目录>/.驾驶_日志。
炉() {
  OUT="${1%/}"; PARENT="$(dirname "$OUT")"; MIRROR="$PARENT/蜂巢镜像"; STATE="$PARENT/状态.json"; LEDGER="$PARENT/台账"
  local tag; tag="$(basename "$OUT")"; tag="${tag#成品_}"; tag="${tag#成品}"; [ -z "$tag" ] && tag="默认"
  LOG="$LOG_OVERRIDE"
  [ -z "$LOG" ] && [ -f "$PARENT/.驾驶_日志" ] && LOG="$(cat "$PARENT/.驾驶_日志")"
  [ -z "$LOG" ] && LOG="$PARENT/驾驶_$tag.out"
  PIDF="${LOG%.out}.pid"
}

驱动pid() { [ -f "$PIDF" ] && tr -d ' \n' < "$PIDF"; }
# macOS 的 ps 会把命令行里的非 ASCII 字节转义成 M-x 序列，grep 中文永远不中；进程匹配一律走 pgrep -f（按原始字节匹配）。
# R62：驱动 = 「某个 python 解释器 + 第一个参数是 …蜂群驾驶.py」，锚定 argv 前缀。旧写法 pgrep -f "蜂群驾驶.py" 是子串匹配，
# 操盘手自己命令行里提到这个文件名的 shell（如 py_compile / grep 流水线/蜂群驾驶.py）都会被数成驱动
# （2026-09-11 A 题 22:51 切换.sh 停净判定「驱动=1」实为滞留工具 shell → 没停净、不续跑）。
DRV_RE='^[^ ]*[Pp]ython[^ ]* [^ ]*蜂群驾驶\.py( |$)'
是驱动() { [ -n "${1:-}" ] && pgrep -f "$DRV_RE" 2>/dev/null | grep -qx "$1"; }
驱动活着() { local p; p="$(驱动pid)"; [ -n "$p" ] && kill -0 "$p" 2>/dev/null && 是驱动 "$p"; }
所有驱动() { pgrep -f "$DRV_RE" 2>/dev/null || true; }
# 同父目录的驱动（状态/台账/镜像共用父目录，两个驱动同父目录 = 双驱动事故）；按父目录名匹配，相对/绝对路径都认
父目录驱动() { pgrep -f "^[^ ]*[Pp]ython[^ ]* [^ ]*蜂群驾驶\.py .*$(basename "$PARENT")/" 2>/dev/null || true; }
驱动命令行() { pgrep -fl "$DRV_RE" 2>/dev/null | grep "^${1:-} " | cut -c1-160; }
# 一条腿 = 一个 codex 包装进程（node 包装或原生二进制）；perl with_timeout 那行虽含同样参数但以 perl 开头，不算
codex腿数() { ps -eo command | grep -E '^(node |/[^ ]*/codex |codex )' | grep -c ' exec ' | tr -d ' '; }
xelatex数() { pgrep -x xelatex 2>/dev/null | wc -l | tr -d ' '; }
是腿() { [ -n "${1:-}" ] && pgrep -f 'role\.sh|图片腿\.sh|codex exec|claude -p' 2>/dev/null | grep -qx "$1"; }
claude腿数() { ps -eo command | grep -E '(^|/)claude -p ' | grep -vc grep | tr -d ' '; }
腿数() { echo $(( $(codex腿数) + $(claude腿数) )); }
# 引擎：状态.json 里记的腿引擎（驱动开炉/续跑时写入）；没有就是 codex（旧炉）
引擎() { [ -f "$STATE" ] && python3 -c "import json,sys; print(json.load(open(sys.argv[1], encoding='utf-8')).get('引擎') or 'codex')" "$STATE" 2>/dev/null || echo codex; }
# 本 skill 目录的默认引擎（发布版由 打包.sh 写 scripts/引擎.default；核心版没有该文件 → codex）
默认引擎() { [ -f "$SKILL_DIR/scripts/引擎.default" ] && tr -d ' \n' < "$SKILL_DIR/scripts/引擎.default" || echo codex; }
# 活腿列表：镜像/日志/*.pid 里 pid 仍活着**且确认是腿**的（pid 文件会过期、pid 会被复用——铁律）
活腿列表() {
  local f p n
  for f in "$MIRROR"/日志/*.pid; do
    [ -f "$f" ] || continue
    p="$(tr -d ' \n' < "$f")"; n="$(basename "$f" .pid)"
    [ -n "$p" ] && kill -0 "$p" 2>/dev/null && 是腿 "$p" && echo "$n $p $(ps -o etime= -p "$p" | tr -d ' ')"
  done
}
# 当前段：最后一次「===== 后台启动」以来的日志（--resume 会追加到同一个文件）
当前段() { [ -f "$LOG" ] && awk '/^===== 后台启动 /{buf=""} {buf=buf $0 "\n"} END{printf "%s", buf}' "$LOG"; }
# 监督用的事件正则（与 references/监督清单.md 同口径）
WATCH_RE='门\[|!!|红队结论|仲裁|升格|降级放行|\b429\b|rc=142|孤儿|Traceback|终态|驾驶结束|预算|腿死亡|重派|S[0-6][ab]?[ :：]|checkpoint\[|回退\(|熔断|搁置|收敛|变化守卫.*超线|结构守卫|页数守卫|复核前|换版清单|修脚本_|rc=[1-9]|美化图|成图美化|转路 [1-9]'
时刻() { date +%H:%M:%S; }
