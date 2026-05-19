"""설정 로딩 및 공통 상수"""
import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / '.env')


class DBConfig:
    HOST = os.getenv('DB_HOST', 'localhost')
    PORT = int(os.getenv('DB_PORT', 3306))
    USER = os.getenv('DB_USER', 'root')
    PASSWORD = os.getenv('DB_PASSWORD', '')
    STOCK_INFO_DB = os.getenv('DB_STOCK_INFO', 'stock_information')
    STOCK_DATA_DB = os.getenv('DB_STOCK_DATA', 'stock_info')
    BACKTEST_DB = os.getenv('DB_BACKTEST', 'kiwoom_backtest')


class HTSConfig:
    """키움 HTS 성과검증(1516) 창 제어용 설정"""
    # 성과검증 창 캡션 (Spy++로 확인 필요 — 보통 아래 형식)
    WINDOW_CAPTION = "성과검증"
    # 또는 영웅문 메인 윈도우 캡션
    MAIN_CAPTION = "영웅문4"

    # 검증 버튼, 날짜입력 등의 좌표는 사용자 환경에 따라 다름
    # → 최초 1회 calibration 필요 (main.py의 calibrate 모드로 좌표 기록)
    # 아래는 기본값 (사용자가 calibrate 후 업데이트)
    DATE_INPUT_POS = (350, 120)       # 검색시점 날짜 입력 필드 좌표
    SEARCH_BUTTON_POS = (500, 120)    # "검증" 버튼 좌표
    RESULT_TABLE_POS = (400, 400)     # 결과 테이블 영역 중심 좌표
    COPY_MENU_OFFSET = (30, 60)      # 우클릭 후 "복사" 메뉴 오프셋

    WAIT_AFTER_SEARCH = int(os.getenv('WAIT_AFTER_SEARCH', 5))


class BacktestConfig:
    CONDITION_NAME = os.getenv('CONDITION_NAME', '60_200이평돌파')
    START_DATE = os.getenv('BACKTEST_START', '2025-05-19')
    END_DATE = os.getenv('BACKTEST_END', '2026-05-16')
    INTERVAL_DAYS = int(os.getenv('SEARCH_INTERVAL_DAYS', 1))
