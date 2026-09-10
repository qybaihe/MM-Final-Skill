#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Q1-B 连续解析相位—色散斜率模型的小样原型（只输出统一 MdAPE）。"""

import sys

sys.path.insert(0, "/tmp/蜂巢/pylibs")

import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.interpolate import LSQUnivariateSpline
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import least_squares
from scipy.signal import hilbert
from sklearn.isotonic import IsotonicRegression


ROUTE_NAME = "Q1-B 连续解析相位—色散斜率模型"
RANDOM_SEED = 20260826
N_POINTS = 800
N_REPEATS = 30
TIME_BUDGET_SEC = 120.0
SOFT_STOP_SEC = 110.0
ESTIMATED_RUNTIME_SEC = 55
TRUE_THICKNESS_UM = 14.0
ANGLES_DEG = (10.0, 15.0)
NOISE_LEVELS = (0.02, 0.05, 0.10)


def atomic_write_json(path: Path, payload: dict) -> None:
    """原子落盘，保证轮换或中断时不留半个 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, allow_nan=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def robust_scale(values: np.ndarray) -> float:
    median = np.median(values)
    mad = np.median(np.abs(values - median))
    return max(1.4826 * mad, 1e-8)


def robust_spline_baseline(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """固定低自由度三次样条，两轮 Huber 权重抑制条纹对基线的拉动。"""
    knots = np.linspace(x[0], x[-1], 6)[1:-1]
    weights = np.ones_like(y, dtype=float)
    baseline = np.full_like(y, np.median(y), dtype=float)
    for _ in range(2):
        spline = LSQUnivariateSpline(x, y, knots, w=weights, k=3)
        baseline = spline(x)
        residual = y - baseline
        scale = robust_scale(residual)
        ratio = np.abs(residual) / (1.5 * scale)
        weights = np.ones_like(ratio)
        mask = ratio > 1.0
        weights[mask] = 1.0 / ratio[mask]
        weights = np.clip(weights, 0.08, 1.0)
    return baseline


def load_real_small_sample(root: Path) -> tuple[np.ndarray, dict[float, np.ndarray]]:
    """读取附件1/2真实数据，只保留800--2400 cm^-1并等间隔降至800点。"""
    curves: dict[float, np.ndarray] = {}
    common_sigma = None
    for angle, filename in zip(ANGLES_DEG, ("附件1.xlsx", "附件2.xlsx")):
        frame = pd.read_excel(
            root / "数据" / filename,
            sheet_name="Sheet1",
            header=0,
            engine="openpyxl",
        )
        sigma_all = frame["波数 (cm-1)"].to_numpy(dtype=float)
        reflectance_all = frame["反射率 (%)"].to_numpy(dtype=float)
        mask = (sigma_all >= 800.0) & (sigma_all <= 2400.0)
        sigma_band = sigma_all[mask]
        reflectance_band = reflectance_all[mask]
        indices = np.rint(np.linspace(0, len(sigma_band) - 1, N_POINTS)).astype(int)
        sigma = sigma_band[indices]
        reflectance = reflectance_band[indices]
        if common_sigma is None:
            common_sigma = sigma
        elif not np.array_equal(common_sigma, sigma):
            raise ValueError("附件1/2的真实波数网格不一致，无法执行公平双角原型。")
        curves[angle] = reflectance
    if common_sigma is None or len(common_sigma) != N_POINTS:
        raise ValueError("小样构造失败：未取得预期的800个真实波数点。")
    return common_sigma, curves


def real_envelopes(
    sigma: np.ndarray, curves: dict[float, np.ndarray]
) -> dict[float, tuple[np.ndarray, np.ndarray]]:
    """从真实反射率中提取角度独立的慢基线与局部振幅尺度。"""
    output: dict[float, tuple[np.ndarray, np.ndarray]] = {}
    for angle, reflectance in curves.items():
        baseline = robust_spline_baseline(sigma, reflectance)
        high_pass = reflectance - baseline
        amplitude = np.sqrt(2.0) * np.sqrt(
            gaussian_filter1d(high_pass**2, sigma=14.0, mode="nearest")
        )
        positive = amplitude[amplitude > 0]
        if positive.size == 0:
            raise ValueError(f"{angle:.0f}°真实曲线无法提取振幅尺度。")
        lower, upper = np.quantile(positive, [0.20, 0.90])
        amplitude = gaussian_filter1d(np.clip(amplitude, lower, upper), 18.0)
        output[angle] = (baseline, amplitude)
    return output


def refractive_index(sigma: np.ndarray, c1: float, c2: float) -> np.ndarray:
    """低维平滑色散基；n(1600 cm^-1)=2.60附近，常数项作物理锚。"""
    u = (sigma - 1600.0) / 800.0
    return 2.60 + c1 * u + c2 * (u**2 - 1.0 / 3.0)


def make_semisynthetic_pair(
    sigma: np.ndarray,
    envelopes: dict[float, tuple[np.ndarray, np.ndarray]],
    rng: np.random.Generator,
    noise_fraction: float,
    add_dropout: bool,
) -> dict[float, np.ndarray]:
    """用真实网格/基线/振幅构造已知厚度的双角半合成光谱。"""
    n_true = refractive_index(sigma, c1=0.055, c2=-0.022)
    normalized_sigma = (sigma - sigma[0]) / (sigma[-1] - sigma[0])
    pair: dict[float, np.ndarray] = {}
    for angle in ANGLES_DEG:
        baseline, base_amplitude = envelopes[angle]
        amplitude = base_amplitude.copy()
        if add_dropout:
            center = rng.uniform(0.28, 0.72)
            width = rng.uniform(0.012, 0.022)
            amplitude *= 1.0 - 0.94 * np.exp(
                -0.5 * ((normalized_sigma - center) / width) ** 2
            )
        optical_factor = np.sqrt(
            np.maximum(n_true**2 - np.sin(np.deg2rad(angle)) ** 2, 1e-10)
        )
        phase_offset = rng.uniform(-np.pi, np.pi)
        phase = (
            4.0
            * np.pi
            * TRUE_THICKNESS_UM
            * 1e-4
            * sigma
            * optical_factor
            + phase_offset
        )
        noise_scale = noise_fraction * float(np.median(base_amplitude))
        pair[angle] = (
            baseline
            + amplitude * np.cos(phase)
            + rng.normal(0.0, noise_scale, size=sigma.size)
        )
    return pair


def extract_monotone_phase(
    sigma: np.ndarray, reflectance: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """低阶样条去基线、局部振幅标准化、Hilbert展相并施加单调约束。"""
    baseline = robust_spline_baseline(sigma, reflectance)
    residual = reflectance - baseline
    local_amplitude = np.sqrt(
        gaussian_filter1d(residual**2, sigma=10.0, mode="nearest")
    )
    floor = max(float(np.quantile(local_amplitude, 0.15)) * 0.5, 1e-8)
    standardized = residual / np.maximum(local_amplitude, floor)
    raw_phase = np.unwrap(np.angle(hilbert(standardized)))

    edge = max(32, int(round(0.06 * sigma.size)))
    keep = np.arange(edge, sigma.size - edge)
    sigma_kept = sigma[keep]
    phase_kept = raw_phase[keep]
    amplitude_kept = local_amplitude[keep]

    trend = np.polyfit(sigma_kept, phase_kept, 1)[0]
    if trend < 0:
        phase_kept = -phase_kept
    median_amplitude = max(float(np.median(amplitude_kept)), 1e-8)
    weights = np.clip(amplitude_kept / median_amplitude, 0.08, 2.5)

    isotonic = IsotonicRegression(increasing=True, out_of_bounds="clip")
    monotone_phase = isotonic.fit_transform(
        sigma_kept, phase_kept, sample_weight=weights
    )
    if monotone_phase[-1] - monotone_phase[0] < 4.0 * np.pi:
        raise RuntimeError("Hilbert展相后有效相位跨度少于2个周期。")
    return sigma_kept, monotone_phase, weights


def fit_shared_thickness(
    phase_data: dict[float, tuple[np.ndarray, np.ndarray, np.ndarray]]
) -> float:
    """双角共享 d,c1,c2，但为每个角度解析消去独立相位截距。"""
    initial_thicknesses = []
    for angle, (sigma, phase, weights) in phase_data.items():
        q0 = np.sqrt(2.60**2 - np.sin(np.deg2rad(angle)) ** 2)
        design = sigma * q0
        slope = np.polyfit(design, phase, 1, w=np.sqrt(weights))[0]
        initial_thicknesses.append(slope / (4.0 * np.pi) * 1e4)
    d_initial = float(np.clip(np.median(initial_thicknesses), 3.0, 25.0))

    def residual_vector(parameters: np.ndarray) -> np.ndarray:
        d_um, c1, c2 = parameters
        pieces = []
        for angle, (sigma, phase, weights) in phase_data.items():
            n_value = refractive_index(sigma, c1, c2)
            q = np.sqrt(
                np.maximum(n_value**2 - np.sin(np.deg2rad(angle)) ** 2, 1e-10)
            )
            prediction_without_intercept = 4.0 * np.pi * d_um * 1e-4 * sigma * q
            intercept = np.average(
                phase - prediction_without_intercept, weights=weights
            )
            pieces.append(
                np.sqrt(weights)
                * (phase - prediction_without_intercept - intercept)
            )
        return np.concatenate(pieces)

    fit = least_squares(
        residual_vector,
        x0=np.array([d_initial, 0.0, 0.0]),
        bounds=(np.array([2.0, -0.16, -0.10]), np.array([30.0, 0.16, 0.10])),
        loss="soft_l1",
        f_scale=0.35,
        max_nfev=90,
        xtol=1e-7,
        ftol=1e-7,
        gtol=1e-7,
    )
    return float(fit.x[0])


def result_payload(
    elapsed: float,
    errors_percent: list[float],
    failures: int,
    stopped_early: bool,
) -> dict:
    metric = (
        round(float(np.median(errors_percent)), 6) if errors_percent else None
    )
    return {
        "路线名": ROUTE_NAME,
        "核心指标_MdAPE_百分比": metric,
        "用时估计_秒": ESTIMATED_RUNTIME_SEC,
        "实际用时秒": round(float(elapsed), 3),
        "完成重复数": len(errors_percent),
        "失败重复数": int(failures),
        "是否时间预算前主动收敛": bool(stopped_early),
        "口径说明": (
            "在附件1/2共同的真实800--2400 cm^-1波数网格上降采样至800点，"
            "以两附件实测反射率提取角度独立的慢基线和局部振幅，"
            "构造共享已知厚度14.0 um的10°/15°半合成光谱。"
            "三档高斯噪声各10次，部分重复加入局部振幅陷落模拟少量漏峰；"
            "每次用低阶稳健样条去基线、局部振幅标准化、Hilbert展相、"
            "带幅度权的单调相位投影及双角共享厚度/色散斜率的soft-L1回归估计d。"
            "唯一裁决指标为已完成重复的|d_hat-d0|/d0×100%之中位数，越小越优；"
            "单次数值失败按100%误差纳入而不静默删除。"
        ),
    }


def main() -> None:
    t0 = time.perf_counter()
    root = Path(__file__).resolve().parents[2]
    output_path = root / "求解" / "问题1" / "原型结果" / "路线2.json"
    errors_percent: list[float] = []
    failures = 0
    stopped_early = False

    atomic_write_json(
        output_path,
        result_payload(0.0, errors_percent, failures, stopped_early),
    )
    sigma, real_curves = load_real_small_sample(root)
    envelopes = real_envelopes(sigma, real_curves)
    rng = np.random.default_rng(RANDOM_SEED)

    for repeat in range(N_REPEATS):
        if time.perf_counter() - t0 >= SOFT_STOP_SEC:
            stopped_early = True
            break
        noise_fraction = NOISE_LEVELS[repeat // 10]
        add_dropout = repeat % 5 in (3, 4)
        try:
            pair = make_semisynthetic_pair(
                sigma, envelopes, rng, noise_fraction, add_dropout
            )
            phase_data = {
                angle: extract_monotone_phase(sigma, pair[angle])
                for angle in ANGLES_DEG
            }
            estimated_thickness = fit_shared_thickness(phase_data)
            error = (
                abs(estimated_thickness - TRUE_THICKNESS_UM)
                / TRUE_THICKNESS_UM
                * 100.0
            )
            if not np.isfinite(error):
                raise FloatingPointError("厚度误差非有限。")
            errors_percent.append(float(error))
        except (ValueError, RuntimeError, FloatingPointError, np.linalg.LinAlgError):
            failures += 1
            errors_percent.append(100.0)

        elapsed = time.perf_counter() - t0
        atomic_write_json(
            output_path,
            result_payload(elapsed, errors_percent, failures, stopped_early),
        )

    elapsed = time.perf_counter() - t0
    if elapsed >= SOFT_STOP_SEC and len(errors_percent) < N_REPEATS:
        stopped_early = True
    atomic_write_json(
        output_path,
        result_payload(elapsed, errors_percent, failures, stopped_early),
    )
    metric = np.median(errors_percent) if errors_percent else float("nan")
    print(
        f"{ROUTE_NAME}：完成{len(errors_percent)}/{N_REPEATS}次，"
        f"MdAPE={metric:.6f}%，失败{failures}次，用时{elapsed:.3f}秒。"
    )


if __name__ == "__main__":
    main()
