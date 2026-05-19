"""포착일 이후 1주/2주/3주/1개월 수익률 및 기간내 최고수익률 계산"""
import pandas as pd
from datetime import date, timedelta
import pymysql
import logging

from config import DBConfig

logger = logging.getLogger(__name__)


class PerformanceCalculator:
    """포착 이후 미래 수익률 계산기"""

    def __init__(self):
        self._conn_params = dict(
            host=DBConfig.HOST,
            port=DBConfig.PORT,
            user=DBConfig.USER,
            password=DBConfig.PASSWORD,
            charset='utf8mb4',
            database=DBConfig.STOCK_DATA_DB,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def _load_future_candles(self, code: str, start_date: date,
                              days_ahead: int = 60) -> pd.DataFrame:
        """포착일 포함 이후 N영업일 일봉 로딩"""
        end_date = start_date + timedelta(days=days_ahead * 2)
        conn = pymysql.connect(**self._conn_params)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT date, open, high, low, close, volume
                    FROM daily_candles
                    WHERE code = %s AND date >= %s AND date <= %s
                    ORDER BY date ASC
                """, (code, start_date, end_date))
                rows = cur.fetchall()
        finally:
            conn.close()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        return df

    def calculate(self, code: str, capture_date: date,
                  capture_close: float = None) -> dict:
        """
        포착일 이후 수익률 계산

        Returns:
            {
                'ret_1w': float or None,   # 5영업일 후 수익률
                'ret_2w': float or None,   # 10영업일 후
                'ret_3w': float or None,   # 15영업일 후
                'ret_1m': float or None,   # 20영업일 후
                'ret_max': float or None,  # 기간(20영업일) 내 최고수익률
                'max_high': float or None, # 기간 내 최고가
                'max_high_date': date or None,
            }
        """
        df = self._load_future_candles(code, capture_date, days_ahead=45)

        if df.empty:
            return {k: None for k in
                    ['ret_1w', 'ret_2w', 'ret_3w', 'ret_1m',
                     'ret_max', 'max_high', 'max_high_date']}

        # 포착일 종가 (기준가)
        capture_ts = pd.Timestamp(capture_date)
        if capture_ts in df.index:
            base_price = float(df.loc[capture_ts, 'close'])
        elif capture_close:
            base_price = capture_close
        else:
            # 포착일 데이터가 없으면 첫 봉 종가 사용
            base_price = float(df.iloc[0]['close'])

        if base_price <= 0:
            return {k: None for k in
                    ['ret_1w', 'ret_2w', 'ret_3w', 'ret_1m',
                     'ret_max', 'max_high', 'max_high_date']}

        # 포착일 다음날부터의 데이터
        # (성과검증은 포착일 종가 기준이므로 포착일 포함)
        future = df[df.index >= capture_ts]

        def ret_at_bar(n):
            """n번째 영업일의 종가 수익률"""
            if len(future) > n:
                return round(
                    (float(future.iloc[n]['close']) - base_price)
                    / base_price * 100, 2
                )
            return None

        # 기간 내 최고수익률 (20영업일 = 약 1개월)
        period_bars = min(len(future), 21)  # 포착일 포함 21봉
        period_data = future.iloc[:period_bars]

        max_high = float(period_data['high'].max())
        max_high_date = period_data['high'].idxmax().date() if not period_data.empty else None
        ret_max = round((max_high - base_price) / base_price * 100, 2)

        return {
            'ret_1w': ret_at_bar(5),       # 5영업일 후
            'ret_2w': ret_at_bar(10),      # 10영업일 후
            'ret_3w': ret_at_bar(15),      # 15영업일 후
            'ret_1m': ret_at_bar(20),      # 20영업일 후
            'ret_max': ret_max,             # 기간 내 최고수익률
            'max_high': max_high,
            'max_high_date': max_high_date,
        }
