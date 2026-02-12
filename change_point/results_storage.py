"""
시뮬레이션/게임 결과 저장 모듈

- 기존 change-point DB와 분리된 SQLite 파일에 결과만 저장
- run / run_summary / grid_results / step_events 한 트랜잭션으로 저장
- 개별 스트링 상세 히스토리(step_events) 필수 포함
"""

import json
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime


# 기본 결과 DB 경로 (시뮬레이션·라이브게임 동일 DB 파일)
def _default_results_db_path():
    root = Path(__file__).resolve().parent.parent
    return root / "results_db" / "sim_results_change_point.db"


def get_results_db_connection(path=None):
    """결과 DB 연결. path가 None이면 기본 경로 사용. 폴더 없으면 생성."""
    if path is None:
        path = _default_results_db_path()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(path))


def init_results_schema(conn):
    """스키마 생성: runs, run_summary, grid_results, step_events 및 인덱스."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            created_at TIMESTAMP NOT NULL,
            engine_version TEXT,
            hypothesis_key TEXT NOT NULL,
            cutoff_grid_string_id INTEGER NOT NULL,
            method TEXT NOT NULL,
            threshold REAL NOT NULL,
            window_sizes TEXT NOT NULL,
            notes TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS run_summary (
            run_id TEXT PRIMARY KEY,
            total_grid_strings INTEGER NOT NULL,
            avg_accuracy REAL NOT NULL,
            max_consecutive_failures INTEGER NOT NULL,
            avg_max_consecutive_failures REAL NOT NULL,
            total_steps INTEGER NOT NULL,
            total_failures INTEGER NOT NULL,
            total_predictions INTEGER NOT NULL,
            total_skipped INTEGER NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS grid_results (
            run_id TEXT NOT NULL,
            grid_string_id INTEGER NOT NULL,
            accuracy REAL NOT NULL,
            max_consecutive_failures INTEGER NOT NULL,
            total_steps INTEGER NOT NULL,
            total_failures INTEGER NOT NULL,
            total_predictions INTEGER NOT NULL,
            total_skipped INTEGER NOT NULL,
            stopped_early INTEGER NOT NULL,
            PRIMARY KEY (run_id, grid_string_id),
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS step_events (
            run_id TEXT NOT NULL,
            grid_string_id INTEGER NOT NULL,
            step INTEGER NOT NULL,
            position INTEGER NOT NULL,
            anchor INTEGER NOT NULL,
            window_size INTEGER NOT NULL,
            prefix TEXT NOT NULL,
            predicted TEXT,
            actual TEXT,
            is_correct INTEGER,
            confidence REAL NOT NULL,
            selected_window_size INTEGER,
            skipped INTEGER NOT NULL,
            skip_reason TEXT,
            all_predictions_json TEXT,
            PRIMARY KEY (run_id, grid_string_id, step),
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        )
    """)
    # 인덱스
    cur.execute("CREATE INDEX IF NOT EXISTS idx_grid_results_run_id ON grid_results(run_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_grid_results_grid_string_id ON grid_results(grid_string_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_step_events_run_gid ON step_events(run_id, grid_string_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_step_events_run_anchor ON step_events(run_id, anchor)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_step_events_run_window ON step_events(run_id, window_size)")
    conn.commit()


def init_live_results_schema(conn):
    """라이브게임 결과 테이블: live_runs, live_run_summary, live_step_events (시뮬레이션과 동일 DB, 별도 테이블)."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS live_runs (
            run_id TEXT PRIMARY KEY,
            created_at TIMESTAMP NOT NULL,
            engine_version TEXT,
            method TEXT NOT NULL,
            threshold REAL NOT NULL,
            window_sizes TEXT NOT NULL,
            grid_string TEXT NOT NULL,
            notes TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS live_run_summary (
            run_id TEXT PRIMARY KEY,
            total_steps INTEGER NOT NULL,
            total_failures INTEGER NOT NULL,
            total_predictions INTEGER NOT NULL,
            total_skipped INTEGER NOT NULL,
            accuracy REAL NOT NULL,
            max_consecutive_failures INTEGER NOT NULL,
            FOREIGN KEY (run_id) REFERENCES live_runs(run_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS live_step_events (
            run_id TEXT NOT NULL,
            step INTEGER NOT NULL,
            position INTEGER NOT NULL,
            anchor INTEGER NOT NULL,
            window_size INTEGER NOT NULL,
            prefix TEXT NOT NULL,
            predicted TEXT,
            actual TEXT,
            is_correct INTEGER,
            confidence REAL NOT NULL,
            skipped INTEGER NOT NULL,
            skip_reason TEXT,
            PRIMARY KEY (run_id, step),
            FOREIGN KEY (run_id) REFERENCES live_runs(run_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_live_step_events_run_id ON live_step_events(run_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_live_step_events_run_anchor ON live_step_events(run_id, anchor)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_live_step_events_run_window ON live_step_events(run_id, window_size)")
    conn.commit()


def insert_run(conn, run_meta):
    """runs 테이블에 1행 삽입. run_meta: run_id, created_at, engine_version, hypothesis_key, cutoff_grid_string_id, method, threshold, window_sizes, notes."""
    conn.execute(
        """
        INSERT INTO runs (run_id, created_at, engine_version, hypothesis_key, cutoff_grid_string_id, method, threshold, window_sizes, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_meta["run_id"],
            run_meta.get("created_at", datetime.utcnow().isoformat()),
            run_meta.get("engine_version"),
            run_meta["hypothesis_key"],
            run_meta["cutoff_grid_string_id"],
            run_meta["method"],
            run_meta["threshold"],
            run_meta["window_sizes"],
            run_meta.get("notes"),
        ),
    )


def insert_run_summary(conn, run_id, summary):
    """run_summary 테이블에 1행 삽입."""
    conn.execute(
        """
        INSERT INTO run_summary (run_id, total_grid_strings, avg_accuracy, max_consecutive_failures, avg_max_consecutive_failures, total_steps, total_failures, total_predictions, total_skipped)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            summary.get("total_grid_strings", 0),
            summary.get("avg_accuracy", 0.0),
            summary.get("max_consecutive_failures", 0),
            summary.get("avg_max_consecutive_failures", 0.0),
            summary.get("total_steps", 0),
            summary.get("total_failures", 0),
            summary.get("total_predictions", 0),
            summary.get("total_skipped", 0),
        ),
    )


def insert_grid_results(conn, run_id, results):
    """grid_results 테이블에 results 목록 삽입. 각 항목에 grid_string_id, accuracy, max_consecutive_failures, total_steps, total_failures, total_predictions, total_skipped, stopped_early 필요."""
    for r in results:
        conn.execute(
            """
            INSERT INTO grid_results (run_id, grid_string_id, accuracy, max_consecutive_failures, total_steps, total_failures, total_predictions, total_skipped, stopped_early)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                r["grid_string_id"],
                r.get("accuracy", 0.0),
                r.get("max_consecutive_failures", 0),
                r.get("total_steps", 0),
                r.get("total_failures", 0),
                r.get("total_predictions", 0),
                r.get("total_skipped", 0),
                1 if r.get("stopped_early") else 0,
            ),
        )


def insert_step_events(conn, run_id, results):
    """각 결과 항목의 history[]를 step_events에 전부 삽입. 상세 히스토리 필수."""
    for r in results:
        gid = r["grid_string_id"]
        history = r.get("history") or []
        for entry in history:
            is_correct = entry.get("is_correct")
            if is_correct is True:
                is_correct_int = 1
            elif is_correct is False:
                is_correct_int = 0
            else:
                is_correct_int = None
            all_pred = entry.get("all_predictions")
            all_predictions_json = json.dumps(all_pred, ensure_ascii=False) if all_pred else None
            conn.execute(
                """
                INSERT INTO step_events (run_id, grid_string_id, step, position, anchor, window_size, prefix, predicted, actual, is_correct, confidence, selected_window_size, skipped, skip_reason, all_predictions_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    gid,
                    entry.get("step", 0),
                    entry.get("position", 0),
                    entry.get("anchor", 0),
                    entry.get("window_size", 0),
                    entry.get("prefix", ""),
                    entry.get("predicted"),
                    entry.get("actual"),
                    is_correct_int,
                    entry.get("confidence", 0.0),
                    entry.get("selected_window_size"),
                    1 if entry.get("skipped") else 0,
                    entry.get("skip_reason"),
                    all_predictions_json,
                ),
            )


def _max_consecutive_failures_from_history(history):
    """히스토리에서 연속 불일치 최대 횟수 계산."""
    max_f = 0
    cur = 0
    for e in history or []:
        if e.get("skipped"):
            continue
        ok = e.get("is_correct")
        if ok is False:
            cur += 1
            max_f = max(max_f, cur)
        else:
            cur = 0
    return max_f


def _summary_from_live_history(history):
    """라이브게임 히스토리에서 total_steps, total_failures, total_predictions, total_skipped, accuracy, max_consecutive_failures 계산."""
    steps = 0
    failures = 0
    predictions = 0
    skipped = 0
    for e in history or []:
        steps += 1
        if e.get("skipped"):
            skipped += 1
            continue
        predictions += 1
        if e.get("is_correct") is False:
            failures += 1
    acc = ((predictions - failures) / predictions * 100) if predictions > 0 else 0.0
    return {
        "total_steps": steps,
        "total_failures": failures,
        "total_predictions": predictions,
        "total_skipped": skipped,
        "accuracy": acc,
        "max_consecutive_failures": _max_consecutive_failures_from_history(history),
    }


def insert_live_run(conn, run_meta):
    """live_runs 테이블에 1행 삽입."""
    conn.execute(
        """
        INSERT INTO live_runs (run_id, created_at, engine_version, method, threshold, window_sizes, grid_string, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_meta["run_id"],
            run_meta.get("created_at", datetime.utcnow().isoformat()),
            run_meta.get("engine_version"),
            run_meta["method"],
            run_meta["threshold"],
            run_meta["window_sizes"],
            run_meta["grid_string"],
            run_meta.get("notes"),
        ),
    )


def insert_live_run_summary(conn, run_id, summary):
    """live_run_summary 테이블에 1행 삽입."""
    conn.execute(
        """
        INSERT INTO live_run_summary (run_id, total_steps, total_failures, total_predictions, total_skipped, accuracy, max_consecutive_failures)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            summary.get("total_steps", 0),
            summary.get("total_failures", 0),
            summary.get("total_predictions", 0),
            summary.get("total_skipped", 0),
            summary.get("accuracy", 0.0),
            summary.get("max_consecutive_failures", 0),
        ),
    )


def insert_live_step_events(conn, run_id, history):
    """라이브게임 히스토리를 live_step_events에 전부 삽입."""
    for entry in history or []:
        is_correct = entry.get("is_correct")
        if is_correct is True:
            is_correct_int = 1
        elif is_correct is False:
            is_correct_int = 0
        else:
            is_correct_int = None
        conn.execute(
            """
            INSERT INTO live_step_events (run_id, step, position, anchor, window_size, prefix, predicted, actual, is_correct, confidence, skipped, skip_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                entry.get("step", 0),
                entry.get("position", 0),
                entry.get("anchor", 0),
                entry.get("window_size", 0),
                entry.get("prefix", ""),
                entry.get("predicted"),
                entry.get("actual"),
                is_correct_int,
                entry.get("confidence", 0.0),
                1 if entry.get("skipped") else 0,
                entry.get("skip_reason"),
            ),
        )


def save_live_run_results(run_meta, history, summary, db_path=None):
    """
    라이브게임 run 1회 분량을 한 트랜잭션으로 저장 (live_runs, live_run_summary, live_step_events).
    - run_meta: method, threshold, window_sizes (list 또는 JSON 문자열), grid_string, notes. run_id, created_at 없으면 자동 생성.
    - history: step 단위 리스트 (step, position, anchor, window_size, prefix, predicted, actual, is_correct, confidence, skipped, skip_reason)
    - summary: total_steps, total_failures, total_predictions, total_skipped, accuracy. max_consecutive_failures 없으면 history에서 계산.

    Returns:
        str: run_id
    """
    run_id = run_meta.get("run_id") or str(uuid.uuid4())
    created_at = run_meta.get("created_at") or datetime.utcnow().isoformat()
    window_sizes = run_meta.get("window_sizes")
    if isinstance(window_sizes, (list, tuple)):
        window_sizes = json.dumps(list(window_sizes))
    meta = {
        "run_id": run_id,
        "created_at": created_at,
        "engine_version": run_meta.get("engine_version"),
        "method": run_meta["method"],
        "threshold": run_meta["threshold"],
        "window_sizes": window_sizes,
        "grid_string": run_meta.get("grid_string", ""),
        "notes": run_meta.get("notes"),
    }
    # 저장 시점에 history 기준으로 요약 재계산 (Cold Start 이후 B/P 입력 반영)
    summary = _summary_from_live_history(history)
    conn = get_results_db_connection(db_path)
    try:
        init_live_results_schema(conn)
        conn.execute("BEGIN")
        insert_live_run(conn, meta)
        insert_live_run_summary(conn, run_id, summary)
        insert_live_step_events(conn, run_id, history)
        conn.commit()
        return run_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_run_results(run_meta, results, summary, db_path=None):
    """
    run 1회 분량을 한 트랜잭션으로 저장.
    - run_meta: hypothesis_key, cutoff_grid_string_id, method, threshold, window_sizes (list 또는 JSON 문자열), notes 등. run_id, created_at 없으면 자동 생성.
    - results: list of dict (grid_string_id, accuracy, max_consecutive_failures, total_steps, total_failures, total_predictions, total_skipped, stopped_early, history)
    - summary: total_grid_strings, avg_accuracy, max_consecutive_failures, avg_max_consecutive_failures, total_steps, total_failures, total_predictions, total_skipped
    - 개별 스트링 상세 히스토리(history)가 없으면 저장하지 않고 ValueError.

    Returns:
        str: run_id
    """
    for r in results:
        if not r.get("history") and (r.get("total_steps", 0) > 0 or r.get("total_predictions", 0) > 0):
            raise ValueError("상세 히스토리(history)가 없으면 run을 저장할 수 없습니다.")
    run_id = run_meta.get("run_id") or str(uuid.uuid4())
    created_at = run_meta.get("created_at") or datetime.utcnow().isoformat()
    window_sizes = run_meta.get("window_sizes")
    if isinstance(window_sizes, (list, tuple)):
        window_sizes = json.dumps(list(window_sizes))
    meta = {
        "run_id": run_id,
        "created_at": created_at,
        "engine_version": run_meta.get("engine_version"),
        "hypothesis_key": run_meta["hypothesis_key"],
        "cutoff_grid_string_id": run_meta["cutoff_grid_string_id"],
        "method": run_meta["method"],
        "threshold": run_meta["threshold"],
        "window_sizes": window_sizes,
        "notes": run_meta.get("notes"),
    }
    conn = get_results_db_connection(db_path)
    try:
        init_results_schema(conn)
        conn.execute("BEGIN")
        insert_run(conn, meta)
        insert_run_summary(conn, run_id, summary)
        insert_grid_results(conn, run_id, results)
        insert_step_events(conn, run_id, results)
        conn.commit()
        return run_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# =============================================================================
# 조회 함수 (Results Viewer 앱용)
# =============================================================================


def query_simulation_by_grid_string_id(grid_string_id, db_path=None):
    """
    grid_string_id로 시뮬레이션 결과 조회.
    해당 grid_string_id를 검증한 run 목록 및 각 run의 grid_result 반환.

    Returns:
        list of dict: [{"run_id", "run_meta", "run_summary", "grid_result"}, ...]
    """
    conn = get_results_db_connection(db_path)
    try:
        import pandas as pd
        df_gr = pd.read_sql_query(
            """
            SELECT gr.run_id, gr.grid_string_id, gr.accuracy, gr.max_consecutive_failures,
                   gr.total_steps, gr.total_failures, gr.total_predictions, gr.total_skipped, gr.stopped_early
            FROM grid_results gr
            WHERE gr.grid_string_id = ?
            ORDER BY gr.run_id
            """,
            conn,
            params=[grid_string_id],
        )
        if len(df_gr) == 0:
            return []

        run_ids = df_gr["run_id"].unique().tolist()
        placeholders = ",".join("?" * len(run_ids))
        df_runs = pd.read_sql_query(
            f"SELECT * FROM runs WHERE run_id IN ({placeholders})",
            conn,
            params=run_ids,
        )
        df_sum = pd.read_sql_query(
            f"SELECT * FROM run_summary WHERE run_id IN ({placeholders})",
            conn,
            params=run_ids,
        )

        runs_dict = df_runs.set_index("run_id").to_dict("index") if len(df_runs) > 0 else {}
        sum_dict = df_sum.set_index("run_id").to_dict("index") if len(df_sum) > 0 else {}

        result = []
        for _, row in df_gr.iterrows():
            run_id = row["run_id"]
            result.append({
                "run_id": run_id,
                "run_meta": runs_dict.get(run_id, {}),
                "run_summary": sum_dict.get(run_id, {}),
                "grid_result": row.to_dict(),
            })
        return result
    finally:
        conn.close()


def query_simulation_step_events(run_id, grid_string_id, db_path=None):
    """
    시뮬레이션 run의 특정 grid_string_id에 대한 step_events 조회.

    Returns:
        list of dict: step 이벤트 목록
    """
    conn = get_results_db_connection(db_path)
    try:
        import pandas as pd
        df = pd.read_sql_query(
            """
            SELECT step, position, anchor, window_size, prefix, predicted, actual,
                   is_correct, confidence, selected_window_size, skipped, skip_reason, all_predictions_json
            FROM step_events
            WHERE run_id = ? AND grid_string_id = ?
            ORDER BY step
            """,
            conn,
            params=[run_id, grid_string_id],
        )
        return df.to_dict("records") if len(df) > 0 else []
    finally:
        conn.close()


def query_simulation_run_detail(run_id, db_path=None):
    """
    시뮬레이션 run 상세: run_meta, run_summary, grid_results( grid_string_id 목록).

    Returns:
        dict: {"run_meta", "run_summary", "grid_results"} 또는 None
    """
    conn = get_results_db_connection(db_path)
    try:
        import pandas as pd
        df_r = pd.read_sql_query("SELECT * FROM runs WHERE run_id = ?", conn, params=[run_id])
        df_s = pd.read_sql_query("SELECT * FROM run_summary WHERE run_id = ?", conn, params=[run_id])
        df_g = pd.read_sql_query(
            "SELECT * FROM grid_results WHERE run_id = ? ORDER BY grid_string_id",
            conn,
            params=[run_id],
        )
        if len(df_r) == 0:
            return None
        return {
            "run_meta": df_r.iloc[0].to_dict(),
            "run_summary": df_s.iloc[0].to_dict() if len(df_s) > 0 else {},
            "grid_results": df_g.to_dict("records") if len(df_g) > 0 else [],
        }
    finally:
        conn.close()


def query_live_run_detail(run_id, db_path=None):
    """
    라이브 run 상세: run_meta, run_summary, step_events.

    Returns:
        dict: {"run_meta", "run_summary", "step_events"} 또는 None
    """
    conn = get_results_db_connection(db_path)
    try:
        init_live_results_schema(conn)
        import pandas as pd
        df_r = pd.read_sql_query("SELECT * FROM live_runs WHERE run_id = ?", conn, params=[run_id])
        df_s = pd.read_sql_query("SELECT * FROM live_run_summary WHERE run_id = ?", conn, params=[run_id])
        df_e = pd.read_sql_query(
            "SELECT * FROM live_step_events WHERE run_id = ? ORDER BY step",
            conn,
            params=[run_id],
        )
        if len(df_r) == 0:
            return None
        return {
            "run_meta": df_r.iloc[0].to_dict(),
            "run_summary": df_s.iloc[0].to_dict() if len(df_s) > 0 else {},
            "step_events": df_e.to_dict("records") if len(df_e) > 0 else [],
        }
    finally:
        conn.close()


def query_live_runs_list(limit=100, db_path=None):
    """
    라이브 run 목록 (최신순).

    Returns:
        list of dict: [{"run_id", "created_at", "grid_string_preview"}, ...]
    """
    conn = get_results_db_connection(db_path)
    try:
        init_live_results_schema(conn)
        import pandas as pd
        df = pd.read_sql_query(
            """
            SELECT run_id, created_at, grid_string
            FROM live_runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            conn,
            params=[limit],
        )
        rows = []
        for _, r in df.iterrows():
            gs = r.get("grid_string") or ""
            preview = (gs[:50] + "..." if len(gs) > 50 else gs) or "(empty)"
            rows.append({
                "run_id": r["run_id"],
                "created_at": r["created_at"],
                "grid_string_preview": preview,
            })
        return rows
    finally:
        conn.close()


def query_simulation_runs_list(limit=100, db_path=None):
    """
    시뮬레이션 run 목록 (최신순).

    Returns:
        list of dict: [{"run_id", "created_at", "hypothesis_key", "cutoff_grid_string_id"}, ...]
    """
    conn = get_results_db_connection(db_path)
    try:
        import pandas as pd
        df = pd.read_sql_query(
            """
            SELECT run_id, created_at, hypothesis_key, cutoff_grid_string_id, method, threshold
            FROM runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            conn,
            params=[limit],
        )
        return df.to_dict("records") if len(df) > 0 else []
    finally:
        conn.close()


def query_simulation_stats(db_path=None):
    """
    시뮬레이션 전체 통계.

    Returns:
        dict: {
            "total_runs": int,
            "total_grid_results": int,
            "max_consecutive_failures_dist": {0: n, 1: n, ...},
            "avg_max_consecutive_failures": float,
            "worst_max_consecutive_failures": int,
            "avg_accuracy_overall": float,
        }
    """
    conn = get_results_db_connection(db_path)
    try:
        import pandas as pd
        df_agg = pd.read_sql_query(
            """
            SELECT
                (SELECT COUNT(*) FROM runs) AS total_runs,
                (SELECT COUNT(*) FROM grid_results) AS total_grid_results,
                (SELECT COALESCE(AVG(max_consecutive_failures), 0) FROM grid_results) AS avg_mcf,
                (SELECT COALESCE(MAX(max_consecutive_failures), 0) FROM grid_results) AS worst_mcf,
                (SELECT COALESCE(AVG(accuracy), 0) FROM grid_results) AS avg_acc
            """,
            conn,
        )
        df_dist = pd.read_sql_query(
            """
            SELECT max_consecutive_failures AS mcf, COUNT(*) AS cnt
            FROM grid_results
            GROUP BY max_consecutive_failures
            ORDER BY max_consecutive_failures
            """,
            conn,
        )
        row = df_agg.iloc[0] if len(df_agg) > 0 else {}
        dist = {}
        for _, r in df_dist.iterrows():
            k = int(r["mcf"]) if r["mcf"] is not None else 0
            dist[k] = int(r["cnt"])
        return {
            "total_runs": int(row.get("total_runs", 0) or 0),
            "total_grid_results": int(row.get("total_grid_results", 0) or 0),
            "max_consecutive_failures_dist": dist,
            "avg_max_consecutive_failures": float(row.get("avg_mcf", 0) or 0),
            "worst_max_consecutive_failures": int(row.get("worst_mcf", 0) or 0),
            "avg_accuracy_overall": float(row.get("avg_acc", 0) or 0),
        }
    finally:
        conn.close()


def query_live_stats(db_path=None):
    """
    라이브 전체 통계.

    Returns:
        dict: {
            "total_runs": int,
            "max_consecutive_failures_dist": {0: n, 1: n, ...},
            "avg_max_consecutive_failures": float,
            "worst_max_consecutive_failures": int,
            "avg_accuracy_overall": float,
        }
    """
    conn = get_results_db_connection(db_path)
    try:
        init_live_results_schema(conn)
        import pandas as pd
        df_agg = pd.read_sql_query(
            """
            SELECT
                (SELECT COUNT(*) FROM live_runs) AS total_runs,
                (SELECT COALESCE(AVG(max_consecutive_failures), 0) FROM live_run_summary) AS avg_mcf,
                (SELECT COALESCE(MAX(max_consecutive_failures), 0) FROM live_run_summary) AS worst_mcf,
                (SELECT COALESCE(AVG(accuracy), 0) FROM live_run_summary) AS avg_acc
            """,
            conn,
        )
        df_dist = pd.read_sql_query(
            """
            SELECT max_consecutive_failures AS mcf, COUNT(*) AS cnt
            FROM live_run_summary
            GROUP BY max_consecutive_failures
            ORDER BY max_consecutive_failures
            """,
            conn,
        )
        row = df_agg.iloc[0] if len(df_agg) > 0 else {}
        dist = {}
        for _, r in df_dist.iterrows():
            k = int(r["mcf"]) if r["mcf"] is not None else 0
            dist[k] = int(r["cnt"])
        return {
            "total_runs": int(row.get("total_runs", 0) or 0),
            "max_consecutive_failures_dist": dist,
            "avg_max_consecutive_failures": float(row.get("avg_mcf", 0) or 0),
            "worst_max_consecutive_failures": int(row.get("worst_mcf", 0) or 0),
            "avg_accuracy_overall": float(row.get("avg_acc", 0) or 0),
        }
    finally:
        conn.close()


def query_simulation_high_failure_results(
    min_max_consecutive_failures=1,
    limit=100,
    hypothesis_key=None,
    db_path=None,
):
    """
    grid_results 기준, max_consecutive_failures >= threshold인 행.
    runs와 JOIN하여 hypothesis_key, cutoff, created_at 포함.

    Returns:
        list of dict: [{
            "run_id", "grid_string_id", "hypothesis_key", "cutoff_grid_string_id",
            "created_at", "max_consecutive_failures", "accuracy",
            "total_predictions", "total_skipped", "total_failures",
            "method", "threshold"
        }, ...]
    """
    conn = get_results_db_connection(db_path)
    try:
        import pandas as pd
        if hypothesis_key:
            df = pd.read_sql_query(
                """
                SELECT gr.run_id, gr.grid_string_id, gr.max_consecutive_failures, gr.accuracy,
                       gr.total_predictions, gr.total_skipped, gr.total_failures,
                       r.hypothesis_key, r.cutoff_grid_string_id, r.created_at, r.method, r.threshold
                FROM grid_results gr
                JOIN runs r ON gr.run_id = r.run_id
                WHERE gr.max_consecutive_failures >= ? AND r.hypothesis_key = ?
                ORDER BY gr.max_consecutive_failures DESC, r.created_at DESC
                LIMIT ?
                """,
                conn,
                params=[min_max_consecutive_failures, hypothesis_key, limit],
            )
        else:
            df = pd.read_sql_query(
                """
                SELECT gr.run_id, gr.grid_string_id, gr.max_consecutive_failures, gr.accuracy,
                       gr.total_predictions, gr.total_skipped, gr.total_failures,
                       r.hypothesis_key, r.cutoff_grid_string_id, r.created_at, r.method, r.threshold
                FROM grid_results gr
                JOIN runs r ON gr.run_id = r.run_id
                WHERE gr.max_consecutive_failures >= ?
                ORDER BY gr.max_consecutive_failures DESC, r.created_at DESC
                LIMIT ?
                """,
                conn,
                params=[min_max_consecutive_failures, limit],
            )
        return df.to_dict("records") if len(df) > 0 else []
    finally:
        conn.close()


def query_live_high_failure_results(
    min_max_consecutive_failures=1,
    limit=100,
    db_path=None,
):
    """
    live_run_summary JOIN live_runs.
    max_consecutive_failures >= threshold인 run 목록.

    Returns:
        list of dict: [{
            "run_id", "created_at", "max_consecutive_failures", "accuracy",
            "total_predictions", "total_skipped", "total_failures",
            "grid_string_preview"
        }, ...]
    """
    conn = get_results_db_connection(db_path)
    try:
        init_live_results_schema(conn)
        import pandas as pd
        df = pd.read_sql_query(
            """
            SELECT s.run_id, r.created_at, s.max_consecutive_failures, s.accuracy,
                   s.total_predictions, s.total_skipped, s.total_failures,
                   r.grid_string
            FROM live_run_summary s
            JOIN live_runs r ON s.run_id = r.run_id
            WHERE s.max_consecutive_failures >= ?
            ORDER BY s.max_consecutive_failures DESC, r.created_at DESC
            LIMIT ?
            """,
            conn,
            params=[min_max_consecutive_failures, limit],
        )
        rows = []
        for _, r in df.iterrows():
            gs = r.get("grid_string") or ""
            preview = (gs[:50] + "..." if len(gs) > 50 else gs) or "(empty)"
            rows.append({
                "run_id": r["run_id"],
                "created_at": r["created_at"],
                "max_consecutive_failures": r["max_consecutive_failures"],
                "accuracy": r["accuracy"],
                "total_predictions": r["total_predictions"],
                "total_skipped": r["total_skipped"],
                "total_failures": r["total_failures"],
                "grid_string_preview": preview,
            })
        return rows
    finally:
        conn.close()


def query_simulation_hypothesis_keys(db_path=None):
    """
    시뮬레이션 runs 테이블의 고유 hypothesis_key 목록.

    Returns:
        list of str
    """
    conn = get_results_db_connection(db_path)
    try:
        import pandas as pd
        df = pd.read_sql_query(
            "SELECT DISTINCT hypothesis_key FROM runs ORDER BY hypothesis_key",
            conn,
        )
        return df["hypothesis_key"].tolist() if len(df) > 0 else []
    finally:
        conn.close()
