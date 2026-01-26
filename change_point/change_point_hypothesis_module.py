"""
Change-point 시뮬레이션 가설 모듈

- Hypothesis 추상 클래스 기반 가설 시스템
- 확장 가능한 가설 구조
- 가설 레지스트리 및 검증 함수
"""

import sys
from pathlib import Path
from abc import ABC, abstractmethod
import pandas as pd

# 상위 폴더의 모듈을 import하기 위해 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from svg_parser_module import get_change_point_db_connection


# ============================================================================
# Hypothesis 추상 클래스
# ============================================================================

class Hypothesis(ABC):
    """시뮬레이션 가설 추상 베이스 클래스"""
    
    @abstractmethod
    def predict(self, grid_string, position, window_sizes, method, threshold, **kwargs):
        """
        예측 수행
        
        Args:
            grid_string: 전체 grid string
            position: 예측할 위치
            window_sizes: 사용할 윈도우 크기 목록
            method: 예측 방법 ("빈도 기반", "가중치 기반", "안전 우선")
            threshold: 임계값
            **kwargs: 가설별 추가 파라미터
            
        Returns:
            dict: {
                "predicted": 예측값 (str 또는 None),
                "confidence": 신뢰도 (float),
                "window_size": 선택된 윈도우 크기 (int 또는 None),
                "prefix": 사용된 prefix (str 또는 None),
                "all_predictions": 모든 윈도우의 예측 목록 (list),
                "skipped": 스킵 여부 (bool, optional)
            }
        """
        pass
    
    @abstractmethod
    def get_name(self):
        """가설 이름 반환"""
        pass
    
    @abstractmethod
    def get_description(self):
        """가설 설명 반환"""
        pass
    
    def get_config_schema(self):
        """
        설정 파라미터 스키마 반환
        
        Returns:
            dict: {
                "param_name": {
                    "type": "number|text|select",
                    "label": "표시 이름",
                    "default": 기본값,
                    "min": 최소값 (number인 경우),
                    "max": 최대값 (number인 경우),
                    "step": 단계 (number인 경우),
                    "options": 옵션 목록 (select인 경우)
                }
            }
        """
        return {}


# ============================================================================
# 가설 레지스트리
# ============================================================================

HYPOTHESIS_REGISTRY = {}


def register_hypothesis(name, hypothesis_class):
    """가설을 레지스트리에 등록"""
    if not issubclass(hypothesis_class, Hypothesis):
        raise ValueError(f"{hypothesis_class}는 Hypothesis를 상속해야 합니다.")
    HYPOTHESIS_REGISTRY[name] = hypothesis_class


def get_hypothesis(name, **kwargs):
    """레지스트리에서 가설 인스턴스 생성"""
    if name not in HYPOTHESIS_REGISTRY:
        raise ValueError(f"가설 '{name}'이 레지스트리에 없습니다. 등록된 가설: {list(HYPOTHESIS_REGISTRY.keys())}")
    return HYPOTHESIS_REGISTRY[name](**kwargs)


def list_hypotheses():
    """등록된 가설 목록 반환"""
    return list(HYPOTHESIS_REGISTRY.keys())


# ============================================================================
# 기본 가설 구현
# ============================================================================

class BestConfidenceHypothesis(Hypothesis):
    """최고 신뢰도 선택 가설 - 여러 윈도우 중 최고 신뢰도 예측 선택"""
    
    def __init__(self):
        pass
    
    def predict(self, grid_string, position, window_sizes, method, threshold, **kwargs):
        """여러 윈도우 크기 중 최고 신뢰도 예측값 선택"""
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
                    "skipped": False,
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
    
    def get_name(self):
        return "최고 신뢰도 선택"
    
    def get_description(self):
        return "여러 윈도우 크기 중 최고 신뢰도를 가진 예측값을 선택합니다."
    
    def get_config_schema(self):
        return {}


class ConfidenceSkipHypothesis(Hypothesis):
    """신뢰도 스킵 가설 - 낮은 신뢰도 예측은 스킵"""
    
    def __init__(self, confidence_skip_threshold=52.0):
        self.confidence_skip_threshold = confidence_skip_threshold
    
    def predict(self, grid_string, position, window_sizes, method, threshold, **kwargs):
        """다중 윈도우 + 신뢰도 스킵. confidence < confidence_skip_threshold 이면 스킵."""
        # kwargs에서 confidence_skip_threshold를 가져올 수 있음 (우선순위 높음)
        skip_threshold = kwargs.get("confidence_skip_threshold", self.confidence_skip_threshold)
        
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
                if skip_threshold is not None and conf < skip_threshold:
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
    
    def get_name(self):
        return "신뢰도 스킵"
    
    def get_description(self):
        return "낮은 신뢰도 예측은 스킵하고, 남은 예측 중 최고 신뢰도를 선택합니다."
    
    def get_config_schema(self):
        return {
            "confidence_skip_threshold": {
                "type": "number",
                "label": "신뢰도 스킵 임계값 (%)",
                "default": 52.0,
                "min": 0.0,
                "max": 100.0,
                "step": 0.5,
            }
        }


class LargeWindowOnlyHypothesis(Hypothesis):
    """큰 윈도우만 사용 가설 - 첫 번째 앵커에서 윈도우 크기 8, 9, 10, 11, 12 모두 검증"""
    
    def __init__(self):
        pass
    
    def predict(self, grid_string, position, window_sizes, method, threshold, **kwargs):
        """
        첫 번째 앵커에서 윈도우 크기 8, 9, 10, 11, 12 모두 사용하여 예측
        검증 함수에서 첫 번째 앵커에서 각 윈도우 크기별로 호출되므로,
        여기서는 해당 position에서 사용 가능한 모든 큰 윈도우의 예측값을 조회하여 최고 신뢰도 선택
        """
        # 큰 윈도우만 필터링 (8 이상)
        large_windows = [w for w in window_sizes if w >= 8]
        if not large_windows:
            return {
                "predicted": None,
                "confidence": 0.0,
                "window_size": None,
                "prefix": None,
                "all_predictions": [],
                "skipped": False,
            }
        
        conn = get_change_point_db_connection()
        try:
            all_predictions = []
            for window_size in large_windows:
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
                    "skipped": False,
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
    
    def get_name(self):
        return "첫 앵커 큰 윈도우 검증 (8-12)"
    
    def get_description(self):
        return "첫 번째 앵커에서 윈도우 크기 8, 9, 10, 11, 12를 모두 검증하여 5회 연속 실패가 발생하는지 테스트합니다."
    
    def get_config_schema(self):
        return {}


class ThresholdSkipAnchorPriorityHypothesis(Hypothesis):
    """임계점 스킵 + 앵커 우선순위 가설 - 임계점 미만 스킵, 앵커 중첩 시 이전 앵커 우선"""
    
    def __init__(self):
        pass
    
    def predict(self, grid_string, position, window_sizes, method, threshold, anchor=None, window_thresholds=None, **kwargs):
        """
        특정 앵커와 윈도우 크기로 예측 수행
        confidence >= threshold인 경우만 반환, 그렇지 않으면 스킵
        
        Args:
            anchor: 사용할 앵커 위치 (position 계산에 사용)
            window_thresholds: 윈도우 크기별 임계값 딕셔너리 {window_size: threshold}
        """
        if anchor is None:
            return {
                "predicted": None,
                "confidence": 0.0,
                "window_size": None,
                "prefix": None,
                "all_predictions": [],
                "skipped": True,
            }
        
        # window_thresholds가 있으면 사용, 없으면 threshold를 모든 윈도우에 적용
        if window_thresholds is None:
            window_thresholds = {ws: threshold for ws in window_sizes}
        
        conn = get_change_point_db_connection()
        try:
            all_predictions = []
            all_attempts_debug = []  # 디버깅용: 모든 시도 기록
            for window_size in window_sizes:
                # 해당 윈도우 크기의 임계값 가져오기
                ws_threshold = window_thresholds.get(window_size, threshold)
                
                # 예측할 위치는 anchor + window_size - 1
                expected_pos = anchor + window_size - 1
                if expected_pos != position:
                    continue
                
                # prefix 계산: position에서 prefix_len만큼 앞부분 추출
                prefix_len = window_size - 1
                # position이 grid_string 길이와 같을 때 (다음 예측할 포지션)
                # prefix는 grid_string[position - prefix_len : position]
                # 이것이 유효하려면 position >= prefix_len이어야 함
                if position < prefix_len:
                    all_attempts_debug.append({
                        "window_size": window_size,
                        "prefix": None,
                        "predicted": None,
                        "confidence": 0.0,
                        "reason": "prefix 길이 부족"
                    })
                    continue
                
                # prefix 추출 가능 여부 확인
                if position > len(grid_string):
                    all_attempts_debug.append({
                        "window_size": window_size,
                        "prefix": None,
                        "predicted": None,
                        "confidence": 0.0,
                        "reason": "position 범위 초과"
                    })
                    continue
                
                prefix = grid_string[position - prefix_len : position]
                q = """
                    SELECT predicted_value, confidence, b_ratio, p_ratio
                    FROM stored_predictions_change_point
                    WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                    LIMIT 1
                """
                # threshold는 DB에 저장된 예측값의 threshold (일반적으로 0)
                # 실제 임계값 비교는 confidence와 ws_threshold로 수행
                df = pd.read_sql_query(q, conn, params=[window_size, prefix, method, 0])
                if len(df) == 0:
                    all_attempts_debug.append({
                        "window_size": window_size,
                        "prefix": prefix,
                        "predicted": None,
                        "confidence": 0.0,
                        "reason": "DB에 예측값 없음"
                    })
                    continue
                row = df.iloc[0]
                conf = row["confidence"]
                
                # 윈도우별 임계점 미만이면 스킵
                if conf < ws_threshold:
                    all_attempts_debug.append({
                        "window_size": window_size,
                        "prefix": prefix,
                        "predicted": row["predicted_value"],
                        "confidence": conf,
                        "reason": f"임계값 미만 ({conf:.1f}% < {ws_threshold}%)"
                    })
                    continue
                
                all_predictions.append({
                    "window_size": window_size,
                    "prefix": prefix,
                    "predicted": row["predicted_value"],
                    "confidence": conf,
                    "b_ratio": row["b_ratio"],
                    "p_ratio": row["p_ratio"],
                })
                all_attempts_debug.append({
                    "window_size": window_size,
                    "prefix": prefix,
                    "predicted": row["predicted_value"],
                    "confidence": conf,
                    "reason": "성공"
                })
            
            if not all_predictions:
                return {
                    "predicted": None,
                    "confidence": 0.0,
                    "window_size": None,
                    "prefix": None,
                    "all_predictions": [],
                    "all_attempts_debug": all_attempts_debug,  # 디버깅 정보 추가
                    "skipped": True,
                }
            
            # 최고 신뢰도 선택
            best = max(all_predictions, key=lambda x: x["confidence"])
            return {
                "predicted": best["predicted"],
                "confidence": best["confidence"],
                "window_size": best["window_size"],
                "prefix": best["prefix"],
                "all_predictions": all_predictions,
                "all_attempts_debug": all_attempts_debug,  # 디버깅 정보 추가
                "skipped": False,
            }
        finally:
            conn.close()
    
    def get_name(self):
        return "임계점 스킵 + 앵커 우선순위"
    
    def get_description(self):
        return "임계점 미만 예측은 스킵하고, 앵커가 중첩되는 경우 이전 앵커를 우선 검증합니다. 이전 앵커가 스킵되면 예측값이 있는 다음 앵커로 검증합니다."
    
    def get_config_schema(self):
        return {}


# ============================================================================
# 가설 등록
# ============================================================================

register_hypothesis("best_confidence", BestConfidenceHypothesis)
register_hypothesis("confidence_skip", ConfidenceSkipHypothesis)
register_hypothesis("large_window_only", LargeWindowOnlyHypothesis)
register_hypothesis("threshold_skip_anchor_priority", ThresholdSkipAnchorPriorityHypothesis)


# ============================================================================
# 검증 함수
# ============================================================================

def validate_hypothesis_cp(
    grid_string_id,
    cutoff_grid_string_id,
    hypothesis,
    window_sizes=(5, 6, 7, 8, 9),
    method="빈도 기반",
    threshold=0,
    **hypothesis_params
):
    """
    가설 기반 단일 grid_string 검증
    
    Args:
        grid_string_id: 검증할 grid_string ID
        cutoff_grid_string_id: cutoff ID (사용하지 않지만 호환성을 위해 유지)
        hypothesis: Hypothesis 인스턴스
        window_sizes: 윈도우 크기 목록
        method: 예측 방법
        threshold: 임계값
        **hypothesis_params: 가설별 추가 파라미터
        
    Returns:
        dict: 검증 결과
    """
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
        
        # Change-point Detection: 앵커 위치 수집
        anchors = []
        for i in range(len(grid_string) - 1):
            if grid_string[i] != grid_string[i+1]:
                # 변화점 감지 시 이전 위치(i)를 앵커로 추가
                anchors.append(i)
        anchors = sorted(list(set(anchors)))
        
        # 첫 번째 앵커만 사용 (가설에 따라)
        # LargeWindowOnlyHypothesis는 첫 번째 앵커에서만 윈도우 크기 8,9,10,11,12 모두 검증
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
            }
        
        # 첫 번째 앵커만 사용
        first_anchor = anchors[0]
        anchors_to_use = [first_anchor]
        
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0
        
        # 첫 번째 앵커에서 각 윈도우 크기별로 예측 수행
        # 앵커는 고정된 상태에서 윈도우 크기 8,9,10,11,12 모두 검증
        for anchor in anchors_to_use:
            # 큰 윈도우만 필터링 (8 이상)
            large_windows = [w for w in window_sizes if w >= 8]
            for window_size in large_windows:
                # 앵커 위치에서 window_size만큼 추출 가능한지 확인
                if anchor + window_size > len(grid_string):
                    continue
                
                # 예측할 위치 (suffix 위치)
                pos = anchor + window_size - 1
                total_steps += 1
                actual = grid_string[pos]
                
                # 가설을 사용하여 예측 (해당 앵커에서 사용 가능한 윈도우 크기만 전달)
                pred_res = hypothesis.predict(
                    grid_string, pos, window_sizes=large_windows, 
                    method=method, threshold=threshold, **hypothesis_params
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
                        "anchor": anchor,
                        "window_size": window_size,
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
                        "anchor": anchor,
                        "window_size": window_size,
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


def batch_validate_hypothesis_cp(
    cutoff_grid_string_id,
    hypothesis,
    window_sizes=(5, 6, 7, 8, 9),
    method="빈도 기반",
    threshold=0,
    **hypothesis_params
):
    """
    가설 기반 배치 검증 (cutoff 이후 grid_string)
    
    Args:
        cutoff_grid_string_id: cutoff ID (이 ID 이후 검증)
        hypothesis: Hypothesis 인스턴스
        window_sizes: 윈도우 크기 목록
        method: 예측 방법
        threshold: 임계값
        **hypothesis_params: 가설별 추가 파라미터
        
    Returns:
        dict: {
            "results": 검증 결과 목록,
            "summary": 요약 통계,
            "grid_string_ids": 검증된 grid_string ID 목록
        }
    """
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
            r = validate_hypothesis_cp(
                gid, cutoff_grid_string_id,
                hypothesis=hypothesis,
                window_sizes=window_sizes, 
                method=method, 
                threshold=threshold,
                **hypothesis_params
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


def validate_threshold_skip_anchor_priority_cp(
    grid_string_id,
    cutoff_grid_string_id,
    window_sizes=(8, 9, 10, 11, 12),
    method="빈도 기반",
    threshold=50,
    window_thresholds=None,
):
    """
    임계점 스킵 + 앵커 우선순위 검증 함수
    
    - 모든 앵커에서 검증
    - 각 position에서 예측할 때, 가능한 모든 앵커를 이전 앵커부터 순서대로 시도
    - 이전 앵커가 스킵되면 예측값이 있는 다음 앵커로 검증
    
    Args:
        grid_string_id: 검증할 grid_string ID
        cutoff_grid_string_id: cutoff ID (사용하지 않지만 호환성을 위해 유지)
        window_sizes: 윈도우 크기 목록
        method: 예측 방법
        threshold: 기본 임계값 (window_thresholds가 없을 때 사용)
        window_thresholds: 윈도우 크기별 임계값 딕셔너리 {window_size: threshold}
        
    Returns:
        dict: 검증 결과
    """
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
        
        # Change-point Detection: 앵커 위치 수집
        anchors = []
        for i in range(len(grid_string) - 1):
            if grid_string[i] != grid_string[i+1]:
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
            }
        
        hypothesis = ThresholdSkipAnchorPriorityHypothesis()
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0
        
        # 각 position에서 예측 시도 (max_ws부터 끝까지)
        for position in range(max_ws - 1, len(grid_string)):
            actual = grid_string[position]
            total_steps += 1
            
            # 해당 position에 도달할 수 있는 모든 앵커 찾기
            # anchor + window_size - 1 == position이 되는 앵커들
            possible_anchors = []
            for anchor in anchors:
                for window_size in window_sizes:
                    if anchor + window_size - 1 == position:
                        if anchor not in possible_anchors:
                            possible_anchors.append(anchor)
                        break
            
            # 앵커를 이전 앵커부터 순서대로 시도
            possible_anchors = sorted(possible_anchors)
            
            pred_result = None
            used_anchor = None
            used_window_size = None
            used_prefix = None
            all_attempts = []
            
            # 각 앵커를 순서대로 시도
            for anchor in possible_anchors:
                pred_res = hypothesis.predict(
                    grid_string, position, window_sizes=window_sizes,
                    method=method, threshold=threshold, anchor=anchor,
                    window_thresholds=window_thresholds
                )
                
                all_attempts.append({
                    "anchor": anchor,
                    "skipped": pred_res.get("skipped", False),
                    "confidence": pred_res.get("confidence", 0.0),
                    "predicted": pred_res.get("predicted"),
                    "window_size": pred_res.get("window_size"),
                    "all_predictions": pred_res.get("all_predictions", []),
                })
                
                # 예측값이 있고 스킵되지 않았으면 사용
                if pred_res.get("predicted") is not None and not pred_res.get("skipped", False):
                    pred_result = pred_res
                    used_anchor = anchor
                    used_window_size = pred_res.get("window_size")
                    used_prefix = pred_res.get("prefix")
                    break  # 예측 성공, 다음 position으로
            
            # 예측 결과 처리
            if pred_result and pred_result.get("predicted") is not None:
                pred = pred_result.get("predicted")
                conf = pred_result.get("confidence", 0.0)
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
                    "position": position,
                    "anchor": used_anchor,
                    "window_size": used_window_size,
                    "prefix": used_prefix,
                    "predicted": pred,
                    "actual": actual,
                    "is_correct": ok,
                    "confidence": conf,
                    "selected_window_size": used_window_size,
                    "all_predictions": pred_result.get("all_predictions", []),
                    "skipped": False,
                    "all_anchor_attempts": all_attempts,
                })
            else:
                # 모든 앵커가 스킵됨
                total_skipped += 1
                history.append({
                    "step": total_steps,
                    "position": position,
                    "anchor": None,
                    "window_size": None,
                    "prefix": None,
                    "predicted": None,
                    "actual": actual,
                    "is_correct": None,
                    "confidence": 0.0,
                    "selected_window_size": None,
                    "all_predictions": [],
                    "skipped": True,
                    "all_anchor_attempts": all_attempts,
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


def batch_validate_threshold_skip_anchor_priority_cp(
    cutoff_grid_string_id,
    window_sizes=(8, 9, 10, 11, 12),
    method="빈도 기반",
    threshold=50,
    window_thresholds=None,
):
    """
    임계점 스킵 + 앵커 우선순위 배치 검증 (cutoff 이후 grid_string)
    
    Args:
        cutoff_grid_string_id: cutoff ID (이 ID 이후 검증)
        window_sizes: 윈도우 크기 목록
        method: 예측 방법
        threshold: 기본 임계값 (window_thresholds가 없을 때 사용)
        window_thresholds: 윈도우 크기별 임계값 딕셔너리 {window_size: threshold}
        
    Returns:
        dict: {
            "results": 검증 결과 목록,
            "summary": 요약 통계,
            "grid_string_ids": 검증된 grid_string ID 목록
        }
    """
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
            res = validate_threshold_skip_anchor_priority_cp(
                gid, cutoff_grid_string_id, window_sizes, method, threshold, window_thresholds
            )
            if res:
                results.append(res)
        
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
            summary = {
                "total_grid_strings": len(results),
                "avg_accuracy": sum(x["accuracy"] for x in results) / len(results),
                "max_consecutive_failures": max(x["max_consecutive_failures"] for x in results),
                "avg_max_consecutive_failures": sum(x["max_consecutive_failures"] for x in results) / len(results),
                "total_steps": sum(x["total_steps"] for x in results),
                "total_failures": sum(x["total_failures"] for x in results),
                "total_predictions": sum(x["total_predictions"] for x in results),
                "total_skipped": sum(x.get("total_skipped", 0) for x in results),
            }
        return {"results": results, "summary": summary, "grid_string_ids": gids}
    finally:
        conn.close()
