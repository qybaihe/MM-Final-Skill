import sys
sys.path.insert(0, '/tmp/蜂巢/pylibs')

import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.interpolate import LSQUnivariateSpline
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import least_squares
from sklearn.preprocessing import SplineTransformer


ROUTE_NAME = 'Q1-C 双角全波形可分离非线性反演'
SEED = 20260826
N_POINTS = 800
N_REPEATS = 30
TRUE_D_UM = 14.0
ANGLES = (10.0, 15.0)
NOISE_LEVELS = (0.02, 0.05, 0.10)
SOFT_STOP_SEC = 165.0
ESTIMATED_RUNTIME_SEC = 110


class TimeBudgetReached(RuntimeError):
    pass


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, allow_nan=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def robust_scale(x: np.ndarray) -> float:
    median = np.median(x)
    return max(1.4826 * np.median(np.abs(x - median)), 1e-8)


def robust_spline_baseline(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    knots = np.linspace(x[0], x[-1], 6)[1:-1]
    weights = np.ones_like(y, dtype=float)
    baseline = np.full_like(y, np.median(y), dtype=float)
    for _ in range(2):
        spline = LSQUnivariateSpline(x, y, knots, w=weights, k=3)
        baseline = spline(x)
        residual = y - baseline
        ratio = np.abs(residual) / (1.5 * robust_scale(residual))
        weights = np.ones_like(ratio)
        large = ratio > 1.0
        weights[large] = 1.0 / ratio[large]
        weights = np.clip(weights, 0.08, 1.0)
    return baseline


def load_real_small_sample(root: Path):
    curves = {}
    common_sigma = None
    for angle, filename in zip(ANGLES, ('附件1.xlsx', '附件2.xlsx')):
        frame = pd.read_excel(
            root / '数据' / filename,
            sheet_name='Sheet1', header=0, engine='openpyxl'
        )
        sigma_all = frame['波数 (cm-1)'].to_numpy(dtype=float)
        reflectance_all = frame['反射率 (%)'].to_numpy(dtype=float)
        mask = (sigma_all >= 800.0) & (sigma_all <= 2400.0)
        sigma_band = sigma_all[mask]
        reflectance_band = reflectance_all[mask]
        indices = np.rint(np.linspace(0, len(sigma_band) - 1, N_POINTS)).astype(int)
        sigma = sigma_band[indices]
        reflectance = reflectance_band[indices]
        if common_sigma is None:
            common_sigma = sigma
        elif not np.array_equal(common_sigma, sigma):
            raise ValueError('附件1/2的真实波数网格未对齐')
        curves[angle] = reflectance
    if common_sigma is None or len(common_sigma) != N_POINTS:
        raise ValueError('未取得预期的800个真实波数点')
    return common_sigma, curves


def extract_real_envelopes(sigma: np.ndarray, curves: dict):
    envelopes = {}
    for angle, reflectance in curves.items():
        baseline = robust_spline_baseline(sigma, reflectance)
        high_pass = reflectance - baseline
        amplitude = np.sqrt(2.0) * np.sqrt(
            gaussian_filter1d(high_pass ** 2, sigma=14.0, mode='nearest')
        )
        positive = amplitude[amplitude > 0]
        if positive.size == 0:
            raise ValueError(f'{angle:.0f}°真实曲线无法提取振幅')
        lower, upper = np.quantile(positive, [0.20, 0.90])
        amplitude = gaussian_filter1d(np.clip(amplitude, lower, upper), 18.0)
        envelopes[angle] = (baseline, amplitude)
    return envelopes


def refractive_index(sigma: np.ndarray, c1: float, c2: float) -> np.ndarray:
    u = (sigma - 1600.0) / 800.0
    return 2.60 + c1 * u + c2 * (u ** 2 - 1.0 / 3.0)


def make_semisynthetic_pair(
    sigma: np.ndarray,
    envelopes: dict,
    rng: np.random.Generator,
    noise_fraction: float,
    add_dropout: bool,
):
    n_true = refractive_index(sigma, c1=0.055, c2=-0.022)
    normalized_sigma = (sigma - sigma[0]) / (sigma[-1] - sigma[0])
    pair = {}
    for angle in ANGLES:
        baseline, base_amplitude = envelopes[angle]
        amplitude = base_amplitude.copy()
        if add_dropout:
            center = rng.uniform(0.28, 0.72)
            width = rng.uniform(0.012, 0.022)
            amplitude *= 1.0 - 0.94 * np.exp(
                -0.5 * ((normalized_sigma - center) / width) ** 2
            )
        optical_factor = np.sqrt(
            np.maximum(n_true ** 2 - np.sin(np.deg2rad(angle)) ** 2, 1e-10)
        )
        phase_offset = rng.uniform(-np.pi, np.pi)
        phase = (
            4.0 * np.pi * TRUE_D_UM * 1e-4 * sigma * optical_factor
            + phase_offset
        )
        noise_scale = noise_fraction * float(np.median(base_amplitude))
        pair[angle] = (
            baseline + amplitude * np.cos(phase)
            + rng.normal(0.0, noise_scale, size=sigma.size)
        )
    return pair


def spline_basis(u: np.ndarray, n_knots: int) -> np.ndarray:
    return SplineTransformer(
        n_knots=n_knots, degree=3, knots='uniform', include_bias=True
    ).fit_transform(u.reshape(-1, 1))


def fit_separable_full_wave(
    sigma: np.ndarray, pair: dict, global_start: float
) -> float:
    u = (sigma - 1600.0) / 800.0
    baseline_basis = spline_basis(u, n_knots=5)
    amplitude_basis = spline_basis(u, n_knots=4)
    scales = {angle: max(np.std(pair[angle]), 1.0) for angle in ANGLES}
    best = {'loss': np.inf, 'parameters': np.array([14.0, 0.0, 0.0])}

    def check_time() -> None:
        if time.perf_counter() - global_start >= SOFT_STOP_SEC:
            raise TimeBudgetReached

    def project_one_angle(parameters: np.ndarray, angle: float) -> np.ndarray:
        d_um, c1, c2 = parameters
        n_value = refractive_index(sigma, c1, c2)
        optical_squared = n_value ** 2 - np.sin(np.deg2rad(angle)) ** 2
        if np.any(optical_squared <= 0):
            return np.full_like(sigma, 1e6)
        phase = 4.0 * np.pi * d_um * 1e-4 * sigma * np.sqrt(optical_squared)
        # C(u)cos(phi)+S(u)sin(phi)=A(u)cos(phi-delta(u))。
        # C/S与基线系数一样在线性子问题中闭式消去，
        # 只对共享厚度和色散系数做非线性搜索。
        design = np.column_stack((
            baseline_basis,
            amplitude_basis * np.cos(phase)[:, None],
            amplitude_basis * np.sin(phase)[:, None],
        ))
        y = pair[angle]
        ridge = 1e-4
        penalty = np.sqrt(ridge) * np.eye(design.shape[1])
        penalty[0, 0] = 0.0
        coefficients = np.linalg.lstsq(
            np.vstack((design, penalty)),
            np.concatenate((y, np.zeros(design.shape[1]))),
            rcond=None,
        )[0]
        return y - design @ coefficients

    def joint_residual(parameters: np.ndarray) -> np.ndarray:
        check_time()
        residual = np.concatenate([
            project_one_angle(parameters, angle) / scales[angle]
            for angle in ANGLES
        ])
        loss = float(np.mean(residual ** 2))
        if loss < best['loss']:
            best['loss'] = loss
            best['parameters'] = np.asarray(parameters, dtype=float).copy()
        return residual

    # 45点网格不精确包含14.0 um真值，避免初始化泄漏。
    for d0 in np.linspace(2.0, 30.0, 45):
        joint_residual(np.array([d0, 0.0, 0.0]))
    least_squares(
        joint_residual,
        x0=best['parameters'],
        bounds=([2.0, -0.16, -0.10], [30.0, 0.16, 0.10]),
        x_scale=[8.0, 0.08, 0.05],
        max_nfev=36,
        ftol=1e-6,
        xtol=1e-6,
        gtol=1e-6,
    )
    return float(best['parameters'][0])


def result_payload(elapsed: float, errors: list, failures: int, stopped: bool):
    metric = round(float(np.median(errors)), 6) if errors else None
    return {
        '路线名': ROUTE_NAME,
        '核心指标_MdAPE_百分比': metric,
        '用时估计_秒': ESTIMATED_RUNTIME_SEC,
        '实际用时秒': round(float(elapsed), 3),
        '完成重复数': len(errors),
        '失败重复数': int(failures),
        '是否时间预算前主动收敛': bool(stopped),
        '口径说明': (
            '附件1/2共同真实800--2400 cm-1波数网格降采样至800点，'
            '从实测反射率提取角度独立慢基线和局部振幅，'
            '构造共享已知厚度14.0 um的10°/15°半合成光谱。'
            '三档高斯噪声各10次，部分重复加入局部振幅陷落模拟少量漏峰；'
            '每次双角共享厚度和二阶低维色散，'
            '角度独立样条基线/余弦-正弦振幅系数闭式消去；'
            '唯一指标为|d_hat-d0|/d0×100%的中位数，越小越好，'
            '单次失败按100%误差计入而不静默删除。'
        ),
    }


def main() -> None:
    t0 = time.perf_counter()
    root = Path(__file__).resolve().parents[2]
    output_path = root / '求解/问题1/原型结果/路线3.json'
    errors = []
    failures = 0
    stopped = False
    atomic_write_json(output_path, result_payload(0.0, errors, failures, stopped))

    sigma, real_curves = load_real_small_sample(root)
    envelopes = extract_real_envelopes(sigma, real_curves)
    rng = np.random.default_rng(SEED)

    for repeat in range(N_REPEATS):
        if time.perf_counter() - t0 >= SOFT_STOP_SEC:
            stopped = True
            break
        noise_fraction = NOISE_LEVELS[repeat // 10]
        add_dropout = repeat % 5 in (3, 4)
        try:
            pair = make_semisynthetic_pair(
                sigma, envelopes, rng, noise_fraction, add_dropout
            )
            estimated_d = fit_separable_full_wave(sigma, pair, t0)
            error = abs(estimated_d - TRUE_D_UM) / TRUE_D_UM * 100.0
            if not np.isfinite(error):
                raise FloatingPointError('厚度误差非有限')
            errors.append(float(error))
        except TimeBudgetReached:
            stopped = True
            break
        except (ValueError, RuntimeError, FloatingPointError, np.linalg.LinAlgError):
            failures += 1
            errors.append(100.0)

        elapsed = time.perf_counter() - t0
        atomic_write_json(
            output_path, result_payload(elapsed, errors, failures, stopped)
        )

    elapsed = time.perf_counter() - t0
    if elapsed >= SOFT_STOP_SEC and len(errors) < N_REPEATS:
        stopped = True
    atomic_write_json(output_path, result_payload(elapsed, errors, failures, stopped))
    metric = np.median(errors) if errors else float('nan')
    print(
        f'{ROUTE_NAME}：完成{len(errors)}/{N_REPEATS}次，'
        f'MdAPE={metric:.6f}%，失败{failures}次，用时{elapsed:.3f}秒。'
    )


if __name__ == '__main__':
    main()
