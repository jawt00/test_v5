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


class FirstAnchorExtendedWindowHypothesis(Hypothesis):
    """첫 앵커 확장 윈도우 가설 - 첫 번째 앵커에서 윈도우 크기 9, 10, 11, 12, 13, 14를 신뢰도 기반 검증"""
    
    def __init__(self):
        pass
    
    def predict(self, grid_string, position, window_sizes, method, threshold, **kwargs):
        """
        첫 번째 앵커에서 윈도우 크기 9, 10, 11, 12, 13, 14를 사용하여 예측
        검증 함수에서 첫 번째 앵커에서 각 윈도우 크기별로 호출되므로,
        여기서는 해당 position에서 사용 가능한 모든 확장 윈도우의 예측값을 조회하여 최고 신뢰도 선택
        """
        # 확장 윈도우만 필터링 (9 이상 14 이하)
        extended_windows = [w for w in window_sizes if 9 <= w <= 14]
        if not extended_windows:
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
            for window_size in extended_windows:
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
            # 신뢰도 기반으로 최고 신뢰도 선택
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
        return "첫 앵커 확장 윈도우 검증 (9-14)"
    
    def get_description(self):
        return "첫 번째 앵커에서 윈도우 크기 9, 10, 11, 12, 13, 14를 신뢰도 기반으로 검증합니다."
    
    def get_config_schema(self):
        return {}


class FirstAnchorExtendedWindowHypothesisV2(Hypothesis):
    """첫 앵커 확장 윈도우 가설 V2 - 첫 번째 앵커에서 윈도우 크기 9, 10, 11, 12, 13, 14를 신뢰도 기반 검증 (독립 구현)"""
    
    def __init__(self):
        pass
    
    def predict(self, grid_string, position, window_sizes, method, threshold, **kwargs):
        """
        첫 번째 앵커에서 윈도우 크기 9, 10, 11, 12, 13, 14를 사용하여 예측
        신뢰도 기반으로 최고 신뢰도 예측값 선택
        """
        # 확장 윈도우만 필터링 (9 이상 14 이하)
        extended_windows = [w for w in window_sizes if 9 <= w <= 14]
        if not extended_windows:
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
            for window_size in extended_windows:
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
            # 신뢰도 기반으로 최고 신뢰도 선택
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
        return "첫 앵커 확장 윈도우 검증 V2 (9-14)"
    
    def get_description(self):
        return "첫 번째 앵커에서 윈도우 크기 9, 10, 11, 12, 13, 14를 신뢰도 기반으로 검증합니다. (독립 구현)"
    
    def get_config_schema(self):
        return {}


class FirstAnchorExtendedWindowHypothesisV3(Hypothesis):
    """첫 앵커 확장 윈도우 가설 V3 - 첫 번째 앵커에서 윈도우 크기 9, 10, 11, 12, 13, 14를 신뢰도 기반 검증 (V2 복제)"""
    
    def __init__(self):
        pass
    
    def predict(self, grid_string, position, window_sizes, method, threshold, **kwargs):
        """
        첫 번째 앵커에서 윈도우 크기 9, 10, 11, 12, 13, 14를 사용하여 예측
        신뢰도 기반으로 최고 신뢰도 예측값 선택
        """
        # 확장 윈도우만 필터링 (9 이상 14 이하)
        extended_windows = [w for w in window_sizes if 9 <= w <= 14]
        if not extended_windows:
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
            for window_size in extended_windows:
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
            # 신뢰도 기반으로 최고 신뢰도 선택
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
        return "첫 앵커 확장 윈도우 검증 V3 (9-14)"
    
    def get_description(self):
        return "첫 번째 앵커에서 윈도우 크기 9, 10, 11, 12, 13, 14를 신뢰도 기반으로 검증합니다. (V2 복제, 수정 가능)"
    
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
            
            # 가장 큰 윈도우 크기 선택 (문서 요구사항: 같은 앵커 내에서는 큰 윈도우 우선)
            best = max(all_predictions, key=lambda x: x["window_size"])
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


class ThresholdSkipAnchorPriorityExtendedHypothesis(Hypothesis):
    """임계점 스킵 + 앵커 우선순위 확장 가설 - 기본 가설에 추가 조건 적용 가능"""
    
    def __init__(self, additional_condition=None):
        """
        Args:
            additional_condition: 추가 조건 함수 (prediction_dict, actual_value) -> bool
                                  True를 반환하면 예측을 사용, False면 스킵
        """
        self.additional_condition = additional_condition
    
    def predict(self, grid_string, position, window_sizes, method, threshold, anchor=None, window_thresholds=None, **kwargs):
        """
        특정 앵커와 윈도우 크기로 예측 수행
        confidence >= threshold인 경우만 반환, 그렇지 않으면 스킵
        추가 조건이 있으면 해당 조건도 확인
        
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
                if position < prefix_len:
                    all_attempts_debug.append({
                        "window_size": window_size,
                        "prefix": None,
                        "predicted": None,
                        "confidence": 0.0,
                        "reason": "prefix 길이 부족"
                    })
                    continue
                
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
                
                # 추가 조건 확인
                prediction_dict = {
                    "window_size": window_size,
                    "prefix": prefix,
                    "predicted": row["predicted_value"],
                    "confidence": conf,
                    "b_ratio": row["b_ratio"],
                    "p_ratio": row["p_ratio"],
                }
                
                # 추가 조건이 있고 조건을 만족하지 않으면 스킵
                if self.additional_condition is not None:
                    actual_value = grid_string[position] if position < len(grid_string) else None
                    if not self.additional_condition(prediction_dict, actual_value):
                        all_attempts_debug.append({
                            "window_size": window_size,
                            "prefix": prefix,
                            "predicted": row["predicted_value"],
                            "confidence": conf,
                            "reason": "추가 조건 불만족"
                        })
                        continue
                
                all_predictions.append(prediction_dict)
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
                    "all_attempts_debug": all_attempts_debug,
                    "skipped": True,
                }
            
            # 가장 큰 윈도우 크기 선택
            best = max(all_predictions, key=lambda x: x["window_size"])
            return {
                "predicted": best["predicted"],
                "confidence": best["confidence"],
                "window_size": best["window_size"],
                "prefix": best["prefix"],
                "all_predictions": all_predictions,
                "all_attempts_debug": all_attempts_debug,
                "skipped": False,
            }
        finally:
            conn.close()
    
    def get_name(self):
        return "임계점 스킵 + 앵커 우선순위 (확장)"
    
    def get_description(self):
        return "임계점 스킵 + 앵커 우선순위 가설에 추가 조건을 적용할 수 있는 확장 버전입니다."
    
    def get_config_schema(self):
        return {}


# ============================================================================
# 가설 등록
# ============================================================================

register_hypothesis("best_confidence", BestConfidenceHypothesis)
register_hypothesis("confidence_skip", ConfidenceSkipHypothesis)
register_hypothesis("large_window_only", LargeWindowOnlyHypothesis)
register_hypothesis("first_anchor_extended_window", FirstAnchorExtendedWindowHypothesis)
register_hypothesis("first_anchor_extended_window_v2", FirstAnchorExtendedWindowHypothesisV2)
register_hypothesis("first_anchor_extended_window_v3", FirstAnchorExtendedWindowHypothesisV3)
register_hypothesis("threshold_skip_anchor_priority", ThresholdSkipAnchorPriorityHypothesis)
register_hypothesis("threshold_skip_anchor_priority_extended", ThresholdSkipAnchorPriorityExtendedHypothesis)


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
    stop_on_match=False,
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
        stop_on_match: True이면 일치하는 결과가 나오면 검증 종료
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
                "stopped_early": False,
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
                "stopped_early": False,
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
        stopped_early = False
        
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
                    
                    # 일치하는 결과가 나오면 검증 종료
                    if stop_on_match and ok:
                        stopped_early = True
                        break
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
            
            # early exit 체크
            if stopped_early:
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


def batch_validate_hypothesis_cp(
    cutoff_grid_string_id,
    hypothesis,
    window_sizes=(5, 6, 7, 8, 9),
    method="빈도 기반",
    threshold=0,
    train_ratio=None,
    stop_on_match=False,
    **hypothesis_params
):
    """
    가설 기반 배치 검증 (cutoff 이후 grid_string)
    
    Args:
        cutoff_grid_string_id: cutoff ID (이 ID 이전 = 학습 데이터, 이후 = 검증 데이터)
        hypothesis: Hypothesis 인스턴스
        window_sizes: 윈도우 크기 목록
        method: 예측 방법
        threshold: 임계값
        train_ratio: 사용하지 않음 (호환성을 위해 유지, 무시됨)
        stop_on_match: True이면 각 grid_string 검증 중 일치하는 결과가 나오면 종료
        **hypothesis_params: 가설별 추가 파라미터
        
    Returns:
        dict: {
            "results": 검증 결과 목록,
            "summary": 요약 통계,
            "grid_string_ids": 검증된 grid_string ID 목록,
            "train_grid_string_ids": 학습용 grid_string ID 목록 (cutoff 이전)
        }
    """
    conn = get_change_point_db_connection()
    try:
        # cutoff 이후의 모든 grid_string ID 조회 (검증 데이터)
        df_test = pd.read_sql_query(
            "SELECT id FROM preprocessed_grid_strings WHERE id > ? ORDER BY id",
            conn,
            params=[cutoff_grid_string_id],
        )
        
        # cutoff 이전의 모든 grid_string ID 조회 (학습 데이터)
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
        
        # cutoff 이후의 모든 데이터를 검증 (train_ratio 무시)
        test_gids = df_test["id"].tolist()
        train_gids = df_train["id"].tolist() if len(df_train) > 0 else []
        
        results = []
        for gid in test_gids:
            r = validate_hypothesis_cp(
                gid, cutoff_grid_string_id,
                hypothesis=hypothesis,
                window_sizes=window_sizes, 
                method=method, 
                threshold=threshold,
                stop_on_match=stop_on_match,
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


def validate_threshold_skip_anchor_priority_cp(
    grid_string_id,
    cutoff_grid_string_id,
    window_sizes=(8, 9, 10, 11, 12),
    method="빈도 기반",
    threshold=50,
    window_thresholds=None,
    stop_on_match=False,
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
        stop_on_match: True이면 일치하는 결과가 나오면 검증 종료
        
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
                "stopped_early": False,
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
                "stopped_early": False,
            }
        
        hypothesis = ThresholdSkipAnchorPriorityHypothesis()
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0
        stopped_early = False
        
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
                
                # 일치하는 결과가 나오면 검증 종료
                if stop_on_match and ok:
                    stopped_early = True
                    break
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
            
            # early exit 체크
            if stopped_early:
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


def validate_threshold_skip_anchor_priority_extended_cp(
    grid_string_id,
    cutoff_grid_string_id,
    window_sizes=(8, 9, 10, 11, 12),
    method="빈도 기반",
    threshold=50,
    window_thresholds=None,
    stop_on_match=False,
    additional_condition=None,
):
    """
    임계점 스킵 + 앵커 우선순위 확장 검증 함수
    
    - 모든 앵커에서 검증
    - 각 position에서 예측할 때, 가능한 모든 앵커를 이전 앵커부터 순서대로 시도
    - 이전 앵커가 스킵되면 예측값이 있는 다음 앵커로 검증
    - 추가 조건 적용 가능
    
    Args:
        grid_string_id: 검증할 grid_string ID
        cutoff_grid_string_id: cutoff ID (사용하지 않지만 호환성을 위해 유지)
        window_sizes: 윈도우 크기 목록
        method: 예측 방법
        threshold: 기본 임계값 (window_thresholds가 없을 때 사용)
        window_thresholds: 윈도우 크기별 임계값 딕셔너리 {window_size: threshold}
        stop_on_match: True이면 일치하는 결과가 나오면 검증 종료
        additional_condition: 추가 조건 함수 (prediction_dict, actual_value) -> bool
        
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
                "stopped_early": False,
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
                "stopped_early": False,
            }
        
        hypothesis = ThresholdSkipAnchorPriorityExtendedHypothesis(additional_condition=additional_condition)
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0
        stopped_early = False
        
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
                
                # 일치하는 결과가 나오면 검증 종료
                if stop_on_match and ok:
                    stopped_early = True
                    break
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
            
            # early exit 체크
            if stopped_early:
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


def batch_validate_threshold_skip_anchor_priority_cp(
    cutoff_grid_string_id,
    window_sizes=(8, 9, 10, 11, 12),
    method="빈도 기반",
    threshold=50,
    window_thresholds=None,
    train_ratio=None,
    stop_on_match=False,
):
    """
    임계점 스킵 + 앵커 우선순위 배치 검증 (cutoff 이후 grid_string)
    
    Args:
        cutoff_grid_string_id: cutoff ID (이 ID 이전 = 학습 데이터, 이후 = 검증 데이터)
        window_sizes: 윈도우 크기 목록
        method: 예측 방법
        threshold: 기본 임계값 (window_thresholds가 없을 때 사용)
        window_thresholds: 윈도우 크기별 임계값 딕셔너리 {window_size: threshold}
        train_ratio: 사용하지 않음 (호환성을 위해 유지, 무시됨)
        stop_on_match: True이면 각 grid_string 검증 중 일치하는 결과가 나오면 종료
        
    Returns:
        dict: {
            "results": 검증 결과 목록,
            "summary": 요약 통계,
            "grid_string_ids": 검증된 grid_string ID 목록,
            "train_grid_string_ids": 학습용 grid_string ID 목록 (cutoff 이전)
        }
    """
    conn = get_change_point_db_connection()
    try:
        # cutoff 이후의 모든 grid_string ID 조회 (검증 데이터)
        df_test = pd.read_sql_query(
            "SELECT id FROM preprocessed_grid_strings WHERE id > ? ORDER BY id",
            conn,
            params=[cutoff_grid_string_id],
        )
        
        # cutoff 이전의 모든 grid_string ID 조회 (학습 데이터)
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
        
        # cutoff 이후의 모든 데이터를 검증 (train_ratio 무시)
        test_gids = df_test["id"].tolist()
        train_gids = df_train["id"].tolist() if len(df_train) > 0 else []
        
        results = []
        for gid in test_gids:
            res = validate_threshold_skip_anchor_priority_cp(
                gid, cutoff_grid_string_id, window_sizes, method, threshold, window_thresholds, stop_on_match
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


def batch_validate_threshold_skip_anchor_priority_extended_cp(
    cutoff_grid_string_id,
    window_sizes=(8, 9, 10, 11, 12),
    method="빈도 기반",
    threshold=50,
    window_thresholds=None,
    train_ratio=None,
    stop_on_match=False,
    additional_condition=None,
):
    """
    임계점 스킵 + 앵커 우선순위 확장 배치 검증 (cutoff 이후 grid_string)
    
    Args:
        cutoff_grid_string_id: cutoff ID (이 ID 이전 = 학습 데이터, 이후 = 검증 데이터)
        window_sizes: 윈도우 크기 목록
        method: 예측 방법
        threshold: 기본 임계값 (window_thresholds가 없을 때 사용)
        window_thresholds: 윈도우 크기별 임계값 딕셔너리 {window_size: threshold}
        train_ratio: 사용하지 않음 (호환성을 위해 유지, 무시됨)
        stop_on_match: True이면 각 grid_string 검증 중 일치하는 결과가 나오면 종료
        additional_condition: 추가 조건 함수 (prediction_dict, actual_value) -> bool
        
    Returns:
        dict: {
            "results": 검증 결과 목록,
            "summary": 요약 통계,
            "grid_string_ids": 검증된 grid_string ID 목록,
            "train_grid_string_ids": 학습용 grid_string ID 목록 (cutoff 이전)
        }
    """
    conn = get_change_point_db_connection()
    try:
        # cutoff 이후의 모든 grid_string ID 조회 (검증 데이터)
        df_test = pd.read_sql_query(
            "SELECT id FROM preprocessed_grid_strings WHERE id > ? ORDER BY id",
            conn,
            params=[cutoff_grid_string_id],
        )
        
        # cutoff 이전의 모든 grid_string ID 조회 (학습 데이터)
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
        
        # cutoff 이후의 모든 데이터를 검증 (train_ratio 무시)
        test_gids = df_test["id"].tolist()
        train_gids = df_train["id"].tolist() if len(df_train) > 0 else []
        
        results = []
        for gid in test_gids:
            res = validate_threshold_skip_anchor_priority_extended_cp(
                gid, cutoff_grid_string_id, window_sizes, method, threshold, window_thresholds, stop_on_match, additional_condition
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


def batch_validate_multiple_train_ratios(
    cutoff_grid_string_id,
    hypothesis,
    window_sizes=(5, 6, 7, 8, 9),
    method="빈도 기반",
    threshold=0,
    start_train_ratio=0.6,
    step_train_ratio=0.05,
    max_train_ratio=0.95,
    stop_on_match=False,
    window_thresholds=None,
    additional_condition=None,
    **hypothesis_params
):
    """
    여러 학습 비율로 배치 검증 수행
    
    Args:
        cutoff_grid_string_id: cutoff ID (이 ID 이후 검증)
        hypothesis: Hypothesis 인스턴스 (또는 가설 이름 문자열)
        window_sizes: 윈도우 크기 목록
        method: 예측 방법
        threshold: 기본 임계값
        start_train_ratio: 시작 학습 비율 (예: 0.6 = 60%)
        step_train_ratio: 학습 비율 간격 (예: 0.05 = 5%)
        max_train_ratio: 최대 학습 비율 (예: 0.95 = 95%)
        stop_on_match: True이면 각 grid_string 검증 중 일치하는 결과가 나오면 종료
        window_thresholds: 윈도우 크기별 임계값 딕셔너리 {window_size: threshold} (threshold_skip 가설용)
        additional_condition: 추가 조건 함수 (확장 가설용)
        **hypothesis_params: 가설별 추가 파라미터
        
    Returns:
        dict: {
            "train_ratios": 학습 비율 목록,
            "results_by_ratio": {train_ratio: 검증 결과} 딕셔너리,
            "summary_by_ratio": {train_ratio: 요약 통계} 딕셔너리
        }
    """
    # 학습 비율 목록 생성
    train_ratios = []
    current_ratio = start_train_ratio
    while current_ratio <= max_train_ratio:
        train_ratios.append(round(current_ratio, 2))
        current_ratio += step_train_ratio
    
    results_by_ratio = {}
    summary_by_ratio = {}
    
    # 가설 이름이 문자열인 경우 인스턴스로 변환
    if isinstance(hypothesis, str):
        if hypothesis == "threshold_skip_anchor_priority_extended":
            hypothesis_instance = ThresholdSkipAnchorPriorityExtendedHypothesis(additional_condition=additional_condition)
        elif hypothesis == "threshold_skip_anchor_priority":
            hypothesis_instance = ThresholdSkipAnchorPriorityHypothesis()
        else:
            hypothesis_instance = get_hypothesis(hypothesis, **hypothesis_params)
    else:
        hypothesis_instance = hypothesis
    
    # 각 학습 비율에 대해 검증 수행
    for train_ratio in train_ratios:
        if isinstance(hypothesis_instance, ThresholdSkipAnchorPriorityHypothesis) and not isinstance(hypothesis_instance, ThresholdSkipAnchorPriorityExtendedHypothesis):
            res = batch_validate_threshold_skip_anchor_priority_cp(
                cutoff_grid_string_id,
                window_sizes=window_sizes,
                method=method,
                threshold=threshold,
                window_thresholds=window_thresholds,
                train_ratio=train_ratio,
                stop_on_match=stop_on_match,
            )
        elif isinstance(hypothesis_instance, ThresholdSkipAnchorPriorityExtendedHypothesis):
            res = batch_validate_threshold_skip_anchor_priority_extended_cp(
                cutoff_grid_string_id,
                window_sizes=window_sizes,
                method=method,
                threshold=threshold,
                window_thresholds=window_thresholds,
                train_ratio=train_ratio,
                stop_on_match=stop_on_match,
                additional_condition=additional_condition,
            )
        else:
            res = batch_validate_hypothesis_cp(
                cutoff_grid_string_id,
                hypothesis=hypothesis_instance,
                window_sizes=window_sizes,
                method=method,
                threshold=threshold,
                train_ratio=train_ratio,
                stop_on_match=stop_on_match,
                **hypothesis_params
            )
        
        results_by_ratio[train_ratio] = res
        summary_by_ratio[train_ratio] = res.get("summary", {})
    
    return {
        "train_ratios": train_ratios,
        "results_by_ratio": results_by_ratio,
        "summary_by_ratio": summary_by_ratio,
    }


# ============================================================================
# 첫 앵커 확장 윈도우 V2 검증 함수 (독립 구현)
# ============================================================================

def validate_first_anchor_extended_window_v2_cp(
    grid_string_id,
    cutoff_grid_string_id,
    window_sizes=(9, 10, 11, 12, 13, 14),
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
):
    """
    첫 앵커 확장 윈도우 V2 검증 함수 (독립 구현)
    
    - 첫 번째 앵커만 사용
    - 첫 번째 앵커에서 윈도우 크기별로 각각 검증
    - 각 윈도우 크기별 신뢰도 기반 예측값 비교
    
    Args:
        grid_string_id: 검증할 grid_string ID
        cutoff_grid_string_id: cutoff ID (사용하지 않지만 호환성을 위해 유지)
        window_sizes: 윈도우 크기 목록 (기본값: 9, 10, 11, 12, 13, 14)
        method: 예측 방법
        threshold: 임계값
        stop_on_match: True이면 일치하는 결과가 나오면 검증 종료
        
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
                "stopped_early": False,
            }
        
        # Change-point Detection: 앵커 위치 수집
        anchors = []
        for i in range(len(grid_string) - 1):
            if grid_string[i] != grid_string[i+1]:
                anchors.append(i)
        anchors = sorted(list(set(anchors)))
        
        # 첫 번째 앵커만 사용
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
        
        first_anchor = anchors[0]
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0
        stopped_early = False
        
        # 첫 번째 앵커에서 각 윈도우 크기별로 예측 수행
        for window_size in window_sizes:
            # 앵커 위치에서 window_size만큼 추출 가능한지 확인
            if first_anchor + window_size > len(grid_string):
                continue
            
            # 예측할 위치 (suffix 위치)
            pos = first_anchor + window_size - 1
            total_steps += 1
            actual = grid_string[pos]
            
            # prefix 계산
            prefix_len = window_size - 1
            prefix = grid_string[pos - prefix_len : pos]
            
            # DB에서 예측값 조회
            q = """
                SELECT predicted_value, confidence, b_ratio, p_ratio
                FROM stored_predictions_change_point
                WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                LIMIT 1
            """
            df_pred = pd.read_sql_query(q, conn, params=[window_size, prefix, method, threshold])
            
            if len(df_pred) == 0:
                # 예측값이 없으면 스킵 (예측 테이블에 값이 없음)
                total_skipped += 1
                history.append({
                    "step": total_steps,
                    "position": pos,
                    "anchor": first_anchor,
                    "window_size": window_size,
                    "prefix": prefix,
                    "predicted": None,
                    "actual": actual,
                    "is_correct": None,
                    "confidence": 0.0,
                    "selected_window_size": window_size,
                    "all_predictions": [],
                    "skipped": True,
                    "skip_reason": "예측 테이블에 값 없음",  # 스킵 사유 추가
                })
                continue
            
            row = df_pred.iloc[0]
            predicted = row["predicted_value"]
            confidence = row["confidence"]
            
            # 예측 결과 비교
            ok = predicted == actual
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
                "anchor": first_anchor,
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
                    "b_ratio": row["b_ratio"],
                    "p_ratio": row["p_ratio"],
                }],
                "skipped": False,
            })
            
            # 일치하는 결과가 나오면 검증 종료
            if stop_on_match and ok:
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


def batch_validate_first_anchor_extended_window_v2_cp(
    cutoff_grid_string_id,
    window_sizes=(9, 10, 11, 12, 13, 14),
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
    auto_generate_predictions=True,
):
    """
    첫 앵커 확장 윈도우 V2 배치 검증 (cutoff 이후 grid_string)
    
    **예측값 사용 방식:**
    1. cutoff 이전 데이터(학습 데이터)로만 예측값 테이블 생성
    2. 생성된 예측값으로 cutoff 이후 데이터(검증 데이터) 검증
    
    Args:
        cutoff_grid_string_id: cutoff ID (이 ID 이전 = 학습 데이터, 이후 = 검증 데이터)
        window_sizes: 윈도우 크기 목록 (기본값: 9, 10, 11, 12, 13, 14)
        method: 예측 방법
        threshold: 임계값 (예측값 생성 시 사용)
        stop_on_match: True이면 각 grid_string 검증 중 일치하는 결과가 나오면 종료
        auto_generate_predictions: True이면 cutoff 이전 데이터로 예측값 자동 생성
        
    Returns:
        dict: {
            "results": 검증 결과 목록,
            "summary": 요약 통계,
            "grid_string_ids": 검증된 grid_string ID 목록,
            "train_grid_string_ids": 학습용 grid_string ID 목록 (cutoff 이전),
            "predictions_generated": 예측값 생성 여부
        }
    """
    from change_point_prediction_module import save_or_update_predictions_for_change_point_data
    
    # cutoff 이전 데이터로 예측값 테이블 생성 (학습 데이터 기반)
    predictions_generated = False
    if auto_generate_predictions:
        try:
            pred_result = save_or_update_predictions_for_change_point_data(
                cutoff_grid_string_id=cutoff_grid_string_id,
                window_sizes=window_sizes,
                methods=(method,),
                thresholds=(threshold,),
                min_sample_count=15,
            )
            predictions_generated = True
        except Exception as e:
            # 예측값 생성 실패 시 경고만 출력하고 계속 진행
            import warnings
            warnings.warn(f"예측값 생성 실패: {e}. 기존 예측값을 사용합니다.")
    
    conn = get_change_point_db_connection()
    try:
        # cutoff 이후의 모든 grid_string ID 조회 (검증 데이터)
        df_test = pd.read_sql_query(
            "SELECT id FROM preprocessed_grid_strings WHERE id > ? ORDER BY id",
            conn,
            params=[cutoff_grid_string_id],
        )
        
        # cutoff 이전의 모든 grid_string ID 조회 (학습 데이터)
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
                "predictions_generated": predictions_generated,
            }
        
        # cutoff 이후의 모든 데이터를 검증
        test_gids = df_test["id"].tolist()
        train_gids = df_train["id"].tolist() if len(df_train) > 0 else []
        
        results = []
        for gid in test_gids:
            r = validate_first_anchor_extended_window_v2_cp(
                gid, cutoff_grid_string_id,
                window_sizes=window_sizes,
                method=method,
                threshold=threshold,
                stop_on_match=stop_on_match,
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
            "predictions_generated": predictions_generated,
        }
    finally:
        conn.close()


# ============================================================================
# 첫 앵커 확장 윈도우 V3 검증 함수 (V2 복제)
# ============================================================================

def validate_first_anchor_extended_window_v3_cp(
    grid_string_id,
    cutoff_grid_string_id,
    window_sizes=(9, 10, 11, 12, 13, 14),
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
):
    """
    첫 앵커 확장 윈도우 V3 검증 함수 (앵커 기반 순차 검증 시스템)
    
    **핵심 검증 로직:**
    [REQ-101] current_pos 이후의 가장 빠른 앵커를 검증 대상으로 선정
    [REQ-102] 윈도우 크기는 순차적으로 9, 10, 11, 12, 13, 14 검증
    
    [RULE-1] 적중 시 즉시 종료 (Success Exit)
    - 윈도우에서 예측값이 실제 결과와 일치하면 즉시 해당 앵커 검증 종료
    - matched_pos + 1을 current_pos로 설정하고 다음 앵커 탐색
    
    [RULE-2] 불일치 시 확장 검증 (Failure Sequence)
    - 예측값이 불일치할 경우에만 다음 윈도우로 확장
    - 3회 연속 불일치 발생 시 해당 앵커 검증 실패로 간주하고 종료
    - 3번째 불일치 포지션의 다음 인덱스(mismatched_pos + 1)를 current_pos로 설정
    
    Args:
        grid_string_id: 검증할 grid_string ID
        cutoff_grid_string_id: cutoff ID (사용하지 않지만 호환성을 위해 유지)
        window_sizes: 윈도우 크기 목록 (기본값: 9, 10, 11, 12, 13, 14)
        method: 예측 방법
        threshold: 임계값
        stop_on_match: True이면 일치하는 결과가 나오면 검증 종료
        
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
                "stopped_early": False,
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
        
        # 첫 번째 앵커부터 검증 시작
        # current_pos를 첫 번째 앵커 이하로 설정하여 첫 번째 앵커부터 검증
        first_anchor = anchors[0] if anchors else 0
        current_pos = 0  # 첫 번째 앵커부터 검증 시작
        MAX_CONSECUTIVE_FAILURES = 3
        
        # 앵커 기반 순차 검증 루프
        # 첫 번째 앵커부터 시작
        anchor_idx = 0
        
        while current_pos < len(grid_string) and anchor_idx < len(anchors):
            # [REQ-101] current_pos 이후의 가장 빠른 앵커 찾기
            # 이미 정렬된 anchors 리스트를 활용하여 인덱스로 접근
            while anchor_idx < len(anchors) and anchors[anchor_idx] < current_pos:
                anchor_idx += 1
            
            # 더 이상 검증할 앵커가 없으면 종료
            if anchor_idx >= len(anchors):
                break
            
            next_anchor = anchors[anchor_idx]
            
            # 해당 앵커에서 윈도우 크기별 순차 검증
            anchor_consecutive_failures = 0
            anchor_success = False
            last_mismatched_pos = None
            anchor_processed_any = False  # 이 앵커에서 실제로 처리한 예측이 있는지
            
            # [REQ-102] 윈도우 크기 9, 10, 11, 12, 13, 14 순차 검증
            for window_size in window_sizes:
                # 앵커 위치에서 window_size만큼 추출 가능한지 확인
                pos = next_anchor + window_size - 1
                if pos >= len(grid_string):
                    break  # 범위를 벗어나면 더 큰 윈도우는 시도하지 않음
                
                # current_pos보다 이전 포지션이면 건너뛰기
                if pos < current_pos:
                    continue
                
                total_steps += 1
                actual = grid_string[pos]
                
                # prefix 계산
                prefix_len = window_size - 1
                prefix = grid_string[pos - prefix_len : pos]
                
                # DB에서 예측값 조회 (시뮬레이션 전용 테이블 사용)
                q = """
                    SELECT predicted_value, confidence, b_ratio, p_ratio
                    FROM simulation_predictions_change_point
                    WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                    LIMIT 1
                """
                df_pred = pd.read_sql_query(q, conn, params=[window_size, prefix, method, threshold])
                
                if len(df_pred) == 0:
                    # 예측값이 없으면 스킵 (연속 실패 카운트에 포함하지 않음)
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
                        "confidence": 0.0,
                        "selected_window_size": window_size,
                        "all_predictions": [],
                        "skipped": True,
                        "skip_reason": "예측 테이블에 값 없음",
                    })
                    continue  # 스킵해도 계속 진행
                
                # 예측값이 있는 경우 처리
                anchor_processed_any = True
                row = df_pred.iloc[0]
                predicted = row["predicted_value"]
                confidence = row["confidence"]
                
                # 예측 결과 비교
                ok = predicted == actual
                total_predictions += 1
                
                if not ok:
                    consecutive_failures += 1
                    anchor_consecutive_failures += 1
                    total_failures += 1
                    last_mismatched_pos = pos
                    
                    if consecutive_failures > max_consecutive_failures:
                        max_consecutive_failures = consecutive_failures
                else:
                    consecutive_failures = 0
                    # [RULE-1] 적중 시 즉시 종료
                    anchor_success = True
                    anchor_consecutive_failures = 0
                
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
                        "b_ratio": row["b_ratio"],
                        "p_ratio": row["p_ratio"],
                    }],
                    "skipped": False,
                })
                
                # [RULE-1] 적중 시 즉시 종료하고 다음 앵커 탐색
                if ok:
                    current_pos = pos + 1
                    anchor_idx += 1  # 다음 앵커로
                    break  # 현재 앵커 검증 종료
                
                # [RULE-2] 3회 연속 불일치 발생 시 해당 앵커 검증 실패로 종료
                if anchor_consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    if last_mismatched_pos is not None:
                        current_pos = last_mismatched_pos + 1
                    else:
                        current_pos = pos + 1
                    anchor_idx += 1  # 다음 앵커로
                    break  # 현재 앵커 검증 종료
            
            # 윈도우 크기 루프가 끝났는데 current_pos가 업데이트되지 않은 경우
            # (적중도 없고 3회 연속 불일치도 없이 루프가 끝난 경우)
            if not anchor_success and anchor_consecutive_failures < MAX_CONSECUTIVE_FAILURES:
                # 이 앵커에서 실제로 처리한 예측이 있었다면 마지막 포지션 다음으로
                if anchor_processed_any and last_mismatched_pos is not None:
                    current_pos = last_mismatched_pos + 1
                elif anchor_processed_any:
                    # 처리했지만 불일치 포지션이 기록되지 않은 경우 (이론적으로 발생하지 않아야 함)
                    # 이 앵커에서 가능한 최대 포지션 다음으로
                    max_pos = min(next_anchor + max(window_sizes) - 1, len(grid_string) - 1)
                    current_pos = max_pos + 1
                else:
                    # 모든 윈도우가 스킵되었거나 범위를 벗어남
                    # 이 앵커에서 가능한 최대 포지션 다음으로
                    max_pos = min(next_anchor + max(window_sizes) - 1, len(grid_string) - 1)
                    if max_pos >= current_pos:
                        current_pos = max_pos + 1
                    else:
                        current_pos = len(grid_string)  # 루프 종료
                
                anchor_idx += 1  # 다음 앵커로
            
            # stop_on_match 옵션 처리
            if stop_on_match and anchor_success:
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


def create_simulation_predictions_change_point_table():
    """
    시뮬레이션 전용 예측 테이블 생성 (기존 DB 수정 없음)
    
    Returns:
        bool: 테이블 생성 성공 여부
    """
    conn = get_change_point_db_connection()
    cursor = conn.cursor()
    try:
        # 기존 테이블이 있으면 삭제하고 재생성 (시뮬레이션마다 새로 생성)
        cursor.execute("DROP TABLE IF EXISTS simulation_predictions_change_point")
        cursor.execute("""
            CREATE TABLE simulation_predictions_change_point (
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
            "CREATE INDEX IF NOT EXISTS idx_sim_cp_sp_window_prefix ON simulation_predictions_change_point(window_size, prefix)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_sim_cp_sp_method_threshold ON simulation_predictions_change_point(method, threshold)"
        )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def save_predictions_to_simulation_table(
    cutoff_grid_string_id=None,
    window_sizes=(9, 10, 11, 12, 13, 14),
    methods=("빈도 기반",),
    thresholds=(0,),
    batch_size=1000,
    min_sample_count=15,
):
    """
    시뮬레이션 전용 테이블에 예측값 저장
    
    - cutoff 이전(id <= cutoff) grid_string으로만 학습
    - simulation_predictions_change_point 테이블에 저장 (기존 테이블 수정 없음)
    
    Args:
        cutoff_grid_string_id: cutoff ID (이 ID 이전 = 학습 데이터)
        window_sizes: 윈도우 크기 목록
        methods: 예측 방법 목록
        thresholds: 임계값 목록
        batch_size: 배치 크기
        min_sample_count: 최소 표본 수 필터
        
    Returns:
        dict: 저장 결과 통계
    """
    from change_point_prediction_module import load_ngram_chunks_change_point
    from hypothesis_validation_app import (
        build_frequency_model,
        build_weighted_model,
        build_safety_first_model,
        predict_for_prefix,
        predict_confidence_threshold,
    )
    
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

            # 최소 표본 수 필터 적용: prefix별 출현 횟수 집계
            prefix_counts = train_ngrams.groupby("prefix").size()
            valid_prefixes = set(
                prefix_counts[prefix_counts >= min_sample_count].index.tolist()
            )

            for method in methods:
                if method == "빈도 기반":
                    model = build_frequency_model(train_ngrams)
                elif method == "가중치 기반":
                    model = build_weighted_model(train_ngrams)
                elif method == "안전 우선":
                    model = build_safety_first_model(train_ngrams)
                else:
                    model = build_frequency_model(train_ngrams)

                # 최소 표본 수 필터 적용된 prefix만 사용
                all_prefixes = set(train_ngrams["prefix"].unique()) & valid_prefixes
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
                                SELECT id FROM simulation_predictions_change_point
                                WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                                """,
                                (item[0], item[1], item[6], item[7]),
                            )
                            existing = cursor.fetchone()
                            cursor.execute(
                                """
                                INSERT OR REPLACE INTO simulation_predictions_change_point
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


def generate_simulation_predictions_table(
    cutoff_grid_string_id,
    window_sizes=(9, 10, 11, 12, 13, 14),
    method="빈도 기반",
    threshold=0,
    min_sample_count=15,
):
    """
    시뮬레이션 전용 예측 테이블 생성 및 예측값 저장 (별도 실행)
    
    **사용 방식:**
    1. 이 함수를 먼저 실행하여 예측값 테이블 생성
    2. 이후 batch_validate_first_anchor_extended_window_v3_cp 실행하여 검증
    
    Args:
        cutoff_grid_string_id: cutoff ID (이 ID 이전 = 학습 데이터)
        window_sizes: 윈도우 크기 목록 (기본값: 9, 10, 11, 12, 13, 14)
        method: 예측 방법
        threshold: 임계값 (예측값 생성 시 사용)
        min_sample_count: 최소 표본 수 필터
        
    Returns:
        dict: 저장 결과 통계
    """
    # 시뮬레이션 전용 테이블 생성
    try:
        create_simulation_predictions_change_point_table()
    except Exception as e:
        import warnings
        warnings.warn(f"시뮬레이션 테이블 생성 실패: {e}")
        raise
    
    # cutoff 이전 데이터로 예측값 생성하여 시뮬레이션 테이블에 저장
    try:
        pred_result = save_predictions_to_simulation_table(
            cutoff_grid_string_id=cutoff_grid_string_id,
            window_sizes=window_sizes,
            methods=(method,),
            thresholds=(threshold,),
            min_sample_count=min_sample_count,
        )
        return pred_result
    except Exception as e:
        import warnings
        warnings.warn(f"예측값 생성 실패: {e}")
        raise


def batch_validate_first_anchor_extended_window_v3_cp(
    cutoff_grid_string_id,
    window_sizes=(9, 10, 11, 12, 13, 14),
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
):
    """
    첫 앵커 확장 윈도우 V3 배치 검증 (cutoff 이후 grid_string)
    
    **예측값 사용 방식:**
    1. 시뮬레이션 전용 테이블 (simulation_predictions_change_point)에서 예측값 조회
    2. cutoff 이후 데이터(검증 데이터) 검증
    3. 기존 stored_predictions_change_point 테이블은 수정하지 않음
    
    **주의사항:**
    - 검증 전에 generate_simulation_predictions_table() 함수를 먼저 실행하여
      예측값 테이블을 생성해야 합니다.
    
    Args:
        cutoff_grid_string_id: cutoff ID (이 ID 이전 = 학습 데이터, 이후 = 검증 데이터)
        window_sizes: 윈도우 크기 목록 (기본값: 9, 10, 11, 12, 13, 14)
        method: 예측 방법
        threshold: 임계값 (예측값 조회 시 사용)
        stop_on_match: True이면 각 grid_string 검증 중 일치하는 결과가 나오면 종료
        
    Returns:
        dict: {
            "results": 검증 결과 목록,
            "summary": 요약 통계,
            "grid_string_ids": 검증된 grid_string ID 목록,
            "train_grid_string_ids": 학습용 grid_string ID 목록 (cutoff 이전)
        }
    """
    
    conn = get_change_point_db_connection()
    try:
        # cutoff 이후의 모든 grid_string ID 조회 (검증 데이터)
        df_test = pd.read_sql_query(
            "SELECT id FROM preprocessed_grid_strings WHERE id > ? ORDER BY id",
            conn,
            params=[cutoff_grid_string_id],
        )
        
        # cutoff 이전의 모든 grid_string ID 조회 (학습 데이터)
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
        
        # cutoff 이후의 모든 데이터를 검증
        test_gids = df_test["id"].tolist()
        train_gids = df_train["id"].tolist() if len(df_train) > 0 else []
        
        results = []
        for gid in test_gids:
            r = validate_first_anchor_extended_window_v3_cp(
                gid, cutoff_grid_string_id,
                window_sizes=window_sizes,
                method=method,
                threshold=threshold,
                stop_on_match=stop_on_match,
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
