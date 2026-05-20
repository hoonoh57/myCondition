"""
scan_result에 v2.1 스코어/등급/클러스터 정보를 일괄 UPDATE

Usage:
    python save_scores.py
"""
import pymysql
import logging
import sys
from datetime import datetime
from pathlib import Path

from config import DBConfig
from scoring_model import ScoringModelV2
from cluster_detector import ClusterDetector

log_dir = Path(__file__).parent / 'logs'
log_dir.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(
            log_dir / f"save_scores_{datetime.now():%Y%m%d_%H%M%S}.log",
            encoding='utf-8'
        ),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


def main():
    model = ScoringModelV2()

    conn = pymysql.connect(
        host=DBConfig.HOST, port=DBConfig.PORT,
        user=DBConfig.USER, password=DBConfig.PASSWORD,
        database=DBConfig.BACKTEST_DB,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )

    # 1) 클러스터 매핑
    logger.info("클러스터 매핑 생성...")
    detector = ClusterDetector(conn)
    cluster_map = detector.build_cluster_map()

    # 2) scan_result 로딩
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, search_date, code, trigger_path,
                   close_price, ma60_200_dist, rsi14, bb_width,
                   vol_ratio_20, ma60_slope_up, ma200_slope_up,
                   day_return
            FROM scan_result
            WHERE condition_name = '60_200이평돌파'
            ORDER BY id
        """)
        rows = cur.fetchall()

    logger.info(f"총 {len(rows)}건 로딩")

    # 3) 스코어링 + UPDATE
    updated = 0
    batch = []

    for row in rows:
        d = str(row['search_date'])
        ci = cluster_map.get((d, row['code']))

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

        batch.append((
            r['s_score'], r['e_score_raw'], r['cluster_bonus'],
            r['cluster_label'], r['e_score'], r['grade'], r['grade_v20'],
            row['id']
        ))

    logger.info(f"스코어링 완료, DB 저장 시작...")

    with conn.cursor() as cur:
        cur.executemany("""
            UPDATE scan_result SET
                s_score = %s,
                e_score_raw = %s,
                cluster_bonus = %s,
                cluster_label = %s,
                e_score = %s,
                grade = %s,
                grade_v20 = %s
            WHERE id = %s
        """, batch)
    conn.commit()
    updated = len(batch)

    logger.info(f"UPDATE 완료: {updated}건")

    # 4) 검증
    with conn.cursor() as cur:
        cur.execute("""
            SELECT grade, COUNT(*) AS cnt,
                   ROUND(AVG(ret_max),1) AS avg_max,
                   ROUND(AVG(ret_1m),1) AS avg_1m
            FROM scan_result
            WHERE condition_name = '60_200이평돌파'
              AND grade IS NOT NULL
            GROUP BY grade
            ORDER BY FIELD(grade,'A1','A2','B1','B2','C','D')
        """)
        results = cur.fetchall()

    logger.info(f"\n{'='*60}")
    logger.info(f"  DB 저장 검증 (등급별 통계)")
    logger.info(f"{'='*60}")
    logger.info(f"  {'등급':<5} {'건수':>5} {'avg_max':>8} {'avg_1m':>8}")
    logger.info(f"  {'-'*30}")
    for r in results:
        logger.info(f"  {r['grade']:<5} {r['cnt']:>5} {r['avg_max']:>+7.1f}% {r['avg_1m']:>+7.1f}%")

    # 5) 클러스터 레이블 검증
    with conn.cursor() as cur:
        cur.execute("""
            SELECT cluster_label, COUNT(*) AS cnt,
                   ROUND(AVG(ret_1m),1) AS avg_1m
            FROM scan_result
            WHERE condition_name = '60_200이평돌파'
              AND cluster_label IS NOT NULL
            GROUP BY cluster_label
            ORDER BY avg_1m DESC
        """)
        cl_results = cur.fetchall()

    logger.info(f"\n  {'레이블':<20} {'건수':>5} {'avg_1m':>8}")
    logger.info(f"  {'-'*35}")
    for r in cl_results:
        logger.info(f"  {r['cluster_label']:<20} {r['cnt']:>5} {r['avg_1m']:>+7.1f}%")

    conn.close()
    logger.info(f"\n완료!")


if __name__ == '__main__':
    main()