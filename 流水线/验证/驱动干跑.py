#!/usr/bin/env python3
"""驱动干跑：用假 LocalHive 在本地跑通 蜂群驾驶.py 的完整编排图（不花算力）。
用途：验收 M1 的状态机/断点续跑、依赖 DAG 分层与级联、门框架与升格分支；
M5-0 迁移后追加「腿进程死亡即时重派」干跑场景（kill -0 判死取代容器轮换判据）。
用法：
  python3 驱动干跑.py 全链        # 完整走一遍，打印节点顺序
  python3 驱动干跑.py 中断续跑    # 在 S2 问2 处模拟 kill，再 --resume，验证跳过
  python3 驱动干跑.py 级联        # 验证 级联重算(1) 令问2重跑
  python3 驱动干跑.py 门升格      # 令 G2 门恒 FAIL，验证返工2次→升格蜂群→降级放行
  python3 驱动干跑.py 韧性        # 场景A 腿死亡即时重派；场景B 预算护栏
  python3 驱动干跑.py G5图路      # R49：末轮留一条 目标=图 的阻塞级硬伤，验证 G5 返工先走绘图师图路再复核过门
  python3 驱动干跑.py G5算条      # R50：末轮留 图+算 各一条，验证 G5 图路照走、算条搁置不派撰稿师、三次检查后降级放行
  python3 驱动干跑.py G5页数      # R51/R52：G5 返工撰稿师把整份源码塞进附录源码章 → 复核前编译、页数守卫回退代码章、G5 仍过门
"""
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import types

模式 = sys.argv[1] if len(sys.argv) > 1 else "全链"
# 引擎 场景 = 全链 + --引擎=claude：验证引擎参数经 配置 → Hive(engine=) → 状态.json 的通路（腿本身是假的，两种引擎契约一致）
引擎场景 = 模式 == "引擎"
if 引擎场景:
    模式 = "全链"
    os.environ["DRYRUN_OPTS"] = (os.environ.get("DRYRUN_OPTS", "") + " --引擎=claude").strip()
流水线 = pathlib.Path(__file__).parent.parent
项目根 = 流水线.parent

# ---------------- 假 LocalHive ----------------
腿记录 = []
效力记录 = {}        # 腿名 → 驱动下发的推理档（effort=），验 --档位=标准 / --角色分档 的分档是否真的传到腿
任务文本 = {}      # 腿名 → 任务正文（回执/裁定 mock 从里面抠台账编号，让回路协议在干跑里真的走一遍）
脚本记录 = []
# 死亡演练：开启时，第一次波次轮询报 done=0 且第一条腿报 DEAD——驱动应即时回收重派
死亡演练 = {"开启": False, "已注入": False}
事件序 = []          # 腿名与脚本名按发生顺序统一登记，用来断言"变体脚本先于裁决""复核先于解读"这类先后关系
红队读数 = {"n": 0}   # 红队不齐 场景：第一次读 红队_问题1.json 给不齐，之后给对齐
坏门 = {"G2"} if 模式 == "门升格" else set()
假根 = {"p": None}      # 假 hive 的工作根：G5图路 场景里 门检_G5 mock 读驱动上传的台账视图，像真门检一样按「收敛」判
中断于 = {"中断续跑": "建模_问题2", "S5续跑": "审2A"}.get(模式)   # S5续跑：轮1 修订后落盘、轮2 首腿被 kill，验证 R43 轮级断点
_已中断 = {"flag": False}

假图 = [f"求解/问题{q}/图片/图{q}_{i}.png" for q in (1, 2, 3) for i in (1, 2, 3, 4, 5, 6)]
假页 = [f"论文/页/p{i:02d}.png" for i in range(1, 20)]


def _mock_json(rel):
    """按路径造出结构合法的交接文件内容。"""
    if rel.endswith("题面契约.json"):
        return {"赛题": "B", "标题": "干跑测试题",
                "问题": [{"编号": q, "原文摘录": "…", "解读": "…", "可交付物": ["模型"],
                          "需求条目": [{"需求号": f"{q}-{i}", "内容": f"需求{q}-{i}", "评分点推测": "…"} for i in (1, 2)]}
                         for q in (1, 2, 3)],
                "硬约束清单": [{"约束号": "C1", "内容": "…", "出处": "题面第1段"}],
                "歧义裁定": [{"条目": "…", "候选解释": ["A", "B"], "裁定": "A", "理由": "…", "影响范围": "问2"}],
                "附件清单": [{"文件": f"附件{i}.xlsx", "内容概述": "…", "关联问题": [1]} for i in (1, 2, 3, 4)]}
    if rel.endswith("题面.json"):
        return {"赛事": "干跑", "问题": [{"编号": q, "标题": f"问{q}", "要求": ["a", "b"]} for q in (1, 2, 3)]}
    if rel.endswith("数据档案.json"):
        return {"文件": [{"名": "附件1.xlsx", "行数": 7469}]}
    if rel.endswith("路线侦察.json"):
        return {"问题": [{"编号": q, "路线": [{"名称": f"路线{k}", "简述": "…", "原型实验设计": "小样"} for k in (1, 2, 3)]}
                        for q in (1, 2, 3)]}
    if "锦标赛_问题" in rel:
        return {"问题": 1, "路线": [{"名称": "路线1", "实测指标": {"MAPE": 0.3}, "用时秒": 90}],
                "优胜": "路线1", "裁决理由": "指标最优", "败者用途": "对比节素材"}
    if rel.endswith("计划.json"):
        return {"问题清单": [
                    {"编号": 1, "标题": "问1", "主方法": "M1", "备选方法": "B1", "方法理由(引用数据档案具体数字)": "7469点",
                     "本题定制改造": {"名称": "改造1", "为什么": "…"},
                     "验证方案": {"基线": "naive", "交叉印证": "…", "防泄漏": "…", "灵敏度": "…"},
                     "依赖问题": [], "锦标赛": {"优胜": "路线1"}, "蜂群变体": []},
                    {"编号": 2, "标题": "问2", "主方法": "M2", "备选方法": "B2", "方法理由(引用数据档案具体数字)": "…",
                     "本题定制改造": {"名称": "改造2", "为什么": "…"},
                     "验证方案": {"基线": "…", "交叉印证": "…", "防泄漏": "…", "灵敏度": "…"},
                     "依赖问题": [1], "锦标赛": {"优胜": "路线2"}, "蜂群变体": []},
                    {"编号": 3, "标题": "问3", "主方法": "M3", "备选方法": "B3", "方法理由(引用数据档案具体数字)": "…",
                     "本题定制改造": {"名称": "改造3", "为什么": "…"},
                     "验证方案": {"基线": "…", "交叉印证": "…", "防泄漏": "…", "灵敏度": "…"},
                     "依赖问题": [], "锦标赛": {"优胜": "路线1"}, "蜂群变体": []}],
                "论文结构": [{"文件名": "0.摘要.tex", "章节标题": "摘要", "内容要点": "四要素", "页数配额": 1},
                             {"文件名": "1.引言.tex", "章节标题": "引言", "内容要点": "…", "页数配额": 1.2},
                             {"文件名": "3.问题1求解.tex", "章节标题": "问1", "内容要点": "…", "页数配额": 4},
                             {"文件名": "4.问题2求解.tex", "章节标题": "问2", "内容要点": "…", "页数配额": 4},
                             {"文件名": "5.问题3求解.tex", "章节标题": "问3", "内容要点": "…", "页数配额": 5},
                             {"文件名": "99.附录.tex", "章节标题": "附录", "内容要点": "源码", "页数配额": "不限"}],
                "图表规划": [{"问题": f"问题{q}", "图名": f"图{q}_{i}", "图型": "折线", "独有信息": "…",
                              "结论一句话(评委只看此图应带走什么)": "…"} for q in (1, 2, 3) for i in range(1, 7)],
                "页数预算": {"摘要": 1, "正文总计": 20, "附录": "不限"},
                "叙事主线": "一句话故事线", "偏离点": ["偏离1", "偏离2", "偏离3"]}
    if "论点脊柱" in rel:
        return {"章": [{"文件名": f, "论点链": [{"发现": "数字1.23", "证据": ["图1_1"], "含义": "…", "衔接下段": "…"}],
                        "开场策略": "…", "禁止": "模板化开场"}
                       for f in ("1.引言.tex", "3.问题1求解.tex", "4.问题2求解.tex", "5.问题3求解.tex")]}
    if "红队_问题" in rel:
        if 模式 == "红队不齐" and "问题1" in rel:
            红队读数["n"] += 1
            if 红队读数["n"] == 1:      # 首轮：缺冻结输入包，全 null 不齐 → 仲裁 → 建模返工 → 复核轮
                return {"问题": 1, "复算方式": "独立实现", "复算指标": {"厚度": None}, "口径说明": {"厚度": "缺冻结输入包"},
                        "结论": "不齐", "分歧明细": [{"指标": "厚度", "建模值": 10.0, "复算值": None, "相对差": 1.0}]}
        return {"问题": 1, "复算方式": "独立实现", "复算指标": {"厚度": 10.0}, "口径说明": {"厚度": "μm"},
                "结论": "对齐", "分歧明细": []}
    if "仲裁_问题" in rel:
        if 模式 == "红队不齐":
            return {"逐项": [{"指标": "厚度", "裁定": "建模错", "理由": "合成输入未冻结导出", "应改方": "建模"}], "总裁定": "建模需返工"}
        return {"逐项": [{"指标": "厚度", "裁定": "口径差异", "理由": "…", "应改方": "无"}], "总裁定": "口径差异成立"}
    if "需求追踪矩阵" in rel:
        return [{"需求号": f"{q}-{i}", "内容": "…", "状态": "已落位",
                 "落位": {"章节": f"{q+2}.问题{q}求解.tex", "图表": [f"图{q}_1"], "关键数字": "1.23"}}
                for q in (1, 2, 3) for i in (1, 2)]
    if re.search(r"审稿意见_轮\d[AB]\.json$", rel):
        轮 = int(re.search(r"轮(\d)", rel).group(1))
        低 = (模式 == "级联" and 轮 == 1)
        # 轮1 给意见进台账；轮≥2 不再给新意见（裁定 mock 会把上轮条目判已消解）→ 台账收敛 → 审稿场按新判据退出
        return {"三分钟印象": "…", "印象分": 8.0 if 低 else 8.7, "总分": 7.9 if 低 else 8.8,
                "各维": [{"维度": "建模严谨", "分数": 9, "理由": "…", "修改指令": "…"}],
                "最高优先级修改": ([{"级别": "正确性" if 模式 in ("S5续跑", "门升格", "G5图路", "G5算条", "G5页数") else "叙述",   # S5续跑/门升格/G5图路/G5算条：轮1 给阻塞级，逼审稿场真的分路并走到轮2
                                     "目标": "算" if 模式 == "门升格" else "文",   # 门升格：派「算」→ 回炉算问1 → G2门 只复检（R44）
                                     "问题": "问题1 厚度网格过粗需重算" if 模式 == "门升格" else "某段偏模板", "定位": "3.tex:2段",
                                     "指令": "重写开场", "验收": "章评≥8"}] if 轮 == 1 else [])}
    if re.search(r"/回执_.+\.json$", rel):
        腿 = re.search(r"回执_(.+)\.json$", rel).group(1)
        ids = sorted(set(re.findall(r"\[([审章图美解]-\d+-\d+)\]", 任务文本.get(腿, ""))))
        return [{"id": i, "改动": "已按指令修改", "证据": "mock:行1"} for i in ids]
    if re.search(r"/裁定_.+\.json$", rel):
        腿 = re.search(r"裁定_(.+)\.json$", rel).group(1)
        ids = sorted(set(re.findall(r"\[([审章图美解]-\d+-\d+)\]", 任务文本.get(腿, ""))))
        return {"逐项": [{"id": i, "裁定": "已消解", "理由": "mock 核对一致"} for i in ids],
                "相对判断": "更好", "决定性理由": "mock", "分数": 8.8}
    if "审稿台账_视图" in rel:
        return {"轮次": 1, "摘要": {}, "收敛": True, "阻塞明细": [], "条目": []}
    if "硬伤_轮" in rel:
        轮 = int(re.search(r"轮(\d)", rel).group(1))
        if 模式 == "级联" and 轮 == 1:
            # 一条“算”级硬伤，指向问题1 → 触发回炉重算 + 级联下游
            return [{"类别": "逻辑幻觉", "目标": "算", "定位": "求解/问题1/结果/核心.json 与正文不符（问题1）",
                     "证据": "问题1 头条数字与结果JSON差 30%", "级别": "硬伤",
                     "修改指令": "重算问题1核心指标并同步正文"}]
        if 模式 == "G5算条" and 轮 == 2:
            return [{"类别": "数字成套错版", "目标": "图", "定位": "求解/问题3/图片/图3_1.png 与 8.3 节正文（问题3）",
                     "证据": "图内硅层厚度标注 2.58 μm，正式结果 JSON 为 2.165 μm", "级别": "硬伤", "修改指令": "改绘图脚本从结果 JSON 读数重绘"},
                    {"类别": "逻辑幻觉", "目标": "算", "定位": "求解/问题3/求解_问题3.py（问题3）",
                     "证据": "硅第二折与最坏扰动未收敛，公平预算与参数敏感性未分开", "级别": "正确性", "修改指令": "重算问题3第二折"}]
        if 模式 == "G5页数" and 轮 == 2:
            return [{"类别": "数字成套错版", "目标": "图", "定位": "求解/问题3/图片/图3_1.png 与 8.3 节正文（问题3）",
                     "证据": "图内硅层厚度标注 2.58 μm，正式结果 JSON 为 2.165 μm", "级别": "硬伤", "修改指令": "改绘图脚本从结果 JSON 读数重绘"},
                    {"类别": "溯源缺失", "目标": "文", "定位": "论文/99.附录.tex（问题3）",
                     "证据": "附录未引用现行求解入口", "级别": "正确性", "修改指令": "补入现有求解入口及其依赖的引用"}]
        if 模式 == "G5图路" and 轮 == 2:
            # 快速档审稿 2 轮：末轮才冒出的图硬伤没有回炉机会，按「轮数上限」带进 G5——真炉里 问3 成套错版就是这么留下的
            return [{"类别": "数字成套错版", "目标": "图", "定位": "求解/问题3/图片/图3_1.png 与 8.3 节正文（问题3）",
                     "证据": "图内硅层厚度标注 2.58 μm，正式结果 JSON 为 2.165 μm", "级别": "硬伤",
                     "修改指令": "改绘图脚本从结果 JSON 读数重绘，正文同步"}]
        return []          # 硬伤清零
    if "评委模拟_轮" in rel:
        轮 = int(re.search(r"轮(\d)", rel).group(1))
        return {"第一印象分": 8.6, "能否复述四要素": "能", "卡住的地方": ["图3标注偏小"] if 轮 == 1 else [],
                "修订建议": ["放大图3标注"] if 轮 == 1 else []}
    if "审计报告" in rel:
        return {"禁用词": {"数量": 0, "明细": []}, "图型": {"图文件数": 18, "示意图数": 3, "柱折占比": 0.4, "违规": []},
                "溯源核验": {"核对通过": 40, "存疑数": 0, "存疑明细": []},
                "图表引用句式": {"引用总数": 30, "如图如表句式数": 9, "占比": 0.3, "违规": []}}
    if "复盘报告" in rel:
        return {"总评": "…", "瓶颈环节": [{"环节": "S5轮2", "现象": "…", "耗时/返工数": "…"}],
                "规则库修改建议": [{"文件": "运行时/禁用词.txt", "修改": "增加X", "理由": "本次4处"},
                                   {"文件": "角色/撰稿师.md", "修改": "强化Y", "理由": "…"},
                                   {"文件": "配置", "修改": "审稿轮数3", "理由": "…"}],
                "下次运行参数建议": {"审稿轮数": 3}}
    if "门检_叙事" in rel:
        return {"通过": True, "明细": [], "统计": {"问题数": 3}}
    if re.search(r"读者R\d", rel):
        return [{"章": "3.问题1求解.tex", "复述": "从条纹间距反推厚度", "卡住": [{"原句": "证据门通过后给出条件答案", "为什么": "不知道证据门是什么"}],
                 "自造词": ["证据门"], "以为的答案": "10 μm", "读者分": 7}]
    if "摘要复述" in rel:
        return {"每问": [{"问题": q, "复述": f"问{q} 厚度 10 μm", "数值": "10 μm", "能否复述": "能"} for q in (1, 2, 3)],
                "第一句白话": True, "置信声明次数": 1, "读不懂的词": [], "30秒印象": "清楚"}
    if "统稿回执" in rel:
        return {"名字表": {"条纹间距": "条纹间距"}, "逐文件": [], "置信保留处": []}
    if re.search(r"门检_(G\d)", rel):
        门 = re.search(r"门检_(G[\d:问]+)", rel).group(1)
        坏 = any(门.startswith(b) for b in 坏门)
        if 模式 in ("G5图路", "G5算条", "G5页数") and 门 == "G5" and 假根["p"]:
            视图p = 假根["p"] / "审稿/审稿台账_视图.json"
            视图 = json.loads(视图p.read_text(encoding="utf-8")) if 视图p.exists() else {}
            阻 = [m for m in 视图.get("阻塞明细", []) if "搁置" not in m]
            搁 = [m for m in 视图.get("阻塞明细", []) if "搁置" in m]
            明细 = ([f"审稿台账未收敛：{len(阻)} 条阻塞级意见未消解（{[m[:40] for m in 阻[:3]]}）"] if (not 视图.get("收敛", True) or 阻) else []) + \
                   ([f"审稿台账有 {len(搁)} 条阻塞级意见被搁置（两次修订未消解，出版前必须处理）"] if 搁 else [])   # 与真门检同判据：搁置也不放行
            if 明细:
                return {"通过": False, "明细": 明细}
        return {"通过": not 坏, "明细": [] if not 坏 else ["模拟门检失败：假设台账检验结果为空"]}
    if re.search(r"图评R\d", rel):
        轮 = int(re.search(r"R(\d)", rel).group(1))
        out = [{"图": p, "分数": 8.5, "问题": [], "修改指令": []} for p in 假图[:7]]
        if 轮 == 1:      # 轮1 一张低分图带指令 → 进台账 → 图修 → 轮2 配对评审判已消解
            out[0] = {"图": 假图[0], "分数": 6.0, "问题": ["极值未标注"], "修改指令": ["在峰值处 annotate 数值并加参考线"]}
        return out
    if re.search(r"章评R\d", rel):
        轮 = int(re.search(r"R(\d)", rel).group(1))
        out = [{"章": f, "分数": 8.6, "AI味": 9, "跳步": 8, "溯源": 9, "问题": [], "修改指令": []}
               for f in ("1.引言.tex", "3.问题1求解.tex")]
        if 轮 == 1:
            out[1] = {"章": "3.问题1求解.tex", "分数": 6.5, "AI味": 6, "跳步": 7, "溯源": 7,
                      "问题": ["第2段数字无 % src"], "修改指令": ["第2段“误差 1.23%”补 % src:结果.json:误差 并改为与真值一致"]}
        return out
    if re.search(r"美(\d)_", rel):
        轮 = int(re.search(r"美(\d)_", rel).group(1))
        return {"页问题": ([{"页": 3, "严重度": 3, "问题": "图3内字号过小", "修改指令": "论文/3.问题1求解.tex 中 fig:3 宽度 0.8→0.95\\textwidth 并 trim 白边"},
                           {"页": 5, "严重度": 2, "目标": "图", "问题": "图5图例遮挡数据", "修改指令": "求解/问题1/绘图_图5.py 图例移到右上角外侧"}]   # P6：图条直派绘图师
                          if 轮 == 1 else []), "总体评价": "好", "美观分": 7.0 if 轮 == 1 else 8.8}
    if re.search(r"终审_", rel):
        return {"页问题": ([{"页": 2, "严重度": 2, "目标": "文", "问题": "表1列宽溢出", "修改指令": "论文/1.引言.tex 表1 改 tabularx"}] if rel.endswith("终审_1.json") else []),
                "总体评价": "好", "美观分": 8.8}   # R53：终审必改非空 → 派终审整改腿，任务只传文件不截断
    if "摘要验收" in rel:
        return {"第一印象分": 8.8, "能否复述四要素": "能", "四要素缺失": [], "30秒摘要复述": "mock"}
    if "摘要评审" in rel:
        return {"各变体": [{"变体": 1, "分数": 8.5}], "终版": "变体2为骨"}
    if "结果声明" in rel:
        return {"问题": 1, "核心指标": {"厚度": 10.0}, "口径说明": {"厚度": "μm"}}
    if "假设台账" in rel:
        return [{"假设号": "A1", "假设": "…", "依据": "数据档案", "灵敏度义务": "±20%", "检验结果": "已检验<3%"}]
    return {"占位": True}


class 假Hive:
    def __init__(self, account="a1", **kw):
        self.account = account
        self.root = pathlib.Path(kw.get("root") or tempfile.mkdtemp())
        self.root.mkdir(parents=True, exist_ok=True)
        假根["p"] = self.root
        假根["engine"] = kw.get("engine", "codex")   # 引擎 场景断言驱动把 --引擎 传到了 Hive


    def exec(self, script, files=None, outputs=None, timeout_s=560, timeout_ms=None, quiet=False):
        s = script
        # 写文件请求（台账视图/需求矩阵/论文.tex/回退等）：本地版真的落到 root 下
        if files:
            for f in files:
                if "content" not in f:          # f_local（回退）：本地文件已由驱动写回，别用空串盖掉
                    continue
                p = self.root / f["path"].lstrip("/")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f.get("content", ""), encoding="utf-8")
        # 波次轮询（M5-1 补记④ 并发闸版）：列出已写 done 的腿名。死亡演练第一轮装作全都没完成，
        # 让活性检查有机会注入 DEAD。
        m = re.search(r"for n in (.+?); do \[ -f 日志/\$n\.done \] && echo \$n; done", s)
        if m and "kill -0" not in s:
            names = m.group(1).split()
            if 死亡演练["开启"] and not 死亡演练["已注入"]:
                return {"exit": 0, "stdout": ""}
            return {"exit": 0, "stdout": "\n".join(names)}
        # 波次轮询（旧版）：done 计数
        m = re.search(r"c=0; for f in (.+?); do", s)
        if m and "kill -0" not in s:
            n = len(m.group(1).split())
            if 死亡演练["开启"] and not 死亡演练["已注入"]:
                return {"exit": 0, "stdout": "0"}      # 第一轮：装作全都没完成
            return {"exit": 0, "stdout": str(n)}
        # 波次活性检查：kill -0 探活（死亡演练时让第一条腿"死"一次）
        if "kill -0" in s and "for n in" in s:
            names = re.search(r"for n in (.+?); do", s).group(1).split()
            out = []
            for nm in names:
                if 死亡演练["开启"] and not 死亡演练["已注入"] and nm == names[0]:
                    死亡演练["已注入"] = True
                    out.append(f"{nm} DEAD")
                else:
                    out.append(f"{nm} ALIVE")
            return {"exit": 0, "stdout": "\n".join(out)}
        # 脚本轮询：rc + RUNNING
        m = re.search(r"cat 日志/(\S+)\.done", s)
        if m:
            return {"exit": 0, "stdout": "rc=0\nRUNNING"}
        m = re.search(r"head -1 日志/(\S+)\.done", s)
        if m:
            return {"exit": 0, "stdout": "PASS 干跑完成摘要"}
        if "shasum" in s:                       # 求解/结果指纹：干跑里每次都不同 → G2 返工总走 声明刷新+红队复核 全路径
            红队读数["指纹"] = 红队读数.get("指纹", 0) + 1
            return {"exit": 0, "stdout": f"fp{红队读数['指纹']:04d}"}
        if "xelatex" in s:
            P = 19
            if 模式 == "G5页数":
                附 = self.root / "论文/99.附录.tex"
                if 附.is_file() and "lstinputlisting" in 附.read_text(encoding="utf-8"):
                    P = 40                      # 整份源码塞进附录：19 → 40 页
            return {"exit": 0, "stdout": f"E=0 O=3 P={P} 摘要页=1"}
        if "find 求解 -name '*.png'" in s:
            return {"exit": 0, "stdout": "\n".join(假图)}
        if "ls 论文/页/*.png" in s:
            return {"exit": 0, "stdout": "\n".join(假页)}
        if "find 求解 -type f" in s or "find " in s and "-type f" in s:
            return {"exit": 0, "stdout": "\n".join(假图[:5])}
        if "ls 论文/*.tex" in s:
            return {"exit": 0, "stdout": "论文/论文.tex\n论文/0.摘要.tex"}
        if "bin/门检.py" in s:
            return {"exit": 0, "stdout": "门检完成"}
        if "成图" in s and "[ -f" in s:
            return {"exit": 0, "stdout": "ok"}
        if "nohup" in s and "codex exec" in s:      # 图片腿（旧内联路径，兜底记录）
            名 = re.search(r"echo (\S+)启动", s)
            腿记录.append(("图片腿", 名.group(1) if 名 else "?"))
            if 名:
                任务文本[名.group(1)] = script
            return {"exit": 0, "stdout": "启动"}
        if "nohup" in s and "python3" in s or "nohup" in s and "bash " in s:
            m = re.search(r"日志/(\S+?)\.log", s)
            脚本记录.append(m.group(1) if m else "?")
            事件序.append(m.group(1) if m else "?")
            return {"exit": 0, "stdout": "started"}
        return {"exit": 0, "stdout": "ok"}

    @staticmethod
    def f_text(path, content):
        return {"path": path, "content": content}

    @staticmethod
    def f_local(path, local_path):
        return {"path": path, "contentB64": "x"}

    def harvest(self, outputs, dest_dir, 重试=3):
        got = []
        for rel in outputs:
            p = pathlib.Path(dest_dir) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if rel.endswith(".json"):
                p.write_text(json.dumps(_mock_json(rel), ensure_ascii=False), encoding="utf-8")
            else:
                p.write_text("干跑占位", encoding="utf-8")
            got.append(rel)
        return got

    def leg(self, role_file, task_text, log_name, timeout_s=560, sync=True, **kw):
        腿记录.append((role_file, log_name))
        效力记录[log_name] = kw.get("effort")
        事件序.append(log_name)
        任务文本[log_name] = task_text
        if 模式 == "G5页数" and log_name == "G5返工图1":  # S0 会清空工作根，附录源码章在 G5 图路时才播种（快照在其后取）
            附 = self.root / "论文/99.附录.tex"
            附.parent.mkdir(parents=True, exist_ok=True)
            if not 附.exists():
                附.write_text("\\section{附录}\n\\begin{lstlisting}\nprint(1)\n\\end{lstlisting}\n", encoding="utf-8")
        if 模式 == "G5页数" and log_name == "G5返工1":   # 撰稿师照评审指令「补入求解入口引用」整份塞源码
            附 = self.root / "论文/99.附录.tex"
            附.write_text(附.read_text(encoding="utf-8") + "\\lstinputlisting[language=Python]{../求解/问题3/求解_问题3.py}\n"
                        "\\lstinputlisting[language=Python]{../求解/问题3/复算.py}\n", encoding="utf-8")
        if 中断于 and log_name.startswith(中断于) and not _已中断["flag"]:
            _已中断["flag"] = True
            raise KeyboardInterrupt(f"模拟 kill 于 {log_name}")
        return {"exit": 0, "stdout": "launched"}

    def leg_img(self, role_file, task_text, log_name, images, timeout=900, reasoning=True, **kw):
        腿记录.append(("图片腿", log_name))
        效力记录[log_name] = kw.get("effort")
        任务文本[log_name] = task_text
        return {"exit": 0, "stdout": f"{log_name}启动"}


# ---------------- 注入假模块并运行驱动 ----------------
def 跑驱动(工作目录, 续跑=False):
    腿记录.clear()
    脚本记录.clear()
    事件序.clear()
    假模块 = types.ModuleType("本地蜂巢")
    假模块.LocalHive = 假Hive
    sys.modules["本地蜂巢"] = 假模块
    import time as _t
    原sleep = _t.sleep
    _t.sleep = lambda *a, **k: None
    argv原 = sys.argv[:]
    sys.argv = ["蜂群驾驶.py", str(项目根 / "真题测试/输入"), str(工作目录 / "成品"), "--快速"] + os.environ.get("DRYRUN_OPTS", "").split() + (["--resume"] if 续跑 else [])
    for m in ("蜂群驾驶", "调度器"):
        sys.modules.pop(m, None)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("蜂群驾驶", 流水线 / "蜂群驾驶.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except BaseException as e:
        print(f"  [驱动退出：{type(e).__name__}: {e}]")
    finally:
        _t.sleep = 原sleep
        sys.argv = argv原
    return list(腿记录), list(脚本记录)


def 读日志(工作目录):
    p = 工作目录 / "运行日志.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def 读状态(工作目录):
    p = 工作目录 / "状态.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


with tempfile.TemporaryDirectory() as td:
    工作 = pathlib.Path(td)
    print(f"===== 干跑模式：{模式} =====")
    腿, 脚本 = 跑驱动(工作)
    日志 = 读日志(工作)
    st = 读状态(工作)
    print(f"\n腿数={len(腿)} 脚本执行={len(脚本)}")
    if any(效力记录.values()):
        import collections
        print(f"角色分档下发：{dict(collections.Counter(v or '全局' for v in 效力记录.values()))}")
    print(f"已完成节点({len(st.get('已完成节点', []))}): {st.get('已完成节点')}")
    print(f"问题门状态: {st.get('问题门状态')}")
    print(f"门失败计数: {st.get('门失败计数')}")
    print(f"降级放行: {st.get('降级放行')}")

    if 模式 == "全链":
        关键 = ["S2 依赖DAG", "拓扑分层", "S2 第1层启动", "S2 第2层启动", "S1 路线侦察", "修订单",
                "图评轮", "章评轮", "S6 终稿收割", "复盘"]
        print("\n--- 关键日志行 ---")
        for line in 日志.splitlines():
            if any(k in line for k in 关键):
                print(" ", line.strip())
        print("\n--- 腿清单（角色, 名称）---")
        for r, n in 腿:
            print(f"   {r:16s} {n}")
        assert any(n == "美化图1" for _, n in 腿) and "成图美化1" in 事件序 and any(n == "排版执行1" for _, n in 腿), \
            f"P6 美化图路未走（美化师 目标=图 的条目应派绘图师 + 成图美化1，文条仍派排版执行）：{[n for _, n in 腿 if '美' in n or '排版' in n]}"
        print("[PASS] P6 美化图路：目标=图 → 绘图师 美化图1 + 成图美化1；目标=文 → 排版执行1")
        assert "终审整改" in 任务文本 and "审稿/终审_1.json" in 任务文本["终审整改"] and "共 1 条必改" in 任务文本["终审整改"], \
            f"R53 终审整改任务应传文件与条数而非截断 JSON：{任务文本.get('终审整改', '')[:200]}"
        print("[PASS] R53 终审整改：任务传 审稿/终审_N.json 与条数，不按字符截断")
        if 引擎场景:
            状态引擎 = json.loads((pathlib.Path(假根["p"]).parent / "状态.json").read_text(encoding="utf-8")).get("引擎")
            assert 状态引擎 == "claude", f"--引擎=claude 未记进 状态.json：{状态引擎}"
            assert 假根.get("engine") == "claude", f"Hive 未收到 engine=claude：{假根.get('engine')}"
            print("[PASS] 引擎：--引擎=claude → 配置 → Hive(engine=claude) → 状态.json 引擎=claude")

    if 模式 == "中断续跑":
        print(f"\n[第一次运行被模拟 kill]，落盘节点={st.get('已完成节点')}")
        腿2, _ = 跑驱动(工作, 续跑=True)
        日志2 = 读日志(工作)
        st2 = 读状态(工作)
        跳过行 = [l for l in 日志2.splitlines() if "已完成，跳过（续跑）" in l]
        print(f"\n[--resume 第二次运行] 新增腿={len(腿2)}")
        print(f"跳过节点行数={len(跳过行)}：")
        for l in 跳过行[:12]:
            print("  ", l.strip())
        重做S0 = [n for _, n in 腿2 if n in ("读题", "体检", "答卷预测", "路线侦察", "规划")]
        print(f"\n续跑是否重做 S0/S1 腿（应为空）：{重做S0}")
        print(f"运行ID 是否保持：{st.get('运行ID')} → {st2.get('运行ID')}  {'一致✓' if st.get('运行ID')==st2.get('运行ID') else '不一致✗'}")
        print(f"最终节点数：{len(st2.get('已完成节点', []))}")

    if 模式 == "门升格":
        S5段 = 日志.split("S5 轮1", 1)[1] if "S5 轮1" in 日志 else ""
        复检行 = [l.strip() for l in S5段.splitlines() if "只复检" in l]
        升格问1 = 日志.count("升格蜂群[G2问1]：派")
        print(f"\nS5 回炉算 只复检行：{复检行[:2]}；升格蜂群[G2问1] 派腿次数（应为 1，只有 S2 那次）：{升格问1}")
        assert 复检行 and 升格问1 == 1 and "回炉算_轮1_问1" in 日志, "R44 回炉后只复检 未生效"
        print("[PASS] R44 已降级放行的问在 S5 回炉后只复检一次，不再返工/升格")

    if 模式 == "S5续跑":
        print(f"\n[第一次运行在 审2A 被模拟 kill]，S5已完成轮={st.get('S5已完成轮')}")
        assert st.get("S5已完成轮") == 1, "轮1 修订后应落盘 S5已完成轮=1"
        腿2, _ = 跑驱动(工作, 续跑=True)
        日志2 = 读日志(工作)
        st2 = 读状态(工作)
        续行 = [l.strip() for l in 日志2.splitlines() if "S5 续跑" in l]
        print("续跑行：", 续行)
        重做轮1 = [n for _, n in 腿2 if n in ("审1A", "审1B", "硬伤1", "评委模拟1") or n.startswith("回炉文_轮1") or n.startswith("回炉图_轮1")]
        轮2 = [n for _, n in 腿2 if n in ("审2A", "审2B")]
        print(f"续跑重做轮1 腿（应为空）：{重做轮1}；轮2 评审腿：{轮2}")
        assert 续行 and not 重做轮1 and 轮2, "R43 轮级断点未生效"
        assert "S5已完成轮" not in st2 and "S5:审稿场" in st2.get("已完成节点", []), "节点完成后应清除轮级断点"
        print(f"[PASS] R43 S5 轮级断点：续跑从轮2 起，节点完成后清键；最终节点数 {len(st2.get('已完成节点', []))}")

    if 模式 == "G5图路":
        序 = [e for e in 事件序 if e in ("G5返工图1", "成图回炉G5_1", "G5返工1", "硬伤G5返工1")]
        门行 = [l.strip() for l in 日志.splitlines() if "门[G5]" in l]
        print("\nG5 事件序：", 序)
        for l in 门行:
            print("  ", l[:160])
        # R50 起：图条消解后没有文/算可改 → 不再派撰稿师 G5返工1，直接猎手复核
        assert 序 == ["G5返工图1", "成图回炉G5_1", "硬伤G5返工1"], f"R49 图路顺序不对：{序}"
        assert any("第1次检查 FAIL" in l for l in 门行) and any("第2次检查 PASS" in l for l in 门行), "G5 应 1 败后图路返工、第 2 次过门"
        assert not any(d.get("门") == "G5" for d in st.get("降级放行", [])) and "G5" in st.get("已完成节点", []), "G5 不应降级放行"
        print(f"[PASS] R49 G5 图路：绘图师改图 → 成图回炉 →（无文可改不派撰稿师，R50）→ 猎手复核 → 第 2 次过门；最终节点数 {len(st.get('已完成节点', []))}")

    if 模式 == "G5页数":
        序 = [e for e in 事件序 if e.startswith("G5返工") or e.startswith("硬伤G5") or e.startswith("成图回炉G5")]
        门行 = [l.strip() for l in 日志.splitlines() if "门[G5]" in l or "页数守卫" in l or "复核前" in l]
        print("\nG5 事件序：", 序)
        for l in 门行:
            print("  ", l[:170])
        附 = (假根["p"] / "论文/99.附录.tex").read_text(encoding="utf-8")
        留 = 假根["p"] / "审稿/回退稿/G5返工1/99.附录.tex"
        assert "G5返工1" in 序 and 序.index("G5返工1") < 序.index("硬伤G5返工1"), f"撰稿师应先于猎手：{序}"
        assert any("编译#G5返工1复核前" in l for l in 日志.splitlines()), "R51：猎手复核前应编译"
        assert any("!! 页数守卫[G5返工1]" in l and "回退本轮代码章" in l for l in 门行), "R52：应回退代码章"
        assert "lstinputlisting" not in 附, "附录源码章应已回退到快照"
        assert 留.is_file() and "lstinputlisting" in 留.read_text(encoding="utf-8"), "回退的新稿应留底"
        assert any("检查 PASS" in l for l in 门行) and not any(d.get("门") == "G5" for d in st.get("降级放行", [])), "G5 应最终过门且不降级"
        assert st.get("美化后页数") == 19 or (st.get("数据") or {}).get("美化后页数") == 19, f"美化后页数基线应记进状态：{st.get('美化后页数')}"
        print(f"[PASS] R51/R52 G5 页数：复核前编译、40 页 > 19+2 → 回退代码章并留底、回执作废后重派、最终过门；G5 相关腿 {len(序)} 条")

    if 模式 == "G5算条":
        序 = [e for e in 事件序 if e.startswith("G5返工") or e.startswith("硬伤G5") or e.startswith("成图回炉G5")]
        门行 = [l.strip() for l in 日志.splitlines() if "门[G5]" in l or "G5 返工" in l]
        print("\nG5 事件序：", 序)
        for l in 门行:
            print("  ", l[:170])
        assert "G5返工图1" in 序 and "成图回炉G5_1" in 序 and "硬伤G5返工1" in 序, f"图路/复核未走：{序}"
        assert "G5返工1" not in 序 and "G5返工2" not in 序, f"算条不该派撰稿师：{序}"
        assert any("无路可派 → 搁置" in l for l in 门行), "应记录算条搁置"
        assert any(d.get("门") == "G5" for d in st.get("降级放行", [])) and "G5" in st.get("已完成节点", []), "算条搁置后 G5 应三次检查降级放行"
        assert "S6:复盘" in st.get("已完成节点", []), "降级后应继续 S6"
        print(f"[PASS] R50 G5 算条：图路照走、撰稿师不空转、算条搁置、降级放行后 S6 走完；G5 相关腿 {len(序)} 条")

    if 模式 == "级联":
        print("\n--- 级联相关日志 ---")
        for line in 日志.splitlines():
            if "级联" in line or "第1层" in line or "第2层" in line:
                print(" ", line.strip())

    if 模式 == "门升格":
        print("\n--- 门/升格相关日志 ---")
        for line in 日志.splitlines():
            if "门[" in line or "升格" in line or "降级" in line:
                print(" ", line.strip())
        升格腿 = [n for _, n in 腿 if n.startswith("升格")]
        print(f"\n升格腿：{升格腿}")
        # R34：变体脚本必须先于裁决腿跑完；裁决后必须有红队复核 + 解读_升格后（门检只认最新一条 解读 done）
        def 位(名):
            return 事件序.index(名) if 名 in 事件序 else -1
        坏 = []
        for q in (1, 2, 3):
            裁 = 位(f"升格裁决_G2问{q}")
            if 裁 < 0:
                坏.append(f"问{q} 无裁决腿"); continue
            if not all(0 <= 位(f"跑升格_问{q}_{k}") < 裁 for k in (1, 2, 3)):
                坏.append(f"问{q} 变体脚本未在裁决前跑完")
            if not (位(f"红队_问题{q}_升格后") > 裁 and 位(f"解读_问题{q}_升格后") > 位(f"红队报告_问题{q}_升格后") > 裁):
                坏.append(f"问{q} 裁决后缺 红队复核→解读_升格后 的顺序")
            解读位 = [i for i, n in enumerate(事件序) if n == f"解读_问题{q}_G2返工"]   # 两次返工同名，取第 n 次出现
            for n in (1, 2):
                if not (0 < 位(f"声明刷新_问题{q}_G2返工{n}") < 位(f"红队_问题{q}_G2返工{n}") and len(解读位) >= n
                        and 解读位[n - 1] > 位(f"红队报告_问题{q}_G2返工{n}")):
                    坏.append(f"问{q} 第{n}次返工后缺 声明刷新→红队复核→解读 的顺序")
        print(f"[{'PASS' if not 坏 else 'FAIL'}] R34 升格链路顺序：{坏 or '变体脚本→裁决→红队复核→解读_升格后 全对'}")

    if 模式 == "红队不齐":
        print("\n--- R33：红队不齐→仲裁→建模返工→红队复核→解读（问1）---")
        期望 = ["红队_问题1", "跑红队_问题1", "红队报告_问题1", "仲裁_问题1", "仲裁返工_问题1", "执行_问题1_仲裁后",
                "声明刷新_问题1_仲裁后", "红队_问题1_复核", "跑红队_问题1_复核", "红队报告_问题1_复核", "解读_问题1_仲裁后"]
        序 = [n for n in 事件序 if n in 期望]
        print("  实际先后：", 序)
        坏 = []
        if 序 != 期望:
            坏.append("先后序与期望不一致")
        if any("复核_复核" in n for n in 事件序):
            坏.append("复核轮递归了（深度上限失效）")
        if 事件序.count("解读_问题1_仲裁后") != 1:
            坏.append(f"解读_问题1_仲裁后 出现 {事件序.count('解读_问题1_仲裁后')} 次（应恰 1 次）")
        if "仲裁_问题1_复核" in 事件序:
            坏.append("复核轮红队已对齐却又派了仲裁")
        if not (st.get("已完成节点") and len(st.get("已完成节点", [])) == 31):
            坏.append(f"全链未走完：{len(st.get('已完成节点', []))} 节点")
        任 = 任务文本.get("红队_问题1_复核", "")
        if "复核轮" not in 任 or "冻结合成输入" not in 任 or "必须写 done 标记" not in 任:
            坏.append("复核轮红队任务文本缺 复核轮/冻结包/done 说明")
        刷 = 任务文本.get("声明刷新_问题1_仲裁后", "")
        if "不做裁定" not in 刷 or "结果声明_问题1.json" not in 刷:
            坏.append("声明刷新腿任务文本不对")
        print(f"[{'PASS' if not 坏 else 'FAIL'}] R33 红队复核链路：{坏 or '顺序、次数、深度上限、任务文本全对'}")

    if 模式 == "韧性":
        print("\n" + "=" * 70)
        print("场景A：波次中腿进程死亡（kill -0 判死）→ 应即时回收重派，不等 timeout 用尽")
        print("=" * 70)
        # 本地版判死：done 未写出且进程死了 → 当轮整组回收并即时重派。
        # 死亡演练让第一波次的第一条腿先"死"一次，之后恢复。
        死亡演练["开启"] = True
        工作B = 项目根 / "真题测试/干跑_韧性"
        shutil.rmtree(工作B, ignore_errors=True)
        (工作B).mkdir(parents=True, exist_ok=True)
        腿B, _ = 跑驱动(工作B)
        日志B = 读日志(工作B)
        判死行 = [l for l in 日志B.splitlines() if "进程已死但 done 未写" in l]
        重派行 = [l for l in 日志B.splitlines() if "即时重派" in l]
        print(f"判死行数={len(判死行)}（期望 ≥1）")
        for l in 判死行[:4]:
            print("  ", l.strip())
        stB = 读状态(工作B)
        print(f"死亡重派后是否仍走完全链：完成节点={len(stB.get('已完成节点', []))}（期望 31）")
        A通过 = len(判死行) >= 1 and len(重派行) >= 1 and len(stB.get("已完成节点", [])) == 31
        print(f"[{'PASS' if A通过 else 'FAIL'}] 场景A：腿死亡即时重派，且之后能跑完全链")

        print("\n" + "=" * 70)
        print("场景B：预算护栏 → 超预算必须刹车并走应急出版，不许无限跑")
        print("=" * 70)
        死亡演练["开启"] = False
        工作C = 项目根 / "真题测试/干跑_预算"
        shutil.rmtree(工作C, ignore_errors=True)
        (工作C).mkdir(parents=True, exist_ok=True)
        腿记录.clear()
        脚本记录.clear()
        假模块 = types.ModuleType("本地蜂巢")
        假模块.LocalHive = 假Hive
        sys.modules["本地蜂巢"] = 假模块
        import time as _t
        原sleep = _t.sleep
        _t.sleep = lambda *a, **k: None
        argv原 = sys.argv[:]
        sys.argv = ["蜂群驾驶.py", str(项目根 / "真题测试/输入"), str(工作C / "成品"), "--快速"]
        for m in ("蜂群驾驶", "调度器"):
            sys.modules.pop(m, None)
        护栏行, 应急行, 报错 = [], [], ""
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("蜂群驾驶", 流水线 / "蜂群驾驶.py")
            mod = importlib.util.module_from_spec(spec)
            # 关键：把腿上限压到 5，让护栏必然在中途触发
            src = (流水线 / "蜂群驾驶.py").read_text(encoding="utf-8").replace(
                '"MAX_LEGS": 600', '"MAX_LEGS": 5')
            exec(compile(src, str(流水线 / "蜂群驾驶.py"), "exec"), mod.__dict__)
        except BaseException as e:
            报错 = f"{type(e).__name__}: {e}"
        finally:
            _t.sleep = 原sleep
            sys.argv = argv原
        日志C = 读日志(工作C)
        护栏行 = [l for l in 日志C.splitlines() if "预算护栏触发" in l]
        应急行 = [l for l in 日志C.splitlines() if "应急出版" in l or "预算护栏收尾" in l]
        print(f"护栏触发行数={len(护栏行)}（期望 ≥1）")
        for l in 护栏行[:3]:
            print("  ", l.strip())
        print(f"应急出版/收尾行数={len(应急行)}（期望 ≥1）")
        for l in 应急行[:4]:
            print("  ", l.strip())
        stC = 读状态(工作C)
        print(f"最终阶段={stC.get('阶段')}（期望 预算耗尽收尾）")
        B通过 = len(护栏行) >= 1 and len(应急行) >= 1 and stC.get("阶段") == "预算耗尽收尾"
        print(f"[{'PASS' if B通过 else 'FAIL'}] 场景B：预算超限能刹车并兜底收割")

        print("\n" + "=" * 70)
        print(f"韧性总判定：{'全部通过' if (A通过 and B通过) else '存在失败项'}")
        print("=" * 70)
