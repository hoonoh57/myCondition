"""
ScoringModel v2.1 등급별 목표/손절/보유기간 최적화 스크립트

목적:
    simulate_grade_strategy_v21.py의 고정 정책 결과에서 손절 비율이 높은 문제가 확인되어,
    등급별 target/stop/max_days 조합을 자동 스윕합니다.

특징:
    - DB 수정 없음
    - scan_result.grade_v21 기준
    - daily_candles 실제 일봉 경로 사용
    - 결과는 등급별 상위 조합을 로그/CSV로 출력

Usage:
    python optimize_grade_strategy_v21.py --grade C_HOT
    python optimize_grade_strategy_v21.py --grade C_FAST
    python optimize_grade_strategy_v21.py --grade ALL
    python optimize_grade_strategy_v21.py --grade C_HOT --export-csv outputs/reports/opt_c_hot.csv
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
            LOG_DIR / f"optimize_grade_strategy_v21_{datetime.now():%Y%m%d_%H%M%S}.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


GRADES = ["A1", "A2", "B1", "B2", "C_HOT", "C_FAST"]


DEFAULT_SWEEPS = {
    "A1": {
        "targets": [8, 10, 12, 15, 20, 25, 30],
        "stops": [-2, -3, -4, -5, -6, -8],
        "days": [3, 5, 10, 15, 20],
        "position_pct": 100,
    },
    "A2": {
        "targets": [5, 8, 10, 12, 15, 20],
        "stops": [-2, -3, -4, -5, -6],
        "days": [3, 5, 10, 15, 20],
        "position_pct": 80,
    },
    "B1": {
        "targets": [5, 8, 10, 12, 15, 20, 25],
        "stops": [-2, -3, -4, -5, -6],
        "days": [2, 3, 5, 7, 10],
        "position_pct": 60,
    },
    "B2": {
        "targets": [4, 5, 8, 10, 12, 15],
        "stops": [-2, -3, -4, -5],
        "days": [2, 3, 5, 7, 10],
        "position_pct": 50,
    },
    "C_HOT": {
        "targets": [5, 8, 10, 12, 15, 20, 25, 30],
        "stops": [-2, -3, -4, -5, -6, -8],
        "days": [1, 2, 3, 5, 7, 10],
        "position_pct": 50,
    },
    "C_FAST": {
        "targets": [3, 5, 8, 10, 12, 15, 20],
        "stops": [-2, -3, -4, -5, -6],
        "days": [1, 2, 3, 5],
        "position_pct": 30,
    },
}


SCAN_COLUMNS = [
    "id", "condition_name", "search_date", "code", "name", "market",
    "trigger_path", "close_price", "day_return", "bb_width", "vol_ratio_20",
    "ret_1w", "ret_2w", "ret_3w", "ret_1m", "ret_max",
    "s_score", "e_score", "grade", "grade_v21", "strategy_v21",
]


def get_conn(database_name=None):
    return pymysql.connect(
        host=DBConfig.HOST,
        port=DBConfig.PORT,
        user=DBConfig.USER,
        password=DBConfig.PASSWORD,
        database=(database_name or DBConfig.BACKTEST_DB),
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


def safe_date_text(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    return str(value)[:10]


def build_where(condition_name, grade, start_date, end_date):
    clauses = ["score_version_v21 = '2.1'", "grade_v21 IS NOT NULL"]
    params = []

    if condition_name:
        clauses.append("condition_name = %s")
        params.append(condition_name)
    if grade and grade != "ALL":
        clauses.append("grade_v21 = %s")
        params.append(grade)
    elif grade == "ALL":
        clauses.append("grade_v21 IN ('A1','A2','B1','B2','C_HOT','C_FAST')")
    if start_date:
        clauses.append("search_date >= %s")
        params.append(start_date)
    if end_date:
        clauses.append("search_date <= %s")
        params.append(end_date)

    return "WHERE " + " AND ".join(clauses), params


def fetch_scan_rows(conn, condition_name, grade, start_date, end_date, limit):
    where_sql, params = build_where(condition_name, grade, start_date, end_date)
    sql = f"""
        SELECT {', '.join(SCAN_COLUMNS)}
        FROM scan_result
        {where_sql}
        ORDER BY search_date, grade_v21, code, id
    """
    if limit is not None and limit > 0:
        sql += " LIMIT %s"
        params.append(int(limit))

    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_future_candles(conn, code, search_date, max_days):
    table_name = f"`{DBConfig.STOCK_DATA_DB}`.`daily_candles`"
    sql = f"""
        SELECT date, open, high, low, close, volume
        FROM {table_name}
        WHERE code = %s
          AND date > %s
        ORDER BY date ASC
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (code, search_date, max_days))
        return cur.fetchall()


def load_candle_cache(stock_conn, rows, max_days):
    cache = {}
    total = len(rows)
    for idx, row in enumerate(rows, 1):
        key = row["id"]
        cache[key] = fetch_future_candles(stock_conn, row.get("code"), safe_date_text(row.get("search_date")), max_days)
        if idx % 500 == 0:
            logger.info("캔들 캐시 로딩: %s / %s", idx, total)
    return cache


def calc_net_return(entry_price, exit_price, buy_fee_pct, sell_fee_pct, tax_pct, slippage_pct):
    if entry_price <= 0:
        return 0.0
    gross = (exit_price - entry_price) / entry_price * 100.0
    total_cost = buy_fee_pct + sell_fee_pct + tax_pct + (slippage_pct * 2.0)
    return gross - total_cost


def choose_same_day_exit(entry_price, target_price, stop_price, candle, policy):
    high_price = safe_float(candle.get("high"))
    low_price = safe_float(candle.get("low"))
    open_price = safe_float(candle.get("open"))

    hit_target = high_price >= target_price
    hit_stop = low_price <= stop_price

    if hit_target and hit_stop:
        if policy == "target_first":
            return target_price, "target_same_day"
        if policy == "open_proximity":
            if abs(open_price - target_price) <= abs(open_price - stop_price):
                return target_price, "target_same_day_open_near"
            return stop_price, "stop_same_day_open_near"
        return stop_price, "stop_same_day"

    if hit_target:
        return target_price, "target"
    if hit_stop:
        return stop_price, "stop"
    return None, None


def simulate_return(row, candles, target, stop, max_days, args):
    entry_price = safe_float(row.get("close_price"))
    if entry_price <= 0 or not candles:
        return None

    target_price = entry_price * (1.0 + target / 100.0)
    stop_price = entry_price * (1.0 + stop / 100.0)

    max_high = entry_price
    min_low = entry_price
    exit_price = None
    exit_reason = None
    holding_days = 0

    usable = candles[:max_days]
    for idx, candle in enumerate(usable, 1):
        high_price = safe_float(candle.get("high"))
        low_price = safe_float(candle.get("low"))
        close_price = safe_float(candle.get("close"))

        if high_price > max_high:
            max_high = high_price
        if low_price < min_low:
            min_low = low_price

        price, reason = choose_same_day_exit(entry_price, target_price, stop_price, candle, args.same_day_policy)
        if price is not None:
            exit_price = price
            exit_reason = reason
            holding_days = idx
            break

        if idx >= max_days:
            exit_price = close_price
            exit_reason = "time_exit"
            holding_days = idx
            break

    if exit_price is None:
        last = usable[-1]
        exit_price = safe_float(last.get("close"))
        exit_reason = "last_available_exit"
        holding_days = len(usable)

    gross = (exit_price - entry_price) / entry_price * 100.0
    net = calc_net_return(entry_price, exit_price, args.buy_fee_pct, args.sell_fee_pct, args.tax_pct, args.slippage_pct)
    max_intra = (max_high - entry_price) / entry_price * 100.0
    min_intra = (min_low - entry_price) / entry_price * 100.0

    return {
        "net": net,
        "gross": gross,
        "holding_days": holding_days,
        "exit_reason": exit_reason,
        "max_intra": max_intra,
        "min_intra": min_intra,
    }


def stats_for_combination(rows, cache, grade, target, stop, days, position_pct, args):
    trade_results = []
    for row in rows:
        if row.get("grade_v21") != grade:
            continue
        candles = cache.get(row["id"], [])
        sim = simulate_return(row, candles, target, stop, days, args)
        if sim is not None:
            trade_results.append(sim)

    cnt = len(trade_results)
    if cnt == 0:
        return None

    net_vals = [x["net"] for x in trade_results]
    gross_vals = [x["gross"] for x in trade_results]
    hold_vals = [x["holding_days"] for x in trade_results]
    max_vals = [x["max_intra"] for x in trade_results]
    min_vals = [x["min_intra"] for x in trade_results]
    target_count = sum(1 for x in trade_results if str(x["exit_reason"]).startswith("target"))
    stop_count = sum(1 for x in trade_results if str(x["exit_reason"]).startswith("stop"))
    time_count = sum(1 for x in trade_results if x["exit_reason"] in ("time_exit", "last_available_exit"))
    wins = sum(1 for x in trade_results if x["net"] > 0)

    avg_net = sum(net_vals) / cnt
    win_rate = wins / cnt * 100.0
    target_rate = target_count / cnt * 100.0
    stop_rate = stop_count / cnt * 100.0
    time_rate = time_count / cnt * 100.0
    weighted_net = avg_net * position_pct / 100.0

    # 점수: 평균수익 중심, 승률/목표도달 보조, 손절률 페널티, 최악손실 페널티
    score = avg_net + (win_rate * 0.03) + (target_rate * 0.02) - (stop_rate * 0.015) + (min(net_vals) * 0.05)

    return {
        "grade": grade,
        "cnt": cnt,
        "target": target,
        "stop": stop,
        "days": days,
        "position_pct": position_pct,
        "avg_net": avg_net,
        "weighted_net": weighted_net,
        "avg_gross": sum(gross_vals) / cnt,
        "win_rate": win_rate,
        "target_rate": target_rate,
        "stop_rate": stop_rate,
        "time_rate": time_rate,
        "avg_hold": sum(hold_vals) / cnt,
        "avg_max_intra": sum(max_vals) / cnt,
        "avg_min_intra": sum(min_vals) / cnt,
        "best_net": max(net_vals),
        "worst_net": min(net_vals),
        "score": score,
    }


def optimize(rows, cache, grades, args):
    results = []
    for grade in grades:
        sweep = DEFAULT_SWEEPS[grade]
        grade_rows = [r for r in rows if r.get("grade_v21") == grade]
        logger.info("%s 최적화 시작: %s건", grade, len(grade_rows))
        for target in sweep["targets"]:
            for stop in sweep["stops"]:
                for days in sweep["days"]:
                    item = stats_for_combination(
                        rows=grade_rows,
                        cache=cache,
                        grade=grade,
                        target=float(target),
                        stop=float(stop),
                        days=int(days),
                        position_pct=float(sweep["position_pct"]),
                        args=args,
                    )
                    if item is not None:
                        results.append(item)
    return results


def print_best(results, top_n):
    by_grade = defaultdict(list)
    for item in results:
        by_grade[item["grade"]].append(item)

    for grade in GRADES:
        items = by_grade.get(grade, [])
        if not items:
            continue
        items.sort(key=lambda x: (x["score"], x["avg_net"], x["win_rate"]), reverse=True)
        logger.info("")
        logger.info("=" * 160)
        logger.info("%s 최적 조합 TOP %s", grade, top_n)
        logger.info("=" * 160)
        logger.info("%8s | %6s | %6s | %4s | %7s | %7s | %7s | %7s | %7s | %7s | %7s | %7s | %8s | %8s | %7s", "grade", "target", "stop", "days", "avgNet", "wNet", "win%", "target", "stop%", "time%", "hold", "maxIn", "best", "worst", "score")
        logger.info("-" * 160)
        for item in items[:top_n]:
            logger.info(
                "%8s | %5.1f%% | %5.1f%% | %4d | %+6.2f%% | %+6.2f%% | %6.1f%% | %6.1f%% | %6.1f%% | %6.1f%% | %6.2f | %+6.2f%% | %+7.2f%% | %+7.2f%% | %+6.2f",
                item["grade"], item["target"], item["stop"], item["days"],
                item["avg_net"], item["weighted_net"], item["win_rate"], item["target_rate"],
                item["stop_rate"], item["time_rate"], item["avg_hold"], item["avg_max_intra"],
                item["best_net"], item["worst_net"], item["score"],
            )


def export_csv(results, path_text):
    if not path_text:
        return
    path = Path(path_text)
    if not path.is_absolute():
        path = Path(__file__).parent / path
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "grade", "cnt", "target", "stop", "days", "position_pct", "avg_net", "weighted_net",
        "avg_gross", "win_rate", "target_rate", "stop_rate", "time_rate", "avg_hold",
        "avg_max_intra", "avg_min_intra", "best_net", "worst_net", "score",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for item in sorted(results, key=lambda x: (x["grade"], -x["score"])):
            writer.writerow(item)
    logger.info("CSV 저장 완료: %s", path)


def parse_args():
    parser = argparse.ArgumentParser(description="grade_v21별 target/stop/max_days 최적 조합 탐색")
    parser.add_argument("--condition-name", default=BacktestConfig.CONDITION_NAME)
    parser.add_argument("--grade", choices=GRADES + ["ALL"], default="ALL")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--same-day-policy", choices=["stop_first", "target_first", "open_proximity"], default="stop_first")
    parser.add_argument("--buy-fee-pct", type=float, default=0.015)
    parser.add_argument("--sell-fee-pct", type=float, default=0.015)
    parser.add_argument("--tax-pct", type=float, default=0.30)
    parser.add_argument("--slippage-pct", type=float, default=0.10)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--export-csv", default=None)
    return parser.parse_args()


def main():
    args = parse_args()
    grades = GRADES if args.grade == "ALL" else [args.grade]
    max_days = max(max(DEFAULT_SWEEPS[g]["days"]) for g in grades)

    logger.info("=" * 160)
    logger.info("v2.1 등급별 target/stop/max_days 최적화 시작")
    logger.info("grade=%s start=%s end=%s max_days=%s same_day_policy=%s", args.grade, args.start, args.end, max_days, args.same_day_policy)
    logger.info("cost=buy %.3f%% sell %.3f%% tax %.3f%% slippage %.3f%%x2", args.buy_fee_pct, args.sell_fee_pct, args.tax_pct, args.slippage_pct)
    logger.info("=" * 160)

    backtest_conn = get_conn(DBConfig.BACKTEST_DB)
    stock_conn = get_conn(DBConfig.STOCK_DATA_DB)
    try:
        rows = fetch_scan_rows(backtest_conn, args.condition_name, args.grade, args.start, args.end, args.limit)
        logger.info("scan_result 로딩 완료: %s건", len(rows))
        cache = load_candle_cache(stock_conn, rows, max_days)
    finally:
        backtest_conn.close()
        stock_conn.close()

    results = optimize(rows, cache, grades, args)
    logger.info("조합 평가 완료: %s개", len(results))
    print_best(results, args.top)
    export_csv(results, args.export_csv)
    logger.info("최적화 완료. DB는 수정하지 않았습니다.")


if __name__ == "__main__":
    main()
