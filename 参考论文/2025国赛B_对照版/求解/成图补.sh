#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT}"
export MPLBACKEND=Agg

# G3 返工只重跑改过构图的脚本；预留 60 秒收尾余量，严格控制在 15 分钟内。
MAX_SECONDS=840
STOPPED_BY_BUDGET=0
run_graph() {
  if (( SECONDS >= MAX_SECONDS )); then
    echo "成图补跑达到时间预算，跳过后续：$1" >&2
    STOPPED_BY_BUDGET=1
    return 0
  fi

  # 图11的绘图脚本明确禁止用空损失列伪造等高线；正式剖面尚未提供
  # 至少 12 个真实损失值时，将该图记为条件不满足并继续其余成图。
  if [[ "$1" == "求解/问题2/绘图_厚度与折射率存在补偿关系.py" ]]; then
    profile="求解/问题2/结果/参数剖面.csv"
    if [[ ! -s "$profile" ]]; then
      echo "跳过图11：正式参数剖面.csv 不存在或为空。" >&2
      return 0
    fi
    usable="$(awk -F, 'NR == 1 { for (i = 1; i <= NF; i++) if ($i ~ /训练标准化损失/) col = i; next } col > 0 && $col ~ /[^[:space:]]/ { n++ } END { print n + 0 }' "$profile")"
    if (( usable < 12 )); then
      echo "跳过图11：正式参数剖面.csv 仅有 ${usable} 个真实损失值（至少需要 12 个）。" >&2
      return 0
    fi
  fi
  python3 "$1"
}

run_graph "求解/公共/绘图_四谱原值显示低波数异常.py"
run_graph "求解/公共/绘图_共同坐标标记质量区段.py"
run_graph "求解/问题1/绘图_一次往返保留两束反射场.py"
run_graph "求解/问题1/绘图_场求和与展开强度相符.py"
run_graph "求解/问题1/绘图_三路线误差按情景分布.py"
run_graph "求解/问题2/绘图_厚度与折射率存在补偿关系.py"
run_graph "求解/问题2/绘图_碳化硅厚度随假设变化.py"
run_graph "求解/问题3/绘图_谐波贡献核对往返递推.py"
run_graph "求解/问题3/绘图_逐块对照区分两材料收益.py"
run_graph "求解/问题3/绘图_厚度扰动区分谱形与测厚影响.py"
run_graph "求解/问题3/绘图_两材料厚度汇总保留条件.py"

if (( STOPPED_BY_BUDGET )); then
  echo "G3 返工已完成预算内成图；其余图因时间预算跳过。"
else
  echo "G3 返工受影响图脚本已按序完成（条件不满足的图已明确跳过）。"
fi
