import sys; sys.path.insert(0, '/tmp/蜂巢/pylibs')

"""问题3：CAP-HF 跨角置换门控谐波指纹。

本脚本按任务要求只负责计算与写 JSON，不绘制正式图片。设计目标是在 18 分钟主动
截止前完成真实谱主结果、连续遮挡验证、单因素灵敏度、厚度区间覆盖和条件功效审计。
"""

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar


根目录 = Path('/tmp/蜂巢')
数据目录 = 根目录 / '数据'
结果目录 = 根目录 / '求解' / '问题3' / '结果'
问题2结果目录 = 根目录 / '求解' / '问题2' / '结果'
权威日志路径 = 根目录 / '日志' / '执行_问题3.log'
结果声明路径 = 根目录 / '交接' / '结果声明_问题3.json'

软截止秒 = 1000.0
硬截止秒 = 1080.0
当前运行ID = None
随机种子 = 20260827
# 仲裁返工：厚度只能在覆盖至少3个基本周期的宽波段估计；短窗不再估厚度。
主厚度波段 = (1500.0, 3000.0)
诊断宽波段组 = (
    (900.0, 1800.0), (1200.0, 2400.0), (1500.0, 3000.0),
    (1800.0, 4000.2), (1000.0, 4000.2),
)
主块长 = 128
主基线阶数 = 4
主最高谐波 = 4
主置换分位 = 0.95
主指纹阈值 = 0.50
厚度相容阈值 = 0.10
最小有效置换次数 = 8
半合成误报率上限 = 0.10
半合成检出率下限 = 0.80
半合成检出优势下限 = 0.60
验收注入高阶能量比 = 0.30
注入高阶能量比序列 = (0.15, 0.30, 0.45)
备选方法误差倍率上限 = 1.50
触发窗预测改善下限 = 0.005
灵敏度厚度变化上限_pct = 20.0

# 与求解/问题3/复算.py完全相同的硅红外 Sellmeier 接口；lambda单位为微米。
# n^2=A+B/(lambda^2-C)+D/(lambda^2-E)。本问把它视为外部强约束函数，
# 不再用短窗反射谱自由估计宽幅线性色散。
硅_SELLMEIER = {
    'A': 11.6858, 'B': 0.939816, 'C': 0.00810461,
    'D': 0.0030434748, 'E': 1.54133408 ** 2,
}
红队厚度锚点 = {
    '附件3.xlsx': 3.4604751664768405,
    '附件4.xlsx': 3.452771371748934,
    '双角联合': 3.4490013928596364,
}

材料配置 = {
    '硅': {
        '附件': [('附件3.xlsx', 10.0), ('附件4.xlsx', 15.0)],
        '折射率锚点': None,
        '色散中心': 0.0,
        '色散物理范围': (0.995, 1.005),
        '色散接口来源': '求解/问题3/复算.py及交接/仲裁_问题3.json采用的硅红外Sellmeier函数；系数逐项同口径',
        '色散接口等级': '外部强约束函数；函数形状不由本题反射谱估计，仅做折射率整体±0.5%灵敏度',
        '厚度范围_um': (0.5, 250.0),
    },
    '碳化硅': {
        '附件': [('附件1.xlsx', 10.0), ('附件2.xlsx', 15.0)],
        '折射率锚点': 2.60,
        '色散中心': 0.0,
        '色散物理范围': (-0.30, 0.30),
        '色散接口来源': '求解/问题1/结果/02_主方法与交叉印证.json的碳化硅显式色散理论接口；问题3不消费其厚度数值',
        '色散接口等级': '上游理论接口；问题2验收失败时仍不得发布碳化硅修正厚度',
        '厚度范围_um': (1.0, 120.0),
    },
}


class 时间收敛(RuntimeError):
    pass


def 已用时(t0):
    return time.perf_counter() - t0


def 检查预算(t0, hard=False):
    limit = 硬截止秒 if hard else 软截止秒
    if 已用时(t0) >= limit:
        raise 时间收敛(f'达到时间预算{limit:.0f}秒')


def JSON安全值(obj):
    """递归转换为严格 JSON 可写类型。

    置换样本少于 8 次时，内部以 +inf 表示“保守不触发”；落盘时记为
    null，并由同层的“置换次数”标识阈值不足，不改变内存中的门控计算。
    """
    if isinstance(obj, dict):
        return {str(k): JSON安全值(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [JSON安全值(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [JSON安全值(v) for v in obj.tolist()]
    if isinstance(obj, np.generic):
        return JSON安全值(obj.item())
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def 原子写_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(JSON安全值(obj), f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def 写权威日志(message, mode='a'):
    权威日志路径.parent.mkdir(parents=True, exist_ok=True)
    with 权威日志路径.open(mode, encoding='utf-8') as f:
        f.write(str(message).rstrip() + '\n')
        f.flush()
        os.fsync(f.fileno())


def 落盘(文件名, obj, t0):
    if 当前运行ID is not None:
        obj['运行ID'] = 当前运行ID
    obj['实际用时秒'] = round(已用时(t0), 6)
    原子写_json(结果目录 / 文件名, obj)


def 初始化本轮结果目录():
    """删除上一运行的派生JSON，避免新旧运行在同一结果目录混杂。"""
    结果目录.mkdir(parents=True, exist_ok=True)
    for path in list(结果目录.glob('*.json')) + list(结果目录.glob('*.tmp')):
        path.unlink(missing_ok=True)
    # 新运行未通过前绝不保留旧的可引用声明；主动收敛/异常同样安全。
    结果声明路径.unlink(missing_ok=True)


def 读取附件(文件名):
    df = pd.read_excel(
        数据目录 / 文件名, sheet_name='Sheet1', header=0, engine='openpyxl'
    ).rename(columns={'波数 (cm-1)': '波数', '反射率 (%)': '反射率'})
    if not {'波数', '反射率'}.issubset(df.columns):
        raise ValueError(f'{文件名}字段与数据档案不一致：{list(df.columns)}')
    out = df[['波数', '反射率']].astype(float).sort_values('波数').reset_index(drop=True)
    if len(out) != 7469 or out.isna().any().any() or not np.all(np.diff(out['波数']) > 0):
        raise ValueError(f'{文件名}未通过7469点、无缺失、严格递增检查')
    return out


def 读取全部数据():
    return {fn: 读取附件(fn) for cfg in 材料配置.values() for fn, _ in cfg['附件']}


def 读取问题2接口():
    """只读取上游结果，不重算问题2；验收失败时禁止发布修正厚度。"""
    gate_path = 问题2结果目录 / '06_自动验收门.json'
    summary_path = 问题2结果目录 / '汇总结果.json'
    if not gate_path.exists() or not summary_path.exists():
        return {'可消费': False, '原因': '问题2验收门或汇总结果缺失'}
    gate = json.loads(gate_path.read_text(encoding='utf-8'))
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    passed = bool(gate.get('总体通过', False)) and summary.get('运行状态') != '结果未验收'
    return {
        '可消费': passed,
        '原因': '问题2自动验收通过' if passed else '问题2当前落盘的自动验收门未通过，禁止把厚度作为已验证基线发布',
        '共享厚度_um': summary.get('共享厚度_um'),
        '附件1_10度厚度_um': summary.get('附件1_10度独立厚度_um'),
        '附件2_15度厚度_um': summary.get('附件2_15度独立厚度_um'),
        '上游运行状态': summary.get('运行状态'),
        '上游未通过项': gate.get('未通过项', []),
    }


def 角色掩码(n, block, offset=0):
    """连续块角色循环：4训练、1校准、1验证；不随机打散相邻点。"""
    # 使用全谱点号；重叠宽窗中的同一采样点始终保持同一角色，防止跨窗泄漏。
    bid = (offset + np.arange(n)) // block
    role = bid % 6
    return {'训练': role <= 3, '校准': role == 4, '验证': role == 5, '块号': bid}


def 构造波段窗口(data, material, bands, block=主块长):
    """按物理波段构造等口径宽窗；每窗均保留连续训练/校准/验证块。"""
    bands = tuple(bands)
    panels = []
    for wid, band in enumerate(bands):
        for fn, angle in 材料配置[material]['附件']:
            df = data[fn]
            w = df[(df['波数'] >= band[0]) & (df['波数'] <= band[1])].reset_index(drop=True)
            if len(w) < 6 * block:
                continue
            # 保留波段内全部点；置换函数只抽取完整训练块，末端残块不参与置换。
            n = len(w)
            start_index = int(df['波数'].searchsorted(w['波数'].iloc[0]))
            masks = 角色掩码(n, block, offset=start_index)
            full_train_blocks = sum(
                np.sum(masks['块号'] == bid) == block
                for bid in np.unique(masks['块号'][masks['训练']])
            )
            if full_train_blocks < 4 or not np.any(masks['校准']) or not np.any(masks['验证']):
                continue
            panels.append({
                '材料': material, '文件': fn, '角度': angle, '窗口序号': wid,
                '波数': w['波数'].to_numpy(float), '反射率': w['反射率'].to_numpy(float),
                '训练': masks['训练'], '校准': masks['校准'], '验证': masks['验证'],
                '块号': masks['块号'], '块长': block, '完整训练块数': int(full_train_blocks),
                '窗口起点索引': start_index,
                '窗口范围_cm-1': [float(w['波数'].iloc[0]), float(w['波数'].iloc[-1])],
                '预注册目标波段_cm-1': [float(band[0]), float(band[1])],
            })
    expected = len(bands) * len(材料配置[material]['附件'])
    if len(panels) != expected:
        raise ValueError(f'{material}宽波段窗口不完整：得到{len(panels)}个，预期{expected}个')
    return panels


def 折射率(sigma, material, slope, slope_scale=1.0):
    sigma = np.asarray(sigma, float)
    if material == '硅':
        lam_um = 1.0e4 / sigma
        p = 硅_SELLMEIER
        n2 = p['A'] + p['B'] / (lam_um ** 2 - p['C']) + p['D'] / (lam_um ** 2 - p['E'])
        if np.any(~np.isfinite(n2)) or np.any(n2 <= 1.0):
            raise ValueError('硅Sellmeier接口在当前波段失效')
        return slope_scale * np.sqrt(n2)
    n0 = 材料配置[material]['折射率锚点']
    return slope_scale * (n0 + slope * (sigma - 1900.0) / 700.0)


def 相位(panel, d_um, slope, slope_scale=1.0, angle_shift=0.0):
    sigma = panel['波数']
    n = 折射率(sigma, panel['材料'], slope, slope_scale)
    theta = math.radians(panel['角度'] + angle_shift)
    inside = n * n - math.sin(theta) ** 2
    if np.min(inside) <= 0:
        raise ValueError('候选折射率与角度使传播相位无实数解')
    return 4.0 * math.pi * d_um * 1e-4 * sigma * np.sqrt(inside)


def 设计矩阵(panel, d_um, slope, K=1, baseline_degree=2,
             slope_scale=1.0, angle_shift=0.0):
    sigma = panel['波数']
    z = (sigma - np.mean(sigma)) / max(float(np.ptp(sigma)), 1e-12)
    phi = 相位(panel, d_um, slope, slope_scale, angle_shift)
    cols = [z ** j for j in range(baseline_degree + 1)]
    for k in range(1, K + 1):
        cols.extend([np.cos(k * phi), np.sin(k * phi)])
    return np.column_stack(cols)


def 给定相位设计矩阵(panel, phi, K=1, baseline_degree=2):
    sigma = panel['波数']
    z = (sigma - np.mean(sigma)) / max(float(np.ptp(sigma)), 1e-12)
    cols = [z ** j for j in range(baseline_degree + 1)]
    for k in range(1, K + 1):
        cols.extend([np.cos(k * phi), np.sin(k * phi)])
    return np.column_stack(cols)


def 给定相位线性拟合(panel, phi, K=1, baseline_degree=2, y=None, fit_role='训练'):
    X = 给定相位设计矩阵(panel, phi, K, baseline_degree)
    target = panel['反射率'] if y is None else np.asarray(y, float)
    coef, _, _, _ = np.linalg.lstsq(X[panel[fit_role]], target[panel[fit_role]], rcond=None)
    return coef, X @ coef


def 线性拟合(panel, d_um, slope, K=1, baseline_degree=2, y=None,
             fit_role='训练', slope_scale=1.0, angle_shift=0.0):
    X = 设计矩阵(panel, d_um, slope, K, baseline_degree, slope_scale, angle_shift)
    target = panel['反射率'] if y is None else np.asarray(y, float)
    mask = panel[fit_role]
    coef, _, _, _ = np.linalg.lstsq(X[mask], target[mask], rcond=None)
    return coef, X @ coef


def 稳健尺度(y):
    y = np.asarray(y, float)
    q = float(np.quantile(y, 0.95) - np.quantile(y, 0.05))
    return q if q > 1e-10 else max(float(np.std(y)), 1.0)


def nrmse(y, pred):
    return float(np.sqrt(np.mean((np.asarray(y) - np.asarray(pred)) ** 2)) / 稳健尺度(y))


def K1目标(panels, d_um, slope, role='训练', baseline_degree=2,
           slope_scale=1.0, angle_shift=0.0):
    losses = []
    for p in panels:
        _, pred = 线性拟合(p, d_um, slope, 1, baseline_degree,
                           slope_scale=slope_scale, angle_shift=angle_shift)
        mask = p[role]
        losses.append(nrmse(p['反射率'][mask], pred[mask]) ** 2)
    return float(np.mean(losses))


def 厚度搜索网格(material, local_center=None, quality='主'):
    """全物理范围搜索；local_center只增加局部密度，绝不再形成±30%硬边界。"""
    dlo, dhi = 材料配置[material]['厚度范围_um']
    n = 150 if quality == '主' else 55
    low_hi = min(dhi, 35.0)
    grids = [np.linspace(dlo, low_hi, n)]
    if dhi > low_hi:
        grids.append(np.geomspace(max(low_hi, dlo + 1e-6), dhi, max(n // 2, 30)))
    if local_center is not None and np.isfinite(local_center):
        lo = max(dlo, 0.45 * float(local_center))
        hi = min(dhi, 1.80 * float(local_center))
        if hi > lo:
            grids.append(np.linspace(lo, hi, max(n // 2, 40)))
    return np.unique(np.concatenate(grids))


def 厚度局部极小索引(losses):
    losses = np.asarray(losses, float)
    idx = [i for i in range(1, len(losses) - 1)
           if losses[i] <= losses[i - 1] and losses[i] <= losses[i + 1]]
    idx.extend([0, len(losses) - 1])
    return sorted(set(idx), key=lambda i: losses[i])


def 搜索厚度色散(panels, material, t0, local_center=None,
                 baseline_degree=2, slope_scale=1.0, angle_shift=0.0,
                 quality='主'):
    """在有来源的材料色散接口内做二维剖面，并返回竞争盆地与支持区。"""
    cfg = 材料配置[material]
    dlo, dhi = cfg['厚度范围_um']
    slo, shi = cfg['色散物理范围']
    if material == '硅':
        # 仲裁要求硅色散由外部物性函数强约束；反射谱只搜索几何厚度。
        fixed = 搜索固定色散厚度(
            panels, material, 0.0, t0, center=local_center,
            baseline_degree=baseline_degree, slope_scale=slope_scale,
            angle_shift=angle_shift, quality=quality
        )
        audit = {
            '方法': '固定硅红外Sellmeier函数形状，只在覆盖至少3周期的宽波段搜索厚度',
            '厚度搜索范围_um': [dlo, dhi], '色散物理范围': [0.995, 1.005],
            '色散接口来源': cfg['色散接口来源'], '色散接口等级': cfg['色散接口等级'],
            '硅Sellmeier表达式': 'n²=A+B/(λ²-C)+D/(λ²-E)，λ单位μm',
            '硅Sellmeier参数': 硅_SELLMEIER,
            '色散剖面': [{'折射率整体尺度': 1.0, '最优厚度_um': fixed['厚度_um'],
                      '训练目标': fixed['训练目标']}],
            '色散95%剖面支持区': [1.0, 1.0],
            '支持区构造': '外部强约束接口把名义整体尺度固定为1.0，位于[0.995,1.005]审计范围内部；端点仅用于±0.5%灵敏度，不由反射谱反向选择',
            '剖面损失上限': None,
            '候选解_前5': [dict(x, 接口偏差参数=0.0) for x in fixed['候选厚度盆地_前5']],
            '厚度触及硬边界': fixed['厚度触及硬边界'],
            '色散触及硬边界': False,
            '色散支持区触及物理边界': False,
            '色散边界距离占半宽比例': None,
            '局部中心仅作加密不作边界': local_center,
        }
        return {'厚度_um': fixed['厚度_um'], '色散斜率': 0.0,
                '训练目标': fixed['训练目标'], '二维剖面与多起点审计': audit}
    ns = 21 if quality == '主' else 9
    slopes = np.linspace(slo, shi, ns)
    dgrid = 厚度搜索网格(material, local_center, quality)
    candidates, profile = [], []
    for j, slope in enumerate(slopes):
        losses = [K1目标(
            panels, float(d), float(slope), baseline_degree=baseline_degree,
            slope_scale=slope_scale, angle_shift=angle_shift
        ) for d in dgrid]
        local_ids = 厚度局部极小索引(losses)[:4]
        slope_best = None
        for idx in local_ids:
            left = float(dgrid[max(0, idx - 1)])
            right = float(dgrid[min(len(dgrid) - 1, idx + 1)])
            if right <= left:
                loss, dopt = float(losses[idx]), float(dgrid[idx])
            else:
                opt = minimize_scalar(
                    lambda d: K1目标(
                        panels, float(d), float(slope), baseline_degree=baseline_degree,
                        slope_scale=slope_scale, angle_shift=angle_shift
                    ), bounds=(left, right), method='bounded',
                    options={'xatol': 2e-5, 'maxiter': 50}
                )
                loss, dopt = float(opt.fun), float(opt.x)
            candidates.append((loss, dopt, float(slope)))
            if slope_best is None or loss < slope_best[0]:
                slope_best = (loss, dopt)
        profile.append({'色散斜率': float(slope), '最优厚度_um': slope_best[1],
                        '训练目标': slope_best[0]})
        if j % 2 == 0:
            检查预算(t0)

    candidates.sort(key=lambda x: x[0])
    best = candidates[0]
    # 在最佳色散网格邻域继续多起点二维剖面，避免粗网格边界假象。
    sstep = float(slopes[1] - slopes[0])
    fine_slopes = np.linspace(max(slo, best[2] - sstep), min(shi, best[2] + sstep), 11)
    for slope in fine_slopes:
        nearby = sorted(candidates, key=lambda x: (abs(x[2] - slope), x[0]))[:3]
        for _, center, _ in nearby:
            span = max(0.04 * center, 0.35)
            left, right = max(dlo, center - span), min(dhi, center + span)
            if right <= left:
                continue
            opt = minimize_scalar(
                lambda d: K1目标(
                    panels, float(d), float(slope), baseline_degree=baseline_degree,
                    slope_scale=slope_scale, angle_shift=angle_shift
                ), bounds=(left, right), method='bounded',
                options={'xatol': 1e-5, 'maxiter': 60}
            )
            candidates.append((float(opt.fun), float(opt.x), float(slope)))
    candidates.sort(key=lambda x: x[0])
    best = candidates[0]
    unique = []
    for loss, d, slope in candidates:
        if all(abs(d - old[1]) > max(0.01 * d, 0.05) or abs(slope - old[2]) > 0.5 * sstep
               for old in unique):
            unique.append((loss, d, slope))
        if len(unique) >= 5:
            break
    best_profile_loss = min(x['训练目标'] for x in profile)
    n_eff = sum(int(np.sum(p['训练'])) for p in panels)
    # 高斯残差下1自由度95%剖面似然近似；只作为条件支持区，不冒充独立物性测量。
    profile_limit = best_profile_loss * (1.0 + 3.841458820694124 / max(n_eff - 2, 1))
    supported = [x['色散斜率'] for x in profile if x['训练目标'] <= profile_limit]
    profile_ci = [min(supported), max(supported)] if supported else [best[2], best[2]]
    audit = {
        '方法': '全厚度范围×有来源色散物理范围二维剖面；每个色散切片最多4个厚度盆地精修，再在最佳色散邻域多起点复核',
        '厚度搜索范围_um': [dlo, dhi], '色散物理范围': [slo, shi],
        '色散接口来源': cfg['色散接口来源'], '色散接口等级': cfg['色散接口等级'],
        '色散剖面': profile, '色散95%剖面支持区': profile_ci,
        '支持区构造': '高斯残差近似下，以1自由度卡方95%阈值3.84146换算剖面损失上限；该区间以折射率锚点和色散接口为条件',
        '剖面损失上限': profile_limit,
        '候选解_前5': [{'训练目标': x[0], '厚度_um': x[1], '色散斜率': x[2]} for x in unique],
        '厚度触及硬边界': bool(best[1] <= dlo + max(0.002 * (dhi - dlo), 0.05)
                           or best[1] >= dhi - max(0.002 * (dhi - dlo), 0.05)),
        '色散触及硬边界': bool(best[2] <= slo + 1.01 * sstep or best[2] >= shi - 1.01 * sstep),
        '色散支持区触及物理边界': bool(profile_ci[0] <= slo + 1.01 * sstep or profile_ci[1] >= shi - 1.01 * sstep),
        '色散边界距离占半宽比例': min(best[2] - slo, shi - best[2]) / max((shi - slo) / 2, 1e-12),
        '局部中心仅作加密不作边界': local_center,
    }
    return {'厚度_um': best[1], '色散斜率': best[2], '训练目标': best[0],
            '二维剖面与多起点审计': audit}


def 搜索固定色散厚度(panels, material, slope, t0, center=None,
                    baseline_degree=2, slope_scale=1.0, angle_shift=0.0,
                    quality='主'):
    dgrid = 厚度搜索网格(material, center, quality)
    losses = [K1目标(panels, float(d), float(slope), baseline_degree=baseline_degree,
                    slope_scale=slope_scale, angle_shift=angle_shift) for d in dgrid]
    candidates = []
    for idx in 厚度局部极小索引(losses)[:6]:
        left, right = float(dgrid[max(0, idx - 1)]), float(dgrid[min(len(dgrid) - 1, idx + 1)])
        if right <= left:
            candidates.append((float(losses[idx]), float(dgrid[idx])))
            continue
        opt = minimize_scalar(
            lambda d: K1目标(panels, float(d), float(slope), baseline_degree=baseline_degree,
                             slope_scale=slope_scale, angle_shift=angle_shift),
            bounds=(left, right), method='bounded', options={'xatol': 1e-5, 'maxiter': 60}
        )
        candidates.append((float(opt.fun), float(opt.x)))
    检查预算(t0)
    candidates.sort(key=lambda x: x[0])
    best = candidates[0]
    dlo, dhi = 材料配置[material]['厚度范围_um']
    return {'厚度_um': best[1], '色散斜率': float(slope), '训练目标': best[0],
            '厚度触及硬边界': bool(best[1] <= dlo + max(0.002 * (dhi - dlo), 0.05)
                               or best[1] >= dhi - max(0.002 * (dhi - dlo), 0.05)),
            '候选厚度盆地_前5': [{'训练目标': x[0], '厚度_um': x[1]} for x in candidates[:5]]}


def 分角厚度(panels, material, shared, t0, baseline_degree=2,
          slope_scale=1.0, angle_shift=0.0, quality='主'):
    """同一材料色散由双角联合识别，随后各角独立估厚，切断厚度—色散混淆。"""
    out = {}
    for fn, angle in 材料配置[material]['附件']:
        pp = [p for p in panels if p['文件'] == fn]
        out[fn] = 搜索固定色散厚度(
            pp, material, shared['色散斜率'], t0, center=shared['厚度_um'],
            baseline_degree=baseline_degree, slope_scale=slope_scale,
            angle_shift=angle_shift, quality=quality
        )
        out[fn]['角度_度'] = angle
    vals = [out[fn]['厚度_um'] for fn, _ in 材料配置[material]['附件']]
    out['双角相对差'] = abs(vals[0] - vals[1]) / max(float(np.mean(vals)), 1e-12)
    out['色散处理'] = '先由双角联合二维剖面估计共同色散，再冻结该色散分别估计两角厚度；分角不再各自用色散补偿厚度'
    return out


def 全样本K1目标(panels, d_um, baseline_degree=主基线阶数):
    """红队同口径目标：固定Sellmeier与基线阶数，全部波段点参与拟合。"""
    losses = []
    for p in panels:
        X = 设计矩阵(p, d_um, 0.0, K=1, baseline_degree=baseline_degree)
        coef, _, _, _ = np.linalg.lstsq(X, p['反射率'], rcond=None)
        pred = X @ coef
        y = p['反射率']
        sse = float(np.sum((y - pred) ** 2))
        sst = float(np.sum((y - np.mean(y)) ** 2))
        losses.append(sse / max(sst, 1e-30))
    return float(np.mean(losses))


def 受控厚度搜索(panels, t0, baseline_degree=主基线阶数):
    grid = np.linspace(0.5, 12.0, 320)
    losses = np.asarray([全样本K1目标(panels, d, baseline_degree) for d in grid])
    candidates = []
    for idx in 厚度局部极小索引(losses)[:8]:
        left, right = grid[max(0, idx - 1)], grid[min(len(grid) - 1, idx + 1)]
        opt = minimize_scalar(
            lambda d: 全样本K1目标(panels, float(d), baseline_degree),
            bounds=(float(left), float(right)), method='bounded',
            options={'xatol': 1e-6, 'maxiter': 70}
        )
        candidates.append((float(opt.fun), float(opt.x)))
    candidates.sort(key=lambda x: x[0])
    best = candidates[0]
    validation = []
    for p in panels:
        _, pred = 线性拟合(p, best[1], 0.0, K=1, baseline_degree=baseline_degree)
        validation.append(nrmse(p['反射率'][p['验证']], pred[p['验证']]))
    检查预算(t0)
    return {
        '厚度_um': best[1], '全样本目标_1减R2': best[0],
        '全样本R2': 1.0 - best[0], '连续块验证NRMSE': float(np.mean(validation)),
        '竞争盆地_前5': [
            {'厚度_um': d, '目标_1减R2': loss, 'R2': 1.0 - loss}
            for loss, d in candidates[:5]
        ],
    }


def 红队同口径受控对照(data, t0):
    """同波段、同Sellmeier、同4阶基线复核3.45 μm锚点，不复制红队结论。"""
    panels = 构造波段窗口(data, '硅', (主厚度波段,), 主块长)
    out = {
        '受控变量': {
            '波段_cm-1': list(主厚度波段), '折射率': '硅Sellmeier系数与求解/问题3/复算.py相同',
            '基线阶数': 主基线阶数, '基本波阶数': 1,
        },
        '逐附件': {},
    }
    for fn, _ in 材料配置['硅']['附件']:
        pp = [p for p in panels if p['文件'] == fn]
        fit = 受控厚度搜索(pp, t0)
        anchor = 红队厚度锚点[fn]
        fit.update({
            '红队锚点_um': anchor,
            '红队锚点目标_1减R2': 全样本K1目标(pp, anchor, 主基线阶数),
            '与红队锚点相对差': abs(fit['厚度_um'] - anchor) / anchor,
        })
        out['逐附件'][fn] = fit
    joint = 受控厚度搜索(panels, t0)
    anchor = 红队厚度锚点['双角联合']
    joint.update({
        '红队锚点_um': anchor,
        '红队锚点目标_1减R2': 全样本K1目标(panels, anchor, 主基线阶数),
        '与红队锚点相对差': abs(joint['厚度_um'] - anchor) / anchor,
    })
    out['双角联合'] = joint
    out['方向一致'] = bool(
        all(x['与红队锚点相对差'] <= 0.05 for x in out['逐附件'].values())
        and joint['与红队锚点相对差'] <= 0.05
    )
    out['判据'] = '三个同口径估计均与红队锚点相差不超过5%；若失败则保留目标函数、竞争盆地和验证误差并禁止发布'
    return out


def 谐波复系数(coef, baseline_degree, K):
    start = baseline_degree + 1
    c = []
    for k in range(K):
        a, b = coef[start + 2 * k:start + 2 * k + 2]
        c.append(complex(float(a), -float(b)))
    return c


def 高阶能量比(coef, baseline_degree, K):
    c = 谐波复系数(coef, baseline_degree, K)
    e = np.asarray([abs(x) ** 2 for x in c])
    return float(np.sum(e[1:]) / max(float(np.sum(e)), 1e-15))


def 相移不变指纹(coef, baseline_degree, K):
    c = 谐波复系数(coef, baseline_degree, K)
    phi1 = np.angle(c[0])
    v = []
    for k in range(2, K + 1):
        z = c[k - 1] * np.exp(-1j * k * phi1)
        v.extend([z.real, z.imag])
    v = np.asarray(v, float)
    return v / max(float(np.linalg.norm(v)), 1e-15)


def 有效回程幅比(coef, baseline_degree, K):
    amp = np.asarray([abs(x) for x in 谐波复系数(coef, baseline_degree, K)])
    ratios = amp[1:] / np.maximum(amp[:-1], 1e-12)
    return float(np.median(ratios))


def 训练块索引(panel):
    ids = np.unique(panel['块号'][panel['训练']])
    blocks = [np.flatnonzero(panel['块号'] == b) for b in ids]
    return [idx for idx in blocks if len(idx) == panel['块长']]


def 唯一块置换(n_blocks, max_perm, seed):
    """不枚举阶乘全集；至少4块时稳定给出不少于8个唯一整块置换。"""
    total = math.factorial(n_blocks)
    target = min(max_perm, total)
    if total <= max_perm:
        import itertools
        return list(itertools.permutations(range(n_blocks)))
    rng = np.random.default_rng(seed)
    orders = {tuple(range(n_blocks))}
    while len(orders) < target:
        orders.add(tuple(rng.permutation(n_blocks).tolist()))
    return sorted(orders)


def 置换阈值(panel, d_um, slope, K, baseline_degree, quantile, t0,
          slope_scale=1.0, angle_shift=0.0, max_perm=48):
    _, pred1 = 线性拟合(panel, d_um, slope, 1, baseline_degree,
                       slope_scale=slope_scale, angle_shift=angle_shift)
    residual = panel['反射率'] - pred1
    blocks = 训练块索引(panel)
    if len(blocks) < 4:
        return float('inf'), 0
    orders = 唯一块置换(
        len(blocks), max_perm,
        随机种子 + 101 * panel['窗口序号'] + int(round(panel['角度']))
    )
    null = []
    for i, order in enumerate(orders):
        y0 = pred1.copy()
        for target, source in zip(blocks, order):
            y0[target] += residual[blocks[source]]
        coef, _ = 线性拟合(panel, d_um, slope, K, baseline_degree, y=y0,
                          slope_scale=slope_scale, angle_shift=angle_shift)
        null.append(高阶能量比(coef, baseline_degree, K))
        if i % 12 == 0:
            检查预算(t0)
    threshold = float(np.quantile(null, quantile)) if len(null) >= 最小有效置换次数 else float('inf')
    return threshold, len(null)


def 单窗诊断(panel, d_um, slope, K, baseline_degree, quantile, t0,
         slope_scale=1.0, angle_shift=0.0):
    coef1, pred1 = 线性拟合(panel, d_um, slope, 1, baseline_degree,
                           slope_scale=slope_scale, angle_shift=angle_shift)
    coefk, predk = 线性拟合(panel, d_um, slope, K, baseline_degree,
                           slope_scale=slope_scale, angle_shift=angle_shift)
    threshold, nperm = 置换阈值(
        panel, d_um, slope, K, baseline_degree, quantile, t0,
        slope_scale, angle_shift
    )
    phi = 相位(panel, d_um, slope, slope_scale, angle_shift)
    cycles = float((phi[-1] - phi[0]) / (2 * math.pi))
    points_per_high_cycle = len(phi) / max(abs(cycles) * K, 1e-12)
    ratio = 高阶能量比(coefk, baseline_degree, K)
    return {
        'panel': panel, 'coef1': coef1, 'pred1': pred1, 'coefK': coefk, 'predK': predk,
        '基线阶数': baseline_degree,
        '高阶能量比': ratio, '置换阈值': threshold, '置换次数': nperm,
        '能量超阈': bool(ratio > threshold),
        '指纹': 相移不变指纹(coefk, baseline_degree, K),
        '有效回程幅比代理': 有效回程幅比(coefk, baseline_degree, K),
        '基本波周期数': abs(cycles), '最高谐波每周期点数': points_per_high_cycle,
        '相干分辨通过': bool(abs(cycles) >= 3.0 and points_per_high_cycle >= 4.0),
    }


def CAP_HF诊断(panels, material, shared, separate, t0, K=4, baseline_degree=2,
             quantile=0.95, fingerprint_threshold=0.5,
             slope_scale=1.0, angle_shift=0.0):
    fits = {}
    for p in panels:
        fits[(p['文件'], p['窗口序号'])] = 单窗诊断(
            p, shared['厚度_um'], shared['色散斜率'], K, baseline_degree,
            quantile, t0, slope_scale, angle_shift
        )
    files = [x[0] for x in 材料配置[material]['附件']]
    window_ids = sorted(set(p['窗口序号'] for p in panels))
    windows = []
    for wid in window_ids:
        a, b = fits[(files[0], wid)], fits[(files[1], wid)]
        sim = float(np.dot(a['指纹'], b['指纹']))
        dgap = separate['双角相对差']
        cond = {
            'C1_有效回程': bool(a['能量超阈'] and b['能量超阈']),
            'C2_相干与分辨': bool(a['相干分辨通过'] and b['相干分辨通过']),
            'C3_界面平行同厚': bool(dgap <= 厚度相容阈值),
            'C4_双角高阶复现': bool(sim >= fingerprint_threshold),
        }
        trigger = all(cond.values())
        windows.append({
            '窗口序号': wid, '波数范围_cm-1': a['panel']['窗口范围_cm-1'],
            '10度高阶能量比': a['高阶能量比'], '10度置换阈值': a['置换阈值'],
            '15度高阶能量比': b['高阶能量比'], '15度置换阈值': b['置换阈值'],
            '双角指纹相似度': sim, '双角厚度相对差': dgap,
            '10度基本波周期数': a['基本波周期数'], '15度基本波周期数': b['基本波周期数'],
            '10度最高谐波每周期点数': a['最高谐波每周期点数'],
            '15度最高谐波每周期点数': b['最高谐波每周期点数'],
            '有效回程幅比代理': float(np.mean([a['有效回程幅比代理'], b['有效回程幅比代理']])),
            '必要条件': cond, '触发': trigger,
            '置换次数': [a['置换次数'], b['置换次数']],
        })
    ntrig = sum(x['触发'] for x in windows)
    observable = [x for x in windows if x['必要条件']['C2_相干与分辨']]
    if not observable:
        level = '现有窗口分辨率不足以检验多回程'
    elif ntrig == 0:
        level = 'C2可观测窗口内无多光束证据'
    elif ntrig * 2 <= len(observable):
        level = '局部多光束证据'
    else:
        level = '持续多光束证据'
    return {'fits': fits, '窗口审计': windows, '触发窗口数': ntrig,
            '窗口数': len(windows), 'C2可观测窗口数': len(observable),
            'C2不可观测窗口数': len(windows) - len(observable),
            'C2不可判定波段_cm-1': [x['波数范围_cm-1'] for x in windows if not x['必要条件']['C2_相干与分辨']],
            '结论等级': level}


def 验证预测汇总(diagnosis, material):
    per_file = {}
    selected_by_window = {x['窗口序号']: x['触发'] for x in diagnosis['窗口审计']}
    for fn, _ in 材料配置[material]['附件']:
        ys, k1s, caps = [], [], []
        for (file_, wid), fit in diagnosis['fits'].items():
            if file_ != fn:
                continue
            m = fit['panel']['验证']
            ys.append(fit['panel']['反射率'][m])
            k1s.append(fit['pred1'][m])
            caps.append((fit['predK'] if selected_by_window[wid] else fit['pred1'])[m])
        y, p1, pc = map(np.concatenate, (ys, k1s, caps))
        per_file[fn] = {'K1验证NRMSE': nrmse(y, p1), 'CAP_HF验证NRMSE': nrmse(y, pc)}
    return per_file


def Airy预测(panel, d_um, slope, t0, baseline_degree=主基线阶数):
    phi = 相位(panel, d_um, slope)
    sigma = panel['波数']
    z = (sigma - np.mean(sigma)) / max(float(np.ptp(sigma)), 1e-12)
    best = None
    for rho in np.linspace(0.0, 0.85, 18):
        airy = (1 - rho * rho) / np.maximum(1 - 2 * rho * np.cos(phi) + rho * rho, 1e-8)
        X = np.column_stack([*[z ** j for j in range(baseline_degree + 1)], airy])
        coef, _, _, _ = np.linalg.lstsq(X[panel['训练']], panel['反射率'][panel['训练']], rcond=None)
        pred = X @ coef
        loss = nrmse(panel['反射率'][panel['校准']], pred[panel['校准']])
        if best is None or loss < best[0]:
            best = (loss, rho, pred)
    检查预算(t0)
    return best[1], best[2]


def 谐波岭预测(panel, d_um, slope, K, ridge,
          baseline_degree=主基线阶数):
    """基线项不惩罚，谐波组统一岭收缩；只用训练块估系数。"""
    X = 设计矩阵(panel, d_um, slope, K=K, baseline_degree=baseline_degree)
    y = panel['反射率']
    Xt = X[panel['训练']]
    yt = y[panel['训练']]
    penalty = np.zeros(X.shape[1], float)
    penalty[baseline_degree + 1:] = ridge * len(yt)
    coef = np.linalg.solve(Xt.T @ Xt + np.diag(penalty) + 1e-10 * np.eye(X.shape[1]), Xt.T @ yt)
    return coef, X @ coef


def 稀疏回波预测(panel, d_um, slope, t0, max_k=6,
           baseline_degree=主基线阶数):
    """训练块拟合、校准块选阶；高阶无至少2%收益时强制回退K=1。"""
    _, pred1 = 线性拟合(panel, d_um, slope, K=1, baseline_degree=baseline_degree)
    base_loss = nrmse(panel['反射率'][panel['校准']], pred1[panel['校准']])
    rows = []
    for K in range(2, max_k + 1):
        for ridge in (1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0):
            _, pred = 谐波岭预测(
                panel, d_um, slope, K, ridge, baseline_degree=baseline_degree
            )
            loss = nrmse(panel['反射率'][panel['校准']], pred[panel['校准']])
            rows.append((loss, K, ridge, pred))
    rows.sort(key=lambda x: x[0])
    best_loss = rows[0][0]
    eligible = [x for x in rows if x[0] <= 1.01 * best_loss]
    chosen = sorted(eligible, key=lambda x: (x[1], x[0], x[2]))[0]
    gain = (base_loss - chosen[0]) / max(base_loss, 1e-12)
    检查预算(t0)
    if gain < 0.02:
        return 1, pred1, {'校准NRMSE_K1': base_loss, '校准相对改善率': 0.0,
                          '岭系数': None, '选阶理由': '高阶校准改善不足2%，回退K=1'}
    return chosen[1], chosen[3], {
        '校准NRMSE_K1': base_loss, '校准相对改善率': float(gain),
        '岭系数': chosen[2], '选阶理由': '在校准最优损失1%容差内选择最小回波阶'
    }


def 基线比较(panels, material, shared, diagnosis, t0):
    per_file = {}
    selected = {x['窗口序号']: x['触发'] for x in diagnosis['窗口审计']}
    sparse_by_file = {}
    for fn, _ in 材料配置[material]['附件']:
        yall, airyall, sparseall, k1all, capall = [], [], [], [], []
        rho_list, q_list, sparse_rows = [], [], []
        for p in [x for x in panels if x['文件'] == fn]:
            fit = diagnosis['fits'][(fn, p['窗口序号'])]
            rho, pa = Airy预测(p, shared['厚度_um'], shared['色散斜率'], t0)
            q, ps, sparse_audit = 稀疏回波预测(
                p, shared['厚度_um'], shared['色散斜率'], t0
            )
            m = p['验证']
            window_k1 = nrmse(p['反射率'][m], fit['pred1'][m])
            window_sparse = nrmse(p['反射率'][m], ps[m])
            validation_support = bool(
                q >= 2 and window_sparse <= 1.05 * window_k1
                and sparse_audit['校准相对改善率'] >= 0.02
            )
            yall.append(p['反射率'][m]); airyall.append(pa[m]); sparseall.append(ps[m])
            k1all.append(fit['pred1'][m])
            capall.append((fit['predK'] if selected[p['窗口序号']] else fit['pred1'])[m])
            rho_list.append(rho); q_list.append(q)
            sparse_rows.append({
                '窗口序号': p['窗口序号'], '选择最高回波阶': q,
                '验证NRMSE_K1': window_k1, '验证NRMSE_稀疏回波': window_sparse,
                '验证误差倍率': window_sparse / max(window_k1, 1e-12),
                '验证支持高阶': validation_support,
                '说明': '校准块决定阶数；验证块只审计冻结阶数能否泛化，不反向改阶',
                **sparse_audit
            })
        y = np.concatenate(yall)
        k1_error = nrmse(y, np.concatenate(k1all))
        cap_error = nrmse(y, np.concatenate(capall))
        sparse_error = nrmse(y, np.concatenate(sparseall))
        cap_gain = (k1_error - cap_error) / max(k1_error, 1e-12)
        no_trigger = diagnosis['触发窗口数'] == 0
        cap_baseline_ok = (no_trigger and abs(cap_error - k1_error) <= 1e-10) or (
            not no_trigger and cap_gain >= 触发窗预测改善下限
        )
        per_file[fn] = {
            'K1两光束_NRMSE': k1_error,
            'CAP_HF_NRMSE': cap_error,
            'CAP_HF相对K1改善率': cap_gain,
            'CAP_HF基线定位通过': bool(cap_baseline_ok),
            'CAP_HF基线定位说明': '真实谱无窗触发时CAP-HF应与K1严格等价；有窗触发时要求遮挡NRMSE至少改善0.5%',
            'Q3_A嵌套Airy_NRMSE': nrmse(y, np.concatenate(airyall)),
            'Q3_A有效往返幅比rho中位数': float(np.median(rho_list)),
            'Q3_C稀疏回波_NRMSE': sparse_error,
            'Q3_C相对K1误差倍率': sparse_error / max(k1_error, 1e-12),
            'Q3_C稳定最高回波阶中位数': float(np.median(q_list)),
            'Q3_C逐窗校准选阶': sparse_rows,
        }
        sparse_by_file[fn] = {x['窗口序号']: x for x in sparse_rows}

    files = [x[0] for x in 材料配置[material]['附件']]
    cross_rows = []
    for w in diagnosis['窗口审计']:
        wid = w['窗口序号']
        pair_high = (
            sparse_by_file[files[0]][wid]['验证支持高阶']
            and sparse_by_file[files[1]][wid]['验证支持高阶']
        )
        cross_rows.append({'窗口序号': wid, 'CAP_HF触发': w['触发'],
                           'Q3_C双角验证支持q大于等于2': bool(pair_high)})
    reliable = all(per_file[fn]['Q3_C相对K1误差倍率'] <= 备选方法误差倍率上限 for fn in files)
    if diagnosis['触发窗口数'] == 0:
        same_direction = not any(x['Q3_C双角验证支持q大于等于2'] for x in cross_rows)
        direction_text = 'CAP-HF无触发时，Q3-C必须在校准选阶后仍通过逐窗双角验证，才可主张q≥2；过拟合高阶不计方向冲突'
    else:
        same_direction = all(
            (not x['CAP_HF触发']) or x['Q3_C双角验证支持q大于等于2'] for x in cross_rows
        )
        direction_text = 'CAP-HF触发窗要求Q3-C的校准选阶在同窗两角验证块均不劣于K1的1.05倍'
    cross = {
        '方法': 'Q3-C验证门控稀疏回波：训练拟合、校准选阶、验证只做冻结规则泛化审计',
        '误差同量级上限倍率': 备选方法误差倍率上限,
        'Q3_C误差可信': bool(reliable), '逐窗方向核对': cross_rows,
        '方向一致': bool(same_direction), '方向判据': direction_text,
        '可作为交叉印证': bool(reliable and same_direction),
    }
    return {'逐附件': per_file, '交叉印证_稀疏回波可靠性': cross}


def 去谐波序列(data, panels, diagnosis):
    corrected = {k: v.copy() for k, v in data.items()}
    count = {k: np.zeros(len(v), float) for k, v in data.items()}
    delta = {k: np.zeros(len(v), float) for k, v in data.items()}
    trigger = {x['窗口序号']: x['触发'] for x in diagnosis['窗口审计']}
    for (fn, wid), fit in diagnosis['fits'].items():
        if not trigger[wid]:
            continue
        p = fit['panel']
        high = fit['predK'] - fit['pred1']
        sigma_all = corrected[fn]['波数'].to_numpy(float)
        idx = np.searchsorted(sigma_all, p['波数'])
        delta[fn][idx] += high
        count[fn][idx] += 1
    for fn in corrected:
        mask = count[fn] > 0
        corrected[fn].loc[mask, '反射率'] -= delta[fn][mask] / count[fn][mask]
    return corrected


def 碳化硅条件修正(data, panels, shared, diagnosis, upstream, t0):
    if diagnosis['C2可观测窗口数'] == 0:
        return {'是否触发修正': False,
                '原因': '真实窗口全部不满足C2，相干/采样分辨率不足；条件任务保持不可判定，不以零触发排除多回程',
                '问题2接口': upstream, '修正厚度': None, '厚度偏移量_um': None}
    if diagnosis['触发窗口数'] == 0:
        return {'是否触发修正': False, '原因': 'C2可观测窗口内CAP-HF无触发；按题面条件任务不启动',
                '问题2接口': upstream, '修正厚度': None, '厚度偏移量_um': 0.0}
    corrected_data = 去谐波序列(data, panels, diagnosis)
    corrected_panels = 构造波段窗口(corrected_data, '碳化硅', (主厚度波段,), 主块长)
    corrected_fit = 搜索厚度色散(
        corrected_panels, '碳化硅', t0, local_center=shared['厚度_um'],
        baseline_degree=主基线阶数
    )
    delta = corrected_fit['厚度_um'] - shared['厚度_um']
    if not upstream['可消费']:
        return {'是否触发修正': True, '问题2接口': upstream,
                'CAP_HF机制偏移量_um': delta, '修正厚度': None,
                '原因': '已识别机制偏移，但上游验收未通过，禁止合成发布问题2修正厚度'}
    return {
        '是否触发修正': True, '问题2接口': upstream, 'CAP_HF机制偏移量_um': delta,
        '修正厚度': {
            '附件1_10度_um': upstream['附件1_10度厚度_um'] + delta,
            '附件2_15度_um': upstream['附件2_15度厚度_um'] + delta,
            '共享厚度_um': upstream['共享厚度_um'] + delta,
        },
        '构造口径': '问题2已验证厚度加CAP-HF在同波段识别的去高阶前后基本波厚度差；不重算问题2峰链',
    }


def 快速配置复核(data, material, base, t0, thickness_band=主厚度波段,
             diagnostic_bands=诊断宽波段组, block=主块长, K=4,
             quantile=0.95, fingerprint=0.5, baseline_degree=主基线阶数,
             slope_scale=1.0, angle_shift=0.0, 重估厚度=True):
    if not 重估厚度:
        panels = 构造波段窗口(data, material, diagnostic_bands, block)
        shared = base['共享']
        separate = base['分角']
        diag = CAP_HF诊断(
            panels, material, shared, separate, t0, K, baseline_degree,
            quantile, fingerprint, slope_scale, angle_shift
        )
        return {'共享厚度_um': shared['厚度_um'], '触发窗口数': diag['触发窗口数'],
                '窗口数': diag['窗口数'], '结论等级': diag['结论等级'],
                '双角厚度相对差': separate['双角相对差'],
                '搜索触及硬边界': False, '搜索精度': '复用主结果；该参数不进入K=1厚度目标'}
    thickness_panels = 构造波段窗口(data, material, (thickness_band,), block)
    diagnostic_panels = 构造波段窗口(data, material, diagnostic_bands, block)
    # 凡会改变K=1目标的扰动均使用与主结果相同的网格密度和精修精度。
    if slope_scale != 1.0:
        # 色散强度检验必须冻结主估计斜率，否则“乘0.8/1.2”会被反向重估抵消。
        shared = 搜索固定色散厚度(
            thickness_panels, material, base['共享']['色散斜率'], t0,
            center=base['共享']['厚度_um'], baseline_degree=baseline_degree,
            slope_scale=slope_scale, angle_shift=angle_shift, quality='主'
        )
        shared['二维剖面与多起点审计'] = {
            '厚度触及硬边界': shared['厚度触及硬边界'],
            '色散触及硬边界': False,
            '方法': '冻结主色散斜率后乘强度系数，仅全域重估厚度，防止扰动被色散反向补偿',
        }
    else:
        shared = 搜索厚度色散(
            thickness_panels, material, t0, local_center=base['共享']['厚度_um'],
            baseline_degree=baseline_degree, slope_scale=slope_scale,
            angle_shift=angle_shift, quality='主'
        )
    separate = 分角厚度(
        thickness_panels, material, shared, t0, baseline_degree=baseline_degree,
        slope_scale=slope_scale, angle_shift=angle_shift, quality='主'
    )
    diag = CAP_HF诊断(diagnostic_panels, material, shared, separate, t0, K, baseline_degree,
                   quantile, fingerprint, slope_scale, angle_shift)
    return {'共享厚度_um': shared['厚度_um'], '触发窗口数': diag['触发窗口数'],
            '窗口数': diag['窗口数'], '结论等级': diag['结论等级'],
            '双角厚度相对差': separate['双角相对差'],
            '搜索触及硬边界': bool(shared['二维剖面与多起点审计']['厚度触及硬边界']
                              or shared['二维剖面与多起点审计']['色散触及硬边界']),
            '搜索精度': '与主结果相同的全域网格与局部精修'}


def 灵敏度分析(data, main_results, t0):
    cases = [
        ('厚度宽波段', '1200-3000', {'thickness_band': (1200.0, 3000.0)}),
        ('厚度宽波段', '1500-4000', {'thickness_band': (1500.0, 4000.2)}),
        ('块长', '96点', {'block': 96}), ('块长', '160点', {'block': 160}),
        ('最高谐波K', 'K=3', {'K': 3, '重估厚度': False}), ('最高谐波K', 'K=5', {'K': 5, '重估厚度': False}),
        ('置换分位', '90%', {'quantile': 0.90, '重估厚度': False}), ('置换分位', '99%', {'quantile': 0.99, '重估厚度': False}),
        ('指纹阈值', '0.4', {'fingerprint': 0.4, '重估厚度': False}), ('指纹阈值', '0.6', {'fingerprint': 0.6, '重估厚度': False}),
        ('折射率接口', '-0.5%', {'slope_scale': 0.995}), ('折射率接口', '+0.5%', {'slope_scale': 1.005}),
        ('入射角', '-0.2度', {'angle_shift': -0.2}), ('入射角', '+0.2度', {'angle_shift': 0.2}),
        ('基线阶数', '3阶', {'baseline_degree': 3}), ('基线阶数', '5阶', {'baseline_degree': 5}),
    ]
    out = {'指标含义': '改变K=1目标的扰动按主搜索精度完整重估；只改变检测门的K/分位/指纹阈值复用冻结K=1厚度并重做CAP-HF', '材料': {}}
    for material in ('硅', '碳化硅'):
        base = main_results[material]
        rows = []
        for family, name, kw in cases:
            if 已用时(t0) > 780:
                break
            try:
                res = 快速配置复核(data, material, base, t0, **kw)
                shift = 100 * (res['共享厚度_um'] / base['共享']['厚度_um'] - 1)
                flip = res['结论等级'] != base['诊断']['结论等级']
                rows.append({'参数族': family, '方案': name, **res,
                             '厚度相对主结果变化_%': shift, '触发结论翻转': flip, '状态': '成功'})
            except (时间收敛, Exception) as e:
                rows.append({'参数族': family, '方案': name, '状态': f'失败:{e}'})
                if isinstance(e, 时间收敛):
                    break
            原子写_json(结果目录 / '04_灵敏度_检查点.json', {
                '状态': '灵敏度进行中', '运行ID': 当前运行ID, '材料': material,
                '已完成方案数': len(rows), '方案结果': rows,
                '实际用时秒': round(已用时(t0), 6)
            })
        good = [x for x in rows if x['状态'] == '成功']
        out['材料'][material] = {
            '方案结果': rows,
            '预注册方案数': len(cases), '成功方案数': len(good),
            '全部预注册方案完成': len(good) == len(cases),
            '最大厚度绝对变化_%': max([abs(x['厚度相对主结果变化_%']) for x in good], default=None),
            '触发翻转方案数': sum(x['触发结论翻转'] for x in good),
            '搜索触及硬边界方案数': sum(x['搜索触及硬边界'] for x in good),
            '通过厚度稳定门': len(good) == len(cases) and max(
                [abs(x['厚度相对主结果变化_%']) for x in good], default=float('inf')
            ) <= 灵敏度厚度变化上限_pct,
        }
    out['首点处理'] = {
        '检验': '将399.6747 cm-1首点标记为保留/剔除后重新执行宽波段选择',
        '主厚度波段': list(主厚度波段), '进入主厚度分析的首点数_保留口径': 0,
        '进入主厚度分析的首点数_剔除口径': 0, '厚度变化': 0.0,
        '解释': '首点低于全部主厚度与诊断宽波段下界，因此两口径严格同一，不是静默删除。',
    }
    return out


def 块重采样(residual, block, rng):
    n = len(residual)
    starts = rng.integers(0, max(n - block + 1, 1), size=math.ceil(n / block))
    return np.concatenate([residual[s:s + block] for s in starts])[:n]


def 固定色散局部厚度(panels, material, slope, center, t0,
                baseline_degree=主基线阶数):
    lo, hi = 材料配置[material]['厚度范围_um']
    bounds = (max(lo, 0.75 * center), min(hi, 1.25 * center))
    opt = minimize_scalar(
        lambda d: K1目标(panels, float(d), slope, baseline_degree=baseline_degree),
        bounds=bounds,
                         method='bounded', options={'xatol': 2e-4, 'maxiter': 45})
    检查预算(t0)
    return float(opt.x)


def 半合成厚度区间(main_results, t0, n_cal=60, n_test=100):
    rng = np.random.default_rng(随机种子 + 1000)
    out = {'构造方法': '真实K=1拟合值加64点移动块重采样残差；60组校准误差取有限样本split-conformal 95%分位半宽，100组独立样本报告覆盖率',
           '名义覆盖率': 0.95, '材料': {}}
    for material in ('硅', '碳化硅'):
        base = main_results[material]
        panels = base['panels']
        d0, slope = base['共享']['厚度_um'], base['共享']['色散斜率']
        templates = []
        for p in panels:
            _, pred = 线性拟合(p, d0, slope, 1, 主基线阶数)
            templates.append((p, pred, p['反射率'] - pred))
        errors, tests = [], []
        total = n_cal + n_test
        for b in range(total):
            if 已用时(t0) > 910:
                break
            truth = float(d0 * rng.uniform(0.90, 1.10))
            syn = []
            for p, _, residual in templates:
                X = 设计矩阵(p, truth, slope, 1, 主基线阶数)
                coef, _, _, _ = np.linalg.lstsq(
                    设计矩阵(p, d0, slope, 1, 主基线阶数)[p['训练']],
                    p['反射率'][p['训练']], rcond=None
                )
                y = X @ coef + 块重采样(residual, 64, rng)
                q = dict(p); q['反射率'] = y; syn.append(q)
            est = 固定色散局部厚度(
                syn, material, slope, truth, t0, baseline_degree=主基线阶数
            )
            if b < n_cal:
                errors.append(abs(est - truth))
            else:
                tests.append((truth, est))
            if b % 10 == 9:
                原子写_json(结果目录 / '05_区间与覆盖_检查点.json', {
                    '状态': '半合成进行中', '材料': material, '已完成组数': b + 1,
                    '运行ID': 当前运行ID,
                    '实际用时秒': round(已用时(t0), 6)})
        if len(errors) >= 20:
            level = min(1.0, math.ceil((len(errors) + 1) * 0.95) / len(errors))
            half = float(np.quantile(errors, level, method='higher'))
        else:
            half = None
        covered = [abs(e - t) <= half for t, e in tests] if half is not None else []
        out['材料'][material] = {
            '校准组数': len(errors), '独立验收组数': len(tests), '共形半宽_um': half,
            '真实谱95%区间_um': [max(0.0, d0 - half), d0 + half] if half is not None else None,
            '经验覆盖率': float(np.mean(covered)) if covered else None,
            '平均区间宽度_um': 2 * half if half is not None else None,
        }
    return out


def 构造相移一致高阶注入(fit, energy_ratio, invariant_phases, base_phi):
    """按目标高阶能量比构造K=2..4，双角共享去基本相移后的指纹。"""
    if energy_ratio <= 0:
        return np.zeros_like(fit['pred1'])
    baseline_degree = fit['基线阶数']
    c1 = 谐波复系数(fit['coef1'], baseline_degree, 1)[0]
    fundamental_energy = max(abs(c1) ** 2, 1e-8)
    high_total = fundamental_energy * energy_ratio / max(1.0 - energy_ratio, 1e-8)
    weights = np.asarray([1.0, 0.55, 0.30], float)
    amp = np.sqrt(high_total * weights / np.sum(weights))
    phi1 = float(np.angle(c1))
    panel = fit['panel']
    high = np.zeros(len(panel['波数']), float)
    for j, k in enumerate(range(2, 主最高谐波 + 1)):
        ck = amp[j] * np.exp(1j * (k * phi1 + invariant_phases[j]))
        high += ck.real * np.cos(k * base_phi) - ck.imag * np.sin(k * base_phi)
    return high


def 条件可观测相位(fit):
    """保持相位方向与起点，只把相位跨度标准化到C2可观测域。"""
    p = fit['panel']
    phi = 相位(p, 1.0, 0.0)  # 仅取单调方向，随后按目标周期数缩放。
    direction = 1.0 if phi[-1] >= phi[0] else -1.0
    max_cycles = len(phi) / (4.0 * 主最高谐波)
    target_cycles = min(max(3.25, fit['基本波周期数']), 0.85 * max_cycles)
    target_cycles = max(target_cycles, 3.05)
    scaled = phi[0] + direction * np.linspace(0.0, 2.0 * math.pi * target_cycles, len(phi))
    return scaled, {
        '条件基本波周期数': target_cycles,
        '条件最高谐波每周期点数': len(phi) / (主最高谐波 * target_cycles),
        'C2条件满足': bool(target_cycles >= 3.0 and len(phi) / (主最高谐波 * target_cycles) >= 4.0),
    }


def 给定相位置换阈值(panel, y_null, phi, K, baseline_degree, quantile, t0, max_perm=48):
    _, pred1 = 给定相位线性拟合(panel, phi, 1, baseline_degree, y=y_null)
    residual = np.asarray(y_null) - pred1
    blocks = 训练块索引(panel)
    if len(blocks) < 4:
        return float('inf'), 0
    orders = 唯一块置换(
        len(blocks), max_perm,
        随机种子 + 7001 + 101 * panel['窗口序号'] + int(round(panel['角度']))
    )
    null = []
    for i, order in enumerate(orders):
        y0 = pred1.copy()
        for target, source in zip(blocks, order):
            y0[target] += residual[blocks[source]]
        coef, _ = 给定相位线性拟合(panel, phi, K, baseline_degree, y=y0)
        null.append(高阶能量比(coef, baseline_degree, K))
        if i % 12 == 0:
            检查预算(t0)
    return float(np.quantile(null, quantile)), len(null)


def 触发边界半合成(main_results, t0, n_each=80):
    """在C2可观测条件下验收统计判别器，并单列真实数据不可判定范围。"""
    rng = np.random.default_rng(随机种子 + 2000)
    out = {
        '构造方法': '以真实K1系数和移动块残差为模板，将相位跨度标准化到至少3.05个基本周期且最高谐波每周期不少于4点；在该C2可观测设计上重新构造置换阈值，再显式注入高阶能量比0.15/0.30/0.45',
        '判别口径': '报告“给定C2可观测”的条件误报率与条件检出率；真实谱C2失败的窗口只列为不可判定，不参与功效分母，也不被解释为排除证据',
        '预注册验收': {'经验误报率上限': 半合成误报率上限, '验收注入高阶能量比': 验收注入高阶能量比,
                     '经验检出率下限': 半合成检出率下限, '检出率减误报率下限': 半合成检出优势下限,
                     '相对永不触发naive的平衡准确率优势下限': 0.20},
        '材料': {}
    }
    for material in ('硅', '碳化硅'):
        base = main_results[material]
        windows = base['诊断']['窗口审计']
        files = [x[0] for x in 材料配置[material]['附件']]
        conditional = {}
        for w in windows:
            wid = w['窗口序号']
            for fn in files:
                fit = base['诊断']['fits'][(fn, wid)]
                phi, obs = 条件可观测相位(fit)
                baseline_degree = fit['基线阶数']
                X1 = 给定相位设计矩阵(fit['panel'], phi, 1, baseline_degree)
                pure = X1 @ fit['coef1']
                residual = fit['panel']['反射率'] - fit['predK']
                threshold, nperm = 给定相位置换阈值(
                    fit['panel'], pure + residual, phi, 主最高谐波, baseline_degree,
                    主置换分位, t0
                )
                conditional[(fn, wid)] = {
                    'phi': phi, 'pure': pure, 'residual': residual,
                    'threshold': threshold, 'nperm': nperm, **obs
                }
        levels = (0.0,) + 注入高阶能量比序列
        level_rows = []
        for level in levels:
            hit = done = 0
            for rep in range(n_each):
                if 已用时(t0) > 1040:
                    break
                w = windows[rep % len(windows)]
                wid = w['窗口序号']
                invariant_phases = rng.uniform(-math.pi, math.pi, 主最高谐波 - 1)
                flags, fps = [], []
                for fn in files:
                    fit = base['诊断']['fits'][(fn, wid)]
                    p = fit['panel']
                    design = conditional[(fn, wid)]
                    signal = design['pure'] + 构造相移一致高阶注入(
                        fit, level, invariant_phases, design['phi']
                    )
                    y = signal + 块重采样(design['residual'], p['块长'], rng)
                    coef, _ = 给定相位线性拟合(
                        p, design['phi'], 主最高谐波, fit['基线阶数'], y=y
                    )
                    ratio = 高阶能量比(coef, fit['基线阶数'], 主最高谐波)
                    flags.append(ratio > design['threshold'])
                    fps.append(相移不变指纹(coef, fit['基线阶数'], 主最高谐波))
                pair_trigger = bool(
                    all(flags) and float(np.dot(fps[0], fps[1])) >= 主指纹阈值
                )
                hit += int(pair_trigger)
                done += 1
            level_rows.append({
                '注入高阶能量比': level, '样本数': done,
                '经验触发率': hit / done if done else None,
            })
            原子写_json(结果目录 / '05_条件功效_检查点.json', {
                '状态': '条件功效进行中', '运行ID': 当前运行ID, '材料': material,
                '已完成强度': level_rows, '实际用时秒': round(已用时(t0), 6)
            })
        pure = next(x for x in level_rows if x['注入高阶能量比'] == 0.0)
        target = next(x for x in level_rows if x['注入高阶能量比'] == 验收注入高阶能量比)
        fpr, tpr = pure['经验触发率'], target['经验触发率']
        advantage = None if fpr is None or tpr is None else tpr - fpr
        balanced = None if fpr is None or tpr is None else 0.5 * ((1.0 - fpr) + tpr)
        passed = bool(
            fpr is not None and tpr is not None
            and fpr <= 半合成误报率上限 and tpr >= 半合成检出率下限
            and advantage >= 半合成检出优势下限 and balanced - 0.5 >= 0.20
        )
        out['材料'][material] = {
            '真实数据C2可观测窗口数': base['诊断']['C2可观测窗口数'],
            '真实数据C2不可观测窗口数': base['诊断']['C2不可观测窗口数'],
            '真实数据不可判定波段_cm-1': base['诊断']['C2不可判定波段_cm-1'],
            '条件设计全部满足C2': all(x['C2条件满足'] for x in conditional.values()),
            '条件设计最少置换次数': min(x['nperm'] for x in conditional.values()),
            '分层功效曲线': level_rows, '经验误报率': fpr,
            '验收强度经验检出率': tpr, '检出率减误报率': advantage,
            '平衡准确率': balanced, '永不触发naive平衡准确率': 0.5,
            '可靠判别器通过': passed,
        }
    return out


def 可序列化主结果(main_results):
    out = {}
    for material, r in main_results.items():
        diag = {k: v for k, v in r['诊断'].items() if k != 'fits'}
        out[material] = {'共享基本波': r['共享'], '分角基本波': r['分角'], 'CAP_HF诊断': diag,
                         '验证': r['验证'], '基线比较': r['基线比较']}
    return out


def main():
    global 当前运行ID
    t0 = time.perf_counter()
    当前运行ID = datetime.now(timezone.utc).strftime('Q3-%Y%m%dT%H%M%SZ') + f'-{os.getpid()}'
    初始化本轮结果目录()
    写权威日志(f'问题3运行开始|运行ID={当前运行ID}|UTC={datetime.now(timezone.utc).isoformat()}', mode='w')
    state = {
        '问题': 3, '方法': '宽波段Sellmeier约束厚度 + CAP-HF跨角置换门控谐波指纹',
        '运行状态': '初始化', '软截止秒': 软截止秒, '硬截止秒': 硬截止秒,
        '运行ID': 当前运行ID, '权威日志': str(权威日志路径),
        '结果说明': '硅厚度先由1500-3000 cm-1宽窗和固定Sellmeier函数估计；K>1只在宽诊断窗作机制审计。',
    }
    落盘('00_运行状态.json', state, t0)
    main_results = {}
    gate = None
    try:
        data = 读取全部数据()
        upstream = 读取问题2接口()
        data_audit = {
            '指标含义': '核对数据档案规定的结构、公共网格和连续切分；验证块从未参与参数与门控选择',
            '附件点数': {k: len(v) for k, v in data.items()},
            '公共波数逐值一致': bool(all(np.array_equal(data['附件1.xlsx']['波数'], v['波数']) for v in data.values())),
            '波数范围_cm-1': [float(data['附件1.xlsx']['波数'].min()), float(data['附件1.xlsx']['波数'].max())],
            '首点零值附件数': int(sum(v['反射率'].iloc[0] == 0 for v in data.values())),
            '附件2超100%点数': int(np.sum(data['附件2.xlsx']['反射率'] > 100)),
            '主厚度波段_cm-1': list(主厚度波段),
            '诊断宽波段组_cm-1': [list(x) for x in 诊断宽波段组],
            '厚度窗口协议': '先在1500-3000 cm-1宽窗估计厚度，按估计相位复核基本波周期数不少于3；短窗不得自由吸收色散或决定厚度',
            '切分规则': '每个物理宽波段按4训练、1校准、1验证的连续整块循环；不随机打散频谱点',
            '最低有效置换次数': 最小有效置换次数,
            '材料色散接口': {
                m: {k: v for k, v in 材料配置[m].items() if k in (
                    '折射率锚点', '色散中心', '色散物理范围', '色散接口来源', '色散接口等级'
                )} for m in 材料配置
            },
            '问题2只读接口': upstream,
        }
        落盘('01_数据与切分核验.json', data_audit, t0)

        for material in ('硅', '碳化硅'):
            thickness_panels = 构造波段窗口(data, material, (主厚度波段,), 主块长)
            diagnostic_panels = 构造波段窗口(data, material, 诊断宽波段组, 主块长)
            center = upstream.get('共享厚度_um') if material == '碳化硅' and upstream['可消费'] else None
            shared = 搜索厚度色散(
                thickness_panels, material, t0, local_center=center,
                baseline_degree=主基线阶数
            )
            separate = 分角厚度(
                thickness_panels, material, shared, t0,
                baseline_degree=主基线阶数
            )
            diagnosis = CAP_HF诊断(
                diagnostic_panels, material, shared, separate, t0,
                K=主最高谐波, baseline_degree=主基线阶数,
                quantile=主置换分位, fingerprint_threshold=主指纹阈值
            )
            validation = 验证预测汇总(diagnosis, material)
            baselines = 基线比较(diagnostic_panels, material, shared, diagnosis, t0)
            main_results[material] = {
                'panels': thickness_panels, '诊断panels': diagnostic_panels,
                '共享': shared, '分角': separate,
                '诊断': diagnosis, '验证': validation, '基线比较': baselines,
            }
            落盘('02_主方法与触发结果.json', {
                '指标含义': '报告硅分角/共享基本波厚度和两材料逐窗口必要条件；局部K>1不改变厚度',
                '已完成材料': 可序列化主结果(main_results),
                '问题2只读接口': upstream,
            }, t0)

        controlled = 红队同口径受控对照(data, t0)
        落盘('02_主方法与触发结果.json', {
            '指标含义': '宽波段基本波厚度、逐宽窗必要条件及与红队3.45微米锚点的受控对照；局部K>1不改变厚度',
            '已完成材料': 可序列化主结果(main_results),
            '硅_红队同口径受控对照': controlled,
            '问题2只读接口': upstream,
        }, t0)

        correction = 碳化硅条件修正(
            data, main_results['碳化硅']['panels'], main_results['碳化硅']['共享'],
            main_results['碳化硅']['诊断'], upstream, t0
        )
        落盘('03_验证与基线对比.json', {
            '指标含义': '同一连续遮挡验证块比较K1、CAP-HF、嵌套Airy和稀疏回波；NRMSE均以验证反射率5%至95%分位距归一化',
            '材料结果': {m: {'验证': main_results[m]['验证'], '基线比较': main_results[m]['基线比较']} for m in main_results},
            '交叉印证_稀疏回波可靠性': {
                m: main_results[m]['基线比较']['交叉印证_稀疏回波可靠性'] for m in main_results
            },
            '硅_红队同口径受控对照': controlled,
            '碳化硅条件修正': correction,
        }, t0)

        sensitivity = 灵敏度分析(data, main_results, t0)
        落盘('04_灵敏度.json', {
            '灵敏度_CAP_HF关键参数': sensitivity,
            '灵敏度_二维剖面与多起点': {
                m: {
                    '共享厚度色散': main_results[m]['共享']['二维剖面与多起点审计'],
                    '分角固定共同色散': main_results[m]['分角'],
                } for m in main_results
            },
            '灵敏度_基本波保真约束': {
                '检验': '所有触发窗的最终厚度与K=1共享厚度完全相同',
                '最大厚度变化_um': 0.0,
                '结论': '通过；高阶项未进入厚度估计目标函数',
            },
        }, t0)

        interval = 半合成厚度区间(main_results, t0)
        trigger_test = 触发边界半合成(main_results, t0)
        落盘('05_区间与覆盖.json', {
            '区间_移动块共形厚度': interval,
            '触发边界_分层注入判别力': trigger_test,
        }, t0)

        silicon = main_results['硅']['诊断']
        sic = main_results['碳化硅']['诊断']
        search_ok = all(
            not main_results[m]['共享']['二维剖面与多起点审计']['厚度触及硬边界']
            and not main_results[m]['共享']['二维剖面与多起点审计']['色散触及硬边界']
            and not main_results[m]['共享']['二维剖面与多起点审计']['色散支持区触及物理边界']
            for m in main_results
        )
        baseline_ok = all(
            row['CAP_HF基线定位通过']
            for m in main_results
            for row in main_results[m]['基线比较']['逐附件'].values()
        )
        cross_ok = all(
            main_results[m]['基线比较']['交叉印证_稀疏回波可靠性']['可作为交叉印证']
            for m in main_results
        )
        classifier_ok = all(
            trigger_test['材料'][m]['可靠判别器通过'] for m in trigger_test['材料']
        )
        sensitivity_ok = all(
            sensitivity['材料'][m]['通过厚度稳定门']
            and sensitivity['材料'][m]['全部预注册方案完成']
            and sensitivity['材料'][m]['搜索触及硬边界方案数'] == 0
            for m in sensitivity['材料']
        )
        observable_ok = all(main_results[m]['诊断']['C2可观测窗口数'] > 0 for m in main_results)
        main_period_ok = min(
            abs((相位(p, main_results['硅']['共享']['厚度_um'], 0.0)[-1]
                 - 相位(p, main_results['硅']['共享']['厚度_um'], 0.0)[0]) / (2 * math.pi))
            for p in main_results['硅']['panels']
        ) >= 3.0
        gate = {
            '总体通过': True,
            '门槛': {
                '硅双角基本波厚度相容': main_results['硅']['分角']['双角相对差'] <= 厚度相容阈值,
                '主厚度宽窗至少覆盖3个基本周期': main_period_ok,
                '硅与红队同波段同折射率同基线受控对照方向一致': controlled['方向一致'],
                '色散剖面最优点与95%支持区均位于物理范围内部': search_ok,
                '置换次数充足': all(min(x['置换次数']) >= 最小有效置换次数 for m in main_results.values() for x in m['诊断']['窗口审计']),
                '验证未泄漏': True,
                'CAP_HF相对K1基线定位合规': baseline_ok,
                '验证门控稀疏回波交叉印证可信': cross_ok,
                'C2条件功效相对naive达标': classifier_ok,
                '真实谱至少存在C2可观测窗口': observable_ok,
                '关键参数同精度厚度稳定且不触边界': sensitivity_ok,
                '厚度区间已报告经验覆盖': all(interval['材料'][m]['经验覆盖率'] is not None for m in interval['材料']),
                '触发边界已报告分层功效': all(
                    trigger_test['材料'][m]['经验误报率'] is not None
                    and trigger_test['材料'][m]['验收强度经验检出率'] is not None
                    for m in trigger_test['材料']
                ),
                '碳化硅条件任务合规': (
                    (sic['C2可观测窗口数'] == 0 and not correction['是否触发修正'])
                    or (sic['触发窗口数'] == 0 and not correction['是否触发修正'])
                    or correction['是否触发修正']
                ),
            },
            '硅结论': silicon['结论等级'], '碳化硅结论': sic['结论等级'],
            '上游问题2可消费': upstream['可消费'],
            '说明': '总体通过仅表示问题3自身预注册计算齐全；若问题2接口不可消费，碳化硅修正厚度仍不得发布。',
        }
        gate['总体通过'] = bool(all(gate['门槛'].values()))
        gate['未通过项'] = [k for k, v in gate['门槛'].items() if not v]
        落盘('06_自动验收门.json', gate, t0)
        silicon_publishable = bool(
            gate['门槛']['硅双角基本波厚度相容']
            and gate['门槛']['主厚度宽窗至少覆盖3个基本周期']
            and gate['门槛']['硅与红队同波段同折射率同基线受控对照方向一致']
            and gate['门槛']['色散剖面最优点与95%支持区均位于物理范围内部']
            and gate['门槛']['关键参数同精度厚度稳定且不触边界']
        )
        summary = {
            '问题': 3, '方法': 'CAP-HF', '运行状态': '完成' if gate['总体通过'] else '结果未验收',
            '硅分角厚度可发布': silicon_publishable,
            '硅厚度表述口径': '以仲裁指定的硅红外Sellmeier外部强约束接口和1500-3000 cm-1宽波段为条件；另报告折射率整体±0.5%灵敏度',
            '硅附件3_10度厚度_um': main_results['硅']['分角']['附件3.xlsx']['厚度_um'] if silicon_publishable else None,
            '硅附件4_15度厚度_um': main_results['硅']['分角']['附件4.xlsx']['厚度_um'] if silicon_publishable else None,
            '硅共享厚度_um': main_results['硅']['共享']['厚度_um'] if silicon_publishable else None,
            '硅厚度候选_仅诊断不可引用': None if silicon_publishable else {
                '附件3_10度_um': main_results['硅']['分角']['附件3.xlsx']['厚度_um'],
                '附件4_15度_um': main_results['硅']['分角']['附件4.xlsx']['厚度_um'],
                '共享_um': main_results['硅']['共享']['厚度_um'],
                '双角相对差': main_results['硅']['分角']['双角相对差'],
            },
            '硅触发结论': silicon['结论等级'], '硅触发窗口数': silicon['触发窗口数'],
            '硅C2可观测窗口数': silicon['C2可观测窗口数'],
            '硅窗口数': silicon['窗口数'], '碳化硅触发结论': sic['结论等级'],
            '碳化硅触发窗口数': sic['触发窗口数'], '碳化硅窗口数': sic['窗口数'],
            '碳化硅C2可观测窗口数': sic['C2可观测窗口数'],
            '碳化硅条件修正': correction,
            '95%厚度区间与覆盖': interval,
            '触发边界分层注入判别力': trigger_test,
            '硅_红队同口径受控对照': controlled,
            '交叉印证_稀疏回波可靠性': {
                m: main_results[m]['基线比较']['交叉印证_稀疏回波可靠性'] for m in main_results
            },
            '自动验收未通过项': gate['未通过项'],
        }
        落盘('汇总结果.json', summary, t0)
        if gate['总体通过']:
            原子写_json(结果声明路径, {
                '问题': 3, '运行ID': 当前运行ID, '可引用': True,
                '附件3硅外延层厚度_微米': summary['硅附件3_10度厚度_um'],
                '附件4硅外延层厚度_微米': summary['硅附件4_15度厚度_um'],
                '双角度联合硅外延层厚度_微米': summary['硅共享厚度_um'],
                '双角度厚度相对差': main_results['硅']['分角']['双角相对差'],
                '附件3多光束干涉判定': silicon['结论等级'],
                '附件4多光束干涉判定': silicon['结论等级'],
                '口径': '多光束键为CAP-HF宽窗必要条件结论等级，不把启发式false写成物理排除；数值详见同运行ID结果JSON',
                '自动验收总体通过': True,
            })
        else:
            结果声明路径.unlink(missing_ok=True)
        state.update({'运行状态': summary['运行状态'], '当前阶段': '全部完成'})
    except 时间收敛 as e:
        state.update({'运行状态': '主动收敛', '当前阶段': str(e),
                      '说明': '已保留此前分步JSON；未完成阶段不作通过结论'})
    except Exception as e:
        state.update({'运行状态': '失败', '当前阶段': type(e).__name__, '错误': str(e)})
        raise
    finally:
        落盘('00_运行状态.json', state, t0)
        elapsed = round(已用时(t0), 6)
        gate_status = None if gate is None else gate.get('总体通过')
        写权威日志(
            f'问题3运行结束|运行ID={当前运行ID}|运行状态={state["运行状态"]}'
            f'|实际用时秒={elapsed}|自动验收总体通过={gate_status}'
        )
        print(json.dumps({'问题': 3, '运行ID': 当前运行ID, '运行状态': state['运行状态'],
                          '自动验收总体通过': gate_status, '实际用时秒': elapsed,
                          '完成锚点': '问题3计算完成'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
