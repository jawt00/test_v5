"""
Change-point 전용 라이브 게임 앱

- stored_predictions_change_point + 다중 윈도우(5~9) 최고 신뢰도 선택
- 신뢰도 스킵 선택 가능
- 목표: 연속 실패 5회 이하
"""

import streamlit as st
import pandas as pd

from svg_parser_module import get_change_point_db_connection
from change_point_prediction_module import (
    get_multi_window_prediction_cp,
    get_multi_window_prediction_with_confidence_skip_cp,
    get_stored_predictions_change_point_count,
)

st.set_page_config(page_title="Live Game (Change-point)", page_icon="🎮", layout="wide")

WINDOW_SIZES = [5, 6, 7, 8, 9]
MAX_WS = max(WINDOW_SIZES)


def _create_tables_cp():
    conn = get_change_point_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS live_game_sessions_cp (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                grid_string TEXT NOT NULL,
                method TEXT NOT NULL,
                threshold REAL NOT NULL,
                confidence_skip_threshold REAL,
                total_steps INTEGER,
                total_predictions INTEGER,
                total_failures INTEGER,
                total_skipped INTEGER,
                max_consecutive_failures INTEGER,
                accuracy REAL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS live_game_steps_cp (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                step INTEGER NOT NULL,
                position INTEGER NOT NULL,
                prefix TEXT,
                predicted TEXT,
                actual TEXT NOT NULL,
                is_correct INTEGER,
                confidence REAL,
                skipped INTEGER,
                FOREIGN KEY (session_id) REFERENCES live_game_sessions_cp(session_id)
            )
        """)
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        st.error(f"테이블 생성 오류: {e}")
        return False
    finally:
        conn.close()


def _save_session_cp(game_state):
    if not _create_tables_cp():
        return None
    conn = get_change_point_db_connection()
    cur = conn.cursor()
    try:
        acc = (
            (game_state["total_predictions"] - game_state["total_failures"])
            / game_state["total_predictions"] * 100
        ) if game_state["total_predictions"] > 0 else 0.0
        cur.execute(
            """
            INSERT INTO live_game_sessions_cp (
                grid_string, method, threshold, confidence_skip_threshold,
                total_steps, total_predictions, total_failures, total_skipped,
                max_consecutive_failures, accuracy
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_state["grid_string"],
                game_state["method"],
                game_state["threshold"],
                game_state.get("confidence_skip_threshold"),
                game_state["total_steps"],
                game_state["total_predictions"],
                game_state["total_failures"],
                game_state.get("total_skipped", 0),
                game_state["max_consecutive_failures"],
                acc,
            ),
        )
        sid = cur.lastrowid
        for h in game_state.get("history", []):
            cur.execute(
                """
                INSERT INTO live_game_steps_cp (
                    session_id, step, position, prefix, predicted, actual,
                    is_correct, confidence, skipped
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sid,
                    h.get("step", 0),
                    h.get("position", 0),
                    h.get("prefix"),
                    h.get("predicted"),
                    h.get("actual", ""),
                    (1 if h.get("is_correct") is True else (0 if h.get("is_correct") is False else None)),
                    h.get("confidence"),
                    1 if h.get("skipped") else 0,
                ),
            )
        conn.commit()
        return sid
    except Exception as e:
        conn.rollback()
        st.error(f"세션 저장 오류: {e}")
        return None
    finally:
        conn.close()


def main():
    st.title("Change-point 라이브 게임")
    st.markdown("**다중 윈도우(5~9) 최고 신뢰도 선택 + 신뢰도 스킵. 목표: 연속 실패 5회 이하.**")
    n_stored = get_stored_predictions_change_point_count()
    if n_stored == 0:
        st.warning("stored_predictions_change_point가 비어 있습니다. change_point_simulation_app에서 예측값을 먼저 생성하세요.")

    if "cp_settings" not in st.session_state:
        st.session_state.cp_settings = None
    if "cp_game_state" not in st.session_state:
        st.session_state.cp_game_state = None

    with st.expander("게임 설정", expanded=st.session_state.cp_settings is None):
        method = st.selectbox("예측 방법", ["빈도 기반", "가중치 기반", "안전 우선"], key="cp_method")
        threshold = st.number_input("임계값", 0, 100, 0, key="cp_threshold")
        use_skip = st.checkbox("신뢰도 스킵 사용", False, key="cp_use_skip")
        conf_skip = st.number_input("신뢰도 스킵 임계값 (%)", 0.0, 100.0, 52.0, 0.5, disabled=not use_skip, key="cp_conf_skip")
        if st.button("설정 저장", type="primary", key="cp_save_settings"):
            st.session_state.cp_settings = {
                "method": method,
                "threshold": threshold,
                "use_skip": use_skip,
                "confidence_skip_threshold": conf_skip if use_skip else None,
            }
            st.success("설정 저장됨.")
            st.rerun()

    grid_input = st.text_area(
        "Grid String (b/p/t)",
        value="",
        height=80,
        key="cp_grid",
        disabled=st.session_state.cp_settings is None,
        help="검증할 grid_string 입력.",
    )
    if st.session_state.cp_settings is None:
        st.warning("먼저 게임 설정을 저장하세요.")

    if st.button("게임 시작 (자동 검증)", type="primary", key="cp_start"):
        if st.session_state.cp_settings is None:
            st.error("설정을 먼저 저장하세요.")
        elif not (grid_input and grid_input.strip()):
            st.error("Grid String을 입력하세요.")
        elif len(grid_input.strip()) < MAX_WS:
            st.error(f"길이 최소 {MAX_WS} 필요 (현재 {len(grid_input.strip())}).")
        elif n_stored == 0:
            st.error("예측값을 먼저 생성하세요.")
        else:
            gs = grid_input.strip()
            cfg = st.session_state.cp_settings
            history = []
            consecutive = 0
            max_consecutive = 0
            total_pred = 0
            total_fail = 0
            total_skip = 0
            for pos in range(MAX_WS - 1, len(gs)):
                actual = gs[pos]
                if cfg["use_skip"] and cfg.get("confidence_skip_threshold") is not None:
                    res = get_multi_window_prediction_with_confidence_skip_cp(
                        gs, pos,
                        window_sizes=tuple(WINDOW_SIZES),
                        method=cfg["method"],
                        threshold=cfg["threshold"],
                        confidence_skip_threshold=cfg["confidence_skip_threshold"],
                    )
                else:
                    res = get_multi_window_prediction_cp(
                        gs, pos,
                        window_sizes=tuple(WINDOW_SIZES),
                        method=cfg["method"],
                        threshold=cfg["threshold"],
                    )
                pred = res.get("predicted") if res else None
                conf = res.get("confidence", 0.0) if res else 0.0
                skipped = res.get("skipped", False) if res else False
                pfx = res.get("prefix") if res else None
                step = len(history) + 1
                if pred is not None and not skipped:
                    ok = pred == actual
                    total_pred += 1
                    if not ok:
                        consecutive += 1
                        total_fail += 1
                        if consecutive > max_consecutive:
                            max_consecutive = consecutive
                    else:
                        consecutive = 0
                    history.append({
                        "step": step, "position": pos, "prefix": pfx,
                        "predicted": pred, "actual": actual, "is_correct": ok,
                        "confidence": conf, "skipped": False,
                    })
                else:
                    if skipped:
                        total_skip += 1
                    history.append({
                        "step": step, "position": pos, "prefix": None,
                        "predicted": None, "actual": actual, "is_correct": None,
                        "confidence": 0.0, "skipped": skipped,
                    })
            acc = (total_pred - total_fail) / total_pred * 100 if total_pred > 0 else 0.0
            st.session_state.cp_game_state = {
                "grid_string": gs,
                "method": cfg["method"],
                "threshold": cfg["threshold"],
                "confidence_skip_threshold": cfg.get("confidence_skip_threshold"),
                "total_steps": len(history),
                "total_predictions": total_pred,
                "total_failures": total_fail,
                "total_skipped": total_skip,
                "max_consecutive_failures": max_consecutive,
                "accuracy": acc,
                "history": history,
            }
            st.rerun()

    state = st.session_state.get("cp_game_state")
    if state:
        st.markdown("---")
        st.markdown("### 결과")
        st.metric("최대 연속 불일치", f"{state['max_consecutive_failures']}회")
        st.metric("정확도", f"{state['accuracy']:.2f}%")
        st.metric("총 예측", state["total_predictions"])
        st.metric("스킵", state.get("total_skipped", 0))
        if state["max_consecutive_failures"] <= 5:
            st.success("목표 달성: 연속 실패 5회 이하.")
        else:
            st.warning("목표 미달: 연속 실패 5회 초과.")
        df = pd.DataFrame([
            {
                "step": h["step"],
                "pos": h["position"],
                "pred": h["predicted"],
                "actual": h["actual"],
                "ok": h["is_correct"],
                "conf": f"{h.get('confidence', 0):.1f}",
                "skip": h.get("skipped", False),
            }
            for h in state["history"][:100]
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)
        if st.button("세션 저장", key="cp_save_session"):
            sid = _save_session_cp(state)
            if sid:
                st.success(f"세션 저장됨 (id={sid}).")
            else:
                st.error("저장 실패.")
        if st.button("초기화", key="cp_reset"):
            st.session_state.cp_game_state = None
            st.rerun()


if __name__ == "__main__":
    main()
