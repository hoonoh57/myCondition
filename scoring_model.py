"""
스코어링 모델 v2.0 — 2축 스코어링 (안정성 + 폭발력)

백테스트 3,860건(하락장 1,959 + 상승장 1,901) 데이터 기반
두 개의 독립적 점수를 산출하여 매트릭스 등급을 부여합니다.

    S-Score (Stability, 0-100): 1개월 수익 안정성 예측
    E-Score (Explosion, 0-100): 최고 수익 폭발력 예측
    Grade: S/E 매트릭스 → A1/A2/B1/B2/C/D

Usage:
    from scoring_model import ScoringModelV2
    model = ScoringModelV2()
    result = model.score({...})
"""


class ScoringModelV2:
    """
    2-Axis Scoring Model v2.0

    S-Score (안정성, 100점):
        F1. 트리거 경로           : 25점
        F2. 60/200 이격률         : 20점
        F3. RSI(14)               : 15점
        F4. 볼린저밴드 폭         : 15점
        F5. 거래량 비율(20일 대비) : 15점
        F6. MA 기울기 조합        : 10점

    E-Score (폭발력, 100점):
        G1. 종가 수준 (가격대)    : 30점
        G2. 당일 등락률           : 25점
        G3. 거래량 비율           : 20점
        G4. 볼린저밴드 폭         : 15점
        G5. 트리거 경로           : 10점

    데이터 근거:
        - P1(2024/05~2025/05 하락/횡보장): 1,959건
        - P2(2025/05~2026/05 상승장):      1,901건
        - 폭발(50%+) 261건 vs 일반(<50%) 3,599건 특성 비교
    """

    VERSION = "2.0"

    # ── 등급 매트릭스 ──
    #       E-Score
    #       High(≥60)   Low(<60)
    # S≥70  A1(최우선)  A2(안정우선)
    # S≥50  B1(폭발후보) B2(표준)
    # S<50  C(관망)      D(패스)

    GRADE_STRATEGY = {
        'A1': {
            'position_pct': 100,
            'target_return': 30.0,
            'stop_loss': -5.0,
            'holding_style': 'swing',
            'description': '최우선: 풀 포지션 스윙 — 안정성+폭발력 모두 높음, 목표 +30%, 손절 -5%'
        },
        'A2': {
            'position_pct': 80,
            'target_return': 20.0,
            'stop_loss': -4.0,
            'holding_style': 'swing',
            'description': '안정우선: 80% 포지션 스윙 — 안정성 높으나 폭발력 보통, 목표 +20%, 손절 -4%'
        },
        'B1': {
            'position_pct': 60,
            'target_return': 25.0,
            'stop_loss': -5.0,
            'holding_style': 'short_swing',
            'description': '폭발후보: 60% 포지션 단기스윙 — 폭발력 높으나 불안정, 목표 +25%, 손절 -5%'
        },
        'B2': {
            'position_pct': 50,
            'target_return': 15.0,
            'stop_loss': -4.0,
            'holding_style': 'short_term',
            'description': '표준: 50% 포지션 단기 — 목표 +15%, 손절 -4%'
        },
        'C': {
            'position_pct': 25,
            'target_return': 10.0,
            'stop_loss': -3.0,
            'holding_style': 'monitor',
            'description': '관망: 소량 진입 모니터링 — 목표 +10%, 손절 -3%'
        },
        'D': {
            'position_pct': 0,
            'target_return': 0,
            'stop_loss': 0,
            'holding_style': 'skip',
            'description': '패스: 진입 금지'
        },
    }

    def score(self, data: dict) -> dict:
        """
        Parameters:
            data: dict with keys:
                - trigger_path:    str
                - ma60_200_dist:   float (%)
                - rsi14:           float
                - bb_width:        float
                - vol_ratio_20:    float
                - ma60_slope_up:   int/bool
                - ma200_slope_up:  int/bool
                - close_price:     float (원)
                - day_return:      float (%)

        Returns:
            dict with keys:
                - s_score:         int (0-100, 안정성)
                - e_score:         int (0-100, 폭발력)
                - grade:           str ('A1'/'A2'/'B1'/'B2'/'C'/'D')
                - s_factors:       dict (S 팩터별 점수)
                - e_factors:       dict (E 팩터별 점수)
                - strategy:        dict
                - details:         str
        """
        # ══════════ S-Score (안정성) ══════════
        sf = {}
        sf['F1_trigger'] = self._s_trigger(data.get('trigger_path', ''))
        sf['F2_ma_dist'] = self._s_ma_dist(data.get('ma60_200_dist'))
        sf['F3_rsi'] = self._s_rsi(data.get('rsi14'))
        sf['F4_bb_width'] = self._s_bb_width(data.get('bb_width'))
        sf['F5_vol_ratio'] = self._s_vol_ratio(data.get('vol_ratio_20'))
        sf['F6_slope'] = self._s_slope(
            data.get('ma60_slope_up'), data.get('ma200_slope_up')
        )
        s_score = sum(sf.values())

        # ══════════ E-Score (폭발력) ══════════
        ef = {}
        ef['G1_price'] = self._e_price(data.get('close_price'))
        ef['G2_day_ret'] = self._e_day_return(data.get('day_return'))
        ef['G3_vol_ratio'] = self._e_vol_ratio(data.get('vol_ratio_20'))
        ef['G4_bb_width'] = self._e_bb_width(data.get('bb_width'))
        ef['G5_trigger'] = self._e_trigger(data.get('trigger_path', ''))
        e_score = sum(ef.values())

        # ══════════ 등급 결정 ══════════
        grade = self._get_grade(s_score, e_score)
        strategy = self.GRADE_STRATEGY[grade]

        s_detail = ' | '.join(f"{k}={v}" for k, v in sf.items())
        e_detail = ' | '.join(f"{k}={v}" for k, v in ef.items())

        return {
            's_score': s_score,
            'e_score': e_score,
            'grade': grade,
            's_factors': sf,
            'e_factors': ef,
            'strategy': strategy,
            'details': f"S[{s_detail}]={s_score} | E[{e_detail}]={e_score}",
        }

    # ═══════════════════════════════════════════
    #  S-Score 팩터 (안정성 — 1m 수익 예측)
    # ═══════════════════════════════════════════

    def _s_trigger(self, tp: str) -> int:
        """F1: 트리거 경로 (최대 25점) — 1m 기준 최적"""
        tp = tp.lower() if tp else ''
        has_d = 'd_ma60gc' in tp
        has_e = 'e_ma200gc' in tp
        has_c = 'c_rsi70' in tp

        if has_d and has_e and not has_c:
            return 25
        if has_d and not has_e and not has_c:
            return 22
        if has_e and not has_d and not has_c:
            return 18
        if has_c and has_e and not has_d:
            return 15
        if tp == 'event':
            return 12
        if has_c and has_d:
            return 8
        if has_c:
            return 0
        return 10

    def _s_ma_dist(self, dist) -> int:
        """F2: 60/200 이격률 (최대 20점)"""
        if dist is None:
            return 10
        dist = float(dist)
        if dist < 3:
            return 20
        elif dist < 7:
            return 17
        elif dist < 15:
            return 10
        else:
            return 0

    def _s_rsi(self, rsi_val) -> int:
        """F3: RSI (최대 15점)"""
        if rsi_val is None:
            return 8
        rsi_val = float(rsi_val)
        if rsi_val < 60:
            return 15
        elif rsi_val < 70:
            return 10
        else:
            return 0

    def _s_bb_width(self, bw) -> int:
        """F4: BB 폭 (최대 15점) — 좁을수록 안정"""
        if bw is None:
            return 8
        bw = float(bw)
        if bw < 10:
            return 12
        elif bw < 20:
            return 15
        elif bw < 30:
            return 8
        elif bw < 40:
            return 3
        else:
            return 0

    def _s_vol_ratio(self, vr) -> int:
        """F5: 거래량 비율 (최대 15점)"""
        if vr is None:
            return 5
        vr = float(vr)
        if vr < 1:
            return 0
        elif vr < 2:
            return 10
        elif vr < 5:
            return 13
        elif vr < 10:
            return 15
        else:
            return 3

    def _s_slope(self, ma60_up, ma200_up) -> int:
        """F6: MA 기울기 조합 (최대 10점)"""
        if ma60_up is None or ma200_up is None:
            return 5
        if int(ma60_up) == 1 and int(ma200_up) == 1:
            return 10
        elif int(ma60_up) == 1:
            return 7
        elif int(ma200_up) == 1:
            return 3
        else:
            return 0

    # ═══════════════════════════════════════════
    #  E-Score 팩터 (폭발력 — max 수익 예측)
    # ═══════════════════════════════════════════

    def _e_price(self, price) -> int:
        """
        G1: 종가 수준 (최대 30점)

        근거:
          CP1 초저가(<3천): 50%+ 확률 11.1%, 100%+ 3.5% → 폭발력 최강
          CP2 저가(3천-1만): 50%+ 6.3%, 100%+ 1.0%
          CP3 중가(1-3만): 50%+ 7.8%, 100%+ 0.8%
          CP4 고가(3-10만): 50%+ 3.2%, 100%+ 0.0%
          CP5 초고가(10만+): 50%+ 2.4%, 100%+ 0.0%
        """
        if price is None:
            return 15
        price = float(price)
        if price < 3000:
            return 30
        elif price < 10000:
            return 20
        elif price < 30000:
            return 15
        elif price < 100000:
            return 5
        else:
            return 0

    def _e_day_return(self, dr) -> int:
        """
        G2: 당일 등락률 (최대 25점)

        근거:
          DR5 급등(15%+): 50%+ 확률 10.2% → 최고 폭발
          DR4 강양봉(7-15%): 50%+ 8.2%
          DR3 중간(3-7%): 50%+ 5.7%
          DR2 소폭(0-3%): 50%+ 5.0%
        """
        if dr is None:
            return 10
        dr = float(dr)
        if dr >= 15:
            return 25
        elif dr >= 7:
            return 20
        elif dr >= 3:
            return 12
        elif dr >= 0:
            return 5
        else:
            return 0   # 음봉

    def _e_vol_ratio(self, vr) -> int:
        """
        G3: 거래량 비율 (최대 20점)

        근거: 폭발 종목 avg 6.3~7.4x vs 일반 5.6x
        """
        if vr is None:
            return 8
        vr = float(vr)
        if vr >= 10:
            return 20
        elif vr >= 5:
            return 18
        elif vr >= 3:
            return 12
        elif vr >= 2:
            return 8
        elif vr >= 1:
            return 4
        else:
            return 0

    def _e_bb_width(self, bw) -> int:
        """
        G4: BB 폭 (최대 15점) — 넓을수록 폭발 (안정성과 반대)

        근거: 폭발 종목 avg BW 22~26 vs 일반 19.5
        """
        if bw is None:
            return 8
        bw = float(bw)
        if bw >= 40:
            return 15
        elif bw >= 30:
            return 13
        elif bw >= 20:
            return 10
        elif bw >= 10:
            return 5
        else:
            return 0

    def _e_trigger(self, tp: str) -> int:
        """
        G5: 트리거 경로 (최대 10점) — 폭발 확률 기준

        근거: event의 HIGH 비중 11.9% (vs NORMAL 6.5%)
              E 37.2% vs 35.7% — 약간 높음
        """
        tp = tp.lower() if tp else ''
        if tp == 'event':
            return 10
        if 'e_ma200gc' in tp and 'd_ma60gc' not in tp and 'c_rsi70' not in tp:
            return 8
        if 'd_ma60gc' in tp:
            return 6
        if 'c_rsi70' in tp:
            return 4
        return 5

    # ═══════════════════════════════════════════
    #  등급 매트릭스
    # ═══════════════════════════════════════════

    def _get_grade(self, s: int, e: int) -> str:
        """
        S/E 매트릭스 → 등급

                    E ≥ 60      E < 60
        S ≥ 70      A1          A2
        50 ≤ S < 70 B1          B2
        S < 50      C           D
        """
        if s >= 70:
            return 'A1' if e >= 60 else 'A2'
        elif s >= 50:
            return 'B1' if e >= 60 else 'B2'
        else:
            return 'C' if e >= 60 else 'D'


# ── 편의 함수 ──
def score_stock(**kwargs) -> dict:
    """단일 종목 간편 스코어링"""
    return ScoringModelV2().score(kwargs)


# ── 테스트 ──
if __name__ == '__main__':
    model = ScoringModelV2()

    print("=" * 80)
    print("  스코어링 모델 v2.0 (2축: 안정성 S + 폭발력 E)")
    print("=" * 80)

    tests = [
        {
            'label': '기가레인 (실제 +427.67%)',
            'data': {
                'trigger_path': 'trend:D_MA60GC+E_MA200GC',
                'ma60_200_dist': 1.5, 'rsi14': 52.0,
                'bb_width': 12.0, 'vol_ratio_20': 8.5,
                'ma60_slope_up': 1, 'ma200_slope_up': 1,
                'close_price': 1579, 'day_return': 12.5,
            }
        },
        {
            'label': '케이씨에스 (실제 +193.80%)',
            'data': {
                'trigger_path': 'trend:D_MA60GC',
                'ma60_200_dist': 4.5, 'rsi14': 58.0,
                'bb_width': 15.0, 'vol_ratio_20': 6.0,
                'ma60_slope_up': 1, 'ma200_slope_up': 0,
                'close_price': 9360, 'day_return': 8.2,
            }
        },
        {
            'label': '삼성화재 (실제 +8.01%, 안정형)',
            'data': {
                'trigger_path': 'trend:D_MA60GC',
                'ma60_200_dist': 2.0, 'rsi14': 55.0,
                'bb_width': 14.0, 'vol_ratio_20': 2.5,
                'ma60_slope_up': 1, 'ma200_slope_up': 1,
                'close_price': 350000, 'day_return': 2.5,
            }
        },
        {
            'label': '저품질 (C단독+대이격+과매수)',
            'data': {
                'trigger_path': 'trend:C_RSI70',
                'ma60_200_dist': 18.0, 'rsi14': 75.0,
                'bb_width': 45.0, 'vol_ratio_20': 12.0,
                'ma60_slope_up': 0, 'ma200_slope_up': 0,
                'close_price': 8000, 'day_return': 18.0,
            }
        },
        {
            'label': '초저가 폭발후보 (E단독+저가+급등)',
            'data': {
                'trigger_path': 'trend:E_MA200GC',
                'ma60_200_dist': 10.0, 'rsi14': 72.0,
                'bb_width': 35.0, 'vol_ratio_20': 15.0,
                'ma60_slope_up': 0, 'ma200_slope_up': 0,
                'close_price': 1500, 'day_return': 20.0,
            }
        },
    ]

    for i, t in enumerate(tests, 1):
        r = model.score(t['data'])
        print(f"\n[{i}] {t['label']}")
        print(f"    S-Score: {r['s_score']}/100  E-Score: {r['e_score']}/100  등급: {r['grade']}")
        print(f"    S: {' | '.join(f'{k}={v}' for k,v in r['s_factors'].items())}")
        print(f"    E: {' | '.join(f'{k}={v}' for k,v in r['e_factors'].items())}")
        print(f"    전략: {r['strategy']['description']}")

    print(f"\n{'=' * 80}")
    print("  등급 매트릭스:")
    print("               E ≥ 60 (폭발력↑)    E < 60 (폭발력↓)")
    print("  S ≥ 70       A1 (최우선)          A2 (안정우선)")
    print("  50 ≤ S < 70  B1 (폭발후보)        B2 (표준)")
    print("  S < 50       C  (관망)            D  (패스)")
    print(f"{'=' * 80}")
