import time

STARTED = time.monotonic()
BUDGET_SECONDS = 120

import hashlib
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import statistics
import xml.etree.ElementTree as ElementTree
import zipfile


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "求解/问题2/原型结果/锦标赛核验.json"
METRIC = "连续留段标准化均方根误差"
REPORT = {
    "问题": 2,
    "性质": "只读复核已有原型预测，补算二次趋势诊断基线，不重拟合三条主方法",
    "时间上限秒": BUDGET_SECONDS,
    "状态": "核验中",
    "路线": [],
}


def save_report():
    REPORT["核验用时秒"] = time.monotonic() - STARTED
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, OUTPUT)


def check_budget(*unused):
    if time.monotonic() - STARTED >= BUDGET_SECONDS or unused:
        raise TimeoutError("核验达到时间上限，保留已完成项目，不认定未核对路线合格")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def close(left, right):
    return math.isclose(float(left), float(right), rel_tol=1e-10, abs_tol=1e-12)


def field(record, *names):
    for name in names:
        if name in record:
            return record[name]
    raise KeyError("/".join(names))


def quantile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower])


def quadratic_fit(coordinates, observed):
    matrix = [[sum(value ** (row + column) for value in coordinates) for column in range(3)]
              + [sum(observation * value ** row for value, observation in zip(coordinates, observed))]
              for row in range(3)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(matrix[row][column]))
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        divisor = matrix[column][column]
        require(abs(divisor) > 1e-12, "二次基线矩阵奇异")
        matrix[column] = [value / divisor for value in matrix[column]]
        for row in range(3):
            if row != column:
                factor = matrix[row][column]
                matrix[row] = [value - factor * pivot_value for value, pivot_value in zip(matrix[row], matrix[column])]
    return [matrix[row][-1] for row in range(3)]


def evaluate_quadratic(coefficients, coordinate):
    return sum(coefficient * coordinate ** power for power, coefficient in enumerate(coefficients))


def read_source(filename):
    check_budget()
    source = ROOT / "数据" / filename
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    namespace = {"表": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(source) as workbook:
        sheet = ElementTree.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
    records = []
    for row in sheet.findall("表:sheetData/表:row", namespace):
        row_number = int(row.attrib["r"])
        if row_number == 1:
            continue
        cells = {}
        for cell in row.findall("表:c", namespace):
            reference = cell.attrib["r"]
            if reference in (f"A{row_number}", f"B{row_number}"):
                value = cell.find("表:v", namespace)
                if value is not None:
                    cells[reference[0]] = float(value.text)
        require(set(cells) == {"A", "B"}, f"{filename}第{row_number}行缺数值")
        records.append((cells["A"], cells["B"] / 100, row_number))
    records.sort()
    window = [record for record in records if 1200 <= record[0] <= 3800]
    selected = [window[index * (len(window) - 1) // 479] for index in range(480)]
    return digest, selected


def make_reference(folds, sources):
    coordinates = [record[0] for record in sources["附件1.xlsx"][1]]
    reference = {}
    for fold in folds:
        training = set(fold["训练块"])
        calibration = set(fold["校准块"])
        testing = set(fold["测试块"])
        require(not (training & calibration or training & testing or calibration & testing), "三类块不互斥")
        require(training | calibration | testing == set(range(1, 13)), "块覆盖不完整")
        guard_edges = [(coordinates[boundary - 1] + coordinates[boundary]) / 2
                       for boundary in range(40, 480, 40)
                       if (boundary // 40 in training) != (boundary // 40 + 1 in training)]
        indices = [index for index, coordinate in enumerate(coordinates)
                   if index // 40 + 1 in training and all(abs(coordinate - edge) >= 20 for edge in guard_edges)]
        center = (coordinates[indices[0]] + coordinates[indices[-1]]) / 2
        half_span = (coordinates[indices[-1]] - coordinates[indices[0]]) / 2
        scaled = [(coordinate - center) / half_span for coordinate in coordinates]
        for filename, angle in (("附件1.xlsx", 10), ("附件2.xlsx", 15)):
            values = [record[1] for record in sources[filename][1]]
            coefficients = quadratic_fit([scaled[index] for index in indices], [values[index] for index in indices])
            predictions = [evaluate_quadratic(coefficients, coordinate) for coordinate in scaled]
            residuals = [values[index] - predictions[index] for index in indices]
            scale = max(quantile(residuals, 0.75) - quantile(residuals, 0.25), 0.0001)
            for block in fold["测试块"]:
                positions = list(range((block - 1) * 40, block * 40))
                rmse = math.sqrt(statistics.mean((predictions[index] - values[index]) ** 2 for index in positions))
                reference[(fold["折号"], angle, block)] = {
                    "训练点数": len(indices),
                    "训练尺度": scale,
                    "波数": [coordinates[index] for index in positions],
                    "观测": [values[index] for index in positions],
                    "二次趋势标准化均方根误差": rmse / scale,
                }
    return reference


def audit_route(number, folds, sources, reference):
    check_budget()
    result_path = f"求解/问题2/原型结果/路线{number}.json"
    result = json.loads((ROOT / result_path).read_text(encoding="utf-8"))
    log = json.loads((ROOT / f"日志/跑原型_问2_{number}.log").read_text(encoding="utf-8"))
    marker = (ROOT / f"日志/跑原型_问2_{number}.done").read_text(encoding="utf-8").strip()
    require(marker == "rc=0", "原型退出码非零")
    require(not result["失败原因"], f"原型声明失败：{result['失败原因']}")
    actual_folds = [{key: fold[key] for key in ("折号", "训练块", "校准块", "测试块")} for fold in result["折分"]]
    require(actual_folds == folds, "折分不符合共同协议")
    score = result["核心指标"][METRIC]
    require(math.isfinite(score) and score >= 0, "主指标非有限非负数")
    require(close(score, result["主指标值"]), "主指标重复字段不一致")
    require(close(score, log["核心指标"][METRIC]), "结果与执行日志指标不符")
    require(close(result["实际用时秒"], log["实际用时秒"]), "结果与执行日志耗时不符")
    require(0 < result["实际用时秒"] <= 175, "原型未在预定时限内完成")
    for source in result["输入哈希"]:
        require(source["哈希"] == sources[field(source, "源附件", "附件")][0], "源附件哈希不符")
    sample_index = result["样本索引"]
    require(len(sample_index) == 480, "样本索引数量不符")
    for index, sample in enumerate(sample_index):
        for filename, source_key in (("附件1.xlsx", "原始行号_附件一"), ("附件2.xlsx", "原始行号_附件二")):
            actual = sources[filename][1][index]
            require(close(sample["波数_cm^-1"], actual[0]) and sample[source_key] == actual[2], "抽样坐标或源行不符")
    observed_keys = set()
    blocks = []
    total_hits = 0
    total_points = 0
    prediction_extrema = []
    for block in result["分块预测与残差"]:
        check_budget()
        key = (block["折号"], block["角度_度"], block["测试块"])
        require(key not in observed_keys and key in reference, "测试块重复或未知")
        observed_keys.add(key)
        expected = reference[key]
        observed = field(block, "观测反射率_比例", "实测反射率_比例")
        predictions = block["预测反射率_比例"]
        scale = field(block, "训练归一化尺度_比例", "训练尺度_反射率比例", "训练尺度_比例")
        lower = field(block, "预测下界_比例", "区间下限_反射率比例")
        upper = field(block, "预测上界_比例", "区间上限_反射率比例")
        require(len(observed) == len(predictions) == len(lower) == len(upper) == 40, "测试向量长度不符")
        require(all(math.isfinite(value) for values in (observed, predictions, lower, upper) for value in values), "测试向量存在非有限数")
        require(all(close(left, right) for left, right in zip(observed, expected["观测"])), "观测值不符原附件百分数换算")
        require(block["波数_cm^-1"] == expected["波数"], "测试坐标不符")
        require(close(scale, expected["训练尺度"]), "训练尺度不符独立二次基线复算")
        rmse = math.sqrt(statistics.mean((prediction - actual) ** 2 for prediction, actual in zip(predictions, observed)))
        block_score = rmse / scale
        require(close(block_score, block["标准化均方根误差"]), "逐块主分不符独立复算")
        hits = sum(low <= actual <= high for low, actual, high in zip(lower, observed, upper))
        require(hits == block["覆盖点数"] and close(hits / 40, block["经验覆盖率"]), "覆盖统计不符")
        total_hits += hits
        total_points += 40
        prediction_extrema.extend((min(predictions), max(predictions)))
        blocks.append({"折号": key[0], "角度_度": key[1], "测试块": key[2], "复算标准化均方根误差": block_score,
                       "复算训练尺度_比例": scale, "训练点数": expected["训练点数"],
                       "二次趋势标准化均方根误差": expected["二次趋势标准化均方根误差"]})
    require(observed_keys == set(reference), "未覆盖全部测试块")
    recomputed = statistics.mean(block["复算标准化均方根误差"] for block in blocks)
    require(close(recomputed, score), "主指标并非八块等权平均")
    coverage = total_hits / total_points
    require(close(coverage, field(result["经验覆盖率"], "测试经验覆盖率", "点数加权覆盖率")), "覆盖汇总不符")
    thicknesses = [field(record, "厚度_um", "条件厚度_um") for record in result["厚度及单位"]]
    require(len(thicknesses) == 2 and all(math.isfinite(value) and value > 0 for value in thicknesses), "分折厚度不完整或非有限正数")
    naive = statistics.mean(block["二次趋势标准化均方根误差"] for block in blocks)
    return {
        "名称": result["路线名"], "来源": result_path, "原型退出码": 0, "核验状态": "原型数值复核通过，非正式正确性验收",
        "原报主指标": score, "复算主指标": recomputed, "主指标绝对偏差": abs(recomputed - score),
        "用时秒": result["实际用时秒"], "输入与抽样坐标一致": True, "测试块数": len(blocks), "测试点次数": total_points,
        "分折主指标": [statistics.mean(block["复算标准化均方根误差"] for block in blocks if block["折号"] == number) for number in (1, 2)],
        "分角度主指标": [statistics.mean(block["复算标准化均方根误差"] for block in blocks if block["角度_度"] == angle) for angle in (10, 15)],
        "分折条件厚度_微米": thicknesses, "双折厚度相对极差_百分比": 100 * (max(thicknesses) - min(thicknesses)) / statistics.mean(thicknesses),
        "测试预测反射率范围_比例": [min(prediction_extrema), max(prediction_extrema)], "复算覆盖率": coverage,
        "二次趋势诊断基线主指标": naive, "相对二次趋势诊断基线改善_百分比": 100 * (naive - score) / naive,
        "数值来源": {"原报主指标": "核心指标/" + METRIC, "用时秒": "实际用时秒", "分折条件厚度_微米": "厚度及单位",
                     "复算覆盖率": "分块预测与残差/*/预测上下界及观测", "复算主指标": "分块预测与残差/*/预测反射率_比例及观测、训练尺度"},
        "分块": blocks,
    }


def append_experiments():
    path = ROOT / "交接/实验记录.json"
    entries = [{
        "类别": "流程事件", "问题": 2,
        "尝试": "核对锦标赛原型产物、运行结束标记并执行独立复算。",
        "现象": "三份原型结果存在且退出码均为0；正式计划、建模笔记及结果目录尚未生成。最初中文键查询漏加引号导致语法错误，改为显式键后读取成功；首次复算又把增强折分字段和附件字段别名误判异常，按共有键与别名规范化后全部复算通过，三个主指标绝对偏差均为0。",
        "决定": "遵照先原型裁决后正式规划的任务边界；不将读取器兼容错误或未到阶段的正式产物缺失当成原型失败，不签发正式解读PASS。",
        "依据": "交接/路线侦察.json：交给下一步的裁决要求/结果后再规划；日志/跑原型_问2_1.done、跑原型_问2_2.done、跑原型_问2_3.done；日志/核验_锦标赛_问2.log；求解/问题2/原型结果/锦标赛核验.json",
    }, {
        "类别": "科学尝试", "问题": 2,
        "尝试": "在共同双角连续留段上独立重算强度预测误差，并用相同训练点、保护带和归一化尺度补算只含二次趋势的对照。",
        "现象": "变投影、峰序匹配、相位回归的平均误差依次为1.1716972867113729、1.2354246858438795、1.2201909148889387；二次趋势对照为1.4403713129480724。变投影较该对照降低18.65310866868019%，但第一折误差0.7662076556966146高于峰序的0.7319395303599306，名义90%的反射率区间仅覆盖49.0625%。",
        "决定": "以变投影为主线，保留峰序的局部优势及相位方法的独立诊断；不把平均优势写成所有波段均占优，也不把反射率预测覆盖解释为厚度覆盖。",
        "依据": "求解结果:路线/0/复算主指标；求解结果:路线/1/复算主指标；求解结果:路线/2/复算主指标；求解结果:路线/0/二次趋势诊断基线主指标；求解结果:路线/0/分折主指标；求解结果:路线/1/分折主指标；求解结果:路线/0/复算覆盖率",
    }]
    with (ROOT / "交接/.实验记录.lock").open("a", encoding="utf-8") as lock:
        lock_deadline = min(STARTED + BUDGET_SECONDS - 2, time.monotonic() + 5)
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= lock_deadline:
                    raise TimeoutError("实验记录锁等待达到上限")
                time.sleep(0.02)
        records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        require(isinstance(records, list), "实验记录顶层必须为数组")
        if not any(record.get("尝试") == entries[0]["尝试"] for record in records):
            records.append(entries[0])
        records.append(entries[1])
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)


def main():
    signal.signal(signal.SIGALRM, check_budget)
    signal.alarm(BUDGET_SECONDS)
    try:
        scout = json.loads((ROOT / "交接/路线侦察.json").read_text(encoding="utf-8"))
        folds = scout["共同原型协议"]["实测小样"]["折分"]
        sources = {filename: read_source(filename) for filename in ("附件1.xlsx", "附件2.xlsx")}
        archive = json.loads((ROOT / "交接/数据档案.json").read_text(encoding="utf-8"))
        hashes = {entry["文件名"]: entry["文件哈希"] for entry in archive["文件档案"]}
        require(all(digest == hashes[filename] for filename, (digest, unused) in sources.items()), "源附件与数据档案哈希不符")
        reference = make_reference(folds, sources)
        REPORT["基线口径"] = "额外诊断：仅用同折保护带后的训练点拟合各角二次趋势，无干涉项；相同八个测试块及训练残差四分位距。此基线不替代尚未生成的正式计划中naive基线，也不加入原定三路线排名。"
        save_report()
        for number in (1, 2, 3):
            try:
                REPORT["路线"].append(audit_route(number, folds, sources, reference))
            except (ValueError, KeyError, OSError) as error:
                REPORT["路线"].append({"路线序号": number, "核验状态": "失败", "实测指标": None, "失败原因": str(error)})
            save_report()
        valid = [route for route in REPORT["路线"] if "复算主指标" in route]
        ranking = sorted(valid, key=lambda route: route["复算主指标"])
        REPORT["实测排序"] = [route["名称"] for route in ranking]
        if ranking:
            winner = ranking[0]
            REPORT["优胜相对其余路线"] = [{"对照": route["名称"], "误差绝对减少": route["复算主指标"] - winner["复算主指标"],
                                      "误差相对减少_百分比": 100 * (route["复算主指标"] - winner["复算主指标"]) / route["复算主指标"],
                                      "八块胜出数": sum(first["复算标准化均方根误差"] < second["复算标准化均方根误差"]
                                                       for first, second in zip(sorted(winner["分块"], key=lambda block: (block["折号"], block["角度_度"], block["测试块"])),
                                                                                sorted(route["分块"], key=lambda block: (block["折号"], block["角度_度"], block["测试块"]))))}
                                     for route in ranking[1:]]
        REPORT["状态"] = "完成" if len(valid) == 3 else "完成，部分路线失败，失败路线不参与排序"
        if REPORT["状态"] == "完成":
            append_experiments()
    except Exception as error:
        REPORT["状态"] = "未完成"
        REPORT["失败原因"] = f"{type(error).__name__}: {error}"
    finally:
        signal.alarm(0)
        save_report()
    print(json.dumps({"状态": REPORT["状态"], "实测排序": REPORT.get("实测排序", []),
                      "核验用时秒": REPORT["核验用时秒"], "失败原因": REPORT.get("失败原因"),
                      "路线": [{key: value for key, value in route.items() if key != "分块"} for route in REPORT["路线"]],
                      "优胜相对其余路线": REPORT.get("优胜相对其余路线", [])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
