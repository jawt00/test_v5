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

ENABLED = True

DB_PATH = Path(__file__).resolve().parent / "pattern_list2.db"
LIVE_STEP_TABLE = "live_step_results"
WS9_WINDOW = 9

FINAL_RULE_INFO = {
    "R1": {
        "label": "R1 · 3-way 일치",
        "description": "grid10 = grid12 = ngram12 일치 → 해당 예측값 사용",
    },
    "R2": {
        "label": "R2 · ngram 불일치",
        "description": "sim ≠ ngram12 → sim 예측값 사용",
    },
    "R3": {
        "label": "R3 · sim=ngram=grid10",
        "description": "sim = ngram12 = grid10 → ngram12 예측값 사용",
    },
    "R4": {
        "label": "R4 · pass",
        "description": "위 조건 해당 없음 → 예측 없음 (pass)",
    },
    "unknown": {
        "label": "미분류",
        "description": "pattern_list2에 없는 prefix",
    },
}


def classify_final_rule(row: pd.Series) -> str:
    if row.get("agree_new_three") is True:
        return "R1"
    if row.get("agree_sim_ngram") is False:
        return "R2"
    if row.get("agree_sim_ngram") is True and row.get("agree_sim_grid10") is True:
        return "R3"
    return "R4"


def _norm_prefix(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip().lower()


@lru_cache(maxsize=1)
def _load_rule_lookup() -> dict[str, dict]:
    try:
        from pattern_predictions_compare_app import build_comparison_df

        cmp_df, _ = build_comparison_df("list2")
    except Exception:
        return {}

    if cmp_df is None or cmp_df.empty:
        return {}

    out: dict[str, dict] = {}
    for _, row in cmp_df.iterrows():
        prefix = _norm_prefix(row.get("ws9_core"))
        if not prefix:
            continue
        rule_id = classify_final_rule(row)
        info = FINAL_RULE_INFO.get(rule_id, FINAL_RULE_INFO["unknown"])
        out[prefix] = {
            "final_rule": rule_id,
            "final_rule_label": info["label"],
            "final_rule_desc": info["description"],
        }
    return out


def _load_live_prefix_stats(db_path: Path | None = None) -> dict[str, dict]:
    path = Path(db_path) if db_path is not None else DB_PATH
    if not path.is_file():
        return {}
    conn = sqlite3.connect(path, timeout=20.0)
    try:
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
    except sqlite3.OperationalError:
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


def _ws9_from_prediction(prefix, window_size) -> str:
    if not prefix or not window_size:
        return ""
    p = _norm_prefix(prefix)
    ws = int(window_size)
    if ws == WS9_WINDOW:
        return p
    try:
        from pattern_predictions_compare_app import to_ws9_prefix

        return to_ws9_prefix(p, ws)
    except Exception:
        return ""


def build_prefix_rule_panel_rows(
    flow_result: dict,
    predict_fn,
    display_order: tuple[str, ...],
    mode_label_fn,
    db_path: Path | None = None,
) -> list[dict]:
    """현재 포지션 예측 prefix별 규칙·라이브 적중률 행 생성."""
    gs = flow_result.get("grid_string") or ""
    results = flow_result.get("results") or {}
    rule_map = _load_rule_lookup()
    stats_map = _load_live_prefix_stats(db_path)
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

        ws9 = _ws9_from_prediction(prefix, window_size)
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
    st_module.markdown("#### 예측 prefix 규칙 · 라이브 적중률 (W9)")
    st_module.caption(
        "윈도우 9 · list2 취합비교 결정 규칙(R1~R3) · ws9 prefix · "
        f"적중률=`{LIVE_STEP_TABLE}` @ `{stats_db.name}` window_size=9 누적"
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
