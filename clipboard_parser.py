"""클립보드 TSV 파싱 + 종목코드 매칭"""
import re
import logging

logger = logging.getLogger(__name__)


def parse_perf_clipboard(raw_text: str, db_manager) -> dict:
    """
    키움 성과검증(1516) 클립보드 복사 데이터를 파싱합니다.

    클립보드 원본 형식 (탭 구분):
    ──────────────────────────────────────────────────
    [행1] 헤더1:  \t종목명\t기간 수익률\t\t\t기간 내 최고수익률\t검색시점 거래량\t기타
    [행2] 헤더2:  \t\t1주\t3개월\t1개월\t\t\t
    [행3~] 데이터: \t기가레인\t"-5.34%"\t""\t"+368.51%"\t"+368.51%"\t"7,832,315"\t"-3.44"
    ──────────────────────────────────────────────────

    Returns:
        {
            'records': [
                {
                    'name': '기가레인',
                    'code': '049080',
                    'market': 'KOSDAQ',
                    'ret_1w': -5.34,
                    'ret_2w': None,      # 비어있으면 None
                    'ret_3w': None,      # 3개월 → 실제 기간에 따라 매핑
                    'ret_1m': 368.51,
                    'ret_max': 368.51,
                    'search_volume': 7832315,
                    'etc_value': -3.44,
                    'raw_line': '원본행...'
                },
                ...
            ],
            'parse_errors': ['매칭 실패 종목명 리스트']
        }
    """
    lines = raw_text.strip().split('\n')

    if len(lines) < 3:
        logger.warning(f"파싱 실패: 행 수 부족 ({len(lines)}행)")
        return {'records': [], 'parse_errors': ['행 수 부족']}

    # ── 헤더 분석 (2번째 행에서 기간 컬럼명 추출) ──
    header2_cols = lines[1].split('\t')
    logger.debug(f"헤더2 컬럼: {header2_cols}")

    # 기간 컬럼의 인덱스와 이름을 동적으로 파악
    # 일반적으로: [빈칸, 빈칸, 1주, 3주(or 2주 or 3개월), 1개월, 빈칸, 빈칸, 빈칸]
    period_names = []
    for col in header2_cols[2:5]:  # 인덱스 2,3,4가 기간수익률
        period_names.append(col.strip().strip('"'))

    # ── 데이터 행 파싱 (3번째 행부터) ──
    records = []
    parse_errors = []

    for line_idx, line in enumerate(lines[2:], start=3):
        cols = line.split('\t')

        if len(cols) < 6:
            logger.debug(f"행{line_idx} 스킵: 컬럼 부족 ({len(cols)})")
            continue

        # 종목명 (인덱스 1)
        stock_name = cols[1].strip().strip('"')
        if not stock_name:
            continue

        # 종목코드 매칭
        code, market = db_manager.resolve_code(stock_name)
        if code is None:
            parse_errors.append(stock_name)
            logger.warning(f"행{line_idx} 종목코드 매칭 실패: '{stock_name}'")
            continue

        # 수익률 파싱 헬퍼
        def parse_pct(val_str):
            """"+5.34%" → 5.34, "" → None"""
            s = val_str.strip().strip('"').strip()
            if not s or s == '':
                return None
            s = s.replace('+', '').replace('%', '').replace(',', '')
            try:
                return float(s)
            except ValueError:
                return None

        def parse_volume(val_str):
            """7,832,315 → 7832315"""
            s = val_str.strip().strip('"').replace(',', '')
            try:
                return int(s)
            except ValueError:
                return 0

        # 기간 수익률 (인덱스 2, 3, 4)
        ret_vals = [parse_pct(cols[i]) if i < len(cols) else None for i in (2, 3, 4)]

        # 최고수익률 (인덱스 5)
        ret_max = parse_pct(cols[5]) if len(cols) > 5 else None

        # 거래량 (인덱스 6)
        volume = parse_volume(cols[6]) if len(cols) > 6 else 0

        # 기타 (인덱스 7)
        etc_val = parse_pct(cols[7]) if len(cols) > 7 else None

        record = {
            'name': stock_name,
            'code': code,
            'market': market,
            'ret_1w': ret_vals[0],           # 첫 번째 기간
            'ret_2w': ret_vals[1],           # 두 번째 기간 (3주 or 2주 or 3개월)
            'ret_3w': None,                   # 아래에서 매핑
            'ret_1m': ret_vals[2],           # 세 번째 기간 (1개월)
            'ret_max': ret_max,
            'search_volume': volume,
            'etc_value': etc_val,
            'raw_line': line,
        }

        # 기간명에 따라 동적 매핑
        # 헤더2의 기간명을 보고 ret_2w/ret_3w 재배치
        if len(period_names) >= 2:
            p2 = period_names[1]
            if '3주' in p2:
                record['ret_3w'] = ret_vals[1]
                record['ret_2w'] = None
            elif '2주' in p2:
                record['ret_2w'] = ret_vals[1]
                record['ret_3w'] = None
            elif '3개월' in p2 or '3월' in p2:
                # 3개월은 ret_3w에 넣되, 실제 의미는 3개월
                record['ret_3w'] = ret_vals[1]
                record['ret_2w'] = None

        records.append(record)

    logger.info(
        f"파싱 완료: {len(records)}종목 성공, "
        f"{len(parse_errors)}종목 매칭 실패"
    )

    return {
        'records': records,
        'parse_errors': parse_errors,
    }
