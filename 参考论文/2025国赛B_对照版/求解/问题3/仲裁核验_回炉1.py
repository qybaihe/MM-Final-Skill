import cmath
import hashlib
import json
import math
from pathlib import Path
import signal
import time
from xml.etree import ElementTree
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "求解/问题3/结果/仲裁核验_回炉1.json"
STARTED = time.monotonic()
BUDGET_SECONDS = 90
STATE = {"问题": 3, "状态": "进行中", "时间预算秒": BUDGET_SECONDS}


def save():
    STATE["实际用时秒"] = time.monotonic() - STARTED
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(STATE, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)


def check_budget():
    if time.monotonic() - STARTED >= BUDGET_SECONDS - 5:
        raise TimeoutError("接近预算，保留已完成核验，不声称后续步骤完成")


def alarm_handler(signum, frame):
    raise TimeoutError("90秒预算到达，保留已完成核验")


def load(relative):
    check_budget()
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def digest(relative):
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def at(document, key_path):
    current = document
    for key in key_path.split("/"):
        current = current[int(key)] if isinstance(current, list) else current[key]
    return current


def read_spectrum(number):
    namespace = {"表": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(ROOT / f"数据/附件{number}.xlsx") as archive:
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    records = []
    for row in sheet.findall("表:sheetData/表:row", namespace):
        row_number = int(row.attrib["r"])
        if row_number < 2:
            continue
        cells = {cell.attrib["r"]: cell for cell in row.findall("表:c", namespace)}
        coordinates = float(cells[f"A{row_number}"].findtext("表:v", namespaces=namespace))
        reflectance = float(cells[f"B{row_number}"].findtext("表:v", namespaces=namespace)) / 100.0
        records.append((row_number, coordinates, reflectance))
    return records


def detrend(values, observed):
    matrix = [[sum(value ** (row + column) for value in values) + (1e-12 if row == column else 0.0)
               for column in range(3)] + [sum(value ** row * target for value, target in zip(values, observed))]
              for row in range(3)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(matrix[row][column]))
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        factor = matrix[column][column]
        matrix[column] = [value / factor for value in matrix[column]]
        for row in range(3):
            if row != column:
                factor = matrix[row][column]
                matrix[row] = [left - factor * right for left, right in zip(matrix[row], matrix[column])]
    coefficients = [row[-1] for row in matrix]
    return [target - sum(coefficient * value ** power for power, coefficient in enumerate(coefficients))
            for value, target in zip(values, observed)]


def quantile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower])


def run():
    declaration = load("交接/结果声明_问题3.json")
    model = load("求解/问题3/结果/厚度结果.json")
    review = load("求解/问题3/结果/复核_回炉_轮1.json")
    upstream = load("求解/问题2/结果/基准交接.json")
    red = load("交接/红队_问题3.json")
    red_result = load("求解/问题3/红队结果/独立复算.json")
    logs = {}
    for path in sorted((ROOT / "日志").glob("执行_问题3*.log")):
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("运行状态") == "已完成数值交付":
                records.append(record)
        logs[str(path.relative_to(ROOT))] = bool(records)
    assert logs and all(logs.values()), "执行日志缺少正常结束摘要"
    STATE["产物硬门"] = {
        "结果目录存在": OUTPUT.parent.is_dir(),
        "结果JSON数": len(list(OUTPUT.parent.glob("*.json"))),
        "执行日志正常结束": logs,
        "限定": "历史主求解结束摘要；最近损耗补算另由复核_回炉_轮1.json追溯，不混称同一次运行",
    }
    sources = {
        "硅全量完整往返厚度_um": (model, "厚度结果.json", "硅/全量条件估计/全量完整往返/厚度_um"),
        "硅折1完整往返条件厚度_um": (model, "厚度结果.json", "硅/折间完整往返/0/厚度_um"),
        "硅折2完整往返条件厚度_um": (model, "厚度结果.json", "硅/折间完整往返/1/厚度_um"),
        "硅已计算条件包络下限_um": (review, "复核_回炉_轮1.json", "硅条件情景包络/下限_um"),
        "硅已计算条件包络上限_um": (review, "复核_回炉_轮1.json", "硅条件情景包络/上限_um"),
        "碳化硅正式基准厚度_um": (model, "厚度结果.json", "碳化硅/问题2正式基准厚度_um"),
        "碳化硅正式条件范围下限_um": (model, "厚度结果.json", "碳化硅/问题2正式条件范围_um/0"),
        "碳化硅正式条件范围上限_um": (model, "厚度结果.json", "碳化硅/问题2正式条件范围_um/1"),
    }
    assert set(sources) == set(declaration["核心指标"]) == set(red["复算指标"])
    differences = {entry["指标"]: entry for entry in red["分歧明细"]}
    STATE["逐键来源核对"] = {}
    for metric, (document, filename, key_path) in sources.items():
        value = at(document, key_path)
        assert isinstance(value, (int, float)) and math.isfinite(value) and value > 0
        assert value == declaration["核心指标"][metric] == differences[metric]["建模值"]
        assert red["复算指标"][metric] is None and red_result["复算指标"][metric] is None
        assert differences[metric]["复算值"] is None and differences[metric]["相对差"] is None
        STATE["逐键来源核对"][metric] = {"数值": value, "来源文件": "求解/问题3/结果/" + filename,
                                           "来源键": key_path, "声明一致": True, "复算值": None, "相对差": None}
    hash_checks = {}
    for name, relative in [("题面契约", "交接/题面契约.json"), ("数据档案", "交接/数据档案.json"),
                           ("结果声明", "交接/结果声明_问题3.json")]:
        checksum = digest(relative)
        assert checksum == red_result["输入文件摘要"][name]
        hash_checks[name] = {"当前摘要": checksum, "与红队输入一致": True}
    for number in range(1, 5):
        filename = f"附件{number}.xlsx"
        checksum = digest("数据/" + filename)
        assert checksum == model["输入哈希"][filename]
        assert checksum == red_result["原始数据核验"][f"附件{number}"]["文件摘要"]
        hash_checks[filename] = {"当前摘要": checksum, "双方输入一致": True}
    STATE["输入摘要核对"] = hash_checks
    full = model["硅"]["全量条件估计"]["全量完整往返"]
    scene_rows = [row for row in model["灵敏度_参数扰动"] if row["材料"] == "硅"]
    values = [full["厚度_um"]] + [row["厚度_um"] for row in model["硅"]["折间完整往返"]]
    values += [row["扰动后厚度_um"] for row in scene_rows]
    envelope = review["硅条件情景包络"]
    assert len(scene_rows) == 28 and len(values) == envelope["数值数"] == 31
    assert min(values) == envelope["下限_um"] and max(values) == envelope["上限_um"]
    STATE["硅包络重聚合"] = {"情景数": len(scene_rows), "全部数值数": len(values), "下限_um": min(values),
                                 "上限_um": max(values), "上端情景": [{"折号": row["折号"], "情景": row["情景"]}
                                 for row in scene_rows if row["扰动后厚度_um"] == max(values)],
                                 "限定": "只核现有31个真实结果的聚合；不是独立反演或31情景重新优化"}
    silicon_carbide = model["碳化硅"]
    assert not silicon_carbide["条件分支是否替换"]
    assert silicon_carbide["问题2正式基准厚度_um"] == upstream["全量参数"]["厚度_um"]
    assert silicon_carbide["问题2正式条件范围_um"] == upstream["条件范围_um"]
    STATE["碳化硅承接"] = {"厚度_um": upstream["全量参数"]["厚度_um"], "条件范围_um": upstream["条件范围_um"],
                                "三项承接一致": True, "限定": "核对上游正式值承接，不是独立生成上游代表解及范围"}
    save()
    spectra = {}
    for number in range(1, 5):
        check_budget()
        rows = read_spectrum(number)
        selected = [row for row in rows if 1200.0 <= row[1] <= 3800.0]
        assert len(rows) == 7469 and len(selected) == 5392
        spectra[number] = selected
        selected_rows = {row[0] for row in selected}
        assert all(source_row in selected_rows for source_row in upstream["抽样源行"][(number - 1) % 2])
    STATE["原始点集核对"] = {"四附件每角窗口点数": {f"附件{number}": len(rows) for number, rows in spectra.items()},
                                  "源行每角点数": [len(rows) for rows in upstream["抽样源行"]], "源行均在原窗口": True,
                                  "正式两折": upstream["折分"], "保护带来源值_cm^-1": upstream["训练保护带_cm^-1"]}
    losses = []
    for number, calibration in zip((3, 4), full["各角度校准"]):
        check_budget()
        coordinates = [row[1] for row in spectra[number]]
        observed = [row[2] for row in spectra[number]]
        standardized = [(coordinate - 2500.0) / 1300.0 for coordinate in coordinates]
        detrended = detrend(standardized, observed)
        scale = max(quantile(detrended, 0.75) - quantile(detrended, 0.25), 0.0001)
        angle = math.radians(calibration["入射角_度"])
        squared_errors = []
        for coordinate, standard, target in zip(coordinates, standardized, observed):
            film_index = full["参考折射率"] + full["色散系数"] * ((2000.0 / coordinate) ** 2 - 1.0)
            substrate_index = film_index + full["衬底折射率对比"]
            normal_air = math.cos(angle)
            normal_film = math.sqrt(film_index ** 2 - math.sin(angle) ** 2)
            normal_substrate = math.sqrt(substrate_index ** 2 - math.sin(angle) ** 2)
            propagation = cmath.exp(-full["有效往返损耗"] * film_index / normal_film
                                     + 4j * math.pi * full["厚度_um"] * coordinate * normal_film / 10000.0)
            intensity = 0.0
            for air, film, substrate in [(normal_air, normal_film, normal_substrate),
                                         (1.0 / normal_air, film_index ** 2 / normal_film, substrate_index ** 2 / normal_substrate)]:
                front = (air - film) / (air + film)
                rear = (film - substrate) / (film + substrate)
                field = front + (1.0 - front ** 2) * rear * propagation / (1.0 + front * rear * propagation)
                intensity += 0.5 * abs(field) ** 2
            gains = calibration["一次幅值端点"]
            predicted = sum(coefficient * standard ** power for power, coefficient in enumerate(calibration["二次基线系数"]))
            predicted += (gains[0] * (1.0 - standard) + gains[1] * (1.0 + standard)) / 2.0 * intensity
            squared_errors.append((target - predicted) ** 2)
        losses.append(sum(squared_errors) / len(squared_errors) / scale ** 2)
    recomputed = sum(losses) / 2.0
    error = abs(recomputed - full["训练标准化损失"])
    assert error < 1e-9, "公开参数与原始附件的全量正向损失不一致"
    STATE["硅全量正向核验"] = {"原结果训练标准化损失": full["训练标准化损失"], "复核值": recomputed,
                                    "绝对差": error, "各角损失": losses,
                                    "限定": "独立写出正向式，使用建模已估参数与校准系数；不是盲算厚度，不计为红队核心对齐"}
    STATE["结论"] = "8个真实核心值来源一致；0个同口径独立数值可比；输入交付缺项仍须修改，仲裁不代表五项总验收通过"
    STATE["状态"] = "核验完成"


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, alarm_handler)
    signal.alarm(BUDGET_SECONDS)
    try:
        run()
    except Exception as error:
        STATE["状态"] = "核验未完成"
        STATE["异常"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        signal.alarm(0)
        save()
        print(json.dumps({"状态": STATE["状态"], "真实数值已核对数": len(STATE.get("逐键来源核对", {})),
                          "实际用时秒": STATE["实际用时秒"], "结果": str(OUTPUT.relative_to(ROOT))}, ensure_ascii=False))
