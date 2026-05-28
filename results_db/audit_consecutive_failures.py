"""
6연패 전수 조사 스크립트

step_events에서 (run_id, grid_string_id)별로 skipped=0인 스텝만 나열했을 때
is_correct=0이 연속으로 6개 이상 나오는 구간이 한 번이라도 있는지 전수 조사.
- 6연패 발생 (run_id, grid_string_id) 건수 및 목록 출력.
- 옵션: --hypothesis-key 로 runs와 JOIN해 해당 가설만 필터.
"""

import argparse
import sqlite3
from pathlib import Path


def _default_db_path():
    return Path(__file__).resolve().parent / "sim_results_change_point.db"


def compute_max_consecutive_failures(rows):
    """
    (run_id, grid_string_id)별로 이미 step 순 정렬된 rows에서
    skipped=0인 행만 대상으로 is_correct=0 연속 최대 길이 반환.
    rows: list of dict with keys skipped, is_correct.
    """
    max_f = 0
    cur = 0
    for r in rows:
        if r.get("skipped") != 0:
            continue
        if r.get("is_correct") == 0 or r.get("is_correct") is False:
            cur += 1
            max_f = max(max_f, cur)
        else:
            cur = 0
    return max_f


def audit_consecutive_failures(db_path, hypothesis_key=None, failure_threshold=6):
    """
    step_events 전수 조사. (run_id, grid_string_id)별 max 연속 실패 >= failure_threshold 인 건수와 목록 반환.
    hypothesis_key가 있으면 runs JOIN으로 해당 가설만 대상.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if hypothesis_key:
            q = """
                SELECT se.run_id, se.grid_string_id, se.step, se.is_correct, se.skipped
                FROM step_events se
                JOIN runs r ON se.run_id = r.run_id
                WHERE r.hypothesis_key = ?
                ORDER BY se.run_id, se.grid_string_id, se.step
            """
            cursor = conn.execute(q, (hypothesis_key,))
        else:
            q = """
                SELECT run_id, grid_string_id, step, is_correct, skipped
                FROM step_events
                ORDER BY run_id, grid_string_id, step
            """
            cursor = conn.execute(q)
        rows = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

    # (run_id, grid_string_id)별로 그룹
    from itertools import groupby
    key_fn = lambda r: (r["run_id"], r["grid_string_id"])
    violators = []
    for (run_id, grid_string_id), group in groupby(rows, key=key_fn):
        events = list(group)
        mcf = compute_max_consecutive_failures(events)
        if mcf >= failure_threshold:
            violators.append({"run_id": run_id, "grid_string_id": grid_string_id, "max_consecutive_failures": mcf})

    return {
        "count": len(violators),
        "violators": violators,
        "failure_threshold": failure_threshold,
        "hypothesis_key": hypothesis_key,
        "db_path": str(db_path),
    }


def main():
    parser = argparse.ArgumentParser(description="6연패 전수 조사 (step_events 기준)")
    parser.add_argument("--db", type=Path, default=None, help="결과 DB 경로 (기본: results_db/sim_results_change_point.db)")
    parser.add_argument("--hypothesis-key", type=str, default=None, help="runs.hypothesis_key 필터 (예: first_anchor_window9_freq518_win50)")
    parser.add_argument("--threshold", type=int, default=6, help="연속 실패 N회 이상을 위반으로 간주 (기본: 6)")
    args = parser.parse_args()
    db_path = args.db if args.db is not None else _default_db_path()
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return 1
    result = audit_consecutive_failures(db_path, hypothesis_key=args.hypothesis_key, failure_threshold=args.threshold)
    print(f"DB: {result['db_path']}")
    if result["hypothesis_key"]:
        print(f"Hypothesis: {result['hypothesis_key']}")
    print(f"연속 {result['failure_threshold']}회 실패 발생 (run_id, grid_string_id) 건수: {result['count']}")
    if result["count"] > 0:
        print("목록:")
        for v in result["violators"]:
            print(f"  run_id={v['run_id']} grid_string_id={v['grid_string_id']} max_consecutive_failures={v['max_consecutive_failures']}")
    return 0 if result["count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
