#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""问题1红队独立复算。

只复算结果声明中的三个真实附件核心量，不读取建模师脚本或其结果目录。
方法：在 2800--3600 cm^-1 窗口内，把反射率响应和每个候选周期的
正余弦列分别对同一个二次基线做最小二乘残差化，再最大化二元正弦投影能量。
"""

from __future__ import annotations

import json
import math
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "数据"
OUT_DIR = ROOT / "求解" / "问题1" / "红队结果"
WINDOW_LOW = 2800.0
WINDOW_HIGH = 3600.0
PERIOD_LOW = 100.0
PERIOD_HIGH = 600.0
COARSE_STEP = 0.25
FINE_HALF_WIDTH = 0.75
FINE_STEP = 0.002
CHUNK_SIZE = 512
TIME_BUDGET_SECONDS = 600.0


def _xlsx_with_openpyxl(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """优先使用 openpyxl 读取两列数值；环境没有该包时由 XML 后备读取。"""
    import openpyxl  # type: ignore

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = book.active
        rows = []
        for row in sheet.iter_rows(min_row=2, max_col=2, values_only=True):
            if row[0] is None and row[1] is None:
                continue
            rows.append((float(row[0]), float(row[1])))
    finally:
        book.close()
    if not rows:
        raise ValueError(f"附件没有数据行: {path}")
    arr = np.asarray(rows, dtype=float)
    return arr[:, 0], arr[:, 1]


_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _column_index(cell_ref: str) -> int:
    letters = []
    for char in cell_ref:
        if char.isalpha():
            letters.append(char.upper())
        else:
            break
    result = 0
    for char in letters:
        result = result * 26 + ord(char) - ord("A") + 1
    return result


def _xml_text(element: ET.Element) -> str:
    return "".join(element.itertext())


def _xlsx_with_xml(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """openpyxl 不可用时的只读 XLSX 后备解析器。"""
    with zipfile.ZipFile(path, "r") as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{{{_MAIN_NS}}}si"):
                shared.append(_xml_text(item))

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_map = {}
        for relation in rels:
            rel_id = relation.attrib.get("Id")
            target = relation.attrib.get("Target", "")
            if target.startswith("/"):
                target = target.lstrip("/")
            elif not target.startswith("xl/"):
                target = "xl/" + target
            rel_map[rel_id] = target
        sheet = workbook.find(f"{{{_MAIN_NS}}}sheets/{{{_MAIN_NS}}}sheet")
        if sheet is None:
            raise ValueError(f"找不到工作表: {path}")
        rel_id = sheet.attrib.get(f"{{{_REL_NS}}}id")
        sheet_path = rel_map.get(rel_id)
        if sheet_path is None:
            raise ValueError(f"找不到工作表关系: {path}")
        worksheet = ET.fromstring(archive.read(sheet_path))
        rows = []
        data_root = worksheet.find(f"{{{_MAIN_NS}}}sheetData")
        if data_root is None:
            raise ValueError(f"找不到工作表数据: {path}")
        for row in data_root.findall(f"{{{_MAIN_NS}}}row"):
            values = {}
            for cell in row.findall(f"{{{_MAIN_NS}}}c"):
                ref = cell.attrib.get("r", "")
                col = _column_index(ref)
                value_node = cell.find(f"{{{_MAIN_NS}}}v")
                if value_node is None:
                    inline = cell.find(f"{{{_MAIN_NS}}}is")
                    raw = _xml_text(inline) if inline is not None else ""
                else:
                    raw = value_node.text or ""
                if cell.attrib.get("t") == "s":
                    raw = shared[int(raw)]
                values[col] = raw
            if 1 in values and 2 in values:
                try:
                    rows.append((float(values[1]), float(values[2])))
                except (TypeError, ValueError):
                    # 第一行表头不是数值，数据行仍须全部可解析。
                    continue
    if not rows:
        raise ValueError(f"附件没有可解析的数值行: {path}")
    arr = np.asarray(rows, dtype=float)
    return arr[:, 0], arr[:, 1]


def read_xlsx(path: Path) -> tuple[np.ndarray, np.ndarray]:
    try:
        return _xlsx_with_openpyxl(path)
    except ModuleNotFoundError as exc:
        if exc.name != "openpyxl":
            raise
        return _xlsx_with_xml(path)


def residualize(values: np.ndarray, baseline: np.ndarray, baseline_pinv: np.ndarray) -> np.ndarray:
    """从列向量或多列矩阵中剔除同一个二次基线子空间。"""
    return values - baseline @ (baseline_pinv @ values)


def projection_scores(
    periods: np.ndarray,
    sigma: np.ndarray,
    response_resid: np.ndarray,
    baseline: np.ndarray,
    baseline_pinv: np.ndarray,
    started: float,
) -> np.ndarray:
    """计算候选周期的二元正弦投影能量，分块避免一次性占满内存。"""
    scores = np.empty(periods.size, dtype=float)
    two_pi_sigma = 2.0 * math.pi * sigma
    response_energy = float(np.dot(response_resid, response_resid))
    if response_energy <= 0.0:
        raise ValueError("基线残差后的反射率没有可投影能量")

    for start in range(0, periods.size, CHUNK_SIZE):
        if time.monotonic() - started > TIME_BUDGET_SECONDS:
            raise TimeoutError("超过复算脚本时间预算")
        stop = min(start + CHUNK_SIZE, periods.size)
        block = periods[start:stop]
        phase = two_pi_sigma[:, None] / block[None, :]
        cos_part = np.cos(phase)
        sin_part = np.sin(phase)
        cos_resid = residualize(cos_part, baseline, baseline_pinv)
        sin_resid = residualize(sin_part, baseline, baseline_pinv)

        g00 = np.einsum("ij,ij->j", cos_resid, cos_resid)
        g11 = np.einsum("ij,ij->j", sin_resid, sin_resid)
        g01 = np.einsum("ij,ij->j", cos_resid, sin_resid)
        b0 = np.einsum("i,ij->j", response_resid, cos_resid)
        b1 = np.einsum("i,ij->j", response_resid, sin_resid)
        determinant = g00 * g11 - g01 * g01
        numerator = b0 * b0 * g11 - 2.0 * b0 * b1 * g01 + b1 * b1 * g00
        block_scores = np.divide(
            numerator,
            determinant * response_energy,
            out=np.zeros_like(numerator),
            where=determinant > 1e-14,
        )
        scores[start:stop] = block_scores
    return scores


def estimate_period(sigma: np.ndarray, reflectance: np.ndarray, started: float) -> tuple[float, int]:
    mask = (
        np.isfinite(sigma)
        & np.isfinite(reflectance)
        & (sigma >= WINDOW_LOW)
        & (sigma <= WINDOW_HIGH)
    )
    sigma = np.asarray(sigma[mask], dtype=float)
    reflectance = np.asarray(reflectance[mask], dtype=float)
    if sigma.size < 20:
        raise ValueError("窗口内有效数据点不足")
    order = np.argsort(sigma)
    sigma = sigma[order]
    reflectance = reflectance[order]

    # 缩放仅为数值稳定；列空间仍是同一个二次基线 span{1, sigma, sigma^2}。
    centered = (sigma - float(np.mean(sigma))) / max(float(np.ptp(sigma)), 1.0)
    baseline = np.column_stack((np.ones_like(centered), centered, centered * centered))
    baseline_pinv = np.linalg.pinv(baseline)
    response_resid = residualize(reflectance, baseline, baseline_pinv)

    coarse = np.arange(PERIOD_LOW, PERIOD_HIGH + COARSE_STEP * 0.5, COARSE_STEP)
    coarse_scores = projection_scores(
        coarse, sigma, response_resid, baseline, baseline_pinv, started
    )
    coarse_index = int(np.nanargmax(coarse_scores))
    coarse_best = float(coarse[coarse_index])
    fine_low = max(PERIOD_LOW, coarse_best - FINE_HALF_WIDTH)
    fine_high = min(PERIOD_HIGH, coarse_best + FINE_HALF_WIDTH)
    fine = np.arange(fine_low, fine_high + FINE_STEP * 0.5, FINE_STEP)
    fine_scores = projection_scores(
        fine, sigma, response_resid, baseline, baseline_pinv, started
    )
    best_index = int(np.nanargmax(fine_scores))
    best_period = float(fine[best_index])

    # 用极小邻域的三点抛物线作连续化；边界点保留网格解。
    if 0 < best_index < fine.size - 1:
        x = fine[best_index - 1 : best_index + 2]
        y = fine_scores[best_index - 1 : best_index + 2]
        curvature = y[0] - 2.0 * y[1] + y[2]
        if np.isfinite(curvature) and curvature < 0.0:
            offset = 0.5 * (y[0] - y[2]) / curvature
            if abs(offset) <= 1.0:
                best_period += float(offset) * FINE_STEP
    return best_period, int(sigma.size)


def main() -> None:
    started = time.monotonic()
    period_values = {}
    point_counts = {}
    for filename, label in (("附件1.xlsx", "附件1周期_每厘米"), ("附件2.xlsx", "附件2周期_每厘米")):
        sigma, reflectance = read_xlsx(DATA_DIR / filename)
        period, count = estimate_period(sigma, reflectance, started)
        period_values[label] = period
        point_counts[filename] = count

    p1 = period_values["附件1周期_每厘米"]
    p2 = period_values["附件2周期_每厘米"]
    median_period = float(np.median(np.asarray([p1, p2], dtype=float)))
    optical_thickness_product = 5000.0 / median_period

    result = {
        "问题": 1,
        "复算方式": "独立实现，未读建模师代码；二次基线残差化后的正余弦二维投影周期搜索",
        "复算指标": {
            "真实附件表观光学厚度乘积_dq_微米": optical_thickness_product,
            "附件1周期_每厘米": p1,
            "附件2周期_每厘米": p2,
        },
        "口径说明": {
            "真实附件表观光学厚度乘积_dq_微米": (
                "附件1、2各自在2800—3600 cm^-1窗口；反射率原始百分数作响应；"
                "不剔除窗口内有效点；响应与每个候选正余弦列均对同一二次基线做最小二乘残差化；"
                "两附件周期取中位数后按5000/Δσ换算，单位微米，表示d×q而非几何厚度。"
            ),
            "附件1周期_每厘米": (
                "仅附件1；2800—3600 cm^-1窗口；反射率原始百分数；不剔除有效点；"
                "同一二次基线残差化；正余弦二维投影能量最大对应周期，单位cm^-1。"
            ),
            "附件2周期_每厘米": (
                "仅附件2；2800—3600 cm^-1窗口；反射率原始百分数；不剔除有效点；"
                "同一二次基线残差化；正余弦二维投影能量最大对应周期，单位cm^-1。"
            ),
        },
        "数据核验": {
            "窗口下限_cm^-1": WINDOW_LOW,
            "窗口上限_cm^-1": WINDOW_HIGH,
            "附件1窗口有效点数": point_counts["附件1.xlsx"],
            "附件2窗口有效点数": point_counts["附件2.xlsx"],
            "搜索周期范围_cm^-1": [PERIOD_LOW, PERIOD_HIGH],
            "粗搜索步长_cm^-1": COARSE_STEP,
            "局部细化步长_cm^-1": FINE_STEP,
        },
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUT_DIR / "问题1红队复算结果.json"
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(result["复算指标"], ensure_ascii=False))


if __name__ == "__main__":
    main()
