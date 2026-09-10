from pathlib import Path
import json
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '求解/问题2/图片/碳化硅厚度随假设变化.png'
plt.rcParams['font.sans-serif'] = ['FandolHei', 'Noto Sans CJK SC', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def main():
    d = json.loads((ROOT / '求解/问题2/结果/厚度结果.json').read_text(encoding='utf-8'))
    best = float(d['同片最佳厚度_微米'])
    cond = [float(x) for x in d['条件区间_微米']]
    two = [float(x) for x in d['两折条件厚度_微米']]
    five = [float(x) for x in d['五折条件厚度_微米']]
    optical = [float(x) for x in d['条件区间组成']['光学剖面厚度范围_微米']]
    sens = d['灵敏度情景搜索完整性']
    svals = [(str(x['情景名称']), float(x['厚度_um'])) for x in sens if x.get('是否纳入条件范围', False)]
    fig, ax = plt.subplots(figsize=(11.5, 6.6))
    y0 = 5
    ax.hlines(y0, cond[0], cond[1], color='#D95F02', lw=6, alpha=0.26,
              label='完整条件范围')
    ax.add_line(Line2D([best], [y0], marker='*', linestyle='None', markersize=13,
                       color='#D95F02', markerfacecolor='#D95F02', markeredgecolor='white',
                       markeredgewidth=0.8, zorder=5))
    ax.annotate(f'非触边代表 {best:.2f}', (best, y0), xytext=(0, 15), textcoords='offset points',
                ha='center', fontsize=11, color='#A63603')
    boundary = json.loads((ROOT / '求解/问题2/结果/验证.json').read_text())['窗口与模型灵敏度'][8]['厚度_um']
    ax.plot(boundary, y0, marker='D', color='#333333', markersize=7)
    ax.annotate(f'触边候选 {boundary:.2f}', (boundary, y0), xytext=(8, -18),
                textcoords='offset points', ha='left', fontsize=10, color='#333333')
    for i, value in enumerate(two):
        ax.add_line(Line2D([value], [4.1], marker='D', linestyle='None', markersize=7,
                           color='#1B9E77', markerfacecolor='#1B9E77', zorder=4))
        ax.annotate(f'{value:.2f}', (value, 4.1), xytext=(0, 8), textcoords='offset points', ha='center', fontsize=10)
    for i, value in enumerate(five, 1):
        fold_y = 3.55 - 0.24 * (i - 1)
        ax.add_line(Line2D([value], [fold_y], marker='o', linestyle='None', markersize=7,
                           color='#7570B3', markerfacecolor='#7570B3', zorder=4))
        ax.annotate(f'折{i}: {value:.2f}', (value, fold_y), xytext=(8, 0),
                    textcoords='offset points', ha='left', va='center', fontsize=10)
    ax.hlines(2.1, optical[0], optical[1], color='#1B9E77', lw=7, alpha=0.35)
    ax.text(np.mean(optical), 2.28, f'光学剖面 [{optical[0]:.3f}, {optical[1]:.3f}]', ha='center', fontsize=11)
    for name, value in svals:
        ax.add_line(Line2D([value], [1.0], marker='o', linestyle='None', markersize=5.8,
                           color='#E7298A', markerfacecolor='#E7298A', alpha=0.82, zorder=4))
    ax.text(np.mean(cond), 0.68, '参数与窗口扰动的厚度重估', ha='center', fontsize=11, color='#9E1B6D')
    ax.set_yticks([1, 2.1, 3.1, 4.1, 5])
    ax.set_yticklabels(['灵敏度', '光学剖面', '五折连续留段', '两折条件', '代表点与重估范围'], fontsize=11)
    ax.set_xlabel('厚度（μm）', fontsize=12)
    ax.set_xlim(max(0, min(cond) - 1), max(cond) + 1); ax.set_ylim(0.35, 5.65)
    ax.axvline(best, color='#D95F02', ls=':', lw=1.5)
    ax.grid(alpha=0.3, linestyle='--'); ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True); fig.savefig(OUT, dpi=200, bbox_inches='tight')
    print(f'图13 已写出：主点 {best:.6g} μm；条件范围 [{cond[0]:.6g}, {cond[1]:.6g}] μm；灵敏度点 {len(svals)} 个')


if __name__ == '__main__':
    main()
