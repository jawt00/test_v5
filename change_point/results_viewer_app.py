"""
시뮬레이션/라이브 결과 조회 앱

- 연속 불일치 높은 결과: 문제 케이스 우선 탐색
- 통계 대시보드: max_consecutive_failures 분포, 평균 정확도 등
- 상세 조회: run_id/grid_string_id로 직접 조회
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd

from svg_parser_module import get_change_point_db_connection
from results_storage import (
    query_simulation_by_grid_string_id,
    query_simulation_step_events,
    query_simulation_run_detail,
    query_simulation_runs_list,
    query_simulation_stats,
    query_simulation_high_failure_results,
    query_simulation_hypothesis_keys,
    query_live_run_detail,
    query_live_runs_list,
    query_live_stats,
    query_live_high_failure_results,
)

st.set_page_config(
    page_title="결과 조회",
    page_icon="📋",
    layout="wide",
)


def _format_match(is_correct):
    if is_correct is True or is_correct == 1:
        return "O"
    if is_correct is False or is_correct == 0:
        return "X"
    return "-"


def _format_predicted_display(entry, is_live=False):
    predicted = entry.get("predicted")
    skipped = entry.get("skipped") == 1 if isinstance(entry.get("skipped"), (int, float)) else bool(entry.get("skipped"))
    skip_reason = entry.get("skip_reason") or ""
    if skipped and skip_reason:
        return f"- ({skip_reason})"
    if skipped:
        return "- (skip)"
    return str(predicted) if predicted else "-"


def build_history_table_rows(events, is_live=False):
    """step_events 또는 live_step_events를 테이블 행 리스트로 변환."""
    rows = []
    for e in events or []:
        is_correct = e.get("is_correct")
        match_status = _format_match(is_correct)
        pred_display = _format_predicted_display(e, is_live)
        conf = e.get("confidence", 0) or 0
        conf_str = f"{conf:.1f}%" if e.get("predicted") else "-"

        row = {
            "Step": e.get("step", 0),
            "Position": e.get("position", ""),
            "Anchor": e.get("anchor", ""),
            "Window Size": e.get("window_size", ""),
            "Prefix": e.get("prefix", ""),
            "예측": pred_display,
            "실제값": e.get("actual", "-"),
            "일치": match_status,
            "신뢰도": conf_str,
            "스킵 사유": e.get("skip_reason", "") or "",
        }
        if not is_live:
            row["선택 윈도우"] = e.get("selected_window_size", "")
        rows.append(row)
    return rows


def get_grid_string_by_id(grid_string_id):
    """preprocessed_grid_strings에서 grid_string 조회 (change_point DB)."""
    conn = get_change_point_db_connection()
    try:
        df = pd.read_sql_query(
            "SELECT grid_string FROM preprocessed_grid_strings WHERE id = ?",
            conn,
            params=[grid_string_id],
        )
        return df.iloc[0]["grid_string"] if len(df) > 0 else None
    finally:
        conn.close()


def query_prediction_table_confidence_stats(window_sizes=(9, 10, 11)):
    """
    simulation_predictions_change_point 테이블에서
    지정 윈도우·모든 method별 신뢰도 요약 조회.
    """
    conn = get_change_point_db_connection()
    try:
        placeholders = ",".join("?" * len(window_sizes))
        q = f"""
            SELECT
                method AS method,
                window_size AS window_size,
                COUNT(*) AS record_count,
                AVG(confidence) AS avg_confidence,
                MIN(confidence) AS min_confidence,
                MAX(confidence) AS max_confidence
            FROM simulation_predictions_change_point
            WHERE window_size IN ({placeholders})
            GROUP BY method, window_size
            ORDER BY method, window_size
        """
        df = pd.read_sql_query(q, conn, params=list(window_sizes))
        return df
    except Exception as e:
        return pd.DataFrame()
    finally:
        conn.close()


def query_prediction_table_prefix_detail(window_sizes=(9, 10, 11)):
    """
    simulation_predictions_change_point 테이블에서
    모든 prefix별 신뢰도 상세 조회.
    """
    conn = get_change_point_db_connection()
    try:
        placeholders = ",".join("?" * len(window_sizes))
        q = f"""
            SELECT
                window_size,
                prefix,
                method,
                threshold,
                predicted_value,
                confidence,
                b_ratio,
                p_ratio
            FROM simulation_predictions_change_point
            WHERE window_size IN ({placeholders})
            ORDER BY window_size, prefix, method
        """
        df = pd.read_sql_query(q, conn, params=list(window_sizes))
        return df
    except Exception as e:
        return pd.DataFrame()
    finally:
        conn.close()


def main():
    st.title("시뮬레이션 / 라이브 결과 조회")
    st.markdown("통계, 연속 불일치 높은 결과 파악, 상세 히스토리 조회")
    st.markdown("---")

    t1, t2, t3 = st.tabs([
        "연속 불일치 높은 결과",
        "통계 대시보드",
        "상세 조회",
    ])

    with t1:
        _render_high_failure_tab()
    with t2:
        _render_stats_tab()
    with t3:
        _render_detail_tab()

    st.markdown("---")
    st.markdown("## 예측 테이블 신뢰도")
    _render_prediction_table_confidence_section()


def _render_high_failure_tab():
    """연속 불일치 높은 결과 탭."""
    st.markdown("### 연속 불일치 높은 결과")
    st.caption("문제 케이스를 우선적으로 파악합니다.")

    run_type = st.radio("Run 유형", ["시뮬레이션", "라이브"], horizontal=True, key="hf_run_type")

    min_mcf_opts = [(0, "0회 이상"), (1, "1회 이상"), (2, "2회 이상"), (3, "3회 이상"), (4, "4회 이상"), (5, "5회 이상")]
    min_mcf = st.selectbox(
        "최소 연속 불일치",
        [o[0] for o in min_mcf_opts],
        format_func=lambda x: next((o[1] for o in min_mcf_opts if o[0] == x), str(x)),
        index=1,
        key="hf_min_mcf",
    )

    hypothesis_key = None
    if run_type == "시뮬레이션":
        hyp_keys = query_simulation_hypothesis_keys()
        hyp_opts = [None] + hyp_keys
        hyp_idx = st.selectbox(
            "가설 필터",
            range(len(hyp_opts)),
            format_func=lambda i: "전체" if hyp_opts[i] is None else hyp_opts[i],
            key="hf_hypothesis",
        )
        hypothesis_key = hyp_opts[hyp_idx]

    if st.button("조회", key="hf_query"):
        if run_type == "시뮬레이션":
            st.session_state["hf_sim_results"] = query_simulation_high_failure_results(
                min_max_consecutive_failures=min_mcf,
                limit=100,
                hypothesis_key=hypothesis_key,
            )
            st.session_state["hf_run_type"] = "sim"
        else:
            st.session_state["hf_live_results"] = query_live_high_failure_results(
                min_max_consecutive_failures=min_mcf,
                limit=100,
            )
            st.session_state["hf_run_type"] = "live"

    if run_type == "시뮬레이션" and "hf_sim_results" in st.session_state:
        results = st.session_state["hf_sim_results"]
        if not results:
            st.info("조건에 맞는 시뮬레이션 결과가 없습니다.")
        else:
            st.markdown(f"#### 시뮬레이션 결과 ({len(results)}건)")
            df = pd.DataFrame([
                {
                    "run_id": r.get("run_id", ""),
                    "hypothesis": r.get("hypothesis_key", ""),
                    "cutoff": r.get("cutoff_grid_string_id", ""),
                    "grid_string_id": r.get("grid_string_id", ""),
                    "최대연속불일치": r.get("max_consecutive_failures", 0),
                    "정확도": f"{r.get('accuracy', 0):.2f}%",
                    "예측수": r.get("total_predictions", 0),
                    "스킵": r.get("total_skipped", 0),
                    "created_at": str(r.get("created_at", ""))[:19],
                }
                for r in results
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

            selected_idx = st.selectbox(
                "상세 조회할 행 선택",
                range(len(results)),
                format_func=lambda i: f"run={str(results[i].get('run_id',''))[:8]}... gid={results[i].get('grid_string_id')} mcf={results[i].get('max_consecutive_failures')}",
                key="hf_sim_row_select",
            )
            if st.button("상세 조회", key="hf_sim_go_detail"):
                r = results[selected_idx]
                st.session_state["detail_run_id"] = r.get("run_id")
                st.session_state["detail_run_type"] = "sim"
                st.session_state["detail_grid_string_id"] = r.get("grid_string_id")
                st.rerun()

    elif run_type == "라이브" and "hf_live_results" in st.session_state:
        results = st.session_state["hf_live_results"]
        if not results:
            st.info("조건에 맞는 라이브 결과가 없습니다.")
        else:
            st.markdown(f"#### 라이브 결과 ({len(results)}건)")
            df = pd.DataFrame([
                {
                    "run_id": r.get("run_id", ""),
                    "최대연속불일치": r.get("max_consecutive_failures", 0),
                    "정확도": f"{r.get('accuracy', 0):.2f}%",
                    "예측수": r.get("total_predictions", 0),
                    "grid_string 미리보기": (r.get("grid_string_preview", "") or "")[:50],
                    "created_at": str(r.get("created_at", ""))[:19],
                }
                for r in results
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

            selected_idx = st.selectbox(
                "상세 조회할 행 선택",
                range(len(results)),
                format_func=lambda i: f"run={str(results[i].get('run_id',''))[:8]}... mcf={results[i].get('max_consecutive_failures')}",
                key="hf_live_row_select",
            )
            if st.button("상세 조회", key="hf_live_go_detail"):
                r = results[selected_idx]
                st.session_state["detail_run_id"] = r.get("run_id")
                st.session_state["detail_run_type"] = "live"
                st.session_state["detail_grid_string_id"] = None
                st.rerun()


def _render_stats_tab():
    """통계 대시보드 탭."""
    st.markdown("### 통계 대시보드")

    sim_stats = query_simulation_stats()
    live_stats = query_live_stats()

    st.markdown("#### 시뮬레이션")
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("총 Run 수", sim_stats.get("total_runs", 0))
    with c2:
        st.metric("총 Grid Result 수", sim_stats.get("total_grid_results", 0))
    with c3:
        st.metric("평균 정확도", f"{sim_stats.get('avg_accuracy_overall', 0):.2f}%")
    with c4:
        st.metric("평균 최대 연속 불일치", f"{sim_stats.get('avg_max_consecutive_failures', 0):.2f}")
    with c5:
        st.metric("최악 최대 연속 불일치", sim_stats.get("worst_max_consecutive_failures", 0))

    dist = sim_stats.get("max_consecutive_failures_dist", {})
    if dist:
        dist_sorted = sorted(dist.items())
        labels = [f"{k}회" for k, _ in dist_sorted]
        values = [v for _, v in dist_sorted]
        if values:
            st.bar_chart(pd.DataFrame({"개수": values}, index=labels))
        st.caption("연속 불일치 분포 (grid_results 기준)")

    st.markdown("---")
    st.markdown("#### 라이브")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("총 Run 수", live_stats.get("total_runs", 0))
    with c2:
        st.metric("평균 정확도", f"{live_stats.get('avg_accuracy_overall', 0):.2f}%")
    with c3:
        st.metric("평균 최대 연속 불일치", f"{live_stats.get('avg_max_consecutive_failures', 0):.2f}")
    with c4:
        st.metric("최악 최대 연속 불일치", live_stats.get("worst_max_consecutive_failures", 0))

    dist_live = live_stats.get("max_consecutive_failures_dist", {})
    if dist_live:
        dist_sorted = sorted(dist_live.items())
        labels = [f"{k}회" for k, _ in dist_sorted]
        values = [v for _, v in dist_sorted]
        if values:
            st.bar_chart(pd.DataFrame({"개수": values}, index=labels))
        st.caption("연속 불일치 분포 (live_run_summary 기준)")


def _render_detail_tab():
    """상세 조회 탭. detail_run_id가 있으면 자동 표시."""
    st.markdown("### 상세 조회")

    # 탭 1에서 선택된 경우 자동 표시
    detail_run_id = st.session_state.get("detail_run_id")
    detail_run_type = st.session_state.get("detail_run_type")
    detail_grid_string_id = st.session_state.get("detail_grid_string_id")

    if detail_run_id:
        st.info(f"선택된 Run: {detail_run_id} ({detail_run_type})")
        run_id = detail_run_id
        run_type = "시뮬레이션" if detail_run_type == "sim" else "라이브"
    else:
        run_type = st.radio("Run 유형", ["시뮬레이션", "라이브"], horizontal=True, key="dt_run_type")
        if run_type == "시뮬레이션":
            sim_runs = query_simulation_runs_list(limit=50)
            if sim_runs:
                opts = [(r["run_id"], f"{r['run_id']} | {r.get('hypothesis_key','')} | {r.get('created_at','')}") for r in sim_runs]
                idx = st.selectbox("Run 선택", range(len(opts)), format_func=lambda i: opts[i][1], key="dt_sim_select")
                run_id = opts[idx][0]
            else:
                run_id = st.text_input("Run ID 입력", placeholder="UUID", key="dt_sim_input")
        else:
            live_runs = query_live_runs_list(limit=50)
            if live_runs:
                opts = [(r["run_id"], f"{r['run_id']} | {r.get('created_at','')} | {r.get('grid_string_preview','')[:40]}") for r in live_runs]
                idx = st.selectbox("Run 선택", range(len(opts)), format_func=lambda i: opts[i][1], key="dt_live_select")
                run_id = opts[idx][0]
            else:
                run_id = st.text_input("Run ID 입력", placeholder="UUID", key="dt_live_input")

    if st.button("조회", key="dt_query") and run_id:
        if run_type == "시뮬레이션":
            detail = query_simulation_run_detail(run_id)
            st.session_state["run_detail"] = detail
            st.session_state["run_detail_type"] = "sim"
        else:
            detail = query_live_run_detail(run_id)
            st.session_state["run_detail"] = detail
            st.session_state["run_detail_type"] = "live"
        if detail_run_id:
            st.session_state.pop("detail_run_id", None)
            st.session_state.pop("detail_run_type", None)
            st.session_state.pop("detail_grid_string_id", None)
        st.rerun()

    # detail_run_id가 있고 아직 조회하지 않은 경우 자동 조회
    if detail_run_id and "run_detail" not in st.session_state and run_id:
        if detail_run_type == "sim":
            detail = query_simulation_run_detail(run_id)
        else:
            detail = query_live_run_detail(run_id)
        if detail:
            st.session_state["run_detail"] = detail
            st.session_state["run_detail_type"] = detail_run_type or "sim"
            st.session_state.pop("detail_run_id", None)
            st.session_state.pop("detail_run_type", None)
            st.session_state.pop("detail_grid_string_id", None)
            st.rerun()

    if "run_detail" in st.session_state:
        detail = st.session_state["run_detail"]
        is_live = st.session_state.get("run_detail_type") == "live"

        if detail is None:
            st.warning("해당 run_id를 찾을 수 없습니다.")
        else:
            meta = detail.get("run_meta", {})
            sm = detail.get("run_summary", {})

            st.markdown("#### Run 메타")
            st.json({k: str(v) for k, v in meta.items()})

            st.markdown("#### 요약")
            if is_live:
                c1, c2, c3, c4, c5 = st.columns(5)
                with c1:
                    st.metric("총 스텝", sm.get("total_steps", 0))
                with c2:
                    st.metric("총 예측", sm.get("total_predictions", 0))
                with c3:
                    st.metric("총 실패", sm.get("total_failures", 0))
                with c4:
                    st.metric("스킵", sm.get("total_skipped", 0))
                with c5:
                    st.metric("정확도", f"{sm.get('accuracy', 0):.2f}%")
                if meta.get("grid_string"):
                    st.markdown("#### Grid String")
                    st.code(meta.get("grid_string", ""))
                events = detail.get("step_events", [])
            else:
                grid_results = detail.get("grid_results", [])
                st.markdown(f"#### Grid Results ({len(grid_results)}건)")
                if grid_results:
                    gr_df = pd.DataFrame([
                        {
                            "grid_string_id": r.get("grid_string_id"),
                            "accuracy": r.get("accuracy"),
                            "max_consecutive_failures": r.get("max_consecutive_failures"),
                            "total_predictions": r.get("total_predictions"),
                            "total_skipped": r.get("total_skipped"),
                        }
                        for r in grid_results
                    ])
                    st.dataframe(gr_df, use_container_width=True, hide_index=True)

                    gid_options = [r.get("grid_string_id") for r in grid_results]
                    default_idx = gid_options.index(detail_grid_string_id) if (detail_grid_string_id is not None and detail_grid_string_id in gid_options) else 0
                    selected_gid = st.selectbox(
                        "상세 히스토리 조회할 grid_string_id 선택",
                        gid_options,
                        format_func=lambda x: str(x),
                        index=default_idx,
                        key="dt_gid_select",
                    )
                    if selected_gid is not None:
                        events = query_simulation_step_events(meta.get("run_id"), selected_gid)
                    else:
                        events = []
                else:
                    events = []
                    st.info("grid_results가 없습니다.")

            if events:
                st.markdown("#### 상세 히스토리")
                rows = build_history_table_rows(events, is_live=is_live)
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                st.caption(f"총 {len(events)}개 스텝")

    # grid_string_id 직접 조회 모드
    st.markdown("---")
    st.markdown("#### grid_string_id로 조회 (시뮬레이션)")
    gid_input = st.number_input("Grid String ID", min_value=1, value=1, step=1, key="dt_gid_input")
    if st.button("조회 (grid_string_id)", key="dt_gid_query"):
        runs = query_simulation_by_grid_string_id(int(gid_input))
        st.session_state["gid_results"] = runs
        st.session_state["gid_value"] = int(gid_input)

    if "gid_results" in st.session_state and st.session_state.get("gid_value") == gid_input:
        runs = st.session_state["gid_results"]
        if not runs:
            st.info(f"grid_string_id {gid_input}에 대한 시뮬레이션 결과가 없습니다.")
        else:
            grid_string = get_grid_string_by_id(gid_input)
            if grid_string:
                st.markdown("##### Grid String")
                st.code(grid_string)
            run_options = [(r.get("run_id"), f"{r.get('run_id','')[:8]}... | {r.get('run_meta',{}).get('hypothesis_key','')}") for r in runs]
            idx = st.selectbox("Run 선택", range(len(run_options)), format_func=lambda i: run_options[i][1], key="dt_gid_run_select")
            selected_run_id = run_options[idx][0]
            selected = runs[idx]
            gr = selected.get("grid_result", {})
            st.metric("정확도", f"{gr.get('accuracy', 0):.2f}%")
            events = query_simulation_step_events(selected_run_id, gid_input)
            if events:
                rows = build_history_table_rows(events, is_live=False)
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_prediction_table_confidence_section():
    """예측 테이블(simulation_predictions_change_point) 신뢰도 섹션. 하단 탭으로 요약/prefix별 상세."""
    window_sizes = (9, 10, 11)
    st.caption("simulation_predictions_change_point · 윈도우 9, 10, 11 · 모든 메소드")

    sub_t1, sub_t2, sub_t3 = st.tabs(["요약", "prefix별 메소드 비교", "prefix별 상세(원본)"])

    with sub_t1:
        _render_prediction_confidence_summary(window_sizes)

    with sub_t2:
        _render_prediction_prefix_method_comparison(window_sizes)

    with sub_t3:
        _render_prediction_prefix_detail(window_sizes)


def _render_prediction_confidence_summary(window_sizes):
    """메소드·윈도우별 신뢰도 요약 테이블."""
    df = query_prediction_table_confidence_stats(window_sizes=window_sizes)

    if df is None or len(df) == 0:
        st.info("예측 테이블에 데이터가 없거나 조회 결과가 없습니다. (윈도우 9, 10, 11 · 모든 method)")
        return

    display_df = pd.DataFrame({
        "메소드": df["method"],
        "윈도우 크기": df["window_size"],
        "레코드 수": df["record_count"].astype(int),
        "평균 신뢰도 (%)": (df["avg_confidence"].round(2)),
        "최소 신뢰도 (%)": (df["min_confidence"].round(2)),
        "최대 신뢰도 (%)": (df["max_confidence"].round(2)),
    })
    st.dataframe(display_df, use_container_width=True, hide_index=True)
    st.caption(f"총 {len(display_df)}개 (메소드×윈도우) 조합 · 윈도우: {window_sizes}")


def _build_prefix_method_comparison_df(df_raw, window_sizes):
    """
    같은 prefix에 대해 메소드별 예측값·신뢰도를 나란히 보여주는 피벗 테이블 생성.
    threshold=0 기준 (라이브 앱과 동일).
    """
    if df_raw is None or len(df_raw) == 0:
        return pd.DataFrame()
    df = df_raw[df_raw["threshold"] == 0].copy()
    if len(df) == 0:
        return pd.DataFrame()
    df["confidence"] = df["confidence"].round(2)
    methods = df["method"].unique().tolist()
    method_order = ["빈도 기반", "가중치 기반", "안전 우선"]
    methods = [m for m in method_order if m in methods] or methods
    rows = []
    for (ws, prefix), grp in df.groupby(["window_size", "prefix"]):
        row = {"윈도우": ws, "prefix": prefix}
        for m in methods:
            sub = grp[grp["method"] == m]
            if len(sub) > 0:
                r = sub.iloc[0]
                row[f"{m}_예측"] = r["predicted_value"] if pd.notna(r["predicted_value"]) and r["predicted_value"] else "-"
                row[f"{m}_신뢰도(%)"] = r["confidence"] if pd.notna(r["confidence"]) else "-"
            else:
                row[f"{m}_예측"] = "-"
                row[f"{m}_신뢰도(%)"] = "-"
        rows.append(row)
    return pd.DataFrame(rows)


def _render_prediction_prefix_method_comparison(window_sizes):
    """같은 prefix에 대해 메소드별 예측값·신뢰도 차이 비교 테이블. 윈도우 크기별 필터 지원."""
    df = query_prediction_table_prefix_detail(window_sizes=window_sizes)
    display_df = _build_prefix_method_comparison_df(df, window_sizes)

    if display_df is None or len(display_df) == 0:
        st.info("예측 테이블에 데이터가 없습니다. (threshold=0 기준)")
        return

    # 윈도우 크기별 필터
    ws_options = ["전체"] + [int(x) for x in sorted(display_df["윈도우"].unique())]
    selected_ws = st.selectbox(
        "윈도우 크기",
        range(len(ws_options)),
        format_func=lambda i: "전체" if ws_options[i] == "전체" else f"{ws_options[i]}",
        key="prefix_method_ws_filter",
    )
    ws_value = ws_options[selected_ws]

    if ws_value != "전체":
        display_df = display_df[display_df["윈도우"] == ws_value].copy()
        # 윈도우 컬럼은 동일값이므로 숨겨도 됨 (선택 시 제거하면 테이블 간결)
        display_df = display_df.drop(columns=["윈도우"])

    st.dataframe(display_df, use_container_width=True, hide_index=True)
    caption_ws = f"윈도우 {ws_value}" if ws_value != "전체" else f"윈도우: {window_sizes}"
    st.caption(
        f"총 {len(display_df):,}개 prefix · {caption_ws} · "
        "같은 행에서 메소드별 예측값·신뢰도 차이 비교"
    )


def _render_prediction_prefix_detail(window_sizes):
    """모든 prefix별 신뢰도 상세 테이블 (원본 행 구조)."""
    df = query_prediction_table_prefix_detail(window_sizes=window_sizes)

    if df is None or len(df) == 0:
        st.info("예측 테이블에 데이터가 없습니다.")
        return

    display_df = pd.DataFrame({
        "윈도우": df["window_size"],
        "prefix": df["prefix"],
        "메소드": df["method"],
        "임계값": df["threshold"],
        "예측값": df["predicted_value"],
        "신뢰도 (%)": (df["confidence"].round(2)),
        "B 비율 (%)": (df["b_ratio"].round(2)),
        "P 비율 (%)": (df["p_ratio"].round(2)),
    })
    st.dataframe(display_df, use_container_width=True, hide_index=True)
    st.caption(f"총 {len(display_df):,}개 레코드 · 윈도우: {window_sizes}")


if __name__ == "__main__":
    main()
