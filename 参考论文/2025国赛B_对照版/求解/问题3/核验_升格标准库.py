import bisect
import cmath
import csv
import hashlib
import json
import math
import time
import xml.etree.ElementTree as element_tree
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "求解/问题3/升格3/结果"


def quantile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[min(lower + 1, len(ordered) - 1)] * fraction


def solve(matrix, right):
    augmented = [list(row) + [value] for row, value in zip(matrix, right)]
    size = len(right)
    for column in range(size):
        pivot = max(range(column, size), key=lambda index: abs(augmented[index][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for index in range(size):
            if index != column:
                multiplier = augmented[index][column]
                augmented[index] = [value - multiplier * reference for value, reference in zip(augmented[index], augmented[column])]
    return [row[-1] for row in augmented]


def read_table(number):
    path = ROOT / f"数据/附件{number}.xlsx"
    namespace = {"表": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(path) as archive:
        document = element_tree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    table = {}
    for row in document.findall("表:sheetData/表:row", namespace):
        index = int(row.get("r"))
        if index == 1:
            continue
        cells = {cell.get("r"): cell for cell in row.findall("表:c", namespace)}
        values = [float(cells[f"{column}{index}"].findtext("表:v", namespaces=namespace)) for column in ("A", "B")]
        table[index] = (values[0], values[1] / 100)
    return table


def run_audit():
    deadline = time.monotonic() + 480
    fair = json.loads((SOURCE / "公平对照.json").read_text())
    tables = {number: read_table(number) for number in range(1, 5)}
    for number in tables:
        path = ROOT / f"数据/附件{number}.xlsx"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == fair["输入哈希"][path.name]
    full = sorted((index for index, values in tables[1].items() if 1200 <= values[0] <= 3800), key=lambda index: tables[1][index][0])
    sample = [full[index * (len(full) - 1) // 479] for index in range(480)]
    assert fair["抽样源行"] == [sample, sample]
    edges = [(tables[1][sample[index - 1]][0] + tables[1][sample[index]][0]) / 2 for index in range(40, 480, 40)]
    errors = {"原附件与预测表观测最大差": 0.0, "预测独立代入最大差": 0.0,
              "训练尺度独立复算最大差": 0.0, "逐块误差独立复算最大差": 0.0,
              "闭式与六十四项场最大差": 0.0, "最大往返乘子模": 0.0}
    split_rows, score_rows = [], []
    for case in fair["案例"]:
        if time.monotonic() >= deadline:
            raise TimeoutError("独立公式核验达到八分钟上限")
        material, fold = case["材料"], case["折号"]
        numbers = (3, 4) if material == "硅" else (1, 2)
        with (SOURCE / f"预测_{material}_折{fold}.csv").open(encoding="utf-8-sig") as stream:
            predictions = list(csv.DictReader(stream))
        source = sample if fold else full
        sigma = [tables[1][index][0] for index in source]
        abscissa = [(value - 2500) / 1300 for value in sigma]
        basis = [[1, value, value * value] for value in abscissa]
        blocks = [bisect.bisect_right(edges, value) + 1 for value in sigma]
        specification = case["切分"]
        guards = [edge for block, edge in enumerate(edges, 1) if (block in specification["训练块"]) != (block + 1 in specification["训练块"])]
        train = [index for index, block in enumerate(blocks) if block in specification["训练块"] and all(abs(sigma[index] - edge) >= 20 for edge in guards)]
        test = [index for index, block in enumerate(blocks) if block in specification["测试块"]]
        calibration = [index for index, block in enumerate(blocks) if block in specification["校准块"]]
        assert [source[index] for index in train] == case["训练源行"]
        assert not set(train) & (set(test) | set(calibration)) and not set(test) & set(calibration)
        inner_count = 0
        for inner in case["训练内选择"]["内层分块"]:
            training, validation = set(inner["训练源行"]), set(inner["验证源行"])
            assert training <= set(case["训练源行"]) and validation <= set(case["训练源行"])
            assert not training & validation
            inner_count += 1
        split_rows.append({"材料": material, "折号": fold, "每角原始点数": len(source), "训练点数": len(train),
                           "校准点数": len(calibration), "测试点数": len(test), "训练与留出交集": 0, "内层成员核验数": inner_count})
        for angle_index, (number, angle) in enumerate(zip(numbers, (10, 15))):
            rows = [row for row in predictions if int(row["附件"]) == number]
            assert [int(row["源行"]) for row in rows] == source
            observed = [tables[number][index][1] for index in source]
            errors["原附件与预测表观测最大差"] = max(errors["原附件与预测表观测最大差"], max(abs(value - float(row["反射率_比例"])) for value, row in zip(observed, rows)))
            matrix = [[sum(basis[index][left] * basis[index][right] for index in train) + (1e-12 if left == right else 0) for right in range(3)] for left in range(3)]
            target = [sum(basis[index][power] * observed[index] for index in train) for power in range(3)]
            coefficients = solve(matrix, target)
            residuals = [observed[index] - sum(value * coefficient for value, coefficient in zip(basis[index], coefficients)) for index in train]
            scale = max(quantile(residuals, .75) - quantile(residuals, .25), 1e-4)
            errors["训练尺度独立复算最大差"] = max(errors["训练尺度独立复算最大差"], abs(scale - case["训练尺度_比例"][angle_index]))
            for model in ["两束", "完整往返"]:
                record = case[model]
                thickness, reference, dispersion, contrast, loss = record["参数向量"]
                degree, gain_degree, penalty = record["响应设置_背景阶_增益阶_惩罚"]
                response = record["各角响应"][angle_index]
                for index, wave_number in enumerate(sigma):
                    film = reference + dispersion * ((2000 / wave_number) ** 2 - 1)
                    support = film + contrast
                    sine = math.sin(math.radians(angle))
                    normal = math.sqrt(film ** 2 - sine ** 2)
                    support_normal = math.sqrt(support ** 2 - sine ** 2)
                    propagation = cmath.exp(-loss * film / normal + 4j * math.pi * thickness * wave_number * normal / 10000)
                    intensity = 0
                    for air, layer, substrate in [(math.cos(math.radians(angle)), normal, support_normal), (1 / math.cos(math.radians(angle)), film ** 2 / normal, support ** 2 / support_normal)]:
                        surface = (air - layer) / (air + layer)
                        bottom = (layer - substrate) / (layer + substrate)
                        first = 4 * air * layer / (air + layer) ** 2 * bottom * propagation
                        ratio = -surface * bottom * propagation
                        closed = surface + first / (1 - ratio)
                        finite, term = complex(surface), first
                        for order in range(64):
                            finite += term
                            term *= ratio
                        errors["闭式与六十四项场最大差"] = max(errors["闭式与六十四项场最大差"], abs(finite - closed))
                        errors["最大往返乘子模"] = max(errors["最大往返乘子模"], abs(ratio))
                        intensity += .5 * abs(closed if model == "完整往返" else surface + first) ** 2
                    background = basis[index][:degree + 1]
                    weights = [1] if gain_degree == 0 else [(1 - abscissa[index]) / 2, (1 + abscissa[index]) / 2]
                    features = [(intensity * weight - sum(value * response["物理投影系数"][power][column] for power, value in enumerate(background))) / response["物理列尺度"][column] for column, weight in enumerate(weights)]
                    reconstructed = response["训练响应均值_比例"] + response["训练响应尺度_比例"] * (sum(value * coefficient for value, coefficient in zip(background, response["背景系数"])) + sum(value * gain for value, gain in zip(features, response["标准化非负增益"])))
                    errors["预测独立代入最大差"] = max(errors["预测独立代入最大差"], abs(reconstructed - float(rows[index][model])))
            if fold:
                for block in specification["测试块"]:
                    members = [index for index, value in enumerate(blocks) if value == block]
                    for model in ["两束", "完整往返", "训练均值", "二次趋势"]:
                        error = math.sqrt(sum((float(rows[index][model]) - observed[index]) ** 2 for index in members) / len(members)) / scale
                        saved = next(row for row in fair["逐块评分"] if row["材料"] == material and row["折号"] == fold and row["附件"] == number and row["测试块"] == block and row["模型"] == model)
                        errors["逐块误差独立复算最大差"] = max(errors["逐块误差独立复算最大差"], abs(error - saved["标准化均方根误差"]))
                        score_rows.append({"材料": material, "折号": fold, "附件": number, "测试块": block, "模型": model, "复核标准化均方根误差": error, "测试点数": len(members)})
    assert errors["预测独立代入最大差"] < 1e-10, errors
    assert errors["逐块误差独立复算最大差"] < 1e-8, errors
    assert errors["原附件与预测表观测最大差"] < 1e-14, errors
    assert errors["闭式与六十四项场最大差"] < 1e-12, errors
    assert errors["最大往返乘子模"] < 1, errors
    sensitivities = [row for row in fair["灵敏度_参数扰动"] if row["材料"] == "硅" and row["状态"] == "已重估"]
    worst = max(sensitivities, key=lambda row: abs(row["相对变化_百分数"]))
    whole = next(row for row in fair["案例"] if row["材料"] == "硅" and row["折号"] == 0)
    folds = [row["完整往返"]["厚度_um"] for row in fair["案例"] if row["材料"] == "硅" and row["折号"]]
    envelope_members = []
    for case in fair["案例"]:
        if case["材料"] != "硅":
            continue
        for model in ["两束", "完整往返"]:
            envelope_members.append(case[model]["厚度_um"])
        for branches in case["搜索状态"]["训练近优分支范围_um"].values():
            envelope_members.extend(branches["训练目标容差10%"])
    envelope_members.extend(row["扰动后厚度_um"] for row in sensitivities)
    envelope = [min(envelope_members), max(envelope_members)]
    assert envelope == fair["厚度条件范围_um"]["硅"]
    upstream = json.loads((ROOT / "求解/问题2/结果/基准交接.json").read_text())
    assert upstream["抽样点数_每角度"] == 480
    assert upstream["全量参数"]["厚度_um"] == fair["核心指标"]["碳化硅正式基准厚度_um"]
    assert upstream["条件范围_um"] == [fair["核心指标"]["碳化硅正式条件范围下限_um"], fair["核心指标"]["碳化硅正式条件范围上限_um"]]
    metrics = {"硅最坏扰动厚度相对变化_百分比": worst["相对变化_百分数"],
               "硅最坏扰动扣除控制厚度变化_um": worst["扣除控制厚度变化_um"],
               "完整往返反射率测试经验覆盖率": sum(row["经验覆盖率"] for row in fair["预测区间"] if row["模型"] == "完整往返") / 16,
               "已知厚度匹配情景条件包络包含率": fair["已知厚度验证"]["条件包络经验包含率"],
               "硅折间厚度最大最小比": max(folds) / min(folds),
               "硅全量两束厚度_um": whole["两束"]["厚度_um"],
               "硅全量完整往返对两束厚度变化_百分比": 100 * (whole["完整往返"]["厚度_um"] / whole["两束"]["厚度_um"] - 1)}
    return {"问题": 3, "核验类型": "标准库读取原附件并独立代入场与响应公式、重建切分和评分；未独立重跑参数优化",
            "误差核验": errors, "切分核验": split_rows, "逐块误差核验": score_rows, "自检指标": metrics,
            "硅条件包络重建": {"成员数": len(envelope_members), "下限_um": envelope[0], "上限_um": envelope[1], "与核心指标一致": True},
            "碳化硅承接口径": {"每角点数": 480, "完整源窗口点数": 5392, "与问题2基准一致": True},
            "最坏扰动": {key: worst[key] for key in ["折号", "情景", "基准厚度_um", "扰动后厚度_um", "相对变化_百分数", "扣除控制厚度变化_um", "配对可比"]},
            "通过数值一致性核验": True}


if __name__ == "__main__":
    report = run_audit()
    target = ROOT / "日志/升格裁决_问3核验/独立公式与评分核验.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"状态": "正常结束", "误差核验": report["误差核验"]}, ensure_ascii=False))
