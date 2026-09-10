"""双束界面场限时原型；仅在显式执行本文件时计算，不生成图表。"""

import time

PROCESS_STARTED = time.monotonic()

import cmath
import fcntl
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import posixpath
import random
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "求解/问题1/原型结果/路线2.json"
SCOUT = ROOT / "交接/路线侦察.json"
ARCHIVE = ROOT / "交接/数据档案.json"
ROUTE = "双束界面场反演"
METRIC = "合成厚度平均相对误差_百分比"
THICKNESS_GRID = tuple(0.5 + 39.5 * index / 95 for index in range(96))
NAMESPACE = {"表": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
RELATIONSHIP = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def initial_report():
    return {
        "问题": 1,
        "路线编号": "一乙",
        "路线名": ROUTE,
        "运行状态": "进行中",
        "核心指标": {METRIC: None},
        "核心指标键值": {METRIC: None},
        "主指标名称": "合成厚度平均相对误差",
        "主指标值": None,
        "主指标单位": "%",
        "指标含义": "100乘以全部24例厚度绝对相对误差的算术平均，越小越好；不删除难例。",
        "用时估计": {
            "性质": "设计上限，非实测速度",
            "读取与生成秒": 15,
            "前向粗扫秒": 85,
            "局部精修与核对秒": 50,
            "汇总预留秒": 15,
            "软截止秒": 165,
            "父进程截止秒": 175,
        },
        "口径说明": {
            "真数据用途": "读取附件1、2真实波数与反射率并保留抽样证据；评分只借用原始波数坐标。",
            "评分对象": "共同合成24例，每例10度、15度各256点；实测反射率不是合成真值。",
            "真实性边界": "附件缺厚度真值与光学参数，不能以本指标声称实测厚度准确率。",
            "模型": "每偏振只含表面反射和一次往返透射反射场；无多次往返分母。",
            "已知参数": "合成层与衬底折射率曲线固定，两偏振各半；不增加基线、增益或自由相位。",
            "搜索": "0.5至40微米均匀96点，最多三个粗网格局部候选；曲率证实凸的小括区间才用黄金分割。",
            "不确定性": "保留三个局部候选、离散损失与粗细差；这些不是统计置信区间。",
            "区间协议": "不构造预测或置信区间，因此经验覆盖率不适用。",
        },
        "输入哈希": {},
        "样本索引": [],
        "折分": "问题一为已知光学参数的合成还原；不套用问题二、三的实测留段评分。",
        "使用附件": [],
        "参数情景": [],
        "分块预测与残差": [],
        "厚度及单位": [],
        "经验覆盖率": None,
        "辅助诊断": {},
        "失败原因": [],
        "逐例结果": [],
        "已完成例数": 0,
        "实际用时秒": 0.0,
        "耗时秒": 0.0,
        "随机种子": 20260909,
    }


def checkpoint(report, started):
    elapsed = time.monotonic() - started
    report["实际用时秒"] = elapsed
    report["耗时秒"] = elapsed
    write_json(OUTPUT, report)


def append_execution_record(report, started):
    deadline = min(started + 173, time.monotonic() + 0.5)
    record_path = ROOT / "交接/实验记录.json"
    with (ROOT / "交接/.实验记录.lock").open("a", encoding="utf-8") as lock_file:
        while True:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    report["实验记录追加状态"] = "共享记录锁暂不可用，未覆盖其他记录"
                    return
                time.sleep(0.01)
        records = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else []
        if not isinstance(records, list):
            raise ValueError("实验记录必须为数组，不覆盖既有内容")
        value = report["主指标值"]
        if value is not None:
            entry = {
                "类别": "科学尝试", "问题": 1,
                "尝试": "固定合成折射率曲线及等权两偏振，用两角度各256点联合反演24例厚度。",
                "现象": f"24例均给出有限正厚度；厚度平均相对误差为{value:.12g}%，保留全部单例误差。",
                "决定": "保留两束界面表达式及各例局部候选；精度有限时降低可信程度，不删除困难样本。",
                "依据": "求解结果:核心指标/合成厚度平均相对误差_百分比；求解结果:逐例结果；求解结果:辅助诊断",
            }
        else:
            entry = {
                "类别": "流程事件", "问题": 1,
                "尝试": "执行路线2限时原型并逐例保存已算得的厚度。",
                "现象": f"形成{report['已完成例数']}例，未凑齐24例共同评分；状态为{report['运行状态']}。",
                "决定": "保留真实部分结果，不以部分均值冒充完整成绩。",
                "依据": "求解结果:已完成例数；求解结果:失败原因",
            }
        records.append(entry)
        write_json(record_path, records)
        report["实验记录追加状态"] = "已追加，保留原有数组元素"


def read_real_samples(report):
    dossier = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    protocol = json.loads(SCOUT.read_text(encoding="utf-8"))["共同原型协议"]
    settings = protocol["合成小样"]
    expected = ([3, 8, 20], [2.4, 3.4], [0, 0.08], [0, 0.001], [10, 15])
    actual = tuple(settings[key] for key in (
        "厚度情景微米", "折射率基值情景", "色散斜率情景", "噪声标准差情景", "角度度"
    ))
    if actual != expected or settings["组合数"] != 24 or settings["随机种子"] != 20260909:
        raise ValueError("共同合成协议已变更，不能沿用旧的评分口径")
    if dossier["总览"]["四文件共有波数点数"] != 7469:
        raise ValueError("权威波数总数改变，不能悄然改变共同样本")
    selection = [index * 7468 // 255 for index in range(256)]
    report["样本索引"] = selection
    report["输入哈希"] = {
        "路线设计": hashlib.sha256(SCOUT.read_bytes()).hexdigest(),
        "数据档案": hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
    }
    provenance = []
    coordinates = None
    for attachment, angle in (("附件1.xlsx", 10), ("附件2.xlsx", 15)):
        path = ROOT / "数据" / attachment
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = next(item for item in dossier["文件档案"] if item["文件名"] == attachment)
        if digest != manifest["文件哈希"]:
            raise ValueError(f"{attachment}与数据档案哈希不符")
        with ZipFile(path) as workbook:
            book = ElementTree.fromstring(workbook.read("xl/workbook.xml"))
            relations = ElementTree.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
            targets = {item.get("Id"): item.get("Target") for item in relations}
            sheet = next(item for item in book.findall("表:sheets/表:sheet", NAMESPACE)
                         if item.get("name") == "Sheet1")
            target = targets[sheet.get(f"{{{RELATIONSHIP}}}id")]
            member = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            sheet_root = ElementTree.fromstring(workbook.read(member))
            records = []
            for row in sheet_root.findall("表:sheetData/表:row", NAMESPACE):
                row_number = int(row.get("r"))
                if row_number == 1:
                    continue
                cells = {cell.get("r"): cell for cell in row}
                values = []
                for column in ("A", "B"):
                    cell = cells[f"{column}{row_number}"]
                    if cell.get("t", "n") != "n" or cell.find("表:f", NAMESPACE) is not None:
                        raise ValueError("数值列含非数值或公式，停止隐式类型转换")
                    value = float(cell.findtext("表:v", namespaces=NAMESPACE))
                    if not math.isfinite(value):
                        raise ValueError("数值列含非有限值")
                    values.append(value)
                records.append((row_number, values[0], values[1]))
        if len(records) != 7469 or any(left[1] >= right[1] for left, right in zip(records, records[1:])):
            raise ValueError("原始坐标不是预期的7469个严格递增点")
        original = [item[1] for item in records]
        if coordinates is not None and original != coordinates:
            raise ValueError("两角度完整原始波数不一致，不能按行配对")
        coordinates = original
        report["输入哈希"][attachment] = digest
        report["使用附件"].append(attachment)
        provenance.append({
            "源附件": attachment,
            "材料": manifest["材料"],
            "入射角_度": angle,
            "原始点数": len(records),
            "抽样点数": len(selection),
            "抽样观测": [{
                "原始行号": records[index][0],
                "波数_每厘米": records[index][1],
                "实测反射率_百分比": records[index][2],
                "实测反射率_比例": records[index][2] / 100,
                "质量标记": (["首点零值"] if index == 0 and records[index][2] == 0 else [])
                + (["反射率超过百分之百"] if records[index][2] > 100 else []),
            } for index in selection],
        })
    report["实测输入抽样"] = provenance
    return [coordinates[index] for index in selection], settings


def generator_reflectance(wavenumber, angle, layer_index, substrate_index, thickness):
    external = math.radians(angle)
    cosines = [math.cos(external), math.cos(math.asin(math.sin(external) / layer_index)),
               math.cos(math.asin(math.sin(external) / substrate_index))]
    indices = [1.0, layer_index, substrate_index]
    phase = 4 * math.pi * thickness * 1e-4 * wavenumber * layer_index * cosines[1]
    propagation = complex(math.cos(phase), math.sin(phase))
    reflected = []
    for polarization in ("垂直", "平行"):
        def interface(incident, transmitted):
            first, second = indices[incident], indices[transmitted]
            cosine_first, cosine_second = cosines[incident], cosines[transmitted]
            if polarization == "垂直":
                denominator = first * cosine_first + second * cosine_second
                reflection = (first * cosine_first - second * cosine_second) / denominator
            else:
                denominator = second * cosine_first + first * cosine_second
                reflection = (second * cosine_first - first * cosine_second) / denominator
            transmission = 2 * first * cosine_first / denominator
            return reflection, transmission
        surface, forward = interface(0, 1)
        substrate, _ = interface(1, 2)
        _, backward = interface(1, 0)
        field = surface + forward * backward * substrate * propagation
        reflected.append(abs(field) ** 2)
    return math.fsum(reflected) / 2


def make_cases(coordinates, settings):
    generator = random.Random(settings["随机种子"])
    combinations = itertools.product(settings["厚度情景微米"], settings["折射率基值情景"],
                                     settings["色散斜率情景"], settings["噪声标准差情景"])
    cases, truth = [], {}
    for case_number, (thickness, base, slope, noise) in enumerate(combinations, 1):
        case_id = f"情景{case_number:02d}"
        indices = [base + slope * (coordinate - 2200) / 1800 for coordinate in coordinates]
        observations = []
        for angle in settings["角度度"]:
            previous = 0.0
            for point_number, (coordinate, layer_index) in enumerate(zip(coordinates, indices)):
                fluctuation = 0.0
                if noise:
                    innovation = generator.gauss(0.0, noise)
                    fluctuation = innovation if point_number == 0 else 0.6 * previous + 0.8 * innovation
                    previous = fluctuation
                observations.append(generator_reflectance(
                    coordinate, angle, layer_index, layer_index + 0.8, thickness
                ) + fluctuation)
        cases.append({"编号": case_id, "折射率基值": base, "色散斜率": slope,
                      "噪声标准差_比例": noise, "层折射率": indices, "观测": observations})
        truth[case_id] = thickness
    return cases, truth


def physical_root(value):
    root = cmath.sqrt(value)
    if root.imag < 0 or (root.imag == 0 and root.real < 0):
        root = -root
    return root


def inverse_fields(wavenumber, angle, layer_index, substrate_index):
    transverse = math.sin(math.radians(angle))
    indices = (complex(1), complex(layer_index), complex(substrate_index))
    longitudinal = tuple(physical_root(index * index - transverse * transverse) for index in indices)
    fields = []
    for polarization in ("垂直", "平行"):
        admittances = longitudinal if polarization == "垂直" else tuple(
            index * index / projection for index, projection in zip(indices, longitudinal)
        )
        def interface(incident, transmitted):
            first, second = admittances[incident], admittances[transmitted]
            return (first - second) / (first + second), 2 * first / (first + second)
        surface, outward = interface(0, 1)
        substrate, _ = interface(1, 2)
        _, inward = interface(1, 0)
        fields.append((surface, outward * inward * substrate))
    coefficient = 4 * math.pi * 1e-4 * wavenumber * longitudinal[1]
    return fields, coefficient


def expanded_reflectance(fields, coefficient, thickness):
    attenuation = math.exp(-coefficient.imag * thickness)
    oscillation = cmath.exp(1j * coefficient.real * thickness)
    return math.fsum(abs(surface) ** 2 + abs(echo) ** 2 * attenuation ** 2
                     + 2 * (surface.conjugate() * echo * oscillation).real * attenuation
                     for surface, echo in fields) / 2


def prepare_optics(coordinates, indices, angles):
    optics = []
    for angle in angles:
        for coordinate, layer_index in zip(coordinates, indices):
            fields, coefficient = inverse_fields(coordinate, angle, layer_index, layer_index + 0.8)
            cross = sum(surface.conjugate() * echo for surface, echo in fields) / 2
            if abs(coefficient.imag) > 1e-12 or abs(cross.imag) > 1e-12:
                raise ValueError("透明合成专用快速目标不接受吸收参数；复数诊断保留完整衰减式")
            baseline = math.fsum(abs(surface) ** 2 + abs(echo) ** 2 for surface, echo in fields) / 2
            optics.append((baseline, 2 * cross.real, coefficient.real))
    return optics


def objective_terms(optics, observations):
    return [(baseline - measured, amplitude, frequency)
            for (baseline, amplitude, frequency), measured in zip(optics, observations)]


def loss(terms, thickness):
    return math.fsum((offset + amplitude * math.cos(frequency * thickness)) ** 2
                     for offset, amplitude, frequency in terms) / len(terms)


def curvature(terms, thickness):
    second_derivatives = []
    for offset, amplitude, frequency in terms:
        cosine = math.cos(frequency * thickness)
        first = -amplitude * frequency * math.sin(frequency * thickness)
        second = -amplitude * frequency * frequency * cosine
        second_derivatives.append(2 * (first * first + (offset + amplitude * cosine) * second))
    return math.fsum(second_derivatives) / len(terms)


def third_derivative_bound(terms):
    return 2 * math.fsum((4 * amplitude ** 2 + abs(offset * amplitude)) * abs(frequency) ** 3
                         for offset, amplitude, frequency in terms) / len(terms)


def coarse_search(terms, deadline):
    evaluations = []
    for thickness in THICKNESS_GRID:
        if time.monotonic() >= deadline:
            break
        evaluations.append((thickness, loss(terms, thickness)))
    return evaluations


def refine_candidate(terms, left, right, deadline):
    best, best_loss = None, math.inf
    evaluations = 0
    certificate = False
    maximum_third = third_derivative_bound(terms)
    for level in range(4):
        sample_count = 33 if level == 0 else 13
        sampled = []
        for index in range(sample_count):
            if time.monotonic() >= deadline:
                return best, best_loss, evaluations, certificate, False
            thickness = left + (right - left) * index / (sample_count - 1)
            value = loss(terms, thickness)
            evaluations += 1
            sampled.append((thickness, value))
            if value < best_loss:
                best, best_loss = thickness, value
        minimum = min(range(sample_count), key=lambda index: sampled[index][1])
        if minimum in (0, sample_count - 1):
            neighbor = 1 if minimum == 0 else sample_count - 2
            left, right = sorted((sampled[minimum][0], sampled[neighbor][0]))
            continue
        left, right = sampled[minimum - 1][0], sampled[minimum + 1][0]
        midpoint = (left + right) / 2
        certificate = curvature(terms, midpoint) - maximum_third * (right - left) / 2 > 0
        if certificate:
            break
    if not certificate:
        return best, best_loss, evaluations, certificate, True
    ratio = (math.sqrt(5) - 1) / 2
    for _ in range(20):
        if time.monotonic() >= deadline:
            return best, best_loss, evaluations, certificate, False
        inner_left = right - ratio * (right - left)
        inner_right = left + ratio * (right - left)
        left_loss, right_loss = loss(terms, inner_left), loss(terms, inner_right)
        evaluations += 2
        for thickness, value in ((inner_left, left_loss), (inner_right, right_loss)):
            if value < best_loss:
                best, best_loss = thickness, value
        if left_loss < right_loss:
            right = inner_right
        else:
            left = inner_left
    return best, best_loss, evaluations, certificate, True


def refine_search(terms, coarse, deadline):
    candidates = []
    for index, (_, value) in enumerate(coarse):
        lower = coarse[index - 1][1] if index else math.inf
        upper = coarse[index + 1][1] if index + 1 < len(coarse) else math.inf
        if value <= lower and value <= upper:
            candidates.append(index)
    selected = sorted(candidates, key=lambda index: coarse[index][1])[:3]
    best, best_loss = min(coarse, key=lambda item: item[1])
    details = []
    for index in selected:
        if time.monotonic() >= deadline:
            break
        left = coarse[max(0, index - 1)][0]
        right = coarse[min(len(coarse) - 1, index + 1)][0]
        thickness, value, count, certified, finished = refine_candidate(terms, left, right, deadline)
        if thickness is not None:
            details.append({"粗候选厚度_微米": coarse[index][0], "候选厚度_微米": thickness,
                            "联合残差平方均值_比例平方": value, "精修目标调用数": count,
                            "凸性证书通过": certified, "精修完整": finished})
            if value < best_loss:
                best, best_loss = thickness, value
    return best, best_loss, details, len(selected)


def score(report, truth):
    completed = report["逐例结果"]
    report["已完成例数"] = len(completed)
    for item in completed:
        reference = truth[item["编号"]]
        item["合成真厚度_微米"] = reference
        item["绝对相对误差_比例"] = abs(item["厚度_微米"] - reference) / reference
    valid = len(completed) == 24 and all(
        math.isfinite(item["厚度_微米"]) and item["厚度_微米"] > 0 for item in completed
    )
    value = 100 * math.fsum(item["绝对相对误差_比例"] for item in completed) / 24 if valid else None
    report["核心指标"] = {METRIC: value}
    report["核心指标键值"] = {METRIC: value}
    report["主指标值"] = value
    report["厚度及单位"] = [{"编号": item["编号"], "厚度_微米": item["厚度_微米"]} for item in completed]


def diagnostics(coordinates, settings, cases, truth, report, deadline):
    field_difference = generator_difference = snell_difference = 0.0
    angle_records, constant_checks = [], []
    by_id = {item["编号"]: item for item in report["逐例结果"]}
    checks = 0
    for case in cases:
        if time.monotonic() >= deadline or case["编号"] not in by_id:
            break
        estimate = by_id[case["编号"]]["厚度_微米"]
        for angle in settings["角度度"]:
            for index in (0, 85, 170, 255):
                coordinate, refractive = coordinates[index], case["层折射率"][index]
                fields, coefficient = inverse_fields(coordinate, angle, refractive, refractive + 0.8)
                direct = math.fsum(abs(surface + echo * cmath.exp(1j * coefficient * estimate)) ** 2
                                   for surface, echo in fields) / 2
                expanded = expanded_reflectance(fields, coefficient, estimate)
                generated = generator_reflectance(coordinate, angle, refractive, refractive + 0.8, estimate)
                field_difference = max(field_difference, abs(direct - expanded))
                generator_difference = max(generator_difference, abs(direct - generated))
                internal_angle = math.asin(math.sin(math.radians(angle)) / refractive)
                projected = math.sqrt(refractive ** 2 - math.sin(math.radians(angle)) ** 2)
                snell_difference = max(snell_difference, abs(projected - refractive * math.cos(internal_angle)))
                checks += 1
            if case["色散斜率"] == 0 and case["噪声标准差_比例"] == 0:
                refractive = case["折射率基值"]
                projected = math.sqrt(refractive ** 2 - math.sin(math.radians(angle)) ** 2)
                phase_start = 4 * math.pi * truth[case["编号"]] * 1e-4 * coordinates[0] * projected
                phase_end = 4 * math.pi * truth[case["编号"]] * 1e-4 * coordinates[-1] * projected
                analytic = (phase_end - phase_start) * 10000 / (
                    4 * math.pi * projected * (coordinates[-1] - coordinates[0])
                )
                constant_checks.append({"编号": case["编号"], "外部入射角_度": angle,
                                        "解析相位厚度_微米": analytic, "反演厚度_微米": estimate,
                                        "差值_微米": estimate - analytic,
                                        "误差不超过千分之一微米": abs(estimate - analytic) <= 0.001})
    for angle in settings["角度度"]:
        angle_records.append({"外部入射角_度": angle, "外部角_弧度": math.radians(angle),
                              "示例层折射率": 2.4,
                              "示例层内角_度": math.degrees(math.asin(math.sin(math.radians(angle)) / 2.4))})
    complex_fields, complex_coefficient = inverse_fields(2000, 15, 2.4 + 0.02j, 3.2 + 0.03j)
    direct_complex = math.fsum(abs(surface + echo * cmath.exp(1j * complex_coefficient * 8)) ** 2
                              for surface, echo in complex_fields) / 2
    return {
        "已核对点数": checks,
        "核对是否全部完成": checks == len(cases) * len(settings["角度度"]) * 4,
        "两电场与展开强度最大差_比例": field_difference if checks else None,
        "独立生成器与切向导纳反演最大差_比例": generator_difference if checks else None,
        "斯涅尔两种表达最大差": snell_difference if checks else None,
        "场一致性检查通过": max(field_difference, generator_difference) <= 1e-10 if checks else None,
        "偏振约定": "生成器用总电场振幅，反演用切向电场导纳；平行反射场整体符号相反，反射强度不变。",
        "反射相位": "由共轭表面场乘往返场确定；无任意波数相位函数或拟合相位参数。",
        "角度换算": angle_records,
        "微米厘米换算": {"输入厚度_微米": 8, "换算厚度_厘米": 8e-4,
                            "回换厚度_微米": 8e-4 * 10000, "波数单位": "每厘米"},
        "复数衰减支核对": {"往返传播因子模": abs(cmath.exp(1j * complex_coefficient * 8)),
                              "展开差_比例": abs(direct_complex - expanded_reflectance(
                                  complex_fields, complex_coefficient, 8)),
                              "仅诊断不参与评分": True},
        "常折射率无噪声核对": constant_checks,
        "解析核对限制": "解析参考由生成器未缠绕相位代入常折射率关系，只核对量纲与退化式；不是实测相位解缠结果。",
        "局部单峰确认": "L=2平均[(4振幅平方+|偏移乘振幅|)|相位斜率|立方]界定目标三阶导数；中点二阶导数减L乘半区间宽为正才用黄金分割。",
        "搜索非全局保证": "96点离散粗扫可能漏掉窄极小值；保留三个候选及全部粗网格损失，不据失败阈值扣掉任何一例。",
    }


def run_worker(started):
    report = initial_report()
    checkpoint(report, started)
    try:
        coordinates, settings = read_real_samples(report)
        cases, truth = make_cases(coordinates, settings)
        report["参数情景"] = [{key: case[key] for key in (
            "编号", "折射率基值", "色散斜率", "噪声标准差_比例"
        )} for case in cases]
        report["随机数口径"] = "依厚度、折射率基值、斜率、噪声的列出顺序做笛卡尔积，再按10度、15度及原坐标递增；零噪声不耗随机数，非零首点为平稳高斯，之后递推。"
        report["共同样本复核边界"] = "本文件公开随机流顺序和样本哈希；另两路线须按同一顺序核对输入，本文件不宣称已完成三路线输入一致性验收。"
        sample_bytes = json.dumps({"波数": coordinates, "合成样本": cases}, ensure_ascii=False,
                                  sort_keys=True, separators=(",", ":")).encode("utf-8")
        report["输入哈希"]["共同合成样本"] = hashlib.sha256(sample_bytes).hexdigest()
        report["阶段用时秒"] = {"读取与生成": time.monotonic() - started}
        checkpoint(report, started)
        optics_cache, prepared, all_coarse = {}, {}, {}
        coarse_started = time.monotonic()
        for case in cases:
            if time.monotonic() >= started + 165:
                break
            optical_key = (case["折射率基值"], case["色散斜率"])
            if optical_key not in optics_cache:
                optics_cache[optical_key] = prepare_optics(coordinates, case["层折射率"], settings["角度度"])
            optics = optics_cache[optical_key]
            terms = objective_terms(optics, case["观测"])
            prepared[case["编号"]] = (optics, terms)
            coarse = coarse_search(terms, started + 165)
            if not coarse:
                break
            all_coarse[case["编号"]] = coarse
            thickness, value = min(coarse, key=lambda pair: pair[1])
            report["逐例结果"].append({
                "编号": case["编号"], "厚度_微米": thickness, "粗扫厚度_微米": thickness,
                "联合残差平方均值_比例平方": value, "已扫厚度数": len(coarse),
                "粗网格": [{"厚度_微米": point, "联合残差平方均值_比例平方": score_value}
                            for point, score_value in coarse],
                "局部候选": [], "已精修候选数": 0, "计划精修候选数": 3,
                "粗细差_微米": None,
            })
            score(report, truth)
            checkpoint(report, started)
        report["阶段用时秒"]["前向粗扫"] = time.monotonic() - coarse_started
        refine_started = time.monotonic()
        for item in report["逐例结果"]:
            if time.monotonic() >= started + 150:
                break
            _, terms = prepared[item["编号"]]
            thickness, value, candidates, expected_count = refine_search(
                terms, all_coarse[item["编号"]], started + 150
            )
            item.update({"厚度_微米": thickness, "联合残差平方均值_比例平方": value,
                         "局部候选": candidates, "已精修候选数": len(candidates),
                         "计划精修候选数": expected_count,
                         "粗细差_微米": thickness - item["粗扫厚度_微米"]})
            score(report, truth)
            checkpoint(report, started)
        report["辅助诊断"] = diagnostics(coordinates, settings, cases, truth, report, started + 165)
        report["阶段用时秒"]["局部精修与核对"] = time.monotonic() - refine_started
        observations_by_id = {case["编号"]: case["观测"] for case in cases}
        for item in report["逐例结果"]:
            if time.monotonic() >= started + 165:
                break
            optics, _ = prepared[item["编号"]]
            predicted = [baseline + amplitude * math.cos(frequency * item["厚度_微米"])
                         for baseline, amplitude, frequency in optics]
            residuals = [observed - estimated for observed, estimated in
                         zip(observations_by_id[item["编号"]], predicted)]
            report["分块预测与残差"].append({
                "编号": item["编号"], "次序": "前256点为10度，后256点为15度；各自按样本索引递增",
                "拟合反射率_比例": predicted, "观测减拟合_比例": residuals,
                "用途": "同一样本还原残差，不是留出预测误差，不增加第二项评分",
            })
        score(report, truth)
        full_search = len(report["逐例结果"]) == 24 and all(
            item["已扫厚度数"] == 96 and item["已精修候选数"] == item["计划精修候选数"]
            and all(candidate["精修完整"] for candidate in item["局部候选"])
            for item in report["逐例结果"]
        )
        if report["主指标值"] is not None:
            all_checks = report["辅助诊断"]["核对是否全部完成"]
            report["运行状态"] = "完成" if full_search and all_checks else "提前收敛，全部24例已作答"
        else:
            report["运行状态"] = "未完成共同评分样本"
            report["失败原因"].append("软截止前未形成全部24例有限正厚度；不能用部分均值参赛")
        if not full_search:
            report["失败原因"].append("存在未完成的粗网格或精修候选，保留当前已算得的厚度而不删除难例")
        checkpoint(report, started)
        try:
            append_execution_record(report, started)
        except (OSError, ValueError) as error:
            report["实验记录追加状态"] = f"追加失败但未覆盖已有结果：{type(error).__name__}: {error}"
        checkpoint(report, started)
        print(json.dumps({"路线名": ROUTE, "样本数": report["已完成例数"],
                          "使用附件": report["使用附件"], "核心指标": report["核心指标"],
                          "厚度条件": "合成光学参数已知；非真实晶圆厚度结论",
                          "实际用时秒": report["实际用时秒"], "状态": report["运行状态"],
                          "经验覆盖率": "不适用，未构造统计区间", "失败原因": report["失败原因"]},
                         ensure_ascii=False), flush=True)
        return 0 if report["主指标值"] is not None else 2
    except Exception as error:
        report["运行状态"] = "异常中止，保留已完成结果"
        report["失败原因"].append(f"{type(error).__name__}: {error}")
        checkpoint(report, started)
        try:
            append_execution_record(report, started)
        except (OSError, ValueError):
            pass
        print(json.dumps({"路线名": ROUTE, "失败原因": report["失败原因"],
                          "核心指标": report["核心指标"]}, ensure_ascii=False), flush=True)
        return 1


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        return run_worker(float(sys.argv[2]))
    started = PROCESS_STARTED
    report = initial_report()
    checkpoint(report, started)
    try:
        remaining = max(0.01, 175 - (time.monotonic() - started))
        outcome = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(started)],
                                 cwd=ROOT, timeout=remaining, check=False)
    except subprocess.TimeoutExpired:
        report = json.loads(OUTPUT.read_text(encoding="utf-8"))
        report["运行状态"] = "达到175秒墙钟上限，保留已有数值"
        report["失败原因"].append("父进程终止本次子进程；不拼接另一次运行的成绩")
        checkpoint(report, started)
        print(json.dumps({"路线名": ROUTE, "状态": report["运行状态"],
                          "核心指标": report["核心指标"], "实际用时秒": report["实际用时秒"]},
                         ensure_ascii=False), flush=True)
        return 2
    report = json.loads(OUTPUT.read_text(encoding="utf-8"))
    if outcome.returncode and not report["失败原因"]:
        report["运行状态"] = "子进程异常退出，保留已有数值"
        report["失败原因"].append(f"退出码{outcome.returncode}")
    checkpoint(report, started)
    return outcome.returncode


if __name__ == "__main__":
    raise SystemExit(main())
