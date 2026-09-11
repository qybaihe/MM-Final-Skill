#!/bin/bash
# 本地 codex 调用口径（单一事实来源）：role.sh / 图片腿.sh 共用，由 bin/ 下的脚本 source。
#
# 端点与模型由 ~/.codex/config.toml 决定（自定义端点 + 模型名只记在那个文件里，本仓库不记）。
# 沙箱时代的 -c model_provider=gw / -m gpt-5.6-sol 覆盖已随网关 proxy:18080 一起退役
# （该网关从本机不可达，见 施工日志 M5-0 迁移条目）。
#
# 安全边界：--sandbox workspace-write（macOS seatbelt）把腿的文件写权限锁在工作根内——
# 沙箱时代 --dangerously-bypass-approvals-and-sandbox 无所谓（一次性容器），
# 在真机上必须收回这个口子。两个默认口子也要堵（冒烟实测：腿曾成功写出 /tmp/越狱测试.txt）：
#   exclude_slash_tmp —— codex 默认把 /tmp 也算可写，必须排除；
#   writable_roots 只放行 TeX Live 用户缓存（xelatex 写格式缓存用）。
# $TMPDIR（每用户随机目录）保留可写：python/xelatex 的临时文件都指望着它。
CODEX_BIN="${CODEX_BIN:-codex}"
CODEX_BASE_FLAGS=(--skip-git-repo-check --sandbox workspace-write
  -c "sandbox_workspace_write.exclude_slash_tmp=true"
  -c "sandbox_workspace_write.writable_roots=[\"$HOME/Library/texlive\"]")

# macOS 无 GNU timeout：perl 替代。
# 旧写法 `perl -e 'alarm shift; exec @ARGV'` 只杀得到 exec 进去的那个进程——而 `codex` 是 node 包装脚本，
# 真干活的是它拉起的原生 codex-darwin-arm64 子进程：包装被 SIGALRM 杀掉后子进程成孤儿（ppid 1）继续跑、
# 继续占端点、几分钟后还会把 done/产物写出来（20260908 重标审稿 C：17:14 判 rc=142，17:22 孤儿写出成品；
# 审稿 A 孤儿跑了 30 分钟以上）。所以这里 fork 一个新会话（setsid）再 exec，到点对整个子进程组
# 先 TERM、最多等 10 秒再 KILL；收到外部 TERM/INT/HUP（驱动 kill -- -pgid 整组回收）同样转发给子组，
# 子组不会因为 setsid 逃出驱动的回收。退出码：超时 142（沿用旧约定，done 标记 AUTO(rc=142) 可 grep），
# 被外部终止 143，正常结束原样透传。
with_timeout() {
  local t="$1"; shift
  perl -e '
    use POSIX qw(setsid :errno_h);
    my $t = shift @ARGV;
    my $pid = fork();
    die "fork failed: $!\n" unless defined $pid;
    if ($pid == 0) { setsid(); exec @ARGV; print STDERR "exec failed: $!\n"; exit 127; }
    my $why = 0;
    # R63：codex 在沙箱里起的工具子进程会另开进程组（setpgid），只杀 -$pid 组它们会逃成孤儿继续跑
    # （2026-09-11 A 题试验：LEG_TIMEOUT=60 的腿 rc=142 后 shell 工具 sleep 400 存活）。
    # 杀之前先按 ppid 树收齐 $pid 的全部后代（父死后孩子会被过继给 1，事后再找就找不到了），组和后代一起 TERM→KILL。
    my $desc = sub {
      my %kids; open(my $ps, "-|", "ps", "-eo", "pid=,ppid=") or return ();
      while (<$ps>) { my ($p, $pp) = split; push @{$kids{$pp}}, $p if defined $pp; }
      close $ps;
      my @q = ($pid); my @all;
      while (@q) { my $x = shift @q; for my $k (@{$kids{$x} || []}) { push @all, $k; push @q, $k; } }
      return @all;
    };
    my $reap = sub {
      my @d = $desc->();
      kill "TERM", -$pid; kill "TERM", @d if @d;
      for (1..10) { last unless kill(0, -$pid) || grep { kill(0, $_) } @d; select(undef, undef, undef, 1); }
      kill "KILL", -$pid; kill "KILL", @d if @d;
    };
    $SIG{ALRM} = sub { $why ||= 142; $reap->(); };
    $SIG{TERM} = $SIG{INT} = $SIG{HUP} = sub { $why ||= 143; $reap->(); };
    alarm $t;
    my $rc = 0;
    while (1) {
      my $w = waitpid($pid, 0);
      if ($w == $pid) { $rc = $?; last; }
      last if $w == -1 && $! != EINTR;
    }
    alarm 0;
    exit $why if $why;
    exit(($rc & 127) ? 128 + ($rc & 127) : ($rc >> 8));
  ' "$t" "$@"
}
