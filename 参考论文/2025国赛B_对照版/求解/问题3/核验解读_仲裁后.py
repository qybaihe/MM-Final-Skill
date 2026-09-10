"""只读复核问题3现有结果，180秒内分阶段保存证据，不重拟合厚度。"""

import csv
import hashlib
import importlib.util
import json
import math
import signal
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "求解/问题3/结果/解读核验_回炉1_仲裁后.json"
START = time.monotonic()
BUDGET = 180
REPORT = {"问题": 3, "状态": "核验中", "核验预算秒": BUDGET}


def read(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def save():
    REPORT["实际用时秒"] = time.monotonic() - START
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2,
                                    allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)


def budget_check(*unused):
    if time.monotonic() - START >= BUDGET - 5:
        raise TimeoutError("到达核验软截止，保存已完成证据")


def digest(relative):
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def resolve(relative, keys):
    value = read(relative)
    for key in keys:
        value = value[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"非有限数值：{relative}:{keys}")
    return value


def verify():
    result_folder = ROOT / "求解/问题3/结果"
    paths = list(result_folder.glob("*.json"))
    for path in paths:
        json.loads(path.read_text(encoding="utf-8"))
    execution = "日志/执行_问题3_回炉1_仲裁后.log"
    marker = "日志/执行_问题3_回炉1_仲裁后.done"
    log_payload = json.loads((ROOT / execution).read_text(encoding="utf-8").strip().splitlines()[-1])
    state = read("求解/问题3/结果/执行状态.json")
    REPORT["产物硬门"] = {
        "结果目录存在": result_folder.is_dir(), "合法结果文件数": len(paths),
        "运行日志": execution, "结束标记": marker,
        "正常结束": "rc=0" in (ROOT / marker).read_text()
        and log_payload["运行状态"] == state["运行状态"] == "已完成数值交付",
        "特殊复核后再返工标记存在": (ROOT / "日志/仲裁返工_问题3_回炉1_复核.done").exists(),
    }
    assert paths and REPORT["产物硬门"]["正常结束"]
    declaration = read("交接/结果声明_问题3.json")
    thickness_path = "求解/问题3/结果/厚度结果.json"
    refresh_path = "求解/问题3/结果/声明刷新_回炉1_仲裁后.json"
    sources = {
        "硅全量完整往返厚度_um": (thickness_path, ["硅", "全量条件估计", "全量完整往返", "厚度_um"]),
        "硅折1完整往返条件厚度_um": (thickness_path, ["硅", "折间完整往返", 0, "厚度_um"]),
        "硅折2完整往返条件厚度_um": (thickness_path, ["硅", "折间完整往返", 1, "厚度_um"]),
        "硅已计算条件包络下限_um": (refresh_path, ["硅条件情景包络", "下限_um"]),
        "硅已计算条件包络上限_um": (refresh_path, ["硅条件情景包络", "上限_um"]),
        "碳化硅正式基准厚度_um": (thickness_path, ["碳化硅", "问题2正式基准厚度_um"]),
        "碳化硅正式条件范围下限_um": (thickness_path, ["碳化硅", "问题2正式条件范围_um", 0]),
        "碳化硅正式条件范围上限_um": (thickness_path, ["碳化硅", "问题2正式条件范围_um", 1]),
    }
    REPORT["核心逐键来源"] = []
    for name, (relative, keys) in sources.items():
        value = resolve(relative, keys)
        assert declaration["核心指标"][name] == value
        REPORT["核心逐键来源"].append({"指标": name, "数值": value, "来源文件": relative,
                                      "来源键": keys, "与声明一致": True})
    REPORT["产物硬门"]["核心数值逐键可寻"] = True
    save()
    budget_check()

    red = read("交接/红队_问题3.json")
    independent = read("求解/问题3/红队结果/复算结果.json")
    independent_check = read("求解/问题3/红队结果/报告核验_回炉1_复核.json")
    comparisons = []
    for item in REPORT["核心逐键来源"]:
        name, value = item["指标"], item["数值"]
        independent_value = independent["复算指标"][name]
        assert independent_value == red["复算指标"][name]
        assert math.isfinite(independent_value)
        assert independent["指标状态"][name]["完整口径执行"]
        difference = abs(value - independent_value) / max(abs(value), 1e-9)
        comparisons.append({"指标": name, "建模值": value, "复算值": independent_value,
                            "相对差": difference, "相对差_%": 100 * difference,
                            "容差内": difference <= 0.01})
    for item in independent_check["输入一致性核验"]:
        assert digest(item["文件"]) == item["摘要"]
    REPORT["独立复算比对"] = {
        "逐项": comparisons, "通过数": sum(item["容差内"] for item in comparisons),
        "指标数": len(comparisons), "相对容差": 0.01,
        "最大相对差": max(item["相对差"] for item in comparisons),
        "最大相对差_%": max(item["相对差_%"] for item in comparisons),
        "停止核验": independent_check["计算状态核验"],
        "附件摘要一致": True,
    }
    thickness = read(thickness_path)
    silicon_values = [thickness["硅"]["全量条件估计"]["全量完整往返"]["厚度_um"]]
    silicon_values.extend(item["厚度_um"] for item in thickness["硅"]["折间完整往返"])
    silicon_values.extend(item["扰动后厚度_um"] for item in thickness["灵敏度_参数扰动"]
                          if item["材料"] == "硅")
    REPORT["硅条件包络"] = {"成员数": len(silicon_values), "下限_um": min(silicon_values),
                            "上限_um": max(silicon_values)}
    assert min(silicon_values) == declaration["核心指标"]["硅已计算条件包络下限_um"]
    assert max(silicon_values) == declaration["核心指标"]["硅已计算条件包络上限_um"]
    save()

    comparison = read("求解/问题3/结果/同口径对照.json")
    main_error = comparison["主方法连续留段标准化均方根误差"]
    REPORT["基线比较"] = {"主方法误差": main_error}
    for name, value in comparison["基线对照"].items():
        REPORT["基线比较"][name + "误差"] = value
        REPORT["基线比较"][name + "下降_%"] = 100 * (1 - main_error / value)
    two_error = comparison["两束对照连续留段标准化均方根误差"]
    REPORT["基线比较"].update({"粗网格两束误差": two_error,
                               "粗网格两束下降_%": 100 * (1 - main_error / two_error),
                               "公平对照结果存在": (result_folder / "公平对照.json").exists()})
    with (result_folder / "留段预测.csv").open(encoding="utf-8-sig", newline="") as stream:
        predictions = [row for row in csv.DictReader(stream) if row["模型"] == "完整往返"]
    covered = sum(float(row["区间下界_比例"]) <= float(row["实测反射率_比例"])
                  <= float(row["区间上界_比例"]) for row in predictions)
    coverage = read("求解/问题3/结果/区间覆盖.json")["经验覆盖率"]
    assert covered == coverage["覆盖点数"] and len(predictions) == coverage["测试点数"]
    assert covered / len(predictions) == coverage["测试经验覆盖率"]
    REPORT["覆盖重计"] = {"覆盖点数": covered, "测试点数": len(predictions),
                           "经验覆盖率": covered / len(predictions),
                           "名义覆盖率": coverage["名义覆盖率"],
                           "与结果一致": True, "对象": "反射率点预测，不是厚度"}
    grouped = {}
    for row in comparison["分块评分"]:
        key = (row["材料"], row["折号"], row["附件"], row["测试块"])
        grouped.setdefault(key, {})[row["模型"]] = row["标准化均方根误差"]
    paired = [{"材料": key[0], "折号": key[1], "附件": key[2], "测试块": key[3],
               "完整往返误差": values["完整往返"], "两束误差": values["两束"],
               "差值": values["完整往返"] - values["两束"]}
              for key, values in grouped.items()]
    REPORT["交叉印证"] = {
        "逐块比较": paired, "配对块数": len(paired),
        "完整往返退步块数": sum(item["差值"] > 0 for item in paired),
        "有限级数最大绝对误差": max(item["几何级数与闭式最大绝对误差"]
                                    for item in comparison["场级数核验"]),
        "双向留角度性质": "共享物理参数由两角训练共同估计，只是响应迁移，不是目标角完全留出",
    }
    save()
    budget_check()

    specification = importlib.util.spec_from_file_location("question_three_review", ROOT / "求解/问题3/求解_问题3.py")
    solver = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(solver)
    inputs = solver.load_inputs()
    baseline = read("求解/问题3/结果/灵敏度_完整基准.json")
    leak_checks = []
    for item in baseline["每折完整基准"]:
        budget_check()
        material, fold = item["材料"], item["折分"]
        case = solver.make_case(material, inputs["coordinates"], inputs["sampled"][material], fold)
        training = set(case["train_indices"])
        calibration = {index for block in fold["校准块"] for index in solver.block_indices(block)}
        testing = {index for block in fold["测试块"] for index in solver.block_indices(block)}
        assert not (training & calibration or training & testing or calibration & testing)
        assert sorted(training) == item["训练索引"]
        original = solver.evaluate(item["参数向量"], case, True)
        changed = [[value if index in training else value + 10 + index / 1000
                    for index, value in enumerate(series)] for series in inputs["sampled"][material]]
        changed_case = solver.make_case(material, inputs["coordinates"], changed, fold)
        altered = solver.evaluate(item["参数向量"], changed_case, True)
        assert original["loss"] == altered["loss"]
        assert original["models"] == altered["models"]
        leak_checks.append({"材料": material, "折号": item["折号"], "训练数_每角": len(training),
                            "校准数_每角": len(calibration), "测试数_每角": len(testing),
                            "三集合互斥": True, "训练索引与冻结基准一致": True,
                            "改变非训练反射率后训练目标绝对差": abs(original["loss"] - altered["loss"]),
                            "训练响应系数完全不变": True})
    REPORT["防泄漏核验"] = leak_checks
    loss = read("求解/问题3/结果/灵敏度_损耗复算.json")
    losses = loss["灵敏度_参数扰动"]
    main_sensitivity = {(item["材料"], item["折号"], item["情景"]): item
                        for item in thickness["灵敏度_参数扰动"]}
    for item in losses:
        other = main_sensitivity[(item["材料"], item["折号"], item["情景"])]
        for key in ["实际固定参数值", "扰动后厚度_um", "扣除控制厚度变化_um"]:
            assert item[key] == other[key]
        assert math.isclose(item["扰动后厚度_um"] - item["控制厚度_um"],
                            item["扣除控制厚度变化_um"], abs_tol=1e-12)
    relative = [item for item in losses if item["情景类别"] == "相对扰动"]
    REPORT["损耗核验"] = {
        "情景数": len(losses), "相对扰动数": len(relative), "与主结果逐项一致": True,
        "相对扰动扣除控制厚度最大绝对变化_um": max(abs(item["扣除控制厚度变化_um"]) for item in relative),
        "硅首折绝对增加0.1配对响应_um": losses[3]["扣除控制厚度变化_um"],
        "硅首折绝对增加0.2配对响应_um": losses[4]["扣除控制厚度变化_um"],
        "说明": "只验证冻结数值一致与分解算术；本轮不重新精修，不把零相对扰动解释成全局不敏感",
    }
    REPORT["源文件摘要"] = {relative: digest(relative) for relative in [
        thickness_path, "求解/问题3/结果/同口径对照.json", "求解/问题3/结果/灵敏度_损耗复算.json",
        "求解/问题3/求解_问题3.py", "求解/问题3/红队结果/复算结果.json", "交接/仲裁_问题3.json"]}
    REPORT["状态"] = "核验完成，公平基线仍待实算"
    save()


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, budget_check)
    signal.alarm(BUDGET - 5)
    try:
        verify()
    except Exception as error:
        REPORT["状态"] = "核验未完成"
        REPORT["异常"] = str(error)
        save()
        raise
    finally:
        signal.alarm(0)
        print(json.dumps({"问题": 3, "状态": REPORT["状态"],
                          "实际用时秒": time.monotonic() - START,
                          "证据": str(OUTPUT.relative_to(ROOT))}, ensure_ascii=False))
