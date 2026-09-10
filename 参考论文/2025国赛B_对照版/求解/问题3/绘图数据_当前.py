from pathlib import Path
import csv
import hashlib
import json
import math
from 绘图数据_正式 import (
    publication_data, publication_predictions, publication_summary, publication_metadata,
)


RESULT = Path(__file__).resolve().parent / '结果'


def current_data():
    fair = json.loads((RESULT / '公平对照.json').read_text(encoding='utf-8'))
    comparison = json.loads((RESULT / '同口径对照.json').read_text(encoding='utf-8'))
    thickness = json.loads((RESULT / '厚度结果.json').read_text(encoding='utf-8'))
    sensitivity = json.loads((RESULT / '灵敏度.json').read_text(encoding='utf-8'))
    if comparison['逐块评分'] != fair['逐块评分']:
        raise ValueError('同口径对照与公平对照的逐块评分不一致')
    if thickness['核心指标'] != fair['核心指标']:
        raise ValueError('厚度核心指标与公平对照不一致')
    if sensitivity['灵敏度_参数扰动'] != fair['灵敏度_参数扰动']:
        raise ValueError('灵敏度与公平对照不一致')
    return fair, comparison, thickness, sensitivity


def paired_predictions(fair, comparison):
    cases = {(row['材料'], row['折号']): row for row in fair['案例']}
    grouped = {}
    cache = {}
    for score in comparison['逐块评分']:
        if score['模型'] not in ('两束', '完整往返'):
            continue
        material, fold = score['材料'], score['折号']
        attachment, block = score['附件'], score['测试块']
        if (material, fold) not in cache:
            path = RESULT / f'预测_{material}_折{fold}.csv'
            with path.open(encoding='utf-8-sig', newline='') as stream:
                cache[material, fold] = list(csv.DictReader(stream))
        rows = sorted((row for row in cache[material, fold]
                       if int(row['附件']) == attachment and int(row['原始块']) == block
                       and row['用途'] == '测试'), key=lambda row: int(row['源行']))
        source_rows = [int(row['源行']) for row in rows]
        if len(rows) != score['测试点数'] or source_rows != score['源行']:
            raise ValueError(f'预测与评分源行不一致：{material}/{fold}/{attachment}/{block}')
        error = math.sqrt(sum((float(row[score['模型']]) - float(row['反射率_比例'])) ** 2
                              for row in rows) / len(rows))
        case = cases[material, fold]
        scale = case['训练尺度_比例'][0 if score['入射角_度'] == 10.0 else 1]
        if not math.isclose(error, score['均方根误差_反射率比例'], rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError('预测CSV与逐块原尺度误差不一致')
        if not math.isclose(error / scale, score['标准化均方根误差'], rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError('预测CSV与逐块标准化误差不一致')
        key = (material, fold, attachment, block)
        pair = grouped.setdefault(key, {'记录': rows, '评分': {}, '角度': score['入射角_度']})
        if score['模型'] in pair['评分']:
            raise ValueError(f'重复评分键：{key}/{score["模型"]}')
        pair['评分'][score['模型']] = score
    if not grouped or any(set(pair['评分']) != {'两束', '完整往返'} for pair in grouped.values()):
        raise ValueError('两束与完整往返的角块配对不完整')
    return grouped


def save_summary(name, values):
    sources = ['公平对照.json', '同口径对照.json', '厚度结果.json', '灵敏度.json']
    sources += sorted(path.name for path in RESULT.glob('预测_*.csv'))
    values = {'图名': name, '数据源摘要': {
        name: hashlib.sha256((RESULT / name).read_bytes()).hexdigest() for name in sources
    }, **values}
    (RESULT / f'图核验_{name}.json').write_text(
        json.dumps(values, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def figure_metadata(name):
    summary = json.loads((RESULT / f'图核验_{name}.json').read_text(encoding='utf-8'))
    for source, expected in summary['数据源摘要'].items():
        if hashlib.sha256((RESULT / source).read_bytes()).hexdigest() != expected:
            raise ValueError(f'计算与成图之间数据变化：{source}')
    script = RESULT.parent / f'绘图_{name}.py'
    return {'图名': name, '图核验': json.dumps(summary, ensure_ascii=False, allow_nan=False),
            '绘图脚本SHA256': hashlib.sha256(script.read_bytes()).hexdigest()}
