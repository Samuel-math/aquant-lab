"""Recreate the frozen 24-formula learner for same-date research comparison."""
from aquant.mining import prepare
from aquant.rolling import fit_and_predict


def formula_predictions(data, cfg, asof, dates):
    features, labels = prepare(data, cfg, asof, lambda: None, workers=4)
    label_map = {(row['signal_date'], row['symbol']): row for row in labels}
    output, fits = [], []
    for date in dates:
        model = fit_and_predict(features, labels, date, count=24, seed=17,
                                train_days=126, workers=1)
        fits.append({'date': date, 'model_id': model['model_id'],
                     'training_days': model['training_days'],
                     'train_max_label_end': model['train_max_label_end'],
                     'winner': model['winner']})
        for prediction in model['predictions']:
            label = label_map[(date, prediction['symbol'])]
            output.append({'date': date, 'symbol': prediction['symbol'],
                           'model': 'frozen_24_formula', 'score': prediction['score'],
                           'status': label['status'], 'gross_return': label['gross_return'],
                           'trained_at': date})
    return output, fits
