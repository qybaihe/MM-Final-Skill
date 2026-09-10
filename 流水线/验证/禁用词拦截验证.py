#!/usr/bin/env python3
"""工单3.1 验收项：故意在一章 tex 里塞禁用词，确认被"机械审计 + G4 门检"逮住并能修掉。

施工方案 工单3.1 的验收原文要求"故意在一章塞禁用词，确认被章评或审计逮住并修掉"。
章评是模型主观判断（每次表述可能不同，不适合做确定性验收），因此这里验证**机械侧**：
  1) 干净稿  → 审计 禁用词违规=0、G4 门检 该项通过
  2) 塞词稿  → 审计 逮到并逐条报出词与位置、G4 门检 不通过且明细点到"禁用词"
  3) 修掉后  → 回到 0（证明"能修掉"这一步闭环，而不是只会报警）

在本地临时目录下跑真实的 bin/审计.py 与 bin/门检.py，不动正式运行目录。
本地版（M5-0）：审计/门检的 chdir 已改为脚本相对（bin/ 的上级），拷进夹具即自动指向夹具，
沙箱时代的 sed 改写与幻影 ENOENT 重试一并退役。
用法：python3 流水线/验证/禁用词拦截验证.py
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from 本地蜂巢 import LocalHive

h = LocalHive(root=tempfile.mkdtemp(prefix="禁用词验证_"))
根 = str(h.root / "禁用词测试")

# 取 3 个真实禁用词（从项目禁用词表里挑，避免我凭印象编）
禁用词表 = (pathlib.Path(__file__).parent.parent / "运行时/禁用词.txt").read_text(encoding="utf-8")
候选 = [w.strip() for w in 禁用词表.splitlines() if w.strip() and not w.strip().startswith("#")]
用词 = 候选[:3]
print(f"禁用词表共 {len(候选)} 条，本次注入：{用词}")

脚本 = r'''
import json, os, shutil, subprocess, sys
根, 词1, 词2, 词3 = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
蜂巢 = sys.argv[5]
shutil.rmtree(根, ignore_errors=True)
for d in ("论文", "审稿", "运行时", "bin", "交接", "日志", "求解/问题1/结果", "论文/图"):
    os.makedirs(f"{根}/{d}", exist_ok=True)
W = lambda p, s: open(f"{根}/{p}", "w", encoding="utf-8").write(s)

# 复制真实的审计/门检/禁用词表进夹具（chdir 脚本相对，拷进夹具即指向夹具，无需改写）
for src, dst in ((f"{蜂巢}/bin/审计.py", f"{根}/bin/审计.py"),
                 (f"{蜂巢}/bin/门检.py", f"{根}/bin/门检.py"),
                 (f"{蜂巢}/运行时/禁用词.txt", f"{根}/运行时/禁用词.txt")):
    shutil.copy(src, dst)

干净 = (r"\section{问题一模型的建立与求解}" "\n"
        r"本节基于双光束干涉建立厚度反演模型。测得厚度为 $10.52\,\mu\mathrm{m}$ % src:求解/问题1/结果/核心.json:厚度均值" "\n"
        r"该结果与红队独立复算的 $10.51\,\mu\mathrm{m}$ 一致，相对偏差 $0.1\%$。" "\n"
        r"如图~\ref{fig:1} 所示，残差在全谱段无系统性漂移。" "\n")

def 写稿(脏):
    正文 = 干净
    if 脏:
        # 三个真实禁用词分别塞进不同句子，模拟撰稿师"不小心"写出套话
        正文 += (f"本模型{词1}，{词2}地刻画了干涉过程。" "\n"
                 f"综上，该方法{词3}，可为工业检测提供参考。" "\n")
    W("论文/3.问题1.tex", 正文)

W("论文/main.tex", r"\documentclass{article}\begin{document}\input{3.问题1}\end{document}")
W("论文/main.log", "Output written on main.pdf (12 pages).\n")
os.makedirs(f"{根}/论文/图", exist_ok=True)

def 跑(阶段):
    print(f"\n########## {阶段} ##########")
    r = subprocess.run([sys.executable, "bin/审计.py"], cwd=根, capture_output=True, text=True, timeout=300)
    rep = {}
    try:
        rep = json.load(open(f"{根}/审稿/审计报告.json", encoding="utf-8"))
    except Exception as e:
        print("读审计报告失败:", e)
    禁 = rep.get("禁用词", {})
    print(f"审计[禁用词] 数量={禁.get('数量')}")
    for d in (禁.get("明细") or [])[:6]:
        print("   ", json.dumps(d, ensure_ascii=False))
    # G4 门检里的禁用词项
    g = subprocess.run([sys.executable, "bin/门检.py", "G4"], cwd=根, capture_output=True, text=True, timeout=300)
    gj = {}
    try:
        gj = json.load(open(f"{根}/审稿/门检_G4.json", encoding="utf-8"))
    except Exception as e:
        print("读G4门检失败:", e)
    禁项 = [x for x in (gj.get("明细") or []) if "禁用词" in str(x)]
    print(f"G4门检 通过={gj.get('通过')}；禁用词相关明细={禁项}")
    return int(禁.get("数量") or 0), 禁项

print("禁用词表条数：", len([l for l in open(f"{根}/运行时/禁用词.txt", encoding="utf-8") if l.strip()]))

写稿(False); 干净数, 干净项 = 跑("阶段1：干净稿（期望 禁用词数=0）")
写稿(True);  脏数,  脏项  = 跑("阶段2：塞入3个真实禁用词（期望 禁用词数=3 且G4逮住）")
写稿(False); 修数,  修项  = 跑("阶段3：修掉后（期望 回到0）")

print("\n===== 判定 =====")
ok1 = 干净数 == 0
ok2 = 脏数 == 3 and bool(脏项)
ok3 = 修数 == 0
print(f"[{'PASS' if ok1 else 'FAIL'}] 干净稿不误报（禁用词数 {干净数}，期望 0）")
print(f"[{'PASS' if ok2 else 'FAIL'}] 塞词被审计逐条逮住且G4门检报出（数 {脏数}/期望3，G4明细 {'有' if 脏项 else '无'}）")
print(f"[{'PASS' if ok3 else 'FAIL'}] 修掉后归零（禁用词数 {修数}，期望 0）")
print("总判定：", "全部通过" if (ok1 and ok2 and ok3) else "存在失败项")
'''

流水线 = pathlib.Path(__file__).parent.parent
h.exec(f'python3 bin/禁用词验证.py {根} {用词[0]} {用词[1]} {用词[2]} {h.root}',
       files=[h.f_text("bin/禁用词验证.py", 脚本),
              h.f_local("bin/审计.py", 流水线 / "运行时/审计.py"),
              h.f_local("bin/门检.py", 流水线 / "运行时/门检.py"),
              h.f_local("运行时/禁用词.txt", 流水线 / "运行时/禁用词.txt")],
       timeout_s=600)
