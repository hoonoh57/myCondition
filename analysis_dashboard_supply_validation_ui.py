"""
myCondition 수급점수 성과검증 대시보드 v2.2

목적:
    기존 analysis_dashboard_ui.py를 직접 훼손하지 않고 상속 방식으로 확장한다.
    전략분석 탭에 수동 수급점수(manual_total)의 실제 성과 검증표를 추가한다.

실행:
    python analysis_dashboard_supply_validation_ui.py

검증 항목:
    1) 수급점수 구간별 ret_max / ret_1w / ret_1m / win_1m%
    2) 수급점수 Top20% vs Bottom20% 성과 비교
    3) 개별 수급항목(program/institution/foreign/theme/news)별 고점수군 성과

DB 수정 없음.
기존 manual_supply_scores.csv 구조 유지.
"""
import math
import tkinter as tk

from analysis_dashboard_ui import AnalysisDashboardUI
from pattern_replay_ui import (
    safe_float,
    safe_date_text,
    row_key,
    manual_score_total,
    compute_context,
)


SUPPLY_FIELDS = [
    ("program", "프로그램"),
    ("institution", "기관"),
    ("foreign", "외국인"),
    ("theme", "테마"),
    ("news", "뉴스"),
]


class SupplyValidationDashboardUI(AnalysisDashboardUI):
    def __init__(self):
        super().__init__()
        self.title("myCondition Analysis Dashboard v2.2 - Supply Validation")

    # ══════════════════════════════════════════
    # 전략분석 탭 확장
    # ══════════════════════════════════════════
    def update_strategy_tab(self, row):
        super().update_strategy_tab(row)

        current_text = self.strategy_text.get("1.0", tk.END).rstrip()
        extra_lines = self.build_supply_validation_lines(row)
        if extra_lines:
            self.strategy_text.delete("1.0", tk.END)
            self.strategy_text.insert(tk.END, current_text + "\n\n" + "\n".join(extra_lines))

    # ══════════════════════════════════════════
    # 수급점수 검증용 레코드 생성
    # ══════════════════════════════════════════
    def build_supply_records(self):
        records = []
        for r in self.rows:
            key = row_key(r)
            manual = self.manual_scores.get(key, {})
            total = manual_score_total(manual)
            ctx, action = compute_context(r, self.manual_scores)

            item = {
                "key": key,
                "date": safe_date_text(r.get("search_date")),
                "code": r.get("code"),
                "name": r.get("name"),
                "manual_total": total,
                "has_manual": key in self.manual_scores,
                "ret_max": safe_float(r.get("ret_max")),
                "ret_1w": safe_float(r.get("ret_1w")),
                "ret_1m": safe_float(r.get("ret_1m")),
                "context_score": ctx,
                "action": action,
            }

            for field_key, _field_label in SUPPLY_FIELDS:
                item[field_key] = safe_float(manual.get(field_key))

            records.append(item)
        return records

    def bucket_name(self, total):
        if total <= 0.0:
            return "00_NO_SCORE"
        if total <= 20.0:
            return "01_001_020"
        if total <= 40.0:
            return "02_021_040"
        if total <= 60.0:
            return "03_041_060"
        if total <= 80.0:
            return "04_061_080"
        return "05_081_100"

    def summarize_records(self, records):
        if not records:
            return None

        count = len(records)
        ret_max_sum = 0.0
        ret_1w_sum = 0.0
        ret_1m_sum = 0.0
        ctx_sum = 0.0
        win_1m = 0
        priority_count = 0
        strong_count = 0
        max_best = -999999.0
        max_worst = 999999.0

        for item in records:
            ret_max = safe_float(item.get("ret_max"))
            ret_1w = safe_float(item.get("ret_1w"))
            ret_1m = safe_float(item.get("ret_1m"))
            ret_max_sum += ret_max
            ret_1w_sum += ret_1w
            ret_1m_sum += ret_1m
            ctx_sum += safe_float(item.get("context_score"))
            if ret_1m > 0.0:
                win_1m += 1
            if item.get("action") == "PRIORITY":
                priority_count += 1
            if item.get("action") == "STRONG":
                strong_count += 1
            if ret_max > max_best:
                max_best = ret_max
            if ret_max < max_worst:
                max_worst = ret_max

        return {
            "count": count,
            "avg_max": ret_max_sum / count,
            "avg_1w": ret_1w_sum / count,
            "avg_1m": ret_1m_sum / count,
            "win_1m_pct": win_1m / count * 100.0,
            "avg_ctx": ctx_sum / count,
            "priority_pct": priority_count / count * 100.0,
            "strong_pct": strong_count / count * 100.0,
            "best_max": max_best,
            "worst_max": max_worst,
        }

    def format_stat_line(self, label, stat):
        if stat is None:
            return f"{label:<18} {'0':>5} {'-':>8} {'-':>8} {'-':>8} {'-':>7} {'-':>8}"
        return (
            f"{label:<18} "
            f"{stat['count']:>5} "
            f"{stat['avg_max']:>+8.2f} "
            f"{stat['avg_1w']:>+8.2f} "
            f"{stat['avg_1m']:>+8.2f} "
            f"{stat['win_1m_pct']:>6.1f}% "
            f"{stat['avg_ctx']:>8.1f}"
        )

    def build_supply_validation_lines(self, selected_row):
        records = self.build_supply_records()
        selected_key = row_key(selected_row) if selected_row is not None else ""
        selected_manual = self.manual_scores.get(selected_key, {})
        selected_total = manual_score_total(selected_manual)

        lines = []
        lines.append("=" * 70)
        lines.append("[수급점수 성과 검증 v2.2]")
        lines.append(f"현재 선택 수급점수 manual_total={selected_total:.1f} / key={selected_key}")
        lines.append("수급점수는 program+institution+foreign+theme+news 합산이며, 각 항목은 0~20점입니다.")
        lines.append("")

        total_count = len(records)
        scored_records = [item for item in records if item.get("manual_total", 0.0) > 0.0]
        saved_records = [item for item in records if item.get("has_manual")]
        lines.append(f"[표본 상태] 현재 조회 {total_count}건 / 저장된 수급점수 {len(saved_records)}건 / 0점 초과 {len(scored_records)}건")
        if len(scored_records) < 5:
            lines.append("판정: 아직 수급점수 입력 표본이 부족합니다. 최소 20건 이상부터 Top/Bottom 비교가 의미 있어집니다.")
        elif len(scored_records) < 20:
            lines.append("판정: 초기 검증 가능 단계입니다. 방향성은 볼 수 있지만 과신하지 말고 표본을 계속 누적하세요.")
        else:
            lines.append("판정: 수급점수 구간별 성과 검증 가능 표본입니다.")
        lines.append("")

        lines.append("[수급점수 구간별 성과]")
        lines.append(f"{'Bucket':<18} {'건수':>5} {'avgMax':>8} {'avg1w':>8} {'avg1m':>8} {'win1m':>7} {'avgCtx':>8}")
        lines.append("-" * 70)
        bucket_order = ["00_NO_SCORE", "01_001_020", "02_021_040", "03_041_060", "04_061_080", "05_081_100"]
        bucket_labels = {
            "00_NO_SCORE": "0 / 미입력",
            "01_001_020": "1~20",
            "02_021_040": "21~40",
            "03_041_060": "41~60",
            "04_061_080": "61~80",
            "05_081_100": "81~100",
        }
        bucket_map = {}
        for item in records:
            b = self.bucket_name(item.get("manual_total", 0.0))
            if b not in bucket_map:
                bucket_map[b] = []
            bucket_map[b].append(item)

        for b in bucket_order:
            stat = self.summarize_records(bucket_map.get(b, []))
            lines.append(self.format_stat_line(bucket_labels[b], stat))

        lines.append("")
        lines.extend(self.build_top_bottom_lines(scored_records))
        lines.append("")
        lines.extend(self.build_supply_field_lines(scored_records))
        lines.append("")
        lines.extend(self.build_supply_verdict_lines(records, scored_records))
        return lines

    def build_top_bottom_lines(self, scored_records):
        lines = []
        lines.append("[수급점수 Top20% vs Bottom20%]")
        lines.append(f"{'Group':<18} {'건수':>5} {'avgMax':>8} {'avg1w':>8} {'avg1m':>8} {'win1m':>7} {'avgCtx':>8}")
        lines.append("-" * 70)

        if len(scored_records) < 5:
            lines.append("Top/Bottom 비교 불가: 0점 초과 수급점수 표본이 5건 미만입니다.")
            return lines

        ordered = sorted(scored_records, key=lambda x: x.get("manual_total", 0.0))
        n = max(1, int(math.ceil(len(ordered) * 0.2)))
        bottom = ordered[:n]
        top = ordered[-n:]
        bottom_stat = self.summarize_records(bottom)
        top_stat = self.summarize_records(top)
        lines.append(self.format_stat_line("Bottom 20%", bottom_stat))
        lines.append(self.format_stat_line("Top 20%", top_stat))

        if top_stat is not None and bottom_stat is not None:
            spread_max = top_stat["avg_max"] - bottom_stat["avg_max"]
            spread_1m = top_stat["avg_1m"] - bottom_stat["avg_1m"]
            spread_win = top_stat["win_1m_pct"] - bottom_stat["win_1m_pct"]
            lines.append("-" * 70)
            lines.append(
                f"{'Spread':<18} {'':>5} {spread_max:>+8.2f} {'':>8} {spread_1m:>+8.2f} {spread_win:>+6.1f}% {'':>8}"
            )

        return lines

    def build_supply_field_lines(self, scored_records):
        lines = []
        lines.append("[개별 수급항목별 10점 이상 그룹 성과]")
        lines.append(f"{'Field':<18} {'건수':>5} {'avgMax':>8} {'avg1w':>8} {'avg1m':>8} {'win1m':>7} {'avgCtx':>8}")
        lines.append("-" * 70)

        if not scored_records:
            lines.append("개별 항목 검증 불가: 0점 초과 수급점수 표본이 없습니다.")
            return lines

        for field_key, field_label in SUPPLY_FIELDS:
            subset = [item for item in scored_records if safe_float(item.get(field_key)) >= 10.0]
            stat = self.summarize_records(subset)
            lines.append(self.format_stat_line(field_label + ">=10", stat))

        lines.append("-" * 70)
        lines.append("해석: 특정 항목의 avg1m 또는 win1m가 낮으면 해당 항목은 단순 가산보다 감점/조건부 반영으로 바꿔야 합니다.")
        return lines

    def build_supply_verdict_lines(self, records, scored_records):
        lines = []
        lines.append("[검증 판정]")

        no_score = [item for item in records if item.get("manual_total", 0.0) <= 0.0]
        high_score = [item for item in records if item.get("manual_total", 0.0) >= 41.0]
        no_stat = self.summarize_records(no_score)
        high_stat = self.summarize_records(high_score)

        if len(scored_records) < 5:
            lines.append("- 표본 부족: 수급점수 저장 건수를 먼저 늘려야 합니다.")
            lines.append("- 우선순위: 실제로 눈에 띄는 수급/재료가 있었던 종목부터 20건 이상 채점하세요.")
            return lines

        if high_stat is None:
            lines.append("- 41점 이상 고점수군이 없습니다. 점수 분포가 너무 낮으면 검증력이 약합니다.")
            return lines

        if no_stat is None:
            lines.append("- 0점군이 없어 베이스라인 대비 검증은 생략합니다.")
            return lines

        max_edge = high_stat["avg_max"] - no_stat["avg_max"]
        one_month_edge = high_stat["avg_1m"] - no_stat["avg_1m"]
        win_edge = high_stat["win_1m_pct"] - no_stat["win_1m_pct"]

        lines.append(f"- 41점 이상군 vs 0점군 avgMax 차이: {max_edge:+.2f}%p")
        lines.append(f"- 41점 이상군 vs 0점군 avg1m 차이: {one_month_edge:+.2f}%p")
        lines.append(f"- 41점 이상군 vs 0점군 win1m 차이: {win_edge:+.1f}%p")

        passed = 0
        if max_edge > 0.0:
            passed += 1
        if one_month_edge > 0.0:
            passed += 1
        if win_edge > 0.0:
            passed += 1

        if passed >= 3:
            lines.append("- 결론: 수급점수는 현재 조회 구간에서 유효한 우선순위 필터로 보입니다.")
        elif passed == 2:
            lines.append("- 결론: 부분 유효합니다. ret_max/ret_1m/win1m 중 약한 축을 개별 항목별로 재검증하세요.")
        else:
            lines.append("- 결론: 현재 수급점수는 성과 설명력이 약합니다. 단순 합산보다 항목별 가중치/감점 로직이 필요합니다.")

        return lines


def main():
    app = SupplyValidationDashboardUI()
    app.mainloop()


if __name__ == "__main__":
    main()
