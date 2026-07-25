"""list3 취합비교 최종 예측 → simulation_predictions_change_point (ws9 · 8자)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

import pandas as pd

from pattern_list3_compare import cmp_row_for_rules
from pattern_list_final_confidence import (
    SIM_INSERT_EXTRA_COLUMNS,
    compute_final_confidence,
    empty_confidence_row,
    migrate_sim_extra_columns,
)
from pattern_list_profiles import SOURCE_DB, TABLE_GRID, TABLE_NGRAM, PatternListProfile

TABLE_SIM = "simulation_predictions_change_point"
METHOD = "빈도 기반"
THRESHOLD = 0.0
WINDOW_SIZE = 9

SIM_DDL = """
CREATE TABLE IF NOT EXISTS simulation_predictions_change_point (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_size INTEGER NOT NULL,
    prefix TEXT NOT NULL,
    predicted_value TEXT,
    confidence REAL,
    b_ratio REAL,
    p_ratio REAL,
    method TEXT NOT NULL,
    threshold REAL NOT NULL,
    pred_frequency REAL,
    created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
    updated_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
    sim_win_rate_pct REAL,
    final_rule TEXT,
    rule_version TEXT,
    agree_count INTEGER,
    agree_count_new_three INTEGER,
    rule_confidence REAL,
    conf_source TEXT,
    min_agree_conf REAL,
    mean_agree_conf REAL,
    legacy_confidence REAL,
    UNIQUE(window_size, prefix, method, threshold)
)
"""

_INSERT_COLS = [
    "window_size",
    "prefix",
    "predicted_value",
    "confidence",
    "b_ratio",
    "p_ratio",
    "method",
    "threshold",
    "pred_frequency",
    "sim_win_rate_pct",
    *SIM_INSERT_EXTRA_COLUMNS,
]


def _norm_prefix(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip().lower()


def _load_metric_lookup(db_path, table: str, window_size: int | None = None) -> dict[str, dict]:
    conn = sqlite3.connect(db_path)
    try:
        q = f"""
            SELECT prefix, predicted_value, confidence, b_ratio, p_ratio, pred_frequency
            FROM {table}
            WHERE method = ? AND threshold = ?
        """
        params: list = [METHOD, THRESHOLD]
        if window_size is not None:
            q += " AND window_size = ?"
            params.append(window_size)
        df = pd.read_sql_query(q, conn, params=params)
    finally:
        conn.close()

    out = {}
    for _, r in df.iterrows():
        p = _norm_prefix(r["prefix"])
        if p:
            out[p] = {
                "confidence": r["confidence"],
                "b_ratio": r["b_ratio"],
                "p_ratio": r["p_ratio"],
                "pred_frequency": r["pred_frequency"],
            }
    return out


def _load_sim_win_rate_lookup() -> dict[str, float | None]:
    conn = sqlite3.connect(SOURCE_DB)
    try:
        df = pd.read_sql_query(
            """
            SELECT prefix, sim_win_rate_pct
            FROM simulation_predictions_change_point
            WHERE window_size = 9 AND method = ? AND threshold = ?
            """,
            conn,
            params=[METHOD, THRESHOLD],
        )
    finally:
        conn.close()
    return {
        _norm_prefix(r["prefix"]): r["sim_win_rate_pct"]
        for _, r in df.iterrows()
        if _norm_prefix(r["prefix"])
    }


def _pick_metrics(
    row: pd.Series,
    ngram_lookup: dict,
    grid11_lookup: dict,
    sim_lookup: dict,
    final_pred: str,
) -> dict:
    ws13 = _norm_prefix(row.get("ws13_full"))
    ws11 = _norm_prefix(row.get("ws11_full"))
    ws9 = _norm_prefix(row.get("ws9_core"))

    def _as_bp(v) -> str | None:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        s = str(v).strip().lower()
        return s if s in ("b", "p") else None

    fp = final_pred.lower() if final_pred and final_pred != "pass" else ""
    sim_p = _as_bp(row.get("sim_pred"))
    ng_p = _as_bp(row.get("ngram13_pred"))

    prefer_sim = bool(fp and sim_p == fp and ng_p is not None and ng_p != fp)
    if prefer_sim and ws9 and ws9 in sim_lookup:
        return sim_lookup[ws9]

    for key in (ws13, ws11, ws9):
        if key and key in ngram_lookup:
            return ngram_lookup[key]
    if ws11 and ws11 in grid11_lookup:
        return grid11_lookup[ws11]
    if ws9 and ws9 in sim_lookup:
        return sim_lookup[ws9]

    conf = row.get("ngram13_conf") or row.get("grid11_conf") or row.get("sim_conf")
    freq = row.get("ngram13_freq") or row.get("grid11_freq") or row.get("sim_freq")
    if prefer_sim:
        conf = row.get("sim_conf") or conf
        freq = row.get("sim_freq") or freq
    if conf is not None and not pd.isna(conf) and final_pred in ("B", "P"):
        fp2 = final_pred.lower()
        c = float(conf)
        b_ratio = c if fp2 == "b" else 100.0 - c
        p_ratio = c if fp2 == "p" else 100.0 - c
        return {
            "confidence": c,
            "b_ratio": b_ratio,
            "p_ratio": p_ratio,
            "pred_frequency": freq,
        }
    return {
        "confidence": None,
        "b_ratio": None,
        "p_ratio": None,
        "pred_frequency": freq,
    }


def build_simulation_predictions_df(
    cmp_df: pd.DataFrame,
    profile: PatternListProfile,
    final_pred_fn: Callable[[pd.Series], str],
    classify_final_rule_fn: Callable[[pd.Series], str] | None = None,
    rule_version: str | None = None,
) -> pd.DataFrame:
    if classify_final_rule_fn is None or rule_version is None:
        from pattern_list3_final_rules import (
            DEFAULT_RULE_VERSION,
            classify_final_rule,
        )

        if classify_final_rule_fn is None:
            classify_final_rule_fn = classify_final_rule
        if rule_version is None:
            rule_version = DEFAULT_RULE_VERSION

    ngram_lookup = _load_metric_lookup(profile.predictions_db, TABLE_NGRAM, window_size=13)
    grid11_lookup = _load_metric_lookup(profile.predictions_db, TABLE_GRID, window_size=11)
    sim_lookup = _load_metric_lookup(SOURCE_DB, TABLE_SIM, window_size=9)
    sim_wr_lookup = _load_sim_win_rate_lookup()

    rows = []
    for _, row in cmp_df.iterrows():
        rule_row = cmp_row_for_rules(row)
        final = final_pred_fn(rule_row)
        final_rule = classify_final_rule_fn(rule_row)
        prefix = _norm_prefix(row["ws9_core"])
        if not prefix:
            continue

        is_pass = final == "pass"
        if is_pass:
            metrics = {
                "confidence": None,
                "b_ratio": None,
                "p_ratio": None,
                "pred_frequency": None,
            }
            conf_meta = empty_confidence_row(final_rule)
            predicted_value = None
        else:
            metrics = _pick_metrics(row, ngram_lookup, grid11_lookup, sim_lookup, final)
            conf_meta = compute_final_confidence(
                rule_row,
                final_rule=final_rule,
                final_pred=final,
                profile="list3",
            )
            predicted_value = final.lower()

        rule_conf = conf_meta.get("rule_confidence")
        legacy_conf = metrics.get("confidence")

        rows.append(
            {
                "window_size": WINDOW_SIZE,
                "prefix": prefix,
                "predicted_value": predicted_value,
                "confidence": rule_conf,
                "b_ratio": metrics["b_ratio"],
                "p_ratio": metrics["p_ratio"],
                "method": METHOD,
                "threshold": THRESHOLD,
                "pred_frequency": metrics["pred_frequency"],
                "sim_win_rate_pct": sim_wr_lookup.get(prefix),
                "final_rule": conf_meta["final_rule"],
                "rule_version": rule_version,
                "agree_count": conf_meta["agree_count"],
                "agree_count_new_three": conf_meta["agree_count_new_three"],
                "rule_confidence": rule_conf,
                "conf_source": conf_meta["conf_source"],
                "min_agree_conf": conf_meta["min_agree_conf"],
                "mean_agree_conf": conf_meta["mean_agree_conf"],
                "legacy_confidence": legacy_conf,
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values("prefix", kind="stable").reset_index(drop=True)


def ensure_simulation_predictions_table(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(SIM_DDL)
    migrate_sim_extra_columns(conn, TABLE_SIM)
    cursor.execute(
        f"CREATE INDEX IF NOT EXISTS idx_sim_cp_sp_window_prefix "
        f"ON {TABLE_SIM}(window_size, prefix)"
    )
    cursor.execute(
        f"CREATE INDEX IF NOT EXISTS idx_sim_cp_sp_method_threshold "
        f"ON {TABLE_SIM}(method, threshold)"
    )


def _row_to_insert_tuple(r) -> tuple:
    return (
        int(r.window_size),
        r.prefix,
        r.predicted_value,
        float(r.confidence) if pd.notna(r.confidence) else None,
        float(r.b_ratio) if pd.notna(r.b_ratio) else None,
        float(r.p_ratio) if pd.notna(r.p_ratio) else None,
        r.method,
        float(r.threshold),
        float(r.pred_frequency) if pd.notna(r.pred_frequency) else None,
        float(r.sim_win_rate_pct) if pd.notna(getattr(r, "sim_win_rate_pct", None)) else None,
        getattr(r, "final_rule", None),
        getattr(r, "rule_version", None),
        int(r.agree_count) if pd.notna(getattr(r, "agree_count", None)) else None,
        int(r.agree_count_new_three)
        if pd.notna(getattr(r, "agree_count_new_three", None))
        else None,
        float(r.rule_confidence) if pd.notna(getattr(r, "rule_confidence", None)) else None,
        getattr(r, "conf_source", None),
        float(r.min_agree_conf) if pd.notna(getattr(r, "min_agree_conf", None)) else None,
        float(r.mean_agree_conf) if pd.notna(getattr(r, "mean_agree_conf", None)) else None,
        float(r.legacy_confidence)
        if pd.notna(getattr(r, "legacy_confidence", None))
        else None,
    )


def save_simulation_predictions(
    profile: PatternListProfile,
    pred_df: pd.DataFrame,
    *,
    replace: bool = False,
    db_path=None,
) -> int:
    target = db_path or profile.predictions_db
    target = Path(target) if not isinstance(target, Path) else target
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    cursor = conn.cursor()
    try:
        if replace:
            cursor.execute(f"DROP TABLE IF EXISTS {TABLE_SIM}")
        ensure_simulation_predictions_table(conn)

        if pred_df.empty:
            conn.commit()
            return 0

        col_sql = ", ".join(_INSERT_COLS)
        placeholders = ", ".join(["?"] * len(_INSERT_COLS))
        insert_sql = f"""
            INSERT OR REPLACE INTO {TABLE_SIM} (
                {col_sql}, updated_at
            ) VALUES ({placeholders}, datetime('now', '+9 hours'))
        """
        records = [_row_to_insert_tuple(r) for r in pred_df.itertuples(index=False)]
        cursor.executemany(insert_sql, records)
        conn.commit()
        return len(records)
    finally:
        conn.close()


def count_simulation_predictions(profile: PatternListProfile) -> int:
    if not profile.predictions_db.is_file():
        return 0
    conn = sqlite3.connect(profile.predictions_db)
    try:
        cur = conn.execute(
            f"SELECT COUNT(*) FROM {TABLE_SIM} WHERE method = ? AND threshold = ?",
            (METHOD, THRESHOLD),
        )
        return int(cur.fetchone()[0])
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def copy_sim_table_to_live(profile: PatternListProfile, live_db_path=None) -> int:
    from pattern_list_profiles import LIST3_LIVE_PREDICTIONS_DB

    live_path = Path(live_db_path or LIST3_LIVE_PREDICTIONS_DB)
    if not profile.predictions_db.is_file():
        return 0
    conn = sqlite3.connect(profile.predictions_db)
    try:
        df = pd.read_sql_query(
            f"SELECT * FROM {TABLE_SIM} WHERE method = ? AND threshold = ?",
            conn,
            params=[METHOD, THRESHOLD],
        )
    finally:
        conn.close()
    if df.empty:
        return 0
    cols = [c for c in _INSERT_COLS if c in df.columns]
    return save_simulation_predictions(profile, df[cols], replace=True, db_path=live_path)
