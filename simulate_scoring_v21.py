"""
ScoringModel v2.1 등급체계 시뮬레이션

목적:
    DB를 수정하지 않고 scan_result의 v2.0 스코어 결과를 읽어
    메모리에서만 v2.1 등급을 재분류합니다.

핵심 변경:
    기존 C등급을 C_HOT / C_FAST / C_WATCH / C_BAD로 세분화합니다.

v2.1 룰:
    A1      : S >= 70 and E >= 60
    A2      : S >= 70 and E < 60
    B1      : 50 <= S < 70 and E >= 60
    B2      : 50 <= S < 70 and E < 60
    C_HOT   : 기존 C 영역 중 S >= 35 and E >= 80 and close_price < 10000
    C_FAST  : 기존 C 영역 중 E >= 80 and bb_width >= 40
    C_WATCH : 기존 C 영역 중 E >= 80 or trigger is event/E-series
    C_BAD   : 나머지 C 영역
    D       : 기존 D 영역

Usage:
    python simulate_scoring_v21.py
    python simulate_scoring_v21.py --top 30
    python simulate_scoring_v21.py --start 2024-05-20 --end 2026-05-15
    python simulate_scoring_v21.py --export-csv outputs/reports/scoring_v21_simulation.csv
"""
import argparse
import csv
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
            LOG_DIR / f"simulate_scoring_v21_{datetime.now():%Y%m%d_%H%M%S}.log",
            encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


V20_ORDER = ["A1", "A2", "B1", "B2", "C", "D"]
V21_ORDER = ["A1", "A2", "B1", "B2", "C_HOT", "C_FAST", "C_WATCH", "C_BAD", "D"]


V21_STRATEGY = {
    "A1": {
        "position_pct": 100,
        "target_return": 30.0,
        "stop_loss": -5.0,
        "max_holding_days": 20,
        "style": "swing",
    },
    "A2": {
        "position_pct": 80,
        "target_return": 20.0,
        "stop_loss": -4.0,
        "max_holding_days": 20,
        "style": "swing_stability",
    },
    "B1": {
        "position_pct": 60,
        "target_return": 25.0,
        "stop_loss": -5.0,
        "max_holding_days": 10,
        "style": "short_swing_explosion",
    },
    "B2": {
        "position_pct": 50,
        "target_return": 15.0,
        "stop_loss": -4.0,
        "max_holding_days": 10,
        "style": "short_term_standard",
    },
    "C_HOT": {
        "position_pct": 50,
        "target_return": 20.0,
        "stop_loss": -5.0,
        "max_holding_days": 10,
        "style": "promoted_low_price_hot",
    },
    "C_FAST": {
        "position_pct": 30,
        "target_return": 15.0,
        "stop_loss": -4.0,
        "max_holding_days": 3,
        "style": "fast_explosion_only",
    },
    "C_WATCH": {
        "position_pct": 10,
        "target_return": 10.0,
        "stop_loss": -3.0,
        "max_holding_days": 3,
        "style": "watch_only",
    },
    "C_BAD": {
        "position_pct": 0,
        "target_return": 0.0,
        "stop_loss": 0.0,
        "max_holding_days": 0,
        "style": "skip_bad_c",
    },
    "D": {
        "position_pct": 0,
        "target_return": 0.0,
        "stop_loss": 0.0,
        "max_holding_days": 0,
        "style": "skip_d",
    },
}


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


def is_event_or_e_series(row):
    tp = norm_trigger(row.get("trigger_path"))
    return tp in ("event", "E", "C+E")


def classify_v21(row):
    s_score = safe_float(row.get("s_score"))
    e_score = safe_float(row.get("e_score"))
    close_price = safe_float(row.get("close_price"))
    bb_width = safe_float(row.get("bb_width"))

    if s_score >= 70:
        if e_score >= 60:
            return "A1"
        return "A2"

    if s_score >= 50:
        if e_score >= 60:
            return "B1"
        return "B2"

    if e_score < 60:
        return "D"

    # 여기부터 기존 C 영역: S < 50 and E >= 60
    if s_score >= 35 and e_score >= 80 and close_price < 10000:
        return "C_HOT"

    if e_score >= 80 and bb_width >= 40:
        return "C_FAST"

    if e_score >= 80 or is_event_or_e_series(row):
        return "C_WATCH"

    return "C_BAD"


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
            "win_rate": 0.0,
            "avg_max": 0.0,
            "avg_1w": 0.0,
            "avg_2w": 0.0,
            "avg_3w": 0.0,
            "avg_1m": 0.0,
            "pct_10plus": 0.0,
            "pct_20plus": 0.0,
            "pct_30plus": 0.0,
            "pct_50plus": 0.0,
            "pct_100plus": 0.0,
            "avg_s": 0.0,
            "avg_e": 0.0,
            "avg_price": 0.0,
            "avg_day_return": 0.0,
            "avg_vol_ratio": 0.0,
            "avg_bb_width": 0.0,
            "best_max": 0.0,
            "worst_1m": 0.0,
        }

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

    wins = sum(1 for v in max_vals if v > 0)
    pct_10 = sum(1 for v in max_vals if v >= 10)
    pct_20 = sum(1 for v in max_vals if v >= 20)
    pct_30 = sum(1 for v in max_vals if v >= 30)
    pct_50 = sum(1 for v in max_vals if v >= 50)
    pct_100 = sum(1 for v in max_vals if v >= 100)

    return {
        "cnt": cnt,
        "win_rate": wins / cnt * 100.0 if cnt else 0.0,
        "avg_max": sum(max_vals) / len(max_vals) if max_vals else 0.0,
        "avg_1w": sum(w1_vals) / len(w1_vals) if w1_vals else 0.0,
        "avg_2w": sum(w2_vals) / len(w2_vals) if w2_vals else 0.0,
        "avg_3w": sum(w3_vals) / len(w3_vals) if w3_vals else 0.0,
        "avg_1m": sum(m1_vals) / len(m1_vals) if m1_vals else 0.0,
        "pct_10plus": pct_10 / cnt * 100.0 if cnt else 0.0,
        "pct_20plus": pct_20 / cnt * 100.0 if cnt else 0.0,
        "pct_30plus": pct_30 / cnt * 100.0 if cnt else 0.0,
        "pct_50plus": pct_50 / cnt * 100.0 if cnt else 0.0,
        "pct_100plus": pct_100 / cnt * 100.0 if cnt else 0.0,
        "avg_s": sum(s_vals) / len(s_vals) if s_vals else 0.0,
        "avg_e": sum(e_vals) / len(e_vals) if e_vals else 0.0,
        "avg_price": sum(price_vals) / len(price_vals) if price_vals else 0.0,
        "avg_day_return": sum(dr_vals) / len(dr_vals) if dr_vals else 0.0,
        "avg_vol_ratio": sum(vr_vals) / len(vr_vals) if vr_vals else 0.0,
        "avg_bb_width": sum(bw_vals) / len(bw_vals) if bw_vals else 0.0,
        "best_max": max(max_vals) if max_vals else 0.0,
        "worst_1m": min(m1_vals) if m1_vals else 0.0,
    }


def print_stats_header():
    logger.info(
        "%12s | %5s | %7s | %7s | %7s | %7s | %7s | %6s | %6s | %6s | %6s | %5s | %5s | %8s",
        "grade",
        "cnt",
        "avgMax",
        "avg1w",
        "avg2w",
        "avg3w",
        "avg1m",
        "20%+",
        "30%+",
        "50%+",
        "100%+",
        "avgS",
        "avgE",
        "best",
    )
    logger.info("-" * 128)


def print_stats_line(label, rows):
    s = calc_stats(rows)
    logger.info(
        "%12s | %5d | %+6.2f%% | %+6.2f%% | %+6.2f%% | %+6.2f%% | %+6.2f%% | %5.1f%% | %5.1f%% | %5.1f%% | %5.1f%% | %5.1f | %5.1f | %+7.2f%%",
        label,
        s["cnt"],
        s["avg_max"],
        s["avg_1w"],
        s["avg_2w"],
        s["avg_3w"],
        s["avg_1m"],
        s["pct_20plus"],
        s["pct_30plus"],
        s["pct_50plus"],
        s["pct_100plus"],
        s["avg_s"],
        s["avg_e"],
        s["best_max"],
    )


def group_by(rows, key_name):
    groups = defaultdict(list)
    for row in rows:
        groups[row.get(key_name)].append(row)
    return groups


def apply_v21(rows):
    for row in rows:
        row["grade_v20"] = row.get("grade")
        row["grade_v21"] = classify_v21(row)
        row["strategy_v21"] = V21_STRATEGY[row["grade_v21"]]["style"]
    return rows


def print_v20_vs_v21(rows):
    logger.info("")
    logger.info("=" * 128)
    logger.info("v2.0 등급별 성과")
    logger.info("=" * 128)
    print_stats_header()
    by_v20 = group_by(rows, "grade_v20")
    for grade in V20_ORDER:
        print_stats_line(grade, by_v20.get(grade, []))

    logger.info("")
    logger.info("=" * 128)
    logger.info("v2.1 등급별 성과")
    logger.info("=" * 128)
    print_stats_header()
    by_v21 = group_by(rows, "grade_v21")
    for grade in V21_ORDER:
        print_stats_line(grade, by_v21.get(grade, []))


def print_transition_matrix(rows):
    logger.info("")
    logger.info("=" * 128)
    logger.info("v2.0 -> v2.1 전이 매트릭스")
    logger.info("=" * 128)

    matrix = defaultdict(lambda: defaultdict(int))
    for row in rows:
        matrix[row.get("grade_v20")][row.get("grade_v21")] += 1

    header = "v20\\v21".rjust(10)
    for grade in V21_ORDER:
        header += f" {grade:>8}"
    logger.info(header)
    logger.info("-" * (10 + len(V21_ORDER) * 9))

    for old_grade in V20_ORDER:
        line = old_grade.rjust(10)
        for new_grade in V21_ORDER:
            line += f" {matrix[old_grade].get(new_grade, 0):>8}"
        logger.info(line)


def print_selected_top(rows, grade, top_n):
    selected = [r for r in rows if r.get("grade_v21") == grade]
    selected.sort(key=lambda r: safe_float(r.get("ret_max")), reverse=True)

    logger.info("")
    logger.info("=" * 128)
    logger.info("%s ret_max 상위 %d", grade, top_n)
    logger.info("=" * 128)

    for i, r in enumerate(selected[:top_n], 1):
        logger.info(
            "%3d. [%s] %-18s(%s) v20=%s v21=%s S=%3s E=%3s max=%+8.2f%% 1w=%+8.2f%% 1m=%+8.2f%% price=%8.0f DR=%+6.2f VR=%6.2f BW=%6.2f TP=%s",
            i,
            r.get("search_date"),
            str(r.get("name"))[:18],
            r.get("code"),
            r.get("grade_v20"),
            r.get("grade_v21"),
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


def print_monthly(rows):
    logger.info("")
    logger.info("=" * 128)
    logger.info("v2.1 주요 등급 월별 성과: A1/B1/C_HOT/C_FAST")
    logger.info("=" * 128)

    monthly = defaultdict(lambda: defaultdict(list))
    for row in rows:
        date_text = str(row.get("search_date"))[:7]
        grade = row.get("grade_v21")
        if grade in ("A1", "B1", "C_HOT", "C_FAST"):
            monthly[date_text][grade].append(row)

    for month in sorted(monthly.keys()):
        logger.info("")
        logger.info("[%s]", month)
        print_stats_header()
        for grade in ("A1", "B1", "C_HOT", "C_FAST"):
            print_stats_line(grade, monthly[month].get(grade, []))


def export_csv(rows, path_text):
    if not path_text:
        return

    path = Path(path_text)
    if not path.is_absolute():
        path = Path(__file__).parent / path
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "id",
        "condition_name",
        "search_date",
        "code",
        "name",
        "market",
        "grade_v20",
        "grade_v21",
        "strategy_v21",
        "s_score",
        "e_score",
        "trigger_path",
        "close_price",
        "day_return",
        "vol_ratio_20",
        "bb_width",
        "ma60_200_dist",
        "rsi14",
        "ma60_slope_up",
        "ma200_slope_up",
        "ret_1w",
        "ret_2w",
        "ret_3w",
        "ret_1m",
        "ret_max",
        "max_high_date",
    ]

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    logger.info("CSV 저장 완료: %s", path)


def parse_args():
    parser = argparse.ArgumentParser(description="ScoringModel v2.1 등급체계를 메모리에서 시뮬레이션합니다.")
    parser.add_argument("--condition-name", default=BacktestConfig.CONDITION_NAME)
    parser.add_argument("--start", default=None, help="search_date 시작일 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="search_date 종료일 YYYY-MM-DD")
    parser.add_argument("--top", type=int, default=30, help="등급별 상위 출력 개수")
    parser.add_argument("--monthly", action="store_true", help="월별 성과 출력")
    parser.add_argument("--export-csv", default=None, help="v2.1 재분류 결과 CSV 저장 경로")
    return parser.parse_args()


def main():
    args = parse_args()

    logger.info("=" * 128)
    logger.info("ScoringModel v2.1 시뮬레이션 시작")
    logger.info("condition_name=%s start=%s end=%s", args.condition_name, args.start, args.end)
    logger.info("=" * 128)

    conn = get_conn()
    try:
        rows = fetch_rows(conn, args.condition_name, args.start, args.end)
    finally:
        conn.close()

    logger.info("로딩 완료: %s건", len(rows))
    if not rows:
        logger.info("분석 대상이 없습니다. 먼저 python update_scan_scores.py 실행 여부를 확인하세요.")
        return

    apply_v21(rows)
    print_v20_vs_v21(rows)
    print_transition_matrix(rows)

    for grade in ("C_HOT", "C_FAST", "C_WATCH", "C_BAD", "A1", "B1"):
        print_selected_top(rows, grade, args.top)

    if args.monthly:
        print_monthly(rows)

    export_csv(rows, args.export_csv)

    logger.info("")
    logger.info("시뮬레이션 완료. logs/simulate_scoring_v21_*.log 파일을 확인하세요.")


if __name__ == "__main__":
    main()
