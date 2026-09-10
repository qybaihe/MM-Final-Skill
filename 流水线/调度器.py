#!/usr/bin/env python3
"""调度器：状态机（断点续跑）、依赖 DAG（拓扑分层 / 级联重算）、门框架（一票否决 / 连败升格）。

本模块是**纯逻辑**：不含任何网络、沙箱、curl 调用，便于本地单测。
被 蜂群驾驶.py 引入使用。所有键名中文，与 施工方案 §4.10 契约一致。
"""
import json
import pathlib
import time


# ============================================================ 状态机
class 状态机:
    """维护 状态.json：节点级 / 问题级完成粒度，支持 --resume 跳过已完成节点。"""

    def __init__(self, 路径, 运行ID=None):
        self.路径 = pathlib.Path(路径)
        self.数据 = {
            "运行ID": 运行ID or time.strftime("%Y%m%d-%H%M%S"),
            "阶段": "",
            "已完成节点": [],
            "问题门状态": {},      # {"1": "PASS"/"FAIL"/"PENDING"}
            "腿数": 0,
            "起始时间": time.time(),
            "门失败计数": {},      # {"G2:问2": 1}
            "级联待重算": [],
            "降级放行": [],        # 记录被迫放行的门
            "事件": [],
        }

    # ---------- 持久化 ----------
    def 加载(self):
        """存在则载入并返回 True（续跑）；不存在返回 False（全新跑）。"""
        if not self.路径.exists():
            return False
        try:
            旧 = json.load(open(self.路径, encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(旧, dict) or not 旧.get("运行ID"):
            return False
        self.数据.update(旧)
        return True

    def 保存(self):
        self.路径.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.路径.with_suffix(".json.tmp")
        json.dump(self.数据, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        tmp.replace(self.路径)

    # ---------- 节点 ----------
    def 已完成(self, 节点):
        return 节点 in self.数据["已完成节点"]

    def 标记完成(self, 节点):
        if 节点 not in self.数据["已完成节点"]:
            self.数据["已完成节点"].append(节点)
        self.保存()

    def 取消完成(self, 节点):
        """级联重算时把节点打回未完成。"""
        if 节点 in self.数据["已完成节点"]:
            self.数据["已完成节点"].remove(节点)
        self.保存()

    def 设阶段(self, 阶段):
        self.数据["阶段"] = 阶段
        self.保存()

    # ---------- 问题门 ----------
    def 设问题门(self, 编号, 结果):
        self.数据["问题门状态"][str(编号)] = 结果
        self.保存()

    def 取问题门(self, 编号):
        return self.数据["问题门状态"].get(str(编号), "PENDING")

    # ---------- 门失败计数 ----------
    def 门失败(self, 门名):
        c = self.数据["门失败计数"].get(门名, 0) + 1
        self.数据["门失败计数"][门名] = c
        self.保存()
        return c

    def 重置门失败(self, 门名):
        if 门名 in self.数据["门失败计数"]:
            self.数据["门失败计数"][门名] = 0
        self.保存()

    def 记降级(self, 门名, 明细=""):
        self.数据["降级放行"].append({"门": 门名, "明细": str(明细)[:400], "时刻": time.strftime("%H:%M:%S")})
        self.保存()

    # ---------- 腿数 ----------
    def 加腿(self, n=1):
        self.数据["腿数"] = self.数据.get("腿数", 0) + n
        self.保存()

    # ---------- 级联 ----------
    def 加级联(self, 问题列表):
        for q in 问题列表:
            if q not in self.数据["级联待重算"]:
                self.数据["级联待重算"].append(q)
        self.保存()

    def 取级联(self):
        return list(self.数据["级联待重算"])

    def 清级联(self, 编号=None):
        if 编号 is None:
            self.数据["级联待重算"] = []
        elif 编号 in self.数据["级联待重算"]:
            self.数据["级联待重算"].remove(编号)
        self.保存()


# ============================================================ 依赖 DAG
def 构建依赖图(问题清单):
    """从 计划.json 的问题清单构建 {编号: [依赖编号]}。非法依赖（指向不存在的问）被丢弃。"""
    图 = {}
    for q in 问题清单:
        try:
            编号 = int(q.get("编号"))
        except Exception:
            continue
        图[编号] = []
    for q in 问题清单:
        try:
            编号 = int(q.get("编号"))
        except Exception:
            continue
        for d in (q.get("依赖问题") or []):
            try:
                d = int(d)
            except Exception:
                continue
            if d in 图 and d != 编号 and d not in 图[编号]:
                图[编号].append(d)
    return 图


def 检测环(图):
    """返回参与环的节点列表（空=无环）。自环也算。"""
    状态 = {}   # 0未访问 1在栈 2完成
    环 = set()

    def dfs(n, 栈):
        状态[n] = 1
        栈.append(n)
        for d in 图.get(n, []):
            if 状态.get(d, 0) == 1:
                # 找到回边，栈中从 d 起全部入环
                if d in 栈:
                    环.update(栈[栈.index(d):])
                else:
                    环.add(d)
            elif 状态.get(d, 0) == 0:
                dfs(d, 栈)
        栈.pop()
        状态[n] = 2

    for n in 图:
        if 状态.get(n, 0) == 0:
            dfs(n, [])
    return sorted(环)


def 拓扑分层(图):
    """返回 [[同层可并行的问题编号...], ...]。有环则抛 ValueError。"""
    环 = 检测环(图)
    if 环:
        raise ValueError(f"问题依赖图存在环：{环}")
    剩余 = {n: set(d for d in 图[n] if d in 图) for n in 图}
    层们 = []
    已完成 = set()
    while 剩余:
        本层 = sorted([n for n, d in 剩余.items() if not (d - 已完成)])
        if not 本层:
            raise ValueError(f"依赖无法推进（疑似环）：{sorted(剩余)}")
        层们.append(本层)
        已完成 |= set(本层)
        for n in 本层:
            del 剩余[n]
    return 层们


def 直接下游(图, 编号):
    return sorted([n for n, deps in 图.items() if 编号 in deps])


def 全部下游(图, 编号):
    """传递闭包下游（不含自身）。"""
    结果, 待 = set(), [编号]
    while 待:
        cur = 待.pop()
        for n in 直接下游(图, cur):
            if n not in 结果:
                结果.add(n)
                待.append(n)
    return sorted(结果)


def 上游(图, 编号):
    return sorted(图.get(编号, []))


# ============================================================ 门框架
class 门:
    """一票否决门：检查→返工→再检查；连败 最多返工 次→升格蜂群；升格后仍不过→降级放行并高亮。

    检查fn() -> (通过bool, 明细)
    返工fn(明细) -> None
    升格fn(明细) -> None（可为 None，则跳过升格直接降级放行）
    """

    def __init__(self, 名称, 检查fn, 返工fn=None, 升格fn=None, 最多返工=2, 状态=None, log=print):
        self.名称 = 名称
        self.检查fn = 检查fn
        self.返工fn = 返工fn
        self.升格fn = 升格fn
        self.最多返工 = 最多返工
        self.状态 = 状态
        self.log = log

    def 执行(self):
        """返回 (放行bool, 是否降级放行bool)。"""
        明细 = None
        for 轮 in range(1, self.最多返工 + 2):
            try:
                通过, 明细 = self.检查fn()
            except Exception as e:
                通过, 明细 = False, f"门检异常:{e}"
            if 通过:
                self.log(f"门[{self.名称}] 第{轮}次检查 PASS")
                if self.状态:
                    self.状态.重置门失败(self.名称)
                return True, False
            计数 = self.状态.门失败(self.名称) if self.状态 else 轮
            self.log(f"门[{self.名称}] 第{轮}次检查 FAIL（累计{计数}）：{str(明细)[:300]}")
            if 轮 <= self.最多返工 and self.返工fn:
                self.log(f"门[{self.名称}] 派返工")
                try:
                    self.返工fn(明细)
                except Exception as e:
                    self.log(f"门[{self.名称}] 返工异常：{e}")
            elif 轮 <= self.最多返工:
                break   # 无返工手段，不必空转
        # 连败 → 升格蜂群
        if self.升格fn:
            self.log(f"!! 门[{self.名称}] 连败{self.最多返工}次 → 升格蜂群重做")
            try:
                self.升格fn(明细)
                通过, 明细 = self.检查fn()
                if 通过:
                    self.log(f"门[{self.名称}] 升格后 PASS")
                    if self.状态:
                        self.状态.重置门失败(self.名称)
                    return True, False
            except Exception as e:
                self.log(f"门[{self.名称}] 升格异常：{e}")
        self.log(f"!!!! 门[{self.名称}] {'升格后仍不过' if self.升格fn else '只复检不返工不升格'} → 降级放行（高亮记录，不阻塞流水线）：{str(明细)[:300]}")
        if self.状态:
            self.状态.记降级(self.名称, 明细)
        return False, True


# ============================================================ 修订单合并（S5）
_级别序 = {"硬伤": 0, "正确性": 1, "叙述": 2, "版式": 3}
_目标序 = {"算": 0, "图": 1, "文": 2}


def 合并修订单(条目们):
    """按 级别（硬伤>正确性>叙述>版式）、同级内 目标（先算后图后文）排序并编号。"""
    清 = []
    for it in 条目们:
        if not isinstance(it, dict):
            continue
        级别 = str(it.get("级别", "叙述")).strip() or "叙述"
        if 级别 not in _级别序:
            级别 = "硬伤" if "硬" in 级别 else ("正确性" if "正确" in 级别 else ("版式" if ("版" in 级别 or "排版" in 级别) else "叙述"))
        目标 = str(it.get("目标", "文")).strip() or "文"
        if 目标 not in _目标序:
            目标 = "算" if ("算" in 目标 or "计算" in 目标) else ("图" if "图" in 目标 else "文")
        清.append({**it, "级别": 级别, "目标": 目标})
    清.sort(key=lambda x: (_级别序[x["级别"]], _目标序[x["目标"]]))
    for i, it in enumerate(清, 1):
        it["序号"] = i
    return 清


def 按目标分组(修订单):
    """→ {"算": [...], "图": [...], "文": [...]}，保持排序。"""
    组 = {"算": [], "图": [], "文": []}
    for it in 修订单:
        组.setdefault(it.get("目标", "文"), []).append(it)
    return 组


if __name__ == "__main__":
    # ---------- 自测 ----------
    图 = 构建依赖图([{"编号": 1, "依赖问题": []}, {"编号": 2, "依赖问题": [1]}, {"编号": 3, "依赖问题": []}])
    assert 图 == {1: [], 2: [1], 3: []}, 图
    assert 拓扑分层(图) == [[1, 3], [2]], 拓扑分层(图)
    assert 全部下游(图, 1) == [2] and 全部下游(图, 3) == []
    环图 = 构建依赖图([{"编号": 1, "依赖问题": [2]}, {"编号": 2, "依赖问题": [1]}])
    assert 检测环(环图), "应检出环"
    自环 = 构建依赖图([{"编号": 1, "依赖问题": [1]}])
    assert 自环 == {1: []}, "自环应被丢弃"
    链 = 构建依赖图([{"编号": 1, "依赖问题": []}, {"编号": 2, "依赖问题": [1]}, {"编号": 3, "依赖问题": [2]}])
    assert 拓扑分层(链) == [[1], [2], [3]] and 全部下游(链, 1) == [2, 3]
    m = 合并修订单([{"级别": "版式", "目标": "文", "问题": "a"}, {"级别": "硬伤", "目标": "文", "问题": "b"},
                    {"级别": "硬伤", "目标": "算", "问题": "c"}])
    assert [x["问题"] for x in m] == ["c", "b", "a"], m
    # 门：恒 FAIL → 升格 → 降级放行
    记 = []
    g = 门("测试门", lambda: (False, "坏"), 返工fn=lambda d: 记.append("返工"),
           升格fn=lambda d: 记.append("升格"), 最多返工=2, log=lambda s: 记.append(s))
    放行, 降级 = g.执行()
    assert not 放行 and 降级 and 记.count("返工") == 2 and 记.count("升格") == 1, 记
    # 门：第二次通过
    状态盒 = {"n": 0}

    def 检查():
        状态盒["n"] += 1
        return (状态盒["n"] >= 2, "ok" if 状态盒["n"] >= 2 else "未好")
    g2 = 门("测试门2", 检查, 返工fn=lambda d: None, log=lambda s: None)
    assert g2.执行() == (True, False)
    # 状态机：落盘→重载→续跑语义
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "状态.json"
        s1 = 状态机(p, 运行ID="测试-1")
        assert s1.加载() is False, "首次应无状态"
        s1.设阶段("S2")
        s1.标记完成("S0")
        s1.标记完成("S2:问1")
        s1.设问题门(1, "PASS")
        s1.加腿(5)
        assert s1.门失败("G2:问2") == 1 and s1.门失败("G2:问2") == 2
        s1.加级联([2, 3])
        s2 = 状态机(p)
        assert s2.加载() is True, "应能续跑"
        assert s2.数据["运行ID"] == "测试-1" and s2.数据["阶段"] == "S2"
        assert s2.已完成("S0") and s2.已完成("S2:问1") and not s2.已完成("S2:问2")
        assert s2.取问题门(1) == "PASS" and s2.取问题门(2) == "PENDING"
        assert s2.数据["腿数"] == 5 and s2.数据["门失败计数"]["G2:问2"] == 2
        assert s2.取级联() == [2, 3]
        s2.清级联(2)
        assert s2.取级联() == [3]
        s2.取消完成("S2:问1")
        assert not s2.已完成("S2:问1"), "级联应能打回节点"
        s2.记降级("G4", "编译仍有错")
        assert 状态机(p).加载() and len(json.load(open(p, encoding="utf-8"))["降级放行"]) == 1
    print("调度器自测全过")
