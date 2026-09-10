"""本批绘图共用的只读数据、样式和一次往返模型工具。"""

from __future__ import annotations

import cmath
import json
import math
import posixpath
import time
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
XML_NS = {"表": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

plt.rcParams["font.sans-serif"] = ["FandolHei", "Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["font.size"] = 11
PLOT_BUDGET_SECONDS = 1200.0
_PLOT_START = time.monotonic()


def check_plot_budget():
    """绘图计算的软预算；本批脚本默认远低于20分钟。"""
    if time.monotonic() - _PLOT_START >= PLOT_BUDGET_SECONDS:
        raise TimeoutError("绘图接近20分钟预算，停止继续扩展计算")


def read_json(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def read_xlsx(relative: str):
    """只读附件前两列，避免绘图脚本依赖 Excel 引擎。"""
    path = ROOT / relative
    with ZipFile(path) as book:
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        targets = {node.get("Id"): node.get("Target") for node in rels}
        sheet = workbook.find("表:sheets/表:sheet", XML_NS)
        target = targets[sheet.get("{" + REL_NS + "}id")]
        member = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
        root = ET.fromstring(book.read(member))
        records = []
        for row in root.findall("表:sheetData/表:row", XML_NS):
            number = int(row.get("r"))
            if number == 1:
                continue
            cells = {cell.get("r"): cell for cell in row}
            values = []
            for column in ("A", "B"):
                cell = cells[f"{column}{number}"]
                if cell.get("t", "n") != "n" or cell.find("表:f", XML_NS) is not None:
                    raise ValueError(f"{path.name}含非数值或公式单元格")
                value = float(cell.findtext("表:v", namespaces=XML_NS))
                if not math.isfinite(value):
                    raise ValueError(f"{path.name}含非有限数值")
                values.append(value)
            records.append((number, values[0], values[1]))
    if len(records) != 7469:
        raise ValueError(f"{path.name}数据行数不是7469")
    if any(left[1] >= right[1] for left, right in zip(records, records[1:])):
        raise ValueError(f"{path.name}波数不是严格递增")
    return np.asarray([row[1] for row in records]), np.asarray([row[2] for row in records]), np.asarray([row[0] for row in records])


def save_figure(fig, public_name: str | None = None, problem: int | None = None):
    if problem is not None:
        folder = ROOT / "求解" / f"问题{problem}" / "图片"
    elif public_name is not None:
        folder = ROOT / "求解" / "公共" / "图片"
    else:
        raise ValueError("必须指定公共图或问题号")
    folder.mkdir(parents=True, exist_ok=True)
    name = public_name if public_name is not None else "未命名"
    path = folder / f"{name}.png"
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"已保存：{path}")


def clean_axes(ax, grid=True):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if grid:
        ax.grid(alpha=0.3, linestyle="--")


def arrow_style(**kwargs):
    from matplotlib.patches import FancyArrowPatch

    defaults = {"arrowstyle": "-|>", "mutation_scale": 14, "lw": 1.8}
    defaults.update(kwargs)
    return FancyArrowPatch(**defaults)


def passive_root(value):
    root = cmath.sqrt(value)
    if root.imag < 0 or (abs(root.imag) < 1e-15 and root.real < 0):
        root = -root
    return root


def index_at(sigma, params):
    x = (sigma - 2200.0) / 1800.0
    real = params["折射率基值"] + params.get("色散斜率", 0.0) * x + params.get("色散曲率", 0.0) * x * x
    layer = complex(real, params.get("消光系数", 0.0))
    substrate = layer + complex(params.get("衬底折射率增量", 0.8), 0.0)
    return layer, substrate


def interface_components(sigma, angle, params):
    layer, substrate = index_at(sigma, params)
    indices = (1.0 + 0j, layer, substrate)
    transverse = math.sin(math.radians(angle))
    q = tuple(passive_root(n * n - transverse * transverse) for n in indices)
    components = []
    for polarization in ("垂直", "平行"):
        admittance = q if polarization == "垂直" else tuple(n * n / qj for n, qj in zip(indices, q))
        y0, y1, y2 = admittance
        r01 = (y0 - y1) / (y0 + y1)
        r12 = (y1 - y2) / (y1 + y2)
        r10 = (y1 - y0) / (y1 + y0)
        t01 = 2.0 * y0 / (y0 + y1)
        t10 = 2.0 * y1 / (y1 + y0)
        components.append((r01, t01 * t10 * r12, r10 * r12, q[1]))
    return components


def propagation(sigma, q, thickness_um):
    return cmath.exp(4.0j * math.pi * sigma * q * thickness_um * 1e-4)


def direct_reflectance(sigma, angle, params, thickness_um, returns=1):
    weights = (params.get("垂直偏振权重", 0.5), 1.0 - params.get("垂直偏振权重", 0.5))
    total = 0.0
    for weight, (surface, echo, repeat, q) in zip(weights, interface_components(sigma, angle, params)):
        field = surface
        if returns > 0:
            z = propagation(sigma, q, thickness_um)
            term = echo * z
            for _ in range(int(returns)):
                field += term
                term *= repeat * z
        total += weight * abs(field) ** 2
    return total


def expanded_coefficients(sigma, angle, params):
    weights = (params.get("垂直偏振权重", 0.5), 1.0 - params.get("垂直偏振权重", 0.5))
    surface_power = 0.0
    echo_power = 0.0
    cross = 0j
    components = interface_components(sigma, angle, params)
    for weight, (surface, echo, _repeat, _q) in zip(weights, components):
        surface_power += weight * abs(surface) ** 2
        echo_power += weight * abs(echo) ** 2
        cross += weight * surface.conjugate() * echo
    return sigma, surface_power, echo_power, cross, components[0][3]


def expanded_reflectance(coefficient, thickness_um):
    sigma, surface_power, echo_power, cross, q = coefficient
    z = propagation(sigma, q, thickness_um)
    return surface_power + echo_power * abs(z) ** 2 + 2.0 * (cross * z).real
