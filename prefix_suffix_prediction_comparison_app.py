"""
Prefix Suffix 예측 비교 및 검증 앱
Prefix별로 여러 예측 방법의 결과를 비교하고, 패턴 검출 결과를 확인하며,
시계열 누적 방식으로 인터랙티브 시나리오 검증을 수행하는 독립적인 앱
"""

import streamlit as st
import sqlite3
import pandas as pd
import numpy as np
import os
from collections import Counter, defaultdict
from datetime import datetime
from math import log2
from scipy import stats
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 페이지 설정
st.set_page_config(
    page_title="Prefix Suffix Prediction Comparison",
    page_icon="📊",
    layout="wide"
)

# DB 경로 설정
DB_PATH = 'hypothesis_validation.db'

# ============================================================================
# 데이터베이스 관련 함수들 (복제)
# ============================================================================

def get_db_connection():
    """데이터베이스 연결"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), DB_PATH)
        if not os.path.exists(db_path):
            st.error(f"데이터베이스 파일을 찾을 수 없습니다: {db_path}")
            return None
        return sqlite3.connect(db_path)
    except Exception as e:
        st.error(f"데이터베이스 연결 오류: {str(e)}")
        return None

def load_preprocessed_data():
    """전처리된 데이터 로드"""
    try:
        conn = get_db_connection()
        if conn is None:
            return pd.DataFrame()
        
        query = """
            SELECT 
                id,
                source_session_id,
                source_id,
                grid_string,
                string_length,
                b_count,
                p_count,
                b_ratio,
                p_ratio,
                created_at,
                processed_at
            FROM preprocessed_grid_strings
            ORDER BY created_at ASC
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except Exception as e:
        st.error(f"데이터 로드 오류: {str(e)}")
        return pd.DataFrame()

def load_ngram_chunks(window_size=None, grid_string_ids=None):
    """N-gram 조각 로드"""
    conn = None
    try:
        conn = get_db_connection()
        if conn is None:
            return pd.DataFrame()
        
        query = """
            SELECT 
                id,
                grid_string_id,
                window_size,
                chunk_index,
                prefix,
                suffix,
                full_chunk
            FROM ngram_chunks
            WHERE 1=1
        """
        params = []
        
        if window_size is not None:
            query += " AND window_size = ?"
            params.append(window_size)
        
        if grid_string_ids is not None and len(grid_string_ids) > 0:
            # SQLite의 파라미터 제한(999개)을 고려하여 배치로 처리
            if len(grid_string_ids) > 900:
                # 배치로 나누어 처리
                all_dfs = []
                batch_size = 900
                for i in range(0, len(grid_string_ids), batch_size):
                    batch_ids = grid_string_ids[i:i + batch_size]
                    placeholders = ','.join(['?'] * len(batch_ids))
                    batch_query = query + f" AND grid_string_id IN ({placeholders})"
                    batch_query += " ORDER BY grid_string_id, window_size, chunk_index"
                    batch_df = pd.read_sql_query(batch_query, conn, params=params + batch_ids)
                    all_dfs.append(batch_df)
                
                if all_dfs:
                    df = pd.concat(all_dfs, ignore_index=True)
                else:
                    df = pd.DataFrame()
            else:
                placeholders = ','.join(['?'] * len(grid_string_ids))
                query += f" AND grid_string_id IN ({placeholders})"
                params.extend(grid_string_ids)
                query += " ORDER BY grid_string_id, window_size, chunk_index"
                df = pd.read_sql_query(query, conn, params=params)
        else:
            query += " ORDER BY grid_string_id, window_size, chunk_index"
            df = pd.read_sql_query(query, conn, params=params)
        
        return df
    except Exception as e:
        st.error(f"N-gram 조각 로드 오류: {str(e)}")
        return pd.DataFrame()
    finally:
        if conn:
            conn.close()

# ============================================================================
# 유틸리티 함수들 (복제)
# ============================================================================

def calculate_prefix_ratio(prefix):
    """
    prefix 문자열의 b/p 비율 계산
    
    Args:
        prefix: prefix 문자열 (예: "bbbbpp")
    
    Returns:
        dict: {'b_ratio': float, 'p_ratio': float, 'b_count': int, 'p_count': int}
    """
    b_count = prefix.count('b')
    p_count = prefix.count('p')
    total = len(prefix)
    
    if total == 0:
        return {'b_ratio': 0.5, 'p_ratio': 0.5, 'b_count': 0, 'p_count': 0}
    
    return {
        'b_ratio': b_count / total,
        'p_ratio': p_count / total,
        'b_count': b_count,
        'p_count': p_count
    }

def extract_prefixes_from_string(grid_string, window_size):
    """
    grid_string을 슬라이딩 윈도우로 슬라이싱하여 prefix와 suffix 추출
    
    Args:
        grid_string: 입력 문자열 (예: "bbbbppbbpp...")
        window_size: 윈도우 크기
    
    Returns:
        list: [(prefix, suffix, index), ...]
    """
    if not grid_string or len(grid_string) < window_size:
        return []
    
    prefixes = []
    for i in range(len(grid_string) - window_size + 1):
        chunk = grid_string[i:i + window_size]
        prefix = chunk[:-1]  # 앞 N-1개
        suffix = chunk[-1]   # 마지막 1개
        prefixes.append((prefix, suffix, i))
    
    return prefixes

def get_next_prefix(current_prefix, value, window_size):
    """
    현재 prefix와 선택된 값으로 다음 prefix 생성
    
    Args:
        current_prefix: 현재 prefix
        value: 선택된 값 ('b' 또는 'p')
        window_size: 윈도우 크기
    
    Returns:
        str: 다음 prefix (마지막 N-1자리)
    """
    new_string = current_prefix + value
    # 윈도우 크기가 N이면 prefix는 N-1자리
    prefix_length = window_size - 1
    if len(new_string) >= prefix_length:
        return new_string[-prefix_length:]
    return new_string

def generate_and_save_prefix_trend_rules(window_size, grid_string_ids=None):
    """
    prefix 비율 규칙 생성 및 DB 저장
    
    ngram_chunks 테이블에서 데이터를 읽어 각 prefix별로 
    b/p 비율과 suffix 분포를 분석하여 트렌드 규칙을 생성하고 저장
    
    Args:
        window_size: 윈도우 크기
        grid_string_ids: 특정 grid_string_id 목록 (None이면 전체)
    
    Returns:
        int: 저장된 규칙 수
    """
    conn = get_db_connection()
    if conn is None:
        return 0
    
    try:
        # ngram_chunks에서 데이터 로드
        if grid_string_ids is not None and len(grid_string_ids) > 0:
            if len(grid_string_ids) > 900:
                # 배치로 처리
                all_ngrams = []
                batch_size = 900
                for i in range(0, len(grid_string_ids), batch_size):
                    batch_ids = grid_string_ids[i:i + batch_size]
                    placeholders = ','.join(['?'] * len(batch_ids))
                    query = f"""
                        SELECT prefix, suffix
                        FROM ngram_chunks
                        WHERE window_size = ? AND grid_string_id IN ({placeholders})
                    """
                    batch_df = pd.read_sql_query(query, conn, params=[window_size] + batch_ids)
                    all_ngrams.append(batch_df)
                
                if all_ngrams:
                    ngrams_df = pd.concat(all_ngrams, ignore_index=True)
                else:
                    ngrams_df = pd.DataFrame()
            else:
                placeholders = ','.join(['?'] * len(grid_string_ids))
                query = f"""
                    SELECT prefix, suffix
                    FROM ngram_chunks
                    WHERE window_size = ? AND grid_string_id IN ({placeholders})
                """
                ngrams_df = pd.read_sql_query(query, conn, params=[window_size] + grid_string_ids)
        else:
            query = """
                SELECT prefix, suffix
                FROM ngram_chunks
                WHERE window_size = ?
            """
            ngrams_df = pd.read_sql_query(query, conn, params=[window_size])
        
        if len(ngrams_df) == 0:
            return 0
        
        # prefix별로 suffix 분포 분석
        prefix_analysis = defaultdict(lambda: {'b': 0, 'p': 0})
        
        for _, row in ngrams_df.iterrows():
            prefix = row['prefix']
            suffix = row['suffix']
            prefix_analysis[prefix][suffix] += 1
        
        # 규칙 계산 및 저장
        cursor = conn.cursor()
        saved_count = 0
        
        for prefix, suffix_counts in prefix_analysis.items():
            # prefix의 b/p 비율 계산
            prefix_ratio = calculate_prefix_ratio(prefix)
            b_ratio = prefix_ratio['b_ratio']
            
            # suffix 분포
            b_suffix_count = suffix_counts['b']
            p_suffix_count = suffix_counts['p']
            total_count = b_suffix_count + p_suffix_count
            
            if total_count == 0:
                continue
            
            b_suffix_ratio = b_suffix_count / total_count
            p_suffix_ratio = p_suffix_count / total_count
            
            # 규칙 결정: 트렌드 따름 vs 반대
            if b_ratio > 0.5:  # prefix에서 b가 많음
                # suffix도 b가 많으면 트렌드 따름
                trend_follow = 1 if b_suffix_ratio > p_suffix_ratio else 0
            elif b_ratio < 0.5:  # prefix에서 p가 많음
                # suffix도 p가 많으면 트렌드 따름
                trend_follow = 1 if p_suffix_ratio > b_suffix_ratio else 0
            else:  # 균형 (50%)
                # 차이가 작으면 트렌드 따름으로 간주
                trend_follow = 1 if abs(b_suffix_ratio - p_suffix_ratio) < 0.2 else 0
            
            # 신뢰도 계산
            confidence = abs(b_suffix_ratio - p_suffix_ratio)
            
            # DB에 저장 (INSERT OR REPLACE)
            cursor.execute('''
                INSERT OR REPLACE INTO prefix_trend_rules (
                    window_size, prefix, b_ratio, p_ratio,
                    b_suffix_count, p_suffix_count, total_count,
                    trend_follow, confidence, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
            ''', (
                window_size, prefix, b_ratio, prefix_ratio['p_ratio'],
                b_suffix_count, p_suffix_count, total_count,
                trend_follow, confidence
            ))
            
            if cursor.rowcount > 0:
                saved_count += 1
        
        conn.commit()
        return saved_count
        
    except Exception as e:
        conn.rollback()
        st.error(f"prefix_trend_rules 생성 및 저장 오류: {str(e)}")
        return 0
    finally:
        conn.close()

def load_prefix_trend_rules(window_size):
    """
    DB에서 prefix 비율 규칙 로드
    
    Args:
        window_size: 윈도우 크기
    
    Returns:
        dict: {prefix: {'b_ratio': float, 'p_ratio': float, 'trend_follow': bool, 'confidence': float, ...}, ...}
    """
    conn = get_db_connection()
    if conn is None:
        return {}
    
    try:
        query = """
            SELECT prefix, b_ratio, p_ratio, b_suffix_count, p_suffix_count,
                   total_count, trend_follow, confidence
            FROM prefix_trend_rules
            WHERE window_size = ?
        """
        df = pd.read_sql_query(query, conn, params=[window_size])
        
        if len(df) == 0:
            return {}
        
        rules = {}
        for _, row in df.iterrows():
            rules[row['prefix']] = {
                'b_ratio': row['b_ratio'],
                'p_ratio': row['p_ratio'],
                'b_suffix_count': int(row['b_suffix_count']),
                'p_suffix_count': int(row['p_suffix_count']),
                'total_count': int(row['total_count']),
                'trend_follow': bool(row['trend_follow']),
                'confidence': row['confidence']
            }
        
        return rules
        
    except Exception as e:
        st.error(f"prefix_trend_rules 로드 오류: {str(e)}")
        return {}
    finally:
        conn.close()

# ============================================================================
# 모델 구축 함수들 (복제)
# ============================================================================

def build_frequency_model(ngrams_df):
    """
    빈도 기반 예측 모델 구축
    
    Args:
        ngrams_df: N-gram 조각 DataFrame
    
    Returns:
        dict: {prefix: {suffix: count, ...}, ...}
    """
    model = defaultdict(lambda: Counter())
    
    for _, row in ngrams_df.iterrows():
        prefix = row['prefix']
        suffix = row['suffix']
        model[prefix][suffix] += 1
    
    return dict(model)

def build_weighted_model(ngrams_df, weight_decay=0.95):
    """
    가중치 기반 모델 구축
    최근 조각에 더 높은 가중치 부여
    
    Args:
        ngrams_df: N-gram 조각 DataFrame
        weight_decay: 가중치 감쇠율 (0~1)
    
    Returns:
        dict: {prefix: {suffix: weighted_count, ...}, ...}
    """
    model = defaultdict(lambda: defaultdict(float))
    
    # grid_string_id별로 그룹화하여 순서 보존
    grouped = ngrams_df.groupby('grid_string_id')
    
    for grid_string_id, group_df in grouped:
        # 최근 조각에 더 높은 가중치
        group_df = group_df.sort_values('chunk_index')
        max_index = len(group_df)
        
        for idx, (_, row) in enumerate(group_df.iterrows()):
            # 가중치: 최근 조각일수록 높음
            weight = weight_decay ** (max_index - idx - 1)
            
            prefix = row['prefix']
            suffix = row['suffix']
            model[prefix][suffix] += weight
    
    return dict(model)

def build_safety_first_model(ngrams_df):
    """
    안전 우선 적응형 모델 구축 (독립적 구현)
    
    빈도 기반 모델을 내부에서 직접 구축하여 기존 함수와 독립적
    
    Args:
        ngrams_df: N-gram 조각 DataFrame
    
    Returns:
        dict: {prefix: {suffix: count, ...}, ...}
    """
    model = defaultdict(lambda: Counter())
    
    for _, row in ngrams_df.iterrows():
        prefix = row['prefix']
        suffix = row['suffix']
        model[prefix][suffix] += 1
    
    return dict(model)

def build_balance_recovery_trend_model_final(ngrams_df, window_size):
    """
    균형 회복 트렌드 모델 구축 (하이브리드 방식: DB 우선, 없으면 계산 후 저장)
    
    Args:
        ngrams_df: N-gram 조각 DataFrame
        window_size: 윈도우 크기
    
    Returns:
        dict: {'prefix_rules': dict, 'frequency_model': dict}
    """
    # 1. DB에서 규칙 로드 시도
    prefix_rules = load_prefix_trend_rules(window_size)
    
    # 2. 규칙이 없거나 부족하면 계산 후 저장
    if not prefix_rules:
        # ngrams_df에서 규칙 계산
        saved_count = generate_and_save_prefix_trend_rules(window_size)
        if saved_count > 0:
            # 다시 로드
            prefix_rules = load_prefix_trend_rules(window_size)
    
    # 3. 빈도 모델 구축 (항상 예측값 반환 보장)
    frequency_model = build_frequency_model(ngrams_df)
    
    return {
        'prefix_rules': prefix_rules,
        'frequency_model': frequency_model
    }

# ============================================================================
# 예측 함수들 (복제)
# ============================================================================

def predict_frequency(model, prefix):
    """빈도 기반 예측"""
    if prefix not in model:
        return None, {}
    
    suffix_counts = model[prefix]
    if not suffix_counts:
        return None, {}
    
    # 가장 빈번한 suffix
    most_common = suffix_counts.most_common(1)[0]
    predicted = most_common[0]
    
    # 비율 계산
    total = sum(suffix_counts.values())
    ratios = {suffix: (count / total * 100) for suffix, count in suffix_counts.items()}
    
    return predicted, ratios

def predict_weighted(model, prefix):
    """가중치 기반 예측"""
    if prefix not in model:
        return None, {}
    
    suffix_weights = model[prefix]
    if not suffix_weights:
        return None, {}
    
    # 가장 높은 가중치의 suffix
    predicted = max(suffix_weights.items(), key=lambda x: x[1])[0]
    
    # 가중치를 비율로 변환
    total = sum(suffix_weights.values())
    ratios = {suffix: (weight / total * 100) for suffix, weight in suffix_weights.items()}
    
    return predicted, ratios

def predict_safety_first(model, prefix, recent_history=None, consecutive_mismatches=0):
    """
    안전 우선 적응형 예측 (독립적 구현)
    
    연속 불일치를 방지하기 위해 안전 모드를 자동으로 활성화
    
    Args:
        model: 학습된 모델 (build_safety_first_model로 구축)
        prefix: 예측할 prefix 문자열
        recent_history: 최근 예측 히스토리 [(predicted, actual, is_match), ...]
        consecutive_mismatches: 현재 연속 불일치 수
    
    Returns:
        dict: {
            'predicted': 예측값,
            'ratios': 비율 딕셔너리,
            'confidence': 신뢰도,
            'strategy_name': '안전우선_적응형',
            'is_safety_mode': 안전 모드 활성화 여부,
            'safety_reason': 안전 모드 활성화 이유
        }
    """
    # 1. 기본 빈도 기반 예측 (독립적으로 계산)
    if prefix not in model:
        return {
            'predicted': None,
            'ratios': {},
            'confidence': 0.0,
            'strategy_name': '안전우선_적응형',
            'is_safety_mode': False,
            'safety_reason': None
        }
    
    suffix_counts = model[prefix]
    if not suffix_counts:
        return {
            'predicted': None,
            'ratios': {},
            'confidence': 0.0,
            'strategy_name': '안전우선_적응형',
            'is_safety_mode': False,
            'safety_reason': None
        }
    
    # 가장 빈번한 suffix
    most_common = suffix_counts.most_common(1)[0]
    base_predicted = most_common[0]
    
    # 비율 계산
    total = sum(suffix_counts.values())
    base_ratios = {suffix: (count / total * 100) for suffix, count in suffix_counts.items()}
    base_confidence = max(base_ratios.values()) if base_ratios else 0.0
    
    # 2. 안전 모드 판단
    is_safety_mode = False
    safety_reason = None
    predicted = base_predicted
    ratios = base_ratios.copy()
    
    # 조건 1: 연속 불일치가 2회 이상이면 안전 모드
    if consecutive_mismatches >= 2:
        is_safety_mode = True
        safety_reason = f"연속 불일치 {consecutive_mismatches}회"
        # 반대 예측으로 전환
        predicted = 'p' if base_predicted == 'b' else 'b'
        # 비율도 반전
        ratios = {'b': base_ratios.get('p', 0.0), 'p': base_ratios.get('b', 0.0)}
    
    # 조건 2: 최근 히스토리가 있으면 성공률 계산
    elif recent_history and len(recent_history) >= 5:
        recent = recent_history[-5:]  # 최근 5개
        recent_success_rate = sum(1 for h in recent if h[2]) / len(recent)  # is_match가 True인 비율
        
        # 최근 성공률이 40% 미만이면 안전 모드
        if recent_success_rate < 0.4:
            is_safety_mode = True
            safety_reason = f"최근 성공률 {recent_success_rate*100:.1f}%"
            # 신뢰도가 낮으면 반대 예측
            if base_confidence < 60:
                predicted = 'p' if base_predicted == 'b' else 'b'
                ratios = {'b': base_ratios.get('p', 0.0), 'p': base_ratios.get('b', 0.0)}
    
    # 조건 3: 신뢰도가 매우 낮으면 (45-55%) 안전 모드
    elif 45 <= base_confidence <= 55:
        is_safety_mode = True
        safety_reason = f"신뢰도가 너무 낮음 ({base_confidence:.1f}%)"
        # 반대 예측으로 전환
        predicted = 'p' if base_predicted == 'b' else 'b'
        ratios = {'b': base_ratios.get('p', 0.0), 'p': base_ratios.get('b', 0.0)}
    
    # 최종 신뢰도 계산
    confidence = max(ratios.values()) if ratios else 0.0
    
    return {
        'predicted': predicted,
        'ratios': ratios,
        'confidence': confidence,
        'strategy_name': '안전우선_적응형',
        'is_safety_mode': is_safety_mode,
        'safety_reason': safety_reason
    }

def predict_balance_recovery_trend_final(model, prefix):
    """
    균형 회복 트렌드 모델로 예측 (항상 예측값 반환 보장)
    
    Args:
        model: build_balance_recovery_trend_model_final로 구축된 모델
        prefix: 예측할 prefix
    
    Returns:
        tuple: (predicted, ratios) - 항상 값 반환
    """
    # 기본 빈도 모델 예측 (항상 있음)
    freq_predicted, freq_ratios = predict_frequency(model['frequency_model'], prefix)
    
    # prefix 규칙이 있으면 적용
    if prefix in model['prefix_rules']:
        rule = model['prefix_rules'][prefix]
        trend_follow = rule['trend_follow']
        confidence = rule['confidence']
        b_ratio = rule['b_ratio']
        
        # 규칙 기반 예측
        if trend_follow:
            # 트렌드 따름: prefix 비율과 같은 방향
            if b_ratio > 0.5:
                rule_predicted = 'b'
            elif b_ratio < 0.5:
                rule_predicted = 'p'
            else:
                # 균형이면 기본 빈도 모델 사용
                return freq_predicted, freq_ratios
        else:
            # 트렌드 반대 (회복): prefix 비율과 반대 방향
            if b_ratio > 0.5:
                rule_predicted = 'p'  # b가 많으면 p 예측 (회복)
            elif b_ratio < 0.5:
                rule_predicted = 'b'  # p가 많으면 b 예측 (회복)
            else:
                # 균형이면 기본 빈도 모델 사용
                return freq_predicted, freq_ratios
        
        # 규칙과 빈도 모델 가중 평균 (신뢰도 기반)
        rule_weight = min(0.6, confidence * 1.2)  # 최대 60%
        freq_weight = 1.0 - rule_weight
        
        # 규칙 비율
        if rule_predicted == 'b':
            rule_b = 0.5 + (confidence * 0.25)
            rule_p = 1.0 - rule_b
        else:
            rule_p = 0.5 + (confidence * 0.25)
            rule_b = 1.0 - rule_p
        
        # 빈도 비율
        freq_b = freq_ratios.get('b', 50) / 100
        freq_p = freq_ratios.get('p', 50) / 100
        
        # 가중 평균
        combined_b = (rule_b * rule_weight) + (freq_b * freq_weight)
        combined_p = (rule_p * rule_weight) + (freq_p * freq_weight)
        
        # 정규화
        total = combined_b + combined_p
        if total > 0:
            combined_b = combined_b / total
            combined_p = combined_p / total
        
        predicted = 'b' if combined_b > combined_p else 'p'
        ratios = {
            'b': combined_b * 100,
            'p': combined_p * 100
        }
        
        return predicted, ratios
    
    # 규칙이 없으면 기본 빈도 모델만 사용 (항상 예측값 반환)
    return freq_predicted, freq_ratios

# ============================================================================
# 패턴 검출 함수들 (신규)
# ============================================================================

def extract_prefix_suffix_sequence(window_size, prefix, min_occurrence=10):
    """
    특정 prefix의 suffix 시계열 시퀀스 추출
    
    Args:
        window_size: 윈도우 크기
        prefix: 분석할 prefix
        min_occurrence: 최소 출현 횟수
    
    Returns:
        dict: {
            'sequence': [suffix1, suffix2, ...],  # 시간 순서
            'timestamps': [created_at1, ...],
            'grid_string_ids': [id1, id2, ...],
            'total_count': 총 출현 횟수
        }
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        query = """
            SELECT 
                nc.suffix,
                nc.grid_string_id,
                nc.chunk_index,
                pgs.created_at as grid_created_at
            FROM ngram_chunks nc
            JOIN preprocessed_grid_strings pgs ON nc.grid_string_id = pgs.id
            WHERE nc.window_size = ? AND nc.prefix = ?
            ORDER BY pgs.created_at ASC, nc.chunk_index ASC
        """
        
        df = pd.read_sql_query(query, conn, params=[window_size, prefix])
        
        if len(df) < min_occurrence:
            return None
        
        return {
            'sequence': df['suffix'].tolist(),
            'timestamps': df['grid_created_at'].tolist(),
            'grid_string_ids': df['grid_string_id'].tolist(),
            'total_count': len(df)
        }
    
    except Exception as e:
        st.error(f"Suffix 시퀀스 추출 오류: {str(e)}")
        return None
    finally:
        conn.close()

def detect_suffix_patterns(sequence):
    """
    Suffix 시퀀스에서 패턴 검출
    
    Args:
        sequence: suffix 리스트 (예: ['b', 'p', 'b', 'b', ...])
    
    Returns:
        dict: 패턴 분석 결과
    """
    if len(sequence) < 5:
        return None
    
    # 숫자로 변환 (b=0, p=1)
    numeric_seq = [0 if s == 'b' else 1 for s in sequence]
    
    results = {
        'total_length': len(sequence),
        'b_count': sequence.count('b'),
        'p_count': sequence.count('p'),
        'b_ratio': sequence.count('b') / len(sequence),
        'p_ratio': sequence.count('p') / len(sequence)
    }
    
    # 1. Runs Test (랜덤성 검정)
    runs = 1
    for i in range(1, len(numeric_seq)):
        if numeric_seq[i] != numeric_seq[i-1]:
            runs += 1
    
    n1 = results['b_count']
    n2 = results['p_count']
    n = len(sequence)
    
    if n1 > 0 and n2 > 0:
        # Runs test 통계량
        expected_runs = (2 * n1 * n2) / (n1 + n2) + 1
        variance_runs = (2 * n1 * n2 * (2 * n1 * n2 - n1 - n2)) / ((n1 + n2) ** 2 * (n1 + n2 - 1))
        
        if variance_runs > 0:
            z_score = (runs - expected_runs) / np.sqrt(variance_runs)
            p_value = 2 * (1 - stats.norm.cdf(abs(z_score)))
            
            results['runs_test'] = {
                'runs_count': runs,
                'expected_runs': expected_runs,
                'z_score': z_score,
                'p_value': p_value,
                'is_random': p_value > 0.05,
                'interpretation': '랜덤' if p_value > 0.05 else '패턴_존재'
            }
        else:
            results['runs_test'] = None
    else:
        results['runs_test'] = None
    
    # 2. 트렌드 분석 (선형 회귀)
    x = np.arange(len(numeric_seq))
    slope, intercept, r_value, p_value, std_err = stats.linregress(x, numeric_seq)
    
    results['trend_analysis'] = {
        'slope': slope,
        'r_squared': r_value ** 2,
        'p_value': p_value,
        'has_trend': p_value < 0.05,
        'trend_direction': 'P_증가' if slope > 0 else 'B_증가' if slope < 0 else '없음'
    }
    
    # 3. 자기상관 분석 (주기성 검출)
    max_lag = min(20, len(sequence) // 4)
    autocorrelations = []
    
    for lag in range(1, max_lag + 1):
        if len(sequence) > lag:
            seq1 = numeric_seq[:-lag]
            seq2 = numeric_seq[lag:]
            if len(seq1) > 0 and np.std(seq1) > 0 and np.std(seq2) > 0:
                corr = np.corrcoef(seq1, seq2)[0, 1]
                if not np.isnan(corr):
                    autocorrelations.append({'lag': lag, 'correlation': corr})
    
    if autocorrelations:
        max_corr = max(autocorrelations, key=lambda x: abs(x['correlation']))
        results['autocorrelation'] = {
            'max_correlation': max_corr['correlation'],
            'max_correlation_lag': max_corr['lag'],
            'has_periodicity': abs(max_corr['correlation']) > 0.3,
            'all_correlations': autocorrelations[:10]
        }
    else:
        results['autocorrelation'] = None
    
    # 4. 마르코프 체인 분석
    transitions = defaultdict(lambda: {'b': 0, 'p': 0})
    
    for i in range(len(sequence) - 1):
        current = sequence[i]
        next_suffix = sequence[i + 1]
        transitions[current][next_suffix] += 1
    
    markov_probs = {}
    for current, counts in transitions.items():
        total = counts['b'] + counts['p']
        if total > 0:
            markov_probs[current] = {
                'b_prob': counts['b'] / total,
                'p_prob': counts['p'] / total,
                'total': total
            }
    
    results['markov_chain'] = {
        'transition_probs': markov_probs,
        'has_dependency': len(markov_probs) > 0 and any(
            abs(prob['b_prob'] - prob['p_prob']) > 0.2 
            for prob in markov_probs.values()
        )
    }
    
    # 5. 순환 패턴 검출
    cycle_patterns = {}
    for cycle_len in [2, 3, 4, 5]:
        if len(sequence) >= cycle_len * 2:
            cycles = []
            for i in range(0, len(sequence) - cycle_len + 1, cycle_len):
                cycle = ''.join(sequence[i:i+cycle_len])
                cycles.append(cycle)
            
            if cycles:
                cycle_counter = Counter(cycles)
                most_common_cycle = cycle_counter.most_common(1)[0]
                cycle_ratio = most_common_cycle[1] / len(cycles)
                
                if cycle_ratio > 0.4:
                    cycle_patterns[cycle_len] = {
                        'pattern': most_common_cycle[0],
                        'frequency': most_common_cycle[1],
                        'ratio': cycle_ratio
                    }
    
    results['cycle_patterns'] = cycle_patterns if cycle_patterns else None
    
    # 6. 변화점 검출
    window_size = max(5, len(sequence) // 10)
    change_points = []
    
    for i in range(window_size, len(sequence) - window_size):
        before = numeric_seq[i-window_size:i]
        after = numeric_seq[i:i+window_size]
        
        before_mean = np.mean(before)
        after_mean = np.mean(after)
        change_magnitude = abs(after_mean - before_mean)
        
        if change_magnitude > 0.3:
            change_points.append({
                'index': i,
                'change_magnitude': change_magnitude,
                'before_ratio': before_mean,
                'after_ratio': after_mean
            })
    
    results['change_points'] = change_points if change_points else None
    
    # 7. 연속성 분석
    max_consecutive_b = 0
    max_consecutive_p = 0
    current_b = 0
    current_p = 0
    
    for s in sequence:
        if s == 'b':
            current_b += 1
            current_p = 0
            max_consecutive_b = max(max_consecutive_b, current_b)
        else:
            current_p += 1
            current_b = 0
            max_consecutive_p = max(max_consecutive_p, current_p)
    
    results['consecutive_analysis'] = {
        'max_consecutive_b': max_consecutive_b,
        'max_consecutive_p': max_consecutive_p,
        'avg_consecutive_b': results['b_count'] / (sequence.count('bp') + sequence.count('pb') + 1),
        'avg_consecutive_p': results['p_count'] / (sequence.count('bp') + sequence.count('pb') + 1)
    }
    
    # 8. 샤논 엔트로피 계산 (예측 가능성 측정)
    def calculate_shannon_entropy(seq):
        """샤논 엔트로피 계산"""
        if len(seq) == 0:
            return 0.0
        
        counts = Counter(seq)
        total = len(seq)
        
        entropy = 0.0
        for count in counts.values():
            if count > 0:
                probability = count / total
                entropy -= probability * log2(probability)
        
        # 정규화 (최대 엔트로피는 log2(고유값 개수))
        max_entropy = log2(len(counts)) if len(counts) > 0 else 1.0
        normalized_entropy = entropy / max_entropy if max_entropy > 0 else 0.0
        
        return normalized_entropy
    
    entropy = calculate_shannon_entropy(sequence)
    results['shannon_entropy'] = {
        'entropy': entropy,
        'normalized_entropy': entropy,
        'predictability': 1.0 - entropy,  # 예측 가능성 (높을수록 예측 가능)
        'interpretation': '예측가능' if entropy < 0.5 else '중간' if entropy < 0.8 else '랜덤'
    }
    
    # 9. 이동 평균 및 빈도 분석 (비율 변화 지점 검출)
    def analyze_moving_average_frequency(seq, window_sz=100):
        """이동 평균 및 빈도 분석"""
        if len(seq) < window_sz:
            window_sz = max(10, len(seq) // 2)
        
        numeric_seq = [0 if s == 'b' else 1 for s in seq]
        
        moving_ratios = []
        change_points = []
        
        for i in range(len(numeric_seq) - window_sz + 1):
            window = numeric_seq[i:i + window_sz]
            b_ratio = window.count(0) / len(window)
            p_ratio = window.count(1) / len(window)
            
            moving_ratios.append({
                'index': i + window_sz // 2,
                'b_ratio': b_ratio,
                'p_ratio': p_ratio,
                'imbalance': abs(b_ratio - p_ratio)
            })
        
        # 변화점 검출
        if len(moving_ratios) > 1:
            for i in range(1, len(moving_ratios)):
                prev_ratio = moving_ratios[i-1]['b_ratio']
                curr_ratio = moving_ratios[i]['b_ratio']
                change_magnitude = abs(curr_ratio - prev_ratio)
                
                if change_magnitude > 0.2:
                    change_points.append({
                        'index': moving_ratios[i]['index'],
                        'change_magnitude': change_magnitude,
                        'prev_b_ratio': prev_ratio,
                        'curr_b_ratio': curr_ratio
                    })
        
        # 전체 통계
        if moving_ratios:
            avg_imbalance = np.mean([r['imbalance'] for r in moving_ratios])
            max_imbalance = max([r['imbalance'] for r in moving_ratios])
            avg_b_ratio = np.mean([r['b_ratio'] for r in moving_ratios])
            avg_p_ratio = np.mean([r['p_ratio'] for r in moving_ratios])
        else:
            avg_imbalance = 0.0
            max_imbalance = 0.0
            avg_b_ratio = 0.5
            avg_p_ratio = 0.5
        
        return {
            'window_size': window_sz,
            'avg_imbalance': avg_imbalance,
            'max_imbalance': max_imbalance,
            'avg_b_ratio': avg_b_ratio,
            'avg_p_ratio': avg_p_ratio,
            'change_points_count': len(change_points),
            'change_points': change_points[:10],
            'has_imbalance': avg_imbalance > 0.3,
            'interpretation': '불균형_패턴' if avg_imbalance > 0.3 else '균형'
        }
    
    window_size_ma = min(100, len(sequence) // 2)
    if window_size_ma >= 10:
        moving_avg_result = analyze_moving_average_frequency(sequence, window_size_ma)
        results['moving_average'] = moving_avg_result
    else:
        results['moving_average'] = None
    
    # 10. 패턴 요약
    pattern_summary = []
    
    if results['runs_test'] and not results['runs_test']['is_random']:
        pattern_summary.append("비랜덤_패턴")
    
    if results['trend_analysis']['has_trend']:
        pattern_summary.append(f"트렌드_{results['trend_analysis']['trend_direction']}")
    
    if results['autocorrelation'] and results['autocorrelation']['has_periodicity']:
        pattern_summary.append(f"주기성_lag{results['autocorrelation']['max_correlation_lag']}")
    
    if results['markov_chain']['has_dependency']:
        pattern_summary.append("마르코프_의존성")
    
    if results['cycle_patterns']:
        pattern_summary.append("순환_패턴")
    
    if results['change_points']:
        pattern_summary.append(f"변화점_{len(results['change_points'])}개")
    
    # 엔트로피 기반 예측 가능성 추가
    if results.get('shannon_entropy'):
        entropy_info = results['shannon_entropy']
        if entropy_info['predictability'] > 0.5:
            pattern_summary.append("예측가능")
        elif entropy_info['predictability'] < 0.2:
            pattern_summary.append("랜덤")
    
    # 이동 평균 기반 패턴 추가
    if results.get('moving_average') and results['moving_average'].get('has_imbalance'):
        pattern_summary.append("불균형_패턴")
    
    results['pattern_summary'] = pattern_summary if pattern_summary else ["랜덤_또는_복합"]
    
    return results

def analyze_prefix_suffix_temporal_patterns(window_size, min_occurrence=10, top_n=50):
    """
    모든 prefix에 대해 시계열 suffix 패턴 분석
    
    Args:
        window_size: 윈도우 크기
        min_occurrence: 분석할 최소 출현 횟수
        top_n: 상위 N개 prefix만 상세 분석
    
    Returns:
        dict: 분석 결과
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # prefix별 출현 횟수 집계
        query = """
            SELECT 
                prefix,
                COUNT(*) as count
            FROM ngram_chunks
            WHERE window_size = ?
            GROUP BY prefix
            HAVING COUNT(*) >= ?
            ORDER BY COUNT(*) DESC
        """
        
        prefix_counts = pd.read_sql_query(query, conn, params=[window_size, min_occurrence])
        
        if len(prefix_counts) == 0:
            return None
        
        all_results = []
        detailed_results = {}
        
        # 상위 N개만 상세 분석
        top_prefixes = prefix_counts.head(top_n)['prefix'].tolist()
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for idx, row in prefix_counts.iterrows():
            prefix = row['prefix']
            count = row['count']
            
            if idx < len(top_prefixes):
                status_text.text(f"상세 분석 중: {prefix} ({count}회)")
                progress_bar.progress((idx + 1) / min(len(prefix_counts), top_n))
            
            # 시퀀스 추출
            sequence_data = extract_prefix_suffix_sequence(window_size, prefix, min_occurrence)
            
            if sequence_data is None:
                continue
            
            # 패턴 검출
            pattern_result = detect_suffix_patterns(sequence_data['sequence'])
            
            if pattern_result:
                result_entry = {
                    'prefix': prefix,
                    'total_count': count,
                    'b_ratio': pattern_result['b_ratio'],
                    'p_ratio': pattern_result['p_ratio'],
                    'pattern_summary': ', '.join(pattern_result['pattern_summary']),
                    'is_random': pattern_result.get('runs_test', {}).get('is_random', True) if pattern_result.get('runs_test') else True,
                    'has_trend': pattern_result.get('trend_analysis', {}).get('has_trend', False),
                    'has_periodicity': pattern_result.get('autocorrelation', {}).get('has_periodicity', False) if pattern_result.get('autocorrelation') else False,
                    'has_markov_dependency': pattern_result.get('markov_chain', {}).get('has_dependency', False),
                    'has_cycle': pattern_result.get('cycle_patterns') is not None,
                    'has_change_points': pattern_result.get('change_points') is not None
                }
                
                all_results.append(result_entry)
                
                # 상위 N개는 상세 결과 저장
                if prefix in top_prefixes:
                    detailed_results[prefix] = {
                        'sequence_data': sequence_data,
                        'pattern_result': pattern_result
                    }
        
        progress_bar.empty()
        status_text.empty()
        
        return {
            'summary_df': pd.DataFrame(all_results),
            'detailed_results': detailed_results,
            'window_size': window_size,
            'min_occurrence': min_occurrence,
            'total_prefixes_analyzed': len(all_results)
        }
    
    except Exception as e:
        st.error(f"시계열 패턴 분석 오류: {str(e)}")
        return None
    finally:
        conn.close()

# ============================================================================
# 예측 결과 비교 함수들 (신규)
# ============================================================================

def build_all_models(ngrams_df, window_size, methods):
    """
    모든 예측 방법의 모델을 한번에 구축
    
    Args:
        ngrams_df: N-gram 조각 DataFrame
        window_size: 윈도우 크기
        methods: 사용할 예측 방법 리스트
    
    Returns:
        dict: {method_name: model}
    """
    models = {}
    
    if '빈도 기반' in methods:
        models['빈도 기반'] = build_frequency_model(ngrams_df)
    
    if '가중치 기반' in methods:
        models['가중치 기반'] = build_weighted_model(ngrams_df)
    
    if '안전 우선' in methods:
        models['안전 우선'] = build_safety_first_model(ngrams_df)
    
    if '균형 회복 트렌드' in methods:
        models['균형 회복 트렌드'] = build_balance_recovery_trend_model_final(ngrams_df, window_size)
    
    return models

def compare_prediction_methods(models, prefixes, include_patterns=False, window_size=None):
    """
    여러 예측 방법의 결과를 비교하여 DataFrame 반환
    
    Args:
        models: {method_name: model} 딕셔너리
        prefixes: 비교할 prefix 리스트 또는 DataFrame (prefix 컬럼 포함)
        include_patterns: 패턴 분석 포함 여부
        window_size: 윈도우 크기 (패턴 분석 시 필요)
    
    Returns:
        pd.DataFrame: 비교 결과
    """
    results = []
    
    # prefix 리스트 추출
    if isinstance(prefixes, pd.DataFrame):
        prefix_list = prefixes['prefix'].unique().tolist()
    else:
        prefix_list = prefixes
    
    for prefix in prefix_list:
        row = {'prefix': prefix}
        
        # 각 방법별 예측
        for method_name, model in models.items():
            if method_name == '빈도 기반':
                predicted, ratios = predict_frequency(model, prefix)
            elif method_name == '가중치 기반':
                predicted, ratios = predict_weighted(model, prefix)
            elif method_name == '안전 우선':
                result = predict_safety_first(model, prefix, recent_history=None, consecutive_mismatches=0)
                predicted = result.get('predicted')
                ratios = result.get('ratios', {})
            elif method_name == '균형 회복 트렌드':
                predicted, ratios = predict_balance_recovery_trend_final(model, prefix)
            else:
                predicted, ratios = None, {}
            
            confidence = max(ratios.values()) if ratios else 0.0
            
            row[f'{method_name}_예측'] = predicted
            row[f'{method_name}_신뢰도'] = confidence
            row[f'{method_name}_B비율'] = ratios.get('b', 0.0)
            row[f'{method_name}_P비율'] = ratios.get('p', 0.0)
        
        # 패턴 분석 - 6가지 방법별로 상세 결과 표시
        if include_patterns and window_size:
            sequence_data = extract_prefix_suffix_sequence(window_size, prefix, min_occurrence=5)
            if sequence_data:
                pattern_result = detect_suffix_patterns(sequence_data['sequence'])
                if pattern_result:
                    # 1. Runs Test 결과
                    runs_test = pattern_result.get('runs_test')
                    if runs_test:
                        row['RunsTest_랜덤여부'] = '랜덤' if runs_test.get('is_random', True) else '비랜덤'
                        row['RunsTest_p값'] = f"{runs_test.get('p_value', 0):.4f}"
                        row['RunsTest_runs수'] = runs_test.get('runs_count', 0)
                    else:
                        row['RunsTest_랜덤여부'] = '분석불가'
                        row['RunsTest_p값'] = '-'
                        row['RunsTest_runs수'] = '-'
                    
                    # 2. 트렌드 분석 결과
                    trend = pattern_result.get('trend_analysis', {})
                    row['트렌드_방향'] = trend.get('trend_direction', '없음')
                    row['트렌드_유의성'] = '유의함' if trend.get('has_trend', False) else '유의없음'
                    row['트렌드_R²'] = f"{trend.get('r_squared', 0):.4f}"
                    row['트렌드_p값'] = f"{trend.get('p_value', 1):.4f}"
                    
                    # 3. 자기상관 분석 (주기성) 결과
                    autocorr = pattern_result.get('autocorrelation')
                    if autocorr:
                        row['주기성_존재'] = '있음' if autocorr.get('has_periodicity', False) else '없음'
                        row['주기성_최대상관'] = f"{autocorr.get('max_correlation', 0):.4f}"
                        row['주기성_lag'] = autocorr.get('max_correlation_lag', '-')
                    else:
                        row['주기성_존재'] = '분석불가'
                        row['주기성_최대상관'] = '-'
                        row['주기성_lag'] = '-'
                    
                    # 4. 마르코프 체인 분석 결과
                    markov = pattern_result.get('markov_chain', {})
                    row['마르코프_의존성'] = '있음' if markov.get('has_dependency', False) else '없음'
                    transition_probs = markov.get('transition_probs', {})
                    if transition_probs:
                        if 'b' in transition_probs:
                            row['마르코프_B다음B확률'] = f"{transition_probs['b'].get('b_prob', 0):.4f}"
                            row['마르코프_B다음P확률'] = f"{transition_probs['b'].get('p_prob', 0):.4f}"
                        else:
                            row['마르코프_B다음B확률'] = '-'
                            row['마르코프_B다음P확률'] = '-'
                        if 'p' in transition_probs:
                            row['마르코프_P다음B확률'] = f"{transition_probs['p'].get('b_prob', 0):.4f}"
                            row['마르코프_P다음P확률'] = f"{transition_probs['p'].get('p_prob', 0):.4f}"
                        else:
                            row['마르코프_P다음B확률'] = '-'
                            row['마르코프_P다음P확률'] = '-'
                    else:
                        row['마르코프_B다음B확률'] = '-'
                        row['마르코프_B다음P확률'] = '-'
                        row['마르코프_P다음B확률'] = '-'
                        row['마르코프_P다음P확률'] = '-'
                    
                    # 5. 순환 패턴 결과
                    cycles = pattern_result.get('cycle_patterns')
                    if cycles:
                        cycle_info = []
                        for cycle_len, cycle_data in cycles.items():
                            cycle_info.append(f"길이{cycle_len}:{cycle_data['pattern']}({cycle_data['ratio']:.2%})")
                        row['순환패턴'] = ', '.join(cycle_info) if cycle_info else '없음'
                    else:
                        row['순환패턴'] = '없음'
                    
                    # 6. 변화점 검출 결과
                    change_points = pattern_result.get('change_points')
                    if change_points:
                        row['변화점_개수'] = len(change_points)
                        if len(change_points) > 0:
                            max_change = max(change_points, key=lambda x: x['change_magnitude'])
                            row['변화점_최대변화량'] = f"{max_change['change_magnitude']:.4f}"
                            row['변화점_위치'] = f"{max_change['index']}"
                        else:
                            row['변화점_최대변화량'] = '-'
                            row['변화점_위치'] = '-'
                    else:
                        row['변화점_개수'] = 0
                        row['변화점_최대변화량'] = '-'
                        row['변화점_위치'] = '-'
                    
                    # 7. 샤논 엔트로피 결과
                    entropy_info = pattern_result.get('shannon_entropy')
                    if entropy_info:
                        row['엔트로피_값'] = f"{entropy_info.get('entropy', 0):.4f}"
                        row['엔트로피_예측가능성'] = f"{entropy_info.get('predictability', 0):.4f}"
                        row['엔트로피_해석'] = entropy_info.get('interpretation', '중간')
                    else:
                        row['엔트로피_값'] = '-'
                        row['엔트로피_예측가능성'] = '-'
                        row['엔트로피_해석'] = '-'
                    
                    # 8. 이동 평균 및 빈도 분석 결과
                    moving_avg = pattern_result.get('moving_average')
                    if moving_avg:
                        row['이동평균_불균형'] = f"{moving_avg.get('avg_imbalance', 0):.4f}"
                        row['이동평균_최대불균형'] = f"{moving_avg.get('max_imbalance', 0):.4f}"
                        row['이동평균_평균B비율'] = f"{moving_avg.get('avg_b_ratio', 0):.4f}"
                        row['이동평균_평균P비율'] = f"{moving_avg.get('avg_p_ratio', 0):.4f}"
                        row['이동평균_변화점수'] = moving_avg.get('change_points_count', 0)
                        row['이동평균_해석'] = moving_avg.get('interpretation', '균형')
                    else:
                        row['이동평균_불균형'] = '-'
                        row['이동평균_최대불균형'] = '-'
                        row['이동평균_평균B비율'] = '-'
                        row['이동평균_평균P비율'] = '-'
                        row['이동평균_변화점수'] = '-'
                        row['이동평균_해석'] = '-'
                else:
                    # 패턴 분석 실패
                    row['RunsTest_랜덤여부'] = '분석불가'
                    row['트렌드_방향'] = '분석불가'
                    row['주기성_존재'] = '분석불가'
                    row['마르코프_의존성'] = '분석불가'
                    row['순환패턴'] = '분석불가'
                    row['변화점_개수'] = '-'
                    row['엔트로피_값'] = '-'
                    row['엔트로피_예측가능성'] = '-'
                    row['엔트로피_해석'] = '-'
                    row['이동평균_불균형'] = '-'
                    row['이동평균_최대불균형'] = '-'
                    row['이동평균_평균B비율'] = '-'
                    row['이동평균_평균P비율'] = '-'
                    row['이동평균_변화점수'] = '-'
                    row['이동평균_해석'] = '-'
            else:
                # 데이터 부족
                row['RunsTest_랜덤여부'] = '데이터부족'
                row['트렌드_방향'] = '데이터부족'
                row['주기성_존재'] = '데이터부족'
                row['마르코프_의존성'] = '데이터부족'
                row['순환패턴'] = '데이터부족'
                row['변화점_개수'] = '-'
                row['엔트로피_값'] = '-'
                row['엔트로피_예측가능성'] = '-'
                row['엔트로피_해석'] = '-'
                row['이동평균_불균형'] = '-'
                row['이동평균_최대불균형'] = '-'
                row['이동평균_평균B비율'] = '-'
                row['이동평균_평균P비율'] = '-'
                row['이동평균_변화점수'] = '-'
                row['이동평균_해석'] = '-'
        
        results.append(row)
    
    return pd.DataFrame(results)

# ============================================================================
# 시계열 누적 검증 함수들 (신규)
# ============================================================================

def simulate_step_by_step(model, grid_string, window_size, method="빈도 기반"):
    """
    단계별 시뮬레이션 및 결과 수집
    
    Args:
        model: 학습된 모델
        grid_string: 검증할 문자열
        window_size: 윈도우 크기
        method: 예측 방법
    
    Returns:
        list: 각 스텝의 결과
    """
    prefixes_data = extract_prefixes_from_string(grid_string, window_size)
    
    if not prefixes_data:
        return []
    
    history = []
    
    for step, (prefix, actual_suffix, index) in enumerate(prefixes_data):
        # 예측 수행
        if method == "빈도 기반":
            predicted, ratios = predict_frequency(model, prefix)
        elif method == "가중치 기반":
            predicted, ratios = predict_weighted(model, prefix)
        elif method == "안전 우선":
            result = predict_safety_first(model, prefix, recent_history=None, consecutive_mismatches=0)
            predicted = result.get('predicted')
            ratios = result.get('ratios', {})
        elif method == "균형 회복 트렌드":
            predicted, ratios = predict_balance_recovery_trend_final(model, prefix)
        else:
            predicted, ratios = None, {}
        
        confidence = max(ratios.values()) if ratios else 0.0
        is_correct = predicted == actual_suffix if predicted else False
        
        history.append({
            'step': step + 1,
            'index': index,
            'prefix': prefix,
            'predicted': predicted,
            'actual': actual_suffix,
            'is_correct': is_correct,
            'confidence': confidence,
            'b_ratio': ratios.get('b', 0.0),
            'p_ratio': ratios.get('p', 0.0)
        })
    
    return history

def validate_cumulative_timeseries(window_size, methods, cutoff_grid_string_id=None):
    """
    시계열 누적 방식으로 검증 수행
    
    Args:
        window_size: 윈도우 크기
        methods: 사용할 예측 방법 리스트
        cutoff_grid_string_id: 학습 데이터 기준 ID (None이면 전체)
    
    Returns:
        dict: 검증 결과
    """
    df_strings = load_preprocessed_data()
    
    if len(df_strings) == 0:
        return None
    
    # created_at 기준 정렬
    df_sorted = df_strings.sort_values('created_at').reset_index(drop=True)
    
    # cutoff 기준 필터링
    if cutoff_grid_string_id:
        train_df = df_sorted[df_sorted['id'] <= cutoff_grid_string_id]
        test_df = df_sorted[df_sorted['id'] > cutoff_grid_string_id]
    else:
        train_df = df_sorted.iloc[:len(df_sorted)//2]  # 전반부
        test_df = df_sorted.iloc[len(df_sorted)//2:]    # 후반부
    
    results_by_method = {}
    
    for method in methods:
        method_results = {
            'total_tested': 0,
            'total_steps': 0,
            'total_correct': 0,
            'total_incorrect': 0,
            'grid_string_results': []
        }
        
        # 각 테스트 grid_string에 대해
        for idx, row in test_df.iterrows():
            current_grid_string = row['grid_string']
            current_id = row['id']
            
            if len(current_grid_string) < window_size:
                continue
            
            # 이전까지의 모든 grid_string ID (현재 제외)
            previous_ids = train_df[train_df['id'] < current_id]['id'].tolist()
            
            if len(previous_ids) == 0:
                continue
            
            try:
                # 이전까지의 누적 데이터로 모델 구축
                train_ngrams = load_ngram_chunks(window_size=window_size, grid_string_ids=previous_ids)
                
                if len(train_ngrams) == 0:
                    continue
                
                # 모델 구축
                if method == "빈도 기반":
                    model = build_frequency_model(train_ngrams)
                elif method == "가중치 기반":
                    model = build_weighted_model(train_ngrams)
                elif method == "안전 우선":
                    model = build_safety_first_model(train_ngrams)
                elif method == "균형 회복 트렌드":
                    model = build_balance_recovery_trend_model_final(train_ngrams, window_size)
                else:
                    continue
                
                # 단계별 시뮬레이션
                history = simulate_step_by_step(model, current_grid_string, window_size, method)
                
                if len(history) > 0:
                    correct_count = sum(1 for h in history if h['is_correct'])
                    total_count = len(history)
                    accuracy = (correct_count / total_count * 100) if total_count > 0 else 0.0
                    
                    method_results['total_tested'] += 1
                    method_results['total_steps'] += total_count
                    method_results['total_correct'] += correct_count
                    method_results['total_incorrect'] += (total_count - correct_count)
                    
                    method_results['grid_string_results'].append({
                        'grid_string_id': current_id,
                        'total_steps': total_count,
                        'correct': correct_count,
                        'incorrect': total_count - correct_count,
                        'accuracy': accuracy,
                        'history': history
                    })
            
            except Exception as e:
                st.warning(f"Grid String ID {current_id} 검증 오류: {str(e)}")
                continue
        
        # 전체 정확도 계산
        if method_results['total_steps'] > 0:
            method_results['overall_accuracy'] = (method_results['total_correct'] / method_results['total_steps'] * 100)
        else:
            method_results['overall_accuracy'] = 0.0
        
        results_by_method[method] = method_results
    
    return {
        'window_size': window_size,
        'methods': methods,
        'cutoff_grid_string_id': cutoff_grid_string_id,
        'results_by_method': results_by_method
    }

# ============================================================================
# 시각화 함수들 (신규)
# ============================================================================

def display_prediction_comparison_table(comparison_df):
    """예측 결과 비교 테이블 표시"""
    if len(comparison_df) == 0:
        st.warning("비교할 데이터가 없습니다.")
        return
    
    st.dataframe(comparison_df, use_container_width=True, hide_index=True)

def display_pattern_analysis_results(analysis_result, selected_prefix=None):
    """패턴 분석 결과 표시"""
    if analysis_result is None:
        st.warning("분석 결과가 없습니다.")
        return
    
    summary_df = analysis_result['summary_df']
    detailed_results = analysis_result['detailed_results']
    
    st.subheader(f"📊 Prefix별 Suffix 시계열 패턴 분석 (Window Size: {analysis_result['window_size']})")
    st.caption(f"총 {analysis_result['total_prefixes_analyzed']}개 prefix 분석")
    
    # 패턴 요약 통계
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        random_count = len(summary_df[summary_df['is_random'] == True])
        pattern_count = len(summary_df[summary_df['is_random'] == False])
        st.metric("랜덤 패턴", random_count)
        st.metric("비랜덤 패턴", pattern_count)
    
    with col2:
        trend_count = len(summary_df[summary_df['has_trend'] == True])
        st.metric("트렌드 존재", trend_count)
    
    with col3:
        periodicity_count = len(summary_df[summary_df['has_periodicity'] == True])
        st.metric("주기성 존재", periodicity_count)
    
    with col4:
        markov_count = len(summary_df[summary_df['has_markov_dependency'] == True])
        st.metric("마르코프 의존성", markov_count)
    
    # 패턴 타입별 분포
    if 'pattern_summary' in summary_df.columns:
        pattern_types = summary_df['pattern_summary'].value_counts().head(10)
        
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=pattern_types.index,
            y=pattern_types.values,
            marker_color='#3498db'
        ))
        fig.update_layout(
            title="패턴 타입별 Prefix 개수",
            xaxis_title="패턴 타입",
            yaxis_title="Prefix 개수",
            height=400,
            xaxis={'tickangle': -45}
        )
        st.plotly_chart(fig, use_container_width=True)
    
    # 요약 테이블
    display_columns = ['prefix', 'total_count', 'b_ratio', 'p_ratio', 
                      'pattern_summary', 'is_random', 'has_trend', 
                      'has_periodicity', 'has_markov_dependency']
    
    st.dataframe(
        summary_df[display_columns].round(3),
        use_container_width=True,
        hide_index=True
    )

def display_cumulative_validation_results(validation_result):
    """시계열 누적 검증 결과 표시"""
    if validation_result is None:
        st.warning("검증 결과가 없습니다.")
        return
    
    results_by_method = validation_result['results_by_method']
    
    st.subheader(f"📈 시계열 누적 검증 결과 (Window Size: {validation_result['window_size']})")
    
    # 방법별 요약
    summary_data = []
    for method, result in results_by_method.items():
        summary_data.append({
            '방법': method,
            '테스트_Grid수': result['total_tested'],
            '전체_스텝': result['total_steps'],
            '정확한_예측': result['total_correct'],
            '틀린_예측': result['total_incorrect'],
            '전체_정확도': f"{result['overall_accuracy']:.2f}%"
        })
    
    summary_df = pd.DataFrame(summary_data)
    st.dataframe(summary_df, use_container_width=True, hide_index=True)
    
    # 방법별 정확도 비교 차트
    if len(summary_data) > 0:
        fig = go.Figure()
        methods = [d['방법'] for d in summary_data]
        accuracies = [float(d['전체_정확도'].replace('%', '')) for d in summary_data]
        
        fig.add_trace(go.Bar(
            x=methods,
            y=accuracies,
            marker_color='#3498db',
            text=[f"{a:.1f}%" for a in accuracies],
            textposition='outside'
        ))
        fig.update_layout(
            title="방법별 전체 정확도 비교",
            xaxis_title="예측 방법",
            yaxis_title="정확도 (%)",
            height=400
        )
        st.plotly_chart(fig, use_container_width=True)
    
    # 상세 결과 (탭으로 표시)
    if len(results_by_method) > 0:
        method_tabs = st.tabs(list(results_by_method.keys()))
        
        for tab_idx, (method, result) in enumerate(results_by_method.items()):
            with method_tabs[tab_idx]:
                st.write(f"**{method} 상세 결과**")
                
                if len(result['grid_string_results']) > 0:
                    # Grid String별 결과 테이블
                    grid_results = []
                    for gr in result['grid_string_results']:
                        grid_results.append({
                            'Grid String ID': gr['grid_string_id'],
                            '스텝 수': gr['total_steps'],
                            '정확한 예측': gr['correct'],
                            '틀린 예측': gr['incorrect'],
                            '정확도': f"{gr['accuracy']:.2f}%"
                        })
                    
                    grid_df = pd.DataFrame(grid_results)
                    st.dataframe(grid_df, use_container_width=True, hide_index=True)
                    
                    # 히스토리 차트 (첫 번째 grid_string)
                    if len(result['grid_string_results']) > 0:
                        first_history = result['grid_string_results'][0]['history']
                        if len(first_history) > 0:
                            st.write("**첫 번째 Grid String의 예측 히스토리**")
                            
                            steps = [h['step'] for h in first_history]
                            predicted_numeric = [0 if h['predicted'] == 'b' else 1 for h in first_history]
                            actual_numeric = [0 if h['actual'] == 'b' else 1 for h in first_history]
                            
                            fig = go.Figure()
                            fig.add_trace(go.Scatter(
                                x=steps,
                                y=predicted_numeric,
                                mode='lines+markers',
                                name='예측값 (0=B, 1=P)',
                                line=dict(color='blue', width=2)
                            ))
                            fig.add_trace(go.Scatter(
                                x=steps,
                                y=actual_numeric,
                                mode='lines+markers',
                                name='실제값 (0=B, 1=P)',
                                line=dict(color='red', width=2, dash='dash')
                            ))
                            fig.update_layout(
                                title=f"Grid String ID {result['grid_string_results'][0]['grid_string_id']} 예측 히스토리",
                                xaxis_title="스텝",
                                yaxis_title="값 (0=B, 1=P)",
                                height=400
                            )
                            st.plotly_chart(fig, use_container_width=True)

# ============================================================================
# Main 함수 (Streamlit UI)
# ============================================================================

def main():
    st.title("📊 Prefix Suffix 예측 비교 및 패턴 검출")
    st.markdown("Prefix별로 여러 예측 방법의 결과를 비교하고, 6가지 패턴 검출 방법의 결과를 확인합니다.")
    st.markdown("---")
    
    # 데이터 로드
    df_strings = load_preprocessed_data()
    
    if len(df_strings) == 0:
        st.warning("⚠️ 전처리된 데이터가 없습니다.")
        return
    
    # 설정 영역
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        window_size = st.selectbox(
            "윈도우 크기",
            options=[5, 6, 7, 8, 9],
            index=2  # 7을 기본값으로
        )
    
    with col2:
        methods = st.multiselect(
            "예측 방법 선택",
            options=["빈도 기반", "가중치 기반", "안전 우선", "균형 회복 트렌드"],
            default=["빈도 기반", "가중치 기반"]
        )
    
    with col3:
        include_patterns = st.checkbox("패턴 분석 포함 (6가지 방법)", value=True)
    
    with col4:
        cutoff_id = st.selectbox(
            "학습 데이터 기준",
            options=[None] + sorted(df_strings['id'].tolist()),
            format_func=lambda x: f"전체" if x is None else f"ID {x}",
            key="cutoff_id"
        )
    
    st.markdown("---")
    
    # Prefix 필터링 옵션
    col1, col2 = st.columns(2)
    with col1:
        min_occurrence = st.number_input("최소 출현 횟수", min_value=1, value=5, key="min_occ")
    with col2:
        prefix_search = st.text_input("Prefix 검색 (선택사항)", key="prefix_search")
    
    if st.button("분석 실행", type="primary", use_container_width=True):
        if len(methods) == 0:
            st.warning("예측 방법을 최소 1개 이상 선택해주세요.")
        else:
            with st.spinner("데이터 로딩 및 모델 구축 중..."):
                # 학습 데이터 설정
                if cutoff_id:
                    train_ids = df_strings[df_strings['id'] <= cutoff_id]['id'].tolist()
                else:
                    train_ids = df_strings['id'].tolist()
                
                # 학습 데이터 로드
                train_ngrams = load_ngram_chunks(window_size=window_size, grid_string_ids=train_ids)
                
                if len(train_ngrams) == 0:
                    st.warning("⚠️ 학습 데이터가 없습니다.")
                else:
                    # 모델 구축
                    models = build_all_models(train_ngrams, window_size, methods)
                    
                    # Prefix 목록 추출 (최소 출현 횟수 필터링)
                    prefix_counts = train_ngrams.groupby('prefix').size().reset_index(name='count')
                    prefix_counts = prefix_counts[prefix_counts['count'] >= min_occurrence]
                    
                    if prefix_search:
                        prefix_counts = prefix_counts[prefix_counts['prefix'].str.contains(prefix_search, case=False)]
                    
                    if len(prefix_counts) == 0:
                        st.warning("조건에 맞는 prefix가 없습니다.")
                    else:
                        st.success(f"✅ {len(prefix_counts)}개 prefix 분석")
                        
                        # 예측 결과 비교 (패턴 분석 항상 포함)
                        comparison_df = compare_prediction_methods(
                            models,
                            prefix_counts['prefix'].tolist(),
                            include_patterns=True,  # 항상 패턴 분석 포함
                            window_size=window_size
                        )
                        
                        # 결과 표시
                        st.subheader("📋 Prefix별 예측 결과 및 패턴 검출 결과")
                        st.caption("각 prefix에 대해 예측 방법별 결과와 6가지 패턴 검출 방법의 결과를 표시합니다.")
                        display_prediction_comparison_table(comparison_df)
                        
                        # 패턴 검출 방법별 요약 통계
                        if include_patterns and len(comparison_df) > 0:
                            st.subheader("📊 패턴 검출 방법별 요약 통계")
                            
                            col1, col2, col3 = st.columns(3)
                            
                            with col1:
                                if 'RunsTest_랜덤여부' in comparison_df.columns:
                                    runs_random = len(comparison_df[comparison_df['RunsTest_랜덤여부'] == '랜덤'])
                                    runs_non_random = len(comparison_df[comparison_df['RunsTest_랜덤여부'] == '비랜덤'])
                                    st.metric("Runs Test - 랜덤", runs_random)
                                    st.metric("Runs Test - 비랜덤", runs_non_random)
                            
                            with col2:
                                if '트렌드_유의성' in comparison_df.columns:
                                    trend_significant = len(comparison_df[comparison_df['트렌드_유의성'] == '유의함'])
                                    st.metric("트렌드 분석 - 유의함", trend_significant)
                            
                            with col3:
                                if '주기성_존재' in comparison_df.columns:
                                    periodicity_yes = len(comparison_df[comparison_df['주기성_존재'] == '있음'])
                                    st.metric("주기성 - 있음", periodicity_yes)
                            
                            col4, col5, col6 = st.columns(3)
                            
                            with col4:
                                if '마르코프_의존성' in comparison_df.columns:
                                    markov_yes = len(comparison_df[comparison_df['마르코프_의존성'] == '있음'])
                                    st.metric("마르코프 의존성 - 있음", markov_yes)
                            
                            with col5:
                                if '순환패턴' in comparison_df.columns:
                                    cycle_yes = len(comparison_df[comparison_df['순환패턴'] != '없음'])
                                    st.metric("순환 패턴 - 있음", cycle_yes)
                            
                            with col6:
                                if '변화점_개수' in comparison_df.columns:
                                    change_points_df = comparison_df[comparison_df['변화점_개수'] != '-']
                                    if len(change_points_df) > 0:
                                        change_points_df['변화점_개수'] = pd.to_numeric(change_points_df['변화점_개수'], errors='coerce')
                                        change_points_yes = len(change_points_df[change_points_df['변화점_개수'] > 0])
                                        st.metric("변화점 - 있음", change_points_yes)
                                    else:
                                        st.metric("변화점 - 있음", 0)
                            
                            # 예측 가능성 측정 지표
                            st.markdown("---")
                            st.subheader("📈 예측 가능성 측정 지표")
                            
                            col7, col8 = st.columns(2)
                            
                            with col7:
                                if '엔트로피_예측가능성' in comparison_df.columns:
                                    entropy_df = comparison_df[comparison_df['엔트로피_예측가능성'] != '-']
                                    if len(entropy_df) > 0:
                                        entropy_df['엔트로피_예측가능성'] = pd.to_numeric(entropy_df['엔트로피_예측가능성'], errors='coerce')
                                        predictable_count = len(entropy_df[entropy_df['엔트로피_예측가능성'] > 0.5])
                                        random_count = len(entropy_df[entropy_df['엔트로피_예측가능성'] < 0.2])
                                        st.metric("엔트로피 - 예측가능 (예측가능성 > 0.5)", predictable_count)
                                        st.metric("엔트로피 - 랜덤 (예측가능성 < 0.2)", random_count)
                                        
                                        # 평균 예측가능성
                                        avg_predictability = entropy_df['엔트로피_예측가능성'].mean()
                                        st.metric("평균 예측가능성", f"{avg_predictability:.3f}")
                            
                            with col8:
                                if '이동평균_해석' in comparison_df.columns:
                                    imbalance_df = comparison_df[comparison_df['이동평균_해석'] == '불균형_패턴']
                                    st.metric("이동평균 - 불균형 패턴", len(imbalance_df))
                                    
                                    # 변화점이 있는 prefix
                                    change_df = comparison_df[comparison_df['이동평균_변화점수'] != '-']
                                    if len(change_df) > 0:
                                        change_df['이동평균_변화점수'] = pd.to_numeric(change_df['이동평균_변화점수'], errors='coerce')
                                        change_points_yes = len(change_df[change_df['이동평균_변화점수'] > 0])
                                        st.metric("이동평균 - 변화점 있음", change_points_yes)
                                        
                                        # 평균 불균형
                                        imbalance_df2 = comparison_df[comparison_df['이동평균_불균형'] != '-']
                                        if len(imbalance_df2) > 0:
                                            imbalance_df2['이동평균_불균형'] = pd.to_numeric(imbalance_df2['이동평균_불균형'], errors='coerce')
                                            avg_imbalance = imbalance_df2['이동평균_불균형'].mean()
                                            st.metric("평균 불균형", f"{avg_imbalance:.3f}")

if __name__ == "__main__":
    main()

