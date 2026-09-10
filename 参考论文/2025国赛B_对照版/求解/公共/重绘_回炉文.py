import importlib.util
import json
import runpy
import subprocess
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
from matplotlib import font_manager


ROOT = Path(__file__).resolve().parents[2]
START = time.monotonic()
BUDGET = 180.0
font_path = subprocess.check_output(['kpsewhich', 'FandolHei-Regular.otf'], text=True).strip()
font_manager.fontManager.addfont(font_path)
spec = importlib.util.spec_from_file_location('绘图公共', ROOT / '求解/公共/绘图公共.py')
common = importlib.util.module_from_spec(spec)
sys.modules['绘图公共'] = common
spec.loader.exec_module(common)
paths = [
    '求解/公共/绘图_全文思路图.py',
    '求解/问题2/绘图_双角拟合残差显示局部偏差.py',
    '求解/问题2/绘图_留角度预测核对共享厚度.py',
    '求解/问题2/绘图_碳化硅厚度随假设变化.py',
    '求解/问题3/绘图_硅双角谱检验高阶贡献.py',
    '求解/问题3/绘图_两材料厚度汇总保留条件.py',
    '求解/问题3/绘图_厚度扰动区分谱形与测厚影响.py',
]
for relative in paths:
    if time.monotonic() - START > BUDGET - 10:
        raise TimeoutError('达到重绘预算，停止新增绘制')
    runpy.run_path(str(ROOT / relative), run_name='__main__')
print(json.dumps({'重绘数量': len(paths), '实际用时_秒': time.monotonic() - START}, ensure_ascii=False))
