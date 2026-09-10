import copy
import hashlib
import importlib.util
import json
import math
import signal
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / '求解/问题3/结果/解读核验_G2返工.json'
START = time.monotonic()
BUDGET_SECONDS = 120
REPORT = {'问题': 3, '状态': '正在核验', '核验预算秒': BUDGET_SECONDS}


def load(relative):
    return json.loads((ROOT / relative).read_text(encoding='utf-8'))


def persist():
    REPORT['实际用时秒'] = time.monotonic() - START
    temporary = OUTPUT.with_suffix('.tmp')
    temporary.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(OUTPUT)


def deadline(signum, frame):
    REPORT['状态'] = '预算截止，保留已完成核验；不得据此宣称全部通过'
    persist()
    print(json.dumps({'状态': REPORT['状态'], '实际用时秒': REPORT['实际用时秒']}, ensure_ascii=False), flush=True)
    raise SystemExit(2)


def resolve(relative, keys):
    value = load(relative)
    for key in keys:
        value = value[key]
    return value


def close(left, right):
    return math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-12)


def main():
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(BUDGET_SECONDS)
    declaration = load('交接/结果声明_问题3.json')
    previous = load('求解/问题3/结果/解读核验_回炉1_仲裁后.json')
    evidence = ROOT / '日志/解读_问题3_G2返工_核验'
    evidence.mkdir(exist_ok=True)
    for name in ['结果解读_问题3.md', '结果声明_问题3.json', '返工单_问题3.json', '返工单_问题3.md']:
        source = ROOT / '交接' / name
        target = evidence / ('修改前_' + name)
        if source.exists() and not target.exists():
            target.write_bytes(source.read_bytes())
    REPORT['核心逐键来源'] = []
    for item in previous['核心逐键来源']:
        value = resolve(item['来源文件'], item['来源键'])
        assert isinstance(value, (int, float)) and math.isfinite(value)
        assert value == declaration['核心指标'][item['指标']]
        REPORT['核心逐键来源'].append({**item, '数值': value, '与声明一致': True})
    execution = json.loads((ROOT / '日志/执行_问题3_G2返工.log').read_text().strip().splitlines()[-1])
    assert execution['状态'] == '正常结束，保留本次条件数值'
    assert (ROOT / '日志/执行_问题3_G2返工.done').read_text().strip() == 'rc=0'
    REPORT['产物硬门'] = {'结果目录存在': OUTPUT.parent.is_dir(),
        '结果JSON数量': len(list(OUTPUT.parent.glob('*.json'))), '核心逐键核对数量': len(REPORT['核心逐键来源']),
        '执行日志': '日志/执行_问题3_G2返工.log', '正常结束': True, '退出码': 0}
    persist()

    fair = load('求解/问题3/结果/公平对照.json')
    red = load('求解/问题3/红队结果/复算结果.json')
    red_report = load('交接/红队_问题3.json')
    arbitration = load('交接/仲裁_问题3.json')
    comparisons = []
    for metric, value in declaration['核心指标'].items():
        independent = red['复算指标'][metric]
        assert independent == red_report['复算指标'][metric]
        difference = abs(value - independent) / max(abs(value), 1e-9)
        item = next(item for item in arbitration['逐项'] if item['指标'] == metric)
        assert item['消解状态'] in ['已消解', '已解释'] and item['消解证据']
        comparisons.append({'指标': metric, '建模值': value, '独立值': independent, '相对差': difference,
                            '阈内': difference <= 0.01, '仲裁状态有证据': True})
    REPORT['独立复算比对'] = {'逐项': comparisons, '阈内数量': sum(item['阈内'] for item in comparisons),
        '最大相对差': max(item['相对差'] for item in comparisons),
        '最大相对差_%': 100 * max(item['相对差'] for item in comparisons),
        '裁决范围': '仅历史八项；不将本次公平对照称为红队已独立复现'}
    persist()

    specification = importlib.util.spec_from_file_location('question3_audit_target', ROOT / '求解/问题3/求解_问题3.py')
    solver = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(solver)
    inputs = solver.load_inputs(public_only=True)
    assert inputs['hashes'] == fair['输入哈希']
    assert inputs['source_rows'] == fair['切分证据']['样本源行']
    assert inputs['folds'] == fair['切分证据']['折分']
    REPORT['防泄漏核验'] = []
    REPORT['公平比较复核'] = []
    REPORT['纯场与量纲复核'] = []
    rebuilt = copy.deepcopy(fair)
    for row in fair['案例']:
        fold = None if row['折号'] == 0 else next(item for item in inputs['folds'] if item['折号'] == row['折号'])
        coordinates = inputs['full_coordinates'] if fold is None else inputs['coordinates']
        values = inputs['full_values'][row['材料']] if fold is None else inputs['sampled'][row['材料']]
        case = solver.make_case(row['材料'], coordinates, values, fold)
        assert case['train_indices'] == row['训练样本序号_从零']
        for model, complete in [('两束', False), ('完整往返', True)]:
            parameters = tuple(row[model][key] for key in solver.PARAMETER_NAMES)
            assert solver.valid(parameters)
            candidate = solver.evaluate(parameters, case, complete)
            assert close(candidate['loss'], row[model]['训练标准化损失'])
            case['full' if complete else 'two'] = candidate
        full = case['full']
        physical_two = solver.physical_reflectance(full['parameters'], coordinates, False)
        full_pred = solver.prediction(full, case)
        two_pred = solver.prediction({**full, 'physical': physical_two}, case)
        field_difference = [math.sqrt(sum((left-right)**2 for left, right in zip(full_values, two_values)) / len(coordinates))
                            for full_values, two_values in zip(full['physical'], physical_two)]
        response_difference = [math.sqrt(sum((left-right)**2 for left, right in zip(full_values, two_values)) / len(coordinates))
                               for full_values, two_values in zip(full_pred, two_pred)]
        assert all(close(left, right) for left, right in zip(field_difference, row['纯场差异']['各角原始场反射率均方根差_比例']))
        assert all(close(left, right) for left, right in zip(response_difference, row['纯场差异']['各角固定响应预测均方根差_比例']))
        REPORT['纯场与量纲复核'].append({'材料': row['材料'], '折号': row['折号'], '参数物理约束满足': True,
            '完整往返预测最小值_比例': min(map(min, full_pred)), '完整往返预测最大值_比例': max(map(max, full_pred)),
            '纯场固定参数复核一致': True, '各角固定响应预测均方根差_比例': response_difference})
        if fold is None:
            persist()
            continue
        trace = row['搜索状态']
        assert trace['物理自由度_每模型'] == trace['响应自由度_每角每模型'] == 5
        assert trace['粗网格次数_每模型'] == trace['粗网格计划次数_每模型']
        assert len(trace['两束精修']) == len(trace['完整精修']) == len(trace['共同初值'])
        assert all(item['停止原因'] != '时间预算' for key in ['两束精修', '完整精修'] for item in trace[key])
        assert trace['搜索完整'] and trace['公平性成立']
        train = set(case['train_indices'])
        calibration = {index for block in fold['校准块'] for index in solver.block_indices(block)}
        test = {index for block in fold['测试块'] for index in solver.block_indices(block)}
        assert not train & calibration and not train & test and not calibration & test
        changed_values = [[value if index in train else value + 10 + index / 1000
                           for index, value in enumerate(series)] for series in values]
        altered = solver.make_case(row['材料'], coordinates, changed_values, fold)
        changed = solver.evaluate(full['parameters'], altered, True)
        assert changed['loss'] == full['loss'] and changed['models'] == full['models']
        test_only = [[value + 10 + index / 1000 if index in test else value
                      for index, value in enumerate(series)] for series in values]
        altered_test = solver.make_case(row['材料'], coordinates, test_only, fold)
        calibrated_before = solver.calibration_score(case, full)
        calibrated_after = solver.calibration_score(altered_test, solver.evaluate(full['parameters'], altered_test, True))
        assert calibrated_before == calibrated_after
        REPORT['防泄漏核验'].append({'材料': row['材料'], '折号': row['折号'], '训练数_每角': len(train),
            '校准数_每角': len(calibration), '测试数_每角': len(test), '三集合互斥': True,
            '改变非训练反射率后训练目标绝对差': abs(changed['loss'] - full['loss']),
            '改变测试反射率后校准分数绝对差': abs(calibrated_after - calibrated_before),
            '限制': '复用当前数学函数核验依赖；不是另一次独立拟合。折内预测不使用测试响应；跨折选择后误差不称新独立测试。'})
        scores = {}
        for model, candidate in [('两束', case['two']), ('完整往返', full)]:
            blocks = solver.score_prediction(case, candidate, model, [])
            for actual, recorded in zip(blocks, row['分块'][model]):
                for key in ['标准化均方根误差', '均方根误差_百分点', '经验覆盖率', '区间半宽_比例']:
                    assert close(actual[key], recorded[key])
            scores[model] = sum(item['标准化均方根误差'] for item in blocks) / len(blocks)
            assert close(scores[model], row['标准化均方根误差'][model])
            assert close(solver.calibration_score(case, candidate), row['校准标准化均方根误差'][model])
        mean_baseline = solver.baseline_score(case, '训练均值')
        trend_baseline = solver.baseline_score(case, '二次趋势')
        assert close(mean_baseline, row['均值基线误差'])
        assert close(trend_baseline, row['二次趋势基线误差'])
        selected = fair['模型选择'][row['材料']]['条件模型']
        summary = {'材料': row['材料'], '折号': row['折号'], '公平配对': row['搜索状态']['公平性成立'],
            '两束误差': scores['两束'], '完整往返误差': scores['完整往返'], '训练均值误差': mean_baseline,
            '二次趋势误差': trend_baseline, '完整往返相对两束下降_%': 100 * (1 - scores['完整往返'] / scores['两束']),
            '校准所选模型': selected, '所选模型误差': scores[selected],
            '所选模型相对均值下降_%': 100 * (1 - scores[selected] / mean_baseline),
            '所选模型相对二次趋势下降_%': 100 * (1 - scores[selected] / trend_baseline)}
        assert close(summary['完整往返相对两束下降_%'], row['相对下降_%'])
        REPORT['公平比较复核'].append(summary)
        persist()
    solver.select_conditional_models(rebuilt, inputs['folds'])
    assert rebuilt['模型选择'] == fair['模型选择']

    planned = {(material, scenario['折号'], scenario['情景']) for material in solver.MATERIAL_ORDER
               for scenario in inputs['contract']['硅条件包络']['扰动生成表']}
    actual = {(item['材料'], item['折号'], item['情景']) for item in fair['灵敏度_参数扰动']}
    missing = []
    for material, fold_number, label in sorted(planned - actual):
        base = next(item for item in fair['案例'] if item['材料'] == material and item['折号'] == fold_number)
        inapplicable = label in solver.LOSS_LABELS[3:] and base['完整往返']['有效往返损耗'] != 0
        missing.append({'材料': material, '折号': fold_number, '情景': label,
                        '基准损耗': base['完整往返']['有效往返损耗'], '零起点绝对扰动不适用': inapplicable})
    relative = []
    for item in fair['灵敏度_参数扰动']:
        assert item['状态'] == '已重估' and item['停止状态']['停止原因'] != '时间预算'
        assert close(item['厚度变化_um'], item['扰动后厚度_um'] - item['基准厚度_um'])
        if item['情景'] in solver.LOSS_LABELS:
            control = next(control for control in fair['灵敏度_参数扰动'] if
                control['材料'] == item['材料'] and control['折号'] == item['折号'] and control['情景'] == solver.LOSS_LABELS[0])
            assert close(item['扣除控制厚度变化_um'], item['扰动后厚度_um'] - control['扰动后厚度_um'])
        if item['情景'] in solver.LOSS_LABELS[1:3]:
            relative.append(abs(item['扣除控制厚度变化_um']))
    REPORT['灵敏度核验'] = {'名义计划数': len(planned), '实际已算数': len(actual), '缺列明细': missing,
        '适用情景缺失数': sum(not item['零起点绝对扰动不适用'] for item in missing),
        '相对损耗最大配对厚度变化_um': max(relative),
        '已算扰动最大厚度绝对相对变化_%': max(abs(item['相对变化_%']) for item in fair['灵敏度_参数扰动']),
        '范围': '当前完整往返条件局部重估；不验证所选两束模型灵敏度或高阶收益稳定性。'}
    REPORT['场级数最大绝对误差'] = max(item['场级数核验']['几何级数与闭式最大绝对误差'] for item in fair['案例'])
    REPORT['源文件摘要'] = {relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() for relative in
        ['求解/问题3/结果/公平对照.json', '求解/问题3/结果/厚度结果.json', '求解/问题3/求解_问题3.py',
         '求解/问题3/红队结果/复算结果.json', '交接/仲裁_问题3.json']}
    REPORT['状态'] = '数值与依赖核验完成，待形成五项裁定'
    persist()
    signal.alarm(0)
    print(json.dumps({'状态': REPORT['状态'], '实际用时秒': REPORT['实际用时秒'],
        '红队对齐数': REPORT['独立复算比对']['阈内数量'], '公平复核案例数': len(REPORT['公平比较复核']),
        '灵敏度核验': REPORT['灵敏度核验']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        REPORT['状态'] = '核验异常，保留已完成部分'
        REPORT['错误'] = f'{type(error).__name__}: {error}'
        persist()
        print(json.dumps({'状态': REPORT['状态'], '错误': REPORT['错误']}, ensure_ascii=False), flush=True)
        raise
