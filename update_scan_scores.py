"""
scan_result 스코어/등급 영구 저장 스크립트

기존 scan_result 레코드에 ScoringModelV2를 적용하여
s_score, e_score, grade, score_version, grade_strategy, score_details, scored_at 컬럼을 채웁니다.

Usage:
    python update_scan_scores.py
    python update_scan_scores.py --condition-name "60_200이평돌파"
    python update_scan_scores.py --start 2024-05-20 --end 2026-05-15
    python update_scan_scores.py --reset
    python update_scan_scores.py --dry-run --limit 20
"""
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import pymysql

from config import BacktestConfig, DBConfig
from scoring_model import ScoringModelV2


LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"update_scan_scores_{datetime.now():%Y%m%d_%H%M%S}.log",
            encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


SCORE_COLUMNS = {
    "s_score": "INT NULL COMMENT 'ScoringModelV2 stability score'",
    "e_score": "INT NULL COMMENT 'ScoringModelV2 explosion score'",
    "grade": "VARCHAR(8) NULL COMMENT 'ScoringModelV2 grade A1/A2/B1/B2/C/D'",
    "score_version": "VARCHAR(16) NULL COMMENT 'scoring model version'",
    "grade_strategy": "VARCHAR(32) NULL COMMENT 'strategy holding style from grade matrix'",
    "score_details": "TEXT NULL COMMENT 'factor scores json/details'",
    "scored_at": "DATETIME NULL COMMENT 'score updated timestamp'",
}


SELECT_COLUMNS = [
    "id",
    "search_date",
    "code",
    "name",
    "condition_name",
    "trigger_path",
    "close_price",
    "ma60_200_dist",
    "rsi14",
    "bb_width",
    "vol_ratio_20",
    "ma60_slope_up",
    "ma200_slope_up",
    "day_return",
    "ret_1w",
    "ret_1m",
    "ret_max",
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


def ensure_score_columns(conn):
    with conn.cursor() as cur:
        for column_name, column_def in SCORE_COLUMNS.items():
            if table_has_column(cur, "scan_result", column_name):
                logger.info("컬럼 존재: scan_result.%s", column_name)
                continue
            sql = f"ALTER TABLE scan_result ADD COLUMN {column_name} {column_def}"
            logger.info("컬럼 추가: %s", sql)
            cur.execute(sql)

        index_specs = [
            ("idx_scan_result_grade", "grade"),
            ("idx_scan_result_score_version", "score_version"),
            ("idx_scan_result_scored_at", "scored_at"),
        ]
        for index_name, index_col in index_specs:
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


def reset_scores(conn, condition_name, start_date, end_date):
    where_sql, params = build_where(condition_name, start_date, end_date, include_unscored_only=False)
    sql = f"""
        UPDATE scan_result
        SET s_score = NULL,
            e_score = NULL,
            grade = NULL,
            score_version = NULL,
            grade_strategy = NULL,
            score_details = NULL,
            scored_at = NULL
        {where_sql}
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        affected = cur.rowcount
    conn.commit()
    logger.info("기존 스코어 초기화 완료: %s건", affected)
    return affected


def build_where(condition_name, start_date, end_date, include_unscored_only):
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
    if include_unscored_only:
        clauses.append("(grade IS NULL OR score_version IS NULL OR score_version <> %s)")
        params.append(ScoringModelV2.VERSION)

    if not clauses:
        return "", params
    return "WHERE " + " AND ".join(clauses), params


def fetch_rows(conn, condition_name, start_date, end_date, unscored_only, limit):
    where_sql, params = build_where(condition_name, start_date, end_date, unscored_only)
    col_sql = ", ".join(SELECT_COLUMNS)
    sql = f"""
        SELECT {col_sql}
        FROM scan_result
        {where_sql}
        ORDER BY search_date, code, id
    """
    if limit is not None and limit > 0:
        sql += " LIMIT %s"
        params.append(int(limit))

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return rows


def make_score_payload(model, row):
    result = model.score({
        "trigger_path": row.get("trigger_path"),
        "ma60_200_dist": row.get("ma60_200_dist"),
        "rsi14": row.get("rsi14"),
        "bb_width": row.get("bb_width"),
        "vol_ratio_20": row.get("vol_ratio_20"),
        "ma60_slope_up": row.get("ma60_slope_up"),
        "ma200_slope_up": row.get("ma200_slope_up"),
        "close_price": row.get("close_price"),
        "day_return": row.get("day_return"),
    })

    details = {
        "version": model.VERSION,
        "s_factors": result["s_factors"],
        "e_factors": result["e_factors"],
        "details": result["details"],
        "strategy": result["strategy"],
    }

    return {
        "id": row["id"],
        "search_date": row.get("search_date"),
        "code": row.get("code"),
        "name": row.get("name"),
        "s_score": int(result["s_score"]),
        "e_score": int(result["e_score"]),
        "grade": result["grade"],
        "score_version": model.VERSION,
        "grade_strategy": result["strategy"].get("holding_style", ""),
        "score_details": json.dumps(details, ensure_ascii=False, separators=(",", ":")),
    }


def update_scores(conn, payloads, batch_size):
    if not payloads:
        return 0

    sql = """
        UPDATE scan_result
        SET s_score = %s,
            e_score = %s,
            grade = %s,
            score_version = %s,
            grade_strategy = %s,
            score_details = %s,
            scored_at = NOW()
        WHERE id = %s
    """

    total = 0
    with conn.cursor() as cur:
        batch = []
        for p in payloads:
            batch.append((
                p["s_score"],
                p["e_score"],
                p["grade"],
                p["score_version"],
                p["grade_strategy"],
                p["score_details"],
                p["id"],
            ))
            if len(batch) >= batch_size:
                cur.executemany(sql, batch)
                total += cur.rowcount
                conn.commit()
                logger.info("DB 업데이트 진행: %s건", total)
                batch = []

        if batch:
            cur.executemany(sql, batch)
            total += cur.rowcount
            conn.commit()
            logger.info("DB 업데이트 진행: %s건", total)

    return total


def summarize(payloads):
    grade_counts = {}
    for p in payloads:
        grade = p["grade"]
        grade_counts[grade] = grade_counts.get(grade, 0) + 1

    order = ["A1", "A2", "B1", "B2", "C", "D"]
    logger.info("등급 분포")
    for grade in order:
        count = grade_counts.get(grade, 0)
        if count == 0:
            continue
        pct = count / len(payloads) * 100.0 if payloads else 0.0
        logger.info("  %s: %s건 (%.1f%%)", grade, count, pct)


def preview(payloads, max_rows):
    logger.info("미리보기 상위 %s건", max_rows)
    for p in payloads[:max_rows]:
        logger.info(
            "  [%s] %s(%s) S=%s E=%s G=%s style=%s",
            p["search_date"],
            p["name"],
            p["code"],
            p["s_score"],
            p["e_score"],
            p["grade"],
            p["grade_strategy"],
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description="scan_result 전체 또는 일부에 ScoringModelV2 결과를 영구 저장합니다."
    )
    parser.add_argument(
        "--condition-name",
        default=BacktestConfig.CONDITION_NAME,
        help="대상 condition_name. 기본값은 config.BacktestConfig.CONDITION_NAME",
    )
    parser.add_argument("--start", default=None, help="search_date 시작일 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="search_date 종료일 YYYY-MM-DD")
    parser.add_argument(
        "--all-versions",
        action="store_true",
        help="이미 같은 score_version으로 계산된 행도 다시 업데이트합니다.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="선택 범위의 기존 스코어 컬럼을 먼저 NULL로 초기화합니다.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="DB 업데이트 없이 계산 결과만 출력합니다.",
    )
    parser.add_argument("--limit", type=int, default=None, help="테스트용 최대 처리 건수")
    parser.add_argument("--batch-size", type=int, default=500, help="DB 업데이트 배치 크기")
    parser.add_argument("--preview", type=int, default=20, help="처리 결과 미리보기 건수")
    return parser.parse_args()


def main():
    args = parse_args()
    model = ScoringModelV2()

    logger.info("=" * 90)
    logger.info("scan_result 스코어/등급 업데이트 시작")
    logger.info("condition_name=%s", args.condition_name)
    logger.info("start=%s end=%s version=%s", args.start, args.end, model.VERSION)
    logger.info("dry_run=%s reset=%s all_versions=%s", args.dry_run, args.reset, args.all_versions)
    logger.info("=" * 90)

    conn = get_conn()
    try:
        ensure_score_columns(conn)

        if args.reset:
            reset_scores(conn, args.condition_name, args.start, args.end)

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

        payloads = []
        for row in rows:
            payloads.append(make_score_payload(model, row))

        summarize(payloads)
        preview(payloads, args.preview)

        if args.dry_run:
            logger.info("dry-run 모드이므로 DB 업데이트를 수행하지 않았습니다.")
            return

        updated = update_scores(conn, payloads, args.batch_size)
        logger.info("완료: DB 업데이트 rowcount=%s / 계산=%s건", updated, len(payloads))

    except Exception:
        conn.rollback()
        logger.exception("스코어 업데이트 실패")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
