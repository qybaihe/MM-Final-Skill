#!/bin/bash
# 打包：从核心 skill（.claude/skills/paper-foundry）生成两个发布版
#   发布/paper-foundry-claude   默认引擎 claude（Claude Code 操盘 + claude -p 子代理当腿），只装 Claude 端
#   发布/paper-foundry-codex    默认引擎 codex（codex exec + seatbelt），只装 Codex 端，带 agents/openai.yaml
# 两份与核心只差：SKILL.md 的 name/description/「本版本」段、scripts/引擎.default、agents/ 有无。不要手改发布版——改核心再打包。
# 用法：打包.sh            生成/覆盖 发布/
#       打包.sh --核对     生成到临时目录与 发布/ 比对，不一致或缺失非零退出（验证.sh 调用，防核心与发布版漂移）
set -u
source "$(dirname "$0")/公共.sh"
CORE="$PROJ/.claude/skills/paper-foundry"; OUTROOT="$PROJ/发布"
[ -f "$CORE/SKILL.md" ] || { echo "!! 核心不存在：$CORE"; exit 1; }
gen() {   # $1=claude|codex  $2=目标目录
  local v="$1" dst="$2"
  rm -rf "$dst"; mkdir -p "$dst"
  rsync -a --exclude='__pycache__/' --exclude='.DS_Store' "$CORE/" "$dst/"
  echo "$v" > "$dst/scripts/引擎.default"
  python3 - "$CORE/SKILL.md" "$dst/SKILL.md" "$v" <<'PY'
import sys, re
src, dst, v = sys.argv[1], sys.argv[2], sys.argv[3]
t = open(src, encoding="utf-8").read()
name = f"paper-foundry-{v}"
t = re.sub(r"^name: paper-foundry$", f"name: {name}", t, count=1, flags=re.M)
tag = {"claude": "【Claude 版：Claude Code 操盘，角色腿 = claude -p 子代理；默认 --引擎=claude】", "codex": "【Codex 版：Codex 操盘，角色腿 = codex exec（seatbelt）；默认 --引擎=codex】"}[v]
t = re.sub(r"^description: ", f"description: {tag}", t, count=1, flags=re.M)
block = {
 "claude": f"""> **本版本：{name}**（由核心 `.claude/skills/paper-foundry` 经 `scripts/打包.sh` 生成，勿手改）
> - 默认腿引擎 **claude**（`scripts/引擎.default`）：每条角色腿是一个 `claude -p` 无头子代理——系统提示 = 工作根 AGENTS.md + 角色文件，任务作用户消息，`--effort` 吃同一张角色分档表，腿内可再开 Agent 子代理。
> - 宿主 Claude Code：`bash scripts/安装.sh` 只装到 `~/.claude/skills/{name}`，调用 `/{name}`。
> - 第一炉之前必做 `bash scripts/体检.sh <产出目录> --引擎=claude --探针`（真调一次 `claude -p`，只转述 CLI 原话；401/未登录是用户侧配置，本 skill 不读不改密钥与端点）。Claude 引擎尚无整炉实跑数据，先 `--档位=快速` 冒一炉。
> - 想用 codex 腿：`启动.sh … --引擎=codex`（引擎只在开炉/续跑边界换，铁律 20）。
""",
 "codex": f"""> **本版本：{name}**（由核心 `.claude/skills/paper-foundry` 经 `scripts/打包.sh` 生成，勿手改）
> - 默认腿引擎 **codex**（`scripts/引擎.default`）：每条角色腿是 `codex exec`（seatbelt workspace-write，端点/模型只在 `~/.codex/config.toml`，本 skill 不读不改）。2026-09 对照跑 411 腿 / 26.7 h 的全部量级数据来自这个引擎。
> - 宿主 Codex：`bash scripts/安装.sh` 只装到 `~/.codex/skills/{name}`，调用 `${name}`；`启动.sh`/`切换.sh`/`停跑.sh` 在**沙箱外**执行（腿要联网、要 kill 进程组）。
> - 想用 claude 腿：`启动.sh … --引擎=claude`，先 `体检.sh --引擎=claude --探针`。
""",
}[v]
h1 = "# 论文铸造厂操盘手册\n"
assert h1 in t, "找不到 H1"
t = t.replace(h1, h1 + "\n" + block, 1)
open(dst, "w", encoding="utf-8").write(t)
PY
  if [ "$v" = codex ]; then
    python3 - "$dst/agents/openai.yaml" <<'PY'
import sys
p = sys.argv[1]; t = open(p, encoding="utf-8").read()
t = t.replace('display_name: "论文铸造厂"', 'display_name: "论文铸造厂（Codex 版）"').replace("$paper-foundry", "$paper-foundry-codex")
open(p, "w", encoding="utf-8").write(t)
PY
  else
    rm -rf "$dst/agents"
  fi
}
if [ "${1:-}" = "--核对" ]; then
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/打包核对.XXXXXX")"; rc=0
  for v in claude codex; do
    gen "$v" "$TMP/paper-foundry-$v"
    if [ ! -d "$OUTROOT/paper-foundry-$v" ]; then echo "  ✗ 发布/paper-foundry-$v 不存在（bash scripts/打包.sh）"; rc=1
    elif diff -r --exclude='__pycache__' --exclude='.DS_Store' "$TMP/paper-foundry-$v" "$OUTROOT/paper-foundry-$v" >/dev/null; then echo "  ✓ 发布/paper-foundry-$v 与核心一致"
    else echo "  ✗ 发布/paper-foundry-$v 与核心不一致（改了核心没打包？bash scripts/打包.sh）"; diff -rq "$TMP/paper-foundry-$v" "$OUTROOT/paper-foundry-$v" | head -5; rc=1; fi
  done
  rm -rf "$TMP"; exit $rc
fi
mkdir -p "$OUTROOT"
for v in claude codex; do gen "$v" "$OUTROOT/paper-foundry-$v"; echo "已生成 发布/paper-foundry-${v}（默认引擎 ${v}，$(find "$OUTROOT/paper-foundry-$v" -type f | wc -l | tr -d ' ') 个文件）"; done
