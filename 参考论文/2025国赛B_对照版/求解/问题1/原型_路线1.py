"""色散峰序反演小样；仅直接运行本文件时求解，不依赖第三方包。

共同合成样本使用附件原坐标；实测反射率只作离散极值适用性诊断。
透明、空气折射率为1时，Snell定律给出q=sqrt(n**2-sin(theta)**2)。
一次往返相位为4*pi*d_cm*sigma*q；峰、谷半周期级次分别取偶、奇数。
因此h=4*d_cm*(sigma*q)+b_angle，共享斜率乘2500得到厚度微米。
相比直接用sigma的峰距法，这里用sigma*q，并在动态规划中允许漏级。
"""

from time import perf_counter, sleep

STARTED = perf_counter()

from hashlib import sha256
from itertools import product
from pathlib import Path
from statistics import mean, median
from zipfile import ZipFile
import fcntl
import json
import math
import os
import random
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "求解/问题1/原型结果/路线1.json"
SOFT_SECONDS = 165.0
HARD_SECONDS = 175.0
DEPTH_LIMITS = (0.5, 40.0)
SEED = 20260909
NS = {"表": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


class BudgetExceeded(Exception):
    pass


def check_budget(started):
    if perf_counter() - started >= SOFT_SECONDS:
        raise BudgetExceeded("达到165秒软截止，保留已完成结果")


def write_json(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(content, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def append_experiment(entry):
    path = ROOT / "交接/实验记录.json"
    with (ROOT / "交接/.实验记录.lock").open("a", encoding="utf-8") as lock:
        deadline = perf_counter() + 0.5
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if perf_counter() >= deadline:
                    return False
                sleep(0.01)
        records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(records, list):
            raise ValueError("实验记录顶层必须为数组")
        records.append(entry)
        write_json(path, records)
    return True


def read_attachment(entry, started):
    path = ROOT / entry["相对路径"]
    payload = path.read_bytes()
    digest = sha256(payload).hexdigest()
    if digest != entry["文件哈希"]:
        raise ValueError(f"{path.name}与数据档案哈希不一致")
    sheet = next(item for item in entry["工作表"] if item["名称"] == "Sheet1")
    records = []
    with ZipFile(path) as archive, archive.open(sheet["内部路径"]) as handle:
        for _, element in ET.iterparse(handle, events=("end",)):
            if element.tag != "{" + NS["表"] + "}row":
                continue
            row_number = int(element.get("r"))
            if row_number == 1:
                element.clear()
                continue
            values = {}
            for cell in element.findall("表:c", NS):
                column = cell.get("r").rstrip("0123456789")
                value = cell.find("表:v", NS)
                if column in ("A", "B") and value is not None:
                    if cell.get("t", "n") != "n":
                        raise ValueError(f"{path.name}第{row_number}行存在非数值")
                    values[column] = float(value.text)
            if set(values) != {"A", "B"} or not all(map(math.isfinite, values.values())):
                raise ValueError(f"{path.name}第{row_number}行不完整")
            records.append((row_number, values["A"], values["B"] / 100.0))
            element.clear()
            if len(records) % 256 == 0:
                check_budget(started)
    if len(records) != 7469 or any(left[1] >= right[1] for left, right in zip(records, records[1:])):
        raise ValueError("附件行数或严格递增坐标与共同小样约定不符")
    return records, digest


def solve_three(matrix, target):
    augmented = [list(row) + [value] for row, value in zip(matrix, target)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-14:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(3):
            if row != column:
                multiplier = augmented[row][column]
                augmented[row] = [left - multiplier * right for left, right in zip(augmented[row], augmented[column])]
    return [row[3] for row in augmented]


def quadratic(coordinates, values, center, width):
    radius = width // 2
    if center < radius or center + radius >= len(values):
        return None
    scale = (coordinates[center + 1] - coordinates[center - 1]) / 2
    local = [(coordinates[index] - coordinates[center]) / scale for index in range(center - radius, center + radius + 1)]
    powers = [sum(value ** degree for value in local) for degree in range(5)]
    matrix = [[powers[row + column] for column in range(3)] for row in range(3)]
    target = [sum(value ** degree * values[index] for value, index in zip(local, range(center - radius, center + radius + 1))) for degree in range(3)]
    coefficients = solve_three(matrix, target)
    return (coefficients, scale) if coefficients is not None else None


def find_events(coordinates, reflectance, width=3, measured=False):
    signal = list(reflectance)
    if width == 5:
        for center in range(2, len(signal) - 2):
            if measured and coordinates[center + 2] - coordinates[center - 2] > 20:
                continue
            fitted = quadratic(coordinates, reflectance, center, 5)
            if fitted is not None:
                signal[center] = fitted[0][0]
    differences = [reflectance[index + 1] - 2 * reflectance[index] + reflectance[index - 1] for index in range(1, len(signal) - 1)]
    location = median(differences)
    noise = median(abs(value - location) for value in differences) / (0.67449 * math.sqrt(6))
    dynamic_range = max(reflectance) - min(reflectance)
    threshold = max(2.5 * noise, 0.015 * dynamic_range, 1e-10)
    events = []
    discarded = 0
    for center in range(1, len(signal) - 1):
        maximum = signal[center] > signal[center - 1] and signal[center] >= signal[center + 1]
        minimum = signal[center] < signal[center - 1] and signal[center] <= signal[center + 1]
        if not maximum and not minimum:
            continue
        local_width = width if center >= width // 2 and center + width // 2 < len(signal) else 3
        if measured and coordinates[center + local_width // 2] - coordinates[center - local_width // 2] > 20:
            local_width = 3
        fitted = quadratic(coordinates, reflectance, center, local_width)
        if fitted is None:
            continue
        coefficients, scale = fitted
        curvature = 2 * coefficients[2] / scale ** 2
        if abs(coefficients[2]) < 1e-15 or (curvature < 0) != maximum:
            discarded += 1
            continue
        offset = -coefficients[1] / (2 * coefficients[2])
        position = coordinates[center] + offset * scale
        if not coordinates[center - 1] <= position <= coordinates[center + 1]:
            discarded += 1
            continue
        radius = 8 if not measured else 1
        left = signal[max(0, center - radius):center]
        right = signal[center + 1:min(len(signal), center + radius + 1)]
        prominence = min(signal[center] - min(left), signal[center] - min(right)) if maximum else min(max(left) - signal[center], max(right) - signal[center])
        if prominence < threshold and not measured:
            discarded += 1
            continue
        events.append({
            "波数_cm^-1": position,
            "原坐标_cm^-1": coordinates[center],
            "小样索引": center,
            "类型": "峰" if maximum else "谷",
            "奇偶": 0 if maximum else 1,
            "曲率_反射率比例每波数平方": curvature,
            "局部显著度_反射率比例": prominence,
            "权重": min(1.0, max(0.1, prominence / max(0.12 * dynamic_range, 1e-10))),
        })
    return events, {"局部窗口点数": width, "峰数": sum(item["奇偶"] == 0 for item in events), "谷数": sum(item["奇偶"] == 1 for item in events), "剔除极值数": discarded, "二阶差分尺度_反射率比例": noise, "尺度说明": "仅为固定极值筛选尺度，混合曲率和相关噪声，不是仪器噪声测量"}


def interface(incident_index, transmitted_index, sine, polarization):
    incident_cosine = math.sqrt(1 - (sine / incident_index) ** 2)
    transmitted_cosine = math.sqrt(1 - (sine / transmitted_index) ** 2)
    if polarization == "横电":
        denominator = incident_index * incident_cosine + transmitted_index * transmitted_cosine
        reflection = (incident_index * incident_cosine - transmitted_index * transmitted_cosine) / denominator
    else:
        denominator = transmitted_index * incident_cosine + incident_index * transmitted_cosine
        reflection = (transmitted_index * incident_cosine - incident_index * transmitted_cosine) / denominator
    transmission = 2 * incident_index * incident_cosine / denominator
    return reflection, transmission


def synthetic_cases(coordinates, config):
    generator = random.Random(config["随机种子"])
    combinations = product(config["厚度情景微米"], config["折射率基值情景"], config["色散斜率情景"], config["噪声标准差情景"])
    for case_number, (true_depth, index_base, dispersion, noise_sd) in enumerate(combinations, 1):
        spectra = []
        for angle in config["角度度"]:
            sine = math.sin(math.radians(angle))
            noise = generator.gauss(0, noise_sd) if noise_sd else 0.0
            values = []
            indices = []
            for position, wavenumber in enumerate(coordinates):
                refractive_index = index_base + dispersion * (wavenumber - 2200) / 1800
                substrate_index = refractive_index + 0.8
                phase = 4 * math.pi * true_depth / 10000 * wavenumber * math.sqrt(refractive_index ** 2 - sine ** 2)
                intensity = 0.0
                for polarization in ("横电", "横磁"):
                    surface, inward = interface(1.0, refractive_index, sine, polarization)
                    _, outward = interface(refractive_index, 1.0, sine, polarization)
                    bottom, _ = interface(refractive_index, substrate_index, sine, polarization)
                    delayed = inward * outward * bottom
                    intensity += 0.5 * (surface ** 2 + delayed ** 2 + 2 * surface * delayed * math.cos(phase))
                if position and noise_sd:
                    noise = 0.6 * noise + math.sqrt(1 - 0.6 ** 2) * generator.gauss(0, noise_sd)
                values.append(intensity + noise)
                indices.append(refractive_index)
            spectra.append({"角度度": angle, "坐标": coordinates, "反射率": values, "折射率": indices})
        yield case_number, spectra, {"真厚度_微米": true_depth, "折射率基值": index_base, "色散斜率": dispersion, "噪声标准差_反射率比例": noise_sd}


def phase_coordinate(event, spectrum, constant_index):
    center = event["小样索引"]
    coordinates = spectrum["坐标"]
    indices = spectrum["折射率"]
    derivative = (indices[min(center + 1, len(indices) - 1)] - indices[max(center - 1, 0)]) / (coordinates[min(center + 1, len(indices) - 1)] - coordinates[max(center - 1, 0)])
    refractive_index = constant_index if constant_index is not None else indices[center] + derivative * (event["波数_cm^-1"] - coordinates[center])
    return event["波数_cm^-1"] * math.sqrt(refractive_index ** 2 - math.sin(math.radians(spectrum["角度度"])) ** 2)


def match_orders(events, depth):
    if not events:
        return [], 0.0
    origin = events[0]["相位坐标_cm^-1"]
    slope = depth / 2500
    cosine = sum(item["权重"] * math.cos(math.pi * (item["奇偶"] - slope * (item["相位坐标_cm^-1"] - origin))) for item in events)
    sine = sum(item["权重"] * math.sin(math.pi * (item["奇偶"] - slope * (item["相位坐标_cm^-1"] - origin))) for item in events)
    offset = math.atan2(sine, cosine) / math.pi
    states = {}
    best_node = None
    for event in events:
        prediction = slope * (event["相位坐标_cm^-1"] - origin) + offset
        order = int(2 * round((prediction - event["奇偶"]) / 2) + event["奇偶"])
        residual = order - prediction
        reward = event["权重"] * (1.0 - (residual / 0.38) ** 2)
        predecessors = [states[previous] for previous in range(order - 6, order) if previous in states]
        predecessor = max(predecessors, key=lambda node: node[0] - 0.18 * (order - node[1] - 1), default=None)
        inherited = predecessor[0] - 0.18 * (order - predecessor[1] - 1) if predecessor else 0.0
        if inherited < 0:
            predecessor = None
            inherited = 0.0
        node = (inherited + reward, order, event, predecessor)
        if node[0] > 0 and (order not in states or node[0] > states[order][0]):
            states[order] = node
            if best_node is None or node[0] > best_node[0]:
                best_node = node
    matched = []
    node = best_node
    while node is not None:
        matched.append((node[2], node[1]))
        node = node[3]
    matched.reverse()
    return matched, best_node[0] if best_node else 0.0


def joint_regression(matches, initial_depth):
    matches = [matched if max((sum(item["奇偶"] == parity for item, order in matched) for parity in (0, 1)), default=0) >= 2 else [] for matched in matches]
    weights = [[item[0]["权重"] for item in matched] for matched in matches]
    depth = initial_depth
    for _ in range(4):
        numerator = 0.0
        denominator = 0.0
        centers = []
        for matched, local_weights in zip(matches, weights):
            weight_sum = sum(local_weights)
            if len(matched) < 2 or weight_sum <= 0:
                centers.append((0.0, 0.0))
                continue
            coordinate_mean = sum(weight * item["相位坐标_cm^-1"] for (item, order), weight in zip(matched, local_weights)) / weight_sum
            order_mean = sum(weight * order for (item, order), weight in zip(matched, local_weights)) / weight_sum
            centers.append((coordinate_mean, order_mean))
            numerator += sum(weight * (item["相位坐标_cm^-1"] - coordinate_mean) * (order - order_mean) for (item, order), weight in zip(matched, local_weights))
            denominator += sum(weight * (item["相位坐标_cm^-1"] - coordinate_mean) ** 2 for (item, order), weight in zip(matched, local_weights))
        if denominator <= 1e-12:
            break
        candidate = 2500 * numerator / denominator
        if not math.isfinite(candidate) or not DEPTH_LIMITS[0] <= candidate <= DEPTH_LIMITS[1]:
            break
        depth = candidate
        residual_groups = [[order - center[1] - depth / 2500 * (item["相位坐标_cm^-1"] - center[0]) for item, order in matched] for matched, center in zip(matches, centers)]
        absolute = [abs(value) for group in residual_groups for value in group]
        scale = max(0.03, 1.4826 * median(absolute)) if absolute else 0.03
        weights = [[item["权重"] * min(1.0, 1.5 * scale / max(abs(residual), 1e-12)) for (item, order), residual in zip(matched, residuals)] for matched, residuals in zip(matches, residual_groups)]
    return depth


def estimate_depth(spectra, started, width=3, fixed_events=None, constant=False):
    groups = []
    detections = []
    raw_events = []
    for position, spectrum in enumerate(spectra):
        if fixed_events is None:
            events, detection = find_events(spectrum["坐标"], spectrum["反射率"], width)
        else:
            events = fixed_events[position]
            detection = {"固定同一极值集合": True}
        raw_events.append(events)
        constant_index = mean(spectrum["折射率"]) if constant else None
        group = [dict(item, **{"相位坐标_cm^-1": phase_coordinate(item, spectrum, constant_index)}) for item in events]
        if any(left["相位坐标_cm^-1"] >= right["相位坐标_cm^-1"] for left, right in zip(group, group[1:])):
            raise ValueError("色散相位坐标不单调，不满足峰序反演条件")
        groups.append(group)
        detections.append(detection)
    seeds = [DEPTH_LIMITS[0] + (DEPTH_LIMITS[1] - DEPTH_LIMITS[0]) * position / 95 for position in range(96)]
    candidates = []
    for depth in seeds:
        check_budget(started)
        matches_and_scores = [match_orders(group, depth) for group in groups]
        candidates.append((sum(item[1] for item in matches_and_scores), depth, [item[0] for item in matches_and_scores]))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    refined = []
    for _, depth, matches in candidates[:3]:
        for _ in range(12):
            check_budget(started)
            updated_depth = joint_regression(matches, depth)
            matched = [match_orders(group, updated_depth) for group in groups]
            refined.append((sum(item[1] for item in matched), updated_depth, [item[0] for item in matched]))
            matches = [item[0] for item in matched]
            if abs(updated_depth - depth) <= 1e-7:
                break
            depth = updated_depth
    best = max(candidates[:3] + refined, key=lambda item: (item[0], -item[1]))
    _, depth, matches = best
    traces = []
    failure_reasons = []
    for spectrum, group, matched, detection in zip(spectra, groups, matches, detections):
        counts = [sum(item["奇偶"] == parity for item, order in matched) for parity in (0, 1)]
        if max(counts, default=0) < 2:
            failure_reasons.append(f"{spectrum['角度度']}度有效同型极值不足；不以一峰一谷冒充同型峰距")
        gaps = [right[1] - left[1] for left, right in zip(matched, matched[1:])]
        residuals = []
        if matched:
            intercept = mean(order - depth / 2500 * event["相位坐标_cm^-1"] for event, order in matched)
            residuals = [order - depth / 2500 * event["相位坐标_cm^-1"] - intercept for event, order in matched]
            if max(map(abs, residuals), default=0) > 0.25:
                failure_reasons.append(f"{spectrum['角度度']}度级次偏离超过四分之一半周期，可能有包络偏移或定位误差")
        traces.append({"角度度": spectrum["角度度"], "检测": detection, "候选极值数": len(group), "接受峰数": counts[0], "接受谷数": counts[1], "未接受数": len(group) - len(matched), "半周期级次跳量": gaps, "漏级约束成立": all(1 <= gap <= 6 for gap in gaps), "奇偶约束成立": all(order % 2 == event["奇偶"] for event, order in matched), "接受极值": [dict(event, **{"半周期整数级次": order, "半周期级次残差": residual}) for (event, order), residual in zip(matched, residuals)]})
    if depth in DEPTH_LIMITS:
        failure_reasons.append("厚度命中数值搜索边界，不作精确识别解释")
    alternatives = [item[1] for item in sorted(refined or candidates[:3], key=lambda item: -item[0])[:3]]
    return {"厚度_微米": depth, "搜索候选厚度_微米": alternatives, "候选说明": "前三内部目标候选不是概率区间，不作为第二个评分", "角度诊断": traces, "失败原因": failure_reasons, "条件": "给定合成折射率曲线，透明两束模型；不代表实测材料已标定", "无充分极值时": "保留有界离散优化器返回的正候选及失败标记，不删除该例或宣称可信识别"}, raw_events


def score_cases(cases, expected=24):
    if len(cases) != expected or any(not math.isfinite(item["厚度_微米"]) or item["厚度_微米"] <= 0 for item in cases):
        return None
    return 100 * mean(abs(item["厚度_微米"] - item["合成真值"]["真厚度_微米"]) / item["合成真值"]["真厚度_微米"] for item in cases)


def unit_diagnostics():
    reference_depth = 7.25
    same_type_coordinate_gap = 5000 / reference_depth
    opposite_type_coordinate_gap = 2500 / reference_depth
    phase_jump = 4 * math.pi * reference_depth / 10000 * same_type_coordinate_gap
    recovered_same = 2500 * 2 / same_type_coordinate_gap
    recovered_opposite = 2500 / opposite_type_coordinate_gap
    maximum_error = max(abs(recovered_same - reference_depth), abs(recovered_opposite - reference_depth))
    return {"用途": "独立量纲恒等式核对，不调用合成真厚度选择参数", "核对厚度_微米": reference_depth, "峰峰相位差_弧度": phase_jump, "与两倍圆周率绝对差": abs(phase_jump - 2 * math.pi), "厚度换算最大绝对误差_微米": maximum_error, "检查通过": maximum_error < 1e-12 and abs(phase_jump - 2 * math.pi) < 1e-12}


def refresh(result, started):
    value = score_cases(result["逐例结果"])
    result["主指标值"] = value
    result["核心指标键值"] = {"合成厚度平均相对误差_%": value}
    result["实际用时秒"] = perf_counter() - started
    result["耗时秒"] = result["实际用时秒"]
    write_json(OUTPUT, result)


def initial_result():
    return {
        "问题": 1, "路线编号": "一甲", "路线名": "色散峰序反演", "运行状态": "运行中",
        "主指标名称": "合成厚度平均相对误差", "主指标单位": "%", "主指标值": None,
        "核心指标键值": {"合成厚度平均相对误差_%": None},
        "指标含义": "100乘24例厚度绝对相对误差的等权平均；越小越好；不删失败或少峰例",
        "用时估计": {"预计秒": 120, "性质": "规模设计估计，非实测速度保证", "内部软截止秒": SOFT_SECONDS, "外层强制截止秒": HARD_SECONDS},
        "口径说明": "共同24例评分；真实附件1、2提供原始坐标并另用480点真实反射率检查离散极值。不存在实测厚度真值，不计算伪造的实测厚度误差。",
        "输入哈希": {}, "使用附件": ["附件1.xlsx", "附件2.xlsx"], "样本索引": {},
        "随机种子": SEED, "生成顺序": "厚度、折射率基值、色散斜率、噪声标准差依次作笛卡尔积；每例10度后15度；零噪声不消耗随机数；首噪声为平稳高斯值，其余按0.6递推",
        "折分": {"状态": "不适用", "原因": "问题一按固定合成情景评分，不以真厚度选择参数；实测仅作无预测评分的诊断，不借用问题二的留段指标"},
        "分块预测与残差": {"状态": "不适用", "原因": "本路线只拟合离散事件，不以逐点反射率预测参与问题一评分"},
        "经验覆盖率": {"状态": "不适用", "值": None, "原因": "共同协议明确本步不生成厚度区间；报告逐例平滑及常折射率敏感性，候选表不是置信区间"},
        "厚度及单位": {"单位": "微米", "位置": "逐例结果/厚度_微米", "相位公式": "半周期级次=4*d_cm*sigma*sqrt(n(sigma)^2-sin(theta)^2)+角度截距", "单位检查": "回归斜率单位cm；微米厚度=2500*半周期级次对色散坐标的斜率；峰峰差为2、峰谷差为1"},
        "参数情景": {}, "逐例结果": [], "辅助诊断": {}, "失败原因": [],
        "方法卡片取舍": "不采用预测、树模型、层级调和或共形区间；本问无厚度真值训练集。整数序列用有限状态动态规划，逐例回代奇偶和漏级约束；不使用随机启发式。",
        "固定超参数": {"厚度粗点数": 96, "精修起点数": 3, "每起点更新轮数上限": 12, "稳健回归轮数": 4, "半周期级次最大跳量": 6, "漏半周期惩罚": 0.18, "半周期定位容差": 0.38, "主定位窗口点数": 3, "对照定位窗口点数": 5, "合成显著度邻域单侧点数": 8, "选取口径": "在计算24例真值误差前固定，未按误差调优"},
    }


def worker(started):
    result = initial_result()
    refresh(result, started)
    try:
        scouting = json.loads((ROOT / "交接/路线侦察.json").read_text(encoding="utf-8"))
        dossier = json.loads((ROOT / "交接/数据档案.json").read_text(encoding="utf-8"))
        config = scouting["共同原型协议"]["合成小样"]
        if config["组合数"] != 24 or config["随机种子"] != SEED:
            raise ValueError("共同合成协议已变化，不能静默沿用原设置")
        result["参数情景"] = config
        result["输入哈希"]["路线侦察"] = sha256((ROOT / "交接/路线侦察.json").read_bytes()).hexdigest()
        result["输入哈希"]["数据档案"] = sha256((ROOT / "交接/数据档案.json").read_bytes()).hexdigest()
        attachments = [next(item for item in dossier["文件档案"] if item["文件名"] == filename) for filename in result["使用附件"]]
        datasets = []
        for entry in attachments:
            records, digest = read_attachment(entry, started)
            datasets.append(records)
            result["输入哈希"][entry["文件名"]] = digest
        if [item[1] for item in datasets[0]] != [item[1] for item in datasets[1]]:
            raise ValueError("同片两角度原始坐标未对齐")
        selected = [position * 7468 // 255 for position in range(256)]
        coordinates = [datasets[0][position][1] for position in selected]
        result["样本索引"]["合成原始零基索引"] = selected
        result["样本索引"]["合成原始行号"] = [datasets[0][position][0] for position in selected]
        result["样本索引"]["合成波数_cm^-1"] = coordinates
        available = [position for position, record in enumerate(datasets[0]) if 1200 <= record[1] <= 3800]
        measured_indices = [available[position * (len(available) - 1) // 479] for position in range(480)]
        result["样本索引"]["实测原始零基索引"] = measured_indices
        measured_diagnostics = []
        for entry, records in zip(attachments, datasets):
            panel = [records[position] for position in measured_indices]
            events, diagnosis = find_events([row[1] for row in panel], [row[2] for row in panel], measured=True)
            measured_diagnostics.append({"源附件": entry["文件名"], "材料": entry["材料"], "角度度": entry["入射角度"], "样本数": len(panel), "诊断": diagnosis, "原始小样": [{"原始行号": row_number, "波数_cm^-1": coordinate, "反射率_比例": value, "质量标记": ["零反射率"] if value == 0 else (["反射率超过百分之百"] if value > 1 else [])} for row_number, coordinate, value in panel], "极值前八项": events[:8], "用途": "实际反射率的离散极值可用性检查；不把未知折射率常数补成真值"})
        result["辅助诊断"]["实测小样"] = measured_diagnostics
        result["辅助诊断"]["单位与峰谷系数核对"] = unit_diagnostics()
        result["辅助诊断"]["异常交集"] = "1200—3800 cm^-1窗口与399.6747首点及801.278—927.1104超百区段无交集；未声称已完成异常敏感性检验"
        refresh(result, started)
        cached = []
        fingerprints = []
        for case_number, spectra, truth in synthetic_cases(coordinates, config):
            check_budget(started)
            estimate, events = estimate_depth(spectra, started)
            estimate["例号"] = case_number
            estimate["合成真值"] = truth
            estimate["敏感性"] = {"状态": "尚未执行可选诊断"}
            result["逐例结果"].append(estimate)
            fingerprints.append(sha256(json.dumps(spectra, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest())
            cached.append((spectra, events))
            refresh(result, started)
            print(f"路线一：完成{case_number}/24，厚度候选={estimate['厚度_微米']:.8g}微米，失效标记={len(estimate['失败原因'])}", flush=True)
        result["输入哈希"]["逐例合成样本"] = fingerprints
        result["运行状态"] = "主评分完成，进行不参与排名的敏感性诊断"
        refresh(result, started)
        for estimate, (spectra, events) in zip(result["逐例结果"], cached):
            check_budget(started)
            constant_estimate, _ = estimate_depth(spectra, started, fixed_events=events, constant=True)
            estimate["敏感性"] = {"状态": "已完成常折射率对照", "常折射率对照厚度_微米": constant_estimate["厚度_微米"], "常折射率对照失败原因": constant_estimate["失败原因"], "常折射率相对变化_%": 100 * (constant_estimate["厚度_微米"] / estimate["厚度_微米"] - 1), "对照口径": "固定原三点定位的同一极值集合；将n(sigma)换成该谱已知合成n曲线的均值；不另算主指标"}
            refresh(result, started)
            smoothed, _ = estimate_depth(spectra, started, width=5)
            estimate["敏感性"].update({"状态": "两项完成", "五点局部窗口厚度_微米": smoothed["厚度_微米"], "五点窗口相对变化_%": 100 * (smoothed["厚度_微米"] / estimate["厚度_微米"] - 1), "五点窗口失败原因": smoothed["失败原因"]})
            refresh(result, started)
        result["运行状态"] = "完成"
    except BudgetExceeded as error:
        result["运行状态"] = "主评分完成，部分可选诊断截止" if score_cases(result["逐例结果"]) is not None else "软截止，主评分未完成"
        result["失败原因"].append(str(error))
    except Exception as error:
        result["运行状态"] = "主评分完成，辅助阶段异常" if score_cases(result["逐例结果"]) is not None else "输入或计算异常，主评分未完成"
        result["失败原因"].append(f"{type(error).__name__}: {error}")
    finally:
        refresh(result, started)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        worker(float(sys.argv[2]))
        return
    baseline = initial_result()
    baseline["运行状态"] = "已启动监督进程"
    refresh(baseline, STARTED)
    try:
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(STARTED)], cwd=ROOT, timeout=max(0.1, HARD_SECONDS - (perf_counter() - STARTED)), check=False)
        abnormal = f"子进程退出码{completed.returncode}" if completed.returncode else None
    except subprocess.TimeoutExpired:
        abnormal = "175秒外层截止终止子进程；保留最近一次原子写出的结果"
    result = json.loads(OUTPUT.read_text(encoding="utf-8"))
    if abnormal:
        result["失败原因"].append(abnormal)
        result["运行状态"] = "主评分完成，辅助阶段被终止" if score_cases(result["逐例结果"]) is not None else "外层截止或异常，主评分未完成"
    if result["主指标值"] is not None:
        failed_count = sum(bool(item["失败原因"]) for item in result["逐例结果"])
        entry = {"类别": "科学尝试", "问题": 1, "尝试": "在色散坐标中使用奇偶整数级次，联合两角度各256点反演24例厚度；同型极值不足时不以峰谷混合补足", "现象": f"合成厚度平均相对误差为{result['主指标值']:.12g}%；24例均保留正数值候选，其中{failed_count}例带失效标记", "决定": "不删除困难情景；常折射率及五点局部窗口只作敏感性对照，不用于选择主厚度", "依据": "求解结果:核心指标键值/合成厚度平均相对误差_%；求解结果:逐例结果；求解结果:辅助诊断/单位与峰谷系数核对"}
    else:
        entry = {"类别": "流程事件", "问题": 1, "尝试": "执行色散峰序反演小样并逐例保存", "现象": f"完成{len(result['逐例结果'])}/24例；实际用时={perf_counter() - STARTED:.3f}秒；状态={result['运行状态']}", "决定": "保留全部现有结果，未完成时不使用部分均值排名", "依据": "求解/问题1/原型结果/路线1.json"}
    try:
        appended = append_experiment(entry)
        result["实验记录追加状态"] = "已追加" if appended else "共享锁等待0.5秒后停止；条目保留在本结果中供补记"
    except (OSError, ValueError) as error:
        result["实验记录追加状态"] = f"追加未成功，未覆盖原记录：{error}"
    result["本次实验条目"] = entry
    refresh(result, STARTED)
    print(json.dumps({"路线名": result["路线名"], "使用附件": result["使用附件"], "完成例数": len(result["逐例结果"]), "核心指标键值": result["核心指标键值"], "实际用时秒": result["实际用时秒"], "运行状态": result["运行状态"], "厚度条件": "已知合成折射率；非实测材料标定", "经验覆盖率": result["经验覆盖率"], "失败原因": result["失败原因"]}, ensure_ascii=False), flush=True)
    if result["主指标值"] is None:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
