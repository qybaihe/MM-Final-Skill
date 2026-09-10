"""图07：色散斜率改变局部峰距。"""

from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location

import matplotlib.pyplot as plt
import numpy as np

_COMMON_SPEC = spec_from_file_location("_plot_common", Path(__file__).resolve().parents[1] / "公共" / "绘图公共.py")
_COMMON = module_from_spec(_COMMON_SPEC)
_COMMON_SPEC.loader.exec_module(_COMMON)
read_json = _COMMON.read_json
save_figure = _COMMON.save_figure
clean_axes = _COMMON.clean_axes
check_plot_budget = _COMMON.check_plot_budget


def main():
    check_plot_budget()
    design = read_json("数据/问题1_冻结合成输入/合成设计.json")
    anchor = design["灵敏度锚例"]
    sigma = np.linspace(1200.0, 3800.0, 260)
    slopes = np.linspace(-0.16, 0.16, 181)
    sigma_grid, slope_grid = np.meshgrid(sigma, slopes)
    x = (sigma_grid - 2200.0) / 1800.0
    n = anchor["折射率基值"] + slope_grid * x
    q = np.sqrt(n * n - np.sin(np.deg2rad(10.0)) ** 2)
    q0 = np.sqrt(anchor["折射率基值"] ** 2 - np.sin(np.deg2rad(10.0)) ** 2)
    d_cm = anchor["厚度_微米"] * 1e-4
    local_phase_slope = q + sigma_grid * n * slope_grid / 1800.0 / q
    true_spacing = 1.0 / (2.0 * d_cm * local_phase_slope)
    constant_spacing = 1.0 / (2.0 * d_cm * q0)
    deviation = (constant_spacing - true_spacing) / true_spacing * 100.0

    fig, ax = plt.subplots(figsize=(12.8, 7.2))
    levels = [-12, -8, -5, -3, -1, 0, 1, 3, 5, 8, 12]
    cf = ax.contourf(sigma_grid, slope_grid, deviation, levels=levels, cmap="RdBu_r", extend="both")
    contour = ax.contour(sigma_grid, slope_grid, deviation, levels=[-5, 0, 5], colors=["#6b233c", "#17202a", "#245b45"], linewidths=1.5)
    ax.clabel(contour, fmt=lambda value: f"{value:g}%", fontsize=11)
    ax.axhspan(-0.08, 0.08, color="#62a87c", alpha=0.12, zorder=2)
    ax.axhline(0, color="#17202a", lw=1.5, ls="--", alpha=0.8)
    ax.axhline(-0.08, color="#2d8a65", lw=1.5, ls=":")
    ax.axhline(0.08, color="#2d8a65", lw=1.5, ls=":")
    ax.text(0.02, 0.95, f"合成锚例：d={anchor['厚度_微米']:g} μm，n₀={anchor['折射率基值']:g}，θ=10°",
            transform=ax.transAxes, ha="left", va="top", fontsize=11, color="#243b53",
            bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#c9d2db", alpha=0.92))
    ax.text(0.98, 0.06, "绿色带：冻结匹配斜率 0、±0.08\n扩展失配另含曲率 ±0.12 或返回束数 8",
            transform=ax.transAxes, ha="right", va="bottom", fontsize=11, color="#245b45",
            bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#9dc8aa", alpha=0.92))
    ax.set_xlabel("波数 (cm$^{-1}$)")
    ax.set_ylabel("色散斜率")
    ax.set_xlim(1200, 3800)
    ax.set_ylim(-0.16, 0.16)
    cbar = fig.colorbar(cf, ax=ax, pad=0.02)
    cbar.set_label("常折射率峰距相对偏差 (%)")
    clean_axes(ax)
    fig.subplots_adjust(left=0.09, right=0.91, bottom=0.12, top=0.98)
    save_figure(fig, problem=1, public_name="色散斜率改变局部峰距")


if __name__ == "__main__":
    main()
