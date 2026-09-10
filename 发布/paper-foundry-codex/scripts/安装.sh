#!/bin/bash
# 双端安装：把本 skill（仓库内 .claude/skills/paper-foundry）以符号链接装到
#   Claude Code 用户级  ~/.claude/skills/paper-foundry   （项目级无需安装：在仓库里打开 Claude Code 即自动发现）
#   Codex              ~/.codex/skills/paper-foundry    （$CODEX_HOME 优先；agents/openai.yaml 供 Codex UI 用）
# 链接而不是复制：两端永远用同一份、随仓库 git 更新。用法：安装.sh [--宿主=claude|codex|both] [--检查|--卸载]
# 发布版（发布/paper-foundry-claude、发布/paper-foundry-codex）各自默认只装到自己的宿主；核心版默认两端都装。
set -u
SRC="$(cd "$(dirname "$0")/.." && pwd -P)"
NAME="$(basename "$SRC")"
CLAUDE="$HOME/.claude/skills/$NAME"
CODEX="${CODEX_HOME:-$HOME/.codex}/skills/$NAME"
HOST="both"; case "$NAME" in *-claude) HOST="claude";; *-codex) HOST="codex";; esac
ARGS=(); for a in "$@"; do case "$a" in --宿主=*) HOST="${a#--宿主=}";; *) ARGS+=("$a");; esac; done; set -- "${ARGS[@]}"
case "$HOST" in claude) TARGETS=("$CLAUDE");; codex) TARGETS=("$CODEX");; both) TARGETS=("$CLAUDE" "$CODEX");; *) echo "!! --宿主 只能是 claude/codex/both"; exit 1;; esac
if [ "${1:-}" = "--检查" ]; then
  rc=0
  for d in "${TARGETS[@]}"; do
    if [ -L "$d" ] && [ -f "$d/SKILL.md" ]; then echo "✓ $d → $(readlink "$d")"; else echo "✗ 未链接 $d"; rc=1; fi
  done
  command -v codex >/dev/null && echo "✓ codex $(codex --version 2>/dev/null | head -1)" || { echo "? 本机没有 codex CLI（腿依赖它）"; }
  [ -f "$SRC/SKILL.md" ] && echo "✓ 源 ${SRC}（$(ls "$SRC/references" | wc -l | tr -d ' ') 份参考，$(ls "$SRC/scripts" | wc -l | tr -d ' ') 个脚本）"
  exit $rc
fi
if [ "${1:-}" = "--卸载" ]; then
  for d in "${TARGETS[@]}"; do [ -L "$d" ] && rm "$d" && echo "已移除链接 $d"; done; exit 0
fi
[ -f "$SRC/SKILL.md" ] || { echo "!! $SRC 里没有 SKILL.md"; exit 1; }
for d in "${TARGETS[@]}"; do
  mkdir -p "$(dirname "$d")"
  if [ -e "$d" ] && [ ! -L "$d" ]; then echo "!! $d 已存在且不是链接，不覆盖（自行处理后重跑）"; continue; fi
  ln -sfn "$SRC" "$d" && echo "已链接 $d → $SRC"
done
[ -f "$CODEX/SKILL.md" ] && echo "Codex：新开会话后用 \$$NAME 调用（或让它自动匹配）"
[ -f "$CLAUDE/SKILL.md" ] && echo "Claude Code：任意目录 /$NAME 调用；在仓库内打开时项目级 skill 已自动生效"
[ -f "$SRC/scripts/引擎.default" ] && echo "本版默认腿引擎：$(cat "$SRC/scripts/引擎.default")"
command -v codex >/dev/null && echo "codex $(codex --version 2>/dev/null | head -1)" || echo "?? 本机没有 codex CLI（引擎=codex 时腿依赖它）"
command -v claude >/dev/null && echo "claude $(claude --version 2>/dev/null | head -1)" || echo "?? 本机没有 claude CLI（引擎=claude 时腿依赖它）"
