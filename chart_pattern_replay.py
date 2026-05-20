"""
패턴 정합성 차트 리플레이 생성기

목적:
    analyze_pattern_consistency.py 결과에서 확인된 패턴별 대표 성공/실패 사례를
    일봉 차트로 저장하여, 조건식/패턴/등급이 실제 차트 구조와 일치하는지 눈으로 검증합니다.

출력:
    outputs/charts/pattern_replay/*.png
    outputs/reports/pattern_replay_manifest.csv

차트 구성:
    - 종가 라인
    - MA60 / MA200
    - 포착일 vertical line
    - ret_max 최고가 발생일 vertical line
    - 진입가 / ret_max 고가 / 기본 target / stop 라인
    - 거래량 하단 패널
    - 제목에 패턴, grade_v21, S/E, ret_max, ret_1m 표시

Usage:
    python chart_pattern_replay.py
    python chart_pattern_replay.py --mode top_bottom --per-group 5
    python chart_pattern_replay.py --mode specific --code 065500 --date 2024-11-25
    python chart_pattern_replay.py --pattern A_EVENT_LIMITLIKE --per-group 10
    python chart_pattern_replay.py --grade C_HOT --per-group 10

DB 수정 없음.
"""
import argparse
import csv
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import pymysql

from config import BacktestConfig, DBConfig


LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
OUT_DIR = Path(__file__).parent / "outputs" / "charts" / "pattern_replay"
OUT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR = Path(__file__).parent / "outputs" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"chart_pattern_replay_{datetime.now():%Y%m%d_%H%M%S}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


SCAN_COLUMNS = [
    "id", "condition_name", "search_date", "code", "name", "market",
    "trigger_path", "close_price", "volume", "day_return", "bb_width", "vol_ratio_20",
    "ret_1w", "ret_2w", "ret_3w", "ret_1m", "ret_max", "max_high_date",
    "s_score", "e_score", "grade", "grade_v21", "strategy_v21",
]


POLICIES = {
    "A1": {"target": 30.0, "stop": -5.0},
    "A2": {"target": 20.0, "stop": -4.0},
    "B1": {"target": 25.0, "stop": -5.0},
    "B2": {"target": 15.0, "stop": -4.0},
    "C_FAST": {"target": 15.0, "stop": -6.0},
    "C_HOT": {"target": 8.0, "stop": -8.0},
    "C_WATCH": {"target": 10.0, "stop": -3.0},
    "C_BAD": {"target": 0.0, "stop": 0.0},
    "D": {"target": 0.0, "stop": 0.0},
}


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


def parse_date(value):
    text = safe_date_text(value)
    return datetime.strptime(text, "%Y-%m-%d").date()


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


def fetch_scan_rows(conn, args):
    clauses = []
    params = []

    if args.condition_name:
        clauses.append("condition_name = %s")
        params.append(args.condition_name)
    if args.start:
        clauses.append("search_date >= %s")
        params.append(args.start)
    if args.end:
        clauses.append("search_date <= %s")
        params.append(args.end)
    if args.grade:
        clauses.append("grade_v21 = %s")
        params.append(args.grade)
    if args.code:
        clauses.append("code = %s")
        params.append(args.code)
    if args.date:
        clauses.append("search_date = %s")
        params.append(args.date)

    where_sql = ""
    if clauses:
        where_sql = "WHERE " + " AND ".join(clauses)

    sql = f"""
        SELECT {', '.join(SCAN_COLUMNS)}
        FROM scan_result
        {where_sql}
        ORDER BY search_date, code, id
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    if args.pattern:
        rows = [row for row in rows if pattern_group(row) == args.pattern]
    return rows


def select_replay_rows(rows, mode, per_group):
    if mode == "specific":
        return rows[:1]

    grouped = {}
    for row in rows:
        pg = pattern_group(row)
        grouped.setdefault(pg, []).append(row)

    selected = []
    order = [p for p in PATTERN_ORDER if p in grouped]
    for pg in order:
        items = grouped[pg]
        items_sorted = sorted(items, key=lambda r: safe_float(r.get("ret_max")), reverse=True)
        selected.extend(items_sorted[:per_group])
        if mode == "top_bottom":
            selected.extend(items_sorted[-per_group:])
    return selected


def fetch_candles(conn, code, center_date, warmup_days, forward_days):
    start_date = center_date - timedelta(days=warmup_days)
    end_date = center_date + timedelta(days=forward_days)
    table_name = f"`{DBConfig.STOCK_DATA_DB}`.`daily_candles`"
    sql = f"""
        SELECT date, open, high, low, close, volume
        FROM {table_name}
        WHERE code = %s
          AND date >= %s
          AND date <= %s
        ORDER BY date ASC
    """
    with conn.cursor() as cur:
        cur.execute(sql, (code, start_date.isoformat(), end_date.isoformat()))
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["ma60"] = df["close"].rolling(60).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    return df


def sanitize_filename(text):
    keep = []
    for ch in str(text):
        if ch.isalnum() or ch in ("-", "_", "."):
            keep.append(ch)
        else:
            keep.append("_")
    return "".join(keep)


def draw_chart(row, candle_df, args):
    if candle_df.empty:
        return None

    code = row.get("code")
    name = row.get("name")
    search_date = safe_date_text(row.get("search_date"))
    max_high_date = safe_date_text(row.get("max_high_date"))
    pg = pattern_group(row)
    grade = row.get("grade_v21")
    entry_price = safe_float(row.get("close_price"))
    ret_max = safe_float(row.get("ret_max"))
    policy = POLICIES.get(grade, {"target": 0.0, "stop": 0.0})
    target_price = entry_price * (1.0 + policy["target"] / 100.0) if policy["target"] != 0 else None
    stop_price = entry_price * (1.0 + policy["stop"] / 100.0) if policy["stop"] != 0 else None
    max_price = entry_price * (1.0 + ret_max / 100.0)

    fig = plt.figure(figsize=(16, 9))
    gs = fig.add_gridspec(4, 1, height_ratios=[3, 0.05, 1, 0.1])
    ax = fig.add_subplot(gs[0, 0])
    axv = fig.add_subplot(gs[2, 0], sharex=ax)

    ax.plot(candle_df["date"], candle_df["close"], linewidth=1.4, label="Close")
    ax.plot(candle_df["date"], candle_df["ma60"], linewidth=1.1, label="MA60")
    ax.plot(candle_df["date"], candle_df["ma200"], linewidth=1.1, label="MA200")

    search_dt = pd.to_datetime(search_date)
    ax.axvline(search_dt, linestyle="--", linewidth=1.2, label="Signal")
    if max_high_date:
        ax.axvline(pd.to_datetime(max_high_date), linestyle=":", linewidth=1.2, label="MaxHigh")

    if entry_price > 0:
        ax.axhline(entry_price, linestyle="--", linewidth=0.9, label="Entry")
    if max_price > 0:
        ax.axhline(max_price, linestyle=":", linewidth=0.9, label="RetMaxPrice")
    if target_price:
        ax.axhline(target_price, linestyle="-.", linewidth=0.9, label="Target")
    if stop_price:
        ax.axhline(stop_price, linestyle="-.", linewidth=0.9, label="Stop")

    title = (
        f"{search_date} {code} {name} | {pg} | grade={grade} | "
        f"S={row.get('s_score')} E={row.get('e_score')} | "
        f"retMax={safe_float(row.get('ret_max')):.2f}% ret1m={safe_float(row.get('ret_1m')):.2f}% | "
        f"DR={safe_float(row.get('day_return')):.2f}% VR={safe_float(row.get('vol_ratio_20')):.2f} BW={safe_float(row.get('bb_width')):.2f}"
    )
    ax.set_title(title, fontsize=10)
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left", fontsize=8)

    axv.bar(candle_df["date"], candle_df["volume"], width=1.0)
    axv.axvline(search_dt, linestyle="--", linewidth=1.0)
    axv.set_ylabel("Volume")
    axv.grid(True, alpha=0.25)

    fig.autofmt_xdate()
    fig.tight_layout()

    filename = f"{search_date}_{sanitize_filename(pg)}_{code}_{sanitize_filename(name)}_{grade}_ret{safe_float(row.get('ret_max')):.1f}.png"
    path = OUT_DIR / filename
    fig.savefig(path, dpi=args.dpi)
    plt.close(fig)
    return path


def write_manifest(items, manifest_path):
    fieldnames = [
        "chart_path", "analysis_group", "search_date", "code", "name", "grade_v21",
        "s_score", "e_score", "trigger_path", "close_price", "day_return", "vol_ratio_20", "bb_width",
        "ret_1w", "ret_2w", "ret_3w", "ret_1m", "ret_max", "max_high_date",
    ]
    with manifest_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            writer.writerow(item)


def parse_args():
    parser = argparse.ArgumentParser(description="패턴별 대표 성공/실패 사례 일봉 차트를 생성합니다.")
    parser.add_argument("--condition-name", default=BacktestConfig.CONDITION_NAME)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--mode", choices=["top", "top_bottom", "specific"], default="top_bottom")
    parser.add_argument("--per-group", type=int, default=5)
    parser.add_argument("--pattern", choices=PATTERN_ORDER, default=None)
    parser.add_argument("--grade", default=None)
    parser.add_argument("--code", default=None)
    parser.add_argument("--date", default=None)
    parser.add_argument("--warmup-days", type=int, default=360)
    parser.add_argument("--forward-days", type=int, default=90)
    parser.add_argument("--dpi", type=int, default=130)
    return parser.parse_args()


def main():
    args = parse_args()
    logger.info("=" * 120)
    logger.info("패턴 차트 리플레이 생성 시작")
    logger.info("mode=%s per_group=%s pattern=%s grade=%s code=%s date=%s", args.mode, args.per_group, args.pattern, args.grade, args.code, args.date)
    logger.info("=" * 120)

    backtest_conn = get_conn(DBConfig.BACKTEST_DB)
    stock_conn = get_conn(DBConfig.STOCK_DATA_DB)
    manifest_items = []
    try:
        rows = fetch_scan_rows(backtest_conn, args)
        logger.info("scan_result 로딩 완료: %s건", len(rows))
        selected = select_replay_rows(rows, args.mode, args.per_group)
        logger.info("차트 생성 대상: %s건", len(selected))

        for idx, row in enumerate(selected, 1):
            center_date = parse_date(row.get("search_date"))
            candles = fetch_candles(stock_conn, row.get("code"), center_date, args.warmup_days, args.forward_days)
            path = draw_chart(row, candles, args)
            if path is None:
                logger.warning("캔들 없음: %s %s %s", row.get("search_date"), row.get("code"), row.get("name"))
                continue
            item = dict(row)
            item["analysis_group"] = pattern_group(row)
            item["chart_path"] = str(path)
            manifest_items.append(item)
            logger.info("%s/%s 저장: %s", idx, len(selected), path)

    finally:
        backtest_conn.close()
        stock_conn.close()

    manifest_path = REPORT_DIR / "pattern_replay_manifest.csv"
    write_manifest(manifest_items, manifest_path)
    logger.info("manifest 저장 완료: %s", manifest_path)
    logger.info("완료. DB는 수정하지 않았습니다.")


if __name__ == "__main__":
    main()
