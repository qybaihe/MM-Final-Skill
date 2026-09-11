#!/usr/bin/env python3
"""本地蜂巢 SDK：取代 hive_sdk，把多智能体流水线跑在本机工作根内。

与沙箱时代的分界：旧 Hive 的"容器"是不守规矩的环境（多实例路由、105-181 分钟轮换、
网关抖动——施工日志 E1-E8，一轮 58.9% 机时耗在等待上）；本地工作根把"持久工作目录"
这个不变量免费给了出来（病根台账 R16），于是播种/探活/指纹/回传整套韧性 machinery 全部
失去存在意义。本模块只保留与旧 Hive 相同的接口形状，让 蜂群驾驶.py 的调用点最小改动。

用法： from 本地蜂巢 import LocalHive; h = LocalHive(root="真题测试/蜂巢镜像"); h.exec("echo hi")
"""
import base64
import os
import pathlib
import shutil
import subprocess
import time

流水线 = pathlib.Path(__file__).resolve().parent
VENV_BIN = 流水线 / "运行时/venv/bin"
TEXLIVE_BIN = pathlib.Path("/opt/homebrew/opt/texlive/bin")
FNM_ALIAS_BIN = pathlib.Path.home() / ".local/share/fnm/aliases/default/bin"


def 找claude():
    """解析 claude CLI（Claude Code 无头模式当腿引擎）：环境变量 > PATH > ~/.local/bin。"""
    候选 = [os.environ.get("CLAUDE_BIN"), shutil.which("claude"), str(pathlib.Path.home() / ".local/bin/claude")]
    for c in 候选:
        if c and pathlib.Path(c).is_file():
            return c
    return "claude"


def 找codex():
    """解析 codex 可执行文件：环境变量 > PATH > fnm default 别名（稳定路径）。"""
    候选 = [os.environ.get("CODEX_BIN"), shutil.which("codex"),
            str(FNM_ALIAS_BIN / "codex")]
    for c in 候选:
        if c and pathlib.Path(c).is_file():
            return c
    return "codex"          # 找不到就交给 PATH 报错（冒烟会第一时间暴露）


class LocalHive:
    """接口与旧 Hive 对齐：exec / f_text / f_local / harvest / leg / leg_img / legs_parallel。"""

    def __init__(self, root, account="a1", engine=None, **_):
        self.root = pathlib.Path(root).resolve()
        self.account = account            # 仅为兼容旧调用签名，本地无多账号概念
        # 腿引擎：codex（默认，codex exec + seatbelt）| claude（claude -p 无头子代理）。经 LEG_ENGINE 交给 role.sh/图片腿.sh 分发；
        # 驱动只认 done 标记与产物，两种引擎的契约一致。
        self.engine = (engine or os.environ.get("LEG_ENGINE") or "codex").strip()
        if self.engine not in ("codex", "claude"):
            raise ValueError(f"未知腿引擎 {self.engine}：只能是 codex 或 claude")
        self.root.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        # PATH 顺序即口径：venv 的 python3 在最前（含全部科学计算依赖），
        # 随后 texlive（xelatex）、fnm default（codex）、homebrew（gs 等）。
        路径们 = [str(VENV_BIN), str(TEXLIVE_BIN), str(FNM_ALIAS_BIN)]
        路径们 = [p for p in 路径们 if pathlib.Path(p).is_dir()]
        env["PATH"] = ":".join(路径们 + [env.get("PATH", "/usr/bin:/bin")])
        # R55（2026-09-11 A 题）：codex 腿在 seatbelt 里用 `/bin/bash -lc` 跑命令，macOS 登录 shell 的 path_helper 会把
        # /etc/paths 的 /opt/homebrew/bin 排到 venv 之前，腿里 `python3` 落到 homebrew 的裸 3.14（无 numpy/openpyxl），
        # 读题官/体检师/规划师三条腿探针全报「不可发现」，规划师据此把全炉锁成纯标准库路线。PATH 顺序在登录 shell 里守不住，
        # 改用 PYTHONPATH 直指 venv 的 site-packages（venv 的 home 就是同一个 homebrew 3.14，二进制兼容），谁当 python3 都能导入。
        站点包 = sorted(VENV_BIN.parent.glob("lib/python3.*/site-packages"))
        if 站点包:
            旧 = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = ":".join([str(站点包[-1])] + ([旧] if 旧 else []))
        # matplotlib 配置/字体缓存收进工作根：seatbelt（workspace-write）下可写，
        # 也避免不同运行之间缓存串味；无显示环境强制 Agg。
        env.setdefault("MPLCONFIGDIR", str(self.root / ".cache/mpl"))
        env.setdefault("MPLBACKEND", "Agg")
        env["CODEX_BIN"] = 找codex()
        env["CLAUDE_BIN"] = 找claude()
        env["LEG_ENGINE"] = self.engine
        self.env = env

    # ---------- 路径安全：一切文件操作锁在工作根内 ----------
    def _内(self, rel):
        p = (self.root / rel.lstrip("/")).resolve()
        if not str(p).startswith(str(self.root)):
            raise ValueError(f"路径越出工作根：{rel}")
        return p

    # ---------- 基础调用 ----------
    def exec(self, script, files=None, outputs=None, timeout_s=560, timeout_ms=None, quiet=False):
        """本地执行一段 bash（自动 cd 到工作根）。返回与旧 Hive 相同的响应 dict。"""
        # 顺手收割：异步腿是本尊的子进程，死后成僵尸会让 kill -0 误判"活着"。
        # 每次 exec 前 poll 一遍登记过的腿，僵尸随即被回收，判死才准确（本地故障注入 场景1）。
        for proc in getattr(self, "_腿进程", []):
            try:
                proc.poll()
            except Exception:
                pass
        if files:
            for f in files:
                p = self._内(f["path"])
                p.parent.mkdir(parents=True, exist_ok=True)
                if "contentB64" in f:
                    p.write_bytes(base64.b64decode(f["contentB64"]))
                else:
                    p.write_text(f.get("content", ""), encoding="utf-8")
        t0 = time.time()
        p = subprocess.Popen(["bash", "-c", script], cwd=self.root, env=self.env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             errors="replace",     # head -c 截断的多字节 UTF-8 不能让驱动整个崩掉（20260908 冒烟实测）
                             start_new_session=True)
        try:
            out, err = p.communicate(timeout=timeout_ms / 1000 if timeout_ms else timeout_s)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            # 杀整个进程组（exec 期间 spawn 的子进程一并收掉）；异步腿不走 exec，
            # 不受此影响（见 leg() 的 start_new_session 语义）。
            try:
                os.killpg(p.pid, 9)
            except ProcessLookupError:
                pass
            out, err = p.communicate()
            rc = -9
        d = {"exit": rc, "stdout": out or "", "stderr": err or "",
             "ms": int((time.time() - t0) * 1000)}
        if outputs:
            fs = []
            for o in outputs:
                q = self._内(o)
                fs.append({"path": o.lstrip("/"), "present": q.is_file(),
                           "content_b64": base64.b64encode(q.read_bytes()).decode() if q.is_file() else ""})
            d["files"] = fs
        if not quiet:
            tag = "OK" if rc == 0 else f"exit={rc}"
            print(f"[exec {tag} {d['ms']}ms] {(d['stdout'] or '')[:400].strip()}")
            if d["stderr"]:
                print("  stderr:", d["stderr"][:200])
        return d

    # ---------- 文件上下行 ----------
    @staticmethod
    def f_text(path, content):
        return {"path": path, "content": content}

    @staticmethod
    def f_local(path, local_path):
        raw = pathlib.Path(local_path).read_bytes()
        return {"path": path, "contentB64": base64.b64encode(raw).decode()}

    def harvest(self, outputs, dest_dir, 重试=3):
        """把工作根内的产物复制到 dest_dir。本地单一文件系统，不存在多实例漏文件，
        重试参数仅为兼容旧签名保留。dest 与工作根同路径时是幂等空转（返回存在清单）。"""
        got = []
        dest = pathlib.Path(dest_dir).resolve()
        for o in outputs:
            src = self._内(o)
            if not src.is_file():
                continue
            tgt = dest / o.lstrip("/")
            if tgt.resolve() == src:
                got.append(o.lstrip("/"))
                continue
            tgt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tgt)
            got.append(o.lstrip("/"))
        return got

    # ---------- 角色腿 ----------
    def _spawn(self, argv, launch_log, env=None):
        """脱离会话 spawn 一条腿：自己是 session leader ⇒ pid==pgid，
        驱动据此做 kill -0 活性检查与 kill -- -pgid 整组回收（沙箱时代做不到的精确性）。
        进程对象登记在案：它死后是本尊的子进程僵尸，靠 exec() 里的 poll 顺手收割。"""
        f = open(launch_log, "a", encoding="utf-8")
        proc = subprocess.Popen(argv, cwd=self.root, env=env or self.env,
                                stdout=f, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        self._腿进程 = getattr(self, "_腿进程", [])
        self._腿进程.append(proc)
        return proc

    def leg(self, role_file, task_text, log_name, timeout_s=560, sync=True, leg_timeout=None, effort=None):
        """跑一条角色腿（引擎见 self.engine）。role_file 相对 角色/，task_text 为任务正文。
        leg_timeout：本条腿的 codex 上限秒数（经 LEG_TIMEOUT 环境变量交给 role.sh 的 with_timeout；
        不传则 role.sh 默认 1200）。M5-1 实测：全文审稿腿在新锚点下 20 分钟内跑不完（rc=142），
        而波次超时与腿上限是两个独立常数——腿上限必须 ≤ 波次超时，否则超时重派会与仍在跑的旧进程双写。"""
        task_rel = f"任务/{log_name}.任务.md"
        p = self._内(task_rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(task_text, encoding="utf-8")
        argv = ["bash", f"bin/role.sh", f"角色/{role_file}", task_rel, log_name]
        env = self.env
        if leg_timeout or effort:
            env = dict(self.env)
            if leg_timeout:
                env["LEG_TIMEOUT"] = str(int(leg_timeout))
            if effort:
                env["LEG_EFFORT"] = str(effort)     # 角色分档推理档（蜂群驾驶.配置["角色档位"]），role.sh 优先于 CODEX_EFFORT
        if sync:
            t0 = time.time()
            proc = subprocess.run(argv, cwd=self.root, env=env, capture_output=True, stdin=subprocess.DEVNULL,
                                  text=True, errors="replace", timeout=timeout_s)   # stdin 必须关：claude -p 会等 stdin EOF
            return {"exit": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr,
                    "ms": int((time.time() - t0) * 1000)}
        launch = self.root / "日志" / f"{log_name}.launch"
        launch.parent.mkdir(parents=True, exist_ok=True)
        self._spawn(argv, launch, env=env)
        return {"exit": 0, "stdout": "launched", "ms": 0}

    def leg_img(self, role_file, task_text, log_name, images, timeout=900, reasoning=True, effort=None):
        """跑一条看图腿（codex：-i 附图；claude：任务里列图路径由腿 Read）。始终异步，由 等腿() 轮询 done 标记。effort：角色分档推理档（LEG_EFFORT）。"""
        task_rel = f"任务/{log_name}.任务.md"
        p = self._内(task_rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(task_text, encoding="utf-8")
        argv = ["bash", "bin/图片腿.sh", f"角色/{role_file}", task_rel, log_name,
                str(timeout), "1" if reasoning else "0"] + list(images)
        launch = self.root / "日志" / f"{log_name}.launch"
        launch.parent.mkdir(parents=True, exist_ok=True)
        env = None
        if effort:
            env = dict(self.env); env["LEG_EFFORT"] = str(effort)
        self._spawn(argv, launch, env=env)
        return {"exit": 0, "stdout": f"{log_name}启动", "ms": 0}

    def legs_parallel(self, legs, poll_interval=30, max_wait=900):
        """并行跑多条腿（接口兼容保留；驱动主路径走 wave()）。"""
        for role, task, name in legs:
            self.leg(role, task, name, sync=False)
        t0 = time.time()
        while time.time() - t0 < max_wait:
            time.sleep(poll_interval)
            n = sum(1 for _, _, name in legs if (self.root / "日志" / f"{name}.done").is_file())
            print(f"  [并行腿 t={int(time.time() - t0)}s] done={n}/{len(legs)}")
            if n >= len(legs):
                return True
        return False


if __name__ == "__main__":
    import sys
    根 = sys.argv[1] if len(sys.argv) > 1 else "/tmp/本地蜂巢自测"
    h = LocalHive(root=根)
    d = h.exec("echo 本地蜂巢在线; pwd; python3 -c 'import numpy, pandas, matplotlib, sklearn; print(\"py栈 OK\")'; which codex xelatex gs")
    print("rc=", d["exit"])
