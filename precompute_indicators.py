"""
기술적 지표 사전 계산 + DB 저장

전 종목의 일봉 데이터에서 이동평균, RSI, 볼린저밴드 등을
일괄 계산하여 MySQL 테이블에 저장합니다.

Usage:
    # 전체 기간 계산 (최초 1회)
    python precompute_indicators.py

    # 특정 기간만 추가 계산
    python precompute_indicators.py --start 2024-05-01 --end 2025-05-16

    # 특정 종목만 재계산
    python precompute_indicators.py --code 049080

    # 기존 캐시 무시하고 강제 재계산
    python precompute_indicators.py --force

    # 지표만 계산 (제외 판정 건너뛰기)
    python precompute_indicators.py --skip-exclusion
"""
import argparse
import logging
import sys
import time
import numpy as np
import pandas as pd
from datetime import datetime, date, timedelta

import pymysql
from config import DBConfig
from indicators import sma, rsi, bollinger_bands, ma_slope_positive

# ── 로깅 ────────────────────────────────────────
from pathlib import Path
log_dir = Path(__file__).parent / 'logs'
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(
            log_dir / f"precompute_{datetime.now():%Y%m%d_%H%M%S}.log",
            encoding='utf-8'
        ),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


def get_conn(db=None):
    params = dict(
        host=DBConfig.HOST, port=DBConfig.PORT,
        user=DBConfig.USER, password=DBConfig.PASSWORD,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )
    if db:
        params['database'] = db
    return pymysql.connect(**params)


# ═══════════════════════════════════════════════════
#  1. 테이블 생성
# ═══════════════════════════════════════════════════
def create_tables():
    conn = get_conn(DBConfig.BACKTEST_DB)
    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS `precomputed_indicators` (
                `code`          CHAR(6)         NOT NULL,
                `date`          DATE            NOT NULL,
                `ma5`           DECIMAL(12,2)   DEFAULT NULL,
                `ma20`          DECIMAL(12,2)   DEFAULT NULL,
                `ma50`          DECIMAL(12,2)   DEFAULT NULL,
                `ma60`          DECIMAL(12,2)   DEFAULT NULL,
                `ma120`         DECIMAL(12,2)   DEFAULT NULL,
                `ma200`         DECIMAL(12,2)   DEFAULT NULL,
                `vol_ma20`      DECIMAL(14,2)   DEFAULT NULL,
                `vol_ma120`     DECIMAL(14,2)   DEFAULT NULL,
                `rsi14`         DECIMAL(6,2)    DEFAULT NULL,
                `bb_upper`      DECIMAL(12,2)   DEFAULT NULL,
                `bb_middle`     DECIMAL(12,2)   DEFAULT NULL,
                `bb_lower`      DECIMAL(12,2)   DEFAULT NULL,
                `bb_width`      DECIMAL(8,2)    DEFAULT NULL,
                `ma60_slope_up`     TINYINT(1)  DEFAULT NULL,
                `ma200_slope_up`    TINYINT(1)  DEFAULT NULL,
                `dev_200`           DECIMAL(8,2)    DEFAULT NULL,
                `ma60_200_dist`     DECIMAL(8,2)    DEFAULT NULL,
                `trade_value`       BIGINT          DEFAULT 0,
                `day_return`        DECIMAL(8,2)    DEFAULT NULL,
                `vol_ratio_20`      DECIMAL(8,2)    DEFAULT NULL,
                PRIMARY KEY (`code`, `date`),
                KEY `idx_date` (`date`),
                KEY `idx_code` (`code`),
                KEY `idx_date_dev200` (`date`, `dev_200`),
                KEY `idx_date_rsi` (`date`, `rsi14`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            COMMENT='사전 계산된 기술적 지표 (일봉 기반)';
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS `precomputed_exclusion` (
                `code`          CHAR(6)         NOT NULL,
                `date`          DATE            NOT NULL,
                `excluded`      TINYINT(1)      NOT NULL DEFAULT 0,
                `reasons`       VARCHAR(500)    DEFAULT NULL,
                PRIMARY KEY (`code`, `date`),
                KEY `idx_date_excluded` (`date`, `excluded`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            COMMENT='프록시 제외 판정 캐시';
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS `precompute_log` (
                `code`          CHAR(6)         NOT NULL,
                `computed_start` DATE           DEFAULT NULL COMMENT '계산 시작일',
                `computed_end`   DATE           DEFAULT NULL COMMENT '계산 종료일',
                `last_date`     DATE            DEFAULT NULL,
                `row_count`     INT             DEFAULT 0,
                `updated_at`    TIMESTAMP       DEFAULT CURRENT_TIMESTAMP
                                ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (`code`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
            """)

        conn.commit()
        logger.info("사전 계산 테이블 생성/확인 완료")
    finally:
        conn.close()


# ═══════════════════════════════════════════════════
#  2. 단일 종목 지표 계산
# ═══════════════════════════════════════════════════
def compute_one_stock(code: str, start_date: date = None,
                      end_date: date = None) -> pd.DataFrame:
    """
    단일 종목의 전체 일봉에서 기술적 지표를 계산하여 DataFrame 반환

    ※ 이동평균 계산을 위해 start_date보다 충분히 이전 데이터도 로딩하되,
       결과는 start_date ~ end_date 범위만 반환합니다.
    """
    conn = get_conn(DBConfig.STOCK_DATA_DB)
    try:
        with conn.cursor() as cur:
            # 200일 이평 계산을 위해 전체 데이터 로딩
            cur.execute("""
                SELECT date, open, high, low, close, volume, tramount
                FROM daily_candles
                WHERE code = %s
                ORDER BY date ASC
            """, (code,))
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows or len(rows) < 200:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)

    close = df['close'].astype(float)
    volume = df['volume'].astype(float)

    # 거래대금 보정
    tramount = df['tramount'].astype(float).copy()
    mask = tramount == 0
    tramount.loc[mask] = close.loc[mask] * volume.loc[mask]

    # ── 이동평균 ──
    _ma5 = sma(close, 5)
    _ma20 = sma(close, 20)
    _ma50 = sma(close, 50)
    _ma60 = sma(close, 60)
    _ma120 = sma(close, 120)
    _ma200 = sma(close, 200)
    _vol_ma20 = sma(volume, 20)
    _vol_ma120 = sma(volume, 120)

    # ── RSI ──
    _rsi14 = rsi(close, 14)

    # ── 볼린저밴드 ──
    bb_upper, bb_mid, bb_lower, bb_width, _ = bollinger_bands(close, 20, 2)

    # ── 기울기 ──
    _ma60_slope = ma_slope_positive(_ma60, 5)
    _ma200_slope = ma_slope_positive(_ma200, 20)

    # ── 이격률 ──
    _dev_200 = ((close - _ma200).abs() / _ma200.replace(0, np.nan) * 100)
    _ma60_200_dist = ((_ma60 - _ma200).abs() / _ma200.replace(0, np.nan) * 100)

    # ── 등락률 ──
    _day_return = close.pct_change() * 100

    # ── 거래량 비율 ──
    _vol_ratio = volume / _vol_ma20.replace(0, np.nan)

    # ── 결과 조립 ──
    result = pd.DataFrame({
        'code': code,
        'ma5': _ma5,
        'ma20': _ma20,
        'ma50': _ma50,
        'ma60': _ma60,
        'ma120': _ma120,
        'ma200': _ma200,
        'vol_ma20': _vol_ma20,
        'vol_ma120': _vol_ma120,
        'rsi14': _rsi14,
        'bb_upper': bb_upper,
        'bb_middle': bb_mid,
        'bb_lower': bb_lower,
        'bb_width': bb_width,
        'ma60_slope_up': _ma60_slope.astype(float),
        'ma200_slope_up': _ma200_slope.astype(float),
        'dev_200': _dev_200,
        'ma60_200_dist': _ma60_200_dist,
        'trade_value': tramount,
        'day_return': _day_return,
        'vol_ratio_20': _vol_ratio,
    }, index=df.index)

    result.index.name = 'date'

    # NaN이 많은 초반 행 제거 (200일 이평 기준)
    result = result.dropna(subset=['ma200'])

    # ★ 요청된 기간만 필터링
    if start_date:
        result = result[result.index >= pd.Timestamp(start_date)]
    if end_date:
        result = result[result.index <= pd.Timestamp(end_date)]

    return result


# ═══════════════════════════════════════════════════
#  3. 프록시 제외 판정 (캐시용)
# ═══════════════════════════════════════════════════
def compute_exclusion_one_stock(code: str, name: str,
                                 dates: list) -> list:
    from exclusion_filter import PriceBasedExclusionFilter

    conn = get_conn(DBConfig.STOCK_DATA_DB)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, open, high, low, close, volume, tramount
                FROM daily_candles
                WHERE code = %s ORDER BY date ASC
            """, (code,))
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows or len(rows) < 20:
        return [{'code': code, 'date': d, 'excluded': 1,
                 'reasons': '데이터부족'} for d in dates]

    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)

    mask = df['tramount'] == 0
    df.loc[mask, 'tramount'] = df.loc[mask, 'close'] * df.loc[mask, 'volume']

    filt = PriceBasedExclusionFilter()
    results = []

    for d in dates:
        if pd.Timestamp(d) not in df.index:
            continue
        exc = filt.check(df, d, stock_name=name)
        results.append({
            'code': code,
            'date': d,
            'excluded': 1 if exc['excluded'] else 0,
            'reasons': '|'.join(exc['reasons']) if exc['reasons'] else None,
        })

    return results


# ═══════════════════════════════════════════════════
#  4. DB 벌크 저장
# ═══════════════════════════════════════════════════
def save_indicators_bulk(df: pd.DataFrame, code: str,
                          start_date: date = None, end_date: date = None):
    if df.empty:
        return 0

    conn = get_conn(DBConfig.BACKTEST_DB)
    try:
        with conn.cursor() as cur:
            rows_data = []
            for dt, row in df.iterrows():
                rows_data.append((
                    code, dt.date(),
                    _safe(row, 'ma5'), _safe(row, 'ma20'),
                    _safe(row, 'ma50'), _safe(row, 'ma60'),
                    _safe(row, 'ma120'), _safe(row, 'ma200'),
                    _safe(row, 'vol_ma20'), _safe(row, 'vol_ma120'),
                    _safe(row, 'rsi14'),
                    _safe(row, 'bb_upper'), _safe(row, 'bb_middle'),
                    _safe(row, 'bb_lower'), _safe(row, 'bb_width'),
                    _safe_int(row, 'ma60_slope_up'),
                    _safe_int(row, 'ma200_slope_up'),
                    _safe(row, 'dev_200'), _safe(row, 'ma60_200_dist'),
                    _safe_int(row, 'trade_value'),
                    _safe(row, 'day_return'), _safe(row, 'vol_ratio_20'),
                ))

            cur.executemany("""
                INSERT INTO precomputed_indicators
                    (code, date,
                     ma5, ma20, ma50, ma60, ma120, ma200,
                     vol_ma20, vol_ma120, rsi14,
                     bb_upper, bb_middle, bb_lower, bb_width,
                     ma60_slope_up, ma200_slope_up,
                     dev_200, ma60_200_dist,
                     trade_value, day_return, vol_ratio_20)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    ma5=VALUES(ma5), ma20=VALUES(ma20),
                    ma50=VALUES(ma50), ma60=VALUES(ma60),
                    ma120=VALUES(ma120), ma200=VALUES(ma200),
                    vol_ma20=VALUES(vol_ma20), vol_ma120=VALUES(vol_ma120),
                    rsi14=VALUES(rsi14),
                    bb_upper=VALUES(bb_upper), bb_middle=VALUES(bb_middle),
                    bb_lower=VALUES(bb_lower), bb_width=VALUES(bb_width),
                    ma60_slope_up=VALUES(ma60_slope_up),
                    ma200_slope_up=VALUES(ma200_slope_up),
                    dev_200=VALUES(dev_200), ma60_200_dist=VALUES(ma60_200_dist),
                    trade_value=VALUES(trade_value),
                    day_return=VALUES(day_return),
                    vol_ratio_20=VALUES(vol_ratio_20)
            """, rows_data)

            # ── 로그 업데이트 (범위 확장 방식) ──
            first_date = df.index[0].date()
            last_date = df.index[-1].date()

            cur.execute("""
                INSERT INTO precompute_log
                    (code, computed_start, computed_end, last_date, row_count)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    computed_start = LEAST(
                        COALESCE(computed_start, VALUES(computed_start)),
                        VALUES(computed_start)
                    ),
                    computed_end = GREATEST(
                        COALESCE(computed_end, VALUES(computed_end)),
                        VALUES(computed_end)
                    ),
                    last_date = GREATEST(
                        COALESCE(last_date, VALUES(last_date)),
                        VALUES(last_date)
                    ),
                    row_count = row_count + VALUES(row_count)
            """, (code, first_date, last_date, last_date, len(rows_data)))

        conn.commit()
        return len(rows_data)
    finally:
        conn.close()


def save_exclusion_bulk(results: list):
    if not results:
        return

    conn = get_conn(DBConfig.BACKTEST_DB)
    try:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO precomputed_exclusion (code, date, excluded, reasons)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    excluded=VALUES(excluded), reasons=VALUES(reasons)
            """, [(r['code'], r['date'], r['excluded'], r['reasons'])
                  for r in results])
        conn.commit()
    finally:
        conn.close()


def _safe(row, col):
    v = row.get(col)
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return round(float(v), 2)


def _safe_int(row, col):
    v = row.get(col)
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return int(v)


# ═══════════════════════════════════════════════════
#  5. 캐시 범위 확인 (구간 갭 감지)
# ═══════════════════════════════════════════════════
def get_compute_range(code: str, req_start: date, req_end: date,
                      computed_map: dict, force: bool = False):
    """
    캐시된 범위와 요청 범위를 비교하여 실제 계산이 필요한 구간을 반환.

    Returns:
        None              → 이미 완전히 캐시됨, 스킵
        (start, end)      → 이 구간만 계산 필요
    """
    if force:
        return (req_start, req_end)

    if code not in computed_map:
        return (req_start, req_end)

    info = computed_map[code]
    cached_start = info.get('computed_start')
    cached_end = info.get('computed_end')

    if cached_start is None or cached_end is None:
        return (req_start, req_end)

    # 요청 범위가 캐시 범위에 완전히 포함 → 스킵
    if cached_start <= req_start and cached_end >= req_end:
        return None

    # ── 부분 겹침: 부족한 구간만 계산 ──

    # Case 1: 요청이 캐시보다 앞쪽으로 확장
    #   req: [====req_start----cached_start====cached_end----req_end]
    #   → 계산 필요: req_start ~ (cached_start - 1일)
    if req_start < cached_start and req_end <= cached_end:
        return (req_start, cached_start - timedelta(days=1))

    # Case 2: 요청이 캐시보다 뒤쪽으로 확장
    if req_start >= cached_start and req_end > cached_end:
        return (cached_end + timedelta(days=1), req_end)

    # Case 3: 양쪽 모두 확장 → 안전하게 전체 재계산
    #   (두 번 나눠서 하면 복잡하므로 전체 계산)
    if req_start < cached_start and req_end > cached_end:
        return (req_start, req_end)

    # 기타 (이론적으로 여기 도달 안 함)
    return (req_start, req_end)


# ═══════════════════════════════════════════════════
#  6. 메인 실행
# ═══════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description='기술적 지표 사전 계산기'
    )
    parser.add_argument('--start', type=str, help='시작일 YYYY-MM-DD')
    parser.add_argument('--end', type=str, help='종료일 YYYY-MM-DD')
    parser.add_argument('--code', type=str, help='특정 종목만 계산')
    parser.add_argument('--force', action='store_true',
                        help='기존 캐시 무시하고 강제 재계산')
    parser.add_argument('--skip-exclusion', action='store_true',
                        help='프록시 제외 판정 건너뛰기')
    args = parser.parse_args()

    create_tables()

    start_date = (datetime.strptime(args.start, '%Y-%m-%d').date()
                  if args.start else date(2024, 1, 1))
    end_date = (datetime.strptime(args.end, '%Y-%m-%d').date()
                if args.end else date(2026, 5, 16))

    # 종목 리스트
    conn = get_conn(DBConfig.STOCK_INFO_DB)
    try:
        with conn.cursor() as cur:
            if args.code:
                cur.execute(
                    "SELECT code, name FROM stock_base_info WHERE code=%s",
                    (args.code,)
                )
            else:
                cur.execute("SELECT code, name FROM stock_base_info")
            stocks = cur.fetchall()
    finally:
        conn.close()

    # 이미 계산된 범위 로딩
    computed_map = {}
    conn = get_conn(DBConfig.BACKTEST_DB)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT code, computed_start, computed_end "
                "FROM precompute_log"
            )
            for r in cur.fetchall():
                computed_map[r['code']] = r
    finally:
        conn.close()

    # ── 종목별 계산 필요 구간 판정 ──
    compute_plan = []  # [(stock, actual_start, actual_end), ...]
    skipped_full = 0

    for s in stocks:
        rng = get_compute_range(
            s['code'], start_date, end_date, computed_map, args.force
        )
        if rng is None:
            skipped_full += 1
        else:
            compute_plan.append((s, rng[0], rng[1]))

    logger.info(
        f"전체 종목: {len(stocks)}개\n"
        f"  이미 완전 캐시: {skipped_full}개 (스킵)\n"
        f"  계산 필요: {len(compute_plan)}개\n"
        f"  요청 기간: {start_date} ~ {end_date}\n"
        f"  강제 재계산: {'Yes' if args.force else 'No'}"
    )

    if not compute_plan:
        logger.info("모든 종목의 지표가 이미 계산되어 있습니다.")
        if not args.skip_exclusion:
            logger.info("\n프록시 제외 판정 계산 시작...")
            compute_all_exclusions(stocks, start_date, end_date)
        return

    # ── 계산 실행 ──
    total_rows = 0
    computed_count = 0
    data_insufficient = 0
    error_count = 0
    t_start = time.time()

    for idx, (stock, act_start, act_end) in enumerate(compute_plan):
        code = stock['code']
        name = stock['name']

        try:
            # 지표 계산 (부족 구간만)
            df_ind = compute_one_stock(code, act_start, act_end)

            if df_ind.empty:
                data_insufficient += 1
                # 데이터 부족이어도 로그 기록 (다음번에 또 시도 방지)
                conn2 = get_conn(DBConfig.BACKTEST_DB)
                try:
                    with conn2.cursor() as cur2:
                        cur2.execute("""
                            INSERT INTO precompute_log
                                (code, computed_start, computed_end,
                                 last_date, row_count)
                            VALUES (%s, %s, %s, %s, 0)
                            ON DUPLICATE KEY UPDATE
                                computed_start = LEAST(
                                    COALESCE(computed_start, %s), %s
                                ),
                                computed_end = GREATEST(
                                    COALESCE(computed_end, %s), %s
                                ),
                                last_date = GREATEST(
                                    COALESCE(last_date, %s), %s
                                )
                        """, (
                            code, act_start, act_end, act_end,
                            act_start, act_start,
                            act_end, act_end,
                            act_end, act_end,
                        ))
                    conn2.commit()
                finally:
                    conn2.close()
                continue

            # DB 저장
            saved = save_indicators_bulk(
                df_ind, code, act_start, act_end
            )
            total_rows += saved
            computed_count += 1

            # 진행률 출력 (100개마다)
            if (idx + 1) % 100 == 0:
                elapsed = time.time() - t_start
                speed = (idx + 1) / elapsed
                remain = (len(compute_plan) - idx - 1) / speed
                logger.info(
                    f"  진행: {idx+1}/{len(compute_plan)} "
                    f"({(idx+1)/len(compute_plan)*100:.1f}%) "
                    f"| 계산: {computed_count} "
                    f"데이터부족: {data_insufficient} "
                    f"오류: {error_count} "
                    f"| 누적 {total_rows:,}행 "
                    f"| 속도 {speed:.1f}종목/초 "
                    f"| 잔여 {remain/60:.0f}분"
                )

        except Exception as e:
            error_count += 1
            logger.warning(f"[{code} {name}] 오류: {e}")

    elapsed = time.time() - t_start
    logger.info(
        f"\n{'='*60}\n"
        f"  지표 사전 계산 완료\n"
        f"{'='*60}\n"
        f"  요청 기간: {start_date} ~ {end_date}\n"
        f"  처리 종목: {computed_count}개\n"
        f"  데이터부족: {data_insufficient}개\n"
        f"  오류: {error_count}개\n"
        f"  저장 행수: {total_rows:,}행\n"
        f"  소요 시간: {elapsed/60:.1f}분\n"
        f"{'='*60}"
    )

    # 프록시 제외 판정
    if not args.skip_exclusion:
        logger.info("\n프록시 제외 판정 계산 시작...")
        compute_all_exclusions(stocks, start_date, end_date)


def compute_all_exclusions(stocks, start_date, end_date):
    """전 종목 프록시 제외 판정 일괄 계산"""
    # 대상 날짜 리스트
    conn = get_conn(DBConfig.STOCK_DATA_DB)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT date FROM daily_candles
                WHERE date BETWEEN %s AND %s
                ORDER BY date
            """, (start_date, end_date))
            all_dates = [r['date'] for r in cur.fetchall()]
    finally:
        conn.close()

    # 이미 제외 판정이 있는 날짜 확인
    conn = get_conn(DBConfig.BACKTEST_DB)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT date FROM precomputed_exclusion
                WHERE date BETWEEN %s AND %s
            """, (start_date, end_date))
            computed_dates = {r['date'] for r in cur.fetchall()}
    finally:
        conn.close()

    new_dates = [d for d in all_dates if d not in computed_dates]

    if not new_dates:
        logger.info("프록시 제외 판정이 이미 완료되어 있습니다.")
        return

    logger.info(
        f"제외 판정 대상: {len(stocks)}종목 × {len(new_dates)}일 "
        f"(기존 {len(computed_dates)}일 스킵)"
    )

    t_start = time.time()
    for idx, stock in enumerate(stocks):
        try:
            results = compute_exclusion_one_stock(
                stock['code'], stock['name'], new_dates
            )
            if results:
                save_exclusion_bulk(results)

            if (idx + 1) % 100 == 0:
                elapsed = time.time() - t_start
                speed = (idx + 1) / elapsed
                remain = (len(stocks) - idx - 1) / speed
                logger.info(
                    f"  제외판정 진행: {idx+1}/{len(stocks)} "
                    f"({(idx+1)/len(stocks)*100:.1f}%) "
                    f"| 잔여 {remain/60:.0f}분"
                )
        except Exception as e:
            logger.warning(f"[{stock['code']}] 제외판정 오류: {e}")

    elapsed = time.time() - t_start
    logger.info(f"프록시 제외 판정 완료 ({elapsed/60:.1f}분)")


if __name__ == '__main__':
    main()
