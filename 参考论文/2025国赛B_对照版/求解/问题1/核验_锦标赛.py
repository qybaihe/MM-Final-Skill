import fcntl
import hashlib
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import time


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "交接/锦标赛核验_问题1.json"
STARTED = time.monotonic()
LIMIT_SECONDS = 120.0


def check_budget():
    if time.monotonic() - STARTED >= LIMIT_SECONDS:
        raise TimeoutError("核验达到120秒上限，保留已完成证据，不追加未执行结论")


def read_json(path):
    check_budget()
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def save_report(report):
    report["核验用时秒"] = time.monotonic() - STARTED
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, OUTPUT)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def load_route(number):
    path = ROOT / f"求解/问题1/原型_路线{number}.py"
    specification = importlib.util.spec_from_file_location(f"tournament_route_{number}", path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def append_record(entry):
    words = (ROOT / "运行时/流程词.txt").read_text(encoding="utf-8").splitlines()
    content = "".join(entry[key] for key in ("尝试", "现象", "决定", "依据"))
    assert not [word for word in words if word and not word.startswith("#") and word in content]
    path = ROOT / "交接/实验记录.json"
    with (ROOT / "交接/.实验记录.lock").open("a", encoding="utf-8") as lock:
        while True:
            check_budget()
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.02)
        entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        assert isinstance(entries, list)
        entries.append(entry)
        temporary = path.with_suffix(".核验临时")
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)


def main():
    report = {"问题": 1, "状态": "核验中", "范围": "仅原型选型证据，不替代正式求解验收", "时间上限秒": LIMIT_SECONDS}
    save_report(report)
    try:
        reports = [read_json(f"求解/问题1/原型结果/路线{number}.json") for number in (1, 2, 3)]
        sources = [f"求解/问题1/原型结果/路线{number}.json" for number in (1, 2, 3)]
        report["输入指纹"] = {source: digest((ROOT / source).read_bytes()) for source in sources}
        report["运行证据"] = []
        errors_by_route = []
        truth_vectors = []
        for number, result in enumerate(reports, 1):
            status = (ROOT / f"日志/跑原型_问1_{number}.done").read_text().strip()
            text = (ROOT / f"日志/跑原型_问1_{number}.log").read_text()
            summaries = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
            assert status == "rc=0" and summaries
            summary = summaries[-1]
            log_metrics = summary.get("核心指标键值", summary.get("核心指标"))
            assert math.isclose(next(iter(log_metrics.values())), result["主指标值"], abs_tol=1e-14)
            assert not result["失败原因"]
            report["运行证据"].append({"路线": result["路线名"], "完成标记": status, "结果状态": result["运行状态"], "结果用时秒": result["实际用时秒"], "摘要打印时用时秒": summary["实际用时秒"], "计时说明": "采用结果最终实际用时秒；日志摘要可能打印于后续汇总之前"})
            rows = result["合成逐例"] if number == 3 else result["逐例结果"]
            assert len(rows) == 24
            truths = [row["合成真值"]["真厚度_微米"] if number == 1 else row["合成真厚度_微米"] if number == 2 else row["真厚度_微米"] for row in rows]
            estimates = [row["厚度_微米"] for row in rows]
            assert all(math.isfinite(value) and value > 0 for value in estimates)
            errors = [100 * abs(estimate - truth) / truth for estimate, truth in zip(estimates, truths)]
            recomputed = math.fsum(errors) / len(errors)
            assert math.isclose(recomputed, result["主指标值"], rel_tol=1e-12, abs_tol=1e-14)
            truth_vectors.append(truths)
            errors_by_route.append(errors)
            report.setdefault("逐路线复算", []).append({"名称": result["路线名"], "来源": sources[number - 1], "逐例数量": len(rows), "有限正厚度数量": len(estimates), "合成厚度平均相对误差_%": recomputed, "与原主指标绝对差": abs(recomputed - result["主指标值"]), "厚度最小值_微米": min(estimates), "厚度最大值_微米": max(estimates), "最大单例相对误差_%": max(errors)})
        assert truth_vectors[0] == truth_vectors[1] == truth_vectors[2]
        save_report(report)
        settings = read_json("交接/路线侦察.json")["共同原型协议"]["合成小样"]
        coordinates = reports[0]["样本索引"]["合成波数_cm^-1"]
        assert coordinates == reports[2]["样本索引"]["合成波数_每厘米"]
        first_route, second_route, third_route = [load_route(number) for number in (1, 2, 3)]
        loaded_report = second_route.initial_report()
        second_coordinates, second_settings = second_route.read_real_samples(loaded_report)
        assert coordinates == second_coordinates and settings == second_settings
        first_cases = list(first_route.synthetic_cases(coordinates, settings))
        second_cases, second_truths = second_route.make_cases(coordinates, settings)
        third_cases = third_route.synthetic_cases(coordinates, settings, third_route.Budget(STARTED))
        first_hashes = [digest(json.dumps(case[1], ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()) for case in first_cases]
        second_hash = digest(json.dumps({"波数": coordinates, "合成样本": second_cases}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        third_hash = digest(json.dumps(third_cases, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
        assert first_hashes == reports[0]["输入哈希"]["逐例合成样本"]
        assert second_hash == reports[1]["输入哈希"]["共同合成样本"]
        assert third_hash == reports[2]["输入哈希"]["共同合成小样哈希"]
        samples = [[list(itertools.chain.from_iterable(spectrum["反射率"] for spectrum in case[1])) for case in first_cases], [case["观测"] for case in second_cases], [list(itertools.chain.from_iterable(case["双角反射率"])) for case in third_cases]]
        maximum_difference = max(abs(left - right) for route in samples[1:] for reference, comparison in zip(samples[0], route) for left, right in zip(reference, comparison))
        assert maximum_difference < 1e-12
        report["共同输入核对"] = {"各自合成哈希重现": True, "三路线坐标一致": True, "三路线样本反射率最大绝对差_比例": maximum_difference, "比较数值容差_比例": 1e-12, "说明": "各路序列化结构及浮点运算不同，哈希不应直接跨路线比较；先分别重现原哈希，再逐观测比较"}
        report["附件及上游哈希核对"] = {}
        for filename, key in [("数据/附件1.xlsx", "附件1.xlsx"), ("数据/附件2.xlsx", "附件2.xlsx"), ("交接/数据档案.json", "数据档案")]:
            actual = digest((ROOT / filename).read_bytes())
            assert actual == reports[0]["输入哈希"][key] == reports[1]["输入哈希"][key] == reports[2]["输入哈希"][filename]
            report["附件及上游哈希核对"][filename] = actual
        save_report(report)
        scenarios = list(itertools.product(settings["厚度情景微米"], settings["折射率基值情景"], settings["色散斜率情景"], settings["噪声标准差情景"]))
        report["预设情景分组复算"] = []
        for dimension, label in enumerate(("厚度_微米", "折射率基值", "色散斜率", "噪声标准差_比例")):
            for value in sorted({scenario[dimension] for scenario in scenarios}):
                positions = [index for index, scenario in enumerate(scenarios) if scenario[dimension] == value]
                means = {result["路线名"]: math.fsum(errors[index] for index in positions) / len(positions) for result, errors in zip(reports, errors_by_route)}
                report["预设情景分组复算"].append({"分组维度": label, "情景值": value, "样本数": len(positions), "合成厚度平均相对误差_%": means, "最低者": min(means, key=means.get)})
        winner_error = reports[1]["主指标值"]
        report["优胜误差降幅_%"] = {reports[index]["路线名"]: 100 * (1 - winner_error / reports[index]["主指标值"]) for index in (0, 2)}
        report["路线三歧义"] = [{"案例": row["案例"], "真厚度_微米": row["真厚度_微米"], "主候选_微米": row["厚度_微米"], "近优候选_微米": row["求解"]["近优候选厚度_微米"]} for row in reports[2]["合成逐例"] if row["求解"].get("相位歧义")]
        assert len(report["路线三歧义"]) == reports[2]["辅助诊断"]["有歧义案例数"]
        report["胜者复现"] = []
        for case, stored in zip(second_cases, reports[1]["逐例结果"]):
            check_budget()
            optics = second_route.prepare_optics(coordinates, case["层折射率"], settings["角度度"])
            terms = second_route.objective_terms(optics, case["观测"])
            coarse = second_route.coarse_search(terms, STARTED + LIMIT_SECONDS - 5)
            estimate, loss, candidates, expected_count = second_route.refine_search(terms, coarse, STARTED + LIMIT_SECONDS - 5)
            assert len(coarse) == 96 and len(candidates) == expected_count
            assert math.isclose(estimate, stored["厚度_微米"], abs_tol=1e-10)
            report["胜者复现"].append({"案例": case["编号"], "厚度_微米": estimate, "与原厚度绝对差_微米": abs(estimate - stored["厚度_微米"]), "联合残差平方均值_比例平方": loss})
        report["真值隔离检查"] = {"范围": "静态调用链核对及胜者数值复现；不证明历史人工调参完全不存在", "结论": "三路线估计函数均仅使用坐标、观测、已知光学参数及预设搜索参数；真厚度在生成和评分阶段使用。胜者复现未向搜索函数传入真厚度。", "位置": ["原型_路线1.py:estimate_depth、worker", "原型_路线2.py:objective_terms、coarse_search、refine_search、score", "原型_路线3.py:estimate_thickness、execute"], "时序切分": "本问无时间轴预测和训练测试切分；不冒称已完成问题二三的留段防泄漏检验"}
        report["未完成的正式检验"] = ["计划中的naive基线尚不存在，本次路线对比不能替代", "已知折射率的合成误差不证明真实晶圆厚度精度", "独立种子、未知折射率、窗口及基线扰动尚需正式阶段补充", "单次耗时包含各自不同辅助诊断，不等同纯反演内核的性能排名", "未生成厚度统计区间，不把离散光学情景跨度视为置信区间"]
        report["状态"] = "原型选型证据核验完成"
        save_report(report)
        append_record({"类别": "科学尝试", "问题": 1, "尝试": "在相同的24组已知折射率双角度合成条件下，对峰序、双束界面场和连续相位的厚度误差按全部案例重新聚合，并逐观测比较三者输入。", "现象": f"三者平均绝对相对误差依次为{reports[0]['主指标值']:.12g}%、{winner_error:.12g}%、{reports[2]['主指标值']:.12g}%；输入反射率最大差为{maximum_difference:.12g}，连续相位有1例近优厚度歧义。", "决定": "以双束界面场为主模型，峰序与连续相位保留为独立对照；不把已知光学参数下的合成回收精度解释成真实晶圆厚度精度。", "依据": "求解结果:主指标值；求解结果:逐例结果；求解结果:合成逐例；求解结果:辅助诊断/有歧义案例数"})
        report["实验记录"] = "已追加科学尝试，保留既有数组"
        save_report(report)
        print(json.dumps({"状态": report["状态"], "误差降幅_%": report["优胜误差降幅_%"], "分组最低者": [entry["最低者"] for entry in report["预设情景分组复算"]], "共同输入": report["共同输入核对"], "胜者复现例数": len(report["胜者复现"]), "核验用时秒": report["核验用时秒"]}, ensure_ascii=False), flush=True)
    except Exception as error:
        report["状态"] = "核验未完成"
        report["错误"] = f"{type(error).__name__}: {error}"
        save_report(report)
        print(json.dumps({"状态": report["状态"], "错误": report["错误"], "核验用时秒": report["核验用时秒"]}, ensure_ascii=False), flush=True)
        raise


if __name__ == "__main__":
    main()
