"""
캐시 기반 초고속 조건식 스캐너

precomputed_indicators + precomputed_exclusion 테이블을 활용하여
종목당 DB 1회 조회 → 조건 판정만 수행합니다.

일봉 원본 로딩 + 지표 계산 없이 동작하므로
2,771종목 스캔이 4분 → 수 초로 단축됩니다.
"""
import pandas as pd
import numpy as np
from datetime import date
import pymysql
import logging

from config import DBConfig

logger = logging.getLogger(__name__)


class FastConditionScanner:
    """
    사전 계산된 지표 테이블 기반 초고속 스캐너

    precomputed_indicators에서 target_date 행을 조회하고
    조건식 판정만 수행합니다. 지표 계산 = 0.
    """

    def __init__(self):
        self._conn_params = dict(
            host=DBConfig.HOST,
            port=DBConfig.PORT,
            user=DBConfig.USER,
            password=DBConfig.PASSWORD,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
        )
        self._load_stock_names()

    def _get_conn(self, db):
        return pymysql.connect(**self._conn_params, database=db)

    def _load_stock_names(self):
        conn = self._get_conn(DBConfig.STOCK_INFO_DB)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT code, name, market FROM stock_base_info")
                rows = cur.fetchall()
            self.stock_info = {r['code']: r for r in rows}
        finally:
            conn.close()

    def scan_date(self, target_date: date) -> list:
        """
        캐시 기반 전종목 스캔

        1) precomputed_exclusion에서 excluded=0인 종목만 필터
        2) precomputed_indicators에서 해당 날짜 + 전일 데이터 조회
        3) 조건식 판정
        """
        conn = self._get_conn(DBConfig.BACKTEST_DB)
        try:
            with conn.cursor() as cur:
                # ── 제외 종목 제거: 캐시에서 조회 ──
                cur.execute("""
                    SELECT code FROM precomputed_exclusion
                    WHERE date = %s AND excluded = 1
                """, (target_date,))
                excluded_codes = {r['code'] for r in cur.fetchall()}

                # ── 당일 + 전일 지표 조회 ──
                # 전일 = target_date보다 작은 최대 날짜
                cur.execute("""
                    SELECT pi_today.code,
                           -- 당일 원본 캔들 (close, volume, open)
                           dc.close AS close_today,
                           dc.open AS open_today,
                           dc.volume AS volume_today,

                           -- 당일 지표
                           pi_today.ma50       AS ma50_today,
                           pi_today.ma60       AS ma60_today,
                           pi_today.ma200      AS ma200_today,
                           pi_today.vol_ma20   AS vol_ma20_today,
                           pi_today.vol_ma120  AS vol_ma120_today,
                           pi_today.rsi14      AS rsi14_today,
                           pi_today.dev_200    AS dev_200_today,
                           pi_today.ma60_200_dist AS ma60_200_dist_today,
                           pi_today.bb_width   AS bb_width_today,
                           pi_today.ma60_slope_up  AS ma60_slope_today,
                           pi_today.ma200_slope_up AS ma200_slope_today,
                           pi_today.trade_value    AS trade_value_today,
                           pi_today.day_return     AS day_return_today,
                           pi_today.vol_ratio_20   AS vol_ratio_today,
                           pi_today.ma5        AS ma5_today,
                           pi_today.ma20       AS ma20_today,
                           pi_today.ma120      AS ma120_today,

                           -- 전일 지표
                           pi_prev.ma60        AS ma60_prev,
                           pi_prev.ma200       AS ma200_prev,
                           pi_prev.rsi14       AS rsi14_prev,

                           -- 전일 원본 캔들
                           dc_prev.close       AS close_prev,

                           -- 15일전 지표
                           pi_15ago.ma60       AS ma60_15ago,
                           pi_15ago.ma200      AS ma200_15ago,
                           dc_15ago.close      AS close_15ago

                    FROM precomputed_indicators pi_today

                    -- 당일 캔들
                    JOIN {stock_db}.daily_candles dc
                        ON dc.code = pi_today.code AND dc.date = pi_today.date

                    -- 전일 지표 (서브쿼리로 직전 영업일 특정)
                    LEFT JOIN precomputed_indicators pi_prev
                        ON pi_prev.code = pi_today.code
                        AND pi_prev.date = (
                            SELECT MAX(date) FROM precomputed_indicators
                            WHERE code = pi_today.code AND date < pi_today.date
                        )

                    -- 전일 캔들
                    LEFT JOIN {stock_db}.daily_candles dc_prev
                        ON dc_prev.code = pi_today.code
                        AND dc_prev.date = pi_prev.date

                    -- 15일전 (대략 15영업일 전 = 가장 가까운 날짜)
                    LEFT JOIN precomputed_indicators pi_15ago
                        ON pi_15ago.code = pi_today.code
                        AND pi_15ago.date = (
                            SELECT MAX(date) FROM precomputed_indicators
                            WHERE code = pi_today.code
                            AND date <= DATE_SUB(pi_today.date, INTERVAL 20 DAY)
                        )

                    LEFT JOIN {stock_db}.daily_candles dc_15ago
                        ON dc_15ago.code = pi_today.code
                        AND dc_15ago.date = pi_15ago.date

                    WHERE pi_today.date = %s
                      AND pi_today.ma200 IS NOT NULL
                """.format(stock_db=DBConfig.STOCK_DATA_DB), (target_date,))

                rows = cur.fetchall()

        finally:
            conn.close()

        logger.info(
            f"[{target_date}] DB 조회: {len(rows)}종목, "
            f"제외: {len(excluded_codes)}종목"
        )

        passed = []

        for row in rows:
            code = row['code']

            # 프록시 제외
            if code in excluded_codes:
                continue

            # 종목 정보
            info = self.stock_info.get(code, {})
            name = info.get('name', '')
            market = info.get('market', '')

            # ── 조건 평가 ──
            result = self._evaluate_from_row(row)

            if result['pass']:
                result['code'] = code
                result['name'] = name
                result['market'] = market
                result['indicators'] = self._extract_indicators(row)
                passed.append(result)

        logger.info(
            f"[{target_date}] 스캔 완료: {len(passed)}종목 통과"
        )
        return passed

    def _evaluate_from_row(self, r: dict) -> dict:
        """단일 행(precomputed row)에서 조건 평가"""

        conditions = {}

        c_today = r['close_today'] or 0
        c_prev = r['close_prev'] or 0
        v_today = r['volume_today'] or 0

        # A: 120일 거래량 돌파
        conditions['A'] = bool(
            r['vol_ma120_today'] and v_today > r['vol_ma120_today']
        )

        # B: 당일 +20%
        day_ret = r['day_return_today'] or 0
        conditions['B'] = bool(day_ret >= 20)

        # C: RSI 70 상향돌파
        conditions['C'] = bool(
            r['rsi14_today'] and r['rsi14_prev'] and
            r['rsi14_today'] >= 70 and r['rsi14_prev'] < 70
        )

        # D: 60이평 골든크로스
        conditions['D'] = bool(
            r['ma60_today'] and r['ma60_prev'] and c_prev and
            c_today >= r['ma60_today'] and c_prev < r['ma60_prev']
        )

        # E: 200이평 골든크로스
        conditions['E'] = bool(
            r['ma200_today'] and r['ma200_prev'] and c_prev and
            c_today >= r['ma200_today'] and c_prev < r['ma200_prev']
        )

        # F: 거래량 > 20일 평균
        conditions['F'] = bool(
            r['vol_ma20_today'] and v_today > r['vol_ma20_today']
        )

        conditions['G'] = False
        conditions['H'] = False
        conditions['I'] = False

        # J: 200이평 ±5%
        conditions['J'] = bool(
            r['dev_200_today'] is not None and r['dev_200_today'] <= 5.0
        )

        # K: 거래대금 ≥ 30억
        conditions['K'] = bool(
            r['trade_value_today'] and r['trade_value_today'] >= 3_000_000_000
        )

        # N, O 필터
        conditions['N_exclude'] = False
        conditions['O_exclude'] = False

        if r['close_15ago'] and r['ma50_today']:
            ma50_today = r['ma50_today']
            c_15ago = r['close_15ago']

            if r['ma60_15ago'] and r['ma60_15ago'] > 0 and ma50_today > 0:
                ratio_15_60 = c_15ago / r['ma60_15ago']
                ratio_now_50 = c_today / ma50_today
                conditions['N_exclude'] = bool(ratio_now_50 < ratio_15_60)

            if r['ma200_15ago'] and r['ma200_15ago'] > 0 and ma50_today > 0:
                ratio_15_200 = c_15ago / r['ma200_15ago']
                ratio_now_50 = c_today / ma50_today
                conditions['O_exclude'] = bool(ratio_now_50 < ratio_15_200)

        # 최종 판정
        path_event = conditions['A'] and conditions['B']
        path_trend = conditions['C'] or conditions['D'] or conditions['E']

        final_pass = (
            (path_event or path_trend) and
            (conditions['F'] or conditions['G'] or
             conditions['H'] or conditions['I']) and
            conditions['J'] and conditions['K'] and
            (not conditions['N_exclude']) and
            (not conditions['O_exclude'])
        )

        trigger_path = None
        if final_pass:
            if path_event:
                trigger_path = 'event'
            else:
                fired = []
                if conditions['C']:
                    fired.append('C_RSI70')
                if conditions['D']:
                    fired.append('D_MA60GC')
                if conditions['E']:
                    fired.append('E_MA200GC')
                trigger_path = 'trend:' + '+'.join(fired)

        return {
            'pass': final_pass,
            'trigger_path': trigger_path,
            'conditions': conditions,
        }

    def _extract_indicators(self, r: dict) -> dict:
        return {
            'close': r['close_today'],
            'open': r['open_today'],
            'volume': r['volume_today'],
            'day_return': r['day_return_today'],
            'trade_value': r['trade_value_today'],
            'ma5': r['ma5_today'],
            'ma20': r['ma20_today'],
            'ma50': r['ma50_today'],
            'ma60': r['ma60_today'],
            'ma120': r['ma120_today'],
            'ma200': r['ma200_today'],
            'rsi14': r['rsi14_today'],
            'vol_ma20': r['vol_ma20_today'],
            'vol_ma120': r['vol_ma120_today'],
            'vol_ratio_20': r['vol_ratio_today'],
            'dev_200': r['dev_200_today'],
            'ma60_200_dist': r['ma60_200_dist_today'],
            'bb_width': r['bb_width_today'],
            'ma60_slope_up': r['ma60_slope_today'],
            'ma200_slope_up': r['ma200_slope_today'],
        }
