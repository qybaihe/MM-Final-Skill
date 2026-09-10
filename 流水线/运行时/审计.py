#!/usr/bin/env python3
"""反AI质量审计：禁用词 / 图型多样性 / 数字溯源 / 句式重复。输出 审稿/审计报告.json"""
import glob
import json
import os
import pathlib
import re
from collections import Counter

os.chdir(pathlib.Path(__file__).resolve().parent.parent)  # bin/ → 工作根（与部署位置无关）
report = {}

texs = {p: open(p, encoding="utf-8").read() for p in glob.glob("论文/*.tex")}
# 只审 论文.tex 真正 \input 的章文件：工作根里可能残留旧版章节/检查用 _xxx.tex（骨架版实测有 9 个），
# 它们不进 PDF，却会把禁用词/密度/缩写账全部污染。主控缺失时退回全部 tex。
if "论文/论文.tex" in texs:
    _主控 = re.sub(r"%.*", "", texs["论文/论文.tex"])
    _章 = ["论文/" + (f if f.endswith(".tex") else f + ".tex") for f in re.findall(r"\\input\{([^}]+)\}", _主控)]
    _章 = [f for f in _章 if f in texs]
    if _章:
        texs = {p: t for p, t in texs.items() if p in _章 or p == "论文/论文.tex"}


_附录集 = set()
if "论文/论文.tex" in texs:
    # 附录起点：主控里的 \\appendix，或第一个名字含「附录」的 \\input（本模板没有 \\appendix，靠 8.附录.tex 起头）；
    # 自此往后的章（8.1.问题一源码.tex…）全算附录。
    _主 = re.sub(r"%.*", "", texs["论文/论文.tex"])
    _进附录 = False
    for m in re.finditer(r"\\appendix|\\input\{([^}]+)\}", _主):
        f = m.group(1)
        if f is None:
            _进附录 = True
            continue
        if "附录" in f:
            _进附录 = True
        if _进附录:
            _附录集.add("论文/" + (f if f.endswith(".tex") else f + ".tex"))


def _是附录(p):
    """附录不检的口径：主控 \\appendix 之后 \\input 的章（8.1.问题一源码.tex 这类名字里没有「附录」的也算）或文件名含 附录/参考文献。"""
    return p in _附录集 or any(k in p for k in ("附录", "参考文献"))


def _遮代码(t):
    """lstlisting/verbatim/minted 环境按行数换成空行：代码不是正文，禁用词/有效数字/段首统计都不该数它
    （20260910 对照跑 G4：附录代码里的 'FAIL'、浮点常量、'def re…' 开头被当正文记账），行号又得对得上。"""
    def _空(m):
        return "\n" * m.group(0).count("\n")
    for e in ("lstlisting", "verbatim", "Verbatim", "minted"):
        t = re.sub(r"\\begin\{" + e + r"\}.*?\\end\{" + e + r"\}", _空, t, flags=re.S)
    return t


body = {p: _遮代码(re.sub(r"%.*", "", t)) for p, t in texs.items()}  # 去注释、遮代码块后的正文

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

# 3) 数字溯源（段级口径，M5-1 表达优化 C1）：与第 7 节合并计算，见下文"段级溯源"。
#    此前按行数比：src 注释条数 / 统计数字个数——它奖励"每个数字后面挂一条 src"，
#    把数字推进每一句（骨架版 71 数字/千汉字，第 4 章 69 条 src，病根 R24）。
#    现在按段：一段一组来源，覆盖率 = 有源段 / (有源段 + 无源但含统计数字的段)。

# 4) 段首句式重复（每段前6字）
openers = Counter()
for p_, t in body.items():
    if _是附录(p_) or os.path.basename(p_) == "论文.tex":
        continue
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

# 7) 段级溯源核验（M5-1）：`% src:` 是**段**的属性，不是行的属性
#    一段散文（空行分隔）里的全部 % src 标注合成一个来源池；段内每个统计数字
#    （小数、百分数、≥100 的整数；年份除外）都必须在池里找得到（容差含四舍五入/百分比/万元缩放/尾数）。
#    表格块同理：整张表的行级 src 合成一个池，表里的数字逐个核。
#    键路径（文件:键1;键2）另行核对，未命中只记账不阻塞——键名书写差异不该当成数字错误。
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import 表达画像 as 画

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

def _mantissa(x):
    import math
    x = abs(x)
    if x == 0:
        return 0.0
    return x / (10 ** math.floor(math.log10(x)))

def _close(n, v):
    for s in (1.0, 100.0, 0.01, 10000.0, 0.0001):
        vv = v * s
        if abs(vv) < 1e-12:
            if abs(n) < 1e-9:
                return True
            continue
        if abs(n - vv) / max(abs(vv), 1e-9) < 0.006 or abs(round(n, 0) - round(vv, 0)) < 0.51 and abs(vv) < 1e6 and abs(n - vv) < 1:
            return True
    # 尾数容差：1.11 对 1.11e-16、0.145 对 0.00145（科学计数法与数量级换算）
    if n != 0 and v != 0 and abs(_mantissa(n) - _mantissa(v)) / _mantissa(v) < 0.006:
        return True
    return False

def _找键(obj, seg):
    """任意深度找第一个名字含 seg 的键（键路径写法与 JSON 嵌套不完全对齐时的回退）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if seg == k or seg in k or k in seg:
                return v
        for v in obj.values():
            r = _找键(v, seg)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _找键(v, seg)
            if r is not None:
                return r
    return None

def _取键(obj, 路径):
    """键路径 a.b.c 逐级取；每级先精确后包含匹配，再退到任意深度搜索；取不到返回 None。"""
    cur = obj
    for seg in [s for s in 路径.split(".") if s]:
        if isinstance(cur, dict):
            if seg in cur:
                cur = cur[seg]
                continue
            候 = [k for k in cur if seg in k or k in seg]
            if 候:
                cur = cur[候[0]]
                continue
            cur = _找键(cur, seg)
            if cur is None:
                return None
        elif isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except Exception:
                return None
        else:
            return None
    return cur

def _md键命中(md, 键):
    """节名键：'4.强度展开、色散和峰谷系数' / '第5节“厚度反演与验证方案”' / '2.' → 标题或正文命中即算命中。"""
    k = str(键).strip()
    m = re.match(r"^(?:第\s*)?(\d+)\s*[.．节章]?\s*(.*)$", k)
    题 = re.sub(r"^[“\"「『‘]|[”\"」』’]$", "", (m.group(2) if m else k).strip()).strip()
    if 题 and 题 in md:
        return True
    if m and re.search(r"^#+\s*" + re.escape(m.group(1)) + r"[.．\s]", md, re.M):
        return True
    return bool(k) and k in md


def _溯源文本(块):
    """段内用于核数字的文字：去注释、去显示公式、去纯标记/版式命令，**保留表格单元格**。"""
    t = 画.去注释(块)
    t = 画._删环境(t, 画.公式环境)
    t = re.sub(r"\\\[.*?\\\]", " ", t, flags=re.S)
    for c in 画.纯标记命令:
        t = re.sub(r"\\" + re.escape(c) + r"\*?(\[[^\]]*\])?\{[^{}]*\}", " ", t)
    for c in 画.版式命令:
        t = re.sub(r"\\" + re.escape(c) + r"(\[[^\]]*\])?(\{[^{}]*\}){0,2}", " ", t)
    t = re.sub(r"\\begin\{[a-zA-Z*]+\}(\[[^\]]*\])?(\{[^{}]*\})*", " ", t)
    t = re.sub(r"\\end\{[a-zA-Z*]+\}", " ", t)
    t = re.sub(r"\\[a-zA-Z@]+\*?", " ", t)
    t = re.sub(r"\d+(?:\.\d+)?\s*\^\s*(?:\{[^{}]*\}|-?[A-Za-z0-9])", "幂", t)   # 10^{4}/10^4 整体是量级，不是统计数字
    t = re.sub(r"\^\{[^{}]*\}|\^-?[A-Za-z0-9]", "", t)          # 上下标不是统计数字（10^{-4}、d_{\mu m}）
    t = re.sub(r"_\{[^{}]*\}|_[A-Za-z0-9]", "", t)
    return t

def _统计数字(text):
    """要核的数字：小数、百分数、≥100 的整数；年份不算。返回 float 列表。"""
    out = []
    for m in re.finditer(r"(?<![A-Za-z\\])\d+(?:\.\d+)?(\\?%)?", text):
        s = m.group(0)
        raw = s.replace("\\%", "").replace("%", "")
        try:
            n = float(raw)
        except ValueError:
            continue
        if "." in raw or s.endswith("%"):
            out.append(n)
        elif n >= 100 and not (1990 <= n <= 2035):
            out.append(n)
    return out

存疑, 键未命中, 核对通过段, _cache = [], [], 0, {}
有源段, 无源段, 无源段明细, src条数 = 0, 0, [], 0
for p, t in texs.items():
    if _是附录(p) or os.path.basename(p) == "论文.tex" or os.path.basename(p).startswith("_"):
        continue
    for 起, 块 in 画.段落们(t):
        源们 = 画.解析src(块)
        src条数 += len(源们)
        if not 源们:
            if 画.是正文段(块) and _统计数字(画.段落正文(块)):
                无源段 += 1
                无源段明细.append({"文件": p, "行": 起, "数字": [str(x) for x in _统计数字(画.段落正文(块))[:5]]})
            continue
        有源段 += 1
        pool, 缺文件 = [], []
        for 文件, 键们 in 源们:
            if 文件 not in _cache:
                try:
                    if not 文件.endswith(".json"):
                        _cache[文件] = {"__文本__": open(文件, encoding="utf-8").read()}
                    else:
                        _cache[文件] = json.load(open(文件, encoding="utf-8"))
                except Exception:
                    _cache[文件] = None
            src = _cache[文件]
            if src is None:
                缺文件.append(文件)
                continue
            if isinstance(src, dict) and "__文本__" in src:
                # 建模笔记(.md)/求解脚本(.py)等文本来源（20260910 前一律记「缺失或非JSON」存疑）：键是节名/函数名，按文本命中核对；数字池取全文数字
                md = src["__文本__"]
                pool.extend(_nums_in(md))
                for 键 in 键们:
                    if not _md键命中(md, 键):
                        键未命中.append({"文件": p, "行": 起, "来源": 文件, "键": 键})
                continue
            _all_numbers(src, pool)
            for 键 in 键们:
                if _取键(src, 键) is None:
                    键未命中.append({"文件": p, "行": 起, "来源": 文件, "键": 键})
        if 缺文件:
            存疑.append({"文件": p, "行": 起, "问题": f"来源文件缺失或非JSON:{缺文件}"})
            continue
        数们 = _统计数字(_溯源文本(块))
        坏 = [n for n in 数们 if not any(_close(n, v) for v in pool)]
        if 坏:
            存疑.append({"文件": p, "行": 起, "数字": 坏[:6], "来源": [f for f, _ in 源们][:4]})
        else:
            核对通过段 += 1
report["溯源核验"] = {"核对通过": 核对通过段, "存疑数": len(存疑), "存疑明细": 存疑[:30],
                     "键未命中数": len(键未命中), "键未命中明细": 键未命中[:20],
                     "口径": "段级：一段（空行分隔）内全部 % src 合成来源池，段内每个统计数字（小数/百分数/≥100整数）须在池中；键路径未命中只记账"}
report["数字溯源"] = {"正文统计数字约": sum(len(_统计数字(画.段落正文(块))) for p_, t_ in texs.items()
                                           if not _是附录(p_) for _, 块 in 画.段落们(t_) if 画.是正文段(块)),
                     "已标注来源": src条数, "有源段": 有源段, "无源段": 无源段, "无源段明细": 无源段明细[:20],
                     "覆盖率": round(有源段 / (有源段 + 无源段), 2) if (有源段 + 无源段) else None}

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

# 10) 有效数字规范：正文里 5 位以上小数 = 直接照搬程序输出、精度失控
#     实测教训（20260827 轮）：论文出现 16.750590%、7.658250--8.062678 μm、0.129226%
#     这类 6 位小数，是把 json 里的浮点原样贴进正文。学术写作应按测量精度收敛有效数字。
#     科学计数法（如 1.11\times10^{-16}）小数位少，不会被误伤。
过精 = []
for p_, t in body.items():
    for m in re.finditer(r"\d+\.\d{5,}", t):
        过精.append({"文件": p_, "行": t[: m.start()].count("\n") + 1, "数字": m.group(0)})
report["有效数字"] = {"过精数量": len(过精), "阈值": "小数位≤4", "明细": 过精[:30]}


# 11) 内部术语密度（**密度受限，非零容忍**）
# 实测（20260831 轮）：全文均值 4.58 次/千字并不高，但分布极不均——
#   摘要 20.90、结论 19.38、第6章 12.44，而第 2/3/5/7 四章为 0。
# 四位审稿员**连续四轮**点名同一件事：「摘要和结论读起来像质量审计报告」
# 「术语密集，却没有先用一句朴素语言回答题目」。改了四轮，交付时仍然存在。
# 单一全局阈值抓不到这个形态（全文均值本就达标），所以按章计密度，
# 并对**评委最先读的章**（摘要/结论/引言）用更严的线。
# 不设零容忍：这些词是本文真实的方法名，全禁会逼撰稿师绕着写反而别扭；
# 四章密度为 0 也证明留有余地。
try:
    内部词 = [w.strip() for w in open("运行时/内部术语.txt", encoding="utf-8")
              if w.strip() and not w.startswith("#")]
except Exception:
    内部词 = []
def _纯正文(t):
    for e in ("figure", "table", "tabular", "lstlisting", "tikzpicture", "longtable"):
        t = re.sub(r"\\begin\{" + e + r"\*?\}.*?\\end\{" + e + r"\*?\}", "", t, flags=re.S)
    t = re.sub(r"\$[^$]*\$", "", t)
    t = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", "", t)
    return re.sub(r"[{}]", "", t)
首读 = ("摘要", "结论", "引言")
章密度, 超线 = {}, []
总命中 = 总字 = 0
for p_, t in body.items():
    纯 = _纯正文(t)
    汉 = len(re.findall(r"[\u4e00-\u9fff]", 纯))
    n = sum(len(re.findall(re.escape(w), 纯)) for w in 内部词)
    总命中 += n; 总字 += 汉
    if 汉 < 200 or _是附录(p_):      # 太短的章密度噪声大；附录本就技术性，不检
        continue
    d = round(n / 汉 * 1000, 2)
    是首读 = any(k in p_ for k in 首读)
    线 = 10.0 if 是首读 else 15.0
    章密度[p_] = {"命中": n, "汉字": 汉, "密度": d, "线": 线, "首读章": 是首读}
    if d > 线:
        超线.append({"文件": p_, "密度": d, "阈值": 线, "命中": n})
report["内部术语密度"] = {
    "全文密度": round(总命中 / 总字 * 1000, 2) if 总字 else 0,
    "超线章数": len(超线), "超线明细": 超线, "按章": 章密度,
    "口径": "次/千汉字；首读章(摘要/结论/引言)≤10，其他正文章≤15，附录不检",
}

# 12) 表达画像（M5-1 表达优化，阶段 0）：数字密度 / 对冲密度 / 句长 / 缩写 / 段首数字 / 段内数字预算
#     量尺在 运行时/表达画像.py（与 验证/范文分布.py 共用同一把尺）；阈值只认 运行时/表达阈值.json，
#     其"出处"字段说明这条线来自先验还是范文分布——阈值文件缺失时判据必须报缺失而不是空过（病根 R18）。
try:
    阈 = json.load(open("运行时/表达阈值.json", encoding="utf-8"))
except Exception:
    阈 = None
对冲词 = 画.读词表("运行时/对冲词.txt")
白名单 = 画.读词表("运行时/缩写白名单.txt")
首读关键词 = ("摘要", "结论", "引言", "问题重述", "评价与推广")
超线, 段首明细, 超预算明细, 密集明细 = [], [], [], []
按章, 全文汉 = {}, {"汉字": 0, "数字个数": 0, "对冲个数": 0}
摘要画像 = {}
if 阈 is None:
    超线.append({"文件": "运行时/表达阈值.json", "指标": "阈值文件", "值": None, "阈值": None})
if not 对冲词:
    超线.append({"文件": "运行时/对冲词.txt", "指标": "词表缺失", "值": None, "阈值": None})
if not 白名单:
    超线.append({"文件": "运行时/缩写白名单.txt", "指标": "词表缺失", "值": None, "阈值": None})
硬线 = int(((阈 or {}).get("段内数字") or {}).get("硬线", 4))
软线 = int(((阈 or {}).get("段内数字") or {}).get("软线", 2))
for p_, t in texs.items():
    if _是附录(p_) or os.path.basename(p_) == "论文.tex" or os.path.basename(p_).startswith("_"):
        continue
    x = 画.画像(t, 对冲词, 白名单, 已拍平=False)
    是摘要 = "摘要" in p_
    类 = "摘要" if 是摘要 else ("首读章" if any(k in p_ for k in 首读关键词) else "正文章")
    段首 = 密集 = 超预算 = 0
    for 起, 块 in 画.段落们(t):
        if not 画.是正文段(块):
            continue
        if 画.段首数字(块):
            段首 += 1
            段首明细.append({"文件": p_, "行": 起, "开头": 画.段落正文(块).strip()[:24]})
        n = len(画.段内数字(块))
        if n > 硬线:
            超预算 += 1
            超预算明细.append({"文件": p_, "行": 起, "数字个数": n})
        elif n > 软线:
            密集 += 1
            密集明细.append({"文件": p_, "行": 起, "数字个数": n})
    for k in 全文汉:
        全文汉[k] += x[k]
    线 = {}
    if 阈:
        线 = {"数字密度": float(阈["数字密度_上限"][类]), "对冲密度": float(阈["对冲密度_上限"][类]),
              "句均汉字": float(阈["句均汉字_上限"][类])}
    按章[p_] = {**x, "类别": 类, "段首数字段": 段首, "密集段": 密集, "超预算段": 超预算, "线": 线}
    if x["汉字"] >= 200 and 线:
        for 指标 in ("数字密度", "对冲密度", "句均汉字"):
            if x[指标] > 线[指标]:
                超线.append({"文件": p_, "指标": 指标, "值": x[指标], "阈值": 线[指标]})
    if 是摘要:
        摘线 = (阈 or {}).get("摘要画像") or {}
        摘超 = []
        for 指标, 键 in (("数字个数", "数字个数_上限"), ("对冲个数", "对冲个数_上限"),
                        ("缩写个数", "缩写个数_上限"), ("句均汉字", "句均汉字_上限")):
            if 键 in 摘线 and x[指标] > 摘线[键]:
                摘超.append(f"{指标} {x[指标]} > {摘线[键]}")
        摘要画像 = {"文件": p_, "数字个数": x["数字个数"], "对冲个数": x["对冲个数"], "缩写个数": x["缩写个数"],
                    "缩写": x["缩写"], "句均汉字": x["句均汉字"], "汉字": x["汉字"], "超线": 摘超}
千 = 全文汉["汉字"] / 1000.0 if 全文汉["汉字"] else 0
report["表达画像"] = {
    "按章": 按章,
    "全文": {"汉字": 全文汉["汉字"], "数字密度": round(全文汉["数字个数"] / 千, 2) if 千 else 0,
             "对冲密度": round(全文汉["对冲个数"] / 千, 2) if 千 else 0},
    "超线明细": 超线, "摘要画像": 摘要画像,
    "段首数字段": len(段首明细), "段首数字明细": 段首明细[:20],
    "超预算段": len(超预算明细), "超预算段明细": 超预算明细[:20],
    "密集段": len(密集明细), "密集段明细": 密集明细[:30],
    "段内数字线": {"硬线": 硬线, "软线": 软线},
    "阈值出处": (阈 or {}).get("出处") if 阈 else "缺失",
    "口径": "密度=次/千汉字（拍平后只计≥4汉字的行）；摘要/首读章/正文章各自一条线；段首数字段与超预算段为 tex 专检",
}

# 13) 自造缩写（B3 命名政策）：≥2 个大写字母的英文串不在 运行时/缩写白名单.txt 即判自造；标题（section/title/caption）零容忍，
#     标题里出现 内部术语.txt 的词同样判违规（标题禁自造词——评委须通读才知道"证据门"是什么）。
缩写明细, 标题违规 = [], []
for p_, t in texs.items():
    if _是附录(p_) or os.path.basename(p_) == "论文.tex" or os.path.basename(p_).startswith("_"):
        continue
    净 = 画.去注释(t)
    净 = 画._删环境(净, ("lstlisting", "verbatim", "thebibliography"))
    for c in 画.纯标记命令:
        净 = re.sub(r"\\" + re.escape(c) + r"\*?(\[[^\]]*\])?\{[^{}]*\}", " ", 净)
    净 = re.sub(r"\\[a-zA-Z@]+\*?", " ", 净)          # 命令名（\FloatBarrier、\LTpre）不是散文里的缩写
    for i, line in enumerate(净.split("\n"), 1):
        for w in 画.缩写们(line, 白名单):
            缩写明细.append({"文件": p_, "行": i, "词": w})
    for 行, 标题 in 画.标题们(t):
        for w in 画.缩写们(标题, 白名单):
            标题违规.append({"文件": p_, "行": 行, "标题": 标题[:60], "词": w})
        for w in 内部词:
            if w and w in 标题:
                标题违规.append({"文件": p_, "行": 行, "标题": 标题[:60], "词": w})
report["自造缩写"] = {"数量": len(缩写明细), "明细": 缩写明细[:40], "去重": sorted({x["词"] for x in 缩写明细})[:30],
                     "标题违规数": len(标题违规), "标题违规": 标题违规[:20], "白名单词数": len(白名单)}

# 14) 图题（C6）：图题 = 对象 + 看到什么；不是限制条款。对冲词在图题里零容忍。
图题明细, 图题违规 = [], []
for p_, t in texs.items():
    if os.path.basename(p_).startswith("_"):
        continue
    for 行, 题 in 画.图题们(t):
        图题明细.append(题)
        命中 = [w for w in 对冲词 if w in 题]
        if 命中:
            图题违规.append({"文件": p_, "行": 行, "图题": 题[:80], "词": 命中[:4]})
report["图题"] = {"图题数": len(图题明细), "对冲违规数": len(图题违规), "对冲违规": 图题违规[:20],
                 "平均汉字": round(sum(len(re.findall(r"[\u4e00-\u9fff]", x)) for x in 图题明细) / len(图题明细), 1) if 图题明细 else 0}

# 15) 实验记录分流（B2）：类别=科学尝试 的条目才许进论文，它们不得含流程词（源头治理，G2 逐问检；这里给全局账）。
流程词 = 画.读词表("运行时/流程词.txt")
记录报告 = {"总条数": 0, "科学尝试": 0, "流程事件": 0, "未分类": 0, "科学尝试含流程词": 0, "明细": [], "按问题": {}}
try:
    记录 = json.load(open("交接/实验记录.json", encoding="utf-8"))
    记录 = 记录 if isinstance(记录, list) else []
except Exception:
    记录 = []
for i, it in enumerate(记录):
    if not isinstance(it, dict):
        continue
    记录报告["总条数"] += 1
    类别 = str(it.get("类别", "")).strip()
    问 = str(it.get("问题", "?"))
    按问 = 记录报告["按问题"].setdefault(问, {"科学尝试": 0, "含流程词": 0})
    if 类别 == "科学尝试":
        记录报告["科学尝试"] += 1
        按问["科学尝试"] += 1
        文 = " ".join(str(it.get(k, "")) for k in ("尝试", "现象", "决定", "依据"))
        命中 = [w for w in 流程词 if w in 文]
        if 命中:
            记录报告["科学尝试含流程词"] += 1
            按问["含流程词"] += 1
            记录报告["明细"].append({"序号": i, "问题": 问, "词": 命中[:4]})
    elif 类别 == "流程事件":
        记录报告["流程事件"] += 1
    else:
        记录报告["未分类"] += 1
记录报告["明细"] = 记录报告["明细"][:20]
report["实验记录分流"] = 记录报告

# 16) 问题重述与总体分析（C5）：1.1 必含"给定/要求"两要素；1.2 总体分析须有思路图。反句式不反内容。
重述 = {"有章": False, "含给定": False, "含要求": False, "有总体分析": False, "有思路图": False, "文件": ""}
for p_, t in texs.items():
    净 = 画.去注释(t)
    if "问题重述" in os.path.basename(p_) or re.search(r"\\section\*?\{[^}]*问题重述", 净):
        重述["有章"] = True
        重述["文件"] = p_
        m = re.search(r"\\subsection\*?\{[^}]*问题重述[^}]*\}(.*?)(?=\\subsection|\Z)", 净, re.S)
        段 = m.group(1) if m else 净
        重述["含给定"] = bool(re.search(r"给定|已知|提供了|附件", 段))
        重述["含要求"] = bool(re.search(r"要求|需要|需求|求解|回答", 段))
        重述["有总体分析"] = bool(re.search(r"\\subsection\*?\{[^}]*(总体分析|分析思路|总体思路)", 净))
        重述["有思路图"] = bool(re.search(r"\\includegraphics", 净))
        break
report["问题重述"] = 重述

os.makedirs("审稿", exist_ok=True)
json.dump(report, open("审稿/审计报告.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps({k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in list(v.items())[:3]}) for k, v in report.items()}, ensure_ascii=False)[:900])
