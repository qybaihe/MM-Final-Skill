#!/usr/bin/env bash
set -euo pipefail

# 本批只负责落图脚本的顺序执行；图脚本自身不超过20分钟预算。
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
python3 求解/公共/绘图_全文思路图.py
python3 求解/公共/绘图_四谱原值显示低波数异常.py
python3 求解/公共/绘图_共同坐标标记质量区段.py
python3 求解/公共/绘图_波段散布差异不等于噪声.py
python3 求解/问题1/绘图_一次往返保留两束反射场.py
python3 求解/问题1/绘图_场求和与展开强度相符.py
python3 求解/问题1/绘图_色散斜率改变局部峰距.py
