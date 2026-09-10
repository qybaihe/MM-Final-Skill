import ast
import hashlib
import json
import math
import re
import signal
import time
from types import SimpleNamespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / '求解/问题3/结果/解读核验_G2新数.json'
START = time.monotonic()
BUDGET = 180
REPORT = {'问题': 3, '状态': '核验中', '核验预算秒': BUDGET}
FAIR = '求解/问题3/结果/公平对照.json'
CORE_PATHS = [
    '案例[0].完整往返.厚度_um', '案例[1].完整往返.厚度_um',
    '案例[2].完整往返.厚度_um', '厚度条件范围_um.硅[0]',
    '厚度条件范围_um.硅[1]', '碳化硅正式基准.厚度_um',
    '碳化硅正式基准.条件范围_um[0]', '碳化硅正式基准.条件范围_um[1]']


def load(relative):
    return json.loads((ROOT / relative).read_text(encoding='utf-8'))


def save(relative, value):
    target = ROOT / relative
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(target)


def persist():
    REPORT['实际用时秒'] = time.monotonic() - START
    save(OUTPUT, REPORT)


def deadline(signum, frame):
    REPORT['状态'] = '核验达到预算，已保存部分证据，不得据此通过'
    persist()
    print(REPORT['状态'], flush=True)
    raise SystemExit(2)


def resolve(relative, path):
    value = load(relative)
    for token in re.split(r'\.|\[|\]', path):
        if token:
            value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def equal(actual, recorded):
    assert math.isclose(actual, recorded, rel_tol=1e-9, abs_tol=1e-11), (actual, recorded)


def restore_candidate(solver, record, case):
    parameters = tuple(record[key] for key in solver.PARAMETER_NAMES)
    physical = solver.physical_reflectance(parameters, case['coordinates'], record['模型'] == '完整往返')
    assert physical is not None
    models = [{'baseline': response['二次基线系数'], 'gains': response['一次幅值端点']}
              for response in record['各角度校准']]
    candidate = {'parameters': parameters, 'physical': physical, 'models': models}
    predictions = solver.prediction(candidate, case)
    candidate['loss'] = sum(sum((predictions[angle][index] - case['values'][angle][index]) ** 2
        for index in case['train_indices']) / len(case['train_indices']) / case['info'][angle]['scale'] ** 2
        for angle in range(2)) / 2
    return candidate


def verify_inner_selection(solver, case, trace):
    diagnostic = trace['训练内响应选择']
    allowed = list(case['train_indices'])
    if len(allowed) > 480:
        allowed = [allowed[index * (len(allowed) - 1) // 479] for index in range(480)]
    position = {source: index for index, source in enumerate(allowed)}
    inner_cases = {}
    checks = []
    for split in diagnostic['内层切分']:
        train = split['训练样本序号_原案例从零']
        validation = split['验证样本序号_原案例从零']
        assert set(train) <= set(allowed) and set(validation) <= set(allowed)
        assert not set(train) & set(validation)
        coordinates = [case['coordinates'][index] for index in allowed]
        values = [[series[index] for index in allowed] for series in case['values']]
        inner = solver.make_case(case['材料'], coordinates, values, None)
        inner['train_indices'] = [position[index] for index in train]
        inner['info'] = [dict(solver.training_info(inner['x'], series, inner['train_indices']),
                             x=inner['x'], y=series) for series in values]
        validation_indices = [position[index] for index in validation]
        lower = min(coordinates[index] for index in validation_indices)
        upper = max(coordinates[index] for index in validation_indices)
        assert all(coordinates[index] < lower - 20 or coordinates[index] > upper + 20
                   for index in inner['train_indices'])
        inner_cases[split['内折']] = (inner, validation_indices)
        checks.append({'内折': split['内折'], '训练数': len(train), '验证数': len(validation),
                       '均为外训练子集': True, '互斥且保护带满足': True})
    assert diagnostic['选型完整'] and len(diagnostic['候选']) == 6
    for choice in diagnostic['候选']:
        all_scores = []
        for inner_result in choice['内折']:
            inner, validation = inner_cases[inner_result['内折']]
            assert inner_result['搜索状态']['公平性成立']
            for model in ['两束', '完整往返']:
                candidate = restore_candidate(solver, {**inner_result[model], '模型': model}, inner)
                equal(candidate['loss'], inner_result[model]['训练标准化损失'])
                prediction = solver.prediction(candidate, inner)
                scores = [math.sqrt(sum((prediction[angle][index] - inner['values'][angle][index]) ** 2
                    for index in validation) / len(validation)) / inner['info'][angle]['scale'] for angle in range(2)]
                for actual, recorded in zip(scores, inner_result['物理模型误差_逐角'][model], strict=True):
                    equal(actual, recorded)
                all_scores.extend(scores)
        equal(sum(all_scores) / len(all_scores), choice['验证均值'])
    selected = min(diagnostic['候选'], key=lambda item: (item['验证均值'],
        item['背景阶数'] + item['幅值阶数'], item['背景阶数']))
    assert [selected['背景阶数'], selected['幅值阶数']] == diagnostic['所选响应阶数'] == case['response']
    return {'材料': case['材料'], '折号': case['折号'], '内层切分': checks,
            '候选数': len(diagnostic['候选']), '候选内验证误差复算一致': True,
            '所选响应阶数': diagnostic['所选响应阶数']}


def main():
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(BUDGET)
    snapshot = ROOT / '日志/解读_问题3_G2新数_核验'
    snapshot.mkdir(exist_ok=True)
    for name in ['结果声明_问题3.json', '结果解读_问题3.md', '配对裁定_问题3_G2返工.json',
                 '返工单_问题3.json', '返工单_问题3.md']:
        target = snapshot / ('修改前_' + name)
        if not target.exists():
            target.write_bytes((ROOT / '交接' / name).read_bytes())
    declaration = load('交接/结果声明_问题3.json')
    assert OUTPUT.parent.is_dir() and list(OUTPUT.parent.glob('*.json'))
    execution = json.loads((ROOT / '日志/执行_问题3_G2返工.log').read_text().strip().splitlines()[-1])
    assert execution['状态'] == '正常结束，保留本次条件数值'
    assert (ROOT / '日志/执行_问题3_G2返工.done').read_text().strip() == 'rc=0'
    REPORT['核心逐键来源'] = []
    for (metric, value), path in zip(declaration['核心指标'].items(), CORE_PATHS, strict=True):
        assert isinstance(value, (int, float)) and math.isfinite(value)
        assert value == resolve(FAIR, path), metric
        REPORT['核心逐键来源'].append({'指标': metric, '数值': value, '来源文件': FAIR, '键名': path})
    REPORT['自检逐键来源'] = []
    for metric, value in declaration['自检指标'].items():
        source = declaration['口径说明']['自检指标口径']['逐指标来源'][metric]
        assert value == resolve(source['来源文件'], source['键名']), metric
        REPORT['自检逐键来源'].append({'指标': metric, '数值': value, **source})
    REPORT['产物硬门'] = {'判定': 'PASS', '结果目录存在': True,
        'JSON数量': len(list(OUTPUT.parent.glob('*.json'))), '核心核对数': len(REPORT['核心逐键来源']),
        '自检核对数': len(REPORT['自检逐键来源']), '正常结束': True, '退出码': 0,
        '执行日志': '日志/执行_问题3_G2返工.log'}
    persist()
    experiments = load('交接/实验记录.json')
    experiments.append({'类别': '流程事件', '问题': 3,
        '尝试': '核对G2新数产物与引用键；中文jq裸键解析失败后改用方括号字符串键',
        '现象': '执行日志正常结束且rc=0，8项核心及23项自检均逐键匹配现存结果',
        '决定': '先保存硬门证据，再复核当前版本；保留旧产物快照，不继承旧核验数值',
        '依据': '求解/问题3/结果/解读核验_G2新数.json:产物硬门'})
    save('交接/实验记录.json', experiments)

    fair = load(FAIR)
    independent = load('求解/问题3/红队结果/复算结果.json')
    red_report = load('交接/红队_问题3.json')
    arbitration = load('交接/仲裁_问题3.json')
    declaration_hash = hashlib.sha256((ROOT / '交接/结果声明_问题3.json').read_bytes()).hexdigest()
    snapshot_declaration_path = snapshot / '修改前_结果声明_问题3.json'
    snapshot_declaration = json.loads(snapshot_declaration_path.read_text(encoding='utf-8'))
    assert independent['声明文件哈希'] == hashlib.sha256(snapshot_declaration_path.read_bytes()).hexdigest()
    original_contract = json.loads(json.dumps(snapshot_declaration['口径说明']['独立复算规范']))
    current_contract = json.loads(json.dumps(declaration['口径说明']['独立复算规范']))
    original_contract.pop('执行与验收状态')
    current_contract.pop('执行与验收状态')
    assert original_contract == current_contract
    assert declaration['核心指标'] == snapshot_declaration['核心指标']
    assert declaration['自检指标'] == snapshot_declaration['自检指标']
    assert independent['估计器版本'] == declaration['口径说明']['独立复算规范']['版本']
    assert independent['估计器版本'].endswith(fair['估计器版本'])
    assert independent['执行状态'] == '计算完成'
    comparisons = []
    for metric, value in declaration['核心指标'].items():
        other = independent['复算指标'][metric]
        assert other == red_report['复算指标'][metric]
        difference = abs(value - other) / max(abs(value), 1e-9)
        old = next(item for item in arbitration['逐项'] if item['指标'] == metric)
        assert old['消解状态'] in ['已消解', '已解释'] and old['消解证据']
        comparisons.append({'指标': metric, '建模值': value, '复算值': other,
            '相对差': difference, '阈内': difference <= 0.01})
    REPORT['独立复算比对'] = {'逐项': comparisons, '阈内数量': sum(item['阈内'] for item in comparisons),
        '最大相对差_%': 100 * max(item['相对差'] for item in comparisons),
        '红队所用声明SHA256': independent['声明文件哈希'], '当前声明SHA256': declaration_hash,
        '公开数学口径及核心自检未变': True, '估计器版本': independent['估计器版本']}
    REPORT['源文件摘要'] = {relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        for relative in [FAIR, '求解/问题3/求解_问题3.py', '求解/问题3/红队结果/复算结果.json']}
    persist()

    source_path = ROOT / '求解/问题3/求解_问题3.py'
    source = ast.parse(source_path.read_text(encoding='utf-8'))
    source.body = [node for node in source.body if not (isinstance(node, ast.Import)
                  and any(alias.name == 'numpy' for alias in node.names))]
    namespace = {'__file__': str(source_path), '__name__': 'question3_readonly_helpers'}
    exec(compile(source, str(source_path), 'exec'), namespace)
    solver = SimpleNamespace(**namespace)
    REPORT['复核方法边界'] = ('当前python3无numpy；不安装依赖、不访问工作根外解释器。'
        '在内存抽取本地标准库函数，用保存的物理参数和响应系数从原附件重算预测、训练损失及留段误差；'
        '不重跑数值优化，不冒充独立拟合。独立厚度复算依据当前红队实物。')
    inputs = solver.load_inputs(public_only=True)
    assert inputs['hashes'] == fair['输入哈希']
    assert inputs['source_rows'] == fair['切分证据']['样本源行']
    REPORT['折内复核'] = []
    REPORT['数值量纲复核'] = []
    REPORT['训练内选择复核'] = []
    REPORT['场级数复核'] = []
    parameter_keys = ['厚度_um', '参考折射率', '色散系数', '衬底折射率对比', '有效往返损耗']
    for row in fair['案例']:
        fold = None if row['折号'] == 0 else next(item for item in inputs['folds'] if item['折号'] == row['折号'])
        coordinates = inputs['full_coordinates'] if fold is None else inputs['coordinates']
        values = inputs['full_values'][row['材料']] if fold is None else inputs['sampled'][row['材料']]
        case = solver.make_case(row['材料'], coordinates, values, fold)
        case['response'] = row['完整往返']['响应阶数_背景与幅值']
        assert case['train_indices'] == row['训练样本序号_从零']
        REPORT['训练内选择复核'].append(verify_inner_selection(solver, case, row['搜索状态']))
        candidates = {}
        for model, complete in [('两束', False), ('完整往返', True)]:
            parameters = [row[model][key] for key in parameter_keys]
            assert solver.valid(parameters)
            candidate = restore_candidate(solver, {**row[model], '模型': model}, case)
            equal(candidate['loss'], row[model]['训练标准化损失'])
            predicted = solver.prediction(candidate, case)
            assert all(math.isfinite(value) for series in predicted for value in series)
            REPORT['数值量纲复核'].append({'材料': row['材料'], '折号': row['折号'], '模型': model,
                '预测最小值_比例': min(map(min, predicted)), '预测最大值_比例': max(map(max, predicted)),
                '物理参数可行': True, '训练损失复核一致': True})
            candidates[model] = candidate
        case['full'] = candidates['完整往返']
        field = solver.field_check(case)
        equal(field['几何级数与闭式最大绝对误差'], row['场级数核验']['几何级数与闭式最大绝对误差'])
        assert field['通过']
        REPORT['场级数复核'].append(field)
        if fold is None:
            persist()
            continue
        train = set(case['train_indices'])
        calibration = {index for block in fold['校准块'] for index in solver.block_indices(block)}
        test = {index for block in fold['测试块'] for index in solver.block_indices(block)}
        assert not train & calibration and not train & test and not calibration & test
        changed_values = [[value if index in train else value + 10 + index / 1000
                           for index, value in enumerate(series)] for series in values]
        altered = solver.make_case(row['材料'], coordinates, changed_values, fold)
        altered['response'] = list(case['response'])
        original = candidates['完整往返']
        changed = restore_candidate(solver, {**row['完整往返'], '模型': '完整往返'}, altered)
        assert changed['loss'] == original['loss'] and changed['models'] == original['models']
        test_values = [[value + 10 if index in test else value for index, value in enumerate(series)] for series in values]
        test_altered = solver.make_case(row['材料'], coordinates, test_values, fold)
        test_altered['response'] = list(case['response'])
        score_before = solver.calibration_score(case, original)
        score_after = solver.calibration_score(test_altered,
            restore_candidate(solver, {**row['完整往返'], '模型': '完整往返'}, test_altered))
        assert score_before == score_after
        scores = {}
        for model, candidate in candidates.items():
            blocks = solver.score_prediction(case, candidate, model, [])
            assert len(blocks) == len(row['分块'][model])
            for actual, saved in zip(blocks, row['分块'][model], strict=True):
                for key in ['标准化均方根误差', '均方根误差_百分点', '经验覆盖率']:
                    equal(actual[key], saved[key])
            scores[model] = sum(item['标准化均方根误差'] for item in blocks) / len(blocks)
            equal(scores[model], row['标准化均方根误差'][model])
            equal(solver.calibration_score(case, candidate), row['校准标准化均方根误差'][model])
        selected = fair['模型选择'][row['材料']]['条件模型']
        baselines = {mode: solver.baseline_score(case, mode) for mode in ['训练均值', '二次趋势']}
        equal(baselines['训练均值'], row['均值基线误差'])
        equal(baselines['二次趋势'], row['二次趋势基线误差'])
        differences = {mode: 100 * (1 - scores[selected] / value) for mode, value in baselines.items()}
        for mode, difference in differences.items():
            equal(difference, fair['分材料验证'][row['材料']]['所选模型相对基线下降_%'][mode][row['折号'] - 1])
        trace = row['搜索状态']
        assert trace['公平性成立'] and trace['搜索完整']
        assert trace['响应自由度_每角每模型'] == sum(case['response']) + 2
        assert len(trace['两束精修']) == len(trace['完整精修']) == len(trace['共同初值'])
        REPORT['折内复核'].append({'材料': row['材料'], '折号': row['折号'], '误差': scores,
            '朴素基线误差': baselines, '所选模型': selected, '所选相对基线下降_%': differences,
            '三集合互斥': True, '训练数': len(train), '校准数': len(calibration), '测试数': len(test),
            '非训练响应改变后训练损失差': abs(changed['loss'] - original['loss']),
            '测试响应改变后校准误差差': abs(score_before - score_after), '当前公平搜索已完成': True})
        persist()
    sensitivity = fair['灵敏度_参数扰动']
    REPORT['灵敏度复核'] = {}
    generated = declaration['口径说明']['独立复算规范']['硅条件包络']['扰动生成表']
    missing = []
    for material in ['硅', '碳化硅']:
        for scene in generated:
            found = any(item['材料'] == material and item['折号'] == scene['折号']
                        and item['情景'] == scene['情景'] for item in sensitivity)
            if not found:
                case_row = next(item for item in fair['案例'] if item['材料'] == material and item['折号'] == scene['折号'])
                loss = case_row['完整往返']['有效往返损耗']
                assert scene['情景'] in solver.LOSS_LABELS[3:] and loss != 0
                missing.append({'材料': material, '折号': scene['折号'], '情景': scene['情景'],
                                '基准损耗': loss, '原因': '仅适用于零基准，当前不适用'})
    assert all(item['状态'] == '已重估' and item['停止状态']['停止原因'] != '时间预算' for item in sensitivity)
    REPORT['灵敏度适用性'] = {'名义数': fair['计划灵敏度数'], '已算数': len(sensitivity),
        '不适用数': len(missing), '适用缺失数': 0, '不适用明细': missing,
        '原始全部完整标志': fair['灵敏度全部完整']}
    for material in ['硅', '碳化硅']:
        rows = [item for item in sensitivity if item['材料'] == material]
        envelope_members = [row[model]['厚度_um'] for row in fair['案例'] if row['材料'] == material
                            for model in ['两束', '完整往返']]
        envelope_members.extend(item['扰动后厚度_um'] for item in rows)
        assert [min(envelope_members), max(envelope_members)] == fair['厚度条件范围_um'][material]
        maximum = max(rows, key=lambda item: abs(item['相对变化_%']))
        REPORT['灵敏度复核'][material] = {'已算情景数': len(rows), '包络成员数': len(envelope_members),
            '最大绝对相对变化_%': abs(maximum['相对变化_%']), '对应情景': maximum['情景'],
            '对应折': maximum['折号'], '最小厚度_um': min(envelope_members), '最大厚度_um': max(envelope_members),
            '范围重聚合一致': True}
    previous = load('求解/问题3/结果/解读核验_G2返工.json')
    REPORT['版本比较'] = []
    for old in previous['公平比较复核']:
        current = next(item for item in REPORT['折内复核'] if item['材料'] == old['材料'] and item['折号'] == old['折号'])
        current_error = current['误差'][current['所选模型']]
        REPORT['版本比较'].append({'材料': old['材料'], '折号': old['折号'],
            '上一版所选模型误差': old['所选模型误差'], '当前所选模型误差': current_error,
            '误差增加_%': 100 * (current_error / old['所选模型误差'] - 1)})
    REPORT['五项协议'] = {
        '量纲与数量级': {'判定': 'PASS', '说明': '物理参数合法，保存系数重新计算的反射率与误差匹配原附件；厚度范围仅为条件集合。'},
        '基线': {'判定': 'FAIL', '说明': '硅所选完整往返在两折均差于训练均值和二次趋势，不由碳化硅收益或红队数值对齐抵消。'},
        '交叉印证': {'判定': 'FAIL', '说明': '硅双折及逐附件平均收益同向这一子项已改善，但逐块存在反向，附件1高阶收益仍跨折变号；不得再称两材料折均收益都变号。'},
        '防泄漏': {'判定': 'PASS', '说明': '仅指折内隔离：内选型均在外训练成员，外校准/测试互斥，尺度仅训练计算；旧外层测试已参与开发，不能宣称独立验证。'},
        '灵敏度': {'判定': 'PASS', '说明': '仅指已定义局部条件估计：54适用项完成，2零基准绝对损耗项不适用，最大变化约3.43%；近优分支宽，不能推广为唯一厚度或稳定高阶检出。'}}
    REPORT['总判定'] = 'FAIL'
    REPORT['不通过协议'] = ['基线', '交叉印证']
    REPORT['状态'] = '核验完成'
    persist()
    signal.alarm(0)
    print(json.dumps({'状态': REPORT['状态'], '产物硬门': REPORT['产物硬门'],
        '独立对齐数': REPORT['独立复算比对']['阈内数量'], '折内复核数': len(REPORT['折内复核']),
        '灵敏度复核': REPORT['灵敏度复核'], '实际用时秒': REPORT['实际用时秒']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        REPORT['状态'] = '核验异常，保留已有证据'
        REPORT['异常'] = repr(error)
        persist()
        experiments = load('交接/实验记录.json')
        experiments.append({'类别': '流程事件', '问题': 3,
            '尝试': '运行G2新数解读核验', '现象': repr(error),
            '决定': '保存异常和已完成证据，修正核验后重跑，不把核验器失败充当数值正确性结论',
            '依据': '求解/问题3/结果/解读核验_G2新数.json:异常'})
        save('交接/实验记录.json', experiments)
        raise
