"""
myCondition 통합 분석 대시보드 v2.1

v2.1 변경사항:
    - 후보 리스트에 Clx(클러스터 레이블) 컬럼 추가
    - 필터에 Cluster 콤보박스 추가
    - 차트 타이틀에 클러스터 레이블 표시
    - Chart/Logic Summary에 [클러스터] 라인 추가
    - 전략분석 탭에 클러스터별 성과 요약 + 가이드 표시

실행:
    python analysis_dashboard_ui.py

DB 수정 없음.
"""
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import date

import pandas as pd

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from pattern_replay_ui import (
    PATTERN_ITEMS,
    GRADE_ITEMS,
    MAX_FILTER_ITEMS,
    CLUSTER_LABEL_ITEMS,
    CLUSTER_GUIDES,
    CLUSTER_CONTEXT_BONUS,
    POLICIES,
    PATTERN_GUIDES,
    fetch_scan_rows,
    fetch_candles,
    simulate_policy,
    draw_candles,
    safe_float,
    safe_int,
    safe_date_text,
    pattern_group,
    cluster_label_short,
    compute_context,
    load_manual_scores,
    save_manual_scores,
    manual_score_total,
    row_key,
)


class AnalysisDashboardUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("myCondition Analysis Dashboard v2.1")
        self.geometry("1860x1040")
        self.minsize(1280, 760)

        self.rows = []
        self.current_row = None
        self.manual_scores = load_manual_scores()

        self._build_ui()
        self.after(100, self.load_rows)

    # ══════════════════════════════════════════
    # UI 빌드
    # ══════════════════════════════════════════
    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        root = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        root.grid(row=0, column=0, sticky="nsew")

        self.left = ttk.Frame(root, padding=8)
        self.right = ttk.Frame(root, padding=8)
        root.add(self.left, weight=2)
        root.add(self.right, weight=5)

        self.left.columnconfigure(0, weight=1)
        self.left.rowconfigure(1, weight=1)
        self.right.columnconfigure(0, weight=1)
        self.right.rowconfigure(0, weight=1)

        self._build_filter_panel()
        self._build_candidate_grid()
        self._build_tabs()

    def _build_filter_panel(self):
        box = ttk.LabelFrame(self.left, text="Filter", padding=8)
        box.grid(row=0, column=0, sticky="ew")
        for i in range(6):
            box.columnconfigure(i, weight=1)

        ttk.Label(box, text="Start").grid(row=0, column=0, sticky="w", padx=2, pady=2)
        self.start_var = tk.StringVar(value="2025-11-20")
        ttk.Entry(box, textvariable=self.start_var, width=12).grid(row=0, column=1, sticky="ew", padx=2, pady=2)

        ttk.Label(box, text="End").grid(row=0, column=2, sticky="w", padx=2, pady=2)
        self.end_var = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(box, textvariable=self.end_var, width=12).grid(row=0, column=3, sticky="ew", padx=2, pady=2)

        ttk.Label(box, text="Pattern").grid(row=1, column=0, sticky="w", padx=2, pady=2)
        self.pattern_var = tk.StringVar(value="ALL")
        ttk.Combobox(box, textvariable=self.pattern_var, values=PATTERN_ITEMS, state="readonly", width=22).grid(row=1, column=1, columnspan=2, sticky="ew", padx=2, pady=2)

        ttk.Label(box, text="Grade").grid(row=1, column=3, sticky="w", padx=2, pady=2)
        self.grade_var = tk.StringVar(value="ALL")
        ttk.Combobox(box, textvariable=self.grade_var, values=GRADE_ITEMS, state="readonly", width=10).grid(row=1, column=4, sticky="ew", padx=2, pady=2)

        ttk.Label(box, text="Max%").grid(row=2, column=0, sticky="w", padx=2, pady=2)
        self.max_filter_var = tk.StringVar(value="ALL")
        ttk.Combobox(box, textvariable=self.max_filter_var, values=MAX_FILTER_ITEMS, state="readonly", width=10).grid(row=2, column=1, sticky="ew", padx=2, pady=2)

        # v2.1: 클러스터 필터 추가
        ttk.Label(box, text="Cluster").grid(row=2, column=2, sticky="w", padx=2, pady=2)
        self.cluster_var = tk.StringVar(value="ALL")
        ttk.Combobox(box, textvariable=self.cluster_var, values=CLUSTER_LABEL_ITEMS, state="readonly", width=18).grid(row=2, column=3, columnspan=2, sticky="ew", padx=2, pady=2)

        ttk.Label(box, text="Before").grid(row=3, column=0, sticky="w", padx=2, pady=2)
        self.before_var = tk.StringVar(value="60")
        ttk.Entry(box, textvariable=self.before_var, width=8).grid(row=3, column=1, sticky="ew", padx=2, pady=2)

        ttk.Label(box, text="After").grid(row=3, column=2, sticky="w", padx=2, pady=2)
        self.after_var = tk.StringVar(value="120")
        ttk.Entry(box, textvariable=self.after_var, width=8).grid(row=3, column=3, sticky="ew", padx=2, pady=2)

        ttk.Button(box, text="조회", command=self.load_rows).grid(row=4, column=0, columnspan=3, sticky="ew", padx=2, pady=4)
        ttk.Button(box, text="선택 수급 저장", command=self.save_supply_current).grid(row=4, column=3, columnspan=3, sticky="ew", padx=2, pady=4)

    def _build_candidate_grid(self):
        box = ttk.LabelFrame(self.left, text="Candidates", padding=4)
        box.grid(row=1, column=0, sticky="nsew", pady=(8, 2))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        # v2.1: clx 컬럼 추가
        columns = (
            "date", "code", "name", "pattern", "grade", "max", "clx",
            "ctx", "action", "manual", "s", "e", "day", "vr", "trade_value", "ret1m",
        )
        self.tree = ttk.Treeview(box, columns=columns, show="headings")

        header = {
            "date": "Date", "code": "Code", "name": "Name",
            "pattern": "Pattern", "grade": "G", "max": "Max%",
            "clx": "Clx", "ctx": "Ctx", "action": "Action",
            "manual": "Manual", "s": "S", "e": "E",
            "day": "Day%", "vr": "VR20", "trade_value": "TradeVal", "ret1m": "1M%",
        }
        width = {
            "date": 86, "code": 70, "name": 120, "pattern": 150, "grade": 55,
            "max": 60, "clx": 62, "ctx": 55, "action": 80,
            "manual": 60, "s": 40, "e": 40,
            "day": 55, "vr": 55, "trade_value": 95, "ret1m": 55,
        }
        for col in columns:
            self.tree.heading(col, text=header[col])
            self.tree.column(col, width=width[col], minwidth=width[col], anchor=tk.W, stretch=False)

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(box, orient=tk.VERTICAL, command=self.tree.yview)
        xscroll = ttk.Scrollbar(box, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self.on_select_row)

        self.count_var = tk.StringVar(value="0 rows")
        ttk.Label(self.left, textvariable=self.count_var).grid(row=2, column=0, sticky="w")

    def _build_tabs(self):
        self.tabs = ttk.Notebook(self.right)
        self.tabs.grid(row=0, column=0, sticky="nsew")

        self.chart_tab = ttk.Frame(self.tabs, padding=6)
        self.supply_tab = ttk.Frame(self.tabs, padding=8)
        self.response_tab = ttk.Frame(self.tabs, padding=8)
        self.strategy_tab = ttk.Frame(self.tabs, padding=8)

        self.tabs.add(self.chart_tab, text="차트분석")
        self.tabs.add(self.supply_tab, text="수급분석")
        self.tabs.add(self.response_tab, text="대응분석")
        self.tabs.add(self.strategy_tab, text="전략분석")
        self.tabs.select(self.chart_tab)

        self._build_chart_tab()
        self._build_supply_tab()
        self._build_response_tab()
        self._build_strategy_tab()

    def _build_chart_tab(self):
        self.chart_tab.columnconfigure(0, weight=1)
        self.chart_tab.rowconfigure(0, weight=1)
        self.chart_tab.rowconfigure(1, weight=0)

        self.fig = Figure(figsize=(12, 7), dpi=100)
        self.ax_price = self.fig.add_subplot(2, 1, 1)
        self.ax_vol = self.fig.add_subplot(2, 1, 2, sharex=self.ax_price)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.chart_tab)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        toolbar_frame = ttk.Frame(self.chart_tab)
        toolbar_frame.grid(row=1, column=0, sticky="ew")
        NavigationToolbar2Tk(self.canvas, toolbar_frame).update()

        info_box = ttk.LabelFrame(self.chart_tab, text="Chart / Logic / Cluster Summary", padding=6)
        info_box.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        info_box.columnconfigure(0, weight=1)
        self.chart_info = tk.Text(info_box, height=12, wrap=tk.WORD)
        self.chart_info.grid(row=0, column=0, sticky="ew")

    def _build_supply_tab(self):
        self.supply_tab.columnconfigure(0, weight=1)
        self.supply_tab.rowconfigure(2, weight=1)

        title = ttk.Label(self.supply_tab, text="종목을 선택하세요", font=("맑은 고딕", 12, "bold"))
        title.grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.supply_title = title

        entry_frame = ttk.LabelFrame(self.supply_tab, text="수급 / 테마 / 뉴스 점수", padding=8)
        entry_frame.grid(row=1, column=0, sticky="ew")
        for i in range(4):
            entry_frame.columnconfigure(i, weight=1)

        fields = [
            ("program", "프로그램 순매수"),
            ("institution", "기관 매수/누적"),
            ("foreign", "외국인 매수/누적"),
            ("theme", "테마 형성"),
            ("news", "뉴스/재료"),
        ]
        self.supply_vars = {}
        for i, (key, label) in enumerate(fields):
            ttk.Label(entry_frame, text=label).grid(row=i, column=0, sticky="w", pady=3)
            var = tk.StringVar(value="0")
            self.supply_vars[key] = var
            ttk.Entry(entry_frame, textvariable=var, width=10).grid(row=i, column=1, sticky="w", padx=4, pady=3)
            ttk.Label(entry_frame, text="0~20").grid(row=i, column=2, sticky="w", padx=4, pady=3)

        ttk.Label(entry_frame, text="근거 메모").grid(row=5, column=0, sticky="nw", pady=4)
        self.supply_note = tk.Text(entry_frame, height=7, wrap=tk.WORD)
        self.supply_note.grid(row=5, column=1, columnspan=3, sticky="ew", padx=4, pady=4)

        button_frame = ttk.Frame(entry_frame)
        button_frame.grid(row=6, column=1, columnspan=3, sticky="e", pady=6)
        ttk.Button(button_frame, text="저장", command=self.save_supply_current).pack(side=tk.RIGHT, padx=4)
        ttk.Button(button_frame, text="초기화", command=self.clear_supply_current).pack(side=tk.RIGHT, padx=4)

        summary_box = ttk.LabelFrame(self.supply_tab, text="수급분석 요약", padding=8)
        summary_box.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        summary_box.columnconfigure(0, weight=1)
        summary_box.rowconfigure(0, weight=1)
        self.supply_summary = tk.Text(summary_box, wrap=tk.WORD)
        self.supply_summary.grid(row=0, column=0, sticky="nsew")

    def _build_response_tab(self):
        self.response_tab.columnconfigure(0, weight=1)
        self.response_tab.rowconfigure(0, weight=1)
        self.response_text = tk.Text(self.response_tab, wrap=tk.WORD)
        self.response_text.grid(row=0, column=0, sticky="nsew")

    def _build_strategy_tab(self):
        self.strategy_tab.columnconfigure(0, weight=1)
        self.strategy_tab.rowconfigure(0, weight=1)
        self.strategy_text = tk.Text(self.strategy_tab, wrap=tk.WORD)
        self.strategy_text.grid(row=0, column=0, sticky="nsew")

    # ══════════════════════════════════════════
    # 데이터 로드 / 표시
    # ══════════════════════════════════════════
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
            messagebox.showerror("조회 실패", str(ex))
            return

        self.tree.delete(*self.tree.get_children())
        for idx, row in enumerate(self.rows):
            self.tree.insert("", tk.END, iid=str(idx), values=self.make_tree_values(row))
        self.count_var.set(f"{len(self.rows)} rows")
        if self.rows:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
            self.show_row(self.rows[0])
        else:
            self.clear_views()

    def make_tree_values(self, row):
        ctx, action = compute_context(row, self.manual_scores)
        manual = self.manual_scores.get(row_key(row))
        clx = cluster_label_short(row.get("cluster_label"))
        return (
            safe_date_text(row.get("search_date")),
            row.get("code"),
            row.get("name"),
            pattern_group(row),
            row.get("grade_v21"),
            f"{safe_float(row.get('ret_max')):.2f}",
            clx,
            f"{ctx:.1f}",
            action,
            f"{manual_score_total(manual):.1f}",
            row.get("s_score"),
            row.get("e_score"),
            f"{safe_float(row.get('day_return')):.2f}",
            f"{safe_float(row.get('vol_ratio_20')):.2f}",
            f"{safe_float(row.get('trade_value')):,.0f}",
            f"{safe_float(row.get('ret_1m')):.2f}",
        )

    def refresh_tree_row(self, idx):
        if 0 <= idx < len(self.rows):
            self.tree.item(str(idx), values=self.make_tree_values(self.rows[idx]))

    def on_select_row(self, _event):
        selected = self.tree.selection()
        if not selected:
            return
        idx = int(selected[0])
        if 0 <= idx < len(self.rows):
            self.show_row(self.rows[idx])

    def show_row(self, row):
        self.current_row = row
        self.update_chart(row)
        self.load_supply_values(row)
        self.update_supply_summary(row)
        self.update_response_tab(row)
        self.update_strategy_tab(row)

    # ══════════════════════════════════════════
    # 차트분석 탭
    # ══════════════════════════════════════════
    def update_chart(self, row):
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
        self.update_chart_info(row, candles, trade_result)

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
            f"{signal_date_text} {row.get('code')} ({row.get('name')}) | "
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

    def update_chart_info(self, row, candles, trade_result):
        pg = pattern_group(row)
        grade = row.get("grade_v21")
        policy = POLICIES.get(grade, POLICIES["D"])
        ctx, action = compute_context(row, self.manual_scores)
        manual = self.manual_scores.get(row_key(row), {})
        before_count = candles.attrs.get("before_count", 0) if not candles.empty else 0
        after_count = candles.attrs.get("after_count", 0) if not candles.empty else 0
        first_date = candles.attrs.get("first_date", "") if not candles.empty else ""
        last_date = candles.attrs.get("last_date", "") if not candles.empty else ""

        # v2.1: 클러스터 정보
        clx_label = str(row.get("cluster_label") or "D_NO_CLUSTER")
        clx_bonus = safe_int(row.get("cluster_bonus"))
        e_raw = safe_int(row.get("e_score_raw"))
        e_final = safe_int(row.get("e_score"))
        clx_guide = CLUSTER_GUIDES.get(clx_label, "")

        lines = []
        lines.append(f"[종목] {safe_date_text(row.get('search_date'))} {row.get('code')} {row.get('name')} / {row.get('market')}")
        lines.append(f"[신뢰판정] context_score={ctx:.1f} / action={action} / manual_total={manual_score_total(manual):.1f}")
        lines.append(f"[차트범위] before={before_count} / after={after_count} / {first_date} ~ {last_date}")
        lines.append(f"[패턴] {pg} / trigger_path={row.get('trigger_path')}")
        lines.append(f"[등급] v20={row.get('grade_v20')} → v21={grade}")
        lines.append(f"[스코어] S={row.get('s_score')} E_raw={e_raw} + cluster_bonus={clx_bonus:+d} = E={e_final}")
        lines.append(f"[클러스터] label={clx_label} / bonus={clx_bonus:+d}")
        lines.append(f"  → {clx_guide}")
        lines.append(f"[성과] ret_1w={safe_float(row.get('ret_1w')):.2f}% / ret_1m={safe_float(row.get('ret_1m')):.2f}% / ret_max={safe_float(row.get('ret_max')):.2f}%")
        lines.append(f"[매매정책] {policy['label']} / target={policy['target']}% / stop={policy['stop']}% / max_days={policy['max_days']}")
        lines.append(f"[매매결과] exit={trade_result.get('exit_date')} / reason={trade_result.get('exit_reason')} / gross={trade_result.get('gross_return'):.2f}% / net={trade_result.get('net_return'):.2f}%")
        lines.append(f"[패턴 가이드] {PATTERN_GUIDES.get(pg, '')}")
        self.chart_info.delete("1.0", tk.END)
        self.chart_info.insert(tk.END, "\n".join(lines))

    # ══════════════════════════════════════════
    # 수급분석 탭
    # ══════════════════════════════════════════
    def load_supply_values(self, row):
        key = row_key(row)
        saved = self.manual_scores.get(key, {})
        clx = cluster_label_short(row.get("cluster_label"))
        self.supply_title.config(
            text=f"{safe_date_text(row.get('search_date'))} {row.get('code')} {row.get('name')} / {pattern_group(row)} / {row.get('grade_v21')} / Clx:{clx}"
        )
        for score_key, var in self.supply_vars.items():
            var.set(str(saved.get(score_key, 0.0)))
        self.supply_note.delete("1.0", tk.END)
        self.supply_note.insert(tk.END, saved.get("note", ""))

    def read_supply_values(self):
        values = {}
        for key, var in self.supply_vars.items():
            values[key] = max(0.0, min(20.0, safe_float(var.get())))
        values["note"] = self.supply_note.get("1.0", tk.END).strip()
        return values

    def save_supply_current(self):
        if self.current_row is None:
            messagebox.showinfo("선택 없음", "종목을 선택하세요.")
            return
        self.manual_scores[row_key(self.current_row)] = self.read_supply_values()
        save_manual_scores(self.manual_scores)
        selected = self.tree.selection()
        if selected:
            self.refresh_tree_row(int(selected[0]))
        self.update_supply_summary(self.current_row)
        self.update_response_tab(self.current_row)
        self.update_chart(self.current_row)
        messagebox.showinfo("저장 완료", "수급분석 점수를 저장했습니다.")

    def clear_supply_current(self):
        if self.current_row is None:
            return
        key = row_key(self.current_row)
        if key in self.manual_scores:
            del self.manual_scores[key]
            save_manual_scores(self.manual_scores)
        for var in self.supply_vars.values():
            var.set("0")
        self.supply_note.delete("1.0", tk.END)
        selected = self.tree.selection()
        if selected:
            self.refresh_tree_row(int(selected[0]))
        self.update_supply_summary(self.current_row)
        self.update_response_tab(self.current_row)
        self.update_chart(self.current_row)

    def update_supply_summary(self, row):
        ctx, action = compute_context(row, self.manual_scores)
        manual = self.manual_scores.get(row_key(row), {})
        clx_label = str(row.get("cluster_label") or "D_NO_CLUSTER")
        clx_bonus_ctx = CLUSTER_CONTEXT_BONUS.get(clx_label, 0.0)
        lines = []
        lines.append("[수급분석]")
        lines.append(f"program={safe_float(manual.get('program')):.1f}")
        lines.append(f"institution={safe_float(manual.get('institution')):.1f}")
        lines.append(f"foreign={safe_float(manual.get('foreign')):.1f}")
        lines.append(f"theme={safe_float(manual.get('theme')):.1f}")
        lines.append(f"news={safe_float(manual.get('news')):.1f}")
        lines.append(f"manual_total={manual_score_total(manual):.1f}")
        lines.append(f"cluster_context_bonus={clx_bonus_ctx:+.1f} ({clx_label})")
        lines.append(f"context_score={ctx:.1f} / action={action}")
        if manual.get("note"):
            lines.append("")
            lines.append("[근거 메모]")
            lines.append(manual.get("note"))
        self.supply_summary.delete("1.0", tk.END)
        self.supply_summary.insert(tk.END, "\n".join(lines))

    # ══════════════════════════════════════════
    # 대응분석 탭
    # ══════════════════════════════════════════
    def update_response_tab(self, row):
        ctx, action = compute_context(row, self.manual_scores)
        pg = pattern_group(row)
        grade = row.get("grade_v21")
        clx_label = str(row.get("cluster_label") or "D_NO_CLUSTER")
        clx_bonus = safe_int(row.get("cluster_bonus"))

        lines = []
        lines.append("[대응분석]")
        lines.append(f"종목: {safe_date_text(row.get('search_date'))} {row.get('code')} {row.get('name')}")
        lines.append(f"패턴: {pg}")
        lines.append(f"등급: {grade}")
        lines.append(f"클러스터: {clx_label} (bonus={clx_bonus:+d})")
        lines.append(f"신뢰점수: {ctx:.1f}")
        lines.append(f"대응패턴: {action}")
        lines.append("")

        if action == "PRIORITY":
            lines.append("▶ 대응 가이드: 장중 눌림/재상승 확인 시 최우선 추적. 수급이 유지되면 적극 대응 후보.")
        elif action == "STRONG":
            lines.append("▶ 대응 가이드: 후보군 상위. 포착봉 고가 재돌파 또는 눌림 지지 확인 후 대응.")
        elif action == "CONFIRM":
            lines.append("▶ 대응 가이드: 바로 진입보다 장중 거래대금/수급/테마 재확인 필요.")
        elif action.startswith("SKIP"):
            lines.append("▶ 대응 가이드: 자동매매 주력 제외. 특수 재료가 없으면 관찰만.")
        else:
            lines.append("▶ 대응 가이드: 관심 후보. 추가 수급/뉴스 확인 전에는 낮은 우선순위.")

        # v2.1: 클러스터별 대응 추가 가이드
        if clx_label == "A_SEMI_CLUSTER":
            lines.append("")
            lines.append("▶ 클러스터 가이드: 반도체장비 동시 포착. 업종 전체 모멘텀 강화. 동일 날짜 다른 반도체장비주도 함께 확인.")
        elif clx_label == "B_COOL_ELEC":
            lines.append("")
            lines.append("▶ 클러스터 가이드: 전기·전자 Cool 돌파. 조용할 때가 기회. 당일 등락률이 낮은 게 오히려 긍정적.")
        elif clx_label == "X_HOT_AVOID":
            lines.append("")
            lines.append("▶ 클러스터 경고: 전기·전자 과열 클러스터(avg_day_ret≥10%). 승률 20%, 평균 -5.9%. 진입 회피 강력 권장.")
        elif clx_label == "C_OTHER_CLUSTER":
            lines.append("")
            lines.append("▶ 클러스터 주의: 기타 업종 클러스터. 과열 가능성. 평균 성과 마이너스. 추가 필터 없이 진입 비권장.")

        lines.append("")
        lines.append("다음 단계: 분봉/틱 기반 진입 타이밍, VI 근접성, 당일 거래대금 속도, 포착봉 저가 지지 여부를 추가하면 이 탭에서 대응 로직을 더 정밀화합니다.")
        self.response_text.delete("1.0", tk.END)
        self.response_text.insert(tk.END, "\n".join(lines))

    # ══════════════════════════════════════════
    # 전략분석 탭 (v2.1: 클러스터별 성과 요약)
    # ══════════════════════════════════════════
    def update_strategy_tab(self, row):
        ctx, action = compute_context(row, self.manual_scores)
        clx_label = str(row.get("cluster_label") or "D_NO_CLUSTER")

        lines = []
        lines.append("[전략분석 v2.1]")
        lines.append(f"현재 선택: {safe_date_text(row.get('search_date'))} {row.get('code')} {row.get('name')}")
        lines.append(f"context_score={ctx:.1f} / action={action} / cluster={clx_label}")
        lines.append("")

        # 현재 조회 데이터 기반 클러스터별 통계
        lines.append("=" * 70)
        lines.append("[현재 조회 데이터 기반 클러스터별 성과 요약]")
        lines.append(f"{'Label':<20} {'건수':>5} {'avg_max':>8} {'avg_1m':>8} {'win%':>6}")
        lines.append("-" * 70)

        cluster_stats = {}
        for r in self.rows:
            lbl = str(r.get("cluster_label") or "D_NO_CLUSTER")
            if lbl not in cluster_stats:
                cluster_stats[lbl] = {"count": 0, "max_sum": 0.0, "1m_sum": 0.0, "wins": 0, "1m_valid": 0}
            st = cluster_stats[lbl]
            st["count"] += 1
            st["max_sum"] += safe_float(r.get("ret_max"))
            ret_1m = r.get("ret_1m")
            if ret_1m is not None:
                st["1m_sum"] += safe_float(ret_1m)
                st["1m_valid"] += 1
                if safe_float(ret_1m) > 0:
                    st["wins"] += 1

        for lbl in ["A_SEMI_CLUSTER", "B_COOL_ELEC", "B2_WARM_ELEC", "D_NO_CLUSTER", "C_OTHER_CLUSTER", "X_HOT_AVOID"]:
            st = cluster_stats.get(lbl)
            if not st or st["count"] == 0:
                continue
            avg_max = st["max_sum"] / st["count"]
            avg_1m = st["1m_sum"] / st["1m_valid"] if st["1m_valid"] > 0 else 0.0
            win_pct = st["wins"] / st["1m_valid"] * 100 if st["1m_valid"] > 0 else 0.0
            marker = " ◀" if lbl == clx_label else ""
            lines.append(f"{lbl:<20} {st['count']:>5} {avg_max:>+8.1f} {avg_1m:>+8.1f} {win_pct:>5.1f}%{marker}")

        lines.append("")
        lines.append("=" * 70)
        lines.append("[전체 백테스트 기준 클러스터 성과 (3,860건)]")
        lines.append(f"{'Label':<20} {'건수':>5} {'avg_1m':>8} {'승률':>6} {'bonus':>6}")
        lines.append("-" * 70)
        ref_data = [
            ("A_SEMI_CLUSTER",  83,  "+7.4%", "55.4%", "+15"),
            ("B_COOL_ELEC",     66,  "+5.6%", "45.5%", "+10"),
            ("B2_WARM_ELEC",    83,  "+2.5%", "43.6%",  "+3"),
            ("D_NO_CLUSTER",  3257,  "+0.4%", "39.7%",   "0"),
            ("C_OTHER_CLUSTER", 339, "-2.3%", "29.9%",  "-3"),
            ("X_HOT_AVOID",     32,  "-5.3%", "29.0%", "-10"),
        ]
        for lbl, cnt, avg1m, wr, bonus in ref_data:
            marker = " ◀" if lbl == clx_label else ""
            lines.append(f"{lbl:<20} {cnt:>5} {avg1m:>8} {wr:>6} {bonus:>6}{marker}")

        lines.append("")
        lines.append("=" * 70)
        lines.append("[클러스터 레이블별 가이드]")
        for lbl, guide in CLUSTER_GUIDES.items():
            lines.append(f"  {lbl}: {guide}")

        lines.append("")
        lines.append("=" * 70)
        lines.append("[향후 추가 예정]")
        lines.append("1. 같은 Action 그룹의 평균 ret_max / ret_1w / ret_1m")
        lines.append("2. 수급점수 상위군 vs 하위군 성과 비교")
        lines.append("3. 패턴별 target/stop/max_days 최적 정책")
        lines.append("4. 시장국면(상승/하락)별 클러스터 효과 차이")
        lines.append("5. 운영로직 승격 여부")

        self.strategy_text.delete("1.0", tk.END)
        self.strategy_text.insert(tk.END, "\n".join(lines))

    # ══════════════════════════════════════════
    # 클리어
    # ══════════════════════════════════════════
    def clear_views(self):
        self.current_row = None
        self.ax_price.clear()
        self.ax_vol.clear()
        self.canvas.draw()
        self.chart_info.delete("1.0", tk.END)
        self.supply_summary.delete("1.0", tk.END)
        self.response_text.delete("1.0", tk.END)
        self.strategy_text.delete("1.0", tk.END)


def main():
    app = AnalysisDashboardUI()
    app.mainloop()


if __name__ == "__main__":
    main()
