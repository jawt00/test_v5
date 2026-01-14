"""
신뢰도 스킵 전략 가설 검증 분석 앱
첫 번째 일치 예측 이후 연속 불일치 패턴 분석
"""

import streamlit as st

# 페이지 설정 (모든 import 전에 실행되어야 함)
st.set_page_config(
    page_title="confidence_skip_hypothesis_analysis_app",
    page_icon="📊",
    layout="wide"
)

import pandas as pd
import sqlite3
from collections import defaultdict
from datetime import datetime
import uuid

# 기존 앱의 함수들 import
from hypothesis_validation_app import get_db_connection

# interactive_multi_step_validation_app에서 필요한 함수들 import
from interactive_multi_step_validation_app import (
    load_ngram_chunks,
    build_frequency_model,
    build_weighted_model,
    predict_for_prefix,
    predict_with_fallback_interval,
    get_next_prefix
)

# DB 경로
DB_PATH = 'hypothesis_validation.db'

def load_validation_sessions():
    """
    저장된 검증 세션 목록 조회
    
    Returns:
        DataFrame: 검증 세션 목록
    """
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()
    
    try:
        query = """
            SELECT 
                validation_id,
                cutoff_grid_string_id,
                window_size,
                method,
                use_threshold,
                threshold,
                max_interval,
                confidence_skip_threshold_1,
                confidence_skip_threshold_2,
                created_at
            FROM confidence_skip_validation_sessions
            ORDER BY created_at DESC
        """
        df = pd.read_sql_query(query, conn)
        return df
    except Exception as e:
        st.error(f"검증 세션 조회 중 오류 발생: {str(e)}")
        return pd.DataFrame()
    finally:
        conn.close()

def load_validation_session_steps(validation_id, confidence_skip_threshold):
    """
    특정 검증 세션의 모든 스텝 데이터 조회
    
    Args:
        validation_id: 검증 세션 ID
        confidence_skip_threshold: 신뢰도 스킵 임계값
    
    Returns:
        DataFrame: 스텝 데이터
    """
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()
    
    try:
        query = """
            SELECT 
                id,
                validation_id,
                confidence_skip_threshold,
                grid_string_id,
                step,
                prefix,
                predicted,
                actual,
                is_correct,
                confidence,
                is_forced,
                current_interval,
                has_prediction,
                validated,
                skipped,
                created_at
            FROM confidence_skip_validation_steps
            WHERE validation_id = ? AND confidence_skip_threshold = ?
            ORDER BY grid_string_id, step
        """
        df = pd.read_sql_query(query, conn, params=[validation_id, confidence_skip_threshold])
        return df
    except Exception as e:
        st.error(f"스텝 데이터 조회 중 오류 발생: {str(e)}")
        return pd.DataFrame()
    finally:
        conn.close()

def load_live_game_sessions():
    """
    저장된 라이브 게임 세션 목록 조회
    
    Returns:
        DataFrame: 라이브 게임 세션 목록
    """
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()
    
    try:
        query = """
            SELECT 
                session_id,
                grid_string,
                window_size,
                method,
                use_threshold,
                threshold,
                max_interval,
                confidence_skip_threshold,
                total_steps,
                total_predictions,
                total_failures,
                max_consecutive_failures,
                accuracy,
                started_at,
                auto_executed
            FROM live_game_sessions
            ORDER BY started_at DESC
        """
        df = pd.read_sql_query(query, conn)
        return df
    except Exception as e:
        st.error(f"라이브 게임 세션 조회 중 오류 발생: {str(e)}")
        return pd.DataFrame()
    finally:
        conn.close()

def load_live_game_steps(session_id):
    """
    특정 라이브 게임 세션의 모든 스텝 데이터 조회
    
    Args:
        session_id: 라이브 게임 세션 ID
    
    Returns:
        DataFrame: 스텝 데이터
    """
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()
    
    try:
        query = """
            SELECT 
                id,
                session_id,
                step,
                prefix,
                predicted_value,
                actual_value,
                is_correct,
                confidence,
                is_forced,
                current_interval,
                has_prediction,
                validated,
                skipped,
                created_at
            FROM live_game_steps
            WHERE session_id = ?
            ORDER BY step
        """
        df = pd.read_sql_query(query, conn, params=[session_id])
        return df
    except Exception as e:
        st.error(f"라이브 게임 스텝 데이터 조회 중 오류 발생: {str(e)}")
        return pd.DataFrame()
    finally:
        conn.close()

def analyze_live_game_first_match_hypothesis(session_id):
    """
    라이브 게임 데이터에 대한 첫 일치 후 연속 불일치 분석 로직 실행
    
    가설: 첫 번째 스킵되지 않은 예측이 일치로 시작하는 경우
    다음 게임부터 최대 연속 불일치가 6개 미만일 것이다.
    
    Args:
        session_id: 라이브 게임 세션 ID
    
    Returns:
        dict: 분석 결과
    """
    # 스텝 데이터 로드
    steps_df = load_live_game_steps(session_id)
    
    if len(steps_df) == 0:
        return {
            'session_id': session_id,
            'has_first_match': False,
            'first_match_step': None,
            'max_consecutive_mismatches_after_first': None,
            'is_below_6': None,
            'total_steps': 0
        }
    
    steps_df = steps_df.sort_values('step').reset_index(drop=True)
    
    # 디버깅: 데이터 확인
    if len(steps_df) > 0:
        # skipped 값 분포 확인
        skipped_counts = steps_df['skipped'].value_counts()
        validated_counts = steps_df['validated'].value_counts()
        has_prediction_counts = steps_df['has_prediction'].value_counts()
        
        # 디버그 정보 (필요시 사용)
        # st.write(f"Session {session_id} - Skipped 분포: {dict(skipped_counts)}")
        # st.write(f"Session {session_id} - Validated 분포: {dict(validated_counts)}")
        # st.write(f"Session {session_id} - Has Prediction 분포: {dict(has_prediction_counts)}")
    
    # 첫 번째 스킵되지 않은 예측 찾기 (skipped=0 또는 False)
    first_non_skipped_idx = None
    for idx, row in steps_df.iterrows():
        skipped_val = row['skipped']
        # 0 또는 False 모두 처리
        if skipped_val == 0 or skipped_val is False or skipped_val == '0':
            first_non_skipped_idx = idx
            break
    
    if first_non_skipped_idx is None:
        # 스킵되지 않은 예측이 없는 경우
        return {
            'session_id': session_id,
            'has_first_match': False,
            'first_match_step': None,
            'max_consecutive_mismatches_after_first': None,
            'is_below_6': None,
            'total_steps': len(steps_df)
        }
    
    first_non_skipped = steps_df.iloc[first_non_skipped_idx]
    
    # 첫 번째 스킵되지 않은 예측이 일치인지 확인
    # is_correct: 1=True, 0=False, None=None
    is_correct_val = first_non_skipped['is_correct']
    # 1 또는 True인 경우만 일치로 처리
    is_match = (is_correct_val == 1 or is_correct_val is True or is_correct_val == '1')
    if not is_match:  # 일치가 아님
        return {
            'session_id': session_id,
            'has_first_match': False,
            'first_match_step': None,
            'max_consecutive_mismatches_after_first': None,
            'is_below_6': None,
            'total_steps': len(steps_df)
        }
    
    # 첫 일치 스텝 기록
    first_match_step = first_non_skipped['step']
    
    # 첫 일치 이후 첫 번째 검증된 스텝 찾기 (두 번째)
    second_validated_idx = None
    for idx in range(first_non_skipped_idx + 1, len(steps_df)):
        row = steps_df.iloc[idx]
        validated_val = row['validated']
        skipped_val = row['skipped']
        is_validated = (validated_val == 1 or validated_val is True or validated_val == '1')
        is_skipped = (skipped_val == 1 or skipped_val is True or skipped_val == '1')
        
        if is_validated and not is_skipped:
            second_validated_idx = idx
            break
    
    # 두 번째가 불일치로 시작하는지 확인
    if second_validated_idx is None:
        return {
            'session_id': session_id,
            'has_first_match': True,
            'first_match_step': first_match_step,
            'second_is_mismatch': False,
            'second_mismatch_step': None,
            'max_consecutive_mismatches_after_first': None,
            'is_below_6': None,
            'is_6_or_more': False,
            'has_complete_data': False,
            'ended_with_mismatch': False,
            'total_steps': len(steps_df)
        }
    
    second_validated = steps_df.iloc[second_validated_idx]
    is_correct_val = second_validated['is_correct']
    is_second_mismatch = (is_correct_val == 0 or is_correct_val is False or is_correct_val == '0')
    
    if not is_second_mismatch:
        return {
            'session_id': session_id,
            'has_first_match': True,
            'first_match_step': first_match_step,
            'second_is_mismatch': False,
            'second_mismatch_step': None,
            'max_consecutive_mismatches_after_first': None,
            'is_below_6': None,
            'is_6_or_more': False,
            'has_complete_data': False,
            'ended_with_mismatch': False,
            'total_steps': len(steps_df)
        }
    
    # 두 번째가 불일치로 시작함 -> 연속 불일치 계산
    max_consecutive_mismatches = 0
    current_consecutive = 0
    has_complete_data = False
    second_mismatch_step = second_validated['step']
    
    # 두 번째 불일치 스텝부터 시작하여 연속 불일치 계산
    for idx in range(second_validated_idx, len(steps_df)):
        row = steps_df.iloc[idx]
        
        validated_val = row['validated']
        skipped_val = row['skipped']
        is_validated = (validated_val == 1 or validated_val is True or validated_val == '1')
        is_skipped = (skipped_val == 1 or skipped_val is True or skipped_val == '1')
        
        if is_validated and not is_skipped:
            is_correct_val = row['is_correct']
            is_match = (is_correct_val == 1 or is_correct_val is True or is_correct_val == '1')
            
            if is_match:
                # 일치를 만남 -> 완전한 데이터
                has_complete_data = True
                if current_consecutive > max_consecutive_mismatches:
                    max_consecutive_mismatches = current_consecutive
                break
            else:
                # 불일치 계속
                current_consecutive += 1
                if current_consecutive > max_consecutive_mismatches:
                    max_consecutive_mismatches = current_consecutive
    
    return {
        'session_id': session_id,
        'has_first_match': True,
        'first_match_step': first_match_step,
        'second_is_mismatch': True,
        'second_mismatch_step': second_mismatch_step,
        'max_consecutive_mismatches_after_first': max_consecutive_mismatches,
        'is_below_6': max_consecutive_mismatches < 6,
        'is_6_or_more': max_consecutive_mismatches >= 6,
        'has_complete_data': has_complete_data,
        'ended_with_mismatch': not has_complete_data,
        'total_steps': len(steps_df)
    }

def analyze_all_live_games_first_match_hypothesis():
    """
    모든 라이브 게임 세션의 전체 히스토리를 하나로 합쳐서 분석
    세션 구분 없이 모든 스텝을 순서대로 처리
    
    Returns:
        dict: 분석 결과 및 통계
    """
    conn = get_db_connection()
    if conn is None:
        return {
            'has_first_match': False,
            'first_match_step': None,
            'max_consecutive_mismatches_after_first': None,
            'is_below_6': None,
            'total_steps': 0,
            'all_steps': []
        }
    
    try:
        # 모든 라이브 게임 스텝을 세션 ID, 스텝 순서대로 조회
        query = """
            SELECT 
                st.session_id,
                st.step,
                st.prefix,
                st.predicted_value,
                st.actual_value,
                st.is_correct,
                st.confidence,
                st.is_forced,
                st.current_interval,
                st.has_prediction,
                st.validated,
                st.skipped,
                s.started_at
            FROM live_game_steps st
            JOIN live_game_sessions s ON st.session_id = s.session_id
            ORDER BY s.started_at ASC, st.step ASC
        """
        all_steps_df = pd.read_sql_query(query, conn)
        
        if len(all_steps_df) == 0:
            return {
                'has_first_match': False,
                'first_match_step': None,
                'max_consecutive_mismatches_after_first': None,
                'is_below_6': None,
                'total_steps': 0,
                'all_steps': []
            }
        
        all_steps_df = all_steps_df.reset_index(drop=True)
        
        # 첫 번째 스킵되지 않은 예측 찾기 (skipped=0 또는 False)
        first_non_skipped_idx = None
        for idx, row in all_steps_df.iterrows():
            skipped_val = row['skipped']
            # 0 또는 False 모두 처리
            if skipped_val == 0 or skipped_val is False or skipped_val == '0':
                first_non_skipped_idx = idx
                break
        
        if first_non_skipped_idx is None:
            # 스킵되지 않은 예측이 없는 경우
            return {
                'has_first_match': False,
                'first_match_step': None,
                'max_consecutive_mismatches_after_first': None,
                'is_below_6': None,
                'total_steps': len(all_steps_df),
                'all_steps': all_steps_df.to_dict('records')
            }
        
        first_non_skipped = all_steps_df.iloc[first_non_skipped_idx]
        
        # 첫 번째 스킵되지 않은 예측이 일치인지 확인
        is_correct_val = first_non_skipped['is_correct']
        is_match = (is_correct_val == 1 or is_correct_val is True or is_correct_val == '1')
        if not is_match:  # 일치가 아님
            return {
                'has_first_match': False,
                'first_match_step': None,
                'max_consecutive_mismatches_after_first': None,
                'is_below_6': None,
                'total_steps': len(all_steps_df),
                'all_steps': all_steps_df.to_dict('records')
            }
        
        # 첫 일치 스텝 기록 (전체 인덱스로 표시)
        first_match_idx = first_non_skipped_idx
        first_match_step = first_non_skipped['step']
        
        # 첫 일치 이후 첫 번째 검증된 스텝 찾기 (두 번째)
        second_validated_idx = None
        for idx in range(first_non_skipped_idx + 1, len(all_steps_df)):
            row = all_steps_df.iloc[idx]
            validated_val = row['validated']
            skipped_val = row['skipped']
            is_validated = (validated_val == 1 or validated_val is True or validated_val == '1')
            is_skipped = (skipped_val == 1 or skipped_val is True or skipped_val == '1')
            
            if is_validated and not is_skipped:
                second_validated_idx = idx
                break
        
        # 두 번째가 불일치로 시작하는지 확인
        if second_validated_idx is None:
            return {
                'has_first_match': True,
                'first_match_idx': first_match_idx,
                'first_match_step': first_match_step,
                'first_match_session_id': first_non_skipped['session_id'],
                'second_is_mismatch': False,
                'second_mismatch_step': None,
                'max_consecutive_mismatches_after_first': None,
                'is_below_6': None,
                'is_6_or_more': False,
                'has_complete_data': False,
                'ended_with_mismatch': False,
                'total_steps': len(all_steps_df),
                'all_steps': all_steps_df.to_dict('records')
            }
        
        second_validated = all_steps_df.iloc[second_validated_idx]
        is_correct_val = second_validated['is_correct']
        is_second_mismatch = (is_correct_val == 0 or is_correct_val is False or is_correct_val == '0')
        
        if not is_second_mismatch:
            return {
                'has_first_match': True,
                'first_match_idx': first_match_idx,
                'first_match_step': first_match_step,
                'first_match_session_id': first_non_skipped['session_id'],
                'second_is_mismatch': False,
                'second_mismatch_step': None,
                'max_consecutive_mismatches_after_first': None,
                'is_below_6': None,
                'is_6_or_more': False,
                'has_complete_data': False,
                'ended_with_mismatch': False,
                'total_steps': len(all_steps_df),
                'all_steps': all_steps_df.to_dict('records')
            }
        
        # 두 번째가 불일치로 시작함 -> 연속 불일치 계산
        max_consecutive_mismatches = 0
        current_consecutive = 0
        has_complete_data = False
        second_mismatch_step = second_validated['step']
        second_mismatch_session_id = second_validated['session_id']
        
        # 두 번째 불일치 스텝부터 시작하여 연속 불일치 계산
        for idx in range(second_validated_idx, len(all_steps_df)):
            row = all_steps_df.iloc[idx]
            
            validated_val = row['validated']
            skipped_val = row['skipped']
            is_validated = (validated_val == 1 or validated_val is True or validated_val == '1')
            is_skipped = (skipped_val == 1 or skipped_val is True or skipped_val == '1')
            
            if is_validated and not is_skipped:
                is_correct_val = row['is_correct']
                is_match = (is_correct_val == 1 or is_correct_val is True or is_correct_val == '1')
                
                if is_match:
                    # 일치를 만남 -> 완전한 데이터
                    has_complete_data = True
                    if current_consecutive > max_consecutive_mismatches:
                        max_consecutive_mismatches = current_consecutive
                    break
                else:
                    # 불일치 계속
                    current_consecutive += 1
                    if current_consecutive > max_consecutive_mismatches:
                        max_consecutive_mismatches = current_consecutive
        
        return {
            'has_first_match': True,
            'first_match_idx': first_match_idx,
            'first_match_step': first_match_step,
            'first_match_session_id': first_non_skipped['session_id'],
            'second_is_mismatch': True,
            'second_mismatch_idx': second_validated_idx,
            'second_mismatch_step': second_mismatch_step,
            'second_mismatch_session_id': second_mismatch_session_id,
            'max_consecutive_mismatches_after_first': max_consecutive_mismatches,
            'is_below_6': max_consecutive_mismatches < 6,
            'is_6_or_more': max_consecutive_mismatches >= 6,
            'has_complete_data': has_complete_data,
            'ended_with_mismatch': not has_complete_data,
            'total_steps': len(all_steps_df),
            'all_steps': all_steps_df.to_dict('records')
        }
        
    except Exception as e:
        st.error(f"전체 라이브 게임 분석 중 오류 발생: {str(e)}")
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
        return {
            'has_first_match': False,
            'first_match_step': None,
            'max_consecutive_mismatches_after_first': None,
            'is_below_6': None,
            'total_steps': 0,
            'all_steps': []
        }
    finally:
        conn.close()

def analyze_first_match_hypothesis(validation_id, confidence_skip_threshold):
    """
    첫 일치 후 연속 불일치 분석 로직 실행
    
    가설: 첫 번째 스킵되지 않은 예측이 일치로 시작하는 grid_string은
    다음 게임부터 최대 연속 불일치가 6개 미만일 것이다.
    
    Args:
        validation_id: 검증 세션 ID
        confidence_skip_threshold: 신뢰도 스킵 임계값
    
    Returns:
        dict: 분석 결과
    """
    # 스텝 데이터 로드
    steps_df = load_validation_session_steps(validation_id, confidence_skip_threshold)
    
    if len(steps_df) == 0:
        return {
            'total_grid_strings': 0,
            'grid_strings_with_first_match': 0,
            'grid_strings_with_second_mismatch': 0,
            'grid_strings_below_6': 0,
            'cases_6_or_more_complete': 0,
            'cases_6_or_more_incomplete': 0,
            'cases_6_or_more_total': 0,
            'incomplete_data_count': 0,
            'below_6_ratio': 0.0,
            'avg_max_consecutive_mismatches': 0.0,
            'max_consecutive_mismatches': 0,
            'results': [],
            'cases_6_or_more_grid_ids': []
        }
    
    # Grid String별로 그룹화
    grid_string_ids = steps_df['grid_string_id'].unique()
    results = []
    
    for grid_string_id in grid_string_ids:
        grid_steps = steps_df[steps_df['grid_string_id'] == grid_string_id].copy()
        grid_steps = grid_steps.sort_values('step').reset_index(drop=True)
        
        # 첫 번째 스킵되지 않은 예측 찾기 (skipped=0이고 has_prediction=1인 경우)
        first_non_skipped_idx = None
        for idx, row in grid_steps.iterrows():
            skipped_val = row['skipped']
            has_prediction_val = row['has_prediction']
            # skipped=0이고 has_prediction=1인 경우만 첫 예측으로 간주
            is_skipped = (skipped_val == 1 or skipped_val is True)
            has_prediction = (has_prediction_val == 1 or has_prediction_val is True)
            
            if not is_skipped and has_prediction:
                first_non_skipped_idx = idx
                break
        
        if first_non_skipped_idx is None:
            # 스킵되지 않은 예측이 없는 경우
            results.append({
                'grid_string_id': grid_string_id,
                'has_first_match': False,
                'first_match_step': None,
                'max_consecutive_mismatches_after_first': None,
                'is_below_6': None
            })
            continue
        
        first_non_skipped = grid_steps.iloc[first_non_skipped_idx]
        
        # 첫 번째 스킵되지 않은 예측이 일치인지 확인
        # is_correct: 1=True, 0=False, None=None
        is_correct_val = first_non_skipped['is_correct']
        is_match = (is_correct_val == 1 or is_correct_val is True)
        if not is_match:  # 일치가 아님
            results.append({
                'grid_string_id': grid_string_id,
                'has_first_match': False,
                'first_match_step': None,
                'max_consecutive_mismatches_after_first': None,
                'is_below_6': None
            })
            continue
        
        # 첫 일치 스텝 기록
        first_match_step = first_non_skipped['step']
        
        # 첫 일치 이후 첫 번째 검증된 스텝 찾기 (두 번째)
        second_validated_idx = None
        for idx in range(first_non_skipped_idx + 1, len(grid_steps)):
            row = grid_steps.iloc[idx]
            validated_val = row['validated']
            skipped_val = row['skipped']
            is_validated = (validated_val == 1 or validated_val is True or validated_val == '1')
            is_skipped = (skipped_val == 1 or skipped_val is True or skipped_val == '1')
            
            if is_validated and not is_skipped:
                second_validated_idx = idx
                break
        
        # 두 번째가 불일치로 시작하는지 확인
        if second_validated_idx is None:
            # 첫 일치 이후 검증된 스텝이 없음
            results.append({
                'grid_string_id': grid_string_id,
                'has_first_match': True,
                'first_match_step': first_match_step,
                'second_is_mismatch': False,
                'second_mismatch_step': None,
                'max_consecutive_mismatches_after_first': None,
                'is_below_6': None,
                'is_6_or_more': False,
                'has_complete_data': False,
                'ended_with_mismatch': False
            })
            continue
        
        second_validated = grid_steps.iloc[second_validated_idx]
        is_correct_val = second_validated['is_correct']
        is_second_mismatch = (is_correct_val == 0 or is_correct_val is False or is_correct_val == '0')
        
        if not is_second_mismatch:
            # 두 번째가 일치임 -> 분석 대상 아님
            results.append({
                'grid_string_id': grid_string_id,
                'has_first_match': True,
                'first_match_step': first_match_step,
                'second_is_mismatch': False,
                'second_mismatch_step': None,
                'max_consecutive_mismatches_after_first': None,
                'is_below_6': None,
                'is_6_or_more': False,
                'has_complete_data': False,
                'ended_with_mismatch': False
            })
            continue
        
        # 두 번째가 불일치로 시작함 -> 연속 불일치 계산
        max_consecutive_mismatches = 0
        current_consecutive = 0
        has_complete_data = False  # 다음 일치가 나왔는지 여부
        second_mismatch_step = second_validated['step']  # 두 번째 불일치 스텝 기록
        
        # 두 번째 불일치 스텝부터 시작하여 연속 불일치 계산
        for idx in range(second_validated_idx, len(grid_steps)):
            row = grid_steps.iloc[idx]
            
            validated_val = row['validated']
            skipped_val = row['skipped']
            is_validated = (validated_val == 1 or validated_val is True or validated_val == '1')
            is_skipped = (skipped_val == 1 or skipped_val is True or skipped_val == '1')
            
            if is_validated and not is_skipped:
                is_correct_val = row['is_correct']
                is_match = (is_correct_val == 1 or is_correct_val is True or is_correct_val == '1')
                
                if is_match:
                    # 일치를 만남 -> 완전한 데이터
                    has_complete_data = True
                    # 현재까지의 연속 불일치와 최대값 비교
                    if current_consecutive > max_consecutive_mismatches:
                        max_consecutive_mismatches = current_consecutive
                    break  # 다음 일치를 만났으므로 계산 종료
                else:
                    # 불일치 계속
                    current_consecutive += 1
                    if current_consecutive > max_consecutive_mismatches:
                        max_consecutive_mismatches = current_consecutive
        
        results.append({
            'grid_string_id': grid_string_id,
            'has_first_match': True,
            'first_match_step': first_match_step,
            'second_is_mismatch': True,
            'second_mismatch_step': second_mismatch_step,
            'max_consecutive_mismatches_after_first': max_consecutive_mismatches,
            'is_below_6': max_consecutive_mismatches < 6,
            'is_6_or_more': max_consecutive_mismatches >= 6,
            'has_complete_data': has_complete_data,
            'ended_with_mismatch': not has_complete_data
        })
    
    # 통계 계산
    total_grid_strings = len(grid_string_ids)
    grid_strings_with_first_match = sum(1 for r in results if r.get('has_first_match'))
    
    # 두 번째가 불일치로 시작하는 케이스만 필터링
    second_mismatch_results = [r for r in results if r.get('has_first_match') and r.get('second_is_mismatch') is True]
    grid_strings_with_second_mismatch = len(second_mismatch_results)
    
    # 완전한 데이터만 (다음 일치를 만난 케이스)
    complete_second_mismatch = [r for r in second_mismatch_results if r.get('has_complete_data') is True]
    
    # 불완전한 데이터 (불일치 상태로 끝난 케이스)
    incomplete_second_mismatch = [r for r in second_mismatch_results if r.get('has_complete_data') is False]
    
    # 6개 이상 연속 불일치 케이스 (핵심!)
    cases_6_or_more_complete = [r for r in complete_second_mismatch if r.get('is_6_or_more') is True]
    cases_6_or_more_incomplete = [r for r in incomplete_second_mismatch if r.get('is_6_or_more') is True]
    cases_6_or_more_total = len(cases_6_or_more_complete) + len(cases_6_or_more_incomplete)
    
    # 6개 미만 케이스 (완전한 데이터만)
    cases_below_6 = [r for r in complete_second_mismatch if r.get('is_below_6') is True]
    grid_strings_below_6 = len(cases_below_6)
    
    # 비율 계산 (완전한 데이터 기준)
    below_6_ratio = (grid_strings_below_6 / len(complete_second_mismatch) * 100) if len(complete_second_mismatch) > 0 else 0.0
    
    # 평균 및 최대값 계산
    max_mismatches_list = [r['max_consecutive_mismatches_after_first'] for r in second_mismatch_results if r['max_consecutive_mismatches_after_first'] is not None]
    avg_max_consecutive_mismatches = sum(max_mismatches_list) / len(max_mismatches_list) if len(max_mismatches_list) > 0 else 0.0
    max_consecutive_mismatches = max(max_mismatches_list) if len(max_mismatches_list) > 0 else 0
    
    return {
        'total_grid_strings': total_grid_strings,
        'grid_strings_with_first_match': grid_strings_with_first_match,
        'grid_strings_with_second_mismatch': grid_strings_with_second_mismatch,
        'grid_strings_below_6': grid_strings_below_6,
        'cases_6_or_more_complete': len(cases_6_or_more_complete),
        'cases_6_or_more_incomplete': len(cases_6_or_more_incomplete),
        'cases_6_or_more_total': cases_6_or_more_total,
        'incomplete_data_count': len(incomplete_second_mismatch),
        'below_6_ratio': below_6_ratio,
        'avg_max_consecutive_mismatches': avg_max_consecutive_mismatches,
        'max_consecutive_mismatches': max_consecutive_mismatches,
        'results': results,
        'cases_6_or_more_grid_ids': [r['grid_string_id'] for r in cases_6_or_more_complete + cases_6_or_more_incomplete]
    }

def validate_interactive_multi_step_scenario_with_confidence_skip_first_step_analysis(
    grid_string_id,
    cutoff_grid_string_id,
    window_size=7,
    method="빈도 기반",
    use_threshold=True,
    threshold=60,
    max_interval=6,
    reverse_forced_prediction=False,
    confidence_skip_threshold=51
):
    """
    신뢰도 기반 스킵 규칙이 있는 인터랙티브 다단계 예측 시나리오 검증 (첫 스텝 스킵 분석용)
    
    기존 함수를 복제하여 첫 스텝 스킵 여부와 게임 종료 상태를 추적하는 독립 함수
    
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
    
    Returns:
        dict: 검증 결과 (first_step_skipped, game_end_status, last_validation_result 포함)
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
                'first_step_skipped': False,
                'game_end_status': 'other',
                'last_validation_result': None,
                'history': []
            }
        
        # 학습 데이터 구축
        train_ids_query = "SELECT id FROM preprocessed_grid_strings WHERE id <= ? ORDER BY id"
        train_ids_df = pd.read_sql_query(train_ids_query, conn, params=[cutoff_grid_string_id])
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
                'first_step_skipped': False,
                'game_end_status': 'other',
                'last_validation_result': None,
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
        first_step_skipped = False  # 첫 번째 예측 가능한 스텝에서 스킵 여부
        first_prediction_encountered = False  # 첫 번째 예측 가능한 스텝을 만났는지 여부
        last_validation_result = None  # 마지막 검증 결과
        
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
                'first_step_skipped': False,
                'game_end_status': 'other',
                'last_validation_result': None,
                'history': []
            }
        
        current_prefix = grid_string[:prefix_length]
        
        # 각 스텝마다 예측
        i = prefix_length
        while i < len(grid_string):
            total_steps += 1
            actual_value = grid_string[i]
            
            # 예측 수행 (기본 규칙: 모든 스텝에서 예측 시도)
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
            
            # 첫 번째 예측 가능한 스텝 감지 및 스킵 여부 추적
            if has_prediction and not first_prediction_encountered:
                first_prediction_encountered = True
                # 신뢰도 기반 스킵 규칙 체크
                if use_threshold and is_forced and confidence < confidence_skip_threshold:
                    first_step_skipped = True
            
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
                
                # 마지막 검증 결과 업데이트
                last_validation_result = 'match' if is_correct else 'mismatch'
                
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
        
        # 게임 종료 상태 판단
        if last_validation_result == 'match':
            game_end_status = 'match_end'
        elif max_consecutive_failures >= 6:
            game_end_status = 'mismatch_6plus'
        else:
            game_end_status = 'other'
        
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
            'first_step_skipped': first_step_skipped,  # 첫 스텝 스킵 여부
            'game_end_status': game_end_status,  # 게임 종료 상태
            'last_validation_result': last_validation_result,  # 마지막 검증 결과
            'history': history
        }
        
    except Exception as e:
        st.error(f"검증 중 오류 발생: {str(e)}")
        return None
    finally:
        conn.close()

def batch_validate_with_first_step_skip_analysis(
    cutoff_grid_string_id,
    window_size=7,
    method="빈도 기반",
    use_threshold=True,
    threshold=60,
    max_interval=6,
    reverse_forced_prediction=False,
    confidence_skip_threshold=51
):
    """
    첫 스텝 스킵 분석을 위한 신뢰도 기반 스킵 규칙 배치 검증 (독립)
    
    Args:
        cutoff_grid_string_id: 기준 grid_string ID
        window_size: 윈도우 크기
        method: 예측 방법
        use_threshold: 임계값 전략 사용 여부
        threshold: 임계값
        max_interval: 최대 예측 없음 간격
        reverse_forced_prediction: 반대 선택 전략 사용 여부
        confidence_skip_threshold: 스킵할 신뢰도 임계값
    
    Returns:
        dict: 배치 검증 결과 (first_step_skipped, game_end_status 포함)
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
            result = validate_interactive_multi_step_scenario_with_confidence_skip_first_step_analysis(
                grid_string_id,
                cutoff_grid_string_id,
                window_size=window_size,
                method=method,
                use_threshold=use_threshold,
                threshold=threshold,
                max_interval=max_interval,
                reverse_forced_prediction=reverse_forced_prediction,
                confidence_skip_threshold=confidence_skip_threshold
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

def save_first_step_skip_analysis_results(
    cutoff_grid_string_id,
    window_size,
    method,
    use_threshold,
    threshold,
    max_interval,
    confidence_skip_threshold,
    batch_results,
    grid_string_ids=None
):
    """
    첫 스텝 스킵 분석 결과를 DB에 저장 (독립)
    
    Args:
        cutoff_grid_string_id: 기준 grid_string ID
        window_size: 윈도우 크기
        method: 예측 방법
        use_threshold: 임계값 전략 사용 여부
        threshold: 임계값
        max_interval: 최대 예측 없음 간격
        confidence_skip_threshold: 스킵 신뢰도 임계값
        batch_results: 배치 검증 결과
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
            INSERT INTO first_step_skip_analysis_sessions (
                validation_id, cutoff_grid_string_id, window_size, method,
                use_threshold, threshold, max_interval,
                confidence_skip_threshold, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
        ''', (
            validation_id,
            cutoff_grid_string_id,
            window_size,
            method,
            use_threshold,
            threshold if use_threshold else None,
            max_interval,
            confidence_skip_threshold
        ))
        
        # 2. Grid String별 결과 저장
        if batch_results and 'results' in batch_results:
            for result in batch_results['results']:
                cursor.execute('''
                    INSERT OR REPLACE INTO first_step_skip_analysis_results (
                        validation_id, confidence_skip_threshold, grid_string_id,
                        max_consecutive_failures, total_steps, total_failures,
                        total_predictions, total_skipped_predictions,
                        accuracy, forced_prediction_rate, forced_success_rate,
                        first_success_step, first_step_skipped, game_end_status,
                        last_validation_result, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
                ''', (
                    validation_id,
                    confidence_skip_threshold,
                    result.get('grid_string_id'),
                    result.get('max_consecutive_failures', 0),
                    result.get('total_steps', 0),
                    result.get('total_failures', 0),
                    result.get('total_predictions', 0),
                    result.get('total_skipped_predictions', 0),
                    result.get('accuracy', 0.0),
                    result.get('forced_prediction_rate', 0.0),
                    result.get('forced_success_rate', 0.0),
                    result.get('first_success_step'),
                    1 if result.get('first_step_skipped', False) else 0,
                    result.get('game_end_status', 'other'),
                    result.get('last_validation_result')
                ))
        
        # 3. 예측값 테이블 스냅샷 저장 (검증 시점에 실시간 계산)
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
        
        # 4. Grid String ID 리스트 저장
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

def analyze_first_step_skip_correlation_from_validation(validation_id, confidence_skip_threshold):
    """
    기존 검증 데이터에서 첫 스텝 스킵과 승률의 상관관계 분석
    (confidence_skip_validation_steps와 confidence_skip_validation_grid_results 사용)
    
    Args:
        validation_id: 분석할 validation_id
        confidence_skip_threshold: 신뢰도 스킵 임계값
    
    Returns:
        dict: 분석 결과
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # 1. 각 grid_string_id별로 첫 번째 예측 가능한 스텝(has_prediction=1)에서 skipped=1인지 확인
        #    그리고 첫 번째 실제 검증된 예측 스텝(has_prediction=1 AND skipped=0 AND validated=1)에서 is_correct 확인
        first_step_query = '''
            WITH first_prediction_steps AS (
                SELECT 
                    grid_string_id,
                    MIN(CASE WHEN has_prediction = 1 THEN step END) as first_prediction_step
                FROM confidence_skip_validation_steps
                WHERE validation_id = ? AND confidence_skip_threshold = ?
                GROUP BY grid_string_id
            ),
            first_validated_steps AS (
                SELECT 
                    grid_string_id,
                    MIN(CASE WHEN has_prediction = 1 AND skipped = 0 AND validated = 1 THEN step END) as first_validated_step
                FROM confidence_skip_validation_steps
                WHERE validation_id = ? AND confidence_skip_threshold = ?
                GROUP BY grid_string_id
            )
            SELECT 
                s.grid_string_id,
                CASE WHEN s.skipped = 1 THEN 1 ELSE 0 END as first_step_skipped,
                CASE 
                    WHEN v.first_validated_step IS NOT NULL THEN
                        (SELECT CASE WHEN is_correct = 1 THEN 1 ELSE 0 END
                         FROM confidence_skip_validation_steps
                         WHERE validation_id = ? AND confidence_skip_threshold = ?
                           AND grid_string_id = s.grid_string_id
                           AND step = v.first_validated_step)
                    WHEN s.is_correct IS NOT NULL THEN
                        CASE WHEN s.is_correct = 1 THEN 1 ELSE 0 END
                    ELSE 0
                END as first_prediction_match
            FROM confidence_skip_validation_steps s
            INNER JOIN first_prediction_steps f ON 
                s.grid_string_id = f.grid_string_id AND 
                s.step = f.first_prediction_step
            LEFT JOIN first_validated_steps v ON
                s.grid_string_id = v.grid_string_id
            WHERE s.validation_id = ? AND s.confidence_skip_threshold = ?
        '''
        first_step_df = pd.read_sql_query(
            first_step_query, 
            conn, 
            params=[validation_id, confidence_skip_threshold, validation_id, confidence_skip_threshold, validation_id, confidence_skip_threshold, validation_id, confidence_skip_threshold]
        )
        
        if len(first_step_df) == 0:
            return {
                'total_complete_games': 0,
                'skip_start_count': 0,
                'non_skip_start_count': 0,
                'skip_start_avg_accuracy': None,
                'non_skip_start_avg_accuracy': None,
                'skip_start_outlier_rate': None,
                'non_skip_start_outlier_rate': None,
                'skip_start_outlier_count': 0,
                'non_skip_start_outlier_count': 0,
                'skip_start_first_match_count': 0,
                'skip_start_first_mismatch_count': 0,
                'non_skip_start_first_match_count': 0,
                'non_skip_start_first_mismatch_count': 0,
                'skip_start_first_match_avg_accuracy': None,
                'skip_start_first_mismatch_avg_accuracy': None,
                'non_skip_start_first_match_avg_accuracy': None,
                'non_skip_start_first_mismatch_avg_accuracy': None
            }
        
        # 2. Grid String별 결과와 조인
        # 완전한 게임만 필터링 (max_consecutive_failures >= 6 또는 정상 종료)
        grid_results_query = '''
            SELECT 
                grid_string_id,
                accuracy,
                max_consecutive_failures
            FROM confidence_skip_validation_grid_results
            WHERE validation_id = ? AND confidence_skip_threshold = ?
        '''
        grid_results_df = pd.read_sql_query(
            grid_results_query, 
            conn, 
            params=[validation_id, confidence_skip_threshold]
        )
        
        if len(grid_results_df) == 0:
            return {
                'total_complete_games': 0,
                'skip_start_count': 0,
                'non_skip_start_count': 0,
                'skip_start_avg_accuracy': None,
                'non_skip_start_avg_accuracy': None,
                'skip_start_outlier_rate': None,
                'non_skip_start_outlier_rate': None,
                'skip_start_outlier_count': 0,
                'non_skip_start_outlier_count': 0,
                'skip_start_first_match_count': 0,
                'skip_start_first_mismatch_count': 0,
                'non_skip_start_first_match_count': 0,
                'non_skip_start_first_mismatch_count': 0,
                'skip_start_first_match_avg_accuracy': None,
                'skip_start_first_mismatch_avg_accuracy': None,
                'non_skip_start_first_match_avg_accuracy': None,
                'non_skip_start_first_mismatch_avg_accuracy': None
            }
        
        # 3. 조인하여 분석 데이터 생성
        df = first_step_df.merge(grid_results_df, on='grid_string_id', how='inner')
        
        # 완전한 게임만 필터링 (max_consecutive_failures >= 6 또는 정상 종료)
        # max_consecutive_failures >= 6이면 mismatch_6plus, 그 외는 match_end로 간주
        df['game_end_status'] = df['max_consecutive_failures'].apply(
            lambda x: 'mismatch_6plus' if x >= 6 else 'match_end'
        )
        df = df[df['game_end_status'].isin(['match_end', 'mismatch_6plus'])]
        
        if len(df) == 0:
            return {
                'total_complete_games': 0,
                'skip_start_count': 0,
                'non_skip_start_count': 0,
                'skip_start_avg_accuracy': None,
                'non_skip_start_avg_accuracy': None,
                'skip_start_outlier_rate': None,
                'non_skip_start_outlier_rate': None,
                'skip_start_outlier_count': 0,
                'non_skip_start_outlier_count': 0,
                'skip_start_first_match_count': 0,
                'skip_start_first_mismatch_count': 0,
                'non_skip_start_first_match_count': 0,
                'non_skip_start_first_mismatch_count': 0,
                'skip_start_first_match_avg_accuracy': None,
                'skip_start_first_mismatch_avg_accuracy': None,
                'non_skip_start_first_match_avg_accuracy': None,
                'non_skip_start_first_mismatch_avg_accuracy': None
            }
        
        # 스킵으로 시작한 게임과 그렇지 않은 게임 분리
        skip_start = df[df['first_step_skipped'] == 1].copy()
        non_skip_start = df[df['first_step_skipped'] == 0].copy()
        
        # 이상치 (불일치 6개 이상) 발생 비율
        skip_start_outliers = skip_start[skip_start['max_consecutive_failures'] >= 6]
        non_skip_start_outliers = non_skip_start[non_skip_start['max_consecutive_failures'] >= 6]
        
        # 통계 계산
        total_complete_games = len(df)
        skip_start_count = len(skip_start)
        non_skip_start_count = len(non_skip_start)
        
        skip_start_avg_accuracy = skip_start['accuracy'].mean() if len(skip_start) > 0 else None
        non_skip_start_avg_accuracy = non_skip_start['accuracy'].mean() if len(non_skip_start) > 0 else None
        
        skip_start_outlier_count = len(skip_start_outliers)
        non_skip_start_outlier_count = len(non_skip_start_outliers)
        
        skip_start_outlier_rate = (skip_start_outlier_count / skip_start_count * 100) if skip_start_count > 0 else None
        non_skip_start_outlier_rate = (non_skip_start_outlier_count / non_skip_start_count * 100) if non_skip_start_count > 0 else None
        
        # 첫 예측 일치/불일치 통계
        skip_start_first_match = skip_start[skip_start['first_prediction_match'] == 1]
        skip_start_first_mismatch = skip_start[skip_start['first_prediction_match'] == 0]
        non_skip_start_first_match = non_skip_start[non_skip_start['first_prediction_match'] == 1]
        non_skip_start_first_mismatch = non_skip_start[non_skip_start['first_prediction_match'] == 0]
        
        skip_start_first_match_count = len(skip_start_first_match)
        skip_start_first_mismatch_count = len(skip_start_first_mismatch)
        non_skip_start_first_match_count = len(non_skip_start_first_match)
        non_skip_start_first_mismatch_count = len(non_skip_start_first_mismatch)
        
        skip_start_first_match_avg_accuracy = skip_start_first_match['accuracy'].mean() if len(skip_start_first_match) > 0 else None
        skip_start_first_mismatch_avg_accuracy = skip_start_first_mismatch['accuracy'].mean() if len(skip_start_first_mismatch) > 0 else None
        non_skip_start_first_match_avg_accuracy = non_skip_start_first_match['accuracy'].mean() if len(non_skip_start_first_match) > 0 else None
        non_skip_start_first_mismatch_avg_accuracy = non_skip_start_first_mismatch['accuracy'].mean() if len(non_skip_start_first_mismatch) > 0 else None
        
        # DataFrame에 첫 예측 일치/불일치 정보 추가 (표시용)
        skip_start_display = skip_start.copy()
        skip_start_display['첫 예측 결과'] = skip_start_display['first_prediction_match'].apply(lambda x: '일치' if x == 1 else '불일치')
        
        non_skip_start_display = non_skip_start.copy()
        non_skip_start_display['첫 예측 결과'] = non_skip_start_display['first_prediction_match'].apply(lambda x: '일치' if x == 1 else '불일치')
        
        return {
            'total_complete_games': total_complete_games,
            'skip_start_count': skip_start_count,
            'non_skip_start_count': non_skip_start_count,
            'skip_start_avg_accuracy': skip_start_avg_accuracy,
            'non_skip_start_avg_accuracy': non_skip_start_avg_accuracy,
            'skip_start_outlier_rate': skip_start_outlier_rate,
            'non_skip_start_outlier_rate': non_skip_start_outlier_rate,
            'skip_start_outlier_count': skip_start_outlier_count,
            'non_skip_start_outlier_count': non_skip_start_outlier_count,
            'skip_start_first_match_count': skip_start_first_match_count,
            'skip_start_first_mismatch_count': skip_start_first_mismatch_count,
            'non_skip_start_first_match_count': non_skip_start_first_match_count,
            'non_skip_start_first_mismatch_count': non_skip_start_first_mismatch_count,
            'skip_start_first_match_avg_accuracy': skip_start_first_match_avg_accuracy,
            'skip_start_first_mismatch_avg_accuracy': skip_start_first_mismatch_avg_accuracy,
            'non_skip_start_first_match_avg_accuracy': non_skip_start_first_match_avg_accuracy,
            'non_skip_start_first_mismatch_avg_accuracy': non_skip_start_first_mismatch_avg_accuracy,
            'skip_start_df': skip_start_display,
            'non_skip_start_df': non_skip_start_display
        }
        
    except Exception as e:
        st.error(f"분석 중 오류 발생: {str(e)}")
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
        return None
    finally:
        conn.close()

def analyze_first_step_skip_correlation(validation_id):
    """
    첫 스텝 스킵과 승률의 상관관계 분석 (기존 함수 - first_step_skip_analysis_results 사용)
    
    Args:
        validation_id: 분석할 validation_id
    
    Returns:
        dict: 분석 결과
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # 완전한 게임만 필터링 (game_end_status IN ('match_end', 'mismatch_6plus'))
        query = '''
            SELECT 
                first_step_skipped,
                accuracy,
                max_consecutive_failures,
                game_end_status
            FROM first_step_skip_analysis_results
            WHERE validation_id = ?
              AND game_end_status IN ('match_end', 'mismatch_6plus')
        '''
        df = pd.read_sql_query(query, conn, params=[validation_id])
        
        if len(df) == 0:
            return {
                'total_complete_games': 0,
                'skip_start_count': 0,
                'non_skip_start_count': 0,
                'skip_start_avg_accuracy': None,
                'non_skip_start_avg_accuracy': None,
                'skip_start_outlier_rate': None,
                'non_skip_start_outlier_rate': None,
                'skip_start_outlier_count': 0,
                'non_skip_start_outlier_count': 0
            }
        
        # 스킵으로 시작한 게임과 그렇지 않은 게임 분리
        skip_start = df[df['first_step_skipped'] == 1]
        non_skip_start = df[df['first_step_skipped'] == 0]
        
        # 이상치 (불일치 6개 이상) 발생 비율
        skip_start_outliers = skip_start[skip_start['max_consecutive_failures'] >= 6]
        non_skip_start_outliers = non_skip_start[non_skip_start['max_consecutive_failures'] >= 6]
        
        # 통계 계산
        total_complete_games = len(df)
        skip_start_count = len(skip_start)
        non_skip_start_count = len(non_skip_start)
        
        skip_start_avg_accuracy = skip_start['accuracy'].mean() if len(skip_start) > 0 else None
        non_skip_start_avg_accuracy = non_skip_start['accuracy'].mean() if len(non_skip_start) > 0 else None
        
        skip_start_outlier_count = len(skip_start_outliers)
        non_skip_start_outlier_count = len(non_skip_start_outliers)
        
        skip_start_outlier_rate = (skip_start_outlier_count / skip_start_count * 100) if skip_start_count > 0 else None
        non_skip_start_outlier_rate = (non_skip_start_outlier_count / non_skip_start_count * 100) if non_skip_start_count > 0 else None
        
        return {
            'total_complete_games': total_complete_games,
            'skip_start_count': skip_start_count,
            'non_skip_start_count': non_skip_start_count,
            'skip_start_avg_accuracy': skip_start_avg_accuracy,
            'non_skip_start_avg_accuracy': non_skip_start_avg_accuracy,
            'skip_start_outlier_rate': skip_start_outlier_rate,
            'non_skip_start_outlier_rate': non_skip_start_outlier_rate,
            'skip_start_outlier_count': skip_start_outlier_count,
            'non_skip_start_outlier_count': non_skip_start_outlier_count,
            'skip_start_df': skip_start,
            'non_skip_start_df': non_skip_start
        }
        
    except Exception as e:
        st.error(f"분석 중 오류 발생: {str(e)}")
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
        return None
    finally:
        conn.close()

def main():
    st.title("📊 신뢰도 스킵 전략 가설 검증 분석")
    st.markdown("""
    **가설**: 첫 번째 스킵되지 않은 예측이 일치로 시작하는 grid_string은
    다음 게임부터 최대 연속 불일치가 6개 미만일 것이다.
    """)
    
    # 검증 세션 선택
    st.markdown("---")
    st.markdown("### 검증 세션 선택")
    
    sessions_df = load_validation_sessions()
    
    if len(sessions_df) == 0:
        st.warning("⚠️ 저장된 검증 세션이 없습니다. 먼저 검증을 실행하고 결과를 저장해주세요.")
        return
    
    # 세션 선택 UI
    session_options = []
    for _, row in sessions_df.iterrows():
        display_text = (
            f"ID: {row['validation_id'][:8]}... | "
            f"임계값: {row['confidence_skip_threshold_1']:.1f}% / {row['confidence_skip_threshold_2']:.1f}% | "
            f"윈도우: {row['window_size']} | "
            f"생성일: {row['created_at']}"
        )
        session_options.append((row['validation_id'], display_text))
    
    selected_session_id = st.selectbox(
        "검증 세션 선택",
        options=[opt[0] for opt in session_options],
        format_func=lambda x: next((opt[1] for opt in session_options if opt[0] == x), x),
        key="selected_validation_session"
    )
    
    if selected_session_id:
        selected_session = sessions_df[sessions_df['validation_id'] == selected_session_id].iloc[0]
        
        # 세션 정보 표시
        st.markdown("---")
        st.markdown("### 검증 세션 정보")
        col_info1, col_info2, col_info3, col_info4 = st.columns(4)
        with col_info1:
            st.metric("윈도우 크기", selected_session['window_size'])
        with col_info2:
            st.metric("예측 방법", selected_session['method'])
        with col_info3:
            st.metric("임계값 1", f"{selected_session['confidence_skip_threshold_1']:.1f}%")
        with col_info4:
            st.metric("임계값 2", f"{selected_session['confidence_skip_threshold_2']:.1f}%")
        
        # 임계값 선택
        st.markdown("---")
        st.markdown("### 분석할 임계값 선택")
        threshold_option = st.radio(
            "분석할 신뢰도 스킵 임계값",
            options=[selected_session['confidence_skip_threshold_1'], selected_session['confidence_skip_threshold_2']],
            format_func=lambda x: f"{x:.1f}%",
            key="validation_threshold_radio"
        )
        
        # 분석 실행
        if st.button("분석 실행", type="primary", use_container_width=True):
            st.session_state.analysis_results = analyze_first_match_hypothesis(
                selected_session_id,
                threshold_option
            )
            st.session_state.selected_validation_id = selected_session_id
            st.session_state.selected_validation_threshold = threshold_option
            st.rerun()
        
        # 분석 결과 표시
        if 'analysis_results' in st.session_state and st.session_state.get('selected_validation_id') == selected_session_id and st.session_state.get('selected_validation_threshold') == threshold_option:
            results = st.session_state.analysis_results
            
            st.markdown("---")
            st.markdown("### 가설 검증 결과")
            
            # 핵심 지표 강조
            col_key1, col_key2, col_key3 = st.columns(3)
            with col_key1:
                st.metric(
                    "🔥 6개 이상 연속 불일치", 
                    f"{results.get('cases_6_or_more_total', 0)}개",
                    help="두 번째가 불일치로 시작하여 연속 불일치가 6개 이상인 케이스"
                )
            with col_key2:
                st.metric(
                    "두 번째가 불일치로 시작", 
                    f"{results.get('grid_strings_with_second_mismatch', 0)}개"
                )
            with col_key3:
                st.metric(
                    "완전한 데이터 중 6개 이상", 
                    f"{results.get('cases_6_or_more_complete', 0)}개"
                )
            
            st.markdown("---")
            
            # 일반 통계
            col_stat1, col_stat2, col_stat3, col_stat4 = st.columns(4)
            with col_stat1:
                st.metric("첫 일치 Grid String 수", f"{results['grid_strings_with_first_match']}")
            with col_stat2:
                st.metric("6개 미만 비율", f"{results['below_6_ratio']:.2f}%")
            with col_stat3:
                st.metric("평균 최대 연속 불일치", f"{results['avg_max_consecutive_mismatches']:.2f}")
            with col_stat4:
                st.metric("최대 연속 불일치", f"{results['max_consecutive_mismatches']}")
            
            # 가설 검증 결과
            st.markdown("---")
            st.markdown("### 가설 검증 요약")
            
            if results['grid_strings_with_first_match'] > 0:
                below_6_count = results['grid_strings_below_6']
                total_count = results['grid_strings_with_first_match']
                ratio = results['below_6_ratio']
                
                if ratio >= 50:
                    st.success(f"✅ 가설 지지: {below_6_count}/{total_count} ({ratio:.2f}%)의 grid_string이 6개 미만입니다.")
                else:
                    st.warning(f"⚠️ 가설 반박: {below_6_count}/{total_count} ({ratio:.2f}%)의 grid_string만 6개 미만입니다.")
                
                # 상세 통계
                col_detail1, col_detail2 = st.columns(2)
                with col_detail1:
                    st.metric("6개 미만", f"{below_6_count}개")
                with col_detail2:
                    st.metric("6개 이상 (완전한 데이터)", f"{results.get('cases_6_or_more_complete', 0)}개")
                
                # 히스토그램 (두 번째가 불일치인 케이스만)
                st.markdown("---")
                st.markdown("### 연속 불일치 분포 (두 번째가 불일치로 시작한 케이스)")
                
                # 두 번째가 불일치인 케이스만 필터링하고 None 제외
                max_mismatches_list = [
                    r['max_consecutive_mismatches_after_first'] 
                    for r in results['results'] 
                    if r.get('has_first_match') 
                    and r.get('second_is_mismatch') 
                    and r.get('max_consecutive_mismatches_after_first') is not None
                ]
                
                if len(max_mismatches_list) > 0:
                    bins = defaultdict(int)
                    for value in max_mismatches_list:
                        if value < 6:
                            bins['0-5'] += 1
                        elif value < 10:
                            bins['6-9'] += 1
                        elif value < 15:
                            bins['10-14'] += 1
                        else:
                            bins['15+'] += 1
                    
                    # 히스토그램 표시
                    max_count = max(bins.values()) if bins else 1
                    total_count = len(max_mismatches_list)
                    
                    for bin_range in ['0-5', '6-9', '10-14', '15+']:
                        count = bins.get(bin_range, 0)
                        ratio = (count / total_count * 100) if total_count > 0 else 0
                        bar_length = int((count / max_count) * 50) if max_count > 0 else 0
                        bar = '█' * bar_length
                        st.text(f"{bin_range:>8}: {bar} {count:>4}개 ({ratio:>5.2f}%)")
                else:
                    st.info("히스토그램 데이터가 없습니다.")
            
            # 불완전한 데이터 알림
            if results.get('incomplete_data_count', 0) > 0:
                st.info(f"ℹ️ {results['incomplete_data_count']}개의 grid_string은 불일치 상태로 종료되어 통계에서 제외되었습니다. (전체 데이터 테이블에서 확인 가능)")
            
            # 6개 이상 케이스 강조 표시
            if results.get('cases_6_or_more_total', 0) > 0:
                st.markdown("---")
                st.warning(f"⚠️ **중요**: {results['cases_6_or_more_total']}개의 grid_string에서 두 번째가 불일치로 시작하여 연속 불일치가 6개 이상 발생했습니다!")
                
                # 6개 이상 케이스 상세 정보
                st.markdown("#### 🔥 6개 이상 연속 불일치 케이스")
                cases_6_or_more = [r for r in results['results'] 
                                   if r.get('has_first_match') and r.get('second_is_mismatch') and r.get('is_6_or_more')]
                
                if cases_6_or_more:
                    critical_data = []
                    for r in cases_6_or_more:
                        critical_data.append({
                            'Grid String ID': r['grid_string_id'],
                            '첫 일치 Step': r['first_match_step'],
                            '두 번째 불일치 Step': r.get('second_mismatch_step'),
                            '연속 불일치 개수': r['max_consecutive_mismatches_after_first'],
                            '완전 여부': "✅ 완료" if r.get('has_complete_data') else "⚠️ 불완전",
                            '상태': "❌ 가설 반박" if r.get('is_6_or_more') else "✅ 가설 지지"
                        })
                    
                    critical_df = pd.DataFrame(critical_data)
                    st.dataframe(critical_df, use_container_width=True, hide_index=True)
            else:
                st.info("💡 첫 일치로 시작하는 grid_string이 없습니다.")
            
            # Grid String별 상세 결과
            st.markdown("---")
            st.markdown("### Grid String별 상세 결과")
            
            results_df_data = []
            for r in results['results']:
                if r.get('has_first_match'):
                    if r.get('second_is_mismatch'):
                        max_mismatches = r['max_consecutive_mismatches_after_first']
                        below_6_status = '✅ 예' if r.get('is_below_6') else '❌ 아니오'
                        status = "✅ 완료" if r.get('has_complete_data') else "⚠️ 불완전"
                        is_critical = "🔥" if r.get('is_6_or_more') else ""
                        results_df_data.append({
                            'Grid String ID': r['grid_string_id'],
                            '첫 일치 Step': r['first_match_step'],
                            '두 번째 불일치 Step': r.get('second_mismatch_step'),
                            '최대 연속 불일치': max_mismatches,
                            '6개 미만': below_6_status,
                            '상태': f"{is_critical} {status}"
                        })
                    else:
                        results_df_data.append({
                            'Grid String ID': r['grid_string_id'],
                            '첫 일치 Step': r['first_match_step'],
                            '두 번째 불일치 Step': '-',
                            '최대 연속 불일치': '-',
                            '6개 미만': '-',
                            '상태': '- (두 번째가 일치)'
                        })
                else:
                    results_df_data.append({
                        'Grid String ID': r['grid_string_id'],
                        '첫 일치 Step': '-',
                        '두 번째 불일치 Step': '-',
                        '최대 연속 불일치': '-',
                        '6개 미만': '-',
                        '상태': '-'
                    })
            
            if len(results_df_data) > 0:
                results_df = pd.DataFrame(results_df_data)
                st.dataframe(results_df, use_container_width=True, hide_index=True)
                
                # 상세 히스토리 조회 (모든 Grid String에 대해)
                st.markdown("---")
                st.markdown("#### 상세 히스토리 조회")
                
                # 첫 일치가 있는 Grid String만 선택 목록에 포함
                first_match_grid_ids = [r['grid_string_id'] for r in results['results'] if r['has_first_match']]
                
                if len(first_match_grid_ids) > 0:
                    selected_grid_id = st.selectbox(
                        "Grid String 선택 (상세 히스토리 보기)",
                        options=[None] + first_match_grid_ids,
                        format_func=lambda x: "선택 안함" if x is None else f"ID {x}",
                        key="selected_grid_id_for_history"
                    )
                    
                    if selected_grid_id:
                        steps_df = load_validation_session_steps(selected_session_id, threshold_option)
                        grid_steps = steps_df[steps_df['grid_string_id'] == selected_grid_id].copy()
                        grid_steps = grid_steps.sort_values('step').reset_index(drop=True)
                        
                        # 선택된 Grid String의 결과 찾기
                        selected_result = next((r for r in results['results'] if r['grid_string_id'] == selected_grid_id), None)
                        first_match_step = selected_result['first_match_step'] if selected_result and selected_result.get('has_first_match') else None
                        second_mismatch_step = selected_result.get('second_mismatch_step') if selected_result and selected_result.get('second_is_mismatch') else None
                        max_consecutive_mismatches = selected_result['max_consecutive_mismatches_after_first'] if selected_result and selected_result.get('has_first_match') and selected_result.get('second_is_mismatch') else None
                        
                        st.markdown(f"**Grid String ID: {selected_grid_id}**")
                        if first_match_step:
                            st.markdown(f"- 첫 일치 스텝: {first_match_step}")
                        if second_mismatch_step:
                            st.markdown(f"- 두 번째 불일치 스텝: {second_mismatch_step}")
                        if max_consecutive_mismatches is not None:
                            st.markdown(f"- 최대 연속 불일치: {max_consecutive_mismatches}개")
                            if selected_result.get('is_6_or_more'):
                                st.error(f"🔥 **중요**: 연속 불일치가 {max_consecutive_mismatches}개로 6개 이상입니다!")
                        if selected_result and selected_result.get('has_complete_data') is False:
                            st.warning("⚠️ 이 데이터는 불일치 상태로 종료되어 불완전합니다.")
                        
                        # 전체 히스토리 테이블
                        history_data = []
                        for _, row in grid_steps.iterrows():
                            is_correct = row['is_correct']
                            match_status = '✅' if is_correct == 1 else ('❌' if is_correct == 0 else '-')
                            is_forced = '⚡' if row['is_forced'] == 1 else ''
                            skipped = '⏭️' if row['skipped'] == 1 else ''
                            
                            # 첫 일치 및 두 번째 불일치 스텝 하이라이트
                            highlight = ''
                            if first_match_step and row['step'] == first_match_step:
                                highlight = ' 🔵 (첫 일치)'
                            # 두 번째 불일치 정보 추가
                            selected_result = next((r for r in results['results'] if r.get('grid_string_id') == selected_grid_id), None)
                            second_mismatch_step = selected_result.get('second_mismatch_step') if selected_result and selected_result.get('second_is_mismatch') else None
                            if second_mismatch_step and row['step'] == second_mismatch_step:
                                highlight = ' 🔴 (두 번째 불일치 시작)'
                            if first_match_step and second_mismatch_step and row['step'] == first_match_step:
                                highlight = ' 🔵 (첫 일치)'
                            
                            has_prediction = (row['has_prediction'] == 1 or row['has_prediction'] is True)
                            
                            history_data.append({
                                'Step': row['step'],
                                'Prefix': row['prefix'],
                                '예측': f"{row['predicted'] or '-'}{is_forced}{skipped}",
                                '실제값': row['actual'],
                                '일치': match_status,
                                '신뢰도': f"{row['confidence']:.1f}%" if has_prediction else '-',
                                '간격': row['current_interval'] if not has_prediction else 0,
                                '검증': '✓' if row['validated'] == 1 else '',
                                '스킵': '⏭️' if row['skipped'] == 1 else '',
                                '비고': highlight
                            })
                        
                        history_df = pd.DataFrame(history_data)
                        st.dataframe(history_df, use_container_width=True, hide_index=True)
                else:
                    st.info("💡 상세 히스토리를 조회할 Grid String이 없습니다.")
                
                # 예외 케이스 (6개 이상)
                st.markdown("---")
                st.markdown("### 예외 케이스 (6개 이상 연속 불일치)")
                
                exception_cases = [r for r in results['results'] 
                                  if r.get('has_first_match') and r.get('second_is_mismatch') and r.get('is_6_or_more')]
                
                if len(exception_cases) > 0:
                    exception_df_data = []
                    for r in exception_cases:
                        exception_df_data.append({
                            'Grid String ID': r['grid_string_id'],
                            '첫 일치 Step': r['first_match_step'],
                            '두 번째 불일치 Step': r.get('second_mismatch_step'),
                            '최대 연속 불일치': r['max_consecutive_mismatches_after_first'],
                            '완전 여부': "✅ 완료" if r.get('has_complete_data') else "⚠️ 불완전"
                        })
                    
                    exception_df = pd.DataFrame(exception_df_data)
                    st.dataframe(exception_df, use_container_width=True, hide_index=True)
                else:
                    st.success("✅ 예외 케이스가 없습니다. 모든 grid_string이 6개 미만입니다.")
            else:
                st.info("💡 첫 일치로 시작하는 grid_string이 없습니다.")
    
    # 라이브 게임 데이터 분석 섹션
    st.markdown("---")
    st.markdown("---")
    st.header("🎮 라이브 게임 데이터 분석")
    st.markdown("""
    **가설**: 첫 번째 스킵되지 않은 예측이 일치로 시작하는 경우
    다음 게임부터 최대 연속 불일치가 6개 미만일 것이다.
    
    **분석 방식**: 모든 라이브 게임 세션의 전체 히스토리를 하나로 합쳐서 분석합니다.
    """)
    
    # 전체 분석 실행
    if st.button("전체 라이브 게임 히스토리 분석 실행", type="primary", use_container_width=True, key="analyze_all_live_games"):
        with st.spinner("분석 중..."):
            st.session_state.all_live_game_analysis_result = analyze_all_live_games_first_match_hypothesis()
        st.rerun()
    
    # 분석 결과 표시
    if 'all_live_game_analysis_result' in st.session_state:
        result = st.session_state.all_live_game_analysis_result
        
        st.markdown("---")
        st.markdown("### 가설 검증 결과")
        
        if result.get('has_first_match'):
            # 핵심 지표 강조
            col_key1, col_key2, col_key3 = st.columns(3)
            with col_key1:
                is_6_or_more = result.get('is_6_or_more', False)
                display_value = f"{result.get('max_consecutive_mismatches_after_first', 0)}개"
                st.metric(
                    "🔥 최대 연속 불일치", 
                    display_value,
                    delta="6개 이상" if is_6_or_more else None,
                    delta_color="inverse" if is_6_or_more else "normal",
                    help="두 번째가 불일치로 시작하여 연속 불일치 개수"
                )
            with col_key2:
                second_mismatch = "✅ 예" if result.get('second_is_mismatch') else "❌ 아니오"
                st.metric(
                    "두 번째가 불일치로 시작", 
                    second_mismatch
                )
            with col_key3:
                complete_status = "✅ 완료" if result.get('has_complete_data') else "⚠️ 불완전"
                st.metric(
                    "데이터 완전 여부", 
                    complete_status
                )
            
            st.markdown("---")
            
            # 일반 통계
            col_stat1, col_stat2 = st.columns(2)
            with col_stat1:
                st.metric("총 스텝 수", f"{result['total_steps']}")
            with col_stat2:
                st.metric("6개 미만 여부", "✅ 예" if result.get('is_below_6') else "❌ 아니오")
            
            # 가설 검증 결과
            st.markdown("---")
            st.markdown("### 가설 검증 요약")
            
            max_mismatches = result.get('max_consecutive_mismatches_after_first', 0)
            if result.get('second_is_mismatch'):
                if result.get('is_below_6'):
                    st.success(f"✅ 가설 지지: 두 번째가 불일치로 시작하여 최대 연속 불일치가 {max_mismatches}개로 6개 미만입니다.")
                elif result.get('is_6_or_more'):
                    st.error(f"❌ 가설 반박: 두 번째가 불일치로 시작하여 최대 연속 불일치가 {max_mismatches}개로 6개 이상입니다.")
                else:
                    st.warning(f"⚠️ 가설 반박: 최대 연속 불일치가 {max_mismatches}개로 6개 이상입니다.")
            else:
                st.info("ℹ️ 두 번째가 일치로 시작하여 분석 대상이 아닙니다.")
            
            # 첫 일치 및 두 번째 불일치 정보
            st.markdown("---")
            st.markdown("### 첫 일치 및 두 번째 불일치 정보")
            col_match1, col_match2 = st.columns(2)
            with col_match1:
                st.markdown("#### 첫 일치")
                st.metric("첫 일치 인덱스", f"{result.get('first_match_idx', '-')}")
                st.metric("첫 일치 스텝", f"{result.get('first_match_step', '-')}")
                st.metric("첫 일치 세션 ID", f"{result.get('first_match_session_id', '-')}")
            with col_match2:
                st.markdown("#### 두 번째 불일치")
                if result.get('second_is_mismatch'):
                    st.metric("두 번째 불일치 인덱스", f"{result.get('second_mismatch_idx', '-')}")
                    st.metric("두 번째 불일치 스텝", f"{result.get('second_mismatch_step', '-')}")
                    st.metric("두 번째 불일치 세션 ID", f"{result.get('second_mismatch_session_id', '-')}")
                else:
                    st.info("두 번째가 불일치가 아닙니다.")
            
            # 전체 히스토리 표시
            st.markdown("---")
            st.markdown("### 전체 히스토리")
            
            if len(result['all_steps']) > 0:
                first_match_idx = result['first_match_idx']
                
                history_data = []
                for idx, entry in enumerate(result['all_steps']):
                    is_correct = entry.get('is_correct')
                    match_status = '✅' if (is_correct == 1 or is_correct is True) else ('❌' if (is_correct == 0 or is_correct is False) else '-')
                    is_forced = '⚡' if (entry.get('is_forced') == 1 or entry.get('is_forced') is True) else ''
                    skipped_val = entry.get('skipped')
                    skipped = '⏭️' if (skipped_val == 1 or skipped_val is True) else ''
                    
                    # 첫 일치 및 두 번째 불일치 하이라이트
                    highlight = ''
                    if idx == first_match_idx:
                        highlight = ' 🔵 (첫 일치)'
                    second_mismatch_idx = result.get('second_mismatch_idx')
                    if second_mismatch_idx is not None and idx == second_mismatch_idx:
                        highlight = ' 🔴 (두 번째 불일치 시작)'
                    if idx == first_match_idx and second_mismatch_idx is not None and idx == second_mismatch_idx:
                        highlight = ' 🔵 (첫 일치)'
                    
                    has_prediction_val = entry.get('has_prediction')
                    has_prediction = (has_prediction_val == 1 or has_prediction_val is True)
                    
                    history_data.append({
                        '인덱스': idx,
                        '세션 ID': entry.get('session_id'),
                        '스텝': entry.get('step'),
                        'Prefix': entry.get('prefix', ''),
                        '예측': f"{entry.get('predicted_value') or '-'}{is_forced}{skipped}",
                        '실제값': entry.get('actual_value', ''),
                        '일치': match_status,
                        '신뢰도': f"{entry.get('confidence', 0):.1f}%" if has_prediction else '-',
                        '비고': highlight
                    })
                
                history_df = pd.DataFrame(history_data)
                st.dataframe(history_df, use_container_width=True, hide_index=True)
                
                # 첫 일치 이후 연속 불일치 구간 표시
                if max_mismatches > 0:
                    st.markdown("---")
                    st.markdown("### 첫 일치 이후 연속 불일치 구간")
                    
                    # 연속 불일치 구간 찾기
                    consecutive_runs = []
                    current_run_start = None
                    current_run_length = 0
                    
                    for idx in range(result['first_match_idx'] + 1, len(result['all_steps'])):
                        entry = result['all_steps'][idx]
                        validated_val = entry.get('validated')
                        is_correct_val = entry.get('is_correct')
                        is_validated = (validated_val == 1 or validated_val is True)
                        is_mismatch = (is_correct_val == 0 or is_correct_val is False)
                        
                        if is_validated and is_mismatch:
                            if current_run_start is None:
                                current_run_start = idx
                            current_run_length += 1
                        else:
                            if current_run_start is not None:
                                consecutive_runs.append({
                                    'start_idx': current_run_start,
                                    'length': current_run_length
                                })
                                current_run_start = None
                                current_run_length = 0
                    
                    # 마지막 구간 처리
                    if current_run_start is not None:
                        consecutive_runs.append({
                            'start_idx': current_run_start,
                            'length': current_run_length
                        })
                    
                    if len(consecutive_runs) > 0:
                        runs_df_data = []
                        for run in consecutive_runs:
                            runs_df_data.append({
                                '시작 인덱스': run['start_idx'],
                                '연속 길이': run['length'],
                                '6개 미만': '✅' if run['length'] < 6 else '❌'
                            })
                        runs_df = pd.DataFrame(runs_df_data)
                        st.dataframe(runs_df, use_container_width=True, hide_index=True)
            else:
                st.info("💡 히스토리 데이터가 없습니다.")
        else:
            st.info("💡 첫 일치로 시작하는 예측이 없습니다.")
            
            # 히스토리 확인용 (첫 일치가 없는 경우에도 전체 스텝 보기)
            if result['total_steps'] > 0:
                st.markdown("---")
                st.markdown("### 전체 히스토리 (첫 일치 없음)")
                
                history_data = []
                for idx, entry in enumerate(result['all_steps']):
                    is_correct = entry.get('is_correct')
                    match_status = '✅' if (is_correct == 1 or is_correct is True) else ('❌' if (is_correct == 0 or is_correct is False) else '-')
                    skipped_val = entry.get('skipped')
                    skipped = '⏭️' if (skipped_val == 1 or skipped_val is True) else ''
                    is_forced = '⚡' if (entry.get('is_forced') == 1 or entry.get('is_forced') is True) else ''
                    
                    has_prediction_val = entry.get('has_prediction')
                    has_prediction = (has_prediction_val == 1 or has_prediction_val is True)
                    
                    history_data.append({
                        '인덱스': idx,
                        '세션 ID': entry.get('session_id'),
                        '스텝': entry.get('step'),
                        'Prefix': entry.get('prefix', ''),
                        '예측': f"{entry.get('predicted_value') or '-'}{is_forced}{skipped}",
                        '실제값': entry.get('actual_value', ''),
                        '일치': match_status,
                        '신뢰도': f"{entry.get('confidence', 0):.1f}%" if has_prediction else '-'
                    })
                
                history_df = pd.DataFrame(history_data)
                st.dataframe(history_df, use_container_width=True, hide_index=True)
    
    # 첫 스텝 스킵 분석 섹션
    st.markdown("---")
    st.markdown("## 첫 스텝 스킵 분석")
    st.markdown("첫 번째 예측 가능한 스텝에서 스킵으로 시작한 게임과 그렇지 않은 게임의 승률 상관관계 분석")
    st.info("💡 이 분석은 '🎯 신뢰도 기반 스킵 전략 검증'에서 검증한 데이터를 사용합니다.")
    
    # 검증 세션 선택
    validation_sessions_df = load_validation_sessions()
    
    if len(validation_sessions_df) == 0:
        st.warning("⚠️ 저장된 검증 세션이 없습니다. 먼저 '🎯 신뢰도 기반 스킵 전략 검증'에서 검증을 실행하고 결과를 저장해주세요.")
    else:
        with st.form("first_step_skip_analysis_form", clear_on_submit=False):
            st.markdown("### 검증 세션 선택")
            
            # 검증 세션 선택
            session_options = []
            for idx, row in validation_sessions_df.iterrows():
                session_label = (
                    f"ID: {row['validation_id'][:8]}... | "
                    f"Cutoff: {row['cutoff_grid_string_id']} | "
                    f"윈도우: {row['window_size']} | "
                    f"임계값1: {row['confidence_skip_threshold_1']:.1f}% | "
                    f"임계값2: {row['confidence_skip_threshold_2']:.1f}% | "
                    f"생성: {row['created_at']}"
                )
                session_options.append((row['validation_id'], session_label))
            
            selected_session_id = st.selectbox(
                "검증 세션 선택",
                options=[opt[0] for opt in session_options],
                format_func=lambda x: next((opt[1] for opt in session_options if opt[0] == x), x),
                key="first_step_skip_session_select"
            )
            
            # 선택된 세션 정보 표시
            if selected_session_id:
                selected_session = validation_sessions_df[validation_sessions_df['validation_id'] == selected_session_id].iloc[0]
                
                col_info1, col_info2, col_info3 = st.columns(3)
                with col_info1:
                    st.metric("윈도우 크기", selected_session['window_size'])
                    st.metric("임계값 1", f"{selected_session['confidence_skip_threshold_1']:.1f}%")
                with col_info2:
                    st.metric("임계값 2", f"{selected_session['confidence_skip_threshold_2']:.1f}%")
                    st.metric("Cutoff ID", selected_session['cutoff_grid_string_id'])
                with col_info3:
                    st.metric("예측 방법", selected_session['method'])
                    st.metric("생성일", selected_session['created_at'])
                
                # 임계값 선택
                threshold_option = st.radio(
                    "분석할 임계값 선택",
                    options=[selected_session['confidence_skip_threshold_1'], selected_session['confidence_skip_threshold_2']],
                    format_func=lambda x: f"{x:.1f}%",
                    key="first_step_skip_threshold_radio"
                )
            
            submitted_skip = st.form_submit_button("첫 스텝 스킵 분석 실행", type="primary")
            
            if submitted_skip:
                if not selected_session_id:
                    st.error("검증 세션을 선택해주세요.")
                else:
                    with st.spinner("분석 실행 중..."):
                        try:
                            # 기존 검증 데이터를 사용하여 분석
                            analysis_result = analyze_first_step_skip_correlation_from_validation(
                                selected_session_id,
                                threshold_option
                            )
                            
                            if analysis_result:
                                # 분석 결과를 session_state에 저장
                                st.session_state.first_step_skip_analysis_result = analysis_result
                                st.session_state.first_step_skip_selected_session_id = selected_session_id
                                st.session_state.first_step_skip_threshold_option = threshold_option
                                st.success("분석이 완료되었습니다.")
                            else:
                                st.warning("분석 결과가 없습니다. 선택한 검증 세션에 데이터가 없을 수 있습니다.")
                        except Exception as e:
                            st.error(f"분석 실행 중 오류 발생: {str(e)}")
                            import traceback
                            st.error(f"상세 오류: {traceback.format_exc()}")
    
    # 분석 결과 표시 (form 밖에서 표시 - session_state에서 읽어서 표시)
    if 'first_step_skip_analysis_result' in st.session_state:
        analysis_result = st.session_state.first_step_skip_analysis_result
        selected_session_id = st.session_state.first_step_skip_selected_session_id
        threshold_option = st.session_state.first_step_skip_threshold_option
        
        st.markdown("---")
        st.markdown("### 분석 결과")
        
        # 요약 통계
        col_summary1, col_summary2 = st.columns(2)
        
        with col_summary1:
            st.markdown("#### 스킵으로 시작한 게임")
            st.metric("게임 수", analysis_result['skip_start_count'])
            if analysis_result['skip_start_avg_accuracy'] is not None:
                st.metric("평균 승률", f"{analysis_result['skip_start_avg_accuracy']:.2f}%")
            if analysis_result['skip_start_outlier_rate'] is not None:
                st.metric("이상치 발생 비율", f"{analysis_result['skip_start_outlier_rate']:.2f}%")
                st.caption(f"이상치 발생: {analysis_result['skip_start_outlier_count']}개")
            if analysis_result.get('skip_start_first_match_count', 0) > 0 or analysis_result.get('skip_start_first_mismatch_count', 0) > 0:
                st.markdown("**첫 예측 결과별**")
                if analysis_result.get('skip_start_first_match_avg_accuracy') is not None:
                    st.caption(f"첫 예측 일치: {analysis_result['skip_start_first_match_count']}개, 평균 승률: {analysis_result['skip_start_first_match_avg_accuracy']:.2f}%")
                if analysis_result.get('skip_start_first_mismatch_avg_accuracy') is not None:
                    st.caption(f"첫 예측 불일치: {analysis_result['skip_start_first_mismatch_count']}개, 평균 승률: {analysis_result['skip_start_first_mismatch_avg_accuracy']:.2f}%")
        
        with col_summary2:
            st.markdown("#### 스킵 없이 시작한 게임")
            st.metric("게임 수", analysis_result['non_skip_start_count'])
            if analysis_result['non_skip_start_avg_accuracy'] is not None:
                st.metric("평균 승률", f"{analysis_result['non_skip_start_avg_accuracy']:.2f}%")
            if analysis_result['non_skip_start_outlier_rate'] is not None:
                st.metric("이상치 발생 비율", f"{analysis_result['non_skip_start_outlier_rate']:.2f}%")
                st.caption(f"이상치 발생: {analysis_result['non_skip_start_outlier_count']}개")
            if analysis_result.get('non_skip_start_first_match_count', 0) > 0 or analysis_result.get('non_skip_start_first_mismatch_count', 0) > 0:
                st.markdown("**첫 예측 결과별**")
                if analysis_result.get('non_skip_start_first_match_avg_accuracy') is not None:
                    st.caption(f"첫 예측 일치: {analysis_result['non_skip_start_first_match_count']}개, 평균 승률: {analysis_result['non_skip_start_first_match_avg_accuracy']:.2f}%")
                if analysis_result.get('non_skip_start_first_mismatch_avg_accuracy') is not None:
                    st.caption(f"첫 예측 불일치: {analysis_result['non_skip_start_first_mismatch_count']}개, 평균 승률: {analysis_result['non_skip_start_first_mismatch_avg_accuracy']:.2f}%")
        
        # 차이 계산
        if analysis_result['skip_start_avg_accuracy'] is not None and analysis_result['non_skip_start_avg_accuracy'] is not None:
            accuracy_diff = analysis_result['non_skip_start_avg_accuracy'] - analysis_result['skip_start_avg_accuracy']
            st.info(f"승률 차이: {accuracy_diff:+.2f}% (스킵 없이 시작한 게임이 {'높음' if accuracy_diff > 0 else '낮음'})")
        
        # 이상치 발생 비율 차이
        if analysis_result['skip_start_outlier_rate'] is not None and analysis_result['non_skip_start_outlier_rate'] is not None:
            outlier_diff = analysis_result['skip_start_outlier_rate'] - analysis_result['non_skip_start_outlier_rate']
            st.info(f"이상치 발생 비율 차이: {outlier_diff:+.2f}% (스킵으로 시작한 게임이 {'높음' if outlier_diff > 0 else '낮음'})")
        
        # 상세 데이터
        st.markdown("#### 상세 데이터")
        if 'skip_start_df' in analysis_result and len(analysis_result['skip_start_df']) > 0:
            st.markdown("**스킵으로 시작한 게임**")
            st.dataframe(analysis_result['skip_start_df'], use_container_width=True)
        
        if 'non_skip_start_df' in analysis_result and len(analysis_result['non_skip_start_df']) > 0:
            st.markdown("**스킵 없이 시작한 게임**")
            st.dataframe(analysis_result['non_skip_start_df'], use_container_width=True)
        
        # 상세 히스토리 확인
        st.markdown("---")
        st.markdown("#### 상세 히스토리 확인")
        
        # 분석 결과에서 사용 가능한 grid_string_id 목록 가져오기
        all_grid_string_ids = []
        if 'skip_start_df' in analysis_result and len(analysis_result['skip_start_df']) > 0:
            all_grid_string_ids.extend(analysis_result['skip_start_df']['grid_string_id'].tolist())
        if 'non_skip_start_df' in analysis_result and len(analysis_result['non_skip_start_df']) > 0:
            all_grid_string_ids.extend(analysis_result['non_skip_start_df']['grid_string_id'].tolist())
        
        if len(all_grid_string_ids) > 0:
            selected_grid_string_id = st.selectbox(
                "Grid String ID 선택",
                options=sorted(set(all_grid_string_ids)),
                key="first_step_skip_grid_string_select"
            )
            
            if selected_grid_string_id:
                # 상세 히스토리 로드
                steps_df = load_validation_session_steps(selected_session_id, threshold_option)
                grid_steps_df = steps_df[steps_df['grid_string_id'] == selected_grid_string_id].sort_values('step')
                
                if len(grid_steps_df) > 0:
                    st.markdown(f"**Grid String ID {selected_grid_string_id} 상세 히스토리**")
                    
                    # 히스토리 데이터 포맷팅
                    history_data = []
                    for _, row in grid_steps_df.iterrows():
                        is_correct = row.get('is_correct')
                        match_status = '✅' if (is_correct == 1 or is_correct is True) else ('❌' if (is_correct == 0 or is_correct is False) else '-')
                        skipped_val = row.get('skipped')
                        skipped = '⏭️' if (skipped_val == 1 or skipped_val is True) else ''
                        is_forced = '⚡' if (row.get('is_forced') == 1 or row.get('is_forced') is True) else ''
                        validated = '✓' if (row.get('validated') == 1 or row.get('validated') is True) else ''
                        
                        has_prediction_val = row.get('has_prediction')
                        has_prediction = (has_prediction_val == 1 or has_prediction_val is True)
                        
                        history_data.append({
                            '스텝': row.get('step'),
                            'Prefix': row.get('prefix', ''),
                            '예측': f"{row.get('predicted') or '-'}{is_forced}{skipped}",
                            '실제값': row.get('actual', ''),
                            '일치': match_status,
                            '신뢰도': f"{row.get('confidence', 0):.1f}%" if has_prediction else '-',
                            '강제예측': '✓' if (row.get('is_forced') == 1 or row.get('is_forced') is True) else '',
                            '검증': validated,
                            '스킵': '✓' if (row.get('skipped') == 1 or row.get('skipped') is True) else '',
                            '간격': row.get('current_interval', 0)
                        })
                    
                    history_df = pd.DataFrame(history_data)
                    st.dataframe(history_df, use_container_width=True, hide_index=True)
                else:
                    st.warning(f"Grid String ID {selected_grid_string_id}의 상세 히스토리를 찾을 수 없습니다.")
        else:
            st.info("상세 히스토리를 확인할 수 있는 Grid String ID가 없습니다.")

if __name__ == "__main__":
    main()
