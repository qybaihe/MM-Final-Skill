import csv
import hashlib
import json
import math
import shutil
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "日志/升格裁决_问3核验"
START = time.monotonic()
DEADLINE = START + 600
NAMES = ("主线", "升格1", "升格2", "升格3")


def remaining():
    if time.monotonic() >= DEADLINE:
        raise TimeoutError("核验达到十分钟上限；保留已写检查点")


def read(path):
    return json.loads((ROOT / path).read_text())


def write(path, data):
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(target)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot():
    WORK.mkdir(parents=True, exist_ok=True)
    for relative in ["求解/问题3/结果", "交接/结果声明_问题3.json", "交接/结果解读_问题3.md",
                     "交接/假设台账_问题3.json", "交接/仲裁_问题3.json",
                     "交接/返工单_问题3.json", "交接/返工单_问题3.md"]:
        source = ROOT / relative
        destination = WORK / "裁决前" / relative
        if not source.exists() or destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)


def folder(name):
    if name == "主线":
        return WORK / "裁决前/求解/问题3/结果"
    return ROOT / "求解/问题3" / name / "结果"


def load_candidate(name, filename):
    return json.loads((folder(name) / filename).read_text())


def gate():
    gates = []
    for name in NAMES:
        remaining()
        result = folder(name)
        files = sorted(result.glob("*.json"))
        for path in files:
            json.loads(path.read_text())
        state = load_candidate(name, "执行状态.json")
        log = ROOT / ("日志/执行_问题3_G2返工.log" if name == "主线"
                      else f"日志/跑升格_问3_{name[-1]}.log")
        text = log.read_text()
        normal = state.get("正常结束", False) or "正常结束" in state.get("状态", "")
        if name == "主线":
            normal = state.get("运行状态") == "已完成数值交付" and "正常结束" in text
        if name in ("升格1", "升格2"):
            summary = json.loads(text)
            normal = normal and summary.get("实际用时秒", 0) > 0 and bool(summary.get("核心指标"))
        if name == "升格3":
            summary = json.loads(text.strip().splitlines()[-1])
            normal = normal and summary.get("状态") == "正常结束"
        passed = bool(files) and normal and "Traceback" not in text
        gates.append({"方案": name, "结果JSON数": len(files), "全部JSON可解析": True,
                      "正常结束证据": str(log.relative_to(ROOT)), "日志SHA256": digest(log),
                      "通过": passed})
    write("日志/升格裁决_问3核验/前置硬门.json", {"逐方案": gates, "通过": all(row["通过"] for row in gates)})
    if not all(row["通过"] for row in gates):
        raise ValueError("前置硬门失败；禁止结果晋级")
    return gates


def comparisons():
    records = []
    for name in NAMES:
        remaining()
        fair = load_candidate(name, "同口径对照.json" if name == "升格1" else "公平对照.json")
        rows = []
        if name == "升格1":
            for entry in fair["逐折汇总"]:
                scores = entry["逐模型误差"]
                rows.append({"材料": entry["材料"], "折号": entry["折号"], "主方法": scores["所选模型"],
                             "低阶对照": scores["谐波1阶"], "高阶对照": scores["谐波3阶"],
                             "训练均值": scores["训练均值"], "二次趋势": scores["二次趋势"]})
        elif name == "升格3":
            for material, validation in fair["分材料验证"].items():
                for entry in validation["逐折汇总"]:
                    scores = entry["平均标准化均方根误差"]
                    rows.append({"材料": material, "折号": entry["折号"], "主方法": scores["完整往返"],
                                 "低阶对照": scores["两束"], "高阶对照": scores["完整往返"],
                                 "训练均值": scores["训练均值"], "二次趋势": scores["二次趋势"]})
        else:
            for entry in fair["案例"]:
                if not entry["折号"]:
                    continue
                scores = entry["标准化均方根误差"]
                rows.append({"材料": entry["材料"], "折号": entry["折号"], "主方法": scores["完整往返"],
                             "低阶对照": scores["两束"], "高阶对照": scores["完整往返"],
                             "训练均值": scores.get("训练均值", entry.get("均值基线误差")),
                             "二次趋势": scores.get("二次趋势", entry.get("二次趋势基线误差"))})
        for row in rows:
            for baseline in ["低阶对照", "训练均值", "二次趋势"]:
                row[f"主方法相对{baseline}改善_百分比"] = 100 * (1 - row["主方法"] / row[baseline])
        silicon = [row for row in rows if row["材料"] == "硅"]
        risk = max(row["主方法"] / min(row["训练均值"], row["二次趋势"]) for row in silicon)
        core = load_candidate(name, "厚度结果.json")["核心指标"]
        if name == "主线":
            core = {"硅全量完整往返厚度_um": fair["硅全量条件厚度_um"],
                    "硅已计算条件包络下限_um": fair["厚度条件范围_um"]["硅"][0],
                    "硅已计算条件包络上限_um": fair["厚度条件范围_um"]["硅"][1]}
        records.append({"方案": name, "逐材料逐折同口径误差": rows, "头条厚度": core,
                        "硅最坏折相对较优朴素基线风险比": risk,
                        "硅两折平均误差": sum(row["主方法"] for row in silicon) / 2,
                        "硅同时胜两朴素基线折数": sum(row["主方法"] < min(row["训练均值"], row["二次趋势"]) for row in silicon),
                        "严格基线协议通过": all(row["主方法"] < min(row["训练均值"], row["二次趋势"], row["低阶对照"]) for row in silicon)})
    return records


def numerical_audit():
    import runpy

    module = runpy.run_path(str(ROOT / "求解/问题3/核验_升格标准库.py"))
    report = module["run_audit"]()
    write("日志/升格裁决_问3核验/独立公式与评分核验.json", report)
    return report


def main():
    snapshot()
    gates = gate()
    comparison = comparisons()
    winner = min(comparison, key=lambda row: row["硅最坏折相对较优朴素基线风险比"])["方案"]
    preliminary = {"问题": 3, "裁决状态": "数值比较已完成，正确性及交付核验进行中", "暂定优胜方案": winner,
                   "前置硬门": gates, "候选比较": comparison,
                   "选择口径": "硅必须作答，碳化硅单独判断；无全过候选时最小化硅最坏折相对两种朴素基线中较优者的风险比，不混合材料掩盖坏折。此为本次风险导向裁决，不冒称原计划预注册的唯一排名。",
                   "验证边界": "固定原外层切分已用于开发；排名不代表独立泛化检验通过。"}
    decision_path = ROOT / "交接/升格裁决_问题3.json"
    if not decision_path.exists() or read(decision_path).get("裁决状态") != "已裁决":
        write("交接/升格裁决_问题3.json", preliminary)
    write("日志/升格裁决_问3核验/同口径比较.json", preliminary)
    audit = numerical_audit()
    print(json.dumps({"阶段": "硬门与同口径比较", "暂定优胜": winner,
                      "风险比": {row["方案"]: row["硅最坏折相对较优朴素基线风险比"] for row in comparison},
                      "独立核验": audit["误差核验"], "实际用时秒": time.monotonic() - START}, ensure_ascii=False))


if __name__ == "__main__":
    main()
