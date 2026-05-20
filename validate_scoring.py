"""
스코어링 모델 v2.1 역산 검증

scan_result 3,860건에 2축 스코어링 + 클러스터 보너스를 적용하여
등급별 실제 수익률을 검증합니다.

v2.0 대비 변경사항:
    - ClusterDetector를 통해 클러스터 정보를 자동 주입
    - v2.0 등급 vs v2.1 등급 비교 (등급 변동 리포트)
    - cluster_label별 성과 통계 추가

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
from cluster_detector import ClusterDetector

# ── 로깅 ──
log_dir = Path(__file__).parent / 'logs'
log_dir.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(
            log_dir / f"validate_v21_{datetime.now():%Y%m%d_%H%M%S}.log",
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


def stats(items):
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

    # ── 클러스터 매핑 생성 ──
    logger.info("클러스터 매핑 생성 중...")
    detector = ClusterDetector(conn)
    cluster_map = detector.build_cluster_map()

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

    # ── 스코어링 적용 (클러스터 포함) ──
    grade_changes = defaultdict(int)  # (v20_grade, v21_grade) → count

    for row in rows:
        d = str(row['search_date'])
        code = row['code']
        ci = cluster_map.get((d, code))

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
        }, cluster_info=ci)

        row['s_score'] = r['s_score']
        row['e_score_raw'] = r['e_score_raw']
        row['e_score'] = r['e_score']
        row['cluster_bonus'] = r['cluster_bonus']
        row['cluster_label'] = r['cluster_label']
        row['grade'] = r['grade']
        row['grade_v20'] = r['grade_v20']

        if r['grade'] != r['grade_v20']:
            grade_changes[(r['grade_v20'], r['grade'])] += 1

    # ── 등급별 통계 출력 ──
    all_data = rows
    grade_order = ['A1', 'A2', 'B1', 'B2', 'C', 'D']

    def print_stats(data, label):
        logger.info(f"{'='*95}")
        logger.info(f"  {label} ({len(data)}건)")
        logger.info(f"{'='*95}")
        header = (
            f"  {'등급':<5} {'건수':>5} {'비율':>6} "
            f"{'승률':>6} {'avg_max':>8} {'avg_1w':>7} {'avg_1m':>7} "
            f"{'50%+':>5} {'100%+':>5} "
            f"{'avg_S':>5} {'avg_E':>5} "
            f"{'best':>8} {'worst1m':>8}"
        )
        logger.info(header)
        logger.info(f"  {'-'*91}")

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

    print_stats(all_data, "v2.1 전체 기간 (클러스터 보너스 반영)")

    # ── v2.0 등급으로도 통계 출력 (비교용) ──
    logger.info("")
    logger.info(f"{'='*95}")
    logger.info(f"  v2.0 등급 기준 (클러스터 보너스 미반영) — 비교용")
    logger.info(f"{'='*95}")
    header = (
        f"  {'등급':<5} {'건수':>5} {'비율':>6} "
        f"{'avg_max':>8} {'avg_1m':>7} {'50%+':>5} {'100%+':>5}"
    )
    logger.info(header)
    logger.info(f"  {'-'*50}")

    v20_grades = defaultdict(list)
    for r in all_data:
        v20_grades[r['grade_v20']].append(r)

    for g in grade_order:
        items = v20_grades.get(g, [])
        if not items:
            continue
        s = stats(items)
        pct = len(items) / len(all_data) * 100
        logger.info(
            f"  {g:<5} {s['cnt']:>5} {pct:>5.1f}% "
            f"{s['avg_max']:>+7.1f}% {s['avg_1m']:>+6.1f}% "
            f"{s['pct_50plus']:>4.1f}% {s['pct_100plus']:>4.1f}%"
        )

    # ── 등급 변동 리포트 ──
    logger.info(f"\n{'='*95}")
    logger.info(f"  v2.0 → v2.1 등급 변동 리포트")
    logger.info(f"{'='*95}")

    total_changed = sum(grade_changes.values())
    logger.info(f"  등급 변동 종목: {total_changed}건 / {len(all_data)}건 "
                f"({total_changed/len(all_data)*100:.1f}%)")
    logger.info(f"  {'v2.0→v2.1':<15} {'건수':>5}")
    logger.info(f"  {'-'*25}")

    for (old, new), cnt in sorted(grade_changes.items(),
                                   key=lambda x: x[1], reverse=True):
        direction = "↑" if grade_order.index(new) < grade_order.index(old) else "↓"
        logger.info(f"  {old}→{new} {direction:<3}    {cnt:>5}건")

    # ── 클러스터 레이블별 성과 ──
    logger.info(f"\n{'='*95}")
    logger.info(f"  클러스터 레이블별 실제 성과 (v2.1 핵심 검증)")
    logger.info(f"{'='*95}")

    label_order = ['A_SEMI_CLUSTER', 'B_COOL_ELEC', 'B2_WARM_ELEC',
                    'D_NO_CLUSTER', 'C_OTHER_CLUSTER', 'X_HOT_AVOID']

    header = (
        f"  {'레이블':<20} {'보너스':>5} {'건수':>5} "
        f"{'avg_max':>8} {'avg_1m':>7} {'승률':>6} {'50%+':>5}"
    )
    logger.info(header)
    logger.info(f"  {'-'*65}")

    cluster_groups = defaultdict(list)
    for r in all_data:
        cluster_groups[r['cluster_label']].append(r)

    for label in label_order:
        items = cluster_groups.get(label, [])
        if not items:
            continue
        bonus = ScoringModelV2.CLUSTER_BONUS[label]
        s = stats(items)
        logger.info(
            f"  {label:<20} {bonus:>+4}점 {s['cnt']:>5} "
            f"{s['avg_max']:>+7.1f}% {s['avg_1m']:>+6.1f}% "
            f"{s['win_rate']:>5.1f}% {s['pct_50plus']:>4.1f}%"
        )

    # ── 핵심 검증 지표 ──
    logger.info(f"\n{'='*95}")
    logger.info("  핵심 검증 지표 (v2.1)")
    logger.info(f"{'='*95}")

    grades = defaultdict(list)
    for r in all_data:
        grades[r['grade']].append(r)

    a1_s = stats(grades.get('A1', []))
    d_s = stats(grades.get('D', []))
    if a1_s and d_s:
        logger.info(
            f"\n  avg_max 변별력: A1({a1_s['avg_max']:+.1f}%) vs "
            f"D({d_s['avg_max']:+.1f}%) → "
            f"차이 {a1_s['avg_max'] - d_s['avg_max']:+.1f}%p"
        )
        logger.info(
            f"  avg_1m 변별력:  A1({a1_s['avg_1m']:+.1f}%) vs "
            f"D({d_s['avg_1m']:+.1f}%) → "
            f"차이 {a1_s['avg_1m'] - d_s['avg_1m']:+.1f}%p"
        )
        logger.info(
            f"  50%+ 비율:      A1({a1_s['pct_50plus']:.1f}%) vs "
            f"D({d_s['pct_50plus']:.1f}%) → "
            f"차이 {a1_s['pct_50plus'] - d_s['pct_50plus']:+.1f}%p"
        )

    # v2.0 대비 A1 변동
    a1_v20 = stats(v20_grades.get('A1', []))
    if a1_s and a1_v20:
        logger.info(
            f"\n  v2.0 A1({a1_v20['cnt']}건, avg_max {a1_v20['avg_max']:+.1f}%) → "
            f"v2.1 A1({a1_s['cnt']}건, avg_max {a1_s['avg_max']:+.1f}%)"
        )

    # ── A1 등급 Top 20 ──
    logger.info(f"\n{'='*95}")
    logger.info("  v2.1 A1 등급 Top 20 (ret_max 기준)")
    logger.info(f"{'='*95}")

    a1_top = sorted(
        [r for r in all_data if r['grade'] == 'A1'
         and r['ret_max'] is not None],
        key=lambda x: float(x['ret_max']),
        reverse=True
    )[:20]

    for i, r in enumerate(a1_top, 1):
        bonus_str = ""
        if r['cluster_bonus'] != 0:
            bonus_str = f" [{r['cluster_label']}:{r['cluster_bonus']:+d}]"
        logger.info(
            f"  {i:>2}. [{r['search_date']}] "
            f"{r['name']}({r['code']}) "
            f"S={r['s_score']} E={r['e_score']}(raw:{r['e_score_raw']}) "
            f"max={safe_float(r['ret_max']):>+7.1f}% "
            f"1m={safe_float(r['ret_1m']):>+7.1f}% "
            f"{r['trigger_path']}{bonus_str}"
        )

    # ── 클러스터 보너스로 승격된 종목 중 Top 10 ──
    logger.info(f"\n{'='*95}")
    logger.info("  클러스터 보너스로 등급 승격된 종목 Top 10")
    logger.info(f"{'='*95}")

    upgraded = [r for r in all_data
                if r['grade'] != r['grade_v20']
                and grade_order.index(r['grade']) < grade_order.index(r['grade_v20'])
                and r['ret_max'] is not None]
    upgraded.sort(key=lambda x: float(x['ret_max']), reverse=True)

    for i, r in enumerate(upgraded[:10], 1):
        logger.info(
            f"  {i:>2}. [{r['search_date']}] "
            f"{r['name']}({r['code']}) "
            f"{r['grade_v20']}→{r['grade']} "
            f"[{r['cluster_label']}:{r['cluster_bonus']:+d}] "
            f"max={safe_float(r['ret_max']):>+7.1f}% "
            f"1m={safe_float(r['ret_1m']):>+7.1f}%"
        )


if __name__ == '__main__':
    main()