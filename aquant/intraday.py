"""Explicit execution observations: last traded price at 10:00 Asia/Shanghai."""
import csv
import datetime as dt
import math
from .core import ValidationError

FIELDS = ['date', 'symbol', 'timestamp', 'price', 'volume', 'tradable']


def read_quotes(path, calendar, execution_time='10:00:00'):
    quotes = {}
    if not path.exists():
        return quotes
    with path.open(encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        if not set(FIELDS).issubset(reader.fieldnames or []):
            raise ValidationError('intraday.csv 缺少10:00执行观测字段')
        for row in reader:
            date, symbol = row['date'], row['symbol']
            timestamp = dt.datetime.fromisoformat(row['timestamp'])
            if (date not in calendar or timestamp.date().isoformat() != date
                    or timestamp.time() != dt.time.fromisoformat(execution_time)
                    or timestamp.utcoffset() != dt.timedelta(hours=8)):
                raise ValidationError('执行观测必须为交易日'+execution_time+'+08:00，不能混用分钟开始/结束时间')
            price, volume = float(row['price']), float(row['volume'])
            if not math.isfinite(price) or price <= 0 or not math.isfinite(volume) or volume < 0:
                raise ValidationError('10:00价格或前一分钟成交量非法')
            if row['tradable'] not in ('0', '1'):
                raise ValidationError('tradable必须为0/1')
            window = int(row.get('volume_window_minutes') or 1)
            if window not in (1, 5, 15, 30, 60):
                raise ValidationError('成交量窗口非法')
            key = (date, symbol)
            if key in quotes:
                raise ValidationError('重复10:00观测: ' + str(key))
            quotes[key] = dict(timestamp=row['timestamp'], price=price, volume=volume,
                               tradable=row['tradable'] == '1', volume_window_minutes=window)
    return quotes
