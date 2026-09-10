import sys; sys.path.insert(0, '/codex_workspace/pylibs')

import json
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error


DATA_PATH = Path('数据/data.csv')
RESULT_PATH = Path('结果/预测结果.json')
FIGURE_PATH = Path('图片/预测对比图.png')
DATE_FORMAT = '%d/%m/%Y'
LAGS = (1, 2, 3, 12)


def mape(y_true, y_pred):
    """计算 MAPE；真实值为零的月份不参与计算。"""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    nonzero = y_true != 0
    if not np.any(nonzero):
        return None
    return float(np.mean(np.abs((y_true[nonzero] - y_pred[nonzero]) /
                                y_true[nonzero])) * 100)


def make_features(series):
    features = pd.DataFrame(index=series.index)
    for lag in LAGS:
        features[f'lag_{lag}'] = series.shift(lag)
    features['month'] = features.index.month
    features['target'] = series
    return features.dropna()


def recursive_rf_forecast(model, train_series, test_index):
    """仅用训练历史和已预测值递归生成六个月预测。"""
    history = train_series.copy()
    predictions = []
    for month in test_index:
        row = {f'lag_{lag}': history.loc[month - lag] for lag in LAGS}
        row['month'] = month.month
        x_next = pd.DataFrame([row], columns=[
            'lag_1', 'lag_2', 'lag_3', 'lag_12', 'month'
        ])
        prediction = max(0.0, float(model.predict(x_next)[0]))
        predictions.append(prediction)
        history.loc[month] = prediction
    return pd.Series(predictions, index=test_index, name='随机森林')


def evaluate(y_true, y_pred):
    return {
        'MAPE(%)': mape(y_true, y_pred),
        'RMSE': float(np.sqrt(mean_squared_error(y_true, y_pred)))
    }


def main():
    data = pd.read_csv(
        DATA_PATH,
        encoding='GBK',
        usecols=['Order Date', 'Ship Date', 'Sales']
    )
    data['Order Date'] = pd.to_datetime(
        data['Order Date'], format=DATE_FORMAT, errors='raise'
    )
    data['Ship Date'] = pd.to_datetime(
        data['Ship Date'], format=DATE_FORMAT, errors='raise'
    )
    data['Sales'] = pd.to_numeric(data['Sales'], errors='raise')

    ship_year_anomaly = (
        (data['Ship Date'].dt.year == 2019) &
        (data['Order Date'].dt.year == 2025) &
        (data['Ship Date'] < data['Order Date'])
    )
    anomaly_count = int(ship_year_anomaly.sum())

    monthly = (
        data.assign(月份=data['Order Date'].dt.to_period('M'))
        .groupby('月份', sort=True)['Sales']
        .sum()
    )
    full_index = pd.period_range(monthly.index.min(), monthly.index.max(), freq='M')
    monthly = monthly.reindex(full_index, fill_value=0.0).astype(float)

    if len(monthly) < 19:
        raise ValueError('月度序列不足 19 个月，无法使用 12 期滞后并保留 6 个月测试集。')

    train = monthly.iloc[:-6]
    test = monthly.iloc[-6:]
    train_table = make_features(train)
    feature_columns = ['lag_1', 'lag_2', 'lag_3', 'lag_12', 'month']

    rf = RandomForestRegressor(
        n_estimators=500,
        random_state=42,
        min_samples_leaf=2,
        n_jobs=-1
    )
    rf.fit(train_table[feature_columns], train_table['target'])
    rf_prediction = recursive_rf_forecast(rf, train, test.index)

    naive_prediction = pd.Series(
        [float(monthly.loc[month - 12]) for month in test.index],
        index=test.index,
        name='去年同月基线'
    )

    metrics = {
        'naive季节基线': evaluate(test.values, naive_prediction.values),
        '随机森林': evaluate(test.values, rf_prediction.values)
    }
    ranking = sorted(
        metrics,
        key=lambda name: (metrics[name]['MAPE(%)'], metrics[name]['RMSE'])
    )
    best_model = ranking[0]

    monthly_comparison = []
    for month in test.index:
        monthly_comparison.append({
            '月份': str(month),
            '真实销售额': float(test.loc[month]),
            'naive季节基线预测': float(naive_prediction.loc[month]),
            '随机森林预测': float(rf_prediction.loc[month])
        })

    result = {
        '数据处理': {
            '文件编码': 'GBK',
            '日期格式': DATE_FORMAT,
            '月度范围': f'{monthly.index.min()}至{monthly.index.max()}',
            '月数': int(len(monthly)),
            '发货年份异常行数': anomaly_count,
            '异常处理': '仅记录，不修正且不参与本任务特征构造'
        },
        '时间切分': {
            '训练期': f'{train.index.min()}至{train.index.max()}',
            '测试期': f'{test.index.min()}至{test.index.max()}',
            '测试月数': int(len(test))
        },
        '模型指标': metrics,
        '逐月预测vs真实': monthly_comparison,
        '最优模型': best_model,
        '最优模型结论': (
            f'以测试集 MAPE 为首要标准、RMSE 为并列判据，{best_model}表现最优。'
        )
    }

    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESULT_PATH.open('w', encoding='utf-8') as file:
        json.dump(result, file, ensure_ascii=False, indent=2, allow_nan=False)

    plt.rcParams['font.sans-serif'] = [
        'FandolHei', 'Noto Sans CJK SC', 'DejaVu Sans'
    ]
    plt.rcParams['axes.unicode_minus'] = False
    figure, axis = plt.subplots(figsize=(10, 5.5))
    recent = monthly.iloc[-24:]
    recent_dates = recent.index.to_timestamp()
    test_dates = test.index.to_timestamp()
    axis.plot(recent_dates, recent.values, marker='o', linewidth=1.8,
              markersize=4, label='真实月销售额')
    axis.plot(test_dates, naive_prediction.values, marker='s', linewidth=1.8,
              linestyle='--', label='去年同月基线预测')
    axis.plot(test_dates, rf_prediction.values, marker='^', linewidth=1.8,
              linestyle='--', label='随机森林预测')
    axis.set_xlabel('月份')
    axis.set_ylabel('销售额')
    axis.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    axis.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    axis.tick_params(axis='x', rotation=45)
    axis.grid(alpha=0.3, linestyle='--')
    axis.spines['top'].set_visible(False)
    axis.spines['right'].set_visible(False)
    axis.legend()
    figure.tight_layout()
    FIGURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(FIGURE_PATH, dpi=200, bbox_inches='tight')
    plt.close(figure)

    print(f'发货年份异常行数：{anomaly_count}')
    print(f"naive季节基线：MAPE={metrics['naive季节基线']['MAPE(%)']:.4f}%，"
          f"RMSE={metrics['naive季节基线']['RMSE']:.4f}")
    print(f"随机森林：MAPE={metrics['随机森林']['MAPE(%)']:.4f}%，"
          f"RMSE={metrics['随机森林']['RMSE']:.4f}")
    print(f'最优模型：{best_model}')


if __name__ == '__main__':
    main()
