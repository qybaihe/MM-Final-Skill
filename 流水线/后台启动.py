#!/usr/bin/env python3
"""后台启动：把长跑进程真正脱离当前会话（实测 nohup & 在本机会话里仍会被回收，
start_new_session=True 才能跨会话存活——见 施工日志 M2 环境补充）。

用法：
  python3 后台启动.py <日志文件> <命令...>
例：
  python3 流水线/后台启动.py 真题测试/驾驶.out python3 流水线/蜂群驾驶.py 真题测试/输入 真题测试/成品
"""
import pathlib
import subprocess
import sys
import time

if len(sys.argv) < 3:
    print(__doc__)
    sys.exit(1)

日志 = pathlib.Path(sys.argv[1])
命令 = sys.argv[2:]
日志.parent.mkdir(parents=True, exist_ok=True)
f = open(日志, "a", buffering=1, encoding="utf-8")
f.write(f"\n===== 后台启动 {time.strftime('%Y-%m-%d %H:%M:%S')}：{' '.join(命令)} =====\n")
p = subprocess.Popen(命令, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     start_new_session=True, cwd=str(pathlib.Path(__file__).parent.parent))
pid文件 = 日志.with_suffix(".pid")
pid文件.write_text(str(p.pid), encoding="utf-8")
print(f"已后台启动 pid={p.pid}（脱离会话）\n日志：{日志}\nPID 文件：{pid文件}")
