import sys

sys.path.insert(0, "/tmp/蜂巢/pylibs")

import json
import math
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import least_squares


ROUTE_NAME = "Q3-A 嵌套Airy—两光束物理似然检验"
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "数据"
OUTPUT_PATH = ROOT / "求解" / "问题3" / "原型结果" / "路线1.json"

T0 = time.monotonic()
HARD_LIMIT_SECONDS = 180.0
SOFT_STOP_SECONDS = 168.0
WINDOW_LENGTH = 512
BLOCK_LENGTH = 64
SIGMA_LOW = 1200.0
SIGMA_HIGH = 2600.0

FILES = {
    "附件1": {"path": DATA_DIR / "附件1.xlsx", "material": "碳化硅", "angle": 10.0},
    "附件2": {"path": DATA_DIR / "附件2.xlsx", "material": "碳化硅", "angle": 15.0},
    "附件3": {"path": DATA_DIR / "附件3.xlsx", "material": "硅", "angle": 10.0},
    "附件4": {"path": DATA_DIR / "附件4.xlsx", "material": "硅", "angle": 15.0},
}

# 仅作原型的低维色散锚点；本脚本不输出正式厚度结论。
REFRACTIVE_INDEX_REFERENCE = {"硅": 3.42, "碳化硅": 2.60}


class TimeBudgetExceeded(RuntimeError):
    pass


def elapsed_seconds():
    return time.monotonic() - T0


def check_time_budget():
    if elapsed_seconds() >= SOFT_STOP_SECONDS:
        raise TimeBudgetExceeded("已到达168秒主动收敛线")


def write_result(metric_value, status):
    """分步原子落盘；除用时外只保留一个可比数值指标。"""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "路线名": ROUTE_NAME,
        "核心指标键值": {
            "四附件连续遮挡块_NRMSE": (
                None if metric_value is None else round(float(metric_value), 10)
            )
        },
        "用时估计": {
            "路线侦察预计秒": 165,
            "脚本硬上限秒": int(HARD_LIMIT_SECONDS),
            "当前实际用时秒": round(elapsed_seconds(), 3),
        },
        "口径说明": (
            "真数据1200–2600 cm-1；硅附件3/4各取3个相隔512点窗口，"
            "碳化硅附件1/2各取同位置的中间512点窗口；窗口内交替64点块训练/遮挡。"
            "M1与M2均只用训练块拟合，共享材料厚度和低维色散，两角度的缓变基线与尺度独立。"
            "仅当rho非边界、可分辨、双角厚度一致且M2遮挡误差改善时选M2，否则回退M1。"
            "单附件NRMSE=RMSE/(遮挡观测最大值-最小值)；先对同材料两角平均，再对两材料平均，越小越优。"
        ),
        "状态": status,
    }
    temporary = OUTPUT_PATH.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, OUTPUT_PATH)


def read_all_data():
    spectra = {}
    common_sigma = None
    for attachment, meta in FILES.items():
        check_time_budget()
        frame = pd.read_excel(meta["path"], sheet_name="Sheet1", header=0, engine="openpyxl")
        if list(frame.columns) != ["波数 (cm-1)", "反射率 (%)"]:
            raise ValueError(f"{attachment}表头与数据档案不一致: {list(frame.columns)}")
        sigma = pd.to_numeric(frame["波数 (cm-1)"], errors="raise").to_numpy(dtype=float)
        reflectance = pd.to_numeric(frame["反射率 (%)"], errors="raise").to_numpy(dtype=float)
        if common_sigma is None:
            common_sigma = sigma
        elif not np.array_equal(common_sigma, sigma):
            raise ValueError("四附件波数键未能逐值对齐")
        spectra[attachment] = reflectance
    return common_sigma, spectra


def choose_windows(common_sigma):
    eligible = np.flatnonzero((common_sigma >= SIGMA_LOW) & (common_sigma <= SIGMA_HIGH))
    if eligible.size < 3 * WINDOW_LENGTH:
        raise ValueError("1200–2600 cm-1范围不足以构造3个512点窗口")
    last_start = eligible.size - WINDOW_LENGTH
    starts = np.array([0, last_start // 2, last_start], dtype=int)
    silicon_windows = [eligible[start : start + WINDOW_LENGTH] for start in starts]
    silicon_carbide_windows = [silicon_windows[1]]
    for left, right in zip(silicon_windows[:-1], silicon_windows[1:]):
        if left[-1] >= right[0]:
            raise ValueError("硅的三个512点窗口未实现相隔")
    return {"硅": silicon_windows, "碳化硅": silicon_carbide_windows}


def make_groups(common_sigma, spectra, windows_by_material):
    groups_by_material = {"硅": [], "碳化硅": []}
    for attachment, meta in FILES.items():
        material = meta["material"]
        sigma_parts = []
        y_parts = []
        train_parts = []
        window_id_parts = []
        for window_id, indices in enumerate(windows_by_material[material]):
            local_index = np.arange(WINDOW_LENGTH)
            block_id = local_index // BLOCK_LENGTH
            train_mask = block_id % 2 == 0
            sigma_parts.append(common_sigma[indices])
            y_parts.append(spectra[attachment][indices])
            train_parts.append(train_mask)
            window_id_parts.append(np.full(WINDOW_LENGTH, window_id, dtype=int))
        sigma = np.concatenate(sigma_parts)
        y = np.concatenate(y_parts)
        train = np.concatenate(train_parts)
        robust_scale = np.percentile(y[train], 75) - np.percentile(y[train], 25)
        if robust_scale <= 1e-9:
            robust_scale = max(float(np.std(y[train])), 1.0)
        groups_by_material[material].append(
            {
                "attachment": attachment,
                "angle": meta["angle"],
                "sigma": sigma,
                "y": y,
                "train": train,
                "valid": ~train,
                "window_id": np.concatenate(window_id_parts),
                "scale": robust_scale,
            }
        )
    return groups_by_material


def airy_centered_shape(phase, rho):
    """Re[e^(i phi)/(1-rho e^(i phi))]；rho=0时严格等于cos(phi)。"""
    cosine = np.cos(phase)
    denominator = np.maximum(1.0 - 2.0 * rho * cosine + rho * rho, 1e-8)
    return (cosine - rho) / denominator


def unpack_parameters(parameters, model_name):
    d_um, dispersion_1, dispersion_2, delta_10, delta_15 = parameters[:5]
    rho = 0.0 if model_name == "M1" else float(parameters[5])
    return d_um, dispersion_1, dispersion_2, delta_10, delta_15, rho


def phase_and_shape(parameters, model_name, material, group):
    d_um, dispersion_1, dispersion_2, delta_10, delta_15, rho = unpack_parameters(
        parameters, model_name
    )
    sigma = group["sigma"]
    z = (sigma - 1900.0) / 700.0
    n_ref = REFRACTIVE_INDEX_REFERENCE[material]
    refractive_index = n_ref * (1.0 + dispersion_1 * z + dispersion_2 * z * z)
    sin_squared = math.sin(math.radians(group["angle"])) ** 2
    optical_factor = np.sqrt(np.maximum(refractive_index * refractive_index - sin_squared, 1e-8))
    delta = delta_10 if group["angle"] == 10.0 else delta_15
    phase = 4.0 * np.pi * (d_um * 1e-4) * sigma * optical_factor + delta
    return phase, airy_centered_shape(phase, rho)


def profile_group(parameters, model_name, material, group):
    _, shape = phase_and_shape(parameters, model_name, material, group)
    z = (group["sigma"] - 1900.0) / 700.0
    # 角度独立的二次基线 + 一次缓变条纹尺度，系数仅由训练块估计。
    design = np.column_stack((np.ones_like(z), z, z * z, shape, z * shape))
    train = group["train"]
    coefficients, _, _, _ = np.linalg.lstsq(design[train], group["y"][train], rcond=None)
    prediction = design @ coefficients
    return prediction


def residual_vector(parameters, model_name, material, groups):
    check_time_budget()
    residuals = []
    for group in groups:
        prediction = profile_group(parameters, model_name, material, group)
        residual = (group["y"][group["train"]] - prediction[group["train"]]) / group["scale"]
        residuals.append(residual / math.sqrt(residual.size))
    return np.concatenate(residuals)


def normalized_train_mse(parameters, model_name, material, groups):
    residual = residual_vector(parameters, model_name, material, groups)
    return float(np.mean(residual * residual))


def spectral_thickness_seeds(material, groups):
    n_ref = REFRACTIVE_INDEX_REFERENCE[material]
    candidates = []
    for group in groups:
        for window_id in np.unique(group["window_id"]):
            select = group["window_id"] == window_id
            sigma = group["sigma"][select]
            y = group["y"][select]
            train = group["train"][select]
            y_filled = np.interp(sigma, sigma[train], y[train])
            z = (sigma - sigma.mean()) / max(float(np.ptp(sigma)), 1e-9)
            trend = np.polyval(np.polyfit(z, y_filled, deg=2), z)
            centered = (y_filled - trend) * np.hanning(y_filled.size)
            frequency = np.fft.rfftfreq(centered.size, d=float(np.median(np.diff(sigma))))
            power = np.abs(np.fft.rfft(centered)) ** 2
            minimum_frequency = 2.0 / max(float(np.ptp(sigma)), 1e-9)
            valid = np.flatnonzero(frequency >= minimum_frequency)
            if valid.size == 0:
                continue
            top = valid[np.argsort(power[valid])[-3:]]
            for index in top:
                # cycles/cm-1 ≈ 2*d(cm)*n，故d(um)=frequency*5000/n。
                d_um = frequency[index] * 5000.0 / n_ref
                candidates.append((float(power[index]), float(np.clip(d_um, 0.5, 2000.0))))
    candidates.sort(reverse=True)
    ordered = [item[1] for item in candidates] + [10.0, 80.0, 400.0, 1200.0]
    seeds = []
    for value in ordered:
        if not any(abs(math.log(value / existing)) < 0.18 for existing in seeds):
            seeds.append(value)
        if len(seeds) >= 6:
            break
    return seeds


def fit_model(material, groups, model_name, thickness_seeds, m1_parameters=None):
    if model_name == "M1":
        starts = [np.array([d, 0.0, 0.0, 0.0, 0.0], dtype=float) for d in thickness_seeds]
        lower = np.array([0.5, -0.08, -0.04, -np.pi, -np.pi])
        upper = np.array([2000.0, 0.08, 0.04, np.pi, np.pi])
        max_nfev = 65
    else:
        base = np.asarray(m1_parameters, dtype=float)
        starts = [np.r_[base, rho] for rho in (0.06, 0.22, 0.50)]
        lower = np.array([0.5, -0.08, -0.04, -np.pi, -np.pi, 0.0])
        upper = np.array([2000.0, 0.08, 0.04, np.pi, np.pi, 0.85])
        max_nfev = 80

    best_parameters = starts[0].copy()
    best_cost = normalized_train_mse(best_parameters, model_name, material, groups)
    for start in starts:
        if elapsed_seconds() >= SOFT_STOP_SECONDS - 8.0:
            break
        try:
            result = least_squares(
                residual_vector,
                x0=np.minimum(np.maximum(start, lower + 1e-10), upper - 1e-10),
                bounds=(lower, upper),
                args=(model_name, material, groups),
                loss="soft_l1",
                f_scale=0.15,
                x_scale="jac",
                max_nfev=max_nfev,
            )
        except TimeBudgetExceeded:
            break
        candidate_cost = normalized_train_mse(result.x, model_name, material, groups)
        if candidate_cost < best_cost:
            best_cost = candidate_cost
            best_parameters = result.x.copy()
    return best_parameters, best_cost


def attachment_nrmse(groups, predictions):
    values = {}
    for group in groups:
        valid = group["valid"]
        observed = group["y"][valid]
        predicted = predictions[group["attachment"]][valid]
        rmse = float(np.sqrt(np.mean((observed - predicted) ** 2)))
        denominator = float(np.max(observed) - np.min(observed))
        if denominator <= 1e-12:
            denominator = max(float(np.std(observed)), 1e-12)
        values[group["attachment"]] = rmse / denominator
    return values


def predictions_for(parameters, model_name, material, groups):
    return {
        group["attachment"]: profile_group(parameters, model_name, material, group)
        for group in groups
    }


def double_angle_consistent(parameters, model_name, material, groups):
    d_shared = float(parameters[0])
    relative_grid = np.linspace(0.90, 1.10, 9)
    angle_optima = []
    for group in groups:
        costs = []
        for multiplier in relative_grid:
            candidate = np.asarray(parameters, dtype=float).copy()
            candidate[0] = np.clip(d_shared * multiplier, 0.5, 2000.0)
            costs.append(normalized_train_mse(candidate, model_name, material, [group]))
        angle_optima.append(d_shared * relative_grid[int(np.argmin(costs))])
    relative_gap = abs(angle_optima[0] - angle_optima[1]) / max(np.mean(angle_optima), 1e-9)
    return relative_gap <= 0.10


def fringes_resolvable(parameters, model_name, material, groups):
    checks = []
    for group in groups:
        phase, _ = phase_and_shape(parameters, model_name, material, group)
        for window_id in np.unique(group["window_id"]):
            local_phase = phase[group["window_id"] == window_id]
            step = float(np.median(np.abs(np.diff(local_phase))))
            span = float(np.ptp(local_phase))
            checks.append(step < np.pi and span >= 2.0 * np.pi)
    return bool(checks) and all(checks)


def fit_material(material, groups):
    thickness_seeds = spectral_thickness_seeds(material, groups)
    m1_parameters, m1_train_cost = fit_model(material, groups, "M1", thickness_seeds)
    m2_parameters, m2_train_cost = fit_model(
        material, groups, "M2", thickness_seeds, m1_parameters=m1_parameters
    )

    m1_predictions = predictions_for(m1_parameters, "M1", material, groups)
    m2_predictions = predictions_for(m2_parameters, "M2", material, groups)
    m1_nrmse = float(np.mean(list(attachment_nrmse(groups, m1_predictions).values())))
    m2_nrmse = float(np.mean(list(attachment_nrmse(groups, m2_predictions).values())))

    rho = float(m2_parameters[5])
    rho_interior = 0.015 <= rho <= 0.82
    train_count = int(sum(np.count_nonzero(group["train"]) for group in groups))
    # M2比M1仅多1个rho参数；以DeltaBIC<0作为原型阶段的似然识别门槛。
    bic_delta = train_count * math.log(
        max(m2_train_cost, 1e-15) / max(m1_train_cost, 1e-15)
    ) + math.log(max(train_count, 2))
    train_identifiable = bic_delta < 0.0
    validation_improves = m2_nrmse <= 0.995 * m1_nrmse
    angle_consistent = double_angle_consistent(m2_parameters, "M2", material, groups)
    resolvable = fringes_resolvable(m2_parameters, "M2", material, groups)
    trigger_m2 = rho_interior and train_identifiable and validation_improves and angle_consistent and resolvable

    selected_parameters = m2_parameters if trigger_m2 else m1_parameters
    selected_model = "M2" if trigger_m2 else "M1"
    selected_predictions = predictions_for(selected_parameters, selected_model, material, groups)
    return selected_predictions, trigger_m2


def main():
    write_result(None, "已启动，结果待计算")
    common_sigma, spectra = read_all_data()
    windows_by_material = choose_windows(common_sigma)
    groups_by_material = make_groups(common_sigma, spectra, windows_by_material)
    write_result(None, "已读取真数据并完成连续分块")

    selected_predictions = {}
    triggers = {}
    # 先做较小的碳化硅任务，保证在临界时限前至少有一个已落盘材料。
    for material in ("碳化硅", "硅"):
        check_time_budget()
        predictions, trigger_m2 = fit_material(material, groups_by_material[material])
        selected_predictions[material] = predictions
        triggers[material] = trigger_m2
        write_result(None, f"已完成{material}的M1/M2嵌套拟合")

    per_material = {}
    for material, groups in groups_by_material.items():
        per_attachment = attachment_nrmse(groups, selected_predictions[material])
        per_material[material] = float(np.mean(list(per_attachment.values())))
    final_metric = float(np.mean([per_material["碳化硅"], per_material["硅"]]))
    write_result(final_metric, "完成")
    print(
        json.dumps(
            {
                "路线名": ROUTE_NAME,
                "四附件连续遮挡块_NRMSE": round(final_metric, 10),
                "模型选择": {
                    "碳化硅": "M2" if triggers["碳化硅"] else "M1",
                    "硅": "M2" if triggers["硅"] else "M1",
                },
                "实际用时秒": round(elapsed_seconds(), 3),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except TimeBudgetExceeded as exc:
        write_result(None, f"主动收敛但未完成四附件指标: {exc}")
        print(f"[主动收敛] {exc}")
    except Exception as exc:
        write_result(None, f"失败: {type(exc).__name__}: {exc}")
        raise
