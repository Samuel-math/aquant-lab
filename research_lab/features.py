"""Point-in-time after-close features for the frozen 09:40-to-open target."""
from __future__ import annotations

import numpy as np
import pandas as pd


FEATURE_COLUMNS = (
    'momentum_5', 'momentum_10', 'momentum_20', 'momentum_60',
    'volatility_5', 'volatility_20', 'volatility_60',
    'reversal_1', 'overnight_gap', 'day_return',
    'amount_ratio_5', 'amount_ratio_20', 'volume_ratio_5', 'volume_ratio_20',
    'amihud_20', 'early_price_to_open', 'close_to_early_price',
    'early_volume_share', 'early_volume_surprise_20', 'early_turnover_share',
)
MINUTE_FEATURE_COLUMNS = (
    'first10_volume_share', 'open30_volume_share', 'close30_volume_share',
    'morning_volume_share', 'intraday_volume_concentration',
    'realized_volatility_5m', 'first10_return',
    'close_vs_bar_vwap_proxy', 'first10_share_surprise20',
)


def build_features(data, cfg, asof, minute_summary_path=None):
    """Use only observations at or before each signal day's close.

    Historical rows are conditional on the 2026-09-28 selected pool and must
    not be described as unbiased historical out-of-sample observations.
    """
    from aquant.strategy import rank

    data.validate_mode(cfg)
    bars = pd.read_csv(data.directory / 'bars.csv', dtype={'symbol': str, 'date': str})
    quotes = pd.read_csv(data.directory / 'intraday.csv', dtype={'symbol': str, 'date': str})
    bars = bars.loc[bars['date'] <= asof].sort_values(['symbol', 'date']).copy()
    quotes = quotes.loc[quotes['date'] <= asof, ['date', 'symbol', 'price', 'volume']]
    if quotes.duplicated(['date', 'symbol']).any():
        raise ValueError('Duplicate 09:40 observation')
    frame = bars.merge(quotes.rename(columns={'price': 'early_price', 'volume': 'early_volume'}),
                       on=['date', 'symbol'], how='left', validate='one_to_one')
    group = frame.groupby('symbol', sort=False)
    frame['adjusted_close'] = frame['close'] * frame['adj_factor']
    frame['daily_return'] = group['adjusted_close'].pct_change(fill_method=None)
    frame['reversal_1'] = -frame['daily_return']
    prior_adjusted_close = group['adjusted_close'].shift(1)
    frame['overnight_gap'] = frame['open'] * frame['adj_factor'] / prior_adjusted_close - 1
    frame['day_return'] = frame['close'] / frame['open'] - 1
    frame['early_price_to_open'] = frame['early_price'] / frame['open'] - 1
    frame['close_to_early_price'] = frame['close'] / frame['early_price'] - 1
    frame['early_volume_share'] = frame['early_volume'] / frame['volume'].replace(0, np.nan)
    frame['early_turnover_share'] = (frame['early_volume'] * frame['early_price'] /
                                     frame['amount'].replace(0, np.nan))
    for window in (5, 10, 20, 60):
        frame[f'momentum_{window}'] = frame['adjusted_close'] / group['adjusted_close'].shift(window) - 1
    for window in (5, 20, 60):
        frame[f'volatility_{window}'] = group['daily_return'].transform(
            lambda s: s.rolling(window, min_periods=window).std(ddof=0))
    for window in (5, 20):
        frame[f'amount_ratio_{window}'] = frame['amount'] / group['amount'].transform(
            lambda s: s.shift(1).rolling(window, min_periods=window).mean()).replace(0, np.nan)
        frame[f'volume_ratio_{window}'] = frame['volume'] / group['volume'].transform(
            lambda s: s.shift(1).rolling(window, min_periods=window).mean()).replace(0, np.nan)
    frame['_amihud_daily'] = (frame['daily_return'].abs() /
                              (frame['amount'] / 1e8).replace(0, np.nan))
    frame['amihud_20'] = group['_amihud_daily'].transform(
        lambda s: s.rolling(20, min_periods=20).mean())
    frame['early_volume_surprise_20'] = frame['early_volume'] / group['early_volume'].transform(
        lambda s: s.shift(1).rolling(20, min_periods=20).mean()).replace(0, np.nan)

    columns = FEATURE_COLUMNS
    if minute_summary_path is not None:
        summary = pd.read_csv(minute_summary_path, dtype={'date': str, 'symbol': str})
        required = {'date', 'symbol', 'bar_count', *MINUTE_FEATURE_COLUMNS[:-1]}
        if not required.issubset(summary.columns) or summary.duplicated(['date', 'symbol']).any():
            raise ValueError('Invalid research minute summary')
        summary = summary.loc[(summary['date'] <= asof) & (summary['bar_count'] == 48)]
        frame = frame.merge(summary.drop(columns=['bar_count']), on=['date', 'symbol'],
                            how='left', validate='one_to_one')
        frame['first10_share_surprise20'] = frame['first10_volume_share'] / frame.groupby(
            'symbol')['first10_volume_share'].transform(
            lambda s: s.shift(1).rolling(20, min_periods=20).mean()).replace(0, np.nan)
        columns = FEATURE_COLUMNS + MINUTE_FEATURE_COLUMNS

    frame = frame.loc[frame['early_price'].gt(0) & frame['early_volume'].gt(0) &
                      frame['momentum_60'].notna()].copy()
    # Eligibility is evaluated with the frozen strategy's ex-ante daily filters.
    eligible = {(date, r['symbol']) for date in sorted(frame['date'].unique())
                for r in rank(data, date, cfg)}
    frame = frame.loc[[key in eligible for key in zip(frame['date'], frame['symbol'])]].copy()
    # Cross-sectional ranks make scale comparable without using later dates.
    for col in columns:
        frame[col] = frame[col].replace([np.inf, -np.inf], np.nan)
        frame[col] = frame.groupby('date')[col].rank(pct=True, method='average') * 2 - 1
    return frame[['date', 'symbol', *columns]].reset_index(drop=True)
