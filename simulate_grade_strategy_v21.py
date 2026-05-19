"""
ScoringModel v2.1 등급별 실전형 매매 시뮬레이션

목적:
    grade_v21별 목표수익/손절/최대보유기간 정책을 적용하여
    ret_max가 아니라 실제 일봉 경로 기반의 실현수익률을 계산합니다.

핵심:
    - 진입가: scan_result.close_price
    - 진입일: scan_result.search_date 종가 진입으로 간주
    - 청산 평가: 다음 거래일부터 daily_candles의 high/low/close 사용
    - 같은 날 target/stop 동시 터치 시 기본은 stop_first 보수 기준
    - 수수료/세금/슬리피지 반영

Usage:
    python simulate_grade_strategy_v21.py --dry-run --limit 20
    python simulate_grade_strategy_v21.py
    python simulate_grade_strategy_v21.py --same-day-policy target_first
    python simulate_grade_strategy_v21.py --export-csv outputs/reports/grade_strategy_v21.csv

주의:
    이 스크립트는 DB를 수정하지 않습니다. 결과는 로그/CSV로만 출력합니다.
"""
import argparse
import csv
import logging
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
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
            LOG_DIR / f"simulate_grade_strategy_v21_{datetime.now():%Y%m%d_%H%M%S}.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


GRADE_ORDER = ["A1", "A2", "B1", "B2", "C_HOT", "C_FAST", "C_WATCH", "C_BAD", "D"]


POLICIES = {
    "A1": {"target": 30.0, "stop": -5.0, "max_days": 20, "position_pct": 100, "enabled": True},
    "A2": {"target": 20.0, "stop": -4.0, "max_days": 20, "position_pct": 80, "enabled": True},
    "B1": {"target": 25.0, "stop": -5.0, "max_days": 10, "position_pct": 60, "enabled": True},
    "B2": {"target": 15.0, "stop": -4.0, "max_days": 10, "position_pct": 50, "enabled": True},
    "C_HOT": {"target": 20.0, "stop": -5.0, "max_days": 10, "position_pct": 50, "enabled": True},
    "C_FAST": {"target": 15.0, "stop": -4.0, "max_days": 3, "position_pct": 30, "enabled": True},
    "C_WATCH": {"target": 10.0, "stop": -3.0, "max_days": 3, "position_pct": 10, "enabled": False},
    "C_BAD": {"target": 0.0, "stop": 0.0, "max_days": 0, "position_pct": 0, "enabled": False},
    "D": {"target": 0.0, "stop": 0.0, "max_days": 0, "position_pct": 0, "enabled": False},
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


def build_where(condition_name, start_date, end_date, enabled_only):
    clauses = ["score_version_v21 = '2.1'", "grade_v21 IS NOT NULL"]
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
    if enabled_only:
        clauses.append("grade_v21 IN ('A1','A2','B1','B2','C_HOT','C_FAST')")

    return "WHERE " + " AND ".join(clauses), params


def fetch_scan_rows(conn, condition_name, start_date, end_date, enabled_only, limit):
    where_sql, params = build_where(condition_name, start_date, end_date, enabled_only)
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
    if max_days <= 0:
        return []

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


def simulate_one(row, candles, args):
    grade = row.get("grade_v21")
    policy = POLICIES.get(grade, POLICIES["D"])
    entry_price = safe_float(row.get("close_price"))
    search_date = safe_date_text(row.get("search_date"))

    result = dict(row)
    result["entry_date"] = search_date
    result["entry_price"] = entry_price
    result["policy_target"] = policy["target"]
    result["policy_stop"] = policy["stop"]
    result["policy_max_days"] = policy["max_days"]
    result["position_pct"] = policy["position_pct"]
    result["trade_enabled"] = 1 if policy["enabled"] else 0
    result["exit_date"] = None
    result["exit_price"] = None
    result["exit_reason"] = "disabled"
    result["holding_days"] = 0
    result["gross_return"] = 0.0
    result["net_return"] = 0.0
    result["weighted_net_return"] = 0.0
    result["max_intratrade_return"] = 0.0
    result["min_intratrade_return"] = 0.0
    result["candle_count"] = len(candles)

    if not policy["enabled"] or entry_price <= 0:
        return result

    if not candles:
        result["exit_reason"] = "no_future_candles"
        return result

    target_price = entry_price * (1.0 + policy["target"] / 100.0)
    stop_price = entry_price * (1.0 + policy["stop"] / 100.0)

    max_high = entry_price
    min_low = entry_price
    exit_price = None
    exit_reason = None
    exit_date = None
    holding_days = 0

    for idx, candle in enumerate(candles, 1):
        high_price = safe_float(candle.get("high"))
        low_price = safe_float(candle.get("low"))
        close_price = safe_float(candle.get("close"))
        candle_date = safe_date_text(candle.get("date"))

        if high_price > max_high:
            max_high = high_price
        if low_price < min_low:
            min_low = low_price

        price, reason = choose_same_day_exit(entry_price, target_price, stop_price, candle, args.same_day_policy)
        if price is not None:
            exit_price = price
            exit_reason = reason
            exit_date = candle_date
            holding_days = idx
            break

        if idx >= policy["max_days"]:
            exit_price = close_price
            exit_reason = "time_exit"
            exit_date = candle_date
            holding_days = idx
            break

    if exit_price is None:
        last = candles[-1]
        exit_price = safe_float(last.get("close"))
        exit_reason = "last_available_exit"
        exit_date = safe_date_text(last.get("date"))
        holding_days = len(candles)

    gross_return = (exit_price - entry_price) / entry_price * 100.0 if entry_price > 0 else 0.0
    net_return = calc_net_return(
        entry_price=entry_price,
        exit_price=exit_price,
        buy_fee_pct=args.buy_fee_pct,
        sell_fee_pct=args.sell_fee_pct,
        tax_pct=args.tax_pct,
        slippage_pct=args.slippage_pct,
    )
    max_intra = (max_high - entry_price) / entry_price * 100.0 if entry_price > 0 else 0.0
    min_intra = (min_low - entry_price) / entry_price * 100.0 if entry_price > 0 else 0.0

    result["exit_date"] = exit_date
    result["exit_price"] = exit_price
    result["exit_reason"] = exit_reason
    result["holding_days"] = holding_days
    result["gross_return"] = gross_return
    result["net_return"] = net_return
    result["weighted_net_return"] = net_return * policy["position_pct"] / 100.0
    result["max_intratrade_return"] = max_intra
    result["min_intratrade_return"] = min_intra
    return result


def calc_stats(rows):
    count = len(rows)
    if count == 0:
        return {
            "cnt": 0, "traded": 0, "win_rate": 0.0, "avg_net": 0.0, "avg_weighted": 0.0,
            "avg_gross": 0.0, "avg_hold": 0.0, "target_rate": 0.0, "stop_rate": 0.0,
            "time_rate": 0.0, "avg_max_intra": 0.0, "avg_min_intra": 0.0,
            "best_net": 0.0, "worst_net": 0.0,
        }

    traded = [r for r in rows if int(r.get("trade_enabled", 0)) == 1 and r.get("exit_reason") not in ("disabled", "no_future_candles")]
    traded_count = len(traded)
    if traded_count == 0:
        return {
            "cnt": count, "traded": 0, "win_rate": 0.0, "avg_net": 0.0, "avg_weighted": 0.0,
            "avg_gross": 0.0, "avg_hold": 0.0, "target_rate": 0.0, "stop_rate": 0.0,
            "time_rate": 0.0, "avg_max_intra": 0.0, "avg_min_intra": 0.0,
            "best_net": 0.0, "worst_net": 0.0,
        }

    net_vals = [safe_float(r.get("net_return")) for r in traded]
    weighted_vals = [safe_float(r.get("weighted_net_return")) for r in traded]
    gross_vals = [safe_float(r.get("gross_return")) for r in traded]
    hold_vals = [safe_float(r.get("holding_days")) for r in traded]
    max_vals = [safe_float(r.get("max_intratrade_return")) for r in traded]
    min_vals = [safe_float(r.get("min_intratrade_return")) for r in traded]

    wins = sum(1 for v in net_vals if v > 0)
    targets = sum(1 for r in traded if str(r.get("exit_reason", "")).startswith("target"))
    stops = sum(1 for r in traded if str(r.get("exit_reason", "")).startswith("stop"))
    times = sum(1 for r in traded if r.get("exit_reason") in ("time_exit", "last_available_exit"))

    return {
        "cnt": count,
        "traded": traded_count,
        "win_rate": wins / traded_count * 100.0,
        "avg_net": sum(net_vals) / traded_count,
        "avg_weighted": sum(weighted_vals) / traded_count,
        "avg_gross": sum(gross_vals) / traded_count,
        "avg_hold": sum(hold_vals) / traded_count,
        "target_rate": targets / traded_count * 100.0,
        "stop_rate": stops / traded_count * 100.0,
        "time_rate": times / traded_count * 100.0,
        "avg_max_intra": sum(max_vals) / traded_count,
        "avg_min_intra": sum(min_vals) / traded_count,
        "best_net": max(net_vals),
        "worst_net": min(net_vals),
    }


def print_stats(results):
    groups = defaultdict(list)
    for row in results:
        groups[row.get("grade_v21")].append(row)

    logger.info("")
    logger.info("=" * 150)
    logger.info("v2.1 등급별 실전형 시뮬레이션 결과")
    logger.info("=" * 150)
    logger.info(
        "%10s | %5s | %6s | %7s | %8s | %8s | %8s | %7s | %7s | %7s | %7s | %8s | %8s | %8s | %8s",
        "grade", "cnt", "trade", "win%", "avgNet", "wNet", "avgGross", "hold", "target", "stop", "time", "maxIntra", "minIntra", "best", "worst",
    )
    logger.info("-" * 150)

    for grade in GRADE_ORDER:
        stats = calc_stats(groups.get(grade, []))
        logger.info(
            "%10s | %5d | %6d | %6.1f%% | %+7.2f%% | %+7.2f%% | %+7.2f%% | %6.2f | %6.1f%% | %6.1f%% | %6.1f%% | %+7.2f%% | %+7.2f%% | %+7.2f%% | %+7.2f%%",
            grade,
            stats["cnt"], stats["traded"], stats["win_rate"], stats["avg_net"], stats["avg_weighted"],
            stats["avg_gross"], stats["avg_hold"], stats["target_rate"], stats["stop_rate"], stats["time_rate"],
            stats["avg_max_intra"], stats["avg_min_intra"], stats["best_net"], stats["worst_net"],
        )


def print_top_bottom(results, grade, top_n):
    rows = [r for r in results if r.get("grade_v21") == grade and int(r.get("trade_enabled", 0)) == 1]
    if not rows:
        return

    rows.sort(key=lambda r: safe_float(r.get("net_return")), reverse=True)
    logger.info("")
    logger.info("[%s] net_return 상위 %d", grade, top_n)
    for i, r in enumerate(rows[:top_n], 1):
        logger.info(
            "%3d. [%s] %-18s(%s) net=%+7.2f%% gross=%+7.2f%% reason=%s hold=%s entry=%.2f exit=%.2f ret_max=%+7.2f%%",
            i, safe_date_text(r.get("search_date")), str(r.get("name"))[:18], r.get("code"),
            safe_float(r.get("net_return")), safe_float(r.get("gross_return")), r.get("exit_reason"),
            r.get("holding_days"), safe_float(r.get("entry_price")), safe_float(r.get("exit_price")), safe_float(r.get("ret_max")),
        )

    logger.info("")
    logger.info("[%s] net_return 하위 %d", grade, top_n)
    for i, r in enumerate(list(reversed(rows[-top_n:])), 1):
        logger.info(
            "%3d. [%s] %-18s(%s) net=%+7.2f%% gross=%+7.2f%% reason=%s hold=%s entry=%.2f exit=%.2f ret_max=%+7.2f%%",
            i, safe_date_text(r.get("search_date")), str(r.get("name"))[:18], r.get("code"),
            safe_float(r.get("net_return")), safe_float(r.get("gross_return")), r.get("exit_reason"),
            r.get("holding_days"), safe_float(r.get("entry_price")), safe_float(r.get("exit_price")), safe_float(r.get("ret_max")),
        )


def export_csv(results, path_text):
    if not path_text:
        return

    path = Path(path_text)
    if not path.is_absolute():
        path = Path(__file__).parent / path
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "id", "condition_name", "search_date", "code", "name", "market",
        "grade", "grade_v21", "strategy_v21", "s_score", "e_score",
        "trigger_path", "close_price", "day_return", "bb_width", "vol_ratio_20",
        "entry_date", "entry_price", "exit_date", "exit_price", "exit_reason",
        "holding_days", "policy_target", "policy_stop", "policy_max_days", "position_pct",
        "gross_return", "net_return", "weighted_net_return", "max_intratrade_return", "min_intratrade_return",
        "ret_1w", "ret_2w", "ret_3w", "ret_1m", "ret_max", "candle_count",
    ]

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            writer.writerow(row)
    logger.info("CSV 저장 완료: %s", path)


def parse_args():
    parser = argparse.ArgumentParser(description="grade_v21별 목표/손절/보유기간 실전형 시뮬레이션")
    parser.add_argument("--condition-name", default=BacktestConfig.CONDITION_NAME)
    parser.add_argument("--start", default=None, help="search_date 시작일 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="search_date 종료일 YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=None, help="테스트용 최대 처리 건수")
    parser.add_argument("--include-disabled", action="store_true", help="C_WATCH/C_BAD/D까지 결과에 포함")
    parser.add_argument("--same-day-policy", choices=["stop_first", "target_first", "open_proximity"], default="stop_first")
    parser.add_argument("--buy-fee-pct", type=float, default=0.015)
    parser.add_argument("--sell-fee-pct", type=float, default=0.015)
    parser.add_argument("--tax-pct", type=float, default=0.30)
    parser.add_argument("--slippage-pct", type=float, default=0.10)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--export-csv", default=None)
    parser.add_argument("--dry-run", action="store_true", help="처리 건수/정책만 확인. 계산은 수행합니다. DB 수정 없음")
    return parser.parse_args()


def main():
    args = parse_args()

    logger.info("=" * 150)
    logger.info("ScoringModel v2.1 등급별 실전형 매매 시뮬레이션 시작")
    logger.info("condition_name=%s start=%s end=%s", args.condition_name, args.start, args.end)
    logger.info("same_day_policy=%s cost=buy %.3f%% sell %.3f%% tax %.3f%% slippage %.3f%%x2", args.same_day_policy, args.buy_fee_pct, args.sell_fee_pct, args.tax_pct, args.slippage_pct)
    logger.info("=" * 150)

    backtest_conn = get_conn(DBConfig.BACKTEST_DB)
    stock_conn = get_conn(DBConfig.STOCK_DATA_DB)
    try:
        rows = fetch_scan_rows(
            conn=backtest_conn,
            condition_name=args.condition_name,
            start_date=args.start,
            end_date=args.end,
            enabled_only=(not args.include_disabled),
            limit=args.limit,
        )
        logger.info("scan_result 로딩 완료: %s건", len(rows))

        results = []
        for idx, row in enumerate(rows, 1):
            grade = row.get("grade_v21")
            policy = POLICIES.get(grade, POLICIES["D"])
            candles = fetch_future_candles(
                conn=stock_conn,
                code=row.get("code"),
                search_date=safe_date_text(row.get("search_date")),
                max_days=policy["max_days"],
            )
            results.append(simulate_one(row, candles, args))
            if idx % 500 == 0:
                logger.info("진행: %s / %s", idx, len(rows))

    finally:
        backtest_conn.close()
        stock_conn.close()

    print_stats(results)
    for grade in ["A1", "A2", "B1", "B2", "C_HOT", "C_FAST"]:
        print_top_bottom(results, grade, args.top)

    export_csv(results, args.export_csv)
    logger.info("시뮬레이션 완료. DB는 수정하지 않았습니다.")


if __name__ == "__main__":
    main()
