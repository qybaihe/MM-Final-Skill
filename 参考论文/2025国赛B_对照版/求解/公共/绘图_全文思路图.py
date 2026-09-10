import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

from 绘图公共 import check_plot_budget, save_figure


def box(axes, left, bottom, width, text, face, edge):
    axes.add_patch(FancyBboxPatch(
        (left, bottom), width, 0.18,
        boxstyle="round,pad=0.008,rounding_size=0.014",
        facecolor=face, edgecolor=edge, linewidth=1.8,
    ))
    axes.text(left + width / 2, bottom + 0.09, text,
              ha="center", va="center", fontsize=16, linespacing=1.5,
              color="#15202b")


def arrow(axes, start, end, dashed=False):
    axes.add_patch(FancyArrowPatch(
        start, end, arrowstyle="-|>", mutation_scale=16,
        linewidth=1.8, linestyle="--" if dashed else "-", color="#526579",
    ))


def main():
    check_plot_budget()
    figure, axes = plt.subplots(figsize=(9.6, 4.6))
    axes.set_xlim(0, 1.03)
    axes.set_ylim(0, 1)
    axes.axis("off")
    for position, label in [(0.155, "输入"), (0.51, "方法"), (0.865, "输出")]:
        axes.text(position, 0.97, label, ha="center", va="center", fontsize=18)
    rows = [
        (0.70, "两界面首次返回\n碳化硅双角条纹", "问题一\n一次往返场展开", "周期与厚度关系\n周期等效量 T"),
        (0.41, "附件一、二 · 碳化硅\n10° / 15°", "问题二\n双角色散消干扰", "碳化硅几何厚度\n情景变化范围"),
        (0.12, "附件三、四 · 硅\n10° / 15°", "问题三\n逐次往返厚度对照", "硅几何厚度\n碳化硅处理判断"),
    ]
    for bottom, inputs, method, outputs in rows:
        box(axes, 0.01, bottom, 0.29, inputs, "#edf3fa", "#527aa3")
        box(axes, 0.365, bottom, 0.29, method, "#e8f5ef", "#2c7a5a")
        box(axes, 0.72, bottom, 0.29, outputs, "#fff2e5", "#bc6c25")
        arrow(axes, (0.308, bottom + 0.09), (0.355, bottom + 0.09))
        arrow(axes, (0.665, bottom + 0.09), (0.710, bottom + 0.09))
    arrow(axes, (0.51, 0.692), (0.51, 0.598), dashed=True)
    axes.text(0.54, 0.645, "模型", fontsize=16, va="center")
    arrow(axes, (0.865, 0.402), (0.865, 0.310))
    axes.text(0.845, 0.355, "碳化硅基准", fontsize=16, va="center", ha="right")
    figure.subplots_adjust(left=0.01, right=0.97, top=0.98, bottom=0.02)
    save_figure(figure, public_name="全文思路图")
    print("摘要：三问各含输入、方法、输出，共九个节点；原有方法与数值承接分别保留。")


if __name__ == "__main__":
    main()
