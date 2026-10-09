"""Fixed-parameter walk-forward binary classification research candidates."""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from aquant.targets import forward_label
from .features import FEATURE_COLUMNS


MODEL_SETTINGS = {
    'logistic': {'C': 0.01, 'max_iter': 500, 'solver': 'lbfgs', 'random_state': 17},
    'lightgbm': {'n_estimators': 120, 'learning_rate': 0.03, 'num_leaves': 7,
                 'max_depth': 3, 'min_child_samples': 100, 'reg_lambda': 10.0,
                 'colsample_bytree': 0.8, 'random_state': 17, 'n_jobs': 4,
                 'verbosity': -1, 'deterministic': True, 'force_col_wise': True},
    'train_days': 126, 'minimum_train_days': 60, 'retrain_every_days': 1,
    'positive_return_strictly_above': 0.01,
}


class SnapshotCache:
    def __init__(self, data):
        self.data = data
        self.cache = {}

    def __getattr__(self, name):
        return getattr(self.data, name)

    def snapshot(self, date):
        if date not in self.cache:
            self.cache[date] = self.data.snapshot(date)
        return self.cache[date]


def attach_labels(data, cfg, frame, asof):
    cached = SnapshotCache(data)
    labels = [forward_label(cached, row.symbol, row.date, asof, cfg)
              for row in frame[['date', 'symbol']].itertuples(index=False)]
    result = frame.copy()
    result['status'] = [x['status'] for x in labels]
    result['gross_return'] = [x['gross_return'] for x in labels]
    result['label_available_date'] = [x['label_available_date'] for x in labels]
    return result


def make_models():
    return {
        'logistic': make_pipeline(SimpleImputer(strategy='median', add_indicator=False),
                                  StandardScaler(), LogisticRegression(**MODEL_SETTINGS['logistic'])),
        'lightgbm': LGBMClassifier(objective='binary', **MODEL_SETTINGS['lightgbm']),
    }


def binary_target(train):
    """Use only observed gross returns; threshold equality is a negative label."""
    gross = train['gross_return']
    if gross.isna().any():
        raise ValueError('Observed training rows contain missing gross returns')
    target = (gross > MODEL_SETTINGS['positive_return_strictly_above']).astype(int)
    if target.nunique() != 2:
        raise ValueError('Binary training window contains only one class')
    return target


def fit_models(train, feature_columns):
    models = make_models()
    x = train[list(feature_columns)]
    y = binary_target(train)
    for model in models.values():
        model.fit(x, y)
    return models, float(y.mean())


def positive_scores(model, features):
    """Column 1 is the strictly >1% class for both candidate classifiers."""
    classes = model.classes_
    if list(classes) != [0, 1]:
        raise ValueError(f'Unexpected binary classes: {classes}')
    return model.predict_proba(features)[:, 1]


def mature_training_rows(frame, date):
    train = frame.loc[(frame['date'] < date) &
                      (frame['label_available_date'].fillna('9999-12-31') <= date) &
                      (frame['status'] == 'observed')].copy()
    mature_dates = sorted(train['date'].unique())[-MODEL_SETTINGS['train_days']:]
    return train.loc[train['date'].isin(mature_dates)], mature_dates


def walk_forward(frame, evaluation_start, asof, feature_columns=FEATURE_COLUMNS):
    """At each retrain date, use only labels mature by that day's close."""
    dates = sorted(d for d in frame['date'].unique() if evaluation_start <= d <= asof)
    predictions, fits = [], []
    models = None
    last_fit_index = -MODEL_SETTINGS['retrain_every_days']
    for index, date in enumerate(dates):
        if models is None or index - last_fit_index >= MODEL_SETTINGS['retrain_every_days']:
            train, mature_dates = mature_training_rows(frame, date)
            if len(mature_dates) < MODEL_SETTINGS['minimum_train_days']:
                continue
            models, positive_rate = fit_models(train, feature_columns)
            last_fit_index = index
            fits.append({'trained_at': date, 'train_start': mature_dates[0],
                         'train_end': mature_dates[-1],
                         'max_label_available_date': max(train['label_available_date']),
                         'train_days': len(mature_dates), 'samples': len(train),
                         'train_positive_rate': positive_rate})
        current = frame.loc[frame['date'] == date]
        if current.empty or models is None:
            continue
        x = current[list(feature_columns)]
        for model_name, model in models.items():
            scores = positive_scores(model, x)
            for (_, row), score in zip(current.iterrows(), scores):
                predictions.append({'date': date, 'symbol': row['symbol'],
                                    'model': model_name, 'score': float(score),
                                    'status': row['status'],
                                    'gross_return': (None if pd.isna(row['gross_return'])
                                                     else float(row['gross_return'])),
                                    'trained_at': fits[-1]['trained_at']})
    return predictions, fits, models


def evaluate(predictions, first, last):
    """Daily AUC and frozen Top10 hit rates; all are gross diagnostics."""
    grouped = defaultdict(list)
    for row in predictions:
        if first <= row['date'] <= last:
            grouped[(row['model'], row['date'])].append(row)
    by_model = defaultdict(list)
    for (model, date), rows in grouped.items():
        ordered = sorted(rows, key=lambda r: (-r['score'], r['symbol']))
        observed = [r for r in ordered if r['status'] == 'observed' and r['gross_return'] is not None]
        if not observed:
            continue
        daily = {'date': date, 'requested': len(ordered), 'observed': len(observed),
                 'top4_gross_mean': float(np.mean([r['gross_return'] for r in ordered[:4]
                                                  if r in observed])) if any(r in observed for r in ordered[:4]) else None}
        for threshold in (0.01, 0.03, 0.05):
            key = f'gt_{int(threshold * 100)}pct'
            y = [r['gross_return'] > threshold for r in observed]
            top = [r for r in ordered[:10] if r in observed]
            daily[key] = {'auc': float(roc_auc_score(y, [r['score'] for r in observed]))
                          if len(set(y)) == 2 else None,
                          'base_rate': sum(y) / len(y),
                          'top10_hits': sum(r['gross_return'] > threshold for r in top),
                          'top10_observed': len(top)}
        by_model[model].append(daily)
    result = {}
    for model, days in by_model.items():
        metrics = {'evaluated_days': len(days), 'coverage': sum(d['observed'] for d in days) /
                   sum(d['requested'] for d in days),
                   'top4_mean_daily_gross_return': float(np.mean([d['top4_gross_mean'] for d in days
                                                                  if d['top4_gross_mean'] is not None]))}
        for threshold in (1, 3, 5):
            key = f'gt_{threshold}pct'
            rows = [d[key] for d in days]
            aucs = [r['auc'] for r in rows if r['auc'] is not None]
            hits = sum(r['top10_hits'] for r in rows)
            count = sum(r['top10_observed'] for r in rows)
            metrics[key] = {'mean_daily_auc': float(np.mean(aucs)) if aucs else None,
                            'auc_days': len(aucs),
                            'pooled_base_rate': float(np.mean([r['base_rate'] for r in rows])),
                            'top10_hit_rate': hits / count if count else None,
                            'top10_hits': hits, 'top10_observed': count}
        result[model] = metrics
    return result


def rankers(predictions):
    grouped = defaultdict(list)
    for r in predictions:
        grouped[(r['model'], r['date'])].append(r)
    output = defaultdict(dict)
    for (model, date), rows in grouped.items():
        ordered = sorted(rows, key=lambda r: (-r['score'], r['symbol']))
        output[model][date] = [{'symbol': r['symbol'], 'score': r['score'], 'rank': n}
                               for n, r in enumerate(ordered, 1)]
    return output
