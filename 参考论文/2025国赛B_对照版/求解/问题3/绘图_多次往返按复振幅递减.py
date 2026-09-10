from pathlib import Path
import json
import signal

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from 绘图数据_当前 import current_data, save_summary


ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "求解" / "问题3" / "结果"
OUT = ROOT / "求解" / "问题3" / "图片" / "多次往返按复振幅递减.png"

plt.rcParams["font.sans-serif"] = ["FandolHei", "Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams['axes.formatter.use_mathtext'] = False
plt.rcParams.update({'mathtext.fontset': 'dejavusans', 'mathtext.default': 'it'})
plt.rcParams.update({"font.size": 11, "axes.labelsize": 11,
                      "xtick.labelsize": 11, "ytick.labelsize": 11,
                      "legend.fontsize": 11})


def box(ax, xy, width, height, text, face, edge="#334155", fontsize=12,
        radius=0.025, text_color="#172033"):
    patch = FancyBboxPatch(
        xy, width, height,
        boxstyle=f"round,pad=0.012,rounding_size={radius}",
        facecolor=face, edgecolor=edge, linewidth=1.5,
        transform=ax.transAxes,
    )
    ax.add_patch(patch)
    patch._label_artist = ax.text(
        xy[0] + width / 2, xy[1] + height / 2, text,
        ha="center", va="center", fontsize=fontsize, color=text_color,
        transform=ax.transAxes,
    )
    return patch


def arrow(ax, start, end, label=None, color="#475569", rad=0.0, fontsize=11):
    patch = FancyArrowPatch(
        start, end, transform=ax.transAxes,
        arrowstyle="-|>", mutation_scale=15,
        connectionstyle=f"arc3,rad={rad}",
        linewidth=1.6, color=color,
    )
    ax.add_patch(patch)
    if label:
        mx = (start[0] + end[0]) / 2
        my = (start[1] + end[1]) / 2 + (0.025 if rad >= 0 else -0.025)
        ax.text(mx, my, label, fontsize=fontsize, color=color,
                ha="center", va="center", transform=ax.transAxes,
                bbox=dict(facecolor="white", edgecolor="none", pad=1.5, alpha=0.85))


def main():
    fair, _, _, _ = current_data()
    checks = [case for case in fair['案例'] if case['折号'] in (1, 2)]
    u_max = {(case['材料'], case['折号']): case['场级数核验']['最大往返乘子模'] for case in checks}
    unknown = {item for case in checks for row in case['场级数核验']['逐附件高阶可观测性诊断']
               for item in row['未提供条件']}
    save_summary(OUT.stem, {'最大往返乘子模': {f'{material}折{fold}': value for (material, fold), value in u_max.items()},
                          '未提供条件': sorted(unknown), '求和关系': '复场相干叠加'})

    fig, ax = plt.subplots(figsize=(8.0, 5.74))
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # 物理层次：把入射、薄膜、衬底与返回路径画成连续的手绘结构。
    ax.add_patch(FancyBboxPatch((0.07, 0.77), 0.79, 0.12,
                                boxstyle="round,pad=0.01,rounding_size=0.02",
                                facecolor="#e0f2fe", edgecolor="#0369a1",
                                linewidth=1.5, transform=ax.transAxes))
    ax.text(0.465, 0.871, "外部入射场", ha="center", va="center", fontsize=12,
            transform=ax.transAxes, color="#075985")
    ax.add_patch(FancyBboxPatch((0.22, 0.77), 0.44, 0.06,
                                boxstyle="round,pad=0.008,rounding_size=0.02",
                                facecolor="#fef3c7", edgecolor="#b45309",
                                linewidth=1.5, transform=ax.transAxes))
    ax.text(0.44, 0.80, "外延层 d；相位随波数变化",
            ha="center", va="center", fontsize=11, transform=ax.transAxes,
            color="#92400e")
    ax.add_patch(FancyBboxPatch((0.07, 0.70), 0.79, 0.055,
                                boxstyle="round,pad=0.008,rounding_size=0.02",
                                facecolor="#e2e8f0", edgecolor="#475569",
                                linewidth=1.5, transform=ax.transAxes))
    ax.text(0.465, 0.727, r"衬底（界面反射 $r_{12}$）", ha="center", va="center",
            fontsize=11, transform=ax.transAxes, color="#334155")
    arrow(ax, (0.12, 0.93), (0.20, 0.84), color="#0369a1", rad=0.0)
    ax.text(0.12, 0.962, '入射', ha='center', fontsize=11, color='#0369a1', transform=ax.transAxes)
    arrow(ax, (0.17, 0.70), (0.17, 0.60), color="#b45309", rad=0.0)

    # 首返回与后续复振幅递推。
    box(ax, (0.07, 0.49), 0.20, 0.105, "(a) 首返回\n$B$", "#fef3c7", edge="#b45309", fontsize=14)
    box(ax, (0.37, 0.49), 0.20, 0.105, "后续返回\n$Bu$", "#dcfce7", edge="#15803d", fontsize=14)
    box(ax, (0.67, 0.49), 0.20, 0.105, "再下一次\n$Bu^2$", "#dbeafe", edge="#1d4ed8", fontsize=14)
    arrow(ax, (0.285, 0.542), (0.355, 0.542), r"$\times u$", color="#15803d")
    arrow(ax, (0.585, 0.542), (0.655, 0.542), r"$\times u$", color="#1d4ed8")
    arrow(ax, (0.885, 0.542), (0.96, 0.542), "…", color="#64748b")
    ax.text(0.50, 0.65, r"$u = r_{10}r_{12}z$" + "\n每次乘 u 改变幅值与相位",
            fontsize=13, ha="center", va="center", transform=ax.transAxes,
            color="#334155")
    ax.text(0.50, 0.18, r"$|u| < 1$" + "\n" + r"相邻返回场相位差为 $\arg(u)$",
            fontsize=13, ha="center", va="center", transform=ax.transAxes,
            color="#334155")

    box(ax, (0.08, 0.275), 0.85, 0.16,
        r"$E_\infty = r_{01}+B+Bu+Bu^2+\cdots$" + "\n" + r"$=r_{01}+\frac{B}{1-u}$",
        "#f3e8ff", edge="#7e22ce", fontsize=15)
    ax.text(0.50, 0.06, "先叠加带相位的复场\n" + r"再取强度 $|E_\infty|^2$", fontsize=13,
            ha="center", va="center", transform=ax.transAxes, color="#7e22ce")
    arrow(ax, (0.47, 0.477), (0.47, 0.451), color="#7e22ce", rad=0.0)

    # 真实结果中的收敛量级作为图内证据，而非把示意路径当作定量曲线。
    stats_ax = fig.add_axes((0.755, 0.47, 0.225, 0.43))
    stats_ax.set_axis_off()
    stats_ax.text(0.5, 1, r"(b) 最大 $|u|$" + "\n（无量纲）", ha='center', va='top', fontsize=13)
    for material, position, color in [('硅', 0.66, '#1d4ed8'), ('碳化硅', 0.25, '#15803d')]:
        stats_ax.text(0.05, position, material, va='top', fontsize=14, color=color)
        stats_ax.text(0.05, position - 0.14,
                      f"折1  {u_max[material, 1]:.4f}\n折2  {u_max[material, 2]:.4f}",
                      va='top', fontsize=13, linespacing=1.5)

    if unknown != {'时间相干长度', '空间重叠', '仪器分辨率'}:
        raise ValueError('当前仪器条件记录变化，须重新核对图内短句')
    missing = "(c) 附件未提供\n时间相干长度\n空间重叠\n仪器分辨率"
    conditions_ax = fig.add_axes((0.755, 0.12, 0.225, 0.24))
    conditions_ax.set_axis_off()
    conditions_ax.text(0.05, 0.97, missing, ha='left', va='top', fontsize=12, linespacing=1.7, color='#9a3412')
    for label in ax.texts:
        label.set_fontsize(max(11, label.get_fontsize()))

    ax.set_position((0.015, 0.04, 0.715, 0.93))
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    for patch in ax.patches:
        if isinstance(patch, FancyBboxPatch):
            bounds = patch.get_window_extent(renderer)
            if not ax.bbox.fully_contains(bounds.x0, bounds.y0) or not ax.bbox.fully_contains(bounds.x1, bounds.y1):
                raise ValueError('图10圆角框越过画布边界，禁止输出裁切图')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f'图10：复场相干叠加，每次乘u；当前公平对照的四个最大|u|：{u_max}')


if __name__ == "__main__":
    signal.alarm(150)
    main()
