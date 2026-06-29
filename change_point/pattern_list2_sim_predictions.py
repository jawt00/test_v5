"""list2 취합비교 최종 예측 → simulation_predictions_change_point 형식 저장."""

from __future__ import annotations

import sqlite3
from typing import Callable

import pandas as pd

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
    UNIQUE(window_size, prefix, method, threshold)
)
"""


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
    grid10_lookup: dict,
    sim_lookup: dict,
    final_pred: str,
) -> dict:
    ws12 = _norm_prefix(row.get("ws12_full"))
    ws10 = _norm_prefix(row.get("ws10_full"))
    ws9 = _norm_prefix(row.get("ws9_core"))

    for key in (ws12, ws10, ws9):
        if key and key in ngram_lookup:
            return ngram_lookup[key]
    if ws10 and ws10 in grid10_lookup:
        return grid10_lookup[ws10]
    if ws9 and ws9 in sim_lookup:
        return sim_lookup[ws9]

    conf = row.get("ngram12_conf") or row.get("grid10_conf") or row.get("sim_conf")
    freq = row.get("ngram12_freq") or row.get("grid10_freq") or row.get("sim_freq")
    if conf is not None and not pd.isna(conf) and final_pred in ("B", "P"):
        fp = final_pred.lower()
        c = float(conf)
        b_ratio = c if fp == "b" else 100.0 - c
        p_ratio = c if fp == "p" else 100.0 - c
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
) -> pd.DataFrame:
    """최종 예측 전체(ws9 prefix), pass는 predicted_value=NULL (livegame 스킵 호환)."""
    ngram_lookup = _load_metric_lookup(profile.predictions_db, TABLE_NGRAM, window_size=12)
    grid10_lookup = _load_metric_lookup(profile.predictions_db, TABLE_GRID, window_size=10)
    sim_lookup = _load_metric_lookup(SOURCE_DB, TABLE_SIM, window_size=9)
    sim_wr_lookup = _load_sim_win_rate_lookup()

    rows = []
    for _, row in cmp_df.iterrows():
        final = final_pred_fn(row)
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
            predicted_value = None
        else:
            metrics = _pick_metrics(row, ngram_lookup, grid10_lookup, sim_lookup, final)
            predicted_value = final.lower()

        sim_wr = sim_wr_lookup.get(prefix)

        rows.append(
            {
                "window_size": WINDOW_SIZE,
                "prefix": prefix,
                "predicted_value": predicted_value,
                "confidence": metrics["confidence"],
                "b_ratio": metrics["b_ratio"],
                "p_ratio": metrics["p_ratio"],
                "method": METHOD,
                "threshold": THRESHOLD,
                "pred_frequency": metrics["pred_frequency"],
                "sim_win_rate_pct": sim_wr,
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values("prefix", kind="stable").reset_index(drop=True)


def ensure_simulation_predictions_table(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(SIM_DDL)
    cursor.execute(
        f"CREATE INDEX IF NOT EXISTS idx_sim_cp_sp_window_prefix "
        f"ON {TABLE_SIM}(window_size, prefix)"
    )
    cursor.execute(
        f"CREATE INDEX IF NOT EXISTS idx_sim_cp_sp_method_threshold "
        f"ON {TABLE_SIM}(method, threshold)"
    )


def save_simulation_predictions(
    profile: PatternListProfile,
    pred_df: pd.DataFrame,
    *,
    replace: bool = False,
) -> int:
    profile.predictions_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(profile.predictions_db)
    cursor = conn.cursor()
    try:
        if replace:
            cursor.execute(f"DROP TABLE IF EXISTS {TABLE_SIM}")
        ensure_simulation_predictions_table(conn)

        if pred_df.empty:
            conn.commit()
            return 0

        insert_sql = f"""
            INSERT OR REPLACE INTO {TABLE_SIM} (
                window_size, prefix, predicted_value, confidence,
                b_ratio, p_ratio, method, threshold, pred_frequency,
                sim_win_rate_pct, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
        """
        records = [
            (
                int(r.window_size),
                r.prefix,
                r.predicted_value,
                float(r.confidence) if pd.notna(r.confidence) else None,
                float(r.b_ratio) if pd.notna(r.b_ratio) else None,
                float(r.p_ratio) if pd.notna(r.p_ratio) else None,
                r.method,
                float(r.threshold),
                float(r.pred_frequency) if pd.notna(r.pred_frequency) else None,
                float(r.sim_win_rate_pct) if pd.notna(r.sim_win_rate_pct) else None,
            )
            for r in pred_df.itertuples(index=False)
        ]
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
