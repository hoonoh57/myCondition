---

## 문서 2: Session Handoff

```markdown
# SESSION HANDOFF — 키움 조건식 백테스트 & 스코어링 프로젝트
**최종 업데이트:** 2026-05-20

---

## 프로젝트 한 줄 요약
키움 조건식의 성과를 검증하고, 과거 데이터를 기반으로 종목의 폭발력을
예측하는 2축 스코어링 모델(안정성 S + 폭발력 E)을 개발했습니다.
모델 고도화를 통해 등급별(A1~D) 변별력을 확보하고 백테스트 검증까지 완료했습니다.

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
   - A1 vs D 변별력: avg_max +9.1%p, avg_1m +4.6%p
   - v1.0의 avg_max 역전 문제(-3.27%p) → v2.0에서 해소(+9.1%p)

### 🔶 보류/미완료
1. **Track A (HTS 자동 수집)** — hts_controller.py 좌표 캘리브레이션 미완,
   자동 저장 로직 불안정. Track B로 대체하여 진행 중
2. **C등급 avg_max 이상** — C등급 avg_max(+22.7%)가 B2(+14.7%)보다 높음.
   S < 50이지만 E ≥ 60인 종목이 max가 높은 구조. 등급 경계 조정 필요
3. **Q6 기울기 조합 분석** — scan_result에 ma60_slope/ma200_slope 수치 컬럼 없음
   (boolean ma60_slope_up/ma200_slope_up만 있음). 별도 분석 불가

---

## 핵심 파일 & 역할

| 파일 | 역할 | 상태 |
|------|------|------|
| `scoring_model.py` | ScoringModelV2 클래스 (2축 스코어링) | ✅ 완성 |
| `validate_scoring.py` | 3,860건 역산 검증 스크립트 | ✅ 완성 |
| `precompute_indicators.py` | 지표 사전 계산 (캐시) | ✅ 완성 |
| `run_scan_backtest.py` | 메인 백테스트 (--fast 지원) | ✅ 완성 |
| `condition_scanner_fast.py` | Fast 모드 조건식 평가 | ✅ 완성 |
| `condition_scanner.py` | 일반 모드 조건식 평가 | ✅ 완성 |
| `exclusion_filter.py` | 프록시 제외 필터 | ✅ 완성 |
| `performance_calculator.py` | 포착 후 수익률 계산 | ✅ 완성 |
| `indicators.py` | 기술적 지표 함수 | ✅ 완성 |
| `config.py` | 설정 (DB, HTS, Backtest) | ✅ 완성 |
| `hts_controller.py` | HTS 자동 제어 (Track A) | 🔶 보류 |
| `clipboard_parser.py` | HTS 클립보드 파싱 (Track A) | 🔶 보류 |

---

## DB 현황

MySQL 로컬 (localhost:3306)

[stock_info] └─ stock_base_info # 2,771개 종목 기본정보

[stock_information]
└─ daily_candles # 일봉 데이터 (전 종목, 수년치)

[kiwoom_backtest] ├─ precomputed_indicators # 지표 캐시 (2024-05-01 ~ 2026-05-14) ├─ precomputed_exclusion # 제외 판정 캐시 (2024-05-02 ~ 2026-05-14, 499일) ├─ precompute_log # 사전 계산 로그 (2,700개 종목) │ ├─ computed_start, computed_end 컬럼 존재 │ └─ last_date 컬럼: NULL DEFAULT NULL로 변경됨 ├─ scan_result # ★ 핵심: 백테스트 결과 3,860건 │ ├─ P1: search_date 2024-05-20 ~ 2025-04-30 (1,959건) │ └─ P2: search_date 2025-05-20 ~ 2026-05-15 (1,901건) └─ scan_log # 일별 처리 로그 (522일)

Copy
---

## 스코어링 모델 v2.0 핵심 수치

### 등급 매트릭스
Copy            E ≥ 60 (폭발력↑)    E < 60 (폭발력↓)
S ≥ 70      A1 (최우선)          A2 (안정우선)
50 ≤ S < 70 B1 (폭발후보)        B2 (표준)
S < 50      C  (관망)            D  (패스)
Copy
### 검증 결과 (전체 3,860건)
| 등급 | 건수(비율) | avg_max | avg_1m | 50%+ | 100%+ |
|------|-----------|---------|--------|------|-------|
| A1 | 524 (13.6%) | +23.3% | +2.9% | 8.4% | 2.7% |
| A2 | 1,519 (39.4%) | +15.6% | +2.6% | 5.5% | 0.6% |
| B1 | 489 (12.7%) | +21.5% | -2.0% | 10.2% | 1.2% |
| B2 | 397 (10.3%) | +14.7% | +0.4% | 4.0% | 0.5% |
| C | 633 (16.4%) | +22.7% | -4.1% | 8.1% | 2.2% |
| D | 298 (7.7%) | +14.2% | -1.7% | 5.7% | 0.0% |

### S-Score 팩터 (100점)
F1 트리거(25) + F2 이격률(20) + F3 RSI(15) + F4 BB폭(15) + F5 거래량(15) + F6 기울기(10)

### E-Score 팩터 (100점)  
G1 종가수준(30) + G2 당일등락률(25) + G3 거래량비율(20) + G4 BB폭(15) + G5 트리거(10)

---

## 주요 발견 사항 (다음 세션 참고)

1. **S와 E의 BB폭 방향이 반대** — S는 좁을수록(안정), E는 넓을수록(폭발) 점수 높음
2. **V5 극폭증(10x+)** — S에서는 패널티(3점), E에서는 최고점(20점)
3. **C_RSI70 트리거** — S에서 0점(최악), 양쪽 기간 모두 1m 마이너스
4. **초저가(<3천원)** — 100%+ 확률 3.5%로 전 구간 압도적 (고가 0.0%)
5. **당일 급등(15%+)** — 50%+ 확률 10.2%로 최고, 하지만 1m은 -3.58%
6. **B1의 50%+ 비율(10.2%)이 A1(8.4%)보다 높음** — 불안정하지만 폭발적
7. **P1(하락장)에서 모든 등급 1m 마이너스** — 시장 환경 감지 로직 필요
8. **D등급 트리거 분포**: C_RSI70 198건(66%), E_MA200GC 51건, C+E 43건

---

## 다음 세션 권장 작업 (우선순위 순)

### 1순위: 모델 고도화
- C등급 경계 조정 (B1과 C 사이에 C등급 종목 중 고E를 B1으로 승격)
- scan_result에 score/grade 컬럼 추가하여 DB에 영구 저장
- 등급별 월별 추이 분석으로 시계열 안정성 재확인

### 2순위: 매매 시뮬레이션
- 등급별 전략으로 2년 가상 매매 시뮬레이션
- 수수료/세금/슬리피지 반영한 실현 수익률 산출
- 동시 보유 종목 수 제한(예: 최대 5종목)에 따른 수익 차이

### 3순위: 실시간 파이프라인
- 일봉 갱신 시 자동 스캔 → 스코어링 → 알림 자동화
- 텔레그램 봇 연동 (A1/A2 즉시 알림)

### 4순위: ML 모델
- scan_result 3,860건을 학습 데이터로 Random Forest/XGBoost 훈련
- 규칙 기반 v2.0과 ML 앙상블

---

## .env 구조 (DB 접속)
DB_HOST=localhost DB_PORT=3306 DB_USER=xxxxx DB_PASSWORD=xxxxx DB_STOCK_INFO=stock_info DB_STOCK_DATA=stock_information DB_BACKTEST=kiwoom_backtest

Copy
---

## 재현 명령어 (새 세션에서 바로 실행 가능)

```bash
cd E:\2026\myCondition

# 스코어링 모델 확인
python scoring_model.py

# 기존 검증 재실행
python validate_scoring.py

# 새 기간 백테스트 (지표 캐시 먼저)
python precompute_indicators.py --start YYYY-MM-DD --end YYYY-MM-DD
python run_scan_backtest.py --start YYYY-MM-DD --end YYYY-MM-DD --fast

# 단일 날짜 테스트
python run_scan_backtest.py --date 2026-04-08 --fast
Copy
---