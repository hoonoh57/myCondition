"""
등급 이상현상 분석 스크립트

목적:
    ScoringModelV2 적용 후 C등급의 avg_max가 B2보다 높은 문제를 정밀 분해합니다.
    특히 S는 낮지만 E가 높은 C등급 종목군을 trigger/path, 가격대, 당일등락률,
    거래량비율, BB폭 기준으로 나눠 C_HOT 또는 B1 승격 조건 후보를 찾습니다.

Usage:
    python analyze_grade_anomaly.py
    python analyze_grade_anomaly.py --condition-name "60_200이평돌파"
    python analyze_grade_anomaly.py --start 2024-05-20 --end 2026-05-15
    python analyze_grade_anomaly.py --top 50
"""
import argparse
import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pymysql

from config import BacktestConfig, DBConfig


LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"grade_anomaly_{datetime.now():%Y%m%d_%H%M%S}.log",
            encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


GRADE_ORDER = ["A1", "A2", "B1", "B2", "C", "D"]


def get_conn():
    return pymysql.connect(
        host=DBConfig.HOST,
        port=DBConfig.PORT,
        user=DBConfig.USER,
        password=DBConfig.PASSWORD,
        database=DBConfig.BACKTEST_DB,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def safe_float(value, default=0.0):
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0):
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def build_where(condition_name, start_date, end_date):
    clauses = ["score_version = '2.0'", "grade IS NOT NULL"]
    params = []

    if condition_name:
        clauses.append("condition_name = %s")
        params.append(condition_name)
    if start_date:
        clauses.append("search_date >= %s")
        params.append(start_date)
    if end_date:
        clauses.append("search_date <= %s")
        params.append(end_date)

    return "WHERE " + " AND ".join(clauses), params


def fetch_rows(conn, condition_name, start_date, end_date):
    where_sql, params = build_where(condition_name, start_date, end_date)
    sql = f"""
        SELECT
            id,
            condition_name,
            search_date,
            code,
            name,
            market,
            trigger_path,
            close_price,
            volume,
            day_return,
            trade_value,
            rsi14,
            ma60_200_dist,
            bb_width,
            vol_ratio_20,
            ma60_slope_up,
            ma200_slope_up,
            ret_1w,
            ret_2w,
            ret_3w,
            ret_1m,
            ret_max,
            max_high_date,
            s_score,
            e_score,
            grade,
            grade_strategy
        FROM scan_result
        {where_sql}
        ORDER BY search_date, code, id
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def calc_stats(rows):
    cnt = len(rows)
    if cnt == 0:
        return {
            "cnt": 0,
            "avg_max": 0.0,
            "avg_1w": 0.0,
            "avg_1m": 0.0,
            "win_rate": 0.0,
            "pct_10plus": 0.0,
            "pct_20plus": 0.0,
            "pct_50plus": 0.0,
            "pct_100plus": 0.0,
            "avg_s": 0.0,
            "avg_e": 0.0,
            "best_max": 0.0,
            "worst_1m": 0.0,
        }

    max_vals = [safe_float(r.get("ret_max")) for r in rows if r.get("ret_max") is not None]
    w1_vals = [safe_float(r.get("ret_1w")) for r in rows if r.get("ret_1w") is not None]
    m1_vals = [safe_float(r.get("ret_1m")) for r in rows if r.get("ret_1m") is not None]
    s_vals = [safe_float(r.get("s_score")) for r in rows if r.get("s_score") is not None]
    e_vals = [safe_float(r.get("e_score")) for r in rows if r.get("e_score") is not None]

    wins = sum(1 for v in max_vals if v > 0)
    pct_10 = sum(1 for v in max_vals if v >= 10)
    pct_20 = sum(1 for v in max_vals if v >= 20)
    pct_50 = sum(1 for v in max_vals if v >= 50)
    pct_100 = sum(1 for v in max_vals if v >= 100)

    return {
        "cnt": cnt,
        "avg_max": sum(max_vals) / len(max_vals) if max_vals else 0.0,
        "avg_1w": sum(w1_vals) / len(w1_vals) if w1_vals else 0.0,
        "avg_1m": sum(m1_vals) / len(m1_vals) if m1_vals else 0.0,
        "win_rate": wins / cnt * 100.0 if cnt else 0.0,
        "pct_10plus": pct_10 / cnt * 100.0 if cnt else 0.0,
        "pct_20plus": pct_20 / cnt * 100.0 if cnt else 0.0,
        "pct_50plus": pct_50 / cnt * 100.0 if cnt else 0.0,
        "pct_100plus": pct_100 / cnt * 100.0 if cnt else 0.0,
        "avg_s": sum(s_vals) / len(s_vals) if s_vals else 0.0,
        "avg_e": sum(e_vals) / len(e_vals) if e_vals else 0.0,
        "best_max": max(max_vals) if max_vals else 0.0,
        "worst_1m": min(m1_vals) if m1_vals else 0.0,
    }


def print_stats_line(label, rows):
    s = calc_stats(rows)
    logger.info(
        "%24s | %5d | avg_max=%+7.2f%% | avg_1w=%+7.2f%% | avg_1m=%+7.2f%% | 20%%+=%5.1f%% | 50%%+=%5.1f%% | 100%%+=%5.1f%% | S=%5.1f | E=%5.1f | best=%+7.2f%%",
        label,
        s["cnt"],
        s["avg_max"],
        s["avg_1w"],
        s["avg_1m"],
        s["pct_20plus"],
        s["pct_50plus"],
        s["pct_100plus"],
        s["avg_s"],
        s["avg_e"],
        s["best_max"],
    )


def group_by(rows, key_func):
    groups = defaultdict(list)
    for row in rows:
        key = key_func(row)
        groups[key].append(row)
    return groups


def norm_trigger(trigger_path):
    value = (trigger_path or "").lower()
    has_c = "c_rsi70" in value
    has_d = "d_ma60gc" in value
    has_e = "e_ma200gc" in value
    if value == "event":
        return "event"
    if has_c and has_d and has_e:
        return "C+D+E"
    if has_d and has_e:
        return "D+E"
    if has_c and has_d:
        return "C+D"
    if has_c and has_e:
        return "C+E"
    if has_d:
        return "D"
    if has_e:
        return "E"
    if has_c:
        return "C"
    return "other"


def price_bucket(row):
    price = safe_float(row.get("close_price"))
    if price < 1000:
        return "P0 <1천"
    if price < 3000:
        return "P1 1천~3천"
    if price < 10000:
        return "P2 3천~1만"
    if price < 30000:
        return "P3 1만~3만"
    if price < 100000:
        return "P4 3만~10만"
    return "P5 10만+"


def e_bucket(row):
    e = safe_float(row.get("e_score"))
    if e >= 90:
        return "E90+"
    if e >= 80:
        return "E80~89"
    if e >= 70:
        return "E70~79"
    if e >= 60:
        return "E60~69"
    return "E<60"


def s_bucket(row):
    s = safe_float(row.get("s_score"))
    if s >= 45:
        return "S45~49"
    if s >= 40:
        return "S40~44"
    if s >= 35:
        return "S35~39"
    if s >= 30:
        return "S30~34"
    return "S<30"


def day_return_bucket(row):
    v = safe_float(row.get("day_return"))
    if v >= 20:
        return "DR20+"
    if v >= 15:
        return "DR15~20"
    if v >= 10:
        return "DR10~15"
    if v >= 7:
        return "DR7~10"
    if v >= 3:
        return "DR3~7"
    if v >= 0:
        return "DR0~3"
    return "DR<0"


def vol_bucket(row):
    v = safe_float(row.get("vol_ratio_20"))
    if v >= 20:
        return "VR20+"
    if v >= 10:
        return "VR10~20"
    if v >= 5:
        return "VR5~10"
    if v >= 3:
        return "VR3~5"
    if v >= 1:
        return "VR1~3"
    return "VR<1"


def bb_bucket(row):
    v = safe_float(row.get("bb_width"))
    if v >= 50:
        return "BW50+"
    if v >= 40:
        return "BW40~50"
    if v >= 30:
        return "BW30~40"
    if v >= 20:
        return "BW20~30"
    if v >= 10:
        return "BW10~20"
    return "BW<10"


def slope_bucket(row):
    ma60 = safe_int(row.get("ma60_slope_up"))
    ma200 = safe_int(row.get("ma200_slope_up"))
    if ma60 == 1 and ma200 == 1:
        return "UP60+UP200"
    if ma60 == 1:
        return "UP60_ONLY"
    if ma200 == 1:
        return "UP200_ONLY"
    return "BOTH_DOWN"


def analyze_groups(title, rows, key_func, min_count):
    logger.info("")
    logger.info("=" * 120)
    logger.info(title)
    logger.info("=" * 120)
    groups = group_by(rows, key_func)
    sortable = []
    for key, items in groups.items():
        if len(items) < min_count:
            continue
        s = calc_stats(items)
        sortable.append((key, items, s))

    sortable.sort(key=lambda x: (x[2]["avg_max"], x[2]["pct_50plus"], x[1].__len__()), reverse=True)

    for key, items, _ in sortable:
        print_stats_line(str(key), items)


def analyze_candidate_rules(c_rows, b1_rows, b2_rows, min_count):
    logger.info("")
    logger.info("=" * 120)
    logger.info("C_HOT / B1 승격 후보 룰")
    logger.info("=" * 120)

    candidate_rules = [
        ("C_E80", lambda r: safe_float(r.get("e_score")) >= 80),
        ("C_E80_PRICE_LT3000", lambda r: safe_float(r.get("e_score")) >= 80 and safe_float(r.get("close_price")) < 3000),
        ("C_E80_DR15", lambda r: safe_float(r.get("e_score")) >= 80 and safe_float(r.get("day_return")) >= 15),
        ("C_E80_VR10", lambda r: safe_float(r.get("e_score")) >= 80 and safe_float(r.get("vol_ratio_20")) >= 10),
        ("C_E80_BW40", lambda r: safe_float(r.get("e_score")) >= 80 and safe_float(r.get("bb_width")) >= 40),
        ("C_PRICE_LT3000_DR15", lambda r: safe_float(r.get("close_price")) < 3000 and safe_float(r.get("day_return")) >= 15),
        ("C_PRICE_LT3000_VR10", lambda r: safe_float(r.get("close_price")) < 3000 and safe_float(r.get("vol_ratio_20")) >= 10),
        ("C_EVENT_OR_E_E80", lambda r: norm_trigger(r.get("trigger_path")) in ("event", "E", "C+E") and safe_float(r.get("e_score")) >= 80),
        ("C_NOT_RSI_ONLY_E80", lambda r: norm_trigger(r.get("trigger_path")) != "C" and safe_float(r.get("e_score")) >= 80),
        ("C_S40_E80", lambda r: safe_float(r.get("s_score")) >= 40 and safe_float(r.get("e_score")) >= 80),
        ("C_S35_E80_PRICE_LT10000", lambda r: safe_float(r.get("s_score")) >= 35 and safe_float(r.get("e_score")) >= 80 and safe_float(r.get("close_price")) < 10000),
    ]

    logger.info("기준 비교")
    print_stats_line("B1 baseline", b1_rows)
    print_stats_line("B2 baseline", b2_rows)
    print_stats_line("C baseline", c_rows)

    logger.info("")
    logger.info("후보 룰별 결과")
    for rule_name, rule_func in candidate_rules:
        selected = [r for r in c_rows if rule_func(r)]
        if len(selected) < min_count:
            logger.info("%24s | %5d | min_count 미달", rule_name, len(selected))
            continue
        print_stats_line(rule_name, selected)


def print_top_rows(title, rows, top_n):
    logger.info("")
    logger.info("=" * 120)
    logger.info(title)
    logger.info("=" * 120)
    sorted_rows = sorted(rows, key=lambda r: safe_float(r.get("ret_max")), reverse=True)[:top_n]
    for i, r in enumerate(sorted_rows, 1):
        logger.info(
            "%3d. [%s] %-18s(%s) G=%s S=%3s E=%3s max=%+8.2f%% 1w=%+8.2f%% 1m=%+8.2f%% price=%8.0f DR=%+6.2f VR=%6.2f BW=%6.2f TP=%s",
            i,
            r.get("search_date"),
            str(r.get("name"))[:18],
            r.get("code"),
            r.get("grade"),
            r.get("s_score"),
            r.get("e_score"),
            safe_float(r.get("ret_max")),
            safe_float(r.get("ret_1w")),
            safe_float(r.get("ret_1m")),
            safe_float(r.get("close_price")),
            safe_float(r.get("day_return")),
            safe_float(r.get("vol_ratio_20")),
            safe_float(r.get("bb_width")),
            r.get("trigger_path"),
        )


def parse_args():
    parser = argparse.ArgumentParser(description="C등급 고수익 이상현상과 C_HOT 후보 룰을 분석합니다.")
    parser.add_argument("--condition-name", default=BacktestConfig.CONDITION_NAME)
    parser.add_argument("--start", default=None, help="search_date 시작일 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="search_date 종료일 YYYY-MM-DD")
    parser.add_argument("--min-count", type=int, default=20, help="그룹/룰 출력 최소 표본 수")
    parser.add_argument("--top", type=int, default=30, help="상위 종목 출력 개수")
    return parser.parse_args()


def main():
    args = parse_args()
    logger.info("=" * 120)
    logger.info("등급 이상현상 분석 시작")
    logger.info("condition_name=%s start=%s end=%s", args.condition_name, args.start, args.end)
    logger.info("=" * 120)

    conn = get_conn()
    try:
        rows = fetch_rows(conn, args.condition_name, args.start, args.end)
    finally:
        conn.close()

    logger.info("로딩 완료: %s건", len(rows))
    if not rows:
        logger.info("분석 대상이 없습니다. 먼저 python update_scan_scores.py 실행 여부를 확인하세요.")
        return

    by_grade = defaultdict(list)
    for row in rows:
        by_grade[row.get("grade")].append(row)

    logger.info("")
    logger.info("=" * 120)
    logger.info("등급별 기준 성과")
    logger.info("=" * 120)
    for grade in GRADE_ORDER:
        print_stats_line(grade, by_grade.get(grade, []))

    c_rows = by_grade.get("C", [])
    b1_rows = by_grade.get("B1", [])
    b2_rows = by_grade.get("B2", [])

    analyze_groups("C등급: E-score 구간별", c_rows, e_bucket, args.min_count)
    analyze_groups("C등급: S-score 구간별", c_rows, s_bucket, args.min_count)
    analyze_groups("C등급: 트리거 경로별", c_rows, lambda r: norm_trigger(r.get("trigger_path")), args.min_count)
    analyze_groups("C등급: 가격대별", c_rows, price_bucket, args.min_count)
    analyze_groups("C등급: 당일등락률 구간별", c_rows, day_return_bucket, args.min_count)
    analyze_groups("C등급: 거래량비율 구간별", c_rows, vol_bucket, args.min_count)
    analyze_groups("C등급: BB폭 구간별", c_rows, bb_bucket, args.min_count)
    analyze_groups("C등급: 기울기 조합별", c_rows, slope_bucket, args.min_count)

    analyze_candidate_rules(c_rows, b1_rows, b2_rows, args.min_count)

    print_top_rows("C등급 ret_max 상위 종목", c_rows, args.top)
    print_top_rows("B1등급 ret_max 상위 종목", b1_rows, args.top)
    print_top_rows("D등급 ret_max 상위 종목", by_grade.get("D", []), args.top)

    logger.info("")
    logger.info("분석 완료. logs/grade_anomaly_*.log 파일을 확인하세요.")


if __name__ == "__main__":
    main()
