#!/bin/bash
# 体检：开炉前 / 换机器后的一键自检，只报告不修（补装用 bash 流水线/运行时/环境就绪.sh）。
# 密钥纪律：~/.codex/config.toml 只看是否存在，绝不读内容、不打印、不复制。
# 输出：✓ 通过 / ✗ 硬失败（不要开炉）/ ? 提醒。退出码 1 = 有硬失败。
source "$(dirname "$0")/公共.sh"
OUT_ARG=""; HARD=0; SOFT=0; ENGINE="$(默认引擎)"; PROBE=0
for a in "$@"; do case "$a" in --引擎=*) ENGINE="${a#--引擎=}";; --探针) PROBE=1;; -h|--help)
  echo "用法: 体检.sh [产出目录] [--引擎=codex|claude] [--探针]   （--探针：对 claude 引擎做一次 ≤120 s 的连通试跑，只转述 CLI 原话）"; exit 0;; *) OUT_ARG="$a";; esac; done
ok()   { printf '  ✓ %s\n' "$1"; }
bad()  { printf '  ✗ %s\n' "$1"; HARD=$((HARD+1)); }
warn() { printf '  ? %s\n' "$1"; SOFT=$((SOFT+1)); }

echo "== 项目 =="; ok "项目根 ${PROJ}"
[ -f "$PROJ/流水线/蜂群驾驶.py" ] && ok "驱动 流水线/蜂群驾驶.py" || bad "缺 流水线/蜂群驾驶.py"
python3 -m py_compile "$PROJ/流水线/蜂群驾驶.py" 2>/dev/null && ok "驱动可编译（py_compile）" || bad "驱动 py_compile 失败"

echo "== 腿引擎（本次按 ${ENGINE}；另一种只作提醒）=="
if [ "$ENGINE" = "claude" ]; then
  if command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ]; then ok "claude $(claude --version 2>/dev/null | head -1)"; else bad "找不到 claude CLI（引擎=claude 的腿全靠它）"; fi
  [ -f "$HOME/.claude/settings.json" ] && ok "Claude Code 用户配置存在（登录态/端点在此；本 skill 不读不改）" || warn "无 ~/.claude/settings.json（用 claude /login 的登录态也行）"
  if [ "$PROBE" = 1 ]; then
    OUTP="$(mktemp)"; ( unset CLAUDECODE; perl -e 'alarm shift; exec @ARGV' 120 claude -p "只回答两个字：可以" --output-format text --model sonnet --max-turns 1 --no-session-persistence </dev/null > "$OUTP" 2>&1 ); rc=$?
    if [ $rc -eq 0 ] && grep -q "可以" "$OUTP"; then ok "claude 探针通了（$(head -c 40 "$OUTP" | tr -d '\n')）"
    elif [ $rc -eq 142 ] || [ $rc -eq 14 ]; then bad "claude 探针 120 s 无响应（常见于 CLI 对不可用端点反复重试后才报 401；用户侧 claude /login 或修 ~/.claude/settings.json 的 env 块，本 skill 不动它）"
    else bad "claude 探针失败 rc=${rc}：$(grep -v '^$' "$OUTP" | tail -1 | cut -c1-120)（用户侧：claude /login 或修 ~/.claude/settings.json 的 env 块；本 skill 不动它）"; fi
    rm -f "$OUTP"
  else warn "未做连通探针（加 --探针 会真调一次 claude -p，≤120 s）"; fi
  command -v codex >/dev/null 2>&1 && warn "另一引擎 codex 也在（--引擎=codex 可切）" || true
else
if command -v codex >/dev/null 2>&1; then ok "codex $(codex --version 2>/dev/null | head -1)"
elif [ -x "$HOME/.local/share/fnm/aliases/default/bin/codex" ]; then warn "codex 不在 PATH，但 fnm 别名目录有（本地蜂巢.py 会自己找到；启动脚本请在能 command -v codex 的 shell 里跑）"
else bad "找不到 codex CLI（腿全靠它）"; fi
CFG="${CODEX_HOME:-$HOME/.codex}/config.toml"
[ -f "$CFG" ] && ok "codex 配置文件存在（端点/模型在此；本 skill 不读不改）" || bad "缺 ${CFG}（端点/模型由它决定；请用户自行配置）"
  command -v claude >/dev/null 2>&1 && warn "另一引擎 claude 也在（--引擎=claude 可切；先 --探针）" || true
fi

echo "== TeX / 渲染 =="
for c in xelatex gs pdftoppm; do
  if command -v "$c" >/dev/null 2>&1 || [ -x "/opt/homebrew/opt/texlive/bin/$c" ]; then ok "$c"; else bad "缺 $c（bash 流水线/运行时/环境就绪.sh）"; fi
done

echo "== Python 科学栈 =="
PYV="$PROJ/流水线/运行时/venv/bin/python3"
if [ -x "$PYV" ]; then
  if "$PYV" -c "import numpy, pandas, matplotlib, scipy, sklearn" 2>/dev/null; then ok "venv 科学栈齐（$("$PYV" -V 2>&1)）"; else bad "venv 缺包（环境就绪.sh 补装）"; fi
else bad "缺 流水线/运行时/venv（环境就绪.sh）"; fi
if python3 -c "import numpy" 2>/dev/null; then ok "shell 里的 python3 也有 numpy（$(python3 -V 2>&1)）"
else warn "shell 里的 python3（$(python3 -V 2>&1)）没有 numpy：正常——腿内只用裸 python3（驱动 PATH 首位是 venv），别写绝对路径解释器（P8）"; fi

echo "== 字体 =="
if ls "$HOME/Library/Fonts"/FandolHei* >/dev/null 2>&1 || ls "$(brew --prefix texlive 2>/dev/null)/share/texmf-dist/fonts/opentype/public/fandol"/FandolHei* >/dev/null 2>&1; then ok "Fandol 字体"; else bad "缺 Fandol 字体（编译与绘图中文的命根子；环境就绪.sh）"; fi
[ -e "$HOME/Library/Fonts/NotoSansCJKsc-Regular.otf" ] && ok "Noto Sans CJK SC" || warn "缺 Noto Sans CJK SC（绘图兜底字体）"

echo "== 磁盘 =="
FREE=$(df -g "$PROJ" 2>/dev/null | awk 'NR==2{print $4}')
if [ -n "$FREE" ]; then
  if [ "$FREE" -lt 10 ]; then bad "磁盘剩余 ${FREE} GB（<10：一炉镜像 1.5 GB + 日志 1 GB，编译/腿写盘会失败）"
  elif [ "$FREE" -lt 20 ]; then warn "磁盘剩余 ${FREE} GB（<20：归档旧炉、删旧炉 蜂巢镜像/日志/*.log 与 论文/页/*.png）"
  else ok "磁盘剩余 ${FREE} GB"; fi
fi

echo "== skill 链接 =="
for d in "$HOME/.claude/skills/paper-foundry" "${CODEX_HOME:-$HOME/.codex}/skills/paper-foundry"; do
  if [ -L "$d" ] && [ -f "$d/SKILL.md" ]; then ok "$d → $(readlink "$d")"; else warn "未链接 $d（bash scripts/安装.sh）"; fi
done

echo "== 炉 =="
if [ -n "$OUT_ARG" ]; then
  炉 "$OUT_ARG"
  P="$(父目录驱动)"
  [ -n "$P" ] && warn "父目录 ${PARENT} 已有驱动在跑（pid ${P}）：只能 状态.sh/看守.sh，不能再开" || ok "父目录 ${PARENT} 无驱动在跑"
  [ -f "$STATE" ] && warn "有断点 ${STATE}（续跑加 --resume；重开加 --全新）" || ok "无断点（全新开炉）"
  LOCK="$PARENT/.切换.lock"
  if [ -f "$LOCK" ]; then
    if kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then warn "有切换在等（pid $(cat "$LOCK")）"; else warn "切换锁残留 ${LOCK}（pid 已死，可 rm）"; fi
  fi
  [ -d "$MIRROR" ] && ok "镜像 ${MIRROR}（$(du -sh "$MIRROR" 2>/dev/null | cut -f1)）"
else
  for p in $(所有驱动); do warn "另有驱动在跑 pid=${p}：$(驱动命令行 "$p")"; done
fi

echo "== git =="
N=$(git -C "$PROJ" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
[ "$N" = 0 ] && ok "工作树干净" || warn "有 ${N} 处未提交改动（开炉前提交一个干净基线，事后才好对照）"

echo "== 结果：硬失败 ${HARD}，提醒 ${SOFT} =="
[ "$HARD" = 0 ] && { echo "可以开炉：bash $SKILL_DIR/scripts/启动.sh <输入> <产出> [--档位=标准] [--引擎=${ENGINE}]"; exit 0; }
echo "!! 有硬失败，先补环境（bash 流水线/运行时/环境就绪.sh）再来"; exit 1
