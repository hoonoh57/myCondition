"""
스코어링 모델 v2.0 역산 검증

기존 백테스트 scan_result 3,860건에 2축 스코어링을 적용하여
등급별 실제 수익률을 검증합니다.

Usage:
    python validate_scoring.py
"""
import logging
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import pymysql
from config import DBConfig
from scoring_model import ScoringModelV2

# ── 로깅 ──
log_dir = Path(__file__).parent / 'logs'
log_dir.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(
            log_dir / f"validate_v2_{datetime.now():%Y%m%d_%H%M%S}.log",
            encoding='utf-8'
        ),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


def get_conn():
    return pymysql.connect(
        host=DBConfig.HOST, port=DBConfig.PORT,
        user=DBConfig.USER, password=DBConfig.PASSWORD,
        database=DBConfig.BACKTEST_DB,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )


def safe_float(v, default=0):
    if v is None:
        return default
    return float(v)


def stats(items, label_key='grade'):
    """리스트 아이템들의 수익률 통계 반환"""
    cnt = len(items)
    if cnt == 0:
        return None

    max_vals = [safe_float(r['ret_max']) for r in items if r['ret_max'] is not None]
    w1_vals = [safe_float(r['ret_1w']) for r in items if r['ret_1w'] is not None]
    m1_vals = [safe_float(r['ret_1m']) for r in items if r['ret_1m'] is not None]

    wins = sum(1 for v in max_vals if v > 0)
    pct_50 = sum(1 for v in max_vals if v >= 50)
    pct_100 = sum(1 for v in max_vals if v >= 100)

    return {
        'cnt': cnt,
        'win_rate': wins / cnt * 100 if cnt else 0,
        'avg_max': sum(max_vals) / len(max_vals) if max_vals else 0,
        'avg_1w': sum(w1_vals) / len(w1_vals) if w1_vals else 0,
        'avg_1m': sum(m1_vals) / len(m1_vals) if m1_vals else 0,
        'best_max': max(max_vals) if max_vals else 0,
        'worst_1m': min(m1_vals) if m1_vals else 0,
        'pct_50plus': pct_50 / cnt * 100 if cnt else 0,
        'pct_100plus': pct_100 / cnt * 100 if cnt else 0,
        'avg_s': sum(r['s_score'] for r in items) / cnt,
        'avg_e': sum(r['e_score'] for r in items) / cnt,
    }


def main():
    model = ScoringModelV2()
    conn = get_conn()

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    id, search_date, code, name, trigger_path,
                    close_price, ma200, ma60_200_dist,
                    rsi14, bb_width, vol_ratio_20,
                    ma60_slope_up, ma200_slope_up,
                    day_return,
                    ret_1w, ret_2w, ret_3w, ret_1m, ret_max,
                    max_high_date
                FROM scan_result
                WHERE condition_name = '60_200이평돌파'
                ORDER BY search_date, code
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    logger.info(f"총 {len(rows)}건 로딩 완료\n")

    # ── 스코어링 적용 ──
    for row in rows:
        r = model.score({
            'trigger_path': row['trigger_path'],
            'ma60_200_dist': row['ma60_200_dist'],
            'rsi14': row['rsi14'],
            'bb_width': row['bb_width'],
            'vol_ratio_20': row['vol_ratio_20'],
            'ma60_slope_up': row['ma60_slope_up'],
            'ma200_slope_up': row['ma200_slope_up'],
            'close_price': row['close_price'],
            'day_return': row['day_return'],
        })
        row['s_score'] = r['s_score']
        row['e_score'] = r['e_score']
        row['grade'] = r['grade']

    # ── 기간 구분 ──
    all_data = rows
    p1 = [r for r in rows if str(r['search_date']) < '2025-05-01']
    p2 = [r for r in rows if str(r['search_date']) >= '2025-05-01']

    # ── 등급별 통계 출력 ──
    grade_order = ['A1', 'A2', 'B1', 'B2', 'C', 'D']

    def print_stats(data, label):
        logger.info(f"{'='*90}")
        logger.info(f"  {label} ({len(data)}건)")
        logger.info(f"{'='*90}")
        header = (
            f"  {'등급':<5} {'건수':>5} {'비율':>6} "
            f"{'승률':>6} {'avg_max':>8} {'avg_1w':>7} {'avg_1m':>7} "
            f"{'50%+':>5} {'100%+':>5} "
            f"{'avg_S':>5} {'avg_E':>5} "
            f"{'best':>8} {'worst1m':>8}"
        )
        logger.info(header)
        logger.info(f"  {'-'*86}")

        grades = defaultdict(list)
        for r in data:
            grades[r['grade']].append(r)

        for g in grade_order:
            items = grades.get(g, [])
            if not items:
                logger.info(f"  {g:<5} {'0':>5}")
                continue

            s = stats(items)
            pct = len(items) / len(data) * 100
            logger.info(
                f"  {g:<5} {s['cnt']:>5} {pct:>5.1f}% "
                f"{s['win_rate']:>5.1f}% {s['avg_max']:>+7.1f}% "
                f"{s['avg_1w']:>+6.1f}% {s['avg_1m']:>+6.1f}% "
                f"{s['pct_50plus']:>4.1f}% {s['pct_100plus']:>4.1f}% "
                f"{s['avg_s']:>5.0f} {s['avg_e']:>5.0f} "
                f"{s['best_max']:>+7.1f}% {s['worst_1m']:>+7.1f}%"
            )

    print_stats(all_data, "전체 기간")
    print_stats(p1, "P1 하락/횡보장 (2024/05~2025/05)")
    print_stats(p2, "P2 상승장 (2025/05~2026/05)")

    # ── 핵심 검증 지표 ──
    logger.info(f"\n{'='*90}")
    logger.info("  핵심 검증 지표")
    logger.info(f"{'='*90}")

    grades = defaultdict(list)
    for r in all_data:
        grades[r['grade']].append(r)

    # A1 vs D의 avg_max 차이
    a1_s = stats(grades.get('A1', []))
    d_s = stats(grades.get('D', []))
    if a1_s and d_s:
        logger.info(
            f"\n  avg_max 변별력: A1({a1_s['avg_max']:+.1f}%) vs "
            f"D({d_s['avg_max']:+.1f}%) → "
            f"차이 {a1_s['avg_max'] - d_s['avg_max']:+.1f}%p"
        )

    # A1 vs D의 avg_1m 차이
    if a1_s and d_s:
        logger.info(
            f"  avg_1m 변별력:  A1({a1_s['avg_1m']:+.1f}%) vs "
            f"D({d_s['avg_1m']:+.1f}%) → "
            f"차이 {a1_s['avg_1m'] - d_s['avg_1m']:+.1f}%p"
        )

    # 50%+ 비율 변별력
    if a1_s and d_s:
        logger.info(
            f"  50%+ 비율:      A1({a1_s['pct_50plus']:.1f}%) vs "
            f"D({d_s['pct_50plus']:.1f}%) → "
            f"차이 {a1_s['pct_50plus'] - d_s['pct_50plus']:+.1f}%p"
        )

    # ── A1 등급 Top 20 ──
    logger.info(f"\n{'='*90}")
    logger.info("  A1 등급 Top 20 (ret_max 기준)")
    logger.info(f"{'='*90}")

    a1_top = sorted(
        [r for r in all_data if r['grade'] == 'A1'
         and r['ret_max'] is not None],
        key=lambda x: float(x['ret_max']),
        reverse=True
    )[:20]

    for i, r in enumerate(a1_top, 1):
        logger.info(
            f"  {i:>2}. [{r['search_date']}] "
            f"{r['name']}({r['code']}) "
            f"S={r['s_score']} E={r['e_score']} "
            f"max={safe_float(r['ret_max']):>+7.1f}% "
            f"1m={safe_float(r['ret_1m']):>+7.1f}% "
            f"{r['trigger_path']}"
        )

    # ── D등급 트리거 분포 ──
    logger.info(f"\n{'='*90}")
    logger.info("  D등급 트리거 분포")
    logger.info(f"{'='*90}")

    d_items = grades.get('D', [])
    if d_items:
        d_triggers = defaultdict(int)
        for r in d_items:
            d_triggers[r['trigger_path']] += 1
        for tp, cnt in sorted(d_triggers.items(), key=lambda x: x[1], reverse=True):
            logger.info(f"    {tp}: {cnt}건")


if __name__ == '__main__':
    main()
