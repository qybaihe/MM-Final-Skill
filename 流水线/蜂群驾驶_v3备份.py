#!/usr/bin/env python3
"""蜂群驾驶：全自主跑完一道数模题（读题→规划→蜂群求解→绘图→撰稿→审稿收敛→美化→出版）。
用法: python3 蜂群驾驶.py <输入目录(含题目PDF与附件)> <产出目录>
设计原则：所有角色腿统一 nohup 异步 + done 标记轮询；每腿失败重试一次；关键 JSON 校验失败带反馈重写一次；
执行/解读/图评/审稿/美化各有修复循环；全程规则驱动零人工；每阶段 checkpoint 收割；任何异常记录后尽量继续。
"""
import glob
import json
import pathlib
import sys
import time
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from hive_sdk import Hive

输入目录 = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/Users/bytedance/数模自动化/真题测试/输入")
产出目录 = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "/Users/bytedance/数模自动化/真题测试/成品")
镜像目录 = 产出目录.parent / "蜂巢镜像"
流水线 = pathlib.Path(__file__).parent
日志文件 = 产出目录.parent / "运行日志.md"
产出目录.mkdir(parents=True, exist_ok=True)

h = Hive(account="a1")
T_START = time.time()
LEG_COUNT = 0
MAX_LEGS = 140
MAX_HOURS = 6
状态 = {"阶段": "", "事件": []}


def log(msg):
    line = f"- {time.strftime('%H:%M:%S')} [{int(time.time()-T_START)}s] {msg}"
    print(line, flush=True)
    with open(日志文件, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    状态["事件"].append(msg)


def budget_ok():
    return LEG_COUNT < MAX_LEGS and (time.time() - T_START) < MAX_HOURS * 3600


def wave(legs, timeout=1300, poll=30, retry=True):
    """异步跑一组腿到 done。legs=[(role,task,name)]。返回 {name: done首行}。失败重试一次。"""
    global LEG_COUNT
    pending = list(legs)
    results = {}
    for attempt in (1, 2):
        if not pending:
            break
        ensure_hive()
        for role, task, name in pending:
            LEG_COUNT += 1
            h.exec(f"rm -f 日志/{name}.done", quiet=True)
            h.leg(role, task, name, sync=False)
        log(f"波次启动 {len(pending)} 腿(第{attempt}次): {[n for _,_,n in pending]}")
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(poll)
            marks = " ".join(f"日志/{n}.done" for _, _, n in pending)
            d = h.exec(f"c=0; for f in {marks}; do [ -f $f ] && c=$((c+1)); done; echo $c", quiet=True)
            try:
                cnt = int((d.get("stdout") or "0").strip().split()[-1])
            except Exception:
                cnt = 0
            if cnt >= len(pending):
                break
        nxt = []
        for role, task, name in pending:
            d = h.exec(f"head -1 日志/{name}.done 2>/dev/null", quiet=True)
            first = (d.get("stdout") or "").strip()
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
    """拉取并解析 JSON；失败时派修复腿一次。"""
    for attempt in (1, 2):
        got = h.harvest([path], str(镜像目录))
        try:
            return json.load(open(镜像目录 / path, encoding="utf-8"))
        except Exception as e:
            log(f"{desc} 解析失败({e})，尝试{attempt}")
            if attempt == 1 and fix_role and budget_ok():
                wave([(fix_role, f"文件 {path} 不是合法JSON或缺失（错误：{e}）。{fix_hint}。请重新生成合法版本覆盖原文件。完成写 日志/修json.done", "修json")], timeout=700)
    return None


def run_script(script_path, log_name, budget=1500, repair_role="建模师.md", repairs=2):
    """跑求解/绘图脚本：nohup+轮询；失败派修复腿再跑。返回 rc。"""
    global LEG_COUNT
    for attempt in range(repairs + 1):
        ensure_hive()
        h.exec(f"rm -f 日志/{log_name}.done; nohup bash -c 'python3 {script_path} > 日志/{log_name}.log 2>&1; echo rc=$? > 日志/{log_name}.done' >/dev/null 2>&1 & echo started", quiet=True)
        t0 = time.time()
        rc = None
        while time.time() - t0 < budget:
            time.sleep(25)
            d = h.exec(f"cat 日志/{log_name}.done 2>/dev/null", quiet=True)
            out = (d.get("stdout") or "").strip()
            if out.startswith("rc="):
                rc = int(out[3:].split()[0])
                break
        log(f"脚本 {script_path} 第{attempt+1}次 rc={rc}")
        if rc == 0:
            return 0
        if attempt < repairs and budget_ok():
            LEG_COUNT += 1
            wave([(repair_role, f"脚本修复：{script_path} 运行失败（日志尾部见 日志/{log_name}.log，用 tail -40 查看）。只修此脚本让它跑通，不改变方法本质。只改不跑。完成写 日志/修脚本_{log_name}.done", f"修脚本_{log_name}")], timeout=700)
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


SCRATCH = pathlib.Path("/private/tmp/claude-501/-Users-bytedance------/4738b35f-4d44-4e78-b113-7ddc6e7a6419/scratchpad")


def ensure_hive():
    """容器轮换检测：蜂巢丢失则从本地镜像自动重播种（韧性核心）。"""
    d = h.exec("[ -f AGENTS.md ] && echo alive", quiet=True)
    if "alive" in (d.get("stdout") or ""):
        return True
    log("!! 检测到容器轮换，蜂巢丢失——自动重播种（基建+输入+镜像状态）")
    files = [h.f_local("AGENTS.md", 流水线 / "AGENTS.md"),
             h.f_local("bin/role.sh", 流水线 / "运行时/role.sh"),
             h.f_local("bin/审计.py", 流水线 / "运行时/审计.py"),
             h.f_local("论文/format.cls", pathlib.Path("/Users/bytedance/数模自动化/沙箱验证/format-sandbox.cls"))]
    for p in (流水线 / "角色").glob("*.md"):
        files.append(h.f_local(f"角色/{p.name}", p))
    for n in ["禁用词.txt", "优秀论文标准.md", "文献卡片库.md", "方法卡片库.md"]:
        fp = 流水线 / "运行时" / n
        if fp.exists():
            files.append(h.f_local(f"运行时/{n}", fp))
    for w in (SCRATCH / "wheels").glob("*.whl"):
        files.append(h.f_local(f"运行时/wheels/{w.name}", w))
    for t in (SCRATCH / "Mrite/模板").glob("*.tex"):
        files.append(h.f_local(f"运行时/模板参考/{t.name}", t))
    for p in sorted(输入目录.iterdir()):
        if p.suffix.lower() == ".pdf":
            files.append(h.f_local(f"题目/{p.name}", p))
        elif p.suffix.lower() in (".xlsx", ".csv", ".docx"):
            files.append(h.f_local(f"数据/{p.name}", p))
    if 镜像目录.exists():
        for p in 镜像目录.rglob("*"):
            if p.is_file() and p.stat().st_size < 8_000_000:
                files.append(h.f_local(str(p.relative_to(镜像目录)), p))
    h.exec("mkdir -p 日志 任务 交接 求解 论文 审稿 pylibs && chmod +x bin/role.sh && "
           "pip3 install --quiet --no-index --no-deps --no-compile --target=/tmp/蜂巢/pylibs 运行时/wheels/*.whl; echo 重播种完成",
           files=files, timeout_s=560, quiet=True)
    log("重播种完成")
    return False


# ============================================================ 主流程
try:
    # ---------- P0 播种 ----------
    状态["阶段"] = "P0"
    log("P0 播种开始（清空旧蜂巢，全量重建）")
    files = [h.f_local("AGENTS.md", 流水线 / "AGENTS.md"),
             h.f_local("bin/role.sh", 流水线 / "运行时/role.sh"),
             h.f_local("bin/审计.py", 流水线 / "运行时/审计.py"),
             h.f_local("bin/循环.py", 流水线 / "运行时/循环.py")]
    for p in (流水线 / "角色").glob("*.md"):
        files.append(h.f_local(f"角色/{p.name}", p))
    for n in ["禁用词.txt", "优秀论文标准.md", "文献卡片库.md", "方法卡片库.md"]:
        files.append(h.f_local(f"运行时/{n}", 流水线 / "运行时" / n))
    files.append(h.f_local("运行时/HMML.md", pathlib.Path("/private/tmp/claude-501/-Users-bytedance------/4738b35f-4d44-4e78-b113-7ddc6e7a6419/scratchpad/mm_src/HMML.md")))
    files.append(h.f_local("论文/format.cls", pathlib.Path("/Users/bytedance/数模自动化/沙箱验证/format-sandbox.cls")))
    for t in pathlib.Path("/private/tmp/claude-501/-Users-bytedance------/4738b35f-4d44-4e78-b113-7ddc6e7a6419/scratchpad/Mrite/模板").glob("*.tex"):
        files.append(h.f_local(f"运行时/模板参考/{t.name}", t))
    for w in pathlib.Path("/private/tmp/claude-501/-Users-bytedance------/4738b35f-4d44-4e78-b113-7ddc6e7a6419/scratchpad/wheels").glob("*.whl"):
        files.append(h.f_local(f"运行时/wheels/{w.name}", w))
    for p in sorted(输入目录.iterdir()):
        if p.suffix.lower() == ".pdf":
            files.append(h.f_local(f"题目/{p.name}", p))
        elif p.suffix.lower() in (".xlsx", ".csv", ".docx"):
            files.append(h.f_local(f"数据/{p.name}", p))
    d = h.exec("cd /tmp && rm -rf 蜂巢.old && mv 蜂巢 蜂巢.old 2>/dev/null; echo cleared", quiet=True)
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
    log(f"P0: {(d.get('stdout') or '')[:150]}")

    # ---------- P1 读题 + 体检 ----------
    状态["阶段"] = "P1"
    r = wave([
        ("读题官.md", "把 交接/题目原文.txt（必要时用 pypdf 重抽 题目/ 下PDF）与 数据/ 目录附件清单转成 交接/题面.json。按角色规范。完成写 日志/读题.done", "读题"),
        ("数据体检师.md", "对 数据/ 目录下全部附件做全量体检（多文件：逐文件+跨文件关系）。产出 交接/数据档案.json 与 日志/运行日志.md。注意编码、隐藏sheet、合并单元格、多级表头、单位、跨附件主键关联。完成写 日志/体检.done", "体检"),
    ], timeout=1300)
    题面 = get_json("交接/题面.json", "题面", "读题官.md", "严格按读题官角色规范的schema")
    档案 = get_json("交接/数据档案.json", "数据档案", "数据体检师.md", "严格按数据体检师角色规范的schema")
    问题数 = len((题面 or {}).get("问题", [])) or 4
    log(f"P1 完成：问题数={问题数}")
    checkpoint(["交接/题面.json", "交接/数据档案.json"], "P1")

    # ---------- P2 规划 ----------
    状态["阶段"] = "P2"
    wave([("规划师.md", "为本题制定完整求解与写作计划（题面/数据档案/HMML/优秀论文标准已就位）。注意题目的硬约束清单必须逐条进入计划；对方法不确定或分值最高的问题启用蜂群变体。按角色规范产出 交接/求解计划.md 与 交接/计划.json。完成写 日志/规划.done", "规划")], timeout=1300)
    计划 = get_json("交接/计划.json", "计划", "规划师.md", "必含 问题清单/论文结构/图表规划/页数预算 四键")
    if not 计划:
        raise RuntimeError("计划.json 两次都拿不到，终止")
    问题清单 = 计划.get("问题清单", [])
    checkpoint(["交接/计划.json", "交接/求解计划.md"], "P2")

    # ---------- P3 逐问求解（蜂群变体 + 修复循环 + 解读否决循环） ----------
    状态["阶段"] = "P3"
    for q in 问题清单:
        编号 = q.get("编号")
        变体 = q.get("蜂群变体") or []
        qdir = f"求解/问题{编号}"
        if len(变体) >= 2 and budget_ok():
            legs = []
            for k, v in enumerate(变体[:3], 1):
                legs.append(("建模师.md", f"你负责问题{编号}的蜂群变体{k}：技术路线={json.dumps(v, ensure_ascii=False)}。按 计划.json 问题{编号} 的验证方案执行，但方法采用本变体路线。产物：交接/建模笔记_问题{编号}_变体{k}.md、{qdir}/变体{k}/求解.py（只写不跑，结果落 {qdir}/变体{k}/结果/，必须输出与其他变体可比的同口径核心指标 到 {qdir}/变体{k}/结果/核心指标.json）、实验记录追加。完成写 日志/建模_问题{编号}_变体{k}.done", f"建模_问题{编号}_变体{k}"))
            wave(legs, timeout=1300)
            for k in range(1, len(变体[:3]) + 1):
                run_script(f"{qdir}/变体{k}/求解.py", f"执行_问题{编号}_变体{k}", repairs=1)
            wave([("解读师.md", f"蜂群裁决：问题{编号}有{len(变体[:3])}个变体已运行（{qdir}/变体*/结果/）。按同口径核心指标+五项正确性协议裁决优胜者，把优胜变体的结果复制/整理到 {qdir}/结果/（规范键名），写 交接/裁决_问题{编号}.json（各变体得分与理由）与 交接/建模笔记_问题{编号}.md（=优胜变体笔记+裁决说明）与 交接/结果解读_问题{编号}.md（含论文引用清单）。实验记录追加裁决过程。完成写 日志/裁决_问题{编号}.done", f"裁决_问题{编号}")], timeout=1300)
        else:
            wave([("建模师.md", f"你负责问题{编号}，按 交接/计划.json 问题{编号} 的主方法/定制改造/验证方案执行。上游依赖见计划的依赖问题字段（其结果在对应 求解/问题X/结果/）。产物：交接/建模笔记_问题{编号}.md、{qdir}/求解_问题{编号}.py（只写不跑，结果落 {qdir}/结果/，键名中文含含义)、实验记录追加。运行时间控制在10分钟内。完成写 日志/建模_问题{编号}.done", f"建模_问题{编号}")], timeout=1300)
            rc = run_script(f"{qdir}/求解_问题{编号}.py", f"执行_问题{编号}")
            for 轮 in (1, 2):
                r = wave([("解读师.md", f"核验并解读问题{编号}（运行日志 日志/执行_问题{编号}.log，结果在 {qdir}/结果/）。五项正确性协议一票否决。通过产出 交接/结果解读_问题{编号}.md（含论文引用清单）；不过写 交接/返工单_问题{编号}.md。完成写 日志/解读_问题{编号}_轮{轮}.done（首行PASS/FAIL）", f"解读_问题{编号}_轮{轮}")], timeout=1100)
                verdict = r.get(f"解读_问题{编号}_轮{轮}", "")
                log(f"问题{编号} 解读轮{轮}: {verdict[:40]}")
                if verdict.startswith("PASS") or not budget_ok():
                    break
                wave([("建模师.md", f"修复任务：问题{编号}被打回，返工单 交接/返工单_问题{编号}.md，逐条落实，直接改 {qdir}/求解_问题{编号}.py 与建模笔记，返工过程追加实验记录。只改不跑。完成写 日志/修复_问题{编号}.done", f"修复_问题{编号}")], timeout=1100)
                run_script(f"{qdir}/求解_问题{编号}.py", f"执行_问题{编号}_修{轮}", repairs=1)
        checkpoint([f"交接/建模笔记_问题{编号}.md", f"交接/结果解读_问题{编号}.md", "交接/实验记录.json"], f"P3-问题{编号}")
        checkpoint_dir(f"{qdir}/结果", f"P3-问题{编号}结果")
        checkpoint_dir(qdir, f"P3-问题{编号}脚本")

    # ---------- P3.5 绘图（分波 + 图评修复循环） ----------
    状态["阶段"] = "P3.5"
    图规划 = 计划.get("图表规划", [])
    波数 = 3 if len(图规划) > 10 else 2
    每波 = (len(图规划) + 波数 - 1) // 波数
    legs = []
    for i in range(波数):
        名单 = 图规划[i * 每波:(i + 1) * 每波]
        if not 名单:
            continue
        legs.append(("绘图师.md", f"本次负责这{len(名单)}张图：{json.dumps(名单, ensure_ascii=False)}。脚本命名 求解/问题X/绘图_<图名>.py（公共图放 求解/公共/），写 求解/成图{i+1}.sh 依次执行本批。图注条目写 交接/图注素材_{i+1}.json。完成写 日志/绘图{i+1}.done", f"绘图{i+1}"))
    wave(legs, timeout=1300)
    for i in range(1, 波数 + 1):
        h.exec(f"[ -f 求解/成图{i}.sh ] && echo ok", quiet=True)
        run_script(f"求解/成图{i}.sh", f"成图{i}", budget=900, repairs=2)
    # 图评（批≤7，附图）
    d = h.exec("find 求解 -name '*.png' | sort", quiet=True)
    pngs = [l for l in (d.get("stdout") or "").split() if l.endswith(".png")]
    log(f"成图 {len(pngs)} 张")
    评腿 = []
    for bi in range(0, len(pngs), 7):
        批 = pngs[bi:bi + 7]
        imgs = " ".join(f'-i "{p}"' for p in 批)
        清单 = "\\n".join(批)
        名 = f"图评{bi//7+1}"
        script = f'''cat 角色/图评师.md > 任务/{名}.txt
printf '\\n=== 本次任务 ===\\n逐张审以下图（附图顺序对应）：\\n{清单}\\n输出JSON数组写 审稿/{名}.json：[{{"图":路径,"分数":x,"问题":[...],"修改指令":[...]}}]。计划中各图的独有信息与结论一句话见 交接/计划.json。完成写 日志/{名}.done\\n' >> 任务/{名}.txt
nohup bash -c 'timeout 900 codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -c model_provider="gw" -c "model_providers.gw.name=\\"gw\\"" -c "model_providers.gw.base_url=\\"https://proxy:18080/v1\\"" -c "model_providers.gw.env_key=\\"OPENAI_API_KEY\\"" -c "model_providers.gw.wire_api=\\"responses\\"" -c "model_reasoning_effort=\\"high\\"" -m gpt-5.6-sol {imgs} -- "$(cat 任务/{名}.txt)" > 日志/{名}.log 2>&1; [ -f 日志/{名}.done ] || echo AUTO > 日志/{名}.done' >/dev/null 2>&1 &
echo {名}启动'''
        h.exec(script, timeout_s=90, quiet=True)
        评腿.append(名)
    t0 = time.time()
    while time.time() - t0 < 1100 and 评腿:
        time.sleep(30)
        marks = " ".join(f"日志/{n}.done" for n in 评腿)
        d = h.exec(f"c=0; for f in {marks}; do [ -f $f ] && c=$((c+1)); done; echo $c", quiet=True)
        try:
            if int((d.get("stdout") or "0").strip().split()[-1]) >= len(评腿):
                break
        except Exception:
            pass
    低分 = []
    for 名 in 评腿:
        v = get_json(f"审稿/{名}.json", 名) or []
        for it in v:
            try:
                if float(it.get("分数", 10)) < 7:
                    低分.append(it)
            except Exception:
                pass
    log(f"图评低分 {len(低分)} 张")
    if 低分 and budget_ok():
        wave([("绘图师.md", f"图修复：以下图<7分，按修改指令改对应绘图脚本（只改不跑），写 求解/成图修.sh 只重跑这些：{json.dumps(低分, ensure_ascii=False)[:6000]}。完成写 日志/图修.done", "图修")], timeout=1100)
        run_script("求解/成图修.sh", "成图修", budget=700, repairs=1)
    checkpoint(["交接/图注素材_1.json", "交接/图注素材_2.json", "交接/图注素材_3.json"], "P3.5")
    checkpoint_dir("求解", "P3.5全量含图")

    # ---------- P4 撰稿（主控生成 + 分波 + 摘要蜂群） ----------
    状态["阶段"] = "P4"
    结构 = 计划.get("论文结构", [])
    章文件 = [c.get("文件名", "").replace("论文/", "") for c in 结构 if c.get("文件名")]
    if not any("摘要" in f for f in 章文件):
        章文件.insert(0, "0.摘要.tex")
    if not any("附录" in f for f in 章文件):
        章文件.append("99.附录.tex")
        结构.append({"文件名": "99.附录.tex", "章节标题": "附录：核心源代码与产物清单", "内容要点": "国赛要求的可运行核心源代码", "页数配额": "不限"})
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
    普通章 = [c for c in 结构 if "摘要" not in c.get("文件名", "")]
    for bi in range(0, len(普通章), 4):
        legs = []
        for c in 普通章[bi:bi + 4]:
            f = c["文件名"].replace("论文/", "")
            特 = "本章是附录：必须嵌入各问核心可运行源代码（lstlisting 环境，从 求解/*/求解*.py 摘核心函数段并注明文件）+ 产物清单表。" if "附录" in f else ""
            legs.append(("撰稿师.md", f"撰写 论文/{f}（标题：{c.get('章节标题')}；配额：{c.get('页数配额','按需')}页；要点：{json.dumps(c.get('内容要点',''), ensure_ascii=False)[:400]}）。{特}相关问题的建模笔记/结果解读/图注素材/实验记录自行从 交接/ 与 求解/ 取。图引用 \\includegraphics[width=0.8\\textwidth]{{../求解/问题X/图片/<图名>.png}}。完成写 日志/写_{f}.done", f"写_{f}"))
        wave(legs, timeout=1300)
    # 摘要蜂群
    摘底 = "写摘要（\\begin{abstract}…\\end{abstract}，\\keywords，\\label{abstract:end}）。≤900字必1页。素材=各问结果解读的论文引用清单+定制改造名。每问一段\\textbf{对于问题X：}+硬数字。禁用词表适用。同时把论文标题按内容定稿写入你输出文件首行注释 %标题：xxx。"
    wave([("撰稿师.md", 摘底 + "变体1侧重方法论完整。写 审稿/摘要_变体1.tex。完成写 日志/摘1.done", "摘1"),
          ("撰稿师.md", 摘底 + "变体2侧重结论冲击力。写 审稿/摘要_变体2.tex。完成写 日志/摘2.done", "摘2"),
          ("撰稿师.md", 摘底 + "变体3侧重评委3分钟可复述。写 审稿/摘要_变体3.tex。完成写 日志/摘3.done", "摘3")], timeout=1100)
    wave([("审稿员.md", "摘要评委：比较 审稿/摘要_变体1/2/3.tex，按摘要四要素+语言军规打分，合并优点写终版到 论文/0.摘要.tex（或结构中的摘要文件名），并把定稿论文标题替换进 论文/论文.tex 的 \\title{}。评分写 审稿/摘要评审.json。完成写 日志/摘评.done", "摘评")], timeout=900)
    checkpoint([f"论文/{f}" for f in 章文件] + ["论文/论文.tex"], "P4")

    # ---------- P5 编译 + 审计 + 审稿收敛 + 美化 ----------
    状态["阶段"] = "P5"
    for fix in range(4):
        E, O, P, out = compile_paper(f"#编{fix}")
        if E == 0:
            break
        if not budget_ok():
            break
        wave([("撰稿师.md", f"编译修复：论文编译报错。错误摘录：{out[:1200]}。用 grep -n 定位 论文/*.tex 相应位置修复（只修错误，不动内容）。完成写 日志/编修{fix}.done", f"编修{fix}")], timeout=900)
    prev = 0.0
    for 轮 in (1, 2, 3):
        h.exec("python3 bin/审计.py > 日志/审计.out 2>&1; echo ok", quiet=True)
        上轮 = f"+ 上轮意见 审稿/审稿意见_轮{轮-1}A.json 与 …{轮-1}B.json 核对整改落实情况" if 轮 > 1 else ""
        r = wave([
            ("审稿员.md", f"第{轮}轮全文审稿·评委A（论文/ 全部tex + 审稿/审计报告.json 特别核对其中溯源核验存疑清单 + 交接/ 材料{上轮}）。独立评审。按角色规范产出 审稿/审稿意见_轮{轮}A.json。完成写 日志/审{轮}A.done", f"审{轮}A"),
            ("审稿员.md", f"第{轮}轮全文审稿·评委B（同上输入，独立评审，不看评委A）。侧重挑硬伤：数字一致性、推导跳步、图文相符。产出 审稿/审稿意见_轮{轮}B.json。完成写 日志/审{轮}B.done", f"审{轮}B"),
        ], timeout=1300)
        意A = get_json(f"审稿/审稿意见_轮{轮}A.json", f"轮{轮}A") or {}
        意B = get_json(f"审稿/审稿意见_轮{轮}B.json", f"轮{轮}B") or {}
        分们 = [float(x.get("总分", 0) or 0) for x in (意A, 意B) if x.get("总分")]
        总分 = sum(分们) / len(分们) if 分们 else 0.0
        log(f"审稿轮{轮} 面板均分={总分:.2f}（A={意A.get('总分')} B={意B.get('总分')}）")
        checkpoint([f"审稿/审稿意见_轮{轮}A.json", f"审稿/审稿意见_轮{轮}B.json", "审稿/审计报告.json"], f"P5-审{轮}")
        if 总分 >= 8.6 or (轮 > 1 and 总分 - prev < 0.15) or not budget_ok():
            break
        prev = 总分
        指令 = json.dumps((意A.get("最高优先级修改", []) or [])[:3] + (意B.get("最高优先级修改", []) or [])[:3], ensure_ascii=False)[:5000]
        wave([("撰稿师.md", f"按审稿轮{轮}最高优先级修改逐条落实：{指令}。涉及补计算的：直接写脚本并运行（python3 可用，结果落对应 结果/ 目录），把真实数字写进论文；涉及删改的直接改tex。完成写 日志/修订{轮}.done", f"修订{轮}")], timeout=1300)
        for fix in range(3):
            E, O, P, out = compile_paper(f"#修{轮}-{fix}")
            if E == 0:
                break
            wave([("撰稿师.md", f"编译修复：{out[:1000]}。完成写 日志/编修R{轮}{fix}.done", f"编修R{轮}{fix}")], timeout=900)
    # 美化循环（2轮）
    for 美轮 in (1, 2):
        E, O, P, out = compile_paper(f"#美{美轮}")
        h.exec("mkdir -p 论文/页 && rm -f 论文/页/*.png && cd 论文 && gs -dNOPAUSE -dBATCH -sDEVICE=png16m -r70 -sOutputFile=页/p%02d.png 论文.pdf > /dev/null 2>&1; ls 页 | wc -l", quiet=True)
        d = h.exec("ls 论文/页/*.png | sort", quiet=True)
        pages = [l for l in (d.get("stdout") or "").split() if l.endswith(".png")]
        美腿 = []
        for bi in range(0, len(pages), 8):
            批 = pages[bi:bi + 8]
            imgs = " ".join(f'-i "{p}"' for p in 批)
            名 = f"美{美轮}_{bi//8+1}"
            script = f'''cat 角色/美化师.md > 任务/{名}.txt
printf '\\n=== 本次任务 ===\\n审附图各页（对应 {批[0]} 起连续{len(批)}页），输出写 审稿/{名}.json。完成写 日志/{名}.done\\n' >> 任务/{名}.txt
nohup bash -c 'timeout 800 codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -c model_provider="gw" -c "model_providers.gw.name=\\"gw\\"" -c "model_providers.gw.base_url=\\"https://proxy:18080/v1\\"" -c "model_providers.gw.env_key=\\"OPENAI_API_KEY\\"" -c "model_providers.gw.wire_api=\\"responses\\"" -m gpt-5.6-sol {imgs} -- "$(cat 任务/{名}.txt)" > 日志/{名}.log 2>&1; [ -f 日志/{名}.done ] || echo AUTO > 日志/{名}.done' >/dev/null 2>&1 &
echo {名}'''
            h.exec(script, timeout_s=90, quiet=True)
            美腿.append(名)
        t0 = time.time()
        while time.time() - t0 < 1000 and 美腿:
            time.sleep(30)
            marks = " ".join(f"日志/{n}.done" for n in 美腿)
            d = h.exec(f"c=0; for f in {marks}; do [ -f $f ] && c=$((c+1)); done; echo $c", quiet=True)
            try:
                if int((d.get("stdout") or "0").strip().split()[-1]) >= len(美腿):
                    break
            except Exception:
                pass
        页问题集 = []
        分数们 = []
        for 名 in 美腿:
            v = get_json(f"审稿/{名}.json", 名) or {}
            页问题集 += v.get("页问题", [])
            try:
                分数们.append(float(v.get("美观分", 0)))
            except Exception:
                pass
        美观 = sum(分数们) / len(分数们) if 分数们 else 0
        log(f"美化轮{美轮}: 页问题{len(页问题集)}条 美观分均值{美观:.1f}")
        if not 页问题集 or 美观 >= 8.5 or not budget_ok():
            break
        wave([("撰稿师.md", f"排版执行：按美化师页问题清单逐条改tex（浮动体参数/图宽/表列宽/位置微调，不改文字内容）：{json.dumps(页问题集, ensure_ascii=False)[:5000]}。完成写 日志/排版执行{美轮}.done", f"排版执行{美轮}")], timeout=1100)

    # ---------- P6 出版 ----------
    状态["阶段"] = "P6"
    E, O, P, out = compile_paper("#终")
    h.exec("python3 bin/审计.py > 日志/终审计.out 2>&1; mkdir -p 论文/页 && rm -f 论文/页/*.png && cd 论文 && gs -dNOPAUSE -dBATCH -sDEVICE=png16m -r70 -sOutputFile=页/p%02d.png 论文.pdf >/dev/null 2>&1; ls 页 | wc -l", quiet=True)
    终 = ["论文/论文.pdf", "审稿/审计报告.json", "交接/实验记录.json", "交接/求解计划.md"]
    d = h.exec("ls 论文/*.tex 论文/页/*.png 审稿/*.json 交接/*.json 交接/*.md 2>/dev/null; find 求解 -name '*.py' -o -name '*.json' -o -name '*.png' -o -name '*.sh' 2>/dev/null", quiet=True)
    for l in (d.get("stdout") or "").split():
        if l and not l.endswith(":"):
            终.append(l.replace("/tmp/蜂巢/", ""))
    got = h.harvest(sorted(set(终)), str(产出目录))
    log(f"P6 终稿收割 {len(got)} 文件 → {产出目录}")
    log(f"终态：E={E} Overfull={O} 页数={P} 总腿数={LEG_COUNT} 用时{int((time.time()-T_START)/60)}分钟")
except Exception as e:
    log(f"!! 驾驶异常终止于阶段{状态['阶段']}: {e}\n{traceback.format_exc()[:800]}")
finally:
    with open(产出目录.parent / "运行报告.md", "w", encoding="utf-8") as f:
        f.write(f"# 蜂群自主运行报告\n\n阶段到达：{状态['阶段']}｜腿数：{LEG_COUNT}｜用时：{int((time.time()-T_START)/60)} 分钟\n\n## 事件流\n")
        for ev in 状态["事件"]:
            f.write(f"- {ev}\n")
    print("驾驶结束")
