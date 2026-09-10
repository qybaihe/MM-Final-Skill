#!/usr/bin/env python3
"""蜂巢自我进化循环引擎（本地工作根内运行；ROOT 取脚本所在 bin/ 的上级，与部署位置无关）。
用法: python3 bin/循环.py <循环名> <配置.json>
配置: {"生成": {"角色":..., "任务":...}(可选，首轮已有产物则省),
       "批评": {"角色":..., "任务模板":...(含{轮}占位)},
       "修订": {"角色":..., "任务模板":...},
       "图片": [路径...](批评时附给 codex 看),
       "达标分": 8.5, "最大轮数": 3, "分数文件": "审稿/循环_<名>_轮{轮}.json"}
批评角色必须把 {"分数": x, "问题": [...], "修改指令": [...]} 写入分数文件。
退出: 达标 / 轮数用尽 / 分数不再提升(平台期)。
"""
import json
import os
import pathlib
import subprocess
import sys

NAME, CFG = sys.argv[1], json.load(open(sys.argv[2], encoding="utf-8"))
ROOT = str(pathlib.Path(__file__).resolve().parent.parent)


def codex(prompt, images=(), log=""):
    # 端点/模型由 ~/.codex/config.toml 决定；沙箱时代的 gw 覆盖已退役（见 codex公共.sh 注释）。
    # seatbelt workspace-write 把写权限锁在 ROOT 内；subprocess timeout 替代 macOS 没有的 GNU timeout。
    cmd = [os.environ.get("CODEX_BIN", "codex"), "exec", "--skip-git-repo-check",
           "--sandbox", "workspace-write",
           "-c", f'sandbox_workspace_write.writable_roots=["{os.environ["HOME"]}/Library/texlive"]',
           "-c", 'model_reasoning_effort="high"']
    for im in images:
        cmd += ["-i", im]
    cmd += ["--", prompt]
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=620)
        rc, out, err = r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -9, "", "TIMEOUT 620s"
    if log:
        open(f"{ROOT}/日志/{log}", "w").write(out[-8000:] + "\n--STDERR--\n" + err[-2000:])
    return rc


def role_prompt(role, task):
    return open(f"{ROOT}/角色/{role}", encoding="utf-8").read() + "\n\n=== 本次任务 ===\n" + task


best = -1
for 轮 in range(1, CFG.get("最大轮数", 3) + 1):
    if 轮 == 1 and CFG.get("生成"):
        g = CFG["生成"]
        codex(role_prompt(g["角色"], g["任务"]), log=f"循环_{NAME}_生成.log")
    # 批评
    c = CFG["批评"]
    score_file = CFG["分数文件"].replace("{轮}", str(轮))
    codex(role_prompt(c["角色"], c["任务模板"].replace("{轮}", str(轮)).replace("{分数文件}", score_file)),
          images=CFG.get("图片", []), log=f"循环_{NAME}_批评{轮}.log")
    try:
        s = json.load(open(f"{ROOT}/{score_file}", encoding="utf-8"))
        score = float(s.get("分数", 0))
    except Exception as e:
        print(f"轮{轮} 分数文件解析失败: {e}")
        score, s = 0.0, {"修改指令": ["分数文件缺失，重新批评并按格式输出"]}
    print(f"轮{轮} 分数={score}")
    if score >= CFG.get("达标分", 8.5):
        print("达标，退出循环")
        break
    if score <= best + 0.2 and 轮 > 1:
        print("平台期，退出循环")
        break
    best = max(best, score)
    if 轮 == CFG.get("最大轮数", 3):
        break
    # 修订
    r = CFG["修订"]
    指令 = json.dumps(s.get("修改指令", s.get("问题", [])), ensure_ascii=False)
    codex(role_prompt(r["角色"], r["任务模板"].replace("{轮}", str(轮)).replace("{指令}", 指令)),
          log=f"循环_{NAME}_修订{轮}.log")

open(f"{ROOT}/日志/循环_{NAME}.done", "w").write(f"最终分数={max(best, score if 'score' in dir() else 0)}\n")
print("循环结束")
