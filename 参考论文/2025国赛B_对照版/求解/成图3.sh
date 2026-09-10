#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
export MPLBACKEND=Agg

python3 "${SCRIPT_DIR}/问题3/绘图_多次往返按复振幅递减.py"
python3 "${SCRIPT_DIR}/问题3/绘图_硅双角谱检验高阶贡献.py"
python3 "${SCRIPT_DIR}/问题3/绘图_逐块对照区分两材料收益.py"
python3 "${SCRIPT_DIR}/问题3/绘图_谐波贡献核对往返递推.py"
python3 "${SCRIPT_DIR}/问题3/绘图_厚度扰动区分谱形与测厚影响.py"
python3 "${SCRIPT_DIR}/问题3/绘图_两材料厚度汇总保留条件.py"
