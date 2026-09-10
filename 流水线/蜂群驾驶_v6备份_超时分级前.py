#!/usr/bin/env python3
"""蜂群驾驶 v4「论文铸造厂」：全自主跑完一道数模题。
S0 吃透题目 →G0→ S1 战略锦标赛 →G1→ S2 建模求解(依赖DAG+红队) →G2/问→ S3 图证 →G3→
S4 撰稿(叙事+章评+摘要蜂群) →G4→ S5 审稿场(五路并审+分级修订+定向回炉) →G5→ S6 出版+复盘

用法: python3 蜂群驾驶.py <输入目录(含题目PDF与附件)> <产出目录> [--resume] [--快速]
设计原则：所有角色腿统一 nohup 异步 + done 标记轮询；每腿失败重试一次；关键 JSON 校验失败带反馈重写一次；
节点原子化+状态机（断点续跑）；六道门一票否决、连败升格蜂群；上游变更级联重算；全程规则驱动零人工。
"""
import base64
import glob
import json
import os
import pathlib
import sys
import time
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from hive_sdk import Hive
import 调度器

参数 = [a for a in sys.argv[1:] if not a.startswith("--")]
选项 = [a for a in sys.argv[1:] if a.startswith("--")]
输入目录 = pathlib.Path(参数[0] if len(参数) > 0 else "/Users/bytedance/数模自动化/真题测试/输入")
产出目录 = pathlib.Path(参数[1] if len(参数) > 1 else "/Users/bytedance/数模自动化/真题测试/成品")
续跑 = "--resume" in 选项
镜像目录 = 产出目录.parent / "蜂巢镜像"
流水线 = pathlib.Path(__file__).parent
日志文件 = 产出目录.parent / "运行日志.md"
状态文件 = 产出目录.parent / "状态.json"
产出目录.mkdir(parents=True, exist_ok=True)

# ---------- 项目内资产路径（工单0.1：已彻底消灭 scratchpad 外部依赖） ----------
项目根 = 流水线.parent
轮子目录 = 项目根 / "沙箱验证/wheels"
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
    "MAX_LEGS": 220,
    "MAX_HOURS": 14,
    # 多实例适配（实测网关会把同账号分发到多个容器，account 参数不控路由）
    "探活次数": 3,           # 每次 ensure_hive 探几次，覆盖到不同实例
    "播种轮数": 3,           # 发现缺蜂巢时连播几轮（无亲和控制，靠多播提高命中）
    "连续确认次数": 3,       # 连续探到几次 alive 才认为蜂巢真就绪（防摇摆）
    "腿静默上限": 900,       # 波次内 done 数长时间不涨 → 判死波，重播种重派
}
if "--快速" in 选项:
    # 快速档用于工单4.1 低配冒烟：目的是让**所有新节点各执行一次**，
    # 因此审稿轮数必须 ≥2（轮1 生成修订单并走定向回炉，轮2 才可能收敛退出）。
    配置.update({"档位": "快速", "全问开锦标赛": False, "每问路线数": 2, "摘要变体数": 3,
                 "图评轮数": 1, "章评轮数": 1, "审稿轮数": 2, "美化轮数": 1,
                 "MAX_LEGS": 220, "MAX_HOURS": 14})

h = Hive(account="a1")
状态 = 调度器.状态机(状态文件)
if 续跑 and 状态.加载():
    # 预算按**实际在跑的机时**累计，而不是首次启动至今的墙钟时间。
    # 教训：中途停跑做分析/改代码的间隔若计入预算，会凭空吃掉额度
    # （本轮三次续跑之间的间隔让"用时"虚高到 20.4h，而真实机时远低于此）。
    已用机时 = 状态.数据.get("累计机时", 0)
    T_START = time.time() - 已用机时
    状态.数据.setdefault("起始时间", time.time())
    续跑生效 = True
else:
    T_START = time.time()
    状态.数据["起始时间"] = T_START
    状态.数据["累计机时"] = 0
    状态.保存()
    续跑生效 = False
LEG_COUNT = 状态.数据.get("腿数", 0)
MAX_LEGS = 配置["MAX_LEGS"]
MAX_HOURS = 配置["MAX_HOURS"]
探活次数 = 配置["探活次数"]
播种轮数 = 配置["播种轮数"]
连续确认次数 = 配置["连续确认次数"]
腿静默上限 = 配置["腿静默上限"]
实例池 = set()            # 见过的容器 hostname（多实例现象的观测记录）
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


def budget_ok():
    return LEG_COUNT < MAX_LEGS and (time.time() - T_START) < MAX_HOURS * 3600


def wave(legs, timeout=1300, poll=30, retry=True):
    """异步跑一组腿到 done。legs=[(role,task,name)]。返回 {name: done首行}。失败重试一次。

    韧性（实测教训）：容器每 105-181 分钟轮换一次。若轮换发生在**波次进行中**，
    旧腿随容器一起消失，而轮询只会对着新的空容器一直查到 timeout 用尽，再"重试"一次
    又可能撞上下一次轮换——问2 就是这样连烧 4 小时也没产出。
    因此轮询时必须探活：一旦发现蜂巢没了，立刻重播种并**当轮重派**，不等 timeout。
    """
    global LEG_COUNT
    pending = list(legs)
    results = {}
    attempt = 0
    重派次数 = 0
    while pending and attempt < 2:
        attempt += 1
        # ---- 原子派发：确保"就绪判定"与"派腿"落在**同一个实例**上 ----
        # 事故复盘：旧实现 ensure_hive() 与 h.leg() 是两次独立请求，中间被网关重路由
        # 就会把腿派进空容器——腿永远写不出 done，而驱动以为一切正常。
        # 现在派发后立刻回读指纹并校验任务文件真的落地了；不一致就重播种重派。
        派发指纹 = None
        for 试 in range(3):
            ok, fp = ensure_hive()
            派发指纹 = fp
            for role, task, name in pending:
                h.exec(f"rm -f 日志/{name}.done", quiet=True)
                h.leg(role, task, name, sync=False)
            # 回收校验：任务文件是否都在？指纹是否还是派发时那个？
            名单 = " ".join(f"任务/{n}.任务.md" for _, _, n in pending)
            d = h.exec(f"c=0; for f in {名单}; do [ -f $f ] && c=$((c+1)); done; echo $c; "
                       f"printf '%s@%s' \"$(hostname | cut -c1-12)\" "
                       f"\"$(cut -c1-8 /proc/sys/kernel/random/boot_id 2>/dev/null)\"", quiet=True)
            o = (d.get("stdout") or "").strip().split()
            落地 = int(o[0]) if o and o[0].isdigit() else 0
            回读指纹 = o[-1] if len(o) > 1 else "?"
            if 落地 >= len(pending) and 回读指纹 == 派发指纹:
                break
            log(f"!! 原子派发校验失败（第{试+1}次）：任务落地 {落地}/{len(pending)}，"
                f"派发@{派发指纹} → 回读@{回读指纹}——重播种后重派")
            ensure_hive()
        for _ in pending:
            LEG_COUNT += 1
            状态.加腿(1)
        log(f"波次启动 {len(pending)} 腿(第{attempt}次)@{派发指纹}: {[n for _, _, n in pending]}")
        t0 = time.time()
        轮换重派 = False
        上次cnt, 上次进展时刻 = -1, time.time()
        while time.time() - t0 < timeout:
            time.sleep(poll)
            marks = " ".join(f"日志/{n}.done" for _, _, n in pending)
            # 一次探测同时拿到：done 数、蜂巢在不在、落在哪个实例（指纹）。
            # 不再做"多探取最大值"——那会掩盖"腿被派进空实例"这一致命情形。
            d = h.exec(f"c=0; for f in {marks}; do [ -f $f ] && c=$((c+1)); done; echo $c; "
                       f"[ -f AGENTS.md ] && [ -f bin/role.sh ] && echo HIVE_ALIVE; "
                       f"printf '%s@%s' \"$(hostname | cut -c1-12)\" "
                       f"\"$(cut -c1-8 /proc/sys/kernel/random/boot_id 2>/dev/null)\"", quiet=True)
            out = (d.get("stdout") or "")
            见活 = "HIVE_ALIVE" in out
            当前fp = out.strip().split()[-1] if out.strip() else "?"
            try:
                cnt = int(out.strip().split()[0])
            except Exception:
                cnt = 0
            if cnt >= len(pending):
                break
            if cnt > 上次cnt:
                上次cnt, 上次进展时刻 = cnt, time.time()
            静默 = time.time() - 上次进展时刻
            # 判死波三条判据（任一即判死，不等 timeout）：
            # 1. 蜂巢没了；2. done 数长时间零进展；3. **实例指纹变了**（腿所在实例已消失）。
            # 第 3 条是本次新增：指纹一变就说明腿的宿主没了，再等下去毫无意义。
            指纹漂移 = (当前fp != "?" and 派发指纹 and 当前fp != 派发指纹)
            if (not 见活 or 静默 > 腿静默上限 or 指纹漂移) and 重派次数 < 2:
                因 = ("蜂巢丢失" if not 见活 else
                      f"实例指纹漂移 {派发指纹}→{当前fp}（腿宿主已消失）" if 指纹漂移 else
                      f"done 数 {静默:.0f}s 零进展（疑似腿派进空实例）")
                log(f"!! 波次判死：{因}（已等 {int(time.time()-t0)}s，done={cnt}/{len(pending)}）"
                    f"→ 重播种并重派本波 {len(pending)} 腿")
                ensure_hive()
                重派次数 += 1
                attempt -= 1          # 环境问题不算腿自身失败，不吃掉重试额度
                轮换重派 = True
                break
        if 轮换重派:
            continue
        # 腿刚落地就把增量搬回本地（write-through）：此刻产物一定在容器里，
        # 越早搬走越安全——晚一步撞上轮换就永久丢失。
        即时回传(f"波次-{pending[0][2] if pending else '空'}")
        nxt = []
        for role, task, name in pending:
            # done 首行同样要多探：落到没有该文件的实例会读成空 → 误判腿失败
            first = ""
            for _ in range(探活次数):
                d = h.exec(f"head -1 日志/{name}.done 2>/dev/null", quiet=True)
                s = (d.get("stdout") or "").strip()
                if s:
                    first = s
                    break
            if not first or first.startswith("AUTO"):
                if retry and attempt == 1 and budget_ok():
                    nxt.append((role, task, name))
                    log(f"腿失败待重试: {name} ({first[:40]})")
                else:
                    results[name] = f"FAILED:{first[:60]}"
                    log(f"腿最终失败: {name}")
            else:
                results[name] = first
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

    韧性：与 wave 同理，脚本跑到一半容器轮换会让进程消失，轮询必须探活，
    否则会白等满 budget 再判失败（实测问2 因此浪费 25 分钟并把 rc 记成 None）。
    """
    global LEG_COUNT
    执行器 = "bash" if script_path.endswith(".sh") else "python3"
    attempt = 0
    轮换重跑 = 0
    while attempt <= repairs:
        attempt += 1
        ensure_hive()
        h.exec(f"rm -f 日志/{log_name}.done; nohup bash -c '{执行器} {script_path} > 日志/{log_name}.log 2>&1; echo rc=$? > 日志/{log_name}.done' >/dev/null 2>&1 & echo started", quiet=True)
        t0 = time.time()
        rc = None
        轮换了 = False
        while time.time() - t0 < budget:
            time.sleep(25)
            # 一次探测拿 rc + 蜂巢 + 进程是否还在。脚本被派进空实例、或实例轮换后
            # 进程随之消失时，rc 永远不会出现——必须靠"进程不在且无 rc"提前判死，
            # 否则白等满 budget（实测单次 22 分钟）。
            d = h.exec(f"cat 日志/{log_name}.done 2>/dev/null; "
                       f"[ -f AGENTS.md ] && echo HIVE_ALIVE; "
                       f"pgrep -f '{script_path}' >/dev/null && echo RUNNING", quiet=True)
            out = (d.get("stdout") or "")
            见活 = "HIVE_ALIVE" in out
            在跑 = "RUNNING" in out
            rc = None
            for line in out.splitlines():
                if line.strip().startswith("rc="):
                    rc = int(line.strip()[3:].split()[0])
                    break
            if rc is not None:
                break
            # 蜂巢没了，或（蜂巢在但进程也不在且没 rc）→ 脚本被"吃掉"了
            失联 = (not 见活) or (见活 and not 在跑 and time.time() - t0 > 90)
            if 失联 and 轮换重跑 < 2:
                因 = "蜂巢丢失" if not 见活 else "进程消失且无 rc（疑似派进空实例/实例已轮换）"
                log(f"!! 脚本 {script_path} 判死：{因}（已等 {int(time.time()-t0)}s）"
                    f"→ 重播种后重跑，不计入修复次数")
                ensure_hive()
                轮换重跑 += 1
                attempt -= 1
                轮换了 = True
                break
        if 轮换了:
            continue
        log(f"脚本 {script_path} 第{attempt}次 rc={rc}")
        if rc == 0:
            # 脚本产出（结果 JSON / 图 png）是最贵的资产，跑完立刻搬回本地
            即时回传(f"脚本-{log_name}")
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
    try:
        got = h.harvest(paths, str(镜像目录))
        log(f"checkpoint[{tag}]: {len(got)} 文件")
    except Exception as e:
        log(f"checkpoint[{tag}] 失败: {e}")


def checkpoint_dir(remote_dir, tag):
    """收割整个远端目录（按文件清单）。"""
    d = h.exec(f"find {remote_dir} -type f 2>/dev/null | head -80", quiet=True)
    paths = [l.replace("/tmp/蜂巢/", "") for l in (d.get("stdout") or "").split() if l and "/" in l]
    if paths:
        checkpoint(paths, tag)


# ============================================================ 腿级即时回传（write-through）
回传水位 = {"戳": 0}          # 上次成功回传的时间戳（容器侧 mtime 基准）
回传统计 = {"次数": 0, "文件": 0, "失败": 0}


def 即时回传(标签="", 目录="交接 求解 论文 审稿 日志", 上限MB=6):
    """把容器里**自上次回传以来新增/改动**的产物一次性拉回本地镜像。

    动机（架构级）：所有中间产物原本只存在于容器 `/tmp/蜂巢`，容器一轮换就全丢，
    只能靠阶段性 checkpoint + 本地镜像重播种兜底——**腿级产物存在丢失窗口**
    （实测容器 4 小时内换了 4 个实例，最坏情况一条刚跑完的腿产出直接蒸发）。
    本函数让容器退化为**纯计算资源**：每波腿一落地就把增量搬回本地，
    本地镜像始终是唯一权威副本，容器随时可丢可重建。

    实现要点（均已实测）：
    - `find -newermt '@戳'` 取增量（GNU findutils 4.10.0 支持）；
    - `tar --null -T -` + `base64 -w0` **一次 exec 拉回全部增量**，
      而不是每个文件一次 harvest（后者在多实例摇摆下极易漏文件且慢）；
    - 首次调用（戳=0）自动退化为"全量回传"，用于续跑/重播种后对齐；
    - 单包超过 上限MB 时按目录拆分重试，避免响应体过大被网关截断。
    """
    起 = 回传水位["戳"]
    条件 = f"-newermt '@{起}'" if 起 else ""
    脚本 = (f"find {目录} -type f {条件} ! -name '*.whl' ! -path '*/pylibs/*' -print0 2>/dev/null "
            f"| tar --null -czf /tmp/_回传.tgz -T - 2>/dev/null; "
            f"S=$(stat -c%s /tmp/_回传.tgz 2>/dev/null || echo 0); "
            f"if [ \"$S\" -gt 0 ] && [ \"$S\" -lt {上限MB * 1024 * 1024} ]; then base64 -w0 /tmp/_回传.tgz; fi; "
            f"echo; echo ---B64END---$S")
    try:
        d = h.exec(脚本, timeout_s=300, quiet=True)
        out = (d.get("stdout") or "")
        if "---B64END---" not in out:
            回传统计["失败"] += 1
            return 0
        b64, _, 尾 = out.rpartition("---B64END---")
        b64 = b64.strip()
        尺寸 = int((尾 or "0").strip() or 0)
        if 尺寸 == 0:
            回传水位["戳"] = int(time.time()) - 5      # 无增量也推进水位（留 5s 重叠防漏）
            return 0
        if not b64:
            log(f"!! 即时回传[{标签}] 增量包 {尺寸/1e6:.1f}MB 超过 {上限MB}MB，改走目录级 checkpoint 兜底")
            回传统计["失败"] += 1
            for d1 in 目录.split():
                checkpoint_dir(d1, f"超限兜底-{标签}-{d1}")
            回传水位["戳"] = int(time.time()) - 5
            return 0
        import io
        import tarfile
        buf = io.BytesIO(base64.b64decode(b64))
        n = 0
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            for m in tf.getmembers():
                if not m.isfile():
                    continue
                名 = m.name.lstrip("./")
                if ".." in 名:
                    continue
                目标 = 镜像目录 / 名
                目标.parent.mkdir(parents=True, exist_ok=True)
                f = tf.extractfile(m)
                if f:
                    目标.write_bytes(f.read())
                    n += 1
        回传水位["戳"] = int(time.time()) - 5
        回传统计["次数"] += 1
        回传统计["文件"] += n
        if n:
            log(f"即时回传[{标签}]: {n} 文件 / {尺寸/1e3:.0f}KB（累计 {回传统计['文件']} 文件）")
        return n
    except Exception as e:
        回传统计["失败"] += 1
        log(f"!! 即时回传[{标签}] 失败（不阻塞）: {str(e)[:120]}")
        return 0


def 基建文件():
    """容器基建+运行时资产的上传清单（播种与重播种共用）。"""
    files = [h.f_local("AGENTS.md", 流水线 / "AGENTS.md"),
             h.f_local("bin/role.sh", 流水线 / "运行时/role.sh"),
             h.f_local("bin/审计.py", 流水线 / "运行时/审计.py"),
             h.f_local("论文/format.cls", 模板CLS)]
    for n in ["循环.py", "门检.py"]:
        fp = 流水线 / "运行时" / n
        if fp.exists():
            files.append(h.f_local(f"bin/{n}", fp))
    for p in (流水线 / "角色").glob("*.md"):
        files.append(h.f_local(f"角色/{p.name}", p))
    for n in ["禁用词.txt", "优秀论文标准.md", "文献卡片库.md", "方法卡片库.md"]:
        fp = 流水线 / "运行时" / n
        if fp.exists():
            files.append(h.f_local(f"运行时/{n}", fp))
    if HMML路径.exists():
        files.append(h.f_local("运行时/HMML.md", HMML路径))
    for w in 轮子目录.glob("*.whl"):
        files.append(h.f_local(f"运行时/wheels/{w.name}", w))
    for t in 模板参考目录.glob("*.tex"):
        files.append(h.f_local(f"运行时/模板参考/{t.name}", t))
    for p in sorted(输入目录.iterdir()):
        if p.suffix.lower() == ".pdf":
            files.append(h.f_local(f"题目/{p.name}", p))
        elif p.suffix.lower() in (".xlsx", ".csv", ".docx"):
            files.append(h.f_local(f"数据/{p.name}", p))
    return files


def 蜂巢就绪(强制播种=False):
    """返回 (就绪bool, 指纹)。**单次**探测——调用方决定要不要重试。

    指纹 = `boot_id`（容器每次启动唯一，hostname 复用也能识别出换了实例）+ hostname。
    实测 `/proc/sys/kernel/random/boot_id` 可读，是比 hostname 更可靠的实例身份。

    与 ensure_hive 的分工：本函数只回答"我这一次打到的实例，蜂巢在不在"，
    不做多次采样、不做"只要有一个活就算活"的聚合。实测教训：网关会在
    多个实例间来回路由（一次运行里见过 e3d2848d0eb3 / d4e91fc29c53 /
    c2c6f914db0a / abd0da3ddbef 四个），"有一个活就算活"会导致腿被派到
    空实例上永远跑不完，而驱动却以为一切正常（实测白等 70 分钟）。
    """
    d = h.exec("printf '%s|%s|' \"$(hostname)\" \"$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)\"; "
               "[ -f AGENTS.md ] && [ -f bin/role.sh ] && echo alive || echo missing", quiet=True)
    out = (d.get("stdout") or "").strip()
    if "|" not in out:
        return False, "?"
    主机, _, 余 = out.partition("|")
    boot, _, 态 = 余.partition("|")
    指纹 = f"{主机[:12]}@{boot[:8]}"
    return 态.strip() == "alive", 指纹


def 当前指纹():
    """只取指纹不判就绪（用于回收时校验是否还是同一实例）。"""
    _, fp = 蜂巢就绪()
    return fp


def 播种一次(files=None):
    """把基建+镜像播到**当前这一次**路由到的实例上。返回该实例 hostname。"""
    if files is None:
        files = 基建文件()
        if 镜像目录.exists():
            for p in 镜像目录.rglob("*"):
                if p.is_file() and p.stat().st_size < 8_000_000:
                    files.append(h.f_local(str(p.relative_to(镜像目录)), p))
    d = h.exec("mkdir -p 日志 任务 交接 求解 论文 审稿 pylibs && chmod +x bin/role.sh && "
               "([ -d pylibs/sklearn ] || pip3 install --quiet --no-index --no-deps --no-compile "
               "--target=/tmp/蜂巢/pylibs 运行时/wheels/*.whl); printf '播种于 %s' \"$(hostname)\"",
               files=files, timeout_s=560, quiet=True)
    return (d.get("stdout") or "").strip()[-14:]


def ensure_hive(最多播种=4):
    """保证"当前能打到的实例"有蜂巢；必要时反复播种直到探到 alive。

    实测（2026-08-27，第二次全开跑）：容器会真轮换（旧实例整体消失，换成
    全新 hostname 且 /tmp/蜂巢 为空），也会多实例并存。旧实现"多探几次、
    只要有一个 alive 就返回 True"在**混合**情形下最危险：它会在
    "一个已播种 + 一个空"之间反复摇摆，宣布"一切正常"，然后把腿派到空实例，
    腿永远写不出 done → 波次白等到超时（实测单次白烧 4184 秒）。

    现在的判据：连续探测，**只要探到 missing 就立刻播种**，直到连续
    连续确认次数 次都 alive 才认为就绪。宁可多播（幂等、25 秒级），
    也不要把腿派进空容器。

    返回 (就绪bool, 指纹)——指纹给调用方做"派发/回收同实例"校验。
    """
    连续 = 0
    播种次数 = 0
    末指纹 = "?"
    while True:
        ok, 指纹 = 蜂巢就绪()
        末指纹 = 指纹
        if ok:
            实例池.add(指纹)
            连续 += 1
            if 连续 >= 连续确认次数:
                return True, 指纹
            continue
        # 探到空实例 → 立刻播种到它上面
        if 播种次数 >= 最多播种:
            log(f"!! 实例 {指纹} 缺蜂巢，已连播 {播种次数} 次仍未稳定（实例池 {sorted(实例池)}）——继续推进，"
                f"后续腿可能失败并触发重试")
            return False, 指纹
        连续 = 0
        播种次数 += 1
        实例池.add(指纹)
        落 = 播种一次()
        log(f"!! 实例 {指纹} 缺蜂巢 → 第{播种次数}次播种（{落}；累计实例池 {sorted(实例池)}）")
        # 播种后立刻把本地镜像里的既有状态推回去（续跑/轮换后对齐），
        # 并重置回传水位，避免把刚推上去的文件当"新增"又拉回来一遍。
        回传水位["戳"] = int(time.time()) - 5


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
    """看图腿：codex -i 批图（≤8 张）+ `--` 分隔提示词。铁则 §二.4。"""
    global LEG_COUNT
    LEG_COUNT += 1
    状态.加腿(1)
    imgs = " ".join(f'-i "{p}"' for p in 图们[:8])
    效 = ' -c model_reasoning_effort="high"' if reasoning else ""
    h.exec(f"rm -f 日志/{名}.done", quiet=True)
    script = f'''cat 角色/{角色文件} > 任务/{名}.txt
cat >> 任务/{名}.txt <<'TASKEOF'

=== 本次任务 ===
{任务文本}
TASKEOF
nohup bash -c 'timeout {timeout} codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -c model_provider="gw" -c "model_providers.gw.name=\\"gw\\"" -c "model_providers.gw.base_url=\\"https://proxy:18080/v1\\"" -c "model_providers.gw.env_key=\\"OPENAI_API_KEY\\"" -c "model_providers.gw.wire_api=\\"responses\\""{效} -m gpt-5.6-sol {imgs} -- "$(cat 任务/{名}.txt)" > 日志/{名}.log 2>&1; [ -f 日志/{名}.done ] || echo AUTO > 日志/{名}.done' >/dev/null 2>&1 &
echo {名}启动'''
    h.exec(script, timeout_s=90, quiet=True)
    return 名


def 等腿(名们, timeout=1100, poll=30):
    """等一批（图片腿等）自建腿的 done 标记。"""
    t0 = time.time()
    while time.time() - t0 < timeout and 名们:
        time.sleep(poll)
        marks = " ".join(f"日志/{n}.done" for n in 名们)
        d = h.exec(f"c=0; for f in {marks}; do [ -f $f ] && c=$((c+1)); done; echo $c", quiet=True)
        try:
            if int((d.get("stdout") or "0").strip().split()[-1]) >= len(名们):
                return True
        except Exception:
            pass
    return False


def 门检(门名, timeout_s=200):
    """容器内跑 运行时/门检.py，返回 (通过bool, 明细)。"""
    d = h.exec(f"python3 bin/门检.py {门名} 2>&1 | tail -5", timeout_s=timeout_s, quiet=True)
    报告 = get_json(f"审稿/门检_{门名}.json", f"门检{门名}") or {}
    通过 = bool(报告.get("通过"))
    明细 = 报告.get("明细", (d.get("stdout") or "")[:300])
    return 通过, 明细


def 升格蜂群(节点名, 角色, 任务模板, 裁决任务, 变体数=3):
    """连败两次的环节升格蜂群：3 条差异化变体腿 + 1 条裁决腿。
    任务模板 含 {切入} 占位；裁决任务 为解读师同口径评。"""
    切入们 = ["换主方法族：放弃当前方法族，改用另一大类方法重做",
              "换数据口径/特征：保持方法族但更换数据口径、特征构造或预处理链重做",
              "保方法改激进修复：沿用当前方法但对暴露的缺陷做激进修正（重写关键实现）"]
    legs = []
    for k, 切 in enumerate(切入们[:变体数], 1):
        legs.append((角色, 任务模板.replace("{切入}", 切).replace("{变体号}", str(k)) +
                     f"\n完成写 日志/升格_{节点名}_{k}.done", f"升格_{节点名}_{k}"))
    log(f"升格蜂群[{节点名}]：派 {len(legs)} 条变体腿")
    wave(legs, timeout=1300)
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
    ensure_hive()
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

    # ---------- S0a 播种 ----------
    def _播种():
        log(f"S0 播种开始（档位={配置['档位']}，清空旧蜂巢，全量重建）")
        files = 基建文件()
        h.exec("cd /tmp && rm -rf 蜂巢.old && mv 蜂巢 蜂巢.old 2>/dev/null; echo cleared", quiet=True)
        d = h.exec(r'''mkdir -p 日志 任务 交接 求解 论文 审稿 pylibs
chmod +x bin/role.sh
pip3 install --quiet --no-index --no-deps --no-compile --target=/tmp/蜂巢/pylibs 运行时/wheels/*.whl && echo wheels装完
python3 - <<'EOF'
import sys; sys.path.insert(0, '/tmp/蜂巢/pylibs')
import sklearn, openpyxl
from pypdf import PdfReader
import glob
for pdf in glob.glob('题目/*.pdf'):
    r = PdfReader(pdf)
    txt = "\n".join(p.extract_text() or "" for p in r.pages)
    open('交接/题目原文.txt', 'w', encoding='utf-8').write(txt)
    print(pdf, len(r.pages), '页', len(txt), '字')
EOF
which xelatex && echo 就绪''', files=files, timeout_s=560)
        log(f"S0 播种: {(d.get('stdout') or '')[:150]}")
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
                    f"④实验记录追加。脚本运行时间控制在12分钟内。完成写 日志/建模_问题{编号}{标}.done")
        if 重算:
            建模任务 = f"【级联重算】上游结果已变更，问题{编号}必须按新上游数字重做。" + 建模任务
        wave([("建模师.md", 建模任务, f"建模_问题{编号}{标}")], timeout=1300)
        run_script(f"{qdir}/求解_问题{编号}.py", f"执行_问题{编号}{标}")
        结论 = ""
        for 轮 in (1, 2):
            r = wave([("解读师.md", f"核验并解读问题{编号}（运行日志 日志/执行_问题{编号}{标}.log，结果在 {qdir}/结果/）。五项正确性协议一票否决。"
                       f"通过产出 交接/结果解读_问题{编号}.md（含论文引用清单：数值+来源文件+键名）与 交接/结果声明_问题{编号}.json"
                       '（供红队独立复算用，schema：{"问题":N,"核心指标":{"指标名":数值},"口径说明":{"指标名":"一句话口径"}}，**只放头条数字**）；'
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

    def 红队复算(编号, 标=""):
        """红队独立复算头条数字 + 驱动仲裁（§4.4）。返回 对齐/不齐/缺失。"""
        qdir = f"求解/问题{编号}"
        wave([("红队.md", f"独立复算问题{编号}的头条数字。**只准读**：交接/题面契约.json、交接/数据档案.json、数据/ 原始数据、交接/结果声明_问题{编号}.json。"
               f"**严禁读**建模师的 {qdir}/求解_问题{编号}.py、交接/建模笔记_问题{编号}.md、交接/结果解读_问题{编号}.md。"
               f"自己独立实现 {qdir}/复算.py（只写不跑），结果落 {qdir}/红队结果/。完成写 日志/红队_问题{编号}{标}.done", f"红队_问题{编号}{标}")], timeout=1300)
        run_script(f"{qdir}/复算.py", f"跑红队_问题{编号}{标}", budget=900, repair_role="红队.md", repairs=1)
        wave([("红队.md", f"读 {qdir}/红队结果/ 与 交接/结果声明_问题{编号}.json，逐键比对，产出 交接/红队_问题{编号}.json（严格按角色规范 schema：复算方式/复算指标/口径说明/结论/分歧明细）。"
               f"结论只能是 对齐 或 不齐。完成写 日志/红队报告_问题{编号}{标}.done", f"红队报告_问题{编号}{标}")], timeout=1100)
        报告 = get_json(f"交接/红队_问题{编号}.json", f"红队{编号}", "红队.md", "严格按 红队 角色 schema")
        if not 报告:
            log(f"问题{编号} 红队报告缺失，记为缺失不阻塞")
            return "缺失"
        结论 = str(报告.get("结论", "")).strip()
        分歧 = [d for d in (报告.get("分歧明细") or [])
                if isinstance(d, dict) and abs(float(d.get("相对差", 0) or 0)) > 配置["红队容差"]]
        if 结论.startswith("对齐") and not 分歧:
            log(f"问题{编号} 红队结论=对齐")
            return "对齐"
        log(f"!! 问题{编号} 红队不齐（{len(分歧)} 处超容差）→ 派仲裁腿")
        wave([("解读师.md", f"【仲裁】问题{编号} 红队复算与建模结果不一致。你**可以**读双方代码（{qdir}/求解_问题{编号}.py 与 {qdir}/复算.py）、"
               f"双方结果（{qdir}/结果/ 与 {qdir}/红队结果/）、交接/红队_问题{编号}.json。逐条定责：是建模错、红队错、还是口径差异成立。"
               f"产出 交接/仲裁_问题{编号}.json：{{\"逐项\":[{{\"指标\",\"裁定\":\"建模错|红队错|口径差异\",\"理由\",\"应改方\":\"建模|红队|无\"}}],\"总裁定\":\"...\"}}。"
               f"若裁定建模错，同时在 交接/返工单_问题{编号}.md 写明必须修什么。完成写 日志/仲裁_问题{编号}{标}.done", f"仲裁_问题{编号}{标}")], timeout=1100)
        仲 = get_json(f"交接/仲裁_问题{编号}.json", f"仲裁{编号}") or {}
        应改建模 = [x for x in (仲.get("逐项") or []) if str(x.get("应改方", "")).startswith("建模")]
        checkpoint([f"交接/仲裁_问题{编号}.json", f"交接/红队_问题{编号}.json"], f"S2-仲裁问{编号}")
        if 应改建模 and budget_ok():
            log(f"仲裁裁定建模需返工（{len(应改建模)} 项）→ 重跑问{编号}子链尾部")
            wave([("建模师.md", f"【仲裁返工】问题{编号}：仲裁裁定建模侧有误，见 交接/仲裁_问题{编号}.json 与 交接/返工单_问题{编号}.md。"
                   f"逐条修 {qdir}/求解_问题{编号}.py 与建模笔记，追加实验记录。只改不跑。"
                   f"**改完必须回写仲裁台账**：对 交接/仲裁_问题{编号}.json 中每条 应改方=建模 的条目，"
                   f"把该条的 消解状态 改为 已消解（确已按裁定修正）或 已解释（经复核裁定不成立），"
                   f"并写 消解证据（改了哪个文件哪一处、新数值是多少、或为何裁定不成立）——"
                   f"证据必须具体可核，禁止空话；未真正处理的条目不许标已消解。"
                   f"完成写 日志/仲裁返工_问题{编号}{标}.done", f"仲裁返工_问题{编号}{标}")], timeout=1100)
            run_script(f"{qdir}/求解_问题{编号}.py", f"执行_问题{编号}{标}_仲裁后", repairs=1)
            wave([("解读师.md", f"问题{编号} 仲裁返工后重新核验解读，更新 交接/结果解读_问题{编号}.md 与 交接/结果声明_问题{编号}.json。"
                   f"完成写 日志/解读_问题{编号}{标}_仲裁后.done（首行PASS/FAIL）", f"解读_问题{编号}{标}_仲裁后")], timeout=1100)
            return "仲裁后修正"
        return "口径差异成立" if 仲 else "不齐"

    def G2门(编号):
        def _检查():
            return 门检(f"G2:{编号}")

        def _返工(明细):
            wave([("建模师.md", f"正确性门 G2（问题{编号}）未通过：{json.dumps(明细, ensure_ascii=False)[:1800]}。"
                   f"逐条修 求解/问题{编号}/求解_问题{编号}.py、交接/建模笔记_问题{编号}.md、交接/假设台账_问题{编号}.json（检验结果必须真实非空并引用结果键）。只改不跑。"
                   f"**若明细含“仲裁裁定建模需修正但未消解”，必须回写 交接/仲裁_问题{编号}.json**："
                   f"对每条 应改方=建模 的条目，把 消解状态 置为 已消解（确已修正）或 已解释（经复核裁定不成立），"
                   f"并写 消解证据（具体到文件/位置/新数值，或不成立的理由）；未真正处理的不许标已消解。"
                   f"完成写 日志/G2返工_问{编号}.done", f"G2返工_问{编号}")], timeout=1100)
            run_script(f"求解/问题{编号}/求解_问题{编号}.py", f"执行_问题{编号}_G2返工", repairs=1)
            wave([("解读师.md", f"问题{编号} G2 返工后重新核验解读，更新 交接/结果解读_问题{编号}.md 与 交接/结果声明_问题{编号}.json。完成写 日志/解读_问题{编号}_G2返工.done（首行PASS/FAIL）", f"解读_问题{编号}_G2返工")], timeout=1100)

        def _升格(明细):
            升格蜂群(f"G2问{编号}", "建模师.md",
                     f"【升格蜂群·变体{{变体号}}】问题{编号} 的 G2 正确性门连败两次（明细：{json.dumps(明细, ensure_ascii=False)[:900]}）。"
                     f"本变体切入：{{切入}}。重写 求解/问题{编号}/升格{{变体号}}/求解.py（只写不跑，结果落同目录 结果/，与主线同口径核心指标），"
                     f"并写 交接/升格笔记_问题{编号}_{{变体号}}.md 说明改了什么。",
                     f"【升格裁决】问题{编号}：主线与 3 条升格变体（求解/问题{编号}/升格*/结果/）按同口径核心指标+五项正确性协议裁决优胜，"
                     f"把优胜结果整理进 求解/问题{编号}/结果/（规范键名），更新 交接/结果解读_问题{编号}.md、交接/结果声明_问题{编号}.json、"
                     f"交接/假设台账_问题{编号}.json，并写 交接/升格裁决_问题{编号}.json。"
                     f"**同时结清仲裁台账**：对 交接/仲裁_问题{编号}.json 中每条 应改方=建模 的条目，"
                     f"依优胜方案的实际情况把 消解状态 写成 已消解 或 已解释，并附具体 消解证据。")
            for k in (1, 2, 3):
                run_script(f"求解/问题{编号}/升格{k}/求解.py", f"跑升格_问{编号}_{k}", budget=900, repairs=1)
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
        for 轮 in range(1, 配置["图评轮数"] + 1):
            d = h.exec("find 求解 -name '*.png' | sort", quiet=True)
            pngs = [l for l in (d.get("stdout") or "").split() if l.endswith(".png")]
            log(f"S3 图评轮{轮}：现有成图 {len(pngs)} 张")
            if not pngs:
                return
            名们 = []
            for bi in range(0, len(pngs), 7):
                批 = pngs[bi:bi + 7]
                名 = f"图评R{轮}_{bi//7+1}"
                清单 = "\n".join(批)
                名们.append(图片腿(名, "图评师.md",
                                  f"逐张审以下图（附图顺序对应）：\n{清单}\n"
                                  f"输出 JSON 数组写 审稿/{名}.json：[{{\"图\":路径,\"分数\":x,\"问题\":[...],\"修改指令\":[...]}}]。"
                                  f"各图的独有信息与结论一句话见 交接/计划.json 图表规划。完成写 日志/{名}.done", 批))
            等腿(名们, timeout=1100)
            低分 = []
            分们 = []
            for 名 in 名们:
                v = get_json(f"审稿/{名}.json", 名) or []
                if isinstance(v, dict):
                    v = v.get("图评", []) or []
                for it in v:
                    try:
                        s = float(it.get("分数", 10))
                        分们.append(s)
                        if s < 配置["图评阈值"]:
                            低分.append(it)
                    except Exception:
                        pass
            均 = sum(分们) / len(分们) if 分们 else 0
            log(f"S3 图评轮{轮}: 均分{均:.2f} 低分({配置['图评阈值']}以下){len(低分)}张")
            checkpoint([f"审稿/{n}.json" for n in 名们], f"S3-图评轮{轮}")
            if not 低分 or not budget_ok():
                log("S3 图评：全部达标或预算限制，退出循环")
                return
            wave([("绘图师.md", f"图修复（第{轮}轮）：以下图低于{配置['图评阈值']}分，按修改指令改对应绘图脚本（只改不跑），"
                   f"写 求解/成图修{轮}.sh 只重跑这些图：{json.dumps(低分, ensure_ascii=False)[:6000]}。完成写 日志/图修{轮}.done", f"图修{轮}")], timeout=1100)
            run_script(f"求解/成图修{轮}.sh", f"成图修{轮}", budget=700, repairs=1)
            checkpoint_dir("求解", f"S3-图修{轮}")
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

    def _S4脊柱():
        wave([("撰稿师.md", "【叙事设计】不写正文。读 交接/计划.json（叙事主线/偏离点/论文结构）、各问 交接/结果解读_问题X.md、"
               "交接/图注素材_*.json、交接/实验记录.json、交接/典型答卷预测.md，产出 交接/论点脊柱.json，schema："
               '{"章":[{"文件名":"3.xxx.tex","论点链":[{"发现":"带具体数字的一句话","证据":["图X","表Y","结果键Z"],"含义":"...","衔接下段":"..."}],'
               '"开场策略":"...","禁止":"模板化开场"}]}。每章 3-6 个论点，发现必须带真实数字。完成写 日志/脊柱.done', "脊柱")], timeout=1300)
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
                本脊 = json.dumps(脊柱表.get(f, {}), ensure_ascii=False)[:2500]
                legs.append(("撰稿师.md", f"撰写 论文/{f}（标题：{c.get('章节标题')}；配额：{c.get('页数配额','按需')}页；要点：{json.dumps(c.get('内容要点',''), ensure_ascii=False)[:400]}）。"
                             f"**本章论点脊柱（必须按它的发现→证据→含义链条组织，开场按其开场策略，禁模板化开场）**：{本脊}\n"
                             f"{特}相关问题的建模笔记/结果解读/假设台账/图注素材/实验记录自行从 交接/ 与 求解/ 取。"
                             f"刻意偏离 交接/典型答卷预测.md 的行文套路。图引用 \\includegraphics[width=0.8\\textwidth]{{../求解/问题X/图片/<图名>.png}}。"
                             f"完成写 日志/写_{f}.done", f"写_{f}"))
            wave(legs, timeout=1300)
        checkpoint([f"论文/{f}" for f in 章文件] + ["论文/论文.tex"], "S4-撰稿")
    节点("S4:撰稿", _S4撰稿, 阶段="S4")

    def _S4章评循环():
        普通章 = [c["文件名"].replace("论文/", "") for c in 结构 if "摘要" not in c.get("文件名", "")]
        for 轮 in range(1, 配置["章评轮数"] + 1):
            legs = []
            for bi in range(0, len(普通章), 4):
                批 = 普通章[bi:bi + 4]
                名 = f"章评R{轮}_{bi//4+1}"
                legs.append(("章评师.md", f"深审以下 {len(批)} 章：{批}。逐章按【AI味/推理跳步/数字溯源】三项打分（1-10）并给可执行改单。"
                             f"对照 交接/论点脊柱.json 核对论点链是否落地、交接/实验记录.json 核对过程感是否真实、审稿/审计报告.json（若存在）核对禁用词与溯源。"
                             f"输出 JSON 数组写 审稿/{名}.json：[{{\"章\":\"文件名\",\"分数\":x,\"AI味\":x,\"跳步\":x,\"溯源\":x,\"问题\":[...],\"修改指令\":[...]}}]。"
                             f"完成写 日志/{名}.done", 名))
            h.exec("python3 bin/审计.py > 日志/审计_章评.out 2>&1; echo ok", quiet=True)
            wave(legs, timeout=1300)
            低分 = []
            分们 = []
            for _, _, 名 in legs:
                v = get_json(f"审稿/{名}.json", 名) or []
                if isinstance(v, dict):
                    v = v.get("章评", []) or []
                for it in v:
                    try:
                        s = float(it.get("分数", 10))
                        分们.append(s)
                        if s < 配置["章评阈值"]:
                            低分.append(it)
                    except Exception:
                        pass
            均 = sum(分们) / len(分们) if 分们 else 0
            log(f"S4 章评轮{轮}: 均分{均:.2f} 低分({配置['章评阈值']}以下){len(低分)}章")
            checkpoint([f"审稿/{n}.json" for _, _, n in legs], f"S4-章评轮{轮}")
            if not 低分 or not budget_ok():
                log("S4 章评：全部达标或预算限制，退出循环")
                return
            重legs = []
            for i, it in enumerate(低分[:6], 1):
                ch = str(it.get("章", "")).replace("论文/", "")
                重legs.append(("撰稿师.md", f"章重写（第{轮}轮）：论文/{ch} 章评仅 {it.get('分数')} 分。逐条落实改单：{json.dumps(it, ensure_ascii=False)[:3000]}。"
                               f"保持 交接/论点脊柱.json 中本章论点链，重写不达标段落（不是小改错别字）。完成写 日志/章重写{轮}_{i}.done", f"章重写{轮}_{i}"))
            wave(重legs, timeout=1300)
            checkpoint([f"论文/{f}" for f in 章文件], f"S4-章重写{轮}")
    节点("S4:章评循环", _S4章评循环, 阶段="S4")

    def _S4摘要():
        n = 配置["摘要变体数"]
        摘底 = ("写摘要（\\begin{abstract}…\\end{abstract}，\\keywords，\\label{abstract:end}）。≤900字必1页。"
                "素材=各问结果解读的论文引用清单+定制改造名+锦标赛优胜依据。每问一段\\textbf{对于问题X：}+硬数字（带单位）。"
                "禁用词表适用；刻意偏离 交接/典型答卷预测.md 的摘要套路。同时把论文标题按内容定稿写入你输出文件首行注释 %标题：xxx。")
        侧重们 = ["侧重方法论完整（模型链条与创新命名）", "侧重结论冲击力（关键数字前置）",
                  "侧重评委3分钟可复述（四要素极清晰）", "侧重工程可信度（验证与稳健性证据）",
                  "侧重问题-方法匹配的说理链（为什么必须这样做）"]
        legs = []
        for k in range(1, n + 1):
            legs.append(("撰稿师.md", 摘底 + f"变体{k}{侧重们[(k-1) % len(侧重们)]}。写 审稿/摘要_变体{k}.tex。完成写 日志/摘{k}.done", f"摘{k}"))
        wave(legs, timeout=1100)
        摘要文件 = next((f for f in 章文件 if "摘要" in f), "0.摘要.tex")
        wave([("审稿员.md", f"【摘要面板评分与融合】比较 审稿/摘要_变体1..{n}.tex，按摘要四要素（具体问题｜模型+算法点名｜每问关键数值+单位｜结论与稳健性）"
               f"+语言军规逐个打分，合并各变体优点写终版到 论文/{摘要文件}，并把定稿论文标题替换进 论文/论文.tex 的 \\title{{}}。"
               f"评分与融合理由写 审稿/摘要评审.json。完成写 日志/摘评.done", "摘评")], timeout=900)
        checkpoint([f"论文/{摘要文件}", "论文/论文.tex", "审稿/摘要评审.json"], "S4-摘要")
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
        wave([("撰稿师.md", f"成稿门 G4 未通过：{json.dumps(明细, ensure_ascii=False)[:2000]}。逐条修 论文/*.tex："
               f"禁用词清零、每个统计数字补 `% src:文件:键` 且必须与 结果/ JSON 真值一致、"
               f"如图如表句式占比降到40%以下、摘要压到恰好1页、正文≤20页。完成写 日志/G4返工.done", "G4返工")], timeout=1300)
        checkpoint([f"论文/{f}" for f in 章文件], "G4-返工")
    节点("G4", lambda: 调度器.门("G4", _G4检查, 返工fn=_G4返工, 状态=状态, log=log).执行(), 阶段="G4")

    # ---------- S5 审稿场（五路并审 + 分级修订单 + 定向回炉） ----------
    def _渲染页():
        h.exec("mkdir -p 论文/页 && rm -f 论文/页/*.png && cd 论文 && "
               "gs -dNOPAUSE -dBATCH -sDEVICE=png16m -r70 -sOutputFile=页/p%02d.png 论文.pdf > /dev/null 2>&1; ls 页 | wc -l", quiet=True)
        d = h.exec("ls 论文/页/*.png | sort", quiet=True)
        return [l for l in (d.get("stdout") or "").split() if l.endswith(".png")]

    def _S5审稿场():
        prev = 0.0
        for 轮 in range(1, 配置["审稿轮数"] + 1):
            if not budget_ok():
                log("S5 预算耗尽，退出审稿循环")
                break
            运行态["阶段"] = f"S5:轮{轮}"
            状态.设阶段(f"S5:轮{轮}")
            E, O, P, out = compile_paper(f"#审{轮}前")
            页们 = _渲染页()
            h.exec("python3 bin/审计.py > 日志/审计.out 2>&1; echo ok", quiet=True)
            门检("G4")   # 机械审计+门检作为第⑤路（不占腿）
            上轮 = f"+ 上轮意见 审稿/审稿意见_轮{轮-1}A.json 与 …{轮-1}B.json 核对整改落实情况" if 轮 > 1 else ""
            # ①②④ 三路文本腿 + ③ 评委模拟（页图腿）
            legs = [
                ("审稿员.md", f"第{轮}轮全文审稿·评委A（论文/ 全部tex + 审稿/审计报告.json 特别核对其中溯源核验存疑清单 + 交接/ 材料{上轮}）。独立评审。"
                 f"按角色规范产出 审稿/审稿意见_轮{轮}A.json。**另外必须更新 交接/需求追踪矩阵.json 的落位字段**（逐条核对该需求是否已在论文中落位，"
                 f"填 章节/图表/关键数字，已落位改状态为 已落位）。完成写 日志/审{轮}A.done", f"审{轮}A"),
                ("审稿员.md", f"第{轮}轮全文审稿·评委B（同上输入，独立评审，不看评委A）。侧重挑硬伤：数字一致性、推导跳步、图文相符。"
                 f"产出 审稿/审稿意见_轮{轮}B.json。完成写 日志/审{轮}B.done", f"审{轮}B"),
                ("硬伤猎手.md", f"第{轮}轮硬伤猎杀。产出 审稿/硬伤_轮{轮}.json（严格按角色规范：[{{类别,定位,证据,级别}}]，**空数组=清零**）。完成写 日志/硬伤{轮}.done", f"硬伤{轮}"),
            ]
            评委名 = None
            if 页们:
                关键页 = 页们[:1] + 页们[1:2] + 页们[2:8]
                评委名 = 图片腿(f"评委模拟{轮}", "评委模拟.md",
                              f"模拟 5-10 分钟初评。附图为论文渲染页（第1张=摘要页，其后为正文首页起）。同时可读 论文/{next((f for f in 章文件 if '摘要' in f), '0.摘要.tex')}。"
                              f"输出 审稿/评委模拟_轮{轮}.json（按角色规范：第一印象分/能否复述四要素/三个卡住的地方/修订建议）。完成写 日志/评委模拟{轮}.done",
                              关键页[:8])
            wave(legs, timeout=1300)
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
            log(f"S5 轮{轮} 面板均分={总分:.2f}（A={意A.get('总分')} B={意B.get('总分')}）硬伤={len(硬伤)}条 评委第一印象={评委.get('第一印象分') or 评委.get('印象分')}")
            checkpoint([f"审稿/审稿意见_轮{轮}A.json", f"审稿/审稿意见_轮{轮}B.json", f"审稿/硬伤_轮{轮}.json",
                        f"审稿/评委模拟_轮{轮}.json", "审稿/审计报告.json", "交接/需求追踪矩阵.json"], f"S5-轮{轮}")
            # ---------- 收敛判据：均分达标或平台期，且硬伤清零 ----------
            达标 = (总分 >= 配置["审稿达标"] or (轮 > 1 and abs(总分 - prev) < 配置["审稿平台期"]))
            if 达标 and not 硬伤:
                log(f"S5 收敛：均分{总分:.2f} 硬伤清零 → 退出审稿循环")
                break
            if 轮 >= 配置["审稿轮数"]:
                log(f"S5 轮数上限：均分{总分:.2f} 硬伤{len(硬伤)}条 → 退出（剩余问题记入运行日志）")
                break
            prev = 总分
            # ---------- 合并五路 → 分级修订单 ----------
            条目 = []
            for 源, 意 in (("审稿员A", 意A), ("审稿员B", 意B)):
                for it in (意.get("最高优先级修改", []) or [])[:6]:
                    if isinstance(it, str):
                        it = {"问题": it}
                    条目.append({"级别": it.get("级别", "正确性" if 源 == "审稿员B" else "叙述"),
                                 "目标": it.get("目标", "文"), "问题": it.get("问题") or it.get("修改指令") or str(it)[:200],
                                 "定位": it.get("定位", it.get("位置", "")), "指令": it.get("指令") or it.get("修改指令") or str(it)[:300],
                                 "验收": it.get("验收", ""), "来源": 源})
            for it in 硬伤:
                if isinstance(it, dict):
                    条目.append({"级别": "硬伤", "目标": it.get("目标", "文"), "问题": it.get("证据") or it.get("类别", ""),
                                 "定位": it.get("定位", ""), "指令": it.get("修改指令") or it.get("建议") or f"清除该硬伤：{it.get('类别')}",
                                 "验收": "该硬伤复查为空", "来源": "硬伤猎手"})
            for it in (评委.get("修订建议", []) or 评委.get("卡住的地方", []) or []):
                条目.append({"级别": "叙述", "目标": "文", "问题": str(it)[:300], "定位": "", "指令": str(it)[:300],
                             "验收": "评委模拟不再指出", "来源": "评委模拟"})
            for it in ((审计.get("溯源核验", {}) or {}).get("存疑明细", []) or [])[:8]:
                条目.append({"级别": "正确性", "目标": "文", "问题": f"溯源存疑：{json.dumps(it, ensure_ascii=False)[:200]}",
                             "定位": f"{it.get('文件')}:{it.get('行')}", "指令": "核对该数字与来源JSON真值，改正文数字或修正 % src 指向",
                             "验收": "审计溯源存疑数减少", "来源": "机械审计"})
            for it in (审计.get("禁用词", {}) or {}).get("明细", [])[:8]:
                条目.append({"级别": "叙述", "目标": "文", "问题": f"禁用词：{it.get('词')}", "定位": f"{it.get('文件')}:{it.get('行')}",
                             "指令": f"改写该句，去掉“{it.get('词')}”且不留同义套话", "验收": "审计禁用词=0", "来源": "机械审计"})
            矩阵 = get_json("交接/需求追踪矩阵.json", "需求矩阵") or []
            for it in [x for x in 矩阵 if isinstance(x, dict) and x.get("状态") != "已落位"][:8]:
                条目.append({"级别": "正确性", "目标": "文", "问题": f"需求未销号：{it.get('需求号')} {str(it.get('内容'))[:120]}",
                             "定位": "", "指令": f"在正文补上该需求的落位（内容+数字+图表），并更新 交接/需求追踪矩阵.json 该条落位字段",
                             "验收": "需求矩阵该条状态=已落位", "来源": "需求矩阵"})
            修订单 = 调度器.合并修订单(条目)
            h.exec("echo 修订单上传", files=[h.f_text(f"审稿/修订单_轮{轮}.json", json.dumps(修订单, ensure_ascii=False, indent=1))], quiet=True)
            组 = 调度器.按目标分组(修订单)
            log(f"S5 轮{轮} 修订单 {len(修订单)} 条 → 算{len(组['算'])}/图{len(组['图'])}/文{len(组['文'])}（先算后图后文）")
            checkpoint([f"审稿/修订单_轮{轮}.json"], f"S5-修订单{轮}")
            # ---------- 定向回炉：算 → 图 → 文 ----------
            if 组["算"] and budget_ok():
                受影响问 = set()
                for it in 组["算"]:
                    for bh in 问表:
                        if f"问题{bh}" in json.dumps(it, ensure_ascii=False):
                            受影响问.add(bh)
                受影响问 = sorted(受影响问) or sorted(问表)[:1]
                log(f"S5 轮{轮} 回炉S2：重算问{受影响问}")
                for bh in 受影响问:
                    wave([("建模师.md", f"【S5回炉·重算】问题{bh}：审稿场判定需要重新计算。修订条目：{json.dumps([x for x in 组['算'] if f'问题{bh}' in json.dumps(x, ensure_ascii=False)] or 组['算'], ensure_ascii=False)[:2500]}。"
                           f"改 求解/问题{bh}/求解_问题{bh}.py 与建模笔记与假设台账，只改不跑。完成写 日志/回炉算_轮{轮}_问{bh}.done", f"回炉算_轮{轮}_问{bh}")], timeout=1300)
                    run_script(f"求解/问题{bh}/求解_问题{bh}.py", f"执行_回炉_轮{轮}_问{bh}", repairs=1)
                    wave([("解读师.md", f"问题{bh} 回炉重算后重新核验解读（五项正确性协议），更新 交接/结果解读_问题{bh}.md 与 交接/结果声明_问题{bh}.json。"
                           f"完成写 日志/解读_回炉_轮{轮}_问{bh}.done（首行PASS/FAIL）", f"解读_回炉_轮{轮}_问{bh}")], timeout=1100)
                    if 配置["开红队"] and budget_ok():
                        红队复算(bh, f"_回炉{轮}")
                    G2门(bh)
                    下游 = 运行态["级联重算"](bh)
                    if 下游:
                        log(f"S5 轮{轮} 级联：问{下游} 已重算，受影响章将进下一轮修订")
                        条目.append({"级别": "正确性", "目标": "文", "问题": f"级联重算问{下游}后，相关章节数字需同步",
                                     "定位": "", "指令": "按新结果更新相关章正文数字与 % src 标注", "验收": "溯源核验通过", "来源": "级联"})
            if 组["图"] and budget_ok():
                wave([("绘图师.md", f"【S5回炉·改图】按修订单图类条目改绘图脚本（只改不跑），写 求解/成图回炉{轮}.sh 只重跑受影响图："
                       f"{json.dumps(组['图'], ensure_ascii=False)[:5000]}。完成写 日志/回炉图_轮{轮}.done", f"回炉图_轮{轮}")], timeout=1300)
                run_script(f"求解/成图回炉{轮}.sh", f"成图回炉{轮}", budget=800, repairs=1)
                checkpoint_dir("求解", f"S5-回炉图{轮}")
            if 组["文"] and budget_ok():
                批 = 组["文"]
                for bi in range(0, min(len(批), 24), 12):
                    片 = 批[bi:bi + 12]
                    wave([("撰稿师.md", f"【S5回炉·改文】第{轮}轮修订单（文类，级别已排序：硬伤>正确性>叙述>版式）逐条落实："
                           f"{json.dumps(片, ensure_ascii=False)[:6000]}。涉及数字的必须与 求解/*/结果/ JSON 真值一致并带 `% src:文件:键`；"
                           f"涉及需求销号的同时更新 交接/需求追踪矩阵.json。完成写 日志/回炉文_轮{轮}_{bi//12+1}.done", f"回炉文_轮{轮}_{bi//12+1}")], timeout=1300)
                _编译修复循环(f"修{轮}", 3)
            checkpoint([f"论文/{f}" for f in 章文件], f"S5-轮{轮}修订后")
    节点("S5:审稿场", _S5审稿场, 阶段="S5")

    # ---------- S5b 美化循环（收敛后置） ----------
    def _S5美化():
        for 美轮 in range(1, 配置["美化轮数"] + 1):
            E, O, P, out = compile_paper(f"#美{美轮}")
            页们 = _渲染页()
            if not 页们:
                log("美化：无渲染页，跳过")
                return
            名们 = []
            for bi in range(0, len(页们), 8):
                批 = 页们[bi:bi + 8]
                名们.append(图片腿(f"美{美轮}_{bi//8+1}", "美化师.md",
                                  f"审附图各页（对应 {批[0]} 起连续{len(批)}页），输出写 审稿/美{美轮}_{bi//8+1}.json。完成写 日志/美{美轮}_{bi//8+1}.done",
                                  批, timeout=800, reasoning=False))
            等腿(名们, timeout=1000)
            页问题集, 分数们 = [], []
            for 名 in 名们:
                v = get_json(f"审稿/{名}.json", 名) or {}
                页问题集 += v.get("页问题", [])
                try:
                    分数们.append(float(v.get("美观分", 0)))
                except Exception:
                    pass
            美观 = sum(分数们) / len(分数们) if 分数们 else 0
            log(f"S5b 美化轮{美轮}: 页问题{len(页问题集)}条 美观分均值{美观:.1f}")
            checkpoint([f"审稿/{n}.json" for n in 名们], f"S5b-美化{美轮}")
            if not 页问题集 or 美观 >= 配置["美观阈值"] or not budget_ok():
                return
            wave([("撰稿师.md", f"排版执行：按美化师页问题清单逐条改tex（浮动体参数/图宽/表列宽/位置微调，不改文字内容）："
                   f"{json.dumps(页问题集, ensure_ascii=False)[:5000]}。完成写 日志/排版执行{美轮}.done", f"排版执行{美轮}")], timeout=1100)
            _编译修复循环(f"美修{美轮}", 2)
    节点("S5:美化", _S5美化, 阶段="S5")

    # ---------- G5 出版门 ----------
    def _G5检查():
        _编译修复循环("终编", 2)
        h.exec("python3 bin/审计.py > 日志/终审计.out 2>&1; echo ok", quiet=True)
        return 门检("G5")

    def _G5返工(明细):
        wave([("撰稿师.md", f"出版门 G5 未通过：{json.dumps(明细, ensure_ascii=False)[:2000]}。逐条整改 论文/*.tex："
               f"需求矩阵必须全销号（同时更新 交接/需求追踪矩阵.json）、硬伤清零、正文≤20页、无 \\tableofcontents、"
               f"附录 lstlisting 必须含各问核心源码、全部 \\cite 键必须存在于 运行时/文献卡片库.md。完成写 日志/G5返工.done", "G5返工")], timeout=1300)
        checkpoint([f"论文/{f}" for f in 章文件] + ["交接/需求追踪矩阵.json"], "G5-返工")
    节点("G5", lambda: 调度器.门("G5", _G5检查, 返工fn=_G5返工, 状态=状态, log=log).执行(), 阶段="G5")

    # ---------- S6 出版 + 复盘 ----------
    def _S6终审():
        E, O, P, out = compile_paper("#终")
        页们 = _渲染页()
        log(f"S6 终审：E={E} 页数={P} 渲染{len(页们)}页")
        if 页们 and budget_ok():
            名们 = []
            for bi in range(0, len(页们), 8):
                批 = 页们[bi:bi + 8]
                名们.append(图片腿(f"终审_{bi//8+1}", "美化师.md",
                                  f"【逐页终审·压轴】审附图各页（{批[0]} 起连续{len(批)}页）。这是交付前最后一道视觉关，只报**必须改**的问题。"
                                  f"输出写 审稿/终审_{bi//8+1}.json（同角色规范 schema）。完成写 日志/终审_{bi//8+1}.done", 批, timeout=800, reasoning=False))
            等腿(名们, timeout=1000)
            必改 = []
            for 名 in 名们:
                v = get_json(f"审稿/{名}.json", 名) or {}
                必改 += v.get("页问题", [])
            log(f"S6 逐页终审：{len(必改)} 条必改")
            checkpoint([f"审稿/终审_{i+1}.json" for i in range(len(名们))], "S6-终审")
            if 必改 and budget_ok():
                wave([("撰稿师.md", f"终审整改（只改版式不改文字）：{json.dumps(必改, ensure_ascii=False)[:5000]}。完成写 日志/终审整改.done", "终审整改")], timeout=1100)
                _编译修复循环("终整改", 2)
    节点("S6:终审", _S6终审, 阶段="S6")

    def _S6出版():
        E, O, P, out = compile_paper("#出版")
        h.exec("python3 bin/审计.py > 日志/终审计.out 2>&1; echo ok", quiet=True)
        for 门名 in ("G0", "G2", "G3", "G4", "G5"):
            h.exec(f"python3 bin/门检.py {门名} > /dev/null 2>&1; echo ok", quiet=True, timeout_s=200)
        终 = ["论文/论文.pdf", "审稿/审计报告.json", "交接/实验记录.json", "交接/求解计划.md"]
        d = h.exec("ls 论文/*.tex 论文/页/*.png 审稿/*.json 交接/*.json 交接/*.md 2>/dev/null; "
                   "find 求解 -name '*.py' -o -name '*.json' -o -name '*.png' -o -name '*.sh' 2>/dev/null", quiet=True)
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
        log(f"===== 续跑（--resume）：运行ID={状态.数据['运行ID']} 已完成节点={len(状态.数据['已完成节点'])} 腿数={LEG_COUNT} =====")
        ensure_hive()
    else:
        log(f"===== 全新运行：运行ID={状态.数据['运行ID']} 档位={配置['档位']} =====")
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
