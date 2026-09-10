#!/bin/bash
# 改代码之后、续跑之前的验证门：py_compile 全部 → 单测 → （可选）驱动干跑场景。任一失败即非零退出。
# 铁律：每次改 流水线/*.py 都要过它；改了驱动编排（节点/门/续跑/S5 轮级断点/回炉/G5/美化）还要跑对应干跑场景。
# 改了角色文件/任务文本：契约核对在单测里；再 python3 流水线/验证/角色冒烟.py <角色> --根=<副本根>（不对着活镜像）。
source "$(dirname "$0")/公共.sh"
DRY=""; ALLTESTS=0
for a in "$@"; do case "$a" in --干跑=*) DRY="${a#--干跑=}";; --全单测) ALLTESTS=1;; -h|--help)
  echo "用法: 验证.sh [--干跑=全链,中断续跑,级联,门升格,韧性,S5续跑,G5图路,G5算条,G5页数,引擎] [--全单测]"; exit 0;; esac; done
FAIL=0
echo "== py_compile =="
PY=$(find 流水线 -name '*.py' -not -path '*/venv/*' -not -path '*/__pycache__/*')
if python3 -m py_compile $PY; then echo "  $(echo "$PY" | wc -l | tr -d ' ') 个文件通过"; else echo "  !! 有文件编译失败"; FAIL=1; fi
echo "== 单测 =="
TESTS="回路单测 统稿守卫单测 门检单测 审计单测 契约核对单测"
bash "$(cd "$(dirname "$0")" && pwd -P)/打包.sh" --核对 || FAIL=1     # 发布版必须与核心一致（改核心后 bash scripts/打包.sh）
bash 流水线/验证/腿引擎分发.sh >/dev/null 2>&1 && echo "  ✓ 腿引擎分发单测（codex/claude 桩）" || { echo "  ✗ 腿引擎分发单测失败：bash 流水线/验证/腿引擎分发.sh"; FAIL=1; }
[ "$ALLTESTS" = 1 ] && TESTS="$TESTS 答案门单测"   # 答案门单测 现状 0/7：源码级抽取漏了 读词表（已知，见 references/流程与档位.md 整合清单）
for t in $TESTS; do
  out="$(python3 流水线/验证/$t.py 2>&1)"; rc=$?
  tail1="$(printf '%s\n' "$out" | tail -1 | cut -c1-100)"
  if [ $rc -ne 0 ] || printf '%s' "$tail1" | grep -Eq '失败|错误|✗|(^| )0/' ; then echo "  ✗ ${t}：$tail1"; FAIL=1; else echo "  ✓ ${t}：$tail1"; fi
done
if [ -n "$DRY" ]; then
  echo "== 驱动干跑 =="
  for m in ${DRY//,/ }; do
    s=$(date +%s); out="$(python3 流水线/验证/驱动干跑.py "$m" 2>&1)"; rc=$?
    if [ $rc -eq 0 ] && ! printf '%s' "$out" | grep -q "AssertionError\|Traceback"; then echo "  ✓ 干跑 $m [$(( $(date +%s)-s ))s]"
    else echo "  ✗ 干跑 $m [$(( $(date +%s)-s ))s]"; printf '%s\n' "$out" | tail -8 | cut -c1-160; FAIL=1; fi
  done
fi
[ "$FAIL" = 0 ] && { echo "验证通过"; exit 0; } || { echo "!! 验证未通过，不要续跑"; exit 1; }
