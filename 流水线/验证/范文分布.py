#!/usr/bin/env python3
"""范文分布：对 运行时/范文/*.pdf 跑与 审计.py 同口径的表达量尺，把真稿分布写回 运行时/表达阈值.json。

原则 P7：合规阈值只认真稿分布。本脚本是"先验 → 分布"的唯一改写路径；手改阈值文件视为违规。
用法：python3 范文分布.py [--只看] [--对照=某.pdf ...]
      --只看        打印分布、不写回
      --对照=x.pdf  用同一口径量任意 PDF（如骨架版成品）并排打印，不写回、不入样本
需要 pdftotext（poppler，M5-0b 已装）。范文 PDF 由用户放入 运行时/范文/，文件名任意。

口径（20260908 实测后定）：
  PDF 文本分不出公式/表格/图内文字，直接数会把目录页码、矩阵元素、坐标刻度全算成"统计数字"
  （首版把目录当正文，数字密度 300+/千汉字）。所以先做 **散文过滤**：空行分块；块内只留
  ≥6 汉字、汉字+中文标点占非空白字符 ≥50%、无点线目录的行；块 ≥20 汉字才算一段。
  这与 tex 侧 拍平→有效行 的口径对得上（骨架版 tex 口径 数字密度 41.1 ↔ PDF 口径 39.9）。
  摘要   = 第一个「摘 要」（封面上常被排成 摘/校/要 三行，允许中间夹 ≤12 字符）到第一个「关键词/关键字」；
  正文章 = 摘要之后、**最后一个**「参考文献」之前（第一个在目录里）；
  首读章 = 「问题重述」→「问题分析」之前 + 「模型的评价/结论」→末尾（找不到就空）。
写回规则：
  数字密度/句均汉字 上限 = P90；对冲密度上限 = max(P90, 1.0)（真稿≈0，但表达四律要求每问结论句后
  一处置信声明，留每千汉字 1 处的余量）；摘要画像 数字个数/句均 = P90，对冲个数 = max(P90, 1)；
  段内数字 硬线 = 全部正文段合并后的 P90，软线 = P75；
  不写回：缩写个数（依赖白名单，真稿里 DFT/SVD 之类标准缩写会被误判）、段首数字（PDF 分块会把小节号并进段首，量不准）。
  某一类样本 <3 篇则该类不写回。篇数 <3 整体不写回。
样本说明：当前 10 篇来自 GitHub zhanwen/MathModel「国赛论文/优秀论文」，是**研究生数模竞赛（华为杯）**的一等奖稿，
  40–104 页，比本科国赛（高教社杯，≤25 页）长；表达层面的数字节律/置信口吻/句长可作代理，篇幅类阈值不可搬用。
"""
import datetime
import json
import pathlib
import re
import subprocess
import sys

线 = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(线 / "运行时"))
import 表达画像 as 画  # noqa: E402

范文目录 = 线 / "运行时/范文"
阈值文件 = 线 / "运行时/表达阈值.json"
数字re = re.compile(r"(?<![A-Za-z\\])\d+(?:\.\d+)?%?")
散文行re = re.compile(r"[一-鿿，。；：、“”（）！？《》]")
目录re = re.compile(r"(?:\.\s*){5,}|…{2,}|(?:·\s*){5,}")


def 汉(s):
    return len(画.汉字re.findall(s))


def 抽文本(pdf):
    try:
        return subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, text=True,
                              errors="replace", timeout=120).stdout
    except Exception as e:
        print(f"!! pdftotext 失败 {pdf.name}: {e}")
        return ""


def 切段(t):
    t = t.replace("\f", "\n")
    m = re.search(r"摘[\s\S]{0,12}?要(.*?)关键[词字]", t, re.S)
    摘 = m.group(1) if m else ""
    起 = m.end() if m else 0
    尾 = t.rfind("参考文献")
    正文 = t[起:尾] if 尾 > 起 else t[起:]
    首读 = ""
    a = re.search(r"问题[的]?重述(.*?)(?=问题[的]?分析|模型[的]?假设|符号说明)", 正文, re.S)
    if a:
        首读 += a.group(1)
    b = re.search(r"(模型[的]?评价|结论|总结)(.*)$", 正文, re.S)
    if b:
        首读 += b.group(2)
    return {"摘要": 摘, "首读章": 首读, "正文章": 正文}


def 散文段(text):
    """PDF 文本 → 散文段列表（见模块说明）。"""
    段 = []
    for 块 in re.split(r"\n\s*\n", text):
        行 = []
        for l in 块.split("\n"):
            s = l.strip()
            if not s or 目录re.search(s):
                continue
            非空 = re.sub(r"\s", "", s)
            if 汉(s) >= 6 and len(散文行re.findall(非空)) / max(1, len(非空)) >= 0.5:
                行.append(s)
        p = "".join(行)
        if 汉(p) >= 20:
            段.append(p)
    return 段


def 量(段们, 对冲词, 白名单):
    text = "\n".join(段们)
    h = 汉(text)
    每段数字 = [len(数字re.findall(p)) for p in 段们]
    句 = [汉(s) for s in re.split(r"[。！？]", text) if 6 <= 汉(s) <= 150]   # >150 汉字的"句"= 标点没抽出来
    冲 = sum(text.count(w) for w in 对冲词)
    return {"汉字": h, "段数": len(段们), "数字个数": sum(每段数字),
            "数字密度": round(1000 * sum(每段数字) / max(1, h), 2),
            "对冲个数": 冲, "对冲密度": round(1000 * 冲 / max(1, h), 2),
            "句均汉字": round(sum(句) / max(1, len(句)), 1) if 句 else 0.0,
            "缩写个数": len(画.缩写们(text, 白名单)), "每段数字": 每段数字}


def P(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[f] + (xs[c] - xs[f]) * (k - f), 1)


def 量一篇(pdf, 对冲词, 白名单):
    t = 抽文本(pdf)
    if 汉(t) < 2000:
        return None
    out = {}
    for 类, 文 in 切段(t).items():
        段 = 散文段(文)
        if 汉("".join(段)) < 150:
            continue
        out[类] = 量(段, 对冲词, 白名单)
    return out


def 一行(名, x):
    a, b = x.get("摘要"), x.get("正文章")
    fa = (f"摘要 汉字{a['汉字']:5d} 数字{a['数字个数']:3d} 对冲{a['对冲个数']:2d} 句均{a['句均汉字']:5.1f}" if a else "摘要 (未识别)")
    fb = (f"正文 汉字{b['汉字']:6d} 段{b['段数']:4d} 数字密度{b['数字密度']:6.1f} 对冲密度{b['对冲密度']:5.2f} 句均{b['句均汉字']:5.1f} "
          f"段内数字P90 {P(b['每段数字'], 0.9)}" if b else "正文 (未识别)")
    return f"{名[:16]:<16} | {fa} | {fb}"


def 主(只看=False, 对照=()):
    对冲词 = 画.读词表(str(线 / "运行时/对冲词.txt"))
    白名单 = 画.读词表(str(线 / "运行时/缩写白名单.txt"))
    pdfs = sorted(范文目录.glob("*.pdf"))
    if not pdfs:
        print(f"运行时/范文/ 没有 PDF；阈值保持 {阈值文件.name} 现值（出处见文件）")
        return 0
    逐篇 = {}
    for pdf in pdfs:
        x = 量一篇(pdf, 对冲词, 白名单)
        if x is None:
            print(f"!! {pdf.name} 抽出汉字 <2000，可能是扫描件，跳过")
            continue
        逐篇[pdf.name] = x
        print(一行(pdf.name, x))
    for p in 对照:
        x = 量一篇(pathlib.Path(p), 对冲词, 白名单)
        print(一行("对照:" + pathlib.Path(p).name, x) if x else f"对照 {p}: 抽不出文本")
    样本 = {类: [x[类] for x in 逐篇.values() if 类 in x] for 类 in ("摘要", "首读章", "正文章")}
    分布 = {}
    for 类, xs in 样本.items():
        if not xs:
            continue
        分布[类] = {指标: {"中位": P([x[指标] for x in xs], 0.5), "P75": P([x[指标] for x in xs], 0.75),
                        "P90": P([x[指标] for x in xs], 0.9), "最大": max(x[指标] for x in xs), "n": len(xs)}
                  for 指标 in ("数字密度", "对冲密度", "句均汉字")}
    if 样本["摘要"]:
        分布["摘要计数"] = {指标: {"中位": P([x[指标] for x in 样本["摘要"]], 0.5), "P90": P([x[指标] for x in 样本["摘要"]], 0.9),
                             "n": len(样本["摘要"])} for 指标 in ("数字个数", "对冲个数", "缩写个数")}
    合并 = [n for x in 样本["正文章"] for n in x["每段数字"]]
    if 合并:
        分布["段内数字"] = {"中位": P(合并, 0.5), "P75": P(合并, 0.75), "P90": P(合并, 0.9), "最大": max(合并), "段数": len(合并),
                        "超4段占比": round(sum(1 for n in 合并 if n > 4) / len(合并), 2)}
    print("分布：", json.dumps(分布, ensure_ascii=False, indent=1))
    篇数 = len(逐篇)
    if 只看 or 篇数 < 3:
        print(f"{'--只看' if 只看 else f'仅 {篇数} 篇（<3）'}：不写回阈值文件")
        return 0
    阈 = json.load(open(阈值文件, encoding="utf-8"))
    未写 = []
    for 类 in ("摘要", "首读章", "正文章"):
        if 类 not in 分布 or 分布[类]["数字密度"]["n"] < 3:
            未写.append(类)
            continue
        阈.setdefault("数字密度_上限", {})[类] = 分布[类]["数字密度"]["P90"]
        阈.setdefault("对冲密度_上限", {})[类] = max(分布[类]["对冲密度"]["P90"], 1.0)
        阈.setdefault("句均汉字_上限", {})[类] = 分布[类]["句均汉字"]["P90"]
    if "摘要计数" in 分布 and 分布["摘要计数"]["数字个数"]["n"] >= 3:
        阈.setdefault("摘要画像", {})["数字个数_上限"] = int(round(分布["摘要计数"]["数字个数"]["P90"]))
        阈["摘要画像"]["对冲个数_上限"] = max(int(round(分布["摘要计数"]["对冲个数"]["P90"])), 1)
        阈["摘要画像"]["句均汉字_上限"] = 分布["摘要"]["句均汉字"]["P90"]
    if "段内数字" in 分布:
        阈["段内数字"] = {"硬线": int(round(分布["段内数字"]["P90"])), "软线": int(round(分布["段内数字"]["P75"])),
                      "说明": f"硬线=G4 阻塞（超预算段）= 范文全部正文段的 P90；软线=报告里标密集段 = P75；"
                              f"范文 {分布['段内数字']['段数']} 段，>4 个数字的段占 {分布['段内数字']['超4段占比']}"}
    阈["出处"] = (f"范文分布：运行时/范文/ 下 {篇数} 篇真稿（研究生数模一等奖，散文过滤口径）的 P90，验证/范文分布.py 生成；"
                f"对冲密度上限有 1.0/千汉字 的置信声明余量；缩写个数/段首数字 保持先验；手改无效")
    阈["更新"] = datetime.date.today().isoformat()
    阈["范文分布"] = {"篇": sorted(逐篇), "口径": "散文过滤（见 验证/范文分布.py 模块说明）", "分布": 分布,
                  "逐篇": {n: {类: {k: v for k, v in x[类].items() if k != "每段数字"} for 类 in x} for n, x in 逐篇.items()},
                  "未写回的类": 未写}
    json.dump(阈, open(阈值文件, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"已写回 {阈值文件}（{篇数} 篇；未写回的类：{未写 or '无'}）")
    return 0


if __name__ == "__main__":
    sys.exit(主(只看="--只看" in sys.argv, 对照=[a[5:] for a in sys.argv[1:] if a.startswith("--对照=")]))
