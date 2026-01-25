"""
Change-point N-gram 기반 예측 레이어

- load_ngram_chunks_change_point: ngram_chunks_change_point 조회
- create_stored_predictions_change_point_table: stored_predictions_change_point 테이블 생성
- save_or_update_predictions_for_change_point_data: 예측값 계산 후 stored_predictions_change_point에 저장
"""

import pandas as pd

from svg_parser_module import get_change_point_db_connection
from hypothesis_validation_app import (
    build_frequency_model,
    build_weighted_model,
    build_safety_first_model,
    predict_for_prefix,
    predict_confidence_threshold,
)


def load_preprocessed_grid_strings_cp():
    """change_point preprocessed_grid_strings에서 id, created_at 로드 (드롭다운용)."""
    conn = get_change_point_db_connection()
    try:
        df = pd.read_sql_query(
            "SELECT id, created_at FROM preprocessed_grid_strings ORDER BY id DESC",
            conn,
        )
        return df
    finally:
        conn.close()


def get_stored_predictions_change_point_count():
    """stored_predictions_change_point 레코드 수 반환."""
    conn = get_change_point_db_connection()
    try:
        df = pd.read_sql_query(
            "SELECT COUNT(*) as cnt FROM stored_predictions_change_point",
            conn,
        )
        return int(df.iloc[0]["cnt"]) if len(df) > 0 else 0
    except Exception:
        return 0
    finally:
        conn.close()


def load_ngram_chunks_change_point(window_size, grid_string_ids=None):
    """
    ngram_chunks_change_point에서 데이터 로드.
    반환 형식은 load_ngram_chunks와 동일 (grid_string_id, prefix, suffix, chunk_index).

    Args:
        window_size: 윈도우 크기
        grid_string_ids: 학습용 grid_string id 목록 (None이면 전체)

    Returns:
        pd.DataFrame: grid_string_id, prefix, suffix, chunk_index 컬럼
    """
    conn = get_change_point_db_connection()
    try:
        if grid_string_ids is None or len(grid_string_ids) == 0:
            query = """
                SELECT grid_string_id, prefix, suffix, chunk_index
                FROM ngram_chunks_change_point
                WHERE window_size = ?
                ORDER BY grid_string_id, chunk_index
            """
            df = pd.read_sql_query(query, conn, params=[window_size])
        else:
            if len(grid_string_ids) > 900:
                all_dfs = []
                for i in range(0, len(grid_string_ids), 900):
                    batch = grid_string_ids[i : i + 900]
                    placeholders = ",".join(["?"] * len(batch))
                    query = f"""
                        SELECT grid_string_id, prefix, suffix, chunk_index
                        FROM ngram_chunks_change_point
                        WHERE window_size = ? AND grid_string_id IN ({placeholders})
                        ORDER BY grid_string_id, chunk_index
                    """
                    part = pd.read_sql_query(query, conn, params=[window_size] + batch)
                    all_dfs.append(part)
                df = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
            else:
                placeholders = ",".join(["?"] * len(grid_string_ids))
                query = f"""
                    SELECT grid_string_id, prefix, suffix, chunk_index
                    FROM ngram_chunks_change_point
                    WHERE window_size = ? AND grid_string_id IN ({placeholders})
                    ORDER BY grid_string_id, chunk_index
                """
                df = pd.read_sql_query(query, conn, params=[window_size] + grid_string_ids)
        return df
    finally:
        conn.close()


def create_stored_predictions_change_point_table():
    """
    change_point_ngram.db 내 stored_predictions_change_point 테이블 생성.
    스키마는 stored_predictions와 동일.
    """
    conn = get_change_point_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DROP TABLE IF EXISTS stored_predictions_change_point")
        cursor.execute("""
            CREATE TABLE stored_predictions_change_point (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                window_size INTEGER NOT NULL,
                prefix TEXT NOT NULL,
                predicted_value TEXT,
                confidence REAL,
                b_ratio REAL,
                p_ratio REAL,
                method TEXT NOT NULL,
                threshold REAL NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                updated_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                UNIQUE(window_size, prefix, method, threshold)
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cp_sp_window_prefix ON stored_predictions_change_point(window_size, prefix)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cp_sp_method_threshold ON stored_predictions_change_point(method, threshold)"
        )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def save_or_update_predictions_for_change_point_data(
    cutoff_grid_string_id=None,
    window_sizes=(5, 6, 7, 8, 9),
    methods=("빈도 기반",),
    thresholds=(0, 50, 60, 70, 80, 90, 100),
    batch_size=1000,
):
    """
    ngram_chunks_change_point로 모델 구축 후 예측값을 stored_predictions_change_point에 저장.

    - cutoff 이전(id <= cutoff) grid_string으로만 학습
    - hypothesis_validation_app의 build_* / predict_* 재사용
    """
    conn = get_change_point_db_connection()
    try:
        if cutoff_grid_string_id is None:
            q = "SELECT id FROM preprocessed_grid_strings ORDER BY id"
            params = []
        else:
            q = "SELECT id FROM preprocessed_grid_strings WHERE id <= ? ORDER BY id"
            params = [cutoff_grid_string_id]
        df_hist = pd.read_sql_query(q, conn, params=params)
        if len(df_hist) == 0:
            return {"total_saved": 0, "new_records": 0, "updated_records": 0, "unique_prefixes": 0}

        historical_ids = df_hist["id"].tolist()
        total_saved = 0
        new_records = 0
        updated_records = 0
        unique_prefixes_set = set()
        cursor = conn.cursor()

        for window_size in window_sizes:
            train_ngrams = load_ngram_chunks_change_point(window_size=window_size, grid_string_ids=historical_ids)
            if len(train_ngrams) == 0:
                continue

            for method in methods:
                if method == "빈도 기반":
                    model = build_frequency_model(train_ngrams)
                elif method == "가중치 기반":
                    model = build_weighted_model(train_ngrams)
                elif method == "안전 우선":
                    model = build_safety_first_model(train_ngrams)
                else:
                    model = build_frequency_model(train_ngrams)

                all_prefixes = set(train_ngrams["prefix"].unique())
                batch_data = []

                for prefix in all_prefixes:
                    unique_prefixes_set.add((window_size, prefix))
                    for threshold in thresholds:
                        if threshold == 0:
                            res = predict_for_prefix(model, prefix, method)
                        else:
                            res = predict_confidence_threshold(model, prefix, method, threshold)
                        pred = res.get("predicted")
                        conf = res.get("confidence", 0.0)
                        ratios = res.get("ratios", {})
                        b_ratio = ratios.get("b", 0.0)
                        p_ratio = ratios.get("p", 0.0)
                        batch_data.append((window_size, prefix, pred, conf, b_ratio, p_ratio, method, threshold))

                for i in range(0, len(batch_data), batch_size):
                    batch = batch_data[i : i + batch_size]
                    for item in batch:
                        try:
                            cursor.execute(
                                """
                                SELECT id FROM stored_predictions_change_point
                                WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                                """,
                                (item[0], item[1], item[6], item[7]),
                            )
                            existing = cursor.fetchone()
                            cursor.execute(
                                """
                                INSERT OR REPLACE INTO stored_predictions_change_point
                                (window_size, prefix, predicted_value, confidence, b_ratio, p_ratio, method, threshold, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
                                """,
                                item,
                            )
                            if existing:
                                updated_records += 1
                            else:
                                new_records += 1
                            total_saved += 1
                        except Exception:
                            continue

        conn.commit()
        return {
            "total_saved": total_saved,
            "new_records": new_records,
            "updated_records": updated_records,
            "unique_prefixes": len(unique_prefixes_set),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 다중 윈도우 예측 / 검증 (change_point 전용, stored_predictions_change_point 조회)
# ---------------------------------------------------------------------------

def get_multi_window_prediction_cp(
    grid_string,
    position,
    window_sizes=(5, 6, 7, 8, 9),
    method="빈도 기반",
    threshold=0,
):
    """
    여러 윈도우 크기 중 최고 신뢰도 예측값 선택 (stored_predictions_change_point 조회).
    """
    conn = get_change_point_db_connection()
    try:
        all_predictions = []
        for window_size in window_sizes:
            prefix_len = window_size - 1
            if position < prefix_len:
                continue
            prefix = grid_string[position - prefix_len : position]
            q = """
                SELECT predicted_value, confidence, b_ratio, p_ratio
                FROM stored_predictions_change_point
                WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                LIMIT 1
            """
            df = pd.read_sql_query(q, conn, params=[window_size, prefix, method, threshold])
            if len(df) == 0:
                continue
            row = df.iloc[0]
            all_predictions.append({
                "window_size": window_size,
                "prefix": prefix,
                "predicted": row["predicted_value"],
                "confidence": row["confidence"],
                "b_ratio": row["b_ratio"],
                "p_ratio": row["p_ratio"],
            })
        if not all_predictions:
            return {
                "predicted": None,
                "confidence": 0.0,
                "window_size": None,
                "prefix": None,
                "all_predictions": [],
            }
        best = max(all_predictions, key=lambda x: x["confidence"])
        return {
            "predicted": best["predicted"],
            "confidence": best["confidence"],
            "window_size": best["window_size"],
            "prefix": best["prefix"],
            "all_predictions": all_predictions,
        }
    finally:
        conn.close()


def get_multi_window_prediction_with_confidence_skip_cp(
    grid_string,
    position,
    window_sizes=(5, 6, 7, 8, 9),
    method="빈도 기반",
    threshold=0,
    confidence_skip_threshold=None,
):
    """
    다중 윈도우 + 신뢰도 스킵. confidence < confidence_skip_threshold 이면 스킵.
    """
    conn = get_change_point_db_connection()
    try:
        all_predictions = []
        for window_size in window_sizes:
            prefix_len = window_size - 1
            if position < prefix_len:
                continue
            prefix = grid_string[position - prefix_len : position]
            q = """
                SELECT predicted_value, confidence, b_ratio, p_ratio
                FROM stored_predictions_change_point
                WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                LIMIT 1
            """
            df = pd.read_sql_query(q, conn, params=[window_size, prefix, method, threshold])
            if len(df) == 0:
                continue
            row = df.iloc[0]
            conf = row["confidence"]
            if confidence_skip_threshold is not None and conf < confidence_skip_threshold:
                continue
            all_predictions.append({
                "window_size": window_size,
                "prefix": prefix,
                "predicted": row["predicted_value"],
                "confidence": conf,
                "b_ratio": row["b_ratio"],
                "p_ratio": row["p_ratio"],
            })
        if not all_predictions:
            return {
                "predicted": None,
                "confidence": 0.0,
                "window_size": None,
                "prefix": None,
                "all_predictions": [],
                "skipped": True,
            }
        best = max(all_predictions, key=lambda x: x["confidence"])
        return {
            "predicted": best["predicted"],
            "confidence": best["confidence"],
            "window_size": best["window_size"],
            "prefix": best["prefix"],
            "all_predictions": all_predictions,
            "skipped": False,
        }
    finally:
        conn.close()


def validate_multi_window_scenario_cp(
    grid_string_id,
    cutoff_grid_string_id,
    window_sizes=(5, 6, 7, 8, 9),
    method="빈도 기반",
    threshold=0,
):
    """다중 윈도우 전략으로 단일 grid_string 검증 (change_point DB)."""
    conn = get_change_point_db_connection()
    try:
        df = pd.read_sql_query(
            "SELECT grid_string FROM preprocessed_grid_strings WHERE id = ?",
            conn,
            params=[grid_string_id],
        )
        if len(df) == 0:
            return None
        grid_string = df.iloc[0]["grid_string"]
        max_ws = max(window_sizes)
        if len(grid_string) < max_ws:
            return {
                "grid_string_id": grid_string_id,
                "max_consecutive_failures": 0,
                "total_steps": 0,
                "total_failures": 0,
                "total_predictions": 0,
                "total_skipped": 0,
                "accuracy": 0.0,
                "history": [],
            }
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        for pos in range(max_ws - 1, len(grid_string)):
            total_steps += 1
            actual = grid_string[pos]
            pred_res = get_multi_window_prediction_cp(
                grid_string, pos, window_sizes=window_sizes, method=method, threshold=threshold
            )
            pred = pred_res.get("predicted") if pred_res else None
            conf = pred_res.get("confidence", 0.0) if pred_res else 0.0
            sel_ws = pred_res.get("window_size") if pred_res else None
            pfx = pred_res.get("prefix") if pred_res else None
            all_preds = pred_res.get("all_predictions", []) if pred_res else []
            if pred is not None:
                ok = pred == actual
                total_predictions += 1
                if not ok:
                    consecutive_failures += 1
                    total_failures += 1
                    if consecutive_failures > max_consecutive_failures:
                        max_consecutive_failures = consecutive_failures
                else:
                    consecutive_failures = 0
                history.append({
                    "step": total_steps,
                    "position": pos,
                    "prefix": pfx,
                    "predicted": pred,
                    "actual": actual,
                    "is_correct": ok,
                    "confidence": conf,
                    "selected_window_size": sel_ws,
                    "all_predictions": all_preds,
                    "skipped": False,
                })
            else:
                history.append({
                    "step": total_steps,
                    "position": pos,
                    "prefix": None,
                    "predicted": None,
                    "actual": actual,
                    "is_correct": None,
                    "confidence": 0.0,
                    "selected_window_size": None,
                    "all_predictions": [],
                    "skipped": False,
                })
        acc = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        return {
            "grid_string_id": grid_string_id,
            "max_consecutive_failures": max_consecutive_failures,
            "total_steps": total_steps,
            "total_failures": total_failures,
            "total_predictions": total_predictions,
            "total_skipped": 0,
            "accuracy": acc,
            "history": history,
        }
    finally:
        conn.close()


def validate_multi_window_scenario_with_confidence_skip_cp(
    grid_string_id,
    cutoff_grid_string_id,
    window_sizes=(5, 6, 7, 8, 9),
    method="빈도 기반",
    threshold=0,
    confidence_skip_threshold=None,
):
    """다중 윈도우 + 신뢰도 스킵 전략으로 단일 grid_string 검증 (change_point DB)."""
    conn = get_change_point_db_connection()
    try:
        df = pd.read_sql_query(
            "SELECT grid_string FROM preprocessed_grid_strings WHERE id = ?",
            conn,
            params=[grid_string_id],
        )
        if len(df) == 0:
            return None
        grid_string = df.iloc[0]["grid_string"]
        max_ws = max(window_sizes)
        if len(grid_string) < max_ws:
            return {
                "grid_string_id": grid_string_id,
                "max_consecutive_failures": 0,
                "total_steps": 0,
                "total_failures": 0,
                "total_predictions": 0,
                "total_skipped": 0,
                "accuracy": 0.0,
                "history": [],
            }
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0
        for pos in range(max_ws - 1, len(grid_string)):
            total_steps += 1
            actual = grid_string[pos]
            pred_res = get_multi_window_prediction_with_confidence_skip_cp(
                grid_string,
                pos,
                window_sizes=window_sizes,
                method=method,
                threshold=threshold,
                confidence_skip_threshold=confidence_skip_threshold,
            )
            pred = pred_res.get("predicted") if pred_res else None
            conf = pred_res.get("confidence", 0.0) if pred_res else 0.0
            sel_ws = pred_res.get("window_size") if pred_res else None
            pfx = pred_res.get("prefix") if pred_res else None
            all_preds = pred_res.get("all_predictions", []) if pred_res else []
            skipped = pred_res.get("skipped", False) if pred_res else False
            if pred is not None:
                ok = pred == actual
                total_predictions += 1
                if not ok:
                    consecutive_failures += 1
                    total_failures += 1
                    if consecutive_failures > max_consecutive_failures:
                        max_consecutive_failures = consecutive_failures
                else:
                    consecutive_failures = 0
                history.append({
                    "step": total_steps,
                    "position": pos,
                    "prefix": pfx,
                    "predicted": pred,
                    "actual": actual,
                    "is_correct": ok,
                    "confidence": conf,
                    "selected_window_size": sel_ws,
                    "all_predictions": all_preds,
                    "skipped": False,
                })
            else:
                if skipped:
                    total_skipped += 1
                history.append({
                    "step": total_steps,
                    "position": pos,
                    "prefix": None,
                    "predicted": None,
                    "actual": actual,
                    "is_correct": None,
                    "confidence": 0.0,
                    "selected_window_size": None,
                    "all_predictions": [],
                    "skipped": skipped,
                })
        acc = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        return {
            "grid_string_id": grid_string_id,
            "max_consecutive_failures": max_consecutive_failures,
            "total_steps": total_steps,
            "total_failures": total_failures,
            "total_predictions": total_predictions,
            "total_skipped": total_skipped,
            "accuracy": acc,
            "history": history,
        }
    finally:
        conn.close()


def batch_validate_multi_window_scenario_cp(
    cutoff_grid_string_id,
    window_sizes=(5, 6, 7, 8, 9),
    method="빈도 기반",
    threshold=0,
):
    """다중 윈도우 전략 배치 검증 (change_point DB, cutoff 이후 grid_string)."""
    conn = get_change_point_db_connection()
    try:
        df = pd.read_sql_query(
            "SELECT id FROM preprocessed_grid_strings WHERE id > ? ORDER BY id",
            conn,
            params=[cutoff_grid_string_id],
        )
        if len(df) == 0:
            return {
                "results": [],
                "summary": {
                    "total_grid_strings": 0,
                    "avg_accuracy": 0.0,
                    "max_consecutive_failures": 0,
                    "avg_max_consecutive_failures": 0.0,
                    "total_steps": 0,
                    "total_failures": 0,
                    "total_predictions": 0,
                    "total_skipped": 0,
                },
                "grid_string_ids": [],
            }
        gids = df["id"].tolist()
        results = []
        for gid in gids:
            r = validate_multi_window_scenario_cp(
                gid, cutoff_grid_string_id,
                window_sizes=window_sizes, method=method, threshold=threshold,
            )
            if r is not None:
                results.append(r)
        if not results:
            summary = {
                "total_grid_strings": 0,
                "avg_accuracy": 0.0,
                "max_consecutive_failures": 0,
                "avg_max_consecutive_failures": 0.0,
                "total_steps": 0,
                "total_failures": 0,
                "total_predictions": 0,
                "total_skipped": 0,
            }
        else:
            n = len(results)
            summary = {
                "total_grid_strings": n,
                "avg_accuracy": sum(x["accuracy"] for x in results) / n,
                "max_consecutive_failures": max(x["max_consecutive_failures"] for x in results),
                "avg_max_consecutive_failures": sum(x["max_consecutive_failures"] for x in results) / n,
                "total_steps": sum(x["total_steps"] for x in results),
                "total_failures": sum(x["total_failures"] for x in results),
                "total_predictions": sum(x["total_predictions"] for x in results),
                "total_skipped": sum(x.get("total_skipped", 0) for x in results),
            }
        return {"results": results, "summary": summary, "grid_string_ids": gids}
    finally:
        conn.close()


def batch_validate_multi_window_with_confidence_skip_cp(
    cutoff_grid_string_id,
    window_sizes=(5, 6, 7, 8, 9),
    method="빈도 기반",
    threshold=0,
    confidence_skip_threshold=None,
):
    """다중 윈도우 + 신뢰도 스킵 배치 검증 (change_point DB)."""
    conn = get_change_point_db_connection()
    try:
        df = pd.read_sql_query(
            "SELECT id FROM preprocessed_grid_strings WHERE id > ? ORDER BY id",
            conn,
            params=[cutoff_grid_string_id],
        )
        if len(df) == 0:
            return {
                "results": [],
                "summary": {
                    "total_grid_strings": 0,
                    "avg_accuracy": 0.0,
                    "max_consecutive_failures": 0,
                    "avg_max_consecutive_failures": 0.0,
                    "total_steps": 0,
                    "total_failures": 0,
                    "total_predictions": 0,
                    "total_skipped": 0,
                },
                "grid_string_ids": [],
            }
        gids = df["id"].tolist()
        results = []
        for gid in gids:
            r = validate_multi_window_scenario_with_confidence_skip_cp(
                gid,
                cutoff_grid_string_id,
                window_sizes=window_sizes,
                method=method,
                threshold=threshold,
                confidence_skip_threshold=confidence_skip_threshold,
            )
            if r is not None:
                results.append(r)
        if not results:
            summary = {
                "total_grid_strings": 0,
                "avg_accuracy": 0.0,
                "max_consecutive_failures": 0,
                "avg_max_consecutive_failures": 0.0,
                "total_steps": 0,
                "total_failures": 0,
                "total_predictions": 0,
                "total_skipped": 0,
            }
        else:
            n = len(results)
            summary = {
                "total_grid_strings": n,
                "avg_accuracy": sum(x["accuracy"] for x in results) / n,
                "max_consecutive_failures": max(x["max_consecutive_failures"] for x in results),
                "avg_max_consecutive_failures": sum(x["max_consecutive_failures"] for x in results) / n,
                "total_steps": sum(x["total_steps"] for x in results),
                "total_failures": sum(x["total_failures"] for x in results),
                "total_predictions": sum(x["total_predictions"] for x in results),
                "total_skipped": sum(x.get("total_skipped", 0) for x in results),
            }
        return {"results": results, "summary": summary, "grid_string_ids": gids}
    finally:
        conn.close()
