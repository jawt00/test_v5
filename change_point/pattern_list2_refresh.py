"""
pattern_list2 예측 테이블 수동 갱신 오케스트레이터.

사용자가 CLI 또는 compare list2 앱 버튼으로 명시 실행할 때만 동작.
자동 폴링·v4 저장 hook 없음.

list2 프로필은 테스트 완료 전까지 db_backup/pattern_list2_TEST.db 만 갱신한다.
라이브(pattern_list2.db)는 livegame_three_modes_v4 전용.

  python3 change_point/pattern_list2_refresh.py --profile list2
  python3 change_point/pattern_list2_refresh.py --profile list2 --full
  python3 change_point/pattern_list2_refresh.py --profile list2 --sim-only
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_pattern_list_predictions import (
    build_frequency_predictions,
    upsert_predictions_table,
)
from extract_pattern_list_data import (
    STATE_KEY_LAST_GRID_ID,
    get_max_source_grid_string_id,
    load_grid_staging,
    load_ngram_staging,
    sync_staging_to_db,
)
from pattern_list_profiles import TABLE_GRID, TABLE_NGRAM, PatternListProfile, get_profile
from pattern_list2_sim_predictions import (
    build_simulation_predictions_df,
    save_simulation_predictions,
)
from pattern_predictions_compare_app import build_comparison_df

KST = timezone(timedelta(hours=9))
PIPELINE_STATE_TABLE = "pipeline_state"


@dataclass
class RefreshResult:
    status: str  # ok | unchanged | error
    message: str = ""
    max_grid_string_id: int = 0
    previous_grid_string_id: int = 0
    grid_rows_synced: int = 0
    ngram_rows_synced: int = 0
    upserted_grid: int = 0
    upserted_ngram: int = 0
    upserted_sim: int = 0
    snapshot_run_id: str = ""
    refreshed_at: str = ""
    mode: str = "incremental"
    extra: dict = field(default_factory=dict)


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def ensure_pipeline_state_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PIPELINE_STATE_TABLE} (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    conn.commit()


def get_pipeline_state(conn: sqlite3.Connection, key: str, default: str = "0") -> str:
    ensure_pipeline_state_schema(conn)
    row = conn.execute(
        f"SELECT value FROM {PIPELINE_STATE_TABLE} WHERE key = ?",
        (key,),
    ).fetchone()
    return row[0] if row else default


def set_pipeline_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    ensure_pipeline_state_schema(conn)
    conn.execute(
        f"""
        INSERT OR REPLACE INTO {PIPELINE_STATE_TABLE} (key, value, updated_at)
        VALUES (?, ?, ?)
        """,
        (key, value, _now_kst()),
    )
    conn.commit()


def rebuild_mid_predictions(profile: PatternListProfile) -> tuple[int, int]:
    """staging 전체 → grid/ngram 예측 테이블 UPSERT."""
    conn = sqlite3.connect(profile.predictions_db)
    try:
        grid_chunks = load_grid_staging(conn)
        ngram_chunks = load_ngram_staging(conn)
        grid_pred = build_frequency_predictions(grid_chunks, prefix_col="csv_prefix")
        ngram_pred = build_frequency_predictions(ngram_chunks, prefix_col="prefix")
        n_grid = upsert_predictions_table(conn, TABLE_GRID, grid_pred)
        n_ngram = upsert_predictions_table(conn, TABLE_NGRAM, ngram_pred)
        return n_grid, n_ngram
    finally:
        conn.close()


def rebuild_sim_predictions(profile: PatternListProfile, final_pred_fn) -> int:
    cmp_df, _ = build_comparison_df(profile.name)
    if cmp_df.empty:
        return 0
    pred_df = build_simulation_predictions_df(cmp_df, profile, final_pred_fn)
    return save_simulation_predictions(profile, pred_df, replace=False)


def run_refresh(
    profile: PatternListProfile,
    *,
    full: bool = False,
    sim_only: bool = False,
    enabled_rules=None,
    final_pred_fn=None,
    classify_final_rule_fn=None,
    rule_version: str | None = None,
) -> RefreshResult:
    """
    수동 갱신 실행.
    - incremental (default): 원본 id 증가분만 staging sync 후 mid+sim UPSERT
    - full: staging 전량 재적재
    - sim_only: mid sync 생략, sim UPSERT만
    - enabled_rules: 적용할 규칙 ID 집합 (R1,R2,R3,R5). 미매칭 prefix → pass
    """
    from pattern_list2_final_rules import build_rule_engine

    if final_pred_fn is None or classify_final_rule_fn is None:
        compute_fn, classify_fn, ver = build_rule_engine(enabled_rules)
        if final_pred_fn is None:
            final_pred_fn = compute_fn
        if classify_final_rule_fn is None:
            classify_final_rule_fn = classify_fn
        if rule_version is None:
            rule_version = ver
    elif rule_version is None:
        from pattern_list2_final_rules import DEFAULT_RULE_VERSION

        rule_version = DEFAULT_RULE_VERSION

    max_id = get_max_source_grid_string_id()
    profile.predictions_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(profile.predictions_db)
    try:
        prev_id = int(get_pipeline_state(conn, STATE_KEY_LAST_GRID_ID, "0"))
    finally:
        conn.close()

    mode = "sim_only" if sim_only else ("full" if full else "incremental")
    result = RefreshResult(
        status="ok",
        max_grid_string_id=max_id,
        previous_grid_string_id=prev_id,
        refreshed_at=_now_kst(),
        mode=mode,
    )

    if not sim_only and not full and max_id <= prev_id:
        result.status = "unchanged"
        result.message = "변경 없음 — 원본 DB에 새 grid_string 없음"
        return result

    try:
        if not sim_only:
            since_id = None if full else prev_id
            sync_stats = sync_staging_to_db(
                profile, since_grid_string_id=since_id, full=full
            )
            result.grid_rows_synced = sync_stats["grid_rows_synced"]
            result.ngram_rows_synced = sync_stats["ngram_rows_synced"]
            n_grid, n_ngram = rebuild_mid_predictions(profile)
            result.upserted_grid = n_grid
            result.upserted_ngram = n_ngram

        result.upserted_sim = rebuild_sim_predictions(profile, final_pred_fn)

        if not sim_only:
            conn = sqlite3.connect(profile.predictions_db)
            try:
                set_pipeline_state(conn, STATE_KEY_LAST_GRID_ID, str(max_id))
            finally:
                conn.close()

        parts = []
        if not sim_only:
            parts.append(
                f"staging grid={result.grid_rows_synced}, ngram={result.ngram_rows_synced}"
            )
            parts.append(
                f"mid grid={result.upserted_grid}, ngram={result.upserted_ngram}"
            )
        parts.append(f"sim={result.upserted_sim}")
        result.message = " · ".join(parts)

        from pattern_list2_snapshot import capture_snapshot_after_refresh

        run_id = capture_snapshot_after_refresh(
            profile,
            result,
            final_pred_fn,
            classify_final_rule_fn,
            rule_version=rule_version,
            set_active=True,
        )
        if run_id:
            result.snapshot_run_id = run_id
            result.message += f" · snapshot={run_id}"

        return result
    except Exception as e:
        result.status = "error"
        result.message = str(e)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="pattern_list2 예측 테이블 수동 갱신")
    parser.add_argument("--profile", default="list2", choices=["list1", "list2"])
    parser.add_argument("--full", action="store_true", help="staging 전량 재적재")
    parser.add_argument("--sim-only", action="store_true", help="sim 테이블만 UPSERT")
    parser.add_argument(
        "--rules",
        default=None,
        help="적용 규칙 (쉼표 구분, 예: R1,R2,R3,R5). 미지정 시 기본 R1+R2+R3+R5",
    )
    args = parser.parse_args()

    profile = get_profile(args.profile)
    print(f"profile: {profile.name}")
    print(f"predictions_db: {profile.predictions_db}")
    if getattr(profile, "is_test_db", False):
        print("NOTE: TEST DB mode — live pattern_list2.db is not modified")

    enabled_rules = None
    if args.rules:
        enabled_rules = [r.strip() for r in args.rules.split(",") if r.strip()]

    result = run_refresh(
        profile,
        full=args.full,
        sim_only=args.sim_only,
        enabled_rules=enabled_rules,
    )

    print(f"status: {result.status}")
    print(f"mode: {result.mode}")
    print(f"message: {result.message}")
    print(f"grid_string_id: {result.previous_grid_string_id} -> {result.max_grid_string_id}")
    if result.status == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
