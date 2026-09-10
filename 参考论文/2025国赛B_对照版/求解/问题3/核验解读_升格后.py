import csv
import json
import math
import re
import signal
import time
from pathlib import Path

import 核验_升格标准库 as standard_audit


ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "求解/问题3/结果"
STARTED = time.monotonic()
TIME_LIMIT = 180
REPORT = {"问题": 3, "状态": "核验中", "时间上限秒": TIME_LIMIT}
CACHE = {}


def load_json(relative):
    path = ROOT / relative
    if path not in CACHE:
        CACHE[path] = json.loads(path.read_text(encoding="utf-8"))
    return CACHE[path]


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def append_event(event):
    path = ROOT / "交接/实验记录.json"
    events = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    assert isinstance(events, list)
    if event not in events:
        events.append(event)
        save_json(path, events)


def checkpoint(stage):
    REPORT["阶段"] = stage
    REPORT["实际用时秒"] = time.monotonic() - STARTED
    save_json(RESULT / "解读核验_升格后.json", REPORT)
    if REPORT["实际用时秒"] >= TIME_LIMIT - 5:
        raise TimeoutError("达到核验时间上限，保留已经完成的逐项结果")


def stop_for_time(signum, frame):
    raise TimeoutError("达到核验硬时间上限，保留当前核验结果")


def same(actual, expected, tolerance=1e-9):
    assert math.isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance), (actual, expected)


def value_at(document, key):
    value = document
    for component in key.split("/"):
        value = value[int(component)] if isinstance(value, list) else value[component]
    return value


def check_citations():
    text = (ROOT / "交接/结果解读_问题3.md").read_text(encoding="utf-8")
    checked = []
    for line in text.splitlines():
        match = re.fullmatch(r"\|([^|]+)\|([^|]+)\|`([^`]+\.json)`\|`([^`]+)`\|", line)
        if match:
            label, printed, source, key = match.groups()
            assert source.startswith("求解/问题3/结果/")
            actual = value_at(load_json(source), key)
            assert isinstance(actual, (float, int)) and not isinstance(actual, bool)
            assert math.isfinite(actual) and float(printed) == actual, (label, printed, actual)
            checked.append({"指标": label, "数值": actual, "来源文件": source, "键名": key})
    assert checked, "未找到可逐键核验的论文引用清单"
    return checked


def main():
    append_event({"类别": "流程事件", "问题": 3, "尝试": "升格后解读读取计划时使用jq中文点号字段",
                  "现象": "jq拒绝未加方括号的中文键，未产生数值改动。", "决定": "改用方括号访问中文字段。",
                  "依据": "本轮工具输出的jq语法错误；交接/计划.json"})
    append_event({"类别": "流程事件", "问题": 3, "尝试": "升格后解读以当前python3运行含numpy的核验入口",
                  "现象": "导入numpy即报ModuleNotFoundError，核验计算尚未开始。",
                  "决定": "不安装依赖、不修改搜索路径；复用本地标准库核验器并补充覆盖、方向和新红队比对。",
                  "依据": "日志/执行_问题3_升格后解读核验_首次失败.log"})
    assert RESULT.is_dir() and list(RESULT.glob("*.json")), "结果目录或JSON缺失"
    declaration = load_json("交接/结果声明_问题3.json")
    thickness = load_json("求解/问题3/结果/厚度结果.json")
    assert set(declaration) == {"问题", "核心指标", "口径说明", "自检指标", "置信"}
    assert declaration["核心指标"] == thickness["核心指标"]
    assert len(declaration["核心指标"]) == 8
    for label, value in declaration["核心指标"].items():
        assert math.isfinite(value) and value > 0 and label in declaration["口径说明"]
    self_checks = load_json("求解/问题3/结果/声明自检指标.json")["自检指标"]
    assert declaration["自检指标"] == self_checks
    normal_log = ROOT / "日志/执行_问题3_升格优胜.log"
    completion = json.loads(normal_log.read_text(encoding="utf-8").splitlines()[-1])
    assert completion["状态"] == "正常结束" and completion["核心指标"] == thickness["核心指标"]
    REPORT["前置硬门"] = {"结果存在": True, "核心数值逐键数": 8, "自检数值逐键数": len(self_checks),
                           "正常结束日志": str(normal_log.relative_to(ROOT)), "通过": True}
    REPORT["论文引用逐键核对"] = check_citations()
    checkpoint("前置硬门完成")

    redteam = load_json("交接/红队_问题3.json")
    independent = load_json("求解/问题3/红队结果/独立复算结果.json")
    assert redteam["结论"] == "对齐"
    assert independent["运行信息"]["声明规范版本"] == declaration["口径说明"]["独立复算规范"]["版本"]
    differences = []
    for label, modeled in thickness["核心指标"].items():
        reconstructed = independent["复算指标"][label]
        assert reconstructed == redteam["复算指标"][label]
        difference = abs(modeled - reconstructed) / max(abs(modeled), 1e-9)
        assert difference <= 0.01
        matching = next(row for row in redteam["分歧明细"] if row["指标"] == label)
        same(difference, matching["相对差"], 1e-13)
        differences.append({"指标": label, "建模值": modeled, "复算值": reconstructed, "相对差": difference})
    arbitration = load_json("交接/仲裁_问题3.json")
    unresolved = [row for row in arbitration["逐项"] if row["应改方"] == "建模"
                  and not (row["消解状态"] in ("已消解", "已解释") and row["消解证据"])]
    REPORT["红队核对"] = {"逐项": differences, "对齐指标数": len(differences),
                           "最大相对差_百分比": 100 * max(row["相对差"] for row in differences),
                           "容差_百分比": 1, "仲裁未结项数": len(unresolved),
                           "核验边界": "核对本轮独立估参产物与报告，不再次寻优；数值对齐不等于科学质量通过"}
    assert not unresolved
    checkpoint("新红队结果与仲裁状态完成")

    standard_audit.SOURCE = RESULT
    numerical = standard_audit.run_audit()
    REPORT["独立算术核验"] = numerical
    for label, expected in numerical["自检指标"].items():
        same(expected, self_checks[label])
    checkpoint("原附件、场公式、切分、尺度及角块误差完成")
    fair = load_json("求解/问题3/结果/公平对照.json")
    aggregation = []
    for material in ("硅", "碳化硅"):
        for fold in (1, 2):
            group = [row for row in numerical["逐块误差核验"] if row["材料"] == material and row["折号"] == fold]
            averages = {}
            for model in ("两束", "完整往返", "训练均值", "二次趋势"):
                values = [row["复核标准化均方根误差"] for row in group if row["模型"] == model]
                assert len(values) == 4
                averages[model] = sum(values) / len(values)
            saved = next(row for row in fair["分材料验证"][material]["逐折汇总"] if row["折号"] == fold)
            for model, value in averages.items():
                same(value, saved["平均标准化均方根误差"][model])
            improvements = {model: 100 * (1 - averages["完整往返"] / averages[model])
                            for model in ("两束", "训练均值", "二次趋势")}
            for model, value in improvements.items():
                same(value, saved["完整相对基线下降_%"][model])
            aggregation.append({"材料": material, "折号": fold, "误差重聚合": averages, "改善重聚合_百分比": improvements})
    REPORT["折均值与改善重聚合"] = aggregation
    coverage_successes = coverage_total = 0
    naive_maximum_difference = 0.0
    enhanced_splits = []
    tables = {number: standard_audit.read_table(number) for number in range(1, 5)}
    for case in fair["案例"]:
        material, fold = case["材料"], case["折号"]
        for inner in case["训练内选择"]["内层分块"]:
            lower = min(tables[1][row][0] for row in inner["验证源行"])
            upper = max(tables[1][row][0] for row in inner["验证源行"])
            assert all(tables[1][row][0] < lower - 20 or tables[1][row][0] > upper + 20 for row in inner["训练源行"])
        with (RESULT / f"预测_{material}_折{fold}.csv").open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        for number in sorted({int(row["附件"]) for row in rows}):
            angle_rows = [row for row in rows if int(row["附件"]) == number]
            training_rows = set(case["训练源行"])
            training = [row for row in angle_rows if int(row["源行"]) in training_rows]
            observed = [tables[number][int(row["源行"])][1] for row in training]
            mean = sum(observed) / len(observed)
            basis = [[1, (float(row["波数_cm^-1"]) - 2500) / 1300,
                      ((float(row["波数_cm^-1"]) - 2500) / 1300) ** 2] for row in training]
            gram = [[sum(row[left] * row[right] for row in basis) + (1e-12 if left == right else 0)
                     for right in range(3)] for left in range(3)]
            target = [sum(row[power] * value for row, value in zip(basis, observed)) for power in range(3)]
            coefficients = standard_audit.solve(gram, target)
            for row in angle_rows:
                coordinate = (float(row["波数_cm^-1"]) - 2500) / 1300
                trend = sum(coefficient * coordinate ** power for power, coefficient in enumerate(coefficients))
                naive_maximum_difference = max(naive_maximum_difference, abs(mean - float(row["训练均值"])),
                                               abs(trend - float(row["二次趋势"])))
            if not fold:
                continue
            calibration = [row for row in angle_rows if row["用途"] == "校准"]
            testing = [row for row in angle_rows if row["用途"] == "测试"]
            assert len(calibration) == len(testing) == 80
            residuals = sorted(abs(float(row["完整往返"]) - float(row["反射率_比例"])) for row in calibration)
            width = residuals[math.ceil((len(residuals) + 1) * 0.9) - 1]
            successes = sum(abs(float(row["完整往返"]) - float(row["反射率_比例"])) <= width for row in testing)
            coverage_successes += successes
            coverage_total += len(testing)
            enhanced_splits.append({"材料": material, "折号": fold, "附件": number,
                                    "覆盖点数": successes, "测试点数": len(testing), "校准半宽_比例": width})
    same(coverage_successes / coverage_total, self_checks["完整往返反射率测试经验覆盖率"])
    assert naive_maximum_difference < 1e-10
    REPORT["朴素基线预测独立重建最大差"] = naive_maximum_difference
    REPORT["覆盖复核"] = {"逐角逐折": enhanced_splits, "覆盖点数": coverage_successes,
                           "测试点数": coverage_total, "完整场经验覆盖率": coverage_successes / coverage_total}
    directions = []
    for material in ("硅", "碳化硅"):
        for fold in (1, 2):
            rows = [row for row in fair["分材料验证"][material]["逐角逐折逐块"] if row["折号"] == fold]
            positive = sum(row["完整相对两束下降_%"] > 0 for row in rows)
            directions.append({"材料": material, "折号": fold, "正向角块数": positive, "反向角块数": len(rows) - positive})
    REPORT["高阶收益方向"] = directions
    synthetic = load_json("求解/问题3/结果/已知厚度验证.json")
    matched = [row for row in synthetic["案例"] if row["情景"] != "色散失配"]
    assert len(matched) == synthetic["完成匹配情景数"]
    contained = sum(row["真厚度落入条件包络"] for row in matched)
    same(contained / len(matched), self_checks["已知厚度匹配情景条件包络包含率"])
    REPORT["合成自检包含重计数"] = {"匹配情景数": len(matched), "包含数": contained, "包含率": contained / len(matched)}
    checkpoint("覆盖重计数与角块方向完成")

    sensitivity = load_json("求解/问题3/结果/灵敏度.json")["灵敏度_参数扰动"]
    worst = next(row for row in sensitivity if row["材料"] == "硅" and row["折号"] == 2 and row["情景"] == "衬底对比加20%")
    same(worst["请求固定参数值"], worst["实际固定参数值"], 1e-14)
    same(100 * (worst["扰动后厚度_um"] / worst["基准厚度_um"] - 1), worst["相对变化_百分数"])
    same(worst["扰动后厚度_um"] - worst["未扰动控制参数"]["厚度_um"], worst["扣除控制厚度变化_um"])
    REPORT["扰动核验"] = {key: worst[key] for key in ("情景", "基准厚度_um", "扰动后厚度_um", "相对变化_百分数",
                                                   "扣除控制厚度变化_um", "停止状态", "未扰动控制停止状态")}
    REPORT["扰动核验"]["扰动后训练目标"] = worst["重估完整参数"]["训练惩罚后目标"]
    REPORT["扰动核验"]["控制训练目标"] = worst["未扰动控制参数"]["训练惩罚后目标"]
    REPORT["扰动核验"]["解释"] = "两侧均为未收敛的有限搜索候选；算术正确，不将24.47%全部归因为物理灵敏度"
    REPORT["五项正确性"] = {"量纲与数量级": "PASS", "基线": "FAIL", "交叉印证": "FAIL",
                            "防泄漏": "PASS（仅本轮估参隔离，不是未使用过的新测试）", "灵敏度": "FAIL"}
    REPORT["状态"] = "正常结束"
    REPORT["质量裁定"] = "FAIL"
    checkpoint("全部核验完成")
    append_event({"类别": "科学尝试", "问题": 3,
                  "尝试": "从原始光谱重建连续波段留段和训练尺度，逐个角块重算完整往返与两束误差。",
                  "现象": "硅首折四个角块均为正改善，次折只有一个正向、三个反向；完整场反射率区间覆盖640点中的473点。",
                  "决定": "采用逐折、逐角、逐块描述，不把次折写成全部反向，也不以平均误差掩盖负收益。",
                  "依据": "求解结果:高阶收益方向；求解结果:覆盖复核"})
    append_event({"类别": "科学尝试", "问题": 3,
                  "尝试": "对照硅衬底对比乘1.2的有限重估与未扰动控制，检查厚度差及优化终止信息。",
                  "现象": "厚度变化24.472324080105423%，扣除控制后为3.9257384455260222微米；扰动和控制均未收敛，目标分别为1.0089866307669761和0.2884347205136217。",
                  "决定": "保留两种条件下的厚度候选与包络，要求分离搜索误差与物理灵敏度，不把变化率视为纯物性效应。",
                  "依据": "求解结果:扰动核验"})
    print(json.dumps({"问题": 3, "状态": "正常结束", "质量裁定": "FAIL", "核心指标数": 8,
                      "红队最大相对差_百分比": REPORT["红队核对"]["最大相对差_百分比"],
                      "核对评分条数": len(numerical["逐块误差核验"]),
                      "论文引用逐键数": len(REPORT["论文引用逐键核对"]), "实际用时秒": REPORT["实际用时秒"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, stop_for_time)
    signal.alarm(TIME_LIMIT)
    try:
        main()
    except Exception as error:
        REPORT["状态"] = "核验未完成"
        REPORT["异常"] = f"{type(error).__name__}: {error}"
        REPORT["实际用时秒"] = time.monotonic() - STARTED
        save_json(RESULT / "解读核验_升格后.json", REPORT)
        append_event({"类别": "流程事件", "问题": 3, "尝试": "运行升格后解读标准库核验入口",
                      "现象": REPORT["异常"], "决定": "保留阶段结果，核对失败位置后再继续，不将未完成当作通过。",
                      "依据": "日志/执行_问题3_升格后解读核验.log"})
        raise
    finally:
        signal.alarm(0)
