"""
점진적 윈도우 검증 시뮬레이션 앱

- 기준일(2026-01-25) 이전 = 초기 학습셋, 이후 = 일 단위 증분 검증.
- REQ-701: 매 스텝 검증 데이터를 다음 스텝 학습에 포함 (누적 학습).
- REQ-702: 검증 데이터 = 해당 일만 (최소 증분 단위).
- **2가설 동시 시뮬레이션**: V3(첫 앵커 확장 윈도우 9-14) vs 윈도우 9,10 전용. 결과 비교.
- **분리된 모드**: 최초 학습 cutoff로 예측 테이블 생성 후, 검증 데이터를 복수 구간으로 나누어 구간별로 [예측 테이블 생성 → 해당 구간만 검증] 순차 실행.
- 기존 모듈 수정 없음. 기존 API만 호출.
"""

import sys
from datetime import datetime as _dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd

from change_point.incremental_validation_utils import (
    get_incremental_validation_dates,
    get_train_cutoff_id_for_date,
    get_validation_ids_for_date,
)
from svg_parser_module import get_change_point_db_connection
from change_point.change_point_hypothesis_module import (
    get_hypothesis,
    get_simulation_predictions_db_connection,
    generate_simulation_predictions_table,
    validate_first_anchor_extended_window_v3_cp,
    validate_first_anchor_extended_window_v3_live_next_anchor_cp,
    validate_first_anchor_window9_only_cp,
    validate_first_anchor_window9_10_cp,
)
from change_point_prediction_module import load_preprocessed_grid_strings_cp

st.set_page_config(
    page_title="점진적 윈도우 검증 시뮬레이션",
    page_icon="📅",
    layout="wide",
)

LABEL_V3 = "첫 앵커 확장 윈도우 v3 (9-14)"
LABEL_W910 = "윈도우 9,10 전용"
WINDOW_SIZES = (9, 10, 11, 12, 13, 14)


def _fmt_dt(ts):
    """created_at 등 timestamp를 읽기 쉬운 문자열로."""
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return ""
    try:
        if isinstance(ts, str) and "T" in ts:
            d = _dt.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            d = pd.to_datetime(ts)
        return d.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


# 분리된 모드에서 사용 가능한 가설
SEPARATED_HYPOTHESES = [
    "first_anchor_extended_window_v3",
    "first_anchor_extended_window_v3_live_next_anchor",
    "first_anchor_window9_only",
    "first_anchor_window9_10",
]


def _get_validation_ids_in_range(conn, low_id, high_id):
    """검증 구간 (low_id, high_id] 에 해당하는 grid_string id 목록 반환."""
    low = 0 if low_id is None else low_id
    df = pd.read_sql_query(
        "SELECT id FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id",
        conn,
        params=[low, high_id],
    )
    return df["id"].tolist()


def _aggregate_day(results):
    """해당 일 결과 리스트에서 요약 dict 생성."""
    if not results:
        return None
    n = len(results)
    return {
        "validation_count": n,
        "avg_accuracy": round(sum(x["accuracy"] for x in results) / n, 2),
        "max_consecutive_failures": max(x["max_consecutive_failures"] for x in results),
        "total_predictions": sum(x["total_predictions"] for x in results),
        "total_skipped": sum(x.get("total_skipped", 0) for x in results),
    }


def run_incremental_validation_both(
    baseline_date,
    method,
    threshold,
    progress_callback=None,
):
    """
    V3와 윈도우 9,10 두 가설을 동시에 일 단위 점진적 검증.
    날짜마다 예측 테이블 1회 생성 후, 같은 검증 id에 대해 두 검증 함수 각각 실행.

    Returns:
        dict: daily_summaries (날짜별 V3 vs W910 비교), daily_results (날짜별 상세),
              overall_summary (가설별 전체 요약), results_by_hypothesis (가설별 daily_summaries/daily_results/overall)
    """
    dates = get_incremental_validation_dates(baseline_date)
    if not dates:
        empty_overall = {
            "total_days": 0,
            "total_validation_count": 0,
            "overall_avg_accuracy": 0.0,
            "overall_max_consecutive_failures": 0,
            "total_predictions": 0,
            "total_skipped": 0,
        }
        return {
            "daily_summaries": [],
            "daily_results": [],
            "overall_summary": {"v3": empty_overall.copy(), "window9_10": empty_overall.copy()},
            "results_by_hypothesis": {
                "v3": {"daily_summaries": [], "daily_results": [], "overall_summary": empty_overall.copy()},
                "window9_10": {"daily_summaries": [], "daily_results": [], "overall_summary": empty_overall.copy()},
            },
        }

    daily_summaries = []
    daily_results = []
    v3_summaries = []
    v3_results_by_day = []
    w910_summaries = []
    w910_results_by_day = []

    v3_acc_sum = 0.0
    v3_count = 0
    v3_max_fail = 0
    v3_tot_pred = 0
    v3_tot_skip = 0
    w910_acc_sum = 0.0
    w910_count = 0
    w910_max_fail = 0
    w910_tot_pred = 0
    w910_tot_skip = 0

    for i, validation_date in enumerate(dates):
        if progress_callback:
            progress_callback(i + 1, len(dates), validation_date)

        train_cutoff_id = get_train_cutoff_id_for_date(validation_date)
        validation_ids = get_validation_ids_for_date(validation_date)

        if train_cutoff_id is None or not validation_ids:
            continue

        try:
            generate_simulation_predictions_table(
                cutoff_grid_string_id=train_cutoff_id,
                window_sizes=WINDOW_SIZES,
                method=method,
                threshold=threshold,
                use_isolated_sim_db=True,
            )
        except Exception as e:
            if progress_callback:
                progress_callback(-1, len(dates), validation_date, error=str(e))
            continue

        pred_conn = get_simulation_predictions_db_connection()
        try:
            results_v3 = []
            results_w910 = []
            for gid in validation_ids:
                rv = validate_first_anchor_extended_window_v3_cp(
                    gid, train_cutoff_id,
                    window_sizes=WINDOW_SIZES,
                    method=method,
                    threshold=threshold,
                    predictions_conn=pred_conn,
                )
                rw = validate_first_anchor_window9_10_cp(
                    gid, train_cutoff_id,
                    method=method,
                    threshold=threshold,
                    predictions_conn=pred_conn,
                )
                if rv is not None:
                    results_v3.append(rv)
                if rw is not None:
                    results_w910.append(rw)
        finally:
            pred_conn.close()

        agg_v3 = _aggregate_day(results_v3)
        agg_w910 = _aggregate_day(results_w910)
        n = len(validation_ids)

        row_compare = {
            "date": validation_date,
            "validation_count": n,
            "v3_avg_accuracy": agg_v3["avg_accuracy"] if agg_v3 else 0.0,
            "v3_max_consecutive_failures": agg_v3["max_consecutive_failures"] if agg_v3 else 0,
            "v3_total_predictions": agg_v3["total_predictions"] if agg_v3 else 0,
            "v3_total_skipped": agg_v3["total_skipped"] if agg_v3 else 0,
            "w910_avg_accuracy": agg_w910["avg_accuracy"] if agg_w910 else 0.0,
            "w910_max_consecutive_failures": agg_w910["max_consecutive_failures"] if agg_w910 else 0,
            "w910_total_predictions": agg_w910["total_predictions"] if agg_w910 else 0,
            "w910_total_skipped": agg_w910["total_skipped"] if agg_w910 else 0,
        }
        daily_summaries.append(row_compare)
        daily_results.append({
            "date": validation_date,
            "v3_results": results_v3,
            "w910_results": results_w910,
        })

        if agg_v3:
            v3_summaries.append({"date": validation_date, **agg_v3})
            v3_results_by_day.append({"date": validation_date, "results": results_v3})
            v3_acc_sum += agg_v3["avg_accuracy"] * agg_v3["validation_count"]
            v3_count += agg_v3["validation_count"]
            v3_max_fail = max(v3_max_fail, agg_v3["max_consecutive_failures"])
            v3_tot_pred += agg_v3["total_predictions"]
            v3_tot_skip += agg_v3["total_skipped"]
        if agg_w910:
            w910_summaries.append({"date": validation_date, **agg_w910})
            w910_results_by_day.append({"date": validation_date, "results": results_w910})
            w910_acc_sum += agg_w910["avg_accuracy"] * agg_w910["validation_count"]
            w910_count += agg_w910["validation_count"]
            w910_max_fail = max(w910_max_fail, agg_w910["max_consecutive_failures"])
            w910_tot_pred += agg_w910["total_predictions"]
            w910_tot_skip += agg_w910["total_skipped"]

    overall_v3 = {
        "total_days": len(v3_summaries),
        "total_validation_count": v3_count,
        "overall_avg_accuracy": round(v3_acc_sum / v3_count, 2) if v3_count > 0 else 0.0,
        "overall_max_consecutive_failures": v3_max_fail,
        "total_predictions": v3_tot_pred,
        "total_skipped": v3_tot_skip,
    }
    overall_w910 = {
        "total_days": len(w910_summaries),
        "total_validation_count": w910_count,
        "overall_avg_accuracy": round(w910_acc_sum / w910_count, 2) if w910_count > 0 else 0.0,
        "overall_max_consecutive_failures": w910_max_fail,
        "total_predictions": w910_tot_pred,
        "total_skipped": w910_tot_skip,
    }

    return {
        "daily_summaries": daily_summaries,
        "daily_results": daily_results,
        "overall_summary": {"v3": overall_v3, "window9_10": overall_w910},
        "results_by_hypothesis": {
            "v3": {"daily_summaries": v3_summaries, "daily_results": v3_results_by_day, "overall_summary": overall_v3},
            "window9_10": {"daily_summaries": w910_summaries, "daily_results": w910_results_by_day, "overall_summary": overall_w910},
        },
    }


def _run_separated_validation(initial_cutoff, range_ends, hyp_name, method, threshold, progress_placeholder, status_placeholder):
    """분리된 모드: 구간별로 예측 테이블 생성 → 해당 구간만 검증. 반환: range_results 리스트."""
    ws = list(WINDOW_SIZES)
    conn = get_change_point_db_connection()
    range_results = []
    try:
        for i, end_id in enumerate(range_ends):
            train_cutoff = initial_cutoff if i == 0 else range_ends[i - 1]
            if status_placeholder:
                status_placeholder.text(f"구간 {i+1}/{len(range_ends)}: 예측 테이블 생성 (cutoff ID {train_cutoff}) 후 검증 (id ≤ {end_id})...")
            if progress_placeholder:
                progress_placeholder.progress((i + 0.3) / len(range_ends))
            try:
                generate_simulation_predictions_table(
                    cutoff_grid_string_id=train_cutoff,
                    window_sizes=tuple(ws),
                    method=method,
                    threshold=threshold,
                    use_isolated_sim_db=True,
                )
            except Exception as e:
                range_results.append({
                    "range_index": i + 1,
                    "train_cutoff": train_cutoff,
                    "range_end": end_id,
                    "results": [],
                    "summary": {},
                    "error": str(e),
                })
                continue
            if progress_placeholder:
                progress_placeholder.progress((i + 0.6) / len(range_ends))
            validation_ids = _get_validation_ids_in_range(conn, train_cutoff, end_id)
            pred_conn = get_simulation_predictions_db_connection()
            results = []
            try:
                for gid in validation_ids:
                    if hyp_name == "first_anchor_extended_window_v3":
                        r = validate_first_anchor_extended_window_v3_cp(
                            gid, train_cutoff, window_sizes=tuple(ws), method=method, threshold=threshold,
                            predictions_conn=pred_conn,
                        )
                    elif hyp_name == "first_anchor_extended_window_v3_live_next_anchor":
                        r = validate_first_anchor_extended_window_v3_live_next_anchor_cp(
                            gid, train_cutoff, window_sizes=tuple(ws), method=method, threshold=threshold,
                            predictions_conn=pred_conn,
                        )
                    elif hyp_name == "first_anchor_window9_only":
                        r = validate_first_anchor_window9_only_cp(
                            gid, train_cutoff, method=method, threshold=threshold,
                            predictions_conn=pred_conn,
                        )
                    elif hyp_name == "first_anchor_window9_10":
                        r = validate_first_anchor_window9_10_cp(
                            gid, train_cutoff, method=method, threshold=threshold,
                            predictions_conn=pred_conn,
                        )
                    else:
                        r = None
                    if r is not None:
                        results.append(r)
            finally:
                pred_conn.close()
            if progress_placeholder:
                progress_placeholder.progress((i + 1) / len(range_ends))
            if not results:
                summary = {"total_grid_strings": 0, "avg_accuracy": 0.0, "max_consecutive_failures": 0, "total_predictions": 0, "total_skipped": 0}
            else:
                n = len(results)
                summary = {
                    "total_grid_strings": n,
                    "avg_accuracy": sum(x["accuracy"] for x in results) / n,
                    "max_consecutive_failures": max(x["max_consecutive_failures"] for x in results),
                    "total_predictions": sum(x["total_predictions"] for x in results),
                    "total_skipped": sum(x.get("total_skipped", 0) for x in results),
                }
            range_results.append({
                "range_index": i + 1,
                "train_cutoff": train_cutoff,
                "range_end": end_id,
                "results": results,
                "summary": summary,
            })
    finally:
        conn.close()
    return range_results


def _main_separated_mode():
    """분리된 모드 UI + 실행 + 결과 표시."""
    df_mw = load_preprocessed_grid_strings_cp()
    if df_mw is None or len(df_mw) == 0:
        st.warning("preprocessed_grid_strings 데이터가 없습니다.")
        return
    # 최초 cutoff 선택: ID 역순(최신이 위) + 생성 일시 표시 (로더가 이미 ORDER BY id DESC)
    ids_desc = df_mw["id"].tolist()
    created_at_list = df_mw["created_at"].tolist()
    ids_asc = sorted(df_mw["id"].tolist())

    st.markdown("#### 분리된 모드: 구간별 순차 검증")
    st.info("최초 학습 cutoff로 예측 테이블 생성 → 1구간만 검증 → 1구간 끝 ID까지 예측 테이블 재생성 → 2구간만 검증 → … 순차 실행.")

    sep_hyp = st.selectbox(
        "가설 선택",
        SEPARATED_HYPOTHESES,
        format_func=lambda x: get_hypothesis(x).get_name(),
        key="separated_hypothesis",
    )
    hyp_instance = get_hypothesis(sep_hyp)
    st.caption(hyp_instance.get_description())
    st.markdown("---")
    st.markdown("##### 1. 최초 학습 데이터 cutoff ID")
    idx_init = st.selectbox(
        "최초 학습데이터 cutoff ID (이 ID 이하로 최초 예측 테이블 생성)",
        range(len(ids_desc)),
        format_func=lambda idx: f"ID {ids_desc[idx]} ({_fmt_dt(created_at_list[idx])})",
        key="sep_init_cutoff_idx",
    )
    initial_cutoff = ids_desc[idx_init]
    st.markdown("##### 2. 검증 구간 개수 및 구간 끝 ID")
    num_ranges = st.number_input("구간 개수", 2, 20, 4, key="sep_num_ranges")
    range_ends = []
    prev = initial_cutoff
    for i in range(int(num_ranges)):
        options_i = [x for x in ids_asc if x > prev]
        if not options_i:
            st.warning(f"구간 {i+1}: 이전 끝 ID({prev})보다 큰 ID가 없습니다.")
            break
        chosen = st.selectbox(
            f"구간 {i+1} 끝 ID (검증: 이전 끝 < id ≤ 선택 ID)",
            options_i,
            format_func=lambda x: f"ID {x}",
            key=f"sep_re_{i}",
        )
        range_ends.append(chosen)
        prev = chosen
    st.markdown("##### 3. 예측 방법·임계값")
    method_sep = st.selectbox("예측 방법", ["빈도 기반", "가중치 기반", "안전 우선"], key="method_sep")
    thresh_sep = st.number_input("임계값", 0, 100, 0, key="thresh_sep")

    if st.button("분리된 모드 시뮬레이션 실행", type="primary", key="run_separated_btn"):
        if len(range_ends) < int(num_ranges):
            st.warning("모든 구간 끝 ID를 선택하세요.")
        else:
            progress_placeholder = st.empty()
            status_placeholder = st.empty()
            with st.spinner("분리된 모드 시뮬레이션 실행 중..."):
                try:
                    range_results = _run_separated_validation(
                        initial_cutoff, range_ends, sep_hyp, method_sep, thresh_sep,
                        progress_placeholder, status_placeholder,
                    )
                    st.session_state["separated_result"] = {
                        "range_results": range_results,
                        "hypothesis": sep_hyp,
                    }
                except Exception as e:
                    st.error(f"실행 실패: {e}")
                    import traceback
                    st.code(traceback.format_exc())
                finally:
                    progress_placeholder.empty()
                    status_placeholder.empty()

    if "separated_result" not in st.session_state:
        return

    res = st.session_state["separated_result"]
    range_results = res.get("range_results", [])
    st.markdown("---")
    st.markdown("## 분리된 모드 시뮬레이션 결과")
    st.markdown("### 구간별 요약")
    rows = []
    for rec in range_results:
        sm = rec.get("summary", {}) or {}
        err = rec.get("error", "")
        rows.append({
            "구간": rec["range_index"],
            "학습 cutoff ID": rec["train_cutoff"],
            "검증 끝 ID": rec["range_end"],
            "검증 건수": sm.get("total_grid_strings", 0) if not err else "-",
            "평균 정확도(%)": f"{sm.get('avg_accuracy', 0):.2f}" if not err else "-",
            "최대 연속 불일치": sm.get("max_consecutive_failures", "-") if not err else "-",
            "오류": err or "",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.markdown("### 구간별 상세")
    for rec in range_results:
        rr = rec.get("results", [])
        with st.expander(f"구간 {rec['range_index']}: 학습 cutoff ID {rec['train_cutoff']} → 검증 id ≤ {rec['range_end']} ({len(rr)}건)"):
            if rec.get("error"):
                st.error(rec["error"])
            elif not rr:
                st.caption("검증 결과 없음")
            else:
                detail_rows = [
                    {
                        "grid_string_id": r["grid_string_id"],
                        "정확도(%)": round(r["accuracy"], 2),
                        "최대 연속 불일치": r["max_consecutive_failures"],
                        "예측 횟수": r["total_predictions"],
                        "스킵": r.get("total_skipped", 0),
                    }
                    for r in rr
                ]
                st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)


def main():
    st.title("점진적 윈도우 검증 시뮬레이션")
    st.markdown("""
    **일 단위 점진적 검증**: V3 vs 윈도우 9,10 전용을 같은 일자·같은 데이터로 검증해 결과를 비교합니다.  
    **분리된 모드**: 최초 학습 cutoff로 예측 테이블 생성 후, 검증 데이터를 복수 구간으로 나누어 구간별로 [예측 테이블 생성 → 해당 구간만 검증] 순차 실행.
    """)

    app_mode = st.radio(
        "모드",
        ["일 단위 점진적 검증", "분리된 모드 (구간별 순차 검증)"],
        horizontal=True,
        key="app_mode",
    )

    if app_mode == "분리된 모드 (구간별 순차 검증)":
        _main_separated_mode()
        return

    st.markdown("---")
    st.markdown("### 일 단위 점진적 검증")
    st.caption("기준일 **이전** = 초기 학습셋, 기준일 **이후** = **일 단위** [학습 → 해당 일만 검증 → 다음 스텝 학습에 포함] (REQ-701, REQ-702).")

    baseline_date = st.date_input(
        "기준일 (Baseline)",
        value=pd.Timestamp("2026-01-25").date(),
        key="baseline_date",
    )
    baseline_str = str(baseline_date)

    method = st.selectbox("예측 방법", ["빈도 기반", "가중치 기반", "안전 우선"], key="method")
    threshold = st.number_input("임계값", 0, 100, 0, key="threshold")
    st.caption("두 가설 모두 동일한 예측 테이블(9~14)을 사용합니다. 실행 시 V3와 윈도우 9,10 전용을 동시에 검증합니다.")

    # 기준일 이후 날짜 미리보기
    try:
        preview_dates = get_incremental_validation_dates(baseline_str)
        if preview_dates:
            st.info(f"기준일 이후 검증 대상 일수: **{len(preview_dates)}**일 (예: {preview_dates[:5]}{'...' if len(preview_dates) > 5 else ''})")
        else:
            st.warning("기준일 이후 데이터가 없거나 created_at이 없습니다.")
    except Exception as e:
        st.warning(f"날짜 조회 실패: {e}")

    if st.button("시뮬레이션 실행 (2가설 동시)", type="primary", key="run_btn"):
        progress_placeholder = st.empty()
        status_placeholder = st.empty()

        def progress_callback(current, total, date, error=None):
            if error:
                status_placeholder.error(f"날짜 {date} 오류: {error}")
                return
            progress_placeholder.progress(current / total, text=f"진행: {date} ({current}/{total} 일)")

        with st.spinner("2가설 점진적 검증 실행 중..."):
            try:
                out = run_incremental_validation_both(
                    baseline_date=baseline_str,
                    method=method,
                    threshold=threshold,
                    progress_callback=progress_callback,
                )
            except Exception as e:
                st.error(f"실행 실패: {e}")
                import traceback
                st.code(traceback.format_exc())
                return
            finally:
                progress_placeholder.empty()
                status_placeholder.empty()

        st.session_state["incremental_result"] = out

    if "incremental_result" not in st.session_state:
        return

    out = st.session_state["incremental_result"]
    daily_summaries = out["daily_summaries"]
    daily_results = out["daily_results"]
    overall = out["overall_summary"]
    o_v3 = overall["v3"]
    o_w910 = overall["window9_10"]

    st.markdown("---")
    st.markdown("## 시뮬레이션 결과 (2가설 비교)")

    # 상단 — 전체 누적 요약: 2가설 나란히 비교
    st.markdown("### 전체 누적 요약 비교")
    col_v3, col_w910 = st.columns(2)
    with col_v3:
        st.subheader(LABEL_V3)
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("검증 건수", f"{o_v3['total_validation_count']:,}")
        with c2:
            st.metric("평균 정확도", f"{o_v3['overall_avg_accuracy']:.2f}%")
        with c3:
            st.metric("최대 연속 불일치", o_v3["overall_max_consecutive_failures"])
        st.caption(f"총 예측: {o_v3['total_predictions']:,} / 스킵: {o_v3['total_skipped']:,}")
    with col_w910:
        st.subheader(LABEL_W910)
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("검증 건수", f"{o_w910['total_validation_count']:,}")
        with c2:
            st.metric("평균 정확도", f"{o_w910['overall_avg_accuracy']:.2f}%")
        with c3:
            st.metric("최대 연속 불일치", o_w910["overall_max_consecutive_failures"])
        st.caption(f"총 예측: {o_w910['total_predictions']:,} / 스킵: {o_w910['total_skipped']:,}")

    # 중단 — 일자별 비교 테이블 (V3 vs 윈도우 9,10)
    st.markdown("### 일자별 비교")
    if not daily_summaries:
        st.info("일별 요약이 없습니다.")
    else:
        df = pd.DataFrame(daily_summaries)
        df_display = pd.DataFrame({
            "날짜": df["date"],
            "검증 건수": df["validation_count"],
            f"{LABEL_V3} 평균정확도(%)": df["v3_avg_accuracy"],
            f"{LABEL_V3} 최대연속불일치": df["v3_max_consecutive_failures"],
            f"{LABEL_W910} 평균정확도(%)": df["w910_avg_accuracy"],
            f"{LABEL_W910} 최대연속불일치": df["w910_max_consecutive_failures"],
        })
        st.dataframe(df_display, use_container_width=True, hide_index=True)

    # 하단 — 일자별 상세: 날짜별 expander 안에 V3 / 윈도우 9,10 탭 또는 나란히
    st.markdown("### 일자별 상세 (가설별)")
    for dr in daily_results:
        date = dr["date"]
        v3_results = dr["v3_results"]
        w910_results = dr["w910_results"]
        with st.expander(f"날짜: {date} (검증 {len(v3_results)}건)"):
            tab_v3, tab_w910 = st.tabs([LABEL_V3, LABEL_W910])
            with tab_v3:
                if not v3_results:
                    st.caption("해당 일 V3 검증 결과 없음")
                else:
                    rows = [
                        {
                            "grid_string_id": r["grid_string_id"],
                            "정확도(%)": round(r["accuracy"], 2),
                            "최대 연속 불일치": r["max_consecutive_failures"],
                            "예측 횟수": r["total_predictions"],
                            "스킵": r.get("total_skipped", 0),
                        }
                        for r in v3_results
                    ]
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            with tab_w910:
                if not w910_results:
                    st.caption("해당 일 윈도우 9,10 검증 결과 없음")
                else:
                    rows = [
                        {
                            "grid_string_id": r["grid_string_id"],
                            "정확도(%)": round(r["accuracy"], 2),
                            "최대 연속 불일치": r["max_consecutive_failures"],
                            "예측 횟수": r["total_predictions"],
                            "스킵": r.get("total_skipped", 0),
                        }
                        for r in w910_results
                    ]
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
