"""
최적 스킵 임계값 탐색 시뮬레이션 앱
50.5 ~ 51.5 범위에서 0.1 단위로 스킵 임계값을 테스트하여
최대 연속 불일치 5 이하를 만족하는 최적 임계값을 찾습니다.
"""

import streamlit as st

# 페이지 설정 (모든 import 전에 실행되어야 함)
st.set_page_config(
    page_title="Optimal Threshold Finder",
    page_icon="🎯",
    layout="wide"
)

import pandas as pd
import sqlite3
import uuid
import time
from collections import defaultdict
from datetime import datetime

# 기존 앱의 함수들 import
from hypothesis_validation_app import (
    get_db_connection,
    load_preprocessed_data
)

# interactive_multi_step_validation_app에서 필요한 함수들 import
from interactive_multi_step_validation_app import (
    batch_validate_interactive_multi_step_scenario_with_confidence_skip
)

# DB 경로
DB_PATH = 'hypothesis_validation.db'

def create_simulation_tables():
    """
    최적 임계값 시뮬레이션 결과 저장을 위한 테이블 생성
    """
    conn = get_db_connection()
    if conn is None:
        return False
    
    cursor = conn.cursor()
    
    try:
        # 1. 시뮬레이션 세션 메타데이터 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS optimal_threshold_simulation_sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT NOT NULL UNIQUE,
                cutoff_grid_string_id INTEGER NOT NULL,
                window_size INTEGER NOT NULL,
                method TEXT NOT NULL,
                use_threshold BOOLEAN NOT NULL,
                threshold REAL,
                max_interval INTEGER NOT NULL,
                min_skip_threshold REAL NOT NULL,
                max_skip_threshold REAL NOT NULL,
                step REAL NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours'))
            )
        ''')
        
        # 2. 각 임계값별 요약 통계 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS optimal_threshold_simulation_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT NOT NULL,
                confidence_skip_threshold REAL NOT NULL,
                max_consecutive_failures INTEGER NOT NULL,
                avg_max_consecutive_failures REAL NOT NULL,
                total_skipped_predictions INTEGER NOT NULL,
                avg_skip_rate REAL NOT NULL,
                below_5_ratio REAL NOT NULL,
                avg_accuracy REAL NOT NULL,
                prediction_rate REAL NOT NULL,
                total_grid_strings INTEGER NOT NULL,
                total_steps INTEGER NOT NULL,
                total_failures INTEGER NOT NULL,
                total_predictions INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                FOREIGN KEY (validation_id) REFERENCES optimal_threshold_simulation_sessions(validation_id),
                UNIQUE(validation_id, confidence_skip_threshold)
            )
        ''')
        
        # 3. Grid String별 상세 결과 테이블 (선택적)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS optimal_threshold_simulation_grid_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT NOT NULL,
                confidence_skip_threshold REAL NOT NULL,
                grid_string_id INTEGER NOT NULL,
                max_consecutive_failures INTEGER NOT NULL,
                total_skipped_predictions INTEGER NOT NULL,
                accuracy REAL NOT NULL,
                total_steps INTEGER NOT NULL,
                total_predictions INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                FOREIGN KEY (validation_id) REFERENCES optimal_threshold_simulation_sessions(validation_id),
                FOREIGN KEY (grid_string_id) REFERENCES preprocessed_grid_strings(id),
                UNIQUE(validation_id, confidence_skip_threshold, grid_string_id)
            )
        ''')
        
        # 인덱스 생성
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_simulation_sessions_created_at 
            ON optimal_threshold_simulation_sessions(created_at)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_simulation_sessions_cutoff 
            ON optimal_threshold_simulation_sessions(cutoff_grid_string_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_simulation_results_validation_id 
            ON optimal_threshold_simulation_results(validation_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_simulation_results_threshold 
            ON optimal_threshold_simulation_results(confidence_skip_threshold)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_simulation_results_max_failures 
            ON optimal_threshold_simulation_results(max_consecutive_failures)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_simulation_grid_results_validation_id 
            ON optimal_threshold_simulation_grid_results(validation_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_simulation_grid_results_threshold 
            ON optimal_threshold_simulation_grid_results(confidence_skip_threshold)
        ''')
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        st.error(f"테이블 생성 중 오류 발생: {str(e)}")
        return False
    finally:
        conn.close()

def simulate_single_threshold(
    cutoff_id,
    confidence_skip_threshold,
    window_size=7,
    method="빈도 기반",
    use_threshold=True,
    main_threshold=60,
    max_interval=6
):
    """
    단일 임계값에 대한 검증 실행 및 지표 수집
    
    Args:
        cutoff_id: 기준 grid_string ID
        confidence_skip_threshold: 스킵 임계값
        window_size: 윈도우 크기
        method: 예측 방법
        use_threshold: 임계값 전략 사용 여부
        main_threshold: 메인 임계값
        max_interval: 최대 예측 없음 간격
    
    Returns:
        dict: 검증 결과 및 지표
    """
    try:
        # 배치 검증 실행
        batch_results = batch_validate_interactive_multi_step_scenario_with_confidence_skip(
            cutoff_id,
            window_size=window_size,
            method=method,
            use_threshold=use_threshold,
            threshold=main_threshold,
            max_interval=max_interval,
            reverse_forced_prediction=False,
            confidence_skip_threshold=confidence_skip_threshold
        )
        
        if batch_results is None or len(batch_results.get('results', [])) == 0:
            return None
        
        summary = batch_results.get('summary', {})
        results = batch_results.get('results', [])
        
        # 추가 지표 계산
        # 5 이하 비율 계산
        below_5_count = sum(1 for r in results if r.get('max_consecutive_failures', 0) <= 5)
        below_5_ratio = (below_5_count / len(results) * 100) if len(results) > 0 else 0.0
        
        # 평균 스킵 비율 계산
        total_skipped = summary.get('total_skipped_predictions', 0)
        total_grid_strings = summary.get('total_grid_strings', 0)
        avg_skip_rate = (total_skipped / total_grid_strings) if total_grid_strings > 0 else 0.0
        
        # 결과 반환
        return {
            'confidence_skip_threshold': confidence_skip_threshold,
            'max_consecutive_failures': summary.get('max_consecutive_failures', 0),
            'avg_max_consecutive_failures': summary.get('avg_max_consecutive_failures', 0.0),
            'total_skipped_predictions': total_skipped,
            'avg_skip_rate': avg_skip_rate,
            'below_5_ratio': below_5_ratio,
            'below_5_count': below_5_count,
            'avg_accuracy': summary.get('avg_accuracy', 0.0),
            'prediction_rate': summary.get('prediction_rate', 0.0),
            'total_grid_strings': total_grid_strings,
            'total_steps': summary.get('total_steps', 0),
            'total_failures': summary.get('total_failures', 0),
            'total_predictions': summary.get('total_predictions', 0),
            'batch_results': batch_results  # 상세 결과 포함
        }
        
    except Exception as e:
        st.error(f"임계값 {confidence_skip_threshold} 시뮬레이션 중 오류: {str(e)}")
        return None

def batch_simulate_threshold_range(
    cutoff_id,
    window_size=7,
    method="빈도 기반",
    use_threshold=True,
    main_threshold=60,
    max_interval=6,
    min_skip_threshold=50.5,
    max_skip_threshold=51.5,
    step=0.1,
    progress_bar=None,
    status_text=None
):
    """
    범위 내 모든 임계값에 대한 시뮬레이션
    
    Args:
        cutoff_id: 기준 grid_string ID
        window_size: 윈도우 크기
        method: 예측 방법
        use_threshold: 임계값 전략 사용 여부
        main_threshold: 메인 임계값
        max_interval: 최대 예측 없음 간격
        min_skip_threshold: 최소 스킵 임계값
        max_skip_threshold: 최대 스킵 임계값
        step: 임계값 간격
        progress_bar: Streamlit progress bar 객체 (선택적)
        status_text: Streamlit status text 객체 (선택적)
    
    Returns:
        dict: 모든 임계값의 시뮬레이션 결과
    """
    # 임계값 리스트 생성
    thresholds = []
    current = min_skip_threshold
    while current <= max_skip_threshold + 0.001:  # 부동소수점 오차 고려
        thresholds.append(round(current, 1))
        current += step
    
    results = []
    total = len(thresholds)
    start_time = time.time()
    first_completion_time = None
    
    # 각 임계값에 대해 시뮬레이션 실행
    for idx, threshold in enumerate(thresholds):
        threshold_start = time.time()
        
        # 상태 업데이트
        if status_text:
            status_text.text(f"임계값 {threshold}% 테스트 중... ({idx+1}/{total})")
        
        result = simulate_single_threshold(
            cutoff_id,
            threshold,
            window_size=window_size,
            method=method,
            use_threshold=use_threshold,
            main_threshold=main_threshold,
            max_interval=max_interval
        )
        
        threshold_elapsed = time.time() - threshold_start
        
        if result is not None:
            results.append(result)
        
        # 첫 번째 완료 시간 기록 (예상 시간 계산용)
        if first_completion_time is None and result is not None:
            first_completion_time = threshold_elapsed
        
        # 프로그레스 바 업데이트
        if progress_bar:
            progress = (idx + 1) / total
            progress_bar.progress(progress)
        
        # 예상 시간 계산 및 표시
        if status_text and idx >= 0:
            elapsed = time.time() - start_time
            if idx > 0:
                avg_time_per_threshold = elapsed / (idx + 1)
                remaining = (total - idx - 1) * avg_time_per_threshold
                
                elapsed_min = int(elapsed // 60)
                elapsed_sec = int(elapsed % 60)
                remaining_min = int(remaining // 60)
                remaining_sec = int(remaining % 60)
                
                if elapsed_min > 0:
                    elapsed_str = f"{elapsed_min}분 {elapsed_sec}초"
                else:
                    elapsed_str = f"{elapsed_sec}초"
                
                if remaining_min > 0:
                    remaining_str = f"{remaining_min}분 {remaining_sec}초"
                else:
                    remaining_str = f"{remaining_sec}초"
                
                status_text.text(
                    f"임계값 {threshold}% 완료 ({idx+1}/{total}) | "
                    f"경과: {elapsed_str} | 예상 남은 시간: {remaining_str}"
                )
            else:
                # 첫 번째 임계값 완료 후 예상 시간 표시
                if first_completion_time:
                    estimated_total = first_completion_time * total
                    estimated_min = int(estimated_total // 60)
                    estimated_sec = int(estimated_total % 60)
                    
                    if estimated_min > 0:
                        estimated_str = f"{estimated_min}분 {estimated_sec}초"
                    else:
                        estimated_str = f"{estimated_sec}초"
                    
                    status_text.text(
                        f"임계값 {threshold}% 완료 ({idx+1}/{total}) | "
                        f"예상 총 소요 시간: 약 {estimated_str}"
                    )
        
        # 진행 상황 업데이트 (session_state에 저장)
        if 'simulation_progress' not in st.session_state:
            st.session_state.simulation_progress = {}
        st.session_state.simulation_progress[threshold] = {
            'completed': True,
            'result': result is not None
        }
    
    return {
        'results': results,
        'thresholds_tested': thresholds,
        'total_tested': total,
        'successful': len(results),
        'failed': total - len(results)
    }

def find_optimal_threshold(simulation_results):
    """
    최적 임계값 선정 알고리즘
    - 1차 필터링: 최대 연속 불일치가 5 이하인 임계값만 선별
    - 2차 정렬: 최대 연속 불일치가 가장 낮은 순, 동일하면 스킵 횟수가 적은 순
    
    Args:
        simulation_results: batch_simulate_threshold_range()의 반환값
    
    Returns:
        dict: 최적 임계값 정보 및 추천 결과
    """
    if not simulation_results or len(simulation_results.get('results', [])) == 0:
        return {
            'optimal_threshold': None,
            'optimal_result': None,
            'candidates': [],
            'all_results': []
        }
    
    all_results = simulation_results['results']
    
    # 1차 필터링: 최대 연속 불일치가 5 이하인 임계값만 선별
    candidates = [
        r for r in all_results 
        if r.get('max_consecutive_failures', 999) <= 5
    ]
    
    if len(candidates) == 0:
        # 조건을 만족하는 임계값이 없으면 전체 결과 반환
        # 최대 연속 불일치가 가장 낮은 것을 선택
        all_results_sorted = sorted(
            all_results,
            key=lambda x: (
                x.get('max_consecutive_failures', 999),
                x.get('total_skipped_predictions', 999999),
                -x.get('avg_accuracy', 0.0)
            )
        )
        optimal_result = all_results_sorted[0] if all_results_sorted else None
        return {
            'optimal_threshold': optimal_result.get('confidence_skip_threshold') if optimal_result else None,
            'optimal_result': optimal_result,
            'candidates': [],
            'all_results': all_results,
            'warning': '최대 연속 불일치 5 이하를 만족하는 임계값이 없습니다.'
        }
    
    # 2차 정렬: 최대 연속 불일치가 가장 낮은 순, 동일하면 스킵 횟수가 적은 순, 동일하면 정확도가 높은 순
    candidates_sorted = sorted(
        candidates,
        key=lambda x: (
            x.get('max_consecutive_failures', 999),
            x.get('total_skipped_predictions', 999999),
            -x.get('avg_accuracy', 0.0)
        )
    )
    
    optimal_result = candidates_sorted[0]
    optimal_threshold = optimal_result.get('confidence_skip_threshold')
    
    return {
        'optimal_threshold': optimal_threshold,
        'optimal_result': optimal_result,
        'candidates': candidates_sorted,
        'all_results': all_results,
        'candidate_count': len(candidates)
    }

def save_simulation_results(
    cutoff_id,
    window_size,
    method,
    use_threshold,
    main_threshold,
    max_interval,
    min_skip_threshold,
    max_skip_threshold,
    step,
    simulation_results,
    optimal_result
):
    """
    시뮬레이션 결과 DB 저장
    
    Args:
        cutoff_id: 기준 grid_string ID
        window_size: 윈도우 크기
        method: 예측 방법
        use_threshold: 임계값 전략 사용 여부
        main_threshold: 메인 임계값
        max_interval: 최대 예측 없음 간격
        min_skip_threshold: 최소 스킵 임계값
        max_skip_threshold: 최대 스킵 임계값
        step: 임계값 간격
        simulation_results: batch_simulate_threshold_range()의 반환값
        optimal_result: find_optimal_threshold()의 반환값
    
    Returns:
        str: validation_id (저장 성공 시), None (실패 시)
    """
    if not create_simulation_tables():
        return None
    
    conn = get_db_connection()
    if conn is None:
        return None
    
    cursor = conn.cursor()
    
    try:
        # validation_id 생성 (UUID)
        validation_id = str(uuid.uuid4())
        
        # 1. 시뮬레이션 세션 저장
        cursor.execute('''
            INSERT INTO optimal_threshold_simulation_sessions (
                validation_id, cutoff_grid_string_id, window_size, method,
                use_threshold, threshold, max_interval,
                min_skip_threshold, max_skip_threshold, step,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
        ''', (
            validation_id,
            cutoff_id,
            window_size,
            method,
            use_threshold,
            main_threshold if use_threshold else None,
            max_interval,
            min_skip_threshold,
            max_skip_threshold,
            step
        ))
        
        # 2. 각 임계값별 결과 저장
        for result in simulation_results.get('results', []):
            cursor.execute('''
                INSERT INTO optimal_threshold_simulation_results (
                    validation_id, confidence_skip_threshold,
                    max_consecutive_failures, avg_max_consecutive_failures,
                    total_skipped_predictions, avg_skip_rate,
                    below_5_ratio, avg_accuracy, prediction_rate,
                    total_grid_strings, total_steps, total_failures, total_predictions,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
            ''', (
                validation_id,
                result.get('confidence_skip_threshold'),
                result.get('max_consecutive_failures', 0),
                result.get('avg_max_consecutive_failures', 0.0),
                result.get('total_skipped_predictions', 0),
                result.get('avg_skip_rate', 0.0),
                result.get('below_5_ratio', 0.0),
                result.get('avg_accuracy', 0.0),
                result.get('prediction_rate', 0.0),
                result.get('total_grid_strings', 0),
                result.get('total_steps', 0),
                result.get('total_failures', 0),
                result.get('total_predictions', 0)
            ))
            
            # 3. Grid String별 상세 결과 저장 (선택적 - 메모리 절약을 위해 제한)
            batch_results = result.get('batch_results')
            if batch_results and 'results' in batch_results:
                # 최대 100개만 저장 (대용량 방지)
                grid_results = batch_results['results'][:100]
                for grid_result in grid_results:
                    cursor.execute('''
                        INSERT OR REPLACE INTO optimal_threshold_simulation_grid_results (
                            validation_id, confidence_skip_threshold, grid_string_id,
                            max_consecutive_failures, total_skipped_predictions,
                            accuracy, total_steps, total_predictions,
                            created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
                    ''', (
                        validation_id,
                        result.get('confidence_skip_threshold'),
                        grid_result.get('grid_string_id'),
                        grid_result.get('max_consecutive_failures', 0),
                        grid_result.get('total_skipped_predictions', 0),
                        grid_result.get('accuracy', 0.0),
                        grid_result.get('total_steps', 0),
                        grid_result.get('total_predictions', 0)
                    ))
        
        conn.commit()
        return validation_id
        
    except Exception as e:
        conn.rollback()
        st.error(f"시뮬레이션 결과 저장 중 오류 발생: {str(e)}")
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
        return None
    finally:
        conn.close()

def load_simulation_sessions():
    """
    저장된 시뮬레이션 세션 목록 로드
    
    Returns:
        pd.DataFrame: 시뮬레이션 세션 목록 (validation_id, cutoff_id, window_size, method, 최적 임계값 등)
    """
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()
    
    try:
        query = """
            SELECT 
                s.validation_id,
                s.cutoff_grid_string_id,
                s.window_size,
                s.method,
                s.use_threshold,
                s.threshold,
                s.max_interval,
                s.min_skip_threshold,
                s.max_skip_threshold,
                s.step,
                s.created_at,
                r.confidence_skip_threshold as optimal_threshold,
                r.max_consecutive_failures,
                r.below_5_ratio,
                r.avg_accuracy
            FROM optimal_threshold_simulation_sessions s
            LEFT JOIN (
                SELECT 
                    validation_id,
                    confidence_skip_threshold,
                    max_consecutive_failures,
                    below_5_ratio,
                    avg_accuracy,
                    ROW_NUMBER() OVER (PARTITION BY validation_id ORDER BY max_consecutive_failures ASC, total_skipped_predictions ASC) as rn
                FROM optimal_threshold_simulation_results
                WHERE max_consecutive_failures <= 5
            ) r ON s.validation_id = r.validation_id AND r.rn = 1
            ORDER BY s.created_at DESC
        """
        df = pd.read_sql_query(query, conn)
        return df
    except Exception as e:
        st.error(f"시뮬레이션 세션 로드 오류: {str(e)}")
        return pd.DataFrame()
    finally:
        conn.close()

def load_simulation_session(validation_id):
    """
    특정 시뮬레이션 세션의 상세 정보 로드
    
    Args:
        validation_id: 시뮬레이션 세션 ID
    
    Returns:
        dict: 세션 정보 및 최적 임계값
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # 세션 정보 로드
        session_query = """
            SELECT 
                validation_id,
                cutoff_grid_string_id,
                window_size,
                method,
                use_threshold,
                threshold,
                max_interval,
                min_skip_threshold,
                max_skip_threshold,
                step,
                created_at
            FROM optimal_threshold_simulation_sessions
            WHERE validation_id = ?
        """
        session_df = pd.read_sql_query(session_query, conn, params=[validation_id])
        
        if len(session_df) == 0:
            return None
        
        session_info = session_df.iloc[0].to_dict()
        
        # 최적 임계값 찾기 (5 이하인 것 중 가장 좋은 것)
        optimal_query = """
            SELECT 
                confidence_skip_threshold,
                max_consecutive_failures,
                below_5_ratio,
                avg_accuracy,
                total_skipped_predictions
            FROM optimal_threshold_simulation_results
            WHERE validation_id = ?
            ORDER BY max_consecutive_failures ASC, total_skipped_predictions ASC, avg_accuracy DESC
            LIMIT 1
        """
        optimal_df = pd.read_sql_query(optimal_query, conn, params=[validation_id])
        
        if len(optimal_df) > 0:
            session_info['optimal_confidence_skip_threshold'] = optimal_df.iloc[0]['confidence_skip_threshold']
            session_info['optimal_max_consecutive_failures'] = optimal_df.iloc[0]['max_consecutive_failures']
            session_info['optimal_below_5_ratio'] = optimal_df.iloc[0]['below_5_ratio']
            session_info['optimal_avg_accuracy'] = optimal_df.iloc[0]['avg_accuracy']
        else:
            session_info['optimal_confidence_skip_threshold'] = None
        
        return session_info
    except Exception as e:
        st.error(f"시뮬레이션 세션 상세 정보 로드 오류: {str(e)}")
        return None
    finally:
        conn.close()

def display_results(simulation_results, optimal_result, cutoff_id, window_size, method, use_threshold, main_threshold, max_interval):
    """결과 표시 함수"""
    st.markdown("---")
    st.markdown("### 📊 시뮬레이션 결과")
    
    # 최적 임계값 추천
    optimal_threshold = optimal_result.get('optimal_threshold')
    optimal_data = optimal_result.get('optimal_result')
    
    if optimal_threshold is not None and optimal_data:
        st.success(f"✅ **추천 최적 임계값: {optimal_threshold}%**")
        
        col_opt1, col_opt2, col_opt3, col_opt4 = st.columns(4)
        with col_opt1:
            st.metric("최대 연속 불일치", f"{optimal_data.get('max_consecutive_failures', 0)}회")
        with col_opt2:
            st.metric("5 이하 비율", f"{optimal_data.get('below_5_ratio', 0):.2f}%")
        with col_opt3:
            st.metric("총 스킵 횟수", f"{optimal_data.get('total_skipped_predictions', 0):,}회")
        with col_opt4:
            st.metric("평균 정확도", f"{optimal_data.get('avg_accuracy', 0):.2f}%")
        
        if optimal_result.get('warning'):
            st.warning(optimal_result['warning'])
    else:
        st.error("❌ 최적 임계값을 찾을 수 없습니다.")
    
    # 모든 임계값 비교 테이블
    st.markdown("---")
    st.markdown("### 📋 모든 임계값 비교")
    
    comparison_data = []
    for result in simulation_results.get('results', []):
        threshold = result.get('confidence_skip_threshold')
        is_optimal = (threshold == optimal_threshold)
        
        comparison_data.append({
            '임계값 (%)': f"{threshold:.1f}",
            '최대 연속 불일치': result.get('max_consecutive_failures', 0),
            '평균 최대 연속 불일치': f"{result.get('avg_max_consecutive_failures', 0):.2f}",
            '5 이하 비율 (%)': f"{result.get('below_5_ratio', 0):.2f}",
            '총 스킵 횟수': result.get('total_skipped_predictions', 0),
            '평균 스킵 비율': f"{result.get('avg_skip_rate', 0):.2f}",
            '평균 정확도 (%)': f"{result.get('avg_accuracy', 0):.2f}",
            '예측률 (%)': f"{result.get('prediction_rate', 0):.2f}",
            '최적': '✅' if is_optimal else ''
        })
    
    comparison_df = pd.DataFrame(comparison_data)
    st.dataframe(comparison_df, use_container_width=True, hide_index=True)
    
    # 결과 저장 버튼
    st.markdown("---")
    col_save1, col_save2 = st.columns([1, 4])
    with col_save1:
        if st.button("💾 결과 저장", type="primary", use_container_width=True):
            validation_id = save_simulation_results(
                cutoff_id,
                window_size,
                method,
                use_threshold,
                main_threshold if use_threshold else 60,
                max_interval,
                50.5,
                51.5,
                0.1,
                simulation_results,
                optimal_result
            )
            
            if validation_id:
                st.session_state.simulation_saved_id = validation_id
                st.success(f"✅ 시뮬레이션 결과가 저장되었습니다. (ID: {validation_id[:8]}...)")
            else:
                st.warning("⚠️ 결과 저장에 실패했습니다.")
    
    with col_save2:
        if 'simulation_saved_id' in st.session_state:
            st.info(f"💾 마지막 저장 ID: {st.session_state.simulation_saved_id[:8]}...")

def main():
    st.title("🎯 최적 스킵 임계값 탐색 시뮬레이션")
    st.markdown("""
    **목표**: 50.5 ~ 51.5 범위에서 0.1 단위로 스킵 임계값을 테스트하여
    최대 연속 불일치 5 이하를 만족하는 최적 임계값을 찾습니다.
    """)
    
    # 테이블 생성 확인
    if 'simulation_tables_created' not in st.session_state:
        if create_simulation_tables():
            st.session_state.simulation_tables_created = True
        else:
            st.error("테이블 생성 실패")
            return
    
    # 설정 섹션
    with st.form("simulation_settings_form", clear_on_submit=False):
        st.markdown("### ⚙️ 시뮬레이션 설정")
        
        col_setting1, col_setting2, col_setting3 = st.columns(3)
        
        with col_setting1:
            window_size = st.selectbox(
                "윈도우 크기",
                options=[5, 6, 7, 8, 9],
                index=2,  # 7을 기본값으로
                key="simulation_window_size",
                help="예측에 사용할 윈도우 크기"
            )
        
        with col_setting2:
            method = st.selectbox(
                "예측 방법",
                options=["빈도 기반", "가중치 기반", "안전 우선"],
                index=0,
                key="simulation_method",
                help="예측에 사용할 방법"
            )
        
        with col_setting3:
            use_threshold = st.checkbox(
                "임계값 전략 사용",
                value=True,
                key="simulation_use_threshold",
                help="임계값 이상일 때만 예측하도록 설정"
            )
            main_threshold = None
            if use_threshold:
                main_threshold = st.number_input(
                    "임계값 (%)",
                    min_value=0,
                    max_value=100,
                    value=56,
                    step=1,
                    key="simulation_main_threshold",
                    help="이 신뢰도 이상일 때만 예측합니다"
                )
        
        col_setting4, col_setting5 = st.columns(2)
        with col_setting4:
            max_interval = st.number_input(
                "최대 예측 없음 간격 (스텝)",
                min_value=1,
                max_value=20,
                value=5,
                step=1,
                key="simulation_max_interval",
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
                
                current_selected = st.session_state.get('simulation_cutoff_id', None)
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
                    key="simulation_cutoff_id_select"
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
        
        # 시뮬레이션 범위 표시
        st.markdown("---")
        st.markdown("### 📊 시뮬레이션 범위")
        col_range1, col_range2, col_range3, col_range4 = st.columns(4)
        with col_range1:
            st.metric("최소 임계값", "50.5%")
        with col_range2:
            st.metric("최대 임계값", "51.5%")
        with col_range3:
            st.metric("테스트 개수", "11개 (0.1 단위)")
        with col_range4:
            # 예상 시간 계산
            if selected_cutoff_id is not None:
                conn = get_db_connection()
                if conn is not None:
                    try:
                        count_query = "SELECT COUNT(*) as count FROM preprocessed_grid_strings WHERE id > ?"
                        count_df = pd.read_sql_query(count_query, conn, params=[selected_cutoff_id])
                        after_count = count_df.iloc[0]['count']
                        
                        # 예상 시간 계산 (경험적 값)
                        # grid_string 하나당 약 0.5~2초 소요 (길이와 복잡도에 따라 다름)
                        # 보수적으로 1초/grid_string으로 계산
                        time_per_grid = 1.0  # 초
                        time_per_threshold = after_count * time_per_grid  # 초
                        total_time_seconds = time_per_threshold * 11  # 11개 임계값
                        
                        total_minutes = int(total_time_seconds // 60)
                        total_seconds = int(total_time_seconds % 60)
                        
                        if total_minutes > 0:
                            estimated_time = f"약 {total_minutes}분 {total_seconds}초"
                        else:
                            estimated_time = f"약 {total_seconds}초"
                        
                        st.metric("예상 소요 시간", estimated_time, 
                                 help=f"검증 대상: {after_count}개 grid_string × 11개 임계값")
                    except:
                        st.metric("예상 소요 시간", "계산 중...")
                    finally:
                        conn.close()
                else:
                    st.metric("예상 소요 시간", "-")
            else:
                st.metric("예상 소요 시간", "-")
        
        # 시뮬레이션 실행 버튼
        if st.form_submit_button("🚀 시뮬레이션 실행", type="primary", use_container_width=True):
            if selected_cutoff_id is None:
                st.warning("⚠️ 기준 Grid String ID를 선택해주세요.")
            else:
                st.session_state.simulation_cutoff_id = selected_cutoff_id
                st.session_state.simulation_results = None
                st.session_state.simulation_optimal = None
                st.session_state.simulation_progress = {}
                st.rerun()
    
    # 시뮬레이션 실행 및 결과 표시
    if 'simulation_cutoff_id' in st.session_state and st.session_state.simulation_cutoff_id is not None:
        cutoff_id = st.session_state.simulation_cutoff_id
        
        # 현재 설정 가져오기
        window_size = st.session_state.get('simulation_window_size', 7)
        method = st.session_state.get('simulation_method', '빈도 기반')
        use_threshold = st.session_state.get('simulation_use_threshold', True)
        main_threshold = st.session_state.get('simulation_main_threshold', 56) if use_threshold else None
        max_interval = st.session_state.get('simulation_max_interval', 5)
        
        # 결과가 캐시되어 있으면 사용, 없으면 실행
        if 'simulation_results' in st.session_state and st.session_state.simulation_results is not None:
            simulation_results = st.session_state.simulation_results
            optimal_result = st.session_state.get('simulation_optimal')
        else:
            with st.spinner("시뮬레이션 실행 중... (11개 임계값 테스트)"):
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                try:
                    # 배치 시뮬레이션 실행
                    status_text.text("시뮬레이션 시작... (예상 시간: 계산 중)")
                    progress_bar.progress(0.0)
                    
                    simulation_results = batch_simulate_threshold_range(
                        cutoff_id,
                        window_size=window_size,
                        method=method,
                        use_threshold=use_threshold,
                        main_threshold=main_threshold if use_threshold else 60,
                        max_interval=max_interval,
                        min_skip_threshold=50.5,
                        max_skip_threshold=51.5,
                        step=0.1,
                        progress_bar=progress_bar,
                        status_text=status_text
                    )
                    
                    if simulation_results and len(simulation_results.get('results', [])) > 0:
                        # 최적 임계값 찾기
                        status_text.text("최적 임계값 분석 중...")
                        progress_bar.progress(0.95)
                        
                        optimal_result = find_optimal_threshold(simulation_results)
                        
                        st.session_state.simulation_results = simulation_results
                        st.session_state.simulation_optimal = optimal_result
                        
                        progress_bar.progress(1.0)
                        status_text.text("완료!")
                    else:
                        st.error("시뮬레이션 실행 실패")
                        simulation_results = None
                        optimal_result = None
                        
                except Exception as e:
                    st.error(f"시뮬레이션 실행 중 오류 발생: {str(e)}")
                    import traceback
                    st.error(f"상세 오류: {traceback.format_exc()}")
                    simulation_results = None
                    optimal_result = None
                finally:
                    progress_bar.empty()
                    status_text.empty()
        
        # 결과 표시
        if simulation_results and optimal_result:
            display_results(simulation_results, optimal_result, cutoff_id, window_size, method, use_threshold, main_threshold, max_interval)
        elif simulation_results:
            st.warning("⚠️ 시뮬레이션 결과는 있지만 최적값 분석에 실패했습니다.")
        else:
            st.info("💡 시뮬레이션을 실행하면 결과가 표시됩니다.")
    else:
        st.info("💡 기준 Grid String ID를 선택하고 시뮬레이션을 실행하세요.")

if __name__ == "__main__":
    main()
