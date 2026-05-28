"""
윈도우 9 전용 (빈도 51.3% + 시뮬레이션 승률 49.9%) 라이브 게임 앱 — live_game_freq_win2 복제본.

- Cold Start → State Handoff → Live Loop (기존 플로우와 동일).
- 예측: simulation_predictions_change_point에서 빈도 기반만 조회,
  빈도 신뢰도 ≥ 51.3% 및 sim_win_rate_pct ≥ 49.9%일 때만 사용 (테이블 threshold=0).
- 윈도우 9만 사용. 앵커 중첩 시 이전 앵커만 검증(validated_positions).
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
    page_title="라이브 게임 (freq_win2)",
    page_icon="🎯",
    layout="wide",
)

# 윈도우 9만, 빈도 51.3% + 시뮬 승률 49.9%
WINDOW_SIZES = (9,)
TABLE_THRESHOLD = 0  # 테이블 조회 시 항상 0
MIN_CONFIDENCE_FREQ = 51.3
MIN_WIN_RATE_PCT = 49.9
MAX_CONSECUTIVE_FAILURES = 3
MIN_GRID_LENGTH = 6


def _anchors_from_grid_string(grid_string: str):
    """grid_string에서 change-point(앵커) 위치 리스트 반환."""
    if not grid_string or len(grid_string) < 2:
        return []
    return sorted(set(i for i in range(len(grid_string) - 1) if grid_string[i] != grid_string[i + 1]))


def _first_anchor_from_position(anchors: list, from_pos: int) -> int:
    """from_pos 이상인 첫 앵커 인덱스. 없으면 len(anchors)."""
    i = 0
    while i < len(anchors) and anchors[i] < from_pos:
        i += 1
    return i


def _query_freq_win2_prediction(conn, window_size: int, prefix: str, min_freq: float, min_win_rate: float):
    """
    테이블에서 빈도 기반만 조회 후 freq_win2 조건 적용.
    조건: conf_freq >= min_freq, sim_win_rate_pct >= min_win_rate.
    반환: (predicted, confidence, skip_reason). 스킵이어도 해당 prefix 예측값이 있으면 predicted를 채워 반환.
    """
    q = """
        SELECT method, predicted_value, confidence, sim_win_rate_pct
        FROM simulation_predictions_change_point
        WHERE window_size = ? AND prefix = ? AND threshold = ?
    """
    df = pd.read_sql_query(q, conn, params=[window_size, prefix, TABLE_THRESHOLD])
    by_method = {}
    for _, row in df.iterrows():
        by_method[row["method"]] = row
    freq_row = by_method.get("빈도 기반")
    if freq_row is None:
        return None, 0.0, "예측 테이블에 값 없음" if len(by_method) == 0 else "빈도 기반 없음"
    pred_val = freq_row.get("predicted_value")
    conf_freq = freq_row["confidence"] if freq_row["confidence"] is not None else 0.0
    if conf_freq < min_freq:
        return pred_val, conf_freq, f"신뢰도 부족 (빈도 {conf_freq:.1f}% < {min_freq}%)"
    sim_wr = freq_row.get("sim_win_rate_pct")
    if sim_wr is None:
        return pred_val, conf_freq, "시뮬레이션 승률 없음"
    if sim_wr < min_win_rate:
        return pred_val, conf_freq, f"시뮬레이션 승률 부족 ({sim_wr:.1f}% < {min_win_rate}%)"
    return pred_val, conf_freq, None


# -----------------------------------------------------------------------------
# Step 1: Cold Start — 앵커 추출 → 전수 검증 (freq_win2 규칙 + 앵커 중첩 시 이전만)
# -----------------------------------------------------------------------------
def cold_start(grid_string: str, min_confidence_freq: float = MIN_CONFIDENCE_FREQ, min_win_rate_pct: float = MIN_WIN_RATE_PCT):
    """
    윈도우 9만, 빈도 신뢰도 ≥ min_confidence_freq 및 시뮬 승률 ≥ min_win_rate_pct 시만 예측 사용.
    앵커 중첩 시 validated_positions로 이전 앵커만 검증.
    """
    min_ws = min(WINDOW_SIZES)
    if len(grid_string) < MIN_GRID_LENGTH:
        return {
            "state": {"current_pos": 0, "active_anchor_idx": 0, "anchor_failure_count": 0, "next_window_size": 9, "anchors": [], "search_from": 0, "min_confidence_freq": min_confidence_freq, "min_win_rate_pct": min_win_rate_pct},
            "history": [],
            "grid_string": grid_string,
            "summary": {"total_steps": 0, "total_failures": 0, "total_predictions": 0, "total_skipped": 0, "accuracy": 0.0},
        }

    anchors = _anchors_from_grid_string(grid_string)
    if not anchors:
        return {
            "state": {"current_pos": 0, "active_anchor_idx": 0, "anchor_failure_count": 0, "next_window_size": 9, "anchors": [], "search_from": 0, "min_confidence_freq": min_confidence_freq, "min_win_rate_pct": min_win_rate_pct},
            "history": [],
            "grid_string": grid_string,
            "summary": {"total_steps": 0, "total_failures": 0, "total_predictions": 0, "total_skipped": 0, "accuracy": 0.0},
        }

    conn = get_change_point_db_connection()
    try:
        history = []
        current_pos = 0
        anchor_idx = _first_anchor_from_position(anchors, current_pos)
        anchor_completed = True
        anchor_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0
        validated_positions = set()

        while current_pos < len(grid_string) and anchor_idx < len(anchors):
            next_anchor = anchors[anchor_idx]
            anchor_success = False
            last_mismatched_pos = None
            fail_count = 0
            exited_string_end = False
            did_finish_anchor = False

            for window_size in WINDOW_SIZES:
                pos = next_anchor + window_size - 1
                if pos >= len(grid_string):
                    current_pos = len(grid_string)
                    anchor_completed = False
                    exited_string_end = True
                    break
                if pos < current_pos:
                    continue
                if pos in validated_positions:
                    continue

                total_steps += 1
                actual = grid_string[pos]
                prefix_len = window_size - 1
                prefix = grid_string[pos - prefix_len : pos]

                predicted, confidence, skip_reason = _query_freq_win2_prediction(conn, window_size, prefix, min_confidence_freq, min_win_rate_pct)
                validated_positions.add(pos)

                if skip_reason:
                    total_skipped += 1
                    current_pos = max(current_pos, pos + 1)
                    history.append({"step": total_steps, "position": pos, "anchor": next_anchor, "anchor_idx": anchor_idx, "window_size": window_size, "prefix": prefix, "predicted": predicted, "actual": actual, "is_correct": None, "confidence": confidence, "skipped": True, "skip_reason": skip_reason})
                    continue

                ok = predicted == actual
                total_predictions += 1
                if not ok:
                    fail_count += 1
                    total_failures += 1
                    last_mismatched_pos = pos
                else:
                    anchor_success = True
                    fail_count = 0

                history.append({"step": total_steps, "position": pos, "anchor": next_anchor, "anchor_idx": anchor_idx, "window_size": window_size, "prefix": prefix, "predicted": predicted, "actual": actual, "is_correct": ok, "confidence": confidence, "skipped": False})

                if ok:
                    current_pos = pos + 1
                    # Cold Start: 정답 시 다음 앵커를 순차로 진행(anchor_idx+1). 위치 기준 점프 시 중간 앵커 검증이 누락됨.
                    anchor_idx += 1
                    anchor_consecutive_failures = 0
                    anchor_completed = True
                    did_finish_anchor = True
                    break

                if fail_count >= MAX_CONSECUTIVE_FAILURES:
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

            if did_finish_anchor:
                pass
            elif exited_string_end:
                anchor_consecutive_failures = fail_count
                break
            if not anchor_success and fail_count < MAX_CONSECUTIVE_FAILURES:
                if last_mismatched_pos is not None:
                    current_pos = last_mismatched_pos + 1
                anchor_idx += 1
                anchor_consecutive_failures = fail_count
                anchor_completed = True

        next_window_size = 9  # 윈도우 9만 사용

        state = {
            "current_pos": current_pos,
            "active_anchor_idx": anchor_idx,
            "anchor_failure_count": anchor_consecutive_failures,
            "next_window_size": next_window_size,
            "anchors": anchors,
            "search_from": current_pos,
            "min_confidence_freq": min_confidence_freq,
            "min_win_rate_pct": min_win_rate_pct,
        }
        acc = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        summary = {"total_steps": total_steps, "total_failures": total_failures, "total_predictions": total_predictions, "total_skipped": total_skipped, "accuracy": acc}
        return {"state": state, "history": history, "grid_string": grid_string, "summary": summary}
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# Live Loop — 예측 노출 / 단일 스텝 검증 (freq_win2)
# -----------------------------------------------------------------------------
def predict_next(state: dict, grid_string: str):
    """다음 포지션(len(grid_string))에 대한 freq_win2 예측 조회."""
    position = len(grid_string)
    anchors = state.get("anchors") or []
    aidx = state.get("active_anchor_idx", 0)
    if aidx >= len(anchors):
        return {"predicted": None, "confidence": 0.0, "window_size": None, "prefix": None, "anchor": None, "skipped": True}

    anchor = anchors[aidx]
    window = position - anchor + 1
    if window not in WINDOW_SIZES:
        return {"predicted": None, "confidence": 0.0, "window_size": None, "prefix": None, "anchor": anchor, "skipped": True}

    prefix_len = window - 1
    if position < prefix_len:
        return {"predicted": None, "confidence": 0.0, "window_size": None, "prefix": None, "anchor": anchor, "skipped": True}
    prefix = grid_string[position - prefix_len : position]

    min_freq = state.get("min_confidence_freq", MIN_CONFIDENCE_FREQ)
    min_wr = state.get("min_win_rate_pct", MIN_WIN_RATE_PCT)
    conn = get_change_point_db_connection()
    try:
        predicted, confidence, skip_reason = _query_freq_win2_prediction(conn, window, prefix, min_freq, min_wr)
        if predicted is None:
            return {"predicted": None, "confidence": 0.0, "window_size": window, "prefix": prefix, "anchor": anchor, "skipped": True, "skip_reason": skip_reason}
        # 조건(신뢰도/승률) 미충족 시에도 DB에 값이 있으면 (pred, conf, reason) 반환됨 → 스킵으로 처리
        if skip_reason:
            return {"predicted": predicted, "confidence": confidence, "window_size": window, "prefix": prefix, "anchor": anchor, "skipped": True, "skip_reason": skip_reason}
        return {
            "predicted": predicted,
            "confidence": confidence,
            "window_size": window,
            "prefix": prefix,
            "anchor": anchor,
            "skipped": False,
        }
    finally:
        conn.close()


def live_step(state: dict, grid_string: str, history: list, user_input: str):
    """B/P 한 글자 입력 시 단일 스텝 검증, 히스토리에 한 행 추가."""
    new_grid_string = grid_string + user_input
    position = len(grid_string)
    actual = user_input.lower()
    if actual not in ("b", "p"):
        return {"state": state, "history": history, "grid_string": grid_string, "step_result": {"error": "b 또는 p만 입력 가능"}}

    pred_result = predict_next(state, grid_string)
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
        # 윈도우 9만 사용: 다음 예측 위치 next_pos에 대응하는 앵커는 next_pos - 8 (window = next_pos - anchor + 1 == 9)
        anchor_for_next = next_pos - 8
        new_aidx = next((i for i, a in enumerate(new_anchors) if a == anchor_for_next), len(new_anchors))
        if new_aidx >= len(new_anchors):
            # 해당 위치에 앵커가 없으면, next_pos - 8 이상인 첫 앵커로 (순차 진행)
            new_aidx = next((i for i, a in enumerate(new_anchors) if a >= anchor_for_next), len(new_anchors))
        new_history = history
        # 해당 앵커의 검증 위치(anchor + 8)일 때만 스킵 행 추가. 불필요한 스텝 행 방지.
        ws = window_size or 9
        if anchor is not None and position == anchor + ws - 1:
            skip_reason = pred_result.get("skip_reason", "") or "스킵"
            new_history = history + [{
                "step": step_num,
                "position": position,
                "anchor": anchor,
                "anchor_idx": aidx,
                "window_size": ws,
                "prefix": prefix or "",
                "predicted": predicted,
                "actual": actual,
                "is_correct": None,
                "confidence": confidence,
                "skipped": True,
                "skip_reason": skip_reason,
            }]
        new_state = {
            "current_pos": next_pos,
            "active_anchor_idx": new_aidx,
            "anchor_failure_count": fc,
            "next_window_size": 9,
            "anchors": new_anchors,
            "search_from": search_from,
            "min_confidence_freq": state.get("min_confidence_freq", MIN_CONFIDENCE_FREQ),
            "min_win_rate_pct": state.get("min_win_rate_pct", MIN_WIN_RATE_PCT),
        }
        return {"state": new_state, "history": new_history, "grid_string": new_grid_string, "step_result": {"skipped": True, "waiting": new_aidx >= len(new_anchors)}}

    ok = predicted.lower() == actual
    new_row = {
        "step": step_num,
        "position": position,
        "anchor": anchor,
        "anchor_idx": aidx,
        "window_size": window_size,
        "prefix": prefix,
        "predicted": predicted,
        "actual": actual,
        "is_correct": ok,
        "confidence": confidence,
        "skipped": False,
    }
    new_history = history + [new_row]
    next_pos = position + 1
    new_anchors = _anchors_from_grid_string(new_grid_string)
    search_from = state.get("search_from", state.get("current_pos", 0))

    # 다음 예측 위치 next_pos를 커버하는 앵커로 전환 (윈도우 9: anchor = next_pos - 8)
    anchor_for_next = next_pos - 8
    new_anchor_idx = next((i for i, a in enumerate(new_anchors) if a == anchor_for_next), len(new_anchors))
    if new_anchor_idx >= len(new_anchors):
        new_anchor_idx = next((i for i, a in enumerate(new_anchors) if a >= anchor_for_next), len(new_anchors))
    new_search_from = next_pos

    if ok:
        new_fc = 0
        new_next_window = 9
    else:
        new_fc = fc + 1
        if new_fc >= MAX_CONSECUTIVE_FAILURES:
            new_fc = 0
        new_next_window = 9

    new_state = {
        "current_pos": next_pos,
        "active_anchor_idx": new_anchor_idx,
        "anchor_failure_count": new_fc,
        "next_window_size": new_next_window,
        "anchors": new_anchors,
        "search_from": new_search_from,
        "min_confidence_freq": state.get("min_confidence_freq", MIN_CONFIDENCE_FREQ),
        "min_win_rate_pct": state.get("min_win_rate_pct", MIN_WIN_RATE_PCT),
    }
    return {"state": new_state, "history": new_history, "grid_string": new_grid_string, "step_result": {"is_correct": ok, "skipped": False}}


CELLS_PER_ROW = 35


def render_grid_string_and_anchors(grid_string: str, anchors: list = None):
    """Grid String 및 앵커 시각화."""
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
        return ["".join(cells[start : start + n]) for start in range(0, len(cells), n)]

    char_cell = base_cell + "white-space:nowrap;overflow:hidden;"
    chars = []
    for i, c in enumerate(grid_string):
        bg = "background:#ADD8E6;" if i in anchors else "background:#fff;"
        chars.append(f"<span style='{char_cell}{bg}'>{c}</span>")
    for row_html in make_rows(chars):
        st.markdown(f"<div style='{row_style}'>{row_html}</div>", unsafe_allow_html=True)
    idx_cells = [f"<span style='{base_cell}color:#555;'>{i}</span>" for i in range(len(grid_string))]
    for row_html in make_rows(idx_cells):
        st.markdown(f"<div style='{row_style}'>{row_html}</div>", unsafe_allow_html=True)
    anchor_cells = []
    for i in range(len(grid_string)):
        if i in pos_to_anchor_idx:
            a_idx = pos_to_anchor_idx[i]
            anchor_cells.append(f"<span style='{base_cell}color:#0066cc;font-weight:bold;'>{a_idx}</span>")
        else:
            anchor_cells.append(f"<span style='{base_cell}color:#ccc;'>·</span>")
    for row_html in make_rows(anchor_cells):
        st.markdown(f"<div style='{row_style}'>{row_html}</div>", unsafe_allow_html=True)
    st.caption("위: 문자 | 가운데: 포지션 | 아래: 앵커 인덱스. 연한 파란색=앵커(변화점)")


def build_validation_history_table(history):
    """히스토리 테이블 행 (최신순). 스킵 시 예측 열은 '예측값(사유)' 형식."""
    rows = []
    for e in history or []:
        ok = e.get("is_correct")
        ms = "✅" if ok else ("❌" if ok is False else "-")
        pred = e.get("predicted")
        skip = e.get("skipped", False)
        reason = e.get("skip_reason", "") or ("스킵" if skip else "")
        if skip:
            pred_part = pred if pred else "-"
            disp = f"{pred_part}({reason})"
        else:
            disp = pred if pred else "-"
        anchor_pos = e.get("anchor", "")
        anchor_i = e.get("anchor_idx")
        anchor_disp = f"{anchor_pos}({anchor_i})" if anchor_i is not None else str(anchor_pos)
        rows.append({
            "Step": e.get("step", 0),
            "Position": e.get("position", ""),
            "Anchor": anchor_disp,
            "Window Size": e.get("window_size", ""),
            "Prefix": e.get("prefix", ""),
            "예측": disp,
            "실제값": e.get("actual", "-"),
            "일치": ms,
            "신뢰도": f"{e.get('confidence', 0):.1f}%" if pred else "-",
            "스킵 사유": reason if skip else "",
        })
    rows.sort(key=lambda r: r["Step"], reverse=True)
    return rows


def main():
    st.title("🎯 라이브 게임 (freq_win2)")
    st.markdown("**Cold Start → State Handoff → Live Loop** · 윈도우 9만 사용 · 빈도 51.3%·승률 49.9% 조건 (테이블 threshold=0)")

    if "flow_freq_win2_result" not in st.session_state:
        st.session_state.flow_freq_win2_result = None
    if "flow_freq_win2_saved_freq" not in st.session_state:
        st.session_state.flow_freq_win2_saved_freq = MIN_CONFIDENCE_FREQ
    if "flow_freq_win2_saved_win_rate" not in st.session_state:
        st.session_state.flow_freq_win2_saved_win_rate = MIN_WIN_RATE_PCT

    # 설정: Grid String 입력 상단 본문에 배치 (사이드바 미사용)
    st.markdown("### 1. 조건 설정")
    col_freq, col_wr = st.columns(2)
    with col_freq:
        min_freq = st.number_input("빈도 신뢰도 최소 (%)", 0.0, 100.0, float(st.session_state.flow_freq_win2_saved_freq), 0.1, key="freq_win2_min_freq", help="빈도 기반 confidence가 이 값 이상일 때만 예측 사용")
    with col_wr:
        min_win_rate = st.number_input("시뮬레이션 승률 최소 (%)", 0.0, 100.0, float(st.session_state.flow_freq_win2_saved_win_rate), 0.1, key="freq_win2_min_win_rate", help="sim_win_rate_pct가 이 값 이상일 때만 예측 사용")
    col_save, col_show, _ = st.columns([1, 2, 3])
    with col_save:
        if st.button("💾 설정 저장", type="secondary", use_container_width=True, key="freq_win2_btn_save"):
            st.session_state.flow_freq_win2_saved_freq = min_freq
            st.session_state.flow_freq_win2_saved_win_rate = min_win_rate
            st.success("저장되었습니다.")
            st.rerun()
    with col_show:
        st.caption(f"**저장된 조건:** 빈도 **{st.session_state.flow_freq_win2_saved_freq}%** · 승률 **{st.session_state.flow_freq_win2_saved_win_rate}%**")
    st.caption("저장 후 게임 시작 시 위 저장된 값으로 Cold Start가 실행됩니다.")

    st.markdown("---")
    st.markdown("## 📝 Grid String 입력")

    grid_input = st.text_area(
        "Grid String",
        key="freq_win2_flow_grid",
        height=80,
        placeholder="예: bbppbppbbp...",
        help="Cold Start 시 사용할 grid_string.",
    )

    col_start, col_reset, _ = st.columns([1, 1, 4])
    with col_start:
        if st.button("🎮 게임 시작 (Cold Start)", type="primary", use_container_width=True, key="freq_win2_btn_start"):
            s = (grid_input or "").strip()
            if not s:
                st.warning("Grid String을 입력하세요.")
            elif len(s) < MIN_GRID_LENGTH:
                st.warning(f"길이는 최소 {MIN_GRID_LENGTH}글자 이상이어야 합니다.")
            else:
                with st.spinner("Cold Start 검증 실행 중..."):
                    try:
                        result = cold_start(s, min_confidence_freq=st.session_state.flow_freq_win2_saved_freq, min_win_rate_pct=st.session_state.flow_freq_win2_saved_win_rate)
                        st.session_state.flow_freq_win2_result = result
                        st.rerun()
                    except Exception as e:
                        st.error(f"Cold Start 실패: {e}")
    with col_reset:
        if st.button("🔄 초기화", use_container_width=True, key="freq_win2_btn_reset"):
            st.session_state.flow_freq_win2_result = None
            st.session_state.pop("flow_freq_win2_last_saved", None)
            st.rerun()

    result = st.session_state.flow_freq_win2_result
    if result is not None:
        state = result.get("state") or {}
        history = result.get("history") or []
        gs = result.get("grid_string") or ""

        st.markdown("---")
        st.markdown("## ✅ 검증 히스토리 & Live 입력")

        st.markdown("### Grid String 및 앵커")
        render_grid_string_and_anchors(gs, anchors=state.get("anchors"))

        st.markdown("### 현재 상태 (State Handoff)")
        cp = state.get("current_pos", 0)
        aidx = state.get("active_anchor_idx", 0)
        fc = state.get("anchor_failure_count", 0)
        nw = state.get("next_window_size", 9)
        sf = state.get("search_from", cp)
        anchors_now = state.get("anchors") or []
        anchor_pos = anchors_now[aidx] if aidx < len(anchors_now) else None
        aidx_label = f"{anchor_pos}({aidx})" if anchor_pos is not None else f"{aidx} (다음 앵커 대기)"
        st.markdown(
            f"**current_pos** = {cp} · **앵커** = {aidx_label} · "
            f"failure_count = {fc} · next_window = {nw} · **search_from** = {sf}"
        )
        cfg_freq = state.get("min_confidence_freq", MIN_CONFIDENCE_FREQ)
        cfg_wr = state.get("min_win_rate_pct", MIN_WIN_RATE_PCT)
        st.caption(f"윈도우 9만 사용 · 빈도 **{cfg_freq}%**·승률 **{cfg_wr}%** 조건 (이 State에 적용된 설정)")

        next_pred = predict_next(state, gs)
        st.markdown("**다음 예측값** (다음 포지션 = len(grid_string))")
        aidx_now = state.get("active_anchor_idx", 0)
        if aidx_now >= len(anchors_now):
            st.info("⏳ **다음 앵커가 생길 때까지 입력 대기** (B/P 입력 시 anchors 갱신 후 재탐색)")
        elif next_pred.get("skipped") or next_pred.get("predicted") is None:
            anchor_pos = next_pred.get("anchor")
            reason = next_pred.get("skip_reason", "")
            anchor_disp = f"{anchor_pos}({aidx_now})" if anchor_pos is not None and aidx_now < len(anchors_now) else (f"{anchor_pos}(?)" if anchor_pos is not None else "")
            if anchor_disp:
                st.info(f"앵커: **{anchor_disp}** · 예측 없음 ({reason or '조건 미충족'})")
            else:
                st.info("예측 없음")
        else:
            pv = next_pred.get("predicted", "")
            conf = next_pred.get("confidence", 0.0)
            ws = next_pred.get("window_size")
            anchor_pos = next_pred.get("anchor")
            anchor_disp = f"{anchor_pos}({aidx_now})" if anchor_pos is not None and aidx_now < len(anchors_now) else str(anchor_pos or "")
            st.markdown(f"예측: **{pv}** · 신뢰도: **{conf:.1f}%** · Anchor: **{anchor_disp}** · Window: {ws}")

        st.caption("B / P 입력 (단일 스텝 검증)")
        col_b, col_p, _ = st.columns([1, 1, 4])
        with col_b:
            if st.button("🔴 B", key="freq_win2_append_b", use_container_width=True):
                try:
                    step_out = live_step(state, gs, history, "b")
                    st.session_state.flow_freq_win2_result = {
                        "state": step_out["state"],
                        "history": step_out["history"],
                        "grid_string": step_out["grid_string"],
                        "summary": result.get("summary"),
                    }
                    st.rerun()
                except Exception as e:
                    st.error(f"live_step 실패: {e}")
        with col_p:
            if st.button("🔵 P", key="freq_win2_append_p", use_container_width=True):
                try:
                    step_out = live_step(state, gs, history, "p")
                    st.session_state.flow_freq_win2_result = {
                        "state": step_out["state"],
                        "history": step_out["history"],
                        "grid_string": step_out["grid_string"],
                        "summary": result.get("summary"),
                    }
                    st.rerun()
                except Exception as e:
                    st.error(f"live_step 실패: {e}")

        st.markdown("### 검증 히스토리 테이블")
        rows = build_validation_history_table(history)
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(f"전체 {len(history)}개 스텝")
        else:
            st.info("히스토리가 없습니다.")

        summary = result.get("summary") or {}
        st.markdown("#### Cold Start 요약")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("총 스텝", summary.get("total_steps", 0))
        c2.metric("총 예측", summary.get("total_predictions", 0))
        c3.metric("총 실패", summary.get("total_failures", 0))
        c4.metric("스킵", summary.get("total_skipped", 0))
        c5.metric("정확도", f"{summary.get('accuracy', 0):.1f}%")

        st.markdown("#### 결과 저장")
        if st.session_state.get("flow_freq_win2_last_saved"):
            last = st.session_state.flow_freq_win2_last_saved
            st.success(f"**저장됨** · run_id: `{last.get('run_id', '')}`")
        if st.button("결과 저장", key="freq_win2_save_results", type="secondary", use_container_width=True):
            try:
                run_id = save_live_run_results(
                    run_meta={
                        "hypothesis": "first_anchor_window9_freq_win2",
                        "window_sizes": list(WINDOW_SIZES),
                        "table_threshold": TABLE_THRESHOLD,
                        "min_confidence_freq": state.get("min_confidence_freq", MIN_CONFIDENCE_FREQ),
                        "min_win_rate_pct": state.get("min_win_rate_pct", MIN_WIN_RATE_PCT),
                        "grid_string": gs,
                    },
                    history=history,
                    summary=summary,
                )
                st.session_state.flow_freq_win2_last_saved = {"run_id": run_id}
                st.rerun()
            except Exception as e:
                st.error(f"저장 실패: {e}")


if __name__ == "__main__":
    main()
