"""
update_sector.py
KRX 업종분류 현황 크롤링 → stock_base_info.sector 업데이트
"""

import pandas as pd
import requests as rq
from io import BytesIO
import pymysql
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

# ── DB 설정 ──
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = int(os.getenv('DB_PORT', 3306))
DB_USER = os.getenv('DB_USER', 'root')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
DB_NAME = os.getenv('DB_STOCK_INFO', 'stock_info')


def get_latest_biz_day():
    """최근 영업일 구하기 (네이버금융에서 추출)"""
    try:
        url = 'https://finance.naver.com/sise/sise_deposit.naver'
        res = rq.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(res.text, 'html.parser')
        # 날짜 텍스트에서 YYYYMMDD 추출
        date_text = soup.select_one('#type_1 > div > ul.subtop_sise_graph2 > li > span.tah').text
        import re
        match = re.search(r'(\d{4})\.(\d{2})\.(\d{2})', date_text)
        if match:
            return match.group(1) + match.group(2) + match.group(3)
    except Exception as e:
        print(f"네이버 영업일 추출 실패: {e}")
    
    # 실패 시 오늘 기준 최근 평일
    today = datetime.now()
    while today.weekday() >= 5:  # 토/일이면 금요일로
        today -= timedelta(days=1)
    return today.strftime('%Y%m%d')


def crawl_krx_sector(biz_day):
    """KRX에서 KOSPI + KOSDAQ 업종분류 현황 크롤링"""
    
    gen_url = 'http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd'
    down_url = 'http://data.krx.co.kr/comm/fileDn/download_csv/download.cmd'
    headers = {
        'Referer': 'http://data.krx.co.kr/contents/MDC/MDI/mdiLoader',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    all_data = []
    
    for mkt_id, mkt_name in [('STK', 'KOSPI'), ('KSQ', 'KOSDAQ')]:
        print(f"  {mkt_name} 업종 데이터 요청 중...")
        
        params = {
            'mktId': mkt_id,
            'trdDd': biz_day,
            'money': '1',
            'csvxls_isNo': 'false',
            'name': 'fileDown',
            'url': 'dbms/MDC/STAT/standard/MDCSTAT03901'
        }
        
        # Step 1: OTP 발급
        otp = rq.post(gen_url, params, headers=headers).text
        
        # Step 2: CSV 다운로드
        res = rq.post(down_url, {'code': otp}, headers=headers)
        
        df = pd.read_csv(BytesIO(res.content), encoding='EUC-KR')
        df['시장'] = mkt_name
        all_data.append(df)
        print(f"  {mkt_name}: {len(df)}건 수신")
    
    result = pd.concat(all_data).reset_index(drop=True)
    result['종목명'] = result['종목명'].str.strip()
    
    print(f"\n총 {len(result)}건 크롤링 완료")
    print(f"컬럼: {list(result.columns)}")
    print(f"\n업종 분포 (상위 20):")
    print(result['업종명'].value_counts().head(20).to_string())
    
    return result


def update_db_sector(krx_df):
    """stock_base_info 테이블에 sector 업데이트"""
    
    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, charset='utf8mb4'
    )
    cursor = conn.cursor()
    
    # 종목코드 6자리 맞추기
    krx_df['종목코드'] = krx_df['종목코드'].astype(str).str.zfill(6)
    
    # 현재 DB 종목 조회
    cursor.execute("SELECT code FROM stock_base_info")
    db_codes = set(row[0] for row in cursor.fetchall())
    
    updated = 0
    not_found = 0
    
    for _, row in krx_df.iterrows():
        code = row['종목코드']
        sector = row['업종명']
        
        if code in db_codes:
            cursor.execute(
                "UPDATE stock_base_info SET sector = %s WHERE code = %s",
                (sector, code)
            )
            if cursor.rowcount > 0:
                updated += 1
        else:
            not_found += 1
    
    conn.commit()
    
    # 결과 확인
    cursor.execute("""
        SELECT COUNT(*) AS total,
               SUM(sector IS NOT NULL AND sector != '') AS has_sector,
               SUM(sector IS NULL OR sector = '') AS no_sector
        FROM stock_base_info
    """)
    total, has, no = cursor.fetchone()
    
    cursor.execute("""
        SELECT sector, COUNT(*) AS cnt 
        FROM stock_base_info 
        WHERE sector IS NOT NULL AND sector != ''
        GROUP BY sector ORDER BY cnt DESC LIMIT 15
    """)
    top_sectors = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    print(f"\n{'='*50}")
    print(f"DB 업데이트 결과")
    print(f"{'='*50}")
    print(f"업데이트 성공: {updated}건")
    print(f"DB에 없는 종목: {not_found}건")
    print(f"\nDB 현황: 전체 {total} | sector 있음 {has} | 없음 {no}")
    print(f"\n상위 업종:")
    for sector, cnt in top_sectors:
        print(f"  {sector}: {cnt}건")
    
    return updated


if __name__ == '__main__':
    print("=" * 50)
    print("KRX 업종분류 → DB 업데이트")
    print("=" * 50)
    
    # 1. 최근 영업일 조회
    biz_day = get_latest_biz_day()
    print(f"기준 영업일: {biz_day}\n")
    
    # 2. KRX 크롤링
    krx_df = crawl_krx_sector(biz_day)
    
    # 3. CSV 백업 저장
    os.makedirs('data', exist_ok=True)
    csv_path = f'data/krx_sector_{biz_day}.csv'
    krx_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"\nCSV 백업: {csv_path}")
    
    # 4. DB 업데이트
    updated = update_db_sector(krx_df)
    
    print(f"\n완료! sector 데이터가 채워졌습니다.")
    print(f"다음 단계: 업종 기반 테마 클러스터 분석 진행 가능")
