"""
reconstructed_grid 가설 검증 앱
N-gram 기반 패턴 예측 가설 검증 시스템
"""

import streamlit as st
import sqlite3
import pandas as pd
import os
import json
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from bs4 import BeautifulSoup

# SVG 파싱 모듈 import
from svg_parser_module import (
    parse_bead_road_svg,
    grid_to_string_column_wise,
    save_parsed_grid_string_to_db,
    generate_and_save_ngram_chunks,
    create_ngram_chunks_table,
    TABLE_WIDTH,
    TABLE_HEIGHT
)

# 페이지 설정 (직접 실행될 때만 설정)
# 다른 모듈에서 import될 때는 이미 설정되어 있을 수 있으므로 try-except로 처리
try:
    st.set_page_config(
        page_title="Hypothesis Validation System",
        page_icon="🔬",
        layout="wide"
    )
except st.errors.StreamlitAPIException:
    # 이미 설정되었거나 다른 앱에서 먼저 설정한 경우 무시
    pass

# DB 경로
DB_PATH = 'hypothesis_validation.db'

# Table dimensions (모듈에서 import하므로 주석 처리)
# TABLE_WIDTH = 15
# TABLE_HEIGHT = 6

# SVG 파싱 함수는 svg_parser_module에서 import됨

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

def create_scenario_validation_tables():
    """시나리오 검증 결과 저장을 위한 테이블 생성"""
    conn = get_db_connection()
    if conn is None:
        return False
    
    cursor = conn.cursor()
    
    try:
        # 테이블 1: scenario_validation_sessions (검증 세션 요약)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scenario_validation_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT NOT NULL UNIQUE,
                grid_string TEXT NOT NULL,
                grid_string_hash TEXT,
                string_length INTEGER NOT NULL,
                b_count INTEGER NOT NULL,
                p_count INTEGER NOT NULL,
                b_ratio REAL,
                p_ratio REAL,
                window_size INTEGER NOT NULL,
                prediction_method TEXT NOT NULL,
                train_ratio REAL,
                result TEXT NOT NULL,
                max_consecutive_mismatches INTEGER NOT NULL,
                consecutive_5_count INTEGER NOT NULL,
                total_steps INTEGER NOT NULL,
                matches INTEGER NOT NULL,
                mismatches INTEGER NOT NULL,
                match_rate REAL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours'))
            )
        ''')
        
        # 인덱스 생성
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_result 
            ON scenario_validation_sessions(result)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_window_method 
            ON scenario_validation_sessions(window_size, prediction_method)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_created_at 
            ON scenario_validation_sessions(created_at)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_max_consecutive 
            ON scenario_validation_sessions(max_consecutive_mismatches)
        ''')
        
        # 테이블 2: scenario_validation_steps (각 스텝 상세)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scenario_validation_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT NOT NULL,
                step_number INTEGER NOT NULL,
                step_index INTEGER NOT NULL,
                prefix TEXT NOT NULL,
                predicted_value TEXT NOT NULL,
                actual_value TEXT NOT NULL,
                is_match INTEGER NOT NULL,
                confidence REAL,
                predicted_ratio REAL,
                actual_ratio REAL,
                consecutive_mismatches INTEGER NOT NULL,
                FOREIGN KEY (validation_id) REFERENCES scenario_validation_sessions(validation_id)
            )
        ''')
        
        # 인덱스 생성
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_validation_id 
            ON scenario_validation_steps(validation_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_step_number 
            ON scenario_validation_steps(validation_id, step_number)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_is_match 
            ON scenario_validation_steps(validation_id, is_match)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_consecutive 
            ON scenario_validation_steps(validation_id, consecutive_mismatches)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_prefix 
            ON scenario_validation_steps(prefix)
        ''')
        
        # 테이블 3: scenario_consecutive_5_occurrences (연속 불일치 5개 발생 위치)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scenario_consecutive_5_occurrences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT NOT NULL,
                occurrence_index INTEGER NOT NULL,
                start_step INTEGER NOT NULL,
                end_step INTEGER NOT NULL,
                steps_list TEXT,
                FOREIGN KEY (validation_id) REFERENCES scenario_validation_sessions(validation_id)
            )
        ''')
        
        # 인덱스 생성
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_validation_id_occurrences 
            ON scenario_consecutive_5_occurrences(validation_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_steps 
            ON scenario_consecutive_5_occurrences(start_step, end_step)
        ''')
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        st.error(f"테이블 생성 오류: {str(e)}")
        return False
    finally:
        conn.close()

# create_ngram_chunks_table는 svg_parser_module에서 import됨

def create_stored_predictions_table():
    """
    예측값 저장 테이블 생성 (DB에 영구 저장)
    - 이전 데이터 전체로 학습한 prefix별 예측값 저장
    - grid_string_id는 저장하지 않음 (prefix가 unique)
    
    Returns:
        bool: 테이블 생성 성공 여부
    """
    conn = get_db_connection()
    if conn is None:
        return False
    
    cursor = conn.cursor()
    
    try:
        # 기존 테이블이 있으면 삭제하고 재생성 (구조 변경)
        cursor.execute('DROP TABLE IF EXISTS stored_predictions')
        
        cursor.execute('''
            CREATE TABLE stored_predictions (
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
        ''')
        
        # 인덱스 생성
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_window_prefix 
            ON stored_predictions(window_size, prefix)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_method_threshold 
            ON stored_predictions(method, threshold)
        ''')
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        st.error(f"stored_predictions 테이블 생성 오류: {str(e)}")
        return False
    finally:
        conn.close()

def create_prefix_trend_rules_table():
    """
    prefix_trend_rules 테이블 생성
    
    prefix의 b/p 비율과 suffix 분포의 관계를 저장하는 테이블
    
    Returns:
        bool: 테이블 생성 성공 여부
    """
    conn = get_db_connection()
    if conn is None:
        return False
    
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS prefix_trend_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                window_size INTEGER NOT NULL,
                prefix TEXT NOT NULL,
                b_ratio REAL NOT NULL,
                p_ratio REAL NOT NULL,
                b_suffix_count INTEGER NOT NULL,
                p_suffix_count INTEGER NOT NULL,
                total_count INTEGER NOT NULL,
                trend_follow INTEGER NOT NULL,
                confidence REAL NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                updated_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                UNIQUE(window_size, prefix)
            )
        ''')
        
        # 인덱스 생성
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_prefix_trend_window_prefix 
            ON prefix_trend_rules(window_size, prefix)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_prefix_trend_window 
            ON prefix_trend_rules(window_size)
        ''')
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        st.error(f"prefix_trend_rules 테이블 생성 오류: {str(e)}")
        return False
    finally:
        conn.close()

# generate_and_save_ngram_chunks는 svg_parser_module에서 import됨

def batch_generate_ngram_chunks_for_existing_data(window_sizes=[5, 6, 7, 8, 9]):
    """
    기존 preprocessed_grid_strings 데이터에 대해 ngram_chunks를 일괄 생성
    
    Args:
        window_sizes: 생성할 윈도우 크기 리스트
    """
    df_strings = load_preprocessed_data()
    
    if len(df_strings) == 0:
        st.warning("⚠️ 처리할 데이터가 없습니다.")
        return
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    total = len(df_strings)
    processed = 0
    errors = []
    
    for idx, row in df_strings.iterrows():
        status_text.text(f"처리 중: {idx + 1}/{total} (ID: {row['id']})")
        progress_bar.progress((idx + 1) / total)
        
        try:
            generate_and_save_ngram_chunks(
                row['id'],
                row['grid_string'],
                window_sizes
            )
            processed += 1
        except Exception as e:
            errors.append(f"ID {row['id']}: {str(e)}")
    
    progress_bar.empty()
    status_text.empty()
    
    if errors:
        st.warning(f"⚠️ {len(errors)}개 오류 발생 (처리 완료: {processed}/{total})")
        with st.expander("오류 상세"):
            for error in errors:
                st.text(error)
    else:
        st.success(f"✅ {processed}/{total}개 grid_string의 ngram_chunks 생성 완료")

def save_scenario_validation_result(result_data, grid_string, window_size, 
                                    prediction_method, train_ratio):
    """
    시나리오 검증 결과를 DB에 저장
    
    Args:
        result_data: simulate_game_scenario의 반환값
        grid_string: 검증한 문자열
        window_size: 윈도우 크기
        prediction_method: 예측 방법
        train_ratio: 학습 세트 비율
    
    Returns:
        str: validation_id (저장된 검증 ID)
    """
    # 테이블 생성 확인
    if not create_scenario_validation_tables():
        raise Exception("테이블 생성 실패")
    
    validation_id = str(uuid.uuid4())
    conn = get_db_connection()
    if conn is None:
        raise Exception("데이터베이스 연결 실패")
    
    cursor = conn.cursor()
    
    try:
        # 1. 세션 요약 저장
        stats = result_data['stats']
        b_count = grid_string.count('b')
        p_count = grid_string.count('p')
        string_length = len(grid_string)
        
        cursor.execute('''
            INSERT INTO scenario_validation_sessions (
                validation_id, grid_string, string_length,
                b_count, p_count, b_ratio, p_ratio,
                window_size, prediction_method, train_ratio,
                result, max_consecutive_mismatches, consecutive_5_count,
                total_steps, matches, mismatches, match_rate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            validation_id,
            grid_string,
            string_length,
            b_count,
            p_count,
            (b_count / string_length * 100) if string_length > 0 else 0,
            (p_count / string_length * 100) if string_length > 0 else 0,
            window_size,
            prediction_method,
            train_ratio,
            result_data['result'],
            result_data['max_consecutive_mismatches'],
            stats['consecutive_5_count'],
            stats['total'],
            stats['matches'],
            stats['mismatches'],
            (stats['matches'] / stats['total'] * 100) if stats['total'] > 0 else 0
        ))
        
        # 2. 각 스텝 상세 저장
        for entry in result_data['history']:
            ratios = entry.get('ratios', {})
            cursor.execute('''
                INSERT INTO scenario_validation_steps (
                    validation_id, step_number, step_index,
                    prefix, predicted_value, actual_value, is_match,
                    confidence, predicted_ratio, actual_ratio,
                    consecutive_mismatches
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                validation_id,
                entry['step'],
                entry['index'],
                entry['prefix'],
                entry['predicted'],
                entry['actual'],
                1 if entry['is_match'] else 0,
                entry['confidence'],
                ratios.get(entry['predicted'], 0.0),
                ratios.get(entry['actual'], 0.0),
                entry.get('consecutive_mismatches', 0)
            ))
        
        # 3. 연속 불일치 5개 발생 위치 저장
        for idx, pos_info in enumerate(result_data['consecutive_5_positions'], 1):
            cursor.execute('''
                INSERT INTO scenario_consecutive_5_occurrences (
                    validation_id, occurrence_index,
                    start_step, end_step, steps_list
                ) VALUES (?, ?, ?, ?, ?)
            ''', (
                validation_id,
                idx,
                pos_info['start_step'],
                pos_info['end_step'],
                json.dumps(pos_info['steps'])
            ))
        
        conn.commit()
        return validation_id
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

# save_parsed_grid_string_to_db는 svg_parser_module에서 import됨

def load_predictions_from_table(window_size, prefix, method, threshold):
    """
    DB 테이블에서 예측값 조회 (grid_string_id 제거)
    
    Args:
        window_size: 윈도우 크기
        prefix: prefix 문자열
        method: 예측 방법
        threshold: 임계값
    
    Returns:
        dict: {
            'predicted_value': 예측값,
            'confidence': 신뢰도,
            'b_ratio': b 비율,
            'p_ratio': p 비율
        } or None (없는 경우)
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT predicted_value, confidence, b_ratio, p_ratio
            FROM stored_predictions
            WHERE window_size = ? 
              AND prefix = ? 
              AND method = ? 
              AND threshold = ?
        ''', (window_size, prefix, method, threshold))
        
        row = cursor.fetchone()
        if row:
            return {
                'predicted_value': row[0],
                'confidence': row[1],
                'b_ratio': row[2],
                'p_ratio': row[3]
            }
        return None
    except Exception as e:
        st.error(f"예측값 조회 오류: {str(e)}")
        return None
    finally:
        conn.close()

def save_or_update_predictions_for_historical_data(
    cutoff_grid_string_id=None,
    window_sizes=[5, 6, 7, 8, 9],
    methods=["빈도 기반"],
    thresholds=[0, 50, 60, 70, 80, 90, 100],
    batch_size=1000
):
    """
    이전 데이터로 예측값을 계산하여 DB 테이블에 저장/업데이트
    - 이전 데이터 전체로 모델 구축
    - 모든 가능한 prefix에 대한 예측값만 저장 (grid_string_id 없이)
    
    Args:
        cutoff_grid_string_id: 기준 grid_string_id (None이면 전체 데이터)
            - 이 ID 이하가 이전 데이터 (id <= cutoff_grid_string_id)
        window_sizes: 윈도우 크기 리스트
        methods: 예측 방법 리스트
        thresholds: 임계값 리스트 (0은 임계값 없이 모든 예측 포함)
        batch_size: 배치 처리 크기 (성능 최적화)
    
    Returns:
        dict: {
            'total_saved': 저장/업데이트된 총 레코드 수,
            'new_records': 새로 생성된 레코드 수,
            'updated_records': 업데이트된 레코드 수,
            'unique_prefixes': 고유 prefix 수
        }
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # 이전 데이터 선택
        if cutoff_grid_string_id is None:
            query = "SELECT id FROM preprocessed_grid_strings ORDER BY id"
            params = []
        else:
            query = "SELECT id FROM preprocessed_grid_strings WHERE id <= ? ORDER BY id"
            params = [cutoff_grid_string_id]
        
        df_historical = pd.read_sql_query(query, conn, params=params)
        
        if len(df_historical) == 0:
            return {
                'total_saved': 0,
                'new_records': 0,
                'updated_records': 0,
                'unique_prefixes': 0
            }
        
        # 이전 데이터의 ngram_chunks 로드
        historical_ids = df_historical['id'].tolist()
        
        total_saved = 0
        new_records = 0
        updated_records = 0
        unique_prefixes_set = set()
        
        cursor = conn.cursor()
        
        for window_size in window_sizes:
            # 해당 윈도우 크기의 ngram_chunks 로드
            train_ngrams = load_ngram_chunks(window_size=window_size, grid_string_ids=historical_ids)
            
            if len(train_ngrams) == 0:
                continue
            
            # 모델 구축 (이전 데이터 전체)
            for method in methods:
                if method == "빈도 기반":
                    model = build_frequency_model(train_ngrams)
                # elif method == "마르코프 체인":
                #     model = build_markov_model(train_ngrams)
                elif method == "가중치 기반":
                    model = build_weighted_model(train_ngrams)
                elif method == "안전 우선":
                    model = build_safety_first_model(train_ngrams)
                else:
                    model = build_frequency_model(train_ngrams)
                
                # 모든 가능한 prefix 추출 (중복 제거)
                all_prefixes = set()
                for _, row in train_ngrams.iterrows():
                    all_prefixes.add(row['prefix'])
                
                # 각 prefix에 대해 예측값 계산 및 저장
                batch_data = []
                
                for prefix in all_prefixes:
                    unique_prefixes_set.add((window_size, prefix))
                    
                    # 각 임계값에 대해 예측값 계산
                    for threshold in thresholds:
                        if threshold == 0:
                            # 임계값 없이 모든 예측 포함
                            prediction_result = predict_for_prefix(model, prefix, method)
                        else:
                            # 임계값 전략 사용
                            prediction_result = predict_confidence_threshold(model, prefix, method, threshold)
                        
                        predicted = prediction_result.get('predicted')
                        confidence = prediction_result.get('confidence', 0.0)
                        ratios = prediction_result.get('ratios', {})
                        
                        b_ratio = ratios.get('b', 0.0)
                        p_ratio = ratios.get('p', 0.0)
                        
                        batch_data.append((
                            window_size,
                            prefix,
                            predicted,
                            confidence,
                            b_ratio,
                            p_ratio,
                            method,
                            threshold
                        ))
                
                # 배치로 저장/업데이트
                if batch_data:
                    for i in range(0, len(batch_data), batch_size):
                        batch = batch_data[i:i + batch_size]
                        
                        for item in batch:
                            try:
                                # 기존 레코드 확인
                                cursor.execute('''
                                    SELECT id FROM stored_predictions
                                    WHERE window_size = ? 
                                      AND prefix = ? 
                                      AND method = ? 
                                      AND threshold = ?
                                ''', (item[0], item[1], item[6], item[7]))
                                
                                existing = cursor.fetchone()
                                
                                cursor.execute('''
                                    INSERT OR REPLACE INTO stored_predictions (
                                        window_size, prefix,
                                        predicted_value, confidence, b_ratio, p_ratio,
                                        method, threshold, updated_at
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
                                ''', item)
                                
                                if existing:
                                    updated_records += 1
                                else:
                                    new_records += 1
                                
                                total_saved += 1
                            except Exception as e:
                                continue
        
        conn.commit()
        
        return {
            'total_saved': total_saved,
            'new_records': new_records,
            'updated_records': updated_records,
            'unique_prefixes': len(unique_prefixes_set)
        }
        
    except Exception as e:
        conn.rollback()
        st.error(f"예측값 저장/업데이트 오류: {str(e)}")
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
        return None
    finally:
        conn.close()

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
            ORDER BY created_at DESC
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

# ============================================================================
# 마르코프 체인 모델 (제거 예정 - 주석 처리)
# ============================================================================
# def build_markov_model(ngrams_df):
#     """마르코프 체인 모델 (빈도 기반과 동일하게 구현)"""
#     return build_frequency_model(ngrams_df)
# 
# def predict_markov(model, prefix):
#     """마르코프 체인 예측"""
#     return predict_frequency(model, prefix)
# ============================================================================

def build_weighted_model(ngrams_df, weight_decay=0.95, id_weight_decay=0.99):
    """
    가중치 기반 모델 구축
    최근 조각에 더 높은 가중치 부여
    - grid_string_id가 클수록 최근 데이터로 간주하여 높은 가중치 부여
    - 각 grid_string_id 내에서도 chunk_index가 클수록 높은 가중치 부여
    
    Args:
        ngrams_df: N-gram 조각 DataFrame
        weight_decay: 가중치 감쇠율 (0~1) - chunk_index 기반 가중치 감쇠율
        id_weight_decay: grid_string_id 기반 가중치 감쇠율 (0~1) - 기본값 0.99
    
    Returns:
        dict: {prefix: {suffix: weighted_count, ...}, ...}
    """
    model = defaultdict(lambda: defaultdict(float))
    
    # 전체 grid_string_id 범위 계산
    if len(ngrams_df) == 0:
        return dict(model)
    
    max_grid_string_id = ngrams_df['grid_string_id'].max()
    
    # grid_string_id별로 그룹화하여 순서 보존
    grouped = ngrams_df.groupby('grid_string_id')
    
    for grid_string_id, group_df in grouped:
        # grid_string_id 기반 가중치 (큰 id일수록 높은 가중치)
        # max_grid_string_id에 가까울수록 최근 데이터로 간주
        id_weight = id_weight_decay ** (max_grid_string_id - grid_string_id)
        
        # 최근 조각에 더 높은 가중치
        group_df = group_df.sort_values('chunk_index')
        max_index = len(group_df)
        
        for idx, (_, row) in enumerate(group_df.iterrows()):
            # chunk_index 기반 가중치 (최근 chunk일수록 높은 가중치)
            chunk_weight = weight_decay ** (max_index - idx - 1)
            
            # 최종 가중치 = id_weight * chunk_weight
            # 최근 grid_string_id의 최근 chunk에 가장 높은 가중치
            weight = id_weight * chunk_weight
            
            prefix = row['prefix']
            suffix = row['suffix']
            model[prefix][suffix] += weight
    
    return dict(model)

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

# ============================================================================
# 안전 우선 적응형 모델 (Safety-First Adaptive Model)
# 독립적으로 구현되어 있어 제거 시 이 섹션만 삭제하면 됨
# ============================================================================

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

# ============================================================================
# 안전 우선 모델 끝
# ============================================================================

# ============================================================================
# 균형 회복 트렌드 모델 (독립 구현)
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

def evaluate_predictions(predictions, actuals):
    """예측 정확도 평가"""
    if len(predictions) != len(actuals) or len(predictions) == 0:
        return {}
    
    correct = sum(1 for p, a in zip(predictions, actuals) if p == a)
    total = len(predictions)
    accuracy = (correct / total * 100) if total > 0 else 0.0
    
    # 문자별 통계
    b_predictions = [p for p in predictions if p == 'b']
    p_predictions = [p for p in predictions if p == 'p']
    b_actuals = [a for a in actuals if a == 'b']
    p_actuals = [a for a in actuals if a == 'p']
    
    b_correct = sum(1 for p, a in zip(b_predictions, b_actuals) if len(b_predictions) > 0 and len(b_actuals) > 0)
    p_correct = sum(1 for p, a in zip(p_predictions, p_actuals) if len(p_predictions) > 0 and len(p_actuals) > 0)
    
    return {
        'accuracy': accuracy,
        'correct': correct,
        'total': total,
        'b_predicted': len(b_predictions),
        'p_predicted': len(p_predictions),
        'b_actual': len(b_actuals),
        'p_actual': len(p_actuals)
    }

def predict_for_prefix(model, prefix, method="빈도 기반"):
    """
    단일 prefix에 대한 예측 수행
    
    Args:
        model: 학습된 모델
        prefix: 예측할 prefix 문자열
        method: 예측 방법
    
    Returns:
        dict: {predicted, ratios, confidence}
    """
    if method == "빈도 기반":
        predicted, ratios = predict_frequency(model, prefix)
    elif method == "가중치 기반":
        predicted, ratios = predict_weighted(model, prefix)
    elif method == "안전 우선":
        # 안전 우선 모델은 히스토리 없이 호출 (기본 모드만 사용)
        result = predict_safety_first(model, prefix, recent_history=None, consecutive_mismatches=0)
        predicted = result.get('predicted')
        ratios = result.get('ratios', {})
    else:  # 기본값: 빈도 기반
        predicted, ratios = predict_frequency(model, prefix)
    
    confidence = max(ratios.values()) if ratios else 0.0
    
    return {
        'predicted': predicted,
        'ratios': ratios,
        'confidence': confidence
    }

def predict_ensemble_voting(model, prefix, methods=['빈도 기반', '가중치 기반', '안전 우선']):
    """
    앙상블 전략 - 다수결 투표 방식
    
    여러 예측 방법의 결과를 투표하여 최종 예측 결정
    
    Args:
        model: 학습된 모델 (여러 방법이 동일한 모델 구조를 사용)
        prefix: 예측할 prefix 문자열
        methods: 사용할 예측 방법 리스트
    
    Returns:
        dict: {predicted, ratios, confidence, strategy_name}
    """
    votes = {'b': 0, 'p': 0}
    all_ratios = {'b': [], 'p': []}
    
    for method in methods:
        # 안전 우선 모델은 직접 호출 (히스토리 없이 - 기본 모드만 사용)
        if method == '안전 우선':
            result = predict_safety_first(model, prefix, recent_history=None, consecutive_mismatches=0)
        else:
            result = predict_for_prefix(model, prefix, method)
        
        predicted = result.get('predicted')
        ratios = result.get('ratios', {})
        
        if predicted is not None:
            votes[predicted] += 1
            for suffix, ratio in ratios.items():
                all_ratios[suffix].append(ratio)
    
    # 다수결 투표
    if votes['b'] > votes['p']:
        predicted = 'b'
    elif votes['p'] > votes['b']:
        predicted = 'p'
    else:
        # 동점인 경우 평균 비율이 높은 것을 선택
        avg_b = sum(all_ratios['b']) / len(all_ratios['b']) if all_ratios['b'] else 0
        avg_p = sum(all_ratios['p']) / len(all_ratios['p']) if all_ratios['p'] else 0
        predicted = 'b' if avg_b > avg_p else 'p'
    
    # 합산 비율 계산
    total_ratios = {}
    for suffix in ['b', 'p']:
        if all_ratios[suffix]:
            total_ratios[suffix] = sum(all_ratios[suffix]) / len(all_ratios[suffix])
        else:
            total_ratios[suffix] = 0.0
    
    # 정규화
    total = sum(total_ratios.values())
    if total > 0:
        ratios = {suffix: (ratio / total * 100) for suffix, ratio in total_ratios.items()}
    else:
        ratios = {'b': 50.0, 'p': 50.0}
    
    confidence = max(ratios.values()) if ratios else 0.0
    
    return {
        'predicted': predicted,
        'ratios': ratios,
        'confidence': confidence,
        'strategy_name': '앙상블_투표'
    }

def predict_ensemble_weighted(model, prefix, methods=['빈도 기반', '가중치 기반', '안전 우선'], weights=None):
    """
    앙상블 전략 - 신뢰도 기반 가중 평균 방식
    
    각 예측 방법의 신뢰도를 가중치로 사용하여 최종 예측 결정
    
    Args:
        model: 학습된 모델
        prefix: 예측할 prefix 문자열
        methods: 사용할 예측 방법 리스트
        weights: 각 방법에 대한 가중치 (None이면 신뢰도 기반 자동 계산)
    
    Returns:
        dict: {predicted, ratios, confidence, strategy_name}
    """
    predictions = []
    confidences = []
    
    for method in methods:
        # 안전 우선 모델은 직접 호출 (히스토리 없이 - 기본 모드만 사용)
        if method == '안전 우선':
            result = predict_safety_first(model, prefix, recent_history=None, consecutive_mismatches=0)
        else:
            result = predict_for_prefix(model, prefix, method)
        
        if result['predicted'] is not None:
            predictions.append(result)
            confidences.append(result['confidence'])
    
    if not predictions:
        return {
            'predicted': None,
            'ratios': {},
            'confidence': 0.0,
            'strategy_name': '앙상블_가중평균'
        }
    
    # 가중치 계산 (신뢰도 기반)
    if weights is None:
        total_confidence = sum(confidences)
        if total_confidence > 0:
            weights = [c / total_confidence for c in confidences]
        else:
            weights = [1.0 / len(confidences)] * len(confidences)
    
    # 가중 평균 비율 계산
    weighted_ratios = {'b': 0.0, 'p': 0.0}
    for i, pred in enumerate(predictions):
        weight = weights[i] if i < len(weights) else 1.0 / len(predictions)
        for suffix, ratio in pred['ratios'].items():
            weighted_ratios[suffix] += ratio * weight
    
    # 정규화
    total = sum(weighted_ratios.values())
    if total > 0:
        ratios = {suffix: (ratio / total * 100) for suffix, ratio in weighted_ratios.items()}
    else:
        ratios = {'b': 50.0, 'p': 50.0}
    
    # 가장 높은 비율의 suffix 선택
    predicted = max(ratios.items(), key=lambda x: x[1])[0]
    confidence = ratios[predicted]
    
    return {
        'predicted': predicted,
        'ratios': ratios,
        'confidence': confidence,
        'strategy_name': '앙상블_가중평균'
    }

def predict_ensemble_new_voting(models_dict, prefix):
    """
    새로운 앙상블 전략 - 다수결 투표 방식 (기존과 독립)
    
    빈도 기반, 가중치 기반, 균형 회복 트렌드 모델을 조합하여 다수결 투표
    
    Args:
        models_dict: {'빈도 기반': model, '가중치 기반': model, '균형 회복 트렌드': model}
        prefix: 예측할 prefix 문자열
    
    Returns:
        dict: {
            'predicted': str,
            'ratios': dict,
            'confidence': float,
            'strategy_name': str,
            'individual_predictions': dict
        }
    """
    votes = {'b': 0, 'p': 0}
    all_ratios = {'b': [], 'p': []}
    individual_predictions = {}
    
    # 각 모델별 예측
    for method_name, model in models_dict.items():
        if method_name == '빈도 기반':
            predicted, ratios = predict_frequency(model, prefix)
        elif method_name == '가중치 기반':
            predicted, ratios = predict_weighted(model, prefix)
        elif method_name == '균형 회복 트렌드':
            predicted, ratios = predict_balance_recovery_trend_final(model, prefix)
        else:
            continue
        
        # 예측값이 None이면 스킵 (하지만 모든 모델이 항상 예측값 반환하도록 보장)
        if predicted is not None:
            votes[predicted] += 1
            for suffix, ratio in ratios.items():
                all_ratios[suffix].append(ratio)
            
            individual_predictions[method_name] = {
                'predicted': predicted,
                'ratios': ratios,
                'confidence': max(ratios.values()) if ratios else 0.0
            }
    
    # 다수결 투표
    if votes['b'] > votes['p']:
        predicted = 'b'
    elif votes['p'] > votes['b']:
        predicted = 'p'
    else:
        # 동점인 경우 평균 비율이 높은 것을 선택
        avg_b = sum(all_ratios['b']) / len(all_ratios['b']) if all_ratios['b'] else 0
        avg_p = sum(all_ratios['p']) / len(all_ratios['p']) if all_ratios['p'] else 0
        predicted = 'b' if avg_b > avg_p else 'p'
    
    # 합산 비율 계산
    total_ratios = {}
    for suffix in ['b', 'p']:
        if all_ratios[suffix]:
            total_ratios[suffix] = sum(all_ratios[suffix]) / len(all_ratios[suffix])
        else:
            total_ratios[suffix] = 0.0
    
    # 정규화
    total = sum(total_ratios.values())
    if total > 0:
        ratios = {suffix: (ratio / total * 100) for suffix, ratio in total_ratios.items()}
    else:
        ratios = {'b': 50.0, 'p': 50.0}
    
    confidence = max(ratios.values()) if ratios else 0.0
    
    return {
        'predicted': predicted,
        'ratios': ratios,
        'confidence': confidence,
        'strategy_name': '앙상블_투표_신규',
        'individual_predictions': individual_predictions,
        'votes': votes
    }

def validate_ensemble_interactive_scenario(grid_string_id, cutoff_grid_string_id, window_size=7, use_threshold=False):
    """
    앙상블 투표 인터랙티브 시나리오 방식으로 단일 grid_string 검증
    
    Args:
        grid_string_id: 검증할 grid_string의 ID
        cutoff_grid_string_id: 학습 데이터 기준 ID (이 ID 이하를 학습 데이터로 사용)
        window_size: 윈도우 크기 (기본값: 7)
        use_threshold: 임계값 사용 여부 (기본값: False)
    
    Returns:
        dict: {
            'grid_string_id': int,
            'max_consecutive_failures': int,
            'total_steps': int,
            'total_failures': int,
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
                'total_steps': 0,
                'total_failures': 0,
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
                'total_steps': 0,
                'total_failures': 0,
                'accuracy': 0.0,
                'history': []
            }
        
        # 모델 구축
        frequency_model = build_frequency_model(train_ngrams)
        weighted_model = build_weighted_model(train_ngrams)
        trend_model = build_balance_recovery_trend_model_final(train_ngrams, window_size)
        
        models_dict = {
            '빈도 기반': frequency_model,
            '가중치 기반': weighted_model,
            '균형 회복 트렌드': trend_model
        }
        
        # 시나리오 방식으로 테스트
        prefix_length = window_size - 1
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        
        # 초기 prefix 생성
        if len(grid_string) < prefix_length:
            return {
                'grid_string_id': grid_string_id,
                'max_consecutive_failures': 0,
                'total_steps': 0,
                'total_failures': 0,
                'accuracy': 0.0,
                'history': []
            }
        
        current_prefix = grid_string[:prefix_length]
        
        # 각 스텝마다 예측 및 검증
        for i in range(prefix_length, len(grid_string)):
            total_steps += 1
            actual_value = grid_string[i]
            
            # 예측
            prediction_result = predict_ensemble_new_voting(models_dict, current_prefix)
            predicted_value = prediction_result.get('predicted')
            
            # 예측값이 None이면 스킵 (임계값 미사용이므로 항상 예측값이 있어야 함)
            if predicted_value is None:
                continue
            
            # 실제값과 비교
            is_correct = predicted_value == actual_value
            
            if not is_correct:
                consecutive_failures += 1
                total_failures += 1
                if consecutive_failures > max_consecutive_failures:
                    max_consecutive_failures = consecutive_failures
            else:
                consecutive_failures = 0
            
            # 히스토리 기록 (개별 모델 예측값 및 투표 결과 포함)
            history.append({
                'step': total_steps,
                'prefix': current_prefix,
                'predicted': predicted_value,
                'actual': actual_value,
                'is_correct': is_correct,
                'confidence': prediction_result.get('confidence', 0.0),
                'consecutive_failures': consecutive_failures,
                'individual_predictions': prediction_result.get('individual_predictions', {}),
                'votes': prediction_result.get('votes', {'b': 0, 'p': 0})
            })
            
            # 다음 prefix 생성
            current_prefix = get_next_prefix(current_prefix, actual_value, window_size)
        
        # 정확도 계산
        accuracy = ((total_steps - total_failures) / total_steps * 100) if total_steps > 0 else 0.0
        
        return {
            'grid_string_id': grid_string_id,
            'max_consecutive_failures': max_consecutive_failures,
            'total_steps': total_steps,
            'total_failures': total_failures,
            'accuracy': accuracy,
            'history': history
        }
        
    except Exception as e:
        st.error(f"검증 중 오류 발생 (grid_string_id={grid_string_id}): {str(e)}")
        return None
    finally:
        conn.close()

def batch_validate_ensemble_scenario(cutoff_grid_string_id, window_size=7, use_threshold=False):
    """
    cutoff_grid_string_id 이후의 모든 grid_string에 대해 배치 검증 실행
    
    Args:
        cutoff_grid_string_id: 기준 grid_string ID (이 ID 이후의 데이터 검증)
        window_size: 윈도우 크기 (기본값: 7)
        use_threshold: 임계값 사용 여부 (기본값: False)
    
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
                    'avg_max_consecutive_failures': 0.0
                }
            }
        
        grid_string_ids = df['id'].tolist()
        results = []
        
        # 각 grid_string에 대해 검증 실행
        for grid_string_id in grid_string_ids:
            result = validate_ensemble_interactive_scenario(
                grid_string_id,
                cutoff_grid_string_id,
                window_size=window_size, 
                use_threshold=use_threshold
            )
            
            if result is not None:
                results.append(result)
        
        # 요약 통계 계산
        if len(results) > 0:
            total_grid_strings = len(results)
            avg_accuracy = sum(r['accuracy'] for r in results) / total_grid_strings
            max_consecutive_failures = max(r['max_consecutive_failures'] for r in results)
            avg_max_consecutive_failures = sum(r['max_consecutive_failures'] for r in results) / total_grid_strings
            
            summary = {
                'total_grid_strings': total_grid_strings,
                'avg_accuracy': avg_accuracy,
                'max_consecutive_failures': max_consecutive_failures,
                'avg_max_consecutive_failures': avg_max_consecutive_failures,
                'total_steps': sum(r['total_steps'] for r in results),
                'total_failures': sum(r['total_failures'] for r in results)
            }
        else:
            summary = {
                'total_grid_strings': 0,
                'avg_accuracy': 0.0,
                'max_consecutive_failures': 0,
                'avg_max_consecutive_failures': 0.0,
                'total_steps': 0,
                'total_failures': 0
            }
        
        return {
            'results': results,
            'summary': summary
        }
        
    except Exception as e:
        st.error(f"배치 검증 중 오류 발생: {str(e)}")
        return None
    finally:
        conn.close()

def predict_confidence_threshold(model, prefix, method="빈도 기반", threshold=60):
    """
    신뢰도 임계값 전략 - 신뢰도가 임계값 미만이면 예측하지 않음
    
    Args:
        model: 학습된 모델
        prefix: 예측할 prefix 문자열
        method: 예측 방법
        threshold: 신뢰도 임계값 (기본값: 60%)
    
    Returns:
        dict: {predicted, ratios, confidence, strategy_name}
    """
    result = predict_for_prefix(model, prefix, method)
    confidence = result.get('confidence', 0.0)
    predicted = result.get('predicted')
    
    # 예측값이 없으면 None 반환
    if predicted is None:
        return {
            'predicted': None,
            'ratios': result.get('ratios', {}),
            'confidence': confidence,
            'strategy_name': f'신뢰도임계값_{threshold}'
        }
    
    # 부동소수점 오차를 고려하여 반올림된 값을 비교
    # 임계값 이상이면 예측값 반환 (threshold=57이면 confidence>=57이면 예측)
    # 반올림하여 비교 (예: 56.9 -> 57, 57.0 -> 57)
    confidence_rounded = round(confidence, 1)
    threshold_rounded = round(threshold, 1)
    
    if confidence_rounded < threshold_rounded:
        # 신뢰도가 낮으면 예측하지 않음
        return {
            'predicted': None,
            'ratios': result.get('ratios', {}),
            'confidence': confidence,
            'strategy_name': f'신뢰도임계값_{threshold}'
        }
    
    return {
        'predicted': result.get('predicted'),
        'ratios': result.get('ratios', {}),
        'confidence': confidence,
        'strategy_name': f'신뢰도임계값_{threshold}'
    }

def predict_confidence_reverse(model, prefix, method="빈도 기반", threshold=50):
    """
    신뢰도 역전략 - 신뢰도가 임계값 미만이면 반대 예측
    
    Args:
        model: 학습된 모델
        prefix: 예측할 prefix 문자열
        method: 예측 방법
        threshold: 신뢰도 임계값 (기본값: 50%)
    
    Returns:
        dict: {predicted, ratios, confidence, strategy_name}
    """
    result = predict_for_prefix(model, prefix, method)
    predicted = result.get('predicted')
    ratios = result.get('ratios', {})
    confidence = result.get('confidence', 0.0)
    
    if confidence < threshold and predicted is not None:
        # 신뢰도가 낮으면 반대 예측
        reverse_predicted = 'p' if predicted == 'b' else 'b'
        reverse_ratios = {}
        for suffix, ratio in ratios.items():
            reverse_ratios[suffix] = 100.0 - ratio
        
        # 정규화
        total = sum(reverse_ratios.values())
        if total > 0:
            reverse_ratios = {suffix: (ratio / total * 100) for suffix, ratio in reverse_ratios.items()}
        
        return {
            'predicted': reverse_predicted,
            'ratios': reverse_ratios,
            'confidence': max(reverse_ratios.values()) if reverse_ratios else 0.0,
            'strategy_name': f'신뢰도역전_{threshold}'
        }
    
    return {
        'predicted': predicted,
        'ratios': ratios,
        'confidence': confidence,
        'strategy_name': f'신뢰도역전_{threshold}'
    }

def predict_reverse(model, prefix, method="빈도 기반"):
    """
    역전략 - 예측과 반대로 예측 (연속 일치 5회 이상 방지)
    
    가장 낮은 빈도의 suffix를 예측
    
    Args:
        model: 학습된 모델
        prefix: 예측할 prefix 문자열
        method: 예측 방법
    
    Returns:
        dict: {predicted, ratios, confidence, strategy_name}
    """
    result = predict_for_prefix(model, prefix, method)
    predicted = result.get('predicted')
    ratios = result.get('ratios', {})
    
    if not ratios:
        return {
            'predicted': None,
            'ratios': {},
            'confidence': 0.0,
            'strategy_name': '역전략'
        }
    
    # 가장 낮은 빈도의 suffix 선택
    reverse_predicted = min(ratios.items(), key=lambda x: x[1])[0]
    
    # 비율 역전 (낮은 비율이 높은 비율로)
    reverse_ratios = {}
    for suffix, ratio in ratios.items():
        reverse_ratios[suffix] = 100.0 - ratio
    
    # 정규화
    total = sum(reverse_ratios.values())
    if total > 0:
        reverse_ratios = {suffix: (ratio / total * 100) for suffix, ratio in reverse_ratios.items()}
    
    confidence = reverse_ratios[reverse_predicted] if reverse_predicted in reverse_ratios else 0.0
    
    return {
        'predicted': reverse_predicted,
        'ratios': reverse_ratios,
        'confidence': confidence,
        'strategy_name': '역전략'
    }

def predict_with_fallback_interval(
    model, 
    prefix, 
    method="빈도 기반", 
    threshold=60,
    max_interval=5,
    current_interval=0
):
    """
    최대 간격 제약이 있는 예측 전략
    
    임계값 기반 예측을 시도하되, 최대 간격을 넘기면 임계값을 무시하고 강제 예측합니다.
    
    Args:
        model: 학습된 모델
        prefix: 예측할 prefix 문자열
        method: 예측 방법
        threshold: 신뢰도 임계값
        max_interval: 최대 예측 없음 간격 (이 간격을 넘기면 강제 예측)
        current_interval: 현재 예측 없음 간격
    
    Returns:
        dict: {
            'predicted': 예측값 (None일 수 있음),
            'confidence': 신뢰도,
            'ratios': 비율,
            'is_forced': 강제 예측 여부,
            'strategy_name': 전략 이름
        }
    """
    # 기본 예측 시도 (임계값 기반)
    prediction_result = predict_confidence_threshold(model, prefix, method, threshold)
    
    # 예측값이 있고 신뢰도가 임계값 이상이면 반환
    if prediction_result.get('predicted') is not None:
        return {
            **prediction_result,
            'is_forced': False,
            'strategy_name': f'임계값_{threshold}'
        }
    
    # 예측값이 없고 최대 간격을 넘겼으면 강제 예측
    # current_interval은 이미 예측 없음이 발생한 횟수
    # 예: max_interval=6이면 간격 6일 때 강제 예측 (예측 없음이 6번 발생)
    # current_interval >= max_interval이면 강제 예측
    # 예: max_interval=6이면 간격 6일 때 강제 예측 (예측 없음이 6번 발생)
    if current_interval >= max_interval:
        # 임계값 없이 예측
        forced_result = predict_for_prefix(model, prefix, method)
        return {
            **forced_result,
            'is_forced': True,
            'strategy_name': f'강제예측_간격{max_interval}'
        }
    
    # 예측값이 없고 간격이 아직 안 넘었으면 None 반환
    return {
        'predicted': None,
        'confidence': prediction_result.get('confidence', 0.0),
        'ratios': prediction_result.get('ratios', {}),
        'is_forced': False,
        'strategy_name': f'임계값_{threshold}'
    }

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

def generate_prediction_tree(model, initial_prefix, window_size, method="빈도 기반", max_depth=5, cache=None):
    """
    다단계 예측 트리 생성 (모든 가능한 경로 자동 생성)
    
    Args:
        model: 학습된 모델
        initial_prefix: 초기 prefix
        window_size: 윈도우 크기
        method: 예측 방법
        max_depth: 최대 깊이
        cache: 캐시 딕셔너리 (중복 계산 방지)
    
    Returns:
        dict: 트리 데이터 구조
    """
    if cache is None:
        cache = {}
    
    # 현재 prefix에 대한 예측
    prediction_result = predict_for_prefix(model, initial_prefix, method)
    ratios = prediction_result.get('ratios', {})
    
    # 트리 노드 생성
    node = {
        'prefix': initial_prefix,
        'predictions': ratios,
        'children': []
    }
    
    # 최대 깊이에 도달하면 종료
    if max_depth <= 1:
        return node
    
    # 모든 가능한 후보값('b', 'p')에 대해 경로 생성
    candidates = ['b', 'p']
    
    for candidate in candidates:
        # 다음 prefix 생성
        next_prefix = get_next_prefix(initial_prefix, candidate, window_size)
        
        # 캐시 확인
        cache_key = f"{next_prefix}_{max_depth-1}"
        if cache_key in cache:
            child_node = cache[cache_key]
        else:
            # 재귀적으로 다음 단계 생성
            child_node = generate_prediction_tree(
                model, next_prefix, window_size, method, max_depth - 1, cache
            )
            cache[cache_key] = child_node
        
        # 경로 정보 추가
        child_node['path_value'] = candidate  # 이 경로로 오기 위해 선택된 값
        node['children'].append(child_node)
    
    return node

def display_prediction_tree_html(node, max_depth=5):
    """
    트리 구조를 HTML로 가로 확장 형태로 표시 (오른쪽으로 확장)
    
    Args:
        node: 트리 노드
        max_depth: 최대 깊이
    """
    html = """
    <style>
    .prediction-tree-container {
        font-family: 'Courier New', monospace;
        font-size: 13px;
        margin: 20px 0;
        overflow-x: auto;
    }
    .tree-row {
        display: flex;
        align-items: flex-start;
        margin: 15px 0;
        min-height: 80px;
    }
    .tree-node {
        display: inline-block;
        vertical-align: top;
        margin: 0 20px;
        padding: 12px;
        border: 2px solid #4CAF50;
        border-radius: 8px;
        background: linear-gradient(135deg, #f9f9f9 0%, #ffffff 100%);
        min-width: 140px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .tree-node-root {
        border-color: #2196F3;
        background: linear-gradient(135deg, #E3F2FD 0%, #BBDEFB 100%);
        font-weight: bold;
        box-shadow: 0 4px 8px rgba(33,150,243,0.3);
    }
    .tree-prefix {
        font-size: 18px;
        font-weight: bold;
        color: #1976D2;
        margin-bottom: 8px;
        text-align: center;
        padding: 5px;
        background-color: rgba(255,255,255,0.5);
        border-radius: 4px;
    }
    .tree-predictions {
        font-size: 12px;
        margin-top: 8px;
    }
    .tree-pred-item {
        margin: 4px 0;
        padding: 4px 8px;
        border-radius: 4px;
        text-align: center;
    }
    .tree-pred-high {
        background-color: #C8E6C9;
        font-weight: bold;
        color: #2E7D32;
    }
    .tree-pred-low {
        background-color: #FFE0B2;
        color: #E65100;
    }
    .tree-connector {
        display: inline-block;
        margin: 0 10px;
        font-size: 24px;
        color: #666;
        vertical-align: middle;
        line-height: 80px;
    }
    .tree-path-label {
        font-size: 10px;
        color: #666;
        margin-top: 5px;
        font-style: italic;
        text-align: center;
    }
    .tree-branch {
        display: flex;
        flex-direction: column;
        align-items: center;
    }
    </style>
    """
    
    # 트리를 레벨별로 구성
    levels = {}
    
    def traverse_tree(n, depth=0, path_label="", parent_prefix=""):
        if depth > max_depth:
            return
        
        prefix = n.get('prefix', '')
        predictions = n.get('predictions', {})
        path_value = n.get('path_value', '')
        
        # 예측값을 높은 순서로 정렬
        sorted_predictions = sorted(predictions.items(), key=lambda x: x[1], reverse=True) if predictions else []
        
        node_data = {
            'prefix': prefix,
            'predictions': sorted_predictions,
            'path_label': path_label,
            'path_value': path_value,
            'parent_prefix': parent_prefix
        }
        
        if depth not in levels:
            levels[depth] = []
        levels[depth].append(node_data)
        
        # 자식 노드 처리 (예측값 높은 순서로)
        children = n.get('children', [])
        if children:
            # 예측값이 높은 경로를 먼저 표시하기 위해 정렬
            sorted_children = sorted(children, key=lambda c: max(c.get('predictions', {}).values()) if c.get('predictions') else 0, reverse=True)
            
            for child in sorted_children:
                child_path_value = child.get('path_value', '')
                new_path_label = f"{path_label} → {child_path_value}" if path_label else child_path_value
                traverse_tree(child, depth + 1, new_path_label, prefix)
    
    traverse_tree(node)
    
    # HTML 생성 - 가로 확장 형태
    html += '<div class="prediction-tree-container">'
    
    for depth in sorted(levels.keys()):
        html += f'<div class="tree-row">'
        html += f'<div style="min-width: 80px; text-align: center; padding-top: 30px; font-weight: bold; color: #666;">Step {depth + 1}</div>'
        
        nodes_at_level = levels[depth]
        
        # 같은 부모를 가진 노드들을 그룹화
        parent_groups = {}
        for node_data in nodes_at_level:
            parent = node_data['parent_prefix']
            if parent not in parent_groups:
                parent_groups[parent] = []
            parent_groups[parent].append(node_data)
        
        # 각 부모 그룹별로 표시
        for parent, group_nodes in parent_groups.items():
            html += '<div class="tree-branch">'
            
            for node_data in group_nodes:
                prefix = node_data['prefix']
                predictions = node_data['predictions']
                path_value = node_data['path_value']
                
                node_class = "tree-node tree-node-root" if depth == 0 else "tree-node"
                
                html += f'<div class="{node_class}">'
                html += f'<div class="tree-prefix">{prefix}</div>'
                
                if path_value:
                    html += f'<div class="tree-path-label">경로: {path_value}</div>'
                
                if predictions:
                    html += '<div class="tree-predictions">'
                    for idx, (value, ratio) in enumerate(predictions):
                        pred_class = "tree-pred-item tree-pred-high" if idx == 0 else "tree-pred-item tree-pred-low"
                        html += f'<div class="{pred_class}">{value}: {ratio:.1f}%</div>'
                    html += '</div>'
                else:
                    html += '<div class="tree-predictions" style="color: #999;">데이터 없음</div>'
                
                html += '</div>'
            
            html += '</div>'
            
            # 다음 레벨로 연결선 표시 (마지막 레벨이 아닌 경우)
            if depth < max(levels.keys()):
                html += '<div class="tree-connector">→</div>'
        
        html += '</div>'
    
    html += '</div>'
    
    st.markdown(html, unsafe_allow_html=True)

def display_prediction_tree(node, depth=0, max_display_depth=5):
    """
    트리 구조를 UI에 표시 (HTML 버전 사용)
    
    Args:
        node: 트리 노드
        depth: 현재 깊이 (사용 안 함, 호환성 유지)
        max_display_depth: 최대 표시 깊이
    """
    display_prediction_tree_html(node, max_display_depth)

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

def simulate_game_scenario(model, grid_string, window_size, method="빈도 기반", strategy_func=None, skip_ending_mismatch=True, max_interval=None, threshold=60, truncate_at_last_match=True):
    """
    게임 시뮬레이션 실행 (모든 스텝 진행 후 연속 불일치/일치 5개 검증)
    
    Args:
        model: 학습된 모델
        grid_string: 검증할 문자열
        window_size: 윈도우 크기
        method: 예측 방법
        strategy_func: 커스텀 전략 함수 (None이면 기본 predict_for_prefix 사용)
        skip_ending_mismatch: True면 불일치 상태로 끝나는 케이스 스킵 (전체 스킵)
        max_interval: 최대 예측 없음 간격 (None이면 강제 예측 사용 안 함)
        threshold: 신뢰도 임계값 (max_interval 사용 시 필요)
        truncate_at_last_match: True면 불일치로 끝날 때 마지막 일치 스텝까지만 유효 처리
    
    Returns:
        dict: {
            'result': 'has_5_consecutive' | 'no_5_consecutive' | 'skipped_ending_mismatch',
            'ends_with_match': 마지막 상태가 일치인지 여부,
            'ends_with_mismatch': 마지막 상태가 불일치인지 여부,
            'ending_consecutive_mismatches': 종료 시 연속 불일치 수,
            'max_consecutive_mismatches': 최대 연속 불일치 수,
            'max_consecutive_matches': 최대 연속 일치 수,
            'consecutive_5_positions': 연속 불일치 5개가 발생한 위치들,
            'consecutive_5_match_positions': 연속 일치 5개가 발생한 위치들,
            'history': [각 스텝의 결과],
            'stats': 통계 정보,
            'skipped': 스킵 여부,
            'truncated': 일치 종료 지점에서 잘렸는지 여부,
            'last_match_step': 마지막 일치 스텝 번호 (truncated=True일 때),
            'truncated_steps': 잘린 스텝 수 (전체 - 유효),
            'forced_predictions': 강제 예측 수 (max_interval 사용 시),
            'forced_prediction_ratio': 강제 예측 비율 (max_interval 사용 시),
            'avg_interval': 평균 간격 (max_interval 사용 시),
            'max_interval_actual': 실제 최대 간격 (max_interval 사용 시),
            'min_interval': 최소 간격 (max_interval 사용 시)
        }
    """
    # prefix들 추출
    prefixes_data = extract_prefixes_from_string(grid_string, window_size)
    
    if not prefixes_data:
        return {
            'result': 'no_5_consecutive',
            'max_consecutive_mismatches': 0,
            'max_consecutive_matches': 0,
            'consecutive_5_positions': [],
            'consecutive_5_match_positions': [],
            'history': [],
            'stats': {
                'total': 0,
                'matches': 0,
                'mismatches': 0,
                'max_consecutive_mismatches': 0,
                'max_consecutive_matches': 0,
                'consecutive_5_count': 0,
                'consecutive_5_match_count': 0
            }
        }
    
    history = []
    consecutive_mismatches = 0
    consecutive_matches = 0
    max_consecutive_mismatches = 0
    max_consecutive_matches = 0
    consecutive_5_positions = []  # 연속 불일치 5개가 발생한 위치들 (겹치지 않는 구간만)
    consecutive_5_match_positions = []  # 연속 일치 5개가 발생한 위치들 (겹치지 않는 구간만)
    total_matches = 0
    total_mismatches = 0
    
    # 히스토리 추적 (안전 우선 모델용)
    prediction_history = []  # [(predicted, actual, is_match), ...]
    
    # 강제 예측 관련 변수 (max_interval 사용 시)
    current_interval = 0  # 현재 예측 없음 간격
    forced_predictions = 0  # 강제 예측 수
    total_predictions = 0  # 전체 예측 수
    intervals = []  # 예측 간격 리스트
    
    # 연속 불일치 구간 추적 (중복 제거를 위해)
    current_consecutive_start = None  # 현재 연속 불일치 구간의 시작 step
    current_consecutive_match_start = None  # 현재 연속 일치 구간의 시작 step
    
    # 안전 우선 모델 래퍼 함수 생성 (히스토리와 consecutive_mismatches 전달)
    def create_safety_first_wrapper(hist, consec_mismatches):
        def wrapper(m, p, mthd):
            return predict_safety_first(m, p, recent_history=hist, consecutive_mismatches=consec_mismatches)
        return wrapper
    
    def add_or_merge_consecutive_range(start_step, end_step, positions_list):
        """연속 구간을 추가하거나 기존 구간과 병합"""
        # 기존 구간과 겹치는지 확인
        merged = False
        for existing_pos in positions_list:
            existing_start = existing_pos['start_step']
            existing_end = existing_pos['end_step']
            
            # 겹치는 경우: 두 구간이 겹치거나 인접한 경우
            # (새 구간의 시작이 기존 구간의 끝 이하이고, 새 구간의 끝이 기존 구간의 시작 이상)
            if start_step <= existing_end + 1 and end_step >= existing_start - 1:
                # 더 넓은 구간으로 병합
                new_start = min(start_step, existing_start)
                new_end = max(end_step, existing_end)
                existing_pos['start_step'] = new_start
                existing_pos['end_step'] = new_end
                existing_pos['steps'] = list(range(new_start, new_end + 1))
                merged = True
                break
        
        # 겹치지 않으면 새로 추가
        if not merged:
            positions_list.append({
                'start_step': start_step,
                'end_step': end_step,
                'steps': list(range(start_step, end_step + 1))
            })
    
    # 모든 스텝을 진행하면서 연속 불일치/일치 추적
    for step, (prefix, actual_suffix, index) in enumerate(prefixes_data, 1):
        # 예측값 계산
        if max_interval is not None:
            # 강제 예측 전략 사용
            prediction_result = predict_with_fallback_interval(
                model, prefix, method, threshold, max_interval, current_interval
            )
        elif strategy_func:
            # strategy_func 호출 (안전 우선 모델인 경우 히스토리는 래퍼에서 전달됨)
            # 안전 우선 모델 래퍼의 히스토리 업데이트
            if hasattr(strategy_func, '_history_ref') and hasattr(strategy_func, '_mismatches_ref'):
                strategy_func._history_ref['data'] = prediction_history.copy()
                strategy_func._mismatches_ref['count'] = consecutive_mismatches
            
            prediction_result = strategy_func(model, prefix, method)
        else:
            prediction_result = predict_for_prefix(model, prefix, method)
        
        predicted = prediction_result.get('predicted')
        ratios = prediction_result.get('ratios', {})
        confidence = prediction_result.get('confidence', 0.0)
        is_forced = prediction_result.get('is_forced', False)
        
        if predicted is None:
            # 예측 데이터가 없으면 간격 증가 (max_interval 사용 시)
            if max_interval is not None:
                current_interval += 1
            continue
        
        # 예측값이 있으면
        if max_interval is not None:
            total_predictions += 1
            if is_forced:
                forced_predictions += 1
            # 간격 기록 (이전 예측 이후의 간격)
            if current_interval > 0:
                intervals.append(current_interval)
            current_interval = 0  # 리셋
        
        # 일치 여부 확인
        is_match = (predicted == actual_suffix)
        
        # 히스토리 업데이트 (다음 스텝에서 사용)
        prediction_history.append((predicted, actual_suffix, is_match))
        # 최근 10개만 유지
        if len(prediction_history) > 10:
            prediction_history.pop(0)
        
        # 통계 업데이트
        if is_match:
            total_matches += 1
            consecutive_matches += 1
            max_consecutive_matches = max(max_consecutive_matches, consecutive_matches)
            
            # 연속 불일치가 5 이상이었는지 확인 (일치 전에 5개 연속이 있었는지)
            if consecutive_mismatches >= 5 and current_consecutive_start is not None:
                end_step = step - 1
                add_or_merge_consecutive_range(current_consecutive_start, end_step, consecutive_5_positions)
            
            # 연속 불일치 리셋
            consecutive_mismatches = 0
            current_consecutive_start = None
            
            # 연속 일치 구간 시작 추적
            if consecutive_matches == 1:
                current_consecutive_match_start = step
        else:
            total_mismatches += 1
            consecutive_mismatches += 1
            max_consecutive_mismatches = max(max_consecutive_mismatches, consecutive_mismatches)
            
            # 연속 일치가 5 이상이었는지 확인 (불일치 전에 5개 연속이 있었는지)
            if consecutive_matches >= 5 and current_consecutive_match_start is not None:
                end_step = step - 1
                add_or_merge_consecutive_range(current_consecutive_match_start, end_step, consecutive_5_match_positions)
            
            # 연속 일치 리셋
            consecutive_matches = 0
            current_consecutive_match_start = None
            
            # 연속 불일치 구간 시작 추적
            if consecutive_mismatches == 1:
                current_consecutive_start = step
        
        # 히스토리 기록 (모든 스텝 기록)
        history.append({
            'step': step,
            'index': index,
            'prefix': prefix,
            'predicted': predicted,
            'actual': actual_suffix,
            'is_match': is_match,
            'confidence': confidence,
            'ratios': ratios,
            'consecutive_mismatches': consecutive_mismatches,
            'consecutive_matches': consecutive_matches
        })
    
    # 마지막에 연속 불일치가 5 이상인지 확인
    if consecutive_mismatches >= 5 and current_consecutive_start is not None:
        end_step = len(history)
        add_or_merge_consecutive_range(current_consecutive_start, end_step, consecutive_5_positions)
    
    # 마지막에 연속 일치가 5 이상인지 확인
    if consecutive_matches >= 5 and current_consecutive_match_start is not None:
        end_step = len(history)
        add_or_merge_consecutive_range(current_consecutive_match_start, end_step, consecutive_5_match_positions)
    
    # 마지막 상태 확인
    ends_with_match = (consecutive_matches > 0 and consecutive_mismatches == 0)
    ends_with_mismatch = (consecutive_mismatches > 0)
    ending_consecutive_mismatches = consecutive_mismatches
    
    # 불일치 상태로 끝나는 경우 처리
    if ends_with_mismatch:
        # skip_ending_mismatch=True면 전체 스킵 (기존 동작)
        if skip_ending_mismatch:
            stats = {
                'total': len(history),
                'matches': total_matches,
                'mismatches': total_mismatches,
                'max_consecutive_mismatches': max_consecutive_mismatches,
                'max_consecutive_matches': max_consecutive_matches,
                'consecutive_5_count': len(consecutive_5_positions),
                'consecutive_5_match_count': len(consecutive_5_match_positions)
            }
            
            return {
                'result': 'skipped_ending_mismatch',
                'ends_with_match': False,
                'ends_with_mismatch': True,
                'ending_consecutive_mismatches': ending_consecutive_mismatches,
                'max_consecutive_mismatches': max_consecutive_mismatches,
                'max_consecutive_matches': max_consecutive_matches,
                'consecutive_5_positions': consecutive_5_positions,
                'consecutive_5_match_positions': consecutive_5_match_positions,
                'history': history,
                'stats': stats,
                'skipped': True,
                'truncated': False,
                'last_match_step': None,
                'truncated_steps': 0
            }
        
        # truncate_at_last_match=True면 마지막 일치 스텝까지만 유효 처리
        if truncate_at_last_match:
            # history를 역순으로 순회하면서 마지막 일치 스텝 찾기
            last_match_step = None
            for i in range(len(history) - 1, -1, -1):
                if history[i]['is_match']:
                    last_match_step = history[i]['step']
                    break
            
            # 일치 스텝이 없으면 전체 스킵
            if last_match_step is None:
                stats = {
                    'total': len(history),
                    'matches': total_matches,
                    'mismatches': total_mismatches,
                    'max_consecutive_mismatches': max_consecutive_mismatches,
                    'max_consecutive_matches': max_consecutive_matches,
                    'consecutive_5_count': len(consecutive_5_positions),
                    'consecutive_5_match_count': len(consecutive_5_match_positions)
                }
                
                return {
                    'result': 'skipped_ending_mismatch',
                    'ends_with_match': False,
                    'ends_with_mismatch': True,
                    'ending_consecutive_mismatches': ending_consecutive_mismatches,
                    'max_consecutive_mismatches': max_consecutive_mismatches,
                    'max_consecutive_matches': max_consecutive_matches,
                    'consecutive_5_positions': consecutive_5_positions,
                    'consecutive_5_match_positions': consecutive_5_match_positions,
                    'history': history,
                    'stats': stats,
                    'skipped': True,
                    'truncated': False,
                    'last_match_step': None,
                    'truncated_steps': 0
                }
            
            # last_match_step까지만 유효한 history 추출
            valid_history = [h for h in history if h['step'] <= last_match_step]
            truncated_steps = len(history) - len(valid_history)
            
            # 통계 재계산
            valid_total_matches = sum(1 for h in valid_history if h['is_match'])
            valid_total_mismatches = sum(1 for h in valid_history if not h['is_match'])
            
            # 연속 불일치/일치 재계산
            valid_consecutive_mismatches = 0
            valid_consecutive_matches = 0
            valid_max_consecutive_mismatches = 0
            valid_max_consecutive_matches = 0
            valid_consecutive_5_positions = []
            valid_consecutive_5_match_positions = []
            valid_current_consecutive_start = None
            valid_current_consecutive_match_start = None
            
            for h in valid_history:
                if h['is_match']:
                    valid_consecutive_matches += 1
                    valid_max_consecutive_matches = max(valid_max_consecutive_matches, valid_consecutive_matches)
                    
                    # 연속 불일치가 5 이상이었는지 확인
                    if valid_consecutive_mismatches >= 5 and valid_current_consecutive_start is not None:
                        end_step = h['step'] - 1
                        add_or_merge_consecutive_range(valid_current_consecutive_start, end_step, valid_consecutive_5_positions)
                    
                    valid_consecutive_mismatches = 0
                    valid_current_consecutive_start = None
                    
                    if valid_consecutive_matches == 1:
                        valid_current_consecutive_match_start = h['step']
                else:
                    valid_consecutive_mismatches += 1
                    valid_max_consecutive_mismatches = max(valid_max_consecutive_mismatches, valid_consecutive_mismatches)
                    
                    # 연속 일치가 5 이상이었는지 확인
                    if valid_consecutive_matches >= 5 and valid_current_consecutive_match_start is not None:
                        end_step = h['step'] - 1
                        add_or_merge_consecutive_range(valid_current_consecutive_match_start, end_step, valid_consecutive_5_match_positions)
                    
                    valid_consecutive_matches = 0
                    valid_current_consecutive_match_start = None
                    
                    if valid_consecutive_mismatches == 1:
                        valid_current_consecutive_start = h['step']
            
            # 마지막에 연속 불일치/일치가 5 이상인지 확인
            if valid_consecutive_mismatches >= 5 and valid_current_consecutive_start is not None:
                end_step = len(valid_history)
                add_or_merge_consecutive_range(valid_current_consecutive_start, end_step, valid_consecutive_5_positions)
            
            if valid_consecutive_matches >= 5 and valid_current_consecutive_match_start is not None:
                end_step = len(valid_history)
                add_or_merge_consecutive_range(valid_current_consecutive_match_start, end_step, valid_consecutive_5_match_positions)
            
            # 결과 결정 (재계산된 통계 기준)
            valid_has_5_consecutive_mismatch = valid_max_consecutive_mismatches >= 5
            valid_has_5_consecutive_match = valid_max_consecutive_matches >= 5
            
            if valid_has_5_consecutive_mismatch or valid_has_5_consecutive_match:
                valid_result = 'has_5_consecutive'
            else:
                valid_result = 'no_5_consecutive'
            
            # 통계 정보 (재계산된 값)
            valid_stats = {
                'total': len(valid_history),
                'matches': valid_total_matches,
                'mismatches': valid_total_mismatches,
                'max_consecutive_mismatches': valid_max_consecutive_mismatches,
                'max_consecutive_matches': valid_max_consecutive_matches,
                'consecutive_5_count': len(valid_consecutive_5_positions),
                'consecutive_5_match_count': len(valid_consecutive_5_match_positions)
            }
            
            # 강제 예측 통계 계산 (유효 범위만)
            result_dict = {
                'result': valid_result,
                'ends_with_match': True,  # 유효 범위는 일치로 끝남
                'ends_with_mismatch': False,
                'ending_consecutive_mismatches': 0,
                'max_consecutive_mismatches': valid_max_consecutive_mismatches,
                'max_consecutive_matches': valid_max_consecutive_matches,
                'consecutive_5_positions': valid_consecutive_5_positions,
                'consecutive_5_match_positions': valid_consecutive_5_match_positions,
                'history': valid_history,  # 잘린 history
                'stats': valid_stats,
                'skipped': False,
                'truncated': True,
                'last_match_step': last_match_step,
                'truncated_steps': truncated_steps
            }
            
            # max_interval 사용 시 추가 통계 (유효 범위만)
            if max_interval is not None:
                # 유효 범위의 강제 예측 통계만 계산
                valid_forced_predictions = 0
                valid_total_predictions = 0
                valid_intervals = []
                
                for h in valid_history:
                    if h.get('predicted') is not None:
                        valid_total_predictions += 1
                        # 강제 예측 여부는 history에 저장되어 있지 않으므로 간단히 처리
                        # 실제로는 simulate_game_scenario 내부에서 추적해야 하지만,
                        # 여기서는 간단히 처리
                
                # 간격 통계는 유효 범위 내에서만 계산
                if valid_intervals:
                    avg_interval = sum(valid_intervals) / len(valid_intervals)
                    max_interval_actual = max(valid_intervals)
                    min_interval = min(valid_intervals)
                else:
                    avg_interval = 0
                    max_interval_actual = 0
                    min_interval = 0
                
                forced_prediction_ratio = (valid_forced_predictions / valid_total_predictions * 100) if valid_total_predictions > 0 else 0
                
                result_dict.update({
                    'forced_predictions': valid_forced_predictions,
                    'forced_prediction_ratio': forced_prediction_ratio,
                    'avg_interval': avg_interval,
                    'max_interval_actual': max_interval_actual,
                    'min_interval': min_interval,
                    'total_predictions': valid_total_predictions
                })
            
            return result_dict
    
    # 결과 결정 (연속 불일치 5회 이상 OR 연속 일치 5회 이상 = 실패)
    has_5_consecutive_mismatch = max_consecutive_mismatches >= 5
    has_5_consecutive_match = max_consecutive_matches >= 5
    
    if has_5_consecutive_mismatch or has_5_consecutive_match:
        result = 'has_5_consecutive'
    else:
        result = 'no_5_consecutive'
    
    # 통계 정보
    stats = {
        'total': len(history),
        'matches': total_matches,
        'mismatches': total_mismatches,
        'max_consecutive_mismatches': max_consecutive_mismatches,
        'max_consecutive_matches': max_consecutive_matches,
        'consecutive_5_count': len(consecutive_5_positions),  # 연속 불일치 5개가 발생한 횟수
        'consecutive_5_match_count': len(consecutive_5_match_positions)  # 연속 일치 5개가 발생한 횟수
    }
    
    # 강제 예측 통계 계산
    result_dict = {
        'result': result,
        'ends_with_match': ends_with_match,
        'ends_with_mismatch': ends_with_mismatch,
        'ending_consecutive_mismatches': ending_consecutive_mismatches,
        'max_consecutive_mismatches': max_consecutive_mismatches,
        'max_consecutive_matches': max_consecutive_matches,
        'consecutive_5_positions': consecutive_5_positions,
        'consecutive_5_match_positions': consecutive_5_match_positions,
        'history': history,
        'stats': stats,
        'skipped': False,
        'truncated': False,
        'last_match_step': None,
        'truncated_steps': 0
    }
    
    # max_interval 사용 시 추가 통계
    if max_interval is not None:
        forced_prediction_ratio = (forced_predictions / total_predictions * 100) if total_predictions > 0 else 0
        avg_interval = sum(intervals) / len(intervals) if intervals else 0
        max_interval_actual = max(intervals) if intervals else 0
        min_interval = min(intervals) if intervals else 0
        
        result_dict.update({
            'forced_predictions': forced_predictions,
            'forced_prediction_ratio': forced_prediction_ratio,
            'avg_interval': avg_interval,
            'max_interval_actual': max_interval_actual,
            'min_interval': min_interval,
            'total_predictions': total_predictions
        })
    
    return result_dict

def test_strategy_on_all_data(strategy_func, strategy_name, df_strings, window_sizes, method="빈도 기반", train_ratio=80):
    """
    특정 전략을 전체 DB 데이터에 대해 시계열 누적 방식으로 테스트
    
    Args:
        strategy_func: 전략 함수 (model, prefix, method) -> dict
        strategy_name: 전략 이름
        df_strings: 전처리된 데이터 DataFrame
        window_sizes: 테스트할 윈도우 크기 리스트
        method: 기본 예측 방법
        train_ratio: 학습 세트 비율 (시계열 누적에서는 사용하지 않음)
    
    Returns:
        dict: {
            window_size: {
                'strategy_name': 전략 이름,
                'total_grid_strings': 전체 grid_string 수,
                'tested_grid_strings': 테스트된 grid_string 수,
                'total_steps': 전체 스텝 수,
                'total_matches': 전체 일치 수,
                'total_mismatches': 전체 불일치 수,
                'avg_accuracy': 평균 정확도,
                'max_consecutive_mismatches': 최대 연속 불일치 수,
                'max_consecutive_matches': 최대 연속 일치 수,
                'total_consecutive_5_count': 전체 연속 불일치 5개 발생 횟수,
                'total_consecutive_5_match_count': 전체 연속 일치 5개 발생 횟수,
                'grid_string_results': [각 grid_string별 결과],
                'all_histories': [모든 grid_string의 history 리스트]  # 신뢰도 통계 분석용
            }
        }
    """
    # created_at 오름차순으로 정렬 (과거 → 현재)
    df_sorted = df_strings.sort_values('created_at').reset_index(drop=True)
    
    results_by_window = {}
    
    for window_size in window_sizes:
        window_results = {
            'strategy_name': strategy_name,
            'total_grid_strings': len(df_sorted),
            'tested_grid_strings': 0,
            'valid_test_count': 0,  # 유효한 테스트 케이스 수
            'skipped_count': 0,  # 스킵된 케이스 수
            'ending_mismatch_count': 0,  # 불일치 상태로 끝난 케이스 수
            'total_steps': 0,
            'total_matches': 0,
            'total_mismatches': 0,
            'max_consecutive_mismatches': 0,
            'max_consecutive_matches': 0,
            'total_consecutive_5_count': 0,
            'total_consecutive_5_match_count': 0,
            'grid_string_results': [],
            'all_histories': [],  # 신뢰도 통계 분석용 history 수집
            # 강제 예측 통계 (max_interval 사용 시)
            'total_forced_predictions': 0,
            'total_all_predictions': 0,
            'all_intervals': []
        }
        
        # 시계열 순서대로 각 grid_string 테스트
        for idx, row in df_sorted.iterrows():
            current_grid_string = row['grid_string']
            current_id = row['id']
            
            # 현재 grid_string 길이 검증
            if len(current_grid_string) < window_size:
                continue
            
            # 이전까지의 모든 grid_string ID (현재 제외)
            previous_ids = df_sorted.iloc[:idx]['id'].tolist()
            
            # 이전 데이터가 없으면 첫 번째 grid_string은 스킵 (학습 데이터 없음)
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
                # elif method == "마르코프 체인":
                #     model = build_markov_model(train_ngrams)
                elif method == "가중치 기반":
                    model = build_weighted_model(train_ngrams)
                elif method == "안전 우선":
                    model = build_safety_first_model(train_ngrams)
                else:
                    model = build_frequency_model(train_ngrams)
                
                # 전략 함수를 사용하여 게임 시뮬레이션 실행
                game_result = simulate_game_scenario(
                    model,
                    current_grid_string,
                    window_size,
                    method,
                    strategy_func=strategy_func,
                    skip_ending_mismatch=True
                )
                
                # 스킵된 케이스 처리
                if game_result.get('skipped', False):
                    window_results['skipped_count'] += 1
                    window_results['tested_grid_strings'] += 1  # 테스트는 했지만 스킵됨
                    if game_result.get('ends_with_mismatch', False):
                        window_results['ending_mismatch_count'] += 1
                    continue  # 스킵된 케이스는 통계에서 제외
                
                # 잘린 케이스 처리
                if game_result.get('truncated', False):
                    window_results['truncated_count'] += 1
                    window_results['total_truncated_steps'] += game_result.get('truncated_steps', 0)
                
                # 유효한 테스트 케이스
                window_results['valid_test_count'] += 1
                window_results['tested_grid_strings'] += 1
                
                # history 수집 (신뢰도 통계 분석용 - 중복 계산 방지)
                if game_result.get('history'):
                    window_results['all_histories'].append(game_result['history'])
                
                # 결과 집계
                stats = game_result['stats']
                window_results['total_steps'] += stats['total']
                window_results['total_matches'] += stats['matches']
                window_results['total_mismatches'] += stats['mismatches']
                window_results['max_consecutive_mismatches'] = max(
                    window_results['max_consecutive_mismatches'],
                    game_result['max_consecutive_mismatches']
                )
                window_results['max_consecutive_matches'] = max(
                    window_results['max_consecutive_matches'],
                    game_result['max_consecutive_matches']
                )
                window_results['total_consecutive_5_count'] += stats['consecutive_5_count']
                window_results['total_consecutive_5_match_count'] += stats.get('consecutive_5_match_count', 0)
                
                # 강제 예측 통계 수집 (max_interval 사용 시)
                if game_result.get('forced_predictions', 0) > 0:
                    window_results['total_forced_predictions'] += game_result.get('forced_predictions', 0)
                    window_results['total_all_predictions'] += game_result.get('total_predictions', 0)
                    if game_result.get('avg_interval', 0) > 0:
                        window_results['all_intervals'].append(game_result.get('avg_interval', 0))
                
                # 각 grid_string별 결과 저장
                accuracy = (stats['matches'] / stats['total'] * 100) if stats['total'] > 0 else 0
                window_results['grid_string_results'].append({
                    'grid_string_id': current_id,
                    'grid_string_length': len(current_grid_string),
                    'steps': stats['total'],
                    'matches': stats['matches'],
                    'mismatches': stats['mismatches'],
                    'accuracy': accuracy,
                    'max_consecutive_mismatches': game_result['max_consecutive_mismatches'],
                    'max_consecutive_matches': game_result['max_consecutive_matches'],
                    'consecutive_5_count': stats['consecutive_5_count'],
                    'consecutive_5_match_count': stats.get('consecutive_5_match_count', 0)
                })
                
            except Exception as e:
                # 에러 발생 시 해당 grid_string 스킵
                continue
        
        # 평균 정확도 계산
        if window_results['total_steps'] > 0:
            window_results['avg_accuracy'] = (window_results['total_matches'] / window_results['total_steps'] * 100)
        else:
            window_results['avg_accuracy'] = 0
        
        results_by_window[window_size] = window_results
    
    return results_by_window

def analyze_confidence_statistics(history_list, threshold=70):
    """
    신뢰도 수준별 통계 분석
    
    Args:
        history_list: 모든 grid_string의 history 리스트 (각 history는 simulate_game_scenario의 history)
        threshold: 분석할 임계값
    
    Returns:
        dict: 신뢰도 통계 정보
    """
    all_confidences = []
    high_confidence_steps = []  # 임계값 이상인 예측의 step 위치
    confidence_intervals = []  # 임계값 이상 예측 간 간격
    
    # 신뢰도 구간별 카운트
    confidence_bins = {
        '0-50': 0,
        '50-60': 0,
        '60-70': 0,
        '70-80': 0,
        '80-90': 0,
        '90-100': 0
    }
    
    # 모든 history에서 신뢰도 수집
    total_steps = 0  # 전체 스텝 수 (예측 여부와 관계없이)
    abstained_steps = []  # 예측을 하지 않은 스텝들
    all_steps = []  # 모든 스텝 (예측 여부와 관계없이)
    
    for history in history_list:
        for entry in history:
            step = entry.get('step', 0)
            total_steps += 1
            all_steps.append(step)
            
            # 예측을 하지 않은 경우 (abstained)
            if entry.get('is_abstained', False) or entry.get('predicted') is None:
                abstained_steps.append(step)
                continue  # 예측을 하지 않은 경우는 신뢰도 통계에 포함하지 않음
            
            confidence = entry.get('confidence', 0.0)
            
            if confidence is not None:
                all_confidences.append(confidence)
                
                # 구간별 카운트
                if confidence < 50:
                    confidence_bins['0-50'] += 1
                elif confidence < 60:
                    confidence_bins['50-60'] += 1
                elif confidence < 70:
                    confidence_bins['60-70'] += 1
                elif confidence < 80:
                    confidence_bins['70-80'] += 1
                elif confidence < 90:
                    confidence_bins['80-90'] += 1
                else:
                    confidence_bins['90-100'] += 1
                
                # 임계값 이상인 예측 추적
                if confidence >= threshold:
                    high_confidence_steps.append(step)
    
    # 임계값 이상 예측 간 간격 계산 (모든 스텝을 고려)
    # 간격 = 임계값 이상 예측 사이의 스텝 수 (예측하지 않은 스텝도 포함)
    if len(high_confidence_steps) > 1:
        high_confidence_steps_sorted = sorted(high_confidence_steps)
        for i in range(1, len(high_confidence_steps_sorted)):
            # 두 임계값 이상 예측 사이의 step 차이 (중간에 예측하지 않은 스텝도 포함)
            interval = high_confidence_steps_sorted[i] - high_confidence_steps_sorted[i-1]
            confidence_intervals.append(interval)
    
    # 첫 번째 임계값 이상 예측까지의 간격도 계산
    if len(high_confidence_steps) > 0 and len(all_steps) > 0:
        first_high_step = min(high_confidence_steps)
        first_step = min(all_steps)
        if first_high_step > first_step:
            # 첫 번째 예측 전까지의 간격도 추가
            confidence_intervals.append(first_high_step - first_step)
    
    # 마지막 임계값 이상 예측 이후의 간격도 계산 (선택사항)
    # 이건 현재 구현에서는 제외 (마지막 예측 이후는 대기 시간이 중요하지 않을 수 있음)
    
    # 통계 계산
    total_predictions = len(all_confidences)  # 실제로 예측을 수행한 횟수
    total_abstained = len(abstained_steps)  # 예측을 하지 않은 횟수
    high_confidence_count = len(high_confidence_steps)
    
    # 전체 스텝 대비 임계값 이상 예측 비율 (실제 예측한 것 중에서)
    high_confidence_ratio = (high_confidence_count / total_predictions * 100) if total_predictions > 0 else 0
    
    # 전체 스텝 대비 예측 수행 비율
    prediction_ratio = (total_predictions / total_steps * 100) if total_steps > 0 else 0
    
    # 전체 스텝 대비 임계값 이상 예측 비율 (모든 스텝 기준)
    high_confidence_ratio_overall = (high_confidence_count / total_steps * 100) if total_steps > 0 else 0
    
    avg_confidence = sum(all_confidences) / len(all_confidences) if all_confidences else 0
    min_confidence = min(all_confidences) if all_confidences else 0
    max_confidence = max(all_confidences) if all_confidences else 0
    
    avg_interval = sum(confidence_intervals) / len(confidence_intervals) if confidence_intervals else 0
    max_interval = max(confidence_intervals) if confidence_intervals else 0
    min_interval = min(confidence_intervals) if confidence_intervals else 0
    
    return {
        'total_steps': total_steps,  # 전체 스텝 수
        'total_predictions': total_predictions,  # 실제로 예측을 수행한 횟수
        'total_abstained': total_abstained,  # 예측을 하지 않은 횟수
        'prediction_ratio': prediction_ratio,  # 전체 스텝 대비 예측 수행 비율
        'high_confidence_count': high_confidence_count,
        'high_confidence_ratio': high_confidence_ratio,  # 예측한 것 중 임계값 이상 비율
        'high_confidence_ratio_overall': high_confidence_ratio_overall,  # 전체 스텝 대비 임계값 이상 비율
        'confidence_bins': confidence_bins,
        'avg_confidence': avg_confidence,
        'min_confidence': min_confidence,
        'max_confidence': max_confidence,
        'avg_interval': avg_interval,
        'max_interval': max_interval,
        'min_interval': min_interval,
        'confidence_intervals': confidence_intervals,
        'threshold': threshold
    }

def analyze_confidence_statistics_by_window(df_strings, window_size, threshold, method="빈도 기반", train_ratio=80, use_threshold_strategy=True):
    """
    특정 윈도우 크기와 임계값에 대해 신뢰도 통계 분석
    
    Args:
        df_strings: 전처리된 데이터 DataFrame
        window_size: 윈도우 크기
        threshold: 임계값
        method: 기본 예측 방법
        train_ratio: 학습 세트 비율
        use_threshold_strategy: 임계값 전략 사용 여부 (False면 모든 예측 포함)
    
    Returns:
        dict: 신뢰도 통계 정보
    """
    # created_at 오름차순으로 정렬
    df_sorted = df_strings.sort_values('created_at').reset_index(drop=True)
    
    all_histories = []
    
    # 시계열 순서대로 각 grid_string 테스트하여 history 수집
    for idx, row in df_sorted.iterrows():
        current_grid_string = row['grid_string']
        
        if len(current_grid_string) < window_size:
            continue
        
        previous_ids = df_sorted.iloc[:idx]['id'].tolist()
        
        if len(previous_ids) == 0:
            continue
        
        try:
            train_ngrams = load_ngram_chunks(window_size=window_size, grid_string_ids=previous_ids)
            
            if len(train_ngrams) == 0:
                continue
            
            # 모델 구축
            if method == "빈도 기반":
                model = build_frequency_model(train_ngrams)
            # elif method == "마르코프 체인":
            #     model = build_markov_model(train_ngrams)
            elif method == "가중치 기반":
                model = build_weighted_model(train_ngrams)
            elif method == "안전 우선":
                model = build_safety_first_model(train_ngrams)
            else:
                model = build_frequency_model(train_ngrams)
            
            # 전략 함수 설정
            if use_threshold_strategy:
                # 임계값 전략 함수
                strategy_func = lambda m, p, method: predict_confidence_threshold(m, p, method, threshold=threshold)
            else:
                # 임계값 없이 모든 예측 포함
                strategy_func = None
            
            # 게임 시뮬레이션 실행
            game_result = simulate_game_scenario(
                model,
                current_grid_string,
                window_size,
                method,
                strategy_func=strategy_func,
                skip_ending_mismatch=False,  # truncate_at_last_match 사용
                truncate_at_last_match=True
            )
            
            # history 수집 (모든 history 포함 - 예측하지 않은 경우도 포함)
            if game_result['history']:
                all_histories.append(game_result['history'])
                
        except Exception as e:
            continue
    
    # 신뢰도 통계 분석
    if all_histories:
        return analyze_confidence_statistics(all_histories, threshold)
    else:
        return {
            'total_steps': 0,
            'total_predictions': 0,
            'total_abstained': 0,
            'prediction_ratio': 0,
            'high_confidence_count': 0,
            'high_confidence_ratio': 0,
            'high_confidence_ratio_overall': 0,
            'confidence_bins': {},
            'avg_confidence': 0,
            'min_confidence': 0,
            'max_confidence': 0,
            'avg_interval': 0,
            'max_interval': 0,
            'min_interval': 0,
            'confidence_intervals': [],
            'threshold': threshold
        }

def find_optimal_combination_for_new_data(
    cutoff_grid_string_id,
    window_sizes,
    thresholds,
    method="빈도 기반",
    use_stored_predictions=True,
    max_intervals=None
):
    """
    새로운 데이터만으로 최적 조합 찾기
    
    Args:
        cutoff_grid_string_id: 기준이 되는 grid_string_id (이 ID 이후가 새로운 데이터)
        window_sizes: 테스트할 윈도우 크기 리스트
        thresholds: 테스트할 임계값 리스트
        method: 기본 예측 방법
        use_stored_predictions: True면 DB 테이블에서 조회, False면 실시간 계산
        max_intervals: 테스트할 최대 간격 리스트 (None이면 강제 예측 사용 안 함)
    
    Returns:
        list: [{
            'window_size': 윈도우 크기,
            'threshold': 임계값,
            'max_interval': 최대 간격 (None이면 사용 안 함),
            'max_consecutive_mismatches': 최대 연속 불일치 수,
            'max_consecutive_matches': 최대 연속 일치 수,
            'total_consecutive_5_count': 연속 불일치 5회 이상 횟수,
            'total_consecutive_5_match_count': 연속 일치 5회 이상 횟수,
            'total_failures': 총 실패 횟수,
            'max_failures': 최대 실패 지표,
            'avg_accuracy': 평균 정확도,
            'tested_grid_strings': 테스트된 grid_string 수,
            'forced_predictions': 강제 예측 수 (max_interval 사용 시),
            'forced_prediction_ratio': 강제 예측 비율 (max_interval 사용 시),
            'avg_interval': 평균 간격 (max_interval 사용 시),
            'max_interval_actual': 실제 최대 간격 (max_interval 사용 시),
            'confidence_stats': 신뢰도 통계
        }, ...]
    """
    conn = get_db_connection()
    if conn is None:
        return {}
    
    try:
        # 이전 데이터 선택 (id <= cutoff_grid_string_id)
        df_historical = pd.read_sql_query(
            "SELECT id FROM preprocessed_grid_strings WHERE id <= ? ORDER BY id",
            conn,
            params=[cutoff_grid_string_id]
        )
        historical_ids = df_historical['id'].tolist()
        
        # 새로운 데이터 선택 (id > cutoff_grid_string_id)
        df_new = pd.read_sql_query(
            "SELECT id, grid_string, created_at FROM preprocessed_grid_strings WHERE id > ? ORDER BY id",
            conn,
            params=[cutoff_grid_string_id]
        )
        
        if len(df_new) == 0:
            return {}
        
        all_combination_results = []
        
        # 이전 데이터로 모델 구축 (한 번만)
        models_by_window = {}
        for window_size in window_sizes:
            train_ngrams = load_ngram_chunks(window_size=window_size, grid_string_ids=historical_ids)
            
            if len(train_ngrams) == 0:
                continue
            
            if method == "빈도 기반":
                models_by_window[window_size] = build_frequency_model(train_ngrams)
            # elif method == "마르코프 체인":
            #     models_by_window[window_size] = build_markov_model(train_ngrams)
            elif method == "가중치 기반":
                models_by_window[window_size] = build_weighted_model(train_ngrams)
            elif method == "안전 우선":
                models_by_window[window_size] = build_safety_first_model(train_ngrams)
            else:
                models_by_window[window_size] = build_frequency_model(train_ngrams)
        
        # 각 윈도우 크기 × 임계값 × max_interval 조합 테스트
        # max_intervals가 None이면 [None]으로 처리 (강제 예측 사용 안 함)
        test_max_intervals = max_intervals if max_intervals is not None else [None]
        
        for window_size in window_sizes:
            if window_size not in models_by_window:
                continue
            
            model = models_by_window[window_size]
            
            for threshold in thresholds:
                for max_interval in test_max_intervals:
                    # 새로운 데이터에 대해 테스트
                    all_histories = []
                    total_steps = 0
                    total_matches = 0
                    total_mismatches = 0
                    max_consecutive_mismatches = 0
                    max_consecutive_matches = 0
                    total_consecutive_5_count = 0
                    total_consecutive_5_match_count = 0
                    tested_grid_strings = 0
                    skipped_count = 0
                    valid_test_count = 0
                    ending_mismatch_count = 0
                    total_forced_predictions = 0
                    total_all_predictions = 0
                    all_intervals = []
                    
                    for _, row in df_new.iterrows():
                        grid_string_id = row['id']
                        grid_string = row['grid_string']
                        
                        if len(grid_string) < window_size:
                            continue
                        
                        # 전략 함수 생성 (max_interval 사용 시 strategy_func는 None)
                        if max_interval is not None:
                            # max_interval 사용 시 simulate_game_scenario 내부에서 predict_with_fallback_interval 사용
                            strategy_func = None
                        elif threshold == 0:
                            strategy_func = None
                        else:
                            strategy_func = lambda m, p, method: predict_confidence_threshold(m, p, method, threshold)
                        
                        # 게임 시뮬레이션 실행
                        game_result = simulate_game_scenario(
                            model,
                            grid_string,
                            window_size,
                            method,
                            strategy_func=strategy_func,
                            skip_ending_mismatch=False,  # 모든 grid_string을 유효 테스트로 처리
                            max_interval=max_interval,
                            threshold=threshold,
                            truncate_at_last_match=False  # 모든 스텝을 유효 테스트로 처리
                        )
                    
                    # 스킵된 케이스 처리
                    if game_result.get('skipped', False):
                        skipped_count += 1
                        tested_grid_strings += 1
                        if game_result.get('ends_with_mismatch', False):
                            ending_mismatch_count += 1
                        continue  # 스킵된 케이스는 통계에서 제외
                    
                    # 유효한 테스트 케이스
                    valid_test_count += 1
                    tested_grid_strings += 1
                    
                    # history 수집
                    if game_result.get('history'):
                        all_histories.append(game_result['history'])
                    
                    # 통계 집계
                    stats = game_result['stats']
                    total_steps += stats['total']
                    total_matches += stats['matches']
                    total_mismatches += stats['mismatches']
                    max_consecutive_mismatches = max(max_consecutive_mismatches, game_result['max_consecutive_mismatches'])
                    max_consecutive_matches = max(max_consecutive_matches, game_result['max_consecutive_matches'])
                    total_consecutive_5_count += stats['consecutive_5_count']
                    total_consecutive_5_match_count += stats.get('consecutive_5_match_count', 0)
                    
                    # 강제 예측 통계 수집 (max_interval 사용 시)
                    if max_interval is not None:
                        total_forced_predictions += game_result.get('forced_predictions', 0)
                        total_all_predictions += game_result.get('total_predictions', 0)
                        # 간격 통계 수집
                        if game_result.get('avg_interval', 0) > 0:
                            all_intervals.append(game_result.get('avg_interval', 0))
                    
                    # 신뢰도 통계 분석
                    if all_histories:
                        confidence_stats = analyze_confidence_statistics(all_histories, threshold)
                    else:
                        confidence_stats = {
                            'total_steps': 0,
                            'total_predictions': 0,
                            'total_abstained': 0,
                            'prediction_ratio': 0,
                            'high_confidence_count': 0,
                            'high_confidence_ratio': 0,
                            'high_confidence_ratio_overall': 0,
                            'confidence_bins': {},
                            'avg_confidence': 0,
                            'min_confidence': 0,
                            'max_confidence': 0,
                            'avg_interval': 0,
                            'max_interval': 0,
                            'min_interval': 0,
                            'confidence_intervals': [],
                            'threshold': threshold
                        }
                    
                    # 성능 지표 계산
                    avg_accuracy = (total_matches / total_steps * 100) if total_steps > 0 else 0
                    total_failures = total_consecutive_5_count + total_consecutive_5_match_count
                    max_failures = max(max_consecutive_mismatches, max_consecutive_matches)
                    
                    # 스킵 통계 계산
                    total_count = len(df_new[df_new['grid_string'].str.len() >= window_size])
                    skipped_ratio = (skipped_count / total_count * 100) if total_count > 0 else 0
                    valid_ratio = (valid_test_count / total_count * 100) if total_count > 0 else 0
                    
                    # 강제 예측 통계 계산
                    forced_prediction_ratio = (total_forced_predictions / total_all_predictions * 100) if total_all_predictions > 0 else 0
                    avg_interval_overall = sum(all_intervals) / len(all_intervals) if all_intervals else 0
                    
                    result_dict = {
                        'window_size': window_size,
                        'threshold': threshold,
                        'max_interval': max_interval,
                        'max_consecutive_mismatches': max_consecutive_mismatches,
                        'max_consecutive_matches': max_consecutive_matches,
                        'total_consecutive_5_count': total_consecutive_5_count,
                        'total_consecutive_5_match_count': total_consecutive_5_match_count,
                        'total_failures': total_failures,
                        'max_failures': max_failures,
                        'avg_accuracy': avg_accuracy,
                        'tested_grid_strings': tested_grid_strings,
                        'valid_test_count': valid_test_count,
                        'skipped_count': skipped_count,
                        'ending_mismatch_count': ending_mismatch_count,
                        'skipped_ratio': skipped_ratio,
                        'valid_ratio': valid_ratio,
                        'confidence_stats': confidence_stats
                    }
                    
                    # max_interval 사용 시 추가 통계
                    if max_interval is not None:
                        result_dict.update({
                            'forced_predictions': total_forced_predictions,
                            'forced_prediction_ratio': forced_prediction_ratio,
                            'avg_interval': avg_interval_overall,
                            'total_predictions': total_all_predictions
                        })
                    
                    all_combination_results.append(result_dict)
        
        return all_combination_results
        
    except Exception as e:
        st.error(f"최적 조합 찾기 오류: {str(e)}")
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
        return []
    finally:
        conn.close()

def calculate_optimal_score(combination_result, min_prediction_ratio=20, forced_prediction_weight=2.0):
    """
    최적 조합 선택을 위한 점수 계산
    
    예측 빈도와 실패 지표를 균형있게 고려하는 점수 시스템
    
    Args:
        combination_result: 조합 결과 딕셔너리
        min_prediction_ratio: 최소 예측 빈도 (기본값: 20%)
        forced_prediction_weight: 강제 예측 페널티 가중치 (기본값: 2.0)
    
    Returns:
        float: 점수 (낮을수록 좋음)
    """
    # 실패 페널티
    failure_penalty = combination_result['max_failures'] * 100 + combination_result['total_failures'] * 10
    
    # 예측 빈도 보너스/페널티
    conf_stats = combination_result.get('confidence_stats', {})
    prediction_ratio = conf_stats.get('high_confidence_ratio_overall', 0)
    
    if prediction_ratio < min_prediction_ratio:
        # 최소 예측 빈도 미만이면 큰 페널티
        prediction_penalty = (min_prediction_ratio - prediction_ratio) * 50
    else:
        # 최소 예측 빈도 이상이면 보너스 (너무 높아도 의미 없으므로 제한)
        prediction_bonus = -min((prediction_ratio - min_prediction_ratio) * 2, 100)  # 최대 보너스 제한
        prediction_penalty = prediction_bonus
    
    # 강제 예측 페널티 (max_interval 사용 시)
    forced_penalty = 0
    if 'forced_prediction_ratio' in combination_result:
        forced_penalty = combination_result['forced_prediction_ratio'] * forced_prediction_weight
    
    total_score = failure_penalty + prediction_penalty + forced_penalty
    
    return total_score

def batch_test_window_sizes_on_all_data(df_strings, window_sizes, method="빈도 기반", train_ratio=80):
    """
    DB 전체 grid_string에 대해 시계열 누적 방식으로 여러 윈도우 크기를 배치 테스트
    
    시계열 누적 방식:
    - created_at 오름차순으로 정렬 (과거 → 현재)
    - 각 grid_string에 대해:
      - 이전까지의 모든 grid_string의 ngram_chunks로 모델 구축
      - 현재 grid_string을 테스트
      - 결과 수집
    
    Args:
        df_strings: 전처리된 데이터 DataFrame (created_at 오름차순 정렬 필요)
        window_sizes: 테스트할 윈도우 크기 리스트
        method: 예측 방법
        train_ratio: 학습 세트 비율 (시계열 누적에서는 사용하지 않음)
    
    Returns:
        dict: {
            window_size: {
                'total_grid_strings': 전체 grid_string 수,
                'tested_grid_strings': 테스트된 grid_string 수,
                'total_steps': 전체 스텝 수,
                'total_matches': 전체 일치 수,
                'total_mismatches': 전체 불일치 수,
                'avg_accuracy': 평균 정확도,
                'max_consecutive_mismatches': 최대 연속 불일치 수 (전체 중),
                'total_consecutive_5_count': 전체 연속 불일치 5개 발생 횟수,
                'grid_string_results': [각 grid_string별 결과]
            }
        }
    """
    # created_at 오름차순으로 정렬 (과거 → 현재)
    df_sorted = df_strings.sort_values('created_at').reset_index(drop=True)
    
    results_by_window = {}
    
    for window_size in window_sizes:
        window_results = {
            'total_grid_strings': len(df_sorted),
            'tested_grid_strings': 0,
            'total_steps': 0,
            'total_matches': 0,
            'total_mismatches': 0,
            'max_consecutive_mismatches': 0,
            'total_consecutive_5_count': 0,
            'grid_string_results': []
        }
        
        # 시계열 순서대로 각 grid_string 테스트
        for idx, row in df_sorted.iterrows():
            current_grid_string = row['grid_string']
            current_id = row['id']
            
            # 현재 grid_string 길이 검증
            if len(current_grid_string) < window_size:
                continue
            
            # 이전까지의 모든 grid_string ID (현재 제외)
            previous_ids = df_sorted.iloc[:idx]['id'].tolist()
            
            # 이전 데이터가 없으면 첫 번째 grid_string은 스킵 (학습 데이터 없음)
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
                # elif method == "마르코프 체인":
                #     model = build_markov_model(train_ngrams)
                elif method == "가중치 기반":
                    model = build_weighted_model(train_ngrams)
                elif method == "안전 우선":
                    model = build_safety_first_model(train_ngrams)
                else:
                    model = build_frequency_model(train_ngrams)
                
                # 현재 grid_string 테스트
                game_result = simulate_game_scenario(
                    model,
                    current_grid_string,
                    window_size,
                    method
                )
                
                # 결과 집계
                stats = game_result['stats']
                window_results['tested_grid_strings'] += 1
                window_results['total_steps'] += stats['total']
                window_results['total_matches'] += stats['matches']
                window_results['total_mismatches'] += stats['mismatches']
                window_results['max_consecutive_mismatches'] = max(
                    window_results['max_consecutive_mismatches'],
                    game_result['max_consecutive_mismatches']
                )
                window_results['total_consecutive_5_count'] += stats['consecutive_5_count']
                
                # 각 grid_string별 결과 저장
                accuracy = (stats['matches'] / stats['total'] * 100) if stats['total'] > 0 else 0
                window_results['grid_string_results'].append({
                    'grid_string_id': current_id,
                    'grid_string_length': len(current_grid_string),
                    'steps': stats['total'],
                    'matches': stats['matches'],
                    'mismatches': stats['mismatches'],
                    'accuracy': accuracy,
                    'max_consecutive_mismatches': game_result['max_consecutive_mismatches'],
                    'consecutive_5_count': stats['consecutive_5_count']
                })
                
            except Exception as e:
                # 에러 발생 시 해당 grid_string 스킵
                continue
        
        # 평균 정확도 계산
        if window_results['total_steps'] > 0:
            window_results['avg_accuracy'] = (window_results['total_matches'] / window_results['total_steps'] * 100)
        else:
            window_results['avg_accuracy'] = 0
        
        results_by_window[window_size] = window_results
    
    return results_by_window

def batch_test_strategies(strategies, df_strings, window_sizes, method="빈도 기반", train_ratio=80):
    """
    여러 전략을 한 번에 테스트
    
    Args:
        strategies: 전략 리스트 [(strategy_func, strategy_name), ...]
        df_strings: 전처리된 데이터 DataFrame
        window_sizes: 테스트할 윈도우 크기 리스트
        method: 기본 예측 방법
        train_ratio: 학습 세트 비율
    
    Returns:
        dict: {
            strategy_name: {
                window_size: {
                    'strategy_name': 전략 이름,
                    'total_grid_strings': 전체 grid_string 수,
                    'tested_grid_strings': 테스트된 grid_string 수,
                    ...
                }
            }
        }
    """
    all_results = {}
    
    for strategy_func, strategy_name in strategies:
        strategy_results = test_strategy_on_all_data(
            strategy_func,
            strategy_name,
            df_strings,
            window_sizes,
            method,
            train_ratio
        )
        all_results[strategy_name] = strategy_results
    
    return all_results

def display_window_size_comparison_all_data(results_by_window):
    """
    전체 DB 데이터에 대한 윈도우 크기별 비교 결과를 테이블로 표시하고 최적 윈도우 크기 추천
    
    Args:
        results_by_window: batch_test_window_sizes_on_all_data의 반환값
    """
    if not results_by_window:
        st.warning("⚠️ 비교할 결과가 없습니다.")
        return
    
    # 비교 테이블 데이터 생성
    comparison_data = []
    for window_size, result in results_by_window.items():
        # 스킵 통계 계산
        total_count = result.get('total_grid_strings', 0)
        skipped_count = result.get('skipped_count', 0)
        valid_count = result.get('valid_test_count', 0)
        skipped_ratio = (skipped_count / total_count * 100) if total_count > 0 else 0
        valid_ratio = (valid_count / total_count * 100) if total_count > 0 else 0
        
        comparison_data.append({
            '윈도우 크기': window_size,
            '유효 테스트 수': valid_count,
            '스킵 수': skipped_count,
            '스킵 비율 (%)': f"{skipped_ratio:.1f}",
            '테스트된 Grid 수': result['tested_grid_strings'],
            '전체 Grid 수': result['total_grid_strings'],
            '최대 연속 불일치': result['max_consecutive_mismatches'],
            '전체 연속 불일치 5개 횟수': result['total_consecutive_5_count'],
            '평균 정확도 (%)': f"{result['avg_accuracy']:.2f}",
            '전체 스텝 수': result['total_steps'],
            '전체 일치 수': result['total_matches'],
            '전체 불일치 수': result['total_mismatches']
        })
    
    # 최대 연속 불일치 수 기준으로 정렬 (오름차순)
    comparison_data.sort(key=lambda x: x['최대 연속 불일치'])
    
    # 비교 테이블 표시
    st.markdown("### 📊 윈도우 크기별 전체 DB 테스트 결과")
    comparison_df = pd.DataFrame(comparison_data)
    st.dataframe(comparison_df, use_container_width=True, hide_index=True)
    
    # 최적 윈도우 크기 추천
    st.markdown("---")
    st.markdown("### 🎯 최적 윈도우 크기 추천")
    
    # 최대 연속 불일치 수가 가장 적은 것 찾기
    best_by_max_consecutive = min(results_by_window.items(), key=lambda x: x[1]['max_consecutive_mismatches'])
    best_window_size = best_by_max_consecutive[0]
    best_max_consecutive = best_by_max_consecutive[1]['max_consecutive_mismatches']
    
    # 동일한 최대 연속 불일치 수를 가진 것들 찾기
    candidates = [(w, r) for w, r in results_by_window.items() if r['max_consecutive_mismatches'] == best_max_consecutive]
    
    if len(candidates) == 1:
        # 단일 최적값
        best_result = best_by_max_consecutive[1]
        st.success(f"✅ **최적 윈도우 크기: {best_window_size}**")
        
        # 스킵 통계 표시
        total_count = best_result.get('total_grid_strings', 0)
        skipped_count = best_result.get('skipped_count', 0)
        valid_count = best_result.get('valid_test_count', 0)
        skipped_ratio = (skipped_count / total_count * 100) if total_count > 0 else 0
        valid_ratio = (valid_count / total_count * 100) if total_count > 0 else 0
        
        st.info(f"""
        **테스트 케이스 통계:**
        - 전체 Grid String: {total_count}개
        - 유효한 테스트 케이스: {valid_count}개 ({valid_ratio:.1f}%)
        - 스킵된 케이스: {skipped_count}개 ({skipped_ratio:.1f}%)
          - 불일치 상태로 종료: {best_result.get('ending_mismatch_count', 0)}개
        """)
        
        st.info(f"""
        **성능 지표:**
        - 최대 연속 불일치: {best_result['max_consecutive_mismatches']}개
        - 전체 연속 불일치 5개 횟수: {best_result['total_consecutive_5_count']}회
        - 평균 정확도: {best_result['avg_accuracy']:.2f}%
        - 테스트된 Grid 수: {best_result['tested_grid_strings']}/{total_count}
        """)
    else:
        # 동점인 경우 추가 기준 적용
        # 1순위: 연속 불일치 5개 횟수가 적은 것
        best_by_consecutive_5 = min(candidates, key=lambda x: x[1]['total_consecutive_5_count'])
        best_consecutive_5 = best_by_consecutive_5[1]['total_consecutive_5_count']
        
        # 2순위: 정확도가 높은 것
        final_candidates = [(w, r) for w, r in candidates if r['total_consecutive_5_count'] == best_consecutive_5]
        best = max(final_candidates, key=lambda x: x[1]['avg_accuracy'])
        
        best_window_size = best[0]
        best_result = best[1]
        
        st.success(f"✅ **최적 윈도우 크기: {best_window_size}** (동점자 중 선택)")
        st.info(f"""
        - 최대 연속 불일치: {best_result['max_consecutive_mismatches']}개
        - 전체 연속 불일치 5개 횟수: {best_result['total_consecutive_5_count']}회
        - 평균 정확도: {best_result['avg_accuracy']:.2f}%
        - 테스트된 Grid 수: {best_result['tested_grid_strings']}/{best_result['total_grid_strings']}
        """)
        
        if len(candidates) > 1:
            st.warning(f"⚠️ {len(candidates)}개의 윈도우 크기가 동일한 최대 연속 불일치 수({best_max_consecutive})를 가집니다.")
    
    # 상세 결과 표시 (확장 가능한 섹션)
    st.markdown("---")
    with st.expander("📋 각 윈도우 크기별 상세 결과"):
        for window_size, result in sorted(results_by_window.items()):
            st.markdown(f"#### 윈도우 크기: {window_size}")
            
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("최대 연속 불일치", result['max_consecutive_mismatches'])
            with col2:
                st.metric("연속 불일치 5개", result['total_consecutive_5_count'])
            with col3:
                st.metric("평균 정확도", f"{result['avg_accuracy']:.2f}%")
            with col4:
                st.metric("테스트된 Grid", f"{result['tested_grid_strings']}/{result['total_grid_strings']}")
            
            # Grid String별 결과 요약
            if result['grid_string_results']:
                st.markdown("**Grid String별 결과 요약:**")
                grid_summary = []
                for gr in result['grid_string_results']:
                    grid_summary.append({
                        'Grid ID': gr['grid_string_id'],
                        '길이': gr['grid_string_length'],
                        '스텝': gr['steps'],
                        '정확도': f"{gr['accuracy']:.2f}%",
                        '최대 연속 불일치': gr['max_consecutive_mismatches'],
                        '연속 불일치 5개': gr['consecutive_5_count']
                    })
                
                grid_summary_df = pd.DataFrame(grid_summary)
                st.dataframe(grid_summary_df, use_container_width=True, hide_index=True)
            
            st.markdown("---")

def display_strategy_comparison(strategy_results, window_size=None):
    """
    전략별 결과를 비교하고 최적 전략 추천
    
    Args:
        strategy_results: batch_test_strategies의 반환값
        window_size: 특정 윈도우 크기만 비교 (None이면 모든 윈도우 크기)
    """
    if not strategy_results:
        st.warning("⚠️ 비교할 결과가 없습니다.")
        return
    
    # 윈도우 크기별로 결과 정리
    if window_size is None:
        # 모든 윈도우 크기에 대해 비교
        all_window_sizes = set()
        for strategy_name, results in strategy_results.items():
            all_window_sizes.update(results.keys())
        window_sizes_to_compare = sorted(all_window_sizes)
    else:
        window_sizes_to_compare = [window_size]
    
    for window_size in window_sizes_to_compare:
        st.markdown(f"### 📊 윈도우 크기 {window_size} - 전략별 비교")
        
        comparison_data = []
        for strategy_name, results in strategy_results.items():
            if window_size not in results:
                continue
            
            result = results[window_size]
            # 실패 지표: 연속 불일치 5회 이상 OR 연속 일치 5회 이상
            total_failures = result.get('total_consecutive_5_count', 0) + result.get('total_consecutive_5_match_count', 0)
            max_failures = max(
                result.get('max_consecutive_mismatches', 0),
                result.get('max_consecutive_matches', 0)
            )
            
            # 스킵 통계 계산
            total_count = result.get('total_grid_strings', 0)
            skipped_count = result.get('skipped_count', 0)
            valid_count = result.get('valid_test_count', 0)
            truncated_count = result.get('truncated_count', 0)
            total_truncated_steps = result.get('total_truncated_steps', 0)
            skipped_ratio = (skipped_count / total_count * 100) if total_count > 0 else 0
            valid_ratio = (valid_count / total_count * 100) if total_count > 0 else 0
            
            comparison_data.append({
                '전략 이름': strategy_name,
                '최대 연속 불일치': result.get('max_consecutive_mismatches', 0),
                '최대 연속 일치': result.get('max_consecutive_matches', 0),
                '최대 실패 지표': max_failures,
                '연속 불일치 5회+': result.get('total_consecutive_5_count', 0),
                '연속 일치 5회+': result.get('total_consecutive_5_match_count', 0),
                '총 실패 횟수': total_failures,
                '평균 정확도 (%)': f"{result.get('avg_accuracy', 0):.2f}",
                '유효 테스트 수': valid_count,
                '스킵 수': skipped_count,
                '스킵 비율 (%)': f"{skipped_ratio:.1f}",
                '잘린 케이스 수': truncated_count,
                '잘린 스텝 수': total_truncated_steps,
                '테스트된 Grid 수': result.get('tested_grid_strings', 0),
                '전체 스텝 수': result.get('total_steps', 0)
            })
        
        if not comparison_data:
            st.warning(f"⚠️ 윈도우 크기 {window_size}에 대한 결과가 없습니다.")
            continue
        
        # 최대 실패 지표 기준으로 정렬 (오름차순)
        comparison_data.sort(key=lambda x: (x['최대 실패 지표'], x['총 실패 횟수']))
        
        comparison_df = pd.DataFrame(comparison_data)
        st.dataframe(comparison_df, use_container_width=True, hide_index=True)
        
        # 최적 전략 추천
        st.markdown("---")
        st.markdown(f"### 🎯 윈도우 크기 {window_size} - 최적 전략 추천")
        
        # 최대 실패 지표가 가장 낮은 전략 찾기
        best_strategy = min(comparison_data, key=lambda x: (x['최대 실패 지표'], x['총 실패 횟수']))
        best_strategy_name = best_strategy['전략 이름']
        best_result = strategy_results[best_strategy_name][window_size]
        
        st.success(f"✅ **최적 전략: {best_strategy_name}**")
        
        # 스킵 및 잘림 통계
        best_skipped_count = best_result.get('skipped_count', 0)
        best_truncated_count = best_result.get('truncated_count', 0)
        best_total_truncated_steps = best_result.get('total_truncated_steps', 0)
        best_valid_count = best_result.get('valid_test_count', 0)
        
        st.info(f"""
        - 최대 연속 불일치: {best_result.get('max_consecutive_mismatches', 0)}개
        - 최대 연속 일치: {best_result.get('max_consecutive_matches', 0)}개
        - 최대 실패 지표: {best_strategy['최대 실패 지표']}개
        - 연속 불일치 5회+: {best_result.get('total_consecutive_5_count', 0)}회
        - 연속 일치 5회+: {best_result.get('total_consecutive_5_match_count', 0)}회
        - 총 실패 횟수: {best_strategy['총 실패 횟수']}회
        - 평균 정확도: {best_result.get('avg_accuracy', 0):.2f}%
        - 유효한 테스트 케이스: {best_valid_count}개
        - 스킵된 케이스: {best_skipped_count}개
        - 잘린 케이스: {best_truncated_count}개
        - 잘린 스텝 수: {best_total_truncated_steps}개
        """)
        
        st.markdown("---")

def display_game_result(result_data):
    """
    게임 결과를 UI에 표시
    
    Args:
        result_data: simulate_game_scenario의 반환값
    """
    result = result_data['result']
    history = result_data['history']
    max_consecutive_mismatches = result_data['max_consecutive_mismatches']
    consecutive_5_positions = result_data['consecutive_5_positions']
    stats = result_data['stats']
    
    # 게임 결과 표시
    st.markdown("### 검증 결과")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if result == 'has_5_consecutive':
            st.error(f"❌ **연속 불일치 5개 발견!** (총 {stats['consecutive_5_count']}회 발생)")
        else:
            st.success(f"✅ **연속 불일치 5개 없음**")
    
    with col2:
        st.metric("총 검증 수", stats['total'])
    
    with col3:
        st.metric("최대 연속 불일치", max_consecutive_mismatches)
    
    # 연속 불일치 5개 발생 위치 표시
    if consecutive_5_positions:
        st.markdown("---")
        st.markdown("### ⚠️ 연속 불일치 5개 발생 위치")
        
        for idx, pos_info in enumerate(consecutive_5_positions, 1):
            st.markdown(f"**발생 #{idx}**: Step {pos_info['start_step']} ~ Step {pos_info['end_step']}")
            st.markdown(f"  - 스텝: {', '.join(map(str, pos_info['steps']))}")
    
    # 통계 정보
    st.markdown("---")
    st.markdown("### 통계 정보")
    
    stat_col1, stat_col2, stat_col3, stat_col4 = st.columns(4)
    
    with stat_col1:
        st.metric("일치 수", stats['matches'])
    
    with stat_col2:
        st.metric("불일치 수", stats['mismatches'])
    
    with stat_col3:
        accuracy = (stats['matches'] / stats['total'] * 100) if stats['total'] > 0 else 0
        st.metric("일치율", f"{accuracy:.1f}%")
    
    with stat_col4:
        st.metric("연속 불일치 5개 횟수", stats['consecutive_5_count'])
    
    # 상세 히스토리 (역순 - 최신순)
    if history:
        st.markdown("---")
        st.markdown("### 상세 히스토리")
        
        history_data = []
        # 역순으로 정렬 (최신순이 위에)
        reversed_history = list(reversed(history))
        for entry in reversed_history:
            match_icon = "✅" if entry['is_match'] else "❌"
            consecutive_info = f"({entry.get('consecutive_mismatches', 0)}연속)" if not entry['is_match'] else ""
            history_data.append({
                'Step': entry['step'],
                'Index': entry['index'],
                'Prefix': entry['prefix'],
                '예측값': entry['predicted'],
                '실제값': entry['actual'],
                '일치': match_icon,
                '연속불일치': entry.get('consecutive_mismatches', 0),
                '신뢰도': f"{entry['confidence']:.1f}%"
            })
        
        history_df = pd.DataFrame(history_data)
        st.dataframe(history_df, use_container_width=True, hide_index=True)
        
        # 연속 불일치 5개 이상인 구간 강조
        if max_consecutive_mismatches >= 5:
            st.warning(f"⚠️ 최대 {max_consecutive_mismatches}개가 연속으로 불일치했습니다.")

def main():
    st.title("🔬 Hypothesis Validation System")
    st.markdown("N-gram 기반 패턴 예측 가설 검증")
    st.markdown("---")
    
    # 시나리오 검증 테이블 생성 (최초 1회)
    create_scenario_validation_tables()
    # ngram_chunks 테이블 생성 (최초 1회)
    create_ngram_chunks_table()
    # stored_predictions 테이블 생성 (최초 1회)
    create_stored_predictions_table()
    # prefix_trend_rules 테이블 생성 (최초 1회)
    create_prefix_trend_rules_table()
    
    # 데이터 로드
    df_strings = load_preprocessed_data()
    
    if len(df_strings) == 0:
        st.warning("⚠️ 전처리된 데이터가 없습니다. 먼저 `preprocess_grid_data.py`를 실행해주세요.")
        return
    
    # 사이드바: 설정
    st.sidebar.header("⚙️ 설정")
    
    # 윈도우 크기 선택
    window_size = st.sidebar.selectbox(
        "윈도우 크기",
        options=[5, 6, 7, 8, 9],
        index=0
    )
    
    # 예측 방법 선택
    prediction_method = st.sidebar.selectbox(
        "예측 방법",
        options=["빈도 기반", "가중치 기반", "안전 우선"],
        index=0
    )
    
    # 학습/테스트 세트 분할
    train_ratio = st.sidebar.slider(
        "학습 세트 비율 (%)",
        min_value=50,
        max_value=90,
        value=80,
        step=5
    )
    
    # 데이터 개요
    st.header("📊 데이터 개요")
    
    # ngram_chunks 일괄 생성 버튼
    col_info1, col_info2 = st.columns([3, 1])
    with col_info1:
        st.markdown("**ngram_chunks 상태 확인 및 일괄 생성**")
    with col_info2:
        if st.button("기존 데이터 ngram_chunks 일괄 생성", key="batch_generate_ngrams", use_container_width=True):
            with st.spinner("ngram_chunks 생성 중..."):
                batch_generate_ngram_chunks_for_existing_data()
                st.rerun()
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("전체 세션 수", len(df_strings))
    
    with col2:
        total_chars = df_strings['string_length'].sum()
        st.metric("총 문자 수", f"{total_chars:,}")
    
    with col3:
        avg_length = df_strings['string_length'].mean()
        st.metric("평균 문자열 길이", f"{avg_length:.1f}")
    
    with col4:
        avg_b_ratio = df_strings['b_ratio'].mean()
        st.metric("평균 'b' 비율", f"{avg_b_ratio:.1f}%")
    
    st.markdown("---")
    
    # 학습/테스트 세트 분할
    split_idx = int(len(df_strings) * train_ratio / 100)
    train_ids = df_strings.iloc[:split_idx]['id'].tolist()
    test_ids = df_strings.iloc[split_idx:]['id'].tolist()
    
    st.header("📈 모델 학습 및 평가")
    
    with st.spinner("데이터 로딩 중..."):
        # 학습 세트 N-gram 로드
        train_ngrams = load_ngram_chunks(window_size=window_size, grid_string_ids=train_ids)
        
        if len(train_ngrams) == 0:
            st.warning("⚠️ 학습 데이터가 없습니다.")
            return
        
        # 테스트 세트 N-gram 로드
        test_ngrams = load_ngram_chunks(window_size=window_size, grid_string_ids=test_ids)
        
        if len(test_ngrams) == 0:
            st.warning("⚠️ 테스트 데이터가 없습니다.")
            return
    
    # 모델 구축
    with st.spinner(f"{prediction_method} 모델 구축 중..."):
        if prediction_method == "빈도 기반":
            model = build_frequency_model(train_ngrams)
            predict_func = predict_frequency
        # elif prediction_method == "마르코프 체인":
        #     model = build_markov_model(train_ngrams)
        #     predict_func = predict_markov
        elif prediction_method == "가중치 기반":
            model = build_weighted_model(train_ngrams)
            predict_func = predict_weighted
        elif prediction_method == "안전 우선":
            model = build_safety_first_model(train_ngrams)
            predict_func = lambda m, p: predict_safety_first(m, p, recent_history=None, consecutive_mismatches=0)
        else:  # 기본값: 빈도 기반
            model = build_frequency_model(train_ngrams)
            predict_func = predict_frequency
    
    st.success(f"✅ 모델 구축 완료 (고유 prefix 패턴: {len(model):,}개)")
    
    # 테스트 세트 예측
    with st.spinner("예측 수행 중..."):
        predictions = []
        actuals = []
        confidence_scores = []
        
        for _, row in test_ngrams.iterrows():
            prefix = row['prefix']
            actual = row['suffix']
            
            predicted, ratios = predict_func(model, prefix)
            
            if predicted is not None:
                predictions.append(predicted)
                actuals.append(actual)
                # 가장 높은 비율을 confidence로 사용
                confidence = max(ratios.values()) if ratios else 0.0
                confidence_scores.append(confidence)
    
    # 평가 결과
    if len(predictions) > 0:
        metrics = evaluate_predictions(predictions, actuals)
        
        st.markdown("### 평가 결과")
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("정확도", f"{metrics['accuracy']:.2f}%")
        
        with col2:
            st.metric("정답 수", f"{metrics['correct']}/{metrics['total']}")
        
        with col3:
            avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0
            st.metric("평균 신뢰도", f"{avg_confidence:.2f}%")
        
        with col4:
            st.metric("테스트 세트 크기", f"{metrics['total']:,}")
        
        # 상세 통계
        st.markdown("### 상세 통계")
        
        stats_data = {
            '항목': ['전체', "'b' 예측", "'p' 예측"],
            '예측 수': [
                metrics['total'],
                metrics['b_predicted'],
                metrics['p_predicted']
            ],
            '실제 수': [
                metrics['total'],
                metrics['b_actual'],
                metrics['p_actual']
            ]
        }
        stats_df = pd.DataFrame(stats_data)
        st.dataframe(stats_df, use_container_width=True, hide_index=True)
    else:
        st.warning("⚠️ 예측 결과가 없습니다.")
    
    st.markdown("---")
    
    # 패턴 분석
    st.header("🔍 패턴 분석")
    
    # 가장 빈번한 prefix 패턴
    st.markdown("### 가장 빈번한 Prefix 패턴 (Top 10)")
    
    prefix_counts = train_ngrams['prefix'].value_counts().head(10)
    prefix_df = pd.DataFrame({
        'Prefix': prefix_counts.index,
        '빈도': prefix_counts.values
    })
    st.dataframe(prefix_df, use_container_width=True, hide_index=True)
    
    # 세션별 상세 분석
    st.markdown("---")
    st.header("📋 세션별 분석")
    
    session_options = [
        f"{row['source_session_id'][:8]}... (길이: {row['string_length']})"
        for _, row in df_strings.iterrows()
    ]
    
    selected_idx = st.selectbox(
        "세션 선택",
        options=range(len(df_strings)),
        format_func=lambda x: session_options[x] if x < len(session_options) else f"세션 {x}"
    )
    
    if selected_idx < len(df_strings):
        selected_row = df_strings.iloc[selected_idx]
        selected_id = selected_row['id']
        selected_string = selected_row['grid_string']
        
        st.markdown(f"**세션 ID**: `{selected_row['source_session_id']}`")
        st.markdown(f"**문자열 길이**: {selected_row['string_length']}")
        st.markdown(f"**'b' 비율**: {selected_row['b_ratio']:.2f}%")
        st.markdown(f"**'p' 비율**: {selected_row['p_ratio']:.2f}%")
        
        # 해당 세션의 N-gram 조각 로드
        session_ngrams = load_ngram_chunks(window_size=window_size, grid_string_ids=[selected_id])
        
        if len(session_ngrams) > 0:
            st.markdown(f"### 윈도우 크기 {window_size} 조각 (처음 20개)")
            
            display_ngrams = session_ngrams.head(20).copy()
            display_ngrams['예측값'] = display_ngrams['prefix'].apply(
                lambda p: predict_func(model, p)[0] if p in model else 'N/A'
            )
            
            display_df = display_ngrams[['chunk_index', 'prefix', 'suffix', '예측값', 'full_chunk']].copy()
            display_df.columns = ['인덱스', 'Prefix', '실제값', '예측값', '전체 조각']
            display_df['일치'] = display_df['실제값'] == display_df['예측값']
            
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            
            # 해당 세션의 정확도
            session_predictions = []
            session_actuals = []
            
            for _, row in session_ngrams.iterrows():
                prefix = row['prefix']
                actual = row['suffix']
                predicted, _ = predict_func(model, prefix)
                
                if predicted is not None:
                    session_predictions.append(predicted)
                    session_actuals.append(actual)
            
            if len(session_predictions) > 0:
                session_metrics = evaluate_predictions(session_predictions, session_actuals)
                st.metric("이 세션의 정확도", f"{session_metrics['accuracy']:.2f}%")
    
    st.markdown("---")
    
    # Prefix 예측 및 검증 섹션
    st.header("🔮 Prefix 예측 및 검증")
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        prefix_input = st.text_input(
            "Prefix 입력",
            value="bbbbb",
            help="예측할 prefix를 입력하세요 (예: 'bbbbb', 'bbbbp' 등)"
        )
    
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        predict_button = st.button("예측", type="primary", use_container_width=True)
    
    if predict_button and prefix_input:
        # prefix 길이 검증
        prefix_length = len(prefix_input)
        expected_prefix_length = window_size - 1
        
        if prefix_length != expected_prefix_length:
            st.warning(f"⚠️ Prefix 길이가 맞지 않습니다. 윈도우 크기 {window_size}에 맞게 {expected_prefix_length}자리여야 합니다.")
        else:
            # 예측 수행
            prediction_result = predict_for_prefix(model, prefix_input, prediction_method)
            
            if prediction_result['predicted']:
                st.markdown("### 예측 결과")
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown(f"**Prefix**: `{prefix_input}`")
                    st.markdown(f"**예측값**: `{prediction_result['predicted']}`")
                    st.metric("신뢰도", f"{prediction_result['confidence']:.2f}%")
                
                with col2:
                    st.markdown("**예측 확률 분포:**")
                    ratios = prediction_result['ratios']
                    for value, ratio in sorted(ratios.items(), key=lambda x: x[1], reverse=True):
                        st.progress(ratio / 100, text=f"'{value}': {ratio:.2f}%")
                
                # 실제값 입력 및 검증
                st.markdown("---")
                st.markdown("### 실제값 입력 및 검증")
                
                actual_value = st.radio(
                    "실제값 선택",
                    options=['b', 'p'],
                    horizontal=True
                )
                
                if st.button("검증", type="primary"):
                    is_correct = prediction_result['predicted'] == actual_value
                    predicted_ratio = ratios.get(actual_value, 0.0)
                    
                    if is_correct:
                        st.success(f"✅ 예측 정확! 예측값 '{prediction_result['predicted']}'와 실제값 '{actual_value}'가 일치합니다.")
                    else:
                        st.error(f"❌ 예측 불일치. 예측값 '{prediction_result['predicted']}'와 실제값 '{actual_value}'가 다릅니다.")
                    
                    st.info(f"실제값 '{actual_value}'의 예측 확률: {predicted_ratio:.2f}%")
            else:
                st.warning(f"⚠️ Prefix '{prefix_input}'에 대한 예측 데이터가 없습니다.")
    
    st.markdown("---")
    
    # 인터랙티브 다단계 예측 시나리오 섹션
    st.header("🌳 인터랙티브 다단계 예측 시나리오")
    
    # 설정 섹션 (st.form으로 그룹화하여 rerun 최소화)
    with st.form("interactive_settings_form", clear_on_submit=False):
        st.markdown("### ⚙️ 설정")
        col_setting1, col_setting2, col_setting3 = st.columns(3)
        
        with col_setting1:
            interactive_window_size = st.selectbox(
                "윈도우 크기",
                options=[5, 6, 7, 8, 9],
                index=2,  # 7을 기본값으로
                key="interactive_window_size",
                help="예측에 사용할 윈도우 크기를 선택하세요"
            )
        
        with col_setting2:
            interactive_method = st.selectbox(
                "예측 방법",
                options=["빈도 기반", "가중치 기반", "안전 우선"],
                index=0,
                key="interactive_method",
                help="예측에 사용할 방법을 선택하세요"
            )
        
        with col_setting3:
            use_threshold = st.checkbox(
                "임계값 전략 사용",
                value=True,
                key="interactive_use_threshold",
                help="임계값 이상일 때만 예측하도록 설정"
            )
            interactive_threshold = None
            if use_threshold:
                interactive_threshold = st.number_input(
                    "임계값 (%)",
                    min_value=0,
                    max_value=100,
                    value=60,
                    step=1,
                    key="interactive_threshold",
                    help="이 신뢰도 이상일 때만 예측합니다"
                )
        
        # 최대 간격 설정 (강제 예측용)
        col_setting4, col_setting5 = st.columns(2)
        with col_setting4:
            interactive_max_interval = st.number_input(
                "최대 예측 없음 간격 (스텝)",
                min_value=1,
                max_value=20,
                value=6,
                step=1,
                key="interactive_max_interval",
                help="이 간격을 넘기면 임계값 무시하고 강제 예측합니다"
            )
        
        # 설정 적용 버튼
        if st.form_submit_button("설정 적용", use_container_width=True):
            # 설정 변경 감지 및 초기화
            if 'last_interactive_window_size' not in st.session_state:
                st.session_state.last_interactive_window_size = interactive_window_size
                st.session_state.last_interactive_method = interactive_method
                st.session_state.last_interactive_threshold = interactive_threshold
                st.session_state.last_interactive_max_interval = interactive_max_interval
            elif (st.session_state.last_interactive_window_size != interactive_window_size or
                  st.session_state.last_interactive_method != interactive_method or
                  st.session_state.last_interactive_threshold != interactive_threshold or
                  st.session_state.last_interactive_max_interval != interactive_max_interval):
                # 설정이 변경되었으면 초기화 및 캐시 무효화
                st.session_state.interactive_path = []
                st.session_state.interactive_current_prefix = None
                st.session_state.interactive_step = 0
                st.session_state.interactive_current_interval = 0
                
                # 모델 캐시 무효화 (설정 변경 시)
                if 'last_interactive_window_size' in st.session_state and 'last_interactive_method' in st.session_state:
                    old_model_key = f'interactive_model_{st.session_state.last_interactive_window_size}_{st.session_state.last_interactive_method}'
                    old_data_key = f'interactive_data_{st.session_state.last_interactive_window_size}'
                    old_ngrams_key = f'interactive_ngrams_{st.session_state.last_interactive_window_size}'
                    if old_model_key in st.session_state:
                        del st.session_state[old_model_key]
                    if old_data_key in st.session_state:
                        del st.session_state[old_data_key]
                    if old_ngrams_key in st.session_state:
                        del st.session_state[old_ngrams_key]
                
                st.session_state.last_interactive_window_size = interactive_window_size
                st.session_state.last_interactive_method = interactive_method
                st.session_state.last_interactive_threshold = interactive_threshold
                st.session_state.last_interactive_max_interval = interactive_max_interval
                st.rerun()
    
    # Session state 초기화
    if 'interactive_path' not in st.session_state:
        st.session_state.interactive_path = []
        st.session_state.interactive_current_prefix = None
        st.session_state.interactive_step = 0
        st.session_state.interactive_current_interval = 0
        st.session_state.last_interactive_window_size = interactive_window_size
        st.session_state.last_interactive_method = interactive_method
        st.session_state.last_interactive_threshold = interactive_threshold
        st.session_state.last_interactive_max_interval = interactive_max_interval
    
    st.markdown("---")
    
    # 초기 prefix 입력 (st.form으로 그룹화하여 rerun 최소화)
    with st.form("start_interactive_form", clear_on_submit=False):
        col1, col2 = st.columns([2, 1])
        
        with col1:
            initial_prefix = st.text_input(
                "초기 Prefix 입력",
                value="bbbbb",
                key="initial_prefix",
                help=f"다단계 예측을 시작할 초기 prefix를 입력하세요 (길이: {interactive_window_size - 1})"
            )
        
        with col2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.form_submit_button("시작", type="primary", use_container_width=True):
                # prefix 길이 검증
                prefix_length = len(initial_prefix)
                expected_prefix_length = interactive_window_size - 1
                
                if prefix_length != expected_prefix_length:
                    st.warning(f"⚠️ Prefix 길이가 맞지 않습니다. 윈도우 크기 {interactive_window_size}에 맞게 {expected_prefix_length}자리여야 합니다.")
                else:
                    st.session_state.interactive_path = []
                    st.session_state.interactive_current_prefix = initial_prefix
                    st.session_state.interactive_step = 1
                    st.session_state.interactive_current_interval = 0
                    st.rerun()
    
    if st.button("초기화", use_container_width=True, key="reset_interactive"):
        st.session_state.interactive_path = []
        st.session_state.interactive_current_prefix = None
        st.session_state.interactive_step = 0
        st.session_state.interactive_current_interval = 0
        st.rerun()
    
    # 인터랙티브 단계별 진행
    if st.session_state.interactive_current_prefix and st.session_state.interactive_step > 0:
        current_prefix = st.session_state.interactive_current_prefix
        current_step = st.session_state.interactive_step
        
        st.markdown("---")
        st.markdown(f"### Step {current_step}: `{current_prefix}`")
        
        # 현재 스텝의 예측 결과 캐시 키
        prediction_result_key = f'interactive_prediction_step_{current_step}'
        prediction_interval_key = f'interactive_interval_before_step_{current_step}'
        
        # 캐시된 예측 결과가 있으면 사용, 없으면 계산
        if prediction_result_key in st.session_state and st.session_state[prediction_result_key] is not None:
            prediction_result = st.session_state[prediction_result_key]
            df_strings = None
            train_ngrams = None
            interactive_model = None
        else:
            # 모델 및 데이터 캐싱
            model_cache_key = f'interactive_model_{interactive_window_size}_{interactive_method}'
            data_cache_key = f'interactive_data_{interactive_window_size}'
            ngrams_cache_key = f'interactive_ngrams_{interactive_window_size}'
            
            # 설정 변경 감지
            settings_changed = (
                'last_interactive_window_size' not in st.session_state or
                st.session_state.last_interactive_window_size != interactive_window_size or
                st.session_state.last_interactive_method != interactive_method
            )
            
            # 캐시 확인 및 모델 구축
            if not settings_changed and model_cache_key in st.session_state:
                # 캐시된 모델 및 데이터 재사용
                interactive_model = st.session_state[model_cache_key]
                df_strings = st.session_state.get(data_cache_key)
                train_ngrams = st.session_state.get(ngrams_cache_key)
            else:
                # 모델 구축 및 캐싱
                df_strings = None
                train_ngrams = None
                interactive_model = None
                
                with st.spinner("모델 구축 중..."):
                    # 학습 데이터 로드
                    df_strings = load_preprocessed_data()
                    if len(df_strings) == 0:
                        st.warning("⚠️ 전처리된 데이터가 없습니다.")
                        prediction_result = {'predicted': None, 'ratios': {}, 'confidence': 0.0, 'is_forced': False}
                    else:
                        # 학습 세트 분할
                        train_ratio = 80
                        split_idx = int(len(df_strings) * train_ratio / 100)
                        train_ids = df_strings.iloc[:split_idx]['id'].tolist()
                        
                        # N-gram 로드
                        train_ngrams = load_ngram_chunks(window_size=interactive_window_size, grid_string_ids=train_ids)
                        
                        if len(train_ngrams) == 0:
                            st.warning(f"⚠️ 윈도우 크기 {interactive_window_size}에 대한 학습 데이터가 없습니다.")
                            prediction_result = {'predicted': None, 'ratios': {}, 'confidence': 0.0, 'is_forced': False}
                        else:
                            # 모델 구축
                            if interactive_method == "빈도 기반":
                                interactive_model = build_frequency_model(train_ngrams)
                            # elif interactive_method == "마르코프 체인":
                            #     interactive_model = build_markov_model(train_ngrams)
                            elif interactive_method == "가중치 기반":
                                interactive_model = build_weighted_model(train_ngrams)
                            elif interactive_method == "안전 우선":
                                interactive_model = build_safety_first_model(train_ngrams)
                            else:  # 기본값: 빈도 기반
                                interactive_model = build_frequency_model(train_ngrams)
                            
                            # 모델 및 데이터 캐싱
                            st.session_state[model_cache_key] = interactive_model
                            st.session_state[data_cache_key] = df_strings
                            st.session_state[ngrams_cache_key] = train_ngrams
            
            # 예측 계산 (모델이 있는 경우)
            if interactive_model is not None:
                # 예측 계산 (간격 업데이트는 하지 않음)
                if use_threshold and interactive_threshold is not None:
                    # 강제 예측 전략 사용
                    current_interval_for_prediction = st.session_state.interactive_current_interval
                    
                    # 디버깅: 예측 전 간격 상태 저장
                    st.session_state[prediction_interval_key] = current_interval_for_prediction
                    
                    prediction_result = predict_with_fallback_interval(
                        interactive_model,
                        current_prefix,
                        interactive_method,
                        threshold=interactive_threshold,
                        max_interval=interactive_max_interval,
                        current_interval=current_interval_for_prediction
                    )
                    # 간격 업데이트는 하지 않음 (다음 스텝으로 넘어갈 때 업데이트)
                else:
                    prediction_result = predict_for_prefix(interactive_model, current_prefix, interactive_method)
                    if 'is_forced' not in prediction_result:
                        prediction_result['is_forced'] = False
            else:
                prediction_result = {'predicted': None, 'ratios': {}, 'confidence': 0.0, 'is_forced': False}
            
            # 예측 결과를 session_state에 저장
            st.session_state[prediction_result_key] = prediction_result
        
        # 디버깅: 예측 결과 확인
        if use_threshold and interactive_threshold is not None:
            st.info(f"🔍 **디버깅**: 스텝={current_step}, prefix='{current_prefix}', 예측 전 간격={st.session_state.interactive_current_interval}, 예측값={prediction_result.get('predicted')}, 강제예측={prediction_result.get('is_forced', False)}, 신뢰도={prediction_result.get('confidence', 0):.2f}%")
        
        # 예측값이 있는 경우
        if prediction_result.get('predicted') is not None:
            ratios = prediction_result.get('ratios', {})
            sorted_ratios = sorted(ratios.items(), key=lambda x: x[1], reverse=True) if ratios else []
            
            # 예측값 표시
            col1, col2 = st.columns([1, 1])
            
            with col1:
                st.markdown("**예측 확률:**")
                if sorted_ratios:
                    for value, ratio in sorted_ratios:
                        color = "🟢" if ratio == max(ratios.values()) else "🟡"
                        st.markdown(f"{color} **'{value}'**: {ratio:.2f}%")
                        st.progress(ratio / 100)
                else:
                    st.info("예측 확률 정보 없음")
            
            with col2:
                st.markdown("**예측값:**")
                is_forced = prediction_result.get('is_forced', False)
                prediction_display = prediction_result['predicted']
                if is_forced:
                    prediction_display = f"{prediction_display} ⚡"  # 강제 예측 표시
                st.markdown(f"### `{prediction_display}`")
                confidence_display = f"{prediction_result.get('confidence', 0):.2f}%"
                if is_forced:
                    confidence_display += " (강제)"
                st.metric("신뢰도", confidence_display)
                
                # 디버깅: 간격 정보 표시
                if use_threshold and interactive_threshold is not None:
                    if prediction_interval_key in st.session_state:
                        interval_before = st.session_state[prediction_interval_key]
                        st.info(f"🔍 **디버깅 정보**: 예측 전 간격={interval_before}, 현재 간격={st.session_state.interactive_current_interval}/{interactive_max_interval} (마지막 예측 이후 예측 없음 연속 스텝 수)")
                    else:
                        st.info(f"🔍 **디버깅 정보**: 현재 간격={st.session_state.interactive_current_interval}/{interactive_max_interval} (마지막 예측 이후 예측 없음 연속 스텝 수)")
            
            # 다음 1개 스텝 실제값 경로 미리보기
            st.markdown("---")
            st.markdown('<p style="font-size: 1em; color: #666; margin-top: -10px;"><strong>다음 스텝 경로 미리보기:</strong></p>', unsafe_allow_html=True)
            
            # 다음 prefix 생성 (b와 p 두 경우 모두)
            next_prefix_b = get_next_prefix(current_prefix, 'b', interactive_window_size)
            next_prefix_p = get_next_prefix(current_prefix, 'p', interactive_window_size)
            
            # 미리보기 계산을 위해 모델이 필요하면 캐시에서 로드
            if interactive_model is None:
                model_cache_key = f'interactive_model_{interactive_window_size}_{interactive_method}'
                if model_cache_key in st.session_state:
                    interactive_model = st.session_state[model_cache_key]
            
            # 다음 prefix에 대한 예측 (모델이 있는 경우)
            if interactive_model is not None:
                next_pred_b = None
                next_pred_p = None
                next_conf_b = 0.0
                next_conf_p = 0.0
                next_forced_b = False
                next_forced_p = False
                
                try:
                    if use_threshold and interactive_threshold is not None:
                        # 다음 스텝 예측용 간격 계산
                        # 현재 스텝에서 예측이 있었으면, 다음 스텝으로 넘어가면 간격이 0으로 리셋
                        # 현재 스텝에서 예측이 없었으면, 다음 스텝으로 넘어가면 간격이 1 증가
                        if prediction_result.get('predicted') is not None:
                            # 현재 스텝에서 예측이 있었으면, 다음 스텝으로 넘어가면 간격이 0으로 리셋
                            next_interval = 0
                        else:
                            # 현재 스텝에서 예측이 없었으면, 다음 스텝으로 넘어가면 간격이 1 증가
                            next_interval = st.session_state.interactive_current_interval + 1
                        
                        # 간격을 고려하여 예측 (간격이 0이므로 강제 예측은 발생하지 않음)
                        next_result_b = predict_with_fallback_interval(
                            interactive_model,
                            next_prefix_b,
                            interactive_method,
                            threshold=interactive_threshold,
                            max_interval=interactive_max_interval,
                            current_interval=next_interval
                        )
                        next_result_p = predict_with_fallback_interval(
                            interactive_model,
                            next_prefix_p,
                            interactive_method,
                            threshold=interactive_threshold,
                            max_interval=interactive_max_interval,
                            current_interval=next_interval
                        )
                        
                        next_forced_b = next_result_b.get('is_forced', False)
                        next_forced_p = next_result_p.get('is_forced', False)
                    else:
                        next_result_b = predict_for_prefix(interactive_model, next_prefix_b, interactive_method)
                        next_result_p = predict_for_prefix(interactive_model, next_prefix_p, interactive_method)
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
        else:
            # 예측값이 없는 경우 (임계값 미만 등)
            col1, col2 = st.columns([1, 1])
            
            with col1:
                st.markdown("**예측 확률:**")
                st.info("⚠️ 예측 데이터 없음")
                if use_threshold and interactive_threshold is not None:
                    if prediction_interval_key in st.session_state:
                        interval_before = st.session_state[prediction_interval_key]
                    else:
                        interval_before = st.session_state.interactive_current_interval
                    st.info(f"🔍 **디버깅 정보**: 예측 전 간격={interval_before}, 현재 간격={st.session_state.interactive_current_interval}/{interactive_max_interval} (마지막 예측 이후 예측 없음 연속 스텝 수)")
                    st.caption(f"임계값({interactive_threshold}%) 미만이거나 학습 데이터가 부족합니다.")
                else:
                    st.caption("학습 데이터가 부족합니다.")
            
            with col2:
                st.markdown("**예측값:**")
                st.markdown("### `-`")
                st.metric("신뢰도", "N/A")
            
            # 다음 1개 스텝 실제값 경로 미리보기
            st.markdown("---")
            st.markdown('<p style="font-size: 1em; color: #666; margin-top: -10px;"><strong>다음 스텝 경로 미리보기:</strong></p>', unsafe_allow_html=True)
            
            # 다음 prefix 생성 (b와 p 두 경우 모두)
            next_prefix_b = get_next_prefix(current_prefix, 'b', interactive_window_size)
            next_prefix_p = get_next_prefix(current_prefix, 'p', interactive_window_size)
            
            # 미리보기 계산을 위해 모델이 필요하면 캐시에서 로드
            if interactive_model is None:
                model_cache_key = f'interactive_model_{interactive_window_size}_{interactive_method}'
                if model_cache_key in st.session_state:
                    interactive_model = st.session_state[model_cache_key]
            
            # 다음 prefix에 대한 예측 (모델이 있는 경우)
            if interactive_model is not None:
                next_pred_b = None
                next_pred_p = None
                next_conf_b = 0.0
                next_conf_p = 0.0
                next_forced_b = False
                next_forced_p = False
                
                try:
                    if use_threshold and interactive_threshold is not None:
                        # 다음 스텝 예측용 간격 계산
                        # 현재 스텝에서 예측이 없었으면, 다음 스텝으로 넘어가면 간격이 1 증가
                        # 현재 스텝에서 예측이 있었으면, 다음 스텝으로 넘어가면 간격이 0으로 리셋
                        if prediction_result.get('predicted') is None:
                            # 현재 스텝에서 예측이 없었으면, 다음 스텝으로 넘어가면 간격이 1 증가
                            next_interval = st.session_state.interactive_current_interval + 1
                        else:
                            # 현재 스텝에서 예측이 있었으면, 다음 스텝으로 넘어가면 간격이 0으로 리셋
                            next_interval = 0
                        
                        next_result_b = predict_with_fallback_interval(
                            interactive_model,
                            next_prefix_b,
                            interactive_method,
                            threshold=interactive_threshold,
                            max_interval=interactive_max_interval,
                            current_interval=next_interval
                        )
                        next_result_p = predict_with_fallback_interval(
                            interactive_model,
                            next_prefix_p,
                            interactive_method,
                            threshold=interactive_threshold,
                            max_interval=interactive_max_interval,
                            current_interval=next_interval
                        )
                    else:
                        next_result_b = predict_for_prefix(interactive_model, next_prefix_b, interactive_method)
                        next_result_p = predict_for_prefix(interactive_model, next_prefix_p, interactive_method)
                    
                    next_pred_b = next_result_b.get('predicted')
                    next_pred_p = next_result_p.get('predicted')
                    next_conf_b = next_result_b.get('confidence', 0.0)
                    next_conf_p = next_result_p.get('confidence', 0.0)
                    next_forced_b = next_result_b.get('is_forced', False)
                    next_forced_p = next_result_p.get('is_forced', False)
                except:
                    pass
                
                # 경로 표시
                col_path1, col_path2 = st.columns(2)
                with col_path1:
                    if next_pred_b is not None:
                        forced_marker = " ⚡" if next_forced_b else ""
                        st.markdown(f'<p style="font-size: 0.95em; color: #333;">실제값 <strong>b</strong> → 다음 prefix: <code>{next_prefix_b}</code> → 예측: <code>{next_pred_b}{forced_marker}</code> ({next_conf_b:.1f}%)</p>', unsafe_allow_html=True)
                    else:
                        st.markdown(f'<p style="font-size: 0.95em; color: #666;">실제값 <strong>b</strong> → 다음 prefix: <code>{next_prefix_b}</code> → 예측: <code>-</code></p>', unsafe_allow_html=True)
                
                with col_path2:
                    if next_pred_p is not None:
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
        
        st.markdown("---")
        
        # 실제값 입력: "다음 스텝 (B)"와 "다음 스텝 (P)" 버튼
        st.markdown("**다음 스텝으로 진행하세요:**")
        
        # 버튼 스타일링을 위한 CSS (배경색 제거 및 색상 구분)
        st.markdown("""
        <style>
        /* B 버튼 스타일 (빨간색) */
        button[kind="secondary"]:has-text("🔴") {
            background-color: transparent !important;
            color: #FF0000 !important;
            border: 2px solid #FF0000 !important;
            font-weight: bold !important;
        }
        button[kind="secondary"]:has-text("🔴"):hover {
            background-color: rgba(255, 0, 0, 0.1) !important;
        }
        /* P 버튼 스타일 (파란색) */
        button[kind="secondary"]:has-text("🔵") {
            background-color: transparent !important;
            color: #0066FF !important;
            border: 2px solid #0066FF !important;
            font-weight: bold !important;
        }
        button[kind="secondary"]:has-text("🔵"):hover {
            background-color: rgba(0, 102, 255, 0.1) !important;
        }
        </style>
        """, unsafe_allow_html=True)
        
        button_col1, button_col2, button_col3 = st.columns([1, 1, 2])
        
        with button_col1:
            if st.button("🔴 다음 스텝 (B)", key=f"next_step_b_{current_step}", use_container_width=True):
                actual_value = 'b'
                
                # 경로 기록
                if prediction_result.get('predicted') is not None:
                    ratios = prediction_result.get('ratios', {})
                    is_forced = prediction_result.get('is_forced', False)
                    path_entry = {
                        'step': current_step,
                        'prefix': current_prefix,
                        'predictions': ratios,
                        'predicted': prediction_result['predicted'],
                        'actual': actual_value,
                        'is_correct': prediction_result['predicted'] == actual_value,
                        'confidence': prediction_result.get('confidence', 0.0),
                        'has_prediction': True,
                        'is_forced': is_forced
                    }
                else:
                    path_entry = {
                        'step': current_step,
                        'prefix': current_prefix,
                        'predictions': {},
                        'predicted': None,
                        'actual': actual_value,
                        'is_correct': None,
                        'confidence': 0.0,
                        'has_prediction': False,
                        'is_forced': False
                    }
                
                st.session_state.interactive_path.append(path_entry)
                
                # 간격 업데이트: 다음 스텝으로 넘어가기 전에 이전 스텝의 예측 결과를 확인
                # 간격은 "마지막 예측 이후 예측 없음이 연속 발생한 스텝 수"
                # 현재 스텝에서 예측이 있었다면 간격은 0으로 리셋
                if path_entry.get('has_prediction', False):
                    # 현재 스텝에서 예측이 있었으면 간격 리셋
                    st.session_state.interactive_current_interval = 0
                else:
                    # 현재 스텝에서 예측이 없었으면 간격 계산
                    # interactive_path를 역순으로 순회하여 마지막 예측이 있었던 스텝을 찾고, 그 이후의 예측 없음 스텝 수를 계산
                    last_prediction_step = None
                    for i in range(len(st.session_state.interactive_path) - 1, -1, -1):
                        entry = st.session_state.interactive_path[i]
                        if entry.get('has_prediction', False):
                            last_prediction_step = entry['step']
                            break
                    
                    if last_prediction_step is not None:
                        # 마지막 예측 이후 예측 없음 스텝 수 계산
                        no_prediction_count = 0
                        for i in range(len(st.session_state.interactive_path) - 1, -1, -1):
                            entry = st.session_state.interactive_path[i]
                            if entry['step'] > last_prediction_step and not entry.get('has_prediction', False):
                                no_prediction_count += 1
                            elif entry['step'] <= last_prediction_step:
                                break
                        st.session_state.interactive_current_interval = no_prediction_count
                    else:
                        # 아직 예측이 없었던 경우: 예측이 없었던 스텝 수를 카운트
                        no_prediction_count = 0
                        for entry in st.session_state.interactive_path:
                            if not entry.get('has_prediction', False):
                                no_prediction_count += 1
                        st.session_state.interactive_current_interval = no_prediction_count
                
                # 다음 prefix 생성 및 스텝 증가
                next_prefix = get_next_prefix(current_prefix, actual_value, interactive_window_size)
                st.session_state.interactive_current_prefix = next_prefix
                st.session_state.interactive_step = current_step + 1
                
                # 현재 스텝의 예측 결과 캐시 삭제
                if prediction_result_key in st.session_state:
                    del st.session_state[prediction_result_key]
                if prediction_interval_key in st.session_state:
                    del st.session_state[prediction_interval_key]
                
                st.rerun()
        
        with button_col2:
            if st.button("🔵 다음 스텝 (P)", key=f"next_step_p_{current_step}", use_container_width=True):
                actual_value = 'p'
                
                # 경로 기록
                if prediction_result.get('predicted') is not None:
                    ratios = prediction_result.get('ratios', {})
                    is_forced = prediction_result.get('is_forced', False)
                    path_entry = {
                        'step': current_step,
                        'prefix': current_prefix,
                        'predictions': ratios,
                        'predicted': prediction_result['predicted'],
                        'actual': actual_value,
                        'is_correct': prediction_result['predicted'] == actual_value,
                        'confidence': prediction_result.get('confidence', 0.0),
                        'has_prediction': True,
                        'is_forced': is_forced
                    }
                else:
                    path_entry = {
                        'step': current_step,
                        'prefix': current_prefix,
                        'predictions': {},
                        'predicted': None,
                        'actual': actual_value,
                        'is_correct': None,
                        'confidence': 0.0,
                        'has_prediction': False,
                        'is_forced': False
                    }
                
                st.session_state.interactive_path.append(path_entry)
                
                # 간격 업데이트: 다음 스텝으로 넘어가기 전에 이전 스텝의 예측 결과를 확인
                # 간격은 "마지막 예측 이후 예측 없음이 연속 발생한 스텝 수"
                # 현재 스텝에서 예측이 있었다면 간격은 0으로 리셋
                if path_entry.get('has_prediction', False):
                    # 현재 스텝에서 예측이 있었으면 간격 리셋
                    st.session_state.interactive_current_interval = 0
                else:
                    # 현재 스텝에서 예측이 없었으면 간격 계산
                    # interactive_path를 역순으로 순회하여 마지막 예측이 있었던 스텝을 찾고, 그 이후의 예측 없음 스텝 수를 계산
                    last_prediction_step = None
                    for i in range(len(st.session_state.interactive_path) - 1, -1, -1):
                        entry = st.session_state.interactive_path[i]
                        if entry.get('has_prediction', False):
                            last_prediction_step = entry['step']
                            break
                    
                    if last_prediction_step is not None:
                        # 마지막 예측 이후 예측 없음 스텝 수 계산
                        no_prediction_count = 0
                        for i in range(len(st.session_state.interactive_path) - 1, -1, -1):
                            entry = st.session_state.interactive_path[i]
                            if entry['step'] > last_prediction_step and not entry.get('has_prediction', False):
                                no_prediction_count += 1
                            elif entry['step'] <= last_prediction_step:
                                break
                        st.session_state.interactive_current_interval = no_prediction_count
                    else:
                        # 아직 예측이 없었던 경우: 예측이 없었던 스텝 수를 카운트
                        no_prediction_count = 0
                        for entry in st.session_state.interactive_path:
                            if not entry.get('has_prediction', False):
                                no_prediction_count += 1
                        st.session_state.interactive_current_interval = no_prediction_count
                
                # 다음 prefix 생성 및 스텝 증가
                next_prefix = get_next_prefix(current_prefix, actual_value, interactive_window_size)
                st.session_state.interactive_current_prefix = next_prefix
                st.session_state.interactive_step = current_step + 1
                
                # 현재 스텝의 예측 결과 캐시 삭제
                if prediction_result_key in st.session_state:
                    del st.session_state[prediction_result_key]
                if prediction_interval_key in st.session_state:
                    del st.session_state[prediction_interval_key]
                
                st.rerun()
        
        with button_col3:
            # 이전 스텝으로 되돌리기
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("이전 스텝", key=f"prev_step_{current_step}", use_container_width=True, disabled=len(st.session_state.interactive_path) == 0):
                if len(st.session_state.interactive_path) > 0:
                    # 마지막 경로 항목 제거
                    last_entry = st.session_state.interactive_path.pop()
                    
                    # 이전 prefix로 복원
                    st.session_state.interactive_current_prefix = last_entry['prefix']
                    
                    # 스텝 번호 감소
                    st.session_state.interactive_step = current_step - 1
                    
                    # 간격 복원: interactive_path를 역순으로 순회하여 마지막 예측이 있었던 스텝을 찾고, 그 이후의 예측 없음 스텝 수를 계산
                    interval = 0
                    for entry in reversed(st.session_state.interactive_path):
                        if entry.get('has_prediction', False):
                            # 예측이 있었던 스텝을 찾으면 중단
                            break
                        interval += 1
                    st.session_state.interactive_current_interval = interval
        
        # 경로 히스토리 표시 (역순 - 최신순)
        if st.session_state.interactive_path:
            st.markdown("---")
            st.markdown("### 경로 히스토리")
            
            # 역순으로 정렬 (최신순이 위에)
            reversed_path = list(reversed(st.session_state.interactive_path))
            for idx, entry in enumerate(reversed_path, 1):
                if entry.get('has_prediction', True):
                    # 예측값이 있는 경우
                    status = "✅" if entry.get('is_correct') else "❌"
                    is_forced = entry.get('is_forced', False)
                    forced_marker = " ⚡" if is_forced else ""
                    predicted_str = f"`{entry['predicted']}{forced_marker}`"
                    confidence_str = f"({entry.get('confidence', 0):.1f}%)"
                    if is_forced:
                        confidence_str += " (강제)"
                else:
                    # 예측값이 없는 경우
                    status = "⚪"
                    predicted_str = "`-` (예측 없음)"
                    confidence_str = "(임계값 미만)"
                
                st.markdown(
                    f"**Step {entry['step']}**: `{entry['prefix']}` → "
                    f"예측: {predicted_str} {confidence_str} / "
                    f"실제: `{entry['actual']}` {status}"
                )
        
        # 통계 요약 (현재까지의 진행 상황)
        if st.session_state.interactive_path:
            st.markdown("---")
            st.markdown("### 현재까지 통계")
            
            total_steps = len(st.session_state.interactive_path)
            steps_with_prediction = sum(1 for e in st.session_state.interactive_path if e.get('has_prediction', True))
            correct_count = sum(1 for e in st.session_state.interactive_path if e.get('is_correct') == True)
            accuracy = (correct_count / steps_with_prediction * 100) if steps_with_prediction > 0 else 0
            
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("총 스텝", f"{total_steps}")
            with col2:
                st.metric("예측 수행", f"{steps_with_prediction}")
            with col3:
                st.metric("정확도", f"{accuracy:.1f}%")
            with col4:
                if steps_with_prediction > 0:
                    avg_confidence = sum(e.get('confidence', 0) for e in st.session_state.interactive_path if e.get('has_prediction', True)) / steps_with_prediction
                    st.metric("평균 신뢰도", f"{avg_confidence:.1f}%")
                else:
                    st.metric("평균 신뢰도", "N/A")
            
            # 상세 히스토리 (역순 - 최신순)
            st.markdown("---")
            st.markdown("### 상세 히스토리")
            
            history_data = []
            # 역순으로 정렬 (최신순이 위에)
            reversed_path = list(reversed(st.session_state.interactive_path))
            for entry in reversed_path:
                # 예측값이 있는 경우와 없는 경우 구분
                if entry.get('has_prediction', True) and entry.get('predicted') is not None:
                    # 예측값이 있는 경우
                    is_forced = entry.get('is_forced', False)
                    forced_marker = " ⚡" if is_forced else ""
                    predicted_value = f"{entry['predicted']}{forced_marker}"
                    predicted_prob = f"{entry['predictions'].get(entry['predicted'], 0):.1f}%"
                    if is_forced:
                        predicted_prob += " (강제)"
                    match_status = '✅' if entry.get('is_correct') == True else '❌'
                else:
                    # 예측값이 없는 경우
                    predicted_value = '-'
                    predicted_prob = 'N/A'
                    match_status = '⚪ (예측 없음)'
                
                history_data.append({
                    'Step': entry['step'],
                    'Prefix': entry['prefix'],
                    '예측값': predicted_value,
                    '예측확률': predicted_prob,
                    '실제값': entry['actual'],
                    '일치': match_status
                })
            
            history_df = pd.DataFrame(history_data)
            st.dataframe(history_df, use_container_width=True, hide_index=True)
            
            if st.button("새로 시작", type="primary"):
                st.session_state.interactive_path = []
                st.session_state.interactive_current_prefix = None
                st.session_state.interactive_step = 0
                st.session_state.interactive_current_interval = 0
    
    st.markdown("---")
    
    # 새로운 앙상블 투표 인터랙티브 시나리오 섹션 (완전 독립)
    st.header("🎯 앙상블 투표 인터랙티브 시나리오")
    
    # 설정 섹션 (st.form으로 그룹화하여 rerun 최소화)
    with st.form("ensemble_interactive_settings_form", clear_on_submit=False):
        st.markdown("### ⚙️ 설정")
        col_setting1, col_setting2, col_setting3 = st.columns(3)
        
        with col_setting1:
            ensemble_interactive_window_size = st.selectbox(
                "윈도우 크기",
                options=[5, 6, 7, 8, 9],
                index=2,  # 7을 기본값으로
                key="ensemble_interactive_window_size",
                help="예측에 사용할 윈도우 크기를 선택하세요"
            )
        
        with col_setting2:
            ensemble_interactive_use_threshold = st.checkbox(
                "임계값 전략 사용",
                value=True,
                key="ensemble_interactive_use_threshold",
                help="임계값 이상일 때만 예측하도록 설정"
            )
            ensemble_interactive_threshold = None
            if ensemble_interactive_use_threshold:
                ensemble_interactive_threshold = st.number_input(
                    "임계값 (%)",
                    min_value=0,
                    max_value=100,
                    value=60,
                    step=1,
                    key="ensemble_interactive_threshold",
                    help="이 신뢰도 이상일 때만 예측합니다"
                )
        
        with col_setting3:
            ensemble_interactive_max_interval = st.number_input(
                "최대 예측 없음 간격 (스텝)",
                min_value=1,
                max_value=20,
                value=6,
                step=1,
                key="ensemble_interactive_max_interval",
                help="이 간격을 넘기면 임계값 무시하고 강제 예측합니다"
            )
        
        # 설정 적용 버튼
        if st.form_submit_button("설정 적용", use_container_width=True):
            # 설정 변경 감지 및 초기화
            if 'last_ensemble_interactive_window_size' not in st.session_state:
                st.session_state.last_ensemble_interactive_window_size = ensemble_interactive_window_size
                st.session_state.last_ensemble_interactive_threshold = ensemble_interactive_threshold
                st.session_state.last_ensemble_interactive_max_interval = ensemble_interactive_max_interval
            elif (st.session_state.last_ensemble_interactive_window_size != ensemble_interactive_window_size or
                  st.session_state.last_ensemble_interactive_threshold != ensemble_interactive_threshold or
                  st.session_state.last_ensemble_interactive_max_interval != ensemble_interactive_max_interval):
                # 설정이 변경되었으면 초기화 및 캐시 무효화
                st.session_state.ensemble_interactive_path = []
                st.session_state.ensemble_interactive_current_prefix = None
                st.session_state.ensemble_interactive_step = 0
                st.session_state.ensemble_interactive_current_interval = 0
                
                # 모델 캐시 무효화
                if 'last_ensemble_interactive_window_size' in st.session_state:
                    old_window_size = st.session_state.last_ensemble_interactive_window_size
                    for model_type in ['frequency', 'weighted', 'trend']:
                        old_model_key = f'ensemble_model_{model_type}_{old_window_size}'
                        if old_model_key in st.session_state:
                            del st.session_state[old_model_key]
                
                st.session_state.last_ensemble_interactive_window_size = ensemble_interactive_window_size
                st.session_state.last_ensemble_interactive_threshold = ensemble_interactive_threshold
                st.session_state.last_ensemble_interactive_max_interval = ensemble_interactive_max_interval
                st.rerun()
    
    # Session state 초기화
    if 'ensemble_interactive_path' not in st.session_state:
        st.session_state.ensemble_interactive_path = []
        st.session_state.ensemble_interactive_current_prefix = None
        st.session_state.ensemble_interactive_step = 0
        st.session_state.ensemble_interactive_current_interval = 0
        st.session_state.last_ensemble_interactive_window_size = ensemble_interactive_window_size
        st.session_state.last_ensemble_interactive_threshold = ensemble_interactive_threshold
        st.session_state.last_ensemble_interactive_max_interval = ensemble_interactive_max_interval
    
    st.markdown("---")
    
    # 초기 prefix 입력 (st.form으로 그룹화하여 rerun 최소화)
    with st.form("start_ensemble_interactive_form", clear_on_submit=False):
        col1, col2 = st.columns([2, 1])
        
        with col1:
            ensemble_initial_prefix = st.text_input(
                "초기 Prefix 입력",
                value="bbbbb",
                key="ensemble_initial_prefix",
                help=f"다단계 예측을 시작할 초기 prefix를 입력하세요 (길이: {ensemble_interactive_window_size - 1})"
            )
        
        with col2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.form_submit_button("시작", type="primary", use_container_width=True):
                # prefix 길이 검증
                prefix_length = len(ensemble_initial_prefix)
                expected_prefix_length = ensemble_interactive_window_size - 1
                
                if prefix_length != expected_prefix_length:
                    st.warning(f"⚠️ Prefix 길이가 맞지 않습니다. 윈도우 크기 {ensemble_interactive_window_size}에 맞게 {expected_prefix_length}자리여야 합니다.")
                else:
                    st.session_state.ensemble_interactive_path = []
                    st.session_state.ensemble_interactive_current_prefix = ensemble_initial_prefix
                    st.session_state.ensemble_interactive_step = 1
                    st.session_state.ensemble_interactive_current_interval = 0
                    st.rerun()
    
    if st.button("초기화", use_container_width=True, key="reset_ensemble_interactive"):
        st.session_state.ensemble_interactive_path = []
        st.session_state.ensemble_interactive_current_prefix = None
        st.session_state.ensemble_interactive_step = 0
        st.session_state.ensemble_interactive_current_interval = 0
        st.rerun()
    
    # 인터랙티브 단계별 진행
    if st.session_state.ensemble_interactive_current_prefix and st.session_state.ensemble_interactive_step > 0:
        current_prefix = st.session_state.ensemble_interactive_current_prefix
        current_step = st.session_state.ensemble_interactive_step
        
        st.markdown("---")
        st.markdown(f"### Step {current_step}: `{current_prefix}`")
        
        # 현재 스텝의 예측 결과 캐시 키
        prediction_result_key = f'ensemble_interactive_prediction_step_{current_step}'
        prediction_interval_key = f'ensemble_interactive_interval_before_step_{current_step}'
        
        # 캐시된 예측 결과가 있으면 사용, 없으면 계산
        if prediction_result_key in st.session_state and st.session_state[prediction_result_key] is not None:
            prediction_result = st.session_state[prediction_result_key]
            df_strings = None
            train_ngrams = None
            frequency_model = None
            weighted_model = None
            trend_model = None
        else:
            # 모델 및 데이터 캐싱
            model_frequency_key = f'ensemble_model_frequency_{ensemble_interactive_window_size}'
            model_weighted_key = f'ensemble_model_weighted_{ensemble_interactive_window_size}'
            model_trend_key = f'ensemble_model_trend_{ensemble_interactive_window_size}'
            data_cache_key = f'ensemble_interactive_data_{ensemble_interactive_window_size}'
            ngrams_cache_key = f'ensemble_interactive_ngrams_{ensemble_interactive_window_size}'
            
            # 설정 변경 감지
            settings_changed = (
                'last_ensemble_interactive_window_size' not in st.session_state or
                st.session_state.last_ensemble_interactive_window_size != ensemble_interactive_window_size
            )
            
            # 캐시 확인 및 모델 구축
            if not settings_changed and (model_frequency_key in st.session_state and 
                                        model_weighted_key in st.session_state and 
                                        model_trend_key in st.session_state):
                # 캐시된 모델 및 데이터 재사용
                frequency_model = st.session_state[model_frequency_key]
                weighted_model = st.session_state[model_weighted_key]
                trend_model = st.session_state[model_trend_key]
                df_strings = st.session_state.get(data_cache_key)
                train_ngrams = st.session_state.get(ngrams_cache_key)
            else:
                # 모델 구축 및 캐싱
                df_strings = None
                train_ngrams = None
                frequency_model = None
                weighted_model = None
                trend_model = None
                
                with st.spinner("모델 구축 중..."):
                    # 학습 데이터 로드
                    df_strings = load_preprocessed_data()
                    if len(df_strings) == 0:
                        st.warning("⚠️ 전처리된 데이터가 없습니다.")
                        prediction_result = {
                            'predicted': None, 
                            'ratios': {}, 
                            'confidence': 0.0, 
                            'is_forced': False,
                            'individual_predictions': {},
                            'votes': {'b': 0, 'p': 0}
                        }
                    else:
                        # 학습 세트 분할
                        train_ratio = 80
                        split_idx = int(len(df_strings) * train_ratio / 100)
                        train_ids = df_strings.iloc[:split_idx]['id'].tolist()
                        
                        # N-gram 로드
                        train_ngrams = load_ngram_chunks(window_size=ensemble_interactive_window_size, grid_string_ids=train_ids)
                        
                        if len(train_ngrams) == 0:
                            st.warning(f"⚠️ 윈도우 크기 {ensemble_interactive_window_size}에 대한 학습 데이터가 없습니다.")
                            prediction_result = {
                                'predicted': None, 
                                'ratios': {}, 
                                'confidence': 0.0, 
                                'is_forced': False,
                                'individual_predictions': {},
                                'votes': {'b': 0, 'p': 0}
                            }
                        else:
                            # 모델 구축
                            frequency_model = build_frequency_model(train_ngrams)
                            weighted_model = build_weighted_model(train_ngrams)
                            trend_model = build_balance_recovery_trend_model_final(train_ngrams, ensemble_interactive_window_size)
                            
                            # 모델 및 데이터 캐싱
                            st.session_state[model_frequency_key] = frequency_model
                            st.session_state[model_weighted_key] = weighted_model
                            st.session_state[model_trend_key] = trend_model
                            st.session_state[data_cache_key] = df_strings
                            st.session_state[ngrams_cache_key] = train_ngrams
            
            # 예측 계산 (모델이 있는 경우)
            if frequency_model is not None and weighted_model is not None and trend_model is not None:
                # models_dict 구성
                models_dict = {
                    '빈도 기반': frequency_model,
                    '가중치 기반': weighted_model,
                    '균형 회복 트렌드': trend_model
                }
                
                # 예측 계산
                if ensemble_interactive_use_threshold and ensemble_interactive_threshold is not None:
                    # 강제 예측 전략 사용
                    current_interval_for_prediction = st.session_state.ensemble_interactive_current_interval
                    
                    # 디버깅: 예측 전 간격 상태 저장
                    st.session_state[prediction_interval_key] = current_interval_for_prediction
                    
                    # 앙상블 투표 예측
                    ensemble_result = predict_ensemble_new_voting(models_dict, current_prefix)
                    
                    # 임계값 체크 및 강제 예측 처리
                    if ensemble_result['confidence'] < ensemble_interactive_threshold:
                        # 신뢰도가 임계값 미만
                        if current_interval_for_prediction >= ensemble_interactive_max_interval:
                            # 강제 예측
                            prediction_result = {
                                **ensemble_result,
                                'is_forced': True
                            }
                        else:
                            # 예측 없음
                            prediction_result = {
                                'predicted': None,
                                'ratios': {},
                                'confidence': 0.0,
                                'is_forced': False,
                                'individual_predictions': ensemble_result.get('individual_predictions', {}),
                                'votes': ensemble_result.get('votes', {'b': 0, 'p': 0})
                            }
                    else:
                        # 정상 예측
                        prediction_result = {
                            **ensemble_result,
                            'is_forced': False
                        }
                else:
                    # 임계값 전략 미사용
                    prediction_result = predict_ensemble_new_voting(models_dict, current_prefix)
                    prediction_result['is_forced'] = False
            else:
                prediction_result = {
                    'predicted': None, 
                    'ratios': {}, 
                    'confidence': 0.0, 
                    'is_forced': False,
                    'individual_predictions': {},
                    'votes': {'b': 0, 'p': 0}
                }
            
            # 예측 결과를 session_state에 저장
            st.session_state[prediction_result_key] = prediction_result
        
        # 디버깅: 예측 결과 확인
        if ensemble_interactive_use_threshold and ensemble_interactive_threshold is not None:
            st.info(f"🔍 **디버깅**: 스텝={current_step}, prefix='{current_prefix}', 예측 전 간격={st.session_state.ensemble_interactive_current_interval}, 예측값={prediction_result.get('predicted')}, 강제예측={prediction_result.get('is_forced', False)}, 신뢰도={prediction_result.get('confidence', 0):.2f}%")
        
        # 예측값이 있는 경우
        if prediction_result.get('predicted') is not None:
            ratios = prediction_result.get('ratios', {})
            sorted_ratios = sorted(ratios.items(), key=lambda x: x[1], reverse=True) if ratios else []
            individual_predictions = prediction_result.get('individual_predictions', {})
            votes = prediction_result.get('votes', {'b': 0, 'p': 0})
            
            # 예측값 표시
            col1, col2 = st.columns([1, 1])
            
            with col1:
                st.markdown("**앙상블 예측 확률:**")
                if sorted_ratios:
                    for value, ratio in sorted_ratios:
                        color = "🟢" if ratio == max(ratios.values()) else "🟡"
                        st.markdown(f"{color} **'{value}'**: {ratio:.2f}%")
                        st.progress(ratio / 100)
                else:
                    st.info("예측 확률 정보 없음")
                
                # 개별 모델 예측 표시
                st.markdown("**개별 모델 예측:**")
                for method_name, pred_info in individual_predictions.items():
                    pred_value = pred_info.get('predicted', '-')
                    pred_conf = pred_info.get('confidence', 0)
                    st.markdown(f"- **{method_name}**: `{pred_value}` ({pred_conf:.1f}%)")
                
                # 투표 결과
                st.markdown("**투표 결과:**")
                st.markdown(f"- **b**: {votes.get('b', 0)}표")
                st.markdown(f"- **p**: {votes.get('p', 0)}표")
            
            with col2:
                st.markdown("**앙상블 최종 예측값:**")
                is_forced = prediction_result.get('is_forced', False)
                prediction_display = prediction_result['predicted']
                if is_forced:
                    prediction_display = f"{prediction_display} ⚡"  # 강제 예측 표시
                st.markdown(f"### `{prediction_display}`")
                confidence_display = f"{prediction_result.get('confidence', 0):.2f}%"
                if is_forced:
                    confidence_display += " (강제)"
                st.metric("신뢰도", confidence_display)
                
                # 디버깅: 간격 정보 표시
                if ensemble_interactive_use_threshold and ensemble_interactive_threshold is not None:
                    if prediction_interval_key in st.session_state:
                        interval_before = st.session_state[prediction_interval_key]
                        st.info(f"🔍 **디버깅 정보**: 예측 전 간격={interval_before}, 현재 간격={st.session_state.ensemble_interactive_current_interval}/{ensemble_interactive_max_interval}")
                    else:
                        st.info(f"🔍 **디버깅 정보**: 현재 간격={st.session_state.ensemble_interactive_current_interval}/{ensemble_interactive_max_interval}")
            
            # 다음 1개 스텝 실제값 경로 미리보기
            st.markdown("---")
            st.markdown('<p style="font-size: 1em; color: #666; margin-top: -10px;"><strong>다음 스텝 경로 미리보기:</strong></p>', unsafe_allow_html=True)
            
            # 다음 prefix 생성 (b와 p 두 경우 모두)
            next_prefix_b = get_next_prefix(current_prefix, 'b', ensemble_interactive_window_size)
            next_prefix_p = get_next_prefix(current_prefix, 'p', ensemble_interactive_window_size)
            
            # 미리보기 계산을 위해 모델이 필요하면 캐시에서 로드
            if frequency_model is None or weighted_model is None or trend_model is None:
                model_frequency_key = f'ensemble_model_frequency_{ensemble_interactive_window_size}'
                model_weighted_key = f'ensemble_model_weighted_{ensemble_interactive_window_size}'
                model_trend_key = f'ensemble_model_trend_{ensemble_interactive_window_size}'
                if model_frequency_key in st.session_state:
                    frequency_model = st.session_state[model_frequency_key]
                if model_weighted_key in st.session_state:
                    weighted_model = st.session_state[model_weighted_key]
                if model_trend_key in st.session_state:
                    trend_model = st.session_state[model_trend_key]
            
            # 다음 prefix에 대한 예측 (모델이 있는 경우)
            if frequency_model is not None and weighted_model is not None and trend_model is not None:
                next_pred_b = None
                next_pred_p = None
                next_conf_b = 0.0
                next_conf_p = 0.0
                next_forced_b = False
                next_forced_p = False
                
                try:
                    models_dict = {
                        '빈도 기반': frequency_model,
                        '가중치 기반': weighted_model,
                        '균형 회복 트렌드': trend_model
                    }
                    
                    if ensemble_interactive_use_threshold and ensemble_interactive_threshold is not None:
                        # 다음 스텝 예측용 간격 계산
                        if prediction_result.get('predicted') is not None:
                            next_interval = 0
                        else:
                            next_interval = st.session_state.ensemble_interactive_current_interval + 1
                        
                        # 간격을 고려하여 예측
                        next_result_b = predict_ensemble_new_voting(models_dict, next_prefix_b)
                        next_result_p = predict_ensemble_new_voting(models_dict, next_prefix_p)
                        
                        # 임계값 체크
                        if next_result_b['confidence'] < ensemble_interactive_threshold:
                            if next_interval >= ensemble_interactive_max_interval:
                                next_forced_b = True
                            else:
                                next_result_b = {'predicted': None, 'confidence': 0.0}
                        
                        if next_result_p['confidence'] < ensemble_interactive_threshold:
                            if next_interval >= ensemble_interactive_max_interval:
                                next_forced_p = True
                            else:
                                next_result_p = {'predicted': None, 'confidence': 0.0}
                        
                        next_forced_b = next_result_b.get('predicted') is not None and next_forced_b
                        next_forced_p = next_result_p.get('predicted') is not None and next_forced_p
                    else:
                        next_result_b = predict_ensemble_new_voting(models_dict, next_prefix_b)
                        next_result_p = predict_ensemble_new_voting(models_dict, next_prefix_p)
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
        else:
            # 예측값이 없는 경우 (임계값 미만 등)
            col1, col2 = st.columns([1, 1])
            
            with col1:
                st.markdown("**예측 확률:**")
                st.info("⚠️ 예측 데이터 없음")
                if ensemble_interactive_use_threshold and ensemble_interactive_threshold is not None:
                    if prediction_interval_key in st.session_state:
                        interval_before = st.session_state[prediction_interval_key]
                    else:
                        interval_before = st.session_state.ensemble_interactive_current_interval
                    st.info(f"🔍 **디버깅 정보**: 예측 전 간격={interval_before}, 현재 간격={st.session_state.ensemble_interactive_current_interval}/{ensemble_interactive_max_interval}")
                    st.caption(f"임계값({ensemble_interactive_threshold}%) 미만이거나 학습 데이터가 부족합니다.")
                else:
                    st.caption("학습 데이터가 부족합니다.")
            
            with col2:
                st.markdown("**예측값:**")
                st.markdown("### `-`")
                st.metric("신뢰도", "N/A")
            
            # 다음 1개 스텝 실제값 경로 미리보기
            st.markdown("---")
            st.markdown('<p style="font-size: 1em; color: #666; margin-top: -10px;"><strong>다음 스텝 경로 미리보기:</strong></p>', unsafe_allow_html=True)
            
            # 다음 prefix 생성 (b와 p 두 경우 모두)
            next_prefix_b = get_next_prefix(current_prefix, 'b', ensemble_interactive_window_size)
            next_prefix_p = get_next_prefix(current_prefix, 'p', ensemble_interactive_window_size)
            
            # 미리보기 계산을 위해 모델이 필요하면 캐시에서 로드
            if frequency_model is None or weighted_model is None or trend_model is None:
                model_frequency_key = f'ensemble_model_frequency_{ensemble_interactive_window_size}'
                model_weighted_key = f'ensemble_model_weighted_{ensemble_interactive_window_size}'
                model_trend_key = f'ensemble_model_trend_{ensemble_interactive_window_size}'
                if model_frequency_key in st.session_state:
                    frequency_model = st.session_state[model_frequency_key]
                if model_weighted_key in st.session_state:
                    weighted_model = st.session_state[model_weighted_key]
                if model_trend_key in st.session_state:
                    trend_model = st.session_state[model_trend_key]
            
            # 다음 prefix에 대한 예측 (모델이 있는 경우)
            if frequency_model is not None and weighted_model is not None and trend_model is not None:
                next_pred_b = None
                next_pred_p = None
                next_conf_b = 0.0
                next_conf_p = 0.0
                next_forced_b = False
                next_forced_p = False
                
                try:
                    models_dict = {
                        '빈도 기반': frequency_model,
                        '가중치 기반': weighted_model,
                        '균형 회복 트렌드': trend_model
                    }
                    
                    if ensemble_interactive_use_threshold and ensemble_interactive_threshold is not None:
                        # 다음 스텝 예측용 간격 계산
                        if prediction_result.get('predicted') is None:
                            next_interval = st.session_state.ensemble_interactive_current_interval + 1
                        else:
                            next_interval = 0
                        
                        next_result_b = predict_ensemble_new_voting(models_dict, next_prefix_b)
                        next_result_p = predict_ensemble_new_voting(models_dict, next_prefix_p)
                        
                        # 임계값 체크
                        if next_result_b['confidence'] < ensemble_interactive_threshold:
                            if next_interval >= ensemble_interactive_max_interval:
                                next_forced_b = True
                            else:
                                next_result_b = {'predicted': None, 'confidence': 0.0}
                        
                        if next_result_p['confidence'] < ensemble_interactive_threshold:
                            if next_interval >= ensemble_interactive_max_interval:
                                next_forced_p = True
                            else:
                                next_result_p = {'predicted': None, 'confidence': 0.0}
                        
                        next_forced_b = next_result_b.get('predicted') is not None and next_forced_b
                        next_forced_p = next_result_p.get('predicted') is not None and next_forced_p
                    else:
                        next_result_b = predict_ensemble_new_voting(models_dict, next_prefix_b)
                        next_result_p = predict_ensemble_new_voting(models_dict, next_prefix_p)
                        next_forced_b = False
                        next_forced_p = False
                    
                    next_pred_b = next_result_b.get('predicted')
                    next_pred_p = next_result_p.get('predicted')
                    next_conf_b = next_result_b.get('confidence', 0.0)
                    next_conf_p = next_result_p.get('confidence', 0.0)
                    next_forced_b = next_result_b.get('is_forced', False) or next_forced_b
                    next_forced_p = next_result_p.get('is_forced', False) or next_forced_p
                except:
                    pass
                
                # 경로 표시
                col_path1, col_path2 = st.columns(2)
                with col_path1:
                    if next_pred_b is not None:
                        forced_marker = " ⚡" if next_forced_b else ""
                        st.markdown(f'<p style="font-size: 0.95em; color: #333;">실제값 <strong>b</strong> → 다음 prefix: <code>{next_prefix_b}</code> → 예측: <code>{next_pred_b}{forced_marker}</code> ({next_conf_b:.1f}%)</p>', unsafe_allow_html=True)
                    else:
                        st.markdown(f'<p style="font-size: 0.95em; color: #666;">실제값 <strong>b</strong> → 다음 prefix: <code>{next_prefix_b}</code> → 예측: <code>-</code></p>', unsafe_allow_html=True)
                
                with col_path2:
                    if next_pred_p is not None:
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
        
        st.markdown("---")
        
        # 실제값 입력: "다음 스텝 (B)"와 "다음 스텝 (P)" 버튼
        st.markdown("**다음 스텝으로 진행하세요:**")
        
        # 버튼 스타일링을 위한 CSS
        st.markdown("""
        <style>
        button[kind="secondary"]:has-text("🔴") {
            background-color: transparent !important;
            color: #FF0000 !important;
            border: 2px solid #FF0000 !important;
            font-weight: bold !important;
        }
        button[kind="secondary"]:has-text("🔴"):hover {
            background-color: rgba(255, 0, 0, 0.1) !important;
        }
        button[kind="secondary"]:has-text("🔵") {
            background-color: transparent !important;
            color: #0066FF !important;
            border: 2px solid #0066FF !important;
            font-weight: bold !important;
        }
        button[kind="secondary"]:has-text("🔵"):hover {
            background-color: rgba(0, 102, 255, 0.1) !important;
        }
        </style>
        """, unsafe_allow_html=True)
        
        button_col1, button_col2, button_col3 = st.columns([1, 1, 2])
        
        with button_col1:
            if st.button("🔴 다음 스텝 (B)", key=f"ensemble_next_step_b_{current_step}", use_container_width=True):
                actual_value = 'b'
                
                # 경로 기록
                if prediction_result.get('predicted') is not None:
                    ratios = prediction_result.get('ratios', {})
                    is_forced = prediction_result.get('is_forced', False)
                    individual_predictions = prediction_result.get('individual_predictions', {})
                    votes = prediction_result.get('votes', {'b': 0, 'p': 0})
                    path_entry = {
                        'step': current_step,
                        'prefix': current_prefix,
                        'predictions': ratios,
                        'predicted': prediction_result['predicted'],
                        'actual': actual_value,
                        'is_correct': prediction_result['predicted'] == actual_value,
                        'confidence': prediction_result.get('confidence', 0.0),
                        'has_prediction': True,
                        'is_forced': is_forced,
                        'individual_predictions': individual_predictions,
                        'votes': votes
                    }
                else:
                    path_entry = {
                        'step': current_step,
                        'prefix': current_prefix,
                        'predictions': {},
                        'predicted': None,
                        'actual': actual_value,
                        'is_correct': None,
                        'confidence': 0.0,
                        'has_prediction': False,
                        'is_forced': False,
                        'individual_predictions': {},
                        'votes': {'b': 0, 'p': 0}
                    }
                
                st.session_state.ensemble_interactive_path.append(path_entry)
                
                # 간격 업데이트
                if path_entry.get('has_prediction', False):
                    st.session_state.ensemble_interactive_current_interval = 0
                else:
                    last_prediction_step = None
                    for i in range(len(st.session_state.ensemble_interactive_path) - 1, -1, -1):
                        entry = st.session_state.ensemble_interactive_path[i]
                        if entry.get('has_prediction', False):
                            last_prediction_step = entry['step']
                            break
                    
                    if last_prediction_step is not None:
                        no_prediction_count = 0
                        for i in range(len(st.session_state.ensemble_interactive_path) - 1, -1, -1):
                            entry = st.session_state.ensemble_interactive_path[i]
                            if entry['step'] > last_prediction_step and not entry.get('has_prediction', False):
                                no_prediction_count += 1
                            elif entry['step'] <= last_prediction_step:
                                break
                        st.session_state.ensemble_interactive_current_interval = no_prediction_count
                    else:
                        no_prediction_count = 0
                        for entry in st.session_state.ensemble_interactive_path:
                            if not entry.get('has_prediction', False):
                                no_prediction_count += 1
                        st.session_state.ensemble_interactive_current_interval = no_prediction_count
                
                # 다음 prefix 생성 및 스텝 증가
                next_prefix = get_next_prefix(current_prefix, actual_value, ensemble_interactive_window_size)
                st.session_state.ensemble_interactive_current_prefix = next_prefix
                st.session_state.ensemble_interactive_step = current_step + 1
                
                # 현재 스텝의 예측 결과 캐시 삭제
                if prediction_result_key in st.session_state:
                    del st.session_state[prediction_result_key]
                if prediction_interval_key in st.session_state:
                    del st.session_state[prediction_interval_key]
                
                st.rerun()
        
        with button_col2:
            if st.button("🔵 다음 스텝 (P)", key=f"ensemble_next_step_p_{current_step}", use_container_width=True):
                actual_value = 'p'
                
                # 경로 기록
                if prediction_result.get('predicted') is not None:
                    ratios = prediction_result.get('ratios', {})
                    is_forced = prediction_result.get('is_forced', False)
                    individual_predictions = prediction_result.get('individual_predictions', {})
                    votes = prediction_result.get('votes', {'b': 0, 'p': 0})
                    path_entry = {
                        'step': current_step,
                        'prefix': current_prefix,
                        'predictions': ratios,
                        'predicted': prediction_result['predicted'],
                        'actual': actual_value,
                        'is_correct': prediction_result['predicted'] == actual_value,
                        'confidence': prediction_result.get('confidence', 0.0),
                        'has_prediction': True,
                        'is_forced': is_forced,
                        'individual_predictions': individual_predictions,
                        'votes': votes
                    }
                else:
                    path_entry = {
                        'step': current_step,
                        'prefix': current_prefix,
                        'predictions': {},
                        'predicted': None,
                        'actual': actual_value,
                        'is_correct': None,
                        'confidence': 0.0,
                        'has_prediction': False,
                        'is_forced': False,
                        'individual_predictions': {},
                        'votes': {'b': 0, 'p': 0}
                    }
                
                st.session_state.ensemble_interactive_path.append(path_entry)
                
                # 간격 업데이트
                if path_entry.get('has_prediction', False):
                    st.session_state.ensemble_interactive_current_interval = 0
                else:
                    last_prediction_step = None
                    for i in range(len(st.session_state.ensemble_interactive_path) - 1, -1, -1):
                        entry = st.session_state.ensemble_interactive_path[i]
                        if entry.get('has_prediction', False):
                            last_prediction_step = entry['step']
                            break
                    
                    if last_prediction_step is not None:
                        no_prediction_count = 0
                        for i in range(len(st.session_state.ensemble_interactive_path) - 1, -1, -1):
                            entry = st.session_state.ensemble_interactive_path[i]
                            if entry['step'] > last_prediction_step and not entry.get('has_prediction', False):
                                no_prediction_count += 1
                            elif entry['step'] <= last_prediction_step:
                                break
                        st.session_state.ensemble_interactive_current_interval = no_prediction_count
                    else:
                        no_prediction_count = 0
                        for entry in st.session_state.ensemble_interactive_path:
                            if not entry.get('has_prediction', False):
                                no_prediction_count += 1
                        st.session_state.ensemble_interactive_current_interval = no_prediction_count
                
                # 다음 prefix 생성 및 스텝 증가
                next_prefix = get_next_prefix(current_prefix, actual_value, ensemble_interactive_window_size)
                st.session_state.ensemble_interactive_current_prefix = next_prefix
                st.session_state.ensemble_interactive_step = current_step + 1
                
                # 현재 스텝의 예측 결과 캐시 삭제
                if prediction_result_key in st.session_state:
                    del st.session_state[prediction_result_key]
                if prediction_interval_key in st.session_state:
                    del st.session_state[prediction_interval_key]
                
                st.rerun()
        
        with button_col3:
            # 이전 스텝으로 되돌리기
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("이전 스텝", key=f"ensemble_prev_step_{current_step}", use_container_width=True, disabled=len(st.session_state.ensemble_interactive_path) == 0):
                if len(st.session_state.ensemble_interactive_path) > 0:
                    # 마지막 경로 항목 제거
                    last_entry = st.session_state.ensemble_interactive_path.pop()
                    
                    # 이전 prefix로 복원
                    st.session_state.ensemble_interactive_current_prefix = last_entry['prefix']
                    
                    # 스텝 번호 감소
                    st.session_state.ensemble_interactive_step = current_step - 1
                    
                    # 간격 복원
                    interval = 0
                    for entry in reversed(st.session_state.ensemble_interactive_path):
                        if entry.get('has_prediction', False):
                            break
                        interval += 1
                    st.session_state.ensemble_interactive_current_interval = interval
                    
                    st.rerun()
        
        # 경로 히스토리 표시 (역순 - 최신순)
        if st.session_state.ensemble_interactive_path:
            st.markdown("---")
            st.markdown("### 경로 히스토리")
            
            # 역순으로 정렬 (최신순이 위에)
            reversed_path = list(reversed(st.session_state.ensemble_interactive_path))
            for idx, entry in enumerate(reversed_path, 1):
                if entry.get('has_prediction', True):
                    # 예측값이 있는 경우
                    status = "✅" if entry.get('is_correct') else "❌"
                    is_forced = entry.get('is_forced', False)
                    forced_marker = " ⚡" if is_forced else ""
                    predicted_str = f"`{entry['predicted']}{forced_marker}`"
                    confidence_str = f"({entry.get('confidence', 0):.1f}%)"
                    if is_forced:
                        confidence_str += " (강제)"
                else:
                    # 예측값이 없는 경우
                    status = "⚪"
                    predicted_str = "`-` (예측 없음)"
                    confidence_str = "(임계값 미만)"
                
                st.markdown(
                    f"**Step {entry['step']}**: `{entry['prefix']}` → "
                    f"예측: {predicted_str} {confidence_str} / "
                    f"실제: `{entry['actual']}` {status}"
                )
        
        # 통계 요약 (현재까지의 진행 상황)
        if st.session_state.ensemble_interactive_path:
            st.markdown("---")
            st.markdown("### 현재까지 통계")
            
            total_steps = len(st.session_state.ensemble_interactive_path)
            steps_with_prediction = sum(1 for e in st.session_state.ensemble_interactive_path if e.get('has_prediction', True))
            correct_count = sum(1 for e in st.session_state.ensemble_interactive_path if e.get('is_correct') == True)
            accuracy = (correct_count / steps_with_prediction * 100) if steps_with_prediction > 0 else 0
            
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("총 스텝", f"{total_steps}")
            with col2:
                st.metric("예측 수행", f"{steps_with_prediction}")
            with col3:
                st.metric("정확도", f"{accuracy:.1f}%")
            with col4:
                if steps_with_prediction > 0:
                    avg_confidence = sum(e.get('confidence', 0) for e in st.session_state.ensemble_interactive_path if e.get('has_prediction', True)) / steps_with_prediction
                    st.metric("평균 신뢰도", f"{avg_confidence:.1f}%")
                else:
                    st.metric("평균 신뢰도", "N/A")
            
            # 상세 히스토리 (역순 - 최신순)
            st.markdown("---")
            st.markdown("### 상세 히스토리")
            
            history_data = []
            # 역순으로 정렬 (최신순이 위에)
            reversed_path = list(reversed(st.session_state.ensemble_interactive_path))
            for entry in reversed_path:
                # 예측값이 있는 경우와 없는 경우 구분
                if entry.get('has_prediction', True) and entry.get('predicted') is not None:
                    # 예측값이 있는 경우
                    is_forced = entry.get('is_forced', False)
                    forced_marker = " ⚡" if is_forced else ""
                    predicted_value = f"{entry['predicted']}{forced_marker}"
                    predicted_prob = f"{entry['predictions'].get(entry['predicted'], 0):.1f}%"
                    if is_forced:
                        predicted_prob += " (강제)"
                    match_status = '✅' if entry.get('is_correct') == True else '❌'
                    
                    # 개별 모델 예측값
                    individual_preds = entry.get('individual_predictions', {})
                    individual_str = ", ".join([f"{k}:{v.get('predicted', '-')}" for k, v in individual_preds.items()])
                    votes = entry.get('votes', {'b': 0, 'p': 0})
                    votes_str = f"b:{votes.get('b', 0)}, p:{votes.get('p', 0)}"
                else:
                    # 예측값이 없는 경우
                    predicted_value = '-'
                    predicted_prob = 'N/A'
                    match_status = '⚪ (예측 없음)'
                    individual_str = '-'
                    votes_str = '-'
                
                history_data.append({
                    'Step': entry['step'],
                    'Prefix': entry['prefix'],
                    '앙상블 예측': predicted_value,
                    '앙상블 확률': predicted_prob,
                    '개별 모델': individual_str,
                    '투표': votes_str,
                    '실제값': entry['actual'],
                    '일치': match_status
                })
            
            history_df = pd.DataFrame(history_data)
            st.dataframe(history_df, use_container_width=True, hide_index=True)
            
            if st.button("새로 시작", type="primary", key="ensemble_new_start"):
                st.session_state.ensemble_interactive_path = []
                st.session_state.ensemble_interactive_current_prefix = None
                st.session_state.ensemble_interactive_step = 0
                st.session_state.ensemble_interactive_current_interval = 0
                st.rerun()
    
    st.markdown("---")
    
    # SVG 파싱 섹션
    st.header("📥 SVG 파싱")
    
    st.markdown("""
    SVG 코드를 입력하여 Grid String을 추출합니다.
    파싱된 Grid String은 자동으로 저장되어 게임 시나리오 검증에서 사용할 수 있습니다.
    """)
    
    # SVG 입력 리셋을 위한 key 관리
    if 'svg_input_key_counter' not in st.session_state:
        st.session_state.svg_input_key_counter = 0
    
    svg_code_input = st.text_area(
        "SVG 코드 입력",
        value="",
        help="SVG 코드를 붙여넣으세요",
        key=f"svg_input_{st.session_state.svg_input_key_counter}",
        height=100
    )
    
    col_svg1, col_svg2 = st.columns([3, 1])
    
    with col_svg1:
        if svg_code_input:
            st.info("SVG 코드를 입력한 후 '파싱' 버튼을 클릭하세요.")
        if 'parsed_grid_string' in st.session_state and st.session_state.parsed_grid_string:
            st.success(f"✅ 파싱된 Grid String이 있습니다. (길이: {len(st.session_state.parsed_grid_string)})")
            # 파싱된 Grid String 표시
            st.markdown("**파싱된 Grid String:**")
            st.code(st.session_state.parsed_grid_string, language=None)
    
    with col_svg2:
        st.markdown("<br>", unsafe_allow_html=True)
        parse_button = st.button("파싱", type="primary", use_container_width=True, key="parse_svg_button")
    
    with col_svg2:
        st.markdown("<br>", unsafe_allow_html=True)
        save_button = st.button("DB 저장", use_container_width=True, key="save_parsed_to_db_button", 
                                disabled=('parsed_grid_string' not in st.session_state or not st.session_state.parsed_grid_string))
    
    with col_svg2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("리셋", use_container_width=True, key="reset_svg_input_button"):
            # SVG 입력 초기화 (key 변경으로 text_area 리셋)
            st.session_state.svg_input_key_counter += 1
            # 파싱된 Grid String 초기화
            if 'parsed_grid_string' in st.session_state:
                del st.session_state.parsed_grid_string
            st.rerun()
    
    if parse_button and svg_code_input:
        if not svg_code_input or not svg_code_input.strip():
            st.warning("⚠️ SVG 코드를 입력해주세요.")
        else:
            try:
                # 파싱 전에 이전 파싱 결과 초기화 (중복 방지)
                if 'parsed_grid_string' in st.session_state:
                    del st.session_state.parsed_grid_string
                
                with st.spinner("SVG 파싱 중..."):
                    # SVG 파싱
                    parsed_grid = parse_bead_road_svg(svg_code_input)
                    
                    # Grid를 문자열로 변환
                    grid_string_parsed = grid_to_string_column_wise(parsed_grid)
                    
                    if grid_string_parsed:
                        # Session state에 저장하여 다음 단계에서 사용
                        st.session_state.parsed_grid_string = grid_string_parsed
                        
                        st.success(f"✅ 파싱 완료! Grid String 길이: {len(grid_string_parsed)}")
                        
                        # 파싱된 Grid String 전체 표시
                        st.markdown("**파싱된 Grid String:**")
                        st.code(grid_string_parsed, language=None)
                        
                        # 파싱 완료 후 버튼 상태 초기화를 위해 rerun
                        st.rerun()
                    else:
                        st.warning("⚠️ 파싱된 Grid에서 유효한 문자열을 추출할 수 없습니다.")
            except Exception as e:
                st.error(f"❌ SVG 파싱 중 오류 발생: {str(e)}")
                import traceback
                st.error(f"상세 오류: {traceback.format_exc()}")
    
    # DB 저장 기능
    if save_button:
        if 'parsed_grid_string' in st.session_state and st.session_state.parsed_grid_string:
            try:
                # DB에 저장
                grid_string_to_save = st.session_state.parsed_grid_string
                save_parsed_grid_string_to_db(grid_string_to_save)
                st.success("✅ DB 저장 완료!")
            except Exception as e:
                st.error(f"❌ DB 저장 중 오류 발생: {str(e)}")
        else:
            st.warning("⚠️ 저장할 Grid String이 없습니다. 먼저 SVG를 파싱해주세요.")
    
    st.markdown("---")
    
    # 게임 시나리오 검증 섹션
    st.header("🎮 게임 시나리오 검증")
    
    st.markdown("""
    **검증 규칙:**
    - Grid String을 입력하면 자동으로 슬라이딩 윈도우로 prefix들을 추출합니다
    - 각 prefix에 대해 예측값과 실제값을 비교합니다
    """)
    
    # Grid String 입력
    grid_string_input = st.text_input(
        "Grid String 입력",
        value="",
        help="검증할 문자열을 입력하세요 (예: 'bbbbppbbppbbpp...')",
        key="game_grid_string_text_input"
    )
    
    if grid_string_input:
        st.info(f"입력된 문자열 길이: {len(grid_string_input)}")
    
    st.markdown("---")
    
    # 게임 시나리오 자동 검증 섹션
    st.header("🎮 게임 시나리오 자동 검증")
    
    st.markdown("""
    **검증 규칙:**
    - Grid String을 입력하면 자동으로 슬라이딩 윈도우로 prefix들을 추출합니다
    - 각 prefix에 대해 예측값과 실제값을 비교합니다
    - **모든 스텝을 진행**하여 전체 검증을 수행합니다
    - **검증 목표**: 불일치 값이 5개 연속되는 결과가 있는지 확인합니다
    """)
    
    # 설정 섹션
    st.markdown("### ⚙️ 설정")
    col_setting1, col_setting2, col_setting3 = st.columns(3)
    
    with col_setting1:
        game_window_size = st.selectbox(
            "윈도우 크기",
            options=[5, 6, 7, 8, 9],
            index=2,  # 7을 기본값으로
            key="game_window_size",
            help="검증에 사용할 윈도우 크기를 선택하세요"
        )
    
    with col_setting2:
        game_method = st.selectbox(
            "예측 방법",
            options=["빈도 기반", "가중치 기반", "안전 우선"],
            index=0,
            key="game_method",
            help="검증에 사용할 예측 방법을 선택하세요"
        )
    
    with col_setting3:
        game_use_threshold = st.checkbox(
            "임계값 전략 사용",
            value=True,
            key="game_use_threshold",
            help="임계값 이상일 때만 예측하도록 설정"
        )
        game_threshold = None
        if game_use_threshold:
            game_threshold = st.number_input(
                "임계값 (%)",
                min_value=0,
                max_value=100,
                value=60,
                step=1,
                key="game_threshold",
                help="이 신뢰도 이상일 때만 예측합니다"
            )
    
    st.markdown("---")
    
    # Grid String 입력 (직접 입력만)
    grid_string_input = st.text_area(
        "Grid String 입력",
        value="",
        help="검증할 문자열을 입력하세요 (예: 'bbbbppbbppbbpp...') 또는 위에서 파싱한 Grid String을 사용하세요",
        height=100,
        key="game_grid_string_input"
    )
    
    # 저장된 Grid String 사용 옵션
    if 'parsed_grid_string' in st.session_state and st.session_state.parsed_grid_string:
        use_parsed = st.checkbox(
            "파싱된 Grid String 사용",
            value=False,
            key="use_parsed_grid_string",
            help="위에서 파싱한 Grid String을 사용합니다"
        )
        
        if use_parsed:
            grid_string_input = st.session_state.parsed_grid_string
            st.info(f"✅ 파싱된 Grid String을 사용합니다. (길이: {len(grid_string_input)})")
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        st.markdown(f"**입력된 문자열 길이**: {len(grid_string_input)}")
        if grid_string_input:
            st.markdown(f"**문자 구성**: 'b': {grid_string_input.count('b')}개, 'p': {grid_string_input.count('p')}개")
    
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        start_validation_button = st.button("검증 시작", type="primary", use_container_width=True, key="start_game_validation")
    
    if start_validation_button and grid_string_input:
        # 문자열 검증
        if len(grid_string_input) < game_window_size:
            st.warning(f"⚠️ 문자열 길이가 너무 짧습니다. 최소 {game_window_size}자 이상이어야 합니다.")
        else:
            # 모델 구축
            with st.spinner("모델 구축 중..."):
                df_strings = load_preprocessed_data()
                if len(df_strings) == 0:
                    st.warning("⚠️ 전처리된 데이터가 없습니다.")
                else:
                    # 학습 세트 분할
                    train_ratio = 80
                    split_idx = int(len(df_strings) * train_ratio / 100)
                    train_ids = df_strings.iloc[:split_idx]['id'].tolist()
                    
                    # N-gram 로드
                    train_ngrams = load_ngram_chunks(window_size=game_window_size, grid_string_ids=train_ids)
                    
                    if len(train_ngrams) == 0:
                        st.warning(f"⚠️ 윈도우 크기 {game_window_size}에 대한 학습 데이터가 없습니다.")
                    else:
                        # 모델 구축
                        if game_method == "빈도 기반":
                            game_model = build_frequency_model(train_ngrams)
                        # elif game_method == "마르코프 체인":
                        #     game_model = build_markov_model(train_ngrams)
                        elif game_method == "가중치 기반":
                            game_model = build_weighted_model(train_ngrams)
                        elif game_method == "안전 우선":
                            game_model = build_safety_first_model(train_ngrams)
                        else:  # 기본값: 빈도 기반
                            game_model = build_frequency_model(train_ngrams)
                        
                        # 전략 함수 설정
                        strategy_func = None
                        if game_use_threshold and game_threshold is not None:
                            strategy_func = lambda m, p, method: predict_confidence_threshold(
                                m, p, method, threshold=game_threshold
                            )
                        
                        # 게임 시뮬레이션 실행
                        with st.spinner("게임 시나리오 검증 중..."):
                            game_result = simulate_game_scenario(
                                game_model,
                                grid_string_input,
                                game_window_size,
                                game_method,
                                strategy_func=strategy_func
                            )
                        
                        st.markdown("---")
                        
                        # 결과 표시
                        display_game_result(game_result)
                        
                        # 추가 정보
                        if game_result['history']:
                            st.markdown("---")
                            st.markdown("### 예측 확률 상세")
                            
                            # 마지막 몇 개 스텝의 예측 확률 표시
                            recent_history = game_result['history'][-5:] if len(game_result['history']) > 5 else game_result['history']
                            
                            for entry in recent_history:
                                with st.expander(f"Step {entry['step']}: `{entry['prefix']}` → 예측: `{entry['predicted']}`, 실제: `{entry['actual']}`"):
                                    ratios = entry['ratios']
                                    sorted_ratios = sorted(ratios.items(), key=lambda x: x[1], reverse=True)
                                    
                                    for value, ratio in sorted_ratios:
                                        is_predicted = (value == entry['predicted'])
                                        label = f"**'{value}'**: {ratio:.2f}% {'(예측값)' if is_predicted else ''}"
                                        st.progress(ratio / 100, text=label)
                        
                        # 결과 저장 기능
                        st.markdown("---")
                        st.markdown("### 결과 저장")
                        
                        save_result = st.checkbox("검증 결과를 DB에 저장", value=False, key="save_validation_result")
                        
                        if save_result:
                            if st.button("저장", type="primary", key="save_validation"):
                                try:
                                    validation_id = save_scenario_validation_result(
                                        game_result,
                                        grid_string_input,
                                        game_window_size,
                                        game_method,
                                        train_ratio
                                    )
                                    st.success(f"✅ 검증 결과가 저장되었습니다. Validation ID: `{validation_id}`")
                                    st.info(f"💡 저장된 데이터: 세션 요약, {len(game_result['history'])}개 스텝 상세, {len(game_result['consecutive_5_positions'])}개 연속 불일치 5개 발생 위치")
                                except Exception as e:
                                    st.error(f"❌ 저장 중 오류 발생: {str(e)}")
    
    st.markdown("---")
    
    # 윈도우 크기 최적화 (전체 DB) 섹션
    st.header("🔍 윈도우 크기 최적화 (전체 DB)")
    
    st.markdown("""
    **시계열 누적 테스트 방식:**
    - DB의 모든 grid_string을 시계열 순서(created_at)로 정렬합니다
    - 각 grid_string에 대해:
      - 이전까지의 모든 grid_string의 ngram_chunks로 모델 구축
      - 현재 grid_string을 테스트
      - 결과 수집
    - 각 윈도우 크기별로 전체 성능을 평가하여 최적의 윈도우 크기를 추천합니다
    """)
    
    # 윈도우 크기 범위 입력
    col_range1, col_range2 = st.columns(2)
    with col_range1:
        window_size_start = st.number_input(
            "윈도우 크기 시작",
            min_value=5,
            max_value=15,
            value=6,
            step=1,
            help="테스트할 윈도우 크기의 시작값",
            key="opt_window_start"
        )
    with col_range2:
        window_size_end = st.number_input(
            "윈도우 크기 끝",
            min_value=5,
            max_value=15,
            value=9,
            step=1,
            help="테스트할 윈도우 크기의 끝값",
            key="opt_window_end"
        )
    
    # 유효성 검사
    if window_size_start > window_size_end:
        st.warning("⚠️ 시작값이 끝값보다 큽니다.")
        window_sizes_list = []
    else:
        window_sizes_list = list(range(window_size_start, window_size_end + 1))
        st.info(f"테스트할 윈도우 크기: {window_sizes_list}")
    
    # 전체 DB 테스트 버튼
    col_opt1, col_opt2 = st.columns([3, 1])
    with col_opt1:
        st.markdown(f"**DB 전체 Grid String 수**: {len(df_strings)}개")
        if len(df_strings) == 0:
            st.warning("⚠️ DB에 grid_string이 없습니다. 먼저 데이터를 전처리하거나 파싱하여 저장하세요.")
    with col_opt2:
        st.markdown("<br>", unsafe_allow_html=True)
        start_optimization_button = st.button("전체 DB 최적화 테스트", type="primary", use_container_width=True, key="start_optimization_test")
    
    # 전체 DB 최적화 테스트 실행
    if start_optimization_button and window_sizes_list and len(df_strings) > 0:
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        try:
            # 배치 테스트 실행
            total_tests = len(window_sizes_list)
            results_by_window = {}
            
            for idx, test_window_size in enumerate(window_sizes_list):
                status_text.text(f"윈도우 크기 {test_window_size} 테스트 중... ({idx + 1}/{total_tests})")
                progress_bar.progress((idx + 1) / total_tests)
                
                # 각 윈도우 크기별로 전체 DB 테스트
                window_result = batch_test_window_sizes_on_all_data(
                    df_strings,
                    [test_window_size],
                    prediction_method,
                    train_ratio
                )
                results_by_window.update(window_result)
            
            progress_bar.empty()
            status_text.empty()
            
            st.markdown("---")
            
            # 비교 결과 표시
            display_window_size_comparison_all_data(results_by_window)
            
        except Exception as e:
            progress_bar.empty()
            status_text.empty()
            st.error(f"❌ 최적화 테스트 중 오류 발생: {str(e)}")
            import traceback
            st.error(f"상세 오류: {traceback.format_exc()}")
    
    st.markdown("---")
    
    # 전략 탐색 및 테스트 섹션
    st.header("🔬 전략 탐색 및 테스트")
    
    st.markdown("""
    **목표:**
    - 연속 불일치 5회 이상 OR 연속 일치 5회 이상 = 실패로 판단
    - 다양한 예측 전략을 자동으로 테스트
    - 최적의 전략을 찾아 추천
    
    **사용 가능한 전략:**
    - 기본 전략: 빈도 기반, 가중치 기반, 안전 우선
    - 앙상블 전략: 투표 방식, 가중 평균 방식
    - 신뢰도 임계값 전략: 신뢰도가 낮으면 예측 보류 또는 반대 예측
    - 역전략: 예측과 반대로 예측
    """)
    
    # 전략 선택
    st.markdown("### 전략 선택")
    
    col_strategy1, col_strategy2 = st.columns(2)
    
    with col_strategy1:
        use_basic = st.checkbox("기본 전략 (빈도 기반)", value=True, key="strategy_basic")
        use_ensemble_voting = st.checkbox("앙상블 - 투표 방식", value=False, key="strategy_ensemble_voting")
        use_ensemble_weighted = st.checkbox("앙상블 - 가중 평균", value=False, key="strategy_ensemble_weighted")
        use_confidence_threshold = st.checkbox("신뢰도 임계값 (60%)", value=False, key="strategy_confidence_threshold")
    
    with col_strategy2:
        use_confidence_reverse = st.checkbox("신뢰도 역전 (50%)", value=False, key="strategy_confidence_reverse")
        use_reverse = st.checkbox("역전략", value=False, key="strategy_reverse")
        # use_markov = st.checkbox("마르코프 체인", value=False, key="strategy_markov")  # 제거됨
        use_weighted = st.checkbox("가중치 기반", value=False, key="strategy_weighted")
        use_safety_first = st.checkbox("안전 우선", value=False, key="strategy_safety_first")
    
    # 윈도우 크기 범위 설정
    st.markdown("### 테스트 설정")
    
    col_window1, col_window2 = st.columns(2)
    with col_window1:
        strategy_window_start = st.number_input(
            "윈도우 크기 시작",
            min_value=5,
            max_value=15,
            value=6,
            step=1,
            help="테스트할 윈도우 크기의 시작값",
            key="strategy_window_start"
        )
    with col_window2:
        strategy_window_end = st.number_input(
            "윈도우 크기 끝",
            min_value=5,
            max_value=15,
            value=9,
            step=1,
            help="테스트할 윈도우 크기의 끝값",
            key="strategy_window_end"
        )
    
    # 유효성 검사
    if strategy_window_start > strategy_window_end:
        st.warning("⚠️ 시작값이 끝값보다 큽니다.")
        strategy_window_sizes_list = []
    else:
        strategy_window_sizes_list = list(range(strategy_window_start, strategy_window_end + 1))
        st.info(f"테스트할 윈도우 크기: {strategy_window_sizes_list}")
    
    # 기본 예측 방법 선택
    base_method = st.selectbox(
        "기본 예측 방법",
        options=["빈도 기반", "가중치 기반", "안전 우선"],
        index=0,
        key="strategy_base_method"
    )
    
    # 전략 테스트 버튼
    col_test1, col_test2 = st.columns([3, 1])
    with col_test1:
        st.markdown(f"**DB 전체 Grid String 수**: {len(df_strings)}개")
        if len(df_strings) == 0:
            st.warning("⚠️ DB에 grid_string이 없습니다. 먼저 데이터를 전처리하거나 파싱하여 저장하세요.")
    with col_test2:
        st.markdown("<br>", unsafe_allow_html=True)
        start_strategy_test_button = st.button("전략 테스트 실행", type="primary", use_container_width=True, key="start_strategy_test")
    
    # 전략 테스트 실행
    if start_strategy_test_button and strategy_window_sizes_list and len(df_strings) > 0:
        # 선택된 전략 수집
        selected_strategies = []
        
        if use_basic:
            selected_strategies.append((lambda m, p, method: predict_for_prefix(m, p, method), "기본_빈도기반"))
        
        # if use_markov:
        #     selected_strategies.append((lambda m, p, method: predict_for_prefix(m, p, "마르코프 체인"), "마르코프체인"))  # 제거됨
        
        if use_safety_first:
            # 안전 우선 모델은 히스토리가 필요하므로 래퍼 함수 생성
            # simulate_game_scenario 내부에서 히스토리를 전달하도록 래퍼 사용
            # 래퍼는 클로저를 사용하여 prediction_history와 consecutive_mismatches를 캡처
            def create_safety_first_strategy_wrapper():
                # prediction_history와 consecutive_mismatches를 저장할 변수
                history_ref = {'data': []}
                mismatches_ref = {'count': 0}
                
                def wrapper(m, p, method):
                    # simulate_game_scenario에서 히스토리를 업데이트하므로
                    # 여기서는 현재 히스토리를 사용하여 예측
                    return predict_safety_first(m, p, recent_history=history_ref['data'], consecutive_mismatches=mismatches_ref['count'])
                
                # 래퍼에 히스토리 참조 추가 (simulate_game_scenario에서 업데이트)
                wrapper._history_ref = history_ref
                wrapper._mismatches_ref = mismatches_ref
                return wrapper
            
            safety_wrapper = create_safety_first_strategy_wrapper()
            selected_strategies.append((safety_wrapper, "안전우선"))
        
        if use_weighted:
            selected_strategies.append((lambda m, p, method: predict_for_prefix(m, p, "가중치 기반"), "가중치기반"))
        
        if use_ensemble_voting:
            selected_strategies.append((lambda m, p, method: predict_ensemble_voting(m, p), "앙상블_투표"))
        
        if use_ensemble_weighted:
            selected_strategies.append((lambda m, p, method: predict_ensemble_weighted(m, p), "앙상블_가중평균"))
        
        if use_confidence_threshold:
            selected_strategies.append((lambda m, p, method: predict_confidence_threshold(m, p, method, threshold=60), "신뢰도임계값_60"))
        
        if use_confidence_reverse:
            selected_strategies.append((lambda m, p, method: predict_confidence_reverse(m, p, method, threshold=50), "신뢰도역전_50"))
        
        if use_reverse:
            selected_strategies.append((lambda m, p, method: predict_reverse(m, p, method), "역전략"))
        
        if not selected_strategies:
            st.warning("⚠️ 최소 하나의 전략을 선택해주세요.")
        else:
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            try:
                # 배치 테스트 실행
                total_strategies = len(selected_strategies)
                all_results = {}
                
                for idx, (strategy_func, strategy_name) in enumerate(selected_strategies):
                    status_text.text(f"전략 '{strategy_name}' 테스트 중... ({idx + 1}/{total_strategies})")
                    progress_bar.progress((idx + 1) / total_strategies)
                    
                    # 각 전략별로 전체 DB 테스트
                    strategy_results = test_strategy_on_all_data(
                        strategy_func,
                        strategy_name,
                        df_strings,
                        strategy_window_sizes_list,
                        base_method,
                        train_ratio
                    )
                    all_results[strategy_name] = strategy_results
                
                progress_bar.empty()
                status_text.empty()
                
                st.markdown("---")
                
                # 결과 비교 표시
                display_strategy_comparison(all_results)
                
            except Exception as e:
                progress_bar.empty()
                status_text.empty()
                st.error(f"❌ 전략 테스트 중 오류 발생: {str(e)}")
                import traceback
                st.error(f"상세 오류: {traceback.format_exc()}")
    
    st.markdown("---")
    
    # 최적 윈도우 크기 및 임계값 탐색 섹션
    st.header("🎯 최적 윈도우 크기 및 임계값 탐색")
    
    st.markdown("""
    **목표:**
    - 윈도우 크기와 신뢰도 임계값의 최적 조합을 찾습니다
    - 여러 조합을 자동으로 테스트하여 최적값 추천
    - 연속 불일치/일치 5회 이상 발생이 가장 적은 조합 선택
    """)
    
    # 윈도우 크기 범위 설정
    st.markdown("### 윈도우 크기 범위")
    col_opt_window1, col_opt_window2 = st.columns(2)
    with col_opt_window1:
        opt_window_start = st.number_input(
            "윈도우 크기 시작",
            min_value=5,
            max_value=15,
            value=6,
            step=1,
            key="opt_threshold_window_start"
        )
    with col_opt_window2:
        opt_window_end = st.number_input(
            "윈도우 크기 끝",
            min_value=5,
            max_value=15,
            value=9,
            step=1,
            key="opt_threshold_window_end"
        )
    
    # 임계값 범위 설정
    st.markdown("### 신뢰도 임계값 범위")
    col_opt_threshold1, col_opt_threshold2, col_opt_threshold3 = st.columns(3)
    with col_opt_threshold1:
        opt_threshold_start = st.number_input(
            "임계값 시작 (%)",
            min_value=0,
            max_value=100,
            value=50,
            step=5,
            key="opt_threshold_start"
        )
    with col_opt_threshold2:
        opt_threshold_end = st.number_input(
            "임계값 끝 (%)",
            min_value=0,
            max_value=100,
            value=70,
            step=5,
            key="opt_threshold_end"
        )
    with col_opt_threshold3:
        opt_threshold_step = st.number_input(
            "임계값 간격 (%)",
            min_value=1,
            max_value=20,
            value=1,
            step=1,
            key="opt_threshold_step"
        )
    
    # 기본 예측 방법 선택
    opt_base_method = st.selectbox(
        "기본 예측 방법",
        options=["빈도 기반", "가중치 기반", "안전 우선"],
        index=0,
        key="opt_threshold_base_method"
    )
    
    # 최소 예측 빈도 조건 설정
    st.markdown("### 최소 예측 빈도 조건")
    min_prediction_ratio = st.number_input(
        "최소 예측 빈도 (%)",
        min_value=0,
        max_value=100,
        value=20,
        step=1,
        key="min_prediction_ratio",
        help="전체 스텝 대비 임계값 이상 예측 비율이 이 값 이상인 조합만 추천 대상으로 고려합니다. 라이브 게임에서 예측 기회가 적은 조합을 제외합니다."
    )
    
    # 유효성 검사
    if opt_window_start > opt_window_end:
        st.warning("⚠️ 윈도우 크기 시작값이 끝값보다 큽니다.")
        opt_window_sizes_list = []
    else:
        opt_window_sizes_list = list(range(opt_window_start, opt_window_end + 1))
    
    if opt_threshold_start > opt_threshold_end:
        st.warning("⚠️ 임계값 시작값이 끝값보다 큽니다.")
        opt_threshold_list = []
    else:
        opt_threshold_list = list(range(opt_threshold_start, opt_threshold_end + 1, opt_threshold_step))
    
    # 조합 수 표시
    if opt_window_sizes_list and opt_threshold_list:
        total_combinations = len(opt_window_sizes_list) * len(opt_threshold_list)
        st.info(f"테스트할 조합 수: {total_combinations}개 (윈도우 크기 {len(opt_window_sizes_list)}개 × 임계값 {len(opt_threshold_list)}개)")
    
    # 최적화 테스트 버튼
    col_opt_test1, col_opt_test2 = st.columns([3, 1])
    with col_opt_test1:
        st.markdown(f"**DB 전체 Grid String 수**: {len(df_strings)}개")
        if len(df_strings) == 0:
            st.warning("⚠️ DB에 grid_string이 없습니다. 먼저 데이터를 전처리하거나 파싱하여 저장하세요.")
    with col_opt_test2:
        st.markdown("<br>", unsafe_allow_html=True)
        start_opt_test_button = st.button("최적 조합 탐색", type="primary", use_container_width=True, key="start_opt_combination_test")
    
    # 최적 조합 탐색 실행
    if start_opt_test_button and opt_window_sizes_list and opt_threshold_list and len(df_strings) > 0:
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        try:
            total_combinations = len(opt_window_sizes_list) * len(opt_threshold_list)
            all_combination_results = []
            current_combination = 0
            
            for window_size in opt_window_sizes_list:
                for threshold in opt_threshold_list:
                    current_combination += 1
                    status_text.text(
                        f"윈도우 크기 {window_size}, 임계값 {threshold}% 테스트 중... "
                        f"({current_combination}/{total_combinations})"
                    )
                    progress_bar.progress(current_combination / total_combinations)
                    
                    # 임계값 전략 함수 생성
                    strategy_func = lambda m, p, method: predict_confidence_threshold(m, p, method, threshold=threshold)
                    strategy_name = f"임계값_{threshold}"
                    
                    # 해당 조합 테스트
                    strategy_results = test_strategy_on_all_data(
                        strategy_func,
                        strategy_name,
                        df_strings,
                        [window_size],
                        opt_base_method,
                        train_ratio
                    )
                    
                    if window_size in strategy_results:
                        result = strategy_results[window_size]
                        # 실패 지표 계산
                        total_failures = result.get('total_consecutive_5_count', 0) + result.get('total_consecutive_5_match_count', 0)
                        max_failures = max(
                            result.get('max_consecutive_mismatches', 0),
                            result.get('max_consecutive_matches', 0)
                        )
                        
                        # 신뢰도 통계 분석 (중복 계산 제거: test_strategy_on_all_data에서 이미 수집한 history 사용)
                        all_histories = result.get('all_histories', [])
                        if all_histories:
                            confidence_stats = analyze_confidence_statistics(all_histories, threshold)
                        else:
                            # history가 없는 경우 기본값 반환
                            confidence_stats = {
                                'total_steps': 0,
                                'total_predictions': 0,
                                'total_abstained': 0,
                                'prediction_ratio': 0,
                                'high_confidence_count': 0,
                                'high_confidence_ratio': 0,
                                'high_confidence_ratio_overall': 0,
                                'confidence_bins': {},
                                'avg_confidence': 0,
                                'min_confidence': 0,
                                'max_confidence': 0,
                                'avg_interval': 0,
                                'max_interval': 0,
                                'min_interval': 0,
                                'confidence_intervals': [],
                                'threshold': threshold
                            }
                        
                        all_combination_results.append({
                            'window_size': window_size,
                            'threshold': threshold,
                            'strategy_name': strategy_name,
                            'max_consecutive_mismatches': result.get('max_consecutive_mismatches', 0),
                            'max_consecutive_matches': result.get('max_consecutive_matches', 0),
                            'max_failures': max_failures,
                            'total_consecutive_5_count': result.get('total_consecutive_5_count', 0),
                            'total_consecutive_5_match_count': result.get('total_consecutive_5_match_count', 0),
                            'total_failures': total_failures,
                            'avg_accuracy': result.get('avg_accuracy', 0),
                            'tested_grid_strings': result.get('tested_grid_strings', 0),
                            'total_steps': result.get('total_steps', 0),
                            'confidence_stats': confidence_stats
                        })
            
            progress_bar.empty()
            status_text.empty()
            
            st.markdown("---")
            
            # 결과 비교 테이블
            if all_combination_results:
                st.markdown("### 📊 조합별 테스트 결과")
                
                comparison_data = []
                for result in all_combination_results:
                    conf_stats = result.get('confidence_stats', {})
                    high_conf_ratio_overall = conf_stats.get('high_confidence_ratio_overall', 0)
                    meets_min_requirement = high_conf_ratio_overall >= min_prediction_ratio
                    
                    comparison_data.append({
                        '윈도우 크기': result['window_size'],
                        '임계값 (%)': result['threshold'],
                        '최대 연속 불일치': result['max_consecutive_mismatches'],
                        '최대 연속 일치': result['max_consecutive_matches'],
                        '최대 실패 지표': result['max_failures'],
                        '연속 불일치 5회+': result['total_consecutive_5_count'],
                        '연속 일치 5회+': result['total_consecutive_5_match_count'],
                        '총 실패 횟수': result['total_failures'],
                        '평균 정확도 (%)': f"{result['avg_accuracy']:.2f}",
                        '예측 수행 비율 (%)': f"{conf_stats.get('prediction_ratio', 0):.2f}",
                        '임계값 이상 비율 (%)': f"{high_conf_ratio_overall:.2f}",
                        '필터 조건 만족': '✅' if meets_min_requirement else '❌',
                        '임계값 이상 예측 수': conf_stats.get('high_confidence_count', 0),
                        '평균 간격': f"{conf_stats.get('avg_interval', 0):.1f}",
                        '최대 간격': conf_stats.get('max_interval', 0),
                        '테스트된 Grid 수': result['tested_grid_strings']
                    })
                
                # 최대 실패 지표 기준으로 정렬 (오름차순)
                comparison_data.sort(key=lambda x: (x['최대 실패 지표'], x['총 실패 횟수']))
                
                comparison_df = pd.DataFrame(comparison_data)
                st.dataframe(comparison_df, use_container_width=True, hide_index=True)
                
                # 최적 조합 추천
                st.markdown("---")
                st.markdown("### 🎯 최적 조합 추천")
                
                # 필터링 조건 적용: 최소 예측 빈도 조건을 만족하는 조합만 추천 대상으로 고려
                filtered_results = [
                    r for r in all_combination_results 
                    if r.get('confidence_stats', {}).get('high_confidence_ratio_overall', 0) >= min_prediction_ratio
                ]
                
                # 점수 기반 선택
                def get_sort_key(x):
                    # 점수 기반 정렬
                    return calculate_optimal_score(x, min_prediction_ratio=min_prediction_ratio)
                
                if filtered_results:
                    best_combination = min(filtered_results, key=get_sort_key)
                    best_conf_stats = best_combination.get('confidence_stats', {})
                    best_score = calculate_optimal_score(best_combination, min_prediction_ratio=min_prediction_ratio)
                    st.info(f"💡 최소 예측 빈도 {min_prediction_ratio}% 조건을 만족하는 {len(filtered_results)}개 조합 중에서 추천합니다. (점수: {best_score:.2f})")
                else:
                    # 필터링된 결과가 없으면 경고와 함께 조건 완화
                    st.warning(f"⚠️ 최소 예측 빈도 {min_prediction_ratio}% 조건을 만족하는 조합이 없습니다.")
                    # 조건을 10%로 완화하여 재시도
                    filtered_results = [
                        r for r in all_combination_results 
                        if r.get('confidence_stats', {}).get('high_confidence_ratio_overall', 0) >= 10
                    ]
                    if filtered_results:
                        best_combination = min(filtered_results, key=get_sort_key)
                        best_conf_stats = best_combination.get('confidence_stats', {})
                        best_score = calculate_optimal_score(best_combination, min_prediction_ratio=10)
                        st.info(f"💡 최소 예측 빈도 10% 조건으로 완화하여 추천합니다. ({len(filtered_results)}개 조합 중 선택, 점수: {best_score:.2f})")
                    else:
                        # 그래도 없으면 점수 기반으로 선택하되 경고 표시
                        best_combination = min(all_combination_results, key=get_sort_key)
                        best_conf_stats = best_combination.get('confidence_stats', {})
                        best_score = calculate_optimal_score(best_combination, min_prediction_ratio=min_prediction_ratio)
                        st.error(f"❌ 최소 예측 빈도 조건을 만족하는 조합이 없어 점수 기반으로 추천합니다. 예측 기회가 매우 적을 수 있습니다. (점수: {best_score:.2f})")
                
                # 필터링 조건 만족 여부 확인
                high_conf_ratio_overall = best_conf_stats.get('high_confidence_ratio_overall', 0)
                meets_min_requirement = high_conf_ratio_overall >= min_prediction_ratio
                
                # 2차 필터링: 강제 예측 비율 체크 (경고만)
                forced_warning = ""
                if 'forced_prediction_ratio' in best_combination:
                    forced_ratio = best_combination['forced_prediction_ratio']
                    if forced_ratio > 50:
                        forced_warning = f" ⚠️ 강제 예측 비율 {forced_ratio:.1f}% (50% 초과)"
                
                if meets_min_requirement:
                    if high_conf_ratio_overall >= 30:
                        status_icon = "✅"
                        status_color = "success"
                    elif high_conf_ratio_overall >= 20:
                        status_icon = "⚠️"
                        status_color = "warning"
                    else:
                        status_icon = "❌"
                        status_color = "error"
                    st.success(f"{status_icon} **최적 조합: 윈도우 크기 {best_combination['window_size']}, 임계값 {best_combination['threshold']}%** (예측 빈도: {high_conf_ratio_overall:.2f}%){forced_warning}")
                else:
                    st.warning(f"⚠️ **최적 조합: 윈도우 크기 {best_combination['window_size']}, 임계값 {best_combination['threshold']}%** (최소 예측 빈도 {min_prediction_ratio}% 조건 미만: {high_conf_ratio_overall:.2f}%){forced_warning}")
                
                # 신뢰도 분포 경고: 실제 신뢰도 분포와 추천 임계값 비교
                recommended_threshold = best_combination['threshold']
                recommended_window = best_combination['window_size']
                
                # 실제 신뢰도 분포 확인 (DB에서 직접 조회)
                try:
                    conn = get_db_connection()
                    if conn:
                        try:
                            # 해당 윈도우 크기의 prefix별 신뢰도 계산
                            query = """
                                SELECT 
                                    prefix,
                                    suffix,
                                    COUNT(*) as suffix_count
                                FROM ngram_chunks
                                WHERE window_size = ?
                                GROUP BY prefix, suffix
                            """
                            
                            df_raw = pd.read_sql_query(query, conn, params=[recommended_window])
                            
                            if len(df_raw) > 0:
                                prefix_confidences = []
                                
                                for prefix in df_raw['prefix'].unique():
                                    prefix_data = df_raw[df_raw['prefix'] == prefix]
                                    b_count = prefix_data[prefix_data['suffix'] == 'b']['suffix_count'].sum() if 'b' in prefix_data['suffix'].values else 0
                                    p_count = prefix_data[prefix_data['suffix'] == 'p']['suffix_count'].sum() if 'p' in prefix_data['suffix'].values else 0
                                    total_count = prefix_data['suffix_count'].sum()
                                    
                                    if total_count > 0:
                                        b_ratio = (b_count / total_count * 100)
                                        p_ratio = (p_count / total_count * 100)
                                        confidence = max(b_ratio, p_ratio)
                                        prefix_confidences.append(confidence)
                                
                                if prefix_confidences:
                                    over_threshold_count = sum(1 for c in prefix_confidences if c >= recommended_threshold)
                                    over_threshold_ratio = (over_threshold_count / len(prefix_confidences) * 100) if prefix_confidences else 0
                                    
                                    if over_threshold_ratio < 20:
                                        st.error(f"❌ **중요 경고**: 추천된 임계값 {recommended_threshold}%에 해당하는 prefix가 전체의 {over_threshold_ratio:.1f}%({over_threshold_count}/{len(prefix_confidences)}개)에 불과합니다. 이 임계값을 사용하면 예측 기회가 매우 제한적일 수 있습니다. 아래 '📊 윈도우 크기별 Prefix 관측수 및 신뢰도 통계' 섹션에서 실제 신뢰도 분포를 확인하세요.")
                                    elif over_threshold_ratio < 30:
                                        st.warning(f"⚠️ **주의**: 추천된 임계값 {recommended_threshold}%에 해당하는 prefix가 전체의 {over_threshold_ratio:.1f}%({over_threshold_count}/{len(prefix_confidences)}개)입니다. 예측 빈도가 낮을 수 있습니다.")
                                    else:
                                        st.info(f"💡 추천된 임계값 {recommended_threshold}%에 해당하는 prefix가 전체의 {over_threshold_ratio:.1f}%({over_threshold_count}/{len(prefix_confidences)}개)입니다.")
                        finally:
                            conn.close()
                except Exception as e:
                    st.warning(f"⚠️ 실제 신뢰도 분포 확인 중 오류: {str(e)}")
                
                col_best1, col_best2 = st.columns(2)
                
                with col_best1:
                    st.markdown("**성능 지표:**")
                    st.info(f"""
                    - 최대 연속 불일치: {best_combination['max_consecutive_mismatches']}개
                    - 최대 연속 일치: {best_combination['max_consecutive_matches']}개
                    - 최대 실패 지표: {best_combination['max_failures']}개
                    - 연속 불일치 5회+: {best_combination['total_consecutive_5_count']}회
                    - 연속 일치 5회+: {best_combination['total_consecutive_5_match_count']}회
                    - 총 실패 횟수: {best_combination['total_failures']}회
                    - 평균 정확도: {best_combination['avg_accuracy']:.2f}%
                    """)
                
                with col_best2:
                    st.markdown("**신뢰도 통계:**")
                    # 예측 빈도 강조
                    if high_conf_ratio_overall >= 30:
                        prediction_status = "✅ 양호"
                    elif high_conf_ratio_overall >= 20:
                        prediction_status = "⚠️ 보통"
                    else:
                        prediction_status = "❌ 부족"
                    
                    # 강제 예측 비율 표시 (있는 경우)
                    forced_prediction_info = ""
                    if 'forced_prediction_ratio' in best_combination:
                        forced_ratio_display = best_combination['forced_prediction_ratio']
                        if forced_ratio_display > 50:
                            forced_prediction_info = f"\n- ⚠️ 강제 예측 비율: {forced_ratio_display:.2f}% (50% 초과)"
                        else:
                            forced_prediction_info = f"\n- 강제 예측 비율: {forced_ratio_display:.2f}%"
                    
                    # 점수 계산
                    best_score_display = calculate_optimal_score(best_combination, min_prediction_ratio=min_prediction_ratio)
                    
                    # 필터링 조건 만족 여부에 따라 다른 스타일로 표시
                    if meets_min_requirement:
                        st.info(f"""
                        - **예측 빈도**: {high_conf_ratio_overall:.2f}% ({prediction_status})
                        - 임계값({best_combination['threshold']}%) 이상 비율: {best_conf_stats.get('high_confidence_ratio', 0):.2f}%
                        - 전체 스텝 대비 임계값 이상 비율: {high_conf_ratio_overall:.2f}% ✅
                        - 임계값 이상 예측 수: {best_conf_stats.get('high_confidence_count', 0)}개
                        - 전체 예측 수: {best_conf_stats.get('total_predictions', 0)}개
                        - 평균 신뢰도: {best_conf_stats.get('avg_confidence', 0):.2f}%
                        - 평균 간격: {best_conf_stats.get('avg_interval', 0):.1f}스텝
                        - 최대 간격: {best_conf_stats.get('max_interval', 0)}스텝{forced_prediction_info}
                        - **점수**: {best_score_display:.2f}
                        """)
                    else:
                        st.warning(f"""
                        - **예측 빈도**: {high_conf_ratio_overall:.2f}% ({prediction_status})
                        - 임계값({best_combination['threshold']}%) 이상 비율: {best_conf_stats.get('high_confidence_ratio', 0):.2f}%
                        - 전체 스텝 대비 임계값 이상 비율: {high_conf_ratio_overall:.2f}% ⚠️ (최소 {min_prediction_ratio}% 미만)
                        - 임계값 이상 예측 수: {best_conf_stats.get('high_confidence_count', 0)}개
                        - 전체 예측 수: {best_conf_stats.get('total_predictions', 0)}개
                        - 평균 신뢰도: {best_conf_stats.get('avg_confidence', 0):.2f}%
                        - 평균 간격: {best_conf_stats.get('avg_interval', 0):.1f}스텝
                        - 최대 간격: {best_conf_stats.get('max_interval', 0)}스텝{forced_prediction_info}
                        - **점수**: {best_score_display:.2f}
                        """)
                
                # 신뢰도 구간별 분포 표시
                st.markdown("---")
                st.markdown("### 📊 신뢰도 구간별 분포")
                
                conf_bins = best_conf_stats.get('confidence_bins', {})
                if conf_bins:
                    bins_data = []
                    for bin_range, count in conf_bins.items():
                        total = best_conf_stats.get('total_predictions', 1)
                        ratio = (count / total * 100) if total > 0 else 0
                        bins_data.append({
                            '신뢰도 구간': bin_range + '%',
                            '예측 수': count,
                            '비율 (%)': f"{ratio:.2f}"
                        })
                    
                    bins_df = pd.DataFrame(bins_data)
                    st.dataframe(bins_df, use_container_width=True, hide_index=True)
                    
                    # 경고 메시지 (강화)
                    prediction_ratio = best_conf_stats.get('prediction_ratio', 0)
                    high_conf_ratio_overall = best_conf_stats.get('high_confidence_ratio_overall', 0)
                    
                    if prediction_ratio < 50:
                        st.warning(f"⚠️ **주의**: 예측 수행 비율이 {prediction_ratio:.2f}%로 낮습니다. 대부분의 스텝에서 예측을 하지 않습니다.")
                    
                    # 최소 예측 빈도 조건과 비교하여 경고 강화
                    if high_conf_ratio_overall < min_prediction_ratio:
                        st.error(f"❌ **경고**: 전체 스텝 대비 임계값({best_combination['threshold']}%) 이상 예측의 비율이 {high_conf_ratio_overall:.2f}%로 설정한 최소 예측 빈도({min_prediction_ratio}%)보다 낮습니다. 라이브 게임에서 예측 기회가 매우 적을 수 있습니다.")
                    elif high_conf_ratio_overall < 10:
                        st.warning(f"⚠️ **주의**: 전체 스텝 대비 임계값({best_combination['threshold']}%) 이상 예측의 비율이 {high_conf_ratio_overall:.2f}%로 매우 낮습니다. 실효성이 떨어질 수 있습니다.")
                    elif high_conf_ratio_overall < 20:
                        st.info(f"💡 전체 스텝 대비 임계값({best_combination['threshold']}%) 이상 예측의 비율이 {high_conf_ratio_overall:.2f}%입니다. 예측 간 간격이 길어질 수 있습니다.")
                    
                    max_interval = best_conf_stats.get('max_interval', 0)
                    avg_interval = best_conf_stats.get('avg_interval', 0)
                    
                    # 간격 설명 추가
                    st.markdown("#### 📏 간격(Interval) 설명")
                    st.info(f"""
                    **간격의 의미**: 임계값({best_combination['threshold']}%) 이상 예측 사이의 스텝 수입니다.
                    
                    - **최대 간격 {max_interval}스텝**: 가장 긴 대기 시간
                      → 예: Step 1에서 예측 후, Step {max_interval + 1}에서 다음 예측
                    - **평균 간격 {avg_interval:.1f}스텝**: 평균 대기 시간
                    
                    **주의**: 
                    - 최대 간격이 1이면 → 임계값 이상 예측이 연속된 스텝에 나타남 (매우 좋음)
                    - 최대 간격이 10이면 → 첫 예측 후 최대 10스텝 대기 후 다음 예측 가능
                    - 중간에 임계값 미만 예측이 있어도 간격에 포함됩니다.
                    """)
                    
                    if max_interval > 10:
                        st.warning(f"⚠️ **주의**: 임계값({best_combination['threshold']}%) 이상 예측 간 최대 간격이 {max_interval}스텝입니다. 첫 번째 실패 후 다음 예측까지 긴 대기 시간이 발생할 수 있습니다.")
                    elif max_interval > 5:
                        st.info(f"💡 **정보**: 임계값({best_combination['threshold']}%) 이상 예측 간 최대 간격이 {max_interval}스텝입니다. 대기 시간이 다소 길 수 있습니다.")
                    elif max_interval <= 1:
                        st.success(f"✅ **좋음**: 임계값({best_combination['threshold']}%) 이상 예측 간 최대 간격이 {max_interval}스텝입니다. 거의 매 스텝마다 예측이 가능합니다.")
                
                # 상위 5개 조합 표시
                st.markdown("---")
                st.markdown("### 📈 상위 5개 조합")
                
                top_5 = sorted(all_combination_results, key=lambda x: (x['max_failures'], x['total_failures']))[:5]
                
                top_5_data = []
                for idx, result in enumerate(top_5, 1):
                    top_5_data.append({
                        '순위': idx,
                        '윈도우 크기': result['window_size'],
                        '임계값 (%)': result['threshold'],
                        '최대 실패 지표': result['max_failures'],
                        '총 실패 횟수': result['total_failures'],
                        '평균 정확도 (%)': f"{result['avg_accuracy']:.2f}"
                    })
                
                top_5_df = pd.DataFrame(top_5_data)
                st.dataframe(top_5_df, use_container_width=True, hide_index=True)
                
                # 히트맵 시각화 (선택사항)
                st.markdown("---")
                st.markdown("### 🔥 실패 지표 히트맵")
                
                # 윈도우 크기 × 임계값 매트릭스 생성
                heatmap_data = {}
                for result in all_combination_results:
                    window = result['window_size']
                    threshold = result['threshold']
                    if window not in heatmap_data:
                        heatmap_data[window] = {}
                    heatmap_data[window][threshold] = result['max_failures']
                
                # DataFrame으로 변환
                heatmap_df = pd.DataFrame(heatmap_data).T
                heatmap_df = heatmap_df.sort_index()
                heatmap_df = heatmap_df.sort_index(axis=1)
                
                st.dataframe(heatmap_df, use_container_width=True)
                st.caption("값이 낮을수록 좋습니다 (최대 실패 지표)")
            else:
                st.warning("⚠️ 테스트 결과가 없습니다.")
                
        except Exception as e:
            progress_bar.empty()
            status_text.empty()
            st.error(f"❌ 최적 조합 탐색 중 오류 발생: {str(e)}")
            import traceback
            st.error(f"상세 오류: {traceback.format_exc()}")
    
    # 예측값 테이블 저장/업데이트 섹션
    st.markdown("---")
    st.header("💾 예측값 테이블 저장/업데이트")
    st.markdown("이전 데이터로 예측값을 계산하여 DB 테이블에 저장합니다. 라이브 게임 전에 실행하세요.")
    
    col_pred1, col_pred2 = st.columns([2, 1])
    
    with col_pred1:
        # 기준 grid_string_id 선택
        df_all_strings = load_preprocessed_data()
        if len(df_all_strings) > 0:
            # grid_string_id와 정보를 함께 표시
            grid_string_options = []
            for _, row in df_all_strings.iterrows():
                display_text = f"ID {row['id']} - 길이 {row['string_length']} - {row['created_at']}"
                grid_string_options.append((row['id'], display_text))
            
            # 최신 것부터 표시
            grid_string_options.sort(key=lambda x: x[0], reverse=True)
            
            selected_cutoff_id = st.selectbox(
                "기준 Grid String ID (이 ID 이하가 이전 데이터)",
                options=[None] + [opt[0] for opt in grid_string_options],
                format_func=lambda x: "전체 데이터" if x is None else f"ID {x} 이하",
                key="pred_cutoff_id"
            )
            
            if selected_cutoff_id is not None:
                selected_info = df_all_strings[df_all_strings['id'] == selected_cutoff_id].iloc[0]
                st.info(f"선택된 기준: ID {selected_cutoff_id} (길이: {selected_info['string_length']}, 생성일: {selected_info['created_at']})")
        else:
            selected_cutoff_id = None
            st.warning("⚠️ 저장된 grid_string이 없습니다.")
    
    with col_pred2:
        st.markdown("<br>", unsafe_allow_html=True)
        generate_predictions_button = st.button("예측값 저장/업데이트", type="primary", use_container_width=True, key="generate_predictions")
    
    # 윈도우 크기, 방법, 임계값 선택
    col_pred3, col_pred4, col_pred5 = st.columns(3)
    
    with col_pred3:
        pred_window_sizes = st.multiselect(
            "윈도우 크기",
            options=[5, 6, 7, 8, 9],
            default=[6, 7, 8, 9],
            key="pred_window_sizes"
        )
    
    with col_pred4:
        pred_methods = st.multiselect(
            "예측 방법",
            options=["빈도 기반", "가중치 기반", "안전 우선"],
            default=["빈도 기반"],
            key="pred_methods"
        )
    
    with col_pred5:
        pred_thresholds = st.multiselect(
            "임계값 (%)",
            options=[0, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100],
            default=[0, 50, 60, 70, 80, 90, 100],
            key="pred_thresholds",
            help="0은 임계값 없이 모든 예측 포함"
        )
    
    # 예측값 저장/업데이트 실행
    if generate_predictions_button and pred_window_sizes and pred_methods and pred_thresholds and len(df_all_strings) > 0:
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        try:
            status_text.text("예측값 계산 및 저장 중...")
            progress_bar.progress(0.1)
            
            result = save_or_update_predictions_for_historical_data(
                cutoff_grid_string_id=selected_cutoff_id,
                window_sizes=pred_window_sizes,
                methods=pred_methods,
                thresholds=pred_thresholds,
                batch_size=1000
            )
            
            progress_bar.progress(1.0)
            status_text.empty()
            progress_bar.empty()
            
            if result:
                st.success(f"✅ 예측값 저장/업데이트 완료!")
                col_res1, col_res2, col_res3, col_res4 = st.columns(4)
                with col_res1:
                    st.metric("총 저장/업데이트", f"{result['total_saved']:,}개")
                with col_res2:
                    st.metric("새 레코드", f"{result['new_records']:,}개")
                with col_res3:
                    st.metric("업데이트", f"{result['updated_records']:,}개")
                with col_res4:
                    st.metric("고유 Prefix 수", f"{result['unique_prefixes']:,}개")
            else:
                st.error("❌ 예측값 저장/업데이트 실패")
                
        except Exception as e:
            progress_bar.empty()
            status_text.empty()
            st.error(f"❌ 예측값 저장/업데이트 중 오류 발생: {str(e)}")
            import traceback
            st.error(f"상세 오류: {traceback.format_exc()}")
    
    # 새 데이터 최적 분석 섹션
    st.markdown("---")
    st.header("🎯 새 데이터 최적 분석")
    st.markdown("저장된 예측값을 사용하여 새로운 데이터만으로 최적 조합을 찾습니다.")
    
    col_opt_new1, col_opt_new2 = st.columns([2, 1])
    
    with col_opt_new1:
        # 기준 grid_string_id 선택 (세션 상태로 유지)
        if 'opt_cutoff_id_new' not in st.session_state:
            st.session_state.opt_cutoff_id_new = None
        
        if len(df_all_strings) > 0:
            grid_string_options_new = []
            for _, row in df_all_strings.iterrows():
                display_text = f"ID {row['id']} - 길이 {row['string_length']} - {row['created_at']}"
                grid_string_options_new.append((row['id'], display_text))
            
            grid_string_options_new.sort(key=lambda x: x[0], reverse=True)
            
            # 세션 상태에 저장된 값이 있으면 유지, 없으면 첫 번째 값 사용
            default_index = 0
            if st.session_state.opt_cutoff_id_new is not None:
                # 저장된 값이 옵션에 있는지 확인
                try:
                    default_index = [opt[0] for opt in grid_string_options_new].index(st.session_state.opt_cutoff_id_new)
                except ValueError:
                    default_index = 0
            
            selected_cutoff_id_new = st.selectbox(
                "기준 Grid String ID (이 ID 이후가 새로운 데이터)",
                options=[opt[0] for opt in grid_string_options_new],
                format_func=lambda x: f"ID {x} 이후",
                index=default_index,
                key="opt_cutoff_id_new_selectbox",
                help="이 ID 이후의 데이터만 테스트합니다. 이 ID 이하의 데이터는 학습 데이터로 사용됩니다."
            )
            
            # 세션 상태 업데이트
            st.session_state.opt_cutoff_id_new = selected_cutoff_id_new
            
            if selected_cutoff_id_new is not None:
                selected_info_new = df_all_strings[df_all_strings['id'] == selected_cutoff_id_new].iloc[0]
                new_data_count = len(df_all_strings[df_all_strings['id'] > selected_cutoff_id_new])
                st.info(f"선택된 기준: ID {selected_cutoff_id_new} (길이: {selected_info_new['string_length']}, 생성일: {selected_info_new['created_at']}) | 새로운 데이터: {new_data_count}개")
        else:
            selected_cutoff_id_new = None
            st.warning("⚠️ 저장된 grid_string이 없습니다.")
    
    with col_opt_new2:
        st.markdown("<br>", unsafe_allow_html=True)
        start_new_analysis_button = st.button("최적 조합 찾기", type="primary", use_container_width=True, key="start_new_analysis")
    
    # 윈도우 크기 및 임계값 범위 설정
    col_opt_new3, col_opt_new4, col_opt_new5 = st.columns(3)
    
    with col_opt_new3:
        new_window_sizes = st.multiselect(
            "윈도우 크기",
            options=[5, 6, 7, 8, 9],
            default=[6, 7, 8],
            key="new_window_sizes"
        )
    
    with col_opt_new4:
        new_threshold_start = st.number_input(
            "임계값 시작 (%)",
            min_value=0,
            max_value=100,
            value=50,
            step=1,
            key="new_threshold_start"
        )
        new_threshold_end = st.number_input(
            "임계값 끝 (%)",
            min_value=0,
            max_value=100,
            value=60,
            step=1,
            key="new_threshold_end"
        )
        new_threshold_step = st.number_input(
            "임계값 간격 (%)",
            min_value=1,
            max_value=20,
            value=1,
            step=1,
            key="new_threshold_step"
        )
    
    with col_opt_new5:
        new_method = st.selectbox(
            "예측 방법",
            options=["빈도 기반", "가중치 기반", "안전 우선"],
            index=0,
            key="new_method"
        )
        use_stored = st.checkbox(
            "저장된 예측값 사용",
            value=True,
            key="use_stored_predictions",
            help="체크하면 DB 테이블에서 조회, 해제하면 실시간 계산"
        )
    
    # 최적 분석 실행
    # selected_cutoff_id_new은 세션 상태에서 가져오기
    if 'opt_cutoff_id_new' in st.session_state:
        selected_cutoff_id_new = st.session_state.opt_cutoff_id_new
    else:
        selected_cutoff_id_new = None
    
    if start_new_analysis_button and selected_cutoff_id_new and new_window_sizes:
        if new_threshold_start > new_threshold_end:
            st.warning("⚠️ 임계값 시작값이 끝값보다 큽니다.")
        else:
            new_threshold_list = list(range(new_threshold_start, new_threshold_end + 1, new_threshold_step))
            
            if not new_threshold_list:
                st.warning("⚠️ 유효한 임계값 범위가 없습니다.")
            else:
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                try:
                    status_text.text("최적 조합 분석 중...")
                    progress_bar.progress(0.1)
                    
                    all_combination_results = find_optimal_combination_for_new_data(
                        cutoff_grid_string_id=selected_cutoff_id_new,
                        window_sizes=new_window_sizes,
                        thresholds=new_threshold_list,
                        method=new_method,
                        use_stored_predictions=use_stored
                    )
                    
                    progress_bar.progress(1.0)
                    status_text.empty()
                    progress_bar.empty()
                    
                    if all_combination_results:
                        # 결과 표시 (기존 최적 탐색 섹션과 동일한 형식)
                        st.markdown("### 📊 조합별 비교")
                        
                        # 전체 통계 표시
                        first_result = all_combination_results[0]
                        total_count = len(df_all_strings[df_all_strings['id'] > selected_cutoff_id_new])
                        total_count_valid = len(df_all_strings[
                            (df_all_strings['id'] > selected_cutoff_id_new) & 
                            (df_all_strings['string_length'] >= min(new_window_sizes))
                        ])
                        
                        st.info(f"""
                        **전체 통계:**
                        - 전체 Grid String: {total_count_valid}개 (윈도우 크기 조건 충족)
                        - 유효한 테스트 케이스: {first_result.get('valid_test_count', 0)}개 ({first_result.get('valid_ratio', 0):.1f}%)
                        - 스킵된 케이스: {first_result.get('skipped_count', 0)}개 ({first_result.get('skipped_ratio', 0):.1f}%)
                          - 불일치 상태로 종료: {first_result.get('ending_mismatch_count', 0)}개
                        - 잘린 케이스: {first_result.get('truncated_count', 0)}개
                        - 잘린 스텝 수: {first_result.get('total_truncated_steps', 0)}개
                        """)
                        
                        comparison_data = []
                        for result in all_combination_results:
                            conf_stats = result.get('confidence_stats', {})
                            comparison_data.append({
                                '윈도우 크기': result['window_size'],
                                '임계값 (%)': result['threshold'],
                                '최대 실패 지표': result['max_failures'],
                                '총 실패 횟수': result['total_failures'],
                                '평균 정확도 (%)': f"{result['avg_accuracy']:.2f}",
                                '예측 수행 비율 (%)': f"{conf_stats.get('prediction_ratio', 0):.2f}",
                                '전체 스텝 대비 임계값 이상 비율 (%)': f"{conf_stats.get('high_confidence_ratio_overall', 0):.2f}",
                                '평균 간격': f"{conf_stats.get('avg_interval', 0):.1f}",
                                '최대 간격': conf_stats.get('max_interval', 0),
                                '유효 테스트 수': result.get('valid_test_count', 0),
                                '스킵 수': result.get('skipped_count', 0)
                            })
                        
                        comparison_data.sort(key=lambda x: (x['최대 실패 지표'], x['총 실패 횟수']))
                        comparison_df = pd.DataFrame(comparison_data)
                        st.dataframe(comparison_df, use_container_width=True, hide_index=True)
                        
                        # 최적 조합 추천
                        st.markdown("### 🎯 최적 조합 추천")
                        
                        # 1차 필터링: 최소 예측 빈도 조건 (필수, 기본값 20%)
                        min_prediction_ratio_new = 20
                        filtered_results_new = [
                            r for r in all_combination_results 
                            if r.get('confidence_stats', {}).get('high_confidence_ratio_overall', 0) >= min_prediction_ratio_new
                        ]
                        
                        # 점수 기반 선택
                        if filtered_results_new:
                            best_combination = min(filtered_results_new, key=lambda x: calculate_optimal_score(x, min_prediction_ratio=min_prediction_ratio_new))
                            st.info(f"💡 최소 예측 빈도 {min_prediction_ratio_new}% 조건을 만족하는 {len(filtered_results_new)}개 조합 중에서 추천합니다.")
                        else:
                            # 필터링된 결과가 없으면 조건 완화하여 재시도
                            filtered_results_new = [
                                r for r in all_combination_results 
                                if r.get('confidence_stats', {}).get('high_confidence_ratio_overall', 0) >= 10
                            ]
                            if filtered_results_new:
                                best_combination = min(filtered_results_new, key=lambda x: calculate_optimal_score(x, min_prediction_ratio=10))
                                st.warning(f"⚠️ 최소 예측 빈도 {min_prediction_ratio_new}% 조건을 만족하는 조합이 없어 10% 조건으로 완화하여 추천합니다.")
                            else:
                                # 그래도 없으면 점수 기반으로 선택하되 경고 표시
                                best_combination = min(all_combination_results, key=lambda x: calculate_optimal_score(x, min_prediction_ratio=min_prediction_ratio_new))
                                st.error(f"❌ 최소 예측 빈도 조건을 만족하는 조합이 없어 점수 기반으로 추천합니다. 예측 기회가 매우 적을 수 있습니다.")
                        
                        best_conf_stats = best_combination.get('confidence_stats', {})
                        high_conf_ratio_overall_new = best_conf_stats.get('high_confidence_ratio_overall', 0)
                        best_score_new = calculate_optimal_score(best_combination, min_prediction_ratio=min_prediction_ratio_new)
                        
                        # 예측 빈도에 따른 상태 표시
                        if high_conf_ratio_overall_new >= 30:
                            status_icon = "✅"
                            status_message = f"✅ **최적 조합: 윈도우 크기 {best_combination['window_size']}, 임계값 {best_combination['threshold']}%** (예측 빈도: {high_conf_ratio_overall_new:.2f}%, 점수: {best_score_new:.2f})"
                            st.success(status_message)
                        elif high_conf_ratio_overall_new >= 20:
                            status_message = f"⚠️ **최적 조합: 윈도우 크기 {best_combination['window_size']}, 임계값 {best_combination['threshold']}%** (예측 빈도: {high_conf_ratio_overall_new:.2f}%, 점수: {best_score_new:.2f})"
                            st.warning(status_message)
                        else:
                            status_message = f"❌ **최적 조합: 윈도우 크기 {best_combination['window_size']}, 임계값 {best_combination['threshold']}%** (예측 빈도: {high_conf_ratio_overall_new:.2f}%, 점수: {best_score_new:.2f})"
                            st.error(status_message)
                        
                        col_best_new1, col_best_new2 = st.columns(2)
                        
                        with col_best_new1:
                            st.markdown("**성능 지표:**")
                            st.info(f"""
                            - 최대 연속 불일치: {best_combination['max_consecutive_mismatches']}개
                            - 최대 연속 일치: {best_combination['max_consecutive_matches']}개
                            - 최대 실패 지표: {best_combination['max_failures']}개
                            - 연속 불일치 5회+: {best_combination['total_consecutive_5_count']}회
                            - 연속 일치 5회+: {best_combination['total_consecutive_5_match_count']}회
                            - 총 실패 횟수: {best_combination['total_failures']}회
                            - 평균 정확도: {best_combination['avg_accuracy']:.2f}%
                            """)
                        
                        with col_best_new2:
                            st.markdown("**신뢰도 통계:**")
                            high_conf_ratio_overall_display = best_conf_stats.get('high_confidence_ratio_overall', 0)
                            
                            # 예측 빈도 강조
                            if high_conf_ratio_overall_display >= 30:
                                prediction_status = "✅ 양호"
                            elif high_conf_ratio_overall_display >= 20:
                                prediction_status = "⚠️ 보통"
                            else:
                                prediction_status = "❌ 부족"
                            
                            # 강제 예측 비율 표시 (있는 경우)
                            forced_prediction_info = ""
                            if 'forced_prediction_ratio' in best_combination:
                                forced_ratio_display = best_combination['forced_prediction_ratio']
                                if forced_ratio_display > 50:
                                    forced_prediction_info = f"\n- ⚠️ 강제 예측 비율: {forced_ratio_display:.2f}% (50% 초과)"
                                else:
                                    forced_prediction_info = f"\n- 강제 예측 비율: {forced_ratio_display:.2f}%"
                            
                            st.info(f"""
                            - **예측 빈도**: {high_conf_ratio_overall_display:.2f}% ({prediction_status})
                            - 임계값({best_combination['threshold']}%) 이상 비율: {best_conf_stats.get('high_confidence_ratio', 0):.2f}%
                            - 전체 스텝 대비 임계값 이상 비율: {high_conf_ratio_overall_display:.2f}%
                            - 임계값 이상 예측 수: {best_conf_stats.get('high_confidence_count', 0)}개
                            - 전체 예측 수: {best_conf_stats.get('total_predictions', 0)}개
                            - 평균 신뢰도: {best_conf_stats.get('avg_confidence', 0):.2f}%
                            - 평균 간격: {best_conf_stats.get('avg_interval', 0):.1f}스텝
                            - 최대 간격: {best_conf_stats.get('max_interval', 0)}스텝{forced_prediction_info}
                            - **점수**: {best_score_new:.2f}
                            """)
                    else:
                        st.warning("⚠️ 분석 결과가 없습니다.")
                        
                except Exception as e:
                    progress_bar.empty()
                    status_text.empty()
                    st.error(f"❌ 최적 분석 중 오류 발생: {str(e)}")
                    import traceback
                    st.error(f"상세 오류: {traceback.format_exc()}")
    
    # 윈도우 크기별 prefix 관측수 및 신뢰도 통계 표시
    st.markdown("---")
    # 예측 기회 보장 시스템 (강제 예측) 섹션
    st.markdown("---")
    st.header("🛡️ 예측 기회 보장 시스템 (강제 예측)")
    
    st.markdown("""
    **목표:**
    - 예측 기회를 보장하면서 연속 실패를 피하는 최적 조합을 찾습니다
    - 최대 간격 제약을 설정하여 예측값이 없는 상태를 최소화합니다
    - N 스텝 동안 예측이 없으면 임계값을 무시하고 강제 예측합니다
    """)
    
    col_fallback1, col_fallback2 = st.columns([2, 1])
    
    with col_fallback1:
        # 기준 grid_string_id 선택
        if 'fallback_cutoff_id' not in st.session_state:
            st.session_state.fallback_cutoff_id = None
        
        if len(df_strings) > 0:
            grid_string_options_fallback = []
            for _, row in df_strings.iterrows():
                display_text = f"ID {row['id']} - 길이 {len(row['grid_string'])} - {row['created_at']}"
                grid_string_options_fallback.append((row['id'], display_text))
            
            grid_string_options_fallback.sort(key=lambda x: x[0], reverse=True)
            
            default_index_fallback = 0
            if st.session_state.fallback_cutoff_id is not None:
                try:
                    default_index_fallback = [opt[0] for opt in grid_string_options_fallback].index(st.session_state.fallback_cutoff_id)
                except ValueError:
                    default_index_fallback = 0
            
            selected_cutoff_id_fallback = st.selectbox(
                "기준 Grid String ID (이 ID 이후가 새로운 데이터)",
                options=[opt[0] for opt in grid_string_options_fallback],
                format_func=lambda x: f"ID {x} 이후",
                index=default_index_fallback,
                key="fallback_cutoff_id_selectbox"
            )
            
            st.session_state.fallback_cutoff_id = selected_cutoff_id_fallback
            
            if selected_cutoff_id_fallback is not None:
                selected_info_fallback = df_strings[df_strings['id'] == selected_cutoff_id_fallback].iloc[0]
                new_data_count_fallback = len(df_strings[df_strings['id'] > selected_cutoff_id_fallback])
                st.info(f"선택된 기준: ID {selected_cutoff_id_fallback} | 새로운 데이터: {new_data_count_fallback}개")
        else:
            selected_cutoff_id_fallback = None
            st.warning("⚠️ 저장된 grid_string이 없습니다.")
    
    with col_fallback2:
        st.markdown("<br>", unsafe_allow_html=True)
        start_fallback_analysis_button = st.button("최적 조합 찾기", type="primary", use_container_width=True, key="start_fallback_analysis")
    
    # 설정
    col_fallback3, col_fallback4, col_fallback5 = st.columns(3)
    
    with col_fallback3:
        fallback_window_sizes = st.multiselect(
            "윈도우 크기",
            options=[5, 6, 7, 8, 9],
            default=[6, 7, 8],
            key="fallback_window_sizes"
        )
    
    with col_fallback4:
        fallback_threshold = st.number_input(
            "임계값 (%)",
            min_value=0,
            max_value=100,
            value=60,
            step=1,
            key="fallback_threshold",
            help="신뢰도가 이 값 이상일 때만 예측"
        )
        fallback_max_intervals = st.multiselect(
            "최대 예측 없음 간격 (스텝)",
            options=[5, 6, 7, 8, 9, 10],
            default=[5, 6, 7],
            key="fallback_max_intervals",
            help="이 간격을 넘기면 임계값 무시하고 강제 예측"
        )
    
    with col_fallback5:
        fallback_method = st.selectbox(
            "예측 방법",
            options=["빈도 기반", "가중치 기반", "안전 우선"],
            index=0,
            key="fallback_method"
        )
    
    # 최적 조합 탐색 실행
    if start_fallback_analysis_button and selected_cutoff_id_fallback and fallback_window_sizes and fallback_max_intervals:
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        try:
            status_text.text("예측 기회 보장 시스템 분석 중...")
            progress_bar.progress(0.1)
            
            all_combination_results = find_optimal_combination_for_new_data(
                cutoff_grid_string_id=selected_cutoff_id_fallback,
                window_sizes=fallback_window_sizes,
                thresholds=[fallback_threshold],
                method=fallback_method,
                use_stored_predictions=True,
                max_intervals=fallback_max_intervals
            )
            
            progress_bar.progress(1.0)
            status_text.empty()
            progress_bar.empty()
            
            if all_combination_results:
                st.markdown("### 📊 조합별 비교")
                
                comparison_data = []
                for result in all_combination_results:
                    conf_stats = result.get('confidence_stats', {})
                    comparison_data.append({
                        '윈도우 크기': result['window_size'],
                        '임계값 (%)': result['threshold'],
                        '최대 간격': result.get('max_interval', 'N/A'),
                        '최대 실패 지표': result['max_failures'],
                        '총 실패 횟수': result['total_failures'],
                        '평균 정확도 (%)': f"{result['avg_accuracy']:.2f}",
                        '강제 예측 비율 (%)': f"{result.get('forced_prediction_ratio', 0):.2f}",
                        '평균 간격': f"{result.get('avg_interval', 0):.1f}",
                        '예측 수행 비율 (%)': f"{conf_stats.get('prediction_ratio', 0):.2f}",
                        '유효 테스트 수': result.get('valid_test_count', 0)
                    })
                
                comparison_data.sort(key=lambda x: (x['최대 실패 지표'], x['총 실패 횟수']))
                comparison_df = pd.DataFrame(comparison_data)
                st.dataframe(comparison_df, use_container_width=True, hide_index=True)
                
                # 최적 조합 추천
                st.markdown("### 🎯 최적 조합 추천")
                best_combination = min(all_combination_results, key=lambda x: (x['max_failures'], x['total_failures']))
                best_conf_stats = best_combination.get('confidence_stats', {})
                
                st.success(f"✅ **최적 조합: 윈도우 크기 {best_combination['window_size']}, 임계값 {best_combination['threshold']}%, 최대 간격 {best_combination.get('max_interval', 'N/A')}**")
                
                col_best_fallback1, col_best_fallback2 = st.columns(2)
                
                with col_best_fallback1:
                    st.markdown("**성능 지표:**")
                    st.info(f"""
                    - 최대 연속 불일치: {best_combination['max_consecutive_mismatches']}개
                    - 최대 연속 일치: {best_combination['max_consecutive_matches']}개
                    - 최대 실패 지표: {best_combination['max_failures']}개
                    - 연속 불일치 5회+: {best_combination['total_consecutive_5_count']}회
                    - 연속 일치 5회+: {best_combination['total_consecutive_5_match_count']}회
                    - 총 실패 횟수: {best_combination['total_failures']}회
                    - 평균 정확도: {best_combination['avg_accuracy']:.2f}%
                    """)
                
                with col_best_fallback2:
                    st.markdown("**예측 기회 통계:**")
                    forced_ratio = best_combination.get('forced_prediction_ratio', 0)
                    avg_int = best_combination.get('avg_interval', 0)
                    total_pred = best_combination.get('total_predictions', 0)
                    forced_pred = best_combination.get('forced_predictions', 0)
                    
                    st.info(f"""
                    - 전체 예측 수: {total_pred}개
                    - 강제 예측 수: {forced_pred}개
                    - 강제 예측 비율: {forced_ratio:.2f}%
                    - 평균 간격: {avg_int:.1f} 스텝
                    - 예측 수행 비율: {best_conf_stats.get('prediction_ratio', 0):.2f}%
                    - 전체 스텝 대비 임계값 이상 비율: {best_conf_stats.get('high_confidence_ratio_overall', 0):.2f}%
                    """)
            else:
                st.warning("⚠️ 결과가 없습니다.")
                
        except Exception as e:
            progress_bar.empty()
            status_text.empty()
            st.error(f"❌ 분석 중 오류 발생: {str(e)}")
            import traceback
            st.error(f"상세 오류: {traceback.format_exc()}")
    
    st.markdown("---")
    
    st.header("📊 윈도우 크기별 Prefix 관측수 및 신뢰도 통계")
    st.markdown("DB에서 직접 집계한 윈도우 크기별 prefix별 관측수와 신뢰도입니다.")
    
    try:
        # 윈도우 크기별 prefix 관측수 및 신뢰도 집계
        window_sizes_to_analyze = [5, 6, 7]
        
        conn = get_db_connection()
        if conn is None:
            st.error("❌ 데이터베이스 연결 실패")
        else:
            try:
                all_results = []
                
                for window_size in window_sizes_to_analyze:
                    # 해당 윈도우 크기의 prefix별 관측수 및 suffix별 관측수 집계
                    query = """
                        SELECT 
                            prefix,
                            suffix,
                            COUNT(*) as suffix_count
                        FROM ngram_chunks
                        WHERE window_size = ?
                        GROUP BY prefix, suffix
                        ORDER BY prefix, suffix
                    """
                    
                    df_raw = pd.read_sql_query(query, conn, params=[window_size])
                    
                    if len(df_raw) > 0:
                        # prefix별로 집계하여 비율 및 신뢰도 계산
                        prefix_stats = []
                        
                        for prefix in df_raw['prefix'].unique():
                            prefix_data = df_raw[df_raw['prefix'] == prefix]
                            
                            # suffix별 관측수
                            b_count = prefix_data[prefix_data['suffix'] == 'b']['suffix_count'].sum() if 'b' in prefix_data['suffix'].values else 0
                            p_count = prefix_data[prefix_data['suffix'] == 'p']['suffix_count'].sum() if 'p' in prefix_data['suffix'].values else 0
                            total_count = prefix_data['suffix_count'].sum()
                            
                            # 비율 계산
                            b_ratio = (b_count / total_count * 100) if total_count > 0 else 0
                            p_ratio = (p_count / total_count * 100) if total_count > 0 else 0
                            
                            # 신뢰도 = max(비율들)
                            confidence = max(b_ratio, p_ratio)
                            
                            # 예측값 (더 높은 비율의 suffix)
                            predicted = 'b' if b_ratio > p_ratio else ('p' if p_ratio > b_ratio else None)
                            
                            # 가능한 suffix 목록
                            possible_suffixes = ', '.join(prefix_data['suffix'].unique())
                            
                            prefix_stats.append({
                                '윈도우 크기': window_size,
                                'Prefix': prefix,
                                '총 관측수': total_count,
                                "b 관측수": b_count,
                                "p 관측수": p_count,
                                "b 비율 (%)": f"{b_ratio:.2f}",
                                "p 비율 (%)": f"{p_ratio:.2f}",
                                '신뢰도 (%)': f"{confidence:.2f}",
                                '예측값': predicted if predicted else '-',
                                '가능한 Suffix': possible_suffixes
                            })
                        
                        if prefix_stats:
                            df_prefix_stats = pd.DataFrame(prefix_stats)
                            # 관측수 내림차순 정렬
                            df_prefix_stats = df_prefix_stats.sort_values('총 관측수', ascending=False)
                            all_results.append(df_prefix_stats)
                
                if all_results:
                    # 모든 결과 합치기
                    combined_df = pd.concat(all_results, ignore_index=True)
                    
                    # 테이블로 표시
                    st.dataframe(combined_df, use_container_width=True, hide_index=True)
                    
                    # 요약 통계
                    st.markdown("### 📈 요약 통계")
                    summary_data = []
                    all_confidence_distributions = {}  # 윈도우 크기별 1% 단위 분포 저장
                    
                    for window_size in window_sizes_to_analyze:
                        window_df = combined_df[combined_df['윈도우 크기'] == window_size]
                        if len(window_df) > 0:
                            # 신뢰도 통계 계산
                            confidences = pd.to_numeric(window_df['신뢰도 (%)'], errors='coerce')
                            
                            # 1% 단위 신뢰도 구간별 통계 (0-100%)
                            confidence_bins_1pct = {}
                            for i in range(0, 100):
                                confidence_bins_1pct[f"{i}-{i+1}"] = 0
                            
                            # 100%는 별도 처리
                            confidence_bins_1pct['100'] = 0
                            
                            for conf in confidences:
                                if pd.notna(conf):
                                    conf_int = int(conf)
                                    if conf >= 100:
                                        confidence_bins_1pct['100'] += 1
                                    elif conf_int < 100:
                                        bin_key = f"{conf_int}-{conf_int+1}"
                                        if bin_key in confidence_bins_1pct:
                                            confidence_bins_1pct[bin_key] += 1
                            
                            # 0이 아닌 구간만 저장
                            filtered_bins = {k: v for k, v in confidence_bins_1pct.items() if v > 0}
                            all_confidence_distributions[window_size] = filtered_bins
                            
                            total_prefixes = len(window_df)
                            
                            # 기본 통계
                            summary_data.append({
                                '윈도우 크기': window_size,
                                '고유 Prefix 수': total_prefixes,
                                '총 관측수': window_df['총 관측수'].sum(),
                                '평균 관측수': f"{window_df['총 관측수'].mean():.2f}",
                                '평균 신뢰도 (%)': f"{confidences.mean():.2f}",
                                '최소 신뢰도 (%)': f"{confidences.min():.2f}",
                                '최대 신뢰도 (%)': f"{confidences.max():.2f}"
                            })
                    
                    if summary_data:
                        summary_df = pd.DataFrame(summary_data)
                        st.dataframe(summary_df, use_container_width=True, hide_index=True)
                        
                        # 신뢰도 구간별 분포 (1% 단위) - 윈도우 크기별로 표시
                        st.markdown("### 📊 신뢰도 구간별 분포 (1% 단위)")
                        
                        for window_size in window_sizes_to_analyze:
                            if window_size in all_confidence_distributions:
                                bins = all_confidence_distributions[window_size]
                                
                                if bins:
                                    with st.expander(f"윈도우 크기 {window_size} - 신뢰도 분포", expanded=False):
                                        # 분포 데이터 준비
                                        dist_data = []
                                        
                                        def sort_key(x):
                                            """정렬 키 함수: 구간을 숫자로 변환"""
                                            key = x[0]
                                            if key == '100':
                                                return 100.0
                                            elif '-' in key:
                                                return float(key.split('-')[0])
                                            else:
                                                return float(key)
                                        
                                        for bin_range, count in sorted(bins.items(), key=sort_key):
                                            total_prefixes = summary_df[summary_df['윈도우 크기'] == window_size]['고유 Prefix 수'].iloc[0]
                                            ratio = (count / total_prefixes * 100) if total_prefixes > 0 else 0
                                            dist_data.append({
                                                '신뢰도 구간 (%)': bin_range,
                                                'Prefix 수': count,
                                                '비율 (%)': f"{ratio:.2f}"
                                            })
                                        
                                        dist_df = pd.DataFrame(dist_data)
                                        
                                        # 여러 컬럼으로 나누어 표시 (가독성 향상)
                                        num_cols = 3
                                        cols = st.columns(num_cols)
                                        
                                        rows_per_col = (len(dist_df) + num_cols - 1) // num_cols
                                        
                                        for col_idx in range(num_cols):
                                            with cols[col_idx]:
                                                start_idx = col_idx * rows_per_col
                                                end_idx = min((col_idx + 1) * rows_per_col, len(dist_df))
                                                if start_idx < len(dist_df):
                                                    col_df = dist_df.iloc[start_idx:end_idx]
                                                    st.dataframe(col_df, use_container_width=True, hide_index=True)
                                        
                                        # 히스토그램 스타일 시각화 (텍스트 기반)
                                        st.markdown("#### 시각화")
                                        max_count = max(bins.values()) if bins else 1
                                        
                                        def sort_key(x):
                                            """정렬 키 함수: 구간을 숫자로 변환"""
                                            key = x[0]
                                            if key == '100':
                                                return 100.0
                                            elif '-' in key:
                                                return float(key.split('-')[0])
                                            else:
                                                return float(key)
                                        
                                        for bin_range, count in sorted(bins.items(), key=sort_key):
                                            total_prefixes = summary_df[summary_df['윈도우 크기'] == window_size]['고유 Prefix 수'].iloc[0]
                                            ratio = (count / total_prefixes * 100) if total_prefixes > 0 else 0
                                            bar_length = int((count / max_count) * 50) if max_count > 0 else 0
                                            bar = '█' * bar_length
                                            st.text(f"{bin_range:>8}%: {bar} {count:>4}개 ({ratio:>5.2f}%)")
                                        
                                        # 요약 정보
                                        def get_bin_value(k):
                                            """구간 키를 숫자로 변환"""
                                            if k == '100':
                                                return 100.0
                                            elif '-' in k:
                                                return float(k.split('-')[0])
                                            else:
                                                return float(k)
                                        
                                        over_60_count = sum(v for k, v in bins.items() if get_bin_value(k) >= 60)
                                        over_60_ratio = (over_60_count / total_prefixes * 100) if total_prefixes > 0 else 0
                                        over_70_count = sum(v for k, v in bins.items() if get_bin_value(k) >= 70)
                                        over_80_count = sum(v for k, v in bins.items() if get_bin_value(k) >= 80)
                                        
                                        st.markdown("---")
                                        col1, col2, col3 = st.columns(3)
                                        with col1:
                                            st.metric("60% 이상 Prefix", f"{over_60_count}개", f"{over_60_ratio:.1f}%")
                                        with col2:
                                            st.metric("70% 이상 Prefix", f"{over_70_count}개")
                                        with col3:
                                            st.metric("80% 이상 Prefix", f"{over_80_count}개")
                                        
                                        # 경고 메시지
                                        if over_60_ratio < 20:
                                            st.warning(f"⚠️ **경고**: 60% 이상 신뢰도인 prefix가 {over_60_ratio:.1f}%로 매우 적습니다. 임계값 60%를 사용하면 예측 기회가 매우 제한적일 수 있습니다.")
                                        elif over_60_ratio < 30:
                                            st.info(f"💡 **정보**: 60% 이상 신뢰도인 prefix가 {over_60_ratio:.1f}%입니다. 임계값 60% 사용 시 예측 빈도가 낮을 수 있습니다.")
                                else:
                                    st.info(f"윈도우 크기 {window_size}: 데이터 없음")
                else:
                    st.warning("⚠️ 데이터가 없습니다. ngram_chunks가 생성되었는지 확인해주세요.")
                
            except Exception as e:
                st.error(f"❌ 데이터 조회 중 오류 발생: {str(e)}")
                import traceback
                st.error(f"상세 오류: {traceback.format_exc()}")
            finally:
                conn.close()
            
    except Exception as e:
        st.error(f"❌ 윈도우 크기별 prefix 관측수 및 신뢰도 통계 계산 중 오류 발생: {str(e)}")
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
    
    # 앙상블 투표 인터랙티브 시나리오 검증 섹션 (독립, 화면 가장 마지막)
    st.markdown("---")
    st.header("앙상블 투표 인터랙티브 시나리오 검증")
    st.markdown("새로 추가되는 grid_string을 앙상블 투표 인터랙티브 시나리오 방식으로 자동 테스트하여 최대 연속 실패 횟수를 분석합니다.")
    
    # 설정 섹션
    # Session state 초기화
    if 'validation_ensemble_cutoff_id' not in st.session_state:
        st.session_state.validation_ensemble_cutoff_id = None
    
    with st.form("validation_ensemble_settings_form", clear_on_submit=False):
        st.markdown("### 설정")
        col1, col2 = st.columns([2, 1])
        
        with col1:
            # 기준 Grid String ID 선택
            df_all_strings = load_preprocessed_data()
            if len(df_all_strings) > 0:
                # grid_string_id와 정보를 함께 표시
                grid_string_options = []
                for _, row in df_all_strings.iterrows():
                    display_text = f"ID {row['id']} - 길이 {row['string_length']} - {row['created_at']}"
                    grid_string_options.append((row['id'], display_text))
                
                # 최신 것부터 표시
                grid_string_options.sort(key=lambda x: x[0], reverse=True)
                
                # 현재 선택된 값 가져오기
                current_selected = st.session_state.validation_ensemble_cutoff_id
                default_index = 0
                if current_selected is not None:
                    option_ids = [None] + [opt[0] for opt in grid_string_options]
                    if current_selected in option_ids:
                        default_index = option_ids.index(current_selected)
                
                selected_cutoff_id = st.selectbox(
                    "기준 Grid String ID (이 ID 이후의 데이터 검증)",
                    options=[None] + [opt[0] for opt in grid_string_options],
                    format_func=lambda x: "전체 데이터" if x is None else f"ID {x} 이후",
                    index=default_index,
                    key="validation_ensemble_cutoff_id_select"
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
        
        with col2:
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("**검증 설정 (고정)**")
            st.info("윈도우 크기: 7")
            st.info("임계값: 사용 안함")
        
        # 검증 실행 버튼
        if st.form_submit_button("검증 실행", type="primary", use_container_width=True):
            if selected_cutoff_id is None:
                st.warning("⚠️ 기준 Grid String ID를 선택해주세요.")
            else:
                st.session_state.validation_ensemble_cutoff_id = selected_cutoff_id
                st.session_state.validation_ensemble_results = None
                st.rerun()
    
    # 검증 실행 및 결과 표시
    if 'validation_ensemble_cutoff_id' in st.session_state and st.session_state.validation_ensemble_cutoff_id is not None:
        cutoff_id = st.session_state.validation_ensemble_cutoff_id
        
        # 결과가 캐시되어 있으면 사용, 없으면 실행
        if 'validation_ensemble_results' in st.session_state and st.session_state.validation_ensemble_results is not None:
            batch_results = st.session_state.validation_ensemble_results
        else:
            with st.spinner("검증 실행 중..."):
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                try:
                    batch_results = batch_validate_ensemble_scenario(
                        cutoff_id,
                        window_size=7,
                        use_threshold=False
                    )
                    
                    if batch_results is not None:
                        st.session_state.validation_ensemble_results = batch_results
                    else:
                        st.error("검증 실행 실패")
                        batch_results = None
                        
                except Exception as e:
                    st.error(f"검증 실행 중 오류 발생: {str(e)}")
                    import traceback
                    st.error(f"상세 오류: {traceback.format_exc()}")
                    batch_results = None
                finally:
                    progress_bar.empty()
                    status_text.empty()
        
        # 결과 표시
        if batch_results is not None and len(batch_results['results']) > 0:
            summary = batch_results['summary']
            results = batch_results['results']
            
            # 요약 통계
            st.markdown("---")
            st.markdown("### 요약 통계")
            
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("총 Grid String 수", f"{summary['total_grid_strings']}")
            with col2:
                st.metric("평균 정확도", f"{summary['avg_accuracy']:.2f}%")
            with col3:
                st.metric("최대 연속 실패", f"{summary['max_consecutive_failures']}")
            with col4:
                st.metric("평균 최대 연속 실패", f"{summary['avg_max_consecutive_failures']:.2f}")
            
            # 전체 통계
            st.markdown("---")
            st.markdown("### 전체 통계")
            col1, col2 = st.columns(2)
            with col1:
                st.metric("총 스텝 수", f"{summary['total_steps']}")
            with col2:
                st.metric("총 실패 횟수", f"{summary['total_failures']}")
            
            # Grid String별 상세 결과 테이블
            st.markdown("---")
            st.markdown("### Grid String별 상세 결과")
            
            results_data = []
            for result in results:
                results_data.append({
                    'Grid String ID': result['grid_string_id'],
                    '최대 연속 실패': result['max_consecutive_failures'],
                    '총 스텝': result['total_steps'],
                    '총 실패': result['total_failures'],
                    '정확도 (%)': f"{result['accuracy']:.2f}"
                })
            
            results_df = pd.DataFrame(results_data)
            st.dataframe(results_df, use_container_width=True, hide_index=True)
            
            # 최대 연속 실패 분포 히스토그램
            st.markdown("---")
            st.markdown("### 최대 연속 실패 분포")
            
            max_failures_list = [r['max_consecutive_failures'] for r in results]
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
                col1, col2 = st.columns([2, 1])
                with col1:
                    st.markdown("#### 구간별 분포")
                    max_count = max(bins.values()) if bins else 1
                    
                    for bin_range, count in sorted(bins.items(), key=lambda x: {
                        '0': 0, '1-2': 1, '3-5': 2, '6-10': 3, '11+': 4
                    }.get(x[0], 5)):
                        ratio = (count / len(results) * 100) if len(results) > 0 else 0
                        bar_length = int((count / max_count) * 50) if max_count > 0 else 0
                        bar = '█' * bar_length
                        st.text(f"{bin_range:>8}: {bar} {count:>4}개 ({ratio:>5.2f}%)")
                
                with col2:
                    st.markdown("#### 통계")
                    st.metric("최소값", min(max_failures_list))
                    st.metric("최대값", max(max_failures_list))
                    st.metric("중앙값", sorted(max_failures_list)[len(max_failures_list) // 2])
            
            # 인사이트 분석
            st.markdown("---")
            st.markdown("### 인사이트 분석")
            
            # 최대 연속 실패가 발생한 grid_string 분석
            max_failure_results = [r for r in results if r['max_consecutive_failures'] == summary['max_consecutive_failures']]
            if len(max_failure_results) > 0:
                st.markdown(f"#### 최대 연속 실패 ({summary['max_consecutive_failures']}회) 발생 Grid String")
                max_failure_ids = [r['grid_string_id'] for r in max_failure_results]
                st.info(f"Grid String ID: {', '.join(map(str, max_failure_ids))}")
            
            # 성공률이 높은/낮은 grid_string 분석
            sorted_results = sorted(results, key=lambda x: x['accuracy'], reverse=True)
            
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("#### 정확도 상위 5개")
                top5_data = []
                for i, result in enumerate(sorted_results[:5], 1):
                    top5_data.append({
                        '순위': i,
                        'Grid String ID': result['grid_string_id'],
                        '정확도 (%)': f"{result['accuracy']:.2f}",
                        '최대 연속 실패': result['max_consecutive_failures']
                    })
                top5_df = pd.DataFrame(top5_data)
                st.dataframe(top5_df, use_container_width=True, hide_index=True)
            
            with col2:
                st.markdown("#### 정확도 하위 5개")
                bottom5_data = []
                for i, result in enumerate(sorted_results[-5:], 1):
                    bottom5_data.append({
                        '순위': len(sorted_results) - 5 + i,
                        'Grid String ID': result['grid_string_id'],
                        '정확도 (%)': f"{result['accuracy']:.2f}",
                        '최대 연속 실패': result['max_consecutive_failures']
                    })
                bottom5_df = pd.DataFrame(bottom5_data)
                st.dataframe(bottom5_df, use_container_width=True, hide_index=True)
            
            # 앙상블 투표 방식의 강점/약점 분석
            st.markdown("---")
            st.markdown("#### 앙상블 투표 방식 분석")
            
            # 연속 실패가 5회 이상인 경우 분석
            high_failure_results = [r for r in results if r['max_consecutive_failures'] >= 5]
            if len(high_failure_results) > 0:
                high_failure_ratio = (len(high_failure_results) / len(results) * 100)
                st.warning(f"⚠️ 최대 연속 실패가 5회 이상인 Grid String: {len(high_failure_results)}개 ({high_failure_ratio:.1f}%)")
                st.caption("5회 연속 실패는 게임 실패 조건이므로 주의가 필요합니다.")
            else:
                st.success(f"✅ 모든 Grid String에서 최대 연속 실패가 5회 미만입니다.")
            
            # 평균 정확도 분석
            if summary['avg_accuracy'] >= 70:
                st.success(f"✅ 평균 정확도가 {summary['avg_accuracy']:.2f}%로 높습니다.")
            elif summary['avg_accuracy'] >= 50:
                st.info(f"💡 평균 정확도가 {summary['avg_accuracy']:.2f}%입니다.")
            else:
                st.warning(f"⚠️ 평균 정확도가 {summary['avg_accuracy']:.2f}%로 낮습니다.")
            
            # 초기화 버튼
            if st.button("결과 초기화", key="validation_ensemble_reset"):
                if 'validation_ensemble_results' in st.session_state:
                    del st.session_state.validation_ensemble_results
                if 'validation_ensemble_cutoff_id' in st.session_state:
                    del st.session_state.validation_ensemble_cutoff_id
                st.rerun()
        else:
            st.info("검증 결과가 없습니다.")
    
if __name__ == "__main__":
    main()

