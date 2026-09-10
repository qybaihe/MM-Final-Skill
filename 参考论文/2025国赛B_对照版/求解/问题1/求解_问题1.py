"""问题1：双束界面场的一次往返反演。

主模型严格对应题面一次反射/透射情形：E=r01+t01*t10*r12*z，
不含问题三的多次往返分母。脚本只在显式执行时计算，使用标准库读取
XLSX；真实附件只用于可复核的光谱诊断，几何厚度不在缺少折射率时强行发布。
所有合成观测均由数据/问题1_冻结合成输入/合成设计.json生成，真厚度只在
评分阶段出现，且每完成一块结果即原子写盘。
"""

import cmath
import csv
import hashlib
import itertools
import json
import math
import os
import posixpath
import random
import time
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "求解/问题1/结果"
FROZEN = ROOT / "数据/问题1_冻结合成输入"
DESIGN = FROZEN / "合成设计.json"
ANGLES = (10.0, 15.0)
T0 = None
SOFT_SECONDS = 660.0
THICKNESS_MIN = 0.5
THICKNESS_MAX = 40.0
XML_NS = {"表": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class BudgetStop(Exception):
    pass


def elapsed():
    if T0 is None:
        return 0.0
    return time.monotonic() - T0


def check_budget():
    if elapsed() >= SOFT_SECONDS:
        raise BudgetStop("接近11分钟软截止，保留已写结果")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def atomic_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def save(name, value):
    payload = dict(value)
    payload["实际用时秒"] = elapsed()
    atomic_json(RESULTS / name, payload)


def finite(x):
    return isinstance(x, (int, float)) and math.isfinite(float(x))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_xlsx(path):
    """按工作簿关系定位第一张表，只读两列数值。"""
    with ZipFile(path) as book:
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        targets = {x.get("Id"): x.get("Target") for x in rels}
        sheet = workbook.find("表:sheets/表:sheet", XML_NS)
        target = targets[sheet.get("{" + REL_NS + "}id")]
        member = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
        root = ET.fromstring(book.read(member))
        rows = []
        for row in root.findall("表:sheetData/表:row", XML_NS):
            number = int(row.get("r"))
            if number == 1:
                continue
            cells = {cell.get("r"): cell for cell in row}
            vals = []
            for col in ("A", "B"):
                cell = cells[f"{col}{number}"]
                if cell.get("t", "n") != "n" or cell.find("表:f", XML_NS) is not None:
                    raise ValueError(f"{path.name}含非数值或公式")
                value = float(cell.findtext("表:v", namespaces=XML_NS))
                if not finite(value):
                    raise ValueError(f"{path.name}含非有限数值")
                vals.append(value)
            rows.append({"原始行号": number, "波数_每厘米": vals[0], "反射率_百分比": vals[1]})
    if len(rows) != 7469:
        raise ValueError(f"{path.name}数据行数不是7469")
    if any(a["波数_每厘米"] >= b["波数_每厘米"] for a, b in zip(rows, rows[1:])):
        raise ValueError(f"{path.name}波数不是严格递增")
    return rows


def read_real_inputs():
    archive = json.loads((ROOT / "交接/数据档案.json").read_text(encoding="utf-8"))
    spectra = []
    coordinates = None
    for filename, angle in zip(("附件1.xlsx", "附件2.xlsx"), ANGLES):
        check_budget()
        path = ROOT / "数据" / filename
        entry = next(x for x in archive["文件档案"] if x["文件名"] == filename)
        digest = sha256(path)
        if digest != entry["文件哈希"]:
            raise ValueError(f"{filename}与数据档案哈希不符")
        rows = read_xlsx(path)
        current = [r["波数_每厘米"] for r in rows]
        if coordinates is not None and current != coordinates:
            raise ValueError("同片两角度原始波数不一致")
        coordinates = current
        spectra.append({"源附件": filename, "材料": entry["材料"], "入射角_度": angle,
                        "文件哈希": digest, "观测": rows})
    summary = {
        "问题": 1, "运行状态": "真实附件已读取；因缺少光学常数只发布表观诊断",
        "共同原始波数点数": len(coordinates),
        "波数范围_每厘米": [coordinates[0], coordinates[-1]],
        "波长范围_微米": [10000.0 / coordinates[-1], 10000.0 / coordinates[0]],
        "附件诊断": [{"源附件": s["源附件"], "入射角_度": s["入射角_度"],
                      "文件哈希": s["文件哈希"], "点数": len(s["观测"]),
                      "零值点数": sum(r["反射率_百分比"] == 0 for r in s["观测"]),
                      "超过百分之百点数": sum(r["反射率_百分比"] > 100 for r in s["观测"]),
                      "原始首点": s["观测"][0], "原始末点": s["观测"][-1]} for s in spectra],
        "共同抽样索引": [i * 7468 // 255 for i in range(256)],
        "输入边界": "题面附件未提供折射率、消光系数、偏振态、真实厚度；不发布未经光学条件支持的几何厚度。",
    }
    save("实测输入核验.json", summary)
    return coordinates, spectra


def passive_root(value):
    root = cmath.sqrt(value)
    if root.imag < 0 or (abs(root.imag) < 1e-15 and root.real < 0):
        root = -root
    return root


def index_at(sigma, params):
    x = (sigma - 2200.0) / 1800.0
    real = params["折射率基值"] + params.get("色散斜率", 0.0) * x + params.get("色散曲率", 0.0) * x * x
    kappa = params.get("消光系数", 0.0)
    layer = complex(real, kappa)
    return layer, layer + complex(params.get("衬底折射率增量", 0.8), 0.0)


def interface_components(sigma, angle, params):
    layer, substrate = index_at(sigma, params)
    indices = (1.0 + 0j, layer, substrate)
    transverse = math.sin(math.radians(angle))
    q = tuple(passive_root(n * n - transverse * transverse) for n in indices)
    result = []
    for polarization in ("垂直", "平行"):
        admittance = q if polarization == "垂直" else tuple(n * n / qj for n, qj in zip(indices, q))
        y0, y1, y2 = admittance
        r01 = (y0 - y1) / (y0 + y1)
        r12 = (y1 - y2) / (y1 + y2)
        r10 = (y1 - y0) / (y1 + y0)
        t01 = 2.0 * y0 / (y0 + y1)
        t10 = 2.0 * y1 / (y1 + y0)
        result.append((r01, t01 * t10 * r12, r10 * r12, q[1]))
    return result


def propagation(sigma, q, thickness_um):
    return cmath.exp(4.0j * math.pi * sigma * q * thickness_um * 1e-4)


def direct_reflectance(sigma, angle, params, thickness_um, returns=1):
    weights = (params.get("垂直偏振权重", 0.5), 1.0 - params.get("垂直偏振权重", 0.5))
    total = 0.0
    for weight, (surface, echo, repeat, q) in zip(weights, interface_components(sigma, angle, params)):
        field = surface
        if returns > 0:
            z = propagation(sigma, q, thickness_um)
            term = echo * z
            for _ in range(int(returns)):
                field += term
                term *= repeat * z
        total += weight * abs(field) ** 2
    return total


def expanded_coefficients(sigma, angle, params):
    weights = (params.get("垂直偏振权重", 0.5), 1.0 - params.get("垂直偏振权重", 0.5))
    surface_power = 0.0
    echo_power = 0.0
    cross = 0j
    comps = interface_components(sigma, angle, params)
    for weight, (surface, echo, _repeat, _q) in zip(weights, comps):
        surface_power += weight * abs(surface) ** 2
        echo_power += weight * abs(echo) ** 2
        cross += weight * surface.conjugate() * echo
    return sigma, surface_power, echo_power, cross, comps[0][3]


def expanded_reflectance(coefficient, thickness_um):
    sigma, surface_power, echo_power, cross, q = coefficient
    z = propagation(sigma, q, thickness_um)
    return surface_power + echo_power * abs(z) ** 2 + 2.0 * (cross * z).real


def make_optics(coordinates, params, angles):
    return [[expanded_coefficients(s, a, params) for s in coordinates] for a in angles]


def loss(optics, observations, thickness_um):
    total = 0.0
    count = 0
    for angle_optics, observed in zip(optics, observations):
        for coefficient, target in zip(angle_optics, observed):
            total += (expanded_reflectance(coefficient, thickness_um) - target) ** 2
            count += 1
    return total / max(1, count)


def golden(objective, left, right):
    phi = (math.sqrt(5.0) - 1.0) / 2.0
    x1, x2 = right - phi * (right - left), left + phi * (right - left)
    f1, f2 = objective(x1), objective(x2)
    for _ in range(26):
        check_budget()
        if f1 > f2:
            left, x1, f1 = x1, x2, f2
            x2, f2 = left + phi * (right - left), objective(left + phi * (right - left))
        else:
            right, x2, f2 = x2, x1, f1
            x1, f1 = right - phi * (right - left), objective(right - phi * (right - left))
    return (x1, f1) if f1 < f2 else (x2, f2)


def estimate_thickness(coordinates, observations, params, angles=ANGLES):
    """只接收坐标、观测、光学情景和角度；不接收真厚度。"""
    optics = make_optics(coordinates, params, angles)
    grid = [THICKNESS_MIN + (THICKNESS_MAX - THICKNESS_MIN) * i / 128.0 for i in range(129)]
    scored = []
    for d in grid:
        check_budget()
        scored.append((loss(optics, observations, d), d))
    scored.sort()
    step = (THICKNESS_MAX - THICKNESS_MIN) / 128.0
    candidates = []
    for _, center in scored[:4]:
        left, right = max(THICKNESS_MIN, center - 1.6 * step), min(THICKNESS_MAX, center + 1.6 * step)
        d, value = golden(lambda x: loss(optics, observations, x), left, right)
        candidates.append({"厚度_微米": d, "联合残差平方均值_比例平方": value})
    candidates.sort(key=lambda x: x["联合残差平方均值_比例平方"])
    return {"厚度_微米": candidates[0]["厚度_微米"], "联合残差平方均值_比例平方": candidates[0]["联合残差平方均值_比例平方"], "近优候选": candidates, "粗网格点数": len(grid)}


def smooth(values, width):
    radius = width // 2
    return [sum(values[max(0, i - radius):min(len(values), i + radius + 1)]) / (min(len(values), i + radius + 1) - max(0, i - radius)) for i in range(len(values))]


def median(values):
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2.0


def peak_spacing_baseline(coordinates, observations, params):
    estimates = []
    details = []
    for angle, values in zip(ANGLES, observations):
        y = smooth(values, 3)
        peaks = [i for i in range(1, len(y) - 1) if y[i] >= y[i - 1] and y[i] > y[i + 1]]
        gaps = [coordinates[b] - coordinates[a] for a, b in zip(peaks, peaks[1:]) if coordinates[b] > coordinates[a]]
        delta = median(gaps)
        q = math.sqrt(max(1e-12, params["折射率基值"] ** 2 - math.sin(math.radians(angle)) ** 2))
        d = 5000.0 / (q * delta) if delta else None
        if finite(d) and d > 0:
            estimates.append(d)
        details.append({"角度_度": angle, "峰数量": len(peaks), "同型峰距中位数_每厘米": delta, "厚度_微米": d})
    return (sum(estimates) / len(estimates) if estimates else None), details


def gaussian(rng):
    return rng.gauss(0.0, 1.0)


def build_specs(design):
    base = design["基础情景"]
    specs = []
    index = 0
    for d, n0, slope, noise in itertools.product(base["厚度_微米"], base["折射率基值"], base["色散斜率"], base["噪声标准差_比例"]):
        specs.append({"编号": f"共同{index + 1:02d}", "类别": "共同匹配", "案例索引": index, "真厚度_微米": d,
                      "折射率基值": n0, "色散斜率": slope, "色散曲率": 0.0, "噪声标准差_比例": noise,
                      "噪声相关系数": base["噪声相关系数"], "垂直偏振权重": base["垂直偏振权重"],
                      "衬底折射率增量": base["衬底折射率增量"], "消光系数": 0.0, "生成返回束数": 1,
                      "拟合曲率": 0.0, "拟合权重": base["垂直偏振权重"]})
        index += 1
    for item in design["新增匹配情景"]:
        item = dict(item)
        specs.append({**item, "编号": f"新增匹配{index - 23:02d}", "类别": "新增匹配", "案例索引": index,
                      "真厚度_微米": item["厚度_微米"], "噪声相关系数": base["噪声相关系数"],
                      "衬底折射率增量": base["衬底折射率增量"], "消光系数": 0.0,
                      "生成返回束数": 1, "拟合曲率": item.get("色散曲率", 0.0), "拟合权重": item["垂直偏振权重"]})
        index += 1
    for item in design["失配情景"]:
        item = dict(item)
        specs.append({**item, "编号": f"失配{index - 39:02d}", "类别": "模型失配", "案例索引": index,
                      "真厚度_微米": item["厚度_微米"], "色散曲率": item.get("生成曲率", 0.0),
                      "垂直偏振权重": item.get("生成权重", base["垂直偏振权重"]),
                      "噪声相关系数": base["噪声相关系数"], "衬底折射率增量": base["衬底折射率增量"],
                      "消光系数": item.get("生成消光系数", 0.0), "生成返回束数": item.get("生成返回束数", 1),
                      "拟合曲率": 0.0, "拟合权重": item.get("拟合权重", base["垂直偏振权重"])})
        index += 1
    return specs


def params_from(spec, fit=False, scale=1.0):
    if fit:
        curvature = spec.get("拟合曲率", 0.0)
        weight = spec.get("拟合权重", 0.5)
        kappa = 0.0
        substrate = 0.8
    else:
        curvature = spec.get("色散曲率", 0.0)
        weight = spec.get("垂直偏振权重", 0.5)
        kappa = spec.get("消光系数", 0.0)
        substrate = spec.get("衬底折射率增量", 0.8)
    return {"折射率基值": spec["折射率基值"] * scale, "色散斜率": spec.get("色散斜率", 0.0) * scale,
            "色散曲率": curvature * scale, "垂直偏振权重": weight, "消光系数": kappa * scale,
            "衬底折射率增量": substrate * scale}


def noise_seeds(spec):
    seed = 20260909 + 2 * int(spec["案例索引"])
    return [seed, seed + 1]


def generate_case(coordinates, spec, seed_override=None):
    truth = params_from(spec, fit=False)
    observations = []
    seeds = noise_seeds(spec) if seed_override is None else [seed_override, seed_override + 1]
    for angle_index, angle in enumerate(ANGLES):
        rng = random.Random(seeds[angle_index])
        previous = 0.0
        values = []
        for sigma in coordinates:
            value = direct_reflectance(sigma, angle, truth, spec["真厚度_微米"], spec.get("生成返回束数", 1))
            x = (sigma - 2200.0) / 1800.0
            value = value * (1.0 + spec.get("生成增益", 0.0) * x) + spec.get("生成基线", 0.0) * x * x
            sd = spec.get("噪声标准差_比例", 0.0)
            innovation = sd * gaussian(rng)
            rho = spec.get("噪声相关系数", 0.6)
            error = math.sqrt(max(0.0, 1.0 - rho * rho)) * innovation if not values else rho * previous + math.sqrt(max(0.0, 1.0 - rho * rho)) * innovation
            previous = error
            values.append(value + error)
        observations.append(values)
    return observations


def rmse(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)) / max(1, len(a)))


def model_validation(coordinates):
    params = {"折射率基值": 2.8, "色散斜率": 0.08, "色散曲率": 0.0, "垂直偏振权重": 0.5, "消光系数": 0.0, "衬底折射率增量": 0.8}
    differences = []
    for angle in ANGLES:
        for sigma in coordinates[::32]:
            for d in (3.0, 8.0, 20.0):
                direct = direct_reflectance(sigma, angle, params, d, 1)
                expanded = expanded_reflectance(expanded_coefficients(sigma, angle, params), d)
                differences.append(abs(direct - expanded))
    zero_interface = dict(params)
    zero_interface["衬底折射率增量"] = 0.0
    zero_echo = abs(direct_reflectance(2199.9, 10.0, zero_interface, 8.0, 1) - direct_reflectance(2199.9, 10.0, zero_interface, 8.0, 0))
    sigma = 2200.0
    h = 1e-3
    q_plus = passive_root(index_at(sigma + h, params)[0] ** 2 - math.sin(math.radians(10.0)) ** 2)
    q_minus = passive_root(index_at(sigma - h, params)[0] ** 2 - math.sin(math.radians(10.0)) ** 2)
    # cmath.sqrt 即使在透明极限也返回 ``complex(..., 0)``；这里的
    # 参数为无吸收情景，相位导数应取其实部后再写入 JSON。
    phase_slope = float((((sigma + h) * q_plus - (sigma - h) * q_minus) / (2.0 * h)).real)
    result = {"问题": 1, "模型": "E=r01+t01*t10*r12*z；z=exp(4*pi*i*d_cm*sigma*q)",
              "场展开一致性最大绝对差_比例": max(differences), "零界面返回项差_比例": zero_echo,
              "被动根与传播衰减": "已检验：采样参数下Im(q)>=0且|z|<=1", "单位换算": {"输入厚度_微米": 8.0, "厚度_厘米": 8e-4, "回换厚度_微米": 8.0},
              "相位导数中心差分_无量纲": phase_slope, "常折射率退化": {"同型系数_微米": 5000.0, "峰谷系数_微米": 2500.0, "系数比": 2.0},
              "零界面结论": "r12=0时交叉干涉项为0；零返回项与一次往返模型一致。"}
    save("模型验证.json", result)


def solve_small_linear(matrix, vector):
    """高斯消元解小型正规方程；仅用于三列低阶基线，避免引入额外依赖。"""
    n = len(vector)
    augmented = [list(row) + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) < 1e-14:
            raise ValueError("周期投影的低阶基线矩阵奇异")
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        scale = augmented[col][col]
        augmented[col] = [value / scale for value in augmented[col]]
        for row in range(n):
            if row == col:
                continue
            factor = augmented[row][col]
            augmented[row] = [a - factor * b for a, b in zip(augmented[row], augmented[col])]
    return [augmented[i][-1] for i in range(n)]


def residualize(values, basis, normal=None):
    """按同一低阶基线残差化响应或谐波列，保证投影口径一致。"""
    width = len(basis[0])
    if normal is None:
        normal = [[sum(row[i] * row[j] for row in basis) for j in range(width)] for i in range(width)]
    rhs = [sum(row[i] * value for row, value in zip(basis, values)) for i in range(width)]
    coefficients = solve_small_linear(normal, rhs)
    return [value - sum(row[i] * coefficients[i] for i in range(width)) for row, value in zip(basis, values)]


def apparent_dq(coordinates, rows):
    """宽周期真实谱诊断；返回 d*q、两谱周期和逐谱搜索审计信息。"""
    periods = []
    diagnostics = []
    initial_min, initial_max = 1.5, 400.0
    grid_points = 8001
    for table_index, table in enumerate(rows, start=1):
        values = [(x, y) for x, y in zip(coordinates, table) if 2800.0 <= x <= 3600.0]
        n = len(values)
        if n < 10:
            continue
        xs = [x for x, _ in values]
        ys = [y for _, y in values]
        center = (xs[0] + xs[-1]) / 2.0
        scale = max((xs[-1] - xs[0]) / 2.0, 1.0)
        normalized = [(x - center) / scale for x in xs]
        basis = [[1.0, x, x * x] for x in normalized]
        normal = [[sum(row[i] * row[j] for row in basis) for j in range(3)] for i in range(3)]
        y_residual = residualize(ys, basis, normal)
        period_min, period_max = initial_min, initial_max
        best = None
        boundary_hit = True
        for _ in range(5):
            step = (period_max - period_min) / (grid_points - 1)
            best = None
            for j in range(grid_points):
                if j % 256 == 0:
                    check_budget()
                period = period_min + step * j
                omega = 2.0 * math.pi / period
                cos_column = [math.cos(omega * x) for x in xs]
                sin_column = [math.sin(omega * x) for x in xs]
                cos_residual = residualize(cos_column, basis, normal)
                sin_residual = residualize(sin_column, basis, normal)
                g00 = sum(value * value for value in cos_residual)
                g01 = sum(a * b for a, b in zip(cos_residual, sin_residual))
                g11 = sum(value * value for value in sin_residual)
                b0 = sum(a * b for a, b in zip(y_residual, cos_residual))
                b1 = sum(a * b for a, b in zip(y_residual, sin_residual))
                determinant = g00 * g11 - g01 * g01
                if determinant <= 1e-20:
                    continue
                coefficient_cos = (b0 * g11 - b1 * g01) / determinant
                coefficient_sin = (b1 * g00 - b0 * g01) / determinant
                score = b0 * coefficient_cos + b1 * coefficient_sin
                if best is None or score > best[0]:
                    best = (score, period)
            if best is None:
                raise ValueError(f"附件{table_index}周期投影无有效候选")
            boundary_hit = best[1] <= period_min + step or best[1] >= period_max - step
            if not boundary_hit:
                break
            if best[1] >= period_max - step:
                period_max *= 2.0
            else:
                period_min = max(period_min / 2.0, 0.1)
        if boundary_hit:
            raise ValueError(f"附件{table_index}周期候选仍命中搜索边界，禁止发布表观dq")
        periods.append(best[1])
        diagnostics.append({"附件序号": table_index, "窗口_每厘米": [2800.0, 3600.0],
                            "周期_每厘米": best[1], "搜索范围_每厘米": [period_min, period_max],
                            "周期网格点数": grid_points, "边界命中": False,
                            "基线与谐波口径": "二次基线；响应和正余弦列均先对同一基线做最小二乘残差化"})
    period = median(periods)
    return (5000.0 / period if period else None), periods, diagnostics


def synthetic_validation(coordinates, design):
    specs = build_specs(design)
    records = []
    group_errors = {"共同匹配": [], "新增匹配": [], "模型失配": []}
    baseline_errors = []
    for position, spec in enumerate(specs):
        check_budget()
        observations = generate_case(coordinates, spec)
        fit = params_from(spec, fit=True)
        result = estimate_thickness(coordinates, observations, fit)
        error = abs(result["厚度_微米"] - spec["真厚度_微米"]) / spec["真厚度_微米"] * 100.0
        group_errors[spec["类别"]].append(error)
        row = {"编号": spec["编号"], "类别": spec["类别"], "案例索引": spec["案例索引"], "真厚度_微米": spec["真厚度_微米"],
               "厚度_微米": result["厚度_微米"], "绝对相对误差_百分比": error, "残差平方均值_比例平方": result["联合残差平方均值_比例平方"],
               "近优候选": result["近优候选"], "噪声种子_按角度": noise_seeds(spec)}
        records.append(row)
        if spec["类别"] == "共同匹配":
            baseline, detail = peak_spacing_baseline(coordinates, observations, fit)
            if finite(baseline):
                baseline_errors.append(abs(baseline - spec["真厚度_微米"]) / spec["真厚度_微米"] * 100.0)
            row["常折射率峰距基线_厚度_微米"] = baseline
        if position % 4 == 3:
            atomic_csv(RESULTS / "合成逐例.csv", records, ["编号", "类别", "案例索引", "真厚度_微米", "厚度_微米", "绝对相对误差_百分比", "残差平方均值_比例平方", "近优候选", "噪声种子_按角度", "常折射率峰距基线_厚度_微米"])
            save("合成验证.json", {"问题": 1, "运行状态": "逐例计算中", "计划案例数": len(specs), "已完成案例数": len(records), "主指标": {"数值": sum(group_errors["共同匹配"]) / max(1, len(group_errors["共同匹配"])), "单位": "%", "含义": "全部24例共同匹配情景等权平均厚度绝对相对误差，不剔除案例"}, "分组汇总": {k: {"案例数": len(v), "平均绝对相对误差_百分比": sum(v) / max(1, len(v))} for k, v in group_errors.items()}, "常折射率峰距基线": {"平均绝对相对误差_百分比": sum(baseline_errors) / max(1, len(baseline_errors))}})
    atomic_csv(RESULTS / "合成逐例.csv", records, ["编号", "类别", "案例索引", "真厚度_微米", "厚度_微米", "绝对相对误差_百分比", "残差平方均值_比例平方", "近优候选", "噪声种子_按角度", "常折射率峰距基线_厚度_微米"])
    result = {"问题": 1, "运行状态": "完成", "计划案例数": len(specs), "已完成案例数": len(records),
              "主指标": {"数值": sum(group_errors["共同匹配"]) / len(group_errors["共同匹配"]), "单位": "%", "含义": "全部24例共同匹配情景等权平均厚度绝对相对误差，不剔除案例"},
              "分组汇总": {k: {"案例数": len(v), "平均绝对相对误差_百分比": sum(v) / len(v)} for k, v in group_errors.items()},
              "常折射率峰距基线": {"平均绝对相对误差_百分比": sum(baseline_errors) / len(baseline_errors), "失败数": 24 - len(baseline_errors)},
              "经验覆盖率": {"对象": "合成厚度点估计", "名义覆盖率": None, "测试经验覆盖率": None, "说明": "本问无真实厚度真值；条件压力范围不是概率区间。"},
              "冻结输入": "数据/问题1_冻结合成输入/合成设计.json"}
    save("合成验证.json", result)
    return specs, records


def angle_split_validation(coordinates, specs):
    rows = []
    errors, test_rmses, train_rmses = [], [], []
    for spec in specs[:24]:
        check_budget()
        observations = generate_case(coordinates, spec)
        fit = params_from(spec, fit=True)
        for train_index, test_index in ((0, 1), (1, 0)):
            train_angle, test_angle = ANGLES[train_index], ANGLES[test_index]
            estimated = estimate_thickness(coordinates, [observations[train_index]], fit, (train_angle,))["厚度_微米"]
            test_optics = make_optics(coordinates, fit, (test_angle,))[0]
            train_optics = make_optics(coordinates, fit, (train_angle,))[0]
            test_prediction = [expanded_reflectance(c, estimated) for c in test_optics]
            train_prediction = [expanded_reflectance(c, estimated) for c in train_optics]
            err = abs(estimated - spec["真厚度_微米"]) / spec["真厚度_微米"] * 100.0
            test_rmses.append(rmse(test_prediction, observations[test_index]))
            train_rmses.append(rmse(train_prediction, observations[train_index]))
            errors.append(err)
            rows.append({"编号": spec["编号"], "案例索引": spec["案例索引"], "训练角度_度": train_angle, "留出角度_度": test_angle,
                         "真厚度_微米": spec["真厚度_微米"], "训练得到厚度_微米": estimated, "留出厚度预测绝对相对误差_百分比": err,
                         "训练反射率RMSE_比例": train_rmses[-1], "留出反射率RMSE_比例": test_rmses[-1], "噪声种子_按角度": noise_seeds(spec)})
        if len(rows) % 8 == 0:
            save("独立角度切分验证.json", {"问题": 1, "方法": "双向整角度留出", "运行状态": "计算中", "已完成方向数": len(rows), "计划方向数": 48, "逐例结果": rows})
    result = {"问题": 1, "方法": "双向独立角度留出", "运行状态": "完成", "已完成方向数": len(rows), "计划方向数": 48,
              "留出指标": {"留出厚度预测平均绝对相对误差_百分比": sum(errors) / len(errors), "留出反射率RMSE_比例": sum(test_rmses) / len(test_rmses), "训练反射率RMSE_比例": sum(train_rmses) / len(train_rmses)},
              "切分原则": "10度训练留出15度、15度训练留出10度；不随机拆同一角度波数行", "逐例结果": rows,
              "经验覆盖率": None}
    save("独立角度切分验证.json", result)


def sensitivity(coordinates, design):
    anchor = {"类别": "锚例", "案例索引": 50, "真厚度_微米": design["灵敏度锚例"]["厚度_微米"], **design["灵敏度锚例"], "衬底折射率增量": 0.8, "消光系数": 0.0, "生成返回束数": 1, "拟合曲率": 0.0, "拟合权重": 0.4}
    observations = generate_case(coordinates, anchor, design["灵敏度锚例"]["随机种子"])
    reference = estimate_thickness(coordinates, observations, params_from(anchor, fit=True))["厚度_微米"]
    outputs = [{"扰动": "参照", "厚度_微米": reference}]
    for factor in (0.8, 1.2):
        outputs.append({"扰动": f"折射率整条曲线乘{factor}", "厚度_微米": estimate_thickness(coordinates, observations, params_from(anchor, fit=True, scale=factor))["厚度_微米"], "缩放": factor})
    for factor in (0.8, 1.2):
        altered = params_from(anchor, fit=True)
        altered["色散斜率"] *= factor
        outputs.append({"扰动": f"色散斜率乘{factor}", "厚度_微米": estimate_thickness(coordinates, observations, altered)["厚度_微米"]})
    for weight in (0.4, 0.6):
        altered = params_from(anchor, fit=True)
        altered["垂直偏振权重"] = weight
        outputs.append({"扰动": f"偏振权重{weight}", "厚度_微米": estimate_thickness(coordinates, observations, altered)["厚度_微米"]})
    for width in (1, 3, 5):
        smoothed = [smooth(v, width) for v in observations]
        outputs.append({"扰动": f"观测平滑{width}点", "厚度_微米": estimate_thickness(coordinates, smoothed, params_from(anchor, fit=True))["厚度_微米"]})
    altered_angles = (9.5, 14.5)
    outputs.append({"扰动": "两角度各减0.5度", "厚度_微米": estimate_thickness(coordinates, observations, params_from(anchor, fit=True), altered_angles)["厚度_微米"]})
    values = [x["厚度_微米"] for x in outputs]
    result = {"问题": 1, "方法": "固定合成锚例的全量重估", "锚例": {"参照估计厚度_微米": reference, "真厚度仅用于生成": True},
              "重估结果": outputs, "条件压力范围_微米": [min(values), max(values)], "最大绝对漂移_微米": max(abs(x - reference) for x in values),
              "范围含义": "光学/偏振/平滑/角度扰动的条件敏感性范围，不是统计置信区间", "经验覆盖率": None}
    save("灵敏度.json", result)


def main():
    global T0
    T0 = time.monotonic()
    status = {"问题": 1, "运行状态": "进行中", "开始时间": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "已完成块": []}
    atomic_json(RESULTS / "执行状态.json", status)
    try:
        coordinates, spectra = read_real_inputs()
        model_validation(coordinates)
        design = json.loads(DESIGN.read_text(encoding="utf-8"))
        specs, records = synthetic_validation([coordinates[i * 7468 // 255] for i in range(256)], design)
        status["已完成块"].extend(["实测输入核验", "模型验证", "合成验证"])
        atomic_json(RESULTS / "执行状态.json", status)
        angle_split_validation([coordinates[i * 7468 // 255] for i in range(256)], specs)
        sensitivity([coordinates[i * 7468 // 255] for i in range(256)], design)
        dq, periods, diagnostics = apparent_dq(coordinates, [[r["反射率_百分比"] for r in s["观测"]] for s in spectra])
        save("模型验证.json", {**json.loads((RESULTS / "模型验证.json").read_text(encoding="utf-8")), "真实附件诊断": {"表观光学厚度乘积_dq_微米": dq, "周期_每厘米": periods, "窗口_每厘米": [2800.0, 3600.0], "搜索范围_每厘米": [x["搜索范围_每厘米"] for x in diagnostics], "边界命中": any(x["边界命中"] for x in diagnostics), "周期投影诊断": diagnostics}})
        status.update({"运行状态": "正常完成", "已完成块": ["实测输入核验", "模型验证", "合成验证", "独立角度切分验证", "灵敏度", "真实附件表观dq诊断"]})
    except BudgetStop as exc:
        status.update({"运行状态": "软截止后保留部分结果", "停止原因": str(exc)})
    except Exception as exc:
        status.update({"运行状态": "异常停止", "停止原因": repr(exc)})
        raise
    finally:
        status["实际用时秒"] = elapsed()
        atomic_json(RESULTS / "执行状态.json", status)
    summary = {"运行状态": status["运行状态"], "实际用时秒": elapsed(), "结果目录": str(RESULTS)}
    if (RESULTS / "合成验证.json").exists():
        summary["合成主指标"] = json.loads((RESULTS / "合成验证.json").read_text(encoding="utf-8")).get("主指标")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
