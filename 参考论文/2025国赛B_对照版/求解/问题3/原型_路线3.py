"""问题三丙：真实光谱小样的受限周期形状配准；不绘图。

sigma 的单位为 cm^-1，d 的单位为微米，x=(sigma-2500)/1300。
n=n_ref+c*((2000/sigma)^2-1)，q=sqrt(n^2-sin(theta)^2)。
n_ref 为2000 cm^-1处的参考折射率，c为无量纲经验色散系数。
由空气折射率为1的透明各向同性Snell关系得到q，再计入往返光程。
往返相位除以 2*pi 得到 cycles=2*d*sigma*q/10000。
R=B0+B1*x+B2*x^2+((1-x)*a_left+(1+x)*a_right)/2*F(cycles)。
两个幅值端点非负；F 是零均值、峰谷差为2的12结点周期线性插值。
各角度各有模板，但同片共享 d、n_ref、c；两种材料不共享参数。
模板交替解循环二阶差分惩罚的12维方程，并作单峰单谷可行投影。
投影枚举峰位置，谷位于对面半周期，分别用PAVA约束两个单调半周；
这是可行投影而非精确欧氏投影，所有更新仍须降低训练惩罚目标。
正弦只用于训练内初始化及形状诊断，不限制最终模板的谐波阶数。
仅16个共同测试块的平均标准化RMSE用于排名；其他量均为诊断。
只编写时不生成数值结果。执行后逐步写原型结果/路线3.json。
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
import posixpath
import subprocess
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "求解/问题3/原型结果/路线3.json"
METRIC = "连续留段标准化均方根误差"
MATERIALS = (("硅", (3, 4)), ("碳化硅", (1, 2)))
ANGLES = (10, 15)
BOUNDS = ((0.5, 40.0), (1.2, 6.0), (-1.0, 1.0))
KNOTS = 12
ROUGHNESS = 0.01
HARD_SECONDS = 175.0
SOFT_SECONDS = 165.0
NAMESPACE = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


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
        path = ROOT / "交接/实验记录.json"
        entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(entries, list):
            raise ValueError("实验记录不是数组，拒绝覆盖")
        entries.append(entry)
        write_json(path, entries)


def initial_result(token):
    return {
        "问题": 3, "路线编号": "三丙", "路线序号": 3, "路线名": "周期形状配准",
        "本次运行标识": token, "运行状态": "读取真实附件", "核心指标": {},
        "核心指标键值": {}, "主指标名称": METRIC, "主指标值": None,
        "主指标单位": "无量纲", "用于同口径排名": False,
        "指标含义": "16个测试块各自的RMSE除以本折本角度训练尺度，再等权平均；越小越好，不是厚度真值误差。",
        "用时估计": {"性质": "设计限额，未经运行测时", "总上限秒": 175,
                     "子进程收尾截止秒": 170, "内部软截止秒": 165,
                     "读取与切分秒": 15, "周期配准与模板更新秒": 105,
                     "形状诊断与区间秒": 30, "汇总写出秒": 15,
                     "粗扫截止秒": 70, "精修截止秒": 120, "诊断截止秒": 150},
        "口径说明": [
            "仅真实附件1至4；1200至3800 cm^-1各480个原坐标；两材料分开，同片两角共同掩码。",
            "12个连续块，每块40点，共同两折；训练与非训练交界移去20 cm^-1训练保护带。",
            "尺度为训练反射率去二次基线残差的线性插值四分位距，下限0.0001反射率比例。",
            "参数、模板、幅值、基线和起点均只由训练残差选择；未覆盖相位槽只靠训练正则化，不从留出点补齐。",
            "形状为12结点零均值、峰谷差2的单峰单谷周期模板；循环二阶差分惩罚固定0.01，不用测试选择。",
            "两束正弦仅作初始化和模板形状对照，不计算第二个总评分，也不据测试表现换成另一条路线。",
            "预测区间取独立校准块绝对残差向上取整90%经验分位数；不裁剪端点，报告测试覆盖。",
            "主窗口不含共同零首点和801.278至927.1104 cm^-1超百段，不声称清洗改善。",
            "厚度与折射率均为有限经验情景下的条件估计；不生成厚度置信区间。",
            "形状非正弦不足以证明多光束；不作显著性结论，不据此正式修正碳化硅厚度。"],
        "使用附件": [f"附件{number}.xlsx" for number in range(1, 5)],
        "输入哈希": [], "样本索引": {}, "折分": [], "参数情景": {},
        "分块预测与残差": [], "厚度及单位": [], "经验覆盖率": {},
        "辅助诊断": {}, "失败原因": [], "随机种子": 20260909,
        "随机性说明": "保序抽样和确定性搜索，不使用随机抽样。",
        "实际用时秒": 0.0, "耗时秒": 0.0,
    }


def read_sheet(path):
    with ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.get("Id"): item.get("Target") for item in relationships}
        sheet = next(item for item in workbook.findall("m:sheets/m:sheet", NAMESPACE)
                     if item.get("name") == "Sheet1")
        relation = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = targets[relation]
        member = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        tree = ET.fromstring(archive.read(member))
        records = []
        for row in tree.findall("m:sheetData/m:row", NAMESPACE):
            row_number = int(row.get("r"))
            if row_number < 2:
                continue
            cells = {cell.get("r"): cell for cell in row.findall("m:c", NAMESPACE)}
            values = []
            for column in ("A", "B"):
                cell = cells.get(f"{column}{row_number}")
                if cell is None or cell.get("t", "n") != "n" or cell.find("m:f", NAMESPACE) is not None:
                    raise ValueError(f"{path.name}第{row_number}行不是两列原始数值")
                values.append(float(cell.findtext("m:v", namespaces=NAMESPACE)))
            if not all(math.isfinite(value) for value in values) or values[0] <= 0:
                raise ValueError("非有限数或非正波数")
            records.append((row_number, values[0], values[1] / 100.0))
    records.sort(key=lambda record: record[1])
    if len(records) != 7469 or len({record[1] for record in records}) != 7469:
        raise ValueError("真实附件行数或坐标唯一性与数据档案不同")
    return records


def read_inputs(result):
    scout = json.loads((ROOT / "交接/路线侦察.json").read_text(encoding="utf-8"))
    archive = json.loads((ROOT / "交接/数据档案.json").read_text(encoding="utf-8"))
    protocol = scout["共同原型协议"]
    sample = protocol["实测小样"]
    if sample["每角度点数"] != 480 or sample["基本窗口波数"] != [1200, 3800]:
        raise ValueError("共同样本发生变化，不能只修改本路线")
    if archive["总览"]["独立晶圆数"] != 2:
        raise ValueError("材料分组与数据档案不一致")
    if len(sample["折分"]) != 2 or {spec["折号"] for spec in sample["折分"]} != {1, 2}:
        raise ValueError("需要共同的两折")
    scenarios = protocol["共同光学情景"]["初始情景"]
    if len(scenarios) != 9:
        raise ValueError("初始色散情景必须为共同的9组")
    tables = {}
    for number in range(1, 5):
        filename = f"附件{number}.xlsx"
        metadata = next(item for item in archive["文件档案"] if item["文件名"] == filename)
        digest = hashlib.sha256((ROOT / "数据" / filename).read_bytes()).hexdigest()
        if digest != metadata["文件哈希"]:
            raise ValueError(f"{filename}哈希与数据档案不同")
        tables[number] = read_sheet(ROOT / "数据" / filename)
        result["输入哈希"].append({"附件": filename, "哈希": digest, "核对通过": True})
    coordinates = [record[1] for record in tables[1]]
    if any([record[1] for record in table] != coordinates for table in tables.values()):
        raise ValueError("四附件不再严格共享原坐标")
    window = [index for index, coordinate in enumerate(coordinates) if 1200 <= coordinate <= 3800]
    if len(window) < 480:
        raise ValueError("窗口内真实坐标不足480点，不允许插值补齐")
    selected = [window[order * (len(window) - 1) // 479] for order in range(480)]
    for material, numbers in MATERIALS:
        for angle, number in zip(ANGLES, numbers):
            metadata = next(item for item in archive["文件档案"] if item["文件名"] == f"附件{number}.xlsx")
            if metadata["材料"] != material or metadata["入射角度"] != angle:
                raise ValueError("附件材料或入射角分组错误")
            result["样本索引"][f"附件{number}"] = [
                {"原始行号": tables[number][index][0], "原坐标从零索引": index,
                 "波数_cm^-1": coordinates[index], "材料": material, "入射角_度": angle}
                for index in selected]
    result["折分"] = sample["折分"]
    result["每附件样本数"] = 480
    result["参数情景"] = {"初始光学情景": scenarios, "厚度搜索盒_um": list(BOUNDS[0]),
                         "参考折射率搜索盒": list(BOUNDS[1]), "色散系数搜索盒": list(BOUNDS[2]),
                         "来源": "共同光学情景；不是材料实测常数或统计区间",
                         "模板结点数": KNOTS, "循环二阶差分惩罚": ROUGHNESS,
                         "坐标缩放": "x=(波数-2500)/1300", "空气折射率情景": 1.0}
    result["辅助诊断"]["输入边界"] = {
        "原始每附件点数": 7469, "异常区与主窗口交集点数": 0,
        "依赖": "原型无上游数值依赖；未提供计划时使用路线侦察的共同原型协议。",
        "环境": "只依赖Python标准库；不导入科学计算库，不安装依赖。"}
    values = {number: [table[index][2] for index in selected] for number, table in tables.items()}
    return [coordinates[index] for index in selected], [coordinates[index] for index in window], values, scenarios


def dot(left, right):
    return sum(first * second for first, second in zip(left, right))


def quantile(values, probability):
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def solve_small(matrix, target):
    augmented = [list(row) + [value] for row, value in zip(matrix, target)]
    count = len(target)
    for column in range(count):
        pivot = max(range(column, count), key=lambda row: abs(augmented[row][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        if abs(augmented[column][column]) < 1e-14:
            raise ArithmeticError("小型正规方程奇异")
        for row in range(column + 1, count):
            factor = augmented[row][column] / augmented[column][column]
            for entry in range(column + 1, count + 1):
                augmented[row][entry] -= factor * augmented[column][entry]
    answer = [0.0] * count
    for row in reversed(range(count)):
        answer[row] = (augmented[row][-1] - sum(augmented[row][column] * answer[column]
                                              for column in range(row + 1, count))) / augmented[row][row]
    return answer


def upper_solve(matrix, target):
    answer = [0.0] * len(target)
    for row in reversed(range(len(target))):
        answer[row] = (target[row] - sum(matrix[row][column] * answer[column]
                                       for column in range(row + 1, len(target)))) / matrix[row][row]
    return answer


def residualize(values, basis):
    projections = [dot(values, direction) for direction in basis]
    residual = [value - sum(projection * direction[index] for projection, direction in zip(projections, basis))
                for index, value in enumerate(values)]
    return residual, projections


def training_data(coordinates, observed, angle):
    scaled = [(coordinate - 2500) / 1300 for coordinate in coordinates]
    basis, triangular = [], [[0.0] * 3 for unused in range(3)]
    for column, values in enumerate(([1.0] * len(scaled), scaled, [value * value for value in scaled])):
        residual = list(values)
        for unused in range(2):
            for previous, direction in enumerate(basis):
                projection = dot(residual, direction)
                triangular[previous][column] += projection
                residual = [value - projection * unit for value, unit in zip(residual, direction)]
        length = math.sqrt(dot(residual, residual))
        if length < 1e-12:
            raise ValueError("训练坐标不足以确定二次基线")
        triangular[column][column] = length
        basis.append([value / length for value in residual])
    residual, projections = residualize(observed, basis)
    return {"coordinates": coordinates, "x": scaled, "y": observed, "angle": angle,
            "basis": basis, "triangular": triangular, "projections": projections,
            "residual": residual, "scale": max(quantile(residual, 0.75) - quantile(residual, 0.25), 0.0001)}


def make_cases(coordinates, full_coordinates, values, specifications):
    cases = []
    for material, numbers in MATERIALS:
        for spec in specifications:
            training, calibration, testing = (set(spec[name]) for name in ("训练块", "校准块", "测试块"))
            if (training & calibration or training & testing or calibration & testing
                    or training | calibration | testing != set(range(1, 13))):
                raise ValueError("训练、校准、测试必须互斥且覆盖12块")
            edges = [(coordinates[index - 1] + coordinates[index]) / 2 for index in range(40, 480, 40)]
            guards = [edge for block, edge in enumerate(edges, 1) if (block in training) != (block + 1 in training)]
            allowed = lambda coordinate: (bisect.bisect_right(edges, coordinate) + 1 in training
                                          and all(abs(coordinate - edge) >= 20 for edge in guards))
            indices = [index for index, coordinate in enumerate(coordinates) if allowed(coordinate)]
            components, component = {}, 0
            for block in sorted(training):
                if block - 1 not in training:
                    component += 1
                components[block] = component
            connected = lambda coordinate: components[bisect.bisect_right(edges, coordinate) + 1]
            train_coordinates = [coordinates[index] for index in indices]
            trains = [training_data(train_coordinates, [values[number][index] for index in indices], angle)
                      for angle, number in zip(ANGLES, numbers)]
            cases.append({"material": material, "numbers": numbers, "spec": spec,
                          "coordinates": coordinates, "values": [values[number] for number in numbers],
                          "indices": indices, "trains": trains, "pool": [], "best": None,
                          "components": [connected(coordinate) for coordinate in train_coordinates],
                          "full_train": [(coordinate, connected(coordinate)) for coordinate in full_coordinates if allowed(coordinate)],
                          "scenario_best": {}, "refinements": []})
    return cases


def phase_cycles(parameters, coordinates, angle):
    thickness, reference, dispersion = parameters
    sine_squared = math.sin(math.radians(angle)) ** 2
    minimum_refractive = math.sin(math.radians(15))
    answer = []
    for coordinate in coordinates:
        refractive = reference + dispersion * ((2000 / coordinate) ** 2 - 1)
        if refractive <= minimum_refractive:
            return None
        answer.append(2 * thickness * coordinate * math.sqrt(refractive ** 2 - sine_squared) / 10000)
    return answer


def valid_parameters(parameters):
    return all(lower <= value <= upper for value, (lower, upper) in zip(parameters, BOUNDS))


def weights(cycles, count):
    answer = []
    for cycle in cycles:
        position = (cycle % 1.0) * count
        left = min(count - 1, math.floor(position))
        answer.append((left, (left + 1) % count, position - left))
    return answer


def interpolate(nodes, operator):
    return [nodes[left] * (1 - fraction) + nodes[right] * fraction for left, right, fraction in operator]


def normalize(nodes):
    center = sum(nodes) / len(nodes)
    half_range = (max(nodes) - min(nodes)) / 2
    if half_range < 1e-10:
        return [math.cos(2 * math.pi * index / len(nodes)) for index in range(len(nodes))]
    return [(value - center) / half_range for value in nodes]


def pava(values):
    blocks = []
    for value in values:
        blocks.append([value, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            last = blocks.pop()
            blocks[-1][0] += last[0]
            blocks[-1][1] += last[1]
    return [total / count for total, count in blocks for unused in range(count)]


def project_shape(nodes):
    count, half = len(nodes), len(nodes) // 2
    high, low = max(nodes), min(nodes)
    if high - low < 1e-10:
        return normalize(nodes)
    best, best_distance = None, math.inf
    for peak in range(count):
        ordered = [nodes[(peak + offset) % count] for offset in range(count)]
        descending = [-value for value in pava([-value for value in ordered[1:half]])]
        ascending = pava(ordered[half + 1:])
        feasible = [high] + descending + [low] + ascending
        distance = sum((original - projected) ** 2 for original, projected in zip(ordered, feasible))
        if distance < best_distance:
            best_distance = distance
            best = [feasible[(index - peak) % count] for index in range(count)]
    return normalize(best)


def roughness(nodes):
    return sum((nodes[(index - 1) % len(nodes)] - 2 * value + nodes[(index + 1) % len(nodes)]) ** 2
               for index, value in enumerate(nodes)) / len(nodes)


def extrema_counts(nodes):
    tolerance = max(1e-10, (max(nodes) - min(nodes)) * 1e-8)
    differences = [nodes[(index + 1) % len(nodes)] - value for index, value in enumerate(nodes)]
    signs = [1 if value > 0 else -1 for value in differences if abs(value) > tolerance]
    peaks = sum(signs[index - 1] == 1 and sign == -1 for index, sign in enumerate(signs))
    valleys = sum(signs[index - 1] == -1 and sign == 1 for index, sign in enumerate(signs))
    return peaks, valleys


def fit_envelope(train, operator, nodes):
    waveform = interpolate(nodes, operator)
    left_column = [(1 - coordinate) * value / 2 for coordinate, value in zip(train["x"], waveform)]
    right_column = [(1 + coordinate) * value / 2 for coordinate, value in zip(train["x"], waveform)]
    left_residual, left_projections = residualize(left_column, train["basis"])
    right_residual, right_projections = residualize(right_column, train["basis"])
    left_square, right_square = dot(left_residual, left_residual), dot(right_residual, right_residual)
    cross = dot(left_residual, right_residual)
    left_target, right_target = dot(left_residual, train["residual"]), dot(right_residual, train["residual"])
    candidates = [(0.0, 0.0), (max(left_target / max(left_square, 1e-16), 0.0), 0.0),
                  (0.0, max(right_target / max(right_square, 1e-16), 0.0))]
    determinant = left_square * right_square - cross * cross
    if determinant > 1e-12 * max(left_square * right_square, 1e-16):
        left_gain = (left_target * right_square - right_target * cross) / determinant
        right_gain = (right_target * left_square - left_target * cross) / determinant
        if left_gain >= 0 and right_gain >= 0:
            candidates.append((left_gain, right_gain))
    best = None
    for left_gain, right_gain in candidates:
        residual = [value - left_gain * left_value - right_gain * right_value
                    for value, left_value, right_value in zip(train["residual"], left_residual, right_residual)]
        rss = dot(residual, residual)
        if best is None or rss < best["rss"]:
            projected = [value - left_gain * left_value - right_gain * right_value
                         for value, left_value, right_value in zip(train["projections"], left_projections, right_projections)]
            best = {"nodes": list(nodes), "baseline": upper_solve(train["triangular"], projected),
                    "amplitude": [left_gain, right_gain], "rss": rss,
                    "loss": rss / len(residual) / train["scale"] ** 2 + ROUGHNESS * roughness(nodes)}
    return best


def sine_initialization(train, operator, count):
    cosines = [math.cos(2 * math.pi * index / count) for index in range(count)]
    sines = [math.sin(2 * math.pi * index / count) for index in range(count)]
    cosine_residual, unused = residualize(interpolate(cosines, operator), train["basis"])
    sine_residual, unused = residualize(interpolate(sines, operator), train["basis"])
    diagonal_cosine, diagonal_sine = dot(cosine_residual, cosine_residual), dot(sine_residual, sine_residual)
    cross = dot(cosine_residual, sine_residual)
    determinant = diagonal_cosine * diagonal_sine - cross ** 2
    if determinant <= 1e-12 * max(diagonal_cosine * diagonal_sine, 1e-16):
        return cosines
    target_cosine, target_sine = dot(cosine_residual, train["residual"]), dot(sine_residual, train["residual"])
    coefficient_cosine = (target_cosine * diagonal_sine - target_sine * cross) / determinant
    coefficient_sine = (target_sine * diagonal_cosine - target_cosine * cross) / determinant
    return normalize([coefficient_cosine * cosine + coefficient_sine * sine for cosine, sine in zip(cosines, sines)])


def update_template(train, operator, model):
    count = len(model["nodes"])
    normal = [[0.0] * count for unused in range(count)]
    target = [0.0] * count
    for index in range(count):
        difference = (((index - 1) % count, 1.0), (index, -2.0), ((index + 1) % count, 1.0))
        for row, row_value in difference:
            for column, column_value in difference:
                normal[row][column] += ROUGHNESS * row_value * column_value / count
        normal[index][index] += 1e-8
    for coordinate, observed, (left, right, fraction) in zip(train["x"], train["y"], operator):
        amplitude = ((1 - coordinate) * model["amplitude"][0] + (1 + coordinate) * model["amplitude"][1]) / 2
        baseline = model["baseline"][0] + model["baseline"][1] * coordinate + model["baseline"][2] * coordinate ** 2
        response = (observed - baseline) / train["scale"]
        left_value, right_value = amplitude * (1 - fraction) / train["scale"], amplitude * fraction / train["scale"]
        normal[left][left] += left_value ** 2 / len(operator)
        normal[right][right] += right_value ** 2 / len(operator)
        normal[left][right] += left_value * right_value / len(operator)
        normal[right][left] += left_value * right_value / len(operator)
        target[left] += left_value * response / len(operator)
        target[right] += right_value * response / len(operator)
    raw = solve_small(normal, target)
    projected = project_shape(raw)
    candidate = fit_envelope(train, operator, projected)
    if candidate["loss"] < model["loss"]:
        model = candidate
    model = dict(model)
    model["raw_extrema"] = extrema_counts(raw)
    model["projection_change"] = math.sqrt(sum((before - after) ** 2 for before, after in zip(normalize(raw), projected)) / count)
    return model


def fit_angle(train, cycles, count=KNOTS, warm=None, updates=1):
    operator = weights(cycles, count)
    if warm is None:
        nodes = sine_initialization(train, operator, count)
    else:
        nodes = project_shape(interpolate(warm["nodes"], weights([index / count for index in range(count)], len(warm["nodes"]))))
    model = fit_envelope(train, operator, nodes)
    for unused in range(updates):
        model = update_template(train, operator, model)
    return model


def evaluate(parameters, case, count=KNOTS, warm=None, updates=1, full_phases=None, diagnostic=False):
    if not diagnostic and not valid_parameters(parameters):
        return None
    if phase_cycles(parameters, (case["coordinates"][0], case["coordinates"][-1]), 15) is None:
        return None
    models = []
    for position, train in enumerate(case["trains"]):
        cycles = ([full_phases[position][index] for index in case["indices"]] if full_phases is not None
                  else phase_cycles(parameters, train["coordinates"], train["angle"]))
        if cycles is None:
            return None
        try:
            models.append(fit_angle(train, cycles, count, None if warm is None else warm["models"][position], updates))
        except ArithmeticError:
            case["numerical_rejections"] = case.get("numerical_rejections", 0) + 1
            return None
    loss = sum(model["loss"] for model in models) / 2
    if not math.isfinite(loss):
        return None
    return {"parameters": tuple(parameters), "models": models, "loss": loss}


def retain(case, candidate):
    if candidate is None:
        return
    if case["best"] is None or candidate["loss"] < case["best"]["loss"]:
        case["best"] = candidate
    combined = case["pool"] + [candidate]
    unique = {}
    for item in combined:
        previous = unique.get(item["parameters"])
        if previous is None or item["loss"] < previous["loss"]:
            unique[item["parameters"]] = item
    case["pool"] = sorted(unique.values(), key=lambda item: item["loss"])[:3]


def refine(case, seed, deadline, count=KNOTS, rounds=20):
    best = seed
    steps = [0.3, 0.12, 0.04]
    completed, stopped = 0, False
    for iteration in range(rounds):
        if time.monotonic() >= deadline:
            stopped = True
            break
        changed = False
        template_candidate = evaluate(best["parameters"], case, count, best, updates=1)
        if template_candidate is not None and template_candidate["loss"] < best["loss"]:
            best, changed = template_candidate, True
        for coordinate in range(3):
            for direction in (-1, 1):
                if time.monotonic() >= deadline:
                    stopped = True
                    break
                parameters = list(best["parameters"])
                parameters[coordinate] += direction * steps[coordinate]
                candidate = evaluate(parameters, case, count, best, updates=0)
                if candidate is not None and candidate["loss"] < best["loss"] - 1e-10:
                    best, changed = candidate, True
            if stopped:
                break
        completed = iteration + 1
        if stopped:
            break
        if not changed:
            steps = [step / 2 for step in steps]
        if max(steps) < 0.0005:
            break
    return best, {"完成轮数": completed, "轮数上限": rounds, "按时间停止": stopped,
                  "初始训练目标": seed["loss"], "最终训练目标": best["loss"]}


def model_record(candidate):
    parameters = candidate["parameters"]
    return {"厚度_um": parameters[0], "参考折射率": parameters[1], "色散系数": parameters[2],
            "训练惩罚目标": candidate["loss"],
            "边界命中": [name for name, value, (lower, upper) in zip(("厚度", "参考折射率", "色散"), parameters, BOUNDS)
                         if min(value - lower, upper - value) < 0.001 * (upper - lower)],
            "各角度模板": [
                {"入射角_度": angle, "周期结点数": len(model["nodes"]), "归一化结点": model["nodes"],
                 "结点平均": sum(model["nodes"]) / len(model["nodes"]),
                 "峰谷差": max(model["nodes"]) - min(model["nodes"]),
                 "峰数谷数": list(extrema_counts(model["nodes"])), "二次基线系数": model["baseline"],
                 "一次幅值两端点_比例": model["amplitude"], "循环二阶差分均方": roughness(model["nodes"])}
                for angle, model in zip(ANGLES, candidate["models"])]}


def predict(candidate, coordinates):
    predictions = []
    for angle, model in zip(ANGLES, candidate["models"]):
        cycles = phase_cycles(candidate["parameters"], coordinates, angle)
        waveform = interpolate(model["nodes"], weights(cycles, len(model["nodes"])))
        values = []
        for coordinate, shape in zip(coordinates, waveform):
            scaled = (coordinate - 2500) / 1300
            baseline = model["baseline"][0] + model["baseline"][1] * scaled + model["baseline"][2] * scaled ** 2
            amplitude = ((1 - scaled) * model["amplitude"][0] + (1 + scaled) * model["amplitude"][1]) / 2
            values.append(baseline + amplitude * shape)
        predictions.append(values)
    return predictions


def refresh_result(result, cases, t0):
    blocks, parameters = [], []
    for case in cases:
        if case["best"] is None:
            continue
        prediction = predict(case["best"], case["coordinates"])
        parameters.append({"材料": case["material"], "折号": case["spec"]["折号"],
                           "共享条件估计": model_record(case["best"]),
                           "有限候选厚度_um": [item["parameters"][0] for item in case["pool"]],
                           "有限情景最优候选": [{"情景序号": number, "厚度_um": item["parameters"][0],
                                                  "参考折射率": item["parameters"][1], "色散系数": item["parameters"][2],
                                                  "训练惩罚目标": item["loss"]}
                                                 for number, item in sorted(case["scenario_best"].items())],
                           "解释": "按训练目标保留的有限情景，不是统计置信区间；两折也不是独立样片。"})
        calibration = [index for index in range(480) if index // 40 + 1 in case["spec"]["校准块"]]
        for position, (angle, number) in enumerate(zip(ANGLES, case["numbers"])):
            errors = sorted(abs(prediction[position][index] - case["values"][position][index]) for index in calibration)
            half_width = errors[math.ceil(0.9 * len(errors)) - 1]
            for block in case["spec"]["测试块"]:
                indices = list(range((block - 1) * 40, block * 40))
                actual = [case["values"][position][index] for index in indices]
                predicted = [prediction[position][index] for index in indices]
                residual = [estimate - observed for estimate, observed in zip(predicted, actual)]
                rmse = math.sqrt(dot(residual, residual) / 40)
                hits = sum(abs(value) <= half_width for value in residual)
                distance = sum(min(abs(case["coordinates"][index] - coordinate)
                                   for coordinate in case["trains"][position]["coordinates"]) for index in indices) / 40
                blocks.append({"材料": case["material"], "附件": f"附件{number}", "折号": case["spec"]["折号"],
                               "入射角_度": angle, "测试块": block, "点数": 40,
                               "训练点数": len(case["indices"]), "训练尺度_比例": case["trains"][position]["scale"],
                               "小样从零索引": indices, "波数_cm^-1": [case["coordinates"][index] for index in indices],
                               "实测反射率_比例": actual, "预测反射率_比例": predicted, "残差_比例": residual,
                               "均方根误差_百分点": 100 * rmse,
                               "标准化均方根误差": rmse / case["trains"][position]["scale"],
                               "校准点数": len(calibration), "名义覆盖率": 0.9,
                               "覆盖点数": hits, "经验覆盖率": hits / 40,
                               "区间下界_比例": [value - half_width for value in predicted],
                               "区间上界_比例": [value + half_width for value in predicted],
                               "区间平均宽度_比例": 2 * half_width, "至训练坐标平均距离_cm^-1": distance})
    result["分块预测与残差"], result["厚度及单位"] = blocks, parameters
    result["完成测试块数"] = len(blocks)
    if len(blocks) == 16:
        score = sum(block["标准化均方根误差"] for block in blocks) / 16
        if not math.isfinite(score):
            raise ArithmeticError("主指标不是有限数")
        result["主指标值"] = score
        result["核心指标"] = {METRIC: score}
        result["核心指标键值"] = {METRIC: score}
        result["经验覆盖率"] = {
            "构造方法": "各折各角独立校准块绝对残差的向上取整90%经验分位半宽",
            "名义覆盖率": 0.9, "覆盖点数": sum(block["覆盖点数"] for block in blocks),
            "测试点数": 640, "测试经验覆盖率": sum(block["覆盖点数"] for block in blocks) / 640,
            "平均宽度_比例": sum(block["区间平均宽度_比例"] for block in blocks) / 16,
            "分块宽度及距离": [{name: block[name] for name in ("材料", "折号", "入射角_度", "测试块",
                                                                "至训练坐标平均距离_cm^-1", "区间平均宽度_比例", "经验覆盖率")}
                                 for block in blocks],
            "解释": "谱内相关下只是经验区间；距离不是时间步长，不使用测试覆盖率调整半宽。"}
    checkpoint(result, t0)


def phase_support(case, candidate):
    records = []
    for angle in ANGLES:
        cycles = phase_cycles(candidate["parameters"], case["trains"][0]["coordinates"], angle)
        bins = [set() for unused in range(KNOTS)]
        spans = {}
        for cycle, component in zip(cycles, case["components"]):
            slot = min(KNOTS - 1, math.floor((cycle % 1) * KNOTS))
            bins[slot].add((component, math.floor(cycle)))
            spans.setdefault(component, []).append(cycle)
        repeats = sum(max(0, math.floor(max(values)) - math.ceil(min(values))) for values in spans.values())
        full_cycles = phase_cycles(candidate["parameters"], [item[0] for item in case["full_train"]], angle)
        increments = []
        for sequence, components in ((cycles, case["components"]), (full_cycles, [item[1] for item in case["full_train"]])):
            increments.append(max((2 * math.pi * abs(sequence[index] - sequence[index - 1])
                                   for index in range(1, len(sequence)) if components[index] == components[index - 1]), default=0.0))
        records.append({"入射角_度": angle, "相位槽训练重复次数": [len(values) for values in bins],
                        "训练连通区完整周期数": repeats, "未覆盖槽数": sum(not values for values in bins),
                        "覆盖完整且至少三次重复": repeats >= 3 and min(map(len, bins)) >= 3,
                        "抽稀训练最大相邻相位差_弧度": increments[0],
                        "原始训练最大相邻相位差_弧度": increments[1],
                        "基频抽稀混叠风险": increments[0] >= math.pi,
                        "高阶形状采样说明": "基频不过奈奎斯特界不保证尖锐模板已解析；结合每槽重复数检查。"})
    return records


def shape_summary(nodes):
    count = len(nodes)
    cosines = [math.cos(2 * math.pi * index / count) for index in range(count)]
    sines = [math.sin(2 * math.pi * index / count) for index in range(count)]
    coefficient_cosine, coefficient_sine = 2 * dot(nodes, cosines) / count, 2 * dot(nodes, sines) / count
    deviation = math.sqrt(sum((value - coefficient_cosine * cosine - coefficient_sine * sine) ** 2
                              for value, cosine, sine in zip(nodes, cosines, sines)) / max(dot(nodes, nodes), 1e-12))
    midpoint, peak_width = (max(nodes) + min(nodes)) / 2, 0.0
    for index, first in enumerate(nodes):
        second = nodes[(index + 1) % count]
        if first >= midpoint and second >= midpoint:
            peak_width += 1 / count
        elif (first - midpoint) * (second - midpoint) < 0:
            fraction = (midpoint - first) / (second - first)
            peak_width += (fraction if first > midpoint else 1 - fraction) / count
    return {"相对两束正弦的结点偏离": deviation, "峰半高宽_周期": peak_width,
            "谷半高宽_周期": 1 - peak_width, "峰数谷数": list(extrema_counts(nodes)),
            "解释": "半高阈值为峰谷中值；正弦峰谷宽均为0.5周期，不作多光束显著性检验。"}


def diagnostics(case, deadline):
    best = case["best"]
    result = {"材料": case["material"], "折号": case["spec"]["折号"],
              "训练相位支持": phase_support(case, best), "模板形状": [],
              "基频减半加倍核对": [], "训练时段稳定性": [], "结点数敏感性": [],
              "双角局部厚度一致性": [], "训练内替代解释": {}, "未完成诊断": []}
    for angle, model in zip(ANGLES, best["models"]):
        result["模板形状"].append(dict(shape_summary(model["nodes"]), 入射角_度=angle))
    baseline_checks = []
    for train, model in zip(case["trains"], best["models"]):
        waveform = interpolate(model["nodes"], weights(phase_cycles(best["parameters"], train["coordinates"], train["angle"]), KNOTS))
        errors = []
        for coordinate, observed, shape in zip(train["x"], train["y"], waveform):
            amplitude = ((1 - coordinate) * model["amplitude"][0] + (1 + coordinate) * model["amplitude"][1]) / 2
            errors.append(model["baseline"][0] + model["baseline"][1] * coordinate + amplitude * shape - observed)
        baseline_checks.append({"入射角_度": train["angle"],
                                "原训练标准化均方残差": model["rss"] / len(errors) / train["scale"] ** 2,
                                "固定删除二次项后训练标准化均方残差": dot(errors, errors) / len(errors) / train["scale"] ** 2})
    result["训练内替代解释"]["基线曲率依赖"] = {
        "各角度": baseline_checks, "说明": "只删除二次基线项、其余不重估的依赖检查；不将此差异作机理检出证据。"}
    if time.monotonic() < deadline:
        constant_index = (best["parameters"][0], best["parameters"][1], 0.0)
        alternative = evaluate(constant_index, case, warm=best, updates=2)
        if alternative is None:
            result["未完成诊断"].append("无色散条件方程未稳定求解")
        else:
            result["训练内替代解释"]["无色散对照"] = {
                "共同经验色散训练目标": best["loss"], "无色散训练目标": alternative["loss"],
                "说明": "固定厚度及参考折射率、令色散为0，仅重估训练模板和基线幅值；不是全局无色散重赛。"}
    else:
        result["未完成诊断"].append("无色散对照")
    for factor in (0.5, 2.0):
        if time.monotonic() >= deadline:
            result["未完成诊断"].append(f"基频乘{factor}")
            continue
        parameters = (best["parameters"][0] * factor, *best["parameters"][1:])
        alias = evaluate(parameters, case, updates=2, diagnostic=True)
        if alias is None:
            result["未完成诊断"].append(f"基频乘{factor}的条件方程未稳定求解")
            continue
        result["基频减半加倍核对"].append({
            "基频倍数": factor, "固定候选厚度_um": parameters[0],
            "超出共同搜索盒": not valid_parameters(parameters), "训练惩罚目标": alias["loss"],
            "各角度投影前峰数谷数": [list(model["raw_extrema"]) for model in alias["models"]],
            "各角度投影后峰数谷数": [list(extrema_counts(model["nodes"])) for model in alias["models"]],
            "各角度投影改变量": [model["projection_change"] for model in alias["models"]],
            "说明": "固定倍频的训练内挑战，不按此选择最终模型；投影后必满足约束不等于投影前形状支持。"})
    for position, train in enumerate(case["trains"]):
        if time.monotonic() >= deadline:
            result["未完成诊断"].append(f"{train['angle']}度的时段及角度检查")
            continue
        local = []
        for factor in (0.95, 0.975, 1.0, 1.025, 1.05):
            if time.monotonic() >= deadline:
                result["未完成诊断"].append(f"{train['angle']}度局部厚度候选搜索")
                break
            parameters = (best["parameters"][0] * factor, *best["parameters"][1:])
            if not valid_parameters(parameters):
                continue
            fitted = fit_angle(train, phase_cycles(parameters, train["coordinates"], train["angle"]),
                               warm=best["models"][position], updates=1)
            local.append((fitted["loss"], parameters[0]))
        if local:
            selected = min(local)
            result["双角局部厚度一致性"].append({"入射角_度": train["angle"], "条件厚度_um": selected[1],
                                               "共同厚度_um": best["parameters"][0], "训练惩罚目标": selected[0],
                                               "完成候选数": len(local), "说明": "固定共同色散、训练内正负5%局部检查，不是真厚度验证。"})
        shapes = []
        for label, selected_components in (("低波数训练段", {1, 2}), ("高波数训练段", {3, 4})):
            if time.monotonic() >= deadline:
                result["未完成诊断"].append(f"{train['angle']}度分段模板检查")
                break
            selected = [index for index, component in enumerate(case["components"]) if component in selected_components]
            subset = training_data([train["coordinates"][index] for index in selected],
                                   [train["y"][index] for index in selected], train["angle"])
            fitted = fit_angle(subset, phase_cycles(best["parameters"], subset["coordinates"], train["angle"]),
                               warm=best["models"][position], updates=2)
            shapes.append({"训练段": label, "训练点数": len(selected), "归一化结点": fitted["nodes"]})
        result["训练时段稳定性"].append({"入射角_度": train["angle"], "分段模板": shapes,
                                       "两段结点均方根差": (math.sqrt(sum((left - right) ** 2 for left, right in
                                                                           zip(shapes[0]["归一化结点"], shapes[1]["归一化结点"])) / KNOTS)
                                                            if len(shapes) == 2 else None)})
    for count in (8, 16):
        if time.monotonic() >= deadline:
            result["未完成诊断"].append(f"{count}结点敏感性")
            continue
        seed = evaluate(best["parameters"], case, count=count, warm=best, updates=1)
        if seed is None:
            result["未完成诊断"].append(f"{count}结点条件方程未稳定求解")
            continue
        candidate, info = refine(case, seed, deadline, count=count, rounds=4)
        if info["按时间停止"]:
            result["未完成诊断"].append(f"{count}结点局部精修达到时间截止")
        result["结点数敏感性"].append({"结点数": count, "厚度_um": candidate["parameters"][0],
                                      "相对12结点厚度变化_百分比": 100 * (candidate["parameters"][0] / best["parameters"][0] - 1),
                                      "训练惩罚目标": candidate["loss"], "精修信息": info,
                                      "说明": "有限4轮局部训练检查，不是全局等预算重赛，不改变12结点主结果。"})
    return result


def fit_cases(result, cases, scenarios, t0):
    for case in cases:
        candidate = evaluate((8.0, 3.0, 0.0), case, updates=2)
        if candidate is None:
            candidate = evaluate((8.0, 3.0, 0.0), case, updates=0)
        if candidate is None:
            raise ArithmeticError("固定初值的训练模板无法形成有限预测")
        retain(case, candidate)
    result["运行状态"] = "全部16个测试块已有训练模板预测，继续训练内搜索"
    refresh_result(result, cases, t0)
    result["辅助诊断"]["搜索前采样核对"] = [
        {"材料": case["material"], "折号": case["spec"]["折号"],
         "口径": "以初始化共同相位在完整与抽稀训练坐标比较；最终估计另作复核，不读取密集留出反射率。",
         "核对": phase_support(case, case["best"])} for case in cases]
    completed = 0
    order = sorted(range(96), key=lambda number: int(f"{number:07b}"[::-1], 2))
    for grid_index in order:
        if time.monotonic() >= t0 + 70:
            break
        thickness = 0.5 + 39.5 * grid_index / 95
        for scenario_index, scenario in enumerate(scenarios, 1):
            if time.monotonic() >= t0 + 70:
                break
            parameters = (thickness, scenario["参考折射率"], scenario["色散系数"])
            full_phases = [phase_cycles(parameters, cases[0]["coordinates"], angle) for angle in ANGLES]
            if any(cycles is None for cycles in full_phases):
                continue
            for case in cases:
                candidate = evaluate(parameters, case, full_phases=full_phases)
                retain(case, candidate)
                previous = case["scenario_best"].get(scenario_index)
                if candidate is not None and (previous is None or candidate["loss"] < previous["loss"]):
                    case["scenario_best"][scenario_index] = candidate
            completed += 1
            if completed % 96 == 0:
                result["辅助诊断"]["粗扫"] = {"完成组合数": completed, "最大组合数": 864}
                refresh_result(result, cases, t0)
    result["辅助诊断"]["粗扫"] = {"完成组合数": completed, "最大组合数": 864,
                                     "说明": "最多9情景乘96厚度；四个材料折分任务同批处理，起点只按训练目标选最优3个。"}
    jobs = [(case, seed) for case in cases for seed in list(case["pool"])]
    for job_index, (case, seed) in enumerate(jobs):
        now = time.monotonic()
        if now >= t0 + 120:
            break
        deadline = now + (t0 + 120 - now) / (len(jobs) - job_index)
        candidate, info = refine(case, seed, deadline)
        retain(case, candidate)
        case["refinements"].append(info)
        result["辅助诊断"]["精修"] = [{"材料": item["material"], "折号": item["spec"]["折号"],
                                          "各起点": item["refinements"]} for item in cases]
        refresh_result(result, cases, t0)


def worker(t0, token):
    result = initial_result(token)
    checkpoint(result, t0)
    try:
        coordinates, full_coordinates, values, scenarios = read_inputs(result)
        cases = make_cases(coordinates, full_coordinates, values, result["折分"])
        checkpoint(result, t0)
        fit_cases(result, cases, scenarios, t0)
        checks = []
        for index, case in enumerate(cases):
            now = time.monotonic()
            if now >= t0 + min(150, SOFT_SECONDS):
                break
            deadline = now + (t0 + 150 - now) / (len(cases) - index)
            checks.append(diagnostics(case, deadline))
            result["辅助诊断"]["形状与可辨识性"] = checks
            checkpoint(result, t0)
        result["辅助诊断"]["高阶解释边界"] = {
            "反射率机制": "一峰一谷但非正弦可提示两束正弦失配，不能单独证明高阶往返。",
            "必要条件缺项": ["复折射率与衬底对比", "吸收损耗", "偏振状态", "分辨率与相干性", "真实厚度"],
            "替代解释": "二次基线和经验色散可能互补，需结合分段形状、倍频、结点敏感性和候选厚度共同解释。",
            "正式碳化硅修正": "本原型不作修正；仍交付硅及碳化硅条件厚度和完整留段预测。"}
        result["辅助诊断"]["数值稳定性"] = [
            {"材料": case["material"], "折号": case["spec"]["折号"],
             "条件方程拒绝次数": case.get("numerical_rejections", 0),
             "说明": "个别候选方程不稳定时保留已有最优训练模板，不将其写为零误差。"} for case in cases]
        refresh_result(result, cases, t0)
        warnings = ["真实厚度与绝对光学参数缺失；条件厚度及谱形预测误差不等于真实厚度精度。"]
        support = [record for item in checks for record in item["训练相位支持"]]
        incomplete = len(checks) < 4 or any(item["未完成诊断"] for item in checks)
        qualified = (len(support) == 8 and not incomplete
                     and all(item["覆盖完整且至少三次重复"] and not item["基频抽稀混叠风险"] for item in support))
        if not qualified:
            warnings.append("存在训练相位支持不足、抽稀混叠风险或未完成诊断；保留完整数值，排名资格降级，不私自加密本路线。")
        if result["经验覆盖率"]["测试经验覆盖率"] < 0.9:
            warnings.append("测试经验覆盖低于90%；不以测试残差回调半宽。")
        if result["辅助诊断"]["粗扫"]["完成组合数"] < 864:
            warnings.append("按时间截止停止粗扫，披露实际网格数量，不声称已完成全网格。")
        if any(model_record(case["best"])["边界命中"] for case in cases):
            warnings.append("有共同搜索盒边界命中；条件厚度不作精确物性结论。")
        result["失败原因"] = warnings
        result["用于同口径排名"] = qualified and result["完成测试块数"] == 16
        result["运行状态"] = "完成真实小样单指标计算" if qualified else "完成真实小样单指标计算，附资格警示"
        checkpoint(result, t0)
        try:
            append_experiment({
                "类别": "科学尝试", "问题": 3,
                "尝试": "用12结点受限周期形状与共同色散相位拟合双角光谱，在连续留段检验谱形预测。",
                "现象": f"四附件各480点，两折16个测试块；标准化均方根误差{result['主指标值']:.8g}，测试经验覆盖率{result['经验覆盖率']['测试经验覆盖率']:.6g}。",
                "决定": "保留条件厚度、相位支持及形状稳定性证据；不把非正弦或留段表现解释为真实厚度精度提高。",
                "依据": "求解结果:核心指标键值；求解结果:经验覆盖率；求解结果:辅助诊断；数据档案:未随附件提供的建模输入"})
        except Exception as error:
            result["失败原因"].append(f"实验记录追加失败：{type(error).__name__}: {error}")
        checkpoint(result, t0)
        return 0
    except Exception as error:
        result["运行状态"] = "异常结束，保留已完成结果"
        result["用于同口径排名"] = False
        result["失败原因"].append(f"{type(error).__name__}: {error}")
        checkpoint(result, t0)
        return 1


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        return worker(float(sys.argv[2]), sys.argv[3])
    t0, token = PROCESS_START, str(time.time_ns())
    result = initial_result(token)
    checkpoint(result, t0)
    failure = None
    try:
        remaining = max(0.1, HARD_SECONDS - 5 - (time.monotonic() - t0))
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(t0), token],
                                   cwd=ROOT, timeout=remaining, check=False)
        if completed.returncode:
            failure = f"子进程返回{completed.returncode}"
    except subprocess.TimeoutExpired:
        failure = "子进程触及170秒收尾保护线；父进程保留175秒内的最近完整结果"
    except Exception as error:
        failure = f"子进程启动异常：{type(error).__name__}: {error}"
    saved = json.loads(OUTPUT.read_text(encoding="utf-8"))
    if saved.get("本次运行标识") == token:
        result = saved
    if failure:
        result["运行状态"] = "未正常完成，保留本次最近检查点"
        result["失败原因"].append(failure)
        result["用于同口径排名"] = False
        result["最近完整核心指标"] = dict(result["核心指标键值"])
        result["主指标值"] = None
        try:
            append_experiment({"类别": "流程事件", "问题": 3, "尝试": "执行周期形状配准原型。",
                               "现象": failure, "决定": "保存本次已完成预测和指标，不把部分测试块用于排名。",
                               "依据": "父进程实测计时与子进程返回状态；求解结果:最近完整核心指标"})
        except Exception as error:
            result["失败原因"].append(f"实验记录追加失败：{error}")
    checkpoint(result, t0)
    print(json.dumps({"路线名": result["路线名"], "运行状态": result["运行状态"],
                      "每附件样本数": result.get("每附件样本数"), "完成测试块数": result.get("完成测试块数", 0),
                      "核心指标键值": result["核心指标键值"], "用于同口径排名": result["用于同口径排名"],
                      "条件厚度": [{"材料": item["材料"], "折号": item["折号"],
                                    "厚度_um": item["共享条件估计"]["厚度_um"]} for item in result["厚度及单位"]],
                      "测试经验覆盖率": result["经验覆盖率"].get("测试经验覆盖率"),
                      "实际用时秒": result["实际用时秒"], "失败原因": result["失败原因"]},
                     ensure_ascii=False, allow_nan=False), flush=True)
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
