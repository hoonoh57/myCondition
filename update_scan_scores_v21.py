"""
scan_result v2.1 등급 영구 저장 스크립트

기존 v2.0 s_score/e_score/grade는 보존하고,
v2.1 등급체계 결과를 별도 컬럼에 저장합니다.

추가 컬럼:
    grade_v21
    strategy_v21
    score_version_v21
    grade_v21_details
    scored_at_v21

Usage:
    python update_scan_scores_v21.py --dry-run --limit 20
    python update_scan_scores_v21.py
    python update_scan_scores_v21.py --all-versions
    python update_scan_scores_v21.py --reset --all-versions
"""
import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pymysql

from config import BacktestConfig, DBConfig


VERSION = "2.1"
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"update_scan_scores_v21_{datetime.now():%Y%m%d_%H%M%S}.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


V21_ORDER = ["A1", "A2", "B1", "B2", "C_HOT", "C_FAST", "C_WATCH", "C_BAD", "D"]


V21_STRATEGY = {
    "A1": {"position_pct": 100, "target_return": 30.0, "stop_loss": -5.0, "max_holding_days": 20, "style": "swing", "description": "최우선: 안정성+폭발력 모두 우수"},
    "A2": {"position_pct": 80, "target_return": 20.0, "stop_loss": -4.0, "max_holding_days": 20, "style": "swing_stability", "description": "안정우선: 폭발력은 낮지만 1개월 안정성 우수"},
    "B1": {"position_pct": 60, "target_return": 25.0, "stop_loss": -5.0, "max_holding_days": 10, "style": "short_swing_explosion", "description": "폭발후보: 단기 스윙형"},
    "B2": {"position_pct": 50, "target_return": 15.0, "stop_loss": -4.0, "max_holding_days": 10, "style": "short_term_standard", "description": "표준 단기형"},
    "C_HOT": {"position_pct": 50, "target_return": 20.0, "stop_loss": -5.0, "max_holding_days": 10, "style": "promoted_low_price_hot", "description": "C 승격: 저가+고E+S35 이상. C 내부 최우수 후보"},
    "C_FAST": {"position_pct": 30, "target_return": 15.0, "stop_loss": -4.0, "max_holding_days": 3, "style": "fast_explosion_only", "description": "초단기 폭발 감시군: 1개월 보유 금지"},
    "C_WATCH": {"position_pct": 10, "target_return": 10.0, "stop_loss": -3.0, "max_holding_days": 3, "style": "watch_only", "description": "관찰군: 조건식 후속 검증 필요"},
    "C_BAD": {"position_pct": 0, "target_return": 0.0, "stop_loss": 0.0, "max_holding_days": 0, "style": "skip_bad_c", "description": "C 내부 저품질: 제외"},
    "D": {"position_pct": 0, "target_return": 0.0, "stop_loss": 0.0, "max_holding_days": 0, "style": "skip_d", "description": "패스"},
}


V21_COLUMNS = {
    "grade_v21": "VARCHAR(16) NULL COMMENT 'ScoringModel v2.1 grade'",
    "strategy_v21": "VARCHAR(64) NULL COMMENT 'ScoringModel v2.1 strategy style'",
    "score_version_v21": "VARCHAR(16) NULL COMMENT 'ScoringModel v2.1 version'",
    "grade_v21_details": "TEXT NULL COMMENT 'ScoringModel v2.1 rule and strategy json'",
    "scored_at_v21": "DATETIME NULL COMMENT 'ScoringModel v2.1 updated timestamp'",
}


SELECT_COLUMNS = [
    "id", "condition_name", "search_date", "code", "name", "market",
    "trigger_path", "close_price", "day_return", "bb_width", "vol_ratio_20",
    "ret_1w", "ret_2w", "ret_3w", "ret_1m", "ret_max",
    "s_score", "e_score", "grade", "grade_v21", "score_version_v21",
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
        autocommit=False,
    )


def safe_float(value, default=0.0):
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


def json_default(value):
    """pymysql이 반환하는 Decimal/date/datetime 값을 JSON 저장 가능 형태로 변환합니다."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def table_has_column(cur, table_name, column_name):
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = %s
        """,
        (table_name, column_name),
    )
    row = cur.fetchone()
    return int(row["cnt"]) > 0


def ensure_v21_columns(conn):
    with conn.cursor() as cur:
        for column_name, column_def in V21_COLUMNS.items():
            if table_has_column(cur, "scan_result", column_name):
                logger.info("컬럼 존재: scan_result.%s", column_name)
                continue
            sql = f"ALTER TABLE scan_result ADD COLUMN {column_name} {column_def}"
            logger.info("컬럼 추가: %s", sql)
            cur.execute(sql)

        indexes = [
            ("idx_scan_result_grade_v21", "grade_v21"),
            ("idx_scan_result_score_version_v21", "score_version_v21"),
            ("idx_scan_result_scored_at_v21", "scored_at_v21"),
        ]
        for index_name, index_col in indexes:
            cur.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM information_schema.statistics
                WHERE table_schema = DATABASE()
                  AND table_name = 'scan_result'
                  AND index_name = %s
                """,
                (index_name,),
            )
            row = cur.fetchone()
            if int(row["cnt"]) > 0:
                logger.info("인덱스 존재: %s", index_name)
                continue
            cur.execute(f"CREATE INDEX {index_name} ON scan_result ({index_col})")
            logger.info("인덱스 추가: %s", index_name)
    conn.commit()


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
    return norm_trigger(row.get("trigger_path")) in ("event", "E", "C+E")


def classify_v21(row):
    s_score = safe_float(row.get("s_score"))
    e_score = safe_float(row.get("e_score"))
    close_price = safe_float(row.get("close_price"))
    bb_width = safe_float(row.get("bb_width"))

    if s_score >= 70:
        return "A1" if e_score >= 60 else "A2"
    if s_score >= 50:
        return "B1" if e_score >= 60 else "B2"
    if e_score < 60:
        return "D"

    # 기존 C 영역: S < 50 and E >= 60
    if s_score >= 35 and e_score >= 80 and close_price < 10000:
        return "C_HOT"
    if e_score >= 80 and bb_width >= 40:
        return "C_FAST"
    if e_score >= 80 or is_event_or_e_series(row):
        return "C_WATCH"
    return "C_BAD"


def build_where(condition_name, start_date, end_date, unscored_only):
    clauses = ["score_version = '2.0'", "s_score IS NOT NULL", "e_score IS NOT NULL"]
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
    if unscored_only:
        clauses.append("(score_version_v21 IS NULL OR score_version_v21 <> %s)")
        params.append(VERSION)

    return "WHERE " + " AND ".join(clauses), params


def fetch_rows(conn, condition_name, start_date, end_date, unscored_only, limit):
    where_sql, params = build_where(condition_name, start_date, end_date, unscored_only)
    sql = f"""
        SELECT {', '.join(SELECT_COLUMNS)}
        FROM scan_result
        {where_sql}
        ORDER BY search_date, code, id
    """
    if limit is not None and limit > 0:
        sql += " LIMIT %s"
        params.append(int(limit))

    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def reset_v21(conn, condition_name, start_date, end_date):
    where_sql, params = build_where(condition_name, start_date, end_date, unscored_only=False)
    sql = f"""
        UPDATE scan_result
        SET grade_v21 = NULL,
            strategy_v21 = NULL,
            score_version_v21 = NULL,
            grade_v21_details = NULL,
            scored_at_v21 = NULL
        {where_sql}
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        affected = cur.rowcount
    conn.commit()
    logger.info("v2.1 기존 결과 초기화 완료: %s건", affected)
    return affected


def make_payload(row):
    grade_v21 = classify_v21(row)
    strategy = V21_STRATEGY[grade_v21]
    details = {
        "version": VERSION,
        "grade_v20": row.get("grade"),
        "grade_v21": grade_v21,
        "rule_inputs": {
            "s_score": safe_float(row.get("s_score")),
            "e_score": safe_float(row.get("e_score")),
            "close_price": safe_float(row.get("close_price")),
            "bb_width": safe_float(row.get("bb_width")),
            "trigger_norm": norm_trigger(row.get("trigger_path")),
            "trigger_path": row.get("trigger_path"),
        },
        "strategy": strategy,
    }
    return {
        "id": row["id"],
        "search_date": row.get("search_date"),
        "code": row.get("code"),
        "name": row.get("name"),
        "grade_v20": row.get("grade"),
        "grade_v21": grade_v21,
        "strategy_v21": strategy["style"],
        "score_version_v21": VERSION,
        "grade_v21_details": json.dumps(details, ensure_ascii=False, separators=(",", ":"), default=json_default),
    }


def update_payloads(conn, payloads, batch_size):
    if not payloads:
        return 0

    sql = """
        UPDATE scan_result
        SET grade_v21 = %s,
            strategy_v21 = %s,
            score_version_v21 = %s,
            grade_v21_details = %s,
            scored_at_v21 = NOW()
        WHERE id = %s
    """

    total = 0
    with conn.cursor() as cur:
        batch = []
        for payload in payloads:
            batch.append((
                payload["grade_v21"],
                payload["strategy_v21"],
                payload["score_version_v21"],
                payload["grade_v21_details"],
                payload["id"],
            ))
            if len(batch) >= batch_size:
                cur.executemany(sql, batch)
                total += cur.rowcount
                conn.commit()
                logger.info("v2.1 DB 업데이트 진행: %s건", total)
                batch = []
        if batch:
            cur.executemany(sql, batch)
            total += cur.rowcount
            conn.commit()
            logger.info("v2.1 DB 업데이트 진행: %s건", total)

    return total


def summarize(payloads):
    counts = defaultdict(int)
    transitions = defaultdict(lambda: defaultdict(int))
    for payload in payloads:
        counts[payload["grade_v21"]] += 1
        transitions[payload.get("grade_v20")][payload["grade_v21"]] += 1

    logger.info("v2.1 등급 분포")
    for grade in V21_ORDER:
        count = counts.get(grade, 0)
        if count == 0:
            continue
        pct = count / len(payloads) * 100.0 if payloads else 0.0
        logger.info("  %s: %s건 (%.1f%%)", grade, count, pct)

    logger.info("v2.0 -> v2.1 전이")
    for old_grade in sorted(transitions.keys()):
        parts = []
        for new_grade in V21_ORDER:
            count = transitions[old_grade].get(new_grade, 0)
            if count > 0:
                parts.append(f"{new_grade}={count}")
        logger.info("  %s -> %s", old_grade, ", ".join(parts))


def preview(payloads, max_rows):
    logger.info("미리보기 상위 %s건", max_rows)
    for payload in payloads[:max_rows]:
        logger.info(
            "  [%s] %s(%s) %s -> %s strategy=%s",
            payload["search_date"],
            payload["name"],
            payload["code"],
            payload["grade_v20"],
            payload["grade_v21"],
            payload["strategy_v21"],
        )


def parse_args():
    parser = argparse.ArgumentParser(description="scan_result에 ScoringModel v2.1 등급을 별도 컬럼으로 저장합니다.")
    parser.add_argument("--condition-name", default=BacktestConfig.CONDITION_NAME)
    parser.add_argument("--start", default=None, help="search_date 시작일 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="search_date 종료일 YYYY-MM-DD")
    parser.add_argument("--all-versions", action="store_true", help="이미 v2.1 계산된 행도 다시 업데이트")
    parser.add_argument("--reset", action="store_true", help="선택 범위의 v2.1 결과를 먼저 NULL 초기화")
    parser.add_argument("--dry-run", action="store_true", help="DB 업데이트 없이 계산 결과만 출력")
    parser.add_argument("--limit", type=int, default=None, help="테스트용 최대 처리 건수")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--preview", type=int, default=20)
    return parser.parse_args()


def main():
    args = parse_args()

    logger.info("=" * 100)
    logger.info("scan_result v2.1 등급 업데이트 시작")
    logger.info("condition_name=%s start=%s end=%s version=%s", args.condition_name, args.start, args.end, VERSION)
    logger.info("dry_run=%s reset=%s all_versions=%s", args.dry_run, args.reset, args.all_versions)
    logger.info("=" * 100)

    conn = get_conn()
    try:
        ensure_v21_columns(conn)

        if args.reset:
            reset_v21(conn, args.condition_name, args.start, args.end)

        rows = fetch_rows(
            conn=conn,
            condition_name=args.condition_name,
            start_date=args.start,
            end_date=args.end,
            unscored_only=(not args.all_versions),
            limit=args.limit,
        )
        logger.info("대상 row 로딩 완료: %s건", len(rows))

        if not rows:
            logger.info("업데이트 대상이 없습니다.")
            return

        payloads = [make_payload(row) for row in rows]
        summarize(payloads)
        preview(payloads, args.preview)

        if args.dry_run:
            logger.info("dry-run 모드이므로 DB 업데이트를 수행하지 않았습니다.")
            return

        updated = update_payloads(conn, payloads, args.batch_size)
        logger.info("완료: v2.1 DB 업데이트 rowcount=%s / 계산=%s건", updated, len(payloads))

    except Exception:
        conn.rollback()
        logger.exception("v2.1 업데이트 실패")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
