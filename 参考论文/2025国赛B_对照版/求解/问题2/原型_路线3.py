"""问题二双角相位回归的小样原型；仅执行本文件时读取真数据并生成结果。

R为附件反射率百分数除以100，σ单位cm^-1，d单位μm。由一次往返光程
得到φ=4π(d/10000)σq+ψ，q=sqrt(n²-sin²θ)，共同经验情景为
n=n参+c*((2000/σ)²-1)。n不是附件提供的已知材料常数。
所有训练量均在双角联动切分、20 cm^-1保护带剔除之后估计。
先在每角度训练集拟合二次基线，再在每个连通段的至多20 cm^-1窗口
投影cos(2πf(σ-σ中心))与sin(2πf(σ-σ中心))，得到atan2(-正弦系数,
余弦系数)。窗口不能识别额外五参数局部模型，故不重复拟合局部基线。
每段独立解缠；每角首段整数固定为零以消除整体2π规范自由度。
其余段只用有限整数候选及训练相位联合估计共享d和两个常偏置ψ。
固定整数、n参和c后，对每角消去相位均值，以加权协方差/方差求d；
权重只取训练解调幅值平方。单调性由d(σq)/dσ>0检查，不强改观测相位。
最终仅用训练强度拟合二次基线和一次余弦幅值包络，不能逐点修正相位。
初始载频之间只按训练重建误差选择；非线性精修的目标仍是相位残差。
唯一排名量为两折、双角、各两测试块共八项标准化均方根误差的均值。
L=(φ+2π整数-ψ)/(2πσ)只用于偏置病态诊断，绝不以相位导数替代L。
"""

import time

PROCESS_START = time.monotonic()

import bisect
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "求解/问题2/原型结果/路线3.json"
METRIC = "连续留段标准化均方根误差"
ANGLES = (10, 15)
HARD_SECONDS = 175.0
SOFT_SECONDS = 165.0
SEED = 20260909
TAU = 2.0 * math.pi
NAMESPACE = {"表": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
EXPECTED_FOLDS = [
    {"折号": 1, "训练块": [1, 2, 4, 5, 7, 8, 10, 11], "校准块": [3, 9], "测试块": [6, 12]},
    {"折号": 2, "训练块": [2, 3, 5, 6, 8, 9, 11, 12], "校准块": [4, 10], "测试块": [1, 7]},
]


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def checkpoint(result, t0):
    result["实际用时秒"] = time.monotonic() - t0
    result["耗时秒"] = result["实际用时秒"]
    write_json(OUTPUT, result)


def append_experiment(entry):
    path = ROOT / "交接/实验记录.json"
    with (ROOT / "交接/.实验记录.lock").open("a", encoding="utf-8") as lock:
        deadline = time.monotonic() + 0.8
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("实验记录追加锁等待超过0.8秒")
                time.sleep(0.01)
        entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(entries, list):
            raise ValueError("实验记录顶层必须为数组")
        entries.append(entry)
        write_json(path, entries)


def initial_result():
    return {
        "问题": 2, "路线编号": "二丙", "路线序号": 3, "路线名": "双角相位回归",
        "运行状态": "读取真实附件", "使用附件": ["附件1.xlsx", "附件2.xlsx"],
        "核心指标": {}, "主指标名称": METRIC, "主指标值": None,
        "指标含义": "八个相同测试块标准化均方根误差等权平均；无量纲，越小越好，不是厚度真值误差。",
        "用时估计": {"性质": "设计分配，未经执行计时", "读取与切分秒": 15,
                     "解调与相位回归秒": 95, "重建区间与病态诊断秒": 40,
                     "汇总写出秒": 15, "软截止秒": SOFT_SECONDS, "墙钟上限秒": HARD_SECONDS},
        "口径说明": {
            "输入": "真实碳化硅双角谱；1200至3800 cm^-1原坐标各480点，不平均、不插值。",
            "隔离": "同波数双角同组；训练边界各舍20 cm^-1；局部窗口总跨度不超过20 cm^-1，不跨缺口解缠。",
            "尺度": "本折本角度保护后训练反射率去二次基线残差四分位距，数值下限0.0001；不是噪声标准差。",
            "评分": "每块40点先算RMSE/训练尺度，再将全部八块等权平均，缺块不报部分均值。",
            "相位": "三载频初始化；每段一个整数、每角一个常偏置；共享d、n参和c；无逐点相位自由度。",
            "重建": "每角二次基线及一次幅值包络乘cos(传播相位+训练相位偏置)，共五个线性强度参数。",
            "选择": "只用训练相位精修，用训练强度残差在载频候选间选择；校准、测试值不进入搜索。",
            "几何消参": "只对已解缠相位减常偏置后构造L；偏置规范自由度意味着无外部锚不能识别绝对L。",
            "异常": "主窗口不含399.6747首点或801.278至927.1104超百区，不宣称清洗改善。",
            "范围": "条件厚度与离散敏感性跨度不是置信区间；不将群光学厚度代入L平方差。",
        },
        "输入哈希": [], "样本索引": [], "折分": [], "参数情景": [],
        "分块预测与残差": [], "厚度及单位": [], "经验覆盖率": {},
        "辅助诊断": {"分折": [], "病态诊断": [], "方法卡片落实":
                   "使用双角同组连续留段；报告两折分项及经验区间覆盖、宽度随留段距离。未引入树模型或分布无关覆盖承诺。"},
        "失败原因": [], "随机种子": SEED, "用于同口径排名": False,
    }


def dot(left, right):
    return sum(first * second for first, second in zip(left, right))


def quantile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower])


def least_squares(columns, observed):
    count = len(columns)
    basis = []
    triangular = [[0.0] * count for unused in range(count)]
    rank = 0
    for column_index, column in enumerate(columns):
        residual = list(column)
        for unused in range(2):
            for basis_index, unit in enumerate(basis):
                projection = dot(unit, residual)
                triangular[basis_index][column_index] += projection
                residual = [value - projection * direction for value, direction in zip(residual, unit)]
        length = math.sqrt(max(0.0, dot(residual, residual)))
        if length < 1e-11:
            triangular[column_index][column_index] = 1.0
            basis.append([0.0] * len(observed))
        else:
            rank += 1
            triangular[column_index][column_index] = length
            basis.append([value / length for value in residual])
    projected = [dot(unit, observed) for unit in basis]
    coefficients = [0.0] * count
    for row in range(count - 1, -1, -1):
        coefficients[row] = (projected[row] - sum(triangular[row][column] * coefficients[column]
                            for column in range(row + 1, count))) / triangular[row][row]
    residual = [value - sum(projected[column] * basis[column][row] for column in range(count))
                for row, value in enumerate(observed)]
    return coefficients, residual, rank


def baseline(coordinates, observed):
    columns = [[1.0] * len(coordinates), coordinates, [value * value for value in coordinates]]
    coefficients, residual, rank = least_squares(columns, observed)
    if rank != 3:
        raise ArithmeticError("训练坐标无法识别共同二次基线")
    return coefficients, residual, max(quantile(residual, 0.75) - quantile(residual, 0.25), 0.0001)


def read_data(result):
    scout = json.loads((ROOT / "交接/路线侦察.json").read_text(encoding="utf-8"))
    archive = json.loads((ROOT / "交接/数据档案.json").read_text(encoding="utf-8"))
    protocol = scout["共同原型协议"]
    specification = protocol["实测小样"]
    if (specification["每角度点数"] != 480 or specification["基本窗口波数"] != [1200, 3800]
            or specification["折分"] != EXPECTED_FOLDS):
        raise ValueError("共同小样口径已改变，三路线必须同步，不独自改动样本或折分")
    metadata = {entry["文件名"]: entry for entry in archive["文件档案"]}
    datasets = []
    for filename, angle in zip(result["使用附件"], ANGLES):
        path = ROOT / "数据" / filename
        expected = metadata[filename]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected["文件哈希"]:
            raise ValueError(f"{filename}与数据档案的SHA-256不一致")
        if expected["材料"] != "碳化硅" or expected["入射角度"] != angle:
            raise ValueError("材料或角度不符合问题二双角谱")
        sheet = next(item for item in expected["工作表"] if item["名称"] == "Sheet1")
        with ZipFile(path) as workbook:
            tree = ET.fromstring(workbook.read(sheet["内部路径"]))
        records = []
        for row in tree.findall("表:sheetData/表:row", NAMESPACE):
            row_number = int(row.attrib["r"])
            if row_number == 1:
                continue
            values = {}
            for cell in row.findall("表:c", NAMESPACE):
                address = cell.attrib["r"]
                column = address.rstrip("0123456789")
                if column not in ("A", "B"):
                    continue
                value = cell.find("表:v", NAMESPACE)
                if (cell.get("t", "n") != "n" or value is None
                        or cell.find("表:f", NAMESPACE) is not None):
                    raise ValueError(f"{filename}的{address}不是原始数值")
                values[column] = float(value.text)
            if set(values) != {"A", "B"} or not all(math.isfinite(value) for value in values.values()):
                raise ValueError(f"{filename}第{row_number}行不是双列有限数值")
            records.append((values["A"], values["B"] / 100.0, row_number))
        records.sort()
        if len(records) != 7469 or any(left[0] >= right[0] for left, right in zip(records, records[1:])):
            raise ValueError("原始点数或严格单调波数与档案不符")
        datasets.append(records)
        result["输入哈希"].append({"附件": filename, "算法": "SHA-256", "哈希": digest, "原始点数": len(records)})
    if [item[0] for item in datasets[0]] != [item[0] for item in datasets[1]]:
        raise ValueError("双角波数没有完全对齐")
    window = [index for index, record in enumerate(datasets[0]) if 1200 <= record[0] <= 3800]
    if len(window) < 480:
        raise ValueError("共同窗口不足480点")
    selected = [window[index * (len(window) - 1) // 479] for index in range(480)]
    coordinates = [datasets[0][index][0] for index in selected]
    values = [[records[index][1] for index in selected] for records in datasets]
    result["样本索引"] = [{"样本序号": index + 1, "块号": index // 40 + 1,
                        "原始行号_附件一": datasets[0][source][2],
                        "原始行号_附件二": datasets[1][source][2], "波数_cm^-1": coordinates[index],
                        "质量标记": "主窗口不含首点和超百区；未裁剪反射率"}
                       for index, source in enumerate(selected)]
    result["窗口原始点数_每角度"] = len(window)
    result["折分"] = specification["折分"]
    result["参数情景"] = protocol["共同光学情景"]["初始情景"]
    expected_scenarios = [{"参考折射率": reference, "色散系数": dispersion}
                          for reference in (2, 3, 4) for dispersion in (-0.5, 0, 0.5)]
    if result["参数情景"] != expected_scenarios:
        raise ValueError("共同九个光学初始情景变化，需同步检查三路线")
    return coordinates, values, [datasets[0][index][0] for index in window]


def make_fold(coordinates, values, full_coordinates, specification):
    training_blocks = set(specification["训练块"])
    edges = [(coordinates[index - 1] + coordinates[index]) / 2 for index in range(40, 480, 40)]
    guard_edges = [edge for block, edge in enumerate(edges, 1)
                   if (block in training_blocks) != (block + 1 in training_blocks)]
    components = {}
    component = -1
    for block in range(1, 13):
        if block in training_blocks:
            if block - 1 not in training_blocks:
                component += 1
            components[block] = component
    indices = [index for index, coordinate in enumerate(coordinates)
               if index // 40 + 1 in training_blocks and all(abs(coordinate - edge) >= 20 for edge in guard_edges)]
    training_coordinates = [coordinates[index] for index in indices]
    center = (min(training_coordinates) + max(training_coordinates)) / 2
    half_span = (max(training_coordinates) - min(training_coordinates)) / 2
    scaled = [(coordinate - center) / half_span for coordinate in training_coordinates]
    groups = [[position for position, index in enumerate(indices) if components[index // 40 + 1] == group]
              for group in range(component + 1)]
    angle_data = []
    for angle_index, angle in enumerate(ANGLES):
        observed = [values[angle_index][index] for index in indices]
        coefficients, residual, scale = baseline(scaled, observed)
        angle_data.append({"angle": angle, "sigma": training_coordinates, "x": scaled,
                           "y": observed, "baseline": coefficients, "residual": residual, "scale": scale})
    full_groups = [[] for unused in groups]
    for coordinate in full_coordinates:
        block = bisect.bisect_right(edges, coordinate) + 1
        if block in training_blocks and all(abs(coordinate - edge) >= 20 for edge in guard_edges):
            full_groups[components[block]].append(coordinate)
    return {"spec": specification, "coordinates": coordinates, "values": values,
            "indices": indices, "groups": groups, "full_groups": full_groups,
            "angles": angle_data, "center": center, "half_span": half_span, "guard_edges": guard_edges}


def valid_optics(reference, dispersion):
    if not 1.2 <= reference <= 6.0 or not -1.0 <= dispersion <= 1.0:
        return False
    sine_squared = math.sin(math.radians(15)) ** 2
    refractive = [reference + dispersion * ((2000 / coordinate) ** 2 - 1) for coordinate in (1200, 3800)]
    monotone_numerator = (reference - dispersion) ** 2 - sine_squared - (4e6 * dispersion) ** 2 / 1200 ** 4
    return min(refractive) > math.sqrt(sine_squared) and monotone_numerator > 0


def phase_rate(coordinate, angle, reference, dispersion):
    refractive = reference + dispersion * ((2000 / coordinate) ** 2 - 1)
    return 4 * math.pi / 10000 * coordinate * math.sqrt(refractive ** 2 - math.sin(math.radians(angle)) ** 2)


def carriers(fold, deadline):
    candidates = []
    for index in range(64):
        if time.monotonic() >= deadline and candidates:
            break
        frequency = 0.00015 + index * (0.048 - 0.00015) / 63
        explained = 0.0
        for data in fold["angles"]:
            cosine = [math.cos(TAU * frequency * coordinate) for coordinate in data["sigma"]]
            sine = [math.sin(TAU * frequency * coordinate) for coordinate in data["sigma"]]
            diagonal_cosine, diagonal_sine, off_diagonal = dot(cosine, cosine), dot(sine, sine), dot(cosine, sine)
            determinant = diagonal_cosine * diagonal_sine - off_diagonal ** 2
            target_cosine, target_sine = dot(cosine, data["residual"]), dot(sine, data["residual"])
            if determinant > 1e-12:
                explained += (diagonal_sine * target_cosine ** 2 + diagonal_cosine * target_sine ** 2
                              - 2 * off_diagonal * target_cosine * target_sine) / determinant / max(dot(data["residual"], data["residual"]), 1e-16)
        candidates.append((explained, frequency))
    chosen = []
    separation = 1.0 / (fold["angles"][0]["sigma"][-1] - fold["angles"][0]["sigma"][0])
    for explained, frequency in sorted(candidates, reverse=True):
        if all(abs(frequency - item["frequency"]) >= separation for item in chosen):
            chosen.append({"frequency": frequency, "explained": explained})
        if len(chosen) == 3:
            break
    return chosen


def demodulate(fold, frequency, window=20.0, amplitude_cutoff=0.05, perturbation=None):
    phase_data = []
    for angle_index, data in enumerate(fold["angles"]):
        residual = data["residual"] if perturbation is None else perturbation[angle_index]
        angle_groups = []
        amplitudes = []
        for group in fold["groups"]:
            points = []
            previous_phase = None
            offset = 0.0
            group_coordinates = [data["sigma"][position] for position in group]
            for position in group:
                coordinate = data["sigma"][position]
                lower = bisect.bisect_left(group_coordinates, coordinate - window / 2)
                upper = bisect.bisect_right(group_coordinates, coordinate + window / 2)
                neighbors = group[lower:upper]
                if len(neighbors) < 2:
                    continue
                diagonal_cosine = diagonal_sine = off_diagonal = target_cosine = target_sine = 0.0
                for neighbor in neighbors:
                    distance = data["sigma"][neighbor] - coordinate
                    taper = math.exp(-2 * (distance / (window / 2)) ** 2)
                    cosine, sine = math.cos(TAU * frequency * distance), math.sin(TAU * frequency * distance)
                    diagonal_cosine += taper * cosine * cosine
                    diagonal_sine += taper * sine * sine
                    off_diagonal += taper * cosine * sine
                    target_cosine += taper * cosine * residual[neighbor]
                    target_sine += taper * sine * residual[neighbor]
                determinant = diagonal_cosine * diagonal_sine - off_diagonal ** 2
                if determinant <= 1e-12 * max(diagonal_cosine * diagonal_sine, 1e-30):
                    continue
                coefficient_cosine = (target_cosine * diagonal_sine - target_sine * off_diagonal) / determinant
                coefficient_sine = (target_sine * diagonal_cosine - target_cosine * off_diagonal) / determinant
                raw_phase = math.atan2(-coefficient_sine, coefficient_cosine)
                if previous_phase is not None:
                    offset -= TAU * round((raw_phase - previous_phase) / TAU)
                phase = raw_phase + offset
                previous_phase = raw_phase
                amplitude = math.hypot(coefficient_cosine, coefficient_sine)
                amplitudes.append(amplitude)
                points.append({"position": position, "sigma": coordinate, "phase": phase, "amplitude": amplitude})
            angle_groups.append(points)
        reference_amplitude = quantile(amplitudes, 0.5) if amplitudes else 0.0
        for points in angle_groups:
            for point in points:
                point["weight"] = (min(point["amplitude"] / reference_amplitude, 10.0) ** 2
                                   if reference_amplitude > 0 and point["amplitude"] >= amplitude_cutoff * reference_amplitude else 0.0)
        if any(sum(point["weight"] for point in points) <= 0 for points in angle_groups):
            raise ArithmeticError("某训练连通段无可识别相位幅值，不能虚造连续相位")
        phase_data.append(angle_groups)
    return {"angles": phase_data, "frequency": frequency, "window": window, "cutoff": amplitude_cutoff}


def moment_table(phase_data, reference, dispersion):
    if not valid_optics(reference, dispersion):
        return None
    tables = []
    for angle, groups in zip(ANGLES, phase_data["angles"]):
        table = []
        for points in groups:
            weights = [point["weight"] for point in points]
            phases = [point["phase"] for point in points]
            rates = [phase_rate(point["sigma"], angle, reference, dispersion) for point in points]
            weight = sum(weights)
            weighted_phases = [weight_value * phase for weight_value, phase in zip(weights, phases)]
            weighted_rates = [weight_value * rate for weight_value, rate in zip(weights, rates)]
            table.append({"weight": weight, "phase": sum(weighted_phases), "rate": sum(weighted_rates),
                          "phase2": dot(weighted_phases, phases), "rate2": dot(weighted_rates, rates),
                          "cross": dot(weighted_phases, rates)})
        tables.append(table)
    return tables


def phase_regression(tables, seed_thickness, rounds=6, initial_turns=None):
    thickness = max(0.5, min(40.0, seed_thickness))
    turns = [[0] * len(table) for table in tables]
    offsets = [(table[0]["phase"] - thickness * table[0]["rate"]) / table[0]["weight"] for table in tables]
    loss = math.inf
    for iteration in range(rounds):
        for angle_index, table in enumerate(tables):
            for segment, moments in enumerate(table):
                if segment == 0:
                    continue
                if iteration == 0 and initial_turns is not None:
                    turns[angle_index][segment] = initial_turns[angle_index][segment]
                    continue
                target = (thickness * moments["rate"] + offsets[angle_index] * moments["weight"] - moments["phase"]) / moments["weight"] / TAU
                center = round(target)
                turns[angle_index][segment] = min((center - 1, center, center + 1), key=lambda integer: abs(integer - target))
        variance = covariance = 0.0
        totals = []
        for table, angle_turns in zip(tables, turns):
            weight = sum(item["weight"] for item in table)
            rate = sum(item["rate"] for item in table)
            phase = sum(item["phase"] + TAU * integer * item["weight"] for item, integer in zip(table, angle_turns))
            cross = sum(item["cross"] + TAU * integer * item["rate"] for item, integer in zip(table, angle_turns))
            phase2 = sum(item["phase2"] + 2 * TAU * integer * item["phase"] + (TAU * integer) ** 2 * item["weight"]
                         for item, integer in zip(table, angle_turns))
            rate2 = sum(item["rate2"] for item in table)
            variance += rate2 - rate * rate / weight
            covariance += cross - rate * phase / weight
            totals.append((weight, rate, phase, rate2, cross, phase2))
        if variance <= 1e-14:
            raise ArithmeticError("相位回归坐标退化")
        updated = max(0.5, min(40.0, covariance / variance))
        offsets = [(phase - updated * rate) / weight for weight, rate, phase, rate2, cross, phase2 in totals]
        loss = sum(max(0.0, phase2 - phase * phase / weight - 2 * updated * (cross - rate * phase / weight)
                       + updated ** 2 * (rate2 - rate * rate / weight))
                   for weight, rate, phase, rate2, cross, phase2 in totals) / sum(item[0] for item in totals)
        difference = abs(updated - thickness)
        thickness = updated
        if iteration > 0 and difference < 1e-9:
            break
    return {"thickness": thickness, "offsets": offsets, "turns": turns, "phase_loss": loss}


def candidate_from_phase(phase_data, reference, dispersion, seed_thickness, initial_turns=None, tables=None):
    tables = moment_table(phase_data, reference, dispersion) if tables is None else tables
    if tables is None:
        return None
    candidate = phase_regression(tables, seed_thickness, initial_turns=initial_turns)
    candidate.update({"reference": reference, "dispersion": dispersion, "phase_data": phase_data})
    return candidate


def reconstruct(fold, candidate):
    models = []
    loss = 0.0
    for angle_index, data in enumerate(fold["angles"]):
        cosine = [math.cos(candidate["thickness"] * phase_rate(coordinate, data["angle"], candidate["reference"], candidate["dispersion"])
                           + candidate["offsets"][angle_index]) for coordinate in data["sigma"]]
        columns = [[1.0] * len(cosine), data["x"], [value ** 2 for value in data["x"]], cosine,
                   [value * coordinate for value, coordinate in zip(cosine, data["x"])]]
        coefficients, residual, rank = least_squares(columns, data["y"])
        loss += dot(residual, residual) / len(residual) / data["scale"] ** 2
        models.append({"coefficients": coefficients, "rank": rank})
    candidate["models"] = models
    candidate["intensity_loss"] = loss / 2
    return candidate


def predict(fold, candidate, angle_index, indices):
    coefficients = candidate["models"][angle_index]["coefficients"]
    predictions = []
    for index in indices:
        coordinate = fold["coordinates"][index]
        scaled = (coordinate - fold["center"]) / fold["half_span"]
        phase = candidate["thickness"] * phase_rate(coordinate, ANGLES[angle_index], candidate["reference"], candidate["dispersion"]) + candidate["offsets"][angle_index]
        predictions.append(coefficients[0] + coefficients[1] * scaled + coefficients[2] * scaled ** 2
                           + (coefficients[3] + coefficients[4] * scaled) * math.cos(phase))
    return predictions


def candidate_record(candidate, fold_number):
    parameters = (candidate["thickness"], candidate["reference"], candidate["dispersion"])
    bounds = ((0.5, 40), (1.2, 6), (-1, 1))
    return {"折号": fold_number, "条件厚度_um": parameters[0], "参考折射率": parameters[1], "色散系数": parameters[2],
            "角度顺序_度": list(ANGLES), "常偏置_rad": candidate["offsets"], "分段整数周数": candidate["turns"],
            "载频_cm": candidate["phase_data"]["frequency"], "局部窗口跨度_cm^-1": candidate["phase_data"]["window"],
            "加权相位残差均方_rad2": candidate["phase_loss"], "训练强度选择损失": candidate["intensity_loss"],
            "强度线性系数_各角": [model["coefficients"] for model in candidate["models"]],
            "强度设计秩_各角": [model["rank"] for model in candidate["models"]],
            "搜索盒边界命中": any(min(abs(value - lower), abs(value - upper)) < 1e-5 for value, (lower, upper) in zip(parameters, bounds)),
            "说明": "条件于经验色散和自由常偏置的有限搜索估计；并非无折射率绝对厚度。"}


def score_fold(fold, candidate):
    blocks = []
    calibration_indices = [index for index in range(480) if index // 40 + 1 in fold["spec"]["校准块"]]
    for angle_index, data in enumerate(fold["angles"]):
        calibration_predictions = predict(fold, candidate, angle_index, calibration_indices)
        absolute_errors = sorted(abs(fold["values"][angle_index][index] - value)
                                 for index, value in zip(calibration_indices, calibration_predictions))
        half_width = absolute_errors[math.ceil(0.9 * len(absolute_errors)) - 1]
        for block in fold["spec"]["测试块"]:
            indices = list(range((block - 1) * 40, block * 40))
            predictions = predict(fold, candidate, angle_index, indices)
            observed = [fold["values"][angle_index][index] for index in indices]
            residual = [value - prediction for value, prediction in zip(observed, predictions)]
            if not all(math.isfinite(value) for value in predictions + residual + [half_width]):
                raise ArithmeticError("测试预测或区间非有限数")
            rmse = math.sqrt(dot(residual, residual) / 40)
            distances = [min(abs(fold["coordinates"][index] - train) for train in data["sigma"]) for index in indices]
            covered = sum(abs(value) <= half_width for value in residual)
            blocks.append({"折号": fold["spec"]["折号"], "材料": "碳化硅", "角度_度": data["angle"],
                           "测试块": block, "点数": 40, "样本序号": [index + 1 for index in indices],
                           "波数_cm^-1": [fold["coordinates"][index] for index in indices],
                           "实测反射率_比例": observed, "预测反射率_比例": predictions, "残差_比例": residual,
                           "均方根误差_比例": rmse, "训练尺度_比例": data["scale"], "标准化均方根误差": rmse / data["scale"],
                           "校准点数": len(calibration_indices), "预测半宽_比例": half_width,
                           "预测下界_比例": [value - half_width for value in predictions],
                           "预测上界_比例": [value + half_width for value in predictions],
                           "覆盖点数": covered, "经验覆盖率": covered / 40,
                           "预测区间平均宽度_比例": 2 * half_width,
                           "距最近训练点均值_cm^-1": sum(distances) / 40, "距最近训练点最大值_cm^-1": max(distances)})
    return blocks


def refresh_score(result, folds, candidates):
    if len(candidates) != 2:
        return
    blocks = [block for fold, candidate in zip(folds, candidates) for block in score_fold(fold, candidate)]
    expected = {(spec["折号"], angle, block) for spec in EXPECTED_FOLDS for angle in ANGLES for block in spec["测试块"]}
    if {(block["折号"], block["角度_度"], block["测试块"]) for block in blocks} != expected or len(blocks) != 8:
        raise ValueError("不得遗漏测试块或按成功子集计算主分数")
    score = sum(block["标准化均方根误差"] for block in blocks) / 8
    if not math.isfinite(score):
        raise ArithmeticError("主分数不是有限数")
    result["核心指标"] = {METRIC: score}
    result["主指标值"] = score
    result["分块预测与残差"] = blocks
    result["厚度及单位"] = [candidate_record(candidate, fold["spec"]["折号"]) for fold, candidate in zip(folds, candidates)]
    covered = sum(block["覆盖点数"] for block in blocks)
    result["经验覆盖率"] = {"构造方法": "各折各角专用校准块绝对残差的向上取整90%经验分位作对称半宽，不裁剪上下界。",
                            "名义覆盖率": 0.9, "覆盖点数": covered, "测试点数": 320, "测试经验覆盖率": covered / 320,
                            "点数加权平均宽度_比例": sum(block["预测区间平均宽度_比例"] for block in blocks) / 8,
                            "覆盖不足": covered / 320 < 0.9,
                            "距离义务": "逐块报告宽度及到最近训练点的距离；本经验半宽不会自动随外推距离增大。",
                            "限制": "两个校准块及相关光谱不支持独立性或分布无关保证；不由测试残差回调。"}
    result["用于同口径排名"] = True


def refine(fold, initial, deadline):
    best = initial
    steps = [0.3, 0.25, 0.10]
    for unused_round in range(20):
        if time.monotonic() >= deadline:
            break
        improved = False
        for axis in range(3):
            for direction in (-1, 1):
                if time.monotonic() >= deadline:
                    return reconstruct(fold, best)
                parameters = [best["thickness"], best["reference"], best["dispersion"]]
                parameters[axis] += direction * steps[axis]
                if not 0.5 <= parameters[0] <= 40:
                    continue
                trial = candidate_from_phase(best["phase_data"], parameters[1], parameters[2], parameters[0])
                if trial is not None and trial["phase_loss"] < best["phase_loss"] - 1e-12:
                    best = trial
                    improved = True
        if not improved:
            steps = [step / 2 for step in steps]
        if max(steps) < 0.001:
            break
    return reconstruct(fold, best)


def search_fold(fold, starts, pilot, scenarios, deadline, save_candidate):
    finalist_pool = [pilot]
    best_running = pilot
    scenario_records = []
    evaluated = 0
    completed = True
    for initialization in starts:
        if time.monotonic() >= deadline:
            completed = False
            break
        phase_data = demodulate(fold, initialization["frequency"])
        carrier_pool = []
        for scenario in scenarios:
            if time.monotonic() >= deadline:
                completed = False
                break
            reference, dispersion = scenario["参考折射率"], scenario["色散系数"]
            tables = moment_table(phase_data, reference, dispersion)
            if tables is None:
                continue
            best = None
            for thickness_index in range(96):
                if time.monotonic() >= deadline:
                    completed = False
                    break
                seed_thickness = 0.5 + thickness_index * 39.5 / 95
                candidate = candidate_from_phase(phase_data, reference, dispersion, seed_thickness, tables=tables)
                evaluated += 1
                if best is None or candidate["phase_loss"] < best["phase_loss"]:
                    best = candidate
            if best is not None:
                reconstruct(fold, best)
                scenario_records.append(candidate_record(best, fold["spec"]["折号"]))
                carrier_pool.append(best)
                if best["intensity_loss"] < best_running["intensity_loss"]:
                    best_running = best
                    save_candidate(best_running)
        finalist_pool.extend(sorted(carrier_pool, key=lambda item: item["phase_loss"])[:3])
    finalist_pool.append(best_running)
    distinct = {}
    for candidate in finalist_pool:
        identity = (round(candidate["thickness"], 9), candidate["reference"], candidate["dispersion"],
                    candidate["phase_data"]["frequency"], tuple(tuple(turns) for turns in candidate["turns"]))
        distinct[identity] = candidate
    finalist_pool = list(distinct.values())
    finalist_pool.sort(key=lambda item: item["intensity_loss"])
    best = finalist_pool[0]
    save_candidate(best)
    refinement_deadline = min(deadline + 9.0, time.monotonic() + 9.0)
    for initial in finalist_pool[:3]:
        if time.monotonic() >= refinement_deadline:
            break
        refined = refine(fold, initial, refinement_deadline)
        if refined["intensity_loss"] < best["intensity_loss"]:
            best = refined
            save_candidate(best)
    close = [item["thickness"] for item in finalist_pool
             if item["intensity_loss"] <= best["intensity_loss"] * 1.05 + 1e-9]
    close.append(best["thickness"])
    return best, {"折号": fold["spec"]["折号"], "载频初始化_cm": [item["frequency"] for item in starts],
                  "实际粗候选数": evaluated, "粗搜索完整": completed,
                  "每角训练点数": len(fold["indices"]), "每角连通段数": len(fold["groups"]),
                  "训练非训练边界_cm^-1": fold["guard_edges"], "离散情景条件估计": scenario_records,
                  "训练重建损失近优5%条件厚度跨度_um": [min(close), max(close)],
                  "跨度含义": "有限载频、整数和光学情景候选的敏感性跨度；不是置信区间。"}


def alias_check(fold, candidate):
    result = {"原训练坐标最大相邻相位差_rad": 0.0, "小样训练最大相邻相位差_rad": 0.0}
    for angle in ANGLES:
        for key, groups in (("原训练坐标最大相邻相位差_rad", fold["full_groups"]),
                            ("小样训练最大相邻相位差_rad", [[fold["angles"][0]["sigma"][position] for position in group] for group in fold["groups"]])):
            for coordinates in groups:
                phases = [candidate["thickness"] * phase_rate(coordinate, angle, candidate["reference"], candidate["dispersion"]) for coordinate in coordinates]
                result[key] = max(result[key], max((right - left for left, right in zip(phases, phases[1:])), default=0.0))
    result["小样混叠风险"] = result["小样训练最大相邻相位差_rad"] >= math.pi
    result["奈奎斯特相位上限_rad"] = math.pi
    result["口径"] = "仅使用完整训练坐标和训练拟合相位；候选核验不是整个搜索盒的无混叠证明。"
    result["风险动作"] = "有风险时仍保留答案并标记资格存疑，需三路线共同增至960点，不能单独改密度。"
    return result


def geometry_values(candidate, offset_changes=(0.0, 0.0)):
    angle_values = []
    for angle_index, groups in enumerate(candidate["phase_data"]["angles"]):
        values = {}
        for segment, points in enumerate(groups):
            for point in points:
                if point["weight"] <= 0:
                    continue
                absolute_phase = point["phase"] + TAU * candidate["turns"][angle_index][segment] - candidate["offsets"][angle_index] - offset_changes[angle_index]
                values[point["position"]] = absolute_phase / (TAU * point["sigma"])
        angle_values.append(values)
    positions = sorted(set(angle_values[0]) & set(angle_values[1]))
    differences = [angle_values[0][position] ** 2 - angle_values[1][position] ** 2 for position in positions]
    if not differences:
        return {"共同有效点数": 0, "平方差中位数_cm2": None, "正平方差比例": None, "正差条件厚度中位数_um": None}
    denominator = 4 * (math.sin(math.radians(15)) ** 2 - math.sin(math.radians(10)) ** 2)
    positive = [math.sqrt(value / denominator) * 10000 for value in differences if value > 0]
    return {"共同有效点数": len(positions), "平方差中位数_cm2": quantile(differences, 0.5),
            "平方差四分位距_cm2": quantile(differences, 0.75) - quantile(differences, 0.25),
            "正平方差比例": len(positive) / len(differences),
            "正差条件厚度中位数_um": quantile(positive, 0.5) if positive else None,
            "口径": "正差厚度只是指定相位偏置下的诊断，负差点完整计入正差比例，不筛为可报告绝对厚度。"}


def diagnostics(fold, candidate, deadline):
    tests = []
    base = geometry_values(candidate)
    for angle_index in range(2):
        for offset in (-TAU, -math.pi / 6, math.pi / 6, TAU):
            shifts = [0.0, 0.0]
            shifts[angle_index] = offset
            tests.append({"扰动角度_度": ANGLES[angle_index], "偏置扰动_rad": offset,
                          "仅整周平移时强度完全等价": abs(offset) == TAU, **geometry_values(candidate, shifts)})
    perturbations = []
    for kind, value in (("窗口", 12.0), ("窗口", 16.0), ("幅值门槛", 0.025), ("幅值门槛", 0.10), ("噪声", 0.05), ("噪声", 0.10)):
        if time.monotonic() >= deadline:
            break
        perturbed = None
        if kind == "噪声":
            generator = random.Random(SEED + fold["spec"]["折号"] * 100 + int(value * 1000))
            perturbed = [[residual + generator.gauss(0.0, value * data["scale"]) for residual in data["residual"]]
                         for data in fold["angles"]]
        try:
            phase_data = demodulate(fold, candidate["phase_data"]["frequency"],
                                    window=value if kind == "窗口" else 20.0,
                                    amplitude_cutoff=value if kind == "幅值门槛" else 0.05, perturbation=perturbed)
            trial = candidate_from_phase(phase_data, candidate["reference"], candidate["dispersion"], candidate["thickness"], initial_turns=candidate["turns"])
            perturbations.append({"检验": kind, "扰动值": value, "条件厚度_um": trial["thickness"],
                                  "相对厚度变化": abs(trial["thickness"] / candidate["thickness"] - 1),
                                  "几何平方差": geometry_values(trial)})
        except ArithmeticError as error:
            perturbations.append({"检验": kind, "扰动值": value, "失败原因": str(error)})
    finite_changes = [entry["相对厚度变化"] for entry in perturbations if "相对厚度变化" in entry]
    alias = alias_check(fold, candidate)
    return {"折号": fold["spec"]["折号"], "双角正弦平方差": math.sin(math.radians(15)) ** 2 - math.sin(math.radians(10)) ** 2,
            "混叠检查": alias, "基准几何诊断": base, "常偏置敏感性": tests,
            "局部窗口幅值门槛与训练噪声": perturbations, "已完成扰动数": len(perturbations), "计划扰动数": 6,
            "扰动条件厚度最大相对变化": max(finite_changes) if finite_changes else None,
            "噪声含义": "向训练去基线值加独立高斯扰动，标准差取训练归一化尺度的5%和10%；不是仪器噪声估计，不进入排名。",
            "无折射率绝对厚度结论": "未识别；只保留条件厚度，不把相位斜率或群厚度用于几何消参。",
            "判别所需补充条件": "需可核验的绝对界面相位或条纹级次锚，并验证色散及基线；两角强度对各自偏置整周平移完全不敏感。",
            "稳定性解释": "独立改变某角偏置2π不会改变强度，却改变L平方差，因此条件拟合给出的偏置不是外部绝对相位证据。"}


def worker(t0):
    result = initial_result()
    candidates = []
    folds = []
    try:
        coordinates, values, full_coordinates = read_data(result)
        checkpoint(result, t0)
        folds = [make_fold(coordinates, values, full_coordinates, specification) for specification in result["折分"]]
        preflight = []
        for fold in folds:
            for scenario in result["参数情景"]:
                if valid_optics(scenario["参考折射率"], scenario["色散系数"]):
                    probe = {"thickness": 40.0, "reference": scenario["参考折射率"], "dispersion": scenario["色散系数"]}
                    preflight.append({"折号": fold["spec"]["折号"], "检查厚度上界_um": 40.0,
                                      **scenario, **alias_check(fold, probe)})
        result["辅助诊断"]["解调前九情景抽稀预检查"] = preflight
        checkpoint(result, t0)
        start_pools = []
        for fold in folds:
            starts = carriers(fold, min(t0 + 30, t0 + SOFT_SECONDS - 12))
            start_pools.append(starts)
            phase_data = demodulate(fold, starts[0]["frequency"])
            seed = starts[0]["frequency"] * 10000 / (2 * math.sqrt(3 ** 2 - math.sin(math.radians(10)) ** 2))
            candidate = reconstruct(fold, candidate_from_phase(phase_data, 3.0, 0.0, seed))
            candidates.append(candidate)
            result["厚度及单位"] = [candidate_record(item, index + 1) for index, item in enumerate(candidates)]
            result["分块预测与残差"].extend(score_fold(fold, candidate))
            checkpoint(result, t0)
        refresh_score(result, folds, candidates)
        result["运行状态"] = "已完成双折初始相位答案，继续有限搜索"
        result["辅助诊断"]["初始混叠检查"] = [alias_check(fold, candidate) for fold, candidate in zip(folds, candidates)]
        checkpoint(result, t0)
        for fold_index, fold in enumerate(folds):
            remaining_folds = len(folds) - fold_index
            remaining_search = max(0.0, min(t0 + 120, t0 + SOFT_SECONDS - 22) - time.monotonic())
            deadline = min(t0 + SOFT_SECONDS - 24, time.monotonic() + max(0.0, remaining_search / remaining_folds - 9))

            def save_candidate(candidate):
                candidates[fold_index] = candidate
                refresh_score(result, folds, candidates)
                checkpoint(result, t0)

            if time.monotonic() >= deadline:
                result["辅助诊断"]["分折"].append({"折号": fold_index + 1, "搜索提前停止": True, "说明": "保留已计算的双折初始相位答案，未减少计分块。"})
                continue
            best, report = search_fold(fold, start_pools[fold_index], candidates[fold_index], result["参数情景"], deadline, save_candidate)
            candidates[fold_index] = best
            result["辅助诊断"]["分折"].append(report)
            checkpoint(result, t0)
        refresh_score(result, folds, candidates)
        result["运行状态"] = "共同八块评分已完成"
        checkpoint(result, t0)
        for fold, candidate in zip(folds, candidates):
            if time.monotonic() >= t0 + SOFT_SECONDS - 5:
                result["失败原因"].append("停止新增辅助诊断；八块主指标已完成，未以部分指标排名。")
                break
            report = diagnostics(fold, candidate, t0 + SOFT_SECONDS - 5)
            result["辅助诊断"]["病态诊断"].append(report)
            if report["混叠检查"]["小样混叠风险"]:
                result["用于同口径排名"] = False
                result["失败原因"].append("拟合相位触发抽稀混叠风险，保留数值，但共同密度需重新核验。")
            checkpoint(result, t0)
        result["辅助诊断"]["折间一致性"] = {
            "两折条件厚度_um": [candidate["thickness"] for candidate in candidates],
            "两折主指标分项": [sum(block["标准化均方根误差"] for block in result["分块预测与残差"] if block["折号"] == fold_number) / 4 for fold_number in (1, 2)],
            "说明": "同一主指标按相反留段方向拆分，非另设加权排名分数；无真实厚度真值。"}
        result["运行状态"] = "完成；条件相位厚度与完整留段评分已计算"
        checkpoint(result, t0)
        append_experiment({"类别": "科学尝试", "问题": 2,
                           "尝试": "对双角连续相位施加共同经验色散、整段整数及各角常偏置约束，并重建独立留段强度",
                           "现象": f"每角480点、两折共8个测试块，标准化均方根误差{result['主指标值']:.8g}，经验预测覆盖率{result['经验覆盖率']['测试经验覆盖率']:.6g}",
                           "决定": "保留条件厚度与完整留段分数，绝对几何消参需额外界面相位锚；不由测试残差重调拟合",
                           "依据": "求解结果:主指标值；求解结果:经验覆盖率；求解结果:厚度及单位；求解结果:辅助诊断"})
        return 0
    except Exception as error:
        complete = len(candidates) == 2 and len(result["分块预测与残差"]) == 8 and result["主指标值"] is not None
        result["运行状态"] = "主指标已完成，后续诊断中止" if complete else "未完成共同计分任务"
        result["失败原因"].append(f"{type(error).__name__}: {error}")
        if not complete:
            result["核心指标"] = {}
            result["主指标值"] = None
            result["用于同口径排名"] = False
        checkpoint(result, t0)
        try:
            append_experiment({"类别": "流程事件", "问题": 2, "尝试": "运行问题二双角相位回归原型",
                               "现象": f"{type(error).__name__}: {error}", "决定": "保留已经完成的真实输出；八块不足时不排名",
                               "依据": "本次原型实际异常与完成块数"})
        except Exception:
            pass
        return 0 if complete else 1


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        return worker(float(sys.argv[2]))
    if len(sys.argv) != 1:
        raise SystemExit("用法：python3 求解/问题2/原型_路线3.py")
    t0 = PROCESS_START
    result = initial_result()
    checkpoint(result, t0)
    failure = None
    try:
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(t0)], cwd=ROOT,
                                   timeout=max(0.1, HARD_SECONDS - 2 - (time.monotonic() - t0)), check=False)
        if completed.returncode:
            failure = f"子进程退出码{completed.returncode}"
    except subprocess.TimeoutExpired:
        failure = "175秒总限时内预留2秒收尾后终止子进程；本次不得以部分进度排名。"
    result = json.loads(OUTPUT.read_text(encoding="utf-8"))
    if failure:
        result["已完成核心指标备查"] = result["核心指标"]
        result["运行状态"] = "未完成"
        result["核心指标"] = {}
        result["主指标值"] = None
        result["用于同口径排名"] = False
        result["失败原因"].append(failure)
        try:
            append_experiment({"类别": "流程事件", "问题": 2, "尝试": "父进程监督三分钟原型",
                               "现象": failure, "决定": "保留已完成结果备查，不报告部分均值", "依据": "实际进程状态和单调时钟"})
        except Exception as error:
            result["失败原因"].append(f"追加实验记录失败: {error}")
    checkpoint(result, t0)
    print(json.dumps({"路线名": result["路线名"], "运行状态": result["运行状态"],
                      "使用附件": result["使用附件"], "每角度点数": len(result["样本索引"]),
                      "核心指标": result["核心指标"], "条件厚度": result["厚度及单位"],
                      "经验覆盖率": result["经验覆盖率"], "实际用时秒": result["实际用时秒"],
                      "失败原因": result["失败原因"]}, ensure_ascii=False, allow_nan=False), flush=True)
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
