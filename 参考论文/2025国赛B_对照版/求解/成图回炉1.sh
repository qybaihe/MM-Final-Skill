#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export MPLBACKEND=Agg
export MPLCONFIGDIR="$ROOT/日志/回炉图_轮1运行/matplotlib"
export PYTHONUNBUFFERED=1
mkdir -p "$MPLCONFIGDIR"

python3 - <<'PY'
import json
import subprocess
import sys
import time
from pathlib import Path

root = Path.cwd()
started = time.monotonic()
deadline = started + 1140
log_dir = root / '日志/回炉图_轮1运行'
jobs = [
    ('公共', '四谱原值显示低波数异常'),
    ('公共', '波段散布差异不等于噪声'),
    ('问题1', '三路线误差按情景分布'),
    ('问题2', '双角拟合残差显示局部偏差'),
    ('问题2', '留角度预测核对共享厚度'),
    ('问题3', '多次往返按复振幅递减'),
    ('问题3', '硅双角谱检验高阶贡献'),
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
    raise RuntimeError('未找到FandolHei字体文件，保留原PNG并停止本图')
font_manager.fontManager.addfont(font_path)
os.chdir(Path(sys.argv[1]).parent)
runpy.run_path(sys.argv[1], run_name='__main__')
'''
records = []
failed = False
for problem, name in jobs:
    remaining = deadline - time.monotonic()
    if remaining < 180:
        records.append({'图名': name, '状态': '未开始：接近19分钟批次预算，已成图全部保留'})
        failed = True
        break
    script = root / '求解' / problem / f'绘图_{name}.py'
    image = script.parent / '图片' / f'{name}.png'
    original_image = image.read_bytes() if image.exists() else None
    log_path = log_dir / f'{name}.log'
    job_started = time.monotonic()
    print(f'开始：{name}（单图上限170秒；不编译论文）', flush=True)
    with log_path.open('w', encoding='utf-8') as stream:
        try:
            process = subprocess.run([sys.executable, '-c', bootstrap, str(script)],
                                     stdout=stream, stderr=subprocess.STDOUT, timeout=170)
            success = process.returncode == 0 and image.is_file()
            state = '完成' if success else f'失败，退出码{process.returncode}'
        except subprocess.TimeoutExpired:
            success = False
            state = '超时：停止本图并保留此前成品'
    if not success:
        failed = True
        if original_image is not None:
            image.write_bytes(original_image)
        elif image.exists():
            image.unlink()
    records.append({'图名': name, '状态': state,
                    '用时_秒': round(time.monotonic() - job_started, 3),
                    '日志': str(log_path.relative_to(root))})
    (log_dir / '逐图执行.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{name}：{state}', flush=True)
(log_dir / '逐图执行.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'本批结束：计划{len(jobs)}张，用时{time.monotonic() - started:.1f}秒；PNG逐张落盘，PDF未编译。')
sys.exit(1 if failed else 0)
PY
