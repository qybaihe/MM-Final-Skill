"""图06：场求和与展开强度相符。"""

from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location

import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
from matplotlib.lines import Line2D
import numpy as np

_COMMON_SPEC = spec_from_file_location("_plot_common", Path(__file__).resolve().parents[1] / "公共" / "绘图公共.py")
_COMMON = module_from_spec(_COMMON_SPEC)
_COMMON_SPEC.loader.exec_module(_COMMON)
read_json = _COMMON.read_json
read_xlsx = _COMMON.read_xlsx
direct_reflectance = _COMMON.direct_reflectance
expanded_coefficients = _COMMON.expanded_coefficients
expanded_reflectance = _COMMON.expanded_reflectance
check_plot_budget = _COMMON.check_plot_budget
save_figure = _COMMON.save_figure
clean_axes = _COMMON.clean_axes


def main():
    design = read_json("数据/问题1_冻结合成输入/合成设计.json")
    validation = read_json("求解/问题1/结果/模型验证.json")
    coordinates, _, _ = read_xlsx("数据/附件1.xlsx")
    # 取冻结设计中的厚度、折射率基值、色散斜率和两入射角，逐情景构造核验点。
    base = design["基础情景"]
    thicknesses = base["厚度_微米"]
    index_bases = base["折射率基值"]
    slopes = base["色散斜率"]
    angles = design["角度_度"]
    sample_sigma = coordinates[::64]
    direct_values, expanded_values, errors, angle_values = [], [], [], []
    for thickness in thicknesses:
        for index_base in index_bases:
            for slope in slopes:
                params = {"折射率基值": index_base, "色散斜率": slope, "色散曲率": 0.0,
                          "消光系数": 0.0, "衬底折射率增量": base["衬底折射率增量"],
                          "垂直偏振权重": base["垂直偏振权重"]}
                for angle in angles:
                    for sigma in sample_sigma:
                        check_plot_budget()
                        direct = direct_reflectance(float(sigma), angle, params, thickness, returns=1)
                        expanded = expanded_reflectance(expanded_coefficients(float(sigma), angle, params), thickness)
                        direct_values.append(direct)
                        expanded_values.append(expanded)
                        errors.append(abs(direct - expanded))
                        angle_values.append(angle)
    direct_values = np.asarray(direct_values)
    expanded_values = np.asarray(expanded_values)
    errors = np.asarray(errors)
    fig, ax = plt.subplots(figsize=(8.4, 7.4))
    lower = min(float(direct_values.min()), float(expanded_values.min()))
    upper = max(float(direct_values.max()), float(expanded_values.max()))
    pad = max((upper - lower) * 0.05, 1e-3)
    norm = LogNorm(vmin=max(float(errors.min()), 1e-18), vmax=max(float(errors.max()), 1e-17))
    sc = ax.scatter(direct_values, expanded_values, c=np.maximum(errors, 1e-18), cmap="viridis", norm=norm,
                    s=14, alpha=0.72, linewidths=0)
    ax.add_line(Line2D([lower - pad, upper + pad], [lower - pad, upper + pad],
                       color="#c44e52", lw=2.0, ls="--", label="等值线 y=x"))
    ax.set_xlim(lower - pad, upper + pad)
    ax.set_ylim(lower - pad, upper + pad)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("直接复场求模平方 R")
    ax.set_ylabel("交叉项展开式 R")
    ax.text(0.04, 0.95, "冻结合成情景：3种厚度 × 2种基值 × 2种斜率 × 2个角度",
            transform=ax.transAxes, ha="left", va="top", fontsize=11, color="#243b53",
            bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#c9d2db", alpha=0.92))
    ax.text(0.04, 0.08, f"结果核验最大绝对差：{validation['场展开一致性最大绝对差_比例']:.1e}\n本图点数：{len(errors)}；非实测拟合",
            transform=ax.transAxes, ha="left", va="bottom", fontsize=11, color="#245b45",
            bbox=dict(boxstyle="round,pad=0.25", fc="#eef8f1", ec="#9dc8aa", alpha=0.92))
    cbar = fig.colorbar(sc, ax=ax, pad=0.03)
    cbar.set_label("两种实现的绝对差 |ΔR|（对数色标）")
    ax.legend(frameon=False, loc="lower right")
    clean_axes(ax)
    fig.subplots_adjust(left=0.13, right=0.90, bottom=0.12, top=0.98)
    save_figure(fig, problem=1, public_name="场求和与展开强度相符")


if __name__ == "__main__":
    main()
