#!/bin/bash
# 本地 Claude 腿引擎口径（单一事实来源）：role.sh / 图片腿.sh 在 LEG_ENGINE=claude 时 source 本文件。
# 与 codex公共.sh 平行：那边是 `codex exec`（seatbelt workspace-write），这边是 `claude -p`（Claude Code CLI 无头模式）。
#
# 鉴权/端点：由用户自己的 Claude Code 配置决定（`claude /login` 的登录态，或 ~/.claude/settings.json 的 env 块）。
# 本流水线不读、不改、不打印任何密钥或端点；体检.sh --引擎=claude 只做一次 ≤120 s 的连通探针并转述 CLI 的原话。
#
# 已知坑（20260910 实测，见 references/故障手册.md I 类）：
#   1. `claude -p` 会等 stdin 到 EOF——从驱动/工具里拉起时 stdin 若是没关的管道就永远挂着：这里一律 </dev/null。
#   2. 嵌套在另一个 Claude Code 会话里跑时，环境变量 CLAUDECODE=1 会被子进程继承；无头模式不受影响，但为稳妥起见清掉。
#   3. settings.json env 块若指向已退役的自定义端点，腿会在 ~3 分钟重试后报 401——这是用户侧配置，不是链路故障。
#
# 沙箱边界：Claude Code 没有 codex 的 seatbelt 文件锁；腿以 --permission-mode acceptEdits（编辑只自动放行 cwd=工作根内）
# + 显式工具白名单跑，联网类工具（WebFetch/WebSearch）禁用——与 codex 腿「无外网」口径一致。腿内允许再开 Agent 子代理。
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
CLAUDE_BASE_FLAGS=(-p --output-format text --no-session-persistence
  --permission-mode acceptEdits
  --allowedTools Bash Edit Write MultiEdit NotebookEdit Read Glob Grep Agent Task
  --disallowedTools WebFetch WebSearch)
# 推理档：驱动按角色分档下发 LEG_EFFORT > 启动命令全局 LEG_EFFORT_DEFAULT（历史名 CODEX_EFFORT 仍认）> high。
# Claude CLI 与 codex 同名（low/medium/high/xhigh；Claude 另有 max，本流水线不用）。
claude_effort() { echo "${LEG_EFFORT:-${LEG_EFFORT_DEFAULT:-${CODEX_EFFORT:-high}}}"; }
# 模型：LEG_MODEL（别名 opus/sonnet/fable 或全名），默认 opus。
claude_model() { echo "${LEG_MODEL:-opus}"; }
# 系统提示 = 工作根 AGENTS.md（若有，与 codex 自动读 AGENTS.md 对齐）+ 角色文件全文；任务正文作为用户消息。
# 文件落在 日志/<腿名>.system.md 便于事后复盘「这条腿到底看到了什么」。
claude_system_file() {   # $1=角色文件 $2=腿名
  local out="日志/$2.system.md"
  { [ -f AGENTS.md ] && { cat AGENTS.md; printf '\n\n'; }; cat "$1"; } > "$out"
  echo "$out"
}
# 跑一条 Claude 腿：claude_run <秒上限> <系统提示文件> <用户消息>  （stdout/stderr 由调用方重定向到腿日志）
claude_run() {
  local t="$1" sys="$2" msg="$3"
  # with_timeout 是 shell 函数，不能经 env 调用：在子 shell 里 unset 再调
  ( unset CLAUDECODE; with_timeout "$t" "$CLAUDE_BIN" "${CLAUDE_BASE_FLAGS[@]}" \
      --model "$(claude_model)" --effort "$(claude_effort)" \
      --append-system-prompt "$(cat "$sys")" "$msg" </dev/null )
}
