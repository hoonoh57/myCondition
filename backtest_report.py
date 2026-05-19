"""
백테스트 결과 분석 + Track A/B 교차 검증

Usage:
    python backtest_report.py
    python backtest_report.py --compare   # Track A vs B 교차검증
"""
import pymysql
import pandas as pd
from config import DBConfig
import logging

logger = logging.getLogger(__name__)


def get_conn():
    return pymysql.connect(
        host=DBConfig.HOST, port=DBConfig.PORT,
        user=DBConfig.USER, password=DBConfig.PASSWORD,
        database=DBConfig.BACKTEST_DB,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )


def report_scan_results():
    """Track B (scan_result) 통계 리포트"""
    conn = get_conn()
    try:
        df = pd.read_sql("""
            SELECT * FROM scan_result
            ORDER BY search_date, code
        """, conn)
    finally:
        conn.close()

    if df.empty:
        print("데이터 없음")
        return

    print("=" * 70)
    print("  Track B 백테스트 결과 통계")
    print("=" * 70)

    # 전체 통계
    total = len(df)
    winners = len(df[df['ret_max'] > 0])
    win_rate = winners / total * 100 if total > 0 else 0
    avg_max = df['ret_max'].mean()
    avg_1m = df['ret_1m'].mean()

    print(f"\n  총 포착 종목수: {total}")
    print(f"  승률 (ret_max > 0): {win_rate:.1f}%")
    print(f"  평균 최고수익률: {avg_max:.2f}%")
    print(f"  평균 1개월수익률: {avg_1m:.2f}%")

    # 트리거 경로별 통계
    print(f"\n  {'─'*50}")
    print(f"  트리거 경로별 성과")
    print(f"  {'─'*50}")

    for path, group in df.groupby('trigger_path'):
        cnt = len(group)
        wr = len(group[group['ret_max'] > 0]) / cnt * 100
        avg = group['ret_max'].mean()
        print(f"  {path:<25} {cnt:>4}건  승률 {wr:5.1f}%  평균max {avg:7.2f}%")

    # 60/200 이격률 구간별
    print(f"\n  {'─'*50}")
    print(f"  60/200이평 이격률 구간별 성과")
    print(f"  {'─'*50}")

    df['dist_bin'] = pd.cut(
        df['ma60_200_dist'],
        bins=[0, 3, 7, 15, 100],
        labels=['<3%(수렴)', '3-7%(근접)', '7-15%(이격)', '>15%(역배열)']
    )
    for label, group in df.groupby('dist_bin', observed=True):
        if len(group) == 0:
            continue
        cnt = len(group)
        wr = len(group[group['ret_max'] > 0]) / cnt * 100
        avg = group['ret_max'].mean()
        print(f"  {label:<20} {cnt:>4}건  승률 {wr:5.1f}%  평균max {avg:7.2f}%")

    # 월별 추이
    print(f"\n  {'─'*50}")
    print(f"  월별 포착 건수 및 성과")
    print(f"  {'─'*50}")

    df['month'] = pd.to_datetime(df['search_date']).dt.to_period('M')
    for month, group in df.groupby('month'):
        cnt = len(group)
        avg = group['ret_max'].mean()
        wr = len(group[group['ret_max'] > 0]) / cnt * 100 if cnt > 0 else 0
        print(f"  {month}  {cnt:>4}건  승률 {wr:5.1f}%  평균max {avg:7.2f}%")

    # Top 20 종목
    print(f"\n  {'─'*50}")
    print(f"  최고수익률 Top 20")
    print(f"  {'─'*50}")

    top = df.nlargest(20, 'ret_max')
    for _, row in top.iterrows():
        print(
            f"  {row['search_date']}  {row['name']:<12} "
            f"({row['code']})  max:{row['ret_max']:+7.2f}%  "
            f"경로:{row['trigger_path']}"
        )


def compare_tracks():
    """Track A (HTS 수집) vs Track B (일봉 스캔) 교차 검증"""
    conn = get_conn()
    try:
        # Track A 데이터
        try:
            df_a = pd.read_sql("""
                SELECT search_date, code, name, ret_max,
                       'track_a' as source
                FROM condition_perf_result
            """, conn)
        except Exception:
            df_a = pd.DataFrame()

        # Track B 데이터
        try:
            df_b = pd.read_sql("""
                SELECT search_date, code, name, ret_max,
                       trigger_path, ma60_200_dist,
                       'track_b' as source
                FROM scan_result
            """, conn)
        except Exception:
            df_b = pd.DataFrame()
    finally:
        conn.close()

    if df_a.empty and df_b.empty:
        print("교차검증할 데이터 없음")
        return

    print("=" * 70)
    print("  Track A (HTS) vs Track B (일봉) 교차 검증")
    print("=" * 70)

    if not df_a.empty and not df_b.empty:
        # 동일 날짜-종목 매칭
        merged = pd.merge(
            df_a[['search_date', 'code', 'name', 'ret_max']],
            df_b[['search_date', 'code', 'name', 'ret_max']],
            on=['search_date', 'code'],
            suffixes=('_hts', '_scan'),
            how='outer',
            indicator=True,
        )

        both = merged[merged['_merge'] == 'both']
        only_a = merged[merged['_merge'] == 'left_only']
        only_b = merged[merged['_merge'] == 'right_only']

        print(f"\n  양쪽 모두 포착: {len(both)}건")
        print(f"  HTS에만 있음: {len(only_a)}건")
        print(f"  일봉스캔에만 있음: {len(only_b)}건")

        if not both.empty:
            # ret_max 일치율
            close_match = len(both[
                (both['ret_max_hts'] - both['ret_max_scan']).abs() < 3.0
            ])
            print(f"  ret_max 오차 <3%p: {close_match}/{len(both)}건")

        if not only_a.empty:
            print(f"\n  HTS에만 포착된 종목 (일봉 조건식 누락 → 조건 보정 필요):")
            for _, row in only_a.head(10).iterrows():
                print(f"    {row['search_date']}  {row['name_hts']}({row['code']})")

        if not only_b.empty:
            print(f"\n  일봉에만 포착된 종목 (HTS에서 제외 → 과잉 포착 확인 필요):")
            for _, row in only_b.head(10).iterrows():
                print(f"    {row['search_date']}  {row['name_scan']}({row['code']})")

    print("\n" + "=" * 70)


if __name__ == '__main__':
    import sys
    if '--compare' in sys.argv:
        compare_tracks()
    else:
        report_scan_results()
