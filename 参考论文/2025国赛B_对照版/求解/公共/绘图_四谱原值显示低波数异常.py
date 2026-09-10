import matplotlib.pyplot as plt
import signal

from 绘图公共 import check_plot_budget, read_json, read_xlsx, save_figure, clean_axes


def main():
    archive = read_json("交接/数据档案.json")
    inputs = [
        ("碳化硅", [("附件1.xlsx", "附件1 · 10°"), ("附件2.xlsx", "附件2 · 15°")]),
        ("硅", [("附件3.xlsx", "附件3 · 10°"), ("附件4.xlsx", "附件4 · 15°")]),
    ]
    spectra = {}
    for material, attachments in inputs:
        for filename, label in attachments:
            check_plot_budget()
            spectra[filename] = read_xlsx(f"数据/{filename}")[:2]
    attachment2 = archive["文件档案"][1]["工作表"][0]
    quality = attachment2["反射率检查"]["超过百分之百区段"][0]
    maximum = attachment2["字段档案"][1]["统计"]["最大值"]
    figure, axes = plt.subplots(2, 2, figsize=(8.8, 5.2), sharey=True)
    colors = ["#2f6f9f", "#d9772a"]
    for column, (material, attachments) in enumerate(inputs):
        for index, (filename, label) in enumerate(attachments):
            wave_number, reflectance = spectra[filename]
            for row in range(2):
                selected = wave_number <= 1100 if row else slice(None)
                axes[row, column].plot(wave_number[selected], reflectance[selected],
                                       color=colors[index], lw=1.8, label=label)
        for row in range(2):
            panel = axes[row, column]
            panel.axhline(100, color="#9c2f2f", lw=1.5, ls="--")
            if column == 0:
                panel.axvspan(quality["起始波数"], quality["终止波数"],
                             color="#cc4c4c", alpha=0.13)
            panel.set_ylim(-2, 123)
            panel.set_yticks([0, 50, 100])
            panel.tick_params(labelsize=16)
            panel.text(0.03, 0.97, f"({chr(97 + row * 2 + column)}) {material} · {'局部' if row else '全谱'}",
                       transform=panel.transAxes, fontsize=16, va="top")
            clean_axes(panel)
        axes[0, column].set_xlim(350, 4050)
        axes[0, column].set_xticks([1000, 2500, 4000])
        axes[0, column].legend(frameon=False, loc="upper right", fontsize=16,
                               bbox_to_anchor=(0.99, 0.78), handlelength=1.6, borderpad=0.1)
        axes[1, column].set_xlim(350, 1100)
        axes[1, column].set_xticks([400, 700, 1000])
        axes[1, column].set_xlabel("波数 / cm$^{-1}$", fontsize=16)
    axes[0, 0].set_ylabel("原始反射率 / %", fontsize=16)
    axes[1, 0].set_ylabel("原始反射率 / %", fontsize=16)
    figure.text(0.52, 0.02,
                f"附件2：{quality['点数']}点超过100%，最高{maximum:.2f}%；四谱首点均为0%",
                ha="center", fontsize=16, color="#8f2626")
    figure.subplots_adjust(hspace=0.40, wspace=0.14, left=0.11, right=0.98, top=0.98, bottom=0.23)
    save_figure(figure, public_name="四谱原值显示低波数异常")
    print(f"摘要：四谱原值全部保留；附件2超界{quality['点数']}点，最高{maximum:.2f}%；字号16磅。")


if __name__ == "__main__":
    signal.alarm(150)
    main()
