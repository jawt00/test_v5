"""
pattern_list CSV의 prefix를 기준으로 change_point_ngram.db(원본)에서 데이터 추출.

요구 1: preprocessed_grid_strings — grid_string이 prefix로 시작 → window_size 자르기
요구 2: ngram_chunks_change_point — window_size=12 prefix 매칭

예측 테이블은 build_pattern_list_predictions.py → profile별 predictions_db

실행:
  python3 change_point/extract_pattern_list_data.py --profile list1
  python3 change_point/extract_pattern_list_data.py --profile list2
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pattern_list_profiles import (
    GRID_CSV_NAME,
    NGRAM_CSV_NAME,
    SOURCE_DB,
    PatternListProfile,
    get_profile,
)

TABLE_GRID_STAGING = "grid_chunks_staging"
TABLE_NGRAM_STAGING = "ngram_chunks_staging"
STATE_KEY_LAST_GRID_ID = "last_grid_string_id"

GRID_STAGING_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_GRID_STAGING} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_size INTEGER NOT NULL,
    csv_prefix TEXT NOT NULL,
    grid_string_id INTEGER NOT NULL,
    string_length INTEGER,
    grid_string TEXT,
    full_chunk TEXT,
    suffix TEXT,
    UNIQUE(grid_string_id, window_size, csv_prefix)
)
"""

NGRAM_STAGING_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NGRAM_STAGING} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ngram_id INTEGER,
    grid_string_id INTEGER NOT NULL,
    window_size INTEGER NOT NULL,
    chunk_index INTEGER,
    prefix TEXT NOT NULL,
    suffix TEXT,
    full_chunk TEXT,
    created_at TEXT,
    grid_string TEXT,
    string_length INTEGER,
    UNIQUE(ngram_id)
)
"""


def get_max_source_grid_string_id() -> int:
    conn = get_source_connection()
    try:
        row = conn.execute("SELECT MAX(id) FROM preprocessed_grid_strings").fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def load_grid_strings_from_source(since_grid_string_id: int | None = None) -> pd.DataFrame:
    conn = get_source_connection()
    try:
        if since_grid_string_id is not None and since_grid_string_id > 0:
            gs_df = pd.read_sql_query(
                """
                SELECT id AS grid_string_id, grid_string, string_length
                FROM preprocessed_grid_strings
                WHERE id > ?
                """,
                conn,
                params=[since_grid_string_id],
            )
        else:
            gs_df = pd.read_sql_query(
                "SELECT id AS grid_string_id, grid_string, string_length FROM preprocessed_grid_strings",
                conn,
            )
    finally:
        conn.close()
    gs_df["grid_string"] = gs_df["grid_string"].astype(str).str.strip().str.lower()
    return gs_df


def get_source_connection() -> sqlite3.Connection:
    return sqlite3.connect(SOURCE_DB)


def load_patterns(profile: PatternListProfile) -> pd.DataFrame:
    df = pd.read_csv(profile.pattern_csv)
    df["window_size"] = df["window_size"].astype(int)
    df["prefix"] = df["prefix"].astype(str).str.strip().str.lower()
    return df


def extract_grid_prefix_chunks(
    patterns: pd.DataFrame,
    *,
    since_grid_string_id: int | None = None,
    gs_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if gs_df is None:
        gs_df = load_grid_strings_from_source(since_grid_string_id)

    rows = []
    for _, pat in patterns.iterrows():
        ws = int(pat["window_size"])
        prefix = pat["prefix"]
        if not prefix:
            continue

        matched = gs_df[
            gs_df["grid_string"].str.startswith(prefix)
            & (gs_df["string_length"] >= ws)
        ]
        for _, row in matched.iterrows():
            gs = row["grid_string"]
            full_chunk = gs[:ws]
            rows.append(
                {
                    "window_size": ws,
                    "csv_prefix": prefix,
                    "grid_string_id": row["grid_string_id"],
                    "string_length": row["string_length"],
                    "grid_string": gs,
                    "full_chunk": full_chunk,
                    "suffix": full_chunk[-1] if full_chunk else None,
                }
            )

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(
            ["window_size", "csv_prefix", "grid_string_id"],
            kind="stable",
        ).reset_index(drop=True)
    return out


def extract_ngram_ws12(
    patterns: pd.DataFrame,
    *,
    since_grid_string_id: int | None = None,
) -> pd.DataFrame:
    prefixes_12 = (
        patterns.loc[patterns["window_size"] == 12, "prefix"]
        .dropna()
        .unique()
        .tolist()
    )
    if not prefixes_12:
        return pd.DataFrame()

    conn = get_source_connection()
    try:
        placeholders = ",".join(["?"] * len(prefixes_12))
        params: list = list(prefixes_12)
        grid_filter = ""
        if since_grid_string_id is not None and since_grid_string_id > 0:
            grid_filter = " AND n.grid_string_id > ?"
            params.append(since_grid_string_id)
        query = f"""
            SELECT
                n.id AS ngram_id,
                n.grid_string_id,
                n.window_size,
                n.chunk_index,
                TRIM(n.prefix) AS prefix,
                n.suffix,
                n.full_chunk,
                n.created_at,
                p.grid_string,
                p.string_length
            FROM ngram_chunks_change_point n
            JOIN preprocessed_grid_strings p ON p.id = n.grid_string_id
            WHERE n.window_size = 12
              AND LOWER(TRIM(n.prefix)) IN ({placeholders})
              {grid_filter}
            ORDER BY n.prefix, n.grid_string_id, n.chunk_index
        """
        df = pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()

    return df


def ensure_staging_schema(conn: sqlite3.Connection) -> None:
    conn.execute(GRID_STAGING_DDL)
    conn.execute(NGRAM_STAGING_DDL)
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE_GRID_STAGING}_prefix "
        f"ON {TABLE_GRID_STAGING}(csv_prefix, window_size)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NGRAM_STAGING}_prefix "
        f"ON {TABLE_NGRAM_STAGING}(prefix)"
    )
    conn.commit()


def clear_staging_tables(conn: sqlite3.Connection) -> None:
    conn.execute(f"DELETE FROM {TABLE_GRID_STAGING}")
    conn.execute(f"DELETE FROM {TABLE_NGRAM_STAGING}")
    conn.commit()


def upsert_grid_staging(conn: sqlite3.Connection, grid_df: pd.DataFrame) -> int:
    if grid_df.empty:
        return 0
    sql = f"""
        INSERT OR IGNORE INTO {TABLE_GRID_STAGING} (
            window_size, csv_prefix, grid_string_id, string_length,
            grid_string, full_chunk, suffix
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    records = [
        (
            int(r.window_size),
            str(r.csv_prefix).strip().lower(),
            int(r.grid_string_id),
            int(r.string_length) if pd.notna(r.string_length) else None,
            r.grid_string,
            r.full_chunk,
            r.suffix,
        )
        for r in grid_df.itertuples(index=False)
    ]
    conn.executemany(sql, records)
    conn.commit()
    return len(records)


def upsert_ngram_staging(conn: sqlite3.Connection, ngram_df: pd.DataFrame) -> int:
    if ngram_df.empty:
        return 0
    sql = f"""
        INSERT OR IGNORE INTO {TABLE_NGRAM_STAGING} (
            ngram_id, grid_string_id, window_size, chunk_index, prefix,
            suffix, full_chunk, created_at, grid_string, string_length
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    records = []
    for r in ngram_df.itertuples(index=False):
        ngram_id = getattr(r, "ngram_id", None)
        if ngram_id is None or (isinstance(ngram_id, float) and pd.isna(ngram_id)):
            continue
        records.append(
            (
                int(ngram_id),
                int(r.grid_string_id),
                int(r.window_size),
                int(r.chunk_index) if pd.notna(r.chunk_index) else None,
                str(r.prefix).strip().lower(),
                r.suffix,
                r.full_chunk,
                r.created_at,
                r.grid_string,
                int(r.string_length) if pd.notna(r.string_length) else None,
            )
        )
    if not records:
        return 0
    conn.executemany(sql, records)
    conn.commit()
    return len(records)


def load_grid_staging(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(f"SELECT * FROM {TABLE_GRID_STAGING}", conn)


def load_ngram_staging(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(f"SELECT * FROM {TABLE_NGRAM_STAGING}", conn)


def sync_staging_to_db(
    profile: PatternListProfile,
    *,
    since_grid_string_id: int | None = None,
    full: bool = False,
) -> dict:
    """원본 DB → pattern_list2.db staging 적재. 반환: grid_rows, ngram_rows."""
    patterns = load_patterns(profile)
    profile.predictions_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(profile.predictions_db)
    try:
        ensure_staging_schema(conn)
        if full:
            clear_staging_tables(conn)
            since_grid_string_id = None

        grid_new = extract_grid_prefix_chunks(patterns, since_grid_string_id=since_grid_string_id)
        ngram_new = extract_ngram_ws12(patterns, since_grid_string_id=since_grid_string_id)
        grid_n = upsert_grid_staging(conn, grid_new)
        ngram_n = upsert_ngram_staging(conn, ngram_new)
        return {"grid_rows_synced": grid_n, "ngram_rows_synced": ngram_n}
    finally:
        conn.close()


def run_extract(profile: PatternListProfile) -> None:
    if not profile.pattern_csv.is_file():
        raise FileNotFoundError(f"pattern CSV not found: {profile.pattern_csv}")

    patterns = load_patterns(profile)
    profile.export_dir.mkdir(parents=True, exist_ok=True)

    grid_output = profile.export_dir / GRID_CSV_NAME
    ngram_output = profile.export_dir / NGRAM_CSV_NAME

    grid_df = extract_grid_prefix_chunks(patterns)
    grid_df.to_csv(grid_output, index=False, encoding="utf-8-sig")

    ngram_df = extract_ngram_ws12(patterns)
    ngram_df.to_csv(ngram_output, index=False, encoding="utf-8-sig")

    prefixes_12 = set(
        patterns.loc[patterns["window_size"] == 12, "prefix"].str.lower()
    )
    found_12 = (
        set(ngram_df["prefix"].str.strip().str.lower()) if not ngram_df.empty else set()
    )
    missing_12 = prefixes_12 - found_12

    ws10_n = len(patterns[patterns.window_size == 10])
    ws12_n = len(patterns[patterns.window_size == 12])
    print(f"Profile: {profile.name} ({profile.pattern_csv.name})")
    print(f"Patterns: {len(patterns)} (ws10={ws10_n}, ws12={ws12_n})")
    print(f"Source DB: {SOURCE_DB}")
    print(f"[1] grid -> {grid_output}")
    print(f"    rows: {len(grid_df)}, unique prefixes: {grid_df['csv_prefix'].nunique() if not grid_df.empty else 0}")
    print(f"[2] ngram ws12 -> {ngram_output}")
    print(f"    rows: {len(ngram_df)}, unique prefixes: {ngram_df['prefix'].nunique() if not ngram_df.empty else 0}")
    if missing_12:
        print(f"    ws12 prefixes with no ngram rows: {len(missing_12)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract pattern list data from source DB")
    parser.add_argument(
        "--profile",
        default="list1",
        choices=["list1", "list2"],
        help="pattern list profile (default: list1)",
    )
    args = parser.parse_args()
    run_extract(get_profile(args.profile))


if __name__ == "__main__":
    main()
