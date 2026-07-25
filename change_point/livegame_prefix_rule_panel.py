"""
라이브게임 v4 — 현재 예측 prefix의 list2 결정 규칙·라이브 적중률 패널.

제거 시: livegame_three_modes_v4.py 의 render_prefix_rule_panel() 호출 1줄만 삭제.
비활성: ENABLED = False
"""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from pathlib import Path

import pandas as pd

from pattern_list2_final_rules import (
    FINAL_RULE_INFO,
    build_rule_engine,
    parse_rule_version,
)

ENABLED = True

DB_PATH = Path(__file__).resolve().parent / "pattern_list2.db"
LIVE_STEP_TABLE = "live_step_results"
WS9_WINDOW = 9
PIPELINE_STATE_TABLE = "pipeline_state"
STATE_KEY_ACTIVE_RULE_VERSION = "active_rule_version"
TABLE_RUNS = "prediction_build_runs"


def _norm_prefix(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip().lower()


def _load_active_rule_version(db_path: Path | None = None) -> str | None:
    path = Path(db_path) if db_path is not None else DB_PATH
    if not path.is_file():
        return None
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            f"SELECT value FROM {PIPELINE_STATE_TABLE} WHERE key = ? LIMIT 1",
            (STATE_KEY_ACTIVE_RULE_VERSION,),
        ).fetchone()
        if row and row[0]:
            return str(row[0])
        row = conn.execute(
            f"SELECT rule_version FROM {TABLE_RUNS} WHERE is_active = 1 LIMIT 1"
        ).fetchone()
        return str(row[0]) if row and row[0] else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


@lru_cache(maxsize=4)
def _load_rule_lookup(profile: str = "list2") -> dict[str, dict]:
    try:
        if profile == "list3":
            from pattern_list3_compare import build_comparison_df, cmp_row_for_rules
            from pattern_list3_final_rules import FINAL_RULE_INFO as RULE_INFO
            from pattern_list3_final_rules import build_rule_engine, parse_rule_version

            cmp_df, _ = build_comparison_df("list3")
        else:
            from pattern_list2_final_rules import FINAL_RULE_INFO as RULE_INFO
            from pattern_list2_final_rules import build_rule_engine, parse_rule_version
            from pattern_predictions_compare_app import build_comparison_df

            cmp_df, _ = build_comparison_df("list2")
    except Exception:
        return {}

    if cmp_df is None or cmp_df.empty:
        return {}

    db_path = _db_path_for_panel(profile)
    enabled = parse_rule_version(_load_active_rule_version(db_path))
    _, classify_fn, rule_version = build_rule_engine(enabled)

    out: dict[str, dict] = {}
    for _, row in cmp_df.iterrows():
        prefix = _norm_prefix(row.get("ws9_core"))
        if not prefix:
            continue
        if profile == "list3":
            rule_id = classify_fn(cmp_row_for_rules(row))
        else:
            rule_id = classify_fn(row)
        info = RULE_INFO.get(rule_id, RULE_INFO["unknown"])
        out[prefix] = {
            "final_rule": rule_id,
            "final_rule_label": info["label"],
            "final_rule_desc": info["description"],
            "rule_version": rule_version,
        }
    return out


def _db_path_for_panel(profile: str) -> Path:
    from pattern_list_profiles import LIST2_TEST_PREDICTIONS_DB, LIST3_TEST_PREDICTIONS_DB

    if profile == "list3":
        return LIST3_TEST_PREDICTIONS_DB
    return LIST2_TEST_PREDICTIONS_DB


def _ws9_from_prediction(prefix, window_size, profile: str = "list2") -> str:
    if not prefix or not window_size:
        return ""
    p = _norm_prefix(prefix)
    ws = int(window_size)
    if profile == "list3":
        try:
            from pattern_list3_ws9_core import to_ws9_core

            return to_ws9_core(p, ws)
        except Exception:
            return p if ws == WS9_WINDOW else ""
    if ws == WS9_WINDOW:
        return p
    try:
        from pattern_predictions_compare_app import to_ws9_prefix

        return to_ws9_prefix(p, ws)
    except Exception:
        return ""


def _load_live_prefix_stats(db_path: Path | None = None) -> dict[str, dict]:
    path = Path(db_path) if db_path is not None else DB_PATH
    if not path.is_file():
        return {}
    conn = sqlite3.connect(path, timeout=20.0)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (LIVE_STEP_TABLE,),
        ).fetchone()
        if not row:
            return {}
        df = pd.read_sql_query(
            f"""
            SELECT prefix,
                   COUNT(*) AS live_n,
                   SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS hits
            FROM {LIVE_STEP_TABLE}
            WHERE window_size = ?
              AND skipped = 0
              AND is_correct IN (0, 1)
              AND LOWER(predicted) IN ('b', 'p')
            GROUP BY prefix
            """,
            conn,
            params=[WS9_WINDOW],
        )
    except (sqlite3.OperationalError, pd.errors.DatabaseError):
        return {}
    finally:
        conn.close()

    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        p = _norm_prefix(r["prefix"])
        if not p:
            continue
        live_n = int(r["live_n"])
        hits = int(r["hits"])
        out[p] = {
            "live_n": live_n,
            "hits": hits,
            "live_accuracy_pct": round(100.0 * hits / live_n, 2) if live_n > 0 else None,
        }
    return out


def build_prefix_rule_panel_rows(
    flow_result: dict,
    predict_fn,
    display_order: tuple[str, ...],
    mode_label_fn,
    db_path: Path | None = None,
    *,
    profile: str = "list2",
) -> list[dict]:
    """현재 포지션 예측 prefix별 규칙·라이브 적중률 행 생성."""
    gs = flow_result.get("grid_string") or ""
    results = flow_result.get("results") or {}
    rule_map = _load_rule_lookup(profile)
    stats_path = Path(db_path) if db_path is not None else _db_path_for_panel(profile)
    stats_map = _load_live_prefix_stats(stats_path)
    unknown = FINAL_RULE_INFO["unknown"]
    rows: list[dict] = []

    for mode in display_order:
        r = results.get(mode, {}) or {}
        state = r.get("state") or {}
        pred = predict_fn(state, gs, mode)
        prefix = pred.get("prefix")
        window_size = pred.get("window_size")
        predicted = pred.get("predicted")
        skipped = pred.get("skipped", True)

        if skipped or not prefix or not window_size:
            rows.append({
                "mode": mode_label_fn(mode),
                "prefix": "-",
                "ws9_prefix": "-",
                "predicted": "-",
                "final_rule": "-",
                "final_rule_desc": "-",
                "live_n": "-",
                "live_accuracy_pct": "-",
            })
            continue

        ws9 = _ws9_from_prediction(prefix, window_size, profile=profile)
        rule = rule_map.get(ws9) or {}
        stats = stats_map.get(ws9) or {}
        rule_id = rule.get("final_rule", "unknown")

        pred_disp = "-"
        if predicted and str(predicted).lower() not in ("pass", "none"):
            pv = str(predicted).strip().upper()
            pred_disp = pv[0] if pv else "-"

        live_n = stats.get("live_n")
        acc = stats.get("live_accuracy_pct")

        rows.append({
            "mode": mode_label_fn(mode),
            "prefix": _norm_prefix(prefix),
            "ws9_prefix": ws9 or "-",
            "predicted": pred_disp,
            "final_rule": rule.get("final_rule_label", unknown["label"])
            if ws9 else "-",
            "final_rule_desc": rule.get("final_rule_desc", unknown["description"])
            if ws9 else "-",
            "live_n": live_n if live_n is not None else "-",
            "live_accuracy_pct": f"{acc:.2f}%" if acc is not None else "-",
            "_rule_id": rule_id if ws9 else "",
        })
    return rows


def render_prefix_rule_panel(
    flow_result: dict,
    *,
    predict_fn,
    display_order: tuple[str, ...],
    mode_label_fn,
    st_module,
    db_path: Path | None = None,
) -> None:
    """예측값 테이블 아래 규칙·적중률 패널 (ENABLED=False면 no-op)."""
    if not ENABLED:
        return

    rows = build_prefix_rule_panel_rows(
        flow_result,
        predict_fn,
        display_order,
        mode_label_fn,
        db_path=db_path,
    )
    if not rows:
        return

    stats_db = Path(db_path) if db_path is not None else DB_PATH
    active_rv = _load_active_rule_version(stats_db)
    st_module.markdown("#### 예측 prefix 규칙 · 라이브 적중률 (W9)")
    st_module.caption(
        "윈도우 9 · list2 취합비교 결정 규칙(R1~R5) · ws9 prefix · "
        f"적중률=`{LIVE_STEP_TABLE}` @ `{stats_db.name}` window_size=9 누적"
        + (f" · active `{active_rv}`" if active_rv else "")
    )

    cols = [
        "ws9_prefix",
        "predicted",
        "final_rule",
        "live_n",
        "live_accuracy_pct",
        "final_rule_desc",
    ]
    if len(display_order) > 1:
        cols = ["mode"] + cols

    show = pd.DataFrame(rows)[cols].rename(columns={
        "mode": "모드",
        "ws9_prefix": "ws9 prefix",
        "predicted": "예측",
        "final_rule": "결정 규칙",
        "live_n": "라이브 n",
        "live_accuracy_pct": "라이브 적중률",
        "final_rule_desc": "규칙 설명",
    })
    st_module.dataframe(show, use_container_width=True, hide_index=True)


def render_unified_prefix_rule_panel(
    flow_result: dict,
    *,
    predict_fn,
    st_module,
) -> None:
    """통합 앱 — W9_10(list2) + W9_11(list3) 규칙·적중률 패널."""
    if not ENABLED:
        return

    from livegame_mode_registry import DISPLAY_ORDER, get_mode, mode_label

    all_rows: list[dict] = []
    for mode in DISPLAY_ORDER:
        cfg = get_mode(mode)
        rows = build_prefix_rule_panel_rows(
            flow_result,
            predict_fn,
            (mode,),
            mode_label,
            db_path=cfg.predictions_db(use_test=True),
            profile=cfg.profile,
        )
        all_rows.extend(rows)

    if not all_rows:
        return

    show = pd.DataFrame(all_rows)[
        [
            "mode",
            "ws9_prefix",
            "predicted",
            "final_rule",
            "live_n",
            "live_accuracy_pct",
        ]
    ].rename(columns={
        "mode": "모드",
        "ws9_prefix": "ws9",
        "predicted": "예측",
        "final_rule": "규칙",
        "live_n": "n",
        "live_accuracy_pct": "적중%",
    })
    with st_module.expander("prefix 규칙 · 라이브 적중률", expanded=True):
        st_module.dataframe(show, use_container_width=True, hide_index=True)
