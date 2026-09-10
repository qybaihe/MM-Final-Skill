from pathlib import Path
import csv
import json
import signal

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter
import numpy as np
from 绘图数据_当前 import current_data, save_summary


ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "求解" / "问题3" / "结果"
OUT = ROOT / "求解" / "问题3" / "图片" / "厚度扰动区分谱形与测厚影响.png"

plt.rcParams["font.sans-serif"] = ["FandolHei", "Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams['axes.formatter.use_mathtext'] = False
plt.rcParams.update({'mathtext.fontset': 'stix', 'mathtext.default': 'it'})
plt.rcParams.update({"font.size": 13, "axes.labelsize": 13,
                      "xtick.labelsize": 12, "ytick.labelsize": 12,
                      "legend.fontsize": 12})


def sensitivity_rows():
    fair, _, _, sensitivity = current_data()
    cases = {(case['材料'], case['折号']): case for case in fair['案例']}
    rows = sensitivity['灵敏度_参数扰动']
    for row in rows:
        baseline = cases[row['材料'], row['折号']]['完整往返']['厚度_um']
        if row['基准厚度_um'] != baseline:
            raise ValueError('扰动与对应材料、折的基准厚度不同版')
        if row['状态'] == '已重估' and not np.isclose(
                row['扰动后厚度_um'] - baseline, row['厚度变化_um'], rtol=1e-10, atol=1e-12):
            raise ValueError('扰动厚度变化与重估结果不一致')
    save_summary(OUT.stem, {'记录数': len(rows), '已重估': sum(row['状态'] == '已重估' for row in rows),
                          '不适用': sum(row['状态'] == '不适用' for row in rows),
                          '最大绝对变化_um': max(abs(row['厚度变化_um']) for row in rows if row['状态'] == '已重估')})
    return rows




def load_optional_impact():
    candidates = ["厚度影响检验.json", "影响检验.json", "高阶影响检验.json"]
    required = ("配对重采样厚度差_um", "无高阶模拟95%经验阈值_um", "独立模拟覆盖计数")
    for name in candidates:
        path = RESULT / name
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not all(key in data for key in required):
            continue
        return {
            "paired": data[required[0]],
            "threshold": float(data[required[1]]),
            "coverage": data[required[2]],
            "sample_count": data.get("配对重采样样本数"),
            "seed": data.get("配对重采样种子"),
            "fixed": data.get("固定参数周期不变对照"),
            "source_name": name,
        }
    return None


def style(ax):
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, position: f'{value:g}'))
    ax.grid(alpha=0.3, linestyle="--", axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)




def draw_formal_impact(ax, info, optional):
    materials = ["硅", "碳化硅"]
    colors = ["#2563eb", "#d97706"]
    for x, material, color in zip([1, 2], materials, colors):
        arr = np.asarray(optional["paired"][material], dtype=float)
        jitter = np.linspace(-0.12, 0.12, len(arr)) if len(arr) else np.array([])
        ax.add_line(Line2D(np.full(len(arr), x) + jitter, arr, linestyle="None", marker="o",
                           markersize=4.6, color=color, markerfacecolor=color,
                           markeredgecolor="white", markeredgewidth=0.35, alpha=0.7, zorder=3))
        if len(arr):
            ax.hlines(np.nanmedian(arr), x - 0.22, x + 0.22, color=color, linewidth=2.0)
            ax.text(x, np.nanmax(arr),
                    f"n={len(arr)}\n中位 {np.nanmedian(arr):+.3f} $\\mu\\mathrm{{m}}$",
                    ha="center", va="bottom", fontsize=8.7)
    ax.axhline(optional["threshold"], color="#991b1b", linestyle="--", linewidth=1.5,
                label=("无高阶模拟95%经验阈值 "
                       f"{optional['threshold']:.3f} $\\mu\\mathrm{{m}}$"))
    ax.set_xticks([1, 2], materials)
    ax.set_ylabel(r"配对重采样厚度差（$\mu\mathrm{m}$）")
    ax.legend(frameon=False, loc="upper right", fontsize=8.5)
    info.axis("off")
    info.text(0.02, 0.92, "厚度影响检验", fontsize=10.5, color="#172033", va="top")
    info.text(0.02, 0.80,
              f"配对重采样：n={optional['sample_count']}\n随机种子：{optional['seed']}\n"
              "阈值与覆盖计数：按结果文件读取",
              fontsize=8.8, color="#334155", va="top")
    info.text(0.02, 0.48, "独立模拟覆盖", fontsize=9.5, color="#172033", va="top")
    y = 0.39
    for key, value in optional["coverage"].items():
        info.text(0.03, y, f"{key}：{value}", fontsize=8.6, color="#334155", va="top")
        y -= 0.09
    fixed = optional["fixed"]
    info.text(0.02, 0.12, f"固定参数周期对照：{fixed}", fontsize=8.4,
              color="#475569", va="top", wrap=True)


def draw_sensitivity_fallback(ax, material, rows):
    grouped = {}
    for row in rows:
        if row['状态'] == '已重估':
            grouped.setdefault(row["情景"], []).append(row)
    order = sorted(grouped, key=lambda key: (-max(abs(float(r["厚度变化_um"])) for r in grouped[key]), key))
    colors = {"硅": "#2563eb", "碳化硅": "#d97706"}
    markers = {1: "o", 2: "s"}
    offsets = {('硅', 1): 0.16, ('硅', 2): -0.16,
               ('碳化硅', 1): 0.16, ('碳化硅', 2): -0.16}
    y_values = np.arange(len(order), dtype=float)
    for y, scenario in zip(y_values, order):
        scenario_rows = [row for row in grouped[scenario] if row['材料'] == material]
        changes = np.array([float(r["厚度变化_um"]) for r in scenario_rows], dtype=float)
        ax.hlines(y, np.min(changes), np.max(changes), color="#cbd5e1", linewidth=2.0, zorder=1)
        for row in scenario_rows:
            material, fold = row["材料"], row["折号"]
            ax.add_line(Line2D([float(row["厚度变化_um"])], [y + offsets[(material, fold)]],
                               linestyle="None", marker=markers[fold], markersize=5.2,
                               color=colors[material], markerfacecolor=colors[material],
                               markeredgecolor="white", markeredgewidth=0.35, zorder=3))
    ax.axvline(0.0, color="#111827", linewidth=1.5)
    labels = [label.replace('有效损耗绝对增加0→', '损耗0→').replace('有效损耗', '损耗')
              .replace('标准化增益', '增益').replace('入射角同时', '角度').replace('偏振权重', '偏振')
              + (r" $\mathrm{cm}^{-1}$" if "窗口" in label else '') for label in order]
    ax.set_yticks(y_values, labels)
    ax.set_ylim(len(order) - 0.5, -0.5)
    changes_all = [row['厚度变化_um'] for row in rows if row['状态'] == '已重估']
    span = max(changes_all) - min(changes_all)
    ax.set_xlim(min(changes_all) - 0.08 * span, max(changes_all) + 0.08 * span)
    ax.set_xlabel(r"厚度变化 $\Delta d$（$\mu\mathrm{m}$）")
    extreme = max((row for row in rows if row['材料'] == material and row['状态'] == '已重估'),
                  key=lambda row: abs(row['厚度变化_um']))
    ax.text(0.02, 1.025, f"{'(a)' if material == '硅' else '(b)'} {material}\n最大变化 {extreme['厚度变化_um']:+.3f} "
            + r'$\mu\mathrm{m}$', transform=ax.transAxes, fontsize=13, va='bottom')
    style(ax)
    return [
        Line2D([0], [0], marker="o", color="#334155", markerfacecolor="none",
               label="折1"),
        Line2D([0], [0], marker="s", color="#334155", markerfacecolor="none",
               label="折2"),
    ]


def main():
    rows = sensitivity_rows()
    fig, axes = plt.subplots(1, 2, figsize=(10, 9.2), sharey=True)
    for axis, material in zip(axes, ('硅', '碳化硅')):
        handles = draw_sensitivity_fallback(axis, material, rows)
    fig.legend(handles=handles, loc='upper center', ncol=2, frameon=False, fontsize=13)
    footer = (f"变化＝重估厚度$-$各折基准；{sum(row['状态'] == '已重估' for row in rows)}项已重估，"
              f"{sum(row['状态'] == '不适用' for row in rows)}项绝对加损耗因基准非零而不适用。")
    fig.text(0.5, 0.018, footer, ha="center", fontsize=11, color="#475569")
    fig.tight_layout(rect=[0, 0.055, 1, 0.92])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f'厚度扰动图：当前灵敏度JSON共{len(rows)}项，按材料与折对应基准；未计算项不画成零点')


if __name__ == "__main__":
    signal.alarm(150)
    main()
