#!/bin/bash
# 环境就绪：本机环境的一键自检/补装（幂等，可反复跑）。
# 覆盖 施工日志 M5-0 之后本地流水线需要的全部要件；缺什么装什么，已齐的跳过。
# 用法：bash 流水线/运行时/环境就绪.sh
set -u
PPL="$(cd "$(dirname "$0")/.." && pwd)"
PROJ="$(dirname "$PPL")"
VENV="$PPL/运行时/venv"
ok=0; ZHUANG=0

JI() { printf '  [%s] %s %s\n' "$([ "$1" = 0 ] && echo ✓ || echo 装)" "$2" "${3:-}"; }

echo "== 1. Homebrew 命令行要件 =="
for pkg in texlive ghostscript graphviz coreutils poppler; do
  if brew list --versions "$pkg" >/dev/null 2>&1; then
    JI 0 "$pkg"
  else
    JI 1 "$pkg" "（brew install $pkg）"
    brew install "$pkg" >/dev/null 2>&1 && ok=$((ok+1)) || { echo "    !! $pkg 安装失败"; }
    ZHUANG=$((ZHUANG+1))
  fi
done

echo "== 2. 中文字体（matplotlib/绘图腿的命根子） =="
TEXFONTS="$(brew --prefix texlive 2>/dev/null)/share/texmf-dist/fonts/opentype/public/fandol"
mkdir -p "$HOME/Library/Fonts"
if [ -d "$TEXFONTS" ]; then
  for f in "$TEXFONTS"/*.otf; do
    MING="$(basename "$f")"
    [ -e "$HOME/Library/Fonts/$MING" ] || { ln -s "$f" "$HOME/Library/Fonts/$MING"; JI 1 "Fandol $MING"; ZHUANG=$((ZHUANG+1)); }
  done
  JI 0 "Fandol 全家（symlink → texlive）"
else
  echo "  !! 未找到 texlive Fandol 字体目录：$TEXFONTS"
fi
if [ -e "$HOME/Library/Fonts/NotoSansCJKsc-Regular.otf" ]; then
  JI 0 "Noto Sans CJK SC"
else
  JI 1 "Noto Sans CJK SC" "（brew install --cask font-noto-sans-cjk-sc）"
  brew install --cask font-noto-sans-cjk-sc >/dev/null 2>&1 || echo "    !! Noto 安装失败（绘图兜底字体-1）"
  ZHUANG=$((ZHUANG+1))
fi

echo "== 3. Python venv（全栈） =="
if [ ! -x "$VENV/bin/python3" ]; then
  JI 1 "venv" "（python3 -m venv）"
  python3 -m venv "$VENV"
fi
QUE=$("$VENV/bin/python3" - <<'EOF'
import importlib.util
需 = ["numpy","pandas","matplotlib","scipy","sklearn","statsmodels","sympy",
     "networkx","pypdf","openpyxl","docx","lxml"]
print(" ".join(m for m in 需 if importlib.util.find_spec(m) is None))
EOF
)
if [ -n "$QUE" ]; then
  JI 1 "python 包：$QUE"
  "$VENV/bin/pip" install -q $QUE || echo "    !! pip 安装失败：$QUE"
  ZHUANG=$((ZHUANG+1))
else
  JI 0 "python 全栈（12 包）"
fi

echo "== 4. matplotlib 字体缓存（装了字体必须重建） =="
# 缓存文件名带的是格式版本（如 fontlist-v3.11.0.json），不是 matplotlib 版本——用 glob 找
HCUN="$(ls -t "$HOME"/.matplotlib/fontlist-*.json 2>/dev/null | head -1)"
REBUILD=0
[ -n "$HCUN" ] && [ -e "$HCUN" ] || REBUILD=1
if [ "$REBUILD" = 0 ]; then
  # 缓存比任一 Fandol symlink 旧就重建（新装字体后缓存必然过期——M5-0b 的教训）
  for f in "$HOME/Library/Fonts/"Fandol*.otf; do
    [ "$f" -nt "$HCUN" ] && REBUILD=1 && break
  done
fi
if [ "$REBUILD" = 1 ]; then
  rm -f "$HOME/.matplotlib"/fontlist-*.json
  "$VENV/bin/python3" -c "from matplotlib import font_manager; font_manager.fontManager" >/dev/null 2>&1
  JI 1 "字体缓存已重建"
  ZHUANG=$((ZHUANG+1))
else
  JI 0 "字体缓存"
fi
HIT=$("$VENV/bin/python3" -c "
from matplotlib import font_manager
名 = {f.name for f in font_manager.fontManager.ttflist}
print(' '.join(x for x in ('FandolHei','Noto Sans CJK SC') if x not in 名))")
[ -z "$HIT" ] && JI 0 "matplotlib 可见 FandolHei/Noto" || echo "  !! matplotlib 仍缺字体：$HIT（手工删 ~/.matplotlib/fontlist-*.json 后重跑本脚本）"

echo "== 5. codex CLI =="
if [ -e "$HOME/.local/share/fnm/aliases/default/bin/codex" ] || command -v codex >/dev/null 2>&1; then
  JI 0 "codex（$(codex --version 2>/dev/null || echo 'fnm default')）"
else
  echo "  !! codex 不在 PATH 也不在 fnm default 别名——需手工装（npm i -g @openai/codex 或 fnm 环境）"
fi

echo
echo "完成：新装/修复 $ZHUANG 项。全部就绪后可跑：python3 流水线/验证/本地故障注入.py"
