"""
조건식/패턴 정합성 분석 스크립트

목적:
    TopN, 종목 수 제한, 포트폴리오 압축으로 바로 가지 않고,
    현재 조건식과 trigger_path 패턴이 실제 수익 차이를 일관되게 설명하는지 검증합니다.

핵심 질문:
    1. trigger_path 패턴별 수익률 차이가 존재하는가?
    2. 같은 패턴이라도 시장국면/P1-P2/가격대/거래량/BB폭에 따라 성과가 달라지는가?
    3. S-score/E-score/grade_v21은 패턴의 구조적 차이를 올바르게 반영하는가?
    4. C_HOT/C_FAST 같은 분류가 단순 결과 과적합인지, 패턴 논리와 연결되는지 확인한다.

Usage:
    python analyze_pattern_consistency.py
    python analyze_pattern_consistency.py --start 2024-05-20 --end 2026-05-15
    python analyze_pattern_consistency.py --min-count 20
    python analyze_pattern_consistency.py --export-csv outputs/reports/pattern_consistency.csv
"""
import argparse
import csv
import logging
import sys
from collections import defaultdict
from datetime import date, datetime
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
            LOG_DIR / f"pattern_consistency_{datetime.now():%Y%m%d_%H%M%S}.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


SCAN_COLUMNS = [
    "id", "condition_name", "search_date", "code", "name", "market",
    "trigger_path", "close_price", "volume", "trade_value", "day_return",
    "ma60_200_dist", "rsi14", "bb_width", "vol_ratio_20",
    "ma60_slope_up", "ma200_slope_up",
    "ret_1w", "ret_2w", "ret_3w", "ret_1m", "ret_max", "max_high_date",
    "s_score", "e_score", "grade", "grade_v21", "strategy_v21",
]


PATTERN_ORDER = [
    "A_EVENT_LIMITLIKE",
    "B_D_E_GOLDEN_CORE",
    "C_D_ONLY_60GC",
    "D_E_ONLY_200GC",
    "E_RSI_E_MIXED",
    "F_RSI_D_MIXED",
    "G_RSI_ONLY",
    "H_COMPLEX_CDE",
    "Z_OTHER",
]


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


def safe_date_text(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    return str(value)[:10]


def norm_trigger(trigger_path):
    text = (trigger_path or "").lower()
    has_c = "c_rsi70" in text
    has_d = "d_ma60gc" in text
    has_e = "e_ma200gc" in text

    if text == "event":
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


def pattern_group(row):
    """
    현재 trigger_path를 구조적 패턴으로 매핑합니다.

    A_EVENT_LIMITLIKE: event성 급등/상한가성 포착
    B_D_E_GOLDEN_CORE: 60/200 골든크로스 코어 구조
    C_D_ONLY_60GC    : 60일선 돌파 중심
    D_E_ONLY_200GC   : 200일선 돌파 중심
    E_RSI_E_MIXED    : RSI 과열 + 200일선 혼합
    F_RSI_D_MIXED    : RSI 과열 + 60일선 혼합
    G_RSI_ONLY       : RSI 과열 단독
    H_COMPLEX_CDE    : RSI+60+200 복합
    """
    nt = norm_trigger(row.get("trigger_path"))
    if nt == "event":
        return "A_EVENT_LIMITLIKE"
    if nt == "D+E":
        return "B_D_E_GOLDEN_CORE"
    if nt == "D":
        return "C_D_ONLY_60GC"
    if nt == "E":
        return "D_E_ONLY_200GC"
    if nt == "C+E":
        return "E_RSI_E_MIXED"
    if nt == "C+D":
        return "F_RSI_D_MIXED"
    if nt == "C":
        return "G_RSI_ONLY"
    if nt == "C+D+E":
        return "H_COMPLEX_CDE"
    return "Z_OTHER"


def market_phase(row):
    d = safe_date_text(row.get("search_date"))
    if d < "2025-05-01":
        return "P1_BEAR_SIDEWAYS"
    return "P2_BULL"


def price_bucket(row):
    p = safe_float(row.get("close_price"))
    if p < 1000:
        return "P0_LT_1000"
    if p < 3000:
        return "P1_1000_3000"
    if p < 10000:
        return "P2_3000_10000"
    if p < 30000:
        return "P3_10000_30000"
    if p < 100000:
        return "P4_30000_100000"
    return "P5_100000_PLUS"


def volume_bucket(row):
    v = safe_float(row.get("vol_ratio_20"))
    if v >= 20:
        return "VR20_PLUS"
    if v >= 10:
        return "VR10_20"
    if v >= 5:
        return "VR5_10"
    if v >= 3:
        return "VR3_5"
    if v >= 1:
        return "VR1_3"
    return "VR_LT_1"


def bb_bucket(row):
    v = safe_float(row.get("bb_width"))
    if v >= 50:
        return "BW50_PLUS"
    if v >= 40:
        return "BW40_50"
    if v >= 30:
        return "BW30_40"
    if v >= 20:
        return "BW20_30"
    if v >= 10:
        return "BW10_20"
    return "BW_LT_10"


def score_bucket(row):
    s = safe_float(row.get("s_score"))
    e = safe_float(row.get("e_score"))
    if s >= 70 and e >= 60:
        return "S_HIGH_E_HIGH"
    if s >= 70:
        return "S_HIGH_E_LOW"
    if s >= 50 and e >= 60:
        return "S_MID_E_HIGH"
    if s >= 50:
        return "S_MID_E_LOW"
    if e >= 80:
        return "S_LOW_E_VERY_HIGH"
    if e >= 60:
        return "S_LOW_E_HIGH"
    return "S_LOW_E_LOW"


def build_where(condition_name, start_date, end_date):
    clauses = []
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
    if not clauses:
        return "", params
    return "WHERE " + " AND ".join(clauses), params


def fetch_rows(conn, condition_name, start_date, end_date):
    where_sql, params = build_where(condition_name, start_date, end_date)
    sql = f"""
        SELECT {', '.join(SCAN_COLUMNS)}
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
        return None

    max_vals = [safe_float(r.get("ret_max")) for r in rows if r.get("ret_max") is not None]
    w1_vals = [safe_float(r.get("ret_1w")) for r in rows if r.get("ret_1w") is not None]
    w2_vals = [safe_float(r.get("ret_2w")) for r in rows if r.get("ret_2w") is not None]
    w3_vals = [safe_float(r.get("ret_3w")) for r in rows if r.get("ret_3w") is not None]
    m1_vals = [safe_float(r.get("ret_1m")) for r in rows if r.get("ret_1m") is not None]
    s_vals = [safe_float(r.get("s_score")) for r in rows if r.get("s_score") is not None]
    e_vals = [safe_float(r.get("e_score")) for r in rows if r.get("e_score") is not None]
    price_vals = [safe_float(r.get("close_price")) for r in rows if r.get("close_price") is not None]
    dr_vals = [safe_float(r.get("day_return")) for r in rows if r.get("day_return") is not None]
    vr_vals = [safe_float(r.get("vol_ratio_20")) for r in rows if r.get("vol_ratio_20") is not None]
    bw_vals = [safe_float(r.get("bb_width")) for r in rows if r.get("bb_width") is not None]

    def avg(values):
        return sum(values) / len(values) if values else 0.0

    pct_10 = sum(1 for v in max_vals if v >= 10) / cnt * 100.0
    pct_20 = sum(1 for v in max_vals if v >= 20) / cnt * 100.0
    pct_30 = sum(1 for v in max_vals if v >= 30) / cnt * 100.0
    pct_50 = sum(1 for v in max_vals if v >= 50) / cnt * 100.0
    pct_100 = sum(1 for v in max_vals if v >= 100) / cnt * 100.0

    return {
        "cnt": cnt,
        "avg_max": avg(max_vals),
        "avg_1w": avg(w1_vals),
        "avg_2w": avg(w2_vals),
        "avg_3w": avg(w3_vals),
        "avg_1m": avg(m1_vals),
        "pct_10plus": pct_10,
        "pct_20plus": pct_20,
        "pct_30plus": pct_30,
        "pct_50plus": pct_50,
        "pct_100plus": pct_100,
        "avg_s": avg(s_vals),
        "avg_e": avg(e_vals),
        "avg_price": avg(price_vals),
        "avg_day_return": avg(dr_vals),
        "avg_vol_ratio": avg(vr_vals),
        "avg_bb_width": avg(bw_vals),
        "best_max": max(max_vals) if max_vals else 0.0,
        "worst_1m": min(m1_vals) if m1_vals else 0.0,
    }


def group_by(rows, key_func):
    groups = defaultdict(list)
    for row in rows:
        groups[key_func(row)].append(row)
    return groups


def print_header():
    logger.info("%28s | %5s | %8s | %7s | %7s | %7s | %7s | %6s | %6s | %6s | %6s | %5s | %5s | %7s | %7s | %7s", "group", "cnt", "avgMax", "avg1w", "avg2w", "avg3w", "avg1m", "20%+", "30%+", "50%+", "100%+", "avgS", "avgE", "avgDR", "avgVR", "avgBW")
    logger.info("-" * 170)


def print_line(label, rows):
    s = calc_stats(rows)
    if s is None:
        return
    logger.info("%28s | %5d | %+7.2f%% | %+6.2f%% | %+6.2f%% | %+6.2f%% | %+6.2f%% | %5.1f%% | %5.1f%% | %5.1f%% | %5.1f%% | %5.1f | %5.1f | %+6.2f%% | %6.2f | %6.2f", label, s["cnt"], s["avg_max"], s["avg_1w"], s["avg_2w"], s["avg_3w"], s["avg_1m"], s["pct_20plus"], s["pct_30plus"], s["pct_50plus"], s["pct_100plus"], s["avg_s"], s["avg_e"], s["avg_day_return"], s["avg_vol_ratio"], s["avg_bb_width"])


def analyze_one_dimension(title, rows, key_func, min_count, preferred_order=None):
    logger.info("")
    logger.info("=" * 170)
    logger.info(title)
    logger.info("=" * 170)
    print_header()
    groups = group_by(rows, key_func)
    items = []
    for key, group_rows in groups.items():
        if len(group_rows) >= min_count:
            items.append((key, group_rows))
    if preferred_order:
        order_index = {k: i for i, k in enumerate(preferred_order)}
        items.sort(key=lambda x: order_index.get(x[0], 999))
    else:
        items.sort(key=lambda x: calc_stats(x[1])["avg_max"], reverse=True)
    for key, group_rows in items:
        print_line(str(key), group_rows)


def analyze_cross(title, rows, key1_func, key2_func, min_count):
    logger.info("")
    logger.info("=" * 170)
    logger.info(title)
    logger.info("=" * 170)
    print_header()
    groups = defaultdict(list)
    for row in rows:
        key = f"{key1_func(row)} x {key2_func(row)}"
        groups[key].append(row)
    items = []
    for key, group_rows in groups.items():
        if len(group_rows) >= min_count:
            items.append((key, group_rows))
    items.sort(key=lambda x: (calc_stats(x[1])["avg_max"], calc_stats(x[1])["pct_50plus"]), reverse=True)
    for key, group_rows in items[:80]:
        print_line(key, group_rows)


def export_rows(rows, path_text):
    if not path_text:
        return
    path = Path(path_text)
    if not path.is_absolute():
        path = Path(__file__).parent / path
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "analysis_group", "phase", "price_bucket", "volume_bucket", "bb_bucket", "score_bucket",
        "id", "search_date", "code", "name", "market", "condition_name", "trigger_path",
        "close_price", "day_return", "vol_ratio_20", "bb_width", "s_score", "e_score", "grade", "grade_v21",
        "ret_1w", "ret_2w", "ret_3w", "ret_1m", "ret_max", "max_high_date",
    ]
    out_rows = []
    for row in rows:
        item = dict(row)
        item["analysis_group"] = pattern_group(row)
        item["phase"] = market_phase(row)
        item["price_bucket"] = price_bucket(row)
        item["volume_bucket"] = volume_bucket(row)
        item["bb_bucket"] = bb_bucket(row)
        item["score_bucket"] = score_bucket(row)
        out_rows.append(item)

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in out_rows:
            writer.writerow(row)
    logger.info("CSV 저장 완료: %s", path)


def parse_args():
    parser = argparse.ArgumentParser(description="조건식/trigger_path 패턴 정합성을 분석합니다.")
    parser.add_argument("--condition-name", default=BacktestConfig.CONDITION_NAME)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--min-count", type=int, default=20)
    parser.add_argument("--export-csv", default=None)
    return parser.parse_args()


def main():
    args = parse_args()
    logger.info("=" * 170)
    logger.info("조건식/패턴 정합성 분석 시작")
    logger.info("condition_name=%s start=%s end=%s min_count=%s", args.condition_name, args.start, args.end, args.min_count)
    logger.info("=" * 170)

    conn = get_conn()
    try:
        rows = fetch_rows(conn, args.condition_name, args.start, args.end)
    finally:
        conn.close()

    logger.info("로딩 완료: %s건", len(rows))
    if not rows:
        return

    analyze_one_dimension("1. trigger_path 구조 패턴별 성과", rows, pattern_group, args.min_count, PATTERN_ORDER)
    analyze_one_dimension("2. 시장국면별 성과", rows, market_phase, args.min_count)
    analyze_one_dimension("3. grade_v21별 성과", rows, lambda r: str(r.get("grade_v21")), args.min_count)
    analyze_one_dimension("4. 가격대별 성과", rows, price_bucket, args.min_count)
    analyze_one_dimension("5. 거래량비율 구간별 성과", rows, volume_bucket, args.min_count)
    analyze_one_dimension("6. BB폭 구간별 성과", rows, bb_bucket, args.min_count)
    analyze_one_dimension("7. S/E score 구조별 성과", rows, score_bucket, args.min_count)

    analyze_cross("8. 패턴 x 시장국면", rows, pattern_group, market_phase, args.min_count)
    analyze_cross("9. 패턴 x 가격대", rows, pattern_group, price_bucket, args.min_count)
    analyze_cross("10. 패턴 x 거래량비율", rows, pattern_group, volume_bucket, args.min_count)
    analyze_cross("11. 패턴 x grade_v21", rows, pattern_group, lambda r: str(r.get("grade_v21")), args.min_count)
    analyze_cross("12. grade_v21 x 시장국면", rows, lambda r: str(r.get("grade_v21")), market_phase, args.min_count)

    export_rows(rows, args.export_csv)
    logger.info("분석 완료. DB는 수정하지 않았습니다.")


if __name__ == "__main__":
    main()
