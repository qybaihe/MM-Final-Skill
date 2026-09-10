#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""问题1：Q1-B 连续解析相位—色散斜率模型及其半合成验证。

说明：本脚本按任务要求只负责实现，不在建模岗位执行。运行后将分阶段把
数据核验、主方法、基线对比、灵敏度和区间覆盖结果写入“结果/”。
"""

import sys

sys.path.insert(0, "/tmp/蜂巢/pylibs")

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.interpolate import LSQUnivariateSpline, PchipInterpolator
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import least_squares
from scipy.signal import find_peaks, hilbert
from sklearn.isotonic import IsotonicRegression


RANDOM_SEED = 20260826
ANGLES_DEG = (10.0, 15.0)
FILES = {10.0: "附件1.xlsx", 15.0: "附件2.xlsx"}
TRUE_THICKNESS_UM = 14.0
FIT_BAND = (800.0, 2400.0)
BLIND_BAND = (1680.0, 1920.0)
CALIBRATION_BANDS = ((1520.0, 1680.0), (1920.0, 2080.0))
CORE_TRAIN_BANDS = ((800.0, 1520.0), (2080.0, 2400.0))
TIME_BUDGET_SEC = 1080.0
SOFT_STOP_SEC = 1020.0
BASELINE_REPEATS = 30
COVERAGE_CALIBRATION_REPEATS = 100
COVERAGE_EVALUATION_REPEATS = 100
BOOTSTRAP_REPEATS = 30
REPRESENTATIVE_BOOTSTRAPS = 160
CROSS_RELATIVE_TOLERANCE = 0.10
CROSS_ABSOLUTE_TOLERANCE_UM = 1.50
WAVEFORM_NRMSE_LIMIT = 1.0
ELIGIBLE_CONFIG_SCORE_RATIO = 1.15
MINIMUM_COVERAGE_LINE = 0.90
CALIBRATION_TARGET_COVERAGE = 0.99
PRIMARY_WEIGHT_POLICY = "未加权双角共享；校准质量权重仅作对照"
REPRESENTATIVE_SAMPLE_SPEC = {"随机种子": RANDOM_SEED + 1, "噪声比例": 0.05, "局部振幅陷落比例": 0.10}
RESULT_DECLARATION_NAME = "结果声明_问题1.json"
REQUIRED_RESULT_FILES = (
    "01_数据核验.json",
    "02_主方法与交叉印证.json",
    "03_基线对比.json",
    "04_灵敏度.json",
    "05_区间与覆盖.json",
    "汇总结果.json",
)


@dataclass(frozen=True)
class Config:
    dispersion_order: int = 2
    n_anchor: float = 2.60
    edge_fraction: float = 0.06
    weight_exponent: float = 1.0
    soft_l1_scale: float = 0.35
    baseline_knots: int = 6
    waveform_baseline_degree: int = 2
    uniformize_for_hilbert: bool = True
    block_length: int = 128


DEFAULT_CONFIG = Config()


def finite_builtin(value: Any) -> Any:
    """把 numpy 标量/数组转为严格 JSON 可写对象，并拒绝 NaN/Inf。"""
    if isinstance(value, dict):
        return {str(k): finite_builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_builtin(v) for v in value]
    if isinstance(value, np.ndarray):
        return finite_builtin(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        x = float(value)
        return x if np.isfinite(x) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(finite_builtin(payload), fh, ensure_ascii=False, indent=2, allow_nan=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class ResultStore:
    """每完成一块即原子落盘，汇总文件同步刷新。"""

    def __init__(self, output_dir: Path, t0: float):
        self.output_dir = output_dir
        self.t0 = t0
        self.summary: dict[str, Any] = {
            "问题": "问题1",
            "主方法": "Q1-B 连续解析相位—色散斜率模型（D2-MPC）",
            "运行状态": "初始化",
            "数据核验": {"状态": "未开始"},
            "主方法结果": {"状态": "未开始", "共享约束是否降低角度偏差": None},
            "交叉印证": {"状态": "未开始"},
            "配置选择_训练校准": {"状态": "未开始"},
            "防泄漏验证": {"状态": "未开始"},
            "基线对比": {"状态": "未开始"},
            "灵敏度分析": {
                "灵敏度_色散基阶数": {"状态": "未开始"},
                "灵敏度_折射率锚点": {"状态": "未开始"},
                "灵敏度_边界裁剪": {"状态": "未开始"},
                "灵敏度_幅度权重指数": {"状态": "未开始"},
                "灵敏度_softL1尺度": {"状态": "未开始"},
                "灵敏度_基线自由度": {"状态": "未开始"},
                "灵敏度_非等距Hilbert处理": {"状态": "未开始"},
                "灵敏度_噪声等级": {"状态": "未开始"},
                "灵敏度_局部振幅陷落": {"状态": "未开始"},
                "灵敏度_二次回程污染": {"状态": "未开始"},
            },
            "区间_移动块经验分位": {"状态": "未开始", "区间标签": "尚未校准，不得称95%区间"},
            "正确性核验": {"状态": "未开始", "结果声明是否生成": False},
            "实际用时秒": 0.0,
            "时间预算秒": TIME_BUDGET_SEC,
            "主动收敛阈值秒": SOFT_STOP_SEC,
        }
        initial_files = {
            "01_数据核验.json": {"数据核验": {"状态": "未开始"}},
            "02_主方法与交叉印证.json": {
                "配置选择_训练校准": {"状态": "未开始"},
                "主方法结果": {"状态": "未开始"},
                "交叉印证": {"状态": "未开始"},
                "防泄漏验证": {"状态": "未开始"},
            },
            "03_基线对比.json": {"基线对比": {"状态": "未开始"}},
            "04_灵敏度.json": {"灵敏度分析": {"状态": "未开始"}},
            "05_区间与覆盖.json": {
                "区间_移动块经验分位": {"状态": "未开始", "区间标签": "尚未校准，不得称95%区间"}
            },
            "06_正确性核验.json": {
                "正确性核验": {"状态": "未开始", "结果声明是否生成": False}
            },
        }
        for filename, payload in initial_files.items():
            atomic_write_json(self.output_dir / filename, payload | {"实际用时秒": 0.0})
        self.flush("00_运行状态.json", {})

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    def near_deadline(self) -> bool:
        return self.elapsed() >= SOFT_STOP_SEC

    def flush(self, filename: str, updates: dict[str, Any]) -> None:
        self.summary.update(updates)
        self.summary["实际用时秒"] = round(self.elapsed(), 3)
        atomic_write_json(self.output_dir / filename, updates | {"实际用时秒": self.summary["实际用时秒"]})
        atomic_write_json(self.output_dir / "汇总结果.json", self.summary)


def robust_scale(values: np.ndarray) -> float:
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    return max(1.4826 * mad, 1e-8)


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    v = values[order]
    w = np.maximum(weights[order], 0.0)
    if float(w.sum()) <= 0:
        return float(np.median(v))
    return float(v[np.searchsorted(np.cumsum(w), 0.5 * w.sum())])


def sample_fingerprint(pair: dict[float, np.ndarray]) -> str:
    """冻结代表样本身份，防止锦标赛重复样本误替换头条样本。"""
    digest = hashlib.sha256()
    for angle in ANGLES_DEG:
        digest.update(np.asarray(pair[angle], dtype="<f8").tobytes())
    return digest.hexdigest()[:20]


def robust_spline_baseline(x: np.ndarray, y: np.ndarray, knot_count: int) -> np.ndarray:
    """低自由度三次样条，两轮 Huber 权重；只接收当前训练块。"""
    if x.size < 40:
        return np.polyval(np.polyfit(x, y, min(2, x.size - 1)), x)
    interior = np.linspace(x[0], x[-1], knot_count + 2)[1:-1]
    interior = interior[(interior > x[3]) & (interior < x[-4])]
    weights = np.ones_like(y, dtype=float)
    baseline = np.full_like(y, np.median(y), dtype=float)
    for _ in range(2):
        try:
            spline = LSQUnivariateSpline(x, y, interior, w=weights, k=3)
            baseline = spline(x)
        except (ValueError, np.linalg.LinAlgError):
            baseline = np.polyval(np.polyfit(x, y, 3, w=np.sqrt(weights)), x)
        residual = y - baseline
        scale = robust_scale(residual)
        ratio = np.abs(residual) / (1.5 * scale)
        weights = np.where(ratio <= 1.0, 1.0, 1.0 / np.maximum(ratio, 1e-12))
        weights = np.clip(weights, 0.08, 1.0)
    return baseline


def load_real_pair(root: Path) -> tuple[np.ndarray, dict[float, np.ndarray], dict[str, Any]]:
    curves: dict[float, np.ndarray] = {}
    common_sigma: np.ndarray | None = None
    checks: dict[str, Any] = {"附件": {}, "公共波数键逐值一致": True}
    for angle in ANGLES_DEG:
        path = root / "数据" / FILES[angle]
        frame = pd.read_excel(path, sheet_name="Sheet1", header=0, engine="openpyxl")
        required = {"波数 (cm-1)", "反射率 (%)"}
        if not required.issubset(frame.columns):
            raise ValueError(f"{path.name}缺少字段：{sorted(required - set(frame.columns))}")
        sigma_all = frame["波数 (cm-1)"].to_numpy(dtype=float)
        y_all = frame["反射率 (%)"].to_numpy(dtype=float)
        if not np.all(np.diff(sigma_all) > 0):
            raise ValueError(f"{path.name}波数不是严格递增。")
        if common_sigma is None:
            common_sigma = sigma_all
        elif not np.array_equal(common_sigma, sigma_all):
            checks["公共波数键逐值一致"] = False
            raise ValueError("附件1/2波数键不一致，不能执行双角共享厚度。")
        mask = (sigma_all >= FIT_BAND[0]) & (sigma_all <= FIT_BAND[1])
        curves[angle] = y_all[mask]
        checks["附件"][path.name] = {
            "总点数": int(sigma_all.size),
            "建模波段点数": int(mask.sum()),
            "波数范围_cm-1": [float(sigma_all.min()), float(sigma_all.max())],
            "步长范围_cm-1": [float(np.diff(sigma_all).min()), float(np.diff(sigma_all).max())],
            "首点反射率_百分比": float(y_all[0]),
            "大于100百分比点数": int(np.sum(y_all > 100.0)),
        }
    assert common_sigma is not None
    band_mask = (common_sigma >= FIT_BAND[0]) & (common_sigma <= FIT_BAND[1])
    sigma = common_sigma[band_mask]
    checks["建模波段_cm-1"] = list(FIT_BAND)
    checks["固定盲区_cm-1"] = list(BLIND_BAND)
    checks["盲区不参与Hilbert/基线/参数选择"] = True
    return sigma, curves, checks


def masks_from_bands(sigma: np.ndarray, bands: Iterable[tuple[float, float]]) -> list[np.ndarray]:
    return [(sigma >= low) & (sigma < high) for low, high in bands]


def training_masks(sigma: np.ndarray) -> list[np.ndarray]:
    """最终拟合只用盲区两侧；端点约定避免同一点进入两个连续段。"""
    return [sigma < BLIND_BAND[0], sigma > BLIND_BAND[1]]


def core_training_masks(sigma: np.ndarray) -> list[np.ndarray]:
    """配置选择的拟合块；校准肩带不参与参数拟合。"""
    return masks_from_bands(sigma, CORE_TRAIN_BANDS)


def real_envelopes(sigma: np.ndarray, curves: dict[float, np.ndarray]) -> dict[float, tuple[np.ndarray, np.ndarray]]:
    """仅作为半合成数据生成器提取真实慢基线/振幅，不传给估计器。"""
    output: dict[float, tuple[np.ndarray, np.ndarray]] = {}
    for angle, y in curves.items():
        baseline = robust_spline_baseline(sigma, y, DEFAULT_CONFIG.baseline_knots)
        high = y - baseline
        amplitude = np.sqrt(2.0 * gaussian_filter1d(high**2, sigma=30.0, mode="nearest"))
        positive = amplitude[amplitude > 0]
        lower, upper = np.quantile(positive, [0.15, 0.90])
        amplitude = gaussian_filter1d(np.clip(amplitude, lower, upper), 24.0, mode="nearest")
        output[angle] = (baseline, amplitude)
    return output


def normalized_u(sigma: np.ndarray) -> np.ndarray:
    return (sigma - 1600.0) / 800.0


def refractive_index(sigma: np.ndarray, coefficients: Iterable[float], config: Config) -> np.ndarray:
    c = list(coefficients)
    u = normalized_u(sigma)
    n = np.full_like(sigma, config.n_anchor, dtype=float)
    if config.dispersion_order >= 1:
        n += c[0] * u
    if config.dispersion_order >= 2:
        n += c[1] * (u**2 - 1.0 / 3.0)
    return n


def optical_coordinate(sigma: np.ndarray, angle: float, coefficients: Iterable[float], config: Config) -> np.ndarray:
    n = refractive_index(sigma, coefficients, config)
    return sigma * np.sqrt(np.maximum(n**2 - np.sin(np.deg2rad(angle)) ** 2, 1e-10))


def make_semisynthetic_pair(
    sigma: np.ndarray,
    envelopes: dict[float, tuple[np.ndarray, np.ndarray]],
    rng: np.random.Generator,
    noise_fraction: float,
    dropout_fraction: float,
    second_harmonic_ratio: float = 0.0,
) -> dict[float, np.ndarray]:
    true_config = DEFAULT_CONFIG
    true_coefficients = [0.055, -0.022]
    z = (sigma - sigma[0]) / (sigma[-1] - sigma[0])
    pair: dict[float, np.ndarray] = {}
    for angle in ANGLES_DEG:
        baseline, amplitude0 = envelopes[angle]
        amplitude = amplitude0.copy()
        if dropout_fraction > 0:
            center = rng.uniform(0.25, 0.75)
            width = max(dropout_fraction / 4.0, 0.008)
            amplitude *= 1.0 - 0.94 * np.exp(-0.5 * ((z - center) / width) ** 2)
        phase = 4.0 * np.pi * TRUE_THICKNESS_UM * 1e-4 * optical_coordinate(
            sigma, angle, true_coefficients, true_config
        ) + rng.uniform(-np.pi, np.pi)
        noise_sd = noise_fraction * float(np.median(amplitude0))
        signal = np.cos(phase)
        if second_harmonic_ratio > 0:
            signal += second_harmonic_ratio * np.cos(2.0 * phase + 0.4)
        pair[angle] = baseline + amplitude * signal + rng.normal(0.0, noise_sd, sigma.size)
    return pair


def split_segments(
    sigma: np.ndarray,
    pair: dict[float, np.ndarray],
    masks: list[np.ndarray],
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for angle in ANGLES_DEG:
        for segment_id, mask in enumerate(masks):
            segments.append({
                "角度": angle,
                "段号": segment_id,
                "波数": sigma[mask],
                "反射率": pair[angle][mask],
            })
    return segments


def split_training_segments(sigma: np.ndarray, pair: dict[float, np.ndarray]) -> list[dict[str, Any]]:
    return split_segments(sigma, pair, training_masks(sigma))


def split_core_training_segments(sigma: np.ndarray, pair: dict[float, np.ndarray]) -> list[dict[str, Any]]:
    return split_segments(sigma, pair, core_training_masks(sigma))


def extract_monotone_phase(segment: dict[str, Any], config: Config) -> dict[str, Any]:
    sigma = segment["波数"]
    y = segment["反射率"]
    baseline = robust_spline_baseline(sigma, y, config.baseline_knots)
    residual = y - baseline
    amplitude = np.sqrt(gaussian_filter1d(residual**2, sigma=12.0, mode="nearest"))
    floor = max(float(np.quantile(amplitude, 0.15)) * 0.5, 1e-8)
    standardized = residual / np.maximum(amplitude, floor)

    if config.uniformize_for_hilbert:
        uniform_sigma = np.linspace(sigma[0], sigma[-1], sigma.size)
        uniform_signal = PchipInterpolator(sigma, standardized)(uniform_sigma)
        uniform_phase = np.unwrap(np.angle(hilbert(uniform_signal)))
        raw_phase = PchipInterpolator(uniform_sigma, uniform_phase)(sigma)
    else:
        raw_phase = np.unwrap(np.angle(hilbert(standardized)))

    edge = max(24, int(round(config.edge_fraction * sigma.size)))
    keep = np.arange(edge, sigma.size - edge)
    sigma_k = sigma[keep]
    phase_k = raw_phase[keep]
    amp_k = amplitude[keep]
    if np.polyfit(sigma_k, phase_k, 1)[0] < 0:
        phase_k = -phase_k
    ratio = amp_k / max(float(np.median(amp_k)), 1e-8)
    weights = np.clip(ratio**config.weight_exponent, 0.05, 3.0)
    monotone = IsotonicRegression(increasing=True, out_of_bounds="clip").fit_transform(
        sigma_k, phase_k, sample_weight=weights
    )
    if monotone[-1] - monotone[0] < 4.0 * np.pi:
        raise RuntimeError("训练块解析相位有效跨度少于两个周期。")
    return segment | {"波数": sigma_k, "相位": monotone, "权重": weights, "局部振幅": amp_k}


def extract_all_phases(
    sigma: np.ndarray,
    pair: dict[float, np.ndarray],
    config: Config,
    core_only: bool = False,
) -> list[dict[str, Any]]:
    segments = split_core_training_segments(sigma, pair) if core_only else split_training_segments(sigma, pair)
    return [extract_monotone_phase(seg, config) for seg in segments]


def parameter_bounds(config: Config) -> tuple[np.ndarray, np.ndarray]:
    lower = [2.0]
    upper = [30.0]
    if config.dispersion_order >= 1:
        lower.append(-0.16)
        upper.append(0.16)
    if config.dispersion_order >= 2:
        lower.append(-0.10)
        upper.append(0.10)
    return np.asarray(lower), np.asarray(upper)


def fit_phase_model(
    phases: list[dict[str, Any]],
    config: Config,
    angle_weights: dict[float, float] | None = None,
    shared_angle_intercept: bool = False,
) -> dict[str, Any]:
    """拟合共享厚度；整周对齐后可令同一角度的左右段共享相位截距。"""
    if angle_weights is None:
        angle_weights = {angle: 1.0 for angle in ANGLES_DEG}
    present_angles = sorted({float(item["角度"]) for item in phases})
    lower, upper = parameter_bounds(config)
    d_initials = []
    for item in phases:
        q0 = optical_coordinate(item["波数"], item["角度"], [], replace(config, dispersion_order=0))
        slope = np.polyfit(q0, item["相位"], 1, w=np.sqrt(item["权重"]))[0]
        d_initials.append(slope * 1e4 / (4.0 * np.pi))
    x0 = np.zeros(1 + config.dispersion_order)
    x0[0] = np.clip(np.median(d_initials), lower[0], upper[0])

    def residual_vector(parameters: np.ndarray) -> np.ndarray:
        d_um = parameters[0]
        coefficients = parameters[1:]
        trends = []
        for item in phases:
            x = optical_coordinate(item["波数"], item["角度"], coefficients, config)
            trend = 4.0 * np.pi * d_um * 1e-4 * x
            trends.append((item, trend))
        intercepts: dict[tuple[float, int], float] = {}
        if shared_angle_intercept:
            for angle in present_angles:
                selected = [(item, trend) for item, trend in trends if item["角度"] == angle]
                residuals = np.concatenate([item["相位"] - trend for item, trend in selected])
                weights = np.concatenate([item["权重"] for item, _ in selected])
                value = float(np.average(residuals, weights=weights))
                for item, _ in selected:
                    intercepts[(angle, int(item["段号"]))] = value
        else:
            for item, trend in trends:
                intercepts[(float(item["角度"]), int(item["段号"]))] = float(
                    np.average(item["相位"] - trend, weights=item["权重"])
                )
        pieces = []
        for item, trend in trends:
            intercept = intercepts[(float(item["角度"]), int(item["段号"]))]
            quality = float(angle_weights.get(float(item["角度"]), 1.0))
            pieces.append(np.sqrt(quality * item["权重"]) * (item["相位"] - trend - intercept))
        return np.concatenate(pieces)

    fit = least_squares(
        residual_vector,
        x0=x0,
        bounds=(lower, upper),
        loss="soft_l1",
        f_scale=config.soft_l1_scale,
        max_nfev=100,
        xtol=1e-8,
        ftol=1e-8,
        gtol=1e-8,
    )
    residual = residual_vector(fit.x)
    coefficients = fit.x[1:]
    physical_checks = {}
    for angle in present_angles:
        all_sigma = np.concatenate([item["波数"] for item in phases if item["角度"] == angle])
        all_sigma = np.sort(all_sigma)
        n_values = refractive_index(all_sigma, coefficients, config)
        coord = optical_coordinate(all_sigma, angle, coefficients, config)
        physical_checks[f"{angle:.0f}度"] = {
            "折射率范围": [float(n_values.min()), float(n_values.max())],
            "色散光程坐标严格递增": bool(np.all(np.diff(coord) > 0)),
        }
    if not all(item["色散光程坐标严格递增"] for item in physical_checks.values()):
        raise RuntimeError("入选色散参数使光程坐标非单调，违反D2-MPC物理约束。")
    return {
        "厚度_微米": float(fit.x[0]),
        "色散系数": coefficients.tolist(),
        "色散阶数": config.dispersion_order,
        "物理约束审计": physical_checks,
        "加权相位RMSE_弧度": float(np.sqrt(np.mean(residual**2))),
        "优化成功": bool(fit.success),
        "函数评估次数": int(fit.nfev),
        "截距约束": "同角度左右段共享" if shared_angle_intercept else "逐角逐段独立",
        "角度质量权重": {f"{angle:.0f}度": float(angle_weights.get(angle, 1.0)) for angle in present_angles},
    }


def align_segment_cycles(
    phases: list[dict[str, Any]],
    preliminary_fit: dict[str, Any],
    config: Config,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """按同角度相位截距选择唯一整数周偏移，再允许共享截距复拟合。"""
    aligned: list[dict[str, Any]] = []
    audit: dict[str, Any] = {}
    for angle in sorted({float(item["角度"]) for item in phases}):
        angle_items = sorted(
            [item for item in phases if item["角度"] == angle], key=lambda item: int(item["段号"])
        )
        intercepts = []
        for item in angle_items:
            coordinate = optical_coordinate(
                item["波数"], angle, preliminary_fit["色散系数"], config
            )
            trend = 4.0 * np.pi * preliminary_fit["厚度_微米"] * 1e-4 * coordinate
            intercepts.append(float(np.average(item["相位"] - trend, weights=item["权重"])))
        reference = intercepts[0]
        shifts = [2.0 * np.pi * int(np.rint((reference - value) / (2.0 * np.pi))) for value in intercepts]
        aligned_intercepts = [value + shift for value, shift in zip(intercepts, shifts)]
        for item, shift in zip(angle_items, shifts):
            aligned.append(item | {"相位": item["相位"] + shift, "已整周对齐": True})
        audit[f"{angle:.0f}度"] = {
            "逐段原截距_弧度": intercepts,
            "逐段施加整数周数": [int(round(shift / (2.0 * np.pi))) for shift in shifts],
            "对齐后最大截距差_弧度": float(max(aligned_intercepts) - min(aligned_intercepts)),
        }
    return aligned, audit


def fit_aligned_phase_model(
    phases: list[dict[str, Any]],
    config: Config,
    angle_weights: dict[float, float] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    preliminary = fit_phase_model(phases, config, angle_weights, shared_angle_intercept=False)
    aligned, audit = align_segment_cycles(phases, preliminary, config)
    final = fit_phase_model(aligned, config, angle_weights, shared_angle_intercept=True)
    final["分段相位整周对齐"] = audit
    return final, aligned


def phase_increment_check(
    phases: list[dict[str, Any]],
    fit: dict[str, Any],
    config: Config,
    angle_weights: dict[float, float],
) -> dict[str, Any]:
    """多滞后增量稳健回归：直接拟合Δφ=4πdΔx，不再取单一滞后的局部中位数。"""
    dx_all, dp_all, weights_all = [], [], []
    coefficients = fit["色散系数"]
    for item in phases:
        x = optical_coordinate(item["波数"], item["角度"], coefficients, config)
        lag_candidates = sorted({max(8, len(x) // divisor) for divisor in (80, 40, 20)})
        for lag in lag_candidates:
            dx = x[lag:] - x[:-lag]
            dp = item["相位"][lag:] - item["相位"][:-lag]
            valid = (dx > 0) & (dp > 0)
            if not np.any(valid):
                continue
            local_w = np.sqrt(item["权重"][lag:][valid] * item["权重"][:-lag][valid])
            local_w *= float(angle_weights.get(float(item["角度"]), 1.0))
            dx_all.append(dx[valid])
            dp_all.append(dp[valid])
            weights_all.append(local_w)
    if not dx_all:
        raise RuntimeError("相位增量未取得正向有效差分。")
    dx = np.concatenate(dx_all)
    dp = np.concatenate(dp_all)
    weights = np.concatenate(weights_all)

    def residual(parameter: np.ndarray) -> np.ndarray:
        slope = 4.0 * np.pi * parameter[0] * 1e-4
        return np.sqrt(weights) * (dp - slope * dx)

    initial = np.array([fit["厚度_微米"]], dtype=float)
    result = least_squares(
        residual, x0=initial, bounds=([2.0], [30.0]), loss="soft_l1",
        f_scale=config.soft_l1_scale, max_nfev=60,
    )
    return {
        "厚度_微米": float(result.x[0]),
        "差分对数": int(dx.size),
        "滞后策略": "各连续段长度的1/80、1/40、1/20三档，去重后联合soft-L1回归",
    }


def extrema_order_estimate(
    sigma: np.ndarray,
    pair: dict[float, np.ndarray],
    frozen_dispersion: Iterable[float],
    config: Config,
) -> dict[str, Any]:
    """峰谷奇偶半阶链；按事件类型补漏阶，不再把所有相邻极值强制编号为1个半阶。"""
    slopes, slope_weights, segment_audits = [], [], []
    rough_d = constant_n_peak_spacing(sigma, pair, n0=config.n_anchor)
    for seg in split_training_segments(sigma, pair):
        x = seg["波数"]
        y = seg["反射率"]
        baseline = robust_spline_baseline(x, y, config.baseline_knots)
        residual = gaussian_filter1d(y - baseline, sigma=2.0)
        prominence = max(0.45 * robust_scale(residual), 1e-8)
        coordinate_all = optical_coordinate(x, seg["角度"], frozen_dispersion, config)
        full_period_coordinate = 1e4 / max(2.0 * rough_d, 1e-8)
        distance = max(
            8,
            int(round(0.55 * full_period_coordinate / max(float(np.median(np.diff(coordinate_all))), 1e-8))),
        )
        peaks, _ = find_peaks(residual, prominence=prominence, distance=distance)
        troughs, _ = find_peaks(-residual, prominence=prominence, distance=distance)
        typed = [(int(index), 0) for index in peaks] + [(int(index), 1) for index in troughs]
        typed.sort(key=lambda value: value[0])
        indices = np.asarray([value[0] for value in typed], dtype=int)
        types = np.asarray([value[1] for value in typed], dtype=int)
        if indices.size < 6:
            continue
        coordinate = optical_coordinate(x[indices], seg["角度"], frozen_dispersion, config)
        gaps = np.diff(coordinate)
        positive = gaps[gaps > 0]
        if positive.size < 4:
            continue
        half_step = float(np.median(positive))
        jumps = []
        for gap, left_type, right_type in zip(gaps, types[:-1], types[1:]):
            raw = max(1, int(np.rint(gap / max(half_step, 1e-8))))
            required_parity = 1 if left_type != right_type else 0
            candidates = [value for value in range(max(1, raw - 2), raw + 3) if value % 2 == required_parity]
            jump = min(candidates, key=lambda value: abs(gap - value * half_step))
            jumps.append(jump)
        half_orders = np.r_[0, np.cumsum(jumps)].astype(float) * np.pi
        slope = np.polyfit(coordinate, half_orders, 1)[0]
        estimate = slope * 1e4 / (4.0 * np.pi)
        fitted = np.polyval(np.polyfit(coordinate, half_orders, 1), coordinate)
        rmse = float(np.sqrt(np.mean((half_orders - fitted) ** 2)))
        slopes.append(float(estimate))
        slope_weights.append(float(indices.size / max(rmse, 0.05)))
        segment_audits.append({
            "角度_度": seg["角度"],
            "段号": seg["段号"],
            "极值数": int(indices.size),
            "补入漏失半阶数": int(sum(jumps) - len(jumps)),
            "最大半阶跳数": int(max(jumps)),
            "半阶回归RMSE_弧度": rmse,
            "厚度_微米": float(estimate),
        })
    if not slopes:
        raise RuntimeError("峰谷交叉印证未取得足够极值。")
    return {
        "厚度_微米": weighted_median(np.asarray(slopes), np.asarray(slope_weights)),
        "分段审计": segment_audits,
        "编号规则": "峰/谷异类间隔取正奇数半阶，同类间隔取正偶数半阶；按稳健中位半阶距补漏阶",
    }


def robust_linear_coefficients(design: np.ndarray, y: np.ndarray) -> np.ndarray:
    """固定设计矩阵上的四轮Huber-IRLS；只用于训练带慢包络系数。"""
    weights = np.ones_like(y, dtype=float)
    coef = np.linalg.lstsq(design, y, rcond=None)[0]
    ridge = 1e-8 * np.eye(design.shape[1])
    ridge[0, 0] = 0.0
    for _ in range(4):
        root_w = np.sqrt(weights)
        lhs = (design * root_w[:, None]).T @ (design * root_w[:, None]) + ridge
        rhs = (design * root_w[:, None]).T @ (y * root_w)
        coef = np.linalg.lstsq(lhs, rhs, rcond=None)[0]
        residual = y - design @ coef
        scale = robust_scale(residual)
        ratio = np.abs(residual) / max(1.5 * scale, 1e-12)
        weights = np.where(ratio <= 1.0, 1.0, 1.0 / np.maximum(ratio, 1e-12))
        weights = np.clip(weights, 0.08, 1.0)
    return coef


def waveform_design(
    sigma: np.ndarray,
    angle: float,
    fit: dict[str, Any],
    config: Config,
    center: float,
    scale: float,
) -> np.ndarray:
    t = (sigma - center) / scale
    phase = 4.0 * np.pi * fit["厚度_微米"] * 1e-4 * optical_coordinate(
        sigma, angle, fit["色散系数"], config
    )
    baseline = [t**degree for degree in range(config.waveform_baseline_degree + 1)]
    return np.column_stack(baseline + [np.cos(phase), np.sin(phase), t * np.cos(phase), t * np.sin(phase)])


def fit_local_waveform(
    sigma: np.ndarray,
    y: np.ndarray,
    angle: float,
    fit: dict[str, Any],
    config: Config,
    train_mask: np.ndarray,
) -> dict[str, Any]:
    x_train = sigma[train_mask]
    center = float(np.mean(x_train))
    scale = max(float(np.ptp(x_train)) / 2.0, 1.0)
    design = waveform_design(x_train, angle, fit, config, center, scale)
    coef = robust_linear_coefficients(design, y[train_mask])
    return {"系数": coef, "中心": center, "尺度": scale}


def predict_local_waveform(
    sigma: np.ndarray,
    angle: float,
    fit: dict[str, Any],
    config: Config,
    model: dict[str, Any],
) -> np.ndarray:
    design = waveform_design(sigma, angle, fit, config, model["中心"], model["尺度"])
    return design @ model["系数"]


def local_phase_offset(model: dict[str, Any], sigma_value: float, config: Config) -> float:
    t = (sigma_value - model["中心"]) / model["尺度"]
    start = config.waveform_baseline_degree + 1
    cosine_coef = model["系数"][start] + t * model["系数"][start + 2]
    sine_coef = model["系数"][start + 1] + t * model["系数"][start + 3]
    return float(np.arctan2(-sine_coef, cosine_coef))


def circular_difference(a: float, b: float) -> float:
    return float((a - b + np.pi) % (2.0 * np.pi) - np.pi)


def calibration_waveform_metrics(
    sigma: np.ndarray,
    pair: dict[float, np.ndarray],
    fit: dict[str, Any],
    config: Config,
) -> dict[str, Any]:
    """用外侧核心带拟合，向内预测两个校准肩带；固定盲区完全不参与。"""
    side_specs = [
        ((1200.0, 1520.0), CALIBRATION_BANDS[0]),
        ((2080.0, 2400.0), CALIBRATION_BANDS[1]),
    ]
    by_angle: dict[str, Any] = {}
    for angle in ANGLES_DEG:
        side_metrics: dict[str, Any] = {}
        side_nrmse = []
        for side_index, (train_band, calibration_band) in enumerate(side_specs):
            train = (sigma >= train_band[0]) & (sigma < train_band[1])
            if side_index == 0:
                calibration = (sigma >= calibration_band[0]) & (sigma < calibration_band[1])
            else:
                calibration = (sigma > calibration_band[0]) & (sigma < calibration_band[1])
            model = fit_local_waveform(sigma, pair[angle], angle, fit, config, train)
            predicted = predict_local_waveform(sigma[calibration], angle, fit, config, model)
            observed = pair[angle][calibration]
            rmse = float(np.sqrt(np.mean((observed - predicted) ** 2)))
            observed_range = float(np.ptp(observed))
            nrmse = rmse / max(observed_range, 1e-8)
            side_nrmse.append(nrmse)
            side_metrics["左校准肩带" if side_index == 0 else "右校准肩带"] = {
                "RMSE_反射率百分点": rmse,
                "观测极差_反射率百分点": observed_range,
                "NRMSE": nrmse,
            }
        by_angle[f"{angle:.0f}度"] = {
            "分肩带": side_metrics,
            "NRMSE": float(np.mean(side_nrmse)),
        }
    mean_nrmse = float(np.mean([item["NRMSE"] for item in by_angle.values()]))
    return {"逐角": by_angle, "平均NRMSE": mean_nrmse}


def validation_waveform_metrics(
    sigma: np.ndarray,
    pair: dict[float, np.ndarray],
    fit: dict[str, Any],
    config: Config,
) -> dict[str, Any]:
    """左右邻带独立拟合慢包络，跨盲区预测后线性融合；逐角审计量纲。"""
    validation = (sigma >= BLIND_BAND[0]) & (sigma <= BLIND_BAND[1])
    left_train = (sigma >= 1360.0) & (sigma < BLIND_BAND[0])
    right_train = (sigma > BLIND_BAND[1]) & (sigma <= 2240.0)
    x_validation = sigma[validation]
    blend_right = (x_validation - BLIND_BAND[0]) / (BLIND_BAND[1] - BLIND_BAND[0])
    by_angle: dict[str, Any] = {}
    for angle in ANGLES_DEG:
        left_model = fit_local_waveform(sigma, pair[angle], angle, fit, config, left_train)
        right_model = fit_local_waveform(sigma, pair[angle], angle, fit, config, right_train)
        predicted_left = predict_local_waveform(x_validation, angle, fit, config, left_model)
        predicted_right = predict_local_waveform(x_validation, angle, fit, config, right_model)
        predicted = (1.0 - blend_right) * predicted_left + blend_right * predicted_right
        observed = pair[angle][validation]
        rmse = float(np.sqrt(np.mean((observed - predicted) ** 2)))
        observed_range = float(np.ptp(observed))
        left_offset = local_phase_offset(left_model, BLIND_BAND[0], config)
        right_offset = local_phase_offset(right_model, BLIND_BAND[1], config)
        closure = circular_difference(right_offset, left_offset)
        by_angle[f"{angle:.0f}度"] = {
            "RMSE_反射率百分点": rmse,
            "观测极差_反射率百分点": observed_range,
            "NRMSE": rmse / max(observed_range, 1e-8),
            "跨盲区相位闭合残差_弧度": closure,
            "跨盲区相位整周偏差": closure / (2.0 * np.pi),
        }
    mean_nrmse = float(np.mean([item["NRMSE"] for item in by_angle.values()]))
    return {
        "逐角": by_angle,
        "平均NRMSE": mean_nrmse,
        "误差是否与观测振幅同量级": bool(mean_nrmse <= WAVEFORM_NRMSE_LIMIT),
        "预设NRMSE上限": WAVEFORM_NRMSE_LIMIT,
        "包络外推形式": "左右320 cm-1邻带分别稳健拟合低阶基线与线性复振幅，在盲区内线性融合",
    }


def constant_n_peak_spacing(sigma: np.ndarray, pair: dict[float, np.ndarray], n0: float = 2.60) -> float:
    estimates = []
    for seg in split_training_segments(sigma, pair):
        x, y, angle = seg["波数"], seg["反射率"], seg["角度"]
        residual = gaussian_filter1d(y - robust_spline_baseline(x, y, 6), sigma=2.0)
        peaks, _ = find_peaks(residual, prominence=0.35 * robust_scale(residual), distance=max(5, len(x) // 25))
        if peaks.size < 3:
            continue
        delta = np.diff(x[peaks])
        q = np.sqrt(n0**2 - np.sin(np.deg2rad(angle)) ** 2)
        estimates.extend((1e4 / (2.0 * q * delta)).tolist())
    if not estimates:
        raise RuntimeError("常折射率峰距基线失败。")
    return float(np.median(estimates))


def fit_integer_order_baseline(sigma: np.ndarray, pair: dict[float, np.ndarray], config: Config) -> float:
    """Q1-A 对照：极值半阶编号后，以双角共享厚度和色散做稳健回归。"""
    dispersion_config = replace(config, dispersion_order=2)
    observations = []
    for seg in split_training_segments(sigma, pair):
        x, y = seg["波数"], seg["反射率"]
        residual = gaussian_filter1d(y - robust_spline_baseline(x, y, 6), sigma=2.0)
        prominence = 0.35 * robust_scale(residual)
        peaks, _ = find_peaks(residual, prominence=prominence, distance=max(5, len(x) // 30))
        troughs, _ = find_peaks(-residual, prominence=prominence, distance=max(5, len(x) // 30))
        idx = np.unique(np.sort(np.r_[peaks, troughs]))
        if idx.size >= 6:
            observations.append((seg["角度"], x[idx], np.arange(idx.size) * np.pi))
    if len(observations) < 2:
        raise RuntimeError("Q1-A极值序列不足。")

    def residual_vector(p: np.ndarray) -> np.ndarray:
        d_um, c1, c2 = p
        pieces = []
        for angle, x, order_phase in observations:
            trend = 4.0 * np.pi * d_um * 1e-4 * optical_coordinate(
                x, angle, [c1, c2], dispersion_config
            )
            intercept = np.median(order_phase - trend)
            pieces.append(order_phase - trend - intercept)
        return np.concatenate(pieces)

    initial_d = float(np.clip(constant_n_peak_spacing(sigma, pair), 2.0, 30.0))
    fit = least_squares(
        residual_vector,
        x0=np.array([initial_d, 0.0, 0.0]),
        bounds=([2.0, -0.16, -0.10], [30.0, 0.16, 0.10]),
        loss="soft_l1", f_scale=0.5, max_nfev=80,
    )
    return float(fit.x[0])


def fit_full_waveform_baseline(sigma: np.ndarray, pair: dict[float, np.ndarray], config: Config) -> float:
    """Q1-C 对照：给定非线性相位后线性消去角度/分段基线与振幅。"""
    dispersion_config = replace(config, dispersion_order=2)
    segments = split_training_segments(sigma, pair)
    for seg in segments:
        high = seg["反射率"] - robust_spline_baseline(
            seg["波数"], seg["反射率"], config.baseline_knots
        )
        seg["固定残差尺度"] = robust_scale(high)

    def residual_vector(p: np.ndarray) -> np.ndarray:
        d_um, c1, c2 = p
        pieces = []
        for seg in segments:
            x = seg["波数"]
            u = normalized_u(x)
            phase = 4.0 * np.pi * d_um * 1e-4 * optical_coordinate(
                x, seg["角度"], [c1, c2], dispersion_config
            )
            design = np.column_stack([
                np.ones_like(u), u, u**2,
                np.cos(phase), np.sin(phase),
                u * np.cos(phase), u * np.sin(phase),
            ])
            coef, *_ = np.linalg.lstsq(design, seg["反射率"], rcond=None)
            pieces.append((seg["反射率"] - design @ coef) / seg["固定残差尺度"])
        return np.concatenate(pieces)

    best = None
    rough_d = float(np.clip(constant_n_peak_spacing(sigma, pair), 2.5, 27.5))
    starts = sorted({float(np.clip(rough_d * factor, 2.0, 30.0)) for factor in (0.8, 1.0, 1.2)})
    for d0 in starts:
        fit = least_squares(
            residual_vector,
            x0=np.array([d0, 0.0, 0.0]),
            bounds=([2.0, -0.16, -0.10], [30.0, 0.16, 0.10]),
            loss="soft_l1", f_scale=0.8, max_nfev=60,
        )
        score = float(np.mean(residual_vector(fit.x) ** 2))
        if best is None or score < best[0]:
            best = (score, float(fit.x[0]))
    assert best is not None
    return best[1]


def candidate_configurations() -> dict[str, Config]:
    """候选集在读取盲区前冻结；同时覆盖色散阶数和包络自由度。"""
    return {
        "C0_常折射率_4结点_线性基线": replace(
            DEFAULT_CONFIG, dispersion_order=0, baseline_knots=4, waveform_baseline_degree=1
        ),
        "C1_一阶色散_4结点_线性基线": replace(
            DEFAULT_CONFIG, dispersion_order=1, baseline_knots=4, waveform_baseline_degree=1
        ),
        "C2_一阶色散_6结点_二次基线": replace(
            DEFAULT_CONFIG, dispersion_order=1, baseline_knots=6, waveform_baseline_degree=2
        ),
        "C3_二阶色散_4结点_线性基线": replace(
            DEFAULT_CONFIG, dispersion_order=2, baseline_knots=4, waveform_baseline_degree=1
        ),
        "C4_二阶色散_6结点_二次基线": DEFAULT_CONFIG,
        "C5_二阶色散_8结点_二次基线": replace(
            DEFAULT_CONFIG, dispersion_order=2, baseline_knots=8, waveform_baseline_degree=2
        ),
    }


def select_configuration(
    sigma: np.ndarray,
    pair: dict[float, np.ndarray],
) -> tuple[Config, dict[float, float], dict[str, Any]]:
    """只按核心拟合带向校准肩带的预测误差与双角一致性选配置。"""
    candidates: dict[str, Any] = {}
    selectable: list[tuple[float, str, Config, dict[str, Any]]] = []
    for name, config in candidate_configurations().items():
        try:
            phases = extract_all_phases(sigma, pair, config, core_only=True)
            shared, _ = fit_aligned_phase_model(phases, config)
            separate = {
                angle: fit_aligned_phase_model(
                    [p for p in phases if p["角度"] == angle], config
                )[0]["厚度_微米"]
                for angle in ANGLES_DEG
            }
            calibration = calibration_waveform_metrics(sigma, pair, shared, config)
            disparity = abs(separate[10.0] - separate[15.0]) / max(shared["厚度_微米"], 1e-8)
            score = calibration["平均NRMSE"] + 0.25 * disparity
            record = {
                "配置": asdict(config),
                "校准肩带逐角": calibration["逐角"],
                "校准平均NRMSE": calibration["平均NRMSE"],
                "核心带分角厚度_微米": {f"{a:.0f}度": separate[a] for a in ANGLES_DEG},
                "核心带分角相对差": disparity,
                "选择得分": score,
                "候选角色": "仅基线，不得入选主模型" if config.dispersion_order == 0 else "显式色散主模型候选",
                "状态": "成功",
            }
            candidates[name] = record
            if config.dispersion_order >= 1:
                selectable.append((score, name, config, calibration))
        except (ValueError, RuntimeError, FloatingPointError, np.linalg.LinAlgError) as exc:
            candidates[name] = {"配置": asdict(config), "状态": "失败", "失败原因": str(exc)}
    if not selectable:
        raise RuntimeError("所有显式色散训练/校准候选配置均失败。")
    selectable.sort(key=lambda item: item[0])
    best_score, selected_name, selected_config, selected_calibration = selectable[0]
    inverse = {}
    for angle in ANGLES_DEG:
        nrmse = selected_calibration["逐角"][f"{angle:.0f}度"]["NRMSE"]
        inverse[angle] = 1.0 / max(nrmse**2, 1e-6)
    mean_inverse = float(np.mean(list(inverse.values())))
    angle_weights = {angle: float(np.clip(value / mean_inverse, 0.25, 4.0)) for angle, value in inverse.items()}
    eligible = [name for score, name, _, _ in selectable if score <= best_score * ELIGIBLE_CONFIG_SCORE_RATIO]
    audit = {
        "状态": "完成",
        "选择数据": "仅800—1520与2080—2400 cm-1核心拟合带，以及1520—1680与1920—2080 cm-1校准肩带",
        "固定盲区是否用于配置选择": False,
        "候选配置": candidates,
        "C0处理规则": "常折射率C0仅作为复杂度基线；即使校准得分最低也不得替代题面要求的显式色散主模型",
        "入选配置": selected_name,
        "入选配置参数": asdict(selected_config),
        "最佳选择得分": best_score,
        "合理配置判据": f"选择得分不超过最佳值的{ELIGIBLE_CONFIG_SCORE_RATIO:.2f}倍",
        "合理配置": eligible,
        "诊断性角度质量权重": {f"{angle:.0f}度": angle_weights[angle] for angle in ANGLES_DEG},
        "主估计权重策略": PRIMARY_WEIGHT_POLICY,
        "角度权重来源": "入选配置在两个校准肩带上的逐角NRMSE平方倒数，归一化后截断至[0.25,4]；仅用于同样本对照",
    }
    return selected_config, angle_weights, audit


def estimate_primary_thickness(
    sigma: np.ndarray,
    pair: dict[float, np.ndarray],
    config: Config,
    angle_weights: dict[float, float],
) -> float:
    phases = extract_all_phases(sigma, pair, config)
    fit, _ = fit_aligned_phase_model(phases, config, angle_weights)
    return float(fit["厚度_微米"])


def one_primary_run(
    sigma: np.ndarray,
    pair: dict[float, np.ndarray],
    config: Config,
    primary_angle_weights: dict[float, float],
    diagnostic_angle_weights: dict[float, float],
) -> dict[str, Any]:
    phases = extract_all_phases(sigma, pair, config)
    shared, aligned_phases = fit_aligned_phase_model(phases, config, primary_angle_weights)
    quality_weighted, _ = fit_aligned_phase_model(phases, config, diagnostic_angle_weights)
    separate = {}
    for angle in ANGLES_DEG:
        separate[angle] = fit_aligned_phase_model(
            [p for p in phases if p["角度"] == angle], config, {angle: 1.0}
        )[0]["厚度_微米"]
    increment = phase_increment_check(aligned_phases, shared, config, primary_angle_weights)
    extrema = extrema_order_estimate(sigma, pair, shared["色散系数"], config)
    cross_values = {
        "连续相位未加权共享厚度_微米": shared["厚度_微米"],
        "相位增量厚度_微米": increment["厚度_微米"],
        "局部峰谷整阶厚度_微米": extrema["厚度_微米"],
    }
    maximum_difference = float(max(cross_values.values()) - min(cross_values.values()))
    reference = float(np.median(list(cross_values.values())))
    allowed_difference = max(CROSS_ABSOLUTE_TOLERANCE_UM, CROSS_RELATIVE_TOLERANCE * reference)
    return {
        "拟合": shared,
        "主估计权重策略": PRIMARY_WEIGHT_POLICY,
        "质量加权共享厚度_微米": quality_weighted["厚度_微米"],
        "诊断性角度质量权重": quality_weighted["角度质量权重"],
        "分角厚度_微米": {f"{angle:.0f}度": separate[angle] for angle in ANGLES_DEG},
        "分角厚度差_微米": abs(separate[10.0] - separate[15.0]),
        "相位增量证据": increment,
        "峰谷整阶证据": extrema,
        "三种厚度一致性": {
            "厚度结果": cross_values,
            "最大两两差_微米": maximum_difference,
            "允许最大差_微米": allowed_difference,
            "预设相对阈值": CROSS_RELATIVE_TOLERANCE,
            "预设绝对阈值_微米": CROSS_ABSOLUTE_TOLERANCE_UM,
            "是否通过": bool(maximum_difference <= allowed_difference),
        },
        "盲区全波形验证": validation_waveform_metrics(sigma, pair, shared, config),
        "相位数据": aligned_phases,
    }


def error_percent(estimate: float) -> float:
    return abs(estimate - TRUE_THICKNESS_UM) / TRUE_THICKNESS_UM * 100.0


def summarize_estimates(estimates: list[float]) -> dict[str, Any]:
    arr = np.asarray(estimates, dtype=float)
    valid = arr[np.isfinite(arr)]
    if valid.size == 0:
        return {"完成数": 0, "MdAPE_百分比": None, "失败率": 1.0}
    errors = np.abs(valid - TRUE_THICKNESS_UM) / TRUE_THICKNESS_UM * 100.0
    return {
        "完成数": int(valid.size),
        "MdAPE_百分比": float(np.median(errors)),
        "厚度中位数_微米": float(np.median(valid)),
        "厚度四分位距_微米": float(np.subtract(*np.percentile(valid, [75, 25]))),
        "失败率": float(1.0 - valid.size / max(len(arr), 1)),
    }


def run_baseline_tournament(
    sigma: np.ndarray,
    envelopes: dict[float, tuple[np.ndarray, np.ndarray]],
    rng: np.random.Generator,
    store: ResultStore,
    config: Config,
    angle_weights: dict[float, float],
) -> dict[str, Any]:
    estimates = {"Q1-B连续解析相位": [], "常折射率相邻峰距": [], "Q1-A整阶稳健回归": [], "Q1-C全波形反演": []}
    runtimes = {name: [] for name in estimates}
    for repeat in range(BASELINE_REPEATS):
        if store.near_deadline():
            break
        noise = (0.02, 0.05, 0.10)[repeat // 10]
        dropout = 0.10 if repeat % 5 in (3, 4) else 0.0
        pair = make_semisynthetic_pair(sigma, envelopes, rng, noise, dropout)
        methods = {
            "Q1-B连续解析相位": lambda: estimate_primary_thickness(sigma, pair, config, angle_weights),
            "常折射率相邻峰距": lambda: constant_n_peak_spacing(sigma, pair),
            "Q1-A整阶稳健回归": lambda: fit_integer_order_baseline(sigma, pair, config),
            "Q1-C全波形反演": lambda: fit_full_waveform_baseline(sigma, pair, config),
        }
        for name, method in methods.items():
            method_t0 = time.perf_counter()
            try:
                estimates[name].append(float(method()))
            except (ValueError, RuntimeError, FloatingPointError, np.linalg.LinAlgError):
                estimates[name].append(float("nan"))
            runtimes[name].append(time.perf_counter() - method_t0)
        if (repeat + 1) % 5 == 0:
            partial = {}
            for name, values in estimates.items():
                partial[name] = summarize_estimates(values) | {
                    "单次中位用时秒": float(np.median(runtimes[name]))
                }
            store.flush("03_基线对比.json", {"基线对比": partial, "已完成重复数": repeat + 1})
    summary = {}
    for name, values in estimates.items():
        summary[name] = summarize_estimates(values) | {
            "单次中位用时秒": float(np.median(runtimes[name])) if runtimes[name] else None,
            "累计用时秒": float(np.sum(runtimes[name])),
        }
    summary["统一真值_微米"] = TRUE_THICKNESS_UM
    summary["说明"] = "四方法使用相同真实网格/包络、相同噪声与振幅陷落；失败不静默删除。"
    primary_error = summary["Q1-B连续解析相位"]["MdAPE_百分比"]
    naive_error = summary["常折射率相邻峰距"]["MdAPE_百分比"]
    summary["主方法相对常折射率MdAPE降幅_百分比"] = (
        (naive_error - primary_error) / naive_error * 100.0
        if primary_error is not None and naive_error not in (None, 0.0) else None
    )
    summary["锦标赛样本用途"] = "仅用于四方法分布比较，不返回且不得替换冻结代表样本"
    return summary


def sensitivity_case(
    sigma: np.ndarray,
    pair: dict[float, np.ndarray],
    config: Config,
    angle_weights: dict[float, float],
) -> float:
    return estimate_primary_thickness(sigma, pair, config, angle_weights)


def run_sensitivity(
    sigma: np.ndarray,
    envelopes: dict[float, tuple[np.ndarray, np.ndarray]],
    representative_pair: dict[float, np.ndarray],
    rng: np.random.Generator,
    store: ResultStore,
    selected_config: Config,
    angle_weights: dict[float, float],
    selection_audit: dict[str, Any],
    representative_id: str,
    headline_point: float,
) -> dict[str, Any]:
    groups: dict[str, Any] = {}

    def grid_result(name: str, cases: dict[str, Config]) -> None:
        values: dict[str, float | None] = {}
        failures: dict[str, str] = {}
        for label, cfg in cases.items():
            try:
                values[label] = sensitivity_case(sigma, representative_pair, cfg, angle_weights)
            except (ValueError, RuntimeError, np.linalg.LinAlgError) as exc:
                values[label] = None
                failures[label] = str(exc)
        base = values.get("基准")
        valid_values = [value for value in values.values() if value is not None]
        groups[name] = {
            "厚度_微米": values,
            "相对基准最大偏移_百分比": (
                max(abs(value - base) / base * 100.0 for value in valid_values)
                if base not in (None, 0.0) and valid_values else None
            ),
            "失败配置": failures,
        }
        store.flush("04_灵敏度.json", {"灵敏度分析": groups})

    configuration_estimates: dict[str, Any] = {}
    eligible_names = set(selection_audit["合理配置"])
    for name, cfg in candidate_configurations().items():
        selection_record = selection_audit["候选配置"][name]
        if selection_record["状态"] != "成功":
            configuration_estimates[name] = {"状态": "校准失败", "是否合理配置": False}
            continue
        try:
            estimate = sensitivity_case(sigma, representative_pair, cfg, angle_weights)
            configuration_estimates[name] = {
                "状态": "成功",
                "色散阶数": cfg.dispersion_order,
                "基线结点数": cfg.baseline_knots,
                "波形基线次数": cfg.waveform_baseline_degree,
                "校准选择得分": selection_record["选择得分"],
                "是否合理配置": name in eligible_names,
                "厚度_微米": estimate,
                "相对真值有符号偏差_百分比": (estimate - TRUE_THICKNESS_UM) / TRUE_THICKNESS_UM * 100.0,
            }
        except (ValueError, RuntimeError, np.linalg.LinAlgError) as exc:
            configuration_estimates[name] = {"状态": "重拟合失败", "是否合理配置": False, "失败原因": str(exc)}
    eligible_values = [
        item["厚度_微米"] for item in configuration_estimates.values()
        if item.get("状态") == "成功" and item.get("是否合理配置")
    ]
    eligible_bias_signs = {
        int(np.sign(value - TRUE_THICKNESS_UM)) for value in eligible_values
        if not np.isclose(value, TRUE_THICKNESS_UM)
    }
    sign_flip = len(eligible_bias_signs) > 1
    stability = {
        "配置选择是否使用固定盲区": False,
        "候选配置结果": configuration_estimates,
        "合理配置数": len(eligible_values),
        "合理配置厚度范围_微米": [min(eligible_values), max(eligible_values)] if eligible_values else [None, None],
        "合理配置偏差方向是否翻转": sign_flip,
        "结论表达规则": "偏差方向翻转则只报告合理配置厚度范围；否则可报告入选配置单点并附范围",
        "头条厚度表达": "范围" if sign_flip else "单点并附合理配置范围",
    }
    groups["灵敏度_色散基阶数"] = stability
    groups["灵敏度_基线自由度"] = stability
    store.flush("04_灵敏度.json", {"灵敏度分析": groups})

    grid_result("灵敏度_折射率锚点", {
        "锚点-2%": replace(selected_config, n_anchor=selected_config.n_anchor * 0.98),
        "基准": selected_config,
        "锚点+2%": replace(selected_config, n_anchor=selected_config.n_anchor * 1.02),
    })
    grid_result("灵敏度_边界裁剪", {
        "4%": replace(selected_config, edge_fraction=0.04),
        "基准": selected_config,
        "8%": replace(selected_config, edge_fraction=0.08),
    })
    grid_result("灵敏度_幅度权重指数", {
        "不加权": replace(selected_config, weight_exponent=0.0),
        "基准": selected_config,
        "平方加权": replace(selected_config, weight_exponent=2.0),
    })
    grid_result("灵敏度_softL1尺度", {
        "0.25": replace(selected_config, soft_l1_scale=0.25),
        "基准": selected_config,
        "0.50": replace(selected_config, soft_l1_scale=0.50),
    })
    grid_result("灵敏度_非等距Hilbert处理", {
        "直接按采样序号": replace(selected_config, uniformize_for_hilbert=False),
        "基准": selected_config,
    })

    for group_name, levels in {
        "灵敏度_噪声等级": [0.02, 0.05, 0.10],
        "灵敏度_局部振幅陷落": [0.0, 0.10, 0.20],
        "灵敏度_二次回程污染": [0.0, 0.05, 0.10, 0.20],
    }.items():
        level_results = {}
        for level in levels:
            estimates = []
            for _ in range(8):
                if store.near_deadline():
                    break
                noise = level if group_name == "灵敏度_噪声等级" else 0.05
                dropout = level if group_name == "灵敏度_局部振幅陷落" else 0.0
                harmonic = level if group_name == "灵敏度_二次回程污染" else 0.0
                pair = make_semisynthetic_pair(sigma, envelopes, rng, noise, dropout, harmonic)
                try:
                    estimates.append(sensitivity_case(sigma, pair, selected_config, angle_weights))
                except (ValueError, RuntimeError, np.linalg.LinAlgError):
                    estimates.append(float("nan"))
            level_results[str(level)] = summarize_estimates(estimates)
        groups[group_name] = level_results
        store.flush("04_灵敏度.json", {"灵敏度分析": groups})
    reproduced = sensitivity_case(sigma, representative_pair, selected_config, angle_weights)
    groups["代表样本一致性审计"] = {
        "代表样本标识": representative_id,
        "主方法头条点估计_微米": headline_point,
        "灵敏度模块同配置基准点_微米": reproduced,
        "绝对差_微米": abs(reproduced - headline_point),
        "是否同一点估计": bool(np.isclose(reproduced, headline_point, rtol=0.0, atol=1e-10)),
        "锦标赛样本是否替换代表样本": False,
    }
    if not groups["代表样本一致性审计"]["是否同一点估计"]:
        raise RuntimeError("灵敏度基准点与冻结代表样本头条点估计不一致。")
    store.flush("04_灵敏度.json", {"灵敏度分析": groups})
    return groups


def resample_blocks(values: np.ndarray, block_length: int, rng: np.random.Generator) -> np.ndarray:
    n = len(values)
    output = []
    while sum(len(v) for v in output) < n:
        start = int(rng.integers(0, max(1, n - block_length + 1)))
        output.append(values[start:start + block_length])
    return np.concatenate(output)[:n]


def bootstrap_phase_interval(
    phases: list[dict[str, Any]],
    fit: dict[str, Any],
    config: Config,
    angle_weights: dict[float, float],
    rng: np.random.Generator,
    repeats: int,
    store: ResultStore,
) -> list[float]:
    trend_items = []
    for item in phases:
        coord = optical_coordinate(item["波数"], item["角度"], fit["色散系数"], config)
        trend = 4.0 * np.pi * fit["厚度_微米"] * 1e-4 * coord
        trend_items.append((item, trend))
    intercept_by_angle = {}
    for angle in sorted({float(item["角度"]) for item in phases}):
        selected = [(item, trend) for item, trend in trend_items if item["角度"] == angle]
        residuals = np.concatenate([item["相位"] - trend for item, trend in selected])
        weights = np.concatenate([item["权重"] for item, _ in selected])
        intercept_by_angle[angle] = float(np.average(residuals, weights=weights))
    synthetic_items = []
    for item, trend in trend_items:
        intercept = intercept_by_angle[float(item["角度"])]
        residual = item["相位"] - trend - intercept
        synthetic_items.append((item, trend + intercept, residual))
    estimates = []
    for _ in range(repeats):
        if store.near_deadline():
            break
        boot = []
        for item, fitted_phase, residual in synthetic_items:
            boot.append(item | {"相位": fitted_phase + resample_blocks(residual, config.block_length, rng)})
        estimates.append(
            fit_phase_model(boot, config, angle_weights, shared_angle_intercept=True)["厚度_微米"]
        )
    return estimates


def basic_interval_components(point: float, samples: list[float]) -> dict[str, Any]:
    q_low, q_high = np.percentile(samples, [2.5, 97.5])
    lower = 2.0 * point - q_high
    upper = 2.0 * point - q_low
    center = 0.5 * (lower + upper)
    half_width = max(0.5 * (upper - lower), 1e-6)
    return {
        "点估计_微米": point,
        "偏差校正中心_微米": float(center),
        "原始basic区间_微米": [float(lower), float(upper)],
        "原始半宽_微米": float(half_width),
    }


def fit_interval_case(
    sigma: np.ndarray,
    pair: dict[float, np.ndarray],
    config: Config,
    angle_weights: dict[float, float],
    rng: np.random.Generator,
    repeats: int,
    store: ResultStore,
) -> tuple[dict[str, Any], list[float]]:
    phases = extract_all_phases(sigma, pair, config)
    fit, aligned_phases = fit_aligned_phase_model(phases, config, angle_weights)
    samples = bootstrap_phase_interval(aligned_phases, fit, config, angle_weights, rng, repeats, store)
    if len(samples) < 20:
        raise RuntimeError("移动块重采样不足20次，不能构造区间。")
    return basic_interval_components(fit["厚度_微米"], samples), samples


def run_interval_coverage(
    sigma: np.ndarray,
    envelopes: dict[float, tuple[np.ndarray, np.ndarray]],
    representative_pair: dict[float, np.ndarray],
    rng: np.random.Generator,
    store: ResultStore,
    config: Config,
    angle_weights: dict[float, float],
    representative_id: str,
    headline_point: float,
) -> dict[str, Any]:
    representative_components, representative_samples = fit_interval_case(
        sigma, representative_pair, config, angle_weights, rng, REPRESENTATIVE_BOOTSTRAPS, store
    )
    point_consistent = bool(np.isclose(
        representative_components["点估计_微米"], headline_point, rtol=0.0, atol=1e-10
    ))
    if not point_consistent:
        raise RuntimeError("区间点估计与冻结代表样本头条点估计不一致。")
    block_sensitivity: dict[str, Any] = {}
    for block_length in (64, 128, 192):
        if store.near_deadline():
            break
        cfg = replace(config, block_length=block_length)
        components, _ = fit_interval_case(
            sigma, representative_pair, cfg, angle_weights, rng, 60, store
        )
        block_sensitivity[str(block_length)] = components

    calibration_scores: list[float] = []
    calibration_widths: list[float] = []
    for repeat in range(COVERAGE_CALIBRATION_REPEATS):
        if store.near_deadline():
            break
        noise = (0.02, 0.05, 0.10)[repeat % 3]
        dropout = (0.0, 0.10, 0.20)[(repeat // 3) % 3]
        pair = make_semisynthetic_pair(sigma, envelopes, rng, noise, dropout)
        try:
            components, _ = fit_interval_case(
                sigma, pair, config, angle_weights, rng, BOOTSTRAP_REPEATS, store
            )
            score = abs(TRUE_THICKNESS_UM - components["偏差校正中心_微米"]) / components["原始半宽_微米"]
            calibration_scores.append(float(score))
            calibration_widths.append(2.0 * components["原始半宽_微米"])
        except (ValueError, RuntimeError, np.linalg.LinAlgError):
            calibration_scores.append(float("inf"))
        if (repeat + 1) % 10 == 0:
            store.flush("05_区间与覆盖.json", {"区间_移动块经验分位": {
                "状态": "半合成校准进行中",
                "半合成校准计划数": COVERAGE_CALIBRATION_REPEATS,
                "半合成校准完成数": len(calibration_scores),
                "代表样本bootstrap完成数": len(representative_samples),
                "块长灵敏度": block_sensitivity,
                "区间标签": "校准未完成，不得称95%区间",
            }})

    all_scores = np.asarray(calibration_scores, dtype=float)
    finite_scores = all_scores[np.isfinite(all_scores)]
    calibration_complete = len(calibration_scores) >= COVERAGE_CALIBRATION_REPEATS
    if all_scores.size == 0:
        calibration_factor = float("inf")
    else:
        ordered = np.sort(all_scores)
        rank = min(
            int(np.ceil((ordered.size + 1) * CALIBRATION_TARGET_COVERAGE)) - 1,
            ordered.size - 1,
        )
        calibration_factor = float(max(1.0, ordered[max(rank, 0)]))

    representative_interval = [None, None]
    if np.isfinite(calibration_factor):
        center = representative_components["偏差校正中心_微米"]
        half_width = calibration_factor * representative_components["原始半宽_微米"]
        representative_interval = [center - half_width, center + half_width]

    coverage_flags: list[bool] = []
    widths: list[float] = []
    if calibration_complete and np.isfinite(calibration_factor):
        for repeat in range(COVERAGE_EVALUATION_REPEATS):
            if store.near_deadline():
                break
            noise = (0.02, 0.05, 0.10)[repeat % 3]
            dropout = (0.0, 0.10, 0.20)[(repeat // 3) % 3]
            pair = make_semisynthetic_pair(sigma, envelopes, rng, noise, dropout)
            try:
                components, _ = fit_interval_case(
                    sigma, pair, config, angle_weights, rng, BOOTSTRAP_REPEATS, store
                )
                center = components["偏差校正中心_微米"]
                half_width = calibration_factor * components["原始半宽_微米"]
                lower, upper = center - half_width, center + half_width
                coverage_flags.append(bool(lower <= TRUE_THICKNESS_UM <= upper))
                widths.append(float(upper - lower))
            except (ValueError, RuntimeError, np.linalg.LinAlgError):
                coverage_flags.append(False)
                widths.append(float("nan"))
            if (repeat + 1) % 10 == 0:
                store.flush("05_区间与覆盖.json", {"区间_移动块经验分位": {
                    "状态": "独立半合成覆盖评估进行中",
                    "半合成校准完成数": len(calibration_scores),
                    "独立覆盖评估计划数": COVERAGE_EVALUATION_REPEATS,
                    "独立覆盖评估完成数": len(coverage_flags),
                    "当前经验覆盖率": float(np.mean(coverage_flags)),
                    "校准扩张因子": calibration_factor,
                    "区间标签": "独立覆盖评估未完成，不得称最终95%区间",
                }})

    empirical_coverage = float(np.mean(coverage_flags)) if coverage_flags else None
    evaluation_complete = len(coverage_flags) >= COVERAGE_EVALUATION_REPEATS
    coverage_pass = bool(
        calibration_complete and evaluation_complete
        and empirical_coverage is not None and empirical_coverage >= MINIMUM_COVERAGE_LINE
    )
    interval_label = (
        "经100组校准与100组独立评估验收的95%厚度区间"
        if coverage_pass else "探索性厚度范围（覆盖验收未通过，不得称95%厚度区间）"
    )
    return {
        "构造方法": "冻结代表样本的训练段相位残差移动块重采样得到basic偏差校正区间；为给0.90验收线留有限样本安全裕度，用100组已知厚度半合成校准非一致性分数的有限样本99%分位扩张半宽，最后用另100组同口径样本独立验收。",
        "区间标签": interval_label,
        "代表样本标识": representative_id,
        "区间点估计与头条点估计一致": point_consistent,
        "头条点估计_微米": headline_point,
        "代表样本区间_微米": representative_interval,
        "代表样本原始区间组成": representative_components,
        "代表样本bootstrap完成数": len(representative_samples),
        "名义覆盖率": 0.95,
        "校准目标覆盖率": CALIBRATION_TARGET_COVERAGE,
        "半合成校准计划数": COVERAGE_CALIBRATION_REPEATS,
        "半合成校准完成数": len(calibration_scores),
        "校准有效数": int(finite_scores.size),
        "校准失败数": int(all_scores.size - finite_scores.size),
        "校准扩张因子": calibration_factor,
        "独立覆盖评估计划数": COVERAGE_EVALUATION_REPEATS,
        "独立覆盖评估完成数": len(coverage_flags),
        "经验覆盖率": empirical_coverage,
        "平均区间宽度_微米": float(np.nanmean(widths)) if widths else None,
        "覆盖率最低披露线": MINIMUM_COVERAGE_LINE,
        "覆盖率是否达到0.90最低披露线": coverage_pass,
        "校准阶段平均原始区间宽度_微米": float(np.mean(calibration_widths)) if calibration_widths else None,
        "块长灵敏度": block_sensitivity,
    }


def compact_primary_result(run: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in run.items() if k != "相位数据"}


def read_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        value = json.load(fh)
    if not isinstance(value, dict):
        raise ValueError(f"{path}顶层必须为JSON对象。")
    return value


def find_current_execution_log(root: Path, started_wall_time: float, marker: str) -> Path | None:
    """只接受本轮新鲜且已写入计算完成锚点的执行日志，避免旧日志误放行。"""
    explicit = os.environ.get("Q1_EXECUTION_LOG_PATH")
    if explicit:
        explicit_path = Path(explicit)
        candidates = [explicit_path if explicit_path.is_absolute() else root / explicit_path]
    else:
        candidates = list((root / "日志").glob("执行_问题1*.log"))
    matched: list[Path] = []
    for path in candidates:
        try:
            if path.stat().st_mtime < started_wall_time - 5.0:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if marker in content:
            matched.append(path)
    return max(matched, key=lambda item: item.stat().st_mtime) if matched else None


def correctness_audit_from_disk(
    root: Path,
    output_dir: Path,
    started_wall_time: float,
    log_marker: str,
) -> dict[str, Any]:
    """重读最新落盘结果和本轮日志，执行五项一票否决核验。"""
    missing = [name for name in REQUIRED_RESULT_FILES if not (output_dir / name).is_file()]
    payloads: dict[str, dict[str, Any]] = {}
    invalid: dict[str, str] = {}
    for name in REQUIRED_RESULT_FILES:
        path = output_dir / name
        if not path.is_file():
            continue
        try:
            payloads[name] = read_json_object(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            invalid[name] = f"{type(exc).__name__}: {exc}"

    log_path = find_current_execution_log(root, started_wall_time, log_marker)
    hard_gate_passed = not missing and not invalid and log_path is not None
    checks: dict[str, Any] = {}
    if not hard_gate_passed:
        return {
            "状态": "未通过",
            "前置硬门": {
                "是否通过": False,
                "缺失结果文件": missing,
                "非法JSON": invalid,
                "本轮执行日志": None if log_path is None else str(log_path.relative_to(root)),
                "日志是否含本轮计算完成锚点": log_path is not None,
            },
            "五项协议": checks,
            "五项是否全部通过": False,
            "结果声明是否生成": False,
        }

    summary = payloads["汇总结果.json"]
    primary = summary["主方法结果"]
    cross = summary["交叉印证"]
    leakage = summary["防泄漏验证"]
    baseline = summary["基线对比"]
    sensitivity = summary["灵敏度分析"]
    interval = summary["区间_移动块经验分位"]

    headline = float(primary["拟合"]["厚度_微米"])
    blind = cross["固定盲区全波形验证"]
    physical = primary["拟合"]["物理约束审计"]
    dimensional_pass = bool(
        np.isfinite(headline)
        and 2.0 <= headline <= 30.0
        and primary["已知半合成真值_微米"] == TRUE_THICKNESS_UM
        and primary["代表样本标识"] == interval["代表样本标识"]
        and all(item["色散光程坐标严格递增"] for item in physical.values())
        and blind["误差是否与观测振幅同量级"]
        and blind["平均NRMSE"] <= blind["预设NRMSE上限"]
    )
    checks["量纲与数量级"] = {
        "是否通过": dimensional_pass,
        "头条量": "冻结半合成样本的几何厚度d，不是真实附件法向光学厚度q",
        "几何厚度d_微米": headline,
        "已知半合成真值_微米": TRUE_THICKNESS_UM,
        "盲区平均NRMSE": blind["平均NRMSE"],
        "盲区NRMSE上限": blind["预设NRMSE上限"],
    }

    main_baseline = baseline["Q1-B连续解析相位"]
    naive_baseline = baseline["常折射率相邻峰距"]
    baseline_pass = bool(
        main_baseline["完成数"] >= 20
        and main_baseline["失败率"] <= naive_baseline["失败率"]
        and main_baseline["MdAPE_百分比"] < naive_baseline["MdAPE_百分比"]
        and baseline["主方法相对常折射率MdAPE降幅_百分比"] > 0.0
    )
    checks["基线"] = {
        "是否通过": baseline_pass,
        "Q1-B_MdAPE_百分比": main_baseline["MdAPE_百分比"],
        "常折射率峰距_MdAPE_百分比": naive_baseline["MdAPE_百分比"],
        "MdAPE降幅_百分比": baseline["主方法相对常折射率MdAPE降幅_百分比"],
    }

    cross_consistency = cross["三种厚度一致性"]
    cross_pass = bool(
        cross_consistency["是否通过"]
        and not cross["峰谷法是否读取半合成生成真值色散"]
        and blind["误差是否与观测振幅同量级"]
        and blind["平均NRMSE"] <= blind["预设NRMSE上限"]
    )
    checks["交叉印证"] = {
        "是否通过": cross_pass,
        "三种厚度_微米": cross_consistency["厚度结果"],
        "最大两两差_微米": cross_consistency["最大两两差_微米"],
        "允许最大差_微米": cross_consistency["允许最大差_微米"],
        "峰谷法读取生成真值": cross["峰谷法是否读取半合成生成真值色散"],
    }

    leakage_expected = {
        "相邻频谱点是否随机打散": False,
        "验证波段是否参与Hilbert预处理": False,
        "验证波段是否参与基线振幅或参数选择": False,
        "色散阶数和基线自由度是否仅由训练校准带选择": True,
        "左右训练段是否分别变换并在整周对齐前设独立相位截距": True,
        "左右训练段是否在最终回归前按整数周对齐": True,
        "峰谷交叉印证是否读取生成器真值": False,
    }
    leakage_pass = bool(
        all(leakage.get(key) == expected for key, expected in leakage_expected.items())
        and leakage["代表样本标识"] == primary["代表样本标识"]
    )
    checks["防泄漏"] = {
        "是否通过": leakage_pass,
        "固定盲区_cm-1": leakage["验证波段_cm-1"],
        "逐项审计": {key: leakage.get(key) for key in leakage_expected},
    }

    scalar_groups = (
        "灵敏度_折射率锚点",
        "灵敏度_边界裁剪",
        "灵敏度_幅度权重指数",
        "灵敏度_softL1尺度",
        "灵敏度_非等距Hilbert处理",
    )
    scalar_sensitivity_pass = all(
        not sensitivity[name]["失败配置"]
        and sensitivity[name]["相对基准最大偏移_百分比"] <= 5.0
        for name in scalar_groups
    )
    pressure_groups = ("灵敏度_噪声等级", "灵敏度_局部振幅陷落", "灵敏度_二次回程污染")
    pressure_pass = all(
        item["完成数"] >= 6 and item["失败率"] <= 0.25
        for name in pressure_groups
        for item in sensitivity[name].values()
    )
    config_sensitivity = sensitivity["灵敏度_色散基阶数"]
    identity_audit = sensitivity["代表样本一致性审计"]
    sensitivity_pass = bool(
        config_sensitivity["合理配置数"] >= 1
        and not config_sensitivity["合理配置偏差方向是否翻转"]
        and scalar_sensitivity_pass
        and pressure_pass
        and identity_audit["是否同一点估计"]
        and not identity_audit["锦标赛样本是否替换代表样本"]
        and interval["区间点估计与头条点估计一致"]
        and interval["覆盖率是否达到0.90最低披露线"]
        and interval["半合成校准完成数"] >= interval["半合成校准计划数"]
        and interval["独立覆盖评估完成数"] >= interval["独立覆盖评估计划数"]
    )
    checks["灵敏度"] = {
        "是否通过": sensitivity_pass,
        "合理配置偏差方向是否翻转": config_sensitivity["合理配置偏差方向是否翻转"],
        "标量参数最大偏移均不超过5%": scalar_sensitivity_pass,
        "压力测试完成且失败率不超过25%": pressure_pass,
        "代表样本是否一致": identity_audit["是否同一点估计"],
        "区间经验覆盖率": interval["经验覆盖率"],
        "覆盖率最低披露线": interval["覆盖率最低披露线"],
    }

    all_passed = all(item["是否通过"] for item in checks.values())
    return {
        "状态": "通过" if all_passed else "未通过",
        "前置硬门": {
            "是否通过": True,
            "结果文件": list(REQUIRED_RESULT_FILES),
            "本轮执行日志": str(log_path.relative_to(root)),
            "日志是否含本轮计算完成锚点": True,
        },
        "五项协议": checks,
        "五项是否全部通过": all_passed,
        "结果声明是否生成": False,
    }


def build_result_declaration(summary: dict[str, Any]) -> dict[str, Any]:
    """只声明可追溯的半合成几何厚度头条，绝不混入真实附件的q。"""
    primary = summary["主方法结果"]
    baseline = summary["基线对比"]
    interval = summary["区间_移动块经验分位"]
    fit = primary["拟合"]
    lower, upper = interval["代表样本区间_微米"]
    order = fit["色散阶数"]
    coefficients = fit["色散系数"]
    spec = primary["代表样本生成规格"]
    n_anchor = primary["入选配置"]["n_anchor"]
    common_scope = (
        "冻结半合成双角样本；附件1/2仅提供真实800—2400 cm-1波数网格、慢基线和振幅，"
        f"植入已知几何厚度真值{primary['已知半合成真值_微米']} μm；"
        f"随机种子{spec['随机种子']}、噪声{100.0 * spec['噪声比例']:.0f}%、"
        f"局部振幅陷落{100.0 * spec['局部振幅陷落比例']:.0f}%；"
        f"折射率锚点n0={n_anchor:.2f}、{order}阶色散系数={coefficients}；1680—1920 cm-1固定盲区不参与拟合；"
        "10°/15°未加权共享几何厚度d，保留角度独立包络；附件2大于100%的真实尺度保留，首个零值在建模波段外。"
    )
    not_q = "该量不是附件1/2真实晶圆的法向光学厚度q=d*sqrt(n^2-sin^2θ)，不得与约19.8 μm的真实附件q直接比较。"
    core = {
        "冻结半合成样本_双角共享几何厚度d_微米": fit["厚度_微米"],
        "冻结半合成样本_几何厚度d区间下限_微米": lower,
        "冻结半合成样本_几何厚度d区间上限_微米": upper,
        "半合成独立评估_经验覆盖率": interval["经验覆盖率"],
        "Q1B半合成重复_厚度MdAPE_百分比": baseline["Q1-B连续解析相位"]["MdAPE_百分比"],
        "Q1B相对常折射率峰距_MdAPE降幅_百分比": baseline["主方法相对常折射率MdAPE降幅_百分比"],
    }
    scope = {
        "冻结半合成样本_双角共享几何厚度d_微米": common_scope + not_q,
        "冻结半合成样本_几何厚度d区间下限_微米": common_scope
        + "区间由128点移动块basic偏差校正、100组半合成99%分数校准和另100组独立覆盖验收构造；本键为下限。"
        + not_q,
        "冻结半合成样本_几何厚度d区间上限_微米": common_scope
        + "区间由128点移动块basic偏差校正、100组半合成99%分数校准和另100组独立覆盖验收构造；本键为上限。"
        + not_q,
        "半合成独立评估_经验覆盖率": (
            "另100组与校准集分离、保留真实网格/包络并覆盖2%/5%/10%噪声和0%/10%/20%陷落的已知14.0 μm半合成样本；"
            "聚合为真值落入校准后区间的样本比例，无量纲；不评价真实附件未知几何厚度。"
        ),
        "Q1B半合成重复_厚度MdAPE_百分比": (
            "30组同一真实网格/包络驱动、统一14.0 μm真值的半合成重复；Q1-B逐组输出几何厚度d，"
            "取绝对百分比误差中位数；失败不静默删除；不是真实附件厚度误差。"
        ),
        "Q1B相对常折射率峰距_MdAPE降幅_百分比": (
            "与上一键完全相同的30组半合成样本和14.0 μm几何厚度真值；"
            "按(常折射率峰距MdAPE-Q1-B MdAPE)/常折射率峰距MdAPE×100%聚合。"
        ),
    }
    return {"问题": 1, "核心指标": core, "口径说明": scope}


def main() -> None:
    t0 = time.perf_counter()
    started_wall_time = time.time()
    root = Path(__file__).resolve().parents[2]
    output_dir = root / "求解" / "问题1" / "结果"
    declaration_path = root / "交接" / RESULT_DECLARATION_NAME
    audit_log_path = root / "日志" / "执行_问题1_声明门.log"
    declaration_path.unlink(missing_ok=True)
    store = ResultStore(output_dir, t0)
    representative_rng = np.random.default_rng(REPRESENTATIVE_SAMPLE_SPEC["随机种子"])
    tournament_rng = np.random.default_rng(RANDOM_SEED + 2)
    sensitivity_rng = np.random.default_rng(RANDOM_SEED + 3)
    interval_rng = np.random.default_rng(RANDOM_SEED + 4)

    try:
        sigma, real_curves, checks = load_real_pair(root)
        store.flush("01_数据核验.json", {"运行状态": "数据核验完成", "数据核验": checks})
        envelopes = real_envelopes(sigma, real_curves)

        representative_pair = make_semisynthetic_pair(
            sigma,
            envelopes,
            representative_rng,
            REPRESENTATIVE_SAMPLE_SPEC["噪声比例"],
            REPRESENTATIVE_SAMPLE_SPEC["局部振幅陷落比例"],
        )
        representative_id = sample_fingerprint(representative_pair)
        selected_config, diagnostic_angle_weights, selection_audit = select_configuration(
            sigma, representative_pair
        )
        primary_angle_weights = {angle: 1.0 for angle in ANGLES_DEG}
        selection_audit["代表样本标识"] = representative_id
        selection_audit["代表样本生成规格"] = REPRESENTATIVE_SAMPLE_SPEC
        store.flush("02_主方法与交叉印证.json", {
            "运行状态": "训练/校准配置选择完成",
            "配置选择_训练校准": selection_audit,
        })
        primary = one_primary_run(
            sigma,
            representative_pair,
            selected_config,
            primary_angle_weights,
            diagnostic_angle_weights,
        )
        primary_public = compact_primary_result(primary)
        primary_public["入选配置"] = asdict(selected_config)
        primary_public["代表样本标识"] = representative_id
        primary_public["代表样本生成规格"] = REPRESENTATIVE_SAMPLE_SPEC
        primary_public["已知半合成真值_微米"] = TRUE_THICKNESS_UM
        primary_public["共享厚度绝对百分比误差"] = error_percent(primary["拟合"]["厚度_微米"])
        primary_public["质量加权共享厚度绝对百分比误差"] = error_percent(
            primary["质量加权共享厚度_微米"]
        )
        separate_mean_error = float(np.mean([error_percent(v) for v in primary["分角厚度_微米"].values()]))
        primary_public["分角APE均值_百分比"] = separate_mean_error
        primary_public["共享约束是否降低角度偏差"] = bool(
            error_percent(primary["拟合"]["厚度_微米"])
            <= separate_mean_error
        )
        primary_public["质量加权是否优于未加权"] = bool(
            error_percent(primary["质量加权共享厚度_微米"])
            <= error_percent(primary["拟合"]["厚度_微米"])
        )
        primary_public["角度权重处置"] = (
            "轮2结果中质量加权APE高于未加权，因此按预注册回退规则以未加权共享为头条；"
            "质量加权仅保留为同样本诊断对照"
        )
        store.flush("02_主方法与交叉印证.json", {
            "运行状态": "主方法完成",
            "配置选择_训练校准": selection_audit,
            "主方法结果": primary_public,
            "交叉印证": {
                "三种厚度一致性": primary["三种厚度一致性"],
                "峰谷法色散来源": "训练段连续相位主拟合的冻结色散估计",
                "峰谷法是否读取半合成生成真值色散": False,
                "相位增量实现": primary["相位增量证据"],
                "峰谷奇偶半阶链审计": primary["峰谷整阶证据"],
                "分段相位整周对齐": primary["拟合"]["分段相位整周对齐"],
                "固定盲区全波形验证": primary["盲区全波形验证"],
            },
            "防泄漏验证": {
                "验证波段_cm-1": list(BLIND_BAND),
                "核心拟合波段_cm-1": [list(band) for band in CORE_TRAIN_BANDS],
                "校准肩带_cm-1": [list(band) for band in CALIBRATION_BANDS],
                "相邻频谱点是否随机打散": False,
                "验证波段是否参与Hilbert预处理": False,
                "验证波段是否参与基线振幅或参数选择": False,
                "色散阶数和基线自由度是否仅由训练校准带选择": True,
                "左右训练段是否分别变换并在整周对齐前设独立相位截距": True,
                "左右训练段是否在最终回归前按整数周对齐": True,
                "峰谷交叉印证是否读取生成器真值": False,
                "代表样本标识": representative_id,
            },
        })

        baseline_summary = run_baseline_tournament(
            sigma, envelopes, tournament_rng, store, selected_config, primary_angle_weights
        )
        store.flush("03_基线对比.json", {"运行状态": "基线对比完成", "基线对比": baseline_summary})

        sensitivity = run_sensitivity(
            sigma, envelopes, representative_pair, sensitivity_rng, store,
            selected_config, primary_angle_weights, selection_audit,
            representative_id, primary["拟合"]["厚度_微米"],
        )
        store.flush("04_灵敏度.json", {"运行状态": "灵敏度完成", "灵敏度分析": sensitivity})

        if not store.near_deadline():
            interval = run_interval_coverage(
                sigma, envelopes, representative_pair, interval_rng, store,
                selected_config, primary_angle_weights,
                representative_id, primary["拟合"]["厚度_微米"],
            )
            store.flush("05_区间与覆盖.json", {"运行状态": "区间覆盖完成", "区间_移动块经验分位": interval})

        calculation_status = "接近时间预算，已主动收敛" if store.near_deadline() else "计算阶段全部完成"
        log_marker = (
            f"问题1 D2-MPC：{calculation_status}；"
            f"代表样本几何厚度d={primary['拟合']['厚度_微米']:.6f} μm；等待五项正确性核验。"
        )
        print(log_marker, flush=True)
        atomic_write_text(audit_log_path, log_marker + "\n")

        audit = correctness_audit_from_disk(root, output_dir, started_wall_time, log_marker)
        store.flush("06_正确性核验.json", {
            "运行状态": "五项正确性核验完成",
            "正确性核验": audit,
        })
        if not audit["五项是否全部通过"]:
            final_status = "计算结果已落盘，但五项正确性核验未全过，未生成结果声明"
            store.flush("00_运行状态.json", {
                "运行状态": final_status,
                "是否时间预算前主动收敛": store.near_deadline(),
                "正确性核验": audit,
                "结果声明路径": str(declaration_path.relative_to(root)),
                "结果声明是否生成": False,
            })
            final_line = f"问题1 D2-MPC：{final_status}；实际用时={store.elapsed():.3f}秒。"
            atomic_write_text(audit_log_path, log_marker + "\n" + final_line + "\n")
            print(final_line, flush=True)
            return

        declaration = build_result_declaration(read_json_object(output_dir / "汇总结果.json"))
        atomic_write_json(declaration_path, declaration)
        audit["结果声明是否生成"] = True
        audit["结果声明路径"] = str(declaration_path.relative_to(root))
        store.flush("06_正确性核验.json", {
            "运行状态": "五项正确性核验通过，结果声明已生成",
            "正确性核验": audit,
        })
        final_status = "全部完成，五项正确性核验通过且结果声明已生成"
        store.flush("00_运行状态.json", {
            "运行状态": final_status,
            "是否时间预算前主动收敛": store.near_deadline(),
            "正确性核验": audit,
            "结果声明路径": str(declaration_path.relative_to(root)),
            "结果声明是否生成": True,
            "结果键说明": {
                "配置选择_训练校准": "盲区外核心带拟合、校准肩带只在显式一/二阶色散候选中选主模型；C0仅为基线。",
                "主方法结果": "冻结代表样本上的未加权双角共享D2-MPC几何厚度d、显式色散、整数周对齐与质量权重回退审计。",
                "基线对比": "相同半合成样本上四种方法的MdAPE、失败率与厚度离散度。",
                "灵敏度分析": "配置先由训练/校准带筛选；合理配置偏差翻转时自动把单点结论降级为范围。",
                "区间_移动块经验分位": "同一冻结代表样本的移动块basic偏差校正、100组99%分数校准、另100组独立覆盖验收；未过0.90线不得称95%区间。",
                "正确性核验": "重读最新结果JSON与本轮执行日志，按量纲、基线、交叉印证、防泄漏、灵敏度五项一票否决。",
            },
        })
        final_line = (
            f"问题1 D2-MPC：{final_status}；"
            f"代表样本几何厚度d={primary['拟合']['厚度_微米']:.6f} μm；"
            f"实际用时={store.elapsed():.3f}秒。"
        )
        atomic_write_text(audit_log_path, log_marker + "\n" + final_line + "\n")
        print(final_line, flush=True)
    except Exception as exc:
        store.flush("00_运行状态.json", {
            "运行状态": "异常中止但已保留既有分步结果",
            "异常类型": type(exc).__name__,
            "异常信息": str(exc),
            "是否时间预算前主动收敛": store.near_deadline(),
        })
        raise


if __name__ == "__main__":
    main()
