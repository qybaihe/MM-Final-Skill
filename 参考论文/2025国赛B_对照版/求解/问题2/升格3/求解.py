"""问题2升格3：双角色散变投影的激进修复版。

仍以问题1的两束模型为主方法，但修复三处会直接伤害厚度可辨识性的实现缺陷：
1. 主窗口使用全部原始波数点；仅在确定性搜索阶段做与响应无关的计算抽样，最终参数总在全量训练点重估。
2. 两角相位不再完全独立：保留逐角相位响应，同时对模 π 的相位差施加可审计的平滑惩罚。
3. 线性投影由普通最小二乘改为保留全部观测点的 Huber IRLS；不裁剪、不删除超界反射率。

脚本默认总预算18分钟，任何阶段均可把已形成的结果写入升格3/结果。只在本文件入口执行时读取真实附件并求解。
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import time
from pathlib import Path

import numpy as np
from openpyxl import load_workbook
from scipy.optimize import minimize


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "求解" / "问题2" / "升格3" / "结果"
# 留出文件写入与解释器收尾余量，硬上限控制在15分钟以内。
TOTAL_BUDGET = 840.0
SEARCH_BUDGET = 36.0
SENSITIVITY_BUDGET = 20.0
ANGLES = (10.0, 15.0)
MAIN_WINDOW = (1200.0, 3800.0)
END_WINDOW = (3800.0, 4000.122)
GUARD_CM = 20.0
ALPHA = 0.10
BASE_DEGREE = 2
AMP_DEGREE = 1
THICKNESS_BOUNDS = (0.5, 40.0)
N_BOUNDS = (1.2, 6.0)
C_BOUNDS = (-1.0, 1.0)
PHASE_SCALE = math.pi / 6.0
PHASE_LAMBDA = 0.05
HUBER_K = 1.5
MAX_SEARCH_POINTS = 960

FOLD_SPECS = [
    {"折号": 1, "训练块": [1, 2, 4, 5, 7, 8, 10, 11], "校准块": [3, 9], "测试块": [6, 12]},
    {"折号": 2, "训练块": [2, 3, 5, 6, 8, 9, 11, 12], "校准块": [4, 10], "测试块": [1, 7]},
]
FIVE_TEST_BLOCKS = [[1, 2], [3, 4], [5, 6], [7, 8], [9, 10]]


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(clean(payload), stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def write_csv(path: Path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: clean(row.get(key, "")) for key in fieldnames})
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def elapsed(t0):
    return time.monotonic() - t0


def finite_vector(values):
    return np.asarray(values, dtype=float)


def quantile(values, p):
    values = np.asarray(values, dtype=float)
    return float(np.quantile(values, p)) if values.size else float("nan")


def read_inputs():
    archive = json.loads((ROOT / "交接" / "数据档案.json").read_text(encoding="utf-8"))
    metadata = {entry["文件名"]: entry for entry in archive["文件档案"]}
    loaded = []
    audit_files = []
    for filename, angle in zip(("附件1.xlsx", "附件2.xlsx"), ANGLES):
        path = ROOT / "数据" / filename
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != metadata[filename]["文件哈希"]:
            raise ValueError(f"{filename}哈希与数据档案不一致")
        workbook = load_workbook(path, read_only=True, data_only=True)
        if workbook.sheetnames != ["Sheet1"]:
            workbook.close()
            raise ValueError(f"{filename}工作表不符合契约")
        rows = list(workbook["Sheet1"].iter_rows(values_only=True))
        workbook.close()
        if len(rows) != 7470 or tuple(rows[0][:2]) != ("波数 (cm-1)", "反射率 (%)"):
            raise ValueError(f"{filename}不是7469点两列附件")
        sigma, refl, source = [], [], []
        for source_row, row in enumerate(rows[1:], 2):
            if len(row) < 2 or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                       and math.isfinite(float(v)) for v in row[:2]):
                raise ValueError(f"{filename}第{source_row}行含非有限值")
            sigma.append(float(row[0]))
            refl.append(float(row[1]) / 100.0)
            source.append(source_row)
        order = np.argsort(sigma)
        sigma = np.asarray(sigma, dtype=float)[order]
        refl = np.asarray(refl, dtype=float)[order]
        source = np.asarray(source, dtype=int)[order]
        if sigma.size != 7469 or np.any(np.diff(sigma) <= 0):
            raise ValueError(f"{filename}波数不严格递增")
        loaded.append((sigma, refl, source))
        audit_files.append({
            "文件名": filename, "入射角度_度": angle, "SHA256": digest,
            "原始点数": int(sigma.size), "反射率换算": "原始百分数数值/100",
            "原始超百分之百点数": int(np.sum(refl > 1.0)),
            "首点": {"波数_cm^-1": float(sigma[0]), "反射率_百分比": float(refl[0] * 100),
                    "原始行号": int(source[0])},
        })
    if not np.array_equal(loaded[0][0], loaded[1][0]):
        raise ValueError("附件1、2波数未逐行对齐")
    sigma_all = loaded[0][0]
    mask = (sigma_all >= MAIN_WINDOW[0]) & (sigma_all <= MAIN_WINDOW[1])
    sigma = sigma_all[mask]
    values = np.vstack([loaded[0][1][mask], loaded[1][1][mask]])
    source_rows = np.vstack([loaded[0][2][mask], loaded[1][2][mask]])
    audit = {
        "问题": 2, "读取": "XLSX/Sheet1；原始顺序按波数核验后使用原坐标",
        "文件": audit_files, "共同波数点数_全谱": int(sigma_all.size),
        "主窗口_cm^-1": list(MAIN_WINDOW), "主窗口点数_每角度": int(sigma.size),
        "抽样": "无；搜索阶段仅对训练索引做与响应无关的均匀抽样，最终拟合使用全部训练点",
        "超界与首点处置": "原值保留，不裁剪、不删除；主窗口质量标记随源行保留",
        "主窗口源行_附件1": source_rows[0].tolist(),
        "主窗口源行_附件2": source_rows[1].tolist(),
    }
    return sigma, values, source_rows, sigma_all, loaded, audit


def block_ids(n):
    return np.minimum((np.arange(n) * 12 // n) + 1, 12).astype(int)


def make_fold(sigma, values, spec, allow_empty_test=False):
    bids = block_ids(len(sigma))
    train_blocks, calibration_blocks, test_blocks = (set(spec[key]) for key in ("训练块", "校准块", "测试块"))
    if (train_blocks & calibration_blocks) or (train_blocks & test_blocks) or (calibration_blocks & test_blocks):
        raise ValueError("折分块重叠")
    if train_blocks | calibration_blocks | test_blocks != set(range(1, 13)):
        raise ValueError("折分块没有覆盖1至12")
    is_train = np.isin(bids, sorted(train_blocks))
    boundary = np.flatnonzero(is_train[:-1] != is_train[1:])
    edges = (sigma[boundary] + sigma[boundary + 1]) / 2.0
    if edges.size:
        distance = np.min(np.abs(sigma[:, None] - edges[None, :]), axis=1)
        train_idx = np.flatnonzero(is_train & (distance >= GUARD_CM))
    else:
        train_idx = np.flatnonzero(is_train)
    cal_idx = np.flatnonzero(np.isin(bids, sorted(calibration_blocks)))
    test_idx = np.flatnonzero(np.isin(bids, sorted(test_blocks)))
    if len(train_idx) < 100 or (not allow_empty_test and len(test_idx) == 0):
        raise ValueError("切分后训练或测试点过少")
    center = float(np.mean(sigma[train_idx]))
    half = float(max((sigma[train_idx].max() - sigma[train_idx].min()) / 2, 1.0))
    return {"spec": spec, "sigma": sigma, "values": values, "block_id": bids,
            "train_idx": train_idx, "cal_idx": cal_idx, "test_idx": test_idx,
            "center": center, "half": half, "guard_edges": edges.tolist()}


def full_fold(sigma, values):
    return make_fold(sigma, values,
                     {"折号": 0, "训练块": list(range(1, 13)), "校准块": [], "测试块": []},
                     allow_empty_test=True)


def valid_parameters(params):
    d, n_ref, c = [float(v) for v in params]
    if not (THICKNESS_BOUNDS[0] <= d <= THICKNESS_BOUNDS[1]):
        return False
    if not (N_BOUNDS[0] <= n_ref <= N_BOUNDS[1]) or not (C_BOUNDS[0] <= c <= C_BOUNDS[1]):
        return False
    n_lo = n_ref + c * ((2000.0 / MAIN_WINDOW[0]) ** 2 - 1.0)
    n_hi = n_ref + c * ((2000.0 / MAIN_WINDOW[1]) ** 2 - 1.0)
    return min(n_lo, n_hi) > math.sin(math.radians(max(ANGLES)))


def refractive_index(sigma, n_ref, c, mode="经验"):
    if mode == "常数":
        return np.full_like(np.asarray(sigma, dtype=float), float(n_ref))
    return float(n_ref) + float(c) * ((2000.0 / np.asarray(sigma, dtype=float)) ** 2 - 1.0)


def phase_rate(sigma, angle, params, mode="经验"):
    d, n_ref, c = params
    n = refractive_index(sigma, n_ref, c, mode)
    inside = n * n - math.sin(math.radians(angle)) ** 2
    if np.any(inside <= 0):
        return None
    q = np.sqrt(inside)
    return 4.0 * math.pi / 10000.0 * float(d) * np.asarray(sigma, dtype=float) * q


def design(sigma, rate, phase, center, half, base_degree, amp_degree):
    x = (np.asarray(sigma, dtype=float) - center) / half
    columns = [x ** power for power in range(base_degree + 1)]
    oscillation = np.cos(rate + phase)
    columns.extend([(x ** power) * oscillation for power in range(amp_degree + 1)])
    return np.column_stack(columns)


def huber_loss(residual, scale):
    u = np.abs(np.asarray(residual, dtype=float)) / max(float(scale), 1e-8)
    return np.where(u <= HUBER_K, 0.5 * u * u, HUBER_K * u - 0.5 * HUBER_K).mean()


def robust_linear_fit(matrix, y, robust=True):
    matrix = np.asarray(matrix, dtype=float)
    y = np.asarray(y, dtype=float)
    coefficient = np.linalg.lstsq(matrix, y, rcond=None)[0]
    for _ in range(3 if robust else 0):
        residual = y - matrix @ coefficient
        scale = max(1.4826 * float(np.median(np.abs(residual - np.median(residual)))), 1e-5)
        u = np.abs(residual) / (HUBER_K * scale)
        weights = np.where(u <= 1.0, 1.0, 1.0 / np.maximum(u, 1e-12))
        sw = np.sqrt(weights)
        coefficient = np.linalg.lstsq(matrix * sw[:, None], y * sw, rcond=None)[0]
    residual = y - matrix @ coefficient
    scale = max(1.4826 * float(np.median(np.abs(residual - np.median(residual)))), 1e-5)
    return {"系数": coefficient, "残差": residual, "稳健尺度": scale,
            "Huber损失": float(huber_loss(residual, scale)),
            "原始RMSE": float(np.sqrt(np.mean(residual * residual))),
            "秩": int(np.linalg.matrix_rank(matrix)),
            "条件数": float(np.linalg.cond(matrix))}


def wrap_mod_pi(angle):
    value = (float(angle) + math.pi / 2.0) % math.pi - math.pi / 2.0
    return float(value)


def phase_candidates(fixed_phase=None):
    if fixed_phase is not None:
        return [float(fixed_phase)]
    return [j * math.pi / 8.0 for j in range(8)]


def fit_angle(params, fold, angle_index, indices, base_degree=BASE_DEGREE,
              amp_degree=AMP_DEGREE, dispersion_mode="经验", robust=True, fixed_phase=None):
    angle = ANGLES[angle_index]
    rate = phase_rate(fold["sigma"][indices], angle, params, dispersion_mode)
    if rate is None:
        return None
    y = fold["values"][angle_index, indices]
    best = None
    for phase in phase_candidates(fixed_phase):
        matrix = design(fold["sigma"][indices], rate, phase, fold["center"], fold["half"],
                        base_degree, amp_degree)
        fit = robust_linear_fit(matrix, y, robust=robust)
        candidate = {"相位_rad": float(phase % math.pi), "角度_度": float(angle),
                     "基线阶数": int(base_degree), "幅值阶数": int(amp_degree),
                     "色散形式": dispersion_mode, **fit}
        if best is None or candidate["Huber损失"] < best["Huber损失"]:
            best = candidate
    return best


def fit_candidate(params, fold, indices=None, base_degree=BASE_DEGREE, amp_degree=AMP_DEGREE,
                  dispersion_mode="经验", robust=True, phase_scale=PHASE_SCALE):
    params = tuple(float(v) for v in params)
    if not valid_parameters(params):
        return None
    if indices is None:
        indices = fold["train_idx"]
    models = [fit_angle(params, fold, ai, indices, base_degree, amp_degree,
                        dispersion_mode, robust) for ai in range(2)]
    if any(model is None for model in models):
        return None
    phase_gap = wrap_mod_pi(models[0]["相位_rad"] - models[1]["相位_rad"])
    penalty = PHASE_LAMBDA * (phase_gap / max(float(phase_scale), 1e-8)) ** 2
    objective = float(np.mean([m["Huber损失"] for m in models]) + penalty)
    return {"参数": params, "模型": models, "目标损失": objective,
            "无惩罚损失": float(np.mean([m["Huber损失"] for m in models])),
            "相位差_mod_pi_rad": phase_gap, "相位惩罚": penalty,
            "训练原始RMSE_比例": float(np.mean([m["原始RMSE"] for m in models])),
            "拟合点数": int(len(indices)), "稳健拟合": bool(robust),
            "相位耦合尺度_rad": float(phase_scale)}


def evenly_subsample(indices, maximum=MAX_SEARCH_POINTS):
    indices = np.asarray(indices, dtype=int)
    if len(indices) <= maximum:
        return indices
    chosen = np.linspace(0, len(indices) - 1, maximum).round().astype(int)
    return indices[np.unique(chosen)]


def parameter_grid(dispersion_mode, fixed_n=None):
    d_values = np.unique(np.r_[np.geomspace(THICKNESS_BOUNDS[0], THICKNESS_BOUNDS[1], 42),
                               [5.073684210526316, 5.171033357319079, 8.0, 10.0, 20.0]])
    n_values = [float(fixed_n)] if fixed_n is not None else [1.2, 1.6, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0]
    c_values = [0.0] if dispersion_mode == "常数" else [-0.75, -0.5, 0.0, 0.5, 0.75]
    return d_values, n_values, c_values


def invalid_fixed_n(fixed_n, dispersion_mode, policy):
    if fixed_n is None:
        return None, False, ""
    requested = float(fixed_n)
    c_values = [0.0] if dispersion_mode == "常数" else [-0.75, -0.5, 0.0, 0.5, 0.75]
    feasible = N_BOUNDS[0] <= requested <= N_BOUNDS[1] and any(
        valid_parameters((1.0, requested, c)) for c in c_values)
    if feasible:
        return requested, False, ""
    if policy == "严格":
        return None, False, "请求固定参考折射率超出搜索盒或不满足传播约束"
    clipped = min(max(requested, N_BOUNDS[0]), N_BOUNDS[1])
    if not any(valid_parameters((1.0, clipped, c)) for c in c_values):
        return None, True, "夹紧到搜索盒后仍不满足传播约束"
    return clipped, True, ""


def search_fold(fold, t0, base_degree=BASE_DEGREE, amp_degree=AMP_DEGREE,
                dispersion_mode="经验", fixed_n=None, fixed_n_policy="严格",
                robust=True, phase_scale=PHASE_SCALE, grid_size=None,
                max_starts=8, budget=SEARCH_BUDGET):
    local_start = time.monotonic()
    deadline = min(local_start + budget, t0 + TOTAL_BUDGET - 25.0)
    effective_n, clamped, invalid_reason = invalid_fixed_n(fixed_n, dispersion_mode, fixed_n_policy)
    if fixed_n is not None and effective_n is None:
        return None, {"搜索状态": "未评估_固定参考折射率不可行", "搜索未评估": True,
                      "搜索预算截断": False, "固定参数一致": False,
                      "请求固定参考折射率": float(fixed_n), "固定参考折射率": None,
                      "固定参考折射率是否夹紧": bool(clamped), "未评估原因": invalid_reason,
                      "搜索用时秒": elapsed(local_start), "搜索预算秒": float(budget)}
    search_indices = evenly_subsample(fold["train_idx"], min(MAX_SEARCH_POINTS, len(fold["train_idx"])))
    d_values, n_values, c_values = parameter_grid(dispersion_mode, effective_n)
    if grid_size is not None and grid_size < len(d_values):
        d_values = np.unique(np.linspace(THICKNESS_BOUNDS[0], THICKNESS_BOUNDS[1], int(grid_size)))
    pool = []
    stopped = False

    def retain(candidate):
        if candidate is None:
            return
        pool.append(candidate)
        pool.sort(key=lambda item: item["目标损失"])
        del pool[12:]

    coarse_count = 0
    for n_ref in n_values:
        for c in c_values:
            for d in d_values:
                if time.monotonic() >= deadline:
                    stopped = True
                    break
                candidate = fit_candidate((float(d), float(n_ref), float(c)), fold, search_indices,
                                          base_degree, amp_degree, dispersion_mode, robust, phase_scale)
                coarse_count += 1
                retain(candidate)
            if stopped:
                break
        if stopped:
            break
    if not pool:
        fallback = (5.1, effective_n if effective_n is not None else 3.0, 0.0)
        candidate = fit_candidate(fallback, fold, search_indices, base_degree, amp_degree,
                                  dispersion_mode, robust, phase_scale)
        if candidate is None:
            raise RuntimeError("搜索没有形成合法候选")
        pool = [candidate]
        stopped = True
    starts = [item["参数"] for item in pool[:max_starts]]
    starts += [(5.073684210526316, effective_n or 3.0, -0.5),
               (5.171033357319079, effective_n or 3.0, -0.5), (8.0, effective_n or 3.0, 0.0)]
    unique = []
    for start in starts:
        d, n, c = start
        n = effective_n if effective_n is not None else n
        c = 0.0 if dispersion_mode == "常数" else c
        item = (float(d), float(n), float(c))
        if valid_parameters(item) and item not in unique:
            unique.append(item)

    for start in unique[:max_starts]:
        if time.monotonic() >= deadline:
            stopped = True
            break
        if effective_n is None and dispersion_mode == "经验":
            z0, bounds = np.asarray(start), [THICKNESS_BOUNDS, N_BOUNDS, C_BOUNDS]
            def unpack(z):
                return tuple(float(v) for v in z)
        elif effective_n is None:
            z0, bounds = np.asarray([start[0], start[1]]), [THICKNESS_BOUNDS, N_BOUNDS]
            def unpack(z):
                return (float(z[0]), float(z[1]), 0.0)
        elif dispersion_mode == "经验":
            z0, bounds = np.asarray([start[0], start[2]]), [THICKNESS_BOUNDS, C_BOUNDS]
            def unpack(z):
                return (float(z[0]), float(effective_n), float(z[1]))
        else:
            z0, bounds = np.asarray([start[0]]), [THICKNESS_BOUNDS]
            def unpack(z):
                return (float(z[0]), float(effective_n), 0.0)

        def objective(z):
            if time.monotonic() >= deadline:
                return 1e12
            params = unpack(z)
            trial = fit_candidate(params, fold, search_indices, base_degree, amp_degree,
                                  dispersion_mode, robust, phase_scale)
            return 1e12 if trial is None else trial["目标损失"]

        result = minimize(objective, z0, method="L-BFGS-B", bounds=bounds,
                          options={"maxiter": 45, "ftol": 1e-8, "maxls": 15})
        candidate = fit_candidate(unpack(result.x), fold, search_indices, base_degree,
                                  amp_degree, dispersion_mode, robust, phase_scale)
        retain(candidate)

    best_search = min(pool, key=lambda item: item["目标损失"])
    best = fit_candidate(best_search["参数"], fold, fold["train_idx"], base_degree,
                         amp_degree, dispersion_mode, robust, phase_scale)
    if best is None:
        best = best_search
    fixed_consistent = effective_n is None or abs(best["参数"][1] - effective_n) <= 1e-10
    info = {
        "搜索状态": "限时完成但候选合法" if stopped else "完整完成", "搜索未评估": False,
        "搜索预算截断": bool(stopped), "固定参数一致": bool(fixed_consistent),
        "粗网格次数": int(coarse_count), "粗网格候选计划次数": int(len(d_values) * len(n_values) * len(c_values)),
        "局部起点数": int(min(len(unique), max_starts)), "搜索用时秒": elapsed(local_start),
        "搜索预算秒": float(budget), "固定参考折射率": effective_n,
        "请求固定参考折射率": None if fixed_n is None else float(fixed_n),
        "固定参考折射率是否夹紧": bool(clamped), "固定参数策略": fixed_n_policy,
        "搜索阶段点数": int(len(search_indices)), "最终训练点数": int(len(fold["train_idx"])),
        "基线阶数": int(base_degree), "幅值阶数": int(amp_degree),
        "色散形式": dispersion_mode, "稳健损失": bool(robust),
        "相位耦合尺度_rad": float(phase_scale),
    }
    return best, info


def predict(candidate, fold, indices, angle_index, phase_override=None):
    model = candidate["模型"][angle_index]
    params = candidate["参数"]
    rate = phase_rate(fold["sigma"][indices], ANGLES[angle_index], params, model["色散形式"])
    phase = model["相位_rad"] if phase_override is None else float(phase_override)
    matrix = design(fold["sigma"][indices], rate, phase, fold["center"], fold["half"],
                    model["基线阶数"], model["幅值阶数"])
    return matrix @ model["系数"]


def conformal_half_width(residual, alpha=ALPHA):
    values = np.sort(np.abs(np.asarray(residual, dtype=float)))
    if values.size == 0:
        return float("nan")
    rank = min(values.size - 1, max(0, int(math.ceil((values.size + 1) * (1.0 - alpha))) - 1))
    return float(values[rank])


def score_blocks(fold, candidate):
    rows = []
    cal_idx = fold["cal_idx"]
    for angle_index, angle in enumerate(ANGLES):
        cal_pred = predict(candidate, fold, cal_idx, angle_index) if len(cal_idx) else np.asarray([])
        cal_residual = fold["values"][angle_index, cal_idx] - cal_pred if len(cal_idx) else np.asarray([])
        half_width = conformal_half_width(cal_residual)
        for test_block in fold["spec"]["测试块"]:
            indices = np.flatnonzero(fold["block_id"] == test_block)
            observed = fold["values"][angle_index, indices]
            predicted = predict(candidate, fold, indices, angle_index)
            residual = observed - predicted
            rows.append({
                "折号": int(fold["spec"]["折号"]), "角度_度": float(angle),
                "测试块": int(test_block), "点数": int(len(indices)),
                "波数_cm^-1": fold["sigma"][indices].tolist(),
                "观测反射率_比例": observed.tolist(), "预测反射率_比例": predicted.tolist(),
                "残差_比例": residual.tolist(), "均方根误差_比例": float(np.sqrt(np.mean(residual ** 2))),
                "训练稳健尺度_比例": float(candidate["模型"][angle_index]["稳健尺度"]),
                "训练点数": int(len(fold["train_idx"])),
                "标准化均方根误差": float(np.sqrt(np.mean(residual ** 2)) /
                                           max(candidate["模型"][angle_index]["稳健尺度"], 1e-4)),
                "校准点数": int(len(cal_idx)), "区间构造": "split-conformal绝对残差有限样本分位",
                "区间名义覆盖率": 1.0 - ALPHA, "区间半宽_比例": half_width,
                "经验覆盖率": None if not math.isfinite(half_width) else
                    float(np.mean(np.abs(residual) <= half_width)),
            })
    return rows


def baseline_blocks(fold):
    rows = []
    for ai, angle in enumerate(ANGLES):
        train_idx = fold["train_idx"]
        x_train = (fold["sigma"][train_idx] - fold["center"]) / fold["half"]
        y_train = fold["values"][ai, train_idx]
        quadratic = np.polyfit(x_train, y_train, 2)
        mean_value = float(np.mean(y_train))
        for block in fold["spec"]["测试块"]:
            indices = np.flatnonzero(fold["block_id"] == block)
            x_test = (fold["sigma"][indices] - fold["center"]) / fold["half"]
            observed = fold["values"][ai, indices]
            for name, predicted in (("训练均值", np.full(len(indices), mean_value)),
                                    ("训练二次趋势", np.polyval(quadratic, x_test))):
                residual = observed - predicted
                rows.append({"折号": int(fold["spec"]["折号"]), "角度_度": float(angle),
                             "测试块": int(block), "基线": name,
                             "均方根误差_比例": float(np.sqrt(np.mean(residual ** 2))),
                             "标准化均方根误差": float(np.sqrt(np.mean(residual ** 2)) /
                                                        max(np.std(y_train), 1e-4))})
    return rows


def frozen_angle_checks(folds, candidates):
    rows = []
    for fold, candidate in zip(folds, candidates):
        for source, target in ((0, 1), (1, 0)):
            frozen_phase = candidate["模型"][source]["相位_rad"]
            target_fit = fit_angle(candidate["参数"], fold, target, fold["train_idx"],
                                   candidate["模型"][target]["基线阶数"],
                                   candidate["模型"][target]["幅值阶数"],
                                   candidate["模型"][target]["色散形式"],
                                   candidate["稳健拟合"], fixed_phase=frozen_phase)
            for block in fold["spec"]["测试块"]:
                indices = np.flatnonzero(fold["block_id"] == block)
                observed = fold["values"][target, indices]
                rate = phase_rate(fold["sigma"][indices], ANGLES[target], candidate["参数"],
                                  target_fit["色散形式"])
                matrix = design(fold["sigma"][indices], rate, frozen_phase, fold["center"],
                                fold["half"], target_fit["基线阶数"], target_fit["幅值阶数"])
                predicted = matrix @ target_fit["系数"]
                residual = observed - predicted
                rows.append({"折号": int(fold["spec"]["折号"]), "来源角度_度": float(ANGLES[source]),
                             "被预测角度_度": float(ANGLES[target]), "测试块": int(block),
                             "冻结共享物理参数": True, "冻结相位": True,
                             "厚度_um": float(candidate["参数"][0]),
                             "标准化均方根误差": float(np.sqrt(np.mean(residual ** 2)) /
                                                        max(target_fit["稳健尺度"], 1e-4))})
    return rows


def five_fold_validation(sigma, values, t0, state):
    fold_rows, prediction_rows = [], []
    for fold_no, test_blocks in enumerate(FIVE_TEST_BLOCKS, 1):
        if elapsed(t0) >= TOTAL_BUDGET - 180:
            break
        calibration = [11, 12]
        training = [b for b in range(1, 13) if b not in set(test_blocks + calibration)]
        spec = {"折号": fold_no + 10, "训练块": training, "校准块": calibration, "测试块": test_blocks}
        fold = make_fold(sigma, values, spec)
        candidate, info = search_fold(fold, t0, grid_size=36, max_starts=5, budget=20.0)
        if candidate is None:
            continue
        blocks = score_blocks(fold, candidate)
        prediction_rows.extend(blocks)
        fold_rows.append({"折号": fold_no, "测试块": test_blocks, "训练块": training,
                          "主指标": float(np.mean([x["标准化均方根误差"] for x in blocks])),
                          "条件厚度_um": float(candidate["参数"][0]), "搜索": info,
                          "条件厚度有效": bool(valid_parameters(candidate["参数"])),
                          "逐折搜索完整且参数有效": bool(info["搜索状态"] == "完整完成" and
                                                       valid_parameters(candidate["参数"]))})
        state.update({"运行状态": f"五折连续留段完成{fold_no}/5", "实际用时秒": elapsed(t0)})
        write_json(OUT / "执行状态.json", state)
        write_json(OUT / "五折_中间.json", {"五折": fold_rows, "分块": prediction_rows,
                                          "实际用时秒": elapsed(t0)})
    return fold_rows, prediction_rows


def summarize_five(rows):
    thickness = [float(row["条件厚度_um"]) for row in rows if row.get("条件厚度有效")]
    complete = bool(rows) and all(row.get("逐折搜索完整且参数有效", False) for row in rows)
    if not thickness:
        return {"有效折数": 0, "五折总折数": 5, "五折条件厚度_微米": [],
                "跨切分稳定性": "无有效折", "逐折搜索完整且参数有效": complete,
                "结论口径": "低置信：没有形成有效五折厚度，仍保留全量条件点估计。"}
    low, high = min(thickness), max(thickness)
    median = float(np.median(thickness))
    rel_range = float((high - low) / max(abs(median), 1e-12))
    status = "不稳定" if (len(thickness) < 5 or not complete or rel_range >= 0.20) else "相对稳定"
    return {"有效折数": len(thickness), "五折总折数": 5, "五折条件厚度_微米": thickness,
            "最小厚度_微米": low, "最大厚度_微米": high, "中位数厚度_微米": median,
            "标准差_微米": float(np.std(thickness, ddof=1)) if len(thickness) > 1 else 0.0,
            "相对极差": rel_range, "跨切分稳定性": status,
            "逐折搜索完整且参数有效": complete,
            "结论口径": "低置信、跨切分不稳定；保留点估计与完整条件范围。" if status == "不稳定"
            else "五折相对稳定；结果仍是条件厚度而非统计置信区间。"}


def profile(full, candidate, t0):
    d, n, c = candidate["参数"]
    d_axis = np.unique(np.r_[np.geomspace(0.5, 40.0, 17), d])
    n_axis = np.unique(np.r_[np.linspace(1.2, 6.0, 13), n])
    indices = evenly_subsample(full["train_idx"], 720)
    rows = []
    for n_ref in n_axis:
        for thickness in d_axis:
            if elapsed(t0) >= TOTAL_BUDGET - 100:
                break
            trial = fit_candidate((float(thickness), float(n_ref), float(c)), full, indices,
                                  phase_scale=PHASE_SCALE)
            if trial is not None:
                rows.append({"厚度_um": float(thickness), "参考折射率": float(n_ref),
                             "色散系数": float(c), "训练标准化损失": float(trial["目标损失"])})
    if not rows:
        rows = [{"厚度_um": float(d), "参考折射率": float(n), "色散系数": float(c),
                 "训练标准化损失": float(candidate["目标损失"])}]
    best_loss = min(row["训练标准化损失"] for row in rows)
    near = [row for row in rows if row["训练标准化损失"] <= best_loss * 1.10 + 1e-12]
    return {"构造方法": "全量训练参数固定色散系数后，对厚度—参考折射率网格逐格重估两角稳健线性投影；搜索抽样只用于节省诊断计算。",
            "数值": rows, "最低训练标准化损失": best_loss, "近等损失阈值": 0.10,
            "近等损失厚度范围_um": [float(min(x["厚度_um"] for x in near)),
                                    float(max(x["厚度_um"] for x in near))],
            "近等损失组合数": len(near), "是否统计置信区间": False,
            "解释": "厚度—折射率条件剖面，不是统计置信区间。"}


def build_window(sigma_all, loaded, window):
    mask = (sigma_all >= window[0]) & (sigma_all <= window[1])
    return sigma_all[mask], np.vstack([loaded[0][1][mask], loaded[1][1][mask]])


def sensitivity(sigma, values, sigma_all, loaded, full, main_candidate, t0):
    variants = [
        {"名称": "主模型", "基线阶数": 2, "幅值阶数": 1, "色散形式": "经验", "稳健损失": True,
         "相位耦合尺度_rad": PHASE_SCALE, "固定参考折射率": None, "固定参数策略": "严格", "纳入条件范围": True},
        {"名称": "相位耦合收紧_pi_12", "基线阶数": 2, "幅值阶数": 1, "色散形式": "经验", "稳健损失": True,
         "相位耦合尺度_rad": math.pi / 12, "固定参考折射率": None, "固定参数策略": "严格", "纳入条件范围": True},
        {"名称": "相位耦合放宽_pi_3", "基线阶数": 2, "幅值阶数": 1, "色散形式": "经验", "稳健损失": True,
         "相位耦合尺度_rad": math.pi / 3, "固定参考折射率": None, "固定参数策略": "严格", "纳入条件范围": True},
        {"名称": "普通最小二乘对照", "基线阶数": 2, "幅值阶数": 1, "色散形式": "经验", "稳健损失": False,
         "相位耦合尺度_rad": PHASE_SCALE, "固定参考折射率": None, "固定参数策略": "严格", "纳入条件范围": True},
        {"名称": "一次基线", "基线阶数": 1, "幅值阶数": 1, "色散形式": "经验", "稳健损失": True,
         "相位耦合尺度_rad": PHASE_SCALE, "固定参考折射率": None, "固定参数策略": "严格", "纳入条件范围": True},
        {"名称": "常数幅值", "基线阶数": 2, "幅值阶数": 0, "色散形式": "经验", "稳健损失": True,
         "相位耦合尺度_rad": PHASE_SCALE, "固定参考折射率": None, "固定参数策略": "严格", "纳入条件范围": True},
        {"名称": "常数折射率", "基线阶数": 2, "幅值阶数": 1, "色散形式": "常数", "稳健损失": True,
         "相位耦合尺度_rad": PHASE_SCALE, "固定参考折射率": None, "固定参数策略": "严格", "纳入条件范围": True},
        {"名称": "窗口下界加100", "基线阶数": 2, "幅值阶数": 1, "色散形式": "经验", "稳健损失": True,
         "相位耦合尺度_rad": PHASE_SCALE, "固定参考折射率": None, "固定参数策略": "严格", "纳入条件范围": True},
        {"名称": "窗口上界加100", "基线阶数": 2, "幅值阶数": 1, "色散形式": "经验", "稳健损失": True,
         "相位耦合尺度_rad": PHASE_SCALE, "固定参考折射率": None, "固定参数策略": "严格", "纳入条件范围": True},
        {"名称": "参考折射率相对减3%_严格请求", "基线阶数": 2, "幅值阶数": 1, "色散形式": "经验", "稳健损失": True,
         "相位耦合尺度_rad": PHASE_SCALE, "固定参考折射率": main_candidate["参数"][1] * 0.97,
         "请求相对变化": -0.03, "固定参数策略": "严格", "纳入条件范围": True},
        {"名称": "参考折射率相对加3%_严格请求", "基线阶数": 2, "幅值阶数": 1, "色散形式": "经验", "稳健损失": True,
         "相位耦合尺度_rad": PHASE_SCALE, "固定参考折射率": main_candidate["参数"][1] * 1.03,
         "请求相对变化": 0.03, "固定参数策略": "严格", "纳入条件范围": True},
    ]
    rows = []
    for variant in variants:
        if elapsed(t0) >= TOTAL_BUDGET - 80:
            break
        target_sigma, target_values = sigma, values
        window_name = "1200-3800"
        if variant["名称"] == "窗口下界加100":
            target_sigma, target_values = build_window(sigma_all, loaded, (1300, 3800))
            window_name = "1300-3800"
        elif variant["名称"] == "窗口上界加100":
            target_sigma, target_values = build_window(sigma_all, loaded, (1200, 3900))
            window_name = "1200-3900"
        fold = full_fold(target_sigma, target_values)
        candidate, info = search_fold(
            fold, t0, variant["基线阶数"], variant["幅值阶数"], variant["色散形式"],
            fixed_n=variant.get("固定参考折射率"), fixed_n_policy=variant["固定参数策略"],
            robust=variant["稳健损失"], phase_scale=variant["相位耦合尺度_rad"],
            budget=SENSITIVITY_BUDGET)
        admissible = bool(candidate is not None and info.get("固定参数一致", False)
                          and not info.get("搜索未评估", False) and variant["纳入条件范围"])
        rows.append({
            "灵敏度项": variant["名称"], "情景名称": variant["名称"],
            "波数窗口_cm^-1": window_name, "厚度_um": None if candidate is None else candidate["参数"][0],
            "参考折射率": None if candidate is None else candidate["参数"][1],
            "色散系数": None if candidate is None else candidate["参数"][2],
            "训练标准化损失": None if candidate is None else candidate["目标损失"],
            "相位差_mod_pi_rad": None if candidate is None else candidate["相位差_mod_pi_rad"],
            "搜索": info, "搜索状态": info.get("搜索状态"),
            "搜索未截断": not bool(info.get("搜索预算截断", False)),
            "搜索未评估": bool(info.get("搜索未评估", False)),
            "固定参考折射率": info.get("固定参考折射率"),
            "请求固定参考折射率": info.get("请求固定参考折射率"),
            "实际固定参考折射率": info.get("固定参考折射率"),
            "固定参考折射率是否夹紧": bool(info.get("固定参考折射率是否夹紧", False)),
            "固定参数策略": variant["固定参数策略"], "固定参数一致": info.get("固定参数一致", False),
            "请求相对变化": variant.get("请求相对变化"),
            "实际相对变化": (None if candidate is None or variant.get("请求相对变化") is None
                              else candidate["参数"][1] / main_candidate["参数"][1] - 1.0),
            "基线阶数": variant["基线阶数"], "幅值阶数": variant["幅值阶数"],
            "色散形式": variant["色散形式"], "稳健损失": variant["稳健损失"],
            "相位耦合尺度_rad": variant["相位耦合尺度_rad"],
            "是否纳入条件范围": admissible, "未评估原因": info.get("未评估原因", ""),
        })
        write_json(OUT / "灵敏度_中间.json", {"窗口与模型灵敏度": rows, "实际用时秒": elapsed(t0)})
    return rows


def end_window_check(sigma_all, loaded, full_candidate, full):
    mask = (sigma_all >= END_WINDOW[0]) & (sigma_all <= END_WINDOW[1])
    rows = []
    sigma = sigma_all[mask]
    for ai, angle in enumerate(ANGLES):
        model = full_candidate["模型"][ai]
        rate = phase_rate(sigma, angle, full_candidate["参数"], model["色散形式"])
        matrix = design(sigma, rate, model["相位_rad"], full["center"], full["half"],
                        model["基线阶数"], model["幅值阶数"])
        predicted = matrix @ model["系数"]
        observed = loaded[ai][1][mask]
        residual = observed - predicted
        rows.append({"角度_度": float(angle), "测试窗口_cm^-1": list(END_WINDOW),
                     "保护窗口_cm^-1": [3750, 3800], "测试点数": int(len(sigma)),
                     "均方根误差_比例": float(np.sqrt(np.mean(residual ** 2))),
                     "平均绝对误差_比例": float(np.mean(np.abs(residual))),
                     "限制": "冻结后的高波数外层检验，不是厚度真值覆盖检验。"})
    return rows


def main():
    t0 = time.monotonic()
    OUT.mkdir(parents=True, exist_ok=True)
    state = {"问题": 2, "变体": "升格3", "运行状态": "开始", "实际用时秒": 0.0,
             "时间预算秒": TOTAL_BUDGET}
    write_json(OUT / "执行状态.json", state)
    try:
        sigma, values, source_rows, sigma_all, loaded, audit = read_inputs()
        audit["实际用时秒"] = elapsed(t0)
        write_json(OUT / "数据审查.json", audit)
        folds = [make_fold(sigma, values, spec) for spec in FOLD_SPECS]
        candidates, searches, scored_blocks, baseline_rows = [], [], [], []
        for fold in folds:
            candidate, info = search_fold(fold, t0)
            if candidate is None:
                raise RuntimeError("主折未形成点估计")
            candidates.append(candidate)
            searches.append(info)
            scored_blocks.extend(score_blocks(fold, candidate))
            baseline_rows.extend(baseline_blocks(fold))
            state.update({"运行状态": f"正式留段完成第{fold['spec']['折号']}折",
                          "实际用时秒": elapsed(t0), "正式测试块已形成": len(scored_blocks)})
            write_json(OUT / "执行状态.json", state)
            write_json(OUT / "留段预测_中间.json", {"分块": scored_blocks, "搜索": searches,
                                                "实际用时秒": elapsed(t0)})
        if not scored_blocks:
            raise RuntimeError("没有形成正式留段指标")
        main_score = float(np.mean([row["标准化均方根误差"] for row in scored_blocks]))
        coverage = float(np.mean([row["经验覆盖率"] for row in scored_blocks
                                  if row["经验覆盖率"] is not None]))
        baseline_summary = {}
        for name in ("训练均值", "训练二次趋势"):
            subset = [row["标准化均方根误差"] for row in baseline_rows if row["基线"] == name]
            baseline_summary[name] = {"连续留段标准化均方根误差": float(np.mean(subset)), "块数": len(subset)}

        full = full_fold(sigma, values)
        full_candidate, full_search = search_fold(full, t0)
        if full_candidate is None:
            raise RuntimeError("全量主方法未形成点估计")
        write_json(OUT / "厚度结果_中间.json", {
            "问题": 2, "变体": "升格3", "运行状态": "全量点估计已形成",
            "同片最佳厚度_微米": full_candidate["参数"][0],
            "参考折射率": full_candidate["参数"][1], "经验色散系数": full_candidate["参数"][2],
            "相位差_mod_pi_rad": full_candidate["相位差_mod_pi_rad"],
            "全量搜索": full_search, "实际用时秒": elapsed(t0),
        })
        full_profile = profile(full, full_candidate, t0)
        five_rows, five_blocks = five_fold_validation(sigma, values, t0, state)
        five_summary = summarize_five(five_rows)
        sens_rows = sensitivity(sigma, values, sigma_all, loaded, full, full_candidate, t0)
        fold_thickness = [float(candidate["参数"][0]) for candidate in candidates]
        five_thickness = [float(row["条件厚度_um"]) for row in five_rows if row.get("条件厚度有效")]
        sensitivity_thickness = [float(row["厚度_um"]) for row in sens_rows
                                 if row.get("是否纳入条件范围") and row.get("厚度_um") is not None]
        components = {
            "两折条件厚度_微米": fold_thickness,
            "五折条件厚度_微米": five_thickness,
            "光学剖面厚度范围_微米": full_profile["近等损失厚度范围_um"],
            "灵敏度情景厚度_微米": sensitivity_thickness,
        }
        all_thickness = [v for seq in components.values() for v in seq]
        condition_range = [float(min(all_thickness)), float(max(all_thickness))]
        formal_baseline = baseline_summary["训练二次趋势"]["连续留段标准化均方根误差"]
        improvement = float(100.0 * (formal_baseline - main_score) / formal_baseline) if formal_baseline else None
        low_conf = five_summary.get("跨切分稳定性") != "相对稳定" or coverage < (1.0 - ALPHA)
        publication = {
            "答案发布判定": "PASS（条件发布）",
            "点估计已形成": True, "条件范围已形成": True,
            "置信等级": "低" if low_conf else "条件中",
            "降级依据": [x for x in ("五折跨切分不稳定" if five_summary.get("跨切分稳定性") != "相对稳定" else "",
                                   "反射率留出覆盖低于名义90%" if coverage < (1.0 - ALPHA) else "") if x],
            "口径": "PASS仅表示真实附件形成数值答案与条件范围，不表示绝对厚度稳定或统计置信协议通过。",
        }
        phase_gap = [float(candidate["相位差_mod_pi_rad"]) for candidate in [full_candidate, *candidates]]
        thickness_result = {
            "问题": 2, "变体": "升格3", "材料": "碳化硅", "输入附件": ["附件1.xlsx", "附件2.xlsx"],
            "运行状态": "已完成", "同片最佳厚度_微米": float(full_candidate["参数"][0]),
            "同片最佳厚度的参考折射率": float(full_candidate["参数"][1]),
            "同片最佳厚度的经验色散系数": float(full_candidate["参数"][2]),
            "条件区间_微米": condition_range,
            "条件区间构造": "两折、五折连续留段、固定经验色散的厚度—参考折射率近等损失剖面和合法灵敏度情景并集；不是统计置信区间。",
            "条件区间组成": components, "两折条件厚度_微米": fold_thickness,
            "五折条件厚度_微米": five_thickness, "逐折搜索": searches,
            "全量拟合参数": full_search, "全量主候选": {"参数": full_candidate["参数"],
                                                   "目标损失": full_candidate["目标损失"],
                                                   "相位差_mod_pi_rad": full_candidate["相位差_mod_pi_rad"]},
            "相位耦合诊断": {"正式尺度_rad": PHASE_SCALE, "全量及两折相位差_mod_pi_rad": phase_gap},
            "光学情景": {"模型": "透明两束；共享厚度、参考折射率和经验色散；逐角低阶基线与幅值",
                         "折射率": "n(σ)=n参+c[(2000/σ)^2−1]，仅为低维经验参数化",
                         "传播约束": "n(σ)>sin(15°)", "稳健投影": "Huber IRLS，原始点不裁剪"},
            "五折跨切分稳定性": five_summary, "答案发布判定": publication,
            "正式结论句": "同片最佳厚度为条件点估计；若五折或留出覆盖不稳定，则按低置信表述并保留完整条件范围。",
            "实际用时秒": elapsed(t0),
        }
        write_json(OUT / "厚度结果.json", thickness_result)
        validation = {
            "问题": 2, "变体": "升格3", "主方法": "双角色散变投影（全点+相位耦合+Huber IRLS）",
            "正式主方法分数_连续留段标准化均方根误差": main_score,
            "正式二次趋势基线分数_连续留段标准化均方根误差": formal_baseline,
            "连续留段标准化均方根误差": main_score,
            "主指标含义": "两折、两角、各测试块RMSE除以对应训练稳健尺度后等权平均；不是厚度误差。",
            "正式主方法相对正式二次趋势改善_百分比": improvement,
            "基线对比": baseline_summary,
            "经验覆盖率": {"构造方法": "split-conformal校准块绝对残差有限样本分位",
                           "校准经验分位点": 1.0 - ALPHA, "名义覆盖率": 1.0 - ALPHA,
                           "测试经验覆盖率": coverage, "测试点数": int(sum(x["点数"] for x in scored_blocks)),
                           "覆盖不足": bool(coverage < 1.0 - ALPHA),
                           "覆盖解释": "仅反射率点预测覆盖，不是厚度覆盖。"},
            "三路线原型对比": {"双角色散变投影": 1.1716972867113729,
                              "双角峰序匹配": 1.2354246858438795,
                              "双角相位回归": 1.2201909148889387,
                              "说明": "沿用锦标赛既有数值，仅作路线选择对照；升格3正式指标单列。"},
            "双向留角度冻结诊断": frozen_angle_checks(folds, candidates),
            "五折连续留段": five_rows, "五折跨切分稳定性": five_summary,
            "窗口与模型灵敏度": sens_rows, "实际用时秒": elapsed(t0),
        }
        write_json(OUT / "验证.json", validation)
        write_json(OUT / "区间覆盖.json", validation["经验覆盖率"])
        write_csv(OUT / "参数剖面.csv", full_profile["数值"],
                  ["厚度_um", "参考折射率", "色散系数", "训练标准化损失"])
        write_csv(OUT / "灵敏度.csv", sens_rows,
                  ["灵敏度项", "情景名称", "波数窗口_cm^-1", "厚度_um", "参考折射率", "色散系数",
                   "训练标准化损失", "相位差_mod_pi_rad", "搜索状态", "搜索未截断", "搜索未评估",
                   "请求固定参考折射率", "实际固定参考折射率", "固定参考折射率是否夹紧", "固定参数一致",
                   "请求相对变化", "实际相对变化", "基线阶数", "幅值阶数", "色散形式", "稳健损失",
                   "相位耦合尺度_rad", "是否纳入条件范围", "未评估原因"])
        end_rows = end_window_check(sigma_all, loaded, full_candidate, full)
        flat_predictions = []
        for row in scored_blocks:
            for sigma_value, observed, predicted, residual in zip(row["波数_cm^-1"], row["观测反射率_比例"],
                                                                   row["预测反射率_比例"], row["残差_比例"]):
                flat_predictions.append({"类型": "正式测试块", "折号": row["折号"], "角度_度": row["角度_度"],
                                         "测试块": row["测试块"], "波数_cm^-1": sigma_value,
                                         "观测反射率_比例": observed, "预测反射率_比例": predicted,
                                         "残差_比例": residual})
        for row in end_rows:
            flat_predictions.append({"类型": "末端新留段", "角度_度": row["角度_度"],
                                     "测试块": "3800-4000.122", "备注": json.dumps(row, ensure_ascii=False)})
        write_csv(OUT / "留段预测.csv", flat_predictions,
                  ["类型", "折号", "角度_度", "测试块", "波数_cm^-1", "观测反射率_比例",
                   "预测反射率_比例", "残差_比例", "备注"])
        write_json(OUT / "基准交接.json", {
            "问题": 2, "变体": "升格3", "用途": "供问题三复算碳化硅条件基准",
            "输入哈希": audit["文件"], "共同窗口_cm^-1": list(MAIN_WINDOW),
            "主窗口点数_每角度": int(len(sigma)), "主窗口源行_按附件": source_rows.tolist(),
            "折分": [fold["spec"] for fold in folds], "训练保护带_cm^-1": GUARD_CM,
            "主模型": "双角色散变投影；相位耦合惩罚+Huber IRLS",
            "全量参数": {"厚度_um": full_candidate["参数"][0], "参考折射率": full_candidate["参数"][1],
                         "色散系数": full_candidate["参数"][2]},
            "两折条件厚度_um": fold_thickness, "条件范围_um": condition_range,
            "五折连续留段": five_rows, "末端新留段": end_rows,
            "评价单位": "反射率比例；厚度微米；波数cm^-1",
            "区间": validation["经验覆盖率"], "原始点不裁剪": True, "实际用时秒": elapsed(t0),
        })
        state = {"问题": 2, "变体": "升格3", "运行状态": "已完成", "实际用时秒": elapsed(t0),
                 "结果文件": ["厚度结果.json", "基准交接.json", "验证.json", "参数剖面.csv",
                             "灵敏度.csv", "留段预测.csv", "区间覆盖.json"]}
        write_json(OUT / "执行状态.json", state)
        print(json.dumps({"问题": 2, "变体": "升格3", "运行状态": "已完成",
                          "同片最佳厚度_微米": thickness_result["同片最佳厚度_微米"],
                          "条件区间_微米": condition_range,
                          "正式主方法分数": main_score, "实际用时秒": elapsed(t0)}, ensure_ascii=False), flush=True)
    except Exception as error:
        state = {"问题": 2, "变体": "升格3", "运行状态": "异常结束但已保存阶段结果",
                 "实际用时秒": elapsed(t0), "错误类型": type(error).__name__, "错误": str(error)}
        write_json(OUT / "执行状态.json", state)
        print(json.dumps(state, ensure_ascii=False), flush=True)
        raise


if __name__ == "__main__":
    main()
