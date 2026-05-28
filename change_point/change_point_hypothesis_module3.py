"""
Change-point 시뮬레이션 가설 모듈3 — 윈도우 9 빈도+승률+빈도수550 전용

- first_anchor_window9_freq518_win50_freq550 가설 (모듈2 복제 + prefix 전체 빈도수 > 550 조건)
- 원본: change_point_hypothesis_module2.py
"""

import sys
from pathlib import Path
from abc import ABC, abstractmethod
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from svg_parser_module import get_change_point_db_connection


# ============================================================================
# Hypothesis 추상 클래스
# ============================================================================

class Hypothesis(ABC):
    """시뮬레이션 가설 추상 베이스 클래스"""

    @abstractmethod
    def predict(self, grid_string, position, window_sizes, method, threshold, **kwargs):
        pass

    @abstractmethod
    def get_name(self):
        pass

    @abstractmethod
    def get_description(self):
        pass

    def get_config_schema(self):
        return {}


# ============================================================================
# 윈도우 9 빈도+승률+빈도수550 가설
# ============================================================================

class FirstAnchorWindow9Freq518Win50Freq550Hypothesis(Hypothesis):
    """
    윈도우 9만 검증. 첫 앵커부터 순차.
    빈도 신뢰도 ≥ 51.8%, 시뮬레이션 승률 ≥ 50%, **prefix 전체 빈도수 > 550**일 때만 예측 사용;
    앵커 중첩 시 이전 앵커만 검증.
    """
    WINDOW_SIZES = (9,)

    def __init__(self):
        pass

    def predict(self, grid_string, position, window_sizes, method, threshold, **kwargs):
        """이 가설은 검증 함수에서 직접 simulation_predictions_change_point를 조회하므로 predict는 사용하지 않음."""
        return {
            "predicted": None,
            "confidence": 0.0,
            "window_size": None,
            "prefix": None,
            "all_predictions": [],
            "skipped": True,
        }

    def get_name(self):
        return "윈도우9 빈도+승률+빈도수550"

    def get_description(self):
        return (
            "윈도우 9만 검증. 첫 앵커부터 순차. "
            "빈도 신뢰도 ≥ 51.8% 및 시뮬레이션 승률 ≥ 50% 및 **prefix 전체 빈도수 > 550**일 때만 예측 사용; "
            "한쪽이라도 불만족 시 스킵. 앵커 중첩 시 이전 앵커만 검증. "
            "validate_first_anchor_window9_freq518_win50_freq550_cp 사용."
        )

    def get_config_schema(self):
        return {}


# ============================================================================
# 윈도우 9 전용 (빈도 51.8% + 승률 50% + prefix 빈도수 > 550) 검증
# ============================================================================

def validate_first_anchor_window9_freq518_win50_freq550_cp(
    grid_string_id,
    cutoff_grid_string_id,
    threshold=0,
    stop_on_match=False,
    predictions_conn=None,
    min_confidence_freq=51.8,
    min_win_rate_pct=50,
    min_prefix_freq=550,
):
    """
    윈도우 9만 검증. 첫 앵커부터 순차, validated_positions로 동일 position 중복 검증 방지.
    예측 사용 조건:
      - 빈도 기반 신뢰도 ≥ min_confidence_freq(기본 51.8%)
      - 시뮬레이션 승률(sim_win_rate_pct) ≥ min_win_rate_pct(기본 50%)
      - 해당 prefix의 전체 빈도수(pred_frequency) > min_prefix_freq(기본 550)
    한쪽이라도 불만족 시 스킵.
    """
    WINDOW_SIZES = (9,)
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
                    SELECT method, predicted_value, confidence, b_ratio, p_ratio, sim_win_rate_pct, pred_frequency
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
                pred_freq = freq_row.get("pred_frequency")

                if conf_freq < min_confidence_freq:
                    total_skipped += 1
                    skip_reason = f"신뢰도 부족 (빈도 {conf_freq:.1f}% < {min_confidence_freq}%)"
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
                        "skipped_prediction": freq_row["predicted_value"],
                    })
                    validated_positions.add(pos)
                    continue
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
                        "skipped_prediction": freq_row["predicted_value"],
                    })
                    validated_positions.add(pos)
                    continue
                if sim_wr < min_win_rate_pct:
                    total_skipped += 1
                    skip_reason = f"시뮬레이션 승률 부족 ({sim_wr:.1f}% < {min_win_rate_pct}%)"
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
                        "skipped_prediction": freq_row["predicted_value"],
                    })
                    validated_positions.add(pos)
                    continue

                # 새 조건: prefix 전체 빈도수 > min_prefix_freq (기본 550)
                try:
                    freq_val = float(pred_freq) if pred_freq is not None else 0.0
                except (TypeError, ValueError):
                    freq_val = 0.0
                if freq_val <= min_prefix_freq:
                    total_skipped += 1
                    skip_reason = f"prefix 빈도수 부족 ({freq_val:.0f} ≤ {min_prefix_freq})"
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
                        "skipped_prediction": freq_row["predicted_value"],
                    })
                    validated_positions.add(pos)
                    continue

                predicted = freq_row["predicted_value"]
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


def batch_validate_first_anchor_window9_freq518_win50_freq550_cp(
    cutoff_grid_string_id,
    threshold=0,
    stop_on_match=False,
    min_confidence_freq=51.8,
    min_win_rate_pct=50,
    min_prefix_freq=550,
    max_grid_string_id=None,
):
    """
    윈도우 9 (빈도 51.8% + 승률 50% + prefix 빈도수 > 550) 배치 검증.
    max_grid_string_id가 있으면 id > cutoff AND id <= max_grid_string_id 구간만 검증 (없으면 cutoff 이후 전부).
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
            r = validate_first_anchor_window9_freq518_win50_freq550_cp(
                gid,
                cutoff_grid_string_id,
                threshold=threshold,
                stop_on_match=stop_on_match,
                min_confidence_freq=min_confidence_freq,
                min_win_rate_pct=min_win_rate_pct,
                min_prefix_freq=min_prefix_freq,
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
