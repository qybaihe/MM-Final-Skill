#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""问题2红队独立复算。

只读取数据/附件1.xlsx和数据/附件2.xlsx。模型采用两束干涉的相位项，
以共享厚度、线性色散和逐角低阶响应做变元回归；不读取建模师脚本或结果。
"""

import json
import os
import sys
import time

# 按蜂巢驱动约定优先使用其运行时依赖。
sys.path.insert(0, "/tmp/蜂巢/pylibs")

import numpy as np
from scipy.optimize import differential_evolution, minimize
from openpyxl import load_workbook


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.join(ROOT, "求解", "问题2", "红队结果")
OUT_FILE = os.path.join(OUT_DIR, "复算结果.json")

START_TIME = time.monotonic()
TIME_BUDGET_SECONDS = 600.0


class BudgetExceeded(RuntimeError):
    pass


def budget_check():
    if time.monotonic() - START_TIME > TIME_BUDGET_SECONDS:
        raise BudgetExceeded("达到复算脚本时间预算，保留当前最优结果")


def read_attachment(path):
    """读取Sheet1的两列数值，保留原始波数与反射率百分数。"""
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb["Sheet1"]
        rows = []
        for row in ws.iter_rows(min_row=2, max_col=2, values_only=True):
            if row[0] is None or row[1] is None:
                continue
            try:
                rows.append((float(row[0]), float(row[1])))
            except (TypeError, ValueError):
                continue
    finally:
        wb.close()
    arr = np.asarray(rows, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 2 or len(arr) < 20:
        raise ValueError("附件数据为空或列结构不符合波数、反射率两列约定")
    return arr


def prepare_data():
    paths = [
        os.path.join(ROOT, "数据", "附件1.xlsx"),
        os.path.join(ROOT, "数据", "附件2.xlsx"),
    ]
    angles = (10.0, 15.0)
    raw = [read_attachment(p) for p in paths]
    x0 = raw[0][:, 0]
    x1 = raw[1][:, 0]
    if len(x0) != len(x1) or not np.allclose(x0, x1, rtol=0.0, atol=1e-8):
        raise ValueError("附件1、2波数网格不能逐点配对，独立复算停止")

    # 与声明的共同窗口一致；窗口内不按全局箱线规则删除数据。
    keep = (x0 >= 1200.0) & (x0 <= 3800.0)
    if not np.any(keep):
        raise ValueError("1200--3800 cm^-1共同窗口无数据")
    x = x0[keep]
    center = 2500.0
    scale = 1300.0
    prepared = []
    for arr, angle in zip(raw, angles):
        y = arr[keep, 1]
        finite = np.isfinite(x) & np.isfinite(y)
        if finite.sum() < 20:
            raise ValueError("共同窗口内有限数据点不足")
        z = (x[finite] - center) / scale
        prepared.append(
            {
                "波数": x[finite],
                "反射率百分数": y[finite],
                "标准化波数": z,
                "入射角度": float(angle),
                "sin入射角": float(np.sin(np.deg2rad(angle))),
            }
        )
    return prepared


def build_design(item, d_um, n0, n1):
    """构造线性响应项；非线性参数只进入干涉相位。"""
    x = item["波数"]
    z = item["标准化波数"]
    n = n0 + n1 * z
    # d_um转为cm；透明两束模型的相位周数为2*波数*d*n*cos(theta_t)。
    d_cm = d_um * 1.0e-4
    transverse = np.maximum(n * n - item["sin入射角"] ** 2, 1.0e-12)
    phase = 2.0 * x * d_cm * np.sqrt(transverse)
    carrier = 2.0 * np.pi * phase
    return np.column_stack(
        [
            np.ones_like(z),
            z,
            z * z,
            z * z * z,
            np.cos(carrier),
            z * np.cos(carrier),
            np.sin(carrier),
            z * np.sin(carrier),
        ]
    )


def make_objective(data):
    state = {"参数": np.array([10.0, 2.0, 0.0], dtype=float), "损失": float("inf")}

    def objective(parameters):
        budget_check()
        d_um, n0, n1 = (float(v) for v in parameters)
        total = 0.0
        count = 0
        for item in data:
            design = build_design(item, d_um, n0, n1)
            y = item["反射率百分数"]
            beta, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
            residual = y - design @ beta
            total += float(np.dot(residual, residual))
            count += len(y)
        loss = total / max(count, 1)
        if loss < state["损失"]:
            state["损失"] = loss
            state["参数"] = np.array([d_um, n0, n1], dtype=float)
        return loss

    return objective, state


def fit_best(data):
    objective, state = make_objective(data)
    bounds = [(0.5, 30.0), (1.2, 3.2), (-0.8, 0.8)]
    objective(state["参数"])
    try:
        de = differential_evolution(
            objective,
            bounds=bounds,
            seed=20260909,
            popsize=8,
            maxiter=55,
            tol=1.0e-7,
            polish=False,
            workers=1,
            updating="immediate",
        )
        try:
            local = minimize(
                objective,
                de.x,
                method="L-BFGS-B",
                bounds=bounds,
                options={"maxiter": 180, "ftol": 1.0e-12, "gtol": 1.0e-8},
            )
            if local.success or local.fun < de.fun:
                objective(local.x)
        except BudgetExceeded:
            pass
    except BudgetExceeded:
        pass
    return objective, state


def profile_interval(data, objective, best_state):
    """固定厚度剖面：保留训练损失不超过最优值110%的厚度包络。"""
    best_loss = float(best_state["损失"])
    if not np.isfinite(best_loss):
        return float(best_state["参数"][0]), float(best_state["参数"][0]), 0

    d_grid = np.linspace(0.5, 30.0, 101)
    profile = []
    starts = [
        np.asarray(best_state["参数"][1:], dtype=float),
        np.asarray([2.4, 0.0], dtype=float),
        np.asarray([1.5, -0.1], dtype=float),
    ]
    for d_um in d_grid:
        budget_check()

        def fixed_objective(q):
            return objective([d_um, float(q[0]), float(q[1])])

        local_best = float("inf")
        for start in starts:
            budget_check()
            try:
                result = minimize(
                    fixed_objective,
                    start,
                    method="L-BFGS-B",
                    bounds=[(1.2, 3.2), (-0.8, 0.8)],
                    options={"maxiter": 80, "ftol": 1.0e-11, "gtol": 1.0e-7},
                )
                local_best = min(local_best, float(result.fun))
            except BudgetExceeded:
                raise
        profile.append((float(d_um), local_best))

    values = np.asarray([v for _, v in profile], dtype=float)
    threshold = 1.10 * float(np.min(values))
    accepted = np.asarray([d for (d, v) in profile if v <= threshold], dtype=float)
    if len(accepted) == 0:
        d = float(best_state["参数"][0])
        return d, d, 0

    # 网格包络是保守区间；边界线性插值只在相邻点均可用时进行。
    lower = float(np.min(accepted))
    upper = float(np.max(accepted))
    return lower, upper, int(len(accepted))


def write_result(data):
    objective, state = fit_best(data)
    profile_error = None
    try:
        lower, upper, accepted_count = profile_interval(data, objective, state)
    except BudgetExceeded as exc:
        profile_error = str(exc)
        lower = upper = float(state["参数"][0])
        accepted_count = 0

    best_d, best_n0, best_n1 = (float(v) for v in state["参数"])
    points = int(sum(len(item["波数"]) for item in data))
    result = {
        "问题": 2,
        "复算方式": "独立实现，未读建模师代码；透明两束相位的变元最小二乘与固定厚度损失剖面",
        "复算指标": {
            "同片最佳厚度_微米": best_d,
            "条件厚度区间下界_微米": float(lower),
            "条件厚度区间上界_微米": float(upper),
        },
        "口径说明": {
            "同片最佳厚度_微米": (
                "单位微米；附件1(10°)与附件2(15°)在1200--3800 cm^-1共同窗口的"
                f"{points}个原始逐点配对样本；全量使用，不剔除窗口内异常反射率点；"
                "共享厚度、线性色散、逐角三次基线及线性正余弦响应，以反射率百分点均方损失最小化。"
            ),
            "条件厚度区间下界_微米": (
                "单位微米；在同一共同窗口和同一模型下固定厚度，重新优化色散参数；"
                "取训练损失不超过全局最小值110%的厚度网格包络下界，非统计置信下界。"
            ),
            "条件厚度区间上界_微米": (
                "单位微米；在同一共同窗口和同一模型下固定厚度，重新优化色散参数；"
                "取训练损失不超过全局最小值110%的厚度网格包络上界，非统计置信上界。"
            ),
        },
        "诊断": {
            "窗口_cm^-1": [1200.0, 3800.0],
            "附件1入射角度_度": 10.0,
            "附件2入射角度_度": 15.0,
            "共同窗口总点数": points,
            "最佳损失_反射率百分点平方": float(state["损失"]),
            "最佳参考折射率_窗口中心": best_n0,
            "最佳色散斜率_标准化波数": best_n1,
            "区间纳入厚度网格点数": accepted_count,
            "剔除原始点": 0,
        },
    }
    if profile_error is not None:
        result["诊断"]["区间计算备注"] = profile_error

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("红队问题2复算完成")
    print("同片最佳厚度(微米):", best_d)
    print("条件厚度区间(微米):", lower, upper)
    print("共同窗口点数:", points, "最优损失:", float(state["损失"]))


def main():
    data = prepare_data()
    write_result(data)


if __name__ == "__main__":
    main()
