#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export MPLBACKEND=Agg
export G5_FIGURE_BATCH=2
export MPLCONFIGDIR="$ROOT/日志/G5返工图2成图/matplotlib"
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$MPLCONFIGDIR"

python3 - <<'PY'
import csv
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import runpy
import signal
import subprocess
import sys
import time

root = Path.cwd()
started = time.monotonic()
deadline = started + 720
log_dir = root / '日志/G5返工图2成图'
records = []
state_path = log_dir / '成图状态.json'


def save_state(status, **extra):
    state_path.write_text(json.dumps({'状态': status, '逐图': records, 'PDF已编译': False,
                                    '用时_秒': round(time.monotonic() - started, 3),
                                    '实际用时秒': round(time.monotonic() - started, 3),
                                    '总预算_秒': 750, **extra},
                                   ensure_ascii=False, indent=2), encoding='utf-8')


def budget_expired(signum, frame):
    save_state('预算耗尽；保留已完成图，禁止编译旧图')
    raise SystemExit('成图总预算750秒耗尽')


signal.signal(signal.SIGALRM, budget_expired)
signal.alarm(750)


def event(problem, attempt, observation):
    target = root / '交接/实验记录.json'
    with (root / '交接/.实验记录.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        entries = json.loads(target.read_text(encoding='utf-8'))
        entries.append({'类别': '绘图尝试', '问题': problem, '尝试': attempt, '现象': observation,
                        '决定': '图5、10—13全部重绘并逐图缺字核验后交编译；本轮不重算、不改正文',
                        '依据': str(state_path.relative_to(root))})
        temporary = target.with_suffix('.G5图2.tmp')
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(target)


save_state('数据预检中；禁止编译旧图')
preflight = '''
import csv
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import 绘图数据_正式 as publication
from 绘图数据_正式 import publication_data, publication_predictions, SNAPSHOT, MANIFEST, SOURCE, digest
result, comparison, _, _ = publication_data()
if not MANIFEST.is_file():
    raise FileNotFoundError('缺少已核验的正式预测交付；本轮禁止重放或重算')
pairs = publication_predictions(result, comparison)
manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
if any(digest(Path(path)) != expected for path, expected in manifest.get('来源摘要', {}).items()):
    raise ValueError('预测交付的重放来源发生变化')
print(json.dumps({"核心指标": result["核心指标"], "逐块评分": result["逐块评分"],
                  "已核验角块数": len(pairs), "正式结果SHA256": digest(SNAPSHOT),
                  "来源摘要": {**manifest.get('来源摘要', {}), **{str(path): digest(path) for path in
                  [SNAPSHOT, MANIFEST, *(SOURCE / name for name in manifest["文件SHA256"])]}}},
                 ensure_ascii=False))
'''
try:
    check = subprocess.run([sys.executable, '-c', preflight], cwd=root / '求解/问题3',
                           capture_output=True, text=True, timeout=min(120, deadline - time.monotonic()))
    (log_dir / '数据预检.log').write_text(check.stdout + check.stderr, encoding='utf-8')
    if check.returncode:
        raise ValueError('同版预测/64项评分核验失败；见数据预检.log，不重放、不回退旧CSV')
    evidence = json.loads(check.stdout)
    residual_path = root / '求解/问题2/结果/留段预测.csv'
    with residual_path.open(encoding='utf-8-sig', newline='') as stream:
        residuals = [row for row in csv.DictReader(stream) if row['类型'] == '主方法测试块']
    residual_gap = max(abs(float(row['残差_比例']) -
                           (float(row['观测反射率_比例']) - float(row['预测反射率_比例']))) for row in residuals)
    if len(residuals) != 320 or residual_gap != 0:
        raise ValueError('图8必须保留320点实测减预测残差')
    evidence['图8逐点最大差'] = residual_gap
    source_paths = [root / f'求解/问题1/原型结果/路线{number}.json' for number in (1, 2, 3)]
    source_paths += [root / '求解/问题1/结果/合成逐例.csv', root / '求解/问题1/结果/合成验证.json', residual_path]
    source_paths += [root / f'求解/问题3/结果/{name}.json' for name in ('公平对照', '同口径对照', '厚度结果', '灵敏度')]
    evidence['来源摘要'].update({str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths})
    (log_dir / '数据预检.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
except Exception as error:
    save_state('预检失败；未重绘，禁止编译旧图', 原因=str(error))
    event(0, 'G5图2批次成图数据预检', str(error))
    raise SystemExit(str(error))

bootstrap = r'''
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import warnings
import matplotlib as mpl
from matplotlib import font_manager
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.mathtext import MathTextParser
from matplotlib.text import Text


def check_mathtext(figure, plot_name=''):
    figure.set_dpi(200)
    numeric_font = FontProperties(fname=str(Path(mpl.get_data_path()) / 'fonts/ttf/DejaVuSans.ttf'))
    for axis in figure.axes:
        for label in axis.get_xticklabels() + axis.get_yticklabels():
            if label.get_text().isascii():
                numeric_font.set_size(label.get_fontsize())
                label.set_fontproperties(numeric_font)
    for label in figure.findobj(Text):
        label.set_math_fontfamily('dejavusans')
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()
    ignored = {id(label) for axis in figure.axes for coordinate in (axis.xaxis, axis.yaxis)
               if not axis.axison or not coordinate.get_visible() for label in coordinate.findobj(Text)}
    for axis in figure.axes:
        for coordinate in (axis.xaxis, axis.yaxis):
            lower, upper = sorted(coordinate.get_view_interval())
            ignored.update(id(label) for tick in coordinate.get_major_ticks() + coordinate.get_minor_ticks()
                           if not lower <= tick.get_loc() <= upper for label in (tick.label1, tick.label2))
    parser, checked, bounds = MathTextParser('path'), [], []
    for label in figure.findobj(Text):
        if id(label) in ignored or not label.get_visible() or not label.get_text().strip():
            continue
        expression, is_math = label._preprocess_math(label.get_text())
        if is_math:
            proof = parser.parse(expression, dpi=200, prop=label.get_fontproperties())
            if any(not glyph[0].get_char_index(glyph[2]) for glyph in proof.glyphs):
                raise RuntimeError(f'实际图中文字缺字：{expression}')
            checked.append(expression)
        if plot_name in ('三路线误差按情景分布', '多次往返按复振幅递减'):
            extent = label.get_window_extent(renderer)
            if not figure.bbox.contains(extent.x0, extent.y0) or not figure.bbox.contains(extent.x1, extent.y1):
                raise ValueError(f'文字超出固定画布：{label.get_text()}')
            if any(extent.overlaps(previous) for previous in bounds):
                raise ValueError(f'文字相互重叠：{label.get_text()}')
            bounds.append(extent)
    for axis in figure.axes:
        for patch in axis.patches:
            if hasattr(patch, '_label_artist'):
                inner, outer = patch._label_artist.get_window_extent(renderer), patch.get_window_extent(renderer)
                if not outer.contains(inner.x0 - 4, inner.y0 - 4) or not outer.contains(inner.x1 + 4, inner.y1 + 4):
                    raise ValueError('框内文字越界或压线')
    print('实际图中文字逐项字形核验：', checked, flush=True)


script = Path(sys.argv[1]).resolve()
from matplotlib.ft2font import FT2Font
numeric_path = Path(mpl.get_data_path()) / 'fonts/ttf/DejaVuSans.ttf'
if not numeric_path.is_file() or not all(FT2Font(str(numeric_path)).get_char_index(code) for code in (45, 0x2212)):
    raise RuntimeError('数值字体缺少ASCII负号或U+2212，禁止成图')
original_draw = Figure.draw

def checked_draw(figure, renderer):
    numeric_font = FontProperties(fname=str(numeric_path))
    for axis in figure.axes:
        for coordinate in (axis.xaxis, axis.yaxis):
            for label in coordinate.get_majorticklabels() + coordinate.get_minorticklabels():
                if label.get_text().isascii():
                    numeric_font.set_size(label.get_fontsize())
                    label.set_fontproperties(numeric_font)
    for label in figure.findobj(Text):
        label.set_math_fontfamily('dejavusans')
    return original_draw(figure, renderer)

Figure.draw = checked_draw
font_path = subprocess.check_output(['kpsewhich', 'FandolHei-Regular.otf'], text=True, timeout=10).strip()
if not font_path or not Path(font_path).is_file():
    raise RuntimeError('缺少FandolHei文件字体，不准静默替代')
font_manager.fontManager.addfont(font_path)
mpl.rcParams.update({'font.sans-serif': ['FandolHei', 'Noto Sans CJK SC', 'DejaVu Sans'],
                     'axes.unicode_minus': False, 'axes.formatter.use_mathtext': False,
                     'mathtext.fontset': 'dejavusans', 'mathtext.default': 'it', 'mathtext.fallback': 'stix'})
warnings.filterwarnings('error', message=r'.*(?:Glyph.*missing|missing glyph|dummy symb|does not have a glyph).*')
original_savefig = Figure.savefig
def checked_savefig(figure, filename, *args, **kwargs):
    check_mathtext(figure, Path(filename).stem)
    summary = json.loads((script.parent / '结果' / ('图核验_' + Path(filename).stem + '.json')).read_text(encoding='utf-8'))
    metadata = kwargs.setdefault('metadata', {})
    metadata.update({'Verification': json.dumps(summary, ensure_ascii=False, allow_nan=False),
                     'PlotScriptSHA256': hashlib.sha256(script.read_bytes()).hexdigest(),
                     'NumericFontSHA256': hashlib.sha256(numeric_path.read_bytes()).hexdigest(),
                     'MathFontFamily': 'dejavusans'})
    return original_savefig(figure, filename, *args, **kwargs)
Figure.savefig = checked_savefig
runpy.run_path(str(script), run_name='__main__')
'''
jobs = [(1, '三路线误差按情景分布'),
        (3, '多次往返按复振幅递减'), (3, '硅双角谱检验高阶贡献'),
        (3, '逐块对照区分两材料收益'), (3, '两材料厚度汇总保留条件')]
glyph_error = re.compile(r'Glyph.*missing|missing\s+glyph|does not have a glyph|dummy\s+symb|Missing character|U\+2212.*(?:missing|substitut)', re.IGNORECASE)
from PIL import Image
failed = False
for problem, name in jobs:
    if deadline - time.monotonic() < 115:
        failed = True
        records.append({'图名': name, '状态': '预算不足；停止并保留已完成图'})
        save_state('预算不足；禁止编译旧图')
        break
    script = root / f'求解/问题{problem}/绘图_{name}.py'
    image = script.parent / '图片' / f'{name}.png'
    previous = image.read_bytes() if image.exists() else None
    previous_stat = image.stat() if image.exists() else None
    log_path = log_dir / f'{name}.log'
    warnings_found = []
    job_started = time.monotonic()
    record = {'图名': name, '状态': '运行中；尚不可编译', '日志': str(log_path.relative_to(root))}
    records.append(record)
    save_state('逐图重绘中；禁止编译旧图')
    try:
        with log_path.open('w', encoding='utf-8') as stream:
            process = subprocess.run([sys.executable, '-c', bootstrap, str(script)], cwd=script.parent,
                                     stdout=stream, stderr=subprocess.STDOUT, timeout=90)
        returncode = process.returncode
        warnings_found = [line for line in log_path.read_text(encoding='utf-8', errors='replace').splitlines()
                          if glyph_error.search(line)]
        if returncode or warnings_found:
            raise ValueError(f'退出码{returncode}；缺字/替代警告{len(warnings_found)}条')
        if not image.is_file() or (previous_stat and image.stat().st_mtime_ns <= previous_stat.st_mtime_ns):
            raise ValueError('没有本轮新生成的PNG')
        with Image.open(image) as rendered:
            rendered.load()
            summary = json.loads(rendered.info['Verification'])
            if rendered.info.get('MathFontFamily') != 'dejavusans' or not rendered.info.get('NumericFontSHA256'):
                raise ValueError('PNG缺少本轮实际字体链标识')
            if min(rendered.size) < 500 or rendered.info['PlotScriptSHA256'] != hashlib.sha256(script.read_bytes()).hexdigest():
                raise ValueError('图像尺寸或脚本散列不合格')
            stored = json.loads((script.parent / '结果' / f'图核验_{name}.json').read_text(encoding='utf-8'))
            if stored != summary:
                raise ValueError('PNG内嵌摘要与本轮计算摘要不同版')
            if name == '三路线误差按情景分布' and (len(summary['逐例误差']) != 72 or summary['正式分组汇总'] != json.loads(
                    (root / '求解/问题1/结果/合成验证.json').read_text(encoding='utf-8'))['分组汇总']):
                raise ValueError('图5内嵌统计与正式分组汇总不符')
            if name == '多次往返按复振幅递减' and summary['最大往返乘子模'] != {
                    f'{case["材料"]}折{case["折号"]}': case['场级数核验']['最大往返乘子模'] for case in json.loads(
                    (root / '求解/问题3/结果/公平对照.json').read_text(encoding='utf-8'))['案例'] if case['折号'] in (1, 2)}:
                raise ValueError('图10四项往返乘子模与正式JSON不符')
            if name == '双角拟合残差显示局部偏差':
                if summary['测试点数'] != 320 or summary['逐点最大差'] != 0 or any(
                        row['残差_比例'] != row['实测减预测_比例'] for row in summary['逐点核验']):
                    raise ValueError('图8内嵌的320点残差验收失败')
            if name == '硅双角谱检验高阶贡献':
                for angle in (10, 15):
                    for model, label in (('两束', '两束误差_百分点'), ('完整往返', '完整误差_百分点')):
                        score = next(row for row in evidence['逐块评分'] if (row['材料'], row['折号'], row['测试块'], row['入射角_度'], row['模型']) == ('硅', 1, 6, angle, model))
                        if not math.isclose(summary['逐角结果'][f'{angle}度'][label], score['均方根误差_反射率比例'] * 100, rel_tol=1e-10, abs_tol=1e-10):
                            raise ValueError('图11内嵌误差与指定版本评分不符')
            if name == '逐块对照区分两材料收益' and summary['分材料'] != {
                    material: {'角块数': 8, '正向改善数': 4} for material in ('硅', '碳化硅')}:
                raise ValueError('图12必须为两材料各4/8')
            if name == '两材料厚度汇总保留条件' and summary['核心指标'] != evidence['核心指标']:
                raise ValueError('图13厚度版本不符')
        record.update({'状态': '已重绘；待整页图评',
                       'PNG路径': str(image.relative_to(root)),
                       'PNG_SHA256': hashlib.sha256(image.read_bytes()).hexdigest()})
    except Exception as error:
        failed = True
        record.update({'状态': '失败；旧图不能冒充本轮产物', '原因': f'{type(error).__name__}: {error}'})
        if previous is not None:
            image.write_bytes(previous)
            os.utime(image, ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns))
        elif image.exists():
            image.unlink()
    if log_path.exists():
        warnings_found = [line for line in log_path.read_text(encoding='utf-8', errors='replace').splitlines()
                          if glyph_error.search(line)]
    record.update({'缺字警告': warnings_found, '用时_秒': round(time.monotonic() - job_started, 3)})
    save_state('失败；禁止编译旧图' if failed else '逐图重绘中；禁止编译旧图')
    event(problem, f'G5图2重绘{name}', record['状态'] + '；' + record.get('原因', ''))
    print(name, record['状态'], flush=True)
    if failed:
        break
changed = [source for source, expected in evidence['来源摘要'].items()
           if not Path(source).is_file() or hashlib.sha256(Path(source).read_bytes()).hexdigest() != expected]
failed = failed or bool(changed) or len(records) != len(jobs)
save_state('失败；禁止编译旧图' if failed else '五图检查通过（重绘5、复用0）；待统一编译及整页图评', 数据变化=changed)
if not failed:
    runpy.run_path(str(root / '求解/核验G5图版嵌入.py'))['write_handoff'](evidence, records)
print('仅重绘受影响的图5、10—13；图8不动；缺字即失败、不重算、不编译PDF；总预算750秒。')
sys.exit(1 if failed else 0)
PY
