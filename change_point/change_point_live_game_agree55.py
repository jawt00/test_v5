"""
새로운 가설 (윈도우 9·10 agree55) 라이브 게임 앱.

- Cold Start → State Handoff → Live Loop (기존 플로우와 동일).
- 예측: simulation_predictions_change_point에서 빈도·가중치 둘 다 조회,
  예측값 일치 + 빈도/가중치 신뢰도 모두 임계값 이상일 때만 사용 (테이블 threshold=0).
- 윈도우 9·10만 사용. 앵커 중첩 시 이전 앵커만 검증(validated_positions).
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
    page_title="새로운 가설 (agree55) 라이브 게임",
    page_icon="🎯",
    layout="wide",
)

# 새로운 가설: 윈도우 9·10만, 빈도/가중치 일치·신뢰도 둘 다 충족
WINDOW_SIZES = (9, 10)
TABLE_THRESHOLD = 0  # 테이블 조회 시 항상 0
MIN_CONFIDENCE_FREQ = 51
MIN_CONFIDENCE_WEIGHT = 51
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


def _query_agree55_prediction(conn, window_size: int, prefix: str, min_freq: int, min_weight: int):
    """
    테이블에서 빈도·가중치 둘 다 조회 후 agree55 조건 적용.
    조건: pred_freq==pred_weight, conf_freq>=min_freq, conf_weight>=min_weight.
    반환: (predicted, confidence, skip_reason) 또는 (None, 0.0, skip_reason).
    """
    q = """
        SELECT method, predicted_value, confidence
        FROM simulation_predictions_change_point
        WHERE window_size = ? AND prefix = ? AND threshold = ?
    """
    df = pd.read_sql_query(q, conn, params=[window_size, prefix, TABLE_THRESHOLD])
    by_method = {}
    for _, row in df.iterrows():
        by_method[row["method"]] = row
    freq_row = by_method.get("빈도 기반")
    weight_row = by_method.get("가중치 기반")
    if freq_row is None or weight_row is None:
        return None, 0.0, "예측 테이블에 값 없음" if (freq_row is None and weight_row is None) else "빈도/가중치 중 하나 없음"
    pred_freq = freq_row["predicted_value"]
    pred_weight = weight_row["predicted_value"]
    conf_freq = freq_row["confidence"] if freq_row["confidence"] is not None else 0.0
    conf_weight = weight_row["confidence"] if weight_row["confidence"] is not None else 0.0
    if pred_freq != pred_weight:
        return None, 0.0, "빈도/가중치 예측값 불일치"
    if conf_freq < min_freq or conf_weight < min_weight:
        return None, 0.0, f"신뢰도 부족 (빈도 {conf_freq:.1f}% / 가중치 {conf_weight:.1f}%)"
    return pred_freq, conf_freq, None


# -----------------------------------------------------------------------------
# Step 1: Cold Start — 앵커 추출 → 전수 검증 (agree55 규칙 + 앵커 중첩 시 이전만)
# -----------------------------------------------------------------------------
def cold_start(grid_string: str, min_confidence_freq: int = MIN_CONFIDENCE_FREQ, min_confidence_weight: int = MIN_CONFIDENCE_WEIGHT):
    """
    새로운 가설(agree55): 윈도우 9·10만, 빈도/가중치 일치·신뢰도 둘 다 충족 시만 예측 사용.
    앵커 중첩 시 validated_positions로 이전 앵커만 검증.
    """
    min_ws = min(WINDOW_SIZES)
    if len(grid_string) < MIN_GRID_LENGTH:
        return {
            "state": {"current_pos": 0, "active_anchor_idx": 0, "anchor_failure_count": 0, "next_window_size": 9, "anchors": [], "search_from": 0, "min_confidence_freq": min_confidence_freq, "min_confidence_weight": min_confidence_weight},
            "history": [],
            "grid_string": grid_string,
            "summary": {"total_steps": 0, "total_failures": 0, "total_predictions": 0, "total_skipped": 0, "accuracy": 0.0},
        }

    anchors = _anchors_from_grid_string(grid_string)
    if not anchors:
        return {
            "state": {"current_pos": 0, "active_anchor_idx": 0, "anchor_failure_count": 0, "next_window_size": 9, "anchors": [], "search_from": 0, "min_confidence_freq": min_confidence_freq, "min_confidence_weight": min_confidence_weight},
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
        last_window_used = None
        anchor_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0
        max_ws = max(WINDOW_SIZES)
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
                    last_window_used = (window_size - 1) if window_size > min_ws else None
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

                predicted, confidence, skip_reason = _query_agree55_prediction(conn, window_size, prefix, min_confidence_freq, min_confidence_weight)
                validated_positions.add(pos)

                if predicted is None:
                    total_skipped += 1
                    current_pos = max(current_pos, pos + 1)
                    history.append({"step": total_steps, "position": pos, "anchor": next_anchor, "window_size": window_size, "prefix": prefix, "predicted": None, "actual": actual, "is_correct": None, "confidence": 0.0, "skipped": True, "skip_reason": skip_reason or "예측 없음"})
                    continue

                last_window_used = window_size
                ok = predicted == actual
                total_predictions += 1
                if not ok:
                    fail_count += 1
                    total_failures += 1
                    last_mismatched_pos = pos
                else:
                    anchor_success = True
                    fail_count = 0

                history.append({"step": total_steps, "position": pos, "anchor": next_anchor, "window_size": window_size, "prefix": prefix, "predicted": predicted, "actual": actual, "is_correct": ok, "confidence": confidence, "skipped": False})

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
            "min_confidence_freq": min_confidence_freq,
            "min_confidence_weight": min_confidence_weight,
        }
        acc = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        summary = {"total_steps": total_steps, "total_failures": total_failures, "total_predictions": total_predictions, "total_skipped": total_skipped, "accuracy": acc}
        return {"state": state, "history": history, "grid_string": grid_string, "summary": summary}
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# Step 3: Live Loop — 예측 노출 / 단일 스텝 검증 (agree55)
# -----------------------------------------------------------------------------
def predict_next(state: dict, grid_string: str):
    """다음 포지션(len(grid_string))에 대한 agree55 예측 조회."""
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
    min_weight = state.get("min_confidence_weight", MIN_CONFIDENCE_WEIGHT)
    conn = get_change_point_db_connection()
    try:
        predicted, confidence, skip_reason = _query_agree55_prediction(conn, window, prefix, min_freq, min_weight)
        if predicted is None:
            return {"predicted": None, "confidence": 0.0, "window_size": window, "prefix": prefix, "anchor": anchor, "skipped": True, "skip_reason": skip_reason}
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
        if aidx < len(anchors):
            old_anchor_pos = anchors[aidx]
            new_aidx = next((i for i in range(len(new_anchors)) if new_anchors[i] == old_anchor_pos), len(new_anchors))
            if new_aidx >= len(new_anchors):
                new_aidx = _first_anchor_from_position(new_anchors, search_from)
        else:
            new_aidx = _first_anchor_from_position(new_anchors, search_from)
        new_history = history
        new_state = {
            "current_pos": next_pos,
            "active_anchor_idx": new_aidx,
            "anchor_failure_count": fc,
            "next_window_size": state.get("next_window_size", 9),
            "anchors": new_anchors,
            "search_from": search_from,
            "min_confidence_freq": state.get("min_confidence_freq", MIN_CONFIDENCE_FREQ),
            "min_confidence_weight": state.get("min_confidence_weight", MIN_CONFIDENCE_WEIGHT),
        }
        return {"state": new_state, "history": new_history, "grid_string": new_grid_string, "step_result": {"skipped": True, "waiting": new_aidx >= len(new_anchors)}}

    ok = predicted.lower() == actual
    new_row = {
        "step": step_num,
        "position": position,
        "anchor": anchor,
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

    if ok:
        new_search_from = next_pos
        new_anchor_idx = _first_anchor_from_position(new_anchors, new_search_from)
        new_fc = 0
        new_next_window = 9
    else:
        new_fc = fc + 1
        if new_fc >= MAX_CONSECUTIVE_FAILURES:
            new_search_from = next_pos
            new_anchor_idx = _first_anchor_from_position(new_anchors, new_search_from)
            new_fc = 0
            new_next_window = 9
        else:
            new_search_from = search_from
            old_val = anchors[aidx] if aidx < len(anchors) else None
            new_anchor_idx = next((i for i in range(len(new_anchors)) if new_anchors[i] == old_val), len(new_anchors))
            if new_anchor_idx >= len(new_anchors):
                new_anchor_idx = min(aidx, len(new_anchors) - 1)
            rest = [w for w in WINDOW_SIZES if w > window_size]
            new_next_window = rest[0] if rest else 9

    new_state = {
        "current_pos": next_pos,
        "active_anchor_idx": new_anchor_idx,
        "anchor_failure_count": new_fc,
        "next_window_size": new_next_window,
        "anchors": new_anchors,
        "search_from": new_search_from,
        "min_confidence_freq": state.get("min_confidence_freq", MIN_CONFIDENCE_FREQ),
        "min_confidence_weight": state.get("min_confidence_weight", MIN_CONFIDENCE_WEIGHT),
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
    """히스토리 테이블 행 (최신순)."""
    rows = []
    for e in history or []:
        ok = e.get("is_correct")
        ms = "✅" if ok else ("❌" if ok is False else "-")
        pred = e.get("predicted")
        skip = e.get("skipped", False)
        reason = e.get("skip_reason", "")
        pm = f"⏭️ ({reason})" if skip and reason else ("⏭️" if skip else "")
        disp = f"{pred}{pm}" if pred else (f"-{pm}" if skip else "-")
        rows.append({
            "Step": e.get("step", 0),
            "Position": e.get("position", ""),
            "Anchor": e.get("anchor", ""),
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
    st.title("🎯 새로운 가설 (윈도우 9·10 agree55) 라이브 게임")
    st.markdown("**Cold Start → State Handoff → Live Loop** · 예측은 빈도/가중치 일치·신뢰도 둘 다 충족 시만 사용 (테이블 threshold=0)")

    if "flow_agree55_result" not in st.session_state:
        st.session_state.flow_agree55_result = None

    with st.sidebar:
        st.markdown("### agree55 신뢰도 조건")
        min_freq = st.number_input("빈도 신뢰도 최소 (%)", 0, 100, MIN_CONFIDENCE_FREQ, key="agree55_min_freq")
        min_weight = st.number_input("가중치 신뢰도 최소 (%)", 0, 100, MIN_CONFIDENCE_WEIGHT, key="agree55_min_weight")
        st.caption("게임 시작 시 위 값으로 Cold Start 실행됩니다.")

    st.markdown("---")
    st.markdown("## 📝 Grid String 입력")

    grid_input = st.text_area(
        "Grid String",
        key="agree55_flow_grid",
        height=80,
        placeholder="예: bbppbppbbp...",
        help="Cold Start 시 사용할 grid_string.",
    )

    col_start, col_reset, _ = st.columns([1, 1, 4])
    with col_start:
        if st.button("🎮 게임 시작 (Cold Start)", type="primary", use_container_width=True, key="agree55_btn_start"):
            s = (grid_input or "").strip()
            if not s:
                st.warning("Grid String을 입력하세요.")
            elif len(s) < MIN_GRID_LENGTH:
                st.warning(f"길이는 최소 {MIN_GRID_LENGTH}글자 이상이어야 합니다.")
            else:
                with st.spinner("Cold Start 검증 실행 중..."):
                    try:
                        result = cold_start(s, min_confidence_freq=min_freq, min_confidence_weight=min_weight)
                        st.session_state.flow_agree55_result = result
                        st.rerun()
                    except Exception as e:
                        st.error(f"Cold Start 실패: {e}")
    with col_reset:
        if st.button("🔄 초기화", use_container_width=True, key="agree55_btn_reset"):
            st.session_state.flow_agree55_result = None
            st.session_state.pop("flow_agree55_last_saved", None)
            st.rerun()

    result = st.session_state.flow_agree55_result
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
        aidx_label = f"a{aidx}" if aidx < len(anchors_now) else f"{aidx} (다음 앵커 대기)"
        st.markdown(
            f"**current_pos** = {cp} · **앵커** = {aidx} ({aidx_label}) · "
            f"failure_count = {fc} · next_window = {nw} · **search_from** = {sf}"
        )
        st.caption("윈도우 9·10만 사용 · 앵커 중첩 시 이전 앵커만 검증")

        next_pred = predict_next(state, gs)
        st.markdown("**다음 예측값** (다음 포지션 = len(grid_string))")
        aidx_now = state.get("active_anchor_idx", 0)
        if aidx_now >= len(anchors_now):
            st.info("⏳ **다음 앵커가 생길 때까지 입력 대기** (B/P 입력 시 anchors 갱신 후 재탐색)")
        elif next_pred.get("skipped") or next_pred.get("predicted") is None:
            anchor = next_pred.get("anchor")
            reason = next_pred.get("skip_reason", "")
            if anchor is not None:
                st.info(f"앵커: **{anchor}** · 예측 없음 ({reason or '조건 미충족'})")
            else:
                st.info("예측 없음")
        else:
            pv = next_pred.get("predicted", "")
            conf = next_pred.get("confidence", 0.0)
            ws = next_pred.get("window_size")
            anchor = next_pred.get("anchor")
            st.markdown(f"예측: **{pv}** · 신뢰도: **{conf:.1f}%** · Anchor: {anchor} · Window: {ws}")

        st.caption("B / P 입력 (단일 스텝 검증)")
        col_b, col_p, _ = st.columns([1, 1, 4])
        with col_b:
            if st.button("🔴 B", key="agree55_append_b", use_container_width=True):
                try:
                    step_out = live_step(state, gs, history, "b")
                    st.session_state.flow_agree55_result = {
                        "state": step_out["state"],
                        "history": step_out["history"],
                        "grid_string": step_out["grid_string"],
                        "summary": result.get("summary"),
                    }
                    st.rerun()
                except Exception as e:
                    st.error(f"live_step 실패: {e}")
        with col_p:
            if st.button("🔵 P", key="agree55_append_p", use_container_width=True):
                try:
                    step_out = live_step(state, gs, history, "p")
                    st.session_state.flow_agree55_result = {
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
        if st.session_state.get("flow_agree55_last_saved"):
            last = st.session_state.flow_agree55_last_saved
            st.success(f"**저장됨** · run_id: `{last.get('run_id', '')}`")
        if st.button("결과 저장", key="agree55_save_results", type="secondary", use_container_width=True):
            try:
                run_id = save_live_run_results(
                    run_meta={
                        "hypothesis": "first_anchor_window9_10_agree55",
                        "window_sizes": list(WINDOW_SIZES),
                        "table_threshold": TABLE_THRESHOLD,
                        "min_confidence_freq": state.get("min_confidence_freq", MIN_CONFIDENCE_FREQ),
                        "min_confidence_weight": state.get("min_confidence_weight", MIN_CONFIDENCE_WEIGHT),
                        "grid_string": gs,
                    },
                    history=history,
                    summary=summary,
                )
                st.session_state.flow_agree55_last_saved = {"run_id": run_id}
                st.rerun()
            except Exception as e:
                st.error(f"저장 실패: {e}")


if __name__ == "__main__":
    main()
