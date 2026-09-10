from pathlib import Path
import hashlib
import json
import os
import signal
import time


ROOT = Path(__file__).resolve().parents[1]
BATCH = os.environ.get('G5_FIGURE_BATCH', '1')
if BATCH not in ('1', '2'):
    raise ValueError('未知成图批次，拒绝沿用其他批次交接')
REPORT = ROOT / ('日志/G5返工图1再交接' if BATCH == '1' else '日志/G5返工图2成图') / 'PDF图版核验.json'
HANDOFF = ROOT / f'交接/G5返工图{BATCH}_编译交接.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pixel_signature(image):
    rgb = image.convert('RGB')
    return rgb.size, hashlib.sha256(rgb.tobytes()).hexdigest()


def budget_expired(signum, frame):
    raise TimeoutError('核验总预算120秒耗尽')


def check_mathtext(figure, plot_name=''):
    import matplotlib as mpl
    from matplotlib.font_manager import FontProperties
    from matplotlib.mathtext import MathTextParser
    from matplotlib.text import Text

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
            if any(not font.get_char_index(code) for font, size, code, offset_x, offset_y in proof.glyphs):
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


def write_handoff(evidence, records):
    handoff = {'状态': '待统一编译；不是出版验收通过', 'PDF': '论文/论文.pdf', '图版': records,
               '正式结果SHA256': evidence['正式结果SHA256'], 'PDF已编译': False,
               '来源摘要': evidence['来源摘要'],
               '编译后核验命令': f'G5_FIGURE_BATCH={BATCH} python3 求解/核验G5图版嵌入.py',
               '整页验收': ['图5逐例对象、单位与分组', '图10每次乘u及求和关系',
                            '图11误差0.4281→0.3000及1.0923→0.8759', '图12两材料各4/8',
                            '图13硅包络1.72—18.16微米', '正文页数不超过20；另由图评确认可读性']}
    target = HANDOFF
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(handoff, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(target)


def main():
    started = time.monotonic()
    report = {'状态': '未通过', 'PDF已编译': '未核验', '整页可读性': '待图评', '正文页数': '待整页核对，不得超过20'}
    try:
        from PIL import Image
        from pypdf import PdfReader

        handoff_path = HANDOFF
        handoff_hash = digest(handoff_path)
        handoff = json.loads(handoff_path.read_text(encoding='utf-8'))
        if handoff['状态'] != '待统一编译；不是出版验收通过' or len(handoff['图版']) != 5:
            raise ValueError('五图成图交接未完成，不能核验旧PDF')
        state = json.loads((REPORT.parent / '成图状态.json').read_text(encoding='utf-8'))
        if state['状态'] != '五图检查通过（重绘5、复用0）；待统一编译及整页图评' or state['逐图'] != handoff['图版']:
            raise ValueError('本轮成图失败或尚未完成；不得沿用前次编译交接')
        if any(digest(Path(source)) != expected_hash for source, expected_hash in handoff['来源摘要'].items()):
            raise ValueError('成图后正式结果或原始数据改变；需重新交接')
        expected_paths = {
            '求解/问题1/图片/三路线误差按情景分布.png',
            *(f'求解/问题3/图片/{name}.png' for name in
              ('多次往返按复振幅递减', '硅双角谱检验高阶贡献',
               '逐块对照区分两材料收益', '两材料厚度汇总保留条件')),
        }
        if {row['PNG路径'] for row in handoff['图版']} != expected_paths:
            raise ValueError('交接图版不恰好覆盖图5、10、11、12、13')
        expected = {}
        for row in handoff['图版']:
            path = ROOT / row['PNG路径']
            if digest(path) != row['PNG_SHA256']:
                raise ValueError(f'编译交接后PNG发生变化：{path.name}')
            with Image.open(path) as image:
                expected[row['图名']] = pixel_signature(image)
        pdf = ROOT / handoff['PDF']
        pdf_hash = digest(pdf)
        reader = PdfReader(pdf)
        matches = {name: [] for name in expected}
        for page_number, page in enumerate(reader.pages, 1):
            if time.monotonic() - started > 100:
                raise TimeoutError('PDF图版核验达到100秒预算')
            for image in page.images:
                signature = pixel_signature(image.image)
                for name, expected_signature in expected.items():
                    if signature == expected_signature:
                        matches[name].append(page_number)
        report.update({'PDF_SHA256': pdf_hash, 'PDF物理总页数': len(reader.pages), '图版所在物理页': matches})
        missing = [name for name, pages in matches.items() if not pages]
        if missing:
            raise ValueError(f'正式PDF未嵌入本轮图版：{missing}；不能用编译退出码或PNG时间替代')
        if digest(pdf) != pdf_hash or digest(handoff_path) != handoff_hash or any(
                digest(ROOT / row['PNG路径']) != row['PNG_SHA256'] for row in handoff['图版']):
            raise ValueError('核验过程中PDF、PNG或交接发生变化')
        if any(digest(Path(source)) != expected_hash for source, expected_hash in handoff['来源摘要'].items()):
            raise ValueError('PDF核验过程中正式数据发生变化')
        report.update({'状态': '五图像素匹配通过；不是整页图评通过', 'PDF已编译': True})
    except Exception as error:
        report['原因'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        report['用时_秒'] = round(time.monotonic() - started, 3)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        import fcntl
        with (ROOT / '交接/.实验记录.lock').open('a') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            events_path = ROOT / '交接/实验记录.json'
            events = json.loads(events_path.read_text(encoding='utf-8'))
            events.append({'类别': '绘图尝试', '问题': 0, '尝试': 'G5编译后五图PNG与PDF内嵌像素核对',
                           '现象': report['状态'] + '；' + report.get('原因', ''),
                           '决定': '像素匹配不替代整页图评与正文页数验收', '依据': str(REPORT.relative_to(ROOT))})
            temporary = events_path.with_suffix('.G5嵌图.tmp')
            temporary.write_text(json.dumps(events, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temporary.replace(events_path)
        print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    signal.signal(signal.SIGALRM, budget_expired)
    signal.alarm(120)
    main()
