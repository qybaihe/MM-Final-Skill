from pathlib import Path
import csv
import hashlib
import json
import math


ROOT = Path(__file__).resolve().parents[2]
RESULT = Path(__file__).resolve().parent / '结果'
SOURCE = RESULT / '回炉轮3候选/仲裁闭环/20260910_131501_81623_建模公式'
SNAPSHOT = SOURCE / '厚度结果.json'
MANIFEST = SOURCE / '预测交付.json'
MODELS = {'两束', '完整往返', '训练均值', '二次趋势'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publication_data():
    result = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
    if result['计算实现'] != '建模公式' or not result['逐块评分']:
        raise ValueError('指定正式结果缺少建模公式或逐块评分，不回退到根目录旧结果')
    return result, result, result, {'条件重估': result['条件重估']}


def publication_predictions(result, comparison):
    if result['逐块评分'] != comparison['逐块评分']:
        raise ValueError('正式结果与比较评分不是同版')
    if not MANIFEST.is_file():
        raise FileNotFoundError(f'等待算腿同版预测交付：{MANIFEST.relative_to(ROOT)}；禁止借用根目录旧CSV')
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if manifest['结果SHA256'] != digest(SNAPSHOT):
        raise ValueError('预测交付绑定的厚度结果散列不匹配')
    scores = comparison['逐块评分']
    expected_files = {f'预测_{row["材料"]}_折{row["折号"]}.csv' for row in scores}
    if set(manifest['文件SHA256']) != expected_files:
        raise ValueError('预测交付必须恰好覆盖正式评分的四个材料折次CSV')
    cache = {}
    for name in sorted(expected_files):
        path = SOURCE / name
        if digest(path) != manifest['文件SHA256'][name]:
            raise ValueError(f'预测CSV散列不匹配：{name}')
        with path.open(encoding='utf-8-sig', newline='') as stream:
            cache[name] = list(csv.DictReader(stream))
    grouped = {}
    for score in scores:
        material, fold = score['材料'], score['折号']
        attachment, block = score['附件'], score['测试块']
        key = material, fold, attachment, block
        model = score['模型']
        if model not in MODELS:
            raise ValueError(f'未定义的正式评分模型：{model}')
        rows = sorted((row for row in cache[f'预测_{material}_折{fold}.csv']
                       if row['用途'] == '测试' and int(row['附件']) == attachment
                       and int(row['原始块']) == block), key=lambda row: int(row['源行']))
        source_rows = [int(row['源行']) for row in rows]
        if len(rows) != score['测试点数'] or source_rows != score['源行'] or len(set(source_rows)) != len(rows):
            raise ValueError(f'预测与正式评分的源行不一致：{key}/{model}')
        values = [float(row[field]) for row in rows
                  for field in ('波数_cm^-1', '反射率_比例', '入射角_度', '训练尺度_比例', *sorted(MODELS))]
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f'预测含非有限值：{key}')
        scales = {float(row['训练尺度_比例']) for row in rows}
        if len(scales) != 1 or min(scales) <= 0:
            raise ValueError(f'同一角块训练尺度不唯一或非正：{key}')
        if any(float(row['入射角_度']) != score['入射角_度'] for row in rows):
            raise ValueError(f'附件入射角与评分不一致：{key}')
        error = math.sqrt(sum((float(row[model]) - float(row['反射率_比例'])) ** 2
                              for row in rows) / len(rows))
        if not math.isclose(error, score['均方根误差_反射率比例'], rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError(f'预测CSV与正式原尺度误差不一致：{key}/{model}')
        if not math.isclose(error / next(iter(scales)), score['标准化均方根误差'], rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError(f'预测CSV与正式标准化误差不一致：{key}/{model}')
        pair = grouped.setdefault(key, {'记录': rows, '评分': {}, '角度': score['入射角_度']})
        if model in pair['评分']:
            raise ValueError(f'重复正式评分：{key}/{model}')
        pair['评分'][model] = score
    if len(scores) != 64 or len(grouped) != 16 or any(set(pair['评分']) != MODELS for pair in grouped.values()):
        raise ValueError('正式预测必须通过16角块、64项四模型评分')
    for material in ('硅', '碳化硅'):
        pairs = [pair for key, pair in grouped.items() if key[0] == material]
        improved = sum(pair['评分']['两束']['标准化均方根误差'] >
                       pair['评分']['完整往返']['标准化均方根误差'] for pair in pairs)
        if len(pairs) != 8 or improved != 4:
            raise ValueError(f'指定正式版本的{material}逐块改善应为4/8')
    expected_rows = {(material, fold, attachment, int(row['源行']))
                     for (material, fold, attachment, block), pair in grouped.items() for row in pair['记录']}
    delivered_rows = []
    for name, rows in cache.items():
        for row in rows:
            if row['用途'] == '测试':
                material, fold_text = name.removeprefix('预测_').removesuffix('.csv').split('_折')
                delivered_rows.append((material, int(fold_text), int(row['附件']), int(row['源行'])))
    if len(delivered_rows) != len(expected_rows) or set(delivered_rows) != expected_rows:
        raise ValueError('交付CSV含多余或重复测试源行')
    return grouped


def publication_summary(name, values):
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    paths = [SNAPSHOT, MANIFEST, *(SOURCE / name for name in sorted(manifest['文件SHA256']))]
    summary = {'图名': name, '正式结果': str(SNAPSHOT.relative_to(ROOT)),
               '数据源摘要': {str(path.relative_to(ROOT)): digest(path) for path in paths}, **values}
    (RESULT / f'图核验_{name}.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def publication_metadata(name):
    summary = json.loads((RESULT / f'图核验_{name}.json').read_text(encoding='utf-8'))
    for source, expected in summary['数据源摘要'].items():
        if digest(ROOT / source) != expected:
            raise ValueError(f'核验后数据发生变化：{source}')
    return {'Title': name, 'Verification': json.dumps(summary, ensure_ascii=False, allow_nan=False),
            'PlotScriptSHA256': digest(RESULT.parent / f'绘图_{name}.py')}
