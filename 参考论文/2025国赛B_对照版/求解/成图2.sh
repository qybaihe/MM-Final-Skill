#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python3 "求解/问题1/绘图_三路线误差按情景分布.py"
python3 "求解/问题2/绘图_连续留段隔离拟合与检验.py"
python3 "求解/问题2/绘图_双角拟合残差显示局部偏差.py"
if ! python3 "求解/问题2/绘图_厚度与折射率存在补偿关系.py"; then
  echo "图11跳过：正式参数剖面的训练标准化损失列为空，待上游补写真实数值后重跑。" >&2
fi
python3 "求解/问题2/绘图_留角度预测核对共享厚度.py"
python3 "求解/问题2/绘图_碳化硅厚度随假设变化.py"
python3 "求解/问题2/绘图_预测覆盖揭示区间失配.py"
