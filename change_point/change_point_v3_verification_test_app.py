"""
V3 검증 테스트 앱.

- grid_string 입력 → 모듈의 V3 검증 로직 실행 → 시뮬레이션과 동일한 결과 표시
- 수정된 라이브 게임 앱의 Cold Start 히스토리와 동일한 결과가 나와야 함
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st
import pandas as pd

from change_point_hypothesis_module import validate_first_anchor_extended_window_v3_cp_from_string
from svg_parser_module import get_change_point_db_connection


st.set_page_config(
    page_title="V3 검증 테스트",
    page_icon="🧪",
    layout="wide",
)

WINDOW_SIZES = (9, 10, 11, 12, 13, 14)
METHOD = "빈도 기반"
THRESHOLD = 0


def _anchors_from_grid_string(grid_string: str):
    """grid_string에서 change-point(앵커) 위치 리스트 반환."""
    if not grid_string or len(grid_string) < 2:
        return []
    return sorted(set(i for i in range(len(grid_string) - 1) if grid_string[i] != grid_string[i + 1]))


def build_history_table(history):
    """히스토리를 테이블 행 리스트로 변환. 라이브 게임 앱과 동일한 포맷."""
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
    st.title("🧪 V3 검증 테스트")
    st.markdown("**grid_string 입력 → 시뮬레이션과 동일한 V3 검증 결과 표시**")
    st.caption("라이브 게임 앱(Cold Start)과 상세 히스토리가 동일해야 함")

    st.markdown("---")
    st.markdown("## 📝 Grid String 입력")
    st.caption("예측값은 simulation_predictions_change_point 테이블에서 조회합니다. (다른 도구로 먼저 생성 필요)")

    grid_input = st.text_area(
        "Grid String",
        key="v3_test_grid",
        height=100,
        placeholder="예: bbppbppbbpbbpp...",
        help="검증할 grid_string. 최소 14자 이상 권장.",
    )

    col_run, col_reset, col_check, _ = st.columns([1, 1, 1, 3])
    with col_run:
        run_clicked = st.button("▶️ 실행", type="primary", use_container_width=True, key="v3_btn_run")
    with col_reset:
        if st.button("🔄 초기화", use_container_width=True, key="v3_btn_reset"):
            if "v3_result" in st.session_state:
                del st.session_state["v3_result"]
            st.rerun()
    with col_check:
        if st.button("📋 예측 테이블 확인", use_container_width=True, key="v3_btn_check"):
            st.session_state["v3_show_table_check"] = True
            st.rerun()

    if run_clicked:
        s = (grid_input or "").strip()
        if not s:
            st.warning("Grid String을 입력하세요.")
        elif len(s) < min(WINDOW_SIZES):
            st.warning(f"길이는 최소 {min(WINDOW_SIZES)} 이상이어야 합니다.")
        else:
            with st.spinner("V3 검증 실행 중..."):
                try:
                    result = validate_first_anchor_extended_window_v3_cp_from_string(
                        grid_string=s,
                        window_sizes=WINDOW_SIZES,
                        method=METHOD,
                        threshold=THRESHOLD,
                        stop_on_match=False,
                    )
                    st.session_state["v3_result"] = result
                    st.rerun()
                except Exception as e:
                    st.error(f"실행 실패: {e}")
                    import traceback
                    st.code(traceback.format_exc())

    # 예측 테이블 확인
    if st.session_state.get("v3_show_table_check"):
        st.session_state["v3_show_table_check"] = False
        conn = get_change_point_db_connection()
        try:
            cnt = pd.read_sql_query("SELECT COUNT(*) as cnt FROM simulation_predictions_change_point", conn)
            n = cnt.iloc[0]["cnt"] if len(cnt) > 0 else 0
            st.info(f"simulation_predictions_change_point: {n:,}건")
            if n > 0:
                sample = pd.read_sql_query(
                    "SELECT window_size, prefix, predicted_value, confidence FROM simulation_predictions_change_point LIMIT 20",
                    conn,
                )
                st.dataframe(sample, use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(f"조회 실패: {e}")
        finally:
            conn.close()
        st.markdown("---")

    result = st.session_state.get("v3_result")
    if result is not None:
        history = result.get("history") or []
        gs = result.get("grid_string", "")

        st.markdown("---")
        st.markdown("## ✅ V3 검증 결과")

        # 요약
        st.markdown("### 요약")
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("총 스텝", result.get("total_steps", 0))
        c2.metric("총 예측", result.get("total_predictions", 0))
        c3.metric("총 실패", result.get("total_failures", 0))
        c4.metric("스킵", result.get("total_skipped", 0))
        c5.metric("최대 연속 실패", result.get("max_consecutive_failures", 0))
        c6.metric("정확도", f"{result.get('accuracy', 0):.1f}%")

        # Grid String 및 앵커
        st.markdown("### Grid String 및 앵커")
        anchors = _anchors_from_grid_string(gs)
        if gs:
            n = 35
            for start in range(0, len(gs), n):
                chunk = gs[start : start + n]
                anchor_in_chunk = [a for a in anchors if start <= a < start + len(chunk)]
                st.text(chunk)
                st.caption(f"포지션 {start}~{start + len(chunk) - 1} | 앵커: {anchor_in_chunk}")
        else:
            st.caption("(없음)")

        # 상세 히스토리 (라이브 게임 앱과 동일 포맷)
        st.markdown("### 상세 히스토리")
        rows = build_history_table(history)
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(f"전체 {len(history)}개 스텝 (라이브 게임 Cold Start와 동일 포맷)")
        else:
            st.info("히스토리가 없습니다.")

        # raw history (디버그용, 접기)
        with st.expander("📎 Raw History (비교용)"):
            st.json([{k: v for k, v in h.items() if k != "all_predictions"} for h in history])


if __name__ == "__main__":
    main()
