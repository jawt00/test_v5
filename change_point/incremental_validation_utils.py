"""
점진적 윈도우 검증 유틸 (일 단위 분할 및 ID 조회)

- 기존 모듈 수정 없이, 새 앱에서만 import하여 사용.
- preprocessed_grid_strings의 created_at 기준으로 일 단위 분할.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from svg_parser_module import get_change_point_db_connection


def get_incremental_validation_dates(baseline_date="2026-01-25"):
    """
    기준일 이후 데이터가 있는 서로 다른 날짜 목록을 날짜 순으로 반환.

    Args:
        baseline_date: 기준일 (이 날짜 이상만 포함). "YYYY-MM-DD" 형식.

    Returns:
        list[str]: 날짜 문자열 목록 (예: ["2026-01-25", "2026-01-26", ...]).
        해당 날짜에 created_at이 있는 행이 없으면 빈 리스트.
    """
    conn = get_change_point_db_connection()
    try:
        # SQLite: date(created_at) 또는 strftime 사용. created_at이 ISO 형식이면 date() 사용 가능.
        q = """
            SELECT DISTINCT date(created_at) AS d
            FROM preprocessed_grid_strings
            WHERE created_at IS NOT NULL AND date(created_at) >= date(?)
            ORDER BY d
        """
        df = pd.read_sql_query(q, conn, params=[baseline_date])
        if len(df) == 0:
            return []
        return df["d"].astype(str).tolist()
    finally:
        conn.close()


def get_train_cutoff_id_for_date(validation_date):
    """
    해당 검증일 직전까지의 학습 데이터 상한 ID (max id where date(created_at) < validation_date).

    Args:
        validation_date: 검증 대상 일자. "YYYY-MM-DD" 형식.

    Returns:
        int or None: 조건을 만족하는 max(id). 해당하는 행이 없으면 None.
    """
    conn = get_change_point_db_connection()
    try:
        q = """
            SELECT max(id) AS mid
            FROM preprocessed_grid_strings
            WHERE created_at IS NOT NULL AND date(created_at) < date(?)
        """
        df = pd.read_sql_query(q, conn, params=[validation_date])
        if len(df) == 0 or df.iloc[0]["mid"] is None:
            return None
        return int(df.iloc[0]["mid"])
    finally:
        conn.close()


def get_validation_ids_for_date(validation_date):
    """
    해당 날짜에 해당하는 preprocessed_grid_strings의 id 목록 (id 순).

    Args:
        validation_date: 검증 대상 일자. "YYYY-MM-DD" 형식.

    Returns:
        list[int]: id 목록. 해당 일에 행이 없으면 빈 리스트.
    """
    conn = get_change_point_db_connection()
    try:
        q = """
            SELECT id
            FROM preprocessed_grid_strings
            WHERE created_at IS NOT NULL AND date(created_at) = date(?)
            ORDER BY id
        """
        df = pd.read_sql_query(q, conn, params=[validation_date])
        if len(df) == 0:
            return []
        return df["id"].astype(int).tolist()
    finally:
        conn.close()
