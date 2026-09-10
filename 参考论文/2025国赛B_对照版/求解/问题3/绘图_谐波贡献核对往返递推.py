from pathlib import Path
import json

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "求解" / "问题3" / "结果" / "谐波贡献诊断.json"
OUT = ROOT / "求解" / "问题3" / "图片" / "谐波贡献核对往返递推.png"

plt.rcParams["font.sans-serif"] = ["FandolHei", "Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams.update({"font.size": 9.4, "axes.labelsize": 9.4,
                      "xtick.labelsize": 9.2, "ytick.labelsize": 9.2,
                      "legend.fontsize": 9.0})


def load_rows():
    result = json.loads(SOURCE.read_text(encoding="utf-8"))
    if result.get("数据源声明") != "数据源：原型路线2辅助诊断；非正式主方法结果":
        raise ValueError("谐波图数据源声明不匹配")
    return result, result["有效行"]


def as_matrix(rows, metric):
    selected = [r for r in rows if r["指标"] == metric]
    labels = [f"{r['入射角_度']:g}°" for r in selected]
    columns = ["1→2", "2→3", "3→4"]
    matrix = np.full((len(selected), len(columns)), np.nan, dtype=float)
    for i, row in enumerate(selected):
        for j, column in enumerate(columns):
            if column in row["数值"]:
                matrix[i, j] = float(row["数值"][column])
    return matrix, labels, columns


def style(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_xlim(-0.55, 2.55)
    ax.set_ylim(-0.55, 1.55)
    ax.set_xticks([0, 1, 2], [r"$1\to2$", r"$2\to3$", r"$3\to4$"])
    ax.tick_params(length=0)
    ax.grid(alpha=0.25, linestyle="--", linewidth=0.7)


def draw_sparse(ax, matrix, labels, columns, cmap, norm, value_fmt, colorbar_label):
    y, x = np.where(np.isfinite(matrix))
    values = matrix[y, x]
    cmap_obj = plt.get_cmap(cmap).copy()
    cmap_obj.set_bad("#f8fafc")
    mesh = ax.pcolormesh(np.arange(matrix.shape[1] + 1) - 0.5,
                         np.arange(matrix.shape[0] + 1) - 0.5,
                         np.ma.masked_invalid(matrix), cmap=cmap_obj, norm=norm,
                         shading="flat", edgecolors="white", linewidth=1.2, zorder=3)
    for row in range(matrix.shape[0]):
        for col in range(matrix.shape[1]):
            if not np.isfinite(matrix[row, col]):
                ax.text(col, row, "—", ha="center", va="center", fontsize=10,
                        color="#94a3b8", zorder=4)
    for xi, yi, value in zip(x, y, values):
        text_color = "white" if norm(value) > 0.55 else "#172033"
        ax.text(xi, yi, value_fmt.format(value), ha="center", va="center",
                fontsize=9.0, color=text_color, zorder=4)
    ax.set_yticks([0, 1], labels)
    ax.set_xlabel("相邻谐波阶次")
    cbar = plt.colorbar(mesh, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label(colorbar_label, fontsize=9.0)
    cbar.ax.tick_params(labelsize=8.5)
    style(ax)


def main():
    result, rows = load_rows()
    amp, labels, columns = as_matrix(rows, "幅值比")
    phase, _, _ = as_matrix(rows, "相位增量_弧度")
    nonempty = int(np.isfinite(amp).sum())
    if nonempty != int(result["非空单元计数"]):
        raise ValueError("有效单元计数与结果 JSON 不一致")

    fig, axes = plt.subplots(2, 1, figsize=(7.6, 5.8), sharex=True,
                             gridspec_kw={"hspace": 0.46})
    amp_norm = plt.Normalize(vmin=0.0, vmax=max(0.5, float(np.nanmax(amp))))
    phase_norm = plt.Normalize(vmin=-np.pi, vmax=np.pi)
    draw_sparse(axes[0], amp, labels, columns, "viridis", amp_norm,
                "{:.3f}", r"$|C_{m+1}/C_m|$")
    draw_sparse(axes[1], phase, labels, columns, "twilight", phase_norm,
                "{:+.2f}", r"$\Delta\phi$（弧度）")
    axes[0].text(0.01, 1.08, "A  有效复比幅值", transform=axes[0].transAxes,
                 fontsize=10.2, color="#172033", va="bottom")
    axes[1].text(0.01, 1.08, "B  有效复比相位增量", transform=axes[1].transAxes,
                 fontsize=10.2, color="#172033", va="bottom")
    axes[0].set_ylabel("入射角")
    axes[1].set_ylabel("入射角")
    fig.text(0.5, 0.955, "原型复数比诊断：硅折2·主窗口·10°/15°",
             ha="center", va="top", fontsize=10.8, color="#172033")
    fig.text(0.5, 0.025,
             f"{result['数据源声明']}；{result['保存说明']}。无同源理论 $u$，不作“观测−递推预期”比较。",
             ha="center", fontsize=8.8, color="#475569")
    fig.tight_layout(rect=[0.02, 0.08, 0.98, 0.93])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"原型复数比诊断图已写出：有效单元 {nonempty} 个；数据源为路线2辅助诊断")


if __name__ == "__main__":
    main()
