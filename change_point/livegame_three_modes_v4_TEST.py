"""
[V4 TEST] Cold Start → State Handoff → Live Loop 테스트용 라이브 게임 앱.

운영본: livegame_three_modes_v4.py · pattern_list2.db
테스트본(이 파일): 예측 테이블 갱신 앱과 동일한 TEST DB를 사용.

- 예측값: change_point/db_backup/pattern_list2_TEST.db · simulation_predictions_change_point
- 결과 저장: 동일 TEST DB · live_step_results
"""

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sqlite3
import streamlit as st
import pandas as pd

from pattern_list_profiles import LIST2_TEST_PREDICTIONS_DB

PREDICTIONS_DB_PATH = LIST2_TEST_PREDICTIONS_DB
PREDICTIONS_TABLE = "simulation_predictions_change_point"
LIVE_STEP_RESULTS_TABLE = "live_step_results"


def _get_predictions_db_connection():
    """pattern_list2_TEST.db 연결 (갱신 앱과 동일)."""
    conn = sqlite3.connect(PREDICTIONS_DB_PATH, timeout=20.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _init_live_step_results_schema(conn):
    """v4 전용 라이브 스텝 누적 테이블 (mode 없음)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (LIVE_STEP_RESULTS_TABLE,),
    )
    if cur.fetchone():
        cur.execute(f"PRAGMA table_info({LIVE_STEP_RESULTS_TABLE})")
        cols = {row[1] for row in cur.fetchall()}
        if "mode" in cols:
            cur.execute(
                f"ALTER TABLE {LIVE_STEP_RESULTS_TABLE} "
                f"RENAME TO {LIVE_STEP_RESULTS_TABLE}_legacy"
            )

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {LIVE_STEP_RESULTS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP NOT NULL,
            step INTEGER NOT NULL,
            position INTEGER NOT NULL,
            anchor INTEGER NOT NULL,
            window_size INTEGER NOT NULL,
            prefix TEXT NOT NULL,
            predicted TEXT,
            actual TEXT,
            is_correct INTEGER,
            confidence REAL NOT NULL DEFAULT 0,
            skipped INTEGER NOT NULL DEFAULT 0,
            skip_reason TEXT
        )
    """)
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{LIVE_STEP_RESULTS_TABLE}_created_at "
        f"ON {LIVE_STEP_RESULTS_TABLE}(created_at)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{LIVE_STEP_RESULTS_TABLE}_prefix "
        f"ON {LIVE_STEP_RESULTS_TABLE}(prefix)"
    )
    conn.commit()


def _is_correct_to_int(value):
    if value is True:
        return 1
    if value is False:
        return 0
    return None


def save_live_step_results(history: list, saved_keys: set):
    """
    아직 저장되지 않은 스텝만 pattern_list2_TEST.db.live_step_results에 누적 INSERT.
    Returns: (inserted_count, updated_saved_keys)
    """
    created_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0
    new_keys = set(saved_keys)

    conn = _get_predictions_db_connection()
    try:
        _init_live_step_results_schema(conn)
        conn.execute("BEGIN")
        for entry in history or []:
            step = entry.get("step", 0)
            if step in new_keys:
                continue
            conn.execute(
                f"""
                INSERT INTO {LIVE_STEP_RESULTS_TABLE}
                (created_at, step, position, anchor, window_size, prefix,
                 predicted, actual, is_correct, confidence, skipped, skip_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    step,
                    entry.get("position", 0),
                    entry.get("anchor", 0),
                    entry.get("window_size", 0),
                    entry.get("prefix", ""),
                    entry.get("predicted"),
                    entry.get("actual"),
                    _is_correct_to_int(entry.get("is_correct")),
                    entry.get("confidence", 0.0),
                    1 if entry.get("skipped") else 0,
                    entry.get("skip_reason"),
                ),
            )
            new_keys.add(step)
            inserted += 1
        conn.commit()
        return inserted, new_keys
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

st.set_page_config(
    page_title="TEST · Change-point 플로우 라이브 게임 v4 (3가지 검증 방식)",
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
PASS_LABEL = "pass"
MATCH_PASS = "P"


def _match_label(is_correct, predicted=None) -> str:
    if is_correct is True:
        return "O"
    if is_correct is False:
        return "X"
    if predicted == PASS_LABEL:
        return MATCH_PASS
    return "-"


def _history_entry_visible(entry: dict) -> bool:
    """검증 히스토리 테이블 표시 여부 (pass 포함)."""
    if not entry:
        return False
    ok = entry.get("is_correct")
    if ok is True or ok is False:
        return True
    return entry.get("predicted") == PASS_LABEL


def _is_null_predicted(value) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _display_predicted_value(value, skipped: bool = False) -> str:
    """화면 표시용 예측값. 테이블 NULL/pass → 'pass', 행 없음·스킵 → '-'."""
    if value == PASS_LABEL:
        return PASS_LABEL
    if _is_null_predicted(value):
        return PASS_LABEL if not skipped else "-"
    if skipped:
        return "-"
    return str(value)


def _lookup_prediction(conn, window_size: int, prefix: str):
    """
    simulation_predictions_change_point 조회.
    Returns (predicted, confidence, status) where status is 'ok' | 'pass' | 'missing'.
    """
    df_pred = pd.read_sql_query(
        f"SELECT predicted_value, confidence FROM {PREDICTIONS_TABLE} "
        "WHERE window_size=? AND prefix=? AND method=? AND threshold=? LIMIT 1",
        conn,
        params=[window_size, prefix, METHOD, THRESHOLD],
    )
    if len(df_pred) == 0:
        return None, 0.0, "missing"
    predicted = df_pred.iloc[0]["predicted_value"]
    confidence = df_pred.iloc[0]["confidence"]
    if _is_null_predicted(predicted):
        return PASS_LABEL, 0.0, "pass"
    return predicted, confidence, "ok"


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

    conn = _get_predictions_db_connection()
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

                predicted, confidence, pred_status = _lookup_prediction(conn, window_size, prefix)

                if pred_status in ("missing", "pass"):
                    total_skipped += 1
                    current_pos = max(current_pos, pos + 1)
                    history.append({
                        "step": total_steps, "position": pos, "anchor": next_anchor, "window_size": window_size,
                        "prefix": prefix,
                        "predicted": PASS_LABEL if pred_status == "pass" else None,
                        "actual": actual, "is_correct": None,
                        "confidence": 0.0, "skipped": True,
                        "skip_reason": "pass (예측값 NULL)" if pred_status == "pass" else "예측 테이블에 값 없음",
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

    conn = _get_predictions_db_connection()
    try:
        predicted, confidence, pred_status = _lookup_prediction(conn, window, prefix)
        if pred_status == "missing":
            return {"predicted": None, "confidence": 0.0, "window_size": window, "prefix": prefix, "anchor": anchor, "skipped": True}
        if pred_status == "pass":
            return {"predicted": PASS_LABEL, "confidence": 0.0, "window_size": window, "prefix": prefix, "anchor": anchor, "skipped": True}
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
    is_pass = predicted == PASS_LABEL
    is_skipped = skipped or predicted is None or is_pass

    if is_skipped:
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
        new_history = history
        if is_pass or (window_size and prefix):
            new_history = history + [{
                "step": step_num, "position": position, "anchor": anchor if anchor is not None else 0,
                "window_size": window_size or 0, "prefix": prefix or "",
                "predicted": PASS_LABEL if is_pass else None,
                "actual": actual, "is_correct": None,
                "confidence": confidence, "skipped": True,
                "skip_reason": "pass (예측값 NULL)" if is_pass else "예측 없음",
            }]
        return {
            "state": new_state, "history": new_history, "grid_string": new_grid_string,
            "step_result": {"skipped": True, "pass": is_pass, "waiting": new_aidx >= len(new_anchors)},
        }

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


# UI 표시 순서: W9_10 → V3 → W9
DISPLAY_ORDER = ("window9_10", "v3", "window9")


def _mode_label(mode: str) -> str:
    if mode == "v3":
        return "V3"
    if mode == "window9":
        return "W9"
    return "W9_10"


def build_current_state_table(flow_result: dict) -> list:
    """현재 포지션(다음 예측 위치)에 대해 3가지 검증별 예측값(P/B). 1행 테이블. 칼럼 순서: Position, W9_10, V3, W9."""
    gs = flow_result.get("grid_string") or ""
    results = flow_result.get("results") or {}
    pos = len(gs)
    row = {"Position": pos}
    for mode in DISPLAY_ORDER:
        r = results.get(mode, {})
        state = r.get("state") or {}
        pred = predict_next_for_mode(state, gs, mode)
        pv = pred.get("predicted")
        row[_mode_label(mode)] = _display_predicted_value(pv, pred.get("skipped", False))
    return [row]


def build_validation_history_table_by_position(flow_result: dict) -> list:
    """포지션 0부터 max까지 한 행에 한 포지션, 열 순서: Position, W9_10, V3, W9 (각 예측/실제/일치)."""
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
            by_pos[p][mode] = {
                "predicted": _display_predicted_value(pred, e.get("skipped", False)),
                "predicted_raw": pred,
                "actual": actual or "-",
                "is_correct": ok,
            }

    max_pos = max(by_pos.keys(), default=-1)
    if len(gs) > 0:
        max_pos = max(max_pos, len(gs) - 1)
    rows = []
    for pos in range(0, max_pos + 1):
        d = by_pos.get(pos, {})
        w910 = d.get("window9_10", {})
        v3 = d.get("v3", {})
        w9 = d.get("window9", {})
        if not any(_history_entry_visible(d.get(m, {})) for m in MODES):
            continue
        match_w910 = _match_label(w910.get("is_correct"), w910.get("predicted_raw"))
        match_v3 = _match_label(v3.get("is_correct"), v3.get("predicted_raw"))
        match_w9 = _match_label(w9.get("is_correct"), w9.get("predicted_raw"))
        row = {
            "Position": pos,
            "W9_10_예측": w910.get("predicted", "-"),
            "W9_10_실제": w910.get("actual", "-"),
            "W9_10_일치": match_w910,
            "V3_예측": v3.get("predicted", "-"),
            "V3_실제": v3.get("actual", "-"),
            "V3_일치": match_v3,
            "W9_예측": w9.get("predicted", "-"),
            "W9_실제": w9.get("actual", "-"),
            "W9_일치": match_w9,
        }
        rows.append(row)
    # 최근 포지션이 상단에 오도록 역순 반환
    return rows[::-1]


def _style_validation_history_df(df: pd.DataFrame) -> "pd.io.formats.style.Styler":
    """일치(O/X/P) 열 시인성: O=녹색, X=빨간색, P=pass, 굵게."""
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


def main():
    st.title("Change-point 플로우 라이브 게임 v4 · TEST")
    st.warning(
        f"**테스트용 라이브 앱** — 예측/결과 DB: `{PREDICTIONS_DB_PATH}` "
        f"(예측 테이블 갱신 앱과 동일). 운영 `pattern_list2.db` 는 사용하지 않습니다."
    )
    st.markdown("**Cold Start → State Handoff → Live Loop** · W9_10 / V3 / 윈도우 9 병렬 실행")
    st.caption(
        f"예측 DB: `{PREDICTIONS_DB_PATH}` · `{PREDICTIONS_TABLE}` · "
        f"결과 저장: `{LIVE_STEP_RESULTS_TABLE}` (스텝 누적 · TEST)"
    )

    if "flow_result" not in st.session_state:
        st.session_state.flow_result = None

    if "flow_saved_step_keys" not in st.session_state:
        st.session_state.flow_saved_step_keys = set()

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
                        st.session_state.flow_saved_step_keys = set()
                        st.session_state.pop("flow_last_save_info", None)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Cold Start 실패: {e}")
    with col_reset:
        if st.button("초기화", use_container_width=True, key="flow_btn_reset_three"):
            st.session_state.flow_result = None
            st.session_state.flow_saved_step_keys = set()
            st.session_state.pop("flow_last_save_info", None)
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
        # 모드별 state 디버깅 정보 · 표시 순서: W9_10 → V3 → W9
        for mode in DISPLAY_ORDER:
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
                f"**{_mode_label(mode)}** · current_pos = {cp} · 앵커 = {aidx} ({aidx_label}) · "
                f"failure_count = {fc} · next_window = {nw} · search_from = {sf}"
            )
        st.caption("위 Grid의 포지션 인덱스·앵커 인덱스(a0,a1,…)와 동일한 0-based 기준")
        current_table = build_current_state_table(result)
        st.dataframe(pd.DataFrame(current_table), use_container_width=True, hide_index=True)
        st.caption("다음 예측 위치 = len(grid_string). W9_10 / V3 / W9 각각 예측값(P 또는 B, NULL이면 pass, 없으면 -).")

        try:
            from livegame_prefix_rule_panel import render_prefix_rule_panel
        except ImportError:
            render_prefix_rule_panel = None
        if render_prefix_rule_panel is not None:
            render_prefix_rule_panel(
                result,
                predict_fn=predict_next_for_mode,
                display_order=("window9",),
                mode_label_fn=_mode_label,
                st_module=st,
                db_path=PREDICTIONS_DB_PATH,
            )

        # 테이블 바로 아래 첫 번째 줄: W9_10 예측 신뢰도 (confidence는 0~100 저장)
        r_w910 = result.get("results") or {}
        state_w910 = r_w910.get("window9_10", {}).get("state") or {}
        pred_w910 = predict_next_for_mode(state_w910, gs, "window9_10")
        if pred_w910.get("predicted") and not pred_w910.get("skipped"):
            conf = pred_w910.get("confidence", 0.0)
            st.caption(f"**W9_10** 예측 신뢰도: {conf:.2f}%")

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

        st.markdown("### 검증 히스토리 테이블 (포지션별 W9_10 / V3 / W9)")
        history_rows = build_validation_history_table_by_position(result)
        if history_rows:
            df_history = pd.DataFrame(history_rows)
            st.dataframe(_style_validation_history_df(df_history), use_container_width=True, hide_index=True)
            st.caption(f"포지션 0 ~ {len(history_rows) - 1} (총 {len(history_rows)}행) · pass=일치 열 P · 최근 포지션 상단 역순")
        else:
            st.info("히스토리가 없습니다.")

        st.markdown("#### Cold Start 요약 (3모드)")
        c1, c2, c3 = st.columns(3)
        for idx, mode in enumerate(DISPLAY_ORDER):
            col = [c1, c2, c3][idx]
            with col:
                summ = results.get(mode, {}).get("summary") or _empty_summary()
                label = "윈도우 9·10" if mode == "window9_10" else ("V3 (9~14)" if mode == "v3" else "윈도우 9")
                st.markdown(f"**{label}**")
                st.metric("총 스텝", summ.get("total_steps", 0))
                st.metric("총 예측", summ.get("total_predictions", 0))
                st.metric("정확도", f"{summ.get('accuracy', 0):.1f}%")

        st.markdown("#### 결과 저장")
        st.caption(
            f"`{PREDICTIONS_DB_PATH.name}` · `{LIVE_STEP_RESULTS_TABLE}` · "
            "아직 저장되지 않은 스텝만 누적 저장 (예측 vs 실제 비교용)"
        )
        if st.session_state.get("flow_last_save_info"):
            last = st.session_state.flow_last_save_info
            st.success(
                f"마지막 저장: {last.get('created_at', '')} · "
                f"{last.get('inserted', 0)}건 추가 · "
                f"세션 누적 {last.get('session_total', 0)}건"
            )
        if st.button("결과 저장", key="flow_save_results_three", type="secondary", use_container_width=True):
            try:
                history = results.get("window9", {}).get("history") or []
                saved_keys = st.session_state.get("flow_saved_step_keys") or set()
                inserted, new_keys = save_live_step_results(history, saved_keys)
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


if __name__ == "__main__":
    main()
