#!/usr/bin/env python3
"""跑后指标：从 驱动 stdout 日志 + 状态.json + 台账 + 审稿 JSON 抽数，给交付报告用（references/交付报告模板.md 各节按此填）。
用法：python3 跑后指标.py <产出目录> [--日志=路径] [--markdown]
读的都是驱动落盘的文件，不碰运行中的进程；随时可跑。"""
import collections
import json
import pathlib
import re
import sys

参 = [a for a in sys.argv[1:] if not a.startswith("--")]
选 = [a for a in sys.argv[1:] if a.startswith("--")]
if not 参:
    print(__doc__); sys.exit(1)
产出 = pathlib.Path(参[0]); 父 = 产出.parent
日志 = next((pathlib.Path(a[5:]) for a in 选 if a.startswith("--日志=")), None)
if 日志 is None:
    记 = 父 / ".驾驶_日志"
    if 记.exists():
        日志 = pathlib.Path(记.read_text(encoding="utf-8").strip())
    else:
        名 = 产出.name.removeprefix("成品_").removeprefix("成品") or "默认"
        日志 = 父 / f"驾驶_{名}.out"
if not 日志.exists():
    print(f"!! 日志不存在：{日志}"); sys.exit(1)
L = 日志.read_text(encoding="utf-8", errors="replace").splitlines()
md = "--markdown" in 选

def 抓(p): return [l for l in L if re.search(p, l)]
def 秒(l):
    m = re.search(r"\[(\d+)s\]", l); return int(m.group(1)) if m else None
def 节(标题): print(f"\n## {标题}" if md else f"## {标题}")
def 行(*xs): print(("  " if not md else "- ") + " ".join(str(x) for x in xs))

节("启动/续跑")
for l in 抓(r"^===== 后台启动"): 行(l[:110])
节("终态/预算")
for l in 抓(r"终态：|驾驶结束|预算护栏|异常终止|应急出版"): 行(l[:170])
最后 = [秒(l) for l in L if 秒(l) is not None]
if 最后: 行(f"累计机时 {最后[-1]/3600:.1f} h（[secs] 跨续跑连续，不含停跑间隔）")
节("腿数")
派 = 抓(r"波次 \d+ 腿")
行(f"波次 {len(派)} 次；腿数标记（续跑时）：{re.findall(r'腿数=(\d+)', chr(10).join(L))}")
状态 = 父 / "状态.json"
if 状态.exists():
    d = json.loads(状态.read_text(encoding="utf-8"))
    行(f"状态.json：腿数={d.get('腿数')} 阶段={d.get('阶段')} 已完成节点={len(d.get('已完成节点', []))} 降级放行={[x.get('门') for x in d.get('降级放行', [])]} 门失败计数={d.get('门失败计数')}")

节("阶段机时（估算：按日志行前缀 S0–S6 / 门[G] 切段累加；G2 含每问过门后的解读腿）")
阶段 = None; 上秒 = None; 累 = collections.OrderedDict()
for l in L:
    s = 秒(l)
    if s is None: continue
    文 = re.sub(r"^- \d\d:\d\d:\d\d \[\d+s\] ", "", l)
    if "已完成，跳过（续跑）" not in 文:
        m = re.match(r"(?:===== )?(S\d[ab]?)[ :：]", 文) or re.match(r"门\[(G\d)", 文) or re.match(r"节点\[(S\d|G\d|G叙事)", 文)
        if m: 阶段 = m.group(1)
    if 阶段 and 上秒 is not None and s >= 上秒:
        累[阶段] = 累.get(阶段, 0) + (s - 上秒)
    上秒 = s
for k, v in 累.items(): 行(f"{k}: {v/3600:.2f} h")
节("R32 计数（可靠性）")
行(f"429={len(抓(r'\b429\b'))} rc=142={len(抓(r'rc=142'))} 孤儿={len(抓(r'孤儿'))} Traceback={len(抓(r'Traceback'))} "
   f"腿重试={len(抓(r'第2次'))} 死腿重派={len(抓(r'死亡|重派'))} !!={len(抓(r'!!'))}")
节("门史")
for l in 抓(r"门\[G[0-9叙事:问]+\] 第\d+次检查|降级放行|升格蜂群\[|升格裁决|仲裁裁定|红队结论|过门="): 行(l[:150])
节("S3 图评")
for l in 抓(r"S3 图评|图评轮|成图")[:8]: 行(l[:150])
节("S4")
for l in 抓(r"S4 章评轮\d+[:：]|S4 章评：|读者分均值|S4 统稿|摘要画像门|S4 章修订\d+ 选章|叙事底稿"): 行(l[:170])
节("S5 审稿场")
for l in 抓(r"S5 轮\d+ 面板|S5 轮\d+ 待改|回执\[|熔断|S5 轮\d+：|S5 收敛|S5 轮数上限|回退\(|S5 续跑|裁定\["): 行(l[:190])
节("变化守卫/结构守卫")
g = 抓(r"变化守卫\[")
行(f"变化守卫 条数={len(g)} 过={sum('过 比例' in l for l in g)} 超线={sum('超线' in l for l in g)} 代码章免检={sum('代码章' in l for l in g)}")
for l in 抓(r"结构守卫"): 行(l[:150])
节("编译")
for l in 抓(r"编译#"): 行(l[:100])

节("回流账（预算见 references/监督清单.md 末表；超线 → references/提示词与回流.md §4）")
def 数(p): return len(抓(p))
转路 = sum(int(m) for l in 抓(r"回执\[.*转路 (\d+)") for m in re.findall(r"转路 (\d+)", l))
守卫超线 = [l for l in 抓(r"变化守卫\[") if "超线" in l]
回流 = [
    ("红队不齐 → 派仲裁", 数(r"红队不齐|派仲裁腿"), "每问 ≤1", "P1"),
    ("仲裁裁定 口径差异", 数(r"口径差异"), "0", "P1"),
    ("仲裁返工", 数(r"仲裁返工"), "—", "P1"),
    ("门 FAIL（各门合计）", 数(r"门\[.*第\d+次检查 FAIL"), "科学性 FAIL 由链路走", "—"),
    ("派返工（各门）", 数(r"派返工"), "同协议项 ≤2 轮", "P2"),
    ("升格蜂群", 数(r"升格蜂群\["), "≤1/问", "P2"),
    ("降级放行", 数(r"降级放行"), "—（进报告）", "—"),
    ("变化守卫 超线回退", len(守卫超线), "≤1 文件/轮", "P4/P5"),
    ("结构守卫回退", 数(r"结构守卫\[.*回退"), "0", "P4"),
    ("页数守卫回退", 数(r"页数守卫\["), "0", "R52/P4"),
    ("回执转路（条）", 转路, "美化 0；审稿场 ≤2/轮", "P6/R40"),
    ("熔断升格 / 熔断搁置", f"{数(r'熔断升格')} / {数(r'熔断搁置')}", "—", "台账"),
    ("脚本 rc≠0", 数(r"脚本 .* rc=[1-9]"), "≤4/炉", "P8"),
    ("修脚本腿", 数(r"先启 \['修脚本_"), "≤4/炉", "P8"),
    ("编译修复循环触发（E>0）", len([l for l in 抓(r"编译#") if re.search(r"E=[1-9]", l)]), "—", "D 编译类"),
    ("腿重试 / 死腿重派", f"{数(r'第2次')} / {数(r'死亡|重派')}", "—", "R32"),
]
if md:
    print("| 回流类型 | 次数 | 预算 | 条款 |"); print("|---|---|---|---|")
    for 名, n, 预, 条 in 回流: print(f"| {名} | {n} | {预} | {条} |")
else:
    for 名, n, 预, 条 in 回流: 行(f"{名}: {n}（预算 {预}；{条}）")

节("P 检测（提示词收紧是否生效；文件在 蜂巢镜像/ 与 台账/）")
镜像 = 父 / "蜂巢镜像"
换版 = sorted((镜像 / "交接").glob("换版清单_问题*.json")) if (镜像 / "交接").exists() else []
行("P3 换版清单：", [f.name for f in 换版] or "无（回炉算后应出现）")
红 = []
for f in sorted((镜像 / "交接").glob("红队_问题*.json")) if (镜像 / "交接").exists() else []:
    try:
        d = json.loads(f.read_text(encoding="utf-8")); 红.append(f"{f.name}:{'有' if '口径对照' in d else '无'}口径对照/结论={d.get('结论')}")
    except Exception as e: 红.append(f"{f.name}:读失败")
行("P1 红队报告：", 红 or "无")
返工 = []
for f in sorted((镜像 / "交接").glob("返工台账_问题*.json")) if (镜像 / "交接").exists() else []:
    try:
        d = json.loads(f.read_text(encoding="utf-8")); 条 = d if isinstance(d, list) else d.get("条目", []) or []
        返工.append(f"{f.name}:{sum(1 for x in 条 if isinstance(x, dict) and x.get('层级'))}/{len(条)} 条带层级")
    except Exception: 返工.append(f"{f.name}:读失败")
行("P2 返工单层级：", 返工 or "无")
def 台账条(名):
    q = 父 / "台账" / f"{名}.json"
    if not q.exists(): return None
    d = json.loads(q.read_text(encoding="utf-8")); return d if isinstance(d, list) else d.get("条目", []) or []
章 = 台账条("章评台账")
if 章 is not None:
    按章 = collections.Counter(re.sub(r"\s.*", "", str(x.get("定位", ""))) for x in 章 if x.get("来源") == "章评")
    行(f"P7 章评台账：{len(章)} 条，带严重度 {sum(1 for x in 章 if x.get('严重度'))}，单章最多 {max(按章.values()) if 按章 else 0} 条（预算 ≤5/章/轮），消解率 "
       f"{(sum(1 for x in 章 if x.get('状态') == '已消解') / len(章) * 100) if 章 else 0:.0f}%")
审 = 台账条("审稿台账")
if 审 is not None:
    级 = collections.Counter(x.get("级别") for x in 审)
    行(f"P5 审稿台账级别：{dict(级)}（版式占 {级.get('版式', 0)}/{len(审)}）；换版类意见 "
       f"{sum(1 for x in 审 if re.search(r'旧值|仍标|成套|错版|不一致|未同步', str(x.get('问题', ''))))} 条（预算 ≤2）")
美 = 台账条("美化台账")
if 美 is not None:
    行(f"P6 美化台账目标：{dict(collections.Counter(x.get('目标') for x in 美))}")

节("S5a 摘要 / S5b 美化 / G5 / S6")
for l in 抓(r"摘要定稿|美化轮|美观|门\[G5\]|终审|出版|复盘|终稿收割")[:30]: 行(l[:170])
台 = 父 / "台账"
for 名 in ["章评台账", "审稿台账", "美化台账"]:
    p = 台 / f"{名}.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8")); 条 = d if isinstance(d, list) else d.get("条目", [])
        节(f"{名}：{len(条)} 条")
        行("状态", dict(collections.Counter(x.get("状态") for x in 条)))
        行("级别", dict(collections.Counter(x.get("级别") for x in 条)), "目标", dict(collections.Counter(x.get("目标") for x in 条)))
审 = 父 / "蜂巢镜像/审稿"
if 审.exists():
    节("审稿意见/评委模拟（各轮）")
    for f in sorted(审.glob("审稿意见_轮*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8")); 行(f"{f.name}: 总分={d.get('总分')} 印象={d.get('印象分')} 最高优先级={len(d.get('最高优先级修改', []) or [])}")
        except Exception as e: 行(f.name, "读失败", e)
    for f in sorted(审.glob("评委模拟_轮*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8")); 行(f"{f.name}: 第一印象={d.get('第一印象分')} 复述={d.get('能否复述四要素')} 相对判断={d.get('相对判断')}")
        except Exception as e: 行(f.name, "读失败", e)
    for f in sorted(审.glob("复盘报告*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8")); 行(f"{f.name}: 瓶颈{len(d.get('瓶颈环节', []))} 规则库建议{len(d.get('规则库修改建议', []))} 总评={str(d.get('总评', ''))[:120]}")
        except Exception as e: 行(f.name, "读失败", e)
论文 = 产出 / "论文/论文.pdf"
if 论文.exists():
    节("成品")
    行(f"{论文} {论文.stat().st_size//1024} KB")
