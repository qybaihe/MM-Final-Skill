"""图03：共同坐标标记质量区段。"""

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from 绘图公共 import check_plot_budget, read_json, save_figure, clean_axes


def main():
    check_plot_budget()
    audit = read_json("求解/问题2/结果/数据审查.json")
    check = read_json("求解/问题1/结果/实测输入核验.json")
    archive = read_json("交接/数据档案.json")
    attachment2 = next(item for item in archive["文件档案"] if item["文件名"] == "附件2.xlsx")
    quality_segment = attachment2["工作表"][0]["反射率检查"]["超过百分之百区段"][0]
    rows = [
        ("附件1 · 10°", "碳化硅", "#2f6f9f"),
        ("附件2 · 15°", "碳化硅", "#d9772a"),
        ("附件3 · 10°", "硅", "#4a8b5c"),
        ("附件4 · 15°", "硅", "#9b5c9b"),
    ]
    y_positions = [3.5, 2.5, 1.5, 0.5]
    xmin, xmax = check["波数范围_每厘米"]
    fit_left, fit_right = audit["主窗口_cm^-1"]
    fig, ax = plt.subplots(figsize=(14, 5.7))
    ax.set_xlim(350, 4160)
    ax.set_ylim(-0.25, 4.15)
    ax.axvspan(fit_left, fit_right, color="#6ba36f", alpha=0.16, zorder=0)
    ax.axvspan(quality_segment["起始波数"], quality_segment["终止波数"], color="#cc4c4c", alpha=0.15, zorder=0)
    ax.axvline(xmin, color="#1f2933", lw=1.8, ls="--", zorder=2)
    for label, material, color, y in zip([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], y_positions):
        ax.hlines(y, xmin, xmax, color="#768692", lw=5.0, alpha=0.42, zorder=1)
        ax.hlines(y, fit_left, fit_right, color=color, lw=8.0, alpha=0.92, zorder=3)
        ax.add_line(Line2D([xmin], [y], marker="o", linestyle="None", color=color,
                           markerfacecolor=color, markeredgecolor="white", markersize=7,
                           markeredgewidth=0.8, zorder=5))
        ax.text(4055, y, label, ha="left", va="center", fontsize=11.5, color=color)
    ax.text(4055, 4.02, "对象 / 角度", ha="left", va="bottom", fontsize=11, color="#52606d")
    ax.annotate(f"共同首点\n{xmin:.3f} cm$^{{-1}}\nR=0%", xy=(xmin, 3.5), xytext=(470, 3.84),
                textcoords="data", fontsize=11, ha="left", va="center",
                arrowprops=dict(arrowstyle="-", color="#1f2933", lw=1.5))
    ax.annotate(f"质量标记：原始行{quality_segment['起始行']}–{quality_segment['终止行']}\n附件2有{quality_segment['点数']}点 >100%",
                xy=((quality_segment["起始波数"] + quality_segment["终止波数"]) / 2, 2.5), xytext=(970, 3.58),
                fontsize=11, color="#8f2626", ha="left", va="center",
                arrowprops=dict(arrowstyle="-", color="#8f2626", lw=1.5))
    ax.annotate(f"主拟合采用：{fit_left:g}–{fit_right:g} cm$^{{-1}}\n每角原始交集 {audit['共同窗口原始点数_每角度']} 点；实际抽样 {audit['抽样点数_每角度']} 点",
                xy=((fit_left + fit_right) / 2, 0.5), xytext=(2050, -0.02),
                fontsize=11, color="#2c6337", ha="center", va="top",
                arrowprops=dict(arrowstyle="-", color="#2c6337", lw=1.5))
    handles = [
        Line2D([0], [0], color="#768692", lw=5, alpha=0.65, label=f"原始坐标存在：{xmin:.3f}–{xmax:.3f}"),
        Line2D([0], [0], color="#4a8b5c", lw=7, label="主拟合采用区段"),
        Line2D([0], [0], color="#cc4c4c", lw=7, alpha=0.6, label="质量标记区段"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#1f2933", markersize=7, label="共同首点"),
    ]
    ax.legend(handles=handles, loc="upper left", frameon=False, ncol=4, bbox_to_anchor=(0, 1.02))
    ax.set_xlabel("原始波数 (cm$^{-1}$)")
    ax.set_yticks([])
    ax.text(0.01, 0.04, "两角使用同一原始波数键；不插值、不平均光谱", transform=ax.transAxes,
            fontsize=11, color="#243b53", bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#c9d2db", alpha=0.9))
    clean_axes(ax, grid=False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_visible(True)
    fig.subplots_adjust(left=0.03, right=0.82, top=0.87, bottom=0.16)
    save_figure(fig, public_name="共同坐标标记质量区段")


if __name__ == "__main__":
    main()
