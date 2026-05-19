"""
키움 HTS 조건식 재현 스캐너

원본 조건식 구조:
  ((A and B) or (C or D or E)) and (F or (G or H or I)) and J and K and !N and !O

  A: 120일 거래량 상향돌파
  B: 당일 +20% 이상 상승
  C: RSI(14)가 오늘 70 상향 돌파
  D: 종가가 60일 이평 오늘 골든크로스
  E: 종가가 200일 이평 오늘 골든크로스
  F: 오늘 거래량이 20일 평균 거래량 상향돌파
  G~I: 1~3일전 거래량 돌파 (현재 비활성)
  J: 종가가 200일 이평 대비 ±5% 이내
  K: 거래대금 ≥ 30억원
  N: 15일전 종가/60이평 vs 현재 종가/50이평 비교 (하락 필터)
  O: 15일전 종가/200이평 vs 현재 종가/50이평 비교 (하락 필터)

  제외: 일봉 기반 프록시 필터 (exclusion_filter.py)
"""
import pandas as pd
import numpy as np
from datetime import date, timedelta
import pymysql
import logging

from config import DBConfig
from exclusion_filter import PriceBasedExclusionFilter
from indicators import (
    sma, rsi,
    ma_deviation_pct, ma_distance_pct, ma_slope_positive,
    bollinger_bands,
)

logger = logging.getLogger(__name__)


class ConditionScanner:
    """키움 조건식을 일봉 데이터로 재현"""

    WARMUP_DAYS = 250  # 200일 이평 + 여유

    def __init__(self, exclusion_config: dict = None):
        """
        Args:
            exclusion_config: PriceBasedExclusionFilter에 전달할 설정 dict
                              None이면 기본값 사용
        """
        self._conn_params = dict(
            host=DBConfig.HOST,
            port=DBConfig.PORT,
            user=DBConfig.USER,
            password=DBConfig.PASSWORD,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
        )

        # ★ 일봉 기반 프록시 필터 (DB 플래그 사용 안 함)
        self.exclusion_filter = PriceBasedExclusionFilter(exclusion_config)

        # 종목 기본정보 로딩 (이름/마켓만 — 플래그 무시)
        self._load_stock_names()

    def _get_conn(self, db):
        return pymysql.connect(**self._conn_params, database=db)

    # ── 종목 기본정보 로딩 (플래그 무시) ──────────────
    def _load_stock_names(self):
        """stock_base_info에서 이름/마켓 정보만 로딩"""
        conn = self._get_conn(DBConfig.STOCK_INFO_DB)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT code, name, market FROM stock_base_info"
                )
                rows = cur.fetchall()
            self.stock_info = {r['code']: r for r in rows}
            logger.info(f"종목 기본정보 로딩: {len(self.stock_info)}개")
        finally:
            conn.close()

    # ── 전체 종목 리스트 (DB 플래그 필터 없이) ──────────
    def _get_all_stocks(self):
        """전체 종목 리스트 반환 — DB 플래그 조건 없음"""
        conn = self._get_conn(DBConfig.STOCK_INFO_DB)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT code, name, market, market_cap
                    FROM stock_base_info
                """)
                return cur.fetchall()
        finally:
            conn.close()

    # ── 일봉 데이터 로딩 (단일 종목) ─────────────────
    def _load_candles(self, code: str, end_date: date) -> pd.DataFrame:
        start_date = end_date - timedelta(days=self.WARMUP_DAYS * 2)

        conn = self._get_conn(DBConfig.STOCK_DATA_DB)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT date, open, high, low, close, volume, tramount
                    FROM daily_candles
                    WHERE code = %s AND date BETWEEN %s AND %s
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

        if 'tramount' in df.columns:
            mask = df['tramount'] == 0
            df.loc[mask, 'tramount'] = df.loc[mask, 'close'] * df.loc[mask, 'volume']

        return df

    # ── 배치 로딩 (전종목 한번에) ─────────────────────
    def _load_all_candles_batch(self, end_date: date) -> dict:
        start_date = end_date - timedelta(days=self.WARMUP_DAYS * 2)

        conn = self._get_conn(DBConfig.STOCK_DATA_DB)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT code, date, open, high, low, close, volume, tramount
                    FROM daily_candles
                    WHERE date BETWEEN %s AND %s
                    ORDER BY code, date ASC
                """, (start_date, end_date))
                rows = cur.fetchall()
        finally:
            conn.close()

        if not rows:
            return {}

        df_all = pd.DataFrame(rows)
        df_all['date'] = pd.to_datetime(df_all['date'])

        mask = df_all['tramount'] == 0
        df_all.loc[mask, 'tramount'] = (
            df_all.loc[mask, 'close'] * df_all.loc[mask, 'volume']
        )

        result = {}
        for code, group in df_all.groupby('code'):
            g = group.set_index('date').drop(columns='code')
            if len(g) >= 200:
                result[code] = g

        logger.info(
            f"배치 로딩 완료: {len(result)}종목 "
            f"({start_date} ~ {end_date})"
        )
        return result

    # ═══════════════════════════════════════════════
    #  조건식 평가 (단일 종목)
    # ═══════════════════════════════════════════════
    def evaluate_conditions(self, df: pd.DataFrame,
                            target_date: date) -> dict:
        """
        단일 종목의 DataFrame에 대해 조건식 A~K + N,O를 평가

        Returns:
            {
                'pass': True/False,
                'trigger_path': 'event' or 'trend:D_MA60GC+E_MA200GC' or None,
                'conditions': {A: T/F, B: T/F, ...},
                'indicators': {ma60: val, ma200: val, rsi: val, ...},
            }
        """
        target_ts = pd.Timestamp(target_date)

        if target_ts not in df.index:
            return {'pass': False, 'trigger_path': None,
                    'conditions': {}, 'indicators': {}}

        idx = df.index.get_loc(target_ts)
        if idx < 200:
            return {'pass': False, 'trigger_path': None,
                    'conditions': {}, 'indicators': {}}

        # ── 시리즈 준비 ──
        close = df['close'].astype(float)
        volume = df['volume'].astype(float)
        open_ = df['open'].astype(float)

        # ── 이동평균 ──
        ma5 = sma(close, 5)
        ma20 = sma(close, 20)
        ma50 = sma(close, 50)
        ma60 = sma(close, 60)
        ma120 = sma(close, 120)
        ma200 = sma(close, 200)

        vol_ma20 = sma(volume, 20)
        vol_ma120 = sma(volume, 120)

        # ── 현재봉/전일봉 값 ──
        t = target_ts
        c_today = float(close.loc[t])
        v_today = float(volume.loc[t])
        o_today = float(open_.loc[t])

        t_prev = df.index[idx - 1]
        c_prev = float(close.loc[t_prev])

        # ═══════════════════════════════════════
        # 조건 A ~ K 평가
        # ═══════════════════════════════════════
        conditions = {}

        # A: 120일 거래량 상향돌파
        _vol_ma120 = vol_ma120.loc[t]
        conditions['A'] = bool(
            v_today > _vol_ma120
        ) if not pd.isna(_vol_ma120) else False

        # B: 당일 등락률 ≥ +20%
        day_return = ((c_today - c_prev) / c_prev * 100) if c_prev > 0 else 0
        conditions['B'] = bool(day_return >= 20)

        # C: RSI(14)가 오늘 70 상향돌파
        rsi_vals = rsi(close, 14)
        rsi_today = rsi_vals.iloc[idx]
        rsi_prev = rsi_vals.iloc[idx - 1]
        if not pd.isna(rsi_today) and not pd.isna(rsi_prev):
            conditions['C'] = bool(rsi_today >= 70 and rsi_prev < 70)
        else:
            conditions['C'] = False

        # D: 종가가 60일 이평 오늘 골든크로스
        _ma60_today = ma60.loc[t]
        _ma60_prev = ma60.iloc[idx - 1]
        if not pd.isna(_ma60_today) and not pd.isna(_ma60_prev):
            conditions['D'] = bool(
                c_today >= _ma60_today and c_prev < _ma60_prev
            )
        else:
            conditions['D'] = False

        # E: 종가가 200일 이평 오늘 골든크로스
        _ma200_today = ma200.loc[t]
        _ma200_prev = ma200.iloc[idx - 1]
        if not pd.isna(_ma200_today) and not pd.isna(_ma200_prev):
            conditions['E'] = bool(
                c_today >= _ma200_today and c_prev < _ma200_prev
            )
        else:
            conditions['E'] = False

        # F: 오늘 거래량 > 20일 평균
        _vol_ma20 = vol_ma20.loc[t]
        conditions['F'] = bool(
            v_today > _vol_ma20
        ) if not pd.isna(_vol_ma20) else False

        # G, H, I: 비활성
        conditions['G'] = False
        conditions['H'] = False
        conditions['I'] = False

        # J: 종가 ↔ 200일이평 ±5% 이내
        dev_200 = None
        if not pd.isna(_ma200_today) and _ma200_today > 0:
            dev_200 = abs(c_today - _ma200_today) / _ma200_today * 100
            conditions['J'] = bool(dev_200 <= 5.0)
        else:
            conditions['J'] = False

        # K: 거래대금 ≥ 30억
        trade_val = float(df['tramount'].iloc[idx])
        if trade_val == 0:
            trade_val = c_today * v_today
        conditions['K'] = bool(trade_val >= 3_000_000_000)

        # ═══════════════════════════════════════
        # N, O 하락 필터
        # ═══════════════════════════════════════
        conditions['N_exclude'] = False
        conditions['O_exclude'] = False

        if idx >= 15:
            c_15ago = float(close.iloc[idx - 15])
            _ma60_15ago = ma60.iloc[idx - 15]
            _ma200_15ago = ma200.iloc[idx - 15]
            _ma50_today = ma50.loc[t]

            # N: 15일전(종가/60이평) vs 현재(종가/50이평)
            if (not pd.isna(_ma60_15ago) and _ma60_15ago > 0 and
                    not pd.isna(_ma50_today) and _ma50_today > 0):
                ratio_15ago_60 = c_15ago / _ma60_15ago
                ratio_now_50 = c_today / _ma50_today
                conditions['N_exclude'] = bool(ratio_now_50 < ratio_15ago_60)

            # O: 15일전(종가/200이평) vs 현재(종가/50이평)
            if (not pd.isna(_ma200_15ago) and _ma200_15ago > 0 and
                    not pd.isna(_ma50_today) and _ma50_today > 0):
                ratio_15ago_200 = c_15ago / _ma200_15ago
                ratio_now_50 = c_today / _ma50_today
                conditions['O_exclude'] = bool(ratio_now_50 < ratio_15ago_200)

        # ═══════════════════════════════════════
        # 최종 판정
        # ═══════════════════════════════════════
        path_event = conditions['A'] and conditions['B']
        path_trend = conditions['C'] or conditions['D'] or conditions['E']

        trigger_ok = path_event or path_trend
        volume_ok = (conditions['F'] or conditions['G'] or
                     conditions['H'] or conditions['I'])
        proximity_ok = conditions['J']
        turnover_ok = conditions['K']
        filter_ok = (not conditions['N_exclude']) and (not conditions['O_exclude'])

        final_pass = (trigger_ok and volume_ok and
                      proximity_ok and turnover_ok and filter_ok)

        # 트리거 경로
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

        # ── 참고 지표값 ──
        bb_upper, bb_mid, bb_lower, bb_width, bb_pctb = bollinger_bands(close, 20, 2)

        _ma60_200_dist = None
        if not pd.isna(_ma60_today) and not pd.isna(_ma200_today) and _ma200_today > 0:
            _ma60_200_dist = round(
                abs(_ma60_today - _ma200_today) / _ma200_today * 100, 2
            )

        indicators = {
            'close': c_today,
            'open': o_today,
            'volume': v_today,
            'day_return': round(day_return, 2),
            'trade_value': trade_val,
            'ma5': float(ma5.loc[t]) if not pd.isna(ma5.loc[t]) else None,
            'ma20': float(ma20.loc[t]) if not pd.isna(ma20.loc[t]) else None,
            'ma50': float(ma50.loc[t]) if not pd.isna(ma50.loc[t]) else None,
            'ma60': float(_ma60_today) if not pd.isna(_ma60_today) else None,
            'ma120': float(ma120.loc[t]) if not pd.isna(ma120.loc[t]) else None,
            'ma200': float(_ma200_today) if not pd.isna(_ma200_today) else None,
            'rsi14': round(float(rsi_today), 2) if not pd.isna(rsi_today) else None,
            'vol_ma20': float(_vol_ma20) if not pd.isna(_vol_ma20) else None,
            'vol_ma120': float(_vol_ma120) if not pd.isna(_vol_ma120) else None,
            'vol_ratio_20': (
                round(v_today / _vol_ma20, 2)
                if not pd.isna(_vol_ma20) and _vol_ma20 > 0
                else None
            ),
            'dev_200': round(dev_200, 2) if dev_200 is not None else None,
            'ma60_200_dist': _ma60_200_dist,
            'bb_width': (
                round(float(bb_width.loc[t]), 2)
                if not pd.isna(bb_width.loc[t])
                else None
            ),
            'ma60_slope_up': (
                bool(ma_slope_positive(ma60, 5).loc[t])
                if not pd.isna(_ma60_today) else None
            ),
            'ma200_slope_up': (
                bool(ma_slope_positive(ma200, 20).loc[t])
                if not pd.isna(_ma200_today) else None
            ),
        }

        return {
            'pass': final_pass,
            'trigger_path': trigger_path,
            'conditions': conditions,
            'indicators': indicators,
        }

    # ═══════════════════════════════════════════════
    #  전종목 스캔 (특정 날짜)
    # ═══════════════════════════════════════════════
    def scan_date(self, target_date: date,
                  candles_cache: dict = None) -> list:
        """
        특정 날짜에 조건식을 통과하는 전 종목 스캔

        Returns:
            통과 종목 리스트
        """
        stocks = self._get_all_stocks()
        passed = []
        scanned = 0
        excluded_by_proxy = 0
        data_insufficient = 0
        errors = 0

        # 제외 필터 결과 수집 (배치 통계용)
        exclusion_results = []

        for stock in stocks:
            code = stock['code']
            name = stock['name']

            try:
                # 일봉 로딩
                if candles_cache and code in candles_cache:
                    df = candles_cache[code]
                else:
                    df = self._load_candles(code, target_date)

                if df.empty or len(df) < 200:
                    data_insufficient += 1
                    continue

                # ★ 일봉 기반 프록시 제외 필터 (DB 플래그 대신)
                exc_result = self.exclusion_filter.check(
                    df, target_date, stock_name=name
                )
                exclusion_results.append(exc_result)

                if exc_result['excluded']:
                    excluded_by_proxy += 1
                    continue

                # 조건식 평가
                result = self.evaluate_conditions(df, target_date)
                scanned += 1

                if result['pass']:
                    passed.append({
                        'code': code,
                        'name': name,
                        'market': stock['market'],
                        'trigger_path': result['trigger_path'],
                        'conditions': result['conditions'],
                        'indicators': result['indicators'],
                    })

            except Exception as e:
                errors += 1
                if errors <= 5:
                    logger.warning(f"[{code} {name}] 평가 오류: {e}")

        # 제외 통계 출력
        if exclusion_results:
            exc_summary = self.exclusion_filter.summarize_exclusions(
                exclusion_results
            )
            logger.info(
                f"[{target_date}] 프록시 필터 통계: "
                f"평가대상 {exc_summary['total']}개 중 "
                f"{exc_summary['excluded']}개 제외 "
                f"({exc_summary['exclusion_rate']}%) "
                f"사유: {exc_summary['reason_counts']}"
            )

        logger.info(
            f"[{target_date}] 스캔 완료: "
            f"전체 {len(stocks)}종목 → "
            f"데이터부족 {data_insufficient}, "
            f"프록시제외 {excluded_by_proxy}, "
            f"조건평가 {scanned}, "
            f"최종통과 {len(passed)}, "
            f"오류 {errors}"
        )
        return passed
