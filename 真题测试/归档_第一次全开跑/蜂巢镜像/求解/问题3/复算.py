#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""问题3红队独立复算：只依赖原始附件与题面/数据口径。"""

import sys
sys.path.insert(0, '/tmp/蜂巢/pylibs')

import json
import math
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar


ROOT = Path('/tmp/蜂巢')
DATA_DIR = ROOT / '数据'
OUT_DIR = ROOT / '求解' / '问题3' / '红队结果'
OUT_FILE = OUT_DIR / '复算结果.json'
START_TIME = time.monotonic()
SOFT_LIMIT_SECONDS = 540.0

# 硅在红外透明区的 Sellmeier 形式；lambda 单位为微米。
# n^2 = A + B/(lambda^2-C) + D/(lambda^2-E)
SI_SELLMEIER = {
    'A': 11.6858,
    'B': 0.939816,
    'C': 0.00810461,
    'D': 0.0030434748,
    'E': 1.54133408 ** 2,
}

# 采用多个重叠窗口，防止某一吸收带或缓慢基线主导结论。
CANDIDATE_BANDS = [
    (900.0, 1800.0),
    (1200.0, 2400.0),
    (1500.0, 3000.0),
    (1800.0, 4000.2),
    (1000.0, 4000.2),
]


def elapsed():
    return time.monotonic() - START_TIME


def time_left():
    return SOFT_LIMIT_SECONDS - elapsed()


def finite_or_none(value):
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def write_json(payload):
    """原子写入，确保接近时间上限时仍有可用结果。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    temp = OUT_FILE.with_suffix('.json.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
    os.replace(temp, OUT_FILE)


def initial_payload():
    return {
        '问题': 3,
        '复算方式': '独立实现，未读建模师代码；硅色散相位扫描与双角度联合拟合',
        '运行状态': '计算中',
        '复算指标': {
            '附件3硅外延层厚度_微米': None,
            '附件4硅外延层厚度_微米': None,
            '双角度联合硅外延层厚度_微米': None,
            '附件3多光束干涉判定': None,
            '附件4多光束干涉判定': None,
            '双角度厚度相对差': None,
        },
        '口径说明': {
            '附件3硅外延层厚度_微米': (
                '附件3（硅、外部入射角10°）；剔除共同首个零值占位疑点；'
                '在900–4000 cm^-1内用多个重叠窗口独立拟合；采用硅红外色散；'
                '各合格窗口厚度按拟合质量加权聚合，单位μm。'
            ),
            '附件4硅外延层厚度_微米': (
                '附件4（硅、外部入射角15°）；剔除共同首个零值占位疑点；'
                '在900–4000 cm^-1内用多个重叠窗口独立拟合；采用硅红外色散；'
                '各合格窗口厚度按拟合质量加权聚合，单位μm。'
            ),
            '双角度联合硅外延层厚度_微米': (
                '附件3与附件4为同一硅晶圆片；固定同一物理厚度，同时按10°和15°折射角相位拟合；'
                '两个角度等权，单位μm。'
            ),
            '附件3多光束干涉判定': (
                '在厚度相位坐标上检验2–4次谐波；至少两个合格窗口同时满足'
                '高次谐波能量/基频能量≥0.03、加入高次谐波的R²增量≥0.015，且二次谐波显著性≥3，判为是。'
            ),
            '附件4多光束干涉判定': (
                '与附件3相同判据，单独对附件4执行；不以反射率峰高本身替代多光束证据。'
            ),
            '双角度厚度相对差': (
                '|附件3厚度-附件4厚度|/两者均值；厚度均按各自多窗口质量加权值，单位为无量纲小数。'
            ),
        },
        '折射率模型': {
            '材料': '晶体硅',
            '表达式': 'n²=A+B/(λ²-C)+D/(λ²-E)，λ单位μm',
            '参数': SI_SELLMEIER,
            '相位': 'phi=4π*d_cm*波数_cm-1*sqrt(n(λ)^2-sin(外部入射角)^2)',
        },
        '各附件诊断': {},
        '异常与失败': [],
        '运行秒数': 0.0,
    }


def read_spectrum(filename):
    path = DATA_DIR / filename
    df = pd.read_excel(path, sheet_name='Sheet1', header=0, engine='openpyxl')
    if df.shape[1] < 2:
        raise ValueError(f'{filename}不足两列')
    x = pd.to_numeric(df.iloc[:, 0], errors='coerce').to_numpy(dtype=float)
    y = pd.to_numeric(df.iloc[:, 1], errors='coerce').to_numpy(dtype=float)
    good = np.isfinite(x) & np.isfinite(y)
    x, y = x[good], y[good]
    order = np.argsort(x)
    x, y = x[order], y[order]
    if x.size < 1000 or np.any(np.diff(x) <= 0):
        raise ValueError(f'{filename}有效数据不足或波数非严格递增')

    removed_first = False
    if x.size >= 102 and y[0] == 0:
        local_jump = np.median(np.abs(np.diff(y[1:102])))
        if local_jump > 0 and abs(y[1] - y[0]) > 20.0 * local_jump:
            x, y = x[1:], y[1:]
            removed_first = True
    return x, y, removed_first


def silicon_n(wavenumber_cm):
    wn = np.asarray(wavenumber_cm, dtype=float)
    lam_um = 1.0e4 / wn
    p = SI_SELLMEIER
    n2 = p['A'] + p['B'] / (lam_um ** 2 - p['C']) + p['D'] / (lam_um ** 2 - p['E'])
    if np.any(~np.isfinite(n2)) or np.any(n2 <= 1.0):
        raise ValueError('硅折射率模型在当前波段失效')
    return np.sqrt(n2)


def phase_coordinate(x, angle_deg):
    """返回 q=sigma*sqrt(n^2-sin^2(theta0))，phi=4*pi*d_cm*q。"""
    n = silicon_n(x)
    s2 = math.sin(math.radians(angle_deg)) ** 2
    return x * np.sqrt(n ** 2 - s2)


def normalize_x(x):
    center = 0.5 * (x[0] + x[-1])
    half = max(0.5 * (x[-1] - x[0]), 1e-12)
    return (x - center) / half


def baseline_matrix(x, degree=4):
    z = normalize_x(x)
    return np.polynomial.chebyshev.chebvander(z, degree)


def linear_fit_score(y, columns):
    design = np.column_stack(columns)
    coef, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
    fitted = design @ coef
    resid = y - fitted
    sse = float(resid @ resid)
    centered = y - np.mean(y)
    sst = float(centered @ centered)
    r2 = 1.0 - sse / max(sst, 1e-30)
    return r2, sse, coef, resid


def score_thickness(d_um, x, y, q, degree=4):
    phi = 4.0 * math.pi * (d_um * 1.0e-4) * q
    base = baseline_matrix(x, degree)
    r2, _, _, _ = linear_fit_score(y, [base, np.cos(phi), np.sin(phi)])
    return r2


def fft_initial_thickness(x, y, q):
    """仅作局部搜索初值；最终厚度来自非均匀色散相位拟合。"""
    base = baseline_matrix(x, degree=5)
    _, _, _, residual = linear_fit_score(y, [base])
    grid = np.linspace(x[0], x[-1], x.size)
    uniform = np.interp(grid, x, residual)
    window = np.hanning(uniform.size)
    spectrum = np.abs(np.fft.rfft((uniform - uniform.mean()) * window)) ** 2
    freq = np.fft.rfftfreq(uniform.size, d=float(grid[1] - grid[0]))
    n_eff = float(np.mean(q / x))
    d_axis = freq * 1.0e4 / (2.0 * n_eff)
    mask = (d_axis >= 0.5) & (d_axis <= 200.0) & (freq > 0)
    if not np.any(mask):
        raise ValueError('FFT厚度搜索区间为空')
    indexes = np.flatnonzero(mask)
    idx = indexes[int(np.argmax(spectrum[mask]))]
    return float(d_axis[idx])


def refine_thickness(x, y, angle_deg):
    q = phase_coordinate(x, angle_deg)
    initial = fft_initial_thickness(x, y, q)
    lo = max(0.3, initial * 0.55)
    hi = min(250.0, initial * 1.45)

    # 较密粗扫描避免局部周期极值误锁；随后在最佳格点附近有界细化。
    grid = np.linspace(lo, hi, 241)
    scores = np.array([score_thickness(d, x, y, q) for d in grid])
    best_i = int(np.argmax(scores))
    left = grid[max(0, best_i - 2)]
    right = grid[min(grid.size - 1, best_i + 2)]
    if right <= left:
        d_best = float(grid[best_i])
    else:
        opt = minimize_scalar(
            lambda d: -score_thickness(float(d), x, y, q),
            bounds=(float(left), float(right)),
            method='bounded',
            options={'xatol': 1e-5, 'maxiter': 100},
        )
        d_best = float(opt.x) if opt.success else float(grid[best_i])
    r2 = score_thickness(d_best, x, y, q)
    return d_best, float(r2), initial


def harmonic_diagnostics(x, y, angle_deg, d_um):
    q = phase_coordinate(x, angle_deg)
    phi = 4.0 * math.pi * (d_um * 1.0e-4) * q
    base = baseline_matrix(x, degree=4)

    fundamental_cols = [base, np.cos(phi), np.sin(phi)]
    r2_1, sse_1, coef_1, _ = linear_fit_score(y, fundamental_cols)

    harmonic_cols = [base]
    for k in range(1, 5):
        harmonic_cols.extend([np.cos(k * phi), np.sin(k * phi)])
    r2_4, sse_4, coef_4, resid_4 = linear_fit_score(y, harmonic_cols)

    base_cols = base.shape[1]
    amp2 = []
    for k in range(4):
        a = float(coef_4[base_cols + 2 * k])
        b = float(coef_4[base_cols + 2 * k + 1])
        amp2.append(a * a + b * b)
    fundamental_energy = max(amp2[0], 1e-30)
    harmonic_ratio = float(sum(amp2[1:]) / fundamental_energy)

    # 用残差标准差与正余弦列的有效样本尺度估计二次谐波显著性。
    dof = max(len(y) - len(coef_4), 1)
    noise_sigma = math.sqrt(max(sse_4 / dof, 0.0))
    amp_second = math.sqrt(amp2[1])
    amp_se = noise_sigma * math.sqrt(2.0 / max(len(y), 1))
    second_significance = float(amp_second / max(amp_se, 1e-30))

    return {
        '基频模型R2': float(r2_1),
        '四谐波模型R2': float(r2_4),
        '加入高次谐波R2增量': float(r2_4 - r2_1),
        '高次谐波能量比': harmonic_ratio,
        '二次谐波显著性': second_significance,
        '四谐波残差标准差_百分点': float(np.std(resid_4, ddof=1)),
        '满足多光束窗口判据': bool(
            harmonic_ratio >= 0.03
            and (r2_4 - r2_1) >= 0.015
            and second_significance >= 3.0
        ),
    }


def analyze_attachment(filename, angle_deg):
    x_all, y_all, removed_first = read_spectrum(filename)
    windows = []
    for lower, upper in CANDIDATE_BANDS:
        if time_left() < 110.0 and windows:
            break
        mask = (x_all >= lower) & (x_all <= upper)
        x, y = x_all[mask], y_all[mask]
        if x.size < 800:
            continue
        try:
            d_um, r2, fft_um = refine_thickness(x, y, angle_deg)
            harmonic = harmonic_diagnostics(x, y, angle_deg, d_um)
            windows.append({
                '波段_cm-1': [float(lower), float(upper)],
                '样本数': int(x.size),
                '厚度_微米': float(d_um),
                '基频拟合R2': float(r2),
                'FFT初值_微米': float(fft_um),
                **harmonic,
            })
        except Exception as exc:
            windows.append({
                '波段_cm-1': [float(lower), float(upper)],
                '样本数': int(x.size),
                '失败': str(exc),
            })

    valid = [w for w in windows if '厚度_微米' in w and w['基频拟合R2'] > 0.01]
    if not valid:
        raise RuntimeError(f'{filename}无合格频段厚度结果')

    ds = np.array([w['厚度_微米'] for w in valid], dtype=float)
    median_d = float(np.median(ds))
    # 先用中位数识别同一厚度模态，避免吸收带伪周期参与聚合。
    tolerance = max(0.08 * median_d, 0.35)
    cluster = [w for w in valid if abs(w['厚度_微米'] - median_d) <= tolerance]
    if len(cluster) < 2:
        # 若中位数邻域不足，选择R²最高窗口，并保留“稳定性不足”标记。
        cluster = [max(valid, key=lambda w: w['基频拟合R2'])]

    weights = np.array([
        max(w['基频拟合R2'], 1e-4) * math.sqrt(w['样本数']) for w in cluster
    ], dtype=float)
    cluster_d = np.array([w['厚度_微米'] for w in cluster], dtype=float)
    aggregate = float(np.average(cluster_d, weights=weights))
    spread = float(np.sqrt(np.average((cluster_d - aggregate) ** 2, weights=weights)))

    positive_harmonic_windows = sum(
        bool(w.get('满足多光束窗口判据', False)) for w in cluster
    )
    multibeam = positive_harmonic_windows >= 2

    return {
        '附件': filename,
        '外部入射角_度': float(angle_deg),
        '原始有效样本数': int(x_all.size + int(removed_first)),
        '剔除首点': bool(removed_first),
        '聚合厚度_微米': aggregate,
        '窗口间加权标准差_微米': spread,
        '参与聚合窗口数': len(cluster),
        '多光束阳性窗口数': int(positive_harmonic_windows),
        '多光束干涉判定': bool(multibeam),
        '各窗口': windows,
    }, (x_all, y_all)


def joint_score(d_um, datasets):
    scores = []
    for x, y, angle_deg in datasets:
        q = phase_coordinate(x, angle_deg)
        scores.append(score_thickness(d_um, x, y, q))
    return float(np.mean(scores))


def joint_refinement(attachment_data, d3, d4):
    datasets = []
    # 联合拟合固定在共同且较透明的宽波段，避免窗口选择差异造成角度偏置。
    for x_all, y_all, angle in attachment_data:
        mask = (x_all >= 1200.0) & (x_all <= 4000.2)
        datasets.append((x_all[mask], y_all[mask], angle))
    center = 0.5 * (d3 + d4)
    lo = max(0.3, min(d3, d4, center) * 0.85)
    hi = min(250.0, max(d3, d4, center) * 1.15)
    grid = np.linspace(lo, hi, 161)
    scores = np.array([joint_score(float(d), datasets) for d in grid])
    best = int(np.argmax(scores))
    left = float(grid[max(0, best - 2)])
    right = float(grid[min(grid.size - 1, best + 2)])
    if right <= left:
        return float(grid[best]), float(scores[best])
    opt = minimize_scalar(
        lambda d: -joint_score(float(d), datasets),
        bounds=(left, right), method='bounded',
        options={'xatol': 1e-5, 'maxiter': 100},
    )
    d_joint = float(opt.x) if opt.success else float(grid[best])
    return d_joint, joint_score(d_joint, datasets)


def main():
    payload = initial_payload()
    write_json(payload)
    attachment_raw = []

    specs = [
        ('附件3.xlsx', 10.0, '附件3硅外延层厚度_微米', '附件3多光束干涉判定'),
        ('附件4.xlsx', 15.0, '附件4硅外延层厚度_微米', '附件4多光束干涉判定'),
    ]

    for filename, angle, thickness_key, multibeam_key in specs:
        try:
            diagnostic, raw = analyze_attachment(filename, angle)
            payload['各附件诊断'][filename] = diagnostic
            payload['复算指标'][thickness_key] = finite_or_none(diagnostic['聚合厚度_微米'])
            payload['复算指标'][multibeam_key] = diagnostic['多光束干涉判定']
            attachment_raw.append((raw[0], raw[1], angle))
        except Exception as exc:
            payload['异常与失败'].append({'附件': filename, '原因': str(exc)})
        payload['运行秒数'] = round(elapsed(), 3)
        write_json(payload)

    d3 = payload['复算指标']['附件3硅外延层厚度_微米']
    d4 = payload['复算指标']['附件4硅外延层厚度_微米']
    if d3 is not None and d4 is not None:
        payload['复算指标']['双角度厚度相对差'] = finite_or_none(
            abs(d3 - d4) / max(0.5 * (abs(d3) + abs(d4)), 1e-9)
        )
        if len(attachment_raw) == 2 and time_left() >= 35.0:
            try:
                d_joint, joint_r2 = joint_refinement(attachment_raw, d3, d4)
                payload['复算指标']['双角度联合硅外延层厚度_微米'] = finite_or_none(d_joint)
                payload['双角度联合诊断'] = {'共同厚度模型平均R2': finite_or_none(joint_r2)}
            except Exception as exc:
                payload['异常与失败'].append({'步骤': '双角度联合拟合', '原因': str(exc)})
    else:
        payload['异常与失败'].append({
            '步骤': '双角度联合拟合',
            '原因': '至少一个单附件厚度复算失败，联合值留空',
        })

    # 仅记录声明是否可供第二阶段比对，不读取或回填任何声明数值。
    claim_path = ROOT / '交接' / '结果声明_问题3.json'
    payload['声明文件状态'] = '存在，待第二阶段逐键比对' if claim_path.exists() else '执行时仍不存在'
    payload['运行状态'] = '完成' if not payload['异常与失败'] else '部分完成'
    payload['运行秒数'] = round(elapsed(), 3)
    write_json(payload)

    print('问题3红队独立复算完成')
    print(json.dumps(payload['复算指标'], ensure_ascii=False, indent=2))
    print(f"运行状态：{payload['运行状态']}；耗时 {payload['运行秒数']:.3f} 秒")


if __name__ == '__main__':
    main()
