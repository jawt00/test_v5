"""
스킵된 스텝만 step_events에서 읽어 CSV로 내보냄.

- 성공/실패 = step_events의 예측값(predicted) vs 실제값(actual) 기준 (is_correct).
- **--with-sim-wr** 지정 시: run별 cutoff로 예측 테이블 복원 후 시뮬 승률(sim_win_rate_pct) 붙임.
  → 놓친 기회(빈도 신뢰도·시뮬 승률) 조건 추출용.

실행 (프로젝트 루트):
  python -m results_db.export_skipped_steps_with_sim_wr --hypothesis first_anchor_window9_freq518_win50 --limit 20 --out skipped_steps.csv
  python -m results_db.export_skipped_steps_with_sim_wr --with-sim-wr --out skipped_steps.csv  # 조건 추출용
"""

import csv
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_change_point_dir = _root / "change_point"
for p in (_root, _change_point_dir):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from change_point.results_storage import get_results_db_connection


DEFAULT_HYPOTHESIS = "first_anchor_window9_freq518_win50"
RUN_LIMIT = 20


def get_run_ids_for_hypothesis(conn, hypothesis_key, limit):
    """runs에서 hypothesis_key에 해당하는 run_id를 created_at ASC로 limit개 반환."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT run_id FROM runs
        WHERE hypothesis_key = ? ORDER BY created_at ASC LIMIT ?
        """,
        (hypothesis_key, limit),
    )
    return [row[0] for row in cur.fetchall()]


def get_runs_with_intervals(conn, hypothesis_key, limit):
    """runs에서 hypothesis_key run을 created_at ASC로 limit개, 각 run의 cutoff/threshold 반환."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT run_id, cutoff_grid_string_id, threshold
        FROM runs WHERE hypothesis_key = ? ORDER BY created_at ASC LIMIT ?
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
        FROM grid_results WHERE run_id IN ({placeholders}) GROUP BY run_id
        """,
        run_ids,
    )
    gid_map = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
    out = []
    for run_id, cutoff, threshold in rows:
        if gid_map.get(run_id, (None, None))[0] is None:
            continue
        out.append({
            "run_id": run_id,
            "cutoff_grid_string_id": cutoff,
            "threshold": float(threshold) if threshold is not None else 0.0,
        })
    return out


def fetch_all_skipped_steps(results_conn, run_ids):
    """step_events에서 run_id IN (...) 이고 skipped=1 인 행 전부."""
    if not run_ids:
        return []
    placeholders = ",".join("?" * len(run_ids))
    cur = results_conn.cursor()
    cur.execute(
        f"""
        SELECT run_id, grid_string_id, step, window_size, prefix, confidence,
               predicted, actual, is_correct, skip_reason
        FROM step_events
        WHERE run_id IN ({placeholders}) AND skipped = 1
        ORDER BY run_id, grid_string_id, step
        """,
        run_ids,
    )
    columns = [
        "run_id", "grid_string_id", "step", "window_size", "prefix",
        "confidence", "predicted", "actual", "is_correct", "skip_reason",
    ]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def fetch_skipped_steps_for_run(results_conn, run_id):
    """step_events에서 해당 run의 skipped=1인 행만."""
    cur = results_conn.cursor()
    cur.execute(
        """
        SELECT run_id, grid_string_id, step, window_size, prefix, confidence,
               predicted, actual, is_correct, skip_reason
        FROM step_events WHERE run_id = ? AND skipped = 1
        ORDER BY grid_string_id, step
        """,
        (run_id,),
    )
    columns = [
        "run_id", "grid_string_id", "step", "window_size", "prefix",
        "confidence", "predicted", "actual", "is_correct", "skip_reason",
    ]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def lookup_sim_win_rate(change_point_conn, window_size, prefix, threshold):
    """simulation_predictions_change_point에서 sim_win_rate_pct 반환."""
    cur = change_point_conn.cursor()
    cur.execute(
        """
        SELECT sim_win_rate_pct FROM simulation_predictions_change_point
        WHERE window_size = ? AND prefix = ? AND threshold = ? LIMIT 1
        """,
        (window_size, prefix, threshold),
    )
    row = cur.fetchone()
    return row[0] if row and row[0] is not None else None


def run_export(
    db_path=None,
    hypothesis_key=DEFAULT_HYPOTHESIS,
    limit=RUN_LIMIT,
    out_path=None,
    with_sim_wr=False,
):
    """
    step_events에서 해당 가설 run들의 스킵 스텝만 읽어 반환/CSV 저장.
    - with_sim_wr=False: confidence, predicted, actual, is_correct만 (예측 테이블 복원 없음).
    - with_sim_wr=True: run별 cutoff로 예측 테이블 복원 후 sim_win_rate_pct 붙임 (놓친 기회 조건 추출용).
    """
    conn = get_results_db_connection(db_path)
    if not with_sim_wr:
        run_ids = get_run_ids_for_hypothesis(conn, hypothesis_key, limit)
        rows_out = fetch_all_skipped_steps(conn, run_ids)
        conn.close()
    else:
        from change_point.change_point_hypothesis_module import generate_simulation_predictions_table
        from svg_parser_module import get_change_point_db_connection
        runs = get_runs_with_intervals(conn, hypothesis_key, limit)
        conn.close()
        rows_out = []
        for run in runs:
            run_id = run["run_id"]
            cutoff = run["cutoff_grid_string_id"]
            threshold = run["threshold"]
            generate_simulation_predictions_table(
                cutoff_grid_string_id=cutoff,
                threshold=threshold,
            )
            conn2 = get_results_db_connection(db_path)
            skipped = fetch_skipped_steps_for_run(conn2, run_id)
            conn2.close()
            cp_conn = get_change_point_db_connection()
            try:
                for s in skipped:
                    sim_wr = lookup_sim_win_rate(cp_conn, s["window_size"], s["prefix"], threshold)
                    rows_out.append({
                        **s,
                        "sim_win_rate_pct": sim_wr,
                    })
            finally:
                cp_conn.close()

    fieldnames = [
        "run_id", "grid_string_id", "step", "window_size", "prefix",
        "confidence", "predicted", "actual", "is_correct", "skip_reason",
    ]
    if with_sim_wr:
        fieldnames.append("sim_win_rate_pct")
    if out_path:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows_out)
    return rows_out


def main():
    import argparse
    p = argparse.ArgumentParser(description="Export skipped steps (optionally with sim_win_rate_pct for condition extraction).")
    p.add_argument("--db", default=None, help="Results DB path")
    p.add_argument("--hypothesis", default=DEFAULT_HYPOTHESIS, help="Hypothesis key")
    p.add_argument("--limit", type=int, default=RUN_LIMIT, help="Max runs to process")
    p.add_argument("--out", default=None, help="Output CSV path (optional)")
    p.add_argument("--with-sim-wr", action="store_true", help="Attach sim_win_rate_pct per run (for extracting missed-opportunity conditions)")
    args = p.parse_args()

    rows = run_export(
        db_path=args.db,
        hypothesis_key=args.hypothesis,
        limit=args.limit,
        out_path=args.out,
        with_sim_wr=args.with_sim_wr,
    )
    print(f"Exported {len(rows)} skipped steps." + (" (with sim_win_rate_pct)" if args.with_sim_wr else ""))
    if args.out:
        print(f"Wrote: {args.out}")
    if rows and not args.out:
        for r in rows[:5]:
            print(r)


if __name__ == "__main__":
    main()
