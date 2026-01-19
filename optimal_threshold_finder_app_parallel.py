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
import random
from collections import defaultdict
from datetime import datetime
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed

# 기존 앱의 함수들 import
from hypothesis_validation_app import (
    get_db_connection,
    load_preprocessed_data,
    create_stored_predictions_table,
    save_or_update_predictions_for_historical_data,
    load_ngram_chunks,
    build_frequency_model,
    build_weighted_model,
    predict_frequency,
    predict_weighted,
    predict_for_prefix
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
                window_size INTEGER,
                method TEXT NOT NULL,
                use_threshold BOOLEAN NOT NULL,
                threshold REAL,
                max_interval INTEGER,
                min_skip_threshold REAL,
                max_skip_threshold REAL,
                step REAL,
                search_method TEXT DEFAULT 'single',
                window_size_min INTEGER,
                window_size_max INTEGER,
                max_interval_min INTEGER,
                max_interval_max INTEGER,
                num_samples INTEGER,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours'))
            )
        ''')
        
        # 2. 각 임계값별 요약 통계 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS optimal_threshold_simulation_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT NOT NULL,
                confidence_skip_threshold REAL NOT NULL,
                window_size INTEGER,
                max_interval INTEGER,
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
                FOREIGN KEY (validation_id) REFERENCES optimal_threshold_simulation_sessions(validation_id)
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
        
        # 기존 테이블에 새 컬럼 추가 (마이그레이션)
        try:
            # 테이블 존재 여부 확인
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='optimal_threshold_simulation_sessions'")
            table_exists = cursor.fetchone()
            
            if table_exists:
                # 컬럼 존재 여부 확인 (PRAGMA table_info 사용)
                cursor.execute("PRAGMA table_info(optimal_threshold_simulation_sessions)")
                existing_columns = [row[1] for row in cursor.fetchall()]
                
                # 컬럼 추가
                new_columns = [
                    ('search_method', 'TEXT DEFAULT "single"'),
                    ('window_size_min', 'INTEGER'),
                    ('window_size_max', 'INTEGER'),
                    ('max_interval_min', 'INTEGER'),
                    ('max_interval_max', 'INTEGER'),
                    ('num_samples', 'INTEGER')
                ]
                
                for col_name, col_def in new_columns:
                    if col_name not in existing_columns:
                        try:
                            cursor.execute(f"ALTER TABLE optimal_threshold_simulation_sessions ADD COLUMN {col_name} {col_def}")
                        except sqlite3.OperationalError:
                            pass  # 무시
            
            # optimal_threshold_simulation_results 테이블 확인 및 마이그레이션
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='optimal_threshold_simulation_results'")
            results_table_exists = cursor.fetchone()
            
            if results_table_exists:
                cursor.execute("PRAGMA table_info(optimal_threshold_simulation_results)")
                existing_result_columns = [row[1] for row in cursor.fetchall()]
                
                new_result_columns = [
                    ('window_size', 'INTEGER'),
                    ('max_interval', 'INTEGER')
                ]
                
                for col_name, col_def in new_result_columns:
                    if col_name not in existing_result_columns:
                        try:
                            cursor.execute(f"ALTER TABLE optimal_threshold_simulation_results ADD COLUMN {col_name} {col_def}")
                        except sqlite3.OperationalError:
                            pass  # 무시
            
            conn.commit()
        except Exception as e:
            # 마이그레이션 실패해도 테이블 생성은 성공한 것으로 간주
            try:
                conn.rollback()
            except:
                pass
        
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
    main_threshold=56,
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

def _simulate_single_worker(args):
    """
    병렬 처리용 worker 함수
    각 프로세스에서 독립적으로 실행되며, DB 연결도 각 프로세스에서 생성됨
    
    Args:
        args: (cutoff_id, confidence_skip_threshold, window_size, method, 
               use_threshold, main_threshold, max_interval, combo)
    
    Returns:
        tuple: (result_dict, combo_tuple)
    """
    (cutoff_id, confidence_skip_threshold, window_size, method,
     use_threshold, main_threshold, max_interval, combo) = args
    
    # 각 프로세스에서 독립적으로 import (pickle 문제 방지)
    try:
        # simulate_single_threshold는 모듈 레벨 함수이므로 직접 호출 가능
        # 하지만 각 프로세스에서 함수를 재정의해야 하므로 여기서 호출
        from interactive_multi_step_validation_app import (
            batch_validate_interactive_multi_step_scenario_with_confidence_skip
        )
        
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
            return None, combo
        
        summary = batch_results.get('summary', {})
        results = batch_results.get('results', [])
        
        # 지표 계산
        below_5_count = sum(1 for r in results if r.get('max_consecutive_failures', 0) <= 5)
        below_5_ratio = (below_5_count / len(results) * 100) if len(results) > 0 else 0.0
        
        total_skipped = summary.get('total_skipped_predictions', 0)
        total_grid_strings = summary.get('total_grid_strings', 0)
        avg_skip_rate = (total_skipped / total_grid_strings) if total_grid_strings > 0 else 0.0
        
        result = {
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
            'total_predictions': summary.get('total_predictions', 0)
        }
        
        return result, combo
        
    except Exception as e:
        # st.error는 메인 프로세스에서만 작동하므로 여기서는 무시
        return None, combo

def random_search_multi_dimensional(
    cutoff_id,
    window_size_range=(5, 9),
    max_interval_range=(1, 20),
    confidence_skip_range=(51.0, 53.5, 0.1),
    num_samples=100,
    method="빈도 기반",
    use_threshold=True,
    main_threshold=56,
    progress_bar=None,
    status_text=None,
    enable_early_stop=False,
    min_meaningful_results=5,
    max_workers=None,  # None이면 자동 계산, 수동 설정 가능
    use_threading=False  # True면 ThreadPoolExecutor 사용 (I/O 바운드에 유리)
):
    """
    다차원 최적화: 윈도우 크기, 최대 예측 없음 간격, 신뢰도 스킵 임계값을 랜덤 서치로 동시에 탐색
    
    Args:
        cutoff_id: 기준 grid_string ID
        window_size_range: (min, max) 튜플 - 윈도우 크기 범위
        max_interval_range: (min, max) 튜플 - 최대 예측 없음 간격 범위
        confidence_skip_range: (min, max, step) 튜플 - 신뢰도 스킵 임계값 범위 및 간격
        num_samples: 테스트할 무작위 조합 개수
        method: 예측 방법
        use_threshold: 임계값 전략 사용 여부
        main_threshold: 메인 임계값
        progress_bar: Streamlit progress bar 객체 (선택적)
        status_text: Streamlit status text 객체 (선택적)
        enable_early_stop: 조기 종료 활성화 여부 (기본값: False)
        min_meaningful_results: 조기 종료를 위한 최소 유의미한 결과 개수 (기본값: 5)
    
    Returns:
        dict: 모든 조합의 시뮬레이션 결과
    """
    # 파라미터 범위에서 유효한 값 리스트 생성
    window_size_min, window_size_max = window_size_range
    max_interval_min, max_interval_max = max_interval_range
    skip_min, skip_max, skip_step = confidence_skip_range
    
    # 윈도우 크기 리스트
    window_sizes = list(range(window_size_min, window_size_max + 1))
    
    # 최대 예측 없음 간격 리스트
    max_intervals = list(range(max_interval_min, max_interval_max + 1))
    
    # 신뢰도 스킵 임계값 리스트
    confidence_skips = []
    current = skip_min
    while current <= skip_max + 0.001:  # 부동소수점 오차 고려
        confidence_skips.append(round(current, 1))
        current += skip_step
    
    # 가능한 모든 조합 생성
    all_combinations = []
    for ws in window_sizes:
        for mi in max_intervals:
            for cs in confidence_skips:
                all_combinations.append((ws, mi, cs))
    
    total_possible = len(all_combinations)
    
    # 샘플링 개수 조정
    if num_samples >= total_possible:
        # 전체 조합 사용
        selected_combinations = all_combinations
        if status_text:
            status_text.text(f"전체 조합 {total_possible}개를 모두 테스트합니다.")
    else:
        # 무작위 샘플링
        selected_combinations = random.sample(all_combinations, num_samples)
    
    total_to_test = len(selected_combinations)
    
    if status_text:
        status_text.text(f"다차원 최적화 시작: {total_to_test}개 조합 테스트 예정 (전체 가능한 조합: {total_possible}개)")
    
    results = []
    tested_combinations = []
    start_time = time.time()
    best_so_far = None
    best_max_failures = 999
    meaningful_count = 0  # 유의미한 결과 개수 추적
    
    # 병렬 처리용 작업 인자 준비
    tasks = [
        (cutoff_id, cs, ws, method, use_threshold, main_threshold, mi, (ws, mi, cs))
        for (ws, mi, cs) in selected_combinations
    ]
    
    # CPU 코어 수 결정
    if max_workers is None:
        # i5-10400F 기준: 6코어 12스레드
        # CPU 사용률을 50-60% 수준으로 유지하여 다른 작업 여유 확보
        # 코어 수의 50% 사용 (최소 2개, 최대 4개)
        cpu_count = mp.cpu_count()
        max_workers = max(2, min(int(cpu_count * 0.5), 4))
        # 만약 CPU가 많으면 좀 더 사용 (8코어 이상이면 4개까지)
        if cpu_count >= 8:
            max_workers = min(4, max_workers)
    else:
        max_workers = max(1, min(max_workers, mp.cpu_count()))
    
    executor_class = None
    if use_threading:
        executor_class = ThreadPoolExecutor
        executor_type = "스레드"
    else:
        executor_class = ProcessPoolExecutor
        executor_type = "프로세스"
    
    if status_text:
        status_text.text(
            f"다차원 최적화 시작: {total_to_test}개 조합 테스트 예정 "
            f"(병렬 처리: {max_workers}개 {executor_type} 작업자 사용, "
            f"예상 CPU 사용률: {max_workers/mp.cpu_count()*100:.0f}%)"
        )
    
    completed_count = 0
    
    # 병렬 처리 실행
    with executor_class(max_workers=max_workers) as executor:
        # 모든 작업 제출
        future_to_combo = {
            executor.submit(_simulate_single_worker, task): combo
            for task, combo in zip(tasks, selected_combinations)
        }
        
        # 완료된 작업부터 처리 (as_completed 사용)
        for future in as_completed(future_to_combo):
            combo = future_to_combo[future]
            ws, mi, cs = combo
            completed_count += 1
            
            try:
                result, _ = future.result()
                
                if result is not None:
                    # 추가 파라미터 정보 저장
                    result['window_size'] = ws
                    result['max_interval'] = mi
                    result['combination'] = combo
                    
                    results.append(result)
                    tested_combinations.append(combo)
                    
                    # 유의미한 결과인지 확인 (예측률 >= 10%, 스킵 비율 <= 90%, 최소 10회 예측)
                    is_meaningful = (
                        result.get('prediction_rate', 0.0) >= 10.0
                        and result.get('avg_skip_rate', 100.0) <= 90.0
                        and result.get('total_predictions', 0) >= 10
                    )
                    
                    if is_meaningful:
                        meaningful_count += 1
                    
                    # 현재까지 최고 결과 추적
                    max_failures = result.get('max_consecutive_failures', 999)
                    if max_failures < best_max_failures:
                        best_max_failures = max_failures
                        best_so_far = {
                            'window_size': ws,
                            'max_interval': mi,
                            'confidence_skip_threshold': cs,
                            'max_consecutive_failures': max_failures
                        }
                    
                    # 조기 종료 조건 확인 (병렬 처리에서는 모든 작업이 완료된 후 확인)
                    # 참고: 병렬 처리에서는 정확한 조기 종료가 어렵지만,
                    # 모든 결과를 받은 후 의미 있는 결과를 필터링할 수 있음
                    
                # 프로그레스 바 업데이트
                if progress_bar:
                    progress = completed_count / total_to_test
                    progress_bar.progress(progress)
                
                # 예상 시간 계산 및 표시
                if status_text:
                    elapsed = time.time() - start_time
                    if completed_count > 0:
                        avg_time_per_combo = elapsed / completed_count
                        remaining = (total_to_test - completed_count) * avg_time_per_combo
                        
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
                            f"완료: {completed_count}/{total_to_test} | "
                            f"경과: {elapsed_str} | 남은 시간: {remaining_str} | "
                            f"현재 최고: {best_max_failures}개 불일치 | "
                            f"병렬 작업자: {max_workers}개"
                        )
                
                # 진행 상황 업데이트
                if 'simulation_progress' not in st.session_state:
                    st.session_state.simulation_progress = {}
                combo_key = f"{ws}_{mi}_{cs}"
                st.session_state.simulation_progress[combo_key] = {
                    'completed': True,
                    'result': result is not None,
                    'window_size': ws,
                    'max_interval': mi,
                    'confidence_skip_threshold': cs
                }
                    
            except Exception as e:
                # 에러는 메인 프로세스에서만 표시 가능
                if status_text:
                    status_text.text(f"조합 {combo} 처리 중 오류 발생 (계속 진행)")
                # 에러 발생 시에도 계속 진행
                pass
    
    return {
        'results': results,
        'tested_combinations': tested_combinations,
        'total_combinations_possible': total_possible,
        'total_tested': len(results),  # 실제 테스트한 개수
        'total_planned': total_to_test,  # 원래 계획된 테스트 개수
        'successful': len(results),
        'failed': total_to_test - len(results),
        'meaningful_count': meaningful_count,  # 유의미한 결과 개수
        'early_stopped': False,  # 병렬 처리에서는 조기 종료가 어려움
        'search_method': 'random_multi_dimensional_parallel',
        'best_so_far': best_so_far,
        'parallel_workers': max_workers  # 사용된 병렬 작업자 수
    }

def batch_simulate_threshold_range(
    cutoff_id,
    window_size=7,
    method="빈도 기반",
    use_threshold=True,
    main_threshold=56,
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

def hybrid_search_optimal_threshold(
    cutoff_id,
    window_size=7,
    method="빈도 기반",
    use_threshold=True,
    main_threshold=56,
    max_interval=6,
    min_skip_threshold=50.5,
    max_skip_threshold=51.5,
    step=0.1,
    target_max_failures=5,
    tolerance=1.0,
    max_binary_iterations=10,
    progress_bar=None,
    status_text=None
):
    """
    하이브리드 탐색: 이진 탐색으로 범위를 좁힌 후 순차 그리드 서치로 정밀 탐색
    
    Args:
        cutoff_id: 기준 grid_string ID
        window_size: 윈도우 크기
        method: 예측 방법
        use_threshold: 임계값 전략 사용 여부
        main_threshold: 메인 임계값
        max_interval: 최대 예측 없음 간격
        min_skip_threshold: 최소 스킵 임계값
        max_skip_threshold: 최대 스킵 임계값
        step: 최종 정밀 탐색 시 임계값 간격
        target_max_failures: 목표 최대 연속 불일치
        tolerance: 이진 탐색 종료 허용 오차
        max_binary_iterations: 최대 이진 탐색 반복 횟수
        progress_bar: Streamlit progress bar 객체 (선택적)
        status_text: Streamlit status text 객체 (선택적)
    
    Returns:
        dict: 모든 임계값의 시뮬레이션 결과 (batch_simulate_threshold_range와 동일한 형식)
    """
    # 1단계: 이진 탐색으로 범위 좁히기
    left = min_skip_threshold
    right = max_skip_threshold
    iteration = 0
    best_threshold = None
    best_result = None
    binary_search_history = []
    
    if status_text:
        status_text.text(f"1단계: 이진 탐색 시작 (범위: {left:.1f}% ~ {right:.1f}%)")
    
    # 이진 탐색 실행
    while (right - left) > tolerance and iteration < max_binary_iterations:
        iteration += 1
        mid = round((left + right) / 2, 1)
        
        if status_text:
            status_text.text(
                f"이진 탐색 {iteration}회차: 임계값 {mid}% 테스트 중... "
                f"(범위: {left:.1f}% ~ {right:.1f}%)"
            )
        
        if progress_bar:
            # 전체 진행률의 30%까지는 이진 탐색
            progress_bar.progress((iteration / max_binary_iterations) * 0.3)
        
        # 중간값 테스트
        result = simulate_single_threshold(
            cutoff_id,
            mid,
            window_size=window_size,
            method=method,
            use_threshold=use_threshold,
            main_threshold=main_threshold,
            max_interval=max_interval
        )
        
        if result:
            max_failures = result.get('max_consecutive_failures', 999)
            binary_search_history.append({
                'threshold': mid,
                'max_consecutive_failures': max_failures,
                'range': (left, right)
            })
            
            if max_failures <= target_max_failures:
                # 조건 만족 - 더 낮은 임계값 시도 (왼쪽 탐색)
                best_threshold = mid
                best_result = result
                right = mid
            else:
                # 조건 불만족 - 더 높은 임계값 시도 (오른쪽 탐색)
                left = mid
    
    # 2단계: 찾은 범위에서 순차 그리드 서치
    if best_threshold is not None:
        # best_threshold 주변으로 탐색 범위 설정
        fine_min = max(min_skip_threshold, best_threshold - tolerance)
        fine_max = min(max_skip_threshold, best_threshold + tolerance)
        
        # 범위를 step 단위로 정렬
        fine_min = round((fine_min // step) * step, 1)
        fine_max = round((fine_max // step) * step, 1)
    else:
        # 조건을 만족하는 임계값을 찾지 못한 경우, 전체 범위에서 순차 탐색
        fine_min = min_skip_threshold
        fine_max = max_skip_threshold
        if status_text:
            status_text.text("⚠️ 이진 탐색에서 조건을 만족하는 임계값을 찾지 못했습니다. 전체 범위에서 순차 탐색합니다.")
    
    if status_text:
        status_text.text(
            f"2단계: 순차 그리드 서치 시작 ({fine_min:.1f}% ~ {fine_max:.1f}%)"
        )
    
    # 순차 그리드 서치 실행
    thresholds = []
    current = fine_min
    while current <= fine_max + 0.001:  # 부동소수점 오차 고려
        thresholds.append(round(current, 1))
        current += step
    
    results = []
    total = len(thresholds)
    start_time = time.time()
    binary_steps = iteration
    
    for idx, threshold in enumerate(thresholds):
        threshold_start = time.time()
        
        if status_text:
            status_text.text(
                f"순차 탐색: 임계값 {threshold}% 테스트 중... "
                f"({idx+1}/{total})"
            )
        
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
        
        # 프로그레스 바 업데이트 (30% ~ 100%)
        if progress_bar:
            progress = 0.3 + ((idx + 1) / total) * 0.7
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
                    f"순차 탐색: {threshold}% 완료 ({idx+1}/{total}) | "
                    f"경과: {elapsed_str} | 예상 남은 시간: {remaining_str} | "
                    f"이진 탐색: {binary_steps}회"
                )
        
        # 진행 상황 업데이트
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
        'failed': total - len(results),
        'binary_search_steps': binary_steps,
        'binary_search_history': binary_search_history,
        'search_method': 'hybrid'
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

def find_optimal_multi_dimensional(simulation_results):
    """
    다차원 최적화 결과에서 최적 조합 선정 알고리즘
    - 1차 필터링: 최대 연속 불일치가 5 이하인 조합만 선별
    - 2차 정렬: 최대 연속 불일치가 가장 낮은 순, 동일하면 스킵 횟수가 적은 순
    
    Args:
        simulation_results: random_search_multi_dimensional()의 반환값
    
    Returns:
        dict: 최적 조합 정보 및 추천 결과
    """
    if not simulation_results or len(simulation_results.get('results', [])) == 0:
        return {
            'optimal_combination': None,
            'candidates': [],
            'all_results': []
        }
    
    all_results = simulation_results['results']
    
    # 1차 필터링: 최대 연속 불일치가 5 이하인 조합만 선별 (최우선 조건)
    candidates = [
        r for r in all_results
        if r.get('max_consecutive_failures', 999) <= 5
    ]
    
    if len(candidates) == 0:
        # 최대 연속 불일치 5 이하인 조합이 없으면 전체 결과에서 선택
        all_results_sorted = sorted(
            all_results,
            key=lambda x: (
                x.get('max_consecutive_failures', 999),
                x.get('total_skipped_predictions', 999999),
                -x.get('avg_accuracy', 0.0)
            )
        )
        optimal_result = all_results_sorted[0] if all_results_sorted else None
        warning_msg = '최대 연속 불일치 5 이하를 만족하는 조합이 없습니다. 전체 결과 중 최선의 조합을 선택했습니다.'
        
        if optimal_result:
            optimal_combination = {
                'window_size': optimal_result.get('window_size'),
                'max_interval': optimal_result.get('max_interval'),
                'confidence_skip_threshold': optimal_result.get('confidence_skip_threshold'),
                'result': optimal_result
            }
        else:
            optimal_combination = None
        
        return {
            'optimal_combination': optimal_combination,
            'candidates': [],
            'all_results': all_results,
            'warning': warning_msg
        }
    
    # 2차 필터링: 유의미한 결과 우선 선택 (선택적)
    # 유의미한 결과 조건:
    # - 예측률 >= 10% (너무 적게 예측한 경우 제외)
    # - 스킵 비율 <= 90% (너무 많이 스킵한 경우 제외)
    # - 총 예측 횟수 >= 10회 (통계적으로 의미 있는 최소 예측 횟수)
    meaningful_candidates = [
        r for r in candidates
        if r.get('prediction_rate', 0.0) >= 10.0
        and r.get('avg_skip_rate', 100.0) <= 90.0
        and r.get('total_predictions', 0) >= 10
    ]
    
    # 유의미한 결과가 있으면 그 중에서 선택, 없으면 전체 후보에서 선택
    final_candidates = meaningful_candidates if len(meaningful_candidates) > 0 else candidates
    
    # 3차 정렬: 최대 연속 불일치가 가장 낮은 순, 동일하면 스킵 횟수가 적은 순, 동일하면 정확도가 높은 순
    candidates_sorted = sorted(
        final_candidates,
        key=lambda x: (
            x.get('max_consecutive_failures', 999),
            x.get('total_skipped_predictions', 999999),
            -x.get('avg_accuracy', 0.0)
        )
    )
    
    optimal_result = candidates_sorted[0]
    optimal_combination = {
        'window_size': optimal_result.get('window_size'),
        'max_interval': optimal_result.get('max_interval'),
        'confidence_skip_threshold': optimal_result.get('confidence_skip_threshold'),
        'result': optimal_result
    }
    
    # 유의미한 결과가 없었는지 확인 (경고 메시지용)
    warning_msg = None
    if len(meaningful_candidates) == 0 and len(candidates) > 0:
        warning_msg = '⚠️ 최대 연속 불일치 5 이하 조건은 만족하지만, 유의미한 결과 조건(예측률 ≥10%, 스킵 비율 ≤90%, 최소 10회 예측)을 만족하지 않아 전체 후보 중에서 선택했습니다.'
    
    return {
        'optimal_combination': optimal_combination,
        'candidates': candidates_sorted,
        'all_results': all_results,
        'candidate_count': len(candidates),
        'warning': warning_msg
    }

def save_multi_dimensional_simulation_results(
    cutoff_id,
    window_size_range,
    max_interval_range,
    confidence_skip_range,
    num_samples,
    method,
    use_threshold,
    main_threshold,
    simulation_results,
    optimal_result
):
    """
    다차원 최적화 시뮬레이션 결과 DB 저장
    
    Args:
        cutoff_id: 기준 grid_string ID
        window_size_range: (min, max) 튜플
        max_interval_range: (min, max) 튜플
        confidence_skip_range: (min, max, step) 튜플
        num_samples: 샘플링 개수
        method: 예측 방법
        use_threshold: 임계값 전략 사용 여부
        main_threshold: 메인 임계값
        simulation_results: random_search_multi_dimensional()의 반환값
        optimal_result: find_optimal_multi_dimensional()의 반환값
    
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
        validation_id = str(uuid.uuid4())
        window_size_min, window_size_max = window_size_range
        max_interval_min, max_interval_max = max_interval_range
        skip_min, skip_max, skip_step = confidence_skip_range
        
        # 세션 저장
        # 다차원 모드에서는 window_size가 없으므로 기본값으로 범위의 중간값 사용 (제약 조건 우회)
        default_window_size = (window_size_min + window_size_max) // 2 if window_size_max > window_size_min else window_size_min
        default_max_interval = (max_interval_min + max_interval_max) // 2 if max_interval_max > max_interval_min else max_interval_min
        
        cursor.execute('''
            INSERT INTO optimal_threshold_simulation_sessions (
                validation_id, cutoff_grid_string_id, window_size, method,
                use_threshold, threshold, max_interval,
                search_method, window_size_min, window_size_max,
                max_interval_min, max_interval_max,
                min_skip_threshold, max_skip_threshold, step, num_samples,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'multi_dimensional', ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
        ''', (
            validation_id, cutoff_id, default_window_size, method, use_threshold,
            main_threshold if use_threshold else None, default_max_interval,
            window_size_min, window_size_max,
            max_interval_min, max_interval_max,
            skip_min, skip_max, skip_step, num_samples
        ))
        
        # 각 조합별 결과 저장
        # 다차원 시뮬레이션에서는 같은 validation_id와 confidence_skip_threshold가 
        # 다른 window_size, max_interval 조합에서 반복될 수 있으므로
        # 기존 validation_id의 모든 레코드를 먼저 삭제하고 새로 저장
        cursor.execute('DELETE FROM optimal_threshold_simulation_results WHERE validation_id = ?', (validation_id,))
        
        for result in simulation_results.get('results', []):
            # 새 레코드 삽입 (중복 체크 불필요, 위에서 이미 삭제했으므로)
            cursor.execute('''
                INSERT INTO optimal_threshold_simulation_results (
                    validation_id, confidence_skip_threshold, window_size, max_interval,
                    max_consecutive_failures, avg_max_consecutive_failures,
                    total_skipped_predictions, avg_skip_rate,
                    below_5_ratio, avg_accuracy, prediction_rate,
                    total_grid_strings, total_steps, total_failures, total_predictions,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
            ''', (
                validation_id,
                result.get('confidence_skip_threshold'),
                result.get('window_size'),
                result.get('max_interval'),
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
        
        conn.commit()
        return validation_id
        
    except Exception as e:
        conn.rollback()
        st.error(f"다차원 시뮬레이션 결과 저장 중 오류: {str(e)}")
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
        return None
    finally:
        conn.close()

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
                search_method, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'single', datetime('now', '+9 hours'))
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
        # 먼저 테이블 생성 및 마이그레이션 실행
        create_simulation_tables()
        
        # 컬럼 존재 여부 확인
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(optimal_threshold_simulation_sessions)")
        columns_info = cursor.fetchall()
        existing_columns = [row[1] for row in columns_info]
        
        # search_method 컬럼이 없으면 기본값으로 조회
        if 'search_method' not in existing_columns:
            # 컬럼이 없을 때는 search_method를 선택하지 않음
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
                    'single' as search_method,
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
        else:
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
                    s.search_method,
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
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
        return pd.DataFrame()
    finally:
        conn.close()

def load_latest_multi_dimensional_result(cutoff_id=None):
    """
    최근 다차원 최적화 결과 1개 로드
    
    Args:
        cutoff_id: 기준 grid_string ID (None이면 전체에서 최근 결과)
    
    Returns:
        dict: 최근 결과 정보 또는 None
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        if cutoff_id is None:
            query = """
                SELECT validation_id
                FROM optimal_threshold_simulation_sessions
                WHERE search_method = 'multi_dimensional'
                ORDER BY created_at DESC
                LIMIT 1
            """
            params = []
        else:
            query = """
                SELECT validation_id
                FROM optimal_threshold_simulation_sessions
                WHERE search_method = 'multi_dimensional'
                  AND cutoff_grid_string_id = ?
                ORDER BY created_at DESC
                LIMIT 1
            """
            params = [cutoff_id]
        
        df = pd.read_sql_query(query, conn, params=params)
        
        if len(df) == 0:
            return None
        
        validation_id = df.iloc[0]['validation_id']
        
        # 세션 정보 로드
        session_query = """
            SELECT *
            FROM optimal_threshold_simulation_sessions
            WHERE validation_id = ?
        """
        session_df = pd.read_sql_query(session_query, conn, params=[validation_id])
        
        if len(session_df) == 0:
            return None
        
        session_info = session_df.iloc[0].to_dict()
        
        # 최적 결과 로드
        optimal_query = """
            SELECT *
            FROM optimal_threshold_simulation_results
            WHERE validation_id = ?
            ORDER BY max_consecutive_failures ASC, total_skipped_predictions ASC
            LIMIT 1
        """
        optimal_df = pd.read_sql_query(optimal_query, conn, params=[validation_id])
        
        if len(optimal_df) > 0:
            session_info['optimal_result'] = optimal_df.iloc[0].to_dict()
        
        # 전체 결과 개수
        count_query = "SELECT COUNT(*) as count FROM optimal_threshold_simulation_results WHERE validation_id = ?"
        count_df = pd.read_sql_query(count_query, conn, params=[validation_id])
        session_info['total_results'] = count_df.iloc[0]['count'] if len(count_df) > 0 else 0
        
        return session_info
        
    except Exception as e:
        st.error(f"최근 다차원 결과 로드 오류: {str(e)}")
        return None
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

def get_multi_window_prediction(
    grid_string,
    position,
    window_sizes=[5, 6, 7, 8, 9],
    method="빈도 기반",
    threshold=0
):
    """
    여러 윈도우 크기 중 가장 높은 신뢰도를 가진 예측값 선택
    
    Args:
        grid_string: 전체 문자열
        position: 예측할 위치 (0-based index)
        window_sizes: 테스트할 윈도우 크기 리스트
        method: 예측 방법
        threshold: 임계값
    
    Returns:
        dict: {
            'predicted': 'b' 또는 'p',
            'confidence': 최고 신뢰도,
            'window_size': 선택된 윈도우 크기,
            'prefix': 사용된 prefix,
            'all_predictions': 모든 윈도우 크기별 예측 결과
        }
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        all_predictions = []
        
        # 각 윈도우 크기별로 예측 조회
        for window_size in window_sizes:
            prefix_length = window_size - 1
            
            # prefix 생성 (현재 위치 이전의 문자열에서)
            if position < prefix_length:
                # prefix가 부족하면 None
                continue
            
            prefix = grid_string[position - prefix_length:position]
            
            # stored_predictions에서 조회
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
                all_predictions.append({
                    'window_size': window_size,
                    'prefix': prefix,
                    'predicted': row['predicted_value'],
                    'confidence': row['confidence'],
                    'b_ratio': row['b_ratio'],
                    'p_ratio': row['p_ratio']
                })
        
        if len(all_predictions) == 0:
            return {
                'predicted': None,
                'confidence': 0.0,
                'window_size': None,
                'prefix': None,
                'all_predictions': []
            }
        
        # 신뢰도가 가장 높은 예측 선택
        best_prediction = max(all_predictions, key=lambda x: x['confidence'])
        
        return {
            'predicted': best_prediction['predicted'],
            'confidence': best_prediction['confidence'],
            'window_size': best_prediction['window_size'],
            'prefix': best_prediction['prefix'],
            'all_predictions': all_predictions
        }
        
    except Exception as e:
        st.error(f"다중 윈도우 예측 조회 오류: {str(e)}")
        return None
    finally:
        conn.close()

def validate_multi_window_scenario(
    grid_string_id,
    cutoff_grid_string_id,
    window_sizes=[5, 6, 7, 8, 9],
    method="빈도 기반",
    threshold=0
):
    """
    다중 윈도우 크기 전략으로 검증
    
    각 위치에서 여러 윈도우 크기 중 최고 신뢰도 예측값 선택
    
    Args:
        grid_string_id: 검증할 grid_string ID
        cutoff_grid_string_id: 학습 데이터 기준 ID
        window_sizes: 테스트할 윈도우 크기 리스트
        method: 예측 방법
        threshold: 임계값
    
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
        max_window_size = max(window_sizes)
        
        if len(grid_string) < max_window_size:
            return {
                'grid_string_id': grid_string_id,
                'max_consecutive_failures': 0,
                'total_steps': 0,
                'total_failures': 0,
                'total_predictions': 0,
                'accuracy': 0.0,
                'history': []
            }
        
        # 검증 시작
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        
        # 각 위치에서 예측
        for position in range(max_window_size - 1, len(grid_string)):
            total_steps += 1
            actual_value = grid_string[position]
            
            # 다중 윈도우 예측 수행
            prediction_result = get_multi_window_prediction(
                grid_string,
                position,
                window_sizes=window_sizes,
                method=method,
                threshold=threshold
            )
            
            predicted_value = prediction_result.get('predicted') if prediction_result else None
            confidence = prediction_result.get('confidence', 0.0) if prediction_result else 0.0
            selected_window_size = prediction_result.get('window_size') if prediction_result else None
            prefix = prediction_result.get('prefix') if prediction_result else None
            all_predictions = prediction_result.get('all_predictions', []) if prediction_result else []
            
            # 예측값이 있으면 검증
            if predicted_value is not None:
                is_correct = predicted_value == actual_value
                total_predictions += 1
                
                if not is_correct:
                    consecutive_failures += 1
                    total_failures += 1
                    if consecutive_failures > max_consecutive_failures:
                        max_consecutive_failures = consecutive_failures
                else:
                    consecutive_failures = 0
                
                history.append({
                    'step': total_steps,
                    'position': position,
                    'prefix': prefix,
                    'predicted': predicted_value,
                    'actual': actual_value,
                    'is_correct': is_correct,
                    'confidence': confidence,
                    'selected_window_size': selected_window_size,
                    'all_predictions': all_predictions
                })
            else:
                # 예측값이 없음
                history.append({
                    'step': total_steps,
                    'position': position,
                    'prefix': None,
                    'predicted': None,
                    'actual': actual_value,
                    'is_correct': None,
                    'confidence': 0.0,
                    'selected_window_size': None,
                    'all_predictions': []
                })
        
        # 정확도 계산
        accuracy = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        
        return {
            'grid_string_id': grid_string_id,
            'max_consecutive_failures': max_consecutive_failures,
            'total_steps': total_steps,
            'total_failures': total_failures,
            'total_predictions': total_predictions,
            'accuracy': accuracy,
            'history': history
        }
        
    except Exception as e:
        st.error(f"다중 윈도우 검증 오류: {str(e)}")
        return None
    finally:
        conn.close()

def batch_validate_multi_window_scenario(
    cutoff_grid_string_id,
    window_sizes=[5, 6, 7, 8, 9],
    method="빈도 기반",
    threshold=0
):
    """
    다중 윈도우 크기 전략으로 배치 검증
    
    Args:
        cutoff_grid_string_id: 기준 grid_string ID
        window_sizes: 테스트할 윈도우 크기 리스트
        method: 예측 방법
        threshold: 임계값
    
    Returns:
        dict: 배치 검증 결과
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # cutoff 이후의 모든 grid_string 로드
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
                    'total_predictions': 0
                }
            }
        
        grid_string_ids = df['id'].tolist()
        results = []
        
        # 각 grid_string에 대해 검증
        for grid_string_id in grid_string_ids:
            result = validate_multi_window_scenario(
                grid_string_id,
                cutoff_grid_string_id,
                window_sizes=window_sizes,
                method=method,
                threshold=threshold
            )
            
            if result is not None:
                results.append(result)
        
        # 요약 통계 계산
        if len(results) > 0:
            total_grid_strings = len(results)
            avg_accuracy = sum(r['accuracy'] for r in results) / total_grid_strings
            max_consecutive_failures = max(r['max_consecutive_failures'] for r in results)
            avg_max_consecutive_failures = sum(r['max_consecutive_failures'] for r in results) / total_grid_strings
            total_steps = sum(r['total_steps'] for r in results)
            total_failures = sum(r['total_failures'] for r in results)
            total_predictions = sum(r['total_predictions'] for r in results)
            
            summary = {
                'total_grid_strings': total_grid_strings,
                'avg_accuracy': avg_accuracy,
                'max_consecutive_failures': max_consecutive_failures,
                'avg_max_consecutive_failures': avg_max_consecutive_failures,
                'total_steps': total_steps,
                'total_failures': total_failures,
                'total_predictions': total_predictions
            }
        else:
            summary = {
                'total_grid_strings': 0,
                'avg_accuracy': 0.0,
                'max_consecutive_failures': 0,
                'avg_max_consecutive_failures': 0.0,
                'total_steps': 0,
                'total_failures': 0,
                'total_predictions': 0
            }
        
        return {
            'results': results,
            'summary': summary,
            'grid_string_ids': grid_string_ids
        }
        
    except Exception as e:
        st.error(f"배치 검증 오류: {str(e)}")
        return None
    finally:
        conn.close()

def get_multi_window_prediction_with_exclusion(
    grid_string,
    position,
    window_sizes=[5, 6, 7, 8, 9],
    method="빈도 기반",
    threshold=0,
    excluded_window_sizes=[]
):
    """
    제외된 윈도우 크기를 제외하고 최고 신뢰도 예측값 선택
    
    Args:
        grid_string: 전체 문자열
        position: 예측할 위치 (0-based index)
        window_sizes: 테스트할 윈도우 크기 리스트
        method: 예측 방법
        threshold: 임계값
        excluded_window_sizes: 제외할 윈도우 크기 리스트
    
    Returns:
        dict: {
            'predicted': 'b' 또는 'p',
            'confidence': 최고 신뢰도,
            'window_size': 선택된 윈도우 크기,
            'prefix': 사용된 prefix,
            'all_predictions': 모든 윈도우 크기별 예측 결과
        }
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        all_predictions = []
        
        # 제외된 윈도우 크기를 제외한 사용 가능한 윈도우 크기
        available_window_sizes = [ws for ws in window_sizes if ws not in excluded_window_sizes]
        
        # 사용 가능한 윈도우 크기가 없으면 제외 리스트 무시
        if len(available_window_sizes) == 0:
            available_window_sizes = window_sizes
        
        # 각 윈도우 크기별로 예측 조회
        for window_size in available_window_sizes:
            prefix_length = window_size - 1
            
            # prefix 생성 (현재 위치 이전의 문자열에서)
            if position < prefix_length:
                # prefix가 부족하면 None
                continue
            
            prefix = grid_string[position - prefix_length:position]
            
            # stored_predictions에서 조회
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
                all_predictions.append({
                    'window_size': window_size,
                    'prefix': prefix,
                    'predicted': row['predicted_value'],
                    'confidence': row['confidence'],
                    'b_ratio': row['b_ratio'],
                    'p_ratio': row['p_ratio']
                })
        
        if len(all_predictions) == 0:
            return {
                'predicted': None,
                'confidence': 0.0,
                'window_size': None,
                'prefix': None,
                'all_predictions': []
            }
        
        # 신뢰도가 가장 높은 예측 선택
        best_prediction = max(all_predictions, key=lambda x: x['confidence'])
        
        return {
            'predicted': best_prediction['predicted'],
            'confidence': best_prediction['confidence'],
            'window_size': best_prediction['window_size'],
            'prefix': best_prediction['prefix'],
            'all_predictions': all_predictions
        }
        
    except Exception as e:
        st.error(f"다중 윈도우 예측 조회 오류 (제외 전략): {str(e)}")
        return None
    finally:
        conn.close()

def get_multi_window_prediction_with_exclusion_and_confidence_skip(
    grid_string,
    position,
    window_sizes=[5, 6, 7, 8, 9],
    method="빈도 기반",
    threshold=0,
    excluded_window_sizes=[],
    confidence_skip_threshold=None
):
    """
    제외된 윈도우 크기를 제외하고 신뢰도 스킵 임계값을 적용하여 최고 신뢰도 예측값 선택
    
    Args:
        grid_string: 전체 문자열
        position: 예측할 위치 (0-based index)
        window_sizes: 테스트할 윈도우 크기 리스트
        method: 예측 방법
        threshold: 임계값
        excluded_window_sizes: 제외할 윈도우 크기 리스트
        confidence_skip_threshold: 신뢰도 스킵 임계값 (None이면 스킵 없음)
    
    Returns:
        dict: {
            'predicted': 'b' 또는 'p',
            'confidence': 최고 신뢰도,
            'window_size': 선택된 윈도우 크기,
            'prefix': 사용된 prefix,
            'all_predictions': 모든 윈도우 크기별 예측 결과,
            'skipped': 스킵 여부
        }
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        all_predictions = []
        
        # 제외된 윈도우 크기를 제외한 사용 가능한 윈도우 크기
        available_window_sizes = [ws for ws in window_sizes if ws not in excluded_window_sizes]
        
        # 사용 가능한 윈도우 크기가 없으면 제외 리스트 무시
        if len(available_window_sizes) == 0:
            available_window_sizes = window_sizes
        
        # 각 윈도우 크기별로 예측 조회
        for window_size in available_window_sizes:
            prefix_length = window_size - 1
            
            # prefix 생성 (현재 위치 이전의 문자열에서)
            if position < prefix_length:
                # prefix가 부족하면 None
                continue
            
            prefix = grid_string[position - prefix_length:position]
            
            # stored_predictions에서 조회
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
                confidence = row['confidence']
                
                # 신뢰도 스킵 임계값 체크 (신뢰도가 임계값 이상인 경우만 포함)
                if confidence_skip_threshold is None or confidence >= confidence_skip_threshold:
                    all_predictions.append({
                        'window_size': window_size,
                        'prefix': prefix,
                        'predicted': row['predicted_value'],
                        'confidence': confidence,
                        'b_ratio': row['b_ratio'],
                        'p_ratio': row['p_ratio']
                    })
        
        if len(all_predictions) == 0:
            return {
                'predicted': None,
                'confidence': 0.0,
                'window_size': None,
                'prefix': None,
                'all_predictions': [],
                'skipped': True
            }
        
        # 신뢰도가 가장 높은 예측 선택
        best_prediction = max(all_predictions, key=lambda x: x['confidence'])
        
        return {
            'predicted': best_prediction['predicted'],
            'confidence': best_prediction['confidence'],
            'window_size': best_prediction['window_size'],
            'prefix': best_prediction['prefix'],
            'all_predictions': all_predictions,
            'skipped': False
        }
        
    except Exception as e:
        st.error(f"다중 윈도우 예측 조회 오류 (제외 전략 + 신뢰도 스킵): {str(e)}")
        return None
    finally:
        conn.close()

def validate_multi_window_scenario_with_exclusion(
    grid_string_id,
    cutoff_grid_string_id,
    window_sizes=[5, 6, 7, 8, 9],
    method="빈도 기반",
    threshold=0
):
    """
    실패한 윈도우 크기를 제외하는 전략으로 검증
    - 실패한 윈도우 크기는 즉시 제외
    - 성공하면 해당 윈도우 크기 즉시 복구
    - 불일치 구간 종료 시 (성공 발생 시) 모든 제외 리스트 초기화
    
    Args:
        grid_string_id: 검증할 grid_string ID
        cutoff_grid_string_id: 학습 데이터 기준 ID
        window_sizes: 테스트할 윈도우 크기 리스트
        method: 예측 방법
        threshold: 임계값
    
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
        max_window_size = max(window_sizes)
        
        if len(grid_string) < max_window_size:
            return {
                'grid_string_id': grid_string_id,
                'max_consecutive_failures': 0,
                'total_steps': 0,
                'total_failures': 0,
                'total_predictions': 0,
                'accuracy': 0.0,
                'history': []
            }
        
        # 검증 시작
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        
        # 제외된 윈도우 크기 관리
        excluded_window_sizes = set()  # 실패한 윈도우 크기 집합
        
        # 각 위치에서 예측
        for position in range(max_window_size - 1, len(grid_string)):
            total_steps += 1
            actual_value = grid_string[position]
            
            # 예측 전 제외 리스트 상태 저장 (히스토리 기록용)
            excluded_before_prediction = list(excluded_window_sizes.copy())
            
            # 제외된 윈도우 크기를 제외하고 예측 수행
            prediction_result = get_multi_window_prediction_with_exclusion(
                grid_string,
                position,
                window_sizes=window_sizes,
                method=method,
                threshold=threshold,
                excluded_window_sizes=excluded_before_prediction
            )
            
            predicted_value = prediction_result.get('predicted') if prediction_result else None
            confidence = prediction_result.get('confidence', 0.0) if prediction_result else 0.0
            selected_window_size = prediction_result.get('window_size') if prediction_result else None
            prefix = prediction_result.get('prefix') if prediction_result else None
            all_predictions = prediction_result.get('all_predictions', []) if prediction_result else []
            
            # 예측값이 있으면 검증
            if predicted_value is not None:
                is_correct = predicted_value == actual_value
                total_predictions += 1
                
                if not is_correct:
                    consecutive_failures += 1
                    total_failures += 1
                    if consecutive_failures > max_consecutive_failures:
                        max_consecutive_failures = consecutive_failures
                    
                    # 실패한 윈도우 크기를 제외 리스트에 추가
                    if selected_window_size:
                        excluded_window_sizes.add(selected_window_size)
                else:
                    # 성공한 경우
                    consecutive_failures = 0
                    
                    # 복구 메커니즘 1: 성공한 윈도우 크기는 즉시 복구
                    if selected_window_size and selected_window_size in excluded_window_sizes:
                        excluded_window_sizes.remove(selected_window_size)
                    
                    # 복구 메커니즘 2: 불일치 구간 종료 시 모든 제외 리스트 초기화
                    # (이전 스텝이 실패였고 현재 스텝이 성공이면 구간 종료)
                    if len(history) > 0 and history[-1].get('is_correct') is False:
                        # 불일치 구간이 종료되었으므로 모든 제외 리스트 초기화
                        excluded_window_sizes.clear()
                
                # 히스토리 기록 (예측 전 제외 리스트 상태 기록)
                history.append({
                    'step': total_steps,
                    'position': position,
                    'prefix': prefix,
                    'predicted': predicted_value,
                    'actual': actual_value,
                    'is_correct': is_correct,
                    'confidence': confidence,
                    'selected_window_size': selected_window_size,
                    'excluded_window_sizes': excluded_before_prediction,  # 예측 시 사용된 제외 리스트 (예측 전 상태)
                    'all_predictions': all_predictions
                })
            else:
                # 예측값이 없음
                history.append({
                    'step': total_steps,
                    'position': position,
                    'prefix': None,
                    'predicted': None,
                    'actual': actual_value,
                    'is_correct': None,
                    'confidence': 0.0,
                    'selected_window_size': None,
                    'excluded_window_sizes': excluded_before_prediction,  # 예측 시 사용된 제외 리스트
                    'all_predictions': []
                })
        
        # 정확도 계산
        accuracy = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        
        return {
            'grid_string_id': grid_string_id,
            'max_consecutive_failures': max_consecutive_failures,
            'total_steps': total_steps,
            'total_failures': total_failures,
            'total_predictions': total_predictions,
            'accuracy': accuracy,
            'history': history
        }
        
    except Exception as e:
        st.error(f"다중 윈도우 검증 오류 (제외 전략): {str(e)}")
        return None
    finally:
        conn.close()

def validate_multi_window_scenario_with_exclusion_and_confidence_skip(
    grid_string_id,
    cutoff_grid_string_id,
    window_sizes=[5, 6, 7, 8, 9],
    method="빈도 기반",
    threshold=0,
    confidence_skip_threshold=None
):
    """
    제외 전략 + 신뢰도 스킵 전략으로 검증
    - 실패한 윈도우 크기는 즉시 제외
    - 성공하면 해당 윈도우 크기 즉시 복구
    - 불일치 구간 종료 시 (성공 발생 시) 모든 제외 리스트 초기화
    - 신뢰도가 임계값 미만인 예측은 스킵
    
    Args:
        grid_string_id: 검증할 grid_string ID
        cutoff_grid_string_id: 학습 데이터 기준 ID
        window_sizes: 테스트할 윈도우 크기 리스트
        method: 예측 방법
        threshold: 임계값
        confidence_skip_threshold: 신뢰도 스킵 임계값 (None이면 스킵 없음)
    
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
        max_window_size = max(window_sizes)
        
        if len(grid_string) < max_window_size:
            return {
                'grid_string_id': grid_string_id,
                'max_consecutive_failures': 0,
                'total_steps': 0,
                'total_failures': 0,
                'total_predictions': 0,
                'total_skipped': 0,
                'accuracy': 0.0,
                'history': []
            }
        
        # 검증 시작
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0
        
        # 제외된 윈도우 크기 관리
        excluded_window_sizes = set()  # 실패한 윈도우 크기 집합
        
        # 각 위치에서 예측
        for position in range(max_window_size - 1, len(grid_string)):
            total_steps += 1
            actual_value = grid_string[position]
            
            # 예측 전 제외 리스트 상태 저장 (히스토리 기록용)
            excluded_before_prediction = list(excluded_window_sizes.copy())
            
            # 제외된 윈도우 크기를 제외하고 신뢰도 스킵 임계값도 적용하여 예측 수행
            prediction_result = get_multi_window_prediction_with_exclusion_and_confidence_skip(
                grid_string,
                position,
                window_sizes=window_sizes,
                method=method,
                threshold=threshold,
                excluded_window_sizes=excluded_before_prediction,
                confidence_skip_threshold=confidence_skip_threshold
            )
            
            predicted_value = prediction_result.get('predicted') if prediction_result else None
            confidence = prediction_result.get('confidence', 0.0) if prediction_result else 0.0
            selected_window_size = prediction_result.get('window_size') if prediction_result else None
            prefix = prediction_result.get('prefix') if prediction_result else None
            all_predictions = prediction_result.get('all_predictions', []) if prediction_result else []
            skipped = prediction_result.get('skipped', False) if prediction_result else False
            
            # 예측값이 있으면 검증
            if predicted_value is not None:
                is_correct = predicted_value == actual_value
                total_predictions += 1
                
                if not is_correct:
                    consecutive_failures += 1
                    total_failures += 1
                    if consecutive_failures > max_consecutive_failures:
                        max_consecutive_failures = consecutive_failures
                    
                    # 실패한 윈도우 크기를 제외 리스트에 추가
                    if selected_window_size:
                        excluded_window_sizes.add(selected_window_size)
                else:
                    # 성공한 경우
                    consecutive_failures = 0
                    
                    # 복구 메커니즘 1: 성공한 윈도우 크기는 즉시 복구
                    if selected_window_size and selected_window_size in excluded_window_sizes:
                        excluded_window_sizes.remove(selected_window_size)
                    
                    # 복구 메커니즘 2: 불일치 구간 종료 시 모든 제외 리스트 초기화
                    # (이전 스텝이 실패였고 현재 스텝이 성공이면 구간 종료)
                    if len(history) > 0 and history[-1].get('is_correct') is False:
                        # 불일치 구간이 종료되었으므로 모든 제외 리스트 초기화
                        excluded_window_sizes.clear()
                
                # 히스토리 기록 (예측 전 제외 리스트 상태 기록)
                history.append({
                    'step': total_steps,
                    'position': position,
                    'prefix': prefix,
                    'predicted': predicted_value,
                    'actual': actual_value,
                    'is_correct': is_correct,
                    'confidence': confidence,
                    'selected_window_size': selected_window_size,
                    'excluded_window_sizes': excluded_before_prediction,  # 예측 시 사용된 제외 리스트 (예측 전 상태)
                    'all_predictions': all_predictions,
                    'skipped': False
                })
            else:
                # 예측값이 없음 (스킵됨)
                if skipped:
                    total_skipped += 1
                
                history.append({
                    'step': total_steps,
                    'position': position,
                    'prefix': None,
                    'predicted': None,
                    'actual': actual_value,
                    'is_correct': None,
                    'confidence': 0.0,
                    'selected_window_size': None,
                    'excluded_window_sizes': excluded_before_prediction,  # 예측 시 사용된 제외 리스트
                    'all_predictions': [],
                    'skipped': skipped
                })
        
        # 정확도 계산
        accuracy = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        
        return {
            'grid_string_id': grid_string_id,
            'max_consecutive_failures': max_consecutive_failures,
            'total_steps': total_steps,
            'total_failures': total_failures,
            'total_predictions': total_predictions,
            'total_skipped': total_skipped,
            'accuracy': accuracy,
            'history': history
        }
        
    except Exception as e:
        st.error(f"다중 윈도우 검증 오류 (제외 전략 + 신뢰도 스킵): {str(e)}")
        return None
    finally:
        conn.close()

def batch_validate_multi_window_scenario_with_exclusion(
    cutoff_grid_string_id,
    window_sizes=[5, 6, 7, 8, 9],
    method="빈도 기반",
    threshold=0
):
    """
    제외 전략을 사용한 다중 윈도우 크기 배치 검증
    
    Args:
        cutoff_grid_string_id: 기준 grid_string ID
        window_sizes: 테스트할 윈도우 크기 리스트
        method: 예측 방법
        threshold: 임계값
    
    Returns:
        dict: 배치 검증 결과
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # cutoff 이후의 모든 grid_string 로드
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
                    'total_predictions': 0
                }
            }
        
        grid_string_ids = df['id'].tolist()
        results = []
        
        # 각 grid_string에 대해 검증
        for grid_string_id in grid_string_ids:
            result = validate_multi_window_scenario_with_exclusion(
                grid_string_id,
                cutoff_grid_string_id,
                window_sizes=window_sizes,
                method=method,
                threshold=threshold
            )
            
            if result is not None:
                results.append(result)
        
        # 요약 통계 계산
        if len(results) > 0:
            total_grid_strings = len(results)
            avg_accuracy = sum(r['accuracy'] for r in results) / total_grid_strings
            max_consecutive_failures = max(r['max_consecutive_failures'] for r in results)
            avg_max_consecutive_failures = sum(r['max_consecutive_failures'] for r in results) / total_grid_strings
            total_steps = sum(r['total_steps'] for r in results)
            total_failures = sum(r['total_failures'] for r in results)
            total_predictions = sum(r['total_predictions'] for r in results)
            
            summary = {
                'total_grid_strings': total_grid_strings,
                'avg_accuracy': avg_accuracy,
                'max_consecutive_failures': max_consecutive_failures,
                'avg_max_consecutive_failures': avg_max_consecutive_failures,
                'total_steps': total_steps,
                'total_failures': total_failures,
                'total_predictions': total_predictions
            }
        else:
            summary = {
                'total_grid_strings': 0,
                'avg_accuracy': 0.0,
                'max_consecutive_failures': 0,
                'avg_max_consecutive_failures': 0.0,
                'total_steps': 0,
                'total_failures': 0,
                'total_predictions': 0
            }
        
        return {
            'results': results,
            'summary': summary,
            'grid_string_ids': grid_string_ids
        }
        
    except Exception as e:
        st.error(f"배치 검증 오류 (제외 전략): {str(e)}")
        return None
    finally:
        conn.close()

def get_multi_window_prediction_with_confidence_skip(
    grid_string,
    position,
    window_sizes=[5, 6, 7, 8, 9],
    method="빈도 기반",
    threshold=0,
    confidence_skip_threshold=None
):
    """
    다중 윈도우 크기 예측 + 신뢰도 스킵 임계값 적용
    
    Args:
        grid_string: 전체 문자열
        position: 예측할 위치 (0-based index)
        window_sizes: 테스트할 윈도우 크기 리스트
        method: 예측 방법
        threshold: 임계값
        confidence_skip_threshold: 신뢰도 스킵 임계값 (None이면 스킵 없음)
    
    Returns:
        dict: {
            'predicted': 'b' 또는 'p',
            'confidence': 최고 신뢰도,
            'window_size': 선택된 윈도우 크기,
            'prefix': 사용된 prefix,
            'all_predictions': 모든 윈도우 크기별 예측 결과,
            'skipped': 스킵 여부
        }
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        all_predictions = []
        
        # 각 윈도우 크기별로 예측 조회
        for window_size in window_sizes:
            prefix_length = window_size - 1
            
            # prefix 생성 (현재 위치 이전의 문자열에서)
            if position < prefix_length:
                # prefix가 부족하면 None
                continue
            
            prefix = grid_string[position - prefix_length:position]
            
            # stored_predictions에서 조회
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
                confidence = row['confidence']
                
                # 신뢰도 스킵 임계값 체크
                if confidence_skip_threshold is None or confidence >= confidence_skip_threshold:
                    all_predictions.append({
                        'window_size': window_size,
                        'prefix': prefix,
                        'predicted': row['predicted_value'],
                        'confidence': confidence,
                        'b_ratio': row['b_ratio'],
                        'p_ratio': row['p_ratio']
                    })
        
        if len(all_predictions) == 0:
            return {
                'predicted': None,
                'confidence': 0.0,
                'window_size': None,
                'prefix': None,
                'all_predictions': [],
                'skipped': True
            }
        
        # 신뢰도가 가장 높은 예측 선택
        best_prediction = max(all_predictions, key=lambda x: x['confidence'])
        
        return {
            'predicted': best_prediction['predicted'],
            'confidence': best_prediction['confidence'],
            'window_size': best_prediction['window_size'],
            'prefix': best_prediction['prefix'],
            'all_predictions': all_predictions,
            'skipped': False
        }
        
    except Exception as e:
        st.error(f"다중 윈도우 예측 조회 오류 (신뢰도 스킵): {str(e)}")
        return None
    finally:
        conn.close()

def validate_multi_window_with_confidence_skip(
    grid_string_id,
    cutoff_grid_string_id,
    window_sizes=[5, 6, 7, 8, 9],
    method="빈도 기반",
    threshold=0,
    confidence_skip_threshold=None
):
    """
    다중 윈도우 크기 + 신뢰도 스킵 전략으로 검증
    
    Args:
        grid_string_id: 검증할 grid_string ID
        cutoff_grid_string_id: 학습 데이터 기준 ID
        window_sizes: 테스트할 윈도우 크기 리스트
        method: 예측 방법
        threshold: 임계값
        confidence_skip_threshold: 신뢰도 스킵 임계값 (None이면 스킵 없음)
    
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
        max_window_size = max(window_sizes)
        
        if len(grid_string) < max_window_size:
            return {
                'grid_string_id': grid_string_id,
                'max_consecutive_failures': 0,
                'total_steps': 0,
                'total_failures': 0,
                'total_predictions': 0,
                'total_skipped': 0,
                'accuracy': 0.0,
                'history': []
            }
        
        # 검증 시작
        history = []
        consecutive_failures = 0
        max_consecutive_failures = 0
        total_steps = 0
        total_failures = 0
        total_predictions = 0
        total_skipped = 0
        
        # 각 위치에서 예측
        for position in range(max_window_size - 1, len(grid_string)):
            total_steps += 1
            actual_value = grid_string[position]
            
            # 다중 윈도우 예측 수행 (신뢰도 스킵 적용)
            prediction_result = get_multi_window_prediction_with_confidence_skip(
                grid_string,
                position,
                window_sizes=window_sizes,
                method=method,
                threshold=threshold,
                confidence_skip_threshold=confidence_skip_threshold
            )
            
            predicted_value = prediction_result.get('predicted') if prediction_result else None
            confidence = prediction_result.get('confidence', 0.0) if prediction_result else 0.0
            selected_window_size = prediction_result.get('window_size') if prediction_result else None
            prefix = prediction_result.get('prefix') if prediction_result else None
            all_predictions = prediction_result.get('all_predictions', []) if prediction_result else []
            skipped = prediction_result.get('skipped', False) if prediction_result else False
            
            # 예측값이 있으면 검증
            if predicted_value is not None:
                is_correct = predicted_value == actual_value
                total_predictions += 1
                
                if not is_correct:
                    consecutive_failures += 1
                    total_failures += 1
                    if consecutive_failures > max_consecutive_failures:
                        max_consecutive_failures = consecutive_failures
                else:
                    consecutive_failures = 0
                
                history.append({
                    'step': total_steps,
                    'position': position,
                    'prefix': prefix,
                    'predicted': predicted_value,
                    'actual': actual_value,
                    'is_correct': is_correct,
                    'confidence': confidence,
                    'selected_window_size': selected_window_size,
                    'all_predictions': all_predictions,
                    'skipped': False
                })
            else:
                # 예측값이 없음 (스킵됨)
                if skipped:
                    total_skipped += 1
                
                history.append({
                    'step': total_steps,
                    'position': position,
                    'prefix': None,
                    'predicted': None,
                    'actual': actual_value,
                    'is_correct': None,
                    'confidence': 0.0,
                    'selected_window_size': None,
                    'all_predictions': [],
                    'skipped': skipped
                })
        
        # 정확도 계산
        accuracy = ((total_predictions - total_failures) / total_predictions * 100) if total_predictions > 0 else 0.0
        
        return {
            'grid_string_id': grid_string_id,
            'max_consecutive_failures': max_consecutive_failures,
            'total_steps': total_steps,
            'total_failures': total_failures,
            'total_predictions': total_predictions,
            'total_skipped': total_skipped,
            'accuracy': accuracy,
            'history': history
        }
        
    except Exception as e:
        st.error(f"다중 윈도우 검증 오류 (신뢰도 스킵): {str(e)}")
        return None
    finally:
        conn.close()

def batch_validate_multi_window_scenario_with_exclusion_and_confidence_skip(
    cutoff_grid_string_id,
    window_sizes=[5, 6, 7, 8, 9],
    method="빈도 기반",
    threshold=0,
    confidence_skip_threshold=None
):
    """
    제외 전략 + 신뢰도 스킵 전략을 사용한 다중 윈도우 크기 배치 검증
    
    Args:
        cutoff_grid_string_id: 기준 grid_string ID
        window_sizes: 테스트할 윈도우 크기 리스트
        method: 예측 방법
        threshold: 임계값
        confidence_skip_threshold: 신뢰도 스킵 임계값 (None이면 스킵 없음)
    
    Returns:
        dict: 배치 검증 결과
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # cutoff 이후의 모든 grid_string 로드
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
                    'total_skipped': 0
                }
            }
        
        grid_string_ids = df['id'].tolist()
        results = []
        
        # 각 grid_string에 대해 검증
        for grid_string_id in grid_string_ids:
            result = validate_multi_window_scenario_with_exclusion_and_confidence_skip(
                grid_string_id,
                cutoff_grid_string_id,
                window_sizes=window_sizes,
                method=method,
                threshold=threshold,
                confidence_skip_threshold=confidence_skip_threshold
            )
            
            if result is not None:
                results.append(result)
        
        # 요약 통계 계산
        if len(results) > 0:
            total_grid_strings = len(results)
            avg_accuracy = sum(r['accuracy'] for r in results) / total_grid_strings
            max_consecutive_failures = max(r['max_consecutive_failures'] for r in results)
            avg_max_consecutive_failures = sum(r['max_consecutive_failures'] for r in results) / total_grid_strings
            total_steps = sum(r['total_steps'] for r in results)
            total_failures = sum(r['total_failures'] for r in results)
            total_predictions = sum(r['total_predictions'] for r in results)
            total_skipped = sum(r['total_skipped'] for r in results)
            
            summary = {
                'total_grid_strings': total_grid_strings,
                'avg_accuracy': avg_accuracy,
                'max_consecutive_failures': max_consecutive_failures,
                'avg_max_consecutive_failures': avg_max_consecutive_failures,
                'total_steps': total_steps,
                'total_failures': total_failures,
                'total_predictions': total_predictions,
                'total_skipped': total_skipped
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
                'total_skipped': 0
            }
        
        return {
            'results': results,
            'summary': summary,
            'grid_string_ids': grid_string_ids
        }
        
    except Exception as e:
        st.error(f"다중 윈도우 배치 검증 오류 (제외 전략 + 신뢰도 스킵): {str(e)}")
        return None
    finally:
        conn.close()

def batch_validate_multi_window_with_confidence_skip(
    cutoff_grid_string_id,
    window_sizes=[5, 6, 7, 8, 9],
    method="빈도 기반",
    threshold=0,
    confidence_skip_threshold=None
):
    """
    다중 윈도우 크기 + 신뢰도 스킵 전략으로 배치 검증
    
    Args:
        cutoff_grid_string_id: 기준 grid_string ID
        window_sizes: 테스트할 윈도우 크기 리스트
        method: 예측 방법
        threshold: 임계값
        confidence_skip_threshold: 신뢰도 스킵 임계값 (None이면 스킵 없음)
    
    Returns:
        dict: 배치 검증 결과
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # cutoff 이후의 모든 grid_string 로드
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
                    'total_skipped': 0
                }
            }
        
        grid_string_ids = df['id'].tolist()
        results = []
        
        # 각 grid_string에 대해 검증
        for grid_string_id in grid_string_ids:
            result = validate_multi_window_with_confidence_skip(
                grid_string_id,
                cutoff_grid_string_id,
                window_sizes=window_sizes,
                method=method,
                threshold=threshold,
                confidence_skip_threshold=confidence_skip_threshold
            )
            
            if result is not None:
                results.append(result)
        
        # 요약 통계 계산
        if len(results) > 0:
            total_grid_strings = len(results)
            avg_accuracy = sum(r['accuracy'] for r in results) / total_grid_strings
            max_consecutive_failures = max(r['max_consecutive_failures'] for r in results)
            avg_max_consecutive_failures = sum(r['max_consecutive_failures'] for r in results) / total_grid_strings
            total_steps = sum(r['total_steps'] for r in results)
            total_failures = sum(r['total_failures'] for r in results)
            total_predictions = sum(r['total_predictions'] for r in results)
            total_skipped = sum(r['total_skipped'] for r in results)
            
            summary = {
                'total_grid_strings': total_grid_strings,
                'avg_accuracy': avg_accuracy,
                'max_consecutive_failures': max_consecutive_failures,
                'avg_max_consecutive_failures': avg_max_consecutive_failures,
                'total_steps': total_steps,
                'total_failures': total_failures,
                'total_predictions': total_predictions,
                'total_skipped': total_skipped
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
                'total_skipped': 0
            }
        
        return {
            'results': results,
            'summary': summary,
            'grid_string_ids': grid_string_ids
        }
        
    except Exception as e:
        st.error(f"배치 검증 오류 (신뢰도 스킵): {str(e)}")
        return None
    finally:
        conn.close()

def binary_search_optimal_threshold_multi_window(
    cutoff_id,
    window_sizes=[5, 6, 7, 8, 9],
    method="빈도 기반",
    threshold=0,
    min_range=50.5,
    max_range=59.0,
    target_max_failures=5,
    tolerance=0.5,
    max_iterations=10,
    progress_bar=None,
    status_text=None
):
    """
    이진 탐색으로 다중 윈도우 크기 전략의 최적 신뢰도 스킵 임계값 찾기
    
    Args:
        cutoff_id: 기준 grid_string ID
        window_sizes: 테스트할 윈도우 크기 리스트
        method: 예측 방법
        threshold: 임계값
        min_range: 최소 임계값 범위
        max_range: 최대 임계값 범위
        target_max_failures: 목표 최대 연속 불일치
        tolerance: 최종 정밀도
        max_iterations: 최대 반복 횟수
        progress_bar: Streamlit progress bar 객체 (선택적)
        status_text: Streamlit status text 객체 (선택적)
    
    Returns:
        tuple: (최적 임계값, 최적 결과, 탐색 히스토리)
    """
    best_threshold = None
    best_result = None
    left = min_range
    right = max_range
    iteration = 0
    search_history = []
    
    # 1단계: 큰 간격으로 이진 탐색
    while (right - left) > tolerance and iteration < max_iterations:
        iteration += 1
        mid = round((left + right) / 2, 1)
        
        if status_text:
            status_text.text(f"이진 탐색 {iteration}회차: 임계값 {mid}% 테스트 중... (범위: {left:.1f} ~ {right:.1f})")
        
        # 중간값 테스트
        result = batch_validate_multi_window_with_confidence_skip(
            cutoff_id,
            window_sizes=window_sizes,
            method=method,
            threshold=threshold,
            confidence_skip_threshold=mid
        )
        
        if result:
            summary = result.get('summary', {})
            max_failures = summary.get('max_consecutive_failures', 999)
            
            search_history.append({
                'iteration': iteration,
                'threshold': mid,
                'max_consecutive_failures': max_failures,
                'avg_accuracy': summary.get('avg_accuracy', 0.0),
                'total_skipped': summary.get('total_skipped', 0),
                'range': (left, right)
            })
            
            if max_failures <= target_max_failures:
                # 조건 만족 - 더 낮은 임계값 시도 (왼쪽 탐색)
                best_threshold = mid
                best_result = result
                right = mid
            else:
                # 조건 불만족 - 더 높은 임계값 시도 (오른쪽 탐색)
                left = mid
        
        if progress_bar:
            progress_bar.progress(iteration / max_iterations * 0.7)  # 70%까지는 이진 탐색
    
    # 2단계: 찾은 범위에서 세밀하게 그리드 서치
    if best_threshold:
        fine_min = max(min_range, best_threshold - tolerance)
        fine_max = min(max_range, best_threshold + tolerance)
        fine_step = 0.1
        
        if status_text:
            status_text.text(f"세밀 탐색: {fine_min:.1f} ~ {fine_max:.1f} 범위에서 0.1 간격으로 테스트 중...")
        
        fine_thresholds = []
        current = fine_min
        while current <= fine_max + 0.001:
            fine_thresholds.append(round(current, 1))
            current += fine_step
        
        fine_results = []
        for idx, thresh in enumerate(fine_thresholds):
            if status_text:
                status_text.text(f"세밀 탐색: {thresh}% 테스트 중... ({idx+1}/{len(fine_thresholds)})")
            
            result = batch_validate_multi_window_with_confidence_skip(
                cutoff_id,
                window_sizes=window_sizes,
                method=method,
                threshold=threshold,
                confidence_skip_threshold=thresh
            )
            
            if result:
                summary = result.get('summary', {})
                fine_results.append({
                    'threshold': thresh,
                    'result': result,
                    'max_consecutive_failures': summary.get('max_consecutive_failures', 999),
                    'avg_accuracy': summary.get('avg_accuracy', 0.0),
                    'total_skipped': summary.get('total_skipped', 0)
                })
            
            if progress_bar:
                progress = 0.7 + (idx + 1) / len(fine_thresholds) * 0.3
                progress_bar.progress(progress)
        
        # 최적값 선택
        candidates = [
            r for r in fine_results
            if r['max_consecutive_failures'] <= target_max_failures
        ]
        
        if candidates:
            optimal = min(candidates, key=lambda x: (
                x['max_consecutive_failures'],
                x['result'].get('summary', {}).get('total_failures', 999999)
            ))
            return optimal['threshold'], optimal['result'], search_history
    
    return best_threshold, best_result, search_history

def format_datetime_for_dropdown(datetime_str):
    """
    드롭다운용 날짜 포맷: MM-DD HH:MM (연도 제외)
    
    Args:
        datetime_str: 날짜 시간 문자열
    
    Returns:
        str: "MM-DD HH:MM" 형식의 문자열
    """
    if datetime_str is None:
        return ""
    
    try:
        # 다양한 날짜 형식 파싱 시도
        from datetime import datetime
        
        # SQLite datetime 형식: "YYYY-MM-DD HH:MM:SS"
        if isinstance(datetime_str, str):
            # 공백으로 분리하여 날짜와 시간 추출
            parts = datetime_str.split()
            if len(parts) >= 2:
                date_part = parts[0]
                time_part = parts[1]
                
                # 날짜 파싱 (YYYY-MM-DD)
                date_parts = date_part.split('-')
                if len(date_parts) >= 3:
                    month = date_parts[1]
                    day = date_parts[2]
                    
                    # 시간 파싱 (HH:MM:SS 또는 HH:MM)
                    time_parts = time_part.split(':')
                    if len(time_parts) >= 2:
                        hour = time_parts[0]
                        minute = time_parts[1]
                        
                        return f"{month}-{day} {hour}:{minute}"
        
        # 파싱 실패 시 원본 반환
        return str(datetime_str)
    except Exception as e:
        return str(datetime_str)

def analyze_failure_patterns(simulation_results, min_failures=6):
    """
    최대 연속 불일치가 min_failures 이상인 grid_string 분석
    
    Args:
        simulation_results: batch_validate_multi_window_scenario()의 반환값
        min_failures: 최소 연속 불일치 수 (기본값: 6)
    
    Returns:
        dict: {
            'failure_grid_strings': 실패한 grid_string 리스트,
            'common_failure_prefixes': 공통 실패 prefix 패턴,
            'failure_positions': 실패 위치 분석,
            'confidence_analysis': 신뢰도 분석,
            'window_size_analysis': 윈도우 크기별 분석,
            'failure_statistics': 실패 통계
        }
    """
    if not simulation_results or len(simulation_results.get('results', [])) == 0:
        return {
            'failure_grid_strings': [],
            'common_failure_prefixes': {},
            'failure_positions': {},
            'confidence_analysis': {},
            'window_size_analysis': {},
            'failure_statistics': {}
        }
    
    results = simulation_results.get('results', [])
    
    # 최대 연속 불일치가 min_failures 이상인 grid_string 필터링
    failure_grid_strings = [
        r for r in results 
        if r.get('max_consecutive_failures', 0) >= min_failures
    ]
    
    if len(failure_grid_strings) == 0:
        return {
            'failure_grid_strings': [],
            'common_failure_prefixes': {},
            'failure_positions': {},
            'confidence_analysis': {},
            'window_size_analysis': {},
            'failure_statistics': {
                'total_failures': 0,
                'failure_rate': 0.0
            }
        }
    
    # 실패 패턴 분석
    common_failure_prefixes = defaultdict(int)
    failure_positions = defaultdict(int)
    confidence_before_failure = []
    confidence_during_failure = []
    window_size_usage = defaultdict(lambda: {'total': 0, 'failures': 0})
    
    for result in failure_grid_strings:
        history = result.get('history', [])
        consecutive_failures = 0
        max_consecutive = result.get('max_consecutive_failures', 0)
        
        for i, h in enumerate(history):
            is_correct = h.get('is_correct')
            prefix = h.get('prefix')
            position = h.get('position')
            confidence = h.get('confidence', 0.0)
            selected_window_size = h.get('selected_window_size')
            
            if is_correct is False:
                consecutive_failures += 1
                
                # 실패 prefix 기록
                if prefix:
                    common_failure_prefixes[prefix] += 1
                
                # 실패 위치 기록
                if position is not None:
                    failure_positions[position] += 1
                
                # 실패 중 신뢰도 기록
                confidence_during_failure.append(confidence)
                
                # 윈도우 크기별 실패 기록
                if selected_window_size:
                    window_size_usage[selected_window_size]['failures'] += 1
                    window_size_usage[selected_window_size]['total'] += 1  # 실패도 예측이므로 total 증가
                
                # 실패 전 신뢰도 기록 (이전 스텝)
                if i > 0:
                    prev_confidence = history[i-1].get('confidence', 0.0)
                    if history[i-1].get('is_correct') is True:
                        confidence_before_failure.append(prev_confidence)
            elif is_correct is True:
                consecutive_failures = 0
                
                # 윈도우 크기별 사용 기록
                if selected_window_size:
                    window_size_usage[selected_window_size]['total'] += 1
            # is_correct is None일 때는 아무것도 하지 않음 (예측값이 없었던 경우)
    
    # 통계 계산
    total_grid_strings = len(results)
    failure_count = len(failure_grid_strings)
    failure_rate = (failure_count / total_grid_strings * 100) if total_grid_strings > 0 else 0.0
    
    # 신뢰도 분석
    avg_confidence_before = sum(confidence_before_failure) / len(confidence_before_failure) if confidence_before_failure else 0.0
    avg_confidence_during = sum(confidence_during_failure) / len(confidence_during_failure) if confidence_during_failure else 0.0
    
    # 윈도우 크기별 실패율 계산
    window_size_failure_rates = {}
    for window_size, stats in window_size_usage.items():
        if stats['total'] > 0:
            failure_rate_ws = (stats['failures'] / stats['total'] * 100)
            window_size_failure_rates[window_size] = {
                'failure_rate': failure_rate_ws,
                'total_usage': stats['total'],
                'failures': stats['failures']
            }
    
    # 공통 실패 prefix 정렬 (빈도순)
    sorted_failure_prefixes = sorted(
        common_failure_prefixes.items(),
        key=lambda x: x[1],
        reverse=True
    )[:20]  # 상위 20개만
    
    return {
        'failure_grid_strings': failure_grid_strings,
        'common_failure_prefixes': dict(sorted_failure_prefixes),
        'failure_positions': dict(failure_positions),
        'confidence_analysis': {
            'avg_before_failure': avg_confidence_before,
            'avg_during_failure': avg_confidence_during,
            'confidence_drop': avg_confidence_before - avg_confidence_during
        },
        'window_size_analysis': window_size_failure_rates,
        'failure_statistics': {
            'total_failures': failure_count,
            'total_grid_strings': total_grid_strings,
            'failure_rate': failure_rate,
            'avg_max_consecutive_failures': sum(r.get('max_consecutive_failures', 0) for r in failure_grid_strings) / failure_count if failure_count > 0 else 0.0
        }
    }

def display_multi_dimensional_results(simulation_results, optimal_result, cutoff_id, method, use_threshold, main_threshold):
    """다차원 최적화 결과 표시 함수"""
    st.markdown("---")
    st.markdown("### 📊 다차원 최적화 시뮬레이션 결과")
    
    # 최적 조합 추천
    optimal_combination = optimal_result.get('optimal_combination')
    
    if optimal_combination is not None:
        opt_result = optimal_combination.get('result')
        
        st.success(
            f"✅ **추천 최적 조합**: "
            f"윈도우 크기={optimal_combination['window_size']}, "
            f"최대 간격={optimal_combination['max_interval']}, "
            f"임계값={optimal_combination['confidence_skip_threshold']}%"
        )
        
        col_opt1, col_opt2, col_opt3, col_opt4 = st.columns(4)
        with col_opt1:
            st.metric("최대 연속 불일치", f"{opt_result.get('max_consecutive_failures', 0)}회")
        with col_opt2:
            st.metric("5 이하 비율", f"{opt_result.get('below_5_ratio', 0):.2f}%")
        with col_opt3:
            st.metric("총 스킵 횟수", f"{opt_result.get('total_skipped_predictions', 0):,}회")
        with col_opt4:
            st.metric("평균 정확도", f"{opt_result.get('avg_accuracy', 0):.2f}%")
        
        if optimal_result.get('warning'):
            st.warning(optimal_result['warning'])
        
        # 조건 만족하는 후보 개수
        candidate_count = optimal_result.get('candidate_count', 0)
        if candidate_count > 0:
            st.info(f"💡 조건을 만족하는 조합: {candidate_count}개 (최대 연속 불일치 ≤ 5)")
    else:
        st.error("❌ 최적 조합을 찾을 수 없습니다.")
    
    # 상위 조합 비교 테이블 (최대 20개)
    st.markdown("---")
    st.markdown("### 📋 조합 비교 (상위 20개)")
    
    all_results = simulation_results.get('results', [])
    
    # 정렬: 최대 연속 불일치 기준
    sorted_results = sorted(
        all_results,
        key=lambda x: (
            x.get('max_consecutive_failures', 999),
            x.get('total_skipped_predictions', 999999),
            -x.get('avg_accuracy', 0.0)
        )
    )[:20]
    
    comparison_data = []
    for result in sorted_results:
        is_optimal = (
            optimal_combination is not None and
            result.get('window_size') == optimal_combination['window_size'] and
            result.get('max_interval') == optimal_combination['max_interval'] and
            result.get('confidence_skip_threshold') == optimal_combination['confidence_skip_threshold']
        )
        
        comparison_data.append({
            '윈도우 크기': result.get('window_size'),
            '최대 간격': result.get('max_interval'),
            '임계값 (%)': f"{result.get('confidence_skip_threshold', 0):.1f}",
            '최대 연속 불일치': result.get('max_consecutive_failures', 0),
            '평균 최대 연속 불일치': f"{result.get('avg_max_consecutive_failures', 0):.2f}",
            '5 이하 비율 (%)': f"{result.get('below_5_ratio', 0):.2f}",
            '총 스킵 횟수': result.get('total_skipped_predictions', 0),
            '평균 정확도 (%)': f"{result.get('avg_accuracy', 0):.2f}",
            '최적': '✅' if is_optimal else ''
        })
    
    comparison_df = pd.DataFrame(comparison_data)
    st.dataframe(comparison_df, use_container_width=True, hide_index=True)
    
    # 파라미터별 효과 분석
    st.markdown("---")
    st.markdown("### 📈 파라미터별 효과 분석")
    
    # 윈도우 크기별 평균 성능
    window_size_stats = defaultdict(lambda: {'total': 0, 'failures_sum': 0, 'count': 0})
    for result in all_results:
        ws = result.get('window_size')
        failures = result.get('max_consecutive_failures', 0)
        window_size_stats[ws]['total'] += failures
        window_size_stats[ws]['count'] += 1
    
    ws_data = []
    for ws in sorted(window_size_stats.keys()):
        stats = window_size_stats[ws]
        avg_failures = stats['total'] / stats['count'] if stats['count'] > 0 else 0
        ws_data.append({
            '윈도우 크기': ws,
            '테스트 횟수': stats['count'],
            '평균 최대 연속 불일치': f"{avg_failures:.2f}"
        })
    
    if ws_data:
        col_analysis1, col_analysis2 = st.columns(2)
        with col_analysis1:
            st.markdown("#### 윈도우 크기별 성능")
            ws_df = pd.DataFrame(ws_data)
            st.dataframe(ws_df, use_container_width=True, hide_index=True)
    
    # 최대 간격별 평균 성능
    interval_stats = defaultdict(lambda: {'total': 0, 'count': 0})
    for result in all_results:
        mi = result.get('max_interval')
        failures = result.get('max_consecutive_failures', 0)
        interval_stats[mi]['total'] += failures
        interval_stats[mi]['count'] += 1
    
    interval_data = []
    for mi in sorted(interval_stats.keys()):
        stats = interval_stats[mi]
        avg_failures = stats['total'] / stats['count'] if stats['count'] > 0 else 0
        interval_data.append({
            '최대 간격': mi,
            '테스트 횟수': stats['count'],
            '평균 최대 연속 불일치': f"{avg_failures:.2f}"
        })
    
    if interval_data:
        with col_analysis2:
            st.markdown("#### 최대 간격별 성능")
            interval_df = pd.DataFrame(interval_data)
            st.dataframe(interval_df, use_container_width=True, hide_index=True)
    
    # 시뮬레이션 통계
    st.markdown("---")
    st.markdown("### 📊 시뮬레이션 통계")
    col_stat1, col_stat2, col_stat3, col_stat4 = st.columns(4)
    with col_stat1:
        total_tested = simulation_results.get('total_tested', 0)
        total_planned = simulation_results.get('total_planned', total_tested)
        if total_planned > total_tested:
            st.metric("테스트한 조합 수", f"{total_tested}개", f"계획: {total_planned}개 (조기 종료)")
        else:
            st.metric("테스트한 조합 수", f"{total_tested}개")
    with col_stat2:
        st.metric("가능한 조합 수", f"{simulation_results.get('total_combinations_possible', 0):,}개")
    with col_stat3:
        meaningful_count = simulation_results.get('meaningful_count', 0)
        st.metric("유의미한 결과", f"{meaningful_count}개", 
                 help="예측률 ≥10%, 스킵 비율 ≤90%, 최소 10회 예측")
    with col_stat4:
        success_rate = (simulation_results.get('successful', 0) / max(total_tested, 1)) * 100
        st.metric("성공률", f"{success_rate:.1f}%")
    
    # 조기 종료 정보
    if simulation_results.get('early_stopped', False):
        st.info(f"⚡ **조기 종료**: 충분한 유의미한 결과를 찾아 {total_planned - total_tested}개 조합을 건너뛰었습니다. 시간 절약: 약 {int((total_planned - total_tested) * 0.3)}분")
    
    # 결과 저장 버튼 (session_state에서 필요한 파라미터 가져오기)
    st.markdown("---")
    col_save1, col_save2 = st.columns([1, 4])
    with col_save1:
        if st.button("💾 결과 저장", type="primary", use_container_width=True, key="save_multi_dimensional"):
            # session_state에서 다차원 설정 가져오기
            window_size_min = st.session_state.get('multi_window_size_min', 5)
            window_size_max = st.session_state.get('multi_window_size_max', 9)
            max_interval_min = st.session_state.get('multi_max_interval_min', 1)
            max_interval_max = st.session_state.get('multi_max_interval_max', 20)
            min_skip_threshold = st.session_state.get('multi_min_skip_threshold', 51.0)
            max_skip_threshold = st.session_state.get('multi_max_skip_threshold', 53.5)
            threshold_step = st.session_state.get('multi_threshold_step', 0.1)
            num_samples = st.session_state.get('multi_num_samples', 100)
            
            validation_id = save_multi_dimensional_simulation_results(
                cutoff_id,
                (window_size_min, window_size_max),
                (max_interval_min, max_interval_max),
                (min_skip_threshold, max_skip_threshold, threshold_step),
                num_samples,
                method,
                use_threshold,
                main_threshold if use_threshold else 56,
                simulation_results,
                optimal_result
            )
            
            if validation_id:
                st.session_state.multi_dimensional_saved_id = validation_id
                st.success(f"✅ 다차원 시뮬레이션 결과가 저장되었습니다. (ID: {validation_id[:8]}...)")
            else:
                st.warning("⚠️ 결과 저장에 실패했습니다.")
    
    with col_save2:
        if 'multi_dimensional_saved_id' in st.session_state:
            st.info(f"💾 마지막 저장 ID: {st.session_state.multi_dimensional_saved_id[:8]}...")

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
                main_threshold if use_threshold else 56,
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
    **목표**: 지정한 범위에서 설정한 단위로 스킵 임계값을 테스트하여
    최대 연속 불일치 5 이하를 만족하는 최적 임계값을 찾습니다.
    """)
    
    # 테이블 생성 확인
    if 'simulation_tables_created' not in st.session_state:
        if create_simulation_tables():
            st.session_state.simulation_tables_created = True
        else:
            st.error("테이블 생성 실패")
            return
    
    # 최근 결과 표시 (이전에 저장된 결과가 있으면 표시)
    st.markdown("---")
    st.markdown("### 📋 최근 저장된 결과")
    
    latest_result = load_latest_multi_dimensional_result()
    if latest_result and 'optimal_result' in latest_result:
        opt_result = latest_result['optimal_result']
        
        col_recent1, col_recent2, col_recent3 = st.columns(3)
        with col_recent1:
            st.metric("최대 연속 불일치", f"{opt_result.get('max_consecutive_failures', 0)}회")
        with col_recent2:
            st.metric("평균 정확도", f"{opt_result.get('avg_accuracy', 0):.2f}%")
        with col_recent3:
            st.metric("총 테스트 조합", f"{latest_result.get('total_results', 0)}개")
        
        if opt_result.get('window_size') and opt_result.get('max_interval'):
            st.info(
                f"**최근 저장된 최적 조합** (저장 시간: {latest_result.get('created_at', 'N/A')}): "
                f"윈도우 크기={opt_result.get('window_size')}, "
                f"최대 간격={opt_result.get('max_interval')}, "
                f"임계값={opt_result.get('confidence_skip_threshold', 0):.1f}%"
            )
    else:
        st.info("💡 저장된 결과가 없습니다. 시뮬레이션을 실행하고 결과를 저장하면 여기에 표시됩니다.")
    
    # 최적화 모드 선택 (form 외부에 배치하여 즉시 반영되도록 함)
    st.markdown("### ⚙️ 시뮬레이션 설정")
    
    # 데이터 새로고침 버튼
    col_refresh1, col_refresh2 = st.columns([1, 4])
    with col_refresh1:
        refresh_clicked = st.button("🔄 데이터 새로고침", key="simulation_refresh_data", use_container_width=True)
    with col_refresh2:
        auto_refresh = st.checkbox(
            "시뮬레이션 실행 시 자동 새로고침",
            value=st.session_state.get('simulation_auto_refresh', False),
            key="simulation_auto_refresh_checkbox",
            help="시뮬레이션 실행 전에 데이터를 자동으로 새로고침합니다"
        )
    
    # 새로고침 버튼 클릭 시 캐시 제거 및 새로고침
    if refresh_clicked:
        # 캐시된 데이터 제거
        if 'preprocessed_data_cache' in st.session_state:
            del st.session_state.preprocessed_data_cache
        # 저장된 시뮬레이션 세션 관련 캐시 제거
        cache_keys_to_remove = [key for key in st.session_state.keys() if 'simulation' in key.lower() and 'cache' in key.lower()]
        for key in cache_keys_to_remove:
            del st.session_state[key]
        
        st.session_state.simulation_auto_refresh = auto_refresh
        st.success("✅ 데이터가 새로고침되었습니다.")
        st.rerun()
    
    # 자동 새로고침 설정 저장
    if auto_refresh != st.session_state.get('simulation_auto_refresh', False):
        st.session_state.simulation_auto_refresh = auto_refresh
    
    optimization_mode = st.radio(
        "최적화 모드",
        options=["단일 파라미터 최적화", "다차원 최적화"],
        index=0,
        key="simulation_optimization_mode",
        help="단일 파라미터: 기존 방식 (임계값만 최적화) | 다차원: 윈도우 크기, 간격, 임계값 동시 최적화",
        horizontal=True
    )
    
    # 모드 확인
    is_multi_dimensional = optimization_mode == "다차원 최적화"
    
    # 설정 섹션
    with st.form("simulation_settings_form", clear_on_submit=False):
        
        # 다차원 모드일 때는 단일 파라미터 설정 숨기기
        if not is_multi_dimensional:
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
                        format_func=lambda x: "전체 데이터" if x is None else next((f"ID {opt[0]} - {format_datetime_for_dropdown(opt[1])}" for opt in grid_string_options if opt[0] == x), f"ID {x} 이후"),
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
        else:
            # 다차원 모드: 기준 Grid String ID만 선택
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
                    format_func=lambda x: "전체 데이터" if x is None else next((f"ID {opt[0]} - {format_datetime_for_dropdown(opt[1])}" for opt in grid_string_options if opt[0] == x), f"ID {x} 이후"),
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
            
            # 다차원 모드를 위한 기본값 설정
            method = st.session_state.get('simulation_method', '빈도 기반')
            use_threshold = st.session_state.get('simulation_use_threshold', True)
            main_threshold = st.session_state.get('simulation_main_threshold', 56) if use_threshold else None
        
        # 시뮬레이션 범위 설정
        st.markdown("---")
        
        if is_multi_dimensional:
            # 다차원 최적화 모드
            st.markdown("### 📊 다차원 최적화 범위 설정")
            
            # 윈도우 크기 범위
            st.markdown("#### 윈도우 크기 범위")
            col_ws1, col_ws2 = st.columns(2)
            with col_ws1:
                window_size_min = st.number_input(
                    "최소 윈도우 크기",
                    min_value=5,
                    max_value=9,
                    value=5,
                    step=1,
                    key="multi_window_size_min",
                    help="테스트할 최소 윈도우 크기"
                )
            with col_ws2:
                window_size_max = st.number_input(
                    "최대 윈도우 크기",
                    min_value=5,
                    max_value=9,
                    value=9,
                    step=1,
                    key="multi_window_size_max",
                    help="테스트할 최대 윈도우 크기"
                )
            
            # 최대 예측 없음 간격 범위
            st.markdown("#### 최대 예측 없음 간격 범위")
            col_interval1, col_interval2 = st.columns(2)
            with col_interval1:
                max_interval_min = st.number_input(
                    "최소 간격",
                    min_value=1,
                    max_value=20,
                    value=1,
                    step=1,
                    key="multi_max_interval_min",
                    help="테스트할 최소 간격"
                )
            with col_interval2:
                max_interval_max = st.number_input(
                    "최대 간격",
                    min_value=1,
                    max_value=20,
                    value=20,
                    step=1,
                    key="multi_max_interval_max",
                    help="테스트할 최대 간격"
                )
            
            # 신뢰도 스킵 임계값 범위
            st.markdown("#### 신뢰도 스킵 임계값 범위")
            col_thresh1, col_thresh2, col_thresh3 = st.columns(3)
            with col_thresh1:
                min_skip_threshold = st.number_input(
                    "최소 임계값 (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=51.0,
                    step=0.1,
                    key="multi_min_skip_threshold",
                    help="테스트할 최소 스킵 임계값"
                )
            with col_thresh2:
                max_skip_threshold = st.number_input(
                    "최대 임계값 (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=53.5,
                    step=0.1,
                    key="multi_max_skip_threshold",
                    help="테스트할 최대 스킵 임계값"
                )
            with col_thresh3:
                threshold_step = st.number_input(
                    "단위",
                    min_value=0.1,
                    max_value=10.0,
                    value=0.1,
                    step=0.1,
                    key="multi_threshold_step",
                    help="임계값 테스트 간격"
                )
            
            # 랜덤 샘플링 설정
            st.markdown("#### 랜덤 샘플링 설정")
            num_samples = st.number_input(
                "샘플링 개수",
                min_value=10,
                max_value=1000,
                value=100,
                step=10,
                key="multi_num_samples",
                help="테스트할 무작위 조합 개수 (10-1000)"
            )
            
            # 조기 종료 옵션
            enable_early_stop = st.checkbox(
                "조기 종료 활성화",
                value=False,
                key="multi_enable_early_stop",
                help="충분한 유의미한 결과를 찾으면 조기 종료하여 시간 절약 (최대 연속 불일치 ≤ 5인 유의미한 결과 5개 이상 발견 시)"
            )
            
            if enable_early_stop:
                min_meaningful_results = st.number_input(
                    "최소 유의미한 결과 개수",
                    min_value=3,
                    max_value=20,
                    value=5,
                    step=1,
                    key="multi_min_meaningful_results",
                    help="조기 종료를 위한 최소 유의미한 결과 개수 (3-20)"
                )
            else:
                min_meaningful_results = 5  # 기본값
            
            # 병렬 처리 설정
            st.markdown("#### 병렬 처리 설정")
            col_parallel1, col_parallel2 = st.columns(2)
            with col_parallel1:
                use_threading = st.checkbox(
                    "스레드 풀 사용 (I/O 바운드 작업에 유리)",
                    value=False,
                    key="multi_use_threading",
                    help="체크 시 ThreadPoolExecutor 사용 (DB 읽기 많을 때 유리), 체크 해제 시 ProcessPoolExecutor 사용 (CPU 작업 많을 때 유리)"
                )
            
            with col_parallel2:
                max_workers_manual = st.number_input(
                    "작업자 수 (None이면 자동)",
                    min_value=1,
                    max_value=mp.cpu_count(),
                    value=None,
                    step=1,
                    key="multi_max_workers",
                    help=f"병렬 작업자 수 (1-{mp.cpu_count()}개). None이면 자동 계산 (CPU의 50%만 사용하여 다른 작업 여유 확보)"
                )
            
            # CPU 정보 표시
            cpu_count = mp.cpu_count()
            if max_workers_manual is None:
                auto_workers = max(2, min(int(cpu_count * 0.5), 4))
                expected_cpu_usage = (auto_workers / cpu_count) * 100
                st.info(
                    f"💡 **CPU 정보**: {cpu_count}개 스레드 감지 | "
                    f"자동 작업자 수: {auto_workers}개 | "
                    f"예상 CPU 사용률: {expected_cpu_usage:.0f}% | "
                    f"다른 작업을 위한 여유: {100 - expected_cpu_usage:.0f}%"
                )
            else:
                expected_cpu_usage = (max_workers_manual / cpu_count) * 100
                st.info(
                    f"💡 **수동 설정**: {max_workers_manual}개 작업자 사용 | "
                    f"예상 CPU 사용률: {expected_cpu_usage:.0f}% | "
                    f"다른 작업을 위한 여유: {100 - expected_cpu_usage:.0f}%"
                )
            
            # 범위 검증 및 조합 수 계산
            valid_ranges = True
            if window_size_min > window_size_max:
                st.error("⚠️ 최소 윈도우 크기는 최대 윈도우 크기보다 작아야 합니다.")
                valid_ranges = False
            if max_interval_min > max_interval_max:
                st.error("⚠️ 최소 간격은 최대 간격보다 작아야 합니다.")
                valid_ranges = False
            if min_skip_threshold >= max_skip_threshold:
                st.error("⚠️ 최소 임계값은 최대 임계값보다 작아야 합니다.")
                valid_ranges = False
            
            if valid_ranges:
                # 가능한 조합 수 계산
                window_size_count = window_size_max - window_size_min + 1
                max_interval_count = max_interval_max - max_interval_min + 1
                threshold_count = int((max_skip_threshold - min_skip_threshold) / threshold_step) + 1
                total_combinations = window_size_count * max_interval_count * threshold_count
                
                col_info1, col_info2 = st.columns(2)
                with col_info1:
                    st.metric("가능한 조합 수", f"{total_combinations:,}개")
                with col_info2:
                    actual_samples = min(num_samples, total_combinations)
                    st.metric("실제 테스트할 조합", f"{actual_samples}개")
                    
                    if num_samples > total_combinations:
                        st.warning(f"⚠️ 샘플링 개수({num_samples}개)가 가능한 조합 수({total_combinations}개)보다 큽니다. 전체 조합을 테스트합니다.")
        else:
            # 단일 파라미터 최적화 모드 (기존 UI)
            st.markdown("### 📊 시뮬레이션 범위")
            col_range1, col_range2, col_range3 = st.columns(3)
            with col_range1:
                min_skip_threshold = st.number_input(
                    "최소 임계값 (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=51.0,
                    step=0.1,
                    key="simulation_min_skip_threshold",
                    help="테스트할 최소 스킵 임계값"
                )
            with col_range2:
                max_skip_threshold = st.number_input(
                    "최대 임계값 (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=53.5,
                    step=0.1,
                    key="simulation_max_skip_threshold",
                    help="테스트할 최대 스킵 임계값"
                )
            with col_range3:
                threshold_step = st.number_input(
                    "단위",
                    min_value=0.1,
                    max_value=10.0,
                    value=0.1,
                    step=0.1,
                    key="simulation_threshold_step",
                    help="임계값 테스트 간격"
                )
            
            # 범위 검증 및 정보 표시
            if min_skip_threshold >= max_skip_threshold:
                st.error("⚠️ 최소 임계값은 최대 임계값보다 작아야 합니다.")
            else:
                # 테스트 개수 계산
                test_count = int((max_skip_threshold - min_skip_threshold) / threshold_step) + 1
                
                col_info1, col_info2, col_info3 = st.columns(3)
                with col_info1:
                    st.metric("테스트 범위", f"{min_skip_threshold:.1f}% ~ {max_skip_threshold:.1f}%")
                with col_info2:
                    st.metric("테스트 개수", f"{test_count}개")
                with col_info3:
                    st.metric("테스트 단위", f"{threshold_step:.1f}")
        # 예상 시간 계산
        should_calculate_time = selected_cutoff_id is not None
        if is_multi_dimensional:
            should_calculate_time = (should_calculate_time and 
                                   window_size_min <= window_size_max and
                                   max_interval_min <= max_interval_max and
                                   min_skip_threshold < max_skip_threshold)
        else:
            should_calculate_time = should_calculate_time and min_skip_threshold < max_skip_threshold
        
        if should_calculate_time:
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
                    time_per_test = after_count * time_per_grid  # 초
                    
                    if is_multi_dimensional:
                        # 다차원 모드: 샘플링 개수 기준
                        estimated_tests = min(num_samples, total_combinations)
                        estimated_time_seconds = time_per_test * estimated_tests
                    else:
                        # 단일 파라미터 모드
                        test_count_full = int((max_skip_threshold - min_skip_threshold) / threshold_step) + 1
                        use_hybrid_estimate = st.session_state.get('simulation_search_method', '하이브리드 (이진 탐색 + 순차 탐색)')
                        if '하이브리드' in use_hybrid_estimate:
                            # 이진 탐색 10회 + 순차 탐색 (범위의 약 20-30%)
                            estimated_tests = 10 + max(5, int(test_count_full * 0.25))
                        else:
                            estimated_tests = test_count_full
                        estimated_time_seconds = time_per_test * estimated_tests
                    
                    total_minutes = int(estimated_time_seconds // 60)
                    total_seconds = int(estimated_time_seconds % 60)
                    
                    if total_minutes > 0:
                        estimated_time = f"약 {total_minutes}분 {total_seconds}초"
                    else:
                        estimated_time = f"약 {total_seconds}초"
                    
                    if is_multi_dimensional:
                        st.info(f"⏱️ **예상 소요 시간**: {estimated_time} (검증 대상: {after_count}개 grid_string × {estimated_tests}개 조합, 다차원 최적화)")
                    else:
                        test_count_display = f"{estimated_tests}개" if '하이브리드' in use_hybrid_estimate else f"{test_count_full}개"
                        method_note = " (하이브리드)" if '하이브리드' in use_hybrid_estimate else " (순차 전체)"
                        st.info(f"⏱️ **예상 소요 시간**: {estimated_time}{method_note} (검증 대상: {after_count}개 grid_string × {test_count_display} 임계값)")
                except:
                    st.info("⏱️ **예상 소요 시간**: 계산 중...")
                finally:
                    conn.close()
            else:
                st.info("⏱️ **예상 소요 시간**: 계산 불가")
        elif selected_cutoff_id is None:
            st.info("💡 기준 Grid String ID를 선택하면 예상 소요 시간을 계산할 수 있습니다.")
        
        # 탐색 방식 선택 (단일 파라미터 모드에서만 표시)
        if not is_multi_dimensional:
            search_method = st.radio(
                "탐색 방식",
                options=["하이브리드 (이진 탐색 + 순차 탐색)", "순차 그리드 서치 (전체 범위)"],
                index=0,
                key="simulation_search_method",
                help="하이브리드: 빠른 이진 탐색 후 정밀 탐색 | 순차: 모든 임계값 순차 테스트"
            )
        else:
            # 다차원 모드는 항상 랜덤 서치 사용
            search_method = "랜덤 서치 (다차원)"
            st.session_state.simulation_search_method = search_method
        
        # 시뮬레이션 실행 버튼
        if st.form_submit_button("🚀 시뮬레이션 실행", type="primary", use_container_width=True):
            if selected_cutoff_id is None:
                st.warning("⚠️ 기준 Grid String ID를 선택해주세요.")
            elif is_multi_dimensional:
                # 다차원 모드 검증
                if window_size_min > window_size_max:
                    st.warning("⚠️ 최소 윈도우 크기는 최대 윈도우 크기보다 작아야 합니다.")
                elif max_interval_min > max_interval_max:
                    st.warning("⚠️ 최소 간격은 최대 간격보다 작아야 합니다.")
                elif min_skip_threshold >= max_skip_threshold:
                    st.warning("⚠️ 최소 임계값은 최대 임계값보다 작아야 합니다.")
                else:
                    st.session_state.simulation_cutoff_id = selected_cutoff_id
                    st.session_state.simulation_results = None
                    st.session_state.simulation_optimal = None
                    st.session_state.simulation_progress = {}
                    st.rerun()
            else:
                # 단일 파라미터 모드 검증
                if min_skip_threshold >= max_skip_threshold:
                    st.warning("⚠️ 최소 임계값은 최대 임계값보다 작아야 합니다.")
                else:
                    st.session_state.simulation_cutoff_id = selected_cutoff_id
                    st.session_state.simulation_results = None
                    st.session_state.simulation_optimal = None
                    st.session_state.simulation_progress = {}
                    st.rerun()
    
    # 시뮬레이션 실행 및 결과 표시
    if 'simulation_cutoff_id' in st.session_state and st.session_state.simulation_cutoff_id is not None:
        cutoff_id = st.session_state.simulation_cutoff_id
        
        # 현재 설정 가져오기 (form 내부 위젯 값들은 자동으로 session_state에 저장됨)
        optimization_mode = st.session_state.get('simulation_optimization_mode', '단일 파라미터 최적화')
        is_multi_dimensional = optimization_mode == "다차원 최적화"
        
        window_size = st.session_state.get('simulation_window_size', 7)
        method = st.session_state.get('simulation_method', '빈도 기반')
        use_threshold = st.session_state.get('simulation_use_threshold', True)
        main_threshold = st.session_state.get('simulation_main_threshold', 56) if use_threshold else None
        max_interval = st.session_state.get('simulation_max_interval', 5)
        min_skip_threshold = st.session_state.get('simulation_min_skip_threshold', 51.0)
        max_skip_threshold = st.session_state.get('simulation_max_skip_threshold', 53.5)
        threshold_step = st.session_state.get('simulation_threshold_step', 0.1)
        search_method = st.session_state.get('simulation_search_method', '하이브리드 (이진 탐색 + 순차 탐색)')
        
        # 다차원 모드 파라미터 가져오기
        if is_multi_dimensional:
            window_size_min = st.session_state.get('multi_window_size_min', 5)
            window_size_max = st.session_state.get('multi_window_size_max', 9)
            max_interval_min = st.session_state.get('multi_max_interval_min', 1)
            max_interval_max = st.session_state.get('multi_max_interval_max', 20)
            min_skip_threshold = st.session_state.get('multi_min_skip_threshold', 51.0)
            max_skip_threshold = st.session_state.get('multi_max_skip_threshold', 53.5)
            threshold_step = st.session_state.get('multi_threshold_step', 0.1)
            num_samples = st.session_state.get('multi_num_samples', 100)
        
        # 테스트 개수 계산
        if is_multi_dimensional:
            window_size_count = window_size_max - window_size_min + 1
            max_interval_count = max_interval_max - max_interval_min + 1
            threshold_count = int((max_skip_threshold - min_skip_threshold) / threshold_step) + 1
            total_combinations = window_size_count * max_interval_count * threshold_count
            test_count = min(num_samples, total_combinations)
        else:
            test_count_full = int((max_skip_threshold - min_skip_threshold) / threshold_step) + 1
            test_count = test_count_full if '순차' in search_method else f"~{max(10, int(test_count_full * 0.3))}개 (이진 탐색 + 순차 탐색)"
        
        # 결과가 캐시되어 있으면 사용, 없으면 실행
        if 'simulation_results' in st.session_state and st.session_state.simulation_results is not None:
            simulation_results = st.session_state.simulation_results
            optimal_result = st.session_state.get('simulation_optimal')
        else:
            with st.spinner(f"시뮬레이션 실행 중... ({test_count}개 임계값 테스트)"):
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                try:
                    # 배치 시뮬레이션 실행
                    status_text.text("시뮬레이션 시작... (예상 시간: 계산 중)")
                    progress_bar.progress(0.0)
                    
                    # 모드에 따라 다른 함수 호출
                    if is_multi_dimensional:
                        # 다차원 최적화 모드
                        enable_early_stop = st.session_state.get('multi_enable_early_stop', False)
                        min_meaningful_results = st.session_state.get('multi_min_meaningful_results', 5)
                        use_threading = st.session_state.get('multi_use_threading', False)
                        max_workers = st.session_state.get('multi_max_workers', None)
                        # None이거나 0이면 자동 계산
                        if max_workers is not None and max_workers <= 0:
                            max_workers = None
                        
                        simulation_results = random_search_multi_dimensional(
                            cutoff_id,
                            window_size_range=(window_size_min, window_size_max),
                            max_interval_range=(max_interval_min, max_interval_max),
                            confidence_skip_range=(min_skip_threshold, max_skip_threshold, threshold_step),
                            num_samples=num_samples,
                            method=method,
                            use_threshold=use_threshold,
                            main_threshold=main_threshold if use_threshold else 56,
                            progress_bar=progress_bar,
                            status_text=status_text,
                            enable_early_stop=enable_early_stop,
                            min_meaningful_results=min_meaningful_results,
                            max_workers=max_workers,
                            use_threading=use_threading
                        )
                    else:
                        # 단일 파라미터 최적화 모드
                        use_hybrid = '하이브리드' in search_method
                        
                        if use_hybrid:
                            simulation_results = hybrid_search_optimal_threshold(
                                cutoff_id,
                                window_size=window_size,
                                method=method,
                                use_threshold=use_threshold,
                                main_threshold=main_threshold if use_threshold else 56,
                                max_interval=max_interval,
                                min_skip_threshold=min_skip_threshold,
                                max_skip_threshold=max_skip_threshold,
                                step=threshold_step,
                                target_max_failures=5,
                                tolerance=1.0,
                                max_binary_iterations=10,
                                progress_bar=progress_bar,
                                status_text=status_text
                            )
                        else:
                            simulation_results = batch_simulate_threshold_range(
                                cutoff_id,
                                window_size=window_size,
                                method=method,
                                use_threshold=use_threshold,
                                main_threshold=main_threshold if use_threshold else 56,
                                max_interval=max_interval,
                                min_skip_threshold=min_skip_threshold,
                                max_skip_threshold=max_skip_threshold,
                                step=threshold_step,
                                progress_bar=progress_bar,
                                status_text=status_text
                            )
                    
                    if simulation_results and len(simulation_results.get('results', [])) > 0:
                        # 최적값 찾기 (모드에 따라 다른 함수 사용)
                        status_text.text("최적 조합 분석 중...")
                        progress_bar.progress(0.95)
                        
                        if is_multi_dimensional:
                            optimal_result = find_optimal_multi_dimensional(simulation_results)
                        else:
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
            if is_multi_dimensional:
                display_multi_dimensional_results(simulation_results, optimal_result, cutoff_id, method, use_threshold, main_threshold)
            else:
                display_results(simulation_results, optimal_result, cutoff_id, window_size, method, use_threshold, main_threshold, max_interval)
        elif simulation_results:
            st.warning("⚠️ 시뮬레이션 결과는 있지만 최적값 분석에 실패했습니다.")
        else:
            st.info("💡 시뮬레이션을 실행하면 결과가 표시됩니다.")
    else:
        st.info("💡 기준 Grid String ID를 선택하고 시뮬레이션을 실행하세요.")
    
    # 구분선
    st.markdown("---")
    
    # 예측값 테이블 관리 섹션
    st.markdown("## 📊 예측값 테이블 관리")
    
    col_table1, col_table2 = st.columns(2)
    
    with col_table1:
        if st.button("🔧 예측값 테이블 생성", use_container_width=True):
            with st.spinner("테이블 생성 중..."):
                if create_stored_predictions_table():
                    st.success("✅ 예측값 테이블이 생성되었습니다.")
                else:
                    st.error("❌ 테이블 생성에 실패했습니다.")
    
    with col_table2:
        # 테이블 상태 확인
        conn = get_db_connection()
        if conn is not None:
            try:
                count_query = "SELECT COUNT(*) as count FROM stored_predictions"
                count_df = pd.read_sql_query(count_query, conn)
                stored_count = count_df.iloc[0]['count'] if len(count_df) > 0 else 0
                st.metric("저장된 예측값 개수", f"{stored_count:,}개")
            except:
                st.metric("저장된 예측값 개수", "0개")
            finally:
                conn.close()
        else:
            st.metric("저장된 예측값 개수", "0개")
    
    # 예측값 생성 섹션
    with st.form("prediction_generation_form", clear_on_submit=False):
        st.markdown("### 예측값 생성")
        
        col_pred1, col_pred2, col_pred3 = st.columns(3)
        
        with col_pred1:
            # 기준 Grid String ID 선택
            df_all_strings = load_preprocessed_data()
            if len(df_all_strings) > 0:
                grid_string_options = []
                for _, row in df_all_strings.iterrows():
                    grid_string_options.append((row['id'], row['created_at']))
                
                grid_string_options.sort(key=lambda x: x[0], reverse=True)
                
                selected_cutoff_id_pred = st.selectbox(
                    "기준 Grid String ID (이 ID 이하를 학습 데이터로 사용)",
                    options=[None] + [opt[0] for opt in grid_string_options],
                    format_func=lambda x: "전체 데이터" if x is None else next((f"ID {opt[0]} - {format_datetime_for_dropdown(opt[1])}" for opt in grid_string_options if opt[0] == x), f"ID {x}"),
                    key="prediction_cutoff_id"
                )
            else:
                selected_cutoff_id_pred = None
                st.warning("⚠️ 저장된 grid_string이 없습니다.")
        
        with col_pred2:
            prediction_methods = st.multiselect(
                "예측 방법 (복수 선택 가능)",
                options=["빈도 기반", "가중치 기반", "안전 우선"],
                default=["빈도 기반", "가중치 기반"],
                key="prediction_methods",
                help="선택한 모든 방법에 대해 예측값이 별도로 저장됩니다"
            )
        
        with col_pred3:
            prediction_threshold = st.number_input(
                "임계값",
                min_value=0,
                max_value=100,
                value=0,
                step=1,
                key="prediction_threshold",
                help="0은 임계값 없이 모든 예측 포함"
            )
        
        if st.form_submit_button("🚀 예측값 생성 시작", type="primary", use_container_width=True):
            if selected_cutoff_id_pred is None:
                st.warning("⚠️ 기준 Grid String ID를 선택해주세요.")
            elif len(prediction_methods) == 0:
                st.warning("⚠️ 최소 하나의 예측 방법을 선택해주세요.")
            else:
                with st.spinner("예측값 생성 중... (시간이 오래 걸릴 수 있습니다)"):
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    status_text.text("예측값 생성 시작...")
                    progress_bar.progress(0.1)
                    
                    result = save_or_update_predictions_for_historical_data(
                        cutoff_grid_string_id=selected_cutoff_id_pred,
                        window_sizes=[5, 6, 7, 8, 9],
                        methods=prediction_methods,
                        thresholds=[prediction_threshold],
                        batch_size=1000
                    )
                    
                    if result:
                        progress_bar.progress(1.0)
                        status_text.text("완료!")
                        st.success(f"✅ 예측값 생성 완료!")
                        methods_str = ", ".join(prediction_methods)
                        st.info(f"""
                        - 생성된 예측 방법: {methods_str}
                        - 총 저장/업데이트: {result.get('total_saved', 0):,}개
                        - 새 레코드: {result.get('new_records', 0):,}개
                        - 업데이트: {result.get('updated_records', 0):,}개
                        - 고유 prefix: {result.get('unique_prefixes', 0):,}개
                        """)
                    else:
                        st.error("❌ 예측값 생성에 실패했습니다.")
                    
                    progress_bar.empty()
                    status_text.empty()
    
    # 다중 윈도우 크기 시뮬레이션 섹션
    st.markdown("---")
    st.markdown("## 🎯 다중 윈도우 크기 시뮬레이션")
    st.markdown("""
    **전략**: 각 위치에서 여러 윈도우 크기(5, 6, 7, 8, 9) 중 가장 높은 신뢰도를 가진 예측값을 선택합니다.
    """)
    
    # stored_predictions 테이블 확인
    conn = get_db_connection()
    if conn is not None:
        try:
            count_query = "SELECT COUNT(*) as count FROM stored_predictions"
            count_df = pd.read_sql_query(count_query, conn)
            stored_count = count_df.iloc[0]['count'] if len(count_df) > 0 else 0
            
            if stored_count == 0:
                st.warning("⚠️ stored_predictions 테이블이 비어있습니다. 위의 '예측값 생성' 섹션에서 예측값을 먼저 생성해주세요.")
        except:
            stored_count = 0
            st.warning("⚠️ stored_predictions 테이블이 없습니다. 위의 '예측값 테이블 생성' 버튼을 먼저 클릭해주세요.")
        finally:
            conn.close()
    else:
        stored_count = 0
    
    # 데이터 새로고침 설정 초기화
    if 'multi_window_auto_refresh' not in st.session_state:
        st.session_state.multi_window_auto_refresh = False
    
    # 데이터 새로고침 버튼 (form 밖에 위치)
    st.markdown("### ⚙️ 시뮬레이션 설정")
    col_refresh_mw1, col_refresh_mw2 = st.columns([1, 4])
    with col_refresh_mw1:
        refresh_clicked_mw = st.button("🔄 데이터 새로고침", key="multi_window_refresh_data", use_container_width=True)
    with col_refresh_mw2:
        auto_refresh_mw = st.checkbox(
            "시뮬레이션 실행 시 자동 새로고침",
            value=st.session_state.multi_window_auto_refresh,
            key="multi_window_auto_refresh_checkbox",
            help="시뮬레이션 실행 전에 데이터를 자동으로 새로고침합니다"
        )
    
    # 새로고침 버튼 클릭 시 캐시 제거
    if refresh_clicked_mw:
        if 'preprocessed_data_cache' in st.session_state:
            del st.session_state.preprocessed_data_cache
        st.session_state.multi_window_auto_refresh = auto_refresh_mw
        st.rerun()
    
    # 자동 새로고침 설정 저장
    if auto_refresh_mw != st.session_state.multi_window_auto_refresh:
        st.session_state.multi_window_auto_refresh = auto_refresh_mw
    
    # 시뮬레이션 설정 폼
    with st.form("multi_window_simulation_form", clear_on_submit=False):
        col_mw1, col_mw2, col_mw3 = st.columns(3)
        
        with col_mw1:
            # 기준 Grid String ID 선택
            df_all_strings_mw = load_preprocessed_data()
            if len(df_all_strings_mw) > 0:
                grid_string_options_mw = []
                for _, row in df_all_strings_mw.iterrows():
                    grid_string_options_mw.append((row['id'], row['created_at']))
                
                grid_string_options_mw.sort(key=lambda x: x[0], reverse=True)
                
                current_selected_mw = st.session_state.get('multi_window_cutoff_id', None)
                default_index_mw = 0
                if current_selected_mw is not None:
                    option_ids_mw = [None] + [opt[0] for opt in grid_string_options_mw]
                    if current_selected_mw in option_ids_mw:
                        default_index_mw = option_ids_mw.index(current_selected_mw)
                
                selected_cutoff_id_mw = st.selectbox(
                    "기준 Grid String ID (이 ID 이후의 데이터 검증)",
                    options=[None] + [opt[0] for opt in grid_string_options_mw],
                    format_func=lambda x: "전체 데이터" if x is None else next((f"ID {opt[0]} - {format_datetime_for_dropdown(opt[1])}" for opt in grid_string_options_mw if opt[0] == x), f"ID {x} 이후"),
                    index=default_index_mw,
                    key="multi_window_cutoff_id_select"
                )
            else:
                selected_cutoff_id_mw = None
                st.warning("⚠️ 저장된 grid_string이 없습니다.")
        
        with col_mw2:
            multi_window_method = st.selectbox(
                "예측 방법",
                options=["빈도 기반", "가중치 기반", "안전 우선"],
                index=0,
                key="multi_window_method"
            )
        
        with col_mw3:
            multi_window_threshold = st.number_input(
                "임계값",
                min_value=0,
                max_value=100,
                value=0,
                step=1,
                key="multi_window_threshold",
                help="0은 임계값 없이 모든 예측 포함"
            )
        
        # 전략 선택
        st.markdown("#### 전략 선택")
        use_exclusion_strategy = st.checkbox(
            "제외 전략 사용",
            value=False,
            key="use_exclusion_strategy",
            help="실패한 윈도우 크기를 제외하는 전략 사용 (복구 메커니즘 포함)"
        )
        
        # 신뢰도 스킵 임계값 설정
        st.markdown("#### 신뢰도 스킵 임계값 설정")
        skip_mode = st.radio(
            "임계값 설정 방식",
            ["수동 설정", "자동 최적화 (이진 탐색)"],
            key="multi_window_skip_mode",
            help="수동 설정: 직접 임계값 입력, 자동 최적화: 이진 탐색으로 최적값 자동 찾기"
        )
        
        confidence_skip_threshold = None
        use_confidence_skip = False
        min_skip = None
        max_skip = None
        tolerance_skip = None
        
        if skip_mode == "수동 설정":
            confidence_skip_threshold = st.number_input(
                "신뢰도 스킵 임계값 (%)",
                min_value=0.0,
                max_value=100.0,
                value=None,
                step=0.1,
                key="multi_window_confidence_skip_manual",
                help="이 신뢰도 미만인 예측은 제외됩니다. None이면 스킵 없음"
            )
            use_confidence_skip = confidence_skip_threshold is not None
        else:
            # 자동 최적화 모드
            col_opt1, col_opt2, col_opt3 = st.columns(3)
            with col_opt1:
                min_skip = st.number_input(
                    "최소 임계값",
                    0.0, 100.0, 50.5, 0.5,
                    key="multi_window_min_skip"
                )
            with col_opt2:
                max_skip = st.number_input(
                    "최대 임계값",
                    0.0, 100.0, 59.0, 0.5,
                    key="multi_window_max_skip"
                )
            with col_opt3:
                tolerance_skip = st.number_input(
                    "최종 정밀도",
                    0.1, 2.0, 0.5, 0.1,
                    key="multi_window_tolerance_skip",
                    help="이진 탐색 후 세밀 탐색할 범위"
                )
            
            use_confidence_skip = True
            # 자동 최적화 시에는 임계값을 None으로 설정 (나중에 찾을 것)
            confidence_skip_threshold = None
        
        # 윈도우 크기 선택
        st.markdown("#### 윈도우 크기 선택")
        col_ws1, col_ws2, col_ws3, col_ws4, col_ws5 = st.columns(5)
        
        with col_ws1:
            use_window_5 = st.checkbox("5", value=True, key="use_window_5")
        with col_ws2:
            use_window_6 = st.checkbox("6", value=True, key="use_window_6")
        with col_ws3:
            use_window_7 = st.checkbox("7", value=True, key="use_window_7")
        with col_ws4:
            use_window_8 = st.checkbox("8", value=True, key="use_window_8")
        with col_ws5:
            use_window_9 = st.checkbox("9", value=True, key="use_window_9")
        
        selected_window_sizes = []
        if use_window_5:
            selected_window_sizes.append(5)
        if use_window_6:
            selected_window_sizes.append(6)
        if use_window_7:
            selected_window_sizes.append(7)
        if use_window_8:
            selected_window_sizes.append(8)
        if use_window_9:
            selected_window_sizes.append(9)
        
        if len(selected_window_sizes) == 0:
            st.warning("⚠️ 최소 하나의 윈도우 크기를 선택해주세요.")
        
        # 시뮬레이션 실행 버튼
        if st.form_submit_button("🚀 다중 윈도우 시뮬레이션 실행", type="primary", use_container_width=True):
            if selected_cutoff_id_mw is None:
                st.warning("⚠️ 기준 Grid String ID를 선택해주세요.")
            elif len(selected_window_sizes) == 0:
                st.warning("⚠️ 최소 하나의 윈도우 크기를 선택해주세요.")
            elif stored_count == 0:
                st.warning("⚠️ stored_predictions 테이블이 비어있습니다. 위의 '예측값 생성' 섹션에서 예측값을 먼저 생성해주세요.")
            else:
                # 자동 새로고침이 활성화되어 있으면 캐시 제거
                if st.session_state.multi_window_auto_refresh:
                    if 'preprocessed_data_cache' in st.session_state:
                        del st.session_state.preprocessed_data_cache
                
                st.session_state.multi_window_cutoff_id = selected_cutoff_id_mw
                st.session_state.multi_window_use_exclusion = use_exclusion_strategy
                # skip_mode는 위젯이 이미 session_state를 관리하므로 저장하지 않음
                # st.session_state.multi_window_skip_mode는 위젯이 자동으로 관리
                st.session_state.multi_window_confidence_skip = confidence_skip_threshold
                st.session_state.multi_window_use_confidence_skip = use_confidence_skip
                if skip_mode == "자동 최적화 (이진 탐색)":
                    # min_skip, max_skip, tolerance_skip도 위젯이 이미 session_state를 관리
                    # st.session_state.multi_window_min_skip 등은 위젯이 자동으로 관리
                    pass
                st.session_state.multi_window_results = None
                st.session_state.multi_window_optimal_result = None
                st.rerun()
    
    # 시뮬레이션 결과 표시
    if 'multi_window_cutoff_id' in st.session_state and st.session_state.multi_window_cutoff_id is not None:
        cutoff_id_mw = st.session_state.multi_window_cutoff_id
        
        # 현재 설정 가져오기
        selected_window_sizes_state = []
        if st.session_state.get('use_window_5', True):
            selected_window_sizes_state.append(5)
        if st.session_state.get('use_window_6', True):
            selected_window_sizes_state.append(6)
        if st.session_state.get('use_window_7', True):
            selected_window_sizes_state.append(7)
        if st.session_state.get('use_window_8', True):
            selected_window_sizes_state.append(8)
        if st.session_state.get('use_window_9', True):
            selected_window_sizes_state.append(9)
        
        multi_window_method_state = st.session_state.get('multi_window_method', '빈도 기반')
        multi_window_threshold_state = st.session_state.get('multi_window_threshold', 0)
        
        # 전략 가져오기
        use_exclusion_strategy_state = st.session_state.get('multi_window_use_exclusion', False)
        skip_mode_state = st.session_state.get('multi_window_skip_mode', '수동 설정')
        confidence_skip_threshold_state = st.session_state.get('multi_window_confidence_skip', None)
        use_confidence_skip_state = st.session_state.get('multi_window_use_confidence_skip', False)
        
        # 결과가 캐시되어 있으면 사용, 없으면 실행
        if 'multi_window_results' in st.session_state and st.session_state.multi_window_results is not None:
            multi_window_results = st.session_state.multi_window_results
            multi_window_optimal_result = st.session_state.get('multi_window_optimal_result')
        else:
            with st.spinner("다중 윈도우 시뮬레이션 실행 중..."):
                progress_bar_mw = st.progress(0)
                status_text_mw = st.empty()
                
                strategy_name = "제외 전략" if use_exclusion_strategy_state else "기본 전략"
                if use_confidence_skip_state:
                    if skip_mode_state == "자동 최적화 (이진 탐색)":
                        strategy_name += " + 신뢰도 스킵 (자동 최적화)"
                    else:
                        strategy_name += f" + 신뢰도 스킵 ({confidence_skip_threshold_state}%)"
                
                status_text_mw.text(f"시뮬레이션 시작... ({strategy_name})")
                progress_bar_mw.progress(0.1)
                
                multi_window_optimal_result = None
                
                # 디버깅: 현재 상태 확인
                # st.info(f"🔍 디버깅 정보:\n- skip_mode_state: {skip_mode_state}\n- use_confidence_skip_state: {use_confidence_skip_state}\n- skip_mode 타입: {type(skip_mode_state)}")
                
                # 자동 최적화 모드인 경우
                if skip_mode_state == "자동 최적화 (이진 탐색)" and use_confidence_skip_state:
                    min_skip_state = st.session_state.get('multi_window_min_skip', 50.5)
                    max_skip_state = st.session_state.get('multi_window_max_skip', 59.0)
                    tolerance_skip_state = st.session_state.get('multi_window_tolerance_skip', 0.5)
                    
                    optimal_threshold, optimal_result, search_history = binary_search_optimal_threshold_multi_window(
                        cutoff_id_mw,
                        window_sizes=selected_window_sizes_state,
                        method=multi_window_method_state,
                        threshold=multi_window_threshold_state,
                        min_range=min_skip_state,
                        max_range=max_skip_state,
                        target_max_failures=5,
                        tolerance=tolerance_skip_state,
                        progress_bar=progress_bar_mw,
                        status_text=status_text_mw
                    )
                    
                    if optimal_result:
                        multi_window_results = optimal_result
                        multi_window_optimal_result = {
                            'optimal_threshold': optimal_threshold,
                            'search_history': search_history
                        }
                        st.session_state.multi_window_optimal_result = multi_window_optimal_result
                    else:
                        st.warning("⚠️ 최적 임계값을 찾을 수 없습니다.")
                        multi_window_results = None
                else:
                    # 수동 설정 모드 또는 신뢰도 스킵 미사용
                    # 디버깅: 왜 자동 최적화가 실행되지 않았는지 확인
                    if skip_mode_state != "자동 최적화 (이진 탐색)":
                        # st.warning(f"⚠️ 자동 최적화 모드가 아닙니다. 현재 모드: {skip_mode_state}")
                        pass
                    if not use_confidence_skip_state:
                        # st.warning(f"⚠️ 신뢰도 스킵이 비활성화되어 있습니다.")
                        pass
                    
                    if use_exclusion_strategy_state:
                        if use_confidence_skip_state:
                            # 제외 전략 + 신뢰도 스킵 (수동)
                            multi_window_results = batch_validate_multi_window_scenario_with_exclusion_and_confidence_skip(
                                cutoff_id_mw,
                                window_sizes=selected_window_sizes_state,
                                method=multi_window_method_state,
                                threshold=multi_window_threshold_state,
                                confidence_skip_threshold=confidence_skip_threshold_state
                            )
                        else:
                            multi_window_results = batch_validate_multi_window_scenario_with_exclusion(
                                cutoff_id_mw,
                                window_sizes=selected_window_sizes_state,
                                method=multi_window_method_state,
                                threshold=multi_window_threshold_state
                            )
                    else:
                        if use_confidence_skip_state:
                            # 기본 전략 + 신뢰도 스킵 (수동)
                            multi_window_results = batch_validate_multi_window_with_confidence_skip(
                                cutoff_id_mw,
                                window_sizes=selected_window_sizes_state,
                                method=multi_window_method_state,
                                threshold=multi_window_threshold_state,
                                confidence_skip_threshold=confidence_skip_threshold_state
                            )
                        else:
                            # 기본 전략만
                            multi_window_results = batch_validate_multi_window_scenario(
                                cutoff_id_mw,
                                window_sizes=selected_window_sizes_state,
                                method=multi_window_method_state,
                                threshold=multi_window_threshold_state
                            )
                
                if multi_window_results:
                    st.session_state.multi_window_results = multi_window_results
                    progress_bar_mw.progress(1.0)
                    status_text_mw.text("완료!")
                else:
                    st.error("시뮬레이션 실행 실패")
                
                progress_bar_mw.empty()
                status_text_mw.empty()
        
        # 결과 표시
        if multi_window_results and len(multi_window_results.get('results', [])) > 0:
            st.markdown("---")
            strategy_label = "제외 전략" if use_exclusion_strategy_state else "기본 전략"
            if use_confidence_skip_state:
                if skip_mode_state == "자동 최적화 (이진 탐색)":
                    optimal_thresh = multi_window_optimal_result.get('optimal_threshold') if multi_window_optimal_result else None
                    if optimal_thresh:
                        strategy_label += f" + 신뢰도 스킵 (최적: {optimal_thresh}%)"
                    else:
                        strategy_label += " + 신뢰도 스킵 (최적화 실패)"
                else:
                    strategy_label += f" + 신뢰도 스킵 ({confidence_skip_threshold_state}%)"
            st.markdown(f"### 📊 다중 윈도우 시뮬레이션 결과 ({strategy_label})")
            
            # 자동 최적화 결과 표시
            if skip_mode_state == "자동 최적화 (이진 탐색)" and multi_window_optimal_result:
                optimal_thresh = multi_window_optimal_result.get('optimal_threshold')
                search_history = multi_window_optimal_result.get('search_history', [])
                
                if optimal_thresh:
                    st.success(f"✅ **최적 신뢰도 스킵 임계값: {optimal_thresh}%**")
                    
                    if search_history:
                        st.markdown("#### 🔍 이진 탐색 히스토리")
                        history_data = []
                        for h in search_history:
                            history_data.append({
                                '회차': h['iteration'],
                                '임계값 (%)': f"{h['threshold']:.1f}",
                                '최대 연속 불일치': h['max_consecutive_failures'],
                                '평균 정확도 (%)': f"{h['avg_accuracy']:.2f}",
                                '스킵 횟수': h['total_skipped'],
                                '범위': f"{h['range'][0]:.1f} ~ {h['range'][1]:.1f}"
                            })
                        history_df = pd.DataFrame(history_data)
                        st.dataframe(history_df, use_container_width=True, hide_index=True)
                else:
                    st.warning("⚠️ 최적 임계값을 찾을 수 없습니다.")
            
            summary = multi_window_results.get('summary', {})
            
            col_result1, col_result2, col_result3, col_result4 = st.columns(4)
            with col_result1:
                st.metric("최대 연속 불일치", f"{summary.get('max_consecutive_failures', 0)}회")
            with col_result2:
                st.metric("평균 정확도", f"{summary.get('avg_accuracy', 0):.2f}%")
            with col_result3:
                st.metric("총 예측 횟수", f"{summary.get('total_predictions', 0):,}회")
            with col_result4:
                st.metric("검증 대상", f"{summary.get('total_grid_strings', 0):,}개")
            
            # 상세 결과 테이블
            st.markdown("#### 상세 결과")
            results_data = []
            for result in multi_window_results.get('results', []):
                results_data.append({
                    'Grid String ID': result.get('grid_string_id'),
                    '최대 연속 불일치': result.get('max_consecutive_failures', 0),
                    '정확도 (%)': f"{result.get('accuracy', 0):.2f}",
                    '총 스텝': result.get('total_steps', 0),
                    '총 예측': result.get('total_predictions', 0),
                    '총 실패': result.get('total_failures', 0)
                })
            
            if results_data:
                results_df = pd.DataFrame(results_data)
                st.dataframe(results_df, use_container_width=True, hide_index=True)
                
                # Grid String 선택 드롭다운
                st.markdown("#### 상세 히스토리 조회")
                grid_string_ids_list = [r.get('grid_string_id') for r in multi_window_results.get('results', [])]
                
                if len(grid_string_ids_list) > 0:
                    selected_grid_id = st.selectbox(
                        "Grid String ID 선택",
                        options=grid_string_ids_list,
                        key="selected_grid_string_id_mw",
                        format_func=lambda x: f"ID {x}"
                    )
                    
                    # 선택된 grid_string의 히스토리 표시
                    selected_result = next(
                        (r for r in multi_window_results.get('results', []) if r.get('grid_string_id') == selected_grid_id),
                        None
                    )
                    
                    if selected_result and len(selected_result.get('history', [])) > 0:
                        history = selected_result.get('history', [])
                        
                        # 실패 구간 강조를 위한 정보
                        consecutive_failures = 0
                        max_consecutive = 0
                        failure_ranges = []
                        current_range_start = None
                        
                        for i, h in enumerate(history):
                            is_correct = h.get('is_correct')
                            if is_correct is False:
                                consecutive_failures += 1
                                if current_range_start is None:
                                    current_range_start = i
                                if consecutive_failures > max_consecutive:
                                    max_consecutive = consecutive_failures
                            else:
                                if current_range_start is not None:
                                    failure_ranges.append((current_range_start, i - 1))
                                    current_range_start = None
                                consecutive_failures = 0
                        
                        if current_range_start is not None:
                            failure_ranges.append((current_range_start, len(history) - 1))
                        
                        col_hist1, col_hist2 = st.columns(2)
                        with col_hist1:
                            st.metric("최대 연속 불일치", f"{selected_result.get('max_consecutive_failures', 0)}회")
                        with col_hist2:
                            st.metric("정확도", f"{selected_result.get('accuracy', 0):.2f}%")
                        
                        if failure_ranges:
                            st.info(f"⚠️ 실패 구간: {len(failure_ranges)}개 (스텝 {', '.join([f'{r[0]+1}-{r[1]+1}' for r in failure_ranges[:5]])}{'...' if len(failure_ranges) > 5 else ''})")
                        
                        # 히스토리 테이블
                        history_data = []
                        for i, h in enumerate(history):
                            is_failure_range = any(start <= i <= end for start, end in failure_ranges)
                            
                            # 제외 전략 사용 시 제외된 윈도우 크기 정보 추가
                            excluded_info = ""
                            if use_exclusion_strategy_state and h.get('excluded_window_sizes'):
                                excluded_info = f"제외: {', '.join(map(str, h.get('excluded_window_sizes', [])))}"
                            
                            history_data.append({
                                '스텝': h.get('step'),
                                '위치': h.get('position'),
                                'Prefix': h.get('prefix', ''),
                                '예측값': h.get('predicted', ''),
                                '실제값': h.get('actual', ''),
                                '정확': '✅' if h.get('is_correct') else '❌' if h.get('is_correct') is False else '-',
                                '신뢰도': f"{h.get('confidence', 0):.2f}%",
                                '선택된 윈도우': h.get('selected_window_size', ''),
                                '제외된 윈도우': excluded_info if use_exclusion_strategy_state else '',
                                '실패 구간': '🔴' if is_failure_range else ''
                            })
                        
                        if history_data:
                            history_df = pd.DataFrame(history_data)
                            st.dataframe(history_df, use_container_width=True, hide_index=True)
            
            # 실패 패턴 분석 섹션
            st.markdown("---")
            st.markdown("### 🔍 실패 패턴 분석 (최대 연속 불일치 6 이상)")
            
            failure_analysis = analyze_failure_patterns(multi_window_results, min_failures=6)
            
            if len(failure_analysis.get('failure_grid_strings', [])) > 0:
                failure_stats = failure_analysis.get('failure_statistics', {})
                
                col_fail1, col_fail2, col_fail3 = st.columns(3)
                with col_fail1:
                    st.metric("실패 Grid String 수", f"{failure_stats.get('total_failures', 0)}개")
                with col_fail2:
                    st.metric("실패율", f"{failure_stats.get('failure_rate', 0):.2f}%")
                with col_fail3:
                    st.metric("평균 최대 연속 불일치", f"{failure_stats.get('avg_max_consecutive_failures', 0):.2f}회")
                
                # 신뢰도 분석
                confidence_analysis = failure_analysis.get('confidence_analysis', {})
                if confidence_analysis:
                    st.markdown("#### 신뢰도 분석")
                    col_conf1, col_conf2, col_conf3 = st.columns(3)
                    with col_conf1:
                        st.metric("실패 전 평균 신뢰도", f"{confidence_analysis.get('avg_before_failure', 0):.2f}%")
                    with col_conf2:
                        st.metric("실패 중 평균 신뢰도", f"{confidence_analysis.get('avg_during_failure', 0):.2f}%")
                    with col_conf3:
                        confidence_drop = confidence_analysis.get('confidence_drop', 0)
                        st.metric("신뢰도 하락", f"{confidence_drop:.2f}%", delta=f"{confidence_drop:.2f}%")
                
                # 공통 실패 prefix 패턴
                common_prefixes = failure_analysis.get('common_failure_prefixes', {})
                if common_prefixes:
                    st.markdown("#### 공통 실패 Prefix 패턴 (상위 10개)")
                    prefix_data = []
                    for prefix, count in list(common_prefixes.items())[:10]:
                        prefix_data.append({
                            'Prefix': prefix,
                            '실패 횟수': count
                        })
                    
                    if prefix_data:
                        prefix_df = pd.DataFrame(prefix_data)
                        st.dataframe(prefix_df, use_container_width=True, hide_index=True)
                
                # 윈도우 크기별 분석
                window_analysis = failure_analysis.get('window_size_analysis', {})
                if window_analysis:
                    st.markdown("#### 윈도우 크기별 실패율")
                    window_data = []
                    for window_size, stats in sorted(window_analysis.items()):
                        window_data.append({
                            '윈도우 크기': window_size,
                            '실패율 (%)': f"{stats.get('failure_rate', 0):.2f}",
                            '총 사용': stats.get('total_usage', 0),
                            '실패 횟수': stats.get('failures', 0)
                        })
                    
                    if window_data:
                        window_df = pd.DataFrame(window_data)
                        st.dataframe(window_df, use_container_width=True, hide_index=True)
                
                # 회피 전략 제안
                st.markdown("#### 💡 회피 전략 제안")
                suggestions = []
                
                if confidence_analysis.get('confidence_drop', 0) > 10:
                    suggestions.append("실패 전 신뢰도가 크게 하락하므로, 신뢰도가 급격히 떨어질 때 예측을 스킵하는 전략을 고려하세요.")
                
                if window_analysis:
                    worst_window = min(window_analysis.items(), key=lambda x: x[1].get('failure_rate', 0))
                    if worst_window[1].get('failure_rate', 0) > 50:
                        suggestions.append(f"윈도우 크기 {worst_window[0]}의 실패율이 높으므로, 해당 윈도우 크기를 제외하거나 가중치를 낮추는 것을 고려하세요.")
                
                if common_prefixes:
                    top_failure_prefix = list(common_prefixes.items())[0][0]
                    suggestions.append(f"Prefix '{top_failure_prefix}'에서 자주 실패하므로, 해당 패턴이 나타날 때 특별한 처리를 고려하세요.")
                
                if suggestions:
                    for i, suggestion in enumerate(suggestions, 1):
                        st.info(f"{i}. {suggestion}")
                else:
                    st.info("추가적인 회피 전략을 분석 중입니다.")
            else:
                st.success("✅ 최대 연속 불일치 6 이상인 grid_string이 없습니다!")
        elif multi_window_results:
            st.warning("⚠️ 시뮬레이션 결과가 없습니다.")
        else:
            st.info("💡 시뮬레이션을 실행하면 결과가 표시됩니다.")
    else:
        st.info("💡 기준 Grid String ID를 선택하고 시뮬레이션을 실행하세요.")
    
    # 윈도우 크기별 신뢰도 목록 섹션
    st.markdown("---")
    st.markdown("## 📊 윈도우 크기별 Prefix 신뢰도 목록")
    st.markdown("**stored_predictions 테이블의 각 윈도우 크기별 모든 prefix와 신뢰도 (높은 순서)**")
    
    conn = get_db_connection()
    if conn is not None:
        try:
            # 윈도우 크기별로 탭 생성
            tab5, tab6, tab7, tab8 = st.tabs(["윈도우 크기 5", "윈도우 크기 6", "윈도우 크기 7", "윈도우 크기 8"])
            
            for tab, window_size in [(tab5, 5), (tab6, 6), (tab7, 7), (tab8, 8)]:
                with tab:
                    # 해당 윈도우 크기의 모든 prefix와 신뢰도를 신뢰도 높은 순으로 조회
                    # ngram_chunks 테이블과 JOIN하여 빈도수도 함께 가져오기
                    query = """
                        SELECT 
                            sp.prefix,
                            sp.confidence,
                            sp.predicted_value,
                            sp.b_ratio,
                            sp.p_ratio,
                            COUNT(nc.id) as frequency
                        FROM stored_predictions sp
                        LEFT JOIN ngram_chunks nc 
                            ON sp.window_size = nc.window_size 
                            AND sp.prefix = nc.prefix
                        WHERE sp.window_size = ?
                        GROUP BY sp.prefix, sp.confidence, sp.predicted_value, sp.b_ratio, sp.p_ratio
                        ORDER BY sp.confidence DESC
                    """
                    
                    df = pd.read_sql_query(query, conn, params=[window_size])
                    
                    if len(df) > 0:
                        # 테이블 데이터 준비
                        result_data = []
                        for idx, row in df.iterrows():
                            result_data.append({
                                '순위': idx + 1,
                                'Prefix': row['prefix'],
                                '신뢰도 (%)': f"{row['confidence']:.2f}",
                                '빈도수': int(row['frequency']) if row['frequency'] is not None else 0,
                                '예측값': row['predicted_value'],
                                'B 비율': f"{row['b_ratio']:.2f}" if row['b_ratio'] is not None else "N/A",
                                'P 비율': f"{row['p_ratio']:.2f}" if row['p_ratio'] is not None else "N/A"
                            })
                        
                        result_df = pd.DataFrame(result_data)
                        
                        # 총 개수 표시
                        st.markdown(f"**총 {len(df)}개 prefix**")
                        
                        # 테이블 표시
                        st.dataframe(result_df, use_container_width=True, hide_index=True)
                    else:
                        st.warning(f"⚠️ 윈도우 크기 {window_size}에 대한 데이터가 없습니다.")
                        
        except Exception as e:
            st.error(f"신뢰도 목록 조회 중 오류 발생: {str(e)}")
        finally:
            conn.close()
    else:
        st.error("⚠️ 데이터베이스 연결에 실패했습니다.")
    
    # 예측 방법별 예측값 분포 비교 분석 섹션
    st.markdown("---")
    st.markdown("## 📈 예측 방법별 예측값 분포 비교")
    st.markdown("**빈도 기반 vs 가중치 기반 예측값 분포 비교 (윈도우 크기 5, 6, 7, 8)**")
    
    conn = get_db_connection()
    if conn is not None:
        try:
            # 윈도우 크기별, 방법별 예측값 분포 집계
            comparison_data = []
            
            for window_size in [5, 6, 7, 8]:
                for method in ["빈도 기반", "가중치 기반"]:
                    # 해당 윈도우 크기와 방법의 전체 예측 개수 및 B/P 분포
                    query = """
                        SELECT 
                            predicted_value,
                            COUNT(*) as count,
                            AVG(confidence) as avg_confidence,
                            AVG(b_ratio) as avg_b_ratio,
                            AVG(p_ratio) as avg_p_ratio
                        FROM stored_predictions
                        WHERE window_size = ? 
                          AND method = ?
                          AND threshold = 0
                        GROUP BY predicted_value
                    """
                    
                    df = pd.read_sql_query(query, conn, params=[window_size, method])
                    
                    total_count = 0
                    b_count = 0
                    p_count = 0
                    b_avg_confidence = 0.0
                    p_avg_confidence = 0.0
                    b_avg_b_ratio = 0.0
                    b_avg_p_ratio = 0.0
                    p_avg_b_ratio = 0.0
                    p_avg_p_ratio = 0.0
                    
                    for _, row in df.iterrows():
                        count = int(row['count'])
                        total_count += count
                        
                        if row['predicted_value'] == 'b':
                            b_count = count
                            b_avg_confidence = row['avg_confidence'] if row['avg_confidence'] is not None else 0.0
                            b_avg_b_ratio = row['avg_b_ratio'] if row['avg_b_ratio'] is not None else 0.0
                            b_avg_p_ratio = row['avg_p_ratio'] if row['avg_p_ratio'] is not None else 0.0
                        elif row['predicted_value'] == 'p':
                            p_count = count
                            p_avg_confidence = row['avg_confidence'] if row['avg_confidence'] is not None else 0.0
                            p_avg_b_ratio = row['avg_b_ratio'] if row['avg_b_ratio'] is not None else 0.0
                            p_avg_p_ratio = row['avg_p_ratio'] if row['avg_p_ratio'] is not None else 0.0
                    
                    if total_count > 0:
                        b_ratio = (b_count / total_count) * 100
                        p_ratio = (p_count / total_count) * 100
                        
                        comparison_data.append({
                            '윈도우 크기': window_size,
                            '예측 방법': method,
                            '총 예측 수': total_count,
                            'B 예측 수': b_count,
                            'P 예측 수': p_count,
                            'B 비율 (%)': f"{b_ratio:.2f}",
                            'P 비율 (%)': f"{p_ratio:.2f}",
                            'B 평균 신뢰도 (%)': f"{b_avg_confidence:.2f}" if b_count > 0 else "N/A",
                            'P 평균 신뢰도 (%)': f"{p_avg_confidence:.2f}" if p_count > 0 else "N/A",
                            'B 예측 평균 B 비율 (%)': f"{b_avg_b_ratio:.2f}" if b_count > 0 else "N/A",
                            'B 예측 평균 P 비율 (%)': f"{b_avg_p_ratio:.2f}" if b_count > 0 else "N/A",
                            'P 예측 평균 B 비율 (%)': f"{p_avg_b_ratio:.2f}" if p_count > 0 else "N/A",
                            'P 예측 평균 P 비율 (%)': f"{p_avg_p_ratio:.2f}" if p_count > 0 else "N/A"
                        })
            
            if comparison_data:
                comparison_df = pd.DataFrame(comparison_data)
                
                # 요약 통계 표시
                st.markdown("### 전체 요약")
                summary_cols = st.columns(2)
                
                with summary_cols[0]:
                    st.markdown("#### 빈도 기반")
                    freq_df = comparison_df[comparison_df['예측 방법'] == '빈도 기반']
                    if len(freq_df) > 0:
                        total_freq = freq_df['총 예측 수'].sum()
                        b_total_freq = freq_df['B 예측 수'].sum()
                        p_total_freq = freq_df['P 예측 수'].sum()
                        b_ratio_freq = (b_total_freq / total_freq * 100) if total_freq > 0 else 0
                        p_ratio_freq = (p_total_freq / total_freq * 100) if total_freq > 0 else 0
                        
                        st.metric("총 예측 수", f"{total_freq:,}")
                        st.metric("B 예측 비율", f"{b_ratio_freq:.2f}%")
                        st.metric("P 예측 비율", f"{p_ratio_freq:.2f}%")
                
                with summary_cols[1]:
                    st.markdown("#### 가중치 기반")
                    weighted_df = comparison_df[comparison_df['예측 방법'] == '가중치 기반']
                    if len(weighted_df) > 0:
                        total_weighted = weighted_df['총 예측 수'].sum()
                        b_total_weighted = weighted_df['B 예측 수'].sum()
                        p_total_weighted = weighted_df['P 예측 수'].sum()
                        b_ratio_weighted = (b_total_weighted / total_weighted * 100) if total_weighted > 0 else 0
                        p_ratio_weighted = (p_total_weighted / total_weighted * 100) if total_weighted > 0 else 0
                        
                        st.metric("총 예측 수", f"{total_weighted:,}")
                        st.metric("B 예측 비율", f"{b_ratio_weighted:.2f}%")
                        st.metric("P 예측 비율", f"{p_ratio_weighted:.2f}%")
                
                # 상세 비교 테이블
                st.markdown("### 윈도우 크기별 상세 비교")
                st.dataframe(comparison_df, use_container_width=True, hide_index=True)
                
                # 차이 분석
                st.markdown("### 차이 분석")
                if len(freq_df) > 0 and len(weighted_df) > 0:
                    # 윈도우 크기별로 비교
                    diff_data = []
                    for window_size in [5, 6, 7, 8]:
                        freq_row = freq_df[freq_df['윈도우 크기'] == window_size]
                        weighted_row = weighted_df[weighted_df['윈도우 크기'] == window_size]
                        
                        if len(freq_row) > 0 and len(weighted_row) > 0:
                            freq_b_ratio = float(freq_row.iloc[0]['B 비율 (%)'].replace('%', ''))
                            weighted_b_ratio = float(weighted_row.iloc[0]['B 비율 (%)'].replace('%', ''))
                            diff = weighted_b_ratio - freq_b_ratio
                            
                            diff_data.append({
                                '윈도우 크기': window_size,
                                '빈도 기반 B 비율 (%)': f"{freq_b_ratio:.2f}",
                                '가중치 기반 B 비율 (%)': f"{weighted_b_ratio:.2f}",
                                '차이 (가중치 - 빈도) (%)': f"{diff:+.2f}",
                                '변화': "증가" if diff > 0 else "감소" if diff < 0 else "동일"
                            })
                    
                    if diff_data:
                        diff_df = pd.DataFrame(diff_data)
                        st.dataframe(diff_df, use_container_width=True, hide_index=True)
            else:
                st.warning("⚠️ 비교할 데이터가 없습니다. stored_predictions 테이블에 빈도 기반과 가중치 기반 데이터가 모두 있어야 합니다.")
                
        except Exception as e:
            st.error(f"예측값 분포 비교 중 오류 발생: {str(e)}")
        finally:
            conn.close()
    else:
        st.error("⚠️ 데이터베이스 연결에 실패했습니다.")
    
    # 실시간 모델 vs stored_predictions 비교 섹션
    st.markdown("---")
    st.markdown("## 🔍 실시간 모델 vs stored_predictions 테이블 비교")
    st.markdown("**실시간 모델로 생성한 예측값과 stored_predictions 테이블의 예측값을 비교합니다.**")
    
    def generate_realtime_predictions_table(cutoff_grid_string_id, window_sizes=[5, 6, 7, 8, 9], methods=["빈도 기반", "가중치 기반"], threshold=0):
        """
        실시간 모델로 예측 테이블 생성 (stored_predictions와 동일한 형식)
        
        Args:
            cutoff_grid_string_id: 기준 grid_string_id (이 ID 이하를 학습 데이터로 사용)
            window_sizes: 윈도우 크기 리스트
            methods: 예측 방법 리스트
            threshold: 임계값 (0은 임계값 없음)
        
        Returns:
            list: [{window_size, prefix, predicted_value, confidence, b_ratio, p_ratio, method, threshold}, ...]
        """
        conn = get_db_connection()
        if conn is None:
            return []
        
        try:
            # 학습 데이터 선택
            if cutoff_grid_string_id is None:
                query = "SELECT id FROM preprocessed_grid_strings ORDER BY id"
                params = []
            else:
                query = "SELECT id FROM preprocessed_grid_strings WHERE id <= ? ORDER BY id"
                params = [cutoff_grid_string_id]
            
            df_historical = pd.read_sql_query(query, conn, params=params)
            
            if len(df_historical) == 0:
                return []
            
            historical_ids = df_historical['id'].tolist()
            predictions = []
            
            for window_size in window_sizes:
                # 해당 윈도우 크기의 ngram_chunks 로드
                train_ngrams = load_ngram_chunks(window_size=window_size, grid_string_ids=historical_ids)
                
                if len(train_ngrams) == 0:
                    continue
                
                # 모든 가능한 prefix 추출
                all_prefixes = set()
                for _, row in train_ngrams.iterrows():
                    all_prefixes.add(row['prefix'])
                
                # 각 방법으로 모델 구축 및 예측
                for method in methods:
                    # 모델 구축
                    if method == "빈도 기반":
                        model = build_frequency_model(train_ngrams)
                    elif method == "가중치 기반":
                        model = build_weighted_model(train_ngrams)
                    else:
                        model = build_frequency_model(train_ngrams)
                    
                    # 각 prefix에 대해 예측값 계산
                    for prefix in all_prefixes:
                        prediction_result = predict_for_prefix(model, prefix, method)
                        
                        predicted = prediction_result.get('predicted')
                        ratios = prediction_result.get('ratios', {})
                        confidence = prediction_result.get('confidence', 0.0)
                        
                        b_ratio = ratios.get('b', 0.0)
                        p_ratio = ratios.get('p', 0.0)
                        
                        predictions.append({
                            'window_size': window_size,
                            'prefix': prefix,
                            'predicted_value': predicted,
                            'confidence': confidence,
                            'b_ratio': b_ratio,
                            'p_ratio': p_ratio,
                            'method': method,
                            'threshold': threshold
                        })
            
            return predictions
            
        except Exception as e:
            st.error(f"실시간 예측 테이블 생성 중 오류: {str(e)}")
            return []
        finally:
            conn.close()
    
    # 비교 섹션 UI
    with st.form("realtime_vs_stored_comparison_form", clear_on_submit=False):
        st.markdown("### 비교 설정")
        
        col_comp1, col_comp2, col_comp3 = st.columns(3)
        
        with col_comp1:
            # 기준 Grid String ID 선택
            df_all_strings_comp = load_preprocessed_data()
            if len(df_all_strings_comp) > 0:
                grid_string_options_comp = []
                for _, row in df_all_strings_comp.iterrows():
                    grid_string_options_comp.append((row['id'], row['created_at']))
                
                grid_string_options_comp.sort(key=lambda x: x[0], reverse=True)
                
                selected_cutoff_id_comp = st.selectbox(
                    "기준 Grid String ID (이 ID 이하를 학습 데이터로 사용)",
                    options=[None] + [opt[0] for opt in grid_string_options_comp],
                    format_func=lambda x: "전체 데이터" if x is None else next((f"ID {opt[0]} - {format_datetime_for_dropdown(opt[1])}" for opt in grid_string_options_comp if opt[0] == x), f"ID {x}"),
                    key="comparison_cutoff_id"
                )
            else:
                selected_cutoff_id_comp = None
                st.warning("⚠️ 저장된 grid_string이 없습니다.")
        
        with col_comp2:
            comparison_methods = st.multiselect(
                "비교할 예측 방법",
                options=["빈도 기반", "가중치 기반"],
                default=["빈도 기반", "가중치 기반"],
                key="comparison_methods"
            )
        
        with col_comp3:
            comparison_threshold = st.number_input(
                "임계값",
                min_value=0,
                max_value=100,
                value=0,
                step=1,
                key="comparison_threshold",
                help="stored_predictions에서 조회할 임계값"
            )
        
        if st.form_submit_button("🔍 비교 실행", type="primary", use_container_width=True):
            if selected_cutoff_id_comp is None:
                st.warning("⚠️ 기준 Grid String ID를 선택해주세요.")
            elif len(comparison_methods) == 0:
                st.warning("⚠️ 최소 하나의 예측 방법을 선택해주세요.")
            else:
                with st.spinner("실시간 모델로 예측 테이블 생성 중..."):
                    # 실시간 모델로 예측 테이블 생성
                    realtime_predictions = generate_realtime_predictions_table(
                        cutoff_grid_string_id=selected_cutoff_id_comp,
                        window_sizes=[5, 6, 7, 8, 9],
                        methods=comparison_methods,
                        threshold=0
                    )
                    
                    st.session_state.realtime_predictions = realtime_predictions
                    st.session_state.comparison_cutoff_id_saved = selected_cutoff_id_comp
                    st.session_state.comparison_methods_saved = comparison_methods
                    st.session_state.comparison_threshold_saved = comparison_threshold
                    st.rerun()
    
    # 비교 결과 표시
    if 'realtime_predictions' in st.session_state and st.session_state.realtime_predictions:
        realtime_predictions = st.session_state.realtime_predictions
        comparison_cutoff_id = st.session_state.get('comparison_cutoff_id_saved')
        comparison_methods = st.session_state.get('comparison_methods_saved', [])
        comparison_threshold = st.session_state.get('comparison_threshold_saved', 0)
        
        # stored_predictions 테이블에서 데이터 로드
        conn = get_db_connection()
        if conn is not None:
            try:
                # 윈도우 크기별, 방법별로 비교
                for window_size in [5, 6, 7, 8, 9]:
                    for method in comparison_methods:
                        st.markdown(f"### 윈도우 크기 {window_size} - {method}")
                        
                        # 실시간 모델 데이터 필터링
                        realtime_data = [
                            p for p in realtime_predictions 
                            if p['window_size'] == window_size and p['method'] == method
                        ]
                        
                        if len(realtime_data) == 0:
                            st.info(f"실시간 모델 데이터가 없습니다.")
                            continue
                        
                        # stored_predictions 데이터 로드
                        stored_query = """
                            SELECT 
                                prefix,
                                predicted_value,
                                confidence,
                                b_ratio,
                                p_ratio
                            FROM stored_predictions
                            WHERE window_size = ?
                              AND method = ?
                              AND threshold = ?
                        """
                        stored_df = pd.read_sql_query(stored_query, conn, params=[window_size, method, comparison_threshold])
                        
                        if len(stored_df) == 0:
                            st.warning(f"stored_predictions에 데이터가 없습니다. (임계값: {comparison_threshold})")
                            continue
                        
                        # 실시간 데이터를 DataFrame으로 변환
                        realtime_df = pd.DataFrame(realtime_data)
                        
                        # 비교 데이터 생성
                        comparison_data = []
                        
                        # 실시간 모델의 모든 prefix에 대해 비교
                        for _, realtime_row in realtime_df.iterrows():
                            prefix = realtime_row['prefix']
                            stored_row = stored_df[stored_df['prefix'] == prefix]
                            
                            if len(stored_row) > 0:
                                stored_row = stored_row.iloc[0]
                                
                                # 신뢰도 차이 계산
                                confidence_diff = realtime_row['confidence'] - stored_row['confidence']
                                
                                # 예측값 일치 여부
                                predicted_match = (realtime_row['predicted_value'] == stored_row['predicted_value'])
                                
                                comparison_data.append({
                                    'Prefix': prefix,
                                    '실시간 예측값': realtime_row['predicted_value'],
                                    '저장된 예측값': stored_row['predicted_value'],
                                    '예측값 일치': '✅' if predicted_match else '❌',
                                    '실시간 신뢰도 (%)': f"{realtime_row['confidence']:.2f}",
                                    '저장된 신뢰도 (%)': f"{stored_row['confidence']:.2f}",
                                    '신뢰도 차이': f"{confidence_diff:+.2f}",
                                    '실시간 B 비율 (%)': f"{realtime_row['b_ratio']:.2f}",
                                    '저장된 B 비율 (%)': f"{stored_row['b_ratio']:.2f}" if pd.notna(stored_row['b_ratio']) else "N/A",
                                    '실시간 P 비율 (%)': f"{realtime_row['p_ratio']:.2f}",
                                    '저장된 P 비율 (%)': f"{stored_row['p_ratio']:.2f}" if pd.notna(stored_row['p_ratio']) else "N/A"
                                })
                        
                        # 저장된 테이블에는 있지만 실시간 모델에는 없는 prefix
                        realtime_prefixes = set(realtime_df['prefix'].tolist())
                        stored_prefixes = set(stored_df['prefix'].tolist())
                        only_in_stored = stored_prefixes - realtime_prefixes
                        
                        for prefix in only_in_stored:
                            stored_row = stored_df[stored_df['prefix'] == prefix].iloc[0]
                            comparison_data.append({
                                'Prefix': prefix,
                                '실시간 예측값': 'N/A',
                                '저장된 예측값': stored_row['predicted_value'],
                                '예측값 일치': '-',
                                '실시간 신뢰도 (%)': 'N/A',
                                '저장된 신뢰도 (%)': f"{stored_row['confidence']:.2f}",
                                '신뢰도 차이': 'N/A',
                                '실시간 B 비율 (%)': 'N/A',
                                '저장된 B 비율 (%)': f"{stored_row['b_ratio']:.2f}" if pd.notna(stored_row['b_ratio']) else "N/A",
                                '실시간 P 비율 (%)': 'N/A',
                                '저장된 P 비율 (%)': f"{stored_row['p_ratio']:.2f}" if pd.notna(stored_row['p_ratio']) else "N/A"
                            })
                        
                        if comparison_data:
                            comparison_df = pd.DataFrame(comparison_data)
                            
                            # 통계 요약
                            matched_count = sum(1 for d in comparison_data if d['예측값 일치'] == '✅')
                            total_count = len([d for d in comparison_data if d['신뢰도 차이'] != 'N/A'])
                            
                            col_stat1, col_stat2, col_stat3 = st.columns(3)
                            with col_stat1:
                                st.metric("총 Prefix 수", len(comparison_data))
                            with col_stat2:
                                st.metric("예측값 일치", f"{matched_count}/{total_count}", f"{matched_count/total_count*100:.1f}%" if total_count > 0 else "0%")
                            with col_stat3:
                                if total_count > 0:
                                    confidence_diffs = [float(d['신뢰도 차이'].replace('+', '')) for d in comparison_data if d['신뢰도 차이'] != 'N/A']
                                    avg_diff = sum(confidence_diffs) / len(confidence_diffs)
                                    st.metric("평균 신뢰도 차이", f"{avg_diff:+.2f}%")
                            
                            # 신뢰도 차이 순으로 정렬 (큰 차이부터)
                            comparison_df_sorted = comparison_df.copy()
                            comparison_df_sorted['신뢰도_차이_숫자'] = comparison_df_sorted['신뢰도 차이'].apply(
                                lambda x: float(x.replace('+', '')) if x != 'N/A' else 0
                            )
                            comparison_df_sorted = comparison_df_sorted.sort_values('신뢰도_차이_숫자', key=abs, ascending=False)
                            comparison_df_sorted = comparison_df_sorted.drop('신뢰도_차이_숫자', axis=1)
                            
                            st.dataframe(comparison_df_sorted, use_container_width=True, hide_index=True)
                        else:
                            st.info("비교할 데이터가 없습니다.")
                        
                        st.markdown("---")
                
            except Exception as e:
                st.error(f"비교 중 오류 발생: {str(e)}")
                import traceback
                st.error(f"상세 오류: {traceback.format_exc()}")
            finally:
                conn.close()

if __name__ == "__main__":
    main()
