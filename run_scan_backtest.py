"""
Track B 메인: 일봉 데이터 기반 조건식 백테스트 실행

Usage:
    # 전체 1년 백테스트 (일반 모드)
    python run_scan_backtest.py

    # 전체 1년 백테스트 (고속 모드 — precompute 먼저 실행 필요)
    python run_scan_backtest.py --fast

    # 특정 날짜만 테스트 (교차검증용)
    python run_scan_backtest.py --date 2026-01-26

    # 날짜 범위 지정
    python run_scan_backtest.py --start 2025-06-01 --end 2025-12-31

    # 배치 모드 (전종목 한 번에 로딩 — RAM 8GB 이상 권장)
    python run_scan_backtest.py --batch
"""
import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pymysql

from config import DBConfig, BacktestConfig
from condition_scanner import ConditionScanner
from condition_scanner_fast import FastConditionScanner
from performance_calculator import PerformanceCalculator

# ── 로깅 ────────────────────────────────────────
log_dir = Path(__file__).parent / 'logs'
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(
            log_dir / f"scan_{datetime.now():%Y%m%d_%H%M%S}.log",
            encoding='utf-8'
        ),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


# ── 영업일 생성 ──────────────────────────────────
def generate_trading_dates(start: date, end: date) -> list:
    dates = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


# ── 이미 완료된 날짜 조회 ─────────────────────────
def get_completed_dates(condition_name: str) -> set:
    """scan_log에서 이미 처리 완료된 날짜 조회"""
    conn_params = dict(
        host=DBConfig.HOST, port=DBConfig.PORT,
        user=DBConfig.USER, password=DBConfig.PASSWORD,
        database=DBConfig.BACKTEST_DB,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        conn = pymysql.connect(**conn_params)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT search_date FROM scan_log WHERE condition_name = %s",
                (condition_name,)
            )
            return {row['search_date'] for row in cur.fetchall()}
    except Exception:
        # 테이블이 아직 없는 경우 등
        return set()
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── 테이블 생성 (최초 1회) ────────────────────────
def ensure_tables():
    """scan_result, scan_log 테이블이 없으면 생성"""
    conn_params = dict(
        host=DBConfig.HOST, port=DBConfig.PORT,
        user=DBConfig.USER, password=DBConfig.PASSWORD,
        database=DBConfig.BACKTEST_DB,
        charset='utf8mb4',
    )
    conn = pymysql.connect(**conn_params)
    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS `scan_result` (
                `id`                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                `condition_name`    VARCHAR(100)    NOT NULL,
                `search_date`       DATE            NOT NULL,
                `code`              CHAR(6)         NOT NULL,
                `name`              VARCHAR(50)     NOT NULL,
                `market`            VARCHAR(10)     DEFAULT NULL,
                `trigger_path`      VARCHAR(50)     DEFAULT NULL COMMENT '트리거 경로',

                -- 포착시점 지표값
                `close_price`       INT             DEFAULT 0,
                `volume`            BIGINT          DEFAULT 0,
                `day_return`        DECIMAL(8,2)    DEFAULT NULL,
                `trade_value`       BIGINT          DEFAULT 0,
                `rsi14`             DECIMAL(6,2)    DEFAULT NULL,
                `ma60`              DECIMAL(12,2)   DEFAULT NULL,
                `ma200`             DECIMAL(12,2)   DEFAULT NULL,
                `ma60_200_dist`     DECIMAL(6,2)    DEFAULT NULL COMMENT '60/200이평 이격률(%%)',
                `bb_width`          DECIMAL(8,2)    DEFAULT NULL,
                `vol_ratio_20`      DECIMAL(8,2)    DEFAULT NULL COMMENT '거래량/20일평균',
                `ma60_slope_up`     TINYINT(1)      DEFAULT NULL,
                `ma200_slope_up`    TINYINT(1)      DEFAULT NULL,

                -- 성과 (미래 수익률)
                `ret_1w`            DECIMAL(8,2)    DEFAULT NULL,
                `ret_2w`            DECIMAL(8,2)    DEFAULT NULL,
                `ret_3w`            DECIMAL(8,2)    DEFAULT NULL,
                `ret_1m`            DECIMAL(8,2)    DEFAULT NULL,
                `ret_max`           DECIMAL(8,2)    DEFAULT NULL,
                `max_high`          INT             DEFAULT NULL,
                `max_high_date`     DATE            DEFAULT NULL,

                -- 조건 상세 (디버깅)
                `cond_A` TINYINT(1) DEFAULT 0,
                `cond_B` TINYINT(1) DEFAULT 0,
                `cond_C` TINYINT(1) DEFAULT 0,
                `cond_D` TINYINT(1) DEFAULT 0,
                `cond_E` TINYINT(1) DEFAULT 0,
                `cond_F` TINYINT(1) DEFAULT 0,
                `cond_N_exclude` TINYINT(1) DEFAULT 0,
                `cond_O_exclude` TINYINT(1) DEFAULT 0,

                `created_at`        TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,

                PRIMARY KEY (`id`),
                UNIQUE KEY `uk_scan_date_code` (`condition_name`,`search_date`,`code`),
                KEY `idx_search_date` (`search_date`),
                KEY `idx_code` (`code`),
                KEY `idx_ret_max` (`ret_max` DESC),
                KEY `idx_trigger` (`trigger_path`),
                KEY `idx_ma_dist` (`ma60_200_dist`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            COMMENT='Track B: 일봉기반 조건식 스캔 결과 + 수익률';
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS `scan_log` (
                `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                `condition_name` VARCHAR(100) NOT NULL,
                `search_date` DATE NOT NULL,
                `scanned_count` INT DEFAULT 0,
                `passed_count` INT DEFAULT 0,
                `avg_ret_max` DECIMAL(8,2) DEFAULT NULL,
                `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (`id`),
                UNIQUE KEY `uk_cond_date` (`condition_name`, `search_date`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
            """)

        conn.commit()
        logger.info("scan_result / scan_log 테이블 확인 완료")
    finally:
        conn.close()


# ── 결과 DB 저장 ─────────────────────────────────
def save_scan_results(condition_name: str, search_date: date,
                      results: list):
    """스캔 + 수익률 결과를 DB에 저장"""
    conn_params = dict(
        host=DBConfig.HOST, port=DBConfig.PORT,
        user=DBConfig.USER, password=DBConfig.PASSWORD,
        database=DBConfig.BACKTEST_DB,
        charset='utf8mb4',
    )

    conn = pymysql.connect(**conn_params)
    try:
        with conn.cursor() as cur:
            for r in results:
                ind = r.get('indicators', {})
                conds = r.get('conditions', {})
                perf = r.get('performance', {})

                cur.execute("""
                INSERT INTO scan_result
                    (condition_name, search_date, code, name, market, trigger_path,
                     close_price, volume, day_return, trade_value,
                     rsi14, ma60, ma200, ma60_200_dist, bb_width,
                     vol_ratio_20, ma60_slope_up, ma200_slope_up,
                     ret_1w, ret_2w, ret_3w, ret_1m, ret_max,
                     max_high, max_high_date,
                     cond_A, cond_B, cond_C, cond_D, cond_E, cond_F,
                     cond_N_exclude, cond_O_exclude)
                VALUES (%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    trigger_path=VALUES(trigger_path),
                    close_price=VALUES(close_price),
                    volume=VALUES(volume),
                    day_return=VALUES(day_return),
                    trade_value=VALUES(trade_value),
                    rsi14=VALUES(rsi14),
                    ma60=VALUES(ma60),
                    ma200=VALUES(ma200),
                    ma60_200_dist=VALUES(ma60_200_dist),
                    bb_width=VALUES(bb_width),
                    vol_ratio_20=VALUES(vol_ratio_20),
                    ma60_slope_up=VALUES(ma60_slope_up),
                    ma200_slope_up=VALUES(ma200_slope_up),
                    ret_1w=VALUES(ret_1w), ret_2w=VALUES(ret_2w),
                    ret_3w=VALUES(ret_3w), ret_1m=VALUES(ret_1m),
                    ret_max=VALUES(ret_max),
                    max_high=VALUES(max_high),
                    max_high_date=VALUES(max_high_date)
                """, (
                    condition_name, search_date,
                    r['code'], r['name'], r['market'], r['trigger_path'],
                    ind.get('close', 0), ind.get('volume', 0),
                    ind.get('day_return'), ind.get('trade_value', 0),
                    ind.get('rsi14'), ind.get('ma60'), ind.get('ma200'),
                    ind.get('ma60_200_dist'), ind.get('bb_width'),
                    ind.get('vol_ratio_20'),
                    1 if ind.get('ma60_slope_up') else 0,
                    1 if ind.get('ma200_slope_up') else 0,
                    perf.get('ret_1w'), perf.get('ret_2w'),
                    perf.get('ret_3w'), perf.get('ret_1m'),
                    perf.get('ret_max'), perf.get('max_high'),
                    perf.get('max_high_date'),
                    1 if conds.get('A') else 0,
                    1 if conds.get('B') else 0,
                    1 if conds.get('C') else 0,
                    1 if conds.get('D') else 0,
                    1 if conds.get('E') else 0,
                    1 if conds.get('F') else 0,
                    1 if conds.get('N_exclude') else 0,
                    1 if conds.get('O_exclude') else 0,
                ))

            # 스캔 로그 저장
            avg_ret = None
            if results:
                rets = [r['performance']['ret_max'] for r in results
                        if r.get('performance', {}).get('ret_max') is not None]
                if rets:
                    avg_ret = round(sum(rets) / len(rets), 2)

            cur.execute("""
            INSERT INTO scan_log (condition_name, search_date, passed_count, avg_ret_max)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                passed_count=VALUES(passed_count), avg_ret_max=VALUES(avg_ret_max)
            """, (condition_name, search_date, len(results), avg_ret))

        conn.commit()
        logger.info(f"[{search_date}] {len(results)}건 DB 저장 완료")
    finally:
        conn.close()


def save_empty_log(condition_name: str, search_date: date):
    """포착 종목 없는 날짜도 로그에 기록 (중복 스캔 방지)"""
    conn_params = dict(
        host=DBConfig.HOST, port=DBConfig.PORT,
        user=DBConfig.USER, password=DBConfig.PASSWORD,
        database=DBConfig.BACKTEST_DB,
        charset='utf8mb4',
    )
    conn = pymysql.connect(**conn_params)
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO scan_log (condition_name, search_date, passed_count, avg_ret_max)
            VALUES (%s, %s, 0, NULL)
            ON DUPLICATE KEY UPDATE passed_count=0
            """, (condition_name, search_date))
        conn.commit()
    finally:
        conn.close()


# ── 메인 실행 ────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Track B: 일봉 기반 백테스트')
    parser.add_argument('--date', type=str, help='단일 날짜 YYYY-MM-DD')
    parser.add_argument('--start', type=str, help='시작일')
    parser.add_argument('--end', type=str, help='종료일')
    parser.add_argument('--batch', action='store_true',
                        help='전종목 배치 로딩 (RAM 8GB+)')
    parser.add_argument('--fast', action='store_true',
                        help='사전계산 캐시 기반 고속 모드 '
                             '(precompute_indicators.py 먼저 실행 필요)')
    args = parser.parse_args()

    condition_name = BacktestConfig.CONDITION_NAME

    # 테이블 생성 확인
    ensure_tables()

    # ── 날짜 결정 ──
    if args.date:
        dates = [datetime.strptime(args.date, '%Y-%m-%d').date()]
    else:
        start = datetime.strptime(
            args.start or BacktestConfig.START_DATE, '%Y-%m-%d'
        ).date()
        end = datetime.strptime(
            args.end or BacktestConfig.END_DATE, '%Y-%m-%d'
        ).date()
        dates = generate_trading_dates(start, end)

    # ── 이미 완료된 날짜 제외 ──
    completed = get_completed_dates(condition_name)
    remaining = [d for d in dates if d not in completed]

    # ── 스캐너 선택 ──
    if args.fast:
        scanner = FastConditionScanner()
        mode_label = "★ Fast 모드 (사전계산 캐시)"
    else:
        scanner = ConditionScanner()
        mode_label = "일반 모드 (일봉 직접 계산)"

    perf_calc = PerformanceCalculator()

    # ── 예상 소요 시간 ──
    per_day_sec = 5 if args.fast else 240
    est_minutes = len(remaining) * per_day_sec / 60
    est_hours = est_minutes / 60

    logger.info(
        f"\n{'='*60}\n"
        f"  백테스트 시작\n"
        f"{'='*60}\n"
        f"  모드: {mode_label}\n"
        f"  조건식: {condition_name}\n"
        f"  기간: {dates[0]} ~ {dates[-1]} ({len(dates)}일)\n"
        f"  이미 완료: {len(completed)}일\n"
        f"  남은 처리: {len(remaining)}일\n"
        f"  예상 소요: {est_minutes:.0f}분 ({est_hours:.1f}시간)\n"
        f"{'='*60}"
    )

    if not remaining:
        logger.info("모든 날짜 처리 완료!")
        return

    # ── 날짜별 스캔 루프 ──
    total_passed = 0
    total_dates = 0
    t_total_start = time.time()

    for idx, target_date in enumerate(remaining):
        logger.info(
            f"\n[{idx+1}/{len(remaining)}] {target_date} 스캔 중..."
        )
        t0 = time.time()

        try:
            # 1) 조건식 스캔
            if args.fast:
                passed_stocks = scanner.scan_date(target_date)
            elif args.batch:
                candles = scanner._load_all_candles_batch(target_date)
                passed_stocks = scanner.scan_date(
                    target_date, candles_cache=candles
                )
                del candles
            else:
                passed_stocks = scanner.scan_date(target_date)

            # 2) 통과 종목 수익률 계산
            for stock in passed_stocks:
                perf = perf_calc.calculate(
                    code=stock['code'],
                    capture_date=target_date,
                    capture_close=stock['indicators'].get('close'),
                )
                stock['performance'] = perf

            # 3) DB 저장
            if passed_stocks:
                save_scan_results(condition_name, target_date, passed_stocks)
                total_passed += len(passed_stocks)
            else:
                save_empty_log(condition_name, target_date)

            total_dates += 1
            elapsed = time.time() - t0
            total_elapsed = time.time() - t_total_start

            # 진행률 / 잔여시간 계산
            avg_per_day = total_elapsed / total_dates
            remaining_days = len(remaining) - (idx + 1)
            eta_sec = remaining_days * avg_per_day
            eta_min = eta_sec / 60

            # 요약 출력
            if passed_stocks:
                for s in passed_stocks:
                    perf = s.get('performance', {})
                    logger.info(
                        f"  ✓ {s['name']}({s['code']}) "
                        f"[{s['trigger_path']}] "
                        f"max:{perf.get('ret_max', '?')}% "
                        f"1m:{perf.get('ret_1m', '?')}%"
                    )
            else:
                logger.info(f"  (통과 종목 없음)")

            logger.info(
                f"  소요: {elapsed:.1f}초 | "
                f"진행: {idx+1}/{len(remaining)} "
                f"({(idx+1)/len(remaining)*100:.1f}%) | "
                f"누적포착: {total_passed}건 | "
                f"잔여: {eta_min:.0f}분"
            )

        except KeyboardInterrupt:
            logger.info(
                "\n\n사용자 중단 (Ctrl+C)\n"
                "다음 실행 시 이어서 진행됩니다.\n"
                f"완료: {total_dates}일, 포착: {total_passed}건"
            )
            break
        except Exception as e:
            logger.error(f"[{target_date}] 오류: {e}", exc_info=True)
            # 오류 발생해도 해당 날짜를 건너뛰고 계속 진행
            save_empty_log(condition_name, target_date)
            time.sleep(1)

    # ── 최종 리포트 ─────────────────────────────
    total_elapsed = time.time() - t_total_start

    logger.info(
        f"\n{'='*60}\n"
        f"  백테스트 완료\n"
        f"{'='*60}\n"
        f"  모드: {mode_label}\n"
        f"  스캔 일수: {total_dates}일\n"
        f"  총 포착 종목: {total_passed}건\n"
        f"  일평균 포착: {total_passed/max(total_dates,1):.1f}건\n"
        f"  총 소요 시간: {total_elapsed/60:.1f}분 "
        f"({total_elapsed/3600:.1f}시간)\n"
        f"  일평균 소요: {total_elapsed/max(total_dates,1):.1f}초\n"
        f"{'='*60}"
    )


if __name__ == '__main__':
    main()
