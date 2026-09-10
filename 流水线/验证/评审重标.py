#!/usr/bin/env python3
"""评审重标（表达优化方案 v1 D3）：角色文件的语言维改成"范文对照"后，同一份稿子的分数会整体平移；
阈值（章评阈值 7.0 / 审稿达标 8.6）若不跟着平移，门会凭空变松或变紧。

做法：把 真题测试/成品_骨架版（上一轮真跑的成稿，旧锚点下章评均分约 6.3）复制到独立工作根 真题测试/重标镜像，
同步最新角色/运行时资产，跑真腿：章评师 ×3（三个求解/检验/评价章各一）+ 审稿员 ×3（全文，独立）。
offset = 新均分 − 6.3；建议阈值 = 旧阈值 + offset，截断到 [6.5, 9.0]。结果写 验证/重标结果.json，
**只给建议不改配置**——改 配置["章评阈值"]/["审稿达标"] 是维护者看过分布后的决定。

用法：python3 评审重标.py            （约 20-40 分钟，6 条真腿并行）
      python3 评审重标.py --只算     （不跑腿，只用现有 重标镜像/审稿/ 里的产物重算）
      python3 评审重标.py --补跑     （只补 done 缺失或 AUTO 失败的腿，不重建镜像）
"""
import json
import pathlib
import re
import shutil
import sys
import time

流水线 = pathlib.Path(__file__).resolve().parent.parent
项目根 = 流水线.parent
sys.path.insert(0, str(流水线))
from 本地蜂巢 import LocalHive  # noqa: E402

骨架版 = 项目根 / "真题测试/成品_骨架版"
镜像 = 项目根 / "真题测试/蜂巢镜像"
根 = 项目根 / "真题测试/重标镜像"
旧章评均分 = 6.3      # 20260831 轮章评轮1 实测均分（施工日志 M4-4），旧锚点
旧阈值 = {"章评阈值": 7.0, "审稿达标": 8.6}


def 准备():
    shutil.rmtree(根, ignore_errors=True)
    for d in ("论文", "求解"):
        if (骨架版 / d).exists():
            shutil.copytree(骨架版 / d, 根 / d)
    for d in ("交接",):
        if (镜像 / d).exists():
            shutil.copytree(镜像 / d, 根 / d)
    for d in ("审稿", "日志", "任务", "bin", "运行时", "角色", "数据", "题目"):
        (根 / d).mkdir(parents=True, exist_ok=True)
    h = LocalHive(root=根)
    同步资产(h)
    return h


def 同步资产(h):
    """把最新 bin/角色/运行时 资产同步进镜像并重跑审计。--补跑 也必须走这里：镜像是建镜像那一刻的快照，
    bin/role.sh 与 codex公共.sh 不换新，补跑的腿仍是 1200s 硬上限 + 只杀包装留孤儿的旧 with_timeout（R31）。"""
    files = [h.f_local("AGENTS.md", 流水线 / "AGENTS.md"),
             h.f_local("bin/role.sh", 流水线 / "运行时/role.sh"),
             h.f_local("bin/codex公共.sh", 流水线 / "运行时/codex公共.sh"),
             h.f_local("bin/审计.py", 流水线 / "运行时/审计.py"),
             h.f_local("bin/表达画像.py", 流水线 / "运行时/表达画像.py")]
    for p in (流水线 / "角色").glob("*.md"):
        files.append(h.f_local(f"角色/{p.name}", p))
    for n in ("禁用词.txt", "内部术语.txt", "优秀论文标准.md", "文献卡片库.md", "方法卡片库.md",
              "对冲词.txt", "缩写白名单.txt", "流程词.txt", "表达阈值.json", "范文卡片库.md", "表达锚点.md"):
        fp = 流水线 / "运行时" / n
        if fp.exists():
            files.append(h.f_local(f"运行时/{n}", fp))
        for p in sorted((流水线 / "运行时/范文/文本").glob("*.txt")):   # 真稿全文（10 篇研究生数模一等奖），评审腿按卡片指的篇号打开对照
            files.append(h.f_local(f"运行时/范文/文本/{p.name}", p))
    h.exec("python3 bin/审计.py > 日志/审计.out 2>&1; echo ok", files=files, timeout_s=300, quiet=True)


def 挂载章():
    主 = (根 / "论文/论文.tex").read_text(encoding="utf-8")
    章 = [f if f.endswith(".tex") else f + ".tex" for f in re.findall(r"\\input\{([^}]+)\}", re.sub(r"%.*", "", 主))]
    return [c for c in 章 if (根 / "论文" / c).exists()]


def 跑腿(h, 补跑=False):
    章 = 挂载章()
    候 = [c for c in 章 if any(k in c for k in ("模型的建立", "模型检验", "模型评价"))] or 章[1:4]
    候 = 候[:3]
    legs = []
    for k, c in enumerate(候, 1):
        名 = f"重标章评{k}"
        legs.append(("章评师.md", f"深审 论文/{c}。按【AI味/推理跳步/数字溯源】三项打分（1-10）并给可执行改单；语言维按角色文件用 运行时/范文卡片库.md 与 "
                     f"运行时/表达锚点.md 做范文对照（首轮相对锚点 6 分档）。对照 交接/论点脊柱.json、交接/实验记录.json、审稿/审计报告.json。"
                     f"输出 JSON 数组写 审稿/{名}.json：[{{\"章\":\"{c}\",\"分数\":x,\"AI味\":x,\"跳步\":x,\"溯源\":x,\"问题\":[...],\"修改指令\":[...],\"范文对照\":{{...}}}}]。"
                     f"完成写 日志/{名}.done", 名))
    for k in ("A", "B", "C"):
        名 = f"重标审稿{k}"
        legs.append(("审稿员.md", f"全文审稿·评委{k}（论文/ 全部 tex + 审稿/审计报告.json + 交接/ 材料）。独立评审，不看其他评委。"
                     f"语言自然度这一维按角色文件做范文对照（运行时/范文卡片库.md、运行时/表达锚点.md，首轮相对锚点 6 分档）。"
                     f"按角色规范产出 审稿/审稿意见_{名}.json。完成写 日志/{名}.done", 名))
    if 补跑:
        def 有效(n):
            p = 根 / "日志" / f"{n}.done"
            return p.exists() and not p.read_text(encoding="utf-8", errors="replace").startswith("AUTO")
        legs = [l for l in legs if not 有效(l[2])]
    for 角色, 任务, 名 in legs:
        h.exec(f"rm -f 日志/{名}.done", quiet=True)
        h.leg(角色, 任务, 名, sync=False, leg_timeout=2700)     # 全文审稿腿实测 >1200s（rc=142），上限放到 45 分钟
    print(f"已启动 {len(legs)} 条真腿：{[n for _, _, n in legs]}")
    t0 = time.time()
    while time.time() - t0 < 3600:
        time.sleep(30)
        完成 = [n for _, _, n in legs if (根 / "日志" / f"{n}.done").exists()]
        if len(完成) == len(legs):
            break
        print(f"  {int(time.time()-t0)}s 完成 {len(完成)}/{len(legs)}", flush=True)
    else:
        print("!! 超时：部分腿未完成，按已有产物计算")
    return [n for _, _, n in legs]


def 算():
    章分, 审分 = [], []
    for p in sorted((根 / "审稿").glob("重标章评*.json")):
        try:
            v = json.loads(p.read_text(encoding="utf-8"))
            v = v if isinstance(v, list) else v.get("章评", [v])
            for it in v:
                if isinstance(it, dict) and "分数" in it:
                    章分.append((p.name, float(it["分数"]), it.get("范文对照")))
        except Exception as e:
            print(f"!! {p.name} 解析失败：{e}")
    for p in sorted((根 / "审稿").glob("审稿意见_重标审稿*.json")):
        try:
            v = json.loads(p.read_text(encoding="utf-8"))
            总 = v.get("总分")
            if 总 is None and v.get("各维"):
                总 = sum(float(x.get("分数", 0)) for x in v["各维"]) / len(v["各维"])
            审分.append((p.name, float(总), v.get("印象分"), v.get("范文对照")))
        except Exception as e:
            print(f"!! {p.name} 解析失败：{e}")
    结果 = {"时间": time.strftime("%Y-%m-%d %H:%M"), "工作根": str(根), "旧章评均分": 旧章评均分, "旧阈值": 旧阈值,
            "章评": {"逐条": 章分, "均分": round(sum(s for _, s, _ in 章分) / len(章分), 2) if 章分 else None},
            "审稿": {"逐条": 审分, "均分": round(sum(s for _, s, _, _ in 审分) / len(审分), 2) if 审分 else None}}
    if 章分:
        off = 结果["章评"]["均分"] - 旧章评均分
        结果["章评"]["offset"] = round(off, 2)
        结果["建议"] = {"章评阈值": round(min(9.0, max(6.5, 旧阈值["章评阈值"] + off)), 1),
                       "审稿达标": round(min(9.0, max(6.5, 旧阈值["审稿达标"] + off)), 1),
                       "说明": "offset=新锚点下同一稿的章评均分−旧锚点均分 6.3；只是建议，改 配置 前先看逐条分布是否离散（同稿两次分差应 ≤0.5）"}
    (流水线 / "验证/重标结果.json").write_text(json.dumps(结果, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(结果, ensure_ascii=False, indent=1))
    return 结果


if __name__ == "__main__":
    if "--补跑" in sys.argv:            # 只补 done 缺失/AUTO 的腿，不重建镜像（保留已完成的章评产物）
        h = LocalHive(root=根)
        同步资产(h)
        跑腿(h, 补跑=True)
    elif "--只算" not in sys.argv:
        h = 准备()
        跑腿(h)
    算()
