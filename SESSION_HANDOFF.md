# SESSION HANDOFF — 키움 조건식 백테스트 & 스코어링 프로젝트
**최종 업데이트:** 2026-05-20

---

## 프로젝트 한 줄 요약
키움 조건식의 성과를 검증하고, 과거 데이터를 기반으로 종목의 폭발력을
예측하는 2축 스코어링 모델(안정성 S + 폭발력 E + 클러스터 보너스)을 개발했습니다.
v2.1에서 업종 클러스터 분석을 통합하여 등급 변별력을 추가 확보했습니다.

---

## 현재 상태 (2026-05-20)

### ✅ 완료된 작업
1. **일봉 기반 조건식 재현** — 키움 HTS 조건(A~K + N/O 필터)을 Python으로 구현
2. **프록시 제외 필터** — DB 플래그 없이 일봉 패턴으로 비정상 종목 차단
3. **지표 사전 계산(캐시)** — 2,583종목 × 2024-05-01~2026-05-14 완료
4. **2기간 백테스트 완료**
   - P1 하락/횡보장 (2024/05/19~2025/05/01): 1,959건
   - P2 상승장 (2025/05/19~2026/05/16): 1,901건
   - 합계 3,860건, scan_result 테이블에 저장됨
5. **과적합 검증 통과** — 하락장에서도 승률 99.3%, avg_max +15.91%
6. **스코어링 모델 v2.0** — 2축(S-Score + E-Score) 설계, 코딩, 역산 검증 완료
7. **업종/테마 데이터 통합** (v2.1 신규)
   - backtest_db.market_all → stock_info.stock_base_info.sector + theme_groups 매핑
   - 2,707건 sector, 2,704건 theme 업데이트 완료 (scan_result 매칭률 99.1%)
8. **업종 클러스터 분석 완료** (v2.1 신규)
   - 같은 날 같은 업종 3종목+ 동시 포착 패턴 분석
   - 53개 코스닥 전기·전자 클러스터, 24개 코스닥 기계·장비 클러스터 검증
   - "Cool & Isolated" vs "Hot" 필터 효과 확인
   - 6단계 cluster_label 배점 체계 확립 및 Q21 검증 통과
9. **스코어링 모델 v2.1** — 클러스터 보너스 통합, DB 영구 저장 완료
   - A1 vs D 변별력: avg_max +9.8%p, avg_1m +5.5%p (v2.0: +9.1%p, +4.6%p)
   - 등급 변동 57건(1.5%), 순승격 +25건
   - scan_result에 s_score/e_score/e_score_raw/cluster_bonus/cluster_label/grade/grade_v20 저장

### 🔶 보류/미완료
1. **Track A (HTS 자동 수집)** — Track B로 대체하여 진행 중
2. **C등급 avg_max 이상** — C등급 avg_max(+23.1%)가 B2(+14.6%)보다 높음 (구조적 특성)
3. **실시간 파이프라인** — 일봉 갱신 → 자동 스캔 → 클러스터 탐지 → 스코어링 → 알림
4. **ML 모델** — RandomForest/XGBoost 앙상블 (scan_result 3,860건 학습)

---

## 핵심 파일 & 역할

| 파일 | 역할 | 상태 |
|------|------|------|
| `scoring_model.py` | ScoringModelV2 클래스 (v2.1: 2축 + 클러스터) | ✅ 완성 |
| `cluster_detector.py` | 클러스터 탐지기 (날짜별-업종별 집계) | ✅ 신규 |
| `validate_scoring.py` | v2.1 역산 검증 (클러스터 포함) | ✅ 완성 |
| `save_scores.py` | scan_result에 스코어/등급 일괄 UPDATE | ✅ 신규 |
| `precompute_indicators.py` | 지표 사전 계산 (캐시) | ✅ 완성 |
| `run_scan_backtest.py` | 메인 백테스트 (--fast 지원) | ✅ 완성 |
| `condition_scanner_fast.py` | Fast 모드 조건식 평가 | ✅ 완성 |
| `condition_scanner.py` | 일반 모드 조건식 평가 | ✅ 완성 |
| `exclusion_filter.py` | 프록시 제외 필터 | ✅ 완성 |
| `performance_calculator.py` | 포착 후 수익률 계산 | ✅ 완성 |
| `indicators.py` | 기술적 지표 함수 | ✅ 완성 |
| `config.py` | 설정 (DB, HTS, Backtest) | ✅ 완성 |
| `backtest_report.py` | 백테스트 리포트 | ✅ 완성 |

---

## DB 현황

Copy
[stock_info] └─ stock_base_info # 2,800개 종목 (sector 2,707건 + theme_groups 2,704건)

[stock_information] └─ daily_candles # 일봉 데이터 (전 종목, 수년치)

[backtest_db] ├─ market_all # KRX 전종목 업종/테마 원본 (2,711 주권) ├─ sector_master # 업종/테마 마스터 (200+건) └─ sector_component # 업종/테마별 구성종목

[kiwoom_backtest] ├─ precomputed_indicators # 지표 캐시 ├─ precomputed_exclusion # 제외 판정 캐시 ├─ precompute_log # 사전 계산 로그 ├─ scan_result # ★ 핵심: 백테스트 3,860건 + v2.1 스코어/등급 저장 │ ├─ s_score, e_score, e_score_raw, cluster_bonus, cluster_label │ ├─ grade (v2.1), grade_v20 (v2.0 비교용) │ └─ 기존: trigger_path, 지표값, ret_1w~ret_max └─ scan_log # 일별 처리 로그

Copy
---

## 스코어링 모델 v2.1 핵심 수치

### 등급 매트릭스
Copy          E ≥ 60 (폭발력↑)    E < 60 (폭발력↓)
S ≥ 70 A1 (최우선) A2 (안정우선) 50 ≤ S < 70 B1 (폭발후보) B2 (표준) S < 50 C (관망) D (패스)

Copy
### 클러스터 보너스 (E-Score에 가산)
| 레이블 | 보너스 | 조건 | avg_1m | 승률 |
|--------|--------|------|--------|------|
| A_SEMI_CLUSTER | +15 | 코스닥 기계·장비 3종목+ | +7.4% | 55.4% |
| B_COOL_ELEC | +10 | 코스닥 전기·전자 3종목+ & avg_day_ret <7% | +5.6% | 45.5% |
| B2_WARM_ELEC | +3 | 코스닥 전기·전자 3종목+ & 7~10% | +2.5% | 43.6% |
| D_NO_CLUSTER | 0 | 클러스터 미해당 | +0.4% | 39.7% |
| C_OTHER_CLUSTER | -3 | 기타 업종 3종목+ | -2.3% | 29.9% |
| X_HOT_AVOID | -10 | 코스닥 전기·전자 3종목+ & 10%+ | -5.3% | 29.0% |

### 검증 결과 (전체 3,860건)
| 등급 | 건수(비율) | avg_max | avg_1m | 50%+ | 100%+ |
|------|-----------|---------|--------|------|-------|
| A1 | 540 (14.0%) | +23.3% | +3.1% | 8.5% | 2.6% |
| A2 | 1,503 (38.9%) | +15.5% | +2.5% | 5.4% | 0.6% |
| B1 | 494 (12.8%) | +21.5% | -2.0% | 10.1% | 1.2% |
| B2 | 392 (10.2%) | +14.6% | +0.4% | 4.1% | 0.5% |
| C | 637 (16.5%) | +23.1% | -3.7% | 8.5% | 2.2% |
| D | 294 (7.6%) | +13.4% | -2.5% | 4.8% | 0.0% |

### v2.0 → v2.1 변별력 개선
| 지표 | v2.0 | v2.1 |
|------|------|------|
| A1 vs D avg_max | +9.1%p | +9.8%p |
| A1 vs D avg_1m | +4.6%p | +5.5%p |
| A1 건수 | 524 | 540 |
| 등급 변동 | - | 57건(1.5%), 순승격 +25건 |

---

## 주요 발견 사항

### v2.0 발견 (유지)
1. S와 E의 BB폭 방향이 반대 — S는 좁을수록, E는 넓을수록
2. 초저가(<3천원) — 100%+ 확률 3.5%로 압도적
3. 당일 급등(15%+) — 50%+ 확률 10.2% 최고, 하지만 1m은 -3.58%
4. D등급 트리거 분포: C_RSI70 198건(66%)

### v2.1 발견 (신규)
5. **"Cool Breakout" 패턴** — 코스닥 전기·전자 클러스터에서 avg_day_ret <7%일 때
   승률 55%, avg_1m +4.7%, MDD -25% (hot 대비 절반)
6. **반도체 장비 = 최고 클러스터** — 코스닥 기계·장비 24건 전부 반도체 장비주 포함,
   승률 70.8%, avg_1m +7.0%
7. **기타 업종 클러스터는 역신호** — 328건 avg_1m -2.3%, 과열 후 되돌림
8. **Hot 클러스터 회피 필수** — 당일 avg 10%+ 시 avg_1m -5.3%, 승률 29%
9. **연속 vs 단독**: 코스닥 전기·전자는 단독(isolated)이 더 좋지만,
   코스닥 기계·장비는 연속도 유효 — 업종별 차별화 필터 필요

---

## 다음 세션 권장 작업

### 1순위: 실시간 파이프라인
- 일봉 갱신 시 자동 스캔 → 클러스터 탐지 → 스코어링 → 텔레그램 알림
- A1/A2 + 클러스터 보너스 종목 즉시 알림

### 2순위: 매매 시뮬레이션
- 등급별 전략으로 2년 가상 매매 시뮬레이션
- 수수료/세금/슬리피지 반영한 실현 수익률
- 동시 보유 종목 수 제한(최대 5종목) 적용

### 3순위: ML 모델
- scan_result 3,860건 + 클러스터 피처로 Random Forest/XGBoost
- 규칙 기반 v2.1과 ML 앙상블

---

## .env 구조
DB_HOST=localhost DB_PORT=3306 DB_USER=xxxxx DB_PASSWORD=xxxxx DB_STOCK_INFO=stock_info DB_STOCK_DATA=stock_information DB_BACKTEST=kiwoom_backtest

Copy
---

## 재현 명령어

```bash
cd E:\2026\myCondition

# 스코어링 모델 확인 (테스트 케이스 5개)
python scoring_model.py

# 클러스터 탐지기 단독 실행 (레이블 분포 확인)
python cluster_detector.py

# v2.1 전체 검증 (등급별/클러스터별 성과 + 변동 리포트)
python validate_scoring.py

# scan_result에 스코어/등급 일괄 저장
python save_scores.py

# 새 기간 백테스트
python precompute_indicators.py --start YYYY-MM-DD --end YYYY-MM-DD
python run_scan_backtest.py --start YYYY-MM-DD --end YYYY-MM-DD --fast
'@ [System.IO.File]::WriteAllText("PWD\SESSION_HANDOFF.md", $handoff, [System.Text.UTF8Encoding]::new(false)) Write-Host ">>> SESSION_HANDOFF.md 업데이트 완료" -ForegroundColor Green

═══════════════════════════════════════════════════
README.md 업데이트 (핵심 수치 반영)
═══════════════════════════════════════════════════
$readme = @'

키움 조건식 백테스트 & 스코어링 시스템
HTS 조건식(60/200 이동평균 돌파)을 일봉 데이터로 재현하고, 2축 스코어링 모델로 종목의 안정성(S)과 폭발력(E)을 정량 평가합니다. v2.1에서 업종 클러스터 분석을 통합하여 동반급등 패턴을 감지하고 등급 변별력을 추가 확보했습니다.

항목	수치
검증 기간	2024/05/19 ~ 2026/05/16 (506영업일)
포착 종목	3,860건 (일 평균 7.6)
승률(ret_max > 0)	99.5%
A1 등급 avg_max	+23.3%
A1 등급 avg_1m	+3.1%
A1 vs D avg_max	+9.8%p
A1 vs D avg_1m	+5.5%p
sector 매칭률	99.1%
종목	포착가	최고수익률	등급
경남제약	680	+646%	A1
기가레인	1,579	+428%	A1
센서뷰	1,300	+296%	A1 (B2_WARM_ELEC +3)
케이씨에스	9,360	+194%	A1
SG	-	+181%	A1
2축 스코어링 모델 v2.1
S-Score (안정성, 0-100) → 1개월 수익 안정성 예측

F1. 트리거 경로(25) D+E 최적
F2. 60/200 이격률(20) 3% 이하 최적
F3. RSI(15) 40-60 최적
F4. BB 폭(15) 10-20 최적
F5. 거래량 비율(15) 5-10 최적
F6. MA 기울기(10) 양쪽 상승 최적
E-Score (폭발력, 0-100) → 최고 수익 폭발력 예측

G1. 종가 수준(30) 3천 미만 최적
G2. 당일 등락률(25) 15%+ 최적
G3. 거래량 비율(20) 10+ 최적
G4. BB 폭(15) 40+ 최적
G5. 트리거 경로(10) event 최적
Cluster Bonus (E-Score 가산, -10 ~ +15)

H1. A_SEMI_CLUSTER(+15) 코스닥 기계·장비 3종목+
H2. B_COOL_ELEC(+10) 코스닥 전기·전자 3종목+ & cool(<7%)
H3. B2_WARM_ELEC(+3) 코스닥 전기·전자 3종목+ & warm(7~10%)
H4. D_NO_CLUSTER(0) 베이스라인
H5. C_OTHER_CLUSTER(-3) 기타 업종 3종목+
H6. X_HOT_AVOID(-10) 코스닥 전기·전자 3종목+ & hot(10%+)
E >= 60 (폭발력↑)	E < 60 (폭발력↓)
S >= 70	A1 (최우선)	A2 (안정우선)
50 <= S < 70	B1 (폭발후보)	B2 (표준)
S < 50	C (관망)	D (패스)
검증 결과 (3,860건)
등급	건수	비율	avg_max	avg_1m	50%+	100%+
A1	540	14.0%	+23.3%	+3.1%	8.5%	2.6%
A2	1,503	38.9%	+15.5%	+2.5%	5.4%	0.6%
B1	494	12.8%	+21.5%	-2.0%	10.1%	1.2%
B2	392	10.2%	+14.6%	+0.4%	4.1%	0.5%
C	637	16.5%	+23.1%	-3.7%	8.5%	2.2%
D	294	7.6%	+13.4%	-2.5%	4.8%	0.0%
클러스터 보너스 검증
레이블	보너스	건수	avg_1m	승률
A_SEMI_CLUSTER	+15	83	+7.4%	55.4%
B_COOL_ELEC	+10	66	+5.6%	45.5%
B2_WARM_ELEC	+3	83	+2.5%	43.6%
D_NO_CLUSTER	0	3,257	+0.4%	39.7%
C_OTHER_CLUSTER	-3	339	-2.3%	29.9%
X_HOT_AVOID	-10	32	-5.3%	29.0%

파일	역할
config.py	DB/HTS/백테스트 설정
db_manager.py	DB 연결·테이블·매핑
indicators.py	기술적 지표 (SMA, RSI, BB 등)
exclusion_filter.py	프록시 제외 필터 (8개 룰)
condition_scanner.py	조건식 평가 (일반 모드)
condition_scanner_fast.py	조건식 평가 (Fast 모드)
performance_calculator.py	수익률 계산 (1w/2w/3w/1m/max)
precompute_indicators.py	지표 사전계산 + DB 캐시
run_scan_backtest.py	메인 백테스트 실행
scoring_model.py	v2.1 스코어링 모델
cluster_detector.py	업종 클러스터 탐지기
validate_scoring.py	v2.1 역산 검증
save_scores.py	scan_result 스코어 일괄 저장
backtest_report.py	백테스트 리포트 + 교차검증
SESSION_HANDOFF.md	세션 인수인계 문서

pip install -r requirements.txt
.env에 DB 접속정보 설정
python precompute_indicators.py --start 2024-05-01 --end 2026-05-16
python run_scan_backtest.py --start 2024-05-19 --end 2026-05-16 --fast
python scoring_model.py
python validate_scoring.py
python save_scores.py
기술 스택
Python 3.x
MySQL 8.0
pymysql, pandas, numpy, python-dotenv
종목 마스터(2,800종목), 업종(2,707건), 테마(2,704건)
로드맵
✅ 스코어링 v2.1 + 클러스터 보너스 + DB 영구저장
🔲 실시간 파이프라인 (자동 스캔 → 클러스터 탐지 → 스코어링 → 텔레그램 알림)
🔲 매매 시뮬레이션 (등급별/클러스터별 전략 백테스트)
🔲 ML 앙상블 (RandomForest/XGBoost + 규칙 기반 모델)