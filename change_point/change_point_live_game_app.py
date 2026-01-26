"""
임계점 스킵 + 앵커 우선순위 라이브 게임 앱
Change-point Detection 기반으로 스텝별 예측값을 확인하고 실제값을 입력하여 검증하는 라이브 게임
"""

import sys
from pathlib import Path

# 상위 폴더의 모듈을 import하기 위해 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
from datetime import datetime

# 페이지 설정
st.set_page_config(
    page_title="Change-point 라이브 게임",
    page_icon="🎮",
    layout="wide"
)

from svg_parser_module import get_change_point_db_connection
from change_point_prediction_module import (
    load_preprocessed_grid_strings_cp,
    get_stored_predictions_change_point_count,
)
from change_point_hypothesis_module import (
    ThresholdSkipAnchorPriorityHypothesis,
)


def _fmt_dt(s):
    """날짜 포맷팅"""
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


def detect_change_points(grid_string):
    """
    Change-point Detection: 변화점 감지 및 앵커 위치 반환
    """
    anchors = []
    for i in range(len(grid_string) - 1):
        if grid_string[i] != grid_string[i+1]:
            anchors.append(i)
    return sorted(list(set(anchors)))


def render_grid_string_with_anchors(grid_string, anchors, current_position, debug_info=None, selected_anchor=None):
    """
    Grid String 전체를 표시하고 앵커 위치와 현재 position을 시각화
    디버깅 정보 포함
    
    Args:
        grid_string: 전체 grid string
        anchors: 앵커 위치 리스트
        current_position: 현재 예측할 position (인덱스)
        debug_info: 디버깅 정보 (선택적)
        selected_anchor: 선택된 앵커 (선택적)
    """
    display_parts = []
    for i, char in enumerate(grid_string):
        style_parts = []
        
        # 선택된 앵커 표시 (빨간색 테두리)
        if selected_anchor is not None and i == selected_anchor:
            style_parts.append("background-color: #FFE6E6; border: 2px solid red;")
        
        # 앵커 위치 표시 (연한 파란색 배경)
        elif i in anchors:
            style_parts.append("background-color: #ADD8E6;")
        
        # 현재 position 표시 (노란색 배경, 굵게)
        if i == current_position:
            style_parts.append("background-color: yellow; font-weight: bold;")
        
        # 스타일 적용
        if style_parts:
            style = " ".join(style_parts)
            display_parts.append(f"<span style='{style}'>{char}</span>")
        else:
            display_parts.append(char)
    
    display_string = "".join(display_parts)
    st.markdown(
        f"<div style='font-size: 20px; font-family: monospace; padding: 10px; border: 1px solid #ddd; border-radius: 5px;'>{display_string}</div>",
        unsafe_allow_html=True
    )
    
    # 인덱스 표시
    index_string = "".join([str(i % 10) for i in range(len(grid_string))])
    st.markdown(
        f"<div style='font-size: 12px; font-family: monospace; color: #666; text-align: center;'>{index_string}</div>",
        unsafe_allow_html=True
    )
    
    # 디버깅 정보 표시
    if debug_info and debug_info.get('all_attempts'):
        st.markdown("#### 🔍 앵커 선택 디버깅 정보")
        debug_data = []
        for attempt in debug_info.get('all_attempts', []):
            # prefix 정보 추가 (grid_string에서 계산)
            anchor = attempt.get('anchor', '')
            window_size = attempt.get('window_size', '')
            position = debug_info.get('position', 0)
            prefix_display = '-'
            if anchor != '' and window_size != '' and position is not None:
                try:
                    prefix_len = window_size - 1
                    if position >= prefix_len and position <= len(grid_string):
                        prefix_display = grid_string[position - prefix_len : position]
                except:
                    pass
            
            debug_data.append({
                'Position': debug_info.get('position', ''),
                '앵커': anchor,
                '윈도우': window_size,
                'Prefix': prefix_display,
                '예측값': attempt.get('predicted', '-'),
                '신뢰도': f"{attempt.get('confidence', 0):.1f}%" if attempt.get('confidence', 0) > 0 else '-',
                '결과': attempt.get('rejection_reason', '-')
            })
        
        if len(debug_data) > 0:
            debug_df = pd.DataFrame(debug_data)
            st.dataframe(debug_df, use_container_width=True, hide_index=True)
            
            # 선택된 앵커 강조
            if selected_anchor is not None:
                st.info(f"✅ **선택된 앵커: {selected_anchor}** (Position {debug_info.get('position', 'N/A')}에서 사용)")
        else:
            st.warning("⚠️ 디버깅 정보가 없습니다. 예측값을 찾을 수 없습니다.")


def update_anchors_after_input(grid_string):
    """
    실제값 입력 후 앵커 재계산
    
    Args:
        grid_string: 업데이트된 grid string
        
    Returns:
        list: 새로운 앵커 위치 리스트
    """
    return detect_change_points(grid_string)


def validate_initial_grid_string(grid_string, anchors, window_sizes, method, window_thresholds):
    """
    게임 시작 시점의 grid_string을 기준으로 모든 position에 대해 예측 수행 및 검증
    이전 히스토리 생성
    
    Args:
        grid_string: 초기 grid_string
        anchors: 앵커 위치 리스트
        window_sizes: 윈도우 크기 목록
        method: 예측 방법
        window_thresholds: 윈도우 크기별 임계값 딕셔너리
        
    Returns:
        list: 히스토리 리스트 (각 position에 대한 예측 및 검증 결과)
    """
    history = []
    max_ws = max(window_sizes)
    
    # 최소 윈도우 크기 이상인 모든 position에 대해 예측 수행
    for position in range(max_ws, len(grid_string)):
        # 예측 수행
        pred_result = predict_for_position(
            grid_string,
            position,
            anchors,
            window_sizes,
            method,
            window_thresholds,
        )
        
        # 실제값 가져오기
        actual_value = grid_string[position] if position < len(grid_string) else None
        
        if pred_result:
            predicted_value = pred_result.get("predicted")
            confidence = pred_result.get("confidence", 0.0)
            window_size = pred_result.get("window_size")
            prefix = pred_result.get("prefix", "")
            anchor = pred_result.get("anchor")
            skipped = pred_result.get("skipped", False)
            debug_info = pred_result.get("debug_info", {})
            
            # 검증 수행
            is_correct = None
            if predicted_value is not None and not skipped and actual_value:
                is_correct = predicted_value == actual_value
            
            history.append({
                'step': position - max_ws + 1,  # 1부터 시작하는 스텝 번호
                'position': position,
                'anchor': anchor,
                'window_size': window_size,
                'prefix': prefix,
                'predicted': predicted_value,
                'actual': actual_value,
                'is_correct': is_correct,
                'confidence': confidence,
                'skipped': skipped,
                'validated': True,
                'interval': 0,
                'debug_info': debug_info
            })
        else:
            # 예측 실패
            history.append({
                'step': position - max_ws + 1,
                'position': position,
                'anchor': None,
                'window_size': None,
                'prefix': '',
                'predicted': None,
                'actual': actual_value,
                'is_correct': None,
                'confidence': 0.0,
                'skipped': True,
                'validated': True,
                'interval': 0,
                'debug_info': {}
            })
    
    return history


def get_anchor_priority_for_position(position, anchors, window_sizes):
    """
    현재 position에서 예측 가능한 앵커-윈도우 조합을 우선순위대로 반환
    
    Args:
        position: 예측할 position (인덱스)
        anchors: 앵커 위치 리스트
        window_sizes: 윈도우 크기 목록
        
    Returns:
        list of tuples: [(anchor, window_size, priority), ...]
        priority가 낮을수록 높은 우선순위
    """
    candidates = []
    
    for anchor in anchors:
        for window_size in sorted(window_sizes, reverse=True):  # 큰 윈도우부터
            if anchor + window_size - 1 == position:
                if position >= window_size - 1:  # prefix 조건 확인
                    # 우선순위: 큰 윈도우가 높은 우선순위
                    priority = len(window_sizes) - window_sizes.index(window_size) if window_size in window_sizes else 999
                    candidates.append((anchor, window_size, priority))
                    break  # 한 앵커당 하나의 윈도우만 (가장 큰 것)
    
    # 우선순위 정렬: priority 낮은 순 (높은 우선순위), 같은 priority면 작은 anchor
    candidates.sort(key=lambda x: (x[2], x[0]))
    
    return candidates


def predict_for_position(
    grid_string,
    position,
    anchors,
    window_sizes,
    method,
    window_thresholds,
):
    """
    특정 position에서 예측 수행 (앵커 우선순위 적용)
    디버깅 정보 포함
    
    Args:
        grid_string: 전체 grid string
        position: 예측할 position
        anchors: 앵커 위치 리스트
        window_sizes: 윈도우 크기 목록
        method: 예측 방법
        window_thresholds: 윈도우 크기별 임계값 딕셔너리
        
    Returns:
        dict: 예측 결과 및 디버깅 정보
    """
    # 해당 position에 도달할 수 있는 모든 앵커 찾기
    # 조건: anchor + window_size - 1 == position
    # 즉: anchor <= position 이어야 함
    possible_anchors = []
    for anchor in anchors:
        # anchor가 position보다 크면 해당 position에 도달할 수 없음
        if anchor > position:
            continue
        for window_size in window_sizes:
            if anchor + window_size - 1 == position:
                if position >= window_size - 1:  # prefix 조건 확인
                    if anchor not in possible_anchors:
                        possible_anchors.append(anchor)
                    break
    
    if not possible_anchors:
        return {
            "predicted": None,
            "confidence": 0.0,
            "window_size": None,
            "prefix": None,
            "anchor": None,
            "skipped": True,
            "debug_info": {
                "position": position,
                "selected_anchor": None,
                "selected_window": None,
                "selected_prediction": None,
                "selected_confidence": 0.0,
                "all_attempts": []
            }
        }
    
    # 앵커를 작은 순서대로 정렬 (작은 앵커 우선 - 이전 앵커 우선순위)
    possible_anchors = sorted(possible_anchors)
    
    hypothesis = ThresholdSkipAnchorPriorityHypothesis()
    all_attempts = []
    selected_result = None
    selected_anchor = None
    selected_window = None
    
    # 각 앵커를 작은 순서대로 시도 (앵커 우선순위: 작은 앵커 우선 - 이전 앵커 우선)
    for anchor in possible_anchors:
        # 해당 앵커에서 해당 position에 도달 가능한 모든 윈도우 크기 시도
        pred_res = hypothesis.predict(
            grid_string, position, window_sizes=window_sizes,
            method=method, threshold=50, anchor=anchor,
            window_thresholds=window_thresholds
        )
        
        # all_predictions와 all_attempts_debug에서 각 윈도우별 시도 정보 수집
        all_predictions = pred_res.get("all_predictions", [])
        all_attempts_debug = pred_res.get("all_attempts_debug", [])
        
        # 디버깅 정보가 있으면 사용 (DB 조회 실패, 임계값 미만 등 상세 정보 포함)
        if all_attempts_debug:
            for attempt_debug in all_attempts_debug:
                ws = attempt_debug.get("window_size")
                conf = attempt_debug.get("confidence", 0.0)
                pred_val = attempt_debug.get("predicted")
                reason = attempt_debug.get("reason", "알 수 없음")
                
                attempt_info = {
                    "anchor": anchor,
                    "window_size": ws,
                    "predicted": pred_val,
                    "confidence": conf,
                    "skipped": pred_val is None or reason != "성공",
                    "rejection_reason": reason
                }
                
                # 성공한 경우에만 선택 여부 확인
                if reason == "성공" and pred_res.get("window_size") == ws:
                    attempt_info["rejection_reason"] = "선택됨"
                
                all_attempts.append(attempt_info)
        elif all_predictions:
            # all_attempts_debug가 없으면 all_predictions 사용 (하위 호환성)
            for pred_info in all_predictions:
                ws = pred_info.get("window_size")
                conf = pred_info.get("confidence", 0.0)
                pred_val = pred_info.get("predicted")
                ws_threshold = window_thresholds.get(ws, 50)
                
                attempt_info = {
                    "anchor": anchor,
                    "window_size": ws,
                    "predicted": pred_val,
                    "confidence": conf,
                    "skipped": conf < ws_threshold,
                    "rejection_reason": None
                }
                
                if conf < ws_threshold:
                    attempt_info["rejection_reason"] = f"임계값 미만 ({conf:.1f}% < {ws_threshold}%)"
                else:
                    attempt_info["rejection_reason"] = "선택됨" if pred_res.get("window_size") == ws else "다른 윈도우 선택됨"
                
                all_attempts.append(attempt_info)
        else:
            # all_predictions가 없는 경우 (예측값 없음 또는 스킵)
            # 해당 앵커에서 가능한 윈도우 크기들을 모두 추가
            for window_size in sorted(window_sizes, reverse=True):
                if anchor + window_size - 1 == position and position >= window_size - 1:
                    all_attempts.append({
                        "anchor": anchor,
                        "window_size": window_size,
                        "predicted": None,
                        "confidence": 0.0,
                        "skipped": True,
                        "rejection_reason": "예측값 없음" if pred_res.get("skipped", False) else "스킵됨"
                    })
                    break  # 한 앵커당 하나의 윈도우만 표시
        
        # 예측값이 있고 스킵되지 않았으면 사용
        if pred_res.get("predicted") is not None and not pred_res.get("skipped", False):
            selected_result = pred_res
            selected_anchor = anchor
            selected_window = pred_res.get("window_size")
            
            # 선택된 조합 표시 업데이트
            for attempt in all_attempts:
                if attempt["anchor"] == selected_anchor and attempt["window_size"] == selected_window:
                    attempt["rejection_reason"] = "선택됨"
            
            break  # 성공했으므로 더 이상 앵커 시도하지 않음
    
    # 선택된 결과가 없으면 모든 시도가 실패
    if selected_result is None:
        return {
            "predicted": None,
            "confidence": 0.0,
            "window_size": None,
            "prefix": None,
            "anchor": None,
            "skipped": True,
            "debug_info": {
                "position": position,
                "selected_anchor": None,
                "selected_window": None,
                "selected_prediction": None,
                "selected_confidence": 0.0,
                "all_attempts": all_attempts
            }
        }
    
    # 나머지 시도하지 않은 앵커들도 추가 (이전 앵커에서 성공하여 시도하지 않음)
    used_anchor_idx = possible_anchors.index(selected_anchor) if selected_anchor in possible_anchors else -1
    if used_anchor_idx >= 0:
        for anchor in possible_anchors[used_anchor_idx + 1:]:
            # 해당 앵커에서 가능한 모든 윈도우 크기 추가
            for window_size in sorted(window_sizes, reverse=True):
                if anchor + window_size - 1 == position and position >= window_size - 1:
                    all_attempts.append({
                        "anchor": anchor,
                        "window_size": window_size,
                        "predicted": None,
                        "confidence": 0.0,
                        "skipped": True,
                        "rejection_reason": "이전 앵커에서 성공"
                    })
                    break
    
    # all_attempts를 우선순위대로 정렬 (작은 앵커, 큰 윈도우 우선)
    all_attempts.sort(key=lambda x: (x.get('anchor', 0), -x.get('window_size', 0)))
    
    return {
        **selected_result,
        "anchor": selected_anchor,
        "debug_info": {
            "position": position,
            "selected_anchor": selected_anchor,
            "selected_window": selected_window,
            "selected_prediction": selected_result.get("predicted"),
            "selected_confidence": selected_result.get("confidence", 0.0),
            "all_attempts": all_attempts
        }
    }


def main():
    st.title("🎮 Change-point 임계점 스킵 라이브 게임")
    st.markdown("**Change-point Detection 기반으로 스텝별 예측값을 확인하고 실제값을 입력하여 검증하는 라이브 게임**")
    
    # 저장된 예측값 확인
    n_stored = get_stored_predictions_change_point_count()
    if n_stored == 0:
        st.warning("⚠️ stored_predictions_change_point가 비어 있습니다. 예측값을 먼저 생성하세요.")
    
    # 게임 상태 초기화
    if 'change_point_game_state' not in st.session_state:
        st.session_state.change_point_game_state = None
    
    # 게임 설정
    st.markdown("---")
    st.markdown("## ⚙️ 게임 설정")
    
    df_mw = load_preprocessed_grid_strings_cp()
    if len(df_mw) == 0:
        st.warning("preprocessed_grid_strings에 데이터가 없습니다.")
        return
    
    cutoff_opts = [None] + df_mw["id"].tolist()
    cutoff_lbl = ["전체 (ID 이후 없음)"] + [f"ID {r['id']} 이후 ({_fmt_dt(r['created_at'])})" for _, r in df_mw.iterrows()]
    
    col1, col2 = st.columns(2)
    with col1:
        idx_cutoff = st.selectbox(
            "기준 Grid String ID (이 ID 이후 검증)",
            range(len(cutoff_opts)),
            format_func=lambda i: cutoff_lbl[i],
            key="cutoff_select",
        )
        cutoff_id = cutoff_opts[idx_cutoff] if cutoff_opts else None
    with col2:
        method = st.selectbox("예측 방법", ["빈도 기반", "가중치 기반", "안전 우선"], key="method")
    
    st.markdown("#### 윈도우 크기 선택 및 임계값 설정")
    st.info("⚠️ 각 윈도우 크기별로 임계값을 개별 설정할 수 있습니다.")
    
    window_thresholds = {}
    col_w1, col_w2, col_w3, col_w4, col_w5 = st.columns(5)
    with col_w1:
        w8 = st.checkbox("8", False, key="w8")
        if w8:
            window_thresholds[8] = st.slider("임계값 (8)", 50, 65, 50, key="thresh_8")
    with col_w2:
        w9 = st.checkbox("9", False, key="w9")
        if w9:
            window_thresholds[9] = st.slider("임계값 (9)", 50, 65, 50, key="thresh_9")
    with col_w3:
        w10 = st.checkbox("10", False, key="w10")
        if w10:
            window_thresholds[10] = st.slider("임계값 (10)", 50, 65, 50, key="thresh_10")
    with col_w4:
        w11 = st.checkbox("11", False, key="w11")
        if w11:
            window_thresholds[11] = st.slider("임계값 (11)", 50, 65, 50, key="thresh_11")
    with col_w5:
        w12 = st.checkbox("12", False, key="w12")
        if w12:
            window_thresholds[12] = st.slider("임계값 (12)", 50, 65, 50, key="thresh_12")
    
    window_sizes = sorted(list(window_thresholds.keys()))
    
    # Grid String 입력 (text_area로 변경)
    st.markdown("---")
    st.markdown("## 📝 Grid String 입력")
    
    grid_string_input = st.text_area(
        "Grid String 입력",
        key="live_game_grid_string",
        height=80,
        help="라이브 게임에서 사용할 grid_string을 입력하세요..."
    )
    
    # 게임 시작/재시작 버튼
    col_start1, col_start2 = st.columns([1, 4])
    with col_start1:
        if st.button("🎮 게임 시작", type="primary", use_container_width=True):
            if not window_sizes:
                st.warning("최소 하나의 윈도우를 선택하세요.")
            elif not grid_string_input or len(grid_string_input.strip()) == 0:
                st.warning("Grid String을 입력하세요.")
            elif n_stored == 0:
                st.warning("예측값을 먼저 생성하세요.")
            elif len(grid_string_input.strip()) < max(window_sizes):
                st.warning(f"Grid String 길이가 최소 {max(window_sizes)} 이상이어야 합니다.")
            else:
                grid_string = grid_string_input.strip()
                
                # Change-point Detection: 앵커 위치 수집
                anchors = detect_change_points(grid_string)
                
                if not anchors:
                    st.warning("Change-point가 감지되지 않았습니다.")
                else:
                    # 게임 상태 초기화
                    # 초기 예측 포지션은 grid_string의 길이 (아직 입력되지 않은 다음 포지션)
                    initial_position = len(grid_string)
                    
                    # 전체 스트링 검증하여 이전 히스토리 생성
                    initial_history = validate_initial_grid_string(
                        grid_string,
                        anchors,
                        window_sizes,
                        method,
                        window_thresholds
                    )
                    
                    # 통계 계산
                    total_predictions = sum(1 for h in initial_history if h.get('predicted') is not None and not h.get('skipped', False))
                    total_failures = sum(1 for h in initial_history if h.get('is_correct') is False)
                    total_skipped = sum(1 for h in initial_history if h.get('skipped', False))
                    
                    # 연속 실패 계산
                    consecutive_failures = 0
                    max_consecutive_failures = 0
                    for h in initial_history:
                        if h.get('is_correct') is False:
                            consecutive_failures += 1
                            if consecutive_failures > max_consecutive_failures:
                                max_consecutive_failures = consecutive_failures
                        elif h.get('is_correct') is True:
                            consecutive_failures = 0
                    
                    st.session_state.change_point_game_state = {
                        'grid_string': grid_string,
                        'initial_grid_string': grid_string,  # 초기 grid_string 저장 (이전 히스토리 prefix 계산용)
                        'anchors': anchors,
                        'window_sizes': window_sizes,
                        'window_thresholds': window_thresholds,
                        'method': method,
                        'cutoff_id': cutoff_id if cutoff_id is not None else 0,
                        'current_step': len(initial_history),  # 이전 히스토리 개수
                        'current_index': len(grid_string) - 1,  # 마지막 인덱스
                        'current_position': initial_position,  # 다음 예측할 포지션
                        'total_steps': len(initial_history),
                        'total_predictions': total_predictions,
                        'total_failures': total_failures,
                        'total_skipped': total_skipped,
                        'consecutive_failures': consecutive_failures,
                        'max_consecutive_failures': max_consecutive_failures,
                        'history': initial_history,
                    }
                    st.rerun()
    
    with col_start2:
        if st.session_state.change_point_game_state is not None:
            if st.button("🔄 게임 재시작", use_container_width=True):
                st.session_state.change_point_game_state = None
                st.rerun()
    
    # 게임 진행
    if st.session_state.change_point_game_state is not None:
        game_state = st.session_state.change_point_game_state
        grid_string = game_state['grid_string']
        anchors = game_state['anchors']
        window_sizes = game_state['window_sizes']
        window_thresholds = game_state['window_thresholds']
        method = game_state['method']
        current_position = game_state['current_position']
        current_step = game_state['current_step']
        
        st.markdown("---")
        st.markdown("## 🎮 게임 진행")
        
        # 현재 상태 표시
        col_stat1, col_stat2, col_stat3, col_stat4 = st.columns(4)
        with col_stat1:
            st.metric("현재 Step", current_step + 1)
        with col_stat2:
            st.metric("최대 연속 불일치", game_state['max_consecutive_failures'])
        with col_stat3:
            st.metric("총 예측 횟수", game_state['total_predictions'])
        with col_stat4:
            st.metric("스킵 횟수", game_state['total_skipped'])
        
        # Grid String 전체 표시 및 앵커 시각화
        st.markdown("---")
        st.markdown("### Grid String 및 앵커")
        
        # 다음 예측할 position은 grid_string의 길이 (아직 입력되지 않은 다음 포지션)
        next_position = len(grid_string)
        
        # 다음 position에서 예측 결과 가져오기 (디버깅 정보 포함)
        pred_result_for_debug = None
        selected_anchor_for_display = None
        debug_info_for_display = None
        
        # position이 grid_string 범위를 벗어나지 않는 경우에만 예측 가능
        # 하지만 라이브 게임에서는 항상 grid_string 길이와 동일한 포지션을 예측
        if next_position >= max(window_sizes):  # 최소 윈도우 크기 이상이어야 예측 가능
            pred_result_for_debug = predict_for_position(
                grid_string,
                next_position,
                anchors,
                window_sizes,
                method,
                window_thresholds,
            )
            if pred_result_for_debug:
                selected_anchor_for_display = pred_result_for_debug.get("anchor")
                debug_info_for_display = pred_result_for_debug.get("debug_info")
        else:
            # 게임 완료 상태에서는 마지막 히스토리의 정보 사용
            if game_state['history']:
                last_entry = game_state['history'][-1]
                selected_anchor_for_display = last_entry.get('anchor')
                debug_info_for_display = last_entry.get('debug_info')
        
        render_grid_string_with_anchors(
            grid_string, 
            anchors, 
            next_position,  # 항상 grid_string 길이와 동일한 포지션 표시
            debug_info=debug_info_for_display,
            selected_anchor=selected_anchor_for_display
        )
        st.caption("💡 연한 파란색: 앵커 위치, 노란색: 현재 예측할 position (grid_string 길이), 빨간색 테두리: 선택된 앵커")
        
        # 예측 가능 여부 확인 (최소 윈도우 크기 이상이어야 함)
        max_ws = max(window_sizes)
        
        # 라이브 게임에서는 항상 다음 포지션을 예측할 수 있음
        # 게임 완료는 사용자가 명시적으로 중단할 때만
        
        # 예측 수행
        st.markdown("---")
        st.markdown("### 📍 현재 스텝")
        
        pred_result = predict_for_position(
            grid_string,
            next_position,
            anchors,
            window_sizes,
            method,
            window_thresholds,
        )
        
        if pred_result is None:
            st.error("예측을 수행할 수 없습니다.")
        else:
            predicted_value = pred_result.get("predicted")
            confidence = pred_result.get("confidence", 0.0)
            window_size = pred_result.get("window_size")
            prefix = pred_result.get("prefix", "")
            anchor = pred_result.get("anchor")
            skipped = pred_result.get("skipped", False)
            has_prediction = predicted_value is not None and not skipped
            debug_info = pred_result.get("debug_info", {})
            
            # 현재 스텝 정보 표시 (live_game_app_parallel.py 구조)
            col_info1, col_info2, col_info3, col_info4 = st.columns(4)
            with col_info1:
                st.caption("Prefix")
                st.markdown(f"<div style='font-size: 18px; font-family: monospace;'>{prefix if prefix else '-'}</div>", unsafe_allow_html=True)
            with col_info2:
                if has_prediction:
                    st.caption("예측값")
                    st.markdown(f"<div style='font-size: 24px; font-weight: bold;'>{predicted_value}</div>", unsafe_allow_html=True)
                else:
                    st.caption("예측값")
                    st.text("⏭️ 스킵")
            with col_info3:
                if has_prediction:
                    st.caption("신뢰도")
                    st.markdown(f"<div style='font-size: 18px;'>{confidence:.1f}%</div>", unsafe_allow_html=True)
                else:
                    st.caption("신뢰도")
                    st.text("-")
            with col_info4:
                st.caption("Anchor / Window")
                st.text(f"{anchor} / {window_size}" if anchor is not None and window_size is not None else "-")
            
            # 실제값 입력
            if has_prediction:
                st.markdown("---")
                st.markdown("#### 실제값 선택")
                
                col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
                with col_btn1:
                    if st.button("🔴 B", use_container_width=True, key=f"live_game_btn_b_{current_step}"):
                        actual_value = 'b'
                        
                        # 검증 수행
                        is_correct = predicted_value == actual_value
                        
                        if not is_correct:
                            game_state['consecutive_failures'] += 1
                            game_state['total_failures'] += 1
                            if game_state['consecutive_failures'] > game_state['max_consecutive_failures']:
                                game_state['max_consecutive_failures'] = game_state['consecutive_failures']
                        else:
                            game_state['consecutive_failures'] = 0
                        
                        game_state['total_predictions'] += 1
                        
                        # grid_string 업데이트 (실제값 추가)
                        new_grid_string = grid_string + actual_value
                        
                        # 앵커 재계산
                        new_anchors = update_anchors_after_input(new_grid_string)
                        
                        # 히스토리 기록
                        game_state['history'].append({
                            'step': current_step + 1,
                            'position': next_position,
                            'anchor': anchor,
                            'window_size': window_size,
                            'prefix': prefix,
                            'predicted': predicted_value,
                            'actual': actual_value,
                            'is_correct': is_correct,
                            'confidence': confidence,
                            'skipped': False,
                            'validated': True,
                            'interval': 0,
                            'debug_info': debug_info
                        })
                        
                        # 게임 상태 업데이트
                        game_state['grid_string'] = new_grid_string
                        game_state['anchors'] = new_anchors
                        game_state['current_position'] = len(new_grid_string)  # 다음 예측할 포지션 = 새로운 grid_string 길이
                        game_state['current_step'] += 1
                        game_state['current_index'] = len(new_grid_string) - 1
                        game_state['total_steps'] += 1
                        
                        st.rerun()
                
                with col_btn2:
                    if st.button("🔵 P", use_container_width=True, key=f"live_game_btn_p_{current_step}"):
                        actual_value = 'p'
                        
                        # 검증 수행
                        is_correct = predicted_value == actual_value
                        
                        if not is_correct:
                            game_state['consecutive_failures'] += 1
                            game_state['total_failures'] += 1
                            if game_state['consecutive_failures'] > game_state['max_consecutive_failures']:
                                game_state['max_consecutive_failures'] = game_state['consecutive_failures']
                        else:
                            game_state['consecutive_failures'] = 0
                        
                        game_state['total_predictions'] += 1
                        
                        # grid_string 업데이트 (실제값 추가)
                        new_grid_string = grid_string + actual_value
                        
                        # 앵커 재계산
                        new_anchors = update_anchors_after_input(new_grid_string)
                        
                        # 히스토리 기록
                        game_state['history'].append({
                            'step': current_step + 1,
                            'position': next_position,
                            'anchor': anchor,
                            'window_size': window_size,
                            'prefix': prefix,
                            'predicted': predicted_value,
                            'actual': actual_value,
                            'is_correct': is_correct,
                            'confidence': confidence,
                            'skipped': False,
                            'validated': True,
                            'interval': 0,
                            'debug_info': debug_info
                        })
                        
                        # 게임 상태 업데이트
                        game_state['grid_string'] = new_grid_string
                        game_state['anchors'] = new_anchors
                        game_state['current_position'] = len(new_grid_string)  # 다음 예측할 포지션 = 새로운 grid_string 길이
                        game_state['current_step'] += 1
                        game_state['current_index'] = len(new_grid_string) - 1
                        game_state['total_steps'] += 1
                        
                        st.rerun()
                
                with col_btn3:
                    if st.button("↩️ 취소", use_container_width=True, key=f"live_game_btn_cancel_{current_step}", disabled=len(game_state['history']) == 0):
                        if len(game_state['history']) > 0:
                            # 마지막 히스토리 항목 제거
                            last_entry = game_state['history'].pop()
                            
                            # grid_string 복원 (마지막 문자 제거)
                            if len(game_state['grid_string']) > 0:
                                game_state['grid_string'] = game_state['grid_string'][:-1]
                            
                            # 앵커 재계산
                            game_state['anchors'] = update_anchors_after_input(game_state['grid_string'])
                            
                            # 스텝 번호 감소
                            game_state['current_step'] = max(0, current_step - 1)
                            game_state['current_index'] = max(0, len(game_state['grid_string']) - 1)
                            game_state['current_position'] = len(game_state['grid_string'])  # 다음 예측할 포지션 = 복원된 grid_string 길이
                            
                            # 통계 복원
                            if last_entry.get('validated', False) and last_entry.get('is_correct') is not None:
                                game_state['total_predictions'] = max(0, game_state['total_predictions'] - 1)
                                if last_entry.get('is_correct') is False:
                                    game_state['total_failures'] = max(0, game_state['total_failures'] - 1)
                                    game_state['consecutive_failures'] = max(0, game_state['consecutive_failures'] - 1)
                                else:
                                    game_state['consecutive_failures'] = 0
                            
                            st.rerun()
                        else:
                            st.error("⚠️ 취소할 이전 상태가 없습니다.")
            elif skipped:
                # 스킵 상태
                st.markdown("---")
                st.markdown("#### 실제값 선택 (스킵 모드)")
                
                col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
                with col_btn1:
                    if st.button("🔴 B", use_container_width=True, key=f"live_game_btn_skip_b_{current_step}"):
                        actual_value = 'b'
                        
                        # grid_string 업데이트
                        new_grid_string = grid_string + actual_value
                        new_anchors = update_anchors_after_input(new_grid_string)
                        
                        # 히스토리 기록 (스킵)
                        game_state['history'].append({
                            'step': current_step + 1,
                            'position': next_position,
                            'anchor': anchor,
                            'window_size': window_size,
                            'prefix': prefix,
                            'predicted': predicted_value,
                            'actual': actual_value,
                            'is_correct': None,
                            'confidence': confidence,
                            'skipped': True,
                            'validated': False,
                            'interval': 0,
                            'debug_info': debug_info
                        })
                        
                        game_state['total_skipped'] += 1
                        game_state['grid_string'] = new_grid_string
                        game_state['anchors'] = new_anchors
                        game_state['current_position'] = len(new_grid_string)  # 다음 예측할 포지션 = 새로운 grid_string 길이
                        game_state['current_step'] += 1
                        game_state['current_index'] = len(new_grid_string) - 1
                        game_state['total_steps'] += 1
                        
                        st.rerun()
                
                with col_btn2:
                    if st.button("🔵 P", use_container_width=True, key=f"live_game_btn_skip_p_{current_step}"):
                        actual_value = 'p'
                        
                        # grid_string 업데이트
                        new_grid_string = grid_string + actual_value
                        new_anchors = update_anchors_after_input(new_grid_string)
                        
                        # 히스토리 기록 (스킵)
                        game_state['history'].append({
                            'step': current_step + 1,
                            'position': next_position,
                            'anchor': anchor,
                            'window_size': window_size,
                            'prefix': prefix,
                            'predicted': predicted_value,
                            'actual': actual_value,
                            'is_correct': None,
                            'confidence': confidence,
                            'skipped': True,
                            'validated': False,
                            'interval': 0,
                            'debug_info': debug_info
                        })
                        
                        game_state['total_skipped'] += 1
                        game_state['grid_string'] = new_grid_string
                        game_state['anchors'] = new_anchors
                        game_state['current_position'] = len(new_grid_string)  # 다음 예측할 포지션 = 새로운 grid_string 길이
                        game_state['current_step'] += 1
                        game_state['current_index'] = len(new_grid_string) - 1
                        game_state['total_steps'] += 1
                        
                        st.rerun()
                
                with col_btn3:
                    if st.button("↩️ 취소", use_container_width=True, key=f"live_game_btn_skip_cancel_{current_step}", disabled=len(game_state['history']) == 0):
                        if len(game_state['history']) > 0:
                            last_entry = game_state['history'].pop()
                            
                            if len(game_state['grid_string']) > 0:
                                game_state['grid_string'] = game_state['grid_string'][:-1]
                            
                            game_state['anchors'] = update_anchors_after_input(game_state['grid_string'])
                            game_state['current_step'] = max(0, current_step - 1)
                            game_state['current_index'] = max(0, len(game_state['grid_string']) - 1)
                            game_state['current_position'] = len(game_state['grid_string'])  # 다음 예측할 포지션 = 복원된 grid_string 길이
                            
                            if last_entry.get('skipped', False):
                                game_state['total_skipped'] = max(0, game_state['total_skipped'] - 1)
                            
                            st.rerun()
                        else:
                            st.error("⚠️ 취소할 이전 상태가 없습니다.")
            else:
                # 예측값 없음
                st.markdown("---")
                st.markdown("#### 실제값 선택 (예측값 없음)")
                
                col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
                with col_btn1:
                    if st.button("🔴 B", use_container_width=True, key=f"live_game_btn_no_pred_b_{current_step}"):
                        actual_value = 'b'
                        
                        new_grid_string = grid_string + actual_value
                        new_anchors = update_anchors_after_input(new_grid_string)
                        
                        game_state['history'].append({
                            'step': current_step + 1,
                            'position': next_position,
                            'anchor': None,
                            'window_size': None,
                            'prefix': '',
                            'predicted': None,
                            'actual': actual_value,
                            'is_correct': None,
                            'confidence': 0.0,
                            'skipped': False,
                            'validated': False,
                            'interval': 0,
                            'debug_info': debug_info
                        })
                        
                        game_state['grid_string'] = new_grid_string
                        game_state['anchors'] = new_anchors
                        game_state['current_position'] = len(new_grid_string)  # 다음 예측할 포지션 = 새로운 grid_string 길이
                        game_state['current_step'] += 1
                        game_state['current_index'] = len(new_grid_string) - 1
                        game_state['total_steps'] += 1
                        
                        st.rerun()
                
                with col_btn2:
                    if st.button("🔵 P", use_container_width=True, key=f"live_game_btn_no_pred_p_{current_step}"):
                        actual_value = 'p'
                        
                        new_grid_string = grid_string + actual_value
                        new_anchors = update_anchors_after_input(new_grid_string)
                        
                        game_state['history'].append({
                            'step': current_step + 1,
                            'position': next_position,
                            'anchor': None,
                            'window_size': None,
                            'prefix': '',
                            'predicted': None,
                            'actual': actual_value,
                            'is_correct': None,
                            'confidence': 0.0,
                            'skipped': False,
                            'validated': False,
                            'interval': 0,
                            'debug_info': debug_info
                        })
                        
                        game_state['grid_string'] = new_grid_string
                        game_state['anchors'] = new_anchors
                        game_state['current_position'] = len(new_grid_string)  # 다음 예측할 포지션 = 새로운 grid_string 길이
                        game_state['current_step'] += 1
                        game_state['current_index'] = len(new_grid_string) - 1
                        game_state['total_steps'] += 1
                        
                        st.rerun()
                
                with col_btn3:
                    if st.button("↩️ 취소", use_container_width=True, key=f"live_game_btn_no_pred_cancel_{current_step}", disabled=len(game_state['history']) == 0):
                        if len(game_state['history']) > 0:
                            last_entry = game_state['history'].pop()
                            
                            if len(game_state['grid_string']) > 0:
                                game_state['grid_string'] = game_state['grid_string'][:-1]
                            
                            game_state['anchors'] = update_anchors_after_input(game_state['grid_string'])
                            game_state['current_step'] = max(0, current_step - 1)
                            game_state['current_index'] = max(0, len(game_state['grid_string']) - 1)
                            game_state['current_position'] = len(game_state['grid_string'])  # 다음 예측할 포지션 = 복원된 grid_string 길이
                            
                            st.rerun()
                        else:
                            st.error("⚠️ 취소할 이전 상태가 없습니다.")
            
            # 상세 히스토리 표시
            if game_state['history']:
                st.markdown("---")
                with st.expander("📊 상세 히스토리", expanded=True):
                    history_data = []
                    history_sorted = sorted(game_state['history'], key=lambda x: x.get('step', 0), reverse=True)
                    
                    for entry in history_sorted:
                        is_correct = entry.get('is_correct')
                        match_status = '✅' if is_correct else ('❌' if is_correct is False else '-')
                        predicted = entry.get('predicted')
                        skipped = entry.get('skipped', False)
                        skipped_mark = '⏭️' if skipped else ''
                        predicted_display = f"{predicted}{skipped_mark}" if predicted else f"-{skipped_mark}" if skipped else "-"
                        
                        history_data.append({
                            'Step': entry.get('step', 0),
                            'Prefix': entry.get('prefix', ''),
                            '예측': predicted_display,
                            '실제값': entry.get('actual', '-'),
                            '일치': match_status,
                            '검증': '✓' if entry.get('validated', False) else '',
                            '신뢰도': f"{entry.get('confidence', 0):.1f}%" if predicted else '-',
                            '간격': entry.get('interval', 0)
                        })
                    
                    if len(history_data) > 0:
                        history_df = pd.DataFrame(history_data)
                        st.dataframe(history_df, use_container_width=True, hide_index=True)
                        st.caption(f"💡 전체 {len(game_state['history'])}개 히스토리가 표시됩니다.")
            
            # 디버깅 정보 표시 (이전 히스토리에 대한 앵커 선택 디버깅 정보)
            if game_state['history']:
                st.markdown("---")
                with st.expander("🔍 디버깅 정보 (이전 히스토리)", expanded=True):
                    # 히스토리에서 디버깅 정보가 있는 모든 항목 표시
                    debug_entries = []
                    for entry in game_state['history']:
                        debug_info = entry.get('debug_info')
                        if debug_info and debug_info.get('all_attempts'):
                            debug_entries.append({
                                'step': entry.get('step', 0),
                                'position': entry.get('position', 0),
                                'debug_info': debug_info
                            })
                    
                    if debug_entries:
                        # 각 히스토리 항목별로 디버깅 정보 표시
                        for debug_entry in debug_entries:
                            step = debug_entry['step']
                            position = debug_entry['position']
                            debug_info = debug_entry['debug_info']
                            
                            st.markdown(f"#### Step {step} - Position {position}")
                            
                            debug_data = []
                            for attempt in debug_info.get('all_attempts', []):
                                # prefix 정보 추가
                                anchor = attempt.get('anchor', '')
                                window_size = attempt.get('window_size', '')
                                prefix_display = '-'
                                if anchor != '' and window_size != '' and position is not None:
                                    try:
                                        # 이전 히스토리의 경우 초기 grid_string 사용
                                        initial_grid_string = game_state.get('initial_grid_string', grid_string)
                                        prefix_len = window_size - 1
                                        if position >= prefix_len and position <= len(initial_grid_string):
                                            prefix_display = initial_grid_string[position - prefix_len : position]
                                    except:
                                        pass
                                
                                debug_data.append({
                                    'Position': position,
                                    '앵커': anchor,
                                    '윈도우': window_size,
                                    'Prefix': prefix_display,
                                    '예측값': attempt.get('predicted', '-'),
                                    '신뢰도': f"{attempt.get('confidence', 0):.1f}%" if attempt.get('confidence', 0) > 0 else '-',
                                    '탈락 사유 (하위 순위)': attempt.get('rejection_reason', '-')
                                })
                            
                            if len(debug_data) > 0:
                                debug_df = pd.DataFrame(debug_data)
                                st.dataframe(debug_df, use_container_width=True, hide_index=True)
                            
                            # 선택된 앵커 정보 표시
                            selected_anchor = debug_info.get('selected_anchor')
                            if selected_anchor is not None:
                                st.info(f"✅ **선택된 앵커: {selected_anchor}** (Position {position}에서 사용)")
                            
                            st.markdown("---")  # 항목 구분선
                    else:
                        st.info("디버깅 정보가 없습니다. 예측을 수행한 후 표시됩니다.")
            
            if st.button("🛑 게임 중단", use_container_width=True, key="stop_game"):
                st.session_state.change_point_game_state = None
                st.rerun()


if __name__ == "__main__":
    main()
