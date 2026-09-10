from pathlib import Path
import json
import numpy as np
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '求解/问题2/图片/预测覆盖揭示区间失配.png'
plt.rcParams['font.sans-serif'] = ['FandolHei', 'Noto Sans CJK SC', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def main():
    v = json.loads((ROOT / '求解/问题2/结果/验证.json').read_text(encoding='utf-8'))
    blocks = v['分块']
    nominal = float(v['经验覆盖率']['校准经验分位点'])
    x = []; y = []; width = []; labels = []
    for b in blocks:
        lo = np.asarray(b['区间下界_比例'], dtype=float)
        hi = np.asarray(b['区间上界_比例'], dtype=float)
        obs = np.asarray(b['观测反射率_比例'], dtype=float)
        covered = (obs >= lo) & (obs <= hi)
        x.append(nominal); y.append(float(covered.mean())); width.append(float(np.mean(hi - lo)))
        labels.append(f'{int(b["角度_度"])}°块{b["测试块"]}\n{int(covered.sum())}/{len(obs)}')
    fig, ax = plt.subplots(figsize=(8.7, 6.6))
    sc = ax.scatter(x, y, c=width, s=150, cmap='plasma', edgecolor='white', linewidth=0.9, zorder=4)
    for xx, yy, lab in zip(x, y, labels):
        ax.annotate(lab, (xx, yy), xytext=(7, 7), textcoords='offset points', fontsize=10)
    agg = float(v['经验覆盖率']['测试经验覆盖率'])
    ax.scatter([nominal - 0.012], [agg], marker='D', s=82, color='#222222', zorder=5, label=f'正式汇总 {agg:.2%}')
    proto = json.loads((ROOT / '求解/问题2/原型结果/路线1.json').read_text(encoding='utf-8'))
    proto_cov = float(proto['经验覆盖率']['测试经验覆盖率'])
    ax.scatter([nominal + 0.012], [proto_cov], marker='s', s=82, color='#777777', zorder=5, label=f'原型汇总 {proto_cov:.2%}')
    ax.axhline(nominal, color='#555555', ls='--', lw=1.2)
    ax.text(0.98, nominal + 0.015, f'校准经验分位点 {nominal:.0%}', transform=ax.transAxes,
            ha='right', fontsize=11, color='#555555')
    cb = fig.colorbar(sc, ax=ax, pad=0.02); cb.set_label('平均区间宽度（反射率比例）', fontsize=12)
    ax.set_xlim(nominal - 0.08, nominal + 0.08); ax.set_ylim(-0.04, 1.08)
    ax.set_xlabel('名义覆盖基准（校准块经验分位点）', fontsize=12)
    ax.set_ylabel('留出经验覆盖率', fontsize=12)
    ax.legend(frameon=False, fontsize=11, loc='lower left')
    ax.text(0.98, 0.04, f'正式留出：{agg:.2%} = {int(round(agg * 320))}/320 点',
            transform=ax.transAxes, ha='right', fontsize=11, color='#A63603')
    ax.grid(alpha=0.3, linestyle='--'); ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    fig.text(0.5, 0.02, '点标签给出每角每块分子/分母；此覆盖只针对反射率点预测，不等于厚度覆盖',
             ha='center', fontsize=10.5)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    OUT.parent.mkdir(parents=True, exist_ok=True); fig.savefig(OUT, dpi=200, bbox_inches='tight')
    print(f'图14 已写出：8个分块覆盖点；正式汇总 {agg:.6%}；原型汇总 {proto_cov:.6%}')


if __name__ == '__main__':
    main()
