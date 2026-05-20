"""
패턴 리플레이 UI  v2.1

v2.1 변경사항:
    - SCAN_COLUMNS에 cluster_label, cluster_bonus, e_score_raw, grade_v20 추가
    - Treeview에 Clx(클러스터 레이블) 컬럼 추가
    - update_info에 [클러스터] 라인 추가
    - compute_context에 클러스터 보너스 반영
    - 전략분석 탭에 클러스터별 성과 요약 추가

실행:
    python pattern_replay_ui.py

DB 수정 없음.
"""
import csv
import math
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pymysql

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from config import BacktestConfig, DBConfig


# ──────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────
REPORT_DIR = Path(__file__).parent / "outputs" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
MANUAL_SCORE_CSV = REPORT_DIR / "manual_supply_scores.csv"

PATTERN_ITEMS = [
    "ALL", "A_EVENT_LIMITLIKE", "B_D_E_GOLDEN_CORE", "C_D_ONLY_60GC",
    "D_E_ONLY_200GC", "E_RSI_E_MIXED", "F_RSI_D_MIXED", "G_RSI_ONLY",
    "H_COMPLEX_CDE", "Z_OTHER",
]
GRADE_ITEMS = [
    "ALL", "A1", "A2", "B1", "B2",
    "C_HOT", "C_FAST", "C_WATCH", "C_BAD", "D",
]
MAX_FILTER_ITEMS = ["ALL", ">=5", ">=10", ">=20", ">=30", ">=50", ">=100"]

CLUSTER_LABEL_ITEMS = [
    "ALL", "A_SEMI_CLUSTER", "B_COOL_ELEC", "B2_WARM_ELEC",
    "C_OTHER_CLUSTER", "D_NO_CLUSTER", "X_HOT_AVOID",
]

# v2.1: cluster 관련 컬럼 추가
SCAN_COLUMNS = [
    "id", "condition_name", "search_date", "code", "name", "market",
    "trigger_path", "close_price", "volume", "trade_value", "day_return",
    "ma60_200_dist", "rsi14", "bb_width", "vol_ratio_20",
    "ma60_slope_up", "ma200_slope_up",
    "ret_1w", "ret_2w", "ret_3w", "ret_1m", "ret_max", "max_high_date",
    "s_score", "e_score", "e_score_raw", "cluster_bonus", "cluster_label",
    "grade", "grade_v20", "grade_v21", "strategy_v21",
]

POLICIES = {
    "A1":      {"target": 30.0, "stop": -5.0,  "max_days": 20, "enabled": True,  "label": "스윙 최우선"},
    "A2":      {"target": 20.0, "stop": -4.0,  "max_days": 20, "enabled": True,  "label": "안정 스윙"},
    "B1":      {"target": 25.0, "stop": -5.0,  "max_days": 10, "enabled": True,  "label": "단기 폭발"},
    "B2":      {"target": 15.0, "stop": -4.0,  "max_days": 10, "enabled": True,  "label": "표준 단기"},
    "C_FAST":  {"target": 15.0, "stop": -6.0,  "max_days": 2,  "enabled": True,  "label": "초단기 폭발"},
    "C_HOT":   {"target": 8.0,  "stop": -8.0,  "max_days": 2,  "enabled": False, "label": "장중 확인 후보"},
    "C_WATCH": {"target": 0.0,  "stop":  0.0,  "max_days": 0,  "enabled": False, "label": "관찰"},
    "C_BAD":   {"target": 0.0,  "stop":  0.0,  "max_days": 0,  "enabled": False, "label": "제외"},
    "D":       {"target": 0.0,  "stop":  0.0,  "max_days": 0,  "enabled": False, "label": "제외"},
}

PATTERN_GUIDES = {
    "A_EVENT_LIMITLIKE": "event성 급등/상한가성 패턴. 폭발력은 강하지만 1개월 보유 안정성은 낮음. 장중 눌림/재상승 확인 후 단기 익절형으로 접근.",
    "B_D_E_GOLDEN_CORE": "60/200 동시 구조. 강세장에서는 추세 지속성이 개선됨. 시장국면 필터와 함께 스윙 후보로 검토.",
    "C_D_ONLY_60GC": "60일선 돌파 중심. 폭발력보다 보유 안정성이 상대적으로 좋음. 강세장에서는 추세 지속형 후보.",
    "D_E_ONLY_200GC": "200일선 돌파 중심. 표본이 넓어 단독 사용은 약함. 저가/거래량/스코어 필터 필요.",
    "E_RSI_E_MIXED": "RSI 과열 + 200일선 혼합. 일부 C_HOT과 결합 시 강한 사례가 있으나 표본 확인 필요.",
    "F_RSI_D_MIXED": "RSI 과열 + 60일선 혼합. 단독 신뢰보다 가격대/거래량/시장국면 보조 필요.",
    "G_RSI_ONLY": "RSI 과열 단독. 과열 추격 위험이 커 자동매매 주력 패턴에서 제외 또는 낮은 우선순위.",
    "H_COMPLEX_CDE": "RSI+60+200 복합. 구조는 강해 보이나 실제 성과는 추가 필터 필요.",
    "Z_OTHER": "기타 패턴. 별도 검증 전에는 주력 사용 금지.",
}

# 클러스터 레이블 가이드 (v2.1 신규)
CLUSTER_GUIDES = {
    "A_SEMI_CLUSTER": "코스닥 기계·장비 반도체장비 클러스터. 승률 70.8%, avg_1m +7.0%. 동일 업종 ≥3종목 동시 포착. 가장 안정적인 클러스터 신호.",
    "B_COOL_ELEC": "코스닥 전기·전자 Cool(avg_day_ret<7%) 클러스터. 승률 55%, avg_1m +4.7%. 조용한 돌파가 더 좋은 성과.",
    "B2_WARM_ELEC": "코스닥 전기·전자 Warm(7~10%) 클러스터. 승률 43.6%, avg_1m +2.5%. 중간 온도, 기대수익 낮음.",
    "C_OTHER_CLUSTER": "기타 업종 ≥3종목 동시 포착. 평균 성과 부정적(avg_1m -2.3%). 과열 가능성 높음.",
    "D_NO_CLUSTER": "클러스터 미해당. 베이스라인 성과(avg_1m +0.4%).",
    "X_HOT_AVOID": "코스닥 전기·전자 Hot(avg_day_ret≥10%) 클러스터. 승률 20%, avg_1m -5.9%. 회피 권장.",
}

GRADE_BASE = {
    "A1": 35.0, "A2": 28.0, "B1": 26.0, "B2": 18.0,
    "C_FAST": 22.0, "C_HOT": 12.0, "C_WATCH": 5.0, "C_BAD": -10.0, "D": -15.0,
}
PATTERN_BASE = {
    "A_EVENT_LIMITLIKE": 22.0, "C_D_ONLY_60GC": 20.0, "B_D_E_GOLDEN_CORE": 18.0,
    "D_E_ONLY_200GC": 12.0, "E_RSI_E_MIXED": 8.0, "F_RSI_D_MIXED": 5.0,
    "H_COMPLEX_CDE": 5.0, "G_RSI_ONLY": -8.0, "Z_OTHER": 0.0,
}

# v2.1 클러스터 보너스 → context_score에 반영
CLUSTER_CONTEXT_BONUS = {
    "A_SEMI_CLUSTER": 12.0,
    "B_COOL_ELEC": 8.0,
    "B2_WARM_ELEC": 2.0,
    "D_NO_CLUSTER": 0.0,
    "C_OTHER_CLUSTER": -3.0,
    "X_HOT_AVOID": -8.0,
}


# ──────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────
def get_conn(database_name=None):
    return pymysql.connect(
        host=DBConfig.HOST,
        port=DBConfig.PORT,
        user=DBConfig.USER,
        password=DBConfig.PASSWORD,
        database=(database_name or DBConfig.BACKTEST_DB),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def safe_float(value, default=0.0):
    if value is None:
        return default
    try:
        v = float(value)
        if math.isnan(v):
            return default
        return v
    except Exception:
        return default


def safe_int(value, default=0):
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def safe_date_text(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    return str(value)[:10]


def row_key(row):
    return f"{safe_date_text(row.get('search_date'))}|{row.get('code')}"


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


def pattern_group(row):
    nt = norm_trigger(row.get("trigger_path"))
    if nt == "event":
        return "A_EVENT_LIMITLIKE"
    if nt == "D+E":
        return "B_D_E_GOLDEN_CORE"
    if nt == "D":
        return "C_D_ONLY_60GC"
    if nt == "E":
        return "D_E_ONLY_200GC"
    if nt == "C+E":
        return "E_RSI_E_MIXED"
    if nt == "C+D":
        return "F_RSI_D_MIXED"
    if nt == "C":
        return "G_RSI_ONLY"
    if nt == "C+D+E":
        return "H_COMPLEX_CDE"
    return "Z_OTHER"


def cluster_label_short(label):
    """Treeview 표시용 축약"""
    mapping = {
        "A_SEMI_CLUSTER": "A_Semi",
        "B_COOL_ELEC": "B_Cool",
        "B2_WARM_ELEC": "B2_Warm",
        "C_OTHER_CLUSTER": "C_Oth",
        "D_NO_CLUSTER": "-",
        "X_HOT_AVOID": "X_Hot",
    }
    return mapping.get(label, str(label or "-")[:6])


# ──────────────────────────────────────────────
# 수동 점수 관리
# ──────────────────────────────────────────────
def load_manual_scores():
    scores = {}
    if not MANUAL_SCORE_CSV.exists():
        return scores
    with MANUAL_SCORE_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            key = f"{r.get('search_date')}|{r.get('code')}"
            scores[key] = {
                "program": safe_float(r.get("program_score")),
                "institution": safe_float(r.get("institution_score")),
                "foreign": safe_float(r.get("foreign_score")),
                "theme": safe_float(r.get("theme_score")),
                "news": safe_float(r.get("news_score")),
                "note": r.get("note") or "",
            }
    return scores


def save_manual_scores(scores):
    fieldnames = [
        "search_date", "code", "program_score", "institution_score",
        "foreign_score", "theme_score", "news_score", "note", "updated_at",
    ]
    with MANUAL_SCORE_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for key, v in sorted(scores.items()):
            search_date, code = key.split("|", 1)
            writer.writerow({
                "search_date": search_date,
                "code": code,
                "program_score": v.get("program", 0.0),
                "institution_score": v.get("institution", 0.0),
                "foreign_score": v.get("foreign", 0.0),
                "theme_score": v.get("theme", 0.0),
                "news_score": v.get("news", 0.0),
                "note": v.get("note", ""),
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })


def manual_score_total(manual):
    if not manual:
        return 0.0
    return (
        safe_float(manual.get("program"))
        + safe_float(manual.get("institution"))
        + safe_float(manual.get("foreign"))
        + safe_float(manual.get("theme"))
        + safe_float(manual.get("news"))
    )


# ──────────────────────────────────────────────
# 신뢰판정 (v2.1: 클러스터 보너스 반영)
# ──────────────────────────────────────────────
def compute_context(row, manual_scores):
    pg = pattern_group(row)
    grade = str(row.get("grade_v21") or "")
    score = GRADE_BASE.get(grade, 0.0) + PATTERN_BASE.get(pg, 0.0)

    trade_value = safe_float(row.get("trade_value"))
    vol_ratio = safe_float(row.get("vol_ratio_20"))
    day_return = safe_float(row.get("day_return"))
    bb_width = safe_float(row.get("bb_width"))
    rsi = safe_float(row.get("rsi14"))
    s_score = safe_float(row.get("s_score"))
    e_score = safe_float(row.get("e_score"))

    if trade_value >= 100_000_000_000:
        score += 12.0
    elif trade_value >= 30_000_000_000:
        score += 8.0
    elif trade_value >= 10_000_000_000:
        score += 5.0

    if vol_ratio >= 20:
        score += 10.0
    elif vol_ratio >= 10:
        score += 7.0
    elif vol_ratio >= 5:
        score += 4.0

    if 3.0 <= day_return <= 18.0:
        score += 6.0
    elif day_return > 25.0:
        score -= 5.0

    if s_score >= 70:
        score += 8.0
    elif s_score >= 50:
        score += 4.0

    if e_score >= 80:
        score += 8.0
    elif e_score >= 60:
        score += 4.0

    if 10.0 <= bb_width <= 40.0:
        score += 4.0
    elif bb_width > 60.0:
        score -= 3.0

    if rsi >= 85.0:
        score -= 4.0

    if pg == "G_RSI_ONLY":
        score -= 10.0

    if grade in ("C_BAD", "D"):
        score -= 20.0

    # v2.1: 클러스터 보너스 반영
    clx = str(row.get("cluster_label") or "D_NO_CLUSTER")
    score += CLUSTER_CONTEXT_BONUS.get(clx, 0.0)

    score += manual_score_total(manual_scores.get(row_key(row)))

    if grade in ("D", "C_BAD"):
        action = "SKIP"
    elif pg == "G_RSI_ONLY" and score < 70:
        action = "SKIP_RSI"
    elif score >= 90:
        action = "PRIORITY"
    elif score >= 75:
        action = "STRONG"
    elif score >= 60:
        action = "CONFIRM"
    else:
        action = "WATCH"

    return score, action


# ──────────────────────────────────────────────
# DB 조회
# ──────────────────────────────────────────────
def build_query(start_text, end_text, grade):
    clauses = ["condition_name = %s"]
    params = [BacktestConfig.CONDITION_NAME]
    if start_text:
        clauses.append("search_date >= %s")
        params.append(start_text)
    if end_text:
        clauses.append("search_date <= %s")
        params.append(end_text)
    if grade and grade != "ALL":
        clauses.append("grade_v21 = %s")
        params.append(grade)
    sql = f"""
        SELECT {', '.join(SCAN_COLUMNS)}
        FROM scan_result
        WHERE {' AND '.join(clauses)}
        ORDER BY search_date DESC, ret_max DESC, code
    """
    return sql, params


def fetch_scan_rows(start_text, end_text, pattern, grade, max_filter, cluster_filter="ALL"):
    conn = get_conn(DBConfig.BACKTEST_DB)
    try:
        sql, params = build_query(start_text, end_text, grade)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    if pattern and pattern != "ALL":
        rows = [row for row in rows if pattern_group(row) == pattern]
    if max_filter and max_filter != "ALL":
        threshold = safe_float(max_filter.replace(">=", ""))
        rows = [row for row in rows if safe_float(row.get("ret_max")) >= threshold]
    # v2.1: 클러스터 필터
    if cluster_filter and cluster_filter != "ALL":
        rows = [row for row in rows if str(row.get("cluster_label") or "") == cluster_filter]

    return rows


def _rows_to_df(rows):
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def fetch_candles(code, center_date_text, before_bars=20, after_bars=120, warmup_bars=260):
    table_name = f"`{DBConfig.STOCK_DATA_DB}`.`daily_candles`"
    sql_before = f"""
        SELECT date, open, high, low, close, volume
        FROM {table_name}
        WHERE code = %s AND date < %s
        ORDER BY date DESC
        LIMIT %s
    """
    sql_after = f"""
        SELECT date, open, high, low, close, volume
        FROM {table_name}
        WHERE code = %s AND date >= %s
        ORDER BY date ASC
        LIMIT %s
    """
    conn = get_conn(DBConfig.STOCK_DATA_DB)
    try:
        with conn.cursor() as cur:
            cur.execute(sql_before, (code, center_date_text, warmup_bars))
            before_rows = list(cur.fetchall())
            cur.execute(sql_after, (code, center_date_text, after_bars))
            after_rows = list(cur.fetchall())
    finally:
        conn.close()

    before_rows.reverse()
    df = _rows_to_df(before_rows + after_rows)
    if df.empty:
        return df

    df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    df["vol_ma20"] = df["volume"].rolling(20).mean()

    signal_dt = pd.to_datetime(center_date_text)
    display_before = df[df["date"] < signal_dt].tail(before_bars)
    display_after = df[df["date"] >= signal_dt].head(after_bars)
    out = pd.concat([display_before, display_after], ignore_index=True)
    out.attrs["before_count"] = len(display_before)
    out.attrs["after_count"] = len(display_after)
    out.attrs["first_date"] = safe_date_text(out["date"].min()) if not out.empty else ""
    out.attrs["last_date"] = safe_date_text(out["date"].max()) if not out.empty else ""
    return out


# ──────────────────────────────────────────────
# 매매 시뮬레이션
# ──────────────────────────────────────────────
def simulate_policy(row, candles):
    grade = row.get("grade_v21")
    policy = POLICIES.get(grade, POLICIES["D"])
    entry_price = safe_float(row.get("close_price"))
    result = {
        "enabled": policy["enabled"], "label": policy["label"],
        "target": policy["target"], "stop": policy["stop"],
        "max_days": policy["max_days"],
        "exit_date": "", "exit_price": 0.0,
        "exit_reason": "disabled", "gross_return": 0.0, "net_return": 0.0,
    }
    if not policy["enabled"] or entry_price <= 0 or candles.empty:
        return result

    signal_dt = pd.to_datetime(safe_date_text(row.get("search_date")))
    future = candles[candles["date"] > signal_dt].head(policy["max_days"])
    if future.empty:
        result["exit_reason"] = "no_future_candle"
        return result

    target_price = entry_price * (1.0 + policy["target"] / 100.0)
    stop_price = entry_price * (1.0 + policy["stop"] / 100.0)
    exit_price = 0.0
    exit_reason = ""
    exit_date = ""

    for _, candle in future.iterrows():
        high_price = safe_float(candle.get("high"))
        low_price = safe_float(candle.get("low"))
        close_price = safe_float(candle.get("close"))
        candle_date = safe_date_text(candle.get("date"))
        if high_price >= target_price and low_price <= stop_price:
            exit_price = stop_price
            exit_reason = "stop_same_day"
            exit_date = candle_date
            break
        if high_price >= target_price:
            exit_price = target_price
            exit_reason = "target"
            exit_date = candle_date
            break
        if low_price <= stop_price:
            exit_price = stop_price
            exit_reason = "stop"
            exit_date = candle_date
            break
        exit_price = close_price
        exit_reason = "time_exit"
        exit_date = candle_date

    gross = (exit_price - entry_price) / entry_price * 100.0 if entry_price > 0 else 0.0
    result.update({
        "exit_date": exit_date, "exit_price": exit_price,
        "exit_reason": exit_reason, "gross_return": gross, "net_return": gross - 0.53,
    })
    return result


# ──────────────────────────────────────────────
# 차트 그리기
# ──────────────────────────────────────────────
def draw_candles(ax, df):
    if df.empty:
        return
    width = 0.58
    for i, row in df.iterrows():
        o = safe_float(row.get("open"))
        h = safe_float(row.get("high"))
        l = safe_float(row.get("low"))
        c = safe_float(row.get("close"))
        lower = min(o, c)
        height = abs(c - o)
        if height == 0:
            height = max(c * 0.001, 1.0)
        color = "r" if c >= o else "b"
        ax.vlines(i, l, h, color=color, linewidth=0.8)
        ax.add_patch(Rectangle(
            (i - width / 2.0, lower), width, height,
            fill=(c >= o), facecolor=color, edgecolor=color, linewidth=0.9,
        ))
    x = list(range(len(df)))
    ax.plot(x, df["close"], linewidth=0.9, alpha=0.5, label="Close")
    ax.plot(x, df["ma20"], linewidth=1.0, label="MA20")
    ax.plot(x, df["ma60"], linewidth=1.0, label="MA60")
    ax.plot(x, df["ma200"], linewidth=1.0, label="MA200")


# ──────────────────────────────────────────────
# 수급 다이얼로그
# ──────────────────────────────────────────────
class SupplyDialog(tk.Toplevel):
    def __init__(self, master, rows, on_apply):
        super().__init__(master)
        self.title("수급/테마/뉴스 분석 등록")
        self.geometry("520x360")
        self.rows = rows
        self.on_apply = on_apply
        self.vars = {}
        self._build()

    def _build(self):
        ttk.Label(self, text=f"대상 종목: {len(self.rows)}건").pack(anchor=tk.W, padx=10, pady=8)
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill=tk.X)
        fields = [
            ("program", "프로그램 순매수 점수"),
            ("institution", "기관 매수 점수"),
            ("foreign", "외국인 매수 점수"),
            ("theme", "테마 형성 점수"),
            ("news", "뉴스/재료 점수"),
        ]
        for i, (key, label) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky=tk.W, pady=3)
            var = tk.StringVar(value="0")
            ttk.Entry(frame, textvariable=var, width=10).grid(row=i, column=1, sticky=tk.W, padx=6)
            ttk.Label(frame, text="0~20").grid(row=i, column=2, sticky=tk.W)
            self.vars[key] = var
        ttk.Label(frame, text="메모").grid(row=5, column=0, sticky=tk.NW, pady=3)
        self.note = tk.Text(frame, height=5, width=42)
        self.note.grid(row=5, column=1, columnspan=2, sticky=tk.W, padx=6)
        btns = ttk.Frame(self, padding=10)
        btns.pack(fill=tk.X)
        ttk.Button(btns, text="등록", command=self.apply).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="취소", command=self.destroy).pack(side=tk.RIGHT, padx=4)

    def apply(self):
        values = {}
        for key, var in self.vars.items():
            v = max(0.0, min(20.0, safe_float(var.get())))
            values[key] = v
        values["note"] = self.note.get("1.0", tk.END).strip()
        self.on_apply(self.rows, values)
        self.destroy()


# ──────────────────────────────────────────────
# 메인 UI
# ──────────────────────────────────────────────
class PatternReplayUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("myCondition Pattern Replay UI v2.1")
        self.geometry("1860x1000")
        self.rows = []
        self.checked = set()
        self.manual_scores = load_manual_scores()
        self._build_ui()

    def _build_ui(self):
        root = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        root.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(root, padding=8)
        right = ttk.Frame(root, padding=8)
        root.add(left, weight=1)
        root.add(right, weight=4)

        # ── 필터 ──
        filter_box = ttk.LabelFrame(left, text="Filter", padding=8)
        filter_box.pack(fill=tk.X)

        ttk.Label(filter_box, text="Start").grid(row=0, column=0, sticky=tk.W)
        self.start_var = tk.StringVar(value="2024-05-20")
        ttk.Entry(filter_box, textvariable=self.start_var, width=12).grid(row=0, column=1, sticky=tk.W, padx=4)

        ttk.Label(filter_box, text="End").grid(row=1, column=0, sticky=tk.W)
        self.end_var = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(filter_box, textvariable=self.end_var, width=12).grid(row=1, column=1, sticky=tk.W, padx=4)

        ttk.Label(filter_box, text="Pattern").grid(row=2, column=0, sticky=tk.W)
        self.pattern_var = tk.StringVar(value="ALL")
        ttk.Combobox(filter_box, textvariable=self.pattern_var, values=PATTERN_ITEMS, state="readonly", width=24).grid(row=2, column=1, sticky=tk.W, padx=4)

        ttk.Label(filter_box, text="Grade").grid(row=3, column=0, sticky=tk.W)
        self.grade_var = tk.StringVar(value="ALL")
        ttk.Combobox(filter_box, textvariable=self.grade_var, values=GRADE_ITEMS, state="readonly", width=12).grid(row=3, column=1, sticky=tk.W, padx=4)

        ttk.Label(filter_box, text="Max%").grid(row=4, column=0, sticky=tk.W)
        self.max_filter_var = tk.StringVar(value="ALL")
        ttk.Combobox(filter_box, textvariable=self.max_filter_var, values=MAX_FILTER_ITEMS, state="readonly", width=12).grid(row=4, column=1, sticky=tk.W, padx=4)

        # v2.1: 클러스터 필터 추가
        ttk.Label(filter_box, text="Cluster").grid(row=5, column=0, sticky=tk.W)
        self.cluster_var = tk.StringVar(value="ALL")
        ttk.Combobox(filter_box, textvariable=self.cluster_var, values=CLUSTER_LABEL_ITEMS, state="readonly", width=20).grid(row=5, column=1, sticky=tk.W, padx=4)

        ttk.Label(filter_box, text="Before").grid(row=6, column=0, sticky=tk.W)
        self.before_var = tk.StringVar(value="20")
        ttk.Entry(filter_box, textvariable=self.before_var, width=8).grid(row=6, column=1, sticky=tk.W, padx=4)

        ttk.Label(filter_box, text="After").grid(row=7, column=0, sticky=tk.W)
        self.after_var = tk.StringVar(value="120")
        ttk.Entry(filter_box, textvariable=self.after_var, width=8).grid(row=7, column=1, sticky=tk.W, padx=4)

        ttk.Button(filter_box, text="조회", command=self.load_rows).grid(row=8, column=0, columnspan=2, sticky=tk.EW, pady=5)
        ttk.Button(filter_box, text="선택 체크/해제", command=self.toggle_selected).grid(row=9, column=0, columnspan=2, sticky=tk.EW, pady=2)
        ttk.Button(filter_box, text="체크 수급분석", command=self.open_checked_supply).grid(row=10, column=0, columnspan=2, sticky=tk.EW, pady=2)
        ttk.Button(filter_box, text="조회전체 수급분석", command=self.open_all_supply).grid(row=11, column=0, columnspan=2, sticky=tk.EW, pady=2)

        # ── 후보 리스트 ──
        list_box = ttk.LabelFrame(left, text="Candidates", padding=4)
        list_box.pack(fill=tk.BOTH, expand=True, pady=8)
        # v2.1: clx 컬럼 추가
        columns = ("chk", "date", "code", "name", "pattern", "grade", "retmax", "clx", "ctx", "action", "s", "e")
        self.tree = ttk.Treeview(list_box, columns=columns, show="headings", height=28)
        headings = {
            "chk": "✓", "date": "Date", "code": "Code", "name": "Name",
            "pattern": "Pattern", "grade": "G", "retmax": "Max%",
            "clx": "Clx", "ctx": "Ctx", "action": "Action", "s": "S", "e": "E",
        }
        widths = {
            "chk": 30, "date": 82, "code": 66, "name": 115, "pattern": 135,
            "grade": 62, "retmax": 62, "clx": 62, "ctx": 55, "action": 76,
            "s": 42, "e": 42,
        }
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor=tk.W)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(list_box, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", self.on_double_click)
        self.count_var = tk.StringVar(value="0 rows")
        ttk.Label(left, textvariable=self.count_var).pack(anchor=tk.W)

        # ── 차트 ──
        chart_box = ttk.LabelFrame(right, text="Chart Replay", padding=4)
        chart_box.pack(fill=tk.BOTH, expand=True)
        self.fig = Figure(figsize=(12, 7), dpi=100)
        self.ax_price = self.fig.add_subplot(2, 1, 1)
        self.ax_vol = self.fig.add_subplot(2, 1, 2, sharex=self.ax_price)
        self.canvas = FigureCanvasTkAgg(self.fig, master=chart_box)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        toolbar = NavigationToolbar2Tk(self.canvas, chart_box)
        toolbar.update()

        info_box = ttk.LabelFrame(right, text="Logic / Pattern / Cluster Guide / Trade Result", padding=8)
        info_box.pack(fill=tk.X, pady=8)
        self.info_text = tk.Text(info_box, height=15, wrap=tk.WORD)
        self.info_text.pack(fill=tk.X)

    # ── 행 갱신 ──
    def row_context(self, row):
        return compute_context(row, self.manual_scores)

    def refresh_tree_row(self, idx):
        row = self.rows[idx]
        ctx, action = self.row_context(row)
        key = row_key(row)
        clx = cluster_label_short(row.get("cluster_label"))
        self.tree.item(str(idx), values=(
            "☑" if key in self.checked else "☐",
            safe_date_text(row.get("search_date")),
            row.get("code"),
            row.get("name"),
            pattern_group(row),
            row.get("grade_v21"),
            f"{safe_float(row.get('ret_max')):.2f}",
            clx,
            f"{ctx:.1f}",
            action,
            row.get("s_score"),
            row.get("e_score"),
        ))

    def load_rows(self):
        try:
            self.rows = fetch_scan_rows(
                self.start_var.get().strip(),
                self.end_var.get().strip(),
                self.pattern_var.get().strip(),
                self.grade_var.get().strip(),
                self.max_filter_var.get().strip(),
                self.cluster_var.get().strip(),
            )
        except Exception as ex:
            messagebox.showerror("DB 조회 실패", str(ex))
            return

        self.checked = set()
        self.tree.delete(*self.tree.get_children())
        for idx, _row in enumerate(self.rows):
            self.tree.insert("", tk.END, iid=str(idx), values=("",) * 12)
            self.refresh_tree_row(idx)
        self.count_var.set(f"{len(self.rows)} rows")
        if self.rows:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
            self.show_row(self.rows[0])

    def on_select(self, _event):
        selected = self.tree.selection()
        if not selected:
            return
        idx = int(selected[0])
        if 0 <= idx < len(self.rows):
            self.show_row(self.rows[idx])

    def on_double_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        if col == "#1":
            self.toggle_selected()

    def toggle_selected(self):
        selected = self.tree.selection()
        for iid in selected:
            idx = int(iid)
            key = row_key(self.rows[idx])
            if key in self.checked:
                self.checked.remove(key)
            else:
                self.checked.add(key)
            self.refresh_tree_row(idx)

    def selected_rows_by_checked(self):
        return [row for row in self.rows if row_key(row) in self.checked]

    def open_checked_supply(self):
        rows = self.selected_rows_by_checked()
        if not rows:
            messagebox.showinfo("대상 없음", "체크된 종목이 없습니다.")
            return
        SupplyDialog(self, rows, self.apply_manual_scores)

    def open_all_supply(self):
        if not self.rows:
            messagebox.showinfo("대상 없음", "현재 조회된 종목이 없습니다.")
            return
        SupplyDialog(self, list(self.rows), self.apply_manual_scores)

    def apply_manual_scores(self, rows, values):
        for row in rows:
            self.manual_scores[row_key(row)] = dict(values)
        save_manual_scores(self.manual_scores)
        for idx in range(len(self.rows)):
            self.refresh_tree_row(idx)
        selected = self.tree.selection()
        if selected:
            idx = int(selected[0])
            self.show_row(self.rows[idx])
        messagebox.showinfo("등록 완료", f"{len(rows)}건 수급/테마/뉴스 점수를 등록했습니다.")

    def show_row(self, row):
        try:
            before_bars = int(self.before_var.get().strip())
            after_bars = int(self.after_var.get().strip())
            candles = fetch_candles(
                row.get("code"),
                safe_date_text(row.get("search_date")),
                before_bars=before_bars,
                after_bars=after_bars,
            )
        except Exception as ex:
            messagebox.showerror("캔들 조회 실패", str(ex))
            return
        trade_result = simulate_policy(row, candles)
        self.draw_chart(row, candles, trade_result)
        self.update_info(row, candles, trade_result)

    def draw_chart(self, row, candles, trade_result):
        self.ax_price.clear()
        self.ax_vol.clear()
        if candles.empty:
            self.ax_price.set_title("No candle data")
            self.canvas.draw()
            return

        signal_date_text = safe_date_text(row.get("search_date"))
        signal_idx_list = candles.index[candles["date"] == pd.to_datetime(signal_date_text)].tolist()
        signal_idx = signal_idx_list[0] if signal_idx_list else None

        max_high_date = safe_date_text(row.get("max_high_date"))
        max_idx = None
        if max_high_date:
            max_idx_list = candles.index[candles["date"] == pd.to_datetime(max_high_date)].tolist()
            max_idx = max_idx_list[0] if max_idx_list else None

        entry_price = safe_float(row.get("close_price"))
        grade = row.get("grade_v21")
        policy = POLICIES.get(grade, POLICIES["D"])
        target_price = entry_price * (1.0 + policy["target"] / 100.0) if policy["target"] != 0 else None
        stop_price = entry_price * (1.0 + policy["stop"] / 100.0) if policy["stop"] != 0 else None

        draw_candles(self.ax_price, candles)

        if signal_idx is not None:
            self.ax_price.axvline(signal_idx, color="purple", linestyle="--", linewidth=1.3, label="Signal")
            self.ax_vol.axvline(signal_idx, color="purple", linestyle="--", linewidth=1.0)
        if max_idx is not None:
            self.ax_price.axvline(max_idx, color="green", linestyle=":", linewidth=1.2, label="MaxHigh")
        if entry_price > 0:
            self.ax_price.axhline(entry_price, color="gray", linestyle="--", linewidth=0.9, label="Entry")
        if target_price:
            self.ax_price.axhline(target_price, color="red", linestyle="-.", linewidth=0.9, label="Target")
        if stop_price:
            self.ax_price.axhline(stop_price, color="blue", linestyle="-.", linewidth=0.9, label="Stop")

        exit_date = trade_result.get("exit_date")
        if exit_date:
            exit_idx_list = candles.index[candles["date"] == pd.to_datetime(exit_date)].tolist()
            if exit_idx_list:
                self.ax_price.axvline(exit_idx_list[0], color="orange", linestyle="-.", linewidth=1.2, label="Exit")

        # v2.1: 타이틀에 클러스터 레이블 추가
        clx = cluster_label_short(row.get("cluster_label"))
        title = (
            f"{signal_date_text} {row.get('code')} {row.get('name')} | "
            f"{pattern_group(row)} | {grade} | Clx:{clx} | "
            f"retMax {safe_float(row.get('ret_max')):.2f}%"
        )
        self.ax_price.set_title(title, fontsize=10)
        self.ax_price.grid(True, alpha=0.25)
        self.ax_price.legend(loc="upper left", fontsize=8)

        x = list(range(len(candles)))
        self.ax_vol.bar(x, candles["volume"], width=0.65, color="gray", alpha=0.6, label="Volume")
        self.ax_vol.plot(x, candles["vol_ma20"], linewidth=1.0, color="orange", label="VolMA20")
        self.ax_vol.grid(True, alpha=0.25)
        self.ax_vol.legend(loc="upper left", fontsize=8)

        step = max(1, len(candles) // 10)
        ticks = list(range(0, len(candles), step))
        labels = [safe_date_text(candles.iloc[i]["date"])[5:] for i in ticks]
        self.ax_vol.set_xticks(ticks)
        self.ax_vol.set_xticklabels(labels, rotation=35, ha="right")
        self.fig.tight_layout()
        self.canvas.draw()

    def update_info(self, row, candles, trade_result):
        pg = pattern_group(row)
        grade = row.get("grade_v21")
        policy = POLICIES.get(grade, POLICIES["D"])
        guide = PATTERN_GUIDES.get(pg, "가이드 없음")
        before_count = candles.attrs.get("before_count", 0) if not candles.empty else 0
        after_count = candles.attrs.get("after_count", 0) if not candles.empty else 0
        first_date = candles.attrs.get("first_date", "") if not candles.empty else ""
        last_date = candles.attrs.get("last_date", "") if not candles.empty else ""
        ctx, action = self.row_context(row)
        manual = self.manual_scores.get(row_key(row), {})

        # v2.1: 클러스터 정보
        clx_label = str(row.get("cluster_label") or "D_NO_CLUSTER")
        clx_bonus = safe_int(row.get("cluster_bonus"))
        e_raw = safe_int(row.get("e_score_raw"))
        e_final = safe_int(row.get("e_score"))
        clx_guide = CLUSTER_GUIDES.get(clx_label, "")

        lines = []
        lines.append(f"[종목] {safe_date_text(row.get('search_date'))} {row.get('code')} {row.get('name')} / {row.get('market')}")
        lines.append(f"[신뢰판정] context_score={ctx:.1f} / action={action} / manual_supply_total={manual_score_total(manual):.1f}")
        lines.append(f"[수급수동] program={safe_float(manual.get('program')):.1f}, inst={safe_float(manual.get('institution')):.1f}, foreign={safe_float(manual.get('foreign')):.1f}, theme={safe_float(manual.get('theme')):.1f}, news={safe_float(manual.get('news')):.1f}")
        if manual.get("note"):
            lines.append(f"[수급메모] {manual.get('note')}")
        lines.append(f"[차트범위] before={before_count}봉 / after={after_count}봉 / {first_date} ~ {last_date}")
        lines.append(f"[패턴] {pg} / trigger_path={row.get('trigger_path')}")
        lines.append(f"[등급] v20={row.get('grade_v20')} / v21={grade} / strategy={row.get('strategy_v21')}")
        lines.append(f"[스코어] S={row.get('s_score')} E_raw={e_raw} + cluster_bonus={clx_bonus:+d} = E={e_final} / RSI14={safe_float(row.get('rsi14')):.2f} / MA60-200 dist={safe_float(row.get('ma60_200_dist')):.2f}")
        lines.append(f"[클러스터] label={clx_label} / bonus={clx_bonus:+d} / {clx_guide}")
        lines.append(f"[지표] day_return={safe_float(row.get('day_return')):.2f}% / vol_ratio20={safe_float(row.get('vol_ratio_20')):.2f} / BB width={safe_float(row.get('bb_width')):.2f}")
        lines.append(f"[성과] ret_1w={safe_float(row.get('ret_1w')):.2f}% / ret_2w={safe_float(row.get('ret_2w')):.2f}% / ret_3w={safe_float(row.get('ret_3w')):.2f}% / ret_1m={safe_float(row.get('ret_1m')):.2f}% / ret_max={safe_float(row.get('ret_max')):.2f}%")
        lines.append(f"[매매정책] {policy['label']} / enabled={policy['enabled']} / target={policy['target']}% / stop={policy['stop']}% / max_days={policy['max_days']}")
        lines.append(f"[매매결과] exit={trade_result.get('exit_date')} / reason={trade_result.get('exit_reason')} / gross={trade_result.get('gross_return'):.2f}% / net={trade_result.get('net_return'):.2f}%")
        lines.append(f"[패턴 가이드] {guide}")
        self.info_text.delete("1.0", tk.END)
        self.info_text.insert(tk.END, "\n".join(lines))


def main():
    app = PatternReplayUI()
    app.mainloop()


if __name__ == "__main__":
    main()
