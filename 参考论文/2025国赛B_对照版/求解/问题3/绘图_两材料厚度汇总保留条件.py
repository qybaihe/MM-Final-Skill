from pathlib import Path
import json
import signal

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from 绘图数据_当前 import (
    publication_data as current_data, publication_predictions as paired_predictions,
    publication_summary as save_summary, publication_metadata as figure_metadata,
)


ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "求解" / "问题3" / "结果"
OUT = ROOT / "求解" / "问题3" / "图片" / "两材料厚度汇总保留条件.png"

plt.rcParams["font.sans-serif"] = ["FandolHei", "Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams['axes.formatter.use_mathtext'] = False
plt.rcParams.update({'mathtext.fontset': 'stix', 'mathtext.default': 'it'})
plt.rcParams.update({"font.size": 11, "axes.labelsize": 11,
                      "xtick.labelsize": 11, "ytick.labelsize": 11,
                      "legend.fontsize": 11})


def main():
    fair, comparison, result, _ = current_data()
    verified = paired_predictions(fair, comparison)
    metrics = result['核心指标']
    si_best = metrics['硅全量完整往返厚度_um']
    si_fold = [metrics[f'硅折{fold}完整往返条件厚度_um'] for fold in (1, 2)]
    si_envelope = [metrics['硅已计算条件包络下限_um'], metrics['硅已计算条件包络上限_um']]
    sic_base = metrics['碳化硅正式基准厚度_um']
    sic_range = [metrics['碳化硅正式条件范围下限_um'], metrics['碳化硅正式条件范围上限_um']]
    sic_replaced = False
    if not (si_envelope[0] <= min(si_fold) <= si_best <= max(si_fold) <= si_envelope[1]):
        raise ValueError('指定版本的硅点估计与条件包络不一致')
    save_summary(OUT.stem, {'核心指标': metrics, '硅折间跨度_um': max(si_fold) - min(si_fold),
                          '硅两折范围_um': [min(si_fold), max(si_fold)],
                          '硅完整条件包络_um': si_envelope, '碳化硅是否替换': sic_replaced,
                          '已核对CSV角块数': len(verified)})

    fig, (ax, lower) = plt.subplots(2, 1, figsize=(9.2, 6.2), sharex=True)
    ax.set_xlim(0, max(si_envelope[1], sic_range[1]) + 0.8)
    ax.set_ylim(-0.62, 1.60)
    y_si, y_sic = 1.0, 0.65
    ax.hlines(-0.12, *si_envelope, color='#64748b', linewidth=4, alpha=0.45)
    ax.plot(si_envelope, [-0.12, -0.12], '|', color='#475569', markersize=13)
    ax.text(0.02, 0.03, f'完整条件包络 {si_envelope[0]:.2f}—{si_envelope[1]:.2f} ' + r'$\mu\mathrm{m}$',
            transform=ax.transAxes, fontsize=12, va='bottom')
    ax.text(0.02, 0.96, '(a) 硅', transform=ax.transAxes, fontsize=13, va='top')
    # 硅的折间范围是条件稳定性带，中心点是全量原始点条件估计。
    ax.hlines(y_si, min(si_fold), max(si_fold), color="#2563eb", linewidth=5, alpha=0.35,
              label="折间条件范围")
    ax.add_line(Line2D([si_best], [y_si], marker="o", linestyle="None", markersize=10,
                       color="#1d4ed8", markerfacecolor="#1d4ed8",
                       label="硅全量完整往返"))
    ax.add_line(Line2D([min(si_fold), max(si_fold)], [y_si, y_si], marker="|",
                       linestyle="None", markersize=16, color="#1d4ed8"))
    ax.annotate(f"全量 {si_best:.2f} " + r'$\mu\mathrm{m}$', (si_best, y_si), xytext=(0, 10),
                textcoords="offset points", ha="center", fontsize=11, color="#1e3a8a")
    ax.annotate(f"两折 {min(si_fold):.2f}—{max(si_fold):.2f} " + r'$\mu\mathrm{m}$'
                + f"；跨度 {max(si_fold) - min(si_fold):.2f} " + r'$\mu\mathrm{m}$',
                ((min(si_fold) + max(si_fold)) / 2, y_si), xytext=(0, -19),
                textcoords="offset points", ha="center", fontsize=12, color="#1e3a8a")

    ax.set_yticks([])
    ax.grid(alpha=0.3, linestyle='--', axis='x')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax = lower
    ax.set_ylim(-0.35, 1.8)
    ax.text(0.02, 0.96, '(b) 碳化硅 · 保留问题二代表点', transform=ax.transAxes,
            fontsize=13, va='top')
    ax.hlines(y_sic, sic_range[0], sic_range[1], color="#d97706", linewidth=5, alpha=0.35,
              label="碳化硅厚度重估范围")
    ax.add_line(Line2D([sic_range[0], sic_range[1]], [y_sic, y_sic], marker="|",
                       linestyle="None", markersize=16, color="#b45309"))
    ax.add_line(Line2D([sic_base], [y_sic], marker="o", linestyle="None", markersize=10,
                       color="#b45309", markerfacecolor="#b45309",
                       label="碳化硅非触边代表点"))
    ax.annotate(f"代表厚度 {sic_base:.2f} " + r'$\mu\mathrm{m}$', (sic_base, y_sic), xytext=(0, 18),
                textcoords="offset points", ha="center", fontsize=11, color="#92400e")
    ax.annotate(f"条件范围 {sic_range[0]:.2f}—{sic_range[1]:.2f} " + r'$\mu\mathrm{m}$',
                ((sic_range[0] + sic_range[1]) / 2, y_sic), xytext=(0, -25),
                textcoords="offset points", ha="center", fontsize=11, color="#92400e")
    ax.set_yticks([])
    ax.set_xlabel(r"厚度（$\mu\mathrm{m}$）")
    ax.grid(alpha=0.3, linestyle="--", axis="x")
    fig.legend(handles=[Line2D([0], [0], marker='o', linestyle='None', color='#334155', label='厚度点'),
                        Line2D([0], [0], linewidth=4, color='#94a3b8', label='对应条件范围')],
               loc='upper center', fontsize=12, ncol=2, frameon=False)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    fig.tight_layout(rect=(0, 0, 1, 0.91), h_pad=1.4)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=200, bbox_inches="tight", metadata=figure_metadata(OUT.stem))
    plt.close(fig)
    print(f"两材料汇总：硅全量{si_best:.6f}，两折{si_fold}，完整条件包络{si_envelope}；碳化硅代表{sic_base:.6f}微米")


if __name__ == "__main__":
    signal.alarm(150)
    main()
