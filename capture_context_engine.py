"""
Capture Context Engine

목적:
    조건검색식이 이미 상승 후보를 잘 포착한다는 전제하에,
    포착된 종목의 수익 회수 가능성을 높이기 위한 보조 컨텍스트 점수를 계산합니다.

핵심 방향:
    1. 조건식 포착 = 1차 후보
    2. 수급/테마/뉴스/거래대금/차트 위치 = 우선순위 보정
    3. 포착 자체보다 포착 이후의 대응 품질을 개선
    4. 패턴별로 다른 대응 가이드를 제공

가용 데이터만 사용:
    - scan_result 기본 컬럼은 항상 사용
    - 프로그램/기관/외국인/테마/뉴스 관련 컬럼은 존재할 때만 자동 반영
    - 해당 컬럼이 없으면 0점 처리하고 분석은 계속 진행

Usage:
    python capture_context_engine.py
    python capture_context_engine.py --start 2026-01-01 --end 2026-05-20
    python capture_context_engine.py --export-csv outputs/reports/capture_context.csv

DB 수정 없음.
"""
import argparse
import csv
import logging
import math
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import pymysql

from config import BacktestConfig, DBConfig


LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
REPORT_DIR = Path(__file__).parent / "outputs" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"capture_context_engine_{datetime.now():%Y%m%d_%H%M%S}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


BASE_COLUMNS = [
    "id", "condition_name", "search_date", "code", "name", "market",
    "trigger_path", "close_price", "volume", "trade_value", "day_return",
    "ma60_200_dist", "rsi14", "bb_width", "vol_ratio_20",
    "ma60_slope_up", "ma200_slope_up",
    "ret_1w", "ret_2w", "ret_3w", "ret_1m", "ret_max", "max_high_date",
    "s_score", "e_score", "grade", "grade_v21", "strategy_v21",
]

OPTIONAL_SCAN_COLUMNS = [
    "program_net_buy", "program_net_buy_amt", "program_buy_ratio",
    "institution_net_buy", "institution_net_buy_amt",
    "foreign_net_buy", "foreign_net_buy_amt",
    "theme_name", "theme_score", "theme_rank", "theme_strength",
    "news_count", "news_score", "news_sentiment", "news_keyword_score",
    "sector_name", "sector_rank", "sector_strength",
    "market_index_return", "kospi_return", "kosdaq_return",
]

GRADE_BASE_SCORE = {
    "A1": 35.0,
    "A2": 28.0,
    "B1": 26.0,
    "B2": 18.0,
    "C_FAST": 22.0,
    "C_HOT": 12.0,
    "C_WATCH": 5.0,
    "C_BAD": -10.0,
    "D": -15.0,
}

PATTERN_BASE_SCORE = {
    "A_EVENT_LIMITLIKE": 22.0,
    "C_D_ONLY_60GC": 20.0,
    "B_D_E_GOLDEN_CORE": 18.0,
    "D_E_ONLY_200GC": 12.0,
    "E_RSI_E_MIXED": 8.0,
    "F_RSI_D_MIXED": 5.0,
    "H_COMPLEX_CDE": 5.0,
    "G_RSI_ONLY": -8.0,
    "Z_OTHER": 0.0,
}


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
        value_float = float(value)
        if math.isnan(value_float):
            return default
        return value_float
    except Exception:
        return default


def safe_date_text(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    return str(value)[:10]


def get_existing_columns(conn, table_name):
    sql = """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (table_name,))
        rows = cur.fetchall()
    return set(str(row["column_name"]) for row in rows)


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


def fetch_rows(conn, args):
    existing = get_existing_columns(conn, "scan_result")
    columns = []
    for col in BASE_COLUMNS:
        if col in existing:
            columns.append(col)
    optional_used = []
    for col in OPTIONAL_SCAN_COLUMNS:
        if col in existing:
            columns.append(col)
            optional_used.append(col)
    logger.info("optional scan_result columns used: %s", optional_used if optional_used else "none")

    clauses = ["condition_name = %s"]
    params = [args.condition_name]
    if args.start:
        clauses.append("search_date >= %s")
        params.append(args.start)
    if args.end:
        clauses.append("search_date <= %s")
        params.append(args.end)
    if args.grade:
        clauses.append("grade_v21 = %s")
        params.append(args.grade)
    sql = f"""
        SELECT {', '.join(columns)}
        FROM scan_result
        WHERE {' AND '.join(clauses)}
        ORDER BY search_date, code, id
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return rows, optional_used


def score_liquidity(row):
    trade_value = safe_float(row.get("trade_value"))
    vol_ratio = safe_float(row.get("vol_ratio_20"))
    day_return = safe_float(row.get("day_return"))
    score = 0.0
    if trade_value >= 100000000000:
        score += 12.0
    elif trade_value >= 30000000000:
        score += 8.0
    elif trade_value >= 10000000000:
        score += 5.0
    if vol_ratio >= 20:
        score += 10.0
    elif vol_ratio >= 10:
        score += 7.0
    elif vol_ratio >= 5:
        score += 4.0
    if 3.0 <= day_return <= 18.0:
        score += 6.0
    elif day_return > 25.0:
        score -= 5.0
    return score


def score_supply_demand(row):
    score = 0.0
    program_amt = safe_float(row.get("program_net_buy_amt"), safe_float(row.get("program_net_buy")))
    inst_amt = safe_float(row.get("institution_net_buy_amt"), safe_float(row.get("institution_net_buy")))
    foreign_amt = safe_float(row.get("foreign_net_buy_amt"), safe_float(row.get("foreign_net_buy")))
    program_ratio = safe_float(row.get("program_buy_ratio"))
    if program_amt > 0:
        score += min(10.0, program_amt / 1000000000.0)
    if inst_amt > 0:
        score += min(10.0, inst_amt / 1000000000.0)
    if foreign_amt > 0:
        score += min(8.0, foreign_amt / 1000000000.0)
    if program_ratio >= 20:
        score += 6.0
    elif program_ratio >= 10:
        score += 3.0
    return score


def score_theme_news(row):
    score = 0.0
    theme_score = safe_float(row.get("theme_score"), safe_float(row.get("theme_strength")))
    news_score = safe_float(row.get("news_score"), safe_float(row.get("news_keyword_score")))
    news_count = safe_float(row.get("news_count"))
    sentiment = safe_float(row.get("news_sentiment"))
    sector_strength = safe_float(row.get("sector_strength"))
    score += min(12.0, theme_score)
    score += min(10.0, news_score)
    if news_count >= 5:
        score += 5.0
    elif news_count >= 2:
        score += 2.0
    if sentiment > 0:
        score += min(5.0, sentiment * 5.0)
    score += min(8.0, sector_strength)
    return score


def score_market_phase(row):
    score = 0.0
    kospi = safe_float(row.get("kospi_return"), safe_float(row.get("market_index_return")))
    kosdaq = safe_float(row.get("kosdaq_return"))
    market = str(row.get("market") or "")
    ref = kosdaq if "KOSDAQ" in market.upper() else kospi
    if ref >= 1.0:
        score += 6.0
    elif ref >= 0.3:
        score += 3.0
    elif ref <= -1.0:
        score -= 6.0
    elif ref <= -0.3:
        score -= 3.0
    return score


def score_chart_context(row):
    score = 0.0
    s_score = safe_float(row.get("s_score"))
    e_score = safe_float(row.get("e_score"))
    bb_width = safe_float(row.get("bb_width"))
    ma_dist = safe_float(row.get("ma60_200_dist"))
    rsi = safe_float(row.get("rsi14"))
    if s_score >= 70:
        score += 8.0
    elif s_score >= 50:
        score += 4.0
    if e_score >= 80:
        score += 8.0
    elif e_score >= 60:
        score += 4.0
    if 10.0 <= bb_width <= 40.0:
        score += 4.0
    elif bb_width > 60.0:
        score -= 3.0
    if -5.0 <= ma_dist <= 8.0:
        score += 4.0
    if rsi >= 85.0:
        score -= 4.0
    return score


def compute_context_score(row):
    grade = str(row.get("grade_v21") or "")
    pattern = pattern_group(row)
    grade_score = GRADE_BASE_SCORE.get(grade, 0.0)
    pattern_score = PATTERN_BASE_SCORE.get(pattern, 0.0)
    liquidity = score_liquidity(row)
    supply = score_supply_demand(row)
    theme_news = score_theme_news(row)
    market_phase = score_market_phase(row)
    chart = score_chart_context(row)
    total = grade_score + pattern_score + liquidity + supply + theme_news + market_phase + chart
    if pattern == "G_RSI_ONLY":
        total -= 10.0
    if grade in ("C_BAD", "D"):
        total -= 20.0
    return {
        "pattern_group": pattern,
        "context_score": total,
        "grade_score": grade_score,
        "pattern_score": pattern_score,
        "liquidity_score": liquidity,
        "supply_demand_score": supply,
        "theme_news_score": theme_news,
        "market_phase_score": market_phase,
        "chart_context_score": chart,
    }


def classify_context(row, score_parts):
    grade = str(row.get("grade_v21") or "")
    pattern = score_parts["pattern_group"]
    score = score_parts["context_score"]
    supply = score_parts["supply_demand_score"]
    theme_news = score_parts["theme_news_score"]
    if grade in ("D", "C_BAD"):
        return "SKIP"
    if pattern == "G_RSI_ONLY" and score < 70:
        return "SKIP_RSI_ONLY"
    if score >= 90:
        return "PRIORITY_CONTEXT"
    if score >= 75:
        return "STRONG_CONTEXT"
    if score >= 60:
        return "INTRADAY_CONFIRM"
    if supply > 0 or theme_news > 0:
        return "SUPPLY_THEME_WATCH"
    return "LOW_PRIORITY"


def enrich_rows(rows):
    enriched = []
    for row in rows:
        item = dict(row)
        parts = compute_context_score(item)
        item.update(parts)
        item["context_action"] = classify_context(item, parts)
        enriched.append(item)
    enriched.sort(key=lambda x: (safe_date_text(x.get("search_date")), safe_float(x.get("context_score"))), reverse=True)
    return enriched


def print_summary(rows):
    logger.info("=" * 140)
    logger.info("Capture Context Summary")
    logger.info("=" * 140)
    logger.info("rows=%s", len(rows))
    by_action = defaultdict(list)
    by_pattern = defaultdict(list)
    by_grade = defaultdict(list)
    for row in rows:
        by_action[row.get("context_action")].append(row)
        by_pattern[row.get("pattern_group")].append(row)
        by_grade[row.get("grade_v21")].append(row)
    logger.info("\n[Action]")
    for key, items in sorted(by_action.items(), key=lambda x: len(x[1]), reverse=True):
        avg_score = sum(safe_float(r.get("context_score")) for r in items) / len(items)
        avg_max = sum(safe_float(r.get("ret_max")) for r in items) / len(items)
        logger.info("%-24s cnt=%5d avgScore=%7.2f avgMax=%+7.2f%%", key, len(items), avg_score, avg_max)
    logger.info("\n[Top 30 Context Candidates]")
    for idx, row in enumerate(rows[:30], 1):
        logger.info(
            "%2d. [%s] %-18s(%s) %-8s %-24s score=%7.2f action=%s retMax=%+7.2f%% S=%s E=%s TV=%.0f VR=%.2f",
            idx,
            safe_date_text(row.get("search_date")),
            str(row.get("name"))[:18],
            row.get("code"),
            row.get("grade_v21"),
            row.get("pattern_group"),
            safe_float(row.get("context_score")),
            row.get("context_action"),
            safe_float(row.get("ret_max")),
            row.get("s_score"),
            row.get("e_score"),
            safe_float(row.get("trade_value")),
            safe_float(row.get("vol_ratio_20")),
        )


def export_csv(rows, path_text):
    if not path_text:
        return
    path = Path(path_text)
    if not path.is_absolute():
        path = Path(__file__).parent / path
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "search_date", "code", "name", "market", "grade_v21", "pattern_group", "context_score", "context_action",
        "grade_score", "pattern_score", "liquidity_score", "supply_demand_score", "theme_news_score", "market_phase_score", "chart_context_score",
        "trigger_path", "close_price", "day_return", "trade_value", "vol_ratio_20", "bb_width", "rsi14", "ma60_200_dist",
        "s_score", "e_score", "ret_1w", "ret_2w", "ret_3w", "ret_1m", "ret_max", "max_high_date",
        "program_net_buy", "program_net_buy_amt", "program_buy_ratio", "institution_net_buy", "institution_net_buy_amt", "foreign_net_buy", "foreign_net_buy_amt",
        "theme_name", "theme_score", "theme_rank", "theme_strength", "news_count", "news_score", "news_sentiment", "news_keyword_score", "sector_name", "sector_rank", "sector_strength",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    logger.info("CSV saved: %s", path)


def parse_args():
    parser = argparse.ArgumentParser(description="조건식 포착 종목에 수익 회수용 보조 컨텍스트 점수를 부여합니다.")
    parser.add_argument("--condition-name", default=BacktestConfig.CONDITION_NAME)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--grade", default=None)
    parser.add_argument("--export-csv", default="outputs/reports/capture_context.csv")
    return parser.parse_args()


def main():
    args = parse_args()
    logger.info("=" * 140)
    logger.info("Capture Context Engine start")
    logger.info("condition=%s start=%s end=%s grade=%s", args.condition_name, args.start, args.end, args.grade)
    logger.info("=" * 140)
    conn = get_conn(DBConfig.BACKTEST_DB)
    try:
        rows, _optional = fetch_rows(conn, args)
    finally:
        conn.close()
    enriched = enrich_rows(rows)
    print_summary(enriched)
    export_csv(enriched, args.export_csv)
    logger.info("done. DB not modified.")


if __name__ == "__main__":
    main()
