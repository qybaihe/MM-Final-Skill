#!/usr/bin/env python3
"""表达画像：论文表达的可量化形态——数字密度 / 对冲密度 / 句长 / 缩写 / 段首数字 / 段内数字预算。

为什么单独成模块：合规阈值只认真稿分布（对抗性重构方案 v2 原则 P7、表达优化方案 v1 A3）。
范文是 PDF（pdftotext 出来的纯文本），我们的稿是 tex；两边要落在**同一把尺**上，
量尺就不能散在 审计.py 和 范文分布.py 各写一份。本模块被两者共用：
  · bin/审计.py（工作根内）——第 12 节表达画像、第 13 节自造缩写、第 14 节图题、段级溯源
  · 验证/范文分布.py——对 运行时/范文/*.pdf 跑同一画像，把分布写回 运行时/表达阈值.json

口径（两边一致）：
  1. 先把 tex 拍平成"读者看到的文字"：去注释、去表格/代码/tikz 整块、figure 只留 caption、
     去掉 \\ref/\\cite/\\label/\\includegraphics 等纯标记、去掉版式命令；显示公式整块删掉
     （pdftotext 里公式是碎片，两边都不拿它计数才公平）；行内公式保留其中的数字。
  2. 只保留"有 ≥4 个汉字的行"再计数：表格行、公式碎片、页码、页眉在 PDF 文本里都过不了这一关。
  3. 数字 = (?<![A-Za-z\\\\])\\d+(?:\\.\\d+)?%?；对冲 = 运行时/对冲词.txt 逐词计次；
     句 = 按 。；！？ 切、≥6 汉字的片段；缩写 = 含 ≥2 个大写字母的英文串且不在白名单。
量纲全部是"次/千汉字"，与 审计.py 第 11 节内部术语密度同口径。
"""
import re

汉字re = re.compile(r"[\u4e00-\u9fff]")
数字re = re.compile(r"(?<![A-Za-z\\])\d+(?:\.\d+)?%?")
缩写re = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]*[A-Z][A-Za-z0-9]*[A-Z])[A-Za-z][A-Za-z0-9]*(?![A-Za-z0-9])")
整块环境 = ("longtable", "tabular", "tabularx", "tabu", "lstlisting", "verbatim", "tikzpicture", "thebibliography")
公式环境 = ("equation", "equation*", "align", "align*", "gather", "gather*", "multline", "multline*",
            "eqnarray", "eqnarray*", "flalign", "flalign*", "alignat", "alignat*", "displaymath")
纯标记命令 = ("ref", "eqref", "cite", "citep", "citet", "label", "includegraphics", "url", "href", "input",
              "include", "pageref", "autoref", "cref", "Cref", "nameref")
版式命令 = ("vspace", "hspace", "vspace*", "hspace*", "setlength", "addtolength", "zihao", "Needspace", "needspace",
            "linespread", "renewcommand", "newcommand", "resizebox", "rule", "noindent", "centering", "selectfont",
            "toprule", "midrule", "bottomrule", "hline", "cline", "endfirsthead", "endhead", "endfoot", "endlastfoot",
            "newpage", "clearpage", "pagestyle", "thispagestyle", "setcounter", "addcontentsline", "vfill", "hfill",
            "medskip", "smallskip", "bigskip", "par", "indent", "songti", "heiti", "kaishu", "fangsong", "song", "hei",
            "bfseries", "itshape", "small", "footnotesize", "scriptsize", "large", "Large", "normalsize", "maketitle",
            "keywords", "captionsetup", "FloatBarrier", "begingroup", "endgroup", "PassOptionsToPackage")


def 读词表(路径):
    """一行一词；# 开头是注释；空行跳过。文件缺失返回空表——调用方必须把"空表"当异常上报，
    不能静默空过（病根 R18：隐式依赖静默失效）。"""
    try:
        return [w.strip() for w in open(路径, encoding="utf-8") if w.strip() and not w.lstrip().startswith("#")]
    except Exception:
        return []


def 去注释(tex):
    """去掉行内 % 注释（保留 \\%）。"""
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


def _删环境(t, 名们, 保留caption=False):
    for e in 名们:
        pat = r"\\begin\{" + re.escape(e) + r"\}.*?\\end\{" + re.escape(e) + r"\}"
        if 保留caption:
            t = re.sub(pat, lambda m: " ".join(re.findall(r"\\caption\{((?:[^{}]|\{[^{}]*\})*)\}", m.group(0))), t, flags=re.S)
        else:
            t = re.sub(pat, " ", t, flags=re.S)
    return t


def 拍平(tex):
    """tex → 读者看到的文字（口径见模块说明）。输入应是**未去注释**或已去注释的 tex 都行。"""
    t = 去注释(tex)
    t = _删环境(t, 整块环境)
    t = _删环境(t, ("figure", "figure*", "table", "table*", "wrapfigure"), 保留caption=True)
    t = _删环境(t, 公式环境)
    t = re.sub(r"\\\[.*?\\\]", " ", t, flags=re.S)                     # \[ … \]
    for c in 纯标记命令:
        t = re.sub(r"\\" + re.escape(c) + r"\*?(\[[^\]]*\])?\{[^{}]*\}", " ", t)
    for c in 版式命令:
        t = re.sub(r"\\" + re.escape(c) + r"(\[[^\]]*\])?(\{[^{}]*\}){0,2}", " ", t)
    t = re.sub(r"\\(begin|end)\{[a-zA-Z*]+\}(\[[^\]]*\])?(\{[^{}]*\})?", " ", t)
    t = re.sub(r"\\(sub)*section\*?\{", "{", t)                            # 标题文字保留
    t = re.sub(r"\\caption\{", "{", t)
    t = re.sub(r"\\text(bf|it|rm|sf|tt|normal)?\{", "{", t)
    t = re.sub(r"\\(mathrm|mathbf|mathit|mathsf|mathcal|boldsymbol|operatorname)\{([^{}]*)\}", r"\2", t)
    t = re.sub(r"\\[a-zA-Z@]+\*?", " ", t)                                 # 其余命令名去掉，保留参数文字
    t = re.sub(r"\\\\(\[[^\]]*\])?", " ", t)
    t = re.sub(r"\d+(?:\.\d+)?\s*\^\s*(?:\{[^{}]*\}|-?[A-Za-z0-9])", "幂", t)   # 10^{4}/10^4 整体是量级，不是统计数字
    t = re.sub(r"\^\{[^{}]*\}|\^-?[A-Za-z0-9]", "", t)                    # 上标（10^{-4}、10^{-16}）不是统计数字
    t = re.sub(r"_\{[^{}]*\}|_[A-Za-z0-9]", "", t)                          # 下标同理（d_{\mu m}）
    t = re.sub(r"[{}$&~^_]", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t


def 有效行(text, 最少汉字=4):
    return [l for l in text.split("\n") if len(汉字re.findall(l)) >= 最少汉字]


def 句们(text):
    t = re.sub(r"\$[^$]*\$", "X", text)
    return [s for s in re.split(r"[。；！？]", t) if len(汉字re.findall(s)) > 5]


def 缩写们(text, 白名单):
    白 = set(白名单)
    out = []
    for tok in 缩写re.findall(text):
        if tok in 白 or tok.rstrip("s") in 白:
            continue
        out.append(tok)
    return out


def 画像(text, 对冲词, 白名单, 已拍平=True):
    """对一段"读者看到的文字"计数。返回值全是基本类型，方便直接进 JSON。"""
    t = text if 已拍平 else 拍平(text)
    行 = 有效行(t)
    体 = "\n".join(行)
    汉 = len(汉字re.findall(体))
    数 = len(数字re.findall(体))
    对 = sum(len(re.findall(re.escape(w), 体)) for w in 对冲词)
    句 = 句们(体)
    句均 = (sum(len(汉字re.findall(s)) for s in 句) / len(句)) if 句 else 0.0
    缩 = 缩写们(体, 白名单)
    千 = 汉 / 1000.0 if 汉 else 0.0
    return {"汉字": 汉, "数字个数": 数, "对冲个数": 对, "句数": len(句),
            "数字密度": round(数 / 千, 2) if 千 else 0.0,
            "对冲密度": round(对 / 千, 2) if 千 else 0.0,
            "句均汉字": round(句均, 1), "缩写个数": len(缩), "缩写": sorted(set(缩))[:20]}


# ---------------------------------------------------------------- tex 专检（范文 PDF 上做不了的）
def 段落们(tex):
    """按空行切段，返回 [(起始行号, 原文块)]。原文块**含注释**——段级溯源要从注释里读 % src。"""
    out, 块, 起 = [], [], None
    for i, line in enumerate(tex.split("\n"), 1):
        if line.strip():
            if 起 is None:
                起 = i
            块.append(line)
        else:
            if 块:
                out.append((起, "\n".join(块)))
            块, 起 = [], None
    if 块:
        out.append((起, "\n".join(块)))
    return out


def 是正文段(块):
    """正文段 = 去注释后含 ≥8 汉字、且不是整块环境（表/代码/图/公式）也不是纯标题。"""
    t = 去注释(块)
    if re.search(r"\\begin\{(" + "|".join(re.escape(e) for e in 整块环境 + 公式环境 + ("figure", "table")) + r")\}", t):
        # 段里嵌了整块环境：只要剥掉环境后还剩正文就算正文段
        t2 = _删环境(_删环境(t, 整块环境 + 公式环境), ("figure", "table"))
        if len(汉字re.findall(t2)) < 8:
            return False
    if len(汉字re.findall(t)) < 8:
        return False
    if re.match(r"^\s*\\(sub)*section", t):
        return len(汉字re.findall(re.sub(r"\\(sub)*section\*?\{[^}]*\}", "", t))) >= 8
    return True


def 段落正文(块):
    """正文段里"读者看到的散文"：去环境、去标记、保留行内公式的数字。"""
    return 拍平(块)


def 段首数字(块):
    """段落第一行散文是否以数字开头（含 $7.7…$ 这种行内公式开头）。"""
    t = 段落正文(块).strip()
    return bool(re.match(r"^[\(（]?\d", t))


def 段内数字(块, 排除年份=True):
    t = 段落正文(块)
    nums = 数字re.findall(t)
    if 排除年份:
        nums = [n for n in nums if not (n.isdigit() and 1990 <= int(n) <= 2035)]
    return nums


def 标题们(tex):
    """[(行号, 标题文字)]：section/subsection/subsubsection/title/caption。"""
    out = []
    for i, line in enumerate(去注释(tex).split("\n"), 1):
        for m in re.finditer(r"\\(?:(?:sub)*section\*?|title|caption)\{((?:[^{}]|\{[^{}]*\})*)\}", line):
            out.append((i, m.group(1)))
    return out


def 图题们(tex):
    """[(行号, 图题文字)] 只取 \\caption。"""
    out = []
    for i, line in enumerate(去注释(tex).split("\n"), 1):
        for m in re.finditer(r"\\caption\{((?:[^{}]|\{[^{}]*\})*)\}", line):
            out.append((i, m.group(1)))
    return out


def 解析src(块):
    """段落里的全部 % src 标注 → [(文件, [键…])]。语法：% src:文件:键1;键2  一行可多个 % src。"""
    out = []
    for line in 块.split("\n"):
        for m in re.finditer(r"%\s*src:\s*([^:\s%]+)(?::([^%\n]*))?", line):
            文件 = m.group(1).strip()
            键 = [k.strip() for k in (m.group(2) or "").split(";") if k.strip()]
            out.append((文件, 键))
    return out


if __name__ == "__main__":
    样 = r"""\section{问题一}
本文先说明对象。厚度为$7.755\,\mu\mathrm m$，相对差$0.63\%$。% src:a.json:k
\begin{equation}
x = 4\pi d
\end{equation}
7.8 是段首数字。

图\ref{fig:1}显示 DOP-VP 与 RMSE。这是保守的诊断性结论。
"""
    print(画像(样, ["保守", "诊断"], ["RMSE"], 已拍平=False))
    for 起, 块 in 段落们(样):
        print(起, 是正文段(块), 段首数字(块), 段内数字(块), 解析src(块))
