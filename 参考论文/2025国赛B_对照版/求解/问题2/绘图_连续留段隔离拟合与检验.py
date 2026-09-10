from pathlib import Path
import json
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '求解/问题2/图片/连续留段隔离拟合与检验.png'
plt.rcParams['font.sans-serif'] = ['FandolHei', 'Noto Sans CJK SC', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def clean(ax):
    ax.grid(alpha=0.3, linestyle='--')
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)


def block_range(block):
    # 结果目录把1200--3800 cm^-1共同窗口等分为12个连续测试块；图仅表达切分关系。
    left, right = 1200 + (block - 1) * (2600 / 12), 1200 + block * (2600 / 12)
    return left, right


def draw_blocks(ax, fold, y, height):
    for b in range(1, 13):
        a, z = block_range(b)
        if b in fold['训练块']:
            color, label = '#BFD7EA', '训练'
        elif b in fold['校准块']:
            color, label = '#F6BD60', '校准'
        else:
            color, label = '#F28482', '测试'
        ax.fill_between([a, z], y - height, y + height, color=color, alpha=0.9,
                        edgecolor='white', linewidth=0.8)
        ax.text((a + z) / 2, y, str(b), ha='center', va='center', fontsize=11)
        if b == fold['训练块'][0]:
            ax.text((a + z) / 2, y + height + 0.08, label, ha='center', fontsize=11)
    # 训练边界保护带只在训练—非训练邻接处显示，避免把保护带误画成测试数据。
    for b in fold['训练块']:
        for edge in (block_range(b)[0], block_range(b)[1]):
            ax.axvspan(edge - 20, edge + 20, color='#777777', alpha=0.18, lw=0)


def main():
    base = json.loads((ROOT / '求解/问题2/结果/基准交接.json').read_text(encoding='utf-8'))
    folds = base['折分']
    fig, ax = plt.subplots(figsize=(12.2, 3.9))
    for i, fold in enumerate(folds):
        draw_blocks(ax, fold, 1 - i, 0.28)
        ax.text(1160, 1 - i, f'折{i + 1}', ha='right', va='center', fontsize=12,
                fontweight='bold')
    ax.axvline(1200, color='#333333', lw=1.5)
    ax.axvline(3800, color='#333333', lw=1.5)
    ax.set_xlim(1120, 3880); ax.set_ylim(-0.55, 1.55)
    ax.set_yticks([])
    ax.set_xlabel('共同波数窗口（cm$^{-1}$）', fontsize=12)
    ax.text(1200, 1.42, '1200', ha='left', fontsize=11)
    ax.text(3800, 1.42, '3800', ha='right', fontsize=11)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c, label=l) for c, l in
               [('#BFD7EA', '训练块'), ('#F6BD60', '校准块'), ('#F28482', '测试块')]]
    ax.legend(handles=handles, ncol=3, frameon=False, loc='upper center',
              bbox_to_anchor=(0.5, -0.18), fontsize=11)
    ax.text(0.99, 0.04, '灰色窄带：训练边界±20 cm$^{-1}$保护带', transform=ax.transAxes,
            ha='right', fontsize=11, color='#555555')
    clean(ax)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=200, bbox_inches='tight')
    print('图09 已写出：2折、12块同步切分，训练保护带20 cm^-1；末端3800--4000.122 cm^-1留段另见结果记录')


if __name__ == '__main__':
    main()
