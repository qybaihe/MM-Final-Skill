"""连续相位拟厚的小样原型；本文件交付时仅作静态检查，不运行反演。

运行：python3 求解/问题1/原型_路线3.py
唯一评分：全部24例的100*mean(abs(估计厚度-真厚度)/真厚度)。
实测附件1、2各480点只作条件性核对，不具有厚度真值，不进入评分。

两束场给出R=B+A*cos(4*pi*d*σ*q+ψ)，q=sqrt(n²-sin²θ)。
以原始采样位置上的σ*q构造载波，局部联合拟合二次基线及正余弦。
若正余弦系数为C、S，残余相位为atan2(-S,C)，加回已知载波后
得到φ=4*pi*d*σ*q+ψ。仅在连续可靠片段内解缠，用两角度独立截距
消去整体2*pi整数，联合回归的斜率乘10000/(4*pi)得到微米厚度。
不差分噪声相位，不插补低幅值相位，不把粗频率最大点当厚度答案。
"""

import bisect
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import posixpath
import random
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "求解/问题1/原型结果/路线3.json"
METRIC = "合成厚度平均相对误差_%"
SOFT_SECONDS = 165.0
HARD_SECONDS = 175.0
THICKNESS_LIMITS = (0.5, 40.0)
CARRIER_COUNT = 64
CANDIDATE_COUNT = 3
ANGLE_DEGREES = (10, 15)
NAMESPACE = {"表": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
RELATIONSHIP = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


class TimeBudgetExceeded(Exception):
    pass


class Budget:
    def __init__(self, started):
        self.started = started

    def check(self):
        if time.monotonic() - self.started >= SOFT_SECONDS:
            raise TimeBudgetExceeded("达到165秒软截止；保留已完成案例，不以部分均值评分")


def write_json(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(content, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def save_result(result, started):
    result["实际用时秒"] = time.monotonic() - started
    result["耗时秒"] = result["实际用时秒"]
    write_json(OUTPUT, result)


def initial_result():
    return {
        "问题": 1,
        "路线编号": "一丙",
        "路线名": "连续相位拟厚",
        "运行状态": "已启动",
        "核心指标键值": {METRIC: None},
        "主指标名称": "合成厚度平均相对误差",
        "主指标值": None,
        "主指标单位": "%",
        "指标含义": {METRIC: "24例相对绝对厚度误差的等权均值乘100，越小越好"},
        "用时估计": {
            "性质": "设计预算，交付时未运行，不是实测耗时保证",
            "读取与生成秒": 15,
            "粗载频与解调秒": 90,
            "解缠回归与核对秒": 45,
            "汇总写出秒": 15,
            "软截止秒": SOFT_SECONDS,
            "父进程截止秒": HARD_SECONDS,
        },
        "口径说明": {
            "评分公式": "100*sum(abs(估计厚度-合成真厚度)/合成真厚度)/24",
            "缺失规则": "任一例没有有限正厚度则评分为null；保留所有24例，不按成功子集取平均",
            "真实数据用途": "附件1、2提供真实坐标及实测480点反射率；实测条件厚度不参与合成评分",
            "真值隔离": "求解器只接收坐标、双角反射率及已知折射率曲线；真厚度仅用于生成与评分",
            "坐标对照": "原坐标与端点等距近似分别解相位；近似时仅替换σ及其解析n(σ)，反射率不插值",
            "初始化": "64个厚度等效载频在各原坐标σ*q上投影，保留3个候选；频率格不是仪器分辨率",
            "数值搜索盒": "0.5至40微米仅为有限搜索盒，不是材料物性范围或置信区间",
            "光学情景": "实测n=2.4、3.4只借用共同合成情景作条件性核对，不称为碳化硅实测折射率",
            "连续性": "低幅值位置保留不可靠标记；不跨缺口解缠，每角度仅使用跨度最大的可靠连通片段",
            "局部窗口": "仅对问题一完整小样操作；半窗1.25个载波周期且不少于6个坐标间隔，舍弃截短边窗",
            "验证边界": "问题一无留段预测任务；不使用问题二、三的训练校准测试折分，不生成预测区间",
            "不确定性": "报告近优载频候选及实测折射率情景敏感性，不把这些离散值解释为统计置信区间",
            "失败判定": "连续相位不足、近优厚度歧义均显式标记；有限主候选仍报数值评分且不隐去失败诊断",
            "方法卡片取舍": "不构造区间、不做因素归因，不使用共形、树模型或随机按行验证",
        },
        "输入哈希": {},
        "样本索引": {},
        "折分": "不适用：问题一的24个固定合成案例，不以实测谱段误差评分",
        "使用附件": ["数据/附件1.xlsx", "数据/附件2.xlsx"],
        "参数情景": {},
        "随机种子": 20260909,
        "合成逐例": [],
        "端点等距对照": [],
        "实测小样": [],
        "厚度及单位": "逐例及实测情景的厚度_微米字段；实测值条件于假定折射率",
        "分块预测与残差": None,
        "经验覆盖率": None,
        "覆盖率说明": "未构造任何统计区间，经验覆盖率不适用，不虚填0或100%",
        "辅助诊断": {},
        "失败原因": [],
    }


def sample_indices(total, count):
    if total < count or count < 2:
        raise ValueError("样本点数不满足共同抽样规定")
    return [index * (total - 1) // (count - 1) for index in range(count)]


def read_spectrum(path, expected_hash, budget):
    budget.check()
    actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(f"附件与数据档案哈希不符：{path.name}")
    with zipfile.ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.get("Id"): item.get("Target") for item in relationships}
        sheets = workbook.findall("表:sheets/表:sheet", NAMESPACE)
        selected = [sheet for sheet in sheets if sheet.get("name") == "Sheet1"]
        if len(selected) != 1:
            raise ValueError("需要唯一Sheet1")
        target = targets[selected[0].get(RELATIONSHIP)]
        member = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            strings = ["".join(item.itertext()) for item in shared]
        sheet_root = ET.fromstring(archive.read(member))
    records = []
    for row in sheet_root.findall("表:sheetData/表:row", NAMESPACE):
        row_number = int(row.get("r"))
        if row_number % 256 == 0:
            budget.check()
        values = {}
        for cell in row.findall("表:c", NAMESPACE):
            address = cell.get("r")
            column = address.rstrip("0123456789")
            if column not in ("A", "B"):
                continue
            if cell.find("表:f", NAMESPACE) is not None:
                raise ValueError("原始数值列不得含公式")
            text = cell.findtext("表:v", namespaces=NAMESPACE)
            if cell.get("t") == "s":
                value = strings[int(text)]
            elif cell.get("t") == "inlineStr":
                value = "".join(cell.find("表:is", NAMESPACE).itertext())
            else:
                value = float(text)
            values[column] = value
        if row_number == 1:
            if [values.get("A"), values.get("B")] != ["波数 (cm-1)", "反射率 (%)"]:
                raise ValueError("表头与数据档案不符")
            continue
        sigma, percent = values["A"], values["B"]
        if not math.isfinite(sigma) or not math.isfinite(percent):
            raise ValueError("不允许静默丢弃非有限观测")
        records.append((row_number, sigma, percent / 100.0))
    if len(records) != 7469 or any(current[1] <= previous[1] for previous, current in zip(records, records[1:])):
        raise ValueError("原始点数或单调性与共同协议不符")
    return records, actual_hash


def solve_system(matrix, target):
    size = len(target)
    augmented = [list(row) + [value] for row, value in zip(matrix, target)]
    scale = max(abs(value) for row in matrix for value in row)
    if scale <= 0.0:
        return None
    for column in range(size):
        pivot = max(range(column, size), key=lambda index: abs(augmented[index][column]))
        if abs(augmented[pivot][column]) <= scale * 1e-11:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        for entry in range(column, size + 1):
            augmented[column][entry] /= divisor
        for row_index in range(size):
            if row_index == column:
                continue
            multiplier = augmented[row_index][column]
            for entry in range(column, size + 1):
                augmented[row_index][entry] -= multiplier * augmented[column][entry]
    return [row[-1] for row in augmented]


def harmonic_fit(sigma, values, optical, thickness, indices, center, radius):
    matrix = [[0.0] * 5 for _ in range(5)]
    target = [0.0] * 5
    stored = []
    for index in indices:
        coordinate = (sigma[index] - center) / radius
        weight = max(0.0, 1.0 - 0.9 * coordinate * coordinate)
        phase = 4.0 * math.pi * thickness * optical[index] / 10000.0
        columns = (1.0, coordinate, coordinate * coordinate, math.cos(phase), math.sin(phase))
        value = values[index]
        for row_index in range(5):
            weighted = weight * columns[row_index]
            target[row_index] += weighted * value
            for column in range(row_index, 5):
                matrix[row_index][column] += weighted * columns[column]
        stored.append((columns, weight, value))
    for row_index in range(5):
        for column in range(row_index):
            matrix[row_index][column] = matrix[column][row_index]
    coefficients = solve_system(matrix, target)
    if coefficients is None:
        return None
    residual = sum(weight * (value - sum(coefficient * column for coefficient, column in zip(coefficients, columns))) ** 2 for columns, weight, value in stored)
    return coefficients, residual / max(sum(item[1] for item in stored), 1e-12)


def optical_coordinates(sigma, refractive, angle):
    sine_square = math.sin(math.radians(angle)) ** 2
    if any(index_value * index_value <= sine_square for index_value in refractive):
        raise ValueError("折射率不满足透明传播条件")
    optical = [coordinate * math.sqrt(index_value * index_value - sine_square) for coordinate, index_value in zip(sigma, refractive)]
    if any(current <= previous for previous, current in zip(optical, optical[1:])):
        raise ValueError("本原型仅接受单调的σ*q相位坐标")
    return optical


def coarse_carriers(sigma, spectra, optical_sets, budget):
    low, high = THICKNESS_LIMITS
    center = (sigma[0] + sigma[-1]) / 2.0
    radius = (sigma[-1] - sigma[0]) / 2.0
    scored = []
    for index in range(CARRIER_COUNT):
        budget.check()
        thickness = low + (high - low) * index / (CARRIER_COUNT - 1)
        fits = [harmonic_fit(sigma, values, optical, thickness, range(len(sigma)), center, radius) for values, optical in zip(spectra, optical_sets)]
        loss = sum(fit[1] for fit in fits) if all(fit is not None for fit in fits) else math.inf
        scored.append((loss, thickness, index))
    minima = [entry for index, entry in enumerate(scored) if (index == 0 or entry[0] <= scored[index - 1][0]) and (index == len(scored) - 1 or entry[0] <= scored[index + 1][0])]
    selected = []
    for entry in sorted(minima) + sorted(scored):
        if math.isfinite(entry[0]) and all(abs(entry[2] - previous[2]) >= 2 for previous in selected):
            selected.append(entry)
        if len(selected) == CANDIDATE_COUNT:
            break
    return [entry[1] for entry in selected]


def demodulate(sigma, values, optical, carrier, budget):
    spacing = statistics.median(current - previous for previous, current in zip(optical, optical[1:]))
    half_width = max(1.25 * 10000.0 / (2.0 * carrier), 6.0 * spacing)
    centers = list(range(0, len(sigma), 4))
    observations = []
    for ordinal, index in enumerate(centers):
        budget.check()
        position = optical[index]
        if position - half_width < optical[0] or position + half_width > optical[-1]:
            observations.append(None)
            continue
        first = bisect.bisect_left(optical, position - half_width)
        last = bisect.bisect_right(optical, position + half_width)
        if last - first < 9:
            observations.append(None)
            continue
        radius = max(sigma[index] - sigma[first], sigma[last - 1] - sigma[index])
        fit = harmonic_fit(sigma, values, optical, carrier, range(first, last), sigma[index], radius)
        if fit is None:
            observations.append(None)
            continue
        cosine, sine = fit[0][-2:]
        observations.append({"序号": ordinal, "坐标": position, "幅值": math.hypot(cosine, sine), "余相位": math.atan2(-sine, cosine)})
    amplitudes = [item["幅值"] for item in observations if item is not None]
    if not amplitudes:
        return None
    amplitude_scale = statistics.median(amplitudes)
    threshold = max(1e-8, 0.2 * amplitude_scale)
    segments = []
    active = []
    unreliable = []
    for index, item in enumerate(observations):
        if item is None or item["幅值"] < threshold:
            unreliable.append(centers[index])
            if active:
                segments.append(active)
                active = []
            continue
        if active:
            previous = active[-1]
            change = math.remainder(item["余相位"] - previous["余相位"], 2.0 * math.pi)
            if abs(change) >= 0.9 * math.pi:
                segments.append(active)
                active = []
        unwrapped = item["余相位"] if not active else active[-1]["展开余相位"] + change
        item["展开余相位"] = unwrapped
        item["相位"] = 4.0 * math.pi * carrier * item["坐标"] / 10000.0 + unwrapped
        item["权重"] = min(4.0, (item["幅值"] / max(amplitude_scale, 1e-12)) ** 2)
        active.append(item)
    if active:
        segments.append(active)
    eligible = [segment for segment in segments if len(segment) >= 6]
    if not eligible:
        return None
    chosen = max(eligible, key=lambda segment: segment[-1]["坐标"] - segment[0]["坐标"])
    span = abs(chosen[-1]["相位"] - chosen[0]["相位"]) / (2.0 * math.pi)
    if span < 1.0:
        return None
    return {
        "相位点": chosen,
        "有效点数": len(chosen),
        "可靠连续片段数": len(eligible),
        "可靠相位跨度_周": span,
        "幅值门槛_反射率比例": threshold,
        "不可靠中心抽样序号": unreliable,
    }


def joint_phase_fit(demodulations):
    moments = []
    for demodulation in demodulations:
        points = demodulation["相位点"]
        total_weight = sum(point["权重"] for point in points)
        mean_coordinate = sum(point["权重"] * point["坐标"] for point in points) / total_weight
        mean_phase = sum(point["权重"] * point["相位"] for point in points) / total_weight
        variance = sum(point["权重"] * (point["坐标"] - mean_coordinate) ** 2 for point in points)
        covariance = sum(point["权重"] * (point["坐标"] - mean_coordinate) * (point["相位"] - mean_phase) for point in points)
        moments.append((mean_coordinate, mean_phase, variance, covariance, total_weight))
    denominator = sum(item[2] for item in moments)
    if denominator <= 0.0:
        return None
    slope = sum(item[3] for item in moments) / denominator
    thickness = slope * 10000.0 / (4.0 * math.pi)
    if not math.isfinite(thickness) or thickness <= 0.0:
        return None
    offsets = [item[1] - slope * item[0] for item in moments]
    error = sum(point["权重"] * (point["相位"] - slope * point["坐标"] - offset) ** 2 for demodulation, offset in zip(demodulations, offsets) for point in demodulation["相位点"])
    angle_details = []
    for angle, demodulation, moment, offset in zip(ANGLE_DEGREES, demodulations, moments, offsets):
        detail = {key: value for key, value in demodulation.items() if key != "相位点"}
        detail.update({"入射角_度": angle, "相位偏置_弧度": offset, "单角厚度_微米": moment[3] / moment[2] * 10000.0 / (4.0 * math.pi)})
        angle_details.append(detail)
    return {"厚度_微米": thickness, "相位回归均方根残差_弧度": math.sqrt(error / sum(item[4] for item in moments)), "角度诊断": angle_details}


def estimate_thickness(sigma, spectra, refractive, budget):
    optical_sets = [optical_coordinates(sigma, refractive, angle) for angle in ANGLE_DEGREES]
    candidates = coarse_carriers(sigma, spectra, optical_sets, budget)
    accepted = []
    rejected = []
    for carrier in candidates:
        budget.check()
        demodulations = [demodulate(sigma, values, optical, carrier, budget) for values, optical in zip(spectra, optical_sets)]
        if any(item is None for item in demodulations):
            rejected.append({"载频对应厚度_微米": carrier, "原因": "至少一角度无6个可靠连续相位点或相位跨越不足1周"})
            continue
        fitted = joint_phase_fit(demodulations)
        if fitted is None:
            rejected.append({"载频对应厚度_微米": carrier, "原因": "连续相位回归未给有限正厚度"})
            continue
        fitted["初始载频对应厚度_微米"] = carrier
        accepted.append(fitted)
    if not accepted:
        return {"厚度_微米": None, "相位判定": "失败：连续相位不足", "候选": [], "排除候选": rejected}
    accepted.sort(key=lambda item: item["相位回归均方根残差_弧度"])
    best = accepted[0]
    cutoff = max(0.05, 1.5 * best["相位回归均方根残差_弧度"])
    near = [item["厚度_微米"] for item in accepted if item["相位回归均方根残差_弧度"] <= cutoff]
    ambiguous = max(near) - min(near) > 0.05 * best["厚度_微米"]
    large_residual = best["相位回归均方根残差_弧度"] > 0.3
    status = "失败：近优候选厚度歧义，保留有限主候选供同口径计分" if ambiguous else "已形成连续相位厚度"
    if large_residual:
        status += "；相位残差较大，降低置信表述"
    largest_step = max(current - previous for optical in optical_sets for previous, current in zip(optical, optical[1:]))
    maximum_phase_step = 4.0 * math.pi * best["厚度_微米"] * largest_step / 10000.0
    return {
        "厚度_微米": best["厚度_微米"],
        "相位判定": status,
        "相位歧义": ambiguous,
        "相位残差较大": large_residual,
        "触及搜索边界": best["厚度_微米"] <= THICKNESS_LIMITS[0] or best["厚度_微米"] >= THICKNESS_LIMITS[1],
        "估计相位最大相邻步长_弧度": maximum_phase_step,
        "抽稀混叠警告": maximum_phase_step >= math.pi,
        "近优候选厚度_微米": near,
        "候选": accepted,
        "排除候选": rejected,
    }


def two_beam_reflectance(sigma, thickness, refractive, angle):
    sine_square = math.sin(math.radians(angle)) ** 2
    layer_indices = (1.0, refractive, refractive + 0.8)
    propagation = [math.sqrt(index_value * index_value - sine_square) for index_value in layer_indices]
    phase = 4.0 * math.pi * thickness / 10000.0 * sigma * propagation[1]
    polarized = []
    for polarization in ("垂直", "平行"):
        admittance = propagation if polarization == "垂直" else [index_value * index_value / component for index_value, component in zip(layer_indices, propagation)]
        ambient, film, substrate = admittance
        reflection = (ambient - film) / (ambient + film)
        transmission_forward = 2.0 * ambient / (ambient + film)
        transmission_back = 2.0 * film / (film + ambient)
        lower_reflection = (film - substrate) / (film + substrate)
        returning = transmission_forward * transmission_back * lower_reflection
        polarized.append(reflection * reflection + returning * returning + 2.0 * reflection * returning * math.cos(phase))
    return sum(polarized) / 2.0


def synthetic_cases(sigma, specification, budget):
    generator = random.Random(specification["随机种子"])
    combinations = itertools.product(specification["厚度情景微米"], specification["折射率基值情景"], specification["色散斜率情景"], specification["噪声标准差情景"])
    cases = []
    for case_number, (truth, base, dispersion, noise) in enumerate(combinations, 1):
        budget.check()
        refractive = [base + dispersion * (coordinate - 2200.0) / 1800.0 for coordinate in sigma]
        spectra = []
        for angle in ANGLE_DEGREES:
            values = []
            previous_noise = 0.0
            for index, (coordinate, refractive_index) in enumerate(zip(sigma, refractive)):
                if noise > 0.0:
                    previous_noise = generator.gauss(0.0, noise) if index == 0 else 0.6 * previous_noise + 0.8 * generator.gauss(0.0, noise)
                value = two_beam_reflectance(coordinate, truth, refractive_index, angle)
                values.append(value + previous_noise)
            spectra.append(values)
        cases.append({"案例": case_number, "真厚度_微米": truth, "折射率基值": base, "色散斜率": dispersion, "噪声标准差_反射率比例": noise, "折射率曲线": refractive, "双角反射率": spectra})
    if len(cases) != 24:
        raise ValueError("共同情景不是24例，不得自行修改评分分母")
    return cases


def update_metric(result):
    records = result["合成逐例"]
    complete = len(records) == 24 and all(item.get("相对误差_%") is not None for item in records)
    value = statistics.mean(item["相对误差_%"] for item in records) if complete else None
    result["核心指标键值"][METRIC] = value
    result["主指标值"] = value
    result["辅助诊断"]["已给有限正厚度案例数"] = sum(item.get("相对误差_%") is not None for item in records)
    result["辅助诊断"]["有歧义案例数"] = sum(item.get("求解", {}).get("相位歧义", False) for item in records)


def execute(result, budget):
    route_path = ROOT / "交接/路线侦察.json"
    archive_path = ROOT / "交接/数据档案.json"
    plan = json.loads(route_path.read_text(encoding="utf-8"))
    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    specification = plan["共同原型协议"]["合成小样"]
    metadata = {entry["文件名"]: entry for entry in archive["文件档案"]}
    datasets = []
    for name in ("附件1.xlsx", "附件2.xlsx"):
        entry = metadata[name]
        records, checksum = read_spectrum(ROOT / entry["相对路径"], entry["文件哈希"], budget)
        datasets.append(records)
        result["输入哈希"][entry["相对路径"]] = checksum
    if [record[1] for record in datasets[0]] != [record[1] for record in datasets[1]]:
        raise ValueError("同片双角原始波数未一对一对齐")
    for path in (route_path, archive_path):
        result["输入哈希"][str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    indices = sample_indices(len(datasets[0]), 256)
    sigma = [datasets[0][index][1] for index in indices]
    result["样本索引"]["合成原始零基序号"] = indices
    result["样本索引"]["合成原始行号"] = [datasets[0][index][0] for index in indices]
    result["样本索引"]["合成波数_每厘米"] = sigma
    result["参数情景"] = {
        "共同合成设定": specification,
        "随机调用顺序": "厚度、基值、色散、噪声依次作笛卡尔积；每例先10度后15度；零噪声不取随机数；非零噪声首项平稳初始化",
        "局部基线阶数": 2,
        "局部拟合列数": 5,
        "载频格点数": CARRIER_COUNT,
        "保留候选数": CANDIDATE_COUNT,
        "厚度搜索盒_微米": list(THICKNESS_LIMITS),
        "幅值相对门槛": 0.2,
        "相位最短跨越_周": 1.0,
        "候选歧义相对门槛": 0.05,
    }
    result["随机种子"] = specification["随机种子"]
    cases = synthetic_cases(sigma, specification, budget)
    encoded = json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    result["输入哈希"]["共同合成小样哈希"] = hashlib.sha256(encoded).hexdigest()
    result["合成逐例"] = [{"案例": case["案例"], "真厚度_微米": case["真厚度_微米"], "折射率基值": case["折射率基值"], "色散斜率": case["色散斜率"], "噪声标准差_反射率比例": case["噪声标准差_反射率比例"], "状态": "尚未计算", "相对误差_%": None} for case in cases]
    result["运行状态"] = "已读取真数据并生成共同24例"
    save_result(result, budget.started)
    window = [index for index, record in enumerate(datasets[0]) if 1200.0 <= record[1] <= 3800.0]
    real_indices = [window[index] for index in sample_indices(len(window), 480)]
    real_sigma = [datasets[0][index][1] for index in real_indices]
    real_spectra = [[dataset[index][2] for index in real_indices] for dataset in datasets]
    result["样本索引"]["实测原始零基序号"] = real_indices
    result["样本索引"]["实测来源"] = [
        {"源附件": name, "材料": "碳化硅", "入射角_度": angle, "原始行号": [dataset[index][0] for index in real_indices], "波数_每厘米": real_sigma, "反射率_比例": values, "质量标记": [["零值"] if value == 0.0 else ["超百分之百"] if value > 1.0 else [] for value in values]}
        for name, angle, dataset, values in zip(("附件1.xlsx", "附件2.xlsx"), ANGLE_DEGREES, datasets, real_spectra)
    ]
    result["辅助诊断"]["异常窗口关系"] = "1200至3800与399.6747首点及801.278至927.1104超百段无交集；未裁剪反射率，不能声称清洗改善"
    for base in (2.4, 3.4):
        budget.check()
        estimate = estimate_thickness(real_sigma, real_spectra, [base] * len(real_sigma), budget)
        result["实测小样"].append({"假定恒折射率": base, "每角度点数": 480, "厚度_微米": estimate["厚度_微米"], "说明": "仅为假定光学参数下的数值，不作真实厚度精度声明，不参与主评分", "求解": estimate})
        if estimate["厚度_微米"] is None:
            result["失败原因"].append(f"实测恒折射率{base}情景：可靠连续相位不足；不虚构实测厚度")
        save_result(result, budget.started)
    for case, record in zip(cases, result["合成逐例"]):
        budget.check()
        estimate = estimate_thickness(sigma, case["双角反射率"], case["折射率曲线"], budget)
        record["求解"] = estimate
        record["厚度_微米"] = estimate["厚度_微米"]
        record["状态"] = estimate["相位判定"]
        if estimate["厚度_微米"] is not None:
            record["相对误差_%"] = 100.0 * abs(estimate["厚度_微米"] - record["真厚度_微米"]) / record["真厚度_微米"]
        else:
            result["失败原因"].append(f"案例{case['案例']}：连续可靠相位不足，保留缺失计入未完成")
        update_metric(result)
        result["运行状态"] = "正在逐例计算；仅24例齐全后生成主分数"
        save_result(result, budget.started)
    uniform_sigma = [sigma[0] + index * (sigma[-1] - sigma[0]) / (len(sigma) - 1) for index in range(len(sigma))]
    result["辅助诊断"]["抽稀原坐标与端点等距最大差_每厘米"] = max(abs(actual - uniform) for actual, uniform in zip(sigma, uniform_sigma))
    result["辅助诊断"]["坐标差解释"] = "256点按floor抽样本身引入不等间隔；此差不等于全7469点网格的0.0006641805034632853微小偏差"
    for case, record in zip(cases, result["合成逐例"]):
        budget.check()
        refractive = [case["折射率基值"] + case["色散斜率"] * (coordinate - 2200.0) / 1800.0 for coordinate in uniform_sigma]
        estimate = estimate_thickness(uniform_sigma, case["双角反射率"], refractive, budget)
        thickness = estimate["厚度_微米"]
        original = record["厚度_微米"]
        result["端点等距对照"].append({"案例": case["案例"], "原坐标厚度_微米": original, "等距近似厚度_微米": thickness, "厚度变化_微米": None if thickness is None or original is None else thickness - original, "状态": estimate["相位判定"], "说明": "配对敏感性诊断，不设第二个聚合评分，不据此调参"})
        save_result(result, budget.started)
    result["辅助诊断"]["实测光学情景离散厚度_微米"] = [item["厚度_微米"] for item in result["实测小样"]]
    result["运行状态"] = "完成" if result["主指标值"] is not None else "未完成：至少一例未给有限正厚度"
    if result["辅助诊断"]["有歧义案例数"]:
        result["运行状态"] += "；存在相位歧义，不宣称全部通过"


def append_runtime_record(result, started):
    import fcntl

    path = ROOT / "交接/实验记录.json"
    with (ROOT / "交接/.实验记录.lock").open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            result["辅助诊断"]["实验追加状态"] = "共享记录正被占用；不等待锁以保持墙钟上限，本次过程保存在结果中"
            return
        if time.monotonic() - started >= HARD_SECONDS - 1.0:
            result["辅助诊断"]["实验追加状态"] = "到达收尾时间界限；本次过程保存在结果中"
            return
        records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(records, list):
            raise ValueError("实验记录顶层必须为数组")
        records.append({
            "类别": "科学尝试" if result["主指标值"] is not None else "流程事件",
            "问题": 1,
            "尝试": "在双角原坐标上作64格载波初选与连续相位回归，并以24例已知厚度合成光谱检验",
            "现象": f"有限正厚度案例{result['辅助诊断'].get('已给有限正厚度案例数', 0)}/24；平均相对误差为{result['主指标值']}%；相位歧义案例{result['辅助诊断'].get('有歧义案例数', 0)}",
            "决定": "保留全部案例、配对坐标对照和失败原因；实测反射率仅给条件厚度，不把合成误差等同材料标定精度",
            "依据": "求解结果:核心指标键值；求解结果:合成逐例；求解结果:端点等距对照；求解结果:实测小样",
        })
        write_json(path, records)
        result["辅助诊断"]["实验追加状态"] = "已追加，未覆盖原有条目"


def worker(started):
    result = json.loads(OUTPUT.read_text(encoding="utf-8"))
    budget = Budget(started)
    try:
        execute(result, budget)
    except TimeBudgetExceeded as error:
        result["运行状态"] = "软截止：保留已完成结果与完整案例清单"
        result["失败原因"].append(str(error))
    except Exception as error:
        result["运行状态"] = "异常中止：保留已完成结果"
        result["失败原因"].append(f"{type(error).__name__}: {error}")
    update_metric(result)
    save_result(result, started)
    print(json.dumps({"路线名": result["路线名"], "运行状态": result["运行状态"], "核心指标键值": result["核心指标键值"], "实际用时秒": result["实际用时秒"], "实测条件厚度_微米": [item["厚度_微米"] for item in result["实测小样"]], "失败原因": result["失败原因"], "经验覆盖率": result["经验覆盖率"]}, ensure_ascii=False), flush=True)
    return 0 if result["主指标值"] is not None else 1


def main():
    started = time.monotonic()
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        return worker(float(sys.argv[2]))
    result = initial_result()
    save_result(result, started)
    try:
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(started)], cwd=ROOT, timeout=max(0.1, HARD_SECONDS - 2.0 - (time.monotonic() - started)), check=False)
        exit_code = completed.returncode
    except subprocess.TimeoutExpired:
        exit_code = 1
        result = json.loads(OUTPUT.read_text(encoding="utf-8"))
        result["运行状态"] = "父进程硬截止：保留已完成结果"
        result["失败原因"].append("子进程达到173秒截止，预留2秒记录状态；未以部分均值替代24例指标")
        save_result(result, started)
    result = json.loads(OUTPUT.read_text(encoding="utf-8"))
    if exit_code and result["运行状态"] not in ("父进程硬截止：保留已完成结果", "软截止：保留已完成结果与完整案例清单", "异常中止：保留已完成结果"):
        result["失败原因"].append(f"子进程退出码{exit_code}")
    if time.monotonic() - started < HARD_SECONDS - 1.0:
        try:
            append_runtime_record(result, started)
        except (OSError, ValueError) as error:
            result["辅助诊断"]["实验追加状态"] = f"追加失败，数值结果保留：{error}"
    save_result(result, started)
    print(f"路线3结束：{result['运行状态']}；{METRIC}={result['主指标值']}；总用时={time.monotonic() - started:.3f}秒", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
