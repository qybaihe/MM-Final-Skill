#!/usr/bin/env python3
"""本地故障注入验证（M5-0 迁移后取代沙箱版故障注入）。

沙箱版测的是"多实例/轮换/回传"——那些故障模式随容器一起退役了。
本地版测本地特有的失败模式，原则不变（P7）：韧性只认故障注入，不在健康态宣布正确。

用法: python3 流水线/验证/本地故障注入.py
"""
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from 本地蜂巢 import LocalHive

结果 = []


def 记(名, ok, 说明=""):
    结果.append((名, bool(ok), 说明))
    print(f"  [{'✓' if ok else '✗'}] {名}  {说明}")


根 = pathlib.Path(tempfile.mkdtemp(prefix="本地故障注入_"))
h = LocalHive(root=根)
print(f"工作根：{根}")

# ---------- 1. 腿进程死亡：pid 文件 + kill -0 活性检查 + kill -- -pgid 整组回收 ----------
print("\n场景1：腿跑到一半死掉 → pid 语义必须支持判死与整组回收")
h.exec("mkdir -p 日志 任务 角色 bin", files=[
    h.f_text("角色/假腿.md", "你是假腿。"),
    h.f_text("bin/codex公共.sh", 'CODEX_BIN="${CODEX_BIN:-codex}"\nwith_timeout() { local t="$1"; shift; perl -e \'alarm shift; exec @ARGV\' "$t" "$@"; }\n'),
    h.f_local("bin/role.sh", pathlib.Path(__file__).parent.parent / "运行时/role.sh"),
], quiet=True)
# 桩 codex：睡 3600 秒（模拟卡死的腿），绝不写 done
桩 = 根 / "bin/假codex.sh"
桩.write_text("#!/bin/bash\nsleep 3600\n", encoding="utf-8")
桩.chmod(0o755)
h.env["CODEX_BIN"] = str(桩)
h.leg("假腿.md", "睡一觉。", "死腿", sync=False)
时间 = time.time()
pid = None
for _ in range(40):                      # 等 pid 文件落盘（最多 4s）
    p = 根 / "日志/死腿.pid"
    if p.is_file():
        pid = int(p.read_text().strip())
        break
    time.sleep(0.1)
记("pid 文件落盘", pid is not None, f"pid={pid} 耗时{time.time()-时间:.1f}s")
活着 = False
if pid:
    try:
        os.kill(pid, 0)
        活着 = True
    except ProcessLookupError:
        pass
记("kill -0 探活为真（腿在跑）", 活着)
if pid:
    # 整组回收：pid==pgid（start_new_session），杀组后 bash 与 codex 子进程必须一起死
    os.killpg(pid, signal.SIGKILL)
    time.sleep(0.5)
    h.exec("true", quiet=True)      # exec 里的 poll 收割僵尸（本尊是腿的父进程）
    try:
        os.kill(pid, 0)
        死了 = False
    except ProcessLookupError:
        死了 = True
    孙子 = subprocess.run(["pgrep", "-f", "假codex.sh"], capture_output=True).returncode != 0
    记("kill -- -pgid 整组回收", 死了 and 孙子, f"bash死={死了} codex子孙清={孙子}")

# ---------- 2. 腿异常退出：role.sh 必须写 AUTO done（wave 的失败信号）----------
print("\n场景2：codex 异常退出（rc=3）→ role.sh 写 AUTO done")
桩2 = 根 / "bin/假codex2.sh"
桩2.write_text("#!/bin/bash\nexit 3\n", encoding="utf-8")
桩2.chmod(0o755)
h.env["CODEX_BIN"] = str(桩2)
h.leg("假腿.md", "立刻死。", "秒死腿", sync=False)
done = None
for _ in range(100):
    p = 根 / "日志/秒死腿.done"
    if p.is_file():
        done = p.read_text(encoding="utf-8").strip()
        break
    time.sleep(0.1)
记("AUTO done 写出", done is not None and done.startswith("AUTO"), f"内容={done!r}")

# ---------- 3. exec 超时：整个进程组被收掉，不留孤儿 ----------
print("\n场景3：exec 超时 → 整组杀，不留孤儿进程")
d = h.exec("sleep 0.1 & sleep 300 & sleep 300", timeout_s=2, quiet=True)
time.sleep(0.5)
孤儿 = subprocess.run(["pgrep", "-f", "sleep 300"], capture_output=True, text=True).stdout.strip()
记("exec 超时 rc≠0", d["exit"] != 0, f"rc={d['exit']}")
记("无孤儿 sleep 300", 孤儿 == "", f"残留={孤儿!r}")

# ---------- 4. harvest：跨目录真实复制，同源路径幂等 ----------
print("\n场景4：harvest 复制语义")
h.exec("mkdir -p 交接 && echo 数据 > 交接/x.json && echo 文 > 交接/y.md", quiet=True)
目 = pathlib.Path(tempfile.mkdtemp(prefix="收割_"))
got = h.harvest(["交接/x.json", "交接/y.md", "交接/不存在.json"], str(目))
记("存在的都收到", sorted(got) == ["交接/x.json", "交接/y.md"], f"got={got}")
记("内容一致", (目 / "交接/x.json").read_text().strip() == "数据")
got2 = h.harvest(["交接/x.json"], str(根))       # 源=目标（工作根本身）→ 幂等
记("同路径幂等", got2 == ["交接/x.json"])

# ---------- 5. 路径安全：files 路径越出工作根必须被拒 ----------
print("\n场景5：路径安全")
try:
    h.exec("true", files=[h.f_text("../越狱.txt", "x")], quiet=True)
    记(".. 越狱被拒", False, "未抛异常")
except ValueError:
    记(".. 越狱被拒", True)

# ---------- 6. 环境：venv python / xelatex / gs / codex / matplotlib 中文字体 ----------
print("\n场景6：环境要件")
d = h.exec("python3 -c 'import numpy, pandas, sklearn, pypdf; print(\"py栈OK\")' && "
           "which xelatex gs codex", quiet=True)
记("py栈+texlive+gs+codex 可达", d["exit"] == 0 and "py栈OK" in (d.get("stdout") or ""),
   (d.get("stdout") or "").strip().replace("\n", " ")[:120])
d2 = h.exec("python3 -c \""
            "from matplotlib import font_manager; "
            "名 = {f.name for f in font_manager.fontManager.ttflist}; "
            "print('字体OK' if {'FandolHei','Noto Sans CJK SC'} <= 名 else '字体缺')\"", quiet=True)
记("matplotlib 可见 FandolHei/Noto（绘图腿命根子）",
   "字体OK" in (d2.get("stdout") or ""), (d2.get("stdout") or "").strip()[:80])

print("\n===== 汇总 =====")
过 = sum(1 for _, ok, _ in 结果 if ok)
print(f"{过}/{len(结果)} 通过")
shutil.rmtree(根, ignore_errors=True)
shutil.rmtree(目, ignore_errors=True)
sys.exit(0 if 过 == len(结果) else 1)
