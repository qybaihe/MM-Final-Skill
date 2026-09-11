#!/usr/bin/env python3
"""蜂群驾驶 v5「论文铸造厂·本地版」：全自主跑完一道数模题。
S0 吃透题目 →G0→ S1 战略锦标赛 →G1→ S2 建模求解(依赖DAG+红队) →G2/问→ S3 图证 →G3→
S4 撰稿(叙事+章评+摘要蜂群) →G4→ S5 审稿场(五路并审+分级修订+定向回炉) →G5→ S6 出版+复盘

用法: python3 蜂群驾驶.py <输入目录(含题目PDF与附件)> <产出目录> [--resume] [--快速]

本地版（施工日志 M5-0）：执行载体从远程沙箱容器换成本机工作根（=产出目录旁的 蜂巢镜像），
codex 走本机 ~/.codex 配置的端点。沙箱时代的播种/探活/指纹/即时回传/容器轮换判据全部退役
（E1-E8 病根随环境一起消失，一轮曾 58.9% 机时耗在等待上）；腿的判死改为 kill -0 活性检查。
设计原则不变：所有角色腿统一异步 + done 标记轮询；每腿失败重试一次；关键 JSON 校验失败带反馈重写一次；
节点原子化+状态机（断点续跑）；六道门一票否决、连败升格蜂群；上游变更级联重算；全程规则驱动零人工。
"""
import glob
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from 本地蜂巢 import LocalHive as Hive
import 调度器
import 回路
sys.path.insert(0, str(pathlib.Path(__file__).parent / "运行时"))
import 统稿守卫      # M5-1：统稿腿只动语言不动事实的机械边界（运行时/统稿守卫.py，有单测）

参数 = [a for a in sys.argv[1:] if not a.startswith("--")]
选项 = [a for a in sys.argv[1:] if a.startswith("--")]
输入目录 = pathlib.Path(参数[0] if len(参数) > 0 else "/Users/bytedance/数模自动化/真题测试/输入")
产出目录 = pathlib.Path(参数[1] if len(参数) > 1 else "/Users/bytedance/数模自动化/真题测试/成品")
续跑 = "--resume" in 选项
镜像目录 = 产出目录.parent / "蜂巢镜像"
# 驱动持有的台账放在镜像之外：镜像是容器产物的落地区，腿在容器里造一份同名文件就会经回传覆盖本地——
# 台账必须免疫这条路径（病根台账 R5/R12：出题的不能改卷）。
台账目录 = 产出目录.parent / "台账"
流水线 = pathlib.Path(__file__).parent
日志文件 = 产出目录.parent / "运行日志.md"
状态文件 = 产出目录.parent / "状态.json"
产出目录.mkdir(parents=True, exist_ok=True)

# ---------- 项目内资产路径（工单0.1：已彻底消灭 scratchpad 外部依赖） ----------
项目根 = 流水线.parent
模板参考目录 = 流水线 / "运行时/模板参考"
HMML路径 = 流水线 / "运行时/HMML.md"
模板CLS = 项目根 / "沙箱验证/format-sandbox.cls"

# ============================================================ 配置（全开档，一处调）
配置 = {
    "档位": "全开",
    "开锦标赛": True,        # S1 战略锦标赛
    "全问开锦标赛": True,    # False=仅分值/复杂度最高问开
    "每问路线数": 3,         # 锦标赛参赛路线数
    "原型预算秒": 400,       # 单条原型脚本运行预算
    "开红队": True,          # S2 红队独立复算
    "红队容差": 0.01,        # 逐键相对误差阈值（≤1% 视为对齐）
    "摘要变体数": 5,
    "图评轮数": 2,
    "图评阈值": 7.0,
    "章评轮数": 2,
    "章评阈值": 7.0,
    "审稿轮数": 4,
    "审稿达标": 8.6,
    "审稿平台期": 0.15,
    "美化轮数": 2,
    "美观阈值": 8.5,
    # 回路协议（回路.py）：定向修改的机械边界。改动行占比超过它 = 推倒重写，整份回退。
    # 升格（两次未消解后换法重做）时放宽到 变化守卫_升格。
    "变化守卫": 0.45,
    "变化守卫_升格": 0.7,
    # 章评每轮派修订腿的章数（按章评分从低到高取），并发闸 4 ⇒ 8 章 = 两个波次
    "章修订每轮章数": 8,
    # 上调依据（实测）：上一轮 223 腿只够走到 G5 前就被护栏刹停，而时间只用了 7.0/14h——
    # 腿预算比时间预算先耗尽，且恰好卡在最后一步。本轮还要重跑问1 并重做 S3~S6。
    # M5-1 表达优化再加：叙事底稿 1 + 叙事返工 ≤1 + 读者腿（章评每轮每批 1）≈4 + 统稿 1 + 摘要复述/修订 ≤3 ≈ 10 腿，
    # 加余量到 380（方案 v1 §5 建议）。
    "MAX_LEGS": 600,
    # 20260910：用户口径「算力不设限」；R33–R36 四次链路修复各自重做了一段（问1 三跑、S4 章评循环重做），
    # 20h 会在 S5 审稿场中途被预算护栏刹停（起始 14:38，S4 重做后剩 <8h，S5 四轮 + S6 约 6–7h）。放到 30h。
    "MAX_HOURS": 40,
    # 本地版的等待纪律只剩两条：轻量脚本调用超时（秒级操作）与波次整体超时。
    # 腿的判死不再靠"静默时长"猜——kill -0 直接问进程还活着没（见 wave()）。
}
# ---------- 档位与角色分档（skill 配置级整合，20260910）：--档位=深度|标准|快速（旧写法 --快速 仍认）----------
# 角色分档：按角色文件给推理档——算/证/裁（读题/规划/建模/红队/解读/预测）xhigh，写/审（撰稿/审稿/猎手/章评/统稿/绘图）high，
# 看图/读者/复盘 medium。空表 = 不分档，全体用启动命令的 CODEX_EFFORT（20260909 对照跑口径：全员 xhigh）。
# 经 本地蜂巢.leg(effort=) → 环境变量 LEG_EFFORT → role.sh/图片腿.sh 的 model_reasoning_effort；未列出的角色用全局值。
角色分档表 = {
    "读题官.md": "xhigh", "规划师.md": "xhigh", "建模师.md": "xhigh", "红队.md": "xhigh", "解读师.md": "xhigh", "答卷预测官.md": "xhigh",
    "撰稿师.md": "high", "审稿员.md": "high", "硬伤猎手.md": "high", "章评师.md": "high", "统稿师.md": "high", "绘图师.md": "high",
    "图评师.md": "medium", "美化师.md": "medium", "评委模拟.md": "medium", "读者.md": "medium", "复盘官.md": "medium",
}
# ---------- 腿引擎（20260910 双版本 skill）：--引擎=codex|claude；不传则看 LEG_ENGINE，再默认 codex ----------
# codex = `codex exec`（seatbelt workspace-write，端点/模型在 ~/.codex/config.toml）；claude = `claude -p` 无头子代理（登录态/settings.json 由用户管）。
# 两种引擎的腿契约一致（工作根写文件 + 日志/<腿名>.done），驱动编排/门/守卫/台账完全不分引擎；引擎只在开炉/续跑边界切换并记进 状态.json。
引擎参 = next((a.split("=", 1)[1] for a in 选项 if a.startswith("--引擎=")), None) or os.environ.get("LEG_ENGINE") or "codex"
if 引擎参 not in ("codex", "claude"):
    sys.exit(f"未知腿引擎 {引擎参}：可选 codex/claude")
配置["引擎"] = 引擎参
配置["角色档位"] = {}
档位参 = next((a.split("=", 1)[1] for a in 选项 if a.startswith("--档位=")), None)
if 档位参 not in (None, "深度", "全开", "标准", "快速"):
    sys.exit(f"未知档位 {档位参}：可选 深度/标准/快速")
if 档位参 == "标准":
    # 标准档 = 深度档去掉两处收益最低的重复算力（摘要变体 5→3、审稿 4→3 轮）+ 角色分档；阈值/门/守卫一律不变。
    配置.update({"档位": "标准", "摘要变体数": 3, "审稿轮数": 3, "MAX_HOURS": 30, "角色档位": dict(角色分档表)})
if "--角色分档" in 选项:
    配置["角色档位"] = dict(角色分档表)
if "--快速" in 选项 or 档位参 == "快速":
    # 快速档用于工单4.1 低配冒烟：目的是让**所有新节点各执行一次**，
    # 因此审稿轮数必须 ≥2（轮1 生成修订单并走定向回炉，轮2 才可能收敛退出）。
    配置.update({"档位": "快速", "全问开锦标赛": False, "每问路线数": 2, "摘要变体数": 3,
                 # 图评/章评也要 2 轮：回路协议的回执→裁定→守卫→回退路径只有第 2 轮才会走到
                 "图评轮数": 2, "章评轮数": 2, "审稿轮数": 2, "美化轮数": 1,
                 "MAX_LEGS": 600, "MAX_HOURS": 20})

h = Hive(root=镜像目录, account="a1", engine=配置["引擎"])
状态 = 调度器.状态机(状态文件)
if 续跑 and 状态.加载():
    # 预算按**实际在跑的机时**累计，而不是首次启动至今的墙钟时间。
    # 教训：中途停跑做分析/改代码的间隔若计入预算，会凭空吃掉额度
    # （本轮三次续跑之间的间隔让"用时"虚高到 20.4h，而真实机时远低于此）。
    已用机时 = 状态.数据.get("累计机时", 0)
    T_START = time.time() - 已用机时
    状态.数据.setdefault("起始时间", time.time())
    续跑生效 = True
    上段引擎 = 状态.数据.get("引擎")          # 续跑时引擎若与上一段不同，开头的续跑日志会明示（操盘手显式指定才会发生）
    状态.数据["引擎"] = 配置["引擎"]
else:
    T_START = time.time()
    状态.数据["起始时间"] = T_START
    状态.数据["累计机时"] = 0
    状态.数据["引擎"] = 配置["引擎"]
    状态.保存()
    续跑生效 = False
    上段引擎 = None
LEG_COUNT = 状态.数据.get("腿数", 0)
MAX_LEGS = 配置["MAX_LEGS"]
MAX_HOURS = 配置["MAX_HOURS"]
运行态 = {"阶段": 状态.数据.get("阶段", ""), "事件": []}
依赖图 = {}


def log(msg):
    line = f"- {time.strftime('%H:%M:%S')} [{int(time.time()-T_START)}s] {msg}"
    print(line, flush=True)
    with open(日志文件, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    运行态["事件"].append(msg)
    # 顺带把"累计机时"落盘：续跑时据此恢复预算，不把停跑间隔算进额度
    try:
        状态.数据["累计机时"] = int(time.time() - T_START)
    except Exception:
        pass


# ---------------- 超时（本地版）----------------
# 沙箱时代的三档（探活/轻/种子/播种）是在给网关抖动、多实例路由、上传∝文件数付保险费，
# 一轮 22.4h 里 13.16h（58.9%）耗在这类等待上（施工日志 M4-4.9）。
# 本地只剩一类：文件系统与进程操作都是亚秒级，T_轻 是纯余量。
T_轻 = 30        # 轮询 done / 读单行 / 删文件 / find 列清单 / 小文件写入


def budget_ok():
    return LEG_COUNT < MAX_LEGS and (time.time() - T_START) < MAX_HOURS * 3600


并发上限 = int(os.environ.get("HIVE_MAX_CONCURRENT", "4"))   # 同时在跑的 codex 腿数；命中 429 自动降 1（下限 2）


def 回收超时腿(name):
    """R60：波次到时限、done 未写而进程还活着 → 判失败（重派或最终失败）之前先整组回收。
    否则旧副本与重试副本并行双写同一批交接文件（2026-09-11 A 题：解读_回炉_轮1_问4 两副本并行 40 min，
    交接/结果声明_问题4.json 在红队复算窗口内被改写 → 红队不齐 → 仲裁；图修1 两副本各写一次 rc=0）。
    设计本意是腿上限（LEG_TIMEOUT=波次-60）先于波次超时把腿杀掉，这里是那条不变量失守时的兜底：
    先收集 role.sh（session leader，pid==pgid）全部后代的进程组（perl 子进程 = codex 组长，另起了会话），
    对每个组 TERM → 最多等 10 s → KILL；不依赖 perl 转发。pid 文件缺失或进程已死则什么都不做。"""
    脚本 = (f"p=$(cat 日志/{name}.pid 2>/dev/null); [ -n \"$p\" ] || exit 0; kill -0 $p 2>/dev/null || exit 0; "
            "[ \"$(ps -o stat= -p $p 2>/dev/null | head -c1)\" != \"Z\" ] || exit 0; "      # 僵尸（已退出、驱动尚未收割）不算活
            "G=\"$p\"; Q=\"$p\"; while [ -n \"$Q\" ]; do N=\"\"; for q in $Q; do for c in $(pgrep -P $q 2>/dev/null); do "
            "N=\"$N $c\"; G=\"$G $(ps -o pgid= -p $c 2>/dev/null | tr -d ' ')\"; done; done; Q=\"$N\"; done; "
            "G=$(echo $G | tr ' ' '\\n' | sort -u | tr '\\n' ' '); "
            "for g in $G; do kill -TERM -- -$g 2>/dev/null; done; "
            "for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 $p 2>/dev/null || break; sleep 1; done; "
            "for g in $G; do kill -KILL -- -$g 2>/dev/null; done; sleep 1; "
            "echo \"回收 pgid:$G 仍活:$(for g in $G; do kill -0 -- -$g 2>/dev/null && printf ' %s' $g; done)\"")
    d = h.exec(脚本, quiet=True, timeout_s=40)
    出 = (d.get("stdout") or "").strip()
    if 出:
        log(f"!! 腿 {name} 到时限仍在跑 → 整组回收（R60）：{出}")


def wave(legs, timeout=1300, poll=30, retry=True):
    """异步跑一组腿到 done。legs=[(role,task,name)]。返回 {name: done首行}。失败重试一次。

    本地判死语义（施工日志 M5-0）：腿是本地进程，role.sh 启动即写 日志/{name}.pid
    （本地蜂巢 以 start_new_session 拉起，pid==pgid）。判死 = "进程死了但 done 没写出来"
    （kill -0 直接问），发现即整组回收、当轮重派。
    慢腿不判死：codex 腿的上限 = 本波次 timeout − 60（经 LEG_TIMEOUT 下发给 role.sh），进程活着就让它跑完；
    上限必须小于波次超时，否则波次判失败重派时旧进程还活着，两条腿双写同一批文件。
    并发闸（M5-1 补记 ④，R32）：一个波次里最多 `并发上限` 条腿同时在跑，其余排队、有腿收尾就补位；
    每条腿的超时从**它自己启动**那一刻算。20260908 重标实测 6 腿并发命中上游 429，≤3 并发整晚 0 次；
    命中 429 的波次把上限降 1（下限 2），下一波次生效。日志里 429 与 rc=142（超腿上限）分开认。
    """
    global LEG_COUNT, 并发上限
    pending = list(legs)
    results = {}
    attempt = 0
    while pending and attempt < 2:
        attempt += 1
        腿上限 = max(600, int(timeout) - 60)
        队列 = list(pending)
        启动时刻 = {}
        完成 = set()
        死亡 = {n: 0 for _, _, n in pending}

        def 派(role, task, name):
            global LEG_COUNT
            h.exec(f"rm -f 日志/{name}.done 日志/{name}.pid", quiet=True, timeout_s=T_轻)
            h.leg(role, task, name, sync=False, leg_timeout=腿上限, effort=配置["角色档位"].get(role))
            启动时刻[name] = time.time()
            LEG_COUNT += 1
            状态.加腿(1)

        def 活腿(now):
            return [n for n in 启动时刻 if n not in 完成 and now - 启动时刻[n] < timeout]

        def 补位(now):
            补 = []
            while 队列 and len(活腿(now)) < 并发上限:      # 活腿 已含刚派出的腿，不能再加 len(补)（干跑实测会把上限 4 变 2）
                role, task, name = 队列.pop(0)
                派(role, task, name)
                补.append(name)
            return 补

        首批 = 补位(time.time())
        log(f"波次 {len(pending)} 腿(第{attempt}次) 并发上限 {并发上限}：先启 {首批}"
            + (f"，排队 {[n for _, _, n in 队列]}" if 队列 else ""))
        while True:
            time.sleep(poll)
            now = time.time()
            未完 = [n for n in 启动时刻 if n not in 完成]
            if 未完:
                d = h.exec("for n in " + " ".join(未完) + "; do [ -f 日志/$n.done ] && echo $n; done; true",
                           quiet=True, timeout_s=T_轻)
                for n in (d.get("stdout") or "").split():
                    if n in 死亡:
                        完成.add(n)
            if len(完成) >= len(pending):
                break
            活 = 活腿(now)
            if not 队列 and not 活:
                break                      # 没排队的、也没在时限内的腿了：按失败收尾
            # 活性检查：对没写 done 的腿逐条 kill -0。进程活着 = 慢，继续等；
            # 进程死了 = done 永远不会来了，立即重派（不再等满 timeout）。
            # DEAD（pid 有、进程无）= 铁证，立即重派；NOPID（pid 文件都没写出来）= 可能只是刚启动，宽限 45s。
            if 活:
                查 = ("for n in " + " ".join(活) +
                      "; do [ -f 日志/$n.done ] && continue; "
                      "p=$(cat 日志/$n.pid 2>/dev/null); "
                      "if [ -z \"$p\" ]; then echo \"$n NOPID\"; "
                      "elif kill -0 $p 2>/dev/null && "
                      "[ \"$(ps -o stat= -p $p 2>/dev/null | head -c1)\" != \"Z\" ]; then echo \"$n ALIVE\"; "
                      "else echo \"$n DEAD\"; fi; done")
                d2 = h.exec(查, quiet=True, timeout_s=T_轻)
                行们 = (d2.get("stdout") or "").splitlines()
                死 = [l.split()[0] for l in 行们 if l.strip().endswith(" DEAD")]
                死 += [l.split()[0] for l in 行们 if l.strip().endswith(" NOPID") and now - 启动时刻.get(l.split()[0], now) >= 45]
                for n in 死:
                    if 死亡[n] >= 2:
                        continue            # 连死 3 次的腿不再重派，到它的时限按失败收尾
                    死亡[n] += 1
                    log(f"!! 腿 {n} 进程已死但 done 未写（第{死亡[n]}次，已跑 {int(now - 启动时刻[n])}s）→ 整组回收并即时重派")
                    h.exec(f"kill -- -$(cat 日志/{n}.pid 2>/dev/null) 2>/dev/null; "
                           f"rm -f 日志/{n}.done 日志/{n}.pid", quiet=True, timeout_s=T_轻)
                    role, task = next((r, t) for r, t, nn in pending if nn == n)
                    派(role, task, n)
            补 = 补位(time.time())
            if 补:
                log(f"补位启动 {补}（并发上限 {并发上限}，仍排队 {len(队列)}）")
        nxt = []
        本波429 = False
        for role, task, name in pending:
            first = ""
            for _ in range(2):
                d = h.exec(f"head -1 日志/{name}.done 2>/dev/null", quiet=True, timeout_s=T_轻)
                s = (d.get("stdout") or "").strip()
                if s:
                    first = s
                    break
            if not first or first.startswith("AUTO"):
                d429 = h.exec(f"grep -c '429 Too Many Requests' 日志/{name}.log 2>/dev/null; true", quiet=True, timeout_s=T_轻)
                if (d429.get("stdout") or "0").strip().split()[-1:] not in (["0"], []):
                    本波429 = True
                    log(f"!! 腿 {name} 命中上游限流 429（并发过高或配额）——本轮按失败处理，重试一次")
                elif "rc=142" in first:
                    log(f"!! 腿 {name} 超过腿上限 {腿上限}s 被杀（rc=142）——任务过重或需放宽该波次 timeout")
                elif not first:
                    log(f"!! 腿 {name} 到时限仍无 done（波次 {timeout}s）")
                if not first:
                    回收超时腿(name)          # R60：重派/判最终失败前先回收还活着的旧副本，杜绝双写
                if retry and attempt == 1 and budget_ok():
                    nxt.append((role, task, name))
                    log(f"腿失败待重试: {name} ({first[:40]})")
                else:
                    results[name] = f"FAILED:{first[:60]}"
                    log(f"腿最终失败: {name}")
            else:
                results[name] = first
        if 本波429 and 并发上限 > 2:
            并发上限 -= 1
            log(f"!! 本波次命中 429：并发上限降为 {并发上限}（R32 自适应）")
        pending = nxt
    return results

def get_json(path, desc, fix_role=None, fix_hint=""):
    """拉取并解析 JSON；失败时派修复腿一次（腿名按文件名唯一，防并行撞名）。"""
    修名 = "修json_" + pathlib.Path(path).stem.replace(" ", "")
    for attempt in (1, 2):
        h.harvest([path], str(镜像目录))
        try:
            return json.load(open(镜像目录 / path, encoding="utf-8"))
        except Exception as e:
            log(f"{desc} 解析失败({e})，尝试{attempt}")
            if attempt == 1 and fix_role and budget_ok():
                wave([(fix_role, f"文件 {path} 不是合法JSON或缺失（错误：{e}）。{fix_hint}。请重新生成合法版本覆盖原文件。完成写 日志/{修名}.done", 修名)], timeout=700)
    return None


def run_script(script_path, log_name, budget=1320, repair_role="建模师.md", repairs=2):
    """跑求解/绘图脚本：nohup+轮询；失败派修复腿再跑。返回 rc。

    本地判死：进程不在且无 rc = 立即重跑（kill -0 探活，不计入修复次数），
    不再白等满 budget——沙箱时代这里单次白烧过 22 分钟。
    """
    global LEG_COUNT
    执行器 = "bash" if script_path.endswith(".sh") else "python3"
    attempt = 0
    死亡重跑 = 0
    while attempt <= repairs:
        attempt += 1
        h.exec(f"rm -f 日志/{log_name}.done 日志/{log_name}.pid; "
               f"nohup bash -c 'echo $$ > 日志/{log_name}.pid; {执行器} {script_path} > 日志/{log_name}.log 2>&1; echo rc=$? > 日志/{log_name}.done' >/dev/null 2>&1 & echo started",
               quiet=True, timeout_s=T_轻)
        t0 = time.time()
        rc = None
        死亡 = False
        while time.time() - t0 < budget:
            time.sleep(25)
            # 一次探测拿 rc + 进程是否还在。进程死了又没 rc = 脚本被"吃掉"（启动即崩），
            # 立即重跑；进程活着 = 还在算，继续等。
            d = h.exec(f"cat 日志/{log_name}.done 2>/dev/null; "
                       f"p=$(cat 日志/{log_name}.pid 2>/dev/null); "
                       f"[ -n \"$p\" ] && kill -0 $p 2>/dev/null && echo RUNNING", quiet=True, timeout_s=T_轻)
            out = (d.get("stdout") or "")
            在跑 = "RUNNING" in out
            rc = None
            for line in out.splitlines():
                if line.strip().startswith("rc="):
                    rc = int(line.strip()[3:].split()[0])
                    break
            if rc is not None:
                break
            if not 在跑 and time.time() - t0 > 45 and 死亡重跑 < 2:
                log(f"!! 脚本 {script_path} 判死：进程消失且无 rc（已等 {int(time.time()-t0)}s）"
                    f"→ 立即重跑，不计入修复次数")
                死亡重跑 += 1
                attempt -= 1
                死亡 = True
                break
        if 死亡:
            continue
        log(f"脚本 {script_path} 第{attempt}次 rc={rc}")
        if rc == 0:
            return 0
        if attempt <= repairs and budget_ok():
            LEG_COUNT += 1
            wave([(repair_role, f"脚本修复：{script_path} 运行失败或超时（日志见 日志/{log_name}.log，用 tail -40 查看）。只修此脚本让它跑通，不改变方法本质。注意运行时间必须控制在 {budget//60} 分钟内，必要时降采样/减少迭代但保持结论口径。只改不跑。完成写 日志/修脚本_{log_name}.done", f"修脚本_{log_name}")], timeout=700)
    return rc if rc is not None else -1


def compile_paper(tag=""):
    d = h.exec(r'''cd 论文 && xelatex -interaction=nonstopmode 论文.tex > /dev/null 2>&1; xelatex -interaction=nonstopmode 论文.tex > /dev/null 2>&1
E=$(grep -c '^!' 论文.log); O=$(grep -c Overfull 论文.log); P=$(grep -oE '\([0-9]+ pages' 论文.log | grep -oE '[0-9]+' | head -1)
A=$(grep 'abstract:end' 论文.aux 2>/dev/null | grep -oE '\{[0-9]+\}' | head -1 | tr -d '{}')
echo "E=$E O=$O P=$P 摘要页=$A"
grep '^!' 论文.log | head -5''', timeout_s=400, quiet=True)
    out = (d.get("stdout") or "").strip()
    log(f"编译{tag}: {out.splitlines()[0] if out else 'FAIL'}")
    try:
        kv = dict(x.split("=") for x in out.splitlines()[0].split())
        return int(kv.get("E", 9)), int(kv.get("O", 0)), int(kv.get("P", 0)), out
    except Exception:
        return 9, 0, 0, out


def checkpoint(paths, tag):
    """本地版：工作根即镜像，产物天然落地，checkpoint 只剩日志（保留调用点不炸）。"""
    try:
        在 = [r for r in paths if (h.root / r).is_file()]
        log(f"checkpoint[{tag}]: {len(在)}/{len(paths)} 文件已在工作根")
    except Exception as e:
        log(f"checkpoint[{tag}] 失败: {e}")


def checkpoint_dir(remote_dir, tag):
    """本地版：目录已在工作根内，只数一下文件量做可见性。"""
    try:
        n = sum(1 for x in (h.root / remote_dir).rglob("*") if x.is_file()) if (h.root / remote_dir).is_dir() else 0
        log(f"checkpoint[{tag}]: {remote_dir}/ {n} 文件已在工作根")
    except Exception as e:
        log(f"checkpoint[{tag}] 失败: {e}")


# ============================================================ 本地基座同步
# 沙箱时代的 L0 播种 / L1 契约回灌 / 打包上传 / 探活指纹 / ensure_hive（约 300 行）随容器一起
# 退役：本地工作根是持久文件系统，"基座资产最新化"只是一次幂等的按内容差异同步。
def 基建清单():
    """L0 基座层：一轮内**不变**的资产 —— [(工作根内相对路径, 本地Path)]。"""
    对 = [("AGENTS.md", 流水线 / "AGENTS.md"),
          ("bin/role.sh", 流水线 / "运行时/role.sh"),
          ("bin/codex公共.sh", 流水线 / "运行时/codex公共.sh"),
          ("bin/图片腿.sh", 流水线 / "运行时/图片腿.sh"),
          ("bin/审计.py", 流水线 / "运行时/审计.py"),
          ("bin/表达画像.py", 流水线 / "运行时/表达画像.py"),     # 审计.py 第 7/12-14 节 import 它；缺了审计直接崩（不是静默）
          ("bin/统稿守卫.py", 流水线 / "运行时/统稿守卫.py"),
          ("论文/format.cls", 模板CLS)]
    for n in ["循环.py", "门检.py"]:
        fp = 流水线 / "运行时" / n
        if fp.exists():
            对.append((f"bin/{n}", fp))
    for p in (流水线 / "角色").glob("*.md"):
        对.append((f"角色/{p.name}", p))
    # 内部术语.txt 必须在列：漏了的话 审计.py 的 try/except 会静默拿到空词表，
    # 内部术语密度恒为 0，G4 判据看起来在跑、其实永远空过。
    # M5-1 表达词表/阈值/范文：对冲词、缩写白名单、流程词、表达阈值 缺失时门检会报"缺失不放行"（不再静默空过）；
    # 范文卡片库/表达锚点 是评审腿"范文对照"的参照，缺了评审腿会退回凭印象打分——所以也进基座。
    for n in ["禁用词.txt", "内部术语.txt", "优秀论文标准.md", "文献卡片库.md", "方法卡片库.md",
              "对冲词.txt", "缩写白名单.txt", "流程词.txt", "表达阈值.json", "范文卡片库.md", "表达锚点.md"]:
        fp = 流水线 / "运行时" / n
        if fp.exists():
            对.append((f"运行时/{n}", fp))
        for p in sorted((流水线 / "运行时/范文/文本").glob("*.txt")):   # 真稿全文（10 篇研究生数模一等奖），评审腿按卡片指的篇号打开对照
            对.append((f"运行时/范文/文本/{p.name}", p))
    if HMML路径.exists():
        对.append(("运行时/HMML.md", HMML路径))
    for t in 模板参考目录.glob("*.tex"):
        对.append((f"运行时/模板参考/{t.name}", t))
    # R54（2026 国赛 A 题）：附件可以带子目录（如 附件3/result1.xlsx 结果填写模板），按相对路径播进 数据/；
    # 题目目录除 PDF 外还认 .txt/.md（如 论文格式规范2026.txt，读题官据此写契约）。仍不递归 .pdf 以外的题目文件夹。
    for p in sorted(输入目录.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        rel = p.relative_to(输入目录).as_posix()
        if p.suffix.lower() in (".pdf", ".txt", ".md") and p.parent == 输入目录:
            对.append((f"题目/{p.name}", p))
        elif p.suffix.lower() in (".xlsx", ".csv", ".docx"):
            对.append((f"数据/{rel}", p))
    return 对


def 本地就绪():
    """保证工作根的基座资产是最新的（按内容差异覆盖，幂等，毫秒级）。
    返回 (True, "local") 兼容旧调用点的 (就绪, 指纹) 解构。"""
    同步 = 0
    for 远, 本 in 基建清单():
        if not 本.exists():
            continue
        q = h.root / 远
        if 远.startswith("论文/") and q.exists():
            continue   # 论文/ 是产品目录：模板只播种一次，续跑不覆盖（R39：编修腿修好的 format.cls 被续跑同步打回模板，审前编译崩、页图为空）
        if q.exists() and q.read_bytes() == 本.read_bytes():
            continue
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_bytes(本.read_bytes())
        同步 += 1
    for d in ("日志", "任务", "交接", "求解", "论文", "审稿"):
        (h.root / d).mkdir(parents=True, exist_ok=True)
    if 同步:
        log(f"本地基座同步：{同步} 文件更新")
    return True, "local"


# ============================================================ 节点框架（断点续跑）
class 预算耗尽(Exception):
    """预算护栏触发：由 节点() 抛出，主流程捕获后走收尾（出最好的稿）而不是硬崩。"""


def 节点(名称, fn, 阶段=None):
    """原子节点：已完成则跳过；执行成功即落盘状态。返回 fn 的返回值（跳过时返回 None）。

    预算护栏在此强制执行：此前 budget_ok() 只挡"可选加项"（多轮审稿/升格/美化），
    主干节点完全不看预算，导致 MAX_HOURS=14 被突破到 15.3 小时仍在跑（实测）。
    现在每进一个未完成节点都先查预算，超了就抛 预算耗尽，由主流程收尾。
    """
    if 状态.已完成(名称):
        log(f"节点[{名称}] 已完成，跳过（续跑）")
        return None
    if not budget_ok():
        用时 = (time.time() - T_START) / 3600
        log(f"!! 预算护栏触发：腿数={LEG_COUNT}/{MAX_LEGS} 用时={用时:.1f}/{MAX_HOURS}h，"
            f"节点[{名称}] 不再开工，转收尾")
        raise 预算耗尽(f"节点[{名称}] 前预算耗尽（腿{LEG_COUNT} 用时{用时:.1f}h）")
    if 阶段:
        运行态["阶段"] = 阶段
        状态.设阶段(阶段)
    r = fn()
    状态.标记完成(名称)
    return r


def 图片腿(名, 角色文件, 任务文本, 图们, timeout=900, reasoning=True):
    """看图腿：codex -i 批图（≤8 张）。铁则 §二.4。本地版走 bin/图片腿.sh（与 role.sh 同口径）。"""
    global LEG_COUNT
    LEG_COUNT += 1
    状态.加腿(1)
    h.exec(f"rm -f 日志/{名}.done 日志/{名}.pid", quiet=True, timeout_s=T_轻)
    h.leg_img(角色文件, 任务文本, 名, 图们[:8], timeout=timeout, reasoning=reasoning, effort=配置["角色档位"].get(角色文件))
    return 名


def 等腿(名们, timeout=1100, poll=30):
    """等一批（图片腿等）自建腿的 done 标记。"""
    t0 = time.time()
    while time.time() - t0 < timeout and 名们:
        time.sleep(poll)
        marks = " ".join(f"日志/{n}.done" for n in 名们)
        d = h.exec(f"c=0; for f in {marks}; do [ -f $f ] && c=$((c+1)); done; echo $c", quiet=True, timeout_s=T_轻)
        try:
            if int((d.get("stdout") or "0").strip().split()[-1]) >= len(名们):
                return True
        except Exception:
            pass
    return False


def 图片腿群(规格们, 单腿超时=800, 等待=1000):
    """R65：图片腿() 直接 spawn、不走 wave() 的并发闸。美化/终审按 8 页一腿整份铺开：对照跑 66 页只有 9 腿，
    2026 A 题 327 页（附录源码 ≈305 页）→ 41 腿同时起，10 倍于实测稳定并发 4（6 并发曾整晚 429）。
    这里按 并发上限 分块：起一块 → 等腿(块) → 下一块；块内单腿仍由 图片腿.sh 的 with_timeout 兜底。
    规格 = (名, 角色文件, 任务文本, 图们, reasoning)。返回启动过的腿名（顺序与规格一致）。"""
    名们 = []
    步 = max(1, int(并发上限))
    for i in range(0, len(规格们), 步):
        块 = 规格们[i:i + 步]
        for 名, 角色, 任务, 图们, 推理 in 块:
            名们.append(图片腿(名, 角色, 任务, 图们, timeout=单腿超时, reasoning=推理))
        if len(规格们) > 步:
            log(f"图片腿群 {len(名们)}/{len(规格们)}：起 {[x[0] for x in 块]}（并发上限 {步}，R65 分块）")
        等腿([x[0] for x in 块], timeout=等待)
    return 名们


def 门检(门名, timeout_s=200):
    """容器内跑 运行时/门检.py，返回 (通过bool, 明细)。"""
    d = h.exec(f"python3 bin/门检.py {门名} 2>&1 | tail -5", timeout_s=timeout_s, quiet=True)
    报告 = get_json(f"审稿/门检_{门名}.json", f"门检{门名}") or {}
    通过 = bool(报告.get("通过"))
    明细 = 报告.get("明细", (d.get("stdout") or "")[:300])
    return 通过, 明细


# ============================================================ 回路：台账 / 回执 / 裁定 / 快照（驱动侧粘合）
# 协议本体在 回路.py（纯逻辑、有单测）。这里只做三件事：把腿的产物读进台账、把台账渲染进任务文本、
# 把快照写回容器。台账文件只由驱动写（台账目录 在镜像之外，腿够不着）。
def 台账路径(名):
    return 台账目录 / f"{名}.json"


def 台账视图上传(t, rel):
    """把驱动持有的台账以只读视图放进容器（门检/复盘读）。每次都从本地真相覆盖——腿改了也没用。"""
    t.保存()
    收敛, 阻塞明细 = t.收敛()
    视图 = {"轮次": t.轮次, "摘要": t.摘要(), "收敛": 收敛, "阻塞明细": 阻塞明细, "条目": t.条目}
    h.exec(f"echo 台账视图 {rel}", files=[h.f_text(rel, json.dumps(视图, ensure_ascii=False, indent=1))], quiet=True, timeout_s=T_轻)


def 收回执(t, 腿名):
    r = get_json(f"审稿/回执_{腿名}.json", f"回执{腿名}")
    r = r if isinstance(r, list) else ((r or {}).get("回执", []) if isinstance(r, dict) else [])
    统 = t.收回执(r, 腿名)
    转 = 0   # R40：撰稿师遇到要出新数字/要重绘的意见，回执以「需转算」「需转图」开头 → 改路并退回待改，下一轮走建模师/绘图师
    for it in r or []:
        if not isinstance(it, dict):
            continue
        m = re.match(r"\s*需转([算图])", str(it.get("改动", "")))
        x = t.取(str(it.get("id", "")).strip()) if m else None
        if x is None:
            continue
        x["目标"], x["状态"] = m.group(1), 回路.待改
        x["尝试次数"] = max(0, int(x.get("尝试次数", 0)) - 1)
        x.setdefault("历史", []).append({"裁定": "转路", "理由": f"{腿名}：{str(it.get('改动', ''))[:80]}"})
        转 += 1
    log(f"回执[{腿名}]：受理 {统['受理']} 未知id {统['未知id']}" + (f" 转路 {转}" if 转 else "") + ("（腿没交回执——按未改处理）" if not r else ""))
    t.保存()
    return 统


def 收裁定(t, 名们):
    """多位评审对同一编号的裁定：任一实质未消解即未消解，评委模拟「未核实」弃权票不算（R45）；相对判断任一更差即更差、否则任一更好即更好。"""
    通道们 = []
    相们, 分们 = [], []
    for 名 in 名们:
        p = 回路.解析配对裁定(get_json(f"审稿/裁定_{名}.json", f"裁定{名}"))
        通道们.append((名, p["逐项"]))
        if p["相对判断"]:
            相们.append(p["相对判断"])
        if p["分数"] is not None:
            分们.append(p["分数"])
    相 = "更差" if "更差" in 相们 else ("更好" if "更好" in 相们 else ("持平" if 相们 else None))
    逐, 弃权数 = 回路.合并裁定(通道们)
    统 = t.收裁定(逐, t.轮次)
    漏 = t.待复核未裁()
    log(f"裁定[{'/'.join(名们)}]：已消解 {统['已消解']} 未消解 {统['未消解']} 未知id {统['未知id']} 漏裁回待改 {漏} 弃权票 {弃权数} 相对判断={相}")
    t.保存()
    return 相, (sum(分们) / len(分们) if 分们 else None)


def 镜像快照(相对路径们):
    out = {}
    for rel in 相对路径们:
        q = 镜像目录 / rel
        if q.is_file():
            out[rel] = q.read_bytes()
    return out


def 回退(文件字典, 标签=""):
    """最优保留的回退动作：快照写回本地镜像与容器。"""
    if not 文件字典:
        return 0
    files = []
    for rel, b in 文件字典.items():
        q = 镜像目录 / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_bytes(b)
        files.append(h.f_local(rel, q))
    h.exec(f"echo 回退 {len(files)}", files=files, quiet=True, timeout_s=T_轻)
    log(f"!! 回退[{标签}]：{len(files)} 文件恢复到上一轮快照")
    return len(files)


def 变化守卫回退(t, 快照, 相对路径们, 上限, 标签):
    """定向修改的机械边界：改动比例超上限的文件整份回退，并把本轮指向它的回执作废（病根台账 R2）。
    R36（20260910）：度量改用 回路.修订守卫——正文句子为单位、`%` 注释行不计、评审引号点名的句子不计，
    再配一把字符级尺；两把尺都超线才算重写。被回退的新稿留底到 审稿/回退稿/，比例明细进日志，供复盘校准。"""
    回退的 = []
    for rel in 相对路径们:
        q = 镜像目录 / rel
        if rel not in 快照 or not q.is_file():
            continue
        名 = pathlib.Path(rel).name
        点名 = [x for x in t.条目 if 名 in str(x.get("定位", ""))]
        旧文, 新文 = 快照[rel].decode("utf-8", "replace"), q.read_text(encoding="utf-8", errors="replace")
        if 旧文 != 新文 and 回路.是代码章(名, 新文):   # R47：源码章随求解器换版整份替换是正常修订，不按句子尺量
            log(f"变化守卫[{标签}]：{rel} 代码章不设守卫（源码随求解器换版；结构守卫仍管空章）")
            continue
        通过, r, 明 = 回路.修订守卫(旧文, 新文, 点名, 上限)
        if 明["行比"] > 0.001:
            log(f"变化守卫[{标签}]：{rel} {'过' if 通过 else '超线'} 比例 {r:.0%}（未点名句 {明['未点名比']:.0%} 字 {明['字比']:.0%} "
                f"行 {明['行比']:.0%}；句 {明['单位数']}→{明['新单位数']} 点名 {明['点名单位']} 片段 {明['点名片段']} 意见 {len(点名)}）")
        if not 通过:
            回退的.append((rel, r))
            try:
                留 = 镜像目录 / "审稿/回退稿" / 标签 / 名
                留.parent.mkdir(parents=True, exist_ok=True)
                留.write_text(新文, encoding="utf-8")
            except Exception as e:
                log(f"回退稿留底失败 {rel}: {e}")
    if not 回退的:
        return []
    回退({rel: 快照[rel] for rel, _ in 回退的}, 标签)
    名们 = [pathlib.Path(rel).name for rel, _ in 回退的]
    for x in t.条目:
        if x.get("状态") == 回路.待复核 and any(n in str(x.get("定位", "")) + json.dumps(x.get("回执", [])[-1:], ensure_ascii=False) for n in 名们):
            x["状态"] = 回路.未消解
            x.setdefault("历史", []).append({"轮次": t.轮次, "裁定": "变化守卫", "理由": f"整份改动比例超 {上限:.0%}，回退作废"})
    for rel, r in 回退的:
        log(f"!! 变化守卫[{标签}]：{rel} 改动比例 {r:.0%} > {上限:.0%} → 回退（不是修订，是重写；新稿留底 审稿/回退稿/{标签}/）")
    t.保存()
    return 回退的


def 结构守卫回退(快照, 相对路径们, 上限, 标签):
    """成稿级返工（G4/G5）的机械边界（病根 R38）：
       ① 论文/论文.tex 的 \\input 集合变了 → 主控回退；② 章被清空（原有正文单位、现在没有）→ 回退；
       ③ 附录 lstlisting 数量减少 → 回退；④ 其余按 回路.修订守卫（无点名，上限=升格线）。被回退的新稿留底 审稿/回退稿/。"""
    回退的 = []
    for rel in 相对路径们:
        q = 镜像目录 / rel
        if rel not in 快照:
            continue
        旧文 = 快照[rel].decode("utf-8", "replace")
        新文 = q.read_text(encoding="utf-8", errors="replace") if q.is_file() else ""
        if 新文 == 旧文:
            continue
        理由 = None
        if rel.endswith("论文.tex"):
            取 = lambda t: set(re.findall(r"\\input\{([^}]+)\}", re.sub(r"%.*", "", t)))
            if 取(旧文) != 取(新文):
                理由 = f"主控 \\input 集变了：{sorted(取(旧文) ^ 取(新文))[:6]}"
        elif not q.is_file():
            理由 = "文件被删除"
        else:
            旧单, 新单 = 回路.正文单位(旧文), 回路.正文单位(新文)
            if 旧单 and not 新单:
                理由 = "章被清空"
            elif 新文.count("\\begin{lstlisting}") < 旧文.count("\\begin{lstlisting}"):
                理由 = f"源码清单减少 {旧文.count(chr(92) + 'begin{lstlisting}')}→{新文.count(chr(92) + 'begin{lstlisting}')}"
            else:
                通过, r, 明 = 回路.修订守卫(旧文, 新文, (), 上限)
                if not 通过:
                    理由 = f"整份改动 {r:.0%} > {上限:.0%}（未点名句 {明['未点名比']:.0%} 字 {明['字比']:.0%}）"
        if 理由:
            回退的.append((rel, 理由))
            try:
                留 = 镜像目录 / "审稿/回退稿" / 标签 / pathlib.Path(rel).name
                留.parent.mkdir(parents=True, exist_ok=True)
                留.write_text(新文, encoding="utf-8")
            except Exception as e:
                log(f"回退稿留底失败 {rel}: {e}")
    if 回退的:
        回退({rel: 快照[rel] for rel, _ in 回退的}, 标签)
        for rel, 理由 in 回退的:
            log(f"!! 结构守卫[{标签}]：{rel} → 回退：{理由}（新稿留底 审稿/回退稿/{标签}/）")
    return 回退的


_算词 = re.compile(r"重算|重跑|重新计算|重新拟合|重新搜索|重新求解|重新仿真|补查|补做|补算|加密网格|网格加密|起点加密|参数扫描|蒙特卡洛|再算一遍|跑一遍")   # 只认"要出新数字"的动词；裸的 网格/起点/复算 在说明性意见里太常见
_图词 = re.compile(r"重绘|绘图脚本|重新绘制|重新画|图例|坐标轴|放大坐标|字号|配色|面板|边框|分辨率|dpi|画质")


def 推断目标(文本):
    """评审腿的意见基本不带 目标 字段（R40：S5 轮1 20/20 条全落到「文」，撰稿师越界补跑网格搜索、重绘 7 图，
    红队/G2 门/图评全被绕过）。按关键词分路：算 > 图 > 文；分不出来才是文。"""
    if _算词.search(文本 or ""):
        return "算"
    if _图词.search(文本 or ""):
        return "图"
    return "文"


def 意见条目(it, 默认级别, 来源):
    """审稿员/评委的意见可能是字符串或字典，统一成台账条目。"""
    if isinstance(it, str):
        m = re.match(r"\s*\[?对应[:：]?\s*([审章图美解]-\d+-\d+)\]?\s*(.*)", it, re.S)
        文 = (m.group(2) if m else it)[:600]
        d = {"级别": 默认级别, "目标": 推断目标(文), "问题": 文, "定位": "", "指令": 文, "验收": "", "来源": 来源}
        if m:
            d["对应"] = m.group(1)
        return d
    if not isinstance(it, dict):
        return None
    # 键别名：审稿员 A/B 同一 schema 两种写法（修改指令/直接执行、验收证据/验收依据、文件与位置）——轮1 实测 B 的 5 条 指令/定位 全空
    d = {"级别": it.get("级别", 默认级别), "目标": str(it.get("目标") or ""),
         "问题": str(it.get("问题") or it.get("建议") or it.get("修改指令") or it.get("直接执行") or it)[:600],
         "定位": str(it.get("定位") or it.get("位置") or it.get("文件与位置") or "")[:200],
         "指令": str(it.get("指令") or it.get("修改指令") or it.get("直接执行") or it.get("修改建议") or it.get("建议") or "")[:600],
         "验收": str(it.get("验收") or it.get("验收证据") or it.get("验收依据") or "")[:200], "来源": 来源}
    if d["目标"] not in ("算", "图", "文"):
        d["目标"] = 推断目标(d["问题"] + " " + d["指令"])
    if it.get("对应"):
        d["对应"] = str(it["对应"])
    return d


def 熔断处理(t, 标签):
    """两次修不掉：硬伤/正确性 → 再给一次换人换法的机会（升格），第三次 → 搁置；叙述/版式 → 直接搁置留痕。绝不静默丢弃。"""
    for x in t.熔断候选(2):
        决 = 回路.熔断决定(x)
        if 决 == "升格" and not x.get("升格过"):
            x["升格过"] = True
            log(f"!! 熔断升格[{标签}] [{x['id']}] {x['级别']}：{x['问题'][:60]}——下一轮换一种改法，允许重写该段")
        else:
            t.搁置条目(x["id"], f"{x['尝试次数']} 次修订未消解" + ("（已升格一次）" if x.get("升格过") else ""))
            log(f"!! 熔断搁置[{标签}] [{x['id']}] {x['级别']}：{x['问题'][:60]}")
    t.保存()


def 升格蜂群(节点名, 角色, 任务模板, 裁决任务, 变体数=3, 变体后=None):
    """连败两次的环节升格蜂群：3 条差异化变体腿 + 1 条裁决腿。
    任务模板 含 {切入} 占位；裁决任务 为解读师同口径评。
    变体后：变体腿写完、裁决腿派出**之前**要做的事（G2 用它跑三条变体脚本）。
    R34（骨架版实测）：原先调用方在 升格蜂群 返回后才跑变体脚本，裁决腿看到的是三个空结果目录
    （`升格裁决_G2问2.done`：升格1/3 结果目录为空、升格2 不存在），升格从未有过赢的可能。"""
    切入们 = ["换主方法族：放弃当前方法族，改用另一大类方法重做",
              "换数据口径/特征：保持方法族但更换数据口径、特征构造或预处理链重做",
              "保方法改激进修复：沿用当前方法但对暴露的缺陷做激进修正（重写关键实现）"]
    legs = []
    for k, 切 in enumerate(切入们[:变体数], 1):
        legs.append((角色, 任务模板.replace("{切入}", 切).replace("{变体号}", str(k)) +
                     f"\n完成写 日志/升格_{节点名}_{k}.done", f"升格_{节点名}_{k}"))
    log(f"升格蜂群[{节点名}]：派 {len(legs)} 条变体腿")
    wave(legs, timeout=1300)
    if 变体后:
        变体后()          # 先让变体的结果落地，裁决腿才有东西可比
    wave([("解读师.md", 裁决任务 + f"\n完成写 日志/升格裁决_{节点名}.done", f"升格裁决_{节点名}")], timeout=1100)
    checkpoint_dir("交接", f"升格-{节点名}")


# ============================================================ 主流程
def 解析路线(侦察, 每问路线数):
    """从 路线侦察.json 抽出 {问编号: [路线...]}，对角色实际用的键名做**容错**。

    实测教训：规划师会把问题数组写成 问题路线 / 各问 / problems 等键，路线数组写成 候选路线 / 参赛路线 等；
    严格按单一键名解析会静默拿到空表，导致整个锦标赛被跳过（这是真题冒烟第一次踩到的坑）。
    因此：先在任意层级找出"含编号且含路线数组"的对象，再取路线。
    """
    问键 = ("问题", "问题路线", "各问", "问题清单", "路线侦察", "问题列表", "problems")
    路键 = ("路线", "参赛路线", "候选路线", "技术路线", "路线清单", "routes")

    def 取路线(o):
        for k in 路键:
            v = o.get(k)
            if isinstance(v, list) and v:
                return v
        return None

    候选 = []

    def 遍历(o):
        if isinstance(o, dict):
            if o.get("编号") is not None and 取路线(o):
                候选.append(o)
            for k in 问键:
                v = o.get(k)
                if isinstance(v, list):
                    for it in v:
                        遍历(it)
            for v in o.values():
                if isinstance(v, (dict, list)) and not isinstance(v, str):
                    遍历(v)
        elif isinstance(o, list):
            for it in o:
                遍历(it)

    遍历(侦察)
    表 = {}
    for q in 候选:
        try:
            bh = int(str(q.get("编号")).strip())
        except Exception:
            continue
        if bh in 表:
            continue
        表[bh] = list(取路线(q) or [])[:每问路线数]
    return 表


def 路线名(r, 缺省="路线"):
    """取一条路线的名字，容忍 名称/路线名/名字/name 等写法。"""
    if not isinstance(r, dict):
        return str(r)[:24] or 缺省
    for k in ("名称", "路线名", "名字", "路线名称", "name"):
        v = r.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for k in ("技术范式", "简述", "核心模型"):
        v = r.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()[:24]
    return 缺省


def 应急出版():
    """预算耗尽/异常时的兜底收割：尽量编译一次并把现有材料全拉回本地。

    目的：护栏刹车后**不能两手空空**。哪怕论文只写了一半，也要把 tex/图/结果/交接
    全部收回来，让人能接手，也让复盘官有料可复盘。
    """
    log("应急出版：尝试编译当前稿件并收割全部现有材料")
    本地就绪()
    try:
        d = h.exec("cd 论文 && (xelatex -interaction=nonstopmode -halt-on-error 论文.tex "
                   ">../日志/应急编译.log 2>&1; xelatex -interaction=nonstopmode 论文.tex "
                   ">>../日志/应急编译.log 2>&1); ls -la 论文.pdf 2>/dev/null | wc -l", quiet=True)
        log(f"应急编译完成（PDF 存在={('1' in (d.get('stdout') or ''))}）")
    except Exception as e:
        log(f"应急编译失败（不阻塞收割）：{e}")
    清单 = ["论文/论文.pdf", "审稿/审计报告.json", "交接/实验记录.json", "交接/求解计划.md",
            "交接/题面契约.json", "交接/需求追踪矩阵.json", "交接/典型答卷预测.md", "交接/计划.json"]
    try:
        d = h.exec("ls 论文/*.tex 论文/页/*.png 审稿/*.json 交接/*.json 交接/*.md 2>/dev/null; "
                   "find 求解 论文/图 -type f 2>/dev/null", quiet=True)
        for l in (d.get("stdout") or "").split():
            if l and not l.endswith(":"):
                清单.append(l.replace("/tmp/蜂巢/", ""))
    except Exception as e:
        log(f"应急列清单失败：{e}")
    got = h.harvest(sorted(set(清单)), str(产出目录))
    log(f"应急出版：收割 {len(got)} 文件 → {产出目录}")


def 主流程():
    global LEG_COUNT, 依赖图

    # ---------- S0a 本地基建 ----------
    def _播种():
        log(f"S0 本地基建（档位={配置['档位']}）：清空旧工作根，全量重建")
        # 上一炉产物不删，改名保留作对照（与沙箱时代 mv 蜂巢 蜂巢.old 同义）。
        if any(h.root.iterdir()):
            旧 = h.root.parent / "蜂巢镜像.old"
            shutil.rmtree(旧, ignore_errors=True)
            h.root.rename(旧)
            h.root.mkdir(parents=True)
        本地就绪()
        # 环境要件一次验齐（缺了第一时间炸，不让腿跑到一半才发现）+ 题目 PDF 抽文本。
        d = h.exec(r'''python3 -c "import numpy, pandas, matplotlib, sklearn, openpyxl, docx, pypdf; print('py栈就绪')"
python3 - <<'EOF'
from pypdf import PdfReader
import glob
for pdf in glob.glob('题目/*.pdf'):
    r = PdfReader(pdf)
    txt = "\n".join(p.extract_text() or "" for p in r.pages)
    open('交接/题目原文.txt', 'w', encoding='utf-8').write(txt)
    print(pdf, len(r.pages), '页', len(txt), '字')
EOF
which xelatex gs codex && echo 环境就绪''', timeout_s=120)
        log(f"S0 本地基建: {(d.get('stdout') or '')[:200]}")
    节点("S0:播种", _播种, 阶段="S0")

    # ---------- S0b 读题 + 体检 + 答卷预测 ----------
    def _读题体检():
        legs = [
            ("读题官.md", "把 交接/题目原文.txt（必要时用 pypdf 重抽 题目/ 下PDF）与 数据/ 目录附件清单转成 交接/题面契约.json（按角色规范的完整 schema，需求逐条编号）。同时保留旧格式 交接/题面.json（键：赛事/背景/数据/问题/评分关键/硬约束清单）以兼容下游。完成写 日志/读题.done", "读题"),
            ("数据体检师.md", "对 数据/ 目录下全部附件做全量体检（多文件：逐文件+跨文件关系）。产出 交接/数据档案.json 与 日志/运行日志.md。注意编码、隐藏sheet、合并单元格、多级表头、单位、跨附件主键关联。完成写 日志/体检.done", "体检"),
        ]
        wave(legs, timeout=1300)
        checkpoint(["交接/题面契约.json", "交接/题面.json", "交接/数据档案.json"], "S0-读题体检")
    节点("S0:读题体检", _读题体检, 阶段="S0")

    契约 = get_json("交接/题面契约.json", "题面契约", "读题官.md", "严格按读题官角色规范的题面契约 schema（问题/需求条目/硬约束清单/歧义裁定/附件清单）")
    题面 = get_json("交接/题面.json", "题面") or {}
    档案 = get_json("交接/数据档案.json", "数据档案", "数据体检师.md", "严格按数据体检师角色规范的schema")
    问题数 = len((契约 or {}).get("问题", [])) or len(题面.get("问题", [])) or 3
    log(f"S0 完成：问题数={问题数}")

    # ---------- S0c 答卷预测 + 需求追踪矩阵 ----------
    def _预测与矩阵():
        wave([("答卷预测官.md", "读 交接/题面契约.json（或 交接/题面.json）与 交接/数据档案.json，产出 交接/典型答卷预测.md。完成写 日志/答卷预测.done", "答卷预测")], timeout=1100)
        # 驱动机械生成需求追踪矩阵（全部未落位）
        矩阵 = []
        for q in (契约 or {}).get("问题", []):
            for 项 in (q.get("需求条目") or []):
                矩阵.append({"需求号": str(项.get("需求号", f"{q.get('编号')}-?")), "内容": 项.get("内容", ""),
                             "状态": "未落位", "落位": {"章节": "", "图表": [], "关键数字": ""}})
        if not 矩阵:   # 契约缺需求条目时用问题要求兜底
            for q in (契约 or {}).get("问题", []) or 题面.get("问题", []):
                for i, 要求 in enumerate(q.get("要求", []) or [q.get("标题", "")], 1):
                    矩阵.append({"需求号": f"{q.get('编号')}-{i}", "内容": str(要求),
                                 "状态": "未落位", "落位": {"章节": "", "图表": [], "关键数字": ""}})
        h.exec("echo 矩阵上传", files=[h.f_text("交接/需求追踪矩阵.json", json.dumps(矩阵, ensure_ascii=False, indent=1))], quiet=True)
        log(f"需求追踪矩阵生成：{len(矩阵)} 条")
        checkpoint(["交接/典型答卷预测.md", "交接/需求追踪矩阵.json"], "S0-预测与矩阵")
    节点("S0:预测与矩阵", _预测与矩阵, 阶段="S0")

    # ---------- G0 契约门 ----------
    def _G0返工(明细):
        wave([("读题官.md", f"契约门 G0 未通过，明细：{json.dumps(明细, ensure_ascii=False)[:2000]}。逐条修正 交接/题面契约.json（需求号连续、每条歧义必须给出裁定与理由、附件清单覆盖 数据/ 下全部文件）。完成写 日志/契约返工.done", "契约返工")], timeout=1100)
        checkpoint(["交接/题面契约.json"], "G0-返工")
    节点("G0", lambda: 调度器.门("G0", lambda: 门检("G0"), 返工fn=_G0返工, 状态=状态, log=log).执行(), 阶段="G0")

    # ---------- S1 战略锦标赛 ----------
    def _S1侦察():
        wave([("规划师.md", f"【步1 路线侦察】读 交接/题面契约.json、交接/数据档案.json、交接/典型答卷预测.md、运行时/HMML.md、运行时/方法卡片库.md、运行时/优秀论文标准.md。"
               f"为每问给出 {配置['每问路线数']} 条**真正不同**的技术路线及其原型实验设计（小样数据、单一可比指标、单条原型运行 ≤3 分钟），产出 交接/路线侦察.json。"
               f"本步**不要**输出完整计划。完成写 日志/路线侦察.done", "路线侦察")], timeout=1300)
        checkpoint(["交接/路线侦察.json"], "S1-侦察")
    节点("S1:侦察", _S1侦察, 阶段="S1")

    侦察 = get_json("交接/路线侦察.json", "路线侦察", "规划师.md", "必含 每问的路线数组，每条含 名称/简述/原型实验设计/预期指标")
    路线表 = 解析路线(侦察, 配置["每问路线数"])
    log(f"S1 路线侦察：{ {k: [路线名(r) for r in v] for k, v in 路线表.items()} }")
    # 哪些问真的会开锦标赛：单一真相，供 原型/裁决/建模消费 三处共用，避免三处各算一遍而漂移
    if 配置["开锦标赛"] and 路线表:
        锦标赛问集 = set(sorted(路线表)) if 配置["全问开锦标赛"] else set(sorted(路线表)[-1:])
    else:
        锦标赛问集 = set()
    log(f"S1 锦标赛参赛问：{sorted(锦标赛问集) or '无（未启用）'}")

    def _S1原型():
        if not (配置["开锦标赛"] and 路线表):
            log("S1 锦标赛未启用或无路线，跳过原型步")
            return
        目标问 = sorted(锦标赛问集)
        legs = []
        for bh in 目标问:
            for k, r in enumerate(路线表[bh], 1):
                名 = f"原型_问{bh}_{k}"
                rn = 路线名(r, f"路线{k}")
                legs.append(("建模师.md", f"【锦标赛原型】问题{bh} 路线{k}（{rn}）={json.dumps(r, ensure_ascii=False)[:1800]}。"
                             f"按 交接/路线侦察.json 中该路线的原型实验设计，写 求解/问题{bh}/原型_路线{k}.py（**只写不跑**）："
                             f"必须用真数据但**小样**（抽样/降采样/限迭代），运行必须 ≤3 分钟，只算这条路线的单一可比指标，"
                             f"结果写 求解/问题{bh}/原型结果/路线{k}.json（含 路线名/核心指标键值/用时估计/口径说明）。"
                             f"不要做完整建模、不要画图。完成写 日志/{名}.done", 名))
        if legs:
            wave(legs, timeout=1300)
        for bh in 目标问:
            for k in range(1, len(路线表[bh]) + 1):
                run_script(f"求解/问题{bh}/原型_路线{k}.py", f"跑原型_问{bh}_{k}",
                           budget=配置["原型预算秒"], repairs=1)
        checkpoint_dir("求解", "S1-原型")
    节点("S1:原型", _S1原型, 阶段="S1")

    def _S1裁决():
        if not (配置["开锦标赛"] and 路线表):
            return
        目标问 = sorted(锦标赛问集)
        legs = []
        for bh in 目标问:
            legs.append(("解读师.md", f"【锦标赛裁决】问题{bh}：{len(路线表[bh])} 条路线原型已跑（结果在 求解/问题{bh}/原型结果/，运行日志 日志/跑原型_问{bh}_*.log）。"
                         f"按**实测指标 + 论文价值**双维裁决优胜路线，产出 交接/锦标赛_问题{bh}.json，schema："
                         '{"问题": N, "路线": [{"名称","原型脚本","实测指标":{...},"用时秒","论文价值","败因/胜因"}], '
                         '"优胜": "路线名", "裁决理由": "...", "败者用途": "论文算法对比节素材"}。'
                         f"原型跑失败的路线按失败路线处理（实测指标记 null 并说明），不得阻塞裁决。完成写 日志/锦标赛_问{bh}.done", f"锦标赛_问{bh}"))
        if legs:
            wave(legs, timeout=1300)
        checkpoint([f"交接/锦标赛_问题{bh}.json" for bh in 目标问], "S1-裁决")
    节点("S1:裁决", _S1裁决, 阶段="S1")

    def _S1定稿():
        锦标提示 = ""
        if 配置["开锦标赛"] and 路线表:
            锦标提示 = "读全部 交接/锦标赛_问题*.json，主方法必须采用各问优胜路线，并在 问题清单 的 锦标赛 键记录参赛路线/优胜/依据；败者写入论文算法对比素材。"
        wave([("规划师.md", f"【步2 计划定稿】{锦标提示}产出 交接/求解计划.md 与 交接/计划.json v2。"
               "计划.json 必含顶层键：问题清单 / 论文结构 / 图表规划 / 页数预算 / 叙事主线（一句话全文故事线）/ 偏离点（对照 交接/典型答卷预测.md 刻意差异化的 3-5 条）。"
               "问题清单每项必含：编号/标题/主方法/备选方法/方法理由(引用数据档案具体数字)/本题定制改造{名称,为什么}/"
               "验证方案{基线,交叉印证,防泄漏,灵敏度}/依赖问题(DAG 唯一依据，无依赖=空数组)/锦标赛/蜂群变体(默认空数组)。"
               "**依赖问题必须显式给出且不得成环**。完成写 日志/规划.done", "规划")], timeout=1300)
        checkpoint(["交接/计划.json", "交接/求解计划.md"], "S1-定稿")
    节点("S1:定稿", _S1定稿, 阶段="S1")

    计划 = get_json("交接/计划.json", "计划", "规划师.md",
                    "必含 问题清单/论文结构/图表规划/页数预算/叙事主线/偏离点；问题清单每项必含 依赖问题（数组）")
    if not 计划:
        raise RuntimeError("计划.json 两次都拿不到，终止")
    问题清单 = 计划.get("问题清单", [])
    图规划 = 计划.get("图表规划", [])

    # ---------- G1 战略门（DAG 合法性 + 计划完整性） ----------
    def _G1检查():
        缺 = []
        for k in ("问题清单", "论文结构", "图表规划", "页数预算"):
            if not 计划.get(k):
                缺.append(f"计划缺键:{k}")
        for q in 问题清单:
            for k in ("主方法", "本题定制改造", "验证方案"):
                if not q.get(k):
                    缺.append(f"问{q.get('编号')}缺{k}")
            if q.get("依赖问题") is None:
                缺.append(f"问{q.get('编号')}缺依赖问题字段")
        if not 计划.get("叙事主线"):
            缺.append("计划缺叙事主线")
        if not 计划.get("偏离点"):
            缺.append("计划缺偏离点")
        n图 = len(图规划)
        if n图 < 12:
            缺.append(f"图表规划仅{n图}张(<12)")
        try:
            g = 调度器.构建依赖图(问题清单)
            调度器.拓扑分层(g)
        except Exception as e:
            缺.append(f"依赖图非法:{e}")
        return (not 缺), 缺

    def _G1返工(明细):
        wave([("规划师.md", f"战略门 G1 未通过：{json.dumps(明细, ensure_ascii=False)[:1500]}。逐条补齐并修正 交接/计划.json（尤其依赖问题不得成环、图表规划 16-22 张、叙事主线与偏离点必填）。完成写 日志/计划返工.done", "计划返工")], timeout=1100)
        checkpoint(["交接/计划.json"], "G1-返工")
    节点("G1", lambda: 调度器.门("G1", _G1检查, 返工fn=_G1返工, 状态=状态, log=log).执行(), 阶段="G1")
    计划 = get_json("交接/计划.json", "计划") or 计划
    问题清单 = 计划.get("问题清单", 问题清单)
    图规划 = 计划.get("图表规划", 图规划)

    # ---------- S2 建模求解（依赖 DAG + 红队 + G2/问） ----------
    依赖图 = 调度器.构建依赖图(问题清单)
    try:
        层们 = 调度器.拓扑分层(依赖图)
    except Exception as e:
        log(f"!! 依赖图非法({e})，退化为串行执行")
        层们 = [[int(q["编号"])] for q in 问题清单 if str(q.get("编号", "")).isdigit()]
    log(f"S2 依赖DAG：{依赖图} → 拓扑分层 {层们}")
    问表 = {}
    for q in 问题清单:
        try:
            问表[int(q["编号"])] = q
        except Exception:
            pass

    def 上游摘要(编号):
        """给下游问注入的上游已验证结果摘要（蓝图：下游只准消费过门数字）。"""
        片 = []
        for u in 调度器.上游(依赖图, 编号):
            片.append(f"上游问题{u}（已过 G2）：解读见 交接/结果解读_问题{u}.md，建模笔记 交接/建模笔记_问题{u}.md，"
                      f"结果目录 求解/问题{u}/结果/（只准消费这些已验证数字，不得自行重算上游）")
        return "\n".join(片) if 片 else "本问无上游依赖。"

    def 结果模板(编号):
        """契约/附件给了本问的结果模板文件（2026 A 题起：数据/附件3/resultN.xlsx）则返回镜像里的路径，否则 None。"""
        try:
            c = sorted((h.root / "数据").rglob(f"result{编号}.xlsx"))
            return c[0] if c else None
        except Exception:
            return None

    def 结果文件核对(编号):
        """R58（2026-09-11 A 题问3）：G2 只看解读/红队/门检，不看契约要求交的结果模板文件——问3 建模师给单元格只设了
        number_format="0.0000"、存的是未舍入浮点，角格也改了名，门照样 PASS。这里在过门后跑只读核对 流水线/验证/核对结果模板.py，
        **只记日志不判门**（要不要进 G2 判据是用户的决定）；'!!' 前缀让监督正则看见，操盘手在边界派回炉腿。"""
        模板 = 结果模板(编号)
        if not 模板:
            return
        try:
            这里 = pathlib.Path(__file__).resolve().parent
            py = 这里 / "运行时/venv/bin/python3"
            r = subprocess.run([str(py) if py.exists() else "python3", str(这里 / "验证/核对结果模板.py"), str(h.root), str(模板.parent),
                                f"--只={模板.name}"], capture_output=True, text=True, timeout=300)
            坏 = [l.strip() for l in (r.stdout or "").splitlines() if l.strip().startswith("✗")]
            if r.returncode == 0 and not 坏:
                log(f"问{编号} 结果文件核对：{模板.name} 全部合格")
            else:
                log(f"!! 问{编号} 结果文件核对不合格（R58：不判门，需在边界派建模师回炉）：" + ("；".join(坏)[:400] if 坏 else (r.stderr or "").strip()[-200:]))
        except Exception as e:
            log(f"问{编号} 结果文件核对未能执行（不阻塞）：{e}")

    def 跑一问(编号, 重算=False):
        """一问的完整子链：建模→执行→解读⟲返工→红队复算→仲裁。返回 (解读结论, 红队结论)。"""
        q = 问表.get(编号, {})
        qdir = f"求解/问题{编号}"
        标 = "重算" if 重算 else ""
        优胜 = ""
        # 只对**真的开过锦标赛**的问去读裁决文件：快速档只有最后一问参赛，
        # 对未参赛的问去 get_json 会白等两轮重试并刷无意义的"解析失败"日志。
        锦 = get_json(f"交接/锦标赛_问题{编号}.json", f"锦标赛{编号}") if 编号 in 锦标赛问集 else None
        if 锦 and 锦.get("优胜"):
            优胜 = f"锦标赛优胜路线={锦.get('优胜')}（裁决理由：{str(锦.get('裁决理由'))[:200]}），主方法必须落实该路线。"
        建模任务 = (f"你负责问题{编号}{标}，按 交接/计划.json 问题{编号} 的主方法/定制改造/验证方案执行。{优胜}\n{上游摘要(编号)}\n"
                    f"产物：①交接/建模笔记_问题{编号}.md ②{qdir}/求解_问题{编号}.py（**只写不跑**，结果落 {qdir}/结果/，键名中文含含义）"
                    f"③交接/假设台账_问题{编号}.json（每条：假设号/假设/依据/灵敏度义务/检验结果；**检验结果必须对应求解脚本里真实存在的检验并引用结果键**）"
                    f"④实验记录追加。⑤若求解依赖自生成的合成/仿真输入（含随机种子生成的观测样本），必须把它们冻结导出到 数据/问题{编号}_冻结合成输入/（含 输入清单.json：每个文件的用途、生成方式、参数、SHA256），结果声明的口径指明用了哪份冻结输入——红队只能拿它做同口径复算，导不出就等于头条数字无人能验。"
                    f"脚本运行时间控制在12分钟内。完成写 日志/建模_问题{编号}{标}.done")
        # P11（R58）：契约列了本问的结果模板文件时，任务文本点名写出纪律——问1/2 建模师自觉 round 了，问3 只设显示格式没舍入存值。
        # 只追加任务文本，不动角色文件（角色只在续跑同步，且这是任务级要求）。
        模板 = 结果模板(编号)
        if 模板:
            建模任务 += (f"\n⑥结果模板文件纪律：契约要求把完整结果写进 {qdir}/结果/{模板.name}（模板 {模板.relative_to(h.root)}）——文件名、工作表名、"
                        f"首行距离网格、A 列时间步与模板及契约裁定一致；**所有存入单元格的数值（含时间列与契约要求补的实际结束行）先按契约小数位 round 再写入，"
                        f"只设 number_format 不算**；角格 A1 沿用模板文字；写完用 openpyxl 读回自检（行列数、步长、末行时刻、每个数值 round(v,位)==v）并写进 "
                        f"{qdir}/结果/导出核验.json。")
        if 重算:
            建模任务 = f"【级联重算】上游结果已变更，问题{编号}必须按新上游数字重做。" + 建模任务
        wave([("建模师.md", 建模任务, f"建模_问题{编号}{标}")], timeout=1300)
        run_script(f"{qdir}/求解_问题{编号}.py", f"执行_问题{编号}{标}")
        结论 = ""
        for 轮 in (1, 2):
            r = wave([("解读师.md", f"核验并解读问题{编号}（运行日志 日志/执行_问题{编号}{标}.log，结果在 {qdir}/结果/）。五项正确性协议一票否决。"
                       f"通过产出 交接/结果解读_问题{编号}.md（含论文引用清单：数值+来源文件+键名）与 交接/结果声明_问题{编号}.json"
                       '（供红队独立复算用，schema：{"问题":N,"核心指标":{"指标名":数值},"口径说明":{"指标名":"一句话口径"},"自检指标":{"指标名":数值}}，'
                       '**核心指标只放独立实现能复现的量（对给定数据的估计值/诊断量/区间）；方法自检误差放 自检指标，红队不比对**）；'
                       f"不过写 交接/返工单_问题{编号}.md。完成写 日志/解读_问题{编号}{标}_轮{轮}.done（首行PASS/FAIL）", f"解读_问题{编号}{标}_轮{轮}")], timeout=1100)
            结论 = r.get(f"解读_问题{编号}{标}_轮{轮}", "")
            log(f"问题{编号}{标} 解读轮{轮}: {结论[:40]}")
            if 结论.startswith("PASS") or not budget_ok():
                break
            wave([("建模师.md", f"修复任务：问题{编号}被打回，返工单 交接/返工单_问题{编号}.md，逐条落实，直接改 {qdir}/求解_问题{编号}.py 与建模笔记与假设台账，返工过程追加实验记录。只改不跑。完成写 日志/修复_问题{编号}{标}_{轮}.done", f"修复_问题{编号}{标}_{轮}")], timeout=1100)
            run_script(f"{qdir}/求解_问题{编号}.py", f"执行_问题{编号}{标}_修{轮}", repairs=1)
        if 配置["开红队"] and budget_ok():
            红结论 = 红队复算(编号, 标)
        else:
            # 红队被跳过时必须留下**声明**：否则 交接/红队_问题N.json 根本不存在，
            # 门检撞 "红队报告缺失" 一票否决 → G2 在数学上不可能通过。
            # 实测（20260827 轮）问3 全部工作发生在预算耗尽之后，红队被静默跳过，
            # 于是白烧 2 次 G2 返工 + 3 条变体升格约 1 小时，最终仍降级放行。
            # 驱动侧本就认定"红队缺失不阻塞"，此处把该口径显式落成产物交给门检。
            红结论 = 声明红队未复算(编号, "预算耗尽" if 配置["开红队"] else "配置关闭红队")
        checkpoint([f"交接/建模笔记_问题{编号}.md", f"交接/结果解读_问题{编号}.md", f"交接/假设台账_问题{编号}.json",
                    f"交接/结果声明_问题{编号}.json", f"交接/红队_问题{编号}.json", "交接/实验记录.json"], f"S2-问{编号}{标}")
        checkpoint_dir(f"{qdir}", f"S2-问{编号}{标}全量")
        return 结论, 红结论

    def 声明红队未复算(编号, 原因):
        """红队被跳过时写明确声明，保持"产物存在"不变量并留痕供人工复核。

        注意与"真失败"的区别：红队腿**跑了但没产出报告**时不会走这里，
        门检仍按 红队报告缺失 一票否决——那是真缺陷，不该被软路径掩盖。
        """
        声明 = {"问题": 编号, "复算方式": "未执行", "复算指标": {}, "口径说明": {},
                "结论": "未复算", "跳过原因": 原因, "分歧明细": []}
        try:
            h.exec("mkdir -p 交接",
                   files=[Hive.f_text(f"交接/红队_问题{编号}.json",
                                      json.dumps(声明, ensure_ascii=False, indent=2))],
                   quiet=True)
            log(f"问题{编号} 红队未复算（{原因}）→ 已写声明，门检软路径放行并留痕")
        except Exception as e:
            log(f"问题{编号} 红队未复算声明写入失败（不阻塞）：{e}")
        return "未复算"

    def 冻结包在(编号):
        """建模师导出的冻结合成输入包（数据/问题N_冻结合成输入/输入清单.json）是否已在工作根。
        首跑问1实测（20260909）：建模在自生成的 24 份合成观测上定义头条指标，红队拿不到同一份输入只能 8 项全填 null。"""
        d = h.exec(f"[ -f 数据/问题{编号}_冻结合成输入/输入清单.json ] && echo 冻结包在; true", quiet=True, timeout_s=T_轻)
        return "冻结包在" in (d.get("stdout") or "")

    def 声明刷新(编号, 标, 缘由):
        """建模改数之后、红队复核之前：解读师按最新 结果/ 逐键刷新 交接/结果声明_问题N.json（不裁定）。
        二跑实测（20260909 18:05）：仲裁返工重跑后声明仍是改前那份，红队复核拿新脚本撞旧声明，6 处"不齐"全是假分歧。"""
        wave([("解读师.md", f"【声明刷新】问题{编号}：建模师已按{缘由}修正并重跑（最新一份 日志/执行_问题{编号}*.log，结果在 求解/问题{编号}/结果/）。"
               f"本腿只做一件事：按最新结果逐键刷新 交接/结果声明_问题{编号}.json（核心指标数值与口径说明；键名尽量不变，结果键确已变化才增删），"
               f"并同步 交接/结果解读_问题{编号}.md 的论文引用清单数值。不做裁定、不写 PASS/FAIL（裁定在红队复核之后另派）。"
               f"完成写 日志/声明刷新_问题{编号}{标}.done", f"声明刷新_问题{编号}{标}")], timeout=1100)

    def _回炉边界句():
        """R42：S5 回炉序列先算→后图→后文，解读腿跑在文/图腿之前；不带这句它会把「正文/图未同步」算进一票否决（07:45 问3 假 FAIL 进返工）。"""
        return ("【回炉序列边界】本轮处于 S5 回炉（先算→后图→后文）：论文正文/附录/图与新数的同步由随后的回炉文/回炉图腿完成，"
                "不属于本次 PASS/FAIL 判据；只核验 求解/*/结果/、结果声明与结果解读本身的五项正确性，正文未同步的条目裁定写「待文腿同步」。"
                if str(运行态.get("阶段", "")).startswith("S5") else "")

    def 红队复算(编号, 标="", 深度=0):
        """红队独立复算头条数字 + 驱动仲裁（§4.4）。返回 对齐/不齐/缺失/口径差异成立/仲裁后修正。
        深度 0 = 首轮：仲裁裁定建模错 → 建模返工 → 跑 → **红队复核（深度 1）** → 解读_仲裁后；
        深度 1 = 复核轮：建模改过数之后重新独立复算；仍不齐可再仲裁、可再让建模返工，但不再递归复核、也不派解读（由调用方收尾）。
        R33（20260909 首跑问1）：仲裁返工后红队报告仍是改前那份（8 项全 null），解读_仲裁后据旧报告判"0/8 可比"→ FAIL；
        G2 的两轮返工改的都是建模侧文件，红队那份永远不刷新 → 必然升格/降级。建模改了数，红队就必须重新撞一次。"""
        qdir = f"求解/问题{编号}"
        冻结句 = (f"**建模师已导出冻结合成输入包** 数据/问题{编号}_冻结合成输入/（含 输入清单.json，允许读）：结果声明里凡定义在合成/冻结输入上的指标，"
                  f"必须逐条用该包里的输入做同口径复算，不许自造替代输入。" if 冻结包在(编号) else
                  f"若结果声明里的指标定义在建模师自生成的合成/仿真输入上、而 数据/ 下没有 问题{编号}_冻结合成输入/ 包，"
                  f"这些键的 复算值 填 null，并在 口径说明 写明'需要建模师导出冻结输入包到 数据/问题{编号}_冻结合成输入/'。")
        复核句 = (f"这是**复核轮**：建模师已按裁定修正并重跑，交接/结果声明_问题{编号}.json 已按新结果刷新。本腿只做一件事——重新写 {qdir}/复算.py"
                  f"（只写不跑；上一版口径没错可沿用，但脚本必须能在当前 数据/ 上跑通并重新落 {qdir}/红队结果/）。比对由驱动跑完脚本后另派一腿做，"
                  f"**本腿写完脚本就算完成，必须写 done 标记**。" if 深度 else "")
        wave([("红队.md", f"独立复算问题{编号}的头条数字。{复核句}**只准读**：交接/题面契约.json、交接/数据档案.json、数据/ 原始数据、交接/结果声明_问题{编号}.json。{冻结句}"
               f"**严禁读**建模师的 {qdir}/求解_问题{编号}.py、交接/建模笔记_问题{编号}.md、交接/结果解读_问题{编号}.md。"
               f"自己独立实现 {qdir}/复算.py（只写不跑），结果落 {qdir}/红队结果/。按角色文件「两套口径」：每个核心指标同时落 按声明口径 与 独立口径 两个数（口径说明四段逐项对齐）；"
               f"脚本自检不致命、只用 python3。完成写 日志/红队_问题{编号}{标}.done", f"红队_问题{编号}{标}")], timeout=1300)
        run_script(f"{qdir}/复算.py", f"跑红队_问题{编号}{标}", budget=900, repair_role="红队.md", repairs=1)
        wave([("红队.md", f"读 {qdir}/红队结果/ 与 交接/结果声明_问题{编号}.json，逐键比对，产出 交接/红队_问题{编号}.json（严格按角色规范 schema：复算方式/复算指标/口径说明/结论/分歧明细）。"
               f"结论只按 按声明口径 的数判（逐键 ≤1% 即 对齐）；独立口径的差异写进 分歧明细（类型=口径）并填 口径对照 字段，不许因口径不同判 不齐。"
               f"结论只能是 对齐 或 不齐。完成写 日志/红队报告_问题{编号}{标}.done", f"红队报告_问题{编号}{标}")], timeout=1100)
        报告 = get_json(f"交接/红队_问题{编号}.json", f"红队{编号}", "红队.md", "严格按 红队 角色 schema")
        if not 报告:
            log(f"问题{编号} 红队报告缺失，记为缺失不阻塞")
            return "缺失"
        结论 = str(报告.get("结论", "")).strip()
        全部分歧 = [d for d in (报告.get("分歧明细") or []) if isinstance(d, dict)]
        # R56（2026-09-11 A 题问1 12:55:00）：P1 让红队把独立口径的差异也写进 分歧明细（类型=口径）只作交叉印证，判齐只看按声明口径；
        # 这里原来不看 类型，一条 4.81% 的口径行（表面半格采样带均值 vs 表面点值）就把结论=对齐 的报告派成了仲裁腿。口径行只记数，不触发仲裁。
        口径行 = [d for d in 全部分歧 if str(d.get("类型", "")).strip() == "口径"]
        分歧 = [d for d in 全部分歧
                if str(d.get("类型", "")).strip() != "口径" and abs(float(d.get("相对差", 0) or 0)) > 配置["红队容差"]]
        if 结论.startswith("对齐") and not 分歧:
            log(f"问题{编号} 红队结论=对齐" + (f"（口径差异 {len(口径行)} 条只作交叉印证，不计入不齐）" if 口径行 else "")
                + ("（复核轮）" if 深度 else ""))
            return "对齐"
        log(f"!! 问题{编号} 红队不齐（{len(分歧)} 处超容差）→ 派仲裁腿" + ("（复核轮）" if 深度 else ""))
        # 仲裁台账是有状态的待办表（门检按 消解状态 结账）：复核轮再仲裁会整份重写，先把上一份存档，
        # 让仲裁腿看得见"上轮裁了什么、建模改了什么"，也给人工复盘留证据链。
        存档句 = ""
        if 深度:
            h.exec(f"[ -f 交接/仲裁_问题{编号}.json ] && cp 交接/仲裁_问题{编号}.json 交接/仲裁_问题{编号}_前轮{标}.json; true", quiet=True, timeout_s=T_轻)
            存档句 = (f"这是复核轮仲裁：上一轮台账已存档为 交接/仲裁_问题{编号}_前轮{标}.json（含建模师回写的 消解状态/消解证据），"
                      f"本轮只对当前新数独立裁定，不许照抄上轮结论；上轮已消解且本轮已对齐的条目不必再列。")
        wave([("解读师.md", f"【仲裁】问题{编号} 红队复算与建模结果不一致。{存档句}你**可以**读双方代码（{qdir}/求解_问题{编号}.py 与 {qdir}/复算.py）、"
               f"双方结果（{qdir}/结果/ 与 {qdir}/红队结果/）、交接/红队_问题{编号}.json。逐条定责：是建模错、红队错、还是口径差异成立。"
               # schema 不在这里内联：它的权威定义在 角色/解读师.md「仲裁台账」节的 json 围栏，
               # 由 验证/契约核对.py 与门检对账。提示词里再抄一份就是第二事实来源——
               # 历史上「提示词写已解释、门检只认已消解」正是这么来的。
               f"产出 交接/仲裁_问题{编号}.json，schema 严格照你角色文件「仲裁台账」节。"
               f"若裁定建模错，同时在 交接/返工单_问题{编号}.md 写明必须修什么。完成写 日志/仲裁_问题{编号}{标}.done", f"仲裁_问题{编号}{标}")], timeout=1100)
        仲 = get_json(f"交接/仲裁_问题{编号}.json", f"仲裁{编号}") or {}
        应改建模 = [x for x in (仲.get("逐项") or []) if str(x.get("应改方", "")).startswith("建模")]
        checkpoint([f"交接/仲裁_问题{编号}.json", f"交接/红队_问题{编号}.json"], f"S2-仲裁问{编号}{标}")
        if 应改建模 and budget_ok():
            log(f"仲裁裁定建模需返工（{len(应改建模)} 项）→ 重跑问{编号}子链尾部" + ("（复核轮）" if 深度 else ""))
            wave([("建模师.md", f"【仲裁返工】问题{编号}：仲裁裁定建模侧有误，见 交接/仲裁_问题{编号}.json 与 交接/返工单_问题{编号}.md。"
                   f"逐条修 {qdir}/求解_问题{编号}.py 与建模笔记，追加实验记录。只改不跑。"
                   f"若裁定涉及红队拿不到你的合成/仿真输入，必须把这些输入冻结导出到 数据/问题{编号}_冻结合成输入/（含 输入清单.json：用途/生成方式/参数/SHA256）。"
                   f"**改完必须回写仲裁台账**：对 交接/仲裁_问题{编号}.json 中每条 应改方=建模 的条目，"
                   f"把该条的 消解状态 改为 已消解（确已按裁定修正）或 已解释（经复核裁定不成立），"
                   f"并写 消解证据（改了哪个文件哪一处、新数值是多少、或为何裁定不成立）——"
                   f"证据必须具体可核，禁止空话；未真正处理的条目不许标已消解。"
                   f"完成写 日志/仲裁返工_问题{编号}{标}.done", f"仲裁返工_问题{编号}{标}")], timeout=1100)
            run_script(f"{qdir}/求解_问题{编号}.py", f"执行_问题{编号}{标}_仲裁后", repairs=1)
            if 深度 == 0:
                # R33：建模改了数，红队那份报告就作废了——先刷新声明、再复核（深度 1），解读师才有新鲜的可比结果可核。
                声明刷新(编号, f"{标}_仲裁后", "仲裁裁定")
                log(f"问题{编号} 仲裁返工后红队复核：{红队复算(编号, 标=f'{标}_复核', 深度=1)}")
                wave([("解读师.md", f"问题{编号} 仲裁返工后重新核验解读（红队已按新数复核：交接/红队_问题{编号}.json；仲裁台账 交接/仲裁_问题{编号}.json）。"
                       f"若复核轮里建模又返工过一次（存在 日志/仲裁返工_问题{编号}{标}_复核.done），红队报告比对的是那次返工前的声明——"
                       f"这部分分歧以仲裁台账的 消解证据 与最新 求解/问题{编号}/结果/ 为准判断是否已消解。"
                       f"五项正确性协议一票否决，更新 交接/结果解读_问题{编号}.md 与 交接/结果声明_问题{编号}.json。{_回炉边界句()}"
                       f"完成写 日志/解读_问题{编号}{标}_仲裁后.done（首行PASS/FAIL）", f"解读_问题{编号}{标}_仲裁后")], timeout=1100)
            return "仲裁后修正"
        return "口径差异成立" if 仲 else "不齐"

    def G2门(编号, 只复检=False):
        次 = {"返工": 0}

        def _检查():
            return 门检(f"G2:{编号}")

        def _红队复核(标):
            # R33：返工/升格改了求解就可能改了头条数字，红队报告随之作废——门检读的是 交接/红队_问题N.json 与仲裁台账，
            # 不重新复算，解读师与门检看到的永远是改前的比对。复核轮（深度 1）可再仲裁、可再让建模返工，不再递归。
            if 配置["开红队"] and budget_ok():
                log(f"问题{编号} {标.strip('_')} 后红队复核：{红队复算(编号, 标=标, 深度=1)}")

        def _指纹():
            # 求解脚本 + 结果目录的内容指纹：返工若只改了台账/实验记录措辞（三跑问1：11 条科学尝试含流程词），
            # 数没变、声明仍有效，就不必再烧一轮 声明刷新 + 红队复核（约 25 分钟）。
            d = h.exec(f"cat 求解/问题{编号}/求解_问题{编号}.py 求解/问题{编号}/结果/* 2>/dev/null | shasum -a 256 | cut -c1-16",
                       quiet=True, timeout_s=T_轻)
            return (d.get("stdout") or "").strip()

        def _只剩流程词(明细):
            项 = 明细 if isinstance(明细, list) else [str(明细)]
            return bool(项) and all("科学尝试含流程词" in str(x) for x in 项)

        def _清洗(明细, 场合):
            # 门只剩"科学尝试含流程词"一项：这是记录措辞问题，不是建模问题——机械降级为流程事件即可出清，
            # 不派建模返工、更不升格蜂群（三跑问1：两次返工都没清干净，第三次就要为措辞烧一小时变体腿）。
            d = h.exec(f"python3 bin/门检.py 清洗实验记录 {编号}", quiet=True, timeout_s=T_轻)
            log(f"问题{编号} G2 {场合}只剩实验记录流程词一项 → 机械降级为流程事件：{(d.get('stdout') or '').strip()[:120]}")

        def _返工(明细):
            if _只剩流程词(明细):
                _清洗(明细, "返工前"); return
            次["返工"] += 1
            指纹前 = _指纹()
            wave([("建模师.md", f"正确性门 G2（问题{编号}）未通过：{json.dumps(明细, ensure_ascii=False)[:1800]}。"
                   f"逐条修 求解/问题{编号}/求解_问题{编号}.py、交接/建模笔记_问题{编号}.md、交接/假设台账_问题{编号}.json（检验结果必须真实非空并引用结果键）。只改不跑。"
                   f"**若明细含“仲裁裁定建模需修正但未消解”，必须回写 交接/仲裁_问题{编号}.json**："
                   f"对每条 应改方=建模 的条目，把 消解状态 置为 已消解（确已修正）或 已解释（经复核裁定不成立），"
                   f"并写 消解证据（具体到文件/位置/新数值，或不成立的理由）；未真正处理的不许标已消解。"
                   f"若头条指标定义在自生成的合成/仿真输入上，冻结输入包 数据/问题{编号}_冻结合成输入/ 必须与当前脚本一致（改了生成方式就重新导出）。"
                   f"完成写 日志/G2返工_问{编号}.done", f"G2返工_问{编号}")], timeout=1100)
            run_script(f"求解/问题{编号}/求解_问题{编号}.py", f"执行_问题{编号}_G2返工", repairs=1)
            if 指纹前 and _指纹() == 指纹前:
                log(f"问题{编号} G2 返工未改求解脚本与结果（指纹 {指纹前[:8]}），声明与红队报告仍有效，跳过声明刷新与红队复核")
                红队句 = "本轮返工未改求解与结果，现有 交接/红队_问题{0}.json 仍有效".format(编号)
            else:
                声明刷新(编号, f"_G2返工{次['返工']}", "G2 返工单")
                _红队复核(f"_G2返工{次['返工']}")
                红队句 = "红队已按新数复核：交接/红队_问题{0}.json".format(编号)
            wave([("解读师.md", f"问题{编号} G2 返工后重新核验解读（{红队句}；仲裁台账 交接/仲裁_问题{编号}.json）。"
                   f"五项正确性协议一票否决，更新 交接/结果解读_问题{编号}.md 与 交接/结果声明_问题{编号}.json。{_回炉边界句()}完成写 日志/解读_问题{编号}_G2返工.done（首行PASS/FAIL）", f"解读_问题{编号}_G2返工")], timeout=1100)

        def _升格(明细):
            if _只剩流程词(明细):
                _清洗(明细, "升格前"); return
            def 跑变体():
                for k in (1, 2, 3):
                    run_script(f"求解/问题{编号}/升格{k}/求解.py", f"跑升格_问{编号}_{k}", budget=900, repairs=1)
            升格蜂群(f"G2问{编号}", "建模师.md",
                     f"【升格蜂群·变体{{变体号}}】问题{编号} 的 G2 正确性门连败两次（明细：{json.dumps(明细, ensure_ascii=False)[:900]}）。"
                     f"本变体切入：{{切入}}。重写 求解/问题{编号}/升格{{变体号}}/求解.py（只写不跑，结果落同目录 结果/，与主线同口径核心指标），"
                     f"并写 交接/升格笔记_问题{编号}_{{变体号}}.md 说明改了什么。",
                     f"【升格裁决】问题{编号}：主线与 3 条升格变体（求解/问题{编号}/升格*/结果/，变体脚本已由驱动跑完，运行日志 日志/跑升格_问{编号}_*.log）"
                     f"按同口径核心指标+五项正确性协议裁决优胜，"
                     f"把优胜结果整理进 求解/问题{编号}/结果/（规范键名），更新 交接/结果解读_问题{编号}.md、交接/结果声明_问题{编号}.json、"
                     f"交接/假设台账_问题{编号}.json，并写 交接/升格裁决_问题{编号}.json。"
                     f"**同时结清仲裁台账**：对 交接/仲裁_问题{编号}.json 中每条 应改方=建模 的条目，"
                     f"依优胜方案的实际情况把 消解状态 写成 已消解 或 已解释，并附具体 消解证据。",
                     变体后=跑变体)
            # R34 ②：门检认的是最新一条 解读_问题N*.done——升格裁决后不派解读腿，门检永远读到返工那轮的 FAIL，升格后必降级。
            _红队复核("_升格后")
            wave([("解读师.md", f"问题{编号} 升格裁决后重新核验解读：优胜方案已整理进 求解/问题{编号}/结果/（见 交接/升格裁决_问题{编号}.json），"
                   f"红队已按新数复核（交接/红队_问题{编号}.json；仲裁台账 交接/仲裁_问题{编号}.json）。五项正确性协议一票否决，"
                   f"更新 交接/结果解读_问题{编号}.md 与 交接/结果声明_问题{编号}.json。{_回炉边界句()}完成写 日志/解读_问题{编号}_升格后.done（首行PASS/FAIL）", f"解读_问题{编号}_升格后")], timeout=1100)
        if 只复检:   # R44：本炉已为该问烧过 返工×2+升格 并降级放行，回炉后只复检一次，不再重复整套（每轮省 1.5–2 h）
            log(f"门[G2:问{编号}] 本炉已升格并降级放行过 → 回炉后只复检一次，不再返工/升格（R44）")
            return 调度器.门(f"G2:问{编号}", _检查, 返工fn=None, 升格fn=None, 状态=状态, log=log).执行()
        return 调度器.门(f"G2:问{编号}", _检查, 返工fn=_返工, 升格fn=_升格, 状态=状态, log=log).执行()

    for 层号, 层 in enumerate(层们, 1):
        待跑 = [bh for bh in 层 if not 状态.已完成(f"S2:问{bh}")]
        跳过 = [bh for bh in 层 if bh not in 待跑]
        if 跳过:
            log(f"S2 第{层号}层：问{跳过} 已完成跳过（续跑）")
        if not 待跑:
            continue
        等待说明 = ""
        if 层号 > 1:
            等待说明 = f"（上游 {sorted(set(sum([调度器.上游(依赖图, b) for b in 待跑], [])))} 已过 G2）"
        log(f"S2 第{层号}层启动：问{待跑} {'并行' if len(待跑) > 1 else ''}{等待说明}")
        运行态["阶段"] = f"S2:第{层号}层"
        状态.设阶段(f"S2:第{层号}层")
        # 同层建模腿合并进一个 wave 并行；执行/解读/红队按问顺序推进（脚本执行本身是容器内并发的瓶颈，逐问更稳）
        for bh in 待跑:
            # 预算护栏必须逐问检查：S2 整体是一个"阶段"，节点() 只在进 S2 前查一次，
            # 而 S2 内部要跑 3 个问 × (建模+执行+解读循环+红队+仲裁+G2门+可能的升格)，
            # 实测可长达十几小时——不逐问查就等于整个 S2 不受护栏约束（本轮实测超支 6.4h）。
            if not budget_ok():
                用时 = (time.time() - T_START) / 3600
                log(f"!! 预算护栏：腿={LEG_COUNT}/{MAX_LEGS} 用时={用时:.1f}/{MAX_HOURS}h，"
                    f"问{bh} 不再开工 → 中止 S2 转收尾（已完成的问保留）")
                raise 预算耗尽(f"S2 问{bh} 前预算耗尽（腿{LEG_COUNT} 用时{用时:.1f}h）")
            结论, 红结论 = 跑一问(bh)
            放行, 降级 = G2门(bh)
            状态.设问题门(bh, "PASS" if 放行 else ("降级放行" if 降级 else "FAIL"))
            log(f"S2 问{bh} 过门={放行}{'（降级放行）' if 降级 else ''} 解读={结论[:20]} 红队={红结论}")
            结果文件核对(bh)
            # 每问收口时机械清洗一次实验记录：降级放行的问其 G2 不再检查，含流程词的科学尝试若留着会进 S4 过程感素材（问2 降级时剩 7 条）。
            d = h.exec(f"python3 bin/门检.py 清洗实验记录 {bh}", quiet=True, timeout_s=T_轻)
            log(f"问{bh} 收口清洗：{(d.get('stdout') or '').strip()[:80]}")
            状态.标记完成(f"S2:问{bh}")

    def 级联重算(源问):
        """上游数字变更 → 依赖它的全部下游作废重跑（蓝图 S2 细则 3）。返回受影响问列表。"""
        下游 = 调度器.全部下游(依赖图, 源问)
        if not 下游:
            log(f"级联重算(问{源问})：无下游，跳过")
            return []
        log(f"!! 级联重算(问{源问}) → 作废下游 {下游} 并按拓扑序重跑")
        状态.加级联(下游)
        for bh in 下游:
            状态.取消完成(f"S2:问{bh}")
            状态.设问题门(bh, "PENDING")
        序 = [bh for 层 in 层们 for bh in 层 if bh in 下游]
        for bh in 序:
            if not budget_ok():
                log(f"级联重算：预算耗尽，问{bh} 未重跑")
                break
            跑一问(bh, 重算=True)
            放行, 降级 = G2门(bh)
            状态.设问题门(bh, "PASS" if 放行 else ("降级放行" if 降级 else "FAIL"))
            状态.标记完成(f"S2:问{bh}")
            状态.清级联(bh)
        return 下游

    运行态["级联重算"] = 级联重算

    # ---------- S3 图证生产 ----------
    def _S3绘图():
        波数 = 3 if len(图规划) > 10 else 2
        每波 = (len(图规划) + 波数 - 1) // 波数
        legs = []
        for i in range(波数):
            名单 = 图规划[i * 每波:(i + 1) * 每波]
            if not 名单:
                continue
            legs.append(("绘图师.md", f"本次负责这{len(名单)}张图：{json.dumps(名单, ensure_ascii=False)}。"
                         f"刻意偏离 交接/典型答卷预测.md 的图表套路。脚本命名 求解/问题X/绘图_<图名>.py（公共图放 求解/公共/），"
                         f"写 求解/成图{i+1}.sh 依次执行本批。图注条目写 交接/图注素材_{i+1}.json。完成写 日志/绘图{i+1}.done", f"绘图{i+1}"))
        wave(legs, timeout=1300)
        for i in range(1, 波数 + 1):
            d = h.exec(f"[ -f 求解/成图{i}.sh ] && echo ok", quiet=True)
            if "ok" in (d.get("stdout") or ""):
                run_script(f"求解/成图{i}.sh", f"成图{i}", budget=900, repairs=2)
        checkpoint([f"交接/图注素材_{i}.json" for i in (1, 2, 3)], "S3-图注素材")
        checkpoint_dir("求解", "S3-全量含图")
    节点("S3:绘图", _S3绘图, 阶段="S3")

    def _S3图评循环():
        # 回路协议：每张图的意见有身份（定位=png 路径）；修过的图下一轮让图评师看新旧两版做相对判断；
        # 判「更差」的图回退脚本与图片（病根台账 R1/R4：上轮 6.17→6.60，低分 11→10 张，轮数上限退出）。
        台 = 回路.台账(台账路径("图评台账"), 前缀="图")
        prev = None
        上轮快照 = {}
        for 轮 in range(1, 配置["图评轮数"] + 1):
            d = h.exec("find 求解 -name '*.png' | sort", quiet=True, timeout_s=T_轻)
            pngs = [l for l in (d.get("stdout") or "").split() if l.endswith(".png")]
            log(f"S3 图评轮{轮}：现有成图 {len(pngs)} 张")
            if not pngs:
                return
            脚本们 = [re.sub(r"/图片/([^/]+)\.png$", r"/绘图_\1.py", p) for p in pngs]
            本轮快照 = 镜像快照(pngs + 脚本们)
            # 上一轮修过的图：把旧版图片放进容器，让图评师新旧对照
            修过 = sorted({x["定位"] for x in 台.条目 if x.get("状态") == 回路.待复核 and x["定位"] in 上轮快照 and x["定位"] in pngs})
            配对文件 = []
            for pth in 修过:
                旧路径 = f"审稿/图快照/R{轮-1}/{pathlib.Path(pth).name}"
                q = 镜像目录 / 旧路径
                q.parent.mkdir(parents=True, exist_ok=True)
                q.write_bytes(上轮快照[pth])
                配对文件.append(h.f_local(旧路径, q))
            if 配对文件:
                h.exec("echo 图快照", files=配对文件, quiet=True, timeout_s=T_轻)
            台账文本 = ("\n【上一轮台账】\n" + 台.渲染给评审腿(裁定文件="审稿/裁定_{名}.json")) if 轮 > 1 and 台.条目 else ""
            名们 = []
            其余 = [p for p in pngs if p not in 修过]
            批次 = [("配对", 修过[i:i + 3]) for i in range(0, len(修过), 3)] + [("单看", 其余[i:i + 7]) for i in range(0, len(其余), 7)]
            for k, (种, 批) in enumerate(批次, 1):
                名 = f"图评R{轮}_{k}"
                名们.append(名)
                if 种 == "配对":
                    图们 = []
                    for pth in 批:
                        图们 += [f"审稿/图快照/R{轮-1}/{pathlib.Path(pth).name}", pth]
                    清单 = "\n".join(f"旧版={图们[2*i]}  新版={pth}" for i, pth in enumerate(批))
                    说明 = (f"以下 {len(批)} 张图上一轮被修改过，附图顺序为 旧1,新1,旧2,新2…：\n{清单}\n"
                            f"每张图按角色文件「配对评审协议」先对新版给相对判断，条目里加 \"相对判断\": \"更好|持平|更差\"；分数按新版打。")
                else:
                    图们 = 批
                    说明 = f"逐张审以下图（附图顺序对应）：\n" + "\n".join(批)
                图片腿(名, "图评师.md",
                      f"{说明}\n输出 JSON 数组写 审稿/{名}.json：[{{\"图\":路径,\"分数\":x,\"问题\":[...],\"修改指令\":[...],\"相对判断\":\"（仅配对图）\"}}]。"
                      f"各图的独有信息与结论一句话见 交接/计划.json 图表规划。{台账文本.replace('{名}', 名)}\n完成写 日志/{名}.done", 图们)
            等腿(名们, timeout=1100)
            条目们 = {}
            分们 = []
            for 名 in 名们:
                v = get_json(f"审稿/{名}.json", 名) or []
                if isinstance(v, dict):
                    v = v.get("图评", []) or []
                for it in v:
                    if not isinstance(it, dict):
                        continue
                    try:
                        sc = float(it.get("分数", 10))
                    except Exception:
                        sc = 10.0
                    分们.append(sc)
                    条目们[str(it.get("图", ""))] = (sc, it)
            均 = sum(分们) / len(分们) if 分们 else 0
            相对判断 = None
            if 轮 > 1 and 台.条目:
                相对判断, _ = 收裁定(台, 名们)
                # 逐图最优保留：图评师判「更差」的图回退脚本与图片，并把它的回执作废
                更差 = [pth for pth, (sc, it) in 条目们.items() if str(it.get("相对判断", "")).startswith("更差") and pth in 上轮快照]
                if 更差:
                    包 = {}
                    for pth in 更差:
                        包[pth] = 上轮快照[pth]
                        sp = re.sub(r"/图片/([^/]+)\.png$", r"/绘图_\1.py", pth)
                        if sp in 上轮快照:
                            包[sp] = 上轮快照[sp]
                    回退(包, f"图评轮{轮} 更差 {len(更差)} 张")
                    for x in 台.条目:
                        if x.get("定位") in 更差 and x.get("状态") in (回路.待复核, 回路.已消解):
                            x["状态"] = 回路.未消解
                            x.setdefault("历史", []).append({"轮次": 轮, "裁定": "回退", "理由": "图评师相对判断：更差"})
                    for pth in 更差:
                        本轮快照[pth] = 上轮快照[pth]
            新条目 = []
            for pth, (sc, it) in 条目们.items():
                if sc >= 配置["图评阈值"]:
                    continue
                指们 = [x for x in (it.get("修改指令") or []) if x] or [f"分数 {sc}：" + "；".join(str(q) for q in (it.get("问题") or [])[:3])]
                for 指 in 指们:
                    d = 意见条目(指, "叙述", "图评")
                    if d:
                        d["定位"] = pth
                        if any(k in d["问题"] for k in ("不一致", "数据", "错误", "对不上")):
                            d["级别"] = "正确性"
                        新条目.append(d)
            统 = 台.并入(新条目, 轮, "图评")
            待 = 台.待改条目()
            低分 = sorted({x["定位"] for x in 待})
            log(f"S3 图评轮{轮}: 均分{均:.2f} 低分({配置['图评阈值']}以下){sum(1 for sc, _ in 条目们.values() if sc < 配置['图评阈值'])}张 "
                f"相对判断={相对判断} 台账 新增{统['新增']} 合并{统['合并']} 重开{统['重开']} 待改{len(待)}条/{len(低分)}图")
            checkpoint([f"审稿/{n}.json" for n in 名们], f"S3-图评轮{轮}")
            if not 待 or not budget_ok():
                log("S3 图评：台账清零或预算限制，退出循环")
                台.保存()
                return
            if 轮 >= 配置["图评轮数"]:
                log(f"S3 图评：轮数上限，仍有 {len(待)} 条意见未消解（留在台账，G3 不阻塞）")
                台.保存()
                return
            prev = 均
            熔断处理(台, f"图评轮{轮}")
            待 = 台.待改条目()
            腿名 = f"图修{轮}"
            wave([("绘图师.md", f"图修复（第{轮}轮）。按角色文件「定向修改协议」执行：只改编号点名的图的绘图脚本（只改不跑），"
                   f"写 求解/成图修{轮}.sh 只重跑这些图。\n" + 台.渲染给修改腿(待, 回执文件=f"审稿/回执_{腿名}.json") +
                   f"\n完成写 日志/{腿名}.done", 腿名)], timeout=1100)
            收回执(台, 腿名)
            run_script(f"求解/成图修{轮}.sh", f"成图修{轮}", budget=700, repairs=1)
            checkpoint_dir("求解", f"S3-图修{轮}")
            上轮快照 = 本轮快照
    节点("S3:图评循环", _S3图评循环, 阶段="S3")

    def _G3检查():
        return 门检("G3")

    def _G3返工(明细):
        wave([("绘图师.md", f"图证门 G3 未通过：{json.dumps(明细, ensure_ascii=False)[:1500]}。按明细补图/改图（配额：全文16-22张、同型≤3、柱折合计≤半、机理示意图≥3），"
               f"新增或修改脚本后写 求解/成图补.sh 重跑受影响图。完成写 日志/G3返工.done", "G3返工")], timeout=1300)
        run_script("求解/成图补.sh", "成图补", budget=900, repairs=1)
        checkpoint_dir("求解", "G3-返工")
    节点("G3", lambda: 调度器.门("G3", _G3检查, 返工fn=_G3返工, 状态=状态, log=log).执行(), 阶段="G3")

    # ---------- S4 撰稿 ----------
    结构 = 计划.get("论文结构", [])
    章文件 = [c.get("文件名", "").replace("论文/", "") for c in 结构 if c.get("文件名")]
    if not any("摘要" in f for f in 章文件):
        章文件.insert(0, "0.摘要.tex")
        结构.insert(0, {"文件名": "0.摘要.tex", "章节标题": "摘要", "内容要点": "四要素", "页数配额": 1})
    if not any("附录" in f for f in 章文件):
        章文件.append("99.附录.tex")
        结构.append({"文件名": "99.附录.tex", "章节标题": "附录：核心源代码与产物清单", "内容要点": "国赛要求的可运行核心源代码", "页数配额": "不限"})

    # ---------- S4 叙事底稿（M5-1 B1，病根 R22）：白话底稿是全部写作腿的第一输入，叙事门机械守住"不懂本题的评委读得懂" ----------
    def _S4叙事底稿():
        wave([("撰稿师.md", "【叙事底稿】按角色文件「叙事底稿」一节产出 交接/叙事底稿.md：每问一节 `## 问题N <一句话>`，节内五个小节标题逐字用 "
               "`### 题目问什么` / `### 数据长什么样` / `### 怎么想` / `### 得到什么` / `### 信到什么程度`，每问 ≥150 汉字；"
               "零公式、零 LaTeX 命令、零文件名、零英文缩写、零流程词（运行时/流程词.txt）、零内部术语（运行时/内部术语.txt）。"
               "素材：各问 交接/结果解读_问题X.md、交接/结果声明_问题X.json（核心指标+置信）、交接/建模笔记_问题X.md、交接/计划.json。"
               "完成写 日志/叙事底稿.done", "叙事底稿")], timeout=1100)
        checkpoint(["交接/叙事底稿.md"], "S4-叙事底稿")
    节点("S4:叙事底稿", _S4叙事底稿, 阶段="S4")

    def _叙事返工(明细):
        wave([("撰稿师.md", f"叙事门未通过：{json.dumps(明细, ensure_ascii=False)[:1500]}。按角色文件「叙事底稿」一节逐条修 交接/叙事底稿.md："
               f"只改点名处（补缺的小节、删公式/命令/文件名/缩写/流程词/内部术语、不足 150 字的问补到 150 字以上），其余不动。完成写 日志/叙事返工.done", "叙事返工")], timeout=900)
        checkpoint(["交接/叙事底稿.md"], "S4-叙事返工")
    节点("G叙事", lambda: 调度器.门("叙事", lambda: 门检("叙事"), 返工fn=_叙事返工, 最多返工=1, 状态=状态, log=log).执行(), 阶段="S4")

    def _S4脊柱():
        wave([("撰稿师.md", "【叙事设计】不写正文。读 交接/叙事底稿.md（白话底稿：论点链里每个「含义」句的口吻以它为准）、交接/计划.json（叙事主线/偏离点/论文结构）、各问 交接/结果解读_问题X.md、"
               "交接/图注素材_*.json、交接/实验记录.json、交接/典型答卷预测.md，产出 交接/论点脊柱.json，schema："
               '{"章":[{"文件名":"3.xxx.tex","论点链":[{"发现":"带具体数字的一句话","证据":["图X","表Y","结果键Z"],"含义":"...","衔接下段":"..."}],'
               '"开场策略":"...","禁止":"模板化开场"}]}。每章 3-6 个论点，发现必须带真实数字。完成写 日志/脊柱.done', "脊柱")], timeout=1800)
        checkpoint(["交接/论点脊柱.json"], "S4-脊柱")
    节点("S4:脊柱", _S4脊柱, 阶段="S4")

    脊柱 = get_json("交接/论点脊柱.json", "论点脊柱", "撰稿师.md", "按 论点脊柱 schema：{\"章\":[{文件名,论点链,开场策略,禁止}]}") or {}
    脊柱表 = {}
    for c in (脊柱.get("章") or []):
        if isinstance(c, dict) and c.get("文件名"):
            脊柱表[str(c["文件名"]).replace("论文/", "")] = c

    def _S4主控():
        主控 = ["\\PassOptionsToPackage{quiet}{xeCJK}", "\\documentclass[withoutpreface,bwprint]{format}",
                "\\usepackage{etoolbox}", "\\usepackage{ctex}", "\\BeforeBeginEnvironment{tabular}{\\zihao{-5}}",
                "\\usepackage[framemethod=TikZ]{mdframed}", "\\usepackage{url}", "\\usepackage{array}",
                "\\usepackage{tabularx}", "\\usepackage{longtable}", "\\usepackage{listings}",
                "\\newcolumntype{C}{>{\\centering\\arraybackslash}X}",
                "\\renewcommand{\\textbf}[1]{{\\song\\bfseries #1}}", "\\usepackage{tikz}", "\\usetikzlibrary{arrows.meta}",
                "\\title{（撰稿师在摘要定稿后由评委腿更新标题）}", "\\begin{document}", "\\maketitle", "\\thispagestyle{empty}"]
        for f in 章文件:
            if "摘要" in f:
                主控 += [f"\\input{{{f}}}", "\\thispagestyle{empty}", "\\newpage", "\\setcounter{page}{1}"]
            else:
                主控.append(f"\\input{{{f}}}")
        主控.append("\\end{document}")
        h.exec("echo 主控写入", files=[h.f_text("论文/论文.tex", "\n".join(主控))], quiet=True)
        log(f"S4 主控 论文.tex 写入（{len(章文件)} 章）")
    节点("S4:主控", _S4主控, 阶段="S4")

    def _S4撰稿():
        普通章 = [c for c in 结构 if "摘要" not in c.get("文件名", "")]
        for bi in range(0, len(普通章), 4):
            legs = []
            for c in 普通章[bi:bi + 4]:
                f = c["文件名"].replace("论文/", "")
                特 = "本章是附录：必须嵌入各问核心可运行源代码（lstlisting 环境，从 求解/*/求解*.py 摘核心函数段并注明文件）+ 产物清单表。" if "附录" in f else ""
                if "问题重述" in f or "总体分析" in f:
                    特 += "1.1 问题重述须写清「题目给定了什么（附件/条件）」与「要求回答什么」，不抄题；1.2 总体分析须引用图表规划里的全文思路图（\\includegraphics）。"
                本脊 = json.dumps(脊柱表.get(f, {}), ensure_ascii=False)[:2500]
                legs.append(("撰稿师.md", f"撰写 论文/{f}（标题：{c.get('章节标题')}；配额：{c.get('页数配额','按需')}页；要点：{json.dumps(c.get('内容要点',''), ensure_ascii=False)[:400]}）。"
                             f"**本章论点脊柱（必须按它的发现→证据→含义链条组织，开场按其开场策略，禁模板化开场）**：{本脊}\n"
                             f"{特}**先读 交接/叙事底稿.md 对应问题的五段**，正文里的解释句与术语的白话定义以它为准。"
                             f"按角色文件「表达四律」写：段首不以数字开头、一段进句数字以 2 个为目标（>4 G4 阻塞）、"
                             f"保留话只在本问结论句后写一处（措辞照 结果声明 的 置信 字段）、不自造缩写、图题取 图注素材 的 图题 字段；"
                             f"过程感只许引用 实验记录 中 类别=科学尝试 的条目；每个含统计数字的段落末尾单独一行 `% src:文件:键1;键2`（段级溯源）。"
                             f"相关问题的建模笔记/结果解读/假设台账/图注素材/实验记录自行从 交接/ 与 求解/ 取。"
                             f"刻意偏离 交接/典型答卷预测.md 的行文套路。图引用 \\includegraphics[width=0.8\\textwidth]{{../求解/问题X/图片/<图名>.png}}。"
                             f"完成写 日志/写_{f}.done", f"写_{f}"))
            wave(legs, timeout=2700)
        checkpoint([f"论文/{f}" for f in 章文件] + ["论文/论文.tex"], "S4-撰稿")
    节点("S4:撰稿", _S4撰稿, 阶段="S4")

    def _S4章评循环():
        # 回路协议：意见有身份、修订只改点名处（变化守卫）、评审先相对后绝对、「更差」回退
        # （病根台账 R2：上轮章重写后 6.50 → 6.40，整章推倒把评审喜欢的部分一起推倒了）。
        普通章 = [c["文件名"].replace("论文/", "") for c in 结构 if "摘要" not in c.get("文件名", "")]
        章路径们 = [f"论文/{f}" for f in 普通章]
        台 = 回路.台账(台账路径("章评台账"), 前缀="章")
        保留 = 回路.最优保留()
        prev = None
        for 轮 in range(1, 配置["章评轮数"] + 1):
            保留.记(轮, 镜像快照(章路径们))
            台账文本 = ("\n【上一轮台账】\n" + 台.渲染给评审腿(裁定文件="审稿/裁定_{名}.json")) if 轮 > 1 and 台.条目 else ""
            legs = []
            for bi in range(0, len(普通章), 4):
                批 = 普通章[bi:bi + 4]
                名 = f"章评R{轮}_{bi//4+1}"
                legs.append(("章评师.md", f"深审以下 {len(批)} 章：{批}。逐章按【AI味/推理跳步/数字溯源】三项打分（1-10）并给可执行改单；"
                             f"语言维按角色文件用 运行时/范文卡片库.md 与 运行时/表达锚点.md 做范文对照（{'相对上一版' if 轮 > 1 else '首轮相对锚点 6 分档'}），每章输出 范文对照 字段。"
                             f"对照 交接/论点脊柱.json 核对论点链是否落地、交接/实验记录.json 核对过程感是否真实（只认 类别=科学尝试）、审稿/审计报告.json（若存在）核对禁用词、溯源与表达画像。"
                             f"输出 JSON 数组写 审稿/{名}.json：[{{\"章\":\"文件名\",\"分数\":x,\"AI味\":x,\"跳步\":x,\"溯源\":x,\"问题\":[...],\"修改指令\":[...]}}]，"
                             f"修改指令每条引用原文片段并说明改成什么，且以 [严重度N] 开头（1 影响档位：数字错/推导跳步/结论不清；2 会扣分；3 锦上添花）；"
                             f"每章 问题 ≤5 条、修改指令 ≤5 条，按严重度排序{'；本轮新意见只对上一轮修订过的段落提，未改段落沿用上一轮台账' if 轮 > 1 else ''}。"
                             f"{台账文本.replace('{名}', 名)}\n完成写 日志/{名}.done", 名))
                # 闭卷读者（M5-1 D2，病根 R21）：不看交接材料，只读这批章；它卡住的地方就是评委会卡住的地方
                读名 = f"读者R{轮}_{bi//4+1}"
                legs.append(("读者.md", f"闭卷读以下 {len(批)} 章（只读 论文/ 下这些 tex；不读 交接/ 求解/ 审稿/）：{批}。"
                             f"按角色文件章级 schema 输出 审稿/{读名}.json（顶层数组，每章一条，卡住 每章 ≥1 条或在复述里证明读懂了）。完成写 日志/{读名}.done", 读名))
            h.exec("python3 bin/审计.py > 日志/审计_章评.out 2>&1; echo ok", quiet=True, timeout_s=T_轻 * 4)
            wave(legs, timeout=3600)      # 章评腿一批 4 章 + 范文对照：单章实测 16-18 分钟（M5-1 重标）；xhigh 再放宽
            读者条目, 读者分们 = [], []
            for _, _, 名 in legs:
                if not 名.startswith("读者R"):
                    continue
                for it in (get_json(f"审稿/{名}.json", 名) or []):
                    if not isinstance(it, dict):
                        continue
                    ch = str(it.get("章", "")).replace("论文/", "")
                    try:
                        读者分们.append(float(it.get("读者分", 0)))
                    except Exception:
                        pass
                    for k in (it.get("卡住") or [])[:3]:
                        if not isinstance(k, dict):
                            continue
                        读者条目.append({"级别": "叙述", "目标": "文", "问题": f"读者卡住：「{str(k.get('原句', ''))[:80]}」——{str(k.get('为什么', ''))[:120]}",
                                         "定位": f"论文/{ch}", "指令": "改写该句让不看交接材料的评委读得懂：先讲清楚对象与结论再钉数字；术语首现处给一句白话定义（抄叙事底稿）",
                                         "验收": "读者腿不再卡在此句", "来源": "读者"})
                    词 = [w for w in (it.get("自造词") or []) if w]
                    if 词:
                        读者条目.append({"级别": "叙述", "目标": "文", "问题": f"读者不认识的词：{词[:6]}", "定位": f"论文/{ch}",
                                         "指令": "展开成 ≤12 字中文短语，或在首次出现处补一句白话定义", "验收": "读者腿自造词为空", "来源": "读者"})
            if 读者分们:
                log(f"S4 章评轮{轮} 读者分均值 {sum(读者分们)/len(读者分们):.2f}，读者意见 {len(读者条目)} 条")
            条目们 = []
            分们 = []
            for _, _, 名 in legs:
                if 名.startswith("读者R"):
                    continue
                v = get_json(f"审稿/{名}.json", 名) or []
                if isinstance(v, dict):
                    v = v.get("章评", []) or []
                for it in v:
                    if not isinstance(it, dict):
                        continue
                    try:
                        sc = float(it.get("分数", 10))
                    except Exception:
                        sc = 10.0
                    分们.append(sc)
                    条目们.append((sc, it))
            均 = sum(分们) / len(分们) if 分们 else 0
            相对判断 = None
            if 轮 > 1 and 台.条目:
                相对判断, _ = 收裁定(台, [名 for _, _, 名 in legs])
                动作, 理由 = 保留.判定(相对判断, prev, 均)
                if 动作 == "回退":
                    回退(保留.回退包(轮 - 1), f"章评轮{轮}：{理由}")
                    for x in 台.条目:
                        if x.get("状态") in (回路.待复核, 回路.已消解) and any(r.get("腿", "").startswith(f"章修订{轮-1}") for r in x.get("回执", [])):
                            x["状态"] = 回路.未消解
                            x.setdefault("历史", []).append({"轮次": 轮, "裁定": "回退", "理由": 理由})
                    保留.记(轮, 保留.回退包(轮 - 1))
                    均 = prev if prev is not None else 均
            新条目 = []
            低分章 = []
            for sc, it in 条目们:
                ch = str(it.get("章", "")).replace("论文/", "")
                if sc >= 配置["章评阈值"]:
                    continue
                低分章.append(ch)
                指们 = [x for x in (it.get("修改指令") or []) if x] or [f"分数 {sc}：" + "；".join(str(q) for q in (it.get("问题") or [])[:3])]
                for 指 in 指们:
                    d = 意见条目(指, "叙述", "章评")
                    if d:
                        m严 = re.search(r"\[严重度(\d)\]", d["问题"])      # P7：章评师按 [严重度N] 标注，修订腿按 1→3 处理
                        d["严重度"] = max(1, min(3, int(m严.group(1)))) if m严 else 2
                        d["定位"] = d["定位"] if ch in d["定位"] else f"论文/{ch} {d['定位']}".strip()
                        if any(k in d["问题"] for k in ("溯源", "不一致", "对不上", "src", "数字")):
                            d["级别"] = "正确性"
                        新条目.append(d)
            统 = 台.并入(新条目 + 读者条目, 轮, "章评")
            待 = 台.待改条目()
            log(f"S4 章评轮{轮}: 均分{均:.2f} 低分({配置['章评阈值']}以下){len(低分章)}章 相对判断={相对判断} "
                f"台账 新增{统['新增']} 合并{统['合并']} 重开{统['重开']} 待改{len(待)}条")
            checkpoint([f"审稿/{n}.json" for _, _, n in legs], f"S4-章评轮{轮}")
            if not 待 or not budget_ok():
                log("S4 章评：台账清零或预算限制，退出循环")
                台.保存()
                return
            if 轮 >= 配置["章评轮数"]:
                log(f"S4 章评：轮数上限，仍有 {len(待)} 条意见未消解（留在台账，进 S5 审稿场继续）")
                台.保存()
                return
            prev = 均
            熔断处理(台, f"章评轮{轮}")
            待 = 台.待改条目()
            按章 = {}
            for x in 待:
                m = re.search(r"([^/\s:：]+\.tex)", x.get("定位", ""))
                按章.setdefault(m.group(1) if m else "（未定位）", []).append(x)
            # R36 补①：修订腿名额按本轮章评分从低到高排（原先按台账字典序取前 6 章——章评 3 分的 4.1.2.模型建立
            # 排在第 9 位，两轮都轮不到它，被修的反而是 6.5 分的前几章）。
            分表 = {str(it.get("章", "")).replace("论文/", ""): sc for sc, it in 条目们}
            排好 = sorted(按章.items(), key=lambda kv: (分表.get(kv[0], 10.0), -len(kv[1])))
            log(f"S4 章修订{轮} 选章（按分从低到高，上限 {配置['章修订每轮章数']}）：" +
                "，".join(f"{ch}({分表.get(ch, '-')}/{len(条)}条)" for ch, 条 in 排好[:配置['章修订每轮章数']]))
            重legs = []
            for i, (ch, 条) in enumerate(排好[:配置["章修订每轮章数"]], 1):
                腿名 = f"章修订{轮}_{i}"
                条 = sorted(条, key=lambda x: int(x.get("严重度", 2) or 2))      # P7：严重度 1 在前
                重legs.append(("撰稿师.md", f"章修订（第{轮}轮）：论文/{ch}。按角色文件「定向修改协议」执行：只改编号点名处，其他段落一字不动；"
                               f"条目已按严重度 1→3 排好，全部处理、先做 1；"
                               f"保持 交接/论点脊柱.json 中本章论点链。\n" + 台.渲染给修改腿(条, 回执文件=f"审稿/回执_{腿名}.json") +
                               f"\n完成写 日志/{腿名}.done", 腿名))
            wave(重legs, timeout=1800)
            for _, _, 腿名 in 重legs:
                收回执(台, 腿名)
            守卫上限 = 配置["变化守卫_升格"] if any(x.get("升格过") for x in 待) else 配置["变化守卫"]
            变化守卫回退(台, 保留.快照们[轮], 章路径们, 守卫上限, f"章评轮{轮}")
            checkpoint([f"论文/{f}" for f in 章文件], f"S4-章修订{轮}")
    节点("S4:章评循环", _S4章评循环, 阶段="S4")

    # ---------- S4 统稿（M5-1 C3）：四条撰稿腿并行写出的章，术语/语气/保留话只有通读全稿才统一得了 ----------
    def _S4统稿():
        普通章 = [c["文件名"].replace("论文/", "") for c in 结构 if "摘要" not in c.get("文件名", "") and "附录" not in c.get("文件名", "")]
        章路径们 = [f"论文/{f}" for f in 普通章]
        快照 = 镜像快照(章路径们)
        wave([("统稿师.md", f"统稿：通读 {章路径们}，按角色文件做术语统一（先列名字表）、置信声明收口（每问只在结论句后一处）、拆长句、段首去数字、"
               f"图题改成陈述、章间衔接。**不动数字/引用键/公式数/插图数/标题/% src**（bin/统稿守卫.py 会机械比对，动了该文件整份回退）。"
               f"产出 审稿/统稿回执.json。完成写 日志/统稿.done", "统稿")], timeout=3600)
        # 事实层守卫（统稿守卫.py）：数字多重集合/引用键/公式数/插图数/标题/src 任一变化 → 该文件回退
        回退的 = []
        for rel in 章路径们:
            q = 镜像目录 / rel
            if rel not in 快照 or not q.is_file():
                continue
            ok, 差 = 统稿守卫.守卫(快照[rel].decode("utf-8", "replace"), q.read_text(encoding="utf-8", errors="replace"))
            if not ok:
                回退的.append((rel, 差))
        if 回退的:
            回退({rel: 快照[rel] for rel, _ in 回退的}, "统稿守卫")
            for rel, 差 in 回退的:
                log(f"!! 统稿守卫：{rel} 动了事实层 → 整份回退：{差[:3]}")
        # 语言层也有边界：改动行占比超 变化守卫 = 重写不是统稿（病根 R2）
        for rel in 章路径们:
            q = 镜像目录 / rel
            if rel not in 快照 or not q.is_file() or any(rel == r for r, _ in 回退的):
                continue
            通过, r, 明 = 回路.修订守卫(快照[rel].decode("utf-8", "replace"), q.read_text(encoding="utf-8", errors="replace"), (), 配置["变化守卫"])
            if not 通过:
                回退({rel: 快照[rel]}, "统稿-变化守卫")
                log(f"!! 统稿变化守卫：{rel} 改动比例 {r:.0%} > {配置['变化守卫']:.0%} → 回退（句 {明['句比']:.0%} 字 {明['字比']:.0%} 行 {明['行比']:.0%}）")
        log(f"S4 统稿完成：{len(章路径们)} 章，事实层回退 {len(回退的)}")
        checkpoint(章路径们 + ["审稿/统稿回执.json"], "S4-统稿")
    节点("S4:统稿", _S4统稿, 阶段="S4")

    def _S4摘要():
        n = 配置["摘要变体数"]
        摘要文件 = next((f for f in 章文件 if "摘要" in f), "0.摘要.tex")
        # 摘要专项（M5-1 C4，病根 R24/R26）：第一句白话、数字只留每问答案、保留话 ≤1 处；机械画像门 + 闭卷复述各最多触发一次定向修订
        摘底 = ("写摘要（\\begin{abstract}…\\end{abstract}，\\keywords，\\label{abstract:end}）。≤900字必1页。"
                "**第一句是白话**：不带术语、不带数字，说清这题在做什么（口吻照 交接/叙事底稿.md）。"
                "素材=各问结果解读的论文引用清单+结果声明（核心指标与置信）+定制改造的中文名+锦标赛优胜依据。每问一段\\textbf{对于问题X：}，一句结论句含数值+单位。"
                "数字只留每问答案（全摘要 ≤12 个统计数字）；保留话全摘要 ≤1 处（照结果声明的置信字段，放在最需要的那一问）；句子 ≤40 字；不自造缩写。"
                "每个含统计数字的段落末尾单独一行 % src:文件:键。禁用词表适用；刻意偏离 交接/典型答卷预测.md 的摘要套路。同时把论文标题按内容定稿写入你输出文件首行注释 %标题：xxx。")
        侧重们 = ["侧重 30 秒可复述（每问一句答案，评委读完能背出三个数）", "侧重结论冲击力（关键数字前置，但第一句仍是白话）",
                  "侧重方法链条清楚（定制改造的中文名与为什么，不堆术语）", "侧重可信度（验证与稳健性证据各一句，保留话仍只一处）",
                  "侧重问题-方法匹配的说理链（为什么必须这样做）"]
        legs = []
        for k in range(1, n + 1):
            legs.append(("撰稿师.md", 摘底 + f"变体{k}{侧重们[(k-1) % len(侧重们)]}。写 审稿/摘要_变体{k}.tex。完成写 日志/摘{k}.done", f"摘{k}"))
        wave(legs, timeout=1100)
        wave([("审稿员.md", f"【摘要面板评分与融合】比较 审稿/摘要_变体1..{n}.tex，按摘要四要素（具体问题｜模型+算法点名｜每问关键数值+单位｜结论与稳健性）"
               f"+表达四律（第一句白话、统计数字 ≤12、保留话 ≤1 处、句均 ≤40 字、无自造缩写）逐个打分，合并各变体优点写终版到 论文/{摘要文件}，"
               f"并把定稿论文标题替换进 论文/论文.tex 的 \\title{{}}。评分与融合理由写 审稿/摘要评审.json。完成写 日志/摘评.done", "摘评")], timeout=1200)
        for 修 in range(2):
            h.exec("python3 bin/审计.py > 日志/审计_摘要.out 2>&1; echo ok", quiet=True, timeout_s=T_轻 * 4)
            审 = get_json("审稿/审计报告.json", "审计报告") or {}
            超 = ((审.get("表达画像") or {}).get("摘要画像") or {}).get("超线") or []
            wave([("读者.md", f"只读 论文/{摘要文件}（不读任何别的文件）。按角色文件「摘要复述」schema 输出 审稿/摘要复述.json。完成写 日志/摘要复述{修}.done", f"摘要复述{修}")], timeout=600)
            复 = get_json("审稿/摘要复述.json", "摘要复述") or {}
            不能 = [q for q in (复.get("每问") or []) if isinstance(q, dict) and not str(q.get("能否复述", "")).startswith("能")]
            问题 = [f"机械画像超线：{x}" for x in 超] + [f"问{q.get('问题')} 读者复述不出答案：{str(q.get('复述', ''))[:80]}" for q in 不能]
            if 复 and not 复.get("第一句白话", True):
                问题.append("第一句不是白话（带术语或数字）")
            log(f"S4 摘要画像门#{修}: 超线{超} 复述不出{len(不能)}问 第一句白话={复.get('第一句白话')} 保留话{复.get('置信声明次数')}处")
            if not 问题 or 修 == 1 or not budget_ok():
                break
            wave([("撰稿师.md", f"摘要定向修订（论文/{摘要文件}）：只改下列问题涉及的句子，其余一字不动：{json.dumps(问题, ensure_ascii=False)[:1500]}。"
                   f"数字退回每问答案、保留话只留一处、第一句白话、句子拆到 40 字内。完成写 日志/摘修{修}.done", f"摘修{修}")], timeout=900)
        checkpoint([f"论文/{摘要文件}", "论文/论文.tex", "审稿/摘要评审.json", "审稿/摘要复述.json"], "S4-摘要")
    节点("S4:摘要", _S4摘要, 阶段="S4")

    # ---------- G4 成稿门 ----------
    def _编译修复循环(标签, 次数=4):
        for fix in range(次数):
            E, O, P, out = compile_paper(f"#{标签}{fix}")
            if E == 0:
                return E, O, P, out
            if not budget_ok():
                return E, O, P, out
            wave([("撰稿师.md", f"编译修复：论文编译报错。错误摘录：{out[:1200]}。用 grep -n 定位 论文/*.tex 相应位置修复（只修错误，不动内容）。完成写 日志/编修{标签}{fix}.done", f"编修{标签}{fix}")], timeout=900)
        return compile_paper(f"#{标签}末")

    def _G4检查():
        _编译修复循环("编", 3)
        h.exec("python3 bin/审计.py > 日志/审计.out 2>&1; echo ok", quiet=True)
        return 门检("G4")

    def _G4返工(明细):
        # 病根台账 R38（20260910 对照跑）：G4 返工腿没有任何守卫，为清零附录代码里的"禁用词/过精浮点"整段删掉三问完整源码清单
        # （标准第 15 条：缺代码可能取消评奖资格）、把 1.1/1.2 清空并入首章并改主控 \\input、改页边距/行距压页数。
        # 现在：改前快照 → 腿 → 结构守卫（主控 \\input 集不许变、章不许清空、附录 lstlisting 不许减、整份改动按升格上限）。
        路径们 = ["论文/论文.tex"] + [f"论文/{f}" for f in 章文件]
        快照 = 镜像快照(路径们)
        wave([("撰稿师.md", f"成稿门 G4 未通过：{json.dumps(明细, ensure_ascii=False)[:2000]}。逐条修 论文/*.tex："
               f"禁用词清零、每个统计数字补 `% src:文件:键` 且必须与 结果/ JSON 真值一致、"
               f"如图如表句式占比降到40%以下、摘要压到恰好1页、正文≤20页；表达判据（角色文件「表达四律」）：密度超线的章把数字退回图表与答案框、"
               f"保留话每问只留结论句后一处、长句拆短；段首数字段主语前置；超预算段多余数字进表；自造缩写展开成中文；图题去保留话；"
               f"问题重述补「给定/要求」与思路图。\n"
               # P12（2026-09-11 A 题 G4 连败两次）：返工腿两次都只改摘要，对 2.2 的对冲密度超线写了一篇「审计器有误」的意见就不改稿——
               # 门判据是用户定的，腿的义务是让稿子过线，异议另走登记，不能替操盘手裁决。
               f"门判据不由你裁决：表达密度/禁用词/缩写等每一条超线，无论命中在正文还是标题、无论你是否认同该词属置信声明，都必须改写措辞把该章压到线下"
               f"（不改事实、不删内容、不改数字）；你认为审计器判错的，另写 审稿/审计异议.md（文件、行、理由）交操盘手登记病根，但不得以此为由不改稿。\n"
               f"封死的做法（做了会被驱动整份回退并记越权）：不许删除或缩短附录里的源码清单（lstlisting）——附录必须含全部关键可运行源代码，"
               f"代码里的字面量不算禁用词、代码里的浮点常量不算过精；不许合并/清空/删除任何章文件，不许改 论文/论文.tex 的 \\input 清单；"
               f"不许改 geometry/行距/字号来压页数（版式归 S5 美化）；正文超 20 页只能把细节退到附录或表格。完成写 日志/G4返工.done", "G4返工")], timeout=1800)
        结构守卫回退(快照, 路径们, 配置["变化守卫_升格"], "G4返工")
        checkpoint([f"论文/{f}" for f in 章文件], "G4-返工")
    节点("G4", lambda: 调度器.门("G4", _G4检查, 返工fn=_G4返工, 状态=状态, log=log).执行(), 阶段="G4")

    # ---------- S5 审稿场（五路并审 + 分级修订单 + 定向回炉） ----------
    def _渲染页():
        h.exec("mkdir -p 论文/页 && rm -f 论文/页/*.png && cd 论文 && "
               "gs -dNOPAUSE -dBATCH -sDEVICE=png16m -r70 -sOutputFile=页/p%02d.png 论文.pdf > /dev/null 2>&1; ls 页 | wc -l", quiet=True)
        d = h.exec("ls 论文/页/*.png | sort", quiet=True)
        return [l for l in (d.get("stdout") or "").split() if l.endswith(".png")]

    def _S5审稿场():
        # 回路协议（回路.py）：台账有身份、修订有回执、评审先相对后绝对、退步回退、修不掉熔断。
        # 退出判据 = 台账阻塞级清零，分数只做诊断（病根台账 R1–R5、R7）。
        台 = 回路.台账(台账路径("审稿台账"), 前缀="审")
        保留 = 回路.最优保留()
        章路径们 = [f"论文/{f}" for f in 章文件]
        prev = None
        起轮 = int(状态.数据.get("S5已完成轮", 0)) + 1   # R43：轮级断点——S5 是最长节点（每轮 1–2.5 h），续跑不再从轮1 重来
        if 起轮 > 1:
            log(f"S5 续跑：前 {起轮 - 1} 轮已落盘，从轮{起轮} 继续（上一轮快照不在内存，本轮不做回退）")
        for 轮 in range(起轮, 配置["审稿轮数"] + 1):
            if not budget_ok():
                log("S5 预算耗尽，退出审稿循环")
                break
            运行态["阶段"] = f"S5:轮{轮}"
            状态.设阶段(f"S5:轮{轮}")
            E, O, P, out = compile_paper(f"#审{轮}前")
            if E:   # R39：审前编译失败不能带病下发——页图为空时评委模拟腿会被静默跳过，五路变四路
                E, O, P, out = _编译修复循环(f"审{轮}前修", 2)
            页们 = _渲染页()
            h.exec("python3 bin/审计.py > 日志/审计.out 2>&1; echo ok", quiet=True, timeout_s=T_轻 * 4)
            门检("G4")   # 机械审计+门检作为第⑤路（不占腿）
            保留.记(轮, 镜像快照(章路径们))
            台账文本 = ("\n【上一轮台账——先按角色文件「配对评审协议」逐条裁定，再审新问题】\n" +
                       台.渲染给评审腿(裁定文件="审稿/裁定_{名}.json")) if 台.条目 else ""   # 台账非空就渲染（--resume 从轮1重进时上轮回执也要被裁定）
            摘要文件 = next((f for f in 章文件 if '摘要' in f), '0.摘要.tex')
            # ①②④ 三路文本腿 + ③ 评委模拟（页图腿）。schema 一律引用角色文件，驱动不重述（病根台账 R6：
            # 上轮驱动内联的硬伤 schema 抄丢了 目标/修改指令，32 条修订全成了“文”）。
            legs = [
                ("审稿员.md", f"第{轮}轮全文审稿·评委A（论文/ 全部tex + 审稿/审计报告.json 特别核对其中溯源核验存疑清单 + 交接/ 材料）。独立评审。"
                 f"按角色规范产出 审稿/审稿意见_轮{轮}A.json。**另外必须更新 交接/需求追踪矩阵.json 的落位字段**（逐条核对该需求是否已在论文中落位，"
                 f"填 章节/图表/关键数字，已落位改状态为 已落位）。{台账文本.replace('{名}', f'审{轮}A')}\n完成写 日志/审{轮}A.done", f"审{轮}A"),
                ("审稿员.md", f"第{轮}轮全文审稿·评委B（同上输入，独立评审，不看评委A）。侧重挑硬伤：数字一致性、推导跳步、图文相符。"
                 f"产出 审稿/审稿意见_轮{轮}B.json。{台账文本.replace('{名}', f'审{轮}B')}\n完成写 日志/审{轮}B.done", f"审{轮}B"),
                ("硬伤猎手.md", f"第{轮}轮硬伤猎杀。产出 审稿/硬伤_轮{轮}.json，schema 严格照你角色文件（含 目标 与 修改指令 两个字段，**空数组=清零**）。"
                 f"{台账文本.replace('{名}', f'硬伤{轮}')}\n完成写 日志/硬伤{轮}.done", f"硬伤{轮}"),
            ]
            评委名 = None
            if 页们:
                关键页 = 页们[:1] + 页们[1:2] + 页们[2:8]
                评委名 = 图片腿(f"评委模拟{轮}", "评委模拟.md",
                              f"模拟 5-10 分钟初评。附图为论文渲染页（第1张=摘要页，其后为正文首页起）。同时可读 论文/{摘要文件}。"
                              f"输出 审稿/评委模拟_轮{轮}.json（按角色规范）。{台账文本.replace('{名}', f'评委模拟{轮}')}\n完成写 日志/评委模拟{轮}.done",
                              关键页[:8])
            wave(legs, timeout=3600)      # 全文审稿腿 + 范文对照：M5-1 重标实测 >20 分钟；xhigh 再放宽
            if 评委名:
                等腿([评委名], timeout=900)
            意A = get_json(f"审稿/审稿意见_轮{轮}A.json", f"轮{轮}A") or {}
            意B = get_json(f"审稿/审稿意见_轮{轮}B.json", f"轮{轮}B") or {}
            硬伤 = get_json(f"审稿/硬伤_轮{轮}.json", f"硬伤{轮}")
            硬伤 = 硬伤 if isinstance(硬伤, list) else ((硬伤 or {}).get("硬伤", []) or [])
            评委 = get_json(f"审稿/评委模拟_轮{轮}.json", f"评委模拟{轮}") or {}
            审计 = get_json("审稿/审计报告.json", "审计报告") or {}
            分们 = [float(x.get("总分", 0) or 0) for x in (意A, 意B) if x.get("总分")]
            总分 = sum(分们) / len(分们) if 分们 else 0.0
            # ---------- 上一轮的回执 → 本轮评审裁定；相对判断决定接受还是回退 ----------
            相对判断 = None
            if 台账文本:
                相对判断, _ = 收裁定(台, [f"审{轮}A", f"审{轮}B", f"硬伤{轮}"] + ([f"评委模拟{轮}"] if 评委名 else []))
                动作, 理由 = 保留.判定(相对判断, prev, 总分)
                if 动作 == "回退" and 保留.回退包(轮 - 1):   # 没有上一轮快照（轮1 或续跑重进）就退不了
                    回退(保留.回退包(轮 - 1), f"S5 轮{轮}：{理由}")
                    for x in 台.条目:
                        if x.get("状态") in (回路.待复核, 回路.已消解) and any(r.get("腿", "").startswith(f"回炉") and f"轮{轮-1}" in r.get("腿", "") for r in x.get("回执", [])):
                            x["状态"] = 回路.未消解
                            x.setdefault("历史", []).append({"轮次": 轮, "裁定": "回退", "理由": 理由})
                    保留.记(轮, 保留.回退包(轮 - 1))
                    总分 = prev if prev is not None else 总分
            # ---------- 五路意见 → 台账（不截断） ----------
            新条目 = []
            for 源, 意 in (("审稿员A", 意A), ("审稿员B", 意B)):
                for it in (意.get("最高优先级修改", []) or []):
                    d = 意见条目(it, "正确性" if 源 == "审稿员B" else "叙述", 源)
                    if d:
                        新条目.append(d)
            for it in 硬伤:
                if isinstance(it, dict):
                    d = {"级别": "硬伤", "目标": it.get("目标", "文"), "问题": str(it.get("证据") or it.get("类别", ""))[:600],
                         "定位": str(it.get("定位", ""))[:200],
                         "指令": str(it.get("修改指令") or it.get("建议") or f"消除该硬伤（{it.get('类别')}）：{str(it.get('证据',''))[:200]}")[:600],
                         "验收": "硬伤猎手复查不再命中", "来源": "硬伤猎手"}
                    if it.get("对应"):
                        d["对应"] = str(it["对应"])
                    新条目.append(d)
            for it in (评委.get("修订建议", []) or 评委.get("卡住的地方", []) or []):
                d = 意见条目(it, "叙述", "评委模拟")
                if d:
                    d["验收"] = d.get("验收") or "评委模拟不再指出"
                    新条目.append(d)
            for it in ((审计.get("溯源核验", {}) or {}).get("存疑明细", []) or []):
                新条目.append({"级别": "正确性", "目标": "文", "问题": f"溯源存疑：{json.dumps(it, ensure_ascii=False)[:200]}",
                             "定位": f"{it.get('文件')}:{it.get('行')}", "指令": "核对该数字与来源JSON真值，改正文数字或修正 % src 指向",
                             "验收": "审计溯源存疑数减少", "来源": "机械审计"})
            for it in (审计.get("禁用词", {}) or {}).get("明细", []):
                新条目.append({"级别": "叙述", "目标": "文", "问题": f"禁用词：{it.get('词')}", "定位": f"{it.get('文件')}:{it.get('行')}",
                             "指令": f"改写该句，去掉“{it.get('词')}”且不留同义套话", "验收": "审计禁用词=0", "来源": "机械审计"})
            # 表达判据（M5-1）：G4 阻塞的同一批机械量，在审稿场里逐条进台账（有身份、可裁定、可熔断）
            画 = 审计.get("表达画像") or {}
            for it in (画.get("超线明细") or [])[:6]:
                新条目.append({"级别": "叙述", "目标": "文", "问题": f"表达密度超线：{os.path.basename(str(it.get('文件', '')))} {it.get('指标')} {it.get('值')} > {it.get('阈值')}",
                             "定位": str(it.get("文件", "")), "指令": "数字退回图表与答案框；保留话每问只留结论句后一处；长句拆短", "验收": "审计表达画像该项不再超线", "来源": "机械审计"})
            for it in (画.get("段首数字明细") or [])[:6]:
                新条目.append({"级别": "叙述", "目标": "文", "问题": f"段首数字：{it.get('开头')}", "定位": f"{it.get('文件')}:{it.get('行')}",
                             "指令": "主语前置：先讲清楚对象与结论，数字跟在后面", "验收": "审计段首数字段减少", "来源": "机械审计"})
            for it in (画.get("超预算段明细") or [])[:6]:
                新条目.append({"级别": "叙述", "目标": "文", "问题": f"一段 {it.get('数字个数')} 个数字（硬线 {(画.get('段内数字线') or {}).get('硬线')}）", "定位": f"{it.get('文件')}:{it.get('行')}",
                             "指令": "多余的数字进表或删，段内只留支撑论点的 2 个", "验收": "审计超预算段减少", "来源": "机械审计"})
            缩 = 审计.get("自造缩写") or {}
            if 缩.get("数量"):
                新条目.append({"级别": "叙述", "目标": "文", "问题": f"自造缩写 {缩.get('数量')} 处：{缩.get('去重')}", "定位": "",
                             "指令": "白名单外的缩写一律展开成 ≤12 字中文短语；标题里的内部术语改成人话", "验收": "审计自造缩写=0", "来源": "机械审计"})
            for it in ((审计.get("图题") or {}).get("对冲违规") or [])[:6]:
                新条目.append({"级别": "叙述", "目标": "文", "问题": f"图题含保留话：{str(it.get('图题', ''))[:40]} {it.get('词')}", "定位": f"{it.get('文件')}:{it.get('行')}",
                             "指令": "图题改成 对象+看到什么（≤25 字），限制条款写进正文", "验收": "审计图题对冲违规=0", "来源": "机械审计"})
            矩阵 = get_json("交接/需求追踪矩阵.json", "需求矩阵") or []
            for it in [x for x in 矩阵 if isinstance(x, dict) and x.get("状态") != "已落位"]:
                新条目.append({"级别": "正确性", "目标": "文", "问题": f"需求未销号：{it.get('需求号')} {str(it.get('内容'))[:120]}",
                             "定位": "", "指令": "在正文补上该需求的落位（内容+数字+图表），并在回执里写明落位的章节与数字（矩阵状态由下一轮审稿员核对后更新）",
                             "验收": "需求矩阵该条状态=已落位", "来源": "需求矩阵"})
            统 = 台.并入(新条目, 轮)
            收敛, 阻塞明细 = 台.收敛()
            log(f"S5 轮{轮} 面板均分={总分:.2f}（A={意A.get('总分')} B={意B.get('总分')}）硬伤={len(硬伤)}条 评委第一印象={评委.get('第一印象分') or 评委.get('印象分')} "
                f"相对判断={相对判断} 台账：新增{统['新增']} 合并{统['合并']} 重开{统['重开']} 阻塞{len(阻塞明细)} {台.摘要()}")
            台账视图上传(台, "审稿/审稿台账_视图.json")
            checkpoint([f"审稿/审稿意见_轮{轮}A.json", f"审稿/审稿意见_轮{轮}B.json", f"审稿/硬伤_轮{轮}.json",
                        f"审稿/评委模拟_轮{轮}.json", "审稿/审计报告.json", "交接/需求追踪矩阵.json", "审稿/审稿台账_视图.json"], f"S5-轮{轮}")
            # ---------- 退出判据：台账阻塞级清零（分数只做诊断） ----------
            if 收敛:
                log(f"S5 收敛：台账阻塞级清零（均分{总分:.2f} 仅供参考）→ 退出审稿循环")
                break
            if 轮 >= 配置["审稿轮数"]:
                log(f"S5 轮数上限：仍有 {len(阻塞明细)} 条阻塞级意见未消解 → 退出；它们留在台账视图里，G5 与复盘必须看见")
                break
            prev = 总分
            熔断处理(台, f"S5轮{轮}")
            # ---------- 修订 = 台账待改（按级别排序，不截断）：算 → 图 → 文 ----------
            待 = 台.待改条目()
            组 = {"算": [x for x in 待 if x["目标"] == "算"], "图": [x for x in 待 if x["目标"] == "图"], "文": [x for x in 待 if x["目标"] == "文"]}
            log(f"S5 轮{轮} 待改 {len(待)} 条 → 算{len(组['算'])}/图{len(组['图'])}/文{len(组['文'])}（先算后图后文）")
            if 组["算"] and budget_ok():
                受影响问 = set()
                for it in 组["算"]:
                    受影响问 |= 回路.条目涉及问(it, 问表)   # R46：认 问题N/问N/问题一二三/章号 4.N. 8.N.
                受影响问 = sorted(受影响问) or sorted(问表)[:1]
                log(f"S5 轮{轮} 回炉S2：重算问{受影响问}")
                for bh in 受影响问:
                    腿名 = f"回炉算_轮{轮}_问{bh}"
                    条 = [x for x in 组["算"] if bh in 回路.条目涉及问(x, 问表)] or 组["算"]
                    wave([("建模师.md", f"【S5回炉·重算】问题{bh}：审稿场判定需要重新计算。按角色文件「定向修改协议」执行。\n" +
                           台.渲染给修改腿(条, 回执文件=f"审稿/回执_{腿名}.json") +
                           f"\n改 求解/问题{bh}/求解_问题{bh}.py 与建模笔记与假设台账，只改不跑；改动任何已发布数值时必须写 交接/换版清单_问题{bh}.json"
                           f"（键/旧值/新值/出现处，角色文件「定向修改协议」5），撰稿师与绘图师按它逐处同步。完成写 日志/{腿名}.done", 腿名)], timeout=1300)
                    收回执(台, 腿名)
                    run_script(f"求解/问题{bh}/求解_问题{bh}.py", f"执行_回炉_轮{轮}_问{bh}", repairs=1)
                    wave([("解读师.md", f"问题{bh} 回炉重算后重新核验解读（五项正确性协议），更新 交接/结果解读_问题{bh}.md 与 交接/结果声明_问题{bh}.json。{_回炉边界句()}"
                           f"另核对 交接/换版清单_问题{bh}.json：存在且每条新值与结果 JSON 一致；缺或对不上只在返工单加一条（级别 正确性、目标 算），不据此判 FAIL。"
                           f"完成写 日志/解读_回炉_轮{轮}_问{bh}.done（首行PASS/FAIL）", f"解读_回炉_轮{轮}_问{bh}")], timeout=1100)
                    if 配置["开红队"] and budget_ok():
                        红队复算(bh, f"_回炉{轮}")
                    已降级 = any(d.get("门") == f"G2:问{bh}" for d in 状态.数据.get("降级放行", []))
                    G2门(bh, 只复检=已降级)   # R44
                    下游 = 运行态["级联重算"](bh)
                    if 下游:
                        log(f"S5 轮{轮} 级联：问{下游} 已重算，受影响章将进下一轮修订")
                        台.并入([{"级别": "正确性", "目标": "文", "问题": f"级联重算问{下游}后，相关章节数字需同步",
                                 "定位": "", "指令": "按新结果更新相关章正文数字与 % src 标注", "验收": "溯源核验通过", "来源": "级联"}], 轮)
                        组["文"] = 台.待改条目(目标们=["文"])
            if 组["图"] and budget_ok():
                腿名 = f"回炉图_轮{轮}"
                wave([("绘图师.md", f"【S5回炉·改图】按角色文件「定向修改协议」改绘图脚本（只改不跑），写 求解/成图回炉{轮}.sh 只重跑受影响图。"
                       f"若存在 交接/换版清单_问题*.json，图内数字/标注一律从结果 JSON 读新值并在回执列出同步的键；成图脚本按「成图脚本纪律」（不覆盖 MPLCONFIGDIR、单图失败不整批非零退出）。\n" +
                       台.渲染给修改腿(组["图"], 回执文件=f"审稿/回执_{腿名}.json") + f"\n完成写 日志/{腿名}.done", 腿名)], timeout=1300)
                收回执(台, 腿名)
                run_script(f"求解/成图回炉{轮}.sh", f"成图回炉{轮}", budget=800, repairs=1)
                checkpoint_dir("求解", f"S5-回炉图{轮}")
            if 组["文"] and budget_ok():
                批 = 组["文"]
                守卫上限 = 配置["变化守卫_升格"] if any(x.get("升格过") for x in 批) else 配置["变化守卫"]
                for bi in range(0, len(批), 12):
                    片 = 批[bi:bi + 12]
                    腿名 = f"回炉文_轮{轮}_{bi//12+1}"
                    wave([("撰稿师.md", f"【S5回炉·改文】第{轮}轮。按角色文件「定向修改协议」执行：只改编号点名处，其他一字不动；"
                           f"涉及数字的必须与 求解/*/结果/ JSON 真值一致并带 `% src:文件:键`；若存在 交接/换版清单_问题*.json，先逐条 grep 旧值同步全文（协议 7）；"
                           f"引用源码只许 路径+SHA256+片段（协议 6），不许整份 \\lstinputlisting。\n" +
                           台.渲染给修改腿(片, 回执文件=f"审稿/回执_{腿名}.json") + f"\n完成写 日志/{腿名}.done", 腿名)], timeout=1300)
                    收回执(台, 腿名)
                变化守卫回退(台, 保留.快照们[轮], 章路径们, 守卫上限, f"S5轮{轮}")
                _编译修复循环(f"修{轮}", 3)
            台账视图上传(台, "审稿/审稿台账_视图.json")
            checkpoint([f"论文/{f}" for f in 章文件], f"S5-轮{轮}修订后")
            状态.数据["S5已完成轮"] = 轮   # R43：本轮产物已全部落盘，续跑从下一轮起
            状态.保存()
        状态.数据.pop("S5已完成轮", None)   # 节点完成，清轮级断点（节点() 随后落盘）
    节点("S5:审稿场", _S5审稿场, 阶段="S5")

    # ---------- S5a 摘要定稿：审稿收敛后的数字才是终值 ----------
    def _S5摘要定稿():
        # 病根台账 R8：摘要在 S5 之前定稿，回炉后数字漂移无人重写；上轮评委模拟到第 4 轮仍在问「问题一交付了什么」。
        # 验收不是打分，是评委模拟的「30 秒摘要」能否逐要素复述（每问一句含数值+单位+不确定性的结论句）。
        摘要文件 = next((f for f in 章文件 if '摘要' in f), '0.摘要.tex')
        台 = 回路.台账(台账路径("摘要台账"), 前缀="摘")
        for 轮 in (1, 2):
            腿名 = f"摘要定稿{轮}"
            待 = 台.待改条目()
            任务 = (f"【摘要定稿（第{轮}轮）】审稿收敛后的数字才是终值：重读各问 交接/结果解读_问题X.md 的论文引用清单与 交接/结果声明_问题X.json，"
                    f"重写 论文/{摘要文件}（\\begin{{abstract}}…\\end{{abstract}}，\\keywords，\\label{{abstract:end}}，≤900 字必 1 页）。"
                    f"四要素硬性：具体问题｜模型+算法点名｜**每问一句结论句，含数值+单位+不确定性**｜稳健性。"
                    f"按 运行时/优秀论文标准.md 第 5 条先用朴素语言回答题目再上术语；禁用词表适用；刻意偏离 交接/典型答卷预测.md 的摘要套路。")
            if 待:
                任务 += "\n按角色文件「定向修改协议」处理以下意见：\n" + 台.渲染给修改腿(待, 回执文件=f"审稿/回执_{腿名}.json")
            wave([("撰稿师.md", 任务 + f"\n完成写 日志/{腿名}.done", 腿名)], timeout=1200)
            if 待:
                收回执(台, 腿名)
            E, O, P, out = compile_paper(f"#摘{轮}")
            页们 = _渲染页()
            if not 页们:
                log("摘要定稿：无渲染页，跳过验收")
                台.保存()
                return
            评名 = f"摘要验收{轮}"
            图片腿(评名, "评委模拟.md",
                  f"只做「30 秒摘要」这一步：附图为摘要页。逐要素判定能否复述（具体问题｜模型+算法｜每问数值+单位｜结论与稳健性），"
                  f"产出 审稿/{评名}.json（按角色规范：第一印象分/能否复述四要素/四要素缺失/30秒摘要复述）。"
                  + (("\n" + 台.渲染给评审腿(裁定文件=f"审稿/裁定_{评名}.json")) if 台.条目 else "")
                  + f"\n完成写 日志/{评名}.done", 页们[:1])
            等腿([评名], timeout=600)
            if 台.条目:
                收裁定(台, [评名])
            v = get_json(f"审稿/{评名}.json", 评名) or {}
            缺 = [q for q in (v.get("四要素缺失") or []) if q]
            能 = str(v.get("能否复述四要素", ""))
            台.并入([{"级别": "正确性", "目标": "文", "问题": f"摘要四要素缺失：{str(q)[:200]}", "定位": f"论文/{摘要文件}",
                     "指令": f"补齐：{str(q)[:200]}", "验收": "评委模拟能复述该要素", "来源": "评委模拟"} for q in 缺], 轮, "评委模拟")
            # 摘要画像门（M5-1）：审稿收敛后重写的摘要同样要过机械量（数字 ≤12、保留话 ≤3、缩写 0、句均 ≤40）
            h.exec("python3 bin/审计.py > 日志/审计_摘定.out 2>&1; echo ok", quiet=True, timeout_s=T_轻 * 4)
            超 = (((get_json("审稿/审计报告.json", "审计报告") or {}).get("表达画像") or {}).get("摘要画像") or {}).get("超线") or []
            台.并入([{"级别": "叙述", "目标": "文", "问题": f"摘要画像超线：{x}", "定位": f"论文/{摘要文件}",
                     "指令": "数字只留每问答案、保留话只留一处、句子拆到 40 字内、缩写展开", "验收": "审计摘要画像不超线", "来源": "机械审计"} for x in 超], 轮, "机械审计")
            log(f"S5 摘要定稿轮{轮}: 能否复述={能 or '?'} 缺失{len(缺)} 画像超线{len(超)} 第一印象={v.get('第一印象分')} 待改{len(台.待改条目())}")
            checkpoint([f"论文/{摘要文件}", f"审稿/{评名}.json"], f"S5-摘要{轮}")
            if 能.startswith("能") and not 台.待改条目():
                break
        台.保存()
    节点("S5:摘要定稿", _S5摘要定稿, 阶段="S5")

    # ---------- S5b 美化循环（收敛后置） ----------
    def _记美化页数(P):
        if isinstance(P, int) and P > 0:
            状态.数据["美化后页数"] = P

    def _S5美化():
        # 回路协议 + 页数守卫（病根台账 R4/R7：上轮 页问题 86→89、美观分 4.7→4.9、页数 85→88→126；
        # 排版执行腿拿到的清单按 5000 字符截断、无严重度，改完没人量页数）。
        台 = 回路.台账(台账路径("美化台账"), 前缀="美")
        章路径们 = [f"论文/{f}" for f in 章文件]
        for 美轮 in range(1, 配置["美化轮数"] + 1):
            E, O, P, out = compile_paper(f"#美{美轮}")
            _记美化页数(P)                       # R52：G5 页数守卫的基线
            页们 = _渲染页()
            if not 页们:
                log("美化：无渲染页，跳过")
                return
            快照 = 镜像快照(章路径们)
            基线页 = P
            台账文本 = ("\n【上一轮台账】\n" + 台.渲染给评审腿(裁定文件="审稿/裁定_{名}.json")) if 美轮 > 1 and 台.条目 else ""
            规格 = []
            for bi in range(0, len(页们), 8):
                批 = 页们[bi:bi + 8]
                名 = f"美{美轮}_{bi//8+1}"
                规格.append((名, "美化师.md",
                             f"审附图各页（对应 {批[0]} 起连续{len(批)}页），输出写 审稿/{名}.json（按角色规范，每条页问题带 严重度 1-3 与可执行的 tex 级指令）。"
                             f"{台账文本.replace('{名}', 名)}\n完成写 日志/{名}.done",
                             批, False))
            名们 = 图片腿群(规格, 单腿超时=800, 等待=1000)        # R65：按并发上限分块起腿，不再整份同时起
            if 美轮 > 1 and 台.条目:
                收裁定(台, 名们)
            页问题集, 分数们 = [], []
            for 名 in 名们:
                v = get_json(f"审稿/{名}.json", 名) or {}
                页问题集 += [q for q in (v.get("页问题", []) or []) if isinstance(q, dict)]
                try:
                    分数们.append(float(v.get("美观分", 0)))
                except Exception:
                    pass
            美观 = sum(分数们) / len(分数们) if 分数们 else 0
            新条目 = []
            for q in 页问题集:
                try:
                    sev = max(1, min(3, int(q.get("严重度", 2))))
                except Exception:
                    sev = 2
                d = {"级别": "版式", "目标": "图" if str(q.get("目标", "")).strip() == "图" else "文",      # P6：美化师标 目标=图 → 绘图师
                     "问题": f"[严重度{sev}] 第{q.get('页')}页：{str(q.get('问题', ''))[:300]}",
                     "定位": f"页{q.get('页')}", "指令": str(q.get("修改指令", ""))[:400], "验收": "该页问题不再被指出", "来源": "美化师"}
                if q.get("对应"):
                    d["对应"] = str(q["对应"])
                新条目.append(d)
            统 = 台.并入(新条目, 美轮, "美化师")
            for x in 台.条目:                      # 严重度存在问题文本里，取出来排序用
                m = re.search(r"\[严重度(\d)\]", x.get("问题", ""))
                x["严重度"] = int(m.group(1)) if m else 2
            待 = 台.待改条目()
            log(f"S5b 美化轮{美轮}: 页问题{len(页问题集)}条 美观分均值{美观:.1f} 页数{P} 台账 新增{统['新增']} 合并{统['合并']} 重开{统['重开']} 待改{len(待)}")
            checkpoint([f"审稿/{n}.json" for n in 名们], f"S5b-美化{美轮}")
            if not 待 or 美观 >= 配置["美观阈值"] or not budget_ok():
                台.保存()
                return
            熔断处理(台, f"美化轮{美轮}")
            待 = sorted(台.待改条目(), key=lambda x: -int(x.get("严重度", 2)))[:15]     # 只派最严重的十几条，不按字符截断
            # P6（20260910 对照跑：两轮排版执行 30 条里 14 条被撰稿师退回「需转图」各多绕一轮）：
            # 美化师标 目标=图 的条目直接派绘图师改脚本并重出受影响图；tex 级条目照旧派撰稿师。两路都走同一次编译与页数守卫。
            图待 = [x for x in 待 if x.get("目标") == "图"]
            文待 = [x for x in 待 if x.get("目标") != "图"]
            if 图待 and budget_ok():
                图腿 = f"美化图{美轮}"
                wave([("绘图师.md", f"【美化·改图】第{美轮}轮：按角色文件「定向修改协议」改绘图脚本（只改不跑：字号/画布/图例/配色/留白，不改数据与结论），"
                       f"写 求解/成图美化{美轮}.sh 只重跑受影响图；成图脚本按「成图脚本纪律」。\n" +
                       台.渲染给修改腿(图待, 回执文件=f"审稿/回执_{图腿}.json") + f"\n完成写 日志/{图腿}.done", 图腿)], timeout=1100)
                收回执(台, 图腿)
                run_script(f"求解/成图美化{美轮}.sh", f"成图美化{美轮}", budget=800, repairs=1)
                checkpoint_dir("求解", f"S5b-美化图{美轮}")
            腿名 = f"排版执行{美轮}"
            if 文待:
                wave([("撰稿师.md", f"排版执行（第{美轮}轮）：按角色文件「定向修改协议」逐条改 tex（浮动体参数/图宽/表列宽/位置微调，不改文字内容）。"
                       f"**页数纪律**：当前 {P} 页，改完页数不得增加；不许引入 \\clearpage/\\newpage/[H]/整页浮动体/通栏放大——"
                       f"实测两轮美化把 85 页撑到 126 页，驱动会量页数并整轮回退。\n" +
                       台.渲染给修改腿(文待, 回执文件=f"审稿/回执_{腿名}.json") + f"\n完成写 日志/{腿名}.done", 腿名)], timeout=1100)
                收回执(台, 腿名)
            E2, O2, P2, out2 = _编译修复循环(f"美修{美轮}", 2)
            # 页数守卫：页数增加超过 1 页 → 整轮回退，本轮回执作废
            if isinstance(P2, int) and isinstance(基线页, int) and P2 > 基线页 + 1:
                回退(快照, f"美化轮{美轮}：页数 {基线页} → {P2}")
                for x in 台.条目:
                    if x.get("状态") == 回路.待复核 and any(r.get("腿") == 腿名 for r in x.get("回执", [])):
                        x["状态"] = 回路.未消解
                        x.setdefault("历史", []).append({"轮次": 美轮, "裁定": "页数守卫", "理由": f"页数 {基线页}→{P2}，整轮回退"})
                _, _, P3, _ = _编译修复循环(f"美退{美轮}", 1)
                _记美化页数(P3)
            else:
                _记美化页数(P2)
            台.保存()
    节点("S5:美化", _S5美化, 阶段="S5")

    # ---------- G5 出版门 ----------
    def _G5检查():
        _编译修复循环("终编", 2)
        台账视图上传(回路.台账(台账路径("审稿台账"), 前缀="审"), "审稿/审稿台账_视图.json")
        h.exec("python3 bin/审计.py > 日志/终审计.out 2>&1; echo ok", quiet=True)
        return 门检("G5")

    G5返工计数 = {"n": 0}

    def 页数守卫_G5(台, 快照, P, 标签):
        """R52（20260910 对照跑）：美化把 113 页压到 68 页之后，G5 返工撰稿师按「补入现有求解入口的引用」把 求解_问题3.py（2566 行）
        + 复算.py（757 行）整份 \\lstinputlisting 进守卫豁免的源码章（R47），68→112 页没人量。以 美化后页数 为基线（+10%，至少 2 页）：
        超线先回退本轮改过的代码章并重编译，仍超线再回退其余改动；被回退文件上的回执作废（待复核 → 未消解），新稿留底 审稿/回退稿/。"""
        基线 = 状态.数据.get("美化后页数")
        if not isinstance(基线, int) or 基线 <= 0 or not isinstance(P, int) or P <= 0 or not 快照:
            return []
        容差 = max(2, 基线 // 10)
        if P <= 基线 + 容差:
            return []
        改过 = [rel for rel, b in 快照.items() if (镜像目录 / rel).is_file() and (镜像目录 / rel).read_bytes() != b]
        代码章 = [rel for rel in 改过 if 回路.是代码章(pathlib.Path(rel).name, (镜像目录 / rel).read_text(encoding="utf-8", errors="replace"))]
        其余 = [rel for rel in 改过 if rel not in 代码章]
        回退过, 当前P = [], P
        for 批, 名 in ((代码章, "代码章"), (其余, "其余改动")):
            if not 批:
                continue
            for rel in 批:
                try:
                    留 = 镜像目录 / "审稿/回退稿" / 标签 / pathlib.Path(rel).name
                    留.parent.mkdir(parents=True, exist_ok=True)
                    留.write_bytes((镜像目录 / rel).read_bytes())
                except Exception as e:
                    log(f"回退稿留底失败 {rel}: {e}")
            回退({rel: 快照[rel] for rel in 批}, f"{标签}页数守卫")
            回退过 += 批
            _, _, 当前P, _ = compile_paper(f"#{标签}页数守卫")
            log(f"!! 页数守卫[{标签}]：{P} 页 > 美化后基线 {基线}+{容差} → 回退本轮{名} {[pathlib.Path(r).name for r in 批]} → {当前P} 页"
                f"（新稿留底 审稿/回退稿/{标签}/）")
            if isinstance(当前P, int) and 0 < 当前P <= 基线 + 容差:
                break
        名们 = [pathlib.Path(rel).name for rel in 回退过]
        for x in 台.条目:
            if x.get("状态") == 回路.待复核 and any(nm in str(x.get("定位", "")) + json.dumps(x.get("回执", [])[-1:], ensure_ascii=False) for nm in 名们):
                x["状态"] = 回路.未消解
                x.setdefault("历史", []).append({"轮次": 台.轮次, "裁定": "页数守卫", "理由": f"总页数 {P} > 美化后基线 {基线}+{容差}，相关文件回退作废"})
        台.保存()
        return 回退过

    def _G5返工(明细):
        # L0 教训：旧返工腿被逼出了最便宜的解——自造 硬伤_轮5.json（0 条）、删引用、自行销号、整章塞附录。
        # 现在：硬伤类意见走台账（回执 + 猎手复核裁定），其余门检明细逐条整改但封死那几条逃生路径。
        G5返工计数["n"] += 1
        n = G5返工计数["n"]
        台 = 回路.台账(台账路径("审稿台账"), 前缀="审")
        for x in 台.条目:                       # 搁置的阻塞级意见在 G5 再给一次机会
            if x.get("状态") == 回路.搁置 and x.get("级别") in 回路.阻塞级别:
                x["状态"] = 回路.待改
        阻 = 台.待改条目(级别们=回路.阻塞级别)
        # R49：G5 返工原本只派撰稿师，目标=图 的阻塞级（图内数字/标注与正式结果成套错版）在这里永远消不了 → 必然降级放行。
        # 补一条与 S5 回炉图同构的图路：绘图师按定向修改协议改脚本 + 成图回炉脚本重跑受影响图 → 再派撰稿师改文 → 硬伤猎手一并复核。
        图条 = [x for x in 阻 if x.get("目标") == "图"]
        if 图条 and budget_ok():
            图腿 = f"G5返工图{n}"
            wave([("绘图师.md", f"【G5返工·改图】出版门未通过（第{n}次返工）。按角色文件「定向修改协议」改绘图脚本（只改不跑），"
                   f"写 求解/成图回炉G5_{n}.sh 只重跑受影响图；图内数字/标注必须与 求解/*/结果/ 正式结果 JSON 一致（有换版清单则按清单同步）；成图脚本按「成图脚本纪律」。\n" +
                   台.渲染给修改腿(图条, 回执文件=f"审稿/回执_{图腿}.json") + f"\n完成写 日志/{图腿}.done", 图腿)], timeout=1300)
            收回执(台, 图腿)
            run_script(f"求解/成图回炉G5_{n}.sh", f"成图回炉G5_{n}", budget=800, repairs=1)
            checkpoint_dir("求解", f"G5-返工图{n}")
            阻 = 台.待改条目(级别们=回路.阻塞级别)     # 图条已进待复核，撰稿师只拿剩下的（文/算）
        其他 = [str(m) for m in (明细 if isinstance(明细, list) else [明细]) if "台账" not in str(m)]
        # R50：G5 没有算路。剩余阻塞级若全是 目标=算（撰稿师只能回「需转算」退回待改），派撰稿师+猎手复核纯属空转（每轮 ≈1 h）；
        # 直接记搁置理由交复盘/人工，门照常按判据走（搁置的阻塞级仍会让 G5 降级放行并高亮）。
        算条 = [x for x in 阻 if x.get("目标") == "算"]
        for x in 算条:
            x["状态"], x["搁置理由"] = 回路.搁置, "G5 无算路（出版前重算风险大），交复盘/人工"
            x.setdefault("历史", []).append({"轮次": 台.轮次, "裁定": "G5搁置", "理由": "目标=算，G5 不重算"})
        派文 = bool([x for x in 阻 if x.get("目标") != "算"] or 其他)
        if 算条:
            log(f"G5 返工{n}：{len(算条)} 条 目标=算 的阻塞级无路可派 → 搁置交复盘/人工：{[x['id'] for x in 算条]}" + ("" if 派文 else "；无其他可改项，不派撰稿师"))
        if not 派文 and not 图条:
            台.保存()
            台账视图上传(台, "审稿/审稿台账_视图.json")
            return
        腿名 = f"G5返工{n}"
        快照 = 镜像快照(["论文/论文.tex"] + [f"论文/{f}" for f in 章文件])   # 主控也入快照：结构守卫要核 \\input 集
        任务 = (f"出版门 G5 未通过（第{n}次返工）。按角色文件「定向修改协议」执行。\n"
                + (台.渲染给修改腿(阻, 回执文件=f"审稿/回执_{腿名}.json") + "\n" if 阻 else "")
                + f"其他门检明细，逐条整改 论文/*.tex：{json.dumps(其他, ensure_ascii=False)[:1500]}。\n"
                f"封死的做法（做了会被驱动回退并记越权）：不许为满足文献核验而删除 \\cite——只能改引用库内条目或删除依赖该文献的论断；"
                f"不许把正文整章移入附录来压页数（改动比例守卫 40%）；不许改写 交接/需求追踪矩阵.json 的状态字段（只在回执里写落位证据）；"
                f"页数纪律：美化后基线 {状态.数据.get('美化后页数') or '?'} 页，改完总页数不得超过基线 +10%——引用求解器源码只许 路径+SHA256+关键片段（linerange），"
                f"不许整份 \\lstinputlisting 求解脚本（页数守卫会回退代码章并作废回执）；存在 交接/换版清单_问题*.json 时先按它逐处同步旧值（协议 7）；"
                f"不许写任何 审稿/ 下的评审文件。完成写 日志/{腿名}.done")
        if 派文:
            wave([("撰稿师.md", 任务, 腿名)], timeout=1300)
            收回执(台, 腿名)
            变化守卫回退(台, 快照, [f"论文/{f}" for f in 章文件], 配置["变化守卫"], 腿名)
            结构守卫回退(快照, ["论文/论文.tex"] + [f"论文/{f}" for f in 章文件], 配置["变化守卫_升格"], 腿名)
        # R51（20260910 对照跑 16:10）：图路/文路改完不编译就派猎手，猎手按上一次门检的旧 PDF 判——图 13 已重绘成 2.17 μm，
        # 猎手仍按旧 PDF 判「仍标 2.58」→ 硬伤熔断搁置。复核前先编译（顺带量页数给 R52 守卫）。
        _, _, P, _ = _编译修复循环(f"G5返工{n}复核前", 1)
        if 派文:
            页数守卫_G5(台, 快照, P, 腿名)
        复核名 = f"硬伤{腿名}"
        wave([("硬伤猎手.md", f"G5 复核（第{n}次）。先按角色文件「配对评审协议」逐条裁定：\n" + 台.渲染给评审腿(裁定文件=f"审稿/裁定_{复核名}.json") +
               f"\n再按角色规范重新猎杀写 审稿/硬伤_G5复核{n}.json（空数组=清零）。"
               f"另外核对 交接/需求追踪矩阵.json：返工腿在回执里声称落位的需求，你核实后把状态改为 已落位 并填落位字段。完成写 日志/{复核名}.done", 复核名)], timeout=1300)
        收裁定(台, [复核名])
        新硬伤 = get_json(f"审稿/硬伤_G5复核{n}.json", f"硬伤G5复核{n}")
        新硬伤 = 新硬伤 if isinstance(新硬伤, list) else ((新硬伤 or {}).get("硬伤", []) or [])
        台.并入([{"级别": "硬伤", "目标": it.get("目标", "文"), "问题": str(it.get("证据") or it.get("类别", ""))[:600], "定位": str(it.get("定位", ""))[:200],
                 "指令": str(it.get("修改指令") or "")[:600], "验收": "硬伤猎手复查不再命中", "来源": "硬伤猎手", **({"对应": str(it["对应"])} if it.get("对应") else {})}
                for it in 新硬伤 if isinstance(it, dict)], 台.轮次 + 1)
        熔断处理(台, 腿名)
        台账视图上传(台, "审稿/审稿台账_视图.json")
        checkpoint([f"论文/{f}" for f in 章文件] + ["交接/需求追踪矩阵.json", "审稿/审稿台账_视图.json"], f"G5-返工{n}")
    节点("G5", lambda: 调度器.门("G5", _G5检查, 返工fn=_G5返工, 状态=状态, log=log).执行(), 阶段="G5")

    # ---------- S6 出版 + 复盘 ----------
    def _S6终审():
        E, O, P, out = compile_paper("#终")
        页们 = _渲染页()
        log(f"S6 终审：E={E} 页数={P} 渲染{len(页们)}页")
        if 页们 and budget_ok():
            规格 = []
            for bi in range(0, len(页们), 8):
                批 = 页们[bi:bi + 8]
                规格.append((f"终审_{bi//8+1}", "美化师.md",
                             f"【逐页终审·压轴】审附图各页（{批[0]} 起连续{len(批)}页）。这是交付前最后一道视觉关，只报**必须改**的问题。"
                             f"输出写 审稿/终审_{bi//8+1}.json（同角色规范 schema）。完成写 日志/终审_{bi//8+1}.done", 批, False))
            名们 = 图片腿群(规格, 单腿超时=800, 等待=1000)        # R65：按并发上限分块
            必改 = []
            for 名 in 名们:
                v = get_json(f"审稿/{名}.json", 名) or {}
                必改 += v.get("页问题", [])
            log(f"S6 逐页终审：{len(必改)} 条必改")
            checkpoint([f"审稿/终审_{i+1}.json" for i in range(len(名们))], "S6-终审")
            if 必改 and budget_ok():
                # R53（对照跑复盘官实证）：必改清单按 5000 字符截断，14 条只下发 10 条且第 10 条句中截断——任务只传文件与条数，腿自己读全。
                wave([("撰稿师.md", f"终审整改（只改版式不改文字）：共 {len(必改)} 条必改，全部在 " + "、".join(f"审稿/{n}.json" for n in 名们) +
                       f" 的 页问题 里，逐条读全、逐条处理，不许只做前几条；目标=图 的（要改绘图脚本才能解决）在回执里标「需转图」并写图名，其余逐条改 tex。"
                       f"回执写 审稿/回执_终审整改.json（数组：页/受理/改动/未改原因）。完成写 日志/终审整改.done", "终审整改")], timeout=1100)
                _编译修复循环("终整改", 2)
    节点("S6:终审", _S6终审, 阶段="S6")

    def _S6出版():
        E, O, P, out = compile_paper("#出版")
        h.exec("python3 bin/审计.py > 日志/终审计.out 2>&1; echo ok", quiet=True)
        for 门名 in ("G0", "G2", "G3", "G4", "G5", "叙事"):
            h.exec(f"python3 bin/门检.py {门名} > /dev/null 2>&1; echo ok", quiet=True, timeout_s=200)
        终 = ["论文/论文.pdf", "审稿/审计报告.json", "交接/实验记录.json", "交接/求解计划.md"]
        d = h.exec("ls 论文/*.tex 论文/页/*.png 审稿/*.json 交接/*.json 交接/*.md 2>/dev/null; "
                   "find 求解 -name '*.py' -o -name '*.json' -o -name '*.png' -o -name '*.sh' -o -name '*.xlsx' 2>/dev/null", quiet=True)   # R66：结果模板交付物 result*.xlsx 也要收割
        for l in (d.get("stdout") or "").split():
            if l and not l.endswith(":"):
                终.append(l.replace("/tmp/蜂巢/", ""))
        got = h.harvest(sorted(set(终)), str(产出目录))
        log(f"S6 终稿收割 {len(got)} 文件 → {产出目录}")
        log(f"终态：E={E} Overfull={O} 页数={P} 总腿数={LEG_COUNT} 用时{int((time.time()-T_START)/60)}分钟")
    节点("S6:出版", _S6出版, 阶段="S6")

    def _S6复盘():
        # 把本地运行日志上传给复盘官
        try:
            日志文本 = 日志文件.read_text(encoding="utf-8")[-60000:]
        except Exception:
            日志文本 = "（运行日志读取失败）"
        h.exec("echo 日志上传", files=[h.f_text("交接/运行日志.md", 日志文本),
                                    h.f_text("交接/状态.json", json.dumps(状态.数据, ensure_ascii=False, indent=1))], quiet=True)
        页们 = []
        d = h.exec("ls 论文/页/*.png | sort", quiet=True)
        页们 = [l for l in (d.get("stdout") or "").split() if l.endswith(".png")]
        抽样 = (页们[:1] + 页们[1:2] + 页们[len(页们)//2:len(页们)//2+2] + 页们[-2:]) if 页们 else []
        if 抽样:
            名 = 图片腿("复盘", "复盘官.md",
                       "复盘本次运行（**复盘系统本身，不是复盘论文**）。读 交接/运行日志.md、交接/状态.json、审稿/ 全部 JSON（审稿意见/硬伤/评委模拟/修订单/审计报告/门检报告）、"
                       "交接/实验记录.json。附图为终稿抽样页。产出 审稿/复盘报告.json（严格按角色规范 schema：总评/瓶颈环节/规则库修改建议/下次运行参数建议）。"
                       "规则库修改建议必须 ≥3 条且每条可直接执行（指名文件+改什么+为什么）。完成写 日志/复盘.done", 抽样[:8])
            等腿([名], timeout=1100)
        else:
            wave([("复盘官.md", "复盘本次运行（复盘系统本身）。读 交接/运行日志.md、交接/状态.json、审稿/ 全部 JSON、交接/实验记录.json。"
                   "产出 审稿/复盘报告.json（按角色规范 schema），规则库修改建议 ≥3 条且可执行。完成写 日志/复盘.done", "复盘")], timeout=1100)
        报告 = get_json("审稿/复盘报告.json", "复盘报告", "复盘官.md", "严格按 复盘官 schema：总评/瓶颈环节/规则库修改建议/下次运行参数建议")
        checkpoint(["审稿/复盘报告.json"], "S6-复盘")
        if 报告:
            h.harvest(["审稿/复盘报告.json"], str(产出目录))
            log(f"S6 复盘报告：瓶颈{len(报告.get('瓶颈环节', []))}项 规则库建议{len(报告.get('规则库修改建议', []))}条")
    节点("S6:复盘", _S6复盘, 阶段="S6")


try:
    if 续跑生效:
        log(f"===== 续跑（--resume）：运行ID={状态.数据['运行ID']} 已完成节点={len(状态.数据['已完成节点'])} 腿数={LEG_COUNT} 引擎={配置['引擎']} =====")
        if 上段引擎 and 上段引擎 != 配置["引擎"]:
            log(f"!! 腿引擎切换：上一段 {上段引擎} → 本段 {配置['引擎']}（产物契约一致；沙箱/附图/系统提示口径不同，见 skill references/角色与契约.md §引擎）")
        本地就绪()
    else:
        log(f"===== 全新运行：运行ID={状态.数据['运行ID']} 档位={配置['档位']} 角色分档={'开' if 配置['角色档位'] else '关'} 引擎={配置['引擎']} =====")
    主流程()
    状态.设阶段("完成")
except 预算耗尽 as e:
    # 预算护栏是**正常刹车**不是崩溃：尽量把当前已有材料收成一份可交付的稿子
    log(f"!! 预算护栏收尾：{e}")
    状态.设阶段("预算耗尽收尾")
    try:
        应急出版()
    except Exception as e2:
        log(f"应急出版也失败：{e2}")
except Exception as e:
    log(f"!! 驾驶异常终止于阶段{运行态['阶段']}: {e}\n{traceback.format_exc()[:800]}")
finally:
    状态.数据["腿数"] = LEG_COUNT
    状态.保存()
    with open(产出目录.parent / "运行报告.md", "w", encoding="utf-8") as f:
        f.write(f"# 蜂群自主运行报告\n\n运行ID：{状态.数据['运行ID']}｜阶段到达：{运行态['阶段']}｜腿数：{LEG_COUNT}｜"
                f"用时：{int((time.time()-T_START)/60)} 分钟｜档位：{配置['档位']}\n\n")
        f.write(f"已完成节点（{len(状态.数据['已完成节点'])}）：{状态.数据['已完成节点']}\n\n")
        f.write(f"问题门状态：{状态.数据['问题门状态']}\n\n门失败计数：{状态.数据['门失败计数']}\n\n")
        if 状态.数据.get("降级放行"):
            f.write(f"**降级放行（需人工关注）**：{json.dumps(状态.数据['降级放行'], ensure_ascii=False)}\n\n")
        f.write("## 事件流\n")
        for ev in 运行态["事件"]:
            f.write(f"- {ev}\n")
    print("驾驶结束")
