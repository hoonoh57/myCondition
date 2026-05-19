"""DB 연결, 테이블 생성, 종목코드 매칭, INSERT"""
import pymysql
from config import DBConfig
import logging

logger = logging.getLogger(__name__)


class DBManager:
    def __init__(self):
        self._conn_params = dict(
            host=DBConfig.HOST,
            port=DBConfig.PORT,
            user=DBConfig.USER,
            password=DBConfig.PASSWORD,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
        )
        self._ensure_backtest_db()
        self._create_tables()
        self._load_stock_name_map()

    # ── 연결 헬퍼 ──────────────────────────────────
    def _get_conn(self, db=None):
        params = {**self._conn_params}
        if db:
            params['database'] = db
        return pymysql.connect(**params)

    # ── 백테스트 DB / 테이블 생성 ────────────────────
    def _ensure_backtest_db(self):
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{DBConfig.BACKTEST_DB}` "
                    f"DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
            conn.commit()
        finally:
            conn.close()

    def _create_tables(self):
        conn = self._get_conn(DBConfig.BACKTEST_DB)
        try:
            with conn.cursor() as cur:
                # ── 메인 결과 테이블 ──
                cur.execute("""
                CREATE TABLE IF NOT EXISTS `condition_perf_result` (
                    `id`                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    `condition_name`    VARCHAR(100)    NOT NULL COMMENT '조건식 이름',
                    `search_date`       DATE            NOT NULL COMMENT '검색시점(포착일)',
                    `code`              CHAR(6)         NOT NULL COMMENT '종목코드',
                    `name`              VARCHAR(50)     NOT NULL COMMENT '종목명',
                    `market`            VARCHAR(10)     DEFAULT NULL COMMENT 'KOSPI/KOSDAQ',

                    -- 기간 수익률 (성과검증 원본 그대로)
                    `ret_1w`            DECIMAL(8,2)    DEFAULT NULL COMMENT '1주 수익률(%)',
                    `ret_2w`            DECIMAL(8,2)    DEFAULT NULL COMMENT '2주 수익률(%)',
                    `ret_3w`            DECIMAL(8,2)    DEFAULT NULL COMMENT '3주 수익률(%)',
                    `ret_1m`            DECIMAL(8,2)    DEFAULT NULL COMMENT '1개월 수익률(%)',
                    `ret_max`           DECIMAL(8,2)    DEFAULT NULL COMMENT '기간 내 최고수익률(%)',

                    -- 검색시점 거래량 / 기타
                    `search_volume`     BIGINT UNSIGNED DEFAULT 0 COMMENT '검색시점 거래량',
                    `etc_value`         DECIMAL(8,2)    DEFAULT NULL COMMENT '기타 열 값',

                    -- 메타
                    `raw_clipboard`     TEXT            DEFAULT NULL COMMENT '원본 클립보드 행(디버깅용)',
                    `created_at`        TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,

                    PRIMARY KEY (`id`),
                    UNIQUE KEY `uk_cond_date_code` (`condition_name`, `search_date`, `code`),
                    KEY `idx_search_date` (`search_date`),
                    KEY `idx_code` (`code`),
                    KEY `idx_ret_max` (`ret_max` DESC)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
                COMMENT='키움 조건식 성과검증 결과';
                """)

                # ── 일자별 시장 수익률 ──
                cur.execute("""
                CREATE TABLE IF NOT EXISTS `condition_perf_market` (
                    `id`                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    `condition_name`    VARCHAR(100)    NOT NULL,
                    `search_date`       DATE            NOT NULL,
                    `market`            VARCHAR(10)     NOT NULL COMMENT 'KOSPI/KOSDAQ/ALL',
                    `total_count`       INT             DEFAULT 0,
                    `up_count`          INT             DEFAULT 0,
                    `down_count`        INT             DEFAULT 0,
                    `flat_count`        INT             DEFAULT 0,
                    `up_ratio`          DECIMAL(6,2)    DEFAULT NULL COMMENT '상승종목비율(%)',
                    `avg_return`        DECIMAL(8,2)    DEFAULT NULL COMMENT '검색종목 평균수익률(%)',
                    `market_return`     DECIMAL(8,2)    DEFAULT NULL COMMENT '시장수익률(%)',
                    `excess_return`     DECIMAL(8,2)    DEFAULT NULL COMMENT '시장대비 초과수익률(%)',
                    `created_at`        TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (`id`),
                    UNIQUE KEY `uk_cond_date_mkt` (`condition_name`, `search_date`, `market`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
                COMMENT='성과검증 시장수익률 요약';
                """)

                # ── 수집 로그 (어떤 날짜를 이미 처리했는지 추적) ──
                cur.execute("""
                CREATE TABLE IF NOT EXISTS `collection_log` (
                    `id`                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    `condition_name`    VARCHAR(100)    NOT NULL,
                    `search_date`       DATE            NOT NULL,
                    `stock_count`       INT             DEFAULT 0,
                    `status`            VARCHAR(20)     DEFAULT 'OK' COMMENT 'OK/EMPTY/ERROR',
                    `message`           TEXT            DEFAULT NULL,
                    `created_at`        TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (`id`),
                    UNIQUE KEY `uk_cond_date` (`condition_name`, `search_date`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
                """)

            conn.commit()
            logger.info("백테스트 테이블 생성/확인 완료")
        finally:
            conn.close()

    # ── 종목명 → 코드 매핑 로딩 ──────────────────────
    def _load_stock_name_map(self):
        """stock_base_info에서 name→code 딕셔너리 로딩"""
        conn = self._get_conn(DBConfig.STOCK_INFO_DB)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT code, name, market FROM stock_base_info"
                )
                rows = cur.fetchall()

            self.name_to_code = {}
            self.name_to_market = {}
            for r in rows:
                name = r['name'].strip()
                self.name_to_code[name] = r['code']
                self.name_to_market[name] = r['market']

            logger.info(f"종목명→코드 매핑 로딩: {len(self.name_to_code)}개")
        finally:
            conn.close()

    def resolve_code(self, stock_name: str):
        """종목명으로 코드 및 마켓 조회. 못 찾으면 (None, None)"""
        name = stock_name.strip()
        code = self.name_to_code.get(name)
        market = self.name_to_market.get(name)
        if code is None:
            # 공백 제거 후 부분 매칭 시도
            for db_name, db_code in self.name_to_code.items():
                if db_name.replace(' ', '') == name.replace(' ', ''):
                    return db_code, self.name_to_market.get(db_name)
            logger.warning(f"종목코드 매칭 실패: '{name}'")
        return code, market

    # ── 이미 수집한 날짜 확인 ────────────────────────
    def get_collected_dates(self, condition_name: str) -> set:
        conn = self._get_conn(DBConfig.BACKTEST_DB)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT search_date FROM collection_log "
                    "WHERE condition_name=%s AND status='OK'",
                    (condition_name,)
                )
                return {r['search_date'] for r in cur.fetchall()}
        finally:
            conn.close()

    # ── 결과 저장 ─────────────────────────────────
    def save_results(self, condition_name: str, search_date: str,
                     records: list, market_summary: dict = None):
        """
        records: list of dict, 각 항목:
          {name, code, market, ret_1w, ret_2w, ret_3w, ret_1m, ret_max,
           search_volume, etc_value, raw_line}
        """
        conn = self._get_conn(DBConfig.BACKTEST_DB)
        try:
            with conn.cursor() as cur:
                for rec in records:
                    cur.execute("""
                    INSERT INTO condition_perf_result
                        (condition_name, search_date, code, name, market,
                         ret_1w, ret_2w, ret_3w, ret_1m, ret_max,
                         search_volume, etc_value, raw_clipboard)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                         ret_1w=VALUES(ret_1w), ret_2w=VALUES(ret_2w),
                         ret_3w=VALUES(ret_3w), ret_1m=VALUES(ret_1m),
                         ret_max=VALUES(ret_max),
                         search_volume=VALUES(search_volume),
                         etc_value=VALUES(etc_value),
                         raw_clipboard=VALUES(raw_clipboard)
                    """, (
                        condition_name, search_date,
                        rec['code'], rec['name'], rec['market'],
                        rec.get('ret_1w'), rec.get('ret_2w'),
                        rec.get('ret_3w'), rec.get('ret_1m'),
                        rec.get('ret_max'),
                        rec.get('search_volume', 0),
                        rec.get('etc_value'),
                        rec.get('raw_line', ''),
                    ))

                # 시장 요약 저장
                if market_summary:
                    for mkt, data in market_summary.items():
                        cur.execute("""
                        INSERT INTO condition_perf_market
                            (condition_name, search_date, market,
                             total_count, up_count, down_count, flat_count,
                             up_ratio, avg_return, market_return, excess_return)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                             total_count=VALUES(total_count),
                             up_count=VALUES(up_count),
                             down_count=VALUES(down_count),
                             flat_count=VALUES(flat_count),
                             up_ratio=VALUES(up_ratio),
                             avg_return=VALUES(avg_return),
                             market_return=VALUES(market_return),
                             excess_return=VALUES(excess_return)
                        """, (
                            condition_name, search_date, mkt,
                            data.get('total', 0), data.get('up', 0),
                            data.get('down', 0), data.get('flat', 0),
                            data.get('up_ratio'), data.get('avg_return'),
                            data.get('market_return'), data.get('excess_return'),
                        ))

                # 수집 로그
                cur.execute("""
                INSERT INTO collection_log
                    (condition_name, search_date, stock_count, status)
                VALUES (%s,%s,%s,'OK')
                ON DUPLICATE KEY UPDATE
                    stock_count=VALUES(stock_count), status='OK'
                """, (condition_name, search_date, len(records)))

            conn.commit()
            logger.info(
                f"[{search_date}] {len(records)}종목 저장 완료"
            )
        finally:
            conn.close()

    def save_error_log(self, condition_name, search_date, message):
        conn = self._get_conn(DBConfig.BACKTEST_DB)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                INSERT INTO collection_log
                    (condition_name, search_date, stock_count, status, message)
                VALUES (%s,%s,0,'ERROR',%s)
                ON DUPLICATE KEY UPDATE status='ERROR', message=VALUES(message)
                """, (condition_name, search_date, message))
            conn.commit()
        finally:
            conn.close()

    def save_empty_log(self, condition_name, search_date):
        conn = self._get_conn(DBConfig.BACKTEST_DB)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                INSERT INTO collection_log
                    (condition_name, search_date, stock_count, status, message)
                VALUES (%s,%s,0,'EMPTY','검색 결과 없음')
                ON DUPLICATE KEY UPDATE status='EMPTY'
                """, (condition_name, search_date))
            conn.commit()
        finally:
            conn.close()
