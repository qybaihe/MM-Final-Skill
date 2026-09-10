#!/usr/bin/env python3
"""钩子样板：给 切换.sh --钩子 用（也可停净后手工跑）。停净之后、续跑之前改 状态.json / 台账 / 镜像稿件。

用法：python3 钩子样板.py --父目录 <父目录> [--演练]
        [--恢复 镜像相对路径=来源文件 ...] [--留底 标签]
        [--状态 键=JSON值 ...]
        [--并入 条目.json --轮次 N --来源 xx] [--台账 审稿台账|章评台账|美化台账]
例（R52 实战三步）：
  python3 钩子样板.py --父目录 真题测试 --演练 \\
    --恢复 论文/8.3.问题三源码.tex=真题测试/蜂巢镜像/日志/G5返工1_修订前/8.3.问题三源码.tex --留底 G5返工1_页数守卫 \\
    --状态 美化后页数=68 \\
    --并入 /path/条目.json --轮次 7 --来源 页数守卫
纪律：只能在 停跑.sh 退出码 0（驱动=0 codex=0 xelatex=0）之后跑；真跑前先 --演练；状态.json 会先备份。
条目 JSON 是数组，每条至少 级别(硬伤|正确性|叙述|版式)/目标(算|图|文)/问题/定位/指令/验收/来源；id 由台账分配（审-{轮次}-{序}）。
"""
import argparse
import inspect
import json
import os
import pathlib
import shutil
import sys
import time

项目根 = pathlib.Path(os.environ.get("SHUMO_PROJ") or pathlib.Path(__file__).resolve().parents[4])
if not (项目根 / "流水线/蜂群驾驶.py").exists():
    sys.exit(f"!! 找不到项目根（需含 流水线/蜂群驾驶.py）：{项目根}；请 export SHUMO_PROJ=<仓库>")
os.chdir(项目根)
sys.path.insert(0, str(项目根 / "流水线"))

ap = argparse.ArgumentParser(add_help=False)
ap.add_argument("--父目录", required=True)
ap.add_argument("--演练", action="store_true")
ap.add_argument("--恢复", action="append", default=[])
ap.add_argument("--留底", default=f"钩子{time.strftime('%m%d-%H%M')}")
ap.add_argument("--状态", action="append", default=[])
ap.add_argument("--并入")
ap.add_argument("--轮次", type=int, default=0)
ap.add_argument("--来源", default="钩子")
ap.add_argument("--台账", default="审稿台账")
ap.add_argument("-h", "--help", action="store_true")
a = ap.parse_args()
if a.help:
    print(__doc__); sys.exit(0)
父 = pathlib.Path(a.父目录); 镜像 = 父 / "蜂巢镜像"; 状态文件 = 父 / "状态.json"
演 = "[演练] " if a.演练 else ""
if not 父.is_dir():
    sys.exit(f"!! 父目录不存在：{父}")

# ---- 0 安全闸：驱动/腿必须已停净 ----
import subprocess
活 = subprocess.run(["pgrep", "-f", "蜂群驾驶.py"], capture_output=True, text=True).stdout.split()
if 活 and not a.演练:
    sys.exit(f"!! 驱动还在跑（pid {活}），钩子拒绝执行：先 bash 停跑.sh 停净（铁律 4）")

# ---- 1 恢复稿件（先留底当前版本） ----
for 项 in a.恢复:
    if "=" not in 项:
        sys.exit(f"!! --恢复 需要 镜像相对路径=来源文件：{项}")
    rel, src = 项.split("=", 1)
    目标 = 镜像 / rel; 来源 = pathlib.Path(src)
    if not 来源.is_file():
        sys.exit(f"!! 来源不存在：{来源}")
    留 = 镜像 / "审稿/回退稿" / a.留底 / pathlib.Path(rel).name
    print(f"{演}恢复 {目标}  ←  {来源}（当前版本留底 {留}）")
    if not a.演练:
        留.parent.mkdir(parents=True, exist_ok=True)
        if 目标.exists():
            shutil.copy2(目标, 留)
        目标.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(来源, 目标)

# ---- 2 状态.json（扁平字典）----
if a.状态:
    if not 状态文件.exists():
        sys.exit(f"!! 状态文件不存在：{状态文件}")
    d = json.loads(状态文件.read_text(encoding="utf-8"))
    for 项 in a.状态:
        k, v = 项.split("=", 1)
        try:
            val = json.loads(v)
        except Exception:
            val = v
        print(f"{演}状态 {k}: {d.get(k)!r} → {val!r}")
        d[k] = val
    if not a.演练:
        备 = 父 / f"状态_钩子备份_{time.strftime('%m%d-%H%M%S')}.json"
        shutil.copy2(状态文件, 备)
        状态文件.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"状态已写（备份 {备.name}）")

# ---- 3 台账并入 ----
if a.并入:
    import 回路
    条目 = json.loads(pathlib.Path(a.并入).read_text(encoding="utf-8"))
    if not isinstance(条目, list):
        sys.exit("!! 条目 JSON 必须是数组")
    必 = ("级别", "目标", "问题", "定位", "指令", "验收", "来源")
    for i, x in enumerate(条目, 1):
        缺 = [k for k in 必 if k not in x]
        if 缺:
            sys.exit(f"!! 第 {i} 条缺字段 {缺}")
        if x["级别"] not in ("硬伤", "正确性", "叙述", "版式") or x["目标"] not in ("算", "图", "文"):
            sys.exit(f"!! 第 {i} 条 级别/目标 取值非法：{x['级别']}/{x['目标']}")
    前缀 = {"审稿台账": "审", "章评台账": "章", "美化台账": "美"}.get(a.台账, "意")
    路径 = 父 / "台账" / f"{a.台账}.json"
    台 = 回路.台账(路径, 前缀=前缀)
    前 = len(台.条目)
    print(f"{演}并入 {len(条目)} 条到 {路径}（现有 {前} 条，轮次 {a.轮次}，来源 {a.来源}）")
    for x in 条目:
        print(f"   - {x['级别']}/{x['目标']} {x['问题'][:60]}")
    if not a.演练:
        kw = {"来源": a.来源} if "来源" in inspect.signature(台.并入).parameters else {}
        统 = 台.并入(条目, a.轮次, **kw)
        台.保存()
        新 = [x["id"] for x in 台.条目[前:]]
        print(f"已并入：{统}；新 id {新}")
print(f"{演}钩子完成")
