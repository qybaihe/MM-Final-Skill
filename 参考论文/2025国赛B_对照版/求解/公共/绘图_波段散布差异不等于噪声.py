"""图04：波段散布差异不等于噪声。"""

import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np
import signal

from 绘图公共 import check_plot_budget, read_json, read_xlsx, save_figure, clean_axes


def main():
    specs = [
        ("附1 碳化硅10°", "数据/附件1.xlsx", "#2f6f9f"),
        ("附2 碳化硅15°", "数据/附件2.xlsx", "#d9772a"),
        ("附3 硅10°", "数据/附件3.xlsx", "#4a8b5c"),
        ("附4 硅15°", "数据/附件4.xlsx", "#9b5c9b"),
    ]
    archive = read_json("交接/数据档案.json")
    reference = next(item for item in archive["文件档案"] if item["文件名"] == "附件2.xlsx")
    bands = [tuple(item["左闭右开波数区间"]) for item in reference["工作表"][0]["分波段描述"]]
    values = []
    for label, path, color in specs:
        check_plot_budget()
        x, y, _ = read_xlsx(path)
        values.append((label, color, x, y))

    low_spreads = [record['工作表'][0]['分波段描述'][1]['反射率统计']['样本标准差'] for record in archive['文件档案']]
    high_spreads = [record['工作表'][0]['分波段描述'][6]['反射率统计']['样本标准差'] for record in archive['文件档案']]
    fig, ax = plt.subplots(figsize=(8.8, 4.4))
    centers = np.arange(len(bands), dtype=float)
    offsets = np.linspace(-0.30, 0.30, len(specs))
    for spec_index, (label, color, x, y) in enumerate(values):
        for band_index, (left, right) in enumerate(bands):
            check_plot_budget()
            mask = (x >= left) & (x < right if band_index < len(bands) - 1 else x <= 4000.122)
            data = y[mask]
            bp = ax.boxplot([data], positions=[centers[band_index] + offsets[spec_index]], widths=0.16,
                            patch_artist=True, showfliers=True, whis=1.5, manage_ticks=False,
                            boxprops=dict(facecolor=color, edgecolor=color, alpha=0.64, linewidth=1.6),
                            medianprops=dict(color="#17202a", linewidth=1.8),
                            whiskerprops=dict(color=color, linewidth=1.5),
                            capprops=dict(color=color, linewidth=1.5),
                            flierprops=dict(marker="o", markersize=2.8, markerfacecolor=color,
                                            markeredgecolor=color, alpha=0.55))
    ax.axhline(100, color="#9c2f2f", lw=1.8, ls="--")
    ax.set_xticks(centers)
    ax.set_xticklabels([f"{left:g}–\n{right:g}" for left, right in bands])
    ax.tick_params(labelsize=16)
    ax.set_xlabel("波数分段 / cm$^{-1}$", fontsize=16)
    ax.set_ylabel("原始反射率 / %", fontsize=16)
    ax.set_ylim(bottom=0, top=max(105, max(float(np.max(y)) for _, _, _, y in values) + 2))
    handles = [Patch(facecolor=color, edgecolor=color, alpha=0.64, label=label) for label, _, color in specs]
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.01),
              frameon=False, ncol=2, fontsize=17, handlelength=1.2, columnspacing=1.2)
    ax.text(0.98, 0.87, '四谱标准差（百分点）\n'
            f'500–1000段：{min(low_spreads):.2f}–{max(low_spreads):.2f}\n'
            f'3000–3500段：{min(high_spreads):.2f}–{max(high_spreads):.2f}',
            transform=ax.transAxes, ha='right', va='top', fontsize=14,
            bbox=dict(facecolor='white', edgecolor='none', alpha=0.9))
    clean_axes(ax)
    fig.subplots_adjust(left=0.10, right=0.99, bottom=0.22, top=0.81)
    save_figure(fig, public_name="波段散布差异不等于噪声")
    print(f"摘要：四附件仍按{len(bands)}个原波段展示全部箱体、箱须与外点；图题：四谱低波数段反射率散布更大。")


if __name__ == "__main__":
    signal.alarm(150)
    main()
