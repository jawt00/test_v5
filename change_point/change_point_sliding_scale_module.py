"""
슬라이딩 스케일 진입 모듈 — 윈도우 9 진입 타점 최적화

- 기존 가설 코드(change_point_hypothesis_module2.py)는 수정하지 않음. 독립 구현.
- C = 빈도 기반 신뢰도, W = 시뮬레이션 승률. 세 가지 조건 중 하나라도 만족하면 ENTRY.
  (기본) C > 51.3 and W > 49.9
  (추가1 알짜) 51.1 <= C <= 51.3 and W >= 54.0
  (추가2 우세) C > 53.2 and W >= 46.5
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pandas as pd
from svg_parser_module import get_change_point_db_connection


# 세 조건 기본값 (C=빈도 신뢰도, W=시뮬 승률)
BASE_CONF = 51.3   # 기본: C > base_conf and W > base_wr
BASE_WR = 49.9
ADD1_CONF_LO = 51.1   # 추가1(알짜): add1_conf_lo <= C <= add1_conf_hi and W >= add1_wr
ADD1_CONF_HI = 51.3
ADD1_WR = 54.0
ADD2_CONF = 53.2      # 추가2(우세): C > add2_conf and W >= add2_wr
ADD2_WR = 46.5


def _safe_pred_val(v):
    """pandas NA/NaN 또는 None이면 None, 그 외는 그대로 반환."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def three_condition_entry(C, W, config=None):
    """
    (활성화된) 조건 중 하나라도 만족하면 ENTRY.
    - 기본: C > base_conf and W > base_wr  (enable_base=True일 때만)
    - 추가1(알짜): add1_conf_lo <= C <= add1_conf_hi and W >= add1_wr  (enable_add1=True일 때만)
    - 추가2(우세): C > add2_conf and W >= add2_wr  (enable_add2=True일 때만)

    config에 enable_base, enable_add1, enable_add2가 있으면 해당 조건만 검사(기본 True).
    """
    cfg = config or {}
    base_conf = cfg.get("base_conf", BASE_CONF)
    base_wr = cfg.get("base_wr", BASE_WR)
    add1_conf_lo = cfg.get("add1_conf_lo", ADD1_CONF_LO)
    add1_conf_hi = cfg.get("add1_conf_hi", ADD1_CONF_HI)
    add1_wr = cfg.get("add1_wr", ADD1_WR)
    add2_conf = cfg.get("add2_conf", ADD2_CONF)
    add2_wr = cfg.get("add2_wr", ADD2_WR)
    enable_base = cfg.get("enable_base", True)
    enable_add1 = cfg.get("enable_add1", True)
    enable_add2 = cfg.get("enable_add2", True)

    if C is None:
        return False, None
    c, w = float(C), float(W) if W is not None else 0.0

    if enable_base and c > base_conf and w > base_wr:
        return True, "기본"
    if enable_add1 and add1_conf_lo <= c <= add1_conf_hi and w >= add1_wr:
        return True, "추가1"
    if enable_add2 and c > add2_conf and w >= add2_wr:
        return True, "추가2"
    return False, None


def validate_first_anchor_window9_sliding_scale_cp(
    grid_string_id,
    cutoff_grid_string_id,
    threshold=0,
    stop_on_match=False,
    predictions_conn=None,
    sliding_scale_config=None,
):
    """
    윈도우 9만 검증. 세 조건 중 하나라도 만족하면 ENTRY.
    (기본) C > 51.3 and W > 49.9
    (추가1) 51.1 <= C <= 51.3 and W >= 54.0
    (추가2) C > 53.2 and W >= 46.5
    """
    WINDOW_SIZES = (9,)
    config = sliding_scale_config or {}
    conn = get_change_point_db_connection()
    pred_conn = predictions_conn if predictions_conn is not None else conn
    try:
        df = pd.read_sql_query(
            "SELECT grid_string FROM preprocessed_grid_strings WHERE id = ?",
            conn,
            params=[grid_string_id],
        )
        if len(df) == 0:
            return None
        grid_string = df.iloc[0]["grid_string"]
        min_ws = min(WINDOW_SIZES)
        if len(grid_string) < min_ws:
            return {
                "grid_string_id": grid_string_id,
                "max_consecutive_failures": 0,
                "total_steps": 0,
                "total_failures": 0,
                "total_predictions": 0,
                "total_skipped": 0,
                "accuracy": 0.0,
                "history": [],
                "stopped_early": False,
            }
        anchors = []
        for i in range(len(grid_string) - 1):
            if grid_string[i] != grid_string[i + 1]:
                anchors.append(i)
        anchors = sorted(list(set(anchors)))
        if not anchors:
            return {
                "grid_string_id": grid_string_id,
                "max_consecutive_failures": 0,
                "total_steps": 0,
                "total_failures": 0,
                "total_predictions": 0,
                "total_skipped": 0,
                "accuracy": 0.0,
                "history": [],
                "stopped_early": False,
            }
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0
        stopped_early = False
        anchor_idx = 0
        validated_positions = set()

        while anchor_idx < len(anchors):
            next_anchor = anchors[anchor_idx]
            anchor_matched = False

            for window_size in WINDOW_SIZES:
                pos = next_anchor + window_size - 1
                if pos >= len(grid_string):
                    continue
                if pos in validated_positions:
                    continue

                total_steps += 1
                actual = grid_string[pos]
                prefix_len = window_size - 1
                prefix = grid_string[pos - prefix_len : pos]

                q = """
                    SELECT method, predicted_value, confidence, b_ratio, p_ratio, sim_win_rate_pct
                    FROM simulation_predictions_change_point
                    WHERE window_size = ? AND prefix = ? AND threshold = ?
                """
                df_pred = pd.read_sql_query(q, pred_conn, params=[window_size, prefix, threshold])
                by_method = {}
                for _, row in df_pred.iterrows():
                    by_method[row["method"]] = row

                freq_row = by_method.get("빈도 기반")
                if freq_row is None:
                    total_skipped += 1
                    skip_reason = "예측 테이블에 값 없음" if len(by_method) == 0 else "빈도 기반 없음"
                    history.append({
                        "step": total_steps,
                        "position": pos,
                        "anchor": next_anchor,
                        "window_size": window_size,
                        "prefix": prefix,
                        "predicted": None,
                        "actual": actual,
                        "is_correct": None,
                        "confidence": 0.0,
                        "selected_window_size": window_size,
                        "all_predictions": [],
                        "skipped": True,
                        "skip_reason": skip_reason,
                        "skipped_prediction": None,
                    })
                    validated_positions.add(pos)
                    continue

                conf_freq = freq_row["confidence"] if freq_row["confidence"] is not None else 0.0
                sim_wr = freq_row.get("sim_win_rate_pct")
                if sim_wr is None:
                    total_skipped += 1
                    history.append({
                        "step": total_steps,
                        "position": pos,
                        "anchor": next_anchor,
                        "window_size": window_size,
                        "prefix": prefix,
                        "predicted": None,
                        "actual": actual,
                        "is_correct": None,
                        "confidence": conf_freq,
                        "selected_window_size": window_size,
                        "all_predictions": [],
                        "skipped": True,
                        "skip_reason": "시뮬레이션 승률 없음",
                        "skipped_prediction": _safe_pred_val(freq_row["predicted_value"]),
                    })
                    validated_positions.add(pos)
                    continue

                entry, condition_name = three_condition_entry(conf_freq, sim_wr, config)
                if not entry:
                    total_skipped += 1
                    skip_reason = f"세 조건 모두 미충족 (C={conf_freq:.1f}%, W={sim_wr:.1f}%)"
                    history.append({
                        "step": total_steps,
                        "position": pos,
                        "anchor": next_anchor,
                        "window_size": window_size,
                        "prefix": prefix,
                        "predicted": None,
                        "actual": actual,
                        "is_correct": None,
                        "confidence": conf_freq,
                        "selected_window_size": window_size,
                        "all_predictions": [],
                        "skipped": True,
                        "skip_reason": skip_reason,
                        "skipped_prediction": _safe_pred_val(freq_row["predicted_value"]),
                    })
                    validated_positions.add(pos)
                    continue

                predicted = _safe_pred_val(freq_row["predicted_value"])
                confidence = conf_freq
                ok = predicted == actual
                total_predictions += 1
                if not ok:
                    consecutive_failures += 1
                    total_failures += 1
                    if consecutive_failures > max_consecutive_failures:
                        max_consecutive_failures = consecutive_failures
                else:
                    consecutive_failures = 0
                    anchor_matched = True

                # 예측 사유: 어떤 조건으로 진입했는지 (기본/추가1/추가2)
                entry_reason = f"신뢰도 {conf_freq:.1f}%, 시뮬승률 {sim_wr:.1f}% (조건: {condition_name})"

                history.append({
                    "step": total_steps,
                    "position": pos,
                    "anchor": next_anchor,
                    "window_size": window_size,
                    "prefix": prefix,
                    "predicted": predicted,
                    "actual": actual,
                    "is_correct": ok,
                    "confidence": confidence,
                    "selected_window_size": window_size,
                    "all_predictions": [{
                        "window_size": window_size,
                        "prefix": prefix,
                        "predicted": predicted,
                        "confidence": confidence,
                        "b_ratio": freq_row["b_ratio"],
                        "p_ratio": freq_row["p_ratio"],
                    }],
                    "skipped": False,
                    "entry_reason": entry_reason,
                })
                validated_positions.add(pos)
            anchor_idx += 1

            if stop_on_match and anchor_matched:
                stopped_early = True
                break

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
            "stopped_early": stopped_early,
        }
    finally:
        conn.close()


def batch_validate_first_anchor_window9_sliding_scale_cp(
    cutoff_grid_string_id,
    threshold=0,
    stop_on_match=False,
    max_grid_string_id=None,
    sliding_scale_config=None,
):
    """
    슬라이딩 스케일 배치 검증. cutoff 이후 grid_string에 대해 validate_first_anchor_window9_sliding_scale_cp 호출.
    max_grid_string_id가 있으면 id > cutoff AND id <= max_grid_string_id 구간만 검증.
    """
    conn = get_change_point_db_connection()
    try:
        if max_grid_string_id is not None:
            df_test = pd.read_sql_query(
                "SELECT id FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id",
                conn,
                params=[cutoff_grid_string_id, max_grid_string_id],
            )
        else:
            df_test = pd.read_sql_query(
                "SELECT id FROM preprocessed_grid_strings WHERE id > ? ORDER BY id",
                conn,
                params=[cutoff_grid_string_id],
            )
        df_train = pd.read_sql_query(
            "SELECT id FROM preprocessed_grid_strings WHERE id <= ? ORDER BY id",
            conn,
            params=[cutoff_grid_string_id],
        )
        if len(df_test) == 0:
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
                "train_grid_string_ids": df_train["id"].tolist() if len(df_train) > 0 else [],
            }
        test_gids = df_test["id"].tolist()
        train_gids = df_train["id"].tolist() if len(df_train) > 0 else []
        results = []
        for gid in test_gids:
            r = validate_first_anchor_window9_sliding_scale_cp(
                gid,
                cutoff_grid_string_id,
                threshold=threshold,
                stop_on_match=stop_on_match,
                sliding_scale_config=sliding_scale_config,
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
                "total_stopped_early": sum(1 for x in results if x.get("stopped_early", False)),
            }
        return {
            "results": results,
            "summary": summary,
            "grid_string_ids": test_gids,
            "train_grid_string_ids": train_gids,
        }
    finally:
        conn.close()
