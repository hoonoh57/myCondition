"""
수급/테마/뉴스 정밀 분석 워크벤치

목적:
    조건검색 포착 종목 중 급등 성공군 또는 관심 후보를 대상으로
    프로그램/기관/외국인/테마/뉴스 점수를 한 종목씩 정밀 입력하고 저장합니다.

핵심:
    - pattern_replay_ui.py와 같은 manual_supply_scores.csv를 사용합니다.
    - 이 화면에서 저장한 점수는 기존 차트 리플레이 UI의 Ctx / Action에 즉시 반영됩니다.
    - DB 수정 없음. CSV 기반 임시 레지스트리입니다.

실행:
    python supply_analysis_workbench.py

권장 흐름:
    1. Max% >= 30 또는 >= 50으로 성공군 조회
    2. 좌측 종목 선택
    3. 우측에 프로그램/기관/외국인/테마/뉴스 점수와 근거 메모 입력
    4. 저장
    5. pattern_replay_ui.py에서 Ctx/Action 반영 확인
"""
import csv
import math
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import date, datetime
from pathlib import Path

import pymysql

from config import BacktestConfig, DBConfig


REPORT_DIR = Path(__file__).parent / "outputs" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
MANUAL_SCORE_CSV = REPORT_DIR / "manual_supply_scores.csv"

PATTERN_ITEMS = [
    "ALL",
    "A_EVENT_LIMITLIKE",
    "B_D_E_GOLDEN_CORE",
    "C_D_ONLY_60GC",
    "D_E_ONLY_200GC",
    "E_RSI_E_MIXED",
    "F_RSI_D_MIXED",
    "G_RSI_ONLY",
    "H_COMPLEX_CDE",
    "Z_OTHER",
]
GRADE_ITEMS = ["ALL", "A1", "A2", "B1", "B2", "C_HOT", "C_FAST", "C_WATCH", "C_BAD", "D"]
MAX_FILTER_ITEMS = ["ALL", ">=5", ">=10", ">=20", ">=30", ">=50", ">=100"]

SCAN_COLUMNS = [
    "id", "condition_name", "search_date", "code", "name", "market",
    "trigger_path", "close_price", "volume", "trade_value", "day_return",
    "ma60_200_dist", "rsi14", "bb_width", "vol_ratio_20",
    "ma60_slope_up", "ma200_slope_up",
    "ret_1w", "ret_2w", "ret_3w", "ret_1m", "ret_max", "max_high_date",
    "s_score", "e_score", "grade", "grade_v21", "strategy_v21",
]

GRADE_BASE = {
    "A1": 35.0,
    "A2": 28.0,
    "B1": 26.0,
    "B2": 18.0,
    "C_FAST": 22.0,
    "C_HOT": 12.0,
    "C_WATCH": 5.0,
    "C_BAD": -10.0,
    "D": -15.0,
}

PATTERN_BASE = {
    "A_EVENT_LIMITLIKE": 22.0,
    "C_D_ONLY_60GC": 20.0,
    "B_D_E_GOLDEN_CORE": 18.0,
    "D_E_ONLY_200GC": 12.0,
    "E_RSI_E_MIXED": 8.0,
    "F_RSI_D_MIXED": 5.0,
    "H_COMPLEX_CDE": 5.0,
    "G_RSI_ONLY": -8.0,
    "Z_OTHER": 0.0,
}


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
        value_float = float(value)
        if math.isnan(value_float):
            return default
        return value_float
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
        "search_date",
        "code",
        "program_score",
        "institution_score",
        "foreign_score",
        "theme_score",
        "news_score",
        "note",
        "updated_at",
    ]
    with MANUAL_SCORE_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for key, value in sorted(scores.items()):
            search_date, code = key.split("|", 1)
            writer.writerow({
                "search_date": search_date,
                "code": code,
                "program_score": value.get("program", 0.0),
                "institution_score": value.get("institution", 0.0),
                "foreign_score": value.get("foreign", 0.0),
                "theme_score": value.get("theme", 0.0),
                "news_score": value.get("news", 0.0),
                "note": value.get("note", ""),
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

    if trade_value >= 100000000000:
        score += 12.0
    elif trade_value >= 30000000000:
        score += 8.0
    elif trade_value >= 10000000000:
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


def fetch_scan_rows(start_text, end_text, pattern, grade, max_filter):
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
    conn = get_conn(DBConfig.BACKTEST_DB)
    try:
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
    return rows


class SupplyAnalysisWorkbench(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Supply / Theme / News Analysis Workbench")
        self.geometry("1480x860")
        self.rows = []
        self.manual_scores = load_manual_scores()
        self.current_row = None
        self._build_ui()

    def _build_ui(self):
        root = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        root.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(root, padding=8)
        right = ttk.Frame(root, padding=8)
        root.add(left, weight=3)
        root.add(right, weight=2)

        filter_box = ttk.LabelFrame(left, text="Filter", padding=8)
        filter_box.pack(fill=tk.X)

        ttk.Label(filter_box, text="Start").grid(row=0, column=0, sticky=tk.W)
        self.start_var = tk.StringVar(value="2025-11-20")
        ttk.Entry(filter_box, textvariable=self.start_var, width=12).grid(row=0, column=1, sticky=tk.W, padx=4)

        ttk.Label(filter_box, text="End").grid(row=0, column=2, sticky=tk.W)
        self.end_var = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(filter_box, textvariable=self.end_var, width=12).grid(row=0, column=3, sticky=tk.W, padx=4)

        ttk.Label(filter_box, text="Pattern").grid(row=1, column=0, sticky=tk.W)
        self.pattern_var = tk.StringVar(value="ALL")
        ttk.Combobox(filter_box, textvariable=self.pattern_var, values=PATTERN_ITEMS, state="readonly", width=24).grid(row=1, column=1, sticky=tk.W, padx=4)

        ttk.Label(filter_box, text="Grade").grid(row=1, column=2, sticky=tk.W)
        self.grade_var = tk.StringVar(value="ALL")
        ttk.Combobox(filter_box, textvariable=self.grade_var, values=GRADE_ITEMS, state="readonly", width=12).grid(row=1, column=3, sticky=tk.W, padx=4)

        ttk.Label(filter_box, text="Max%").grid(row=2, column=0, sticky=tk.W)
        self.max_filter_var = tk.StringVar(value=">=50")
        ttk.Combobox(filter_box, textvariable=self.max_filter_var, values=MAX_FILTER_ITEMS, state="readonly", width=12).grid(row=2, column=1, sticky=tk.W, padx=4)

        ttk.Button(filter_box, text="조회", command=self.load_rows).grid(row=2, column=3, sticky=tk.EW, padx=4, pady=4)

        list_box = ttk.LabelFrame(left, text="Analysis Targets", padding=4)
        list_box.pack(fill=tk.BOTH, expand=True, pady=8)

        columns = ("date", "code", "name", "pattern", "grade", "retmax", "ctx", "action", "manual", "s", "e")
        self.tree = ttk.Treeview(list_box, columns=columns, show="headings", height=28)
        headings = {
            "date": "Date",
            "code": "Code",
            "name": "Name",
            "pattern": "Pattern",
            "grade": "G",
            "retmax": "Max%",
            "ctx": "Ctx",
            "action": "Action",
            "manual": "Manual",
            "s": "S",
            "e": "E",
        }
        widths = {
            "date": 86,
            "code": 70,
            "name": 120,
            "pattern": 150,
            "grade": 70,
            "retmax": 70,
            "ctx": 60,
            "action": 85,
            "manual": 70,
            "s": 45,
            "e": 45,
        }
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor=tk.W)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(list_box, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        self.count_var = tk.StringVar(value="0 rows")
        ttk.Label(left, textvariable=self.count_var).pack(anchor=tk.W)

        editor = ttk.LabelFrame(right, text="Selected Stock Supply Analysis", padding=10)
        editor.pack(fill=tk.X)

        self.title_var = tk.StringVar(value="종목을 선택하세요")
        ttk.Label(editor, textvariable=self.title_var, font=("맑은 고딕", 11, "bold")).grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=4)

        fields = [
            ("program", "프로그램 순매수"),
            ("institution", "기관 매수/누적"),
            ("foreign", "외국인 매수/누적"),
            ("theme", "테마 형성"),
            ("news", "뉴스/재료"),
        ]
        self.score_vars = {}
        for i, (key, label) in enumerate(fields, 1):
            ttk.Label(editor, text=label).grid(row=i, column=0, sticky=tk.W, pady=4)
            var = tk.StringVar(value="0")
            self.score_vars[key] = var
            ttk.Entry(editor, textvariable=var, width=10).grid(row=i, column=1, sticky=tk.W, padx=6)
            ttk.Label(editor, text="0~20").grid(row=i, column=2, sticky=tk.W)

        ttk.Label(editor, text="근거 메모").grid(row=6, column=0, sticky=tk.NW, pady=4)
        self.note_text = tk.Text(editor, height=8, width=54)
        self.note_text.grid(row=6, column=1, columnspan=2, sticky=tk.W, padx=6)

        button_frame = ttk.Frame(editor)
        button_frame.grid(row=7, column=0, columnspan=3, sticky=tk.EW, pady=8)
        ttk.Button(button_frame, text="저장", command=self.save_current).pack(side=tk.RIGHT, padx=4)
        ttk.Button(button_frame, text="점수 초기화", command=self.clear_current).pack(side=tk.RIGHT, padx=4)

        summary = ttk.LabelFrame(right, text="Analysis Summary", padding=10)
        summary.pack(fill=tk.BOTH, expand=True, pady=8)
        self.summary_text = tk.Text(summary, height=20, wrap=tk.WORD)
        self.summary_text.pack(fill=tk.BOTH, expand=True)

    def load_rows(self):
        try:
            self.rows = fetch_scan_rows(
                self.start_var.get().strip(),
                self.end_var.get().strip(),
                self.pattern_var.get().strip(),
                self.grade_var.get().strip(),
                self.max_filter_var.get().strip(),
            )
        except Exception as ex:
            messagebox.showerror("DB 조회 실패", str(ex))
            return
        self.tree.delete(*self.tree.get_children())
        for idx, row in enumerate(self.rows):
            self.tree.insert("", tk.END, iid=str(idx), values=self.make_tree_values(row))
        self.count_var.set(f"{len(self.rows)} rows")
        self.update_summary()
        if self.rows:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
            self.show_row(self.rows[0])

    def make_tree_values(self, row):
        ctx, action = compute_context(row, self.manual_scores)
        manual = self.manual_scores.get(row_key(row))
        manual_total = manual_score_total(manual)
        return (
            safe_date_text(row.get("search_date")),
            row.get("code"),
            row.get("name"),
            pattern_group(row),
            row.get("grade_v21"),
            f"{safe_float(row.get('ret_max')):.2f}",
            f"{ctx:.1f}",
            action,
            f"{manual_total:.1f}",
            row.get("s_score"),
            row.get("e_score"),
        )

    def refresh_tree_row(self, idx):
        if idx < 0 or idx >= len(self.rows):
            return
        self.tree.item(str(idx), values=self.make_tree_values(self.rows[idx]))

    def on_select(self, _event):
        selected = self.tree.selection()
        if not selected:
            return
        idx = int(selected[0])
        if 0 <= idx < len(self.rows):
            self.show_row(self.rows[idx])

    def show_row(self, row):
        self.current_row = row
        key = row_key(row)
        saved = self.manual_scores.get(key, {})
        self.title_var.set(f"{safe_date_text(row.get('search_date'))} {row.get('code')} {row.get('name')} / {pattern_group(row)} / {row.get('grade_v21')}")
        for score_key, var in self.score_vars.items():
            var.set(str(saved.get(score_key, 0.0)))
        self.note_text.delete("1.0", tk.END)
        self.note_text.insert(tk.END, saved.get("note", ""))
        self.update_selected_summary(row)

    def read_editor_values(self):
        values = {}
        for key, var in self.score_vars.items():
            values[key] = max(0.0, min(20.0, safe_float(var.get())))
        values["note"] = self.note_text.get("1.0", tk.END).strip()
        return values

    def save_current(self):
        if self.current_row is None:
            messagebox.showinfo("선택 없음", "저장할 종목을 선택하세요.")
            return
        key = row_key(self.current_row)
        self.manual_scores[key] = self.read_editor_values()
        save_manual_scores(self.manual_scores)
        selected = self.tree.selection()
        if selected:
            self.refresh_tree_row(int(selected[0]))
        self.update_summary()
        self.update_selected_summary(self.current_row)
        messagebox.showinfo("저장 완료", f"{self.current_row.get('name')} 수급분석을 저장했습니다.")

    def clear_current(self):
        if self.current_row is None:
            return
        key = row_key(self.current_row)
        if key in self.manual_scores:
            del self.manual_scores[key]
            save_manual_scores(self.manual_scores)
        for var in self.score_vars.values():
            var.set("0")
        self.note_text.delete("1.0", tk.END)
        selected = self.tree.selection()
        if selected:
            self.refresh_tree_row(int(selected[0]))
        self.update_summary()
        self.update_selected_summary(self.current_row)

    def update_selected_summary(self, row):
        ctx, action = compute_context(row, self.manual_scores)
        manual = self.manual_scores.get(row_key(row), {})
        lines = []
        lines.append("[선택 종목]")
        lines.append(f"{safe_date_text(row.get('search_date'))} {row.get('code')} {row.get('name')} / {row.get('market')}")
        lines.append(f"pattern={pattern_group(row)} / trigger={row.get('trigger_path')}")
        lines.append(f"grade_v21={row.get('grade_v21')} / S={row.get('s_score')} / E={row.get('e_score')}")
        lines.append(f"ret_max={safe_float(row.get('ret_max')):.2f}% / ret_1m={safe_float(row.get('ret_1m')):.2f}%")
        lines.append(f"day_return={safe_float(row.get('day_return')):.2f}% / vol_ratio20={safe_float(row.get('vol_ratio_20')):.2f} / trade_value={safe_float(row.get('trade_value')):,.0f}")
        lines.append("")
        lines.append("[수동 분석 점수]")
        lines.append(f"program={safe_float(manual.get('program')):.1f}")
        lines.append(f"institution={safe_float(manual.get('institution')):.1f}")
        lines.append(f"foreign={safe_float(manual.get('foreign')):.1f}")
        lines.append(f"theme={safe_float(manual.get('theme')):.1f}")
        lines.append(f"news={safe_float(manual.get('news')):.1f}")
        lines.append(f"manual_total={manual_score_total(manual):.1f}")
        lines.append(f"context_score={ctx:.1f} / action={action}")
        if manual.get("note"):
            lines.append("")
            lines.append("[근거 메모]")
            lines.append(manual.get("note"))
        self.summary_text.delete("1.0", tk.END)
        self.summary_text.insert(tk.END, "\n".join(lines))

    def update_summary(self):
        if not self.rows:
            self.summary_text.delete("1.0", tk.END)
            self.summary_text.insert(tk.END, "조회된 종목이 없습니다.")
            return
        action_counts = {}
        saved_count = 0
        for row in self.rows:
            _ctx, action = compute_context(row, self.manual_scores)
            action_counts[action] = action_counts.get(action, 0) + 1
            if row_key(row) in self.manual_scores:
                saved_count += 1
        lines = []
        lines.append("[조회 요약]")
        lines.append(f"rows={len(self.rows)} / 수급분석 저장={saved_count}")
        for action, count in sorted(action_counts.items(), key=lambda x: x[0]):
            lines.append(f"{action}: {count}")
        self.summary_text.delete("1.0", tk.END)
        self.summary_text.insert(tk.END, "\n".join(lines))


def main():
    app = SupplyAnalysisWorkbench()
    app.mainloop()


if __name__ == "__main__":
    main()
