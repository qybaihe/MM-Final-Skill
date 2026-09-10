import sys; sys.path.insert(0, '/tmp/蜂巢/pylibs')

import itertools
import json
import math
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd


路线名 = "Q3-B 相位域谐波指纹—基本波保真修正"
根目录 = Path("/tmp/蜂巢")
数据目录 = 根目录 / "数据"
结果目录 = 根目录 / "求解" / "问题3" / "原型结果"
结果文件 = 结果目录 / "路线2.json"
预计用时秒 = 95
软截止秒 = 165.0       # 180秒硬上限前预留汇总、刷新时间
搜索截止秒 = 135.0
窗口点数 = 512
块长 = 64

材料配置 = {
    "硅": {
        "附件": [("附件3.xlsx", 10.0), ("附件4.xlsx", 15.0)],
        "窗口中心": [1350.0, 1900.0, 2450.0],
        "参考折射率": 3.42,
        "斜率范围": (-0.24, 0.24),
    },
    "碳化硅": {
        "附件": [("附件1.xlsx", 10.0), ("附件2.xlsx", 15.0)],
        "窗口中心": [1900.0],
        "参考折射率": 2.60,
        "斜率范围": (-0.20, 0.20),
    },
}


def 用时(t0):
    return time.perf_counter() - t0


def 刷新(payload):
    """分步写合法JSON；进程被轮换时至少保留最近检查点。"""
    结果目录.mkdir(parents=True, exist_ok=True)
    tmp = 结果文件.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, 结果文件)


def 结果骨架(t0, 状态):
    return {
        "路线名": 路线名,
        "状态": 状态,
        "核心指标": {
            "键": "四附件遮挡验证NRMSE_材料等权",
            "值": None,
            "方向": "越小越优",
        },
        "用时估计秒": 预计用时秒,
        "实际用时秒": round(用时(t0), 6),
        "口径说明": (
            "附件3/4各取1200至2600 cm-1内三个相隔的512点连续窗口，附件1/2各取"
            "同位置一个512点窗口；每窗按交替64点块分为拟合块和遮挡验证块。双角共享"
            "基本波厚度参数和材料线性色散参数，相位含Snell角度修正。高阶能量同时超过"
            "两角分块置换95%阈值且相移不变指纹复现时用K=4，否则用K=1。每附件NRMSE"
            "以验证反射率5%至95%分位距归一化；先材料内平均，再对两材料等权平均。"
            "该原型只输出这一个可比指标，不形成正式厚度结论。"
        ),
    }


def 读取附件(文件名):
    df = pd.read_excel(
        数据目录 / 文件名, sheet_name="Sheet1", header=0, engine="openpyxl"
    ).rename(columns={"波数 (cm-1)": "波数", "反射率 (%)": "反射率"})
    if not {"波数", "反射率"}.issubset(df.columns):
        raise ValueError(f"{文件名}字段与数据档案不一致: {list(df.columns)}")
    return df.loc[
        (df["波数"] >= 1200.0) & (df["波数"] <= 2600.0), ["波数", "反射率"]
    ].reset_index(drop=True)


def 截窗(df, 中心):
    sigma = df["波数"].to_numpy(dtype=float)
    middle = int(np.argmin(np.abs(sigma - 中心)))
    start = max(0, min(len(df) - 窗口点数, middle - 窗口点数 // 2))
    w = df.iloc[start:start + 窗口点数].reset_index(drop=True)
    if len(w) != 窗口点数:
        raise ValueError(f"中心{中心} cm-1无法取得{窗口点数}点连续窗口")
    return w


def 构造面板():
    panels = []
    for material, cfg in 材料配置.items():
        for filename, angle in cfg["附件"]:
            df = 读取附件(filename)
            for window_id, center in enumerate(cfg["窗口中心"]):
                w = 截窗(df, center)
                index = np.arange(窗口点数)
                train = ((index // 块长) % 2) == 0
                panels.append({
                    "材料": material,
                    "文件": filename,
                    "角度": angle,
                    "窗口序号": window_id,
                    "波数": w["波数"].to_numpy(dtype=float),
                    "反射率": w["反射率"].to_numpy(dtype=float),
                    "拟合掩码": train,
                    "验证掩码": ~train,
                    "参考折射率": cfg["参考折射率"],
                })
    return panels


def 相位(panel, d_um, slope):
    sigma = panel["波数"]
    # 原型低维色散：n(sigma)=n0+slope*(sigma-1900)/700。
    n = panel["参考折射率"] + slope * (sigma - 1900.0) / 700.0
    if np.min(n) <= 1.0:
        raise ValueError("候选色散使折射率不满足n>1")
    theta = math.radians(panel["角度"])
    # phi=4*pi*d*sigma*sqrt(n^2-sin^2(theta0))，d由um换算为cm。
    return 4.0 * math.pi * d_um * 1e-4 * sigma * np.sqrt(
        n * n - math.sin(theta) ** 2
    )


def 设计矩阵(panel, d_um, slope, K):
    sigma = panel["波数"]
    z = (sigma - np.mean(sigma)) / max(np.ptp(sigma), 1e-12)
    phi = 相位(panel, d_um, slope)
    columns = [np.ones_like(z), z, z * z]
    for k in range(1, K + 1):
        columns.extend([np.cos(k * phi), np.sin(k * phi)])
    return np.column_stack(columns)


def 拟合(panel, d_um, slope, K, y_override=None):
    X = 设计矩阵(panel, d_um, slope, K)
    mask = panel["拟合掩码"]
    y = panel["反射率"] if y_override is None else y_override
    coef, _, _, _ = np.linalg.lstsq(X[mask], y[mask], rcond=None)
    return coef, X @ coef


def 稳健尺度(y):
    scale = float(np.quantile(y, 0.95) - np.quantile(y, 0.05))
    return scale if scale > 1e-12 else max(float(np.std(y)), 1.0)


def K1目标(panels, d_um, slope):
    losses = []
    for panel in panels:
        _, pred = 拟合(panel, d_um, slope, K=1)
        mask = panel["拟合掩码"]
        losses.append(np.mean(
            ((panel["反射率"][mask] - pred[mask]) / 稳健尺度(panel["反射率"][mask])) ** 2
        ))
    return float(np.mean(losses))


def 搜索共享相位(material, panels, t0, checkpoint):
    lo, hi = 材料配置[material]["斜率范围"]
    d_grid = np.linspace(2.0, 250.0, 140)
    b_grid = np.linspace(lo, hi, 7)
    best = None
    evaluated = 0
    for slope in b_grid:
        for d_um in d_grid:
            loss = K1目标(panels, float(d_um), float(slope))
            evaluated += 1
            if best is None or loss < best[0]:
                best = (loss, float(d_um), float(slope))
            if evaluated % 20 == 0 and 用时(t0) >= 搜索截止秒:
                break
        if 用时(t0) >= 搜索截止秒:
            break
    if best is None:
        raise RuntimeError(f"{material}搜索没有产生候选")

    if 用时(t0) < 搜索截止秒:
        d_step, b_step = float(d_grid[1] - d_grid[0]), float(b_grid[1] - b_grid[0])
        fine_d = np.linspace(max(0.5, best[1] - 1.5 * d_step), best[1] + 1.5 * d_step, 70)
        fine_b = np.linspace(max(lo, best[2] - b_step), min(hi, best[2] + b_step), 7)
        for slope in fine_b:
            for d_um in fine_d:
                loss = K1目标(panels, float(d_um), float(slope))
                if loss < best[0]:
                    best = (loss, float(d_um), float(slope))
                if 用时(t0) >= 搜索截止秒:
                    break
            if 用时(t0) >= 搜索截止秒:
                break

    checkpoint["状态"] = f"已完成{material}基本波共享相位搜索"
    checkpoint["实际用时秒"] = round(用时(t0), 6)
    刷新(checkpoint)
    return best[1], best[2]


def 高阶比(coef):
    energies = []
    for k in range(1, 5):
        a, b = coef[3 + 2 * (k - 1):3 + 2 * k]
        energies.append(float(a * a + b * b))
    return float(sum(energies[1:]) / max(sum(energies), 1e-15))


def 相移不变指纹(coef):
    c = []
    for k in range(1, 5):
        a, b = coef[3 + 2 * (k - 1):3 + 2 * k]
        c.append(complex(a, -b))
    phase1 = np.angle(c[0])
    v = []
    for k in range(2, 5):
        ck = c[k - 1] * np.exp(-1j * k * phase1)
        v.extend([ck.real, ck.imag])
    v = np.asarray(v, dtype=float)
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 1e-15 else np.zeros_like(v)


def 置换阈值(panel, d_um, slope, pred1, t0):
    residual = panel["反射率"] - pred1
    blocks = [
        np.arange(b * 块长, (b + 1) * 块长)
        for b in range(窗口点数 // 块长) if b % 2 == 0
    ]
    null = []
    for order in itertools.permutations(range(len(blocks))):
        if 用时(t0) >= 软截止秒:
            break
        y_null = pred1.copy()
        for target, source_id in zip(blocks, order):
            y_null[target] += residual[blocks[source_id]]
        coef, _ = 拟合(panel, d_um, slope, K=4, y_override=y_null)
        null.append(高阶比(coef))
    # 少于8次时不再冒险触发修正，保证超时降级仍可给出指标。
    return (float(np.quantile(null, 0.95)), len(null)) if len(null) >= 8 else (float("inf"), len(null))


def 谐波诊断(material, panels, d_um, slope, t0):
    fits = {}
    for panel in panels:
        _, pred1 = 拟合(panel, d_um, slope, K=1)
        coef4, pred4 = 拟合(panel, d_um, slope, K=4)
        threshold, n_perm = 置换阈值(panel, d_um, slope, pred1, t0)
        fits[(panel["文件"], panel["窗口序号"])] = {
            "panel": panel,
            "pred1": pred1,
            "pred4": pred4,
            "ratio": 高阶比(coef4),
            "threshold": threshold,
            "fingerprint": 相移不变指纹(coef4),
            "置换次数": n_perm,
        }

    file10, file15 = [x[0] for x in 材料配置[material]["附件"]]
    triggers = {}
    for wid in range(len(材料配置[material]["窗口中心"])):
        r10, r15 = fits[(file10, wid)], fits[(file15, wid)]
        repeat = float(np.dot(r10["fingerprint"], r15["fingerprint"]))
        significant = r10["ratio"] > r10["threshold"] and r15["ratio"] > r15["threshold"]
        triggers[wid] = bool(significant and repeat >= 0.50)
    return fits, triggers


def 汇总指标(all_fits, all_triggers):
    attachment_scores = {}
    for material, cfg in 材料配置.items():
        for filename, _ in cfg["附件"]:
            ys, preds = [], []
            for wid in range(len(cfg["窗口中心"])):
                r = all_fits[material][(filename, wid)]
                panel, mask = r["panel"], r["panel"]["验证掩码"]
                pred = r["pred4"] if all_triggers[material][wid] else r["pred1"]
                ys.append(panel["反射率"][mask])
                preds.append(pred[mask])
            y, pred = np.concatenate(ys), np.concatenate(preds)
            attachment_scores[filename] = float(
                np.sqrt(np.mean((y - pred) ** 2)) / 稳健尺度(y)
            )
    si = np.mean([attachment_scores["附件3.xlsx"], attachment_scores["附件4.xlsx"]])
    sic = np.mean([attachment_scores["附件1.xlsx"], attachment_scores["附件2.xlsx"]])
    return float(np.mean([si, sic]))


def main():
    t0 = time.perf_counter()
    checkpoint = 结果骨架(t0, "初始化")
    刷新(checkpoint)
    try:
        panels = 构造面板()
        checkpoint["状态"] = "真数据小样已读取并完成交替64点块切分"
        checkpoint["实际用时秒"] = round(用时(t0), 6)
        刷新(checkpoint)

        all_fits, all_triggers, audit = {}, {}, {}
        for material in ("硅", "碳化硅"):
            material_panels = [p for p in panels if p["材料"] == material]
            d_um, slope = 搜索共享相位(material, material_panels, t0, checkpoint)
            fits, triggers = 谐波诊断(material, material_panels, d_um, slope, t0)
            all_fits[material], all_triggers[material] = fits, triggers
            audit[material] = {
                "窗口数": len(triggers),
                "触发窗口数": int(sum(triggers.values())),
                "判定": "触发K=4重构" if any(triggers.values()) else "保持K=1基本波重构",
                "置换规则": "每窗4个拟合块全排列，最多24次，取高阶能量比95%分位阈值",
            }
            checkpoint["状态"] = f"已完成{material}谐波诊断"
            checkpoint["实际用时秒"] = round(用时(t0), 6)
            刷新(checkpoint)

        score = 汇总指标(all_fits, all_triggers)
        final = 结果骨架(t0, "完成")
        final["核心指标"]["值"] = round(score, 10)
        final["触发审计"] = audit
        final["实际用时秒"] = round(用时(t0), 6)
        final["时间预算状态"] = "未触及软截止" if 用时(t0) < 软截止秒 else "已主动收敛"
        刷新(final)
        print(
            f"{路线名}完成：四附件遮挡验证NRMSE_材料等权={score:.6f}，"
            f"实际用时={用时(t0):.2f}秒。"
        )
    except Exception as exc:
        failed = 结果骨架(t0, "失败但已保留检查点")
        failed["错误"] = f"{type(exc).__name__}: {exc}"
        failed["实际用时秒"] = round(用时(t0), 6)
        刷新(failed)
        raise


if __name__ == "__main__":
    main()
