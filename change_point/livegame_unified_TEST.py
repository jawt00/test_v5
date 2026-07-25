"""
통합 라이브게임 TEST — W9_10 (list2) + W9_11 (list3) 병렬.

실행:
  streamlit run change_point/livegame_unified_TEST.py

- W9_10 → pattern_list2_TEST.db
- W9_11 → pattern_list3_TEST.db (ws11 → ws9_core lookup)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import streamlit as st

from livegame_engine import (
    MATCH_PASS,
    MIN_GRID_LENGTH,
    PASS_LABEL,
    _anchors_from_grid_string,
    _display_predicted_value,
    _empty_summary,
    _history_entry_visible,
    _match_label,
    build_current_state_table,
    build_validation_history_table_by_position,
    cold_start_for_mode,
    live_step_for_mode,
    predict_next_for_mode,
    render_grid_string_and_anchors,
    save_all_live_step_results,
    set_use_test_db,
)
from livegame_mode_registry import DISPLAY_ORDER, MODES, mode_label
from pattern_list_profiles import LIST2_TEST_PREDICTIONS_DB, LIST3_TEST_PREDICTIONS_DB

set_use_test_db(True)

st.set_page_config(
    page_title="TEST · 통합 라이브게임 (L2 W9_10 + L3 W9_11)",
    page_icon="🎮",
    layout="wide",
)


def main() -> None:
    st.title("통합 라이브게임 · TEST")
    st.caption(
        f"⚠ W9_10→L2 `{LIST2_TEST_PREDICTIONS_DB.name}` · "
        f"W9_11→L3 `{LIST3_TEST_PREDICTIONS_DB.name}`"
    )

    if "flow_result" not in st.session_state:
        st.session_state.flow_result = None
    if "flow_saved_step_keys" not in st.session_state:
        st.session_state.flow_saved_step_keys = set()

    grid_input = st.text_area(
        "Grid String",
        key="flow_grid_unified",
        height=56,
        placeholder="bbppbppbbp...",
    )

    col_start, col_reset, _ = st.columns([1, 1, 4])
    with col_start:
        if st.button("게임 시작 (Cold Start)", type="primary", use_container_width=True, key="flow_btn_start"):
            s = (grid_input or "").strip()
            if not s:
                st.warning("Grid String을 입력하세요.")
            elif len(s) < MIN_GRID_LENGTH:
                st.warning(f"길이는 최소 {MIN_GRID_LENGTH}글자 이상이어야 합니다.")
            else:
                with st.spinner("Cold Start (W9_10 + W9_11)..."):
                    try:
                        results = {}
                        for mode in MODES:
                            results[mode] = cold_start_for_mode(s, mode)
                        st.session_state.flow_result = {"grid_string": s, "results": results}
                        st.session_state.flow_saved_step_keys = set()
                        st.session_state.pop("flow_last_save_info", None)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Cold Start 실패: {e}")
    with col_reset:
        if st.button("초기화", use_container_width=True, key="flow_btn_reset"):
            st.session_state.flow_result = None
            st.session_state.flow_saved_step_keys = set()
            st.session_state.pop("flow_last_save_info", None)
            st.rerun()

    result = st.session_state.flow_result
    if result is None:
        return

    gs = result.get("grid_string") or ""
    results = result.get("results") or {}
    ref_state = (results.get("window9_10") or {}).get("state") or {}
    anchors = ref_state.get("anchors") or _anchors_from_grid_string(gs)

    render_grid_string_and_anchors(gs, anchors=anchors)

    current_table = build_current_state_table(result, predict_next_for_mode)
    st.dataframe(pd.DataFrame(current_table), use_container_width=True, hide_index=True)

    try:
        from livegame_prefix_rule_panel import render_unified_prefix_rule_panel
    except ImportError:
        render_unified_prefix_rule_panel = None
    if render_unified_prefix_rule_panel is not None:
        render_unified_prefix_rule_panel(
            result,
            predict_fn=predict_next_for_mode,
            st_module=st,
        )

    col_b, col_p, _ = st.columns([1, 1, 4])

    def _do_live_step(char: str) -> None:
        new_results = {}
        step_out = None
        for mode in MODES:
            r = results.get(mode, {})
            step_out = live_step_for_mode(r.get("state"), gs, r.get("history") or [], char, mode)
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

    with col_b:
        if st.button("B", key="flow_append_b", use_container_width=True):
            try:
                _do_live_step("b")
            except Exception as e:
                st.error(f"live_step 실패: {e}")
    with col_p:
        if st.button("P", key="flow_append_p", use_container_width=True):
            try:
                _do_live_step("p")
            except Exception as e:
                st.error(f"live_step 실패: {e}")

    history_rows = build_validation_history_table_by_position(result)
    if history_rows:
        df_history = pd.DataFrame(history_rows)
        st.dataframe(_style_validation_history_df(df_history), use_container_width=True, hide_index=True)
    else:
        st.caption("검증 히스토리 없음")

    with st.expander("Cold Start 요약 · 결과 저장", expanded=True):
        c1, c2 = st.columns(2)
        for idx, mode in enumerate(DISPLAY_ORDER):
            with [c1, c2][idx]:
                summ = (results.get(mode) or {}).get("summary") or _empty_summary()
                st.caption(f"**{mode_label(mode)}**")
                m1, m2, m3 = st.columns(3)
                m1.metric("스텝", summ.get("total_steps", 0))
                m2.metric("예측", summ.get("total_predictions", 0))
                m3.metric("정확도", f"{summ.get('accuracy', 0):.1f}%")
        if st.session_state.get("flow_last_save_info"):
            last = st.session_state.flow_last_save_info
            st.caption(
                f"마지막 저장 {last.get('created_at', '')} · "
                f"+{last.get('inserted', 0)} · 누적 {last.get('session_total', 0)}"
            )
        if st.button("결과 저장", key="flow_save_results", type="secondary", use_container_width=True):
            try:
                saved_keys = st.session_state.get("flow_saved_step_keys") or set()
                inserted, new_keys = save_all_live_step_results(results, saved_keys)
                st.session_state.flow_saved_step_keys = new_keys
                st.session_state.flow_last_save_info = {
                    "created_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                    "inserted": inserted,
                    "session_total": len(new_keys),
                }
                if inserted == 0:
                    st.info("저장할 새 스텝이 없습니다.")
                st.rerun()
            except Exception as e:
                st.error(f"저장 실패: {e}")


def _style_validation_history_df(df: pd.DataFrame):
    match_cols = [c for c in df.columns if c.endswith("_일치")]

    def cell_style(val):
        if val == "O":
            return "background-color: white; color: #16a34a; font-weight: bold; font-size: 1.1em;"
        if val == "X":
            return "background-color: white; color: #dc2626; font-weight: bold; font-size: 1.1em;"
        if val == MATCH_PASS:
            return "background-color: white; color: #6b7280; font-weight: bold; font-size: 1.1em;"
        return ""

    def style_column(series):
        if series.name in match_cols:
            return [cell_style(v) for v in series]
        return [""] * len(series)

    return df.style.apply(style_column, axis=0)


if __name__ == "__main__":
    main()
