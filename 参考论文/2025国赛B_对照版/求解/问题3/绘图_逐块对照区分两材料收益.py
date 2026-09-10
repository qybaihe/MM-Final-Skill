from pathlib import Path
import json
import signal
from collections import defaultdict

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter
from 绘图数据_当前 import (
    publication_data as current_data, publication_predictions as paired_predictions,
    publication_summary as save_summary, publication_metadata as figure_metadata,
)


ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "求解" / "问题3" / "结果"
OUT = ROOT / "求解" / "问题3" / "图片" / "逐块对照区分两材料收益.png"

plt.rcParams["font.sans-serif"] = ["FandolHei", "Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams['axes.formatter.use_mathtext'] = False
plt.rcParams.update({'mathtext.fontset': 'stix', 'mathtext.default': 'it'})
plt.rcParams.update({"font.size": 11, "axes.labelsize": 11,
                      "xtick.labelsize": 11, "ytick.labelsize": 11,
                      "legend.fontsize": 11})


def main():
    fair, comparison, _, _ = current_data()
    verified = paired_predictions(fair, comparison)
    scores = comparison["逐块评分"]
    paired = defaultdict(dict)
    for row in scores:
        if row['模型'] not in ('两束', '完整往返'):
            continue
        key = (row["材料"], row['附件'], float(row["入射角_度"]), row["折号"], row["测试块"])
        paired[key][row["模型"]] = float(row["标准化均方根误差"])

    by_material = defaultdict(lambda: defaultdict(list))
    for (material, attachment, angle, fold, block), values in paired.items():
        if set(values) != {"两束", "完整往返"}:
            raise ValueError(f"块级配对不完整：{material} {angle}° 折{fold} 块{block}")
        # 正值表示完整往返的误差更低，负值保留“完整模型劣化”事实。
        diff = values["两束"] - values["完整往返"]
        by_material[material][angle].append((fold, block, diff))
    summary = {material: {'角块数': sum(len(values) for values in angles.values()),
                         '正向改善数': sum(value[2] > 0 for values in angles.values() for value in values)}
               for material, angles in by_material.items()}
    save_summary(OUT.stem, {'分材料': summary, '已核对CSV角块数': len(verified),
                          '附件3折1块6标准化改善': paired['硅', 3, 10.0, 1, 6]['两束'] - paired['硅', 3, 10.0, 1, 6]['完整往返']})

    all_diff = [x[2] for angles in by_material.values() for vals in angles.values() for x in vals]
    lim = max(0.12, 1.18 * max(abs(x) for x in all_diff))
    fold_color = {1: "#2563eb", 2: "#f59e0b"}
    fig, axes = plt.subplots(1, 2, figsize=(10, 5.8), sharey=True)
    materials = ["硅", "碳化硅"]
    for ax, material in zip(axes, materials):
        positions, labels = [], []
        angle_means = {}
        index = 0
        for angle in (10.0, 15.0):
            vals = sorted(by_material[material][angle], key=lambda t: (t[0], t[1]))
            group_x = []
            for fold, block, diff in vals:
                x = index
                positions.append(x)
                group_x.append(x)
                ax.add_line(Line2D([x], [diff], linestyle="None", marker="o", markersize=7,
                                   color=fold_color[fold], markerfacecolor=fold_color[fold],
                                   markeredgecolor="white", markeredgewidth=0.7, zorder=3))
                labels.append(f"{int(angle)}°\n{block}")
                index += 1
            angle_means[angle] = sum(v[2] for v in vals) / len(vals)
            mean_x = sum(group_x) / len(group_x)
            ax.add_line(Line2D([mean_x], [angle_means[angle]], linestyle="None", marker="D",
                               markersize=8, color="#111827", markerfacecolor="#111827",
                               zorder=4, label="角度均值" if angle == 10.0 else None))
            ax.annotate(f"均值 {angle_means[angle]:+.3f}",
                        (mean_x, angle_means[angle]), xytext=(0, 11),
                        textcoords="offset points", ha="center", fontsize=11,
                        color="#111827")
            index += 1  # 角度之间留出视觉分隔
        ax.axhline(0, color="#111827", linewidth=1.5)
        ax.set_ylim(-lim, lim)
        ax.set_xticks(positions)
        ax.set_xticklabels(labels, fontsize=11)
        ax.set_xlabel("入射角与测试块")
        ax.set_ylabel("两束-完整往返\n标准化均方根误差")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, position: f'{value:g}'))
        ax.grid(alpha=0.3, linestyle="--", axis="y")
        ax.text(0.02, 0.96, f"{'(a)' if material == '硅' else '(b)'} {material} · 正向{summary[material]['正向改善数']}/{summary[material]['角块数']}",
                transform=ax.transAxes, fontsize=13, va="top", ha="left",
                bbox=dict(facecolor="white", edgecolor="#cbd5e1", pad=2, alpha=0.9))
        if material == "硅":
            value = paired['硅', 3, 10.0, 1, 6]['两束'] - paired['硅', 3, 10.0, 1, 6]['完整往返']
            position = next(index for index, label in zip(positions, labels) if label == '10°\n6')
            ax.annotate(f"附件3折1块6\n改善 {value:+.4f}", (position, value), xytext=(0.08, 0.72),
                        textcoords="axes fraction", arrowprops=dict(arrowstyle="->", color="#166534", lw=1.5),
                        fontsize=11, color="#166534")
    handles = [Line2D([0], [0], marker="o", color="w", markerfacecolor=fold_color[1],
                      markersize=8, label="折1"),
               Line2D([0], [0], marker="o", color="w", markerfacecolor=fold_color[2],
                      markersize=8, label="折2"),
               Line2D([0], [0], marker="D", color="#111827", markerfacecolor="#111827",
                      markersize=7, label="角度均值")]
    fig.legend(handles=handles, loc="upper center", ncol=3, fontsize=11, frameon=False)
    fig.text(0.5, 0.02, "正值：高阶往返降低误差；负值：高阶往返增加误差",
             ha="center", fontsize=11, color="#475569")
    fig.tight_layout(rect=[0, 0.06, 1, 0.91])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=200, bbox_inches="tight", metadata=figure_metadata(OUT.stem))
    plt.close(fig)
    print(f'逐块对照：{summary}；附件3折1块6改善{value:+.6f}')


if __name__ == "__main__":
    signal.alarm(150)
    main()
