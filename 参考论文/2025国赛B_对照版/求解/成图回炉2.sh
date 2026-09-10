#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export MPLBACKEND=Agg
export MPLCONFIGDIR="$ROOT/日志/回炉图_轮2运行/matplotlib"
export PYTHONUNBUFFERED=1
mkdir -p "$MPLCONFIGDIR"

python3 - <<'PY'
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

root = Path.cwd()
started = time.monotonic()
deadline = started + 1140
log_dir = root / '日志/回炉图_轮2运行'
jobs = [
    ('问题1', '三路线误差按情景分布'),
    ('问题2', '双角拟合残差显示局部偏差'),
    ('问题2', '留角度预测核对共享厚度'),
    ('问题3', '多次往返按复振幅递减'),
    ('问题3', '硅双角谱检验高阶贡献'),
    ('问题3', '逐块对照区分两材料收益'),
    ('问题3', '两材料厚度汇总保留条件'),
    ('问题3', '厚度扰动区分谱形与测厚影响'),
]
bootstrap = '''
import os
import runpy
import signal
import subprocess
import sys
from pathlib import Path
signal.alarm(165)
from matplotlib import font_manager
font_path = subprocess.check_output(['kpsewhich', 'FandolHei-Regular.otf'], text=True, timeout=10).strip()
if not font_path or not Path(font_path).is_file():
    raise RuntimeError('未找到FandolHei字体文件，停止本图')
font_manager.fontManager.addfont(font_path)
os.chdir(Path(sys.argv[1]).parent)
runpy.run_path(sys.argv[1], run_name='__main__')
'''
records = []
failed = False
for problem, name in jobs:
    if deadline - time.monotonic() < 180:
        records.append({'图名': name, '状态': '未开始：接近19分钟预算，保留已生成结果'})
        failed = True
        break
    script = root / '求解' / problem / f'绘图_{name}.py'
    image = script.parent / '图片' / f'{name}.png'
    original = image.read_bytes() if image.exists() else None
    original_mtime = image.stat().st_mtime_ns if image.exists() else 0
    log_path = log_dir / f'{name}.log'
    job_started = time.monotonic()
    print(f'开始：{name}；单图170秒上限', flush=True)
    with log_path.open('w', encoding='utf-8') as stream:
        try:
            process = subprocess.run([sys.executable, '-c', bootstrap, str(script)],
                                     stdout=stream, stderr=subprocess.STDOUT, timeout=170)
            success = (process.returncode == 0 and image.is_file()
                       and image.stat().st_mtime_ns > original_mtime)
            state = '已重绘，待入PDF核验' if success else f'失败，退出码{process.returncode}，恢复原PNG'
        except subprocess.TimeoutExpired:
            success = False
            state = '单图超时，恢复原PNG'
    if not success:
        failed = True
        if original is not None:
            image.write_bytes(original)
        elif image.exists():
            image.unlink()
    records.append({'图名': name, '状态': state, '用时_秒': round(time.monotonic() - job_started, 3),
                    '日志': str(log_path.relative_to(root)),
                    '新图SHA256': hashlib.sha256(image.read_bytes()).hexdigest() if success else None})
    (log_dir / '逐图执行.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{name}：{state}', flush=True)
(log_dir / '逐图执行.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'本批计划{len(jobs)}张，用时{time.monotonic() - started:.1f}秒；未运行求解器、未编译PDF。')
sys.exit(1 if failed else 0)
PY
