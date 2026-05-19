"""
키움 영웅문4 성과검증(1516) 창 자동 제어

※ 핵심 주의사항:
  - 키움 HTS는 32비트 프로세스 → Python도 32비트로 실행해야 win32gui 핸들 접근 가능
  - 또는 pyautogui (좌표 기반)를 사용하면 비트 무관
  - 이 모듈은 pyautogui(좌표 기반) + win32gui(핸들 보조) 하이브리드 방식
"""
import time
import pyautogui
import pyperclip
import win32gui
import win32con
import win32api
import logging
from datetime import datetime, date

logger = logging.getLogger(__name__)

# pyautogui 안전 설정
pyautogui.FAILSAFE = True     # 마우스를 좌상단에 대면 긴급 정지
pyautogui.PAUSE = 0.3         # 모든 동작 사이 0.3초 딜레이


class HTSController:
    """키움 영웅문4 성과검증(1516) 자동 제어 클래스"""

    def __init__(self, config):
        """
        config: HTSConfig 객체
        """
        self.cfg = config
        self.hwnd_main = None
        self.hwnd_perf = None
        self._find_windows()

    # ── 윈도우 핸들 탐색 ─────────────────────────────
    def _find_windows(self):
        """영웅문4 메인 윈도우 및 성과검증 탭/창 핸들 탐색"""

        def _enum_callback(hwnd, results):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title:
                    results.append((hwnd, title))

        windows = []
        win32gui.EnumWindows(_enum_callback, windows)

        for hwnd, title in windows:
            if self.cfg.MAIN_CAPTION in title:
                self.hwnd_main = hwnd
                logger.info(f"영웅문 메인 창 발견: hwnd={hwnd}, title='{title}'")
            if self.cfg.WINDOW_CAPTION in title:
                self.hwnd_perf = hwnd
                logger.info(f"성과검증 창 발견: hwnd={hwnd}, title='{title}'")

        if not self.hwnd_main:
            logger.warning(
                "영웅문4 메인 창을 찾지 못했습니다. "
                "HTS를 실행하고 성과검증(1516)을 열어주세요."
            )

    def activate_window(self):
        """성과검증 창을 최전면으로 가져오기"""
        target = self.hwnd_perf or self.hwnd_main
        if target:
            try:
                # 최소화 상태이면 복원
                if win32gui.IsIconic(target):
                    win32gui.ShowWindow(target, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(target)
                time.sleep(0.5)
                return True
            except Exception as e:
                logger.error(f"창 활성화 실패: {e}")
        return False

    # ── 날짜 입력 ──────────────────────────────────
    def set_search_date(self, target_date: date):
        """
        성과검증 창의 검색시점 날짜를 변경합니다.

        방법 1 (권장): 날짜 입력 필드를 클릭 → 전체선택 → 날짜 타이핑
        방법 2 (대안): DateTimePicker 컨트롤에 직접 메시지 전송
        """
        date_str = target_date.strftime('%Y/%m/%d')

        # 날짜 입력 필드 클릭
        x, y = self.cfg.DATE_INPUT_POS
        pyautogui.click(x, y)
        time.sleep(0.3)

        # 전체 선택 후 날짜 입력
        # 키움 DateTimePicker는 보통 숫자만 입력하면 됨
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.1)

        # 날짜를 숫자로 입력 (20250519 형식이 되는 경우)
        # 또는 각 필드(년/월/일)를 탭으로 이동하며 입력
        # ──────────────────────────────────────────
        # 방법 A: 전체를 한번에 입력 (HTS가 허용하는 경우)
        # pyautogui.typewrite(target_date.strftime('%Y%m%d'), interval=0.05)

        # 방법 B: 년→월→일 각각 입력 (DateTimePicker 일반적 방식)
        year = target_date.strftime('%Y')
        month = target_date.strftime('%m')
        day = target_date.strftime('%d')

        # 년도 필드 (이미 선택된 상태)
        pyautogui.typewrite(year, interval=0.03)
        time.sleep(0.1)

        # 오른쪽 화살표로 월 필드 이동 (또는 Tab)
        # ※ 키움 DateTimePicker에 따라 '/' 키가 다음 필드로 이동시킬 수도 있음
        pyautogui.press('right')
        time.sleep(0.1)
        pyautogui.typewrite(month, interval=0.03)
        time.sleep(0.1)

        pyautogui.press('right')
        time.sleep(0.1)
        pyautogui.typewrite(day, interval=0.03)
        time.sleep(0.1)

        pyautogui.press('enter')
        time.sleep(0.3)

        logger.info(f"검색 날짜 설정: {date_str}")

    # ── 검증 실행 ─────────────────────────────────
    def click_search_button(self):
        """검증 버튼 클릭"""
        x, y = self.cfg.SEARCH_BUTTON_POS
        pyautogui.click(x, y)
        logger.info("검증 버튼 클릭")
        time.sleep(self.cfg.WAIT_AFTER_SEARCH)

    # ── 결과 복사 ─────────────────────────────────
    def copy_result_table(self) -> str:
        """
        결과 테이블 영역에서 우클릭 → 복사 → 클립보드 내용 반환

        ※ 키움 성과검증 테이블의 우클릭 메뉴 구조:
           보통 "복사" 또는 "클립보드 복사"가 첫 번째~세 번째 항목
        """
        # 클립보드 초기화
        pyperclip.copy('')

        # 결과 테이블 영역 좌클릭 (포커스 이동 + 전체 선택)
        x, y = self.cfg.RESULT_TABLE_POS
        pyautogui.click(x, y)
        time.sleep(0.3)

        # 전체 선택 (Ctrl+A)
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.3)

        # 우클릭 → 컨텍스트 메뉴
        pyautogui.rightClick(x, y)
        time.sleep(0.5)

        # "복사" 메뉴 클릭 (컨텍스트 메뉴의 상대적 위치)
        cx, cy = self.cfg.COPY_MENU_OFFSET
        pyautogui.click(x + cx, y + cy)
        time.sleep(0.5)

        # 클립보드에서 내용 가져오기
        clipboard_content = pyperclip.paste()

        if not clipboard_content or clipboard_content.strip() == '':
            logger.warning("클립보드가 비어있습니다. 복사 실패 가능성.")
            # 재시도: Ctrl+C로 복사 시도
            pyautogui.click(x, y)
            time.sleep(0.2)
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.2)
            pyautogui.hotkey('ctrl', 'c')
            time.sleep(0.5)
            clipboard_content = pyperclip.paste()

        lines = clipboard_content.strip().split('\n') if clipboard_content else []
        logger.info(f"클립보드 복사: {len(lines)}행")

        return clipboard_content

    # ── 결과 비어있는지 확인 ────────────────────────
    def check_no_result(self) -> bool:
        """
        검색 결과가 없는 경우를 감지합니다.
        (화면에 "검색된 종목이 없습니다" 등의 메시지가 나올 때)

        이미지 기반 감지를 쓰거나, 클립보드 복사 결과가 빈 경우로 판단.
        """
        clipboard = self.copy_result_table()
        lines = clipboard.strip().split('\n') if clipboard.strip() else []
        # 헤더 2줄만 있고 데이터가 없으면 → 결과 없음
        return len(lines) <= 2

    # ── 캘리브레이션 모드 ──────────────────────────
    @staticmethod
    def calibrate():
        """
        좌표 캘리브레이션: 마우스를 원하는 위치에 놓고 Enter를 누르면 좌표 기록

        실행 방법: python -c "from hts_controller import HTSController; HTSController.calibrate()"
        """
        positions = {}
        targets = [
            ('DATE_INPUT_POS', '날짜 입력 필드 위에 마우스를 놓고 Enter'),
            ('SEARCH_BUTTON_POS', '"검증" 버튼 위에 마우스를 놓고 Enter'),
            ('RESULT_TABLE_POS', '결과 테이블 중앙에 마우스를 놓고 Enter'),
            ('COPY_MENU_OFFSET', '(이전 위치에서) 우클릭 후 "복사" 메뉴 위에 놓고 Enter'),
        ]

        print("=" * 60)
        print("  키움 HTS 성과검증(1516) 좌표 캘리브레이션")
        print("  ※ 성과검증 창을 미리 열어두세요")
        print("=" * 60)

        for key, instruction in targets:
            print(f"\n▶ {instruction}")
            input("  준비되면 Enter...")
            x, y = pyautogui.position()

            if key == 'COPY_MENU_OFFSET':
                # 결과 테이블 좌표 대비 오프셋 계산
                ref = positions['RESULT_TABLE_POS']
                positions[key] = (x - ref[0], y - ref[1])
                print(f"  ✓ {key} = ({x - ref[0]}, {y - ref[1]}) [오프셋]")
            else:
                positions[key] = (x, y)
                print(f"  ✓ {key} = ({x}, {y})")

        print("\n" + "=" * 60)
        print("  캘리브레이션 결과 → .env 또는 config.py에 반영하세요:")
        print("=" * 60)
        for key, pos in positions.items():
            print(f"  {key} = {pos}")

        return positions
