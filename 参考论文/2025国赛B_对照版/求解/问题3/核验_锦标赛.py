import cmath
import hashlib
import json
import math
import signal
import time
import xml.etree.ElementTree as element_tree
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "求解/问题3/原型结果/锦标赛核验.json"
STARTED = time.monotonic()
TIME_BUDGET = 90
METRIC = "连续留段标准化均方根误差"
MATERIAL_FILES = {("硅", 10): "附件3.xlsx", ("硅", 15): "附件4.xlsx",
                  ("碳化硅", 10): "附件1.xlsx", ("碳化硅", 15): "附件2.xlsx"}
REPORT = {"问题": 3, "状态": "核验进行中", "时间预算秒": TIME_BUDGET,
          "口径": "仅独立复算已冻结预测，不重拟合，不改原型，不将原型选型集当最终独立测试集。",
          "路线核验": [], "失败原因": []}


def save_report():
    REPORT["核验用时秒"] = time.monotonic() - STARTED
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)


def budget_check():
    if time.monotonic() - STARTED >= TIME_BUDGET - 3:
        raise TimeoutError("接近核验时间上限，保存已核验路线")


def timeout_handler(signum, frame):
    raise TimeoutError("核验硬时限触发，保存当前结果")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def quantile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def mean(values):
    return math.fsum(values) / len(values)


def root_mean_square(values):
    return math.sqrt(mean([value * value for value in values]))


def read_raw(path):
    namespace = {"sheet": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        xml_root = element_tree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    records = {}
    for row in xml_root.findall("sheet:sheetData/sheet:row", namespace):
        cells = {}
        for cell in row.findall("sheet:c", namespace):
            value = cell.find("sheet:v", namespace)
            column = "".join(character for character in cell.attrib["r"] if character.isalpha())
            if value is not None and cell.attrib.get("t", "n") == "n":
                cells[column] = float(value.text)
        if "A" in cells and "B" in cells:
            records[int(row.attrib["r"])] = (cells["A"], cells["B"] / 100)
    return records


def training_scale(coordinates, values, specification):
    training = set(specification["训练块"])
    calibration = set(specification["校准块"])
    testing = set(specification["测试块"])
    require(not (training & calibration or training & testing or calibration & testing), "训练校准测试块交叠")
    require(training | calibration | testing == set(range(1, 13)), "切分未覆盖全部块")
    edges = [(coordinates[index - 1] + coordinates[index]) / 2 for index in range(40, 480, 40)]
    guards = [edge for index, edge in enumerate(edges, 1) if (index in training) != (index + 1 in training)]
    selected = [index for index, coordinate in enumerate(coordinates)
                if index // 40 + 1 in training and all(abs(coordinate - edge) >= 20 for edge in guards)]
    scaled = [(coordinates[index] - 2500) / 1300 for index in selected]
    observed = [values[index] for index in selected]
    columns = [[coordinate ** power for coordinate in scaled] for power in range(3)]
    matrix = [[math.fsum(left * right for left, right in zip(columns[row], columns[column]))
               for column in range(3)] + [math.fsum(left * right for left, right in zip(columns[row], observed))]
              for row in range(3)]
    for pivot in range(3):
        pivot_row = max(range(pivot, 3), key=lambda row: abs(matrix[row][pivot]))
        matrix[pivot], matrix[pivot_row] = matrix[pivot_row], matrix[pivot]
        divisor = matrix[pivot][pivot]
        require(abs(divisor) > 1e-12, "独立二次基线求解退化")
        matrix[pivot] = [value / divisor for value in matrix[pivot]]
        for row in range(3):
            if row != pivot:
                multiplier = matrix[row][pivot]
                matrix[row] = [left - multiplier * right for left, right in zip(matrix[row], matrix[pivot])]
    coefficients = [row[-1] for row in matrix]
    residuals = [value - math.fsum(coefficients[power] * coordinate ** power for power in range(3))
                 for coordinate, value in zip(scaled, observed)]
    return max(quantile(residuals, 0.75) - quantile(residuals, 0.25), 0.0001), selected


def physical_prediction(parameters, coordinate, angle, complete):
    reference = parameters["参考折射率"] + parameters["色散系数"] * ((2000 / coordinate) ** 2 - 1)
    substrate = reference + parameters["衬底折射率对比"]
    sine_squared = math.sin(math.radians(angle)) ** 2
    air_normal = math.cos(math.radians(angle))
    film_normal = math.sqrt(reference * reference - sine_squared)
    substrate_normal = math.sqrt(substrate * substrate - sine_squared)
    propagation = cmath.exp(-parameters["有效往返损耗"] * reference / film_normal
                            + 4j * math.pi * parameters["厚度_微米"] * coordinate * film_normal / 10000)
    intensities = []
    for air_admittance, film_admittance, substrate_admittance in (
            (air_normal, film_normal, substrate_normal),
            (1 / air_normal, reference * reference / film_normal, substrate * substrate / substrate_normal)):
        surface = (air_admittance - film_admittance) / (air_admittance + film_admittance)
        bottom = (film_admittance - substrate_admittance) / (film_admittance + substrate_admittance)
        if complete:
            field = (surface + bottom * propagation) / (1 + surface * bottom * propagation)
        else:
            field = surface + (1 - surface * surface) * bottom * propagation
        intensities.append(abs(field) ** 2)
    physical = mean(intensities)
    require(0 <= physical <= 1 + 1e-12 and abs(propagation) <= 1 + 1e-12, "物理前向存在非物理增益")
    calibration = next(item for item in parameters["各角度校准"] if item["入射角_度"] == angle)
    coordinate_scaled = (coordinate - 2500) / 1300
    baseline = math.fsum(coefficient * coordinate_scaled ** power
                         for power, coefficient in enumerate(calibration["二次基线系数"]))
    left_gain, right_gain = calibration["一次幅值端点"]
    envelope = (left_gain * (1 - coordinate_scaled) + right_gain * (1 + coordinate_scaled)) / 2
    return baseline + envelope * physical


def summarize_blocks(blocks):
    result = {"标准化误差": mean([block["标准化误差"] for block in blocks]),
              "块均方根误差均值_百分点": mean([block["均方根误差_百分点"] for block in blocks])}
    if all(block["两束标准化误差"] is not None for block in blocks):
        baseline = mean([block["两束标准化误差"] for block in blocks])
        result.update({"两束标准化误差": baseline, "两束减主模型误差": baseline - result["标准化误差"],
                       "较本路线两束改善百分比": 100 * (baseline - result["标准化误差"]) / baseline})
    return result


def audit_route(number, result, raw, hashes, canonical_rows, canonical_coordinates, specifications):
    budget_check()
    filename = ROOT / f"求解/问题3/原型结果/路线{number}.json"
    logged = json.loads((ROOT / f"日志/跑原型_问3_{number}.log").read_text(encoding="utf-8"))
    require((ROOT / f"日志/跑原型_问3_{number}.done").read_text().strip() == "rc=0", "原型非正常退出")
    require(result["主指标值"] == logged.get("核心指标", logged.get("核心指标键值", {}))[METRIC], "日志指标与结果不一致")
    input_hashes = result["输入哈希"]
    if isinstance(input_hashes, list):
        input_hashes = {item["附件"]: item["哈希"] for item in input_hashes}
    require(input_hashes == hashes, "原型输入哈希与实盘不一致")
    require(result["折分"] == specifications, "路线切分不同")
    for attachment, expected_rows in canonical_rows.items():
        if isinstance(result["样本索引"], list):
            references = [item["各附件原始行号"][attachment] for item in result["样本索引"]]
        else:
            references = [item["原始行号"] for item in result["样本索引"][Path(attachment).stem]]
        require(references == expected_rows, "原始行号与共同比较组不同")
    blocks = result["分块预测与残差"]
    expected_keys = {(material, spec["折号"], angle, block) for material in ("硅", "碳化硅")
                     for spec in specifications for angle in (10, 15) for block in spec["测试块"]}
    require(len(blocks) == 16 and {(item["材料"], item["折号"], item["入射角_度"], item["测试块"])
                                 for item in blocks} == expected_keys, "测试块覆盖不完整或重复")
    audited = []
    maximum_scale_error = maximum_forward_error = maximum_residual_error = 0.0
    all_predictions = []
    all_observed = []
    coverage_hits = 0
    for block in blocks:
        material, angle, fold = block["材料"], block["入射角_度"], block["折号"]
        attachment = MATERIAL_FILES[(material, angle)]
        sample_values = [raw[attachment][row][1] for row in canonical_rows[attachment]]
        scale, training_indices = training_scale(canonical_coordinates, sample_values, specifications[fold - 1])
        stored_scale = block.get("训练尺度_比例", block.get("训练尺度_反射率比例"))
        maximum_scale_error = max(maximum_scale_error, abs(scale - stored_scale))
        require(abs(scale - stored_scale) < 1e-10, "训练尺度与独立重算不同")
        selected = list(range((block["测试块"] - 1) * 40, block["测试块"] * 40))
        require(block["波数_cm^-1"] == [canonical_coordinates[index] for index in selected], "测试坐标不一致")
        observed = [sample_values[index] for index in selected]
        require(block["实测反射率_比例"] == observed, "结果观测值与原始附件不一致")
        predicted = block.get("完整预测反射率_比例", block.get("预测反射率_比例"))
        require(len(predicted) == 40 and all(math.isfinite(value) for value in predicted), "预测缺失或非有限")
        residuals = [estimate - value for estimate, value in zip(predicted, observed)]
        reported_residuals = block.get("完整残差_比例", block.get("预测减实测_比例", block.get("残差_比例")))
        maximum_residual_error = max(maximum_residual_error, max(abs(left - right) for left, right in zip(residuals, reported_residuals)))
        require(maximum_residual_error < 1e-12, "残差不对应预测减实测")
        score = root_mean_square(residuals) / scale
        require(abs(score - block.get("完整标准化均方根误差", block.get("标准化均方根误差"))) < 1e-9, "分块误差不一致")
        baseline_predictions = block.get("两束预测反射率_比例", block.get("两束参照预测反射率_比例"))
        baseline_score = None if baseline_predictions is None else root_mean_square([estimate - value for estimate, value in zip(baseline_predictions, observed)]) / scale
        lower = block.get("区间下界_比例", block.get("预测下限_比例"))
        upper = block.get("区间上界_比例", block.get("预测上限_比例"))
        hits = sum(left <= value <= right for left, value, right in zip(lower, observed, upper))
        require(hits == block["覆盖点数"], "区间覆盖点数不一致")
        coverage_hits += hits
        if number == 1:
            parameters = next(item for item in result["厚度及单位"] if item["材料"] == material and item["折号"] == fold)
            for model_name, complete, estimates in (("完整往返", True, predicted), ("两束", False, baseline_predictions)):
                differences = [abs(physical_prediction(parameters[model_name], coordinate, angle, complete) - estimate)
                               for coordinate, estimate in zip(block["波数_cm^-1"], estimates)]
                maximum_forward_error = max(maximum_forward_error, max(differences))
            require(maximum_forward_error < 1e-10, "冻结物理参数不能复现预测")
        audited.append({"材料": material, "折号": fold, "入射角_度": angle, "测试块": block["测试块"],
                        "训练点数": len(training_indices), "标准化误差": score,
                        "均方根误差_百分点": 100 * root_mean_square(residuals), "两束标准化误差": baseline_score})
        all_predictions.extend(predicted)
        all_observed.extend(observed)
    summary = summarize_blocks(audited)
    require(abs(summary["标准化误差"] - result["主指标值"]) < 1e-9, "总分无法复现")
    summary.update({"路线名": result["路线名"], "原型结果来源": str(filename.relative_to(ROOT)),
                    "原型实际用时秒": result["实际用时秒"], "运行正常结束": True,
                    "原始数据及切分一致": True, "全部测试预测点数": len(all_predictions),
                    "经验覆盖率": coverage_hits / len(all_predictions), "覆盖点数": coverage_hits,
                    "观测范围_比例": [min(all_observed), max(all_observed)],
                    "预测范围_比例": [min(all_predictions), max(all_predictions)],
                    "预测超物理范围点数": sum(value < 0 or value > 1 for value in all_predictions),
                    "训练尺度独立重算最大差": maximum_scale_error, "残差重算最大差": maximum_residual_error,
                    "冻结参数前向最大差": maximum_forward_error if number == 1 else None,
                    "分块复算": audited,
                    "分材料": [{"材料": material, **summarize_blocks([item for item in audited if item["材料"] == material])}
                                for material in ("硅", "碳化硅")],
                    "分折": [{"折号": fold, **summarize_blocks([item for item in audited if item["折号"] == fold])}
                              for fold in (1, 2)],
                    "分材料分折": [{"材料": material, "折号": fold,
                                    **summarize_blocks([item for item in audited if item["材料"] == material and item["折号"] == fold])}
                                   for material in ("硅", "碳化硅") for fold in (1, 2)]})
    return summary


def main():
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(TIME_BUDGET)
    save_report()
    try:
        results = [json.loads((ROOT / f"求解/问题3/原型结果/路线{number}.json").read_text(encoding="utf-8")) for number in (1, 2, 3)]
        hashes = {attachment: hashlib.sha256((ROOT / "数据" / attachment).read_bytes()).hexdigest()
                  for attachment in MATERIAL_FILES.values()}
        raw = {attachment: read_raw(ROOT / "数据" / attachment) for attachment in hashes}
        canonical_rows = {attachment: [item["原始行号"] for item in results[0]["样本索引"][Path(attachment).stem]] for attachment in hashes}
        canonical_coordinates = [raw["附件3.xlsx"][row][0] for row in canonical_rows["附件3.xlsx"]]
        require(len(canonical_coordinates) == 480, "小样点数不是480")
        for attachment in hashes:
            require([raw[attachment][row][0] for row in canonical_rows[attachment]] == canonical_coordinates, "附件坐标不一致")
        for number, result in enumerate(results, 1):
            REPORT["路线核验"].append(audit_route(number, result, raw, hashes, canonical_rows, canonical_coordinates, results[0]["折分"]))
            save_report()
        winner = REPORT["路线核验"][0]
        REPORT["路线一相对优势"] = [{"对比路线": item["路线名"], "主指标相对降低百分比":
                                   100 * (item["标准化误差"] - winner["标准化误差"]) / item["标准化误差"]}
                                  for item in REPORT["路线核验"][1:]]
        REPORT["路线一条件厚度范围"] = [{"材料": material, "下界_微米": min(item["完整往返"]["厚度_微米"] for item in results[0]["厚度及单位"] if item["材料"] == material),
                                      "上界_微米": max(item["完整往返"]["厚度_微米"] for item in results[0]["厚度及单位"] if item["材料"] == material),
                                      "性质": "两折条件估计极差，非统计置信区间"} for material in ("硅", "碳化硅")]
        REPORT["路线三资格诊断"] = [{"材料": item["材料"], "折号": item["折号"],
                                    "诊断未完成项数": len(item["未完成诊断"]),
                                    "通过相位支持角度数": sum(record["覆盖完整且至少三次重复"] for record in item["训练相位支持"]),
                                    "基频抽稀混叠角度数": sum(record["基频抽稀混叠风险"] for record in item["训练相位支持"]),
                                    "连通区完整周期数": [record["训练连通区完整周期数"] for record in item["训练相位支持"]]}
                                   for item in results[2]["辅助诊断"]["形状与可辨识性"]]
        REPORT["路线一训练互补诊断"] = []
        for item in results[0]["辅助诊断"]["新增参数互补"]:
            baseline = next(record["完整往返"] for record in results[0]["厚度及单位"]
                            if record["材料"] == item["材料"] and record["折号"] == item["折号"])
            for scenario in item["训练参数互补情景"]:
                if scenario["情景"] != "厚度折射率互补方向" or not scenario["可行"]:
                    continue
                REPORT["路线一训练互补诊断"].append({
                    "材料": item["材料"], "折号": item["折号"],
                    "基准厚度_微米": baseline["厚度_微米"],
                    "扰动厚度_微米": scenario["参数"]["厚度_微米"],
                    "厚度增加百分比": 100 * (scenario["参数"]["厚度_微米"] / baseline["厚度_微米"] - 1),
                    "基准训练损失": scenario["基准训练损失"],
                    "扰动训练损失": scenario["参数"]["训练标准化均方残差"],
                    "扰动后损失相对变化百分比": 100 * (scenario["参数"]["训练标准化均方残差"] / scenario["基准训练损失"] - 1),
                    "限制": "既有训练内情景的独立汇总；厚度与折射率色散联动，仅重估基线幅值，不是重新优化厚度或留出集灵敏度检验。"})
        REPORT["选型结论"] = {
            "优胜": "往返衰减场反演",
            "所有材料分折组均优于另两路线": all(
                winner_group["标准化误差"] < other_group["标准化误差"]
                for other in REPORT["路线核验"][1:]
                for winner_group, other_group in zip(winner["分材料分折"], other["分材料分折"])),
            "主指标性质": "原型留段谱形预测误差，不是真实厚度误差",
            "两束基线性质": "各路线内部参照分别拟合，不能把各自两束总分当同一个公共naive；计划未生成。",
            "正式验收": "未进行；硅对两束的分折改善方向不一，预测覆盖不足，厚度互补显著。仍须输出带光学假设的最佳厚度与不确定性范围。"
        }
        REPORT["状态"] = "完成：三条冻结预测指标、原始数据、训练尺度及路线一前向均可复现；不代表正式厚度验收通过"
    except Exception as error:
        REPORT["状态"] = "核验中断，保留已完成证据"
        REPORT["失败原因"].append(f"{type(error).__name__}: {error}")
        raise
    finally:
        signal.alarm(0)
        save_report()
        print(json.dumps({"状态": REPORT["状态"], "完成路线数": len(REPORT["路线核验"]),
                          "核验用时秒": REPORT["核验用时秒"],
                          "摘要": [{key: item[key] for key in ("路线名", "标准化误差", "经验覆盖率", "预测超物理范围点数")}
                                   for item in REPORT["路线核验"]], "失败原因": REPORT["失败原因"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
