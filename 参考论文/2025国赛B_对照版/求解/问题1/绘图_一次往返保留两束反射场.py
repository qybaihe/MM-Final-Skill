"""图05：用本机 XeLaTeX 和 Ghostscript 绘制一次往返光路。"""

import json
import math
from pathlib import Path
import subprocess
import time


ROOT = Path(__file__).resolve().parents[2]
TIME_BUDGET_SECONDS = 180


def main():
    started = time.monotonic()
    upper_height = 3.4
    lower_height = 1.6
    surface = (4.0, upper_height)
    interface = (4.675, lower_height)
    exit_point = (5.35, upper_height)
    incoming = (2.65, 5.1)
    reflected = (5.35, 5.1)
    outgoing = (6.7, 5.1)
    outer_angle = math.degrees(math.atan2(1.35, 1.7))
    inner_angle = math.degrees(math.atan2(0.675, upper_height - lower_height))
    geometry = {
        "角度用途": "图中角度展示光路几何，不代指实测入射角或材料参数",
        "入射端点": incoming,
        "表面入射点": surface,
        "表面反射端点": reflected,
        "下界面反射点": interface,
        "上界面出射点": exit_point,
        "返回出射端点": outgoing,
        "外角_度": outer_angle,
        "内角_度": inner_angle,
        "厚度标记纵向端点": [lower_height, upper_height],
        "表面反射对称偏差": abs(incoming[0] + reflected[0] - 2 * surface[0]),
        "出射与表面反射方向偏差": abs((outgoing[0] - exit_point[0]) - (reflected[0] - surface[0])),
        "层内往返对称偏差": abs(surface[0] + exit_point[0] - 2 * interface[0]),
    }
    for key in ["表面反射对称偏差", "出射与表面反射方向偏差", "层内往返对称偏差"]:
        if geometry[key] > 1e-12:
            raise ValueError(f"光路几何不一致：{key}")
    results = ROOT / "求解/问题1/结果"
    results.mkdir(parents=True, exist_ok=True)
    (results / "光路几何核验.json").write_text(json.dumps(geometry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    tex = r"""\documentclass[tikz,border=3pt]{standalone}
\usepackage[fontset=fandol]{ctex}
\setCJKmainfont{FandolHei-Regular.otf}
\usetikzlibrary{arrows.meta}
\definecolor{incident}{HTML}{2F6F9F}
\definecolor{surfacecolor}{HTML}{C44E52}
\definecolor{layercolor}{HTML}{7B5E32}
\definecolor{returncolor}{HTML}{2D8A65}
\definecolor{normalcolor}{HTML}{788591}
\begin{document}
\begin{tikzpicture}[x=1.5cm,y=1.5cm,font=\large,
  ray/.style={-{Stealth[length=3mm]},line width=1.6pt},
  normal/.style={dashed,line width=1.5pt,normalcolor}]
\path[use as bounding box] (0.1,0) rectangle (9.9,5.6);
\fill[incident!6] (0.3,3.4) rectangle (9.7,5.5);
\fill[layercolor!6] (0.3,1.6) rectangle (9.7,3.4);
\fill[normalcolor!18] (0.3,0.85) rectangle (9.7,1.6);
\draw[line width=1.6pt,normalcolor] (0.3,3.4) -- (9.7,3.4);
\draw[line width=1.6pt,layercolor] (0.3,1.6) -- (9.7,1.6);
\node[anchor=west,incident] at (0.5,4.75) {空气（介质 0）};
\node[anchor=west,layercolor] at (0.5,2.85) {外延层（介质 1）};
\node[anchor=west,normalcolor!80!black] at (0.5,1.06) {衬底（介质 2）};
\draw[normal] (4,2.5) -- (4,5.0);
\draw[normal] (4.675,1.05) -- (4.675,2.5);
\draw[normal] (5.35,2.6) -- (5.35,4.7);
\node[anchor=east,font=\normalsize,normalcolor] at (3.85,4.99) {法线};
\draw[{Stealth[length=2mm]}-{Stealth[length=2mm]},line width=1.5pt,layercolor]
  (9,1.6) -- (9,3.4) node[midway,right] {$d$};
\draw[ray,incident] @incoming@ -- @surface@;
\draw[ray,surfacecolor] @surface@ -- @reflected@;
\draw[ray,layercolor] @surface@ -- @interface@;
\draw[ray,returncolor] @interface@ -- @exit@;
\draw[ray,returncolor] @exit@ -- @outgoing@;
\node[incident] at (2.03,4.12) {入射场 $E_0$};
\node[surfacecolor] at (5.5,5.3) {表面反射 $E_s=r_{01}E_0$};
\node[layercolor] at (3.25,2.0) {透射至界面};
\node[returncolor] at (7.7,4.68) {一次往返后出射 $E_1$};
\node[returncolor] at (7.7,4.24) {$E_1=t_{01}t_{10}r_{12}zE_0$};
\node[layercolor] at (5.1,1.83) {$r_{12}$};
\draw[incident,line width=1.5pt] (4,3.975)
  arc[start angle=90,end angle=@outer_end@,radius=0.575];
\node[incident] at (3.62,4.12) {$\theta$};
\draw[layercolor,line width=1.5pt] (4,2.775)
  arc[start angle=270,end angle=@inner_end@,radius=0.625];
\node[layercolor] at (3.75,2.63) {$\theta_1$};
\draw[layercolor,line width=1.5pt] (4.675,2.1)
  arc[start angle=90,end angle=@bottom_end@,radius=0.5];
\node[layercolor] at (4.25,2.23) {$\theta_1$};
\draw[returncolor,line width=1.5pt] (5.35,3.975)
  arc[start angle=90,end angle=@exit_end@,radius=0.575];
\node[returncolor] at (5.62,4.12) {$\theta$};
\draw[rounded corners=3pt,fill=returncolor!8,draw=returncolor,line width=1.5pt]
  (0.3,0.03) rectangle (9.7,0.71);
\node[returncolor!75!black,font=\normalsize] at (5,0.49)
  {问题一模型：$E=E_s+E_1$；只保留表面反射与一次往返返回场};
\node[returncolor!75!black,font=\normalsize] at (5,0.19) {不引入多次往返分母};
\end{tikzpicture}
\end{document}
"""
    replacements = {
        "incoming": incoming, "surface": surface, "reflected": reflected,
        "interface": interface, "exit": exit_point, "outgoing": outgoing,
        "outer_end": 90 + outer_angle, "inner_end": 270 + inner_angle,
        "bottom_end": 90 + inner_angle, "exit_end": 90 - outer_angle,
    }
    for key, value in replacements.items():
        tex = tex.replace(f"@{key}@", str(value))
    workdir = ROOT / "日志/章修订1_5核验/光路排版"
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "ray.tex").write_text(tex, encoding="utf-8")
    output = ROOT / "求解/问题1/图片/一次往返保留两束反射场.png"
    pending = workdir / "ray.png"
    commands = [
        ["xelatex", "-interaction=nonstopmode", "-halt-on-error", "ray.tex"],
        ["gs", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=png16m", "-r200",
         "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4", f"-sOutputFile={pending}", "ray.pdf"],
    ]
    for command in commands:
        remaining = TIME_BUDGET_SECONDS - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("绘图时间预算耗尽，保留已写出的几何核验结果")
        with (workdir / f"{command[0]}.log").open("w", encoding="utf-8") as logfile:
            subprocess.run(command, cwd=workdir, check=True, timeout=remaining, stdout=logfile, stderr=subprocess.STDOUT)
    pending.replace(output)
    print("光路核验：两界面法线垂直；表面反射对称；返回光进入空气且与表面反射平行；厚度仅跨外延层。")
    print(f"已保存：{output}；耗时 {time.monotonic() - started:.2f} 秒")


if __name__ == "__main__":
    main()
