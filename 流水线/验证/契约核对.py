#!/usr/bin/env python3
"""契约核对：消费方读的每个字段，生产方都得声明过。纯本地，不依赖容器。

这条测试存在的理由，是一个实测事实：**光靠人记住是不够的。**
20260831 的交叉核对发现 9 处未背书字段读取，其中两处是我自己在前两轮
一边专门思考契约漂移、一边亲手制造的——`跳过原因` 加进了驱动没加进 红队.md，
`有效数字` 加进了 审计.py 却没有任何 schema。

历史上三次「判据与提示词打架」都是同一个病：
  · 仲裁台账（提示词「已解释」vs 门检「已消解」）
  · 红队缺失（驱动「不阻塞」vs 门检一票否决）
  · 答案门（schema 有 核心指标，门检从不读）
每次都被当成新问题修了一遍。本脚本把这个只能靠人记住的不变量，变成会失败的测试。

用法：python3 契约核对.py
"""
import ast
import pathlib
import re
import sys

线 = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(线 / "运行时"))
from 契约表 import 契约表, 查, 归一, 围栏, 代码, 无      # noqa: E402


# ============================================================ 生产方：声明了什么
def 围栏字段(md路径: str, 契约路径: str):
    """从角色 md 里取「紧跟该产物路径的 ```json 围栏」的键名。

    角色文件里的写法固定为：`交接/xxx.json` —— 说明（schema 严格照此…）：\n```json …```
    这些围栏是手工调优的、已经存在的生产方权威定义；本核对从这里解析，
    不另建一套 schema 语言，也不去生成提示词（那会毁掉手工调优的行文）。
    """
    p = 线 / md路径
    if not p.exists():
        return None
    t = p.read_text(encoding="utf-8")
    # 契约路径是归一过的（…问题N.json），角色文件里写的是 问题X；两边都放宽成通配
    模式 = re.escape(契约路径).replace(r"问题N", r"问题[NX\d]").replace(r"轮N", r"轮[NX\d]")
    # 路径与围栏之间允许说明文字加最多一个空行（角色文件里两种写法都有）
    m = re.search(r"`" + 模式 + r"`[^\n]{0,160}\n{0,2}[ \t]*```json\n(.*?)```", t, re.S)
    if not m:
        return None
    return set(re.findall(r'"([^"]+)"\s*:', m.group(1)))


def 代码字段(登记: dict):
    """代码产出的契约：schema **就是那段写入代码**，从 AST 里解析出来。

    刻意不要求产出脚本额外维护一份 schema 常量——那正是契约表开头点名的
    「第二事实来源」。这里认两种写法：
        report["禁用词"] = {"数量": …, "明细": …}     顶层键 + 嵌套字面量键
        矩阵.append({"需求号": …, "落位": {"章节": …}})  追加式构造
    """
    文件 = 线 / 登记["生产"]
    变量 = 登记.get("写入")
    if not 文件.exists() or not 变量:
        return None
    树 = ast.parse(文件.read_text(encoding="utf-8"))
    键 = set()

    def 收字面(o):
        """递归收 dict 字面量的键（嵌套结构两侧口径要一致——门检也是嵌套着读的）。"""
        if isinstance(o, ast.Dict):
            for k, v in zip(o.keys, o.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    键.add(k.value)
                收字面(v)
        elif isinstance(o, (ast.List, ast.Tuple, ast.Set)):
            for e in o.elts:
                收字面(e)

    中间 = set()          # report["X"] = {... 明细: 过精[:30]} 里引用到的本地列表名

    for n in ast.walk(树):
        # report["K"] = <expr> ；以及 声明 = {整份字面量}
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == 变量:
                    收字面(n.value)        # 整份 dict 一次性构造的写法
                if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                        and t.value.id == 变量 and isinstance(t.slice, ast.Constant)
                        and isinstance(t.slice.value, str)):
                    键.add(t.slice.value)
                    收字面(n.value)
                    # 明细类字段常经本地列表中转：过精.append({"数字":…}) → report["有效数字"]["明细"]。
                    # 不追这一层，就会把真产出的 `数字` 误报成未声明。
                    for sub in ast.walk(n.value):
                        if isinstance(sub, ast.Name):
                            中间.add(sub.id)
        # 矩阵.append({...})
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "append" and isinstance(n.func.value, ast.Name)
              and n.func.value.id == 变量):
            for a in n.args:
                收字面(a)

    # 第二遍：把中转变量的 append 字面量、以及整份 dict 赋值（摘要画像 = {"文件": …}）也收进来。
    # 审计第 12 节把摘要画像先构造成本地 dict 再塞进 report["表达画像"]，门检嵌套着读 摘.get("超线")——
    # 只认 append 会把这层真产出误报成未声明（M5-1 实测）。
    for n in ast.walk(树):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "append" and isinstance(n.func.value, ast.Name)
                and n.func.value.id in 中间):
            for a in n.args:
                收字面(a)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id in 中间:
                    收字面(n.value)
                    for sub in ast.walk(n.value):       # 再追一层中转（记录报告 = {...: 明细}）
                        if isinstance(sub, ast.Name) and sub.id not in 中间:
                            中间.add(sub.id)
    return 键 or None


def 生产方字段(契约路径: str, 表=None):
    登记 = (表 if 表 is not None else 契约表).get(契约路径)
    if not 登记:
        return None, "未登记"
    if 登记["声明"] == 无:
        return None, "登记为『无声明』"
    got = 围栏字段(登记["生产"], 契约路径) if 登记["声明"] == 围栏 else 代码字段(登记)
    if got is None:
        return None, f"登记为{登记['声明']}但没解析到（{登记['生产']}）"
    # 双生产方取并集：谁写的字段谁声明，不必挤进同一份 schema
    另 = 登记.get("另产")
    if 另:
        更 = 围栏字段(另["生产"], 契约路径) if 另["声明"] == 围栏 else 代码字段(另)
        if 更 is None:
            return None, f"另产方 {另['生产']} 没解析到"
        got = got | 更
    return got, ""


# ============================================================ 消费方：读了什么
def 取字面(node):
    """常量或 f-string 取值；f-string 的占位统一成 X，再交给 归一()。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        s = ""
        for v in node.values:
            s += str(v.value) if isinstance(v, ast.Constant) else "X"
        return s
    return None


def 消费方字段(py路径: pathlib.Path):
    """AST 提取「变量绑定到哪个契约」以及「在它上面读了哪些字段」。

    覆盖四类真实模式（前三类是 20260831 首版探测器漏掉的）：
      A  v = 读json(路径)                          直接绑定
      B  for k in ("a","b"): if k not in v          顶层键检查（门检 G0/G2 用它检 12 个字段）
      C  x = v.get("K") / for x in v.get("K")       派生变量，字段读在派生对象上
      D  for p in glob.glob(模式): 读json(p)        glob 到的实例文件
    绑定跑到不动点，以处理 C 的多级派生。
    """
    模块 = ast.parse(py路径.read_text(encoding="utf-8"))
    # **按函数作用域分别分析**：门检把 G0/G2/G3/G4/G5 写成同级函数，`项`/`q`/`x`
    # 这类循环变量在多个门里复用。模块级统一绑定会让字段跨契约串味——首版就把
    # 题面契约的 需求号/内容/评分点推测 误报成 假设台账 的未声明字段。
    # 会哭狼的核对器比没有更糟：它会逼人去补一份假 schema。
    读取 = {}

    def 记读(契约, 字段):
        if 契约 and 字段:
            读取.setdefault(契约, set()).add(字段)

    作用域 = [n for n in ast.walk(模块) if isinstance(n, ast.FunctionDef)]
    for 树 in 作用域:
        _扫一个作用域(树, 记读)
    return 读取


def _扫一个作用域(fn, 记读):
    """按**源码顺序**推进绑定，重绑定即失效（流敏感到足以处理变量名复用）。

    为什么不用不动点：不动点「一旦绑定永不失效」，而门检 G2 里 `x` 先绑到
    仲裁的 逐项 条目、随后又 `for x in 未决` 绑到本地构造的字典。不动点会把
    本地键 `有证据` 误报成仲裁契约的未声明字段——而**补一个假字段进 schema
    正是这条测试要防的事**，所以宁可让分析更笨也不能让它更吵。

    识别四类模式：
      A  v = 读json(路径)                     直接绑定
      B  for k in ("a","b"): if k not in v     顶层键检查
      C  x = v.get("K") / for x in v.get("K")  派生变量
      D  for p in glob.glob(模式): 读json(p)   glob 到的实例文件
    """
    绑定 = {}

    def 解析契约(node, 局部=None):
        """这个表达式求出来的东西属于哪个契约？不属于任何契约则返回 None。

        局部 是推导式自带作用域的覆盖层：`[x[...] for x in 未决]` 里的 x
        与外层同名的契约变量无关，必须先查局部。
        """
        while isinstance(node, ast.BoolOp):        # (v or {}) / (v or [])
            node = node.values[0]
        if isinstance(node, ast.Name):
            if 局部 is not None and node.id in 局部:
                return 局部[node.id]
            return 绑定.get(node.id)
        if isinstance(node, ast.Subscript):        # 硬们[-1]
            return 解析契约(node.value, 局部)
        if isinstance(node, ast.Call):
            f = node.func
            # A: 读json(路径)
            if isinstance(f, ast.Name) and f.id == "读json" and node.args:
                路 = 取字面(node.args[0])
                return 归一(路) if 路 else 解析契约(node.args[0], 局部)
            if isinstance(f, ast.Attribute):
                # D: glob.glob(模式) / sorted(glob.glob(...))
                if f.attr == "glob" and node.args:
                    路 = 取字面(node.args[0])
                    return 归一(路) if 路 else None
                # C: v.get("K")
                if f.attr == "get":
                    return 解析契约(f.value, 局部)
            if isinstance(f, ast.Name) and f.id == "sorted" and node.args:
                return 解析契约(node.args[0], 局部)
        return None

    def 设(名, 契约):
        """绑定或**解绑**——解绑是关键：名字被重新赋成非契约值后不该再算契约读。"""
        if not 名:
            return
        if 契约:
            绑定[名] = 契约
        else:
            绑定.pop(名, None)

    推导 = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

    def 收读(node, 局部=None):
        """在当前绑定下采集契约字段读；推导式按自带作用域单独处理。

        必须显式递归而非 ast.walk：`缺证 = [x["指标"] for x in 未决 …]` 里的
        x 是推导式自己的变量，而外层同名 x 恰好绑在仲裁契约上。平铺遍历会把
        本地键 `有证据` 记成仲裁的未声明字段——**补一个假字段进 schema 正是
        这条测试要防的事**。
        """
        if isinstance(node, 推导):
            局 = dict(局部 or {})
            for g in node.generators:
                收读(g.iter, 局)
                源 = 解析契约(g.iter, 局)
                if isinstance(g.target, ast.Name):
                    if 源:
                        局[g.target.id] = 源
                    else:
                        局[g.target.id] = None      # 显式遮蔽外层同名契约变量
                for c in g.ifs:
                    收读(c, 局)
            for 子 in ([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]):
                收读(子, 局)
            return
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args):
            c = 解析契约(node.func.value, 局部)
            if c:
                记读(c, 取字面(node.args[0]))
        elif (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
              and isinstance(node.slice.value, str)):
            c = 解析契约(node.value, 局部)
            if c:
                记读(c, node.slice.value)
        for 子 in ast.iter_child_nodes(node):
            收读(子, 局部)

    def 处理块(块):
        for st in 块:
            处理语句(st)

    def 处理语句(st):
        if isinstance(st, ast.Assign):
            收读(st.value)                       # 先按赋值前的绑定采集
            契约 = 解析契约(st.value)
            for t in st.targets:
                if isinstance(t, ast.Name):
                    设(t.id, 契约)               # 再更新（可能是解绑）
            return
        if isinstance(st, (ast.For, ast.AsyncFor)):
            收读(st.iter)
            # B: for k in ("a","b",…) 且循环体里 k 与某契约变量做成员判断
            if isinstance(st.target, ast.Name) and isinstance(st.iter, (ast.Tuple, ast.List)):
                键们 = [e.value for e in st.iter.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                名 = st.target.id
                if 键们:
                    for sub in ast.walk(st):
                        契 = None
                        # B1: if k not in <契约变量>
                        if (isinstance(sub, ast.Compare) and isinstance(sub.left, ast.Name)
                                and sub.left.id == 名):
                            for c in sub.comparators:
                                契 = 契 or 解析契约(c)
                        # B2: <契约变量>.get(k) —— 拿循环变量当键。门检 G2 检查假设台账
                        # 的 假设/依据/灵敏度义务/检验结果 就是这么写的；只认字面量键
                        # 会把这一整组字段静默漏掉（漏报比误报安全，但仍是盲区）。
                        elif (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                              and sub.func.attr == "get" and sub.args
                              and isinstance(sub.args[0], ast.Name) and sub.args[0].id == 名):
                            契 = 解析契约(sub.func.value)
                        # B3: <契约变量>[k]
                        elif (isinstance(sub, ast.Subscript) and isinstance(sub.slice, ast.Name)
                              and sub.slice.id == 名):
                            契 = 解析契约(sub.value)
                        if 契:
                            for k in 键们:
                                记读(契, k)
            源契约 = 解析契约(st.iter)
            if isinstance(st.target, ast.Name):
                设(st.target.id, 源契约)         # 非契约来源 → 解绑
            处理块(st.body); 处理块(st.orelse)
            return
        if isinstance(st, (ast.If, ast.While)):
            收读(st.test); 处理块(st.body); 处理块(st.orelse); return
        if isinstance(st, ast.Try):
            处理块(st.body)
            for h in st.handlers:
                处理块(h.body)
            处理块(st.orelse); 处理块(st.finalbody); return
        if isinstance(st, (ast.With, ast.AsyncWith)):
            处理块(st.body); return
        if isinstance(st, ast.FunctionDef):
            return                               # 内嵌函数由外层的作用域列表单独扫
        收读(st)

    处理块(fn.body)


# ============================================================ 对账
def 核对(门检路径=None, 表=None):
    """返回 (逐契约结果, 未登记列表)。抽成函数是为了让单测能喂夹具进来。

    **本核对只覆盖字段级漂移**（消费方读了生产方没声明的键）。这是今后最常见的
    失效模式——20260831 探测到的两处正是这样产生的，而且都是我一边专门思考契约
    漂移一边亲手制造的。但历史上那三次事故都**不是**字段级的：
        · 仲裁台账——提示词写「已解释」、门检只认「已消解」，是**值域**不一致；
        · 红队缺失——驱动「不阻塞」、门检一票否决，是**行为**不一致；
        · 答案门——schema 有 `核心指标` 而门检从不读，是**反向**（声明了没人读）。
    反向那一类下面用 `未读` 提示（不判失败，因为契约本就可以有非门检消费方）；
    值域与行为两类**尚未覆盖**，是明确的后续项，别把本测试通过误当成契约无漂移。
    """
    表 = 表 if 表 is not None else 契约表
    读取 = 消费方字段(pathlib.Path(门检路径) if 门检路径 else 线 / "运行时/门检.py")
    结果, 未登记 = [], []
    for 契约 in sorted(读取):
        字段 = 读取[契约]
        登记 = 表.get(契约)
        if not 登记:
            未登记.append(契约)
            结果.append({"契约": 契约, "态": "未登记", "缺": sorted(字段)})
            continue
        声明, 因 = 生产方字段(契约, 表)
        if 声明 is None:
            结果.append({"契约": 契约, "态": "无声明", "因": 因, "缺": sorted(字段)})
            continue
        容错 = set((登记.get("容错") or {}).keys())
        结果.append({"契约": 契约, "态": "缺字段" if (字段 - 声明 - 容错) else "通过",
                     "缺": sorted(字段 - 声明 - 容错),
                     "容错命中": sorted(容错 & 字段),
                     "未读": sorted(声明 - 字段),
                     "读": len(字段), "声明": len(声明), "生产": 登记["生产"]})
    return 结果, 未登记


def 主():
    结果, 未登记 = 核对()
    print("=" * 78)
    print("契约核对：消费方(门检.py) 读的字段，生产方是否都声明过")
    print("=" * 78)
    print(f"登记契约 {len(契约表)} 种；门检实际消费 {len(结果)} 种\n")
    坏 = []
    for r in 结果:
        if r["态"] == "通过":
            if r["容错命中"]:
                print(f"  · {r['契约']} 消费方容错键（读侧宽容，生产方不应输出）：{r['容错命中']}")
            print(f"✓ {r['契约']}  读 {r['读']}/声明 {r['声明']} 字段  ← {r['生产']}")
        else:
            坏.append(r)
            print(f"✗ {r['契约']}")
            print(f"    {r.get('因', r['态'])}：{r['缺']}")
    print("\n" + "-" * 78)
    if 坏:
        print(f"✗ {len(坏)} 个契约存在未背书的字段读取：")
        for r in 坏:
            print(f"    {r['契约']:32s} {r.get('因', r['态']):16s} {r['缺']}")
    else:
        print("✓ 消费方读取的每个字段都有生产方声明背书")
    if 未登记:
        print(f"!! 门检消费但未登记进契约表：{未登记}")
    # 反向提示：声明了但门检不读。答案门事故就是这个形状（schema 有 核心指标、
    # 门检从不读），所以值得看见——但不判失败：契约本就可以有非门检的消费方。
    未读 = [(r["契约"], r["未读"]) for r in 结果 if r["态"] == "通过" and r["未读"]]
    if 未读:
        print("\n提示 · 生产方声明但门检未读（可能是判据缺位，也可能另有消费方）：")
        for c, ks in 未读:
            print(f"    {c:32s} {ks[:8]}{' …' if len(ks) > 8 else ''}")
    print("=" * 78)
    return 1 if (坏 or 未登记) else 0


if __name__ == "__main__":
    sys.exit(主())
