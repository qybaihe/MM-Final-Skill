#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Q1-A 色散光程—整阶稳健回归的3分钟小样原型（只输出 MdAPE）。"""

import sys

sys.path.insert(0, '/tmp/蜂巢/pylibs')

import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.signal import find_peaks, periodogram, savgol_filter


ROOT = Path('/tmp/蜂巢')
DATA_DIR = ROOT / '数据'
OUT_PATH = ROOT / '求解' / '问题1' / '原型结果' / '路线1.json'
ROUTE_NAME = 'Q1-A 色散光程—整阶稳健回归'
SAMPLE_POINTS = 800
REPEATS = 30
SOFT_BUDGET_SECONDS = 165.0
EXPECTED_SECONDS = 40
N_REF = 2.60
TRUE_BETA = np.array([0.045, -0.018], dtype=float)
ANGLES = (10.0, 15.0)
RNG_SEED = 20260826


def write_result(payload):
    """每次都写入合法 JSON 并立即 flush，防止超时时结果目录为空。"""
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = OUT_PATH.with_suffix('.json.tmp')
    with tmp_path.open('w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    tmp_path.replace(OUT_PATH)


def result_payload(mdape, elapsed, completed, anchor_um, note):
    return {
        '路线名': ROUTE_NAME,
        '核心指标键值': {
            '指标名': '厚度绝对百分比误差中位数MdAPE',
            '值_百分比': None if mdape is None else round(float(mdape), 6),
            '方向': '越小越优'
        },
        '用时估计': {
            '预计秒': EXPECTED_SECONDS,
            '硬上限秒': 180,
            '主动收敛秒': int(SOFT_BUDGET_SECONDS)
        },
        '实际用时秒': round(float(elapsed), 3),
        '已完成重复数': int(completed),
        '计划重复数': REPEATS,
        '真数据小样点数_每角度': SAMPLE_POINTS,
        '真实数据驱动厚度锚点_微米': None if anchor_um is None else round(float(anchor_um), 6),
        '口径说明': note
    }


def odd_window(target, n, minimum=5):
    """得到不超过数据长度的奇数 Savitzky-Golay 窗口。"""
    w = min(int(target), n if n % 2 == 1 else n - 1)
    w = max(minimum, w)
    if w % 2 == 0:
        w -= 1
    return w


def load_real_small_sample():
    """读取附件1/2真数据，取800--2400 cm^-1并在公共索引上降采样到800点。"""
    curves = []
    common_sigma = None
    for filename in ('附件1.xlsx', '附件2.xlsx'):
        df = pd.read_excel(DATA_DIR / filename, sheet_name='Sheet1', header=0, engine='openpyxl')
        sigma = pd.to_numeric(df['波数 (cm-1)'], errors='raise').to_numpy(float)
        reflectance = pd.to_numeric(df['反射率 (%)'], errors='raise').to_numpy(float)
        mask = (sigma >= 800.0) & (sigma <= 2400.0)
        sigma = sigma[mask]
        reflectance = reflectance[mask]
        if len(sigma) < SAMPLE_POINTS:
            raise ValueError('真实频谱在800--2400 cm^-1内不足800点')
        take = np.linspace(0, len(sigma) - 1, SAMPLE_POINTS).round().astype(int)
        sigma = sigma[take]
        reflectance = reflectance[take]
        if common_sigma is None:
            common_sigma = sigma
        elif not np.allclose(common_sigma, sigma, rtol=0.0, atol=1e-10):
            raise ValueError('附件1/2的下采样波数键不一致')
        curves.append(reflectance)
    return common_sigma, curves


def decompose_real_profile(reflectance):
    """从真实曲线提取缓变基线、局部振幅与高频噪声尺度。"""
    n = len(reflectance)
    baseline = savgol_filter(reflectance, odd_window(151, n), 3, mode='interp')
    oscillation = reflectance - baseline
    envelope = savgol_filter(np.abs(oscillation), odd_window(101, n), 2, mode='interp')
    amp_floor = max(float(np.median(envelope)) * 0.35, 0.5)
    envelope = np.maximum(1.8 * envelope, amp_floor)
    short = savgol_filter(reflectance, odd_window(11, n), 2, mode='interp')
    hf = reflectance - short
    med = np.median(hf)
    noise = 1.4826 * np.median(np.abs(hf - med))
    noise = max(float(noise), 0.015 * float(np.median(envelope)))
    return baseline, envelope, noise


def optical_index(sigma, beta):
    """低维色散基：载流子代理影响吸收进 beta1,beta2。"""
    z = (sigma - 1600.0) / 800.0
    return N_REF + beta[0] * z + beta[1] * z * z


def spectral_pilot(sigma, curves):
    """仅用作真数据锚点/优化初值；最终路线指标不由频谱峰直接给出。"""
    d_grid = np.linspace(3.0, 40.0, 600)
    total = np.zeros_like(d_grid)
    step = float(np.median(np.diff(sigma)))
    for angle, reflectance in zip(ANGLES, curves):
        base = savgol_filter(reflectance, odd_window(151, len(sigma)), 3, mode='interp')
        freq, power = periodogram(reflectance - base, fs=1.0 / step, detrend=False)
        s = np.sin(np.deg2rad(angle))
        target_f = 2.0 * (d_grid * 1e-4) * np.sqrt(N_REF * N_REF - s * s)
        interp_power = np.interp(target_f, freq, power, left=0.0, right=0.0)
        positive = interp_power[interp_power > 0]
        scale = np.median(positive) if len(positive) else 1.0
        total += interp_power / max(float(scale), 1e-12)
    return float(d_grid[int(np.argmax(total))])


def synthesize_repeat(sigma, profiles, d_true_um, repeat_id, rng):
    """使用真实基线/振幅/噪声制作可知真值的双角合成重复。"""
    noise_level = (0.55, 1.00, 1.45)[repeat_id % 3]
    output = []
    d_cm = d_true_um * 1e-4
    n_sigma = optical_index(sigma, TRUE_BETA)
    for angle_id, (angle, profile) in enumerate(zip(ANGLES, profiles)):
        baseline, envelope, real_noise = profile
        attenuation = np.ones_like(sigma)
        if repeat_id % 2 == 1:
            count = 1 + int(repeat_id % 3 == 2)
            centers = rng.choice(sigma[100:-100], size=count, replace=False)
            for center in np.atleast_1d(centers):
                attenuation *= 1.0 - 0.88 * np.exp(-0.5 * ((sigma - center) / 28.0) ** 2)
        phase0 = 0.35 + 0.55 * angle_id
        sin2 = np.sin(np.deg2rad(angle)) ** 2
        phase = 4.0 * np.pi * d_cm * sigma * np.sqrt(n_sigma * n_sigma - sin2) + phase0
        noise = rng.normal(0.0, noise_level * real_noise, size=len(sigma))
        output.append(baseline + attenuation * envelope * np.cos(phase) + noise)
    return output


def quadratic_vertex(x, y, index):
    """在固定候选点的3点邻域内做局部二次定位。"""
    if index <= 0 or index >= len(x) - 1:
        return float(x[index])
    xx = x[index - 1:index + 2]
    yy = y[index - 1:index + 2]
    coef = np.polyfit(xx - x[index], yy, 2)
    if abs(coef[0]) < 1e-12:
        return float(x[index])
    delta = -coef[1] / (2.0 * coef[0])
    half_span = 0.5 * float(xx[-1] - xx[0])
    if abs(delta) > half_span:
        return float(x[index])
    return float(x[index] + delta)


def extract_extrema(sigma, reflectance, d_hint_um, angle):
    """固定平滑/显著性规则取候选峰谷，再做局部二次定位。"""
    n = len(sigma)
    base = savgol_filter(reflectance, odd_window(151, n), 3, mode='interp')
    residual = reflectance - base
    smooth = savgol_filter(residual, odd_window(9, n), 3, mode='interp')
    robust_scale = 1.4826 * np.median(np.abs(smooth - np.median(smooth)))
    prominence = max(0.22 * float(np.std(smooth)), 0.16 * float(robust_scale), 1e-6)
    half_period_cm = 1.0 / max(
        4.0 * d_hint_um * 1e-4 * np.sqrt(N_REF * N_REF - np.sin(np.deg2rad(angle)) ** 2),
        1e-8
    )
    distance = max(5, int(0.42 * half_period_cm / np.median(np.diff(sigma))))
    peaks, _ = find_peaks(smooth, prominence=prominence, distance=distance)
    valleys, _ = find_peaks(-smooth, prominence=prominence, distance=distance)
    candidates = [(int(i), 1) for i in peaks] + [(int(i), -1) for i in valleys]
    candidates.sort(key=lambda item: item[0])
    if not candidates:
        return np.array([]), np.array([], dtype=int)
    reduced = []
    for item in candidates:
        if reduced and item[1] == reduced[-1][1]:
            old = reduced[-1]
            if abs(smooth[item[0]]) > abs(smooth[old[0]]):
                reduced[-1] = item
        else:
            reduced.append(item)
    positions = np.array([quadratic_vertex(sigma, smooth, i) for i, _ in reduced], dtype=float)
    kinds = np.array([kind for _, kind in reduced], dtype=int)
    return positions, kinds


def estimate_offset(predicted):
    grid = np.linspace(-0.5, 0.5, 101)
    losses = [np.median(np.abs(predicted + g - np.rint(predicted + g))) for g in grid]
    return float(grid[int(np.argmin(losses))])


def dp_assign_orders(predicted, kinds, max_jump=5):
    """分配单调整数半周阶次，允许2--5阶跳跃表示漏峰。"""
    lo = int(np.floor(np.min(predicted))) - 3
    hi = int(np.ceil(np.max(predicted))) + 3
    states = np.arange(lo, hi + 1, dtype=int)
    k = len(predicted)
    cost = np.full((k, len(states)), np.inf)
    prev = np.full((k, len(states)), -1, dtype=int)
    cost[0] = np.minimum((states - predicted[0]) ** 2, 4.0)
    for i in range(1, k):
        parity_needed = 1 if kinds[i] != kinds[i - 1] else 0
        data_cost = np.minimum((states - predicted[i]) ** 2, 4.0)
        for j, q_now in enumerate(states):
            jumps = q_now - states
            valid = (jumps >= 1) & (jumps <= max_jump) & ((jumps % 2) == parity_needed)
            if not np.any(valid):
                continue
            transition = cost[i - 1] + 0.12 * (jumps - 1) ** 2
            transition[~valid] = np.inf
            p = int(np.argmin(transition))
            cost[i, j] = transition[p] + data_cost[j]
            prev[i, j] = p
    end = int(np.argmin(cost[-1]))
    if not np.isfinite(cost[-1, end]):
        raise RuntimeError('动态规划未找到可行单调整阶序列')
    orders = np.empty(k, dtype=int)
    orders[-1] = states[end]
    for i in range(k - 1, 0, -1):
        end = prev[i, end]
        orders[i - 1] = states[end]
    return orders


def joint_integer_huber_fit(sigma, curves):
    """双角共享 d,beta1,beta2，角度各自仅保留一个相位截距。"""
    d_init = spectral_pilot(sigma, curves)
    extrema = []
    for angle, curve in zip(ANGLES, curves):
        pos, kinds = extract_extrema(sigma, curve, d_init, angle)
        if len(pos) < 8:
            raise RuntimeError(f'{angle:.0f}°候选极值仅{len(pos)}个，不足以整阶回归')
        extrema.append((pos, kinds))

    offsets = []
    for angle, (pos, _) in zip(ANGLES, extrema):
        sin2 = np.sin(np.deg2rad(angle)) ** 2
        predicted = 4.0 * d_init * 1e-4 * pos * np.sqrt(N_REF * N_REF - sin2)
        offsets.append(estimate_offset(predicted))
    params = np.array([d_init, 0.0, 0.0, offsets[0], offsets[1]], dtype=float)

    for _ in range(4):
        assigned = []
        for angle_id, (angle, (pos, kinds)) in enumerate(zip(ANGLES, extrema)):
            n_pos = optical_index(pos, params[1:3])
            predicted = (
                4.0 * params[0] * 1e-4 * pos
                * np.sqrt(n_pos * n_pos - np.sin(np.deg2rad(angle)) ** 2)
                + params[3 + angle_id]
            )
            assigned.append(dp_assign_orders(predicted, kinds))

        def residual_vector(p):
            residuals = []
            for angle_id, (angle, (pos, _)) in enumerate(zip(ANGLES, extrema)):
                n_pos = optical_index(pos, p[1:3])
                model_order = (
                    4.0 * p[0] * 1e-4 * pos
                    * np.sqrt(n_pos * n_pos - np.sin(np.deg2rad(angle)) ** 2)
                    + p[3 + angle_id]
                )
                residuals.extend(model_order - assigned[angle_id])
            residuals.extend([0.25 * p[1] / 0.08, 0.25 * p[2] / 0.08])
            return np.asarray(residuals, dtype=float)

        fit = least_squares(
            residual_vector,
            params,
            bounds=([3.0, -0.12, -0.12, -5.0, -5.0],
                    [40.0, 0.12, 0.12, 5.0, 5.0]),
            loss='huber',
            f_scale=0.30,
            max_nfev=120,
            xtol=1e-7,
            ftol=1e-7,
            gtol=1e-7
        )
        params = fit.x
    return float(params[0])


def main():
    t0 = time.monotonic()
    base_note = (
        '用附件1/2在800--2400 cm^-1的真实公共波数网格，各降采样至800点；'
        '真实曲线提供基线、振幅与噪声尺度。为保留路线侦察的已知真值公平口径，'
        '以真数据双角频谱共识作厚度锚点，生成3档噪声与少量局部漏峰的30次数据驱动重复。'
        '每次仅由固定极值规则、局部二次定位、允许跳阶的动态规划与双角共享厚度的Huber整阶回归估计d；'
        '唯一比较指标为30次厚度绝对百分比误差的中位数。'
    )
    write_result(result_payload(None, 0.0, 0, None, base_note + '运行初始化。'))
    sigma, real_curves = load_real_small_sample()
    profiles = [decompose_real_profile(curve) for curve in real_curves]
    d_anchor_um = spectral_pilot(sigma, real_curves)
    rng = np.random.default_rng(RNG_SEED)
    errors = []

    for repeat_id in range(REPEATS):
        if time.monotonic() - t0 >= SOFT_BUDGET_SECONDS:
            break
        synthetic_curves = synthesize_repeat(sigma, profiles, d_anchor_um, repeat_id, rng)
        try:
            d_est_um = joint_integer_huber_fit(sigma, synthetic_curves)
            error_pct = abs(d_est_um - d_anchor_um) / d_anchor_um * 100.0
        except (RuntimeError, ValueError, FloatingPointError):
            error_pct = 100.0
        errors.append(float(error_pct))
        elapsed = time.monotonic() - t0
        write_result(result_payload(
            float(np.median(errors)), elapsed, len(errors), d_anchor_um,
            base_note + f'已逐步落盘{len(errors)}次重复。'
        ))

    elapsed = time.monotonic() - t0
    if not errors:
        raise RuntimeError('时间预算内未完成任何重复')
    final_note = base_note
    if len(errors) < REPEATS:
        final_note += f'在165秒软预算处主动收敛，按已完成的{len(errors)}次重复计算MdAPE。'
    else:
        final_note += '已完成全30次重复。'
    mdape = float(np.median(errors))
    write_result(result_payload(mdape, elapsed, len(errors), d_anchor_um, final_note))
    print(
        f'{ROUTE_NAME}：MdAPE={mdape:.4f}%，完成{len(errors)}/{REPEATS}次，'
        f'用时{elapsed:.2f}秒，结果={OUT_PATH}'
    )


if __name__ == '__main__':
    main()
