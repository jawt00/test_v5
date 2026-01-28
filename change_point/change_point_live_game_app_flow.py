"""
Cold Start → State Handoff → Live Loop 플로우 전용 라이브 게임 앱.

- Step 1 (Cold Start): 긴 grid_string 입력 시 앵커 추출 → 전수 검증 → 히스토리 생성
- Step 2 (State Handoff): 루프 종료 시 current_pos, active_anchor_idx, anchor_failure_count, next_window_size 유지
- Step 3 (Live Loop): B/P 입력 시 단일 스텝 검증만 수행, 히스토리에 한 행만 추가 (전수 검증 없음)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st
import pandas as pd

from svg_parser_module import get_change_point_db_connection

st.set_page_config(
    page_title="Change-point 플로우 라이브 게임",
    page_icon="🎮",
    layout="wide",
)

WINDOW_SIZES = (9, 10, 11, 12, 13, 14)
METHOD = "빈도 기반"
THRESHOLD = 0
MAX_CONSECUTIVE_FAILURES = 3


def _anchors_from_grid_string(grid_string: str):
    """grid_string에서 change-point(앵커) 위치 리스트 반환."""
    if not grid_string or len(grid_string) < 2:
        return []
    return sorted(set(i for i in range(len(grid_string) - 1) if grid_string[i] != grid_string[i + 1]))


def _first_anchor_from_position(anchors: list, from_pos: int) -> int:
    """종료 포지션(from_pos)부터 다음 앵커 탐색. anchors[i] >= from_pos 인 첫 인덱스 i. 없으면 len(anchors)."""
    i = 0
    while i < len(anchors) and anchors[i] < from_pos:
        i += 1
    return i


def _first_anchor_covering_position(anchors: list, pos: int) -> int:
    """포지션 pos를 윈도우 [9..14]로 커버할 수 있는 첫 앵커의 인덱스. 없으면 len(anchors)."""
    min_ws, max_ws = min(WINDOW_SIZES), max(WINDOW_SIZES)
    for i in range(len(anchors)):
        a = anchors[i]
        if a + min_ws - 1 <= pos <= a + max_ws - 1:
            return i
    return len(anchors)


# -----------------------------------------------------------------------------
# Step 1: Cold Start — 단계별 구현 (앵커 추출 → 전수 검증 → State Handoff)
# -----------------------------------------------------------------------------
def cold_start(grid_string: str):
    """
    [단계 1] 앵커 추출
    [단계 2] current_pos=0, 다음 앵커 = first anchor >= 0
    [단계 3] 앵커별 검증 루프
      - 종료조건 충족(적중/3패): 종료 포지션부터 다음 앵커 탐색 → active_anchor_idx 갱신
      - 종료조건 미충족(문자열 끝): anchor_idx 변경 없음 → 현재 앵커 유지
    """
    min_ws = min(WINDOW_SIZES)
    if len(grid_string) < min_ws:
        return {
            "state": {"current_pos": 0, "active_anchor_idx": 0, "anchor_failure_count": 0, "next_window_size": 9, "anchors": []},
            "history": [],
            "grid_string": grid_string,
            "summary": {"total_steps": 0, "total_failures": 0, "total_predictions": 0, "total_skipped": 0, "accuracy": 0.0},
        }

    # [단계 1] 앵커 전체 추출
    anchors = _anchors_from_grid_string(grid_string)
    if not anchors:
        return {
            "state": {"current_pos": 0, "active_anchor_idx": 0, "anchor_failure_count": 0, "next_window_size": 9, "anchors": []},
            "history": [],
            "grid_string": grid_string,
            "summary": {"total_steps": 0, "total_failures": 0, "total_predictions": 0, "total_skipped": 0, "accuracy": 0.0},
        }

    conn = get_change_point_db_connection()
    try:
        history = []
        current_pos = 0
        # [단계 2] 처음엔 "current_pos(0) 이상인 첫 앵커" = anchor_idx
        anchor_idx = _first_anchor_from_position(anchors, current_pos)
        anchor_completed = True
        last_window_used = None
        anchor_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0
        max_ws = max(WINDOW_SIZES)

        # [단계 3] 앵커별 검증 루프
        while current_pos < len(grid_string) and anchor_idx < len(anchors):
            next_anchor = anchors[anchor_idx]
            anchor_success = False
            last_mismatched_pos = None
            fail_count = 0
            exited_string_end = False
            did_finish_anchor = False  # 적중 또는 3패로 이 앵커 종료 → while 탈출 후 state 전달

            for window_size in WINDOW_SIZES:
                pos = next_anchor + window_size - 1
                if pos >= len(grid_string):
                    # 케이스 A: 문자열 끝 → 종료조건 미충족. anchor_idx 변경 없음(현재 앵커 유지)
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
                    history.append({"step": total_steps, "position": pos, "anchor": next_anchor, "window_size": window_size, "prefix": prefix, "predicted": None, "actual": actual, "is_correct": None, "confidence": 0.0, "skipped": True, "skip_reason": "예측 테이블에 값 없음"})
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

                history.append({"step": total_steps, "position": pos, "anchor": next_anchor, "window_size": window_size, "prefix": prefix, "predicted": predicted, "actual": actual, "is_correct": ok, "confidence": confidence, "skipped": False})

                # 케이스 B: 적중 → 종료 포지션(pos)에서 앵커 리스트 갱신 후, 그 리스트에서 다음 앵커 탐색
                if ok:
                    current_pos = pos + 1
                    anchor_idx = _first_anchor_from_position(anchors, pos)
                    if anchor_idx >= len(anchors):
                        anchors.append(current_pos)  # 종료 포지션을 다음 앵커로 추가(리스트 업데이트)
                        anchor_idx = len(anchors) - 1
                    anchor_consecutive_failures = 0
                    anchor_completed = True
                    did_finish_anchor = True
                    break

                # 케이스 C: 3연속 불일치 → 종료 포지션(ref_pos)에서 앵커 리스트 갱신 후, 그 리스트에서 다음 앵커 탐색
                if fail_count >= MAX_CONSECUTIVE_FAILURES:
                    ref_pos = last_mismatched_pos if last_mismatched_pos is not None else pos
                    current_pos = ref_pos + 1
                    anchor_idx = _first_anchor_from_position(anchors, ref_pos)
                    if anchor_idx >= len(anchors):
                        anchors.append(current_pos)
                        anchor_idx = len(anchors) - 1
                    anchor_consecutive_failures = 0
                    anchor_completed = True
                    did_finish_anchor = True
                    break

            if did_finish_anchor:
                break  # 케이스 B/C: 종료조건 충족 → while 탈출, state 전달
            if exited_string_end:
                anchor_consecutive_failures = fail_count  # 현재 앵커까지의 연속 실패 수
                break  # 케이스 A: 문자열 끝(종료조건 미충족) → while 탈출, anchor_idx 그대로
            # for가 break 없이 끝남: 윈도우 다 돌았으나 성공/3패 아님 → 다음 앵커로
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
        }
        acc = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        summary = {"total_steps": total_steps, "total_failures": total_failures, "total_predictions": total_predictions, "total_skipped": total_skipped, "accuracy": acc}
        return {"state": state, "history": history, "grid_string": grid_string, "summary": summary}
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# Step 3: Live Loop — 예측 노출 / 단일 스텝 검증 / 히스토리 누적
# -----------------------------------------------------------------------------
def predict_next(state: dict, grid_string: str):
    """
    현재 상태 기준으로 '다음 포지션'(= len(grid_string))에 대한 예측을 DB에서 조회하여 반환.
    """
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

    conn = get_change_point_db_connection()
    try:
        q = """
            SELECT predicted_value, confidence FROM simulation_predictions_change_point
            WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ? LIMIT 1
        """
        df = pd.read_sql_query(q, conn, params=[window, prefix, METHOD, THRESHOLD])
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


def live_step(state: dict, grid_string: str, history: list, user_input: str):
    """
    사용자 입력(B/P) 한 글자에 대해 단일 스텝 검증만 수행, 히스토리에 한 행 추가.
    전수 검증 없음.
    반환: { "state": new_state, "history": extended_history, "grid_string": new_grid_string, "step_result": {...} }
    """
    new_grid_string = grid_string + user_input
    position = len(grid_string)  # 방금 예측했던 위치
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
        # 스킵: current_pos만 진행. 앵커 인덱스는 "방금 추가된 위치(next_pos-1) 이상인 첫 앵커" 한 규칙으로만 결정.
        next_pos = position + 1
        new_anchors = _anchors_from_grid_string(new_grid_string)
        new_aidx = _first_anchor_from_position(new_anchors, max(0, next_pos - 1))

        new_history = history
        new_state = {
            "current_pos": next_pos,
            "active_anchor_idx": new_aidx,
            "anchor_failure_count": fc,
            "next_window_size": state.get("next_window_size", 9),
            "anchors": new_anchors,
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
    new_anchors = _anchors_from_grid_string(new_grid_string)  # 실제값 입력될 때마다 앵커 리스트 업데이트

    if ok:
        new_anchor_idx = _first_anchor_from_position(new_anchors, next_pos)
        new_fc = 0
        new_next_window = 9
    else:
        new_fc = fc + 1
        if new_fc >= MAX_CONSECUTIVE_FAILURES:
            new_anchor_idx = _first_anchor_from_position(new_anchors, next_pos)
            new_fc = 0
            new_next_window = 9
        else:
            # 같은 앵커 유지 → 갱신된 리스트에서 이전 앵커 값(anchors[aidx])의 인덱스 찾기
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
    }

    return {
        "state": new_state,
        "history": new_history,
        "grid_string": new_grid_string,
        "step_result": {"is_correct": ok, "skipped": False},
    }


def render_grid_string_and_anchors(grid_string: str, anchors: list = None):
    """Grid String 및 앵커 위치 시각화. 각 위치별 간격·포지션 인덱스·앵커 인덱스 통일."""
    if not grid_string:
        st.caption("(Grid String 없음)")
        return
    if anchors is None:
        anchors = _anchors_from_grid_string(grid_string)
    # anchor_position -> anchor_idx
    pos_to_anchor_idx = {p: i for i, p in enumerate(anchors)}

    cell_style = "display:inline-block;min-width:1.5em;text-align:center;font-family:monospace;vertical-align:top;"
    box_style = "font-size:18px;padding:8px 10px;border:1px solid #ddd;border-radius:6px;background:#fafafa;line-height:1.5;"

    # 1행: 문자 (앵커 위치는 연한 파란색)
    chars = []
    for i, c in enumerate(grid_string):
        bg = "background:#ADD8E6;" if i in anchors else ""
        chars.append(f"<span style='{cell_style}{bg}'>{c}</span>")
    st.markdown(f"<div style='{box_style}'>{''.join(chars)}</div>", unsafe_allow_html=True)

    # 2행: 포지션 인덱스(0,1,...,9,10,11,...) — 2자리 고정으로 통일
    idx_cells = []
    for i in range(len(grid_string)):
        idx_cells.append(f"<span style='{cell_style}font-size:11px;color:#555;'>{i:2d}</span>")
    st.markdown(f"<div style='{box_style}font-size:11px;color:#555;'>{''.join(idx_cells)}</div>", unsafe_allow_html=True)

    # 3행: 앵커 인덱스 (앵커 위치에만 a0, a1, ... 표시)
    anchor_cells = []
    for i in range(len(grid_string)):
        if i in pos_to_anchor_idx:
            a_idx = pos_to_anchor_idx[i]
            anchor_cells.append(f"<span style='{cell_style}font-size:10px;color:#0066cc;font-weight:bold;'>a{a_idx}</span>")
        else:
            anchor_cells.append(f"<span style='{cell_style}font-size:10px;color:#ccc;'>·</span>")
    st.markdown(f"<div style='{box_style}font-size:10px;color:#0066cc;'>{''.join(anchor_cells)}</div>", unsafe_allow_html=True)

    st.caption("위: 문자 | 가운데: 포지션 인덱스(0~) | 아래: 앵커 인덱스(a0,a1,...). 연한 파란색=앵커(변화점)")


def build_validation_history_table(history):
    """히스토리를 테이블 행 리스트로 변환. 최신순 상단."""
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
    st.title("🎮 Change-point 플로우 라이브 게임")
    st.markdown("**Cold Start → State Handoff → Live Loop** (게임 시작 시 전수 검증, B/P 입력 시 단일 스텝만 검증)")

    if "flow_result" not in st.session_state:
        st.session_state.flow_result = None

    st.markdown("---")
    st.markdown("## 📝 Grid String 입력")

    grid_input = st.text_area(
        "Grid String",
        key="flow_grid",
        height=80,
        placeholder="예: bbppbppbbp...",
        help="Cold Start 시 사용할 grid_string.",
    )

    col_start, col_reset, _ = st.columns([1, 1, 4])
    with col_start:
        if st.button("🎮 게임 시작 (Cold Start)", type="primary", use_container_width=True, key="flow_btn_start"):
            s = (grid_input or "").strip()
            if not s:
                st.warning("Grid String을 입력하세요.")
            elif len(s) < min(WINDOW_SIZES):
                st.warning(f"길이는 최소 {min(WINDOW_SIZES)} 이상이어야 합니다.")
            else:
                with st.spinner("Cold Start 검증 실행 중..."):
                    try:
                        result = cold_start(s)
                        st.session_state.flow_result = result
                        st.rerun()
                    except Exception as e:
                        st.error(f"Cold Start 실패: {e}")
    with col_reset:
        if st.button("🔄 초기화", use_container_width=True, key="flow_btn_reset"):
            st.session_state.flow_result = None
            st.rerun()

    result = st.session_state.flow_result
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
        anchors_now = state.get("anchors") or []
        aidx_label = f"a{aidx}" if aidx < len(anchors_now) else f"{aidx} (다음 앵커 대기)"
        st.markdown(
            f"**포지션 인덱스** = {cp} · **앵커 인덱스** = {aidx} ({aidx_label}) · "
            f"anchor_failure_count = {fc} · next_window_size = {nw}"
        )
        st.caption("위 Grid의 ‘포지션 인덱스’·‘앵커 인덱스(a0,a1,…)’와 동일한 0-based 기준")

        next_pred = predict_next(state, gs)
        st.markdown("**다음 예측값** (다음 포지션 = len(grid_string))")
        anchors_now = state.get("anchors") or []
        aidx_now = state.get("active_anchor_idx", 0)
        if aidx_now >= len(anchors_now):
            st.info("⏳ **다음 앵커가 생길 때까지 입력을 기다리는 상태** (current_pos 이후 앵커 없음 · B/P 입력 시 anchors 갱신 후 다시 탐색)")
        elif next_pred.get("skipped") or next_pred.get("predicted") is None:
            anchor = next_pred.get("anchor")
            if anchor is not None:
                st.info(f"앵커: **{anchor}** · 예측 없음 (해당 position/prefix 없음)")
            else:
                st.info("예측 없음")
        else:
            pv = next_pred.get("predicted", "")
            conf = next_pred.get("confidence", 0.0)
            ws = next_pred.get("window_size")
            anchor = next_pred.get("anchor")
            st.markdown(f"예측: **{pv}** · 신뢰도: **{conf:.1f}%** · Anchor: {anchor} · Window: {ws}")

        st.caption("B / P 입력 (단일 스텝 검증만 수행, 전수 검증 없음)")
        col_b, col_p, _ = st.columns([1, 1, 4])
        with col_b:
            if st.button("🔴 B", key="flow_append_b", use_container_width=True):
                try:
                    step_out = live_step(state, gs, history, "b")
                    st.session_state.flow_result = {
                        "state": step_out["state"],
                        "history": step_out["history"],
                        "grid_string": step_out["grid_string"],
                        "summary": result.get("summary"),
                    }
                    st.rerun()
                except Exception as e:
                    st.error(f"live_step 실패: {e}")
        with col_p:
            if st.button("🔵 P", key="flow_append_p", use_container_width=True):
                try:
                    step_out = live_step(state, gs, history, "p")
                    st.session_state.flow_result = {
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


if __name__ == "__main__":
    main()
