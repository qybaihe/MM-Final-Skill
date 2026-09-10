import hashlib
import importlib.util
import json
import math
import signal
import time
from collections import Counter
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "求解/问题3/结果"
REPORT_PATH = RESULT / "解读核验_回炉轮3.json"
STARTED = time.monotonic()
TIME_LIMIT = 240
REPORT = {"问题": 3, "状态": "核验中", "时间上限秒": TIME_LIMIT}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def checkpoint(stage):
    REPORT["阶段"] = stage
    REPORT["实际用时秒"] = time.monotonic() - STARTED
    save_json(REPORT_PATH, REPORT)
    if REPORT["实际用时秒"] >= TIME_LIMIT - 5:
        raise TimeoutError("停止扩展核验，保留已完成证据")


def alarm_handler(signum, frame):
    raise TimeoutError("核验达到四分钟上限")


def load_module(name, path):
    specification = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def close(actual, expected, tolerance=1e-8):
    assert np.allclose(actual, expected, rtol=tolerance, atol=tolerance), (actual, expected)


def frozen_prediction(case, record, physical):
    degree, gain_degree, penalty = record["响应设置_背景阶_增益阶_惩罚"]
    background = np.vander(case["abscissa"], degree + 1, increasing=True)
    weights = np.ones((len(background), 1)) if gain_degree == 0 else np.column_stack(
        ((1 - case["abscissa"]) / 2, (1 + case["abscissa"]) / 2))
    predictions = []
    for angle, response in enumerate(record["各角响应"]):
        columns = physical[angle, :, None] * weights
        normalized = (columns - background @ np.asarray(response["物理投影系数"])) / response["物理列尺度"]
        predictions.append(response["训练响应均值_比例"] + response["训练响应尺度_比例"] * (
            background @ response["背景系数"] + normalized @ response["标准化非负增益"]))
    return np.asarray(predictions)


def verify_supplement(supplement_path, supplement, fair):
    engine = load_module("problem3_frozen_engine", ROOT / "求解/问题3/升格3/求解.py")
    inputs = engine.load_inputs()
    cases = {}
    records = {(row["材料"], row["折号"]): row for row in fair["案例"]}
    for key, record in records.items():
        material, fold = key
        case = engine.build_case(material, inputs["tables"], inputs["selected"] if fold else inputs["full_indices"],
                                 inputs["edges"], inputs["folds"][fold - 1] if fold else None)
        assert case["rows"][case["train"]].tolist() == record["训练源行"]
        cases[key] = case
    frozen_errors = []
    frozen_summaries = []
    for index, row in enumerate(supplement["固定返回阶数对照"]):
        key = row["材料"], row["折号"]
        case, record = cases[key], records[key]
        anchor_model = row["锚点模型"]
        other_model = "两束" if anchor_model == "完整往返" else "完整往返"
        anchor, other = record[anchor_model], record[other_model]
        assert row["固定五参数"] == anchor["参数向量"]
        assert row["固定响应"] == anchor["各角响应"]
        members = np.flatnonzero(np.isin(case["rows"], row["源行"]))
        assert case["rows"][members].tolist() == row["源行"]
        physical = {model: engine.fields(anchor["参数向量"], case, model == "完整往返") for model in engine.MODELS}
        frozen = {model: frozen_prediction(case, anchor, physical[model]) for model in engine.MODELS}
        config = tuple(row["响应设置"])
        response_only = engine.evaluate(anchor["参数向量"], case, other_model, config)["prediction"]
        destination = frozen_prediction(case, other, engine.fields(other["参数向量"], case, other_model == "完整往返"))
        predictions = [frozen[anchor_model], frozen[other_model], response_only, destination]
        losses = np.asarray([np.mean(((predicted[:, members] - case["values"][:, members]) /
                                      case["scales"][:, None]) ** 2, axis=1) for predicted in predictions])
        decomposition = row["收益路径分解"]
        expected = np.asarray(decomposition["四节点标准化MSE_逐角"])
        close(losses, expected)
        contributions = losses[:-1] - losses[1:]
        for part, name in enumerate(("仅返回阶数贡献_逐角", "响应重估贡献_逐角", "物理参数重估贡献_逐角")):
            close(contributions[part], decomposition[name])
        close(losses[0] - losses[-1], decomposition["总收益_逐角"])
        close(losses[0] - losses[-1] - contributions.sum(axis=0), decomposition["恒等式残差_逐角"])
        for model in engine.MODELS:
            close(np.sqrt(np.mean(((frozen[model][:, members] - case["values"][:, members]) /
                                  case["scales"][:, None]) ** 2, axis=1)), row["冻结响应均方根误差_逐角"][model])
        frozen_errors.append(float(np.max(np.abs(losses - expected))))
        if row["点集"].startswith("测试块"):
            frozen_summaries.append({"来源序号": index, "材料": row["材料"], "折号": row["折号"],
                "锚点模型": anchor_model, "点集": row["点集"], "仅返回阶数贡献_逐角": contributions[0].tolist(),
                "总收益_逐角": (losses[0] - losses[-1]).tolist()})
    REPORT["冻结阶数核验"] = {"记录数": len(frozen_errors), "四节点MSE代入最大差": max(frozen_errors),
        "测试块记录": frozen_summaries, "解释": "冻结响应独立代入并重聚合；同参数响应重估复用正式响应拟合，不宣称独立寻优"}
    contribution_summaries = []
    for material in ("硅", "碳化硅"):
        for fold in (1, 2):
            for anchor in ("两束", "完整往返"):
                selected = [row["收益路径分解"] for row in supplement["固定返回阶数对照"]
                            if row["材料"] == material and row["折号"] == fold
                            and row["锚点模型"] == anchor and row["点集"].startswith("测试块")]
                contribution_summaries.append({"材料": material, "折号": fold, "锚点模型": anchor,
                    **{name.replace("_逐角", "_角块等权均值"): float(np.mean([row[name] for row in selected]))
                       for name in ("仅返回阶数贡献_逐角", "响应重估贡献_逐角", "物理参数重估贡献_逐角", "总收益_逐角")}})
    REPORT["冻结贡献汇总"] = contribution_summaries
    checkpoint("四节点收益分解实物核验完成")
    summaries = []
    all_visited = []
    for material, field in (("硅", "成对单元"), ("碳化硅", "碳化硅成对单元")):
        members = supplement[field]
        maximum_loss_difference = 0.0
        maximum_violation = 0.0
        request_counts = 0
        for row in members:
            case = cases[row["材料"], row["折号"]]
            saved = read_json(supplement_path.parent / row["文件"])
            for name in row:
                if name in saved:
                    assert row[name] == saved[name], (row["文件"], name)
            if not row["优化器返回终点"]:
                assert all(row[name] is None for name in ("终点参数", "终点约束", "终点求解器目标"))
            if row["保留点对应收敛终点"]:
                assert row["优化器收敛"] and row["优化器返回终点"]
                close(row["保留最优"]["参数向量"], row["终点参数"], 1e-4)
            candidate = row["保留最优"]
            parameters = np.asarray(candidate["参数向量"])
            assert engine.feasible(parameters, case)
            close(parameters[row["固定参数索引"]], row["固定参数值"])
            recomputed = engine.evaluate(parameters, case, row["模型"], tuple(candidate["响应设置_背景阶_增益阶_惩罚"]))
            difference = abs(recomputed["loss"] - candidate["训练惩罚后目标"])
            close(recomputed["loss"], candidate["训练惩罚后目标"])
            close(candidate["训练惩罚后目标"], min(visit["训练目标"] for visit in saved["全部访问"]))
            maximum_loss_difference = max(maximum_loss_difference, difference)
            maximum_violation = max(maximum_violation, row["保留最优约束"]["最大约束违反量"])
            assert row["保留最优约束"]["最大约束违反量"] <= 1e-8
            request_counts += len(saved["全部访问"])
            if material == "硅":
                all_visited.extend(visit["可行参数"][0] for visit in saved["全部访问"])
        summaries.append({"材料": material, "成员数": len(members), "目标评估记录数": request_counts,
            "求解器报告收敛数": sum(row["优化器收敛"] for row in members),
            "保留点对应收敛终点数": sum(row["保留点对应收敛终点"] for row in members),
            "停止原因计数": dict(Counter(row["停止原因"] for row in members)),
            "保留目标重算最大差": maximum_loss_difference, "保留点最大约束违反量": maximum_violation})
        checkpoint(f"{material}逐成员目标及终点核验完成")
    REPORT["成对成员核验"] = summaries
    close([min(all_visited), max(all_visited)], supplement["硅折2已算可行候选包络_um"])
    REPORT["探索跨度核验"] = {"所有可行访问最小厚度_um": min(all_visited), "所有可行访问最大厚度_um": max(all_visited),
        "含义": "全部可行目标访问点的跨度，包含盒边界和较差目标；不是近优分支包络或概率区间，不能替换冻结正式范围"}
    stable_counts = []
    for field, member_field in (("同起点分级稳定性", "成对单元"), ("碳化硅同起点分级稳定性", "碳化硅成对单元")):
        lookup = {(row["材料"], row["折号"], row["起点序号"], row["模型"], row["情景"], row["共同评估上限"]): row
                  for row in supplement[member_field]}
        for comparison in supplement[field]:
            key = tuple(comparison[name] for name in ("材料", "折号", "起点序号", "模型", "情景"))
            older, newer = [lookup[(*key, level)] for level in comparison["评估上限前后"]]
            close(older["实际初值"], newer["实际初值"])
            thickness_delta = newer["保留最优"]["厚度_um"] - older["保留最优"]["厚度_um"]
            objective_delta = newer["保留最优"]["训练惩罚后目标"] - older["保留最优"]["训练惩罚后目标"]
            close(thickness_delta, comparison["厚度差_um"])
            close(objective_delta, comparison["训练目标差"])
            stable = (older["保留点对应收敛终点"] and newer["保留点对应收敛终点"] and abs(thickness_delta) <= 0.01
                      and abs(objective_delta) <= 1e-6 * max(1, abs(newer["保留最优"]["训练惩罚后目标"])))
            assert bool(stable) == comparison["局部数值稳定"]
        stable_counts.append({"项目": field, "比较数": len(supplement[field]),
                              "稳定数": sum(row["局部数值稳定"] for row in supplement[field])})
    REPORT["同起点稳定性核验"] = stable_counts
    pair_rows = []
    for index, row in enumerate(supplement["同起点控制分解"]):
        control = read_json(supplement_path.parent / row["控制记录"])
        perturbed = read_json(supplement_path.parent / row["扰动记录"])
        free = [position for position in range(5) if position not in control["固定参数索引"]]
        close(np.asarray(control["实际初值"])[free], np.asarray(perturbed["实际初值"])[free])
        difference = perturbed["保留最优"]["厚度_um"] - control["保留最优"]["厚度_um"]
        close(difference, row["扣除控制厚度变化_um"])
        assert row["成对收敛"] == (control["保留点对应收敛终点"] and perturbed["保留点对应收敛终点"])
        if row["共同评估上限"] == 600:
            pair_rows.append({"来源序号": index, "模型": row["模型"], "起点序号": row["起点序号"],
                "控制厚度_um": control["保留最优"]["厚度_um"], "扰动厚度_um": perturbed["保留最优"]["厚度_um"],
                "扣除控制厚度变化_um": difference, "相对控制变化_百分比": 100 * difference / control["保留最优"]["厚度_um"],
                "成对收敛": row["成对收敛"], "控制是本模型未扰动条件": row["控制是本模型未扰动条件"]})
    REPORT["同起点控制核验"] = pair_rows
    REPORT["补查汇总"] = {"成对成员总数": sum(row["成员数"] for row in summaries),
        "硅原正式次折训练目标": records["硅", 2]["完整往返"]["训练惩罚后目标"],
        "本轮完整场同起点收敛控制训练目标": next(row["控制目标"] for row in supplement["同起点控制分解"]
            if row["模型"] == "完整往返" and row["起点序号"] == 0 and row["共同评估上限"] == 600)}


def finish(declaration, supplement_path):
    paired = {"逐项": [
        {"id": "审-1-06", "裁定": "已消解", "理由":
         "算侧原不对称搜索与缺少冻结阶数对照已消解：正式源码与结果哈希相符，两模型共享五参数族、训练响应配置、初值及评估上限；本轮44条双向锚点记录的冻结参数、响应、四节点误差及加和恒等式逐项核对，MSE最大差1.7763568394002505e-14。该结论只消解公平归因缺口，不保证主方法赢基线；正文4.3、附录8.3/8.4待文腿同步，不计本次FAIL。"},
        {"id": "审-1-23", "裁定": "已消解", "理由":
         "原正式灵敏度与原附件复核一致：零损耗相对增减的请求值、实际固定值和重估值均为0且厚度差为0；绝对0→0.1、0→0.2独立列项，总变化等于控制推进加扣控制差。该标签修复保持有效；如正文5.2、附录8.4或旧损耗图表仍需更新，待文腿同步。"},
        {"id": "审-2-04", "裁定": "未消解", "理由":
         "缺少实算产物这一子项已消解：硅126、碳化硅54个分级成员全部存在，保留目标重算差均为0，截断终点为空而非伪造末次请求点。保留点对应收敛终点分别44与5个，同起点跨级稳定分别24/84与3/36；这些计数仅作诊断，不要求所有起点收敛。关键完整场同起点收敛对从16.047703443012995变至13.827618418020297 μm，扣控制-2.2200850249926987 μm，与历史未收敛正向变化反向。主线仍保留原未收敛候选和旧包络，尚未完成近优可行候选整合、正式选择与全折重评分，数值可靠性未消解。正文和图未同步不在此项否决依据中。"}],
        "相对判断": "更好", "决定性理由":
        "只比较上一轮解读的实物：此前新增补查为空，本轮已有44条冻结对照和180个成对成员，目标、终点与约束已核对且部分同起点比较稳定；八项正式头条及原逐块预测未变。证据完整性改善，不虚称预测精度提高。"}
    save_json(ROOT / "交接/配对裁定_问题3_回炉轮3.json", paired)
    REPORT["配对评审"] = paired
    REPORT["诊断评分"] = {"分数": 6.5, "锚点": "公平归因和实算证据更完整；仍有基线、跨折及条件稳定性缺陷，未到评委不扣分标准",
                          "说明": "先逐项裁定、再给相对判断，最后评分；不代替正确性硬判据"}
    checkpoint("配对裁定及相对判断完成")
    citations = []

    def cite(label, path, key):
        value = read_json(path)
        for component in key.split("/"):
            value = value[int(component)] if isinstance(value, list) else value[component]
        assert isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        citations.append({"指标": label, "数值": value, "来源文件": str(path.relative_to(ROOT)), "键名": key})
        return value

    for name, value in declaration["核心指标"].items():
        assert cite(name, RESULT / "厚度结果.json", f"核心指标/{name}") == value
    frozen_self = read_json(RESULT / "声明自检指标.json")["自检指标"]
    self_metrics = {}
    for name in frozen_self:
        label = "冻结主线" + name if "最坏扰动" in name else name
        self_metrics[label] = cite(label, RESULT / "声明自检指标.json", f"自检指标/{name}")
    selected_pair = next(index for index, row in enumerate(REPORT["同起点控制核验"])
                         if row["模型"] == "完整往返" and row["起点序号"] == 0)
    supplemental_citations = {
        "本轮冻结对照记录数": "冻结阶数核验/记录数",
        "本轮冻结四节点代入最大差": "冻结阶数核验/四节点MSE代入最大差",
        "本轮成对成员总数": "补查汇总/成对成员总数",
        "硅补查成员数": "成对成员核验/0/成员数",
        "硅补查保留点收敛数": "成对成员核验/0/保留点对应收敛终点数",
        "碳化硅补查成员数": "成对成员核验/1/成员数",
        "碳化硅补查保留点收敛数": "成对成员核验/1/保留点对应收敛终点数",
        "硅同起点跨级比较数": "同起点稳定性核验/0/比较数",
        "硅同起点跨级稳定数": "同起点稳定性核验/0/稳定数",
        "碳化硅同起点跨级比较数": "同起点稳定性核验/1/比较数",
        "碳化硅同起点跨级稳定数": "同起点稳定性核验/1/稳定数",
        "硅次折同起点收敛控制厚度_um": f"同起点控制核验/{selected_pair}/控制厚度_um",
        "硅次折同起点收敛扰动厚度_um": f"同起点控制核验/{selected_pair}/扰动厚度_um",
        "硅次折同起点收敛扣控制变化_um": f"同起点控制核验/{selected_pair}/扣除控制厚度变化_um",
        "硅次折同起点收敛相对控制变化_百分比": f"同起点控制核验/{selected_pair}/相对控制变化_百分比",
        "硅本轮所有可行访问厚度下界_um": "探索跨度核验/所有可行访问最小厚度_um",
        "硅本轮所有可行访问厚度上界_um": "探索跨度核验/所有可行访问最大厚度_um",
        "硅首折冻结两束仅增加返回阶数的标准化MSE下降": "冻结贡献汇总/0/仅返回阶数贡献_角块等权均值",
        "硅次折冻结两束仅增加返回阶数的标准化MSE下降": "冻结贡献汇总/2/仅返回阶数贡献_角块等权均值",
        "完整场反射率覆盖点数": "覆盖复核/覆盖点数",
        "完整场反射率测试点数": "覆盖复核/测试点数",
        "原始观测与预测表观测最大差": "独立算术核验/误差核验/原附件与预测表观测最大差",
        "逐角块误差独立复算最大差": "独立算术核验/误差核验/逐块误差独立复算最大差",
        "闭式与有限项复场最大差": "独立算术核验/误差核验/闭式与六十四项场最大差",
        "硅原正式次折训练目标": "补查汇总/硅原正式次折训练目标",
        "本轮完整场同起点收敛控制训练目标": "补查汇总/本轮完整场同起点收敛控制训练目标"}
    for label, key in supplemental_citations.items():
        value = cite(label, REPORT_PATH, key)
        if label.startswith("硅次折同起点收敛") or label in (
                "硅补查成员数", "硅补查保留点收敛数", "碳化硅补查成员数", "碳化硅补查保留点收敛数"):
            self_metrics[label] = value
    for material in ("硅", "碳化硅"):
        for fold in (1, 2):
            for model in ("两束", "训练均值", "二次趋势"):
                label = f"{material}折{fold}相对{model}改善_百分比"
                cite(label, RESULT / "公平对照.json", f"分材料验证/{material}/逐折汇总/{fold - 1}/完整相对基线下降_%/{model}")
    declaration["自检指标"] = self_metrics
    for key in declaration["口径说明"]:
        scope = "〔版本边界〕本键仅复现2026-09-10原正式主线冻结估计器，不是本轮全部探索点的全局最优声明；成对补查独立列为自检，不自动混入本键。"
        declaration["口径说明"][key] = scope + declaration["口径说明"][key]
    for key in ("硅已计算条件包络下限_um", "硅已计算条件包络上限_um"):
        declaration["口径说明"][key] += "本轮0.5至40 μm是全部可行访问点跨度，包含较差目标及盒边界，不是按同一近优规则筛选的包络；不把它混作本键，也不把本键称截至本轮所有候选的范围。"
    declaration["置信"] = {"等级": "低", "理由":
        "原始数据和角块误差复算一致，44条冻结对照及180个成对成员已核对；但硅次折较训练均值劣化11.30%，同起点收敛衬底扰动仍使厚度下降13.83%，近优候选尚未形成稳定的正式选择和全折验证闭环，故只将点值及描述性包络解释为冻结模型条件下的估计。"}
    save_json(ROOT / "交接/结果声明_问题3.json", declaration)
    rework = [{"对应": "审-2-04", "级别": "正确性", "目标": "算",
        "定位": f"{supplement_path.relative_to(ROOT)}:分级汇总、同起点控制分解、碳化硅同起点分级稳定性；求解/问题3/结果/公平对照.json:分材料验证/硅/逐折汇总/1；求解/问题3/结果/厚度结果.json:核心指标",
        "问题": "协议2、3、5仍不过，但不是缺少补查：44条冻结对照与180个成员已核验。正式硅次折相对均值、两束分别劣化11.301029489189297%、10.320926260535511%，跨折收益反向；原正式次折训练目标0.2884353550697428，新可行收敛控制已到0.288208431333304，但尚未按统一规则整合并冻结新主线。完整场同起点收敛控制/扰动厚度为16.047703443012995/13.827618418020297 μm，变化-2.2200850249926987 μm，与旧未收敛正向变化不一致。当前问题是估计与验证闭环未完成，不是所有起点都必须收敛，也不是正文或图未同步。",
        "指令": "保留已有44条对照和180个成员，不重复把执行入口存在当成进展。将同一五参数族内可行的新旧候选按预先说明的训练目标与近优容差整合，分别检查原自由参数主线的最优分支和固定条件收敛对，不能因较优点未收敛而删除，也不能把固定条件点误作无条件真厚度；围绕仍竞争的近优分支有限追加精修/剖面，给出停止与约束、稳定分支和剩余歧义。只用训练数据调整弱识别/响应控制，然后冻结选择，对全部既有角块重新输出主方法与均值、二次趋势、两束误差及方向；这些旧测试只称探索性。按明确规则构造更新后的条件范围，不用全部试探点0.5—40范围冒充不确定性区间。审-1-06公平归因子项已消解，不重开；文图交后续阶段同步。",
        "验收": "下一轮必须产出：厚度结果.json:核心指标中的硅全量及两折点值、硅条件范围两端、碳化硅正式值及范围两端，八键均为真实数值；公平对照.json:分材料验证中的两材料各折各角各块主方法和三基线的误差、改善与方向；灵敏度.json或明确的新结果中的已冻结分支、控制/扰动点值、扣控制差及区间、停止状态和约束余量。若数值仍未达质量标尺，继续报告最佳已算条件估计、包含竞争分支的范围和具体局限并保留FAIL，不清空答案；不得只交说明或要求正文同步。"}]
    save_json(ROOT / "交接/返工单_问题3.json", rework)
    rework_text = "# 问题3回炉轮3返工单\n\nFAIL。前置硬门通过；量纲、防泄漏通过，基线、交叉印证、灵敏度不通过。\n\n"
    rework_text += "## 已消解，不重复返工\n\n审-1-06算侧公平性及冻结返回阶数对照已消解；审-1-23零损耗标签保持已消解。正文4.3/5.2、附录8.3/8.4和相应图表待文腿同步，不属于本次FAIL理由。\n\n"
    rework_text += "## 审-2-04：从已算证据完成数值闭环\n\n"
    for field in ("定位", "问题", "指令", "验收"):
        rework_text += f"**{field}**：{rework[0][field]}\n\n"
    (ROOT / "交接/返工单_问题3.md").write_text(rework_text, encoding="utf-8")
    citation_table = "\n".join(f"|{row['指标']}|{row['数值']}|`{row['来源文件']}`|`{row['键名']}`|" for row in citations)
    baseline_rows = []
    fair = read_json(RESULT / "公平对照.json")
    for material in ("硅", "碳化硅"):
        for fold in (1, 2):
            values = fair["分材料验证"][material]["逐折汇总"][fold - 1]["完整相对基线下降_%"]
            baseline_rows.append(f"|{material}，折{fold}|{values['训练均值']:.4f}%|{values['二次趋势']:.4f}%|{values['两束']:.4f}%|")
    text = """# 问题3结果解读：回炉轮3重新核验

## 结论与本轮证据

**FAIL：前置产物硬门通过；量纲与防泄漏通过，基线、交叉印证与灵敏度未通过。** 本轮有实际新计算，不能沿用“新增补查未执行”的旧结论；质量未过不等于不作答。

本轮执行日志 `日志/执行_回炉_轮3_问3.log` 与配套 `rc=0` 标记相符；“有限预算补查停止”是程序完成状态，不代表所有数值点已收敛。原正式日志 `日志/执行_问题3_升格优胜.log` 显示正常结束。本轮解读复核也正常结束，证据为 `日志/执行_问题3_回炉轮3解读核验.log` 与 `求解/问题3/结果/解读核验_回炉轮3.json`。

补查结果位于 `SUPPLEMENT_PATH`。已核验44条冻结返回阶数对照、180个分级成对成员，逐成员目标与保存值一致，四节点标准化MSE重建最大差为1.7763568394002505e-14。补查源证据哈希与当前正式结果相同，八项冻结头条未变。此处将算侧实物与文图同步分开，不以正文、附录或图未同步作为FAIL理由。

## 配对裁定先于评分

- **审-1-06：已消解（算侧）。** 共享参数族、响应配置、初值及评估上限真实存在；固定参数和全部响应后只切返回阶数的对照及四节点分解已实算。原公平性问题已解决，不把“方法必须变好”追加成该历史条目的验收条件。当前基线和跨折质量仍按五项协议单独判定。
- **审-1-23：已消解（算侧）。** 零损耗相对扰动仍为零，绝对增加另列，实际参数及控制变化核验一致。
- **审-2-04：未消解。** 缺少执行证据的子项已修复；竞争近优分支、正式候选选择及扰动可靠性尚未形成完整闭环。下一轮沿用该编号处理，不再重复要求已经完成的对照。
- **相对判断：更好。** 上一版缺少新增补查实数，本轮已有完整对照与部分稳定条件对；改善的是证据完整性，不能说正式预测精度已提高。其后给诊断分6.5，仅作诊断，不替代FAIL。
- 正文4.3、5.2、附录8.3/8.4及对应图表**待文腿同步**；结构化裁定见 `交接/配对裁定_问题3_回炉轮3.json`。

## 数值答案与解释

### 硅：冻结主线给出厚度，新补查解释不稳定来源

当前冻结主线的全量双角完整往返条件厚度为 **2.165120838064207 μm**，其原定义条件包络为 **[1.732058431974077, 19.968296377341588] μm**；两个留段条件点分别为 **1.7321417462264854 μm**、**16.042358431815565 μm**。不取两折平均代替全量点值。全量两束为 **2.3193002615505804 μm**，完整场相对变化 **-6.6476698184518845%**，这是条件模型差而不是真实厚度误差。

这些头条仍按原冻结估计器复现，不宣称是本轮所有探索点的全局最优。新补查中的可行收敛控制训练目标 **0.288208431333304** 已低于原次折 **0.2884353550697428**，但它还未按公开选择规则进入完整主线并完成全部角块重评分。不能直接拿候选的较低训练损失替代测试性能。

### 基线收益必须分材料、分折报告

下表为完整场相对基线的标准化RMSE下降比例，正值为改善，负值为劣化；每折角和块等权，误差尺度只从该折训练反射率残差求得。

|材料与折|相对训练均值|相对二次趋势|相对两束|
|---|---:|---:|---:|
BASELINE_ROWS

硅首折改善不抵消次折劣化；碳化硅相对两束也有跨折反向。不得用两材料总均值或朴素预测标签的误差代替主模型性能。逐附件判断仍为：与多次返回条件相容，但高阶可观测收益不持续同向；这不是证明多光束不存在。

### 新冻结对照将返回阶数与响应、参数重估分开

以两束候选为锚点，固定五物理参数与全部响应，只增加返回阶数，硅首折标准化MSE的角块等权下降为 **0.009289389505148878**，次折为 **-0.2895567572048651**。因此，不能再把“误差差额”统称高阶返回带来的收益。新增四节点分解分别记录仅切返回阶数、重估训练响应、重估物理参数的贡献；双向锚点分解依赖所选起点，不作因果效应解释。这些MSE差不能与前表RMSE改善百分比混用。

### 成对重估已有进展，但不能把执行完整等同收敛

硅 **126** 个成员中 **44** 个保留点对应收敛终点；碳化硅 **54** 个成员中为 **5** 个。同起点跨级比较分别有 **24/84** 与 **3/36** 标为局部稳定。收敛计数仅为诊断，不要求全部起点收敛才允许给出答案；还要看竞争近优分支和实际使用的点。

硅次折完整场同起点、同固定规则的已收敛条件对，控制厚度为 **16.047703443012995 μm**，衬底对比乘以1.2后为 **13.827618418020297 μm**，扣控制变化 **-2.2200850249926987 μm**，相对控制为 **-13.834284967169559%**。这是已收敛的局部条件响应，不是唯一材料灵敏度。它与原冻结主线未收敛“最坏扰动”的正向 **24.472324080105423%**、扣控制 **3.9257384455260222 μm** 方向相反，旧数只能作为历史有限重估诊断保留，不再当成本轮稳定的最坏物理响应。

本轮另报的 **[0.5, 40.0] μm** 来自全部可行目标访问点，包含较差目标和参数盒边界，**不是不确定性区间**。原正式条件包络也不代表本轮所有试探点的范围。须先明确近优分支筛选与条件集合，再更新范围，不能以这两个不同集合互相替换。

### 碳化硅：保留问题二正式值

正式厚度为 **10.80351642616071 μm**，正式条件范围为 **[0.6366538383706806, 18.106446399344016] μm**，本问原样承接问题二。高阶收益未形成持续同向支持，修正条件未触发；不能拿本问诊断候选代替该基准。新增碳化硅搜索记录不改变此承接规则。

## 五项正确性

|协议|裁定|依据|
|---|---|---|
|量纲与数量级|通过|厚度单位、百分数转比例、训练尺度和角块误差核对一致；模型预测范围未见超界。|
|基线|不通过|正式硅次折同时劣于训练均值与两束，新候选尚未提供替换后全折预测结果。|
|交叉印证|不通过|闭式与有限级数一致，但高阶收益跨折反向；新的固定阶数对照进一步保留了这种差异。|
|防泄漏|通过（执行级）|源行重建确认内外训练、校准、测试隔离及保护带；新条件对共用训练信息。原测试参与开发，所有复查仅称探索性。|
|灵敏度|不通过|关键局部条件响应仍显著变化，并与历史未收敛响应反向；竞争近优分支与正式估计尚未统一。不是因为缺文件或文图未同步。|

观测轴为波数而不是时间，两侧训练波段不等于未来信息泄漏。已复核 `prepare_case`、`inner_partitions`、`choose_config`、`score_case` 的训练尺度、选型和评分分工。原始观测逐项一致；逐角块误差独立复算最大差为 **4.4941828036826337e-13**，闭式与有限项复场最大差为 **5.566705740848049e-16**。新候选目标重算复用正式响应算法，只证明存储与代入一致，不冒称独立全局寻优。

## 论文引用清单

下列值均已逐文件逐键读取核对。数组索引从零开始；表中保存原始精度，论文按统一规则舍入。核心仅列附件条件估计；误差、覆盖和补查表现属于自检，不作为红队不同方法之间的同名头条比较。

|指标|数值|来源文件|键名|
|---|---:|---|---|
CITATION_TABLE

## 局限与下游使用

- 全量点值与包络是冻结模型条件估计，不是唯一真厚度或概率置信区间；新探索跨度与旧条件包络严格分列。
- 完整场反射率经验覆盖 **473/640=0.7390625**；匹配合成条件的厚度包络包含率 **0.1111111111111111**。二者均为方法自检，不能冒充实测厚度真值覆盖。
- 未知光学常数、仪器分辨率、相干条件以及弱识别继续限制物理解释；测试集已参与开发，不能声称新增独立泛化检验。
- **唯一置信母本为 `交接/结果声明_问题3.json:置信`**。撰稿仅在本问结论句按母本写一次保留话，其余按已算事实陈述。
- 下一轮围绕审-2-04完成候选整合、有限稳定性核查和全角块重评分；无论是否达到质量标尺，始终给出最佳已算条件点值、明确定义的范围和具体局限，不清空数值答案。
"""
    text = text.replace("SUPPLEMENT_PATH", str(supplement_path.relative_to(ROOT)))
    text = text.replace("BASELINE_ROWS", "\n".join(baseline_rows)).replace("CITATION_TABLE", citation_table)
    (ROOT / "交接/结果解读_问题3.md").write_text(text, encoding="utf-8")
    REPORT["论文引用逐键核对"] = citations
    REPORT["自检指标"] = self_metrics
    events_path = ROOT / "交接/实验记录.json"
    events = read_json(events_path)
    previous_count = len(events)
    additions = [
        {"类别": "科学尝试", "问题": 3, "尝试": "对冻结两束候选只增加返回阶数，重新按测试角块等权汇总误差",
         "现象": "硅首折标准化MSE下降0.009289389505148878，次折下降-0.2895567572048651；单独的高阶项也没有保持跨折同向收益",
         "决定": "将返回阶数、训练响应重估和物理参数重估分开解释，不把总误差差额归于高阶反射",
         "依据": "求解结果:冻结贡献汇总"},
        {"类别": "科学尝试", "问题": 3, "尝试": "重新代入同一初值的完整场控制与衬底对比乘1.2条件，检查已收敛保留点的厚度差",
         "现象": "硅次折控制16.047703443012995微米，扰动13.827618418020297微米，扣控制下降2.2200850249926987微米；相对下降13.834284967169559%，与原未收敛正向响应反向",
         "决定": "报告局部条件响应及竞争分支，把原24.472324080105423%的正向变化保留为历史有限重估诊断而非稳定物理结论",
         "依据": "求解结果:同起点控制核验；求解结果:最坏扰动核验"},
        {"类别": "科学尝试", "问题": 3, "尝试": "逐项检查硅全部可行目标访问点的最小和最大厚度及其集合定义",
         "现象": "跨度0.5至40.0微米恰含参数盒两端，集合包含较差训练目标，不是统一近优筛选后的厚度包络",
         "决定": "将搜索访问跨度与冻结条件包络分列，拒绝把全部试探点范围当作不确定性区间",
         "依据": "求解结果:探索跨度核验"},
        {"类别": "流程事件", "问题": 3, "尝试": "完成问题3回炉轮3结果解读与逐项配对评审",
         "现象": "前置产物硬门通过；审-1-06算侧与审-1-23已消解，审-2-04仍未消解；相对判断更好，五项协议2、3、5仍不通过",
         "决定": "更新解读、八项非空核心声明与同编号返工意见，首行FAIL；正文和图待后续同步，不作本次否决理由",
         "依据": "交接/配对裁定_问题3_回炉轮3.json；求解/问题3/结果/解读核验_回炉轮3.json"}]
    forbidden = [line.strip() for line in (ROOT / "运行时/流程词.txt").read_text().splitlines()
                 if line.strip() and not line.startswith("#")]
    for event in additions:
        if event["类别"] == "科学尝试":
            fields = " ".join(event[key] for key in ("尝试", "现象", "决定", "依据"))
            assert not any(word in fields for word in forbidden), fields
        if event not in events:
            events.append(event)
    save_json(events_path, events)
    REPORT["交付检查"] = {"核心指标非空数": len(declaration["核心指标"]), "论文引用核验数": len(citations),
        "本次实验记录追加数": len(events) - previous_count, "科学尝试流程词检查": "通过",
        "声明顶层schema": list(declaration), "配对相对判断": paired["相对判断"], "文图未作为否决项": True}
    assert set(declaration) == {"问题", "核心指标", "口径说明", "自检指标", "置信"}
    assert set(declaration["核心指标"]) == set(declaration["口径说明"])
    assert all(isinstance(value, (int, float)) and math.isfinite(value) for value in declaration["自检指标"].values())
    checkpoint("引用、声明、返工单及实验追加验收完成")
    marker = ("FAIL\n前置硬门通过；量纲与防泄漏通过，基线、交叉印证及灵敏度未通过。\n"
              "已核验44条冻结对照与180个成对成员，保留八项真实条件数值答案。\n"
              "审-1-06算侧、审-1-23已消解；审-2-04未消解，相对判断更好。\n"
              "解读、声明、配对裁定、结构化返工单及实验记录已更新；文图待后续同步。\n")
    (ROOT / "日志/解读_回炉_轮3_问3.done").write_text(marker, encoding="utf-8")


def main():
    backup = ROOT / "日志/解读_回炉轮3_问3核验/修改前"
    backup.mkdir(parents=True, exist_ok=True)
    for name in ("结果声明_问题3.json", "结果解读_问题3.md", "返工单_问题3.json", "返工单_问题3.md"):
        source = ROOT / "交接" / name
        if source.exists() and not (backup / name).exists():
            (backup / name).write_bytes(source.read_bytes())
    assert RESULT.is_dir() and list(RESULT.glob("*.json"))
    declaration = read_json(ROOT / "交接/结果声明_问题3.json")
    fair = read_json(RESULT / "公平对照.json")
    thickness = read_json(RESULT / "厚度结果.json")
    log_path = ROOT / "日志/执行_回炉_轮3_问3.log"
    completion = json.loads(log_path.read_text().splitlines()[-1])
    assert (ROOT / "日志/执行_回炉_轮3_问3.done").read_text().strip() == "rc=0"
    assert completion["状态"].startswith("有限预算补查停止；")
    original_log = ROOT / "日志/执行_问题3_升格优胜.log"
    assert json.loads(original_log.read_text().splitlines()[-1])["状态"] == "正常结束"
    supplement_path = Path(completion["输出目录"]) / "补查结果.json"
    assert supplement_path.is_relative_to(ROOT)
    supplement = read_json(supplement_path)
    assert supplement["状态"] == completion["状态"]
    assert declaration["核心指标"] == thickness["核心指标"] == fair["核心指标"] == supplement["核心指标"]
    assert len(declaration["核心指标"]) == 8
    assert all(isinstance(value, (int, float)) and math.isfinite(value) for value in declaration["核心指标"].values())
    REPORT["前置硬门"] = {"结果目录存在且含JSON": True, "正式正常结束日志": str(original_log.relative_to(ROOT)),
        "本轮执行日志": str(log_path.relative_to(ROOT)), "本轮退出码": 0, "本轮状态": supplement["状态"],
        "本轮补查结果": str(supplement_path.relative_to(ROOT)), "核心指标逐键一致": True, "通过": True}
    REPORT["核心指标"] = declaration["核心指标"]
    checkpoint("前置硬门通过；五项正确性待核验")
    for name in ("公平对照", "灵敏度"):
        assert hashlib.sha256((RESULT / f"{name}.json").read_bytes()).hexdigest() == supplement["源证据SHA256"][name]
    assert hashlib.sha256((ROOT / "求解/问题3/升格3/求解.py").read_bytes()).hexdigest() == fair["实现SHA256"]
    assert hashlib.sha256((ROOT / "求解/问题3/求解_问题3.py").read_bytes()).hexdigest() == supplement["补查合同"]["补查实现SHA256"]
    audit = load_module("problem3_independent_audit", ROOT / "求解/问题3/核验_升格标准库.py")
    audit.SOURCE = RESULT
    REPORT["独立算术核验"] = audit.run_audit()
    previous = load_module("problem3_previous_audit_helpers", ROOT / "求解/问题3/核验解读_回炉轮2.py")
    previous.CURRENT = RESULT
    previous.REPORT = REPORT
    previous.check_predictions(fair, audit)
    previous.check_sensitivity(fair)
    summaries = []
    for material in ("硅", "碳化硅"):
        for fold in (1, 2):
            group = [row for row in REPORT["独立算术核验"]["逐块误差核验"] if row["材料"] == material and row["折号"] == fold]
            means = {model: float(np.mean([row["复核标准化均方根误差"] for row in group if row["模型"] == model]))
                     for model in ("两束", "完整往返", "训练均值", "二次趋势")}
            improvements = {model: 100 * (1 - means["完整往返"] / means[model]) for model in ("两束", "训练均值", "二次趋势")}
            saved = fair["分材料验证"][material]["逐折汇总"][fold - 1]
            for model in improvements:
                close(improvements[model], saved["完整相对基线下降_%"][model])
            summaries.append({"材料": material, "折号": fold, "误差重聚合": means, "改善重聚合_百分比": improvements})
    REPORT["逐折复核"] = summaries
    checkpoint("正式数值、原始附件、留段、基线及覆盖核验完成")
    verify_supplement(supplement_path, supplement, fair)
    REPORT["五项正确性"] = {
        "量纲与数量级": {"通过": True, "理由": "厚度单位与窗口明确，反射率比例及标准化误差独立代入一致，负收益未隐藏"},
        "基线": {"通过": False, "理由": "冻结主方法硅次折相对均值和两束仍分别劣化11.3010%与10.3209%；新增对照未产生替换后的完整预测验证"},
        "交叉印证": {"通过": False, "理由": "有限级数与闭式一致，但高阶预测收益跨折反向；四节点分解只解释现存候选，不能代替一致的预测收益"},
        "防泄漏": {"通过": True, "理由": "谱域留段不是时间预测；内层训练、外层保护带与校准测试隔离成立。已有测试参与开发，结果限定探索性，不称新独立检验"},
        "灵敏度": {"通过": False, "理由": "180项补查已实算，局部收敛有改善；同起点仅部分稳定，完整场收敛对的衬底扰动仍改变厚度，历史扰动方向不稳定"}}
    REPORT["质量裁定"] = "FAIL"
    REPORT["状态"] = "正常结束"
    checkpoint("五项正确性完成；质量FAIL但保留真实数值答案")
    finish(declaration, supplement_path)
    print(json.dumps({"状态": REPORT["状态"], "质量裁定": REPORT["质量裁定"], "前置硬门": True,
        "冻结记录数": REPORT["冻结阶数核验"]["记录数"], "成对成员核验": REPORT["成对成员核验"],
        "实际用时秒": REPORT["实际用时秒"]}, ensure_ascii=False))


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, alarm_handler)
    signal.alarm(TIME_LIMIT)
    try:
        main()
    except Exception as error:
        REPORT["状态"] = f"核验异常：{type(error).__name__}: {error}"
        REPORT["实际用时秒"] = time.monotonic() - STARTED
        save_json(REPORT_PATH, REPORT)
        events_path = ROOT / "交接/实验记录.json"
        events = read_json(events_path)
        events.append({"类别": "流程事件", "问题": 3, "尝试": "问题3回炉轮3解读核验", "现象": REPORT["状态"],
                       "决定": "保留阶段证据，查明异常后再给完整裁定", "依据": "求解结果:解读核验_回炉轮3/状态"})
        save_json(events_path, events)
        raise
    finally:
        signal.alarm(0)
