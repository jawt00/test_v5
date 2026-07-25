"""
pattern_list3 CSV → change_point_ngram.db extract (ws11 grid + ws13 ngram).

실행:
  python3 change_point/extract_pattern_list3_data.py --profile list3
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_pattern_list_data import (
    GRID_STAGING_DDL,
    NGRAM_STAGING_DDL,
    STATE_KEY_LAST_GRID_ID,
    TABLE_GRID_STAGING,
    TABLE_NGRAM_STAGING,
    clear_staging_tables,
    ensure_staging_schema,
    extract_grid_prefix_chunks,
    get_max_source_grid_string_id,
    get_source_connection,
    load_grid_staging,
    load_ngram_staging,
    load_patterns,
    upsert_grid_staging,
    upsert_ngram_staging,
)
from pattern_list_profiles import (
    GRID_CSV_NAME,
    NGRAM_CSV_NAME_LIST3,
    SOURCE_DB,
    PatternListProfile,
    get_profile,
)

NGRAM_CSV_NAME = NGRAM_CSV_NAME_LIST3


def extract_ngram_ws13(
    patterns: pd.DataFrame,
    *,
    since_grid_string_id: int | None = None,
) -> pd.DataFrame:
    prefixes_13 = (
        patterns.loc[patterns["window_size"] == 13, "prefix"]
        .dropna()
        .unique()
        .tolist()
    )
    if not prefixes_13:
        return pd.DataFrame()

    conn = get_source_connection()
    try:
        placeholders = ",".join(["?"] * len(prefixes_13))
        params: list = list(prefixes_13)
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
            WHERE n.window_size = 13
              AND LOWER(TRIM(n.prefix)) IN ({placeholders})
              {grid_filter}
            ORDER BY n.prefix, n.grid_string_id, n.chunk_index
        """
        df = pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()

    return df


def sync_staging_to_db(
    profile: PatternListProfile,
    *,
    since_grid_string_id: int | None = None,
    full: bool = False,
) -> dict:
    patterns = load_patterns(profile)
    profile.predictions_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(profile.predictions_db)
    try:
        ensure_staging_schema(conn)
        if full:
            clear_staging_tables(conn)
            since_grid_string_id = None

        grid_new = extract_grid_prefix_chunks(patterns, since_grid_string_id=since_grid_string_id)
        ngram_new = extract_ngram_ws13(patterns, since_grid_string_id=since_grid_string_id)
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

    ngram_df = extract_ngram_ws13(patterns)
    ngram_df.to_csv(ngram_output, index=False, encoding="utf-8-sig")

    prefixes_13 = set(
        patterns.loc[patterns["window_size"] == 13, "prefix"].str.lower()
    )
    found_13 = (
        set(ngram_df["prefix"].str.strip().str.lower()) if not ngram_df.empty else set()
    )
    missing_13 = prefixes_13 - found_13

    ws11_n = len(patterns[patterns.window_size == 11])
    ws13_n = len(patterns[patterns.window_size == 13])
    print(f"Profile: {profile.name} ({profile.pattern_csv.name})")
    print(f"Patterns: {len(patterns)} (ws11={ws11_n}, ws13={ws13_n})")
    print(f"Source DB: {SOURCE_DB}")
    print(f"[1] grid -> {grid_output}")
    print(f"    rows: {len(grid_df)}, unique prefixes: {grid_df['csv_prefix'].nunique() if not grid_df.empty else 0}")
    print(f"[2] ngram ws13 -> {ngram_output}")
    print(f"    rows: {len(ngram_df)}, unique prefixes: {ngram_df['prefix'].nunique() if not ngram_df.empty else 0}")
    if missing_13:
        print(f"    ws13 prefixes with no ngram rows: {len(missing_13)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract pattern_list3 data from source DB")
    parser.add_argument(
        "--profile",
        default="list3",
        choices=["list3"],
        help="pattern list profile (default: list3)",
    )
    args = parser.parse_args()
    run_extract(get_profile(args.profile))


if __name__ == "__main__":
    main()
