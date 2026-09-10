import sys
sys.path.insert(0, '/tmp/蜂巢/pylibs')

import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.signal import find_peaks, savgol_filter


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "数据"
OUT_DIR = ROOT / "求解" / "问题2" / "原型结果"
OUT_PATH = OUT_DIR / "路线3.json"

ROUTE_NAME = "Q2-C 原网格色散匹配相干扫描"
SOFT_BUDGET_SECONDS = 160.0
HARD_BUDGET_SECONDS = 175.0
MAX_POINTS = 1800
SIGMA_MIN = 1200.0
SIGMA_MAX = 2400.0
VALID_MIN = 1680.0
VALID_MAX = 1920.0
NOMINAL_RUNTIME_SECONDS = 140


class TimeBudgetReached(RuntimeError):
    pass


def to_builtin(value):
    if isinstance(value, dict):
        return {str(k): to_builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_builtin(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    return value


def flush_json(payload):
    """原子替换落盘，保证中途被中断时仍保留最近一个合法 JSON。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = OUT_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(to_builtin(payload), f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, OUT_PATH)


def elapsed(t0):
    return time.monotonic() - t0


def check_budget(t0, limit=SOFT_BUDGET_SECONDS):
    if elapsed(t0) >= limit:
        raise TimeBudgetReached(f"已用时 {elapsed(t0):.1f} 秒，触发原型时间预算。")


def read_attachment(path):
    df = pd.read_excel(path, sheet_name="Sheet1", header=0, engine="openpyxl")
    expected = ["波数 (cm-1)", "反射率 (%)"]
    if list(df.columns) != expected:
        raise ValueError(f"{path.name} 列名与数据档案不一致: {list(df.columns)}")
    sigma = df[expected[0]].to_numpy(dtype=float)
    reflectance = df[expected[1]].to_numpy(dtype=float)
    keep = (sigma >= SIGMA_MIN) & (sigma <= SIGMA_MAX)
    return sigma[keep], reflectance[keep]


def load_small_sample():
    sigma1, y1 = read_attachment(DATA_DIR / "附件1.xlsx")
    sigma2, y2 = read_attachment(DATA_DIR / "附件2.xlsx")
    if len(sigma1) != len(sigma2) or not np.array_equal(sigma1, sigma2):
        raise ValueError("附件1/2在1200–2400 cm-1内未保持公共波数键。")

    if len(sigma1) > MAX_POINTS:
        index = np.unique(np.rint(np.linspace(0, len(sigma1) - 1, MAX_POINTS)).astype(int))
        sigma1, y1, y2 = sigma1[index], y1[index], y2[index]

    fit_mask = (sigma1 < VALID_MIN) | (sigma1 > VALID_MAX)
    valid_mask = ~fit_mask
    if fit_mask.sum() < 100 or valid_mask.sum() < 30:
        raise ValueError("连续块切分后样本数不足。")
    return sigma1, {10.0: y1, 15.0: y2}, fit_mask, valid_mask


def block_polynomial_q(sigma_fit):
    """对左、右拟合块分别建二次基线，返回其正交投影基。"""
    left = sigma_fit < VALID_MIN
    right = sigma_fit > VALID_MAX
    columns = []
    for mask in (left, right):
        x = np.zeros_like(sigma_fit, dtype=float)
        local = sigma_fit[mask]
        scale = max(np.ptp(local) / 2.0, 1.0)
        x[mask] = (local - np.mean(local)) / scale
        columns.extend([
            mask.astype(float),
            mask.astype(float) * x,
            mask.astype(float) * x * x,
        ])
    baseline = np.column_stack(columns)
    q, _ = np.linalg.qr(baseline, mode="reduced")
    return q


def refractive_index(sigma, n0, slope):
    """低维色散基：n(σ)=n0+slope*(σ-1800)/600。"""
    return n0 + slope * ((sigma - 1800.0) / 600.0)


def physical_phase(sigma, angle_deg, thickness_um, n0, slope):
    n_sigma = refractive_index(sigma, n0, slope)
    sin2 = np.sin(np.deg2rad(angle_deg)) ** 2
    radicand = n_sigma * n_sigma - sin2
    if np.any(n_sigma <= 1.0) or np.any(radicand <= 0.0):
        return None
    thickness_cm = thickness_um * 1.0e-4
    return 4.0 * np.pi * thickness_cm * sigma * np.sqrt(radicand)


def prepare_scan_state(sigma, y_by_angle, fit_mask):
    sigma_fit = sigma[fit_mask]
    q = block_polynomial_q(sigma_fit)
    y_residual = {}
    baseline_rss = {}
    for angle, y in y_by_angle.items():
        yf = y[fit_mask]
        yr = yf - q @ (q.T @ yf)
        y_residual[angle] = yr
        baseline_rss[angle] = float(yr @ yr)
    return {
        "sigma_fit": sigma_fit,
        "q": q,
        "y_residual": y_residual,
        "baseline_rss": baseline_rss,
        "total_baseline_rss": float(sum(baseline_rss.values())),
    }


def coherence_score(params, state, return_coefficients=False):
    thickness_um, n0, slope = [float(v) for v in params]
    q = state["q"]
    total_gain = 0.0
    coefficients = {}
    for angle in (10.0, 15.0):
        phase = physical_phase(state["sigma_fit"], angle, thickness_um, n0, slope)
        if phase is None:
            return (-np.inf, {}) if return_coefficients else -np.inf
        harmonic = np.column_stack((np.cos(phase), np.sin(phase)))
        harmonic -= q @ (q.T @ harmonic)
        gram = harmonic.T @ harmonic
        rhs = harmonic.T @ state["y_residual"][angle]
        if np.linalg.cond(gram) > 1.0e10:
            return (-np.inf, {}) if return_coefficients else -np.inf
        beta = np.linalg.solve(gram, rhs)
        total_gain += float(rhs @ beta)
        coefficients[angle] = beta
    score = total_gain / max(state["total_baseline_rss"], np.finfo(float).eps)
    return (score, coefficients) if return_coefficients else score


def coarse_scan(state, t0):
    # 小样原型仅扫描一个线性色散维度；不增加高阶色散以避免超时与严重共线。
    d_grid = np.linspace(2.0, 80.0, 131)
    n0_grid = np.linspace(2.45, 2.75, 5)
    slope_grid = np.linspace(-0.12, 0.12, 5)
    candidates = []
    stopped_early = False

    try:
        for n0 in n0_grid:
            for slope in slope_grid:
                for j, thickness_um in enumerate(d_grid):
                    if j % 12 == 0:
                        check_budget(t0)
                    params = (float(thickness_um), float(n0), float(slope))
                    candidates.append((float(coherence_score(params, state)), params))
    except TimeBudgetReached:
        stopped_early = True

    if not candidates:
        raise TimeBudgetReached("粗扫描未完成首个候选点。")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates, stopped_early


def select_diverse_starts(candidates, count=3):
    selected = []
    for score, params in candidates:
        if not np.isfinite(score):
            continue
        if all(abs(params[0] - old[1][0]) >= 1.0 for old in selected):
            selected.append((score, params))
        if len(selected) >= count:
            break
    return selected or [candidates[0]]


def refine_candidates(candidates, state, t0):
    best_score, best_params = candidates[0]
    refined_any = False
    for _, start in select_diverse_starts(candidates):
        if elapsed(t0) >= SOFT_BUDGET_SECONDS:
            break
        local_bounds = [
            (max(2.0, start[0] - 1.2), min(80.0, start[0] + 1.2)),
            (max(2.35, start[1] - 0.10), min(2.85, start[1] + 0.10)),
            (max(-0.20, start[2] - 0.08), min(0.20, start[2] + 0.08)),
        ]

        def objective(x):
            check_budget(t0)
            score = coherence_score(x, state)
            return 1.0e6 if not np.isfinite(score) else -score

        try:
            result = minimize(
                objective,
                np.asarray(start, dtype=float),
                method="L-BFGS-B",
                bounds=local_bounds,
                options={"maxiter": 25, "maxfun": 80, "ftol": 1.0e-10},
            )
            refined_any = True
            score = coherence_score(result.x, state)
            if np.isfinite(score) and score > best_score:
                best_score = float(score)
                best_params = tuple(float(v) for v in result.x)
        except TimeBudgetReached:
            break
    return best_score, best_params, refined_any


def quadratic_locations(sigma, signal, indices):
    locations = []
    for idx in indices:
        if idx <= 0 or idx >= len(signal) - 1:
            continue
        x_local = sigma[idx - 1:idx + 2] - sigma[idx]
        y_local = signal[idx - 1:idx + 2]
        a, b, _ = np.polyfit(x_local, y_local, deg=2)
        if abs(a) <= 1.0e-14:
            locations.append(float(sigma[idx]))
            continue
        offset = -b / (2.0 * a)
        if x_local[0] <= offset <= x_local[-1]:
            locations.append(float(sigma[idx] + offset))
        else:
            locations.append(float(sigma[idx]))
    return np.asarray(locations, dtype=float)


def fixed_observed_extrema(sigma_valid, y_valid):
    """路线间共用口径：二次去基线、固定SG规则、局部二次定位。"""
    x = (sigma_valid - np.mean(sigma_valid)) / max(np.ptp(sigma_valid) / 2.0, 1.0)
    baseline = np.polyval(np.polyfit(x, y_valid, deg=2), x)
    residual = y_valid - baseline
    window = min(31, len(residual) if len(residual) % 2 == 1 else len(residual) - 1)
    window = max(window, 7)
    smooth = savgol_filter(residual, window_length=window, polyorder=3, mode="interp")

    diff_median = np.median(np.diff(smooth))
    noise = 1.4826 * np.median(np.abs(np.diff(smooth) - diff_median))
    prominence = max(0.10 * float(np.std(smooth)), 1.5 * float(noise), 1.0e-8)
    peak_idx, _ = find_peaks(smooth, prominence=prominence, distance=8)
    valley_idx, _ = find_peaks(-smooth, prominence=prominence, distance=8)
    # 仅在该固定阈值完全无极值时，回退到同一平滑曲线的无prominence符号变化定位。
    if len(peak_idx) == 0:
        peak_idx, _ = find_peaks(smooth, distance=8)
    if len(valley_idx) == 0:
        valley_idx, _ = find_peaks(-smooth, distance=8)
    return {
        "峰": quadratic_locations(sigma_valid, smooth, peak_idx),
        "谷": quadratic_locations(sigma_valid, -smooth, valley_idx),
    }


def predicted_extrema(sigma_valid, angle, params, beta):
    phase = physical_phase(sigma_valid, angle, *params)
    if phase is None:
        return {"峰": np.array([]), "谷": np.array([])}
    # a*cos(phi)+b*sin(phi)=A*cos(phi-delta), A>=0。
    delta = float(np.arctan2(beta[1], beta[0]))
    z = phase - delta
    if z[0] <= z[-1]:
        z_interp, sigma_interp = z, sigma_valid
    else:
        z_interp, sigma_interp = z[::-1], sigma_valid[::-1]
    k_min = int(np.ceil(z_interp[0] / np.pi))
    k_max = int(np.floor(z_interp[-1] / np.pi))
    peak_positions, valley_positions = [], []
    for k in range(k_min, k_max + 1):
        position = float(np.interp(k * np.pi, z_interp, sigma_interp))
        if sigma_valid[1] <= position <= sigma_valid[-2]:
            (peak_positions if k % 2 == 0 else valley_positions).append(position)
    return {
        "峰": np.asarray(peak_positions, dtype=float),
        "谷": np.asarray(valley_positions, dtype=float),
    }


def nearest_errors(observed, predicted):
    if len(observed) == 0 or len(predicted) == 0:
        return []
    predicted = np.sort(predicted)
    errors = []
    for value in observed:
        idx = int(np.searchsorted(predicted, value))
        neighbors = []
        if idx < len(predicted):
            neighbors.append(predicted[idx])
        if idx > 0:
            neighbors.append(predicted[idx - 1])
        errors.append(float(min(abs(value - candidate) for candidate in neighbors)))
    return errors


def validation_metric(sigma, y_by_angle, valid_mask, params, coefficients):
    sigma_valid = sigma[valid_mask]
    all_errors = []
    counts = {}
    for angle in (10.0, 15.0):
        observed = fixed_observed_extrema(sigma_valid, y_by_angle[angle][valid_mask])
        predicted = predicted_extrema(sigma_valid, angle, params, coefficients[angle])
        angle_errors = []
        for kind in ("峰", "谷"):
            angle_errors.extend(nearest_errors(observed[kind], predicted[kind]))
        if not angle_errors:
            raise ValueError(f"{angle:.0f}°验证块无法形成可比的峰谷位置误差。")
        all_errors.extend(angle_errors)
        counts[f"附件{1 if angle == 10.0 else 2}_观测峰谷数"] = int(len(observed["峰"]) + len(observed["谷"]))
        counts[f"附件{1 if angle == 10.0 else 2}_参与匹配数"] = int(len(angle_errors))
    return float(np.median(all_errors)), counts


def initial_payload(t0):
    return {
        "路线名": ROUTE_NAME,
        "状态": "初始化",
        "核心指标键值": {
            "指标名": "连续遮挡验证块峰谷位置MdAE",
            "值": None,
            "单位": "cm-1",
            "方向": "越小越优",
        },
        "用时估计": {
            "路线侦察预计秒": NOMINAL_RUNTIME_SECONDS,
            "脚本设计上限秒": int(HARD_BUDGET_SECONDS),
            "当前实际用时秒": round(elapsed(t0), 3),
        },
        "口径说明": (
            "使用附件1/2真实数据的1200–2400 cm-1波段，最多保留1800个公共原网格点；"
            "1680–1920 cm-1为居中连续遮挡块，其余左右两块拟合。"
            "用固定平滑与局部二次定位得到观测峰谷，分别与物理相位预测的同类极值最近匹配，"
            "将附件1/2全部绝对波数误差合并后取中位数。"
        ),
    }


def main():
    t0 = time.monotonic()
    payload = initial_payload(t0)
    flush_json(payload)

    try:
        sigma, y_by_angle, fit_mask, valid_mask = load_small_sample()
        payload.update({
            "状态": "真数据小样已读取",
            "小样设置": {
                "波数范围_cm-1": [SIGMA_MIN, SIGMA_MAX],
                "总点数": int(len(sigma)),
                "左右拟合块点数": int(fit_mask.sum()),
                "居中遮挡块点数": int(valid_mask.sum()),
                "遮挡块范围_cm-1": [VALID_MIN, VALID_MAX],
                "是否重采样到等距网格": False,
            },
        })
        payload["用时估计"]["当前实际用时秒"] = round(elapsed(t0), 3)
        flush_json(payload)

        state = prepare_scan_state(sigma, y_by_angle, fit_mask)
        candidates, coarse_stopped = coarse_scan(state, t0)
        payload["状态"] = "粗网格已完成" if not coarse_stopped else "粗网格因时间预算提前收敛"
        payload["用时估计"]["当前实际用时秒"] = round(elapsed(t0), 3)
        flush_json(payload)

        best_score, best_params, refined_any = refine_candidates(candidates, state, t0)
        _, coefficients = coherence_score(best_params, state, return_coefficients=True)
        metric, counts = validation_metric(
            sigma, y_by_angle, valid_mask, best_params, coefficients
        )

        final_elapsed = elapsed(t0)
        if final_elapsed >= HARD_BUDGET_SECONDS:
            raise TimeBudgetReached(f"总用时 {final_elapsed:.1f} 秒超过硬上限。")
        payload.update({
            "状态": "完成（时间预算内提前收敛）" if coarse_stopped else "完成",
            "核心指标键值": {
                "指标名": "连续遮挡验证块峰谷位置MdAE",
                "值": round(metric, 8),
                "单位": "cm-1",
                "方向": "越小越优",
            },
            "原型参数": {
                "共享厚度_um": round(best_params[0], 8),
                "1800cm-1处折射率n0": round(best_params[1], 8),
                "线性色散斜率": round(best_params[2], 8),
                "已执行有界细化": bool(refined_any),
                "相位字典": "原始非均匀波数坐标 + 10°/15° Snell修正 + 共享厚度",
            },
            "评估计数": counts,
        })
        payload["用时估计"]["实际用时秒"] = round(final_elapsed, 3)
        payload["用时估计"].pop("当前实际用时秒", None)
        flush_json(payload)
        print(
            f"{ROUTE_NAME}完成：遮挡块峰谷位置MdAE="
            f"{metric:.6f} cm-1，用时{final_elapsed:.2f}秒。"
        )
    except Exception as exc:
        payload["状态"] = "失败"
        payload["错误"] = f"{type(exc).__name__}: {exc}"
        payload["用时估计"]["实际用时秒"] = round(elapsed(t0), 3)
        payload["用时估计"].pop("当前实际用时秒", None)
        flush_json(payload)
        print(f"{ROUTE_NAME}失败：{type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    main()
