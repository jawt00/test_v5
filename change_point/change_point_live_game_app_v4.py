"""
V4 라이브 게임 앱 - 단계별 새로 구현

기존 코드 활용 없이, grid_string 입력만 있는 상태에서 시작하여
요구사항을 단계별로 반영하며 새롭게 구현합니다.

- 1) 게임 시작 / 초기화 버튼
- 2) 입력 스트링에 대해 V3 검증 실행 (hypothesis_module 검증 로직 복제)
- 3) 검증 상세 히스토리 테이블 표시
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st
import pandas as pd

from svg_parser_module import get_change_point_db_connection

st.set_page_config(
    page_title="Change-point V4 라이브 게임",
    page_icon="🎮",
    layout="wide",
)

WINDOW_SIZES = (9, 10, 11, 12)  # 윈도우 13,14 제거


def validate_grid_string_v3_cp(
    grid_string: str,
    window_sizes=(9, 10, 11, 12),
    method="빈도 기반",
    threshold=0,
):
    """
    입력된 grid_string에 대해 V3 검증 수행.
    (change_point_hypothesis_module.validate_first_anchor_extended_window_v3_cp 로직 복제)
    - grid_string을 인자로 직접 받고, DB에서 읽지 않음.
    - simulation_predictions_change_point 테이블에서 예측값 조회.
    - REQ-101: current_pos 이후 가장 빠른 앵커부터 검증
    - REQ-102: 윈도우 9,10,11,12 순차 검증 (13,14 제거)
    - RULE-1: 적중 시 즉시 다음 앵커로
    - RULE-2: 3회 연속 불일치 시 해당 앵커 종료 후 다음 앵커로
    """
    conn = get_change_point_db_connection()
    try:
        min_ws = min(window_sizes)
        # 최소 윈도우만큼은 있어야 첫 스텝 검증 가능 (position = anchor + window - 1 이므로 len >= min_ws 필요)
        if len(grid_string) < min_ws:
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
                "anchors": [],
            }

        anchors = []
        for i in range(len(grid_string) - 1):
            if grid_string[i] != grid_string[i + 1]:
                anchors.append(i)
        anchors = sorted(list(set(anchors)))

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
                "anchors": anchors,
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

        max_ws = max(window_sizes)
        while current_pos < len(grid_string) and anchor_idx < len(anchors):
            # 해당 앵커가 커버할 수 있는 최대 position(anchor+max_ws-1)을 이미 지났을 때만 skip
            while anchor_idx < len(anchors) and anchors[anchor_idx] + max_ws - 1 < current_pos:
                anchor_idx += 1
            if anchor_idx >= len(anchors):
                break

            next_anchor = anchors[anchor_idx]
            anchor_consecutive_failures = 0
            anchor_success = False
            last_mismatched_pos = None
            anchor_processed_any = False
            exit_for_pos_beyond = False

            for window_size in window_sizes:
                pos = next_anchor + window_size - 1
                if pos >= len(grid_string):
                    exit_for_pos_beyond = True
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
                df_pred = pd.read_sql_query(
                    q, conn, params=[window_size, prefix, method, threshold]
                )

                if len(df_pred) == 0:
                    total_skipped += 1
                    # 스킵이어도 이 포지션은 지남 → 이미 지난 포지션으로 역방향 예측하는 일 방지
                    current_pos = max(current_pos, pos + 1)
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
                    break
                if anchor_consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    current_pos = (last_mismatched_pos + 1) if last_mismatched_pos is not None else (pos + 1)
                    anchor_idx += 1
                    break

            # 문자열 길이 때문에 for가 끊긴 경우: 앵커를 바꾸지 않고 while 종료 (다음 예측은 같은 앵커·다음 윈도우)
            if exit_for_pos_beyond:
                break
            if not anchor_success and anchor_consecutive_failures < MAX_CONSECUTIVE_FAILURES:
                if anchor_processed_any and last_mismatched_pos is not None:
                    current_pos = last_mismatched_pos + 1
                elif anchor_processed_any:
                    current_pos = min(next_anchor + max(window_sizes) - 1, len(grid_string) - 1) + 1
                else:
                    current_pos = min(next_anchor + max(window_sizes) - 1, len(grid_string) - 1) + 1
                anchor_idx += 1

        acc = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        return {
            "max_consecutive_failures": max_consecutive_failures,
            "total_steps": total_steps,
            "total_failures": total_failures,
            "total_predictions": total_predictions,
            "total_skipped": total_skipped,
            "accuracy": acc,
            "history": history,
            "final_current_pos": current_pos,
            "final_anchor_idx": anchor_idx,
            "final_anchor_consecutive_failures": anchor_consecutive_failures,
            "anchors": anchors,
        }
    finally:
        conn.close()


def predict_next_v3_cp(
    grid_string: str,
    window_sizes=(9, 10, 11, 12),
    method="빈도 기반",
    threshold=0,
    current_pos=0,
    anchor_idx=0,
    anchor_consecutive_failures=0,
):
    """
    현재 grid_string 기준으로 다음 예측값 반환 (position = len(grid_string)).
    검증이 넘긴 (current_pos, anchor_idx)를 그대로 사용: anchor_idx가 "다음에 쓸 앵커"이므로
    그 앵커에서 윈도우 9~14 순차 시도, required_pos == position 인 첫 사용 가능 예측 반환.
    """
    position = len(grid_string)
    anchors = _anchors_from_grid_string(grid_string)
    if not anchors:
        return {"predicted": None, "confidence": 0.0, "window_size": None, "prefix": None, "anchor": None, "skipped": True}

    # 검증에서 넘긴 final_anchor_idx = "다음에 쓸 앵커"이므로 그대로 사용.
    # (앵커 위치 < current_pos 여도, position=len(gs)에 대한 예측은 그 앵커·윈도우 조합으로 가능)
    if anchor_idx >= len(anchors):
        return {"predicted": None, "confidence": 0.0, "window_size": None, "prefix": None, "anchor": None, "skipped": True}

    next_anchor = anchors[anchor_idx]
    conn = get_change_point_db_connection()
    try:
        for window_size in window_sizes:
            required_pos = next_anchor + window_size - 1
            if required_pos != position or required_pos < current_pos:
                continue
            prefix_len = window_size - 1
            if position < prefix_len:
                continue
            prefix = grid_string[position - prefix_len : position]
            q = """
                SELECT predicted_value, confidence FROM simulation_predictions_change_point
                WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ? LIMIT 1
            """
            df = pd.read_sql_query(q, conn, params=[window_size, prefix, method, threshold])
            if len(df) == 0:
                continue
            row = df.iloc[0]
            return {
                "predicted": row["predicted_value"],
                "confidence": row["confidence"],
                "window_size": window_size,
                "prefix": prefix,
                "anchor": next_anchor,
                "skipped": False,
            }
        return {"predicted": None, "confidence": 0.0, "window_size": None, "prefix": None, "anchor": next_anchor, "skipped": True}
    finally:
        conn.close()


def _anchors_from_grid_string(grid_string: str):
    """grid_string에서 change-point(앵커) 위치 리스트 반환."""
    if not grid_string or len(grid_string) < 2:
        return []
    return sorted(set(i for i in range(len(grid_string) - 1) if grid_string[i] != grid_string[i + 1]))


def render_grid_string_and_anchors(grid_string: str):
    """Grid String 및 앵커 위치를 시각화 (연한 파란색 = 앵커)."""
    if not grid_string:
        st.caption("(Grid String 없음)")
        return
    anchors = _anchors_from_grid_string(grid_string)
    parts = []
    for i, c in enumerate(grid_string):
        if i in anchors:
            parts.append(f"<span style='background-color:#ADD8E6'>{c}</span>")
        else:
            parts.append(c)
    st.markdown(
        f"<div style='font-size:20px;font-family:monospace;padding:10px;border:1px solid #ddd;border-radius:5px;'>{''.join(parts)}</div>",
        unsafe_allow_html=True,
    )
    idx_line = "".join(str(i % 10) for i in range(len(grid_string)))
    st.markdown(
        f"<div style='font-size:12px;font-family:monospace;color:#666;'>{idx_line}</div>",
        unsafe_allow_html=True,
    )
    st.caption("연한 파란색: 앵커(변화점)")


def build_validation_history_table(history):
    """검증 history를 hypothesis_test_app 스타일의 테이블 행 리스트로 변환. 스텝 역순(최신순 상단)."""
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
    # 스텝 역순: 최신순이 상단
    rows.sort(key=lambda r: r["Step"], reverse=True)
    return rows


def main():
    st.title("🎮 Change-point V4 라이브 게임")
    st.markdown("**단계별 새로 구현 (grid_string 입력 → 게임 시작 → 검증 상세 히스토리)**")

    if "v4_validation_result" not in st.session_state:
        st.session_state.v4_validation_result = None

    st.markdown("---")
    st.markdown("## 📝 Grid String 입력")

    grid_input = st.text_area(
        "Grid String",
        key="v4_grid",
        height=80,
        placeholder="예: bbppbppbbp...",
        help="라이브 게임에서 사용할 grid_string을 입력하세요.",
    )

    col_start, col_reset, _ = st.columns([1, 1, 4])
    with col_start:
        if st.button("🎮 게임 시작", type="primary", use_container_width=True, key="v4_btn_start"):
            s = (grid_input or "").strip()
            if not s:
                st.warning("Grid String을 입력하세요.")
            elif len(s) < min(WINDOW_SIZES):
                st.warning(f"길이는 최소 {min(WINDOW_SIZES)} 이상이어야 합니다.")
            else:
                with st.spinner("검증 실행 중..."):
                    try:
                        result = validate_grid_string_v3_cp(
                            grid_string=s,
                            window_sizes=WINDOW_SIZES,
                            method="빈도 기반",
                            threshold=0,
                        )
                        result["grid_string"] = s
                        st.session_state.v4_validation_result = result
                        st.rerun()
                    except Exception as e:
                        st.error(f"검증 실패: {e}")
    with col_reset:
        if st.button("🔄 초기화", use_container_width=True, key="v4_btn_reset"):
            st.session_state.v4_validation_result = None
            st.rerun()

    result = st.session_state.v4_validation_result
    if result is not None:
        st.markdown("---")
        st.markdown("## ✅ 검증 상세 히스토리")

        st.markdown("### Grid String 및 앵커")
        render_grid_string_and_anchors(result.get("grid_string") or "")

        # 현재 grid_string 기준 다음 예측값 (V3 검증 상태 사용)
        gs = result.get("grid_string") or ""
        next_pred = predict_next_v3_cp(
            grid_string=gs,
            window_sizes=WINDOW_SIZES,
            method="빈도 기반",
            threshold=0,
            current_pos=result.get("final_current_pos", 0),
            anchor_idx=result.get("final_anchor_idx", 0),
            anchor_consecutive_failures=result.get("final_anchor_consecutive_failures", 0),
        )
        st.markdown("**다음 예측값**")
        if next_pred.get("skipped") or next_pred.get("predicted") is None:
            st.info("예측 없음 (스킵 또는 해당 prefix 없음)")
        else:
            pv = next_pred.get("predicted", "")
            conf = next_pred.get("confidence", 0.0)
            ws = next_pred.get("window_size")
            anchor = next_pred.get("anchor")
            st.markdown(f"예측: **{pv}** · 신뢰도: **{conf:.1f}%** · Anchor: {anchor} · Window: {ws}")

        st.caption("b / p 입력:")
        col_b, col_p, _ = st.columns([1, 1, 4])
        with col_b:
            if st.button("🔴 B", key="v4_append_b", use_container_width=True):
                new_s = (result.get("grid_string") or "") + "b"
                with st.spinner("검증 갱신 중..."):
                    try:
                        new_result = validate_grid_string_v3_cp(
                            grid_string=new_s,
                            window_sizes=WINDOW_SIZES,
                            method="빈도 기반",
                            threshold=0,
                        )
                        new_result["grid_string"] = new_s
                        st.session_state.v4_validation_result = new_result
                        st.rerun()
                    except Exception as e:
                        st.error(f"갱신 실패: {e}")
        with col_p:
            if st.button("🔵 P", key="v4_append_p", use_container_width=True):
                new_s = (result.get("grid_string") or "") + "p"
                with st.spinner("검증 갱신 중..."):
                    try:
                        new_result = validate_grid_string_v3_cp(
                            grid_string=new_s,
                            window_sizes=WINDOW_SIZES,
                            method="빈도 기반",
                            threshold=0,
                        )
                        new_result["grid_string"] = new_s
                        st.session_state.v4_validation_result = new_result
                        st.rerun()
                    except Exception as e:
                        st.error(f"갱신 실패: {e}")

        st.markdown("### 검증 히스토리 테이블")
        rows = build_validation_history_table(result.get("history", []))
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(f"전체 {len(result.get('history', []))}개 스텝")
        else:
            st.info("히스토리가 없습니다.")

        st.markdown("#### 요약")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("최대 연속 불일치", result.get("max_consecutive_failures", 0))
        c2.metric("총 스텝", result.get("total_steps", 0))
        c3.metric("총 예측", result.get("total_predictions", 0))
        c4.metric("스킵", result.get("total_skipped", 0))
        c5.metric("정확도", f"{result.get('accuracy', 0):.1f}%")


if __name__ == "__main__":
    main()
