#!/usr/bin/env python3
"""回路：所有「生成 → 评审 → 修订」循环共用的收敛协议（纯逻辑，本地可测）。

为什么需要它——20260831 轮实测（真题测试/运行日志.md 可逐行回查）：
  图评   6.17 → 6.60（低分 11 → 10 张，轮数上限退出）
  章评   6.50 → 6.40（整章重写后**更差**，低分 4 → 5 章）
  审稿   6.15 → 7.15 → 6.05 → 6.30（噪声 ±0.5 之下拿「平台期 0.15」当收敛信号；硬伤 5→7→2→6 从未清零）
  美化   页问题 86 → 89、美观分 4.7 → 4.9，页数 85 → 88 → 126
四个循环没有一个收敛。病根相同：
  ① 意见没有身份：每轮从零开始审，同一个毛病换个措辞就成了「新问题」
     （评委模拟 轮1 与 轮4「卡住的地方」是同一件事：某问没交出答案、图内字太小）；
  ② 修订没有边界：「章重写」把整章推倒，评审喜欢的部分一起没了；
  ③ 评审没有参照：绝对打分（1–10）噪声 ±0.5，却拿它当退出判据，于是永远跑到轮数上限；
  ④ 退步没有回退：更差的版本照单全收；
  ⑤ 修不掉的没有出口：轮数用完就「记入运行日志」（= 丢弃），然后 G5 逼返工腿自造 硬伤_轮5.json（0 条）过门。

协议五条，本模块各给一个可测的实现：
  台账     —— 意见有身份（id）、有状态、跨轮存活；同一毛病不许换措辞当新问题。
  回执     —— 修改腿只报「改了什么、证据在哪」，**不许改台账状态**（出题的不能改卷）。
  配对裁定 —— 评审腿先对上轮每条意见给 已消解/未消解，再给 更好|持平|更差 的相对判断，最后才是分数。
  最优保留 —— 相对判断为「更差」或分数跌破噪声带就回退到上一轮快照。
  熔断     —— 一条意见两次修不掉：硬伤/正确性 → 升格（换人换路），叙述/版式 → 搁置留痕，绝不静默丢弃。
退出判据从「分数平台期」改为「台账里没有阻塞级别的未消解条目」；分数只做诊断。

所有权：台账文件**只由驱动写**。评审腿产出裁定、修改腿产出回执，驱动合并。
"""
import difflib
import json
import pathlib
import re
import time

待改, 待复核, 已消解, 未消解, 搁置 = "待改", "待复核", "已消解", "未消解", "搁置"
阻塞级别 = ("硬伤", "正确性")
_级别序 = {"硬伤": 0, "正确性": 1, "叙述": 2, "版式": 3}


def _净(s):
    return re.sub(r"[\s，。；：、,.;:()（）\[\]【】\"“”'‘’]+", "", str(s or "")).lower()


def 相似(a, b):
    """两段意见文本的相似度（0–1）。评审员措辞每轮都变，所以阈值不能定太高；显式 `对应` 优先。"""
    a, b = _净(a), _净(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _位置键(定位):
    """定位 → 粗粒度位置键：文件名（去目录）+ 第一个数字段。同键才谈相似。"""
    s = str(定位 or "")
    文件 = re.search(r"([\w.\-一-龥]+\.(tex|py|json|png|md))", s)
    数 = re.search(r"(\d+)", s.split("/")[-1] if "/" in s else s)
    return ((文件.group(1) if 文件 else "") + ":" + (数.group(1) if 数 else "")).strip(":")


def _再修提示(x):
    """已修过的条目再派时的提示：被变化守卫回退的（评审没判过）与被评审判未消解的，要说的话相反——
    前者是「改小点、只动点名句」，后者是「换一种改法」。"""
    if int(x.get("尝试次数", 0)) <= 0:
        return ""
    历 = x.get("历史") or []
    if 历 and 历[-1].get("裁定") == "变化守卫":
        return ("\n    注意：上次的修改因整份「未点名句子」改动比例超线被驱动整份回退（评审没有判过它）。"
                "这次只动本条点名的句子（意见引号里的原文及其所在段落）；没点名的句子一字不动，不要顺手润色、合并、前移别的段落")
    return (f"\n    注意：这条已经修过 {x['尝试次数']} 次仍被评审判未消解，上次改动：{(x.get('回执') or [{}])[-1].get('改动','')[:120]}——换一种改法，不要重复上次")


class 台账:
    """一个循环的意见台账。条目字段：
    id 级别 目标 定位 问题 指令 验收 来源 轮次 状态 尝试次数 重开次数 回执 历史
    """

    def __init__(self, 路径, 前缀="意"):
        self.路径 = pathlib.Path(路径)
        self.前缀 = 前缀
        self.条目 = []
        self.轮次 = 0
        if self.路径.exists():
            try:
                d = json.loads(self.路径.read_text(encoding="utf-8"))
                self.条目 = d.get("条目", [])
                self.轮次 = int(d.get("轮次", 0))
            except Exception:
                pass

    # ---------------------------------------------------------------- 持久化
    def 保存(self):
        self.路径.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.路径.with_suffix(".tmp")
        tmp.write_text(json.dumps({"轮次": self.轮次, "更新": time.strftime("%Y-%m-%d %H:%M:%S"),
                                   "摘要": self.摘要(), "条目": self.条目}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.路径)

    def 取(self, id):
        return next((x for x in self.条目 if x.get("id") == id), None)

    # ---------------------------------------------------------------- 并入新意见
    def 并入(self, 新条目们, 轮次, 来源=""):
        """把一轮评审的新意见并进台账。返回 {新增, 合并, 重开}。

        同一条意见的判定（按优先级）：
          1. 新条目带 `对应`（评审腿引用的已有 id）→ 直接合并；
          2. 位置键相同且问题相似 ≥ 0.45 → 合并；
          3. 问题相似 ≥ 0.75 → 合并。
        合并到一条**已消解**的条目上 = 重开（评审认为没修好）。
        """
        self.轮次 = max(self.轮次, int(轮次))
        统计 = {"新增": 0, "合并": 0, "重开": 0}
        序 = sum(1 for x in self.条目 if x.get("轮次") == 轮次)
        for 新 in 新条目们:
            if not isinstance(新, dict):
                新 = {"问题": str(新)}
            旧 = None
            if 新.get("对应"):
                旧 = self.取(str(新["对应"]).strip())
            if 旧 is None:
                候选 = []
                for x in self.条目:
                    if x.get("状态") == 搁置:
                        continue
                    s = 相似(x.get("问题"), 新.get("问题"))
                    if _位置键(x.get("定位")) and _位置键(x.get("定位")) == _位置键(新.get("定位")) and s >= 0.45:
                        候选.append((s + 0.5, x))
                    elif s >= 0.75:
                        候选.append((s, x))
                if 候选:
                    旧 = max(候选, key=lambda t: t[0])[1]
            if 旧 is not None:
                统计["合并"] += 1
                旧.setdefault("历史", []).append({"轮次": 轮次, "来源": 来源 or 新.get("来源", ""), "问题": str(新.get("问题", ""))[:300]})
                if 旧.get("状态") == 已消解:
                    旧["状态"] = 待改
                    旧["重开次数"] = int(旧.get("重开次数", 0)) + 1
                    统计["重开"] += 1
                # 级别只升不降：叙述级的意见被硬伤猎手再次点名 → 硬伤
                if _级别序.get(新.get("级别", ""), 9) < _级别序.get(旧.get("级别", ""), 9):
                    旧["级别"] = 新["级别"]
                if 新.get("指令"):
                    旧["指令"] = 新["指令"]
                continue
            序 += 1
            self.条目.append({
                "id": f"{self.前缀}-{轮次}-{序:02d}",
                "级别": 新.get("级别") if 新.get("级别") in _级别序 else "叙述",
                "目标": 新.get("目标") if 新.get("目标") in ("算", "图", "文") else "文",
                "定位": str(新.get("定位", 新.get("位置", "")))[:200],
                "问题": str(新.get("问题", ""))[:600],
                "指令": str(新.get("指令", 新.get("修改指令", "")))[:600],
                "验收": str(新.get("验收", ""))[:200],
                "来源": 来源 or str(新.get("来源", "")),
                "轮次": 轮次, "状态": 待改, "尝试次数": 0, "重开次数": 0, "回执": [], "历史": []})
            统计["新增"] += 1
        return 统计

    # ---------------------------------------------------------------- 查询
    def 待改条目(self, 级别们=None, 目标们=None):
        out = [x for x in self.条目 if x.get("状态") in (待改, 未消解)]
        if 级别们:
            out = [x for x in out if x.get("级别") in 级别们]
        if 目标们:
            out = [x for x in out if x.get("目标") in 目标们]
        out.sort(key=lambda x: (_级别序.get(x.get("级别"), 9), x.get("id", "")))
        return out

    def 摘要(self):
        m = {}
        for x in self.条目:
            k = f"{x.get('级别')}/{x.get('状态')}"
            m[k] = m.get(k, 0) + 1
        return m

    # ---------------------------------------------------------------- 回执（修改腿）
    def 收回执(self, 回执们, 腿名=""):
        """修改腿的回执：[{id, 改动, 证据}] → 状态=待复核。未知 id 忽略并计数（腿编 id 不算数）。"""
        统计 = {"受理": 0, "未知id": 0}
        for r in 回执们 or []:
            if not isinstance(r, dict):
                continue
            x = self.取(str(r.get("id", "")).strip())
            if x is None:
                统计["未知id"] += 1
                continue
            x.setdefault("回执", []).append({"腿": 腿名, "改动": str(r.get("改动", r.get("改动摘要", "")))[:400],
                                              "证据": str(r.get("证据", ""))[:300]})
            if x.get("状态") in (待改, 未消解):
                x["状态"] = 待复核
                x["尝试次数"] = int(x.get("尝试次数", 0)) + 1
            统计["受理"] += 1
        return 统计

    # ---------------------------------------------------------------- 裁定（评审腿）
    def 收裁定(self, 裁定们, 轮次=None):
        """评审腿对上轮条目的裁定：[{id, 裁定: 已消解|未消解, 理由}]。
        未消解不加尝试次数（尝试次数只在修改腿交回执时加）；只有评审腿能把条目置为已消解。"""
        统计 = {"已消解": 0, "未消解": 0, "未知id": 0}
        for r in 裁定们 or []:
            if not isinstance(r, dict):
                continue
            x = self.取(str(r.get("id", "")).strip())
            if x is None:
                统计["未知id"] += 1
                continue
            裁 = str(r.get("裁定", "")).strip()
            if 裁.startswith("已消解") or 裁.startswith("已解决") or 裁.startswith("已修"):
                x["状态"] = 已消解
                统计["已消解"] += 1
            else:
                x["状态"] = 未消解
                统计["未消解"] += 1
            x.setdefault("历史", []).append({"轮次": 轮次 if 轮次 is not None else self.轮次, "裁定": 裁[:20],
                                              "理由": str(r.get("理由", ""))[:300]})
        return 统计

    def 待复核未裁(self):
        """评审腿漏裁的待复核条目：不能默认通过，回到待改并记一笔。"""
        n = 0
        for x in self.条目:
            if x.get("状态") == 待复核:
                x["状态"] = 待改
                x.setdefault("历史", []).append({"轮次": self.轮次, "裁定": "评审未裁", "理由": "回执未获裁定，按未消解处理"})
                n += 1
        return n

    # ---------------------------------------------------------------- 熔断
    def 熔断候选(self, 阈值=2):
        return [x for x in self.条目 if x.get("状态") in (待改, 未消解) and int(x.get("尝试次数", 0)) >= 阈值]

    def 搁置条目(self, id, 理由):
        x = self.取(id)
        if x is None:
            return False
        x["状态"] = 搁置
        x["搁置理由"] = str(理由)[:300]
        return True

    # ---------------------------------------------------------------- 退出判据
    def 收敛(self, 阻塞=阻塞级别):
        """收敛 = 没有阻塞级别的 待改/待复核/未消解。返回 (收敛bool, 明细列表)。
        搁置的阻塞级条目不算「未收敛」，但会进明细——它们必须被 G5 与复盘看见。"""
        阻 = [x for x in self.条目 if x.get("级别") in 阻塞 and x.get("状态") in (待改, 待复核, 未消解)]
        搁 = [x for x in self.条目 if x.get("级别") in 阻塞 and x.get("状态") == 搁置]
        明细 = [f"{x['id']} {x['级别']} {x['状态']}：{x['问题'][:60]}" for x in 阻] + \
               [f"{x['id']} {x['级别']} 搁置（{x.get('搁置理由','')[:40]}）：{x['问题'][:60]}" for x in 搁]
        return (not 阻), 明细

    # ---------------------------------------------------------------- 给腿看的文本
    def 渲染给修改腿(self, 条目们=None, 回执文件="审稿/回执.json"):
        条目们 = 条目们 if 条目们 is not None else self.待改条目()
        行 = [f"以下 {len(条目们)} 条是本轮要你处理的意见（编号是身份，回执必须按编号写）："]
        for x in 条目们:
            行.append(f"[{x['id']}] 级别={x['级别']} 目标={x['目标']} 定位={x['定位'] or '（未指明，先定位再改）'}\n"
                      f"    问题：{x['问题']}\n    指令：{x['指令'] or '（无具体指令：按问题描述自行判断最小修改）'}"
                      + (f"\n    验收：{x['验收']}" if x.get("验收") else "")
                      + _再修提示(x))
        行.append(f"处理完写回执 {回执文件}（合法 JSON 数组）：[{{\"id\": \"编号\", \"改动\": \"改了哪一处、怎么改的\", \"证据\": \"文件:行 或 新数值/新键名\"}}]。"
                  f"只写你真的动过的编号；改不了的也要写，`改动` 填「未改」并说明原因。**不许改台账文件本身。**")
        return "\n".join(行)

    def 渲染给评审腿(self, 裁定文件="审稿/裁定.json"):
        复 = [x for x in self.条目 if x.get("状态") == 待复核]
        未 = [x for x in self.条目 if x.get("状态") in (待改, 未消解)]
        行 = []
        if 复:
            行.append(f"上一轮有 {len(复)} 条意见已由修改腿处理，你必须逐条裁定是否真的消解（看改后的实物，不看回执的说法）：")
            for x in 复:
                回 = (x.get("回执") or [{}])[-1]
                行.append(f"[{x['id']}] {x['级别']}｜{x['定位']}｜问题：{x['问题'][:200]}\n    修改腿回执：{回.get('改动','')[:200]}｜证据：{回.get('证据','')[:120]}")
        if 未:
            行.append(f"另有 {len(未)} 条尚未处理的意见（编号如下），你若再次发现同一毛病，**引用这些编号**（在新意见里写 \"对应\": 编号），不要换措辞当新问题：")
            for x in 未:
                行.append(f"[{x['id']}] {x['级别']}｜{x['定位']}｜{x['问题'][:120]}")
        行.append(f"裁定写 {裁定文件}（合法 JSON）：{{\"逐项\": [{{\"id\": \"编号\", \"裁定\": \"已消解|未消解\", \"理由\": \"看到了什么\"}}], "
                  f"\"相对判断\": \"更好|持平|更差\", \"决定性理由\": \"相对上一版本，最关键的一处变化\"}}。"
                  f"相对判断只比较上一版本与这一版本，不看绝对分。")
        return "\n".join(行)


# ==================================================================== 定向修改的边界
def 变化比例(旧文本, 新文本):
    """按行比较的变化比例：0 = 一字未动，1 = 面目全非。"""
    a = str(旧文本 or "").splitlines()
    b = str(新文本 or "").splitlines()
    if not a and not b:
        return 0.0
    return 1.0 - difflib.SequenceMatcher(None, a, b).ratio()


def 变化守卫(旧文本, 新文本, 上限=0.4):
    """定向修改的机械边界：改动比例超过上限就是「重写」，不是「修订」。返回 (通过, 比例)。"""
    r = 变化比例(旧文本, 新文本)
    return r <= 上限, r


# ---- 病根台账 R36（20260910 对照跑 S4 章评轮1：6 章修订回退 5 章）----
# 上面的 变化比例 按「行」比对。可 tex 一段就是一行：1.1.问题重述.tex 18 行里正文只有 4 行、
# 9 行是 `% src` 注释——评审点名 9 处、4 段各改一句 + 按要求重排 src 路径，行比例就是 61%，
# 定向修订被机械判成「重写」整份回退，且台账把这 44 条记成「修过一次仍未消解」。
# 度量必须对上协议本身：「只改点名处，其他一字不动」——
#   ① 单位是正文句子，不是行；`%` 注释行（src 指针、评审要求重排格式的东西）不计；
#   ② 评审在意见里引号引出的原文片段就是「点名处」，点名句子的改动不计；
#   ③ 另算一条字符级比例作第二把尺：术语统一/拆长句这类全稿语言动作句句都碰、字却动得少。
#   两把尺都说超线才是重写；任一把尺说没超就放行。
_引号片段 = re.compile(r"[“\"「『‘]([^”\"」』’]{4,120})[”\"」』’]")
_范围指法 = re.compile(r"从\s*[“\"「『‘]([^”\"」』’]{4,120})[”\"」』’]\s*(?:到|至|直到)\s*[“\"「『‘]([^”\"」』’]{4,120})[”\"」』’]")
# 引号片段后面紧跟的「段」字眼 ⇒ 评审点的是整段而不是这一句；两段/三段 ⇒ 连同其后的段
_段落指法 = re.compile(r"[”\"」』’]\s*(?:起头|开头|所在|整段|这段|这一段|那段|该段|一段|两段|三段|段落|段)")
# 意见里出现这些动词 ⇒ 结构性操作，点到的句子所在整段都算点名（压缩/合并/前移的段句序与句数必然变）
_结构动词 = re.compile(r"压缩|合并|移出|前移|后移|移到|移至|拆成|拆分|重排|删去整段|删除整段|重写整段|改写整段")


def 正文单位(文本, 带段号=False):
    """把 tex 正文切成句子级单位：去掉 `%` 开头的注释行，再按换行与中文句末标点切分。
    带段号=True 时返回 [(句, 段号)]，段号 = 该句所在的正文行序（tex 一段一行）。"""
    行 = [l for l in str(文本 or "").splitlines() if not l.strip().startswith("%")]
    出 = []
    段 = -1
    for l in 行:
        if not l.strip():
            continue
        段 += 1
        for x in re.split(r"(?<=[。！？；])", l):
            x = x.strip()
            if x:
                出.append((x, 段) if 带段号 else x)
    return 出


def _条目文本(x):
    if isinstance(x, dict):
        return " ".join(str(x.get(k) or "") for k in ("问题", "指令", "原句", "定位"))
    return str(x)


def 点名片段(条目们):
    """评审意见（问题/指令/原句）里引号引出的原文片段——修改腿被点名要动的地方。"""
    片 = set()
    for x in 条目们 or ():
        for m in _引号片段.finditer(_条目文本(x)):
            片.add(m.group(1).strip())
    return 片


def _被点名(单位, 片段们):
    """句子含某个点名片段（或片段含整句、或二者有 ≥10 字的公共子串——评审引文常有标点出入）。"""
    for 片 in 片段们:
        if 片 in 单位 or 单位 in 片:
            return True
        if len(片) >= 10 and len(单位) >= 10:
            m = difflib.SequenceMatcher(None, 片, 单位, autojunk=False).find_longest_match(0, len(片), 0, len(单位))
            if m.size >= 10:
                return True
    return False


def 点名掩码(旧单位, 条目们):
    """按评审的指法粒度标出旧文哪些句子被点名：
       句级——引号片段所在句；范围——「从“A”到“B”」之间全部句；段级——片段后紧跟「起头/所在/整段/两段…」
       或意见含结构动词（压缩/合并/前移…）时，片段所在整段（两段/三段 ⇒ 连同其后 1–2 段）。"""
    n = len(旧单位)
    点名 = [False] * n
    if not n:
        return 点名
    段号 = [d for _, d in 旧单位]
    句 = [u for u, _ in 旧单位]

    def 标段(i, 段数=1):
        起 = 段号[i]
        for k in range(n):
            if 起 <= 段号[k] < 起 + 段数:
                点名[k] = True

    for x in 条目们 or ():
        文 = _条目文本(x)
        结构 = bool(_结构动词.search(文))
        for m in _引号片段.finditer(文):
            片 = m.group(1).strip()
            命中 = [i for i in range(n) if _被点名(句[i], {片})]
            if not 命中:
                continue
            尾 = 文[m.end() - 1:m.end() + 8]
            if _段落指法.match(尾):            # “X”起头的两段 / “X”所在段 / “X”整段
                段数 = 3 if "三段" in 尾[:6] else (2 if "两段" in 尾[:6] else 1)
                for i in 命中:
                    标段(i, 段数)
            elif 结构:                          # 压缩/合并/前移…：点到的句子所在整段
                for i in 命中:
                    标段(i, 1)
            else:
                for i in 命中:
                    点名[i] = True
        for m in _范围指法.finditer(文):
            起点 = [i for i in range(n) if _被点名(句[i], {m.group(1).strip()})]
            终点 = [i for i in range(n) if _被点名(句[i], {m.group(2).strip()})]
            if 起点 and 终点 and min(起点) <= max(终点):
                for i in range(min(起点), max(终点) + 1):
                    点名[i] = True
    return 点名


def _存活(旧句, 新句们, 阈=0.9):
    """旧句是否在新文里近乎原样存在（不看位置——评审要求前移/后移的段不该算丢失）。"""
    sm = difflib.SequenceMatcher(None, "", 旧句, autojunk=False)
    for 新 in 新句们:
        if abs(len(新) - len(旧句)) > 0.3 * max(len(旧句), 1):
            continue
        sm.set_seq1(新)
        if sm.real_quick_ratio() >= 阈 and sm.quick_ratio() >= 阈 and sm.ratio() >= 阈:
            return True
    return False


def 修订守卫(旧文本, 新文本, 条目们=(), 上限=0.45):
    """定向修改的机械边界（R36 版）。返回 (通过, 比例, 明细)。
    比例 = min(未点名句改动比, 字符丢失比)；明细供日志与复盘。
      未点名句改动比：旧文里没被评审点名的句子，有多大比例在新文里找不到近乎原样的对应（与位置无关）；
      字符丢失比：按序匹配块字数 / 旧文字数——删也是推倒（R2：评审喜欢的部分被推倒）。"""
    旧带段 = 正文单位(旧文本, 带段号=True)
    旧句 = [u for u, _ in 旧带段]
    新句 = 正文单位(新文本)
    片段们 = 点名片段(条目们)
    点名 = 点名掩码(旧带段, 条目们) if 片段们 else [False] * len(旧句)
    新集 = set(新句)
    改了 = [False if u in 新集 else not _存活(u, 新句) for u in 旧句]
    未点名 = [i for i in range(len(旧句)) if not 点名[i]]
    未点名比 = (sum(1 for i in 未点名 if 改了[i]) / len(未点名)) if 未点名 else 0.0
    句比 = (sum(改了) / len(旧句)) if 旧句 else 0.0
    旧正, 新正 = "\n".join(旧句), "\n".join(新句)
    if not 旧正:
        字比 = 0.0
    elif len(旧正) + len(新正) <= 60000:
        留 = sum(b.size for b in difflib.SequenceMatcher(None, 旧正, 新正, autojunk=False).get_matching_blocks())
        字比 = 1.0 - 留 / len(旧正)
    else:
        字比 = 句比
    比例 = min(未点名比, 字比)
    明细 = {"句比": round(句比, 3), "未点名比": round(未点名比, 3), "字比": round(字比, 3), "行比": round(变化比例(旧文本, 新文本), 3),
            "单位数": len(旧句), "点名单位": sum(点名), "新单位数": len(新句), "点名片段": len(片段们)}
    return 比例 <= 上限, 比例, 明细


# ==================================================================== 配对裁定
def 解析配对裁定(obj):
    """容错解析评审腿的裁定文件。返回 {"逐项": [...], "相对判断": 更好|持平|更差|None, "理由": str, "分数": float|None}。"""
    if isinstance(obj, list):
        obj = {"逐项": obj}
    if not isinstance(obj, dict):
        return {"逐项": [], "相对判断": None, "理由": "", "分数": None}
    逐 = obj.get("逐项") or obj.get("裁定") or []
    if isinstance(逐, dict):
        逐 = [{"id": k, "裁定": v} for k, v in 逐.items()]
    相 = str(obj.get("相对判断") or obj.get("相对") or "").strip()
    相 = "更好" if 相.startswith("更好") or 相.startswith("好") else ("更差" if 相.startswith("更差") or 相.startswith("差") else ("持平" if 相 else None))
    分 = None
    for k in ("分数", "总分", "美观分", "第一印象分"):
        try:
            分 = float(obj[k])
            break
        except Exception:
            continue
    return {"逐项": [x for x in 逐 if isinstance(x, dict)], "相对判断": 相,
            "理由": str(obj.get("决定性理由") or obj.get("理由") or "")[:300], "分数": 分}


# ==================================================================== 多通道裁定合并（R45）与受影响问识别（R46）
_弃权词 = re.compile(r"未核实|不能核实|无法核实|未能核[实验]|未获.{0,8}(实物|材料|范围)|不能证明|本通道|未提供|未展示|无法验证|回执代验|不能销号")


def 是弃权票(通道, 裁定, 理由):
    """只看前 8 页页图的评委模拟腿裁不了工程/全文项时会写「未核实…不能销号」：那是弃权，不是否决。
    只对 评委模拟* 通道生效——审稿员/硬伤猎手读全文，他们的未消解一律算数（R45）。"""
    return (str(通道).startswith("评委模拟") and not str(裁定).strip().startswith("已")
            and bool(_弃权词.search(str(理由 or ""))))


def 合并裁定(通道裁定们):
    """[(通道, 逐项列表)] → 每个 id 一条：任一实质未消解即未消解（弃权票不算），否则任一已消解即已消解；
    只有弃权票的 id 不裁（回 待复核未裁 路径）。返回 (逐项列表, 弃权票数)。"""
    逐, 弃 = {}, {}
    for 通道, 逐项 in 通道裁定们:
        for x in 逐项 or []:
            if not isinstance(x, dict):
                continue
            i = str(x.get("id", "")).strip()
            if not i:
                continue
            裁 = str(x.get("裁定", "")).strip()
            由 = f"{通道}：{x.get('理由', '')}"
            if 是弃权票(通道, 裁, x.get("理由")):
                弃.setdefault(i, []).append(由)
                continue
            旧 = 逐.get(i)
            if 旧 is None or not 裁.startswith("已"):
                逐[i] = {"id": i, "裁定": 裁, "理由": 由}
    for i, 由们 in 弃.items():
        if i in 逐:
            逐[i]["理由"] = (逐[i]["理由"] + "｜弃权：" + "；".join(由们))[:600]
    return list(逐.values()), sum(len(v) for v in 弃.values())


_中文数 = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六"}


def 条目涉及问(条目, 问表):
    """一条意见涉及哪些问：认「问题N」「问N」「问题一/二/三」以及章号「4.N.」「8.N.」（正文第 N 问章与附录源码章的文件名）。
    以前只认字面「问题N」，定位写成 4.2.3.求解与结果分析.tex 的算路意见派不到任何回炉算腿（R46）。"""
    s = json.dumps(条目, ensure_ascii=False)
    出 = set()
    for bh in 问表:
        try:
            n = int(bh)
        except Exception:
            n = None
        if (f"问题{bh}" in s or f"问{bh}" in s
                or (n is not None and (f"问题{_中文数.get(n, '')}" in s or re.search(rf"(?<![0-9.])[48]\.{n}\.", s)))):
            出.add(bh)
    return 出


def 是代码章(文件名, 文本):
    """附录源码章（8.N.问题N源码.tex 之类）随求解器换版整份替换是正常修订，不是重写：
    文件名含「源码/代码」，或用 lstinputlisting/inputminted 引入代码，或半数以上行落在 lstlisting/verbatim/minted 环境内，就不设变化守卫（R47）。"""
    if re.search(r"源码|代码", str(文件名)):
        return True
    文本 = str(文本 or "")
    行们 = 文本.splitlines()
    if not 行们:
        return False
    if re.search(r"\\lstinputlisting|\\inputminted", 文本):
        return True
    内, 在 = 0, False
    for l in 行们:
        if re.search(r"\\begin\{(lstlisting|verbatim|minted)", l):
            在 = True
        if 在:
            内 += 1
        if re.search(r"\\end\{(lstlisting|verbatim|minted)", l):
            在 = False
    return 内 >= 0.5 * len(行们)


# ==================================================================== 最优保留
class 最优保留:
    """每轮快照 + 回退决定。快照是 {相对路径: bytes}，由驱动从本地镜像取。"""

    def __init__(self):
        self.快照们 = {}

    def 记(self, 轮次, 文件字典):
        self.快照们[int(轮次)] = dict(文件字典)

    def 判定(self, 相对判断, 旧分=None, 新分=None, 噪声=0.5):
        """接受还是回退：
          更差 → 回退；更好 → 接受；
          持平/无相对判断 → 看分数：新分跌破 旧分-噪声 才回退（±噪声内的波动不算退步）。"""
        if 相对判断 == "更差":
            return "回退", "评审相对判断：更差"
        if 相对判断 == "更好":
            return "接受", "评审相对判断：更好"
        if 旧分 is not None and 新分 is not None and 新分 < 旧分 - 噪声:
            return "回退", f"分数 {旧分:.2f} → {新分:.2f} 跌破噪声带 {噪声}"
        return "接受", "持平或在噪声带内"

    def 回退包(self, 轮次):
        return dict(self.快照们.get(int(轮次), {}))


# ==================================================================== 熔断
def 熔断决定(条目):
    """两次修不掉的条目怎么办：硬伤/正确性 → 升格（换人换路重做）；叙述/版式 → 搁置留痕。"""
    return "升格" if 条目.get("级别") in 阻塞级别 else "搁置"


def 修订单条目(级别, 目标, 问题, 定位="", 指令="", 验收="", 来源="", 对应=None):
    d = {"级别": 级别, "目标": 目标, "问题": 问题, "定位": 定位, "指令": 指令, "验收": 验收, "来源": 来源}
    if 对应:
        d["对应"] = 对应
    return d
