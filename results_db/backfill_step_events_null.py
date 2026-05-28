"""
Step events null 백필 스크립트

first_anchor_window9_freq518_win50 가설의 시뮬레이션 run에 대해,
runs·grid_results로 구간을 파악한 뒤 cutoff별로 예측 테이블을 생성하고
검증을 재실행하여 step_events의 null(predicted, actual, is_correct, all_predictions_json)을 채웁니다.

실행: 프로젝트 루트(test_v5)에서
  python -m results_db.backfill_step_events_null
  또는
  python results_db/backfill_step_events_null.py
"""

import math
import sys
from pathlib import Path

# 프로젝트 루트와 change_point 폴더를 path에 추가
# (change_point 내부 모듈이 from results_storage import ... 할 때 필요)
_root = Path(__file__).resolve().parent.parent
_change_point_dir = _root / "change_point"
for p in (_root, _change_point_dir):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from change_point.results_storage import (
    get_results_db_connection,
    _normalize_step_entry_for_storage,
)
from change_point.change_point_hypothesis_module import generate_simulation_predictions_table
from change_point.change_point_hypothesis_module2 import (
    batch_validate_first_anchor_window9_freq518_win50_cp,
)


HYPOTHESIS_KEY = "first_anchor_window9_freq518_win50"
RUN_LIMIT = 20


def get_runs_with_intervals(conn, hypothesis_key, limit):
    """
    runs에서 hypothesis_key에 해당하는 run을 created_at ASC로 limit개 선택하고,
    각 run에 대해 grid_results에서 MIN/MAX grid_string_id를 구해 반환.

    Returns:
        list of dict: [{"run_id", "cutoff_grid_string_id", "threshold", "start_gid", "last_gid"}, ...]
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT run_id, cutoff_grid_string_id, threshold
        FROM runs
        WHERE hypothesis_key = ?
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (hypothesis_key, limit),
    )
    rows = cur.fetchall()
    if not rows:
        return []

    run_ids = [r[0] for r in rows]
    placeholders = ",".join("?" * len(run_ids))
    cur.execute(
        f"""
        SELECT run_id, MIN(grid_string_id) AS start_gid, MAX(grid_string_id) AS last_gid
        FROM grid_results
        WHERE run_id IN ({placeholders})
        GROUP BY run_id
        """,
        run_ids,
    )
    gid_map = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

    out = []
    for run_id, cutoff_grid_string_id, threshold in rows:
        start_gid, last_gid = gid_map.get(run_id, (None, None))
        if start_gid is None or last_gid is None:
            continue
        out.append({
            "run_id": run_id,
            "cutoff_grid_string_id": cutoff_grid_string_id,
            "threshold": float(threshold) if threshold is not None else 0.0,
            "start_gid": start_gid,
            "last_gid": last_gid,
        })
    return out


def _sanitize_for_db(value):
    """pandas NaN / numpy 타입을 DB에 넣기 좋게 정리. None/NaN -> None, 그 외는 str 등으로."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item") and hasattr(value, "dtype"):  # numpy scalar
        try:
            v = value.item()
            return None if isinstance(v, float) and math.isnan(v) else v
        except Exception:
            return str(value) if value is not None else None
    return value


def update_step_events_from_validation(conn, run_id, results):
    """
    검증 결과 results[]의 각 result["history"]를 정규화한 뒤
    step_events에서 (run_id, grid_string_id, step) 일치 행을 UPDATE.
    - COALESCE 사용: 정규화된 값이 있을 때만 채우고, null로 덮어쓰지 않음 (신뢰도 부족 등 스킵 스텝도 채움).
    """
    cur = conn.cursor()
    for r in results:
        gid = r["grid_string_id"]
        history = r.get("history") or []
        for entry in history:
            # entry의 skipped_prediction 등이 pandas NaN일 수 있음 -> 정규화 전에 None으로 통일
            entry_clean = dict(entry)
            for key in ("predicted", "actual", "skipped_prediction"):
                if key in entry_clean:
                    entry_clean[key] = _sanitize_for_db(entry_clean[key])
            norm = _normalize_step_entry_for_storage(entry_clean)
            step = entry.get("step", 0)
            pred = _sanitize_for_db(norm["predicted"])
            actual = _sanitize_for_db(norm["actual"])
            is_correct = norm["is_correct_int"]
            if is_correct is not None and isinstance(is_correct, float) and math.isnan(is_correct):
                is_correct = None
            all_json = _sanitize_for_db(norm["all_predictions_json"])
            sel_ws = norm["selected_window_size"]
            if sel_ws is not None and isinstance(sel_ws, float) and math.isnan(sel_ws):
                sel_ws = None
            cur.execute(
                """
                UPDATE step_events
                SET predicted = COALESCE(?, predicted),
                    actual = COALESCE(?, actual),
                    is_correct = CASE WHEN ? IS NOT NULL THEN ? ELSE is_correct END,
                    all_predictions_json = COALESCE(?, all_predictions_json),
                    selected_window_size = COALESCE(?, selected_window_size)
                WHERE run_id = ? AND grid_string_id = ? AND step = ?
                """,
                (
                    pred,
                    actual,
                    is_correct,
                    is_correct,
                    all_json,
                    sel_ws,
                    run_id,
                    gid,
                    step,
                ),
            )


def run_backfill(db_path=None, hypothesis_key=HYPOTHESIS_KEY, limit=RUN_LIMIT, dry_run=False):
    """
    백필 실행: run 순서대로 예측 테이블 생성 → 검증 → step_events UPDATE.

    Args:
        db_path: 결과 DB 경로. None이면 results_storage 기본값.
        hypothesis_key: 가설 키.
        limit: 처리할 run 개수.
        dry_run: True면 검증만 하고 step_events는 수정하지 않음.

    Returns:
        dict: {"runs_processed": int, "runs_skipped": int, "error": str or None}
    """
    runs_processed = 0
    conn = get_results_db_connection(db_path)
    try:
        runs = get_runs_with_intervals(conn, hypothesis_key, limit)
        if not runs:
            return {"runs_processed": 0, "runs_skipped": 0, "error": None}

        for run in runs:
            run_id = run["run_id"]
            cutoff = run["cutoff_grid_string_id"]
            threshold = run["threshold"]
            last_gid = run["last_gid"]

            # 1) 해당 run의 cutoff로 예측 테이블 생성
            generate_simulation_predictions_table(
                cutoff_grid_string_id=cutoff,
                threshold=threshold,
            )

            # 2) 해당 구간만 검증
            out = batch_validate_first_anchor_window9_freq518_win50_cp(
                cutoff_grid_string_id=cutoff,
                threshold=threshold,
                max_grid_string_id=last_gid,
            )
            results = out.get("results") or []

            if not dry_run:
                # 3) step_events 보정 (run 단위 commit)
                update_step_events_from_validation(conn, run_id, results)
                conn.commit()
            runs_processed += 1
            print(f"  run {run_id[:8]}... cutoff={cutoff} last_gid={last_gid} -> {len(results)} grid strings")

        return {"runs_processed": runs_processed, "runs_skipped": 0, "error": None}
    except Exception as e:
        if not dry_run:
            conn.rollback()
        return {"runs_processed": runs_processed, "runs_skipped": 0, "error": str(e)}
    finally:
        conn.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Backfill step_events nulls for first_anchor_window9_freq518_win50 runs.")
    parser.add_argument("--db", default=None, help="Results DB path (default: results_db/sim_results_change_point.db)")
    parser.add_argument("--hypothesis", default=HYPOTHESIS_KEY, help="Hypothesis key")
    parser.add_argument("--limit", type=int, default=RUN_LIMIT, help="Max number of runs to process")
    parser.add_argument("--dry-run", action="store_true", help="Run validation only, do not update step_events")
    args = parser.parse_args()

    print(f"Backfill step_events (hypothesis={args.hypothesis}, limit={args.limit}, dry_run={args.dry_run})")
    result = run_backfill(db_path=args.db, hypothesis_key=args.hypothesis, limit=args.limit, dry_run=args.dry_run)
    if result["error"]:
        print(f"Error: {result['error']}")
        sys.exit(1)
    print(f"Done. runs_processed={result['runs_processed']}")


if __name__ == "__main__":
    main()
