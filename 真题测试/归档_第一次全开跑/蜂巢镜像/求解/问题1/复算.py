#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
问题1红队独立复算。

本脚本只使用题面契约、数据档案、原始附件和结果声明（如存在）。
不读取建模师代码、建模笔记或建模师结果目录。
"""

import sys
sys.path.insert(0, '/tmp/蜂巢/pylibs')

import json
import math
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import find_peaks, savgol_filter


ROOT = Path('/tmp/蜂巢')
DATA_DIR = ROOT / '数据'
OUT_DIR = ROOT / '求解' / '问题1' / '红队结果'
DECLARATION = ROOT / '交接' / '结果声明_问题1.json'
START_TIME = time.monotonic()
TIME_BUDGET_SECONDS = 18 * 60


def check_budget(stage):
    """保留两分钟用于落盘；本数据规模正常应在数秒内完成。"""
    elapsed = time.monotonic() - START_TIME
    if elapsed > TIME_BUDGET_SECONDS:
        raise TimeoutError(f'复算在阶段“{stage}”触发主动收敛，已用{elapsed:.1f}秒')


def atomic_json_dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
    os.replace(tmp, path)


def flatten_key_paths(obj, prefix=''):
    """只提取声明的键路径，绝不把对方数值带入复算。"""
    paths = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_prefix = f'{prefix}.{key}' if prefix else str(key)
            paths.append(new_prefix)
            paths.extend(flatten_key_paths(value, new_prefix))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            paths.extend(flatten_key_paths(value, f'{prefix}[{i}]'))
    return paths


def read_spectrum(filename):
    path = DATA_DIR / filename
    df = pd.read_excel(path, sheet_name='Sheet1', header=0, engine='openpyxl')
    if df.shape[1] != 2:
        raise ValueError(f'{filename}应为2列，实际为{df.shape[1]}列')
    x = pd.to_numeric(df.iloc[:, 0], errors='coerce').to_numpy(dtype=float)
    y = pd.to_numeric(df.iloc[:, 1], errors='coerce').to_numpy(dtype=float)
    good = np.isfinite(x) & np.isfinite(y)
    x, y = x[good], y[good]
    order = np.argsort(x)
    x, y = x[order], y[order]
    if len(x) < 100 or np.any(np.diff(x) <= 0):
        raise ValueError(f'{filename}的波数序列不满足复算要求')

    # 数据档案将全部附件的首个零值标记为边界/占位疑点。
    removed_first = False
    if len(y) >= 102 and y[0] == 0:
        local_step = np.median(np.abs(np.diff(y[1:102])))
        jump_ratio = abs(y[1] - y[0]) / max(local_step, 1e-12)
        if jump_ratio > 20:
            x, y = x[1:], y[1:]
            removed_first = True
    return x, y, removed_first


def odd_window(n, target):
    w = int(target)
    if w % 2 == 0:
        w += 1
    w = min(w, n - 1 if n % 2 == 0 else n)
    if w % 2 == 0:
        w -= 1
    return max(w, 7)


def parabolic_frequency(freq, power, idx):
    """对FFT功率谱峰做三点抛物线插值，减少网格量化误差。"""
    if idx <= 0 or idx >= len(power) - 1:
        return float(freq[idx])
    a, b, c = np.log(power[idx - 1:idx + 2] + np.finfo(float).tiny)
    denom = a - 2.0 * b + c
    shift = 0.0 if abs(denom) < 1e-15 else 0.5 * (a - c) / denom
    shift = float(np.clip(shift, -0.5, 0.5))
    return float(freq[idx] + shift * (freq[1] - freq[0]))


def estimate_window(x, y, lower, upper):
    mask = (x >= lower) & (x <= upper)
    xb, yb = x[mask], y[mask]
    if len(xb) < 500:
        raise ValueError(f'窗口{lower}-{upper} cm^-1数据不足')

    dx = float(np.median(np.diff(xb)))
    xu = np.arange(xb[0], xb[-1] + 0.25 * dx, dx)
    yu = np.interp(xu, xb, yb)

    # 使用大窗口Savitzky-Golay只移除缓慢光谱包络，不利用对方的折射率或厚度设定。
    baseline_window = odd_window(len(yu), max(301, round(len(yu) * 0.19)))
    baseline = savgol_filter(yu, baseline_window, polyorder=3, mode='interp')
    residual = yu - baseline
    residual -= np.mean(residual)
    tapered = residual * np.hanning(len(residual))

    nfft = 1 << int(math.ceil(math.log2(max(2048, len(tapered) * 8))))
    spectrum = np.fft.rfft(tapered, n=nfft)
    power = np.abs(spectrum) ** 2
    freq = np.fft.rfftfreq(nfft, d=dx)  # cycles per (cm^-1)
    span = float(xu[-1] - xu[0])
    fmin = max(3.0 / span, 0.0008)      # 至少在窗口中出现3个周期
    fmax = min(0.25, 0.45 / dx)         # 对应光学厚度上限1250 μm
    valid = (freq >= fmin) & (freq <= fmax)
    ids = np.flatnonzero(valid)
    if len(ids) < 5:
        raise ValueError('有效频率网格不足')

    local_peaks, _ = find_peaks(power[ids])
    candidate_ids = ids[local_peaks]
    if len(candidate_ids) == 0:
        candidate_ids = np.array([ids[int(np.argmax(power[ids]))]])
    candidate_ids = candidate_ids[np.argsort(power[candidate_ids])[-8:]][::-1]

    floor = float(np.median(power[ids])) + np.finfo(float).tiny
    candidates = []
    for idx in candidate_ids:
        f = parabolic_frequency(freq, power, int(idx))
        candidates.append({
            '法向光学厚度_微米': f * 5000.0,
            '条纹频率_周每cm-1': f,
            '谱峰功率与中位噪声比': float(power[idx] / floor),
        })

    primary = candidates[0]
    f0 = primary['条纹频率_周每cm-1']
    expected_period = 1.0 / f0
    min_distance = max(2, int(0.55 * expected_period / dx))
    prominence = max(0.12 * float(np.std(residual)), np.finfo(float).eps)
    maxima, _ = find_peaks(residual, distance=min_distance, prominence=prominence)
    minima, _ = find_peaks(-residual, distance=min_distance, prominence=prominence)

    spacing = []
    if len(maxima) >= 3:
        spacing.extend(np.diff(xu[maxima]).tolist())
    if len(minima) >= 3:
        spacing.extend(np.diff(xu[minima]).tolist())
    spacing = np.asarray(spacing, dtype=float)
    # 只保留与FFT主周期相容的峰谷间距，避免局部噪声伪峰。
    spacing = spacing[(spacing > 0.55 * expected_period) & (spacing < 1.55 * expected_period)]
    extrema_q = None
    if len(spacing) >= 4:
        extrema_q = 5000.0 / float(np.median(spacing))

    return {
        '窗口_cm-1': [float(lower), float(upper)],
        '样本数': int(len(xb)),
        '首选法向光学厚度_微米': float(primary['法向光学厚度_微米']),
        '极值间距复核_微米': None if extrema_q is None else float(extrema_q),
        '候选谱峰': candidates,
    }


def cluster_window_candidates(window_results):
    """
    在多个波数窗口中聚类同一频率峰。主结果要求至少两个窗口支持；
    评分为各窗口对数信噪比之和，不使用任何建模师数值。
    """
    clusters = []
    for wi, wr in enumerate(window_results):
        for cand in wr['候选谱峰']:
            q = float(cand['法向光学厚度_微米'])
            snr = float(cand['谱峰功率与中位噪声比'])
            matches = [c for c in clusters if abs(q - c['center']) / max(c['center'], 1e-12) <= 0.025]
            if matches:
                cluster = min(matches, key=lambda c: abs(q - c['center']))
            else:
                cluster = {'values': [], 'snrs': [], 'windows': set(), 'center': q}
                clusters.append(cluster)
            cluster['values'].append(q)
            cluster['snrs'].append(snr)
            cluster['windows'].add(wi)
            weights = np.log1p(np.asarray(cluster['snrs']))
            cluster['center'] = float(np.average(cluster['values'], weights=weights))

    summaries = []
    for c in clusters:
        support = len(c['windows'])
        score = float(sum(math.log1p(v) for v in c['snrs']))
        summaries.append({
            '中心法向光学厚度_微米': float(c['center']),
            '支持窗口数': int(support),
            '累积对数信噪比': score,
            '窗间相对离散': float(np.std(c['values'], ddof=1) / c['center']) if len(c['values']) > 1 else None,
        })
    summaries.sort(key=lambda c: (c['支持窗口数'], c['累积对数信噪比']), reverse=True)
    supported = [c for c in summaries if c['支持窗口数'] >= 2]
    chosen = supported[0] if supported else summaries[0]
    return chosen, summaries[:10]


def estimate_attachment(filename):
    x, y, removed_first = read_spectrum(filename)
    windows = [(1000.0, 4000.0), (1200.0, 4000.0), (1500.0, 4000.0), (1800.0, 4000.0)]
    results = []
    for lower, upper in windows:
        check_budget(f'{filename}:{lower}-{upper}')
        results.append(estimate_window(x, y, lower, upper))
    chosen, clusters = cluster_window_candidates(results)
    return {
        '主估计_法向光学厚度_微米': chosen['中心法向光学厚度_微米'],
        '主估计支持窗口数': chosen['支持窗口数'],
        '首点边界异常已剔除': removed_first,
        '原始大于100的反射率是否保留': True,
        '候选聚类': clusters,
        '分窗口结果': results,
    }


def synthetic_model_check():
    """对推导的 4π 相位系数和 q=f/2 换算做独立数值自检。"""
    x = np.linspace(1000.0, 4000.0, 12001)
    q_true_um = 31.25
    q_true_cm = q_true_um / 1.0e4
    y = 20.0 + 2.0 * np.cos(4.0 * np.pi * q_true_cm * x + 0.37)
    wr = estimate_window(x, y, 1000.0, 4000.0)
    q_est = wr['首选法向光学厚度_微米']
    return {
        '设定法向光学厚度_微米': q_true_um,
        '反演法向光学厚度_微米': q_est,
        '相对误差': abs(q_est - q_true_um) / q_true_um,
        '通过_1百分比阈值': abs(q_est - q_true_um) / q_true_um <= 0.01,
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    declared_key_paths = []
    declaration_status = '执行时未找到结果声明_问题1.json'
    if DECLARATION.exists():
        with DECLARATION.open('r', encoding='utf-8') as f:
            declaration = json.load(f)
        declared_key_paths = flatten_key_paths(declaration)
        declaration_status = '已只读取声明键路径，未使用声明数值参与复算'

    output = {
        '问题': 1,
        '复算方式': '独立实现，未读建模师代码；用两光束光程差推导+原始光谱FFT/极值间距复核',
        '声明文件状态': declaration_status,
        '声明键路径': declared_key_paths,
        '模型独立推导': {
            '可辨识量定义': 'q(ν~)=d*sqrt(n(ν~)^2-sin(θ0)^2)=n*d*cos(θt)',
            '一次往返光程差': 'Δ=2*n(ν~)*d*cos(θt)=2*d*sqrt(n(ν~)^2-sin(θ0)^2)',
            '相位差': 'δ(ν~)=4π*ν~*d*sqrt(n(ν~)^2-sin(θ0)^2)+φr',
            '通用相邻同类条纹厚度公式': 'd=1/{2*|ν~2*sqrt[n(ν~2)^2-sin^2(θ0)]-ν~1*sqrt[n(ν~1)^2-sin^2(θ0)]|}',
            '常折射率局部简式': 'd=1/[2*Δν~*sqrt(n^2-sin^2(θ0))]',
            '单位': '波数用cm^-1时d先得cm，乘10^4转为μm',
            '适用条件': '仅表面反射光与外延层-衬底界面一次反射返回光干涉；同类相邻极值的附加反射相位差不变',
        },
        '复算指标': {},
        '口径说明': {},
        '过程证据': {},
        '失败项': [],
    }

    try:
        self_check = synthetic_model_check()
        output['过程证据']['合成信号公式自检'] = self_check
    except Exception as exc:
        output['失败项'].append({'指标': '合成信号公式自检', '原因': repr(exc)})

    estimates = {}
    for filename, angle in [('附件1.xlsx', 10), ('附件2.xlsx', 15)]:
        check_budget(filename)
        try:
            estimates[filename] = estimate_attachment(filename)
            key = f'{filename[:-5]}_{angle}度法向光学厚度_微米'
            output['复算指标'][key] = estimates[filename]['主估计_法向光学厚度_微米']
            output['口径说明'][key] = (
                f'{filename}，入射角{angle}°；使用1000-4000、1200-4000、1500-4000、1800-4000 cm^-1四窗口；'
                '剔除首个零值边界疑点，不截断>100%反射率；各窗口去慢变包络后FFT取条纹频率，'
                '以q=f/2换算法向光学厚度q=d*sqrt(n^2-sin^2θ0)，多窗口2.5%容差聚类，单位μm；'
                '该量不引入未由题面给定的折射率常数。'
            )
        except Exception as exc:
            estimates[filename] = None
            output['失败项'].append({'指标': filename, '原因': repr(exc)})
            key = f'{filename[:-5]}_{angle}度法向光学厚度_微米'
            output['复算指标'][key] = None
            output['口径说明'][key] = '复算失败，原因见失败项；未抄取对方数值。'

    output['过程证据']['附件分窗口详情'] = estimates
    q1 = output['复算指标'].get('附件1_10度法向光学厚度_微米')
    q2 = output['复算指标'].get('附件2_15度法向光学厚度_微米')
    if q1 is not None and q2 is not None:
        rel = abs(q1 - q2) / max(abs(q1), 1e-9)
        output['复算指标']['双角度法向光学厚度相对差'] = rel
        output['口径说明']['双角度法向光学厚度相对差'] = (
            '|附件1的q-附件2的q|/max(|附件1的q|,1e-9)；两附件为同一晶圆不同入射角，'
            '此处比较的是可直接从条纹频率识别的q，不是在未知n(ν~)下强行换算的几何厚度d。'
        )

    output['耗时_秒'] = time.monotonic() - START_TIME
    atomic_json_dump(OUT_DIR / '问题1_独立复算.json', output)

    print('问题1红队复算完成')
    print('复算指标摘要:', json.dumps(output['复算指标'], ensure_ascii=False))
    print('结果文件:', OUT_DIR / '问题1_独立复算.json')


if __name__ == '__main__':
    main()
