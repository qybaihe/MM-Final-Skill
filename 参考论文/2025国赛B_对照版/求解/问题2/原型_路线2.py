"""双角峰序匹配小样原型；只在实际执行时读取附件并生成结果。

R为附件百分数除以100，σ单位cm^-1，d单位um，外部角度为10、15度。
透明近似q=sqrt(n²-sin²θ)，n=n参+c*((2000/σ)²-1)。
往返相位4π(d/10000)σq加常相位ψ，在峰、谷处分别为偶数、奇数倍π。
故半级次h=4(d/10000)σq+β，β=ψ/π；同型级差为偶数，异型为奇数。
列表动态规划允许拒绝极值及至多两个漏周期，稳健回归估共同d和角度截距。
n参、c经有界坐标更新估计，不用绝对反射率或测试条纹选择物理参数。
最后仅在训练强度点估计二次基线及一次幅值，共五个线性系数/角度。
唯一排名指标是两折、两角、两测试块的标准化均方根误差等权平均。
"""

import time

PROCESS_START = time.monotonic()

import bisect
import fcntl
import json
import math
import os
from pathlib import Path
import runpy
import statistics
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "求解/问题2/原型结果/路线2.json"
METRIC = "连续留段标准化均方根误差"
ANGLES = (10, 15)
SOFT_SECONDS = 165.0
HARD_SECONDS = 175.0
BOUNDS = ((0.5, 40.0), (1.2, 6.0), (-1.0, 1.0))
EXPECTED_FOLDS = [
    {"折号": 1, "训练块": [1, 2, 4, 5, 7, 8, 10, 11],
     "校准块": [3, 9], "测试块": [6, 12]},
    {"折号": 2, "训练块": [2, 3, 5, 6, 8, 9, 11, 12],
     "校准块": [4, 10], "测试块": [1, 7]},
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
        deadline = time.monotonic() + 0.5
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("实验记录锁等待超过0.5秒")
                time.sleep(0.01)
        entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(entries, list):
            raise ValueError("实验记录必须为数组")
        entries.append(entry)
        write_json(path, entries)


def initial_result():
    return {
        "问题": 2, "路线编号": "二乙", "路线序号": 2, "路线名": "双角峰序匹配",
        "运行状态": "正在读取真实附件", "可比资格": False, "核心指标": {}, "核心指标键值": {},
        "主指标名称": METRIC, "主指标值": None,
        "指标含义": "八个连续测试块的标准化均方根误差等权平均，无量纲，越小越好；非真实厚度误差。",
        "用时估计": {"性质": "预设限额，未经运行计时验证", "读取与切分秒": 15,
                     "峰序与情景拟合秒": 95, "重建与区间诊断秒": 40,
                     "汇总写出秒": 15, "内部软截止秒": SOFT_SECONDS,
                     "父进程墙钟上限秒": HARD_SECONDS},
        "口径说明": {
            "输入": "附件1、2的Sheet1；百分数除以100；保留原值、源行号与哈希。",
            "取样": "1200—3800 cm^-1窗口，每角floor(序号*(窗口点数-1)/479)选480点；不平均、不插值。",
            "切分": "12块、每块40点，两角共用两折；训练/非训练交界内侧舍去20 cm^-1。",
            "训练尺度": "与二甲同一掩码；全部保留训练点去二次基线后的残差四分位距，下限0.0001；不是仪器噪声。",
            "峰位": "只访问训练连通段，二次邻域总跨度不超过20 cm^-1；显著度取本段及局部二阶差分尺度。",
            "级次": "半级次为整数；峰偶谷奇，同型跳2/4/6、异型跳1/3/5；任次最多漏2周期。",
            "重建": "共享d、n参、c；每角一个峰位回归常相位、二次基线及一次幅值；不由强度反调物理量。",
            "模型选择": "只用训练峰位的稳健级次代价及拒峰代价；不以校准或测试误差选情景、级次或阈值。",
            "区间": "校准绝对残差第ceil(0.9*n)顺序统计量为半宽，端点不截断；报告独立测试经验覆盖，无分布无关保证。",
            "厚度范围": "离散情景及近等训练代价范围，不是概率置信区间；未提供实测折射率或厚度真值。",
            "缺口": "仅以训练模型延拓；多整数解释并列时标记级次未完成，仍保留各分支数值但不作为合格赢家。",
            "异常": "主窗口不含399.6747首点与801.278—927.1104超百段，不宣称异常清洗改善。",
            "数值自由度": "9个初始情景、每情景至多96个厚度点；至多3候选、每候选20轮精修。",
            "辅助检验": "区间覆盖、边界、峰邻域、周期支持及双向留角度仅为诊断，不生成第二个排名分数。",
        },
        "使用附件": ["附件1.xlsx", "附件2.xlsx"], "输入哈希": [], "样本索引": [],
        "折分": [], "参数情景": [], "分块预测与残差": [], "厚度及单位": [],
        "峰位与级次": [], "经验覆盖率": {}, "辅助诊断": {}, "失败原因": [],
        "随机种子": 20260909, "随机性说明": "确定性枚举，无随机拆分与随机初值。",
        "实际用时秒": 0.0, "耗时秒": 0.0,
    }


def dot(left, right):
    return sum(first * second for first, second in zip(left, right))


def quantile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower])


def least_squares(rows, target, weights=None):
    count = len(rows[0])
    roots = [math.sqrt(value) for value in weights] if weights else [1.0] * len(rows)
    columns = [[row[column] * weight for row, weight in zip(rows, roots)]
               for column in range(count)]
    observed = [value * weight for value, weight in zip(target, roots)]
    basis = []
    triangular = [[0.0] * count for unused in range(count)]
    for column_index, column in enumerate(columns):
        residual = column[:]
        for unused in range(2):
            for basis_index, unit in enumerate(basis):
                projection = dot(unit, residual)
                triangular[basis_index][column_index] += projection
                residual = [value - projection * direction
                            for value, direction in zip(residual, unit)]
        length = math.sqrt(dot(residual, residual))
        if length < 1e-12:
            raise ValueError("小维回归秩不足")
        triangular[column_index][column_index] = length
        basis.append([value / length for value in residual])
    projected = [dot(unit, observed) for unit in basis]
    solution = [0.0] * count
    for row in reversed(range(count)):
        solution[row] = (projected[row] - sum(triangular[row][column] * solution[column]
                         for column in range(row + 1, count))) / triangular[row][row]
    return solution


def read_data(result):
    archive = json.loads((ROOT / "交接/数据档案.json").read_text(encoding="utf-8"))
    scout = json.loads((ROOT / "交接/路线侦察.json").read_text(encoding="utf-8"))
    protocol = scout["共同原型协议"]
    sample = protocol["实测小样"]
    if (sample["每角度点数"] != 480 or sample["基本窗口波数"] != [1200, 3800]
            or sample["折分"] != EXPECTED_FOLDS):
        raise ValueError("共同取样或折分变化，三路线必须同步修改")
    scenarios = protocol["共同光学情景"]["初始情景"]
    if len(scenarios) != 9:
        raise ValueError("共同色散初始情景必须为9个")
    reader = runpy.run_path(str(ROOT / "运行时/数据体检.py"),
                           run_name="prototype_readonly_loader")["read_workbook"]
    metadata = {entry["文件名"]: entry for entry in archive["文件档案"]}
    datasets = []
    for filename, angle in zip(result["使用附件"], ANGLES):
        manifest, sheets = reader(ROOT / "数据" / filename)
        if (manifest["文件哈希"] != metadata[filename]["文件哈希"]
                or metadata[filename]["材料"] != "碳化硅"
                or metadata[filename]["入射角度"] != angle):
            raise ValueError(f"{filename}哈希、材料或角度不符")
        cells = next(sheet for sheet in sheets if sheet["名称"] == "Sheet1")["单元格"]
        records = []
        for row in range(2, 7471):
            coordinate, observed = cells[f"A{row}"]["值"], cells[f"B{row}"]["值"]
            if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                       and math.isfinite(value) for value in (coordinate, observed)):
                raise ValueError(f"{filename}第{row}行不是有限数值")
            records.append((coordinate, observed / 100.0, row))
        records.sort()
        if any(left[0] >= right[0] for left, right in zip(records, records[1:])):
            raise ValueError("原始波数不严格递增")
        datasets.append(records)
        result["输入哈希"].append({"源附件": filename, "算法": "SHA-256",
                                   "哈希": manifest["文件哈希"], "原始点数": len(records)})
    if [record[0] for record in datasets[0]] != [record[0] for record in datasets[1]]:
        raise ValueError("同片两角原始波数没有完全对齐")
    window = [index for index, record in enumerate(datasets[0]) if 1200 <= record[0] <= 3800]
    if len(window) < 480:
        raise ValueError("共同窗口不足480点")
    selected = [window[index * (len(window) - 1) // 479] for index in range(480)]
    coordinates = [datasets[0][index][0] for index in selected]
    observations = [[records[index][1] for index in selected] for records in datasets]
    result["样本索引"] = [{"样本序号": index + 1, "块号": index // 40 + 1,
                          "波数_cm^-1": datasets[0][source][0],
                          "原始行号_附件一": datasets[0][source][2],
                          "原始行号_附件二": datasets[1][source][2]}
                         for index, source in enumerate(selected)]
    result["参数情景"] = scenarios
    result["折分"] = sample["折分"]
    result["窗口原始点数_每角度"] = len(window)
    return coordinates, observations, [datasets[0][index][0] for index in window], scenarios


def make_fold(coordinates, observations, full_coordinates, spec):
    train_blocks = set(spec["训练块"])
    edges = [(coordinates[index - 1] + coordinates[index]) / 2
             for index in range(40, 480, 40)]
    guard_edges = [edge for block, edge in enumerate(edges, 1)
                   if (block in train_blocks) != (block + 1 in train_blocks)]
    components = {}
    component = 0
    for block in range(1, 13):
        if block in train_blocks:
            if block - 1 not in train_blocks:
                component += 1
            components[block] = component
    def allowed(coordinate, block):
        return block in train_blocks and all(abs(coordinate - edge) >= 20 for edge in guard_edges)
    indices = [index for index, coordinate in enumerate(coordinates)
               if allowed(coordinate, index // 40 + 1)]
    center = (coordinates[indices[0]] + coordinates[indices[-1]]) / 2
    half_span = (coordinates[indices[-1]] - coordinates[indices[0]]) / 2
    scaled = [(coordinates[index] - center) / half_span for index in indices]
    rows = [[1.0, value, value * value] for value in scaled]
    angle_data = []
    for angle_index, angle in enumerate(ANGLES):
        values = [observations[angle_index][index] for index in indices]
        baseline = least_squares(rows, values)
        residual = [value - dot(row, baseline) for value, row in zip(values, rows)]
        angle_data.append({"angle": angle, "observed": values,
                           "scale": max(quantile(residual, 0.75) - quantile(residual, 0.25), 0.0001)})
    full_train = [(coordinate, components[bisect.bisect_right(edges, coordinate) + 1])
                  for coordinate in full_coordinates
                  if allowed(coordinate, bisect.bisect_right(edges, coordinate) + 1)]
    return {"spec": spec, "indices": indices, "coordinates": coordinates,
            "observations": observations, "center": center, "half_span": half_span,
            "angles": angle_data, "components": components, "full_train": full_train,
            "sample_train": [(coordinates[index], components[index // 40 + 1]) for index in indices]}


def noise_scale(values):
    differences = [values[index + 1] - 2 * values[index] + values[index - 1]
                   for index in range(1, len(values) - 1)]
    return max(statistics.median(abs(value) for value in differences) / (0.67449 * math.sqrt(6)),
               1e-8) if differences else 1e-8


def extract_events(fold, angle_index, threshold=3.0):
    candidates = []
    for component in sorted(set(fold["components"].values())):
        indices = [index for index in fold["indices"]
                   if fold["components"][index // 40 + 1] == component]
        coordinates = [fold["coordinates"][index] for index in indices]
        values = [fold["observations"][angle_index][index] for index in indices]
        segment_noise = noise_scale(values)
        for position in range(1, len(indices) - 1):
            left_diff = values[position] - values[position - 1]
            right_diff = values[position + 1] - values[position]
            if left_diff * right_diff >= 0:
                continue
            parity = 0 if left_diff > 0 else 1
            coordinate = coordinates[position]
            neighborhood = [neighbor for neighbor, value in enumerate(coordinates)
                            if abs(value - coordinate) <= 10]
            width = coordinates[neighborhood[-1]] - coordinates[neighborhood[0]]
            event = {"id": len(candidates), "index": indices[position], "component": component,
                     "sigma": coordinate, "original": coordinate, "parity": parity,
                     "points": len(neighborhood), "width": width, "reason": "",
                     "strength": 0.0, "shift": 0.0}
            candidates.append(event)
            if len(neighborhood) < 3 or width > 20:
                event["reason"] = "20 cm^-1邻域内不足3点"
                continue
            local_values = [values[neighbor] for neighbor in neighborhood]
            local_noise = max(segment_noise, noise_scale(local_values))
            rows = [[1.0, (coordinates[neighbor] - coordinate) / 10,
                     ((coordinates[neighbor] - coordinate) / 10) ** 2] for neighbor in neighborhood]
            intercept, slope, curvature = least_squares(rows, local_values)
            if curvature == 0 or (curvature < 0) != (parity == 0):
                event["reason"] = "二次曲率与峰谷类型不符"
                continue
            shift = -10 * slope / (2 * curvature)
            event["shift"] = shift
            if not coordinates[position - 1] <= coordinate + shift <= coordinates[position + 1]:
                event["reason"] = "二次顶点超出相邻原坐标"
                continue
            sign = 1.0 if parity == 0 else -1.0
            transformed = [sign * value for value in values]
            bases = []
            for direction in (-1, 1):
                neighbor = position + direction
                minimum = transformed[position]
                while 0 <= neighbor < len(values):
                    minimum = min(minimum, transformed[neighbor])
                    if transformed[neighbor] > transformed[position]:
                        break
                    neighbor += direction
                bases.append(minimum)
            prominence = max(0.0, transformed[position] - max(bases))
            event.update({"sigma": coordinate + shift, "strength": prominence / local_noise,
                          "noise": local_noise, "prominence": prominence,
                          "curvature": curvature, "fitted": intercept - slope * slope / (4 * curvature)})
            if event["strength"] < threshold:
                event["reason"] = f"显著度小于本训练段局部差分尺度的{threshold:g}倍"
    return candidates


def optical_coordinate(coordinate, angle, reference, dispersion):
    refractive = reference + dispersion * ((2000 / coordinate) ** 2 - 1)
    return coordinate * math.sqrt(refractive * refractive - math.sin(math.radians(angle)) ** 2)


def valid_parameters(parameters):
    if not all(lower <= value <= upper for value, (lower, upper) in zip(parameters, BOUNDS)):
        return False
    thickness, reference, dispersion = parameters
    for coordinate in (1200.0, 3800.0):
        refractive = reference + dispersion * ((2000 / coordinate) ** 2 - 1)
        if refractive <= math.sin(math.radians(15)):
            return False
        derivative = -2 * dispersion * 2000 ** 2 / coordinate ** 3
        for angle in ANGLES:
            squared = refractive * refractive - math.sin(math.radians(angle)) ** 2
            if squared + coordinate * refractive * derivative <= 0:
                return False
    return True


def huber(value):
    magnitude = abs(value)
    return 0.5 * magnitude * magnitude if magnitude <= 1 else magnitude - 0.5


def match_sequence(events, angle, parameters):
    accepted = sorted((event for event in events if not event["reason"]), key=lambda event: event["sigma"])
    if len(accepted) < 2:
        return None
    thickness, reference, dispersion = parameters
    half_phases = [4 * thickness / 10000 * optical_coordinate(event["sigma"], angle, reference, dispersion)
                   for event in accepted]
    rewards = [1.0 + min(2.0, math.log1p(event["strength"] / 3)) for event in accepted]
    costs = [-reward for reward in rewards]
    previous = [-1] * len(accepted)
    increments = [0] * len(accepted)
    for right in range(len(accepted)):
        for left in range(right - 1, -1, -1):
            difference = half_phases[right] - half_phases[left]
            if difference > 6.75:
                break
            parity = (accepted[right]["parity"] - accepted[left]["parity"]) % 2
            options = (1, 3, 5) if parity else (2, 4, 6)
            increment = min(options, key=lambda value: abs(value - difference))
            if abs(increment - difference) > 0.75:
                continue
            transition = huber((difference - increment) / 0.18) + 0.12 * ((increment - 1) // 2)
            proposal = costs[left] + transition - rewards[right]
            if proposal < costs[right]:
                costs[right], previous[right], increments[right] = proposal, left, increment
    endpoint = min(range(len(costs)), key=costs.__getitem__)
    path = []
    cursor = endpoint
    while cursor >= 0:
        path.append(cursor)
        cursor = previous[cursor]
    path.reverse()
    if len(path) < 2:
        return None
    order = accepted[path[0]]["parity"]
    ordered = []
    for position, cursor in enumerate(path):
        if position:
            order += increments[cursor]
        ordered.append((accepted[cursor], order))
    return {"ordered": ordered, "reward_total": sum(rewards),
            "cost": (sum(rewards) + costs[endpoint]) / sum(rewards)}


def fixed_path_cost(match, angle, parameters):
    ordered = match["ordered"]
    selected_reward = sum(1.0 + min(2.0, math.log1p(event["strength"] / 3)) for event, order in ordered)
    penalty = match["reward_total"] - selected_reward
    for (left, left_order), (right, right_order) in zip(ordered, ordered[1:]):
        increment = right_order - left_order
        predicted = 4 * parameters[0] / 10000 * (
            optical_coordinate(right["sigma"], angle, parameters[1], parameters[2])
            - optical_coordinate(left["sigma"], angle, parameters[1], parameters[2]))
        penalty += huber((predicted - increment) / 0.18) + 0.12 * ((increment - 1) // 2)
    return penalty / match["reward_total"]


def regress_orders(parameters, matches, angle_indices):
    rows, target, weights = [], [], []
    for local_angle, angle_index in enumerate(angle_indices):
        for event, order in matches[local_angle]["ordered"]:
            optical = optical_coordinate(event["sigma"], ANGLES[angle_index], parameters[1], parameters[2])
            rows.append([4 * optical / 10000] + [float(index == local_angle) for index in range(len(matches))])
            target.append(order)
            weights.append(min(3.0, max(1.0, event["strength"] / 3)))
    current_weights = weights[:]
    for unused in range(4):
        fitted = least_squares(rows, target, current_weights)
        residual = [observed - dot(row, fitted) for row, observed in zip(rows, target)]
        scale = max(0.04, 1.4826 * statistics.median(abs(value) for value in residual))
        current_weights = [weight * min(1.0, 1.5 * scale / max(abs(value), 1e-12))
                           for weight, value in zip(weights, residual)]
    physical = (fitted[0], parameters[1], parameters[2])
    if not valid_parameters(physical):
        return None
    residual_cost = sum(weight * huber(value / 0.18) for weight, value in zip(weights, residual)) / sum(weights)
    path_cost = sum(fixed_path_cost(match, ANGLES[index], physical)
                    for match, index in zip(matches, angle_indices)) / len(matches)
    return {"parameters": physical, "offsets": fitted[1:], "matches": matches,
            "angle_indices": list(angle_indices),
            "loss": path_cost + residual_cost}


def evaluate(parameters, events, angle_indices):
    if not valid_parameters(parameters):
        return None
    matches = [match_sequence(events[index], ANGLES[index], parameters) for index in angle_indices]
    if any(match is None for match in matches):
        return None
    try:
        candidate = regress_orders(parameters, matches, angle_indices)
        if candidate is None:
            return None
        rematched = [match_sequence(events[index], ANGLES[index], candidate["parameters"])
                     for index in angle_indices]
        if all(match is not None for match in rematched):
            updated = regress_orders(candidate["parameters"], rematched, angle_indices)
            if updated is not None:
                candidate = updated
        return candidate
    except ValueError:
        return None


def retain(pool, candidate, limit=3):
    if candidate is None:
        return
    signature = tuple(round(value, 5) for value in candidate["parameters"])
    for previous in pool:
        if tuple(round(value, 5) for value in previous["parameters"]) == signature:
            if previous["loss"] <= candidate["loss"]:
                return
            pool.remove(previous)
            break
    pool.append(candidate)
    pool.sort(key=lambda item: item["loss"])
    del pool[limit:]


def refine(candidate, events, deadline, rounds=20):
    best = candidate
    steps = [0.35, 0.2, 0.05]
    for unused in range(rounds):
        if time.monotonic() >= deadline:
            break
        improved = False
        for dimension in range(3):
            for direction in (-1, 1):
                if time.monotonic() >= deadline:
                    return best
                trial = list(best["parameters"])
                trial[dimension] += direction * steps[dimension]
                updated = evaluate(trial, events, best["angle_indices"])
                if updated is not None and updated["loss"] < best["loss"]:
                    best, improved = updated, True
        if not improved:
            steps = [step * 0.5 for step in steps]
        if max(steps) < 0.0005:
            break
    return best


def parameter_record(candidate):
    return {"厚度_um": candidate["parameters"][0], "参考折射率": candidate["parameters"][1],
            "色散系数": candidate["parameters"][2], "训练峰序代价_非排名": candidate["loss"],
            "各角常相位_rad": [math.pi * value for value in candidate["offsets"]],
            "参与附件": [f"附件{index + 1}.xlsx" for index in candidate["angle_indices"]]}


def intensity_model(fold, angle_index, candidate, phase=None):
    thickness, reference, dispersion = candidate["parameters"]
    if phase is None:
        phase = math.pi * candidate["offsets"][candidate["angle_indices"].index(angle_index)]
    def features(coordinate):
        scaled = (coordinate - fold["center"]) / fold["half_span"]
        oscillation = math.cos(4 * math.pi * thickness / 10000 * optical_coordinate(
            coordinate, ANGLES[angle_index], reference, dispersion) + phase)
        return [1.0, scaled, scaled * scaled, oscillation, scaled * oscillation]
    rows = [features(fold["coordinates"][index]) for index in fold["indices"]]
    coefficients = least_squares(rows, fold["angles"][angle_index]["observed"])
    predictions = [dot(features(coordinate), coefficients) for coordinate in fold["coordinates"]]
    if not all(math.isfinite(value) for value in predictions):
        raise ValueError("强度预测出现非有限值")
    return predictions, coefficients


def score_fold(fold, candidate):
    records = []
    for angle_index, angle in enumerate(ANGLES):
        predictions, coefficients = intensity_model(fold, angle_index, candidate)
        calibration = [index for index in range(480) if index // 40 + 1 in fold["spec"]["校准块"]]
        residuals = sorted(abs(predictions[index] - fold["observations"][angle_index][index])
                           for index in calibration)
        half_width = residuals[math.ceil(0.9 * len(residuals)) - 1]
        for block in fold["spec"]["测试块"]:
            indices = list(range((block - 1) * 40, block * 40))
            errors = [predictions[index] - fold["observations"][angle_index][index] for index in indices]
            scale = fold["angles"][angle_index]["scale"]
            hits = sum(abs(error) <= half_width for error in errors)
            records.append({"折号": fold["spec"]["折号"], "材料": "碳化硅", "角度_度": angle,
                            "源附件": f"附件{angle_index + 1}.xlsx", "测试块": block,
                            "测试点数": len(indices), "训练点数": len(fold["indices"]),
                            "训练尺度_反射率比例": scale, "标准化均方根误差": math.sqrt(dot(errors, errors) / len(errors)) / scale,
                            "预测反射率_比例": [predictions[index] for index in indices],
                            "实测反射率_比例": [fold["observations"][angle_index][index] for index in indices],
                            "预测减实测_反射率比例": errors, "波数_cm^-1": [fold["coordinates"][index] for index in indices],
                            "强度系数": coefficients, "区间半宽_反射率比例": half_width,
                            "区间下限_反射率比例": [predictions[index] - half_width for index in indices],
                            "区间上限_反射率比例": [predictions[index] + half_width for index in indices],
                            "名义覆盖率": 0.9, "经验覆盖率": hits / len(indices), "覆盖点数": hits,
                            "平均区间宽度_反射率比例": 2 * half_width, "校准点数": len(calibration)})
    return records


def refresh_score(result):
    records = result["分块预测与残差"]
    expected = {(fold["折号"], angle, block) for fold in EXPECTED_FOLDS
                for angle in ANGLES for block in fold["测试块"]}
    obtained = {(record["折号"], record["角度_度"], record["测试块"]) for record in records}
    if (obtained == expected and len(records) == 8
            and all(math.isfinite(record["标准化均方根误差"]) for record in records)):
        value = statistics.mean(record["标准化均方根误差"] for record in records)
        result["主指标值"] = value
        result["核心指标"] = {METRIC: value}
        result["核心指标键值"] = {METRIC: value}
    else:
        result["主指标值"] = None
        result["核心指标"] = {}
        result["核心指标键值"] = {}
    total = sum(record["测试点数"] for record in records)
    if total:
        result["经验覆盖率"] = {"名义覆盖率": 0.9, "测试点次": total,
                               "覆盖点次": sum(record["覆盖点数"] for record in records),
                               "点数加权覆盖率": sum(record["覆盖点数"] for record in records) / total,
                               "平均宽度_反射率比例": sum(record["平均区间宽度_反射率比例"] * record["测试点数"]
                                                        for record in records) / total,
                               "宽度分组": "按角度、折号及波数测试块逐项列于分块预测与残差，不存在时间预测步长。",
                               "限制": "两折重复坐标按测试点次计数；谱内相关，不宣称独立重复测量。"}


def event_records(fold, events, candidate, sample_index):
    records, supports, gaps = [], [], []
    for angle_index, angle in enumerate(ANGLES):
        ordered = candidate["matches"][angle_index]["ordered"]
        lookup = {event["id"]: order for event, order in ordered}
        cycles = 0
        inferred_cycles = 0
        for component in sorted(set(fold["components"].values())):
            local = [order for event, order in ordered if event["component"] == component]
            inferred_cycles += (max(local) - min(local)) // 2 if local else 0
            continuous_half_cycles = 0
            for left, right in zip(local, local[1:]):
                if right - left <= 2:
                    continuous_half_cycles += right - left
                else:
                    cycles += continuous_half_cycles // 2
                    continuous_half_cycles = 0
            cycles += continuous_half_cycles // 2
        supports.append({"角度_度": angle, "使用极值数": len(ordered),
                         "训练连通段内完整周期数": cycles, "级次推算周期数_含漏峰": inferred_cycles,
                         "至少3个完整周期": cycles >= 3,
                         "口径": "仅连续观测级差不超过1周期的片段计完整周期，较大漏峰跳跃不计支持周期。"})
        for event in events[angle_index]:
            order = lookup.get(event["id"])
            records.append({"折号": fold["spec"]["折号"], "角度_度": angle,
                            "源附件": f"附件{angle_index + 1}.xlsx",
                            "原始行号": sample_index[event["index"]]["原始行号_附件一"],
                            "训练连通段": event["component"], "候选原波数_cm^-1": event["original"],
                            "峰位_cm^-1": event["sigma"], "类型": "峰" if event["parity"] == 0 else "谷",
                            "邻域点数": event["points"], "邻域跨度_cm^-1": event["width"],
                            "细化偏移_cm^-1": event["shift"], "显著度倍数": event["strength"],
                            "差分尺度_反射率比例": event.get("noise"),
                            "半级次整数": order, "级次_周期": order / 2 if order is not None else None,
                            "拒绝原因": event["reason"] or ("动态规划拒绝：级差不兼容或弱事件代价较高" if order is None else "保留")})
        for (left, left_order), (right, right_order) in zip(ordered, ordered[1:]):
            if left["component"] != right["component"]:
                gaps.append({"角度_度": angle, "左段": left["component"], "右段": right["component"],
                             "左峰位_cm^-1": left["sigma"], "右峰位_cm^-1": right["sigma"],
                             "半级次差": right_order - left_order,
                             "跨越未观测周期数": (right_order - left_order - 1) // 2})
    return records, supports, gaps


def alias_check(fold, scenarios, candidate):
    parameters = [(40.0, scenario["参考折射率"], scenario["色散系数"]) for scenario in scenarios]
    if candidate is not None:
        parameters.append(candidate["parameters"])
    maxima = {"原训练坐标": 0.0, "抽稀训练坐标": 0.0}
    checked = 0
    for physical in parameters:
        if not valid_parameters(physical):
            continue
        checked += 1
        for angle in ANGLES:
            for label, coordinates in (("原训练坐标", fold["full_train"]),
                                        ("抽稀训练坐标", fold["sample_train"])):
                phases = [4 * math.pi * physical[0] / 10000 * optical_coordinate(
                    coordinate, angle, physical[1], physical[2]) for coordinate, component in coordinates]
                maxima[label] = max(maxima[label], max((abs(phases[index + 1] - phases[index])
                    for index in range(len(phases) - 1)
                    if coordinates[index][1] == coordinates[index + 1][1]), default=0.0))
    return {"折号": fold["spec"]["折号"], "检查情景数": checked,
            "原坐标最大相邻相位差_rad": maxima["原训练坐标"],
            "小样最大相邻相位差_rad": maxima["抽稀训练坐标"],
            "小样存在混叠风险": maxima["抽稀训练坐标"] >= math.pi,
            "口径": "仅训练连通段完整坐标和初始情景40 um上界及拟合解；不读取完整强度加密寻峰，不跨缺口。",
            "动作": "风险不隐藏；不单独改密度，须三路线共同改960点后另行比较。"}


def held_angle_checks(fold, events, pools, deadline):
    records = []
    for source_index, pool in enumerate(pools):
        if not pool or time.monotonic() >= deadline:
            records.append({"来源角度_度": ANGLES[source_index], "状态": "未完成独立单角估计"})
            continue
        candidate = refine(pool[0], events, min(deadline, time.monotonic() + 2), rounds=6)
        target_index = 1 - source_index
        phase = math.pi * candidate["offsets"][0]
        predictions, coefficients = intensity_model(fold, target_index, candidate, phase=phase)
        block_records = []
        for block in fold["spec"]["测试块"]:
            errors = [predictions[index] - fold["observations"][target_index][index]
                      for index in range((block - 1) * 40, block * 40)]
            block_records.append({"测试块": block, "标准化均方根误差": math.sqrt(dot(errors, errors) / 40)
                                  / fold["angles"][target_index]["scale"]})
        records.append({"来源角度_度": ANGLES[source_index], "被检角度_度": ANGLES[target_index],
                        "状态": "完成", "来源独立估计": parameter_record(candidate),
                        "冻结常相位_rad": phase, "被检角度重新拟合厚度": False,
                        "被检角度自由量": "只用其训练强度拟合二次基线、一次幅值，共5系数",
                        "限制": "来源物理参数只由该角训练极值选择；另冻结来源相位，可能因角度相位不同而失败。",
                        "分块诊断": block_records, "强度系数": coefficients})
    return records


def gap_disagreements(best, candidates):
    conflicts = []
    for candidate in candidates:
        for angle_index, best_match, other_match in zip(best["angle_indices"], best["matches"], candidate["matches"]):
            other_lookup = {event["id"]: order for event, order in other_match["ordered"]}
            shared = [(event, order, other_lookup[event["id"]]) for event, order in best_match["ordered"]
                      if event["id"] in other_lookup]
            anchors = {}
            for event, best_order, other_order in shared:
                anchors.setdefault(event["component"], (best_order, other_order))
            components = sorted(anchors)
            for left, right in zip(components, components[1:]):
                best_delta = anchors[right][0] - anchors[left][0]
                other_delta = anchors[right][1] - anchors[left][1]
                if best_delta != other_delta:
                    conflicts.append({"角度_度": ANGLES[angle_index], "左段": left, "右段": right,
                                      "最优半级差": best_delta, "近等分支半级差": other_delta,
                                      "近等厚度_um": candidate["parameters"][0]})
    return conflicts


def threshold_check(fold, best, deadline):
    records = []
    for threshold in (2.4, 3.6):
        if time.monotonic() >= deadline:
            records.append({"显著度阈值倍数": threshold, "状态": "时间限制，未计算"})
            continue
        events = [extract_events(fold, index, threshold=threshold) for index in range(2)]
        candidate = evaluate(best["parameters"], events, (0, 1))
        if candidate is not None:
            candidate = refine(candidate, events, min(deadline, time.monotonic() + 0.75), rounds=4)
        records.append({"显著度阈值倍数": threshold, "相对主阈值变化": threshold / 3 - 1,
                        "两角有效候选数": [sum(not event["reason"] for event in pair) for pair in events],
                        "状态": "完成" if candidate is not None else "有效极值不能支持共同回归",
                        "厚度_um": candidate["parameters"][0] if candidate is not None else None,
                        "厚度相对变化": candidate["parameters"][0] / best["parameters"][0] - 1 if candidate is not None else None,
                        "口径": "仅以主解为起点作训练极值局部敏感性，不读取校准测试量，不据此回调主阈值。"})
    return records


def worker(t0):
    result = initial_result()
    checkpoint(result, t0)
    try:
        coordinates, observations, full_coordinates, scenarios = read_data(result)
        folds = [make_fold(coordinates, observations, full_coordinates, spec) for spec in EXPECTED_FOLDS]
        all_events = [[extract_events(fold, angle_index) for angle_index in range(2)] for fold in folds]
        result["辅助诊断"]["全部训练候选_拟合前"] = [
            {"折号": fold_index + 1, "源附件": f"附件{angle_index + 1}.xlsx",
             "角度_度": ANGLES[angle_index], "原始行号": result["样本索引"][event["index"]]["原始行号_附件一"],
             "原始波数_cm^-1": event["original"], "峰位_cm^-1": event["sigma"],
             "类型": "峰" if event["parity"] == 0 else "谷", "训练连通段": event["component"],
             "邻域点数": event["points"], "邻域跨度_cm^-1": event["width"],
             "显著度倍数": event["strength"], "拒绝原因": event["reason"] or "通过局部筛选，级次由训练匹配确定"}
            for fold_index, pair in enumerate(all_events) for angle_index, events in enumerate(pair) for event in events]
        result["辅助诊断"]["初始抽稀检查"] = [alias_check(fold, scenarios, None) for fold in folds]
        result["辅助诊断"]["训练候选数"] = [[len(events) for events in pair] for pair in all_events]
        result["运行状态"] = "训练峰序情景枚举"
        checkpoint(result, t0)
        pools = [[], []]
        single_pools = [[[], []], [[], []]]
        scenario_pools = [[[] for scenario in scenarios] for fold in folds]
        counts = [0, 0]
        coarse_deadline = min(t0 + 95, time.monotonic() + 75)
        coarse_complete = True
        for grid_index in range(96):
            for scenario_index, scenario in enumerate(scenarios):
                if time.monotonic() >= coarse_deadline:
                    coarse_complete = False
                    break
                initial = (0.5 + 39.5 * grid_index / 95, scenario["参考折射率"], scenario["色散系数"])
                for fold_index in range(2):
                    events = all_events[fold_index]
                    candidate = evaluate(initial, events, (0, 1))
                    retain(pools[fold_index], candidate)
                    retain(scenario_pools[fold_index][scenario_index], candidate, limit=1)
                    for angle_index in range(2):
                        retain(single_pools[fold_index][angle_index], evaluate(initial, events, (angle_index,)))
                    counts[fold_index] += 1
            result["辅助诊断"]["粗枚举计数_每折"] = counts[:]
            result["辅助诊断"]["当前最优训练候选"] = [parameter_record(pool[0]) if pool else None for pool in pools]
            if grid_index % 8 == 0 or not coarse_complete or grid_index == 95:
                checkpoint(result, t0)
            if not coarse_complete:
                break
        result["辅助诊断"]["九情景枚举完整"] = coarse_complete
        for fold_index in range(2):
            for candidate in list(pools[fold_index]):
                deadline = min(t0 + 110, time.monotonic() + 2.0)
                retain(pools[fold_index], refine(candidate, all_events[fold_index], deadline))
        for fold_index, fold in enumerate(folds):
            if not pools[fold_index]:
                result["失败原因"].append(f"第{fold_index + 1}折无法由两角训练极值形成共同正厚度；未读测试峰补链")
                continue
            best = pools[fold_index][0]
            result["厚度及单位"].append({"折号": fold_index + 1, **parameter_record(best)})
            result["分块预测与残差"].extend(score_fold(fold, best))
            records, supports, gaps = event_records(fold, all_events[fold_index], best, result["样本索引"])
            result["峰位与级次"].extend(records)
            candidates = [pool[0] for pool in scenario_pools[fold_index] if pool] + pools[fold_index]
            near = [candidate for candidate in candidates if candidate["loss"] <= best["loss"] + 0.02]
            conflicts = gap_disagreements(best, near)
            missing_components = any(len({event["component"] for event, order in match["ordered"]})
                                     < len(set(fold["components"].values())) for match in best["matches"])
            ambiguous = bool(conflicts) or missing_components
            diagnostics = {"周期支持": supports, "跨缺口级次": gaps,
                           "跨缺口定级状态": "未完成：多分支近等代价或训练连通段未连通" if ambiguous else "有限搜索内完成",
                           "歧义规则": "训练峰序总代价距最优不超过0.02，共同极值锚点跨段整数差冲突；不将仅拒峰端点变化误判为跳周。非统计检验。",
                           "共同峰锚点级差冲突": conflicts,
                           "近等分支数": len(near), "近等厚度范围_um": [min(item["parameters"][0] for item in near),
                                                                           max(item["parameters"][0] for item in near)],
                           "离散情景条件解": [parameter_record(pool[0]) for pool in scenario_pools[fold_index] if pool],
                           "近等分支": [parameter_record(item) for item in near],
                           "参数边界命中": [name for name, value, bounds in zip(
                               ("厚度_um", "参考折射率", "色散系数"), best["parameters"], BOUNDS)
                               if min(value - bounds[0], bounds[1] - value) < 1e-3],
                           "判别所需补充条件": "独立折射率曲线或预先保留波段的高信噪比完整条纹；不能读取当前测试峰补链。",
                           "抽稀检查": alias_check(fold, scenarios, best)}
            result["辅助诊断"][f"第{fold_index + 1}折"] = diagnostics
            if ambiguous:
                result["失败原因"].append(f"第{fold_index + 1}折跨缺口级次未完成，保留条件数值但不宣称唯一解")
            refresh_score(result)
            checkpoint(result, t0)
        for fold_index, fold in enumerate(folds):
            if time.monotonic() >= min(t0 + 150, t0 + SOFT_SECONDS):
                result["失败原因"].append("达到辅助诊断时间上限，停止新增诊断")
                break
            if pools[fold_index]:
                result["辅助诊断"][f"第{fold_index + 1}折"]["双向冻结参数留角度"] = held_angle_checks(
                    fold, all_events[fold_index], single_pools[fold_index], min(t0 + 150, t0 + SOFT_SECONDS))
                result["辅助诊断"][f"第{fold_index + 1}折"]["显著度阈值灵敏度"] = threshold_check(
                    fold, pools[fold_index][0], min(t0 + 150, t0 + SOFT_SECONDS))
                checkpoint(result, t0)
        refresh_score(result)
        result["运行状态"] = "完成小样预测" if result["主指标值"] is not None else "未完成共同八块预测"
        result["可比资格"] = (result["主指标值"] is not None and coarse_complete
                              and not result["失败原因"] and all(
                                  not record["小样存在混叠风险"] for record in result["辅助诊断"]["初始抽稀检查"])
                              and all(not result["辅助诊断"][f"第{fold_index + 1}折"]["抽稀检查"]["小样存在混叠风险"]
                                      for fold_index in range(2)))
        result["置信说明"] = "保留八块数值；周期支持、参数边界、阈值敏感性及区间覆盖逐项披露，预测误差不是厚度准确度。"
        checkpoint(result, t0)
        append_experiment({"类别": "科学尝试", "问题": 2,
                           "尝试": "在两角各480个原坐标上比较9个色散起点，以峰谷奇偶及不超过2个漏周期约束共同厚度。",
                           "现象": f"两折分别考察{counts[0]}、{counts[1]}组起点，获得{len(result['分块预测与残差'])}个连续测试块预测；主指标为{result['主指标值']}。",
                           "决定": "保留所有已形成的条件厚度及逐峰依据；级次多解、覆盖不足或参数触边只降低结论强度，不隐去已得数值。",
                           "依据": "求解结果:厚度及单位；求解结果:峰位与级次；求解结果:主指标值；求解结果:辅助诊断"})
    except Exception as error:
        result["运行状态"] = "未完成或部分完成"
        result["失败原因"].append(f"{type(error).__name__}: {error}")
        refresh_score(result)
        checkpoint(result, t0)
        try:
            append_experiment({"类别": "流程事件", "问题": 2, "尝试": "执行问题2路线2限时小样原型",
                               "现象": f"{type(error).__name__}: {error}",
                               "决定": "保留已完成块及错误，不以成功子集的均值替代共同八块指标",
                               "依据": "求解/问题2/原型结果/路线2.json:失败原因"})
        except Exception as record_error:
            result["失败原因"].append(f"追加记录失败: {record_error}")
    checkpoint(result, t0)
    print(json.dumps({"路线名": result["路线名"], "状态": result["运行状态"],
                      "核心指标": result["核心指标"], "厚度及单位": result["厚度及单位"],
                      "实际用时秒": result["实际用时秒"], "失败原因": result["失败原因"]}, ensure_ascii=False))


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--限时子进程":
        worker(float(sys.argv[2]))
        return
    started = time.monotonic()
    checkpoint(initial_result(), started)
    try:
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--限时子进程", str(started)],
                                   cwd=ROOT, timeout=max(0.1, HARD_SECONDS - (time.monotonic() - started)), check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"子进程退出码{completed.returncode}")
    except (subprocess.TimeoutExpired, RuntimeError) as error:
        result = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else initial_result()
        if result["主指标值"] is not None:
            result["辅助诊断"]["截止前八块指标_仅留档不排名"] = result["主指标值"]
        result["主指标值"] = None
        result["核心指标"] = {}
        result["核心指标键值"] = {}
        result["可比资格"] = False
        result["运行状态"] = "未完成：175秒墙钟限时或异常退出"
        result["失败原因"].append(f"{type(error).__name__}: {error}")
        checkpoint(result, started)
        try:
            append_experiment({"类别": "流程事件", "问题": 2, "尝试": "以175秒总墙钟上限执行峰序原型",
                               "现象": result["失败原因"][-1], "决定": "停止运行并保留检查点，不拼接多次超时成绩",
                               "依据": "求解/问题2/原型结果/路线2.json:运行状态"})
        except Exception as record_error:
            print(f"实验记录追加失败: {record_error}", file=sys.stderr)
        print(json.dumps({"路线名": result["路线名"], "运行状态": result["运行状态"],
                          "实际用时秒": result["实际用时秒"], "失败原因": result["失败原因"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
