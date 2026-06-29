"""
pattern_list export CSV → 빈도 기반 예측 테이블 2개 (profile별 predictions_db).

실행:
  python3 change_point/build_pattern_list_predictions.py --profile list1
  python3 change_point/build_pattern_list_predictions.py --profile list2
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pattern_list_profiles import (
    GRID_CSV_NAME,
    NGRAM_CSV_NAME,
    TABLE_GRID,
    TABLE_NGRAM,
    PatternListProfile,
    get_profile,
)

METHOD = "빈도 기반"
THRESHOLD = 0.0

PREDICTIONS_DDL = """
CREATE TABLE {table_name} (
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
    UNIQUE(window_size, prefix, method, threshold)
)
"""


def _norm_suffix(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().lower()
    return s if s in ("b", "p") else None


def build_frequency_predictions(df: pd.DataFrame, prefix_col: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "window_size",
                "prefix",
                "predicted_value",
                "confidence",
                "b_ratio",
                "p_ratio",
                "method",
                "threshold",
                "pred_frequency",
            ]
        )

    work = df.copy()
    work["window_size"] = work["window_size"].astype(int)
    work["prefix"] = work[prefix_col].astype(str).str.strip().str.lower()
    work["suffix_norm"] = work["suffix"].map(_norm_suffix)
    work = work[work["suffix_norm"].notna() & work["prefix"].astype(bool)]

    rows = []
    for (window_size, prefix), grp in work.groupby(["window_size", "prefix"], sort=True):
        counts = Counter(grp["suffix_norm"])
        total = sum(counts.values())
        if total == 0:
            continue

        predicted = counts.most_common(1)[0][0]
        b_count = counts.get("b", 0)
        p_count = counts.get("p", 0)
        b_ratio = b_count / total * 100.0
        p_ratio = p_count / total * 100.0
        confidence = max(b_ratio, p_ratio)

        rows.append(
            {
                "window_size": int(window_size),
                "prefix": prefix,
                "predicted_value": predicted,
                "confidence": confidence,
                "b_ratio": b_ratio,
                "p_ratio": p_ratio,
                "method": METHOD,
                "threshold": THRESHOLD,
                "pred_frequency": float(total),
            }
        )

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["window_size", "prefix"], kind="stable").reset_index(
            drop=True
        )
    return out


def ensure_predictions_table(conn: sqlite3.Connection, table_name: str) -> None:
    cursor = conn.cursor()
    cursor.execute(PREDICTIONS_DDL.format(table_name=table_name).replace(
        f"CREATE TABLE {table_name}", f"CREATE TABLE IF NOT EXISTS {table_name}"
    ))
    cursor.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_window_prefix "
        f"ON {table_name}(window_size, prefix)"
    )
    cursor.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_method_threshold "
        f"ON {table_name}(method, threshold)"
    )


def upsert_predictions_table(
    conn: sqlite3.Connection,
    table_name: str,
    pred_df: pd.DataFrame,
) -> int:
    ensure_predictions_table(conn, table_name)
    if pred_df.empty:
        conn.commit()
        return 0

    insert_sql = f"""
        INSERT OR REPLACE INTO {table_name} (
            window_size, prefix, predicted_value, confidence,
            b_ratio, p_ratio, method, threshold, pred_frequency, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
    """
    records = [
        (
            int(r.window_size),
            r.prefix,
            r.predicted_value,
            float(r.confidence),
            float(r.b_ratio),
            float(r.p_ratio),
            r.method,
            float(r.threshold),
            float(r.pred_frequency),
        )
        for r in pred_df.itertuples(index=False)
    ]
    conn.executemany(insert_sql, records)
    conn.commit()
    return len(records)


def create_and_load_predictions_table(
    conn: sqlite3.Connection,
    table_name: str,
    pred_df: pd.DataFrame,
) -> int:
    cursor = conn.cursor()
    cursor.execute(f"DROP TABLE IF EXISTS {table_name}")
    cursor.execute(PREDICTIONS_DDL.format(table_name=table_name))
    cursor.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_window_prefix "
        f"ON {table_name}(window_size, prefix)"
    )
    cursor.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_method_threshold "
        f"ON {table_name}(method, threshold)"
    )

    if pred_df.empty:
        conn.commit()
        return 0

    insert_sql = f"""
        INSERT INTO {table_name} (
            window_size, prefix, predicted_value, confidence,
            b_ratio, p_ratio, method, threshold, pred_frequency
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    records = [
        (
            int(r.window_size),
            r.prefix,
            r.predicted_value,
            float(r.confidence),
            float(r.b_ratio),
            float(r.p_ratio),
            r.method,
            float(r.threshold),
            float(r.pred_frequency),
        )
        for r in pred_df.itertuples(index=False)
    ]
    cursor.executemany(insert_sql, records)
    conn.commit()
    return len(records)


def run_build(profile: PatternListProfile) -> None:
    grid_csv = profile.export_dir / GRID_CSV_NAME
    ngram_csv = profile.export_dir / NGRAM_CSV_NAME

    if not grid_csv.is_file():
        raise FileNotFoundError(
            f"Run extract first. Missing: {grid_csv}"
        )
    if not ngram_csv.is_file():
        raise FileNotFoundError(
            f"Run extract first. Missing: {ngram_csv}"
        )

    grid_chunks = pd.read_csv(grid_csv)
    ngram_chunks = pd.read_csv(ngram_csv)

    grid_pred = build_frequency_predictions(grid_chunks, prefix_col="csv_prefix")
    ngram_pred = build_frequency_predictions(ngram_chunks, prefix_col="prefix")

    profile.predictions_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(profile.predictions_db)
    try:
        n_grid = create_and_load_predictions_table(conn, TABLE_GRID, grid_pred)
        n_ngram = create_and_load_predictions_table(conn, TABLE_NGRAM, ngram_pred)
    finally:
        conn.close()

    ws10_n = len(grid_pred[grid_pred.window_size == 10]) if not grid_pred.empty else 0
    ws12_n = len(grid_pred[grid_pred.window_size == 12]) if not grid_pred.empty else 0
    print(f"Profile: {profile.name}")
    print(f"DB: {profile.predictions_db}")
    print(f"[1] {TABLE_GRID}: {n_grid} rows (ws10={ws10_n}, ws12={ws12_n})")
    print(f"[2] {TABLE_NGRAM}: {n_ngram} rows")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build pattern list prediction tables")
    parser.add_argument(
        "--profile",
        default="list1",
        choices=["list1", "list2"],
        help="pattern list profile (default: list1)",
    )
    args = parser.parse_args()
    run_build(get_profile(args.profile))


if __name__ == "__main__":
    main()
