"""
스코어링 모델 v2.1 — 2축 스코어링 (안정성 + 폭발력) + 클러스터 보너스

v2.0 대비 변경사항:
    - 클러스터 보너스 시스템 추가 (+15 ~ -10점)
    - 6단계 cluster_label: A_SEMI_CLUSTER / B_COOL_ELEC / B2_WARM_ELEC /
                            C_OTHER_CLUSTER / D_NO_CLUSTER / X_HOT_AVOID
    - 최종 E-Score에 클러스터 보너스를 가산 (0~100 범위 클램프)
    - 등급 매트릭스는 보너스 반영된 E-Score로 판정

    검증 근거 (Q21, 3,714건):
        A_SEMI_CLUSTER (+15): avg_1m +7.4%, 승률 55.4% (83건)
        B_COOL_ELEC    (+10): avg_1m +5.6%, 승률 45.5% (66건)
        B2_WARM_ELEC    (+3): avg_1m +2.5%, 승률 43.6% (78건)
        D_NO_CLUSTER     (0): avg_1m +0.4%, 승률 39.7% (3128건)
        C_OTHER_CLUSTER (-3): avg_1m -2.3%, 승률 29.9% (328건)
        X_HOT_AVOID    (-10): avg_1m -5.3%, 승률 29.0% (31건)

Usage:
    from scoring_model import ScoringModelV2
    model = ScoringModelV2()

    # v2.0 호환 (클러스터 정보 없이)
    result = model.score({...})

    # v2.1 클러스터 보너스 포함
    result = model.score({...}, cluster_info={
        'sector': '코스닥 기계·장비',
        'sector_cnt': 4,
        'cluster_avg_day_ret': 7.2
    })
"""


class ScoringModelV2:
    """
    2-Axis Scoring Model v2.1

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

    Cluster Bonus (-10 ~ +15):
        H1. 업종 클러스터 분류     : E-Score에 가산, 0~100 클램프
    """

    VERSION = "2.1"

    CLUSTER_BONUS = {
        'A_SEMI_CLUSTER':  15,
        'B_COOL_ELEC':     10,
        'B2_WARM_ELEC':     3,
        'D_NO_CLUSTER':     0,
        'C_OTHER_CLUSTER': -3,
        'X_HOT_AVOID':    -10,
    }

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

    @staticmethod
    def classify_cluster(cluster_info: dict) -> str:
        if cluster_info is None:
            return 'D_NO_CLUSTER'
        sector = cluster_info.get('sector') or ''
        cnt = cluster_info.get('sector_cnt', 0)
        avg_dr = cluster_info.get('cluster_avg_day_ret', 0)
        if cnt < 3:
            return 'D_NO_CLUSTER'
        if sector == '코스닥 기계·장비':
            return 'A_SEMI_CLUSTER'
        if sector == '코스닥 전기·전자':
            if avg_dr < 7:
                return 'B_COOL_ELEC'
            elif avg_dr < 10:
                return 'B2_WARM_ELEC'
            else:
                return 'X_HOT_AVOID'
        return 'C_OTHER_CLUSTER'

    def score(self, data: dict, cluster_info: dict = None) -> dict:
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
        e_score_raw = sum(ef.values())

        # ══════════ Cluster Bonus (v2.1) ══════════
        cluster_label = self.classify_cluster(cluster_info)
        cluster_bonus = self.CLUSTER_BONUS[cluster_label]
        e_score = max(0, min(100, e_score_raw + cluster_bonus))

        # ══════════ 등급 결정 ══════════
        grade = self._get_grade(s_score, e_score)
        grade_v20 = self._get_grade(s_score, e_score_raw)
        strategy = self.GRADE_STRATEGY[grade]

        s_detail = ' | '.join(f"{k}={v}" for k, v in sf.items())
        e_detail = ' | '.join(f"{k}={v}" for k, v in ef.items())

        return {
            's_score': s_score,
            'e_score_raw': e_score_raw,
            'cluster_bonus': cluster_bonus,
            'cluster_label': cluster_label,
            'e_score': e_score,
            'grade': grade,
            'grade_v20': grade_v20,
            's_factors': sf,
            'e_factors': ef,
            'strategy': strategy,
            'details': (
                f"S[{s_detail}]={s_score} | "
                f"E[{e_detail}]={e_score_raw} "
                f"+cluster({cluster_label}:{cluster_bonus:+d})={e_score}"
            ),
        }

    # ═══════════════════════════════════════════
    #  S-Score 팩터 (안정성)
    # ═══════════════════════════════════════════

    def _s_trigger(self, tp: str) -> int:
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
    #  E-Score 팩터 (폭발력)
    # ═══════════════════════════════════════════

    def _e_price(self, price) -> int:
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
            return 0

    def _e_vol_ratio(self, vr) -> int:
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
        if s >= 70:
            return 'A1' if e >= 60 else 'A2'
        elif s >= 50:
            return 'B1' if e >= 60 else 'B2'
        else:
            return 'C' if e >= 60 else 'D'


def score_stock(**kwargs) -> dict:
    cluster = kwargs.pop('cluster_info', None)
    return ScoringModelV2().score(kwargs, cluster_info=cluster)


if __name__ == '__main__':
    model = ScoringModelV2()

    print("=" * 90)
    print("  스코어링 모델 v2.1 (2축: 안정성 S + 폭발력 E + 클러스터 보너스)")
    print("=" * 90)

    tests = [
        {
            'label': '기가레인 (실제 +427%) — 클러스터 없음',
            'data': {
                'trigger_path': 'trend:D_MA60GC+E_MA200GC',
                'ma60_200_dist': 1.5, 'rsi14': 52.0,
                'bb_width': 12.0, 'vol_ratio_20': 8.5,
                'ma60_slope_up': 1, 'ma200_slope_up': 1,
                'close_price': 1579, 'day_return': 12.5,
            },
            'cluster': None,
        },
        {
            'label': '반도체장비 클러스터 (A_SEMI +15)',
            'data': {
                'trigger_path': 'trend:D_MA60GC+E_MA200GC',
                'ma60_200_dist': 3.0, 'rsi14': 55.0,
                'bb_width': 18.0, 'vol_ratio_20': 6.0,
                'ma60_slope_up': 1, 'ma200_slope_up': 1,
                'close_price': 8000, 'day_return': 7.5,
            },
            'cluster': {'sector': '코스닥 기계·장비', 'sector_cnt': 4, 'cluster_avg_day_ret': 7.5},
        },
        {
            'label': '전기전자 Cool 클러스터 (B_COOL +10)',
            'data': {
                'trigger_path': 'trend:E_MA200GC',
                'ma60_200_dist': 5.0, 'rsi14': 58.0,
                'bb_width': 15.0, 'vol_ratio_20': 4.0,
                'ma60_slope_up': 1, 'ma200_slope_up': 0,
                'close_price': 5000, 'day_return': 5.5,
            },
            'cluster': {'sector': '코스닥 전기·전자', 'sector_cnt': 3, 'cluster_avg_day_ret': 5.5},
        },
        {
            'label': '전기전자 Hot 과열 (X_HOT -10)',
            'data': {
                'trigger_path': 'trend:C_RSI70',
                'ma60_200_dist': 18.0, 'rsi14': 75.0,
                'bb_width': 45.0, 'vol_ratio_20': 12.0,
                'ma60_slope_up': 0, 'ma200_slope_up': 0,
                'close_price': 8000, 'day_return': 18.0,
            },
            'cluster': {'sector': '코스닥 전기·전자', 'sector_cnt': 5, 'cluster_avg_day_ret': 15.0},
        },
        {
            'label': '기타 업종 클러스터 (C_OTHER -3)',
            'data': {
                'trigger_path': 'trend:D_MA60GC',
                'ma60_200_dist': 4.0, 'rsi14': 60.0,
                'bb_width': 20.0, 'vol_ratio_20': 3.0,
                'ma60_slope_up': 1, 'ma200_slope_up': 0,
                'close_price': 12000, 'day_return': 6.0,
            },
            'cluster': {'sector': '코스닥 유통', 'sector_cnt': 3, 'cluster_avg_day_ret': 5.0},
        },
    ]

    for i, t in enumerate(tests, 1):
        r = model.score(t['data'], cluster_info=t.get('cluster'))
        upgrade = ""
        if r['grade'] != r['grade_v20']:
            upgrade = f" (v2.0:{r['grade_v20']} -> v2.1:{r['grade']})"
        print(f"\n[{i}] {t['label']}")
        print(f"    S={r['s_score']}  E_raw={r['e_score_raw']}  "
              f"cluster={r['cluster_label']}({r['cluster_bonus']:+d})  "
              f"E_final={r['e_score']}  등급={r['grade']}{upgrade}")
        print(f"    전략: {r['strategy']['description']}")

    print(f"\n{'=' * 90}")
    print("  클러스터 보너스 배점표:")
    print("  A_SEMI_CLUSTER  +15  코스닥 기계·장비 3종목+")
    print("  B_COOL_ELEC     +10  코스닥 전기·전자 3종목+ & avg_day_ret <7%")
    print("  B2_WARM_ELEC     +3  코스닥 전기·전자 3종목+ & 7~10%")
    print("  D_NO_CLUSTER      0  클러스터 미해당 (베이스라인)")
    print("  C_OTHER_CLUSTER  -3  기타 업종 3종목+")
    print("  X_HOT_AVOID     -10  코스닥 전기·전자 3종목+ & 10%+")
    print(f"{'=' * 90}")