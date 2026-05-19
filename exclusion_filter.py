"""
시장기피종목 프록시 필터 (일봉 기반)

DB의 is_management, is_invest_warning 등 플래그를 사용하지 않고
일봉 캔들 데이터만으로 "투자부적격" 종목을 걸러냅니다.

대체 근거:
  관리종목/투자경고 → 거래량 극소, 가격 극저, 급등락 반복
  우선주           → stock_base_info.name 접미사로 식별
  거래정지         → 최근 거래량 = 0 연속
  정리매매         → 연속 하한가 패턴

Usage:
    from exclusion_filter import PriceBasedExclusionFilter

    filt = PriceBasedExclusionFilter()
    result = filt.check(df_candles, target_date, stock_name='하이트진로2우B')
    if result['excluded']:
        print(f"제외: {result['reasons']}")
"""
import pandas as pd
import numpy as np
from datetime import date
import logging

logger = logging.getLogger(__name__)


class PriceBasedExclusionFilter:
    """
    일봉 데이터로 산출하는 시장기피종목 프록시 필터

    모든 판정은 캔들 데이터 + 종목명에서만 도출합니다.
    DB 플래그(is_management 등)는 일절 사용하지 않습니다.
    """

    # ── 우선주 접미사 패턴 ──
    PREFERRED_SUFFIXES = ('우', '우B', '우C', '1우', '2우', '2우B', '3우B')

    def __init__(self, config: dict = None):
        """
        config: 필터 임계값 딕셔너리 (선택). 기본값으로 동작 가능.

        예시:
            PriceBasedExclusionFilter({'min_price': 1000})
        """
        c = config or {}

        # ── 1. 저가주 필터 ──
        self.min_price = c.get('min_price', 500)

        # ── 2. 거래 부재 필터 ──
        self.zero_vol_days_limit = c.get('zero_vol_days_limit', 5)
        self.zero_vol_lookback = c.get('zero_vol_lookback', 20)

        # ── 3. 극저유동성 필터 ──
        self.min_avg_trade_value = c.get('min_avg_trade_value', 100_000_000)
        self.liquidity_lookback = c.get('liquidity_lookback', 20)

        # ── 4. 비정상 변동성 필터 ──
        self.extreme_move_pct = c.get('extreme_move_pct', 29.0)
        self.extreme_move_max_count = c.get('extreme_move_max_count', 3)
        self.extreme_move_lookback = c.get('extreme_move_lookback', 60)

        # ── 5. 연속 하한가 필터 ──
        self.consec_limit_down_pct = c.get('consec_limit_down_pct', -29.0)
        self.consec_limit_down_max = c.get('consec_limit_down_max', 2)

        # ── 6. 장기 거래량 고사 필터 ──
        self.min_long_avg_volume = c.get('min_long_avg_volume', 1000)
        self.long_vol_lookback = c.get('long_vol_lookback', 120)

        # ── 7. 가격 연속성 필터 ──
        self.flat_price_days = c.get('flat_price_days', 5)

        logger.debug(
            f"PriceBasedExclusionFilter 초기화: "
            f"min_price={self.min_price}, "
            f"min_avg_trade_value={self.min_avg_trade_value/1e8:.1f}억"
        )

    # ─────────────────────────────────────────────
    #  우선주 판별 (종목명 기반)
    # ─────────────────────────────────────────────
    @classmethod
    def is_preferred_stock(cls, name: str) -> bool:
        """종목명 접미사로 우선주 여부 판별"""
        name = name.strip()
        for suffix in cls.PREFERRED_SUFFIXES:
            if name.endswith(suffix):
                # "삼성전자우" → True
                # "우리금융" → False (끝이 아니라 시작에 '우'가 있음)
                # 접미사 앞 글자가 한글/숫자인지 확인 (오탐 방지)
                prefix = name[:-len(suffix)]
                if prefix:  # 접미사만으로 이루어진 이름이 아닌 경우
                    return True
        if '우선주' in name:
            return True
        return False

    # ─────────────────────────────────────────────
    #  메인 판정 메서드
    # ─────────────────────────────────────────────
    def check(self, df: pd.DataFrame, target_date: date,
              stock_name: str = '') -> dict:
        """
        단일 종목의 일봉 데이터로 제외 여부 판정

        Args:
            df: 일봉 DataFrame
                - index: datetime (date)
                - columns: open, high, low, close, volume, tramount
            target_date: 판정 기준일
            stock_name: 종목명 (우선주 판별용)

        Returns:
            {
                'excluded': True/False,
                'reasons': ['사유1', '사유2', ...],
                'details': {각 필터별 측정값}
            }
        """
        target_ts = pd.Timestamp(target_date)
        reasons = []
        details = {}

        # ── target_date까지의 데이터만 사용 (미래 데이터 누출 방지) ──
        df_until = df[df.index <= target_ts].copy()

        if df_until.empty or len(df_until) < 10:
            return {
                'excluded': True,
                'reasons': ['데이터 부족(<10일)'],
                'details': {'bars': len(df_until)}
            }

        close = df_until['close'].astype(float)
        volume = df_until['volume'].astype(float)

        # 거래대금 계산
        if 'tramount' in df_until.columns:
            trade_val = df_until['tramount'].astype(float).copy()
            mask = trade_val == 0
            trade_val.loc[mask] = close.loc[mask] * volume.loc[mask]
        else:
            trade_val = close * volume

        today_close = float(close.iloc[-1])

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 0. 우선주 (종목명 기반)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if stock_name and self.is_preferred_stock(stock_name):
            reasons.append(f'우선주({stock_name})')
            details['preferred_stock'] = True

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 1. 저가주
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        details['last_close'] = today_close
        if today_close < self.min_price:
            reasons.append(
                f'저가주(종가 {today_close:,.0f}원 < {self.min_price:,}원)'
            )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 2. 거래 부재 (최근 N일 중 거래량=0 일수)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        lookback_vol = volume.iloc[-min(self.zero_vol_lookback, len(volume)):]
        zero_days = int((lookback_vol == 0).sum())
        details['zero_vol_days'] = zero_days
        if zero_days >= self.zero_vol_days_limit:
            reasons.append(
                f'거래부재(최근{self.zero_vol_lookback}일 중 '
                f'{zero_days}일 거래량=0)'
            )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 3. 극저유동성
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        lookback_tv = trade_val.iloc[-min(self.liquidity_lookback, len(trade_val)):]
        avg_tv = float(lookback_tv.mean())
        details['avg_trade_value_20d'] = avg_tv
        if avg_tv < self.min_avg_trade_value:
            reasons.append(
                f'극저유동성(20일 평균거래대금 '
                f'{avg_tv / 1e8:.2f}억 < {self.min_avg_trade_value / 1e8:.0f}억)'
            )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 4. 비정상 변동성
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if len(close) >= 2:
            pct_change = close.pct_change().dropna() * 100
            lookback_pct = pct_change.iloc[
                -min(self.extreme_move_lookback, len(pct_change)):
            ]
            extreme_count = int(
                (lookback_pct.abs() >= self.extreme_move_pct).sum()
            )
            details['extreme_move_count_60d'] = extreme_count
            if extreme_count >= self.extreme_move_max_count:
                reasons.append(
                    f'비정상변동(최근{self.extreme_move_lookback}일 내 '
                    f'±{self.extreme_move_pct}% 이상 {extreme_count}회)'
                )

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 5. 연속 하한가
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            recent_pct = pct_change.iloc[-min(20, len(pct_change)):]
            limit_downs = int(
                (recent_pct <= self.consec_limit_down_pct).sum()
            )
            details['limit_down_count_20d'] = limit_downs
            if limit_downs >= self.consec_limit_down_max:
                reasons.append(
                    f'하한가반복(최근20일 내 '
                    f'{self.consec_limit_down_pct}% 이하 {limit_downs}회)'
                )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 6. 장기 거래량 고사
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if len(volume) >= self.long_vol_lookback:
            long_avg_vol = float(
                volume.iloc[-self.long_vol_lookback:].mean()
            )
            details['avg_volume_120d'] = long_avg_vol
            if long_avg_vol < self.min_long_avg_volume:
                reasons.append(
                    f'거래량고사(120일 평균 {long_avg_vol:,.0f}주 '
                    f'< {self.min_long_avg_volume:,}주)'
                )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 7. 가격 고정 (변화 없음)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if len(close) >= self.flat_price_days:
            recent_close = close.iloc[-self.flat_price_days:]
            if recent_close.nunique() == 1:
                reasons.append(
                    f'가격고정(최근{self.flat_price_days}일 종가 변화 없음)'
                )
                details['flat_price'] = True

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 최종 판정
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        excluded = len(reasons) > 0

        if excluded:
            logger.debug(
                f"제외: {stock_name} - {', '.join(reasons)}"
            )

        return {
            'excluded': excluded,
            'reasons': reasons,
            'details': details,
        }

    # ─────────────────────────────────────────────
    #  배치 통계 (디버깅/리포트용)
    # ─────────────────────────────────────────────
    def summarize_exclusions(self, exclusion_results: list) -> dict:
        """
        여러 종목의 제외 결과를 집계합니다.

        Args:
            exclusion_results: check() 반환값의 리스트

        Returns:
            {
                'total': N,
                'excluded': N,
                'passed': N,
                'reason_counts': {'우선주': N, '저가주': N, ...}
            }
        """
        total = len(exclusion_results)
        excluded = sum(1 for r in exclusion_results if r['excluded'])
        passed = total - excluded

        reason_counts = {}
        for r in exclusion_results:
            for reason in r['reasons']:
                # 사유의 첫 단어(카테고리)만 추출
                category = reason.split('(')[0]
                reason_counts[category] = reason_counts.get(category, 0) + 1

        return {
            'total': total,
            'excluded': excluded,
            'passed': passed,
            'exclusion_rate': round(excluded / total * 100, 1) if total > 0 else 0,
            'reason_counts': dict(
                sorted(reason_counts.items(), key=lambda x: -x[1])
            ),
        }
