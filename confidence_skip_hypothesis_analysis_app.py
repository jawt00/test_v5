"""
신뢰도 스킵 전략 가설 검증 분석 앱
첫 번째 일치 예측 이후 연속 불일치 패턴 분석
"""

import streamlit as st

# 페이지 설정 (모든 import 전에 실행되어야 함)
st.set_page_config(
    page_title="Confidence Skip Hypothesis Analysis",
    page_icon="📊",
    layout="wide"
)

import pandas as pd
import sqlite3
from collections import defaultdict
from datetime import datetime

# 기존 앱의 함수들 import
from hypothesis_validation_app import get_db_connection

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

if __name__ == "__main__":
    main()
