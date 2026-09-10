from pathlib import Path
import csv
import hashlib
import json
import math
import signal


ROOT = Path(__file__).resolve().parents[2]


def verify_data():
    result = ROOT / '求解/问题3/结果'
    comparison = json.loads((result / '同口径对照.json').read_text(encoding='utf-8'))
    fair = json.loads((result / '公平对照.json').read_text(encoding='utf-8'))
    thickness = json.loads((result / '厚度结果.json').read_text(encoding='utf-8'))
    if comparison['逐块评分'] != fair['逐块评分'] or thickness['核心指标'] != fair['核心指标']:
        raise ValueError('同口径对照、厚度与公平对照不是同版数据')
    cases = {(row['材料'], row['折号']): row for row in fair['案例']}
    predictions, paired, gaps, raw_gaps = {}, {}, [], []
    for score in comparison['逐块评分']:
        case_key = score['材料'], score['折号']
        if case_key not in predictions:
            path = result / f'预测_{case_key[0]}_折{case_key[1]}.csv'
            with path.open(encoding='utf-8-sig', newline='') as stream:
                predictions[case_key] = list(csv.DictReader(stream))
        rows = sorted((row for row in predictions[case_key] if row['用途'] == '测试'
                       and int(row['附件']) == score['附件'] and int(row['原始块']) == score['测试块']),
                      key=lambda row: int(row['源行']))
        if len(rows) != score['测试点数'] or [int(row['源行']) for row in rows] != score['源行']:
            raise ValueError('预测CSV的测试点数或有序源行不匹配')
        error = math.sqrt(sum((float(row[score['模型']]) - float(row['反射率_比例'])) ** 2
                              for row in rows) / len(rows))
        scale = cases[case_key]['训练尺度_比例'][0 if score['入射角_度'] == 10 else 1]
        raw_gaps.append(abs(error - score['均方根误差_反射率比例']))
        gaps.append(abs(error / scale - score['标准化均方根误差']))
        pair_key = score['材料'], score['折号'], score['附件'], score['测试块']
        pair = paired.setdefault(pair_key, {})
        if score['模型'] in pair:
            raise ValueError('模型角块评分键重复')
        pair[score['模型']] = score
    if len(gaps) != 64 or max(gaps) > 1e-10 or max(raw_gaps) > 1e-12:
        raise ValueError('64项模型角块的CSV重评分未通过')
    if len(paired) != 16 or any(set(pair) != {'两束', '完整往返', '训练均值', '二次趋势'} for pair in paired.values()):
        raise ValueError('16个角块未完整覆盖四种模型')
    improvement = {}
    for material in ('硅', '碳化硅'):
        selected = [pair for key, pair in paired.items() if key[0] == material]
        improvement[material] = {'角块数': len(selected), '正向改善数': sum(
            pair['两束']['标准化均方根误差'] > pair['完整往返']['标准化均方根误差'] for pair in selected)}
    block_six = {f'{angle}度': {'测试点数': paired['硅', 1, attachment, 6]['两束']['测试点数'],
                              '两束误差_百分点': paired['硅', 1, attachment, 6]['两束']['均方根误差_反射率比例'] * 100,
                              '完整误差_百分点': paired['硅', 1, attachment, 6]['完整往返']['均方根误差_反射率比例'] * 100}
                 for angle, attachment in ((10, 3), (15, 4))}
    with (ROOT / '求解/问题2/结果/留段预测.csv').open(encoding='utf-8-sig', newline='') as stream:
        residuals = [row for row in csv.DictReader(stream) if row['类型'] == '主方法测试块']
    residual_gap = max(abs(float(row['残差_比例']) -
                           (float(row['观测反射率_比例']) - float(row['预测反射率_比例']))) for row in residuals)
    if len(residuals) != 320 or residual_gap != 0:
        raise ValueError('问题二320点的实测减预测残差不一致')
    extreme = min(residuals, key=lambda row: float(row['残差_比例']))
    paths = [result / name for name in ('同口径对照.json', '公平对照.json', '厚度结果.json', '灵敏度.json', '条件判定.json')]
    paths += sorted(result.glob('预测_*.csv'))
    paths += [ROOT / f'求解/问题1/原型结果/路线{number}.json' for number in (1, 2, 3)]
    paths += [ROOT / '求解/问题1/结果/合成逐例.csv', ROOT / '求解/问题2/结果/留段预测.csv']
    return {'模型角块数': len(gaps), '原尺度误差最大差': max(raw_gaps), '标准化误差最大差': max(gaps),
            '当前完整往返平均标准化误差': sum(pair['完整往返']['标准化均方根误差'] for pair in paired.values()) / len(paired),
            '图11逐角结果': block_six, '图12分材料': improvement, '核心指标': thickness['核心指标'],
            '图8测试点数': len(residuals), '图8残差最大差': residual_gap,
            '图8极值波数_cm^-1': float(extreme['波数_cm^-1']),
            '图8极值残差_百分点': float(extreme['残差_比例']) * 100,
            '数据源摘要': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}}


if __name__ == '__main__':
    signal.alarm(60)
    print(json.dumps(verify_data(), ensure_ascii=False, indent=2, allow_nan=False))
