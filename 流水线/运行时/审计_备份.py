#!/usr/bin/env python3
"""反AI质量审计：禁用词 / 图型多样性 / 数字溯源 / 句式重复。输出 审稿/审计报告.json"""
import glob
import json
import os
import re
from collections import Counter

os.chdir("/tmp/蜂巢")
report = {}

texs = {p: open(p, encoding="utf-8").read() for p in glob.glob("论文/*.tex")}
body = {p: re.sub(r"%.*", "", t) for p, t in texs.items()}  # 去注释后的正文

# 1) 禁用词
banned = [w.strip() for w in open("运行时/禁用词.txt", encoding="utf-8") if w.strip()]
hits = []
for p, t in body.items():
    for w in banned:
        for m in re.finditer(re.escape(w), t):
            line = t[: m.start()].count("\n") + 1
            hits.append({"文件": p, "行": line, "词": w})
report["禁用词"] = {"数量": len(hits), "明细": hits[:30]}

# 2) 图型多样性（扫描绘图脚本的 mpl 调用）
kind_pat = {
    "柱状": r"\.bar\(|\.barh\(", "折线": r"\.plot\(", "散点": r"\.scatter\(",
    "热力": r"\.imshow\(|pcolormesh", "箱线": r"\.boxplot\(", "饼": r"\.pie\(",
    "面积": r"\.fill_between\(|stackplot", "直方": r"\.hist\(", "示意/手绘": r"FancyArrowPatch|FancyBboxPatch|add_patch",
}
kind_count = Counter()
for p in glob.glob("求解/**/绘图_*.py", recursive=True):
    src = open(p, encoding="utf-8").read()
    for k, pat in kind_pat.items():
        if re.search(pat, src):
            kind_count[k] += 1
pngs = glob.glob("求解/**/图片/*.png", recursive=True)
total = sum(kind_count.values()) or 1
report["图型"] = {
    "图文件数": len(pngs), "脚本图型分布": dict(kind_count),
    "柱折占比": round((kind_count["柱状"] + kind_count["折线"]) / total, 2),
    "示意图数": kind_count["示意/手绘"],
    "违规": ([] if kind_count["示意/手绘"] >= 2 else ["自设计示意图不足2张"]) +
            ([] if (kind_count["柱状"] + kind_count["折线"]) / total <= 0.55 else ["柱状+折线占比过半"]),
}

# 3) 数字溯源：正文中的统计数字应有 % src: 注释
src_annot = sum(len(re.findall(r"%\s*src:", t)) for t in texs.values())
nums = 0
for p, t in body.items():
    if "摘要" in p or "5." in p or "6." in p or "结果" in p or "检验" in p:
        nums += len(re.findall(r"\d+\.\d+%?|\d{2,}%", t))
report["数字溯源"] = {"正文统计数字约": nums, "已标注来源": src_annot,
                     "覆盖率": round(src_annot / nums, 2) if nums else None}

# 4) 段首句式重复（每段前6字）
openers = Counter()
for t in body.values():
    for para in re.split(r"\n\s*\n", t):
        para = para.strip()
        if len(para) > 40 and not para.startswith("\\"):
            openers[para[:6]] += 1
rep = {k: v for k, v in openers.items() if v >= 3}
report["段首重复"] = rep

# 5) "首先…最后"整套连用计数
firstlast = sum(len(re.findall(r"首先[^\n]{0,120}接着[^\n]{0,200}(然后[^\n]{0,200})?最后", t)) for t in body.values())
report["首先最后套件"] = firstlast


# 6) 机械叙述模式
ref_all = sum(len(re.findall(r"\\ref\{(fig|tab)", t)) for t in body.values())
ref_ruji = sum(len(re.findall(r"如[图表]\\s*\\\\ref", t)) + len(re.findall(r"如[图表]\\ref", t)) for t in body.values())
report["图表引用句式"] = {"引用总数": ref_all, "如图如表句式数": ref_ruji,
                          "占比": round(ref_ruji / ref_all, 2) if ref_all else None,
                          "违规": ["如图如表句式超40%"] if ref_all and ref_ruji / ref_all > 0.4 else []}
wanneng = sum(len(re.findall(r"数据真实可靠|假设数据无误", t)) for t in body.values())
report["万能假设"] = wanneng

# 7) 溯源核验：% src 注释指向的 JSON 里必须真的有这个数（容差含四舍五入/百分比/万元缩放）
def _nums_in(text):
    out = []
    for m in re.finditer(r"-?\d+(?:\.\d+)?", text.replace(",", "")):
        try:
            out.append(float(m.group()))
        except ValueError:
            pass
    return out

def _all_numbers(obj, acc):
    if isinstance(obj, dict):
        for v in obj.values():
            _all_numbers(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _all_numbers(v, acc)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        acc.append(float(obj))
    elif isinstance(obj, str):
        acc.extend(_nums_in(obj))

def _close(n, v):
    for s in (1.0, 100.0, 0.01, 10000.0, 0.0001):
        vv = v * s
        if abs(vv) < 1e-12:
            if abs(n) < 1e-9:
                return True
            continue
        if abs(n - vv) / max(abs(vv), 1e-9) < 0.006 or abs(round(n, 0) - round(vv, 0)) < 0.51 and abs(vv) < 1e6 and abs(n - vv) < 1:
            return True
    return False

存疑, 核对, _cache = [], 0, {}
for p, t in texs.items():
    for ln, line in enumerate(t.splitlines(), 1):
        m = re.search(r"%\s*src:([^:\s]+):?(\S*)", line)
        if not m:
            continue
        srcf = m.group(1)
        if srcf not in _cache:
            try:
                _cache[srcf] = json.load(open(srcf, encoding="utf-8"))
            except Exception:
                _cache[srcf] = None
        src = _cache[srcf]
        if src is None:
            存疑.append({"文件": p, "行": ln, "问题": f"来源文件缺失或非JSON:{srcf}"})
            continue
        pool = []
        _all_numbers(src, pool)
        line_nums = [n for n in _nums_in(re.sub(r"%.*", "", line)) if not (1990 <= n <= 2035 and n == int(n))]
        if not line_nums:
            核对 += 1
            continue
        if any(_close(n, v) for n in line_nums for v in pool):
            核对 += 1
        else:
            存疑.append({"文件": p, "行": ln, "数字": line_nums[:4], "来源": srcf})
report["溯源核验"] = {"核对通过": 核对, "存疑数": len(存疑), "存疑明细": 存疑[:20]}

# 8) 假设台账核验：每条 检验结果 非空、非"待检验"，且引用的结果键真实存在
台账报告, 台账违规 = {}, []
for 台 in sorted(glob.glob("交接/假设台账_问题*.json")):
    问 = re.search(r"问题(\w+)", 台)
    问 = 问.group(1) if 问 else "?"
    try:
        条目 = json.load(open(台, encoding="utf-8"))
    except Exception as e:
        台账违规.append({"文件": 台, "问题": f"非法JSON:{e}"})
        continue
    if not isinstance(条目, list) or not 条目:
        台账违规.append({"文件": 台, "问题": "台账为空或不是数组"})
        continue
    # 收集该问结果 JSON 的全部键，供引用核对
    键池 = set()

    def _收键(o):
        if isinstance(o, dict):
            for k, v in o.items():
                键池.add(str(k))
                _收键(v)
        elif isinstance(o, list):
            for v in o:
                _收键(v)
    for rp in glob.glob(f"求解/问题{问}/结果/*.json"):
        try:
            _收键(json.load(open(rp, encoding="utf-8")))
        except Exception:
            pass
    for it in 条目:
        if not isinstance(it, dict):
            台账违规.append({"文件": 台, "问题": f"条目非对象:{str(it)[:60]}"})
            continue
        号 = it.get("假设号", "?")
        for k in ("假设", "依据", "灵敏度义务", "检验结果"):
            if not str(it.get(k, "")).strip():
                台账违规.append({"文件": 台, "假设号": 号, "问题": f"{k} 为空"})
        检 = str(it.get("检验结果", ""))
        if 检.strip() and ("待检验" in 检 or "未检验" in 检):
            台账违规.append({"文件": 台, "假设号": 号, "问题": "检验结果仍为待检验/未检验"})
        for 键 in re.findall(r"[（(][^）)]*?[:：]\s*([^）)，,]+)", 检):
            键 = 键.strip()
            if 键 and 键池 and not any(键 in k or k in 键 for k in 键池):
                台账违规.append({"文件": 台, "假设号": 号, "问题": f"引用的结果键不存在:{键}"})
        # 万能假设检测
        if re.search(r"数据真实可靠|数据无误|忽略一切|不考虑任何", str(it.get("假设", ""))):
            台账违规.append({"文件": 台, "假设号": 号, "问题": "疑似万能假设（无生效位置）"})
    台账报告[台] = len(条目)
report["假设台账"] = {"台账文件": 台账报告, "条目合计": sum(台账报告.values()),
                      "违规数": len(台账违规), "违规明细": 台账违规[:20]}

os.makedirs("审稿", exist_ok=True)
json.dump(report, open("审稿/审计报告.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps({k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in list(v.items())[:3]}) for k, v in report.items()}, ensure_ascii=False)[:900])
