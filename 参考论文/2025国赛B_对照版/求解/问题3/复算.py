import os

for thread_variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[thread_variable] = "1"

import hashlib
import itertools
import json
import math
import signal
import sys
import time
from pathlib import Path

import numpy as np
import openpyxl
import scipy
from scipy.optimize import minimize


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "求解" / "问题3" / "红队结果"
STARTED = time.monotonic()
SOFT_SECONDS = 560.0
HARD_SECONDS = 590.0
SILICON_KEYS = ("硅全量完整往返厚度_um", "硅折1完整往返条件厚度_um", "硅折2完整往返条件厚度_um")
RANGE_KEYS = ("硅已计算条件包络下限_um", "硅已计算条件包络上限_um")
CARBIDE_KEYS = ("碳化硅正式基准厚度_um", "碳化硅正式条件范围下限_um", "碳化硅正式条件范围上限_um")
EXPECTED_HASHES = {
    "附件1.xlsx": "b42f8bdabe7497951284cd044386c3cdf9d349b5e8ef4baaa0336e65ff84c67b",
    "附件2.xlsx": "24b3113e80d5ec5458ba4f447df9ed185578481e8b0081bdf0673689505e49d0",
    "附件3.xlsx": "79e9f66a00409f1d1130d9f4f3d76b4d5e5a1ccd3ac2d24dc0af8ecc583d0ec8",
    "附件4.xlsx": "9cf1400404c4560958da06685beb8c2e1e4df5918a6e13863663a54440ba12e3",
}
REPORT = {
    "问题": 3,
    "复算方式": "独立实现；仅读取白名单中的题面契约、数据档案、结果声明和四份原始附件；未读建模师代码或结果",
    "复算指标": {name: None for name in SILICON_KEYS + RANGE_KEYS + CARBIDE_KEYS},
    "口径说明": {}, "逐键状态": {}, "数据校验": {}, "计算记录": [], "运行状态": "启动",
    "版本": {"解释器": sys.version.split()[0], "数值库": np.__version__, "科学计算库": scipy.__version__, "表格库": openpyxl.__version__},
}


class BudgetReached(Exception):
    pass


def elapsed():
    return time.monotonic() - STARTED


def check_budget(deadline=None):
    limit = STARTED + SOFT_SECONDS if deadline is None else min(deadline, STARTED + SOFT_SECONDS)
    if time.monotonic() >= limit:
        raise BudgetReached("达到阶段或全程时间预算")


def json_value(value):
    if isinstance(value, dict):
        return {str(name): json_value(item) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, np.ndarray):
        return json_value(value.tolist())
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def checkpoint():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    REPORT["运行秒数"] = elapsed()
    temporary = OUTPUT / "独立复算结果.json.tmp"
    temporary.write_text(json.dumps(json_value(REPORT), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(OUTPUT / "独立复算结果.json")


def add_record(kind, attempt, observation, decision, basis):
    REPORT["计算记录"].append({"类别": kind, "问题": 3, "尝试": attempt, "现象": observation, "决定": decision, "依据": basis})
    checkpoint()


def row_digest(rows):
    return hashlib.sha256(",".join(str(int(row)) for row in rows).encode("utf-8")).hexdigest()


def load_raw():
    spectra = {}
    for number in range(1, 5):
        check_budget()
        filename = f"附件{number}.xlsx"
        path = ROOT / "数据" / filename
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != EXPECTED_HASHES[filename]:
            raise ValueError(f"{filename} SHA256与声明不同，拒绝沿用冻结成员口径")
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        rows = []
        try:
            worksheet = workbook["Sheet1"]
            for row_number, values in enumerate(worksheet.iter_rows(min_row=2, min_col=1, max_col=2, values_only=True), start=2):
                if values == (None, None):
                    continue
                if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
                    raise ValueError(f"{filename}第{row_number}行不是完整数值，不静默剔除")
                if not np.all(np.isfinite(values)):
                    raise ValueError(f"{filename}第{row_number}行含非有限数")
                rows.append((row_number, float(values[0]), float(values[1]) / 100.0))
        finally:
            workbook.close()
        table = np.asarray(rows, dtype=float)
        table = table[np.argsort(table[:, 1], kind="stable")]
        if len(table) != 7469 or np.any(np.diff(table[:, 1]) <= 0):
            raise ValueError(f"{filename}行数或波数唯一性与数据档案不符")
        spectra[number] = table
        REPORT["数据校验"][filename] = {"文件摘要": actual_hash, "原始记录数": len(table), "剔除异常点数": 0, "百分数除数": 100}
    for number in range(2, 5):
        if not np.array_equal(spectra[1][:, :2], spectra[number][:, :2]):
            raise ValueError("四附件源行或波数轴不一致")
    return spectra


def sampling(spectra, material, low=1200.0, high=3800.0, full=False):
    attachments = (3, 4) if material == "硅" else (1, 2)
    reference = spectra[attachments[0]]
    selected = np.flatnonzero((reference[:, 1] >= low) & (reference[:, 1] <= high))
    if len(selected) < 480:
        raise ValueError("窗口不足480个真实源行")
    if not full:
        selected = selected[np.arange(480, dtype=np.int64) * (len(selected) - 1) // 479]
    return {"波数": reference[selected, 1], "反射率": np.vstack([spectra[number][selected, 2] for number in attachments]), "源行": reference[selected, 0].astype(np.int64), "附件": [f"附件{number}.xlsx" for number in attachments]}


def training_indices(sample, train_blocks):
    wave = sample["波数"]
    if len(wave) != 480:
        raise ValueError("分块仅适用于固定480行样本")
    blocks = np.arange(480) // 40 + 1
    in_training = np.isin(blocks, train_blocks)
    boundaries = []
    for boundary in range(1, 12):
        if (boundary in train_blocks) != (boundary + 1 in train_blocks):
            midpoint = (wave[boundary * 40 - 1] + wave[boundary * 40]) / 2.0
            boundaries.append(midpoint)
            in_training &= np.abs(wave - midpoint) >= 20.0
    return np.flatnonzero(in_training), boundaries


class CarbideObjective:
    def __init__(self, sample, indices, baseline_degree=2, amplitude_degree=1):
        self.wave = sample["波数"][indices]
        self.observed = sample["反射率"][:, indices]
        midpoint = (self.wave.min() + self.wave.max()) / 2.0
        halfwidth = (self.wave.max() - self.wave.min()) / 2.0
        self.coordinate = (self.wave - midpoint) / halfwidth
        self.baseline = np.vander(self.coordinate, baseline_degree + 1, increasing=True)
        quadratic = np.vander(self.coordinate, 3, increasing=True)
        detrended = self.observed - (quadratic @ np.linalg.lstsq(quadratic, self.observed.T, rcond=None)[0]).T
        self.scale = np.maximum(np.quantile(detrended, 0.75, axis=1) - np.quantile(detrended, 0.25, axis=1), 1e-4)
        self.orthogonal = np.linalg.qr(self.baseline, mode="reduced")[0]
        self.residual = self.observed - (self.orthogonal @ (self.orthogonal.T @ self.observed.T)).T
        self.amplitude_degree = amplitude_degree
        self.evaluations = 0

    @staticmethod
    def feasible(parameters):
        thickness, reference_index, dispersion = parameters
        if not (0.5 <= thickness <= 40.0 and 1.2 <= reference_index <= 6.0 and -1.0 <= dispersion <= 1.0):
            return False
        endpoints = reference_index + dispersion * ((2000.0 / np.array([1200.0, 3800.0])) ** 2 - 1.0)
        return bool(np.all(endpoints > np.sin(np.deg2rad(15.0))))

    def __call__(self, parameters):
        self.evaluations += 1
        if not self.feasible(parameters):
            return 1e10
        thickness, reference_index, dispersion = parameters
        refractive = reference_index + dispersion * ((2000.0 / self.wave) ** 2 - 1.0)
        axial = np.sqrt(refractive[None, :] ** 2 - np.sin(np.deg2rad([10.0, 15.0]))[:, None] ** 2)
        phase = 4.0 * np.pi * thickness * self.wave[None, :] * axial / 10000.0
        cosines, sines = np.cos(phase), np.sin(phase)
        basis = np.stack([cosines, sines, cosines * self.coordinate, sines * self.coordinate], axis=2)
        projected = basis - np.einsum("nj,jk,akl->anl", self.orthogonal, self.orthogonal.T, basis, optimize=True)
        gram = np.einsum("ani,anj->aij", projected, projected)
        cross = np.einsum("ani,an->ai", projected, self.residual)
        residual_norm = np.sum(self.residual ** 2, axis=1)

        def phase_cost(choices):
            cosine = np.cos(choices)
            negative_sine = -np.sin(choices)
            weights = np.stack([cosine, negative_sine], axis=2)
            cross_first = np.einsum("api,ai->ap", weights, cross[:, :2])
            gram_first = np.einsum("api,aij,apj->ap", weights, gram[:, :2, :2], weights)
            if self.amplitude_degree == 0:
                explained = cross_first ** 2 / np.maximum(gram_first, 1e-30)
                unstable = gram_first <= 1e-20
            else:
                cross_second = np.einsum("api,ai->ap", weights, cross[:, 2:])
                gram_second = np.einsum("api,aij,apj->ap", weights, gram[:, 2:, 2:], weights)
                gram_joint = np.einsum("api,aij,apj->ap", weights, gram[:, :2, 2:], weights)
                determinant = gram_first * gram_second - gram_joint ** 2
                explained = (gram_second * cross_first ** 2 + gram_first * cross_second ** 2 - 2.0 * gram_joint * cross_first * cross_second) / np.maximum(determinant, 1e-30)
                unstable = determinant <= 1e-10 * np.maximum(gram_first * gram_second, 1e-30)
            costs = (residual_norm[:, None] - explained) / (len(self.wave) * self.scale[:, None] ** 2)
            for angle_index, choice_index in np.argwhere(unstable | ~np.isfinite(costs)):
                carrier = np.cos(phase[angle_index] + choices[angle_index, choice_index])
                columns = [self.baseline, carrier[:, None]]
                if self.amplitude_degree:
                    columns.append((carrier * self.coordinate)[:, None])
                design = np.column_stack(columns)
                coefficients = np.linalg.lstsq(design, self.observed[angle_index], rcond=None)[0]
                costs[angle_index, choice_index] = np.mean((self.observed[angle_index] - design @ coefficients) ** 2) / self.scale[angle_index] ** 2
            return costs

        phases = np.broadcast_to(np.arange(12) * np.pi / 12.0, (2, 12)).copy()
        selected = np.argmin(phase_cost(phases), axis=1)
        current = phases[np.arange(2), selected]
        step = np.pi / 12.0
        for refinement in range(8):
            choices = np.mod(np.stack([current, current - step, current + step], axis=1), np.pi)
            current = choices[np.arange(2), np.argmin(phase_cost(choices), axis=1)]
            step /= 2.0
        losses = []
        for angle_index in range(2):
            carrier = np.cos(phase[angle_index] + current[angle_index])
            columns = [self.baseline, carrier[:, None]]
            if self.amplitude_degree:
                columns.append((carrier * self.coordinate)[:, None])
            design = np.column_stack(columns)
            coefficients = np.linalg.lstsq(design, self.observed[angle_index], rcond=None)[0]
            losses.append(np.mean((self.observed[angle_index] - design @ coefficients) ** 2) / self.scale[angle_index] ** 2)
        return float(np.mean(losses))


def distinct_parameters(parameters, candidates):
    return not any(np.all(np.abs(parameters - existing) < 1e-12) for existing in candidates)


def carbide_search(sample, indices, label, grid_count=96, max_starts=12, seconds=42.0, baseline_degree=2, amplitude_degree=1, constant_index=False, fixed_index=None, interior=False):
    objective = CarbideObjective(sample, indices, baseline_degree, amplitude_degree)
    deadline = min(time.monotonic() + seconds, STARTED + SOFT_SECONDS)
    pool = []
    local_states = []
    coarse_count = 0
    completed = True

    def retain(parameters, loss, origin):
        if not objective.feasible(parameters) or not np.isfinite(loss) or loss >= 1e9:
            return
        if fixed_index is not None and abs(parameters[1] - fixed_index) > 1e-12:
            return
        if not distinct_parameters(parameters, [member[1] for member in pool]):
            return
        pool.append((float(loss), np.array(parameters, dtype=float), origin))
        pool.sort(key=lambda member: member[0])
        del pool[8:]

    if fixed_index is not None and not 1.2 <= fixed_index <= 6.0:
        return {"情景": label, "厚度_um": None, "完成": False, "失败原因": "固定参考折射率请求出盒；未夹紧", "候选": []}
    try:
        reference_values = [fixed_index] if fixed_index is not None else [2.0, 3.0, 4.0]
        dispersion_values = [0.0] if constant_index else [-0.5, 0.0, 0.5]
        for reference_index in reference_values:
            for dispersion in dispersion_values:
                for thickness in np.linspace(0.5, 40.0, grid_count):
                    check_budget(deadline)
                    parameters = np.array([thickness, reference_index, dispersion])
                    if objective.feasible(parameters):
                        retain(parameters, objective(parameters), "粗搜索")
                    coarse_count += 1
        proposed = [member[1].copy() for member in pool[:max_starts]]
        proposed.extend(np.array(values, dtype=float) for values in [(5.171033357319079, 3.0, -0.5), (5.073684210526316, 3.0, -0.5), (8.0, 2.0, 0.0), (20.0, 4.0, 0.5)])
        starts = []
        for parameters in proposed:
            if fixed_index is not None:
                parameters[1] = fixed_index
            if constant_index:
                parameters[2] = 0.0
            if objective.feasible(parameters) and distinct_parameters(parameters, starts):
                starts.append(parameters.copy())
        for initial in starts[:max_starts]:
            check_budget(deadline)
            free_positions = [0] if fixed_index is not None else ([0, 1] if constant_index else [0, 1, 2])
            physical_bounds = [(0.5, 40.0), (1.2, 6.0), (-1.0, 1.0)]
            timed_out = [False]

            def expand(values):
                parameters = initial.copy()
                parameters[free_positions] = values
                return parameters

            def local_objective(values):
                if time.monotonic() >= deadline:
                    timed_out[0] = True
                    return 1e10
                return objective(expand(values))

            fitted = minimize(local_objective, initial[free_positions], method="L-BFGS-B", bounds=[physical_bounds[position] for position in free_positions], options={"maxiter": 70, "ftol": 1e-9, "maxls": 20, "eps": 1e-8, "gtol": 1e-5, "maxcor": 10, "maxfun": 15000})
            parameters = expand(fitted.x)
            retain(parameters, objective(parameters), "局部返回点")
            local_states.append({"收敛": bool(fitted.success), "停止说明": str(fitted.message), "迭代数": int(fitted.nit), "时间截断": timed_out[0]})
            if timed_out[0]:
                completed = False
                break
    except BudgetReached:
        completed = False
    eligible = pool
    if interior:
        lower = np.array([0.5, 1.2, -1.0])
        upper = np.array([40.0, 6.0, 1.0])
        eligible = [member for member in pool if np.all(np.minimum((member[1] - lower) / (upper - lower), (upper - member[1]) / (upper - lower)) > 1e-4)]
    result = {"情景": label, "完成": completed, "粗搜索次数": coarse_count, "目标评估次数": objective.evaluations, "局部状态": local_states, "训练点数_每角": len(indices), "源行摘要": row_digest(sample["源行"][indices]), "尺度": objective.scale, "候选": [{"参数": member[1], "损失": member[0], "来源": member[2]} for member in pool]}
    if eligible:
        result.update({"厚度_um": float(eligible[0][1][0]), "参数": eligible[0][1], "损失": eligible[0][0]})
    else:
        result.update({"厚度_um": None, "失败原因": "无满足该情景资格的有限候选"})
    REPORT.setdefault("碳化硅搜索", []).append(result)
    checkpoint()
    return result


class SiliconObjective:
    lower = np.array([0.5, 1.2, -1.0, -1.5, 0.0])
    upper = np.array([40.0, 6.0, 1.0, 2.5, 3.0])
    span = upper - lower

    def __init__(self, sample, indices, baseline_degree, penalty):
        self.wave = sample["波数"][indices]
        self.observed = sample["反射率"][:, indices]
        self.coordinate = (self.wave - 2500.0) / 1300.0
        self.design = np.vander(self.coordinate, baseline_degree + 1, increasing=True)
        self.inverse = np.linalg.pinv(self.design, rcond=1e-12)
        self.weights = np.column_stack([(1.0 - self.coordinate) / 2.0, (1.0 + self.coordinate) / 2.0])
        quadratic = np.vander(self.coordinate, 3, increasing=True)
        trend = quadratic @ np.linalg.solve(quadratic.T @ quadratic + 1e-12 * np.eye(3), quadratic.T @ self.observed.T)
        detrended = self.observed.T - trend
        self.scale = np.maximum(np.quantile(detrended, 0.75, axis=0) - np.quantile(detrended, 0.25, axis=0), 1e-4)
        centered = (self.observed - self.observed.mean(axis=1, keepdims=True)) / self.scale[:, None]
        regularizer = penalty * np.arange(baseline_degree + 1) ** 2 / 10.0
        self.beta = np.linalg.solve(self.design.T @ self.design / len(self.wave) + np.diag(regularizer), self.design.T @ centered.T / len(self.wave))
        self.remaining = centered.T - self.design @ self.beta
        self.beta_penalty = np.sum(regularizer[:, None] * self.beta ** 2, axis=0)
        self.penalty = penalty
        self.evaluations = 0

    def constraints(self, scaled):
        parameters = self.lower + self.span * scaled
        endpoint_dispersion = (2000.0 / np.array([1200.47, 3799.562])) ** 2 - 1.0
        film = parameters[1] + parameters[2] * endpoint_dispersion
        limit = np.sin(np.deg2rad(15.0)) + 2e-6
        return np.r_[film - limit, film + parameters[3] - limit]

    def constraint_jacobian(self, scaled):
        endpoint_dispersion = (2000.0 / np.array([1200.47, 3799.562])) ** 2 - 1.0
        matrix = np.zeros((4, 5))
        matrix[:, 1] = 1.0
        matrix[:, 2] = np.tile(endpoint_dispersion, 2)
        matrix[2:, 3] = 1.0
        return matrix * self.span

    def feasible(self, parameters):
        scaled = (np.asarray(parameters) - self.lower) / self.span
        return bool(np.all(scaled >= -1e-12) and np.all(scaled <= 1.0 + 1e-12) and np.min(self.constraints(scaled)) >= -1e-7)

    def repair(self, parameters):
        scaled = (np.clip(parameters, self.lower, self.upper) - self.lower) / self.span
        margins = self.constraints(scaled)
        if np.min(margins) >= 0.0:
            return self.lower + self.span * scaled
        anchor = (np.array([20.0, 6.0, 0.0, 0.8, 0.2]) - self.lower) / self.span
        anchor_margins = self.constraints(anchor)
        negative = margins < 0.0
        fraction = min(1.0, float(np.max(-margins[negative] / (anchor_margins[negative] - margins[negative]))) + 1e-10)
        return self.lower + self.span * ((1.0 - fraction) * scaled + fraction * anchor)

    def __call__(self, parameters):
        self.evaluations += 1
        if not self.feasible(parameters):
            return 1e10
        thickness, reference_index, dispersion, substrate_difference, loss = parameters
        film = reference_index + dispersion * ((2000.0 / self.wave) ** 2 - 1.0)
        substrate = film + substrate_difference
        sine_squared = np.sin(np.deg2rad([10.0, 15.0]))[:, None] ** 2
        air_axial = np.sqrt(1.0 - sine_squared)
        film_axial = np.sqrt(film[None, :] ** 2 - sine_squared)
        substrate_axial = np.sqrt(substrate[None, :] ** 2 - sine_squared)
        propagation = np.exp(-loss * film[None, :] / film_axial + 4j * np.pi * thickness * self.wave[None, :] * film_axial / 10000.0)
        intensity = np.zeros_like(film_axial)
        for polarization in ("横电", "横磁"):
            if polarization == "横电":
                air_admittance, film_admittance, substrate_admittance = air_axial, film_axial, substrate_axial
            else:
                air_admittance = 1.0 / air_axial
                film_admittance = film[None, :] ** 2 / film_axial
                substrate_admittance = substrate[None, :] ** 2 / substrate_axial
            front = (air_admittance - film_admittance) / (air_admittance + film_admittance)
            back = (film_admittance - substrate_admittance) / (film_admittance + substrate_admittance)
            if np.any(np.abs(front * back * propagation) >= 1.0):
                return 1e10
            field = (front + back * propagation) / (1.0 + front * back * propagation)
            intensity += 0.5 * np.abs(field) ** 2
        angle_losses = []
        for angle_index in range(2):
            features = intensity[angle_index, :, None] * self.weights
            orthogonal = features - self.design @ (self.inverse @ features)
            standardized = orthogonal / np.maximum(np.sqrt(np.mean(orthogonal ** 2, axis=0)), 1e-8)
            matrix = standardized.T @ standardized / len(self.wave) + self.penalty * np.eye(2)
            cross = standardized.T @ self.remaining[:, angle_index] / len(self.wave)
            best_gamma = None
            best_score = float("inf")
            for active in itertools.product((0, 1, 2), repeat=2):
                free = [position for position, state in enumerate(active) if state == 1]
                fixed = [position for position, state in enumerate(active) if state != 1]
                gamma = np.array([4.0 if state == 2 else 0.0 for state in active])
                if free:
                    right = cross[free] - matrix[np.ix_(free, fixed)] @ gamma[fixed]
                    gamma[free] = np.linalg.solve(matrix[np.ix_(free, free)], right)
                if np.any(gamma < -1e-10) or np.any(gamma > 4.0 + 1e-10):
                    continue
                gamma = np.clip(gamma, 0.0, 4.0)
                score = float(gamma @ matrix @ gamma - 2.0 * cross @ gamma)
                if score < best_score:
                    best_score, best_gamma = score, gamma
            residual = self.remaining[:, angle_index] - standardized @ best_gamma
            angle_losses.append(np.mean(residual ** 2) + self.penalty * float(best_gamma @ best_gamma) + self.beta_penalty[angle_index])
        return float(np.mean(angle_losses))


def silicon_starts(objective, deadline):
    count = len(objective.wave)
    subset = np.arange(count) if count <= 480 else np.arange(480) * (count - 1) // 479
    wave = objective.wave[subset]
    coordinate = (wave - 2500.0) / 1300.0
    trend_design = np.vander(coordinate, 3, increasing=True)
    observations = objective.observed[:, subset]
    detrended = observations - (trend_design @ np.linalg.lstsq(trend_design, observations.T, rcond=1e-12)[0]).T
    detrended /= objective.scale[:, None]
    starts = [np.array([5.0, 3.0, 0.0, 0.8, 0.2])]
    thicknesses = np.linspace(0.5, 40.0, 384)
    for reference_index in (1.6, 2.6, 3.6, 4.8, 5.8):
        for dispersion in (-0.3, 0.0, 0.3):
            check_budget(deadline)
            film = reference_index + dispersion * ((2000.0 / wave) ** 2 - 1.0)
            scores = np.zeros(len(thicknesses))
            for angle_index, angle in enumerate((10.0, 15.0)):
                axial = np.sqrt(film ** 2 - np.sin(np.deg2rad(angle)) ** 2)
                phase = 4.0 * np.pi * thicknesses[:, None] * wave[None, :] * axial[None, :] / 10000.0
                for carriers in (np.cos(phase), np.sin(phase)):
                    carriers -= carriers.mean(axis=1, keepdims=True)
                    scores += 0.5 * (carriers @ detrended[angle_index]) ** 2 / np.maximum(np.sum(carriers ** 2, axis=1), 1e-30)
            selected = []
            for candidate in np.argsort(-scores, kind="stable"):
                thickness = thicknesses[candidate]
                if all(abs(thickness - previous) >= 0.5 for previous in selected):
                    selected.append(thickness)
                if len(selected) == 2:
                    break
            for thickness in selected:
                for contrast in (-0.6, 0.8):
                    parameters = np.array([thickness, reference_index, dispersion, contrast, 0.2])
                    if objective.feasible(parameters):
                        starts.append(parameters)
    return starts


def silicon_search(sample, indices, baseline_degree, penalty, label, seconds=38.0):
    objective = SiliconObjective(sample, indices, baseline_degree, penalty)
    deadline = min(time.monotonic() + seconds, STARTED + SOFT_SECONDS)
    candidates = []
    states = []
    complete = True

    def retain(parameters, origin):
        if objective.feasible(parameters):
            loss = objective(parameters)
            if np.isfinite(loss) and loss < 1e9:
                candidates.append((loss, np.array(parameters, dtype=float), origin))
                return loss
        return 1e10

    try:
        starts = silicon_starts(objective, deadline)
        scored = []
        for sequence, parameters in enumerate(starts):
            check_budget(deadline)
            scored.append((retain(parameters, "独立原始起点"), sequence, parameters))
        scored.sort(key=lambda item: (item[0], item[1]))
        selected = scored[:3]
        for candidate in scored[3:]:
            if len(selected) >= 6:
                break
            if candidate[0] <= 1.1 * scored[0][0] + 1e-12:
                selected.append(candidate)
        for original_loss, original_sequence, initial in selected:
            for evaluation_limit, iteration_limit in ((150, 30), (300, 60)):
                check_budget(deadline)
                calls = [0]
                best = [original_loss, initial.copy()]

                def scaled_objective(scaled):
                    check_budget(deadline)
                    if calls[0] >= evaluation_limit:
                        raise BudgetReached("该原始起点评估次数达到分级上限")
                    calls[0] += 1
                    requested = objective.lower + objective.span * scaled
                    repaired = objective.repair(requested)
                    loss = objective(repaired)
                    if loss < best[0]:
                        best[:] = [loss, repaired.copy()]
                    displacement = (repaired - requested) / objective.span
                    return loss + 1000.0 * float(displacement @ displacement)

                fitted = None
                stop_reason = ""
                try:
                    fitted = minimize(scaled_objective, (initial - objective.lower) / objective.span, method="SLSQP", bounds=[(0.0, 1.0)] * 5, constraints=[{"type": "ineq", "fun": objective.constraints, "jac": objective.constraint_jacobian}], options={"ftol": 1e-7, "eps": 1e-6, "maxiter": iteration_limit})
                    returned = objective.lower + objective.span * fitted.x
                    retain(returned, "独立优化真实返回点")
                    stop_reason = str(fitted.message)
                except BudgetReached as error:
                    stop_reason = str(error)
                    if time.monotonic() >= deadline:
                        complete = False
                candidates.append((best[0], best[1], "独立优化保留点"))
                states.append({"原始起点序号": original_sequence, "评估上限": evaluation_limit, "实际评估": calls[0], "停止说明": stop_reason, "收敛": bool(fitted is not None and fitted.success), "保留厚度_um": float(best[1][0])})
                if not complete:
                    raise BudgetReached("硅阶段墙钟预算到达")
    except BudgetReached:
        complete = False
    candidates.sort(key=lambda item: (item[0], tuple(item[1])))
    result = {"情景": label, "独立搜索完成": complete, "历史候选整合完成": False, "训练点数_每角": len(indices), "源行摘要": row_digest(sample["源行"][indices]), "尺度": objective.scale, "优化状态": states, "候选": [{"损失": item[0], "参数": item[1], "来源": item[2]} for item in candidates]}
    if candidates:
        result.update({"厚度_um": float(candidates[0][1][0]), "参数": candidates[0][1], "损失": candidates[0][0]})
    else:
        result.update({"厚度_um": None, "失败原因": "独立搜索没有取得有限可行候选"})
    REPORT.setdefault("硅独立搜索", []).append(result)
    checkpoint()
    return result


def store_metric(name, value, status, **details):
    REPORT["复算指标"][name] = None if value is None else float(value)
    REPORT["逐键状态"][name] = {"状态": status, "复算值": REPORT["复算指标"][name], **details}
    checkpoint()


def prepare_descriptions():
    silicon_common = "单位μm；附件3、4分别读取10°、15°实测反射率，百分数除100；不插值、不裁剪、不额外剔除异常；波数1200—3800 cm^-1，传播约束端点1200.47/3799.562。双角等权标准化惩罚损失，自行重写完整往返场、正交响应及活动集求解；不平均两角厚度。"
    for fold, name in enumerate(SILICON_KEYS):
        sample_scope = "全部5392原始点/角" if fold == 0 else f"固定480源行按第{fold}折训练块及20 cm^-1保护带取292训练点/角；校准和测试不参与估计"
        REPORT["口径说明"][name] = silicon_common + sample_scope + "。数值仅为独立原始起点重优化的最小目标候选；未使用不在本次白名单内的历史参数表，不能声称复现含471条历史候选的v3r3有限搜索集合。候选集差异及截断情况单列，不得作为已核对历史有限最优的证据。"
    missing_range = "单位μm；附件3、4；不额外剔除异常。声明要求六自由案例、固定锚点扰动及控制各自10%近优成员的联合极值，不是任意独立搜索点极值或概率区间。公开口径引用数据/问题3_公开复算输入/冻结条件.json中的471条历史候选及六案例固定锚点；该文件不属于本轮获准读取的原始数据或问题3_冻结合成输入包，未读取。白名单中的结果声明未列出完整历史参数及锚点，无法重建1101成员，故复算值为null；需将这些必需输入纳入允许的冻结输入包或另行明确授权，不以其他合成情景替代。"
    for name in RANGE_KEYS:
        REPORT["口径说明"][name] = missing_range
        REPORT["逐键状态"][name] = {"状态": "失败", "复算值": None, "失败原因": "精确有限候选集与固定锚点不在当前允许输入中；未编造包络"}
    carbide_common = "单位μm；附件1、2分别读取10°、15°反射率，百分数除100；默认1200—3800 cm^-1每角480固定源行，不插值、不裁剪、不额外剔异常；每角训练中心/半幅及二次去趋势IQR独立重建，两角标准化MSE等权平均。独立以正交投影快速筛相位、原设计矩阵最小二乘复评损失；公开12相位和8轮折半规则、96厚度格及L-BFGS-B重新计算，不抄跨问题头条。"
    REPORT["口径说明"][CARBIDE_KEYS[0]] = carbide_common + "全480点/角训练；有限候选池内选训练损失最小且三参数相对边界距离均大于0.0001的代表；不是5392点完整往返模型厚度。"
    for name in CARBIDE_KEYS[1:]:
        extreme = "最小值" if "下限" in name else "最大值"
        REPORT["口径说明"][name] = carbide_common + f"正式两折、五折、10%近等损失剖面两端与合法灵敏度情景联合取{extreme}；两追加窗口各自在原附件重抽480源行，严格固定n出盒排除，边界夹紧诊断排除；不是分位数或概率区间。若全程预算阻止必需情景执行，则核心值为null，已完成部分范围另存，绝不冒充完整范围。"
    REPORT["指标定义"] = {
        name: {"单位": "微米", "样本范围": "附件3、4" if name.startswith("硅") else "附件1、2", "是否剔除异常点": False, "聚合方式": REPORT["口径说明"][name]}
        for name in REPORT["复算指标"]
    }


def carbide_profile(sample, parameters):
    objective = CarbideObjective(sample, np.arange(480))
    thicknesses = np.unique(np.r_[np.linspace(0.5, 40.0, 17), parameters[0]])
    references = np.unique(np.r_[np.linspace(1.2, 6.0, 13), parameters[1]])
    candidates = []
    complete = True
    try:
        for reference_index in references:
            for thickness in thicknesses:
                check_budget()
                requested = np.array([thickness, reference_index, parameters[2]])
                if objective.feasible(requested):
                    candidates.append((objective(requested), float(thickness), float(reference_index)))
    except BudgetReached:
        complete = False
    if candidates:
        best_loss = min(item[0] for item in candidates)
        accepted = [item for item in candidates if item[0] <= 1.1 * best_loss + 1e-12]
        limits = [min(item[1] for item in accepted), max(item[1] for item in accepted)]
    else:
        best_loss, accepted, limits = None, [], []
    REPORT["碳化硅独立剖面"] = {"完成": complete, "有效格数": len(candidates), "最小损失": best_loss, "近优成员": [{"损失": item[0], "厚度_um": item[1], "参考折射率": item[2]} for item in accepted], "两端_um": limits}
    checkpoint()
    return limits, complete


def run_calculations():
    prepare_descriptions()
    checkpoint()
    declaration_path = ROOT / "交接" / "结果声明_问题3.json"
    declaration_bytes = declaration_path.read_bytes()
    declaration = json.loads(declaration_bytes)
    contract = json.loads((ROOT / "交接" / "题面契约.json").read_text(encoding="utf-8"))
    archive = json.loads((ROOT / "交接" / "数据档案.json").read_text(encoding="utf-8"))
    if not any(question.get("编号") == 3 for question in contract.get("问题", [])):
        raise ValueError("题面契约缺少问题3")
    if archive.get("下游读取规范", {}).get("文件格式") != "xlsx":
        raise ValueError("数据档案不再声明xlsx输入")
    if set(declaration.get("核心指标", {})) != set(REPORT["复算指标"]):
        raise ValueError("声明核心键已变化，不能用旧口径继续计算")
    REPORT["声明文件摘要"] = hashlib.sha256(declaration_bytes).hexdigest()
    REPORT["目标值隔离"] = "仅校验声明的核心键和口径；未将声明中的核心数值传入目标函数、初始点、选择规则或输出值"
    REPORT["输入隔离"] = "不读取任何建模师脚本、求解结果、建模笔记、结果解读、实验记录或上一版红队结果；不读取白名单外公开复算参数表"
    REPORT["合成输入处理"] = "当前八核心键均定义于四份实测附件；未使用现有合成验证包替换原始实测输入。若将来有核心键定义在缺失的合成输入上，必须另导出数据/问题3_冻结合成输入/，不能在本实现里自造数据。"
    REPORT["自检口径说明"] = "声明明确自检指标沿用前轮，并非v3r3重评分；本轮不逐键比较、不将沿用指标当新版本复算事实。"
    del declaration
    spectra = load_raw()
    carbide_sample = sampling(spectra, "碳化硅")
    silicon_sample = sampling(spectra, "硅")
    silicon_full = sampling(spectra, "硅", full=True)
    expected_sample_digest = "31e31cc956b8f63f7eb5a3c2128897eed2a271f4fdeacf1e42b2d28d20720625"
    if row_digest(silicon_sample["源行"]) != expected_sample_digest or not np.array_equal(carbide_sample["源行"], silicon_sample["源行"]):
        raise ValueError("固定480源行摘要与公开口径不符")
    if len(silicon_full["波数"]) != 5392 or not np.allclose(silicon_full["波数"][[0, -1]], [1200.47, 3799.562], atol=1e-9, rtol=0):
        raise ValueError("全量窗口端点或样本数不符")
    fold_blocks = ([1, 2, 4, 5, 7, 8, 10, 11], [2, 3, 5, 6, 8, 9, 11, 12])
    expected_folds = ("17c71ccbd62c3ac27f7a6a3db0b61fbe7d7f5a51cec94c24e744217d3a5ca8fd", "1a6734b916dc0b2e6945c0f2fb5bb50efb2e2987d2dc4d1409fe4f76e365e213")
    folds = []
    REPORT["固定抽样"] = {"源行摘要": expected_sample_digest, "全量每角点数": 5392, "抽样每角点数": 480, "实际窗口": silicon_full["波数"][[0, -1]]}
    for fold_number, blocks in enumerate(fold_blocks, start=1):
        indices, boundaries = training_indices(silicon_sample, blocks)
        if len(indices) != 292 or row_digest(silicon_sample["源行"][indices]) != expected_folds[fold_number - 1]:
            raise ValueError(f"折{fold_number}训练成员与公开口径不符")
        folds.append(indices)
        REPORT.setdefault("分折核验", []).append({"折号": fold_number, "训练块": blocks, "保护边界": boundaries, "训练点数_每角": len(indices), "源行摘要": row_digest(silicon_sample["源行"][indices])})
    add_record("流程事件", "从四份原附件重建全量、固定源行与保护带", "文件摘要、5392全量点、480抽样点及每折292训练点通过独立校验", "开展原始数据独立拟合", "结果声明公开采样规则与数据档案")

    members = []
    carbide_partial = False
    for fold_number, indices in enumerate(folds, start=1):
        result = carbide_search(carbide_sample, indices, f"正式第{fold_number}折")
        if result["厚度_um"] is not None:
            members.append({"来源": f"正式第{fold_number}折", "厚度_um": result["厚度_um"], "搜索完成": result["完成"]})
        else:
            carbide_partial = True
    full = carbide_search(carbide_sample, np.arange(480), "正式480点全量非触边代表", interior=True)
    store_metric(CARBIDE_KEYS[0], full["厚度_um"], "已独立计算" if full["厚度_um"] is not None else "失败", 搜索完成=full["完成"], 失败原因=full.get("失败原因"))

    silicon_cases = [(silicon_full, np.arange(5392), 1, 0.1), (silicon_sample, folds[0], 2, 1.0), (silicon_sample, folds[1], 2, 0.1)]
    for name, (sample, indices, degree, penalty) in zip(SILICON_KEYS, silicon_cases):
        check_budget()
        result = silicon_search(sample, indices, degree, penalty, name)
        store_metric(name, result["厚度_um"], "独立拟合值；历史候选集合未复现" if result["厚度_um"] is not None else "失败", 独立搜索完成=result["独立搜索完成"], 同口径限制="训练数据和目标相同；不含白名单外历史候选，不能认定为原有限候选集复现", 失败原因=result.get("失败原因"))
    add_record("科学尝试", "独立Airy有理场及双角受限响应重估硅三个自由厚度", "计算值及每起点的评估/收敛状态已落盘；历史候选未读取", "保留真实拟合量，候选集限制显式传给比对腿；不伪造1101成员包络", "独立投影初值、原始光谱和公开物理/响应公式")

    if full["厚度_um"] is not None:
        limits, profile_complete = carbide_profile(carbide_sample, full["参数"])
        for endpoint, thickness in zip(("剖面下端", "剖面上端"), limits):
            members.append({"来源": endpoint, "厚度_um": thickness, "搜索完成": profile_complete})
        carbide_partial |= not profile_complete or not limits
    else:
        carbide_partial = True
    REPORT["碳化硅条件成员"] = members
    checkpoint()
    for fold_number in range(5):
        check_budget()
        test_blocks = {2 * fold_number + 1, 2 * fold_number + 2}
        train_blocks = [block for block in range(1, 11) if block not in test_blocks]
        indices, boundaries = training_indices(carbide_sample, train_blocks)
        result = carbide_search(carbide_sample, indices, f"五折检查{fold_number + 1}", grid_count=48, max_starts=4, seconds=30.0)
        if result["厚度_um"] is not None:
            members.append({"来源": f"五折检查{fold_number + 1}", "厚度_um": result["厚度_um"], "搜索完成": result["完成"]})
        else:
            carbide_partial = True
        checkpoint()
    reference = None if full["厚度_um"] is None else float(full["参数"][1])
    scenarios = [
        ("主模型", {}, True),
        ("常数基线", {"baseline_degree": 0}, True),
        ("一次基线", {"baseline_degree": 1}, True),
        ("常数幅值", {"amplitude_degree": 0}, True),
        ("常数折射率", {"constant_index": True}, True),
        ("参考折射率相对减3%_严格请求", {"fixed_index": None if reference is None else 0.97 * reference}, True),
        ("参考折射率相对加3%_严格请求", {"fixed_index": None if reference is None else 1.03 * reference}, True),
        ("物理可行域夹紧诊断_下边界外请求", {"fixed_index": None if reference is None else float(np.clip(max(0.0, 1.2 - 0.03 * reference), 1.2, 6.0))}, False),
        ("厚度网格加密2倍", {"grid_count": 192}, True),
    ]
    for label, options, admissible in scenarios:
        check_budget()
        if "fixed_index" in options and options["fixed_index"] is None:
            carbide_partial |= admissible
            REPORT.setdefault("未评估情景", []).append({"情景": label, "原因": "独立全量参数未生成，无法定义相对折射率请求"})
            continue
        result = carbide_search(carbide_sample, np.arange(480), label, **options)
        if result["厚度_um"] is not None and admissible:
            members.append({"来源": label, "厚度_um": result["厚度_um"], "搜索完成": result["完成"]})
        elif result["厚度_um"] is None:
            REPORT.setdefault("未评估情景", []).append({"情景": label, "原因": result.get("失败原因"), "预定资格": admissible})
            if "出盒" not in result.get("失败原因", ""):
                carbide_partial |= admissible
        checkpoint()
    for low, high in ((1300.0, 3800.0), (1200.0, 3900.0)):
        check_budget()
        window_sample = sampling(spectra, "碳化硅", low=low, high=high)
        label = f"窗口{int(low)}至{int(high)}重新抽样"
        result = carbide_search(window_sample, np.arange(480), label)
        if result["厚度_um"] is not None:
            members.append({"来源": label, "厚度_um": result["厚度_um"], "搜索完成": result["完成"]})
        else:
            carbide_partial = True
        checkpoint()
    if members:
        limits = [min(member["厚度_um"] for member in members), max(member["厚度_um"] for member in members)]
        REPORT["碳化硅已完成部分范围_um"] = limits
        REPORT["碳化硅条件集合完成"] = not carbide_partial
        if not carbide_partial:
            for name, value in zip(CARBIDE_KEYS[1:], limits):
                store_metric(name, value, "已独立汇总", 成员数=len(members), 包含按公开局部预算截断的合法值=any(not member["搜索完成"] for member in members))
        else:
            for name in CARBIDE_KEYS[1:]:
                store_metric(name, None, "失败", 失败原因="所需合法情景或剖面有未完成项；部分范围不得冒充正式条件范围")
    add_record("科学尝试", "独立重做碳化硅两折、五折、剖面及灵敏度条件集合", "逐情景参数、训练损失、有限候选、实际计算次数和停止状态已保存", "只对合法集合取极值；未完成键保留null", "原始附件以及声明中跨问题正式生成规则")


def finish_missing(reason):
    for name, value in REPORT["复算指标"].items():
        if value is None and name not in REPORT["逐键状态"]:
            REPORT["逐键状态"][name] = {"状态": "失败", "复算值": None, "失败原因": reason}


def main():
    checkpoint()
    try:
        run_calculations()
        REPORT["运行状态"] = "计算阶段结束；未与声明数值比较"
    except BudgetReached as error:
        REPORT["运行状态"] = "时间预算到达，已保存实际完成部分"
        finish_missing(str(error))
    except Exception as error:
        REPORT["运行状态"] = "输入或计算失败，未伪造缺失值"
        REPORT["失败原因"] = f"{type(error).__name__}: {error}"
        finish_missing(REPORT["失败原因"])
    finally:
        finish_missing("对应计算未完成，详见计算记录")
        partial_members = REPORT.get("碳化硅条件成员", [])
        if partial_members and "碳化硅已完成部分范围_um" not in REPORT:
            REPORT["碳化硅已完成部分范围_um"] = [min(member["厚度_um"] for member in partial_members), max(member["厚度_um"] for member in partial_members)]
            REPORT["碳化硅条件集合完成"] = False
        checkpoint()
        print(json.dumps(json_value({"复算指标": REPORT["复算指标"], "逐键状态": REPORT["逐键状态"], "运行状态": REPORT["运行状态"], "运行秒数": REPORT["运行秒数"]}), ensure_ascii=False, allow_nan=False))


def hard_timeout(signum, frame):
    raise BudgetReached("590秒硬预算触发，保存已完成数值")


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, hard_timeout)
    signal.setitimer(signal.ITIMER_REAL, HARD_SECONDS)
    main()
