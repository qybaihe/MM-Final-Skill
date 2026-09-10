import cmath
import hashlib
import itertools
import json
import math
import signal
import statistics
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '求解/问题3/结果/回炉轮3候选/仲裁闭环/20260910_131501_81623_建模公式/厚度结果.json'
OUTPUT = ROOT / '求解/问题3/结果/解读核验_回炉3_仲裁后.json'
STARTED = time.monotonic()
TIME_LIMIT = 600
AUDIT = {'问题': 3, '状态': '核验中', '时间上限秒': TIME_LIMIT, '当前结果来源': str(SOURCE.relative_to(ROOT))}
NAMESPACE = {'表': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save():
    AUDIT['实际用时秒'] = time.monotonic() - STARTED
    temporary = OUTPUT.with_suffix('.tmp')
    temporary.write_text(json.dumps(AUDIT, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(OUTPUT)


def event(category, attempt, observation, decision, evidence):
    path = ROOT / '交接/实验记录.json'
    records = load(path) if path.exists() else []
    record = {'类别': category, '问题': 3, '尝试': attempt, '现象': observation, '决定': decision, '依据': evidence}
    if record not in records:
        records.append(record)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        temporary.replace(path)


def checkpoint():
    if time.monotonic() - STARTED > TIME_LIMIT - 10:
        raise TimeoutError('接近时间上限，保存已完成核验，不补认通过')


def timeout_handler(signum, frame):
    raise TimeoutError('达到600秒核验上限')


def linear_solve(matrix, right):
    size = len(right)
    augmented = [list(row) + [value] for row, value in zip(matrix, right)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda index: abs(augmented[index][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        if abs(divisor) < 1e-20:
            raise ValueError('独立线性求解出现奇异矩阵')
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row != column:
                multiplier = augmented[row][column]
                augmented[row] = [left - multiplier * right_value for left, right_value in zip(augmented[row], augmented[column])]
    return [row[-1] for row in augmented]


def dot(left, right):
    return sum(first * second for first, second in zip(left, right))


def least_squares(design, values, diagonal=None):
    width = len(design[0])
    gram = [[sum(row[left] * row[right] for row in design) for right in range(width)] for left in range(width)]
    if diagonal:
        for index, value in enumerate(diagonal):
            gram[index][index] += value
    return linear_solve(gram, [sum(row[index] * value for row, value in zip(design, values)) for index in range(width)])


def quantile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[min(lower + 1, len(ordered) - 1)] * fraction


def bounded_gains(gram, right):
    candidates = []
    for flags in itertools.product((0, 1, 2), repeat=len(right)):
        gains = [4.0 if flag == 2 else 0.0 for flag in flags]
        free = [index for index, flag in enumerate(flags) if flag == 1]
        fixed = [index for index, flag in enumerate(flags) if flag != 1]
        if free:
            solution = linear_solve([[gram[row][column] for column in free] for row in free],
                                    [right[row] - sum(gram[row][column] * gains[column] for column in fixed) for row in free])
            for index, value in zip(free, solution):
                gains[index] = value
        if all(-1e-9 <= value <= 4 + 1e-9 for value in gains):
            gains = [min(4.0, max(0.0, value)) for value in gains]
            cost = dot(gains, [dot(row, gains) for row in gram]) - 2 * dot(right, gains)
            candidates.append((cost, gains))
    return min(candidates, key=lambda item: item[0])[1]


def read_attachment(number):
    path = ROOT / '数据' / f'附件{number}.xlsx'
    with ZipFile(path) as archive:
        document = ElementTree.fromstring(archive.read('xl/worksheets/sheet1.xml'))
    rows = []
    for row in document.findall('.//表:sheetData/表:row', NAMESPACE):
        source_row = int(row.attrib['r'])
        if source_row == 1:
            continue
        values = {}
        for cell in row.findall('表:c', NAMESPACE):
            value = cell.find('表:v', NAMESPACE)
            if value is not None and value.text is not None:
                values[cell.attrib['r'].rstrip('0123456789')] = float(value.text)
        if 'A' in values and 'B' in values:
            rows.append((source_row, values['A'], values['B'] / 100.0))
    return sorted(rows, key=lambda row: row[1])


def field(parameters, wave, angle, complete):
    thickness, reference, dispersion, contrast, loss = parameters
    film = reference + dispersion * ((2000 / wave) ** 2 - 1)
    support = film + contrast
    sine_squared = math.sin(math.radians(angle)) ** 2
    air_normal = math.cos(math.radians(angle))
    film_normal = math.sqrt(film * film - sine_squared)
    support_normal = math.sqrt(support * support - sine_squared)
    propagation = cmath.exp(-loss * film / film_normal + 4j * math.pi * thickness * wave * film_normal / 10000)
    intensities = []
    ratios = []
    for air, layer, substrate in ((air_normal, film_normal, support_normal),
                                  (1 / air_normal, film * film / film_normal, support * support / support_normal)):
        reflection = (air - layer) / (air + layer)
        interface = (layer - substrate) / (layer + substrate)
        first = 4 * air * layer / (air + layer) ** 2 * interface * propagation
        ratio = -reflection * interface * propagation
        amplitude = reflection + (first / (1 - ratio) if complete else first)
        intensities.append(abs(amplitude) ** 2)
        ratios.append(abs(ratio))
    return statistics.mean(intensities), max(ratios)


def independent_predictions(rows, training, parameters, config, angle, complete):
    degree, gain_degree, penalty = config
    abscissa = [(row[1] - 2500) / 1300 for row in rows]
    observed = [row[2] for row in rows]
    quadratic = [[1.0, value, value * value] for value in abscissa]
    trend = least_squares([quadratic[index] for index in training], [observed[index] for index in training], [1e-12] * 3)
    residuals = [observed[index] - dot(quadratic[index], trend) for index in training]
    scale = max(quantile(residuals, 0.75) - quantile(residuals, 0.25), 1e-4)
    mean = statistics.mean(observed[index] for index in training)
    background = [[value ** power for power in range(degree + 1)] for value in abscissa]
    target = [(observed[index] - mean) / scale for index in training]
    coefficients = least_squares([background[index] for index in training], target,
                                [len(training) * penalty / 10 * power * power for power in range(degree + 1)])
    physics = [field(parameters, row[1], angle, complete) for row in rows]
    physical = [item[0] for item in physics]
    weights = [[1.0] if gain_degree == 0 else [(1 - value) / 2, (1 + value) / 2] for value in abscissa]
    raw = [[value * weight for weight in pair] for value, pair in zip(physical, weights)]
    projections = [least_squares([background[index] for index in training], [raw[index][column] for index in training]) for column in range(gain_degree + 1)]
    centered = [[raw[index][column] - dot(background[index], projections[column]) for column in range(gain_degree + 1)] for index in range(len(rows))]
    normalizers = [max(math.sqrt(statistics.mean(centered[index][column] ** 2 for index in training)), 1e-8) for column in range(gain_degree + 1)]
    response = [[value / divisor for value, divisor in zip(row, normalizers)] for row in centered]
    remainder = [value - dot(background[index], coefficients) for value, index in zip(target, training)]
    gram = [[statistics.mean(response[index][left] * response[index][right] for index in training) + (penalty if left == right else 0)
             for right in range(gain_degree + 1)] for left in range(gain_degree + 1)]
    right = [statistics.mean(response[index][column] * value for index, value in zip(training, remainder)) for column in range(gain_degree + 1)]
    gains = bounded_gains(gram, right)
    predicted = [mean + scale * (dot(background[index], coefficients) + dot(response[index], gains)) for index in range(len(rows))]
    objective = statistics.mean(((observed[index] - predicted[index]) / scale) ** 2 for index in training)
    objective += penalty * dot(gains, gains) + sum(penalty / 10 * power * power * value * value for power, value in enumerate(coefficients))
    return {'预测': predicted, '训练均值': [mean] * len(rows), '二次趋势': [dot(row, trend) for row in quadratic],
            '尺度': scale, '目标': objective, '最大返回倍率': max(item[1] for item in physics), '物理反射率范围': [min(physical), max(physical)]}


def main():
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, TIME_LIMIT)
    current = load(SOURCE)
    statement = load(ROOT / '交接/结果声明_问题3.json')
    package_path = ROOT / '数据/问题3_公开复算输入/冻结条件.json'
    package = load(package_path)
    execution = (ROOT / '日志/执行_问题3_回炉3_仲裁后.log').read_text(encoding='utf-8')
    execution_record = json.loads(execution.strip().splitlines()[-1])
    gate = {'结果JSON数': len(list((ROOT / '求解/问题3/结果').glob('*.json'))),
            '八键逐项一致': {key: value == current['核心指标'].get(key) for key, value in statement['核心指标'].items()},
            '执行记录与结果八键一致': execution_record['核心指标'] == current['核心指标'],
            '结束状态': (ROOT / '日志/执行_问题3_回炉3_仲裁后.done').read_text().strip(),
            '复核追加返工标记存在': (ROOT / '日志/仲裁返工_问题3_回炉3_复核.done').exists(),
            '根目录历史结果不作当前版本': True}
    assert gate['结果JSON数'] > 0 and all(gate['八键逐项一致'].values()) and gate['执行记录与结果八键一致'] and gate['结束状态'] == 'rc=0'
    AUDIT['前置硬门'] = gate
    AUDIT['核心指标'] = current['核心指标']
    AUDIT['来源SHA256'] = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    AUDIT['核验输入声明SHA256'] = hashlib.sha256((ROOT / '交接/结果声明_问题3.json').read_bytes()).hexdigest()
    AUDIT['冻结输入SHA256'] = hashlib.sha256(package_path.read_bytes()).hexdigest()
    AUDIT['阶段'] = '前置硬门完成'
    save()
    event('流程事件', '仲裁后解读先核对版本化产物和数值环境',
          '八项声明在最新候选厚度结果同名键均存在且与正常结束记录一致；根目录三键仍为历史数。python3实际不能导入numpy。',
          '锁定声明指定v3r3结果，改用标准库直接读取附件并独立重算，不安装依赖、不补路径。',
          '求解结果:解读核验_回炉3_仲裁后/前置硬门')
    tables = {number: read_attachment(number) for number in range(1, 5)}
    raw_hashes = {f'附件{number}.xlsx': hashlib.sha256((ROOT / '数据' / f'附件{number}.xlsx').read_bytes()).hexdigest() for number in tables}
    assert raw_hashes == package['原始输入哈希']
    full = [index for index, row in enumerate(tables[1]) if 1200 <= row[1] <= 3800]
    selected = [full[position * (len(full) - 1) // 479] for position in range(480)]
    selected_rows = [tables[1][index] for index in selected]
    edges = [(selected_rows[index - 1][1] + selected_rows[index][1]) / 2 for index in range(40, 480, 40)]
    configurations = {1: {'训练': {1, 2, 4, 5, 7, 8, 10, 11}, '校准': {3, 9}, '测试': {6, 12}},
                      2: {'训练': {2, 3, 5, 6, 8, 9, 11, 12}, '校准': {4, 10}, '测试': {1, 7}}}
    splits = {}
    for fold, specification in configurations.items():
        guards = [edge for block, edge in enumerate(edges, 1) if (block in specification['训练']) != (block + 1 in specification['训练'])]
        groups = {name: [index for index, row in enumerate(selected_rows) if index // 40 + 1 in blocks and (name != '训练' or all(abs(row[1] - edge) >= 20 for edge in guards))] for name, blocks in specification.items()}
        assert all(not (set(groups[left]) & set(groups[right])) for left, right in itertools.combinations(groups, 2))
        splits[fold] = groups
    score_differences = []
    objective_differences = []
    coverage_differences = []
    physical_ranges = []
    predictive_ranges = []
    roundtrip_maxima = []
    checked_cases = []
    for specification in package['案例']:
        checkpoint()
        material, fold = specification['材料'], specification['折号']
        indices = full if fold == 0 else selected
        training = list(range(len(indices))) if fold == 0 else splits[fold]['训练']
        assert [tables[1][indices[index]][0] for index in training] == specification['训练源行']
        numbers = (3, 4) if material == '硅' else (1, 2)
        for model in ('两束', '完整往返'):
            record = next(row for row in current['案例'] if row['材料'] == material and row['折号'] == fold and row['模型'] == model)
            objectives = []
            for number, angle in zip(numbers, (10, 15)):
                rows = [tables[number][index] for index in indices]
                computed = independent_predictions(rows, training, record['最优']['参数向量'], specification['响应设置'], angle, model == '完整往返')
                objectives.append(computed['目标'])
                physical_ranges.append(computed['物理反射率范围'])
                predictive_ranges.append([min(computed['预测']), max(computed['预测'])])
                roundtrip_maxima.append(computed['最大返回倍率'])
                if fold == 0:
                    continue
                for scored_model in (model, '训练均值', '二次趋势'):
                    predictions = computed['预测'] if scored_model == model else computed[scored_model]
                    calibration_residuals = sorted(abs(predictions[index] - rows[index][2]) for index in splits[fold]['校准'])
                    half_width = calibration_residuals[min(math.ceil((len(calibration_residuals) + 1) * 0.9), len(calibration_residuals)) - 1]
                    for block in configurations[fold]['测试']:
                        members = list(range((block - 1) * 40, block * 40))
                        observed = next(row for row in current['逐块评分'] if (row['材料'], row['折号'], row['附件'], row['测试块'], row['模型']) == (material, fold, number, block, scored_model))
                        assert observed['源行'] == [rows[index][0] for index in members]
                        error = math.sqrt(statistics.mean((predictions[index] - rows[index][2]) ** 2 for index in members))
                        score_differences.append(abs(error / computed['尺度'] - observed['标准化均方根误差']))
                        interval = next(row for row in current['预测区间'] if (row['材料'], row['折号'], row['附件'], row['测试块'], row['模型']) == (material, fold, number, block, scored_model))
                        coverage = statistics.mean(abs(predictions[index] - rows[index][2]) <= half_width for index in members)
                        coverage_differences.append(abs(coverage - interval['经验覆盖率']))
            objective_differences.append(abs(statistics.mean(objectives) - record['最优']['训练目标']))
            checked_cases.append({'材料': material, '折号': fold, '模型': model, '独立目标': statistics.mean(objectives), '记录目标': record['最优']['训练目标']})
            AUDIT['独立逐案例目标'] = checked_cases
            save()
    assert max(score_differences) < 1e-7 and max(objective_differences) < 1e-7 and max(coverage_differences) == 0
    AUDIT['独立算术核验'] = {'目标最大绝对差': max(objective_differences), '标准化RMSE最大绝对差': max(score_differences),
                            '反射率覆盖率最大差': max(coverage_differences), '完整重算案例数': len(checked_cases),
                            '方式': '标准库从四附件重建源行、训练尺度、复场、约束响应及角块误差；给定已保存参数，不冒充独立全局搜索'}
    AUDIT['防泄漏'] = {'原附件哈希一致': True, '全量每角点数': len(full), '每角抽样点数': len(selected),
                      '逐折组数': {str(fold): {name: len(values) for name, values in groups.items()} for fold, groups in splits.items()},
                      '训练校准测试交集为空': True, '案例训练源行全部一致': True,
                      '代码核对': '升格3/求解.py:prepare_case、response_fit、spectral_seeds只读取训练反射率；score_case使用校准残差定区间、测试仅评分。求解_问题3.py:closure_search只以训练目标整合历史候选和选根。',
                      '边界': '非时间序列，使用连续波数留块而非时间前推；旧测试已参与开发，本轮只是探索性复评，不声明新增独立泛化。'}
    AUDIT['量纲'] = {'物理反射率最小': min(row[0] for row in physical_ranges), '物理反射率最大': max(row[1] for row in physical_ranges),
                    '最大返回倍率': max(roundtrip_maxima), '所有核心厚度正且有限': all(math.isfinite(value) and value > 0 for value in current['核心指标'].values()),
                    '响应预测反射率最小': min(row[0] for row in predictive_ranges), '响应预测反射率最大': max(row[1] for row in predictive_ranges),
                    '实际窗口观测反射率范围': [min(rows[index][2] for rows in tables.values() for index in full), max(rows[index][2] for rows in tables.values() for index in full)],
                    '原始反射率范围': [min(row[2] for rows in tables.values() for row in rows), max(row[2] for rows in tables.values() for row in rows)]}
    assert 0 <= AUDIT['量纲']['响应预测反射率最小'] <= AUDIT['量纲']['响应预测反射率最大'] <= 1
    assert AUDIT['量纲']['最大返回倍率'] < 1
    summaries = {}
    for material in ('硅', '碳化硅'):
        summaries[material] = []
        for fold in (1, 2):
            averages = {model: statistics.mean(row['标准化均方根误差'] for row in current['逐块评分'] if row['材料'] == material and row['折号'] == fold and row['模型'] == model)
                        for model in ('两束', '完整往返', '训练均值', '二次趋势')}
            improvements = {model: (1 - averages['完整往返'] / averages[model]) * 100 for model in ('两束', '训练均值', '二次趋势')}
            declared = current['分材料验证'][material]['逐折汇总'][fold - 1]
            assert all(abs(value - declared['完整相对基线下降_%'][key]) < 1e-12 for key, value in improvements.items())
            summaries[material].append({'折号': fold, '平均标准化均方根误差': averages, '完整相对基线下降_%': improvements})
    AUDIT['分材料验证'] = summaries
    members = current['范围成员']
    assert all(row['材料'] == '硅' and row['范围资格'] and row['约束']['最大约束违反量'] <= 1e-7 for row in members)
    minimum = min(row['厚度_um'] for row in members)
    maximum = max(row['厚度_um'] for row in members)
    assert minimum == current['核心指标']['硅已计算条件包络下限_um'] and maximum == current['核心指标']['硅已计算条件包络上限_um']
    AUDIT['包络复核'] = {'成员数': len(members), '下限_um': minimum, '上限_um': maximum,
                       '全部成员约束和资格通过': True, '已算条件数': current['已算条件数'], '计划条件数': current['计划条件数'],
                       '定义': '有限条件、两模型、近优分支的描述性极差，无概率覆盖含义；不把固定参数条件点改称自由最优'}
    pair_differences = []
    for row in current['同起点控制分解']:
        independent = row['扰动']['厚度_um'] - row['控制']['厚度_um']
        pair_differences.append(abs(independent - row['扣除控制厚度变化_um']))
    assert max(pair_differences) < 1e-12
    example = next(row for row in current['同起点控制分解'] if row['材料'] == '硅' and row['折号'] == 2 and row['情景'] == '衬底对比加20%' and row['原始起点序号'] == 11 and row['评估上限'] == 150)
    AUDIT['灵敏度'] = {'同起点配对数': len(pair_differences), '扣控制差最大重建差_um': max(pair_differences),
                      '示例控制厚度_um': example['控制']['厚度_um'], '示例扰动厚度_um': example['扰动']['厚度_um'],
                      '示例扣控制变化_um': example['扣除控制厚度变化_um'],
                      '示例相对控制变化_百分比': example['扣除控制厚度变化_um'] / example['控制']['厚度_um'] * 100,
                      '示例成对收敛': example['成对收敛'], '示例原始起点序号': example['原始起点序号'], '示例评估上限': example['评估上限'],
                      '示例来源序号': current['同起点控制分解'].index(example)}
    branch = next(row for row in current['共同起点池条件响应'] if row['折号'] == 2 and row['情景'] == '色散系数减20%')
    AUDIT['灵敏度']['色散减20%竞争分支差值下限_um'] = branch['竞争分支差值范围_um'][0]
    AUDIT['灵敏度']['色散减20%竞争分支差值上限_um'] = branch['竞争分支差值范围_um'][1]
    AUDIT['灵敏度']['方向稳定性解释'] = '同一扰动的近优竞争分支差值跨零，尚不能证明方向稳定；部分局部终点未收敛，不把跨零说成真实物理效应已被反向证实。'
    loss_rows = [row for row in current['条件重估'] if row.get('情景', '').startswith('有效损耗') and '最优' in row]
    assert all(abs(row['最优']['参数向量'][4] - row['固定参数值'][0]) < 1e-12 for row in loss_rows)
    relative_rows = [row for row in loss_rows if row['情景'] in ('有效损耗减20%', '有效损耗加20%')]
    anchors = {(row['材料'], row['折号']): row['固定参数锚点'][4] for row in package['案例']}
    for row in relative_rows:
        factor = 0.8 if row['情景'] == '有效损耗减20%' else 1.2
        assert abs(row['固定参数值'][0] - anchors[(row['材料'], row['折号'])] * factor) < 1e-12
    AUDIT['损耗标签核验'] = {'实际参数与固定值全部一致': True, '零损耗相对扰动仍为零': True,
                           '相对扰动按各案例原始锚点核对': True,
                           '硅次折原始损耗锚点': anchors[('硅', 2)],
                           '绝对增加单独列项': all(any(row['情景'] == label for row in loss_rows) for label in ('有效损耗绝对增加0→0.1', '有效损耗绝对增加0→0.2'))}
    event('流程事件', '修正解读核验中的损耗断言',
          '首次加强核验误把全部相对扰动案例视为零损耗；实物中硅次折原始锚点为0.06614993116422857，实际固定值等于该锚点乘0.8或1.2。',
          '逐案例按公开原始锚点核对，保留零锚点与非零锚点区别；这不是建模侧错误。', '求解结果:解读核验_回炉3_仲裁后/损耗标签核验')
    arbitration = load(ROOT / '交接/仲裁_问题3.json')
    red = load(ROOT / '交接/红队_问题3.json')
    AUDIT['仲裁适用性'] = {'对应核验输入声明SHA256': AUDIT['核验输入声明SHA256'],
                          '当前未齐数': len(red['分歧明细']), '应改方为建模的条目数': sum(row['应改方'] == '建模' for row in arbitration['逐项']),
                          '口径差异数': sum(row['裁定'] == '口径差异' for row in arbitration['逐项']),
                          '红队错数': sum(row['裁定'] == '红队错' for row in arbitration['逐项']),
                          '独立八键对齐完成': False,
                          '处理': '采纳最新四项口径差异及一项红队实现偏差定责，保留待独立同口径复算；不沿用旧上限算错归因，也不将红队null编为数字。'}
    core = current['核心指标']
    AUDIT['自检汇总'] = {'硅折间厚度最大最小比': core['硅折2完整往返条件厚度_um'] / core['硅折1完整往返条件厚度_um'],
                       '完整往返反射率测试经验覆盖率': statistics.mean(row['经验覆盖率'] for row in current['预测区间'] if row['模型'] == '完整往返'),
                       '硅全量两束厚度_um': current['案例'][0]['最优']['厚度_um'],
                       '硅全量完整往返对两束厚度变化_百分比': (core['硅全量完整往返厚度_um'] / current['案例'][0]['最优']['厚度_um'] - 1) * 100}
    AUDIT['五项正确性'] = {'量纲与数量级': '通过', '基线': '未通过', '交叉印证': '未通过', '防泄漏': '通过（仅探索性口径）', '灵敏度': '未通过'}
    AUDIT['质量裁定'] = 'FAIL'
    AUDIT['状态'] = '正常结束'
    AUDIT['阶段'] = '数值及五项协议核验完成'
    save()
    event('科学尝试', '在同一有限候选集合下从原始光谱重建两折的复场预测和朴素基线',
          f"硅第一折相对两束改善{summaries['硅'][0]['完整相对基线下降_%']['两束']:.8f}%，第二折相对两束为{summaries['硅'][1]['完整相对基线下降_%']['两束']:.8f}%，相对训练均值为{summaries['硅'][1]['完整相对基线下降_%']['训练均值']:.8f}%。",
          '分折陈述高阶收益，不用首折改善抵消次折劣化；保留全量条件厚度与分支范围。', '求解结果:分材料验证')
    event('科学尝试', '重新计算同一原始起点下衬底对比增加20%的厚度响应并扣除未扰动优化推进',
          f"同起点收敛控制为{example['控制']['厚度_um']}微米，扰动为{example['扰动']['厚度_um']}微米，扣控制变化{example['扣除控制厚度变化_um']}微米。",
          '只作为局部条件响应，不把不同分支混合后的最坏变化解释为固有材料灵敏度。', '求解结果:灵敏度')
    print(json.dumps({'状态': AUDIT['状态'], '质量裁定': AUDIT['质量裁定'], '核心指标': core,
                      '独立算术核验': AUDIT['独立算术核验'], '实际用时秒': AUDIT['实际用时秒']}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        AUDIT['状态'] = '核验中止'
        AUDIT['错误'] = f'{type(error).__name__}: {error}'
        save()
        event('流程事件', '执行仲裁后标准库独立核验', AUDIT['错误'], '保留已完成证据，定位后再核验，不凭中断报告通过。', '求解结果:解读核验_回炉3_仲裁后/错误')
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
