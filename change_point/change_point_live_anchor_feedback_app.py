"""
실시간 앵커 검증 및 피드백 시스템
- REQ-401~603: 검증 히스토리 전수 계산, 현재 상태, 예측값 노출, B/P 입력 시 즉시 갱신
- 의존성: 데이터베이스(change_point_ngram.db)만 사용. 다른 Python 모듈 import 없음.
- 예측 테이블은 선행 설정, 실시간 검증/초기화 시 변경하지 않음.
"""

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st


# ---------------------------------------------------------------------------
# DB 연결 (앱 내 복제, 외부 모듈 import 없음)
# ---------------------------------------------------------------------------

def get_db_connection():
    """앱 파일 위치 기준 change_point_ngram.db 연결 (읽기 전용 사용, 예측 테이블 수정 안 함)"""
    base = Path(__file__).resolve().parent
    db_path = base / "change_point_ngram.db"
    if not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=20.0, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
    return conn


# ---------------------------------------------------------------------------
# 앵커 계산 (앱 내 복제)
# ---------------------------------------------------------------------------

def detect_change_points(grid_string):
    """Change-point Detection: 변화점 감지 및 앵커 위치 반환"""
    anchors = []
    for i in range(len(grid_string) - 1):
        if grid_string[i] != grid_string[i + 1]:
            anchors.append(i)
    return sorted(list(set(anchors)))


def update_anchors_after_input(grid_string):
    """실제값 입력 후 앵커 재계산"""
    return detect_change_points(grid_string)


# ---------------------------------------------------------------------------
# V3 전수 검증 (앱 내 복제, simulation_predictions_change_point 읽기만)
# ---------------------------------------------------------------------------

def validate_grid_string_v3(
    grid_string,
    conn,
    window_sizes=(9, 10, 11, 12, 13, 14),
    method="빈도 기반",
    threshold=0,
):
    """
    Grid String에 대해 V3 전수 검증. history, final_current_pos, final_anchor_idx, final_anchor_consecutive_failures 반환.
    conn은 호출자가 넘기며, 이 함수는 conn을 닫지 않음. 예측 테이블은 읽기만 함.
    """
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
            "final_current_pos": 0,
            "final_anchor_idx": 0,
            "final_anchor_consecutive_failures": 0,
        }

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
            "final_current_pos": 0,
            "final_anchor_idx": 0,
            "final_anchor_consecutive_failures": 0,
        }

    history = []
    consecutive_failures = 0
    max_consecutive_failures = 0
    total_steps = 0
    total_failures = 0
    total_predictions = 0
    total_skipped = 0
    MAX_CONSECUTIVE_FAILURES = 3

    current_pos = 0
    anchor_idx = 0
    final_anchor_consecutive_failures = 0
    exit_while_for_pos_beyond = False

    while current_pos < len(grid_string) and anchor_idx < len(anchors):
        while anchor_idx < len(anchors) and anchors[anchor_idx] < current_pos:
            anchor_idx += 1
        if anchor_idx >= len(anchors):
            break

        next_anchor = anchors[anchor_idx]
        anchor_consecutive_failures = 0
        anchor_success = False
        last_mismatched_pos = None
        anchor_processed_any = False

        for window_size in window_sizes:
            pos = next_anchor + window_size - 1
            if pos >= len(grid_string):
                final_anchor_consecutive_failures = anchor_consecutive_failures
                exit_while_for_pos_beyond = True
                break
            if pos < current_pos:
                continue

            total_steps += 1
            actual = grid_string[pos]
            prefix_len = window_size - 1
            prefix = grid_string[pos - prefix_len : pos]

            q = """
                SELECT predicted_value, confidence, b_ratio, p_ratio
                FROM simulation_predictions_change_point
                WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                LIMIT 1
            """
            df_pred = pd.read_sql_query(q, conn, params=[window_size, prefix, method, threshold])

            if len(df_pred) == 0:
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
                continue

            anchor_processed_any = True
            row = df_pred.iloc[0]
            predicted = row["predicted_value"]
            confidence = row["confidence"]
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

            if ok:
                current_pos = pos + 1
                anchor_idx += 1
                final_anchor_consecutive_failures = 0
                break

            if anchor_consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                current_pos = (last_mismatched_pos + 1) if last_mismatched_pos is not None else (pos + 1)
                anchor_idx += 1
                final_anchor_consecutive_failures = 0
                break

        if exit_while_for_pos_beyond:
            break

        if not anchor_success and anchor_consecutive_failures < MAX_CONSECUTIVE_FAILURES:
            if anchor_processed_any and last_mismatched_pos is not None:
                current_pos = last_mismatched_pos + 1
            elif anchor_processed_any:
                max_pos = min(next_anchor + max(window_sizes) - 1, len(grid_string) - 1)
                current_pos = max_pos + 1
            else:
                max_pos = min(next_anchor + max(window_sizes) - 1, len(grid_string) - 1)
                current_pos = max_pos + 1
            anchor_idx += 1
            final_anchor_consecutive_failures = 0

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


# ---------------------------------------------------------------------------
# 다음 포지션 예측 (앱 내 복제, 예측 테이블 읽기만)
# ---------------------------------------------------------------------------

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
    conn,
):
    """다음 검증 포지션의 예측값·윈도우·앵커 정보 반환. conn은 호출자가 관리."""
    while anchor_idx < len(anchors) and anchors[anchor_idx] < current_pos:
        anchor_idx += 1
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
            "debug_info": {"position": position, "selected_anchor": None, "selected_window": None, "all_attempts": []},
        }

    next_anchor = anchors[anchor_idx]
    all_attempts = []
    selected_result = None
    selected_window = None

    for window_size in window_sizes:
        required_pos = next_anchor + window_size - 1
        if required_pos != position:
            continue
        if required_pos < current_pos:
            continue
        prefix_len = window_size - 1
        if position < prefix_len:
            continue
        prefix = grid_string[position - prefix_len : position]

        q = """
            SELECT predicted_value, confidence, b_ratio, p_ratio
            FROM simulation_predictions_change_point
            WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
            LIMIT 1
        """
        df_pred = pd.read_sql_query(q, conn, params=[window_size, prefix, method, threshold])
        if len(df_pred) == 0:
            all_attempts.append({
                "anchor": next_anchor,
                "window_size": window_size,
                "predicted": None,
                "confidence": 0.0,
                "skipped": True,
                "rejection_reason": "예측 테이블에 값 없음",
            })
            continue

        row = df_pred.iloc[0]
        predicted = row["predicted_value"]
        confidence = row["confidence"]
        all_attempts.append({
            "anchor": next_anchor,
            "window_size": window_size,
            "predicted": predicted,
            "confidence": confidence,
            "skipped": False,
            "rejection_reason": None,
        })
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
            break

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
            "debug_info": {"position": position, "selected_anchor": None, "selected_window": None, "all_attempts": all_attempts},
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
            "all_attempts": all_attempts,
        },
    }


# ---------------------------------------------------------------------------
# B/P 피드백 후 상태 전이 (앱 내 함수로 묶음, 예측 테이블 미수정)
# ---------------------------------------------------------------------------

def apply_feedback_and_advance_state(
    game_state,
    actual_value,
    predicted_value,
    anchor,
    anchor_idx,
    anchors,
    window_sizes,
):
    """
    B/P 입력 후 grid_string 추가, 앵커 재계산, RULE-1/RULE-2에 따라
    current_pos, anchor_idx, anchor_consecutive_failures 갱신.
    game_state를 in-place 업데이트. 예측 테이블은 건드리지 않음.
    반환: (new_current_pos, new_anchor_idx, new_anchor_consecutive_failures) 형태로
    game_state에 반영할 값들. 호출 측에서 history append 및 grid_string/anchors 업데이트까지 수행.
    """
    grid_string = game_state["grid_string"]
    current_pos = game_state["current_pos"]
    anchor_consecutive_failures = game_state["anchor_consecutive_failures"]

    new_grid_string = grid_string + actual_value
    new_anchors = update_anchors_after_input(new_grid_string)

    is_correct = predicted_value == actual_value
    new_current_pos = current_pos
    new_anchor_idx = anchor_idx
    new_anchor_consecutive_failures = anchor_consecutive_failures

    if not is_correct:
        game_state["consecutive_failures"] = game_state.get("consecutive_failures", 0) + 1
        game_state["total_failures"] = game_state.get("total_failures", 0) + 1
        new_anchor_consecutive_failures += 1
        if game_state["consecutive_failures"] > game_state.get("max_consecutive_failures", 0):
            game_state["max_consecutive_failures"] = game_state["consecutive_failures"]

        if new_anchor_consecutive_failures >= 3:
            current_anchor_pos = anchor if anchor is not None else (anchors[anchor_idx] if anchor_idx < len(anchors) else -1)
            next_anchor_idx_in_new = len(new_anchors)
            for i, a in enumerate(new_anchors):
                if a > current_anchor_pos:
                    next_anchor_idx_in_new = i
                    break
            if next_anchor_idx_in_new < len(new_anchors):
                next_anchor_pos = new_anchors[next_anchor_idx_in_new]
                new_current_pos = next_anchor_pos + min(window_sizes) - 1
                new_anchor_idx = next_anchor_idx_in_new
            else:
                new_current_pos = len(new_grid_string)
                new_anchor_idx = len(new_anchors)
            new_anchor_consecutive_failures = 0
    else:
        game_state["consecutive_failures"] = 0
        current_anchor_pos = anchor if anchor is not None else (anchors[anchor_idx] if anchor_idx < len(anchors) else -1)
        next_anchor_idx_in_new = len(new_anchors)
        for i, a in enumerate(new_anchors):
            if a > current_anchor_pos:
                next_anchor_idx_in_new = i
                break
        if next_anchor_idx_in_new < len(new_anchors):
            next_anchor_pos = new_anchors[next_anchor_idx_in_new]
            new_current_pos = next_anchor_pos + min(window_sizes) - 1
            new_anchor_idx = next_anchor_idx_in_new
        else:
            new_current_pos = len(new_grid_string)
            new_anchor_idx = len(new_anchors)
        new_anchor_consecutive_failures = 0

    return {
        "new_grid_string": new_grid_string,
        "new_anchors": new_anchors,
        "new_current_pos": new_current_pos,
        "new_anchor_idx": new_anchor_idx,
        "new_anchor_consecutive_failures": new_anchor_consecutive_failures,
        "is_correct": is_correct,
    }


# ---------------------------------------------------------------------------
# UI 렌더 헬퍼
# ---------------------------------------------------------------------------

def render_grid_string_with_anchors(grid_string, anchors, current_position, selected_anchor=None):
    """Grid String과 앵커·현재 포지션 시각화"""
    display_parts = []
    for i, char in enumerate(grid_string):
        style_parts = []
        if selected_anchor is not None and i == selected_anchor:
            style_parts.append("background-color: #FFE6E6; border: 2px solid red;")
        elif i in anchors:
            style_parts.append("background-color: #ADD8E6;")
        if i == current_position:
            style_parts.append("background-color: yellow; font-weight: bold;")
        if style_parts:
            style_str = " ".join(style_parts)
            display_parts.append(f"<span style='{style_str}'>{char}</span>")
        else:
            display_parts.append(char)
    html_inner = "".join(display_parts)
    st.markdown(
        f"<div style='font-size: 20px; font-family: monospace; padding: 10px; border: 1px solid #ddd; border-radius: 5px;'>{html_inner}</div>",
        unsafe_allow_html=True,
    )
    index_string = "".join([str(i % 10) for i in range(len(grid_string))])
    st.markdown(f"<div style='font-size: 12px; font-family: monospace; color: #666;'>{index_string}</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 메인 앱
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(page_title="실시간 앵커 검증·피드백", page_icon="📌", layout="wide")
    st.title("실시간 앵커 검증 및 피드백 시스템")
    st.markdown("검증 히스토리 전수 계산, 현재 상태·다음 예측값 노출, B/P 입력 시 즉시 갱신. **예측 테이블은 선행 설정이며, 실시간 검증/초기화 시 변경되지 않습니다.**")

    window_sizes = (9, 10, 11, 12, 13, 14)
    min_len = min(window_sizes) - 1  # 8

    conn = get_db_connection()
    try:
        # 예측 테이블 존재·레코드 수 확인 (읽기만, 변경 없음)
        try:
            df_cnt = pd.read_sql_query("SELECT COUNT(*) as cnt FROM simulation_predictions_change_point", conn)
            n_sim = int(df_cnt.iloc[0]["cnt"]) if len(df_cnt) > 0 else 0
        except Exception:
            n_sim = 0
        if n_sim == 0:
            st.warning("예측 테이블(simulation_predictions_change_point)이 비어 있습니다. 다른 도구로 먼저 생성한 뒤 사용하세요.")

        # Cutoff ID 목록: DB에서 읽기만 (테이블 없으면 빈 목록)
        try:
            df_ids = pd.read_sql_query("SELECT id FROM preprocessed_grid_strings ORDER BY id", conn)
            cutoff_ids = [None] + df_ids["id"].tolist() if len(df_ids) > 0 else [None]
        except sqlite3.OperationalError:
            cutoff_ids = [None]
        except Exception:
            cutoff_ids = [None]
    finally:
        conn.close()

    # 예측 테이블 설정 (선행·불변: 세션에 저장해 두고, 초기화 시 바꾸지 않음)
    st.markdown("---")
    st.markdown("## 예측 테이블 설정 (선행·변경 시에만 바꿈)")
    col_m, col_t, col_c = st.columns(3)
    with col_m:
        method = st.selectbox("Method", ["빈도 기반", "가중치 기반", "안전 우선"], key="fb_method")
    with col_t:
        threshold = st.number_input("Threshold", 0, 100, 0, key="fb_threshold")
    with col_c:
        cutoff_id = st.selectbox(
            "Cutoff ID (조회용)",
            cutoff_ids,
            format_func=lambda x: "선택 안 함" if x is None else f"ID {x}",
            key="fb_cutoff",
        )
    st.caption("예측값은 simulation_predictions_change_point에서 (method, threshold)로 조회합니다. 초기화해도 이 설정은 유지됩니다.")

    # 세션 상태: 검증 세션만 초기화할 때 바꾸고, 예측 테이블 설정은 위 위젯 값 유지
    if "feedback_state" not in st.session_state:
        st.session_state.feedback_state = None

    st.markdown("---")
    st.markdown("## Grid String 입력 및 세션 초기화")
    grid_input = st.text_area("Grid String", key="fb_grid", height=100, placeholder="b/p/t 시퀀스 입력 (최소 길이 8 이상)")

    col_go, col_reset, _ = st.columns([1, 1, 3])
    with col_go:
        if st.button("세션 초기화 (검증 시작)", type="primary", key="fb_init"):
            if not grid_input or len(grid_input.strip()) < min_len:
                st.warning(f"Grid String 길이는 최소 {min_len} 이상이어야 합니다.")
            elif n_sim == 0:
                st.warning("예측 테이블을 먼저 생성한 뒤 사용하세요.")
            else:
                gs = grid_input.strip()
                anchors = detect_change_points(gs)
                if not anchors:
                    st.warning("Change-point가 감지되지 않았습니다.")
                else:
                    conn2 = get_db_connection()
                    try:
                        val = validate_grid_string_v3(gs, conn2, window_sizes=window_sizes, method=method, threshold=threshold)
                    finally:
                        conn2.close()
                    st.session_state.feedback_state = {
                        "grid_string": gs,
                        "anchors": anchors,
                        "window_sizes": list(window_sizes),
                        "method": method,
                        "threshold": threshold,
                        "current_pos": val["final_current_pos"],
                        "anchor_idx": val["final_anchor_idx"],
                        "anchor_consecutive_failures": val["final_anchor_consecutive_failures"],
                        "total_steps": val["total_steps"],
                        "total_predictions": val["total_predictions"],
                        "total_failures": val["total_failures"],
                        "total_skipped": val["total_skipped"],
                        "max_consecutive_failures": val["max_consecutive_failures"],
                        "consecutive_failures": 0,
                        "history": list(val["history"]),
                    }
                    st.rerun()

    with col_reset:
        if st.session_state.feedback_state is not None:
            if st.button("새 스트링으로 초기화", key="fb_reset"):
                st.session_state.feedback_state = None
                st.rerun()

    if st.session_state.feedback_state is None:
        st.info("Grid String을 입력한 뒤 '세션 초기화'를 누르세요. 예측 테이블은 이미 생성되어 있어야 합니다.")
        return

    fs = st.session_state.feedback_state
    grid_string = fs["grid_string"]
    anchors = fs["anchors"]
    window_sizes_list = fs["window_sizes"]
    method = fs["method"]
    threshold = fs["threshold"]
    current_pos = fs["current_pos"]
    anchor_idx = fs["anchor_idx"]
    anchor_consecutive_failures = fs["anchor_consecutive_failures"]
    next_position = len(grid_string)

    # ----- 예측 조회 (Current Status / Prediction Panel / Input Box에서 사용, 예측 테이블 읽기만) -----
    conn3 = get_db_connection()
    try:
        pred_result = predict_for_position_v3(
            grid_string, next_position, anchors, tuple(window_sizes_list),
            method, threshold, current_pos, anchor_idx, anchor_consecutive_failures, conn3,
        )
    finally:
        conn3.close()

    # ----- History View -----
    st.markdown("---")
    st.markdown("## History View (앵커별 성공/실패)")
    if fs["history"]:
        # 앵커별 요약
        by_anchor = {}
        for e in fs["history"]:
            a = e.get("anchor")
            if a is None:
                continue
            if a not in by_anchor:
                by_anchor[a] = {"마지막_윈도우": e.get("window_size"), "결과": "진행중", "스텝": []}
            by_anchor[a]["마지막_윈도우"] = e.get("window_size")
            by_anchor[a]["스텝"].append(e.get("step"))
            ok = e.get("is_correct")
            if ok is True:
                by_anchor[a]["결과"] = "성공"
            elif ok is False:
                by_anchor[a]["결과"] = "실패"
        summary_rows = []
        for a, v in sorted(by_anchor.items()):
            summary_rows.append({
                "앵커 위치": a,
                "결과": v["결과"],
                "마지막 윈도우": v["마지막_윈도우"],
                "관련 스텝": ",".join(map(str, v["스텝"][:5])) + ("..." if len(v["스텝"]) > 5 else ""),
            })
        if summary_rows:
            with st.expander("앵커별 요약", expanded=True):
                st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)
        # 스텝별 테이블
        rows = []
        for e in fs["history"]:
            ok = e.get("is_correct")
            match_s = "성공" if ok is True else ("실패" if ok is False else "스킵")
            rows.append({
                "Step": e.get("step"),
                "Position": e.get("position"),
                "Anchor": e.get("anchor"),
                "Window": e.get("window_size"),
                "예측": e.get("predicted") or "-",
                "실제": e.get("actual", "-"),
                "결과": match_s,
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("아직 검증 이력이 없습니다.")

    # ----- Current Status -----
    st.markdown("---")
    st.markdown("## Current Status")
    w_display = pred_result.get("window_size")
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        cur_anchor = anchors[anchor_idx] if anchor_idx < len(anchors) else None
        st.metric("현재 앵커 위치", cur_anchor if cur_anchor is not None else "N/A")
    with c2:
        st.metric("사용 중인 윈도우 크기", w_display if w_display is not None else "N/A")
    with c3:
        st.metric("연속 실패 횟수", anchor_consecutive_failures)
    with c4:
        st.metric("총 예측 횟수", fs["total_predictions"])
    with c5:
        st.metric("총 실패", fs["total_failures"])

    # ----- Prediction Panel -----
    st.markdown("---")
    st.markdown("## Prediction Panel")
    anchor_display = pred_result.get("anchor")
    w_display = pred_result.get("window_size")
    pv = pred_result.get("predicted")
    skipped = pred_result.get("skipped", True)
    if anchor_display is not None and w_display is not None:
        msg = f"현재 앵커(Index {anchor_display}) 기준, 윈도우 {w_display}단계 검증 중입니다. "
        if not skipped and pv is not None:
            msg += f"다음 예측값은 **[{pv}]**입니다."
            st.markdown(msg)
            st.markdown(f"<div style='font-size: 28px; font-weight: bold;'>{pv}</div>", unsafe_allow_html=True)
        else:
            msg += "다음 예측값 없음(스킵 또는 예측 불가)."
            st.markdown(msg)
    else:
        st.markdown("다음 검증할 앵커/윈도우가 없습니다. (검증 완료 또는 범위 밖)")

    # ----- Input Box (B/P) -----
    st.markdown("---")
    st.markdown("## 실제 결과 입력 (B/P)")
    st.caption("예측값이 없어도 B/P를 입력하면 스트링이 갱신되어 다음 예측이 가능해집니다.")
    col_b, col_p, _ = st.columns([1, 1, 2])
    with col_b:
        if st.button("B", key="fb_btn_b", use_container_width=True):
            _apply_and_rerun(fs, "b", pred_result, anchor_idx, anchors, tuple(window_sizes_list))
    with col_p:
        if st.button("P", key="fb_btn_p", use_container_width=True):
            _apply_and_rerun(fs, "p", pred_result, anchor_idx, anchors, tuple(window_sizes_list))

    # Grid 시각화
    st.markdown("---")
    st.markdown("### Grid String 및 앵커")
    render_grid_string_with_anchors(grid_string, anchors, next_position, selected_anchor=pred_result.get("anchor"))


def _apply_and_rerun(game_state, actual_value, pred_result, anchor_idx, anchors, window_sizes):
    predicted_value = pred_result.get("predicted")
    has_pred = predicted_value is not None and not pred_result.get("skipped", True)
    anchor = pred_result.get("anchor")

    if has_pred:
        out = apply_feedback_and_advance_state(
            game_state, actual_value, predicted_value, anchor, anchor_idx, anchors, window_sizes
        )
        game_state["grid_string"] = out["new_grid_string"]
        game_state["anchors"] = out["new_anchors"]
        game_state["current_pos"] = out["new_current_pos"]
        game_state["anchor_idx"] = out["new_anchor_idx"]
        game_state["anchor_consecutive_failures"] = out["new_anchor_consecutive_failures"]
        game_state["total_predictions"] = game_state.get("total_predictions", 0) + 1
        game_state["total_steps"] = game_state.get("total_steps", 0) + 1
        game_state["history"].append({
            "step": len(game_state["history"]) + 1,
            "position": len(out["new_grid_string"]) - 1,
            "anchor": anchor,
            "window_size": pred_result.get("window_size"),
            "prefix": pred_result.get("prefix"),
            "predicted": predicted_value,
            "actual": actual_value,
            "is_correct": out["is_correct"],
            "confidence": pred_result.get("confidence", 0) or 0,
            "skipped": False,
        })
    else:
        # 예측 없음: 스트링·앵커만 갱신해 다음 예측이 가능하도록 함
        new_grid = game_state["grid_string"] + actual_value
        new_anchors = update_anchors_after_input(new_grid)
        game_state["grid_string"] = new_grid
        game_state["anchors"] = new_anchors
        game_state["total_steps"] = game_state.get("total_steps", 0) + 1
        game_state["history"].append({
            "step": len(game_state["history"]) + 1,
            "position": len(new_grid) - 1,
            "anchor": anchor,
            "window_size": pred_result.get("window_size"),
            "prefix": pred_result.get("prefix"),
            "predicted": None,
            "actual": actual_value,
            "is_correct": None,
            "confidence": 0.0,
            "skipped": True,
            "skip_reason": "예측 없이 입력만 반영",
        })
    st.rerun()


if __name__ == "__main__":
    main()
