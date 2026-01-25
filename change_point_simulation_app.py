"""
Change-point 전용 다중 윈도우 시뮬레이션 앱

- stored_predictions_change_point 기반
- 최고 신뢰도 선택 + 신뢰도 스킵 전략
- 목표: 연속 실패 5회 이하
"""

import streamlit as st
import pandas as pd
from datetime import datetime

from change_point_prediction_module import (
    load_preprocessed_grid_strings_cp,
    get_stored_predictions_change_point_count,
    create_stored_predictions_change_point_table,
    save_or_update_predictions_for_change_point_data,
    batch_validate_multi_window_scenario_cp,
    batch_validate_multi_window_with_confidence_skip_cp,
)

st.set_page_config(
    page_title="Change-point 시뮬레이션",
    page_icon="🎯",
    layout="wide",
)


def _fmt_dt(s):
    if s is None:
        return ""
    try:
        if isinstance(s, str) and "T" in s:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            d = pd.to_datetime(s)
        return d.strftime("%m-%d %H:%M")
    except Exception:
        return str(s)


def _run_binary_search_confidence_skip(
    cutoff_id,
    window_sizes,
    method,
    threshold,
    min_skip,
    max_skip,
    tolerance,
    progress_bar=None,
    status_text=None,
):
    """그리드 탐색으로 max_consecutive_failures <= 5 만족하는 신뢰도 스킵 임계값 탐색."""
    step = max(tolerance, 0.1)
    thresh_list = []
    v = float(min_skip)
    while v <= float(max_skip):
        thresh_list.append(round(v, 2))
        v += step
    if not thresh_list:
        thresh_list = [min_skip]

    best_thresh = None
    best_result = None
    history = []

    def update_progress(pct, msg):
        if progress_bar:
            progress_bar.progress(min(1.0, max(0.0, pct)))
        if status_text:
            status_text.text(msg)

    for i, t in enumerate(thresh_list):
        update_progress(0.1 + 0.8 * (i / max(1, len(thresh_list))), f"탐색 {i+1}/{len(thresh_list)} (임계값={t:.2f}%)")
        res = batch_validate_multi_window_with_confidence_skip_cp(
            cutoff_id,
            window_sizes=window_sizes,
            method=method,
            threshold=threshold,
            confidence_skip_threshold=t,
        )
        if not res or not res.get("results"):
            history.append({"threshold": t, "max_failures": None, "ok": False})
            continue
        summary = res["summary"]
        mf = summary.get("max_consecutive_failures", 999)
        ok = mf <= 5
        history.append({"threshold": t, "max_failures": mf, "ok": ok})
        if ok and (best_thresh is None or t > best_thresh):
            best_thresh = t
            best_result = res

    update_progress(1.0, "완료")
    return best_thresh, best_result, history


def main():
    st.title("Change-point 다중 윈도우 시뮬레이션")
    st.markdown("""
    **전략**: 각 위치에서 여러 윈도우(5~9) 중 최고 신뢰도 예측 선택.  
    **목표**: 연속 예측 실패 5회 이하.
    """)

    # --- 예측값 테이블 관리 ---
    st.markdown("## 예측값 테이블 관리")
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        if st.button("예측값 테이블 생성 (stored_predictions_change_point)", use_container_width=True):
            with st.spinner("테이블 생성 중..."):
                try:
                    create_stored_predictions_change_point_table()
                    st.success("테이블 생성 완료.")
                except Exception as e:
                    st.error(f"테이블 생성 실패: {e}")
    with col_t2:
        n = get_stored_predictions_change_point_count()
        st.metric("저장된 예측값 개수", f"{n:,}개")

    with st.form("pred_form", clear_on_submit=False):
        st.markdown("### 예측값 생성")
        df_cp = load_preprocessed_grid_strings_cp()
        cutoff_options = [None] + df_cp["id"].tolist() if len(df_cp) > 0 else [None]
        cutoff_labels = ["전체 데이터"] + [f"ID {r['id']} ({_fmt_dt(r['created_at'])})" for _, r in df_cp.iterrows()]

        col_p1, col_p2, col_p3 = st.columns(3)
        with col_p1:
            sel_cutoff = st.selectbox(
                "기준 Grid String ID (이 ID 이하 학습)",
                range(len(cutoff_options)),
                format_func=lambda i: cutoff_labels[i] if i < len(cutoff_labels) else str(cutoff_options[i]),
                key="pred_cutoff",
            )
            cutoff_pred = cutoff_options[sel_cutoff] if cutoff_options else None
        with col_p2:
            methods = st.multiselect(
                "예측 방법",
                ["빈도 기반", "가중치 기반", "안전 우선"],
                default=["빈도 기반"],
                key="pred_methods",
            )
        with col_p3:
            thresh_pred = st.number_input("임계값", 0, 100, 0, key="pred_thresh")

        if st.form_submit_button("예측값 생성 시작", type="primary"):
            if not methods:
                st.warning("최소 하나의 예측 방법을 선택하세요.")
            else:
                with st.spinner("예측값 생성 중..."):
                    bar = st.progress(0)
                    status = st.empty()
                    status.text("생성 중...")
                    try:
                        out = save_or_update_predictions_for_change_point_data(
                            cutoff_grid_string_id=cutoff_pred,
                            window_sizes=(5, 6, 7, 8, 9),
                            methods=tuple(methods),
                            thresholds=(thresh_pred,),
                        )
                        bar.progress(1.0)
                        status.text("완료")
                        st.success(f"저장/업데이트 {out.get('total_saved', 0):,}개, 고유 prefix {out.get('unique_prefixes', 0):,}개")
                    except Exception as e:
                        st.error(f"생성 실패: {e}")
                    finally:
                        bar.empty()
                        status.empty()

    st.markdown("---")
    st.markdown("## 다중 윈도우 시뮬레이션")
    n_stored = get_stored_predictions_change_point_count()
    if n_stored == 0:
        st.warning("stored_predictions_change_point가 비어 있습니다. 위에서 예측값을 먼저 생성하세요.")

    df_mw = load_preprocessed_grid_strings_cp()
    if len(df_mw) == 0:
        st.warning("preprocessed_grid_strings에 데이터가 없습니다.")

    with st.form("sim_form", clear_on_submit=False):
        st.markdown("### 시뮬레이션 설정")
        cutoff_opts = [None] + df_mw["id"].tolist()
        cutoff_lbl = ["전체 (ID 이후 없음)"] + [f"ID {r['id']} 이후 ({_fmt_dt(r['created_at'])})" for _, r in df_mw.iterrows()]

        c1, c2, c3 = st.columns(3)
        with c1:
            idx_cutoff = st.selectbox(
                "기준 Grid String ID (이 ID 이후 검증)",
                range(len(cutoff_opts)),
                format_func=lambda i: cutoff_lbl[i],
                key="sim_cutoff_select",
            )
            cutoff_sim = cutoff_opts[idx_cutoff]
        with c2:
            method_sim = st.selectbox("예측 방법", ["빈도 기반", "가중치 기반", "안전 우선"], key="sim_method")
        with c3:
            thresh_sim = st.number_input("임계값", 0, 100, 0, key="sim_thresh")

        st.markdown("#### 신뢰도 스킵")
        skip_mode = st.radio("설정", ["미사용", "수동 설정", "자동 최적화 (이진 탐색)"], key="skip_mode")
        conf_skip = None
        min_skip = max_skip = tol_skip = None
        if skip_mode == "수동 설정":
            conf_skip = st.number_input("신뢰도 스킵 임계값 (%)", 0.0, 100.0, 52.0, 0.5, key="conf_skip_man")
        elif skip_mode == "자동 최적화 (이진 탐색)":
            min_skip = st.number_input("최소 임계값", 0.0, 100.0, 50.5, 0.5, key="min_skip")
            max_skip = st.number_input("최대 임계값", 0.0, 100.0, 59.0, 0.5, key="max_skip")
            tol_skip = st.number_input("정밀도", 0.1, 2.0, 0.5, 0.1, key="tol_skip")

        st.markdown("#### 윈도우 크기")
        w5 = st.checkbox("5", True, key="w5")
        w6 = st.checkbox("6", True, key="w6")
        w7 = st.checkbox("7", True, key="w7")
        w8 = st.checkbox("8", True, key="w8")
        w9 = st.checkbox("9", True, key="w9")
        ws = []
        if w5: ws.append(5)
        if w6: ws.append(6)
        if w7: ws.append(7)
        if w8: ws.append(8)
        if w9: ws.append(9)

        if st.form_submit_button("시뮬레이션 실행", type="primary"):
            if not ws:
                st.warning("최소 하나의 윈도우를 선택하세요.")
            elif n_stored == 0:
                st.warning("예측값을 먼저 생성하세요.")
            else:
                st.session_state["sim_run_cutoff"] = cutoff_sim if cutoff_sim is not None else 0
                st.session_state["sim_run_ws"] = ws
                st.session_state["sim_run_method"] = method_sim
                st.session_state["sim_run_thresh"] = thresh_sim
                st.session_state["sim_run_skip_mode"] = skip_mode
                st.session_state["sim_run_conf_skip"] = conf_skip
                st.session_state["sim_run_min_skip"] = min_skip
                st.session_state["sim_run_max_skip"] = max_skip
                st.session_state["sim_run_tol_skip"] = tol_skip
                st.session_state["sim_results"] = None
                st.rerun()

    if "sim_results" in st.session_state and st.session_state["sim_results"] is not None:
        res = st.session_state["sim_results"]
        st.markdown("---")
        st.markdown("### 시뮬레이션 결과")
        rr = res.get("results", [])
        sm = res.get("summary", {})
        if not rr:
            st.info("검증 결과가 없습니다.")
        else:
            st.metric("최대 연속 불일치", f"{sm.get('max_consecutive_failures', 0)}회")
            st.metric("평균 정확도", f"{sm.get('avg_accuracy', 0):.2f}%")
            st.metric("총 예측 횟수", f"{sm.get('total_predictions', 0):,}")
            st.metric("스킵 횟수", f"{sm.get('total_skipped', 0):,}")

            rows = []
            for r in rr:
                rows.append({
                    "grid_string_id": r["grid_string_id"],
                    "최대 연속 불일치": r["max_consecutive_failures"],
                    "정확도": f"{r['accuracy']:.2f}%",
                    "예측 횟수": r["total_predictions"],
                    "스킵": r.get("total_skipped", 0),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            with st.expander("히스토리 (첫 grid_string)"):
                if rr:
                    h = rr[0].get("history", [])[:50]
                    for x in h:
                        st.text(
                            f"step={x.get('step')} pos={x.get('position')} "
                            f"pred={x.get('predicted')} actual={x.get('actual')} "
                            f"ok={x.get('is_correct')} conf={x.get('confidence', 0):.1f} "
                            f"skipped={x.get('skipped', False)}"
                        )

    elif "sim_run_cutoff" in st.session_state:
        cutoff_sim = st.session_state["sim_run_cutoff"]  # 0 = 전체 (id > 0)
        ws = st.session_state.get("sim_run_ws", [5, 6, 7, 8, 9])
        method_sim = st.session_state.get("sim_run_method", "빈도 기반")
        thresh_sim = st.session_state.get("sim_run_thresh", 0)
        skip_mode = st.session_state.get("sim_run_skip_mode", "미사용")
        conf_skip = st.session_state.get("sim_run_conf_skip")
        min_skip = st.session_state.get("sim_run_min_skip", 50.5)
        max_skip = st.session_state.get("sim_run_max_skip", 59.0)
        tol_skip = st.session_state.get("sim_run_tol_skip", 0.5)

        with st.spinner("시뮬레이션 실행 중..."):
            bar = st.progress(0)
            status = st.empty()
            status.text("배치 검증 중...")
            res = None
            try:
                if skip_mode == "자동 최적화 (이진 탐색)":
                    best_t, best_r, hist = _run_binary_search_confidence_skip(
                        cutoff_sim, ws, method_sim, thresh_sim,
                        min_skip, max_skip, tol_skip, bar, status,
                    )
                    res = best_r
                    if best_t is not None:
                        st.session_state["sim_optimal_thresh"] = best_t
                        st.info(f"자동 최적화: 신뢰도 스킵 임계값 {best_t:.2f}%")
                elif skip_mode == "수동 설정" and conf_skip is not None:
                    res = batch_validate_multi_window_with_confidence_skip_cp(
                        cutoff_sim,
                        window_sizes=tuple(ws),
                        method=method_sim,
                        threshold=thresh_sim,
                        confidence_skip_threshold=conf_skip,
                    )
                else:
                    res = batch_validate_multi_window_scenario_cp(
                        cutoff_sim,
                        window_sizes=tuple(ws),
                        method=method_sim,
                        threshold=thresh_sim,
                    )
                st.session_state["sim_results"] = res
            except Exception as e:
                st.error(f"시뮬레이션 실패: {e}")
            finally:
                bar.empty()
                status.empty()
        st.rerun()


if __name__ == "__main__":
    main()
