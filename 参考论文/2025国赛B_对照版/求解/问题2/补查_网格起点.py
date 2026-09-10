import importlib.util
import json
import time
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
START = time.monotonic()
BUDGET = 240.0
OUTPUT = ROOT / '求解/问题2/结果/搜索补查.json'
spec = importlib.util.spec_from_file_location('original_solver', ROOT / '求解/问题2/求解_问题2.py')
solver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(solver)
sigma, values, source_rows, datasets, audit = solver.read_inputs()
fold = solver.full_fold(sigma, values)
original = json.loads((ROOT / '求解/问题2/结果/厚度结果.json').read_text())
validation = json.loads((ROOT / '求解/问题2/结果/验证.json').read_text())
result = {'固定条件': {'每角点数': len(sigma), '窗口_每厘米': [1200, 3800],
                   '参数边界': solver.BOUNDS, '基线阶数': 2, '幅值阶数': 1},
          '停止条件': {'总预算_秒': BUDGET, '单次搜索预算_秒': 60, '单起点最大迭代': 70,
                   '函数相对容差': 1e-9, '默认梯度容差': 1e-5, '最大线搜索步数': 20},
          '候选': [], '加密搜索': [], '局部终点': []}


def summarize(params, loss, origin):
    margins = [(value - lower) / (upper - lower) for value, (lower, upper) in zip(params, solver.BOUNDS)]
    boundary = [name for name, margin in zip(('厚度', '参考折射率', '色散系数'), margins)
                if min(margin, 1 - margin) <= 1e-4]
    return {'来源': origin, '厚度_微米': float(params[0]), '参考折射率': float(params[1]),
            '色散系数': float(params[2]), '损失': float(loss), '触边参数': boundary,
            '最小相对边界距离': float(min(min(value, 1 - value) for value in margins))}


def save():
    result['实际用时_秒'] = time.monotonic() - START
    solver.write_json(OUTPUT, result)


for index in (0, 8):
    entry = validation['窗口与模型灵敏度'][index]
    params = (entry['厚度_um'], entry['参考折射率'], entry['色散系数'])
    candidate = solver.fit_candidate(params, fold)
    result['候选'].append(summarize(params, candidate['损失'], entry['情景名称']))
    result['候选'][-1]['原记录损失'] = entry['训练标准化损失']
save()
native_minimize = solver.minimize


def record_minimize(objective, initial, **kwargs):
    outcome = native_minimize(objective, initial, **kwargs)
    entry = summarize(outcome.x, outcome.fun, '连续优化终点')
    entry.update({'初值': list(initial), '迭代数': int(outcome.nit),
                  '成功终止': bool(outcome.success), '停止说明': str(outcome.message)})
    result['局部终点'].append(entry)
    return outcome


solver.minimize = record_minimize
for grid in (192, 384):
    if time.monotonic() - START > BUDGET - 70:
        break
    candidate, info = solver.search_fold(fold, START, grid_size=grid, max_starts=16, budget_seconds=60)
    entry = summarize(candidate['参数'], candidate['损失'], f'厚度网格{grid}点')
    result['候选'].append(entry)
    result['加密搜索'].append({'网格点数': grid, '详情': info, '最优终点': entry})
    save()

for entry in result['候选'][:2]:
    for shift in (-0.01, 0.01):
        if time.monotonic() - START > BUDGET - 20:
            break
        initial = np.array([entry['厚度_微米'] * (1 + shift), entry['参考折射率'], entry['色散系数']])
        def objective(params):
            if time.monotonic() - START >= BUDGET - 5:
                return 1e10
            candidate = solver.fit_candidate(params, fold)
            return 1e10 if candidate is None else candidate['损失']
        record_minimize(objective, initial, method='L-BFGS-B', bounds=solver.BOUNDS,
                        options={'maxiter': 70, 'ftol': 1e-9, 'maxls': 20})
        save()

best = min(result['候选'] + result['局部终点'], key=lambda entry: entry['损失'])
result['补查最低损失候选'] = best
result['局部停止汇总'] = {'终点数': len(result['局部终点']),
                       '满足数值停止准则': sum(entry['成功终止'] for entry in result['局部终点']),
                       '达到迭代上限': sum('ITERATIONS REACHED LIMIT' in entry['停止说明'] for entry in result['局部终点'])}
result['原网格到加密厚度变化_百分比'] = 100 * (result['候选'][1]['厚度_微米'] / result['候选'][0]['厚度_微米'] - 1)
result['原代表相对最低损失差_百分比'] = 100 * (result['候选'][0]['损失'] / best['损失'] - 1)
result['代表点规则'] = '沿用原96点网格搜索的最低损失可行解作为跨问题比较坐标；要求各参数相对边界距离大于0.0001。补查更低损失候选全部保留，不称代表点为全域最优，也不将触边候选删去。'
result['解释'] = '固定观测、模型和物理边界的移动属于数值搜索不确定性；改变窗口、光学参数或切分属于另列情景变化。局部算法终止不证明全域唯一性。'
save()
ledger = ROOT / '交接/实验记录.json'
records = json.loads(ledger.read_text())
records.append({'问题': '问题2', '类别': '科学尝试', '尝试': '固定每角480点、原模型及边界，补查192与384点网格并扰动原解和边界解的厚度起点。',
                '现象': f"原代表厚度{result['候选'][0]['厚度_微米']}微米，加密候选{result['候选'][1]['厚度_微米']}微米；补查最低损失{best['损失']}，厚度{best['厚度_微米']}微米，触边参数{best['触边参数']}。",
                '决定': '保留原非触边点作为比较代表，显式展示边界候选，将数值搜索变化与物理情景变化分开。',
                '依据': '求解/问题2/结果/搜索补查.json'})
solver.write_json(ledger, records)
print(json.dumps({'最低损失候选': best, '候选数': len(result['候选']) + len(result['局部终点']), '用时_秒': result['实际用时_秒']}, ensure_ascii=False))
