from pathlib import Path
import json
import signal
import numpy as np
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '求解/问题2/图片/留角度预测核对共享厚度.png'
plt.rcParams['font.sans-serif'] = ['FandolHei', 'Noto Sans CJK SC', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['axes.formatter.use_mathtext'] = True
plt.rcParams['font.size'] = 13


def main():
    data = json.loads((ROOT / '求解/问题2/结果/验证.json').read_text(encoding='utf-8'))
    diag = data['双向留角度冻结诊断']
    if len(diag) != 8 or not all(row['来源相位冻结'] and row['目标角度仅重估有限基线幅值'] for row in diag):
        raise ValueError('跨角响应核对必须保留原八条有限响应适配记录')
    (OUT.parent.parent / '结果/图核验_留角度预测核对共享厚度.json').write_text(
        json.dumps({'参数来源': '双角训练联合拟合', '误差记录': diag}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 5.7), sharey=True)
    pairs = [(10.0, 15.0), (15.0, 10.0)]
    colors = ['#4477AA', '#CC6677']
    for ax, (source, target), color in zip(axes, pairs, colors):
        pts = [r for r in diag if r['来源角度_度'] == source and r['被预测角度_度'] == target]
        x = np.arange(1, len(pts) + 1)
        y = np.array([float(r['标准化均方根误差']) for r in pts])
        ax.scatter(x, y, s=62, color=color, edgecolor='white', linewidth=0.8, zorder=3)
        for xx, yy, p in zip(x, y, pts):
            ax.annotate(f'折{p["折号"]}块{p["测试块"]}\n{yy:.4f}', (xx, yy),
                        xytext=(0, 9), textcoords='offset points', ha='center', fontsize=12)
        ax.axhline(np.median(y), color='#555555', ls='--', lw=1.5)
        ax.text(0.04, 0.94, f'中位数 {np.median(y):.3f}', transform=ax.transAxes, fontsize=11)
        ax.set_xticks(x); ax.set_xticklabels([f'记录{i}' for i in x], fontsize=11)
        ax.set_xlabel(f'{"(a)" if source == 10.0 else "(b)"} 相位 {int(source)}° → {int(target)}°', fontsize=13)
        ax.set_xlim(0.55, len(pts) + 0.45)
        ax.set_ylim(0, 4.25)
        ax.grid(alpha=0.3, linestyle='--'); ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    axes[0].set_ylabel('冻结共享参数的标准化均方根误差', fontsize=12)
    fig.text(0.5, 0.94, '共享厚度与折射参数：来自双角训练联合拟合，随后固定',
             ha='center', fontsize=13, bbox=dict(facecolor='#eef2f6', edgecolor='none', pad=6))
    fig.text(0.26, 0.065, '来源角度\n只冻结该角相位', ha='center', fontsize=12, linespacing=1.5)
    fig.text(0.73, 0.065, '目标角度\n重估有限基线与幅值，预测留出波段', ha='center', fontsize=12, linespacing=1.5)
    fig.tight_layout(rect=(0, 0.19, 1, 0.88))
    OUT.parent.mkdir(parents=True, exist_ok=True); fig.savefig(OUT, dpi=200, bbox_inches='tight')
    print(f'跨角响应图已写出：10°→15° {sum(r["来源角度_度"] == 10.0 for r in diag)} 条、15°→10° {sum(r["来源角度_度"] == 15.0 for r in diag)} 条冻结诊断')


if __name__ == '__main__':
    signal.alarm(150)
    main()
