"""
Change-point 가설 테스트 앱

- 다양한 가설을 선택하고 테스트
- 단일 테스트 모드: 하나의 가설만 테스트
- 비교 테스트 모드: 여러 가설 동시 실행 및 비교
"""

import sys
from pathlib import Path

# 상위 폴더의 모듈을 import하기 위해 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
from datetime import datetime

from change_point_prediction_module import (
    load_preprocessed_grid_strings_cp,
    get_stored_predictions_change_point_count,
)
from svg_parser_module import get_change_point_db_connection
from change_point_hypothesis_module import (
    list_hypotheses,
    get_hypothesis,
    batch_validate_hypothesis_cp,
    batch_validate_threshold_skip_anchor_priority_cp,
    batch_validate_first_anchor_extended_window_v2_cp,
    batch_validate_first_anchor_extended_window_v3_cp,
    HYPOTHESIS_REGISTRY,
)

st.set_page_config(
    page_title="Change-point 가설 테스트",
    page_icon="🧪",
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


def render_hypothesis_config_ui(hypothesis_name, hypothesis_instance, key_prefix):
    """가설별 설정 UI 렌더링"""
    schema = hypothesis_instance.get_config_schema()
    if not schema:
        return {}
    
    config = {}
    for param_name, param_schema in schema.items():
        param_type = param_schema.get("type", "text")
        label = param_schema.get("label", param_name)
        default = param_schema.get("default", None)
        
        key = f"{key_prefix}_{param_name}"
        
        if param_type == "number":
            min_val = param_schema.get("min", 0.0)
            max_val = param_schema.get("max", 100.0)
            step = param_schema.get("step", 1.0)
            config[param_name] = st.number_input(
                label, min_val, max_val, default, step, key=key
            )
        elif param_type == "text":
            config[param_name] = st.text_input(label, default, key=key)
        elif param_type == "select":
            options = param_schema.get("options", [])
            default_idx = 0
            if default in options:
                default_idx = options.index(default)
            config[param_name] = st.selectbox(label, options, default_idx, key=key)
    
    return config


def main():
    st.title("Change-point 가설 테스트")
    st.markdown("""
    다양한 시뮬레이션 가설을 선택하고 테스트할 수 있습니다.
    - **단일 테스트**: 하나의 가설을 상세히 분석
    - **비교 테스트**: 여러 가설을 동시에 실행하여 성능 비교
    """)
    
    # 저장된 예측값 확인
    n_stored = get_stored_predictions_change_point_count()
    if n_stored == 0:
        st.warning("⚠️ stored_predictions_change_point가 비어 있습니다. 예측값을 먼저 생성하세요.")
    
    # 등록된 가설 목록
    available_hypotheses = list_hypotheses()
    if not available_hypotheses:
        st.error("등록된 가설이 없습니다.")
        return
    
    # 테스트 모드 선택
    test_mode = st.radio("테스트 모드", ["단일 테스트", "비교 테스트"], horizontal=True)
    
    # 데이터 새로고침 버튼
    st.markdown("---")
    col_refresh1, col_refresh2 = st.columns([1, 4])
    with col_refresh1:
        refresh_clicked = st.button("🔄 데이터 새로고침", key="simulation_refresh_data", use_container_width=True)
    with col_refresh2:
        if refresh_clicked:
            st.success("✅ 데이터가 새로고침되었습니다.")
            st.rerun()
    
    df_mw = load_preprocessed_grid_strings_cp()
    if len(df_mw) == 0:
        st.warning("preprocessed_grid_strings에 데이터가 없습니다.")
        return
    
    cutoff_opts = [None] + df_mw["id"].tolist()
    cutoff_lbl = ["전체 (ID 이후 없음)"] + [f"ID {r['id']} 이후 ({_fmt_dt(r['created_at'])})" for _, r in df_mw.iterrows()]
    
    # 가설 선택 및 설정
    st.markdown("---")
    st.markdown("## 가설 설정")
    
    if test_mode == "단일 테스트":
        selected_hypothesis_name = st.selectbox(
            "가설 선택",
            available_hypotheses,
            format_func=lambda x: get_hypothesis(x).get_name(),
            key="single_hypothesis",
        )
        
        hypothesis_instance = get_hypothesis(selected_hypothesis_name)
        st.info(f"**설명**: {hypothesis_instance.get_description()}")
        
        # 시뮬레이션 설정 (가설 선택 하위)
        st.markdown("---")
        st.markdown("### 시뮬레이션 설정")
        
        col1, col2 = st.columns(2)
        with col1:
            idx_cutoff = st.selectbox(
                "기준 Grid String ID (이 ID 이후 검증)",
                range(len(cutoff_opts)),
                format_func=lambda i: cutoff_lbl[i],
                key="cutoff_select",
            )
            cutoff_sim = cutoff_opts[idx_cutoff]
        with col2:
            method_sim = st.selectbox("예측 방법", ["빈도 기반", "가중치 기반", "안전 우선"], key="method")
        
        # 데이터 분리 설명
        if cutoff_sim is not None:
            st.info(f"📊 **데이터 분리**: ID {cutoff_sim} 이전 = 학습 데이터, ID {cutoff_sim} 이후 = 검증 데이터 (모두 검증)")
        else:
            st.info("📊 **데이터 분리**: cutoff를 선택하면 이전은 학습 데이터, 이후는 검증 데이터로 사용됩니다.")
        
        # threshold_skip_anchor_priority 가설인 경우 특별 처리
        is_threshold_skip_anchor_priority = (selected_hypothesis_name == "threshold_skip_anchor_priority")
        # first_anchor_extended_window 가설인 경우 특별 처리
        is_first_anchor_extended = (selected_hypothesis_name == "first_anchor_extended_window")
        # first_anchor_extended_window_v2 가설인 경우 특별 처리
        is_first_anchor_extended_v2 = (selected_hypothesis_name == "first_anchor_extended_window_v2")
        # first_anchor_extended_window_v3 가설인 경우 특별 처리
        is_first_anchor_extended_v3 = (selected_hypothesis_name == "first_anchor_extended_window_v3")
        
        if is_threshold_skip_anchor_priority:
            st.markdown("#### 윈도우 크기 선택 및 임계값 설정")
            st.info("⚠️ 각 윈도우 크기별로 임계값을 개별 설정할 수 있습니다.")
            
            # 윈도우 크기 선택 및 임계값 설정
            window_thresholds = {}
            col_w1, col_w2, col_w3, col_w4, col_w5 = st.columns(5)
            with col_w1:
                w8 = st.checkbox("8", False, key="w8_special")
                if w8:
                    window_thresholds[8] = st.slider("임계값 (8)", 50, 65, 50, key="thresh_8")
            with col_w2:
                w9 = st.checkbox("9", False, key="w9_special")
                if w9:
                    window_thresholds[9] = st.slider("임계값 (9)", 50, 65, 50, key="thresh_9")
            with col_w3:
                w10 = st.checkbox("10", False, key="w10_special")
                if w10:
                    window_thresholds[10] = st.slider("임계값 (10)", 50, 65, 50, key="thresh_10")
            with col_w4:
                w11 = st.checkbox("11", False, key="w11_special")
                if w11:
                    window_thresholds[11] = st.slider("임계값 (11)", 50, 65, 50, key="thresh_11")
            with col_w5:
                w12 = st.checkbox("12", False, key="w12_special")
                if w12:
                    window_thresholds[12] = st.slider("임계값 (12)", 50, 65, 50, key="thresh_12")
            
            ws = list(window_thresholds.keys())
            ws.sort()
            hypothesis_config = {"window_thresholds": window_thresholds}
        elif is_first_anchor_extended:
            st.markdown("#### 윈도우 크기 (9-14)")
            st.info("📌 첫 번째 앵커에서 윈도우 크기 9, 10, 11, 12, 13, 14를 신뢰도 기반으로 검증합니다.")
            
            col_w1, col_w2, col_w3, col_w4, col_w5, col_w6 = st.columns(6)
            with col_w1:
                w9 = st.checkbox("9", True, key="w9_extended")
            with col_w2:
                w10 = st.checkbox("10", True, key="w10_extended")
            with col_w3:
                w11 = st.checkbox("11", True, key="w11_extended")
            with col_w4:
                w12 = st.checkbox("12", True, key="w12_extended")
            with col_w5:
                w13 = st.checkbox("13", True, key="w13_extended")
            with col_w6:
                w14 = st.checkbox("14", True, key="w14_extended")
            
            ws = []
            if w9: ws.append(9)
            if w10: ws.append(10)
            if w11: ws.append(11)
            if w12: ws.append(12)
            if w13: ws.append(13)
            if w14: ws.append(14)
            
            st.markdown("#### 임계값")
            thresh_sim = st.number_input("임계값", 0, 100, 0, key="thresh_extended")
            hypothesis_config = {}
        elif is_first_anchor_extended_v2:
            st.markdown("#### 윈도우 크기 (9-14)")
            st.info("📌 첫 번째 앵커에서 윈도우 크기 9, 10, 11, 12, 13, 14를 신뢰도 기반으로 검증합니다. (V2 독립 구현)")
            
            col_w1, col_w2, col_w3, col_w4, col_w5, col_w6 = st.columns(6)
            with col_w1:
                w9 = st.checkbox("9", True, key="w9_extended_v2")
            with col_w2:
                w10 = st.checkbox("10", True, key="w10_extended_v2")
            with col_w3:
                w11 = st.checkbox("11", True, key="w11_extended_v2")
            with col_w4:
                w12 = st.checkbox("12", True, key="w12_extended_v2")
            with col_w5:
                w13 = st.checkbox("13", True, key="w13_extended_v2")
            with col_w6:
                w14 = st.checkbox("14", True, key="w14_extended_v2")
            
            ws = []
            if w9: ws.append(9)
            if w10: ws.append(10)
            if w11: ws.append(11)
            if w12: ws.append(12)
            if w13: ws.append(13)
            if w14: ws.append(14)
            
            st.markdown("#### 임계값")
            thresh_sim = st.number_input("임계값", 0, 100, 0, key="thresh_extended_v2")
            hypothesis_config = {}
        elif is_first_anchor_extended_v3:
            st.markdown("#### 윈도우 크기 (9-14)")
            st.info("📌 첫 번째 앵커에서 윈도우 크기 9, 10, 11, 12, 13, 14를 신뢰도 기반으로 검증합니다. (V3 - V2 복제, 수정 가능)")
            
            col_w1, col_w2, col_w3, col_w4, col_w5, col_w6 = st.columns(6)
            with col_w1:
                w9 = st.checkbox("9", True, key="w9_extended_v3")
            with col_w2:
                w10 = st.checkbox("10", True, key="w10_extended_v3")
            with col_w3:
                w11 = st.checkbox("11", True, key="w11_extended_v3")
            with col_w4:
                w12 = st.checkbox("12", True, key="w12_extended_v3")
            with col_w5:
                w13 = st.checkbox("13", True, key="w13_extended_v3")
            with col_w6:
                w14 = st.checkbox("14", True, key="w14_extended_v3")
            
            ws = []
            if w9: ws.append(9)
            if w10: ws.append(10)
            if w11: ws.append(11)
            if w12: ws.append(12)
            if w13: ws.append(13)
            if w14: ws.append(14)
            
            st.markdown("#### 임계값")
            thresh_sim = st.number_input("임계값", 0, 100, 0, key="thresh_extended_v3")
            hypothesis_config = {}
        else:
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
            
            st.markdown("#### 임계값")
            thresh_sim = st.number_input("임계값", 0, 100, 0, key="thresh")
            
            # 가설별 설정
            schema = hypothesis_instance.get_config_schema()
            if schema:
                st.markdown("#### 가설별 설정")
                hypothesis_config = render_hypothesis_config_ui(
                    selected_hypothesis_name, hypothesis_instance, "single"
                )
            else:
                hypothesis_config = {}
        
        if st.button("시뮬레이션 실행", type="primary", use_container_width=True):
            if is_first_anchor_extended_v3:
                # V3 독립 검증 함수 사용
                if not ws:
                    st.warning("최소 하나의 윈도우를 선택하세요.")
                elif n_stored == 0:
                    st.warning("예측값을 먼저 생성하세요.")
                else:
                    st.session_state["test_mode"] = "single"
                    st.session_state["test_hypothesis"] = selected_hypothesis_name
                    st.session_state["test_config"] = hypothesis_config
                    st.session_state["test_cutoff"] = cutoff_sim if cutoff_sim is not None else 0
                    st.session_state["test_ws"] = ws
                    st.session_state["test_method"] = method_sim
                    st.session_state["test_thresh"] = thresh_sim
                    st.session_state["test_results"] = None
                    st.rerun()
            elif is_first_anchor_extended_v2:
                # V2 독립 검증 함수 사용
                if not ws:
                    st.warning("최소 하나의 윈도우를 선택하세요.")
                elif n_stored == 0:
                    st.warning("예측값을 먼저 생성하세요.")
                else:
                    st.session_state["test_mode"] = "single"
                    st.session_state["test_hypothesis"] = selected_hypothesis_name
                    st.session_state["test_config"] = hypothesis_config
                    st.session_state["test_cutoff"] = cutoff_sim if cutoff_sim is not None else 0
                    st.session_state["test_ws"] = ws
                    st.session_state["test_method"] = method_sim
                    st.session_state["test_thresh"] = thresh_sim
                    st.session_state["test_results"] = None
                    st.rerun()
            elif is_threshold_skip_anchor_priority:
                # 특별한 검증 함수 사용
                if not ws:
                    st.warning("최소 하나의 윈도우를 선택하세요.")
                elif n_stored == 0:
                    st.warning("예측값을 먼저 생성하세요.")
                else:
                    st.session_state["test_mode"] = "single"
                    st.session_state["test_hypothesis"] = selected_hypothesis_name
                    st.session_state["test_config"] = hypothesis_config
                    st.session_state["test_cutoff"] = cutoff_sim if cutoff_sim is not None else 0
                    st.session_state["test_ws"] = ws
                    st.session_state["test_method"] = method_sim
                    st.session_state["test_thresh"] = window_thresholds  # 윈도우별 임계값 딕셔너리
                    st.session_state["test_results"] = None
                    st.rerun()
            else:
                if not ws:
                    st.warning("최소 하나의 윈도우를 선택하세요.")
                elif n_stored == 0:
                    st.warning("예측값을 먼저 생성하세요.")
                else:
                    st.session_state["test_mode"] = "single"
                    st.session_state["test_hypothesis"] = selected_hypothesis_name
                    st.session_state["test_config"] = hypothesis_config
                    st.session_state["test_cutoff"] = cutoff_sim if cutoff_sim is not None else 0
                    st.session_state["test_ws"] = ws
                    st.session_state["test_method"] = method_sim
                    st.session_state["test_thresh"] = thresh_sim
                    st.session_state["test_results"] = None
                    st.rerun()
    
    else:  # 비교 테스트
        selected_hypotheses = st.multiselect(
            "가설 선택 (여러 개 선택 가능)",
            available_hypotheses,
            format_func=lambda x: get_hypothesis(x).get_name(),
            default=available_hypotheses[:2] if len(available_hypotheses) >= 2 else available_hypotheses,
            key="compare_hypotheses",
        )
        
        if selected_hypotheses:
            st.markdown("#### 선택된 가설 정보")
            for hyp_name in selected_hypotheses:
                hyp_instance = get_hypothesis(hyp_name)
                with st.expander(f"📋 {hyp_instance.get_name()}"):
                    st.text(f"설명: {hyp_instance.get_description()}")
            
            # 시뮬레이션 설정 (가설 선택 하위)
            st.markdown("---")
            st.markdown("### 시뮬레이션 설정")
            
            col1, col2 = st.columns(2)
            with col1:
                idx_cutoff = st.selectbox(
                    "기준 Grid String ID (이 ID 이후 검증)",
                    range(len(cutoff_opts)),
                    format_func=lambda i: cutoff_lbl[i],
                    key="cutoff_select_compare",
                )
                cutoff_sim = cutoff_opts[idx_cutoff]
            with col2:
                method_sim = st.selectbox("예측 방법", ["빈도 기반", "가중치 기반", "안전 우선"], key="method_compare")
            
            # 데이터 분리 설명
            if cutoff_sim is not None:
                st.info(f"📊 **데이터 분리**: ID {cutoff_sim} 이전 = 학습 데이터, ID {cutoff_sim} 이후 = 검증 데이터 (모두 검증)")
            else:
                st.info("📊 **데이터 분리**: cutoff를 선택하면 이전은 학습 데이터, 이후는 검증 데이터로 사용됩니다.")
            
            # threshold_skip_anchor_priority 가설이 포함되어 있는지 확인
            has_threshold_skip = "threshold_skip_anchor_priority" in selected_hypotheses
            
            if has_threshold_skip:
                st.markdown("#### 윈도우 크기 선택 및 임계값 설정 (임계점 스킵 가설용)")
                st.info("⚠️ 임계점 스킵 가설은 각 윈도우 크기별로 임계값을 개별 설정할 수 있습니다.")
                
                window_thresholds = {}
                col_w1, col_w2, col_w3, col_w4, col_w5 = st.columns(5)
                with col_w1:
                    w8 = st.checkbox("8", False, key="w8_compare")
                    if w8:
                        window_thresholds[8] = st.slider("임계값 (8)", 50, 65, 50, key="thresh_8_compare")
                with col_w2:
                    w9 = st.checkbox("9", False, key="w9_compare")
                    if w9:
                        window_thresholds[9] = st.slider("임계값 (9)", 50, 65, 50, key="thresh_9_compare")
                with col_w3:
                    w10 = st.checkbox("10", False, key="w10_compare")
                    if w10:
                        window_thresholds[10] = st.slider("임계값 (10)", 50, 65, 50, key="thresh_10_compare")
                with col_w4:
                    w11 = st.checkbox("11", False, key="w11_compare")
                    if w11:
                        window_thresholds[11] = st.slider("임계값 (11)", 50, 65, 50, key="thresh_11_compare")
                with col_w5:
                    w12 = st.checkbox("12", False, key="w12_compare")
                    if w12:
                        window_thresholds[12] = st.slider("임계값 (12)", 50, 65, 50, key="thresh_12_compare")
                
                ws = list(window_thresholds.keys())
                ws.sort()
            else:
                st.markdown("#### 윈도우 크기")
                col_w1, col_w2, col_w3, col_w4, col_w5 = st.columns(5)
                with col_w1:
                    w5 = st.checkbox("5", True, key="w5_compare")
                    w6 = st.checkbox("6", True, key="w6_compare")
                with col_w2:
                    w7 = st.checkbox("7", True, key="w7_compare")
                    w8 = st.checkbox("8", True, key="w8_compare")
                with col_w3:
                    w9 = st.checkbox("9", True, key="w9_compare")
                    w10 = st.checkbox("10", True, key="w10_compare")
                with col_w4:
                    w11 = st.checkbox("11", True, key="w11_compare")
                    w12 = st.checkbox("12", True, key="w12_compare")
                ws = []
                if w5: ws.append(5)
                if w6: ws.append(6)
                if w7: ws.append(7)
                if w8: ws.append(8)
                if w9: ws.append(9)
                if w10: ws.append(10)
                if w11: ws.append(11)
                if w12: ws.append(12)
            
            st.markdown("#### 임계값 (일반 가설용)")
            thresh_sim = st.number_input("임계값", 0, 100, 0, key="thresh_compare")
            
            st.markdown("#### 가설별 설정")
            hypothesis_configs = {}
            for hyp_name in selected_hypotheses:
                hyp_instance = get_hypothesis(hyp_name)
                schema = hyp_instance.get_config_schema()
                if schema:
                    st.markdown(f"**{hyp_instance.get_name()}**")
                    config = render_hypothesis_config_ui(hyp_name, hyp_instance, f"compare_{hyp_name}")
                    hypothesis_configs[hyp_name] = config
                else:
                    hypothesis_configs[hyp_name] = {}
            
            if has_threshold_skip:
                hypothesis_configs["threshold_skip_anchor_priority"] = {"window_thresholds": window_thresholds}
        
        if st.button("비교 시뮬레이션 실행", type="primary", use_container_width=True):
            if not selected_hypotheses:
                st.warning("최소 하나의 가설을 선택하세요.")
            elif has_threshold_skip and not ws:
                st.warning("임계점 스킵 가설을 위해 최소 하나의 윈도우를 선택하세요.")
            elif not has_threshold_skip and not ws:
                st.warning("최소 하나의 윈도우를 선택하세요.")
            elif n_stored == 0:
                st.warning("예측값을 먼저 생성하세요.")
            else:
                st.session_state["test_mode"] = "compare"
                st.session_state["test_hypotheses"] = selected_hypotheses
                st.session_state["test_configs"] = hypothesis_configs
                st.session_state["test_cutoff"] = cutoff_sim if cutoff_sim is not None else 0
                st.session_state["test_ws"] = ws
                st.session_state["test_method"] = method_sim
                if has_threshold_skip:
                    st.session_state["test_thresh"] = window_thresholds
                else:
                    st.session_state["test_thresh"] = thresh_sim
                st.session_state["test_results"] = None
                st.rerun()
    
    # 결과 표시
    if "test_results" in st.session_state and st.session_state["test_results"] is not None:
        st.markdown("---")
        st.markdown("## 시뮬레이션 결과")
        
        if st.session_state.get("test_mode") == "single":
            # 단일 테스트 결과
            res = st.session_state["test_results"]
            rr = res.get("results", [])
            sm = res.get("summary", {})
            
            if not rr:
                st.info("검증 결과가 없습니다.")
            else:
                col1, col2, col3, col4, col5 = st.columns(5)
                with col1:
                    max_failures = sm.get('max_consecutive_failures', 0)
                    st.metric("최대 연속 불일치", f"{max_failures}회", help="전체 검증 중 가장 많이 연속으로 틀린 횟수")
                with col2:
                    st.metric("평균 최대 연속 불일치", f"{sm.get('avg_max_consecutive_failures', 0):.2f}회", help="각 grid_string의 최대 연속 불일치의 평균")
                with col3:
                    st.metric("평균 정확도", f"{sm.get('avg_accuracy', 0):.2f}%")
                with col4:
                    st.metric("총 예측 횟수", f"{sm.get('total_predictions', 0):,}")
                with col5:
                    st.metric("스킵 횟수", f"{sm.get('total_skipped', 0):,}")
                
                # 최대 연속 불일치별 케이스 개수 통계
                st.markdown("#### 최대 연속 불일치별 케이스 개수")
                failure_counts = {}
                for r in rr:
                    failures = r["max_consecutive_failures"]
                    failure_counts[failures] = failure_counts.get(failures, 0) + 1
                
                failure_stats = []
                for failures in sorted(failure_counts.keys(), reverse=True):
                    failure_stats.append({
                        "최대 연속 불일치": f"{failures}회",
                        "케이스 개수": failure_counts[failures],
                        "비율": f"{failure_counts[failures] / len(rr) * 100:.1f}%"
                    })
                
                if failure_stats:
                    st.dataframe(pd.DataFrame(failure_stats), use_container_width=True, hide_index=True)
                
                st.markdown("#### 상세 결과")
                # grid_string 조회를 위한 딕셔너리 생성
                grid_string_dict = {}
                if len(rr) > 0:
                    grid_string_ids = [r["grid_string_id"] for r in rr]
                    conn = get_change_point_db_connection()
                    try:
                        df_grid = pd.read_sql_query(
                            "SELECT id, grid_string FROM preprocessed_grid_strings WHERE id IN ({})".format(
                                ",".join("?" * len(grid_string_ids))
                            ),
                            conn,
                            params=grid_string_ids,
                        )
                        for _, row in df_grid.iterrows():
                            grid_string_dict[row["id"]] = row["grid_string"]
                    finally:
                        conn.close()
                
                rows = []
                for r in rr:
                    gid = r["grid_string_id"]
                    grid_string = grid_string_dict.get(gid, "N/A")
                    rows.append({
                        "grid_string_id": gid,
                        "전체 스트링": grid_string,
                        "최대 연속 불일치": r["max_consecutive_failures"],
                        "정확도": f"{r['accuracy']:.2f}%",
                        "예측 횟수": r["total_predictions"],
                        "스킵": r.get("total_skipped", 0),
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                
                # grid_string_id 선택 UI
                if rr:
                    grid_string_ids = [r["grid_string_id"] for r in rr]
                    selected_gid_idx = st.selectbox(
                        "상세 히스토리를 조회할 grid_string_id 선택",
                        range(len(grid_string_ids)),
                        format_func=lambda i: f"ID {grid_string_ids[i]}",
                        key="single_history_gid",
                    )
                    selected_gid = grid_string_ids[selected_gid_idx]
                    
                    # 선택한 grid_string_id에 해당하는 결과 찾기
                    selected_result = next((r for r in rr if r["grid_string_id"] == selected_gid), None)
                    
                    with st.expander(f"📊 상세 히스토리 (grid_string_id: {selected_gid})", expanded=True):
                        if selected_result and len(selected_result.get("history", [])) > 0:
                            history_data = []
                            h = selected_result.get("history", [])
                            
                            for entry in h:
                                is_correct = entry.get('is_correct')
                                match_status = '✅' if is_correct else ('❌' if is_correct is False else '-')
                                predicted = entry.get('predicted')
                                skipped = entry.get('skipped', False)
                                skip_reason = entry.get('skip_reason', '')
                                
                                skipped_mark = '⏭️' if skipped else ''
                                if skipped and skip_reason:
                                    skipped_mark = f'⏭️ ({skip_reason})'
                                predicted_display = f"{predicted}{skipped_mark}" if predicted else f"-{skipped_mark}" if skipped else "-"
                                
                                history_data.append({
                                    'Step': entry.get('step', 0),
                                    'Position': entry.get('position', ''),
                                    'Anchor': entry.get('anchor', ''),
                                    'Window Size': entry.get('window_size', ''),
                                    'Prefix': entry.get('prefix', ''),
                                    '예측': predicted_display,
                                    '실제값': entry.get('actual', '-'),
                                    '일치': match_status,
                                    '신뢰도': f"{entry.get('confidence', 0):.1f}%" if predicted else '-',
                                    '선택 윈도우': entry.get('selected_window_size', ''),
                                    '스킵 사유': skip_reason if skipped else '',
                                })
                            
                            if len(history_data) > 0:
                                history_df = pd.DataFrame(history_data)
                                st.dataframe(history_df, use_container_width=True, hide_index=True)
                                st.caption(f"💡 전체 {len(h)}개 히스토리가 표시됩니다.")
                            
                            # 디버깅 정보 표시
                            st.markdown("#### 🔍 디버깅 정보 (각 스텝별 시도한 앵커 및 윈도우)")
                            for entry in h:
                                step = entry.get('step', 0)
                                position = entry.get('position', '')
                                all_anchor_attempts = entry.get('all_anchor_attempts', [])
                                all_predictions = entry.get('all_predictions', [])
                                
                                with st.expander(f"Step {step} - Position {position} 상세", expanded=(step >= 16)):
                                    st.write(f"**사용된 앵커**: {entry.get('anchor', 'N/A')}")
                                    st.write(f"**사용된 윈도우**: {entry.get('window_size', 'N/A')}")
                                    
                                    if all_anchor_attempts:
                                        st.write("**시도한 모든 앵커:**")
                                        for attempt in all_anchor_attempts:
                                            att_anchor = attempt.get("anchor", "")
                                            att_skipped = attempt.get("skipped", False)
                                            att_conf = attempt.get("confidence", 0.0)
                                            att_pred = attempt.get("predicted", "")
                                            att_ws = attempt.get("window_size", "")
                                            status = "⏭️ 스킵" if att_skipped else "✅ 성공"
                                            st.write(f"  - 앵커 {att_anchor}: {status}, 윈도우 {att_ws}, 신뢰도 {att_conf:.1f}%, 예측 {att_pred}")
                                    
                                    if all_predictions:
                                        st.write("**해당 앵커에서 시도한 모든 윈도우:**")
                                        for pred in all_predictions:
                                            ws = pred.get("window_size", "")
                                            conf = pred.get("confidence", 0.0)
                                            pred_val = pred.get("predicted", "")
                                            pfx = pred.get("prefix", "")
                                            st.write(f"  - 윈도우 {ws}: 신뢰도 {conf:.1f}%, 예측 {pred_val}, prefix '{pfx}'")
                        else:
                            st.info("히스토리 데이터가 없습니다.")
        
        else:  # 비교 테스트 결과
            results_dict = st.session_state["test_results"]
            
            # 비교 테이블 생성
            compare_data = []
            for hyp_name, res in results_dict.items():
                hyp_instance = get_hypothesis(hyp_name)
                sm = res.get("summary", {})
                compare_data.append({
                    "가설": hyp_instance.get_name(),
                    "최대 연속 불일치": sm.get("max_consecutive_failures", 0),
                    "평균 정확도": f"{sm.get('avg_accuracy', 0):.2f}%",
                    "총 예측 횟수": sm.get("total_predictions", 0),
                    "스킵 횟수": sm.get("total_skipped", 0),
                    "평균 최대 연속 불일치": f"{sm.get('avg_max_consecutive_failures', 0):.2f}",
                })
            
            st.markdown("#### 가설 비교")
            compare_df = pd.DataFrame(compare_data)
            st.dataframe(compare_df, use_container_width=True, hide_index=True)
            
            # 각 가설별 상세 결과
            st.markdown("#### 가설별 상세 결과")
            for hyp_name, res in results_dict.items():
                hyp_instance = get_hypothesis(hyp_name)
                with st.expander(f"📊 {hyp_instance.get_name()}"):
                    rr = res.get("results", [])
                    sm = res.get("summary", {})
                    
                    col1, col2, col3, col4, col5 = st.columns(5)
                    with col1:
                        max_failures = sm.get('max_consecutive_failures', 0)
                        st.metric("최대 연속 불일치", f"{max_failures}회", help="전체 검증 중 가장 많이 연속으로 틀린 횟수")
                    with col2:
                        st.metric("평균 최대 연속 불일치", f"{sm.get('avg_max_consecutive_failures', 0):.2f}회", help="각 grid_string의 최대 연속 불일치의 평균")
                    with col3:
                        st.metric("평균 정확도", f"{sm.get('avg_accuracy', 0):.2f}%")
                    with col4:
                        st.metric("총 예측 횟수", f"{sm.get('total_predictions', 0):,}")
                    with col5:
                        st.metric("스킵 횟수", f"{sm.get('total_skipped', 0):,}")
                    
                    # 최대 연속 불일치별 케이스 개수 통계
                    if rr:
                        st.markdown("##### 최대 연속 불일치별 케이스 개수")
                        failure_counts = {}
                        for r in rr:
                            failures = r["max_consecutive_failures"]
                            failure_counts[failures] = failure_counts.get(failures, 0) + 1
                        
                        failure_stats = []
                        for failures in sorted(failure_counts.keys(), reverse=True):
                            failure_stats.append({
                                "최대 연속 불일치": f"{failures}회",
                                "케이스 개수": failure_counts[failures],
                                "비율": f"{failure_counts[failures] / len(rr) * 100:.1f}%"
                            })
                        
                        if failure_stats:
                            st.dataframe(pd.DataFrame(failure_stats), use_container_width=True, hide_index=True)
                    
                    if rr:
                        # grid_string 조회를 위한 딕셔너리 생성
                        grid_string_dict = {}
                        if len(rr) > 0:
                            grid_string_ids = [r["grid_string_id"] for r in rr[:10]]
                            conn = get_change_point_db_connection()
                            try:
                                df_grid = pd.read_sql_query(
                                    "SELECT id, grid_string FROM preprocessed_grid_strings WHERE id IN ({})".format(
                                        ",".join("?" * len(grid_string_ids))
                                    ),
                                    conn,
                                    params=grid_string_ids,
                                )
                                for _, row in df_grid.iterrows():
                                    grid_string_dict[row["id"]] = row["grid_string"]
                            finally:
                                conn.close()
                        
                        detail_rows = []
                        for r in rr[:10]:  # 처음 10개만 표시
                            gid = r["grid_string_id"]
                            grid_string = grid_string_dict.get(gid, "N/A")
                            detail_rows.append({
                                "grid_string_id": gid,
                                "전체 스트링": grid_string,
                                "최대 연속 불일치": r["max_consecutive_failures"],
                                "정확도": f"{r['accuracy']:.2f}%",
                                "예측 횟수": r["total_predictions"],
                                "스킵": r.get("total_skipped", 0),
                            })
                        st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)
                        
                        # grid_string_id 선택 UI
                        if rr:
                            grid_string_ids = [r["grid_string_id"] for r in rr]
                            selected_gid_idx = st.selectbox(
                                f"상세 히스토리를 조회할 grid_string_id 선택 ({hyp_instance.get_name()})",
                                range(len(grid_string_ids)),
                                format_func=lambda i: f"ID {grid_string_ids[i]}",
                                key=f"compare_history_gid_{hyp_name}",
                            )
                            selected_gid = grid_string_ids[selected_gid_idx]
                            
                            # 선택한 grid_string_id에 해당하는 결과 찾기
                            selected_result = next((r for r in rr if r["grid_string_id"] == selected_gid), None)
                            
                            with st.expander(f"📊 상세 히스토리 (grid_string_id: {selected_gid})", expanded=True):
                                if selected_result and len(selected_result.get("history", [])) > 0:
                                    history_data = []
                                    h = selected_result.get("history", [])
                                    
                                    for entry in h:
                                        is_correct = entry.get('is_correct')
                                        match_status = '✅' if is_correct else ('❌' if is_correct is False else '-')
                                        predicted = entry.get('predicted')
                                        skipped = entry.get('skipped', False)
                                        skip_reason = entry.get('skip_reason', '')
                                        
                                        skipped_mark = '⏭️' if skipped else ''
                                        if skipped and skip_reason:
                                            skipped_mark = f'⏭️ ({skip_reason})'
                                        predicted_display = f"{predicted}{skipped_mark}" if predicted else f"-{skipped_mark}" if skipped else "-"
                                        
                                        history_data.append({
                                            'Step': entry.get('step', 0),
                                            'Position': entry.get('position', ''),
                                            'Anchor': entry.get('anchor', ''),
                                            'Window Size': entry.get('window_size', ''),
                                            'Prefix': entry.get('prefix', ''),
                                            '예측': predicted_display,
                                            '실제값': entry.get('actual', '-'),
                                            '일치': match_status,
                                            '신뢰도': f"{entry.get('confidence', 0):.1f}%" if predicted else '-',
                                            '선택 윈도우': entry.get('selected_window_size', ''),
                                            '스킵 사유': skip_reason if skipped else '',
                                        })
                                    
                                    if len(history_data) > 0:
                                        history_df = pd.DataFrame(history_data)
                                        st.dataframe(history_df, use_container_width=True, hide_index=True)
                                        st.caption(f"💡 전체 {len(h)}개 히스토리가 표시됩니다.")
                                    
                                    # 디버깅 정보 표시
                                    st.markdown("#### 🔍 디버깅 정보 (각 스텝별 시도한 앵커 및 윈도우)")
                                    for entry in h:
                                        step = entry.get('step', 0)
                                        position = entry.get('position', '')
                                        all_anchor_attempts = entry.get('all_anchor_attempts', [])
                                        all_predictions = entry.get('all_predictions', [])
                                        
                                        with st.expander(f"Step {step} - Position {position} 상세", expanded=(step >= 16)):
                                            st.write(f"**사용된 앵커**: {entry.get('anchor', 'N/A')}")
                                            st.write(f"**사용된 윈도우**: {entry.get('window_size', 'N/A')}")
                                            
                                            if all_anchor_attempts:
                                                st.write("**시도한 모든 앵커:**")
                                                for attempt in all_anchor_attempts:
                                                    att_anchor = attempt.get("anchor", "")
                                                    att_skipped = attempt.get("skipped", False)
                                                    att_conf = attempt.get("confidence", 0.0)
                                                    att_pred = attempt.get("predicted", "")
                                                    att_ws = attempt.get("window_size", "")
                                                    status = "⏭️ 스킵" if att_skipped else "✅ 성공"
                                                    st.write(f"  - 앵커 {att_anchor}: {status}, 윈도우 {att_ws}, 신뢰도 {att_conf:.1f}%, 예측 {att_pred}")
                                            
                                            if all_predictions:
                                                st.write("**해당 앵커에서 시도한 모든 윈도우:**")
                                                for pred in all_predictions:
                                                    ws = pred.get("window_size", "")
                                                    conf = pred.get("confidence", 0.0)
                                                    pred_val = pred.get("predicted", "")
                                                    pfx = pred.get("prefix", "")
                                                    st.write(f"  - 윈도우 {ws}: 신뢰도 {conf:.1f}%, 예측 {pred_val}, prefix '{pfx}'")
                                else:
                                    st.info("히스토리 데이터가 없습니다.")
    
    # 시뮬레이션 실행
    elif "test_cutoff" in st.session_state:
        test_mode = st.session_state.get("test_mode")
        cutoff_sim = st.session_state.get("test_cutoff", 0)
        ws = st.session_state.get("test_ws", [5, 6, 7, 8, 9, 10, 11, 12])
        method_sim = st.session_state.get("test_method", "빈도 기반")
        thresh_sim = st.session_state.get("test_thresh", 0)
        
        with st.spinner("시뮬레이션 실행 중..."):
            bar = st.progress(0)
            status = st.empty()
            
            try:
                if test_mode == "single":
                    # 단일 테스트
                    hyp_name = st.session_state.get("test_hypothesis")
                    hyp_config = st.session_state.get("test_config", {})
                    
                    status.text(f"가설 '{get_hypothesis(hyp_name).get_name()}' 실행 중...")
                    
                    # first_anchor_extended_window_v3 가설인 경우 독립 검증 함수 사용
                    if hyp_name == "first_anchor_extended_window_v3":
                        res = batch_validate_first_anchor_extended_window_v3_cp(
                            cutoff_sim,
                            window_sizes=tuple(ws),
                            method=method_sim,
                            threshold=thresh_sim,
                        )
                    # first_anchor_extended_window_v2 가설인 경우 독립 검증 함수 사용
                    elif hyp_name == "first_anchor_extended_window_v2":
                        res = batch_validate_first_anchor_extended_window_v2_cp(
                            cutoff_sim,
                            window_sizes=tuple(ws),
                            method=method_sim,
                            threshold=thresh_sim,
                        )
                    # threshold_skip_anchor_priority 가설인 경우 특별한 검증 함수 사용
                    elif hyp_name == "threshold_skip_anchor_priority":
                        window_thresholds = hyp_config.get("window_thresholds", {})
                        res = batch_validate_threshold_skip_anchor_priority_cp(
                            cutoff_sim,
                            window_sizes=tuple(ws),
                            method=method_sim,
                            threshold=50,  # 기본값 (실제로는 window_thresholds 사용)
                            window_thresholds=window_thresholds,
                        )
                    else:
                        hypothesis = get_hypothesis(hyp_name, **hyp_config)
                        res = batch_validate_hypothesis_cp(
                            cutoff_sim,
                            hypothesis=hypothesis,
                            window_sizes=tuple(ws),
                            method=method_sim,
                            threshold=thresh_sim,
                            **hyp_config
                        )
                    st.session_state["test_results"] = res
                    bar.progress(1.0)
                    status.text("완료")
                
                else:  # 비교 테스트
                    hypotheses = st.session_state.get("test_hypotheses", [])
                    configs = st.session_state.get("test_configs", {})
                    
                    results_dict = {}
                    total = len(hypotheses)
                    
                    for i, hyp_name in enumerate(hypotheses):
                        hyp_instance = get_hypothesis(hyp_name)
                        status.text(f"가설 '{hyp_instance.get_name()}' 실행 중... ({i+1}/{total})")
                        bar.progress((i + 0.5) / total)
                        
                        # first_anchor_extended_window_v3 가설인 경우 독립 검증 함수 사용
                        if hyp_name == "first_anchor_extended_window_v3":
                            res = batch_validate_first_anchor_extended_window_v3_cp(
                                cutoff_sim,
                                window_sizes=tuple(ws),
                                method=method_sim,
                                threshold=thresh_sim,
                            )
                        # first_anchor_extended_window_v2 가설인 경우 독립 검증 함수 사용
                        elif hyp_name == "first_anchor_extended_window_v2":
                            res = batch_validate_first_anchor_extended_window_v2_cp(
                                cutoff_sim,
                                window_sizes=tuple(ws),
                                method=method_sim,
                                threshold=thresh_sim,
                            )
                        # threshold_skip_anchor_priority 가설인 경우 특별한 검증 함수 사용
                        elif hyp_name == "threshold_skip_anchor_priority":
                            hyp_config = configs.get(hyp_name, {})
                            window_thresholds = hyp_config.get("window_thresholds", {})
                            res = batch_validate_threshold_skip_anchor_priority_cp(
                                cutoff_sim,
                                window_sizes=tuple(ws),
                                method=method_sim,
                                threshold=50,  # 기본값 (실제로는 window_thresholds 사용)
                                window_thresholds=window_thresholds,
                            )
                        else:
                            hyp_config = configs.get(hyp_name, {})
                            hypothesis = get_hypothesis(hyp_name, **hyp_config)
                            res = batch_validate_hypothesis_cp(
                                cutoff_sim,
                                hypothesis=hypothesis,
                                window_sizes=tuple(ws),
                                method=method_sim,
                                threshold=thresh_sim,
                                **hyp_config
                            )
                        results_dict[hyp_name] = res
                    
                    st.session_state["test_results"] = results_dict
                    bar.progress(1.0)
                    status.text("완료")
                
            except Exception as e:
                st.error(f"시뮬레이션 실패: {e}")
                import traceback
                st.code(traceback.format_exc())
            finally:
                bar.empty()
                status.empty()
        
        st.rerun()


if __name__ == "__main__":
    main()
