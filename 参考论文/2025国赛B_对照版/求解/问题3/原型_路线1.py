"""问题三路线一：只用真实附件小样的往返衰减场反演，不绘图。

波数sigma单位cm^-1，厚度d单位微米，n=n参+c*((2000/sigma)^2-1)。
由切向波矢连续，q_j=sqrt(n_j^2-sin(theta)^2)，空气n_0=1。
切向电场的导纳取Y_s=q、Y_p=n^2/q；由界面边界条件得到
r_ij=(Y_i-Y_j)/(Y_i+Y_j)、t_ij=2*Y_i/(Y_i+Y_j)。
衬底n_2=n+对比量；z=exp(-有效损耗*n/q_1+4*pi*i*d*sigma*q_1/10000)。
有效损耗非负，合并未提供的吸收和界面损耗，不解释成消光系数。
首个返回场B=t_01*t_10*r_12*z，后续每次乘u=r_10*r_12*z。
因此两束E=r_01+B；完整E=r_01+B/(1-u)，仅在|u|<1时求和。
两偏振反射率等权混合是情景，不是已知的仪器偏振。
观测模型为二次基线+一次非负幅值包络*物理反射率；两模型同口径。
基线、幅值和物理参数只访问训练反射率，校准块仅定经验分位半宽。
两材料分别估参数，不能跨材料合并厚度；唯一排名量为16块等权误差。
只写不跑时不生成结果；正常执行会写原型结果/路线1.json并打印摘要。
"""

import time

PROCESS_START = time.monotonic()

import bisect
import cmath
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
OUTPUT = ROOT / "求解/问题3/原型结果/路线1.json"
METRIC = "连续留段标准化均方根误差"
BOUNDS = ((0.5, 40.0), (1.2, 6.0), (-1.0, 1.0), (-1.5, 2.5), (0.0, 3.0))
ANGLES = (10, 15)
MATERIALS = (("硅", (3, 4)), ("碳化硅", (1, 2)))
HARD_SECONDS = 175.0
SOFT_SECONDS = 165.0
GAIN_LIMIT = 20.0
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
        deadline = time.monotonic() + 0.8
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("实验记录追加锁等待超过0.8秒")
                time.sleep(0.01)
        path = ROOT / "交接/实验记录.json"
        records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(records, list):
            raise ValueError("实验记录必须是数组，不覆盖现有内容")
        records.append(entry)
        write_json(path, records)


def initial_result():
    return {
        "问题": 3, "路线编号": "三甲", "路线序号": 1, "路线名": "往返衰减场反演",
        "运行状态": "读取真实附件", "核心指标": {}, "核心指标键值": {},
        "主指标名称": METRIC, "主指标值": None, "主指标单位": "无量纲",
        "用于同口径排名": False, "输入哈希": {}, "样本索引": {}, "折分": [],
        "使用附件": [f"附件{number}.xlsx" for number in (1, 2, 3, 4)],
        "厚度及单位": [], "分块预测与残差": [], "经验覆盖率": {},
        "辅助诊断": {}, "失败原因": [], "随机种子": 20260909,
        "用时估计": {"性质": "预设时间分配，尚未经执行计时验证", "总上限秒": 175,
                     "粗扫截止秒": 65, "两束精修截止秒": 85, "完整精修截止秒": 125,
                     "诊断截止秒": 150, "软截止秒": 165, "父进程收尾预留秒": 5},
        "参数情景": {
            "厚度范围_微米": [0.5, 40.0], "参考折射率范围": [1.2, 6.0],
            "色散系数范围": [-1.0, 1.0], "衬底折射率对比范围": [-1.5, 2.5],
            "有效往返损耗范围": [0.0, 3.0], "两束固定衬底对比": 0.8,
            "两束固定有效损耗": 0.0, "幅值包络端点范围": [0.0, GAIN_LIMIT],
            "基线次数": 2, "幅值次数": 1, "各角度线性参数数": 5,
            "完整模型每材料新增参数数": 2, "偏振权重": [0.5, 0.5],
            "来源": "d、n参、c来自共同搜索盒；额外界面参数和幅值上限为预设数值情景，不是材料实测常数。"},
        "口径说明": {
            "主指标": "两材料、两折、两角、两测试块，共16块标准化均方根误差等权平均，越小越好。",
            "尺度": "仅训练点去二次基线残差四分位距，线性插值分位数，下限0.0001反射率比例；不是仪器噪声。",
            "样本": "1200至3800 cm^-1各480个原坐标，不插值、不平均；12块每块40点，边界保护20 cm^-1。",
            "唯一评分": "两束对照使用同一个指标；覆盖率、厚度变化、参数情景不组成额外综合评分。",
            "自由度": "完整比两束多衬底对比和有效损耗两个参数；改善可能来自自由度，不直接证明多光束。",
            "光学假设": "空气折射率1，实数低阶经验色散，正传播支，等权两偏振；窗口未经透明性标定。",
            "衰减": "z的模为exp(-有效损耗*n/q)，只表示相干往返有效衰减；不拆分吸收、粗糙及界面损耗。",
            "校准": "x=(波数-2500)/1300；二次加性基线与端点均在0至20的一次非负乘性包络，两模型相同。",
            "区间": "各折各角校准绝对残差向上取整90%经验分位作半宽；不裁剪端点；无独立同分布覆盖保证。",
            "厚度": "输出光学情景下条件厚度及候选范围，不生成统计厚度置信区间，不等同真实厚度精度。",
            "正式修正": "原型独立建立两束参照；未消费问题二正式结果，不实施碳化硅正式修正。",
            "必要条件": "没有仪器分辨率、相干信息、重复测量；不能把采样步长当分辨率或把支持等同机制证明。"}}


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
        root = ET.fromstring(archive.read(member))
        records = []
        for row in root.findall("m:sheetData/m:row", NAMESPACE):
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
                raise ValueError("波数或反射率非有限，或波数非正")
            records.append((row_number, values[0], values[1] / 100.0))
    records.sort(key=lambda record: record[1])
    if len(records) != 7469 or len({record[1] for record in records}) != 7469:
        raise ValueError("附件行数或波数唯一性与档案不一致")
    return records


def read_data(result):
    scout = json.loads((ROOT / "交接/路线侦察.json").read_text(encoding="utf-8"))
    archive = json.loads((ROOT / "交接/数据档案.json").read_text(encoding="utf-8"))
    if archive["总览"]["独立晶圆数"] != 2:
        raise ValueError("同片分组与原型约定不同")
    protocol = scout["共同原型协议"]
    sample = protocol["实测小样"]
    if sample["每角度点数"] != 480 or sample["基本窗口波数"] != [1200, 3800]:
        raise ValueError("共同样本改变，必须共同修订三条原型")
    result["折分"] = sample["折分"]
    if len(result["折分"]) != 2 or {spec["折号"] for spec in result["折分"]} != {1, 2}:
        raise ValueError("本次原型必须使用共同的两折")
    scenarios = protocol["共同光学情景"]["初始情景"]
    if len(scenarios) != 9:
        raise ValueError("共同光学情景数应为9")
    result["参数情景"]["初始情景"] = scenarios
    tables = {}
    for number in range(1, 5):
        path = ROOT / f"数据/附件{number}.xlsx"
        result["输入哈希"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        tables[number] = read_sheet(path)
    coordinates = [record[1] for record in tables[1]]
    if any([record[1] for record in tables[number]] != coordinates for number in (2, 3, 4)):
        raise ValueError("四谱波数没有逐点对齐")
    window_indices = [index for index, coordinate in enumerate(coordinates) if 1200 <= coordinate <= 3800]
    if len(window_indices) < 480:
        raise ValueError("真实窗口不足480个原坐标，不允许补点或插值")
    indices = [window_indices[order * (len(window_indices) - 1) // 479] for order in range(480)]
    diagnostic_indices = sorted(set([order * 7468 // 127 for order in range(128)] + [0]))
    suspicious = [index for index, coordinate in enumerate(coordinates) if 801.278 <= coordinate <= 927.1104]
    diagnostic_indices = sorted(set(diagnostic_indices + [suspicious[order * (len(suspicious) - 1) // 15] for order in range(16)]))
    result["辅助诊断"]["异常面板"] = {
        "用于拟合或评分": False, "主窗口与首点及超百段交集点数": 0,
        "说明": "面板仅保留异常证据；不在声明生效窗口外外推色散，不宣称清洗改善。",
        "各附件": {f"附件{number}": [{"原始行号": tables[number][index][0],
                                     "波数_cm^-1": coordinates[index], "反射率_比例": tables[number][index][2]}
                                    for index in diagnostic_indices] for number in range(1, 5)}}
    samples = {}
    for material, numbers in MATERIALS:
        samples[material] = [[tables[number][index][2] for index in indices] for number in numbers]
        for angle, number in zip(ANGLES, numbers):
            result["样本索引"][f"附件{number}"] = [
                {"原始行号": tables[number][index][0], "源附件": f"附件{number}.xlsx", "材料": material,
                 "入射角_度": angle, "波数_cm^-1": coordinates[index], "原坐标从零索引": index,
                 "质量标记": "超百分之百" if tables[number][index][2] > 1 else "窗口内保留原值"}
                for index in indices]
    result["每附件样本数"] = len(indices)
    return [coordinates[index] for index in indices], [coordinates[index] for index in window_indices], samples, scenarios


def dot(left, right):
    return sum(first * second for first, second in zip(left, right))


def quantile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def backsolve(matrix, target):
    answer = [0.0] * len(target)
    for row in reversed(range(len(target))):
        answer[row] = (target[row] - sum(matrix[row][column] * answer[column]
                                      for column in range(row + 1, len(target)))) / matrix[row][row]
    return answer


def training_basis(scaled, observed):
    basis = []
    triangular = [[0.0] * 3 for unused in range(3)]
    for column_number, column in enumerate(([1.0] * len(scaled), scaled, [value * value for value in scaled])):
        residual = list(column)
        for unused in range(2):
            for basis_number, unit in enumerate(basis):
                projection = dot(unit, residual)
                triangular[basis_number][column_number] += projection
                residual = [value - projection * direction for value, direction in zip(residual, unit)]
        length = math.sqrt(dot(residual, residual))
        if length < 1e-12:
            raise ValueError("训练坐标无法确定二次基线")
        triangular[column_number][column_number] = length
        basis.append([value / length for value in residual])
    projected = [dot(unit, observed) for unit in basis]
    residual = [value - sum(projected[column] * basis[column][row] for column in range(3))
                for row, value in enumerate(observed)]
    return {"basis": basis, "triangular": triangular, "projected": projected, "residual": residual,
            "rss": dot(residual, residual), "scale": max(quantile(residual, 0.75) - quantile(residual, 0.25), 0.0001)}


def make_cases(coordinates, full_coordinates, samples, specs):
    cases = []
    for material, numbers in MATERIALS:
        for spec in specs:
            training, calibration, testing = (set(spec[key]) for key in ("训练块", "校准块", "测试块"))
            if training & calibration or training & testing or calibration & testing or training | calibration | testing != set(range(1, 13)):
                raise ValueError("两折的训练、校准、测试块必须互斥且覆盖12块")
            edges = [(coordinates[index - 1] + coordinates[index]) / 2 for index in range(40, 480, 40)]
            guards = [edge for block, edge in enumerate(edges, 1) if (block in training) != (block + 1 in training)]
            allowed = lambda coordinate: (bisect.bisect_right(edges, coordinate) + 1 in training
                                          and all(abs(coordinate - edge) >= 20 for edge in guards))
            indices = [index for index, coordinate in enumerate(coordinates) if allowed(coordinate)]
            components = {}
            component_number = 0
            for block in sorted(training):
                if block - 1 not in training:
                    component_number += 1
                components[block] = component_number
            connected = lambda coordinate: components[bisect.bisect_right(edges, coordinate) + 1]
            scaled = [(coordinate - 2500) / 1300 for coordinate in coordinates]
            angle_data = []
            for angle_index, angle in enumerate(ANGLES):
                observed = [samples[material][angle_index][index] for index in indices]
                train_scaled = [scaled[index] for index in indices]
                angle_data.append(dict(training_basis(train_scaled, observed), angle=angle, y=observed, x=train_scaled))
            cases.append({"material": material, "numbers": numbers, "spec": spec, "coordinates": coordinates,
                          "values": samples[material], "scaled": scaled, "indices": indices, "angles": angle_data,
                          "full_train": [(value, connected(value)) for value in full_coordinates if allowed(value)],
                          "sample_train": [(coordinates[index], connected(coordinates[index])) for index in indices],
                          "pool": [], "baseline": None, "complete": None, "refinements": []})
    return cases


def valid(parameters):
    if len(parameters) != 5 or any(not lower <= value <= upper for value, (lower, upper) in zip(parameters, BOUNDS)):
        return False
    for coordinate in (1200, 3800):
        refractive = parameters[1] + parameters[2] * ((2000 / coordinate) ** 2 - 1)
        if min(refractive, refractive + parameters[3]) <= math.sin(math.radians(15)) + 1e-7:
            return False
    return True


def interface_terms(parameters, coordinate, angle):
    thickness, reference, dispersion, contrast, loss = parameters
    refractive = reference + dispersion * ((2000 / coordinate) ** 2 - 1)
    substrate = refractive + contrast
    sine_squared = math.sin(math.radians(angle)) ** 2
    air_q = math.cos(math.radians(angle))
    film_q = math.sqrt(refractive * refractive - sine_squared)
    substrate_q = math.sqrt(substrate * substrate - sine_squared)
    propagation = cmath.exp(complex(-loss * refractive / film_q,
                                   4 * math.pi * thickness * coordinate * film_q / 10000))
    terms = []
    for air_admittance, film_admittance, substrate_admittance in (
            (air_q, film_q, substrate_q),
            (1 / air_q, refractive * refractive / film_q, substrate * substrate / substrate_q)):
        reflection = (air_admittance - film_admittance) / (air_admittance + film_admittance)
        reverse = -reflection
        bottom = (film_admittance - substrate_admittance) / (film_admittance + substrate_admittance)
        forward_transmission = 2 * air_admittance / (air_admittance + film_admittance)
        reverse_transmission = 2 * film_admittance / (air_admittance + film_admittance)
        first = forward_transmission * reverse_transmission * bottom * propagation
        ratio = reverse * bottom * propagation
        terms.append((reflection, first, ratio, abs(propagation)))
    return terms


def reflectances(parameters, coordinates, complete):
    if not valid(parameters):
        return None
    values = []
    for angle in ANGLES:
        spectrum = []
        for coordinate in coordinates:
            intensity = 0.0
            for reflection, first, ratio, attenuation in interface_terms(parameters, coordinate, angle):
                if attenuation > 1 + 1e-12 or abs(ratio) >= 1:
                    return None
                field = reflection + first / (1 - ratio) if complete else reflection + first
                intensity += 0.5 * abs(field) ** 2
            spectrum.append(intensity)
        values.append(spectrum)
    return values


def calibrate(physical, angle_data, indices):
    selected = [physical[index] for index in indices]
    columns = [[value * (1 + sign * coordinate) / 2 for value, coordinate in zip(selected, angle_data["x"])]
               for sign in (-1, 1)]
    projections = [[dot(unit, column) for unit in angle_data["basis"]] for column in columns]
    residuals = [[value - sum(projected[basis_number] * angle_data["basis"][basis_number][row]
                              for basis_number in range(3)) for row, value in enumerate(column)]
                 for column, projected in zip(columns, projections)]
    left_ss, cross, right_ss = dot(residuals[0], residuals[0]), dot(residuals[0], residuals[1]), dot(residuals[1], residuals[1])
    left_target, right_target = (dot(residual, angle_data["residual"]) for residual in residuals)
    candidates = []
    determinant = left_ss * right_ss - cross * cross
    if determinant > max(1e-30, 1e-12 * left_ss * right_ss):
        left_gain = (right_ss * left_target - cross * right_target) / determinant
        right_gain = (left_ss * right_target - cross * left_target) / determinant
        if 0 <= left_gain <= GAIN_LIMIT and 0 <= right_gain <= GAIN_LIMIT:
            candidates.append((left_gain, right_gain))
    for boundary in (0.0, GAIN_LIMIT):
        candidates.append((boundary, min(GAIN_LIMIT, max(0.0, (right_target - cross * boundary) / max(right_ss, 1e-30)))))
        candidates.append((min(GAIN_LIMIT, max(0.0, (left_target - cross * boundary) / max(left_ss, 1e-30))), boundary))
    def squared_error(gains):
        left_gain, right_gain = gains
        return (angle_data["rss"] - 2 * (left_gain * left_target + right_gain * right_target)
                + left_gain * left_gain * left_ss + 2 * left_gain * right_gain * cross + right_gain * right_gain * right_ss)
    gains = min(candidates, key=squared_error)
    baseline_target = [angle_data["projected"][index] - gains[0] * projections[0][index] - gains[1] * projections[1][index]
                       for index in range(3)]
    baseline = backsolve(angle_data["triangular"], baseline_target)
    return {"loss": max(0.0, squared_error(gains)) / len(indices) / angle_data["scale"] ** 2,
            "baseline": baseline, "gains": gains}


def evaluate(parameters, case, complete, physical=None):
    if physical is None:
        physical = reflectances(parameters, case["coordinates"], complete)
    if physical is None:
        return None
    models = [calibrate(spectrum, angle_data, case["indices"]) for spectrum, angle_data in zip(physical, case["angles"])]
    loss = sum(model["loss"] for model in models) / 2
    if not math.isfinite(loss):
        return None
    return {"parameters": tuple(parameters), "loss": loss, "models": models}


def retain(case, candidate):
    if candidate is None:
        return
    pool = case["pool"] + [candidate]
    selected = []
    for entry in sorted(pool, key=lambda item: item["loss"]):
        if all(any(abs(entry["parameters"][index] - existing["parameters"][index]) > threshold
                   for index, threshold in enumerate((0.3, 0.15, 0.05))) for existing in selected):
            selected.append(entry)
        if len(selected) == 3:
            break
    case["pool"] = selected
    case["baseline"] = selected[0]


def refine(seed, case, complete, deadline):
    best = seed
    steps = [0.24, 0.12, 0.05, 0.18, 0.15]
    rounds = 0
    evaluations = 0
    for round_number in range(20):
        if time.monotonic() >= deadline:
            break
        improved = False
        rounds = round_number + 1
        for index in range(5 if complete else 3):
            for direction in (-1, 1):
                if time.monotonic() >= deadline:
                    return best, {"轮数": rounds, "评估次数": evaluations, "提前收敛": True}
                trial = list(best["parameters"])
                trial[index] = min(BOUNDS[index][1], max(BOUNDS[index][0], trial[index] + direction * steps[index]))
                if tuple(trial) == best["parameters"]:
                    continue
                candidate = evaluate(trial, case, complete)
                evaluations += 1
                if candidate is not None and candidate["loss"] < best["loss"]:
                    best, improved = candidate, True
        if not improved:
            steps = [step * 0.5 for step in steps]
        if max(steps) < 0.001:
            break
    return best, {"轮数": rounds, "评估次数": evaluations, "提前收敛": time.monotonic() >= deadline}


def predictions(case, candidate, complete):
    physical = reflectances(candidate["parameters"], case["coordinates"], complete)
    values = []
    for spectrum, model in zip(physical, candidate["models"]):
        baseline, gains = model["baseline"], model["gains"]
        values.append([baseline[0] + baseline[1] * coordinate + baseline[2] * coordinate * coordinate
                       + (gains[0] * (1 - coordinate) + gains[1] * (1 + coordinate)) / 2 * intensity
                       for coordinate, intensity in zip(case["scaled"], spectrum)])
    return values


def parameter_record(candidate):
    parameters = candidate["parameters"]
    return {"厚度_微米": parameters[0], "参考折射率": parameters[1], "色散系数": parameters[2],
            "衬底折射率对比": parameters[3], "有效往返损耗": parameters[4],
            "训练标准化均方残差": candidate["loss"],
            "边界命中参数序号": [index + 1 for index, (value, bounds) in enumerate(zip(parameters, BOUNDS))
                               if min(value - bounds[0], bounds[1] - value) < 1e-6],
            "各角度校准": [{"入射角_度": angle, "二次基线系数": model["baseline"],
                            "一次幅值端点": list(model["gains"]),
                            "幅值上限命中": any(abs(value - GAIN_LIMIT) < 1e-6 for value in model["gains"])}
                           for angle, model in zip(ANGLES, candidate["models"])]}


def refresh_result(result, cases, t0):
    blocks, thicknesses = [], []
    for case in cases:
        if case["baseline"] is None or case["complete"] is None:
            continue
        baseline_values = predictions(case, case["baseline"], False)
        complete_values = predictions(case, case["complete"], True)
        thicknesses.append({"材料": case["material"], "使用附件": [f"附件{number}" for number in case["numbers"]],
                            "折号": case["spec"]["折号"],
                            "两束": parameter_record(case["baseline"]), "完整往返": parameter_record(case["complete"]),
                            "厚度变化_微米": case["complete"]["parameters"][0] - case["baseline"]["parameters"][0],
                            "范围性质": "有限训练候选和折间情景，不是统计置信区间",
                            "两束训练候选厚度_微米": [entry["parameters"][0] for entry in case["pool"]]})
        calibration_indices = [index for index in range(480) if index // 40 + 1 in case["spec"]["校准块"]]
        for angle_index, (angle, number) in enumerate(zip(ANGLES, case["numbers"])):
            observed = case["values"][angle_index]
            predicted = complete_values[angle_index]
            baseline = baseline_values[angle_index]
            calibration_errors = sorted(abs(predicted[index] - observed[index]) for index in calibration_indices)
            half_width = calibration_errors[math.ceil(0.9 * len(calibration_errors)) - 1]
            for block in case["spec"]["测试块"]:
                indices = list(range((block - 1) * 40, block * 40))
                residuals = [predicted[index] - observed[index] for index in indices]
                base_residuals = [baseline[index] - observed[index] for index in indices]
                rmse = math.sqrt(dot(residuals, residuals) / 40)
                base_rmse = math.sqrt(dot(base_residuals, base_residuals) / 40)
                scale = case["angles"][angle_index]["scale"]
                hits = sum(abs(error) <= half_width for error in residuals)
                distance = [min(abs(case["coordinates"][index] - case["coordinates"][train_index])
                                for train_index in case["indices"]) for index in indices]
                blocks.append({"材料": case["material"], "附件": f"附件{number}", "折号": case["spec"]["折号"],
                               "入射角_度": angle, "测试块": block, "点数": 40, "训练点数": len(case["indices"]),
                               "波数_cm^-1": [case["coordinates"][index] for index in indices],
                               "小样从零索引": indices, "实测反射率_比例": [observed[index] for index in indices],
                               "完整预测反射率_比例": [predicted[index] for index in indices],
                               "两束预测反射率_比例": [baseline[index] for index in indices],
                               "完整残差_比例": residuals, "两束残差_比例": base_residuals,
                               "训练尺度_比例": scale, "完整标准化均方根误差": rmse / scale,
                               "两束标准化均方根误差": base_rmse / scale,
                               "完整均方根误差_百分点": 100 * rmse, "两束均方根误差_百分点": 100 * base_rmse,
                               "名义覆盖率": 0.9, "校准点数": len(calibration_indices), "覆盖点数": hits,
                               "经验覆盖率": hits / 40, "区间平均宽度_比例": 2 * half_width,
                               "区间下界_比例": [predicted[index] - half_width for index in indices],
                               "区间上界_比例": [predicted[index] + half_width for index in indices],
                               "至最近训练波数平均距离_cm^-1": sum(distance) / 40})
    result["分块预测与残差"], result["厚度及单位"] = blocks, thicknesses
    result["完成测试块数"] = len(blocks)
    if len(blocks) == 16:
        score = sum(block["完整标准化均方根误差"] for block in blocks) / 16
        if not math.isfinite(score):
            raise ValueError("完整主指标非有限")
        result["主指标值"] = score
        result["核心指标"] = result["核心指标键值"] = {METRIC: score}
        result["两束对照同指标值"] = sum(block["两束标准化均方根误差"] for block in blocks) / 16
        result["经验覆盖率"] = {"构造方法": "各折各角独立校准的向上取整90%绝对残差经验分位",
                                 "名义覆盖率": 0.9, "覆盖点数": sum(block["覆盖点数"] for block in blocks),
                                 "测试点数": 640, "测试经验覆盖率": sum(block["覆盖点数"] for block in blocks) / 640,
                                 "平均宽度_比例": sum(block["区间平均宽度_比例"] for block in blocks) / 16,
                                 "距离及宽度变化": [{key: block[key] for key in ("材料", "折号", "入射角_度", "测试块",
                                                                          "至最近训练波数平均距离_cm^-1", "区间平均宽度_比例", "经验覆盖率")}
                                                   for block in blocks],
                                 "解释": "谱内相关，不宣称分布无关保证；距离不是时间预测步长，不用测试覆盖重调区间。"}
        support = []
        for number in range(1, 5):
            differences = []
            for fold_number in (1, 2):
                selected = [block for block in blocks if block["附件"] == f"附件{number}" and block["折号"] == fold_number]
                differences.append(sum(block["两束标准化均方根误差"] - block["完整标准化均方根误差"] for block in selected) / 2)
            label = "两折误差均改善，仅描述性支持" if all(value > 0 for value in differences) else "折间不一致" if any(value > 0 for value in differences) else "两折均未改善"
            support.append({"附件": f"附件{number}", "逐折两束减完整误差": differences, "支持描述": label,
                            "同片共同厚度的逐折变化": [{"折号": item["折号"],
                                                       "两束厚度_微米": item["两束"]["厚度_微米"],
                                                       "完整厚度_微米": item["完整往返"]["厚度_微米"],
                                                       "厚度变化_微米": item["厚度变化_微米"],
                                                       "两束训练损失": item["两束"]["训练标准化均方残差"],
                                                       "完整训练损失": item["完整往返"]["训练标准化均方残差"]}
                                                      for item in thicknesses if f"附件{number}" in item["使用附件"]],
                            "阈值说明": "仅以差值正负描述，无显著性或物理检出率承诺"})
        result["辅助诊断"]["每附件高阶支持"] = support
    checkpoint(result, t0)


def alias_diagnostic(case, parameter_sets):
    maxima = {"原始训练坐标最大相邻相位差_弧度": 0.0, "抽稀训练坐标最大相邻相位差_弧度": 0.0}
    for parameters in parameter_sets:
        if not valid(parameters):
            continue
        for angle in ANGLES:
            sine_squared = math.sin(math.radians(angle)) ** 2
            for key, selected in zip(maxima, (case["full_train"], case["sample_train"])):
                previous = None
                for coordinate, component in selected:
                    refractive = parameters[1] + parameters[2] * ((2000 / coordinate) ** 2 - 1)
                    phase = 4 * math.pi * parameters[0] * coordinate * math.sqrt(refractive * refractive - sine_squared) / 10000
                    if previous is not None and component == previous[1]:
                        maxima[key] = max(maxima[key], abs(phase - previous[0]))
                    previous = (phase, component)
    maxima["材料"], maxima["折号"] = case["material"], case["spec"]["折号"]
    maxima["基频抽稀混叠风险"] = maxima["抽稀训练坐标最大相邻相位差_弧度"] >= math.pi
    maxima["含义"] = "逐训练连通区检查，不跨留出缺口；风险只降级说明，不阻止输出条件答案。"
    return maxima


def field_checks(case):
    parameters = case["complete"]["parameters"]
    closed_error, truncated_error, maximum_ratio, maximum_attenuation, maximum_tail = (0.0,) * 5
    passive_maximum = 0.0
    for angle in ANGLES:
        for index in range(0, 480, 24):
            intensity = 0.0
            for reflection, first, ratio, attenuation in interface_terms(parameters, case["coordinates"][index], angle):
                closed = reflection + first / (1 - ratio)
                partial, term = complex(reflection), first
                for order in range(512):
                    partial += term
                    if order == 0:
                        truncated_error = max(truncated_error, abs(partial - (reflection + first)))
                    term *= ratio
                    tail = abs(term) / (1 - abs(ratio))
                    if tail < 1e-12:
                        break
                closed_error = max(closed_error, abs(partial - closed))
                maximum_tail = max(maximum_tail, tail)
                maximum_ratio = max(maximum_ratio, abs(ratio))
                maximum_attenuation = max(maximum_attenuation, attenuation)
                intensity += 0.5 * abs(closed) ** 2
            passive_maximum = max(passive_maximum, intensity)
    return {"材料": case["material"], "折号": case["spec"]["折号"], "一次往返截断误差": truncated_error,
            "几何级数与闭式最大绝对误差": closed_error, "尾项绝对上界": maximum_tail,
            "最大往返乘子模": maximum_ratio, "最大传播因子模": maximum_attenuation,
            "物理反射率最大值_比例": passive_maximum,
            "通过": (truncated_error < 1e-12 and closed_error <= maximum_tail + 1e-10
                     and maximum_ratio < 1 and maximum_attenuation <= 1 + 1e-12 and passive_maximum <= 1 + 1e-10),
            "百分之一场振幅尾项对应往返阶数": (max(1, math.ceil(math.log(0.01) / math.log(maximum_ratio)))
                                               if maximum_ratio > 0 else 1),
            "解释": "物理场核对不含加性基线和幅值校准；无非物理传播增益。"}


def local_scenarios(case, deadline):
    best = case["complete"]
    records = []
    for label, index, factor in (("衬底对比减百分之二十", 3, 0.8), ("衬底对比增百分之二十", 3, 1.2),
                                 ("有效损耗减百分之二十", 4, 0.8), ("有效损耗增百分之二十", 4, 1.2),
                                 ("衬底对比和损耗联合补偿", 3, 1.2),
                                 ("厚度折射率互补方向", 0, 1.1)):
        if time.monotonic() >= deadline:
            break
        trial = list(best["parameters"])
        trial[index] *= factor
        if index == 4 and best["parameters"][4] == 0:
            trial[index] = 0.1 if factor < 1 else 0.2
        if index == 3 and best["parameters"][3] == 0:
            trial[index] = -0.1 if factor < 1 else 0.1
        if index == 0:
            trial[1], trial[2] = trial[1] / factor, trial[2] / factor
        if label == "衬底对比和损耗联合补偿":
            trial[4] += math.log(factor)
        candidate = evaluate(trial, case, True)
        records.append({"情景": label, "参数": parameter_record(candidate) if candidate is not None else None,
                        "可行": candidate is not None, "基准训练损失": best["loss"],
                        "说明": "只重估训练基线幅值，不按此诊断选择最终模型；零参数用预设加性扰动。"})
    return {"材料": case["material"], "折号": case["spec"]["折号"], "训练参数互补情景": records}


def fit_cases(result, cases, scenarios, t0):
    for case in cases:
        retain(case, evaluate((8.0, 3.0, 0.0, 0.8, 0.0), case, False))
        case["complete"] = evaluate(case["baseline"]["parameters"], case, True)
    result["运行状态"] = "已形成全部留段初值预测，继续训练内搜索"
    refresh_result(result, cases, t0)
    diagnostic_sets = [(40.0, scene["参考折射率"], scene["色散系数"], 0.8, 0.0) for scene in scenarios]
    result["辅助诊断"]["搜索前混叠"] = [alias_diagnostic(case, diagnostic_sets) for case in cases]
    searched = 0
    for scene in scenarios:
        for thickness_index in range(96):
            if time.monotonic() >= t0 + 65:
                break
            parameters = (0.5 + thickness_index * 39.5 / 95, scene["参考折射率"], scene["色散系数"], 0.8, 0.0)
            physical = reflectances(parameters, cases[0]["coordinates"], False)
            if physical is not None:
                for case in cases:
                    retain(case, evaluate(parameters, case, False, physical))
            searched += 1
        result["辅助诊断"]["唯一全局粗扫"] = {"全局扫描次数": 1, "已算网格组合": searched,
                                               "最多网格组合": 864, "共享内容": "只缓存前向函数值；两材料两折各按自己的训练损失独立选厚度。"}
        for case in cases:
            candidate = evaluate(case["baseline"]["parameters"], case, True)
            if candidate["loss"] < case["complete"]["loss"]:
                case["complete"] = candidate
        refresh_result(result, cases, t0)
        if time.monotonic() >= t0 + 65:
            break
    for case_index, case in enumerate(cases):
        deadline = time.monotonic() + max(0.0, t0 + 85 - time.monotonic()) / (len(cases) - case_index)
        seeds = list(case["pool"])
        for seed_index, seed in enumerate(seeds):
            local_deadline = time.monotonic() + max(0.0, deadline - time.monotonic()) / (len(seeds) - seed_index)
            candidate, record = refine(seed, case, False, local_deadline)
            retain(case, candidate)
            case["refinements"].append(dict(record, 模型="两束", 起点=seed_index + 1))
        refresh_result(result, cases, t0)
    for case_index, case in enumerate(cases):
        deadline = time.monotonic() + max(0.0, t0 + 125 - time.monotonic()) / (len(cases) - case_index)
        seeds = [evaluate(seed["parameters"], case, True) for seed in case["pool"][:3]]
        for seed_index, seed in enumerate(seeds):
            local_deadline = time.monotonic() + max(0.0, deadline - time.monotonic()) / (len(seeds) - seed_index)
            candidate, record = refine(seed, case, True, local_deadline)
            if candidate["loss"] < case["complete"]["loss"]:
                case["complete"] = candidate
            case["refinements"].append(dict(record, 模型="完整往返", 起点=seed_index + 1))
        refresh_result(result, cases, t0)
    result["辅助诊断"]["精修记录"] = [{"材料": case["material"], "折号": case["spec"]["折号"],
                                         "各起点": case["refinements"]} for case in cases]


def worker(t0):
    result = initial_result()
    checkpoint(result, t0)
    try:
        coordinates, full_coordinates, samples, scenarios = read_data(result)
        cases = make_cases(coordinates, full_coordinates, samples, result["折分"])
        checkpoint(result, t0)
        fit_cases(result, cases, scenarios, t0)
        checks, aliases, complements = [], [], []
        for case in cases:
            if time.monotonic() >= t0 + SOFT_SECONDS:
                break
            checks.append(field_checks(case))
            alias = alias_diagnostic(case, [case["baseline"]["parameters"], case["complete"]["parameters"]])
            alias["高阶抽稀保守风险"] = (alias["抽稀训练坐标最大相邻相位差_弧度"]
                                         * checks[-1]["百分之一场振幅尾项对应往返阶数"] >= math.pi)
            alias["高阶解释"] = "以场级数衰减到百分之一的阶数作保守采样警示；不是多光束检出阈值。"
            aliases.append(alias)
            complements.append(local_scenarios(case, t0 + 150))
            result["辅助诊断"]["电场必要核对"] = checks
            result["辅助诊断"]["拟合后混叠"] = aliases
            result["辅助诊断"]["新增参数互补"] = complements
            checkpoint(result, t0)
        warnings = ["训练外谱形表现不是厚度真值验证；仪器条件未知，不能正式判断多光束存在或修正碳化硅。"]
        if result["经验覆盖率"]["测试经验覆盖率"] < 0.9:
            warnings.append("经验覆盖不足90%；如实保留，未用测试残差调宽区间。")
        if result["主指标值"] >= result["两束对照同指标值"]:
            warnings.append("完整模型未改善总体留段误差，仍输出全部条件厚度及预测，不因对比不利拒绝作答。")
        if any(record["提前收敛"] for case in cases for record in case["refinements"]):
            warnings.append("部分坐标搜索按分配时间停止，使用已得最优训练候选；没有以测试表现选起点。")
        if result["辅助诊断"]["唯一全局粗扫"]["已算网格组合"] < 864:
            warnings.append("唯一全局粗扫在预设截止时刻停止，披露已计算网格数，不把部分搜索说成全网格搜索。")
        if any(item.get("高阶抽稀保守风险", False) for item in aliases):
            warnings.append("有高阶谱形抽稀保守风险；保留条件数值，需三路线共同更密取样复核。")
        if any(model["幅值上限命中"] for item in result["厚度及单位"]
               for model in item["完整往返"]["各角度校准"]):
            warnings.append("至少一组完整模型幅值达到预设上限，可能依赖校准自由度；不解释为物理高阶证据。")
        all_aliases = result["辅助诊断"].get("搜索前混叠", []) + aliases
        eligible = (len(checks) == 4 and all(item["通过"] for item in checks)
                    and not any(item["基频抽稀混叠风险"] or item.get("高阶抽稀保守风险", False) for item in all_aliases))
        if not eligible:
            warnings.append("必要核对缺项或基频抽稀存在风险；保留完整数值，排名资格降级，密度只能三路线共同调整。")
        result["置信限制"], result["用于同口径排名"] = warnings, eligible
        result["运行状态"] = "已完成" if eligible else "已完成数值，资格需复核"
        checkpoint(result, t0)
        try:
            append_experiment({"类别": "科学尝试", "问题": 3,
                               "尝试": "比较同片共享厚度色散的两束场与含衬底对比和有效损耗的完整往返场。",
                               "现象": f"四附件各480点，两折16个测试块；完整误差{result['主指标值']:.8g}，两束误差{result['两束对照同指标值']:.8g}，经验覆盖{result['经验覆盖率']['测试经验覆盖率']:.6g}。",
                               "决定": "保留两种参数化及各附件分折证据；不把训练或留段改善解释为真实厚度精度提高。",
                               "依据": "求解结果:核心指标；求解结果:两束对照同指标值；求解结果:辅助诊断/每附件高阶支持"})
        except Exception as error:
            result["失败原因"].append(f"实验记录追加失败但数值保留：{error}")
        checkpoint(result, t0)
        return 0
    except Exception as error:
        result["运行状态"] = "中断，保留已完成数值"
        result["用于同口径排名"] = False
        result["失败原因"].append(f"{type(error).__name__}: {error}")
        checkpoint(result, t0)
        return 1


def main():
    t0 = PROCESS_START
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        return worker(float(sys.argv[2]))
    if len(sys.argv) != 1:
        raise SystemExit("用法：python3 求解/问题3/原型_路线1.py")
    checkpoint(initial_result(), t0)
    failure = None
    try:
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(t0)],
                                   cwd=ROOT, timeout=max(0.1, HARD_SECONDS - (time.monotonic() - t0)), check=False)
        if completed.returncode:
            failure = f"子进程退出码{completed.returncode}"
    except subprocess.TimeoutExpired:
        failure = "达到175秒外层限时；保留最近完整预测，不以部分块均值参赛。"
    result = json.loads(OUTPUT.read_text(encoding="utf-8"))
    if failure:
        result["失败原因"].append(failure)
        result["用于同口径排名"] = False
        result["运行状态"] = "未正常完成，保留已形成答案"
        try:
            append_experiment({"类别": "流程事件", "问题": 3, "尝试": "执行往返衰减场原型。",
                               "现象": failure, "决定": "保留最近检查点的真实预测和已完整形成的指标，不拼接分次执行成绩。",
                               "依据": "本次父进程实际计时及子进程返回状态。"})
        except Exception as error:
            result["失败原因"].append(f"追加记录失败：{error}")
    checkpoint(result, t0)
    print(json.dumps({"路线名": result["路线名"], "运行状态": result["运行状态"],
                      "每附件样本数": result.get("每附件样本数"), "完成测试块数": result.get("完成测试块数", 0),
                      "核心指标": result["核心指标"], "两束对照同指标值": result.get("两束对照同指标值"),
                      "条件厚度": result["厚度及单位"], "经验覆盖率": result["经验覆盖率"].get("测试经验覆盖率"),
                      "实际用时秒": result["实际用时秒"], "失败原因": result["失败原因"]}, ensure_ascii=False, allow_nan=False), flush=True)
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
