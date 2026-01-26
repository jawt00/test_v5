"""
Change-point 전용 다중 윈도우 시뮬레이션 앱

- stored_predictions_change_point 기반
- 최고 신뢰도 선택 + 신뢰도 스킵 전략
- 목표: 연속 실패 5회 이하
"""

import sys
from pathlib import Path

# 상위 폴더의 모듈을 import하기 위해 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
from datetime import datetime
import warnings
import logging

# Streamlit ScriptRunContext 경고 억제 (병렬 처리 시 발생하는 무해한 경고)
warnings.filterwarnings("ignore", message=".*missing ScriptRunContext.*")
logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)

from change_point_prediction_module import (
    load_preprocessed_grid_strings_cp,
    get_stored_predictions_change_point_count,
    create_stored_predictions_change_point_table,
    save_or_update_predictions_for_change_point_data,
    batch_validate_multi_window_scenario_cp,
    batch_validate_multi_window_with_confidence_skip_cp,
)

# 같은 폴더의 walk_forward_simulation_cp 모듈 import
try:
    from walk_forward_simulation_cp import walk_forward_simulation_cp
except ImportError:
    # 상대 import 시도
    from .walk_forward_simulation_cp import walk_forward_simulation_cp

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
    **전략**: 각 위치에서 여러 윈도우(5~12) 중 최고 신뢰도 예측 선택.  
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
                            window_sizes=(5, 6, 7, 8, 9, 10, 11, 12),
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
        col_w1, col_w2, col_w3, col_w4, col_w5 = st.columns(5)
        with col_w1:
            w5 = st.checkbox("5", True, key="w5")
            w6 = st.checkbox("6", True, key="w6")
        with col_w2:
            w7 = st.checkbox("7", True, key="w7")
            w8 = st.checkbox("8", True, key="w8")
        with col_w3:
            w9 = st.checkbox("9", True, key="w9")
            w10 = st.checkbox("10", True, key="w10")
        with col_w4:
            w11 = st.checkbox("11", True, key="w11")
            w12 = st.checkbox("12", True, key="w12")
        ws = []
        if w5: ws.append(5)
        if w6: ws.append(6)
        if w7: ws.append(7)
        if w8: ws.append(8)
        if w9: ws.append(9)
        if w10: ws.append(10)
        if w11: ws.append(11)
        if w12: ws.append(12)

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
        ws = st.session_state.get("sim_run_ws", [5, 6, 7, 8, 9, 10, 11, 12])
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

    st.markdown("---")
    st.markdown("## Walk-forward Analysis 시뮬레이션")
    st.markdown("""
    **전략**: 시간 순서대로 데이터를 분할하여 학습/검증/업데이트를 반복 수행.  
    **목표**: Max Consecutive Losses < 5를 만족하는 가장 낮은 임계값(T) 탐색.
    """)

    with st.form("wf_form", clear_on_submit=False):
        st.markdown("### Walk-forward Analysis 설정")
        
        col_wf1, col_wf2 = st.columns(2)
        with col_wf1:
            method_wf = st.selectbox("예측 방법", ["빈도 기반", "가중치 기반", "안전 우선"], key="wf_method")
        with col_wf2:
            min_sample_count = st.number_input(
                "최소 표본 수 (S_min)",
                min_value=1,
                max_value=100,
                value=15,
                step=1,
                key="wf_min_sample",
                help="패턴이 이 횟수 이상 출현한 경우만 예측에 사용"
            )

        st.markdown("#### 윈도우 크기 (8-12)")
        col_wf_w1, col_wf_w2, col_wf_w3, col_wf_w4, col_wf_w5 = st.columns(5)
        wf_windows = []
        with col_wf_w1:
            wf_w8 = st.checkbox("8", False, key="wf_w8")
            if wf_w8: wf_windows.append(8)
        with col_wf_w2:
            wf_w9 = st.checkbox("9", False, key="wf_w9")
            if wf_w9: wf_windows.append(9)
        with col_wf_w3:
            wf_w10 = st.checkbox("10", False, key="wf_w10")
            if wf_w10: wf_windows.append(10)
        with col_wf_w4:
            wf_w11 = st.checkbox("11", False, key="wf_w11")
            if wf_w11: wf_windows.append(11)
        with col_wf_w5:
            wf_w12 = st.checkbox("12", False, key="wf_w12")
            if wf_w12: wf_windows.append(12)

        st.markdown("#### 임계값 범위")
        col_wf_t1, col_wf_t2, col_wf_t3 = st.columns(3)
        with col_wf_t1:
            threshold_min = st.number_input("최소 임계값 (%)", 0, 100, 50, 1, key="wf_thresh_min")
        with col_wf_t2:
            threshold_max = st.number_input("최대 임계값 (%)", 0, 100, 65, 1, key="wf_thresh_max")
        with col_wf_t3:
            threshold_step = st.number_input("임계값 단계", 0.1, 5.0, 1.0, 0.1, key="wf_thresh_step")

        # 병렬 처리 제거 - 순차 실행으로 변경
        st.info("ℹ️ **실행 모드**: 순차 실행 (병렬 처리 비활성화) - 안정성과 디버깅 용이성을 위해 순차 실행으로 변경되었습니다.")

        if st.form_submit_button("Walk-forward Analysis 실행", type="primary"):
            if not wf_windows:
                st.warning("최소 하나의 윈도우를 선택하세요.")
            elif threshold_min >= threshold_max:
                st.warning("최소 임계값은 최대 임계값보다 작아야 합니다.")
            else:
                # 위젯 key와 다른 이름으로 session_state 저장 (오류 방지)
                st.session_state["wf_run"] = True
                st.session_state["wf_run_windows"] = wf_windows
                st.session_state["wf_run_method"] = method_wf
                st.session_state["wf_run_min_sample"] = min_sample_count
                st.session_state["wf_run_thresh_range"] = (threshold_min, threshold_max, threshold_step)
                st.session_state["wf_results"] = None
                st.rerun()

    if "wf_results" in st.session_state and st.session_state["wf_results"] is not None:
        wf_res = st.session_state["wf_results"]
        st.markdown("---")
        st.markdown("### Walk-forward Analysis 결과")
        
        all_results = wf_res.get("results", [])
        optimal_combinations = wf_res.get("optimal_combinations", [])
        
        if not all_results:
            st.info("결과가 없습니다.")
        else:
            # 최적 조합 표시
            if optimal_combinations:
                st.success(f"✅ MCL < 5 만족하는 조합: {len(optimal_combinations)}개 발견")
                
                # 가장 낮은 T 값 추천
                best_combination = optimal_combinations[0] if optimal_combinations else None
                if best_combination:
                    st.info(
                        f"🎯 추천 조합: 윈도우 크기={best_combination['window_size']}, "
                        f"임계값={best_combination['threshold']}% "
                        f"(MCL={best_combination['mcl']}, Failure Score={best_combination['failure_score']})"
                    )
            else:
                st.warning("⚠️ MCL < 5 만족하는 조합을 찾지 못했습니다.")
            
            # 필터링 옵션
            show_only_passed = st.checkbox("Failure Score = 0인 조합만 표시", key="wf_filter_passed")
            
            # 결과 테이블 생성
            display_results = all_results
            if show_only_passed:
                display_results = [r for r in all_results if r.get("is_passed", False)]
            
            if display_results:
                # 데이터프레임 생성
                df_results = pd.DataFrame(display_results)
                
                # 컬럼명 한글화 및 정렬
                df_display = df_results[[
                    "window_size", "threshold", "mcl", "total_bets", 
                    "win_rate", "failure_score", "is_passed"
                ]].copy()
                df_display.columns = ["윈도우 크기", "임계값 (%)", "MCL", "Total Bets", "Win Rate (%)", "Failure Score", "합격"]
                df_display["합격"] = df_display["합격"].map({True: "✅", False: "❌"})
                df_display["Win Rate (%)"] = df_display["Win Rate (%)"].round(2)
                
                # MCL < 5 만족하는 행 하이라이트
                def highlight_passed(row):
                    if row["MCL"] < 5:
                        return ['background-color: #90EE90'] * len(row)
                    return [''] * len(row)
                
                st.dataframe(
                    df_display.style.apply(highlight_passed, axis=1),
                    use_container_width=True,
                    hide_index=True
                )
                
                # 통계 요약
                st.markdown("#### 통계 요약")
                col_stat1, col_stat2, col_stat3, col_stat4 = st.columns(4)
                with col_stat1:
                    passed_count = sum(1 for r in display_results if r.get("is_passed", False))
                    st.metric("합격 조합", f"{passed_count}개")
                with col_stat2:
                    avg_mcl = sum(r["mcl"] for r in display_results) / len(display_results) if display_results else 0
                    st.metric("평균 MCL", f"{avg_mcl:.2f}")
                with col_stat3:
                    avg_win_rate = sum(r["win_rate"] for r in display_results) / len(display_results) if display_results else 0
                    st.metric("평균 Win Rate", f"{avg_win_rate:.2f}%")
                with col_stat4:
                    total_bets_sum = sum(r["total_bets"] for r in display_results)
                    st.metric("총 베팅 횟수", f"{total_bets_sum:,}")
                
                # 최적 조합 상세 표시
                if optimal_combinations:
                    with st.expander("최적 조합 상세 (MCL < 5, 임계값 낮은 순)"):
                        opt_df = pd.DataFrame(optimal_combinations[:10])  # 상위 10개만
                        opt_display = opt_df[[
                            "window_size", "threshold", "mcl", "total_bets", 
                            "win_rate", "failure_score"
                        ]].copy()
                        opt_display.columns = ["윈도우 크기", "임계값 (%)", "MCL", "Total Bets", "Win Rate (%)", "Failure Score"]
                        opt_display["Win Rate (%)"] = opt_display["Win Rate (%)"].round(2)
                        st.dataframe(opt_display, use_container_width=True, hide_index=True)

    elif "wf_run" in st.session_state and st.session_state.get("wf_run"):
        wf_windows = st.session_state.get("wf_run_windows", [8, 9, 10, 11, 12])
        wf_method = st.session_state.get("wf_run_method", "빈도 기반")
        wf_min_sample = st.session_state.get("wf_run_min_sample", 15)
        wf_thresh_range = st.session_state.get("wf_run_thresh_range", (50, 65, 1))

        # 진행 상황 표시를 위한 컨테이너
        progress_container = st.container()
        with progress_container:
            st.markdown("### 진행 상황")
            bar = st.progress(0)
            status = st.empty()
            
            # 추가 정보 표시 영역
            info_col1, info_col2, info_col3, info_col4 = st.columns(4)
            metric_elapsed = info_col1.empty()
            metric_remaining = info_col2.empty()
            metric_completed = info_col3.empty()
            metric_workers = info_col4.empty()
            
            # 즉시 시작 메시지 표시
            status.success("🚀 시뮬레이션 시작 중... 데이터 로드 및 초기화 진행 중입니다.")
            bar.progress(0.01)  # 1%로 시작 표시
            
            # 마지막 업데이트 시간 추적
            last_update_time = st.empty()
            current_task_info = st.empty()
            
            def update_progress(pct, msg):
                import time
                from datetime import datetime
                
                # 진행률 업데이트 (퍼센트 포함)
                progress_value = min(1.0, max(0.0, pct))
                bar.progress(progress_value)
                
                # 마지막 업데이트 시간 표시
                current_time = datetime.now().strftime("%H:%M:%S")
                last_update_time.caption(f"🕐 마지막 업데이트: {current_time}")
                
                # 메시지 타입에 따라 다른 스타일 적용
                if "시작" in msg or "초기화" in msg:
                    status.info(f"🔄 {msg}")
                elif "완료" in msg or "✅" in msg:
                    status.success(f"✅ {msg}")
                elif "경고" in msg or "오류" in msg:
                    status.warning(f"⚠️ {msg}")
                else:
                    status.info(f"⏳ {msg}")
                
                # 메시지 파싱하여 정보 추출
                msg_parts = msg.split(" | ")
                
                # 진행률 정보 추출
                progress_info = None
                elapsed_info = None
                remaining_info = None
                current_work = None
                workers_info = None
                
                for part in msg_parts:
                    if "진행률:" in part:
                        progress_info = part.replace("진행률:", "").strip()
                    elif "경과 시간:" in part:
                        elapsed_info = part.replace("경과 시간:", "").strip()
                    elif "예상 남은 시간:" in part:
                        remaining_info = part.replace("예상 남은 시간:", "").strip()
                    elif "처리 중:" in part or "현재 최고:" in part:
                        current_work = part.replace("처리 중:", "").replace("현재 최고:", "").strip()
                    elif "병렬 작업자:" in part:
                        workers_info = part.replace("병렬 작업자:", "").strip()
                
                # 메트릭 업데이트
                if elapsed_info:
                    metric_elapsed.metric("⏱️ 경과 시간", elapsed_info)
                
                if remaining_info:
                    metric_remaining.metric("⏳ 남은 시간", remaining_info)
                
                if progress_info:
                    metric_completed.metric("📊 진행률", progress_info)
                    # 진행률 바에 퍼센트 표시를 위한 추가 정보
                    try:
                        # 진행률에서 숫자 추출
                        import re
                        pct_match = re.search(r'(\d+\.?\d*)%', progress_info)
                        if pct_match:
                            pct_value = float(pct_match.group(1))
                            bar.progress(pct_value / 100.0)
                    except:
                        pass
                
                if workers_info:
                    metric_workers.metric("⚙️ 작업자", workers_info)
                
                # 현재 작업 정보 표시
                if current_work:
                    current_task_info.info(f"🔧 **현재 작업**: {current_work}")
                elif progress_info:
                    # 진행률만 있는 경우
                    current_task_info.info(f"⏳ **상태**: 시뮬레이션 진행 중... ({progress_info})")
            
            try:
                # 시뮬레이션 실행 시작 확인 메시지
                status.success("✅ 시뮬레이션 실행 중... 작업이 시작되었습니다.")
                
                wf_res = walk_forward_simulation_cp(
                    window_sizes=tuple(wf_windows),
                    threshold_range=wf_thresh_range,
                    method=wf_method,
                    initial_train_ratio=0.4,
                    validation_ratio=0.1,
                    min_sample_count=wf_min_sample,
                    progress_callback=update_progress,
                    max_workers=10,  # ThreadPoolExecutor 작업자 수
                )
                
                # 성공적으로 완료된 경우
                if wf_res and wf_res.get("results"):
                    st.session_state["wf_results"] = wf_res
                    st.session_state["wf_run"] = False
                    status.success("✅ 시뮬레이션 완료!")
                else:
                    st.warning("⚠️ 시뮬레이션이 완료되었지만 결과가 없습니다.")
                    st.session_state["wf_run"] = False
                    
            except KeyboardInterrupt:
                st.warning("⚠️ 사용자에 의해 시뮬레이션이 중단되었습니다.")
                st.session_state["wf_run"] = False
            except Exception as e:
                st.error(f"❌ Walk-forward Analysis 실패: {e}")
                import traceback
                with st.expander("상세 오류 정보"):
                    st.code(traceback.format_exc(), language="python")
                st.session_state["wf_run"] = False
            finally:
                # 완료 후 메트릭 정리
                try:
                    metric_elapsed.empty()
                    metric_remaining.empty()
                    metric_completed.empty()
                    metric_workers.empty()
                    last_update_time.empty()
                    current_task_info.empty()
                except:
                    pass
                bar.empty()
                status.empty()
        st.rerun()


if __name__ == "__main__":
    main()
