"""
키움 HTS 성과검증 자동 수집 메인 루프

Usage:
    # 1) 최초 좌표 캘리브레이션
    python main.py --calibrate

    # 2) 전체 백테스트 실행
    python main.py

    # 3) 특정 날짜 범위만 실행
    python main.py --start 2025-06-01 --end 2025-12-31

    # 4) 수동 클립보드 모드 (HTS에서 직접 복사한 후 붙여넣기)
    python main.py --manual
"""
import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from config import HTSConfig, BacktestConfig, DBConfig
from db_manager import DBManager
from hts_controller import HTSController
from clipboard_parser import parse_perf_clipboard

# ── 로깅 설정 ────────────────────────────────────
log_dir = Path(__file__).parent / 'logs'
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(
            log_dir / f"backtest_{datetime.now():%Y%m%d_%H%M%S}.log",
            encoding='utf-8'
        ),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


# ── 영업일 생성 (주말 제외, 공휴일은 별도 처리 가능) ────
def generate_trading_dates(start: date, end: date) -> list:
    """주말을 제외한 영업일 리스트 생성"""
    dates = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # 월(0)~금(4)
            dates.append(current)
        current += timedelta(days=1)
    return dates


# ── 한국 공휴일 필터 (간이 버전) ────────────────────
KOREAN_HOLIDAYS_2025_2026 = {
    # 2025
    date(2025, 1, 1), date(2025, 1, 28), date(2025, 1, 29),
    date(2025, 1, 30), date(2025, 3, 1), date(2025, 5, 1),
    date(2025, 5, 5), date(2025, 5, 6), date(2025, 6, 6),
    date(2025, 8, 15), date(2025, 10, 3), date(2025, 10, 5),
    date(2025, 10, 6), date(2025, 10, 7), date(2025, 10, 9),
    date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 2, 16), date(2026, 2, 17),
    date(2026, 2, 18), date(2026, 3, 1), date(2026, 3, 2),
    date(2026, 5, 1), date(2026, 5, 5), date(2026, 5, 24),
    date(2026, 6, 6), date(2026, 8, 15), date(2026, 10, 3),
    date(2026, 10, 9), date(2026, 12, 25),
}


def filter_holidays(dates: list) -> list:
    """공휴일 제거"""
    return [d for d in dates if d not in KOREAN_HOLIDAYS_2025_2026]


# ── 자동 모드: HTS 제어 ──────────────────────────
def run_auto_mode(args):
    """HTS 자동 제어로 날짜별 성과검증 데이터 수집"""

    db = DBManager()
    hts = HTSController(HTSConfig)

    # 날짜 범위 설정
    start = datetime.strptime(
        args.start or BacktestConfig.START_DATE, '%Y-%m-%d'
    ).date()
    end = datetime.strptime(
        args.end or BacktestConfig.END_DATE, '%Y-%m-%d'
    ).date()

    condition = BacktestConfig.CONDITION_NAME

    # 영업일 리스트 생성
    all_dates = generate_trading_dates(start, end)
    all_dates = filter_holidays(all_dates)

    # 이미 수집된 날짜 제외
    collected = db.get_collected_dates(condition)
    remaining = [d for d in all_dates if d not in collected]

    logger.info(
        f"백테스트 범위: {start} ~ {end}\n"
        f"  전체 영업일: {len(all_dates)}일\n"
        f"  이미 수집: {len(collected)}일\n"
        f"  남은 수집: {len(remaining)}일"
    )

    if not remaining:
        logger.info("모든 날짜 수집 완료!")
        return

    # HTS 활성화
    if not hts.activate_window():
        logger.error("HTS 창 활성화 실패. 프로그램 종료.")
        return

    # ── 날짜별 루프 ──────────────────────────────
    success_count = 0
    error_count = 0
    empty_count = 0

    for idx, target_date in enumerate(remaining):
        logger.info(
            f"\n{'='*50}\n"
            f"[{idx+1}/{len(remaining)}] {target_date} 처리 중...\n"
            f"{'='*50}"
        )

        try:
            # 1) 날짜 설정
            hts.set_search_date(target_date)
            time.sleep(0.5)

            # 2) 검증 실행
            hts.click_search_button()

            # 3) 결과 복사
            raw_clipboard = hts.copy_result_table()

            if not raw_clipboard or raw_clipboard.strip() == '':
                logger.info(f"[{target_date}] 검색 결과 없음")
                db.save_empty_log(condition, target_date)
                empty_count += 1
                continue

            # 4) 파싱
            parsed = parse_perf_clipboard(raw_clipboard, db)

            if not parsed['records']:
                logger.info(f"[{target_date}] 파싱 결과 0건")
                db.save_empty_log(condition, target_date)
                empty_count += 1
                continue

            # 5) DB 저장
            db.save_results(
                condition_name=condition,
                search_date=target_date.strftime('%Y-%m-%d'),
                records=parsed['records'],
            )

            success_count += 1

            # 너무 빠르게 돌면 HTS가 과부하될 수 있으므로 쉬어가기
            if (idx + 1) % 10 == 0:
                logger.info("10건 처리 완료, 3초 쉬어가기...")
                time.sleep(3)

        except KeyboardInterrupt:
            logger.info("\n사용자 중단 (Ctrl+C)")
            break
        except Exception as e:
            logger.error(f"[{target_date}] 오류: {e}", exc_info=True)
            db.save_error_log(condition, target_date, str(e))
            error_count += 1
            time.sleep(2)

    # ── 최종 리포트 ─────────────────────────────
    logger.info(
        f"\n{'='*60}\n"
        f"  수집 완료 리포트\n"
        f"{'='*60}\n"
        f"  성공: {success_count}일\n"
        f"  빈 결과: {empty_count}일\n"
        f"  오류: {error_count}일\n"
        f"  총 소요: {success_count + empty_count + error_count}일\n"
        f"{'='*60}"
    )


# ── 수동 모드: 클립보드 붙여넣기 ────────────────────
def run_manual_mode(args):
    """
    수동 모드: 사용자가 HTS에서 직접 복사한 클립보드 데이터를
    입력받아 파싱 후 DB 저장.

    ※ HTS 자동제어가 어려운 환경에서 사용
    """
    db = DBManager()
    condition = BacktestConfig.CONDITION_NAME

    print("=" * 60)
    print("  수동 모드: 키움 성과검증 클립보드 데이터 입력")
    print("  종료하려면 'quit' 입력")
    print("=" * 60)

    while True:
        # 날짜 입력
        date_str = input("\n검색 시점 날짜 (YYYY-MM-DD): ").strip()
        if date_str.lower() == 'quit':
            break
        try:
            search_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            print("  날짜 형식 오류. YYYY-MM-DD 형식으로 입력하세요.")
            continue

        # 클립보드 읽기 또는 직접 입력
        print("  HTS에서 결과를 복사(Ctrl+C)한 후 Enter를 누르세요...")
        input("  [Enter] ")

        raw = pyperclip.paste()
        if not raw or raw.strip() == '':
            print("  클립보드가 비어있습니다.")
            continue

        lines = raw.strip().split('\n')
        print(f"  클립보드: {len(lines)}행 감지")

        # 파싱
        parsed = parse_perf_clipboard(raw, db)

        if not parsed['records']:
            print("  파싱 결과 0건.")
            if parsed['parse_errors']:
                print(f"  매칭 실패: {parsed['parse_errors']}")
            continue

        # 미리보기
        print(f"\n  파싱 결과 ({len(parsed['records'])}종목):")
        print(f"  {'종목명':<12} {'코드':<8} {'1주':>8} {'1개월':>8} {'최고':>8}")
        print("  " + "-" * 50)
        for r in parsed['records']:
            ret1w = f"{r['ret_1w']:+.2f}%" if r['ret_1w'] is not None else '-'
            ret1m = f"{r['ret_1m']:+.2f}%" if r['ret_1m'] is not None else '-'
            retmax = f"{r['ret_max']:+.2f}%" if r['ret_max'] is not None else '-'
            print(f"  {r['name']:<12} {r['code']:<8} {ret1w:>8} {ret1m:>8} {retmax:>8}")

        if parsed['parse_errors']:
            print(f"\n  ⚠ 매칭 실패 종목: {parsed['parse_errors']}")

        # 저장 확인
        confirm = input("\n  DB에 저장하시겠습니까? (y/n): ").strip().lower()
        if confirm == 'y':
            db.save_results(
                condition_name=condition,
                search_date=search_date.strftime('%Y-%m-%d'),
                records=parsed['records'],
            )
            print(f"  ✓ {search_date} / {len(parsed['records'])}종목 저장 완료")
        else:
            print("  저장 취소.")


# ── import용 pyperclip (수동 모드에서만 필요) ──────────
try:
    import pyperclip
except ImportError:
    pass


# ── 엔트리 포인트 ────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='키움 HTS 성과검증 자동 수집기'
    )
    parser.add_argument(
        '--calibrate', action='store_true',
        help='좌표 캘리브레이션 모드'
    )
    parser.add_argument(
        '--manual', action='store_true',
        help='수동 모드 (클립보드 직접 입력)'
    )
    parser.add_argument('--start', type=str, help='시작일 YYYY-MM-DD')
    parser.add_argument('--end', type=str, help='종료일 YYYY-MM-DD')
    parser.add_argument(
        '--dry-run', action='store_true',
        help='DB 저장 없이 파싱만 테스트'
    )

    args = parser.parse_args()

    if args.calibrate:
        HTSController.calibrate()
    elif args.manual:
        run_manual_mode(args)
    else:
        run_auto_mode(args)


if __name__ == '__main__':
    main()
