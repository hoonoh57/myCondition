"""
Top-N 포트폴리오 압축 전략 시뮬레이터 v2.1/v2.2

목적:
    조건식 포착 종목 전체를 매매하지 않고, 매일 가장 확신도 높은 1~N개만 선택하여
    실제 포트폴리오 손익비를 검증합니다.

핵심 철학:
    - 많이 매매하지 않는다.
    - 매일 후보 중 최상위 1~2개 또는 제한된 N개만 선택한다.
    - 동시보유 수를 제한한다.
    - ret_max가 아니라 실제 일봉 경로의 target/stop/time_exit 결과로 평가한다.
    - C_HOT은 기본적으로 장중 확인 후보로만 두고, 자동 포트폴리오 매매 대상에서 제외한다.

기본 매매 가능 등급:
    A1, A2, B1, B2, C_FAST

기본 정책:
    A1      +30 / -5 / 20일
    A2      +20 / -4 / 20일
    B1      +25 / -5 / 10일
    B2      +15 / -4 / 10일
    C_FAST  +15 / -6 / 2일

Usage:
    python simulate_topn_portfolio_v21.py --top-n 1 --max-positions 1
    python simulate_topn_portfolio_v21.py --top-n 2 --max-positions 2
    python simulate_topn_portfolio_v21.py --top-n 3 --max-positions 2
    python simulate_topn_portfolio_v21.py --rank-mode grade_score
    python simulate_topn_portfolio_v21.py --export-csv outputs/reports/topn_portfolio_v21.csv

DB 수정 없음. 결과는 로그/CSV만 생성.
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
            LOG_DIR / f"simulate_topn_portfolio_v21_{datetime.now():%Y%m%d_%H%M%S}.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


TRADE_GRADES = ["A1", "A2", "B1", "B2", "C_FAST"]
GRADE_PRIORITY = {
    "A1": 100,
    "C_FAST": 92,
    "B1": 90,
    "A2": 82,
    "B2": 70,
    "C_HOT": 50,
    "C_WATCH": 20,
    "C_BAD": 0,
    "D": 0,
}


POLICIES = {
    "A1": {"target": 30.0, "stop": -5.0, "max_days": 20, "weight": 1.00, "enabled": True},
    "A2": {"target": 20.0, "stop": -4.0, "max_days": 20, "weight": 0.80, "enabled": True},
    "B1": {"target": 25.0, "stop": -5.0, "max_days": 10, "weight": 0.60, "enabled": True},
    "B2": {"target": 15.0, "stop": -4.0, "max_days": 10, "weight": 0.50, "enabled": True},
    "C_FAST": {"target": 15.0, "stop": -6.0, "max_days": 2, "weight": 0.30, "enabled": True},
}


SCAN_COLUMNS = [
    "id", "condition_name", "search_date", "code", "name", "market",
    "trigger_path", "close_price", "volume", "day_return", "trade_value",
    "bb_width", "vol_ratio_20", "ret_1w", "ret_2w", "ret_3w", "ret_1m", "ret_max",
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


def build_where(condition_name, start_date, end_date, include_c_hot):
    clauses = ["score_version_v21 = '2.1'", "grade_v21 IS NOT NULL"]
    params = []

    grades = list(TRADE_GRADES)
    if include_c_hot:
        grades.append("C_HOT")
    placeholders = ",".join(["%s"] * len(grades))
    clauses.append(f"grade_v21 IN ({placeholders})")
    params.extend(grades)

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


def fetch_candidates(conn, condition_name, start_date, end_date, include_c_hot):
    where_sql, params = build_where(condition_name, start_date, end_date, include_c_hot)
    sql = f"""
        SELECT {', '.join(SCAN_COLUMNS)}
        FROM scan_result
        {where_sql}
        ORDER BY search_date, grade_v21, code, id
    """
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


def net_return(entry_price, exit_price, args):
    if entry_price <= 0:
        return 0.0
    gross = (exit_price - entry_price) / entry_price * 100.0
    cost = args.buy_fee_pct + args.sell_fee_pct + args.tax_pct + (args.slippage_pct * 2.0)
    return gross - cost


def simulate_trade(row, candles, args):
    grade = row.get("grade_v21")
    policy = POLICIES.get(grade)
    if policy is None:
        return None

    entry_price = safe_float(row.get("close_price"))
    if entry_price <= 0 or not candles:
        return None

    target = policy["target"]
    stop = policy["stop"]
    max_days = policy["max_days"]
    target_price = entry_price * (1.0 + target / 100.0)
    stop_price = entry_price * (1.0 + stop / 100.0)

    max_high = entry_price
    min_low = entry_price
    exit_price = None
    exit_reason = None
    exit_date = None
    holding_days = 0

    usable = candles[:max_days]
    for idx, candle in enumerate(usable, 1):
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

        if idx >= max_days:
            exit_price = close_price
            exit_reason = "time_exit"
            exit_date = candle_date
            holding_days = idx
            break

    if exit_price is None:
        last = usable[-1]
        exit_price = safe_float(last.get("close"))
        exit_reason = "last_available_exit"
        exit_date = safe_date_text(last.get("date"))
        holding_days = len(usable)

    gross_return = (exit_price - entry_price) / entry_price * 100.0
    nr = net_return(entry_price, exit_price, args)
    return {
        "id": row.get("id"),
        "code": row.get("code"),
        "name": row.get("name"),
        "entry_date": safe_date_text(row.get("search_date")),
        "entry_price": entry_price,
        "exit_date": exit_date,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "holding_days": holding_days,
        "grade_v21": grade,
        "s_score": row.get("s_score"),
        "e_score": row.get("e_score"),
        "rank_score": row.get("rank_score"),
        "target": target,
        "stop": stop,
        "max_days": max_days,
        "gross_return": gross_return,
        "net_return": nr,
        "max_intratrade_return": (max_high - entry_price) / entry_price * 100.0,
        "min_intratrade_return": (min_low - entry_price) / entry_price * 100.0,
        "ret_max": row.get("ret_max"),
        "day_return": row.get("day_return"),
        "trade_value": row.get("trade_value"),
        "vol_ratio_20": row.get("vol_ratio_20"),
        "bb_width": row.get("bb_width"),
        "trigger_path": row.get("trigger_path"),
    }


def rank_score(row, rank_mode):
    grade = row.get("grade_v21")
    gp = float(GRADE_PRIORITY.get(grade, 0))
    s_score = safe_float(row.get("s_score"))
    e_score = safe_float(row.get("e_score"))
    day_return = safe_float(row.get("day_return"))
    vol_ratio = safe_float(row.get("vol_ratio_20"))
    bb_width = safe_float(row.get("bb_width"))
    trade_value = safe_float(row.get("trade_value"))
    ret_max = safe_float(row.get("ret_max"))

    if rank_mode == "grade_score":
        return gp * 1000.0 + s_score * 3.0 + e_score * 5.0 + min(day_return, 30.0) * 2.0 + min(vol_ratio, 20.0)
    if rank_mode == "explosion":
        return e_score * 10.0 + min(day_return, 30.0) * 8.0 + min(vol_ratio, 20.0) * 3.0 + min(bb_width, 80.0)
    if rank_mode == "stability":
        return s_score * 10.0 + gp * 20.0 - abs(day_return - 8.0) * 2.0
    if rank_mode == "liquidity":
        return gp * 100.0 + trade_value / 100000000.0 + e_score * 2.0
    if rank_mode == "oracle_retmax":
        return ret_max
    return gp * 1000.0 + s_score + e_score


def group_by_date(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[safe_date_text(row.get("search_date"))].append(row)
    return grouped


def select_daily_candidates(rows, args):
    selected = []
    grouped = group_by_date(rows)
    for search_date in sorted(grouped.keys()):
        items = grouped[search_date]
        for row in items:
            row["rank_score"] = rank_score(row, args.rank_mode)
        items.sort(key=lambda r: safe_float(r.get("rank_score")), reverse=True)
        selected.extend(items[:args.top_n])
    return selected


def is_position_open(position, current_date):
    exit_date = position.get("exit_date")
    if not exit_date:
        return False
    return str(exit_date) >= str(current_date)


def simulate_portfolio(selected_rows, stock_conn, args):
    trades = []
    open_positions = []

    for idx, row in enumerate(selected_rows, 1):
        current_date = safe_date_text(row.get("search_date"))
        open_positions = [p for p in open_positions if is_position_open(p, current_date)]

        if len(open_positions) >= args.max_positions:
            continue

        if args.no_duplicate_open:
            code = row.get("code")
            duplicate = False
            for p in open_positions:
                if p.get("code") == code:
                    duplicate = True
                    break
            if duplicate:
                continue

        grade = row.get("grade_v21")
        policy = POLICIES.get(grade)
        if policy is None:
            continue

        candles = fetch_future_candles(stock_conn, row.get("code"), current_date, policy["max_days"])
        trade = simulate_trade(row, candles, args)
        if trade is None:
            continue

        allocation = min(args.capital_per_trade_pct, 100.0 / max(1, args.max_positions))
        trade["allocation_pct"] = allocation
        trade["portfolio_return_contribution"] = trade["net_return"] * allocation / 100.0
        trades.append(trade)
        open_positions.append(trade)

        if idx % 500 == 0:
            logger.info("진행: selected %s / %s, trades=%s", idx, len(selected_rows), len(trades))

    return trades


def calc_stats(trades):
    cnt = len(trades)
    if cnt == 0:
        return {}
    net_vals = [safe_float(t.get("net_return")) for t in trades]
    contrib_vals = [safe_float(t.get("portfolio_return_contribution")) for t in trades]
    hold_vals = [safe_float(t.get("holding_days")) for t in trades]
    wins = sum(1 for v in net_vals if v > 0)
    targets = sum(1 for t in trades if str(t.get("exit_reason", "")).startswith("target"))
    stops = sum(1 for t in trades if str(t.get("exit_reason", "")).startswith("stop"))
    times = sum(1 for t in trades if t.get("exit_reason") in ("time_exit", "last_available_exit"))

    by_grade = defaultdict(list)
    by_month = defaultdict(float)
    for t in trades:
        by_grade[t.get("grade_v21")].append(t)
        by_month[str(t.get("entry_date"))[:7]] += safe_float(t.get("portfolio_return_contribution"))

    return {
        "cnt": cnt,
        "win_rate": wins / cnt * 100.0,
        "avg_net": sum(net_vals) / cnt,
        "sum_contribution": sum(contrib_vals),
        "avg_contribution": sum(contrib_vals) / cnt,
        "target_rate": targets / cnt * 100.0,
        "stop_rate": stops / cnt * 100.0,
        "time_rate": times / cnt * 100.0,
        "avg_hold": sum(hold_vals) / cnt,
        "best_net": max(net_vals),
        "worst_net": min(net_vals),
        "by_grade": by_grade,
        "by_month": by_month,
    }


def print_summary(trades, args):
    stats = calc_stats(trades)
    logger.info("")
    logger.info("=" * 150)
    logger.info("Top-N 포트폴리오 압축 전략 결과")
    logger.info("=" * 150)
    logger.info("rank_mode=%s top_n=%s max_positions=%s same_day_policy=%s", args.rank_mode, args.top_n, args.max_positions, args.same_day_policy)
    if not stats:
        logger.info("거래 없음")
        return
    logger.info("trades=%s win=%.1f%% avgNet=%+.2f%% sumPortfolioContribution=%+.2f%% avgContribution=%+.3f%% target=%.1f%% stop=%.1f%% time=%.1f%% hold=%.2f best=%+.2f%% worst=%+.2f%%", stats["cnt"], stats["win_rate"], stats["avg_net"], stats["sum_contribution"], stats["avg_contribution"], stats["target_rate"], stats["stop_rate"], stats["time_rate"], stats["avg_hold"], stats["best_net"], stats["worst_net"])

    logger.info("")
    logger.info("등급별 거래 결과")
    for grade in ["A1", "A2", "B1", "B2", "C_FAST", "C_HOT"]:
        items = stats["by_grade"].get(grade, [])
        if not items:
            continue
        s = calc_stats(items)
        logger.info("%8s trades=%4d win=%5.1f%% avgNet=%+6.2f%% sumContrib=%+7.2f%% target=%5.1f%% stop=%5.1f%%", grade, s["cnt"], s["win_rate"], s["avg_net"], s["sum_contribution"], s["target_rate"], s["stop_rate"])

    logger.info("")
    logger.info("월별 포트폴리오 기여도")
    for month in sorted(stats["by_month"].keys()):
        logger.info("%s %+7.2f%%", month, stats["by_month"][month])


def print_top_bottom(trades, top_n):
    rows = list(trades)
    rows.sort(key=lambda x: safe_float(x.get("net_return")), reverse=True)
    logger.info("")
    logger.info("상위 거래 %s", top_n)
    for i, t in enumerate(rows[:top_n], 1):
        logger.info("%3d. [%s] %-18s(%s) %s net=%+7.2f%% contrib=%+7.3f%% reason=%s rank=%.2f", i, t.get("entry_date"), str(t.get("name"))[:18], t.get("code"), t.get("grade_v21"), safe_float(t.get("net_return")), safe_float(t.get("portfolio_return_contribution")), t.get("exit_reason"), safe_float(t.get("rank_score")))
    logger.info("")
    logger.info("하위 거래 %s", top_n)
    for i, t in enumerate(list(reversed(rows[-top_n:])), 1):
        logger.info("%3d. [%s] %-18s(%s) %s net=%+7.2f%% contrib=%+7.3f%% reason=%s rank=%.2f", i, t.get("entry_date"), str(t.get("name"))[:18], t.get("code"), t.get("grade_v21"), safe_float(t.get("net_return")), safe_float(t.get("portfolio_return_contribution")), t.get("exit_reason"), safe_float(t.get("rank_score")))


def export_csv(trades, path_text):
    if not path_text:
        return
    path = Path(path_text)
    if not path.is_absolute():
        path = Path(__file__).parent / path
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "entry_date", "exit_date", "code", "name", "grade_v21", "s_score", "e_score", "rank_score",
        "entry_price", "exit_price", "exit_reason", "holding_days", "target", "stop", "max_days",
        "gross_return", "net_return", "allocation_pct", "portfolio_return_contribution",
        "max_intratrade_return", "min_intratrade_return", "ret_max", "day_return", "trade_value", "vol_ratio_20", "bb_width", "trigger_path",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for t in trades:
            writer.writerow(t)
    logger.info("CSV 저장 완료: %s", path)


def parse_args():
    parser = argparse.ArgumentParser(description="매일 Top-N 후보만 선택하는 포트폴리오 압축 전략 시뮬레이션")
    parser.add_argument("--condition-name", default=BacktestConfig.CONDITION_NAME)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--top-n", type=int, default=1)
    parser.add_argument("--max-positions", type=int, default=1)
    parser.add_argument("--capital-per-trade-pct", type=float, default=100.0)
    parser.add_argument("--rank-mode", choices=["grade_score", "explosion", "stability", "liquidity", "oracle_retmax"], default="grade_score")
    parser.add_argument("--include-c-hot", action="store_true", help="C_HOT을 랭킹 후보에 포함. 기본은 제외")
    parser.add_argument("--no-duplicate-open", action="store_true", default=True)
    parser.add_argument("--same-day-policy", choices=["stop_first", "target_first", "open_proximity"], default="stop_first")
    parser.add_argument("--buy-fee-pct", type=float, default=0.015)
    parser.add_argument("--sell-fee-pct", type=float, default=0.015)
    parser.add_argument("--tax-pct", type=float, default=0.30)
    parser.add_argument("--slippage-pct", type=float, default=0.10)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--export-csv", default=None)
    return parser.parse_args()


def main():
    args = parse_args()
    logger.info("=" * 150)
    logger.info("Top-N 포트폴리오 압축 전략 시뮬레이션 시작")
    logger.info("top_n=%s max_positions=%s rank_mode=%s include_c_hot=%s", args.top_n, args.max_positions, args.rank_mode, args.include_c_hot)
    logger.info("=" * 150)

    backtest_conn = get_conn(DBConfig.BACKTEST_DB)
    stock_conn = get_conn(DBConfig.STOCK_DATA_DB)
    try:
        rows = fetch_candidates(backtest_conn, args.condition_name, args.start, args.end, args.include_c_hot)
        logger.info("후보 로딩 완료: %s건", len(rows))
        selected = select_daily_candidates(rows, args)
        logger.info("일별 Top-%s 선택 완료: %s건", args.top_n, len(selected))
        trades = simulate_portfolio(selected, stock_conn, args)
    finally:
        backtest_conn.close()
        stock_conn.close()

    print_summary(trades, args)
    print_top_bottom(trades, args.top)
    export_csv(trades, args.export_csv)
    logger.info("완료. DB는 수정하지 않았습니다.")


if __name__ == "__main__":
    main()
