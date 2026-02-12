"""
Cold Start → State Handoff → Live Loop 플로우 전용 라이브 게임 앱 (3가지 검증 방식).

- V3 (9~14), 윈도우 9 전용, 윈도우 9·10 세 모드를 병렬 실행.
- 현재 상태: 현재 포지션에 대해 3가지 검증별 예측값 있음/없음 테이블.
- 검증 히스토리: 포지션 0부터 전체, 1개 테이블에 V3/W9/W9_10 열로 표시.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st
import pandas as pd

from svg_parser_module import get_change_point_db_connection
from results_storage import save_live_run_results

st.set_page_config(
    page_title="Change-point 플로우 라이브 게임 (3가지 검증 방식)",
    page_icon="🎮",
    layout="wide",
)

MODES = ("v3", "window9", "window9_10")
WINDOW_SIZES_V3 = (9, 10, 11, 12, 13, 14)
WINDOW_SIZES_W9 = (9,)
WINDOW_SIZES_W9_10 = (9, 10)
METHOD = "빈도 기반"
THRESHOLD = 0
MAX_CONSECUTIVE_FAILURES = 3
MIN_GRID_LENGTH = 6


def _window_sizes_for_mode(mode: str):
    if mode == "v3":
        return WINDOW_SIZES_V3
    if mode == "window9":
        return WINDOW_SIZES_W9
    if mode == "window9_10":
        return WINDOW_SIZES_W9_10
    return WINDOW_SIZES_V3


def _anchors_from_grid_string(grid_string: str):
    if not grid_string or len(grid_string) < 2:
        return []
    return sorted(set(i for i in range(len(grid_string) - 1) if grid_string[i] != grid_string[i + 1]))


def _first_anchor_from_position(anchors: list, from_pos: int) -> int:
    i = 0
    while i < len(anchors) and anchors[i] < from_pos:
        i += 1
    return i


def _first_anchor_covering_position(anchors: list, position: int, window_sizes: tuple) -> int:
    """position을 주어진 window_sizes 중 하나로 커버할 수 있는 첫 앵커 인덱스. 없으면 len(anchors)."""
    if not window_sizes:
        return len(anchors)
    min_ws, max_ws = min(window_sizes), max(window_sizes)
    for i in range(len(anchors)):
        a = anchors[i]
        if a + min_ws - 1 <= position <= a + max_ws - 1:
            return i
    return len(anchors)


def _empty_state(anchors=None):
    return {
        "current_pos": 0,
        "active_anchor_idx": 0,
        "anchor_failure_count": 0,
        "next_window_size": 9,
        "anchors": anchors or [],
        "search_from": 0,
    }


def _empty_summary():
    return {"total_steps": 0, "total_failures": 0, "total_predictions": 0, "total_skipped": 0, "accuracy": 0.0}


# -----------------------------------------------------------------------------
# Cold Start per mode
# -----------------------------------------------------------------------------
def cold_start_for_mode(grid_string: str, mode: str):
    """
    Cold Start for a single mode. Returns { "state", "history", "summary" } (no grid_string).
    """
    window_sizes = _window_sizes_for_mode(mode)
    min_ws = min(window_sizes)
    max_ws = max(window_sizes)

    if len(grid_string) < MIN_GRID_LENGTH:
        return {"state": _empty_state(), "history": [], "summary": _empty_summary()}

    anchors = _anchors_from_grid_string(grid_string)
    if not anchors:
        return {"state": _empty_state(), "history": [], "summary": _empty_summary()}

    conn = get_change_point_db_connection()
    try:
        history = []
        current_pos = 0
        anchor_idx = _first_anchor_from_position(anchors, current_pos)
        anchor_completed = True
        last_window_used = None
        anchor_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0

        while current_pos < len(grid_string) and anchor_idx < len(anchors):
            next_anchor = anchors[anchor_idx]
            anchor_success = False
            last_mismatched_pos = None
            fail_count = 0
            exited_string_end = False
            did_finish_anchor = False
            last_pos = None

            for window_size in window_sizes:
                pos = next_anchor + window_size - 1
                if pos >= len(grid_string):
                    # V3/W9/W9_10 동일: 문자열 끝이면 앵커 유지, current_pos만 설정 후 루프 탈출
                    current_pos = len(grid_string)
                    anchor_completed = False
                    last_window_used = (window_size - 1) if window_size > min_ws else None
                    exited_string_end = True
                    break
                if pos < current_pos:
                    continue

                total_steps += 1
                actual = grid_string[pos]
                prefix_len = window_size - 1
                prefix = grid_string[pos - prefix_len : pos]

                df_pred = pd.read_sql_query(
                    "SELECT predicted_value, confidence FROM simulation_predictions_change_point WHERE window_size=? AND prefix=? AND method=? AND threshold=? LIMIT 1",
                    conn, params=[window_size, prefix, METHOD, THRESHOLD],
                )

                if len(df_pred) == 0:
                    total_skipped += 1
                    current_pos = max(current_pos, pos + 1)
                    history.append({
                        "step": total_steps, "position": pos, "anchor": next_anchor, "window_size": window_size,
                        "prefix": prefix, "predicted": None, "actual": actual, "is_correct": None,
                        "confidence": 0.0, "skipped": True, "skip_reason": "예측 테이블에 값 없음",
                    })
                    last_pos = pos
                    if mode == "window9":
                        current_pos = max(current_pos, pos + 1)
                        anchor_idx = _first_anchor_from_position(anchors, current_pos)
                        if anchor_idx >= len(anchors):
                            anchors.append(current_pos)
                            anchor_idx = len(anchors) - 1
                        break
                    if mode == "window9_10":
                        last_pos = pos
                    continue

                last_window_used = window_size
                predicted = df_pred.iloc[0]["predicted_value"]
                confidence = df_pred.iloc[0]["confidence"]
                ok = predicted == actual
                total_predictions += 1

                if not ok:
                    fail_count += 1
                    total_failures += 1
                    last_mismatched_pos = pos
                else:
                    anchor_success = True
                    fail_count = 0

                history.append({
                    "step": total_steps, "position": pos, "anchor": next_anchor, "window_size": window_size,
                    "prefix": prefix, "predicted": predicted, "actual": actual, "is_correct": ok,
                    "confidence": confidence, "skipped": False,
                })
                last_pos = pos

                if ok:
                    current_pos = pos + 1
                    anchor_idx = _first_anchor_from_position(anchors, current_pos)
                    if anchor_idx >= len(anchors):
                        anchors.append(current_pos)
                        anchor_idx = len(anchors) - 1
                    anchor_consecutive_failures = 0
                    anchor_completed = True
                    did_finish_anchor = True
                    break

                if mode == "window9":
                    current_pos = pos + 1
                    anchor_idx = _first_anchor_from_position(anchors, current_pos)
                    if anchor_idx >= len(anchors):
                        anchors.append(current_pos)
                        anchor_idx = len(anchors) - 1
                    anchor_consecutive_failures = 0
                    anchor_completed = True
                    did_finish_anchor = True
                    break

                if mode == "v3" and fail_count >= MAX_CONSECUTIVE_FAILURES:
                    ref_pos = last_mismatched_pos if last_mismatched_pos is not None else pos
                    current_pos = ref_pos + 1
                    anchor_idx = _first_anchor_from_position(anchors, current_pos)
                    if anchor_idx >= len(anchors):
                        anchors.append(current_pos)
                        anchor_idx = len(anchors) - 1
                    anchor_consecutive_failures = 0
                    anchor_completed = True
                    did_finish_anchor = True
                    break

            else:
                if mode == "window9_10" and last_pos is not None:
                    current_pos = last_pos + 1
                    anchor_idx = _first_anchor_from_position(anchors, current_pos)
                    if anchor_idx >= len(anchors):
                        anchors.append(current_pos)
                        anchor_idx = len(anchors) - 1
                    anchor_completed = True
                    did_finish_anchor = True

            if did_finish_anchor:
                pass
            elif exited_string_end:
                anchor_consecutive_failures = fail_count
                break
            if not anchor_success and fail_count < MAX_CONSECUTIVE_FAILURES and mode == "v3":
                if last_mismatched_pos is not None:
                    current_pos = last_mismatched_pos + 1
                anchor_idx += 1
                anchor_consecutive_failures = fail_count
                anchor_completed = True

        next_window_size = 9 if anchor_completed else (last_window_used + 1 if last_window_used is not None else 9)
        if next_window_size > max_ws:
            next_window_size = 9

        state = {
            "current_pos": current_pos,
            "active_anchor_idx": anchor_idx,
            "anchor_failure_count": anchor_consecutive_failures,
            "next_window_size": next_window_size,
            "anchors": anchors,
            "search_from": current_pos,
        }
        acc = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        summary = {"total_steps": total_steps, "total_failures": total_failures, "total_predictions": total_predictions, "total_skipped": total_skipped, "accuracy": acc}
        return {"state": state, "history": history, "summary": summary}
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# Predict / Live step per mode
# -----------------------------------------------------------------------------
def predict_next_for_mode(state: dict, grid_string: str, mode: str):
    """
    다음 포지션(len(grid_string))에 대한 예측 조회.
    state의 active_anchor_idx 기준으로만 조회 (V3: 3실패 종료 후 다음 앵커가 포지션을 커버하지 않으면 예측 없음).
    """
    position = len(grid_string)
    anchors = state.get("anchors") or []
    aidx = state.get("active_anchor_idx", 0)
    window_sizes = _window_sizes_for_mode(mode)

    if aidx >= len(anchors):
        return {"predicted": None, "confidence": 0.0, "window_size": None, "prefix": None, "anchor": None, "skipped": True}

    anchor = anchors[aidx]
    window = position - anchor + 1
    if window not in window_sizes:
        return {"predicted": None, "confidence": 0.0, "window_size": None, "prefix": None, "anchor": anchor, "skipped": True}

    prefix_len = window - 1
    if position < prefix_len:
        return {"predicted": None, "confidence": 0.0, "window_size": None, "prefix": None, "anchor": anchor, "skipped": True}
    prefix = grid_string[position - prefix_len : position]

    conn = get_change_point_db_connection()
    try:
        df = pd.read_sql_query(
            "SELECT predicted_value, confidence FROM simulation_predictions_change_point WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ? LIMIT 1",
            conn, params=[window, prefix, METHOD, THRESHOLD],
        )
        if len(df) == 0:
            return {"predicted": None, "confidence": 0.0, "window_size": window, "prefix": prefix, "anchor": anchor, "skipped": True}
        row = df.iloc[0]
        return {
            "predicted": row["predicted_value"],
            "confidence": row["confidence"],
            "window_size": window,
            "prefix": prefix,
            "anchor": anchor,
            "skipped": False,
        }
    finally:
        conn.close()


def live_step_for_mode(state: dict, grid_string: str, history: list, user_input: str, mode: str):
    new_grid_string = grid_string + user_input
    position = len(grid_string)
    actual = user_input.lower()
    window_sizes = _window_sizes_for_mode(mode)

    if actual not in ("b", "p"):
        return {"state": state, "history": history, "grid_string": grid_string, "step_result": {"error": "b 또는 p만 입력 가능"}}

    pred_result = predict_next_for_mode(state, grid_string, mode)
    predicted = pred_result.get("predicted")
    window_size = pred_result.get("window_size")
    prefix = pred_result.get("prefix")
    anchor = pred_result.get("anchor")
    confidence = pred_result.get("confidence", 0.0)
    skipped = pred_result.get("skipped", True)

    anchors = state.get("anchors") or []
    aidx = state.get("active_anchor_idx", 0)
    fc = state.get("anchor_failure_count", 0)
    step_num = (max((e.get("step", 0) for e in history), default=0)) + 1

    if skipped or predicted is None:
        next_pos = position + 1
        new_anchors = _anchors_from_grid_string(new_grid_string)
        search_from = state.get("search_from", state.get("current_pos", 0))
        if aidx < len(anchors):
            old_anchor_pos = anchors[aidx]
            new_aidx = next((i for i in range(len(new_anchors)) if new_anchors[i] == old_anchor_pos), len(new_anchors))
            if new_aidx >= len(new_anchors):
                new_aidx = _first_anchor_from_position(new_anchors, search_from)
        else:
            new_aidx = _first_anchor_from_position(new_anchors, search_from)
        new_state = {
            "current_pos": next_pos,
            "active_anchor_idx": new_aidx,
            "anchor_failure_count": fc,
            "next_window_size": state.get("next_window_size", 9),
            "anchors": new_anchors,
            "search_from": search_from,
        }
        return {"state": new_state, "history": history, "grid_string": new_grid_string, "step_result": {"skipped": True, "waiting": new_aidx >= len(new_anchors)}}

    ok = predicted.lower() == actual
    new_row = {
        "step": step_num, "position": position, "anchor": anchor, "window_size": window_size,
        "prefix": prefix, "predicted": predicted, "actual": actual, "is_correct": ok,
        "confidence": confidence, "skipped": False,
    }
    new_history = history + [new_row]
    next_pos = position + 1
    new_anchors = _anchors_from_grid_string(new_grid_string)
    search_from = state.get("search_from", state.get("current_pos", 0))

    if ok:
        # V3/W9/W9_10 동일: 다음 앵커 = next_pos 이상 첫 앵커, next_window = 9
        new_search_from = next_pos
        new_anchor_idx = _first_anchor_from_position(new_anchors, new_search_from)
        if new_anchor_idx >= len(new_anchors):
            new_anchors.append(next_pos)
            new_anchor_idx = len(new_anchors) - 1
        new_fc = 0
        new_next_window = 9
    else:
        new_fc = fc + 1
        if mode == "v3" and new_fc >= MAX_CONSECUTIVE_FAILURES:
            new_search_from = next_pos
            new_anchor_idx = _first_anchor_from_position(new_anchors, new_search_from)
            new_fc = 0
            new_next_window = 9
        elif mode == "window9":
            # 종료 규칙만 다름(앵커당 1스텝); 다음 앵커 찾기는 V3와 동일
            new_search_from = next_pos
            new_anchor_idx = _first_anchor_from_position(new_anchors, new_search_from)
            if new_anchor_idx >= len(new_anchors):
                new_anchors.append(next_pos)
                new_anchor_idx = len(new_anchors) - 1
            new_fc = 0
            new_next_window = 9
        elif mode == "window9_10":
            rest = [w for w in window_sizes if w > window_size]
            if rest:
                new_search_from = search_from
                old_val = anchors[aidx] if aidx < len(anchors) else None
                new_anchor_idx = next((i for i in range(len(new_anchors)) if new_anchors[i] == old_val), len(new_anchors))
                if new_anchor_idx >= len(new_anchors):
                    new_anchor_idx = min(aidx, len(new_anchors) - 1)
                new_next_window = rest[0]
            else:
                # 종료 규칙만 다름(2스텝 후); 다음 앵커 찾기는 V3와 동일
                new_search_from = next_pos
                new_anchor_idx = _first_anchor_from_position(new_anchors, new_search_from)
                if new_anchor_idx >= len(new_anchors):
                    new_anchors.append(next_pos)
                    new_anchor_idx = len(new_anchors) - 1
                new_fc = 0
                new_next_window = 9
        else:
            new_search_from = search_from
            old_val = anchors[aidx] if aidx < len(anchors) else None
            new_anchor_idx = next((i for i in range(len(new_anchors)) if new_anchors[i] == old_val), len(new_anchors))
            if new_anchor_idx >= len(new_anchors):
                new_anchor_idx = min(aidx, len(new_anchors) - 1)
            rest = [w for w in window_sizes if w > window_size]
            new_next_window = rest[0] if rest else 9

    new_state = {
        "current_pos": next_pos,
        "active_anchor_idx": new_anchor_idx,
        "anchor_failure_count": new_fc,
        "next_window_size": new_next_window,
        "anchors": new_anchors,
        "search_from": new_search_from,
    }
    return {"state": new_state, "history": new_history, "grid_string": new_grid_string, "step_result": {"is_correct": ok, "skipped": False}}


# -----------------------------------------------------------------------------
# UI helpers
# -----------------------------------------------------------------------------
CELLS_PER_ROW = 35


def render_grid_string_and_anchors(grid_string: str, anchors: list = None):
    if not grid_string:
        st.caption("(Grid String 없음)")
        return
    if anchors is None:
        anchors = _anchors_from_grid_string(grid_string)
    pos_to_anchor_idx = {p: i for i, p in enumerate(anchors)}
    cell_w = "1.85em"
    cell_h = "1.85em"
    base_cell = (
        f"display:inline-block;width:{cell_w};min-width:{cell_w};max-width:{cell_w};"
        f"min-height:{cell_h};height:{cell_h};line-height:{cell_h};"
        "text-align:center;font-family:monospace;vertical-align:middle;"
        "border:1px solid #ccc;box-sizing:border-box;padding:0;font-size:12px;"
    )
    row_style = "margin-bottom:2px;line-height:0;font-size:0;"

    def make_rows(cells: list, n: int = CELLS_PER_ROW):
        return ["".join(cells[s : s + n]) for s in range(0, len(cells), n)]

    char_cell = base_cell + "white-space:nowrap;overflow:hidden;"
    chars = [f"<span style='{char_cell}{'background:#ADD8E6;' if i in anchors else 'background:#fff;'}'>{c}</span>" for i, c in enumerate(grid_string)]
    for row_html in make_rows(chars):
        st.markdown(f"<div style='{row_style}'>{row_html}</div>", unsafe_allow_html=True)
    idx_cells = [f"<span style='{base_cell}color:#555;'>{i}</span>" for i in range(len(grid_string))]
    for row_html in make_rows(idx_cells):
        st.markdown(f"<div style='{row_style}'>{row_html}</div>", unsafe_allow_html=True)
    anchor_cells = []
    for i in range(len(grid_string)):
        if i in pos_to_anchor_idx:
            anchor_cells.append(f"<span style='{base_cell}color:#0066cc;font-weight:bold;'>{pos_to_anchor_idx[i]}</span>")
        else:
            anchor_cells.append(f"<span style='{base_cell}color:#ccc;'>·</span>")
    for row_html in make_rows(anchor_cells):
        st.markdown(f"<div style='{row_style}'>{row_html}</div>", unsafe_allow_html=True)
    st.caption("위: 문자 | 가운데: 포지션 인덱스(0~) | 아래: 앵커 인덱스(0,1,...). 연한 파란색=앵커(변화점)")


def build_current_state_table(flow_result: dict) -> list:
    """현재 포지션(다음 예측 위치)에 대해 3가지 검증별 예측값(P/B). 1행 테이블."""
    gs = flow_result.get("grid_string") or ""
    results = flow_result.get("results") or {}
    pos = len(gs)
    row = {"Position": pos}
    for mode in MODES:
        r = results.get(mode, {})
        state = r.get("state") or {}
        pred = predict_next_for_mode(state, gs, mode)
        pv = pred.get("predicted")
        row[mode.upper() if mode == "v3" else ("W9" if mode == "window9" else "W9_10")] = pv if (not pred.get("skipped") and pv) else "-"
    return [row]


def build_validation_history_table_by_position(flow_result: dict) -> list:
    """포지션 0부터 max까지 한 행에 한 포지션, 열: Position, V3_예측/실제/일치, W9_예측/실제/일치, W9_10_예측/실제/일치."""
    results = flow_result.get("results") or {}
    gs = flow_result.get("grid_string") or ""
    by_pos = {}
    for mode in MODES:
        hist = results.get(mode, {}).get("history") or []
        for e in hist:
            p = e.get("position", 0)
            if p not in by_pos:
                by_pos[p] = {}
            pred = e.get("predicted")
            actual = e.get("actual")
            ok = e.get("is_correct")
            by_pos[p][mode] = {"predicted": pred or "-", "actual": actual or "-", "is_correct": ok}

    max_pos = max(by_pos.keys(), default=-1)
    if len(gs) > 0:
        max_pos = max(max_pos, len(gs) - 1)
    rows = []
    for pos in range(0, max_pos + 1):
        d = by_pos.get(pos, {})
        row = {
            "Position": pos,
            "V3_예측": d.get("v3", {}).get("predicted", "-"),
            "V3_실제": d.get("v3", {}).get("actual", "-"),
            "V3_일치": "O" if d.get("v3", {}).get("is_correct") else ("X" if d.get("v3", {}).get("is_correct") is False else "-"),
            "W9_예측": d.get("window9", {}).get("predicted", "-"),
            "W9_실제": d.get("window9", {}).get("actual", "-"),
            "W9_일치": "O" if d.get("window9", {}).get("is_correct") else ("X" if d.get("window9", {}).get("is_correct") is False else "-"),
            "W9_10_예측": d.get("window9_10", {}).get("predicted", "-"),
            "W9_10_실제": d.get("window9_10", {}).get("actual", "-"),
            "W9_10_일치": "O" if d.get("window9_10", {}).get("is_correct") else ("X" if d.get("window9_10", {}).get("is_correct") is False else "-"),
        }
        rows.append(row)
    # 최근 포지션이 상단에 오도록 역순 반환
    return rows[::-1]


def main():
    st.title("Change-point 플로우 라이브 게임 (3가지 검증 방식)")
    st.markdown("**Cold Start → State Handoff → Live Loop** · V3 / 윈도우 9 / 윈도우 9·10 병렬 실행")

    if "flow_result" not in st.session_state:
        st.session_state.flow_result = None

    st.markdown("---")
    st.markdown("## Grid String 입력")

    grid_input = st.text_area(
        "Grid String",
        key="flow_grid_three",
        height=80,
        placeholder="예: bbppbppbbp...",
        help="Cold Start 시 사용할 grid_string.",
    )

    col_start, col_reset, _ = st.columns([1, 1, 4])
    with col_start:
        if st.button("게임 시작 (Cold Start)", type="primary", use_container_width=True, key="flow_btn_start_three"):
            s = (grid_input or "").strip()
            if not s:
                st.warning("Grid String을 입력하세요.")
            elif len(s) < MIN_GRID_LENGTH:
                st.warning(f"길이는 최소 {MIN_GRID_LENGTH}글자 이상이어야 합니다.")
            else:
                with st.spinner("Cold Start 검증 실행 중 (3모드)..."):
                    try:
                        r_v3 = cold_start_for_mode(s, "v3")
                        r_w9 = cold_start_for_mode(s, "window9")
                        r_w910 = cold_start_for_mode(s, "window9_10")
                        st.session_state.flow_result = {
                            "grid_string": s,
                            "results": {"v3": r_v3, "window9": r_w9, "window9_10": r_w910},
                        }
                        st.rerun()
                    except Exception as e:
                        st.error(f"Cold Start 실패: {e}")
    with col_reset:
        if st.button("초기화", use_container_width=True, key="flow_btn_reset_three"):
            st.session_state.flow_result = None
            st.session_state.pop("flow_last_saved_three", None)
            st.rerun()

    result = st.session_state.flow_result
    if result is not None:
        gs = result.get("grid_string") or ""
        results = result.get("results") or {}
        state_v3 = results.get("v3", {}).get("state") or {}
        anchors = state_v3.get("anchors") or _anchors_from_grid_string(gs)

        st.markdown("---")
        st.markdown("## 검증 히스토리 & Live 입력")

        st.markdown("### Grid String 및 앵커")
        render_grid_string_and_anchors(gs, anchors=anchors)

        st.markdown("### 현재 상태 (현재 포지션에 대한 3가지 검증 예측값)")
        # 모드별 state 디버깅 정보 (기존 라이브앱과 동일 형식)
        mode_labels = {"v3": "V3", "window9": "W9", "window9_10": "W9_10"}
        for mode in MODES:
            r = results.get(mode, {})
            state = r.get("state") or {}
            cp = state.get("current_pos", 0)
            aidx = state.get("active_anchor_idx", 0)
            fc = state.get("anchor_failure_count", 0)
            nw = state.get("next_window_size", 9)
            sf = state.get("search_from", cp)
            anchors_now = state.get("anchors") or []
            aidx_label = f"a{aidx}" if aidx < len(anchors_now) else f"{aidx} (다음 앵커 대기)"
            st.markdown(
                f"**{mode_labels[mode]}** · current_pos = {cp} · 앵커 = {aidx} ({aidx_label}) · "
                f"failure_count = {fc} · next_window = {nw} · search_from = {sf}"
            )
        st.caption("위 Grid의 포지션 인덱스·앵커 인덱스(a0,a1,…)와 동일한 0-based 기준")
        current_table = build_current_state_table(result)
        st.dataframe(pd.DataFrame(current_table), use_container_width=True, hide_index=True)
        st.caption("다음 예측 위치 = len(grid_string). V3 / W9 / W9_10 각각 예측값(P 또는 B, 없으면 -).")

        st.caption("B / P 입력 (3모드 동시 단일 스텝 검증)")
        col_b, col_p, _ = st.columns([1, 1, 4])
        with col_b:
            if st.button("B", key="flow_append_b_three", use_container_width=True):
                try:
                    new_results = {}
                    for mode in MODES:
                        r = results.get(mode, {})
                        step_out = live_step_for_mode(r.get("state"), gs, r.get("history") or [], "b", mode)
                        new_results[mode] = {
                            "state": step_out["state"],
                            "history": step_out["history"],
                            "summary": r.get("summary"),
                        }
                    st.session_state.flow_result = {
                        "grid_string": step_out["grid_string"],
                        "results": new_results,
                    }
                    st.rerun()
                except Exception as e:
                    st.error(f"live_step 실패: {e}")
        with col_p:
            if st.button("P", key="flow_append_p_three", use_container_width=True):
                try:
                    new_results = {}
                    for mode in MODES:
                        r = results.get(mode, {})
                        step_out = live_step_for_mode(r.get("state"), gs, r.get("history") or [], "p", mode)
                        new_results[mode] = {
                            "state": step_out["state"],
                            "history": step_out["history"],
                            "summary": r.get("summary"),
                        }
                    st.session_state.flow_result = {
                        "grid_string": step_out["grid_string"],
                        "results": new_results,
                    }
                    st.rerun()
                except Exception as e:
                    st.error(f"live_step 실패: {e}")

        st.markdown("### 검증 히스토리 테이블 (포지션별 V3 / W9 / W9_10)")
        history_rows = build_validation_history_table_by_position(result)
        if history_rows:
            st.dataframe(pd.DataFrame(history_rows), use_container_width=True, hide_index=True)
            st.caption(f"포지션 0 ~ {len(history_rows) - 1} (총 {len(history_rows)}행) · 최근 포지션 상단 역순 표시")
        else:
            st.info("히스토리가 없습니다.")

        st.markdown("#### Cold Start 요약 (3모드)")
        c1, c2, c3 = st.columns(3)
        for idx, mode in enumerate(MODES):
            col = [c1, c2, c3][idx]
            with col:
                summ = results.get(mode, {}).get("summary") or _empty_summary()
                label = "V3 (9~14)" if mode == "v3" else ("윈도우 9" if mode == "window9" else "윈도우 9·10")
                st.markdown(f"**{label}**")
                st.metric("총 스텝", summ.get("total_steps", 0))
                st.metric("총 예측", summ.get("total_predictions", 0))
                st.metric("정확도", f"{summ.get('accuracy', 0):.1f}%")

        st.markdown("#### 결과 저장")
        if st.session_state.get("flow_last_saved_three"):
            last = st.session_state.flow_last_saved_three
            st.success(f"저장됨 · run_id: {last.get('run_id', '')}")
        if st.button("결과 저장", key="flow_save_results_three", type="secondary", use_container_width=True):
            try:
                run_id = save_live_run_results(
                    run_meta={
                        "method": METHOD,
                        "threshold": THRESHOLD,
                        "window_sizes": list(WINDOW_SIZES_V3),
                        "grid_string": gs,
                        "modes": list(MODES),
                        "history_by_mode": {m: results.get(m, {}).get("history") for m in MODES},
                        "summary_by_mode": {m: results.get(m, {}).get("summary") for m in MODES},
                    },
                    history=results.get("v3", {}).get("history"),
                    summary=results.get("v3", {}).get("summary") or _empty_summary(),
                )
                st.session_state.flow_last_saved_three = {"run_id": run_id}
                st.rerun()
            except Exception as e:
                st.error(f"저장 실패: {e}")


if __name__ == "__main__":
    main()
