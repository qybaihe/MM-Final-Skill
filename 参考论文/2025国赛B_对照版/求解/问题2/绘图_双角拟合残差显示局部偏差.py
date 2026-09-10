from pathlib import Path
import csv
import json
from collections import defaultdict
import itertools
import signal
import hashlib
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '求解/问题2/图片/双角拟合残差显示局部偏差.png'
plt.rcParams['font.sans-serif'] = ['FandolHei', 'Noto Sans CJK SC', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['axes.formatter.use_mathtext'] = False
plt.rcParams.update({'mathtext.fontset': 'stix', 'mathtext.default': 'it'})
plt.rcParams['font.size'] = 13


def style(ax):
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, position: f'{value:g}'))
    ax.grid(alpha=0.3, linestyle='--')
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)


def main():
    rows = list(csv.DictReader((ROOT / '求解/问题2/结果/留段预测.csv').open(
        encoding='utf-8-sig', newline='')))
    rows = [r for r in rows if r['类型'] == '主方法测试块']
    residual_gap = max(abs(float(row['残差_比例']) -
                           (float(row['观测反射率_比例']) - float(row['预测反射率_比例'])))
                       for row in rows)
    if len(rows) != 320 or residual_gap != 0.0:
        raise ValueError('正式320点必须逐点满足残差=实测−预测；禁止反转曲线')
    extreme = min(rows, key=lambda row: float(row['残差_比例']))
    by_angle = defaultdict(list)
    for r in rows:
        by_angle[float(r['角度_度'])].append(r)
    summary = {'测试点数': len(rows), '残差定义': '实测减预测', '逐点最大差': residual_gap,
               '预测CSV_SHA256': hashlib.sha256((ROOT / '求解/问题2/结果/留段预测.csv').read_bytes()).hexdigest(),
               '逐点核验': [{'角度_度': float(row['角度_度']), '波数_cm^-1': float(row['波数_cm^-1']),
                             '残差_比例': float(row['残差_比例']),
                             '实测减预测_比例': float(row['观测反射率_比例']) - float(row['预测反射率_比例'])}
                            for row in rows],
               '极值波数_cm^-1': float(extreme['波数_cm^-1']),
               '极值残差_百分点': 100 * float(extreme['残差_比例']),
               '极值实测_百分比': 100 * float(extreme['观测反射率_比例']),
               '极值预测_百分比': 100 * float(extreme['预测反射率_比例'])}
    (OUT.parent.parent / '结果/图核验_双角拟合残差显示局部偏差.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    fig, axes = plt.subplots(2, 2, figsize=(9.2, 6.2), sharex='col', sharey='row')
    all_upper = []
    all_residuals = []
    for col, angle in enumerate([10.0, 15.0]):
        part = sorted(by_angle[angle], key=lambda r: (int(r['折号']), int(r['测试块']), float(r['波数_cm^-1'])))
        groups = []
        for key, group_iter in itertools.groupby(
                part, key=lambda r: (r['折号'], r['测试块'])):
            group = list(group_iter)
            groups.append((key, group))
        for _, group in groups:
            all_upper.extend(float(r['观测反射率_比例']) * 100 for r in group)
            all_upper.extend(float(r['预测反射率_比例']) * 100 for r in group)
            all_residuals.extend(float(r['残差_比例']) * 100 for r in group)

        for group_index, ((fold, block), group) in enumerate(groups):
            x = np.array([float(r['波数_cm^-1']) for r in group])
            obs = np.array([float(r['观测反射率_比例']) for r in group]) * 100
            pred = np.array([float(r['预测反射率_比例']) for r in group]) * 100
            res = np.array([float(r['残差_比例']) for r in group]) * 100
            label_obs = '实测' if group_index == 0 else None
            label_pred = '冻结预测' if group_index == 0 else None
            axes[0, col].plot(x, obs, color='#222222', lw=1.6, label=label_obs)
            axes[0, col].plot(x, pred, color='#4477AA', lw=1.6, label=label_pred)
            axes[1, col].plot(x, res, color='#CC6677', lw=1.5)
            left, right = x[0], x[-1]
            if len(x) > 1:
                half_step = 0.5 * float(np.median(np.diff(x)))
            else:
                half_step = 1.0
            for axis in (axes[0, col], axes[1, col]):
                axis.axvspan(left - half_step, right + half_step,
                             color='#F6BD60', alpha=0.12, lw=0, zorder=0)
                axis.axvline(left - half_step, color='#999999', lw=1.5, ls=':')
                axis.axvline(right + half_step, color='#999999', lw=1.5, ls=':')
            axes[0, col].text((left + right) / 2, 0.98 - 0.065 * (group_index % 2), f'折{fold}块{block}',
                              transform=axes[0, col].get_xaxis_transform(),
                              ha='center', va='top', fontsize=11, color='#6b4f1d')

        axes[1, col].axhline(0, color='#555555', lw=1.5)
        axes[1, col].text(0.02, 1.04, f'({"c" if col == 0 else "d"}) 正：低估；负：高估',
                          transform=axes[1, col].transAxes, va='bottom', fontsize=12, color='#475569')
        axes[0, col].text(0.02, 1.04, f'({"a" if col == 0 else "b"}) {int(angle)}°', transform=axes[0, col].transAxes,
                          fontsize=12, va='bottom')
        axes[0, col].set_ylabel('反射率（%）', fontsize=12)
        axes[1, col].set_ylabel('残差：实测-预测\n（百分点）', fontsize=13)
        axes[1, col].set_xlabel('波数（cm^-1）', fontsize=12)
        style(axes[0, col]); style(axes[1, col])
        axes[0, col].annotate(f'测试点 {len(part)} 个', xy=(0.98, 1.04), xycoords='axes fraction', ha='right', fontsize=11)

    upper_low = float(np.min(all_upper))
    upper_high = float(np.max(all_upper))
    upper_span = upper_high - upper_low
    upper_margin = 0.16 * (upper_span if upper_span > 0 else max(abs(upper_high), 1.0))
    upper_ylim = (upper_low - 0.02 * upper_span, upper_high + upper_margin)
    residual_limit = 1.1 * float(np.max(np.abs(all_residuals)))
    residual_limit = residual_limit if residual_limit > 0 else 1.0
    axes[0, 0].set_ylim(*upper_ylim)
    axes[0, 1].set_ylim(*upper_ylim)
    axes[1, 0].set_ylim(-residual_limit, residual_limit)
    axes[1, 1].set_ylim(-residual_limit, residual_limit)
    axes[1, 0].scatter([float(extreme['波数_cm^-1'])], [100 * float(extreme['残差_比例'])],
                       color='#AA3377', s=36, zorder=5)
    fig.text(0.5, 0.115, '残差＝实测-预测；负：预测高估；正：预测低估',
             ha='center', fontsize=13)
    fig.text(0.5, 0.025,
             f"{float(extreme['角度_度']):g}°、{float(extreme['波数_cm^-1']):.3f} cm^-1：预测高估\n"
             f"实测 {100 * float(extreme['观测反射率_比例']):.4f}% - 预测 {100 * float(extreme['预测反射率_比例']):.4f}%"
             + f" = {100 * float(extreme['残差_比例']):.4f} 个百分点",
             ha='center', fontsize=12, linespacing=1.4)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', ncol=2, frameon=False)
    fig.tight_layout(rect=(0, 0.23, 1, 0.94), h_pad=1.6)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=200, bbox_inches='tight', metadata={
        'Verification': json.dumps(summary, ensure_ascii=False, allow_nan=False),
        'PlotScriptSHA256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    print(f'图8 已写出：320点残差=实测−预测，逐点最大差{residual_gap}；彩色背景为留出测试块')


if __name__ == '__main__':
    signal.alarm(150)
    main()
