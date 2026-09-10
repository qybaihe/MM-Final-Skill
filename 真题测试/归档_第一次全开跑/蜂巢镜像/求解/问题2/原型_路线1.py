#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Q2-A 峰链动态规划—双角整阶回归的小样原型（只输出统一 MdAE）。"""

import sys
sys.path.insert(0, '/tmp/蜂巢/pylibs')

import json
import math
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.signal import find_peaks, savgol_filter


ROUTE_NAME = "Q2-A 峰链动态规划—双角整阶回归"
TIME_LIMIT_SECONDS = 165.0
EXPECTED_SECONDS = 75
RANGE = (1200.0, 2400.0)
VALIDATION_RANGE = (1680.0, 1920.0)
MAX_POINTS_PER_ANGLE = 1800


class TimeBudgetReached(RuntimeError):
    """原型超过主动收敛时限。"""


def atomic_write_json(path, payload):
    """原子写入，保证轮换或异常时不留半个 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def elapsed(t0):
    return time.perf_counter() - t0


def budget_guard(t0):
    if elapsed(t0) >= TIME_LIMIT_SECONDS:
        raise TimeBudgetReached("已达165秒主动收敛线")


def result_payload(metric, seconds, status, note):
    return {
        "路线名": ROUTE_NAME,
        "核心指标键值": {
            "指标": "连续遮挡验证块峰谷位置MdAE",
            "值": None if metric is None else round(float(metric), 6),
            "单位": "cm-1",
            "优化方向": "越小越优"
        },
        "用时估计": {
            "路线侦察预计秒": EXPECTED_SECONDS,
            "脚本主动收敛秒": int(TIME_LIMIT_SECONDS)
        },
        "实际用时秒": round(float(seconds), 3),
        "口径说明": (
            "附件1/2分别使用1200–2400 cm-1真实数据，每角度确定性降采样至不超过1800点；"
            "将居中1680–1920 cm-1连续块完全遮挡于拟合之外。外侧两块仅用宽松峰谷候选、"
            "交替类型/局部间距平滑/奇数半阶跳跃的动态规划选链；两角共享厚度与线性色散参数做Huber损失拟合。"
            "验证块目标峰谷由固定评估器平滑后局部二次定位，预测与目标按峰/谷分类作双向最近位置误差，"
            "合并10°/15°后取中位数。反射率仅用于候选生成，不截断超100%数值。"
        ),
        "运行状态": status,
        "补充说明": note
    }


def load_small_sample(path):
    frame = pd.read_excel(path, sheet_name="Sheet1", header=0, engine="openpyxl")
    x = pd.to_numeric(frame.iloc[:, 0], errors="raise").to_numpy(dtype=float)
    y = pd.to_numeric(frame.iloc[:, 1], errors="raise").to_numpy(dtype=float)
    keep = np.isfinite(x) & np.isfinite(y) & (x >= RANGE[0]) & (x <= RANGE[1])
    x, y = x[keep], y[keep]
    if x.size < 50 or np.any(np.diff(x) <= 0):
        raise ValueError(f"{path.name}的小样不足或波数未严格递增")
    if x.size > MAX_POINTS_PER_ANGLE:
        idx = np.unique(np.rint(np.linspace(0, x.size - 1, MAX_POINTS_PER_ANGLE)).astype(int))
        x, y = x[idx], y[idx]
    return x, y


def odd_window(points, dx, width_cm):
    window = max(7, int(round(width_cm / dx)))
    if window % 2 == 0:
        window += 1
    largest = points if points % 2 == 1 else points - 1
    return max(5, min(window, largest))


def local_quadratic_x(x, y, index):
    lo, hi = max(0, index - 2), min(x.size, index + 3)
    xx, yy = x[lo:hi], y[lo:hi]
    if xx.size < 3:
        return float(x[index])
    center = float(x[index])
    coef = np.polyfit(xx - center, yy, deg=2)
    if abs(coef[0]) < 1e-12:
        return center
    vertex = center - coef[1] / (2.0 * coef[0])
    if vertex < xx.min() or vertex > xx.max():
        return center
    return float(vertex)


def extrema_candidates(x, y, prominence_factor, iqr_fraction, block_id):
    """find_peaks仅生成宽松候选，不在此决定最终峰链。"""
    dx = float(np.median(np.diff(x)))
    smooth = savgol_filter(y, odd_window(x.size, dx, 10.0), polyorder=3, mode="interp")
    difference = np.diff(smooth)
    noise = 1.4826 * np.median(np.abs(difference - np.median(difference)))
    iqr = float(np.percentile(smooth, 75) - np.percentile(smooth, 25))
    prominence = max(prominence_factor * max(noise, 1e-9), iqr_fraction * max(iqr, 1e-9))
    distance = max(3, int(round(5.0 / dx)))
    peaks, peak_info = find_peaks(smooth, prominence=prominence, distance=distance)
    valleys, valley_info = find_peaks(-smooth, prominence=prominence, distance=distance)
    candidates = []
    for sign, indices, info in ((1, peaks, peak_info), (-1, valleys, valley_info)):
        for idx, prom in zip(indices, info["prominences"]):
            candidates.append({
                "x": local_quadratic_x(x, smooth, int(idx)),
                "type": int(sign),
                "prominence": float(prom),
                "threshold": float(prominence),
                "block": int(block_id)
            })
    return sorted(candidates, key=lambda item: item["x"])


def generate_fit_candidates(x, y):
    candidates = []
    for block_id, mask in enumerate((x < VALIDATION_RANGE[0], x > VALIDATION_RANGE[1])):
        candidates.extend(extrema_candidates(x[mask], y[mask], 2.5, 0.01, block_id))
    return sorted(candidates, key=lambda item: item["x"])


def estimate_half_spacing(candidates):
    gaps = []
    for left, right in zip(candidates[:-1], candidates[1:]):
        gap = right["x"] - left["x"]
        if left["type"] != right["type"] and left["block"] == right["block"] and 20.0 <= gap <= 220.0:
            gaps.append(gap)
    return float(np.median(gaps)) if gaps else 110.0


def choose_odd_step(gap, base_gap, expected=None):
    allowed = np.array([1, 3, 5, 7, 9], dtype=int)
    cost = np.log(np.maximum(gap / (allowed * base_gap), 1e-8)) ** 2 + 0.035 * (allowed - 1)
    if expected is not None:
        cost = cost + 0.18 * (allowed - expected) ** 2
    return int(allowed[np.argmin(cost)])


def chain_dynamic_programming(candidates, phase_prior=None, t0=None):
    """二阶DAG动态规划：交替峰谷，并用奇数半阶跳跃表示漏阶。"""
    n = len(candidates)
    if n < 5:
        raise ValueError("宽松候选极值少于5个，无法建立跨块峰链")
    base_gap = estimate_half_spacing(candidates)

    def node_cost(index):
        item = candidates[index]
        quality = np.clip(item["prominence"] / max(3.0 * item["threshold"], 1e-9), 0.0, 4.0)
        cost = 0.36 / (quality + 0.18) - 0.72
        if phase_prior is not None:
            phase = float(phase_prior(item["x"]))
            cost += 1.2 * abs(phase - np.rint(phase)) ** 2
        return float(cost)

    states = {}
    for i in range(n - 1):
        if t0 is not None:
            budget_guard(t0)
        for j in range(i + 1, n):
            if candidates[i]["type"] == candidates[j]["type"]:
                continue
            gap = candidates[j]["x"] - candidates[i]["x"]
            expected = None
            if phase_prior is not None:
                expected = max(1, int(round(abs(phase_prior(candidates[j]["x"]) - phase_prior(candidates[i]["x"])))))
            step = choose_odd_step(gap, base_gap, expected)
            normalized = gap / step
            initial = node_cost(i) + node_cost(j) + ((normalized - base_gap) / (0.55 * base_gap)) ** 2
            states[(i, j)] = (float(initial), [i, j], [0, step], float(normalized))

    for _ in range(n):
        updated = False
        for (i, j), (cost, path, orders, previous_gap) in list(states.items()):
            if t0 is not None:
                budget_guard(t0)
            for k in range(j + 1, n):
                if candidates[j]["type"] == candidates[k]["type"]:
                    continue
                gap = candidates[k]["x"] - candidates[j]["x"]
                expected = None
                if phase_prior is not None:
                    expected = max(1, int(round(abs(phase_prior(candidates[k]["x"]) - phase_prior(candidates[j]["x"])))))
                step = choose_odd_step(gap, base_gap, expected)
                normalized = gap / step
                smooth_cost = ((normalized - previous_gap) / (0.32 * base_gap)) ** 2
                new_cost = cost + smooth_cost + 0.11 * (step - 1) + node_cost(k)
                key = (j, k)
                old = states.get(key)
                if old is None or new_cost < old[0] - 1e-12:
                    states[key] = (float(new_cost), path + [k], orders + [orders[-1] + step], float(normalized))
                    updated = True
        if not updated:
            break

    valid = []
    x_min, x_max = candidates[0]["x"], candidates[-1]["x"]
    for cost, path, orders, _ in states.values():
        if len(path) < 5 or {candidates[idx]["block"] for idx in path} != {0, 1}:
            continue
        coverage = ((candidates[path[0]]["x"] - x_min) + (x_max - candidates[path[-1]]["x"])) / base_gap
        valid.append((cost + 0.45 * coverage, path, orders))
    if not valid:
        raise ValueError("未找到同时覆盖两个拟合块的交替峰谷链")
    _, path, selected_orders = min(valid, key=lambda item: item[0])
    return [candidates[idx] for idx in path], np.asarray(selected_orders, dtype=float)


def fit_joint_huber(chains, orders, t0):
    """两角共享 d,n0,n1，允许角度独立的反射相位截距。"""
    rows, slopes = [], []
    for angle in (10, 15):
        xx = np.asarray([item["x"] for item in chains[angle]], dtype=float)
        kk = np.asarray(orders[angle], dtype=float)
        rows.extend((angle, x_value, k_value) for x_value, k_value in zip(xx, kk))
        if xx.size >= 2:
            slopes.append(np.polyfit(xx, kk, 1)[0])
    d_start = np.median(slopes) / (4e-4 * 2.60) if slopes else 10.0
    d_start = float(np.clip(d_start, 2.0, 80.0))

    def residual(params):
        budget_guard(t0)
        d_um, n0, n1, b10, b15 = params
        values = []
        for angle, sigma, k_value in rows:
            scaled = (sigma - 1800.0) / 600.0
            refractive_index = n0 + n1 * scaled
            optical_factor = math.sqrt(max(refractive_index ** 2 - math.sin(math.radians(angle)) ** 2, 1e-10))
            predicted = 4e-4 * d_um * sigma * optical_factor + (b10 if angle == 10 else b15)
            values.append(predicted - k_value)
        values.extend([(n0 - 2.60) / 0.30, n1 / 0.25])
        return np.asarray(values, dtype=float)

    initial = np.array([d_start, 2.60, 0.0, -10.0, -10.0], dtype=float)
    for pos, angle in ((3, 10), (4, 15)):
        first_x = chains[angle][0]["x"]
        factor = math.sqrt(2.60 ** 2 - math.sin(math.radians(angle)) ** 2)
        initial[pos] = orders[angle][0] - 4e-4 * d_start * first_x * factor
    fitted = least_squares(
        residual, initial,
        bounds=([1.0, 2.30, -0.45, -80.0, -80.0], [100.0, 3.00, 0.45, 40.0, 40.0]),
        loss="huber", f_scale=0.20, max_nfev=350, x_scale="jac"
    )
    budget_guard(t0)
    return fitted.x


def phase_function(params, angle):
    d_um, n0, n1, b10, b15 = params
    intercept = b10 if angle == 10 else b15

    def evaluate(sigma):
        sigma_array = np.asarray(sigma, dtype=float)
        scaled = (sigma_array - 1800.0) / 600.0
        n_value = n0 + n1 * scaled
        factor = np.sqrt(np.maximum(n_value ** 2 - np.sin(np.deg2rad(angle)) ** 2, 1e-10))
        value = 4e-4 * d_um * sigma_array * factor + intercept
        return float(value) if np.ndim(sigma) == 0 else value
    return evaluate


def validation_targets(x, y):
    mask = (x >= VALIDATION_RANGE[0]) & (x <= VALIDATION_RANGE[1])
    return extrema_candidates(x[mask], y[mask], 3.5, 0.015, block_id=2)


def predicted_validation_extrema(phase, first_type):
    dense_x = np.linspace(VALIDATION_RANGE[0], VALIDATION_RANGE[1], 4001)
    dense_phase = phase(dense_x)
    if dense_phase[-1] < dense_phase[0]:
        dense_phase, dense_x = dense_phase[::-1], dense_x[::-1]
    if np.any(np.diff(dense_phase) <= 0):
        order = np.argsort(dense_phase)
        dense_phase, dense_x = dense_phase[order], dense_x[order]
    integers = np.arange(math.ceil(float(dense_phase[0])), math.floor(float(dense_phase[-1])) + 1)
    positions = np.interp(integers, dense_phase, dense_x)
    return [{"x": float(xv), "type": int(first_type if kv % 2 == 0 else -first_type)}
            for kv, xv in zip(integers, positions)]


def symmetric_location_errors(observed, predicted):
    errors = []
    penalty = VALIDATION_RANGE[1] - VALIDATION_RANGE[0]
    for sign in (1, -1):
        obs = np.asarray([item["x"] for item in observed if item["type"] == sign], dtype=float)
        pred = np.asarray([item["x"] for item in predicted if item["type"] == sign], dtype=float)
        if obs.size == 0 and pred.size == 0:
            continue
        if obs.size == 0:
            errors.extend([penalty] * pred.size)
        elif pred.size == 0:
            errors.extend([penalty] * obs.size)
        else:
            distance = np.abs(obs[:, None] - pred[None, :])
            errors.extend(np.min(distance, axis=1).tolist())
            errors.extend(np.min(distance, axis=0).tolist())
    return errors


def main():
    t0 = time.perf_counter()
    root = Path(__file__).resolve().parents[2]
    output = root / "求解" / "问题2" / "原型结果" / "路线1.json"
    all_errors = []
    atomic_write_json(output, result_payload(None, elapsed(t0), "运行中", "已创建结果文件，正在读取真数据小样。"))
    try:
        samples = {
            10: load_small_sample(root / "数据" / "附件1.xlsx"),
            15: load_small_sample(root / "数据" / "附件2.xlsx")
        }
        budget_guard(t0)
        atomic_write_json(output, result_payload(None, elapsed(t0), "运行中", "两角小样已读取并降采样。"))

        candidates = {angle: generate_fit_candidates(*samples[angle]) for angle in (10, 15)}
        chains, orders = {}, {}
        for angle in (10, 15):
            chains[angle], orders[angle] = chain_dynamic_programming(candidates[angle], t0=t0)
        params = fit_joint_huber(chains, orders, t0)
        atomic_write_json(output, result_payload(None, elapsed(t0), "运行中", "初始双角参数已拟合，正反向校验候选链。"))

        for angle in (10, 15):
            chains[angle], orders[angle] = chain_dynamic_programming(
                candidates[angle], phase_prior=phase_function(params, angle), t0=t0
            )
        params = fit_joint_huber(chains, orders, t0)

        for angle in (10, 15):
            observed = validation_targets(*samples[angle])
            predicted = predicted_validation_extrema(phase_function(params, angle), chains[angle][0]["type"])
            angle_errors = symmetric_location_errors(observed, predicted)
            if not angle_errors:
                raise ValueError(f"{angle}°验证块无可比峰谷")
            all_errors.extend(angle_errors)
            atomic_write_json(output, result_payload(
                float(np.median(all_errors)), elapsed(t0), "运行中", f"已完成{angle}°验证块，中间结果已落盘。"
            ))
            budget_guard(t0)

        metric = float(np.median(all_errors))
        atomic_write_json(output, result_payload(metric, elapsed(t0), "完成", "10°/15°误差已合并；未计算其他性能指标，未生成图形。"))
        print(f"{ROUTE_NAME}：验证块峰谷位置 MdAE = {metric:.6f} cm-1，用时 {elapsed(t0):.2f} 秒。")
    except TimeBudgetReached as exc:
        partial = float(np.median(all_errors)) if all_errors else None
        atomic_write_json(output, result_payload(partial, elapsed(t0), "时间预算主动收敛", str(exc)))
        print(f"{ROUTE_NAME}：{exc}，已落盘当前可用结果。")
    except Exception as exc:
        partial = float(np.median(all_errors)) if all_errors else None
        atomic_write_json(output, result_payload(partial, elapsed(t0), "失败", f"{type(exc).__name__}: {exc}"))
        raise


if __name__ == "__main__":
    main()
