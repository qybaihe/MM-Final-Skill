#!/usr/bin/env python3
"""门检：G0-G5 机械项收拢（容器内运行）。
用法: python3 bin/门检.py G0 | G2:1 | G3 | G4 | G5 | 叙事
输出: 审稿/门检_<门名>.json  {"通过": bool, "明细": [...], "统计": {...}}
纪律：只做**机械可判**的检查（数得出来、比得出来的），主观质量交给角色腿。
"""
import glob
import json
import os
import pathlib
import re
import sys

os.chdir(pathlib.Path(__file__).resolve().parent.parent)  # bin/ → 工作根（与部署位置无关）
门名 = (sys.argv[1] if len(sys.argv) > 1 else "G0").strip()
明细, 统计 = [], {}


def 读json(p, 默认=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return 默认


def 读词表(p):
    try:
        return [w.strip() for w in open(p, encoding="utf-8") if w.strip() and not w.lstrip().startswith("#")]
    except Exception:
        return []


def 正文tex():
    """论文 tex：原文（含注释）与去注释正文。"""
    texs = {}
    for p in glob.glob("论文/*.tex"):
        try:
            texs[p] = open(p, encoding="utf-8").read()
        except Exception:
            pass
    return texs, {p: re.sub(r"%.*", "", t) for p, t in texs.items()}


# ============================================================ G0 契约门
def G0():
    契约 = 读json("交接/题面契约.json")
    if not isinstance(契约, dict):
        明细.append("交接/题面契约.json 缺失或非法 JSON")
        return
    for k in ("赛题", "问题", "硬约束清单", "歧义裁定", "附件清单"):
        if k not in 契约:
            明细.append(f"契约缺顶层键：{k}")
    问题们 = 契约.get("问题") or []
    if not 问题们:
        明细.append("契约 问题 为空")
    需求总数 = 0
    for q in 问题们:
        编号 = q.get("编号")
        for k in ("原文摘录", "解读", "需求条目"):
            if not q.get(k):
                明细.append(f"问{编号} 缺 {k}")
        条目 = q.get("需求条目") or []
        需求总数 += len(条目)
        号们 = []
        for it in 条目:
            号 = str(it.get("需求号", ""))
            if not it.get("内容"):
                明细.append(f"问{编号} 需求 {号} 内容为空")
            if not it.get("评分点推测"):
                明细.append(f"问{编号} 需求 {号} 缺评分点推测")
            m = re.match(rf"^{编号}-(\d+)$", 号)
            if not m:
                明细.append(f"问{编号} 需求号格式非法：{号}（应为 {编号}-序号）")
            else:
                号们.append(int(m.group(1)))
        if 号们 and sorted(号们) != list(range(1, len(号们) + 1)):
            明细.append(f"问{编号} 需求号不连续：{sorted(号们)}")
    统计["需求条目总数"] = 需求总数
    # 歧义裁定每条必须有裁定
    for i, a in enumerate(契约.get("歧义裁定") or [], 1):
        if not str(a.get("裁定", "")).strip():
            明细.append(f"歧义裁定第{i}条缺'裁定'")
        if not str(a.get("理由", "")).strip():
            明细.append(f"歧义裁定第{i}条缺'理由'")
    # 附件清单覆盖 数据/ 全部文件
    实际 = {os.path.basename(p) for p in glob.glob("数据/*") if os.path.isfile(p)}
    声明 = {str(a.get("文件", "")).strip() for a in (契约.get("附件清单") or [])}
    漏 = 实际 - 声明
    if 漏:
        明细.append(f"附件清单未覆盖：{sorted(漏)}")
    统计["数据文件数"] = len(实际)
    统计["附件清单数"] = len(声明)
    # 需求追踪矩阵条目数应等于需求条目数
    矩阵 = 读json("交接/需求追踪矩阵.json")
    if isinstance(矩阵, list):
        统计["矩阵条目数"] = len(矩阵)
        if 需求总数 and len(矩阵) != 需求总数:
            明细.append(f"需求追踪矩阵条目数({len(矩阵)}) ≠ 契约需求条目数({需求总数})")


# ============================================================ G2 正确性门（每问）
def G2(编号):
    qdir = f"求解/问题{编号}"
    统计["问题"] = 编号
    # 1) 结果目录非空
    结果们 = [p for p in glob.glob(f"{qdir}/结果/*") if os.path.isfile(p)]
    统计["结果文件数"] = len(结果们)
    if not 结果们:
        明细.append(f"{qdir}/结果/ 为空")
    # 2) 解读 PASS（找该问最后一条解读 done 标记）
    dones = sorted(glob.glob(f"日志/解读_问题{编号}*.done"), key=os.path.getmtime)
    if not dones:
        明细.append(f"未找到问{编号}的解读 done 标记")
    else:
        首行 = ""
        try:
            首行 = open(dones[-1], encoding="utf-8", errors="ignore").readline().strip()
        except Exception:
            pass
        统计["解读首行"] = 首行[:60]
        统计["解读标记"] = os.path.basename(dones[-1])
        if not 首行.upper().startswith("PASS"):
            明细.append(f"问{编号} 解读未 PASS：{首行[:80]}")
    # 3) 红队结论=对齐（或仲裁裁定在案）
    红 = 读json(f"交接/红队_问题{编号}.json")
    仲 = 读json(f"交接/仲裁_问题{编号}.json")
    if 红 is None:
        明细.append(f"问{编号} 红队报告缺失")
    else:
        结论 = str(红.get("结论", "")).strip()
        统计["红队结论"] = 结论
        if 结论.startswith("未复算"):
            # 红队因预算/配置被跳过，是资源约束而非质量缺陷：软路径放行但留痕。
            # 此前驱动侧口径是"红队缺失不阻塞"、门检侧却一票否决，两边打架
            # 会让 G2 永不可过（实测问3 因此白烧一轮升格后降级放行）。
            # 真失败（红队跑了但无报告）走上面的 红 is None 分支，仍然阻塞。
            统计["红队未复算"] = str(红.get("跳过原因", ""))[:60]
        elif not 结论.startswith("对齐"):
            if isinstance(仲, dict) and str(仲.get("总裁定", "")).strip():
                统计["仲裁总裁定"] = str(仲.get("总裁定"))[:80]
                # 仲裁台账是**有状态的待办表**，不是一次性快照：
                # 判"建模需改"的条目，返工后必须由返工腿把该条 消解状态 置为
                # 已消解/已修正（并写 消解证据），门检才认为该项出清。
                # 否则一条永久留在文件里的 应改方=建模 会让 G2 永不可过（实测三问全中）。
                未决 = []
                已解释项 = []
                for x in (仲.get("逐项") or []):
                    if not str(x.get("应改方", "")).startswith("建模"):
                        continue
                    消 = str(x.get("消解状态", "") or x.get("消解", "")).strip()
                    证 = str(x.get("消解证据", "") or x.get("证据", "")).strip()
                    真改 = 消.startswith(("已消解", "已修正", "已解决", "已落实"))
                    解释 = 消.startswith(("已解释", "裁定不成立", "复核不成立"))
                    # 证据是硬门槛：光声明"已消解"不给证据，不算出清（防糊弄）
                    if (真改 or 解释) and 证:
                        if 解释:
                            已解释项.append(str(x.get("指标", ""))[:40])
                        continue
                    未决.append({"指标": str(x.get("指标", ""))[:40],
                                 "消解状态": 消 or "（空）",
                                 "有证据": bool(证)})
                统计["仲裁建模项"] = len([x for x in (仲.get("逐项") or [])
                                         if str(x.get("应改方", "")).startswith("建模")])
                统计["仲裁未消解"] = len(未决)
                # "已解释"是软路径（复核后认为裁定不成立）：放行但记账，便于人工复核
                if 已解释项:
                    统计["仲裁已解释项"] = 已解释项
                if 未决:
                    缺证 = [x["指标"] for x in 未决 if x["消解状态"] != "（空）" and not x["有证据"]]
                    补 = f"（其中 {len(缺证)} 项声明已处理但缺 消解证据）" if 缺证 else ""
                    明细.append(f"问{编号} 仲裁裁定建模需修正但未消解：{len(未决)}项{补}")
            else:
                明细.append(f"问{编号} 红队结论={结论 or '空'} 且无仲裁裁定在案")
    # 4) 假设台账检验结果全非空且引用的结果键存在
    台账 = 读json(f"交接/假设台账_问题{编号}.json")
    if not isinstance(台账, list) or not 台账:
        明细.append(f"问{编号} 假设台账缺失或为空")
    else:
        统计["台账条数"] = len(台账)
        池 = set()
        for p in glob.glob(f"{qdir}/结果/*.json"):
            def 收键(o):
                if isinstance(o, dict):
                    for k, v in o.items():
                        池.add(str(k))
                        收键(v)
                elif isinstance(o, list):
                    for v in o:
                        收键(v)
            收键(读json(p, {}))
        for it in 台账:
            号 = it.get("假设号", "?")
            for k in ("假设", "依据", "灵敏度义务", "检验结果"):
                if not str(it.get(k, "")).strip():
                    明细.append(f"问{编号} 假设{号} 的 {k} 为空")
            检 = str(it.get("检验结果", ""))
            if 检.strip() and ("待检验" in 检 or "未检验" in 检):
                明细.append(f"问{编号} 假设{号} 检验结果仍为待检验")
            # 引用的结果键（括号内 结果:键 形式）应能在结果 JSON 里找到
            引 = re.findall(r"[（(][^）)]*?[:：]\s*([^）)，,]+)", 检)
            for 键 in 引:
                键 = 键.strip()
                if 键 and 池 and not any(键 in k or k in 键 for k in 池):
                    明细.append(f"问{编号} 假设{号} 引用的结果键不存在：{键}")

    # 4b) 实验记录分流（M5-1 B2，病根 R23）：撰稿师只许引用 类别=科学尝试 的记录，
    #     所以进论文的过程感素材在这里就得是干净的：本问 ≥1 条科学尝试、科学尝试不含流程词、
    #     没有未分类条目（未分类 = 建模师没按 schema 写，撰稿师就分不清哪条能引）。
    记录 = 读json("交接/实验记录.json", [])
    流程词 = 读词表("运行时/流程词.txt")
    if not 流程词:
        明细.append("运行时/流程词.txt 缺失或为空——记录分流判据无法执行，不放行")
    本问数, 科学数, 未分数, 脏 = 0, 0, 0, []
    try:
        for it in (记录 or []):
            if not isinstance(it, dict) or re.sub(r"\D", "", str(it.get("问题", ""))) != str(编号):
                continue
            本问数 += 1
            类 = str(it.get("类别", "")).strip()
            if 类 == "科学尝试":
                科学数 += 1
                文 = " ".join([str(it.get("尝试", "")), str(it.get("现象", "")), str(it.get("决定", "")), str(it.get("依据", ""))])
                命中 = [w for w in 流程词 if w in 文]
                if 命中:
                    脏.append(命中[:3])
            elif 类 not in ("流程事件", "绘图尝试", "图评尝试", "撰写尝试", "工程修复"):
                # R41：S3/S4/S5 的绘图/图评/撰稿/审稿腿也往实验记录里写（自报类别 绘图尝试 等），S5 回炉重开 G2 门时
                # 这些条目被当成"建模师没按 schema 写"→ 假 FAIL 派建模返工。只有真没类别的才算未分类。
                未分数 += 1
    except TypeError:
        明细.append("交接/实验记录.json 顶层须为数组")
    统计["实验记录_本问"] = 本问数
    统计["实验记录_科学尝试"] = 科学数
    统计["实验记录_未分类"] = 未分数
    if not 本问数:
        明细.append(f"问{编号} 在 交接/实验记录.json 里没有任何记录（过程感素材为空）")
    elif not 科学数:
        明细.append(f"问{编号} 实验记录没有一条 类别=科学尝试（{本问数} 条全是流程事件或未分类）——论文将无过程感素材可引")
    if 未分数:
        明细.append(f"问{编号} 实验记录有 {未分数} 条未分类（类别 须为 科学尝试|流程事件；绘图尝试|图评尝试|撰写尝试|工程修复 视同流程事件）")
    if 脏:
        明细.append(f"问{编号} 有 {len(脏)} 条科学尝试含流程词（应改类别为流程事件或改写成科学表述）：{脏[:3]}")

    # 5) 必须真的产出了题面答案——数模竞赛的核心交付物
    #    实测教训（20260827 轮）：三问全部以"不发布/不联合/证据不足"收尾，
    #    交接/结果声明_问题1.json 与 _问题2.json 根本不存在、_问题3.json 的核心指标为 {}，
    #    而 G2 依然全部放行——因为此前全部判据都是"过程合规"，没有一条度量"有没有做出来"。
    #    于是"宣布不发布"成了最安全的通关策略：没有数字就没有错误的数字，
    #    五项正确性协议自动全过、红队复算也一致，门顺利通过，论文却交了白卷。
    #    结果声明_问题N.json 是全流程唯一承载题面答案的契约文件，必须硬检。
    声明 = 读json(f"交接/结果声明_问题{编号}.json")
    if not isinstance(声明, dict):
        明细.append(f"问{编号} 结果声明缺失——未产出题面答案")
    else:
        指标 = 声明.get("核心指标")
        数值项 = {k: v for k, v in 指标.items()
                  if isinstance(v, (int, float)) and not isinstance(v, bool)} if isinstance(指标, dict) else {}
        统计["核心指标数"] = len(数值项)
        if not 数值项:
            明细.append(f"问{编号} 核心指标为空——未产出题面要求的数值答案"
                        f"（不得以\u201c不发布/不联合/证据不足\u201d规避作答，"
                        f"应给出最佳估计并标注不确定性区间与局限）")


# ============================================================ G3 图证门
def G3():
    pngs = glob.glob("求解/**/图片/*.png", recursive=True)
    统计["图数"] = len(pngs)
    if len(pngs) < 16:
        明细.append(f"图数不足：{len(pngs)}（要求 16-22）")
    if len(pngs) > 22:
        明细.append(f"图数超额：{len(pngs)}（要求 16-22）")
    kind_pat = {"柱状": r"\.bar\(|\.barh\(", "折线": r"\.plot\(", "散点": r"\.scatter\(",
                "热力": r"\.imshow\(|pcolormesh", "箱线": r"\.boxplot\(", "饼": r"\.pie\(",
                "面积": r"\.fill_between\(|stackplot", "直方": r"\.hist\(",
                "示意/手绘": r"FancyArrowPatch|FancyBboxPatch|add_patch"}
    from collections import Counter
    cnt = Counter()
    for p in glob.glob("求解/**/绘图_*.py", recursive=True):
        try:
            src = open(p, encoding="utf-8").read()
        except Exception:
            continue
        for k, pat in kind_pat.items():
            if re.search(pat, src):
                cnt[k] += 1
    统计["图型分布"] = dict(cnt)
    总 = sum(cnt.values()) or 1
    统计["柱折占比"] = round((cnt["柱状"] + cnt["折线"]) / 总, 2)
    统计["示意图数"] = cnt["示意/手绘"]
    if cnt["示意/手绘"] < 3:
        明细.append(f"机理/示意手绘图不足 3 张：{cnt['示意/手绘']}")
    if (cnt["柱状"] + cnt["折线"]) / 总 > 0.5:
        明细.append(f"柱状+折线合计占比 {统计['柱折占比']} 超过一半")
    for k, v in cnt.items():
        if k != "示意/手绘" and v > 3:
            明细.append(f"同型图 {k} 超 3 张：{v}")
    # 图注素材齐备
    素材 = glob.glob("交接/图注素材*.json")
    统计["图注素材文件数"] = len(素材)
    if not 素材:
        明细.append("交接/图注素材*.json 缺失")


# ============================================================ G4 成稿门
def G4():
    texs, body = 正文tex()
    if not texs:
        明细.append("论文/*.tex 缺失")
        return
    # 编译错误 E=0
    try:
        log = open("论文/论文.log", encoding="utf-8", errors="ignore").read()
        E = len(re.findall(r"(?m)^!", log))
        统计["编译错误E"] = E
        if E:
            明细.append(f"编译错误 E={E}")
        m = re.search(r"\((\d+) pages", log)
        if m:
            统计["页数"] = int(m.group(1))
    except Exception:
        明细.append("论文/论文.log 缺失（未编译？）")
    # 摘要恰 1 页
    try:
        aux = open("论文/论文.aux", encoding="utf-8", errors="ignore").read()
        m = re.search(r"abstract:end\}\{(\d+)\}", aux) or re.search(r"abstract:end.*?\{(\d+)\}", aux)
        if m:
            统计["摘要页"] = int(m.group(1))
            if int(m.group(1)) != 1:
                明细.append(f"摘要不是恰好 1 页：aux 记为第 {m.group(1)} 页")
        else:
            明细.append("论文.aux 未找到 abstract:end 标签（摘要页数无法核验）")
    except Exception:
        明细.append("论文/论文.aux 缺失")
    审计 = 读json("审稿/审计报告.json")
    if not isinstance(审计, dict):
        明细.append("审稿/审计报告.json 缺失（先跑 bin/审计.py）")
        return
    禁 = (审计.get("禁用词") or {}).get("数量", 0)
    统计["禁用词"] = 禁
    if 禁:
        明细.append(f"禁用词命中 {禁} 处")
    # 有效数字：5 位以上小数说明把 json 浮点原样贴进了正文
    # 实测（20260827 轮）该轮论文命中 71 处，最长 0.9986697662（10 位小数）。
    过精 = (审计.get("有效数字") or {}).get("过精数量", 0)
    统计["有效数字过精"] = 过精
    if 过精:
        样例 = [x.get("数字") for x in ((审计.get("有效数字") or {}).get("明细") or [])[:5]]
        明细.append(f"有效数字过精 {过精} 处（小数位应≤4，按测量精度收敛）：{样例}")
    # 内部术语密度：**阈值判据，不是零容忍**。
    # 实测（20260831 轮）四位审稿员连续四轮点名同一件事——「摘要和结论读起来像
    # 质量审计报告」「术语密集，却没有先用一句朴素语言回答题目」，改了四轮仍在。
    # 但全禁不可取：「证据门」就是本文真实的方法名，第 6 章标题即用它；
    # 全禁只会逼撰稿师绕着写，反而别扭。所以按章限密度，首读章(摘要/结论/引言)
    # 更严——评委最先读的正是它们，而实测恰恰是它们最术语（17.91 / 15.50 次每千字，
    # 而另有七章为 0，说明留有余地）。
    术语 = 审计.get("内部术语密度") or {}
    统计["内部术语_全文密度"] = 术语.get("全文密度")
    超线 = 术语.get("超线明细") or []
    统计["内部术语超线章数"] = len(超线)
    if 超线:
        说 = "；".join(f"{os.path.basename(x.get('文件',''))} {x.get('密度')}>{x.get('阈值')}"
                       for x in 超线[:4])
        明细.append(f"内部术语密度超线 {len(超线)} 章（{术语.get('口径','')}）：{说}"
                    f"——先用朴素语言回答题目，术语首次出现时补一句白话定义")
    存疑 = (审计.get("溯源核验") or {}).get("存疑数", 0)
    统计["溯源存疑"] = 存疑
    if 存疑:
        # 允许逐条带"已解释"
        未解释 = [x for x in ((审计.get("溯源核验") or {}).get("存疑明细") or [])
                  if "已解释" not in json.dumps(x, ensure_ascii=False)]
        if 未解释:
            明细.append(f"溯源核验存疑 {存疑} 条（未解释 {len(未解释)} 条）")
    句 = (审计.get("图表引用句式") or {})
    统计["如图如表占比"] = 句.get("占比")
    if 句.get("违规"):
        明细.append(f"图表引用句式：{句['违规']}")
    # 段首重复
    重 = 审计.get("段首重复") or {}
    if 重:
        统计["段首重复"] = 重
        明细.append(f"段首开场词重复 ≥3 次：{list(重)[:5]}")
    # 溯源标注覆盖（段级口径：有源段 /(有源段+无源含统计数字段)）
    数 = 审计.get("数字溯源") or {}
    统计["溯源覆盖率"] = 数.get("覆盖率")
    if 数.get("覆盖率") is not None and 数["覆盖率"] < 0.6:
        明细.append(f"% src 溯源标注覆盖率仅 {数['覆盖率']}（<0.6）")
    # ---------- 表达判据（M5-1 表达优化：病根 R24/R25/R26/R27） ----------
    # 阈值只认 运行时/表达阈值.json（先验或范文分布，"阈值出处"说明来源）；阈值文件缺失 = 判据无法执行，不放行。
    画 = 审计.get("表达画像") or {}
    if not 画:
        明细.append("审计报告缺 表达画像 节（bin/审计.py 版本过旧或未跑完）——表达判据无法执行，不放行")
    if 画:
        统计["表达_阈值出处"] = str(画.get("阈值出处"))[:40]
        if not 画.get("阈值出处") or 画.get("阈值出处") == "缺失":
            明细.append("表达阈值文件缺失（运行时/表达阈值.json）——密度判据无法执行，不放行")
        超 = 画.get("超线明细") or []
        统计["表达_超线数"] = len(超)
        if 超:
            说 = "；".join(f"{os.path.basename(str(x.get('文件', '')))} {x.get('指标')} {x.get('值')}>{x.get('阈值')}" for x in 超[:5])
            明细.append(f"表达密度超线 {len(超)} 处（{画.get('口径', '')}）：{说}"
                        f"——数字退回图表与答案框、置信声明全文只写一处、长句拆短")
        摘 = 画.get("摘要画像") or {}
        if 摘.get("超线"):
            明细.append(f"摘要画像超线：{摘.get('超线')}——摘要是写给 30 秒评委的：数字只留每问答案、置信只说一次、句子 ≤40 字")
        段首 = 画.get("段首数字段") or 0
        统计["表达_段首数字段"] = 段首
        if 段首:
            例 = [(os.path.basename(str(x.get('文件', ''))), x.get('行')) for x in (画.get("段首数字明细") or [])[:4]]
            明细.append(f"{段首} 段以数字开头（先讲清楚再钉数字）：{例}")
        超预算 = 画.get("超预算段") or 0
        统计["表达_超预算段"] = 超预算
        if 超预算:
            线 = (画.get("段内数字线") or {}).get("硬线")
            例 = [(os.path.basename(str(x.get('文件', ''))), x.get('行'), x.get('数字个数')) for x in (画.get("超预算段明细") or [])[:4]]
            明细.append(f"{超预算} 段进句数字超过硬线 {线} 个：{例}——多余的数字进表或删")
    缩 = 审计.get("自造缩写") or {}
    if 缩:
        统计["自造缩写"] = 缩.get("数量")
        if 缩.get("数量"):
            明细.append(f"自造缩写 {缩.get('数量')} 处：{缩.get('去重')}——改成描述性中文短语（≤12 字），或确属通用缩写则由维护者加入白名单")
        if 缩.get("标题违规数"):
            例 = [(str(x.get('标题', ''))[:24], x.get('词')) for x in (缩.get("标题违规") or [])[:4]]
            明细.append(f"标题含自造缩写/内部术语 {缩.get('标题违规数')} 处：{例}")
    图题 = 审计.get("图题") or {}
    if 图题.get("对冲违规数"):
        例 = [(str(x.get('图题', ''))[:24], x.get('词')) for x in (图题.get("对冲违规") or [])[:4]]
        明细.append(f"图题含对冲词 {图题.get('对冲违规数')} 处：{例}——图题 = 对象 + 看到什么，限制条款写进正文")
    重 = 审计.get("问题重述") or {}
    if 重:
        缺 = [k for k in ("有章", "含给定", "含要求", "有总体分析", "有思路图") if not 重.get(k)]
        if 缺:
            明细.append(f"问题重述与总体分析缺要素：{缺}（1.1 须写清题目给定什么、要求什么；1.2 总体分析须配思路图）")


# ============================================================ G5 出版门

def _外链清单(t):
    """列出 tex 里的 \\lstinputlisting[选项]{文件}：选项可含 {}（caption={..}、linerange={..}），按花括号深度找配对的 ]。"""
    出 = []
    i = 0
    while True:
        i = t.find("\\lstinputlisting", i)
        if i < 0:
            break
        j = i + len("\\lstinputlisting")
        选项 = ""
        if j < len(t) and t[j] == "[":
            深 = 0
            k = j + 1
            while k < len(t):
                c = t[k]
                if c == "{":
                    深 += 1
                elif c == "}":
                    深 -= 1
                elif c == "]" and 深 == 0:
                    break
                k += 1
            选项 = t[j + 1:k]
            j = k + 1
        m = re.match(r"\s*\{([^}]*)\}", t[j:])
        if m:
            出.append((选项, m.group(1).strip()))
        i = j
    return 出


def _行范围(选项, 总行数):
    """firstline/lastline/linerange 截取范围（1 起，闭区间）；没写就是全文件。"""
    mr = re.search(r"linerange=\{([^}]*)\}", 选项)
    if mr:
        出 = []
        for seg in mr.group(1).split(","):
            a, _, b = seg.strip().partition("-")
            出.append((int(a or 1), int(b or 总行数)))
        return 出
    a = re.search(r"firstline=(\d+)", 选项)
    b = re.search(r"lastline=(\d+)", 选项)
    return [(int(a.group(1)) if a else 1, int(b.group(1)) if b else 总行数)]

def G5():
    texs, body = 正文tex()
    if not texs:
        明细.append("论文/*.tex 缺失")
        return
    # 需求矩阵全销号
    矩阵 = 读json("交接/需求追踪矩阵.json")
    if not isinstance(矩阵, list) or not 矩阵:
        明细.append("需求追踪矩阵缺失或为空")
    else:
        未 = [x for x in 矩阵 if str(x.get("状态", "")) != "已落位"]
        统计["矩阵条目"] = len(矩阵)
        统计["未销号"] = len(未)
        if 未:
            明细.append(f"需求矩阵未销号 {len(未)} 条：{[x.get('需求号') for x in 未][:8]}")
        缺位 = [x.get("需求号") for x in 矩阵
                if str(x.get("状态")) == "已落位" and not str((x.get("落位") or {}).get("章节", "")).strip()]
        if 缺位:
            明细.append(f"已落位但未填章节：{缺位[:8]}")
    # 硬伤清零 —— 只认驱动持有的审稿台账视图（病根台账 R5/R12）。
    # L0 教训（20260831 轮）：旧判据取 glob 最大号的 硬伤_轮*.json，返工腿自己写了一份 0 条的 硬伤_轮5.json 就过了门。
    # 视图由驱动在每次 G5 检查前从本地真相重新上传，腿在容器里写什么都不算数。
    视图 = 读json("审稿/审稿台账_视图.json")
    if not isinstance(视图, dict):
        明细.append("审稿台账视图缺失（驱动未上传）——硬伤状态不可知，不放行")
    else:
        阻塞明细 = [str(m) for m in (视图.get("阻塞明细") or [])]
        阻 = [m for m in 阻塞明细 if "搁置" not in m]
        搁 = [m for m in 阻塞明细 if "搁置" in m]
        统计["台账摘要"] = 视图.get("摘要")
        统计["台账收敛"] = bool(视图.get("收敛"))
        统计["搁置阻塞数"] = len(搁)
        if not 视图.get("收敛") or 阻:
            明细.append(f"审稿台账未收敛：{len(阻)} 条阻塞级意见未消解（{[m[:40] for m in 阻[:3]]}）")
        if 搁:
            明细.append(f"审稿台账有 {len(搁)} 条阻塞级意见被搁置（两次修订未消解，出版前必须处理）：{[m[:40] for m in 搁[:3]]}")
    # 版式硬规范
    try:
        log = open("论文/论文.log", encoding="utf-8", errors="ignore").read()
        m = re.search(r"\((\d+) pages", log)
        总页 = int(m.group(1)) if m else None
        统计["总页数"] = 总页
        if 总页:
            正文页 = 总页 - 1        # 摘要独立 1 页
            # 附录页不计入正文 20 页限制：用 aux 里附录章起始页粗测
            try:
                aux = open("论文/论文.aux", encoding="utf-8", errors="ignore").read()
                附 = re.findall(r"\{section\}\{[^}]*附录[^}]*\}\{(\d+)\}", aux) or \
                     re.findall(r"附录[^}]*\}\{(\d+)\}\{", aux)
                if 附:
                    正文页 = int(附[0]) - 1
                    统计["附录起始页"] = int(附[0])
            except Exception:
                pass
            统计["正文页数估计"] = 正文页
            if 正文页 > 20:
                明细.append(f"正文页数 {正文页} 超过 20 页")
    except Exception:
        明细.append("论文/论文.log 缺失")
    # 无目录
    for p, t in body.items():
        if "\\tableofcontents" in t:
            明细.append(f"{p} 含 \\tableofcontents（国赛要求不放目录）")
    # 附录含代码且非空（R48）：附录范围 = 文件名带 附录/源码/补充材料 的章（8.附录.tex 只是导语，源码在 8.N.问题N源码.tex）；
    # 代码量 = 内联 lstlisting + \lstinputlisting 外链源码（按 firstline/lastline/linerange 截取，文件相对该 tex 所在目录）。
    # 旧规则只数 8.附录.tex 的内联代码：排版执行按美化师意见删掉重复内联刊载后只剩外链 → G5 报「107 字符」误判（20260910 15:08）。
    附文 = {p: t for p, t in texs.items() if re.search(r"附录|源码|补充材料", os.path.basename(p))}
    if not 附文:
        明细.append("未找到附录 tex")
    else:
        码量 = 0
        外链 = 0
        for p, t in 附文.items():
            for m in re.finditer(r"\\begin\{lstlisting\}(.*?)\\end\{lstlisting\}", t, re.S):
                码量 += len(m.group(1).strip())
            for 选项, 文件 in _外链清单(t):
                fp = os.path.join(os.path.dirname(p), 文件)
                try:
                    行们 = open(fp, encoding="utf-8", errors="replace").read().splitlines()
                except OSError:
                    明细.append(f"附录 \\lstinputlisting 外链文件不存在：{文件}（{p}）")
                    continue
                for a, b in _行范围(选项, len(行们)):
                    码量 += sum(len(x.strip()) for x in 行们[max(0, a - 1):b])
                外链 += 1
        统计["附录代码字符数"] = 码量
        统计["附录外链源码数"] = 外链
        if 码量 < 500:
            明细.append(f"附录 lstlisting 代码过少或缺失（{码量} 字符，外链 {外链} 处）")
    # \cite 键 ⊆ 文献卡片库
    库 = ""
    try:
        库 = open("运行时/文献卡片库.md", encoding="utf-8").read()
    except Exception:
        明细.append("运行时/文献卡片库.md 缺失，无法核验引用")
    if 库:
        用到 = set()
        for t in body.values():
            for m in re.finditer(r"\\cite\{([^}]+)\}", t):
                for k in m.group(1).split(","):
                    if k.strip():
                        用到.add(k.strip())
        统计["cite键数"] = len(用到)
        野 = sorted([k for k in 用到 if k not in 库])
        if 野:
            明细.append(f"引用键不在文献卡片库：{野[:8]}")
    # 图配额复用 G3 判据
    pngs = glob.glob("求解/**/图片/*.png", recursive=True)
    统计["图数"] = len(pngs)
    if not (16 <= len(pngs) <= 22):
        明细.append(f"图数 {len(pngs)} 不在 16-22 区间")
    # 全部图被引用
    引用图 = set()
    for t in body.values():
        for m in re.finditer(r"includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", t):
            引用图.add(os.path.basename(m.group(1)))
    未引 = [os.path.basename(p) for p in pngs if os.path.basename(p) not in 引用图]
    统计["未被引用图数"] = len(未引)
    if len(未引) > 2:
        明细.append(f"有 {len(未引)} 张图未被正文引用：{未引[:6]}")


# ============================================================ 叙事门（B1 叙事底稿）
def 叙事():
    """叙事底稿是写给"不懂本题的评委"的白话：每问五段（题目问什么/数据长什么样/怎么想/得到什么/信到什么程度），
    零公式、零命令、零文件名、零自造缩写、零流程词、零内部术语。它是 S4 全部写作腿的第一输入（病根 R22）。"""
    try:
        t = open("交接/叙事底稿.md", encoding="utf-8").read()
    except Exception:
        明细.append("交接/叙事底稿.md 缺失")
        return
    契约 = 读json("交接/题面契约.json") or {}
    编号们 = []
    for q in (契约.get("问题") or []) if isinstance(契约, dict) else []:
        try:
            编号们.append(int(str(q.get("编号")).strip()))
        except Exception:
            pass
    中文数 = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九"}
    反查 = {v: k for k, v in 中文数.items()}
    if not 编号们:
        # 契约缺失/无编号时退回底稿自报的「## 问题N」——不能让"没有问题清单"变成"没有检查"（病根 R18）
        for m in re.finditer(r"(?m)^#+\s*问题\s*(\d+|[一二三四五六七八九])\b", t):
            g = m.group(1)
            编号们.append(int(g) if g.isdigit() else 反查[g])
        编号们 = sorted(set(编号们))
        if not 编号们:
            明细.append("叙事底稿没有任何「## 问题N」节，且 交接/题面契约.json 无问题清单可对照")
    五段 = ("题目问什么", "数据长什么样", "怎么想", "得到什么", "信到什么程度")
    统计["问题数"] = len(编号们)
    for n in 编号们:
        m = re.search(rf"(?m)^#+\s*问题\s*(?:{n}|{中文数.get(n, n)})\b.*$", t)
        if not m:
            明细.append(f"叙事底稿缺「问题{n}」一节（标题须写成 ## 问题{n} …）")
            continue
        后 = t[m.end():]
        下一 = re.search(rf"(?m)^#+\s*问题\s*(?:\d+|[一二三四五六七八九])\b", 后)
        节 = 后[:下一.start()] if 下一 else 后
        汉 = len(re.findall(r"[\u4e00-\u9fff]", 节))
        统计[f"问{n}汉字"] = 汉
        if 汉 < 150:
            明细.append(f"问题{n} 叙事只有 {汉} 汉字（每问至少 150 字的白话）")
        缺 = [k for k in 五段 if k not in 节]
        if 缺:
            明细.append(f"问题{n} 缺小节标记：{缺}（五段各用「### {五段[0]}」这类标题分开）")
    if "$" in t or "\\" in t:
        明细.append("叙事底稿含公式或 LaTeX 命令（$ 或 \\）——底稿是白话，公式留给正文")
    文件名 = re.findall(r"[\w\-一-龥/]+\.(?:json|py|tex|csv|xlsx|md|png|sh)\b", t)
    if 文件名:
        明细.append(f"叙事底稿含文件名 {len(文件名)} 处：{文件名[:4]}")
    白 = set(读词表("运行时/缩写白名单.txt"))
    缩 = sorted({w for w in re.findall(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]*[A-Z][A-Za-z0-9]*[A-Z])[A-Za-z][A-Za-z0-9]*(?![A-Za-z0-9])", t) if w not in 白})
    if 缩:
        明细.append(f"叙事底稿含缩写 {缩[:6]}（白话不用缩写，写成中文全称）")
    流程 = [w for w in 读词表("运行时/流程词.txt") if w in t]
    if 流程:
        明细.append(f"叙事底稿含流程词 {流程[:6]}（评委不知道什么是任务单/回炉）")
    术语 = [w for w in 读词表("运行时/内部术语.txt") if w in t]
    if 术语:
        明细.append(f"叙事底稿含内部术语 {术语[:6]}")


# ============================================================ 记录清洗（驱动调用，不是门）
def 清洗实验记录(编号):
    """把本问含流程词的 科学尝试 机械降级为 流程事件（G2 判据给出的两种修法之一，确定性执行）。
    三跑问1实测（20260909）：11 条含流程词 → 建模返工一次剩 1 条 → 再返工仍可能剩 → 第三次就要升格蜂群，
    为几条记录措辞烧三条变体腿+裁决+红队复核（>1 小时）完全不成比例。降级后的条目不进论文过程感素材，
    但保留在记录里供复盘；被降级的条目带 `清洗` 字段留痕。返回降级条数。"""
    流程词 = 读词表("运行时/流程词.txt")
    路径 = "交接/实验记录.json"
    记录 = 读json(路径)
    if not isinstance(记录, list) or not 流程词:
        print("清洗实验记录：记录不是数组或流程词表缺失，未动"); return 0
    n = 0
    for it in 记录:
        if not isinstance(it, dict) or re.sub(r"\D", "", str(it.get("问题", ""))) != str(编号):
            continue
        if str(it.get("类别", "")).strip() != "科学尝试":
            continue
        文 = " ".join([str(it.get("尝试", "")), str(it.get("现象", "")), str(it.get("决定", "")), str(it.get("依据", ""))])
        命中 = [w for w in 流程词 if w in 文]
        if 命中:
            it["类别"] = "流程事件"
            it["清洗"] = f"驱动按 G2 判据降级：科学尝试含流程词 {命中[:3]}"
            n += 1
    json.dump(记录, open(路径, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"清洗实验记录：问{编号} 降级 {n} 条科学尝试→流程事件")
    return n


if 门名 == "清洗实验记录":
    清洗实验记录(sys.argv[2] if len(sys.argv) > 2 else "1")
    sys.exit(0)

# ============================================================ 调度
if 门名.startswith("G2"):
    编号 = 门名.split(":", 1)[1].replace("问", "").strip() if ":" in 门名 else "1"
    G2(编号)
elif 门名 == "G0":
    G0()
elif 门名 == "G3":
    G3()
elif 门名 == "G4":
    G4()
elif 门名 == "G5":
    G5()
elif 门名 == "叙事":
    叙事()
else:
    明细.append(f"未知门名：{门名}")

os.makedirs("审稿", exist_ok=True)
报告 = {"门": 门名, "通过": not 明细, "明细": 明细, "统计": 统计}
安全名 = 门名.replace(":", "_")
json.dump(报告, open(f"审稿/门检_{门名}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
if 安全名 != 门名:
    json.dump(报告, open(f"审稿/门检_{安全名}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(报告, ensure_ascii=False)[:1500])
