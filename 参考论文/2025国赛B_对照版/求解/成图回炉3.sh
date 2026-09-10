#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export MPLBACKEND=Agg
export MPLCONFIGDIR="$ROOT/日志/回炉图_轮3运行/matplotlib"
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$MPLCONFIGDIR"

python3 - <<'PY'
import hashlib
import json
import math
import re
import runpy
import signal
import subprocess
import sys
import time
from pathlib import Path

root = Path.cwd()
started = time.monotonic()
deadline = started + 740
job_timeout = 80
signal.alarm(760)
from PIL import Image
log_dir = root / '日志/回炉图_轮3运行'
ready = log_dir / '成图状态.json'
records = []
ready.write_text(json.dumps({'状态': '未完成，禁止据此编译', '逐图': []}, ensure_ascii=False), encoding='utf-8')
verifier = root / '求解/公共/核验_回炉图_轮3.py'
check = subprocess.run([sys.executable, str(verifier)], capture_output=True, text=True, timeout=60)
(log_dir / '数据预检.log').write_text(check.stderr + check.stdout, encoding='utf-8')
if check.returncode:
    raise SystemExit('数据预检失败，未重绘；见数据预检.log')
evidence = json.loads(check.stdout)
(log_dir / '数据预检.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
jobs = [
    ('公共', '全文思路图'),
    ('问题1', '三路线误差按情景分布'),
    ('问题2', '双角拟合残差显示局部偏差'),
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
signal.alarm(75)
import matplotlib as mpl
from matplotlib import font_manager
font_path = subprocess.check_output(['kpsewhich', 'FandolHei-Regular.otf'], text=True, timeout=10).strip()
if not font_path or not Path(font_path).is_file():
    raise RuntimeError('缺少FandolHei字体文件')
font_manager.fontManager.addfont(font_path)
mpl.rcParams.update({'mathtext.fontset': 'stix', 'mathtext.default': 'it',
                     'mathtext.fallback': 'stix', 'axes.formatter.use_mathtext': False})
os.chdir(Path(sys.argv[1]).parent)
if Path('绘图数据_当前.py').is_file():
    import 绘图数据_当前 as plot_data
    original_metadata = plot_data.figure_metadata
    metadata_keys = {'图名': 'Title', '图核验': 'Verification', '绘图脚本SHA256': 'PlotScriptSHA256'}
    def png_metadata(name):
        return {metadata_keys.get(key, key): value for key, value in original_metadata(name).items()}
    plot_data.figure_metadata = png_metadata
runpy.run_path(sys.argv[1], run_name='__main__')
'''
glyph_error = re.compile(r'Glyph .*missing|does not have a glyph|dummy symbol|Missing character|U\+2212.*(?:missing|substitut)', re.IGNORECASE)
failed = False
for problem, name in jobs:
    if deadline - time.monotonic() < job_timeout + 15:
        records.append({'图名': name, '状态': '预算不足，保留已完成图，禁止编译'})
        failed = True
        break
    script = root / '求解' / problem / f'绘图_{name}.py'
    image = script.parent / '图片' / f'{name}.png'
    original = image.read_bytes() if image.exists() else None
    original_mtime = image.stat().st_mtime_ns if image.exists() else 0
    log_path = log_dir / f'{name}.log'
    job_started = time.monotonic()
    success = False
    warning_lines = []
    reason = ''
    print(f'开始：{name}；单图{job_timeout}秒上限', flush=True)
    try:
        with log_path.open('w', encoding='utf-8') as stream:
            process = subprocess.run([sys.executable, '-c', bootstrap, str(script)],
                                     stdout=stream, stderr=subprocess.STDOUT, timeout=job_timeout)
        warning_lines = [line for line in log_path.read_text(encoding='utf-8', errors='replace').splitlines()
                         if glyph_error.search(line)]
        if process.returncode or warning_lines:
            raise ValueError(f'退出码{process.returncode}，缺字/替代警告{len(warning_lines)}条')
        if not image.is_file() or image.stat().st_mtime_ns <= original_mtime:
            raise ValueError('未生成本轮新PNG')
        with Image.open(image) as rendered:
            rendered.load()
            if min(rendered.size) < 500:
                raise ValueError('PNG尺寸异常')
            if name in ('硅双角谱检验高阶贡献', '逐块对照区分两材料收益', '两材料厚度汇总保留条件'):
                summary = json.loads(rendered.info['Verification'])
                if rendered.info['PlotScriptSHA256'] != hashlib.sha256(script.read_bytes()).hexdigest():
                    raise ValueError('PNG不属于当前绘图脚本')
                if summary != json.loads((script.parent / '结果' / f'图核验_{name}.json').read_text(encoding='utf-8')):
                    raise ValueError('PNG内嵌数据与图核验摘要不同版')
                for source, expected in summary['数据源摘要'].items():
                    if hashlib.sha256((script.parent / '结果' / source).read_bytes()).hexdigest() != expected:
                        raise ValueError('PNG来源散列失配')
                if name == '硅双角谱检验高阶贡献':
                    for angle, values in evidence['图11逐角结果'].items():
                        for key, expected in values.items():
                            if not math.isclose(summary['逐角结果'][angle][key], expected, rel_tol=1e-10, abs_tol=1e-12):
                                raise ValueError('图11内嵌角块误差不匹配')
                elif name == '逐块对照区分两材料收益':
                    if summary['分材料'] != evidence['图12分材料']:
                        raise ValueError('图12改善计数不匹配')
                elif summary['核心指标'] != evidence['核心指标']:
                    raise ValueError('图13厚度版本不匹配')
        success = True
    except Exception as error:
        reason = f'{type(error).__name__}: {error}'
    if not success:
        failed = True
        if original is not None:
            image.write_bytes(original)
        elif image.exists():
            image.unlink()
    records.append({'图名': name, '状态': '新PNG生成，待整页图评' if success else '失败，旧PNG不得作为本轮成图',
                    '原因': reason, '缺字警告': warning_lines, '缺字警告数': len(warning_lines),
                    '用时_秒': round(time.monotonic() - job_started, 3), '日志': str(log_path.relative_to(root)),
                    '新图SHA256': hashlib.sha256(image.read_bytes()).hexdigest() if success else None})
    (log_dir / '逐图执行.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{name}：{records[-1]["状态"]} {reason}', flush=True)
    if failed:
        break
changed_sources = [source for source, expected in evidence['数据源摘要'].items()
                   if hashlib.sha256((root / source).read_bytes()).hexdigest() != expected]
failed = failed or bool(changed_sources) or len(records) != len(jobs)
ready.write_text(json.dumps({'状态': '失败，禁止编译旧图' if failed else '八图已重绘，待统一编译与整页验收',
                             '数据变化': changed_sources, '逐图': records, 'PDF已编译': False,
                             '用时_秒': round(time.monotonic() - started, 3)}, ensure_ascii=False, indent=2), encoding='utf-8')
print('本批仅重绘图1/5/8/10/11/12/13/18；未执行求解器，未编译PDF。')
sys.exit(1 if failed else 0)
PY
