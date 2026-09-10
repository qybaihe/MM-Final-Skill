"""问题三路线二：共享相位谐波反演的小样原型，只输出一个排名指标。

输入为四个真实XLSX，R=原反射率百分数/100，厚度d的计算单位为微米。
由透明传播假设，n=n参+c*((2000/σ)^2-1)，qθ=sqrt(n^2-sin(θ)^2)，
一次往返相位Φθ=4π*(d/10000)*σ*qθ。每材料两角共享d、n参、c。
Rθ=Bθ二次+Σ[aθ,h*cos(h*Φθ)+bθ,h*sin(h*Φθ)]，h=1,...,4。
先投影消去基线，再将每个正余弦组白化为Xh'Xh/N=I；最小化
||R-B-Xγ||²/(2N)+λ*Σ(h>=2)||γh||，基频不惩罚。
固定其余组，zh=Xh'(R-B-Σ(k!=h)Xkγk)/N，故γh=(1-λ/||zh||)+*zh。
用训练内连续留段选惩罚；外层校准只定90%经验残差分位半宽。
主分数为两材料、两折、两角、两测试块共16个标准化RMSE等权均值。
光学搜索盒不是材料常数；厚度仅是条件估计；谐波不是物理多光束证明。
直接运行本文件启用175秒父进程限时；本次交付不执行数值计算。
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
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "求解/问题3/原型结果/路线2.json"
METRIC = "连续留段标准化均方根误差"
SOFT_SECONDS = 165.0
HARD_SECONDS = 175.0
BOUNDS = ((0.5, 40.0), (1.2, 6.0), (-1.0, 1.0))
ANGLES = (10, 15)
PENALTIES = (0.02, 0.08, 0.25)
MATERIALS = (("硅", ("附件3.xlsx", "附件4.xlsx")),
             ("碳化硅", ("附件1.xlsx", "附件2.xlsx")))
XML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
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
                    raise TimeoutError("共享实验记录锁未及时释放")
                time.sleep(0.01)
        entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(entries, list):
            raise ValueError("实验记录顶层必须为数组")
        entries.append(entry)
        write_json(path, entries)


def initial_result():
    return {
        "问题": 3, "路线编号": "三乙", "路线序号": 2, "路线名": "共享相位谐波反演",
        "运行状态": "已启动", "核心指标": {}, "主指标名称": METRIC, "主指标值": None,
        "指标含义": "16个测试块的标准化均方根误差等权平均，无量纲，越小越好；不是厚度真值误差。",
        "用时估计": {"性质": "设计上限，未经实测", "总上限秒": 175,
                     "内部软截止秒": 165, "读取与切分秒": 15,
                     "基频与稀疏谐波拟合秒": 105, "递推诊断与区间秒": 30,
                     "汇总写出秒": 15},
        "口径说明": [
            "真实附件各480点；1200至3800 cm^-1；共同原坐标等序号抽样，无插值与裁剪。",
            "两材料分别拟合，两角度共享厚度及经验色散；每角度独立二次基线和至多四个正余弦组。",
            "训练与非训练交界保留20 cm^-1保护带；内部验证也按连续块及共同双角切分。",
            "尺度=max(外层训练反射率去二次基线残差的四分位距,0.0001)，仅由训练确定。",
            "惩罚只在训练内选取；校准、测试值不参与相位、阶次、惩罚、起点或基线选择。",
            "主窗口不含共同零首点或超百分之百区段，不宣称清洗改善，也不伪造异常区对照。",
            "基频始终入模且不收缩；全部高阶只能是同一相位的整数倍，不将最高谱峰改称基频。",
            "诊断失败保留已经算出的数值、标记资格失败；不得用训练拟合优度宣称真实厚度精度。",
            "本原型不生成厚度置信区间；预测区间为相关光谱上的经验区间，无分布无关保证。"],
        "使用附件": [filename for _, filenames in MATERIALS for filename in filenames],
        "输入哈希": [], "样本索引": [], "折分": [], "参数情景": [],
        "分块预测与残差": [], "厚度及单位": [], "经验覆盖率": None,
        "辅助诊断": [], "失败原因": [], "随机种子": 20260909,
        "随机性说明": "确定性网格与坐标下降，不使用随机抽样。",
        "资格状态": "尚未核对", "实际用时秒": 0.0, "耗时秒": 0.0,
    }


def dot(left, right):
    return sum(first * second for first, second in zip(left, right))


def quantile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[min(lower + 1, len(ordered) - 1)] * fraction


def upper_solve(matrix, target):
    solution = [0.0] * len(target)
    for index in range(len(target) - 1, -1, -1):
        solution[index] = (target[index] - sum(matrix[index][other] * solution[other]
                                             for other in range(index + 1, len(target)))) / matrix[index][index]
    return solution


def read_inputs(result):
    archive = json.loads((ROOT / "交接/数据档案.json").read_text(encoding="utf-8"))
    scout = json.loads((ROOT / "交接/路线侦察.json").read_text(encoding="utf-8"))
    protocol = scout["共同原型协议"]
    sample = protocol["实测小样"]
    if sample["每角度点数"] != 480 or sample["基本窗口波数"] != [1200, 3800]:
        raise ValueError("共同小样已改变；须同步所有路线，不能私自改密度或窗口")
    for spec in sample["折分"]:
        groups = [set(spec[name]) for name in ("训练块", "校准块", "测试块")]
        if (set.union(*groups) != set(range(1, 13))
                or sum(len(group) for group in groups) != 12
                or [len(group) for group in groups] != [8, 2, 2]):
            raise ValueError("训练、校准、测试块必须互斥，按8、2、2块覆盖12块")
    metadata = {entry["文件名"]: entry for entry in archive["文件档案"]}
    loaded = {}
    for material, filenames in MATERIALS:
        for filename, angle in zip(filenames, ANGLES):
            entry = metadata[filename]
            path = ROOT / "数据" / filename
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != entry["文件哈希"] or entry["材料"] != material or entry["入射角度"] != angle:
                raise ValueError(f"{filename}哈希、材料或角度与数据档案不符")
            with zipfile.ZipFile(path) as workbook:
                document = ET.fromstring(workbook.read(entry["工作表"][0]["内部路径"]))
            rows = []
            for row in document.iter(XML_NS + "row"):
                row_number = int(row.attrib["r"])
                if row_number == 1:
                    continue
                cells = {}
                for cell in row.findall(XML_NS + "c"):
                    address = cell.attrib["r"]
                    column = address.rstrip("0123456789")
                    if column not in ("A", "B"):
                        continue
                    value = cell.find(XML_NS + "v")
                    if cell.attrib.get("t", "n") != "n" or value is None:
                        raise ValueError(f"{filename}:{address}不是数值单元格")
                    cells[column] = float(value.text)
                if set(cells) != {"A", "B"} or not all(math.isfinite(value) for value in cells.values()):
                    raise ValueError(f"{filename}:{row_number}缺失或非有限值")
                rows.append((cells["A"], cells["B"] / 100.0, row_number))
            rows.sort()
            if len(rows) != 7469 or any(first[0] >= second[0] for first, second in zip(rows, rows[1:])):
                raise ValueError(f"{filename}行数或波数次序与档案不符")
            loaded[filename] = rows
            result["输入哈希"].append({"附件": filename, "算法": "SHA-256", "哈希": digest,
                                       "材料": material, "入射角_度": angle, "原始点数": len(rows)})
    reference = loaded["附件1.xlsx"]
    coordinates = [row[0] for row in reference]
    if any([row[0] for row in rows] != coordinates for rows in loaded.values()):
        raise ValueError("四谱原始波数不完全对齐")
    window = [index for index, coordinate in enumerate(coordinates) if 1200 <= coordinate <= 3800]
    if len(window) < 480:
        raise ValueError("主窗口不足480点")
    selected = [window[index * (len(window) - 1) // 479] for index in range(480)]
    sigma = [coordinates[index] for index in selected]
    result["样本索引"] = [{"样本序号": index + 1, "块号": index // 40 + 1,
                             "波数_cm^-1": coordinates[source],
                             "各附件原始行号": {filename: rows[source][2] for filename, rows in loaded.items()}}
                            for index, source in enumerate(selected)]
    result["折分"] = sample["折分"]
    result["参数情景"] = protocol["共同光学情景"]["初始情景"]
    result["窗口原始点数_每角度"] = len(window)
    result["异常口径"] = {"零首点波数_cm^-1": 399.6747, "超百段波数_cm^-1": [801.278, 927.1104],
                          "主窗口异常交集点数": sum(not 0 < rows[index][1] <= 1
                                                   for rows in loaded.values() for index in window),
                          "处理": "保留原值，未裁剪；异常区不参加主分数。"}
    values = {material: [[loaded[filename][index][1] for index in selected] for filename in filenames]
              for material, filenames in MATERIALS}
    return sigma, values, [coordinates[index] for index in window], protocol


def allowed_indices(sigma, blocks):
    edges = [(sigma[index - 1] + sigma[index]) / 2 for index in range(40, 480, 40)]
    guards = [edge for block, edge in enumerate(edges, 1) if (block in blocks) != (block + 1 in blocks)]
    indices = [index for index, coordinate in enumerate(sigma)
               if index // 40 + 1 in blocks and all(abs(coordinate - edge) >= 20 for edge in guards)]
    return indices, edges, guards


def prepare(sigma, values, indices, degree=2):
    coordinates = [sigma[index] for index in indices]
    center = (min(coordinates) + max(coordinates)) / 2
    half_span = (max(coordinates) - min(coordinates)) / 2
    scaled = [(coordinate - center) / half_span for coordinate in coordinates]
    basis = []
    triangular = [[0.0] * (degree + 1) for _ in range(degree + 1)]
    for power in range(degree + 1):
        column = [coordinate ** power for coordinate in scaled]
        for index, previous in enumerate(basis):
            projection = dot(column, previous)
            triangular[index][power] = projection
            column = [value - projection * old for value, old in zip(column, previous)]
        norm = math.sqrt(dot(column, column))
        if norm < 1e-12:
            raise ValueError("训练基线矩阵退化")
        triangular[power][power] = norm
        basis.append([value / norm for value in column])
    angles = []
    for angle, observations in zip(ANGLES, values):
        observed = [observations[index] for index in indices]
        projections = [dot(column, observed) for column in basis]
        residual = [value - sum(weight * column[index] for weight, column in zip(projections, basis))
                    for index, value in enumerate(observed)]
        scale = max(quantile(residual, 0.75) - quantile(residual, 0.25), 0.0001)
        angles.append({"angle": angle, "y": observed, "residual": residual,
                       "projected": projections, "scale": scale})
    return {"sigma": coordinates, "indices": indices, "center": center, "half_span": half_span,
            "basis": basis, "triangular": triangular, "angles": angles, "degree": degree}


def phase(parameters, coordinate, angle):
    thickness, reference, dispersion = parameters
    refractive = reference + dispersion * ((2000.0 / coordinate) ** 2 - 1)
    sine = math.sin(math.radians(angle))
    if refractive <= sine:
        raise ValueError("折射率不满足正传播支")
    return 4 * math.pi * thickness / 10000 * coordinate * math.sqrt(refractive * refractive - sine * sine)


def valid(parameters):
    return (all(lower <= value <= upper for value, (lower, upper) in zip(parameters, BOUNDS))
            and all(parameters[1] + parameters[2] * ((2000 / coordinate) ** 2 - 1)
                    > math.sin(math.radians(15)) for coordinate in (1200, 3800)))


def design_cache(parameters, data, angle_data, order):
    count = len(data["sigma"])
    raw = [[] for _ in range(2 * order)]
    for coordinate in data["sigma"]:
        fundamental = phase(parameters, coordinate, angle_data["angle"])
        cosine, sine = math.cos(fundamental), math.sin(fundamental)
        current_cosine, current_sine = cosine, sine
        for harmonic in range(order):
            raw[2 * harmonic].append(current_cosine)
            raw[2 * harmonic + 1].append(current_sine)
            current_cosine, current_sine = (current_cosine * cosine - current_sine * sine,
                                           current_sine * cosine + current_cosine * sine)
    projections = [[dot(column, basis) for basis in data["basis"]] for column in raw]
    columns = []
    transforms = []
    for harmonic in range(order):
        pair = []
        for offset in (0, 1):
            position = 2 * harmonic + offset
            pair.append([value - sum(weight * basis[index] for weight, basis in
                                     zip(projections[position], data["basis"]))
                         for index, value in enumerate(raw[position])])
        cosine_norm = math.sqrt(dot(pair[0], pair[0]) / count)
        if cosine_norm < 1e-10:
            raise ValueError("正弦余弦组与基线混淆")
        cosine_column = [value / cosine_norm for value in pair[0]]
        cross = dot(pair[1], cosine_column) / count
        sine_residual = [value - cross * basis for value, basis in zip(pair[1], cosine_column)]
        sine_norm = math.sqrt(dot(sine_residual, sine_residual) / count)
        if sine_norm < 1e-10:
            raise ValueError("正弦余弦组退化")
        columns.extend((cosine_column, [value / sine_norm for value in sine_residual]))
        transforms.append((cosine_norm, cross, sine_norm))
    gram = [[dot(first, second) / count for second in columns] for first in columns]
    target = [dot(column, angle_data["residual"]) / count for column in columns]
    return {"gram": gram, "target": target, "columns": columns,
            "projections": projections, "transforms": transforms}


def fit_coefficients(data, angle_data, cache, penalty, deadline):
    gram, target = cache["gram"], cache["target"]
    coefficients = [0.0] * len(target)
    count = len(data["sigma"])
    absolute_penalty = penalty * angle_data["scale"]
    converged = False
    iterations = 0
    for iteration in range(60):
        previous = coefficients[:]
        for start in range(0, len(target), 2):
            updated = [target[index] - sum(gram[index][other] * coefficients[other]
                       for other in range(len(target)) if other not in (start, start + 1))
                       for index in (start, start + 1)]
            norm = math.hypot(*updated)
            shrink = 1.0 if start == 0 else max(0.0, 1 - absolute_penalty / max(norm, 1e-30))
            coefficients[start:start + 2] = [shrink * value for value in updated]
        iterations = iteration + 1
        if max(abs(first - second) for first, second in zip(coefficients, previous)) <= 1e-7 * angle_data["scale"]:
            converged = True
            break
        if time.monotonic() >= deadline:
            break
    residual = [value - sum(coefficient * column[index] for coefficient, column in
                            zip(coefficients, cache["columns"]))
                for index, value in enumerate(angle_data["residual"])]
    squared = dot(residual, residual) / count
    group_norms = [math.hypot(*coefficients[start:start + 2]) for start in range(0, len(target), 2)]
    raw_coefficients = []
    for harmonic, (cosine_norm, cross, sine_norm) in enumerate(cache["transforms"]):
        sine_coefficient = coefficients[2 * harmonic + 1] / sine_norm
        cosine_coefficient = (coefficients[2 * harmonic] - cross * sine_coefficient) / cosine_norm
        raw_coefficients.extend((cosine_coefficient, sine_coefficient))
    baseline_target = [projection - sum(coefficient * weights[index] for coefficient, weights in
                                      zip(raw_coefficients, cache["projections"]))
                       for index, projection in enumerate(angle_data["projected"])]
    return {"baseline": upper_solve(data["triangular"], baseline_target), "harmonics": raw_coefficients,
            "norms": group_norms, "mse": squared,
            "objective": (0.5 * squared + absolute_penalty * sum(group_norms[1:])) / angle_data["scale"] ** 2,
            "converged": converged, "iterations": iterations}


def evaluate(parameters, data, order, penalty, deadline, caches=None):
    if not valid(parameters):
        return None
    try:
        if caches is None:
            caches = [design_cache(parameters, data, angle_data, order) for angle_data in data["angles"]]
        models = [fit_coefficients(data, angle_data, cache, penalty, deadline)
                  for angle_data, cache in zip(data["angles"], caches)]
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    objective = sum(model["objective"] for model in models) / 2
    if not math.isfinite(objective):
        return None
    return {"parameters": tuple(parameters), "models": models, "objective": objective,
            "order": order, "penalty": penalty}


def refine(candidate, data, deadline, rounds=20):
    steps = [0.3, 0.15, 0.08]
    best = candidate
    for _ in range(min(rounds, 20)):
        improved = False
        for dimension in range(3):
            for direction in (-1, 1):
                if time.monotonic() >= deadline:
                    return best
                parameters = list(best["parameters"])
                parameters[dimension] += direction * steps[dimension]
                trial = evaluate(parameters, data, best["order"], best["penalty"], deadline)
                if trial is not None and trial["objective"] < best["objective"]:
                    best, improved = trial, True
        if not improved:
            steps = [step / 2 for step in steps]
        if max(steps) < 0.0001:
            break
    return best


def predict(candidate, data, sigma, angle_index):
    model = candidate["models"][angle_index]
    predictions = []
    for coordinate in sigma:
        scaled = (coordinate - data["center"]) / data["half_span"]
        value = sum(coefficient * scaled ** power for power, coefficient in enumerate(model["baseline"]))
        fundamental = phase(candidate["parameters"], coordinate, ANGLES[angle_index])
        for harmonic in range(candidate["order"]):
            value += (model["harmonics"][2 * harmonic] * math.cos((harmonic + 1) * fundamental)
                      + model["harmonics"][2 * harmonic + 1] * math.sin((harmonic + 1) * fundamental))
        predictions.append(value)
    return predictions


def validation_score(candidate, data, sigma, values, blocks, eligible):
    errors = []
    for angle_index, observations in enumerate(values):
        for block in blocks:
            indices = [index for index in range((block - 1) * 40, block * 40) if index in eligible]
            predictions = predict(candidate, data, [sigma[index] for index in indices], angle_index)
            errors.append(math.sqrt(sum((estimate - observations[index]) ** 2
                                       for index, estimate in zip(indices, predictions)) / len(indices))
                          / data["angles"][angle_index]["scale"])
    return sum(errors) / len(errors)


def solve_fold(sigma, values, spec, scenarios, deadline):
    started = time.monotonic()
    span = max(0.0, deadline - started)
    outer_indices, edges, guards = allowed_indices(sigma, set(spec["训练块"]))
    outer = prepare(sigma, values, outer_indices)
    inner_validation = [spec["训练块"][2], spec["训练块"][5]]
    inner_blocks = set(spec["训练块"]) - set(inner_validation)
    inner_indices, _, _ = allowed_indices(sigma, inner_blocks)
    inner_indices = sorted(set(inner_indices) & set(outer_indices))
    inner = prepare(sigma, values, inner_indices)
    seed = evaluate((8.0, 3.0, 0.0), inner, 1, 0.0, deadline)
    if seed is None:
        raise ValueError("合法初值无法拟合，不能构造真实预测")
    pool = [seed]
    visited = 0
    coarse_deadline = started + 0.42 * span
    for scenario in scenarios[:9]:
        for grid_index in range(96):
            if time.monotonic() >= coarse_deadline:
                break
            parameters = (0.5 + 39.5 * grid_index / 95, scenario["参考折射率"], scenario["色散系数"])
            candidate = evaluate(parameters, inner, 1, 0.0, coarse_deadline)
            visited += 1
            if candidate is not None:
                pool.append(candidate)
                pool.sort(key=lambda item: item["objective"])
                pool = pool[:3]
        if time.monotonic() >= coarse_deadline:
            break
    refined = []
    for candidate in pool:
        refined.append(refine(candidate, inner, started + 0.53 * span, rounds=12))
    refined.sort(key=lambda item: item["objective"])
    best_baseline_inner = refined[0]
    eligible = set(outer_indices)
    choices = [(validation_score(best_baseline_inner, inner, sigma, values, inner_validation, eligible),
                1, 0.0, best_baseline_inner)]
    cached_designs = [design_cache(best_baseline_inner["parameters"], inner, angle_data, 4)
                      for angle_data in inner["angles"]]
    tuning_records = []
    for penalty_index, penalty in enumerate(PENALTIES):
        candidate = evaluate(best_baseline_inner["parameters"], inner, 4, penalty, deadline, cached_designs)
        if candidate is None:
            continue
        tuning_deadline = started + (0.53 + 0.16 * (penalty_index + 1) / len(PENALTIES)) * span
        candidate = refine(candidate, inner, tuning_deadline, rounds=4)
        score = validation_score(candidate, inner, sigma, values, inner_validation, eligible)
        choices.append((score, 4, penalty, candidate))
    choices.sort(key=lambda item: (item[0], item[1], -item[2]))
    for score, order, penalty, _ in choices:
        tuning_records.append({"最高候选阶次": order, "相对惩罚": penalty,
                               "训练内连续留段标准化均方根误差": score})
    _, order, penalty, chosen = choices[0]
    baseline = evaluate(best_baseline_inner["parameters"], outer, 1, 0.0, deadline)
    if baseline is None:
        raise ValueError("外层基频拟合退化")
    baseline = refine(baseline, outer, started + 0.78 * span, rounds=8)
    best = evaluate(chosen["parameters"], outer, order, penalty, deadline)
    if best is None:
        best = baseline
    starts = [best]
    for candidate in refined[:2]:
        fitted = evaluate(candidate["parameters"], outer, order, penalty, deadline)
        if fitted is not None:
            starts.append(fitted)
    for candidate in starts[:3]:
        candidate = refine(candidate, outer, deadline, rounds=12)
        if candidate["objective"] < best["objective"]:
            best = candidate
    if order == 1 and baseline["objective"] < best["objective"]:
        best = baseline
    details = {"粗网格已访问数": visited, "粗网格上限": 864,
               "训练样本数_每角度": len(outer_indices), "训练内拟合点数_每角度": len(inner_indices),
               "训练内验证点数_每角度": sum(index // 40 + 1 in inner_validation for index in outer_indices),
               "训练内验证块": inner_validation, "训练内候选": tuning_records,
               "选定最高候选阶次": best["order"], "选定相对惩罚": best["penalty"],
               "阶段用时秒": time.monotonic() - started,
               "截止收敛": time.monotonic() >= deadline,
               "相位共享参数数": 3, "每角度设计矩阵最多列数": 11}
    return {"data": outer, "best": best, "baseline": baseline, "spec": spec,
            "sigma": sigma, "values": values, "edges": edges, "guards": guards, "details": details}


def score_fold(material, fitted):
    data, candidate = fitted["data"], fitted["best"]
    sigma, values, spec = fitted["sigma"], fitted["values"], fitted["spec"]
    rows = []
    for angle_index, angle in enumerate(ANGLES):
        calibration = [index for index in range(480) if index // 40 + 1 in spec["校准块"]]
        prediction = predict(candidate, data, [sigma[index] for index in calibration], angle_index)
        absolute = sorted(abs(estimate - values[angle_index][index]) for index, estimate in zip(calibration, prediction))
        half_width = absolute[math.ceil(0.9 * len(absolute)) - 1]
        for block in spec["测试块"]:
            indices = list(range((block - 1) * 40, block * 40))
            coordinates = [sigma[index] for index in indices]
            prediction = predict(candidate, data, coordinates, angle_index)
            baseline = predict(fitted["baseline"], data, coordinates, angle_index)
            observed = [values[angle_index][index] for index in indices]
            residual = [estimate - value for estimate, value in zip(prediction, observed)]
            if not all(math.isfinite(value) for value in prediction + baseline + residual):
                raise ValueError("测试预测出现非有限值")
            rmse = math.sqrt(dot(residual, residual) / 40)
            scale = data["angles"][angle_index]["scale"]
            hits = sum(abs(value) <= half_width for value in residual)
            rows.append({"材料": material, "折号": spec["折号"], "入射角_度": angle, "测试块": block,
                         "样本数": 40, "样本序号": [index + 1 for index in indices], "波数_cm^-1": coordinates,
                         "实测反射率_比例": observed, "预测反射率_比例": prediction,
                         "预测减实测_比例": residual, "两束参照预测反射率_比例": baseline,
                         "均方根误差_反射率比例": rmse, "均方根误差_百分点": 100 * rmse,
                         "训练尺度_反射率比例": scale, "标准化均方根误差": rmse / scale,
                         "两束参照同口径标准化均方根误差": math.sqrt(sum((estimate - value) ** 2
                              for estimate, value in zip(baseline, observed)) / 40) / scale,
                         "区间构造": "本折本角度80个专用校准残差绝对值的向上90%经验分位数；非共形保证。",
                         "名义覆盖率": 0.9, "校准样本数": len(absolute),
                         "区间半宽_反射率比例": half_width, "区间平均宽度_反射率比例": 2 * half_width,
                         "预测下限_比例": [estimate - half_width for estimate in prediction],
                         "预测上限_比例": [estimate + half_width for estimate in prediction],
                         "覆盖点数": hits, "经验覆盖率": hits / 40,
                         "覆盖不足": hits / 40 < 0.9,
                         "距最近训练坐标均值_cm^-1": sum(min(abs(coordinate - training) for training in data["sigma"])
                                                                  for coordinate in coordinates) / 40})
    return rows


def coefficient_summary(candidate, data):
    summaries = []
    for angle_data, model in zip(data["angles"], candidate["models"]):
        values = [complex(model["harmonics"][index], -model["harmonics"][index + 1])
                  for index in range(0, len(model["harmonics"]), 2)]
        threshold = 1e-8 * angle_data["scale"]
        active = [index + 1 for index, norm in enumerate(model["norms"]) if norm > threshold]
        ratios = [values[index + 1] / values[index] for index in range(len(values) - 1)
                  if index + 1 in active and index + 2 in active]
        mean_ratio = sum(ratios) / len(ratios) if ratios else None
        spread = (max(abs(value - mean_ratio) for value in ratios) / max(abs(mean_ratio), 1e-12)
                  if len(ratios) >= 2 else None)
        summaries.append({"入射角_度": angle_data["angle"], "有效阶次": active,
                          "基频非零": 1 in active, "基线系数": model["baseline"],
                          "基线阶数": data["degree"],
                          "谐波系数": [{"阶次": index + 1, "余弦系数_反射率比例": value.real,
                                         "正弦系数_反射率比例": -value.imag,
                                         "幅值_反射率比例": abs(value), "相位_弧度": cmath.phase(value),
                                         "白化组范数_反射率比例": model["norms"][index]}
                                        for index, value in enumerate(values)],
                          "相邻阶复比": [{"幅值比": abs(value), "相位增量_弧度": cmath.phase(value)} for value in ratios],
                          "递推相对离散度": spread,
                          "所有可算幅值比小于一": all(abs(value) < 1 for value in ratios) if ratios else None,
                          "收缩迭代收敛": model["converged"], "收缩迭代数": model["iterations"]})
    return summaries


def diagnostics(material, fitted, full_coordinates, deadline):
    data, candidate = fitted["data"], fitted["best"]
    parameters, spec = candidate["parameters"], fitted["spec"]
    summaries = coefficient_summary(candidate, data)
    failures = []
    if not all(item["基频非零"] for item in summaries):
        failures.append("基频为零：必要核对失败；保留预测分数，厚度不得作为唯一基频解释。")
    training_blocks = set(spec["训练块"])
    def component(coordinate):
        block = bisect.bisect_right(fitted["edges"], coordinate) + 1
        if block not in training_blocks or any(abs(coordinate - edge) < 20 for edge in fitted["guards"]):
            return None
        while block - 1 in training_blocks:
            block -= 1
        return block
    alias_rows = []
    for summary in summaries:
        highest = max(summary["有效阶次"], default=1)
        maxima = []
        for coordinates in (full_coordinates, data["sigma"]):
            maximum = 0.0
            for first, second in zip(coordinates, coordinates[1:]):
                if component(first) is not None and component(first) == component(second):
                    difference = abs(phase(parameters, second, summary["入射角_度"])
                                     - phase(parameters, first, summary["入射角_度"]))
                    maximum = max(maximum, highest * difference)
            maxima.append(maximum)
        alias_rows.append({"入射角_度": summary["入射角_度"], "核对最高有效阶次": highest,
                           "完整训练坐标最大相位步长_弧度": maxima[0], "抽样训练最大相位步长_弧度": maxima[1],
                           "低于奈奎斯特相位步长": maxima[1] < math.pi})
        if maxima[1] >= math.pi:
            failures.append("480点对选中谐波可能混叠；须全问共同改为960点重比，不能独自加密。")
    rivals = []
    rival_penalty = candidate["penalty"] if candidate["order"] == 4 else PENALTIES[-1]
    rival_reference = evaluate(parameters, data, 4, rival_penalty, deadline)
    for multiplier in (0.5, 1 / 3, 0.25, 2.0, 3.0, 4.0):
        if time.monotonic() >= deadline:
            break
        alternative = (parameters[0] * multiplier, parameters[1], parameters[2])
        rival = evaluate(alternative, data, 4, rival_penalty, deadline)
        if rival is not None and rival_reference is not None:
            compatible = rival["objective"] <= 1.01 * rival_reference["objective"] + 1e-12
            nonzero = all(item["基频非零"] for item in coefficient_summary(rival, data))
            rivals.append({"厚度倍率": multiplier, "候选厚度_um": alternative[0],
                           "训练目标相对同复杂度参照": rival["objective"] / max(rival_reference["objective"], 1e-30),
                           "基频非零": nonzero, "与当前近似等好或更好": compatible})
            if compatible and nonzero:
                failures.append("整数阶次存在竞争解释：必要核对失败，不将高阶最大幅值当基频改厚度。")
    stability = []
    for label, indices, degree in (("低波数训练半段", [index for index in data["indices"] if index < 240], 2),
                                   ("高波数训练半段", [index for index in data["indices"] if index >= 240], 2),
                                   ("一次基线诊断", data["indices"], 1)):
        if time.monotonic() >= deadline:
            break
        local = prepare(fitted["sigma"], fitted["values"], indices, degree)
        fitted_local = evaluate(parameters, local, candidate["order"], candidate["penalty"], deadline)
        if fitted_local is not None:
            stability.append({"对照": label, "点数_每角度": len(indices), "共同相位是否冻结": True,
                              "各角度系数": coefficient_summary(fitted_local, local)})
    active_sets = [set(summary["有效阶次"]) - {1} for summary in summaries]
    for item in stability:
        active_sets.extend(set(summary["有效阶次"]) - {1} for summary in item["各角度系数"])
    persistent = sorted(set.intersection(*active_sets)) if active_sets else []
    rows = fitted["rows"]
    improved = all(row["标准化均方根误差"] < row["两束参照同口径标准化均方根误差"] for row in rows)
    recurrence_available = all(summary["递推相对离散度"] is not None for summary in summaries)
    recurrence_compatible = recurrence_available and all(
        summary["所有可算幅值比小于一"] and summary["递推相对离散度"] <= 0.25 for summary in summaries)
    complete = len(stability) == 3 and len(rivals) == sum(valid((parameters[0] * multiple, parameters[1], parameters[2]))
                                                       for multiple in (0.5, 1 / 3, 0.25, 2.0, 3.0, 4.0))
    interpretation = "未得到稳定高阶增益；不等于证明多光束不存在。"
    if persistent and improved:
        interpretation = "两束形状不足；高阶可作为显式干扰项，不能单凭谐波认定物理多光束。"
        if recurrence_compatible and complete:
            interpretation += "在预设描述容差下与衰减递推相容，仍缺偏振、光学常数及仪器相干信息。"
    if not complete:
        interpretation += "限时诊断不完整，降低证据强度。"
    boundary = [name for name, value, limits in zip(("厚度_um", "参考折射率", "色散系数"), parameters, BOUNDS)
                if min(abs(value - limits[0]), abs(value - limits[1])) < 1e-6]
    if boundary:
        failures.append("命中数值搜索边界：" + "、".join(boundary))
    return {"材料": material, "折号": spec["折号"], "搜索与训练内选择": fitted["details"],
            "各角度系数": summaries, "分段与基线对照": stability, "持续高阶": persistent,
            "全部留段优于两束参照": improved, "衰减递推相容": bool(recurrence_compatible),
            "递推说明": "复系数C阶=a阶-i*b阶；检查相邻C阶比的幅值衰减及复比恒定性。",
            "描述容差": {"递推相对离散度上限": 0.25, "竞争目标相对容差": 0.01,
                         "统计含义": "仅描述检查，未校准误报率，不作显著性检验。"},
            "抽稀核对": alias_rows, "整数阶次竞争": rivals,
            "阶次竞争口径": {"共同最高阶次": 4, "共同相对惩罚": rival_penalty,
                             "参照厚度_um": parameters[0],
                             "说明": "同复杂度、同惩罚比较，避免四阶模型相对单基频的自由度优势造成假竞争。"},
            "诊断完整": complete, "边界命中": boundary, "解释": interpretation,
            "必要核对失败原因": sorted(set(failures)),
            "附加验证边界": "不做合成回收、双向留角度或全异常波段拟合；本原型不宣称完整建模验证。"}


def refresh_score(result):
    rows = result["分块预测与残差"]
    expected = {(material, fold, angle, block) for material, _ in MATERIALS
                for fold, blocks in ((1, (6, 12)), (2, (1, 7))) for angle in ANGLES for block in blocks}
    actual = {(row["材料"], row["折号"], row["入射角_度"], row["测试块"]) for row in rows}
    if len(rows) != 16 or actual != expected:
        return
    score = sum(row["标准化均方根误差"] for row in rows) / 16
    result["主指标值"] = score
    result["核心指标"] = {METRIC: score}
    result["经验覆盖率"] = sum(row["覆盖点数"] for row in rows) / sum(row["样本数"] for row in rows)
    result["名义覆盖率"] = 0.9
    result["区间平均宽度_反射率比例"] = sum(row["区间平均宽度_反射率比例"] * row["样本数"]
                                             for row in rows) / sum(row["样本数"] for row in rows)
    result["两束参照同一指标"] = sum(row["两束参照同口径标准化均方根误差"] for row in rows) / 16
    result["分材料原始误差"] = [
        {"材料": material, "测试块均方根误差均值_百分点":
         sum(row["均方根误差_百分点"] for row in rows if row["材料"] == material) / 8}
        for material, _ in MATERIALS]
    result["覆盖不足说明"] = ("测试经验覆盖低于名义90%，未用测试残差回调半宽。"
                               if result["经验覆盖率"] < 0.9 else "经验覆盖达标，不代表相关光谱上的分布无关保证。")


def worker(t0):
    result = initial_result()
    checkpoint(result, t0)
    try:
        sigma, values, full_coordinates, protocol = read_inputs(result)
        checkpoint(result, t0)
        jobs = [(material, spec) for material, _ in MATERIALS for spec in protocol["实测小样"]["折分"]]
        fitted_jobs = []
        fit_deadline = t0 + 120.0
        for job_index, (material, spec) in enumerate(jobs):
            now = time.monotonic()
            deadline = now + max(0.0, fit_deadline - now) / (len(jobs) - job_index)
            fitted = solve_fold(sigma, values[material], spec, protocol["共同光学情景"]["初始情景"], deadline)
            fitted["rows"] = score_fold(material, fitted)
            fitted_jobs.append((material, fitted))
            result["分块预测与残差"].extend(fitted["rows"])
            parameters = fitted["best"]["parameters"]
            result["厚度及单位"].append({"材料": material, "折号": spec["折号"],
                                         "厚度_um": parameters[0], "参考折射率": parameters[1],
                                         "色散系数": parameters[2],
                                         "两束参照厚度_um": fitted["baseline"]["parameters"][0],
                                         "厚度变化_um": parameters[0] - fitted["baseline"]["parameters"][0],
                                         "条件": "经验色散与0.5至40微米搜索盒下的共同基相位估计，非标定真值。"})
            result["运行状态"] = f"已完成{job_index + 1}/4组冻结留段预测"
            refresh_score(result)
            checkpoint(result, t0)
        for job_index, (material, fitted) in enumerate(fitted_jobs):
            now = time.monotonic()
            deadline = now + max(0.0, t0 + 150.0 - now) / (len(fitted_jobs) - job_index)
            diagnostic = diagnostics(material, fitted, full_coordinates, deadline)
            result["辅助诊断"].append(diagnostic)
            result["失败原因"].extend(diagnostic["必要核对失败原因"])
            checkpoint(result, t0)
            if time.monotonic() >= t0 + SOFT_SECONDS:
                break
        result["失败原因"] = sorted(set(result["失败原因"]))
        result["资格状态"] = ("必要核对失败，保留数值供解释而不作合格路线排名" if result["失败原因"]
                              else "检查未发现硬失败，但不是物理多光束机制验证")
        if len(result["辅助诊断"]) != 4 or not all(item["诊断完整"] for item in result["辅助诊断"]):
            result["资格状态"] += "；附加诊断不完整"
        result["运行状态"] = "完成单一指标；诊断限制见资格状态"
        checkpoint(result, t0)
        try:
            append_experiment({"类别": "科学尝试", "问题": 3,
                               "尝试": "比较共同基相位的基频参照与至四阶成组收缩，以双角同步连续留段约束复杂度。",
                               "现象": f"四附件各480点，16个测试块的标准化均方根误差为{result['主指标值']:.8g}；"
                                       f"反射率经验区间总覆盖率为{result['经验覆盖率']:.6g}。",
                               "决定": "保留条件厚度及全部留段预测；递推、基频或持续性不支持时降低多光束解释强度。",
                               "依据": "求解结果:主指标值；求解结果:经验覆盖率；求解结果:辅助诊断"})
        except (OSError, ValueError, TimeoutError) as error:
            result["实验记录追加异常"] = str(error)
    except Exception as error:
        result["运行状态"] = "未完成"
        result["失败原因"].append(f"{type(error).__name__}: {error}")
        if result["主指标值"] is None:
            result["核心指标"] = {}
        else:
            result["运行状态"] = "已完成主分数，后续诊断异常"
        try:
            append_experiment({"类别": "流程事件", "问题": 3,
                               "尝试": "执行问题3路线2限时小样原型。",
                               "现象": f"{type(error).__name__}: {error}",
                               "决定": "保留已经完成的分块结果，缺失共同测试块时不作子集排名。",
                               "依据": "求解/问题3/原型结果/路线2.json的失败原因和分块预测与残差。"})
        except (OSError, ValueError, TimeoutError) as record_error:
            result["实验记录追加异常"] = str(record_error)
    checkpoint(result, t0)
    print(json.dumps({"路线名": result["路线名"], "状态": result["运行状态"], "使用附件": result["使用附件"],
                      "每附件样本数": 480 if result["样本索引"] else 0, "完整测试块数": len(result["分块预测与残差"]),
                      "核心指标": result["核心指标"], "厚度及单位": result["厚度及单位"],
                      "经验覆盖率": result["经验覆盖率"], "实际用时秒": result["实际用时秒"],
                      "失败原因": result["失败原因"]}, ensure_ascii=False, allow_nan=False), flush=True)


def main():
    t0 = PROCESS_START
    checkpoint(initial_result(), t0)
    try:
        remaining = max(0.1, HARD_SECONDS - (time.monotonic() - t0))
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(t0)],
                                   cwd=ROOT, timeout=remaining, check=False)
        if completed.returncode == 0:
            return
        reason = f"子进程异常退出，退出码{completed.returncode}"
    except subprocess.TimeoutExpired:
        reason = "到达175秒墙钟硬截止；已终止子进程，不能以部分子集参赛。"
    result = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else initial_result()
    result["截止前指标快照"] = result["主指标值"]
    result["主指标值"] = None
    result["核心指标"] = {}
    result["运行状态"] = "未完成"
    result["失败原因"].append(reason)
    checkpoint(result, t0)
    try:
        append_experiment({"类别": "流程事件", "问题": 3,
                           "尝试": "由父进程执行175秒墙钟限时。", "现象": reason,
                           "决定": "保留截止前快照，未完成的实验不排名。",
                           "依据": "求解/问题3/原型结果/路线2.json的截止前指标快照和失败原因。"})
    except (OSError, ValueError, TimeoutError) as error:
        print(f"实验记录追加异常: {error}", flush=True)
    print(json.dumps({"路线名": result["路线名"], "失败原因": reason,
                      "实际用时秒": result["实际用时秒"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        worker(float(sys.argv[2]))
    else:
        main()
