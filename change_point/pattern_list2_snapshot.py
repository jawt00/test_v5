"""
pattern_list2 예측 스냅샷 · 라이브 적중률 평가 · 현재 테이블 복원.

list2 프로필은 테스트 완료 전까지 db_backup/pattern_list2_TEST.db 대상.
운영 라이브 DB(pattern_list2.db)와 분리됨.

  python3 change_point/pattern_list2_snapshot.py --list-runs
  python3 change_point/pattern_list2_snapshot.py --eval --from 2026-06-01 --to 2026-06-25
  python3 change_point/pattern_list2_snapshot.py --restore RUN_ID
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pattern_list2_refresh import (
    PIPELINE_STATE_TABLE,
    RefreshResult,
    ensure_pipeline_state_schema,
    get_pipeline_state,
    set_pipeline_state,
)
from pattern_list2_sim_predictions import (
    METHOD,
    THRESHOLD,
    WINDOW_SIZE,
    build_simulation_predictions_df,
    save_simulation_predictions,
)
from pattern_list_profiles import PatternListProfile, get_profile
from pattern_predictions_compare_app import build_comparison_df

KST = timezone(timedelta(hours=9))

TABLE_RUNS = "prediction_build_runs"
TABLE_SNAPSHOTS = "simulation_predictions_change_point_snapshots"
TABLE_SCORES = "prediction_run_live_scores"

RULE_VERSION = "final_pred_v3"
STATE_KEY_ACTIVE_SNAPSHOT = "active_snapshot_run_id"
STATE_KEY_ACTIVE_RULE_VERSION = "active_rule_version"
STATE_KEY_RESTORED_AT = "restored_at"

RUNS_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_RUNS} (
    run_id TEXT PRIMARY KEY,
    profile TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    mode TEXT NOT NULL,
    source_max_grid_id INTEGER NOT NULL,
    prev_grid_id INTEGER NOT NULL,
    rule_version TEXT NOT NULL,
    grid_rows_synced INTEGER NOT NULL DEFAULT 0,
    ngram_rows_synced INTEGER NOT NULL DEFAULT 0,
    upserted_sim INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 0
)
"""

SNAPSHOTS_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_SNAPSHOTS} (
    run_id TEXT NOT NULL,
    window_size INTEGER NOT NULL,
    prefix TEXT NOT NULL,
    predicted_value TEXT,
    confidence REAL,
    b_ratio REAL,
    p_ratio REAL,
    method TEXT NOT NULL,
    threshold REAL NOT NULL,
    pred_frequency REAL,
    sim_win_rate_pct REAL,
    final_rule TEXT,
    agree_new_three INTEGER,
    agree_sim_ngram INTEGER,
    agree_sim_grid10 INTEGER,
    sim_pred TEXT,
    ngram12_pred TEXT,
    grid10_pred TEXT,
    PRIMARY KEY (run_id, window_size, prefix, method, threshold),
    FOREIGN KEY (run_id) REFERENCES {TABLE_RUNS}(run_id)
)
"""

SCORES_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_SCORES} (
    run_id TEXT NOT NULL,
    eval_from TEXT,
    eval_to TEXT,
    live_n INTEGER NOT NULL DEFAULT 0,
    eval_n INTEGER NOT NULL DEFAULT 0,
    hits INTEGER NOT NULL DEFAULT 0,
    accuracy_pct REAL,
    skipped_null_pred INTEGER NOT NULL DEFAULT 0,
    verdict TEXT,
    computed_at TIMESTAMP NOT NULL,
    PRIMARY KEY (run_id, eval_from, eval_to),
    FOREIGN KEY (run_id) REFERENCES {TABLE_RUNS}(run_id)
)
"""


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def _new_run_id() -> str:
    ts = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{uuid.uuid4().hex[:8]}"


def _bool_to_int(value) -> int | None:
    if value is True:
        return 1
    if value is False:
        return 0
    return None


def _norm_pred(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().lower()
    return s if s in ("b", "p") else None


def get_db_connection(profile: PatternListProfile) -> sqlite3.Connection:
    profile.predictions_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(profile.predictions_db, timeout=20.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def ensure_snapshot_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(RUNS_DDL)
    cur.execute(SNAPSHOTS_DDL)
    cur.execute(SCORES_DDL)
    cur.execute(
        f"CREATE INDEX IF NOT EXISTS idx_pred_snap_prefix "
        f"ON {TABLE_SNAPSHOTS}(prefix, run_id)"
    )
    cur.execute(
        f"CREATE INDEX IF NOT EXISTS idx_pred_runs_created "
        f"ON {TABLE_RUNS}(created_at DESC)"
    )
    conn.commit()


def build_snapshot_df(
    cmp_df: pd.DataFrame,
    pred_df: pd.DataFrame,
    classify_final_rule_fn: Callable[[pd.Series], str],
) -> pd.DataFrame:
    """pred_df + cmp 컨텍스트(final_rule, agree flags, source preds)."""
    if pred_df.empty:
        return pred_df

    ctx = cmp_df.copy()
    ctx["prefix"] = ctx["ws9_core"].astype(str).str.strip().str.lower()
    ctx["final_rule"] = ctx.apply(classify_final_rule_fn, axis=1)
    ctx["sim_pred_norm"] = ctx["sim_pred"].map(_norm_pred)
    ctx["ngram12_pred_norm"] = ctx["ngram12_pred"].map(_norm_pred)
    ctx["grid10_pred_norm"] = ctx["grid10_pred"].map(_norm_pred)

    ctx_cols = [
        "prefix",
        "final_rule",
        "agree_new_three",
        "agree_sim_ngram",
        "agree_sim_grid10",
        "sim_pred_norm",
        "ngram12_pred_norm",
        "grid10_pred_norm",
    ]
    merged = pred_df.merge(ctx[ctx_cols], on="prefix", how="left")
    merged = merged.rename(
        columns={
            "sim_pred_norm": "sim_pred",
            "ngram12_pred_norm": "ngram12_pred",
            "grid10_pred_norm": "grid10_pred",
        }
    )
    merged["agree_new_three"] = merged["agree_new_three"].map(_bool_to_int)
    merged["agree_sim_ngram"] = merged["agree_sim_ngram"].map(_bool_to_int)
    merged["agree_sim_grid10"] = merged["agree_sim_grid10"].map(_bool_to_int)
    return merged


def save_build_run_snapshot(
    profile: PatternListProfile,
    refresh_result: RefreshResult,
    cmp_df: pd.DataFrame,
    pred_df: pd.DataFrame,
    snapshot_df: pd.DataFrame,
    *,
    rule_version: str | None = None,
    set_active: bool = False,
) -> str:
    """갱신 성공 후 run 메타 + 풀스냅샷 INSERT. run_id 반환."""
    run_id = _new_run_id()
    rv = rule_version or RULE_VERSION
    conn = get_db_connection(profile)
    try:
        ensure_snapshot_schema(conn)
        conn.execute("BEGIN")
        conn.execute(
            f"""
            INSERT INTO {TABLE_RUNS} (
                run_id, profile, created_at, mode, source_max_grid_id, prev_grid_id,
                rule_version, grid_rows_synced, ngram_rows_synced, upserted_sim, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                profile.name,
                refresh_result.refreshed_at or _now_kst(),
                refresh_result.mode,
                refresh_result.max_grid_string_id,
                refresh_result.previous_grid_string_id,
                rv,
                refresh_result.grid_rows_synced,
                refresh_result.ngram_rows_synced,
                refresh_result.upserted_sim,
                1 if set_active else 0,
            ),
        )
        if set_active:
            conn.execute(f"UPDATE {TABLE_RUNS} SET is_active = 0 WHERE run_id != ?", (run_id,))

        if not snapshot_df.empty:
            insert_sql = f"""
                INSERT INTO {TABLE_SNAPSHOTS} (
                    run_id, window_size, prefix, predicted_value, confidence,
                    b_ratio, p_ratio, method, threshold, pred_frequency, sim_win_rate_pct,
                    final_rule, agree_new_three, agree_sim_ngram, agree_sim_grid10,
                    sim_pred, ngram12_pred, grid10_pred
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            records = [
                (
                    run_id,
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
                    getattr(r, "final_rule", None),
                    getattr(r, "agree_new_three", None),
                    getattr(r, "agree_sim_ngram", None),
                    getattr(r, "agree_sim_grid10", None),
                    getattr(r, "sim_pred", None),
                    getattr(r, "ngram12_pred", None),
                    getattr(r, "grid10_pred", None),
                )
                for r in snapshot_df.itertuples(index=False)
            ]
            conn.executemany(insert_sql, records)

        if set_active:
            ensure_pipeline_state_schema(conn)
            now = _now_kst()
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {PIPELINE_STATE_TABLE} (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (STATE_KEY_ACTIVE_SNAPSHOT, run_id, now),
            )
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {PIPELINE_STATE_TABLE} (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (STATE_KEY_ACTIVE_RULE_VERSION, rv, now),
            )
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {PIPELINE_STATE_TABLE} (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (STATE_KEY_RESTORED_AT, now, now),
            )

        conn.commit()
        return run_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def capture_snapshot_after_refresh(
    profile: PatternListProfile,
    refresh_result: RefreshResult,
    final_pred_fn: Callable[[pd.Series], str],
    classify_final_rule_fn: Callable[[pd.Series], str],
    *,
    rule_version: str | None = None,
    set_active: bool = True,
) -> str | None:
    """refresh ok 후 cmp/pred 재빌드 → 스냅샷 저장."""
    cmp_df, _ = build_comparison_df(profile.name)
    if cmp_df.empty:
        return None
    pred_df = build_simulation_predictions_df(cmp_df, profile, final_pred_fn)
    snapshot_df = build_snapshot_df(cmp_df, pred_df, classify_final_rule_fn)
    return save_build_run_snapshot(
        profile,
        refresh_result,
        cmp_df,
        pred_df,
        snapshot_df,
        rule_version=rule_version,
        set_active=set_active,
    )


def list_runs(profile: PatternListProfile, limit: int = 100) -> pd.DataFrame:
    if not profile.predictions_db.is_file():
        return pd.DataFrame()
    conn = get_db_connection(profile)
    try:
        ensure_snapshot_schema(conn)
        return pd.read_sql_query(
            f"""
            SELECT r.*,
                   (SELECT COUNT(*) FROM {TABLE_SNAPSHOTS} s WHERE s.run_id = r.run_id) AS snapshot_rows
            FROM {TABLE_RUNS} r
            WHERE r.profile = ?
            ORDER BY r.created_at DESC
            LIMIT ?
            """,
            conn,
            params=[profile.name, limit],
        )
    except sqlite3.OperationalError:
        return pd.DataFrame()
    finally:
        conn.close()


def load_snapshot(profile: PatternListProfile, run_id: str) -> pd.DataFrame:
    conn = get_db_connection(profile)
    try:
        return pd.read_sql_query(
            f"SELECT * FROM {TABLE_SNAPSHOTS} WHERE run_id = ? ORDER BY prefix",
            conn,
            params=[run_id],
        )
    finally:
        conn.close()


def load_latest_scores(
    profile: PatternListProfile,
    eval_from: str | None = None,
    eval_to: str | None = None,
) -> pd.DataFrame:
    conn = get_db_connection(profile)
    try:
        ensure_snapshot_schema(conn)
        if eval_from is not None or eval_to is not None:
            ef = eval_from or ""
            et = eval_to or ""
            return pd.read_sql_query(
                f"""
                SELECT s.*, r.created_at, r.mode, r.is_active, r.source_max_grid_id
                FROM {TABLE_SCORES} s
                JOIN {TABLE_RUNS} r ON r.run_id = s.run_id
                WHERE s.eval_from = ? AND s.eval_to = ?
                ORDER BY s.accuracy_pct DESC NULLS LAST, r.created_at DESC
                """,
                conn,
                params=[ef, et],
            )
        return pd.read_sql_query(
            f"""
            SELECT s.*, r.created_at, r.mode, r.is_active, r.source_max_grid_id
            FROM {TABLE_SCORES} s
            JOIN {TABLE_RUNS} r ON r.run_id = s.run_id
            WHERE s.computed_at = (
                SELECT MAX(s2.computed_at) FROM {TABLE_SCORES} s2 WHERE s2.run_id = s.run_id
            )
            ORDER BY s.accuracy_pct DESC NULLS LAST, r.created_at DESC
            """,
            conn,
        )
    except sqlite3.OperationalError:
        return pd.DataFrame()
    finally:
        conn.close()


def evaluate_snapshot_live(
    profile: PatternListProfile,
    run_id: str,
    eval_from: str | None = None,
    eval_to: str | None = None,
) -> dict:
    from live_w9_insights import load_valid_w9_steps, summarize_overall

    conn = get_db_connection(profile)
    try:
        live_df = load_valid_w9_steps(conn, eval_from, eval_to)
    finally:
        conn.close()

    snap_df = load_snapshot(profile, run_id)
    live_n = len(live_df)

    if snap_df.empty:
        summary = summarize_overall(pd.DataFrame())
        return {
            "run_id": run_id,
            "eval_from": eval_from,
            "eval_to": eval_to,
            "live_n": live_n,
            "eval_n": 0,
            "hits": 0,
            "accuracy_pct": 0.0,
            "skipped_null_pred": 0,
            "verdict": summary["verdict"],
            "verdict_label": summary["verdict_label"],
        }

    snap_by_prefix = snap_df.set_index("prefix")
    eval_rows = []
    skipped_null = 0

    for _, row in live_df.iterrows():
        prefix = row["prefix"]
        if prefix not in snap_by_prefix.index:
            continue
        snap_row = snap_by_prefix.loc[prefix]
        if isinstance(snap_row, pd.DataFrame):
            snap_row = snap_row.iloc[0]
        snap_pred = snap_row["predicted_value"]
        if snap_pred is None or (isinstance(snap_pred, float) and pd.isna(snap_pred)):
            skipped_null += 1
            continue
        pred = str(snap_pred).strip().lower()
        actual = str(row["actual"]).strip().lower()
        eval_rows.append({"is_correct": 1 if pred == actual else 0})

    eval_df = pd.DataFrame(eval_rows)
    summary = summarize_overall(eval_df)
    hits = int((eval_df["is_correct"] == 1).sum()) if len(eval_df) else 0

    return {
        "run_id": run_id,
        "eval_from": eval_from,
        "eval_to": eval_to,
        "live_n": live_n,
        "eval_n": len(eval_df),
        "hits": hits,
        "accuracy_pct": summary["accuracy_pct"],
        "skipped_null_pred": skipped_null,
        "verdict": summary["verdict"],
        "verdict_label": summary["verdict_label"],
    }


def upsert_run_live_score(profile: PatternListProfile, score: dict) -> None:
    conn = get_db_connection(profile)
    try:
        ensure_snapshot_schema(conn)
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {TABLE_SCORES} (
                run_id, eval_from, eval_to, live_n, eval_n, hits, accuracy_pct,
                skipped_null_pred, verdict, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                score["run_id"],
                score.get("eval_from") or "",
                score.get("eval_to") or "",
                score["live_n"],
                score["eval_n"],
                score["hits"],
                score["accuracy_pct"],
                score["skipped_null_pred"],
                score["verdict"],
                _now_kst(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def evaluate_all_runs(
    profile: PatternListProfile,
    eval_from: str | None = None,
    eval_to: str | None = None,
) -> pd.DataFrame:
    runs = list_runs(profile)
    if runs.empty:
        return pd.DataFrame()
    results = []
    for run_id in runs["run_id"]:
        score = evaluate_snapshot_live(profile, run_id, eval_from, eval_to)
        upsert_run_live_score(profile, score)
        results.append(score)
    return pd.DataFrame(results)


def diff_snapshots(
    profile: PatternListProfile,
    run_id_a: str,
    run_id_b: str,
) -> pd.DataFrame:
    a = load_snapshot(profile, run_id_a)[
        ["prefix", "predicted_value", "final_rule"]
    ].rename(columns={"predicted_value": "pred_a", "final_rule": "rule_a"})
    b = load_snapshot(profile, run_id_b)[
        ["prefix", "predicted_value", "final_rule"]
    ].rename(columns={"predicted_value": "pred_b", "final_rule": "rule_b"})
    merged = a.merge(b, on="prefix", how="outer")
    merged["pred_a"] = merged["pred_a"].apply(lambda v: v if pd.notna(v) else None)
    merged["pred_b"] = merged["pred_b"].apply(lambda v: v if pd.notna(v) else None)
    changed = merged[
        merged["pred_a"].fillna("__NULL__") != merged["pred_b"].fillna("__NULL__")
    ].sort_values("prefix")
    return changed.reset_index(drop=True)


def restore_snapshot_to_current(profile: PatternListProfile, run_id: str) -> int:
    snap_df = load_snapshot(profile, run_id)
    if snap_df.empty:
        raise ValueError(f"스냅샷 없음: run_id={run_id}")

    pred_df = snap_df[
        [
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
        ]
    ].copy()

    n = save_simulation_predictions(profile, pred_df, replace=False)

    conn = get_db_connection(profile)
    try:
        ensure_snapshot_schema(conn)
        ensure_pipeline_state_schema(conn)
        conn.execute("BEGIN")
        conn.execute(f"UPDATE {TABLE_RUNS} SET is_active = 0")
        conn.execute(
            f"UPDATE {TABLE_RUNS} SET is_active = 1 WHERE run_id = ?",
            (run_id,),
        )
        now = _now_kst()
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {PIPELINE_STATE_TABLE} (key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            (STATE_KEY_ACTIVE_SNAPSHOT, run_id, now),
        )
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {PIPELINE_STATE_TABLE} (key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            (STATE_KEY_RESTORED_AT, now, now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return n


def get_active_run_id(profile: PatternListProfile) -> str | None:
    if not profile.predictions_db.is_file():
        return None
    conn = get_db_connection(profile)
    try:
        ensure_pipeline_state_schema(conn)
        val = get_pipeline_state(conn, STATE_KEY_ACTIVE_SNAPSHOT, "")
        if val:
            return val
        row = conn.execute(
            f"SELECT run_id FROM {TABLE_RUNS} WHERE is_active = 1 LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def get_active_rule_version(profile: PatternListProfile) -> str | None:
    if not profile.predictions_db.is_file():
        return None
    conn = get_db_connection(profile)
    try:
        ensure_snapshot_schema(conn)
        ensure_pipeline_state_schema(conn)
        val = get_pipeline_state(conn, STATE_KEY_ACTIVE_RULE_VERSION, "")
        if val:
            return val
        run_id = get_active_run_id(profile)
        if not run_id:
            return None
        row = conn.execute(
            f"SELECT rule_version FROM {TABLE_RUNS} WHERE run_id = ? LIMIT 1",
            (run_id,),
        ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="pattern_list2 예측 스냅샷 관리")
    parser.add_argument("--profile", default="list2", choices=["list1", "list2"])
    parser.add_argument("--list-runs", action="store_true", help="run 목록")
    parser.add_argument("--eval", action="store_true", help="전 run 라이브 적중률 평가")
    parser.add_argument("--from", dest="eval_from", default=None, help="평가 시작일")
    parser.add_argument("--to", dest="eval_to", default=None, help="평가 종료일")
    parser.add_argument("--restore", metavar="RUN_ID", default=None, help="스냅샷 복원")
    args = parser.parse_args()

    profile = get_profile(args.profile)

    if args.list_runs:
        df = list_runs(profile)
        if df.empty:
            print("run 없음")
            return
        print(df.to_string(index=False))
        return

    if args.eval:
        df = evaluate_all_runs(profile, args.eval_from, args.eval_to)
        if df.empty:
            print("평가할 run 없음")
            return
        print(df.to_string(index=False))
        return

    if args.restore:
        n = restore_snapshot_to_current(profile, args.restore)
        print(f"복원 완료: run_id={args.restore}, rows={n}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
