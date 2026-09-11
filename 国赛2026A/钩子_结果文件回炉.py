#!/usr/bin/env python3
"""切换.sh 钩子（驱动停净后、续跑前执行）：对镜像里契约列出的 result*.xlsx 跑 核对结果模板.py；有 ✗ 的文件逐个派 建模师「回炉·结果文件」腿
（补腿.py 同步等 done），再核对一次；仍有 ✗ → 退出 1（切换.sh 不续跑，交人工）。无 ✗ → 退出 0。不手填任何数值。"""
import json, pathlib, re, subprocess, sys, time
PROJ = pathlib.Path("/Users/bytedance/数模自动化"); 镜像 = PROJ / "国赛2026A/蜂巢镜像"; 模板目录 = 镜像 / "数据/附件3"
SCR = pathlib.Path(__file__).resolve().parent; PY = PROJ / "流水线/运行时/venv/bin/python3"
def 时刻(): return time.strftime("%H:%M:%S")
def 核对():
    r = subprocess.run([str(PY), str(PROJ / "流水线/验证/核对结果模板.py"), str(镜像), str(模板目录)], capture_output=True, text=True, cwd=PROJ, timeout=600)
    行 = [l.rstrip() for l in (r.stdout or "").splitlines()]
    坏 = {}
    for l in 行:
        m = re.match(r"\s*✗ result(\d)\.xlsx", l)
        if m: 坏.setdefault(m.group(1), []).append(l.strip())
    return 行, 坏
def 契约摘录(n):
    s = json.dumps(json.load(open(镜像 / "交接/题面契约.json", encoding="utf-8")), ensure_ascii=False)
    return [m.group(0) for m in re.finditer(r'[^"]{0,120}result%s\.xlsx[^"]{0,240}' % n, s)][:6]
def 任务文本(n, 坏行, 全行):
    本文件行 = [l for l in 全行 if f"result{n}.xlsx" in l]
    return f"""你负责问题{n}的【结果文件回炉】——不是重新建模，模型、口径、结果声明与所有已定数值一律不变，只修 求解/问题{n}/结果/result{n}.xlsx 的写出层。
契约要求把完整结果按 数据/附件3/result{n}.xlsx 模板写出，题面硬约束「所有结果保留四位小数」。核对脚本对当前文件的结论（✗ 为不合格）：
{chr(10).join('  ' + l for l in 本文件行)}
契约相关裁定摘录：
{chr(10).join('  - ' + l for l in 契约摘录(n))}
修法要求：
1. 所有存入单元格的数值——含 A 列时间（包括契约要求补的实际结束行的时刻）与全部含水率/温度——先 round(值, 4) 再写入单元格；只设 number_format="0.0000" 不算保留四位小数。
2. 角格 A1 沿用模板文字「时间\\到药材中心的距离」；工作表名、首行距离网格（0…2 每 0.1 cm，result4 末列「药材表面」）、A 列时间步、行数、末行（实际结束时刻）、越界格留空规则全部保持契约裁定不变。
3. 优先从已保存的未舍入数表（如 求解/问题{n}/结果/分钟数表_未舍入.json、检查点文件）重建工作簿；若必须重跑求解脚本，运行控制在 12 分钟内。
4. 改动必须落在 求解/问题{n}/求解_问题{n}.py 的导出函数里（附录要收录可复现源码），并另写一个最小重建脚本 求解/问题{n}/重建结果文件.py 供本次直接运行；本次就运行它把 result{n}.xlsx 覆盖为合格版。
5. 写完用 openpyxl 读回自检：行数、列数、A 列步长、末行时刻、每个数值 round(v,4)==v、角格文字，把自检结果追加进 求解/问题{n}/结果/导出核验.json 的键「回炉自检」。
6. 交接/实验记录.json 追加一条（类别 流程事件）；交接/建模笔记_问题{n}.md 末尾追加一节「结果文件回炉」说明改了什么、数值未变。
7. 不许手填/手改任何数值，不许改 交接/结果声明_问题{n}.json 的数值。
完成写 日志/回炉_结果文件_问题{n}.done（首行 PASS 或 FAIL，其后一行写自检摘要）。"""
if __name__ == "__main__":
    if subprocess.run(["pgrep", "-f", "蜂群驾驶.py"], capture_output=True).returncode == 0:
        sys.exit("!! 驱动在跑，钩子拒绝执行（铁律 4）")
    行, 坏 = 核对()
    print(f"[{时刻()}] 首次核对：不合格文件 {sorted(坏) or '无'}"); [print("  " + l) for l in 行 if l.strip().startswith(("✗", "△", "结果模板核对"))]
    if not 坏: sys.exit(0)
    for n in sorted(坏):
        任务 = SCR / f"任务_回炉_结果文件_问题{n}.md"; 任务.write_text(任务文本(n, 坏[n], 行), encoding="utf-8")
        print(f"[{时刻()}] 派 建模师 回炉_结果文件_问题{n}（任务 {任务.name}）", flush=True)
        r = subprocess.run([sys.executable, str(SCR / "补腿.py"), "建模师.md", f"回炉_结果文件_问题{n}", str(任务), "--上限秒=1500"], cwd=PROJ, text=True, capture_output=True, timeout=1800)
        print((r.stdout or "").strip()[-1200:]); print((r.stderr or "").strip()[-600:], file=sys.stderr)
    行, 坏 = 核对()
    print(f"[{时刻()}] 复核：不合格文件 {sorted(坏) or '无'}"); [print("  " + l) for l in 行 if l.strip().startswith(("✗", "△", "结果模板核对"))]
    sys.exit(1 if 坏 else 0)
