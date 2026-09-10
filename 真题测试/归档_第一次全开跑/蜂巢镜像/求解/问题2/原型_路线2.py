import sys

sys.path.insert(0, "/tmp/蜂巢/pylibs")

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.ndimage import median_filter
from scipy.signal import find_peaks, hilbert, savgol_filter


路线名 = "Q2-B 双角相位同步—分段稳健回归"
核心指标键 = "验证块峰谷位置MdAE_cm-1"
根目录 = Path(__file__).resolve().parents[2]
结果路径 = 根目录 / "求解" / "问题2" / "原型结果" / "路线2.json"
数据路径 = [根目录 / "数据" / "附件1.xlsx", 根目录 / "数据" / "附件2.xlsx"]
入射角_度 = [10.0, 15.0]

波数下限 = 1200.0
波数上限 = 2400.0
最大样本数 = 1800
验证块下限 = 1680.0
验证块上限 = 1920.0  # 中央20%连续遮挡，两侧为拟合块
时间预算_秒 = 170.0
主动收敛阈值_秒 = 155.0
预计用时_秒 = 90


def 写结果(结果):
    """原子替换结果JSON，使中途阶段也已落盘。"""
    结果路径.parent.mkdir(parents=True, exist_ok=True)
    临时路径 = 结果路径.with_suffix(".json.tmp")
    with 临时路径.open("w", encoding="utf-8") as f:
        json.dump(结果, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
    临时路径.replace(结果路径)


def 检查预算(t0, 结果, 阶段):
    已用时 = time.perf_counter() - t0
    结果["实际用时秒"] = round(已用时, 3)
    结果["当前阶段"] = 阶段
    写结果(结果)
    if 已用时 >= 时间预算_秒:
        raise TimeoutError(f"原型运行超过{时间预算_秒:.0f}秒预算，已保留当前落盘结果。")


def 读取同步小样():
    数据表 = []
    for path in 数据路径:
        df = pd.read_excel(path, sheet_name="Sheet1", header=0, engine="openpyxl")
        df = df.rename(columns={"波数 (cm-1)": "波数_cm-1", "反射率 (%)": "反射率_百分比"})
        if not {"波数_cm-1", "反射率_百分比"}.issubset(df.columns):
            raise ValueError(f"{path.name}的列名与数据档案不一致。")
        df = df.loc[
            df["波数_cm-1"].between(波数下限, 波数上限, inclusive="both"),
            ["波数_cm-1", "反射率_百分比"],
        ].reset_index(drop=True)
        数据表.append(df)

    x0 = 数据表[0]["波数_cm-1"].to_numpy(dtype=float)
    x1 = 数据表[1]["波数_cm-1"].to_numpy(dtype=float)
    if len(x0) != len(x1) or not np.array_equal(x0, x1):
        raise ValueError("附件1/2未能按公共波数键严格同步。")
    if len(x0) < 300:
        raise ValueError("指定波数区间内样本过少。")

    if len(x0) > 最大样本数:
        # 两角度使用完全相同的原始行索引，不对反射率做平均。
        idx = np.unique(np.rint(np.linspace(0, len(x0) - 1, 最大样本数)).astype(int))
        数据表 = [df.iloc[idx].reset_index(drop=True) for df in 数据表]

    x = 数据表[0]["波数_cm-1"].to_numpy(dtype=float)
    y = [df["反射率_百分比"].to_numpy(dtype=float) for df in 数据表]
    return x, y


def 奇数窗口(目标, n, 下限=9):
    w = int(min(目标, n - 1 if n % 2 == 0 else n))
    w = max(int(下限), w)
    if w % 2 == 0:
        w -= 1
    if w >= n:
        w = n - 1 if n % 2 == 0 else n
    return max(5, int(w))


def 加权中位数(values, weights):
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    order = np.argsort(values)
    values, weights = values[order], weights[order]
    cutoff = 0.5 * np.sum(weights)
    return float(values[np.searchsorted(np.cumsum(weights), cutoff, side="left")])


def 块内解析相位(x, y):
    """每个连续拟合块独立去基线和Hilbert变换，不读取中央验证块。"""
    n = len(x)
    if n < 80:
        raise ValueError("拟合块过短，无法稳定提取解析相位。")

    基线窗 = 奇数窗口(max(101, n // 3), n)
    baseline = median_filter(y, size=基线窗, mode="nearest")
    centered = y - baseline
    振幅窗 = 奇数窗口(max(41, n // 10), n)
    power = savgol_filter(centered**2, 振幅窗, polyorder=2, mode="interp")
    amp = np.sqrt(np.maximum(power, np.finfo(float).eps))
    normalized = centered / np.maximum(amp, np.quantile(amp, 0.20) * 0.25)
    phase = np.unwrap(np.angle(hilbert(normalized)))

    trim = max(18, int(round(0.06 * n)))
    keep = np.arange(trim, n - trim)
    xk, pk, ak = x[keep], phase[keep], amp[keep]
    if np.median(np.diff(pk)) < 0:
        pk = -pk

    grad = np.gradient(pk, xk)
    positive_grad = grad[grad > 0]
    grad_ref = np.median(positive_grad) if positive_grad.size else 1.0
    amp_ref = max(np.quantile(ak, 0.70), np.finfo(float).eps)
    amp_weight = np.clip(ak / amp_ref, 0.05, 1.0)
    monotone_weight = np.where(grad >= 0.15 * grad_ref, 1.0, 0.12)
    edge_weight = np.sin(np.linspace(0.15, np.pi - 0.15, len(xk))) ** 2
    weight = np.clip(amp_weight * monotone_weight * edge_weight, 0.01, 1.0)
    return {"x": xk, "phase": pk, "weight": weight}


def 初始整周对齐(left, right):
    dl = np.diff(left["phase"]) / np.diff(left["x"])
    dr = np.diff(right["phase"]) / np.diff(right["x"])
    slope = np.median(np.r_[dl[dl > 0], dr[dr > 0]])
    expected = left["phase"][-1] + slope * (right["x"][0] - left["x"][-1])
    return int(np.rint((expected - right["phase"][0]) / (2.0 * np.pi)))


def 设计矩阵(x, angle_deg, sigma_center, sigma_scale):
    """
    用n_ref=2.6的一阶展开构造角度修正与二阶色散基：
    sqrt(n(sigma)^2-sin(theta)^2) 约于 h_theta + n_ref/h_theta*(c1*z+c2*z^2)。
    共享的3个系数分别对应光学厚度主项与两个色散修正项。
    """
    n_ref = 2.6
    z = (x - sigma_center) / sigma_scale
    h = np.sqrt(n_ref**2 - np.sin(np.deg2rad(angle_deg)) ** 2)
    return np.column_stack(
        [
            x * h / 1000.0,
            x * (n_ref / h) * z / 1000.0,
            x * (n_ref / h) * z**2 / 1000.0,
        ]
    )


def 稳健线性拟合(X, y, base_weight, 最大迭代=12):
    w = np.clip(np.asarray(base_weight, dtype=float), 1e-4, None)
    beta = np.zeros(X.shape[1], dtype=float)
    for _ in range(最大迭代):
        sw = np.sqrt(w)
        new_beta = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)[0]
        residual = y - X @ new_beta
        scale = 1.4826 * np.median(np.abs(residual - np.median(residual))) + 1e-9
        u = np.abs(residual) / (1.345 * scale)
        huber_weight = np.ones_like(u)
        mask = u > 1.0
        huber_weight[mask] = 1.0 / u[mask]
        w = np.clip(base_weight * huber_weight, 1e-4, None)
        if np.max(np.abs(new_beta - beta)) < 1e-8 * (1.0 + np.max(np.abs(beta))):
            beta = new_beta
            break
        beta = new_beta
    residual = y - X @ beta
    score = 加权中位数(np.abs(residual), base_weight)
    return beta, score


def 联合相位拟合(分块相位, sigma_center, sigma_scale, t0, 结果):
    base_shifts = [初始整周对齐(blocks["left"], blocks["right"]) for blocks in 分块相位]
    best = None
    soft_best = None
    候选偏移 = [range(k - 2, k + 3) for k in base_shifts]

    for shift10 in 候选偏移[0]:
        for shift15 in 候选偏移[1]:
            rows, targets, weights = [], [], []
            for angle_id, (angle, blocks, shift) in enumerate(
                zip(入射角_度, 分块相位, [shift10, shift15])
            ):
                for segment in ("left", "right"):
                    block = blocks[segment]
                    physical = 设计矩阵(block["x"], angle, sigma_center, sigma_scale)
                    intercept = np.zeros((len(block["x"]), 2))
                    intercept[:, angle_id] = 1.0
                    rows.append(np.column_stack([intercept, physical]))
                    phase = block["phase"].copy()
                    if segment == "right":
                        phase += 2.0 * np.pi * shift
                    targets.append(phase)
                    weights.append(block["weight"])

            X = np.vstack(rows)
            y = np.concatenate(targets)
            w = np.concatenate(weights)
            beta, score = 稳健线性拟合(X, y, w)

            # 单调约束只检查实际外推的验证块。全区间严格逐点检查会把
            # 拟合块边缘的轻微二次色散回摆误判为整个候选失败。
            grid = np.linspace(验证块下限, 验证块上限, 160)
            monotone = True
            violation = 0.0
            for angle_id, angle in enumerate(入射角_度):
                phi = beta[angle_id] + 设计矩阵(grid, angle, sigma_center, sigma_scale) @ beta[2:]
                dphi = np.diff(phi)
                step_ref = max(float(np.median(np.abs(dphi))), 1e-10)
                step_floor = 1e-4 * step_ref
                monotone &= bool(phi[-1] > phi[0] and np.all(dphi > step_floor))
                violation += float(np.mean(np.maximum(step_floor - dphi, 0.0)))
            if monotone and (best is None or score < best["score"]):
                best = {"beta": beta, "score": score, "shifts": [shift10, shift15]}

            # 保留软约束后备：若Hilbert相位只在验证块有局部小回摆，
            # 不应让25个整周候选全部空缺。惩罚与相位残差同量纲。
            penalized_score = score + 20.0 * violation
            if soft_best is None or penalized_score < soft_best["penalized_score"]:
                soft_best = {
                    "beta": beta,
                    "score": score,
                    "penalized_score": penalized_score,
                    "monotone_violation": violation,
                    "shifts": [shift10, shift15],
                }

            if time.perf_counter() - t0 >= 主动收敛阈值_秒:
                if best is not None:
                    结果["状态"] = "接近时间预算，已主动收敛到当前最优整周对齐"
                    return best
                if soft_best is not None:
                    结果["状态"] = "接近时间预算，已保留当前单调违约最小解"
                    结果["单调违约量"] = round(
                        float(soft_best["monotone_violation"]), 10
                    )
                    return soft_best

    if best is None:
        if soft_best is None:
            raise RuntimeError("整周对齐未产生可用拟合候选。")
        best = soft_best
        结果["状态"] = "未获得严格单调候选，已按验证块单调违约惩罚选择最优解"
        结果["单调违约量"] = round(float(best["monotone_violation"]), 10)
    return best


def 局部二次定位(x, y, indices):
    positions = []
    for i in indices:
        if i <= 0 or i >= len(x) - 1:
            continue
        coef = np.polyfit(x[i - 1 : i + 2], y[i - 1 : i + 2], deg=2)
        if abs(coef[0]) < 1e-14:
            positions.append(float(x[i]))
            continue
        vertex = -coef[1] / (2.0 * coef[0])
        lo, hi = x[i - 1], x[i + 1]
        positions.append(float(vertex if lo <= vertex <= hi else x[i]))
    return np.asarray(positions, dtype=float)


def 固定评估器峰谷(x, y):
    """评估器只在拟合完成后读取验证块，规则不随附件调参。"""
    x_scaled = (x - np.mean(x)) / max(np.ptp(x) / 2.0, 1.0)
    baseline = np.polyval(np.polyfit(x_scaled, y, deg=2), x_scaled)
    residual = y - baseline
    window = 奇数窗口(31, len(x))
    smooth = savgol_filter(residual, window, polyorder=3, mode="interp")
    diff_median = np.median(np.diff(smooth))
    noise = 1.4826 * np.median(np.abs(np.diff(smooth) - diff_median))
    prominence = max(0.10 * float(np.std(smooth)), 1.5 * float(noise), 1e-8)
    peaks, _ = find_peaks(smooth, prominence=prominence, distance=8)
    valleys, _ = find_peaks(-smooth, prominence=prominence, distance=8)
    if len(peaks) == 0:
        peaks, _ = find_peaks(smooth, distance=8)
    if len(valleys) == 0:
        valleys, _ = find_peaks(-smooth, distance=8)
    return 局部二次定位(x, smooth, peaks), 局部二次定位(x, smooth, valleys)


def 预测相位极值(x, angle_id, angle, beta, sigma_center, sigma_scale):
    dense_x = np.linspace(float(x[0]), float(x[-1]), 5000)
    phi = beta[angle_id] + 设计矩阵(dense_x, angle, sigma_center, sigma_scale) @ beta[2:]
    if np.any(np.diff(phi) <= 0):
        # 只对软约束后备解的局部回摆作最小单调包络修正，
        # 保证 np.interp 的自变量严格递增，不改变峰谷半周阶次口径。
        phi = np.maximum.accumulate(phi)
        eps_step = max(float(np.ptp(phi)), 1.0) * 1e-12
        phi = phi + eps_step * np.arange(len(phi))
    k_min = int(np.ceil(phi[0] / np.pi))
    k_max = int(np.floor(phi[-1] / np.pi))
    if k_max < k_min:
        return np.array([]), np.array([])
    k = np.arange(k_min, k_max + 1)
    positions = np.interp(k * np.pi, phi, dense_x)
    peaks = positions[k % 2 == 0]
    valleys = positions[k % 2 != 0]
    return peaks, valleys


def 最近位置误差(observed, predicted):
    if len(observed) == 0 or len(predicted) == 0:
        return np.array([], dtype=float)
    return np.min(np.abs(observed[:, None] - predicted[None, :]), axis=1)


def main():
    t0 = time.perf_counter()
    结果 = {
        "路线名": 路线名,
        "核心指标键值": {
            "指标名": "连续遮挡验证块峰谷位置MdAE",
            "值": None,
            "单位": "cm-1",
            "方向": "越小越优",
        },
        "用时估计": {
            "路线侦察预计秒": 预计用时_秒,
            "脚本设计上限秒": int(时间预算_秒),
        },
        "口径说明": (
            "附件1/2各取1200–2400 cm-1真实数据，用相同原始行索引下采样至最多1800点；"
            "1680–1920 cm-1中央连续块完全遮挡，两侧块独立去基线、振幅归一与Hilbert展开，"
            "以低振幅及边界权重做双角共享光学厚度/色散基稳健回归；"
            "验证块仅由拟合相位外推极值，与固定平滑+局部二次定位的实测峰谷比较；"
            "合并两附件全部同类峰谷的最近位置绝对误差取中位数，越小越优。"
        ),
        "实际用时秒": 0.0,
        "状态": "已初始化",
    }
    写结果(结果)

    try:
        x, y_by_angle = 读取同步小样()
        结果["状态"] = "真数据小样已读取并同步"
        检查预算(t0, 结果, "数据读取")

        n = len(x)
        i0 = int(np.searchsorted(x, 验证块下限, side="left"))
        i1 = int(np.searchsorted(x, 验证块上限, side="right"))
        if min(i0, n - i1, i1 - i0) < 80:
            raise ValueError("拟合/验证分块点数不足。")

        分块相位 = []
        for y in y_by_angle:
            分块相位.append(
                {
                    "left": 块内解析相位(x[:i0], y[:i0]),
                    "right": 块内解析相位(x[i1:], y[i1:]),
                }
            )
        结果["状态"] = "两角度分块解析相位已提取"
        检查预算(t0, 结果, "相位提取")

        sigma_center = 0.5 * (波数下限 + 波数上限)
        sigma_scale = 0.5 * (波数上限 - 波数下限)
        best = 联合相位拟合(分块相位, sigma_center, sigma_scale, t0, 结果)
        结果["状态"] = "双角共享参数稳健拟合已完成"
        检查预算(t0, 结果, "联合稳健回归")

        validation_x = x[i0:i1]
        all_errors = []
        for angle_id, (angle, y) in enumerate(zip(入射角_度, y_by_angle)):
            observed_peaks, observed_valleys = 固定评估器峰谷(validation_x, y[i0:i1])
            predicted_peaks, predicted_valleys = 预测相位极值(
                validation_x, angle_id, angle, best["beta"], sigma_center, sigma_scale
            )
            all_errors.extend(最近位置误差(observed_peaks, predicted_peaks).tolist())
            all_errors.extend(最近位置误差(observed_valleys, predicted_valleys).tolist())

        if not all_errors:
            raise RuntimeError("验证块未获得可比的同类峰谷位置。")
        mdae = float(np.median(np.asarray(all_errors, dtype=float)))
        结果["核心指标键值"]["值"] = round(mdae, 6)
        结果["状态"] = "完成"
        结果["当前阶段"] = "单一指标评估"
        结果["实际用时秒"] = round(time.perf_counter() - t0, 3)
        写结果(结果)
        print(
            f"{路线名}：{核心指标键}={mdae:.6f} cm-1，"
            f"实际用时{结果['实际用时秒']:.3f}秒。"
        )
    except Exception as exc:
        结果["状态"] = "失败或预算主动中止"
        结果["错误"] = f"{type(exc).__name__}: {exc}"
        结果["实际用时秒"] = round(time.perf_counter() - t0, 3)
        写结果(结果)
        print(f"{路线名}未完成：{结果['错误']}")
        raise


if __name__ == "__main__":
    main()
