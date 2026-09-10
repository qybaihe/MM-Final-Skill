#!/usr/bin/env python3
"""G2「答案产出」判据单测（M4-4.11 验收）——纯本地，不需要容器。

背景：20260827 轮三问全部以"厚度不发布/不联合/证据不足"收尾，论文交了白卷，
而 G2 全部放行——因为此前判据全是"过程合规"，没有一条度量"有没有做出来"。
`交接/结果声明_问题N.json` 是全流程唯一承载题面答案的契约文件，现已加为 G2 硬判据。

为什么不用 门检单测.py：那个要在真实容器里跑，令牌过期时完全阻塞。
本脚本用 AST 从**真实源码**抽出 G2 函数体，注入受控的 读json / glob / os，
在本地就能验证判据逻辑，容器不可用时也能守住回归。
用法：python3 答案门单测.py
"""
import ast
import pathlib
import sys

源 = (pathlib.Path(__file__).parent.parent / "运行时/门检.py").read_text(encoding="utf-8")
树 = ast.parse(源)
G2源 = next((ast.get_source_segment(源, n) for n in 树.body
             if isinstance(n, ast.FunctionDef) and n.name == "G2"), None)
assert G2源, "没能从源码里抽到 G2"


def 跑G2(声明, 台账检验="已检验，变化2.1%（结果:灵敏度_折射率）"):
    """注入一套"其余全部合规"的夹具，只让 结果声明 这一项变化。"""
    文件 = {
        "交接/红队_问题1.json": {"问题": 1, "复算方式": "独立", "复算指标": {"厚度均值": 10.51},
                                  "口径说明": {}, "结论": "对齐", "分歧明细": []},
        "交接/仲裁_问题1.json": None,
        "交接/假设台账_问题1.json": [{"假设号": "A1", "假设": "界面平行", "依据": "数据档案",
                                       "灵敏度义务": "±20%", "检验结果": 台账检验}],
        "求解/问题1/结果/核心.json": {"厚度均值": 10.52, "灵敏度_折射率": 0.021},
    }
    if 声明 is not None:
        文件["交接/结果声明_问题1.json"] = 声明

    明细, 统计 = [], {}

    def 假读json(路径, 默认=None):
        return 文件.get(路径, 默认)

    class 假glob:
        @staticmethod
        def glob(pat):
            if "结果/*" in pat:
                return ["求解/问题1/结果/核心.json"]
            if "解读_问题" in pat:
                return ["日志/解读_问题1_轮1.done"]
            return []

    class 假os:
        class path:
            @staticmethod
            def isfile(_):
                return True

            @staticmethod
            def basename(p):
                return p.rsplit("/", 1)[-1]

            @staticmethod
            def getmtime(_):
                return 1.0

    class 假re:
        @staticmethod
        def findall(*a, **k):
            return []

    class _假done:
        def readline(self):
            return "PASS 五项协议全过\n"   # 其余全部合规，只让 结果声明 变化

    def 假open(*a, **k):
        return _假done()

    ns = {"读json": 假读json, "glob": 假glob, "os": 假os, "re": 假re,
          "open": 假open,
          "明细": 明细, "统计": 统计, "sorted": sorted, "str": str,
          "isinstance": isinstance, "len": len, "any": any, "set": set,
          "dict": dict, "int": int, "float": float, "bool": bool}
    exec(G2源, ns)
    # G2 内部还会找解读 done 标记（用 glob），这里返回空 → 会多一条"未找到解读标记"，
    # 不影响本单测：我们只断言"答案类明细"是否出现。
    try:
        ns["G2"](1)
    except Exception as e:
        return [f"异常:{type(e).__name__}:{e}"], 统计
    return 明细, 统计


def 有答案类明细(明细):
    return any(("核心指标为空" in m) or ("结果声明缺失" in m) for m in 明细)


用例 = [
    ("有真实答案：核心指标含数值",
     {"问题": 1, "核心指标": {"厚度均值": 10.52}, "口径说明": {"厚度均值": "μm"}},
     False, "正常求解，不该被答案门拦"),
    ("★ 核心指标为空（三问皆'不发布'的真实失败模式）",
     {"问题": 1, "核心指标": {}, "口径说明": {}},
     True, "必须拦下——这正是 20260827 轮交白卷却过门的情形"),
    ("★ 结果声明整个缺失（问1/问2 的真实情况）",
     None,
     True, "文件不存在必须拦下"),
    ("核心指标只有字符串没有数值",
     {"问题": 1, "核心指标": {"厚度均值": "不发布"}, "口径说明": {}},
     True, "拿字符串占位不算答案"),
    ("核心指标是 bool（True 不是数值答案）",
     {"问题": 1, "核心指标": {"是否触发": True}, "口径说明": {}},
     True, "bool 是 int 的子类，必须显式排除"),
    ("多个指标，含至少一个数值",
     {"问题": 1, "核心指标": {"结论": "触发", "厚度均值": 7.76}, "口径说明": {}},
     False, "混合时只要有真实数值即可放行"),
    ("核心指标字段类型错误（写成 list）",
     {"问题": 1, "核心指标": [10.52], "口径说明": {}},
     True, "schema 不符应拦下"),
]

print("=" * 70)
print("G2「答案产出」判据单测（源码级抽取，本地运行，不依赖容器）")
print("=" * 70)
过 = 0
for 名, 声明, 期望拦下, 要点 in 用例:
    明细, 统计 = 跑G2(声明)
    异常 = [m for m in 明细 if m.startswith("异常:")]
    实际拦下 = 有答案类明细(明细)
    ok = (not 异常) and (实际拦下 == 期望拦下)
    过 += ok
    print(f"{'✓' if ok else '✗'} {名}")
    print(f"    期望拦下={期望拦下} 实际拦下={实际拦下} 核心指标数={统计.get('核心指标数', '—')}")
    if 异常:
        print(f"    !! {异常[0]}")
    答案明细 = [m for m in 明细 if ("核心指标" in m or "结果声明" in m)]
    if 答案明细:
        print(f"    明细：{答案明细[0][:78]}")
    print(f"    {要点}")

print("=" * 70)
print(f"{过}/{len(用例)} 通过")
sys.exit(0 if 过 == len(用例) else 1)
