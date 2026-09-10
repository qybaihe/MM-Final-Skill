from pathlib import Path
import csv
import math
import signal

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from 绘图数据_当前 import (
    publication_data as current_data, publication_predictions as paired_predictions,
    publication_summary as save_summary, publication_metadata as figure_metadata,
)


ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "求解" / "问题3" / "结果"
OUT = ROOT / "求解" / "问题3" / "图片" / "硅双角谱检验高阶贡献.png"

plt.rcParams["font.sans-serif"] = ["FandolHei", "Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams['axes.formatter.use_mathtext'] = False
plt.rcParams.update({'mathtext.fontset': 'stix', 'mathtext.default': 'it'})
plt.rcParams.update({"font.size": 11, "axes.labelsize": 11,
                      "xtick.labelsize": 11, "ytick.labelsize": 11,
                      "legend.fontsize": 11})


def load_rows():
    fair, comparison, _, _ = current_data()
    rows = []
    for (material, fold, attachment, block), pair in paired_predictions(fair, comparison).items():
        for row in pair['记录']:
            for model in ('两束', '完整往返'):
                observed, predicted = float(row['反射率_比例']), float(row[model])
                rows.append({'材料': material, '折号': str(fold), '附件': attachment,
                             '测试块': str(block), '入射角_度': pair['角度'], '模型': model,
                             '波数_cm^-1': row['波数_cm^-1'], '实测反射率_比例': observed,
                             '预测反射率_比例': predicted, '残差_比例': predicted - observed})
    return rows


def pick(rows, angle, model):
    chosen = [
        r for r in rows
        if r["材料"] == "硅"
        and r["折号"] == "1"
        and r["测试块"] == "6"
        and r["模型"] == model
        and float(r["入射角_度"]) == angle
    ]
    if not chosen:
        raise ValueError(f"缺少硅 {angle:g}°、第1折测试块6、{model}的同段预测")
    return sorted(chosen, key=lambda r: float(r["波数_cm^-1"]))


def main():
    rows = load_rows()
    residual_gap = max(abs(float(row['残差_比例']) -
                           (float(row['预测反射率_比例']) - float(row['实测反射率_比例'])))
                       for row in rows)
    if residual_gap != 0.0:
        raise ValueError('原图残差须逐点等于预测−实测，禁止沿用另一符号解释')
    data = {}
    for angle in (10.0, 15.0):
        actual = pick(rows, angle, "两束")
        two = pick(rows, angle, "两束")
        full = pick(rows, angle, "完整往返")
        if len(actual) != len(full):
            raise ValueError("两束与完整往返的同段点数不一致")
        x = [float(r["波数_cm^-1"]) for r in actual]
        y = [float(r["实测反射率_比例"]) for r in actual]
        y_two = [float(r["预测反射率_比例"]) for r in two]
        y_full = [float(r["预测反射率_比例"]) for r in full]
        data[angle] = {
            "x": x,
            "y": y,
            "two": y_two,
            "full": y_full,
            "r_two": [float(r["残差_比例"]) for r in two],
            "r_full": [float(r["残差_比例"]) for r in full],
        }
    summary = {f'{angle:g}度': {
        '测试点数': len(values['x']),
        '两束误差_百分点': 100 * math.sqrt(sum(value ** 2 for value in values['r_two']) / len(values['x'])),
        '完整误差_百分点': 100 * math.sqrt(sum(value ** 2 for value in values['r_full']) / len(values['x']))
    } for angle, values in data.items()}
    save_summary(OUT.stem, {'逐角结果': summary, '残差定义': '预测减实测', '折号': 1, '测试块': 6,
                          '逐点残差最大差': residual_gap})

    top_min = min(min(v[k]) for v in data.values() for k in ("y", "two", "full"))
    top_max = max(max(v[k]) for v in data.values() for k in ("y", "two", "full"))
    pad = max(0.006, 0.06 * (top_max - top_min))
    residual_max = max(max(abs(z) for z in v[k]) for v in data.values() for k in ("r_two", "r_full"))
    residual_lim = max(0.01, 1.15 * residual_max)

    fig, axes = plt.subplots(2, 2, figsize=(10, 6.8), sharex="col",
                             gridspec_kw={"height_ratios": [2.2, 1.15]})
    colors = {"two": "#2563eb", "full": "#dc2626"}
    labels = {"two": "两束截断", "full": "完整往返"}
    for col, angle in enumerate((10.0, 15.0)):
        top, bottom = axes[0, col], axes[1, col]
        d = data[angle]
        top.plot(d["x"], d["y"], color="#111827", linewidth=1.7, label="实测")
        top.plot(d["x"], d["two"], color=colors["two"], linewidth=1.7, label=labels["two"])
        top.plot(d["x"], d["full"], color=colors["full"], linewidth=1.7, label=labels["full"])
        bottom.plot(d["x"], [100 * z for z in d["r_two"]], color=colors["two"], linewidth=1.7)
        bottom.plot(d["x"], [100 * z for z in d["r_full"]], color=colors["full"], linewidth=1.7)
        bottom.axhline(0, color="#111827", linewidth=1.5)
        top.set_ylim(top_min - pad, top_max + pad)
        bottom.set_ylim(-100 * residual_lim, 100 * residual_lim)
        top.grid(alpha=0.3, linestyle="--")
        bottom.grid(alpha=0.3, linestyle="--")
        top.text(0.02, 1.12, f"({'a' if col == 0 else 'b'}) {angle:g}° · 附件{col + 3}",
                 transform=top.transAxes, fontsize=12, ha="left", va="bottom",
                 color="#172033")
        top.text(0.98, 1.12, f"{len(d['x'])}点", transform=top.transAxes,
                 fontsize=11, ha='right', va='bottom')
        rms_two = 100 * math.sqrt(sum(z * z for z in d["r_two"]) / len(d["r_two"]))
        rms_full = 100 * math.sqrt(sum(z * z for z in d["r_full"]) / len(d["r_full"]))
        note = f"两束 {rms_two:.4f} → 完整 {rms_full:.4f}"
        top.text(0.02, 1.025, note, transform=top.transAxes, fontsize=12,
                 ha="left", va="bottom", color="#334155")
        bottom.text(0.02, 1.04, f'({"c" if col == 0 else "d"}) 误差降低 {rms_two - rms_full:.4f} 个百分点',
                    transform=bottom.transAxes, fontsize=11, va='bottom', color='#166534')
        top.set_ylabel("反射率（比例）")
        bottom.set_ylabel("残差：预测-实测\n（百分点）")
        bottom.set_xlabel("波数（cm^-1）")
        for axis in (top, bottom):
            axis.yaxis.set_major_formatter(FuncFormatter(lambda value, position: f'{value:g}'))
            axis.spines['top'].set_visible(False)
            axis.spines['right'].set_visible(False)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=3, fontsize=11, frameon=False)
    fig.text(0.5, 0.02, "第1折块6；均方根误差单位：百分点\n残差＝预测-实测：正值为预测高估，负值为预测低估",
             ha="center", fontsize=11, color="#475569")
    fig.tight_layout(rect=[0, 0.09, 1, 0.90], h_pad=1.6)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=200, bbox_inches="tight", metadata=figure_metadata(OUT.stem))
    plt.close(fig)
    print(f'硅双角谱：当前配对预测；残差=预测减实测，最大核验差{residual_gap}；{summary}')


if __name__ == "__main__":
    signal.alarm(150)
    main()
