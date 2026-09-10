#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export MPLBACKEND=Agg

python3 "求解/问题1/绘图_一次往返保留两束反射场.py"
python3 "求解/问题2/绘图_双角拟合残差显示局部偏差.py"
python3 "求解/问题3/绘图_厚度扰动区分谱形与测厚影响.py"
python3 "求解/问题3/绘图_谐波贡献核对往返递推.py"
