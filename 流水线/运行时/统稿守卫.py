#!/usr/bin/env python3
"""统稿守卫：统稿腿只许动语言，不许动事实。机械比对旧稿与新稿，任何事实层差异 = 该文件整份回退。

为什么要有它：统稿是一次"全稿通读、术语与语气统一"的语言腿（表达优化方案 v1 C3）。它拿到的是整本论文，
比任何一条章修订腿的权限都大；没有机械边界，它就会顺手"改顺"一个数字、合并两个公式、删掉一条 % src。
这些都是评审看不见、门检也未必抓得到的事实漂移（病根 R2 的放大版）。所以边界不写在提示词里，写在这里：

  事实层（逐文件必须完全相等）：
    · 统计数字多重集合（去注释后全文，含表格；数字可以在散文与表格之间搬家，不能增删改）
    · \\label / \\ref·\\eqref·\\autoref·\\cref / \\cite 三个键集合
    · 显示公式个数（equation/align/gather/multline/eqnarray/flalign/alignat/displaymath/\\[ \\]）
    · \\includegraphics 个数、\\section 系标题文字序列（标题由计划锁定，逐字照搬）
    · `% src:` 溯源标注条数（只许增不许减——统稿师补标注可以，删标注不行）
  语言层（不比）：句子、段落、措辞、对冲句、段首词。

用法（工作根内）：python3 bin/统稿守卫.py 旧目录 新目录 [文件相对路径…]
  输出 JSON {"通过": bool, "逐文件": {文件: {"通过": bool, "差异": [...]}}}，同时写 审稿/统稿守卫.json。
作为模块：守卫(旧文本, 新文本) -> (通过, 差异列表)；逐文件(旧们, 新们) -> 报告。
"""
import json
import os
import re
import sys
from collections import Counter

数字re = re.compile(r"(?<![A-Za-z\\])\d+(?:\.\d+)?%?")
公式环境 = ("equation", "equation*", "align", "align*", "gather", "gather*", "multline", "multline*",
            "eqnarray", "eqnarray*", "flalign", "flalign*", "alignat", "alignat*", "displaymath")


def 去注释(tex):
    行们 = []
    for line in tex.split("\n"):
        out, i = [], 0
        while i < len(line):
            c = line[i]
            if c == "\\" and i + 1 < len(line):
                out.append(line[i:i + 2]); i += 2; continue
            if c == "%":
                break
            out.append(c); i += 1
        行们.append("".join(out))
    return "\n".join(行们)


def 事实层(tex):
    净 = 去注释(tex)
    # 上下标/年份不剔：这里比的是"有没有变"，不是"算不算统计数字"，口径越宽越安全
    数字 = Counter(数字re.findall(净))
    键 = lambda 名: set(re.findall(r"\\" + 名 + r"\*?\{([^}]*)\}", 净))
    引 = set()
    for c in ("ref", "eqref", "autoref", "cref", "Cref", "pageref"):
        引 |= 键(c)
    cite = set()
    for m in re.findall(r"\\cite[pt]?\*?(?:\[[^\]]*\])?\{([^}]*)\}", 净):
        cite |= {k.strip() for k in m.split(",") if k.strip()}
    公式 = sum(len(re.findall(r"\\begin\{" + re.escape(e) + r"\}", 净)) for e in 公式环境) + len(re.findall(r"\\\[", 净))
    图 = len(re.findall(r"\\includegraphics", 净))
    标题 = re.findall(r"\\(?:sub)*section\*?\{((?:[^{}]|\{[^{}]*\})*)\}", 净)
    src = len(re.findall(r"%\s*src\s*:", tex))
    return {"数字": 数字, "label": 键("label"), "ref": 引, "cite": cite, "公式": 公式, "图": 图, "标题": 标题, "src": src}


def 守卫(旧文本, 新文本):
    a, b = 事实层(旧文本), 事实层(新文本)
    差异 = []
    if a["数字"] != b["数字"]:
        少 = list((a["数字"] - b["数字"]).elements())[:8]
        多 = list((b["数字"] - a["数字"]).elements())[:8]
        差异.append(f"统计数字被改动：少了 {少} 多了 {多}")
    for k in ("label", "ref", "cite"):
        if a[k] != b[k]:
            差异.append(f"{k} 集合被改动：少了 {sorted(a[k] - b[k])[:6]} 多了 {sorted(b[k] - a[k])[:6]}")
    if a["公式"] != b["公式"]:
        差异.append(f"显示公式个数 {a['公式']} → {b['公式']}")
    if a["图"] != b["图"]:
        差异.append(f"插图个数 {a['图']} → {b['图']}")
    if a["标题"] != b["标题"]:
        差异.append(f"章节标题被改动：{[t for t in a['标题'] if t not in b['标题']][:3]} → {[t for t in b['标题'] if t not in a['标题']][:3]}")
    if b["src"] < a["src"]:
        差异.append(f"% src 溯源标注减少 {a['src']} → {b['src']}")
    return (not 差异), 差异


def 逐文件(旧们, 新们):
    """旧们/新们: {相对路径: 文本}。新稿缺文件 = 不通过（统稿不许删章）。"""
    报 = {"通过": True, "逐文件": {}}
    for f, 旧 in 旧们.items():
        新 = 新们.get(f)
        if 新 is None:
            报["逐文件"][f] = {"通过": False, "差异": ["文件消失"]}
        else:
            ok, 差 = 守卫(旧, 新)
            报["逐文件"][f] = {"通过": ok, "差异": 差}
        if not 报["逐文件"][f]["通过"]:
            报["通过"] = False
    return 报


def _读目录(d, 文件们):
    out = {}
    for f in 文件们:
        p = os.path.join(d, f)
        try:
            out[f] = open(p, encoding="utf-8").read()
        except Exception:
            pass
    return out


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(2)
    旧目录, 新目录 = sys.argv[1], sys.argv[2]
    文件们 = sys.argv[3:] or sorted(f for f in os.listdir(旧目录) if f.endswith(".tex"))
    报 = 逐文件(_读目录(旧目录, 文件们), _读目录(新目录, 文件们))
    try:
        os.makedirs("审稿", exist_ok=True)
        json.dump(报, open("审稿/统稿守卫.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass
    print(json.dumps(报, ensure_ascii=False))
    sys.exit(0 if 报["通过"] else 1)
