from pathlib import Path
import csv
import json
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '求解/问题2/图片/厚度与折射率存在补偿关系.png'
plt.rcParams['font.sans-serif'] = ['FandolHei', 'Noto Sans CJK SC', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def main():
    profile = ROOT / '求解/问题2/结果/参数剖面.csv'
    rows = list(csv.DictReader(profile.open(encoding='utf-8-sig', newline='')))
    usable = [r for r in rows if r.get('训练标准化损失', '').strip()]
    # 上游当前正式剖面文件的损失列为空，禁止拿升格变体或人为插值冒充正式剖面。
    if len(usable) < 12:
        raise RuntimeError('图11暂不生成：正式参数剖面.csv的训练标准化损失列为空，需上游补写真实剖面数值后再画等高线。')
    d = np.array([float(r['厚度_um']) for r in usable])
    n = np.array([float(r['参考折射率']) for r in usable])
    loss = np.array([float(r['训练标准化损失']) for r in usable])
    fig, ax = plt.subplots(figsize=(8.6, 6.4))
    tri = ax.tricontourf(d, n, loss, levels=14, cmap='viridis')
    lines = ax.tricontour(d, n, loss, levels=8, colors='white', linewidths=0.7, alpha=0.75)
    ax.clabel(lines, inline=True, fontsize=10, fmt='%.2f')
    cb = fig.colorbar(tri, ax=ax, pad=0.02); cb.set_label('训练标准化损失', fontsize=12)
    result = json.loads((ROOT / '求解/问题2/结果/厚度结果.json').read_text(encoding='utf-8'))
    best_d = float(result['同片最佳厚度_微米']); best_n = float(result['同片最佳厚度的参考折射率'])
    ax.add_line(Line2D([best_d], [best_n], marker='*', linestyle='None', markersize=12,
                       color='#D95F02', markerfacecolor='#D95F02', markeredgecolor='white',
                       markeredgewidth=0.8, zorder=5,
                       label=f'主点 ({best_d:.2f}, {best_n:.2f})'))
    near = result['条件区间组成'].get('五折条件厚度_微米', [])
    # 只标结果中真实给出的五折厚度；折射率未知时不伪造坐标，采用底部带状标识。
    ax.axvspan(min(near), max(near), color='#D95F02', alpha=0.08, label='五折厚度范围')
    ax.axvline(min(d), color='#444444', ls=':', lw=1.2); ax.axvline(max(d), color='#444444', ls=':', lw=1.2)
    ax.text(min(d), ax.get_ylim()[1], f'搜索边界 d={min(d):g}', ha='left', va='top', fontsize=10)
    ax.text(max(d), ax.get_ylim()[1], f'd={max(d):g}', ha='right', va='top', fontsize=10)
    ax.set_xlabel('厚度（μm）', fontsize=12); ax.set_ylabel('参考折射率', fontsize=12)
    ax.legend(frameon=False, fontsize=11, loc='best')
    ax.grid(alpha=0.3, linestyle='--'); ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    fig.text(0.5, 0.02, '等高线是其余参数重估后的训练损失剖面；星标为全量主点，阴影为五折厚度范围',
             ha='center', fontsize=11)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    OUT.parent.mkdir(parents=True, exist_ok=True); fig.savefig(OUT, dpi=200, bbox_inches='tight')
    print(f'图11 已写出：剖面点 {len(usable)} 个；主点 d={best_d:.6g} μm, n={best_n:.6g}')


if __name__ == '__main__':
    main()
