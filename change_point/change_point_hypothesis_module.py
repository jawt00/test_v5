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

try:
    from svg_parser_module import get_simulation_predictions_db_connection
except ImportError:
    import os
    import sqlite3

    def get_simulation_predictions_db_connection():
        """점진적 검증 시뮬레이션 전용 예측 DB 연결 (로컬 fallback)."""
        db_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "change_point",
            "simulation_predictions.db",
        )
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=20.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn


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
        return "첫 앵커 확장 윈도우 검증 v3 ( 9 - 14 )"
    
    def get_description(self):
        return "첫 번째 앵커에서 윈도우 크기 9, 10, 11, 12, 13, 14를 신뢰도 기반으로 검증합니다. (V2 복제, 수정 가능)"
    
    def get_config_schema(self):
        return {}


class FirstAnchorExtendedWindowHypothesisV3LiveNextAnchor(Hypothesis):
    """
    첫 앵커 확장 윈도우 가설 V3 (라이브 다음 앵커) - 라이브 게임 검증 방식.
    
    V3와 동일한 REQ-102, RULE-1, RULE-2 적용.
    다음 앵커 선택만 [REQ-101-LIVE]: current_pos를 예측 가능한 가장 낮은 앵커 선택.
    (V3는 anchor_position >= current_pos, 본 가설은 anchor + max_ws - 1 >= current_pos)
    상세: change_point/라이브게임_V3_다음앵커_로직_차이.md 참고.
    """
    
    def __init__(self):
        pass
    
    def predict(self, grid_string, position, window_sizes, method, threshold, **kwargs):
        """
        V3(9-14)와 동일: stored_predictions_change_point 조회, 확장 윈도우 9~14 중 최고 신뢰도 선택.
        (다음 앵커 선택만 검증 루프에서 [REQ-101-LIVE] 적용, predict 자체는 V3와 동일)
        """
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
        return "첫 앵커 확장 윈도우 V3 (라이브 다음 앵커)"
    
    def get_description(self):
        return (
            "라이브 게임 검증 방식: current_pos를 예측 가능한 가장 낮은 앵커 선택. "
            "REQ-102·RULE-1·RULE-2는 V3와 동일. "
            "검증 시 validate_first_anchor_extended_window_v3_live_next_anchor_cp 사용."
        )
    
    def get_config_schema(self):
        return {}


class FirstAnchorWindow9OnlyHypothesis(Hypothesis):
    """
    첫 앵커 윈도우 9 전용 가설 (V3 복제·수정).
    앵커당 윈도우 9만 검증, 윈도우 9 검증 후 종료 조건 충족 시 다음 포지션으로 진행.
    """
    WINDOW_SIZE = 9

    def __init__(self):
        pass

    def predict(self, grid_string, position, window_sizes, method, threshold, **kwargs):
        """윈도우 9만 사용하여 예측 (stored_predictions_change_point 조회)."""
        ws = self.WINDOW_SIZE
        prefix_len = ws - 1
        if position < prefix_len:
            return {
                "predicted": None,
                "confidence": 0.0,
                "window_size": None,
                "prefix": None,
                "all_predictions": [],
                "skipped": False,
            }
        prefix = grid_string[position - prefix_len : position]
        conn = get_change_point_db_connection()
        try:
            q = """
                SELECT predicted_value, confidence, b_ratio, p_ratio
                FROM stored_predictions_change_point
                WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                LIMIT 1
            """
            df = pd.read_sql_query(q, conn, params=[ws, prefix, method, threshold])
            if len(df) == 0:
                return {
                    "predicted": None,
                    "confidence": 0.0,
                    "window_size": None,
                    "prefix": None,
                    "all_predictions": [],
                    "skipped": False,
                }
            row = df.iloc[0]
            return {
                "predicted": row["predicted_value"],
                "confidence": row["confidence"],
                "window_size": ws,
                "prefix": prefix,
                "all_predictions": [{
                    "window_size": ws,
                    "prefix": prefix,
                    "predicted": row["predicted_value"],
                    "confidence": row["confidence"],
                    "b_ratio": row["b_ratio"],
                    "p_ratio": row["p_ratio"],
                }],
                "skipped": False,
            }
        finally:
            conn.close()

    def get_name(self):
        return "윈도우 9 전용"

    def get_description(self):
        return "전체 스트링의 모든 앵커에 대해 윈도우 9만 검증. 앵커당 1회 검증 후 해당 앵커 종료(다음 앵커로). 검증 시 validate_first_anchor_window9_only_cp 사용."

    def get_config_schema(self):
        return {}


class FirstAnchorWindow9And10Hypothesis(Hypothesis):
    """
    윈도우 9·10 검증 가설 (윈도우 9 전용 복제·수정).
    전체 스트링의 모든 앵커에 대해 윈도우 9, 10까지 검증 후 해당 앵커 종료(다음 앵커로).
    """
    WINDOW_SIZES = (9, 10)

    def __init__(self):
        pass

    def predict(self, grid_string, position, window_sizes, method, threshold, **kwargs):
        """윈도우 9·10 사용하여 예측 (stored_predictions_change_point 조회, 최고 신뢰도 선택)."""
        conn = get_change_point_db_connection()
        try:
            all_predictions = []
            for ws in self.WINDOW_SIZES:
                prefix_len = ws - 1
                if position < prefix_len:
                    continue
                prefix = grid_string[position - prefix_len : position]
                q = """
                    SELECT predicted_value, confidence, b_ratio, p_ratio
                    FROM stored_predictions_change_point
                    WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                    LIMIT 1
                """
                df = pd.read_sql_query(q, conn, params=[ws, prefix, method, threshold])
                if len(df) == 0:
                    continue
                row = df.iloc[0]
                all_predictions.append({
                    "window_size": ws,
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
        return "윈도우 9,10 전용"

    def get_description(self):
        return "전체 스트링의 모든 앵커에 대해 윈도우 9, 10까지 검증 후 해당 앵커 종료. 검증 시 validate_first_anchor_window9_10_cp 사용."

    def get_config_schema(self):
        return {}


class FirstAnchorWindow9And10V2Hypothesis(Hypothesis):
    """
    윈도우 9·10 검증 가설 v2 (FirstAnchorWindow9And10Hypothesis 복제).
    전체 스트링의 모든 앵커에 대해 윈도우 9, 10까지 검증 후 해당 앵커 종료(다음 앵커로).
    """
    WINDOW_SIZES = (9, 10)

    def __init__(self):
        pass

    def predict(self, grid_string, position, window_sizes, method, threshold, **kwargs):
        """윈도우 9·10 사용하여 예측 (stored_predictions_change_point 조회, 최고 신뢰도 선택)."""
        conn = get_change_point_db_connection()
        try:
            all_predictions = []
            for ws in self.WINDOW_SIZES:
                prefix_len = ws - 1
                if position < prefix_len:
                    continue
                prefix = grid_string[position - prefix_len : position]
                q = """
                    SELECT predicted_value, confidence, b_ratio, p_ratio
                    FROM stored_predictions_change_point
                    WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                    LIMIT 1
                """
                df = pd.read_sql_query(q, conn, params=[ws, prefix, method, threshold])
                if len(df) == 0:
                    continue
                row = df.iloc[0]
                all_predictions.append({
                    "window_size": ws,
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
        return "윈도우 9,10 전용 v2"

    def get_description(self):
        return (
            "전체 스트링의 모든 앵커에 대해 윈도우 9, 10까지 검증 후 해당 앵커 종료. "
            "선택한 예측방법의 신뢰도가 설정값 이하이면 스킵. 검증 시 validate_first_anchor_window9_10_v2_cp 사용."
        )

    def get_config_schema(self):
        return {
            "min_confidence": {
                "type": "number",
                "label": "신뢰도 최소 (%)",
                "default": 51.5,
                "min": 0.0,
                "max": 100.0,
                "step": 0.1,
            }
        }


class FirstAnchorWindow9And10V3Hypothesis(Hypothesis):
    """
    윈도우 9·10 검증 가설 v3 (윈도우 9,10 전용 복제).
    전체 스트링의 모든 앵커에 대해 윈도우 9, 10까지 검증 후 해당 앵커 종료(다음 앵커로).
    """
    WINDOW_SIZES = (9, 10)

    def __init__(self):
        pass

    def predict(self, grid_string, position, window_sizes, method, threshold, **kwargs):
        """윈도우 9·10 사용하여 예측 (stored_predictions_change_point 조회, 최고 신뢰도 선택)."""
        conn = get_change_point_db_connection()
        try:
            all_predictions = []
            for ws in self.WINDOW_SIZES:
                prefix_len = ws - 1
                if position < prefix_len:
                    continue
                prefix = grid_string[position - prefix_len : position]
                q = """
                    SELECT predicted_value, confidence, b_ratio, p_ratio
                    FROM stored_predictions_change_point
                    WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                    LIMIT 1
                """
                df = pd.read_sql_query(q, conn, params=[ws, prefix, method, threshold])
                if len(df) == 0:
                    continue
                row = df.iloc[0]
                all_predictions.append({
                    "window_size": ws,
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
        return "윈도우 9,10 전용 v3"

    def get_description(self):
        return (
            "기존 윈도우 9,10 전용과 동일한 다음 앵커/위치 진행 규칙(current_pos, anchor_idx). "
            "진입 판정은 윈도우 9에서만(빈도 신뢰도≥51.5%·가중치 예측값 일치). "
            "미충족 시 해당 앵커 즉시 종료·윈도우 10 생략. 윈도우 9 일치 시 조기 종료; 불일치 시에만 윈도우 10 연쇄 검증(윈도우 10은 진입 판정 없이 예측값만 사용). validate_first_anchor_window9_10_v3_cp 사용."
        )

    def get_config_schema(self):
        return {
            "threshold_freq": {
                "type": "number",
                "label": "빈도 신뢰도 최소 (%)",
                "default": 51.5,
                "min": 0.0,
                "max": 100.0,
                "step": 0.1,
            }
        }


class FirstAnchorWindow9_10Agree55Hypothesis(Hypothesis):
    """
    윈도우 9·10 검증 (agree55): 앵커당 9→10만, 빈도/가중치 일치·빈도 신뢰도 ≥ 임계값,
    조기 종료(윈도우 9 적중 시), 앵커 중첩 시 이전 앵커만 검증.
    """
    WINDOW_SIZES = (9, 10)

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
        return "윈도우 9,10 (agree55)"

    def get_description(self):
        return (
            "앵커당 윈도우 9·10만 검증. 빈도/가중치 예측값 일치·빈도 신뢰도 ≥ 임계값(기본 55%)일 때만 예측 사용; "
            "윈도우 9 적중 시 조기 종료; 앵커 중첩 시 이전 앵커만 검증. validate_first_anchor_window9_10_agree55_cp 사용."
        )

    def get_config_schema(self):
        return {}


class FirstAnchorWindow9_10Agree55V2Hypothesis(Hypothesis):
    """
    agree55 v2: 가중치 신뢰도 70% 이상이면 무조건 가중치 예측값 사용, 나머지 규칙은 agree55와 동일.
    """
    WINDOW_SIZES = (9, 10)

    def __init__(self):
        pass

    def predict(self, grid_string, position, window_sizes, method, threshold, **kwargs):
        return {
            "predicted": None,
            "confidence": 0.0,
            "window_size": None,
            "prefix": None,
            "all_predictions": [],
            "skipped": True,
        }

    def get_name(self):
        return "윈도우 9,10 (agree55 v2)"

    def get_description(self):
        return (
            "agree55와 동일하되, 가중치 기반 예측 신뢰도 70% 이상이면 무조건 그 예측값 사용. "
            "그 미만일 때만 빈도/가중치 일치·빈도·가중치 신뢰도 임계값 적용."
        )

    def get_config_schema(self):
        return {}


class FirstAnchorWindow9_10Agree55V3Hypothesis(Hypothesis):
    """
    agree55 v3: 진입 판정(빈도≥51.5%·가중치일치), 윈도우 9 미충족 시 윈도우 10 생략, 불일치 시에만 윈도우 10 확장 검증.
    """
    WINDOW_SIZES = (9, 10)

    def __init__(self):
        pass

    def predict(self, grid_string, position, window_sizes, method, threshold, **kwargs):
        return {
            "predicted": None,
            "confidence": 0.0,
            "window_size": None,
            "prefix": None,
            "all_predictions": [],
            "skipped": True,
        }

    def get_name(self):
        return "윈도우 9,10 (agree55 v3)"

    def get_description(self):
        return (
            "v3: 윈도우 9에서 빈도 신뢰도≥51.5%·가중치 예측 일치 시만 진입. "
            "미충족 시 윈도우 10 생략·앵커 종료. 진입 후 불일치일 때만 윈도우 10 확장 검증."
        )

    def get_config_schema(self):
        return {}


class FirstAnchorWindow9Freq518Win50Hypothesis(Hypothesis):
    """
    윈도우 9만 검증. 첫 앵커부터 순차, 빈도 신뢰도 ≥ 51.8% 및 시뮬레이션 승률 ≥ 50%일 때만 예측 사용; 앵커 중첩 시 이전 앵커만 검증.
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
        return "윈도우9 빈도+승률"

    def get_description(self):
        return (
            "윈도우 9만 검증. 첫 앵커부터 순차. 빈도 신뢰도 ≥ 51.8% 및 시뮬레이션 승률 ≥ 50%일 때만 예측 사용; "
            "한쪽이라도 불만족 시 스킵. 앵커 중첩 시 이전 앵커만 검증. validate_first_anchor_window9_freq518_win50_cp 사용."
        )

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
register_hypothesis("first_anchor_extended_window_v3_live_next_anchor", FirstAnchorExtendedWindowHypothesisV3LiveNextAnchor)
register_hypothesis("first_anchor_window9_only", FirstAnchorWindow9OnlyHypothesis)
register_hypothesis("first_anchor_window9_10", FirstAnchorWindow9And10Hypothesis)
register_hypothesis("first_anchor_window9_10_v2", FirstAnchorWindow9And10V2Hypothesis)
register_hypothesis("first_anchor_window9_10_v3", FirstAnchorWindow9And10V3Hypothesis)
register_hypothesis("first_anchor_window9_10_agree55", FirstAnchorWindow9_10Agree55Hypothesis)
register_hypothesis("first_anchor_window9_10_agree55_v2", FirstAnchorWindow9_10Agree55V2Hypothesis)
register_hypothesis("first_anchor_window9_10_agree55_v3", FirstAnchorWindow9_10Agree55V3Hypothesis)
register_hypothesis("first_anchor_window9_freq518_win50", FirstAnchorWindow9Freq518Win50Hypothesis)
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
    end_grid_string_id=None,
    **hypothesis_params
):
    """
    가설 기반 배치 검증 (cutoff 이후 grid_string)
    
    Args:
        cutoff_grid_string_id: cutoff ID (이 ID 이전 = 학습 데이터, 이후 = 검증 데이터)
        end_grid_string_id: None이면 cutoff 이후 전체 검증; 지정 시 해당 ID까지만 검증 (cutoff < id <= end)
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
        # cutoff 이후(및 end_grid_string_id 이하) grid_string ID 조회 (검증 데이터)
        if end_grid_string_id is not None:
            df_test = pd.read_sql_query(
                "SELECT id FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id",
                conn,
                params=[cutoff_grid_string_id, end_grid_string_id],
            )
        else:
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
        
        # cutoff 이후(및 end 이하) 데이터를 검증 (train_ratio 무시)
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
    end_grid_string_id=None,
):
    """
    임계점 스킵 + 앵커 우선순위 배치 검증 (cutoff 이후 grid_string)
    
    Args:
        cutoff_grid_string_id: cutoff ID (이 ID 이전 = 학습 데이터, 이후 = 검증 데이터)
        end_grid_string_id: None이면 cutoff 이후 전체; 지정 시 해당 ID까지만 검증
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
        # cutoff 이후(및 end 이하) grid_string ID 조회 (검증 데이터)
        if end_grid_string_id is not None:
            df_test = pd.read_sql_query(
                "SELECT id FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id",
                conn,
                params=[cutoff_grid_string_id, end_grid_string_id],
            )
        else:
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
        
        # cutoff 이후(및 end 이하) 데이터를 검증 (train_ratio 무시)
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
    end_grid_string_id=None,
):
    """
    임계점 스킵 + 앵커 우선순위 확장 배치 검증 (cutoff 이후 grid_string)
    
    Args:
        cutoff_grid_string_id: cutoff ID (이 ID 이전 = 학습 데이터, 이후 = 검증 데이터)
        end_grid_string_id: None이면 cutoff 이후 전체; 지정 시 해당 ID까지만 검증
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
        # cutoff 이후(및 end 이하) grid_string ID 조회 (검증 데이터)
        if end_grid_string_id is not None:
            df_test = pd.read_sql_query(
                "SELECT id FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id",
                conn,
                params=[cutoff_grid_string_id, end_grid_string_id],
            )
        else:
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
        
        # cutoff 이후(및 end 이하) 데이터를 검증 (train_ratio 무시)
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
        if hypothesis == "first_anchor_window9_only" or isinstance(hypothesis_instance, FirstAnchorWindow9OnlyHypothesis):
            res = batch_validate_first_anchor_window9_only_cp(
                cutoff_grid_string_id,
                method=method,
                threshold=threshold,
                stop_on_match=stop_on_match,
            )
        elif hypothesis == "first_anchor_window9_10" or isinstance(hypothesis_instance, FirstAnchorWindow9And10Hypothesis):
            res = batch_validate_first_anchor_window9_10_cp(
                cutoff_grid_string_id,
                method=method,
                threshold=threshold,
                stop_on_match=stop_on_match,
            )
        elif hypothesis == "first_anchor_window9_10_v2" or isinstance(hypothesis_instance, FirstAnchorWindow9And10V2Hypothesis):
            min_conf_v2 = hypothesis_params.get("min_confidence", 51.5)
            res = batch_validate_first_anchor_window9_10_v2_cp(
                cutoff_grid_string_id,
                method=method,
                threshold=threshold,
                stop_on_match=stop_on_match,
                min_confidence=min_conf_v2,
            )
        elif hypothesis == "first_anchor_window9_10_v3" or isinstance(hypothesis_instance, FirstAnchorWindow9And10V3Hypothesis):
            min_conf_freq = hypothesis_params.get("threshold_freq", 51.5)
            res = batch_validate_first_anchor_window9_10_v3_cp(
                cutoff_grid_string_id,
                threshold=threshold,
                stop_on_match=stop_on_match,
                min_confidence_freq=min_conf_freq,
            )
        elif hypothesis == "first_anchor_window9_10_agree55" or isinstance(hypothesis_instance, FirstAnchorWindow9_10Agree55Hypothesis):
            thresh_freq = hypothesis_params.get("threshold_freq", threshold)
            thresh_weight = hypothesis_params.get("threshold_weight", threshold)
            res = batch_validate_first_anchor_window9_10_agree55_cp(
                cutoff_grid_string_id,
                threshold=0,
                stop_on_match=stop_on_match,
                min_confidence_freq=thresh_freq,
                min_confidence_weight=thresh_weight,
            )
        elif hypothesis == "first_anchor_window9_10_agree55_v2" or isinstance(hypothesis_instance, FirstAnchorWindow9_10Agree55V2Hypothesis):
            thresh_freq = hypothesis_params.get("threshold_freq", threshold)
            thresh_weight = hypothesis_params.get("threshold_weight", threshold)
            weight_unconditional = hypothesis_params.get("weight_confidence_unconditional", 70)
            res = batch_validate_first_anchor_window9_10_agree55_v2_cp(
                cutoff_grid_string_id,
                threshold=0,
                stop_on_match=stop_on_match,
                min_confidence_freq=thresh_freq,
                min_confidence_weight=thresh_weight,
                weight_confidence_unconditional=weight_unconditional,
            )
        elif hypothesis == "first_anchor_window9_10_agree55_v3" or isinstance(hypothesis_instance, FirstAnchorWindow9_10Agree55V3Hypothesis):
            thresh_freq = hypothesis_params.get("threshold_freq", threshold)
            thresh_weight = hypothesis_params.get("threshold_weight", threshold)
            weight_unconditional = hypothesis_params.get("weight_confidence_unconditional", 70)
            res = batch_validate_first_anchor_window9_10_agree55_v3_cp(
                cutoff_grid_string_id,
                threshold=0,
                stop_on_match=stop_on_match,
                min_confidence_freq=thresh_freq,
                min_confidence_weight=thresh_weight,
                weight_confidence_unconditional=weight_unconditional,
            )
        elif hypothesis == "first_anchor_window9_freq518_win50" or isinstance(hypothesis_instance, FirstAnchorWindow9Freq518Win50Hypothesis):
            min_conf_freq = hypothesis_params.get("threshold_freq", 51.8)
            min_wr = hypothesis_params.get("min_win_rate_pct", 50)
            res = batch_validate_first_anchor_window9_freq518_win50_cp(
                cutoff_grid_string_id,
                threshold=0,
                stop_on_match=stop_on_match,
                min_confidence_freq=min_conf_freq,
                min_win_rate_pct=min_wr,
            )
        elif isinstance(hypothesis_instance, ThresholdSkipAnchorPriorityHypothesis) and not isinstance(hypothesis_instance, ThresholdSkipAnchorPriorityExtendedHypothesis):
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
    end_grid_string_id=None,
):
    """
    첫 앵커 확장 윈도우 V2 배치 검증 (cutoff 이후 grid_string)
    
    **예측값 사용 방식:**
    1. cutoff 이전 데이터(학습 데이터)로만 예측값 테이블 생성
    2. 생성된 예측값으로 cutoff 이후 데이터(검증 데이터) 검증
    
    Args:
        cutoff_grid_string_id: cutoff ID (이 ID 이전 = 학습 데이터, 이후 = 검증 데이터)
        end_grid_string_id: None이면 cutoff 이후 전체; 지정 시 해당 ID까지만 검증
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
        # cutoff 이후(및 end 이하) grid_string ID 조회 (검증 데이터)
        if end_grid_string_id is not None:
            df_test = pd.read_sql_query(
                "SELECT id FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id",
                conn,
                params=[cutoff_grid_string_id, end_grid_string_id],
            )
        else:
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
        
        # cutoff 이후(및 end 이하) 데이터를 검증
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
    predictions_conn=None,
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
        predictions_conn: 예측 테이블 조회용 DB 연결. None이면 change_point_ngram.db 사용.
        
    Returns:
        dict: 검증 결과
    """
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
                df_pred = pd.read_sql_query(q, pred_conn, params=[window_size, prefix, method, threshold])
                
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


# ============================================================================
# 첫 앵커 윈도우 9 전용 검증 (전체 스트링 대상, 앵커당 윈도우 9만 검증)
# ============================================================================
# "윈도우 9 종료" = 해당 앵커에 대한 종료(다음 앵커로 이동). 전체 스트링 종료 아님.
# 전체 스트링의 모든 앵커에 대해 윈도우 9로 1회씩 검증 후 다음 앵커로 진행.
# ============================================================================

def validate_first_anchor_window9_only_cp(
    grid_string_id,
    cutoff_grid_string_id,
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
    predictions_conn=None,
):
    """
    윈도우 9 전용 검증: 전체 스트링의 모든 앵커에 대해 윈도우 9만 검증.
    - 전체 스트링: 모든 앵커를 순서대로 검증 (첫 번째 앵커만이 아님).
    - 앵커당 윈도우 9 한 번 검증 후, "해당 앵커에 대한 종료" → 다음 앵커로 이동.
    - simulation_predictions_change_point 사용.
    """
    WINDOW_SIZE = 9
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
        if len(grid_string) < WINDOW_SIZE:
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
        current_pos = 0
        anchor_idx = 0

        # 전체 스트링: 모든 앵커에 대해 윈도우 9 검증 (앵커당 1회 후 해당 앵커 종료 → 다음 앵커)
        while current_pos < len(grid_string) and anchor_idx < len(anchors):
            while anchor_idx < len(anchors) and anchors[anchor_idx] < current_pos:
                anchor_idx += 1
            if anchor_idx >= len(anchors):
                break
            next_anchor = anchors[anchor_idx]
            pos = next_anchor + WINDOW_SIZE - 1
            if pos >= len(grid_string):
                anchor_idx += 1
                continue
            if pos < current_pos:
                anchor_idx += 1
                continue

            total_steps += 1
            actual = grid_string[pos]
            prefix_len = WINDOW_SIZE - 1
            prefix = grid_string[pos - prefix_len : pos]
            q = """
                SELECT predicted_value, confidence, b_ratio, p_ratio
                FROM simulation_predictions_change_point
                WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                LIMIT 1
            """
            df_pred = pd.read_sql_query(q, pred_conn, params=[WINDOW_SIZE, prefix, method, threshold])

            if len(df_pred) == 0:
                total_skipped += 1
                history.append({
                    "step": total_steps,
                    "position": pos,
                    "anchor": next_anchor,
                    "window_size": WINDOW_SIZE,
                    "prefix": prefix,
                    "predicted": None,
                    "actual": actual,
                    "is_correct": None,
                    "confidence": 0.0,
                    "selected_window_size": WINDOW_SIZE,
                    "all_predictions": [],
                    "skipped": True,
                    "skip_reason": "예측 테이블에 값 없음",
                })
                # 해당 앵커에 대한 검증 완료(스킵) → 다음 앵커로
                current_pos = pos + 1
                anchor_idx += 1
                continue

            row = df_pred.iloc[0]
            predicted = row["predicted_value"]
            confidence = row["confidence"]
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
                "anchor": next_anchor,
                "window_size": WINDOW_SIZE,
                "prefix": prefix,
                "predicted": predicted,
                "actual": actual,
                "is_correct": ok,
                "confidence": confidence,
                "selected_window_size": WINDOW_SIZE,
                "all_predictions": [{
                    "window_size": WINDOW_SIZE,
                    "prefix": prefix,
                    "predicted": predicted,
                    "confidence": confidence,
                    "b_ratio": row["b_ratio"],
                    "p_ratio": row["p_ratio"],
                }],
                "skipped": False,
            })
            # 해당 앵커에 대한 윈도우 9 검증 완료 → 다음 앵커로 (전체 스트링 종료 아님)
            current_pos = pos + 1
            anchor_idx += 1
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


# ============================================================================
# 윈도우 9·10 검증 (전체 스트링, 앵커당 9→10 검증 후 해당 앵커 종료)
# ============================================================================

def validate_first_anchor_window9_10_cp(
    grid_string_id,
    cutoff_grid_string_id,
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
    predictions_conn=None,
):
    """
    윈도우 9·10 검증: 전체 스트링의 모든 앵커에 대해 윈도우 9, 10까지 검증 후 해당 앵커 종료.
    - 앵커당: 윈도우 9 검증 → 윈도우 10 검증 (순차), 적중 시 즉시 해당 앵커 종료; 아니면 10까지 후 종료.
    - simulation_predictions_change_point 사용.
    """
    WINDOW_SIZES = (9, 10)
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
        current_pos = 0
        anchor_idx = 0

        while current_pos < len(grid_string) and anchor_idx < len(anchors):
            while anchor_idx < len(anchors) and anchors[anchor_idx] < current_pos:
                anchor_idx += 1
            if anchor_idx >= len(anchors):
                break
            next_anchor = anchors[anchor_idx]
            anchor_matched = False
            last_pos = None

            for window_size in WINDOW_SIZES:
                pos = next_anchor + window_size - 1
                if pos >= len(grid_string):
                    # 이 앵커에서 더 이상 검증할 위치 없음 → 앵커 종료 후 다음 앵커로 (무한루프 방지)
                    current_pos = next_anchor + 1
                    anchor_idx += 1
                    break
                if pos < current_pos:
                    continue

                total_steps += 1
                actual = grid_string[pos]
                prefix_len = window_size - 1
                prefix = grid_string[pos - prefix_len : pos]
                q = """
                    SELECT predicted_value, confidence, b_ratio, p_ratio
                    FROM simulation_predictions_change_point
                    WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                    LIMIT 1
                """
                df_pred = pd.read_sql_query(q, pred_conn, params=[window_size, prefix, method, threshold])

                if len(df_pred) == 0:
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
                    last_pos = pos
                    continue

                row = df_pred.iloc[0]
                predicted = row["predicted_value"]
                confidence = row["confidence"]
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
                        "b_ratio": row["b_ratio"],
                        "p_ratio": row["p_ratio"],
                    }],
                    "skipped": False,
                })
                last_pos = pos
                if ok:
                    current_pos = pos + 1
                    anchor_idx += 1
                    anchor_matched = True
                    break
            else:
                current_pos = (last_pos + 1) if last_pos is not None else (next_anchor + max(WINDOW_SIZES))
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


# ============================================================================
# 윈도우 9·10 검증 v2 (FirstAnchorWindow9And10Hypothesis 복제 + 신뢰도 스킵)
# ============================================================================

def validate_first_anchor_window9_10_v2_cp(
    grid_string_id,
    cutoff_grid_string_id,
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
    predictions_conn=None,
    min_confidence=51.5,
):
    """
    윈도우 9·10 검증 v2. validate_first_anchor_window9_10_cp와 동일하되,
    선택한 예측방법의 신뢰도가 min_confidence 이하이면 예측 스킵.
    """
    WINDOW_SIZES = (9, 10)
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
        current_pos = 0
        anchor_idx = 0

        while current_pos < len(grid_string) and anchor_idx < len(anchors):
            while anchor_idx < len(anchors) and anchors[anchor_idx] < current_pos:
                anchor_idx += 1
            if anchor_idx >= len(anchors):
                break
            next_anchor = anchors[anchor_idx]
            anchor_matched = False
            last_pos = None

            for window_size in WINDOW_SIZES:
                pos = next_anchor + window_size - 1
                if pos >= len(grid_string):
                    current_pos = next_anchor + 1
                    anchor_idx += 1
                    break
                if pos < current_pos:
                    continue

                total_steps += 1
                actual = grid_string[pos]
                prefix_len = window_size - 1
                prefix = grid_string[pos - prefix_len : pos]
                q = """
                    SELECT predicted_value, confidence, b_ratio, p_ratio
                    FROM simulation_predictions_change_point
                    WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                    LIMIT 1
                """
                df_pred = pd.read_sql_query(q, pred_conn, params=[window_size, prefix, method, threshold])

                if len(df_pred) == 0:
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
                    last_pos = pos
                    continue

                row = df_pred.iloc[0]
                predicted = row["predicted_value"]
                confidence = row["confidence"] if row["confidence"] is not None else 0.0

                # v2 전용: 선택한 예측방법 신뢰도가 min_confidence 이하이면 스킵
                if confidence <= min_confidence:
                    total_skipped += 1
                    history.append({
                        "step": total_steps,
                        "position": pos,
                        "anchor": next_anchor,
                        "window_size": window_size,
                        "prefix": prefix,
                        "predicted": predicted,
                        "actual": actual,
                        "is_correct": None,
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
                        "skipped": True,
                        "skip_reason": f"신뢰도 {confidence:.1f}% ≤ {min_confidence:.1f}% (스킵)",
                    })
                    last_pos = pos
                    continue

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
                        "b_ratio": row["b_ratio"],
                        "p_ratio": row["p_ratio"],
                    }],
                    "skipped": False,
                })
                last_pos = pos
                if ok:
                    current_pos = pos + 1
                    anchor_idx += 1
                    anchor_matched = True
                    break
            else:
                current_pos = (last_pos + 1) if last_pos is not None else (next_anchor + max(WINDOW_SIZES))
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


# ============================================================================
# 윈도우 9·10 검증 v3 (기존 윈도우 9,10 전용 구조 + 윈도우 9에서만 진입 판정)
# ============================================================================

def validate_first_anchor_window9_10_v3_cp(
    grid_string_id,
    cutoff_grid_string_id,
    threshold=0,
    stop_on_match=False,
    predictions_conn=None,
    min_confidence_freq=51.5,
):
    """
    윈도우 9·10 검증 v3. 기존 윈도우 9,10 전용(validate_first_anchor_window9_10_cp)과 동일한
    다음 앵커/위치 진행 규칙(current_pos, anchor_idx)을 사용하고,
    윈도우 9에서만 사용자 정의 진입 판정(빈도 신뢰도 ≥ min_confidence_freq AND 빈도·가중치 예측값 일치)을 적용.
    - 진입 판정 미충족 시 해당 앵커 즉시 종료(윈도우 10 생략), current_pos/anchor_idx는 기존 규칙대로 갱신.
    - 윈도우 9 일치 시 조기 종료(윈도우 10 생략).
    - 윈도우 10: 진입 판정 없음, 테이블 예측값만 사용(빈도 우선, 없으면 가중치).
    """
    WINDOW_SIZES = (9, 10)
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
        current_pos = 0
        anchor_idx = 0

        while current_pos < len(grid_string) and anchor_idx < len(anchors):
            while anchor_idx < len(anchors) and anchors[anchor_idx] < current_pos:
                anchor_idx += 1
            if anchor_idx >= len(anchors):
                break
            next_anchor = anchors[anchor_idx]
            anchor_matched = False
            last_pos = None
            w9_entered_but_mismatch = False  # 윈도우 9 진입 후 불일치일 때만 윈도우 10 진행

            for window_size in WINDOW_SIZES:
                pos = next_anchor + window_size - 1
                if pos >= len(grid_string):
                    current_pos = next_anchor + 1
                    anchor_idx += 1
                    break
                if pos < current_pos:
                    continue
                # 윈도우 10은 윈도우 9 진입·불일치인 경우에만
                if window_size == 10 and not w9_entered_but_mismatch:
                    continue

                total_steps += 1
                actual = grid_string[pos]
                prefix_len = window_size - 1
                prefix = grid_string[pos - prefix_len : pos]

                if window_size == 9:
                    # 윈도우 9: 진입 판정만 적용 (빈도·가중치 둘 다 조회)
                    q = """
                        SELECT method, predicted_value, confidence, b_ratio, p_ratio
                        FROM simulation_predictions_change_point
                        WHERE window_size = ? AND prefix = ? AND threshold = ?
                    """
                    df_pred = pd.read_sql_query(q, pred_conn, params=[window_size, prefix, threshold])
                    by_method = {}
                    for _, row in df_pred.iterrows():
                        by_method[row["method"]] = row
                    freq_row = by_method.get("빈도 기반")
                    weight_row = by_method.get("가중치 기반")
                    if freq_row is None or weight_row is None:
                        total_skipped += 1
                        skip_reason = "예측 테이블에 값 없음" if (freq_row is None and weight_row is None) else "빈도/가중치 중 하나 없음"
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
                        })
                        current_pos = pos + 1
                        anchor_idx += 1
                        break
                    pred_freq = freq_row["predicted_value"]
                    pred_weight = weight_row["predicted_value"]
                    conf_freq = freq_row["confidence"] if freq_row["confidence"] is not None else 0.0
                    entry_ok = (conf_freq >= min_confidence_freq and pred_freq == pred_weight)
                    if not entry_ok:
                        total_skipped += 1
                        reasons = []
                        if pred_freq != pred_weight:
                            reasons.append("빈도/가중치 예측값 불일치")
                        if conf_freq < min_confidence_freq:
                            reasons.append(f"빈도 신뢰도 {conf_freq:.1f}% < {min_confidence_freq}%")
                        skip_reason = "; ".join(reasons) if reasons else "진입 조건 미충족"
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
                        })
                        current_pos = pos + 1
                        anchor_idx += 1
                        break
                    predicted = pred_freq
                    confidence = conf_freq
                    row_used = freq_row
                else:
                    # 윈도우 10: 진입 판정 없음, 테이블 예측값만 사용 (빈도 우선, 없으면 가중치)
                    q = """
                        SELECT method, predicted_value, confidence, b_ratio, p_ratio
                        FROM simulation_predictions_change_point
                        WHERE window_size = ? AND prefix = ? AND threshold = ?
                    """
                    df_pred = pd.read_sql_query(q, pred_conn, params=[window_size, prefix, threshold])
                    by_method = {}
                    for _, row in df_pred.iterrows():
                        by_method[row["method"]] = row
                    freq_row = by_method.get("빈도 기반")
                    weight_row = by_method.get("가중치 기반")
                    if freq_row is None and weight_row is None:
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
                        last_pos = pos
                        continue
                    if freq_row is not None:
                        predicted = freq_row["predicted_value"]
                        confidence = freq_row["confidence"] if freq_row["confidence"] is not None else 0.0
                        row_used = freq_row
                    else:
                        predicted = weight_row["predicted_value"]
                        confidence = weight_row["confidence"] if weight_row["confidence"] is not None else 0.0
                        row_used = weight_row

                ok = predicted == actual
                total_predictions += 1
                if not ok:
                    consecutive_failures += 1
                    total_failures += 1
                    if consecutive_failures > max_consecutive_failures:
                        max_consecutive_failures = consecutive_failures
                    if window_size == 9:
                        w9_entered_but_mismatch = True
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
                        "b_ratio": row_used["b_ratio"],
                        "p_ratio": row_used["p_ratio"],
                    }],
                    "skipped": False,
                })
                last_pos = pos
                if ok:
                    current_pos = pos + 1
                    anchor_idx += 1
                    anchor_matched = True
                    break
            else:
                current_pos = (last_pos + 1) if last_pos is not None else (next_anchor + max(WINDOW_SIZES))
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


# ============================================================================
# 윈도우 9·10 검증 (빈도/가중치 일치·신뢰도 55%·조기 종료·앵커 중첩 시 이전 앵커만)
# ============================================================================

def validate_first_anchor_window9_10_agree55_cp(
    grid_string_id,
    cutoff_grid_string_id,
    threshold=0,
    stop_on_match=False,
    predictions_conn=None,
    min_confidence_freq=51,
    min_confidence_weight=51,
):
    """
    윈도우 9·10 검증 (agree55): 기존 시뮬레이션과 별도 규칙.
    - 앵커 0부터 순차적으로 검증 (current_pos 기반 건너뛰기 없음).
    - 종료 포지션(pos) 기준 가장 오래된 순: 앵커 순으로, 앵커당 윈도우 9 → 10만 검증.
    - 빈도/가중치 일치·빈도·가중치 신뢰도 모두 임계값 이상 시만 예측 사용, 미충족 시 스킵.
    - 조기 종료: 윈도우 9 적중 시 해당 앵커에서 윈도우 10 생략.
    - 앵커 중첩 시 이전 앵커만 검증(validated_positions로 같은 position 재검증 안 함).
    - threshold: 테이블 조회 시 0 고정.
    """
    WINDOW_SIZES = (9, 10)
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

        # agree55 전용: 앵커 0부터 순차, 종료 포지션 오래된 순(앵커당 9→10)
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
                    SELECT method, predicted_value, confidence, b_ratio, p_ratio
                    FROM simulation_predictions_change_point
                    WHERE window_size = ? AND prefix = ? AND threshold = ?
                """
                df_pred = pd.read_sql_query(q, pred_conn, params=[window_size, prefix, threshold])
                by_method = {}
                for _, row in df_pred.iterrows():
                    by_method[row["method"]] = row

                freq_row = by_method.get("빈도 기반")
                weight_row = by_method.get("가중치 기반")
                if freq_row is None or weight_row is None:
                    total_skipped += 1
                    skip_reason = "예측 테이블에 값 없음" if (freq_row is None and weight_row is None) else "빈도/가중치 중 하나 없음"
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

                pred_freq = freq_row["predicted_value"]
                pred_weight = weight_row["predicted_value"]
                conf_freq = freq_row["confidence"] if freq_row["confidence"] is not None else 0.0
                conf_weight = weight_row["confidence"] if weight_row["confidence"] is not None else 0.0
                if pred_freq != pred_weight:
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
                        "skip_reason": "빈도/가중치 예측값 불일치",
                        "skipped_prediction": f"{pred_freq}/{pred_weight}",
                    })
                    validated_positions.add(pos)
                    continue
                if conf_freq < min_confidence_freq or conf_weight < min_confidence_weight:
                    total_skipped += 1
                    reasons = []
                    if conf_freq < min_confidence_freq:
                        reasons.append(f"빈도 {conf_freq:.1f}% < {min_confidence_freq}%")
                    if conf_weight < min_confidence_weight:
                        reasons.append(f"가중치 {conf_weight:.1f}% < {min_confidence_weight}%")
                    skip_reason = "신뢰도 부족 (" + ", ".join(reasons) + ")"
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
                        "skipped_prediction": pred_freq,
                    })
                    validated_positions.add(pos)
                    continue

                predicted = pred_freq
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

                if ok and window_size == 9:
                    anchor_matched = True
                    break
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


# ============================================================================
# 윈도우 9 전용 (빈도 51.3% + 시뮬레이션 승률 50%)
# ============================================================================

def validate_first_anchor_window9_freq518_win50_cp(
    grid_string_id,
    cutoff_grid_string_id,
    threshold=0,
    stop_on_match=False,
    predictions_conn=None,
    min_confidence_freq=51.3,
    min_win_rate_pct=50,
):
    """
    윈도우 9만 검증. 첫 앵커부터 순차, validated_positions로 동일 position 중복 검증 방지.
    예측 사용 조건: 빈도 기반 신뢰도 ≥ min_confidence_freq(기본 51.3%) 및 시뮬레이션 승률(sim_win_rate_pct) ≥ min_win_rate_pct(기본 50%).
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


# ============================================================================
# agree55 복제 (v2): 가중치 신뢰도 70% 이상이면 무조건 가중치 예측값 사용, 나머지 규칙 동일
# ============================================================================

WEIGHT_CONFIDENCE_UNCONDITIONAL = 70  # 가중치 기반 신뢰도가 이 값 이상이면 무조건 예측값 사용


def validate_first_anchor_window9_10_agree55_v2_cp(
    grid_string_id,
    cutoff_grid_string_id,
    threshold=0,
    stop_on_match=False,
    predictions_conn=None,
    min_confidence_freq=51,
    min_confidence_weight=51,
    weight_confidence_unconditional=70,
):
    """
    agree55 v2: 다른 규칙은 동일하고, 가중치 기반 예측값의 신뢰도가 weight_confidence_unconditional 이상이면 무조건 그 예측값을 사용.
    (빈도/가중치 일치·빈도·가중치 신뢰도 임계값은 그 미만일 때만 적용)
    """
    WINDOW_SIZES = (9, 10)
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
                    SELECT method, predicted_value, confidence, b_ratio, p_ratio
                    FROM simulation_predictions_change_point
                    WHERE window_size = ? AND prefix = ? AND threshold = ?
                """
                df_pred = pd.read_sql_query(q, pred_conn, params=[window_size, prefix, threshold])
                by_method = {}
                for _, row in df_pred.iterrows():
                    by_method[row["method"]] = row

                freq_row = by_method.get("빈도 기반")
                weight_row = by_method.get("가중치 기반")
                if freq_row is None or weight_row is None:
                    total_skipped += 1
                    skip_reason = "예측 테이블에 값 없음" if (freq_row is None and weight_row is None) else "빈도/가중치 중 하나 없음"
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

                pred_freq = freq_row["predicted_value"]
                pred_weight = weight_row["predicted_value"]
                conf_freq = freq_row["confidence"] if freq_row["confidence"] is not None else 0.0
                conf_weight = weight_row["confidence"] if weight_row["confidence"] is not None else 0.0

                # v2: 가중치 신뢰도 weight_confidence_unconditional 이상이면 무조건 가중치 예측값 사용; 아니면 agree55 규칙(일치+빈도/가중치 임계값)
                use_prediction = False
                predicted = None
                confidence = 0.0
                if conf_weight >= weight_confidence_unconditional:
                    use_prediction = True
                    predicted = pred_weight
                    confidence = conf_weight
                elif pred_freq == pred_weight and conf_freq >= min_confidence_freq and conf_weight >= min_confidence_weight:
                    use_prediction = True
                    predicted = pred_freq
                    confidence = conf_freq

                if not use_prediction:
                    if pred_freq != pred_weight:
                        skip_reason = "빈도/가중치 예측값 불일치"
                        sp_val = f"{pred_freq}/{pred_weight}"
                    else:
                        reasons = []
                        if conf_freq < min_confidence_freq:
                            reasons.append(f"빈도 {conf_freq:.1f}% < {min_confidence_freq}%")
                        if conf_weight < min_confidence_weight:
                            reasons.append(f"가중치 {conf_weight:.1f}% < {min_confidence_weight}%")
                        skip_reason = "신뢰도 부족 (" + ", ".join(reasons) + ")"
                        sp_val = pred_freq
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
                        "skip_reason": skip_reason,
                        "skipped_prediction": sp_val,
                    })
                    validated_positions.add(pos)
                    continue

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

                if ok and window_size == 9:
                    anchor_matched = True
                    break
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


# ============================================================================
# agree55 v3: 진입 판정(빈도≥51.5%·가중치일치)·조건부 즉시 종료·윈도우10 확장 검증
# ============================================================================

def validate_first_anchor_window9_10_agree55_v3_cp(
    grid_string_id,
    cutoff_grid_string_id,
    threshold=0,
    stop_on_match=False,
    predictions_conn=None,
    min_confidence_freq=51.5,
    min_confidence_weight=51,
    weight_confidence_unconditional=70,
):
    """
    agree55 v3 규칙:
    1) 첫 앵커부터, 성공/실패/조건미달 시 다음 앵커로 이동
    2) 앵커당 윈도우 9 먼저 검사
    3) 진입 판정: 윈도우 9에서 [빈도 신뢰도 ≥ min_confidence_freq AND 빈도·가중치 예측값 일치]일 때만 예측 사용
    4) 윈도우 9가 규칙 3 미충족 시 윈도우 10 생략, 해당 앵커 즉시 종료
    5) 윈도우 9가 규칙 3 충족했으나 불일치인 경우에만 윈도우 10 확장 검증
    6) 앵커 순차·동일 포지션 중첩 검증 방지(validated_positions)
    """
    WINDOW_SIZES = (9, 10)
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
            w9_entered_but_mismatch = False  # 규칙 5: 윈도우 9 진입 후 불일치일 때만 윈도우 10 진행

            for window_size in WINDOW_SIZES:
                pos = next_anchor + window_size - 1
                if pos >= len(grid_string):
                    break
                if pos in validated_positions:
                    continue
                # 규칙 5: 윈도우 10은 윈도우 9가 진입·불일치인 경우에만
                if window_size == 10 and not w9_entered_but_mismatch:
                    continue

                total_steps += 1
                actual = grid_string[pos]
                prefix_len = window_size - 1
                prefix = grid_string[pos - prefix_len : pos]

                q = """
                    SELECT method, predicted_value, confidence, b_ratio, p_ratio
                    FROM simulation_predictions_change_point
                    WHERE window_size = ? AND prefix = ? AND threshold = ?
                """
                df_pred = pd.read_sql_query(q, pred_conn, params=[window_size, prefix, threshold])
                by_method = {}
                for _, row in df_pred.iterrows():
                    by_method[row["method"]] = row

                freq_row = by_method.get("빈도 기반")
                weight_row = by_method.get("가중치 기반")
                if freq_row is None or weight_row is None:
                    total_skipped += 1
                    skip_reason = "예측 테이블에 값 없음" if (freq_row is None and weight_row is None) else "빈도/가중치 중 하나 없음"
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
                    if window_size == 9:
                        break
                    continue

                pred_freq = freq_row["predicted_value"]
                pred_weight = weight_row["predicted_value"]
                conf_freq = freq_row["confidence"] if freq_row["confidence"] is not None else 0.0
                conf_weight = weight_row["confidence"] if weight_row["confidence"] is not None else 0.0

                # v3 규칙 3: 진입 판정 — 빈도 신뢰도 ≥ min_confidence_freq AND 빈도·가중치 예측값 일치
                entry_ok = (conf_freq >= min_confidence_freq and pred_freq == pred_weight)

                if not entry_ok:
                    total_skipped += 1
                    reasons = []
                    if pred_freq != pred_weight:
                        reasons.append("빈도/가중치 예측값 불일치")
                    if conf_freq < min_confidence_freq:
                        reasons.append(f"빈도 신뢰도 {conf_freq:.1f}% < {min_confidence_freq}%")
                    skip_reason = "; ".join(reasons) if reasons else "진입 조건 미충족"
                    sp_val = f"{pred_freq}/{pred_weight}" if pred_freq != pred_weight else pred_freq
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
                        "skipped_prediction": sp_val,
                    })
                    validated_positions.add(pos)
                    if window_size == 9:
                        break
                    continue

                predicted = pred_freq
                confidence = conf_freq
                ok = predicted == actual
                total_predictions += 1
                if not ok:
                    consecutive_failures += 1
                    total_failures += 1
                    if consecutive_failures > max_consecutive_failures:
                        max_consecutive_failures = consecutive_failures
                    if window_size == 9:
                        w9_entered_but_mismatch = True
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

                if ok and window_size == 9:
                    anchor_matched = True
                    break

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


def validate_first_anchor_window9_10_cp_from_string(
    grid_string: str,
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
):
    """grid_string 직접 입력 시 윈도우 9·10 검증. validate_first_anchor_window9_10_cp와 동일 로직."""
    WINDOW_SIZES = (9, 10)
    empty = {
        "grid_string": grid_string if isinstance(grid_string, str) else "",
        "max_consecutive_failures": 0,
        "total_steps": 0,
        "total_failures": 0,
        "total_predictions": 0,
        "total_skipped": 0,
        "accuracy": 0.0,
        "history": [],
        "stopped_early": False,
    }
    if not grid_string or not isinstance(grid_string, str):
        return {**empty, "grid_string": grid_string or ""}
    if len(grid_string) < min(WINDOW_SIZES):
        return {**empty, "grid_string": grid_string}
    anchors = []
    for i in range(len(grid_string) - 1):
        if grid_string[i] != grid_string[i + 1]:
            anchors.append(i)
    anchors = sorted(list(set(anchors)))
    if not anchors:
        return {**empty, "grid_string": grid_string}
    conn = get_change_point_db_connection()
    try:
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0
        stopped_early = False
        current_pos = 0
        anchor_idx = 0
        while current_pos < len(grid_string) and anchor_idx < len(anchors):
            while anchor_idx < len(anchors) and anchors[anchor_idx] < current_pos:
                anchor_idx += 1
            if anchor_idx >= len(anchors):
                break
            next_anchor = anchors[anchor_idx]
            anchor_matched = False
            last_pos = None
            for window_size in WINDOW_SIZES:
                pos = next_anchor + window_size - 1
                if pos >= len(grid_string):
                    current_pos = next_anchor + 1
                    anchor_idx += 1
                    break
                if pos < current_pos:
                    continue
                total_steps += 1
                actual = grid_string[pos]
                prefix_len = window_size - 1
                prefix = grid_string[pos - prefix_len : pos]
                q = """
                    SELECT predicted_value, confidence, b_ratio, p_ratio
                    FROM simulation_predictions_change_point
                    WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                    LIMIT 1
                """
                df_pred = pd.read_sql_query(q, conn, params=[window_size, prefix, method, threshold])
                if len(df_pred) == 0:
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
                    last_pos = pos
                    continue
                row = df_pred.iloc[0]
                predicted = row["predicted_value"]
                confidence = row["confidence"]
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
                        "b_ratio": row["b_ratio"],
                        "p_ratio": row["p_ratio"],
                    }],
                    "skipped": False,
                })
                last_pos = pos
                if ok:
                    current_pos = pos + 1
                    anchor_idx += 1
                    anchor_matched = True
                    break
            else:
                current_pos = (last_pos + 1) if last_pos is not None else (next_anchor + max(WINDOW_SIZES))
                anchor_idx += 1
            if stop_on_match and anchor_matched:
                stopped_early = True
                break
        acc = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        return {
            "grid_string": grid_string,
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


def validate_first_anchor_window9_only_cp_from_string(
    grid_string: str,
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
):
    """grid_string 직접 입력 시 윈도우 9 전용 검증. 전체 스트링의 모든 앵커에 대해 윈도우 9 검증. validate_first_anchor_window9_only_cp와 동일 로직."""
    WINDOW_SIZE = 9
    if not grid_string or not isinstance(grid_string, str):
        return {
            "grid_string": grid_string or "",
            "max_consecutive_failures": 0,
            "total_steps": 0,
            "total_failures": 0,
            "total_predictions": 0,
            "total_skipped": 0,
            "accuracy": 0.0,
            "history": [],
            "stopped_early": False,
        }
    if len(grid_string) < WINDOW_SIZE:
        return {
            "grid_string": grid_string,
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
            "grid_string": grid_string,
            "max_consecutive_failures": 0,
            "total_steps": 0,
            "total_failures": 0,
            "total_predictions": 0,
            "total_skipped": 0,
            "accuracy": 0.0,
            "history": [],
            "stopped_early": False,
        }
    conn = get_change_point_db_connection()
    try:
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0
        stopped_early = False
        current_pos = 0
        anchor_idx = 0
        while current_pos < len(grid_string) and anchor_idx < len(anchors):
            while anchor_idx < len(anchors) and anchors[anchor_idx] < current_pos:
                anchor_idx += 1
            if anchor_idx >= len(anchors):
                break
            next_anchor = anchors[anchor_idx]
            pos = next_anchor + WINDOW_SIZE - 1
            if pos >= len(grid_string):
                anchor_idx += 1
                continue
            if pos < current_pos:
                anchor_idx += 1
                continue
            total_steps += 1
            actual = grid_string[pos]
            prefix_len = WINDOW_SIZE - 1
            prefix = grid_string[pos - prefix_len : pos]
            q = """
                SELECT predicted_value, confidence, b_ratio, p_ratio
                FROM simulation_predictions_change_point
                WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                LIMIT 1
            """
            df_pred = pd.read_sql_query(q, conn, params=[WINDOW_SIZE, prefix, method, threshold])
            if len(df_pred) == 0:
                total_skipped += 1
                history.append({
                    "step": total_steps,
                    "position": pos,
                    "anchor": next_anchor,
                    "window_size": WINDOW_SIZE,
                    "prefix": prefix,
                    "predicted": None,
                    "actual": actual,
                    "is_correct": None,
                    "confidence": 0.0,
                    "selected_window_size": WINDOW_SIZE,
                    "all_predictions": [],
                    "skipped": True,
                    "skip_reason": "예측 테이블에 값 없음",
                })
                current_pos = pos + 1
                anchor_idx += 1
                continue
            row = df_pred.iloc[0]
            predicted = row["predicted_value"]
            confidence = row["confidence"]
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
                "anchor": next_anchor,
                "window_size": WINDOW_SIZE,
                "prefix": prefix,
                "predicted": predicted,
                "actual": actual,
                "is_correct": ok,
                "confidence": confidence,
                "selected_window_size": WINDOW_SIZE,
                "all_predictions": [{
                    "window_size": WINDOW_SIZE,
                    "prefix": prefix,
                    "predicted": predicted,
                    "confidence": confidence,
                    "b_ratio": row["b_ratio"],
                    "p_ratio": row["p_ratio"],
                }],
                "skipped": False,
            })
            current_pos = pos + 1
            anchor_idx += 1
            if stop_on_match and ok:
                stopped_early = True
                break
        acc = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        return {
            "grid_string": grid_string,
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


def validate_first_anchor_extended_window_v3_cp_from_string(
    grid_string: str,
    window_sizes=(9, 10, 11, 12, 13, 14),
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
):
    """
    grid_string을 직접 입력받아 V3 검증 수행.
    validate_first_anchor_extended_window_v3_cp와 동일한 로직.
    테스트/앱에서 grid_string 입력 시 사용.

    Returns:
        dict: validate_first_anchor_extended_window_v3_cp와 동일한 구조
              (grid_string_id 대신 "grid_string" 필드 포함)
    """
    if not grid_string or not isinstance(grid_string, str):
        return {
            "grid_string": grid_string or "",
            "max_consecutive_failures": 0,
            "total_steps": 0,
            "total_failures": 0,
            "total_predictions": 0,
            "total_skipped": 0,
            "accuracy": 0.0,
            "history": [],
            "stopped_early": False,
        }
    max_ws = max(window_sizes)
    if len(grid_string) < max_ws:
        return {
            "grid_string": grid_string,
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
        if grid_string[i] != grid_string[i + 1]:
            anchors.append(i)
    anchors = sorted(list(set(anchors)))

    if not anchors:
        return {
            "grid_string": grid_string,
            "max_consecutive_failures": 0,
            "total_steps": 0,
            "total_failures": 0,
            "total_predictions": 0,
            "total_skipped": 0,
            "accuracy": 0.0,
            "history": [],
            "stopped_early": False,
        }

    conn = get_change_point_db_connection()
    try:
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0
        stopped_early = False
        current_pos = 0
        MAX_CONSECUTIVE_FAILURES = 3
        anchor_idx = 0

        while current_pos < len(grid_string) and anchor_idx < len(anchors):
            while anchor_idx < len(anchors) and anchors[anchor_idx] < current_pos:
                anchor_idx += 1
            if anchor_idx >= len(anchors):
                break

            next_anchor = anchors[anchor_idx]
            anchor_consecutive_failures = 0
            anchor_success = False
            last_mismatched_pos = None
            anchor_processed_any = False

            for window_size in window_sizes:
                pos = next_anchor + window_size - 1
                if pos >= len(grid_string):
                    break
                if pos < current_pos:
                    continue

                total_steps += 1
                actual = grid_string[pos]
                prefix_len = window_size - 1
                prefix = grid_string[pos - prefix_len : pos]

                q = """
                    SELECT predicted_value, confidence, b_ratio, p_ratio
                    FROM simulation_predictions_change_point
                    WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                    LIMIT 1
                """
                df_pred = pd.read_sql_query(q, conn, params=[window_size, prefix, method, threshold])

                if len(df_pred) == 0:
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
                    continue

                anchor_processed_any = True
                row = df_pred.iloc[0]
                predicted = row["predicted_value"]
                confidence = row["confidence"]
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

                if ok:
                    current_pos = pos + 1
                    anchor_idx += 1
                    break

                if anchor_consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    if last_mismatched_pos is not None:
                        current_pos = last_mismatched_pos + 1
                    else:
                        current_pos = pos + 1
                    anchor_idx += 1
                    break

            if not anchor_success and anchor_consecutive_failures < MAX_CONSECUTIVE_FAILURES:
                if anchor_processed_any and last_mismatched_pos is not None:
                    current_pos = last_mismatched_pos + 1
                elif anchor_processed_any:
                    max_pos = min(next_anchor + max(window_sizes) - 1, len(grid_string) - 1)
                    current_pos = max_pos + 1
                else:
                    max_pos = min(next_anchor + max(window_sizes) - 1, len(grid_string) - 1)
                    if max_pos >= current_pos:
                        current_pos = max_pos + 1
                    else:
                        current_pos = len(grid_string)
                anchor_idx += 1

            if stop_on_match and anchor_success:
                stopped_early = True
                break

        acc = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        return {
            "grid_string": grid_string,
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


# ============================================================================
# 첫 앵커 확장 윈도우 V3 + 라이브 게임형 다음 앵커 선택 (신규 가설)
# ============================================================================
# 차이: [REQ-101] 대신 [REQ-101-LIVE] 사용.
# - V3: current_pos 이후의 가장 빠른 앵커 (anchor_position >= current_pos)
# - 본 함수: current_pos를 예측 가능한 가장 낮은 앵커 (anchor + max_ws - 1 >= current_pos)
# 자세한 차이는 change_point/라이브게임_V3_다음앵커_로직_차이.md 참고.


def validate_first_anchor_extended_window_v3_live_next_anchor_cp(
    grid_string_id,
    cutoff_grid_string_id,
    window_sizes=(9, 10, 11, 12, 13, 14),
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
    predictions_conn=None,
):
    """
    첫 앵커 확장 윈도우 V3 + 라이브 게임형 다음 앵커 선택 검증 함수
    
    V3와 동일한 [REQ-102], [RULE-1], [RULE-2] 적용.
    **다음 앵커 선택만** 라이브 게임 방식으로 변경:
    
    [REQ-101-LIVE] current_pos를 예측 가능한 가장 낮은 앵커를 검증 대상으로 선정
    - skip 조건: anchors[anchor_idx] + max_ws - 1 < current_pos
    - (V3는 anchors[anchor_idx] < current_pos)
    
    추가: 문자열 길이로 for가 끊긴 경우 앵커를 바꾸지 않고 종료 (exit_for_pos_beyond).
    
    Args/Returns: validate_first_anchor_extended_window_v3_cp 와 동일.
    """
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
        current_pos = 0
        anchor_idx = 0
        MAX_CONSECUTIVE_FAILURES = 3

        while current_pos < len(grid_string) and anchor_idx < len(anchors):
            # [REQ-101-LIVE] 해당 앵커가 커버할 수 있는 최대 position을 이미 지났을 때만 skip
            while anchor_idx < len(anchors) and anchors[anchor_idx] + max_ws - 1 < current_pos:
                anchor_idx += 1
            if anchor_idx >= len(anchors):
                break

            next_anchor = anchors[anchor_idx]
            anchor_consecutive_failures = 0
            anchor_success = False
            last_mismatched_pos = None
            anchor_processed_any = False
            exit_for_pos_beyond = False

            for window_size in window_sizes:
                pos = next_anchor + window_size - 1
                if pos >= len(grid_string):
                    exit_for_pos_beyond = True
                    break
                if pos < current_pos:
                    continue

                total_steps += 1
                actual = grid_string[pos]
                prefix_len = window_size - 1
                prefix = grid_string[pos - prefix_len : pos]

                q = """
                    SELECT predicted_value, confidence, b_ratio, p_ratio
                    FROM simulation_predictions_change_point
                    WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?
                    LIMIT 1
                """
                df_pred = pd.read_sql_query(q, pred_conn, params=[window_size, prefix, method, threshold])

                if len(df_pred) == 0:
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
                    continue

                anchor_processed_any = True
                row = df_pred.iloc[0]
                predicted = row["predicted_value"]
                confidence = row["confidence"]
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

                if ok:
                    current_pos = pos + 1
                    anchor_idx += 1
                    break
                if anchor_consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    current_pos = (last_mismatched_pos + 1) if last_mismatched_pos is not None else (pos + 1)
                    anchor_idx += 1
                    break

            if exit_for_pos_beyond:
                break
            if not anchor_success and anchor_consecutive_failures < MAX_CONSECUTIVE_FAILURES:
                if anchor_processed_any and last_mismatched_pos is not None:
                    current_pos = last_mismatched_pos + 1
                elif anchor_processed_any:
                    max_pos = min(next_anchor + max(window_sizes) - 1, len(grid_string) - 1)
                    current_pos = max_pos + 1
                else:
                    max_pos = min(next_anchor + max(window_sizes) - 1, len(grid_string) - 1)
                    current_pos = max_pos + 1 if max_pos >= current_pos else len(grid_string)
                anchor_idx += 1

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


def create_simulation_predictions_change_point_table(conn=None):
    """
    시뮬레이션 전용 예측 테이블 생성 (기존 테이블 삭제 후 재생성).
    주의: 기존 데이터가 모두 삭제됩니다. 빈도만 갱신하려면 ensure_simulation_predictions_change_point_table 사용.
    """
    own_conn = False
    if conn is None:
        conn = get_change_point_db_connection()
        own_conn = True
    cursor = conn.cursor()
    try:
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
                pred_frequency REAL,
                sim_win_rate_pct REAL,
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
        if own_conn and conn is not None:
            conn.close()


def ensure_simulation_predictions_change_point_table(conn=None):
    """
    시뮬레이션 전용 예측 테이블이 존재하는지 확인하고 없으면 생성.
    기존 테이블은 유지하며, 누락된 컬럼(pred_frequency, sim_win_rate_pct)만 추가.
    가중치 기반 등 기존 데이터를 덮어쓰지 않음.
    """
    own_conn = False
    if conn is None:
        conn = get_change_point_db_connection()
        own_conn = True
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS simulation_predictions_change_point (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                window_size INTEGER NOT NULL,
                prefix TEXT NOT NULL,
                predicted_value TEXT,
                confidence REAL,
                b_ratio REAL,
                p_ratio REAL,
                method TEXT NOT NULL,
                threshold REAL NOT NULL,
                pred_frequency REAL,
                sim_win_rate_pct REAL,
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
        cursor.execute("PRAGMA table_info(simulation_predictions_change_point)")
        cols = [row[1] for row in cursor.fetchall()]
        if "pred_frequency" not in cols:
            cursor.execute(
                "ALTER TABLE simulation_predictions_change_point ADD COLUMN pred_frequency REAL"
            )
        if "sim_win_rate_pct" not in cols:
            cursor.execute(
                "ALTER TABLE simulation_predictions_change_point ADD COLUMN sim_win_rate_pct REAL"
            )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        if own_conn and conn is not None:
            conn.close()


def get_simulation_predictions_change_point_count(conn=None):
    """
    simulation_predictions_change_point 테이블의 레코드 수를 반환.
    시뮬레이션 실행 시 테이블이 비어 있지 않으면 테이블 생성을 생략해도 됨.
    """
    own_conn = False
    if conn is None:
        conn = get_change_point_db_connection()
        own_conn = True
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM simulation_predictions_change_point"
        )
        return cur.fetchone()[0] if cur else 0
    except Exception:
        return 0
    finally:
        if own_conn and conn is not None:
            conn.close()


def save_predictions_to_simulation_table(
    cutoff_grid_string_id=None,
    window_sizes=(9, 10, 11, 12, 13, 14),
    methods=("빈도 기반",),
    thresholds=(0,),
    batch_size=1000,
    min_sample_count=15,
    predictions_conn=None,
    win_rate_map=None,
):
    """
    시뮬레이션 전용 테이블에 예측값 저장.
    지정한 methods에 해당하는 행만 INSERT/REPLACE하며, 다른 method 행은 건드리지 않음.

    - cutoff 이전(id <= cutoff) grid_string으로만 학습
    - simulation_predictions_change_point 테이블에 저장

    Args:
        cutoff_grid_string_id: cutoff ID (이 ID 이전 = 학습 데이터)
        window_sizes: 윈도우 크기 목록
        methods: 예측 방법 목록 (예: ("빈도 기반",) 만 넣으면 가중치 기반 등 기존 행 유지)
        thresholds: 임계값 목록
        batch_size: 배치 크기
        min_sample_count: 최소 표본 수 필터
        predictions_conn: 예측 저장용 DB 연결. None이면 change_point_ngram.db 사용.
        win_rate_map: (window_size, prefix) -> 시뮬레이션 승률(%). None이면 sim_win_rate_pct는 NULL.

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
    
    main_conn = get_change_point_db_connection()
    pred_conn = predictions_conn if predictions_conn is not None else main_conn
    own_pred_conn = predictions_conn is not None
    try:
        if cutoff_grid_string_id is None:
            q = "SELECT id FROM preprocessed_grid_strings ORDER BY id"
            params = []
        else:
            q = "SELECT id FROM preprocessed_grid_strings WHERE id <= ? ORDER BY id"
            params = [cutoff_grid_string_id]
        df_hist = pd.read_sql_query(q, main_conn, params=params)
        if len(df_hist) == 0:
            return {"total_saved": 0, "new_records": 0, "updated_records": 0, "unique_prefixes": 0}

        historical_ids = df_hist["id"].tolist()
        total_saved = 0
        new_records = 0
        updated_records = 0
        unique_prefixes_set = set()
        cursor = pred_conn.cursor()
        # 기존 테이블에 pred_frequency, sim_win_rate_pct 컬럼이 없으면 추가 (구 스키마 마이그레이션)
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='simulation_predictions_change_point'"
        )
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(simulation_predictions_change_point)")
            cols = [row[1] for row in cursor.fetchall()]
            if "pred_frequency" not in cols:
                cursor.execute(
                    "ALTER TABLE simulation_predictions_change_point ADD COLUMN pred_frequency REAL"
                )
            if "sim_win_rate_pct" not in cols:
                cursor.execute(
                    "ALTER TABLE simulation_predictions_change_point ADD COLUMN sim_win_rate_pct REAL"
                )
            pred_conn.commit()

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

                def _norm_prefix(p):
                    return (str(p).strip() if p is not None else "")

                for prefix in all_prefixes:
                    unique_prefixes_set.add((window_size, prefix))
                    pred_freq = sum(model[prefix].values()) if prefix in model and model[prefix] else None
                    # 승률 조회 시 prefix 정규화(공백 제거)로 step_events 쪽 키와 동일하게 매칭
                    sim_wr = (win_rate_map.get((window_size, _norm_prefix(prefix))) if win_rate_map else None)
                    for threshold in thresholds:
                        if threshold == 0:
                            res = predict_for_prefix(model, prefix, method)
                        else:
                            res = predict_confidence_threshold(model, prefix, method, threshold)
                        pred = res.get("predicted")
                        if pred is None and threshold != 0:
                            raw = predict_for_prefix(model, prefix, method)
                            pred = raw.get("predicted")
                        conf = res.get("confidence", 0.0)
                        ratios = res.get("ratios", {})
                        b_ratio = ratios.get("b", 0.0)
                        p_ratio = ratios.get("p", 0.0)
                        batch_data.append((window_size, prefix, pred, conf, b_ratio, p_ratio, method, threshold, pred_freq, sim_wr))

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
                                (window_size, prefix, predicted_value, confidence, b_ratio, p_ratio, method, threshold, pred_frequency, sim_win_rate_pct, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
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

        pred_conn.commit()
        return {
            "total_saved": total_saved,
            "new_records": new_records,
            "updated_records": updated_records,
            "unique_prefixes": len(unique_prefixes_set),
        }
    except Exception:
        pred_conn.rollback()
        raise
    finally:
        main_conn.close()


def generate_simulation_predictions_table(
    cutoff_grid_string_id,
    window_sizes=(9, 10, 11, 12, 13, 14),
    method="빈도 기반",
    threshold=0,
    min_sample_count=15,
    methods=None,
    use_isolated_sim_db=False,
    include_weighted=False,
):
    """
    시뮬레이션 전용 예측 테이블에 빈도 기반(필수) 및 optionally 가중치 기반 저장.
    기존 테이블을 지우지 않아 다른 method 행은 유지됨.
    시뮬레이션 승률을 sim_win_rate_pct 컬럼에 함께 저장.

    **사용 방식:**
    1. 이 함수를 먼저 실행하여 예측값 갱신 (테이블 생성/유지)
    2. 이후 batch_validate_* 실행하여 검증

    Args:
        cutoff_grid_string_id: cutoff ID (이 ID 이전 = 학습 데이터)
        window_sizes: 윈도우 크기 목록
        method: 무시됨.
        threshold: 임계값 (예측값 생성 시 사용)
        min_sample_count: 최소 표본 수 필터
        methods: None이면 include_weighted에 따라 결정. 지정 시 그대로 사용.
        use_isolated_sim_db: True이면 simulation_predictions.db 사용.
        include_weighted: True이면 ("빈도 기반", "가중치 기반") 저장, False이면 ("빈도 기반",) 만.

    Returns:
        dict: 저장 결과 통계
    """
    from results_storage import (
        compute_step_events_prefix_win_rate,
        get_default_results_db_path,
        get_step_events_nonskipped_count,
    )

    if methods is not None:
        methods = tuple(methods)
    else:
        methods = ("빈도 기반", "가중치 기반") if include_weighted else ("빈도 기반",)
    pred_conn = get_simulation_predictions_db_connection() if use_isolated_sim_db else None
    step_events_db_path = get_default_results_db_path()
    try:
        ensure_simulation_predictions_change_point_table(conn=pred_conn)
        # step_events 원본을 읽어 스킵하지 않은 스텝만 취합 후 (window_size, prefix)별 일치 여부로 승률 계산
        # 계산한 승률을 simulation_predictions_change_point 테이블의 sim_win_rate_pct에 저장
        try:
            win_rate_map = compute_step_events_prefix_win_rate(
                tuple(window_sizes), db_path=step_events_db_path
            )
            win_rate_map = win_rate_map if win_rate_map else None
        except Exception as e:
            import warnings
            warnings.warn(
                f"시뮬레이션 승률 계산 실패 → sim_win_rate_pct는 NULL로 저장됩니다. "
                f"results_db의 step_events에 데이터가 있는지 확인하세요. 오류: {e}"
            )
            win_rate_map = None
        pred_result = save_predictions_to_simulation_table(
            cutoff_grid_string_id=cutoff_grid_string_id,
            window_sizes=window_sizes,
            methods=methods,
            thresholds=(threshold,),
            min_sample_count=min_sample_count,
            predictions_conn=pred_conn,
            win_rate_map=win_rate_map,
        )
        pred_result["win_rate_entries"] = len(win_rate_map) if win_rate_map else 0
        pred_result["step_events_db_path"] = step_events_db_path
        try:
            pred_result["step_events_nonskipped_count"] = get_step_events_nonskipped_count(
                tuple(window_sizes), db_path=step_events_db_path
            )
        except Exception:
            pred_result["step_events_nonskipped_count"] = None
        return pred_result
    except Exception as e:
        import warnings
        warnings.warn(f"예측값 생성 실패: {e}")
        raise
    finally:
        if pred_conn is not None:
            pred_conn.close()


def batch_validate_first_anchor_extended_window_v3_cp(
    cutoff_grid_string_id,
    window_sizes=(9, 10, 11, 12, 13, 14),
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
    end_grid_string_id=None,
):
    """
    첫 앵커 확장 윈도우 V3 배치 검증 (cutoff 이후 grid_string)
    
    **예측값 사용 방식:**
    1. 시뮬레이션 전용 테이블 (simulation_predictions_change_point)에서 예측값 조회
    2. cutoff 이후(및 end_grid_string_id 이하) 데이터(검증 데이터) 검증
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
        # cutoff 이후(및 end 이하) grid_string ID 조회 (검증 데이터)
        if end_grid_string_id is not None:
            df_test = pd.read_sql_query(
                "SELECT id FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id",
                conn,
                params=[cutoff_grid_string_id, end_grid_string_id],
            )
        else:
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
        
        # cutoff 이후(및 end 이하) 데이터를 검증
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


def batch_validate_first_anchor_window9_only_cp(
    cutoff_grid_string_id,
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
    end_grid_string_id=None,
):
    """
    첫 앵커 윈도우 9 전용 배치 검증.
    validate_first_anchor_window9_only_cp를 cutoff 이후(및 end 이하) grid_string에 대해 호출.
    simulation_predictions_change_point 사용.
    """
    conn = get_change_point_db_connection()
    try:
        if end_grid_string_id is not None:
            df_test = pd.read_sql_query(
                "SELECT id FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id",
                conn,
                params=[cutoff_grid_string_id, end_grid_string_id],
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
            r = validate_first_anchor_window9_only_cp(
                gid, cutoff_grid_string_id,
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


def batch_validate_first_anchor_window9_10_cp(
    cutoff_grid_string_id,
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
    end_grid_string_id=None,
):
    """
    윈도우 9·10 배치 검증.
    validate_first_anchor_window9_10_cp를 cutoff 이후(및 end 이하) grid_string에 대해 호출.
    simulation_predictions_change_point 사용.
    """
    conn = get_change_point_db_connection()
    try:
        if end_grid_string_id is not None:
            df_test = pd.read_sql_query(
                "SELECT id FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id",
                conn,
                params=[cutoff_grid_string_id, end_grid_string_id],
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
            r = validate_first_anchor_window9_10_cp(
                gid, cutoff_grid_string_id,
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


def batch_validate_first_anchor_window9_10_v2_cp(
    cutoff_grid_string_id,
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
    min_confidence=51.5,
    end_grid_string_id=None,
):
    """
    윈도우 9·10 v2 배치 검증.
    validate_first_anchor_window9_10_v2_cp를 cutoff 이후(및 end 이하) grid_string에 대해 호출.
    min_confidence: 선택한 예측방법 신뢰도가 이 값 이하이면 스킵.
    """
    conn = get_change_point_db_connection()
    try:
        if end_grid_string_id is not None:
            df_test = pd.read_sql_query(
                "SELECT id FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id",
                conn,
                params=[cutoff_grid_string_id, end_grid_string_id],
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
            r = validate_first_anchor_window9_10_v2_cp(
                gid,
                cutoff_grid_string_id,
                method=method,
                threshold=threshold,
                stop_on_match=stop_on_match,
                min_confidence=min_confidence,
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


def batch_validate_first_anchor_window9_10_v3_cp(
    cutoff_grid_string_id,
    threshold=0,
    stop_on_match=False,
    min_confidence_freq=51.5,
    end_grid_string_id=None,
):
    """
    윈도우 9·10 v3 배치 검증. 진입 판정(빈도≥51.5%·가중치일치)·조기종료·연쇄검증.
    validate_first_anchor_window9_10_v3_cp를 cutoff 이후(및 end 이하) grid_string에 대해 호출.
    simulation_predictions_change_point 사용 (빈도·가중치 둘 다 필요).
    """
    conn = get_change_point_db_connection()
    try:
        if end_grid_string_id is not None:
            df_test = pd.read_sql_query(
                "SELECT id FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id",
                conn,
                params=[cutoff_grid_string_id, end_grid_string_id],
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
            r = validate_first_anchor_window9_10_v3_cp(
                gid, cutoff_grid_string_id,
                threshold=threshold,
                stop_on_match=stop_on_match,
                min_confidence_freq=min_confidence_freq,
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


def batch_validate_first_anchor_window9_10_agree55_cp(
    cutoff_grid_string_id,
    threshold=0,
    stop_on_match=False,
    min_confidence_freq=51,
    min_confidence_weight=51,
    end_grid_string_id=None,
):
    """
    윈도우 9·10 agree55 배치 검증.
    validate_first_anchor_window9_10_agree55_cp를 cutoff 이후(및 end 이하) grid_string에 대해 호출.
    threshold: 테이블 조회 시 사용 (테이블 생성 시 사용한 값, 보통 0).
    """
    conn = get_change_point_db_connection()
    try:
        if end_grid_string_id is not None:
            df_test = pd.read_sql_query(
                "SELECT id FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id",
                conn,
                params=[cutoff_grid_string_id, end_grid_string_id],
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
            r = validate_first_anchor_window9_10_agree55_cp(
                gid,
                cutoff_grid_string_id,
                threshold=threshold,
                stop_on_match=stop_on_match,
                min_confidence_freq=min_confidence_freq,
                min_confidence_weight=min_confidence_weight,
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


def batch_validate_first_anchor_window9_freq518_win50_cp(
    cutoff_grid_string_id,
    threshold=0,
    stop_on_match=False,
    min_confidence_freq=51.3,
    min_win_rate_pct=50,
    end_grid_string_id=None,
):
    """
    윈도우 9 (빈도 51.3% + 승률 50%) 배치 검증.
    validate_first_anchor_window9_freq518_win50_cp를 cutoff 이후(및 end 이하) grid_string에 대해 호출.
    """
    conn = get_change_point_db_connection()
    try:
        if end_grid_string_id is not None:
            df_test = pd.read_sql_query(
                "SELECT id FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id",
                conn,
                params=[cutoff_grid_string_id, end_grid_string_id],
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
            r = validate_first_anchor_window9_freq518_win50_cp(
                gid,
                cutoff_grid_string_id,
                threshold=threshold,
                stop_on_match=stop_on_match,
                min_confidence_freq=min_confidence_freq,
                min_win_rate_pct=min_win_rate_pct,
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


def batch_validate_first_anchor_window9_10_agree55_v2_cp(
    cutoff_grid_string_id,
    threshold=0,
    stop_on_match=False,
    min_confidence_freq=51,
    min_confidence_weight=51,
    weight_confidence_unconditional=70,
    end_grid_string_id=None,
):
    """
    agree55 v2 배치 검증. validate_first_anchor_window9_10_agree55_v2_cp를 cutoff 이후(및 end 이하) grid_string에 대해 호출.
    """
    conn = get_change_point_db_connection()
    try:
        if end_grid_string_id is not None:
            df_test = pd.read_sql_query(
                "SELECT id FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id",
                conn,
                params=[cutoff_grid_string_id, end_grid_string_id],
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
            r = validate_first_anchor_window9_10_agree55_v2_cp(
                gid,
                cutoff_grid_string_id,
                threshold=threshold,
                stop_on_match=stop_on_match,
                min_confidence_freq=min_confidence_freq,
                min_confidence_weight=min_confidence_weight,
                weight_confidence_unconditional=weight_confidence_unconditional,
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


def batch_validate_first_anchor_window9_10_agree55_v3_cp(
    cutoff_grid_string_id,
    threshold=0,
    stop_on_match=False,
    min_confidence_freq=51.5,
    min_confidence_weight=51,
    weight_confidence_unconditional=70,
    end_grid_string_id=None,
):
    """
    agree55 v3 배치 검증. 진입 판정(빈도≥51.5%·가중치일치)·조건부 즉시 종료·윈도우10 확장 검증.
    """
    conn = get_change_point_db_connection()
    try:
        if end_grid_string_id is not None:
            df_test = pd.read_sql_query(
                "SELECT id FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id",
                conn,
                params=[cutoff_grid_string_id, end_grid_string_id],
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
            r = validate_first_anchor_window9_10_agree55_v3_cp(
                gid,
                cutoff_grid_string_id,
                threshold=threshold,
                stop_on_match=stop_on_match,
                min_confidence_freq=min_confidence_freq,
                min_confidence_weight=min_confidence_weight,
                weight_confidence_unconditional=weight_confidence_unconditional,
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


def batch_validate_first_anchor_extended_window_v3_live_next_anchor_cp(
    cutoff_grid_string_id,
    window_sizes=(9, 10, 11, 12, 13, 14),
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
    end_grid_string_id=None,
):
    """
    첫 앵커 확장 윈도우 V3 + 라이브 게임형 다음 앵커 선택 배치 검증.

    validate_first_anchor_extended_window_v3_live_next_anchor_cp 를
    cutoff 이후(및 end 이하) grid_string 에 대해 호출한 결과를 반환.
    Args/Returns: batch_validate_first_anchor_extended_window_v3_cp 와 동일 구조.
    """
    conn = get_change_point_db_connection()
    try:
        if end_grid_string_id is not None:
            df_test = pd.read_sql_query(
                "SELECT id FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id",
                conn,
                params=[cutoff_grid_string_id, end_grid_string_id],
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
            r = validate_first_anchor_extended_window_v3_live_next_anchor_cp(
                gid,
                cutoff_grid_string_id,
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
