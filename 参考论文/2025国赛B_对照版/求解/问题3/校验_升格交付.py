import ast
import hashlib
import json
import math
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
START = time.monotonic()


def invalid_constant(value):
    raise ValueError(value)


def read(path):
    return json.loads((ROOT / path).read_text(), parse_constant=invalid_constant)


def require(condition, message):
    if time.monotonic() - START >= 120:
        raise TimeoutError("交付校验达到两分钟上限；已有产物保留")
    if not condition:
        raise AssertionError(message)


def main():
    statement = read("交接/结果声明_问题3.json")
    decision = read("交接/升格裁决_问题3.json")
    core = read("求解/问题3/结果/厚度结果.json")["核心指标"]
    source = read("求解/问题3/升格3/结果/厚度结果.json")["核心指标"]
    require(set(statement) == {"问题", "核心指标", "口径说明", "自检指标", "置信"}, "声明顶层schema")
    require(len(core) == 8 and all(isinstance(value, (int, float)) and math.isfinite(value) for value in core.values()), "八项非空真实数值")
    require(core == source == statement["核心指标"] == decision["同口径核心指标"], "四份头条一致")
    require(statement == read("求解/问题3/结果/结果声明_问题3.json"), "声明副本一致")
    require(set(core) <= set(statement["口径说明"]), "每个核心指标有口径")
    require("480" in statement["口径说明"]["碳化硅正式基准厚度_um"], "上游样本口径")
    require(statement["自检指标"] == read("求解/问题3/结果/声明自检指标.json")["自检指标"], "自检量来源一致")
    require(decision["优胜方案"] == "升格3" and decision["质量状态"] == "FAIL", "优胜不等于全过")
    require(sum(row["裁定"] == "通过" for row in decision["五项正确性协议"]) == 2, "协议通过数")
    for path in (ROOT / "求解/问题3/结果").glob("*.json"):
        read(path)
    require(read("求解/问题3/结果/公平对照.json") == read("求解/问题3/升格3/结果/公平对照.json"), "完整胜出原数值未改")
    for citation in read("求解/问题3/结果/论文引用核对.json")["逐项"]:
        value = read(citation["来源文件"])
        for key in citation["键名"]:
            value = value[key]
        require(value == citation["数值"], citation["指标"])
    arbitration = read("交接/仲裁_问题3.json")
    pending = [row for row in arbitration["逐项"] if row["应改方"] == "建模" and (row.get("消解状态") not in ("已消解", "已解释") or not row.get("消解证据"))]
    require(not pending, "仲裁有未结清条目")
    rework = read("交接/返工单_问题3.json")
    require(isinstance(rework, list) and all(row["级别"] == "正确性" and row["目标"] == "算" for row in rework), "结构化返工schema")
    require(any(row.get("对应") == "审-1-06" for row in rework), "已有问题引用原编号")
    require((ROOT / "求解/问题3/历史结果_升格裁决前/厚度结果.json").exists(), "历史结果存档")
    require((ROOT / "日志/执行_问题3_升格优胜.log").read_bytes() == (ROOT / "日志/跑升格_问3_3.log").read_bytes(), "执行日志别名是原日志副本")
    fair = read("求解/问题3/结果/公平对照.json")
    require(hashlib.sha256((ROOT / "求解/问题3/升格3/求解.py").read_bytes()).hexdigest() == fair["实现SHA256"], "已跑实现与源代码哈希")
    for name in ["裁决_升格.py", "核验_升格标准库.py", "整理_升格裁决.py", "校验_升格交付.py"]:
        tree = ast.parse((ROOT / "求解/问题3" / name).read_text())
        single = [node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and len(node.id) == 1]
        require(not single, f"{name}出现单字母局部变量")
    forbidden = [line.strip() for line in (ROOT / "运行时/流程词.txt").read_text().splitlines() if line.strip() and not line.startswith("#")]
    events = read("交接/实验记录.json")
    new_attempts = ["固定两折和两种朴素对照，比较四种反演的硅最坏折预测风险", "从原始观测重建连续留段，并独立代入复场与响应公式核对逐块误差", "对硅第二折衬底对比加20%并与未扰动条件配对重估"]
    for attempt in new_attempts:
        matching = [row for row in events if row.get("尝试") == attempt and row.get("问题") == 3]
        require(len(matching) == 1, "实验记录追加缺失或重复")
        event = matching[0]
        narrative = " ".join(event[key] for key in ["尝试", "现象", "决定", "依据"])
        require(not any(word in narrative for word in forbidden), "科学记录含流程词")
    for path in ["日志/升格裁决_G2问3.done", "日志/解读_问题3.done"]:
        lines = (ROOT / path).read_text().splitlines()
        require(lines[0] == "FAIL" and len(lines) <= 5, "完成标记状态或长度")
    report = {"问题": 3, "状态": "正常结束", "交付结构与引用核验": "通过", "科学质量状态": "FAIL",
              "核心数值个数": 8, "引用逐键核对数": len(read("求解/问题3/结果/论文引用核对.json")["逐项"]),
              "仲裁未结清数": len(pending), "源代码哈希吻合": True,
              "新估计器已独立寻优": False, "说明": "本核验通过仅指文件、口径与引用一致，绝不覆盖五项正确性FAIL。", "实际用时秒": time.monotonic() - START}
    target = ROOT / "求解/问题3/结果/交付核验.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
