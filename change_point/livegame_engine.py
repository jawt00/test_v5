"""통합 라이브게임 엔진 — W9_10(L2) + W9_11(L3) · profile별 DB lookup."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

from livegame_mode_registry import (
    DISPLAY_ORDER,
    DUAL_WINDOW_MODES,
    MODES,
    get_mode,
    mode_label,
)
from pattern_list3_ws9_core import to_ws9_core
from pattern_list_profiles import (
    LIST2_TEST_PREDICTIONS_DB,
    LIST3_TEST_PREDICTIONS_DB,
)

KST = timezone(timedelta(hours=9))

PREDICTIONS_TABLE = "simulation_predictions_change_point"
LIVE_STEP_RESULTS_TABLE = "live_step_results"
METHOD = "빈도 기반"
THRESHOLD = 0
WS9_LOOKUP = 9
MIN_GRID_LENGTH = 6
PASS_LABEL = "pass"
MATCH_PASS = "P"

USE_TEST_DB = True

_conn_cache: dict[str, sqlite3.Connection] = {}


def set_use_test_db(use_test: bool) -> None:
    global USE_TEST_DB, _conn_cache
    USE_TEST_DB = use_test
    for conn in _conn_cache.values():
        try:
            conn.close()
        except Exception:
            pass
    _conn_cache = {}


def _db_path_for_profile(profile: str) -> Path:
    if profile == "list2":
        if USE_TEST_DB:
            return LIST2_TEST_PREDICTIONS_DB
        return LIST2_TEST_PREDICTIONS_DB.parent.parent / "pattern_list2.db"
    if profile == "list3":
        if USE_TEST_DB:
            return LIST3_TEST_PREDICTIONS_DB
        return LIST3_TEST_PREDICTIONS_DB.parent.parent / "pattern_list3.db"
    raise ValueError(f"unknown profile: {profile!r}")


def _get_predictions_db_connection(profile: str) -> sqlite3.Connection:
    path = str(_db_path_for_profile(profile))
    conn = _conn_cache.get(path)
    if conn is None:
        conn = sqlite3.connect(path, timeout=20.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        _conn_cache[path] = conn
    return conn


def _init_live_step_results_schema(conn: sqlite3.Connection) -> None:
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
    if value == PASS_LABEL:
        return PASS_LABEL
    if _is_null_predicted(value):
        return PASS_LABEL if not skipped else "-"
    if skipped:
        return "-"
    return str(value)


def _match_label(is_correct, predicted=None) -> str:
    if is_correct is True:
        return "O"
    if is_correct is False:
        return "X"
    if predicted == PASS_LABEL:
        return MATCH_PASS
    return "-"


def _history_entry_visible(entry: dict) -> bool:
    if not entry:
        return False
    ok = entry.get("is_correct")
    if ok is True or ok is False:
        return True
    return entry.get("predicted") == PASS_LABEL


def _lookup_prediction(mode: str, window_size: int, prefix: str) -> dict:
    """DB 조회. Plan C 이후 confidence = rule_confidence."""
    cfg = get_mode(mode)
    conn = _get_predictions_db_connection(cfg.profile)

    if cfg.lookup == "ws9_core":
        lookup_key = to_ws9_core(prefix, window_size)
        if not lookup_key:
            return {"predicted": None, "confidence": 0.0, "status": "missing"}
        query_ws = WS9_LOOKUP
        query_prefix = lookup_key
    else:
        query_ws = window_size
        query_prefix = prefix.strip().lower()

    base_q = (
        f"SELECT predicted_value, confidence FROM {PREDICTIONS_TABLE} "
        "WHERE window_size=? AND prefix=? AND method=? AND threshold=? LIMIT 1"
    )
    ext_q = (
        f"SELECT predicted_value, confidence, rule_confidence, conf_source, final_rule "
        f"FROM {PREDICTIONS_TABLE} "
        "WHERE window_size=? AND prefix=? AND method=? AND threshold=? LIMIT 1"
    )
    params = [query_ws, query_prefix, METHOD, THRESHOLD]
    try:
        df_pred = pd.read_sql_query(ext_q, conn, params=params)
    except Exception:
        df_pred = pd.read_sql_query(base_q, conn, params=params)

    if len(df_pred) == 0:
        return {"predicted": None, "confidence": 0.0, "status": "missing"}

    row = df_pred.iloc[0]
    predicted = row["predicted_value"]
    rule_conf = row.get("rule_confidence") if "rule_confidence" in df_pred.columns else None
    conf = row["confidence"]
    if pd.notna(rule_conf):
        confidence = float(rule_conf)
    elif pd.notna(conf):
        confidence = float(conf)
    else:
        confidence = 0.0

    meta = {
        "conf_source": row.get("conf_source") if "conf_source" in df_pred.columns else None,
        "final_rule": row.get("final_rule") if "final_rule" in df_pred.columns else None,
    }
    if pd.isna(meta["conf_source"]):
        meta["conf_source"] = None
    if pd.isna(meta["final_rule"]):
        meta["final_rule"] = None

    if _is_null_predicted(predicted):
        return {
            "predicted": PASS_LABEL,
            "confidence": 0.0,
            "status": "pass",
            **meta,
        }
    return {
        "predicted": predicted,
        "confidence": confidence,
        "status": "ok",
        **meta,
    }


def _window_sizes_for_mode(mode: str) -> tuple[int, ...]:
    return get_mode(mode).window_sizes


def _anchors_from_grid_string(grid_string: str):
    if not grid_string or len(grid_string) < 2:
        return []
    return sorted(set(i for i in range(len(grid_string) - 1) if grid_string[i] != grid_string[i + 1]))


def _first_anchor_from_position(anchors: list, from_pos: int) -> int:
    i = 0
    while i < len(anchors) and anchors[i] < from_pos:
        i += 1
    return i


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
    return {
        "total_steps": 0,
        "total_failures": 0,
        "total_predictions": 0,
        "total_skipped": 0,
        "accuracy": 0.0,
    }


def cold_start_for_mode(grid_string: str, mode: str):
    window_sizes = _window_sizes_for_mode(mode)
    min_ws = min(window_sizes)
    max_ws = max(window_sizes)

    if len(grid_string) < MIN_GRID_LENGTH:
        return {"state": _empty_state(), "history": [], "summary": _empty_summary()}

    anchors = _anchors_from_grid_string(grid_string)
    if not anchors:
        return {"state": _empty_state(), "history": [], "summary": _empty_summary()}

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
        fail_count = 0
        exited_string_end = False
        did_finish_anchor = False
        last_pos = None

        for window_size in window_sizes:
            pos = next_anchor + window_size - 1
            if pos >= len(grid_string):
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

            lookup = _lookup_prediction(mode, window_size, prefix)
            predicted = lookup["predicted"]
            confidence = lookup["confidence"]
            pred_status = lookup["status"]

            if pred_status in ("missing", "pass"):
                total_skipped += 1
                current_pos = max(current_pos, pos + 1)
                history.append({
                    "step": total_steps,
                    "position": pos,
                    "anchor": next_anchor,
                    "window_size": window_size,
                    "prefix": prefix,
                    "predicted": PASS_LABEL if pred_status == "pass" else None,
                    "actual": actual,
                    "is_correct": None,
                    "confidence": 0.0,
                    "skipped": True,
                    "skip_reason": "pass (예측값 NULL)" if pred_status == "pass" else "예측 테이블에 값 없음",
                })
                last_pos = pos
                continue

            last_window_used = window_size
            ok = predicted == actual
            total_predictions += 1

            if not ok:
                fail_count += 1
                total_failures += 1
            else:
                fail_count = 0

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

        else:
            if mode in DUAL_WINDOW_MODES and last_pos is not None:
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
    acc = (
        (total_predictions - total_failures) / total_predictions * 100
        if total_predictions > 0
        else 0.0
    )
    summary = {
        "total_steps": total_steps,
        "total_failures": total_failures,
        "total_predictions": total_predictions,
        "total_skipped": total_skipped,
        "accuracy": acc,
    }
    return {"state": state, "history": history, "summary": summary}


def predict_next_for_mode(state: dict, grid_string: str, mode: str):
    position = len(grid_string)
    anchors = state.get("anchors") or []
    aidx = state.get("active_anchor_idx", 0)
    window_sizes = _window_sizes_for_mode(mode)

    if aidx >= len(anchors):
        return {
            "predicted": None,
            "confidence": 0.0,
            "window_size": None,
            "prefix": None,
            "anchor": None,
            "skipped": True,
        }

    anchor = anchors[aidx]
    window = position - anchor + 1
    if window not in window_sizes:
        return {
            "predicted": None,
            "confidence": 0.0,
            "window_size": None,
            "prefix": None,
            "anchor": anchor,
            "skipped": True,
        }

    prefix_len = window - 1
    if position < prefix_len:
        return {
            "predicted": None,
            "confidence": 0.0,
            "window_size": None,
            "prefix": None,
            "anchor": anchor,
            "skipped": True,
        }
    prefix = grid_string[position - prefix_len : position]

    lookup = _lookup_prediction(mode, window, prefix)
    predicted = lookup["predicted"]
    confidence = lookup["confidence"]
    pred_status = lookup["status"]
    conf_source = lookup.get("conf_source")
    final_rule = lookup.get("final_rule")
    if pred_status == "missing":
        return {
            "predicted": None,
            "confidence": 0.0,
            "conf_source": None,
            "final_rule": None,
            "window_size": window,
            "prefix": prefix,
            "anchor": anchor,
            "skipped": True,
        }
    if pred_status == "pass":
        return {
            "predicted": PASS_LABEL,
            "confidence": 0.0,
            "conf_source": conf_source or "pass",
            "final_rule": final_rule or "R4",
            "window_size": window,
            "prefix": prefix,
            "anchor": anchor,
            "skipped": True,
        }
    return {
        "predicted": predicted,
        "confidence": confidence,
        "conf_source": conf_source,
        "final_rule": final_rule,
        "window_size": window,
        "prefix": prefix,
        "anchor": anchor,
        "skipped": False,
    }


def live_step_for_mode(state: dict, grid_string: str, history: list, user_input: str, mode: str):
    new_grid_string = grid_string + user_input
    position = len(grid_string)
    actual = user_input.lower()
    window_sizes = _window_sizes_for_mode(mode)

    if actual not in ("b", "p"):
        return {
            "state": state,
            "history": history,
            "grid_string": grid_string,
            "step_result": {"error": "b 또는 p만 입력 가능"},
        }

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
            new_aidx = next(
                (i for i in range(len(new_anchors)) if new_anchors[i] == old_anchor_pos),
                len(new_anchors),
            )
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
                "step": step_num,
                "position": position,
                "anchor": anchor if anchor is not None else 0,
                "window_size": window_size or 0,
                "prefix": prefix or "",
                "predicted": PASS_LABEL if is_pass else None,
                "actual": actual,
                "is_correct": None,
                "confidence": confidence,
                "skipped": True,
                "skip_reason": "pass (예측값 NULL)" if is_pass else "예측 없음",
            }]
        return {
            "state": new_state,
            "history": new_history,
            "grid_string": new_grid_string,
            "step_result": {"skipped": True, "pass": is_pass, "waiting": new_aidx >= len(new_anchors)},
        }

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
        if new_anchor_idx >= len(new_anchors):
            new_anchors.append(next_pos)
            new_anchor_idx = len(new_anchors) - 1
        new_fc = 0
        new_next_window = 9
    else:
        new_fc = fc + 1
        rest = [w for w in window_sizes if w > window_size]
        if rest:
            new_search_from = search_from
            old_val = anchors[aidx] if aidx < len(anchors) else None
            new_anchor_idx = next(
                (i for i in range(len(new_anchors)) if new_anchors[i] == old_val),
                len(new_anchors),
            )
            if new_anchor_idx >= len(new_anchors):
                new_anchor_idx = min(aidx, len(new_anchors) - 1)
            new_next_window = rest[0]
        else:
            new_search_from = next_pos
            new_anchor_idx = _first_anchor_from_position(new_anchors, new_search_from)
            if new_anchor_idx >= len(new_anchors):
                new_anchors.append(next_pos)
                new_anchor_idx = len(new_anchors) - 1
            new_fc = 0
            new_next_window = 9

    new_state = {
        "current_pos": next_pos,
        "active_anchor_idx": new_anchor_idx,
        "anchor_failure_count": new_fc,
        "next_window_size": new_next_window,
        "anchors": new_anchors,
        "search_from": new_search_from,
    }
    return {
        "state": new_state,
        "history": new_history,
        "grid_string": new_grid_string,
        "step_result": {"is_correct": ok, "skipped": False},
    }


def _live_step_save_key(mode: str, entry: dict) -> str:
    return (
        f"{mode}:{entry.get('step', 0)}:"
        f"{entry.get('position', 0)}:{entry.get('window_size', 0)}:"
        f"{entry.get('prefix', '')}"
    )


def resolve_list_profile_from_grid(grid_string: str) -> str | None:
    """
    Grid 앞쪽 패딩으로 list profile 판별 (세션 전체 고정).

    - bbb / ppp → list3 (ws11 native 10자)
    - bb / pp   → list2 (ws10 native 9자)
    """
    s = (grid_string or "").strip().lower()
    if not s:
        return None
    if s.startswith("bbb") or s.startswith("ppp"):
        return "list3"
    if s.startswith("bb") or s.startswith("pp"):
        return "list2"
    return None


def _should_persist_entry(mode: str, entry: dict, grid_string: str) -> bool:
    target = resolve_list_profile_from_grid(grid_string)
    if target is None:
        return False
    return get_mode(mode).profile == target


def normalize_saved_step_keys(raw) -> set[str]:
    """session_state용 저장 키 (set/tuple/list 혼용 대응)."""
    if not raw:
        return set()
    out: set[str] = set()
    for key in raw:
        if isinstance(key, str):
            out.add(key)
        elif isinstance(key, (list, tuple)) and len(key) == 2:
            out.add(f"{key[0]}:{key[1]}")
    return out


def count_unsaved_live_steps(results: dict, saved_keys, grid_string: str) -> int:
    keys = normalize_saved_step_keys(saved_keys)
    total = 0
    for mode in MODES:
        for entry in (results.get(mode) or {}).get("history") or []:
            if not _should_persist_entry(mode, entry, grid_string):
                continue
            if _live_step_save_key(mode, entry) not in keys:
                total += 1
    return total


def save_live_step_results_for_mode(
    mode: str,
    history: list,
    saved_keys,
    grid_string: str,
) -> tuple[int, set]:
    """이번 세션에서 아직 저장하지 않은 스텝만 grid profile DB에 누적 INSERT."""
    target = resolve_list_profile_from_grid(grid_string)
    if target is None or get_mode(mode).profile != target:
        return 0, normalize_saved_step_keys(saved_keys)
    db_path = _db_path_for_profile(target)
    created_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0
    new_keys = normalize_saved_step_keys(saved_keys)

    conn = sqlite3.connect(db_path, timeout=20.0, check_same_thread=False)
    try:
        _init_live_step_results_schema(conn)
        conn.execute("BEGIN")
        for entry in history or []:
            if not _should_persist_entry(mode, entry, grid_string):
                continue
            key = _live_step_save_key(mode, entry)
            if key in new_keys:
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
                    entry.get("step", 0),
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
            new_keys.add(key)
            inserted += 1
        conn.commit()
        return inserted, new_keys
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def live_save_db_name(profile: str | None) -> str | None:
    if not profile:
        return None
    return _db_path_for_profile(profile).name


def unsaved_save_db_name(results: dict, saved_keys, grid_string: str) -> str | None:
    """미저장 스텝이 들어갈 TEST DB 파일명."""
    target = resolve_list_profile_from_grid(grid_string)
    if target is None:
        return None
    if count_unsaved_live_steps(results, saved_keys, grid_string) == 0:
        return None
    return live_save_db_name(target)


def save_all_live_step_results(
    results: dict, saved_keys, grid_string: str
) -> tuple[int, set, str | None]:
    target = resolve_list_profile_from_grid(grid_string)
    if target is None:
        return 0, normalize_saved_step_keys(saved_keys), None
    total_inserted = 0
    keys = normalize_saved_step_keys(saved_keys)
    for mode in MODES:
        if get_mode(mode).profile != target:
            continue
        hist = (results.get(mode) or {}).get("history") or []
        n, keys = save_live_step_results_for_mode(mode, hist, keys, grid_string)
        total_inserted += n
    return total_inserted, keys, target


CELLS_PER_ROW = 35


def render_grid_string_and_anchors(grid_string: str, anchors: list = None):
    import streamlit as st

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
    chars = [
        f"<span style='{char_cell}{'background:#ADD8E6;' if i in anchors else 'background:#fff;'}'>{c}</span>"
        for i, c in enumerate(grid_string)
    ]
    for row_html in make_rows(chars):
        st.markdown(f"<div style='{row_style}'>{row_html}</div>", unsafe_allow_html=True)
    idx_cells = [f"<span style='{base_cell}color:#555;'>{i}</span>" for i in range(len(grid_string))]
    for row_html in make_rows(idx_cells):
        st.markdown(f"<div style='{row_style}'>{row_html}</div>", unsafe_allow_html=True)
    anchor_cells = []
    for i in range(len(grid_string)):
        if i in pos_to_anchor_idx:
            anchor_cells.append(
                f"<span style='{base_cell}color:#0066cc;font-weight:bold;'>{pos_to_anchor_idx[i]}</span>"
            )
        else:
            anchor_cells.append(f"<span style='{base_cell}color:#ccc;'>·</span>")
    for row_html in make_rows(anchor_cells):
        st.markdown(f"<div style='{row_style}'>{row_html}</div>", unsafe_allow_html=True)
    st.caption("위: 문자 | 가운데: 포지션 | 아래: 앵커(a0,a1,…)")


def _format_prediction_confidence(pred: dict) -> str:
    if pred.get("skipped"):
        return "-"
    conf = pred.get("confidence")
    if conf is None or (isinstance(conf, float) and pd.isna(conf)) or conf == 0.0:
        if pred.get("predicted") == PASS_LABEL:
            return "-"
        return "-"
    text = f"{float(conf):.1f}%"
    src = pred.get("conf_source")
    if src and str(src) not in ("pass", "unknown", ""):
        text = f"{text} · {src}"
    rule = pred.get("final_rule")
    if rule and str(rule) not in ("", "R4"):
        text = f"{text} · {rule}"
    return text


def build_current_state_table(flow_result: dict, predict_fn) -> list:
    gs = flow_result.get("grid_string") or ""
    results = flow_result.get("results") or {}
    pos = len(gs)
    row = {"Position": pos}
    for mode in DISPLAY_ORDER:
        r = results.get(mode, {})
        state = r.get("state") or {}
        pred = predict_fn(state, gs, mode)
        pv = pred.get("predicted")
        label = mode_label(mode)
        row[f"{label} 예측"] = _display_predicted_value(pv, pred.get("skipped", False))
        row[f"{label} 신뢰도"] = _format_prediction_confidence(pred)
    return [row]


def build_validation_history_table_by_position(flow_result: dict) -> list:
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
        w911 = d.get("window9_11", {})
        if not any(_history_entry_visible(d.get(m, {})) for m in MODES):
            continue
        rows.append({
            "Position": pos,
            "W9_10_예측": w910.get("predicted", "-"),
            "W9_10_실제": w910.get("actual", "-"),
            "W9_10_일치": _match_label(w910.get("is_correct"), w910.get("predicted_raw")),
            "W9_11_예측": w911.get("predicted", "-"),
            "W9_11_실제": w911.get("actual", "-"),
            "W9_11_일치": _match_label(w911.get("is_correct"), w911.get("predicted_raw")),
        })
    return rows[::-1]
