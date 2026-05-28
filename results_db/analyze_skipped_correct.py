"""
스킵된 스텝 중 예측 평가 가능한 행에 대해, '스킵했지만 일치(is_correct=1)' 구간의
신뢰도·스킵 사유 분석. CLI 요약 출력 및 선택적 CSV 내보내기.

실행 (프로젝트 루트):
  python -m results_db.analyze_skipped_correct --hypothesis first_anchor_window9_freq518_win50
  python -m results_db.analyze_skipped_correct --out skipped_evaluable.csv
"""

import argparse
import csv
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_change_point_dir = _root / "change_point"
for p in (_root, _change_point_dir):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from change_point.results_storage import (
    query_skipped_steps_evaluable_by_hypothesis,
    query_skipped_correct_insight_aggregates,
    query_skipped_correct_insight_aggregates_sim_wr_insufficient,
    query_skipped_sim_win_rates_extracted,
    query_skipped_sim_wr_insufficient_by_confidence,
)

DEFAULT_HYPOTHESIS = "first_anchor_window9_freq518_win50"


def _confidence_band(conf):
    """신뢰도 구간 라벨 (뷰어 인사이트용과 동일)."""
    if conf is None:
        return "미상"
    c = float(conf)
    if c < 40:
        return "40% 미만"
    if c < 50:
        return "40~50%"
    if c < 51.3:
        return "50~51.3%"
    return "51.3% 이상"


def run_analysis(hypothesis_key, db_path=None, out_path=None):
    df = query_skipped_steps_evaluable_by_hypothesis(hypothesis_key, db_path)
    agg = query_skipped_correct_insight_aggregates(hypothesis_key, db_path)

    total_eval = agg["total_evaluable"]
    if total_eval == 0:
        print(f"가설 '{hypothesis_key}': 예측 평가 가능한 스킵 스텝이 없습니다.")
        return

    total_correct = agg["total_correct"]
    total_wrong = agg["total_wrong"]
    pct_correct = 100.0 * total_correct / total_eval if total_eval else 0

    print(f"가설: {hypothesis_key}")
    print("=" * 60)
    print(f"예측 평가 가능한 스킵 스텝: {total_eval:,}")
    print(f"  - 일치(is_correct=1): {total_correct:,} ({pct_correct:.1f}%)")
    print(f"  - 불일치(is_correct=0): {total_wrong:,}")

    if total_correct > 0:
        print("\n[스킵했지만 일치한 스텝] 신뢰도")
        print(f"  평균: {agg['confidence_mean_correct']:.2f}%")
        print(f"  중앙값: {agg['confidence_median_correct']:.2f}%")
        print(f"  25% 백분위: {agg['confidence_p25_correct']:.2f}%")
        print(f"  75% 백분위: {agg['confidence_p75_correct']:.2f}%")

        print("\n[스킵했지만 일치] 스킵 사유별 건수·비율")
        for row in agg["skip_reason_counts_correct"]:
            print(f"  {row['skip_reason']}: {row['count']:,} ({row['pct']}%)")

    if total_wrong > 0 and agg["skip_reason_counts_wrong"]:
        print("\n[스킵했지만 불일치] 스킵 사유별 건수·비율 (대비)")
        for row in agg["skip_reason_counts_wrong"]:
            print(f"  {row['skip_reason']}: {row['count']:,} ({row['pct']}%)")

    # 스크립트 전용: skip_reason × confidence band 크로스탭 (일치만)
    if total_correct > 0 and len(df) > 0:
        correct_df = df[df["is_correct"] == 1].copy()
        correct_df["confidence_band"] = correct_df["confidence"].map(_confidence_band)
        cross = correct_df.groupby(["skip_reason", "confidence_band"]).size().reset_index(name="count")
        print("\n[스킵했지만 일치] 스킵 사유 × 신뢰도 구간")
        for _, r in cross.iterrows():
            print(f"  {r['skip_reason']} | {r['confidence_band']}: {r['count']:,}")

    # 시뮬레이션 승률 부족 케이스만 별도 분석
    agg_sw = query_skipped_correct_insight_aggregates_sim_wr_insufficient(hypothesis_key, db_path)
    total_eval_sw = agg_sw["total_evaluable"]
    print("\n" + "=" * 60)
    print("[시뮬레이션 승률 부족 케이스] (스킵 사유가 '시뮬레이션 승률 부족'인 경우만)")
    if total_eval_sw == 0:
        print("  해당 케이스 없음.")
    else:
        total_correct_sw = agg_sw["total_correct"]
        total_wrong_sw = agg_sw["total_wrong"]
        pct_sw = 100.0 * total_correct_sw / total_eval_sw if total_eval_sw else 0
        print(f"  예측 평가 가능 스킵: {total_eval_sw:,}")
        print(f"  - 일치: {total_correct_sw:,} ({pct_sw:.1f}%)")
        print(f"  - 불일치: {total_wrong_sw:,}")
        if total_correct_sw > 0:
            print(f"  신뢰도(일치 구간) 평균: {agg_sw['confidence_mean_correct']:.2f}%, 중앙값: {agg_sw['confidence_median_correct']:.2f}%")

    # 스킵 사유에서 추출한 시뮬 승률(해당 스텝 승률) 분포
    print("\n" + "=" * 60)
    print("[스킵 사유에서 추출한 시뮬 승률] (형식: '시뮬레이션 승률 부족 (50.0% < 52.0%)' → 50.0 취합)")
    sim_wr_agg = query_skipped_sim_win_rates_extracted(hypothesis_key, db_path)
    if sim_wr_agg["count"] == 0:
        print("  추출 가능한 행 없음.")
    else:
        print(f"  건수: {sim_wr_agg['count']:,}")
        print(f"  평균: {sim_wr_agg['mean']:.2f}%, 중앙값: {sim_wr_agg['median']:.2f}%, 25%: {sim_wr_agg['p25']:.2f}%, 75%: {sim_wr_agg['p75']:.2f}%")
        bc = sim_wr_agg["by_correct"]
        c_mean = bc["correct"]["mean"]
        w_mean = bc["wrong"]["mean"]
        print(f"  일치한 스텝: {bc['correct']['count']:,}건, 시뮬 승률 평균: {c_mean:.2f}%" if c_mean is not None else f"  일치한 스텝: {bc['correct']['count']:,}건")
        print(f"  불일치한 스텝: {bc['wrong']['count']:,}건, 시뮬 승률 평균: {w_mean:.2f}%" if w_mean is not None else f"  불일치한 스텝: {bc['wrong']['count']:,}건")

    # 시뮬 승률 부족 케이스 상세: 신뢰도 구간별 시뮬 승률
    print("\n" + "=" * 60)
    print("[시뮬 승률 부족 케이스 상세] 신뢰도 구간별 시뮬 승률 (신뢰도 높은 케이스의 시뮬 승률 특성)")
    print("  일치율 = (일치 건수 / 해당 구간 스킵된 전체 스텝 수) × 100")
    detail = query_skipped_sim_wr_insufficient_by_confidence(hypothesis_key, db_path)
    if detail["total_count"] == 0:
        print("  해당 케이스 없음.")
    else:
        print(f"  전체 건수: {detail['total_count']:,}")
        if detail["summary_by_confidence_band"]:
            print("  신뢰도 구간별 (건수=스킵 전체, 일치율=스킵 전체 기준):")
            for row in detail["summary_by_confidence_band"]:
                m = row.get("mean_sim_win_rate")
                med = row.get("median_sim_win_rate")
                m_s = f"{m:.1f}%" if m is not None else "—"
                med_s = f"{med:.1f}%" if med is not None else "—"
                n_wr = row.get("sim_wr_valid_count", row["count"])
                print(f"    {row['confidence_band']}: 스킵 전체 {row['count']:,}건, 시뮬 승률 추출 {n_wr:,}건, 평균 {m_s}, 중앙값 {med_s}, 일치 {row['correct_count']:,}건 (일치율 {row['correct_rate_pct']}%)")
        high = detail["high_confidence"]
        low = detail["lower_confidence"]
        th = high["threshold"]
        print(f"  신뢰도 {th}% 이상: {high['count']:,}건, 시뮬 승률 평균 {high['mean_sim_win_rate']:.1f}%" if high["mean_sim_win_rate"] is not None else f"  신뢰도 {th}% 이상: {high['count']:,}건")
        print(f"  신뢰도 {th}% 미만: {low['count']:,}건, 시뮬 승률 평균 {low['mean_sim_win_rate']:.1f}%" if low["mean_sim_win_rate"] is not None else f"  신뢰도 {th}% 미만: {low['count']:,}건")
        if high["mean_sim_win_rate"] is not None and low["mean_sim_win_rate"] is not None and high["count"] and low["count"]:
            diff = high["mean_sim_win_rate"] - low["mean_sim_win_rate"]
            print(f"  → 신뢰도 높은 구간이 시뮬 승률 {abs(diff):.1f}% {'더 높음' if diff > 0 else '더 낮음' if diff < 0 else '비슷'}")

    if out_path:
        fieldnames = [
            "run_id", "grid_string_id", "step", "prefix", "window_size",
            "predicted", "actual", "is_correct", "confidence", "skip_reason",
        ]
        rows = df.to_dict("records")
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"\nCSV 저장: {out_path} ({len(rows):,}행)")


def main():
    parser = argparse.ArgumentParser(
        description="스킵 스텝 중 예측 평가 가능한 행에 대해, 스킵했지만 일치한 구간의 신뢰도·스킵 사유 분석"
    )
    parser.add_argument("--db", default=None, help="결과 DB 경로")
    parser.add_argument("--hypothesis", default=DEFAULT_HYPOTHESIS, help="가설 키")
    parser.add_argument("--out", default=None, help="예측 평가 가능 스킵 스텝 CSV 저장 경로")
    args = parser.parse_args()

    run_analysis(
        hypothesis_key=args.hypothesis,
        db_path=args.db,
        out_path=args.out,
    )


if __name__ == "__main__":
    main()
