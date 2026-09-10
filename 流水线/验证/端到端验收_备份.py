#!/usr/bin/env python3
"""施工方案 §七 端到端验收清单：对 真题测试/成品 逐项机械核对，输出全绿/未过明细。

用法：python3 流水线/验证/端到端验收.py [成品目录]
默认 成品目录 = 真题测试/成品

设计原则：**只读**、**可重复跑**、**每项都给出实测值而不只给结论**——
未过的项必须能直接看出差多少，方便定位。
"""
import glob
import json
import os
import pathlib
import re
import sys

成品 = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "真题测试/成品")
根 = pathlib.Path(__file__).parent.parent.parent
if not 成品.is_absolute():
    成品 = 根 / 成品
状态文件 = 成品.parent / "状态.json"

结果 = []   # (类别, 项目, 通过bool, 实测说明)


def 记(类别, 项目, 通过, 说明=""):
    结果.append((类别, 项目, bool(通过), str(说明)))


def 读json(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def 找(模式):
    return sorted(glob.glob(str(成品 / 模式), recursive=True))


# ============================================================ A. 产物齐备
pdf = 找("**/论文.pdf") or 找("论文.pdf")
记("产物", "论文.pdf", bool(pdf), f"{os.path.getsize(pdf[0])} 字节" if pdf else "缺失")

texs = 找("**/*.tex")
记("产物", "全部 tex", len(texs) >= 5, f"{len(texs)} 个 tex")

页png = [p for p in 找("**/*.png") if "页" in p or "page" in p.lower()]
记("产物", "页渲染 png", len(页png) >= 1, f"{len(页png)} 张页图")

脚本 = [p for p in 找("**/*.py") if "求解" in p or "复算" in p or "原型" in p]
记("产物", "求解脚本", len(脚本) >= 3, f"{len(脚本)} 个脚本")

结果json = [p for p in 找("**/*.json") if "结果" in p or "验证" in p]
记("产物", "结果 JSON", len(结果json) >= 3, f"{len(结果json)} 个结果 JSON")

图png = [p for p in 找("**/*.png") if p not in 页png]
记("产物", "图 png", len(图png) >= 1, f"{len(图png)} 张图")

红队们 = 找("**/红队_问题*.json")
记("产物", "红队结果", len(红队们) >= 1, f"{len(红队们)} 份红队报告")

for 名, 模式 in (("题面契约", "**/题面契约.json"), ("计划v2", "**/计划.json"),
                 ("锦标赛", "**/锦标赛_问题*.json"), ("假设台账", "**/假设台账_问题*.json"),
                 ("实验记录", "**/实验记录.json"), ("论点脊柱", "**/论点脊柱.json"),
                 ("需求追踪矩阵", "**/需求追踪矩阵.json")):
    命中 = 找(模式)
    记("产物", f"交接/{名}", bool(命中), f"{len(命中)} 个" if 命中 else "缺失")

矩阵路径 = 找("**/需求追踪矩阵.json")
if 矩阵路径:
    m = 读json(矩阵路径[0]) or []
    未销 = [x.get("需求号") for x in m if isinstance(x, dict)
            and not str(x.get("状态", "")).startswith(("已落位", "已销号", "已完成"))]
    记("产物", "需求追踪矩阵全销号", not 未销,
       f"共 {len(m)} 条，未销号 {len(未销)} 条{('：' + str(未销[:6])) if 未销 else ''}")
else:
    记("产物", "需求追踪矩阵全销号", False, "矩阵缺失")

for 名, 模式 in (("五路审稿 JSON", "**/审稿/*.json"), ("修订单", "**/修订单*.json"),
                 ("审计报告", "**/审计报告.json"), ("门检报告", "**/门检_*.json"),
                 ("复盘报告", "**/复盘报告*.json")):
    命中 = 找(模式)
    记("产物", 名, bool(命中), f"{len(命中)} 个" if 命中 else "缺失")

# ============================================================ B. 硬指标
审计 = None
for p in 找("**/审计报告.json"):
    审计 = 读json(p)
    if 审计:
        break

# 编译指标从 状态.json / 运行报告 里取
运行报告 = 成品.parent / "运行报告.md"
报告文 = 运行报告.read_text(encoding="utf-8") if 运行报告.exists() else ""
m = re.search(r"终态：E=(\d+) Overfull=(\d+) 页数=(\d+)", 报告文)
if not m:
    日志 = 成品.parent / "运行日志.md"
    if 日志.exists():
        m = re.search(r"终态：E=(\d+) Overfull=(\d+) 页数=(\d+)", 日志.read_text(encoding="utf-8"))
if m:
    E, O, P = int(m.group(1)), int(m.group(2)), int(m.group(3))
    记("硬指标", "编译 E=0", E == 0, f"E={E} Overfull={O}")
    记("硬指标", "正文 ≤20 页", P <= 21, f"总页数={P}（含附录）")
else:
    记("硬指标", "编译 E=0", False, "日志中未找到终态行（可能未到 S6）")
    记("硬指标", "正文 ≤20 页", False, "同上")

# 摘要 1 页 / 无目录 / 附录代码：直接查 tex
摘要tex = [p for p in texs if "摘要" in p or "abstract" in p.lower()]
记("硬指标", "摘要 tex 存在", bool(摘要tex), f"{len(摘要tex)} 个")
主tex = [p for p in texs if "论文.tex" in p or "main" in p.lower()]
无目录 = True
附录有码 = False
for p in 主tex + texs:
    try:
        t = open(p, encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    if "\\tableofcontents" in t:
        无目录 = False
    if "lstlisting" in t and len(re.findall(r"\\begin\{lstlisting\}", t)) >= 1:
        附录有码 = True
记("硬指标", "无目录", 无目录, "未发现 \\tableofcontents" if 无目录 else "发现 \\tableofcontents")
记("硬指标", "附录含 lstlisting 源码", 附录有码, "有" if 附录有码 else "未发现 lstlisting")

if 审计:
    禁 = (审计.get("禁用词") or {}).get("数量")
    记("硬指标", "禁用词命中=0", 禁 == 0, f"命中 {禁} 处")
    句 = (审计.get("图表引用句式") or {}).get("占比")
    记("硬指标", '"如图/如表"占比 ≤40%', (句 is None or 句 <= 0.4), f"占比={句}")
    段 = 审计.get("段首重复") or {}
    坏 = {k: v for k, v in 段.items() if isinstance(v, int) and v >= 3}
    记("硬指标", "段首开场词重复合规", not 坏, f"超限词={坏}" if 坏 else "无超限")
    图型 = 审计.get("图型") or {}
    图数 = 图型.get("图文件数")
    记("硬指标", "图 16-22 张", (isinstance(图数, int) and 16 <= 图数 <= 22), f"图数={图数}")
    柱折 = 图型.get("柱折占比")
    记("硬指标", "柱折合计 ≤半", (柱折 is None or 柱折 <= 0.5), f"柱折占比={柱折}")
    溯 = 审计.get("溯源核验") or {}
    存疑 = 溯.get("存疑数")
    记("硬指标", "溯源核验存疑=0", 存疑 == 0, f"存疑 {存疑} 条")
    台 = 审计.get("假设台账") or {}
    记("硬指标", "假设台账无违规", (台.get("违规数") == 0), f"违规 {台.get('违规数')} 条")
else:
    for 项 in ("禁用词命中=0", '"如图/如表"占比 ≤40%', "段首开场词重复合规",
               "图 16-22 张", "柱折合计 ≤半", "溯源核验存疑=0", "假设台账无违规"):
        记("硬指标", 项, False, "审计报告缺失")

# 红队结论
for p in 红队们:
    d = 读json(p) or {}
    问 = d.get("问题", pathlib.Path(p).stem)
    结论 = str(d.get("结论", "")).strip()
    仲 = 找(f"**/仲裁_问题{问}.json")
    有仲裁 = bool(仲) and bool((读json(仲[0]) or {}).get("总裁定"))
    记("硬指标", f"问{问} 红队对齐或仲裁在案",
       结论.startswith("对齐") or 有仲裁, f"结论={结论 or '空'}，仲裁在案={有仲裁}")

# 评分 / 硬伤 / 美观
评分们 = []
for p in 找("**/审稿/*.json"):
    d = 读json(p)
    if isinstance(d, dict):
        for k in ("总分", "评分", "均分", "美观分"):
            v = d.get(k)
            if isinstance(v, (int, float)):
                评分们.append((pathlib.Path(p).name, k, v))
记("硬指标", "有评分记录", bool(评分们), f"{len(评分们)} 条评分：{评分们[:5]}")

硬伤们 = 找("**/硬伤_*.json")
末轮空 = None
if 硬伤们:
    d = 读json(sorted(硬伤们)[-1])
    末轮空 = isinstance(d, list) and len(d) == 0
记("硬指标", "末轮硬伤清单为空", bool(末轮空),
   f"末轮硬伤文件={pathlib.Path(sorted(硬伤们)[-1]).name if 硬伤们 else '缺失'}，条数={len(读json(sorted(硬伤们)[-1]) or []) if 硬伤们 else 'NA'}")

# 状态 / 腿数 / 时长
st = 读json(状态文件)
if st:
    腿 = st.get("腿数")
    记("硬指标", "总腿数 ≤220", (isinstance(腿, int) and 腿 <= 220), f"腿数={腿}")
    import time
    t0 = st.get("起始时间")
    时长 = (time.time() - t0) / 3600 if t0 else None
    记("硬指标", "总时长 ≤14h", (时长 is not None and 时长 <= 14), f"用时={时长:.1f}h" if 时长 else "无起始时间")
    门 = st.get("问题门状态") or {}
    记("硬指标", "每问 G2 有过门记录", bool(门), f"问题门={门}")
    降 = st.get("降级放行") or []
    记("硬指标", "无降级放行（有则需人工关注）", not 降, f"降级 {len(降)} 项：{[x.get('门') for x in 降]}")
    记("硬指标", "状态.json 完整可复盘",
       all(k in st for k in ("运行ID", "已完成节点", "腿数", "问题门状态")), f"键={list(st.keys())[:8]}")
else:
    for 项 in ("总腿数 ≤220", "总时长 ≤14h", "每问 G2 有过门记录", "无降级放行（有则需人工关注）", "状态.json 完整可复盘"):
        记("硬指标", 项, False, "状态.json 缺失")

# DAG 顺序（日志可查）
日志文件 = 成品.parent / "运行日志.md"
if 日志文件.exists():
    L = 日志文件.read_text(encoding="utf-8")
    有DAG = "依赖DAG" in L and "拓扑分层" in L
    记("硬指标", "依赖 DAG 与执行顺序可查", 有DAG,
       re.search(r"S2 依赖DAG：.*", L).group(0)[:80] if 有DAG else "日志无 DAG 行")
else:
    记("硬指标", "依赖 DAG 与执行顺序可查", False, "运行日志缺失")

# ============================================================ 输出
print("=" * 78)
print(f"施工方案 §七 端到端验收清单  —  成品目录：{成品}")
print("=" * 78)
当前 = None
过 = 总 = 0
for 类别, 项目, ok, 说明 in 结果:
    if 类别 != 当前:
        print(f"\n【{类别}】")
        当前 = 类别
    总 += 1
    过 += ok
    print(f"  [{'✓' if ok else '✗'}] {项目:32s} {说明}")
print("\n" + "=" * 78)
print(f"总计：{过}/{总} 项通过（{'全绿' if 过 == 总 else str(总-过) + ' 项未过'}）")
print("=" * 78)
未过 = [(c, i, s) for c, i, ok, s in 结果 if not ok]
if 未过:
    print("\n未过项汇总：")
    for c, i, s in 未过:
        print(f"  - [{c}] {i} → {s}")
