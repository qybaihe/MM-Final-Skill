from pathlib import Path
import csv
import json
import ast
import signal
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, NullFormatter, LogLocator

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "求解/问题1/图片/三路线误差按情景分布.png"
plt.rcParams['font.sans-serif'] = ['FandolHei', 'Noto Sans CJK SC', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['axes.formatter.use_mathtext'] = False
plt.rcParams.update({'mathtext.fontset': 'dejavusans', 'mathtext.default': 'it'})
plt.rcParams['font.size'] = 13


def no_spine(ax):
    ax.grid(alpha=0.3, linestyle='--')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)


def main():
    route_files = [
        ROOT / '求解/问题1/原型结果/路线1.json',
        ROOT / '求解/问题1/原型结果/路线2.json',
        ROOT / '求解/问题1/原型结果/路线3.json',
    ]
    route_names = ['色散峰序反演', '双束界面场反演', '连续相位拟厚']
    rows = []
    for path, name in zip(route_files, route_names):
        data = json.loads(path.read_text(encoding='utf-8'))
        items = data.get('逐例结果') or data.get('合成逐例')
        for case_idx, item in enumerate(items, 1):
            true_value = item.get('合成真厚度_微米', item.get('真厚度_微米'))
            if true_value is None and isinstance(item.get('合成真值'), dict):
                true_value = item['合成真值'].get('真厚度_微米')
            true = float(true_value)
            est = float(item.get('厚度_微米'))
            err = item.get('相对误差_%')
            if err is None:
                err = abs(est - true) / true * 100
            elif float(err) < 1e-12:
                err = abs(est - true) / true * 100
            case_id = item.get('例号', item.get('案例', case_idx))
            if case_id is None:
                case_id = case_idx
            rows.append({'route': name, 'case': int(case_id),
                         'true': true, 'err': float(err), 'status': item.get('状态', '')})
    common = sorted({r['case'] for r in rows})[:24]
    ambiguity = {1}  # 交接/锦标赛核验_问题1.json 的路线三歧义案例
    colors = ['#4477AA', '#CC6677', '#228833']
    with (ROOT / '求解/问题1/结果/合成逐例.csv').open(encoding='utf-8-sig', newline='') as stream:
        qrows = list(csv.DictReader(stream))
    official = json.loads((OUT.parent.parent / '结果/合成验证.json').read_text(encoding='utf-8'))['分组汇总']
    for group in ('新增匹配', '模型失配'):
        values = [float(row['绝对相对误差_百分比']) for row in qrows if row['类别'] == group]
        if len(values) != official[group]['案例数'] or not np.isclose(
                np.mean(values), official[group]['平均绝对相对误差_百分比'], rtol=1e-12, atol=1e-15):
            raise ValueError(f'{group}逐例数据与正式JSON不一致')
    if len(common) != official['共同匹配']['案例数']:
        raise ValueError('共同案例数与正式JSON不一致')
    if len(rows) != len(route_names) * len(common):
        raise ValueError('三路线逐例点数必须完整，禁止省略案例')
    summary = {'共同案例数': len(common), '歧义案例': sorted(ambiguity),
               '新增匹配数': sum(row['类别'] == '新增匹配' for row in qrows),
               '模型失配数': sum(row['类别'] == '模型失配' for row in qrows),
               '正式分组汇总': official, '逐例误差': rows}
    (OUT.parent.parent / '结果/图核验_三路线误差按情景分布.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    fig = plt.figure(figsize=(8.0, 5.74))
    ax = fig.add_axes((0.19, 0.49, 0.785, 0.36))
    bx = fig.add_axes((0.32, 0.16, 0.655, 0.18))
    offsets = [-0.22, 0, 0.22]
    for j, name in enumerate(route_names):
        vals = [next(r['err'] for r in rows if r['route'] == name and r['case'] == c)
                for c in common]
        y = np.arange(len(common)) + offsets[j]
        ax.add_line(Line2D(y, vals, linestyle='None', marker='o', markersize=5.5,
                           color=colors[j], markerfacecolor=colors[j],
                           markeredgecolor='white', markeredgewidth=0.5,
                           label=name, zorder=3))
        if j:
            prev = [next(r['err'] for r in rows if r['route'] == route_names[j-1] and r['case'] == c)
                    for c in common]
            segments = [[(yy + offsets[j-1], x0), (yy + offsets[j], x1)]
                        for yy, x0, x1 in zip(np.arange(len(common)), prev, vals)]
            ax.add_collection(LineCollection(segments, colors='#999999', linewidths=1.5,
                                             alpha=0.35, zorder=1))
        if name == '连续相位拟厚':
            idx = common.index(1)
            ax.add_line(Line2D([idx + offsets[j]], [vals[idx]], linestyle='None', marker='o',
                               markersize=10.5, markerfacecolor='none',
                               markeredgecolor='#AA3377', markeredgewidth=1.8, zorder=4))
            ax.annotate('例1相位歧义', (idx + offsets[j], vals[idx]),
                        xytext=(10, 12), textcoords='offset points', fontsize=11,
                        arrowprops={'arrowstyle': '->', 'lw': 1.5, 'color': '#AA3377'})
    ax.set_xticks(np.arange(len(common)))
    ax.set_xticklabels([f'{case:02d}' for case in common], fontsize=11)
    for true_value in sorted({row['true'] for row in rows}):
        indices = [index for index, case in enumerate(common)
                   if next(row['true'] for row in rows if row['case'] == case) == true_value]
        ax.axvspan(min(indices) - 0.45, max(indices) + 0.45, color='#e2e8f0', alpha=0.22, zorder=0)
        ax.text(np.mean(indices), 1.025, f'真厚度 {true_value:g} ' + r'$\mu\mathrm{m}$',
                transform=ax.get_xaxis_transform(), ha='center', fontsize=12)
    ax.set_xlabel(f'(a) 三路线共同{len(common)}例：案例编号', fontsize=13)
    ax.set_ylabel('厚度绝对相对误差（%）', fontsize=13, labelpad=8)
    ax.set_yscale('log'); ax.set_xlim(-0.7, len(common)-0.3); ax.autoscale_view(scalex=False)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, position: f'{value:.0e}' if value > 0 else ''))
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.yaxis.set_major_locator(LogLocator(base=10, numticks=5))
    ax.axhline(0.001, color='#555555', ls=':', lw=1.5)
    ax.text(0.98, 0.001, '参考 0.001%', transform=ax.get_yaxis_transform(),
            fontsize=11, color='#555555', ha='right', va='bottom')
    fig.legend(*ax.get_legend_handles_labels(), frameon=False, fontsize=13, loc='upper center', ncol=3)
    no_spine(ax)
    ax.grid(False, axis='x')
    for boundary in (7.5, 15.5):
        ax.axvline(boundary, color='#94a3b8', lw=1.5, zorder=0)

    groups = [('新增匹配', '#66A61E'), ('模型失配', '#E6AB02')]
    positions = [1, 0]
    for (group, color), yp in zip(groups, positions):
        vals = [float(r['绝对相对误差_百分比']) for r in qrows if r['类别'] == group]
        bx.add_line(Line2D(vals, yp + np.linspace(-0.13, 0.13, len(vals)), linestyle='None', marker='o',
                           markersize=5.8, color=color, markerfacecolor=color,
                           markeredgecolor='white', markeredgewidth=0.5, alpha=0.82,
                           zorder=3))
        mean = official[group]['平均绝对相对误差_百分比']
        bx.add_line(Line2D([mean, mean], [yp - 0.25, yp + 0.25],
                          color=color, linewidth=2.0, zorder=4))
    bx.set_yticks([0, 1])
    bx.set_yticklabels([f'{group}（{official[group]["案例数"]}例）\n'
                       f'均值 {official[group]["平均绝对相对误差_百分比"]:.4g}%'
                       for group in ('模型失配', '新增匹配')], fontsize=12)
    bx.tick_params(axis='y', length=0, pad=10)
    bx.set_xlabel('(b) 双束界面场反演误差（%）', fontsize=13, labelpad=3)
    bx.set_ylim(-0.5, 1.5)
    bx.set_xscale('log')
    bx.xaxis.set_major_formatter(FuncFormatter(lambda value, position: f'{value:.0e}' if value > 0 else ''))
    bx.xaxis.set_minor_formatter(NullFormatter())
    bx.xaxis.set_major_locator(LogLocator(base=10, numticks=4))
    bx.axvline(0.001, color='#555555', ls=':', lw=1.5)
    no_spine(bx)
    fig.text(0.5, 0.025, '点＝案例；短线＝组均值；错开防重叠；1e-03＝0.001%',
             ha='center', fontsize=12)
    fig.canvas.draw()
    for axis in (ax.yaxis, bx.xaxis):
        if any('\u2212' in label.get_text() or '$' in label.get_text()
               for label in axis.get_majorticklabels() + axis.get_minorticklabels()):
            raise ValueError('图5对数刻度必须保留可见ASCII负号，不接受数学负号替代')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=200, bbox_inches='tight')
    print(f'三路线误差图已写出：共同案例 {len(common)} 例；新增匹配 {sum(r["类别"] == "新增匹配" for r in qrows)} 例；模型失配 {sum(r["类别"] == "模型失配" for r in qrows)} 例')


if __name__ == '__main__':
    signal.alarm(150)
    main()
