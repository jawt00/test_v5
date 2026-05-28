"""
점진적으로 시뮬레이션 실행: run 순서대로 cutoff로 예측 테이블 생성 → 슬라이딩 스케일 검증 → 결과를
first_anchor_window9_sliding_scale 가설로 저장 → (선택) 6연패 감사.

- 데이터 소스: first_anchor_window9_freq518_win50 의 20개 run (created_at ASC).
- 각 run마다: generate_simulation_predictions_table(cutoff) → batch_validate_first_anchor_window9_sliding_scale_cp → save_run_results(..., hypothesis_key=first_anchor_window9_sliding_scale).
- 슬라이딩 설정은 --config JSON 파일 또는 기본값.

실행 예:
  python -m results_db.run_progressive_sliding_scale --limit 20
  python -m results_db.run_progressive_sliding_scale --config derived_config.json --limit 20 --audit
"""

import json
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_change_point_dir = _root / "change_point"
for p in (_root, _change_point_dir):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from change_point.results_storage import get_results_db_connection, save_run_results
from change_point.change_point_hypothesis_module import generate_simulation_predictions_table
from change_point.change_point_sliding_scale_module import batch_validate_first_anchor_window9_sliding_scale_cp

# 백필과 동일한 run 구간 조회
def get_runs_with_intervals(conn, hypothesis_key, limit):
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


SOURCE_HYPOTHESIS = "first_anchor_window9_freq518_win50"
TARGET_HYPOTHESIS = "first_anchor_window9_sliding_scale"
RUN_LIMIT = 20


# 세 조건 config 키 (모듈과 동일)
NEW_KEYS = ("base_conf", "base_wr", "add1_conf_lo", "add1_conf_hi", "add1_wr", "add2_conf", "add2_wr")


def load_sliding_config(config_path):
    """JSON에서 슬라이딩 설정 반환 (세 조건: base_conf/base_wr, add1_*, add2_*). 구버전 키면 변환."""
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data = {k: v for k, v in data.items() if not k.startswith("_")}
    if any(k in data for k in NEW_KEYS):
        return {k: data[k] for k in NEW_KEYS if k in data}
    # 구버전: conf_safe_low, wr_safe 등 → 기본 조건만 매핑, 추가1/2는 모듈 기본값 사용
    out = {}
    if "conf_safe_low" in data:
        out["base_conf"] = data["conf_safe_low"]
    if "wr_safe" in data:
        out["base_wr"] = data["wr_safe"]
    return out


def run_progressive(
    db_path=None,
    source_hypothesis=SOURCE_HYPOTHESIS,
    target_hypothesis=TARGET_HYPOTHESIS,
    limit=RUN_LIMIT,
    sliding_config=None,
    dry_run=False,
):
    """
    Run 순서대로: 예측 테이블 생성 → 슬라이딩 스케일 검증 → target 가설로 저장.

    Returns:
        dict: runs_processed, run_ids_saved, error
    """
    sliding_config = sliding_config or {}
    conn = get_results_db_connection(db_path)
    runs = get_runs_with_intervals(conn, source_hypothesis, limit)
    conn.close()
    if not runs:
        return {"runs_processed": 0, "run_ids_saved": [], "error": None}

    run_ids_saved = []
    for run in runs:
        cutoff = run["cutoff_grid_string_id"]
        threshold = run["threshold"]
        last_gid = run["last_gid"]

        generate_simulation_predictions_table(
            cutoff_grid_string_id=cutoff,
            threshold=threshold,
        )
        out = batch_validate_first_anchor_window9_sliding_scale_cp(
            cutoff_grid_string_id=cutoff,
            threshold=threshold,
            max_grid_string_id=last_gid,
            sliding_scale_config=sliding_config,
        )
        results = out.get("results") or []
        summary = out.get("summary") or {}

        if not dry_run and results:
            run_meta = {
                "hypothesis_key": target_hypothesis,
                "cutoff_grid_string_id": cutoff,
                "method": "빈도 기반",
                "threshold": threshold,
                "window_sizes": [9],
                "notes": f"Progressive sliding (source={source_hypothesis})",
            }
            run_id = save_run_results(run_meta, results, summary, db_path=db_path)
            run_ids_saved.append(run_id)
        print(f"  cutoff={cutoff} last_gid={last_gid} -> {len(results)} grid strings")

    return {"runs_processed": len(runs), "run_ids_saved": run_ids_saved, "error": None}


def main():
    import argparse
    p = argparse.ArgumentParser(description="Run progressive sliding-scale validation and save as hypothesis.")
    p.add_argument("--db", default=None, help="Results DB path")
    p.add_argument("--source", default=SOURCE_HYPOTHESIS, help="Source hypothesis for run intervals")
    p.add_argument("--target", default=TARGET_HYPOTHESIS, help="Target hypothesis_key to save")
    p.add_argument("--limit", type=int, default=RUN_LIMIT)
    p.add_argument("--config", default=None, help="JSON file with base_conf, base_wr, add1_*, add2_* (or legacy conf_safe_low, wr_safe)")
    p.add_argument("--dry-run", action="store_true", help="Run validation only, do not save")
    p.add_argument("--audit", action="store_true", help="After run, execute audit_consecutive_failures for target hypothesis")
    args = p.parse_args()

    sliding_config = None
    if args.config and Path(args.config).exists():
        sliding_config = load_sliding_config(args.config)
        print(f"Using config: {sliding_config}")

    print(f"Progressive sliding (source={args.source}, target={args.target}, limit={args.limit}, dry_run={args.dry_run})")
    result = run_progressive(
        db_path=args.db,
        source_hypothesis=args.source,
        target_hypothesis=args.target,
        limit=args.limit,
        sliding_config=sliding_config,
        dry_run=args.dry_run,
    )
    print(f"Done. runs_processed={result['runs_processed']}, saved={len(result['run_ids_saved'])}")

    if args.audit and result["run_ids_saved"]:
        audit_script = _root / "results_db" / "audit_consecutive_failures.py"
        db_path = args.db or str(_root / "results_db" / "sim_results_change_point.db")
        if audit_script.exists():
            import subprocess
            ret = subprocess.run(
                [
                    sys.executable,
                    str(audit_script),
                    "--hypothesis-key", args.target,
                    "--db", db_path,
                ],
                cwd=str(_root),
            )
            if ret.returncode != 0:
                print("Audit: 6연패 발생 건수 > 0. 조건 조정 후 재실행 권장.")


if __name__ == "__main__":
    main()
