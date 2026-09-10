#!/bin/bash
# 本机编译论文（需 brew install texlive）。用法: bash 编译.sh <论文目录>
# 与容器口径一致：xelatex 跑三遍（目录/引用/页码收敛），失败时打印真错误行。
set -u
DIR="${1:?用法: bash 编译.sh <论文目录>}"
export PATH="/opt/homebrew/opt/texlive/bin:/opt/homebrew/bin:$PATH"
cd "$DIR" || exit 1
command -v xelatex >/dev/null || { echo "✗ 找不到 xelatex"; exit 1; }
for i in 1 2 3; do
  xelatex -interaction=nonstopmode -halt-on-error 论文.tex > /tmp/tex_pass$i.log 2>&1
  rc=$?
  if [ $rc -ne 0 ]; then
    echo "✗ 第 $i 遍失败 (rc=$rc)，错误摘录："
    grep -nE "^!|^l\.[0-9]+|LaTeX Error|Package .* Error|Font .* not (found|loadable)" /tmp/tex_pass$i.log | head -20
    exit $rc
  fi
done
E=$(grep -c "^!" /tmp/tex_pass3.log)
O=$(grep -c "Overfull \\\\hbox" /tmp/tex_pass3.log)
P=$(grep -oE "Output written on 论文.pdf \(([0-9]+) page" /tmp/tex_pass3.log | grep -oE "[0-9]+" | head -1)
echo "✓ 编译完成  E=$E  Overfull=$O  页数=${P:-?}"
ls -la 论文.pdf
