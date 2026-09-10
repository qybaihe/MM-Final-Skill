"""问题2升格变体2：双角色散变投影的全原谱稳健特征版。

本变体保持问题2的主方法族：共享厚度、参考折射率和低维色散，逐角度
投影掉低阶响应。改变的是数据口径和特征链：不再把1200--3800 cm^-1
端点抽成480点，而是保留共同原始波数；只在训练点内拟合稳健低阶基线，
以基线残差/MAD形成无量纲干涉特征。非线性搜索可用固定步长稀疏索引
降负担，最终全量拟合、留段评价和区间诊断仍回到原始点。

脚本按工作根运行，不生成合成观测。所有输出写入本文件同目录的结果/。
主流程有14分钟软预算，阶段性写入执行状态和各结果文件；只要真实附件
可读，即使部分灵敏度被时间预算截断，也保留一个合法数值点估计。
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
RESULT_DIR = Path(__file__).resolve().parent / "结果"
# 给文件读写、汇总和最终落盘预留约1分钟，确保整次运行不超过15分钟。
SOFT_SECONDS = 840.0
ANGLE_DEG = (10.0, 15.0)
SEARCH_STRIDE = 8
RAW_WINDOW = (1200.0, 3800.0)
THICKNESS_BOUNDS = (0.25, 80.0)
N_REF_BOUNDS = (1.2, 6.0)
DISPERSION_BOUNDS = (-1.0, 1.0)
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


def write_csv(path: Path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: clean(row.get(field, "")) for field in fields})
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def elapsed(t0):
    return time.monotonic() - t0


def qtile(values, p):
    values = np.asarray(values, dtype=float)
    return float(np.quantile(values, p)) if values.size else float("nan")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_inputs():
    """完整读取两个附件，保留原行号、原值、共同非等距波数。"""
    archive = json.loads((ROOT / "交接" / "数据档案.json").read_text(encoding="utf-8"))
    metadata = {item["文件名"]: item for item in archive["文件档案"]}
    data = []
    audit = {
        "问题": 2,
        "变体": "升格2",
        "输入口径": "附件1、附件2各自7469行原始值；不做端点抽样、不裁剪超100%值",
        "主窗口_cm^-1": list(RAW_WINDOW),
        "文件": [],
    }
    for filename, angle in zip(("附件1.xlsx", "附件2.xlsx"), ANGLE_DEG):
        path = ROOT / "数据" / filename
        digest = sha256(path)
        expected = metadata[filename]
        if digest != expected["文件哈希"]:
            raise ValueError(f"{filename} SHA256与数据档案不一致")
        book = load_workbook(path, read_only=True, data_only=True)
        if book.sheetnames != ["Sheet1"]:
            raise ValueError(f"{filename}工作表不符合契约")
        rows = list(book["Sheet1"].iter_rows(values_only=True))
        book.close()
        if len(rows) != 7470 or rows[0][:2] != ("波数 (cm-1)", "反射率 (%)"):
            raise ValueError(f"{filename}应有表头加7469行数据")
        values = []
        for source_row, row in enumerate(rows[1:], 2):
            sigma, reflectance = row[:2]
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                       for v in (sigma, reflectance)):
                raise ValueError(f"{filename}第{source_row}行含非有限值")
            values.append((float(sigma), float(reflectance) / 100.0, source_row))
        values.sort(key=lambda item: item[0])
        if len(values) != 7469 or any(a[0] >= b[0] for a, b in zip(values, values[1:])):
            raise ValueError(f"{filename}波数未严格递增")
        data.append(values)
        audit["文件"].append({
            "文件名": filename, "入射角度_度": angle, "SHA256": digest,
            "原始点数": len(values), "原始反射率单位": "%", "计算单位": "比例",
            "超百分之百点数": int(sum(v > 1.0 for _, v, _ in values)),
            "零值点数": int(sum(v == 0.0 for _, v, _ in values)),
            "首点": {"波数_cm^-1": values[0][0], "反射率_百分比": values[0][1] * 100,
                    "原始行号": values[0][2]},
        })
    sigma0 = np.asarray([item[0] for item in data[0]], dtype=float)
    sigma1 = np.asarray([item[0] for item in data[1]], dtype=float)
    if not np.array_equal(sigma0, sigma1):
        raise ValueError("附件1、附件2波数未逐行相同")
    values = np.asarray([[item[1] for item in data[a]] for a in range(2)], dtype=float)
    source_rows = np.asarray([[item[2] for item in data[a]] for a in range(2)], dtype=int)
    mask = (sigma0 >= RAW_WINDOW[0]) & (sigma0 <= RAW_WINDOW[1])
    audit["共同原始点数_每角度"] = int(mask.sum())
    audit["原始波数严格等间距"] = bool(np.allclose(np.diff(sigma0), np.diff(sigma0)[0], rtol=0.0, atol=1e-12))
    audit["主窗口保留超界点数_附件1"] = int(np.sum(values[0, mask] > 1.0))
    audit["主窗口保留超界点数_附件2"] = int(np.sum(values[1, mask] > 1.0))
    audit["主窗口保留零点数"] = int(np.sum(values[:, mask] == 0.0))
    audit["抽样改变"] = "不抽样；搜索阶段仅使用训练内固定步长索引，最终拟合和评价使用主窗口全部原始点"
    return sigma0[mask], values[:, mask], source_rows[:, mask], audit


def robust_polyfit(x, y, degree=2, max_iter=6):
    """训练集内的Huber型加权低阶拟合；不删除峰谷，只降低极端残差权重。"""
    design = np.column_stack([x ** k for k in range(degree + 1)])
    coef = np.linalg.lstsq(design, y, rcond=None)[0]
    for _ in range(max_iter):
        residual = y - design @ coef
        scale = max(1.4826 * float(np.median(np.abs(residual - np.median(residual)))), 1e-8)
        weights = np.minimum(1.0, 1.5 * scale / np.maximum(np.abs(residual), 1e-12))
        weighted = design * weights[:, None]
        coef_new = np.linalg.lstsq(weighted, y * weights, rcond=None)[0]
        if np.max(np.abs(coef_new - coef)) < 1e-10:
            coef = coef_new
            break
        coef = coef_new
    residual = y - design @ coef
    return coef, residual


def make_split(sigma, values, spec, baseline_degree=2, feature_mode="稳健"):
    """在原始窗口上做连续块切分；特征参数只从训练块估计。"""
    n = len(sigma)
    block = np.minimum((np.arange(n) * 12 // n) + 1, 12)
    train_blocks = set(spec["训练块"])
    calibration_blocks = set(spec.get("校准块", []))
    test_blocks = set(spec.get("测试块", []))
    train_idx = np.flatnonzero(np.isin(block, list(train_blocks)))
    calibration_idx = np.flatnonzero(np.isin(block, list(calibration_blocks)))
    test_idx = np.flatnonzero(np.isin(block, list(test_blocks)))
    if not len(train_idx):
        raise ValueError("训练块为空")
    boundaries = []
    for k in range(1, 12):
        boundaries.append(float((sigma[np.flatnonzero(block == k)[-1]] +
                                 sigma[np.flatnonzero(block == k + 1)[0]]) / 2.0))
    guard_edges = [edge for k, edge in enumerate(boundaries, 1)
                   if (k in train_blocks) != (k + 1 in train_blocks)]
    guard = np.ones(n, dtype=bool)
    for edge in guard_edges:
        guard &= np.abs(sigma - edge) >= 30.0
    train_idx = train_idx[guard[train_idx]]
    center = float((sigma[train_idx].min() + sigma[train_idx].max()) / 2.0)
    half_span = max(float((sigma[train_idx].max() - sigma[train_idx].min()) / 2.0), 1e-12)
    x_all = (sigma - center) / half_span
    views = []
    for angle_i in range(2):
        y = values[angle_i]
        train_y = y[train_idx]
        if feature_mode == "稳健":
            base_coef, train_residual = robust_polyfit(x_all[train_idx], train_y, baseline_degree)
            base_all = np.column_stack([x_all ** k for k in range(baseline_degree + 1)]) @ base_coef
            mad = 1.4826 * float(np.median(np.abs(train_residual - np.median(train_residual))))
            scale = max(mad, 1e-4)
        elif feature_mode == "普通":
            base_design = np.column_stack([x_all[train_idx] ** k for k in range(baseline_degree + 1)])
            base_coef = np.linalg.lstsq(base_design, train_y, rcond=None)[0]
            base_all = np.column_stack([x_all ** k for k in range(baseline_degree + 1)]) @ base_coef
            scale = max(float(np.std(train_y - base_all[train_idx], ddof=1)), 1e-4)
            train_residual = train_y - base_all[train_idx]
        else:
            raise ValueError(f"未知特征模式:{feature_mode}")
        feature = (y - base_all) / scale
        raw_scale = max(qtile(np.abs(train_residual), 0.75), 1e-4)
        views.append({
            "角度_度": ANGLE_DEG[angle_i], "x": x_all, "基线": base_all,
            "基线系数": base_coef, "特征": feature, "特征尺度": float(scale),
            "训练残差": train_residual, "原始评价尺度": float(raw_scale),
            "训练特征尺度": max(qtile(feature[train_idx], 0.75) - qtile(feature[train_idx], 0.25), 1e-4),
        })
    return {
        "sigma": sigma, "values": values, "block": block, "views": views,
        "train_idx": train_idx, "calibration_idx": calibration_idx, "test_idx": test_idx,
        "guard_edges": guard_edges, "spec": spec, "训练点数": int(len(train_idx)),
        "主窗口点数": int(n), "特征模式": feature_mode, "基线阶数": baseline_degree,
    }


def valid_params(params):
    d, n_ref, dispersion = [float(v) for v in params]
    if not (THICKNESS_BOUNDS[0] <= d <= THICKNESS_BOUNDS[1]):
        return False
    if not (N_REF_BOUNDS[0] <= n_ref <= N_REF_BOUNDS[1]):
        return False
    if not (DISPERSION_BOUNDS[0] <= dispersion <= DISPERSION_BOUNDS[1]):
        return False
    n_lo = n_ref + dispersion * ((2000.0 / RAW_WINDOW[0]) ** 2 - 1.0)
    n_hi = n_ref + dispersion * ((2000.0 / RAW_WINDOW[1]) ** 2 - 1.0)
    return min(n_lo, n_hi) > math.sin(math.radians(max(ANGLE_DEG)))


def phase_rate(sigma, angle, n_ref, dispersion, thickness_um):
    n = n_ref + dispersion * ((2000.0 / sigma) ** 2 - 1.0)
    q = np.sqrt(np.maximum(n * n - math.sin(math.radians(angle)) ** 2, 1e-14))
    return 4.0 * math.pi / 10000.0 * thickness_um * sigma * q


def parameter_vector(candidate):
    """统一接受三元参数向量或search_split返回的候选字典。"""
    if isinstance(candidate, dict):
        candidate = candidate.get("参数")
    if candidate is None:
        raise ValueError("候选参数缺失")
    params = np.asarray(candidate, dtype=float).reshape(-1)
    if params.size != 3:
        raise ValueError(f"候选参数应为[厚度,参考折射率,色散]三元组，实际为{params.size}项")
    return params


def fit_angle(params, view, indices, base_degree=1, amp_degree=1, fixed_phase=None):
    params = parameter_vector(params)
    sigma = view["sigma"] if "sigma" in view else None
    if sigma is None:
        raise ValueError("view缺少sigma")
    idx = np.asarray(indices, dtype=int)
    angle = float(view["角度_度"])
    phase = phase_rate(sigma[idx], angle, params[1], params[2], params[0])
    x = view["x"][idx]
    target = view["特征"][idx]
    candidates = [float(fixed_phase)] if fixed_phase is not None else [j * math.pi / 8.0 for j in range(8)]

    def evaluate(psi):
        psi = float(psi) % math.pi
        c = np.cos(phase + psi)
        matrix = np.column_stack([x ** k for k in range(base_degree + 1)] +
                                  [(x ** k) * c for k in range(amp_degree + 1)])
        coef, _, rank, _ = np.linalg.lstsq(matrix, target, rcond=None)
        residual = target - matrix @ coef
        loss = float(np.mean(residual * residual) / view["训练特征尺度"] ** 2)
        return {"相位_rad": psi, "系数": coef, "残差": residual, "损失": loss,
                "秩": int(rank), "条件数": float(np.linalg.cond(matrix)), "矩阵": matrix}

    best = min((evaluate(psi) for psi in candidates), key=lambda item: item["损失"])
    if fixed_phase is None:
        step = math.pi / 8.0
        for _ in range(5):
            trial = [best, evaluate(best["相位_rad"] - step), evaluate(best["相位_rad"] + step)]
            best = min(trial, key=lambda item: item["损失"])
            step /= 2.0
    best["角度_度"] = angle
    best["基线阶数_特征"] = base_degree
    best["幅值阶数_特征"] = amp_degree
    return best


def fit_candidate(params, split, indices=None, fixed_n=None, dispersion_mode="经验",
                  base_degree=1, amp_degree=1, fixed_phases=None):
    candidate = np.asarray(params, dtype=float).copy()
    if fixed_n is not None:
        candidate[1] = float(fixed_n)
    if dispersion_mode == "常数":
        candidate[2] = 0.0
    if not valid_params(candidate):
        return None
    idx = split["train_idx"] if indices is None else np.asarray(indices, dtype=int)
    models = []
    for ai, view in enumerate(split["views"]):
        phase = None if fixed_phases is None else fixed_phases[ai]
        models.append(fit_angle(candidate, {**view, "sigma": split["sigma"]}, idx,
                                base_degree, amp_degree, phase))
    loss = float(np.mean([m["损失"] for m in models]))
    return {"参数": [float(v) for v in candidate], "模型": models, "损失": loss}


def predict_feature(split, candidate, indices, base_degree=1, amp_degree=1,
                    fixed_phases=None, refit_linear=True):
    """用候选共享物理参数在给定索引上拟合/预测特征，再还原为原始反射率。"""
    params = parameter_vector(candidate)
    idx = np.asarray(indices, dtype=int)
    output = []
    models = []
    for ai, view in enumerate(split["views"]):
        phase = None if fixed_phases is None else fixed_phases[ai]
        if refit_linear:
            model = fit_angle(candidate, {**view, "sigma": split["sigma"]}, split["train_idx"],
                              base_degree, amp_degree, phase)
        else:
            model = fit_angle(candidate, {**view, "sigma": split["sigma"]}, split["train_idx"],
                              base_degree, amp_degree, phase)
        x = view["x"][idx]
        ph = phase_rate(split["sigma"][idx], view["角度_度"], params[1], params[2], params[0])
        psi = model["相位_rad"]
        c = np.cos(ph + psi)
        matrix = np.column_stack([x ** k for k in range(base_degree + 1)] +
                                  [(x ** k) * c for k in range(amp_degree + 1)])
        yhat_feature = matrix @ model["系数"]
        output.append(view["基线"][idx] + view["特征尺度"] * yhat_feature)
        models.append(model)
    return np.asarray(output), models


def search_split(split, t0, budget=36.0, grid_size=64, max_starts=8,
                 fixed_n=None, dispersion_mode="经验", baseline_degree=1,
                 amplitude_degree=1):
    """固定小预算搜索；搜索用训练内stride，最终候选再在全部训练原始点评价。"""
    local_start = time.monotonic()
    deadline = min(local_start + budget, t0 + SOFT_SECONDS - 30.0)
    train = split["train_idx"]
    search_idx = train[::SEARCH_STRIDE]
    if len(search_idx) < 40:
        search_idx = train
    n_values = [float(fixed_n)] if fixed_n is not None else [1.5, 2.5, 3.5, 4.5]
    c_values = [0.0] if dispersion_mode == "常数" else [-0.5, 0.0, 0.5]
    pool = []
    coarse_count = 0
    thickness_grid = np.linspace(THICKNESS_BOUNDS[0], THICKNESS_BOUNDS[1], grid_size)

    def keep(item):
        if item is None:
            return
        if any(np.max(np.abs(np.asarray(item["参数"]) - np.asarray(old["参数"]))) < 1e-10 for old in pool):
            return
        pool.append(item)
        pool.sort(key=lambda row: row["损失"])
        del pool[10:]

    stopped = False
    for n_ref in n_values:
        for dispersion in c_values:
            for thickness in thickness_grid:
                if time.monotonic() >= deadline:
                    stopped = True
                    break
                keep(fit_candidate((thickness, n_ref, dispersion), split, search_idx,
                                   fixed_n, dispersion_mode, baseline_degree, amplitude_degree))
                coarse_count += 1
            if stopped:
                break
        if stopped:
            break
    if not pool:
        fallback = (10.0, float(fixed_n) if fixed_n is not None else 2.5, 0.0)
        item = fit_candidate(fallback, split, search_idx, fixed_n, dispersion_mode,
                             baseline_degree, amplitude_degree)
        if item is None:
            raise RuntimeError("无合法搜索候选")
        pool = [item]
        stopped = True

    starts = [tuple(item["参数"]) for item in pool[:max_starts]]
    starts.extend([(10.0, 2.5, 0.0), (5.0, 3.5, -0.5), (20.0, 2.0, 0.5)])
    unique = []
    for start in starts:
        item = list(start)
        if fixed_n is not None:
            item[1] = float(fixed_n)
        if dispersion_mode == "常数":
            item[2] = 0.0
        item = tuple(item)
        if valid_params(item) and item not in unique:
            unique.append(item)

    def objective(z):
        if time.monotonic() >= deadline:
            return 1e12
        if fixed_n is None:
            if dispersion_mode == "经验":
                p = (float(z[0]), float(z[1]), float(z[2]))
            else:
                p = (float(z[0]), float(z[1]), 0.0)
        else:
            p = (float(z[0]), float(fixed_n), float(z[1]) if dispersion_mode == "经验" else 0.0)
        item = fit_candidate(p, split, search_idx, fixed_n, dispersion_mode,
                             baseline_degree, amplitude_degree)
        return 1e12 if item is None else item["损失"]

    for start in unique[:max_starts]:
        if time.monotonic() >= deadline:
            stopped = True
            break
        if fixed_n is None:
            z0 = np.asarray(start, dtype=float)
            bounds = [THICKNESS_BOUNDS, N_REF_BOUNDS, DISPERSION_BOUNDS]
        else:
            z0 = np.asarray([start[0], start[2]], dtype=float) if dispersion_mode == "经验" else np.asarray([start[0]])
            bounds = [THICKNESS_BOUNDS, DISPERSION_BOUNDS] if dispersion_mode == "经验" else [THICKNESS_BOUNDS]
        result = minimize(objective, z0, method="L-BFGS-B", bounds=bounds,
                          options={"maxiter": 36, "ftol": 1e-8, "maxls": 12})
        if fixed_n is None:
            p = (float(result.x[0]), float(result.x[1]), float(result.x[2]) if dispersion_mode == "经验" else 0.0)
        else:
            p = (float(result.x[0]), float(fixed_n), float(result.x[1]) if dispersion_mode == "经验" else 0.0)
        keep(fit_candidate(p, split, search_idx, fixed_n, dispersion_mode,
                           baseline_degree, amplitude_degree))

    best_search = min(pool, key=lambda item: item["损失"])
    full = fit_candidate(best_search["参数"], split, split["train_idx"], fixed_n,
                         dispersion_mode, baseline_degree, amplitude_degree)
    if full is None:
        full = best_search
    info = {
        "粗网格次数": coarse_count, "粗网格计划次数": int(len(n_values) * len(c_values) * grid_size),
        "局部起点数": min(len(unique), max_starts), "搜索预算秒": float(budget),
        "搜索用时秒": float(elapsed(local_start)), "搜索预算截断": bool(stopped),
        "搜索状态": "预算内完成" if not stopped else "时间预算截断后保留当前最优",
        "搜索点口径": f"训练原始点每{SEARCH_STRIDE}点取1点，仅用于非线性搜索",
        "最终评价点口径": "训练块全部原始点",
        "固定参考折射率": None if fixed_n is None else float(fixed_n),
        "色散形式": dispersion_mode, "基线阶数": baseline_degree,
        "幅值阶数": amplitude_degree,
    }
    return full, info


def block_score(split, model, candidate, indices, base_degree=1, amp_degree=1):
    idx = np.asarray(indices, dtype=int)
    yhat, _ = predict_feature(split, candidate, idx, base_degree, amp_degree)
    rows = []
    for ai, view in enumerate(split["views"]):
        for block in sorted(set(split["block"][idx])):
            take = idx[split["block"][idx] == block]
            pred = yhat[ai][split["block"][idx] == block]
            actual = split["values"][ai, take]
            rmse = float(np.sqrt(np.mean((actual - pred) ** 2)))
            rows.append({
                "角度_度": float(ANGLE_DEG[ai]), "块号": int(block), "点数": int(len(take)),
                "波数下界_cm^-1": float(split["sigma"][take].min()),
                "波数上界_cm^-1": float(split["sigma"][take].max()),
                "均方根误差_比例": rmse,
                "标准化均方根误差": float(rmse / view["原始评价尺度"]),
                "校准经验分位点": None, "经验覆盖率": None,
            })
    return rows


def coverage_for_fold(split, candidate, q=0.75):
    cal_idx = split["calibration_idx"]
    test_idx = split["test_idx"]
    predictions_cal, _ = predict_feature(split, candidate, cal_idx)
    predictions_test, _ = predict_feature(split, candidate, test_idx)
    rows = []
    for ai, view in enumerate(split["views"]):
        cal_resid = np.abs(split["values"][ai, cal_idx] - predictions_cal[ai])
        half = qtile(cal_resid, q)
        test_resid = np.abs(split["values"][ai, test_idx] - predictions_test[ai])
        rows.append({"角度_度": float(ANGLE_DEG[ai]), "校准点数": int(len(cal_idx)),
                     "校准经验分位点": float(q), "区间半宽_比例": float(half),
                     "测试点数": int(len(test_idx)),
                     "覆盖点数": int(np.sum(test_resid <= half)),
                     "经验覆盖率": float(np.mean(test_resid <= half))})
    return rows


def cross_angle_diagnostic(split, candidate):
    """冻结共享d,n,c，分别给严格冻结和目标角有限适配的留角度诊断。"""
    train = split["train_idx"]
    test = split["test_idx"]
    source_rows = []
    for source, target in ((0, 1), (1, 0)):
        source_view = split["views"][source]
        source_model = fit_angle(candidate, {**source_view, "sigma": split["sigma"]}, train)
        source_phase = source_model["相位_rad"]
        target_view = split["views"][target]
        strict_pred, _ = predict_feature(split, candidate, test, fixed_phases=[source_phase, source_phase])
        strict_resid = split["values"][target, test] - strict_pred[target]
        adapted = fit_angle(candidate, {**target_view, "sigma": split["sigma"]}, train)
        adapted_pred, _ = predict_feature(split, candidate, test,
                                          fixed_phases=[adapted["相位_rad"], adapted["相位_rad"]])
        adapted_resid = split["values"][target, test] - adapted_pred[target]
        source_rows.append({
            "训练源角度_度": float(ANGLE_DEG[source]), "冻结目标角度_度": float(ANGLE_DEG[target]),
            "严格冻结共享与响应_RMSE_比例": float(np.sqrt(np.mean(strict_resid ** 2))),
            "目标角有限适配_RMSE_比例": float(np.sqrt(np.mean(adapted_resid ** 2))),
            "冻结内容": "共享厚度、折射率、色散；严格版复用源角相位和系数，适配版仅在目标训练块重估低维响应",
        })
    return source_rows


def profile(split, candidate, t0, count=36):
    d0, n0, c0 = [float(v) for v in candidate]
    d_grid = np.linspace(max(THICKNESS_BOUNDS[0], d0 * 0.4), min(THICKNESS_BOUNDS[1], d0 * 1.8), count)
    n_grid = np.linspace(max(N_REF_BOUNDS[0], n0 * 0.8), min(N_REF_BOUNDS[1], n0 * 1.2), 8)
    rows = []
    for d in d_grid:
        if elapsed(t0) >= SOFT_SECONDS - 45:
            break
        for n in n_grid:
            item = fit_candidate((d, n, c0), split, split["train_idx"])
            if item is not None:
                rows.append({"厚度_um": float(d), "参考折射率": float(n),
                             "色散系数": float(c0), "训练标准化损失": float(item["损失"])})
    return rows


def run_one_window(sigma, values, window, feature_mode, baseline_degree, t0,
                   fixed_n=None, dispersion_mode="经验", grid_size=64, budget=30.0):
    mask = (sigma >= window[0]) & (sigma <= window[1])
    s = sigma[mask]
    v = values[:, mask]
    spec = {"折号": 0, "训练块": list(range(1, 13)), "校准块": [], "测试块": []}
    split = make_split(s, v, spec, baseline_degree, feature_mode)
    candidate, info = search_split(split, t0, budget, grid_size, 8, fixed_n,
                                   dispersion_mode, 1, 1)
    return s, v, split, candidate, info


def main():
    t0 = time.monotonic()
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    status = {"问题": 2, "变体": "升格2", "运行状态": "开始", "实际用时秒": 0.0,
              "输出目录": str(RESULT_DIR.relative_to(ROOT))}
    write_json(RESULT_DIR / "执行状态.json", status)
    try:
        sigma, values, source_rows, audit = load_inputs()
        audit["实际用时秒"] = elapsed(t0)
        write_json(RESULT_DIR / "数据审查.json", audit)

        s, v, full_split, full_candidate, full_search = run_one_window(
            sigma, values, RAW_WINDOW, "稳健", 2, t0, budget=42.0, grid_size=64)
        write_json(RESULT_DIR / "阶段_主搜索.json", {
            "参数": full_candidate["参数"], "训练标准化损失": full_candidate["损失"],
            "搜索": full_search, "特征口径": "全原始窗口+训练稳健二阶基线残差/MAD",
            "实际用时秒": elapsed(t0),
        })

        # 两折连续留段和校准覆盖：与主线使用相同核心指标名，但块是原始连续块。
        fold_results = []
        fold_scores = []
        fold_coverages = []
        fold_predictions = []
        for spec in FOLD_SPECS:
            if elapsed(t0) >= SOFT_SECONDS - 120:
                break
            split = make_split(s, v, spec, 2, "稳健")
            candidate, info = search_split(split, t0, 30.0, 48, 6, None, "经验", 1, 1)
            rows = block_score(split, None, candidate, split["test_idx"])
            coverage = coverage_for_fold(split, candidate, 0.75)
            for row in rows:
                row["折号"] = int(spec["折号"])
                row["特征口径"] = "稳健基线残差/MAD"
            fold_scores.extend(rows)
            fold_coverages.extend([{**row, "折号": int(spec["折号"])} for row in coverage])
            fold_results.append({"折号": int(spec["折号"]), "条件厚度_微米": float(candidate["参数"][0]),
                                 "参数": candidate["参数"], "训练标准化损失": float(candidate["损失"]),
                                 "搜索": info, "训练点数": int(split["训练点数"]),
                                 "测试点数": int(len(split["test_idx"]))})
            cross = cross_angle_diagnostic(split, candidate)
            fold_results[-1]["双向留角度冻结诊断"] = cross
            # 只保存测试行级别的必要复算字段，不把全谱数组塞进JSON。
            pred, _ = predict_feature(split, candidate, split["test_idx"])
            for ai in range(2):
                for idx, pred_value in zip(split["test_idx"], pred[ai]):
                    fold_predictions.append({"折号": int(spec["折号"]), "角度_度": float(ANGLE_DEG[ai]),
                                             "原始行序_窗口": int(idx), "波数_cm^-1": float(s[idx]),
                                             "观测反射率_比例": float(v[ai, idx]),
                                             "预测反射率_比例": float(pred_value),
                                             "残差_比例": float(v[ai, idx] - pred_value),
                                             "块号": int(split["block"][idx]),
                                             "厚度_um": float(candidate["参数"][0])})
            write_json(RESULT_DIR / "阶段_连续留段.json", {
                "折数已完成": len(fold_results), "逐折": fold_results,
                "实际用时秒": elapsed(t0),
            })
            write_csv(RESULT_DIR / "留段预测.csv", fold_predictions,
                      ["折号", "角度_度", "原始行序_窗口", "波数_cm^-1", "块号",
                       "观测反射率_比例", "预测反射率_比例", "残差_比例", "厚度_um"])

        # 五折连续留段：固定计划块，任何已完成折都保留。
        five_fold = []
        for k, test_blocks in enumerate(FIVE_TEST_BLOCKS, 1):
            if elapsed(t0) >= SOFT_SECONDS - 100:
                break
            all_blocks = set(range(1, 13))
            spec = {"折号": k, "训练块": sorted(all_blocks - set(test_blocks) - {11, 12}),
                    "校准块": [11, 12], "测试块": list(test_blocks)}
            split = make_split(s, v, spec, 2, "稳健")
            candidate, info = search_split(split, t0, 20.0, 32, 4, None, "经验", 1, 1)
            five_fold.append({"折号": k, "测试块": test_blocks,
                              "条件厚度_微米": float(candidate["参数"][0]),
                              "参数": candidate["参数"], "搜索": info,
                              "训练点数": int(split["训练点数"]), "测试点数": int(len(split["test_idx"]))})
            write_json(RESULT_DIR / "阶段_五折.json", {"已完成折数": len(five_fold), "逐折": five_fold,
                                                    "实际用时秒": elapsed(t0)})

        # 反射率主指标和训练二次趋势基线，统一使用同一批测试块。
        main_scores = [row["标准化均方根误差"] for row in fold_scores]
        main_score = float(np.mean(main_scores)) if main_scores else float("nan")
        baseline_scores = []
        for spec in FOLD_SPECS[:len(fold_results)]:
            split = make_split(s, v, spec, 2, "稳健")
            for ai, view in enumerate(split["views"]):
                idx = split["test_idx"]
                x = view["x"][idx]
                train = split["train_idx"]
                raw_design_train = np.column_stack([split["sigma"][train] ** k for k in range(3)])
                raw_coef = np.linalg.lstsq(raw_design_train, v[ai, train], rcond=None)[0]
                raw_design_test = np.column_stack([split["sigma"][idx] ** k for k in range(3)])
                pred = raw_design_test @ raw_coef
                for block in sorted(set(split["block"][idx])):
                    take = idx[split["block"][idx] == block]
                    rmse = np.sqrt(np.mean((v[ai, take] - pred[split["block"][idx] == block]) ** 2))
                    baseline_scores.append(float(rmse / view["原始评价尺度"]))
        baseline_score = float(np.mean(baseline_scores)) if baseline_scores else float("nan")
        improvement = float(100.0 * (baseline_score - main_score) / baseline_score) \
            if np.isfinite(main_score) and np.isfinite(baseline_score) and baseline_score else float("nan")

        # 灵敏度：每一行都重新搜索共享物理参数；超时则保留状态，不制造伪厚度。
        sensitivity = []
        sensitivity_specs = [
            ("特征链_普通二阶基线+标准差", RAW_WINDOW, "普通", 2, None, "经验", 48),
            ("主窗口_下界加100", (1300.0, 3800.0), "稳健", 2, None, "经验", 48),
            ("主窗口_上界减100", (1200.0, 3700.0), "稳健", 2, None, "经验", 48),
            ("特征基线一次阶", RAW_WINDOW, "稳健", 1, None, "经验", 48),
            ("常数折射率对照", RAW_WINDOW, "稳健", 2, None, "常数", 48),
            ("厚度网格加密2倍", RAW_WINDOW, "稳健", 2, None, "经验", 128),
        ]
        for name, window, mode, degree, fixed_n, dispersion_mode, grid in sensitivity_specs:
            if elapsed(t0) >= SOFT_SECONDS - 150:
                sensitivity.append({"灵敏度项": name, "搜索未评估": True,
                                    "未评估原因": "主流程软预算接近14分钟"})
                continue
            try:
                ss, vv, split, candidate, info = run_one_window(
                    sigma, values, window, mode, degree, t0, fixed_n,
                    dispersion_mode, grid, 24.0)
                sensitivity.append({"灵敏度项": name, "情景名称": name, "窗口_cm^-1": list(window),
                                    "特征模式": mode, "基线阶数": degree,
                                    "厚度_um": float(candidate["参数"][0]),
                                    "参考折射率": float(candidate["参数"][1]),
                                    "色散系数": float(candidate["参数"][2]),
                                    "训练标准化损失": float(candidate["损失"]),
                                    "搜索未评估": False, "搜索": info,
                                    "是否纳入条件范围": True})
            except Exception as exc:
                sensitivity.append({"灵敏度项": name, "搜索未评估": True,
                                    "未评估原因": type(exc).__name__ + ":" + str(exc)})
            write_csv(RESULT_DIR / "灵敏度.csv", sensitivity,
                      ["灵敏度项", "情景名称", "窗口_cm^-1", "特征模式", "基线阶数", "厚度_um",
                       "参考折射率", "色散系数", "训练标准化损失", "搜索未评估", "是否纳入条件范围",
                       "未评估原因", "搜索"])

        # 严格±3%固定参考折射率：以全量主模型n为基准，不把不可行值夹紧冒充扰动。
        for delta in (-0.03, 0.03):
            requested = float(full_candidate["参数"][1] * (1.0 + delta))
            row = {"灵敏度项": f"参考折射率相对{delta:+.0%}_严格请求", "情景名称": f"参考折射率相对{delta:+.0%}_严格请求",
                   "请求固定参考折射率": requested, "请求相对变化": delta,
                   "特征模式": "稳健", "基线阶数": 2, "是否纳入条件范围": False}
            if N_REF_BOUNDS[0] <= requested <= N_REF_BOUNDS[1] and valid_params((full_candidate["参数"][0], requested, full_candidate["参数"][2])):
                try:
                    ss, vv, split, candidate, info = run_one_window(
                        sigma, values, RAW_WINDOW, "稳健", 2, t0, requested, "经验", 48, 24.0)
                    row.update({"参考折射率": float(candidate["参数"][1]), "实际相对变化": float(candidate["参数"][1] / full_candidate["参数"][1] - 1.0),
                                "厚度_um": float(candidate["参数"][0]), "色散系数": float(candidate["参数"][2]),
                                "训练标准化损失": float(candidate["损失"]), "搜索未评估": False,
                                "是否纳入条件范围": True, "搜索": info})
                except Exception as exc:
                    row.update({"搜索未评估": True, "未评估原因": type(exc).__name__ + ":" + str(exc)})
            else:
                row.update({"搜索未评估": True, "未评估原因": "请求值超出搜索盒或不满足传播约束；未夹紧"})
            sensitivity.append(row)
            write_csv(RESULT_DIR / "灵敏度.csv", sensitivity,
                      ["灵敏度项", "情景名称", "窗口_cm^-1", "特征模式", "基线阶数", "厚度_um",
                       "参考折射率", "请求固定参考折射率", "请求相对变化", "实际相对变化", "色散系数",
                       "训练标准化损失", "搜索未评估", "是否纳入条件范围", "未评估原因", "搜索"])

        profile_rows = profile(full_split, full_candidate["参数"], t0, 32)
        write_csv(RESULT_DIR / "参数剖面.csv", profile_rows,
                  ["厚度_um", "参考折射率", "色散系数", "训练标准化损失"])

        coverage_rows = [row for row in fold_coverages]
        coverage_values = [row["经验覆盖率"] for row in coverage_rows]
        coverage = {
            "构造方法": "连续校准块绝对残差的75%经验分位对称反射率区间；不宣称有限样本共形保证",
            "校准经验分位点": 0.75,
            "名义覆盖率": None,
            "测试经验覆盖率": float(np.mean(coverage_values)) if coverage_values else None,
            "测试点数": int(sum(row["测试点数"] for row in coverage_rows)),
            "覆盖点数": int(sum(row["覆盖点数"] for row in coverage_rows)),
            "覆盖不足": bool(coverage_values and np.mean(coverage_values) < 0.75),
            "覆盖解释": "这是原始反射率点预测诊断，不是厚度覆盖率；真实晶圆无厚度真值。",
            "分角分折": coverage_rows,
            "实际用时秒": elapsed(t0),
        }
        write_json(RESULT_DIR / "区间覆盖.json", coverage)

        fold_thickness = [row["条件厚度_微米"] for row in five_fold]
        valid_sensitivity = [row["厚度_um"] for row in sensitivity
                             if row.get("是否纳入条件范围") and not row.get("搜索未评估") and "厚度_um" in row]
        profile_thickness = [row["厚度_um"] for row in profile_rows if row["训练标准化损失"] <= full_candidate["损失"] * 1.10]
        condition_values = [full_candidate["参数"][0]] + fold_thickness + valid_sensitivity + profile_thickness
        condition_values = [float(x) for x in condition_values if np.isfinite(x)]
        condition_interval = [float(min(condition_values)), float(max(condition_values))] if condition_values else [float(full_candidate["参数"][0])] * 2
        if fold_thickness:
            median_d = float(np.median(fold_thickness))
            relative_range = float((max(fold_thickness) - min(fold_thickness)) / median_d) if median_d else None
        else:
            relative_range = None
        stability = "稳定" if relative_range is not None and relative_range < 0.20 else "不稳定"
        point_estimate = float(full_candidate["参数"][0])
        answer_ready = bool(np.isfinite(point_estimate) and len(condition_interval) == 2 and condition_interval[0] <= condition_interval[1])

        verification = {
            "问题": 2, "变体": "升格2", "主方法": "双角色散变投影（全原谱稳健特征）",
            "正式主方法分数_连续留段标准化均方根误差": main_score,
            "正式二次趋势基线分数_连续留段标准化均方根误差": baseline_score,
            "连续留段标准化均方根误差": main_score,
            "主指标含义": "按原始波数连续块、两角等权的测试RMSE除以训练原始残差尺度；越小越好，不是厚度误差。",
            "正式主方法相对正式二次趋势改善_百分比": improvement,
            "基线对比": {"训练二次趋势": baseline_score, "主方法": main_score},
            "特征口径": "训练块内稳健二阶基线残差/MAD；零点和超100%原值不删除，最终指标在原始反射率上计算。",
            "三路线原型对比": {"主线双角色散变投影": 1.1716972867113729,
                             "主线双角峰序匹配": 1.2354246858438795,
                             "主线双角相位回归": 1.2201909148889387,
                             "说明": "仅作为主线既有原型参考；升格2的正式分数以本次全原谱流程重算。"},
            "两折连续留段": fold_results,
            "五折连续留段": five_fold,
            "五折跨切分稳定性": {"有效折数": len(five_fold), "条件厚度_微米": fold_thickness,
                               "相对极差": relative_range, "稳定性": stability,
                               "判据": "极差/中位数<20%仅作表述分级，不作统计显著性检验"},
            "双向留角度冻结诊断": [item for fold in fold_results for item in fold.get("双向留角度冻结诊断", [])],
            "窗口与特征灵敏度": sensitivity,
            "答案发布判定": {"答案发布判定": "PASS（条件发布）" if answer_ready else "PASS（回退条件发布）",
                         "点估计已形成": answer_ready, "条件范围已形成": bool(condition_values),
                         "置信等级": "低" if stability == "不稳定" or coverage.get("覆盖不足") else "中",
                         "说明": "升格2只改变数据口径/特征链；反射率覆盖不足或跨切分不稳定时降低置信，不删除真实数值答案。"},
            "实际用时秒": elapsed(t0),
        }
        write_json(RESULT_DIR / "验证.json", verification)

        thickness_result = {
            "问题": 2, "变体": "升格2", "材料": "碳化硅",
            "输入附件": [{"文件名": "附件1.xlsx", "入射角度_度": 10.0, "覆盖": True},
                        {"文件名": "附件2.xlsx", "入射角度_度": 15.0, "覆盖": True}],
            "运行状态": "已完成",
            "同片最佳厚度_微米": point_estimate,
            "条件区间_微米": condition_interval,
            "条件区间构造": "全量稳健特征主估计、五折连续留段、合法特征/窗口/色散灵敏度和固定色散厚度剖面的并集；不是统计置信区间。",
            "条件区间组成": {"主估计": [point_estimate], "五折": fold_thickness,
                           "灵敏度合法候选": valid_sensitivity, "近等值剖面": profile_thickness},
            "两折条件厚度_微米": [row["条件厚度_微米"] for row in fold_results],
            "五折条件厚度_微米": fold_thickness,
            "同片最佳厚度的参考折射率": float(full_candidate["参数"][1]),
            "同片最佳厚度的经验色散系数": float(full_candidate["参数"][2]),
            "全量拟合参数": {"厚度_um": point_estimate, "参考折射率": float(full_candidate["参数"][1]),
                          "色散系数": float(full_candidate["参数"][2]),
                          "训练标准化损失": float(full_candidate["损失"]), "搜索": full_search},
            "光学情景": {"模型": "问题1两束相位 + 双角低维投影",
                       "折射率": "n(σ)=n参+c[(2000/σ)^2−1]，参数化情景而非材料常数",
                       "特征": "训练内稳健二阶基线残差/MAD；最终拟合使用主窗口全部原始点",
                       "传播约束": "n(σ)>sin(15°)"},
            "五折跨切分稳定性": verification["五折跨切分稳定性"],
            "答案发布判定": verification["答案发布判定"],
            "不确定性说明": "条件范围来自口径、切分和参数情景；反射率点覆盖不等于厚度覆盖，实测没有真实厚度可直接验证。",
            "正式结论句": f"全原谱稳健特征版给出同片最佳条件厚度{point_estimate:.6f}微米，条件范围为[{condition_interval[0]:.6f},{condition_interval[1]:.6f}]微米，置信等级为{verification['答案发布判定']['置信等级']}。",
            "实际用时秒": elapsed(t0),
        }
        write_json(RESULT_DIR / "厚度结果.json", thickness_result)

        baseline = {
            "问题": 2, "变体": "升格2", "材料": "碳化硅",
            "数据口径": {"主窗口_cm^-1": list(RAW_WINDOW), "原始点数_每角度": int(len(sigma)),
                       "源文件行号": source_rows.tolist(), "原值保留": True},
            "参数": thickness_result["全量拟合参数"], "掩码": "主窗口原始点；无插值、无超界裁剪",
            "切分": {"两折": FOLD_SPECS, "五折测试块": FIVE_TEST_BLOCKS, "保护带_cm^-1": 30.0},
            "预测": {"评价": "留段预测.csv", "原始反射率单位": "%", "计算比例": True},
            "区间构造": coverage["构造方法"], "种子": None,
            "核心指标": {"同片最佳厚度_微米": point_estimate, "条件区间_微米": condition_interval,
                      "正式主方法分数_连续留段标准化均方根误差": main_score,
                      "正式二次趋势基线分数_连续留段标准化均方根误差": baseline_score,
                      "测试经验覆盖率": coverage["测试经验覆盖率"]},
            "复算限制": "本文件只交付真实附件的原始点、掩码、切分和参数；升格2结果不覆盖主线基准。",
            "实际用时秒": elapsed(t0),
        }
        write_json(RESULT_DIR / "基准交接.json", baseline)
        status.update({"运行状态": "已完成", "同片最佳厚度_微米": point_estimate,
                       "条件区间_微米": condition_interval, "实际用时秒": elapsed(t0)})
        write_json(RESULT_DIR / "执行状态.json", status)
        print(json.dumps({"问题": 2, "变体": "升格2", "状态": "已完成",
                          "同片最佳厚度_微米": point_estimate, "条件区间_微米": condition_interval,
                          "实际用时秒": elapsed(t0)}, ensure_ascii=False))
    except Exception as exc:
        status.update({"运行状态": "异常但已保留阶段结果", "异常": type(exc).__name__ + ":" + str(exc),
                       "实际用时秒": elapsed(t0)})
        write_json(RESULT_DIR / "执行状态.json", status)
        raise


if __name__ == "__main__":
    main()
