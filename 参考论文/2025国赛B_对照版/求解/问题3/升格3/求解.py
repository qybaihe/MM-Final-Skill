"""问题3变体3：保留往返衰减场，重写响应、光程搜索及训练内验证。

直接运行本文件；所有结果写入本文件旁的结果目录，不改主线声明或评审。
默认18分钟主动停止扩展计算；初始六案例先保存，随后逐案例、逐扰动保存。
本文件在设计交付阶段仅作静态检查，不执行反演。
"""

import csv
import hashlib
import itertools
import json
import math
import os
import posixpath
import time
import xml.etree.ElementTree as element_tree
from pathlib import Path
from zipfile import ZipFile

import numpy as np
from scipy.optimize import minimize


ROOT = Path(__file__).resolve().parents[3]
RESULT = Path(__file__).resolve().parent / "结果"
NAMESPACE = {"sheet": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
MATERIALS = {"硅": (3, 4), "碳化硅": (1, 2)}
MODELS = ("两束", "完整往返")
ANGLES = (10.0, 15.0)
BOUNDS = np.array([[0.5, 40.0], [1.2, 6.0], [-1.0, 1.0], [-1.5, 2.5], [0.0, 3.0]])
OPTICAL_LOW = np.array([0.6, 1.2, -1.0, -1.5, 0.0])
OPTICAL_SPAN = np.array([239.4, 4.8, 2.0, 4.0, 3.0])
CONFIGS = ((0, 0, 0.1), (1, 0, 0.1), (2, 0, 0.1),
           (1, 1, 0.1), (2, 1, 0.1), (2, 1, 1.0))
DEFAULT_CONFIG = (1, 0, 0.1)
GAIN_CAP = 4.0
SOFT_SECONDS = 1080.0
SEED = 20260910
VERSION = "往返场_正交收缩响应_光程多起点_嵌套原块_v3"


def native(value):
    if isinstance(value, np.ndarray):
        return native(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [native(item) for item in value]
    return value


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(native(payload), stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_sheet(path):
    with ZipFile(path) as archive:
        workbook = element_tree.fromstring(archive.read("xl/workbook.xml"))
        relations = element_tree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.get("Id"): item.get("Target") for item in relations}
        sheet = next(item for item in workbook.findall("sheet:sheets/sheet:sheet", NAMESPACE)
                     if item.get("name") == "Sheet1")
        relation = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = targets[relation]
        member = target.lstrip("/") if target.startswith("/") else posixpath.normpath(
            posixpath.join("xl", target))
        document = element_tree.fromstring(archive.read(member))
        records = []
        for row in document.findall("sheet:sheetData/sheet:row", NAMESPACE):
            source_row = int(row.get("r"))
            if source_row == 1:
                continue
            cells = {cell.get("r"): cell for cell in row.findall("sheet:c", NAMESPACE)}
            values = []
            for column in ("A", "B"):
                cell = cells.get(f"{column}{source_row}")
                if (cell is None or cell.get("t", "n") != "n"
                        or cell.find("sheet:f", NAMESPACE) is not None):
                    raise ValueError(f"{path.name}第{source_row}行并非原始数值")
                values.append(float(cell.findtext("sheet:v", namespaces=NAMESPACE)))
            if not all(math.isfinite(value) for value in values) or values[0] <= 0:
                raise ValueError(f"{path.name}第{source_row}行有非法数值")
            records.append((source_row, values[0], values[1] / 100.0))
    records.sort(key=lambda item: item[1])
    table = np.asarray(records)
    if len(table) != 7469 or len(np.unique(table[:, 1])) != 7469:
        raise ValueError("原附件行数或波数唯一性不符数据档案")
    return table


def load_inputs():
    archive = json.loads((ROOT / "交接/数据档案.json").read_text(encoding="utf-8"))
    baseline_path = ROOT / "求解/问题2/结果/基准交接.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline["共同窗口_cm^-1"] != [1200, 3800] or baseline["抽样点数_每角度"] != 480:
        raise ValueError("上游窗口或抽样口径变化，需显式重新定义比较")
    tables = {}
    hashes = {}
    for number in range(1, 5):
        path = ROOT / "数据" / f"附件{number}.xlsx"
        hashes[path.name] = fingerprint(path)
        tables[number] = read_sheet(path)
        metadata = next(item for item in archive["文件档案"] if item["文件名"] == path.name)
        if hashes[path.name] != metadata["文件哈希"]:
            raise ValueError(f"{path.name}哈希不符数据档案")
    for record in baseline["输入哈希"]:
        if hashes[record["文件名"]] != record["SHA256"]:
            raise ValueError("问题2基准与原附件不匹配")
    sigma = tables[1][:, 1]
    if any(not np.array_equal(sigma, tables[number][:, 1]) for number in range(2, 5)):
        raise ValueError("四谱不再逐行对齐")
    full_indices = np.flatnonzero((sigma >= 1200.0) & (sigma <= 3800.0))
    selected = full_indices[np.arange(480) * (len(full_indices) - 1) // 479]
    rows = tables[1][selected, 0].astype(int).tolist()
    if baseline["抽样源行"] != [rows, rows]:
        raise ValueError("480点源行不符上游；禁止静默换样本")
    expected = [
        {"折号": 1, "训练块": [1, 2, 4, 5, 7, 8, 10, 11], "校准块": [3, 9], "测试块": [6, 12]},
        {"折号": 2, "训练块": [2, 3, 5, 6, 8, 9, 11, 12], "校准块": [4, 10], "测试块": [1, 7]},
    ]
    if baseline["折分"] != expected or baseline["训练保护带_cm^-1"] != 20:
        raise ValueError("原外层块或保护带改变")
    sampled_sigma = sigma[selected]
    edges = (sampled_sigma[39:440:40] + sampled_sigma[40:480:40]) / 2.0
    return {"tables": tables, "full_indices": full_indices, "selected": selected,
            "edges": edges, "folds": expected, "baseline": baseline, "hashes": hashes,
            "baseline_hash": fingerprint(baseline_path), "rows": rows}


def build_case(material, tables, indices, edges, specification=None, train=None):
    coordinates = tables[1][indices, 1].copy()
    values = np.asarray([tables[number][indices, 2] for number in MATERIALS[material]])
    blocks = np.searchsorted(edges, coordinates, side="right") + 1
    if specification is None:
        specification = {"折号": 0, "训练块": list(range(1, 13)), "校准块": [], "测试块": []}
    if train is None:
        train_blocks = specification["训练块"]
        guards = [edge for block, edge in enumerate(edges, 1)
                  if (block in train_blocks) != (block + 1 in train_blocks)]
        keep = np.isin(blocks, train_blocks)
        for edge in guards:
            keep &= np.abs(coordinates - edge) >= 20.0
        train = np.flatnonzero(keep)
    case = {"material": material, "spec": specification, "sigma": coordinates,
            "values": values, "blocks": blocks, "train": np.asarray(train, dtype=int),
            "rows": tables[1][indices, 0].astype(int), "angles": ANGLES,
            "weight": 0.5, "angle_weights": np.array([0.5, 0.5]), "cache": {}}
    return prepare_case(case)


def prepare_case(case):
    case = dict(case, cache={})
    case["abscissa"] = (case["sigma"] - 2500.0) / 1300.0
    train = case["train"]
    if len(train) < 16:
        raise ValueError("训练成员少于16点")
    basis = np.vander(case["abscissa"], 3, increasing=True)
    case["means"] = case["values"][:, train].mean(axis=1)
    coefficients = np.linalg.solve(basis[train].T @ basis[train] + 1e-12 * np.eye(3),
                                   basis[train].T @ case["values"][:, train].T)
    residual = case["values"][:, train] - (basis[train] @ coefficients).T
    case["scales"] = np.maximum(np.quantile(residual, 0.75, axis=1)
                                 - np.quantile(residual, 0.25, axis=1), 1e-4)
    case["naive"] = {"训练均值": np.repeat(case["means"][:, None], len(case["sigma"]), axis=1),
                     "二次趋势": (basis @ coefficients).T}
    return case


def select_training(case, train):
    return prepare_case(dict(case, train=np.asarray(train, dtype=int)))


def feasible(parameters, case):
    if np.any(parameters < BOUNDS[:, 0] - 1e-9) or np.any(parameters > BOUNDS[:, 1] + 1e-9):
        return False
    film = parameters[1] + parameters[2] * ((2000.0 / case["sigma"][[0, -1]]) ** 2 - 1.0)
    threshold = max(math.sin(math.radians(angle)) for angle in case["angles"]) + 1e-6
    return bool(np.min(film) > threshold and np.min(film + parameters[3]) > threshold)


def fields(parameters, case, complete, return_terms=False):
    if not feasible(parameters, case):
        return None
    thickness, reference, dispersion, contrast, loss = parameters
    sigma = case["sigma"]
    film = reference + dispersion * ((2000.0 / sigma) ** 2 - 1.0)
    substrate = film + contrast
    spectra = []
    term_rows = []
    for angle in case["angles"]:
        sine_squared = math.sin(math.radians(angle)) ** 2
        normal_air = math.cos(math.radians(angle))
        normal_film = np.sqrt(film ** 2 - sine_squared)
        normal_substrate = np.sqrt(substrate ** 2 - sine_squared)
        propagation = np.exp(-loss * film / normal_film
                             + 4j * math.pi * thickness * sigma * normal_film / 10000.0)
        intensities = []
        for air, layer, support in ((normal_air, normal_film, normal_substrate),
                                    (1 / normal_air, film ** 2 / normal_film,
                                     substrate ** 2 / normal_substrate)):
            reflection = (air - layer) / (air + layer)
            interface = (layer - support) / (layer + support)
            first = 4 * air * layer / (air + layer) ** 2 * interface * propagation
            ratio = -reflection * interface * propagation
            if np.max(np.abs(ratio)) >= 1.0:
                return None
            field = reflection + (first / (1 - ratio) if complete else first)
            intensities.append(np.abs(field) ** 2)
            if return_terms:
                term_rows.append((reflection, first, ratio, field))
        spectra.append(case["weight"] * intensities[0] + (1 - case["weight"]) * intensities[1])
    return (np.asarray(spectra), term_rows) if return_terms else np.asarray(spectra)


def box_quadratic(gram, right, gain_cap):
    best = None
    for state in itertools.product((0, 1, 2), repeat=len(right)):
        gains = np.array([gain_cap if flag == 2 else 0.0 for flag in state])
        free = np.flatnonzero(np.array(state) == 1)
        fixed = np.flatnonzero(np.array(state) != 1)
        if len(free):
            gains[free] = np.linalg.solve(gram[np.ix_(free, free)],
                right[free] - gram[np.ix_(free, fixed)] @ gains[fixed])
        if np.min(gains) < -1e-9 or np.max(gains) > gain_cap + 1e-9:
            continue
        gains = np.clip(gains, 0.0, gain_cap)
        objective = float(gains @ gram @ gains - 2 * right @ gains)
        if best is None or objective < best[0]:
            best = (objective, gains)
    return best[1]


def response_fit(case, physical, config):
    degree, gain_degree, penalty = config
    train = case["train"]
    cache_key = (degree, gain_degree, penalty)
    if cache_key not in case["cache"]:
        background = np.vander(case["abscissa"], degree + 1, increasing=True)
        inverse = np.linalg.pinv(background[train], rcond=1e-12)
        trend_penalty = penalty / 10.0 * np.arange(degree + 1, dtype=float) ** 2
        background_gram = background[train].T @ background[train] / len(train)
        background_gram += np.diag(trend_penalty)
        weights = (np.ones((len(background), 1)) if gain_degree == 0 else
                   np.column_stack(((1 - case["abscissa"]) / 2, (1 + case["abscissa"]) / 2)))
        case["cache"][cache_key] = (background, inverse, background_gram, weights, trend_penalty)
    background, inverse, background_gram, weights, trend_penalty = case["cache"][cache_key]
    predictions = []
    rows = []
    objectives = []
    for angle_index, spectrum in enumerate(physical):
        scale = case["scales"][angle_index]
        mean = case["means"][angle_index]
        target = (case["values"][angle_index, train] - mean) / scale
        raw = spectrum[:, None] * weights
        projection = inverse @ raw[train]
        centered = raw - background @ projection
        normalization = np.maximum(np.sqrt(np.mean(centered[train] ** 2, axis=0)), 1e-8)
        response = centered / normalization
        coefficients = np.linalg.solve(background_gram, background[train].T @ target / len(train))
        residual_target = target - background[train] @ coefficients
        gram = response[train].T @ response[train] / len(train) + penalty * np.eye(gain_degree + 1)
        right = response[train].T @ residual_target / len(train)
        gain_cap = case.get("gain_cap", GAIN_CAP)
        gains = box_quadratic(gram, right, gain_cap)
        predicted = mean + scale * (background @ coefficients + response @ gains)
        residual = (case["values"][angle_index, train] - predicted[train]) / scale
        objective = float(np.mean(residual ** 2) + penalty * (gains @ gains)
                          + np.sum(trend_penalty * coefficients ** 2))
        predictions.append(predicted)
        objectives.append(objective)
        rows.append({"背景系数": coefficients, "标准化非负增益": gains, "物理投影系数": projection,
                     "物理列尺度": normalization, "训练响应均值_比例": mean,
                     "训练响应尺度_比例": scale, "训练标准化均方误差": np.mean(residual ** 2),
                     "惩罚后损失": objective, "增益上界": gain_cap})
    return np.asarray(predictions), rows, float(np.dot(case["angle_weights"], objectives))


def evaluate(parameters, case, model, config):
    parameters = np.asarray(parameters, dtype=float)
    physical = fields(parameters, case, model == "完整往返")
    if physical is None:
        return None
    predicted, response, loss = response_fit(case, physical, config)
    if not np.isfinite(loss) or not np.all(np.isfinite(predicted)):
        return None
    return {"parameters": parameters.copy(), "prediction": predicted, "response": response,
            "loss": loss, "config": config, "model": model}


def encode(parameters):
    optical = np.array(parameters, dtype=float)
    optical[0] *= optical[1]
    return (optical - OPTICAL_LOW) / OPTICAL_SPAN


def decode(unit):
    parameters = OPTICAL_LOW + np.asarray(unit) * OPTICAL_SPAN
    parameters[0] /= parameters[1]
    return parameters


class StopSearch(Exception):
    pass


def refine(seed, case, model, config, deadline, maximum=100, fixed=None):
    fixed = {} if fixed is None else dict(fixed)
    parameters = np.asarray(seed, dtype=float).copy()
    for index, value in fixed.items():
        parameters[index] = value
    best = evaluate(parameters, case, model, config)
    if best is None:
        for reference, dispersion, contrast in itertools.product(
                (parameters[1], 2.0, 3.5, 5.8), (parameters[2], 0.0), (parameters[3], 0.8)):
            repaired = parameters.copy()
            repaired[1:4] = (reference, dispersion, contrast)
            for index, value in fixed.items():
                repaired[index] = value
            best = evaluate(repaired, case, model, config)
            if best is not None:
                parameters = repaired
                break
    if best is None:
        return None, {"停止原因": "固定条件下未找到可行初值，不等于证明无解", "评估次数": 0, "完整": False}
    initial = encode(parameters)
    free = [index for index in range(5) if index not in fixed]
    calls = 0
    reason = "优化结束"

    def rebuild(values):
        unit = initial.copy()
        unit[free] = values
        candidate = decode(unit)
        for index, value in fixed.items():
            candidate[index] = value
        return candidate

    def objective(values):
        nonlocal calls, best
        if time.monotonic() >= deadline:
            raise StopSearch("时间预算")
        if calls >= maximum:
            raise StopSearch("共同评估上限")
        calls += 1
        candidate = evaluate(rebuild(values), case, model, config)
        if candidate is None:
            return 1e12
        if candidate["loss"] < best["loss"]:
            best = candidate
        return candidate["loss"]

    def constraints(values):
        candidate = rebuild(values)
        endpoints = case["sigma"][[0, -1]]
        film = candidate[1] + candidate[2] * ((2000.0 / endpoints) ** 2 - 1)
        threshold = max(math.sin(math.radians(angle)) for angle in case["angles"]) + 2e-6
        return np.concatenate(([candidate[0] - 0.5, 40 - candidate[0]],
                               film - threshold, film + candidate[3] - threshold))

    try:
        outcome = minimize(objective, initial[free], method="SLSQP", bounds=[(0, 1)] * len(free),
                           constraints={"type": "ineq", "fun": constraints},
                           options={"maxiter": 30, "ftol": 1e-7, "eps": 1e-6})
        reason = "收敛" if outcome.success else f"优化器停止:{outcome.status}"
    except StopSearch as error:
        reason = str(error)
    return best, {"停止原因": reason, "评估次数": calls, "共同评估上限": maximum,
                  "完整": reason != "时间预算", "优化器收敛": reason == "收敛",
                  "全局最优保证": False, "固定参数索引": list(fixed)}


def spectral_seeds(case, deadline):
    train = case["train"]
    if len(train) > 480:
        train = train[np.arange(480) * (len(train) - 1) // 479]
    sigma = case["sigma"][train]
    baseline = np.vander(case["abscissa"][train], 3, increasing=True)
    residual = case["values"][:, train].T
    residual = residual - baseline @ np.linalg.lstsq(baseline, residual, rcond=1e-12)[0]
    residual /= case["scales"][None, :]
    seeds = [np.array([5.0, 3.0, 0.0, 0.8, 0.2])]
    grid = np.linspace(0.5, 40.0, 384)
    for reference, dispersion in itertools.product((1.6, 2.6, 3.6, 4.8, 5.8), (-0.3, 0.0, 0.3)):
        if time.monotonic() >= deadline:
            break
        seed = np.array([5.0, reference, dispersion, 0.8, 0.2])
        if not feasible(seed, case):
            continue
        scores = np.zeros(len(grid))
        film = reference + dispersion * ((2000 / sigma) ** 2 - 1)
        for angle_index, angle in enumerate(case["angles"]):
            normal = np.sqrt(film ** 2 - math.sin(math.radians(angle)) ** 2)
            phase = 4 * math.pi * grid[:, None] * sigma[None, :] * normal[None, :] / 10000
            cosine, sine = np.cos(phase), np.sin(phase)
            cosine -= cosine.mean(axis=1, keepdims=True)
            sine -= sine.mean(axis=1, keepdims=True)
            scores += case["angle_weights"][angle_index] * (
                (cosine @ residual[:, angle_index]) ** 2 / np.maximum(np.sum(cosine ** 2, axis=1), 1e-12)
                + (sine @ residual[:, angle_index]) ** 2 / np.maximum(np.sum(sine ** 2, axis=1), 1e-12))
        chosen = []
        for index in np.argsort(scores)[::-1]:
            if all(abs(grid[index] - old) >= 0.5 for old in chosen):
                chosen.append(float(grid[index]))
                for contrast in (-0.6, 0.8):
                    candidate = np.array([grid[index], reference, dispersion, contrast, 0.2])
                    if feasible(candidate, case):
                        seeds.append(candidate)
            if len(chosen) == 2:
                break
    return seeds


def fit_pair(case, config, seeds, deadline, maximum=100, starts=2, fixed=None):
    pools = {model: [] for model in MODELS}
    coarse_complete = True
    for seed in seeds:
        if time.monotonic() >= deadline and pools[MODELS[0]]:
            coarse_complete = False
            break
        for model in MODELS:
            candidate = evaluate(seed, case, model, config)
            if candidate is not None:
                pools[model].append(candidate)
    if any(not pool for pool in pools.values()):
        raise ValueError("共同物理候选均不可行")
    common = []
    for rank in range(starts):
        for model in MODELS:
            ordered = sorted(pools[model], key=lambda item: item["loss"])
            seed = ordered[min(rank, len(ordered) - 1)]["parameters"]
            if not any(np.allclose(seed, previous, rtol=0, atol=1e-10) for previous in common):
                common.append(seed)
    best = {model: min(pool, key=lambda item: item["loss"]) for model, pool in pools.items()}
    traces = {model: [] for model in MODELS}
    for seed in common:
        for model in MODELS:
            candidate, trace = refine(seed, case, model, config, deadline, maximum=maximum, fixed=fixed)
            traces[model].append(trace)
            if candidate is not None:
                pools[model].append(candidate)
                if candidate["loss"] < best[model]["loss"]:
                    best[model] = candidate
    complete = coarse_complete and all(row["完整"] for group in traces.values() for row in group)
    branches = {}
    for model in MODELS:
        branches[model] = {f"训练目标容差{tolerance:g}%": [
            float(min(item["parameters"][0] for item in pools[model]
                      if item["loss"] <= best[model]["loss"] * (1 + tolerance / 100) + 1e-12)),
            float(max(item["parameters"][0] for item in pools[model]
                      if item["loss"] <= best[model]["loss"] * (1 + tolerance / 100) + 1e-12))]
            for tolerance in (0, 5, 10)}
    return best, {"公平性成立": complete, "共同初值": common, "共同粗候选数": len(seeds),
                  "粗搜索完整": coarse_complete, "优化记录": traces, "训练近优分支范围_um": branches}


def rmse(case, prediction, indices):
    return np.sqrt(np.mean((prediction[:, indices] - case["values"][:, indices]) ** 2, axis=1)) / case["scales"]


def inner_partitions(case):
    allowed = case["train"]
    available = sorted(set(case["blocks"][allowed].tolist()))
    chosen = sorted(set((available[0], available[-1], available[len(available) // 3],
                         available[2 * len(available) // 3])))
    partitions = []
    for block in chosen:
        validation = allowed[case["blocks"][allowed] == block]
        lower, upper = case["sigma"][validation[[0, -1]]]
        training = allowed[(case["sigma"][allowed] < lower - 20)
                           | (case["sigma"][allowed] > upper + 20)]
        if len(training) >= 16 and len(validation) >= 8:
            partitions.append((select_training(case, training), validation, block))
    return partitions


def choose_config(case, deadline, progress):
    partitions = inner_partitions(case)
    records = [[] for config in CONFIGS]
    details = []
    complete = True
    for inner, validation, block in partitions:
        if time.monotonic() >= deadline:
            complete = False
            break
        seeds = spectral_seeds(inner, deadline)
        naive = {label: rmse(inner, predicted, validation) for label, predicted in inner["naive"].items()}
        detail = {"原始块号": block, "训练源行": inner["rows"][inner["train"]],
                  "验证源行": inner["rows"][validation], "朴素误差_逐角": naive, "候选": []}
        for index, config in enumerate(CONFIGS):
            if time.monotonic() >= deadline:
                complete = False
                break
            best, trace = fit_pair(inner, config, seeds, deadline, maximum=70, starts=1)
            scores = {model: rmse(inner, best[model]["prediction"], validation) for model in MODELS}
            reference = np.minimum(naive["训练均值"], naive["二次趋势"])
            ratios = np.concatenate([scores[model] / np.maximum(reference, 1e-8) for model in MODELS])
            records[index].append({"分块误差": scores, "朴素误差": naive,
                                   "风险比": ratios, "公平完整": trace["公平性成立"]})
            detail["候选"].append({"响应设置": config, "误差_逐角": scores,
                                    "公平完整": trace["公平性成立"]})
            complete &= trace["公平性成立"]
        details.append(detail)
        progress({"内层分块": details, "状态": "训练内比较中"})
    complete &= all(len(rows) == len(partitions) for rows in records) and len(partitions) >= 3
    summary = []
    for config, rows in zip(CONFIGS, records):
        if rows:
            ratios = np.concatenate([row["风险比"] for row in rows])
            summary.append({"响应设置": config, "完成内折数": len(rows),
                            "平均风险比": float(ratios.mean()), "最大风险比": float(ratios.max()),
                            "选择目标": float(0.5 * ratios.mean() + 0.5 * ratios.max())})
    selected = min(summary, key=lambda row: (row["选择目标"], sum(row["响应设置"][:2]))) if complete else None
    config = tuple(selected["响应设置"]) if selected else DEFAULT_CONFIG
    model_scores = {}
    if complete:
        chosen = records[CONFIGS.index(config)]
        for model in (*MODELS, "训练均值", "二次趋势"):
            values = np.concatenate([row["分块误差" if model in MODELS else "朴素误差"][model] for row in chosen])
            model_scores[model] = float(0.5 * np.mean(values) + 0.5 * np.max(values))
    selected_model = min(model_scores, key=model_scores.get) if model_scores else "完整往返"
    report = {"状态": "完整" if complete else "计算截断，使用预先固定响应，不从部分候选中择优",
              "选型完整": bool(complete), "内层分块": details, "候选汇总": summary,
              "所选响应设置": config, "内层预测选择分数": model_scores,
              "所选预测模型": selected_model, "条件厚度模型": "完整往返",
              "选择依据": "同原始块的端点与内部留段；两物理模型等权，平均与最差风险比各占一半",
              "校准用途": "只构造区间，不选择参数、响应或模型",
              "解释": "若朴素模型胜出仅改变预测标签，不以朴素误差冒充往返场精度"}
    progress(report)
    return config, selected_model, report


def parameter_record(candidate):
    parameters = candidate["parameters"]
    unidentifiable = all(np.max(row["标准化非负增益"]) <= 1e-8 for row in candidate["response"])
    return {"厚度_um": parameters[0], "参考折射率": parameters[1], "色散系数": parameters[2],
            "衬底折射率对比": parameters[3], "有效往返损耗": parameters[4],
            "参考光学厚度_um": parameters[0] * parameters[1], "参数向量": parameters,
            "响应设置_背景阶_增益阶_惩罚": candidate["config"], "训练惩罚后目标": candidate["loss"],
            "各角响应": candidate["response"], "厚度未识别": unidentifiable,
            "点值含义": "零增益平台的算法代表值，有效物理答案是全搜索范围" if unidentifiable else "给定光学与响应族的条件解，不保证唯一",
            "触边参数索引": np.flatnonzero(np.any(np.isclose(parameters[:, None], BOUNDS,
                                                                         rtol=0, atol=1e-4), axis=1)),
            "补充条件": "需独立折射率色散、仪器响应或标定厚度消解多解；经验色散不是已测光学常数"}


def empirical_half_width(residuals, alpha=0.1):
    residuals = np.sort(np.abs(np.asarray(residuals)))
    rank = math.ceil((len(residuals) + 1) * (1 - alpha))
    return None if rank > len(residuals) else float(residuals[rank - 1])


def score_case(case, best, selected_model):
    predictions = {model: best[model]["prediction"] for model in MODELS}
    predictions.update(case["naive"])
    calibration = np.flatnonzero(np.isin(case["blocks"], case["spec"]["校准块"]))
    rows = []
    intervals = []
    for model, prediction in predictions.items():
        for angle_index, number in enumerate(MATERIALS[case["material"]]):
            residuals = prediction[angle_index, calibration] - case["values"][angle_index, calibration]
            half_width = empirical_half_width(residuals)
            block_residuals = [float(np.max(np.abs(prediction[angle_index, case["blocks"] == block]
                                      - case["values"][angle_index, case["blocks"] == block])))
                               for block in case["spec"]["校准块"]]
            block_width = empirical_half_width(block_residuals)
            for block in case["spec"]["测试块"]:
                indices = np.flatnonzero(case["blocks"] == block)
                residual = prediction[angle_index, indices] - case["values"][angle_index, indices]
                raw_rmse = float(np.sqrt(np.mean(residual ** 2)))
                rows.append({"材料": case["material"], "折号": case["spec"]["折号"], "附件": number,
                             "入射角_度": case["angles"][angle_index], "测试块": block, "模型": model,
                             "测试点数": len(indices), "标准化均方根误差": raw_rmse / case["scales"][angle_index],
                             "均方根误差_反射率比例": raw_rmse,
                             "所选预测模型": selected_model, "源行": case["rows"][indices]})
                distance = np.min(np.abs(case["sigma"][indices, None]
                                         - case["sigma"][case["train"]][None, :]), axis=1)
                intervals.append({"材料": case["material"], "折号": case["spec"]["折号"], "附件": number,
                                  "测试块": block, "模型": model, "名义覆盖率": 0.9,
                                  "点残差半宽_比例": half_width,
                                  "经验覆盖率": float(np.mean(np.abs(residual) <= half_width)) if half_width is not None else 1.0,
                                  "平均区间宽度_比例": None if half_width is None else 2 * half_width,
                                  "距最近训练波数均值_cm^-1": float(distance.mean()),
                                  "块同时覆盖半宽_比例": block_width,
                                  "块同时覆盖半宽类型": "正无穷" if block_width is None else "有限",
                                  "块同时经验覆盖率": 1.0 if block_width is None else float(np.all(np.abs(residual) <= block_width)),
                                  "解释": "点残差有限样本秩校正；连续谱不交换，不宣称共形保证。仅2校准块时90%块共形半宽必为无穷"})
    return rows, intervals


def field_checks(case, candidate, deadline):
    physical, terms = fields(candidate["parameters"], case, True, return_terms=True)
    finite_errors = {}
    tail_bounds = {}
    maximum_ratio = max(float(np.max(np.abs(row[2]))) for row in terms)
    needed = 1 if maximum_ratio == 0 else min(4096, max(64, math.ceil(math.log(1e-15) / math.log(maximum_ratio))))
    for count in sorted(set((1, 2, 4, 8, 16, 32, 64, needed))):
        if time.monotonic() >= deadline:
            break
        errors = []
        tails = []
        for reflection, first, ratio, complete in terms:
            term = first.copy()
            finite = reflection.astype(complex).copy()
            for order in range(count):
                finite += term
                term *= ratio
            errors.append(float(np.max(np.abs(finite - complete))))
            tails.append(float(np.max(np.abs(first) * np.abs(ratio) ** count / (1 - np.abs(ratio)))))
        finite_errors[str(count)] = max(errors)
        tail_bounds[str(count)] = max(tails)
    sample = np.unique(np.linspace(0, len(case["sigma"]) - 1, 33, dtype=int))
    scalar_errors = []
    thickness, reference, dispersion, contrast, loss = candidate["parameters"]
    for angle_index, angle in enumerate(case["angles"]):
        sine_squared = math.sin(math.radians(angle)) ** 2
        air_normal = math.cos(math.radians(angle))
        for index in sample:
            sigma = float(case["sigma"][index])
            film = reference + dispersion * ((2000 / sigma) ** 2 - 1)
            substrate = film + contrast
            film_normal = math.sqrt(film ** 2 - sine_squared)
            substrate_normal = math.sqrt(substrate ** 2 - sine_squared)
            phase = 4 * math.pi * thickness * sigma * film_normal / 10000
            propagation = math.exp(-loss * film / film_normal) * complex(math.cos(phase), math.sin(phase))
            reflected = []
            for air, layer, support in ((air_normal, film_normal, substrate_normal),
                                        (1 / air_normal, film ** 2 / film_normal, substrate ** 2 / substrate_normal)):
                front = (air - layer) / (air + layer)
                back = (layer - support) / (layer + support)
                first = 2 * air / (air + layer) * 2 * layer / (air + layer) * back * propagation
                field = front + first / (1 + front * back * propagation)
                reflected.append(abs(field) ** 2)
            value = case["weight"] * reflected[0] + (1 - case["weight"]) * reflected[1]
            scalar_errors.append(abs(value - physical[angle_index, index]))
    two_physical = fields(candidate["parameters"], case, False)
    gain_degree = candidate["config"][1]
    weights = (np.ones((len(case["sigma"]), 1)) if gain_degree == 0 else
               np.column_stack(((1 - case["abscissa"]) / 2, (1 + case["abscissa"]) / 2)))
    observation = []
    for angle_index, response in enumerate(candidate["response"]):
        change = case["scales"][angle_index] * (
            (physical[angle_index] - two_physical[angle_index])[:, None]
            * weights / response["物理列尺度"]) @ response["标准化非负增益"]
        amplitude = float(np.sqrt(np.mean(change[case["train"]] ** 2)))
        noise = float(np.sqrt(np.mean((candidate["prediction"][angle_index, case["train"]]
                                      - case["values"][angle_index, case["train"]]) ** 2)))
        observation.append({"附件": MATERIALS[case["material"]][angle_index],
                            "同参数同响应高阶改变量均方根_比例": amplitude,
                            "训练残差均方根_比例": noise,
                            "高阶改变量相对残差": amplitude / max(noise, 1e-12),
                            "必要条件": "非零返回、后续反射且往返模小于1；不代表相干条件已知",
                            "未提供条件": ["时间相干长度", "空间重叠", "仪器分辨率"],
                            "替代解释": "平滑响应失配、色散误设、折射率厚度互补；需结合逐块验证"})
    return {"有限项复场最大差": finite_errors, "几何余项上界": tail_bounds,
            "最大往返乘子模": maximum_ratio, "传播与收敛约束满足": maximum_ratio < 1,
            "标量独立实现最大差_反射率比例": max(scalar_errors),
            "闭式核验通过": finite_errors.get(str(needed), 1.0) <= 1e-12 and max(scalar_errors) <= 1e-12,
            "固定参数两束与完整场均方根差_比例": np.sqrt(np.mean((physical - two_physical) ** 2, axis=1)),
            "逐附件高阶可观测性诊断": observation}


def sensitivity_specs(candidate):
    parameters = candidate["parameters"]
    specs = [("有效损耗未扰动控制", 4, parameters[4], None),
             ("有效损耗减20%", 4, parameters[4] * 0.8, None),
             ("有效损耗加20%", 4, parameters[4] * 1.2, None),
             ("有效损耗绝对增加0→0.1", 4, 0.1, "仅零损耗"),
             ("有效损耗绝对增加0→0.2", 4, 0.2, "仅零损耗")]
    for index, name, factors in ((3, "衬底对比", (0.8, 1.2)), (1, "参考折射率", (0.97, 1.03)),
                                  (2, "色散系数", (0.8, 1.2))):
        for factor in factors:
            specs.append((f"{name}{'减' if factor < 1 else '加'}{abs(factor - 1) * 100:g}%",
                          index, parameters[index] * factor, None))
    specs.extend((label, None, value, kind) for label, value, kind in (
        ("偏振权重全s", 1.0, "偏振"), ("偏振权重全p", 0.0, "偏振"),
        ("入射角同时减0.5度", -0.5, "角度"), ("入射角同时加0.5度", 0.5, "角度"),
        ("窗口下界加100", 1300.0, "下界"), ("窗口上界减100", 3700.0, "上界")))
    if abs(parameters[2]) <= 1e-8:
        specs.extend((("零色散绝对减0.05", 2, -0.05, None), ("零色散绝对加0.05", 2, 0.05, None)))
    if abs(parameters[3]) <= 1e-8:
        specs.extend((("零衬底对比绝对减0.1", 3, -0.1, None), ("零衬底对比绝对加0.1", 3, 0.1, None)))
    specs.extend((("响应收缩减半", None, 0.5, "收缩"), ("响应收缩加倍", None, 2.0, "收缩"),
                  ("标准化增益上界减半", None, GAIN_CAP / 2, "增益上界"),
                  ("标准化增益上界加倍", None, GAIN_CAP * 2, "增益上界")))
    return specs


def sensitivity_case(case, candidate, spec, deadline):
    label, index, requested, kind = spec
    row = {"材料": case["material"], "折号": case["spec"]["折号"], "情景": label,
           "基准厚度_um": candidate["parameters"][0], "请求固定参数值": requested}
    if kind == "仅零损耗" and abs(candidate["parameters"][4]) > 1e-8:
        return dict(row, 状态="不适用", 原因="当前基准损耗非零；不是遗漏重估")
    if time.monotonic() >= deadline:
        return dict(row, 状态="未完成", 原因="18分钟软截止；不把未计算情景写成稳定")
    changed = dict(case)
    config = candidate["config"]
    if kind == "偏振":
        changed["weight"] = requested
    elif kind == "角度":
        changed["angles"] = tuple(angle + requested for angle in ANGLES)
    elif kind in ("下界", "上界"):
        keep = case["sigma"][case["train"]] >= requested if kind == "下界" else case["sigma"][case["train"]] <= requested
        changed["train"] = case["train"][keep]
    elif kind == "收缩":
        config = (*config[:2], config[2] * requested)
    elif kind == "增益上界":
        changed["gain_cap"] = requested
    changed = prepare_case(changed)
    fixed = {} if index is None else {index: float(np.clip(requested, *BOUNDS[index]))}
    control_fixed = {} if index is None else {index: float(candidate["parameters"][index])}
    control_deadline = time.monotonic() + max(0.0, deadline - time.monotonic()) / 2
    control, control_trace = refine(candidate["parameters"], case, "完整往返", candidate["config"],
                                    control_deadline, maximum=110, fixed=control_fixed)
    best, trace = refine(candidate["parameters"], changed, "完整往返", config,
                         deadline, maximum=110, fixed=fixed)
    if best is None:
        return dict(row, 状态="约束不可行", 停止状态=trace)
    return dict(row, 状态="已重估", 重估完整参数=parameter_record(best), 停止状态=trace,
                实际固定参数值=fixed.get(index), 扰动后厚度_um=float(best["parameters"][0]),
                厚度变化_um=float(best["parameters"][0] - candidate["parameters"][0]),
                相对变化_百分数=float(100 * (best["parameters"][0] / candidate["parameters"][0] - 1)),
                未扰动控制参数=None if control is None else parameter_record(control),
                未扰动控制停止状态=control_trace,
                扣除控制厚度变化_um=None if control is None else float(best["parameters"][0] - control["parameters"][0]),
                配对可比=bool(control is not None and control_trace["完整"] and trace["完整"]),
                训练源行=changed["rows"][changed["train"]])


def cross_angle(case, best, deadline):
    rows = []
    for source in range(2):
        if time.monotonic() >= deadline:
            break
        changed = dict(case, angle_weights=np.eye(2)[source], cache={})
        seeds = spectral_seeds(changed, deadline)
        fitted, trace = fit_pair(changed, DEFAULT_CONFIG, seeds, deadline,
                                 maximum=90, starts=1)
        target = 1 - source
        test = np.flatnonzero(np.isin(case["blocks"], case["spec"]["测试块"]))
        rows.append({"训练物理参数的附件": MATERIALS[case["material"]][source],
                     "目标附件": MATERIALS[case["material"]][target],
                     "目标响应拟合成员": "仅目标角外训练；共享物理量冻结",
                     "响应设置": "预先固定默认值，不借用双角联合选型结果",
                     "标准化误差": {model: rmse(case, fitted[model]["prediction"], test)[target] for model in MODELS},
                     "条件厚度_um": {model: fitted[model]["parameters"][0] for model in MODELS},
                     "搜索状态": trace})
    return rows


def summarize_validation(rows):
    groups = {}
    for row in rows:
        key = (row["材料"], row["折号"], row["附件"], row["测试块"])
        groups.setdefault(key, {})[row["模型"]] = row["标准化均方根误差"]
    comparisons = []
    for key, scores in groups.items():
        row = {"材料": key[0], "折号": key[1], "附件": key[2], "测试块": key[3], "各模型误差": scores}
        row["完整相对两束下降_%"] = 100 * (1 - scores["完整往返"] / max(scores["两束"], 1e-12))
        for label in ("训练均值", "二次趋势"):
            row[f"完整相对{label}下降_%"] = 100 * (1 - scores["完整往返"] / max(scores[label], 1e-12))
        comparisons.append(row)
    material_summary = {}
    for material in MATERIALS:
        relevant = [row for row in comparisons if row["材料"] == material]
        fold_summary = []
        for fold in (1, 2):
            members = [row for row in relevant if row["折号"] == fold]
            if members:
                means = {model: float(np.mean([row["各模型误差"][model] for row in members]))
                         for model in (*MODELS, "训练均值", "二次趋势")}
                fold_summary.append({"折号": fold, "完成比较单元数": len(members), "平均标准化均方根误差": means,
                                     "完整相对基线下降_%": {model: 100 * (1 - means["完整往返"] / max(means[model], 1e-12))
                                                            for model in ("两束", "训练均值", "二次趋势")}})
        material_summary[material] = {"逐角逐折逐块": relevant, "逐折汇总": fold_summary,
            "预期比较单元数": 8, "完成比较单元数": len(relevant),
            "收益阈值敏感性": [{"收益阈值_%": threshold,
                                "超过阈值单元数": sum(row["完整相对两束下降_%"] > threshold for row in relevant),
                                "全部单元超过": len(relevant) == 8 and all(row["完整相对两束下降_%"] > threshold for row in relevant)}
                               for threshold in (0.0, 0.5, 1.0, 2.0)],
            "全部块严格胜两朴素基线": len(relevant) == 8 and all(row[f"完整相对{label}下降_%"] > 0
                                                  for row in relevant for label in ("训练均值", "二次趋势")),
            "高阶收益全部块同向为正": len(relevant) == 8 and all(row["完整相对两束下降_%"] > 0 for row in relevant),
            "反向块": [row for row in relevant if row["完整相对两束下降_%"] <= 0],
            "通过含义": "只是本次内部逐块数值诊断；不代写G2裁定，不宣称新独立验证"}
    return material_summary


def bootstrap_pair(case, best, deadline, progress, repetitions=40):
    random_seed = SEED + (0 if case["material"] == "硅" else 1)
    generator = np.random.default_rng(random_seed)
    train = case["train"]
    blocks = sorted(set(case["blocks"][train].tolist()))
    rows = []
    for repetition in range(repetitions):
        if time.monotonic() >= deadline:
            break
        selected = generator.choice(blocks, size=len(blocks), replace=True)
        members = np.concatenate([train[case["blocks"][train] == block] for block in selected])
        changed = select_training(case, members)
        fitted, trace = fit_pair(changed, best["完整往返"]["config"],
                                 [best[model]["parameters"] for model in MODELS], deadline,
                                 maximum=60, starts=1)
        row = {"编号": repetition + 1, "抽取原块": selected,
               "厚度_um": {model: fitted[model]["parameters"][0] for model in MODELS},
               "配对厚度差_um": fitted["完整往返"]["parameters"][0] - fitted["两束"]["parameters"][0],
               "完整": trace["公平性成立"]}
        rows.append(row)
        progress(rows)
    valid = [row for row in rows if row["完整"]]
    result = {"材料": case["material"], "计划次数": repetitions, "完成次数": len(valid), "随机种子": random_seed,
              "逐次记录": rows, "构造": "同次12个原波数块成对重采样并重估；固定已选响应，局部条件稳定性，不作厚度置信区间",
              "厚度经验覆盖率": None, "厚度经验覆盖率不可计算原因": "真实厚度未随附件提供"}
    if valid:
        differences = [row["配对厚度差_um"] for row in valid]
        result["配对厚度差经验分位范围_um"] = np.quantile(differences, [0.025, 0.975])
        result["完整厚度经验分位范围_um"] = np.quantile([row["厚度_um"]["完整往返"] for row in valid], [0.025, 0.975])
        relative = abs(best["完整往返"]["parameters"][0] / best["两束"]["parameters"][0] - 1) * 100
        result["厚度影响情景判断"] = [{"阈值_%": threshold, "绝对相对变化_%": relative,
                                       "超过情景阈值": relative > threshold} for threshold in (0.5, 1, 2)]
    return result


def known_thickness_validation(template, deadline, t0):
    destination = ROOT / "数据/问题3_冻结合成输入/升格3条件验证"
    destination.mkdir(parents=True, exist_ok=True)
    generator = np.random.default_rng(SEED + 30)
    manifest = {"用途": "独立已知光学条件的数值验证，不进入实测核心指标", "随机种子": SEED + 30, "文件": []}
    rows = []
    types = (("低损耗", 0.05, True), ("强损耗", 1.5, True),
             ("无高阶负对照", 0.1, False), ("色散失配", 0.05, True))
    for kind, loss, complete in types:
        for thickness, reference in ((2.35, 2.2), (7.45, 3.6), (12.65, 5.0)):
            if time.monotonic() >= deadline:
                break
            truth = np.array([thickness, reference, 0.18, 0.8, loss])
            physical = fields(truth, template, complete)
            observed = physical + generator.normal(0.0, 0.002, size=physical.shape)
            identifier = f"情景{len(rows) + 1:02d}"
            path = destination / f"{identifier}.csv"
            temporary = path.with_name(path.name + ".tmp")
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["原附件源行", "波数_cm^-1", "反射率10度_比例", "反射率15度_比例"])
                writer.writerows(zip(template["rows"], template["sigma"], observed[0], observed[1]))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            manifest["文件"].append({"文件名": path.name, "用途": kind,
                "生成方式": "固定真参数往返场或两束场，加独立高斯反射率比例噪声；原波数坐标不变",
                "参数": {"真厚度_um": thickness, "参考折射率": reference, "色散系数": 0.18,
                         "衬底对比": 0.8, "损耗": loss, "噪声标准差_比例": 0.002, "完整往返": complete},
                "SHA256": fingerprint(path)})
            atomic_json(destination / "输入清单.json", manifest)
            changed = prepare_case(dict(template, values=observed.copy()))
            fixed = {1: reference, 2: 0.0 if kind == "色散失配" else 0.18, 3: 0.8, 4: loss}
            seeds = [np.array([value, fixed[1], fixed[2], fixed[3], fixed[4]])
                     for value in np.linspace(0.5, 40.0, 384)]
            best, trace = fit_pair(changed, (0, 0, 0.01), seeds, deadline,
                                   maximum=90, starts=2, fixed=fixed)
            selected = "完整往返" if complete else "两束"
            interval = trace["训练近优分支范围_um"][selected]["训练目标容差10%"]
            scores, coverage = score_case(changed, best, selected)
            rows.append({"情景": kind, "冻结输入": str(path.relative_to(ROOT)), "输入SHA256": fingerprint(path),
                         "真厚度_um": thickness, "估计厚度_um": best[selected]["parameters"][0],
                         "相对误差_%": 100 * (best[selected]["parameters"][0] / thickness - 1),
                         "条件包络_um": interval, "真厚度落入条件包络": interval[0] <= thickness <= interval[1],
                         "配对厚度差_um": best["完整往返"]["parameters"][0] - best["两束"]["parameters"][0],
                         "逐块评分": scores, "反射率区间覆盖": coverage, "搜索状态": trace,
                         "口径": "已知光学参数下检验厚度搜索；失配组故意将色散设零，不能代替未知光学常数的实测精度"})
            atomic_json(RESULT / "已知厚度验证.json", {"案例": rows, "实际用时秒": time.monotonic() - t0})
    matched = [row for row in rows if row["情景"] != "色散失配" and row["搜索状态"]["公平性成立"]]
    null = [abs(row["配对厚度差_um"]) for row in rows
            if row["情景"] == "无高阶负对照" and row["搜索状态"]["公平性成立"]]
    result = {"案例": rows, "完成匹配情景数": len(matched), "计划情景数": 12,
              "条件包络经验包含率": float(np.mean([row["真厚度落入条件包络"] for row in matched])) if matched else None,
              "条件包络名义覆盖率": None,
              "包络构造": "同一训练惩罚目标10%内的已计算物理解；只是条件包络，不冒充95%置信区间",
              "无高阶样本数": len(null), "无高阶绝对厚度差95%经验分位_um": float(np.quantile(null, 0.95)) if null else None,
              "阈值用途": "少量独立负对照仅校查误判风险，不足以承诺实测5%误判率或自动修正碳化硅",
              "厚度覆盖解释": "这里存在模拟真值；经验包含率不转用作实测厚度覆盖率",
              "冻结输入清单": str((destination / "输入清单.json").relative_to(ROOT)),
              "实际用时秒": time.monotonic() - t0}
    atomic_json(RESULT / "已知厚度验证.json", result)
    return result


def write_predictions(case, best):
    path = RESULT / f"预测_{case['material']}_折{case['spec']['折号']}.csv"
    temporary = path.with_name(path.name + ".tmp")
    train = set(case["train"].tolist())
    predictions = {model: best[model]["prediction"] for model in MODELS}
    predictions.update(case["naive"])
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["附件", "源行", "波数_cm^-1", "原始块", "用途", "反射率_比例", *predictions])
        for angle_index, number in enumerate(MATERIALS[case["material"]]):
            for index, sigma in enumerate(case["sigma"]):
                block = int(case["blocks"][index])
                kind = ("训练" if index in train else "校准" if block in case["spec"]["校准块"]
                        else "测试" if block in case["spec"]["测试块"] else "保护带")
                writer.writerow([number, case["rows"][index], sigma, block, kind,
                                 case["values"][angle_index, index],
                                 *(prediction[angle_index, index] for prediction in predictions.values())])
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def publish(payload, t0):
    payload["实际用时秒"] = time.monotonic() - t0
    cases = payload["案例"]
    ranges = {}
    for material in MATERIALS:
        estimates = [row[model]["厚度_um"] for row in cases if row["材料"] == material for model in MODELS]
        for row in payload["灵敏度_参数扰动"]:
            if row["材料"] == material and row.get("状态") == "已重估":
                estimates.append(row["扰动后厚度_um"])
        for row in cases:
            if row["材料"] != material:
                continue
            for branches in row.get("搜索状态", {}).get("训练近优分支范围_um", {}).values():
                estimates.extend(branches["训练目标容差10%"])
        ranges[material] = ([0.5, 40.0] if any(row[model]["厚度未识别"] for row in cases
                          if row["材料"] == material for model in MODELS) else [min(estimates), max(estimates)])
    silicon = next(row for row in cases if row["材料"] == "硅" and row["折号"] == 0)
    silicon_folds = {row["折号"]: row for row in cases if row["材料"] == "硅"}
    baseline = payload["碳化硅正式基准"]
    core = {"硅全量完整往返厚度_um": silicon["完整往返"]["厚度_um"],
            "硅折1完整往返条件厚度_um": silicon_folds[1]["完整往返"]["厚度_um"],
            "硅折2完整往返条件厚度_um": silicon_folds[2]["完整往返"]["厚度_um"],
            "硅已计算条件包络下限_um": ranges["硅"][0], "硅已计算条件包络上限_um": ranges["硅"][1],
            "碳化硅正式基准厚度_um": baseline["厚度_um"],
            "碳化硅正式条件范围下限_um": baseline["条件范围_um"][0],
            "碳化硅正式条件范围上限_um": baseline["条件范围_um"][1]}
    payload.update({"核心指标": core, "厚度条件范围_um": ranges,
                    "硅全量条件厚度_um": core["硅全量完整往返厚度_um"], "硅条件范围_um": ranges["硅"],
                    "分材料验证": summarize_validation(payload["逐块评分"])})
    atomic_json(RESULT / "公平对照.json", payload)
    atomic_json(RESULT / "厚度结果.json", {"问题": 3, "核心指标": core,
                "厚度条件范围_um": ranges, "运行状态": payload["状态"],
                "范围构造": "两模型全量、两折、训练目标10%近优候选及已算扰动的联合包络；无概率含义",
                "实际用时秒": payload["实际用时秒"]})
    atomic_json(RESULT / "结果声明_问题3.json", {"问题": 3, "核心指标": core,
        "口径说明": {"估计器版本": VERSION, "原始输入哈希": payload["输入哈希"],
                     "输入": "四个真实XLSX；无合成观测进入任何核心指标",
                     "窗口_cm^-1": [1200, 3800], "全量每角点数": silicon["每角点数"],
                     "折内每角点数": 480, "上游基准哈希": payload["上游基准哈希"],
                     "执行实现SHA256": payload["实现SHA256"],
                     "独立复算": "按本文件与输入源行重算新估计器；不得沿用旧估计器的对齐结论",
                     "旧外测试": "已用于开发诊断，仅探索性复查；未移动、删除或筛选任何测试块",
                     "厚度模型": "完整往返的条件最优，不随朴素预测胜出而清空或改称朴素模型厚度"},
        "置信": "缺少已知折射率、厚度与仪器信息；条件多解，验证不足照实保留数值及失败幅度",
        "实际用时秒": payload["实际用时秒"]})
    atomic_json(RESULT / "区间覆盖.json", {"逐角逐折逐块": payload["预测区间"],
                "含义": "仅反射率预测经验覆盖；绝不是厚度覆盖", "实际用时秒": payload["实际用时秒"]})


def case_record(case, best, trace, selected="完整往返", selection=None):
    return {"材料": case["material"], "折号": case["spec"]["折号"], "每角点数": len(case["sigma"]),
            "切分": case["spec"], "训练源行": case["rows"][case["train"]],
            "训练样本序号_从零": case["train"], "训练尺度_比例": case["scales"],
            **{model: parameter_record(best[model]) for model in MODELS}, "搜索状态": trace,
            "所选预测模型": selected, "训练内选择": selection}


def main():
    t0 = time.monotonic()
    RESULT.mkdir(parents=True, exist_ok=True)
    status = {"问题": 3, "状态": "读取真实附件", "实际用时秒": 0.0}
    atomic_json(RESULT / "执行状态.json", status)
    payload = None
    try:
        inputs = load_inputs()
        cases = []
        for material in MATERIALS:
            cases.append(build_case(material, inputs["tables"], inputs["full_indices"], inputs["edges"]))
            cases.extend(build_case(material, inputs["tables"], inputs["selected"], inputs["edges"], fold)
                         for fold in inputs["folds"])
        payload = {"问题": 3, "变体": 3, "主方法": "往返衰减场反演", "估计器版本": VERSION,
                   "实现SHA256": fingerprint(Path(__file__)),
                   "状态": "基础可行估计已算，继续训练内选型", "案例": [], "灵敏度_参数扰动": [],
                   "逐块评分": [], "预测区间": [], "输入哈希": inputs["hashes"],
                   "上游基准哈希": inputs["baseline_hash"], "抽样源行": [inputs["rows"], inputs["rows"]],
                   "碳化硅正式基准": {"厚度_um": inputs["baseline"]["全量参数"]["厚度_um"],
                                      "条件范围_um": inputs["baseline"]["条件范围_um"], "是否替换": False},
                   "指标含义": {"厚度_um": "微米；完整往返条件模型的估计厚度",
                                "标准化均方根误差": "原反射率比例RMSE除以该角训练二次去趋势残差IQR",
                                "训练惩罚后目标": "双角标准化MSE加响应收缩惩罚；不能与留段RMSE混用",
                                "厚度条件范围_um": "已计算条件包络，不是置信区间",
                                "相对下降_%": "100×(1−完整误差/对照误差)；负数保留",
                                "经验覆盖率": "校准点绝对残差秩区间在原测试点的实际命中比例"},
                   "已知限制": ["两次旧外测试已参与开发，不能再称独立检验",
                                "无真实厚度，不能报告实测厚度覆盖率",
                                "不把响应增益归零时的任意厚度称为可识别点估计"]}
        working = []
        for case in cases:
            best, trace = fit_pair(case, DEFAULT_CONFIG, [np.array([5.0, 3.0, 0.0, 0.8, 0.2])],
                                   min(t0 + 90, time.monotonic() + 3), maximum=18, starts=1)
            working.append(best)
            payload["案例"].append(case_record(case, best, trace))
            atomic_json(RESULT / f"基础估计_{case['material']}_折{case['spec']['折号']}.json",
                        dict(payload["案例"][-1], 实际用时秒=time.monotonic() - t0))
        publish(payload, t0)
        for index, case in enumerate(cases):
            if time.monotonic() >= t0 + 660:
                break
            deadline = min(t0 + 660, time.monotonic() + 95)
            case_name = f"{case['material']}_折{case['spec']['折号']}"

            def save_selection(record, name=case_name):
                atomic_json(RESULT / f"训练内选择_{name}.json", dict(record, 实际用时秒=time.monotonic() - t0))

            config, selected, selection = choose_config(case, min(deadline, time.monotonic() + 60), save_selection)
            seeds = spectral_seeds(case, deadline)
            best, trace = fit_pair(case, config, seeds, deadline, maximum=150, starts=3)
            trace["响应选择完整"] = selection["选型完整"]
            trace["公平性成立"] &= selection["选型完整"]
            working[index] = best
            record = case_record(case, best, trace, selected, selection)
            record["场级数核验"] = field_checks(case, best["完整往返"], t0 + SOFT_SECONDS)
            payload["案例"][index] = record
            if case["spec"]["折号"]:
                scores, intervals = score_case(case, best, selected)
                payload["逐块评分"].extend(scores)
                payload["预测区间"].extend(intervals)
                record["双向冻结留角度"] = cross_angle(case, best, min(t0 + 690, time.monotonic() + 5))
            write_predictions(case, best)
            publish(payload, t0)
            print(f"{case_name} 完整往返厚度={best['完整往返']['parameters'][0]:.8f} um；预测选择={selected}；用时={time.monotonic()-t0:.1f}s", flush=True)
        for case, best in zip(cases, working):
            if not case["spec"]["折号"]:
                continue
            present = any(row["材料"] == case["material"] and row["折号"] == case["spec"]["折号"]
                          for row in payload["逐块评分"])
            if not present:
                scores, intervals = score_case(case, best, "完整往返")
                payload["逐块评分"].extend(scores)
                payload["预测区间"].extend(intervals)
                write_predictions(case, best)
        publish(payload, t0)
        for case, best in zip(cases, working):
            if not case["spec"]["折号"]:
                continue
            for spec in sensitivity_specs(best["完整往返"]):
                row = sensitivity_case(case, best["完整往返"], spec,
                                       min(t0 + 900, time.monotonic() + 3))
                payload["灵敏度_参数扰动"].append(row)
                publish(payload, t0)
        payload["已知厚度验证"] = known_thickness_validation(cases[1], min(t0 + 980, time.monotonic() + 45), t0)
        publish(payload, t0)
        payload["配对块重采样"] = []
        for case, best in zip(cases, working):
            if case["spec"]["折号"] or time.monotonic() >= t0 + 1040:
                continue

            def save_bootstrap(rows, material=case["material"]):
                atomic_json(RESULT / f"配对重采样_{material}.json", {"逐次记录": rows, "实际用时秒": time.monotonic() - t0})

            result = bootstrap_pair(case, best, min(t0 + 1040, time.monotonic() + 60), save_bootstrap)
            payload["配对块重采样"].append(result)
            atomic_json(RESULT / f"配对重采样_{case['material']}.json", dict(result, 实际用时秒=time.monotonic() - t0))
            publish(payload, t0)
        incomplete = (any(not row["搜索状态"].get("响应选择完整", False) for row in payload["案例"])
                      or any(row.get("状态") == "未完成" for row in payload["灵敏度_参数扰动"]))
        payload["状态"] = ("已形成答案；部分扩展检验受限" if incomplete or time.monotonic() >= t0 + SOFT_SECONDS
                           else "计算完成；是否胜出须读取逐块诊断")
        publish(payload, t0)
        status.update({"状态": "正常结束", "实际用时秒": time.monotonic() - t0,
                       "核心指标": payload["核心指标"], "不代写评审结论": True})
        atomic_json(RESULT / "执行状态.json", status)
        print(json.dumps(native(status), ensure_ascii=False, allow_nan=False), flush=True)
    except Exception as error:
        status.update({"状态": "异常结束，保留已计算产物", "原因": f"{type(error).__name__}:{error}",
                       "实际用时秒": time.monotonic() - t0})
        atomic_json(RESULT / "执行状态.json", status)
        if payload is not None and len(payload["案例"]) == 6:
            payload["状态"] = status["状态"]
            publish(payload, t0)
        print(json.dumps(status, ensure_ascii=False), flush=True)
        raise


if __name__ == "__main__":
    main()
