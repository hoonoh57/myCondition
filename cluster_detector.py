"""
클러스터 탐지기 — scan_result + stock_base_info에서 클러스터 정보 계산

validate_scoring.py 및 실시간 스캐너에서 사용합니다.

Usage:
    from cluster_detector import ClusterDetector
    detector = ClusterDetector(conn)
    clusters = detector.build_cluster_map(condition_name='60_200이평돌파')
    # clusters = { ('2026-04-14', '053080'): {sector, sector_cnt, cluster_avg_day_ret}, ... }
"""
import pymysql
import logging
from collections import defaultdict
from config import DBConfig

logger = logging.getLogger(__name__)


class ClusterDetector:
    """
    DB에서 날짜별-업종별 클러스터 정보를 계산

    build_cluster_map()은 scan_result 전체를 읽어
    (search_date, code) → cluster_info dict 매핑을 반환합니다.
    """

    def __init__(self, conn=None):
        self._conn = conn
        self._own_conn = False

    def _get_conn(self):
        if self._conn and self._conn.open:
            return self._conn
        self._conn = pymysql.connect(
            host=DBConfig.HOST, port=DBConfig.PORT,
            user=DBConfig.USER, password=DBConfig.PASSWORD,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
        )
        self._own_conn = True
        return self._conn

    def close(self):
        if self._own_conn and self._conn:
            self._conn.close()

    def build_cluster_map(self, condition_name: str = '60_200이평돌파') -> dict:
        """
        scan_result + stock_base_info JOIN으로 클러스터 매핑 생성

        Returns:
            dict[(search_date_str, code)] = {
                'sector': str,
                'sector_cnt': int,
                'cluster_avg_day_ret': float
            }
        """
        conn = self._get_conn()
        backtest_db = DBConfig.BACKTEST_DB
        stock_db = getattr(DBConfig, 'STOCK_DATA_DB', 'stock_info')

        # stock_base_info가 어느 DB에 있는지 확인
        # config.py에서 STOCK_DATA_DB = stock_info
        stock_info_db = stock_db

        logger.info("클러스터 매핑 생성 시작...")

        with conn.cursor() as cur:
            # Step 1: scan_result + sector 조인
            cur.execute(f"""
                SELECT sr.search_date, sr.code, sr.day_return, sbi.sector
                FROM {backtest_db}.scan_result sr
                JOIN {stock_info_db}.stock_base_info sbi ON sr.code = sbi.code
                WHERE sr.condition_name = %s
                  AND sbi.sector IS NOT NULL
                  AND sbi.sector != ''
                ORDER BY sr.search_date
            """, (condition_name,))
            rows = cur.fetchall()

        logger.info(f"  조인 결과: {len(rows)}건")

        # Step 2: (date, sector) 별 집계
        # key = (date, sector) → list of (code, day_return)
        groups = defaultdict(list)
        code_sector = {}

        for r in rows:
            d = str(r['search_date'])
            sector = r['sector']
            code = r['code']
            dr = float(r['day_return']) if r['day_return'] is not None else 0

            groups[(d, sector)].append((code, dr))
            code_sector[(d, code)] = sector

        # Step 3: 클러스터 매핑 생성
        cluster_map = {}

        for (d, sector), members in groups.items():
            cnt = len(members)
            avg_dr = sum(dr for _, dr in members) / cnt if cnt > 0 else 0

            for code, _ in members:
                cluster_map[(d, code)] = {
                    'sector': sector,
                    'sector_cnt': cnt,
                    'cluster_avg_day_ret': round(avg_dr, 2),
                }

        logger.info(
            f"  클러스터 매핑 완료: {len(cluster_map)}건, "
            f"3종목+ 클러스터: "
            f"{sum(1 for v in cluster_map.values() if v['sector_cnt'] >= 3)}건"
        )

        return cluster_map

    def get_cluster_info(self, cluster_map: dict,
                         search_date, code: str) -> dict:
        """
        cluster_map에서 특정 종목의 클러스터 정보 조회

        Returns:
            cluster_info dict 또는 None
        """
        d = str(search_date)
        return cluster_map.get((d, code))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s [%(levelname)s] %(message)s')

    from scoring_model import ScoringModelV2

    detector = ClusterDetector()
    try:
        cmap = detector.build_cluster_map()

        # 클러스터 레이블별 통계
        from collections import Counter
        label_counts = Counter()
        for info in cmap.values():
            label = ScoringModelV2.classify_cluster(info)
            label_counts[label] += 1

        print("\n" + "=" * 60)
        print("  클러스터 레이블 분포")
        print("=" * 60)
        for label in ['A_SEMI_CLUSTER', 'B_COOL_ELEC', 'B2_WARM_ELEC',
                       'D_NO_CLUSTER', 'C_OTHER_CLUSTER', 'X_HOT_AVOID']:
            bonus = ScoringModelV2.CLUSTER_BONUS[label]
            cnt = label_counts.get(label, 0)
            print(f"  {label:<20} {bonus:>+3}점  {cnt:>5}건")
        print(f"  {'합계':<20}       {sum(label_counts.values()):>5}건")
        print("=" * 60)
    finally:
        detector.close()