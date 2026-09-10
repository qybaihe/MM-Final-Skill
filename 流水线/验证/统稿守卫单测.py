#!/usr/bin/env python3
"""统稿守卫单测：语言层改动放行，事实层改动（数字/引用/公式/标题/src）逐项拦截。用法：python3 统稿守卫单测.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "运行时"))
import 统稿守卫 as 守  # noqa: E402

旧 = r"""\section{问题一的建模与求解：一次往返的色散反演}
厚度反演给出 10.52 微米，比基线低 3.1\%。% src:求解/问题1/结果/核心.json:厚度均值;基线差
\begin{equation}\label{eq:phase} d = \frac{m\lambda}{2n} \end{equation}
证据落在图\ref{fig:q1}，与文献\cite{fresnel1823}一致。
\begin{longtable}{cc} 角度 & 厚度 \\ 10 & 10.52 \\ 15 & 10.49 \end{longtable}
\includegraphics[width=0.8\textwidth]{../求解/问题1/图片/q1.png}
"""
用例 = [
    ("语言层改写（放行）", 旧.replace("厚度反演给出 10.52 微米，比基线低 3.1\\%。", "先看结论：外延层厚 10.52 微米，比基线低 3.1\\%。"), True),
    ("数字搬进表格（放行）", 旧.replace("15 & 10.49", "15 & 10.49").replace("比基线低 3.1\\%。", "比基线低。") + "\\begin{longtable}{c} 3.1\\% \\end{longtable}\n", True),
    ("改数字（拦）", 旧.replace("10.52 微米", "10.5 微米"), False),
    ("删数字（拦）", 旧.replace("，比基线低 3.1\\%", ""), False),
    ("改引用键（拦）", 旧.replace("\\ref{fig:q1}", "\\ref{fig:q2}"), False),
    ("删公式（拦）", 旧.replace("\\begin{equation}\\label{eq:phase} d = \\frac{m\\lambda}{2n} \\end{equation}\n", ""), False),
    ("改标题（拦）", 旧.replace("一次往返的色散反演", "色散反演"), False),
    ("删 src（拦）", 旧.replace("% src:求解/问题1/结果/核心.json:厚度均值;基线差", ""), False),
    ("补 src（放行）", 旧.replace("一致。", "一致。% src:交接/文献.json:fresnel"), True),
    ("删插图（拦）", 旧.replace("\\includegraphics[width=0.8\\textwidth]{../求解/问题1/图片/q1.png}\n", ""), False),
]
过 = 0
for 名, 新, 期望 in 用例:
    ok, 差 = 守.守卫(旧, 新)
    符 = ok == 期望
    过 += 符
    print(f"{'✓' if 符 else '✗'} {名}: 通过={ok} 差异={差[:2]}")
报 = 守.逐文件({"a.tex": 旧, "b.tex": 旧}, {"a.tex": 旧})
符 = (not 报["通过"]) and 报["逐文件"]["b.tex"]["差异"] == ["文件消失"]
过 += 符
print(f"{'✓' if 符 else '✗'} 文件消失（拦）: {报['逐文件']['b.tex']}")
总 = len(用例) + 1
print(f"\n{过}/{总} 用例符合期望")
sys.exit(0 if 过 == 总 else 1)
