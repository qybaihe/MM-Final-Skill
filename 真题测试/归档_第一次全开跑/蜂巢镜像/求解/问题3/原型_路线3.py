import sys; sys.path.insert(0, '/tmp/蜂巢/pylibs')

import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd


路线名 = "Q3-C 光程域稀疏回波阶梯"
根目录 = Path(__file__).resolve().parents[2]
数据目录 = 根目录 / "数据"
结果目录 = 根目录 / "求解" / "问题3" / "原型结果"
结果文件 = 结果目录 / "路线3.json"

# 搜索在165秒前主动收敛，预留5秒汇总和落盘。
总预算秒 = 170.0
窗口点数 = 512
分块点数 = 64

附件信息 = {
    "附件1.xlsx": ("碳化硅", 10.0),
    "附件2.xlsx": ("碳化硅", 15.0),
    "附件3.xlsx": ("硅", 10.0),
    "附件4.xlsx": ("硅", 15.0),
}

# 原型相位坐标锚点；正式模型应由文献或标定给出完整色散式。
# 本原型仍显式令n随波数变化，并在拟合块上选择线性色散斜率beta。
折射率锚点 = {"碳化硅": 2.60, "硅": 3.42}


def 写结果(payload):
    """原子替换并分步落盘，避免中途轮换留下损坏JSON。"""
    结果目录.mkdir(parents=True, exist_ok=True)
    临时文件 = 结果文件.with_suffix(".json.tmp")
    with 临时文件.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    临时文件.replace(结果文件)


def 读取真数据():
    数据 = {}
    公共波数 = None
    for 文件名, (材料, 角度) in 附件信息.items():
        df = pd.read_excel(
            数据目录 / 文件名,
            sheet_name="Sheet1",
            header=0,
            engine="openpyxl",
        )
        必需列 = ["波数 (cm-1)", "反射率 (%)"]
        if list(df.columns) != 必需列:
            raise ValueError(f"{文件名}列名异常：{list(df.columns)}")
        sigma = df[必需列[0]].to_numpy(dtype=float)
        reflectance = df[必需列[1]].to_numpy(dtype=float)
        if len(sigma) != 7469 or not np.all(np.diff(sigma) > 0):
            raise ValueError(f"{文件名}行数或波数单调性不符合数据档案")
        if not np.all(np.isfinite(reflectance)):
            raise ValueError(f"{文件名}含非有限反射率")
        if 公共波数 is None:
            公共波数 = sigma
        elif not np.array_equal(公共波数, sigma):
            raise ValueError("四附件波数网格不一致")
        数据[文件名] = {
            "材料": 材料,
            "角度": 角度,
            "波数": sigma,
            "反射率": reflectance,
        }
    return 数据


def 构造窗口任务(数据, 材料):
    """硅每角度取三窗，碳化硅每角度只取同位置中间窗。"""
    任务 = []
    for 文件名, item in 数据.items():
        if item["材料"] != 材料:
            continue
        sigma_all = item["波数"]
        y_all = item["反射率"]
        eligible = np.flatnonzero((sigma_all >= 1200.0) & (sigma_all <= 2600.0))
        if len(eligible) < 3 * 窗口点数:
            raise ValueError("1200至2600 cm-1范围不足以构造三个相隔512点窗口")
        last_start = len(eligible) - 窗口点数
        all_local_starts = [0, last_start // 2, last_start]
        local_starts = all_local_starts if 材料 == "硅" else [all_local_starts[1]]
        for 窗口号, local_start in enumerate(local_starts, start=1):
            start = int(eligible[local_start])
            stop = start + 窗口点数
            if stop > len(sigma_all) or sigma_all[stop - 1] > 2600.0:
                raise ValueError(f"窗口{窗口号}越出1200至2600 cm-1约定范围")
            sigma = sigma_all[start:stop]
            y = y_all[start:stop]
            block_id = np.arange(窗口点数) // 分块点数
            train_mask = block_id % 2 == 0
            valid_mask = ~train_mask
            center = float(np.mean(sigma))
            half_range = float((sigma[-1] - sigma[0]) / 2.0)
            train_y = y[train_mask]
            robust_scale = float(np.percentile(train_y, 95) - np.percentile(train_y, 5))
            if robust_scale <= 1e-10:
                robust_scale = max(float(np.std(train_y)), 1.0)
            任务.append({
                "附件": 文件名,
                "角度": item["角度"],
                "窗口号": 窗口号,
                "波数": sigma,
                "反射率": y,
                "拟合掩码": train_mask,
                "遮挡掩码": valid_mask,
                "中心": center,
                "半范围": half_range,
                "尺度": robust_scale,
            })
    expected = 6 if 材料 == "硅" else 2
    if len(任务) != expected:
        raise RuntimeError(f"{材料}窗口任务数应为{expected}，实际为{len(任务)}")
    return 任务


def 色散折射率(材料, sigma, beta):
    x = (sigma - 1900.0) / 700.0
    n = 折射率锚点[材料] + beta * x
    if np.min(n) <= 1.0:
        raise ValueError("候选色散使折射率不满足n>1")
    return n


def 设计矩阵(task, 材料, 厚度_um, beta, qs, mask_name):
    mask = task[mask_name]
    sigma = task["波数"][mask]
    x_local = (sigma - task["中心"]) / task["半范围"]
    cols = [np.ones_like(sigma), x_local, x_local ** 2]

    n = 色散折射率(材料, sigma, beta)
    sin2 = np.sin(np.deg2rad(task["角度"])) ** 2
    snell_factor = np.sqrt(np.maximum(n ** 2 - sin2, 1e-12))
    d_cm = 厚度_um * 1e-4
    for q in qs:
        phase = 4.0 * np.pi * q * d_cm * sigma * snell_factor
        cols.extend((np.cos(phase), np.sin(phase)))
    return np.column_stack(cols)


def 岭解(X, y):
    penalty = np.full(X.shape[1], 1e-6, dtype=float)
    penalty[:3] = 1e-10
    lhs = X.T @ X + np.diag(penalty)
    rhs = X.T @ y
    try:
        return np.linalg.solve(lhs, rhs)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(lhs, rhs, rcond=None)[0]


def 拟合配置(tasks, 材料, 厚度_um, beta, qs, 返回系数=False):
    标准化残差平方和 = 0.0
    样本数 = 0
    系数表 = []
    for task in tasks:
        X = 设计矩阵(task, 材料, 厚度_um, beta, qs, "拟合掩码")
        y = task["反射率"][task["拟合掩码"]]
        coef = 岭解(X, y)
        residual = y - X @ coef
        标准化残差平方和 += float(np.sum((residual / task["尺度"]) ** 2))
        样本数 += len(y)
        if 返回系数:
            系数表.append(coef)
    参数数 = len(tasks) * (3 + 2 * len(qs))
    bic = 样本数 * np.log(max(标准化残差平方和 / 样本数, 1e-15)) + 参数数 * np.log(样本数)
    return bic, 标准化残差平方和, 系数表


def 组稀疏前向选择(tasks, 材料, 厚度_um, beta):
    """q=1为基本回程；q=2至4按跨全部窗口和角度的组增益进入。"""
    active = [1]
    current_bic, current_sse, _ = 拟合配置(tasks, 材料, 厚度_um, beta, active)
    remaining = [2, 3, 4]
    while remaining:
        trials = []
        for q in remaining:
            qs = active + [q]
            bic, sse, _ = 拟合配置(tasks, 材料, 厚度_um, beta, qs)
            trials.append((bic, sse, q))
        best_bic, best_sse, best_q = min(trials, key=lambda z: z[0])
        relative_gain = (current_sse - best_sse) / max(current_sse, 1e-15)
        if current_bic - best_bic < 2.0 or relative_gain < 0.0015:
            break
        active.append(best_q)
        active.sort()
        remaining.remove(best_q)
        current_bic, current_sse = best_bic, best_sse
    return current_bic, tuple(active)


def 搜索材料参数(tasks, 材料, deadline):
    # 混合网格兼顾薄层低频和较厚层高频，不做O(n^2)全谱扫描。
    coarse_d = np.unique(np.concatenate([
        np.geomspace(0.8, 12.0, 24),
        np.linspace(12.0, 260.0, 72),
    ]))
    coarse_beta = (-0.12, 0.0, 0.12)
    best = None
    截止触发 = False

    def 尝试网格(d_grid, beta_grid, current_best):
        nonlocal 截止触发
        for beta in beta_grid:
            for d_um in d_grid:
                if time.monotonic() >= deadline:
                    截止触发 = True
                    return current_best
                bic, qs = 组稀疏前向选择(tasks, 材料, float(d_um), float(beta))
                candidate = {"BIC": bic, "厚度_um": float(d_um), "beta": float(beta), "阶数": qs}
                if current_best is None or candidate["BIC"] < current_best["BIC"]:
                    current_best = candidate
        return current_best

    best = 尝试网格(coarse_d, coarse_beta, best)
    if best is None:
        bic, qs = 组稀疏前向选择(tasks, 材料, 20.0, 0.0)
        best = {"BIC": bic, "厚度_um": 20.0, "beta": 0.0, "阶数": qs}

    if not 截止触发:
        half_width = max(2.0, 0.08 * best["厚度_um"])
        fine_d = np.linspace(
            max(0.5, best["厚度_um"] - half_width),
            min(280.0, best["厚度_um"] + half_width),
            31,
        )
        fine_beta = np.clip(best["beta"] + np.linspace(-0.06, 0.06, 5), -0.18, 0.18)
        best = 尝试网格(fine_d, np.unique(fine_beta), best)
    best["时间截止触发"] = 截止触发
    return best


def 稳定高阶(tasks, 材料, best):
    qs = best["阶数"]
    _, _, coef_list = 拟合配置(
        tasks, 材料, best["厚度_um"], best["beta"], qs, 返回系数=True
    )
    stable = []
    q1_pos = qs.index(1)
    for q in qs:
        if q == 1:
            continue
        q_pos = qs.index(q)
        ratios_by_angle = {10.0: [], 15.0: []}
        all_ratios = []
        for task, coef in zip(tasks, coef_list):
            amp1 = float(np.hypot(coef[3 + 2 * q1_pos], coef[4 + 2 * q1_pos]))
            ampq = float(np.hypot(coef[3 + 2 * q_pos], coef[4 + 2 * q_pos]))
            ratio = ampq / max(amp1, 1e-10)
            ratios_by_angle[task["角度"]].append(ratio)
            all_ratios.append(ratio)
        angle_ok = all(
            sum(r >= 0.10 for r in values) >= max(1, int(np.ceil(2 * len(values) / 3)))
            for values in ratios_by_angle.values()
        )
        if angle_ok and float(np.median(all_ratios)) >= 0.08:
            stable.append(q)
    return stable


def 材料遮挡预测(tasks, 材料, best):
    stable_q = 稳定高阶(tasks, 材料, best)
    final_qs = tuple([1] + stable_q)
    _, _, coef_list = 拟合配置(
        tasks, 材料, best["厚度_um"], best["beta"], final_qs, 返回系数=True
    )
    errors = {}
    truths = {}
    for task, coef in zip(tasks, coef_list):
        X_valid = 设计矩阵(
            task, 材料, best["厚度_um"], best["beta"], final_qs, "遮挡掩码"
        )
        y_true = task["反射率"][task["遮挡掩码"]]
        y_pred = X_valid @ coef
        errors.setdefault(task["附件"], []).append(y_pred - y_true)
        truths.setdefault(task["附件"], []).append(y_true)
    return errors, truths, final_qs


def 附件NRMSE(error_parts, truth_parts):
    err = np.concatenate(error_parts)
    truth = np.concatenate(truth_parts)
    denominator = float(np.max(truth) - np.min(truth))
    if denominator <= 1e-10:
        denominator = max(float(np.std(truth)), 1.0)
    return float(np.sqrt(np.mean(err ** 2)) / denominator)


def 主流程():
    t0 = time.monotonic()
    global_deadline = t0 + 165.0
    口径 = (
        "使用真实附件1至4；硅附件3/4在1200至2600 cm-1内各取三个相隔的512点连续窗口，"
        "碳化硅附件1/2各取同位置的中间512点窗口；每窗以交替64点连续块划分拟合块和遮挡验证块。原子相位为"
        "phi_q=4*pi*q*d*sigma*sqrt(n(sigma)^2-sin(theta)^2)，同材料10°/15°共享d；"
        "n(sigma)=n0+beta*(sigma-1900)/700，beta只由拟合块选择。q=1固定，q=2至4经组OMP/BIC选择，"
        "且须在两角度各至少2/3窗口稳定出现才进入重构；单窗口迁移测试要求双角均出现。"
        "附件级NRMSE=RMSE/(遮挡观测最大值-最小值)，"
        "先在材料内平均双角，再在两材料间平均；越小越优。附件2大于100%的原值不截断。"
    )
    写结果({
        "路线名": 路线名,
        "状态": "已启动，尚未形成完整核心指标",
        "核心指标键值": {"四附件连续遮挡块_NRMSE": None},
        "用时估计": {"路线侦察预计秒": 150, "脚本硬上限秒": int(总预算秒)},
        "硬截止_秒": 总预算秒,
        "口径说明": 口径,
    })

    数据 = 读取真数据()
    全部误差 = {}
    全部真值 = {}
    稳定阶数 = {}
    搜索状态 = {}
    材料顺序 = ("碳化硅", "硅")

    for idx, 材料 in enumerate(材料顺序):
        tasks = 构造窗口任务(数据, 材料)
        remaining_materials = len(材料顺序) - idx
        now = time.monotonic()
        fair_share = max(20.0, (global_deadline - now - 8.0) / remaining_materials)
        material_deadline = min(global_deadline - 5.0, now + min(76.0, fair_share))
        best = 搜索材料参数(tasks, 材料, material_deadline)
        errors, truths, final_qs = 材料遮挡预测(tasks, 材料, best)
        全部误差.update(errors)
        全部真值.update(truths)
        稳定阶数[材料] = list(final_qs)
        搜索状态[材料] = "主动截止并保留当前最优" if best["时间截止触发"] else "完成粗细网格"
        写结果({
            "路线名": 路线名,
            "状态": f"已完成{idx + 1}/2种材料，分步落盘",
            "核心指标键值": {"四附件连续遮挡块_NRMSE": None},
            "稳定回波阶数_非比较指标": 稳定阶数,
            "搜索状态": 搜索状态,
            "用时估计": {"路线侦察预计秒": 150, "脚本硬上限秒": int(总预算秒)},
            "硬截止_秒": 总预算秒,
            "口径说明": 口径,
        })

    attachment_scores = {
        name: 附件NRMSE(全部误差[name], 全部真值[name]) for name in 附件信息
    }
    material_scores = {
        "碳化硅": float(np.mean([attachment_scores["附件1.xlsx"], attachment_scores["附件2.xlsx"]])),
        "硅": float(np.mean([attachment_scores["附件3.xlsx"], attachment_scores["附件4.xlsx"]])),
    }
    unified_nrmse = float(np.mean(list(material_scores.values())))
    elapsed = float(time.monotonic() - t0)

    result = {
        "路线名": 路线名,
        "状态": "完成",
        "核心指标键值": {"四附件连续遮挡块_NRMSE": unified_nrmse},
        "稳定回波阶数_非比较指标": 稳定阶数,
        "搜索状态": 搜索状态,
        "用时估计": {"路线侦察预计秒": 150, "脚本硬上限秒": int(总预算秒)},
        "实际用时_秒": elapsed,
        "硬截止_秒": 总预算秒,
        "口径说明": 口径,
    }
    写结果(result)
    print(
        f"{路线名}完成：四附件连续遮挡块_NRMSE={unified_nrmse:.6f}，"
        f"稳定阶数={稳定阶数}，实际用时={elapsed:.2f}秒"
    )


if __name__ == "__main__":
    主流程()
