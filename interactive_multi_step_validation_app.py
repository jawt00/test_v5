"""
인터랙티브 다단계 예측 시나리오 검증 앱
인터랙티브 다단계 예측 시나리오를 자동으로 검증하는 시스템
"""

import streamlit as st

# 페이지 설정 (모든 import 전에 실행되어야 함)
st.set_page_config(
    page_title="Interactive Multi-Step Validation",
    page_icon="🌳",
    layout="wide"
)

import pandas as pd
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime

# 기존 앱의 함수들 import
# 주의: hypothesis_validation_app.py도 set_page_config()를 호출하지만,
# 이미 위에서 호출했으므로 무시됩니다.
from hypothesis_validation_app import (
    get_db_connection,
    load_preprocessed_data,
    load_ngram_chunks,
    build_frequency_model,
    build_weighted_model,
    predict_for_prefix,
    predict_with_fallback_interval,
    get_next_prefix
)

# DB 경로
DB_PATH = 'hypothesis_validation.db'

def get_prediction_from_stored_table(prefix, window_size, method, threshold=0):
    """
    stored_predictions 테이블에서 예측값 조회
    
    Args:
        prefix: 예측할 prefix 문자열
        window_size: 윈도우 크기
        method: 예측 방법 ("빈도 기반" 또는 "가중치 기반")
        threshold: 임계값 (기본값: 0)
    
    Returns:
        dict: {
            'predicted': 예측값 ('b' 또는 'p' 또는 None),
            'confidence': 신뢰도,
            'ratios': {'b': b_ratio, 'p': p_ratio},
            'is_forced': False (테이블에서 가져온 예측은 강제 예측이 아님)
        }
    """
    conn = get_db_connection()
    if conn is None:
        return {
            'predicted': None,
            'confidence': 0.0,
            'ratios': {},
            'is_forced': False
        }
    
    try:
        query = """
            SELECT predicted_value, confidence, b_ratio, p_ratio
            FROM stored_predictions
            WHERE window_size = ?
              AND prefix = ?
              AND method = ?
              AND threshold = ?
            LIMIT 1
        """
        
        df = pd.read_sql_query(
            query,
            conn,
            params=[window_size, prefix, method, threshold]
        )
        
        if len(df) > 0:
            row = df.iloc[0]
            return {
                'predicted': row['predicted_value'],
                'confidence': row['confidence'],
                'ratios': {'b': row['b_ratio'], 'p': row['p_ratio']},
                'is_forced': False
            }
        else:
            return {
                'predicted': None,
                'confidence': 0.0,
                'ratios': {},
                'is_forced': False
            }
            
    except Exception as e:
        return {
            'predicted': None,
            'confidence': 0.0,
            'ratios': {},
            'is_forced': False
        }
    finally:
        conn.close()

def predict_with_fallback_interval_stored(
    prefix,
    window_size,
    method,
    threshold=60,
    max_interval=6,
    current_interval=0,
    stored_threshold=0
):
    """
    stored_predictions 테이블 기반 임계값 전략 예측 (강제 예측 포함)
    
    Args:
        prefix: 예측할 prefix 문자열
        window_size: 윈도우 크기
        method: 예측 방법
        threshold: 임계값 (%)
        max_interval: 최대 예측 없음 간격
        current_interval: 현재 예측 없음 간격
        stored_threshold: stored_predictions에서 조회할 임계값
    
    Returns:
        dict: {
            'predicted': 예측값,
            'confidence': 신뢰도,
            'ratios': 비율 딕셔너리,
            'is_forced': 강제 예측 여부
        }
    """
    # stored_predictions에서 예측값 조회
    result = get_prediction_from_stored_table(prefix, window_size, method, stored_threshold)
    
    predicted = result.get('predicted')
    confidence = result.get('confidence', 0.0)
    ratios = result.get('ratios', {})
    
    if predicted is None:
        # prefix가 테이블에 없는 경우
        return {
            'predicted': None,
            'confidence': 0.0,
            'ratios': {},
            'is_forced': False
        }
    
    # 임계값 전략 적용
    if confidence >= threshold:
        # 신뢰도가 임계값 이상이면 예측
        return {
            'predicted': predicted,
            'confidence': confidence,
            'ratios': ratios,
            'is_forced': False
        }
    elif current_interval >= max_interval:
        # 간격이 최대치를 넘으면 강제 예측
        return {
            'predicted': predicted,
            'confidence': confidence,
            'ratios': ratios,
            'is_forced': True
        }
    else:
        # 예측 안 함
        return {
            'predicted': None,
            'confidence': confidence,
            'ratios': ratios,
            'is_forced': False
        }

def create_validation_tables():
    """검증 결과 저장을 위한 테이블 생성"""
    conn = get_db_connection()
    if conn is None:
        return False
    
    cursor = conn.cursor()
    
    try:
        # 1. 검증 세션 메타데이터 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS interactive_validation_sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT NOT NULL UNIQUE,
                cutoff_grid_string_id INTEGER NOT NULL,
                window_size INTEGER NOT NULL,
                method TEXT NOT NULL,
                use_threshold BOOLEAN NOT NULL,
                threshold REAL,
                max_interval INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours'))
            )
        ''')
        
        # 2. 전략별 요약 통계 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS interactive_validation_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT NOT NULL,
                strategy_type TEXT NOT NULL,
                total_grid_strings INTEGER NOT NULL,
                avg_accuracy REAL NOT NULL,
                max_consecutive_failures INTEGER NOT NULL,
                avg_max_consecutive_failures REAL NOT NULL,
                prediction_rate REAL NOT NULL,
                forced_prediction_rate REAL NOT NULL,
                forced_success_rate REAL NOT NULL,
                total_steps INTEGER NOT NULL,
                total_failures INTEGER NOT NULL,
                total_predictions INTEGER NOT NULL,
                total_forced_predictions INTEGER NOT NULL,
                total_forced_successes INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                FOREIGN KEY (validation_id) REFERENCES interactive_validation_sessions(validation_id),
                UNIQUE(validation_id, strategy_type)
            )
        ''')
        
        # 3. Grid String별 상세 결과 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS interactive_validation_grid_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT NOT NULL,
                strategy_type TEXT NOT NULL,
                grid_string_id INTEGER NOT NULL,
                max_consecutive_failures INTEGER NOT NULL,
                total_steps INTEGER NOT NULL,
                total_failures INTEGER NOT NULL,
                total_predictions INTEGER NOT NULL,
                accuracy REAL NOT NULL,
                forced_prediction_rate REAL NOT NULL,
                forced_success_rate REAL NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                FOREIGN KEY (validation_id) REFERENCES interactive_validation_sessions(validation_id),
                FOREIGN KEY (grid_string_id) REFERENCES preprocessed_grid_strings(id),
                UNIQUE(validation_id, strategy_type, grid_string_id)
            )
        ''')
        
        # 인덱스 생성
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_validation_sessions_created_at 
            ON interactive_validation_sessions(created_at)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_validation_sessions_cutoff 
            ON interactive_validation_sessions(cutoff_grid_string_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_validation_sessions_settings 
            ON interactive_validation_sessions(window_size, method, use_threshold)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_validation_summaries_validation_id 
            ON interactive_validation_summaries(validation_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_validation_summaries_strategy 
            ON interactive_validation_summaries(strategy_type)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_validation_summaries_max_failures 
            ON interactive_validation_summaries(max_consecutive_failures)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_validation_summaries_accuracy 
            ON interactive_validation_summaries(avg_accuracy)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_validation_grid_results_validation_id 
            ON interactive_validation_grid_results(validation_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_validation_grid_results_strategy 
            ON interactive_validation_grid_results(strategy_type)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_validation_grid_results_grid_string_id 
            ON interactive_validation_grid_results(grid_string_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_validation_grid_results_max_failures 
            ON interactive_validation_grid_results(max_consecutive_failures)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_validation_grid_results_accuracy 
            ON interactive_validation_grid_results(accuracy)
        ''')
        
        # 4. 신뢰도 구간별 통계 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS confidence_statistics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT,
                strategy_type TEXT,
                confidence_range TEXT NOT NULL,
                total_predictions INTEGER NOT NULL,
                matches INTEGER NOT NULL,
                mismatches INTEGER NOT NULL,
                match_rate REAL NOT NULL,
                avg_confidence REAL NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                UNIQUE(validation_id, strategy_type, confidence_range)
            )
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_confidence_statistics_validation_id 
            ON confidence_statistics(validation_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_confidence_statistics_strategy 
            ON confidence_statistics(strategy_type)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_confidence_statistics_range 
            ON confidence_statistics(confidence_range)
        ''')
        
        # 5. 신뢰도 스킵 전략 검증 세션 테이블 (2개 임계값 비교용)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS confidence_skip_validation_sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT NOT NULL UNIQUE,
                cutoff_grid_string_id INTEGER NOT NULL,
                window_size INTEGER NOT NULL,
                method TEXT NOT NULL,
                use_threshold BOOLEAN NOT NULL,
                threshold REAL,
                max_interval INTEGER NOT NULL,
                confidence_skip_threshold_1 REAL NOT NULL,
                confidence_skip_threshold_2 REAL NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours'))
            )
        ''')
        
        # 6. 신뢰도 스킵 전략 요약 통계 테이블 (임계값별)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS confidence_skip_validation_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT NOT NULL,
                confidence_skip_threshold REAL NOT NULL,
                total_grid_strings INTEGER NOT NULL,
                avg_accuracy REAL NOT NULL,
                max_consecutive_failures INTEGER NOT NULL,
                avg_max_consecutive_failures REAL NOT NULL,
                prediction_rate REAL NOT NULL,
                forced_prediction_rate REAL NOT NULL,
                forced_success_rate REAL NOT NULL,
                total_steps INTEGER NOT NULL,
                total_failures INTEGER NOT NULL,
                total_predictions INTEGER NOT NULL,
                total_forced_predictions INTEGER NOT NULL,
                total_forced_successes INTEGER NOT NULL,
                total_skipped_predictions INTEGER NOT NULL,
                avg_first_success_step REAL,
                min_first_success_step INTEGER,
                max_first_success_step INTEGER,
                total_with_success INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                FOREIGN KEY (validation_id) REFERENCES confidence_skip_validation_sessions(validation_id),
                UNIQUE(validation_id, confidence_skip_threshold)
            )
        ''')
        
        # 7. 신뢰도 스킵 전략 Grid String별 상세 결과 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS confidence_skip_validation_grid_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT NOT NULL,
                confidence_skip_threshold REAL NOT NULL,
                grid_string_id INTEGER NOT NULL,
                max_consecutive_failures INTEGER NOT NULL,
                total_steps INTEGER NOT NULL,
                total_failures INTEGER NOT NULL,
                total_predictions INTEGER NOT NULL,
                total_skipped_predictions INTEGER NOT NULL,
                accuracy REAL NOT NULL,
                forced_prediction_rate REAL NOT NULL,
                forced_success_rate REAL NOT NULL,
                first_success_step INTEGER,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                FOREIGN KEY (validation_id) REFERENCES confidence_skip_validation_sessions(validation_id),
                FOREIGN KEY (grid_string_id) REFERENCES preprocessed_grid_strings(id),
                UNIQUE(validation_id, confidence_skip_threshold, grid_string_id)
            )
        ''')
        
        # 8. 신뢰도 스킵 전략 스텝별 상세 히스토리 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS confidence_skip_validation_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT NOT NULL,
                confidence_skip_threshold REAL NOT NULL,
                grid_string_id INTEGER NOT NULL,
                step INTEGER NOT NULL,
                prefix TEXT,
                predicted TEXT,
                actual TEXT,
                is_correct INTEGER,
                confidence REAL,
                is_forced INTEGER NOT NULL,
                current_interval INTEGER NOT NULL,
                has_prediction INTEGER NOT NULL,
                validated INTEGER NOT NULL,
                skipped INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                FOREIGN KEY (validation_id) REFERENCES confidence_skip_validation_sessions(validation_id),
                FOREIGN KEY (grid_string_id) REFERENCES preprocessed_grid_strings(id)
            )
        ''')
        
        # 인덱스 생성
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_confidence_skip_sessions_created_at 
            ON confidence_skip_validation_sessions(created_at)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_confidence_skip_sessions_cutoff 
            ON confidence_skip_validation_sessions(cutoff_grid_string_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_confidence_skip_summaries_validation_id 
            ON confidence_skip_validation_summaries(validation_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_confidence_skip_summaries_threshold 
            ON confidence_skip_validation_summaries(confidence_skip_threshold)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_confidence_skip_grid_results_validation_id 
            ON confidence_skip_validation_grid_results(validation_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_confidence_skip_grid_results_threshold 
            ON confidence_skip_validation_grid_results(confidence_skip_threshold)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_confidence_skip_grid_results_grid_string_id 
            ON confidence_skip_validation_grid_results(grid_string_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_confidence_skip_grid_results_first_success 
            ON confidence_skip_validation_grid_results(first_success_step)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_confidence_skip_steps_validation_id 
            ON confidence_skip_validation_steps(validation_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_confidence_skip_steps_threshold 
            ON confidence_skip_validation_steps(confidence_skip_threshold)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_confidence_skip_steps_grid_string_id 
            ON confidence_skip_validation_steps(grid_string_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_confidence_skip_steps_step 
            ON confidence_skip_validation_steps(step)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_confidence_skip_steps_validation_grid_step 
            ON confidence_skip_validation_steps(validation_id, grid_string_id, step)
        ''')
        
        # 9. 세션별 예측값 테이블 스냅샷 테이블
        # interactive_validation_sessions와 confidence_skip_validation_sessions 모두 지원
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS validation_session_prediction_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT NOT NULL,
                window_size INTEGER NOT NULL,
                prefix TEXT NOT NULL,
                predicted_value TEXT,
                confidence REAL,
                b_ratio REAL,
                p_ratio REAL,
                method TEXT NOT NULL,
                threshold REAL NOT NULL,
                snapshot_created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                UNIQUE(validation_id, window_size, prefix, method, threshold)
            )
        ''')
        
        # 10. 세션별 검증한 Grid String ID 리스트 테이블
        # interactive_validation_sessions와 confidence_skip_validation_sessions 모두 지원
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS validation_session_grid_strings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT NOT NULL,
                grid_string_id INTEGER NOT NULL,
                sequence_order INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                FOREIGN KEY (grid_string_id) REFERENCES preprocessed_grid_strings(id),
                UNIQUE(validation_id, grid_string_id)
            )
        ''')
        
        # 인덱스 생성
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_prediction_snapshots_validation_id 
            ON validation_session_prediction_snapshots(validation_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_prediction_snapshots_window_method_threshold 
            ON validation_session_prediction_snapshots(window_size, method, threshold)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_session_grid_strings_validation_id 
            ON validation_session_grid_strings(validation_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_session_grid_strings_grid_string_id 
            ON validation_session_grid_strings(grid_string_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_session_grid_strings_sequence 
            ON validation_session_grid_strings(validation_id, sequence_order)
        ''')
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        st.error(f"테이블 생성 중 오류 발생: {str(e)}")
        return False
    finally:
        conn.close()

def collect_confidence_statistics(history, validation_id=None, strategy_type=None):
    """
    히스토리에서 신뢰도 구간별 통계 수집 (50-60% 구간, 1% 간격)
    
    Args:
        history: 검증 히스토리 리스트
        validation_id: 검증 ID (선택적)
        strategy_type: 전략 타입 (선택적)
    
    Returns:
        dict: 신뢰도 구간별 통계
    """
    # 신뢰도 구간별 통계 초기화 (50-60%, 1% 간격)
    confidence_ranges = {}
    for i in range(50, 61):  # 50, 51, 52, ..., 60
        range_key = f"{i}-{i+1}" if i < 60 else "60+"
        confidence_ranges[range_key] = {
            'total_predictions': 0,
            'matches': 0,
            'mismatches': 0,
            'confidence_sum': 0.0
        }
    
    # 히스토리에서 통계 수집
    for entry in history:
        has_prediction = entry.get('has_prediction', False)
        is_correct = entry.get('is_correct')
        confidence = entry.get('confidence', 0.0)
        validated = entry.get('validated', False)
        
        # 예측값이 있고 검증된 경우만 통계에 포함
        if has_prediction and validated and is_correct is not None:
            # 신뢰도 구간 결정
            conf_int = int(confidence)
            if conf_int < 50:
                continue  # 50% 미만은 제외
            elif conf_int >= 60:
                range_key = "60+"
            else:
                range_key = f"{conf_int}-{conf_int+1}"
            
            if range_key in confidence_ranges:
                confidence_ranges[range_key]['total_predictions'] += 1
                confidence_ranges[range_key]['confidence_sum'] += confidence
                
                if is_correct:
                    confidence_ranges[range_key]['matches'] += 1
                else:
                    confidence_ranges[range_key]['mismatches'] += 1
    
    # 통계 계산 및 정리
    statistics = []
    for range_key, stats in confidence_ranges.items():
        if stats['total_predictions'] > 0:
            match_rate = (stats['matches'] / stats['total_predictions']) * 100
            avg_confidence = stats['confidence_sum'] / stats['total_predictions']
            
            statistics.append({
                'confidence_range': range_key,
                'total_predictions': stats['total_predictions'],
                'matches': stats['matches'],
                'mismatches': stats['mismatches'],
                'match_rate': match_rate,
                'avg_confidence': avg_confidence
            })
    
    return statistics

def save_confidence_statistics(statistics, validation_id=None, strategy_type=None):
    """
    신뢰도 통계를 DB에 저장
    
    Args:
        statistics: collect_confidence_statistics()의 반환값
        validation_id: 검증 ID (선택적)
        strategy_type: 전략 타입 (선택적)
    """
    conn = get_db_connection()
    if conn is None:
        return False
    
    cursor = conn.cursor()
    
    try:
        for stat in statistics:
            cursor.execute('''
                INSERT OR REPLACE INTO confidence_statistics (
                    validation_id, strategy_type, confidence_range,
                    total_predictions, matches, mismatches,
                    match_rate, avg_confidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
            ''', (
                validation_id,
                strategy_type,
                stat['confidence_range'],
                stat['total_predictions'],
                stat['matches'],
                stat['mismatches'],
                stat['match_rate'],
                stat['avg_confidence']
            ))
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        st.error(f"신뢰도 통계 저장 중 오류 발생: {str(e)}")
        return False
    finally:
        conn.close()

def save_confidence_skip_validation_results(
    cutoff_grid_string_id,
    window_size,
    method,
    use_threshold,
    threshold,
    max_interval,
    confidence_skip_threshold_1,
    confidence_skip_threshold_2,
    batch_results_1,
    batch_results_2,
    grid_string_ids=None
):
    """
    신뢰도 스킵 전략 검증 결과를 DB에 저장 (2개 임계값 비교)
    
    Args:
        cutoff_grid_string_id: 기준 grid_string ID
        window_size: 윈도우 크기
        method: 예측 방법
        use_threshold: 임계값 전략 사용 여부
        threshold: 임계값
        max_interval: 최대 예측 없음 간격
        confidence_skip_threshold_1: 첫 번째 스킵 신뢰도 임계값
        confidence_skip_threshold_2: 두 번째 스킵 신뢰도 임계값
        batch_results_1: 첫 번째 임계값 검증 결과
        batch_results_2: 두 번째 임계값 검증 결과
        grid_string_ids: 검증한 grid_string_id 리스트 (선택적)
    
    Returns:
        str: validation_id (저장 성공 시), None (실패 시)
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    cursor = conn.cursor()
    
    try:
        # validation_id 생성 (UUID)
        validation_id = str(uuid.uuid4())
        
        # 1. 검증 세션 저장
        cursor.execute('''
            INSERT INTO confidence_skip_validation_sessions (
                validation_id, cutoff_grid_string_id, window_size, method,
                use_threshold, threshold, max_interval,
                confidence_skip_threshold_1, confidence_skip_threshold_2,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
        ''', (
            validation_id,
            cutoff_grid_string_id,
            window_size,
            method,
            use_threshold,
            threshold if use_threshold else None,
            max_interval,
            confidence_skip_threshold_1,
            confidence_skip_threshold_2
        ))
        
        # 2. 첫 번째 임계값 요약 통계 저장
        if batch_results_1 and 'summary' in batch_results_1:
            summary_1 = batch_results_1['summary']
            cursor.execute('''
                INSERT INTO confidence_skip_validation_summaries (
                    validation_id, confidence_skip_threshold,
                    total_grid_strings, avg_accuracy, max_consecutive_failures,
                    avg_max_consecutive_failures, prediction_rate,
                    forced_prediction_rate, forced_success_rate,
                    total_steps, total_failures, total_predictions,
                    total_forced_predictions, total_forced_successes,
                    total_skipped_predictions,
                    avg_first_success_step, min_first_success_step, max_first_success_step,
                    total_with_success, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
            ''', (
                validation_id,
                confidence_skip_threshold_1,
                summary_1.get('total_grid_strings', 0),
                summary_1.get('avg_accuracy', 0.0),
                summary_1.get('max_consecutive_failures', 0),
                summary_1.get('avg_max_consecutive_failures', 0.0),
                summary_1.get('prediction_rate', 0.0),
                summary_1.get('forced_prediction_rate', 0.0),
                summary_1.get('forced_success_rate', 0.0),
                summary_1.get('total_steps', 0),
                summary_1.get('total_failures', 0),
                summary_1.get('total_predictions', 0),
                summary_1.get('total_forced_predictions', 0),
                summary_1.get('total_forced_successes', 0),
                summary_1.get('total_skipped_predictions', 0),
                summary_1.get('avg_first_success_step'),
                summary_1.get('min_first_success_step'),
                summary_1.get('max_first_success_step'),
                summary_1.get('total_with_success', 0)
            ))
            
            # Grid String별 결과 저장 (첫 번째 임계값)
            if 'results' in batch_results_1:
                for result in batch_results_1['results']:
                    cursor.execute('''
                        INSERT OR REPLACE INTO confidence_skip_validation_grid_results (
                            validation_id, confidence_skip_threshold, grid_string_id,
                            max_consecutive_failures, total_steps, total_failures,
                            total_predictions, total_skipped_predictions,
                            accuracy, forced_prediction_rate, forced_success_rate,
                            first_success_step, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
                    ''', (
                        validation_id,
                        confidence_skip_threshold_1,
                        result.get('grid_string_id'),
                        result.get('max_consecutive_failures', 0),
                        result.get('total_steps', 0),
                        result.get('total_failures', 0),
                        result.get('total_predictions', 0),
                        result.get('total_skipped_predictions', 0),
                        result.get('accuracy', 0.0),
                        result.get('forced_prediction_rate', 0.0),
                        result.get('forced_success_rate', 0.0),
                        result.get('first_success_step')
                    ))
                    
                    # 히스토리 저장 (첫 번째 임계값)
                    if 'history' in result and result['history']:
                        for entry in result['history']:
                            cursor.execute('''
                                INSERT INTO confidence_skip_validation_steps (
                                    validation_id, confidence_skip_threshold, grid_string_id,
                                    step, prefix, predicted, actual, is_correct,
                                    confidence, is_forced, current_interval,
                                    has_prediction, validated, skipped, created_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
                            ''', (
                                validation_id,
                                confidence_skip_threshold_1,
                                result.get('grid_string_id'),
                                entry.get('step', 0),
                                entry.get('prefix'),
                                entry.get('predicted'),
                                entry.get('actual'),
                                1 if entry.get('is_correct') is True else (0 if entry.get('is_correct') is False else None),
                                entry.get('confidence', 0.0),
                                1 if entry.get('is_forced', False) else 0,
                                entry.get('current_interval', 0),
                                1 if entry.get('has_prediction', False) else 0,
                                1 if entry.get('validated', False) else 0,
                                1 if entry.get('skipped', False) else 0
                            ))
        
        # 3. 두 번째 임계값 요약 통계 저장
        if batch_results_2 and 'summary' in batch_results_2:
            summary_2 = batch_results_2['summary']
            cursor.execute('''
                INSERT INTO confidence_skip_validation_summaries (
                    validation_id, confidence_skip_threshold,
                    total_grid_strings, avg_accuracy, max_consecutive_failures,
                    avg_max_consecutive_failures, prediction_rate,
                    forced_prediction_rate, forced_success_rate,
                    total_steps, total_failures, total_predictions,
                    total_forced_predictions, total_forced_successes,
                    total_skipped_predictions,
                    avg_first_success_step, min_first_success_step, max_first_success_step,
                    total_with_success, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
            ''', (
                validation_id,
                confidence_skip_threshold_2,
                summary_2.get('total_grid_strings', 0),
                summary_2.get('avg_accuracy', 0.0),
                summary_2.get('max_consecutive_failures', 0),
                summary_2.get('avg_max_consecutive_failures', 0.0),
                summary_2.get('prediction_rate', 0.0),
                summary_2.get('forced_prediction_rate', 0.0),
                summary_2.get('forced_success_rate', 0.0),
                summary_2.get('total_steps', 0),
                summary_2.get('total_failures', 0),
                summary_2.get('total_predictions', 0),
                summary_2.get('total_forced_predictions', 0),
                summary_2.get('total_forced_successes', 0),
                summary_2.get('total_skipped_predictions', 0),
                summary_2.get('avg_first_success_step'),
                summary_2.get('min_first_success_step'),
                summary_2.get('max_first_success_step'),
                summary_2.get('total_with_success', 0)
            ))
            
            # Grid String별 결과 저장 (두 번째 임계값)
            if 'results' in batch_results_2:
                for result in batch_results_2['results']:
                    cursor.execute('''
                        INSERT OR REPLACE INTO confidence_skip_validation_grid_results (
                            validation_id, confidence_skip_threshold, grid_string_id,
                            max_consecutive_failures, total_steps, total_failures,
                            total_predictions, total_skipped_predictions,
                            accuracy, forced_prediction_rate, forced_success_rate,
                            first_success_step, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
                    ''', (
                        validation_id,
                        confidence_skip_threshold_2,
                        result.get('grid_string_id'),
                        result.get('max_consecutive_failures', 0),
                        result.get('total_steps', 0),
                        result.get('total_failures', 0),
                        result.get('total_predictions', 0),
                        result.get('total_skipped_predictions', 0),
                        result.get('accuracy', 0.0),
                        result.get('forced_prediction_rate', 0.0),
                        result.get('forced_success_rate', 0.0),
                        result.get('first_success_step')
                    ))
                    
                    # 히스토리 저장 (두 번째 임계값)
                    if 'history' in result and result['history']:
                        for entry in result['history']:
                            cursor.execute('''
                                INSERT INTO confidence_skip_validation_steps (
                                    validation_id, confidence_skip_threshold, grid_string_id,
                                    step, prefix, predicted, actual, is_correct,
                                    confidence, is_forced, current_interval,
                                    has_prediction, validated, skipped, created_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
                            ''', (
                                validation_id,
                                confidence_skip_threshold_2,
                                result.get('grid_string_id'),
                                entry.get('step', 0),
                                entry.get('prefix'),
                                entry.get('predicted'),
                                entry.get('actual'),
                                1 if entry.get('is_correct') is True else (0 if entry.get('is_correct') is False else None),
                                entry.get('confidence', 0.0),
                                1 if entry.get('is_forced', False) else 0,
                                entry.get('current_interval', 0),
                                1 if entry.get('has_prediction', False) else 0,
                                1 if entry.get('validated', False) else 0,
                                1 if entry.get('skipped', False) else 0
                            ))
        
        # 4. 예측값 테이블 스냅샷 저장 (검증 시점에 실시간 계산)
        # cutoff_grid_string_id 기준으로 학습 데이터 구축하여 모델 생성
        # 학습 데이터에서 나올 수 있는 모든 prefix에 대한 예측값 계산 및 저장
        snapshot_threshold = threshold if use_threshold else 0.0
        
        try:
            # 학습 데이터 구축
            train_ids_query = "SELECT id FROM preprocessed_grid_strings WHERE id <= ? ORDER BY id"
            train_ids_df = pd.read_sql_query(train_ids_query, conn, params=[cutoff_grid_string_id])
            train_ids = train_ids_df['id'].tolist() if len(train_ids_df) > 0 else []
            
            if len(train_ids) > 0:
                # N-gram 로드
                train_ngrams = load_ngram_chunks(window_size=window_size, grid_string_ids=train_ids)
                
                if len(train_ngrams) > 0:
                    # 모델 구축
                    if method == "빈도 기반":
                        model = build_frequency_model(train_ngrams)
                    elif method == "가중치 기반":
                        model = build_weighted_model(train_ngrams)
                    else:
                        model = build_frequency_model(train_ngrams)
                    
                    # 학습 데이터에서 나올 수 있는 모든 prefix 추출
                    prefixes = set()
                    for ngram in train_ngrams:
                        if len(ngram) >= window_size:
                            prefix = ngram[:window_size-1]
                            prefixes.add(prefix)
                    
                    # 각 prefix에 대해 예측값 계산 및 저장
                    snapshot_count = 0
                    for prefix in prefixes:
                        prediction_result = predict_for_prefix(model, prefix, method)
                        predicted_value = prediction_result.get('predicted')
                        confidence = prediction_result.get('confidence', 0.0)
                        ratios = prediction_result.get('ratios', {})
                        b_ratio = ratios.get('b', 0.0) if ratios else 0.0
                        p_ratio = ratios.get('p', 0.0) if ratios else 0.0
                        
                        cursor.execute('''
                            INSERT INTO validation_session_prediction_snapshots (
                                validation_id, window_size, prefix, predicted_value,
                                confidence, b_ratio, p_ratio, method, threshold,
                                snapshot_created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
                        ''', (
                            validation_id, window_size, prefix, predicted_value,
                            confidence, b_ratio, p_ratio, method, snapshot_threshold
                        ))
                        snapshot_count += 1
                    
                    if snapshot_count > 0:
                        st.info(f"예측값 스냅샷 {snapshot_count}개 저장 완료")
        except Exception as e:
            # 예측값 스냅샷 저장 실패해도 세션 저장은 계속 진행
            st.warning(f"예측값 스냅샷 저장 중 오류 발생 (세션은 저장됨): {str(e)}")
        
        # 5. Grid String ID 리스트 저장
        if grid_string_ids and len(grid_string_ids) > 0:
            for order, grid_string_id in enumerate(grid_string_ids, start=1):
                cursor.execute('''
                    INSERT OR REPLACE INTO validation_session_grid_strings (
                        validation_id, grid_string_id, sequence_order, created_at
                    ) VALUES (?, ?, ?, datetime('now', '+9 hours'))
                ''', (validation_id, grid_string_id, order))
        
        conn.commit()
        return validation_id
        
    except Exception as e:
        conn.rollback()
        st.error(f"검증 결과 저장 중 오류 발생: {str(e)}")
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
        return None
    finally:
        conn.close()

def save_validation_results(
    cutoff_grid_string_id,
    window_size,
    method,
    use_threshold,
    threshold,
    max_interval,
    batch_results_default,
    batch_results_reverse,
    grid_string_ids=None
):
    """
    검증 결과를 DB에 저장
    
    Args:
        cutoff_grid_string_id: 기준 grid_string ID
        window_size: 윈도우 크기
        method: 예측 방법
        use_threshold: 임계값 전략 사용 여부
        threshold: 임계값
        max_interval: 최대 예측 없음 간격
        batch_results_default: 기본 전략 배치 결과
        batch_results_reverse: 반대 선택 전략 배치 결과
        grid_string_ids: 검증한 grid_string_id 리스트 (선택적)
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    cursor = conn.cursor()
    
    try:
        # validation_id 생성 (UUID)
        validation_id = str(uuid.uuid4())
        
        # 1. 검증 세션 저장
        cursor.execute('''
            INSERT INTO interactive_validation_sessions (
                validation_id, cutoff_grid_string_id, window_size, method,
                use_threshold, threshold, max_interval, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
        ''', (
            validation_id,
            cutoff_grid_string_id,
            window_size,
            method,
            use_threshold,
            threshold if use_threshold else None,
            max_interval
        ))
        
        # 2. 요약 통계 저장 (기본 전략)
        if batch_results_default and 'summary' in batch_results_default:
            summary_default = batch_results_default['summary']
            cursor.execute('''
                INSERT INTO interactive_validation_summaries (
                    validation_id, strategy_type, total_grid_strings,
                    avg_accuracy, max_consecutive_failures, avg_max_consecutive_failures,
                    prediction_rate, forced_prediction_rate, forced_success_rate,
                    total_steps, total_failures, total_predictions,
                    total_forced_predictions, total_forced_successes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
            ''', (
                validation_id,
                'default',
                summary_default.get('total_grid_strings', 0),
                summary_default.get('avg_accuracy', 0.0),
                summary_default.get('max_consecutive_failures', 0),
                summary_default.get('avg_max_consecutive_failures', 0.0),
                summary_default.get('prediction_rate', 0.0),
                summary_default.get('forced_prediction_rate', 0.0),
                summary_default.get('forced_success_rate', 0.0),
                summary_default.get('total_steps', 0),
                summary_default.get('total_failures', 0),
                summary_default.get('total_predictions', 0),
                summary_default.get('total_forced_predictions', 0),
                summary_default.get('total_forced_successes', 0)
            ))
            
            # Grid String별 결과 저장 (기본 전략)
            if 'results' in batch_results_default:
                for result in batch_results_default['results']:
                    cursor.execute('''
                        INSERT OR REPLACE INTO interactive_validation_grid_results (
                            validation_id, strategy_type, grid_string_id,
                            max_consecutive_failures, total_steps, total_failures,
                            total_predictions, accuracy, forced_prediction_rate,
                            forced_success_rate, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
                    ''', (
                        validation_id,
                        'default',
                        result.get('grid_string_id'),
                        result.get('max_consecutive_failures', 0),
                        result.get('total_steps', 0),
                        result.get('total_failures', 0),
                        result.get('total_predictions', 0),
                        result.get('accuracy', 0.0),
                        result.get('forced_prediction_rate', 0.0),
                        result.get('forced_success_rate', 0.0)
                    ))
        
        # 3. 요약 통계 저장 (반대 선택 전략)
        if batch_results_reverse and 'summary' in batch_results_reverse:
            summary_reverse = batch_results_reverse['summary']
            cursor.execute('''
                INSERT INTO interactive_validation_summaries (
                    validation_id, strategy_type, total_grid_strings,
                    avg_accuracy, max_consecutive_failures, avg_max_consecutive_failures,
                    prediction_rate, forced_prediction_rate, forced_success_rate,
                    total_steps, total_failures, total_predictions,
                    total_forced_predictions, total_forced_successes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
            ''', (
                validation_id,
                'reverse',
                summary_reverse.get('total_grid_strings', 0),
                summary_reverse.get('avg_accuracy', 0.0),
                summary_reverse.get('max_consecutive_failures', 0),
                summary_reverse.get('avg_max_consecutive_failures', 0.0),
                summary_reverse.get('prediction_rate', 0.0),
                summary_reverse.get('forced_prediction_rate', 0.0),
                summary_reverse.get('forced_success_rate', 0.0),
                summary_reverse.get('total_steps', 0),
                summary_reverse.get('total_failures', 0),
                summary_reverse.get('total_predictions', 0),
                summary_reverse.get('total_forced_predictions', 0),
                summary_reverse.get('total_forced_successes', 0)
            ))
            
            # Grid String별 결과 저장 (반대 선택 전략)
            if 'results' in batch_results_reverse:
                for result in batch_results_reverse['results']:
                    cursor.execute('''
                        INSERT OR REPLACE INTO interactive_validation_grid_results (
                            validation_id, strategy_type, grid_string_id,
                            max_consecutive_failures, total_steps, total_failures,
                            total_predictions, accuracy, forced_prediction_rate,
                            forced_success_rate, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
                    ''', (
                        validation_id,
                        'reverse',
                        result.get('grid_string_id'),
                        result.get('max_consecutive_failures', 0),
                        result.get('total_steps', 0),
                        result.get('total_failures', 0),
                        result.get('total_predictions', 0),
                        result.get('accuracy', 0.0),
                        result.get('forced_prediction_rate', 0.0),
                        result.get('forced_success_rate', 0.0)
                    ))
        
        # 4. 예측값 테이블 스냅샷 저장 (검증 시점에 실시간 계산)
        # cutoff_grid_string_id 기준으로 학습 데이터 구축하여 모델 생성
        # 학습 데이터에서 나올 수 있는 모든 prefix에 대한 예측값 계산 및 저장
        snapshot_threshold = threshold if use_threshold else 0.0
        
        try:
            # 학습 데이터 구축
            train_ids_query = "SELECT id FROM preprocessed_grid_strings WHERE id <= ? ORDER BY id"
            train_ids_df = pd.read_sql_query(train_ids_query, conn, params=[cutoff_grid_string_id])
            train_ids = train_ids_df['id'].tolist() if len(train_ids_df) > 0 else []
            
            if len(train_ids) > 0:
                # N-gram 로드
                train_ngrams = load_ngram_chunks(window_size=window_size, grid_string_ids=train_ids)
                
                if len(train_ngrams) > 0:
                    # 모델 구축
                    if method == "빈도 기반":
                        model = build_frequency_model(train_ngrams)
                    elif method == "가중치 기반":
                        model = build_weighted_model(train_ngrams)
                    else:
                        model = build_frequency_model(train_ngrams)
                    
                    # 학습 데이터에서 나올 수 있는 모든 prefix 추출
                    prefixes = set()
                    for ngram in train_ngrams:
                        if len(ngram) >= window_size:
                            prefix = ngram[:window_size-1]
                            prefixes.add(prefix)
                    
                    # 각 prefix에 대해 예측값 계산 및 저장
                    snapshot_count = 0
                    for prefix in prefixes:
                        prediction_result = predict_for_prefix(model, prefix, method)
                        predicted_value = prediction_result.get('predicted')
                        confidence = prediction_result.get('confidence', 0.0)
                        ratios = prediction_result.get('ratios', {})
                        b_ratio = ratios.get('b', 0.0) if ratios else 0.0
                        p_ratio = ratios.get('p', 0.0) if ratios else 0.0
                        
                        cursor.execute('''
                            INSERT INTO validation_session_prediction_snapshots (
                                validation_id, window_size, prefix, predicted_value,
                                confidence, b_ratio, p_ratio, method, threshold,
                                snapshot_created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
                        ''', (
                            validation_id, window_size, prefix, predicted_value,
                            confidence, b_ratio, p_ratio, method, snapshot_threshold
                        ))
                        snapshot_count += 1
                    
                    if snapshot_count > 0:
                        st.info(f"예측값 스냅샷 {snapshot_count}개 저장 완료")
        except Exception as e:
            # 예측값 스냅샷 저장 실패해도 세션 저장은 계속 진행
            st.warning(f"예측값 스냅샷 저장 중 오류 발생 (세션은 저장됨): {str(e)}")
        
        # 5. Grid String ID 리스트 저장
        if grid_string_ids and len(grid_string_ids) > 0:
            for order, grid_string_id in enumerate(grid_string_ids, start=1):
                cursor.execute('''
                    INSERT OR REPLACE INTO validation_session_grid_strings (
                        validation_id, grid_string_id, sequence_order, created_at
                    ) VALUES (?, ?, ?, datetime('now', '+9 hours'))
                ''', (validation_id, grid_string_id, order))
        
        conn.commit()
        return validation_id
        
    except Exception as e:
        conn.rollback()
        st.error(f"검증 결과 저장 중 오류 발생: {str(e)}")
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
        return None
    finally:
        conn.close()

def validate_interactive_multi_step_scenario_with_confidence_skip(
    grid_string_id,
    cutoff_grid_string_id,
    window_size=7,
    method="빈도 기반",
    use_threshold=True,
    threshold=60,
    max_interval=6,
    reverse_forced_prediction=False,
    confidence_skip_threshold=51,
    use_stored_predictions=False,
    stored_threshold=0
):
    """
    신뢰도 기반 스킵 규칙이 있는 인터랙티브 다단계 예측 시나리오 검증
    
    규칙:
    1. 기본 규칙은 기존과 동일
    2. 강제 예측 신뢰도가 51% 미만인 경우 해당 스텝은 스킵 (다음 스텝으로 진행)
    3. 스킵 상태에서 간격 계산은 멈춤 (증가하지 않음)
    4. 다음 스텝에서 임계값 만족 예측 또는 신뢰도 51% 이상 강제 예측이 나올 때까지 대기
    
    Args:
        grid_string_id: 검증할 grid_string의 ID
        cutoff_grid_string_id: 학습 데이터 기준 ID
        window_size: 윈도우 크기
        method: 예측 방법
        use_threshold: 임계값 전략 사용 여부
        threshold: 임계값
        max_interval: 최대 예측 없음 간격
        reverse_forced_prediction: 반대 선택 전략 사용 여부
        confidence_skip_threshold: 스킵할 신뢰도 임계값 (기본값: 51)
        use_stored_predictions: 예측값 테이블(stored_predictions) 사용 여부 (기본값: False)
        stored_threshold: stored_predictions 조회 시 사용할 임계값 (기본값: 0)
    
    Returns:
        dict: 검증 결과
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # grid_string 로드
        query = "SELECT grid_string FROM preprocessed_grid_strings WHERE id = ?"
        df = pd.read_sql_query(query, conn, params=[grid_string_id])
        
        if len(df) == 0:
            return None
        
        grid_string = df.iloc[0]['grid_string']
        
        if len(grid_string) < window_size:
            return {
                'grid_string_id': grid_string_id,
                'max_consecutive_failures': 0,
                'max_consecutive_matches': 0,
                'total_steps': 0,
                'total_failures': 0,
                'total_predictions': 0,
                'total_forced_predictions': 0,
                'total_skipped_predictions': 0,
                'forced_prediction_rate': 0.0,
                'accuracy': 0.0,
                'first_success_step': None,
                'history': []
            }
        
        # 예측값 테이블 사용 여부에 따라 모델 구축 또는 스킵
        model = None
        if not use_stored_predictions:
            # 실시간 모델 사용: 학습 데이터 구축 (검증 데이터 제외)
            # grid_string_id가 cutoff_grid_string_id 이하인 경우 학습 데이터에서 제외
            train_ids_query = "SELECT id FROM preprocessed_grid_strings WHERE id <= ? AND id < ? ORDER BY id"
            train_ids_df = pd.read_sql_query(train_ids_query, conn, params=[cutoff_grid_string_id, grid_string_id])
            train_ids = train_ids_df['id'].tolist() if len(train_ids_df) > 0 else []
            
            # N-gram 로드
            train_ngrams = load_ngram_chunks(window_size=window_size, grid_string_ids=train_ids)
            
            if len(train_ngrams) == 0:
                return {
                    'grid_string_id': grid_string_id,
                    'max_consecutive_failures': 0,
                    'max_consecutive_matches': 0,
                    'total_steps': 0,
                    'total_failures': 0,
                    'total_predictions': 0,
                    'total_forced_predictions': 0,
                    'total_skipped_predictions': 0,
                    'forced_prediction_rate': 0.0,
                    'accuracy': 0.0,
                    'first_success_step': None,
                    'history': []
                }
            
            # 모델 구축
            if method == "빈도 기반":
                model = build_frequency_model(train_ngrams)
            elif method == "가중치 기반":
                model = build_weighted_model(train_ngrams)
            else:
                model = build_frequency_model(train_ngrams)
        
        # 시나리오 방식으로 테스트
        prefix_length = window_size - 1
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        consecutive_matches = 0
        max_consecutive_matches = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_forced_predictions = 0
        total_skipped_predictions = 0
        total_forced_successes = 0
        current_interval = 0
        first_success_step = None  # 첫 번째 성공 스텝 추적
        
        # 초기 prefix 생성
        if len(grid_string) < prefix_length:
            return {
                'grid_string_id': grid_string_id,
                'max_consecutive_failures': 0,
                'max_consecutive_matches': 0,
                'total_steps': 0,
                'total_failures': 0,
                'total_predictions': 0,
                'total_forced_predictions': 0,
                'total_skipped_predictions': 0,
                'forced_prediction_rate': 0.0,
                'accuracy': 0.0,
                'first_success_step': None,
                'history': []
            }
        
        current_prefix = grid_string[:prefix_length]
        
        # 각 스텝마다 예측
        i = prefix_length
        while i < len(grid_string):
            total_steps += 1
            actual_value = grid_string[i]
            
            # 예측 수행 (기본 규칙: 모든 스텝에서 예측 시도)
            if use_stored_predictions:
                # 예측값 테이블 사용
                if use_threshold:
                    # 임계값 전략 사용: 임계값 이상일 때만 예측, 아니면 강제 예측
                    prediction_result = predict_with_fallback_interval_stored(
                        current_prefix,
                        window_size,
                        method,
                        threshold=threshold,
                        max_interval=max_interval,
                        current_interval=current_interval,
                        stored_threshold=stored_threshold
                    )
                else:
                    # 임계값 전략 미사용: 모든 스텝에서 예측
                    prediction_result = get_prediction_from_stored_table(
                        current_prefix,
                        window_size,
                        method,
                        stored_threshold
                    )
                    if 'is_forced' not in prediction_result:
                        prediction_result['is_forced'] = False
            else:
                # 실시간 모델 사용
                if use_threshold:
                    # 임계값 전략 사용: 임계값 이상일 때만 예측, 아니면 강제 예측
                    prediction_result = predict_with_fallback_interval(
                        model,
                        current_prefix,
                        method=method,
                        threshold=threshold,
                        max_interval=max_interval,
                        current_interval=current_interval
                    )
                else:
                    # 임계값 전략 미사용: 모든 스텝에서 예측 (기본 규칙)
                    prediction_result = predict_for_prefix(model, current_prefix, method)
                    # predict_for_prefix는 항상 예측값을 반환하거나 None을 반환
                    # None인 경우도 있으므로 is_forced는 False로 설정
                    if 'is_forced' not in prediction_result:
                        prediction_result['is_forced'] = False
            
            predicted_value = prediction_result.get('predicted')
            confidence = prediction_result.get('confidence', 0.0)
            is_forced = prediction_result.get('is_forced', False)
            
            # 반대 선택 전략: 강제 예측 시 반대 값 선택
            if is_forced and reverse_forced_prediction and predicted_value is not None:
                predicted_value = 'p' if predicted_value == 'b' else 'b'
            
            has_prediction = predicted_value is not None
            
            # 신뢰도 기반 스킵 규칙 체크
            should_skip = False
            # 기본 규칙: use_threshold=False일 때는 모든 예측값에 대해 검증 수행
            # 스킵 규칙은 use_threshold=True이고 강제 예측일 때만 적용
            if use_threshold and has_prediction and is_forced and confidence < confidence_skip_threshold:
                # 임계값 전략 사용 중이고, 강제 예측이고 신뢰도가 임계값 미만이면 스킵
                should_skip = True
                total_skipped_predictions += 1
            
            # 검증 수행 여부 결정 (기본 규칙: 예측값이 있으면 항상 검증)
            is_correct = None
            should_validate = False
            
            if has_prediction and not should_skip:
                # 기본 규칙: 예측값이 있고 스킵하지 않으면 항상 검증 수행
                should_validate = True
                is_correct = predicted_value == actual_value
                
                if not is_correct:
                    consecutive_failures += 1
                    consecutive_matches = 0
                    total_failures += 1
                    if consecutive_failures > max_consecutive_failures:
                        max_consecutive_failures = consecutive_failures
                else:
                    consecutive_failures = 0
                    consecutive_matches += 1
                    if consecutive_matches > max_consecutive_matches:
                        max_consecutive_matches = consecutive_matches
                    # 첫 번째 성공 스텝 기록
                    if first_success_step is None:
                        first_success_step = total_steps
                
                total_predictions += 1
                if is_forced:
                    total_forced_predictions += 1
                    if is_correct:
                        total_forced_successes += 1
                
                # 검증 후 간격 리셋
                current_interval = 0
                
                # 히스토리 기록
                history.append({
                    'step': total_steps,
                    'prefix': current_prefix,
                    'predicted': predicted_value,
                    'actual': actual_value,
                    'is_correct': is_correct,
                    'confidence': confidence,
                    'is_forced': is_forced,
                    'current_interval': current_interval,
                    'has_prediction': has_prediction,
                    'validated': True,
                    'skipped': False
                })
                
                # 다음 스텝으로 진행
                i += 1
                current_prefix = get_next_prefix(current_prefix, actual_value, window_size)
            elif has_prediction and should_skip:
                # 스킵: 다음 스텝으로 진행하되 간격은 증가하지 않음 (멈춤)
                # 히스토리 기록
                history.append({
                    'step': total_steps,
                    'prefix': current_prefix,
                    'predicted': predicted_value,
                    'actual': actual_value,
                    'is_correct': None,
                    'confidence': confidence,
                    'is_forced': is_forced,
                    'current_interval': current_interval,
                    'has_prediction': has_prediction,
                    'validated': False,
                    'skipped': True
                })
                
                # 다음 스텝으로 진행 (간격은 증가하지 않음 - 멈춤 상태)
                i += 1
                current_prefix = get_next_prefix(current_prefix, actual_value, window_size)
                # current_interval은 증가하지 않음 (멈춤)
            else:
                # 예측값이 없음: 간격 증가
                history.append({
                    'step': total_steps,
                    'prefix': current_prefix,
                    'predicted': None,
                    'actual': actual_value,
                    'is_correct': None,
                    'confidence': confidence,
                    'is_forced': False,
                    'current_interval': current_interval,
                    'has_prediction': False,
                    'validated': False,
                    'skipped': False
                })
                
                current_interval += 1
                # 다음 스텝으로 진행
                i += 1
                current_prefix = get_next_prefix(current_prefix, actual_value, window_size)
        
        # 정확도 계산
        accuracy = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        
        # 강제 예측 비율 계산
        forced_prediction_rate = (total_forced_predictions / total_predictions * 100) if total_predictions > 0 else 0.0
        
        # 강제 예측 성공 비율 계산
        forced_success_rate = (total_forced_successes / total_forced_predictions * 100) if total_forced_predictions > 0 else 0.0
        
        return {
            'grid_string_id': grid_string_id,
            'max_consecutive_failures': max_consecutive_failures,
            'max_consecutive_matches': max_consecutive_matches,
            'total_steps': total_steps,
            'total_failures': total_failures,
            'total_predictions': total_predictions,
            'total_forced_predictions': total_forced_predictions,
            'total_skipped_predictions': total_skipped_predictions,
            'forced_prediction_rate': forced_prediction_rate,
            'forced_success_rate': forced_success_rate,
            'accuracy': accuracy,
            'first_success_step': first_success_step,  # 첫 번째 성공 스텝
            'history': history
        }
        
    except Exception as e:
        st.error(f"검증 중 오류 발생: {str(e)}")
        return None
    finally:
        conn.close()

def validate_interactive_multi_step_scenario(
    grid_string_id,
    cutoff_grid_string_id,
    window_size=7,
    method="빈도 기반",
    use_threshold=True,
    threshold=60,
    max_interval=6,
    reverse_forced_prediction=False
):
    """
    인터랙티브 다단계 예측 시나리오 방식으로 단일 grid_string 검증
    
    Args:
        grid_string_id: 검증할 grid_string의 ID
        cutoff_grid_string_id: 학습 데이터 기준 ID (이 ID 이하를 학습 데이터로 사용)
        window_size: 윈도우 크기 (기본값: 7)
        method: 예측 방법 ("빈도 기반", "가중치 기반", "안전 우선", 기본값: "빈도 기반")
        use_threshold: 임계값 전략 사용 여부 (기본값: True)
        threshold: 임계값 (기본값: 60)
        max_interval: 최대 예측 없음 간격 (기본값: 6)
    
    Returns:
        dict: {
            'grid_string_id': int,
            'max_consecutive_failures': int,
            'total_steps': int,
            'total_failures': int,
            'total_predictions': int,
            'accuracy': float,
            'history': list
        }
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # grid_string 로드
        query = "SELECT grid_string FROM preprocessed_grid_strings WHERE id = ?"
        df = pd.read_sql_query(query, conn, params=[grid_string_id])
        
        if len(df) == 0:
            return None
        
        grid_string = df.iloc[0]['grid_string']
        
        if len(grid_string) < window_size:
            return {
                'grid_string_id': grid_string_id,
                'max_consecutive_failures': 0,
                'max_consecutive_matches': 0,
                'total_steps': 0,
                'total_failures': 0,
                'total_predictions': 0,
                'total_forced_predictions': 0,
                'forced_prediction_rate': 0.0,
                'accuracy': 0.0,
                'history': []
            }
        
        # 학습 데이터 구축 (cutoff_grid_string_id 이하, 검증 데이터 제외)
        # grid_string_id가 cutoff_grid_string_id 이하인 경우 학습 데이터에서 제외
        train_ids_query = "SELECT id FROM preprocessed_grid_strings WHERE id <= ? AND id < ? ORDER BY id"
        train_ids_df = pd.read_sql_query(train_ids_query, conn, params=[cutoff_grid_string_id, grid_string_id])
        train_ids = train_ids_df['id'].tolist() if len(train_ids_df) > 0 else []
        
        # N-gram 로드
        train_ngrams = load_ngram_chunks(window_size=window_size, grid_string_ids=train_ids)
        
        if len(train_ngrams) == 0:
            return {
                'grid_string_id': grid_string_id,
                'max_consecutive_failures': 0,
                'max_consecutive_matches': 0,
                'total_steps': 0,
                'total_failures': 0,
                'total_predictions': 0,
                'total_forced_predictions': 0,
                'forced_prediction_rate': 0.0,
                'accuracy': 0.0,
                'history': []
            }
        
        # 모델 구축
        if method == "빈도 기반":
            model = build_frequency_model(train_ngrams)
        elif method == "가중치 기반":
            model = build_weighted_model(train_ngrams)
        else:
            # 안전 우선은 별도 처리 필요 (일단 빈도 기반으로 대체)
            model = build_frequency_model(train_ngrams)
        
        # 시나리오 방식으로 테스트
        prefix_length = window_size - 1
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        consecutive_matches = 0
        max_consecutive_matches = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_forced_predictions = 0
        total_forced_successes = 0
        current_interval = 0
        
        # 초기 prefix 생성
        if len(grid_string) < prefix_length:
            return {
                'grid_string_id': grid_string_id,
                'max_consecutive_failures': 0,
                'max_consecutive_matches': 0,
                'total_steps': 0,
                'total_failures': 0,
                'total_predictions': 0,
                'total_forced_predictions': 0,
                'forced_prediction_rate': 0.0,
                'accuracy': 0.0,
                'history': []
            }
        
        current_prefix = grid_string[:prefix_length]
        
        # 예측값이 있는 모든 스텝에서 검증 수행
        # 간격 조건은 예측이 없는 스텝을 추적하는 용도로만 사용 (current_interval)
        
        # 각 스텝마다 예측 (모든 스텝에서 예측값 생성)
        for i in range(prefix_length, len(grid_string)):
            total_steps += 1
            actual_value = grid_string[i]
            
            # 예측 (모든 스텝에서 수행)
            if use_threshold:
                # 임계값 전략 사용
                prediction_result = predict_with_fallback_interval(
                    model,
                    current_prefix,
                    method=method,
                    threshold=threshold,
                    max_interval=max_interval,
                    current_interval=current_interval
                )
            else:
                # 임계값 전략 미사용: 모든 스텝에서 예측 (기본 규칙)
                prediction_result = predict_for_prefix(model, current_prefix, method)
                # predict_for_prefix는 항상 예측값을 반환하거나 None을 반환
                # None인 경우도 있으므로 is_forced는 False로 설정
                if 'is_forced' not in prediction_result:
                    prediction_result['is_forced'] = False
            
            predicted_value = prediction_result.get('predicted')
            confidence = prediction_result.get('confidence', 0.0)
            is_forced = prediction_result.get('is_forced', False)
            
            # 반대 선택 전략: 강제 예측 시 반대 값 선택
            if is_forced and reverse_forced_prediction and predicted_value is not None:
                predicted_value = 'p' if predicted_value == 'b' else 'b'
            
            has_prediction = predicted_value is not None
            
            # 실제값과 비교: 예측값이 있는 모든 스텝에서 검증 수행
            # 간격 조건은 예측이 없는 스텝을 카운트하는 용도로만 사용
            is_correct = None
            should_validate = False
            
            if has_prediction:
                # 예측값이 있으면 항상 검증 수행
                should_validate = True
                is_correct = predicted_value == actual_value
                
                if not is_correct:
                    consecutive_failures += 1
                    consecutive_matches = 0  # 불일치 시 연속 일치 리셋
                    total_failures += 1
                    if consecutive_failures > max_consecutive_failures:
                        max_consecutive_failures = consecutive_failures
                else:
                    consecutive_failures = 0  # 일치 시 연속 불일치 리셋
                    consecutive_matches += 1
                    if consecutive_matches > max_consecutive_matches:
                        max_consecutive_matches = consecutive_matches
                
                total_predictions += 1
                if is_forced:
                    total_forced_predictions += 1
                    if is_correct:
                        total_forced_successes += 1
            
            # 히스토리 기록 (모든 스텝 기록, 예측값이 있으면 항상 검증)
            history.append({
                'step': total_steps,
                'prefix': current_prefix,
                'predicted': predicted_value,
                'actual': actual_value,
                'is_correct': is_correct,
                'confidence': confidence,
                'is_forced': is_forced,
                'current_interval': current_interval,  # 예측 전 간격
                'has_prediction': has_prediction,
                'validated': should_validate  # 이 스텝에서 실제 비교가 수행되었는지
            })
            
            # 간격 업데이트 (다음 스텝으로 넘어가기 전에)
            if has_prediction:
                current_interval = 0  # 예측이 있었으면 간격 리셋
            else:
                current_interval += 1  # 예측이 없었으면 간격 증가
            
            # 다음 prefix 생성
            current_prefix = get_next_prefix(current_prefix, actual_value, window_size)
        
        # 정확도 계산 (예측이 있었던 스텝만 고려)
        accuracy = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        
        # 강제 예측 비율 계산
        forced_prediction_rate = (total_forced_predictions / total_predictions * 100) if total_predictions > 0 else 0.0
        
        # 강제 예측 성공 비율 계산
        forced_success_rate = (total_forced_successes / total_forced_predictions * 100) if total_forced_predictions > 0 else 0.0
        
        return {
            'grid_string_id': grid_string_id,
            'max_consecutive_failures': max_consecutive_failures,
            'max_consecutive_matches': max_consecutive_matches,
            'total_steps': total_steps,
            'total_failures': total_failures,
            'total_predictions': total_predictions,
            'total_forced_predictions': total_forced_predictions,
            'total_forced_successes': total_forced_successes,
            'forced_prediction_rate': forced_prediction_rate,
            'forced_success_rate': forced_success_rate,
            'accuracy': accuracy,
            'history': history
        }
        
    except Exception as e:
        st.error(f"검증 중 오류 발생: {str(e)}")
        return None
    finally:
        conn.close()

def batch_validate_interactive_multi_step_scenario(
    cutoff_grid_string_id,
    window_size=7,
    method="빈도 기반",
    use_threshold=True,
    threshold=60,
    max_interval=6,
    reverse_forced_prediction=False
):
    """
    cutoff_grid_string_id 이후의 모든 grid_string에 대해 배치 검증 실행
    
    Args:
        cutoff_grid_string_id: 기준 grid_string ID (이 ID 이후의 데이터 검증)
        window_size: 윈도우 크기 (기본값: 7)
        method: 예측 방법 (기본값: "빈도 기반")
        use_threshold: 임계값 전략 사용 여부 (기본값: True)
        threshold: 임계값 (기본값: 60)
        max_interval: 최대 예측 없음 간격 (기본값: 6)
    
    Returns:
        dict: {
            'results': list,
            'summary': dict
        }
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # cutoff_grid_string_id 이후의 모든 grid_string 로드
        query = "SELECT id FROM preprocessed_grid_strings WHERE id > ? ORDER BY id"
        df = pd.read_sql_query(query, conn, params=[cutoff_grid_string_id])
        
        if len(df) == 0:
            return {
                'results': [],
                'summary': {
                    'total_grid_strings': 0,
                    'avg_accuracy': 0.0,
                    'max_consecutive_failures': 0,
                    'avg_max_consecutive_failures': 0.0,
                    'total_steps': 0,
                    'total_failures': 0,
                    'total_predictions': 0,
                    'prediction_rate': 0.0
                },
                'grid_string_ids': []
            }
        
        grid_string_ids = df['id'].tolist()
        results = []
        all_history = []  # 신뢰도 통계 수집용
        
        # 각 grid_string에 대해 검증 실행
        for grid_string_id in grid_string_ids:
            result = validate_interactive_multi_step_scenario(
                grid_string_id,
                cutoff_grid_string_id,
                window_size=window_size,
                method=method,
                use_threshold=use_threshold,
                threshold=threshold,
                max_interval=max_interval,
                reverse_forced_prediction=reverse_forced_prediction
            )
            
            if result is not None:
                results.append(result)
                # 히스토리 수집 (신뢰도 통계용)
                all_history.extend(result.get('history', []))
        
        # 요약 통계 계산
        if len(results) > 0:
            total_grid_strings = len(results)
            avg_accuracy = sum(r['accuracy'] for r in results) / total_grid_strings
            max_consecutive_failures = max(r['max_consecutive_failures'] for r in results)
            avg_max_consecutive_failures = sum(r['max_consecutive_failures'] for r in results) / total_grid_strings
            total_steps = sum(r['total_steps'] for r in results)
            total_failures = sum(r['total_failures'] for r in results)
            total_predictions = sum(r['total_predictions'] for r in results)
            total_forced_predictions = sum(r.get('total_forced_predictions', 0) for r in results)
            total_forced_successes = sum(r.get('total_forced_successes', 0) for r in results)
            prediction_rate = (total_predictions / total_steps * 100) if total_steps > 0 else 0.0
            forced_prediction_rate = (total_forced_predictions / total_predictions * 100) if total_predictions > 0 else 0.0
            forced_success_rate = (total_forced_successes / total_forced_predictions * 100) if total_forced_predictions > 0 else 0.0
            
            summary = {
                'total_grid_strings': total_grid_strings,
                'avg_accuracy': avg_accuracy,
                'max_consecutive_failures': max_consecutive_failures,
                'avg_max_consecutive_failures': avg_max_consecutive_failures,
                'total_steps': total_steps,
                'total_failures': total_failures,
                'total_predictions': total_predictions,
                'total_forced_predictions': total_forced_predictions,
                'total_forced_successes': total_forced_successes,
                'prediction_rate': prediction_rate,
                'forced_prediction_rate': forced_prediction_rate,
                'forced_success_rate': forced_success_rate
            }
        else:
            summary = {
                'total_grid_strings': 0,
                'avg_accuracy': 0.0,
                'max_consecutive_failures': 0,
                'avg_max_consecutive_failures': 0.0,
                'total_steps': 0,
                'total_failures': 0,
                'total_predictions': 0,
                'total_forced_predictions': 0,
                'total_forced_successes': 0,
                'prediction_rate': 0.0,
                'forced_prediction_rate': 0.0,
                'forced_success_rate': 0.0
            }
        
        return {
            'results': results,
            'summary': summary,
            'all_history': all_history,  # 신뢰도 통계 수집용
            'grid_string_ids': grid_string_ids  # 검증한 grid_string_id 리스트
        }
        
    except Exception as e:
        st.error(f"배치 검증 중 오류 발생: {str(e)}")
        return None
    finally:
        conn.close()

def batch_validate_interactive_multi_step_scenario_with_confidence_skip(
    cutoff_grid_string_id,
    window_size=7,
    method="빈도 기반",
    use_threshold=True,
    threshold=60,
    max_interval=6,
    reverse_forced_prediction=False,
    confidence_skip_threshold=51,
    use_stored_predictions=False,
    stored_threshold=0
):
    """
    신뢰도 기반 스킵 규칙이 있는 배치 검증
    
    Args:
        cutoff_grid_string_id: 기준 grid_string ID
        window_size: 윈도우 크기
        method: 예측 방법
        use_threshold: 임계값 전략 사용 여부
        threshold: 임계값
        max_interval: 최대 예측 없음 간격
        reverse_forced_prediction: 반대 선택 전략 사용 여부
        confidence_skip_threshold: 스킵할 신뢰도 임계값
        use_stored_predictions: 예측값 테이블(stored_predictions) 사용 여부 (기본값: False)
        stored_threshold: stored_predictions 조회 시 사용할 임계값 (기본값: 0)
    
    Returns:
        dict: 배치 검증 결과
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # cutoff_grid_string_id 이후의 모든 grid_string 로드
        query = "SELECT id FROM preprocessed_grid_strings WHERE id > ? ORDER BY id"
        df = pd.read_sql_query(query, conn, params=[cutoff_grid_string_id])
        
        if len(df) == 0:
            return {
                'results': [],
                'summary': {
                    'total_grid_strings': 0,
                    'avg_accuracy': 0.0,
                    'max_consecutive_failures': 0,
                    'avg_max_consecutive_failures': 0.0,
                    'total_steps': 0,
                    'total_failures': 0,
                    'total_predictions': 0,
                    'total_skipped_predictions': 0,
                    'prediction_rate': 0.0
                },
                'grid_string_ids': []
            }
        
        grid_string_ids = df['id'].tolist()
        results = []
        all_history = []  # 신뢰도 통계 수집용
        
        # 각 grid_string에 대해 검증 실행
        for grid_string_id in grid_string_ids:
            result = validate_interactive_multi_step_scenario_with_confidence_skip(
                grid_string_id,
                cutoff_grid_string_id,
                window_size=window_size,
                method=method,
                use_threshold=use_threshold,
                threshold=threshold,
                max_interval=max_interval,
                reverse_forced_prediction=reverse_forced_prediction,
                confidence_skip_threshold=confidence_skip_threshold,
                use_stored_predictions=use_stored_predictions,
                stored_threshold=stored_threshold
            )
            
            if result is not None:
                results.append(result)
                # 히스토리 수집 (신뢰도 통계용)
                all_history.extend(result.get('history', []))
        
        # 요약 통계 계산
        if len(results) > 0:
            total_grid_strings = len(results)
            avg_accuracy = sum(r['accuracy'] for r in results) / total_grid_strings
            max_consecutive_failures = max(r['max_consecutive_failures'] for r in results)
            avg_max_consecutive_failures = sum(r['max_consecutive_failures'] for r in results) / total_grid_strings
            total_steps = sum(r['total_steps'] for r in results)
            total_failures = sum(r['total_failures'] for r in results)
            total_predictions = sum(r['total_predictions'] for r in results)
            total_skipped_predictions = sum(r.get('total_skipped_predictions', 0) for r in results)
            total_forced_predictions = sum(r.get('total_forced_predictions', 0) for r in results)
            total_forced_successes = sum(r.get('total_forced_successes', 0) for r in results)
            prediction_rate = (total_predictions / total_steps * 100) if total_steps > 0 else 0.0
            forced_prediction_rate = (total_forced_predictions / total_predictions * 100) if total_predictions > 0 else 0.0
            forced_success_rate = (total_forced_successes / total_forced_predictions * 100) if total_forced_predictions > 0 else 0.0
            
            # 첫 번째 성공 스텝 통계
            first_success_steps = [r.get('first_success_step') for r in results if r.get('first_success_step') is not None]
            avg_first_success_step = sum(first_success_steps) / len(first_success_steps) if len(first_success_steps) > 0 else None
            min_first_success_step = min(first_success_steps) if len(first_success_steps) > 0 else None
            max_first_success_step = max(first_success_steps) if len(first_success_steps) > 0 else None
            
            summary = {
                'total_grid_strings': total_grid_strings,
                'avg_accuracy': avg_accuracy,
                'max_consecutive_failures': max_consecutive_failures,
                'avg_max_consecutive_failures': avg_max_consecutive_failures,
                'total_steps': total_steps,
                'total_failures': total_failures,
                'total_predictions': total_predictions,
                'total_skipped_predictions': total_skipped_predictions,
                'total_forced_predictions': total_forced_predictions,
                'total_forced_successes': total_forced_successes,
                'prediction_rate': prediction_rate,
                'forced_prediction_rate': forced_prediction_rate,
                'forced_success_rate': forced_success_rate,
                'avg_first_success_step': avg_first_success_step,
                'min_first_success_step': min_first_success_step,
                'max_first_success_step': max_first_success_step,
                'total_with_success': len(first_success_steps)  # 성공이 있었던 grid_string 수
            }
        else:
            summary = {
                'total_grid_strings': 0,
                'avg_accuracy': 0.0,
                'max_consecutive_failures': 0,
                'avg_max_consecutive_failures': 0.0,
                'total_steps': 0,
                'total_failures': 0,
                'total_predictions': 0,
                'total_skipped_predictions': 0,
                'total_forced_predictions': 0,
                'total_forced_successes': 0,
                'prediction_rate': 0.0,
                'forced_prediction_rate': 0.0,
                'forced_success_rate': 0.0
            }
        
        return {
            'results': results,
            'summary': summary,
            'all_history': all_history,  # 신뢰도 통계 수집용
            'grid_string_ids': grid_string_ids  # 검증한 grid_string_id 리스트
        }
        
    except Exception as e:
        st.error(f"배치 검증 중 오류 발생: {str(e)}")
        return None
    finally:
        conn.close()

def get_failure_history_interactive(
    grid_string_id,
    cutoff_grid_string_id,
    window_size=7,
    method="빈도 기반",
    use_threshold=True,
    threshold=60,
    max_interval=6,
    reverse_forced_prediction=False
):
    """
    실패 Grid String의 상세 히스토리 조회 (인터랙티브 다단계 예측 시나리오용)
    
    Args:
        grid_string_id: 조회할 grid_string의 ID
        cutoff_grid_string_id: 학습 데이터 기준 ID
        window_size: 윈도우 크기
        method: 예측 방법
        use_threshold: 임계값 전략 사용 여부
        threshold: 임계값
        max_interval: 최대 예측 없음 간격
    
    Returns:
        dict: 히스토리 데이터
    """
    result = validate_interactive_multi_step_scenario(
        grid_string_id,
        cutoff_grid_string_id,
        window_size=window_size,
        method=method,
        use_threshold=use_threshold,
        threshold=threshold,
        max_interval=max_interval,
        reverse_forced_prediction=reverse_forced_prediction
    )
    
    if result is None:
        return None
    
    return result

def get_grid_strings_by_percentage_range(start_percentage, end_percentage):
    """
    비율 범위에 해당하는 grid_string들을 로드
    
    Args:
        start_percentage: 시작 비율 (0-100)
        end_percentage: 종료 비율 (0-100)
    
    Returns:
        DataFrame: 해당 비율 범위의 grid_string DataFrame (id 기준 정렬)
    """
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()
    
    try:
        # 전체 grid_string 개수 확인
        count_query = "SELECT COUNT(*) as count FROM preprocessed_grid_strings"
        count_df = pd.read_sql_query(count_query, conn)
        total_count = count_df.iloc[0]['count'] if len(count_df) > 0 else 0
        
        if total_count == 0:
            return pd.DataFrame()
        
        # 비율에 해당하는 인덱스 계산
        start_index = int(total_count * start_percentage / 100)
        end_index = int(total_count * end_percentage / 100)
        
        # 모든 grid_string을 id 기준으로 정렬하여 로드
        query = "SELECT id, grid_string, created_at FROM preprocessed_grid_strings ORDER BY id"
        df_all = pd.read_sql_query(query, conn)
        
        if len(df_all) == 0:
            return pd.DataFrame()
        
        # 해당 범위의 데이터 추출
        if start_index < len(df_all) and end_index <= len(df_all):
            df_range = df_all.iloc[start_index:end_index].copy()
        else:
            return pd.DataFrame()
        
        return df_range
        
    except Exception as e:
        st.error(f"데이터 로딩 중 오류 발생: {str(e)}")
        return pd.DataFrame()
    finally:
        conn.close()

def validate_forced_prediction_hypothesis(
    train_cutoff_id,
    validation_start_id,
    validation_end_id,
    window_size=7,
    method="빈도 기반"
):
    """
    강제 예측 가설 검증 (단일 단계)
    
    모든 스텝에서 강제 예측을 수행하고, 간격을 윈도우 크기 -1로 설정
    
    Args:
        train_cutoff_id: 학습 데이터 기준 ID (이 ID 이하를 학습 데이터로 사용)
        validation_start_id: 검증 시작 ID
        validation_end_id: 검증 종료 ID (이 ID 이하까지 검증)
        window_size: 윈도우 크기
        method: 예측 방법
    
    Returns:
        dict: 검증 결과
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # 학습 데이터 구축
        train_ids_query = "SELECT id FROM preprocessed_grid_strings WHERE id <= ? ORDER BY id"
        train_ids_df = pd.read_sql_query(train_ids_query, conn, params=[train_cutoff_id])
        train_ids = train_ids_df['id'].tolist() if len(train_ids_df) > 0 else []
        
        # N-gram 로드
        train_ngrams = load_ngram_chunks(window_size=window_size, grid_string_ids=train_ids)
        
        if len(train_ngrams) == 0:
            return {
                'train_cutoff_id': train_cutoff_id,
                'validation_start_id': validation_start_id,
                'validation_end_id': validation_end_id,
                'total_grid_strings': 0,
                'tested_grid_strings': 0,
                'max_consecutive_failures': 0,
                'max_consecutive_matches': 0,
                'total_steps': 0,
                'total_failures': 0,
                'total_predictions': 0,
                'total_forced_predictions': 0,
                'accuracy': 0.0,
                'forced_success_rate': 0.0,
                'results': []
            }
        
        # 모델 구축
        if method == "빈도 기반":
            model = build_frequency_model(train_ngrams)
        elif method == "가중치 기반":
            model = build_weighted_model(train_ngrams)
        else:
            model = build_frequency_model(train_ngrams)
        
        # 검증 데이터 로드
        validation_query = """
            SELECT id, grid_string 
            FROM preprocessed_grid_strings 
            WHERE id > ? AND id <= ? 
            ORDER BY id
        """
        validation_df = pd.read_sql_query(
            validation_query, 
            conn, 
            params=[validation_start_id, validation_end_id]
        )
        
        if len(validation_df) == 0:
            return {
                'train_cutoff_id': train_cutoff_id,
                'validation_start_id': validation_start_id,
                'validation_end_id': validation_end_id,
                'total_grid_strings': 0,
                'tested_grid_strings': 0,
                'max_consecutive_failures': 0,
                'max_consecutive_matches': 0,
                'total_steps': 0,
                'total_failures': 0,
                'total_predictions': 0,
                'total_forced_predictions': 0,
                'accuracy': 0.0,
                'forced_success_rate': 0.0,
                'results': []
            }
        
        # 검증 실행
        results = []
        max_consecutive_failures_all = 0
        max_consecutive_matches_all = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_forced_predictions = 0
        total_forced_successes = 0
        
        # max_interval = window_size - 1
        max_interval = window_size - 1
        
        for _, row in validation_df.iterrows():
            grid_string_id = row['id']
            grid_string = row['grid_string']
            
            if len(grid_string) < window_size:
                continue
            
            # 검증 실행 (use_threshold=False, 항상 예측하되 max_interval=window_size-1로 설정)
            # 가설: 모든 스텝은 강제 예측, 간격 = 윈도우 크기 - 1
            # use_threshold=False로 설정하면 모든 스텝에서 예측이 발생 (강제 예측 개념)
            result = validate_interactive_multi_step_scenario(
                grid_string_id,
                train_cutoff_id,
                window_size=window_size,
                method=method,
                use_threshold=False,  # 임계값 전략 사용 안 함 (모든 스텝에서 예측)
                threshold=60,
                max_interval=max_interval,  # 윈도우 크기 - 1 (가설 요구사항)
                reverse_forced_prediction=False
            )
            
            if result is not None:
                results.append(result)
                max_consecutive_failures_all = max(max_consecutive_failures_all, result['max_consecutive_failures'])
                max_consecutive_matches_all = max(max_consecutive_matches_all, result.get('max_consecutive_matches', 0))
                total_steps += result['total_steps']
                total_failures += result['total_failures']
                total_predictions += result['total_predictions']
                total_forced_predictions += result['total_forced_predictions']
                total_forced_successes += result.get('total_forced_successes', 0)
        
        # 정확도 계산
        accuracy = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        
        # 강제 예측 성공률 계산
        forced_success_rate = (total_forced_successes / total_forced_predictions * 100) if total_forced_predictions > 0 else 0.0
        
        return {
            'train_cutoff_id': train_cutoff_id,
            'validation_start_id': validation_start_id,
            'validation_end_id': validation_end_id,
            'total_grid_strings': len(validation_df),
            'tested_grid_strings': len(results),
            'max_consecutive_failures': max_consecutive_failures_all,
            'max_consecutive_matches': max_consecutive_matches_all,
            'total_steps': total_steps,
            'total_failures': total_failures,
            'total_predictions': total_predictions,
            'total_forced_predictions': total_forced_predictions,
            'accuracy': accuracy,
            'forced_success_rate': forced_success_rate,
            'results': results
        }
        
    except Exception as e:
        st.error(f"검증 중 오류 발생: {str(e)}")
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
        return None
    finally:
        conn.close()

def render_live_game_play(game_state):
    """
    라이브 게임 진행 UI 렌더링
    게임 상태가 있을 때만 호출되어야 함
    """
    # 자동 실행 완료 메시지 제거 (성능 개선)
    
    # 현재 스텝 정보
    st.markdown("---")
    st.markdown("### 📍 현재 스텝")
    
    # 예측 수행
    current_prefix = game_state['current_prefix']
    current_interval = game_state['current_interval']
    model = game_state['model']
    
    if game_state['use_threshold']:
        prediction_result = predict_with_fallback_interval(
            model,
            current_prefix,
            method=game_state['method'],
            threshold=game_state['threshold'],
            max_interval=game_state['max_interval'],
            current_interval=current_interval
        )
    else:
        prediction_result = predict_for_prefix(model, current_prefix, game_state['method'])
        if 'is_forced' not in prediction_result:
            prediction_result['is_forced'] = False
    
    predicted_value = prediction_result.get('predicted')
    confidence = prediction_result.get('confidence', 0.0)
    is_forced = prediction_result.get('is_forced', False)
    has_prediction = predicted_value is not None
    
    # 스킵 규칙 체크
    should_skip = False
    if game_state['use_threshold'] and has_prediction and is_forced and confidence < game_state['confidence_skip_threshold']:
        should_skip = True
    
    # 현재 스텝 정보 표시 (컴팩트하게)
    col_info1, col_info2, col_info3, col_info4 = st.columns(4)
    with col_info1:
        st.caption("Prefix")
        st.markdown(f"<div style='font-size: 24px; font-weight: bold;'>{current_prefix}</div>", unsafe_allow_html=True)
    with col_info2:
        if has_prediction:
            forced_mark = "⚡" if is_forced else ""
            skip_mark = "⏭️" if should_skip else ""
            st.caption("예측값")
            st.text(f"{predicted_value}{forced_mark}{skip_mark}")
        else:
            st.caption("예측값")
            st.text("없음")
    with col_info3:
        if has_prediction:
            st.caption("신뢰도")
            st.text(f"{confidence:.1f}%")
        else:
            st.caption("신뢰도")
            st.text("-")
    with col_info4:
        st.caption("간격")
        st.text(f"{current_interval}/{game_state['max_interval']}")
    
    # 다음 스텝 경로 미리보기
    st.markdown("---")
    st.markdown('<p style="font-size: 1em; color: #666; margin-top: -10px;"><strong>다음 스텝 경로 미리보기:</strong></p>', unsafe_allow_html=True)
    
    # 다음 prefix 생성 (b와 p 두 경우 모두)
    next_prefix_b = get_next_prefix(current_prefix, 'b', game_state['window_size'])
    next_prefix_p = get_next_prefix(current_prefix, 'p', game_state['window_size'])
    
    # 다음 prefix에 대한 예측 (모델이 있는 경우)
    if model is not None:
        next_pred_b = None
        next_pred_p = None
        next_conf_b = 0.0
        next_conf_p = 0.0
        next_forced_b = False
        next_forced_p = False
        
        try:
            if game_state['use_threshold']:
                # 다음 스텝 예측용 간격 계산
                # 현재 스텝에서 예측이 있었으면, 다음 스텝으로 넘어가면 간격이 0으로 리셋
                # 현재 스텝에서 예측이 없었으면, 다음 스텝으로 넘어가면 간격이 1 증가
                if has_prediction:
                    # 현재 스텝에서 예측이 있었으면, 다음 스텝으로 넘어가면 간격이 0으로 리셋
                    next_interval = 0
                else:
                    # 현재 스텝에서 예측이 없었으면, 다음 스텝으로 넘어가면 간격이 1 증가
                    next_interval = current_interval + 1
                
                # 간격을 고려하여 예측
                next_result_b = predict_with_fallback_interval(
                    model,
                    next_prefix_b,
                    game_state['method'],
                    threshold=game_state['threshold'],
                    max_interval=game_state['max_interval'],
                    current_interval=next_interval
                )
                next_result_p = predict_with_fallback_interval(
                    model,
                    next_prefix_p,
                    game_state['method'],
                    threshold=game_state['threshold'],
                    max_interval=game_state['max_interval'],
                    current_interval=next_interval
                )
                
                next_forced_b = next_result_b.get('is_forced', False)
                next_forced_p = next_result_p.get('is_forced', False)
            else:
                next_result_b = predict_for_prefix(model, next_prefix_b, game_state['method'])
                next_result_p = predict_for_prefix(model, next_prefix_p, game_state['method'])
                next_forced_b = False
                next_forced_p = False
            
            next_pred_b = next_result_b.get('predicted')
            next_pred_p = next_result_p.get('predicted')
            next_conf_b = next_result_b.get('confidence', 0.0)
            next_conf_p = next_result_p.get('confidence', 0.0)
        except Exception as e:
            pass
        
        # 경로 표시
        col_path1, col_path2 = st.columns(2)
        with col_path1:
            if next_pred_b is not None and str(next_pred_b).strip() != '':
                forced_marker = " ⚡" if next_forced_b else ""
                st.markdown(f'<p style="font-size: 0.95em; color: #333;">실제값 <strong>b</strong> → 다음 prefix: <code>{next_prefix_b}</code> → 예측: <code>{next_pred_b}{forced_marker}</code> ({next_conf_b:.1f}%)</p>', unsafe_allow_html=True)
            else:
                st.markdown(f'<p style="font-size: 0.95em; color: #666;">실제값 <strong>b</strong> → 다음 prefix: <code>{next_prefix_b}</code> → 예측: <code>-</code></p>', unsafe_allow_html=True)
        
        with col_path2:
            if next_pred_p is not None and str(next_pred_p).strip() != '':
                forced_marker = " ⚡" if next_forced_p else ""
                st.markdown(f'<p style="font-size: 0.95em; color: #333;">실제값 <strong>p</strong> → 다음 prefix: <code>{next_prefix_p}</code> → 예측: <code>{next_pred_p}{forced_marker}</code> ({next_conf_p:.1f}%)</p>', unsafe_allow_html=True)
            else:
                st.markdown(f'<p style="font-size: 0.95em; color: #666;">실제값 <strong>p</strong> → 다음 prefix: <code>{next_prefix_p}</code> → 예측: <code>-</code></p>', unsafe_allow_html=True)
    else:
        # 모델이 없는 경우 prefix만 표시
        col_path1, col_path2 = st.columns(2)
        with col_path1:
            st.markdown(f'<p style="font-size: 0.95em; color: #666;">실제값 <strong>b</strong> → 다음 prefix: <code>{next_prefix_b}</code></p>', unsafe_allow_html=True)
        with col_path2:
            st.markdown(f'<p style="font-size: 0.95em; color: #666;">실제값 <strong>p</strong> → 다음 prefix: <code>{next_prefix_p}</code></p>', unsafe_allow_html=True)
    
    # 실제값 입력 (버튼식)
    if has_prediction and not should_skip:
        st.markdown("---")
        st.markdown("#### 실제값 선택")
        
        # 이전 상태 저장 (취소 기능용)
        if 'previous_game_state' not in st.session_state or st.session_state.get('previous_game_state_step', -1) != game_state['current_step']:
            st.session_state.previous_game_state = {
                'current_step': game_state['current_step'],
                'current_index': game_state['current_index'],
                'current_prefix': game_state['current_prefix'],
                'current_interval': game_state['current_interval'],
                'consecutive_failures': game_state['consecutive_failures'],
                'max_consecutive_failures': game_state['max_consecutive_failures'],
                'total_predictions': game_state['total_predictions'],
                'total_failures': game_state['total_failures'],
                'total_forced_predictions': game_state['total_forced_predictions'],
                'history': game_state['history'].copy()
            }
            st.session_state.previous_game_state_step = game_state['current_step']
        
        col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
        with col_btn1:
            if st.button("🔴 B", use_container_width=True, key=f"live_game_btn_b_{game_state['current_step']}"):
                actual_value = 'b'
                
                # 검증 수행
                is_correct = predicted_value == actual_value
                
                if not is_correct:
                    game_state['consecutive_failures'] += 1
                    game_state['total_failures'] += 1
                    if game_state['consecutive_failures'] > game_state['max_consecutive_failures']:
                        game_state['max_consecutive_failures'] = game_state['consecutive_failures']
                else:
                    game_state['consecutive_failures'] = 0
                
                game_state['total_predictions'] += 1
                if is_forced:
                    game_state['total_forced_predictions'] += 1
                
                # 간격 리셋
                game_state['current_interval'] = 0
                
                # 히스토리 기록
                game_state['history'].append({
                    'step': game_state['current_step'] + 1,
                    'prefix': current_prefix,
                    'predicted': predicted_value,
                    'actual': actual_value,
                    'is_correct': is_correct,
                    'confidence': confidence,
                    'is_forced': is_forced,
                    'current_interval': 0,
                    'has_prediction': True,
                    'validated': True,
                    'skipped': False
                })
                
                # 다음 스텝으로 진행
                game_state['current_step'] += 1
                game_state['current_index'] += 1
                # prefix 업데이트 (인터랙티브 모드에서는 항상 업데이트)
                game_state['current_prefix'] = get_next_prefix(
                    current_prefix,
                    actual_value,
                    game_state['window_size']
                )
                
                st.rerun()
        
        with col_btn2:
            if st.button("🔵 P", use_container_width=True, key=f"live_game_btn_p_{game_state['current_step']}"):
                actual_value = 'p'
                
                # 검증 수행
                is_correct = predicted_value == actual_value
                
                if not is_correct:
                    game_state['consecutive_failures'] += 1
                    game_state['total_failures'] += 1
                    if game_state['consecutive_failures'] > game_state['max_consecutive_failures']:
                        game_state['max_consecutive_failures'] = game_state['consecutive_failures']
                else:
                    game_state['consecutive_failures'] = 0
                
                game_state['total_predictions'] += 1
                if is_forced:
                    game_state['total_forced_predictions'] += 1
                
                # 간격 리셋
                game_state['current_interval'] = 0
                
                # 히스토리 기록
                game_state['history'].append({
                    'step': game_state['current_step'] + 1,
                    'prefix': current_prefix,
                    'predicted': predicted_value,
                    'actual': actual_value,
                    'is_correct': is_correct,
                    'confidence': confidence,
                    'is_forced': is_forced,
                    'current_interval': 0,
                    'has_prediction': True,
                    'validated': True,
                    'skipped': False
                })
                
                # 다음 스텝으로 진행
                game_state['current_step'] += 1
                game_state['current_index'] += 1
                # prefix 업데이트 (인터랙티브 모드에서는 항상 업데이트)
                game_state['current_prefix'] = get_next_prefix(
                    current_prefix,
                    actual_value,
                    game_state['window_size']
                )
                
                st.rerun()
        
        with col_btn3:
            if st.button("↩️ 취소", use_container_width=True, key=f"live_game_btn_cancel_{game_state['current_step']}"):
                if 'previous_game_state' in st.session_state:
                    prev_state = st.session_state.previous_game_state
                    # 이전 상태로 복원
                    game_state['current_step'] = prev_state['current_step']
                    game_state['current_index'] = prev_state['current_index']
                    game_state['current_prefix'] = prev_state['current_prefix']
                    game_state['current_interval'] = prev_state['current_interval']
                    game_state['consecutive_failures'] = prev_state['consecutive_failures']
                    game_state['max_consecutive_failures'] = prev_state['max_consecutive_failures']
                    game_state['total_predictions'] = prev_state['total_predictions']
                    game_state['total_failures'] = prev_state['total_failures']
                    game_state['total_forced_predictions'] = prev_state['total_forced_predictions']
                    game_state['history'] = prev_state['history'].copy()
                    st.rerun()
    elif has_prediction and should_skip:
        # 스킵 상태
        st.markdown("---")
        st.markdown("#### 실제값 선택 (스킵 모드)")
        
        # 이전 상태 저장 (취소 기능용)
        if 'previous_game_state' not in st.session_state or st.session_state.get('previous_game_state_step', -1) != game_state['current_step']:
            st.session_state.previous_game_state = {
                'current_step': game_state['current_step'],
                'current_index': game_state['current_index'],
                'current_prefix': game_state['current_prefix'],
                'current_interval': game_state['current_interval'],
                'consecutive_failures': game_state['consecutive_failures'],
                'max_consecutive_failures': game_state['max_consecutive_failures'],
                'total_predictions': game_state['total_predictions'],
                'total_failures': game_state['total_failures'],
                'total_forced_predictions': game_state['total_forced_predictions'],
                'total_skipped_predictions': game_state['total_skipped_predictions'],
                'history': game_state['history'].copy()
            }
            st.session_state.previous_game_state_step = game_state['current_step']
        
        col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
        with col_btn1:
            if st.button("🔴 B", use_container_width=True, key=f"live_game_btn_skip_b_{game_state['current_step']}"):
                actual_value = 'b'
                
                # 히스토리 기록 (스킵)
                game_state['history'].append({
                    'step': game_state['current_step'] + 1,
                    'prefix': current_prefix,
                    'predicted': predicted_value,
                    'actual': actual_value,
                    'is_correct': None,
                    'confidence': confidence,
                    'is_forced': is_forced,
                    'current_interval': current_interval,
                    'has_prediction': True,
                    'validated': False,
                    'skipped': True
                })
                
                game_state['total_skipped_predictions'] += 1
                
                # 다음 스텝으로 진행 (간격은 증가하지 않음)
                game_state['current_step'] += 1
                game_state['current_index'] += 1
                game_state['current_prefix'] = get_next_prefix(
                    current_prefix,
                    actual_value,
                    game_state['window_size']
                )
                
                st.rerun()
        
        with col_btn2:
            if st.button("🔵 P", use_container_width=True, key=f"live_game_btn_skip_p_{game_state['current_step']}"):
                actual_value = 'p'
                
                # 히스토리 기록 (스킵)
                game_state['history'].append({
                    'step': game_state['current_step'] + 1,
                    'prefix': current_prefix,
                    'predicted': predicted_value,
                    'actual': actual_value,
                    'is_correct': None,
                    'confidence': confidence,
                    'is_forced': is_forced,
                    'current_interval': current_interval,
                    'has_prediction': True,
                    'validated': False,
                    'skipped': True
                })
                
                game_state['total_skipped_predictions'] += 1
                
                # 다음 스텝으로 진행 (간격은 증가하지 않음)
                game_state['current_step'] += 1
                game_state['current_index'] += 1
                game_state['current_prefix'] = get_next_prefix(
                    current_prefix,
                    actual_value,
                    game_state['window_size']
                )
                
                st.rerun()
        
        with col_btn3:
            if st.button("↩️ 취소", use_container_width=True, key=f"live_game_btn_skip_cancel_{game_state['current_step']}"):
                if 'previous_game_state' in st.session_state:
                    prev_state = st.session_state.previous_game_state
                    # 이전 상태로 복원
                    game_state['current_step'] = prev_state['current_step']
                    game_state['current_index'] = prev_state['current_index']
                    game_state['current_prefix'] = prev_state['current_prefix']
                    game_state['current_interval'] = prev_state['current_interval']
                    game_state['consecutive_failures'] = prev_state['consecutive_failures']
                    game_state['max_consecutive_failures'] = prev_state['max_consecutive_failures']
                    game_state['total_predictions'] = prev_state['total_predictions']
                    game_state['total_failures'] = prev_state['total_failures']
                    game_state['total_forced_predictions'] = prev_state['total_forced_predictions']
                    game_state['total_skipped_predictions'] = prev_state.get('total_skipped_predictions', game_state['total_skipped_predictions'])
                    game_state['history'] = prev_state['history'].copy()
                    st.rerun()
    else:
        # 예측값이 없음
        st.markdown("---")
        st.markdown("#### 실제값 선택 (예측값 없음)")
        
        # 이전 상태 저장 (취소 기능용)
        if 'previous_game_state' not in st.session_state or st.session_state.get('previous_game_state_step', -1) != game_state['current_step']:
            st.session_state.previous_game_state = {
                'current_step': game_state['current_step'],
                'current_index': game_state['current_index'],
                'current_prefix': game_state['current_prefix'],
                'current_interval': game_state['current_interval'],
                'consecutive_failures': game_state['consecutive_failures'],
                'max_consecutive_failures': game_state['max_consecutive_failures'],
                'total_predictions': game_state['total_predictions'],
                'total_failures': game_state['total_failures'],
                'total_forced_predictions': game_state['total_forced_predictions'],
                'total_skipped_predictions': game_state['total_skipped_predictions'],
                'history': game_state['history'].copy()
            }
            st.session_state.previous_game_state_step = game_state['current_step']
        
        col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
        with col_btn1:
            if st.button("🔴 B", use_container_width=True, key=f"live_game_btn_no_pred_b_{game_state['current_step']}"):
                actual_value = 'b'
                
                # 히스토리 기록
                game_state['history'].append({
                    'step': game_state['current_step'] + 1,
                    'prefix': current_prefix,
                    'predicted': None,
                    'actual': actual_value,
                    'is_correct': None,
                    'confidence': confidence,
                    'is_forced': False,
                    'current_interval': current_interval,
                    'has_prediction': False,
                    'validated': False,
                    'skipped': False
                })
                
                # 간격 증가
                game_state['current_interval'] += 1
                
                # 다음 스텝으로 진행
                game_state['current_step'] += 1
                game_state['current_index'] += 1
                game_state['current_prefix'] = get_next_prefix(
                    current_prefix,
                    actual_value,
                    game_state['window_size']
                )
                
                st.rerun()
        
        with col_btn2:
            if st.button("🔵 P", use_container_width=True, key=f"live_game_btn_no_pred_p_{game_state['current_step']}"):
                actual_value = 'p'
                
                # 히스토리 기록
                game_state['history'].append({
                    'step': game_state['current_step'] + 1,
                    'prefix': current_prefix,
                    'predicted': None,
                    'actual': actual_value,
                    'is_correct': None,
                    'confidence': confidence,
                    'is_forced': False,
                    'current_interval': current_interval,
                    'has_prediction': False,
                    'validated': False,
                    'skipped': False
                })
                
                # 간격 증가
                game_state['current_interval'] += 1
                
                # 다음 스텝으로 진행
                game_state['current_step'] += 1
                game_state['current_index'] += 1
                game_state['current_prefix'] = get_next_prefix(
                    current_prefix,
                    actual_value,
                    game_state['window_size']
                )
                
                st.rerun()
        
        with col_btn3:
            if st.button("↩️ 취소", use_container_width=True, key=f"live_game_btn_no_pred_cancel_{game_state['current_step']}"):
                if 'previous_game_state' in st.session_state:
                    prev_state = st.session_state.previous_game_state
                    # 이전 상태로 복원
                    game_state['current_step'] = prev_state['current_step']
                    game_state['current_index'] = prev_state['current_index']
                    game_state['current_prefix'] = prev_state['current_prefix']
                    game_state['current_interval'] = prev_state['current_interval']
                    game_state['consecutive_failures'] = prev_state['consecutive_failures']
                    game_state['max_consecutive_failures'] = prev_state['max_consecutive_failures']
                    game_state['total_predictions'] = prev_state['total_predictions']
                    game_state['total_failures'] = prev_state['total_failures']
                    game_state['total_forced_predictions'] = prev_state['total_forced_predictions']
                    game_state['total_skipped_predictions'] = prev_state.get('total_skipped_predictions', game_state['total_skipped_predictions'])
                    game_state['history'] = prev_state['history'].copy()
                    st.rerun()
    
    # 상세 히스토리 표시
    if len(game_state['history']) > 0:
        st.markdown("---")
        with st.expander("📊 상세 히스토리", expanded=True):
            history_data = []
            history_sorted = sorted(game_state['history'], key=lambda x: x.get('step', 0), reverse=True)
            
            for entry in history_sorted[:50]:  # 최신 50개만 표시
                is_correct = entry.get('is_correct')
                match_status = '✅' if is_correct else ('❌' if is_correct is False else '-')
                has_prediction = entry.get('has_prediction', False)
                is_forced = entry.get('is_forced', False)
                validated = entry.get('validated', False)
                skipped = entry.get('skipped', False)
                
                forced_mark = '⚡' if is_forced else ''
                skipped_mark = '⏭️' if skipped else ''
                validated_mark = '✓' if validated else ''
                
                history_data.append({
                    'Step': entry.get('step', 0),
                    'Prefix': entry.get('prefix', ''),
                    '예측': f"{entry.get('predicted', '-')}{forced_mark}{skipped_mark}",
                    '실제값': entry.get('actual', '-'),
                    '일치': match_status,
                    '검증': validated_mark,
                    '신뢰도': f"{entry.get('confidence', 0):.1f}%" if has_prediction else '-',
                    '간격': entry.get('current_interval', 0) if not has_prediction else 0
                })
            
            if len(history_data) > 0:
                history_df = pd.DataFrame(history_data)
                st.dataframe(history_df, use_container_width=True, hide_index=True)
                
                if len(game_state['history']) > 50:
                    st.caption(f"💡 전체 {len(game_state['history'])}개 중 최신 50개만 표시됩니다.")
    
    # 게임 완료 체크 (메시지 제거 - 성능 개선)
    if game_state['current_index'] >= len(game_state['grid_string']):
        st.markdown("---")
        
        accuracy = ((game_state['total_predictions'] - game_state['total_failures']) / game_state['total_predictions'] * 100) if game_state['total_predictions'] > 0 else 0.0
        
        col_final1, col_final2, col_final3, col_final4 = st.columns(4)
        with col_final1:
            st.metric("총 스텝", game_state['current_step'])
        with col_final2:
            st.metric("총 예측", game_state['total_predictions'])
        with col_final3:
            st.metric("최대 연속 실패", game_state['max_consecutive_failures'])
        with col_final4:
            st.metric("정확도", f"{accuracy:.2f}%")

def progressive_validate_forced_prediction_hypothesis(
    window_size=7,
    method="빈도 기반",
    start_ratio=70,
    step_ratio=5,
    max_ratio=100
):
    """
    강제 예측 가설 점진적 검증
    
    Args:
        window_size: 윈도우 크기
        method: 예측 방법
        start_ratio: 시작 비율 (기본값: 70)
        step_ratio: 단계별 증가 비율 (기본값: 5)
        max_ratio: 최대 비율 (기본값: 100)
    
    Returns:
        dict: 점진적 검증 결과
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # 전체 grid_string 개수 확인
        count_query = "SELECT COUNT(*) as count FROM preprocessed_grid_strings"
        count_df = pd.read_sql_query(count_query, conn)
        total_count = count_df.iloc[0]['count'] if len(count_df) > 0 else 0
        
        if total_count == 0:
            return None
        
        # 모든 grid_string을 id 기준으로 정렬하여 로드
        query = "SELECT id FROM preprocessed_grid_strings ORDER BY id"
        df_all_ids = pd.read_sql_query(query, conn)
        all_ids = df_all_ids['id'].tolist()
        
        if len(all_ids) == 0:
            return None
        
        steps_results = []
        max_consecutive_failures_all_steps = 0
        max_consecutive_matches_all_steps = 0
        total_tested_grid_strings = 0
        total_steps_all = 0
        total_failures_all = 0
        total_accuracy_sum = 0.0
        step_count = 0
        
        # 점진적 검증 실행
        current_ratio = start_ratio
        while current_ratio < max_ratio:
            # 학습 데이터 범위 계산
            train_index = int(total_count * current_ratio / 100)
            if train_index >= len(all_ids):
                break
            
            train_cutoff_id = all_ids[train_index - 1] if train_index > 0 else all_ids[0]
            
            # 검증 데이터 범위 계산
            validation_end_ratio = min(current_ratio + step_ratio, max_ratio)
            validation_end_index = int(total_count * validation_end_ratio / 100)
            
            if validation_end_index >= len(all_ids):
                validation_end_index = len(all_ids)
            
            if validation_end_index <= train_index:
                break
            
            # 검증 시작 ID와 종료 ID
            # 검증 시작 ID는 학습 데이터 범위 바로 다음 ID
            if train_index < len(all_ids):
                validation_start_id = all_ids[train_index] if train_index < len(all_ids) else all_ids[-1]
            else:
                validation_start_id = all_ids[-1]
            
            validation_end_id = all_ids[validation_end_index - 1] if validation_end_index > 0 and validation_end_index <= len(all_ids) else all_ids[-1]
            
            # 단일 단계 검증 실행
            validation_results = validate_forced_prediction_hypothesis(
                train_cutoff_id,
                validation_start_id,
                validation_end_id,
                window_size=window_size,
                method=method
            )
            
            if validation_results is not None:
                steps_results.append({
                    'train_ratio': current_ratio,
                    'validation_start_ratio': current_ratio,
                    'validation_end_ratio': validation_end_ratio,
                    'validation_results': validation_results
                })
                
                # 통계 집계
                max_consecutive_failures_all_steps = max(
                    max_consecutive_failures_all_steps,
                    validation_results['max_consecutive_failures']
                )
                max_consecutive_matches_all_steps = max(
                    max_consecutive_matches_all_steps,
                    validation_results.get('max_consecutive_matches', 0)
                )
                total_tested_grid_strings += validation_results['tested_grid_strings']
                total_steps_all += validation_results['total_steps']
                total_failures_all += validation_results['total_failures']
                total_accuracy_sum += validation_results['accuracy']
                step_count += 1
            
            # 다음 단계로 이동
            current_ratio += step_ratio
        
        # 요약 통계 계산
        avg_max_consecutive_failures = (
            sum(s['validation_results']['max_consecutive_failures'] for s in steps_results) / len(steps_results)
            if len(steps_results) > 0 else 0.0
        )
        avg_accuracy = total_accuracy_sum / step_count if step_count > 0 else 0.0
        
        # 평균 최대 연속 일치 수 계산
        avg_max_consecutive_matches = (
            sum(s['validation_results'].get('max_consecutive_matches', 0) for s in steps_results) / len(steps_results)
            if len(steps_results) > 0 else 0.0
        )
        
        return {
            'window_size': window_size,
            'method': method,
            'steps': steps_results,
            'summary': {
                'max_consecutive_failures_all_steps': max_consecutive_failures_all_steps,
                'max_consecutive_matches_all_steps': max_consecutive_matches_all_steps,
                'avg_max_consecutive_failures': avg_max_consecutive_failures,
                'avg_max_consecutive_matches': avg_max_consecutive_matches,
                'total_tested_grid_strings': total_tested_grid_strings,
                'total_steps': total_steps_all,
                'total_failures': total_failures_all,
                'avg_accuracy': avg_accuracy,
                'step_count': step_count
            }
        }
        
    except Exception as e:
        st.error(f"점진적 검증 중 오류 발생: {str(e)}")
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
        return None
    finally:
        conn.close()

def main():
    # 테이블 생성 (앱 시작 시)
    if 'validation_tables_created' not in st.session_state:
        if create_validation_tables():
            st.session_state.validation_tables_created = True
        else:
            st.error("테이블 생성 실패")
            return
    st.title("🌳 인터랙티브 다단계 예측 시나리오 검증")
    st.markdown("인터랙티브 다단계 예측 시나리오를 자동으로 검증하여 최대 연속 실패 횟수를 분석합니다.")
    
    # 설정 섹션
    with st.form("validation_interactive_settings_form", clear_on_submit=False):
        st.markdown("### ⚙️ 설정")
        
        col_setting1, col_setting2, col_setting3 = st.columns(3)
        
        with col_setting1:
            window_size = st.selectbox(
                "윈도우 크기",
                options=[5, 6, 7, 8, 9],
                index=2,  # 7을 기본값으로
                key="validation_interactive_window_size",
                help="예측에 사용할 윈도우 크기를 선택하세요"
            )
        
        with col_setting2:
            method = st.selectbox(
                "예측 방법",
                options=["빈도 기반", "가중치 기반", "안전 우선"],
                index=0,
                key="validation_interactive_method",
                help="예측에 사용할 방법을 선택하세요"
            )
        
        with col_setting3:
            use_threshold = st.checkbox(
                "임계값 전략 사용",
                value=True,
                key="validation_interactive_use_threshold",
                help="임계값 이상일 때만 예측하도록 설정"
            )
            threshold = None
            if use_threshold:
                threshold = st.number_input(
                    "임계값 (%)",
                    min_value=0,
                    max_value=100,
                    value=60,
                    step=1,
                    key="validation_interactive_threshold",
                    help="이 신뢰도 이상일 때만 예측합니다"
                )
        
        # 최대 간격 설정 (강제 예측용)
        col_setting4, col_setting5 = st.columns(2)
        with col_setting4:
            max_interval = st.number_input(
                "최대 예측 없음 간격 (스텝)",
                min_value=1,
                max_value=20,
                value=6,
                step=1,
                key="validation_interactive_max_interval",
                help="이 간격을 넘기면 임계값 무시하고 강제 예측합니다"
            )
        
        with col_setting5:
            # 기준 Grid String ID 선택
            df_all_strings = load_preprocessed_data()
            if len(df_all_strings) > 0:
                grid_string_options = []
                for _, row in df_all_strings.iterrows():
                    grid_string_options.append((row['id'], row['created_at']))
                
                grid_string_options.sort(key=lambda x: x[0], reverse=True)
                
                current_selected = st.session_state.get('validation_interactive_cutoff_id', None)
                default_index = 0
                if current_selected is not None:
                    option_ids = [None] + [opt[0] for opt in grid_string_options]
                    if current_selected in option_ids:
                        default_index = option_ids.index(current_selected)
                
                selected_cutoff_id = st.selectbox(
                    "기준 Grid String ID (이 ID 이후의 데이터 검증)",
                    options=[None] + [opt[0] for opt in grid_string_options],
                    format_func=lambda x: "전체 데이터" if x is None else next((f"ID {opt[0]} - {opt[1]}" for opt in grid_string_options if opt[0] == x), f"ID {x} 이후"),
                    index=default_index,
                    key="validation_interactive_cutoff_id_select"
                )
                
                if selected_cutoff_id is not None:
                    selected_info = df_all_strings[df_all_strings['id'] == selected_cutoff_id].iloc[0]
                    st.info(f"선택된 기준: ID {selected_cutoff_id} (길이: {selected_info['string_length']}, 생성일: {selected_info['created_at']})")
                    
                    # 이후 데이터 개수 확인
                    conn = get_db_connection()
                    if conn is not None:
                        try:
                            count_query = "SELECT COUNT(*) as count FROM preprocessed_grid_strings WHERE id > ?"
                            count_df = pd.read_sql_query(count_query, conn, params=[selected_cutoff_id])
                            after_count = count_df.iloc[0]['count']
                            st.caption(f"검증 대상: {after_count}개의 grid_string")
                        except:
                            pass
                        finally:
                            conn.close()
            else:
                selected_cutoff_id = None
                st.warning("⚠️ 저장된 grid_string이 없습니다.")
        
        # 검증 실행 버튼
        if st.form_submit_button("검증 실행", type="primary", use_container_width=True):
            if selected_cutoff_id is None:
                st.warning("⚠️ 기준 Grid String ID를 선택해주세요.")
            else:
                st.session_state.validation_interactive_cutoff_id = selected_cutoff_id
                st.session_state.validation_interactive_results = None
                st.rerun()
    
    # 검증 실행 및 결과 표시
    if 'validation_interactive_cutoff_id' in st.session_state and st.session_state.validation_interactive_cutoff_id is not None:
        cutoff_id = st.session_state.validation_interactive_cutoff_id
        
        # 현재 설정 가져오기
        window_size = st.session_state.get('validation_interactive_window_size', 7)
        method = st.session_state.get('validation_interactive_method', '빈도 기반')
        use_threshold = st.session_state.get('validation_interactive_use_threshold', True)
        threshold = st.session_state.get('validation_interactive_threshold', 60) if use_threshold else None
        max_interval = st.session_state.get('validation_interactive_max_interval', 6)
        
        # 결과가 캐시되어 있으면 사용, 없으면 실행 (두 전략 모두)
        if 'validation_interactive_results_default' in st.session_state and 'validation_interactive_results_reverse' in st.session_state:
            batch_results_default = st.session_state.validation_interactive_results_default
            batch_results_reverse = st.session_state.validation_interactive_results_reverse
        else:
            with st.spinner("검증 실행 중... (기본 전략 + 반대 선택 전략)"):
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                try:
                    # 기본 전략 실행
                    status_text.text("기본 전략 검증 중...")
                    progress_bar.progress(0.3)
                    batch_results_default = batch_validate_interactive_multi_step_scenario(
                        cutoff_id,
                        window_size=window_size,
                        method=method,
                        use_threshold=use_threshold,
                        threshold=threshold if use_threshold else 60,
                        max_interval=max_interval,
                        reverse_forced_prediction=False
                    )
                    
                    # 반대 선택 전략 실행
                    status_text.text("반대 선택 전략 검증 중...")
                    progress_bar.progress(0.7)
                    batch_results_reverse = batch_validate_interactive_multi_step_scenario(
                        cutoff_id,
                        window_size=window_size,
                        method=method,
                        use_threshold=use_threshold,
                        threshold=threshold if use_threshold else 60,
                        max_interval=max_interval,
                        reverse_forced_prediction=True
                    )
                    
                    if batch_results_default is not None and batch_results_reverse is not None:
                        st.session_state.validation_interactive_results_default = batch_results_default
                        st.session_state.validation_interactive_results_reverse = batch_results_reverse
                        
                        # 신뢰도 통계 수집 및 저장 (기본 전략)
                        if 'all_history' in batch_results_default:
                            confidence_stats_default = collect_confidence_statistics(
                                batch_results_default['all_history'],
                                validation_id=None,
                                strategy_type='default'
                            )
                            if confidence_stats_default:
                                save_confidence_statistics(
                                    confidence_stats_default,
                                    validation_id=None,
                                    strategy_type='default'
                                )
                        
                        # 신뢰도 통계 수집 및 저장 (반대 선택 전략)
                        if 'all_history' in batch_results_reverse:
                            confidence_stats_reverse = collect_confidence_statistics(
                                batch_results_reverse['all_history'],
                                validation_id=None,
                                strategy_type='reverse'
                            )
                            if confidence_stats_reverse:
                                save_confidence_statistics(
                                    confidence_stats_reverse,
                                    validation_id=None,
                                    strategy_type='reverse'
                                )
                        
                        # 검증 결과 자동 저장 (비활성화됨)
                        # validation_id = save_validation_results(
                        #     cutoff_id,
                        #     window_size,
                        #     method,
                        #     use_threshold,
                        #     threshold if use_threshold else 60,
                        #     max_interval,
                        #     batch_results_default,
                        #     batch_results_reverse,
                        #     grid_string_ids=batch_results_default.get('grid_string_ids') if batch_results_default else None
                        # )
                        # 
                        # if validation_id:
                        #     st.session_state.validation_interactive_saved_id = validation_id
                        #     st.success(f"✅ 검증 결과가 저장되었습니다. (ID: {validation_id[:8]}...)")
                        # else:
                        #     st.warning("⚠️ 검증 결과 저장에 실패했습니다.")
                    else:
                        st.error("검증 실행 실패")
                        batch_results_default = None
                        batch_results_reverse = None
                        
                except Exception as e:
                    st.error(f"검증 실행 중 오류 발생: {str(e)}")
                    import traceback
                    st.error(f"상세 오류: {traceback.format_exc()}")
                    batch_results_default = None
                    batch_results_reverse = None
                finally:
                    progress_bar.empty()
                    status_text.empty()
        
        # 결과 비교 표시
        if batch_results_default is not None and batch_results_reverse is not None and len(batch_results_default['results']) > 0 and len(batch_results_reverse['results']) > 0:
            summary_default = batch_results_default['summary']
            summary_reverse = batch_results_reverse['summary']
            results_default = batch_results_default['results']
            results_reverse = batch_results_reverse['results']
            
            # 전략 비교 헤더
            st.markdown("---")
            st.markdown("### 전략 비교")
            col1, col2 = st.columns(2)
            with col1:
                st.info("📊 **기본 전략**: 강제 예측 시 현재 예측값 사용")
            with col2:
                st.info("📊 **반대 선택 전략**: 강제 예측 시 반대 값 선택")
            
            # 요약 통계 비교
            st.markdown("---")
            st.markdown("### 요약 통계 비교")
            
            # 비교 테이블
            comparison_data = []
            comparison_data.append({
                '항목': '총 Grid String 수',
                '기본 전략': f"{summary_default['total_grid_strings']}",
                '반대 선택 전략': f"{summary_reverse['total_grid_strings']}",
                '차이': f"{summary_reverse['total_grid_strings'] - summary_default['total_grid_strings']:+d}"
            })
            comparison_data.append({
                '항목': '평균 정확도 (%)',
                '기본 전략': f"{summary_default['avg_accuracy']:.2f}",
                '반대 선택 전략': f"{summary_reverse['avg_accuracy']:.2f}",
                '차이': f"{summary_reverse['avg_accuracy'] - summary_default['avg_accuracy']:+.2f}"
            })
            comparison_data.append({
                '항목': '최대 연속 실패',
                '기본 전략': f"{summary_default['max_consecutive_failures']}",
                '반대 선택 전략': f"{summary_reverse['max_consecutive_failures']}",
                '차이': f"{summary_reverse['max_consecutive_failures'] - summary_default['max_consecutive_failures']:+d}"
            })
            comparison_data.append({
                '항목': '평균 최대 연속 실패',
                '기본 전략': f"{summary_default['avg_max_consecutive_failures']:.2f}",
                '반대 선택 전략': f"{summary_reverse['avg_max_consecutive_failures']:.2f}",
                '차이': f"{summary_reverse['avg_max_consecutive_failures'] - summary_default['avg_max_consecutive_failures']:+.2f}"
            })
            comparison_data.append({
                '항목': '예측률 (%)',
                '기본 전략': f"{summary_default['prediction_rate']:.2f}",
                '반대 선택 전략': f"{summary_reverse['prediction_rate']:.2f}",
                '차이': f"{summary_reverse['prediction_rate'] - summary_default['prediction_rate']:+.2f}"
            })
            comparison_data.append({
                '항목': '강제 예측 비율 (%)',
                '기본 전략': f"{summary_default.get('forced_prediction_rate', 0):.2f}",
                '반대 선택 전략': f"{summary_reverse.get('forced_prediction_rate', 0):.2f}",
                '차이': f"{summary_reverse.get('forced_prediction_rate', 0) - summary_default.get('forced_prediction_rate', 0):+.2f}"
            })
            comparison_data.append({
                '항목': '강제 예측 성공 비율 (%)',
                '기본 전략': f"{summary_default.get('forced_success_rate', 0):.2f}",
                '반대 선택 전략': f"{summary_reverse.get('forced_success_rate', 0):.2f}",
                '차이': f"{summary_reverse.get('forced_success_rate', 0) - summary_default.get('forced_success_rate', 0):+.2f}"
            })
            comparison_data.append({
                '항목': '총 스텝 수',
                '기본 전략': f"{summary_default['total_steps']}",
                '반대 선택 전략': f"{summary_reverse['total_steps']}",
                '차이': f"{summary_reverse['total_steps'] - summary_default['total_steps']:+d}"
            })
            comparison_data.append({
                '항목': '총 실패 횟수',
                '기본 전략': f"{summary_default['total_failures']}",
                '반대 선택 전략': f"{summary_reverse['total_failures']}",
                '차이': f"{summary_reverse['total_failures'] - summary_default['total_failures']:+d}"
            })
            comparison_data.append({
                '항목': '총 예측 횟수',
                '기본 전략': f"{summary_default['total_predictions']}",
                '반대 선택 전략': f"{summary_reverse['total_predictions']}",
                '차이': f"{summary_reverse['total_predictions'] - summary_default['total_predictions']:+d}"
            })
            comparison_data.append({
                '항목': '총 강제 예측 횟수',
                '기본 전략': f"{summary_default.get('total_forced_predictions', 0)}",
                '반대 선택 전략': f"{summary_reverse.get('total_forced_predictions', 0)}",
                '차이': f"{summary_reverse.get('total_forced_predictions', 0) - summary_default.get('total_forced_predictions', 0):+d}"
            })
            
            comparison_df = pd.DataFrame(comparison_data)
            st.dataframe(comparison_df, use_container_width=True, hide_index=True)
            
            # 신뢰도 구간별 통계 표시
            st.markdown("---")
            st.markdown("### 📊 신뢰도 구간별 통계 (50-60% 구간)")
            
            # DB에서 신뢰도 통계 조회
            conn = get_db_connection()
            if conn is not None:
                try:
                    # 기본 전략 통계
                    stats_query_default = """
                        SELECT confidence_range, total_predictions, matches, mismatches, 
                               match_rate, avg_confidence
                        FROM confidence_statistics
                        WHERE strategy_type = 'default'
                        ORDER BY confidence_range
                    """
                    stats_df_default = pd.read_sql_query(stats_query_default, conn)
                    
                    # 반대 선택 전략 통계
                    stats_query_reverse = """
                        SELECT confidence_range, total_predictions, matches, mismatches, 
                               match_rate, avg_confidence
                        FROM confidence_statistics
                        WHERE strategy_type = 'reverse'
                        ORDER BY confidence_range
                    """
                    stats_df_reverse = pd.read_sql_query(stats_query_reverse, conn)
                    
                    if len(stats_df_default) > 0 or len(stats_df_reverse) > 0:
                        col_stats1, col_stats2 = st.columns(2)
                        
                        with col_stats1:
                            st.markdown("#### 기본 전략")
                            if len(stats_df_default) > 0:
                                st.dataframe(stats_df_default, use_container_width=True, hide_index=True)
                            else:
                                st.info("통계 데이터가 없습니다.")
                        
                        with col_stats2:
                            st.markdown("#### 반대 선택 전략")
                            if len(stats_df_reverse) > 0:
                                st.dataframe(stats_df_reverse, use_container_width=True, hide_index=True)
                            else:
                                st.info("통계 데이터가 없습니다.")
                    else:
                        st.info("💡 신뢰도 통계 데이터가 없습니다. 검증을 실행하면 통계가 수집됩니다.")
                except Exception as e:
                    st.warning(f"신뢰도 통계 조회 중 오류: {str(e)}")
                finally:
                    conn.close()
            
            # 마지막 Grid String 히스토리 자동 표시 (검증용)
            st.markdown("---")
            st.markdown("### 🔍 마지막 Grid String 검증 히스토리")
            st.markdown("**의도대로 동작하는지 확인하기 위한 마지막 grid_string_id의 상세 히스토리**")
            
            if len(results_default) > 0 and len(results_reverse) > 0:
                # 마지막 grid_string_id 찾기 (두 결과는 같은 순서이므로 마지막 항목 사용)
                last_result_default = results_default[-1]
                last_result_reverse = results_reverse[-1]
                last_grid_id = last_result_default['grid_string_id']
                
                st.info(f"📌 **검증 대상**: Grid String ID {last_grid_id} (마지막 검증된 grid_string)")
                
                # 전체 히스토리 보기 옵션
                show_full_history = st.checkbox(
                    "전체 히스토리 보기 (기본: 최근 50개만 표시)",
                    value=False,
                    key="last_grid_full_history"
                )
                
                # 기본 전략과 반대 선택 전략 모두 표시
                col_last1, col_last2 = st.columns(2)
                
                with col_last1:
                    st.markdown("#### 기본 전략 히스토리")
                    
                    if last_result_default:
                        failure_history_default = get_failure_history_interactive(
                            last_grid_id,
                            cutoff_id,
                            window_size=window_size,
                            method=method,
                            use_threshold=use_threshold,
                            threshold=threshold if use_threshold else 60,
                            max_interval=max_interval,
                            reverse_forced_prediction=False
                        )
                        
                        if failure_history_default:
                            st.metric("최대 연속 실패", f"{failure_history_default['max_consecutive_failures']}회")
                            st.metric("총 스텝", f"{failure_history_default['total_steps']}")
                            st.metric("총 예측", f"{failure_history_default['total_predictions']}")
                            st.metric("정확도", f"{failure_history_default['accuracy']:.2f}%")
                            
                            # 히스토리 테이블
                            history_default = failure_history_default.get('history', [])
                            if len(history_default) > 0:
                                history_limit = None if show_full_history else 50
                                history_title = "##### 상세 히스토리" + (f" (최신 {history_limit}개)" if history_limit else " (전체)")
                                st.markdown(history_title)
                                history_data_default = []
                                # 히스토리를 최신순으로 정렬 (step 내림차순)
                                history_default_sorted = sorted(history_default, key=lambda x: x.get('step', 0), reverse=True)
                                display_history = history_default_sorted[:history_limit] if history_limit else history_default_sorted
                                
                                for entry in display_history:
                                    is_correct = entry.get('is_correct')
                                    match_status = '✅' if is_correct else ('❌' if is_correct is False else '-')
                                    has_prediction = entry.get('has_prediction', False)
                                    is_forced = entry.get('is_forced', False)
                                    validated = entry.get('validated', False)
                                    
                                    forced_mark = '⚡' if is_forced else ''
                                    no_pred_mark = '🚫' if not has_prediction else ''
                                    validated_mark = '✓' if validated else ''
                                    
                                    history_data_default.append({
                                        'Step': entry.get('step', 0),
                                        'Prefix': entry.get('prefix', ''),
                                        '예측': f"{entry.get('predicted', '-')}{forced_mark}{no_pred_mark}",
                                        '실제값': entry.get('actual', '-'),
                                        '일치': match_status,
                                        '검증': validated_mark,
                                        '신뢰도': f"{entry.get('confidence', 0):.1f}" if has_prediction else '-',
                                        '간격': entry.get('current_interval', 0) if not has_prediction else 0
                                    })
                                
                                history_df_default = pd.DataFrame(history_data_default)
                                st.dataframe(history_df_default, use_container_width=True, hide_index=True)
                                
                                if not show_full_history and len(history_default) > 50:
                                    st.caption(f"💡 전체 {len(history_default)}개 중 최신 50개만 표시됩니다. 전체 히스토리를 보려면 위의 체크박스를 선택하세요.")
                
                with col_last2:
                    st.markdown("#### 반대 선택 전략 히스토리")
                    
                    if last_result_reverse:
                        failure_history_reverse = get_failure_history_interactive(
                            last_grid_id,
                            cutoff_id,
                            window_size=window_size,
                            method=method,
                            use_threshold=use_threshold,
                            threshold=threshold if use_threshold else 60,
                            max_interval=max_interval,
                            reverse_forced_prediction=True
                        )
                        
                        if failure_history_reverse:
                            st.metric("최대 연속 실패", f"{failure_history_reverse['max_consecutive_failures']}회")
                            st.metric("총 스텝", f"{failure_history_reverse['total_steps']}")
                            st.metric("총 예측", f"{failure_history_reverse['total_predictions']}")
                            st.metric("정확도", f"{failure_history_reverse['accuracy']:.2f}%")
                            
                            # 히스토리 테이블
                            history_reverse = failure_history_reverse.get('history', [])
                            if len(history_reverse) > 0:
                                history_limit = None if show_full_history else 50
                                history_title = "##### 상세 히스토리" + (f" (최신 {history_limit}개)" if history_limit else " (전체)")
                                st.markdown(history_title)
                                history_data_reverse = []
                                # 히스토리를 최신순으로 정렬 (step 내림차순)
                                history_reverse_sorted = sorted(history_reverse, key=lambda x: x.get('step', 0), reverse=True)
                                display_history = history_reverse_sorted[:history_limit] if history_limit else history_reverse_sorted
                                
                                for entry in display_history:
                                    is_correct = entry.get('is_correct')
                                    match_status = '✅' if is_correct else ('❌' if is_correct is False else '-')
                                    has_prediction = entry.get('has_prediction', False)
                                    is_forced = entry.get('is_forced', False)
                                    validated = entry.get('validated', False)
                                    
                                    forced_mark = '⚡' if is_forced else ''
                                    no_pred_mark = '🚫' if not has_prediction else ''
                                    validated_mark = '✓' if validated else ''
                                    
                                    history_data_reverse.append({
                                        'Step': entry.get('step', 0),
                                        'Prefix': entry.get('prefix', ''),
                                        '예측': f"{entry.get('predicted', '-')}{forced_mark}{no_pred_mark}",
                                        '실제값': entry.get('actual', '-'),
                                        '일치': match_status,
                                        '검증': validated_mark,
                                        '신뢰도': f"{entry.get('confidence', 0):.1f}" if has_prediction else '-',
                                        '간격': entry.get('current_interval', 0) if not has_prediction else 0
                                    })
                                
                                history_df_reverse = pd.DataFrame(history_data_reverse)
                                st.dataframe(history_df_reverse, use_container_width=True, hide_index=True)
                                
                                if not show_full_history and len(history_reverse) > 50:
                                    st.caption(f"💡 전체 {len(history_reverse)}개 중 최신 50개만 표시됩니다. 전체 히스토리를 보려면 위의 체크박스를 선택하세요.")
                
                # 검증 포인트 안내
                st.markdown("---")
                st.markdown("#### 🔍 검증 포인트")
                st.markdown("""
                다음 사항들을 확인해주세요:
                1. **간격 조건**: `validated` 컬럼이 '✓'인 스텝에서만 실제 비교가 수행되는지 확인
                2. **강제 예측**: `⚡` 표시가 있는 스텝에서 강제 예측이 올바르게 수행되는지 확인
                3. **연속 실패 추적**: 연속으로 실패하는 경우가 올바르게 카운트되는지 확인
                4. **간격 계산**: 예측이 없을 때 간격이 올바르게 증가하는지 확인
                5. **반대 선택 전략**: 반대 선택 전략에서 강제 예측 시 반대 값이 선택되는지 확인
                """)
            else:
                st.warning("⚠️ 마지막 grid_string_id를 찾을 수 없습니다.")
            
            # 최적 전략 추천
            st.markdown("---")
            st.markdown("### 최적 전략 추천")
            
            # 점수 계산 (최대 연속 실패 감소가 가장 중요)
            default_score = (
                (100 - summary_default['max_consecutive_failures']) * 10 +
                summary_default['avg_accuracy'] * 0.1
            )
            reverse_score = (
                (100 - summary_reverse['max_consecutive_failures']) * 10 +
                summary_reverse['avg_accuracy'] * 0.1
            )
            
            if reverse_score > default_score:
                best_strategy = "반대 선택 전략"
                best_summary = summary_reverse
                worst_summary = summary_default
                improvement = reverse_score - default_score
            else:
                best_strategy = "기본 전략"
                best_summary = summary_default
                worst_summary = summary_reverse
                improvement = default_score - reverse_score
            
            st.success(f"✅ **추천 전략**: {best_strategy}")
            col1, col2 = st.columns(2)
            with col1:
                st.metric("최대 연속 실패", 
                         f"{worst_summary['max_consecutive_failures']}회 → {best_summary['max_consecutive_failures']}회",
                         f"{best_summary['max_consecutive_failures'] - worst_summary['max_consecutive_failures']:+d}회")
            with col2:
                st.metric("평균 정확도",
                         f"{worst_summary['avg_accuracy']:.2f}% → {best_summary['avg_accuracy']:.2f}%",
                         f"{best_summary['avg_accuracy'] - worst_summary['avg_accuracy']:+.2f}%")
            
            # 강제 예측 성공률 비교
            forced_success_default = summary_default.get('forced_success_rate', 0)
            forced_success_reverse = summary_reverse.get('forced_success_rate', 0)
            
            if forced_success_default < 30:
                st.warning(f"⚠️ 기본 전략의 강제 예측 성공률이 {forced_success_default:.2f}%로 매우 낮습니다. 반대 선택 전략이 더 효과적일 수 있습니다.")
            
            # Grid String별 비교 결과
            comparison_results_data = []
            for i, result_default in enumerate(results_default):
                result_reverse = results_reverse[i] if i < len(results_reverse) else None
                if result_reverse is None:
                    continue
                
                grid_id = result_default['grid_string_id']
                comparison_results_data.append({
                    'Grid String ID': grid_id,
                    '기본_최대연속실패': result_default['max_consecutive_failures'],
                    '반대_최대연속실패': result_reverse['max_consecutive_failures'],
                    '최대연속실패_차이': f"{result_reverse['max_consecutive_failures'] - result_default['max_consecutive_failures']:+d}",
                    '기본_정확도': f"{result_default['accuracy']:.2f}%",
                    '반대_정확도': f"{result_reverse['accuracy']:.2f}%",
                    '정확도_차이': f"{result_reverse['accuracy'] - result_default['accuracy']:+.2f}%",
                    '기본_강제성공률': f"{result_default.get('forced_success_rate', 0):.2f}%",
                    '반대_강제성공률': f"{result_reverse.get('forced_success_rate', 0):.2f}%"
                })
            
            comparison_results_df = pd.DataFrame(comparison_results_data)
            st.dataframe(comparison_results_df, use_container_width=True, hide_index=True)
            
            # 최대 연속 실패 분포 비교
            st.markdown("---")
            st.markdown("### 최대 연속 실패 분포 비교")
            
            max_failures_default = [r['max_consecutive_failures'] for r in results_default]
            max_failures_reverse = [r['max_consecutive_failures'] for r in results_reverse]
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown("#### 기본 전략")
                max_failures_list = max_failures_default
                
                if len(max_failures_list) > 0:
                    max_value = max(max_failures_list)
                    
                    # 구간별 분포 계산
                    bins = defaultdict(int)
                    for value in max_failures_list:
                        if value == 0:
                            bins['0'] += 1
                        elif value <= 2:
                            bins['1-2'] += 1
                        elif value <= 5:
                            bins['3-5'] += 1
                        elif value <= 10:
                            bins['6-10'] += 1
                        else:
                            bins['11+'] += 1
                    
                    # 히스토그램 표시
                    st.markdown("##### 구간별 분포")
                    max_count = max(bins.values()) if bins else 1
                    
                    for bin_range, count in sorted(bins.items(), key=lambda x: {
                        '0': 0, '1-2': 1, '3-5': 2, '6-10': 3, '11+': 4
                    }.get(x[0], 5)):
                        ratio = (count / len(results_default) * 100) if len(results_default) > 0 else 0
                        bar_length = int((count / max_count) * 50) if max_count > 0 else 0
                        bar = '█' * bar_length
                        st.text(f"{bin_range:>8}: {bar} {count:>4}개 ({ratio:>5.2f}%)")
                    
                    st.markdown("##### 통계")
                    st.metric("최소값", min(max_failures_list))
                    st.metric("최대값", max(max_failures_list))
                    st.metric("평균값", f"{summary_default['avg_max_consecutive_failures']:.2f}")
            
            with col2:
                st.markdown("#### 반대 선택 전략")
                max_failures_list = max_failures_reverse
                
                if len(max_failures_list) > 0:
                    max_value = max(max_failures_list)
                    
                    # 구간별 분포 계산
                    bins = defaultdict(int)
                    for value in max_failures_list:
                        if value == 0:
                            bins['0'] += 1
                        elif value <= 2:
                            bins['1-2'] += 1
                        elif value <= 5:
                            bins['3-5'] += 1
                        elif value <= 10:
                            bins['6-10'] += 1
                        else:
                            bins['11+'] += 1
                    
                    # 히스토그램 표시
                    st.markdown("##### 구간별 분포")
                    max_count = max(bins.values()) if bins else 1
                    
                    for bin_range, count in sorted(bins.items(), key=lambda x: {
                        '0': 0, '1-2': 1, '3-5': 2, '6-10': 3, '11+': 4
                    }.get(x[0], 5)):
                        ratio = (count / len(results_reverse) * 100) if len(results_reverse) > 0 else 0
                        bar_length = int((count / max_count) * 50) if max_count > 0 else 0
                        bar = '█' * bar_length
                        st.text(f"{bin_range:>8}: {bar} {count:>4}개 ({ratio:>5.2f}%)")
                    
                    st.markdown("##### 통계")
                    st.metric("최소값", min(max_failures_list))
                    st.metric("최대값", max(max_failures_list))
                    st.metric("평균값", f"{summary_reverse['avg_max_consecutive_failures']:.2f}")
            
            # 상세 히스토리 조회
            st.markdown("---")
            st.markdown("### 상세 히스토리 조회")
            
            # 최대 연속 실패가 발생한 Grid String 선택 (두 전략 중 더 나쁜 결과 기준)
            high_failure_results = []
            for i, result_default in enumerate(results_default):
                result_reverse = results_reverse[i] if i < len(results_reverse) else None
                if result_reverse is None:
                    continue
                
                max_fail = max(result_default.get('max_consecutive_failures', 0), 
                              result_reverse.get('max_consecutive_failures', 0))
                if max_fail >= 5:
                    high_failure_results.append({
                        'grid_string_id': result_default['grid_string_id'],
                        'max_consecutive_failures': max_fail,
                        'default_accuracy': result_default.get('accuracy', 0),
                        'reverse_accuracy': result_reverse.get('accuracy', 0) if result_reverse else 0
                    })
            
            if len(high_failure_results) > 0:
                st.markdown("#### 최대 연속 실패 발생 Grid String")
                
                failure_options = []
                for result in high_failure_results:
                    display_text = f"ID {result['grid_string_id']} - 최대 연속 실패: {result['max_consecutive_failures']}회 - 기본: {result['default_accuracy']:.2f}% / 반대: {result['reverse_accuracy']:.2f}%"
                    failure_options.append((result['grid_string_id'], display_text))
                
                selected_history_id = st.selectbox(
                    "Grid String 선택",
                    options=[None] + [opt[0] for opt in failure_options],
                    format_func=lambda x: "선택 안함" if x is None else f"ID {x}",
                    key="validation_interactive_selected_history_id"
                )
                
                if selected_history_id is not None:
                    col_hist1, col_hist2 = st.columns(2)
                    with col_hist1:
                        if st.button("기본 전략 히스토리 보기", key="validation_interactive_view_history_default"):
                            st.session_state.validation_interactive_view_history_id = selected_history_id
                            st.session_state.validation_interactive_view_history_strategy = 'default'
                            st.rerun()
                    with col_hist2:
                        if st.button("반대 선택 전략 히스토리 보기", key="validation_interactive_view_history_reverse"):
                            st.session_state.validation_interactive_view_history_id = selected_history_id
                            st.session_state.validation_interactive_view_history_strategy = 'reverse'
                            st.rerun()
                
                # 상세 히스토리 표시
                if 'validation_interactive_view_history_id' in st.session_state:
                    history_id = st.session_state.validation_interactive_view_history_id
                    strategy_type = st.session_state.get('validation_interactive_view_history_strategy', 'default')
                    reverse_forced = (strategy_type == 'reverse')
                    
                    failure_history = get_failure_history_interactive(
                        history_id,
                        cutoff_id,
                        window_size=window_size,
                        method=method,
                        use_threshold=use_threshold,
                        threshold=threshold if use_threshold else 60,
                        max_interval=max_interval,
                        reverse_forced_prediction=reverse_forced
                    )
                    
                    if failure_history is not None:
                        st.markdown("---")
                        strategy_label = "반대 선택 전략" if reverse_forced else "기본 전략"
                        st.markdown(f"### Grid String ID {history_id} 상세 히스토리 ({strategy_label})")
                        
                        # 요약 정보
                        col1, col2, col3, col4, col5 = st.columns(5)
                        with col1:
                            st.metric("최대 연속 실패", f"{failure_history['max_consecutive_failures']}회")
                        with col2:
                            st.metric("총 스텝", f"{failure_history['total_steps']}")
                        with col3:
                            st.metric("총 실패", f"{failure_history['total_failures']}")
                        with col4:
                            st.metric("총 예측", f"{failure_history['total_predictions']}")
                        with col5:
                            st.metric("정확도", f"{failure_history['accuracy']:.2f}%")
                        
                        # 히스토리 테이블
                        st.markdown("#### 상세 히스토리")
                        history_data = []
                        history = failure_history.get('history', [])
                        
                        for entry in history:
                            is_correct = entry.get('is_correct')
                            match_status = '✅' if is_correct else ('❌' if is_correct is False else '-')
                            has_prediction = entry.get('has_prediction', False)
                            is_forced = entry.get('is_forced', False)
                            
                            forced_mark = '⚡' if is_forced else ''
                            no_pred_mark = '🚫' if not has_prediction else ''
                            
                            history_data.append({
                                'Step': entry.get('step', 0),
                                'Prefix': entry.get('prefix', ''),
                                '예측': f"{entry.get('predicted', '-')}{forced_mark}{no_pred_mark}",
                                '실제값': entry.get('actual', '-'),
                                '일치': match_status,
                                '신뢰도 (%)': f"{entry.get('confidence', 0):.1f}" if has_prediction else '-',
                                '간격': entry.get('current_interval', 0) if not has_prediction else 0
                            })
                        
                        history_df = pd.DataFrame(history_data)
                        st.dataframe(history_df, use_container_width=True, hide_index=True)
            else:
                st.info("💡 최대 연속 실패가 5회 이상인 Grid String이 없습니다.")
        else:
            st.info("💡 검증 결과가 없습니다. 먼저 검증을 실행하세요.")
    else:
        st.info("💡 기준 Grid String ID를 선택하고 검증을 실행하세요.")
    
    # 신뢰도 기반 스킵 전략 검증 섹션
    st.markdown("---")
    st.header("🎯 신뢰도 기반 스킵 전략 검증")
    st.markdown("""
    **전략 설명:**
    - 기본 규칙은 기존과 동일
    - 강제 예측 신뢰도가 51% 미만인 경우 해당 스텝은 스킵 (다음 스텝으로 진행)
    - 스킵 상태에서 간격 계산은 멈춤 (증가하지 않음)
    - 다음 스텝에서 임계값 만족 예측 또는 신뢰도 51% 이상 강제 예측이 나올 때까지 대기
    
    **검증 목적:**
    - 신뢰도 51% 미만인 강제 예측의 성공 확률이 낮은지 검증
    - 스킵 전략이 최대 연속 실패를 줄이는지 확인
    """)
    
    # 설정 섹션
    st.markdown("### ⚙️ 설정")
    
    # 기준 Grid String ID 새로고침 버튼 (form 밖에 위치)
    col_refresh_header = st.columns([1, 4])
    with col_refresh_header[0]:
        if st.button("🔄 데이터 새로고침", key="confidence_skip_refresh_data", use_container_width=True):
            # 데이터 새로고침을 위해 캐시 제거
            if 'preprocessed_data_cache' in st.session_state:
                del st.session_state.preprocessed_data_cache
            st.rerun()
    with col_refresh_header[1]:
        st.caption("데이터 목록을 업데이트합니다")
    
    # 데이터 로드 (form 밖에서)
    df_all_strings_skip = load_preprocessed_data()
    grid_string_options_skip = []
    if len(df_all_strings_skip) > 0:
        for _, row in df_all_strings_skip.iterrows():
            grid_string_options_skip.append((row['id'], row['created_at']))
        grid_string_options_skip.sort(key=lambda x: x[0], reverse=True)
    
    with st.form("confidence_skip_settings_form", clear_on_submit=False):
        col_skip1, col_skip2, col_skip3 = st.columns(3)
        
        with col_skip1:
            skip_window_size = st.selectbox(
                "윈도우 크기",
                options=[5, 6, 7, 8, 9],
                index=0,
                key="confidence_skip_window_size",
                help="예측에 사용할 윈도우 크기"
            )
        
        with col_skip2:
            skip_method = st.selectbox(
                "예측 방법",
                options=["빈도 기반", "가중치 기반", "안전 우선"],
                index=0,
                key="confidence_skip_method",
                help="예측에 사용할 방법"
            )
        
        with col_skip3:
            skip_use_threshold = st.checkbox(
                "임계값 전략 사용",
                value=True,
                key="confidence_skip_use_threshold",
                help="임계값 이상일 때만 예측"
            )
            skip_threshold = None
            if skip_use_threshold:
                skip_threshold = st.number_input(
                    "임계값 (%)",
                    min_value=0,
                    max_value=100,
                    value=56,
                    step=1,
                    key="confidence_skip_threshold_value",
                    help="이 신뢰도 이상일 때만 예측"
                )
        
        # 예측값 테이블 사용 옵션 (새로운 행)
        st.markdown("---")
        st.markdown("**📊 예측 모델 선택**")
        col_model1, col_model2 = st.columns(2)
        
        with col_model1:
            skip_use_stored_predictions = st.checkbox(
                "예측값 테이블 사용 (stored_predictions)",
                value=False,
                key="confidence_skip_use_stored_predictions",
                help="체크하면 stored_predictions 테이블의 예측값을 사용, 체크 해제하면 실시간 모델 사용"
            )
        
        with col_model2:
            skip_stored_threshold = st.number_input(
                "테이블 조회 임계값",
                min_value=0,
                max_value=100,
                value=0,
                step=1,
                key="confidence_skip_stored_threshold",
                help="stored_predictions 테이블에서 조회할 때 사용할 임계값 (기본값: 0)",
                disabled=not st.session_state.get('confidence_skip_use_stored_predictions', False)
            )
        
        col_skip4, col_skip5 = st.columns(2)
        with col_skip4:
            skip_max_interval = st.number_input(
                "최대 예측 없음 간격",
                min_value=1,
                max_value=20,
                value=5,
                step=1,
                key="confidence_skip_max_interval",
                help="이 간격을 넘기면 강제 예측"
            )
            
            # 스킵 신뢰도 임계값 2개 선택
            st.markdown("**스킵 신뢰도 임계값**")
            col_threshold1, col_threshold2 = st.columns(2)
            with col_threshold1:
                skip_confidence_threshold_1 = st.number_input(
                    "임계값 1 (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=50.9,
                    step=0.1,
                    key="confidence_skip_threshold_1",
                    help="첫 번째 스킵 신뢰도 임계값"
                )
            with col_threshold2:
                skip_confidence_threshold_2 = st.number_input(
                    "임계값 2 (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=51.9,
                    step=0.1,
                    key="confidence_skip_threshold_2",
                    help="두 번째 스킵 신뢰도 임계값"
                )
        
        with col_skip5:
            # 기준 Grid String ID 선택
            if len(grid_string_options_skip) > 0:
                current_selected_skip = st.session_state.get('confidence_skip_cutoff_id', None)
                default_index_skip = 0
                if current_selected_skip is not None:
                    option_ids_skip = [None] + [opt[0] for opt in grid_string_options_skip]
                    if current_selected_skip in option_ids_skip:
                        default_index_skip = option_ids_skip.index(current_selected_skip)
                
                selected_cutoff_id_skip = st.selectbox(
                    "기준 Grid String ID",
                    options=[None] + [opt[0] for opt in grid_string_options_skip],
                    format_func=lambda x: "전체 데이터" if x is None else next((f"ID {opt[0]} - {opt[1]}" for opt in grid_string_options_skip if opt[0] == x), f"ID {x} 이후"),
                    index=default_index_skip,
                    key="confidence_skip_cutoff_id_select"
                )
                
                if selected_cutoff_id_skip is not None:
                    st.session_state.confidence_skip_cutoff_id = selected_cutoff_id_skip
            else:
                selected_cutoff_id_skip = None
                st.info("⚠️ 저장된 grid_string이 없습니다.")
        
        # 검증 실행 버튼
        submitted = st.form_submit_button("신뢰도 스킵 전략 검증 실행", type="primary", use_container_width=True)
    
    # form 밖에서 submit 처리
    if submitted:
        # form 안에서 선택된 값은 위젯의 key를 통해 session_state에 자동 저장됨
        selected_cutoff_id_skip = st.session_state.get('confidence_skip_cutoff_id_select', None)
        if selected_cutoff_id_skip is None:
            st.warning("⚠️ 기준 Grid String ID를 선택해주세요.")
        else:
            # 선택된 값을 confidence_skip_cutoff_id에 저장
            st.session_state.confidence_skip_cutoff_id = selected_cutoff_id_skip
            # 스킵 신뢰도 임계값은 위젯에서 자동으로 session_state에 저장되므로 읽기만 함
            # 결과 캐시 제거하여 새로 실행하도록 함
            if 'confidence_skip_results_1' in st.session_state:
                del st.session_state.confidence_skip_results_1
            if 'confidence_skip_results_2' in st.session_state:
                del st.session_state.confidence_skip_results_2
            st.rerun()
    
    # 신뢰도 스킵 전략 검증 실행 및 결과 표시
    if 'confidence_skip_cutoff_id' in st.session_state and st.session_state.confidence_skip_cutoff_id is not None:
        cutoff_id_skip = st.session_state.confidence_skip_cutoff_id
        
        # 현재 설정 가져오기
        skip_window_size = st.session_state.get('confidence_skip_window_size', 6)
        skip_method = st.session_state.get('confidence_skip_method', '빈도 기반')
        skip_use_threshold = st.session_state.get('confidence_skip_use_threshold', True)
        skip_threshold_val = st.session_state.get('confidence_skip_threshold_value', 56) if skip_use_threshold else None
        skip_max_interval = st.session_state.get('confidence_skip_max_interval', 5)
        skip_confidence_threshold_1 = st.session_state.get('confidence_skip_threshold_1', 51)
        skip_confidence_threshold_2 = st.session_state.get('confidence_skip_threshold_2', 52)
        # 예측값 테이블 사용 옵션
        skip_use_stored_predictions = st.session_state.get('confidence_skip_use_stored_predictions', False)
        skip_stored_threshold = st.session_state.get('confidence_skip_stored_threshold', 0)
        
        # 예측 모델 정보 표시
        if skip_use_stored_predictions:
            st.info(f"📊 **예측값 테이블 사용**: stored_predictions (임계값: {skip_stored_threshold})")
        else:
            st.info("🧠 **실시간 모델 사용**: 학습 데이터 기반 모델 생성")
        
        # 첫 번째 임계값 검증 실행
        if 'confidence_skip_results_1' in st.session_state and st.session_state.confidence_skip_results_1 is not None:
            batch_results_skip_1 = st.session_state.confidence_skip_results_1
        else:
            model_type_str = "예측값 테이블" if skip_use_stored_predictions else "실시간 모델"
            with st.spinner(f"신뢰도 스킵 전략 검증 실행 중... (임계값 1: {skip_confidence_threshold_1}%, {model_type_str})"):
                try:
                    batch_results_skip_1 = batch_validate_interactive_multi_step_scenario_with_confidence_skip(
                        cutoff_id_skip,
                        window_size=skip_window_size,
                        method=skip_method,
                        use_threshold=skip_use_threshold,
                        threshold=skip_threshold_val if skip_use_threshold else 60,
                        max_interval=skip_max_interval,
                        reverse_forced_prediction=False,
                        confidence_skip_threshold=skip_confidence_threshold_1,
                        use_stored_predictions=skip_use_stored_predictions,
                        stored_threshold=skip_stored_threshold
                    )
                    
                    if batch_results_skip_1 is not None:
                        st.session_state.confidence_skip_results_1 = batch_results_skip_1
                    else:
                        batch_results_skip_1 = None
                        st.session_state.confidence_skip_results_1 = None
                except Exception as e:
                    st.error(f"검증 실행 중 오류 발생: {str(e)}")
                    batch_results_skip_1 = None
                    st.session_state.confidence_skip_results_1 = None
        
        # 두 번째 임계값 검증 실행
        if 'confidence_skip_results_2' in st.session_state and st.session_state.confidence_skip_results_2 is not None:
            batch_results_skip_2 = st.session_state.confidence_skip_results_2
        else:
            model_type_str = "예측값 테이블" if skip_use_stored_predictions else "실시간 모델"
            with st.spinner(f"신뢰도 스킵 전략 검증 실행 중... (임계값 2: {skip_confidence_threshold_2}%, {model_type_str})"):
                try:
                    batch_results_skip_2 = batch_validate_interactive_multi_step_scenario_with_confidence_skip(
                        cutoff_id_skip,
                        window_size=skip_window_size,
                        method=skip_method,
                        use_threshold=skip_use_threshold,
                        threshold=skip_threshold_val if skip_use_threshold else 60,
                        max_interval=skip_max_interval,
                        reverse_forced_prediction=False,
                        confidence_skip_threshold=skip_confidence_threshold_2,
                        use_stored_predictions=skip_use_stored_predictions,
                        stored_threshold=skip_stored_threshold
                    )
                    
                    if batch_results_skip_2 is not None:
                        st.session_state.confidence_skip_results_2 = batch_results_skip_2
                    else:
                        batch_results_skip_2 = None
                        st.session_state.confidence_skip_results_2 = None
                except Exception as e:
                    st.error(f"검증 실행 중 오류 발생: {str(e)}")
                    batch_results_skip_2 = None
                    st.session_state.confidence_skip_results_2 = None
        
        # 첫 번째 임계값 결과 표시
        if batch_results_skip_1 is None:
            st.info("💡 검증을 실행하면 결과가 표시됩니다.")
        elif len(batch_results_skip_1.get('results', [])) == 0:
            st.warning("⚠️ 검증 결과가 없습니다. 기준 Grid String ID 이후의 데이터가 없을 수 있습니다.")
        else:
            summary_skip_1 = batch_results_skip_1.get('summary', {})
            
            st.markdown("---")
            st.markdown(f"### 신뢰도 스킵 전략 검증 결과 (임계값 1: {skip_confidence_threshold_1}%)")
            
            col_skip_result1, col_skip_result2, col_skip_result3, col_skip_result4, col_skip_result5, col_skip_result6 = st.columns(6)
            with col_skip_result1:
                st.metric("평균 정확도", f"{summary_skip_1.get('avg_accuracy', 0):.2f}%")
            with col_skip_result2:
                st.metric("최대 연속 실패", f"{summary_skip_1.get('max_consecutive_failures', 0)}회")
            with col_skip_result3:
                st.metric("총 스킵 횟수", f"{summary_skip_1.get('total_skipped_predictions', 0)}회")
            with col_skip_result4:
                st.metric("예측률", f"{summary_skip_1.get('prediction_rate', 0):.2f}%")
            with col_skip_result5:
                first_success = summary_skip_1.get('avg_first_success_step')
                if first_success is not None:
                    st.metric("평균 첫 성공 스텝", f"{first_success:.1f}")
                else:
                    st.metric("평균 첫 성공 스텝", "-")
            with col_skip_result6:
                max_first_success = summary_skip_1.get('max_first_success_step')
                if max_first_success is not None and max_first_success > 0:
                    st.metric("최대 첫 성공 스텝", f"{max_first_success}")
                else:
                    st.metric("최대 첫 성공 스텝", "-")
            
            # 추가 통계 표시
            st.markdown("---")
            st.markdown("#### 상세 통계")
            detail_stats_1 = {
                '총 Grid String 수': summary_skip_1.get('total_grid_strings', 0),
                '총 스텝 수': summary_skip_1.get('total_steps', 0),
                '총 예측 횟수': summary_skip_1.get('total_predictions', 0),
                '총 실패 횟수': summary_skip_1.get('total_failures', 0),
                '총 강제 예측 횟수': summary_skip_1.get('total_forced_predictions', 0),
                '평균 최대 연속 실패': f"{summary_skip_1.get('avg_max_consecutive_failures', 0):.2f}",
                '강제 예측 비율': f"{summary_skip_1.get('forced_prediction_rate', 0):.2f}%",
                '강제 예측 성공 비율': f"{summary_skip_1.get('forced_success_rate', 0):.2f}%",
                '평균 첫 성공 스텝': f"{summary_skip_1.get('avg_first_success_step', 0):.2f}" if summary_skip_1.get('avg_first_success_step') is not None else "-",
                '최소 첫 성공 스텝': f"{summary_skip_1.get('min_first_success_step', 0)}" if summary_skip_1.get('min_first_success_step') is not None else "-",
                '최대 첫 성공 스텝': f"{summary_skip_1.get('max_first_success_step', 0)}" if summary_skip_1.get('max_first_success_step') is not None else "-",
                '성공이 있었던 Grid String 수': summary_skip_1.get('total_with_success', 0)
            }
            detail_df_1 = pd.DataFrame([detail_stats_1])
            st.dataframe(detail_df_1, use_container_width=True, hide_index=True)
            
            # 신뢰도 통계 표시
            st.markdown("---")
            st.markdown("### 신뢰도 구간별 통계 (신뢰도 스킵 전략)")
            
            conn = get_db_connection()
            if conn is not None:
                try:
                    stats_query_skip = """
                        SELECT confidence_range, total_predictions, matches, mismatches, 
                               match_rate, avg_confidence
                        FROM confidence_statistics
                        WHERE strategy_type = 'confidence_skip'
                        ORDER BY confidence_range
                    """
                    stats_df_skip = pd.read_sql_query(stats_query_skip, conn)
                    
                    if len(stats_df_skip) > 0:
                        st.dataframe(stats_df_skip, use_container_width=True, hide_index=True)
                    else:
                        st.info("💡 신뢰도 통계 데이터가 없습니다. 검증을 실행하면 통계가 수집됩니다.")
                except Exception as e:
                    st.warning(f"신뢰도 통계 조회 중 오류: {str(e)}")
                finally:
                    conn.close()
            
            # 최대 연속 실패 Grid String 히스토리 자동 표시 (검증용)
            st.markdown("---")
            st.markdown("### 🔍 최대 연속 실패 Grid String 검증 히스토리 (신뢰도 스킵 전략)")
            st.markdown("**의도대로 동작하는지 확인하기 위한 최대 연속 실패가 발생한 grid_string_id의 상세 히스토리**")
            
            results_skip_1 = batch_results_skip_1.get('results', [])
            if len(results_skip_1) > 0:
                # 최대 연속 실패가 발생한 grid_string_id 찾기
                max_failure_result = max(results_skip_1, key=lambda x: x.get('max_consecutive_failures', 0))
                max_failure_grid_id = max_failure_result['grid_string_id']
                max_failure_count = max_failure_result.get('max_consecutive_failures', 0)
                
                st.info(f"📌 **검증 대상**: Grid String ID {max_failure_grid_id} (최대 연속 실패: {max_failure_count}회)")
                
                # 전체 히스토리 보기 옵션
                show_full_history_skip_1 = st.checkbox(
                    "전체 히스토리 보기 (기본: 최근 50개만 표시)",
                    value=False,
                    key="last_grid_full_history_skip_1"
                )
                
                # 히스토리 가져오기
                failure_history_skip_1 = validate_interactive_multi_step_scenario_with_confidence_skip(
                    max_failure_grid_id,
                    cutoff_id_skip,
                    window_size=skip_window_size,
                    method=skip_method,
                    use_threshold=skip_use_threshold,
                    threshold=skip_threshold_val if skip_use_threshold else 60,
                    max_interval=skip_max_interval,
                    reverse_forced_prediction=False,
                    confidence_skip_threshold=skip_confidence_threshold_1
                )
                
                if failure_history_skip_1:
                    st.markdown("#### 요약 정보")
                    col_hist1, col_hist2, col_hist3, col_hist4, col_hist5, col_hist6 = st.columns(6)
                    with col_hist1:
                        st.metric("최대 연속 실패", f"{failure_history_skip_1['max_consecutive_failures']}회")
                    with col_hist2:
                        st.metric("총 스텝", f"{failure_history_skip_1['total_steps']}")
                    with col_hist3:
                        st.metric("총 예측", f"{failure_history_skip_1['total_predictions']}")
                    with col_hist4:
                        st.metric("총 스킵", f"{failure_history_skip_1.get('total_skipped_predictions', 0)}회")
                    with col_hist5:
                        st.metric("정확도", f"{failure_history_skip_1['accuracy']:.2f}%")
                    with col_hist6:
                        first_success_step = failure_history_skip_1.get('first_success_step')
                        if first_success_step is not None:
                            st.metric("첫 성공 스텝", f"{first_success_step}")
                        else:
                            st.metric("첫 성공 스텝", "-")
                    
                    # 히스토리 테이블 (최신순으로 표시)
                    st.markdown("#### 상세 히스토리")
                    history_skip_1 = failure_history_skip_1.get('history', [])
                    if len(history_skip_1) > 0:
                        # 히스토리를 최신순으로 정렬 (step 내림차순)
                        history_skip_sorted_1 = sorted(history_skip_1, key=lambda x: x.get('step', 0), reverse=True)
                        
                        history_limit_skip_1 = None if show_full_history_skip_1 else 50
                        history_title_skip_1 = "##### 상세 히스토리" + (f" (최신 {history_limit_skip_1}개)" if history_limit_skip_1 else " (전체)")
                        st.markdown(history_title_skip_1)
                        history_data_skip_1 = []
                        # 최신순으로 정렬된 히스토리에서 최신 N개 선택
                        display_history_skip_1 = history_skip_sorted_1[:history_limit_skip_1] if history_limit_skip_1 else history_skip_sorted_1
                        
                        for entry in display_history_skip_1:
                            is_correct = entry.get('is_correct')
                            match_status = '✅' if is_correct else ('❌' if is_correct is False else '-')
                            has_prediction = entry.get('has_prediction', False)
                            is_forced = entry.get('is_forced', False)
                            validated = entry.get('validated', False)
                            skipped = entry.get('skipped', False)
                            
                            forced_mark = '⚡' if is_forced else ''
                            no_pred_mark = '🚫' if not has_prediction else ''
                            validated_mark = '✓' if validated else ''
                            skipped_mark = '⏭️' if skipped else ''
                            
                            history_data_skip_1.append({
                                'Step': entry.get('step', 0),
                                'Prefix': entry.get('prefix', ''),
                                '예측': f"{entry.get('predicted', '-')}{forced_mark}{no_pred_mark}{skipped_mark}",
                                '실제값': entry.get('actual', '-'),
                                '일치': match_status,
                                '검증': validated_mark,
                                '스킵': '⏭️' if skipped else '',
                                '신뢰도': f"{entry.get('confidence', 0):.1f}" if has_prediction else '-',
                                '간격': entry.get('current_interval', 0) if not has_prediction else 0
                            })
                        
                        history_df_skip_1 = pd.DataFrame(history_data_skip_1)
                        st.dataframe(history_df_skip_1, use_container_width=True, hide_index=True)
                        
                        if not show_full_history_skip_1 and len(history_skip_1) > 50:
                            st.caption(f"💡 전체 {len(history_skip_1)}개 중 최신 50개만 표시됩니다. 전체 히스토리를 보려면 위의 체크박스를 선택하세요.")
                    
                    # 검증 포인트 안내
                    st.markdown("---")
                    st.markdown("#### 🔍 검증 포인트")
                    st.markdown("""
                    다음 사항들을 확인해주세요:
                    1. **스킵 규칙**: 강제 예측(`⚡`)이고 신뢰도가 51% 미만인 경우 `⏭️` 표시가 있는지 확인
                    2. **간격 계산**: 스킵된 스텝에서 간격이 증가하지 않는지 확인 (간격이 멈춰있는지)
                    3. **검증 수행**: `검증` 컬럼이 '✓'인 스텝에서만 실제 비교가 수행되는지 확인
                    4. **연속 실패 추적**: 연속으로 실패하는 경우가 올바르게 카운트되는지 확인
                    5. **다음 스텝 진행**: 스킵 후 다음 스텝의 prefix로 예측이 수행되는지 확인
                    """)
            else:
                st.info("💡 검증 결과가 없습니다.")
        
        # 두 번째 임계값 결과 표시
        if batch_results_skip_2 is None:
            pass  # 첫 번째 결과가 없으면 두 번째도 표시하지 않음
        elif len(batch_results_skip_2.get('results', [])) == 0:
            pass  # 결과가 없으면 표시하지 않음
        else:
            summary_skip_2 = batch_results_skip_2.get('summary', {})
            
            st.markdown("---")
            st.markdown(f"### 신뢰도 스킵 전략 검증 결과 (임계값 2: {skip_confidence_threshold_2}%)")
            
            col_skip_result2_1, col_skip_result2_2, col_skip_result2_3, col_skip_result2_4, col_skip_result2_5, col_skip_result2_6 = st.columns(6)
            with col_skip_result2_1:
                st.metric("평균 정확도", f"{summary_skip_2.get('avg_accuracy', 0):.2f}%")
            with col_skip_result2_2:
                st.metric("최대 연속 실패", f"{summary_skip_2.get('max_consecutive_failures', 0)}회")
            with col_skip_result2_3:
                st.metric("총 스킵 횟수", f"{summary_skip_2.get('total_skipped_predictions', 0)}회")
            with col_skip_result2_4:
                st.metric("예측률", f"{summary_skip_2.get('prediction_rate', 0):.2f}%")
            with col_skip_result2_5:
                first_success_2 = summary_skip_2.get('avg_first_success_step')
                if first_success_2 is not None:
                    st.metric("평균 첫 성공 스텝", f"{first_success_2:.1f}")
                else:
                    st.metric("평균 첫 성공 스텝", "-")
            with col_skip_result2_6:
                max_first_success_2 = summary_skip_2.get('max_first_success_step')
                if max_first_success_2 is not None and max_first_success_2 > 0:
                    st.metric("최대 첫 성공 스텝", f"{max_first_success_2}")
                else:
                    st.metric("최대 첫 성공 스텝", "-")
            
            # 추가 통계 표시
            st.markdown("---")
            st.markdown("#### 상세 통계")
            detail_stats_2 = {
                '총 Grid String 수': summary_skip_2.get('total_grid_strings', 0),
                '총 스텝 수': summary_skip_2.get('total_steps', 0),
                '총 예측 횟수': summary_skip_2.get('total_predictions', 0),
                '총 실패 횟수': summary_skip_2.get('total_failures', 0),
                '총 강제 예측 횟수': summary_skip_2.get('total_forced_predictions', 0),
                '평균 최대 연속 실패': f"{summary_skip_2.get('avg_max_consecutive_failures', 0):.2f}",
                '강제 예측 비율': f"{summary_skip_2.get('forced_prediction_rate', 0):.2f}%",
                '강제 예측 성공 비율': f"{summary_skip_2.get('forced_success_rate', 0):.2f}%",
                '평균 첫 성공 스텝': f"{summary_skip_2.get('avg_first_success_step', 0):.2f}" if summary_skip_2.get('avg_first_success_step') is not None else "-",
                '최소 첫 성공 스텝': f"{summary_skip_2.get('min_first_success_step', 0)}" if summary_skip_2.get('min_first_success_step') is not None else "-",
                '최대 첫 성공 스텝': f"{summary_skip_2.get('max_first_success_step', 0)}" if summary_skip_2.get('max_first_success_step') is not None else "-",
                '성공이 있었던 Grid String 수': summary_skip_2.get('total_with_success', 0)
            }
            detail_df_2 = pd.DataFrame([detail_stats_2])
            st.dataframe(detail_df_2, use_container_width=True, hide_index=True)
            
            # 최대 연속 실패 Grid String 히스토리 자동 표시 (검증용)
            st.markdown("---")
            st.markdown(f"### 🔍 최대 연속 실패 Grid String 검증 히스토리 (임계값 2: {skip_confidence_threshold_2}%)")
            st.markdown("**의도대로 동작하는지 확인하기 위한 최대 연속 실패가 발생한 grid_string_id의 상세 히스토리**")
            
            results_skip_2 = batch_results_skip_2.get('results', [])
            if len(results_skip_2) > 0:
                # 최대 연속 실패가 발생한 grid_string_id 찾기
                max_failure_result_2 = max(results_skip_2, key=lambda x: x.get('max_consecutive_failures', 0))
                max_failure_grid_id_2 = max_failure_result_2['grid_string_id']
                max_failure_count_2 = max_failure_result_2.get('max_consecutive_failures', 0)
                
                st.info(f"📌 **검증 대상**: Grid String ID {max_failure_grid_id_2} (최대 연속 실패: {max_failure_count_2}회)")
                
                # 전체 히스토리 보기 옵션
                show_full_history_skip_2 = st.checkbox(
                    "전체 히스토리 보기 (기본: 최근 50개만 표시)",
                    value=False,
                    key="last_grid_full_history_skip_2"
                )
                
                # 히스토리 가져오기
                failure_history_skip_2 = validate_interactive_multi_step_scenario_with_confidence_skip(
                    max_failure_grid_id_2,
                    cutoff_id_skip,
                    window_size=skip_window_size,
                    method=skip_method,
                    use_threshold=skip_use_threshold,
                    threshold=skip_threshold_val if skip_use_threshold else 60,
                    max_interval=skip_max_interval,
                    reverse_forced_prediction=False,
                    confidence_skip_threshold=skip_confidence_threshold_2
                )
                
                if failure_history_skip_2:
                    st.markdown("#### 요약 정보")
                    col_hist2_1, col_hist2_2, col_hist2_3, col_hist2_4, col_hist2_5, col_hist2_6 = st.columns(6)
                    with col_hist2_1:
                        st.metric("최대 연속 실패", f"{failure_history_skip_2['max_consecutive_failures']}회")
                    with col_hist2_2:
                        st.metric("총 스텝", f"{failure_history_skip_2['total_steps']}")
                    with col_hist2_3:
                        st.metric("총 예측", f"{failure_history_skip_2['total_predictions']}")
                    with col_hist2_4:
                        st.metric("총 스킵", f"{failure_history_skip_2.get('total_skipped_predictions', 0)}회")
                    with col_hist2_5:
                        st.metric("정확도", f"{failure_history_skip_2['accuracy']:.2f}%")
                    with col_hist2_6:
                        first_success_step_2 = failure_history_skip_2.get('first_success_step')
                        if first_success_step_2 is not None:
                            st.metric("첫 성공 스텝", f"{first_success_step_2}")
                        else:
                            st.metric("첫 성공 스텝", "-")
                    
                    # 히스토리 테이블 (최신순으로 표시)
                    st.markdown("#### 상세 히스토리")
                    history_skip_2 = failure_history_skip_2.get('history', [])
                    if len(history_skip_2) > 0:
                        # 히스토리를 최신순으로 정렬 (step 내림차순)
                        history_skip_sorted_2 = sorted(history_skip_2, key=lambda x: x.get('step', 0), reverse=True)
                        
                        history_limit_skip_2 = None if show_full_history_skip_2 else 50
                        history_title_skip_2 = "##### 상세 히스토리" + (f" (최신 {history_limit_skip_2}개)" if history_limit_skip_2 else " (전체)")
                        st.markdown(history_title_skip_2)
                        history_data_skip_2 = []
                        # 최신순으로 정렬된 히스토리에서 최신 N개 선택
                        display_history_skip_2 = history_skip_sorted_2[:history_limit_skip_2] if history_limit_skip_2 else history_skip_sorted_2
                        
                        for entry in display_history_skip_2:
                            is_correct = entry.get('is_correct')
                            match_status = '✅' if is_correct else ('❌' if is_correct is False else '-')
                            has_prediction = entry.get('has_prediction', False)
                            is_forced = entry.get('is_forced', False)
                            validated = entry.get('validated', False)
                            skipped = entry.get('skipped', False)
                            
                            forced_mark = '⚡' if is_forced else ''
                            no_pred_mark = '🚫' if not has_prediction else ''
                            validated_mark = '✓' if validated else ''
                            skipped_mark = '⏭️' if skipped else ''
                            
                            history_data_skip_2.append({
                                'Step': entry.get('step', 0),
                                'Prefix': entry.get('prefix', ''),
                                '예측': f"{entry.get('predicted', '-')}{forced_mark}{no_pred_mark}{skipped_mark}",
                                '실제값': entry.get('actual', '-'),
                                '일치': match_status,
                                '검증': validated_mark,
                                '스킵': '⏭️' if skipped else '',
                                '신뢰도': f"{entry.get('confidence', 0):.1f}" if has_prediction else '-',
                                '간격': entry.get('current_interval', 0) if not has_prediction else 0
                            })
                        
                        history_df_skip_2 = pd.DataFrame(history_data_skip_2)
                        st.dataframe(history_df_skip_2, use_container_width=True, hide_index=True)
                        
                        if not show_full_history_skip_2 and len(history_skip_2) > 50:
                            st.caption(f"💡 전체 {len(history_skip_2)}개 중 최신 50개만 표시됩니다. 전체 히스토리를 보려면 위의 체크박스를 선택하세요.")
        
        # 히스토리 저장 상태 확인 섹션 추가
        st.markdown("---")
        st.markdown("### 📋 히스토리 저장 상태 확인")
        
        # 현재 세션의 히스토리 데이터 확인
        if batch_results_skip_1 and batch_results_skip_2:
            col_check1, col_check2 = st.columns(2)
            
            with col_check1:
                if 'results' in batch_results_skip_1 and len(batch_results_skip_1['results']) > 0:
                    total_history_1 = sum(len(r.get('history', [])) for r in batch_results_skip_1['results'])
                    grid_with_history_1 = sum(1 for r in batch_results_skip_1['results'] if r.get('history'))
                    st.metric("임계값 1 - 히스토리 있는 Grid String", f"{grid_with_history_1}/{len(batch_results_skip_1['results'])}")
                    st.metric("임계값 1 - 총 히스토리 스텝 수", f"{total_history_1}")
                    
                    if grid_with_history_1 < len(batch_results_skip_1['results']):
                        st.warning(f"⚠️ {len(batch_results_skip_1['results']) - grid_with_history_1}개의 Grid String에 히스토리가 없습니다.")
            
            with col_check2:
                if 'results' in batch_results_skip_2 and len(batch_results_skip_2['results']) > 0:
                    total_history_2 = sum(len(r.get('history', [])) for r in batch_results_skip_2['results'])
                    grid_with_history_2 = sum(1 for r in batch_results_skip_2['results'] if r.get('history'))
                    st.metric("임계값 2 - 히스토리 있는 Grid String", f"{grid_with_history_2}/{len(batch_results_skip_2['results'])}")
                    st.metric("임계값 2 - 총 히스토리 스텝 수", f"{total_history_2}")
                    
                    if grid_with_history_2 < len(batch_results_skip_2['results']):
                        st.warning(f"⚠️ {len(batch_results_skip_2['results']) - grid_with_history_2}개의 Grid String에 히스토리가 없습니다.")
            
            # 저장된 validation_id 확인 기능
            st.markdown("#### 저장된 검증 세션 확인")
            validation_id_input = st.text_input(
                "확인할 Validation ID 입력 (또는 전체 목록 조회)",
                key="check_validation_id",
                placeholder="예: f81bfed8 또는 빈칸"
            )
            
            if validation_id_input:
                # 특정 validation_id 확인
                conn = get_db_connection()
                if conn is not None:
                    try:
                        cursor = conn.cursor()
                        # validation_id로 시작하는 세션들 찾기
                        cursor.execute('''
                            SELECT validation_id, created_at,
                                   (SELECT COUNT(*) FROM confidence_skip_validation_grid_results 
                                    WHERE validation_id = s.validation_id) as grid_count,
                                   (SELECT COUNT(*) FROM confidence_skip_validation_steps 
                                    WHERE validation_id = s.validation_id) as step_count
                            FROM confidence_skip_validation_sessions s
                            WHERE validation_id LIKE ?
                            ORDER BY created_at DESC
                        ''', (f"{validation_id_input}%",))
                        
                        matching_sessions = cursor.fetchall()
                        
                        if len(matching_sessions) > 0:
                            st.markdown("##### 일치하는 세션 목록")
                            session_info = []
                            for row in matching_sessions:
                                session_info.append({
                                    'Validation ID': row[0],
                                    '생성일': row[1],
                                    'Grid String 수': row[2],
                                    '저장된 스텝 수': row[3]
                                })
                            session_df = pd.DataFrame(session_info)
                            st.dataframe(session_df, use_container_width=True, hide_index=True)
                        else:
                            st.info(f"💡 '{validation_id_input}'로 시작하는 검증 세션을 찾을 수 없습니다.")
                    except Exception as e:
                        st.error(f"조회 중 오류: {str(e)}")
                    finally:
                        conn.close()
            else:
                # 전체 세션 목록 조회 (최근 10개)
                conn = get_db_connection()
                if conn is not None:
                    try:
                        cursor = conn.cursor()
                        cursor.execute('''
                            SELECT s.validation_id, s.created_at,
                                   (SELECT COUNT(DISTINCT grid_string_id) FROM confidence_skip_validation_grid_results 
                                    WHERE validation_id = s.validation_id) as grid_count,
                                   (SELECT COUNT(*) FROM confidence_skip_validation_steps 
                                    WHERE validation_id = s.validation_id) as step_count
                            FROM confidence_skip_validation_sessions s
                            ORDER BY s.created_at DESC
                            LIMIT 10
                        ''')
                        
                        all_sessions = cursor.fetchall()
                        
                        if len(all_sessions) > 0:
                            st.markdown("##### 최근 검증 세션 목록 (최근 10개)")
                            session_info = []
                            for row in all_sessions:
                                session_info.append({
                                    'Validation ID': row[0][:12] + '...',
                                    '전체 ID': row[0],
                                    '생성일': row[1],
                                    'Grid String 수': row[2],
                                    '저장된 스텝 수': row[3]
                                })
                            session_df = pd.DataFrame(session_info)
                            st.dataframe(session_df, use_container_width=True, hide_index=True)
                            
                            # 스텝이 없는 세션 강조
                            no_steps_sessions = [s for s in all_sessions if s[3] == 0]
                            if len(no_steps_sessions) > 0:
                                st.warning(f"⚠️ {len(no_steps_sessions)}개의 세션에 저장된 스텝이 없습니다.")
                        else:
                            st.info("💡 저장된 검증 세션이 없습니다.")
                    except Exception as e:
                        st.error(f"조회 중 오류: {str(e)}")
                    finally:
                        conn.close()
        
        # 비교 테이블 (화면 가장 하단에 추가)
        if (batch_results_skip_1 is not None and len(batch_results_skip_1.get('results', [])) > 0 and
            batch_results_skip_2 is not None and len(batch_results_skip_2.get('results', [])) > 0):
            st.markdown("---")
            st.markdown("### 📊 임계값 비교 테이블")
            
            summary_skip_1 = batch_results_skip_1.get('summary', {})
            summary_skip_2 = batch_results_skip_2.get('summary', {})
            
            # 비교 테이블
            comparison_data = []
            comparison_data.append({
                '항목': '스킵 신뢰도 임계값',
                f'임계값 {skip_confidence_threshold_1:.1f}%': f"{skip_confidence_threshold_1:.1f}%",
                f'임계값 {skip_confidence_threshold_2:.1f}%': f"{skip_confidence_threshold_2:.1f}%",
                '차이': f"{skip_confidence_threshold_2 - skip_confidence_threshold_1:+.1f}%"
            })
            comparison_data.append({
                '항목': '평균 정확도 (%)',
                f'임계값 {skip_confidence_threshold_1}%': f"{summary_skip_1.get('avg_accuracy', 0):.2f}",
                f'임계값 {skip_confidence_threshold_2}%': f"{summary_skip_2.get('avg_accuracy', 0):.2f}",
                '차이': f"{summary_skip_2.get('avg_accuracy', 0) - summary_skip_1.get('avg_accuracy', 0):+.2f}"
            })
            comparison_data.append({
                '항목': '최대 연속 실패',
                f'임계값 {skip_confidence_threshold_1}%': f"{summary_skip_1.get('max_consecutive_failures', 0)}",
                f'임계값 {skip_confidence_threshold_2}%': f"{summary_skip_2.get('max_consecutive_failures', 0)}",
                '차이': f"{summary_skip_2.get('max_consecutive_failures', 0) - summary_skip_1.get('max_consecutive_failures', 0):+d}"
            })
            comparison_data.append({
                '항목': '평균 최대 연속 실패',
                f'임계값 {skip_confidence_threshold_1}%': f"{summary_skip_1.get('avg_max_consecutive_failures', 0):.2f}",
                f'임계값 {skip_confidence_threshold_2}%': f"{summary_skip_2.get('avg_max_consecutive_failures', 0):.2f}",
                '차이': f"{summary_skip_2.get('avg_max_consecutive_failures', 0) - summary_skip_1.get('avg_max_consecutive_failures', 0):+.2f}"
            })
            comparison_data.append({
                '항목': '총 스킵 횟수',
                f'임계값 {skip_confidence_threshold_1}%': f"{summary_skip_1.get('total_skipped_predictions', 0)}",
                f'임계값 {skip_confidence_threshold_2}%': f"{summary_skip_2.get('total_skipped_predictions', 0)}",
                '차이': f"{summary_skip_2.get('total_skipped_predictions', 0) - summary_skip_1.get('total_skipped_predictions', 0):+d}"
            })
            comparison_data.append({
                '항목': '예측률 (%)',
                f'임계값 {skip_confidence_threshold_1}%': f"{summary_skip_1.get('prediction_rate', 0):.2f}",
                f'임계값 {skip_confidence_threshold_2}%': f"{summary_skip_2.get('prediction_rate', 0):.2f}",
                '차이': f"{summary_skip_2.get('prediction_rate', 0) - summary_skip_1.get('prediction_rate', 0):+.2f}"
            })
            comparison_data.append({
                '항목': '평균 첫 성공 스텝',
                f'임계값 {skip_confidence_threshold_1}%': f"{summary_skip_1.get('avg_first_success_step', 0):.2f}" if summary_skip_1.get('avg_first_success_step') is not None else "-",
                f'임계값 {skip_confidence_threshold_2}%': f"{summary_skip_2.get('avg_first_success_step', 0):.2f}" if summary_skip_2.get('avg_first_success_step') is not None else "-",
                '차이': f"{(summary_skip_2.get('avg_first_success_step', 0) - summary_skip_1.get('avg_first_success_step', 0)):+.2f}" if (summary_skip_1.get('avg_first_success_step') is not None and summary_skip_2.get('avg_first_success_step') is not None) else "-"
            })
            comparison_data.append({
                '항목': '성공이 있었던 Grid String 수',
                f'임계값 {skip_confidence_threshold_1}%': f"{summary_skip_1.get('total_with_success', 0)}",
                f'임계값 {skip_confidence_threshold_2}%': f"{summary_skip_2.get('total_with_success', 0)}",
                '차이': f"{summary_skip_2.get('total_with_success', 0) - summary_skip_1.get('total_with_success', 0):+d}"
            })
            
            comparison_df = pd.DataFrame(comparison_data)
            st.dataframe(comparison_df, use_container_width=True, hide_index=True)
            
            # 검증 결과 저장 버튼
            st.markdown("---")
            col_save1, col_save2 = st.columns([1, 4])
            with col_save1:
                if st.button("💾 검증 결과 저장", type="primary", use_container_width=True, key="save_confidence_skip_results"):
                    # 저장 전 히스토리 확인
                    has_history_1 = False
                    has_history_2 = False
                    total_steps_count_1 = 0
                    total_steps_count_2 = 0
                    
                    if batch_results_skip_1 and 'results' in batch_results_skip_1:
                        for result in batch_results_skip_1['results']:
                            if 'history' in result and result['history']:
                                has_history_1 = True
                                total_steps_count_1 += len(result['history'])
                    
                    if batch_results_skip_2 and 'results' in batch_results_skip_2:
                        for result in batch_results_skip_2['results']:
                            if 'history' in result and result['history']:
                                has_history_2 = True
                                total_steps_count_2 += len(result['history'])
                    
                    if not has_history_1 or not has_history_2:
                        st.warning(f"⚠️ 히스토리 데이터 확인: 임계값1={has_history_1} ({total_steps_count_1}개), 임계값2={has_history_2} ({total_steps_count_2}개)")
                    
                    validation_id = save_confidence_skip_validation_results(
                        cutoff_id_skip,
                        skip_window_size,
                        skip_method,
                        skip_use_threshold,
                        skip_threshold_val if skip_use_threshold else None,
                        skip_max_interval,
                        skip_confidence_threshold_1,
                        skip_confidence_threshold_2,
                        batch_results_skip_1,
                        batch_results_skip_2,
                        grid_string_ids=batch_results_skip_1.get('grid_string_ids') if batch_results_skip_1 else None
                    )
                    
                    if validation_id:
                        st.session_state.confidence_skip_saved_validation_id = validation_id
                        
                        # 저장 후 실제 저장된 스텝 수 확인
                        conn = get_db_connection()
                        if conn is not None:
                            try:
                                cursor = conn.cursor()
                                cursor.execute('''
                                    SELECT COUNT(*) as count 
                                    FROM confidence_skip_validation_steps 
                                    WHERE validation_id = ?
                                ''', (validation_id,))
                                saved_steps_count = cursor.fetchone()[0]
                                
                                st.success(f"✅ 검증 결과가 저장되었습니다. (ID: {validation_id[:8]}..., 저장된 스텝: {saved_steps_count}개)")
                            except:
                                st.success(f"✅ 검증 결과가 저장되었습니다. (ID: {validation_id[:8]}...)")
                            finally:
                                conn.close()
                        else:
                            st.success(f"✅ 검증 결과가 저장되었습니다. (ID: {validation_id[:8]}...)")
                    else:
                        st.warning("⚠️ 검증 결과 저장에 실패했습니다.")
            
            with col_save2:
                if 'confidence_skip_saved_validation_id' in st.session_state:
                    saved_id = st.session_state.confidence_skip_saved_validation_id
                    
                    # 저장된 스텝 수 확인
                    conn = get_db_connection()
                    if conn is not None:
                        try:
                            cursor = conn.cursor()
                            cursor.execute('''
                                SELECT 
                                    COUNT(*) as total_steps,
                                    COUNT(DISTINCT grid_string_id) as grid_strings
                                FROM confidence_skip_validation_steps 
                                WHERE validation_id = ?
                            ''', (saved_id,))
                            row = cursor.fetchone()
                            total_steps = row[0] if row else 0
                            grid_strings = row[1] if row else 0
                            
                            st.info(f"💾 마지막 저장 ID: {saved_id[:8]}... | 저장된 스텝: {total_steps}개 | Grid String: {grid_strings}개")
                        except:
                            st.info(f"💾 마지막 저장 ID: {saved_id[:8]}...")
                        finally:
                            conn.close()
                    else:
                        st.info(f"💾 마지막 저장 ID: {saved_id[:8]}...")
    
    # 라이브 게임 섹션 (화면에서 숨김 처리)
    # ============================================
    # 아래 라이브 게임 섹션은 if False로 숨김 처리되어 화면에 표시되지 않습니다.
    # 필요시 아래 조건문의 False를 True로 변경하여 활성화할 수 있습니다.
    # ============================================
    if False:  # 라이브 게임 섹션 숨김 처리
        st.markdown("---")
        st.header("🎮 신뢰도 스킵 전략 라이브 게임")
        st.markdown("**스텝별로 예측값을 확인하고 실제값을 입력하여 검증하는 라이브 게임**")
        
        # 게임 설정 초기화
        if 'live_game_settings' not in st.session_state:
            st.session_state.live_game_settings = None
        
        # 게임 설정
        with st.expander("⚙️ 게임 설정", expanded=True):
            st.markdown("### 설정값")
            
            col_game1, col_game2 = st.columns(2)
        
        with col_game1:
            live_window_size = st.selectbox(
                "윈도우 크기",
                options=[5, 6, 7, 8, 9],
                index=0,
                key="live_game_window_size"
            )
            
            live_method = st.selectbox(
                "예측 방법",
                options=["빈도 기반", "가중치 기반", "안전 우선"],
                index=0,
                key="live_game_method"
            )
        
        with col_game2:
            live_use_threshold = st.checkbox(
                "임계값 전략 사용",
                value=True,
                key="live_game_use_threshold"
            )
            
            live_threshold = st.number_input(
                "임계값 (%)",
                min_value=0,
                max_value=100,
                value=56,
                step=1,
                key="live_game_threshold",
                disabled=not live_use_threshold
            )
            
            live_max_interval = st.number_input(
                "최대 간격",
                min_value=1,
                max_value=20,
                value=5,
                step=1,
                key="live_game_max_interval"
            )
            
            live_confidence_skip_threshold = st.number_input(
                "신뢰도 스킵 임계값 (%)",
                min_value=0,
                max_value=100,
                value=51,
                step=1,
                key="live_game_confidence_skip_threshold"
            )
        
        # 설정 저장 버튼
        col_save1, col_save2 = st.columns([1, 4])
        with col_save1:
                if st.button("💾 설정 저장", type="primary", use_container_width=True):
                    st.session_state.live_game_settings = {
                        'window_size': live_window_size,
                        'method': live_method,
                        'use_threshold': live_use_threshold,
                        'threshold': live_threshold,
                        'max_interval': live_max_interval,
                        'confidence_skip_threshold': live_confidence_skip_threshold
                    }
                    # st.success 제거 (성능 개선)
                    st.rerun()
        
        with col_save2:
            pass  # 메시지 제거
        
        # Grid String 입력 섹션
        st.markdown("---")
        st.markdown("### Grid String 입력")
        live_grid_string = st.text_area(
        "Grid String",
        value="",
        height=80,
        key="live_game_grid_string",
            help="라이브 게임에서 사용할 grid_string을 입력하세요. 기존 데이터는 모두 학습 데이터로 사용됩니다.",
            disabled=st.session_state.live_game_settings is None
        )
        
        if st.session_state.live_game_settings is None:
            st.warning("⚠️ 먼저 게임 설정을 저장해주세요.")
        
        # 게임 초기화
        if 'live_game_state' not in st.session_state:
            st.session_state.live_game_state = None
        
        # 게임 시작/재시작 버튼
        col_start1, col_start2 = st.columns([1, 4])
        with col_start1:
            # 설정이 저장되어 있고 grid string이 입력되어 있는지 확인 (최적화: 단순 체크만)
            settings_saved = st.session_state.live_game_settings is not None
            grid_string_entered = bool(live_grid_string and live_grid_string.strip())
            
            if st.button("🎮 게임 시작", type="primary", use_container_width=True, disabled=not settings_saved or not grid_string_entered):
                if not settings_saved:
                    st.error("게임 설정을 먼저 저장해주세요.")
                elif not grid_string_entered:
                    st.error("Grid String을 입력해주세요.")
                else:
                    grid_string = live_grid_string.strip()
                    settings = st.session_state.live_game_settings
                    
                    if len(grid_string) < settings['window_size']:
                        st.error(f"Grid String이 너무 짧습니다. (길이: {len(grid_string)}, 최소 필요: {settings['window_size']})")
                    else:
                        # 게임 초기화
                        conn = get_db_connection()
                        if conn is None:
                            st.error("데이터베이스 연결 실패")
                        else:
                            try:
                                # 모든 기존 데이터를 학습 데이터로 사용 (캐싱 확인)
                                model_cache_key = f"live_game_model_{settings['window_size']}_{settings['method']}"
                                
                                if model_cache_key in st.session_state:
                                    # 캐시된 모델 재사용
                                    model = st.session_state[model_cache_key]
                                else:
                                    # 모델 구축
                                    train_ids_query = "SELECT id FROM preprocessed_grid_strings ORDER BY id"
                                    train_ids_df = pd.read_sql_query(train_ids_query, conn)
                                    train_ids = train_ids_df['id'].tolist() if len(train_ids_df) > 0 else []
                                    
                                    # N-gram 로드
                                    train_ngrams = load_ngram_chunks(window_size=settings['window_size'], grid_string_ids=train_ids)
                                    
                                    if len(train_ngrams) == 0:
                                        st.warning("⚠️ 학습 데이터가 없습니다. 빈 모델로 시작합니다.")
                                        train_ngrams = []
                                    
                                    # 모델 구축
                                    if settings['method'] == "빈도 기반":
                                        model = build_frequency_model(train_ngrams)
                                    elif settings['method'] == "가중치 기반":
                                        model = build_weighted_model(train_ngrams)
                                    else:
                                        model = build_frequency_model(train_ngrams)
                                    
                                    # 모델 캐싱
                                    st.session_state[model_cache_key] = model
                                
                                # 게임 상태 초기화
                                prefix_length = settings['window_size'] - 1
                                initial_prefix = grid_string[:prefix_length]
                                
                                # 입력된 grid_string 길이만큼 자동 실행
                                history = []
                                consecutive_failures = 0
                                max_consecutive_failures = 0
                                total_predictions = 0
                                total_failures = 0
                                total_forced_predictions = 0
                                total_skipped_predictions = 0
                                current_interval = 0
                                current_index = prefix_length
                                current_prefix = initial_prefix
                                current_step = 0
                                
                                # grid_string의 마지막까지 자동 실행
                                while current_index < len(grid_string):
                                        # 예측 수행
                                        if settings['use_threshold']:
                                            prediction_result = predict_with_fallback_interval(
                                                model,
                                                current_prefix,
                                                method=settings['method'],
                                                threshold=settings['threshold'],
                                                max_interval=settings['max_interval'],
                                                current_interval=current_interval
                                            )
                                        else:
                                            prediction_result = predict_for_prefix(model, current_prefix, settings['method'])
                                            if 'is_forced' not in prediction_result:
                                                prediction_result['is_forced'] = False
                                        
                                        predicted_value = prediction_result.get('predicted')
                                        confidence = prediction_result.get('confidence', 0.0)
                                        is_forced = prediction_result.get('is_forced', False)
                                        has_prediction = predicted_value is not None
                                        
                                        # 스킵 규칙 체크
                                        should_skip = False
                                        if settings['use_threshold'] and has_prediction and is_forced and confidence < settings['confidence_skip_threshold']:
                                            should_skip = True
                                            total_skipped_predictions += 1
                                        
                                        # 실제값 가져오기 (grid_string에서)
                                        actual_value = grid_string[current_index]
                                        
                                        # 검증 수행 (예측값이 있고 스킵하지 않는 경우)
                                        if has_prediction and not should_skip:
                                            is_correct = predicted_value == actual_value
                                            
                                            if not is_correct:
                                                consecutive_failures += 1
                                                total_failures += 1
                                                if consecutive_failures > max_consecutive_failures:
                                                    max_consecutive_failures = consecutive_failures
                                            else:
                                                consecutive_failures = 0
                                            
                                            total_predictions += 1
                                            if is_forced:
                                                total_forced_predictions += 1
                                            
                                            # 간격 리셋
                                            current_interval = 0
                                            
                                            # 히스토리 기록
                                            history.append({
                                                'step': current_step + 1,
                                                'prefix': current_prefix,
                                                'predicted': predicted_value,
                                                'actual': actual_value,
                                                'is_correct': is_correct,
                                                'confidence': confidence,
                                                'is_forced': is_forced,
                                                'current_interval': 0,
                                                'has_prediction': True,
                                                'validated': True,
                                                'skipped': False
                                            })
                                        elif has_prediction and should_skip:
                                            # 스킵된 경우 히스토리 기록
                                            history.append({
                                                'step': current_step + 1,
                                                'prefix': current_prefix,
                                                'predicted': predicted_value,
                                                'actual': actual_value,
                                                'is_correct': None,
                                                'confidence': confidence,
                                                'is_forced': is_forced,
                                                'current_interval': current_interval,
                                                'has_prediction': True,
                                                'validated': False,
                                                'skipped': True
                                            })
                                            # 스킵 상태에서 간격 계산은 멈춤 (증가하지 않음)
                                        else:
                                            # 예측값이 없는 경우 히스토리 기록 (전체 스텝 표시를 위해)
                                            history.append({
                                                'step': current_step + 1,
                                                'prefix': current_prefix,
                                                'predicted': None,
                                                'actual': actual_value,
                                                'is_correct': None,
                                                'confidence': 0.0,
                                                'is_forced': False,
                                                'current_interval': current_interval,
                                                'has_prediction': False,
                                                'validated': False,
                                                'skipped': False
                                            })
                                            # 예측값이 없는 경우 간격 증가
                                            if settings['use_threshold']:
                                                current_interval += 1
                                        
                                        # 다음 스텝으로 진행
                                        current_step += 1
                                        current_index += 1
                                        
                                        # prefix 업데이트 (인터랙티브 모드 전환을 위해 항상 업데이트)
                                        current_prefix = get_next_prefix(
                                            current_prefix,
                                            actual_value,
                                            settings['window_size']
                                        )
                                
                                # 게임 상태 저장 (다음 스텝부터 인터랙티브로 진행)
                                st.session_state.live_game_state = {
                                    'grid_string': grid_string,
                                    'model': model,
                                    'current_step': current_step,
                                    'current_index': current_index,
                                    'current_prefix': current_prefix,
                                    'current_interval': current_interval,
                                    'history': history,
                                    'consecutive_failures': consecutive_failures,
                                    'max_consecutive_failures': max_consecutive_failures,
                                    'total_predictions': total_predictions,
                                    'total_failures': total_failures,
                                    'total_forced_predictions': total_forced_predictions,
                                    'total_skipped_predictions': total_skipped_predictions,
                                    'window_size': settings['window_size'],
                                    'method': settings['method'],
                                    'use_threshold': settings['use_threshold'],
                                    'threshold': settings['threshold'],
                                    'max_interval': settings['max_interval'],
                                    'confidence_skip_threshold': settings['confidence_skip_threshold'],
                                    'auto_executed': True  # 자동 실행 완료 플래그
                                }
                                
                                # st.success 제거 (성능 개선)
                                st.rerun()
                            except Exception as e:
                                st.error(f"게임 초기화 중 오류: {str(e)}")
                                import traceback
                                st.error(f"상세 오류: {traceback.format_exc()}")
                            finally:
                                conn.close()
        
        with col_start2:
            if st.session_state.live_game_state is not None:
                if st.button("🔄 게임 재시작", use_container_width=True):
                    st.session_state.live_game_state = None
                    st.rerun()
        
            # 게임 진행 (게임 상태가 있을 때만 함수 호출 - 성능 개선)
            if st.session_state.live_game_state is not None:
                render_live_game_play(st.session_state.live_game_state)

if __name__ == "__main__":
    main()

