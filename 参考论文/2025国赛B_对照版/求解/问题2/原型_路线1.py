"""问题二路线一原型；默认命令自带175秒父进程限时，不生成图。

按实测小样协议，R=原始反射率百分数/100，d的搜索单位为微米。
透明两束近似给出q=sqrt(n²-sin²θ)，故相位为4π(d/10000)σq。
共同经验色散为n=n参+c*((2000/σ)²-1)，不是已知材料光学常数。
各折只用训练坐标定义x=(σ-训练中点)/训练半跨度，预测时不截断x。
模型为b0+b1*x+b2*x²+(a0+a1*x)*cos(相位+ψ)，两角共享d、n参、c。
对给定物理参数及一个角度常相位，投影消去三个基线系数，再解
两个幅值系数；正弦列仅计算常相位的充分统计量，不单独自由拟合。
所有非线性候选仅按训练残差选择；校准块只确定经验预测半宽。
唯一排名量是两折、两角、两测试块共八项标准化均方根误差的均值。
本文件只实现小样比选，不输出全量厚度结论，不使用问题一数值结果。
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
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "求解/问题2/原型结果/路线1.json"
METRIC = "连续留段标准化均方根误差"
HARD_SECONDS = 175.0
SOFT_SECONDS = 165.0
BOUNDS = ((0.5, 40.0), (1.2, 6.0), (-1.0, 1.0))
THICKNESSES = tuple(0.5 + index * 39.5 / 95 for index in range(96))
ANGLES = (10, 15)


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
        until = time.monotonic() + 1.0
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise TimeoutError("实验记录锁等待超过一秒")
                time.sleep(0.01)
        records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(records, list):
            raise ValueError("实验记录必须是数组")
        records.append(entry)
        write_json(path, records)


def initial_result():
    return {
        "问题": 2,
        "路线编号": "二甲",
        "路线序号": 1,
        "路线名": "双角色散变投影",
        "运行状态": "正在读取真实附件",
        "核心指标": {},
        "主指标名称": METRIC,
        "主指标值": None,
        "指标含义": "八个测试块的标准化均方根误差等权平均，无量纲，越小越好；不是厚度真值误差。",
        "用时估计": {
            "性质": "预设上限与分配，非已测速度承诺",
            "读取与切分秒": 15,
            "两折搜索秒": 100,
            "区间与剖面诊断秒": 35,
            "汇总写出秒": 15,
            "内部软截止秒": SOFT_SECONDS,
            "父进程墙钟上限秒": HARD_SECONDS,
        },
        "口径说明": {
            "输入": "仅附件1、2的Sheet1；反射率百分数除以100；不修改原始附件。",
            "样本": "1200至3800 cm^-1共同窗口，按floor(序号*(窗口点数-1)/479)保留480个原坐标。",
            "切分": "12个连续块，每块40点；两角共用折分；训练与非训练交界舍去20 cm^-1训练保护带。",
            "尺度": "本折本角度全部保留训练点去二次基线的残差四分位距，下限0.0001；不是噪声标准差。",
            "分位数约定": "训练尺度用线性插值分位数；预测半宽用ceil(0.9*校准点数)阶绝对残差。",
            "训练目标": "两个角度训练均方误差除以各自固定尺度的平方后等权平均。",
            "相位限制": "每角仅一个常相位和五个线性系数；没有独立正弦包络、自由相位漂移或高次谐波。",
            "区间": "条件反射率预测经验区间，端点不裁剪；谱内相关下不宣称分布无关保证。",
            "厚度范围": "训练剖面近等损失集合与离散光学情景，不是厚度统计置信区间。",
            "异常": "主窗口与399.6747首点、801.278至927.1104 cm^-1超百段无交集；不声称清洗带来改善。",
            "原型边界": "只计算本路线一个排名指标；覆盖、边界、留角度预测和剖面只作诊断，不合成第二总分。",
            "光学条件": "透明各向同性和空气折射率1为工作近似；缺实测折射率及厚度真值，不能视为材料标定。",
            "检查点": "搜索中保存的预测不参与参数选择；只有共同八项全部有限才形成主指标。",
        },
        "输入哈希": [],
        "样本索引": [],
        "折分": [],
        "使用附件": ["附件1.xlsx", "附件2.xlsx"],
        "参数情景": [],
        "分块预测与残差": [],
        "厚度及单位": [],
        "经验覆盖率": {},
        "辅助诊断": {},
        "失败原因": [],
        "随机种子": 20260909,
        "随机性说明": "确定性网格与有界坐标更新，不调用随机数。",
        "实际用时秒": 0.0,
        "耗时秒": 0.0,
    }


def dot(left, right):
    return sum(first * second for first, second in zip(left, right))


def quantile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower])


def upper_solve(matrix, target):
    solution = [0.0] * len(target)
    for row in reversed(range(len(target))):
        remainder = sum(matrix[row][column] * solution[column]
                        for column in range(row + 1, len(target)))
        solution[row] = (target[row] - remainder) / matrix[row][row]
    return solution


def baseline_basis(coordinates, observed):
    columns = [[1.0] * len(coordinates), coordinates,
               [value * value for value in coordinates]]
    basis = []
    triangular = [[0.0] * 3 for unused in range(3)]
    for column_index, column in enumerate(columns):
        residual = list(column)
        for unused in range(2):
            for basis_index, unit in enumerate(basis):
                projection = dot(unit, residual)
                triangular[basis_index][column_index] += projection
                residual = [value - projection * direction
                            for value, direction in zip(residual, unit)]
        length = math.sqrt(dot(residual, residual))
        if length < 1e-12:
            raise ValueError("训练坐标不足以识别二次基线")
        triangular[column_index][column_index] = length
        basis.append([value / length for value in residual])
    projected = [dot(unit, observed) for unit in basis]
    residual = [value - sum(projected[index] * basis[index][row] for index in range(3))
                for row, value in enumerate(observed)]
    scale = max(quantile(residual, 0.75) - quantile(residual, 0.25), 0.0001)
    return basis, triangular, projected, residual, scale


def read_data(result):
    scout = json.loads((ROOT / "交接/路线侦察.json").read_text(encoding="utf-8"))
    archive = json.loads((ROOT / "交接/数据档案.json").read_text(encoding="utf-8"))
    protocol = scout["共同原型协议"]
    sample_spec = protocol["实测小样"]
    if sample_spec["每角度点数"] != 480 or sample_spec["基本窗口波数"] != [1200, 3800]:
        raise ValueError("共同小样协议已变化，须三路线同步修改，不能单独改密度")
    metadata = {entry["文件名"]: entry for entry in archive["文件档案"]}
    reader = runpy.run_path(str(ROOT / "运行时/数据体检.py"),
                           run_name="prototype_readonly_loader")["read_workbook"]
    datasets = []
    for filename, angle in zip(result["使用附件"], ANGLES):
        manifest, sheets = reader(ROOT / "数据" / filename)
        if manifest["文件哈希"] != metadata[filename]["文件哈希"]:
            raise ValueError(f"{filename}哈希与数据档案不一致")
        if metadata[filename]["材料"] != "碳化硅" or metadata[filename]["入射角度"] != angle:
            raise ValueError("附件材料或角度与本路线不符")
        cells = next(sheet for sheet in sheets if sheet["名称"] == "Sheet1")["单元格"]
        records = []
        for row in range(2, 7471):
            wavenumber = cells[f"A{row}"]["值"]
            reflectance = cells[f"B{row}"]["值"]
            if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                       and math.isfinite(value) for value in (wavenumber, reflectance)):
                raise ValueError(f"{filename}第{row}行不为有限数值")
            records.append((wavenumber, reflectance / 100.0, row))
        records.sort()
        if any(left[0] >= right[0] for left, right in zip(records, records[1:])):
            raise ValueError("波数重复或不严格递增")
        datasets.append(records)
        result["输入哈希"].append({"源附件": filename, "算法": "SHA-256",
                                   "哈希": manifest["文件哈希"], "原始点数": len(records)})
    if [record[0] for record in datasets[0]] != [record[0] for record in datasets[1]]:
        raise ValueError("双角原始波数不完全对齐，不能各自抽样")
    window = [index for index, record in enumerate(datasets[0]) if 1200 <= record[0] <= 3800]
    if len(window) < 480:
        raise ValueError("共同窗口不足480点")
    selected = [window[index * (len(window) - 1) // 479] for index in range(480)]
    wavenumbers = [datasets[0][index][0] for index in selected]
    values = [[records[index][1] for index in selected] for records in datasets]
    result["样本索引"] = [
        {"样本序号": index + 1, "块号": index // 40 + 1,
         "原始行号_附件一": datasets[0][source][2],
         "原始行号_附件二": datasets[1][source][2],
         "波数_cm^-1": datasets[0][source][0], "材料": "碳化硅",
         "质量标记": "共同主窗口，未截断或插值"}
        for index, source in enumerate(selected)
    ]
    result["窗口原始点数_每角度"] = len(window)
    result["原始行号说明"] = "Excel行号，从1计数，包含首行表头。"
    result["参数情景"] = protocol["共同光学情景"]["初始情景"]
    result["折分"] = [dict(spec) for spec in sample_spec["折分"]]
    return wavenumbers, values, [datasets[0][index][0] for index in window]


def make_fold(wavenumbers, values, full_coordinates, spec):
    training_blocks = set(spec["训练块"])
    calibration_blocks = set(spec["校准块"])
    test_blocks = set(spec["测试块"])
    if (training_blocks & calibration_blocks or training_blocks & test_blocks
            or calibration_blocks & test_blocks
            or training_blocks | calibration_blocks | test_blocks != set(range(1, 13))):
        raise ValueError("连续块切分必须互斥并覆盖12块")
    edges = [(wavenumbers[boundary - 1] + wavenumbers[boundary]) / 2
             for boundary in range(40, 480, 40)]
    guard_edges = [edge for block, edge in enumerate(edges, 1)
                   if (block in training_blocks) != (block + 1 in training_blocks)]
    def train_allowed(coordinate, block):
        return block in training_blocks and all(abs(coordinate - edge) >= 20 for edge in guard_edges)
    train_indices = [index for index, coordinate in enumerate(wavenumbers)
                     if train_allowed(coordinate, index // 40 + 1)]
    train_coordinates = [wavenumbers[index] for index in train_indices]
    center = (min(train_coordinates) + max(train_coordinates)) / 2
    half_span = (max(train_coordinates) - min(train_coordinates)) / 2
    scaled = [(coordinate - center) / half_span for coordinate in train_coordinates]
    components = {}
    component = 0
    for block in range(1, 13):
        if block in training_blocks:
            if block - 1 not in training_blocks:
                component += 1
            components[block] = component
    full_training = [(coordinate, components[bisect.bisect_right(edges, coordinate) + 1])
                     for coordinate in full_coordinates
                     if train_allowed(coordinate, bisect.bisect_right(edges, coordinate) + 1)]
    sampled_training = [(wavenumbers[index], components[index // 40 + 1]) for index in train_indices]
    angle_data = []
    for angle_index, angle in enumerate(ANGLES):
        observed = [values[angle_index][index] for index in train_indices]
        basis, triangular, projected, residual, scale = baseline_basis(scaled, observed)
        angle_data.append({"angle": angle, "sigma": train_coordinates, "x": scaled,
                           "y": observed, "basis": basis, "triangular": triangular,
                           "projected": projected, "residual": residual,
                           "residual_ss": dot(residual, residual), "scale": scale})
    return {"spec": spec, "train_indices": train_indices, "center": center,
            "half_span": half_span, "angles": angle_data, "full_train": full_training,
            "sample_train": sampled_training, "wavenumbers": wavenumbers,
            "values": values, "edges": edges, "guard_edges": guard_edges}


def valid_parameters(parameters):
    if not all(lower <= value <= upper for value, (lower, upper) in zip(parameters, BOUNDS)):
        return False
    reference, dispersion = parameters[1:]
    return min(reference + dispersion * ((2000 / coordinate) ** 2 - 1)
               for coordinate in (1200, 3800)) > math.sin(math.radians(15))


def phase_rates(coordinates, angle, reference, dispersion):
    sine_squared = math.sin(math.radians(angle)) ** 2
    return [4 * math.pi / 10000 * coordinate
            * math.sqrt((reference + dispersion * ((2000 / coordinate) ** 2 - 1)) ** 2
                        - sine_squared) for coordinate in coordinates]


def fit_angle(parameters, data, fixed_phase=None, rates=None):
    if rates is None:
        rates = phase_rates(data["sigma"], data["angle"], parameters[1], parameters[2])
    cosine = [math.cos(parameters[0] * rate) for rate in rates]
    sine = [math.sin(parameters[0] * rate) for rate in rates]
    columns = [cosine, sine, [value * coordinate for value, coordinate in zip(cosine, data["x"])],
               [value * coordinate for value, coordinate in zip(sine, data["x"])]]
    projections = [[dot(unit, column) for unit in data["basis"]] for column in columns]
    gram = [[0.0] * 4 for unused in range(4)]
    for row in range(4):
        for column in range(row, 4):
            value = dot(columns[row], columns[column]) - dot(projections[row], projections[column])
            gram[row][column] = value
            gram[column][row] = value
    target = [dot(column, data["residual"]) for column in columns]

    def solve_phase(phase):
        phase %= math.pi
        phase_cosine, phase_sine = math.cos(phase), math.sin(phase)
        cosine_squared, sine_squared = phase_cosine ** 2, phase_sine ** 2
        product = phase_cosine * phase_sine
        diagonal_first = cosine_squared * gram[0][0] - 2 * product * gram[0][1] + sine_squared * gram[1][1]
        diagonal_second = cosine_squared * gram[2][2] - 2 * product * gram[2][3] + sine_squared * gram[3][3]
        off_diagonal = cosine_squared * gram[0][2] - product * (gram[0][3] + gram[1][2]) + sine_squared * gram[1][3]
        target_first = phase_cosine * target[0] - phase_sine * target[1]
        target_second = phase_cosine * target[2] - phase_sine * target[3]
        determinant = diagonal_first * diagonal_second - off_diagonal ** 2
        rank = 2
        if diagonal_first > 1e-14 and diagonal_second > 1e-14 and determinant > 1e-12 * diagonal_first * diagonal_second:
            amplitude_first = (target_first * diagonal_second - target_second * off_diagonal) / determinant
            amplitude_second = (target_second * diagonal_first - target_first * off_diagonal) / determinant
        else:
            rank = 1
            gain_first = target_first ** 2 / diagonal_first if diagonal_first > 1e-14 else 0.0
            gain_second = target_second ** 2 / diagonal_second if diagonal_second > 1e-14 else 0.0
            if gain_first >= gain_second and gain_first > 0:
                amplitude_first, amplitude_second = target_first / diagonal_first, 0.0
            elif gain_second > 0:
                amplitude_first, amplitude_second = 0.0, target_second / diagonal_second
            else:
                amplitude_first, amplitude_second, rank = 0.0, 0.0, 0
        loss = max(0.0, data["residual_ss"] - amplitude_first * target_first - amplitude_second * target_second)
        return loss, phase, amplitude_first, amplitude_second, rank

    if fixed_phase is None:
        best = min((solve_phase(index * math.pi / 12) for index in range(12)), key=lambda item: item[0])
        step = math.pi / 12
        for unused in range(8):
            candidates = [best, solve_phase(best[1] - step), solve_phase(best[1] + step)]
            best = min(candidates, key=lambda item: item[0])
            step /= 2
    else:
        best = solve_phase(fixed_phase)
    unused_loss, phase, amplitude_first, amplitude_second, rank = best
    phase_cosine, phase_sine = math.cos(phase), math.sin(phase)
    adjusted = [data["projected"][index]
                - amplitude_first * (phase_cosine * projections[0][index] - phase_sine * projections[1][index])
                - amplitude_second * (phase_cosine * projections[2][index] - phase_sine * projections[3][index])
                for index in range(3)]
    baseline = upper_solve(data["triangular"], adjusted)
    fitted = [baseline[0] + baseline[1] * coordinate + baseline[2] * coordinate ** 2
              + (amplitude_first + amplitude_second * coordinate)
              * (phase_cosine * cosine[index] - phase_sine * sine[index])
              for index, coordinate in enumerate(data["x"])]
    loss = sum((observed - predicted) ** 2 for observed, predicted in zip(data["y"], fitted))
    normalized = loss / len(fitted) / data["scale"] ** 2
    if not math.isfinite(normalized):
        raise ArithmeticError("候选训练损失不是有限数")
    return {"loss": normalized, "phase": phase,
            "coefficients": baseline + [amplitude_first, amplitude_second], "rank": rank}


def evaluate(parameters, angle_data, cached_rates=None):
    if not valid_parameters(parameters):
        return None
    models = [fit_angle(parameters, data, rates=None if cached_rates is None else cached_rates[index])
              for index, data in enumerate(angle_data)]
    return {"parameters": tuple(parameters), "models": models,
            "loss": sum(model["loss"] for model in models) / len(models)}


def retain(pool, candidate):
    if candidate is None:
        return
    if any(existing["parameters"] == candidate["parameters"] for existing in pool):
        return
    pool.append(candidate)
    pool.sort(key=lambda item: item["loss"])
    del pool[3:]


def refine(candidate, angle_data, deadline, rounds=20):
    best = candidate
    steps = [39.5 / 95, 0.2, 0.1]
    evaluations = 0
    for unused in range(rounds):
        improved = False
        for coordinate in range(3):
            current = best
            for direction in (-1, 1):
                if time.monotonic() >= deadline:
                    return best, evaluations, True
                proposal = list(current["parameters"])
                lower, upper = BOUNDS[coordinate]
                proposal[coordinate] = max(lower, min(upper, proposal[coordinate] + direction * steps[coordinate]))
                if tuple(proposal) == current["parameters"]:
                    continue
                trial = evaluate(proposal, angle_data)
                evaluations += 1
                if trial is not None and trial["loss"] < best["loss"]:
                    best, improved = trial, True
        if not improved:
            steps = [step / 2 for step in steps]
        if max(steps) < 1e-5:
            break
    return best, evaluations, False


def model_record(candidate, fold_number):
    thickness, reference, dispersion = candidate["parameters"]
    return {"折号": fold_number, "厚度_um": thickness, "参考折射率": reference,
            "色散系数": dispersion, "训练标准化均方损失": candidate["loss"],
            "参数边界命中": [name for name, value, (lower, upper) in zip(
                ("厚度", "参考折射率", "色散系数"), candidate["parameters"], BOUNDS)
                if abs(value - lower) < 1e-6 or abs(value - upper) < 1e-6],
            "分角度参数": [{"角度_度": ANGLES[angle_index], "常相位_rad": model["phase"],
                               "五个线性系数": model["coefficients"], "幅值有效秩": model["rank"]}
                              for angle_index, model in enumerate(candidate["models"])]}


def search_fold(fold, scenarios, seed, deadline, on_scenario):
    pool = [seed]
    single_pools = [[{"parameters": seed["parameters"], "models": [model], "loss": model["loss"]}]
                    for model in seed["models"]]
    scenario_results = []
    coarse_count = 0
    refinement_count = 0
    stopped = False
    coarse_deadline = min(deadline, time.monotonic() + max(0.0, deadline - time.monotonic()) * 0.7)
    for scenario in scenarios:
        reference, dispersion = scenario["参考折射率"], scenario["色散系数"]
        rates = [phase_rates(data["sigma"], data["angle"], reference, dispersion) for data in fold["angles"]]
        scenario_best = None
        for thickness in THICKNESSES:
            if time.monotonic() >= coarse_deadline:
                stopped = True
                break
            candidate = evaluate((thickness, reference, dispersion), fold["angles"], rates)
            coarse_count += 1
            if candidate is None:
                continue
            retain(pool, candidate)
            if scenario_best is None or candidate["loss"] < scenario_best["loss"]:
                scenario_best = candidate
            for angle_index, model in enumerate(candidate["models"]):
                retain(single_pools[angle_index], {"parameters": candidate["parameters"],
                                                  "models": [model], "loss": model["loss"]})
        if scenario_best is not None:
            scenario_results.append(model_record(scenario_best, fold["spec"]["折号"]))
        on_scenario(pool[0], coarse_count)
        if stopped:
            break
    starting_points = list(pool)
    for position, candidate in enumerate(starting_points):
        local_deadline = min(deadline, time.monotonic()
                             + max(0.0, deadline - time.monotonic()) / (len(starting_points) - position))
        refined, count, clipped = refine(candidate, fold["angles"], local_deadline)
        refinement_count += count
        stopped = stopped or clipped
        retain(pool, refined)
    return pool[0], single_pools, {"折号": fold["spec"]["折号"], "粗网格实际次数": coarse_count,
                                    "粗网格计划次数": 864, "精修候选数": len(starting_points),
                                    "精修实际次数": refinement_count, "搜索预算截断": stopped,
                                    "离散光学情景最优": scenario_results}


def predict(parameters, model, fold, angle_index, indices):
    rates = phase_rates([fold["wavenumbers"][index] for index in indices], ANGLES[angle_index],
                        parameters[1], parameters[2])
    baseline_zero, baseline_first, baseline_second, amplitude_zero, amplitude_first = model["coefficients"]
    predictions = []
    for index, rate in zip(indices, rates):
        coordinate = (fold["wavenumbers"][index] - fold["center"]) / fold["half_span"]
        value = baseline_zero + baseline_first * coordinate + baseline_second * coordinate ** 2
        value += (amplitude_zero + amplitude_first * coordinate) * math.cos(parameters[0] * rate + model["phase"])
        if not math.isfinite(value):
            raise ArithmeticError("留段预测不为有限数")
        predictions.append(value)
    return predictions


def score_fold(fold, candidate):
    blocks = []
    calibration_indices = [index for index in range(480) if index // 40 + 1 in fold["spec"]["校准块"]]
    training_coordinates = fold["angles"][0]["sigma"]
    for angle_index, model in enumerate(candidate["models"]):
        calibration_predictions = predict(candidate["parameters"], model, fold, angle_index, calibration_indices)
        absolute_errors = sorted(abs(fold["values"][angle_index][index] - prediction)
                                 for index, prediction in zip(calibration_indices, calibration_predictions))
        half_width = absolute_errors[math.ceil(0.9 * len(absolute_errors)) - 1]
        for block in fold["spec"]["测试块"]:
            indices = list(range((block - 1) * 40, block * 40))
            predictions = predict(candidate["parameters"], model, fold, angle_index, indices)
            observed = [fold["values"][angle_index][index] for index in indices]
            residual = [value - prediction for value, prediction in zip(observed, predictions)]
            rmse = math.sqrt(sum(value * value for value in residual) / len(residual))
            residual_mean = sum(residual) / len(residual)
            centered_residual = [value - residual_mean for value in residual]
            residual_energy = dot(centered_residual, centered_residual)
            lag_one = dot(centered_residual[:-1], centered_residual[1:]) / residual_energy if residual_energy > 0 else None
            scale = fold["angles"][angle_index]["scale"]
            distances = [min(abs(fold["wavenumbers"][index] - train) for train in training_coordinates)
                         for index in indices]
            blocks.append({"折号": fold["spec"]["折号"], "材料": "碳化硅", "角度_度": ANGLES[angle_index],
                           "测试块": block, "点数": len(indices), "样本序号": [index + 1 for index in indices],
                           "波数_cm^-1": [fold["wavenumbers"][index] for index in indices],
                           "观测反射率_比例": observed, "预测反射率_比例": predictions,
                           "残差_观测减预测_比例": residual, "均方根误差_比例": rmse,
                           "残差一阶自相关": lag_one,
                           "残差诊断口径": "只描述块内相邻残差相关，不解释为显著性检验或另一排名分数。",
                           "训练归一化尺度_比例": scale, "标准化均方根误差": rmse / scale,
                           "校准点数": len(calibration_indices), "校准样本序号": [index + 1 for index in calibration_indices],
                           "校准绝对残差升序_比例": absolute_errors,
                           "预测区间半宽_比例": half_width, "预测区间平均宽度_比例": 2 * half_width,
                           "预测下界_比例": [value - half_width for value in predictions],
                           "预测上界_比例": [value + half_width for value in predictions],
                           "覆盖点数": sum(abs(value) <= half_width for value in residual),
                           "经验覆盖率": sum(abs(value) <= half_width for value in residual) / len(residual),
                           "距最近训练点均值_cm^-1": sum(distances) / len(distances),
                           "距最近训练点最大值_cm^-1": max(distances)})
    return blocks


def refresh_score(result, folds, candidates):
    blocks = [block for fold, candidate in zip(folds, candidates) for block in score_fold(fold, candidate)]
    if len(blocks) != 8 or any(block["点数"] != 40 for block in blocks):
        raise ValueError("主指标必须覆盖全部八个测试块")
    score = sum(block["标准化均方根误差"] for block in blocks) / 8
    if not math.isfinite(score):
        raise ArithmeticError("主指标不为有限数")
    result["核心指标"] = {METRIC: score}
    result["主指标值"] = score
    result["分块预测与残差"] = blocks
    result["厚度及单位"] = [model_record(candidate, fold["spec"]["折号"])
                             for fold, candidate in zip(folds, candidates)]
    coverage = sum(block["覆盖点数"] for block in blocks) / 320
    result["经验覆盖率"] = {"构造方法": "校准绝对残差向上取整90%经验分位，对称预测区间",
                            "名义覆盖率": 0.9, "测试经验覆盖率": coverage, "覆盖点数": round(coverage * 320),
                            "测试点数": 320, "点数加权平均宽度_比例": sum(block["预测区间平均宽度_比例"] for block in blocks) / 8,
                            "覆盖不足": coverage < 0.9,
                            "宽度与留段距离": "各测试块列出平均宽度及距最近训练点距离；本经验半宽不随外推距离自动扩大。",
                            "限制": "测试位置均为真实数据；折间波段邻近且仅两个校准块，不宣称独立样本或分布无关保证。"}


def alias_check(fold, parameter_sets):
    admissible = [parameters for parameters in parameter_sets if valid_parameters(parameters)]
    result = {"折号": fold["spec"]["折号"], "原坐标最大相邻相位差_rad": 0.0,
              "小样最大相邻相位差_rad": 0.0, "检查情景数": len(admissible),
              "排除非传播情景数": len(parameter_sets) - len(admissible),
              "传播条件": "整个窗口内折射率严格大于sin(15度)；色散单调，检查窗口两端即可。"}
    for parameters in admissible:
        for angle in ANGLES:
            for label, coordinates in (("原坐标最大相邻相位差_rad", fold["full_train"]),
                                       ("小样最大相邻相位差_rad", fold["sample_train"])):
                rates = phase_rates([coordinate for coordinate, component in coordinates], angle,
                                    parameters[1], parameters[2])
                maximum = max((parameters[0] * abs(rates[index + 1] - rates[index])
                               for index in range(len(rates) - 1)
                               if coordinates[index][1] == coordinates[index + 1][1]), default=0.0)
                result[label] = max(result[label], maximum)
    result["奈奎斯特相位上限_rad"] = math.pi
    result["小样存在混叠风险"] = result["小样最大相邻相位差_rad"] >= math.pi
    result["口径"] = "只检查完整训练连通段内坐标和有限物理情景，不跨留段缺口、不读取留段反射率；不是整个连续参数盒的证明。"
    result["风险动作"] = "若触发则保留数值并标记可比资格存疑，请三路线共同改960点；本路线不单独改密度。"
    return result


def held_angle_checks(fold, single_pools, deadline):
    checks = []
    for source_index, pool in enumerate(single_pools):
        target_index = 1 - source_index
        best = pool[0]
        for candidate in pool:
            local_deadline = min(deadline, time.monotonic() + 2.0)
            refined, unused_count, unused_clipped = refine(candidate, [fold["angles"][source_index]], local_deadline, rounds=8)
            if refined["loss"] < best["loss"]:
                best = refined
        frozen_phase = best["models"][0]["phase"]
        target_model = fit_angle(best["parameters"], fold["angles"][target_index], fixed_phase=frozen_phase)
        block_errors = []
        for block in fold["spec"]["测试块"]:
            indices = list(range((block - 1) * 40, block * 40))
            predictions = predict(best["parameters"], target_model, fold, target_index, indices)
            squared = [(fold["values"][target_index][index] - value) ** 2 for index, value in zip(indices, predictions)]
            block_errors.append({"测试块": block, "标准化均方根误差": math.sqrt(sum(squared) / 40) / fold["angles"][target_index]["scale"]})
        checks.append({"折号": fold["spec"]["折号"], "来源角度_度": ANGLES[source_index],
                       "被检角度_度": ANGLES[target_index], "冻结厚度_um": best["parameters"][0],
                       "冻结参考折射率": best["parameters"][1], "冻结色散系数": best["parameters"][2],
                       "冻结常相位_rad": frozen_phase, "被检角度重新拟合厚度": False,
                       "被检角度自由量": "只有二次基线及一次幅值五个线性系数；来源常相位也冻结，不增估被检角度相位。",
                       "候选选择": "仅按来源角度训练损失排序；复用粗网格中的单角独立损失，不用双角优选结果决定来源物理参数。",
                       "限制": "额外冻结常相位是保守迁移诊断，失败也可能来自两角反射相位不同。",
                       "分块诊断": block_errors})
    return checks


def profile_check(fold, candidate, deadline):
    reference_grid = sorted(set([min(6.0, 1.2 + index * 0.4) for index in range(13)] + [candidate["parameters"][1]]))
    thickness_grid = sorted(set([0.5 + index * 39.5 / 16 for index in range(17)]
                               + [candidate["parameters"][0]]
                               + [max(0.5, min(40.0, candidate["parameters"][0] * candidate["parameters"][1] / reference))
                                  for reference in reference_grid]))
    entries = [{"厚度_um": candidate["parameters"][0], "参考折射率": candidate["parameters"][1],
                "训练标准化均方损失": candidate["loss"]}]
    stopped = False
    excluded_references = []
    for reference in reference_grid:
        if not valid_parameters((candidate["parameters"][0], reference, candidate["parameters"][2])):
            excluded_references.append(reference)
            continue
        rates = [phase_rates(data["sigma"], data["angle"], reference, candidate["parameters"][2]) for data in fold["angles"]]
        for thickness in thickness_grid:
            if time.monotonic() >= deadline:
                stopped = True
                break
            parameters = (thickness, reference, candidate["parameters"][2])
            if parameters == candidate["parameters"]:
                continue
            trial = evaluate(parameters, fold["angles"], rates)
            if trial is not None:
                entries.append({"厚度_um": thickness, "参考折射率": reference,
                                "训练标准化均方损失": trial["loss"]})
        if stopped:
            break
    minimum = min(entry["训练标准化均方损失"] for entry in entries)
    sets = []
    for tolerance in (0.05, 0.1, 0.2):
        near = [entry for entry in entries if entry["训练标准化均方损失"] <= minimum * (1 + tolerance) + 1e-12]
        sets.append({"相对损失容差": tolerance, "组合数": len(near),
                     "厚度下界_um": min(entry["厚度_um"] for entry in near),
                     "厚度上界_um": max(entry["厚度_um"] for entry in near), "近等损失组合": near})
    return {"折号": fold["spec"]["折号"], "固定色散系数": candidate["parameters"][2],
            "构造": "固定训练所得色散，交叉扫描厚度与参考折射率；每格重估训练常相位和五个线性系数，不访问校准或测试值。",
            "厚度轴_um": thickness_grid, "参考折射率轴": reference_grid,
            "实际组合数": len(entries), "计划网格组合数": len(thickness_grid) * len(reference_grid),
            "排除非传播参考折射率": excluded_references,
            "训练剖面最低损失": minimum, "搜索预算截断": stopped, "剖面数值": entries,
            "近等损失容差敏感性": sets,
            "是否统计置信区间": False,
            "诊断不反馈主模型": True,
            "解释限制": "只覆盖所列有限网格且固定色散；长平谷、多个厚度或剖面优于主搜索均需披露，不能据此声称厚度已唯一识别。"}


def worker(t0):
    result = initial_result()
    checkpoint(result, t0)
    try:
        wavenumbers, values, full_coordinates = read_data(result)
        folds = [make_fold(wavenumbers, values, full_coordinates, spec) for spec in result["折分"]]
        for fold, spec in zip(folds, result["折分"]):
            spec["实际训练样本序号"] = [index + 1 for index in fold["train_indices"]]
            spec["保护后训练点数_每角度"] = len(fold["train_indices"])
            spec["剔除训练点数_每角度"] = 320 - len(fold["train_indices"])
            spec["训练坐标中点_cm^-1"] = fold["center"]
            spec["训练坐标半跨度_cm^-1"] = fold["half_span"]
            spec["保护边界_cm^-1"] = fold["guard_edges"]
            spec["训练归一化尺度_按十度十五度"] = [data["scale"] for data in fold["angles"]]
        scenarios = result["参数情景"]
        alias_parameters = [(40.0, scenario["参考折射率"], scenario["色散系数"]) for scenario in scenarios]
        alias_parameters += [(40.0, reference, dispersion) for reference in (1.2, 6.0) for dispersion in (-1.0, 1.0)]
        result["辅助诊断"]["搜索前抽稀混叠检查"] = [alias_check(fold, alias_parameters) for fold in folds]
        checkpoint(result, t0)
        seed_parameters = (THICKNESSES[18], 3.0, 0.0)
        candidates = [evaluate(seed_parameters, fold["angles"]) for fold in folds]
        refresh_score(result, folds, candidates)
        result["运行状态"] = "两折已有完整真实预测，正在有限搜索"
        checkpoint(result, t0)
        searches, single_pools = [], []
        for fold_index, fold in enumerate(folds):
            deadline = min(t0 + 65.0 + 50.0 * fold_index, t0 + SOFT_SECONDS - 30.0)
            def on_scenario(best, count):
                result["当前搜索"] = {"折号": fold["spec"]["折号"], "粗网格实际次数": count,
                                      "当前训练最优": model_record(best, fold["spec"]["折号"])}
                checkpoint(result, t0)
            candidate, angle_pools, search_info = search_fold(fold, scenarios, candidates[fold_index], deadline, on_scenario)
            candidates[fold_index] = candidate
            single_pools.append(angle_pools)
            searches.append(search_info)
            result["辅助诊断"]["两折训练搜索"] = searches
            refresh_score(result, folds, candidates)
            checkpoint(result, t0)
        result["运行状态"] = "主指标已完整计算，正在限时辅助诊断"
        result["辅助诊断"]["拟合后抽稀混叠检查"] = [alias_check(fold, [candidate["parameters"]]) for fold, candidate in zip(folds, candidates)]
        checkpoint(result, t0)
        held_checks = []
        for fold, pools in zip(folds, single_pools):
            held_checks.extend(held_angle_checks(fold, pools, min(t0 + 137.0, t0 + SOFT_SECONDS - 15.0)))
            result["辅助诊断"]["双向留角度预测"] = held_checks
            checkpoint(result, t0)
        profiles = []
        for fold_index, (fold, candidate) in enumerate(zip(folds, candidates)):
            remaining = max(0.0, t0 + 150.0 - time.monotonic())
            deadline = min(t0 + 150.0, time.monotonic() + remaining / (2 - fold_index))
            profiles.append(profile_check(fold, candidate, deadline))
            result["辅助诊断"]["厚度折射率剖面"] = profiles
            checkpoint(result, t0)
        risks = []
        if any(entry["参数边界命中"] for entry in result["厚度及单位"]):
            risks.append("训练最优命中搜索盒边界；保留厚度及评分，不能作精确材料标定。")
        if result["经验覆盖率"]["覆盖不足"]:
            risks.append("经验覆盖低于名义90%；未用测试残差重定半宽。")
        if any(entry["搜索预算截断"] for entry in searches + profiles):
            risks.append("有限搜索或剖面按预算提前停止；两折八块评分完整，未拼接多次运行。")
        alias_risk = any(entry["小样存在混叠风险"] for entry in result["辅助诊断"]["搜索前抽稀混叠检查"]
                         + result["辅助诊断"]["拟合后抽稀混叠检查"])
        if alias_risk:
            risks.append("抽稀混叠资格检查有风险；三路线应共同调整密度后重比，本次不自行加点。")
        result["置信限制"] = risks + ["缺实测折射率、重复测量及真实厚度，本指标仅衡量条件留段谱形预测。"]
        result["运行状态"] = "已完成"
        result["用于同口径排名"] = not alias_risk
        result["完成测试块数"] = 8
        checkpoint(result, t0)
        try:
            append_experiment({"类别": "科学尝试", "问题": 2,
                               "尝试": "用双角共享厚度与经验色散拟合两束基频，各角分别估计二次基线和一次幅值。",
                               "现象": f"两个角度各480点，两折共八个测试块；标准化均方根误差为{result['主指标值']:.8g}，预测经验覆盖率为{result['经验覆盖率']['测试经验覆盖率']:.6g}。",
                               "决定": "保留共同口径误差与全部条件厚度，以光学剖面和经验覆盖限制解释，不把谱形误差等同厚度误差。",
                               "依据": "求解结果:主指标值；求解结果:经验覆盖率；求解结果:辅助诊断/厚度折射率剖面"})
        except Exception as record_error:
            result["失败原因"].append(f"实验记录追加失败，已完成指标仍保留: {record_error}")
        checkpoint(result, t0)
        return 0
    except Exception as error:
        result["运行状态"] = "未完成"
        result["核心指标"] = {}
        result["主指标值"] = None
        result["失败原因"].append(f"{type(error).__name__}: {error}")
        checkpoint(result, t0)
        try:
            append_experiment({"类别": "流程事件", "问题": 2,
                               "尝试": "运行问题2路线1限时小样原型。", "现象": result["失败原因"][-1],
                               "决定": "保存已完成分块与失败状态，不用部分分数排名。", "依据": "本次原型进程实际异常。"})
        except Exception as record_error:
            result["失败原因"].append(f"实验记录追加失败: {record_error}")
            checkpoint(result, t0)
        return 1


def main():
    t0 = PROCESS_START
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        return worker(float(sys.argv[2]))
    if len(sys.argv) != 1:
        raise SystemExit("用法：python3 求解/问题2/原型_路线1.py")
    result = initial_result()
    checkpoint(result, t0)
    failure = None
    try:
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(t0)],
                                   cwd=ROOT, timeout=max(0.1, HARD_SECONDS - (time.monotonic() - t0)), check=False)
        if completed.returncode:
            failure = f"原型子进程退出码{completed.returncode}"
    except subprocess.TimeoutExpired:
        failure = "到达175秒父进程墙钟上限，已停止子进程；不得用部分结果排名。"
    result = json.loads(OUTPUT.read_text(encoding="utf-8"))
    if failure:
        result["运行状态"] = "未完成"
        result["主指标值"] = None
        result["核心指标"] = {}
        result["用于同口径排名"] = False
        result["失败原因"].append(failure)
        try:
            append_experiment({"类别": "流程事件", "问题": 2, "尝试": "执行问题2路线1三分钟原型。",
                               "现象": failure, "决定": "保留已完成块和失败原因，不给部分均值排名。",
                               "依据": "父进程实际退出状态与计时器。"})
        except Exception as error:
            result["失败原因"].append(f"实验记录追加失败: {error}")
    checkpoint(result, t0)
    print(json.dumps({"路线名": result["路线名"], "运行状态": result["运行状态"],
                      "使用附件": result["使用附件"], "每角度点数": len(result["样本索引"]),
                      "核心指标": result["核心指标"], "条件厚度": result["厚度及单位"],
                      "经验覆盖率": result["经验覆盖率"], "实际用时秒": result["实际用时秒"],
                      "失败原因": result["失败原因"]}, ensure_ascii=False, allow_nan=False), flush=True)
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
