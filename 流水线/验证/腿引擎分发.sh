#!/bin/bash
# 腿引擎分发单测：用桩 codex/claude（只记 argv、写 done）在临时工作根跑 role.sh 与 图片腿.sh，
# 断言两种引擎各自的调用口径（flag、推理档、系统提示、附图传法、stdin 关闭）与共同契约（done 标记、日志）。不花算力、秒级。
set -u
cd "$(dirname "$0")/../.." || exit 1
ROOT="$(mktemp -d "${TMPDIR:-/tmp}/腿引擎单测.XXXXXX")"; FAIL=0
ok()  { echo "  ✓ $1"; }
bad() { echo "  ✗ $1"; FAIL=1; }
mkdir -p "$ROOT/bin" "$ROOT/角色" "$ROOT/任务" "$ROOT/日志" "$ROOT/stub"
cp 流水线/运行时/role.sh 流水线/运行时/图片腿.sh 流水线/运行时/codex公共.sh 流水线/运行时/claude公共.sh "$ROOT/bin/"
printf '你是测试角色。\n' > "$ROOT/角色/测试.md"; printf '工作根总纲（AGENTS）\n' > "$ROOT/AGENTS.md"
printf '把 1+1 写进 交接/答.txt\n' > "$ROOT/任务/t.任务.md"
# 桩：把 argv 一行一个记到 日志/<桩>.argv，stdin 若未到 EOF 会卡住——用 timeout 检测（stdin 关闭是契约）
cat > "$ROOT/stub/claude" <<'S'
#!/bin/bash
printf '%s\n' "$@" > "日志/claude.argv"; cat > "日志/claude.stdin"; echo "claude 桩完成"
S
cat > "$ROOT/stub/codex" <<'S'
#!/bin/bash
printf '%s\n' "$@" > "日志/codex.argv"; echo "codex 桩完成"
S
chmod +x "$ROOT/stub/claude" "$ROOT/stub/codex"
export CODEX_BIN="$ROOT/stub/codex" CLAUDE_BIN="$ROOT/stub/claude"
# ---- claude 引擎 ----
( cd "$ROOT" && LEG_ENGINE=claude LEG_EFFORT=xhigh LEG_TIMEOUT=30 bash bin/role.sh 角色/测试.md 任务/t.任务.md 腿A > /dev/null 2>&1 )
A="$ROOT/日志/claude.argv"
[ -f "$A" ] && ok "claude 引擎被调用" || bad "claude 引擎未被调用"
grep -qx -- "-p" "$A" && grep -qx -- "--effort" "$A" && grep -qx "xhigh" "$A" && ok "claude: -p 与 --effort xhigh（角色分档透传）" || bad "claude: 缺 -p/--effort xhigh"
grep -qx -- "--append-system-prompt" "$A" && grep -q "工作根总纲" "$A" && grep -q "你是测试角色" "$A" && ok "claude: 系统提示 = AGENTS.md + 角色文件" || bad "claude: 系统提示未含 AGENTS/角色"
grep -q "=== 本次任务 ===" "$A" && grep -q "把 1+1 写进" "$A" && ok "claude: 任务正文作为用户消息" || bad "claude: 任务正文缺失"
grep -qx -- "--permission-mode" "$A" && grep -qx "acceptEdits" "$A" && grep -qx "WebFetch" "$A" && ok "claude: acceptEdits + 禁网工具" || bad "claude: 权限/工具口径不对"
grep -qx -- "--no-session-persistence" "$A" && ok "claude: 不留会话" || bad "claude: 未禁会话持久化"
[ -f "$ROOT/日志/claude.stdin" ] && [ ! -s "$ROOT/日志/claude.stdin" ] && ok "claude: stdin 已关闭（空 EOF）" || bad "claude: stdin 未关闭（会挂死）"
[ -f "$ROOT/日志/腿A.system.md" ] && ok "claude: 系统提示留档 日志/腿A.system.md" || bad "claude: 无系统提示留档"
grep -q "AUTO(rc=0)" "$ROOT/日志/腿A.done" 2>/dev/null && ok "共同契约: 角色未写 done 时 AUTO(rc=0) 兜底" || bad "共同契约: done 兜底缺失"
# ---- codex 引擎（默认）----
rm -f "$ROOT/日志/"*.done
( cd "$ROOT" && LEG_EFFORT=high LEG_TIMEOUT=30 bash bin/role.sh 角色/测试.md 任务/t.任务.md 腿B > /dev/null 2>&1 )
C="$ROOT/日志/codex.argv"
[ -f "$C" ] && [ ! -f "$ROOT/日志/claude.argv.2" ] && ok "默认引擎 = codex" || bad "默认引擎不是 codex"
grep -qx "exec" "$C" && grep -q "model_reasoning_effort=high" "$C" && grep -q "workspace-write" "$C" && ok "codex: exec + effort + seatbelt" || bad "codex: 调用口径不对"
grep -q "你是测试角色" "$C" && grep -q "=== 本次任务 ===" "$C" && ok "codex: 角色 + 任务合成一段提示" || bad "codex: 提示合成不对"
# ---- 看图腿 ----
rm -f "$A" "$C"; : > "$ROOT/图1.png"; : > "$ROOT/图2.png"
( cd "$ROOT" && LEG_ENGINE=claude LEG_TIMEOUT=30 bash bin/图片腿.sh 角色/测试.md 任务/t.任务.md 看图A 30 0 图1.png 图2.png > /dev/null 2>&1 )
grep -q "先用 Read 工具逐张打开下列附图（共 2 张）" "$A" && grep -q "^- 图2.png" "$A" && ! grep -qx -- "-i" "$A" && ok "claude 看图腿: 图路径列进任务、无 -i" || bad "claude 看图腿: 附图传法不对"
grep -qx "medium" "$A" && ok "claude 看图腿: 非推理腿 effort=medium" || bad "claude 看图腿: 非推理腿档位不对"
( cd "$ROOT" && LEG_TIMEOUT=30 bash bin/图片腿.sh 角色/测试.md 任务/t.任务.md 看图B 30 1 图1.png > /dev/null 2>&1 )
grep -qx -- "-i" "$C" && grep -qx "图1.png" "$C" && grep -q "model_reasoning_effort=high" "$C" && ok "codex 看图腿: -i 附图 + 推理腿 high" || bad "codex 看图腿: 口径不对"
# ---- 引擎名校验（本地蜂巢）----
python3 - "$ROOT" <<'PY' && ok "本地蜂巢: engine 参数校验与环境下发" || bad "本地蜂巢: engine 校验失败"
import sys, pathlib, importlib.util
spec = importlib.util.spec_from_file_location("本地蜂巢", pathlib.Path("流水线/本地蜂巢.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
h = m.LocalHive(root=sys.argv[1] + "/根1", engine="claude"); assert h.env["LEG_ENGINE"] == "claude" and h.env.get("CLAUDE_BIN")
h2 = m.LocalHive(root=sys.argv[1] + "/根2"); assert h2.env["LEG_ENGINE"] == "codex"
try: m.LocalHive(root=sys.argv[1] + "/根3", engine="gemini"); raise SystemExit("应拒绝未知引擎")
except ValueError: pass
PY
rm -rf "$ROOT"
[ "$FAIL" = 0 ] && { echo "腿引擎分发单测：全部通过"; exit 0; } || { echo "!! 腿引擎分发单测有失败"; exit 1; }
