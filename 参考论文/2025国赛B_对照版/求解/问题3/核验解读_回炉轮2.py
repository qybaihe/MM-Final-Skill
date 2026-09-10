import csv
import hashlib
import importlib.util
import json
import math
import signal
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "求解/问题3/结果"
CURRENT = RESULT / "回炉轮2候选/升格3复算"
REPORT_PATH = RESULT / "解读核验_回炉轮2.json"
STARTED = time.monotonic()
TIME_LIMIT = 240
REPORT = {"问题": 3, "状态": "核验中", "时间上限秒": TIME_LIMIT}
BACKUP = ROOT / "日志/解读_回炉轮2_问3核验/修改前"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def checkpoint(stage):
    REPORT["阶段"] = stage
    REPORT["实际用时秒"] = time.monotonic() - STARTED
    save_json(REPORT_PATH, REPORT)
    if REPORT["实际用时秒"] > TIME_LIMIT - 5:
        raise TimeoutError("核验达到时间上限，保留已完成结果")


def time_limit(signum, frame):
    raise TimeoutError("核验达到时间上限")


def same(actual, expected, tolerance=1e-9):
    assert math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance), (actual, expected)


def numeric_differences(before, after, prefix=""):
    if isinstance(before, dict) and isinstance(after, dict):
        assert set(before) == set(after), prefix
        return [row for key in before if "用时" not in key
                for row in numeric_differences(before[key], after[key], f"{prefix}/{key}")]
    if isinstance(before, list) and isinstance(after, list):
        assert len(before) == len(after), prefix
        return [row for index, (left, right) in enumerate(zip(before, after))
                for row in numeric_differences(left, right, f"{prefix}/{index}")]
    return [] if before == after else [{"键": prefix, "上一版": before, "本版": after}]


def value_at(document, key):
    value = document
    for component in key.split("/"):
        value = value[int(component)] if isinstance(value, list) else value[component]
    return value


def citation(label, path, key):
    value = value_at(read_json(path), key)
    assert isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return {"指标": label, "数值": value, "来源文件": str(path.relative_to(ROOT)), "键名": key}


def append_events(events):
    path = ROOT / "交接/实验记录.json"
    previous = read_json(path) if path.exists() else []
    assert isinstance(previous, list)
    forbidden = [line.strip() for line in (ROOT / "运行时/流程词.txt").read_text().splitlines()
                 if line.strip() and not line.startswith("#")]
    for event in events:
        if event["类别"] == "科学尝试":
            text = " ".join(event[key] for key in ("尝试", "现象", "决定", "依据"))
            assert not any(word in text for word in forbidden), text
        if event not in previous:
            previous.append(event)
    save_json(path, previous)


def check_predictions(fair, audit):
    coverage_count = coverage_total = 0
    baseline_error = 0.0
    ranges = []
    partitions = []
    tables = {number: audit.read_table(number) for number in range(1, 5)}
    for case in fair["案例"]:
        material, fold = case["材料"], case["折号"]
        for inner in case["训练内选择"]["内层分块"]:
            lower = min(tables[1][row][0] for row in inner["验证源行"])
            upper = max(tables[1][row][0] for row in inner["验证源行"])
            assert all(tables[1][row][0] < lower - 20 or tables[1][row][0] > upper + 20
                       for row in inner["训练源行"])
        with (CURRENT / f"预测_{material}_折{fold}.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        for number in sorted({int(row["附件"]) for row in rows}):
            angle_rows = [row for row in rows if int(row["附件"]) == number]
            training = [row for row in angle_rows if int(row["源行"]) in set(case["训练源行"])]
            observed = [tables[number][int(row["源行"])][1] for row in training]
            mean = sum(observed) / len(observed)
            basis = [[1, (float(row["波数_cm^-1"]) - 2500) / 1300,
                      ((float(row["波数_cm^-1"]) - 2500) / 1300) ** 2] for row in training]
            gram = [[sum(row[left] * row[right] for row in basis) + (1e-12 if left == right else 0)
                     for right in range(3)] for left in range(3)]
            target = [sum(row[power] * value for row, value in zip(basis, observed)) for power in range(3)]
            coefficients = audit.solve(gram, target)
            for row in angle_rows:
                coordinate = (float(row["波数_cm^-1"]) - 2500) / 1300
                trend = sum(coefficient * coordinate ** power for power, coefficient in enumerate(coefficients))
                baseline_error = max(baseline_error, abs(mean - float(row["训练均值"])),
                                     abs(trend - float(row["二次趋势"])))
            ranges.append({"材料": material, "折号": fold, "附件": number,
                           "观测范围_比例": [min(float(row["反射率_比例"]) for row in angle_rows),
                                           max(float(row["反射率_比例"]) for row in angle_rows)],
                           "完整往返预测范围_比例": [min(float(row["完整往返"]) for row in angle_rows),
                                                   max(float(row["完整往返"]) for row in angle_rows)]})
            if not fold:
                continue
            calibration = [row for row in angle_rows if row["用途"] == "校准"]
            testing = [row for row in angle_rows if row["用途"] == "测试"]
            assert len(calibration) == len(testing) == 80
            residuals = sorted(abs(float(row["完整往返"]) - float(row["反射率_比例"])) for row in calibration)
            half_width = residuals[math.ceil((len(residuals) + 1) * 0.9) - 1]
            covered = sum(abs(float(row["完整往返"]) - float(row["反射率_比例"])) <= half_width for row in testing)
            coverage_count += covered
            coverage_total += len(testing)
            partitions.append({"材料": material, "折号": fold, "附件": number,
                               "覆盖点数": covered, "测试点数": len(testing), "半宽_比例": half_width})
    assert baseline_error < 1e-9
    REPORT["量纲检查"] = ranges
    REPORT["朴素基线独立重建最大差"] = baseline_error
    REPORT["覆盖复核"] = {"覆盖点数": coverage_count, "测试点数": coverage_total,
                           "覆盖率": coverage_count / coverage_total, "逐项": partitions}


def check_sensitivity(fair):
    checks = []
    cases = {(row["材料"], row["折号"]): row for row in fair["案例"]}
    for index, row in enumerate(fair["灵敏度_参数扰动"]):
        if row["状态"] != "已重估":
            continue
        original = cases[(row["材料"], row["折号"])]["完整往返"]
        changed = row["重估完整参数"]
        control = row["未扰动控制参数"]
        total = changed["厚度_um"] - row["基准厚度_um"]
        advancement = control["厚度_um"] - row["基准厚度_um"]
        conditional = changed["厚度_um"] - control["厚度_um"]
        same(total, row["厚度变化_um"])
        same(conditional, row["扣除控制厚度变化_um"])
        same(total, advancement + conditional)
        if "有效损耗" in row["情景"]:
            baseline = original["参数向量"][4]
            expected = baseline
            if row["情景"] == "有效损耗减20%":
                expected *= 0.8
            elif row["情景"] == "有效损耗加20%":
                expected *= 1.2
            elif row["情景"] == "有效损耗绝对增加0→0.1":
                expected = 0.1
            elif row["情景"] == "有效损耗绝对增加0→0.2":
                expected = 0.2
            for actual in (row["请求固定参数值"], row["实际固定参数值"], changed["参数向量"][4]):
                same(actual, expected)
            same(control["参数向量"][4], baseline)
            checks.append({"材料": row["材料"], "折号": row["折号"], "情景": row["情景"],
                           "来源序号": index, "原损耗": baseline, "实际损耗": expected,
                           "总厚度变化_um": total, "优化推进_um": advancement,
                           "扣除控制厚度变化_um": conditional, "实参一致": True})
    worst_index, worst = max(((index, row) for index, row in enumerate(fair["灵敏度_参数扰动"])
                              if row["材料"] == "硅" and row["状态"] == "已重估"),
                             key=lambda pair: abs(pair[1]["相对变化_百分数"]))
    REPORT["损耗实参核验"] = checks
    REPORT["最坏扰动核验"] = {"来源序号": worst_index, "情景": worst["情景"],
        "基准厚度_um": worst["基准厚度_um"], "扰动厚度_um": worst["扰动后厚度_um"],
        "控制厚度_um": worst["未扰动控制参数"]["厚度_um"], "相对变化_百分比": worst["相对变化_百分数"],
        "扣除控制厚度变化_um": worst["扣除控制厚度变化_um"],
        "扰动训练目标": worst["重估完整参数"]["训练惩罚后目标"],
        "控制训练目标": worst["未扰动控制参数"]["训练惩罚后目标"],
        "扰动停止状态": worst["停止状态"], "控制停止状态": worst["未扰动控制停止状态"]}


def main():
    BACKUP.mkdir(parents=True, exist_ok=True)
    for name in ("结果解读_问题3.md", "结果声明_问题3.json", "返工单_问题3.md", "返工单_问题3.json"):
        source = ROOT / "交接" / name
        if source.exists() and not (BACKUP / name).exists():
            (BACKUP / name).write_bytes(source.read_bytes())
    assert RESULT.is_dir() and list(RESULT.glob("*.json")), "缺结果目录或JSON"
    thickness = read_json(CURRENT / "厚度结果.json")
    declaration = read_json(ROOT / "交接/结果声明_问题3.json")
    assert declaration["核心指标"] == thickness["核心指标"]
    core_citations = [citation(key, CURRENT / "厚度结果.json", f"核心指标/{key}") for key in declaration["核心指标"]]
    run_log = ROOT / "日志/执行_回炉_轮2_问3.log"
    lines = run_log.read_text().splitlines()
    completion = next(json.loads(line) for line in reversed(lines) if line.startswith("{"))
    exit_status = (ROOT / "日志/执行_回炉_轮2_问3.done").read_text().strip()
    assert completion["状态"] == "正常结束" and exit_status == "rc=0", "执行日志与退出码未同时满足正常结束"
    assert completion["核心指标"] == thickness["核心指标"]
    previous_log = ROOT / "日志/执行_问题3_升格优胜.log"
    assert json.loads(previous_log.read_text().splitlines()[-1])["状态"] == "正常结束"
    REPORT["前置硬门"] = {"结果目录存在且含JSON": True, "本轮正常结束日志": str(run_log.relative_to(ROOT)),
                           "问题3正常结束日志": str(previous_log.relative_to(ROOT)), "本轮退出码": 0,
                           "核心指标逐键": core_citations, "通过": True}
    checkpoint("前置硬门完成；尚未完成五项正确性")
    previous = read_json(RESULT / "公平对照.json")
    fair = read_json(CURRENT / "公平对照.json")
    differences = numeric_differences(previous, fair)
    assert not differences, differences
    assert hashlib.sha256((ROOT / "求解/问题3/升格3/求解.py").read_bytes()).hexdigest() == fair["实现SHA256"]
    REPORT["版本对照"] = {"上一版本": "求解/问题3/结果/公平对照.json",
                            "本轮版本": str((CURRENT / "公平对照.json").relative_to(ROOT)),
                            "忽略字段": "实际用时秒", "其他字段完全一致": True,
                            "独立重寻优": False, "相对判断": "持平"}
    supplement = RESULT / "回炉轮2候选/成对补查/补查结果.json"
    REPORT["补查产物"] = {"文件": str(supplement.relative_to(ROOT)), "存在": supplement.exists(),
        "缺少实算键": ["固定返回阶数对照", "历史复演", "成对单元", "分级汇总", "同起点控制分解", "分级数值稳定性"]}
    assert not supplement.exists(), "出现新补查结果，须扩展本轮审阅，不能沿用缺失裁决"
    checkpoint("版本与新增对照可用性完成")
    specification = importlib.util.spec_from_file_location("problem3_numeric_audit", ROOT / "求解/问题3/核验_升格标准库.py")
    audit = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(audit)
    audit.SOURCE = CURRENT
    numerical = audit.run_audit()
    REPORT["独立算术核验"] = numerical
    check_predictions(fair, audit)
    checkpoint("原附件、场公式、切分、预测、尺度与覆盖完成")
    summaries = []
    for material in ("硅", "碳化硅"):
        for fold in (1, 2):
            group = [row for row in numerical["逐块误差核验"] if row["材料"] == material and row["折号"] == fold]
            means = {}
            for model in ("两束", "完整往返", "训练均值", "二次趋势"):
                values = [row["复核标准化均方根误差"] for row in group if row["模型"] == model]
                assert len(values) == 4
                means[model] = sum(values) / len(values)
            saved = fair["分材料验证"][material]["逐折汇总"][fold - 1]
            improvements = {model: 100 * (1 - means["完整往返"] / means[model])
                            for model in ("两束", "训练均值", "二次趋势")}
            for model, value in means.items():
                same(value, saved["平均标准化均方根误差"][model])
            for model, value in improvements.items():
                same(value, saved["完整相对基线下降_%"][model])
            directions = [row["完整相对两束下降_%"] for row in fair["分材料验证"][material]["逐角逐折逐块"]
                          if row["折号"] == fold]
            summaries.append({"材料": material, "折号": fold, "误差重聚合": means,
                              "改善重聚合_百分比": improvements,
                              "正向角块数": sum(value > 0 for value in directions),
                              "反向角块数": sum(value < 0 for value in directions)})
    REPORT["逐折复核"] = summaries
    fairness = []
    for case in fair["案例"]:
        trace = case["搜索状态"]
        assert case["两束"]["响应设置_背景阶_增益阶_惩罚"] == case["完整往返"]["响应设置_背景阶_增益阶_惩罚"]
        assert len(case["两束"]["参数向量"]) == len(case["完整往返"]["参数向量"]) == 5
        limits = [[row["共同评估上限"] for row in trace["优化记录"][model]] for model in ("两束", "完整往返")]
        assert limits[0] == limits[1] and len(limits[0]) == len(trace["共同初值"])
        assert trace["粗搜索完整"] and trace["响应选择完整"] and trace["公平性成立"]
        fairness.append({"材料": case["材料"], "折号": case["折号"],
                         "同响应设置": case["两束"]["响应设置_背景阶_增益阶_惩罚"],
                         "共同起点数": len(trace["共同初值"]), "两模型共同评估上限": limits,
                         "完整往返收敛数": sum(row["优化器收敛"] for row in trace["优化记录"]["完整往返"]),
                         "完整往返停止原因": [row["停止原因"] for row in trace["优化记录"]["完整往返"]]})
    REPORT["搜索配对核验"] = fairness
    assert read_json(RESULT / "灵敏度.json")["灵敏度_参数扰动"] == fair["灵敏度_参数扰动"]
    check_sensitivity(fair)
    self_checks = dict(numerical["自检指标"])
    for fold in (1, 2):
        row = fair["分材料验证"]["硅"]["逐折汇总"][fold - 1]
        self_checks[f"硅折{fold}完整往返标准化均方根误差"] = row["平均标准化均方根误差"]["完整往返"]
        self_checks[f"硅折{fold}相对训练均值改善_百分比"] = row["完整相对基线下降_%"]["训练均值"]
        self_checks[f"硅折{fold}相对两束改善_百分比"] = row["完整相对基线下降_%"]["两束"]
    for key, value in self_checks.items():
        same(value, declaration["自检指标"][key])
    same(self_checks["完整往返反射率测试经验覆盖率"], REPORT["覆盖复核"]["覆盖率"])
    REPORT["自检指标"] = {key: declaration["自检指标"][key] for key in self_checks}
    checkpoint("基线、跨折方向、损耗实参和未收敛控制完成")
    finish(fair, declaration, core_citations)


def finish(fair, declaration, core_citations):
    paired = {"逐项": [
        {"id": "审-1-06", "裁定": "未消解", "理由":
         "已核对本轮公平对照与正式v3源码：两模型共享五参数族、响应设置、起点与评估上限，旧不对称搜索问题已消解；"
         "但新增固定全部物理与响应参数、只切返回阶数的对照尚无补查结果.json实算产物。"
         "硅次折完整场误差2.4697868556271865，较均值劣化11.301029489189297%，较两束劣化10.320926260535511%，"
         "高阶收益首折正向、次折平均反向，故该编号的完整算侧验收尚未完成。正文4.3与附录待文腿同步，不作为本次FAIL原因。"},
        {"id": "审-1-23", "裁定": "已消解", "理由":
         "核对现存灵敏度.json及本轮公平对照的同一组真实记录，硅首折原损耗为0，减20%和加20%的请求值、实传值与重估损耗均为0，"
         "两项厚度变化均为0；绝对0→0.1、0→0.2独立命名且实参分别为0.1、0.2。"
         "总变化=未扰动优化推进+扣控制变化逐项成立。算侧标签错配已消解；论文5.2、附录8.4及旧灵敏度表图待文腿同步，不计FAIL。"},
        {"id": "审-2-04", "裁定": "未消解", "理由":
         "本轮运行的是隔离全流程复算，公平对照除用时外与上一版本逐字段相同；硅次折完整场三起点仍为停止码4、评估上限、评估上限，"
         "最坏衬底扰动及控制也均未收敛。新增成对补查/补查结果.json不存在，尚无150/300/600级的真实终点、"
         "约束残差、最优访问点及同起点分解，不能凭已写代码消解数值稳定性。"}],
        "相对判断": "持平", "决定性理由":
        "只与此次回炉执行前的正式升格3版本比较：公平对照除实际用时外全部字段完全一致，八项头条、逐角块误差、扰动与停止状态未变；"
        "新增补查设计尚未执行。算侧真实数值没有改善，也没有因文图尚未同步而判更差。"}
    save_json(ROOT / "交接/配对裁定_问题3_回炉轮2.json", paired)
    REPORT["配对评审"] = paired
    REPORT["诊断评分"] = {"分数": 6.0, "锚点": "有真实条件答案且可复核，但基线、跨折与收敛性存在明显缺陷",
                          "用途": "先逐项裁定和相对判断，再给诊断分；评分不替代一票否决"}
    REPORT["五项正确性"] = [
        {"协议": "量纲与数量级", "裁定": "通过", "证据":
         "厚度为有限正微米值；原附件百分数转比例逐点一致，主模型预测均处于合理反射率范围；"
         "场公式、训练尺度、原始比例RMSE与无量纲RMSE/IQR独立核对一致，条件包络与概率区间明确区分。"},
        {"协议": "基线", "裁定": "不通过", "证据":
         "硅首折较均值改善31.758768367184466%、较两束改善25.43689096320393%，"
         "次折分别为-11.301029489189297%、-10.320926260535511%；不能以折平均或朴素预测标签遮盖败于基线的折。"},
        {"协议": "交叉印证", "裁定": "不通过", "证据":
         "有限场与闭式的公式交叉核对通过，但硅高阶收益首折四角块正向、次折一正三负；"
         "碳化硅高阶收益也跨折反向。公式一致和旧独立估参对齐不能替代不同切分的结论方向一致。"},
        {"协议": "防泄漏", "裁定": "通过（单次执行口径）", "证据":
         "这是波数分块反演而非时序预测；外训练、校准、测试源行互斥，内验证只取外训练，保护带经源坐标重建；"
         "训练尺度、均值、趋势、响应和初值只用训练数据。已用外测试参与前轮开发，故仅允许探索性比较，不宣称全新独立泛化证据。",
         "源码": ["求解/问题3/升格3/求解.py:143", "求解/问题3/升格3/求解.py:164",
                  "求解/问题3/升格3/求解.py:385", "求解/问题3/升格3/求解.py:473",
                  "求解/问题3/升格3/求解.py:489", "求解/问题3/升格3/求解.py:565"]},
        {"协议": "灵敏度", "裁定": "不通过", "证据":
         "零损耗标签与实参已修正；但最坏衬底扰动厚度变化24.472324080105423%，扣控制后3.9257384455260222微米，"
         "两侧均为停止码4且未收敛，缺少新增分级同起点补查，尚不能分离搜索不稳定与物理条件响应。"}]
    REPORT["质量裁定"] = "FAIL"
    checkpoint("配对裁定与五项正确性完成")
    rework = [
        {"对应": "审-1-06", "级别": "正确性", "目标": "算",
         "定位": "求解/问题3/结果/回炉轮2候选/升格3复算/公平对照.json:分材料验证/硅/逐折汇总；求解/问题3/求解_问题3.py:run_paired_budget_review",
         "问题": "协议2、3仍不通过：硅次折较均值劣化11.301029489189297%、较两束劣化10.320926260535511%，"
                 "高阶收益跨折反向；本轮只重跑原v3，新增固定返回阶数对照未执行。不是正文或图未同步造成的FAIL。",
         "指令": "先真实执行成对补查，保留固定五物理参数和全部响应、只切返回阶数的逐材料逐角块结果；"
                 "再仅在训练内改进弱识别或响应控制，保留全部原测试块与均值、二次趋势、两束对照。"
                 "若重复使用原测试，继续标探索性，不宣称新独立验证。任何未达标情形都须给出最新最佳条件点值、完整条件范围和局限，不能清空答案。",
         "验收": "必须产出成对补查/补查结果.json:固定返回阶数对照真实数值，以及公平对照.json:分材料验证中的两材料逐角逐折逐块误差和改善。"
                 "逐折核对主方法相对均值、二次和两束的幅度及方向，不能删坏折。厚度结果.json:核心指标必须保留八个实数键："
                 "硅全量与两折厚度、硅包络两端、碳化硅正式值与范围两端；即使仍FAIL也必须继续发布条件数值答案。"},
        {"对应": "审-2-04", "级别": "正确性", "目标": "算",
         "定位": "求解/问题3/结果/回炉轮2候选/成对补查/补查结果.json（缺失）；公平对照.json:案例[材料=硅,折号=2]/搜索状态；灵敏度.json:灵敏度_参数扰动/27",
         "问题": "协议5不通过：完整场硅次折三起点无一收敛，最坏扰动与控制仍均为停止码4；"
                 "衬底扰动从16.042358431815565到19.968296377341588微米，扣控制仍差3.9257384455260222微米。"
                 "新增150/300/600级补查没有实算产物，不能把原有限重估的差称为固有灵敏度。",
         "指令": "运行 python3 求解/问题3/求解_问题3.py --paired-budget-review，不要再次仅运行 --fair-comparison-only。"
                 "核查该入口实际结束并保留真实停止终点、最优访问点、约束余量、局部差分、同起点同上限的两模型与扰动/控制结果；"
                 "逐级比较厚度、训练目标、固定值与扣控制变化，分离数值问题和条件影响。有限时限内仍未收敛时保留当前最优可行候选及条件包络，不虚报收敛。",
         "验收": "补查结果.json须含历史复演、成对单元、分级汇总、同起点控制分解、分级数值稳定性的非空真实记录，"
                 "每侧至少给厚度_um、训练目标、最大约束违反量、停止原因和评估上限；同步给含历史累计最优与已算包络端点。"
                 "检查同起点、同训练源行、固定请求=实际值和跨级稳定性；候选若改变正式点值须按公开选择规则冻结并更新八项非空核心指标，不只交代码或回执。"}]
    save_json(ROOT / "交接/返工单_问题3.json", rework)
    rework_text = "# 问题3回炉轮2返工单\n\n判定：FAIL。前置产物硬门通过；否决来自算侧协议2、3、5。\n\n"
    for row in rework:
        rework_text += f"## {row['对应']}（{row['级别']}／{row['目标']}）\n\n"
        rework_text += "\n\n".join(f"**{key}**：{row[key]}" for key in ("定位", "问题", "指令", "验收")) + "\n\n"
    rework_text += ("## 已消解与边界\n\n审-1-23的现存结果损耗标签已消解。论文5.2、附录8.4及旧图表待文腿同步；"
                    "审-1-06的正文4.3与附录也待文腿同步。这些不作为本轮FAIL理由。\n\n"
                    "本轮八项头条与上一版完全相同，已给真实条件点值和范围；不以FAIL停止数值作答。"
                    "条件范围不是概率置信区间。配对裁定见交接/配对裁定_问题3_回炉轮2.json。\n")
    (ROOT / "交接/返工单_问题3.md").write_text(rework_text, encoding="utf-8")
    declaration["置信"] = {"等级": "低", "理由":
        "本轮原附件、场响应、切分及64项角块误差复算一致，八项条件数值与上一版相同；"
        "但硅次折较训练均值劣化11.30%、高阶收益跨折反向，最坏扰动与控制均未收敛且厚度变24.47%，"
        "新增数值稳定性补查尚未执行，因此报告条件最佳估计和已计算包络，不解释为唯一真厚度或概率置信区间。"}
    instructions = declaration["口径说明"]
    contract = instructions.get("独立复算规范")
    if contract is not None:
        contract["时间与数值复现"] = (
            "本轮隔离复算18分钟软上限，59.11855150014162秒正常结束；除用时外结果与前版相同。"
            "只完成原v3复算，新增150/300/600级补查尚未执行。新独立实现应沿同候选、同训练目标、"
            "同停止规则复算并报告浮点/优化器分歧，不以旧厚度作初值；公开生成规则不保证优化问题唯一。")
        contract_text = "；".join(f"〔{key}〕{value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}"
                                 for key, value in contract.items())
        specifications = {key: instructions[key] for key in declaration["核心指标"]}
        first = next(iter(specifications))
        specifications[first] += " 本声明八项指标共用的独立复算规范：" + contract_text
        declaration["口径说明"] = specifications
    assert set(declaration) == {"问题", "核心指标", "口径说明", "自检指标", "置信"}
    assert set(declaration["口径说明"]) == set(declaration["核心指标"])
    assert all(isinstance(value, str) for value in declaration["口径说明"].values())
    save_json(ROOT / "交接/结果声明_问题3.json", declaration)
    citations = list(core_citations)
    for key in declaration["自检指标"]:
        citations.append(citation(key, REPORT_PATH, f"自检指标/{key}"))
    for fold in (1, 2):
        for model in ("两束", "训练均值", "二次趋势"):
            citations.append(citation(f"硅折{fold}{model}标准化误差", CURRENT / "公平对照.json",
                                      f"分材料验证/硅/逐折汇总/{fold - 1}/平均标准化均方根误差/{model}"))
        citations.append(citation(f"硅折{fold}相对二次趋势改善_百分比", CURRENT / "公平对照.json",
                                  f"分材料验证/硅/逐折汇总/{fold - 1}/完整相对基线下降_%/二次趋势"))
        citations.append(citation(f"碳化硅折{fold}相对两束改善_百分比", CURRENT / "公平对照.json",
                                  f"分材料验证/碳化硅/逐折汇总/{fold - 1}/完整相对基线下降_%/两束"))
    for key in ("基准厚度_um", "扰动厚度_um", "控制厚度_um", "扰动训练目标", "控制训练目标"):
        citations.append(citation("最坏衬底扰动_" + key, REPORT_PATH, f"最坏扰动核验/{key}"))
    for key in ("覆盖点数", "测试点数", "覆盖率"):
        citations.append(citation("完整场反射率区间_" + key, REPORT_PATH, f"覆盖复核/{key}"))
    for index, title in ((0, "硅首折"), (1, "硅次折")):
        for key in ("正向角块数", "反向角块数"):
            citations.append(citation(title + key, REPORT_PATH, f"逐折复核/{index}/{key}"))
    for index in (1, 2, 3, 4):
        label = fair["灵敏度_参数扰动"][index]["情景"]
        for key in ("实际固定参数值", "厚度变化_um"):
            citations.append(citation("硅首折" + label + key, CURRENT / "公平对照.json", f"灵敏度_参数扰动/{index}/{key}"))
    for key in ("预测独立代入最大差", "逐块误差独立复算最大差", "闭式与六十四项场最大差"):
        citations.append(citation(key, REPORT_PATH, f"独立算术核验/误差核验/{key}"))
    REPORT["论文引用逐键核对"] = citations
    checkpoint("声明、返工单与引用逐键完成")
    table = "|指标|数值|来源文件|键名|\n|---|---:|---|---|\n"
    table += "\n".join(f"|{row['指标']}|{row['数值']}|`{row['来源文件']}`|`{row['键名']}`|" for row in citations)
    text = """# 问题3结果解读：回炉轮2重新核验

## 本轮结论与证据版本

**FAIL：前置产物硬门通过，五项正确性中的基线、交叉印证和灵敏度仍未通过。** 数值答案保留，不将FAIL写成不作答。

本轮实际执行产物在 `求解/问题3/结果/回炉轮2候选/升格3复算/`；执行日志 `日志/执行_回炉_轮2_问3.log` 明示正常结束，配套执行完成标记为 `rc=0`。原正式执行日志 `日志/执行_问题3_升格优胜.log` 同样正常结束。将本轮公平对照与执行前正式版本逐字段比较，除实际用时外全部相同；不是已经完成新增成对稳定性补查的新解。当前八项核心数值在本轮厚度结果中逐键存在。

审阅范围仅含结果、结果声明和结果解读。正文、附录与图的同步留给后续文图环节；未同步不构成本轮FAIL。核验底稿为 `求解/问题3/结果/解读核验_回炉轮2.json`，重建原附件、场响应、切分、训练尺度和逐块误差，未独立重跑非线性寻优，也不把历史独立复算对齐称作本轮新复算。

## 配对评审（先裁定，再作相对判断）

- **审-1-06：未消解。** 当前两模型的参数族、响应设置、共同初值和计算上限相同，旧不对称求解已修；但固定全部物理与响应参数后只切换返回阶数的新增对照还没有实算产物，主模型基线与跨折性能问题也仍在。正文4.3与附录待文腿同步。
- **审-1-23：已消解（算侧）。** 当前零损耗的相对增减确实保持零，绝对增加独立列出，请求、实传和重估参数相符；变化分解也成立。论文5.2、附录8.4及旧损耗图表待文腿同步，不据此否决。
- **审-2-04：未消解。** 当前完整场次折全部精修终点未收敛，最坏扰动和控制也未收敛；新增分级补查结果文件不存在，不能把实现了算法当作已经验证稳定。
- **相对判断：持平。** 比较对象是此次执行前的正式v3，不是更早的旧主线；核心厚度、逐块误差和扰动结果未变。结构化裁定见 `交接/配对裁定_问题3_回炉轮2.json`。

## 结果讲了什么

### 硅：给出条件厚度，同时说明谱形解释的边界

全量双角完整往返模型的条件厚度为 **2.165120838064207 μm**，已计算条件包络为 **[1.732058431974077, 19.968296377341588] μm**。两个留段条件解分别为 **1.7321417462264854 μm**、**16.042358431815565 μm**，不取它们的平均代替全量点值。全量两束条件厚度为 **2.3193002615505804 μm**，完整场相对改变 **-6.6476698184518845%**；这是两个条件模型的点值差，不是真实测厚偏差。

首折完整场相对训练均值改善 **31.758768367184466%**、相对两束改善 **25.43689096320393%**；次折则分别为 **-11.301029489189297%**、**-10.320926260535511%**。次折完整场标准化误差 **2.4697868556271865**，均值与两束分别为 **2.2190152840114363**、**2.238729259573566**。这说明当前完整场能解释部分谱形，却未形成跨留段稳定的高阶收益；不得把预测标签选中的朴素模型误差冒称完整场测厚方法误差。

### 碳化硅：保留问题二答案，不以本问诊断候选替换

正式厚度继续为 **10.80351642616071 μm**，正式条件范围 **[0.6366538383706806, 18.106446399344016] μm**。它来自问题二正式基准，在本问结果的核心指标中原样承接。两个留段的完整场相对两束收益一正一负，尚未满足修正厚度的条件。不要把 `厚度条件范围_um/碳化硅` 的本问诊断范围误写成该正式范围。

附件分别保留判断：附件3与4的高阶收益跨段不稳定；附件1与2也未同时形成稳定的可观测高阶收益与必要厚度修正。以上是模型比较结论，不是对多光束物理过程不存在的证明。

### 损耗口径与数值稳定性分开处理

硅首折零损耗的相对增减，实际固定值及厚度变化都为 **0.0**；绝对增加至 **0.1**、**0.2** 时，厚度变化分别为 **0.00030375927734094255 μm**、**0.0005652588624236454 μm**，不可混写成相对扰动。

最坏衬底对比扰动的厚度从 **16.042358431815565 μm** 变至 **19.968296377341588 μm**，相对变化 **24.472324080105423%**；控制点为 **16.042557931815566 μm**，扣控制后差 **3.9257384455260222 μm**。扰动与控制训练目标分别为 **1.0089866307669761**、**0.2884347205136217**，两者均未收敛。这组差是已算有限重估的条件响应，还不能归结为纯物理灵敏度。

## 五项正确性

|协议|裁决|决定性证据|
|---|---|---|
|量纲与数量级|通过|厚度为微米，反射率百分数转比例；原观测、场公式、训练尺度和角块误差独立重建一致；未发现主模型反射率越界。|
|基线|不通过|硅次折同时劣于训练均值和同口径两束；不能只用首折或跨材料平均优势。|
|交叉印证|不通过|场级数与闭式一致，但高阶预测收益跨折反向；公式正确不等于实证结论稳定。|
|防泄漏|通过（执行级）|按源行重建外训练、校准、测试互斥关系及内验证保护带；训练尺度、朴素基线和响应只取训练数据。原外测试已用于开发，只作探索性比较。|
|灵敏度|不通过|零损耗标签已修，但最坏扰动与控制仍未收敛，缺少新增同起点分级补查实数。|

这里的观测轴是波数，不是时间；双侧训练波数并非未来观测泄漏。对应源码为 `求解/问题3/升格3/求解.py` 的 `prepare_case`、`inner_partitions`、`choose_config`、`score_case`。不可将执行级分离夸大为独立外部泛化验证。

## 论文引用清单

以下每一数值均按结果JSON中的键实际读取并校验；数组位置从零起算。前八项为独立复算头条，其余为方法自检或解释证据。标准化误差为逐角逐块的反射率比例RMSE除以对应训练去趋势残差IQR，再等权聚合；改善为相对误差下降百分比，负值保留。源值采用完整精度，正文可统一舍入，不能改符号或口径。

""" + table + """

## 局限与下游使用

- 条件包络是已计算模型、折间、训练近优分支与实际完成扰动的并集，不是概率置信区间；未收敛可行候选必须保留其停止状态，不称全局最优或唯一真厚度。
- 完整场反射率区间在测试点的经验覆盖为 **473/640 = 0.7390625**；匹配合成条件下的厚度包络包含率为 **0.1111111111111111**。两者均属方法自检，不是实测厚度真值覆盖。
- 外测试已参与开发，物理条件还缺独立光学常数、仪器分辨率与相干信息；不将高相关或谐波直接写成多光束的充分证据。
- 唯一置信母本是 `交接/结果声明_问题3.json:置信`；撰稿只在该问结论句按母本使用一次保留话，其余陈述上述已计算事实。
- 下一轮必须真实执行 `--paired-budget-review`，产出固定返回阶数对照和同起点分级稳定性数值，再独立复核。无论是否达标，始终保留最佳条件点值与完整条件范围；文图同步不属于本轮算侧否决项。
"""
    (ROOT / "交接/结果解读_问题3.md").write_text(text, encoding="utf-8")
    append_events([
        {"类别": "流程事件", "问题": 3, "尝试": "回炉轮2首次核验把退出码误读为执行日志末行",
         "现象": "执行日志末行是正常结束结果，rc=0实际位于执行完成标记，导致核验断言中止；求解本身正常。",
         "决定": "分别读取结果末行与完成标记后重核，不将核验器读取错误归为模型失败。",
         "依据": "日志/执行_问题3_回炉轮2解读核验_退出码读取修正前.log；日志/执行_回炉_轮2_问3.done"},
        {"类别": "科学尝试", "问题": 3, "尝试": "由原始观测和保存预测重新计算各角各留段误差，再按角块等权比较完整场与均值和两束",
         "现象": "硅首折相对均值改善31.758768367184466%，次折反而劣化11.301029489189297%；完整场对两束首折四块正向，次折一正三负。",
         "决定": "保留硅条件厚度2.165120838064207微米及完整条件范围，不把局部谱形优势扩展为跨波段稳定的高阶优势。",
         "依据": "求解结果:逐折复核；求解结果:核心指标"},
        {"类别": "科学尝试", "问题": 3, "尝试": "逐项比对零有效损耗的相对扰动和绝对增加，并重新计算总变化与扣控制变化",
         "现象": "硅首折零损耗相对增减均保持0且厚度变化为0；绝对增加至0.1和0.2分别使厚度增加0.00030375927734094255和0.0005652588624236454微米。",
         "决定": "将零点相对扰动与绝对扰动分开解释，不以零点相对扰动不变证明非零损耗下也稳定。",
         "依据": "求解结果:损耗实参核验"},
        {"类别": "流程事件", "问题": 3, "尝试": "完成问题3回炉轮2配对评审与五项正确性核验",
         "现象": "八项头条及全部公平对照实数与上一版相同；成对补查未执行；审-1-23算侧已消解，审-1-06与审-2-04未消解。",
         "决定": "相对判断持平，质量FAIL；保留非空数值声明，文图待同步不作否决原因。",
         "依据": "交接/配对裁定_问题3_回炉轮2.json；求解/问题3/结果/解读核验_回炉轮2.json"}])
    for row in citations:
        assert value_at(read_json(ROOT / row["来源文件"]), row["键名"]) == row["数值"]
    assert read_json(ROOT / "交接/结果声明_问题3.json")["核心指标"] == read_json(CURRENT / "厚度结果.json")["核心指标"]
    REPORT["状态"] = "正常结束"
    REPORT["交付检查"] = {"声明严格顶层": True, "核心指标数": len(core_citations),
                            "口径逐项为字符串": True, "论文数值逐键数": len(citations), "实验记录追加完成": True}
    checkpoint("交付完成")
    (ROOT / "日志/解读_回炉_轮2_问3.done").write_text(
        "FAIL\n前置硬门通过；八项核心指标逐键存在，原附件与角块误差复核一致。\n"
        "协议2、3、5仍不通过；新增固定返回阶数及分级成对补查尚无实算产物。\n"
        "审-1-23算侧已消解；审-1-06、审-2-04未消解；相对判断持平，文图待同步不计FAIL。\n"
        "已更新结果解读、低置信非空声明、结构化返工单、配对裁定与实验记录；保留条件厚度及范围。\n", encoding="utf-8")
    print(json.dumps({"问题": 3, "状态": REPORT["状态"], "质量裁定": "FAIL", "核心指标数": len(core_citations),
                      "论文引用逐键数": len(citations), "实际用时秒": REPORT["实际用时秒"],
                      "误差核验": REPORT["独立算术核验"]["误差核验"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, time_limit)
    signal.alarm(TIME_LIMIT)
    try:
        main()
    except Exception as error:
        REPORT["状态"] = "核验中止，未宣告通过"
        REPORT["异常"] = f"{type(error).__name__}: {error}"
        REPORT["实际用时秒"] = time.monotonic() - STARTED
        save_json(REPORT_PATH, REPORT)
        append_events([{"类别": "流程事件", "问题": 3, "尝试": "回炉轮2解读核验",
                        "现象": REPORT["异常"], "决定": "保存当前核验结果，查明原因后继续",
                        "依据": "求解/问题3/结果/解读核验_回炉轮2.json"}])
        raise
    finally:
        signal.alarm(0)
