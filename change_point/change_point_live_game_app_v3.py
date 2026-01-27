"""
V3 라이브 게임 앱
Change-point Detection 기반 V3 검증 로직을 사용하는 라이브 게임

- 첫 번째 앵커부터 검증 시작
- 앵커 기반 순차 검증 시스템
- 윈도우 크기 9, 10, 11, 12, 13, 14 순차 검증
- 적중 시 즉시 종료, 3회 연속 불일치 시 다음 앵커로
- simulation_predictions_change_point 테이블 사용
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
    page_title="Change-point V3 라이브 게임",
    page_icon="🎮",
    layout="wide"
)

from svg_parser_module import get_change_point_db_connection
from change_point_prediction_module import (
    load_preprocessed_grid_strings_cp,
    get_stored_predictions_change_point_count,
)
from change_point_hypothesis_module import (
    generate_simulation_predictions_table,
)
import pandas as pd


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


def update_anchors_after_input(grid_string):
    """
    실제값 입력 후 앵커 재계산
    """
    return detect_change_points(grid_string)


def validate_grid_string_v3(
    grid_string,
    window_sizes=(9, 10, 11, 12, 13, 14),
    method="빈도 기반",
    threshold=0,
):
    """
    Grid String을 직접 받아서 V3 검증 수행 (라이브게임용)
    
    Args:
        grid_string: 검증할 grid_string
        window_sizes: 윈도우 크기 목록
        method: 예측 방법
        threshold: 임계값
        
    Returns:
        dict: 검증 결과 (history, 통계 등)
    """
    conn = get_change_point_db_connection()
    try:
        max_ws = max(window_sizes)
        if len(grid_string) < max_ws:
            return {
                "max_consecutive_failures": 0,
                "total_steps": 0,
                "total_failures": 0,
                "total_predictions": 0,
                "total_skipped": 0,
                "accuracy": 0.0,
                "history": [],
            }
        
        # Change-point Detection: 앵커 위치 수집
        anchors = detect_change_points(grid_string)
        
        if not anchors:
            return {
                "max_consecutive_failures": 0,
                "total_steps": 0,
                "total_failures": 0,
                "total_predictions": 0,
                "total_skipped": 0,
                "accuracy": 0.0,
                "history": [],
            }
        
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0
        MAX_CONSECUTIVE_FAILURES = 3
        
        # 첫 번째 앵커부터 검증 시작
        current_pos = 0
        anchor_idx = 0
        final_anchor_consecutive_failures = 0  # 검증 종료 시 앵커별 연속 실패 (다음 예측용)
        
        while current_pos < len(grid_string) and anchor_idx < len(anchors):
            # [REQ-101] current_pos 이후의 가장 빠른 앵커 찾기
            while anchor_idx < len(anchors) and anchors[anchor_idx] < current_pos:
                anchor_idx += 1
            
            # 더 이상 검증할 앵커가 없으면 종료
            if anchor_idx >= len(anchors):
                break
            
            next_anchor = anchors[anchor_idx]
            
            # 해당 앵커에서 윈도우 크기별 순차 검증
            anchor_consecutive_failures = 0
            anchor_success = False
            last_mismatched_pos = None
            anchor_processed_any = False
            
            # [REQ-102] 윈도우 크기 9, 10, 11, 12, 13, 14 순차 검증
            exit_while_for_pos_beyond = False  # pos >= len(grid_string)로 for 탈출 시 while도 종료
            for window_size in window_sizes:
                # 앵커 위치에서 window_size만큼 추출 가능한지 확인
                pos = next_anchor + window_size - 1
                if pos >= len(grid_string):
                    # 범위를 벗어남: 같은 앵커에서 아직 연속 불일치 1~2번인 상태 보존 (현재 스텝 예측용)
                    final_anchor_consecutive_failures = anchor_consecutive_failures
                    exit_while_for_pos_beyond = True
                    break  # 범위를 벗어나면 더 큰 윈도우는 시도하지 않음
                
                # current_pos보다 이전 포지션이면 건너뛰기
                if pos < current_pos:
                    continue
                
                total_steps += 1
                actual = grid_string[pos]
                
                # prefix 계산
                prefix_len = window_size - 1
                prefix = grid_string[pos - prefix_len : pos]
                
                # DB에서 예측값 조회 (시뮬레이션 전용 테이블 사용)
                q = """
                    SELECT predicted_value, confidence, b_ratio, p_ratio
                    FROM simulation_predictions_change_point
                    WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                    LIMIT 1
                """
                df_pred = pd.read_sql_query(q, conn, params=[window_size, prefix, method, threshold])
                
                if len(df_pred) == 0:
                    # 예측값이 없으면 스킵 (연속 실패 카운트에 포함하지 않음)
                    total_skipped += 1
                    history.append({
                        "step": total_steps,
                        "position": pos,
                        "anchor": next_anchor,
                        "window_size": window_size,
                        "prefix": prefix,
                        "predicted": None,
                        "actual": actual,
                        "is_correct": None,
                        "confidence": 0.0,
                        "skipped": True,
                        "skip_reason": "예측 테이블에 값 없음",
                    })
                    continue  # 스킵해도 계속 진행
                
                # 예측값이 있는 경우 처리
                anchor_processed_any = True
                row = df_pred.iloc[0]
                predicted = row["predicted_value"]
                confidence = row["confidence"]
                
                # 예측 결과 비교
                ok = predicted == actual
                total_predictions += 1
                
                if not ok:
                    consecutive_failures += 1
                    anchor_consecutive_failures += 1
                    total_failures += 1
                    last_mismatched_pos = pos
                    
                    if consecutive_failures > max_consecutive_failures:
                        max_consecutive_failures = consecutive_failures
                else:
                    consecutive_failures = 0
                    # [RULE-1] 적중 시 즉시 종료
                    anchor_success = True
                    anchor_consecutive_failures = 0
                
                history.append({
                    "step": total_steps,
                    "position": pos,
                    "anchor": next_anchor,
                    "window_size": window_size,
                    "prefix": prefix,
                    "predicted": predicted,
                    "actual": actual,
                    "is_correct": ok,
                    "confidence": confidence,
                    "skipped": False,
                })
                
                # [RULE-1] 적중 시 즉시 종료하고 다음 앵커 탐색
                if ok:
                    current_pos = pos + 1
                    anchor_idx += 1  # 다음 앵커로
                    final_anchor_consecutive_failures = 0  # 다음 앵커이므로 0
                    break  # 현재 앵커 검증 종료
                
                # [RULE-2] 3회 연속 불일치 발생 시 해당 앵커 검증 실패로 종료
                if anchor_consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    if last_mismatched_pos is not None:
                        current_pos = last_mismatched_pos + 1
                    else:
                        current_pos = pos + 1
                    anchor_idx += 1  # 다음 앵커로
                    final_anchor_consecutive_failures = 0  # 다음 앵커이므로 0
                    break  # 현재 앵커 검증 종료
            
            if exit_while_for_pos_beyond:
                break  # pos >= len(grid_string)으로 끝난 경우, 앵커/연속실패 상태 유지하고 검증 종료 (fallback 건너뜀)
            
            # 윈도우 크기 루프가 끝났는데 current_pos가 업데이트되지 않은 경우
            if not anchor_success and anchor_consecutive_failures < MAX_CONSECUTIVE_FAILURES:
                if anchor_processed_any and last_mismatched_pos is not None:
                    current_pos = last_mismatched_pos + 1
                elif anchor_processed_any:
                    max_pos = min(next_anchor + max(window_sizes) - 1, len(grid_string) - 1)
                    current_pos = max_pos + 1
                else:
                    # 모든 윈도우가 스킵되었거나 범위를 벗어남
                    max_pos = min(next_anchor + max(window_sizes) - 1, len(grid_string) - 1)
                    current_pos = max_pos + 1
                anchor_idx += 1
                final_anchor_consecutive_failures = 0  # 다음 앵커로 이동했으므로 0
        
        accuracy = (total_predictions - total_failures) / total_predictions if total_predictions > 0 else 0.0
        
        return {
            "max_consecutive_failures": max_consecutive_failures,
            "total_steps": total_steps,
            "total_failures": total_failures,
            "total_predictions": total_predictions,
            "total_skipped": total_skipped,
            "accuracy": accuracy,
            "history": history,
            "final_current_pos": current_pos,
            "final_anchor_idx": anchor_idx,
            "final_anchor_consecutive_failures": final_anchor_consecutive_failures,
        }
    finally:
        conn.close()


def predict_for_position_v3(
    grid_string,
    position,
    anchors,
    window_sizes,
    method,
    threshold,
    current_pos,
    anchor_idx,
    anchor_consecutive_failures,
):
    """
    V3 검증 로직을 사용하여 특정 position에서 예측 수행
    
    라이브게임에서는 매 스텝마다 다음 position을 예측하므로,
    V3 규칙에 따라 current_pos 이후의 가장 빠른 앵커에서
    윈도우 크기별로 순차 검증하여 첫 번째 사용 가능한 예측값을 반환합니다.
    
    Args:
        grid_string: 전체 grid string
        position: 예측할 position (grid_string의 길이, 다음 입력할 위치)
        anchors: 앵커 위치 리스트
        window_sizes: 윈도우 크기 목록 (9, 10, 11, 12, 13, 14)
        method: 예측 방법
        threshold: 임계값
        current_pos: 현재 검증 포지션
        anchor_idx: 현재 앵커 인덱스
        anchor_consecutive_failures: 현재 앵커에서의 연속 실패 횟수
        
    Returns:
        dict: 예측 결과 및 상태 정보
    """
    # current_pos 이후의 가장 빠른 앵커 찾기
    while anchor_idx < len(anchors) and anchors[anchor_idx] < current_pos:
        anchor_idx += 1
    
    # 더 이상 검증할 앵커가 없으면 예측 불가
    if anchor_idx >= len(anchors):
        return {
            "predicted": None,
            "confidence": 0.0,
            "window_size": None,
            "prefix": None,
            "anchor": None,
            "skipped": True,
            "current_pos": current_pos,
            "anchor_idx": anchor_idx,
            "anchor_consecutive_failures": anchor_consecutive_failures,
            "debug_info": {
                "position": position,
                "selected_anchor": None,
                "selected_window": None,
                "all_attempts": []
            }
        }
    
    next_anchor = anchors[anchor_idx]
    
    # 해당 앵커에서 윈도우 크기별 순차 검증
    all_attempts = []
    selected_result = None
    selected_window = None
    
    conn = get_change_point_db_connection()
    try:
        # [REQ-102] 윈도우 크기 9, 10, 11, 12, 13, 14 순차 검증
        for window_size in window_sizes:
            # 앵커 위치에서 window_size만큼 추출 가능한지 확인
            # position = next_anchor + window_size - 1 이어야 함
            required_pos = next_anchor + window_size - 1
            
            # 라이브게임에서는 position = len(grid_string) (다음 입력할 위치)
            # 정확히 일치해야 예측 가능
            if required_pos != position:
                # 이 앵커-윈도우 조합으로는 현재 position에 도달할 수 없음
                # (다음 윈도우 크기로 계속 시도)
                continue
            
            # current_pos보다 이전 포지션이면 건너뛰기 (이미 검증한 포지션)
            if required_pos < current_pos:
                continue
            
            # prefix 계산 (position 이전의 window_size-1 길이)
            prefix_len = window_size - 1
            if position < prefix_len:
                continue
            prefix = grid_string[position - prefix_len : position]
            
            # DB에서 예측값 조회 (시뮬레이션 전용 테이블 사용)
            q = """
                SELECT predicted_value, confidence, b_ratio, p_ratio
                FROM simulation_predictions_change_point
                WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                LIMIT 1
            """
            df_pred = pd.read_sql_query(q, conn, params=[window_size, prefix, method, threshold])
            
            if len(df_pred) == 0:
                # 예측값이 없으면 스킵 (연속 실패 카운트에 포함하지 않음)
                all_attempts.append({
                    "anchor": next_anchor,
                    "window_size": window_size,
                    "predicted": None,
                    "confidence": 0.0,
                    "skipped": True,
                    "rejection_reason": "예측 테이블에 값 없음"
                })
                continue  # 스킵해도 계속 진행
            
            # 예측값이 있는 경우 처리
            row = df_pred.iloc[0]
            predicted = row["predicted_value"]
            confidence = row["confidence"]
            
            all_attempts.append({
                "anchor": next_anchor,
                "window_size": window_size,
                "predicted": predicted,
                "confidence": confidence,
                "skipped": False,
                "rejection_reason": None
            })
            
            # 첫 번째 사용 가능한 예측값을 선택 (V3 로직: 순차적으로 검증하되, 라이브게임에서는 첫 번째 사용)
            if selected_result is None:
                selected_result = {
                    "predicted": predicted,
                    "confidence": confidence,
                    "window_size": window_size,
                    "prefix": prefix,
                    "anchor": next_anchor,
                    "b_ratio": row["b_ratio"],
                    "p_ratio": row["p_ratio"],
                }
                selected_window = window_size
                # 선택된 예측값 표시 업데이트
                for attempt in all_attempts:
                    if attempt["anchor"] == next_anchor and attempt["window_size"] == window_size:
                        attempt["rejection_reason"] = "선택됨"
                
                # V3 로직: 첫 번째 사용 가능한 예측값을 찾으면 중단 (라이브게임에서는 즉시 사용)
                break
    finally:
        conn.close()
    
    # 선택된 결과가 없으면 모든 시도가 실패
    if selected_result is None:
        return {
            "predicted": None,
            "confidence": 0.0,
            "window_size": None,
            "prefix": None,
            "anchor": None,
            "skipped": True,
            "current_pos": current_pos,
            "anchor_idx": anchor_idx,
            "anchor_consecutive_failures": anchor_consecutive_failures,
            "debug_info": {
                "position": position,
                "selected_anchor": None,
                "selected_window": None,
                "all_attempts": all_attempts
            }
        }
    
    return {
        **selected_result,
        "skipped": False,
        "current_pos": current_pos,
        "anchor_idx": anchor_idx,
        "anchor_consecutive_failures": anchor_consecutive_failures,
        "debug_info": {
            "position": position,
            "selected_anchor": next_anchor,
            "selected_window": selected_window,
            "selected_prediction": selected_result["predicted"],
            "selected_confidence": selected_result["confidence"],
            "all_attempts": all_attempts
        }
    }


def build_completed_validation_history_table(history):
    """
    완료된 검증(이전 히스토리)을 hypothesis_test_app 시뮬레이션 결과와 동일한 형식의
    상세 히스토리 테이블 데이터로 변환합니다.
    
    Args:
        history: validate_grid_string_v3 결과의 history 리스트
        
    Returns:
        list[dict]: st.dataframe에 넣을 수 있는 행 리스트 (컬럼: Step, Position, Anchor, Window Size, Prefix, 예측, 실제값, 일치, 신뢰도, 스킵 사유)
    """
    rows = []
    for entry in history or []:
        is_correct = entry.get("is_correct")
        match_status = "✅" if is_correct else ("❌" if is_correct is False else "-")
        predicted = entry.get("predicted")
        skipped = entry.get("skipped", False)
        skip_reason = entry.get("skip_reason", "")
        skipped_mark = "⏭️" if skipped else ""
        if skipped and skip_reason:
            skipped_mark = f"⏭️ ({skip_reason})"
        predicted_display = f"{predicted}{skipped_mark}" if predicted else f"-{skipped_mark}" if skipped else "-"
        rows.append({
            "Step": entry.get("step", 0),
            "Position": entry.get("position", ""),
            "Anchor": entry.get("anchor", ""),
            "Window Size": entry.get("window_size", ""),
            "Prefix": entry.get("prefix", ""),
            "예측": predicted_display,
            "실제값": entry.get("actual", "-"),
            "일치": match_status,
            "신뢰도": f"{entry.get('confidence', 0):.1f}%" if predicted else "-",
            "스킵 사유": skip_reason if skipped else "",
        })
    return rows


def render_completed_validation_history_section(game_state):
    """
    게임 진행 영역 상단에 '완료된 검증 결과(이전 히스토리)'를
    hypothesis_test_app 시뮬레이션 결과와 같은 방식으로 상세 테이블로 표시합니다.
    """
    validation_done = game_state.get("validation_completed", False)
    history = game_state.get("history") or []
    if not validation_done or not history:
        return
    st.markdown("### ✅ 완료된 검증 결과 (이전 히스토리)")
    rows = build_completed_validation_history_table(history)
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"💡 게임 시작 시 수행한 전체 검증: {len(history)}개 스텝")
    else:
        st.info("히스토리 데이터가 없습니다.")


def main():
    st.title("🎮 Change-point V3 라이브 게임")
    st.markdown("**V3 검증 로직 기반 라이브 게임: 첫 번째 앵커부터 검증, 적중 시 즉시 종료, 3회 연속 불일치 시 다음 앵커로**")
    
    # 시뮬레이션 예측값 테이블 확인
    conn = get_change_point_db_connection()
    try:
        df_check = pd.read_sql_query(
            "SELECT COUNT(*) as cnt FROM simulation_predictions_change_point",
            conn
        )
        n_sim_predictions = int(df_check.iloc[0]["cnt"]) if len(df_check) > 0 else 0
    except:
        n_sim_predictions = 0
    finally:
        conn.close()
    
    if n_sim_predictions == 0:
        st.warning("⚠️ simulation_predictions_change_point가 비어 있습니다. 예측값 테이블을 먼저 생성하세요.")
    
    # 게임 상태 초기화
    if 'v3_game_state' not in st.session_state:
        st.session_state.v3_game_state = None
    
    # 게임 설정
    st.markdown("---")
    st.markdown("## ⚙️ 게임 설정")
    
    df_mw = load_preprocessed_grid_strings_cp()
    if len(df_mw) == 0:
        st.warning("preprocessed_grid_strings에 데이터가 없습니다.")
        return
    
    col1, col2, col3 = st.columns(3)
    with col1:
        method = st.selectbox("예측 방법", ["빈도 기반", "가중치 기반", "안전 우선"], key="v3_method")
    with col2:
        threshold = st.number_input("임계값", 0, 100, 0, key="v3_threshold")
    with col3:
        cutoff_id = st.selectbox(
            "Cutoff ID (예측값 생성 기준)",
            [None] + df_mw["id"].tolist(),
            format_func=lambda x: "선택 안 함" if x is None else f"ID {x}",
            key="v3_cutoff"
        )
    
    # V3 전용: 예측값 테이블 생성 버튼
    st.markdown("---")
    st.markdown("#### 🔧 V3 시뮬레이션 예측값 테이블 생성")
    st.info("💡 V3 라이브 게임을 실행하기 전에 먼저 예측값 테이블을 생성해야 합니다.")
    
    window_sizes_v3 = [9, 10, 11, 12, 13, 14]
    
    col_gen1, col_gen2 = st.columns([1, 4])
    with col_gen1:
        if st.button("예측값 테이블 생성", key="generate_v3_live_predictions", type="secondary"):
            if cutoff_id is None:
                st.warning("Cutoff ID를 선택하세요.")
            else:
                with st.spinner("예측값 테이블 생성 중... (시간이 소요될 수 있습니다)"):
                    try:
                        result = generate_simulation_predictions_table(
                            cutoff_grid_string_id=cutoff_id,
                            window_sizes=tuple(window_sizes_v3),
                            method=method,
                            threshold=threshold,
                        )
                        st.success(f"✅ 예측값 테이블 생성 완료! (저장된 레코드: {result.get('total_saved', 0):,}개)")
                        st.session_state["v3_predictions_generated"] = True
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ 예측값 테이블 생성 실패: {str(e)}")
                        st.session_state["v3_predictions_generated"] = False
    
    with col_gen2:
        if n_sim_predictions > 0:
            st.success(f"✅ 예측값 테이블에 {n_sim_predictions:,}개 레코드가 있습니다.")
        else:
            st.info("예측값 테이블을 생성하세요.")
    
    # Grid String 입력
    st.markdown("---")
    st.markdown("## 📝 Grid String 입력")
    
    grid_string_input = st.text_area(
        "Grid String 입력",
        key="v3_live_game_grid_string",
        height=80,
        help="라이브 게임에서 사용할 grid_string을 입력하세요..."
    )
    
    # 게임 시작/재시작 버튼
    col_start1, col_start2 = st.columns([1, 4])
    with col_start1:
        if st.button("🎮 게임 시작", type="primary", use_container_width=True):
            if not grid_string_input or len(grid_string_input.strip()) == 0:
                st.warning("Grid String을 입력하세요.")
            elif n_sim_predictions == 0:
                st.warning("⚠️ 먼저 '예측값 테이블 생성' 버튼을 클릭하여 예측값 테이블을 생성하세요.")
            elif cutoff_id is None:
                st.warning("Cutoff ID를 선택하세요.")
            elif len(grid_string_input.strip()) < min(window_sizes_v3) - 1:
                # 최소 길이 계산:
                # - 첫 번째 앵커 = 0
                # - 최소 윈도우 크기 = 9
                # - position = anchor + window_size - 1 = 0 + 9 - 1 = 8
                # - prefix = grid_string[0:8] (길이 8)
                # - 따라서 grid_string의 최소 길이는 8
                st.warning(f"Grid String 길이가 최소 {min(window_sizes_v3) - 1} 이상이어야 합니다. (첫 번째 앵커=0, 최소 윈도우=9일 때 position=8이므로 prefix 길이 8 필요)")
            else:
                grid_string = grid_string_input.strip()
                
                # Change-point Detection: 앵커 위치 수집
                anchors = detect_change_points(grid_string)
                
                if not anchors:
                    st.warning("Change-point가 감지되지 않았습니다.")
                else:
                    # 게임 시작 시 전체 grid_string에 대해 V3 검증 수행
                    with st.spinner("전체 grid_string 검증 중..."):
                        validation_result = validate_grid_string_v3(
                            grid_string=grid_string,
                            window_sizes=tuple(window_sizes_v3),
                            method=method,
                            threshold=threshold,
                        )
                    
                    # V3 게임 상태 초기화 (검증 결과 포함)
                    initial_history_count = len(validation_result['history'])
                    st.session_state.v3_game_state = {
                        'grid_string': grid_string,
                        'anchors': anchors,
                        'window_sizes': window_sizes_v3,
                        'method': method,
                        'threshold': threshold,
                        'cutoff_id': cutoff_id,
                        'current_step': initial_history_count,  # 검증 완료된 스텝 수
                        'current_position': len(grid_string),  # 다음 예측할 포지션 (라이브게임 시작 위치)
                        'current_pos': validation_result.get('final_current_pos', len(grid_string)),  # 검증 완료 후 current_pos
                        'anchor_idx': validation_result.get('final_anchor_idx', len(anchors)),  # 검증 완료 후 anchor_idx
                        'anchor_consecutive_failures': validation_result.get('final_anchor_consecutive_failures', 0),  # 검증 종료 시 앵커별 연속 실패 (현재 스텝 예측용)
                        'total_steps': validation_result['total_steps'],
                        'total_predictions': validation_result['total_predictions'],
                        'total_failures': validation_result['total_failures'],
                        'total_skipped': validation_result['total_skipped'],
                        'consecutive_failures': 0,  # 라이브게임 시작 시 초기화
                        'max_consecutive_failures': validation_result['max_consecutive_failures'],
                        'history': validation_result['history'],  # 검증 결과 히스토리
                        'validation_completed': True,  # 검증 완료 플래그
                        'initial_history_count': initial_history_count,  # 초기 검증 히스토리 개수
                    }
                    st.rerun()
    
    with col_start2:
        if st.session_state.v3_game_state is not None:
            if st.button("🔄 게임 재시작", use_container_width=True):
                st.session_state.v3_game_state = None
                st.rerun()
    
    # 게임 진행
    if st.session_state.v3_game_state is not None:
        game_state = st.session_state.v3_game_state
        grid_string = game_state['grid_string']
        anchors = game_state['anchors']
        window_sizes = game_state['window_sizes']
        method = game_state['method']
        threshold = game_state['threshold']
        current_position = game_state['current_position']
        current_step = game_state['current_step']
        current_pos = game_state['current_pos']
        anchor_idx = game_state['anchor_idx']
        anchor_consecutive_failures = game_state['anchor_consecutive_failures']
        
        st.markdown("---")
        st.markdown("## 🎮 게임 진행")
        
        # ----- 새 영역: 완료된 검증 결과(상세 히스토리) - hypothesis_test_app 시뮬레이션 결과와 동일한 테이블 -----
        render_completed_validation_history_section(game_state)
        # ----- 새 영역 끝 -----
        
        # 검증 완료 여부 확인
        validation_completed = game_state.get('validation_completed', False)
        
        if validation_completed:
            st.success("✅ 전체 grid_string 검증이 완료되었습니다. 이제 라이브 게임 모드로 진행합니다.")
        
        # 현재 상태 표시
        col_stat1, col_stat2, col_stat3, col_stat4, col_stat5 = st.columns(5)
        with col_stat1:
            st.metric("현재 Step", current_step + 1)
        with col_stat2:
            st.metric("최대 연속 불일치", game_state['max_consecutive_failures'])
        with col_stat3:
            st.metric("총 예측 횟수", game_state['total_predictions'])
        with col_stat4:
            st.metric("스킵 횟수", game_state['total_skipped'])
        with col_stat5:
            st.metric("현재 앵커", f"{anchors[anchor_idx] if anchor_idx < len(anchors) else 'N/A'}")
        
        # Grid String 전체 표시 및 앵커 시각화
        st.markdown("---")
        st.markdown("### Grid String 및 앵커")
        
        # 다음 예측할 position은 grid_string의 길이
        next_position = len(grid_string)
        
        # 다음 position에서 예측 결과 가져오기
        pred_result = predict_for_position_v3(
            grid_string,
            next_position,
            anchors,
            window_sizes,
            method,
            threshold,
            current_pos,
            anchor_idx,
            anchor_consecutive_failures,
        )
        
        selected_anchor = pred_result.get("anchor")
        debug_info = pred_result.get("debug_info", {})
        
        render_grid_string_with_anchors(
            grid_string, 
            anchors, 
            next_position,
            debug_info=debug_info,
            selected_anchor=selected_anchor
        )
        st.caption("💡 연한 파란색: 앵커 위치, 노란색: 현재 예측할 position (grid_string 길이), 빨간색 테두리: 선택된 앵커")
        
        # 예측 수행
        st.markdown("---")
        st.markdown("### 📍 현재 스텝")
        
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
            
            # 현재 스텝 정보 표시
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
            
            # V3 디버깅 정보 표시
            if debug_info and debug_info.get('all_attempts'):
                with st.expander("🔍 V3 앵커/윈도우 시도 정보", expanded=False):
                    debug_data = []
                    for attempt in debug_info.get('all_attempts', []):
                        debug_data.append({
                            '앵커': attempt.get('anchor', ''),
                            '윈도우': attempt.get('window_size', ''),
                            '예측값': attempt.get('predicted', '-'),
                            '신뢰도': f"{attempt.get('confidence', 0):.1f}%" if attempt.get('confidence', 0) > 0 else '-',
                            '결과': attempt.get('rejection_reason', '-')
                        })
                    
                    if len(debug_data) > 0:
                        debug_df = pd.DataFrame(debug_data)
                        st.dataframe(debug_df, use_container_width=True, hide_index=True)
                        if selected_anchor is not None:
                            st.info(f"✅ **선택된 앵커: {selected_anchor}**, 윈도우: {window_size}")
            
            # 실제값 입력
            if has_prediction:
                st.markdown("---")
                st.markdown("#### 실제값 선택")
                
                col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
                with col_btn1:
                    if st.button("🔴 B", use_container_width=True, key=f"v3_live_game_btn_b_{current_step}"):
                        actual_value = 'b'
                        
                        # 검증 수행
                        is_correct = predicted_value == actual_value
                        
                        # V3 로직에 따른 상태 업데이트
                        new_current_pos = current_pos
                        new_anchor_idx = anchor_idx
                        new_anchor_consecutive_failures = anchor_consecutive_failures
                        
                        # grid_string 업데이트 (실제값 추가)
                        new_grid_string = grid_string + actual_value
                        
                        # 앵커 재계산
                        new_anchors = update_anchors_after_input(new_grid_string)
                        
                        if not is_correct:
                            # 불일치: 연속 실패 카운트 증가
                            game_state['consecutive_failures'] += 1
                            game_state['total_failures'] += 1
                            new_anchor_consecutive_failures += 1
                            
                            if game_state['consecutive_failures'] > game_state['max_consecutive_failures']:
                                game_state['max_consecutive_failures'] = game_state['consecutive_failures']
                            
                            # [RULE-2] 3회 연속 불일치 발생 시 다음 앵커로
                            if new_anchor_consecutive_failures >= 3:
                                # 현재 앵커 위치를 기준으로 다음 앵커 찾기
                                current_anchor_pos = anchor if anchor is not None else (anchors[anchor_idx] if anchor_idx < len(anchors) else -1)
                                # new_anchors에서 current_anchor_pos 이후의 첫 번째 앵커 찾기
                                next_anchor_idx_in_new = 0
                                for i, a in enumerate(new_anchors):
                                    if a > current_anchor_pos:
                                        next_anchor_idx_in_new = i
                                        break
                                    next_anchor_idx_in_new = i + 1
                                
                                if next_anchor_idx_in_new < len(new_anchors):
                                    next_anchor_pos = new_anchors[next_anchor_idx_in_new]
                                    min_window = min(window_sizes)
                                    new_current_pos = next_anchor_pos + min_window - 1
                                    new_anchor_idx = next_anchor_idx_in_new
                                else:
                                    new_current_pos = len(new_grid_string)
                                    new_anchor_idx = len(new_anchors)
                                new_anchor_consecutive_failures = 0
                        else:
                            # [RULE-1] 적중 시 즉시 종료하고 다음 앵커로
                            game_state['consecutive_failures'] = 0
                            # 현재 앵커 위치를 기준으로 다음 앵커 찾기
                            current_anchor_pos = anchor if anchor is not None else (anchors[anchor_idx] if anchor_idx < len(anchors) else -1)
                            # new_anchors에서 current_anchor_pos 이후의 첫 번째 앵커 찾기
                            next_anchor_idx_in_new = 0
                            for i, a in enumerate(new_anchors):
                                if a > current_anchor_pos:
                                    next_anchor_idx_in_new = i
                                    break
                                next_anchor_idx_in_new = i + 1
                            
                            if next_anchor_idx_in_new < len(new_anchors):
                                next_anchor_pos = new_anchors[next_anchor_idx_in_new]
                                min_window = min(window_sizes)
                                new_current_pos = next_anchor_pos + min_window - 1
                                new_anchor_idx = next_anchor_idx_in_new
                            else:
                                new_current_pos = len(new_grid_string)
                                new_anchor_idx = len(new_anchors)
                            new_anchor_consecutive_failures = 0
                        
                        game_state['total_predictions'] += 1
                        
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
                            'debug_info': debug_info
                        })
                        
                        # 게임 상태 업데이트
                        game_state['grid_string'] = new_grid_string
                        game_state['anchors'] = new_anchors
                        game_state['current_position'] = len(new_grid_string)
                        game_state['current_step'] += 1
                        game_state['current_pos'] = new_current_pos
                        game_state['anchor_idx'] = new_anchor_idx
                        game_state['anchor_consecutive_failures'] = new_anchor_consecutive_failures
                        game_state['total_steps'] += 1
                        
                        st.rerun()
                
                with col_btn2:
                    if st.button("🔵 P", use_container_width=True, key=f"v3_live_game_btn_p_{current_step}"):
                        actual_value = 'p'
                        
                        # 검증 수행
                        is_correct = predicted_value == actual_value
                        
                        # V3 로직에 따른 상태 업데이트
                        new_current_pos = current_pos
                        new_anchor_idx = anchor_idx
                        new_anchor_consecutive_failures = anchor_consecutive_failures
                        
                        # grid_string 업데이트 (실제값 추가)
                        new_grid_string = grid_string + actual_value
                        
                        # 앵커 재계산
                        new_anchors = update_anchors_after_input(new_grid_string)
                        
                        if not is_correct:
                            # 불일치: 연속 실패 카운트 증가
                            game_state['consecutive_failures'] += 1
                            game_state['total_failures'] += 1
                            new_anchor_consecutive_failures += 1
                            
                            if game_state['consecutive_failures'] > game_state['max_consecutive_failures']:
                                game_state['max_consecutive_failures'] = game_state['consecutive_failures']
                            
                            # [RULE-2] 3회 연속 불일치 발생 시 다음 앵커로
                            if new_anchor_consecutive_failures >= 3:
                                # 현재 앵커 위치를 기준으로 다음 앵커 찾기
                                current_anchor_pos = anchor if anchor is not None else (anchors[anchor_idx] if anchor_idx < len(anchors) else -1)
                                # new_anchors에서 current_anchor_pos 이후의 첫 번째 앵커 찾기
                                next_anchor_idx_in_new = 0
                                for i, a in enumerate(new_anchors):
                                    if a > current_anchor_pos:
                                        next_anchor_idx_in_new = i
                                        break
                                    next_anchor_idx_in_new = i + 1
                                
                                if next_anchor_idx_in_new < len(new_anchors):
                                    next_anchor_pos = new_anchors[next_anchor_idx_in_new]
                                    min_window = min(window_sizes)
                                    new_current_pos = next_anchor_pos + min_window - 1
                                    new_anchor_idx = next_anchor_idx_in_new
                                else:
                                    new_current_pos = len(new_grid_string)
                                    new_anchor_idx = len(new_anchors)
                                new_anchor_consecutive_failures = 0
                        else:
                            # [RULE-1] 적중 시 즉시 종료하고 다음 앵커로
                            game_state['consecutive_failures'] = 0
                            # 현재 앵커 위치를 기준으로 다음 앵커 찾기
                            current_anchor_pos = anchor if anchor is not None else (anchors[anchor_idx] if anchor_idx < len(anchors) else -1)
                            # new_anchors에서 current_anchor_pos 이후의 첫 번째 앵커 찾기
                            next_anchor_idx_in_new = 0
                            for i, a in enumerate(new_anchors):
                                if a > current_anchor_pos:
                                    next_anchor_idx_in_new = i
                                    break
                                next_anchor_idx_in_new = i + 1
                            
                            if next_anchor_idx_in_new < len(new_anchors):
                                next_anchor_pos = new_anchors[next_anchor_idx_in_new]
                                min_window = min(window_sizes)
                                new_current_pos = next_anchor_pos + min_window - 1
                                new_anchor_idx = next_anchor_idx_in_new
                            else:
                                new_current_pos = len(new_grid_string)
                                new_anchor_idx = len(new_anchors)
                            new_anchor_consecutive_failures = 0
                        
                        game_state['total_predictions'] += 1
                        
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
                            'debug_info': debug_info
                        })
                        
                        # 게임 상태 업데이트
                        game_state['grid_string'] = new_grid_string
                        game_state['anchors'] = new_anchors
                        game_state['current_position'] = len(new_grid_string)
                        game_state['current_step'] += 1
                        game_state['current_pos'] = new_current_pos
                        game_state['anchor_idx'] = new_anchor_idx
                        game_state['anchor_consecutive_failures'] = new_anchor_consecutive_failures
                        game_state['total_steps'] += 1
                        
                        st.rerun()
                
                with col_btn3:
                    if st.button("↩️ 취소", use_container_width=True, key=f"v3_live_game_btn_cancel_{current_step}", disabled=len(game_state['history']) == 0):
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
                            game_state['current_position'] = len(game_state['grid_string'])
                            
                            # 통계 복원
                            if last_entry.get('is_correct') is not None:
                                game_state['total_predictions'] = max(0, game_state['total_predictions'] - 1)
                                if last_entry.get('is_correct') is False:
                                    game_state['total_failures'] = max(0, game_state['total_failures'] - 1)
                                    game_state['consecutive_failures'] = max(0, game_state['consecutive_failures'] - 1)
                                else:
                                    game_state['consecutive_failures'] = 0
                            
                            # V3 상태 복원 (간단화: 이전 상태로 복원)
                            # 실제로는 히스토리를 역추적하여 정확한 상태 복원이 필요하지만,
                            # 간단화를 위해 기본값으로 복원
                            game_state['current_pos'] = 0
                            game_state['anchor_idx'] = 0
                            game_state['anchor_consecutive_failures'] = 0
                            
                            st.rerun()
                        else:
                            st.error("⚠️ 취소할 이전 상태가 없습니다.")
            elif skipped:
                # 스킵 상태
                st.markdown("---")
                st.markdown("#### 실제값 선택 (스킵 모드)")
                
                col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
                with col_btn1:
                    if st.button("🔴 B", use_container_width=True, key=f"v3_live_game_btn_skip_b_{current_step}"):
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
                            'debug_info': debug_info
                        })
                        
                        game_state['total_skipped'] += 1
                        game_state['grid_string'] = new_grid_string
                        game_state['anchors'] = new_anchors
                        game_state['current_position'] = len(new_grid_string)
                        game_state['current_step'] += 1
                        game_state['total_steps'] += 1
                        
                        st.rerun()
                
                with col_btn2:
                    if st.button("🔵 P", use_container_width=True, key=f"v3_live_game_btn_skip_p_{current_step}"):
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
                            'debug_info': debug_info
                        })
                        
                        game_state['total_skipped'] += 1
                        game_state['grid_string'] = new_grid_string
                        game_state['anchors'] = new_anchors
                        game_state['current_position'] = len(new_grid_string)
                        game_state['current_step'] += 1
                        game_state['total_steps'] += 1
                        
                        st.rerun()
                
                with col_btn3:
                    if st.button("↩️ 취소", use_container_width=True, key=f"v3_live_game_btn_skip_cancel_{current_step}", disabled=len(game_state['history']) == 0):
                        if len(game_state['history']) > 0:
                            last_entry = game_state['history'].pop()
                            
                            if len(game_state['grid_string']) > 0:
                                game_state['grid_string'] = game_state['grid_string'][:-1]
                            
                            game_state['anchors'] = update_anchors_after_input(game_state['grid_string'])
                            game_state['current_step'] = max(0, current_step - 1)
                            game_state['current_position'] = len(game_state['grid_string'])
                            
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
                    if st.button("🔴 B", use_container_width=True, key=f"v3_live_game_btn_no_pred_b_{current_step}"):
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
                            'debug_info': debug_info
                        })
                        
                        game_state['grid_string'] = new_grid_string
                        game_state['anchors'] = new_anchors
                        game_state['current_position'] = len(new_grid_string)
                        game_state['current_step'] += 1
                        game_state['total_steps'] += 1
                        
                        st.rerun()
                
                with col_btn2:
                    if st.button("🔵 P", use_container_width=True, key=f"v3_live_game_btn_no_pred_p_{current_step}"):
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
                            'debug_info': debug_info
                        })
                        
                        game_state['grid_string'] = new_grid_string
                        game_state['anchors'] = new_anchors
                        game_state['current_position'] = len(new_grid_string)
                        game_state['current_step'] += 1
                        game_state['total_steps'] += 1
                        
                        st.rerun()
                
                with col_btn3:
                    if st.button("↩️ 취소", use_container_width=True, key=f"v3_live_game_btn_no_pred_cancel_{current_step}", disabled=len(game_state['history']) == 0):
                        if len(game_state['history']) > 0:
                            last_entry = game_state['history'].pop()
                            
                            if len(game_state['grid_string']) > 0:
                                game_state['grid_string'] = game_state['grid_string'][:-1]
                            
                            game_state['anchors'] = update_anchors_after_input(game_state['grid_string'])
                            game_state['current_step'] = max(0, current_step - 1)
                            game_state['current_position'] = len(game_state['grid_string'])
                            
                            st.rerun()
                        else:
                            st.error("⚠️ 취소할 이전 상태가 없습니다.")
            
            # 상세 히스토리 표시 (라이브게임 히스토리만)
            validation_completed = game_state.get('validation_completed', False)
            initial_history_count = game_state.get('initial_history_count', 0)
            # 초기 검증 히스토리는 제외하고 라이브게임 히스토리만 표시
            live_history = [h for h in game_state['history'] if h.get('step', 0) > initial_history_count] if validation_completed else game_state['history']
            
            if live_history:
                st.markdown("---")
                with st.expander("📊 라이브 게임 히스토리", expanded=True):
                    history_data = []
                    history_sorted = sorted(live_history, key=lambda x: x.get('step', 0), reverse=True)
                    
                    for entry in history_sorted:
                        is_correct = entry.get('is_correct')
                        match_status = '✅' if is_correct else ('❌' if is_correct is False else '-')
                        predicted = entry.get('predicted')
                        skipped = entry.get('skipped', False)
                        skipped_mark = '⏭️' if skipped else ''
                        predicted_display = f"{predicted}{skipped_mark}" if predicted else f"-{skipped_mark}" if skipped else "-"
                        
                        history_data.append({
                            'Step': entry.get('step', 0),
                            'Position': entry.get('position', ''),
                            'Anchor': entry.get('anchor', ''),
                            'Window': entry.get('window_size', ''),
                            'Prefix': entry.get('prefix', ''),
                            '예측': predicted_display,
                            '실제값': entry.get('actual', '-'),
                            '일치': match_status,
                            '신뢰도': f"{entry.get('confidence', 0):.1f}%" if predicted else '-',
                        })
                    
                    if len(history_data) > 0:
                        history_df = pd.DataFrame(history_data)
                        st.dataframe(history_df, use_container_width=True, hide_index=True)
                        st.caption(f"💡 라이브 게임 {len(live_history)}개 히스토리가 표시됩니다.")
            
            if st.button("🛑 게임 중단", use_container_width=True, key="v3_stop_game"):
                st.session_state.v3_game_state = None
                st.rerun()


if __name__ == "__main__":
    main()
