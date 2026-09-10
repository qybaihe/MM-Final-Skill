#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""问题2：DA-HOC 双角奇半阶峰链反演（正式脚本，仅由执行岗运行）。"""

import sys
sys.path.insert(0, '/tmp/蜂巢/pylibs')

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.signal import find_peaks, hilbert, savgol_filter


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "数据"
OUT_DIR = ROOT / "求解" / "问题2" / "结果"
LOG_DIR = ROOT / "日志"

ANGLES = (10.0, 15.0)
FILES = {10.0: "附件1.xlsx", 15.0: "附件2.xlsx"}
ANALYSIS_RANGE = (399.6747, 3000.0)
BLIND_RANGE = (1680.0, 1920.0)
CALIBRATION_RANGES = ((399.6747, 620.0), (1500.0, 1680.0), (1920.0, 2100.0))
OVER_100_RANGE = (801.278, 927.1104)
CROSSCHECK_RANGE = (1450.0, 4000.0)
SIGMA_CENTER = 1800.0
SIGMA_SCALE = 1200.0
N_ANCHOR = 2.60

BASELINE_SCALES_CM = (400.0, 600.0, 800.0)
MAIN_PERIOD_BOUNDS_CM = (120.0, 500.0)
LOCAL_WEAK_PERIOD_RANGE_CM = (6.0, 15.0)
THICKNESS_BOUNDS_UM = (1.0, 120.0)
DEFAULT_CONFIG = {"基线尺度_cm-1": 600.0, "平滑尺度_cm-1": 24.0,
                  "显著性因子": 2.5, "漏阶惩罚": 0.11}
MAX_CANDIDATES = 140
MAX_CANDIDATES_SIM = 90
MAX_PREDECESSORS = 14
MAX_SUPPORTED_HALF_STEP = 9
ALLOWED_ODD_STEPS = np.arange(1, MAX_SUPPORTED_HALF_STEP + 1, 2, dtype=int)
PREFERRED_SEGMENT_CHAIN_NODES = 7
MIN_SEGMENT_CHAIN_NODES = 4

# 17.5分钟停止启动新阶段，19分钟绝对停止，留出落盘与异常处理余量。
SOFT_BUDGET_SECONDS = 1050.0
HARD_BUDGET_SECONDS = 1140.0
SEMISYNTHETIC_CALIBRATION = 60
SEMISYNTHETIC_VALIDATION = 100
ABNORMAL_DOWNWEIGHT = 0.35
SUBBAND_TOLERANCE_PERCENT = 15.0
ALTERNATIVE_TOLERANCE_PERCENT = 25.0
SENSITIVITY_TOLERANCE_PERCENT = 10.0
COVERAGE_ACCEPTANCE = 0.90
RNG_SEED = 20260827
DISPERSION_PRIOR_C1 = 0.10
DISPERSION_PRIOR_C2 = 0.06
CONFIG_STABILITY_SHORTLIST = 2
CANONICAL_BLIND_METRIC_ID = "盲块_宽尺度主周期链_冻结训练截距_分类双向MdAE_v3"


class TimeBudgetReached(RuntimeError):
    """接近20分钟硬上限时主动结束并保留已落盘结果。"""


def native(value):
    if isinstance(value, dict):
        return {str(k): native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [native(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer, np.bool_)):
        return value.item()
    return value


def atomic_write_json(filename, payload):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / filename
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(native(payload), handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def atomic_write_text(path, text_value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        handle.write(text_value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def write_authoritative_log(summary):
    """每次正式执行覆盖权威日志，避免旧日志与新JSON混配。"""
    state = summary.get("运行状态", "未知")
    lines = [
        f"UTC时间={datetime.now(timezone.utc).isoformat()}",
        "问题=2",
        f"运行状态={state}",
        f"指标口径ID={CANONICAL_BLIND_METRIC_ID}",
        f"共享厚度_um={summary.get('共享厚度_um')}",
        f"附件1_10度独立厚度_um={summary.get('附件1_10度独立厚度_um')}",
        f"附件2_15度独立厚度_um={summary.get('附件2_15度独立厚度_um')}",
        f"盲块双向峰谷MdAE_cm-1={summary.get('盲块双向峰谷MdAE_cm-1')}",
        f"实际用时秒={summary.get('实际用时秒')}",
        "权威结果=求解/问题2/结果/汇总结果.json",
    ]
    if summary.get("说明"):
        lines.append(f"说明={summary['说明']}")
    atomic_write_text(LOG_DIR / "执行_问题2.log", "\n".join(lines) + "\n")


def elapsed(t0):
    return time.perf_counter() - t0


def budget_guard(t0, allow_soft=False):
    used = elapsed(t0)
    limit = HARD_BUDGET_SECONDS if allow_soft else SOFT_BUDGET_SECONDS
    if used >= limit:
        raise TimeBudgetReached(f"已用时{used:.1f}秒，触发{limit:.0f}秒主动收敛线")


def write_status(t0, stage, state="运行中", note=""):
    atomic_write_json("00_运行状态.json", {
        "问题": 2,
        "方法": "DA-HOC 双角奇半阶峰链",
        "当前阶段": stage,
        "运行状态": state,
        "说明": note,
        "主动收敛线秒": SOFT_BUDGET_SECONDS,
        "绝对停止线秒": HARD_BUDGET_SECONDS,
        "实际用时秒": round(elapsed(t0), 3),
    })


def odd_window(n, dx, width_cm, minimum=7):
    window = max(minimum, int(round(width_cm / max(dx, 1e-9))))
    if window % 2 == 0:
        window += 1
    largest = n if n % 2 == 1 else n - 1
    return max(5, min(window, largest))


def wide_scale_residual(x, y, baseline_width_cm):
    """宽尺度基线只描述慢变包络，禁止用短于主条纹周期的窗口扣除条纹。"""
    dx = float(np.median(np.diff(x)))
    baseline = savgol_filter(
        y, odd_window(x.size, dx, baseline_width_cm, 31), 2, mode="interp")
    return baseline, np.asarray(y, float) - baseline


def estimate_period_at_scale(sigma, y, mask, baseline_width_cm):
    """在原波数坐标上估计120—500 cm-1主周期，并审计6—15 cm-1弱波动。"""
    rows = []
    for indices in contiguous_segments(mask):
        x = sigma[indices]
        values = y[indices]
        span = float(x[-1] - x[0])
        if x.size < 120 or span < 1.5 * MAIN_PERIOD_BOUNDS_CM[0]:
            continue
        grid = np.linspace(x[0], x[-1], x.size)
        signal = np.interp(grid, x, values)
        dx = float(grid[1] - grid[0])
        _, residual = wide_scale_residual(grid, signal, baseline_width_cm)
        tapered = residual * np.hanning(residual.size)
        frequency = np.fft.rfftfreq(tapered.size, d=dx)
        power = np.abs(np.fft.rfft(tapered)) ** 2
        main_valid = ((frequency >= 1.0 / MAIN_PERIOD_BOUNDS_CM[1]) &
                      (frequency <= 1.0 / MAIN_PERIOD_BOUNDS_CM[0]))
        if not np.any(main_valid):
            continue
        main_indices = np.flatnonzero(main_valid)
        main_index = int(main_indices[np.argmax(power[main_valid])])
        main_power = float(power[main_index])
        period = float(1.0 / frequency[main_index])
        weak_valid = ((frequency >= 1.0 / LOCAL_WEAK_PERIOD_RANGE_CM[1]) &
                      (frequency <= 1.0 / LOCAL_WEAK_PERIOD_RANGE_CM[0]))
        weak_power = float(np.max(power[weak_valid])) if np.any(weak_valid) else 0.0
        rows.append({
            "波段_cm-1": [float(x[0]), float(x[-1])],
            "主周期_cm-1": period,
            "主频功率": main_power,
            "6至15cm-1弱波动最大功率": weak_power,
            "弱波动相对主频功率": weak_power / max(main_power, 1e-12),
            "权重": max(main_power, 1e-12) * span,
        })
    if not rows:
        raise ValueError(f"基线尺度{baseline_width_cm:.0f}cm-1未得到可用主周期")
    periods = np.asarray([row["主周期_cm-1"] for row in rows], float)
    weights = np.asarray([row["权重"] for row in rows], float)
    order = np.argsort(periods)
    cumulative = np.cumsum(weights[order])
    selected = order[int(np.searchsorted(cumulative, 0.5 * cumulative[-1]))]
    return float(periods[selected]), rows


def multiscale_period_audit(sigma, y, mask):
    """400/600/800 cm-1三尺度必须保留同一低频主条纹。"""
    rows = []
    for width in BASELINE_SCALES_CM:
        period, blocks = estimate_period_at_scale(sigma, y, mask, width)
        rows.append({"基线尺度_cm-1": width, "主周期_cm-1": period,
                     "分块频谱审计": blocks})
    periods = np.asarray([row["主周期_cm-1"] for row in rows], float)
    center = float(np.median(periods))
    spread = 100.0 * float(np.max(np.abs(periods / center - 1.0)))
    return {
        "方法": "分别以400/600/800 cm-1 Savitzky-Golay宽基线保留低频条纹；仅在120—500 cm-1周期带选主频",
        "尺度结果": rows,
        "冻结主周期_cm-1": center,
        "三尺度最大相对偏差_%": spread,
        "局部弱波动拒绝带_cm-1": list(LOCAL_WEAK_PERIOD_RANGE_CM),
        "主周期是否避开弱波动带": bool(center > LOCAL_WEAK_PERIOD_RANGE_CM[1]),
    }


def read_data():
    spectra = {}
    for angle in ANGLES:
        path = DATA_DIR / FILES[angle]
        frame = pd.read_excel(path, sheet_name="Sheet1", header=0, engine="openpyxl")
        expected = ["波数 (cm-1)", "反射率 (%)"]
        if list(frame.columns) != expected:
            raise ValueError(f"{path.name}列名异常：{list(frame.columns)}")
        sigma = pd.to_numeric(frame.iloc[:, 0], errors="raise").to_numpy(float)
        reflectance = pd.to_numeric(frame.iloc[:, 1], errors="raise").to_numpy(float)
        if sigma.size != 7469 or np.any(~np.isfinite(sigma)) or np.any(~np.isfinite(reflectance)):
            raise ValueError(f"{path.name}行数或数值完整性与数据档案不一致")
        if np.any(np.diff(sigma) <= 0):
            raise ValueError(f"{path.name}波数未严格递增")
        spectra[angle] = reflectance
    sigma = pd.read_excel(DATA_DIR / FILES[10.0], sheet_name="Sheet1", header=0,
                          engine="openpyxl").iloc[:, 0].to_numpy(float)
    sigma15 = pd.read_excel(DATA_DIR / FILES[15.0], sheet_name="Sheet1", header=0,
                            engine="openpyxl").iloc[:, 0].to_numpy(float)
    if not np.array_equal(sigma, sigma15):
        raise ValueError("附件1/2公共波数键未逐值一致")
    return sigma, spectra


def interval_mask(sigma, ranges):
    mask = np.zeros(sigma.size, dtype=bool)
    for lo, hi in ranges:
        mask |= (sigma >= lo) & (sigma <= hi)
    return mask


def make_masks(sigma, include_first=True, exclude_over100=False):
    analysis = (sigma >= ANALYSIS_RANGE[0]) & (sigma <= ANALYSIS_RANGE[1])
    blind = analysis & (sigma >= BLIND_RANGE[0]) & (sigma <= BLIND_RANGE[1])
    calibration = analysis & interval_mask(sigma, CALIBRATION_RANGES) & ~blind
    fit = analysis & ~blind & ~calibration
    final_train = analysis & ~blind
    if not include_first:
        fit[0] = calibration[0] = final_train[0] = False
    if exclude_over100:
        abnormal = (sigma >= OVER_100_RANGE[0]) & (sigma <= OVER_100_RANGE[1])
        fit &= ~abnormal
        calibration &= ~abnormal
        final_train &= ~abnormal
    return {"分析": analysis, "拟合": fit, "校准": calibration,
            "盲块": blind, "最终训练": final_train}


def contiguous_segments(mask):
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    cuts = np.flatnonzero(np.diff(idx) > 1) + 1
    return [part for part in np.split(idx, cuts) if part.size >= 9]


def local_quadratic_position(x, y, index):
    lo, hi = max(0, index - 2), min(x.size, index + 3)
    xx, yy = x[lo:hi], y[lo:hi]
    center = float(x[index])
    if xx.size < 3:
        return center
    coef = np.polyfit(xx - center, yy, 2)
    if abs(coef[0]) < 1e-14:
        return center
    vertex = center - coef[1] / (2.0 * coef[0])
    return float(vertex if xx.min() <= vertex <= xx.max() else center)


def segment_extrema(x, y, config, block_id, period_hint, fixed_evaluator=False,
                    reliability_fn=None):
    dx = float(np.median(np.diff(x)))
    base_width = float(config.get("基线尺度_cm-1", 600.0))
    if base_width < 400.0:
        raise ValueError("主峰链基线尺度不得小于400 cm-1")
    baseline, residual = wide_scale_residual(x, y, base_width)
    del baseline
    smooth_width = (max(20.0, 0.08 * period_hint) if fixed_evaluator else
                    max(config["平滑尺度_cm-1"], 0.08 * period_hint))
    smooth = savgol_filter(residual, odd_window(x.size, dx, smooth_width), 3, mode="interp")
    diff = np.diff(smooth)
    noise = 1.4826 * np.median(np.abs(diff - np.median(diff))) + 1e-10
    iqr = float(np.percentile(smooth, 75) - np.percentile(smooth, 25))
    factor = 3.5 if fixed_evaluator else config["显著性因子"]
    prominence = max(factor * noise, (0.060 if fixed_evaluator else 0.045) * max(iqr, 1e-9))
    # find_peaks的distance约束同类极值；取主周期的45%，从候选层拒绝6—15 cm-1密集弱波动。
    minimum_same_type_distance = max(45.0, 0.45 * period_hint)
    distance = max(3, int(round(minimum_same_type_distance / dx)))
    candidates = []
    for kind, signal in ((1, smooth), (-1, -smooth)):
        indices, info = find_peaks(signal, prominence=prominence, distance=distance)
        for index, prom in zip(indices, info["prominences"]):
            position = local_quadratic_position(x, smooth, int(index))
            candidates.append({
                "波数": position,
                "类型": int(kind),
                "显著度": float(prom),
                "阈值": float(prominence),
                "分块": int(block_id),
                "主周期先验_cm-1": float(period_hint),
                "主半周期先验_cm-1": float(0.5 * period_hint),
                "同类极值最小间距_cm-1": float(minimum_same_type_distance),
                "可靠性权重": float(reliability_fn(position) if reliability_fn else 1.0),
            })
    return sorted(candidates, key=lambda item: item["波数"])


def generate_candidates(sigma, y, mask, config, max_candidates=MAX_CANDIDATES,
                        fixed_evaluator=False, reliability_fn=None, period_hint=260.0):
    candidates = []
    for block_id, indices in enumerate(contiguous_segments(mask)):
        candidates.extend(segment_extrema(sigma[indices], y[indices], config, block_id,
                                          period_hint,
                                          fixed_evaluator=fixed_evaluator,
                                          reliability_fn=reliability_fn))
    if len(candidates) > max_candidates:
        quality = lambda item: item["显著度"] / max(item["阈值"], 1e-12)
        block_ids = sorted({item["分块"] for item in candidates})
        reserve = max(8, min(24, max_candidates // max(2 * len(block_ids), 1)))
        kept = []
        for block_id in block_ids:
            local = [item for item in candidates if item["分块"] == block_id]
            kept.extend(sorted(local, key=quality, reverse=True)[:reserve])
        kept_ids = {id(item) for item in kept}
        remaining = sorted([item for item in candidates if id(item) not in kept_ids],
                           key=quality, reverse=True)
        kept.extend(remaining[:max(0, max_candidates - len(kept))])
        candidates = sorted(kept[:max_candidates], key=lambda item: item["波数"])
    return candidates


def estimate_half_spacing(candidates):
    stored = [item.get("主半周期先验_cm-1") for item in candidates
              if item.get("主半周期先验_cm-1") is not None]
    if stored:
        return float(np.median(stored))
    gaps = []
    for left, right in zip(candidates[:-1], candidates[1:]):
        gap = right["波数"] - left["波数"]
        if left["类型"] != right["类型"] and left["分块"] == right["分块"]:
            gaps.append(gap)
    if not gaps:
        raise ValueError("候选中没有可用于估计半阶间距的异类相邻极值")
    return float(np.median(gaps))


def phase_coordinate(params, sigma, angle, n_anchor=N_ANCHOR):
    d_um, c1, c2 = params[:3]
    intercept_map = params[3]
    z = (np.asarray(sigma, float) - SIGMA_CENTER) / SIGMA_SCALE
    n_value = n_anchor + c1 * z + c2 * z * z
    eta = np.sqrt(np.maximum(n_value * n_value - np.sin(np.deg2rad(angle)) ** 2, 1e-12))
    return 4.0e-4 * d_um * np.asarray(sigma, float) * eta + intercept_map[angle]


def choose_odd_step(gap, base_gap, expected=None):
    """跳阶只由间距和物理先验判定；漏阶惩罚仅进入链总代价，避免双重计罚翻转。"""
    normalized = gap / (ALLOWED_ODD_STEPS * base_gap)
    costs = np.log(np.maximum(normalized, 1e-12)) ** 2
    if expected is not None:
        costs += 0.12 * (ALLOWED_ODD_STEPS - expected) ** 2
    return int(ALLOWED_ODD_STEPS[np.argmin(costs)])


def dynamic_peak_chain(candidates, config, t0, prior=None, min_blocks=2,
                       min_nodes=PREFERRED_SEGMENT_CHAIN_NODES):
    """二阶动态规划；边状态保留上一归一化半阶间距，限制前驱数以守住预算。"""
    n = len(candidates)
    min_nodes = max(MIN_SEGMENT_CHAIN_NODES, int(min_nodes))
    if n < min_nodes:
        raise ValueError(f"宽松候选少于{min_nodes}个，无法形成峰链")
    base_gap = estimate_half_spacing(candidates)

    def node_cost(index):
        item = candidates[index]
        quality = np.clip(item["显著度"] / max(3.0 * item["阈值"], 1e-12), 0.0, 5.0)
        cost = 0.34 / (quality + 0.16) - 0.65
        cost += 0.85 * (1.0 - float(item.get("可靠性权重", 1.0)))
        if prior is not None:
            p = float(prior(item["波数"]))
            cost += 0.9 * abs(p - np.rint(p)) ** 2
        return float(cost)

    states = {}
    for j in range(1, n):
        budget_guard(t0)
        for i in range(max(0, j - 2 * MAX_PREDECESSORS), j):
            if candidates[i]["类型"] == candidates[j]["类型"]:
                continue
            gap = candidates[j]["波数"] - candidates[i]["波数"]
            expected = None if prior is None else max(1, int(round(abs(
                prior(candidates[j]["波数"]) - prior(candidates[i]["波数"])))))
            step = choose_odd_step(gap, base_gap, expected)
            norm_gap = gap / step
            states[(i, j)] = {
                "代价": (node_cost(i) + node_cost(j) +
                       ((norm_gap - base_gap) / (0.55 * base_gap)) ** 2 +
                       config["漏阶惩罚"] * (step - 1)),
                "前驱": None, "步长": step, "归一间距": norm_gap, "长度": 2,
            }

    for j in range(1, n - 1):
        budget_guard(t0)
        incoming = [(edge, state) for edge, state in states.items() if edge[1] == j]
        incoming = sorted(incoming, key=lambda pair: pair[1]["代价"])[:MAX_PREDECESSORS]
        for k in range(j + 1, n):
            if candidates[j]["类型"] == candidates[k]["类型"]:
                continue
            gap = candidates[k]["波数"] - candidates[j]["波数"]
            expected = None if prior is None else max(1, int(round(abs(
                prior(candidates[k]["波数"]) - prior(candidates[j]["波数"])))))
            step = choose_odd_step(gap, base_gap, expected)
            norm_gap = gap / step
            for edge, previous in incoming:
                smooth = ((norm_gap - previous["归一间距"]) / (0.32 * base_gap)) ** 2
                new_cost = previous["代价"] + smooth + config["漏阶惩罚"] * (step - 1) + node_cost(k)
                key = (j, k)
                old = states.get(key)
                if old is None or new_cost < old["代价"]:
                    states[key] = {"代价": float(new_cost), "前驱": edge, "步长": step,
                                   "归一间距": float(norm_gap), "长度": previous["长度"] + 1}

    def reconstruct(last_edge):
        path_edges = []
        current = last_edge
        while current is not None:
            path_edges.append(current)
            current = states[current]["前驱"]
        path_edges.reverse()
        path_indices = [path_edges[0][0]] + [item[1] for item in path_edges]
        return path_edges, path_indices

    terminals = []
    x_min, x_max = candidates[0]["波数"], candidates[-1]["波数"]
    all_blocks = {item["分块"] for item in candidates}
    for edge, state in states.items():
        if state["长度"] < min_nodes:
            continue
        path_edges, path_indices = reconstruct(edge)
        used_blocks = {candidates[index]["分块"] for index in path_indices}
        span = candidates[path_indices[-1]]["波数"] - candidates[path_indices[0]]["波数"]
        misses_outer_block = (min_blocks >= 2 and
                              (min(used_blocks) != min(all_blocks) or
                               max(used_blocks) != max(all_blocks)))
        if len(used_blocks) < min_blocks or misses_outer_block or span < 0.45 * (x_max - x_min):
            continue
        boundary_penalty = (((candidates[path_indices[0]]["波数"] - x_min) / base_gap) ** 2 +
                            ((candidates[path_indices[-1]]["波数"] - x_max) / base_gap) ** 2) * 0.08
        score = state["代价"] + boundary_penalty - 0.06 * len(path_indices) - 0.002 * span
        terminals.append((score, edge, path_edges, path_indices))
    if not terminals:
        raise ValueError(f"动态规划未得到跨至少{min_blocks}个连续块且覆盖45%候选跨度的峰链")
    _, _, edges, indices = min(terminals, key=lambda item: item[0])
    steps = [states[item]["步长"] for item in edges]
    orders = np.r_[0.0, np.cumsum(steps).astype(float)]
    chain = [candidates[index] for index in indices]
    return chain, orders


def segmented_peak_chains(candidates, config, t0, prior=None):
    """每个连续数据块独立选链，避免把盲区宽缺口误编码为超大漏阶。"""
    segments, local_orders = [], []
    for block_id in sorted({item["分块"] for item in candidates}):
        local = [item for item in candidates if item["分块"] == block_id]
        if len(local) < MIN_SEGMENT_CHAIN_NODES:
            continue
        try:
            # 仲裁后主周期约260 cm-1，而防泄漏切分后单块仅约880–900 cm-1。
            # find_peaks不取端点，因此短块物理上可能只有4–6个峰谷。先保留
            # 7节点优选；若不可行才降到4节点，最终仍由每角总链长>=10验收。
            preferred_nodes = min(PREFERRED_SEGMENT_CHAIN_NODES, len(local))
            try:
                chain, orders = dynamic_peak_chain(
                    local, config, t0, prior=prior, min_blocks=1,
                    min_nodes=preferred_nodes)
            except ValueError:
                if preferred_nodes <= MIN_SEGMENT_CHAIN_NODES:
                    raise
                chain, orders = dynamic_peak_chain(
                    local, config, t0, prior=prior, min_blocks=1,
                    min_nodes=MIN_SEGMENT_CHAIN_NODES)
            segments.append(chain)
            local_orders.append(orders)
        except ValueError:
            continue
    if not segments:
        raise ValueError(
            f"所有连续块均未形成至少{MIN_SEGMENT_CHAIN_NODES}"
            "节点的受支持峰链")
    return segments, local_orders


def nearest_integer_with_parity(value, parity):
    center = int(round(value))
    candidates = range(center - 3, center + 4)
    return min((item for item in candidates if item % 2 == parity),
               key=lambda item: abs(item - value))


def align_segment_orders(segments, local_orders, angle, physical_params):
    """用跨块物理相位增量推断被遮挡区阶数，块内跳阶仍严格限制在1—9。"""
    intercepts = {angle: 0.0}
    params = (physical_params[0], physical_params[1], physical_params[2], intercepts)
    anchor_sigma = segments[0][0]["波数"]
    anchor_type = segments[0][0]["类型"]
    anchor_phase = float(phase_coordinate(params, anchor_sigma, angle))
    combined_chain, combined_orders, bridge_audit = [], [], []
    for chain, orders in zip(segments, local_orders):
        phase_delta = float(phase_coordinate(params, chain[0]["波数"], angle) - anchor_phase)
        parity = 0 if chain[0]["类型"] == anchor_type else 1
        offset = nearest_integer_with_parity(phase_delta, parity)
        combined_chain.extend(chain)
        combined_orders.extend((orders + offset).tolist())
        bridge_audit.append({
            "分块": int(chain[0]["分块"]),
            "块首波数_cm-1": float(chain[0]["波数"]),
            "物理预测跨块半阶增量": phase_delta,
            "采用整数偏移": int(offset),
            "奇偶与块首峰谷一致": bool(offset % 2 == parity),
        })
    order = np.argsort([item["波数"] for item in combined_chain])
    return ([combined_chain[index] for index in order],
            np.asarray([combined_orders[index] for index in order], float), bridge_audit)


def fit_order_regression(chains, orders, angles, t0, n_anchor=N_ANCHOR,
                         angle_values=None, pseudo_orders=None, multistart=False):
    angle_values = angle_values or {angle: angle for angle in angles}
    pseudo_orders = pseudo_orders or orders
    rows = []
    for angle in angles:
        for item, order in zip(chains[angle], pseudo_orders[angle]):
            rows.append((angle, float(item["波数"]), float(order),
                         float(item.get("可靠性权重", 1.0))))
    slopes = []
    for angle in angles:
        xx = np.asarray([item["波数"] for item in chains[angle]])
        kk = np.asarray(pseudo_orders[angle])
        if xx.size >= 2:
            slopes.append(np.polyfit(xx, kk, 1)[0])
    d0 = float(np.clip(np.median(slopes) / (4e-4 * n_anchor), *THICKNESS_BOUNDS_UM))
    intercept_start = []
    for angle in angles:
        first_sigma = chains[angle][0]["波数"]
        eta = math.sqrt(n_anchor ** 2 - math.sin(math.radians(angle_values[angle])) ** 2)
        intercept_start.append(pseudo_orders[angle][0] - 4e-4 * d0 * first_sigma * eta)

    def unpack(vector):
        d_um = float(vector[0])
        c1 = float(0.30 * np.tanh(vector[1]))
        c2 = float(0.18 * np.tanh(vector[2]))
        intercepts = {angle: float(vector[3 + i]) for i, angle in enumerate(angles)}
        return d_um, c1, c2, intercepts

    def residual(vector):
        budget_guard(t0, allow_soft=True)
        d_um, c1, c2, intercepts = unpack(vector)
        values = []
        for angle, sigma_value, observed_order, reliability in rows:
            z = (sigma_value - SIGMA_CENTER) / SIGMA_SCALE
            n_value = n_anchor + c1 * z + c2 * z * z
            radicand = n_value ** 2 - math.sin(math.radians(angle_values[angle])) ** 2
            if radicand <= 0.0:
                values.append(100.0)
            else:
                prediction = 4e-4 * d_um * sigma_value * math.sqrt(radicand) + intercepts[angle]
                values.append(math.sqrt(reliability) * (prediction - observed_order))
        # 返工轮2：色散形状必须由多波段共同支持，不能用边界色散补偿错阶。
        # 按有效极值数缩放的弱信息正则使其在不同链长下含义一致。
        prior_scale = math.sqrt(max(len(rows), 1) / 20.0)
        values.extend([prior_scale * c1 / DISPERSION_PRIOR_C1,
                       prior_scale * c2 / DISPERSION_PRIOR_C2])
        return np.asarray(values, float)

    initial = np.r_[d0, 0.0, 0.0, intercept_start]
    lower = np.r_[THICKNESS_BOUNDS_UM[0], -3.0, -3.0, np.asarray(intercept_start) - 300.0]
    upper = np.r_[THICKNESS_BOUNDS_UM[1], 3.0, 3.0, np.asarray(intercept_start) + 300.0]
    starts = [initial]
    if multistart:
        starts.extend([
            np.r_[np.clip(0.82 * d0, 1.01, 119.99), -0.7, 0.0, intercept_start],
            np.r_[np.clip(1.18 * d0, 1.01, 119.99), 0.7, 0.0, intercept_start],
            np.r_[d0, 0.0, -0.7, intercept_start],
            np.r_[d0, 0.0, 0.7, intercept_start],
        ])
    fitted_rows = []
    for start_index, start in enumerate(starts):
        budget_guard(t0, allow_soft=True)
        candidate = least_squares(residual, start, bounds=(lower, upper), loss="huber",
                                  f_scale=0.22, max_nfev=260, x_scale="jac")
        p = unpack(candidate.x)
        fitted_rows.append({"序号": start_index + 1, "拟合对象": candidate,
                            "厚度_um": p[0], "c1": p[1], "c2": p[2],
                            "目标代价": float(candidate.cost),
                            "成功": bool(candidate.success)})
    selected = min(fitted_rows, key=lambda row: row["目标代价"])
    fitted = selected["拟合对象"]
    d_um, c1, c2, intercepts = unpack(fitted.x)
    parameter = (d_um, c1, c2, intercepts)
    data_residual = residual(fitted.x)[:-2]
    audit = [{key: value for key, value in row.items() if key != "拟合对象"}
             for row in fitted_rows]
    return {"参数": parameter, "残差": data_residual, "代价": float(np.median(np.abs(data_residual))),
            "成功": bool(fitted.success),
            "色散参数边界占用率": float(max(abs(c1) / 0.30, abs(c2) / 0.18)),
            "色散重参数化": "c1=0.30*tanh(u1), c2=0.18*tanh(u2)，并按链长加入色散形状正则",
            "多初值审计": audit,
            "多初值厚度跨度_%": (100.0 * (max(row["厚度_um"] for row in audit) -
                                             min(row["厚度_um"] for row in audit)) /
                                  max(d_um, 1e-12))}


def fit_fixed_dispersion(chains, orders, angles, c1, c2, t0, n_anchor=N_ANCHOR,
                         angle_values=None):
    """固定共享色散，仅由指定角度的峰链独立估计厚度与截距。"""
    angle_values = angle_values or {angle: angle for angle in angles}
    rows = []
    slopes = []
    for angle in angles:
        x = np.asarray([item["波数"] for item in chains[angle]], float)
        k = np.asarray(orders[angle], float)
        if x.size < 3:
            raise ValueError(f"{angle:.0f}度固定色散拟合的峰链少于3点")
        slopes.append(np.polyfit(x, k, 1)[0])
        rows.extend((angle, float(item["波数"]), float(order),
                     float(item.get("可靠性权重", 1.0)))
                    for item, order in zip(chains[angle], k))
    d0 = float(np.clip(np.median(slopes) / (4e-4 * n_anchor), *THICKNESS_BOUNDS_UM))
    intercept_start = []
    for angle in angles:
        x0 = chains[angle][0]["波数"]
        z0 = (x0 - SIGMA_CENTER) / SIGMA_SCALE
        n0 = n_anchor + c1 * z0 + c2 * z0 * z0
        eta0 = math.sqrt(max(n0 * n0 - math.sin(math.radians(angle_values[angle])) ** 2,
                             1e-12))
        intercept_start.append(orders[angle][0] - 4e-4 * d0 * x0 * eta0)

    def residual(vector):
        budget_guard(t0, allow_soft=True)
        d_um = float(vector[0])
        intercepts = {angle: float(vector[1 + i]) for i, angle in enumerate(angles)}
        values = []
        for angle, sigma_value, observed_order, reliability in rows:
            z = (sigma_value - SIGMA_CENTER) / SIGMA_SCALE
            n_value = n_anchor + c1 * z + c2 * z * z
            eta = math.sqrt(max(n_value * n_value -
                                math.sin(math.radians(angle_values[angle])) ** 2, 1e-12))
            prediction = 4e-4 * d_um * sigma_value * eta + intercepts[angle]
            values.append(math.sqrt(reliability) * (prediction - observed_order))
        return np.asarray(values, float)

    initial = np.r_[d0, intercept_start]
    lower = np.r_[THICKNESS_BOUNDS_UM[0], np.asarray(intercept_start) - 300.0]
    upper = np.r_[THICKNESS_BOUNDS_UM[1], np.asarray(intercept_start) + 300.0]
    fitted = least_squares(residual, initial, bounds=(lower, upper), loss="huber",
                           f_scale=0.22, max_nfev=180, x_scale="jac")
    data_residual = residual(fitted.x)
    intercepts = {angle: float(fitted.x[1 + i]) for i, angle in enumerate(angles)}
    return {"参数": (float(fitted.x[0]), float(c1), float(c2), intercepts),
            "残差": data_residual, "代价": float(np.median(np.abs(data_residual))),
            "目标代价": float(fitted.cost), "成功": bool(fitted.success),
            "色散处理": "固定于双角全段冻结色散，仅重估厚度与角度截距"}


def estimate_dahoc(sigma, spectra, mask, config, t0, n_anchor=N_ANCHOR,
                   angle_values=None, max_candidates=MAX_CANDIDATES, min_blocks=2,
                   abnormal_mode="稳健降权", multistart=False):
    del min_blocks  # 连续块在块内选链，跨块只用物理相位增量对齐，不再直接跳阶。
    candidates, chains, orders, bridges, period_audits = {}, {}, {}, {}, {}
    raw_segments, raw_orders, seed_rows = {}, {}, []
    for angle in ANGLES:
        period_audits[angle] = multiscale_period_audit(sigma, spectra[angle], mask)
        period_hint = period_audits[angle]["冻结主周期_cm-1"]
        reliability_fn = None
        if abnormal_mode == "稳健降权" and angle == 15.0:
            reliability_fn = lambda x: (ABNORMAL_DOWNWEIGHT if
                                        OVER_100_RANGE[0] <= x <= OVER_100_RANGE[1] else 1.0)
        candidates[angle] = generate_candidates(sigma, spectra[angle], mask, config,
                                                max_candidates=max_candidates,
                                                reliability_fn=reliability_fn,
                                                period_hint=period_hint)
        segments, local_orders = segmented_peak_chains(candidates[angle], config, t0)
        raw_segments[angle], raw_orders[angle] = segments, local_orders
        local_slopes = []
        for chain, order_values in zip(segments, local_orders):
            if len(chain) >= 3:
                local_slopes.append(np.polyfit([item["波数"] for item in chain],
                                               order_values, 1)[0])
        eta = math.sqrt(n_anchor ** 2 - math.sin(math.radians(angle)) ** 2)
        d_seed = float(np.clip(np.median(local_slopes) / (4e-4 * eta),
                               *THICKNESS_BOUNDS_UM))
        seed_rows.append({"角度": angle, "块内斜率厚度种子_um": d_seed,
                          "有效分块数": len(local_slopes)})
    # 同晶圆先形成不读取盲块的双角共同种子，再做跨块对齐，避免某一角的错链自我强化。
    consensus_seed = float(np.median([row["块内斜率厚度种子_um"] for row in seed_rows]))
    for angle in ANGLES:
        chains[angle], orders[angle], bridges[angle] = align_segment_orders(
            raw_segments[angle], raw_orders[angle], angle, (consensus_seed, 0.0, 0.0))
    first_fit = fit_order_regression(chains, orders, ANGLES, t0, n_anchor, angle_values)
    for angle in ANGLES:
        prior = lambda x, a=angle: phase_coordinate(first_fit["参数"], x, a, n_anchor)
        segments, local_orders = segmented_peak_chains(candidates[angle], config, t0, prior=prior)
        chains[angle], orders[angle], bridges[angle] = align_segment_orders(
            segments, local_orders, angle, first_fit["参数"][:3])
    final_fit = fit_order_regression(chains, orders, ANGLES, t0, n_anchor, angle_values,
                                     multistart=multistart)
    return {"候选": candidates, "峰链": chains, "阶次": orders, "拟合": final_fit,
            "跨块阶次对齐审计": bridges, "异常波段口径": abnormal_mode,
            "双角共同厚度种子_um": consensus_seed, "角度种子审计": seed_rows,
            "多尺度主周期审计": period_audits}


def type_even_for_chain(chain, orders):
    even_types = [item["类型"] for item, order in zip(chain, orders) if int(round(order)) % 2 == 0]
    return int(np.sign(np.sum(even_types))) if even_types else int(chain[0]["类型"])


def predicted_extrema(params, angle, lo, hi, even_type, n_anchor=N_ANCHOR):
    grid = np.linspace(lo, hi, 5001)
    phase = phase_coordinate(params, grid, angle, n_anchor)
    order = np.argsort(phase)
    phase, grid = phase[order], grid[order]
    phase = np.maximum.accumulate(phase) + np.arange(phase.size) * 1e-12
    integers = np.arange(math.ceil(float(phase[0])), math.floor(float(phase[-1])) + 1)
    positions = np.interp(integers, phase, grid)
    return [{"波数": float(position), "类型": int(even_type if integer % 2 == 0 else -even_type)}
            for integer, position in zip(integers, positions)]


def fixed_observed(sigma, y, mask):
    return generate_candidates(sigma, y, mask, DEFAULT_CONFIG, max_candidates=500,
                               fixed_evaluator=True, period_hint=260.0)


def symmetric_extrema_metric(observed, predicted, width):
    errors = []
    detail = {}
    for kind, label in ((1, "峰"), (-1, "谷")):
        obs = np.asarray([item["波数"] for item in observed if item["类型"] == kind])
        pred = np.asarray([item["波数"] for item in predicted if item["类型"] == kind])
        if obs.size == 0 and pred.size == 0:
            detail[label] = {"观测数": 0, "预测数": 0}
            continue
        if obs.size == 0:
            local = [width] * pred.size
        elif pred.size == 0:
            local = [width] * obs.size
        else:
            distances = np.abs(obs[:, None] - pred[None, :])
            local = np.min(distances, axis=1).tolist() + np.min(distances, axis=0).tolist()
        errors.extend(local)
        detail[label] = {"观测数": int(obs.size), "预测数": int(pred.size),
                         "双向误差数": len(local)}
    return {"双向MdAE_cm-1": float(np.median(errors)) if errors else None,
            "双向MAE_cm-1": float(np.mean(errors)) if errors else None,
            "误差": errors, "计数": detail,
            "数量比": float(len(predicted) / max(len(observed), 1))}


def evaluate_ranges(sigma, spectra, mask, estimate, ranges, n_anchor=N_ANCHOR):
    output, all_errors = {}, []
    for angle in ANGLES:
        angle_results = []
        even_type = type_even_for_chain(estimate["峰链"][angle], estimate["阶次"][angle])
        for lo, hi in ranges:
            local_mask = mask & (sigma >= lo) & (sigma <= hi)
            observed = fixed_observed(sigma, spectra[angle], local_mask)
            predicted = predicted_extrema(estimate["拟合"]["参数"], angle, lo, hi,
                                          even_type, n_anchor)
            metric = symmetric_extrema_metric(observed, predicted, hi - lo)
            angle_results.append({"波段_cm-1": [lo, hi], **metric})
            all_errors.extend(metric["误差"])
        output[f"{int(angle)}度"] = angle_results
    return output, float(np.median(all_errors)) if all_errors else float("inf")


def calibration_configs():
    base = DEFAULT_CONFIG
    return [
        dict(base),
        {**base, "基线尺度_cm-1": 400.0}, {**base, "基线尺度_cm-1": 800.0},
        {**base, "平滑尺度_cm-1": 19.2}, {**base, "平滑尺度_cm-1": 28.8},
        {**base, "显著性因子": 2.0}, {**base, "显著性因子": 3.0},
        {**base, "漏阶惩罚": 0.132},
    ]


def structural_config_variants(config):
    return [
        ("宽基线400cm-1", {**config, "基线尺度_cm-1": 400.0}),
        ("宽基线800cm-1", {**config, "基线尺度_cm-1": 800.0}),
        ("平滑尺度-20%", {**config, "平滑尺度_cm-1": config["平滑尺度_cm-1"] * 0.8}),
        ("平滑尺度+20%", {**config, "平滑尺度_cm-1": config["平滑尺度_cm-1"] * 1.2}),
        ("候选显著性-20%", {**config, "显著性因子": config["显著性因子"] * 0.8}),
        ("候选显著性+20%", {**config, "显著性因子": config["显著性因子"] * 1.2}),
        ("漏阶惩罚-20%", {**config, "漏阶惩罚": config["漏阶惩罚"] * 0.8}),
        ("漏阶惩罚+20%", {**config, "漏阶惩罚": config["漏阶惩罚"] * 1.2}),
    ]


def configuration_stability_audit(sigma, spectra, fit_mask, config, t0):
    """仅用拟合块审查配置邻域，不读取校准块和盲块。"""
    base = estimate_dahoc(sigma, spectra, fit_mask, config, t0)
    base_d = float(base["拟合"]["参数"][0])
    separate = fit_separate_angles(base, t0)
    angle_gap = 200.0 * abs(separate[10.0]["参数"][0] - separate[15.0]["参数"][0]) / max(
        separate[10.0]["参数"][0] + separate[15.0]["参数"][0], 1e-12)
    rows = []
    for label, local_config in structural_config_variants(config):
        budget_guard(t0)
        try:
            fitted = estimate_dahoc(sigma, spectra, fit_mask, local_config, t0)
            value = float(fitted["拟合"]["参数"][0])
            rows.append({"方案": label, "共享厚度_um": value,
                         "相对基准变化_%": 100.0 * (value / base_d - 1.0), "状态": "成功"})
        except TimeBudgetReached:
            raise
        except (ValueError, RuntimeError) as exc:
            rows.append({"方案": label, "状态": f"失败:{exc}"})
    deviations = [abs(row["相对基准变化_%"]) for row in rows if "相对基准变化_%" in row]
    maximum = max(deviations) if deviations else None
    all_success = all(row.get("状态") == "成功" for row in rows)
    qualified = bool(all_success and maximum is not None and
                     maximum <= SENSITIVITY_TOLERANCE_PERCENT and
                     angle_gap <= 10.0 and
                     base["拟合"]["色散参数边界占用率"] < 0.95)
    return {
        "审查数据": "仅拟合块；校准块和1680—1920 cm-1盲块均未读取",
        "基准厚度_um": base_d,
        "结构扰动": rows,
        "结构扰动最大绝对变化_%": maximum,
        "双角条件厚度相对差_%": angle_gap,
        "色散边界占用率": base["拟合"]["色散参数边界占用率"],
        "全部扰动成功": all_success,
        "稳定性合格": qualified,
        "合格规则": "400/800cm-1宽基线及平滑、显著性、漏阶惩罚±20%共八个结构扰动均成功且厚度变化<=10%，双角条件厚度差<=10%，色散边界占用率<0.95",
    }


def select_config(sigma, spectra, masks, t0):
    trials = []
    for config in calibration_configs():
        budget_guard(t0)
        try:
            estimate = estimate_dahoc(sigma, spectra, masks["拟合"], config, t0)
            detail, mdae = evaluate_ranges(sigma, spectra, masks["校准"], estimate,
                                           CALIBRATION_RANGES)
            count_ratios = [item["数量比"] for rows in detail.values() for item in rows]
            count_penalty = np.median(np.abs(np.asarray(count_ratios) - 1.0)) * 25.0
            score = mdae + count_penalty
            trials.append({"配置": config, "校准双向MdAE_cm-1": mdae,
                           "数量偏差惩罚": float(count_penalty), "选择分数": float(score),
                           "状态": "成功"})
        except TimeBudgetReached:
            raise
        except (ValueError, RuntimeError) as exc:
            trials.append({"配置": config, "选择分数": None, "状态": f"失败:{exc}"})
        atomic_write_json("02_配置校准.json", {
            "指标含义": "只用拟合块建模、用校准块的双向极值误差与数量偏差冻结超参数",
            "已完成配置数": len(trials),
            "配置试验": trials,
            "运行状态": "进行中",
            "实际用时秒": round(elapsed(t0), 3),
        })
    successful = [item for item in trials if item["选择分数"] is not None]
    if not successful:
        raise RuntimeError("全部校准配置失败")
    shortlist = sorted(successful, key=lambda item: item["选择分数"])[:CONFIG_STABILITY_SHORTLIST]
    for item in shortlist:
        audit = configuration_stability_audit(sigma, spectra, masks["拟合"],
                                              item["配置"], t0)
        item["配置稳定性审计"] = audit
        maximum = audit["结构扰动最大绝对变化_%"]
        excess = max(0.0, (maximum if maximum is not None else 100.0) -
                     SENSITIVITY_TOLERANCE_PERCENT)
        item["稳定性选择分数"] = (item["选择分数"] + 3.0 * excess +
                                  (0.0 if audit["稳定性合格"] else 50.0))
        atomic_write_json("02_配置校准.json", {
            "指标含义": "先以校准块误差短名单，再只用拟合块审查400/800cm-1宽基线、±20%结构扰动、双角一致性和色散边界",
            "配置试验": trials, "运行状态": "稳定性审查中",
            "实际用时秒": round(elapsed(t0), 3),
        })
    eligible = [item for item in shortlist
                if item.get("配置稳定性审计", {}).get("稳定性合格")]
    best = (min(eligible, key=lambda item: item["选择分数"]) if eligible else
            min(shortlist, key=lambda item: item["稳定性选择分数"]))
    selection = {
        "稳定配置存在": bool(eligible),
        "冻结理由": ("选择校准误差最低且通过拟合块稳定性门的配置" if eligible else
                     "短名单无配置通过稳定性门；冻结综合违约最小配置，但自动验收必须保持失败"),
        "冻结配置稳定性合格": bool(best["配置稳定性审计"]["稳定性合格"]),
        "冻结配置稳定性审计": best["配置稳定性审计"],
    }
    return best["配置"], trials, selection


def chain_summary(estimate):
    output = {}
    for angle in ANGLES:
        chain, orders = estimate["峰链"][angle], estimate["阶次"][angle]
        steps = np.diff(orders)
        within_block = np.asarray([
            left["分块"] == right["分块"] for left, right in zip(chain[:-1], chain[1:])
        ], dtype=bool)
        supported_steps = steps[within_block]
        large_jump_rows = []
        for left, right, step, same_block in zip(chain[:-1], chain[1:], steps, within_block):
            if same_block and step > 1:
                large_jump_rows.append({"起点_cm-1": left["波数"], "终点_cm-1": right["波数"],
                                        "半阶跳跃": int(step),
                                        "受支持": bool(step <= MAX_SUPPORTED_HALF_STEP)})
        output[f"{int(angle)}度"] = {
            "宽尺度冻结主周期_cm-1": estimate["多尺度主周期审计"][angle]["冻结主周期_cm-1"],
            "基线尺度审计": estimate["多尺度主周期审计"][angle],
            "宽松候选数": len(estimate["候选"][angle]),
            "入选峰链数": len(chain),
            "峰数": int(sum(item["类型"] == 1 for item in chain)),
            "谷数": int(sum(item["类型"] == -1 for item in chain)),
            "块内漏阶跳跃数": int(np.sum(supported_steps > 1)),
            "块内最大半阶跳跃": int(np.max(supported_steps)) if supported_steps.size else 0,
            "块内奇数跳跃是否全满足": bool(np.all((supported_steps.astype(int) % 2) == 1)),
            "块内跳跃上限": MAX_SUPPORTED_HALF_STEP,
            "逐段漏阶审计": large_jump_rows,
            "跨块阶次对齐": estimate["跨块阶次对齐审计"][angle],
            "峰链波数_cm-1": [round(item["波数"], 6) for item in chain],
            "半阶序号": orders.astype(int).tolist(),
        }
    return output


def fit_separate_angles(estimate, t0, n_anchor=N_ANCHOR):
    """分角厚度共享全段冻结色散，但各自独立使用本角峰链和截距。"""
    output = {}
    _, c1, c2, _ = estimate["拟合"]["参数"]
    for angle in ANGLES:
        fit = fit_fixed_dispersion(estimate["峰链"], estimate["阶次"], (angle,),
                                   c1, c2, t0, n_anchor=n_anchor)
        output[angle] = fit
    return output


def constant_spacing_baseline(estimate, n_anchor=N_ANCHOR):
    estimates = {}
    for angle in ANGLES:
        chain = estimate["峰链"][angle]
        x = np.asarray([item["波数"] for item in chain])
        same_block = np.asarray([left["分块"] == right["分块"]
                                 for left, right in zip(chain[:-1], chain[1:])], bool)
        step = np.diff(estimate["阶次"][angle])[same_block]
        gap = np.diff(x)[same_block]
        if step.size == 0:
            raise ValueError(f"{angle:.0f}度没有可用于峰距基线的块内相邻链边")
        eta = math.sqrt(n_anchor ** 2 - math.sin(math.radians(angle)) ** 2)
        values = 1.0e4 * step / (4.0 * eta * gap)
        estimates[angle] = float(np.median(values))
    return float(np.mean(list(estimates.values()))), estimates


def fft_baseline(sigma, spectra, mask, n_anchor=N_ANCHOR):
    estimates = {}
    for angle in ANGLES:
        local = []
        for indices in contiguous_segments(mask):
            x, y = sigma[indices], spectra[angle][indices]
            if x.size < 120:
                continue
            grid = np.linspace(x[0], x[-1], x.size)
            signal = np.interp(grid, x, y)
            dx = grid[1] - grid[0]
            _, residual = wide_scale_residual(grid, signal, 600.0)
            frequency = np.fft.rfftfreq(residual.size, d=dx)
            power = np.abs(np.fft.rfft(residual * np.hanning(residual.size))) ** 2
            valid = ((frequency >= 1 / MAIN_PERIOD_BOUNDS_CM[1]) &
                     (frequency <= 1 / MAIN_PERIOD_BOUNDS_CM[0]))
            if np.any(valid):
                f = frequency[valid][np.argmax(power[valid])]
                eta = math.sqrt(n_anchor ** 2 - math.sin(math.radians(angle)) ** 2)
                local.append(f * 1.0e4 / (2.0 * eta))
        estimates[angle] = float(np.median(local))
    return float(np.mean(list(estimates.values()))), estimates


def continuous_phase_baseline(sigma, spectra, mask, t0, n_anchor=N_ANCHOR):
    local_estimates = []
    for angle in ANGLES:
        for indices in contiguous_segments(mask):
            budget_guard(t0)
            x, y = sigma[indices], spectra[angle][indices]
            if x.size < 100:
                continue
            dx = float(np.median(np.diff(x)))
            _, residual = wide_scale_residual(x, y, 600.0)
            phase = np.unwrap(np.angle(hilbert(residual)))
            if np.median(np.diff(phase)) < 0:
                phase = -phase
            trim = max(12, int(0.04 * x.size))
            x, phase = x[trim:-trim], phase[trim:-trim]
            if x.size < 30:
                continue
            # 显式固定相位随波数增加的方向；截距按块独立，不再让列构造错误产生负厚度。
            slope = float(np.polyfit(x - np.mean(x), phase, 1)[0])
            eta = math.sqrt(n_anchor ** 2 - math.sin(math.radians(angle)) ** 2)
            d_local = abs(slope) / (4.0 * math.pi * 1e-4 * eta)
            if THICKNESS_BOUNDS_UM[0] <= d_local <= THICKNESS_BOUNDS_UM[1]:
                local_estimates.append({"角度": angle, "厚度_um": d_local,
                                        "相位斜率_rad每cm-1": slope})
    if not local_estimates:
        raise ValueError("Q2-B没有得到正且有界的分块相位厚度")
    return float(np.median([item["厚度_um"] for item in local_estimates])), local_estimates


def harmonic_r2(x, residual, angle, d_um, n_anchor=N_ANCHOR):
    """常折射率同口径的原网格谐波回归，不读取峰链、阶次或主方法色散。"""
    eta = math.sqrt(n_anchor ** 2 - math.sin(math.radians(angle)) ** 2)
    phase = 4.0 * math.pi * 1e-4 * d_um * x * eta
    design = np.column_stack([np.cos(phase), np.sin(phase)])
    beta = np.linalg.lstsq(design, residual, rcond=None)[0]
    error = residual - design @ beta
    return float(1.0 - np.sum(error ** 2) / max(np.sum(residual ** 2), 1e-12))


def coherence_scan_baseline(sigma, spectra, t0, n_anchor=N_ANCHOR):
    """1450—4000 cm-1、1—120 μm独立全域扫描；不得由DA-HOC候选锚定窗口。"""
    mask = ((sigma >= CROSSCHECK_RANGE[0]) & (sigma <= CROSSCHECK_RANGE[1]))
    prepared = {}
    for angle in ANGLES:
        x, y = sigma[mask], spectra[angle][mask]
        _, residual = wide_scale_residual(x, y, 600.0)
        prepared[angle] = (x, residual)

    coarse_grid = np.linspace(THICKNESS_BOUNDS_UM[0], THICKNESS_BOUNDS_UM[1], 477)
    coarse_scores = {angle: [] for angle in ANGLES}
    for index, d_um in enumerate(coarse_grid):
        if index % 20 == 0:
            budget_guard(t0)
        for angle in ANGLES:
            x, residual = prepared[angle]
            coarse_scores[angle].append(harmonic_r2(x, residual, angle, float(d_um), n_anchor))

    joint_coarse = np.mean(np.column_stack([coarse_scores[angle] for angle in ANGLES]), axis=1)
    seed_values = [float(coarse_grid[int(np.argmax(coarse_scores[angle]))]) for angle in ANGLES]
    seed_values.append(float(coarse_grid[int(np.argmax(joint_coarse))]))
    refine_grid = np.unique(np.concatenate([
        np.linspace(max(THICKNESS_BOUNDS_UM[0], seed - 0.75),
                    min(THICKNESS_BOUNDS_UM[1], seed + 0.75), 151)
        for seed in seed_values
    ]))
    refine_scores = {angle: [] for angle in ANGLES}
    for index, d_um in enumerate(refine_grid):
        if index % 20 == 0:
            budget_guard(t0)
        for angle in ANGLES:
            x, residual = prepared[angle]
            refine_scores[angle].append(harmonic_r2(x, residual, angle, float(d_um), n_anchor))
    joint_refine = np.mean(np.column_stack([refine_scores[angle] for angle in ANGLES]), axis=1)

    angle_results = {}
    for angle in ANGLES:
        scores = np.asarray(refine_scores[angle], float)
        best_index = int(np.argmax(scores))
        coarse = np.asarray(coarse_scores[angle], float)
        high_mask = (coarse_grid >= 80.0) & (coarse_grid <= 100.0)
        ranked = np.argsort(coarse)[::-1]
        candidates = [{"厚度_um": float(refine_grid[best_index]),
                       "R2": float(scores[best_index]), "来源": "全局粗扫最优附近细化"}]
        for candidate_index in ranked:
            d_value = float(coarse_grid[candidate_index])
            if all(abs(d_value - row["厚度_um"]) >= 1.0 for row in candidates):
                candidates.append({"厚度_um": d_value, "R2": float(coarse[candidate_index]),
                                   "来源": "1至120um全域粗扫"})
            if len(candidates) == 5:
                break
        angle_results[f"{int(angle)}度"] = {
            "全局最优厚度_um": float(refine_grid[best_index]),
            "全局最优R2": float(scores[best_index]),
            "80至100um最大R2": float(np.max(coarse[high_mask])),
            "前五个分离候选": candidates,
        }
    joint_index = int(np.argmax(joint_refine))
    return {
        "共享厚度_um": float(refine_grid[joint_index]),
        "共享平均R2": float(joint_refine[joint_index]),
        "分角扫描": angle_results,
        "搜索范围_um": list(THICKNESS_BOUNDS_UM),
        "波数范围_cm-1": list(CROSSCHECK_RANGE),
        "折射率口径": f"常折射率n={n_anchor:.2f}并含Snell角修正",
        "预处理": "600 cm-1宽基线；原波数网格谐波回归",
        "独立性声明": "不复用DA-HOC候选、不复用半阶阶次、不读取主方法厚度或色散、不预设80—100 μm局部窗",
    }


def intercepts_for_thickness(estimate, d_um, c1, c2, n_anchor=N_ANCHOR):
    intercepts = {}
    for angle in ANGLES:
        x = np.asarray([item["波数"] for item in estimate["峰链"][angle]])
        k = estimate["阶次"][angle]
        z = (x - SIGMA_CENTER) / SIGMA_SCALE
        n_value = n_anchor + c1 * z + c2 * z * z
        eta = np.sqrt(n_value ** 2 - np.sin(np.deg2rad(angle)) ** 2)
        intercepts[angle] = float(np.median(k - 4e-4 * d_um * x * eta))
    return (float(d_um), float(c1), float(c2), intercepts)


def blind_metric_for_thickness(sigma, spectra, masks, estimate, d_um, c1, c2,
                               n_anchor=N_ANCHOR):
    proxy = {**estimate, "拟合": {**estimate["拟合"],
             "参数": intercepts_for_thickness(estimate, d_um, c1, c2, n_anchor)}}
    detail, metric = evaluate_ranges(sigma, spectra, masks["盲块"], proxy,
                                     (BLIND_RANGE,), n_anchor)
    return metric, detail


def run_baselines(sigma, spectra, masks, estimate, canonical_blind_mdae,
                  canonical_blind_detail, t0):
    d_main, c1, c2, _ = estimate["拟合"]["参数"]
    constant_d, constant_each = constant_spacing_baseline(estimate)
    fft_d, fft_each = fft_baseline(sigma, spectra, masks["最终训练"])
    methods = {
        "DA-HOC主方法": {"共享厚度_um": d_main,
                         "连续盲块双向峰谷MdAE_cm-1": canonical_blind_mdae,
                         "逐角逐类型计数与误差": canonical_blind_detail,
                         "指标口径ID": CANONICAL_BLIND_METRIC_ID,
                         "截距处理": "冻结最终训练拟合所得角度截距；盲块不重估任何参数"},
        "常折射率相邻峰距": {"共享厚度_um": constant_d, "分角厚度_um": constant_each,
                               "独立性说明": "复用DA-HOC峰链，仅作最低复杂度基线，不承担主链正确性证明"},
        "等距重采样FFT": {"共享厚度_um": fft_d, "分角厚度_um": fft_each},
    }
    try:
        phase_d, phase_blocks = continuous_phase_baseline(
            sigma, spectra, masks["最终训练"], t0)
        methods["Q2-B分块连续相位"] = {
            "共享厚度_um": phase_d, "分块审计": phase_blocks, "状态": "成功",
            "符号约定": "各连续块相位统一为随波数递增，厚度取正相位斜率"}
    except TimeBudgetReached:
        raise
    except (ValueError, RuntimeError) as exc:
        methods["Q2-B分块连续相位"] = {"共享厚度_um": None, "状态": f"失败:{exc}"}
    try:
        coherence = coherence_scan_baseline(sigma, spectra, t0)
        methods["Q2-C原网格相干扫描"] = {
            **coherence, "状态": "成功"}
    except TimeBudgetReached:
        raise
    except (ValueError, RuntimeError) as exc:
        methods["Q2-C原网格相干扫描"] = {"共享厚度_um": None, "状态": f"失败:{exc}"}
    for name, result in methods.items():
        if name == "DA-HOC主方法":
            continue
        value = result.get("共享厚度_um")
        if value is not None and np.isfinite(value) and value > 0:
            metric, _ = blind_metric_for_thickness(sigma, spectra, masks, estimate,
                                                   value, c1, c2)
            result["连续盲块双向峰谷MdAE_cm-1"] = metric
            result["指标口径ID"] = "盲块_训练链重估方法截距_分类双向MdAE_v2"
            result["截距处理"] = "仅用最终训练峰链为该基线厚度估计角度截距；盲块不参与"
    return methods


def dispersion_identifiability_audit(estimate, t0, n_anchor=N_ANCHOR):
    """在固定峰链上做3×3色散剖面，并汇总联合拟合的多初值证据。"""
    d_main, c1_main, c2_main, _ = estimate["拟合"]["参数"]
    c1_grid = np.unique(np.clip(c1_main + np.asarray([-0.06, 0.0, 0.06]), -0.285, 0.285))
    c2_grid = np.unique(np.clip(c2_main + np.asarray([-0.04, 0.0, 0.04]), -0.171, 0.171))
    rows = []
    for c1 in c1_grid:
        for c2 in c2_grid:
            budget_guard(t0)
            fitted = fit_fixed_dispersion(estimate["峰链"], estimate["阶次"], ANGLES,
                                          float(c1), float(c2), t0, n_anchor=n_anchor)
            rows.append({"c1": float(c1), "c2": float(c2),
                         "厚度_um": fitted["参数"][0],
                         "半阶残差MdAE": fitted["代价"],
                         "目标代价": fitted["目标代价"]})
    best_cost = min(row["目标代价"] for row in rows)
    # 目标增量<=max(0.5, 5%)定义为近优剖面，只用于辨识性警报而不参与选参。
    tolerance = max(0.5, 0.05 * max(best_cost, 1e-12))
    near = [row for row in rows if row["目标代价"] <= best_cost + tolerance]
    profile_span = (100.0 * (max(row["厚度_um"] for row in near) -
                             min(row["厚度_um"] for row in near)) / max(d_main, 1e-12))
    multistart = estimate["拟合"].get("多初值审计", [])
    multistart_span = estimate["拟合"].get("多初值厚度跨度_%", 0.0)
    boundary = estimate["拟合"]["色散参数边界占用率"]
    alarm = bool(boundary >= 0.95 or multistart_span > 10.0 or profile_span > 15.0)
    return {
        "方法": "固定峰链3×3(c1,c2)剖面重估厚度；联合模型用5个厚度/色散初值重复优化",
        "色散正则尺度": {"c1": DISPERSION_PRIOR_C1, "c2": DISPERSION_PRIOR_C2},
        "参数剖面": rows,
        "近优定义": f"目标代价不超过最优值+{tolerance:.6g}",
        "近优剖面厚度跨度_%": profile_span,
        "多初值结果": multistart,
        "多初值厚度跨度_%": multistart_span,
        "边界占用率": boundary,
        "边界补偿警报": alarm,
        "警报规则": "边界占用率>=0.95，或多初值厚度跨度>10%，或近优剖面厚度跨度>15%",
    }


def fit_subbands(sigma, spectra, config, full_estimate, t0):
    """分波段重选峰链，但冻结全段共享色散，只检验几何厚度是否一致。"""
    output = {}
    _, c1, c2, _ = full_estimate["拟合"]["参数"]
    for label, limits in (("低波数段", (ANALYSIS_RANGE[0], BLIND_RANGE[0])),
                          ("高波数段", (BLIND_RANGE[1], ANALYSIS_RANGE[1]))):
        mask = (sigma >= limits[0]) & (sigma < limits[1])
        try:
            candidates, chains, orders, bridges = {}, {}, {}, {}
            for angle in ANGLES:
                reliability_fn = (lambda x: ABNORMAL_DOWNWEIGHT if
                                  OVER_100_RANGE[0] <= x <= OVER_100_RANGE[1] else 1.0) \
                                  if angle == 15.0 else None
                period_audit = multiscale_period_audit(sigma, spectra[angle], mask)
                candidates[angle] = generate_candidates(
                    sigma, spectra[angle], mask, config, reliability_fn=reliability_fn,
                    period_hint=period_audit["冻结主周期_cm-1"])
                prior = lambda x, a=angle: phase_coordinate(full_estimate["拟合"]["参数"], x, a)
                segments, local_orders = segmented_peak_chains(candidates[angle], config, t0,
                                                                prior=prior)
                chains[angle], orders[angle], bridges[angle] = align_segment_orders(
                    segments, local_orders, angle, full_estimate["拟合"]["参数"][:3])
            fitted = fit_fixed_dispersion(chains, orders, ANGLES, c1, c2, t0)
            output[label] = {"波段_cm-1": list(limits),
                             "共享厚度_um": fitted["参数"][0],
                             "条件化说明": "本波段重新检测和选择峰链；c1、c2冻结于全段拟合，仅重估共享厚度与双角截距",
                             "峰链数": {f"{int(angle)}度": len(chains[angle]) for angle in ANGLES},
                             "跨块对齐": bridges, "状态": "成功"}
        except TimeBudgetReached:
            raise
        except (ValueError, RuntimeError) as exc:
            output[label] = {"波段_cm-1": list(limits), "状态": f"失败:{exc}"}
    return output


def run_variant(sigma, spectra, config, t0, include_first=True, exclude_over100=False,
                n_anchor=N_ANCHOR, angle_values=None, abnormal_mode="稳健降权"):
    exclude_over100 = exclude_over100 or abnormal_mode == "整段排除"
    masks = make_masks(sigma, include_first, exclude_over100)
    estimate = estimate_dahoc(sigma, spectra, masks["最终训练"], config, t0,
                              n_anchor=n_anchor, angle_values=angle_values,
                              abnormal_mode=abnormal_mode)
    return float(estimate["拟合"]["参数"][0])


def run_sensitivity(sigma, spectra, config, main_d, t0):
    groups = {
        "灵敏度_首点处理": [
            ("含399.6747cm-1零值首点", {"include_first": True}),
            ("剔除首点", {"include_first": False}),
        ],
        "灵敏度_超100波段三口径": [
            ("保留原值且等权使用峰位", {"abnormal_mode": "保留"}),
            ("保留原值并对异常段稳健降权", {"abnormal_mode": "稳健降权"}),
            ("整段排除801.278至927.1104cm-1", {"abnormal_mode": "整段排除"}),
        ],
        "灵敏度_平滑尺度": [
            ("平滑尺度-20%", {"config": {**config, "平滑尺度_cm-1": config["平滑尺度_cm-1"] * 0.8}}),
            ("平滑尺度+20%", {"config": {**config, "平滑尺度_cm-1": config["平滑尺度_cm-1"] * 1.2}}),
        ],
        "灵敏度_宽基线尺度": [
            ("宽基线400cm-1", {"config": {**config, "基线尺度_cm-1": 400.0}}),
            ("宽基线800cm-1", {"config": {**config, "基线尺度_cm-1": 800.0}}),
        ],
        "灵敏度_候选显著性": [
            ("显著性阈值-20%", {"config": {**config, "显著性因子": config["显著性因子"] * 0.8}}),
            ("显著性阈值+20%", {"config": {**config, "显著性因子": config["显著性因子"] * 1.2}}),
        ],
        "灵敏度_漏阶惩罚": [
            ("漏阶惩罚-20%", {"config": {**config, "漏阶惩罚": config["漏阶惩罚"] * 0.8}}),
            ("漏阶惩罚+20%", {"config": {**config, "漏阶惩罚": config["漏阶惩罚"] * 1.2}}),
        ],
        "灵敏度_折射率锚点": [
            ("n锚点2.50", {"n_anchor": 2.50}), ("n锚点2.70", {"n_anchor": 2.70}),
        ],
        "灵敏度_入射角": [
            ("两角均减0.2度", {"angle_values": {10.0: 9.8, 15.0: 14.8}}),
            ("两角均加0.2度", {"angle_values": {10.0: 10.2, 15.0: 15.2}}),
        ],
    }
    output = {}
    for key, variants in groups.items():
        rows = []
        for label, changes in variants:
            budget_guard(t0)
            kwargs = {"include_first": True, "exclude_over100": False,
                      "n_anchor": N_ANCHOR, "angle_values": None,
                      "abnormal_mode": "稳健降权"}
            local_changes = dict(changes)
            local_config = local_changes.pop("config", config)
            kwargs.update(local_changes)
            try:
                value = run_variant(sigma, spectra, local_config, t0, **kwargs)
                rows.append({"方案": label, "共享厚度_um": value,
                             "相对主结果变化_%": 100.0 * (value / main_d - 1.0), "状态": "成功"})
            except TimeBudgetReached:
                raise
            except (ValueError, RuntimeError) as exc:
                rows.append({"方案": label, "状态": f"失败:{exc}"})
        successful = [abs(row["相对主结果变化_%"]) for row in rows if "相对主结果变化_%" in row]
        output[key] = {"指标含义": "改变单一假设后共享厚度相对主结果的变化，绝对值越小越稳健",
                       "方案结果": rows,
                       "最大绝对变化_%": max(successful) if successful else None,
                       "全部方案成功": all(row.get("状态") == "成功" for row in rows)}
        if key == "灵敏度_超100波段三口径":
            output[key]["预注册主口径"] = "保留原值并对异常段稳健降权"
            output[key]["决定性依赖"] = bool(not output[key]["全部方案成功"] or not successful or
                                             max(successful) > SENSITIVITY_TOLERANCE_PERCENT)
            output[key]["决定性依赖阈值_%"] = SENSITIVITY_TOLERANCE_PERCENT
        atomic_write_json("04_灵敏度.json", {
            **output, "运行状态": "进行中", "实际用时秒": round(elapsed(t0), 3)
        })
    return output


def circular_block_resample(values, block_length, rng):
    values = np.asarray(values)
    n = values.size
    result = []
    while len(result) < n:
        start = int(rng.integers(0, n))
        result.extend(values[(start + np.arange(block_length)) % n].tolist())
    return np.asarray(result[:n])


def extract_envelopes(sigma, spectra, estimate):
    envelopes = {}
    for angle in ANGLES:
        y = spectra[angle]
        dx = float(np.median(np.diff(sigma)))
        baseline, centered = wide_scale_residual(sigma, y, 600.0)
        power = savgol_filter(centered ** 2, odd_window(y.size, dx, 60.0, 15), 2, mode="interp")
        amplitude = np.sqrt(np.maximum(2.0 * power, 1e-10))
        phase = np.pi * phase_coordinate(estimate["拟合"]["参数"], sigma, angle)
        noise = centered - amplitude * np.cos(phase)
        envelopes[angle] = {"基线": baseline, "振幅": amplitude, "噪声": noise}
    return envelopes


def semisynthetic_spectrum(sigma, envelopes, main_params, d_true, rng):
    _, c1, c2, intercepts = main_params
    spectra = {}
    for angle in ANGLES:
        params = (d_true, c1, c2, intercepts)
        phase = np.pi * phase_coordinate(params, sigma, angle)
        noise = circular_block_resample(envelopes[angle]["噪声"], 64, rng)
        spectra[angle] = envelopes[angle]["基线"] + envelopes[angle]["振幅"] * np.cos(phase) + noise
    return spectra


def interval_and_coverage(sigma, spectra, masks, estimate, config, t0):
    """全流程重选链的分块半合成 split-conformal 区间与独立100组覆盖验收。"""
    rng = np.random.default_rng(RNG_SEED)
    envelopes = extract_envelopes(sigma, spectra, estimate)
    main_d = estimate["拟合"]["参数"][0]
    separate = fit_separate_angles(estimate, t0)
    real_points = {"共享厚度": main_d,
                   "附件1_10度": separate[10.0]["参数"][0],
                   "附件2_15度": separate[15.0]["参数"][0]}
    keys = tuple(real_points)

    calibration_errors = {key: [] for key in keys}
    calibration_rows = []
    calibration_failures = 0
    for repeat in range(SEMISYNTHETIC_CALIBRATION):
        budget_guard(t0)
        d_true = float(main_d * rng.uniform(0.90, 1.10))
        synthetic = semisynthetic_spectrum(sigma, envelopes, estimate["拟合"]["参数"], d_true, rng)
        try:
            fitted = estimate_dahoc(sigma, synthetic, masks["最终训练"], config, t0,
                                    max_candidates=MAX_CANDIDATES_SIM,
                                    abnormal_mode="稳健降权")
            fitted_separate = fit_separate_angles(fitted, t0)
            points = {"共享厚度": fitted["拟合"]["参数"][0],
                      "附件1_10度": fitted_separate[10.0]["参数"][0],
                      "附件2_15度": fitted_separate[15.0]["参数"][0]}
            for key in keys:
                calibration_errors[key].append(points[key] - d_true)
            calibration_rows.append({"重复号": repeat + 1, "真厚度_um": d_true,
                                     "估计厚度_um": points, "状态": "成功"})
        except TimeBudgetReached:
            raise
        except (ValueError, RuntimeError) as exc:
            calibration_failures += 1
            calibration_rows.append({"重复号": repeat + 1, "真厚度_um": d_true,
                                     "状态": f"失败:{exc}"})
        if (repeat + 1) % 10 == 0:
            atomic_write_json("05_区间与覆盖.json", {
                "区间_全流程重选链共形校准与覆盖": {
                    "构造方法": "64点连续噪声块半合成；每组重新候选检测、分段选链、跨块对齐和Huber反演；split-conformal校准",
                    "名义覆盖率": 0.95,
                    "校准已完成组数": repeat + 1,
                    "校准成功组数": len(calibration_rows) - calibration_failures,
                    "校准失败组数": calibration_failures,
                    "实际用时秒": round(elapsed(t0), 3),
                }
            })

    if any(len(calibration_errors[key]) < 30 for key in keys):
        raise RuntimeError("全流程区间校准成功组数不足30，不能构造区间")

    bias, half_width, real_intervals = {}, {}, {}
    for key in keys:
        errors = np.asarray(calibration_errors[key], float)
        bias[key] = float(np.median(errors))
        scores = np.abs(errors - bias[key])
        rank = min(len(scores) - 1, math.ceil((len(scores) + 1) * 0.95) - 1)
        half_width[key] = float(np.sort(scores)[rank])
        center = real_points[key] - bias[key]
        real_intervals[key] = {"下限_um": center - half_width[key],
                               "上限_um": center + half_width[key],
                               "宽度_um": 2.0 * half_width[key],
                               "偏差校正_um": bias[key],
                               "共形半宽_um": half_width[key]}

    covered = {key: 0 for key in keys}
    validation_rows = []
    validation_failures = 0
    for repeat in range(SEMISYNTHETIC_VALIDATION):
        budget_guard(t0)
        d_true = float(main_d * rng.uniform(0.90, 1.10))
        synthetic = semisynthetic_spectrum(sigma, envelopes, estimate["拟合"]["参数"], d_true, rng)
        try:
            fitted = estimate_dahoc(sigma, synthetic, masks["最终训练"], config, t0,
                                    max_candidates=MAX_CANDIDATES_SIM,
                                    abnormal_mode="稳健降权")
            fitted_separate = fit_separate_angles(fitted, t0)
            points = {"共享厚度": fitted["拟合"]["参数"][0],
                      "附件1_10度": fitted_separate[10.0]["参数"][0],
                      "附件2_15度": fitted_separate[15.0]["参数"][0]}
            hit = {}
            for key in keys:
                center = points[key] - bias[key]
                hit[key] = center - half_width[key] <= d_true <= center + half_width[key]
                covered[key] += int(hit[key])
            validation_rows.append({"重复号": repeat + 1, "真厚度_um": d_true,
                                    "估计厚度_um": points, "覆盖": hit, "状态": "成功"})
        except TimeBudgetReached:
            raise
        except (ValueError, RuntimeError) as exc:
            validation_failures += 1
            validation_rows.append({"重复号": repeat + 1, "真厚度_um": d_true,
                                    "覆盖": {key: False for key in keys},
                                    "状态": f"失败:{exc}"})
        if (repeat + 1) % 10 == 0:
            atomic_write_json("05_区间与覆盖.json", {
                "区间_全流程重选链共形校准与覆盖": {
                    "构造方法": "全流程重选链 split-conformal；校准误差先作中位偏差校正，再取有限样本95%共形分位",
                    "名义覆盖率": 0.95,
                    "真实附件三个口径区间": real_intervals,
                    "独立验收已完成组数": repeat + 1,
                    "当前经验覆盖率_失败计不覆盖": {
                        key: covered[key] / (repeat + 1) for key in keys},
                    "实际用时秒": round(elapsed(t0), 3),
                }
            })

    coverage = {key: covered[key] / SEMISYNTHETIC_VALIDATION for key in keys}
    calibration_success_rate = 1.0 - calibration_failures / SEMISYNTHETIC_CALIBRATION
    success_rate = 1.0 - validation_failures / SEMISYNTHETIC_VALIDATION
    return {
        "构造方法": "64点连续噪声块半合成保留真实网格、包络和异常结构；每组完整重选峰链。60组校准误差作中位偏差校正与有限样本split-conformal膨胀，另用100组独立样本验收覆盖。",
        "名义覆盖率": 0.95,
        "真实附件三个口径区间": real_intervals,
        "半合成设置": {
            "校准组数": SEMISYNTHETIC_CALIBRATION,
            "独立验收组数": SEMISYNTHETIC_VALIDATION,
            "保留结构": "真实7469点波数网格、慢变基线、局部振幅、附件2超100%基线结构和64点连续相关噪声块",
            "已知真厚度范围": "主估计的90%至110%",
        },
        "偏差校正_um": bias,
        "共形半宽_um": half_width,
        "校准成功组数": SEMISYNTHETIC_CALIBRATION - calibration_failures,
        "校准失败组数": calibration_failures,
        "校准成功率": calibration_success_rate,
        "独立验收成功组数": SEMISYNTHETIC_VALIDATION - validation_failures,
        "独立验收失败组数": validation_failures,
        "独立验收成功率": success_rate,
        "经验覆盖率": coverage,
        "平均区间宽度_um": {key: real_intervals[key]["宽度_um"] for key in keys},
        "区间表述等级": {key: ("经独立覆盖验收的95%共形区间"
                                  if value >= COVERAGE_ACCEPTANCE and
                                  calibration_success_rate >= COVERAGE_ACCEPTANCE and
                                  success_rate >= COVERAGE_ACCEPTANCE
                                  else "探索性经验范围（覆盖或成功率不足0.90，已降级）")
                           for key, value in coverage.items()},
        "校准逐组审计": calibration_rows,
        "独立验收逐组审计": validation_rows,
    }


def automatic_acceptance(main_result, validation, sensitivity, interval):
    main_d = main_result["共享厚度_um"]
    chain_rows = main_result["峰链审计"]
    chain_ok = all(row["入选峰链数"] >= 10 and
                   row["块内最大半阶跳跃"] <= MAX_SUPPORTED_HALF_STEP and
                   row["块内奇数跳跃是否全满足"]
                   for row in chain_rows.values())

    subband_rows = validation["交叉印证_分波段"]
    subband_deviation = {}
    for label, row in subband_rows.items():
        subband_deviation[label] = (abs(row["共享厚度_um"] / main_d - 1.0) * 100.0
                                    if row.get("状态") == "成功" else None)
    subband_ok = all(value is not None and value <= SUBBAND_TOLERANCE_PERCENT
                     for value in subband_deviation.values())

    alternatives = validation["基线对比"]
    alternative_audit = {}
    for name in ("Q2-B分块连续相位", "Q2-C原网格相干扫描"):
        value = alternatives[name].get("共享厚度_um")
        deviation = (abs(value / main_d - 1.0) * 100.0
                     if value is not None and value > 0 else None)
        alternative_audit[name] = {"厚度_um": value, "相对主方法差_%": deviation,
                                   "相容": bool(deviation is not None and
                                               deviation <= ALTERNATIVE_TOLERANCE_PERCENT)}
    alternative_ok = (all(row["厚度_um"] is not None and row["厚度_um"] > 0
                          for row in alternative_audit.values()) and
                      any(row["相容"] for row in alternative_audit.values()))

    critical_keys = ("灵敏度_超100波段三口径", "灵敏度_宽基线尺度", "灵敏度_平滑尺度",
                     "灵敏度_候选显著性", "灵敏度_漏阶惩罚")
    sensitivity_audit = {key: {"最大绝对变化_%": sensitivity[key]["最大绝对变化_%"],
                               "全部方案成功": sensitivity[key]["全部方案成功"]}
                         for key in critical_keys}
    sensitivity_ok = all(row["全部方案成功"] and row["最大绝对变化_%"] is not None and
                         row["最大绝对变化_%"] <= SENSITIVITY_TOLERANCE_PERCENT
                         for row in sensitivity_audit.values())

    coverage = interval["经验覆盖率"]
    coverage_ok = (interval["校准成功率"] >= COVERAGE_ACCEPTANCE and
                   interval["独立验收成功率"] >= COVERAGE_ACCEPTANCE and
                   all(value >= COVERAGE_ACCEPTANCE for value in coverage.values()))
    identifiability = main_result["色散可辨识性审计"]
    dispersion_ok = (main_result["折射率参数化"]["边界占用率"] < 0.95 and
                     not identifiability["边界补偿警报"])
    angle_ok = main_result["双角独立厚度相对差_%"] <= 10.0
    config_ok = main_result["配置冻结结论"]["冻结配置稳定性合格"]
    scale_audit = main_result["多尺度主周期保真审计"]
    multiscale_ok = all(
        row["主周期是否避开弱波动带"] and row["三尺度最大相对偏差_%"] <= 20.0
        for row in scale_audit.values())
    independent = alternatives["Q2-C原网格相干扫描"]
    independent_rows = {}
    target_by_angle = {
        "10度": main_result["附件1_10度独立厚度_um"],
        "15度": main_result["附件2_15度独立厚度_um"],
    }
    for label, target in target_by_angle.items():
        row = independent.get("分角扫描", {}).get(label, {})
        best_d = row.get("全局最优厚度_um")
        best_r2 = row.get("全局最优R2")
        high_r2 = row.get("80至100um最大R2")
        deviation = (100.0 * abs(best_d / target - 1.0)
                     if best_d is not None and target > 0 else None)
        independent_rows[label] = {
            "全域最优厚度_um": best_d,
            "相对DA-HOC分角厚度差_%": deviation,
            "全域最优R2": best_r2,
            "80至100um最大R2": high_r2,
            "R2优势": (best_r2 - high_r2 if best_r2 is not None and high_r2 is not None else None),
        }
    independent_ok = (
        independent.get("状态") == "成功" and
        independent.get("搜索范围_um") == list(THICKNESS_BOUNDS_UM) and
        all(row["相对DA-HOC分角厚度差_%"] is not None and
            row["相对DA-HOC分角厚度差_%"] <= ALTERNATIVE_TOLERANCE_PERCENT and
            row["R2优势"] is not None and row["R2优势"] >= 0.10
            for row in independent_rows.values()))
    canonical_validation = validation["验证_冻结截距盲块统一口径"]
    canonical_baseline = validation["基线对比"]["DA-HOC主方法"]
    metric_ok = (canonical_validation["指标口径ID"] == CANONICAL_BLIND_METRIC_ID and
                 canonical_baseline["指标口径ID"] == CANONICAL_BLIND_METRIC_ID and
                 abs(canonical_validation["双角合并双向MdAE_cm-1"] -
                     canonical_baseline["连续盲块双向峰谷MdAE_cm-1"]) <= 1e-12)
    blind_count_ok = bool(canonical_validation["通过"])
    physical_ok = all(value > 0 for value in (
        main_result["共享厚度_um"], main_result["附件1_10度独立厚度_um"],
        main_result["附件2_15度独立厚度_um"]))

    gates = {
        "配置冻结稳定性门": {"通过": config_ok,
                              "审计": main_result["配置冻结结论"]["冻结配置稳定性审计"]},
        "宽基线多尺度保真门": {"通过": multiscale_ok,
                                  "审计": scale_audit,
                                  "规则": "400/600/800cm-1三尺度主周期最大偏差<=20%，且冻结主周期不落入6—15cm-1弱波动带"},
        "峰链稳定性门": {"通过": chain_ok, "阈值": f"每角链长>=10、块内跳跃<={MAX_SUPPORTED_HALF_STEP}且奇数约束成立"},
        "分波段交叉印证门": {"通过": subband_ok, "相对全段差_%": subband_deviation,
                              "阈值_%": SUBBAND_TOLERANCE_PERCENT},
        "独立方法物理解与相容门": {"通过": alternative_ok, "方法审计": alternative_audit,
                                    "阈值_%": ALTERNATIVE_TOLERANCE_PERCENT,
                                    "规则": "Q2-B/Q2-C均为正，且至少一种相对主方法差不超过阈值"},
        "1至120um独立全域扫描门": {
            "通过": independent_ok,
            "方法审计": independent_rows,
            "规则": "1450—4000cm-1、常折射率同口径、1—120um无锚点扫描；两角全局最优均须与DA-HOC分角厚度差<=25%，且R2至少比80—100um区间最优高0.10",
            "独立性声明": independent.get("独立性声明"),
        },
        "关键灵敏度门": {"通过": sensitivity_ok, "最大变化_%": sensitivity_audit,
                          "阈值_%": SENSITIVITY_TOLERANCE_PERCENT},
        "区间覆盖门": {"通过": coverage_ok, "经验覆盖率": coverage,
                        "校准成功率": interval["校准成功率"],
                        "独立验收成功率": interval["独立验收成功率"],
                        "阈值": COVERAGE_ACCEPTANCE},
        "色散可辨识性门": {"通过": dispersion_ok,
                              "边界占用率": main_result["折射率参数化"]["边界占用率"],
                              "参数剖面与多初值": identifiability, "边界阈值": 0.95},
        "双角一致性门": {"通过": angle_ok,
                           "独立厚度相对差_%": main_result["双角独立厚度相对差_%"], "阈值_%": 10.0},
        "盲块指标唯一口径门": {"通过": metric_ok, "指标口径ID": CANONICAL_BLIND_METRIC_ID,
                                  "验证表MdAE_cm-1": canonical_validation["双角合并双向MdAE_cm-1"],
                                  "基线表DA-HOC_MdAE_cm-1": canonical_baseline["连续盲块双向峰谷MdAE_cm-1"]},
        "盲块极值数量同阶门": {"通过": blind_count_ok,
                                  "规则": canonical_validation["数量同阶判据"]},
        "物理解门": {"通过": physical_ok, "要求": "共享、10度、15度厚度均为正"},
    }
    passed = all(row["通过"] for row in gates.values())
    return {"总体通过": passed, "门槛": gates,
            "未通过项": [key for key, row in gates.items() if not row["通过"]],
            "状态解释": ("全部预注册门槛通过，可送交结果解读复核" if passed else
                           "至少一项预注册门槛未通过，厚度与区间不得作为已验收论文结论")}


def main():
    t0 = time.perf_counter()
    write_status(t0, "初始化")
    summary = {"问题": 2, "方法": "DA-HOC 双角奇半阶峰链", "实际用时秒": 0.0}
    try:
        sigma, spectra = read_data()
        masks = make_masks(sigma)
        data_audit = {
            "指标含义": "核验问题2输入、公共网格、固定连续分块与透明异常处理",
            "附件1点数": int(sigma.size), "附件2点数": int(sigma.size),
            "公共波数范围_cm-1": [float(sigma.min()), float(sigma.max())],
            "步长中位数_cm-1": float(np.median(np.diff(sigma))),
            "公共波数键逐值一致": True,
            "固定盲块_cm-1": list(BLIND_RANGE),
            "主峰链宽基线尺度_cm-1": list(BASELINE_SCALES_CM),
            "独立交叉校验波数范围_cm-1": list(CROSSCHECK_RANGE),
            "独立交叉校验厚度范围_um": list(THICKNESS_BOUNDS_UM),
            "拟合点数": int(masks["拟合"].sum()), "校准点数": int(masks["校准"].sum()),
            "盲块点数": int(masks["盲块"].sum()),
            "首点处理": "主分析保留原始0值；另做剔除首点灵敏度，不插值覆盖",
            "附件2超100%处理": "主分析保留原值并将801.278—927.1104 cm-1候选及回归可靠性权重降为0.35；同时预注册等权保留与整段排除口径，禁止截断到100%",
            "实际用时秒": round(elapsed(t0), 3),
        }
        atomic_write_json("01_数据与切分核验.json", data_audit)
        write_status(t0, "数据核验完成")

        chosen_config, calibration, config_selection = select_config(sigma, spectra, masks, t0)
        atomic_write_json("02_配置校准.json", {
            "指标含义": "先按校准块双向极值误差形成短名单，再用拟合块的400/800cm-1宽基线与±20%结构扰动、双角一致性和色散边界冻结配置",
            "冻结配置": chosen_config,
            "配置冻结结论": config_selection,
            "配置试验": calibration,
            "运行状态": "完成",
            "实际用时秒": round(elapsed(t0), 3),
        })
        write_status(t0, "校准完成", note=f"冻结配置：{chosen_config}")
        estimate = estimate_dahoc(sigma, spectra, masks["最终训练"], chosen_config, t0,
                                  abnormal_mode="稳健降权", multistart=True)
        d_shared, c1, c2, intercepts = estimate["拟合"]["参数"]
        separate = fit_separate_angles(estimate, t0)
        identifiability = dispersion_identifiability_audit(estimate, t0)
        main_result = {
            "指标含义": "附件1/2分别估计后，以同晶圆共享厚度和共享低维色散作最终反演",
            "主方法": "Q2-A 峰链动态规划—双角整阶Huber回归（DA-HOC）",
            "仲裁修复声明": "主峰链先由400/600/800cm-1宽尺度谱冻结120—500cm-1主周期，再约束候选距离与奇半阶漏阶；不再使用约80cm-1短基线或4cm-1最小峰距",
            "异常波段主口径": "保留原值、候选与整阶残差可靠性权重0.35；等权保留和整段排除用于预注册三口径审计",
            "冻结配置": chosen_config,
            "配置冻结结论": config_selection,
            "配置校准审计": calibration,
            "共享厚度_um": d_shared,
            "附件1_10度独立厚度_um": separate[10.0]["参数"][0],
            "附件2_15度独立厚度_um": separate[15.0]["参数"][0],
            "双角独立厚度相对差_%": 200.0 * abs(separate[10.0]["参数"][0] - separate[15.0]["参数"][0]) /
                                    (separate[10.0]["参数"][0] + separate[15.0]["参数"][0]),
            "双角独立厚度口径": "两角分别使用本角峰链和截距；共享全段冻结色散c1、c2，避免分角短样本把色散差误算成厚度差",
            "折射率参数化": {"公式": "n(σ)=2.60+c1*z+c2*z^2, z=(σ-1800)/1200",
                              "c1": c1, "c2": c2,
                              "优化重参数化": estimate["拟合"]["色散重参数化"],
                              "色散正则尺度": {"c1": DISPERSION_PRIOR_C1,
                                                "c2": DISPERSION_PRIOR_C2},
                              "边界占用率": estimate["拟合"]["色散参数边界占用率"],
                              "分析波段折射率范围": [
                                  float(np.min(N_ANCHOR + c1 * ((sigma[masks["分析"]] - SIGMA_CENTER) / SIGMA_SCALE) +
                                               c2 * ((sigma[masks["分析"]] - SIGMA_CENTER) / SIGMA_SCALE) ** 2)),
                                  float(np.max(N_ANCHOR + c1 * ((sigma[masks["分析"]] - SIGMA_CENTER) / SIGMA_SCALE) +
                                               c2 * ((sigma[masks["分析"]] - SIGMA_CENTER) / SIGMA_SCALE) ** 2))]},
            "色散可辨识性审计": identifiability,
            "双角共同厚度种子_um": estimate["双角共同厚度种子_um"],
            "角度种子审计": estimate["角度种子审计"],
            "角度独立反射相位截距": {f"{int(angle)}度": value
                                      for angle, value in intercepts.items()},
            "峰链审计": chain_summary(estimate),
            "多尺度主周期保真审计": {f"{int(angle)}度": estimate["多尺度主周期审计"][angle]
                                        for angle in ANGLES},
            "Huber半阶残差MdAE": estimate["拟合"]["代价"],
            "实际用时秒": round(elapsed(t0), 3),
        }
        atomic_write_json("02_主方法与厚度结果.json", main_result)
        summary.update({
            "共享厚度_um": d_shared,
            "附件1_10度独立厚度_um": separate[10.0]["参数"][0],
            "附件2_15度独立厚度_um": separate[15.0]["参数"][0],
            "实际用时秒": round(elapsed(t0), 3),
        })
        write_status(t0, "主方法完成")

        blind_detail, blind_mdae = evaluate_ranges(sigma, spectra, masks["盲块"], estimate,
                                                   (BLIND_RANGE,))
        summary.update({"盲块双向峰谷MdAE_cm-1": blind_mdae,
                        "盲块指标口径ID": CANONICAL_BLIND_METRIC_ID,
                        "实际用时秒": round(elapsed(t0), 3)})
        baselines = run_baselines(sigma, spectra, masks, estimate, blind_mdae,
                                  blind_detail, t0)
        subbands = fit_subbands(sigma, spectra, chosen_config, estimate, t0)
        validation = {
            "验证_连续盲块防泄漏": {
                "指标含义": "1680—1920 cm-1从候选生成、参数校准、平滑和拟合中完全遮挡，冻结后才读取",
                "盲块范围_cm-1": list(BLIND_RANGE), "通过": True,
            },
            "验证_冻结截距盲块统一口径": {
                "指标含义": "冻结最终训练得到的DA-HOC角度截距；观测到预测与预测到观测的同类峰谷最近距离合并，盲块不重估参数",
                "指标口径ID": CANONICAL_BLIND_METRIC_ID,
                "双角合并双向MdAE_cm-1": blind_mdae,
                "逐角逐类型计数与误差": blind_detail,
                "数量同阶判据": "各角预测/观测总数比在0.5至2.0内",
                "通过": all(0.5 <= item["数量比"] <= 2.0 for rows in blind_detail.values() for item in rows),
            },
            "验证_双角共享约束": {
                "指标含义": "固定全段共享色散后，两角分别只用本角峰链估计厚度与截距；再与共享厚度比较",
                "10度独立厚度_um": separate[10.0]["参数"][0],
                "15度独立厚度_um": separate[15.0]["参数"][0],
                "共享厚度_um": d_shared,
                "共享结果位于独立结果包络": min(separate[10.0]["参数"][0], separate[15.0]["参数"][0]) <= d_shared <=
                                           max(separate[10.0]["参数"][0], separate[15.0]["参数"][0]),
            },
            "基线对比": baselines,
            "交叉印证_分波段": subbands,
            "交叉印证结论规则": f"低/高波数段重新选链但固定全段色散，均须成功且相对全段差不超过{SUBBAND_TOLERANCE_PERCENT:.0f}%；Q2-B须为正；Q2-C必须在1450—4000cm-1独立扫描1—120um且两角最优相对DA-HOC差不超过{ALTERNATIVE_TOLERANCE_PERCENT:.0f}%，同时显著优于80—100um的R2；不以盲块反向选择主参数",
            "实际用时秒": round(elapsed(t0), 3),
        }
        atomic_write_json("03_验证与基线对比.json", validation)
        write_status(t0, "验证与基线完成")

        sensitivity = run_sensitivity(sigma, spectra, chosen_config, d_shared, t0)
        sensitivity["实际用时秒"] = round(elapsed(t0), 3)
        atomic_write_json("04_灵敏度.json", sensitivity)
        write_status(t0, "灵敏度完成")

        interval = interval_and_coverage(sigma, spectra, masks, estimate, chosen_config, t0)
        interval_payload = {"区间_全流程重选链共形校准与覆盖": interval,
                            "实际用时秒": round(elapsed(t0), 3)}
        atomic_write_json("05_区间与覆盖.json", interval_payload)

        acceptance = automatic_acceptance(main_result, validation, sensitivity, interval)
        atomic_write_json("06_自动验收门.json", {
            "指标含义": "任何宽基线多尺度失真、1—120um独立扫描不相容、分波段失败、关键灵敏度超阈、覆盖不足或色散再饱和都会阻止结果进入已验收状态",
            **acceptance, "实际用时秒": round(elapsed(t0), 3)})
        accepted = acceptance["总体通过"]

        reported_interval_level = (interval["区间表述等级"] if accepted else
                                   {key: "覆盖校准已完成，但总体自动验收未通过，不得作为论文95%区间"
                                    for key in interval["区间表述等级"]})
        summary.update({
            "运行状态": "验收通过" if accepted else "结果未验收",
            "共享厚度_um": d_shared,
            "附件1_10度独立厚度_um": separate[10.0]["参数"][0],
            "附件2_15度独立厚度_um": separate[15.0]["参数"][0],
            "区间候选_附件1_10度_um": [interval["真实附件三个口径区间"]["附件1_10度"]["下限_um"],
                                      interval["真实附件三个口径区间"]["附件1_10度"]["上限_um"]],
            "区间候选_附件2_15度_um": [interval["真实附件三个口径区间"]["附件2_15度"]["下限_um"],
                                      interval["真实附件三个口径区间"]["附件2_15度"]["上限_um"]],
            "区间候选_共享厚度_um": [interval["真实附件三个口径区间"]["共享厚度"]["下限_um"],
                                     interval["真实附件三个口径区间"]["共享厚度"]["上限_um"]],
            "区间表述等级": reported_interval_level,
            "半合成经验覆盖率": interval["经验覆盖率"],
            "盲块双向峰谷MdAE_cm-1": blind_mdae,
            "盲块指标口径ID": CANONICAL_BLIND_METRIC_ID,
            "自动验收门": acceptance,
            "结果文件": ["01_数据与切分核验.json", "02_配置校准.json", "02_主方法与厚度结果.json",
                         "03_验证与基线对比.json", "04_灵敏度.json", "05_区间与覆盖.json",
                         "06_自动验收门.json"],
            "实际用时秒": round(elapsed(t0), 3),
        })
        if accepted and all(value >= COVERAGE_ACCEPTANCE for value in interval["经验覆盖率"].values()) and \
                interval["校准成功率"] >= COVERAGE_ACCEPTANCE and \
                interval["独立验收成功率"] >= COVERAGE_ACCEPTANCE:
            summary["经覆盖验收的95%共形区间_um"] = {
                key: [row["下限_um"], row["上限_um"]]
                for key, row in interval["真实附件三个口径区间"].items()
            }
        atomic_write_json("汇总结果.json", summary)
        write_authoritative_log(summary)
        write_status(t0, "自动验收完成", state=("验收通过" if accepted else "结果未验收"),
                     note=acceptance["状态解释"])
        print("问题2 DA-HOC计算及自动验收完成：")
        print(f"共享厚度={d_shared:.6f} μm；10°={separate[10.0]['参数'][0]:.6f} μm；"
              f"15°={separate[15.0]['参数'][0]:.6f} μm")
        coverage = interval["经验覆盖率"]
        print(f"盲块双向峰谷MdAE={blind_mdae:.6f} cm-1；"
              f"经验覆盖率(共享/10°/15°)="
              f"{coverage['共享厚度']:.3f}/{coverage['附件1_10度']:.3f}/"
              f"{coverage['附件2_15度']:.3f}；验收状态="
              f"{'通过' if accepted else '未通过'}；实际用时={elapsed(t0):.2f}秒")
    except TimeBudgetReached as exc:
        summary.update({"运行状态": "结果未验收（时间预算主动收敛）", "说明": str(exc),
                        "实际用时秒": round(elapsed(t0), 3)})
        atomic_write_json("汇总结果.json", summary)
        write_authoritative_log(summary)
        write_status(t0, "主动收敛", state="结果未验收", note=str(exc))
        print(f"问题2主动收敛：{exc}；已保留全部已完成阶段。")
    except Exception as exc:
        summary.update({"运行状态": "结果未验收（异常退出）", "说明": f"{type(exc).__name__}: {exc}",
                        "实际用时秒": round(elapsed(t0), 3)})
        atomic_write_json("汇总结果.json", summary)
        write_authoritative_log(summary)
        write_status(t0, "异常退出", state="结果未验收", note=summary["说明"])
        raise


if __name__ == "__main__":
    main()
