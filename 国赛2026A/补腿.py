#!/usr/bin/env python3
"""驱动**停净之后**对活镜像派一条一次性角色腿（同步等它写 done）。用法：
  python3 补腿.py <角色文件.md> <腿名> <任务文本文件> [--根=国赛2026A/蜂巢镜像] [--上限秒=1800]
拒绝在驱动运行时执行（铁律 4）。"""
import pathlib, subprocess, sys, time
sys.path.insert(0, "/Users/bytedance/数模自动化/流水线")
from 本地蜂巢 import LocalHive
args = [a for a in sys.argv[1:] if not a.startswith("--")]
opts = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
角色, 腿名, 任务文件 = args[0], args[1], args[2]
根 = pathlib.Path(opts.get("根", "/Users/bytedance/数模自动化/国赛2026A/蜂巢镜像"))
上限 = int(opts.get("上限秒", "1800"))
if subprocess.run(["pgrep", "-f", "蜂群驾驶.py"], capture_output=True).returncode == 0:
    sys.exit("!! 驱动在跑，拒绝派腿（先 停跑.sh）")
任务 = pathlib.Path(任务文件).read_text(encoding="utf-8")
h = LocalHive(root=根)
t0 = time.time()
print(f"[{time.strftime('%H:%M:%S')}] 派腿 {腿名}（{角色}，上限 {上限}s）")
r = h.leg(角色, 任务, 腿名, sync=True, timeout_s=上限 + 120, leg_timeout=上限)
done = 根 / "日志" / f"{腿名}.done"
print(f"[{time.strftime('%H:%M:%S')}] exit={r['exit']} 用时 {int(time.time()-t0)}s；done={'有' if done.exists() else '无'}")
if done.exists():
    print(done.read_text(encoding='utf-8')[:800])
