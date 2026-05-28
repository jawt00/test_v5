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


def get_default_results_db_path():
    """기본 결과 DB 절대 경로(문자열). 예측 테이블 생성 시 step_events 읽는 경로와 동일하게 쓸 때 사용."""
    return str(_default_results_db_path())


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
    # step_events에 entry_reason 컬럼 없으면 추가 (예측 스텝 진입 사유)
    cur.execute("PRAGMA table_info(step_events)")
    cols = [row[1] for row in cur.fetchall()]
    if "entry_reason" not in cols:
        cur.execute("ALTER TABLE step_events ADD COLUMN entry_reason TEXT")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS run_prefix_win_rate (
            run_id TEXT NOT NULL,
            window_size INTEGER NOT NULL,
            prefix TEXT NOT NULL,
            total INTEGER NOT NULL,
            correct INTEGER NOT NULL,
            win_rate_pct REAL NOT NULL,
            PRIMARY KEY (run_id, window_size, prefix),
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_run_prefix_win_rate_run_id ON run_prefix_win_rate(run_id)")
    conn.commit()


def _live_run_summary_has_mode(conn):
    """live_run_summary 테이블에 mode 컬럼 존재 여부."""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='live_run_summary'")
    if cur.fetchone() is None:
        return False
    cur.execute("PRAGMA table_info(live_run_summary)")
    return any(row[1] == "mode" for row in cur.fetchall())


def _live_step_events_has_mode(conn):
    """live_step_events 테이블에 mode 컬럼 존재 여부."""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='live_step_events'")
    if cur.fetchone() is None:
        return False
    cur.execute("PRAGMA table_info(live_step_events)")
    return any(row[1] == "mode" for row in cur.fetchall())


def init_live_results_schema(conn):
    """라이브게임 결과 테이블: live_runs, live_run_summary(mode 포함), live_step_events(mode 포함). 기존 테이블은 mode 컬럼 없으면 마이그레이션."""
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
            notes TEXT,
            modes TEXT
        )
    """)
    # live_runs에 modes 컬럼 없으면 추가 (기존 DB)
    cur.execute("PRAGMA table_info(live_runs)")
    if not any(row[1] == "modes" for row in cur.fetchall()):
        try:
            cur.execute("ALTER TABLE live_runs ADD COLUMN modes TEXT")
        except sqlite3.OperationalError:
            pass

    if not _live_run_summary_has_mode(conn):
        # 기존 live_run_summary가 있으면 mode 컬럼 추가 마이그레이션
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='live_run_summary'")
        if cur.fetchone() is not None:
            cur.execute("""
                CREATE TABLE live_run_summary_new (
                    run_id TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'v3',
                    total_steps INTEGER NOT NULL,
                    total_failures INTEGER NOT NULL,
                    total_predictions INTEGER NOT NULL,
                    total_skipped INTEGER NOT NULL,
                    accuracy REAL NOT NULL,
                    max_consecutive_failures INTEGER NOT NULL,
                    PRIMARY KEY (run_id, mode),
                    FOREIGN KEY (run_id) REFERENCES live_runs(run_id)
                )
            """)
            cur.execute("""
                INSERT INTO live_run_summary_new (run_id, mode, total_steps, total_failures, total_predictions, total_skipped, accuracy, max_consecutive_failures)
                SELECT run_id, 'v3', total_steps, total_failures, total_predictions, total_skipped, accuracy, max_consecutive_failures FROM live_run_summary
            """)
            cur.execute("DROP TABLE live_run_summary")
            cur.execute("ALTER TABLE live_run_summary_new RENAME TO live_run_summary")
        else:
            cur.execute("""
                CREATE TABLE live_run_summary (
                    run_id TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'v3',
                    total_steps INTEGER NOT NULL,
                    total_failures INTEGER NOT NULL,
                    total_predictions INTEGER NOT NULL,
                    total_skipped INTEGER NOT NULL,
                    accuracy REAL NOT NULL,
                    max_consecutive_failures INTEGER NOT NULL,
                    PRIMARY KEY (run_id, mode),
                    FOREIGN KEY (run_id) REFERENCES live_runs(run_id)
                )
            """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS live_run_summary (
                run_id TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'v3',
                total_steps INTEGER NOT NULL,
                total_failures INTEGER NOT NULL,
                total_predictions INTEGER NOT NULL,
                total_skipped INTEGER NOT NULL,
                accuracy REAL NOT NULL,
                max_consecutive_failures INTEGER NOT NULL,
                PRIMARY KEY (run_id, mode),
                FOREIGN KEY (run_id) REFERENCES live_runs(run_id)
            )
        """)

    if not _live_step_events_has_mode(conn):
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='live_step_events'")
        if cur.fetchone() is not None:
            cur.execute("""
                CREATE TABLE live_step_events_new (
                    run_id TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'v3',
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
                    PRIMARY KEY (run_id, mode, step),
                    FOREIGN KEY (run_id) REFERENCES live_runs(run_id)
                )
            """)
            cur.execute("""
                INSERT INTO live_step_events_new (run_id, mode, step, position, anchor, window_size, prefix, predicted, actual, is_correct, confidence, skipped, skip_reason)
                SELECT run_id, 'v3', step, position, anchor, window_size, prefix, predicted, actual, is_correct, confidence, skipped, skip_reason FROM live_step_events
            """)
            cur.execute("DROP TABLE live_step_events")
            cur.execute("ALTER TABLE live_step_events_new RENAME TO live_step_events")
        else:
            cur.execute("""
                CREATE TABLE live_step_events (
                    run_id TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'v3',
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
                    PRIMARY KEY (run_id, mode, step),
                    FOREIGN KEY (run_id) REFERENCES live_runs(run_id)
                )
            """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS live_step_events (
                run_id TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'v3',
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
                PRIMARY KEY (run_id, mode, step),
                FOREIGN KEY (run_id) REFERENCES live_runs(run_id)
            )
        """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_live_step_events_run_id ON live_step_events(run_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_live_step_events_run_mode ON live_step_events(run_id, mode)")
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


def _normalize_step_entry_for_storage(entry):
    """
    스킵된 스텝도 복기 시 null 없이 보이도록 값을 채움.
    - predicted: predicted 없으면 skipped_prediction 사용
    - is_correct: 스킵이어도 predicted/actual 있으면 비교해 1/0 설정
    - selected_window_size: 없으면 window_size 사용
    - all_predictions: 비어 있으면 predicted/skipped_prediction으로 최소 1건 구성
    - NaN/NA는 None으로 취급 (신뢰도 부족 등 스킵 스텝 복기 시 안정적 저장).
    """
    def _safe_val(v):
        if v is None:
            return None
        if isinstance(v, float) and v != v:
            return None  # NaN
        return v

    predicted = _safe_val(entry.get("predicted"))
    if predicted is None:
        predicted = _safe_val(entry.get("skipped_prediction"))

    actual = _safe_val(entry.get("actual"))

    is_correct = entry.get("is_correct")
    if is_correct is True:
        is_correct_int = 1
    elif is_correct is False:
        is_correct_int = 0
    else:
        # 스킵된 스텝: predicted·actual이 있으면 비교해 채움 (복기 편의)
        if predicted is not None and actual is not None:
            is_correct_int = 1 if predicted == actual else 0
        else:
            is_correct_int = None

    selected_window_size = entry.get("selected_window_size")
    if selected_window_size is None:
        selected_window_size = entry.get("window_size")

    all_pred = entry.get("all_predictions")
    if not all_pred and (entry.get("predicted") is not None or entry.get("skipped_prediction") is not None):
        # 스킵된 스텝도 예측값·신뢰도가 있으면 all_predictions 1건 구성
        pred_val = entry.get("predicted") or entry.get("skipped_prediction")
        all_pred = [
            {
                "window_size": entry.get("window_size"),
                "prefix": entry.get("prefix", ""),
                "predicted": pred_val,
                "confidence": entry.get("confidence", 0.0),
            }
        ]
    all_predictions_json = json.dumps(all_pred, ensure_ascii=False) if all_pred else None

    return {
        "predicted": predicted,
        "actual": actual,
        "is_correct_int": is_correct_int,
        "confidence": entry.get("confidence", 0.0),
        "selected_window_size": selected_window_size,
        "skip_reason": entry.get("skip_reason"),
        "entry_reason": entry.get("entry_reason"),
        "all_predictions_json": all_predictions_json,
    }


def insert_step_events(conn, run_id, results):
    """각 결과 항목의 history[]를 step_events에 전부 삽입. 스킵된 스텝도 predicted/actual/is_correct 등 가능한 값 채움."""
    for r in results:
        gid = r["grid_string_id"]
        history = r.get("history") or []
        for entry in history:
            norm = _normalize_step_entry_for_storage(entry)
            conn.execute(
                """
                INSERT INTO step_events (run_id, grid_string_id, step, position, anchor, window_size, prefix, predicted, actual, is_correct, confidence, selected_window_size, skipped, skip_reason, entry_reason, all_predictions_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    gid,
                    entry.get("step", 0),
                    entry.get("position", 0),
                    entry.get("anchor", 0),
                    entry.get("window_size", 0),
                    entry.get("prefix", ""),
                    norm["predicted"],
                    norm["actual"],
                    norm["is_correct_int"],
                    norm["confidence"],
                    norm["selected_window_size"],
                    1 if entry.get("skipped") else 0,
                    norm["skip_reason"],
                    norm.get("entry_reason"),
                    norm["all_predictions_json"],
                ),
            )


def _compute_run_prefix_win_rate_aggregates(results):
    """
    results의 history에서 (window_size, prefix)별 total·correct 집계.
    skipped=True인 스텝 제외. 메소드 구분 없이 하나의 승률용 집계.

    Returns:
        list of dict: [{"window_size", "prefix", "total", "correct", "win_rate_pct"}, ...]
    """
    aggregated = {}
    for r in results:
        for entry in r.get("history") or []:
            if entry.get("skipped"):
                continue
            ws = entry.get("window_size")
            prefix = entry.get("prefix", "")
            if ws is None:
                continue
            key = (ws, prefix)
            if key not in aggregated:
                aggregated[key] = {"total": 0, "correct": 0}
            aggregated[key]["total"] += 1
            if entry.get("is_correct") is True or entry.get("is_correct") == 1:
                aggregated[key]["correct"] += 1
    out = []
    for (ws, prefix), v in aggregated.items():
        total = v["total"]
        correct = v["correct"]
        win_rate_pct = 100.0 * correct / total if total > 0 else 0.0
        out.append({
            "window_size": ws,
            "prefix": prefix,
            "total": total,
            "correct": correct,
            "win_rate_pct": win_rate_pct,
        })
    return out


def insert_run_prefix_win_rate(conn, run_id, results):
    """run 저장 시 시뮬레이션 승률을 (window_size, prefix)별로 계산해 run_prefix_win_rate에 삽입. 메소드 구분 없음."""
    rows = _compute_run_prefix_win_rate_aggregates(results)
    for row in rows:
        conn.execute(
            """
            INSERT INTO run_prefix_win_rate (run_id, window_size, prefix, total, correct, win_rate_pct)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                row["window_size"],
                row["prefix"],
                row["total"],
                row["correct"],
                row["win_rate_pct"],
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
    """live_runs 테이블에 1행 삽입. run_meta에 modes (list) 있으면 JSON 문자열로 저장."""
    modes = run_meta.get("modes")
    if isinstance(modes, (list, tuple)):
        modes = json.dumps(list(modes))
    conn.execute(
        """
        INSERT INTO live_runs (run_id, created_at, engine_version, method, threshold, window_sizes, grid_string, notes, modes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            modes,
        ),
    )


def insert_live_run_summary(conn, run_id, summary, mode="v3"):
    """live_run_summary 테이블에 1행 삽입 (run_id, mode별)."""
    conn.execute(
        """
        INSERT INTO live_run_summary (run_id, mode, total_steps, total_failures, total_predictions, total_skipped, accuracy, max_consecutive_failures)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            mode,
            summary.get("total_steps", 0),
            summary.get("total_failures", 0),
            summary.get("total_predictions", 0),
            summary.get("total_skipped", 0),
            summary.get("accuracy", 0.0),
            summary.get("max_consecutive_failures", 0),
        ),
    )


def insert_live_step_events(conn, run_id, history, mode="v3"):
    """라이브게임 히스토리를 live_step_events에 전부 삽입 (run_id, mode별)."""
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
            INSERT INTO live_step_events (run_id, mode, step, position, anchor, window_size, prefix, predicted, actual, is_correct, confidence, skipped, skip_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                mode,
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


def save_live_run_results(run_meta, history=None, summary=None, db_path=None, history_by_mode=None, summary_by_mode=None):
    """
    라이브게임 run 1회 분량을 한 트랜잭션으로 저장 (live_runs, live_run_summary, live_step_events).
    - run_meta: method, threshold, window_sizes (list 또는 JSON 문자열), grid_string, notes, modes(선택). run_id, created_at 없으면 자동 생성.
    - 다중 모드: history_by_mode, summary_by_mode 제공 시 모드별로 저장 (run_meta.modes 사용 또는 history_by_mode 키 목록).
    - 단일 모드(기존 호환): history, summary 제공 시 mode='v3'로 1건 저장.

    Returns:
        str: run_id
    """
    run_id = run_meta.get("run_id") or str(uuid.uuid4())
    created_at = run_meta.get("created_at") or datetime.utcnow().isoformat()
    window_sizes = run_meta.get("window_sizes")
    if isinstance(window_sizes, (list, tuple)):
        window_sizes = json.dumps(list(window_sizes))
    modes_list = run_meta.get("modes")
    if isinstance(modes_list, (list, tuple)):
        modes_list = list(modes_list)
    elif history_by_mode:
        modes_list = list(history_by_mode.keys())
    else:
        modes_list = None

    meta = {
        "run_id": run_id,
        "created_at": created_at,
        "engine_version": run_meta.get("engine_version"),
        "method": run_meta["method"],
        "threshold": run_meta["threshold"],
        "window_sizes": window_sizes,
        "grid_string": run_meta.get("grid_string", ""),
        "notes": run_meta.get("notes"),
        "modes": modes_list,
    }
    conn = get_results_db_connection(db_path)
    try:
        init_live_results_schema(conn)
        conn.execute("BEGIN")
        insert_live_run(conn, meta)
        if history_by_mode is not None and summary_by_mode is not None:
            for mode in (modes_list or list(history_by_mode.keys())):
                h = history_by_mode.get(mode) or []
                s = summary_by_mode.get(mode) or _summary_from_live_history(h)
                insert_live_run_summary(conn, run_id, s, mode=mode)
                insert_live_step_events(conn, run_id, h, mode=mode)
        else:
            if history is None:
                history = []
            summary = summary or _summary_from_live_history(history)
            insert_live_run_summary(conn, run_id, summary, mode="v3")
            insert_live_step_events(conn, run_id, history, mode="v3")
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
    - 시뮬레이션 승률: (window_size, prefix)별로 history에서 계산해 run_prefix_win_rate 테이블에 저장(메소드 구분 없음).

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
        insert_run_prefix_win_rate(conn, run_id, results)
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
        list of dict: step 이벤트 목록 (entry_reason 포함, 구버전 DB는 None)
    """
    import sqlite3
    import pandas as pd
    conn = get_results_db_connection(db_path)
    try:
        try:
            df = pd.read_sql_query(
                """
                SELECT step, position, anchor, window_size, prefix, predicted, actual,
                       is_correct, confidence, selected_window_size, skipped, skip_reason, entry_reason, all_predictions_json
                FROM step_events
                WHERE run_id = ? AND grid_string_id = ?
                ORDER BY step
                """,
                conn,
                params=[run_id, grid_string_id],
            )
        except sqlite3.OperationalError:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(step_events)")
            if not any(row[1] == "entry_reason" for row in cur.fetchall()):
                cur.execute("ALTER TABLE step_events ADD COLUMN entry_reason TEXT")
                conn.commit()
            df = pd.read_sql_query(
                """
                SELECT step, position, anchor, window_size, prefix, predicted, actual,
                       is_correct, confidence, selected_window_size, skipped, skip_reason, entry_reason, all_predictions_json
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


def query_aggregated_prefix_win_rate(window_sizes, db_path=None):
    """
    run_prefix_win_rate 테이블에서 (window_size, prefix)별 시뮬레이션 승률 집계.
    여러 run을 합쳐 sum(correct)/sum(total)*100 반환.

    Returns:
        dict: (window_size, prefix) -> win_rate_pct (float). 키는 (int, str) 튜플.
    """
    conn = get_results_db_connection(db_path)
    try:
        init_results_schema(conn)
        placeholders = ",".join("?" * len(window_sizes))
        q = """
            SELECT window_size, prefix,
                   SUM(correct) AS total_correct,
                   SUM(total) AS total_count
            FROM run_prefix_win_rate
            WHERE window_size IN ({})
            GROUP BY window_size, prefix
        """.format(placeholders)
        df = __import__("pandas").read_sql_query(q, conn, params=list(window_sizes))
        out = {}
        for _, r in df.iterrows():
            tc = r.get("total_correct") or 0
            tn = r.get("total_count") or 0
            pct = 100.0 * tc / tn if tn > 0 else 0.0
            out[(int(r["window_size"]), str(r["prefix"]))] = pct
        return out
    finally:
        conn.close()


def compute_step_events_prefix_win_rate(window_sizes, run_id=None, db_path=None):
    """
    step_events 원본 행을 읽어, 스킵하지 않은 스텝만 취합한 뒤
    (window_size, prefix)별로 일치(is_correct) 여부를 기준으로 승률을 **계산**하여 반환.

    - 조회만 하는 것이 아니라, 스킵 제외 → 그룹별 correct/total → 승률(%) 계산 로직을 수행.
    - 예측값 테이블 생성 시 sim_win_rate_pct 저장용으로 사용.

    Args:
        window_sizes: tuple/list of int (e.g. (9, 10, 11))
        run_id: optional; 지정하면 해당 run만, None이면 전체 run 누적
        db_path: optional; results DB 경로

    Returns:
        dict: (window_size, prefix) -> win_rate_pct (float, 0~100). 한 건도 없으면 빈 dict.
    """
    conn = get_results_db_connection(db_path)
    try:
        init_results_schema(conn)
        placeholders = ",".join("?" * len(window_sizes))
        params = list(window_sizes)
        where_clause = "WHERE window_size IN ({})".format(placeholders)
        if run_id is not None:
            where_clause += " AND run_id = ?"
            params.append(run_id)
        q = """
            SELECT window_size, prefix, skipped, is_correct
            FROM step_events
            {}
        """.format(where_clause)
        cur = conn.execute(q, params)
        rows = cur.fetchall()
    finally:
        conn.close()

    # 스킵하지 않은 스텝만 취합, (window_size, prefix)별 total / correct 계산
    # prefix는 공백 제거 후 통일해 예측 테이블 저장 시 키와 동일하게 맞춤
    def _norm_prefix(p):
        return (str(p).strip() if p is not None else "")

    agg = {}
    for row in rows:
        ws, prefix, skipped, is_correct = row[0], row[1], row[2], row[3]
        if skipped != 0:
            continue
        key = (int(ws), _norm_prefix(prefix))
        if key not in agg:
            agg[key] = {"total": 0, "correct": 0}
        agg[key]["total"] += 1
        if is_correct == 1:
            agg[key]["correct"] += 1

    # 승률 계산: 일치 건수 / 전체 건수 * 100
    win_rate_map = {}
    for key, v in agg.items():
        total = v["total"]
        correct = v["correct"]
        if total > 0:
            win_rate_map[key] = 100.0 * correct / total
    return win_rate_map


def get_step_events_nonskipped_count(window_sizes, db_path=None):
    """
    step_events에서 지정 window_sizes·스킵 제외 건수. 진단용.
    Returns:
        int: skipped=0 이고 window_size IN (...) 인 행 수.
    """
    conn = get_results_db_connection(db_path)
    try:
        init_results_schema(conn)
        placeholders = ",".join("?" * len(window_sizes))
        params = list(window_sizes)
        q = (
            "SELECT COUNT(*) FROM step_events WHERE skipped = 0 AND window_size IN ({})".format(
                placeholders
            )
        )
        cur = conn.execute(q, params)
        row = cur.fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def query_step_events_prefix_win_rate(window_sizes, run_id=None, db_path=None):
    """
    step_events에서 (window_size, prefix)별 시뮬레이션 승률 집계.
    skipped=0인 행만 사용, correct/total 비율을 퍼센트로 반환.

    Args:
        window_sizes: tuple/list of int (e.g. (9, 10, 11))
        run_id: optional; 지정하면 해당 run만, None이면 전체 run 누적
        db_path: optional; results DB 경로

    Returns:
        pandas.DataFrame with columns: window_size, prefix, total, correct, win_rate_pct
    """
    import pandas as pd

    conn = get_results_db_connection(db_path)
    try:
        init_results_schema(conn)
        placeholders = ",".join("?" * len(window_sizes))
        params = list(window_sizes)
        where_clause = "WHERE skipped = 0 AND window_size IN ({})".format(placeholders)
        if run_id is not None:
            where_clause += " AND run_id = ?"
            params.append(run_id)
        q = """
            SELECT window_size, prefix,
                   COUNT(*) AS total,
                   SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS correct
            FROM step_events
            {}
            GROUP BY window_size, prefix
        """.format(where_clause)
        df = pd.read_sql_query(q, conn, params=params)
        if len(df) == 0:
            return df
        import numpy as np
        df["win_rate_pct"] = np.where(
            df["total"] > 0,
            100.0 * df["correct"].astype(float) / df["total"],
            0.0,
        )
        return df
    finally:
        conn.close()


def query_step_events_prefix_win_rate_by_hypothesis(hypothesis_key, window_sizes, db_path=None):
    """
    step_events에서 특정 가설(hypothesis_key)의 run만 대상으로 (window_size, prefix)별 시뮬레이션 승률 집계.
    skipped=0인 행만 사용, correct/total 비율을 퍼센트로 반환.

    Args:
        hypothesis_key: runs.hypothesis_key (e.g. 'first_anchor_window9_freq518_win50')
        window_sizes: tuple/list of int (e.g. (9,))
        db_path: optional; results DB 경로

    Returns:
        pandas.DataFrame with columns: window_size, prefix, total, correct, win_rate_pct
    """
    import pandas as pd
    import numpy as np

    conn = get_results_db_connection(db_path)
    try:
        init_results_schema(conn)
        placeholders = ",".join("?" * len(window_sizes))
        params = [hypothesis_key] + list(window_sizes)
        q = """
            SELECT se.window_size, se.prefix,
                   COUNT(*) AS total,
                   SUM(CASE WHEN se.is_correct = 1 THEN 1 ELSE 0 END) AS correct
            FROM step_events se
            JOIN runs r ON se.run_id = r.run_id
            WHERE r.hypothesis_key = ? AND se.skipped = 0 AND se.window_size IN ({})
            GROUP BY se.window_size, se.prefix
        """.format(placeholders)
        df = pd.read_sql_query(q, conn, params=params)
        if len(df) == 0:
            return df
        df["win_rate_pct"] = np.where(
            df["total"] > 0,
            100.0 * df["correct"].astype(float) / df["total"],
            0.0,
        )
        return df
    finally:
        conn.close()


def query_step_events_skip_summary_by_hypothesis(hypothesis_key, db_path=None):
    """
    step_events에서 특정 가설의 run만 대상으로 스킵 요약: 전체 스텝/스킵/예측 수, skip_reason별 건수.

    Args:
        hypothesis_key: runs.hypothesis_key (e.g. 'first_anchor_window9_freq518_win50')
        db_path: optional; results DB 경로

    Returns:
        dict: total_steps, total_skipped, total_predictions, skip_reason_counts (list of {skip_reason, count})
    """
    import pandas as pd

    conn = get_results_db_connection(db_path)
    try:
        init_results_schema(conn)
        # Totals in one query
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_steps,
                SUM(CASE WHEN se.skipped = 1 THEN 1 ELSE 0 END) AS total_skipped,
                SUM(CASE WHEN se.skipped = 0 THEN 1 ELSE 0 END) AS total_predictions
            FROM step_events se
            JOIN runs r ON se.run_id = r.run_id
            WHERE r.hypothesis_key = ?
            """,
            (hypothesis_key,),
        ).fetchone()
        total_steps = row[0] or 0
        total_skipped = row[1] or 0
        total_predictions = row[2] or 0

        # Skip reason breakdown: coalesce null/empty to '미분류'
        df = pd.read_sql_query(
            """
            SELECT COALESCE(NULLIF(TRIM(se.skip_reason), ''), '미분류') AS skip_reason, COUNT(*) AS count
            FROM step_events se
            JOIN runs r ON se.run_id = r.run_id
            WHERE r.hypothesis_key = ? AND se.skipped = 1
            GROUP BY COALESCE(NULLIF(TRIM(se.skip_reason), ''), '미분류')
            ORDER BY count DESC
            """,
            conn,
            params=[hypothesis_key],
        )
        skip_reason_counts = df.to_dict("records") if len(df) > 0 else []

        return {
            "total_steps": total_steps,
            "total_skipped": total_skipped,
            "total_predictions": total_predictions,
            "skip_reason_counts": skip_reason_counts,
        }
    finally:
        conn.close()


def query_skipped_steps_evaluable_by_hypothesis(hypothesis_key, db_path=None):
    """
    step_events에서 특정 가설의 run만 대상으로, 스킵되었으나 예측 평가 가능한 행만 조회.
    (skipped=1 이고 predicted/actual 모두 NOT NULL)

    Args:
        hypothesis_key: runs.hypothesis_key (e.g. 'first_anchor_window9_freq518_win50')
        db_path: optional; results DB 경로

    Returns:
        pandas.DataFrame: run_id, grid_string_id, step, prefix, window_size, predicted, actual, is_correct, confidence, skip_reason
    """
    import pandas as pd

    conn = get_results_db_connection(db_path)
    try:
        init_results_schema(conn)
        df = pd.read_sql_query(
            """
            SELECT se.run_id, se.grid_string_id, se.step, se.prefix, se.window_size,
                   se.predicted, se.actual, se.is_correct, se.confidence,
                   COALESCE(NULLIF(TRIM(se.skip_reason), ''), '미분류') AS skip_reason
            FROM step_events se
            JOIN runs r ON se.run_id = r.run_id
            WHERE r.hypothesis_key = ? AND se.skipped = 1
              AND se.predicted IS NOT NULL AND se.actual IS NOT NULL
            ORDER BY se.run_id, se.grid_string_id, se.step
            """,
            conn,
            params=[hypothesis_key],
        )
        return df
    finally:
        conn.close()


def query_skipped_correct_insight_aggregates(hypothesis_key, db_path=None):
    """
    스킵되었으나 예측 평가 가능한 스텝에 대해, '스킵했지만 일치(is_correct=1)' 구간의 인사이트 집계.
    뷰어/스크립트에서 신뢰도·스킵 사유 특징 분석용.

    Args:
        hypothesis_key: runs.hypothesis_key (e.g. 'first_anchor_window9_freq518_win50')
        db_path: optional; results DB 경로

    Returns:
        dict:
          - total_evaluable: int (스킵 + predicted/actual 있음)
          - total_correct: int (스킵 + is_correct=1)
          - total_wrong: int (스킵 + is_correct=0)
          - confidence_mean_correct: float (일치한 스킵의 신뢰도 평균)
          - confidence_median_correct: float (일치한 스킵의 신뢰도 중앙값)
          - confidence_p25_correct, confidence_p75_correct: float
          - skip_reason_counts_correct: list of {skip_reason, count, pct}
          - skip_reason_counts_wrong: list of {skip_reason, count, pct}
    """
    import pandas as pd

    conn = get_results_db_connection(db_path)
    try:
        init_results_schema(conn)
        df = pd.read_sql_query(
            """
            SELECT se.is_correct, se.confidence,
                   COALESCE(NULLIF(TRIM(se.skip_reason), ''), '미분류') AS skip_reason
            FROM step_events se
            JOIN runs r ON se.run_id = r.run_id
            WHERE r.hypothesis_key = ? AND se.skipped = 1
              AND se.predicted IS NOT NULL AND se.actual IS NOT NULL
            """,
            conn,
            params=[hypothesis_key],
        )
    finally:
        conn.close()

    if len(df) == 0:
        return {
            "total_evaluable": 0,
            "total_correct": 0,
            "total_wrong": 0,
            "confidence_mean_correct": None,
            "confidence_median_correct": None,
            "confidence_p25_correct": None,
            "confidence_p75_correct": None,
            "skip_reason_counts_correct": [],
            "skip_reason_counts_wrong": [],
        }

    total_evaluable = len(df)
    correct = df[df["is_correct"] == 1]
    wrong = df[df["is_correct"] == 0]
    total_correct = len(correct)
    total_wrong = len(wrong)

    confidence_mean_correct = float(correct["confidence"].mean()) if total_correct > 0 else None
    confidence_median_correct = float(correct["confidence"].median()) if total_correct > 0 else None
    confidence_p25_correct = float(correct["confidence"].quantile(0.25)) if total_correct > 0 else None
    confidence_p75_correct = float(correct["confidence"].quantile(0.75)) if total_correct > 0 else None

    def _reason_counts(sub):
        if len(sub) == 0:
            return []
        c = sub["skip_reason"].value_counts().reset_index()
        c.columns = ["skip_reason", "count"]
        c["pct"] = (100.0 * c["count"] / len(sub)).round(1)
        return c.to_dict("records")

    return {
        "total_evaluable": total_evaluable,
        "total_correct": total_correct,
        "total_wrong": total_wrong,
        "confidence_mean_correct": confidence_mean_correct,
        "confidence_median_correct": confidence_median_correct,
        "confidence_p25_correct": confidence_p25_correct,
        "confidence_p75_correct": confidence_p75_correct,
        "skip_reason_counts_correct": _reason_counts(correct),
        "skip_reason_counts_wrong": _reason_counts(wrong),
    }


def query_skipped_correct_insight_aggregates_sim_wr_insufficient(hypothesis_key, db_path=None):
    """
    스킵 사유가 '시뮬레이션 승률 부족'인 케이스만 대상으로 동일한 인사이트 집계.
    skip_reason LIKE '시뮬레이션 승률 부족%' 인 행만 사용 (예: "시뮬레이션 승률 부족 (45.0% < 50%)").

    Returns:
        dict: query_skipped_correct_insight_aggregates와 동일한 키 구조.
    """
    import pandas as pd

    conn = get_results_db_connection(db_path)
    try:
        init_results_schema(conn)
        df = pd.read_sql_query(
            """
            SELECT se.is_correct, se.confidence,
                   COALESCE(NULLIF(TRIM(se.skip_reason), ''), '미분류') AS skip_reason
            FROM step_events se
            JOIN runs r ON se.run_id = r.run_id
            WHERE r.hypothesis_key = ? AND se.skipped = 1
              AND se.predicted IS NOT NULL AND se.actual IS NOT NULL
              AND se.skip_reason LIKE '시뮬레이션 승률 부족%'
            """,
            conn,
            params=[hypothesis_key],
        )
    finally:
        conn.close()

    if len(df) == 0:
        return {
            "total_evaluable": 0,
            "total_correct": 0,
            "total_wrong": 0,
            "confidence_mean_correct": None,
            "confidence_median_correct": None,
            "confidence_p25_correct": None,
            "confidence_p75_correct": None,
            "skip_reason_counts_correct": [],
            "skip_reason_counts_wrong": [],
        }

    total_evaluable = len(df)
    correct = df[df["is_correct"] == 1]
    wrong = df[df["is_correct"] == 0]
    total_correct = len(correct)
    total_wrong = len(wrong)

    confidence_mean_correct = float(correct["confidence"].mean()) if total_correct > 0 else None
    confidence_median_correct = float(correct["confidence"].median()) if total_correct > 0 else None
    confidence_p25_correct = float(correct["confidence"].quantile(0.25)) if total_correct > 0 else None
    confidence_p75_correct = float(correct["confidence"].quantile(0.75)) if total_correct > 0 else None

    def _reason_counts(sub):
        if len(sub) == 0:
            return []
        c = sub["skip_reason"].value_counts().reset_index()
        c.columns = ["skip_reason", "count"]
        c["pct"] = (100.0 * c["count"] / len(sub)).round(1)
        return c.to_dict("records")

    return {
        "total_evaluable": total_evaluable,
        "total_correct": total_correct,
        "total_wrong": total_wrong,
        "confidence_mean_correct": confidence_mean_correct,
        "confidence_median_correct": confidence_median_correct,
        "confidence_p25_correct": confidence_p25_correct,
        "confidence_p75_correct": confidence_p75_correct,
        "skip_reason_counts_correct": _reason_counts(correct),
        "skip_reason_counts_wrong": _reason_counts(wrong),
    }


def _parse_sim_win_rate_from_skip_reason(skip_reason):
    """
    '시뮬레이션 승률 부족 (50.0% < 52.0%)' 형식에서 해당 스텝의 시뮬 승률(첫 번째 숫자) 추출.
    Returns float or None if parsing fails.
    """
    import re
    if not skip_reason or not isinstance(skip_reason, str):
        return None
    m = re.search(r"\((\d+\.?\d*)%", skip_reason)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (ValueError, TypeError):
        return None


def query_skipped_sim_win_rates_extracted(hypothesis_key, db_path=None):
    """
    스킵 사유가 '시뮬레이션 승률 부족 (X% < Y%)' 형태인 행에서
    해당 스텝의 시뮬 승률(X)을 추출해 반환. (predicted/actual 여부와 무관하게 skip_reason만으로 추출)

    Returns:
        dict:
          - count: int (파싱 성공한 행 수)
          - values: list of float (추출된 시뮬 승률 목록)
          - mean, median, p25, p75: float or None
          - by_correct: { "correct": { "count", "mean", "median" }, "wrong": { "count", "mean", "median" } }
          - rows: list of dict (run_id, grid_string_id, step, skip_reason, extracted_sim_win_rate, is_correct, confidence) for optional export
    """
    import pandas as pd
    import re

    conn = get_results_db_connection(db_path)
    try:
        init_results_schema(conn)
        df = pd.read_sql_query(
            """
            SELECT se.run_id, se.grid_string_id, se.step, se.skip_reason,
                   se.is_correct, se.confidence, se.predicted, se.actual
            FROM step_events se
            JOIN runs r ON se.run_id = r.run_id
            WHERE r.hypothesis_key = ? AND se.skipped = 1
              AND se.skip_reason LIKE '시뮬레이션 승률 부족%'
            ORDER BY se.run_id, se.grid_string_id, se.step
            """,
            conn,
            params=[hypothesis_key],
        )
    finally:
        conn.close()

    if len(df) == 0:
        return {
            "count": 0,
            "values": [],
            "mean": None,
            "median": None,
            "p25": None,
            "p75": None,
            "by_correct": {"correct": {"count": 0, "mean": None, "median": None}, "wrong": {"count": 0, "mean": None, "median": None}},
            "rows": [],
        }

    def parse_rate(s):
        if not s or not isinstance(s, str):
            return None
        m = re.search(r"\((\d+\.?\d*)%", s)
        if not m:
            return None
        try:
            return float(m.group(1))
        except (ValueError, TypeError):
            return None

    df["extracted_sim_win_rate"] = df["skip_reason"].map(parse_rate)
    df = df[df["extracted_sim_win_rate"].notna()]
    if len(df) == 0:
        return {
            "count": 0,
            "values": [],
            "mean": None,
            "median": None,
            "p25": None,
            "p75": None,
            "by_correct": {"correct": {"count": 0, "mean": None, "median": None}, "wrong": {"count": 0, "mean": None, "median": None}},
            "rows": [],
        }

    values = df["extracted_sim_win_rate"].astype(float).tolist()
    correct_df = df[df["is_correct"] == 1]
    wrong_df = df[df["is_correct"] == 0]
    v = df["extracted_sim_win_rate"].astype(float)

    def sub_stats(sub):
        if len(sub) == 0:
            return {"count": 0, "mean": None, "median": None}
        vv = sub["extracted_sim_win_rate"].astype(float)
        return {
            "count": len(sub),
            "mean": float(vv.mean()),
            "median": float(vv.median()),
        }

    return {
        "count": len(df),
        "values": values,
        "mean": float(v.mean()),
        "median": float(v.median()),
        "p25": float(v.quantile(0.25)),
        "p75": float(v.quantile(0.75)),
        "by_correct": {
            "correct": sub_stats(correct_df),
            "wrong": sub_stats(wrong_df),
        },
        "rows": df[
            ["run_id", "grid_string_id", "step", "skip_reason", "extracted_sim_win_rate", "is_correct", "confidence"]
        ].to_dict("records"),
    }


def _confidence_band_label(conf, bands=None):
    """신뢰도 구간 라벨. bands: [(low, high, label), ...] 또는 기본 (51.3~53, 53~55, 55~60, 60 이상)."""
    if conf is None:
        return "미상"
    c = float(conf)
    if bands is None:
        if c < 53:
            return "51.3~53%"
        if c < 55:
            return "53~55%"
        if c < 60:
            return "55~60%"
        return "60% 이상"
    for low, high, label in bands:
        if low <= c < high:
            return label
    return "기타"


def query_skipped_sim_wr_insufficient_by_confidence(hypothesis_key, db_path=None, high_confidence_threshold=55.0):
    """
    시뮬 승률 부족 스킵 케이스를 신뢰도 구간별로 나누어, 각 구간의 시뮬 승률(추출값) 특성 분석.
    신뢰도가 높은 케이스가 갖는 시뮬 승률 특성 파악용.

    Returns:
        dict:
          - summary_by_confidence_band: list of { confidence_band, count, mean_sim_win_rate, median_sim_win_rate, correct_count, correct_rate_pct }
          - high_confidence: { threshold, count, mean_sim_win_rate, median_sim_win_rate, correct_count, correct_rate_pct } (confidence >= threshold)
          - lower_confidence: { count, mean_sim_win_rate, median_sim_win_rate, correct_count, correct_rate_pct } (confidence < threshold)
          - total_count: int
    """
    import pandas as pd
    import re

    conn = get_results_db_connection(db_path)
    try:
        init_results_schema(conn)
        df = pd.read_sql_query(
            """
            SELECT se.run_id, se.grid_string_id, se.step, se.skip_reason,
                   se.is_correct, se.confidence
            FROM step_events se
            JOIN runs r ON se.run_id = r.run_id
            WHERE r.hypothesis_key = ? AND se.skipped = 1
              AND se.skip_reason LIKE '시뮬레이션 승률 부족%'
            ORDER BY se.run_id, se.grid_string_id, se.step
            """,
            conn,
            params=[hypothesis_key],
        )
    finally:
        conn.close()

    if len(df) == 0:
        return {
            "summary_by_confidence_band": [],
            "high_confidence": {"threshold": high_confidence_threshold, "count": 0, "mean_sim_win_rate": None, "median_sim_win_rate": None, "correct_count": 0, "correct_rate_pct": None},
            "lower_confidence": {"count": 0, "mean_sim_win_rate": None, "median_sim_win_rate": None, "correct_count": 0, "correct_rate_pct": None},
            "total_count": 0,
        }

    def parse_rate(s):
        if not s or not isinstance(s, str):
            return None
        m = re.search(r"\((\d+\.?\d*)%", s)
        if not m:
            return None
        try:
            return float(m.group(1))
        except (ValueError, TypeError):
            return None

    df["extracted_sim_win_rate"] = df["skip_reason"].map(parse_rate)
    # 일치율은 '해당 구간 스킵된 전체 스텝'을 분모로 함. 시뮬 승률 평균/중앙값만 추출 가능한 행으로 계산.
    df["confidence_band"] = df["confidence"].map(lambda c: _confidence_band_label(c))
    band_order = ["51.3~53%", "53~55%", "55~60%", "60% 이상"]
    df["_order"] = df["confidence_band"].map(lambda b: band_order.index(b) if b in band_order else 99)
    df_with_wr = df[df["extracted_sim_win_rate"].notna()].copy()
    df_with_wr["extracted_sim_win_rate"] = df_with_wr["extracted_sim_win_rate"].astype(float)

    def band_stats(full_band, sub_with_wr):
        """full_band: 해당 구간 스킵 전체. sub_with_wr: 그중 시뮬 승률 추출 가능한 행."""
        if len(full_band) == 0:
            return {"count": 0, "sim_wr_valid_count": 0, "mean_sim_win_rate": None, "median_sim_win_rate": None, "correct_count": 0, "correct_rate_pct": None}
        total = len(full_band)
        correct_count = int((full_band["is_correct"] == 1).sum())
        correct_rate_pct = round(100.0 * correct_count / total, 1)  # 해당 구간 스킵 전체 기준 일치율
        if len(sub_with_wr) == 0:
            return {"count": total, "sim_wr_valid_count": 0, "mean_sim_win_rate": None, "median_sim_win_rate": None, "correct_count": correct_count, "correct_rate_pct": correct_rate_pct}
        v = sub_with_wr["extracted_sim_win_rate"]
        return {
            "count": total,
            "sim_wr_valid_count": len(sub_with_wr),
            "mean_sim_win_rate": float(v.mean()),
            "median_sim_win_rate": float(v.median()),
            "correct_count": correct_count,
            "correct_rate_pct": correct_rate_pct,
        }

    summary_by_band = []
    for _order in sorted(df["_order"].unique()):
        full_band = df[df["_order"] == _order]
        sub_with_wr = df_with_wr[df_with_wr["_order"] == _order]
        band_name = full_band["confidence_band"].iloc[0]
        s = band_stats(full_band, sub_with_wr)
        summary_by_band.append({"confidence_band": band_name, **s})

    high_full = df[df["confidence"] >= high_confidence_threshold]
    low_full = df[df["confidence"] < high_confidence_threshold]
    high_with_wr = df_with_wr[df_with_wr["confidence"] >= high_confidence_threshold]
    low_with_wr = df_with_wr[df_with_wr["confidence"] < high_confidence_threshold]
    high_s = band_stats(high_full, high_with_wr)
    low_s = band_stats(low_full, low_with_wr)

    return {
        "summary_by_confidence_band": summary_by_band,
        "high_confidence": {"threshold": high_confidence_threshold, "count": high_s["count"], "mean_sim_win_rate": high_s["mean_sim_win_rate"], "median_sim_win_rate": high_s["median_sim_win_rate"], "correct_count": high_s["correct_count"], "correct_rate_pct": high_s["correct_rate_pct"]},
        "lower_confidence": {"count": low_s["count"], "mean_sim_win_rate": low_s["mean_sim_win_rate"], "median_sim_win_rate": low_s["median_sim_win_rate"], "correct_count": low_s["correct_count"], "correct_rate_pct": low_s["correct_rate_pct"]},
        "total_count": len(df),
    }


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
    라이브 run 상세: run_meta, run_summary, run_summary_by_mode, step_events.

    Returns:
        dict: {"run_meta", "run_summary", "run_summary_by_mode", "step_events"} 또는 None.
        run_summary: 첫 번째 모드 요약(하위 호환). run_summary_by_mode: mode별 요약 dict.
    """
    conn = get_results_db_connection(db_path)
    try:
        init_live_results_schema(conn)
        import pandas as pd
        df_r = pd.read_sql_query("SELECT * FROM live_runs WHERE run_id = ?", conn, params=[run_id])
        df_s = pd.read_sql_query("SELECT * FROM live_run_summary WHERE run_id = ?", conn, params=[run_id])
        df_e = pd.read_sql_query(
            "SELECT * FROM live_step_events WHERE run_id = ? ORDER BY mode, step",
            conn,
            params=[run_id],
        )
        if len(df_r) == 0:
            return None
        run_summary = df_s.iloc[0].to_dict() if len(df_s) > 0 else {}
        run_summary_by_mode = {}
        if len(df_s) > 0 and "mode" in df_s.columns:
            run_summary_by_mode = {row["mode"]: row.to_dict() for _, row in df_s.iterrows()}
        return {
            "run_meta": df_r.iloc[0].to_dict(),
            "run_summary": run_summary,
            "run_summary_by_mode": run_summary_by_mode,
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


def query_simulation_consecutive_failure_stats(hypothesis_key, db_path=None):
    """
    특정 가설의 시뮬레이션 결과에서 연속 불일치(consecutive failures) 통계.
    grid_results와 runs JOIN하여 hypothesis_key로 필터 후 집계.

    Returns:
        dict: {
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
                COUNT(*) AS total_grid_results,
                COALESCE(AVG(gr.max_consecutive_failures), 0) AS avg_mcf,
                COALESCE(MAX(gr.max_consecutive_failures), 0) AS worst_mcf,
                COALESCE(AVG(gr.accuracy), 0) AS avg_acc
            FROM grid_results gr
            JOIN runs r ON gr.run_id = r.run_id
            WHERE r.hypothesis_key = ?
            """,
            conn,
            params=[hypothesis_key],
        )
        df_dist = pd.read_sql_query(
            """
            SELECT gr.max_consecutive_failures AS mcf, COUNT(*) AS cnt
            FROM grid_results gr
            JOIN runs r ON gr.run_id = r.run_id
            WHERE r.hypothesis_key = ?
            GROUP BY gr.max_consecutive_failures
            ORDER BY gr.max_consecutive_failures
            """,
            conn,
            params=[hypothesis_key],
        )
        row = df_agg.iloc[0] if len(df_agg) > 0 else {}
        dist = {int(r["mcf"]) if r["mcf"] is not None else 0: int(r["cnt"]) for _, r in df_dist.iterrows()}
        return {
            "total_grid_results": int(row.get("total_grid_results", 0) or 0),
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


def _max_consecutive_matches_from_events(events):
    """
    step 이벤트 리스트에서 연속 일치(consecutive correct) 최대 횟수.
    skipped=1인 스텝은 제외하고, is_correct=1만 연속으로 카운트.
    """
    max_m = 0
    cur = 0
    for e in events:
        if e.get("skipped") == 1 or e.get("skipped") is True:
            continue
        if e.get("is_correct") == 1 or e.get("is_correct") is True:
            cur += 1
            max_m = max(max_m, cur)
        else:
            cur = 0
    return max_m


def query_simulation_consecutive_match_stats(hypothesis_key, db_path=None):
    """
    특정 가설의 시뮬레이션 결과에서 연속 일치(consecutive correct) 통계.
    step_events에서 (run_id, grid_string_id)별로 연속 일치 최대 횟수를 계산한 뒤 집계.

    Returns:
        dict: {
            "total_grid_results": int,
            "max_consecutive_matches_dist": {0: n, 1: n, ...},
            "avg_max_consecutive_matches": float,
            "best_max_consecutive_matches": int,
            "avg_accuracy_overall": float,
        }
    """
    conn = get_results_db_connection(db_path)
    try:
        import pandas as pd
        df = pd.read_sql_query(
            """
            SELECT se.run_id, se.grid_string_id, se.step, se.is_correct, se.skipped
            FROM step_events se
            JOIN runs r ON se.run_id = r.run_id
            WHERE r.hypothesis_key = ?
            ORDER BY se.run_id, se.grid_string_id, se.step
            """,
            conn,
            params=[hypothesis_key],
        )
        if len(df) == 0:
            return {
                "total_grid_results": 0,
                "max_consecutive_matches_dist": {},
                "avg_max_consecutive_matches": 0.0,
                "best_max_consecutive_matches": 0,
                "avg_accuracy_overall": 0.0,
            }
        mcm_list = []
        for (run_id, gid), grp in df.groupby(["run_id", "grid_string_id"]):
            events = grp.sort_values("step").to_dict("records")
            mcm = _max_consecutive_matches_from_events(events)
            mcm_list.append({"run_id": run_id, "grid_string_id": gid, "max_consecutive_matches": mcm})
        df_mcm = pd.DataFrame(mcm_list)
        placeholders = ",".join("?" * len(df_mcm["run_id"].unique().tolist()))
        df_gr = pd.read_sql_query(
            """
            SELECT run_id, grid_string_id, accuracy
            FROM grid_results
            WHERE run_id IN ({})
            """.format(placeholders),
            conn,
            params=df_mcm["run_id"].unique().tolist(),
        )
        df_mcm = df_mcm.merge(df_gr, on=["run_id", "grid_string_id"], how="left")
        dist = df_mcm["max_consecutive_matches"].value_counts().sort_index()
        dist = {int(k): int(v) for k, v in dist.items()}
        return {
            "total_grid_results": len(df_mcm),
            "max_consecutive_matches_dist": dist,
            "avg_max_consecutive_matches": float(df_mcm["max_consecutive_matches"].mean()),
            "best_max_consecutive_matches": int(df_mcm["max_consecutive_matches"].max()),
            "avg_accuracy_overall": float(df_mcm["accuracy"].mean()) if "accuracy" in df_mcm.columns and df_mcm["accuracy"].notna().any() else 0.0,
        }
    finally:
        conn.close()


def query_simulation_top_consecutive_matches(hypothesis_key, limit=10, db_path=None):
    """
    특정 가설 결과에서 연속 일치가 가장 많이 발생한 개별 스트링 상위 limit건.

    Returns:
        list of dict: [{
            "run_id", "grid_string_id", "max_consecutive_matches", "accuracy",
            "total_predictions", "total_skipped", "created_at", "hypothesis_key", ...
        }, ...]
    """
    conn = get_results_db_connection(db_path)
    try:
        import pandas as pd
        df = pd.read_sql_query(
            """
            SELECT se.run_id, se.grid_string_id, se.step, se.is_correct, se.skipped
            FROM step_events se
            JOIN runs r ON se.run_id = r.run_id
            WHERE r.hypothesis_key = ?
            ORDER BY se.run_id, se.grid_string_id, se.step
            """,
            conn,
            params=[hypothesis_key],
        )
        if len(df) == 0:
            return []
        mcm_list = []
        for (run_id, gid), grp in df.groupby(["run_id", "grid_string_id"]):
            events = grp.sort_values("step").to_dict("records")
            mcm = _max_consecutive_matches_from_events(events)
            mcm_list.append({"run_id": run_id, "grid_string_id": gid, "max_consecutive_matches": mcm})
        df_mcm = pd.DataFrame(mcm_list)
        df_mcm = df_mcm.sort_values("max_consecutive_matches", ascending=False).head(limit)
        run_ids = df_mcm["run_id"].unique().tolist()
        placeholders = ",".join("?" * len(run_ids))
        df_r = pd.read_sql_query(
            "SELECT run_id, created_at, hypothesis_key FROM runs WHERE run_id IN ({})".format(placeholders),
            conn,
            params=run_ids,
        )
        df_gr = pd.read_sql_query(
            """
            SELECT run_id, grid_string_id, accuracy, total_predictions, total_skipped, total_failures
            FROM grid_results
            WHERE run_id IN ({})
            """.format(placeholders),
            conn,
            params=run_ids,
        )
        df_mcm = df_mcm.merge(df_r, on="run_id", how="left").merge(
            df_gr, on=["run_id", "grid_string_id"], how="left"
        )
        return df_mcm.to_dict("records")
    finally:
        conn.close()
